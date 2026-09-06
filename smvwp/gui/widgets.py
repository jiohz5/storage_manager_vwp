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


# 서식은 `smvwp.formatting` 으로 옮겼다 - Qt 를 쓰지 않는 코드가 Qt 파일 안에
# 있으면 시험할 수 없기 때문이다(개발 PC 에 PyQt5 가 없다). 부르는 자리를 한꺼번에
# 고치면 GUI 시험이 없는 상태에서 위험이 커지므로, 여기서 이름을 그대로 이어
# 준다. 새로 쓰는 코드는 `smvwp.formatting` 을 직접 부르는 편이 낫다.
from ..formatting import (  # noqa: F401
    format_bytes,
    format_forecast_cell,
    format_forecast_tooltip,
    format_kb,
    format_kb_delta,
    format_prediction,
    format_size_pair,
    scan_label,
    tier_badge_text,
)


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
