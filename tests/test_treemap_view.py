"""트리맵 위젯을 실제로 그려 본다 (화면 없이).

배치 자체는 `test_treemap.py` 가 본다. 여기서 보는 것은 **그리는 쪽에서만
드러나는 것**이다 - 실제로 칠해지는가, 들어가고 나오는 동작이 맞는가,
들어갈 수 없는 칸으로 들어가려 하지 않는가.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtCore import QEvent, QPoint, Qt
    from PyQt5.QtGui import QMouseEvent, QPixmap
    from PyQt5.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover - PyQt5 없는 환경
    HAVE_QT = False

from smvwp import tree_view

GB = 1024 * 1024
_APP = None


def _app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
        from smvwp.gui import theme

        theme.apply(_APP)
    return _APP


def double_click(widget, x, y):
    event = QMouseEvent(
        QEvent.MouseButtonDblClick, QPoint(x, y),
        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
    )
    widget.mouseDoubleClickEvent(event)


def move(widget, x, y):
    event = QMouseEvent(
        QEvent.MouseMove, QPoint(x, y), Qt.NoButton, Qt.NoButton, Qt.NoModifier
    )
    widget.mouseMoveEvent(event)


@unittest.skipUnless(HAVE_QT, "PyQt5 없음")
class TreemapViewTests(unittest.TestCase):
    def setUp(self):
        _app()
        from smvwp.gui.treemap_view import TreemapView

        self.view = TreemapView()
        self.view.resize(600, 400)
        self.addCleanup(self.view.deleteLater)

    def tree(self):
        # 깊이 3 까지 둔다. 가장 깊이 그려지는 조각(깊이 2)이 자식을 가져야
        # 실제 데이터와 같은 모양이 된다 - 그 조각은 덮는 것이 없어 어디를
        # 눌러도 들어간다.
        return tree_view.build_tree([
            ("/proj", 1000 * GB),
            ("/proj/layout", 600 * GB),
            ("/proj/sim", 300 * GB),
            ("/proj/layout/run1", 400 * GB),
            ("/proj/layout/run1/data", 300 * GB),
        ])

    def paint(self):
        """실제로 한 번 칠해 본다 - 그리다 터지는 것은 여기서만 드러난다."""

        pixmap = QPixmap(self.view.size())
        self.view.render(pixmap)
        return pixmap

    def enterable_spot(self):
        """두 번 누르면 실제로 들어가지는 자리를 찾는다.

        조각 가운데를 누르면 그 위에 **덮인 자식**이 잡힌다. 부모는 자식이
        덮지 않은 곳(이름 띠)에서만 잡히고, 가장 깊이 그려진 조각은 덮는 것이
        없으니 어디서나 잡힌다. 이것이 옳은 동작이라, 시험이 좌표 운에
        기대지 않게 실제로 잡히는 자리를 찾아서 누른다."""

        from smvwp import treemap

        for tile in self.view._tiles:
            node = tile.node
            if node.kind in (tree_view.REST, tree_view.MORE):
                continue
            if not getattr(node, "children", None):
                continue
            # 이름 띠는 자식이 덮지 않는다 - 덩어리를 통째로 들어가는 자리다.
            header = treemap.header_height(tile)
            spots = []
            if header > 0:
                spots.append((int(tile.x + tile.width / 2), int(tile.y + header / 2)))
            spots.append((int(tile.x + tile.width / 2), int(tile.y + tile.height / 2)))
            for x, y in spots:
                if self.view._tile_at(x, y) is tile:
                    return x, y
        raise AssertionError("들어갈 수 있는 자리가 없다")

    def enter(self, spot=None):
        x, y = spot or self.enterable_spot()
        double_click(self.view, x, y)

    # -- 그리기 ---------------------------------------------------------
    def test_it_paints_without_data(self):
        """빈 상태에서 터지면 계정을 고르기도 전에 창이 죽는다."""

        self.view.set_roots([])
        self.assertFalse(self.paint().isNull())

    def test_it_paints_with_data(self):
        self.view.set_roots(self.tree())
        self.assertFalse(self.paint().isNull())

    def test_a_zero_sized_widget_does_not_raise(self):
        self.view.set_roots(self.tree())
        self.view.resize(0, 0)
        self.assertIsNotNone(self.paint())

    # -- 들어가고 나오기 --------------------------------------------------
    def test_double_click_goes_inside(self):
        self.view.set_roots(self.tree())
        self.paint()   # 배치가 잡히게 한 번 그린다
        self.assertFalse(self.view.can_go_up())
        self.enter()
        self.assertTrue(self.view.can_go_up())

    def test_going_up_comes_back(self):
        self.view.set_roots(self.tree())
        self.paint()
        self.enter()
        self.view.go_up()
        self.assertFalse(self.view.can_go_up())

    def test_the_path_is_reported_so_the_header_can_show_it(self):
        """그림만 보면 지금 무엇의 안인지 알 수 없다 - 조각 이름은 상대 이름이다."""

        self.view.set_roots(self.tree())
        self.paint()
        self.assertEqual(self.view.path_labels(), [])
        self.enter()
        self.assertTrue(self.view.path_labels())

    def test_home_returns_all_the_way(self):
        self.view.set_roots(self.tree())
        self.paint()
        self.enter()
        self.paint()
        self.view.go_home()
        self.assertEqual(self.view.path_labels(), [])

    def test_a_computed_tile_cannot_be_entered(self):
        """'직접 있는 파일' 은 폴더가 아니다 - 들어갈 곳이 없다."""

        from smvwp.gui import treemap_view as module

        # 하위가 없는 나머지 칸만 있는 트리.
        roots = tree_view.build_tree([("/a", 900 * GB), ("/a/b", 100 * GB)])
        self.view.set_roots(roots)
        self.paint()
        rest = None
        for tile in self.view._tiles:
            if tile.node.kind == tree_view.REST:
                rest = tile
                break
        self.assertIsNotNone(rest, "나머지 칸이 있어야 이 시험이 뜻이 있다")
        double_click(
            self.view, int(rest.x + rest.width / 2), int(rest.y + rest.height / 2)
        )
        self.assertFalse(self.view.can_go_up())
        self.assertIsNotNone(module)

    def test_a_leaf_cannot_be_entered(self):
        roots = tree_view.build_tree([("/a", 900 * GB)])
        self.view.set_roots(roots)
        self.paint()
        double_click(self.view, 300, 200)
        self.assertFalse(self.view.can_go_up())

    def test_clicking_empty_space_does_nothing(self):
        self.view.set_roots([])
        self.paint()
        double_click(self.view, 300, 200)
        self.assertFalse(self.view.can_go_up())

    # -- 색 -------------------------------------------------------------
    def test_sibling_groups_get_different_hues(self):
        """한 색으로 칠하면 붙어 있는 덩어리가 하나로 보인다 (실제로 그랬다)."""

        self.view.set_roots(self.tree())
        self.paint()
        hues = self.view._hue_map()
        group_depth = self.view._group_depth()
        groups = [
            hues[id(tile)] for tile in self.view._tiles
            if tile.depth == group_depth
        ]
        self.assertGreater(len(groups), 1)
        self.assertEqual(len(set(groups)), len(groups))

    def test_the_group_depth_is_where_it_actually_branches(self):
        """계정 하나를 볼 때 뿌리는 하나뿐이다 - 거기서 나누면 온 화면이 한 색이다."""

        self.view.set_roots(self.tree())
        self.paint()
        self.assertEqual(self.view._group_depth(), 1)

    def test_children_keep_their_group_hue(self):
        """자식이 색조를 잃으면 어느 덩어리에 속한 것인지가 사라진다."""

        self.view.set_roots(self.tree())
        self.paint()
        hues = self.view._hue_map()
        group_depth = self.view._group_depth()
        current = None
        for tile in self.view._tiles:
            if tile.depth == group_depth:
                current = hues[id(tile)]
            elif tile.depth > group_depth and tile.node.kind == tree_view.DIRECTORY:
                self.assertEqual(hues[id(tile)], current)

    def test_no_group_takes_the_warning_colour(self):
        """경고 테두리가 빨강이라, 덩어리까지 빨강이면 둘을 가릴 수 없다."""

        from smvwp.gui import treemap_view as module

        for base in range(0, 360, 17):
            for hue in module._palette(base):
                self.assertFalse(
                    hue >= module.RESERVED_HUE_LOW or hue <= module.RESERVED_HUE_HIGH,
                    f"{hue} 가 경고색 범위에 들어감 (base={base})",
                )

    # -- 가리키기 --------------------------------------------------------
    def test_hovering_shows_what_it_is(self):
        self.view.set_roots(self.tree())
        self.paint()
        move(self.view, 300, 200)
        tip = self.view.toolTip()
        self.assertIn("/proj", tip)
        self.assertIn("GB", tip)

    def test_leaving_clears_the_highlight(self):
        self.view.set_roots(self.tree())
        self.paint()
        move(self.view, 300, 200)
        self.view.leaveEvent(None)
        self.assertIsNone(self.view._hover)

    def test_setting_new_roots_forgets_where_we_were(self):
        """계정을 바꿨는데 이전 계정 안에 들어가 있던 상태가 남으면 안 된다."""

        self.view.set_roots(self.tree())
        self.paint()
        self.enter()
        self.view.set_roots(self.tree())
        self.assertFalse(self.view.can_go_up())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
