"""상세 스캔 현황을 그리는 작은 위젯들 - 알약, 칸 막대, 밤 시간 막대.

숫자는 `smvwp.scan_overview` 가 내고, 여기는 그리기만 한다. 색은 `theme` 의
SCAN_* / NIGHT_SLOTS / CHIPS 를 쓴다 - 등급 색과 섞이지 않는 파랑·회색 계열이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from PyQt5.QtCore import QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QLabel, QSizePolicy, QToolTip, QWidget

from . import theme


class Chip(QLabel):
    """상태 알약 - 글자와 색을 함께 쓴다 (색만으로 상태를 말하지 않는다)."""

    def __init__(self, text: str = "", kind: str = "soft", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        background, foreground = theme.CHIPS.get(kind, theme.CHIPS["soft"])
        border = f"1px dashed {theme.TEXT_FAINT}" if kind == "unseen" else f"1px solid {background}"
        self.setStyleSheet(
            f"background: {background}; color: {foreground}; border: {border}; "
            f"border-radius: 10px; padding: 2px 9px; font-weight: bold; "
            f"font-size: {theme.FONT_CAPTION}px;"
        )


class SegmentBar(QWidget):
    """칸 여럿을 한 줄에 - 칸마다 폭(무게)과 채운 비율.

    '모든 계정 한 바퀴' 는 계정마다 한 칸이고 폭은 디렉터리 수다. 하는 중인 계정은
    잰 만큼만 채운다. 칸 위에 마우스를 두면 그 칸의 설명(계정 이름 등)이 뜬다."""

    GAP = 3

    def __init__(self, height: int = 26, parent=None):
        super().__init__(parent)
        self._height = height
        self._segments: List[Tuple[float, float, str, str, str]] = []
        self.setMouseTracking(True)
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet("background: transparent;")

    def sizeHint(self) -> QSize:
        return QSize(400, self._height)

    def set_segments(self, segments: Sequence[Tuple[float, float, str, str, str]]) -> None:
        """`(무게, 채운 비율 0~1, 채운 색, 빈 칸 색, 설명)` 들."""

        self._segments = [segment for segment in segments if segment[0] > 0]
        self.update()

    def segments(self) -> List[Tuple[float, float, str, str, str]]:
        return list(self._segments)

    def _rects(self) -> List[QRectF]:
        total = sum(segment[0] for segment in self._segments)
        if not total:
            return []
        usable = max(0.0, self.width() - self.GAP * (len(self._segments) - 1))
        rects, left = [], 0.0
        for weight, *_rest in self._segments:
            width = max(3.0, usable * weight / total)
            rects.append(QRectF(left, 0, width, self._height))
            left += width + self.GAP
        return rects

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 규약
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        for rect, (_weight, fraction, color, track, _tip) in zip(self._rects(), self._segments):
            painter.setBrush(QColor(track))
            painter.drawRoundedRect(rect, 4, 4)
            if fraction > 0:
                filled = QRectF(rect.left(), rect.top(), rect.width() * min(1.0, fraction), rect.height())
                painter.setBrush(QColor(color))
                painter.drawRoundedRect(filled, 4, 4)
        painter.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        for rect, segment in zip(self._rects(), self._segments):
            if rect.contains(event.pos()):
                QToolTip.showText(event.globalPos(), segment[4], self)
                return
        QToolTip.hideText()


class TimelineBar(QWidget):
    """한 밤(예: 22시~06시) 위에 구간들을 칠한다. 아래에 두 시간마다 눈금 글자.

    밤 일정(계정마다 언제부터 언제까지)과 지난 밤들(실행이 언제부터 언제까지
    돌았나)이 같은 축을 쓴다 - 둘을 나란히 놓고 견줄 수 있게."""

    def __init__(self, bar_height: int = 18, show_axis: bool = True, parent=None):
        super().__init__(parent)
        self._bar_height = bar_height
        self._show_axis = show_axis
        self._axis: Optional[Tuple[datetime, datetime]] = None
        self._spans: List[Tuple[datetime, datetime, str]] = []
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(bar_height + (16 if show_axis else 0))
        self.setStyleSheet("background: transparent;")

    def sizeHint(self) -> QSize:
        return QSize(260, self.height())

    def set_timeline(self, axis_start: datetime, axis_end: datetime,
                     spans: Sequence[Tuple[datetime, datetime, str]]) -> None:
        self._axis = (axis_start, axis_end)
        self._spans = list(spans)
        self.update()

    def spans(self) -> List[Tuple[datetime, datetime, str]]:
        return list(self._spans)

    def _x(self, moment: datetime) -> float:
        start, end = self._axis
        total = (end - start).total_seconds() or 1.0
        ratio = (moment - start).total_seconds() / total
        return max(0.0, min(1.0, ratio)) * self.width()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 규약
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BORDER))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self._bar_height), 5, 5)
        if self._axis is not None:
            for start, end, color in self._spans:
                left, right = self._x(start), self._x(end)
                if right - left < 1:
                    continue
                painter.setBrush(QColor(color))
                painter.drawRoundedRect(QRectF(left, 0, right - left - 1.5, self._bar_height), 4, 4)
            if self._show_axis:
                self._paint_axis(painter)
        painter.end()

    def _paint_axis(self, painter: QPainter) -> None:
        start, end = self._axis
        painter.setPen(QPen(QColor(theme.TEXT_MUTED)))
        font = painter.font()
        font.setPixelSize(11)
        painter.setFont(font)
        tick = start.replace(minute=0, second=0, microsecond=0)
        if tick < start:
            tick += timedelta(hours=1)
        if tick.hour % 2:
            tick += timedelta(hours=1)
        top = self._bar_height + 2
        while tick <= end:
            x = self._x(tick)
            label = f"{tick.hour:02d}"
            width = 20
            left = min(max(0.0, x - width / 2), self.width() - width)
            painter.drawText(QRectF(left, top, width, 14), Qt.AlignCenter, label)
            tick += timedelta(hours=2)


class MiniBar(QWidget):
    """칸 하나짜리 진행 막대 + 오른쪽 글자 (표 칸에 넣는다)."""

    TEXT_WIDTH = 70

    def __init__(self, fraction: float, color: str, text: str, parent=None):
        super().__init__(parent)
        self.fraction = max(0.0, min(1.0, fraction))
        self._color = color
        self.text = text
        self.setMinimumWidth(self.TEXT_WIDTH + 80)
        self.setStyleSheet("background: transparent;")

    def sizeHint(self) -> QSize:
        return QSize(self.TEXT_WIDTH + 140, 26)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 규약
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track_width = max(0, self.width() - self.TEXT_WIDTH - 10)
        top = (self.height() - 8) / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BORDER))
        painter.drawRoundedRect(QRectF(0, top, track_width, 8), 4, 4)
        if self.fraction > 0:
            painter.setBrush(QColor(self._color))
            painter.drawRoundedRect(QRectF(0, top, max(8.0, track_width * self.fraction), 8), 4, 4)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRectF(track_width + 8, 0, self.TEXT_WIDTH, self.height()),
            Qt.AlignRight | Qt.AlignVCenter, self.text,
        )
        painter.end()


class Swatch(QWidget):
    """범례의 작은 색 네모. `color` 는 색이나, 그릴 때 색을 돌려주는 함수 -
    범례는 한 번 만들고 오래 남으므로, 화면 색을 바꿨을 때 따라 바뀌려면 함수를 준다."""

    def __init__(self, color, size: int = 10, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(size, size)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 규약
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._color() if callable(self._color) else self._color))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 2, 2)
        painter.end()
