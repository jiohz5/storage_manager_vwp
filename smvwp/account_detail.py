"""계정 하나에 대해 우리가 아는 것을 한곳에 모은다 (Qt 없음).

## 왜 필요한가

한 계정에 대한 사실이 화면 네 군데에 흩어져 있었다 - 사용률과 예측은 홈 표,
추세는 추세 창, 밤새 잰 것과 큰 파일은 상세 스캔 탭, 백업 여부는 헬스체크.
"이 계정 왜 이래?" 하나에 답하려면 네 군데를 오가야 했다.

여기서 그 넷을 한 번에 읽어 `AccountDetail` 하나로 준다. 화면은 이것을
그리기만 한다.

## 여기서 지키는 것

**한 조각이 터져도 나머지는 준다.** 헬스체크가 실패했다고 사용률까지 못 보게
되면 훨씬 나쁜 실패다 (대시보드가 예측을 다루는 방식과 같다). 무엇을 못
읽었는지는 `failures` 에 담아 화면이 말하게 한다 - 조용히 비워 두면 "없다"로
읽힌다.

**GUI 스레드에서 부르지 않는다.** 스캔 상태·헬스체크·예측이 각각 DB 를 훑고,
데이터 디렉터리가 NFS 위면 그 동안 창이 굳는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from . import config as config_module
from . import forecast_notify, health, large_files, nightly_scan, store, tiers, trend

logger = logging.getLogger(__name__)

# 창에 그릴 추세 기간. 표본 보존 기간과 맞춘다 - 더 길게 잡아 봐야 앞쪽이
# 통째로 빈 그래프가 된다.
TREND_DAYS = 90

# 표에 올릴 줄 수. 이 창은 훑어보라고 있는 것이지 뒤지라고 있는 것이 아니다 -
# 더 봐야 하면 상세 스캔 탭과 폴더 펼쳐 보기가 있다.
ROWS_SHOWN = 12

# 무엇을 못 읽었는지 (화면이 그대로 말한다).
PART_SAMPLE = "sample"
PART_TREND = "trend"
PART_FORECAST = "forecast"
PART_SCAN = "scan"
PART_HEALTH = "health"


@dataclass
class AccountDetail:
    """계정 하나에 대해 아는 것 전부."""

    account_id: str
    name: str
    path: str
    kind: str = config_module.ACCOUNT_KIND_UNSET
    backup_name: str = ""

    sample: Optional[store.SampleRecord] = None
    forecast: object = None
    series: object = None                      # trend.Series
    scan: object = None                        # nightly_scan.AccountScanSnapshot
    large_files: List[object] = field(default_factory=list)
    health: List[health.RunHealth] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)

    # -- 몇 가지 파생값 ------------------------------------------------
    @property
    def tier(self) -> str:
        return self.sample.overall_tier if self.sample else tiers.UNKNOWN

    @property
    def growth_rows(self) -> List[object]:
        """경로별 증감. 기준선만 있으면 큰 경로 목록이 대신 온다."""

        if self.scan is None:
            return []
        return list(self.scan.growth or self.scan.top_paths or [])

    @property
    def measured_delta_kb(self) -> Optional[int]:
        """지난 스캔 대비 이 계정이 얼마나 늘었나. 견줄 것이 없으면 None."""

        if self.scan is None:
            return None
        current, previous = self.scan.measured_kb, self.scan.previous_measured_kb
        if current is None or previous is None:
            return None
        return current - previous

    @property
    def backed_up(self) -> List[health.RunHealth]:
        return [item for item in self.health if item.status == health.BACKED_UP]

    @property
    def at_risk(self) -> List[health.RunHealth]:
        return [item for item in self.health if item.risky]

    @property
    def reclaimable_kb(self) -> int:
        return sum(item.reclaimable_kb for item in self.health)

    @property
    def has_backup_link(self) -> bool:
        return any(item.status != health.NO_LINK for item in self.health)


def read(
    data_dir: Path,
    config: config_module.AppConfig,
    account_id: str,
    trend_days: int = TREND_DAYS,
    now: Optional[datetime] = None,
) -> Optional[AccountDetail]:
    """계정 하나에 대한 것을 모아 온다 (읽기 전용). 계정이 없으면 None."""

    account = config_module.find_account(config, account_id)
    if account is None:
        return None

    backup = (
        config_module.find_account(config, account.backup_account_id)
        if account.backup_account_id
        else None
    )
    detail = AccountDetail(
        account_id=account.account_id,
        name=account.name,
        path=account.path,
        kind=account.kind,
        backup_name=backup.name if backup else "",
    )
    now = now or datetime.now(timezone.utc)

    _read_samples(detail, data_dir, account_id, trend_days, now)
    _read_forecast(detail, data_dir, config, account_id)
    _read_scan(detail, data_dir, config, account)
    _read_health(detail, data_dir, config, account_id)
    return detail


def _read_samples(detail, data_dir, account_id, trend_days, now) -> None:
    """지금 얼마인가 + 추세. 둘은 같은 연결로 읽는다 (왕복을 아낀다)."""

    conn = None
    try:
        conn = store.connect(data_dir)
        detail.sample = store.latest_samples(conn).get(account_id)
    except Exception:
        logger.exception("표본 읽기 실패 (%s)", account_id)
        detail.failures.append(PART_SAMPLE)
        if conn is not None:
            conn.close()
        return

    try:
        since = now - timedelta(days=trend_days)
        samples = store.samples_since(conn, account_id, since)
        # 창을 명시한다. 안 그러면 표본이 하루치뿐인 계정의 하루가 화면을 꽉
        # 채워 "90일 추세" 라며 하루를 보여 준다.
        detail.series = trend.build(samples, since=since, until=now)
    except Exception:
        logger.exception("추세 읽기 실패 (%s)", account_id)
        detail.failures.append(PART_TREND)
    finally:
        conn.close()


def _read_forecast(detail, data_dir, config, account_id) -> None:
    try:
        for item in forecast_notify.build_forecasts(data_dir, config):
            if item.account_id == account_id:
                detail.forecast = item
                break
    except Exception:
        logger.exception("예측 실패 (%s)", account_id)
        detail.failures.append(PART_FORECAST)


def _read_scan(detail, data_dir, config, account) -> None:
    """야간 스캔이 이 계정에 대해 남긴 것.

    **이 계정만 담은 설정으로 묻는다.** `get_status_snapshot` 은 계정마다 십여
    개의 쿼리를 던지므로, 계정이 스물이면 창 하나 띄우는 데 그 전부를 읽게
    된다."""

    try:
        one = replace(config, accounts=[account])
        snapshot = nightly_scan.get_status_snapshot(data_dir, one)
        detail.scan = next(iter(snapshot.accounts), None)
        if detail.scan is not None:
            detail.large_files = large_files.build(
                detail.scan.large_files, detail.scan.measured_kb
            )
    except Exception:
        logger.exception("스캔 상태 읽기 실패 (%s)", account.account_id)
        detail.failures.append(PART_SCAN)


def _read_health(detail, data_dir, config, account_id) -> None:
    """백업이 된 과제와 안 된 과제.

    `check_all` 은 전체를 보지만, 이 계정 것만 걸러 쓴다 - 대조는 **연결된
    백업 계정**의 내용을 함께 봐야 하므로 이 계정만 떼어 낼 수가 없다."""

    try:
        summary = health.check_all(data_dir, config)
        detail.health = [
            item for item in summary.items if item.account_id == account_id
        ]
    except Exception:
        logger.exception("헬스체크 실패 (%s)", account_id)
        detail.failures.append(PART_HEALTH)
