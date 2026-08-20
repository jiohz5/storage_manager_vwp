"""등급 배지 등 작은 재사용 위젯.

색상 + 텍스트 라벨을 항상 함께 쓴다 (DESIGN.md 2부 6절 "1차 채택" —
색상만으로 등급을 구분하지 않는다).
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QTableWidgetItem,
    QWidget,
)

from .. import i18n, tiers
from . import theme


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



def format_size_pair(used_kb: Optional[int], total_kb: Optional[int]) -> str:
    """`17.2 / 40.0 TB` 처럼 사용량과 총 용량을 한 칸에 보여준다.

    **두 값에 같은 단위를 쓴다.** 각자 알아서 단위를 고르게 하면
    `950.0 GB / 1.0 TB`처럼 나와서, 한눈에 비교하라고 붙여 놓은 표시가 오히려
    암산을 요구하게 된다. 큰 쪽(총 용량)의 단위로 맞춘다.

    **단위는 TB에서 멈춘다.** 실제 계정은 많아야 수십 TB라 PB로 올라가면
    `0.0 / 0.0 PB`처럼 뭉개져 아무것도 못 읽는다. 100TB대까지는 TB로 두는 편이
    훨씬 잘 읽힌다 (`99.1 / 100.0 TB`).
    """

    if total_kb is None:
        return format_kb(used_kb) if used_kb is not None else "-"

    unit, divisor = _unit_for(total_kb, max_unit="TB")
    used_text = f"{used_kb / divisor:,.1f}" if used_kb is not None else "?"
    return f"{used_text} / {total_kb / divisor:,.1f} {unit}"


_UNITS = ("KB", "MB", "GB", "TB", "PB")


def _unit_for(size_kb: int, max_unit: str = "PB") -> "tuple":
    """이 크기를 읽기 좋은 단위와 그 나눗수. `max_unit`에서 올라가기를 멈춘다."""

    limit = _UNITS.index(max_unit)
    value = float(size_kb)
    divisor = 1.0
    for index, unit in enumerate(_UNITS):
        if abs(value) < 1024 or index >= limit:
            return unit, divisor
        value /= 1024
        divisor *= 1024
    return max_unit, divisor  # pragma: no cover - 위 루프에서 반환됨


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
    """등급을 배경색 + 텍스트로 함께 보여주는 알약 모양 라벨."""

    def __init__(self, tier: str = tiers.UNKNOWN, pct: Optional[float] = None, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_tier(tier, pct)

    def set_tier(self, tier: str, pct: Optional[float]) -> None:
        self.setText(tier_badge_text(tier, pct))
        color = tiers.color(tier)
        self.setStyleSheet(
            f"background-color: {color}; color: white; border-radius: 9px; "
            f"padding: 3px 10px; font-weight: bold; font-size: {theme.FONT_CAPTION}px;"
        )


def badge_cell(tier: str, pct: Optional[float]) -> QWidget:
    """표 칸에 넣을 배지.

    배지를 칸에 그대로 넣으면 칸 전체로 늘어나 알약 모양이 사라지고, 칸이
    좁으면 글자가 잘린다. 가운데 정렬하는 컨테이너로 감싸 원래 크기를
    유지시킨다."""

    container = QWidget()
    # 전역 QSS의 QWidget 배경(창 배경색)이 흰 표 위에 회색 사각형으로 찍히는
    # 것을 막는다. 행 강조 색도 이 칸에서만 끊기면 안 된다.
    container.setStyleSheet("background: transparent;")
    box = QHBoxLayout(container)
    box.setContentsMargins(6, 3, 6, 3)
    box.addStretch(1)
    box.addWidget(TierBadge(tier, pct))
    box.addStretch(1)
    return container


def badge_column_width(font_metrics) -> int:
    """등급 열에 필요한 폭.

    표의 `ResizeToContents`는 칸 위젯(배지)의 크기를 계산에 넣지 않는다.
    그래서 가장 긴 등급 문구를 기준으로 직접 재서 열 폭을 정해 준다 - 이걸
    안 하면 배지 글자가 잘린다."""

    widest = 0
    for tier in tiers.LABELS:
        text = tier_badge_text(tier, 100.0)
        widest = max(widest, font_metrics.width(text))
    # 배지 좌우 padding(10*2) + 컨테이너 여백(6*2) + 여유
    return widest + 20 + 12 + 10



class NumericItem(QTableWidgetItem):
    """숫자로 정렬되는 표 항목.

    기본 `QTableWidgetItem`은 **보이는 글자**로 정렬한다. 그러면 `900.0 MB`가
    `1.5 GB`보다 크게 잡히고, `9.0%`가 `10.0%`보다 뒤로 간다 - 크기를 보려고
    정렬했는데 결과가 거짓이 된다. 정렬용 숫자를 따로 들고 그걸로 비교한다.

    값이 없는 행(`-`)은 항상 아래로 보낸다. 오름차순으로 정렬했을 때 빈 칸이
    맨 위를 차지하면 정작 보려던 것이 화면 밖으로 밀린다.
    """

    def __init__(self, text: str, value=None):
        super().__init__(text)
        self._value = value

    def __lt__(self, other) -> bool:  # noqa: D105 - Qt 정렬 훅
        mine = self._value
        theirs = getattr(other, "_value", None)
        if mine is None and theirs is None:
            return self.text() < other.text()
        if mine is None:
            return False   # 값 없음은 항상 뒤로
        if theirs is None:
            return True
        return mine < theirs


class TierRowDelegate(QStyledItemDelegate):
    """등급이 주의 이상인 행의 배경을 옅게 칠하는 델리게이트.

    `QTableWidgetItem.setBackground()`을 쓰지 않는 이유: QSS로 `::item`을
    스타일링하면 Qt가 항목 배경을 스타일시트 기준으로 그려서 setBackground이
    무시된다(Qt의 알려진 동작). 델리게이트에서 직접 칠하면 두 방식이
    충돌하지 않는다.

    색은 등급을 전달하는 유일한 수단이 아니라 보조 수단이다 - 등급은 같은 행의
    배지에 글자로도 항상 보인다."""

    def __init__(self, tint_for_row, parent=None):
        super().__init__(parent)
        self._tint_for_row = tint_for_row

    def paint(self, painter, option, index) -> None:
        color = self._tint_for_row(index.row())
        # 선택된 행은 선택색이 우선이다 - 그 위에 등급색을 덧칠하면 어느 행을
        # 고른 것인지 알 수 없게 된다.
        if color and not (option.state & QStyle.State_Selected):
            painter.fillRect(option.rect, QColor(color))
        super().paint(painter, option, index)


class UsageBar(QWidget):
    """사용률을 숫자 + 막대로 함께 보여준다.

    숫자만 있으면 여러 계정을 훑을 때 서로 비교가 안 된다. 막대를 옆에 두면
    어느 쪽이 더 찼는지 읽지 않고도 보인다. 색은 등급 색을 그대로 쓰므로
    표의 다른 등급 표시와 어긋나지 않는다."""

    BAR_HEIGHT = 7
    TEXT_WIDTH = 52

    def __init__(self, pct: Optional[float], tier: str, parent=None):
        super().__init__(parent)
        self._pct = pct
        self._tier = tier
        self.setMinimumWidth(self.TEXT_WIDTH + 60)
        # 칸 위젯은 표 배경 위에 얹히므로 자기 배경을 그리면 안 된다.
        self.setStyleSheet("background: transparent;")

    def sizeHint(self) -> QSize:
        return QSize(self.TEXT_WIDTH + 90, 26)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        text = f"{self._pct:.1f}%" if self._pct is not None else i18n.t("common.none")
        text_rect = self.rect().adjusted(0, 0, -(self.width() - self.TEXT_WIDTH), 0)
        painter.setPen(QColor(theme.TEXT))
        font = painter.font()
        font.setWeight(75)  # Bold - 숫자가 표에서 먼저 읽혀야 한다
        painter.setFont(font)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, text)

        if self._pct is None:
            painter.end()
            return

        track_left = self.TEXT_WIDTH + 4
        track_width = max(0, self.width() - track_left - 6)
        top = (self.height() - self.BAR_HEIGHT) / 2
        radius = self.BAR_HEIGHT / 2

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BORDER))
        painter.drawRoundedRect(
            QRectF(track_left, top, track_width, self.BAR_HEIGHT), radius, radius
        )

        ratio = max(0.0, min(1.0, self._pct / 100.0))
        filled = track_width * ratio
        if filled > 0:
            # 아주 작은 값도 보이도록 최소 폭을 준다 (0.5%가 아예 안 보이면
            # "측정이 안 된 것"과 구분되지 않는다).
            filled = max(filled, self.BAR_HEIGHT)
            painter.setBrush(QColor(tiers.color(self._tier)))
            painter.drawRoundedRect(
                QRectF(track_left, top, filled, self.BAR_HEIGHT), radius, radius
            )
        painter.end()
