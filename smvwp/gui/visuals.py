"""용량을 글이 아니라 그림으로 읽게 하는 작은 위젯들.

화면이 흰 바탕에 글자만 가득하면 읽히지 않는다 (2026-11 사용자 지적). 같은 뜻을
막대 길이·알약·색 면으로 보이게 하는 부품을 여기 모은다. 숫자는 늘 곁에 글자로도
있다 - 그림은 눈을 데려가는 쪽이고, 값을 대신하지 않는다.

- `DeltaBar` - 끝난 두 바퀴의 계정 합계를 한 막대에 (지난번까지 + 늘어난/줄어든 몫).
- `ShareStack` - 큰 파일들이 계정에서 차지하는 몫을 한 줄에 쌓는다.
- `CollectStrip` - 지난 24시간 수집을 한 시간 한 칸으로 (홈 표의 '최근 수집').
- `ChangeList` - 계정마다 지난 스캔 대비 증감을 가운데 0 선 양쪽 막대로 (홈).
- `ActionDelegate` - '무엇부터 할까' 한 줄을 알약 + 계정 + 값 + 설명 두 줄로.
- `SummaryBand` - 표 위의 요약 띠 (큰 값 + 그림 + 알약).

색은 그릴 때마다 `theme` 에서 읽는다 - 어두운 화면으로 바꾸면 따라 바뀐다.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PyQt5.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .. import tiers
from . import theme
from .overview_widgets import Chip
from .widgets import bar_color, paint_chip


def _font(painter: QPainter, size: int, bold: bool = False) -> QFont:
    font = QFont(painter.font())
    font.setPixelSize(size)
    font.setBold(bold)
    painter.setFont(font)
    return font


class DeltaBar(QWidget):
    """끝난 두 바퀴의 계정 합계를 한 막대에.

    지난번 합계까지는 회색, 늘어난 몫은 주의 색으로 이어 칠한다. 줄었으면 지금
    합계까지 회색, 사라진 몫은 좋은 색 테두리의 옅은 칸이다. `+2.1 TB` 가 계정
    30 TB 에서 얼마만큼인지가 글을 읽지 않고 보인다."""

    HEIGHT = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: Optional[Tuple[int, int]] = None
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("background: transparent;")

    def sizeHint(self) -> QSize:
        return QSize(320, self.HEIGHT)

    def set_values(self, previous_kb: Optional[int], current_kb: Optional[int]) -> None:
        self._values = (
            (int(previous_kb), int(current_kb))
            if previous_kb is not None and current_kb is not None else None
        )
        self.setVisible(self._values is not None)
        self.update()

    def values(self) -> Optional[Tuple[int, int]]:
        return self._values

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 규약
        if self._values is None:
            return
        previous, current = self._values
        full = max(previous, current, 1)
        width = float(self.width())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        radius = self.HEIGHT / 2
        painter.setBrush(QColor(theme.BORDER))
        painter.drawRoundedRect(QRectF(0, 0, width, self.HEIGHT), radius, radius)
        base = width * min(previous, current) / full
        if current > previous:
            painter.setBrush(QColor(theme.tier_color(tiers.WARN)))
            painter.drawRoundedRect(QRectF(0, 0, width, self.HEIGHT), radius, radius)
        elif current < previous:
            soft = QColor(theme.GOOD_TEXT)
            soft.setAlphaF(0.25)
            painter.setBrush(soft)
            painter.setPen(QPen(QColor(theme.GOOD_TEXT), 1, Qt.DashLine))
            painter.drawRoundedRect(QRectF(0.5, 0.5, width - 1, self.HEIGHT - 1), radius, radius)
            painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.TEXT_FAINT))
        painter.drawRoundedRect(QRectF(0, 0, max(base, self.HEIGHT), self.HEIGHT), radius, radius)
        painter.end()


class ShareStack(QWidget):
    """계정 하나를 한 줄로 놓고, 그 안에서 파일들이 차지하는 몫을 쌓는다.

    `(크기, 색 이름, 설명)` 들과 계정 합계. 파일 몫은 칸마다 1px 틈을 두고, 나머지는
    빈 칸(회색)이다. 칸 위에 마우스를 두면 그 파일의 설명이 뜬다."""

    HEIGHT = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parts: List[Tuple[int, str, str]] = []
        self._total = 0
        self.setMouseTracking(True)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("background: transparent;")

    def sizeHint(self) -> QSize:
        return QSize(320, self.HEIGHT)

    def set_parts(self, parts: Sequence[Tuple[int, str, str]], total_kb: Optional[int]) -> None:
        self._parts = [part for part in parts if part[0] > 0]
        self._total = max(int(total_kb or 0), sum(part[0] for part in self._parts))
        self.setVisible(bool(self._parts) and self._total > 0)
        self.update()

    def parts(self) -> List[Tuple[int, str, str]]:
        return list(self._parts)

    def _rects(self) -> List[QRectF]:
        if not self._total:
            return []
        rects, left = [], 0.0
        for size, _kind, _tip in self._parts:
            width = max(2.0, self.width() * size / self._total)
            rects.append(QRectF(left, 0, width, self.HEIGHT))
            left += width
        return rects

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 규약
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BORDER))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.HEIGHT), 4, 4)
        for rect, (_size, kind, _tip) in zip(self._rects(), self._parts):
            painter.setBrush(QColor(bar_color(kind)))
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, 0), 3, 3)
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        for rect, part in zip(self._rects(), self._parts):
            if rect.contains(event.pos()):
                QToolTip.showText(event.globalPos(), part[2], self)
                return
        QToolTip.hideText()


class CollectStrip(QWidget):
    """지난 24시간 수집 - 한 시간에 한 칸, 그 시간에 모인 표본만큼 차오른다.

    예전 칸은 '방금' 한 마디였고, 24시간 가운데 26% 만 모였을 때(cron 이 안 돌아
    창을 열 때만 수집) 그 '방금' 을 주황으로 칠했다 - 방금 모았다면서 경고 색이라
    무슨 뜻인지 읽히지 않았다. 빈 시간이 칸으로 보이면 '밤에는 안 모였다' 가 그냥
    보인다. 오른쪽 글자는 마지막 수집이 얼마 전인가이고, 오래됐을 때만 경고 색이다."""

    CELL = 2
    GAP = 1
    BAR = 14

    def __init__(self, hours: Sequence[float], text: str, alert: bool = False,
                 tooltip: str = "", parent=None):
        super().__init__(parent)
        self.hours = [max(0.0, min(1.0, value)) for value in hours]
        self.text = text
        self.alert = alert
        self.setToolTip(tooltip)
        self.setStyleSheet("background: transparent;")
        self.setMinimumWidth(self._strip_width() + 52)

    def _strip_width(self) -> int:
        return len(self.hours) * (self.CELL + self.GAP)

    def sizeHint(self) -> QSize:
        return QSize(self._strip_width() + 70, 26)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 규약
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        bottom = (self.height() + self.BAR) / 2
        left = 0.0
        for value in self.hours:
            if value > 0:
                height = max(3.0, self.BAR * value)
                painter.setBrush(QColor(theme.SCAN_DONE))
                painter.drawRoundedRect(QRectF(left, bottom - height, self.CELL, height), 1, 1)
            else:
                painter.setBrush(QColor(theme.BORDER_STRONG))
                painter.drawRect(QRectF(left, bottom - 2, self.CELL, 2))
            left += self.CELL + self.GAP
        _font(painter, theme.FONT_CAPTION + 1, bold=self.alert)
        painter.setPen(QColor(theme.tier_text(tiers.ALERT) if self.alert else theme.TEXT))
        painter.drawText(
            QRectF(left + 8, 0, max(0.0, self.width() - left - 8), self.height()),
            Qt.AlignLeft | Qt.AlignVCenter, self.text,
        )
        painter.end()


class ChangeList(QWidget):
    """계정마다 지난 스캔 대비 증감 - 가운데 0 선을 두고 늘면 오른쪽, 줄면 왼쪽.

    `(계정 id, 이름, 증감 KB, 설명)` 들. 가장 큰 변화가 반 폭을 다 쓴다. 줄을 누르면
    `chosen(계정 id)` - 홈은 그 계정의 용량 변화 탭을 연다."""

    chosen = pyqtSignal(str)
    ROW = 28
    NAME_WIDTH = 108
    VALUE_WIDTH = 78

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: List[Tuple[str, str, int, str]] = []
        self._hover = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("background: transparent;")

    def set_rows(self, rows: Sequence[Tuple[str, str, int, str]]) -> None:
        self._rows = list(rows)
        self.setFixedHeight(max(1, len(self._rows)) * self.ROW)
        self.update()

    def rows(self) -> List[Tuple[str, str, int, str]]:
        return list(self._rows)

    def sizeHint(self) -> QSize:
        return QSize(360, max(1, len(self._rows)) * self.ROW)

    def _row_at(self, y: float) -> int:
        index = int(y // self.ROW)
        return index if 0 <= index < len(self._rows) else -1

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 규약
        from .. import formatting

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        scale = max((abs(row[2]) for row in self._rows), default=0) or 1
        bar_left = self.NAME_WIDTH + 8
        bar_width = max(20.0, self.width() - bar_left - self.VALUE_WIDTH - 8)
        zero = bar_left + bar_width / 2
        for index, (_id, name, change, _tip) in enumerate(self._rows):
            top = index * self.ROW
            if index == self._hover:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(theme.SURFACE_SUNKEN))
                painter.drawRoundedRect(QRectF(0, top + 1, self.width(), self.ROW - 2), 6, 6)
            middle = top + self.ROW / 2
            _font(painter, theme.FONT_CAPTION + 1, bold=True)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(
                QRectF(4, top, self.NAME_WIDTH - 4, self.ROW), Qt.AlignLeft | Qt.AlignVCenter,
                painter.fontMetrics().elidedText(name, Qt.ElideRight, self.NAME_WIDTH - 4),
            )
            length = bar_width / 2 * min(1.0, abs(change) / scale)
            painter.setPen(Qt.NoPen)
            if change:
                painter.setBrush(QColor(theme.tier_color(tiers.WARN) if change > 0 else theme.GOOD_TEXT))
                left = zero if change > 0 else zero - length
                painter.drawRoundedRect(QRectF(left, middle - 5, max(2.0, length), 10), 3, 3)
            _font(painter, theme.FONT_CAPTION + 1, bold=True)
            painter.setPen(QColor(
                theme.tier_text(tiers.WARN) if change > 0
                else theme.GOOD_TEXT if change < 0 else theme.TEXT_MUTED
            ))
            painter.drawText(
                QRectF(self.width() - self.VALUE_WIDTH, top, self.VALUE_WIDTH - 4, self.ROW),
                Qt.AlignRight | Qt.AlignVCenter, formatting.format_kb_delta(change),
            )
        painter.setPen(QPen(QColor(theme.BORDER_STRONG), 1))
        if self._rows:
            painter.drawLine(int(zero), 2, int(zero), len(self._rows) * self.ROW - 2)
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        index = self._row_at(event.pos().y())
        if index != self._hover:
            self._hover = index
            self.update()
        if index >= 0 and self._rows[index][3]:
            QToolTip.showText(event.globalPos(), self._rows[index][3], self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        index = self._row_at(event.pos().y())
        if event.button() == Qt.LeftButton and index >= 0:
            self.chosen.emit(self._rows[index][0])
        super().mouseReleaseEvent(event)


class ActionDelegate(QStyledItemDelegate):
    """'무엇부터 할까' 한 줄 - 왼쪽 급함 띠, 종류 알약, 계정, 오른쪽 값, 아래 설명.

    예전에는 문장 한 줄씩이었다 ("pnr_shared 96% - 비울 수 있는 것을 아직 못
    찾았습니다"). 세 줄이 모두 같은 회색 문장이라 무엇이 급한지, 무엇 때문인지를
    읽어야 알았다. 칸의 `CARD_ROLE` 에 `action_guide.card` 의 dict 가 있으면 카드로, 없으면(안내 줄) 흐린 글로 접어 그린다. 칸의 글자
    (`text()`)는 예전 문장 그대로다 - 툴팁·시험·안내 창이 그 문장을 쓴다."""

    CARD_ROLE = Qt.UserRole + 20
    CARD_HEIGHT = 54

    def _note_height(self, text: str, width: int, option) -> int:
        font = QFont(option.font)
        font.setPixelSize(theme.FONT_CAPTION + 1)
        bound = QFontMetrics(font).boundingRect(
            0, 0, max(80, width - 24), 400, Qt.TextWordWrap, text
        )
        return bound.height() + 18

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt 규약
        view = self.parent()
        width = view.viewport().width() if view is not None else option.rect.width()
        if index.data(self.CARD_ROLE):
            return QSize(width, self.CARD_HEIGHT)
        return QSize(width, self._note_height(index.data(Qt.DisplayRole) or "", width, option))

    def paint(self, painter, option, index) -> None:
        card = index.data(self.CARD_ROLE)
        rect = QRectF(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if option.state & QStyle.State_MouseOver:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.SURFACE_SUNKEN))
            painter.drawRoundedRect(rect.adjusted(0, 2, 0, -2), 8, 8)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawLine(int(rect.left()), int(rect.bottom()), int(rect.right()), int(rect.bottom()))
        if not card:
            _font(painter, theme.FONT_CAPTION + 1)
            painter.setPen(QColor(theme.TEXT_MUTED))
            painter.drawText(rect.adjusted(12, 8, -12, -8), Qt.TextWordWrap | Qt.AlignVCenter,
                             index.data(Qt.DisplayRole) or "")
            painter.restore()
            return

        stripe = {0: theme.tier_color(tiers.EMERGENCY), 1: theme.tier_color(tiers.ALERT)}.get(
            card.get("level"), theme.ACCENT)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(stripe))
        painter.drawRoundedRect(QRectF(rect.left() + 2, rect.top() + 9, 4, rect.height() - 18), 2, 2)

        line1 = rect.top() + 17
        left = rect.left() + 14
        chip_text, chip_kind = card["chip"]
        left += paint_chip(painter, left, line1, chip_text, chip_kind) + 8
        metric_font = _font(painter, theme.FONT_SUBTITLE, bold=True)
        metric = card.get("metric") or ""
        metric_width = QFontMetrics(metric_font).horizontalAdvance(metric)
        if card.get("metric_good"):
            metric_color = theme.GOOD_TEXT
        elif card.get("metric_tier"):
            metric_color = theme.tier_text(card["metric_tier"])
        else:
            metric_color = theme.TEXT
        painter.setPen(QColor(metric_color))
        painter.drawText(QRectF(rect.right() - metric_width - 10, line1 - 12, metric_width + 4, 24),
                         Qt.AlignRight | Qt.AlignVCenter, metric)
        title_font = _font(painter, theme.FONT_BODY, bold=True)
        room = rect.right() - metric_width - 22 - left
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(QRectF(left, line1 - 12, max(0.0, room), 24), Qt.AlignLeft | Qt.AlignVCenter,
                         QFontMetrics(title_font).elidedText(card["title"], Qt.ElideRight, int(max(0, room))))
        detail_font = _font(painter, theme.FONT_CAPTION + 1)
        painter.setPen(QColor(theme.TEXT_MUTED))
        detail_room = rect.width() - 26
        painter.drawText(
            QRectF(rect.left() + 14, rect.top() + 28, detail_room, 20), Qt.AlignLeft | Qt.AlignVCenter,
            QFontMetrics(detail_font).elidedText(card.get("detail") or "", Qt.ElideRight, int(detail_room)),
        )
        painter.restore()


class SummaryBand(QFrame):
    """표 위의 요약 띠 - 왼쪽에 이름·큰 값·한 줄, 오른쪽에 그림 하나와 알약들.

    용량 변화·큰 파일·폴더 펼쳐 보기 표 위의 설명은 두세 줄 글이었다 ("proj_layout:
    261109 스캔 기준, 261108 스캔과 같은 경로끼리 비교 · 계정 전체 +2.1 TB (30.7 TB →
    32.8 TB) · 경로는 ..."). 답(계정이 얼마나 변했나, 큰 파일이 얼마나 차지하나)을
    큰 값과 막대로 세우고, 경고는 알약으로 접고 자세한 말은 알약 툴팁에 둔다."""

    def __init__(self, graphic: Optional[QWidget] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        # 자기 내용 높이만 - 아래 표가 짧으면 남는 세로를 띠가 받아 부풀었다.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 12, 18, 12)
        row.setSpacing(22)
        left = QVBoxLayout()
        left.setSpacing(2)
        self.caption = QLabel()
        self.caption.setObjectName("statLabel")
        left.addWidget(self.caption)
        self.value = QLabel("-")
        left.addWidget(self.value)
        self.sub = QLabel()
        self.sub.setObjectName("caption")
        left.addWidget(self.sub)
        row.addLayout(left)
        right = QVBoxLayout()
        right.setSpacing(10)
        right.addStretch(1)
        self.graphic = graphic
        if graphic is not None:
            right.addWidget(graphic)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        right.addLayout(self.chips)
        right.addStretch(1)
        row.addLayout(right, 1)
        self.set_value("-")

    def set_value(self, text: str, color: str = "") -> None:
        self.value.setText(text)
        self.value.setStyleSheet(
            f"font-size: 26px; font-weight: {theme.WEIGHT_HEAVY};"
            + (f" color: {color};" if color else "")
        )

    def set_chips(self, chips: Sequence[Tuple[str, str, str]]) -> None:
        """`[(글, 알약 종류, 툴팁), ...]` - 지난 것은 지운다."""

        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for text, kind, tip in chips:
            chip = Chip(text, kind)
            chip.setToolTip(tip)
            self.chips.addWidget(chip)
        self.chips.addStretch(1)

    def chip_texts(self) -> List[str]:
        return [
            self.chips.itemAt(index).widget().text()
            for index in range(self.chips.count())
            if self.chips.itemAt(index).widget() is not None
        ]
