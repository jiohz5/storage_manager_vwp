"""등급 배지 등 작은 재사용 위젯.

색상 + 텍스트 라벨을 항상 함께 쓴다 (DESIGN.md 2부 6절 "1차 채택" —
색상만으로 등급을 구분하지 않는다).
"""

from __future__ import annotations

import re
from typing import Optional

from PyQt5.QtCore import QEasingCurve, QEvent, QRectF, QSize, Qt, QVariantAnimation
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import i18n, tiers
from ..formatting import tier_badge_text
from . import theme


class CountingLabel(QLabel):
    """바뀌면 잠깐 세어 가는 큰 숫자 (홈 타일의 `96.0%`, `3`, `67%`).

    꾸밈이 아니라 **방금 새 값을 읽었다** 는 신호다. 새로고침을 눌러도 숫자가
    그대로면 눌린 건지 알 수 없다. 처음 읽을 때는 0 에서, 그 뒤로는 지난 값에서 센다.

    - 숫자가 아닌 값(`11/11(수) 새벽`, `-`)은 그 자리에서 바뀐다.
    - 숨은 동안에는 세지 않는다 (아무도 안 보는 그림을 그릴 까닭이 없다).
    - `text()` 는 **늘 마지막에 받은 값**이다. 세는 도중의 글자는 그림일 뿐 - 화면
      코드와 시험이 그 값을 읽는다 (`ElidedLabel` 과 같은 약속).
    - 세는 동안은 마지막 값의 폭을 잡아 둔다. 글자 폭이 바뀌면 옆 알약이 흔들린다.
    """

    DURATION_MS = 450
    # 앞뒤 글 없이 숫자 하나와 단위 (`96.0%`, `3`, `67%`).
    _NUMBER = re.compile(r"^(\d+(?:\.(\d+))?)(\D*)$")

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._target = text
        self._shape = (0, "")   # (소수 자리, 단위) - 세는 동안 쓸 모양
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(self.DURATION_MS)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._show_step)
        self._animation.finished.connect(self._settle)

    def text(self) -> str:  # noqa: D401 - QLabel 과 같은 이름
        return self._target

    def setText(self, text: str) -> None:  # noqa: N802 - Qt 규약
        previous, self._target = self._target, text
        self._animation.stop()
        new = self._NUMBER.match(text)
        if new is None or not self.isVisible():
            self._settle()
            return
        old = self._NUMBER.match(previous)
        start = float(old.group(1)) if old and old.group(3) == new.group(3) else 0.0
        end = float(new.group(1))
        if start == end:
            self._settle()
            return
        self._shape = (len(new.group(2) or ""), new.group(3))
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance(text))
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.start()

    def _show_step(self, value) -> None:
        decimals, unit = self._shape
        super().setText(f"{float(value):.{decimals}f}{unit}")

    def _settle(self) -> None:
        super().setText(self._target)
        self.setMinimumWidth(0)


class ElidedLabel(QLabel):
    """한 줄 글 - 폭이 모자라면 끝을 '…' 로 줄이고 전체는 툴팁으로 보인다.

    머리줄처럼 옆 단추들과 폭을 나눠 쓰는 자리에서 긴 경고 문장이 단추를 밀어내거나
    중간에서 잘려 뜻이 끊기지 않게 한다. `text()` 는 줄이기 전 전체를 돌려준다."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        # 폭은 남는 만큼만 쓴다 - 글 길이가 머리줄 폭을 정하지 않게.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt 규약
        self._full = text or ""
        self._elide()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(0, self.width()))
        QLabel.setText(self, shown)
        self.setToolTip(self._full if shown != self._full else "")


class TierBadge(QLabel):
    """등급을 배경색 + 텍스트로 함께 보여주는 알약 모양 라벨."""

    def __init__(self, tier: str = tiers.UNKNOWN, pct: Optional[float] = None, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_tier(tier, pct)

    def set_tier(self, tier: str, pct: Optional[float]) -> None:
        self.setText(tier_badge_text(tier, pct))
        background, foreground = theme.tier_chip(tier)
        self.setStyleSheet(
            f"background-color: {background}; color: {foreground}; border-radius: 9px; "
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


def read_only_table(columns=0, headers=None, row_height: int = 30,
                    stretch_column: int = 0) -> QTableWidget:
    """읽기 전용 표 하나. **이 프로그램의 표는 전부 이것으로 만든다.**

    ## 왜 한 곳인가

    표 설정이 여덟 군데에 복사돼 있었고, 복사본마다 조금씩 달랐다. 경로를
    가운데에서 줄이는 설정을 상세 스캔 탭에서 고쳤는데(오른쪽을 자르면
    `run_03/BACKUP` 같은 정작 다른 뒤쪽이 사라진다), 스캔 진행·검색 결과처럼
    **똑같이 경로를 보여 주는 표**는 그 고침을 받지 못하고 여전히 뒤를 잘랐다.

    - 경로는 **가운데를** 줄인다.
    - 줄바꿈을 끈다. 켜 두면 줄임 대신 행 안에서 접히려다 잘린다.
    - 고칠 수 없다 (이 도구는 보기만 한다), 행 단위로 고른다, 격자는 없다.
    - `stretch_column` 이 남는 폭을 가져가고 나머지는 내용대로 잡는다. None 이면
      폭 정책은 부르는 쪽이 정한다.

    `columns` 는 열 수, `headers` 는 머리글 목록 (주면 열 수도 거기서 정한다).
    """

    if headers is not None:
        columns = len(headers)
    table = QTableWidget(0, columns)
    if headers is not None:
        table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.ElideMiddle)
    header = table.horizontalHeader()
    header.setHighlightSections(False)
    if stretch_column is not None and columns:
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(stretch_column, QHeaderView.Stretch)
    return table


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


def clear_layout(layout) -> None:
    """틀 안의 위젯을 모두 걷는다 - **곧바로 떼어 내고** 나중에 지운다.

    `deleteLater()` 만 부르면 이벤트 루프가 그 지우기를 처리할 때까지 옛 위젯이 창의
    자식으로 남는다. 리눅스(오프스크린)의 시험에서는 `processEvents()` 가 그 지우기를
    처리하지 않아, 어두운 화면으로 바꾼 뒤에도 옛 알약(밝은 색)이 창 안에 남아 있었다
    (CI 가 잡았다). 떼어 내면 그 순간부터 창의 일부가 아니다."""

    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def bar_color(kind: str) -> str:
    """칸 막대·알약 옆 막대의 색 이름 -> 지금 화면의 색. 그릴 때마다 묻는다 (어두운
    화면으로 바꾸면 따라 바뀌어야 한다)."""

    if kind == "accent":
        return theme.ACCENT
    if kind == "good":
        return theme.GOOD_TEXT
    if kind == "neutral":
        return theme.TEXT_FAINT
    if kind == "attention":
        return theme.tier_color(tiers.ALERT)
    return theme.tier_color(kind)


def paint_chip(painter, left: float, middle: float, text: str, kind: str,
               height: float = 20) -> float:
    """`left` 에서 시작하는 알약 하나를 그리고 그 폭을 돌려준다 (`Chip` 과 같은 색).
    줄이 낮은 표(폴더 펼쳐 보기)는 `height` 를 줄여 준다 - 알약이 줄 밖으로 넘친다."""

    background, foreground = theme.CHIPS.get(kind, theme.CHIPS["soft"])
    font = painter.font()
    font.setPixelSize(theme.FONT_CAPTION if height >= 18 else theme.FONT_CAPTION - 1)
    font.setBold(True)
    painter.setFont(font)
    width = painter.fontMetrics().horizontalAdvance(text) + (18 if height >= 18 else 12)
    rect = QRectF(left, middle - height / 2, width, height)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(background))
    painter.drawRoundedRect(rect, height / 2, height / 2)
    painter.setPen(QColor(foreground))
    painter.drawText(rect, Qt.AlignCenter, text)
    return width


class CellDelegate(QStyledItemDelegate):
    """표 칸 하나를 글자와 그림으로 - 증감 막대, 비중 막대, 알약, 두 색 경로.

    용량을 보는 표(용량 변화·큰 파일·폴더 펼쳐 보기)가 흰 바탕에 글자만 가득했다.
    `+3.2 TB` 와 `+307.2 GB` 는 한 줄 차이로 보이지만 열 배이고, `5.7%` 와 `0.3%`
    도 글자로는 비슷하다. 같은 뜻을 막대 길이·알약으로 보이면 읽지 않고도 보인다.
    칸 위젯으로 얹지 않는 것은 Qt 정렬을 켜 두기 위해서다 - 칸 위젯은 정렬해도
    행을 따라가지 않는다.

    칸의 역할(role)로 무엇을 그릴지 정한다:

    - `VALUE_ROLE` - 부호 있는 증감(KB). 칸 뒤에 옅은 막대를 깐다 - 늘면 주의 색,
      줄면 회청 (`theme.CHANGE_*`). 가장 큰 변화가 칸을 다 채운다 (`scale`, 표를 채울 때 정한다).
    - `BAR_ROLE` - `(비율 0~1, 색 이름)`. 글자 왼쪽에 가는 막대 (비중·크기).
    - `CHIP_ROLE` - `(글, 알약 종류)`. 글자 앞에 알약 (새 폴더, 없어짐). 알약 글이 칸의
      글과 같으면 알약만 그린다 (`지난번 못 잼`, `목록에 없던 파일`).
    - `PATH_ROLE` - 참이면 경로의 마지막 조각만 굵게, 앞의 폴더는 흐리게.
    - `SPARK_ROLE` - 값 목록 (오래된 것부터). 칸 안에 작은 추세선, 끝점에 점.

    글자색은 칸의 전경색을 따른다 (`setForeground`) - 시험과 다른 화면이 그 값을 읽는다."""

    VALUE_ROLE = Qt.UserRole + 7
    BAR_ROLE = Qt.UserRole + 8
    CHIP_ROLE = Qt.UserRole + 9
    PATH_ROLE = Qt.UserRole + 10
    SPARK_ROLE = Qt.UserRole + 11   # [값, ...] 오래된 것부터 - 칸 안의 작은 추세선
    INSET = 6
    ALPHA = 0.22
    PAD = 8
    BAR_HEIGHT = 6
    BAR_MIN = 48

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scale = 0

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt 규약
        size = super().sizeHint(option, index)
        extra = 0
        chip = index.data(self.CHIP_ROLE)
        if chip:
            font = option.font
            font.setPixelSize(theme.FONT_CAPTION)
            font.setBold(True)
            extra += QFontMetrics(font).horizontalAdvance(chip[0]) + 26
        if index.data(self.BAR_ROLE):
            extra += self.BAR_MIN + 10
        return QSize(size.width() + extra, size.height())

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        selected = bool(opt.state & QStyle.State_Selected)
        # 바탕·선택·초점은 스타일이 그대로 그린다 (QSS 의 선택색). 글자만 비워 둔다.
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        value = index.data(self.VALUE_ROLE)
        if value and self.scale > 0 and not selected:
            back = opt.rect.adjusted(self.INSET, 7, -self.INSET, -7)
            width = max(2.0, back.width() * min(1.0, abs(value) / self.scale))
            # 옅은 면 색은 어두운 화면에서 바탕과 거의 같아 막대가 안 보였다. 변화 색을
            # 비쳐 보이게 깔면 두 화면 모두에서 읽힌다 (등급 색은 쓰지 않는다 - 위험이 아니다).
            color = QColor(theme.CHANGE_UP_FILL if value > 0 else theme.CHANGE_DOWN_FILL)
            color.setAlphaF(self.ALPHA)
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(
                QRectF(back.right() - width, back.top(), width, back.height()), 3, 3
            )

        rect = QRectF(opt.rect.adjusted(self.PAD, 0, -self.PAD, 0))
        middle = rect.center().y()
        spark = index.data(self.SPARK_ROLE)
        if spark and len(spark) >= 2:
            self._paint_spark(painter, rect.adjusted(0, 8, 0, -8), spark)
        alignment = index.data(Qt.TextAlignmentRole)
        right = bool(int(alignment or 0) & Qt.AlignRight)
        chip = index.data(self.CHIP_ROLE)
        if chip and chip[0] == text:
            # 알약이 칸의 말 전부면 알약만 그린다 (`지난번 못 잼`, `목록에 없던 파일`).
            text = ""
        if chip:
            chip_text, kind = chip
            height = max(14.0, min(20.0, opt.rect.height() - 4.0))
            if right and not text:
                font = painter.font()
                font.setPixelSize(theme.FONT_CAPTION if height >= 18 else theme.FONT_CAPTION - 1)
                font.setBold(True)
                width = QFontMetrics(font).horizontalAdvance(chip_text) + (18 if height >= 18 else 12)
                paint_chip(painter, rect.right() - width, middle, chip_text, kind, height)
            else:
                width = paint_chip(painter, rect.left(), middle, chip_text, kind, height)
                rect.setLeft(rect.left() + width + 8)

        painter.setFont(opt.font)
        brush = index.data(Qt.ForegroundRole)
        ink = brush.color() if hasattr(brush, "color") else QColor(theme.TEXT)
        metrics = painter.fontMetrics()
        bar = index.data(self.BAR_ROLE)
        if bar and text:
            fraction, kind = bar
            text_width = metrics.horizontalAdvance(text)
            track = QRectF(rect.left(), middle - self.BAR_HEIGHT / 2,
                           max(0.0, rect.width() - text_width - 10), self.BAR_HEIGHT)
            if track.width() >= 12:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(theme.BORDER))
                painter.drawRoundedRect(track, 3, 3)
                filled = track.width() * max(0.0, min(1.0, fraction or 0.0))
                if filled > 0:
                    painter.setBrush(QColor(bar_color(kind)))
                    painter.drawRoundedRect(
                        QRectF(track.left(), track.top(), max(filled, self.BAR_HEIGHT),
                               track.height()), 3, 3)
            rect.setLeft(rect.right() - text_width)
            right = True

        if text and index.data(self.PATH_ROLE) and "/" in text:
            folder, name = text.rsplit("/", 1)
            folder += "/"
            bold = QFont(opt.font)
            bold.setBold(True)
            name_width = QFontMetrics(bold).horizontalAdvance(name)
            room = max(0.0, rect.width() - name_width)
            shown = metrics.elidedText(folder, Qt.ElideMiddle, int(room))
            painter.setPen(QColor(theme.TEXT_MUTED) if ink == QColor(theme.TEXT) else ink)
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, shown)
            painter.setFont(bold)
            painter.setPen(ink)
            left = rect.left() + metrics.horizontalAdvance(shown)
            painter.drawText(
                QRectF(left, rect.top(), rect.right() - left, rect.height()),
                Qt.AlignLeft | Qt.AlignVCenter,
                QFontMetrics(bold).elidedText(name, Qt.ElideMiddle, int(rect.right() - left)),
            )
        elif text:
            painter.setPen(ink)
            painter.drawText(
                rect, (Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignVCenter,
                metrics.elidedText(text, opt.textElideMode, int(rect.width())),
            )
        painter.restore()


# 추세선의 세로 폭은 그 폴더의 최저~최고지만, 최고값의 이만큼보다 좁히지 않는다 -
# 5.0 -> 4.9 -> 5.0 TB 가 칸을 꽉 채운 출렁임으로 보이면 '평평하다' 를 못 읽는다.
SPARK_MIN_SPAN = 0.10


def _spark_points(rect, values):
    low, high = min(values), max(values)
    real = high - low
    span = max(real, abs(high) * SPARK_MIN_SPAN) or 1
    low -= (span - real) / 2
    step = rect.width() / (len(values) - 1)
    return [
        (rect.left() + i * step, rect.bottom() - (value - low) / span * rect.height())
        for i, value in enumerate(values)
    ]


def _paint_spark(self, painter, rect, values) -> None:
    """칸 안의 작은 추세선 - 축 없이 모양만 (오르나, 평평한가, 꺾였나).

    세로는 그 폴더 자신의 최저~최고라, 작은 폴더의 출렁임도 모양으로 보인다. 크기는
    옆 칸의 숫자와 막대가 말한다."""

    from PyQt5.QtCore import QPointF
    from PyQt5.QtGui import QPainterPath, QPen

    points = _spark_points(rect, values)
    path = QPainterPath(QPointF(*points[0]))
    for x, y in points[1:]:
        path.lineTo(x, y)
    color = QColor(theme.ACCENT)
    painter.setPen(QPen(color, 1.6))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(path)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    x, y = points[-1]
    painter.drawEllipse(QRectF(x - 2.5, y - 2.5, 5, 5))


CellDelegate._paint_spark = _paint_spark


class ChangeBarDelegate(CellDelegate):
    """증감 칸 - `CellDelegate` 의 증감 막대를 쓰는 칸 (예전 이름을 그대로 둔다)."""


class UsageBar(QWidget):
    """사용률을 숫자 + 막대로 함께 보여준다.

    숫자만 있으면 여러 계정을 훑을 때 서로 비교가 안 된다. 막대를 옆에 두면
    어느 쪽이 더 찼는지 읽지 않고도 보인다. 색은 등급 색을 그대로 쓰므로
    표의 다른 등급 표시와 어긋나지 않는다."""

    BAR_HEIGHT = 7
    TEXT_WIDTH = 52
    PILL_WIDTH = 46
    # 알약을 붙이는 등급 - 정상에는 붙이지 않는다 (색은 눈이 가야 할 곳에만).
    PILL_TIERS = {
        tiers.WARN: "tier_warn", tiers.ALERT: "tier_alert",
        tiers.EMERGENCY: "tier_emergency", tiers.FULL: "tier_full",
    }

    def __init__(self, pct: Optional[float], tier: str, parent=None, show_tier: bool = False):
        super().__init__(parent)
        self._pct = pct
        self._tier = tier
        # 홈 표는 막대 오른쪽에 등급 글자를 알약으로 붙인다 - 색만으로는 경계값이
        # 어느 쪽인지 말해 주지 못한다.
        self._pill = self.PILL_TIERS.get(tier) if show_tier else None
        self.setMinimumWidth(self.TEXT_WIDTH + 60 + (self.PILL_WIDTH if self._pill else 0))
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
        reserve = self.PILL_WIDTH + 6 if self._pill else 0
        track_width = max(0, self.width() - track_left - 6 - reserve)
        if self._pill:
            background, foreground = theme.CHIPS[self._pill]
            pill = QRectF(self.width() - self.PILL_WIDTH - 2, (self.height() - 18) / 2,
                          self.PILL_WIDTH, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(background))
            painter.drawRoundedRect(pill, 9, 9)
            painter.setPen(QColor(foreground))
            small = painter.font()
            small.setPixelSize(theme.FONT_CAPTION - 1)
            painter.setFont(small)
            painter.drawText(pill, Qt.AlignCenter, tiers.label(self._tier))
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
            painter.setBrush(QColor(theme.tier_color(self._tier)))
            painter.drawRoundedRect(
                QRectF(track_left, top, filled, self.BAR_HEIGHT), radius, radius
            )
        painter.end()


class StatCard(QFrame):
    """숫자 하나를 크게 세우는 작은 카드.

    표를 읽고 스스로 요약을 만들게 하는 대신 **답을 먼저 보여 주기** 위한
    것이다. 한 카드에 값 하나만 둔다 - 둘을 넣으면 어느 쪽이 답인지 흐려진다.

    값이 없으면 '-' 를 세운다. 0 을 세우면 "없다"가 되는데 "아직 모른다"와는
    다른 이야기다.
    """

    def __init__(self, label: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 10, 14, 10)
        box.setSpacing(1)

        self.label = QLabel(label)
        self.label.setObjectName("statLabel")
        box.addWidget(self.label)

        self.value = QLabel("-")
        self.value.setObjectName("statValue")
        box.addWidget(self.value)

        self.detail = QLabel("")
        self.detail.setObjectName("caption")
        self.detail.setWordWrap(True)
        box.addWidget(self.detail)

    def set_label(self, text: str) -> None:
        self.label.setText(text)

    def show_value(self, value: str, detail: str = "", color: Optional[str] = None):
        """값과 그 밑의 한 줄을 세운다. `color` 를 주면 값만 물들인다.

        설명까지 같이 물들이면 카드 전체가 경고처럼 보여서, 정작 급한 카드와
        구분이 안 된다."""

        self.value.setText(value if value else "-")
        self.detail.setText(detail)
        self.value.setStyleSheet(f"color: {color};" if color else "")
