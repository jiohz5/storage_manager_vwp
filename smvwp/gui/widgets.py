"""Fluent GUI용 표시 헬퍼.

값 계산은 전부 백엔드 모듈에 있으므로 여기서는 **문자열 만들기와 색
고르기만** 한다.

지켜야 할 규칙 두 가지 (DESIGN.md 1부):
- 등급은 색만이 아니라 **텍스트로도** 보여야 한다 (색맹·흑백 출력 대비).
- 근거가 없는 값은 지어내지 않고 사유를 밝힌다 (`예측 불가(표본 부족)` 등).
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from .. import i18n, tiers


def tier_badge_text(tier: str, pct: Optional[float]) -> str:
    return tiers.display_text(tier, pct)


class TierBadge(QLabel):
    """등급을 배경색 + 텍스트로 함께 보여주는 배지."""

    def __init__(self, tier: str = tiers.UNKNOWN, pct: Optional[float] = None, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_tier(tier, pct)

    def set_tier(self, tier: str, pct: Optional[float]) -> None:
        self.setText(tier_badge_text(tier, pct))
        self.setStyleSheet(
            f"background-color: {tiers.color(tier)}; color: white; border-radius: 4px;"
            "padding: 2px 8px; font-weight: bold;"
        )


def format_kb(size_kb: Optional[int]) -> str:
    """KB 정수를 사람이 읽는 크기 문자열로. 값이 없으면 '-'."""

    if size_kb is None:
        return "-"
    value = float(size_kb)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024 or unit == "PB":
            if unit == "KB":
                return f"{int(value):,} KB"
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} PB"  # pragma: no cover


def format_bytes(size_bytes: Optional[int]) -> str:
    if size_bytes is None:
        return "-"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value):,} B"
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} TB"  # pragma: no cover


def format_kb_delta(delta_kb: Optional[int]) -> str:
    """증감량 표시 - 부호를 명시하고 0은 '변화 없음'으로."""

    if delta_kb is None:
        return "-"
    if delta_kb == 0:
        return i18n.t("scan.no_change")
    sign = "+" if delta_kb > 0 else "-"
    return f"{sign}{format_kb(abs(delta_kb))}"


def _unavailable_text(prediction) -> str:
    reason_key = f"forecast.reason.{prediction.reason}" if prediction.reason else ""
    reason = i18n.t(reason_key) if reason_key else ""
    unavailable = i18n.t("forecast.unavailable")
    if reason and reason != reason_key:
        return f"{unavailable}({reason})"
    return unavailable


def format_prediction(prediction) -> str:
    if not prediction.ok or prediction.hours_to_full is None:
        return _unavailable_text(prediction)
    if prediction.hours_to_full < 1:
        return i18n.t("forecast.within_hour")
    if prediction.hours_to_full < 48:
        return i18n.t("forecast.hours", hours=f"{prediction.hours_to_full:.0f}")
    return i18n.t("forecast.days", days=f"{prediction.days_to_full:.0f}")


def format_forecast_cell(forecast, imminent_hours: float = 48.0) -> str:
    """임박했으면 시간 하나만, 평상시엔 7일/30일을 나란히."""

    if forecast is None:
        return i18n.t("common.none")
    if forecast.imminent.ok and forecast.imminent.hours_to_full is not None:
        if forecast.imminent.hours_to_full < imminent_hours:
            return format_prediction(forecast.imminent)

    short, long = forecast.short_trend, forecast.long_trend
    if not short.ok and not long.ok and short.reason == long.reason:
        return _unavailable_text(short)
    return i18n.t(
        "forecast.pair", short=format_prediction(short), long=format_prediction(long)
    )


def format_forecast_tooltip(forecast, window_hours: int) -> str:
    if forecast is None:
        return ""
    slope = forecast.imminent.slope_kb_per_hour
    slope_text = f"{slope:,.0f} KB/h" if slope else i18n.t("common.none")
    return i18n.t(
        "forecast.tooltip",
        short=format_prediction(forecast.short_trend),
        long=format_prediction(forecast.long_trend),
        window=window_hours,
        slope=slope_text,
    )
