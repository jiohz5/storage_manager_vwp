"""수집 -> 저장 -> outbox 알림 -> 보존기간 정리, 한 사이클 전체.

이 모듈은 PyQt5에 의존하지 않는다 - GUI 내부 타이머(`smvwp.scheduler`)와
cron 경로(`smvwp_cli.py collect`)가 이 함수 하나를 공유해서, 두
실행 경로("cron 또는 백그라운드 타이머", DESIGN.md 2부 7절 3번)의
동작이 어긋나지 않게 한다.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import atomic, collector, config as config_module, forecast_notify, notifications, reports
from . import popup_queue, scan_store, servermon, sqlite_db, store, usage_log

logger = logging.getLogger(__name__)

# 한 바퀴가 어디에 시간을 썼나 - `smvwp_cli.py status` 가 읽는다.
#
# 2026-11 에 "수집 한 번이 DB 를 여러 번 연다" 를 줄이려다 먼저 세어 보았다: 평소 한 바퀴에
# 7~8번이고(보고서를 만드는 날만 더), 여는 것 자체는 NFS 에서도 왕복 몇 번이다. 연결을
# 네 모듈에 걸쳐 넘기도록 고칠 값어치가 있는지는 **실기에서 어느 단계가 느린지** 를 봐야
# 안다 - 그래서 고치기 전에 재는 것부터 둔다.
STAGES = ("df", "save", "server", "notify", "forecast", "reports", "home", "scan_view")


def timing_path(data_dir: Path) -> Path:
    return data_dir / "view" / "cycle.json"


def read_timing(data_dir: Path) -> Optional[dict]:
    """마지막 한 바퀴의 `{finished_at, host, total, seconds:{단계: 초}}`. 없거나 깨졌으면 None."""

    try:
        payload = json.loads(timing_path(data_dir).read_text(encoding="utf-8"))
        seconds = {str(k): float(v) for k, v in payload["seconds"].items()}
        return {"finished_at": str(payload["finished_at"]), "host": str(payload.get("host", "")),
                "total": float(payload["total"]), "seconds": seconds,
                "server_sample": str(payload.get("server_sample", ""))}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


class _Stopwatch:
    def __init__(self) -> None:
        self.seconds: Dict[str, float] = {}
        self._started = time.monotonic()
        # 이번 바퀴의 서버 부하 표본이 어떻게 됐나 - 실기에서 표본이 한 번도 안 쌓였는데
        # 말없이 버려지는 길(잴 수 없음, 빈 표본)이 있어 까닭을 알 수 없었다.
        self.server_sample = ""

    @contextmanager
    def stage(self, name: str):
        started = time.monotonic()
        try:
            yield
        finally:
            self.seconds[name] = self.seconds.get(name, 0.0) + time.monotonic() - started

    def save(self, data_dir: Path) -> None:
        payload = {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "host": socket.gethostname(),
            "total": round(time.monotonic() - self._started, 3),
            "seconds": {name: round(value, 3) for name, value in self.seconds.items()},
            "server_sample": self.server_sample,
        }
        try:
            atomic.write_text(timing_path(data_dir), json.dumps(payload))
        except Exception:  # pragma: no cover - 재는 일이 수집을 막으면 안 된다
            logger.exception("수집 시간 기록을 쓰지 못했습니다")


def forecast_and_home(data_dir: Path, config: config_module.AppConfig,
                      watch: Optional[_Stopwatch] = None, write: bool = True):
    """홈 한 장을 **한 번** 계산해 FULL 예측 알림과 홈 결과 파일이 나눠 쓴다.

    예전에는 예측 알림이 계정마다 30일 표본을 읽어 예측을 내고, 바로 뒤에 홈 결과
    파일을 만들면서 같은 표본을 또 읽어 같은 예측을 다시 냈다. cron 수집은 프로세스가
    매번 새로 떠서 기억해 둔 것도 없다 - 15분마다 가장 무거운 읽기를 두 번 했다
    (계정 100개·90일 실측: 79MB -> 42MB, `tests/read_cost.py`).

    계산한 홈(`DashboardData`)을 돌려준다 (못 했으면 None). `write=False` 면 파일은
    쓰지 않는다 - 부르는 쪽이 나중에 `_write_home` 으로 쓴다. 어느 단계가 실패해도
    이미 저장된 df 표본은 남아야 하므로 예외를 삼킨다.
    """

    from . import dashboard

    watch = watch or _Stopwatch()
    home = None
    try:
        with watch.stage("home"):
            home = dashboard.read_dashboard(data_dir, config)
    except Exception:  # pragma: no cover - 방어적 처리
        logger.exception("홈을 계산하지 못했습니다 (창은 DB 에서 읽습니다)")

    try:
        with watch.stage("forecast"):
            if home is not None and not home.forecast_failed:
                forecasts = [
                    home.forecasts[account.account_id]
                    for account in config_module.enabled_accounts(config)
                    if account.account_id in home.forecasts
                ]
            else:
                # 홈 계산이 안 됐어도 알림은 따로 해 본다.
                forecasts = forecast_notify.build_forecasts(data_dir, config)
            forecast_notify.notify_forecasts(data_dir, config, forecasts)
    except Exception:  # pragma: no cover - 방어적 처리
        logger.exception("FULL 예측 알림 실패 (수집 결과는 이미 저장됨)")

    if write:
        _write_home(data_dir, config, home, watch)
    return home


def _write_home(data_dir: Path, config, home, watch: _Stopwatch) -> None:
    try:
        from . import home_view

        with watch.stage("home"):
            # `home` 이 없으면 (계산 실패) `write` 가 한 번 더 읽어 본다.
            home_view.write(data_dir, config, home)
    except Exception:  # pragma: no cover - 방어적 처리
        logger.exception("홈 결과 파일을 쓰지 못했습니다 (창은 DB 에서 읽습니다)")


def _write_scan_view(data_dir: Path, config, watch: _Stopwatch) -> None:
    from . import scan_lock, scan_view

    try:
        if scan_lock.is_locked(data_dir):
            return
        with watch.stage("scan_view"):
            scan_view.write(data_dir, config)
    except Exception:  # pragma: no cover - 방어적 처리
        logger.exception("상세 스캔 결과 파일을 쓰지 못했습니다 (창은 DB 에서 읽습니다)")


def run_collection_cycle(data_dir: Path, config: config_module.AppConfig) -> List[store.SampleRecord]:
    settings = config.settings
    accounts = config_module.enabled_accounts(config)

    # df 를 읽는 동안이 그대로 서버 부하 측정 구간이 된다.
    #
    # CPU 사용률 같은 값은 두 시점의 차이로만 구할 수 있어서 간격이 필요하다.
    # 수집기는 어차피 df 를 부르느라 몇 초를 쓰므로, 그 시간을 쓰면 **표본을
    # 위해 따로 기다리지 않아도 된다.** 그 구간에는 우리 수집 작업도 들어가는데,
    # 그것 역시 이 도구가 서버에 주는 부하의 일부라 빼는 편이 오히려 사실과 멀다.
    records: List[store.SampleRecord] = []
    watch = _Stopwatch()

    def collect():
        records.extend(
            collector.collect_all(
                accounts,
                df_timeout_seconds=settings.df_timeout_seconds,
                quota_command=settings.quota_command,
            )
        )

    server_sample = None
    with watch.stage("df"):
        try:
            server_sample = servermon.take_one(
                work=collect, with_cmdline=settings.record_process_cmdline
            )
            if server_sample is None:
                watch.server_sample = "잴 수 없음 (/proc 없음)"
                logger.warning("서버 부하 표본: 이 장비에서는 잴 수 없습니다 (/proc 없음)")
        except Exception as exc:  # 측정 실패가 수집을 막으면 안 된다
            watch.server_sample = f"측정 실패: {type(exc).__name__}: {exc}"
            logger.exception("서버 부하 표본 실패 (수집은 계속합니다)")
            if not records:
                collect()

    with watch.stage("save"), store.session(data_dir) as conn:
        store.insert_samples(conn, records)
        store.prune_old_samples(conn, settings.sample_retention_days)

    # 서버 부하는 스캔 DB 쪽에 남긴다 - 스캔 실행 이력과 같은 곳에 있어야
    # "이 시각에 우리가 뭘 하고 있었나"를 조인 한 번으로 볼 수 있다.
    #
    # 여기서 실패해도 df 표본은 이미 저장돼 있다. 부가 정보 때문에 수집
    # 자체가 실패로 끝나면 안 되므로 예외를 삼킨다.
    if server_sample is not None:
        try:
            with watch.stage("server"), scan_store.session(data_dir) as scan_conn:
                sample_id = scan_store.save_server_sample(
                    scan_conn, server_sample, source=scan_store.SOURCE_COLLECTOR
                )
                if sample_id is None:
                    watch.server_sample = "빈 표본 (CPU·load 를 못 읽음) - 저장 안 함"
                    logger.warning("서버 부하 표본: CPU·load 를 못 읽어 저장하지 않았습니다")
                else:
                    watch.server_sample = f"저장 (id {sample_id})"
                scan_store.prune_server_samples(
                    scan_conn, settings.server_sample_retention_days
                )
                scan_store.prune_usage_events(
                    scan_conn, usage_log.DEFAULT_RETENTION_DAYS
                )
        except Exception as exc:  # pragma: no cover - 방어적 처리
            if sqlite_db.is_busy(exc):
                # 다른 작업이 스캔 DB 를 잡고 있었다 - 고장이 아니다. 표본 한 벌을
                # 건너뛸 뿐이고 df 표본은 이미 저장됐다.
                watch.server_sample = f"스캔 DB 잠김 - 건너뜀 ({exc})"
                logger.warning("스캔 DB 가 잠겨 있어 이번 서버 부하 표본은 건너뜁니다 (%s)", exc)
            else:
                watch.server_sample = f"저장 실패: {type(exc).__name__}: {exc}"
                logger.exception("서버 부하 표본 저장 실패 (수집 결과는 이미 저장됨)")

    accounts_by_id = {a.account_id: a for a in accounts}
    notify_started = time.monotonic()
    state = notifications.load_notify_state(data_dir)
    # 등급 알림은 계정마다 따로 간다. 같은 스토리지의 계정 여럿이 한꺼번에 차면
    # 같은 이야기가 계정 수만큼 가지만, 그런 일은 드물어 묶지 않고 있는 그대로
    # 보인다 (2026-10 결정). FULL 임박 예측은 `forecast_notify` 가 파일시스템
    # 단위로 이미 합치고, 화면의 "무엇부터 할까"도 스토리지 단위로 묶는다.
    for record in records:
        account = accounts_by_id.get(record.account_id)
        if account is None:
            continue
        notifications.maybe_notify(
            data_dir,
            account,
            record,
            state,
            min_tier=settings.notification_min_tier,
            cooldown_minutes=settings.notification_cooldown_minutes,
            mode=settings.notification_mode,
            command=settings.notification_command,
            webhook_url=settings.notification_webhook_url,
            timeout_seconds=settings.notification_timeout_seconds,
            immediate_pct=settings.immediate_notify_pct,
        )
    notifications.save_notify_state(data_dir, state)

    # 오래된 알림 파일을 치운다.
    #
    # 치우는 함수(`prune_old_events`)는 처음부터 있었는데 **부르는 곳이
    # 없었다.** 임계값을 넘은 계정은 15분마다 파일이 하나씩 생기므로 outbox 가
    # 끝없이 자랐고, 팝업을 확인할 때마다 NFS 위에서 그 전부를 읽었다.
    # 다른 보존 정리와 같은 자리(매 수집)에 둔다 - 치운 뒤에는 남은 것만
    # 훑으므로 비용이 거의 없다.
    try:
        popup_queue.prune_old_events(data_dir, popup_queue.EVENT_RETENTION_DAYS)
    except Exception:  # pragma: no cover - 정리 실패가 수집을 막으면 안 된다
        logger.exception("오래된 알림 정리 실패 (수집 결과는 이미 저장됨)")
    watch.seconds["notify"] = time.monotonic() - notify_started

    home = forecast_and_home(data_dir, config, watch, write=False)

    # 밀린 보고서가 있으면 여기서 만든다.
    #
    # 예전에는 야간 상세 스캔이 끝날 때만 만들었는데, 스캔은 시간창 밖이거나
    # 잠금이 잡혀 있으면 아예 시작하지 않고 그대로 돌아간다. 그러면 일간
    # 보고서까지 함께 사라졌다 - 정작 일간 보고서 내용은 df 표본 요약이라
    # 스캔과 아무 상관이 없는데도. 수집은 15분마다 확실히 도니까 여기에 건다.
    #
    # 이미 만든 것은 다시 만들지 않으므로(파일 존재 확인) 15분마다 불려도
    # 대부분은 stat 몇 번으로 끝난다.
    try:
        with watch.stage("reports"):
            reports.ensure_scheduled(data_dir, config)
    except Exception:  # pragma: no cover - 방어적 처리
        logger.exception("보고서 생성 실패 (수집 결과는 이미 저장됨)")

    # 홈 한 장을 파일로 - 창은 이것만 읽는다 (`home_view`). 실패하면 창이 예전처럼 DB 에서
    # 읽을 뿐이다. 보고서 뒤에 쓰는 것은 예전 순서 그대로다.
    _write_home(data_dir, config, home, watch)
    # 상세 스캔 상태도 한 장 (`scan_view`) - 스캔이 안 도는 낮에도 창이 DB 를 열지 않게.
    # 스캔이 도는 중이면 스캔이 30초마다 쓰므로 여기서는 건너뛴다.
    _write_scan_view(data_dir, config, watch)

    watch.save(data_dir)

    return records
