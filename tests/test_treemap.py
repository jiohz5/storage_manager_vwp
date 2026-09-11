"""용량을 넓이로 그리는 배치.

## 이 그림이 거짓말하기 쉬운 지점

트리맵의 위험은 **빠진 것이 안 보인다**는 것이다. 하위 폴더만 그리면 그 합이
부모보다 작은데도 화면은 부모를 꽉 채우므로, 보는 사람은 "이게 전부"라고 읽는다.

그래서 여기서 가장 힘주어 보는 것은 **넓이가 크기에 비례하는가**와 **형제들이
부모를 빈틈없이 덮는가**다.
"""

import unittest

from smvwp import treemap, tree_view

GB = 1024 * 1024


def total_area(rects):
    return sum(w * h for _x, _y, w, h in rects)


def overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return False
    # 맞닿는 것은 겹친 것이 아니다 (부동소수 오차만큼 너그럽게).
    eps = 1e-6
    return (
        ax + aw > bx + eps and bx + bw > ax + eps
        and ay + ah > by + eps and by + bh > ay + eps
    )


class SquarifyTests(unittest.TestCase):
    def test_area_is_proportional_to_value(self):
        """이 성질이 깨지면 그림 전체가 거짓말이 된다."""

        rects = treemap.squarify([50, 30, 20], 0, 0, 100, 100)
        areas = [w * h for _x, _y, w, h in rects]
        self.assertAlmostEqual(areas[0] / areas[1], 50 / 30, places=6)
        self.assertAlmostEqual(areas[1] / areas[2], 30 / 20, places=6)

    def test_the_whole_rectangle_is_used(self):
        """남는 자리가 있으면 '여기는 비었다'로 읽힌다."""

        rects = treemap.squarify([7, 3, 2, 1], 0, 0, 200, 120)
        self.assertAlmostEqual(total_area(rects), 200 * 120, places=4)

    def test_pieces_do_not_overlap(self):
        rects = treemap.squarify([9, 7, 5, 4, 3, 2, 1], 0, 0, 300, 200)
        for first in range(len(rects)):
            for second in range(first + 1, len(rects)):
                self.assertFalse(
                    overlaps(rects[first], rects[second]),
                    f"{rects[first]} 와 {rects[second]} 가 겹침",
                )

    def test_everything_stays_inside(self):
        rects = treemap.squarify([5, 4, 3, 2, 1], 10, 20, 100, 80)
        for x, y, w, h in rects:
            self.assertGreaterEqual(x + 1e-6, 10)
            self.assertGreaterEqual(y + 1e-6, 20)
            self.assertLessEqual(x + w, 10 + 100 + 1e-6)
            self.assertLessEqual(y + h, 20 + 80 + 1e-6)

    def test_the_biggest_starts_at_the_top_left(self):
        """사람이 보는 순서와 맞아야 한다."""

        rects = treemap.squarify([80, 10, 10], 0, 0, 100, 100)
        self.assertAlmostEqual(rects[0][0], 0)
        self.assertAlmostEqual(rects[0][1], 0)

    def test_pieces_are_not_needlessly_long(self):
        """같은 넓이라도 길쭉하면 서로 견주지 못한다 - squarified 를 쓰는 이유."""

        rects = treemap.squarify([1] * 16, 0, 0, 400, 400)
        worst = max(max(w, h) / min(w, h) for _x, _y, w, h in rects)
        # 순진하게 한 줄로 늘어놓으면 16:1 이 된다.
        self.assertLess(worst, 3.0)

    def test_the_input_order_is_kept(self):
        """돌려받은 목록이 입력과 짝이 맞아야 어느 조각이 무엇인지 안다."""

        rects = treemap.squarify([10, 90], 0, 0, 100, 100)
        self.assertLess(rects[0][2] * rects[0][3], rects[1][2] * rects[1][3])

    # -- 못 그리는 경우 -------------------------------------------------
    def test_zero_sized_items_take_no_room(self):
        rects = treemap.squarify([10, 0, 5], 0, 0, 100, 100)
        self.assertEqual(rects[1][2] * rects[1][3], 0)
        self.assertAlmostEqual(total_area(rects), 100 * 100, places=4)

    def test_all_zero_draws_nothing(self):
        self.assertEqual(treemap.squarify([0, 0], 0, 0, 100, 100), [])

    def test_an_empty_area_draws_nothing(self):
        self.assertEqual(treemap.squarify([10, 5], 0, 0, 0, 100), [])

    def test_no_values_draws_nothing(self):
        self.assertEqual(treemap.squarify([], 0, 0, 100, 100), [])

    def test_negative_values_are_ignored(self):
        """크기가 음수일 리 없지만, 들어와도 배치가 무너지면 안 된다."""

        rects = treemap.squarify([10, -5], 0, 0, 100, 100)
        self.assertAlmostEqual(rects[0][2] * rects[0][3], 100 * 100, places=4)


def node(label, size_kb, children=(), kind=tree_view.DIRECTORY):
    item = tree_view.TreeNode(path=f"/{label}", label=label, size_kb=size_kb, kind=kind)
    item.children = list(children)
    return item


class TileTests(unittest.TestCase):
    def test_children_are_drawn_inside_their_parent(self):
        parent = node("a", 100, [node("a1", 60), node("a2", 40)])
        tiles = treemap.build_tiles([parent], 0, 0, 400, 300)
        outer = tiles[0]
        for tile in tiles[1:]:
            self.assertGreaterEqual(tile.x + 1e-6, outer.x)
            self.assertGreaterEqual(tile.y + 1e-6, outer.y)
            self.assertLessEqual(tile.x + tile.width, outer.x + outer.width + 1e-6)
            self.assertLessEqual(tile.y + tile.height, outer.y + outer.height + 1e-6)

    def test_the_parent_comes_before_its_children(self):
        """그리는 쪽이 이 순서대로 칠하면 자식이 부모 위에 덮인다."""

        parent = node("a", 100, [node("a1", 100)])
        tiles = treemap.build_tiles([parent], 0, 0, 400, 300)
        self.assertEqual(tiles[0].node.label, "a")
        self.assertEqual(tiles[1].node.label, "a1")

    def test_depth_is_recorded(self):
        parent = node("a", 100, [node("a1", 100, [node("a1x", 100)])])
        tiles = treemap.build_tiles([parent], 0, 0, 400, 300)
        self.assertEqual([tile.depth for tile in tiles], [0, 1, 2])

    def test_it_stops_at_the_depth_asked_for(self):
        parent = node("a", 100, [node("a1", 100, [node("a1x", 100)])])
        tiles = treemap.build_tiles([parent], 0, 0, 400, 300, max_depth=1)
        self.assertEqual([tile.node.label for tile in tiles], ["a", "a1"])

    def test_it_does_not_descend_into_a_sliver(self):
        """글자도 못 넣을 조각 안을 또 나누면 경계선만 남는다."""

        small = node("tiny", 1, [node("inside", 1)])
        parent = node("a", 100000, [node("big", 99999), small])
        tiles = treemap.build_tiles([parent], 0, 0, 200, 120)
        self.assertNotIn("inside", [tile.node.label for tile in tiles])

    def test_a_parent_with_children_is_marked(self):
        parent = node("a", 100, [node("a1", 100)])
        tiles = treemap.build_tiles([parent], 0, 0, 400, 300)
        self.assertTrue(tiles[0].has_children)
        self.assertFalse(tiles[1].has_children)

    def test_a_leaf_gets_no_header(self):
        leaf = node("a", 100)
        tiles = treemap.build_tiles([leaf], 0, 0, 400, 300)
        self.assertEqual(treemap.header_height(tiles[0]), 0.0)

    def test_a_roomy_parent_gets_a_header(self):
        """자식으로 꽉 채우면 부모가 무엇이었는지 알 수 없게 된다."""

        parent = node("a", 100, [node("a1", 100)])
        tiles = treemap.build_tiles([parent], 0, 0, 400, 300)
        self.assertGreater(treemap.header_height(tiles[0]), 0.0)

    def test_nothing_in_nothing_out(self):
        self.assertEqual(treemap.build_tiles([], 0, 0, 400, 300), [])
        self.assertEqual(treemap.build_tiles([node("a", 1)], 0, 0, 0, 300), [])


class HonestyTests(unittest.TestCase):
    """이 그림에 빠진 것이 없는가."""

    def tree(self):
        # 900 짜리 폴더 안에 하위가 500 뿐이다. 나머지 400 은 직접 있는 파일.
        return tree_view.build_tree([
            ("/proj", 900 * GB),
            ("/proj/layout", 500 * GB),
        ])

    def test_the_gap_is_drawn_not_dropped(self):
        """하위만 그리면 화면은 꽉 차는데 실제로는 400GB 가 빠진다.

        보는 사람은 "layout 이 이 폴더의 전부"라고 읽게 된다."""

        roots = self.tree()
        tiles = treemap.build_tiles(roots, 0, 0, 400, 300)
        kinds = [tile.node.kind for tile in tiles]
        self.assertIn(tree_view.REST, kinds)

    def test_siblings_exactly_fill_their_parent(self):
        roots = self.tree()
        tiles = treemap.build_tiles(roots, 0, 0, 400, 300, max_depth=1)
        parent = tiles[0]
        children = [tile for tile in tiles if tile.depth == 1]
        self.assertTrue(children)
        inner = treemap._inner_rect(
            parent.x, parent.y, parent.width, parent.height
        )
        self.assertAlmostEqual(
            sum(tile.area for tile in children), inner[2] * inner[3], places=4
        )

    def test_the_area_ratio_matches_the_size_ratio(self):
        """layout 이 500, 나머지가 400 이면 넓이도 5:4 여야 한다."""

        roots = self.tree()
        tiles = treemap.build_tiles(roots, 0, 0, 400, 300, max_depth=1)
        by_kind = {}
        for tile in tiles:
            if tile.depth == 1:
                by_kind[tile.node.kind] = tile.area
        self.assertAlmostEqual(
            by_kind[tree_view.DIRECTORY] / by_kind[tree_view.REST], 5 / 4, places=4
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
