"""등급 배지 등 작은 재사용 위젯.

색상 + 텍스트 라벨을 항상 함께 쓴다 (DESIGN.md 2부 6절 "1차 채택" —
색상만으로 등급을 구분하지 않는다).
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel

from .. import i18n, tiers


def tier_badge_text(tier: str, pct: Optional[float]) -> str:
    return tiers.display_text(tier, pct)


def format_kb(size_kb: Optional[int]) -> str:
    """KB 정수를 사람이 읽는 크기 문자열로. 값이 없으면 '-'.

    `du -sk`가 KB 단위로 주므로 KB를 기준 단위로 삼는다. 1024 배수를 쓰되
    소수점 한 자리까지만 보여준다 (정밀도를 실제보다 높아 보이게 하지 않기
    위해 유효숫자를 늘리지 않는다)."""

    if size_kb is None:
        return "-"
    value = float(size_kb)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024 or unit == "PB":
            if unit == "KB":
                return f"{int(value):,} KB"
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} PB"  # pragma: no cover - 위 루프에서 반환됨


def _unavailable_text(prediction) -> str:
    reason_key = f"forecast.reason.{prediction.reason}" if prediction.reason else ""
    reason = i18n.t(reason_key) if reason_key else ""
    unavailable = i18n.t("forecast.unavailable")
    # 번역 키가 없으면 t()가 키를 그대로 돌려주므로, 그럴 땐 사유를 붙이지 않는다.
    if reason and reason != reason_key:
        return f"{unavailable}({reason})"
    return unavailable


def format_prediction(prediction) -> str:
    """예측 하나를 짧은 표시 문자열로. 실패 사유도 사람이 읽게 옮긴다."""

    if not prediction.ok or prediction.hours_to_full is None:
        return _unavailable_text(prediction)
    if prediction.hours_to_full < 1:
        # 반올림해서 '약 0시간'이 되면 "이미 찼다"는 뜻인지 "곧"인지 모호하다.
        return i18n.t("forecast.within_hour")
    if prediction.hours_to_full < 48:
        return i18n.t("forecast.hours", hours=f"{prediction.hours_to_full:.0f}")
    return i18n.t("forecast.days", days=f"{prediction.days_to_full:.0f}")


def format_forecast_cell(forecast, imminent_hours: float = 48.0) -> str:
    """대시보드 `FULL 예상` 칸.

    임박했을 때는 시간 단위 하나만 크게 보여주고(그 순간엔 그게 유일하게
    중요한 정보다), 평상시에는 7일/30일 추세를 나란히 보여줘 추세가 빨라졌는지
    느려졌는지를 사람이 판단하게 한다."""

    if forecast is None:
        return i18n.t("common.none")
    if forecast.imminent.ok and forecast.imminent.hours_to_full is not None:
        if forecast.imminent.hours_to_full < imminent_hours:
            return format_prediction(forecast.imminent)

    short, long = forecast.short_trend, forecast.long_trend
    # 둘 다 같은 이유로 불가면 같은 문구를 두 번 쓰지 않는다 - 칸만 길어지고
    # 읽는 사람이 얻는 정보는 같다.
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


def format_bytes(size_bytes: Optional[int]) -> str:
    """byte 단위 크기를 사람이 읽는 문자열로 (파일 실제 크기 표시용)."""

    if size_bytes is None:
        return "-"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value):,} B"
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} TB"  # pragma: no cover - 위 루프에서 반환됨


def format_kb_delta(delta_kb: Optional[int]) -> str:
    """증감량 표시 - 부호를 명시하고 0은 '변화 없음'으로."""

    if delta_kb is None:
        return "-"
    if delta_kb == 0:
        return "변화 없음"
    sign = "+" if delta_kb > 0 else "-"
    return f"{sign}{format_kb(abs(delta_kb))}"


class TierBadge(QLabel):
    """등급을 배경색 + 텍스트로 함께 보여주는 라벨."""

    def __init__(self, tier: str = tiers.UNKNOWN, pct: Optional[float] = None, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_tier(tier, pct)

    def set_tier(self, tier: str, pct: Optional[float]) -> None:
        self.setText(tier_badge_text(tier, pct))
        color = tiers.color(tier)
        self.setStyleSheet(
            f"background-color: {color}; color: white; border-radius: 4px; "
            "padding: 2px 8px; font-weight: bold;"
        )
