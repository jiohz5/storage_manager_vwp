"""등급 배지 등 작은 재사용 위젯.

색상 + 텍스트 라벨을 항상 함께 쓴다 (DESIGN.md 2부 6절 "1차 채택" —
색상만으로 등급을 구분하지 않는다).
"""

from __future__ import annotations

import re
from typing import Optional

from PyQt5.QtCore import QEasingCurve, QEvent, QRectF, QSize, Qt, QVariantAnimation
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
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


class ChangeBarDelegate(QStyledItemDelegate):
    """증감 칸 뒤에 그 크기만큼의 옅은 막대를 깐다.

    숫자만으로는 `+3.2 TB` 와 `+307.2 GB` 가 한 줄 차이로 보인다 - 열 배인데.
    막대를 깔면 목록을 읽지 않고도 어디가 크게 변했는지 보인다. 늘어난 것은 주의
    색, 줄어든 것은 좋은 색의 옅은 쪽이고 글자는 그 위에 그대로 읽힌다.

    값은 칸의 `VALUE_ROLE` (부호 있는 KB). 가장 큰 변화가 칸을 다 채운다
    (`scale` - 표를 채울 때 정한다). 막대를 칸 위젯으로 얹지 않는 것은 Qt 정렬을
    켜 두기 위해서다 - 칸 위젯은 정렬해도 행을 따라가지 않는다."""

    VALUE_ROLE = Qt.UserRole + 7
    INSET = 6
    ALPHA = 0.22

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scale = 0

    def paint(self, painter, option, index) -> None:
        value = index.data(self.VALUE_ROLE)
        if value and self.scale > 0 and not (option.state & QStyle.State_Selected):
            rect = option.rect.adjusted(self.INSET, 7, -self.INSET, -7)
            width = max(2.0, rect.width() * min(1.0, abs(value) / self.scale))
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            # 옅은 면 색(`WARN_SOFT`)은 어두운 화면에서 바탕과 거의 같아 막대가 안 보였다.
            # 등급 색·좋은 색을 비쳐 보이게 깔면 두 화면 모두에서 읽힌다.
            color = QColor(theme.tier_color(tiers.WARN) if value > 0 else theme.GOOD_TEXT)
            color.setAlphaF(self.ALPHA)
            painter.setBrush(color)
            painter.drawRoundedRect(
                QRectF(rect.right() - width, rect.top(), width, rect.height()), 3, 3
            )
            painter.restore()
        super().paint(painter, option, index)


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
