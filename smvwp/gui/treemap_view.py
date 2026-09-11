"""트리맵을 그리는 위젯.

## 왜 색을 이렇게 쓰는가

형제끼리 **다른 색조**를 준다. 한 가지 색의 명암만 쓰면 경계가 붙어 있는
조각들이 한 덩어리로 보여서, 정작 알고 싶은 "이 안에 몇 개가 들어 있나"가
안 보인다. 자식은 부모의 색조를 물려받고 밝기만 올린다 - 그래야 어느 덩어리에
속한 것인지가 유지된다.

색조는 테마의 강조색에서 시작해 일정 간격으로 돌린다. 팔레트를 박아 두면
테마를 바꿨을 때 혼자 어긋난다.

'이 폴더에 직접 있는 파일'과 '그 밖 폴더'는 회색이다. 계산으로 만든 줄이지
진짜 폴더가 아니므로, 색이 같으면 눌러서 들어가려 한다.

## 눈에 띄는 것은 테두리로 표시한다

칠을 바꾸지 않는 것이 중요하다. 칠은 **어느 덩어리에 속하는가**를 나르고
있어서, 그것을 경고색으로 덮으면 소속이 사라진다.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QWidget

from .. import formatting, i18n, tree_view, treemap
from . import theme

# 형제끼리 색조를 이만큼씩 벌린다. 너무 좁으면 옆 조각과 구분이 안 되고,
# 너무 넓으면 무지개가 되어 그림이 시끄러워진다.
HUE_STEP = 47

# 이 색조 범위는 덩어리 색으로 쓰지 않는다.
#
# 경고 테두리가 빨강이라, 덩어리까지 빨강이면 "이 조각이 경고인가 그냥 저
# 덩어리인가"를 가릴 수 없다. 실제로 네 번째 덩어리가 붉게 나와 헷갈렸다.
RESERVED_HUE_LOW = 335
RESERVED_HUE_HIGH = 25

# 조각 안에 이름을 쓸 최소 크기. 이보다 작으면 글자가 잘려 나와 오히려
# 읽기를 방해한다.
LABEL_MIN_WIDTH = 46.0
LABEL_MIN_HEIGHT = 26.0

# 얼마나 깊이까지 그릴지. 더 깊이 그려도 조각이 작아 안 읽히고, 그 대신
# 눌러서 들어가면 된다.
DRAW_DEPTH = 2


def _palette(base: int) -> list:
    """덩어리에 쓸 색조들 - 경고색과 겹치는 범위는 뺀다.

    강조색에서 출발해 일정 간격으로 돌린다. 팔레트를 박아 두면 테마를 바꿨을
    때 혼자 어긋난다."""

    candidates = [(base + index * HUE_STEP) % 360 for index in range(24)]
    allowed = [
        hue for hue in candidates
        if not (hue >= RESERVED_HUE_LOW or hue <= RESERVED_HUE_HIGH)
    ]
    return allowed or [base]


class TreemapView(QWidget):
    """용량을 넓이로 그리는 화면. 누르면 그 안으로 들어간다."""

    # 안으로 들어갔거나 위로 나왔을 때. 위쪽 경로 표시를 갱신하라는 뜻이다.
    zoomed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(240)
        self._roots: List = []
        # 어디까지 들어왔는지. 마지막이 지금 보고 있는 것이고, 비어 있으면
        # 처음 받은 뿌리를 본다.
        self._stack: List = []
        self._tiles: List[treemap.Tile] = []
        self._hover: Optional[treemap.Tile] = None
        self._layout_size = None

    # -- 데이터 -------------------------------------------------------
    def set_roots(self, roots) -> None:
        self._roots = list(roots or [])
        self._stack = []
        self._invalidate()

    def current_nodes(self) -> List:
        if self._stack:
            return list(self._stack[-1].children or [])
        return self._roots

    def path_labels(self) -> List[str]:
        """지금까지 들어온 경로 - 위쪽에 적을 것."""

        return [node.label for node in self._stack]

    def can_go_up(self) -> bool:
        return bool(self._stack)

    def go_up(self) -> None:
        if self._stack:
            self._stack.pop()
            self._invalidate()
            self.zoomed.emit()

    def go_home(self) -> None:
        if self._stack:
            self._stack = []
            self._invalidate()
            self.zoomed.emit()

    def _invalidate(self) -> None:
        self._tiles = []
        self._hover = None
        self._layout_size = None
        self.update()

    # -- 배치 ---------------------------------------------------------
    def _ensure_layout(self) -> None:
        size = (self.width(), self.height())
        if self._tiles and self._layout_size == size:
            return
        self._layout_size = size
        self._tiles = treemap.build_tiles(
            self.current_nodes(), 0, 0, self.width(), self.height(),
            max_depth=DRAW_DEPTH,
        )

    def _tile_at(self, x: float, y: float) -> Optional[treemap.Tile]:
        """그 자리의 조각. **가장 깊은 것**을 고른다 - 자식이 부모 위에 있다."""

        found = None
        for tile in self._tiles:
            if (tile.x <= x < tile.x + tile.width
                    and tile.y <= y < tile.y + tile.height):
                if found is None or tile.depth >= found.depth:
                    found = tile
        return found

    # -- 그리기 -------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor(theme.SURFACE))

        self._ensure_layout()
        if not self._tiles:
            self._draw_empty(painter)
            painter.end()
            return

        hues = self._hue_map()
        for tile in self._tiles:
            self._draw_tile(painter, tile, hues)

        if self._hover is not None:
            self._draw_hover(painter, self._hover)
        painter.end()

    def _draw_empty(self, painter: QPainter) -> None:
        painter.setPen(QColor(theme.TEXT_FAINT))
        painter.drawText(
            self.rect(), Qt.AlignCenter, i18n.t("treemap.empty")
        )

    def _group_depth(self) -> int:
        """색조를 나눌 깊이 - **형제가 둘 이상인 가장 얕은 깊이**.

        계정 하나를 볼 때 뿌리는 하나뿐이라, 뿌리마다 색을 주면 화면이 통째로
        한 색이 된다 (실제로 그랬다). 실제로 갈라지는 자리에서 색을 나눠야
        덩어리가 눈에 들어온다."""

        counts = {}
        for tile in self._tiles:
            counts[tile.depth] = counts.get(tile.depth, 0) + 1
        for depth in sorted(counts):
            if counts[depth] > 1:
                return depth
        return 0

    def _hue_map(self) -> dict:
        """조각마다 색조를 미리 정한다 (한 번만 훑는다).

        예전에는 그릴 때마다 앞쪽을 되짚어 조상을 찾았는데, 조각이 수백 개면
        그것만으로 제곱이 된다."""

        palette = _palette(QColor(theme.ACCENT).hue())
        group_depth = self._group_depth()
        hues = {}
        index = 0
        last_group_hue = palette[0]
        for tile in self._tiles:
            if tile.depth == group_depth:
                last_group_hue = palette[index % len(palette)]
                index += 1
                hues[id(tile)] = last_group_hue
            elif tile.depth > group_depth:
                # 자식은 덩어리의 색조를 물려받는다 - 어디에 속한 것인지가
                # 유지되어야 한다.
                hues[id(tile)] = last_group_hue
            else:
                # 덩어리보다 얕은 것은 그릇일 뿐이다. 색을 주면 안에 든 것과
                # 경쟁한다.
                hues[id(tile)] = None
        return hues

    def _draw_tile(self, painter: QPainter, tile, hues) -> None:
        if tile.width <= 0 or tile.height <= 0:
            return
        rect = QRectF(tile.x, tile.y, tile.width, tile.height)
        node = tile.node

        hue = hues.get(id(tile))
        if node.kind in (tree_view.REST, tree_view.MORE):
            # 계산으로 만든 줄이다. 색이 같으면 눌러서 들어가려 한다.
            fill = QColor(theme.SURFACE_SUNKEN).darker(105 + tile.depth * 6)
        elif hue is None:
            # 덩어리를 담는 그릇. 거의 흰색으로 두어 안에 든 것이 드러나게 한다.
            fill = QColor(theme.SURFACE_SUNKEN)
        else:
            # 깊이가 깊을수록 옅게. 안에 든 것이 위에 얹힌 느낌이 된다.
            depth_in_group = max(0, tile.depth - self._group_depth())
            fill = QColor.fromHsv(
                hue,
                max(24, 105 - depth_in_group * 34),
                min(252, 205 + depth_in_group * 18),
            )

        painter.fillRect(rect, fill)

        # 경계선. 얕을수록 진하게 해서 덩어리가 먼저 보이게 한다.
        pen = QPen(QColor(theme.BORDER if tile.depth else theme.BORDER_STRONG))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawRect(rect)

        if getattr(node, "is_notable", False):
            # 칠을 바꾸지 않는다 - 칠은 '어느 덩어리에 속하는가'를 나른다.
            warn = QPen(QColor(theme.DANGER))
            warn.setWidth(2)
            painter.setPen(warn)
            painter.drawRect(rect.adjusted(1, 1, -1, -1))

        self._draw_label(painter, tile)

    def _draw_label(self, painter: QPainter, tile) -> None:
        if tile.width < LABEL_MIN_WIDTH or tile.height < LABEL_MIN_HEIGHT:
            return
        header = treemap.header_height(tile)
        if tile.has_children and header <= 0:
            # 자식이 다 덮어서 이름 쓸 자리가 없다.
            return

        node = tile.node
        name = self._label_for(node)
        size = formatting.format_kb(node.size_kb)
        painter.setPen(QColor(theme.TEXT))

        if tile.has_children:
            area = QRectF(tile.x + 4, tile.y, tile.width - 8, header)
            painter.drawText(area, Qt.AlignVCenter | Qt.AlignLeft, f"{name}  {size}")
            return

        area = QRectF(tile.x + 4, tile.y + 3, tile.width - 8, tile.height - 6)
        painter.drawText(
            area, Qt.AlignTop | Qt.AlignLeft | Qt.TextWordWrap, f"{name}\n{size}"
        )

    def _label_for(self, node) -> str:
        if node.kind == tree_view.REST:
            return i18n.t("treemap.rest")
        if node.kind == tree_view.MORE:
            return i18n.t("tree.more", count=f"{node.hidden_count:,}")
        return node.label

    def _draw_hover(self, painter: QPainter, tile) -> None:
        pen = QPen(QColor(theme.ACCENT))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(
            QRectF(tile.x, tile.y, tile.width, tile.height).adjusted(1, 1, -1, -1)
        )

    # -- 마우스 -------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        self._ensure_layout()
        tile = self._tile_at(event.x(), event.y())
        if tile is not self._hover:
            self._hover = tile
            self.setToolTip(self._tooltip_for(tile) if tile else "")
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        if self._hover is not None:
            self._hover = None
            self.update()

    def _tooltip_for(self, tile) -> str:
        node = tile.node
        lines = [node.path or node.label, formatting.format_kb(node.size_kb)]
        share = node.share_pct
        if share is not None:
            lines.append(i18n.t("treemap.share", pct=f"{share:.1f}"))
        if node.compared:
            if node.previous_kb is None:
                lines.append(i18n.t("tree.new"))
            else:
                lines.append(formatting.format_kb_delta(node.delta_kb))
        return "\n".join(lines)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        """조각을 두 번 누르면 그 안으로 들어간다.

        한 번 누르기로 하지 않은 것은 의도다 - 그림 위에서 마우스를 움직이다
        보면 한 번 누르기가 쉽게 일어나서, 보려던 자리를 잃는다."""

        self._ensure_layout()
        tile = self._tile_at(event.x(), event.y())
        if tile is None:
            return
        node = tile.node
        # 계산으로 만든 줄에는 들어갈 곳이 없다.
        if node.kind in (tree_view.REST, tree_view.MORE):
            return
        if not getattr(node, "children", None):
            return
        self._stack.append(node)
        self._invalidate()
        self.zoomed.emit()
