from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class CapacityForecast:
    samples: int
    window_days: int
    slope_pct_per_day: float
    current_pct: int
    days_to_alert: Optional[int]
    days_to_full: Optional[int]


@dataclass(frozen=True)
class GrowthAnomaly:
    detected: bool
    latest_delta_kb: int
    baseline_median_kb: int
    threshold_kb: int
    ratio: float


def _days_to_target(
    current_pct: int,
    target_pct: int,
    slope: float,
    max_days: int,
) -> Optional[int]:
    if current_pct >= target_pct:
        return 0
    if slope <= 0.02:
        return None
    days = int(math.ceil((target_pct - current_pct) / slope))
    return days if 0 <= days <= max_days else None


def capacity_forecast(
    points: Iterable[Tuple[str, int]],
    window_days: int,
    alert_threshold: int = 95,
    max_days: int = 3650,
) -> Optional[CapacityForecast]:
    materialized = list(points)[-max(3, window_days) :]
    if len(materialized) < 3:
        return None

    parsed: List[Tuple[int, int]] = []
    for day, percent in materialized:
        try:
            ordinal = datetime.strptime(day, "%Y-%m-%d").date().toordinal()
        except ValueError:
            continue
        parsed.append((ordinal, int(percent)))
    if len(parsed) < 3 or parsed[-1][0] == parsed[0][0]:
        return None

    x_mean = statistics.fmean(row[0] for row in parsed)
    y_mean = statistics.fmean(row[1] for row in parsed)
    denominator = sum((row[0] - x_mean) ** 2 for row in parsed)
    if denominator == 0:
        return None
    slope = sum(
        (ordinal - x_mean) * (percent - y_mean)
        for ordinal, percent in parsed
    ) / denominator
    current = int(parsed[-1][1])
    return CapacityForecast(
        samples=len(parsed),
        window_days=window_days,
        slope_pct_per_day=float(slope),
        current_pct=current,
        days_to_alert=_days_to_target(current, alert_threshold, slope, max_days),
        days_to_full=_days_to_target(current, 100, slope, max_days),
    )


def detect_growth_anomaly(
    used_points: Sequence[Tuple[str, int]],
    multiplier: float = 3.0,
    min_growth_kb: int = 100 * 1024 * 1024,
) -> GrowthAnomaly:
    if len(used_points) < 7:
        return GrowthAnomaly(False, 0, 0, min_growth_kb, 0.0)

    deltas = [
        int(used_points[index][1]) - int(used_points[index - 1][1])
        for index in range(1, len(used_points))
    ]
    latest = deltas[-1]
    history = [delta for delta in deltas[:-1] if delta > 0]
    baseline = int(statistics.median(history)) if history else 0
    deviations = [abs(delta - baseline) for delta in history]
    mad = int(statistics.median(deviations)) if deviations else 0
    relative_threshold = int(baseline * multiplier)
    dispersion_threshold = baseline + 3 * mad
    threshold = max(int(min_growth_kb), relative_threshold, dispersion_threshold)
    ratio = float(latest / baseline) if baseline > 0 else (float("inf") if latest > 0 else 0.0)
    return GrowthAnomaly(
        detected=latest >= threshold,
        latest_delta_kb=latest,
        baseline_median_kb=baseline,
        threshold_kb=threshold,
        ratio=ratio,
    )
