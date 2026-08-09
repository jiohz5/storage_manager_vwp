"""FULL 도달 예측과 급증 판정 (15분 표본 기반).

`samples` 테이블에 쌓이는 15분 `df` 표본만으로 "이 파일시스템이 언제 차는가"를
추정한다. 별도 수집을 추가하지 않는다 - 이미 있는 데이터를 읽기만 한다.

## 왜 최소제곱 회귀인가

첫 표본과 마지막 표본의 차이로 기울기를 내면, 하필 그 두 시점에 큰 파일이
지워졌거나 생겼을 때 예측이 통째로 휘둘린다. `df` 값은 파일 생성/삭제로 톱니
처럼 흔들리므로, 창 안의 모든 점을 쓰는 최소제곱 회귀가 훨씬 안정적이다.

## 숫자를 만들지 않는 규칙 (DESIGN.md 1부 "과장하지 않는 UI")

아래 중 하나라도 해당하면 예상 시각을 계산하지 않고 "예측 불가"로 둔다.
그럴듯한 숫자를 내놓는 것보다 모른다고 말하는 편이 낫다.

- 창 안의 유효 표본이 최소치에 못 미침 (`insufficient_samples`)
- 기울기가 0 이하 = 줄고 있거나 정체 (`not_growing`)
- 도달 예상이 너무 먼 미래 (`too_far`) - 기울기가 미미하면 산술적으로는
  "3만 년 뒤"가 나오는데, 이건 예측이 아니라 잡음이다.

## 공유 파일시스템

`df`는 계정 자체가 아니라 그 경로가 속한 **파일시스템 전체** 값이다. 같은
파일시스템에 계정이 여러 개면 예측도 같으므로, 알림은 파일시스템 단위로
합쳐야 한다 (`group_by_filesystem` 참고). 대시보드는 계정별로 보여준다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

HOURS_PER_YEAR = 365 * 24

REASON_INSUFFICIENT = "insufficient_samples"
REASON_NOT_GROWING = "not_growing"
REASON_TOO_FAR = "too_far"


@dataclass
class Prediction:
    """한 창(window)에서 낸 FULL 도달 추정."""

    ok: bool
    hours_to_full: Optional[float] = None
    slope_kb_per_hour: Optional[float] = None
    sample_count: int = 0
    reason: str = ""

    @property
    def days_to_full(self) -> Optional[float]:
        return None if self.hours_to_full is None else self.hours_to_full / 24

    def eta(self, now: datetime) -> Optional[datetime]:
        if self.hours_to_full is None:
            return None
        return now + timedelta(hours=self.hours_to_full)


@dataclass
class CapacityForecast:
    """계정 하나에 대한 예측 묶음."""

    account_id: str
    filesystem: Optional[str] = None
    imminent: Prediction = field(default_factory=lambda: Prediction(ok=False))
    short_trend: Prediction = field(default_factory=lambda: Prediction(ok=False))
    long_trend: Prediction = field(default_factory=lambda: Prediction(ok=False))
    surge_kb: Optional[int] = None  # 급증 창 동안의 실제 증가량
    is_surge: bool = False


def _parse_time(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    # 저장은 UTC aware로 하지만, 과거 데이터나 수동 편집을 대비해 naive면
    # UTC로 간주한다 (비교 시 예외가 나지 않도록).
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def linear_slope(points: Sequence[Tuple[float, float]]) -> Optional[float]:
    """최소제곱 회귀 기울기. 점이 2개 미만이거나 x가 모두 같으면 None."""

    count = len(points)
    if count < 2:
        return None
    mean_x = sum(x for x, _ in points) / count
    mean_y = sum(y for _, y in points) / count
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return None
    return numerator / denominator


def _usable_samples(samples: Sequence, now: datetime, window_hours: float) -> List:
    """창 안의 성공한 표본만, 오래된 것부터."""

    cutoff = now - timedelta(hours=window_hours)
    usable = []
    for sample in samples:
        if not sample.ok or sample.used_kb is None:
            continue
        collected = _parse_time(sample.collected_at)
        if collected is None or collected < cutoff or collected > now:
            continue
        usable.append((collected, sample))
    usable.sort(key=lambda item: item[0])
    return usable


def predict(
    samples: Sequence,
    now: datetime,
    window_hours: float,
    min_samples: int,
    max_years: int = 10,
) -> Prediction:
    """창 안의 표본으로 FULL 도달까지 남은 시간을 추정한다."""

    usable = _usable_samples(samples, now, window_hours)
    if len(usable) < min_samples:
        return Prediction(ok=False, sample_count=len(usable), reason=REASON_INSUFFICIENT)

    origin = usable[0][0]
    points = [
        ((collected - origin).total_seconds() / 3600.0, float(sample.used_kb))
        for collected, sample in usable
    ]
    slope = linear_slope(points)
    if slope is None or slope <= 0:
        return Prediction(
            ok=False,
            slope_kb_per_hour=slope,
            sample_count=len(usable),
            reason=REASON_NOT_GROWING,
        )

    latest_sample = usable[-1][1]
    available_kb = latest_sample.avail_kb
    if available_kb is None:
        return Prediction(ok=False, sample_count=len(usable), reason=REASON_INSUFFICIENT)

    hours_to_full = max(0.0, available_kb / slope)
    if hours_to_full > max_years * HOURS_PER_YEAR:
        # 기울기가 미미해 산술적으로만 나오는 먼 미래. 예측이 아니라 잡음이다.
        return Prediction(
            ok=False,
            slope_kb_per_hour=slope,
            sample_count=len(usable),
            reason=REASON_TOO_FAR,
        )

    return Prediction(
        ok=True,
        hours_to_full=hours_to_full,
        slope_kb_per_hour=slope,
        sample_count=len(usable),
    )


def measure_surge(samples: Sequence, now: datetime, window_hours: float) -> Optional[int]:
    """창 동안의 실제 사용량 증가분(KB). 표본이 2개 미만이면 None.

    회귀가 아니라 실제 관측값의 차이를 쓴다 - "얼마나 늘었나"는 추정이 아니라
    사실이어야 하기 때문."""

    usable = _usable_samples(samples, now, window_hours)
    if len(usable) < 2:
        return None
    return int(usable[-1][1].used_kb - usable[0][1].used_kb)


def build_forecast(
    account_id: str,
    samples: Sequence,
    settings,
    now: Optional[datetime] = None,
) -> CapacityForecast:
    now = now or datetime.now(timezone.utc)
    filesystem = None
    for sample in samples:
        if sample.ok and sample.filesystem:
            filesystem = sample.filesystem
            break

    imminent = predict(
        samples,
        now,
        window_hours=settings.full_prediction_window_hours,
        min_samples=settings.full_prediction_min_samples,
        max_years=settings.full_prediction_max_years,
    )
    short_trend = predict(
        samples,
        now,
        window_hours=settings.trend_short_days * 24,
        min_samples=settings.trend_short_min_samples,
        max_years=settings.full_prediction_max_years,
    )
    long_trend = predict(
        samples,
        now,
        window_hours=settings.trend_long_days * 24,
        min_samples=settings.trend_long_min_samples,
        max_years=settings.full_prediction_max_years,
    )

    surge_kb = measure_surge(samples, now, settings.full_prediction_window_hours)
    is_surge = surge_kb is not None and surge_kb >= settings.capacity_surge_min_kb

    return CapacityForecast(
        account_id=account_id,
        filesystem=filesystem,
        imminent=imminent,
        short_trend=short_trend,
        long_trend=long_trend,
        surge_kb=surge_kb,
        is_surge=is_surge,
    )


def group_by_filesystem(forecasts: Sequence[CapacityForecast]) -> Dict[str, List[CapacityForecast]]:
    """같은 파일시스템끼리 묶는다 (알림 중복 제거용).

    `df`가 파일시스템 전체 값을 주므로, 같은 파일시스템의 계정들은 예측이
    동일하다. 계정마다 알리면 같은 경고가 N번 온다. filesystem을 모르는
    경우(수집 실패 등)에는 계정별로 따로 둔다 - 임의로 묶는 것보다 안전하다."""

    grouped: Dict[str, List[CapacityForecast]] = {}
    for forecast in forecasts:
        key = forecast.filesystem or f"__account__:{forecast.account_id}"
        grouped.setdefault(key, []).append(forecast)
    return grouped
