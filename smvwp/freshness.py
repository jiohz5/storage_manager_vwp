"""수집이 실제로 돌고 있는지 감시한다.

## 왜 "마지막 수집 시각"만으로는 부족한가

가장 흔한 실패는 cron이 조용히 안 도는 것이다. 폐쇄망에서 관리자가 아니면
`/etc/cron.deny`로 막혀 있거나, cron 데몬 자체가 그 사용자에게 동작하지 않을
수 있는데 **아무 오류도 보이지 않는다.**

문제는 이 상태에서 사용자가 GUI를 열면 시작하자마자 내부 타이머가 한 번
수집해 버린다는 점이다. 그래서 "마지막 수집 시각"만 보면 늘 방금 전이고,
정작 "지난 사흘간 cron이 한 번도 안 돌았다"는 사실은 영영 드러나지 않는다.

그래서 두 가지를 함께 본다.

1. **최신 표본 나이** - 지금 이 순간 수집이 멈춰 있는가
2. **최근 창의 수집 커버리지** - 기대한 만큼 표본이 쌓였는가

2번이 핵심이다. 15분 간격이면 24시간에 96개가 쌓여야 하는데 3개뿐이라면,
그건 "GUI를 열 때만 수집됐다"는 뜻이고 곧 cron이 죽었다는 뜻이다.

## 판정을 느슨하게 잡은 이유

cron은 몇 분씩 밀리고, 장비가 잠깐 꺼져 있을 수도 있다. 한두 번 놓친 것으로
경고하면 경고가 일상이 되어 아무도 안 본다. 그래서 기본값은 넉넉하게 두고
(최신 표본은 간격의 4배, 커버리지는 50% 미만), 정말 "안 돌고 있다"에
가까울 때만 알린다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

STATUS_OK = "ok"
STATUS_NEVER = "never"          # 한 번도 수집된 적 없음
STATUS_STALE = "stale"          # 최신 표본이 너무 오래됨
STATUS_GAPPY = "gappy"          # 최신은 있지만 그동안 많이 빠짐 (cron 의심)


@dataclass
class FreshnessStatus:
    account_id: str
    status: str
    latest_at: Optional[str] = None
    age_seconds: Optional[float] = None
    expected_samples: int = 0
    actual_samples: int = 0

    @property
    def coverage_pct(self) -> Optional[float]:
        if not self.expected_samples:
            return None
        return min(100.0, self.actual_samples / self.expected_samples * 100.0)

    @property
    def needs_attention(self) -> bool:
        return self.status != STATUS_OK


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def evaluate_account(
    account_id: str,
    samples: Sequence,
    settings,
    now: Optional[datetime] = None,
) -> FreshnessStatus:
    """계정 하나의 수집 상태를 판정한다.

    `samples`는 커버리지 창 안의 표본이면 된다 (오래된 순/최신 순 상관 없음).
    수집 실패(ok=False)한 표본도 "수집은 시도됐다"는 증거이므로 커버리지에는
    포함한다 - 여기서 보려는 것은 값의 정상 여부가 아니라 수집기가 도는지다.
    """

    now = now or datetime.now(timezone.utc)
    window_hours = settings.freshness_window_hours
    interval_seconds = settings.collector_interval_seconds

    times = [t for t in (_parse(getattr(s, "collected_at", None)) for s in samples) if t]
    times = [t for t in times if t <= now]

    if not times:
        return FreshnessStatus(account_id=account_id, status=STATUS_NEVER)

    latest = max(times)
    age_seconds = (now - latest).total_seconds()

    # 창 안의 기대 표본 수. 창보다 짧게 운영된 경우를 감안해 실제 관측 구간이
    # 짧으면 그만큼만 기대한다 (막 등록한 계정을 지연으로 몰지 않기 위함).
    window_start = now - timedelta(hours=window_hours)
    observed_start = max(min(times), window_start)
    observed_seconds = max(0.0, (now - observed_start).total_seconds())
    expected = int(observed_seconds // interval_seconds) if interval_seconds else 0
    actual = len([t for t in times if t >= window_start])

    status = STATUS_OK
    if age_seconds > interval_seconds * settings.freshness_stale_multiplier:
        status = STATUS_STALE
    elif expected >= settings.freshness_min_expected_samples:
        coverage = actual / expected * 100.0
        if coverage < settings.freshness_min_coverage_pct:
            # 최신 표본은 있는데 그동안 많이 빠졌다 = GUI 열 때만 수집된 정황.
            status = STATUS_GAPPY

    return FreshnessStatus(
        account_id=account_id,
        status=status,
        latest_at=latest.isoformat(),
        age_seconds=age_seconds,
        expected_samples=expected,
        actual_samples=actual,
    )


def evaluate_all(
    data_dir,
    config,
    now: Optional[datetime] = None,
) -> List[FreshnessStatus]:
    """활성 계정 전체의 수집 상태 (읽기 전용)."""

    from . import config as config_module
    from . import store

    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=config.settings.freshness_window_hours)

    results: List[FreshnessStatus] = []
    conn = store.connect(data_dir)
    try:
        for account in config_module.enabled_accounts(config):
            samples = store.samples_since(conn, account.account_id, since)
            results.append(
                evaluate_account(account.account_id, samples, config.settings, now)
            )
    finally:
        conn.close()
    return results


def format_age(age_seconds: Optional[float]) -> str:
    """사람이 읽는 경과 시간. 정확한 초보다 '얼마나 오래됐나'가 중요하다."""

    from . import i18n

    if age_seconds is None:
        return i18n.t("common.none")
    minutes = age_seconds / 60
    if minutes < 1:
        return i18n.t("freshness.just_now")
    if minutes < 60:
        return i18n.t("freshness.minutes_ago", minutes=int(minutes))
    hours = minutes / 60
    if hours < 24:
        return i18n.t("freshness.hours_ago", hours=int(hours))
    return i18n.t("freshness.days_ago", days=int(hours / 24))
