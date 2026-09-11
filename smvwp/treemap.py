"""용량을 **넓이**로 그리는 배치 (Qt 없음).

## 왜 그림이 필요한가

"layout 이 420GB" 는 표도 말한다. 그런데 **그 420GB 중 절반이 폴더 하나**라는
것은 표로는 줄을 읽고 암산해야 알 수 있고, 그러면 대부분 안 한다. 넓이로
그리면 그 비교가 눈에서 끝난다.

용량 도구들이 하나같이 이 그림으로 알려져 있는 이유가 그것이다.

## 정확해야 한다 - 이 그림은 거짓말하기 쉽다

트리맵의 위험은 **빠진 것이 안 보인다**는 점이다. 하위 폴더만 그리면 그 합이
부모보다 작은데도 화면은 부모를 꽉 채우므로, 보는 사람은 "이게 전부"라고 읽는다.

그래서 `tree_view` 가 만든 트리를 그대로 쓴다. 거기에는 '이 폴더에 직접 있는
파일'(나머지)과 '그 밖 폴더 N개'(접은 것)가 이미 들어 있어서, 형제들의 합이
**정확히** 부모와 같다. 그 성질을 시험으로 못박는다.

## 배치 방법

squarified treemap (Bruls·Huizing·van Wijk). 같은 넓이라도 길쭉한 조각은
서로 견주기 어렵다 - 가로로 긴 것과 세로로 긴 것의 넓이를 눈으로 비교하지
못하기 때문이다. 이 방법은 조각을 되도록 정사각형에 가깝게 만든다.

외부 라이브러리를 쓰지 않는다. 폐쇄망이라 그럴 수 없기도 하지만, 알고리즘
자체가 짧아서 가져올 이유가 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

# 이보다 작아지는 조각 안으로는 더 들어가지 않는다. 글자도 못 넣고 경계선만
# 남아서, 그릴수록 그림이 지저분해지기만 한다.
MIN_TILE_SIDE = 14.0

# 자식을 그릴 때 부모 안쪽으로 띄우는 여백. 경계가 보여야 "안에 든 것"으로
# 읽힌다.
CHILD_PADDING = 2.0

# 부모 이름을 얹을 위쪽 띠의 높이. 이만큼 여유가 없으면 띠를 만들지 않고
# 자식으로 다 채운다.
HEADER_HEIGHT = 16.0

# 띠를 만들 최소 폭. 좁으면 이름이 어차피 잘린다.
HEADER_MIN_WIDTH = 60.0


@dataclass
class Tile:
    """그릴 조각 하나."""

    node: object          # tree_view.TreeNode
    x: float
    y: float
    width: float
    height: float
    depth: int = 0
    # 이 조각이 자식을 품고 있는가. 품고 있으면 이름은 위쪽 띠에만 쓰고
    # 가운데는 자식에게 내준다.
    has_children: bool = False

    @property
    def area(self) -> float:
        return self.width * self.height


def _worst(areas: Sequence[float], side: float) -> float:
    """이 줄에 든 조각들의 가장 나쁜 가로세로 비. 작을수록 정사각형에 가깝다."""

    total = sum(areas)
    if total <= 0 or side <= 0:
        return float("inf")
    biggest, smallest = max(areas), min(areas)
    if smallest <= 0:
        return float("inf")
    return max(
        (side * side * biggest) / (total * total),
        (total * total) / (side * side * smallest),
    )


def squarify(values: Sequence[float], x: float, y: float,
             width: float, height: float) -> List[tuple]:
    """값들을 넓이에 비례하는 사각형으로 나눈다 (`(x, y, w, h)` 목록).

    입력 순서를 지킨다 - 부르는 쪽이 이미 큰 것부터 정렬해 두었다는 전제다.
    그래야 큰 조각이 왼쪽 위에 오고, 사람이 보는 순서와 맞는다.
    """

    if width <= 0 or height <= 0:
        return []
    positive = [float(value) for value in values]
    total = sum(value for value in positive if value > 0)
    if total <= 0:
        return []

    scale = (width * height) / total
    result: List[tuple] = [None] * len(positive)

    # 넓이가 0 인 것은 배치에서 빼고 자리도 0 으로 준다. 섞어 두면 가로세로
    # 비 계산이 0 으로 나눠진다.
    order = [index for index, value in enumerate(positive) if value > 0]
    for index, value in enumerate(positive):
        if value <= 0:
            result[index] = (x, y, 0.0, 0.0)

    rect_x, rect_y, rect_w, rect_h = x, y, width, height
    cursor = 0
    while cursor < len(order):
        side = min(rect_w, rect_h)
        row = [order[cursor]]
        probe = cursor + 1
        while probe < len(order):
            wider = row + [order[probe]]
            if _worst([positive[i] * scale for i in wider], side) <= _worst(
                [positive[i] * scale for i in row], side
            ):
                row = wider
                probe += 1
            else:
                break

        row_area = sum(positive[i] * scale for i in row)
        if rect_w >= rect_h:
            # 세로로 쌓아 왼쪽에 한 열을 만든다.
            column_width = row_area / rect_h if rect_h else 0.0
            offset = rect_y
            for i in row:
                piece = (positive[i] * scale) / column_width if column_width else 0.0
                result[i] = (rect_x, offset, column_width, piece)
                offset += piece
            rect_x += column_width
            rect_w -= column_width
        else:
            row_height = row_area / rect_w if rect_w else 0.0
            offset = rect_x
            for i in row:
                piece = (positive[i] * scale) / row_height if row_height else 0.0
                result[i] = (offset, rect_y, piece, row_height)
                offset += piece
            rect_y += row_height
            rect_h -= row_height

        cursor = probe
    return result


def build_tiles(nodes, x: float, y: float, width: float, height: float,
                max_depth: int = 2, depth: int = 0) -> List[Tile]:
    """트리 노드들을 조각 목록으로 펼친다 (부모가 먼저, 자식이 나중).

    그리는 쪽은 이 순서대로 칠하면 된다 - 자식이 부모 위에 덮인다.

    `nodes` 는 `tree_view` 가 준 순서(큰 것부터)를 그대로 쓴다. 형제들의 합이
    부모와 같으므로 빈틈 없이 채워지고, 그래서 이 그림은 **빠진 것이 없다**.
    """

    if width <= 0 or height <= 0 or not nodes:
        return []

    sizes = [max(0, getattr(node, "size_kb", 0) or 0) for node in nodes]
    rects = squarify(sizes, x, y, width, height)

    tiles: List[Tile] = []
    for node, rect in zip(nodes, rects):
        if rect is None:
            continue
        tile_x, tile_y, tile_w, tile_h = rect
        children = list(getattr(node, "children", []) or [])
        tile = Tile(
            node=node, x=tile_x, y=tile_y, width=tile_w, height=tile_h, depth=depth
        )
        tiles.append(tile)

        if not children or depth >= max_depth:
            continue
        inner = _inner_rect(tile_x, tile_y, tile_w, tile_h)
        if inner is None:
            continue
        tile.has_children = True
        tiles.extend(
            build_tiles(
                children, inner[0], inner[1], inner[2], inner[3],
                max_depth=max_depth, depth=depth + 1,
            )
        )
    return tiles


def _inner_rect(x: float, y: float, width: float, height: float):
    """자식이 들어갈 안쪽 칸. 너무 좁으면 None (더 안 들어간다).

    이름 띠를 둘 여유가 있으면 위를 그만큼 비운다 - 자식으로 꽉 채우면 부모가
    무엇이었는지 알 수 없게 된다."""

    header = 0.0
    if width >= HEADER_MIN_WIDTH and height >= HEADER_HEIGHT + MIN_TILE_SIDE:
        header = HEADER_HEIGHT

    inner_x = x + CHILD_PADDING
    inner_y = y + header + CHILD_PADDING
    inner_w = width - CHILD_PADDING * 2
    inner_h = height - header - CHILD_PADDING * 2
    if inner_w < MIN_TILE_SIDE or inner_h < MIN_TILE_SIDE:
        return None
    return inner_x, inner_y, inner_w, inner_h


def header_height(tile: Tile) -> float:
    """이 조각이 이름 띠를 가졌는가 - 가졌으면 그 높이.

    그리는 쪽이 이름을 어디에 쓸지 정하는 데 쓴다. 배치와 같은 규칙을 두 번
    쓰면 어긋나므로 여기서 한 번만 정한다."""

    if not tile.has_children:
        return 0.0
    if tile.width >= HEADER_MIN_WIDTH and tile.height >= HEADER_HEIGHT + MIN_TILE_SIDE:
        return HEADER_HEIGHT
    return 0.0
