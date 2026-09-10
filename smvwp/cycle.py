"""수집 -> 저장 -> outbox 알림 -> 보존기간 정리, 한 사이클 전체.

이 모듈은 PyQt5에 의존하지 않는다 - GUI 내부 타이머(`smvwp.scheduler`)와
cron 경로(`smvwp_cli.py collect`)가 이 함수 하나를 공유해서, 두
실행 경로("cron 또는 백그라운드 타이머", DESIGN.md 2부 7절 3번)의
동작이 어긋나지 않게 한다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from . import collector, config as config_module, forecast_notify, notifications, reports
from . import scan_store, servermon, store

logger = logging.getLogger(__name__)


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

    def collect():
        records.extend(
            collector.collect_all(
                accounts,
                df_timeout_seconds=settings.df_timeout_seconds,
                quota_command=settings.quota_command,
            )
        )

    server_sample = None
    try:
        server_sample = servermon.take_one(
            work=collect, with_cmdline=settings.record_process_cmdline
        )
    except Exception:  # pragma: no cover - 측정 실패가 수집을 막으면 안 된다
        logger.exception("서버 부하 표본 실패 (수집은 계속합니다)")
        if not records:
            collect()

    conn = store.connect(data_dir)
    try:
        for record in records:
            store.insert_sample(conn, record)
        store.prune_old_samples(conn, settings.sample_retention_days)
    finally:
        conn.close()

    # 서버 부하는 스캔 DB 쪽에 남긴다 - 스캔 실행 이력과 같은 곳에 있어야
    # "이 시각에 우리가 뭘 하고 있었나"를 조인 한 번으로 볼 수 있다.
    #
    # 여기서 실패해도 df 표본은 이미 저장돼 있다. 부가 정보 때문에 수집
    # 자체가 실패로 끝나면 안 되므로 예외를 삼킨다.
    if server_sample is not None:
        try:
            scan_conn = scan_store.connect(data_dir)
            try:
                scan_store.save_server_sample(
                    scan_conn, server_sample, source=scan_store.SOURCE_COLLECTOR
                )
                scan_store.prune_server_samples(
                    scan_conn, settings.server_sample_retention_days
                )
            finally:
                scan_conn.close()
        except Exception:  # pragma: no cover - 방어적 처리
            logger.exception("서버 부하 표본 저장 실패 (수집 결과는 이미 저장됨)")

    accounts_by_id = {a.account_id: a for a in accounts}
    state = notifications.load_notify_state(data_dir)
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

    # 예측/급증 알림은 수집이 끝난 뒤 별도로 처리한다. 여기서 실패해도 이미
    # 저장된 df 표본은 그대로 남아야 하므로 예외를 삼킨다 - 예측은 부가
    # 정보이고, 수집 자체가 이 사이클의 본질이다.
    try:
        forecasts = forecast_notify.build_forecasts(data_dir, config)
        forecast_notify.notify_forecasts(data_dir, config, forecasts)
    except Exception:  # pragma: no cover - 방어적 처리
        logger.exception("FULL 예측 알림 실패 (수집 결과는 이미 저장됨)")

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
        reports.ensure_scheduled(data_dir, config)
    except Exception:  # pragma: no cover - 방어적 처리
        logger.exception("보고서 생성 실패 (수집 결과는 이미 저장됨)")

    return records
