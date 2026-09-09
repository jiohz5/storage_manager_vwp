"""계정 안을 펼쳐 보는 트리.

## 여기서 지키는 것

- `du` 값은 **자기 포함 합계**다. 자식을 늘어놓으면 부모보다 작아지는데, 그
  차이를 숨기면 "나머지는 어디 갔나"가 된다.
- 트리는 **경로**로 세운다. 쪼개진 체크포인트는 깊이를 자기 루트에서 다시
  세므로 깊이 칸으로 세우면 어긋난다.
- 모르는 것은 모른다고 둔다 - 나머지 줄의 증감은 계산하지 않는다.
"""

import unittest

from smvwp import tree_view


def kinds(nodes):
    return [node.kind for node in nodes]


def labels(nodes):
    return [node.label for node in nodes]


def find(nodes, path):
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if node.path == path and node.kind == tree_view.DIRECTORY:
            return node
        stack.extend(node.children)
    raise AssertionError(f"{path} 없음")


class ShapeTests(unittest.TestCase):
    def test_a_child_hangs_under_its_parent(self):
        roots = tree_view.build_tree([("/a", 300), ("/a/b", 100)])
        self.assertEqual([node.path for node in roots], ["/a"])
        self.assertEqual(roots[0].children[0].path, "/a/b")

    def test_the_label_is_relative_to_the_parent(self):
        roots = tree_view.build_tree([("/a", 300), ("/a/b", 100)])
        self.assertEqual(roots[0].children[0].label, "b")

    def test_the_root_keeps_its_full_path(self):
        roots = tree_view.build_tree([("/srv/acct", 300)])
        self.assertEqual(roots[0].label, "/srv/acct")

    def test_children_come_biggest_first(self):
        roots = tree_view.build_tree(
            [("/a", 900), ("/a/small", 10), ("/a/big", 500), ("/a/mid", 100)]
        )
        names = [node.label for node in roots[0].children if node.kind == tree_view.DIRECTORY]
        self.assertEqual(names, ["big", "mid", "small"])

    def test_it_is_built_from_paths_not_the_depth_column(self):
        """쪼개진 체크포인트는 자기 루트에서 깊이를 0부터 다시 센다.

        깊이 칸을 믿으면 서브트리가 통째로 뿌리로 튀어나온다."""

        rows = [
            ("/a", 900, 0),
            ("/a/b", 400, 1),
            ("/a/b/c", 300, 0),      # 쪼개져서 깊이가 0으로 다시 시작
            ("/a/b/c/d", 200, 1),
        ]
        roots = tree_view.build_tree(rows)
        self.assertEqual(len(roots), 1)
        self.assertEqual(find(roots, "/a/b/c/d").label, "d")

    def test_a_missing_middle_directory_does_not_break_the_tree(self):
        """중간이 비어도 끊기지 않고 가장 가까운 조상에 붙는다."""

        roots = tree_view.build_tree([("/a", 900), ("/a/b/c", 300)])
        self.assertEqual(len(roots), 1)
        child = roots[0].children[0]
        self.assertEqual(child.path, "/a/b/c")
        # 라벨은 조상 기준 상대 경로 - 'c'만 쓰면 어디의 c인지 알 수 없다.
        self.assertEqual(child.label, "b/c")

    def test_several_roots_are_allowed(self):
        roots = tree_view.build_tree([("/a", 100), ("/b", 300)])
        self.assertEqual([node.path for node in roots], ["/b", "/a"])

    def test_nothing_in_nothing_out(self):
        self.assertEqual(tree_view.build_tree([]), [])


class RemainderTests(unittest.TestCase):
    """부모에서 자식 합을 뺀 나머지."""

    def test_the_gap_between_parent_and_children_is_shown(self):
        roots = tree_view.build_tree([("/a", 900 * 1024), ("/a/b", 400 * 1024)])
        rest = [node for node in roots[0].children if node.kind == tree_view.REST]
        self.assertEqual(len(rest), 1)
        self.assertEqual(rest[0].size_kb, 500 * 1024)

    def test_the_remainder_comes_last(self):
        roots = tree_view.build_tree(
            [("/a", 900 * 1024), ("/a/b", 400 * 1024), ("/a/c", 100 * 1024)]
        )
        self.assertEqual(kinds(roots[0].children)[-1], tree_view.REST)

    def test_a_tiny_remainder_is_not_worth_a_row(self):
        """du 는 디렉터리 자기 inode 블록도 부모에 넣는다 - 몇십 KB 는 잡음이다."""

        roots = tree_view.build_tree([("/a", 400 * 1024 + 40), ("/a/b", 400 * 1024)])
        self.assertNotIn(tree_view.REST, kinds(roots[0].children))

    def test_a_leaf_gets_no_remainder_row(self):
        """쪼갤 자식이 없으면 나머지랄 것도 없다 - 그 줄이 곧 전부다."""

        roots = tree_view.build_tree([("/a", 900 * 1024)])
        self.assertEqual(roots[0].children, [])

    def test_a_negative_gap_does_not_become_a_row(self):
        """하드링크나 재측정이 겹치면 자식 합이 부모를 넘을 수 있다."""

        roots = tree_view.build_tree([("/a", 100 * 1024), ("/a/b", 400 * 1024)])
        self.assertNotIn(tree_view.REST, kinds(roots[0].children))

    def test_the_remainder_makes_no_growth_claim(self):
        """이전 세대의 자식 구성이 같다는 보장이 없다 - 그럴듯하지만 틀릴 수 있다."""

        roots = tree_view.build_tree(
            [("/a", 900 * 1024), ("/a/b", 400 * 1024)],
            previous=[("/a", 500 * 1024), ("/a/b", 100 * 1024)],
        )
        rest = [node for node in roots[0].children if node.kind == tree_view.REST][0]
        self.assertIsNone(rest.previous_kb)
        self.assertIsNone(rest.delta_kb)
        self.assertFalse(rest.is_notable)


class OverflowTests(unittest.TestCase):
    def test_too_many_children_are_folded_into_one_row(self):
        entries = [("/a", 10_000_000)]
        entries += [(f"/a/d{index:04d}", 1000 + index) for index in range(150)]
        roots = tree_view.build_tree(entries)
        dirs = [n for n in roots[0].children if n.kind == tree_view.DIRECTORY]
        more = [n for n in roots[0].children if n.kind == tree_view.MORE]
        self.assertEqual(len(dirs), tree_view.MAX_CHILDREN_SHOWN)
        self.assertEqual(len(more), 1)
        self.assertEqual(more[0].hidden_count, 150 - tree_view.MAX_CHILDREN_SHOWN)

    def test_the_folded_row_carries_the_hidden_total(self):
        entries = [("/a", 10_000_000)]
        entries += [(f"/a/d{index:04d}", 1000) for index in range(150)]
        roots = tree_view.build_tree(entries)
        more = [n for n in roots[0].children if n.kind == tree_view.MORE][0]
        self.assertEqual(more.size_kb, 50 * 1000)

    def test_folding_does_not_distort_the_remainder(self):
        """접은 것도 부모 안에 있는 것이다 - 나머지에서 빠지면 안 된다."""

        entries = [("/a", 200 * 1024)]
        entries += [(f"/a/d{index:04d}", 1024) for index in range(150)]
        roots = tree_view.build_tree(entries)
        rest = [n for n in roots[0].children if n.kind == tree_view.REST][0]
        self.assertEqual(rest.size_kb, 200 * 1024 - 150 * 1024)


class ComparisonTests(unittest.TestCase):
    def test_growth_is_measured_per_path(self):
        roots = tree_view.build_tree(
            [("/a", 900 * 1024)], previous=[("/a", 300 * 1024)]
        )
        self.assertEqual(roots[0].delta_kb, 600 * 1024)
        self.assertTrue(roots[0].grew_sharply)

    def test_no_previous_generation_is_not_the_same_as_a_new_path(self):
        """비교할 스캔이 아예 없는 것과 '그때 없었다'는 다른 이야기다."""

        alone = tree_view.build_tree([("/a", 900 * 1024)])[0]
        self.assertFalse(alone.is_new)
        self.assertFalse(alone.compared)

        appeared = tree_view.build_tree(
            [("/a", 900 * 1024)], previous=[("/other", 5)]
        )[0]
        self.assertTrue(appeared.is_new)

    def test_a_small_directory_doubling_is_not_an_alert(self):
        """비율만 보면 4KB -> 8KB 도 2배다. 온 트리가 빨개지면 알림이 아니다."""

        node = tree_view.build_tree([("/a", 8)], previous=[("/a", 4)])[0]
        self.assertTrue(node.compared)
        self.assertFalse(node.grew_sharply)
        self.assertFalse(node.is_notable)

    def test_a_big_new_directory_is_notable(self):
        node = tree_view.build_tree(
            [("/a", 900 * 1024)], previous=[("/other", 5)]
        )[0]
        self.assertTrue(node.is_notable)

    def test_share_is_of_the_account_total(self):
        roots = tree_view.build_tree(
            [("/a", 1000), ("/a/b", 250)], account_total_kb=1000
        )
        self.assertAlmostEqual(roots[0].children[0].share_pct, 25.0)

    def test_without_a_total_the_roots_are_the_total(self):
        roots = tree_view.build_tree([("/a", 1000), ("/a/b", 250)])
        self.assertAlmostEqual(roots[0].children[0].share_pct, 25.0)


class LargeFileTests(unittest.TestCase):
    """2단계에서 모은 큰 파일을 트리 안 제자리에 놓는다."""

    def test_a_file_hangs_under_its_own_directory(self):
        roots = tree_view.build_tree(
            [("/a", 900 * 1024), ("/a/b", 800 * 1024)],
            large_files=[("/a/b/movie.iso", 700 * 1024, None)],
        )
        node = find(roots, "/a/b")
        self.assertEqual(kinds(node.children), [tree_view.FILE])
        self.assertEqual(node.children[0].label, "movie.iso")

    def test_a_file_deeper_than_the_scan_went_hangs_on_its_nearest_ancestor(self):
        roots = tree_view.build_tree(
            [("/a", 900 * 1024)],
            large_files=[("/a/x/y/z/movie.iso", 700 * 1024, None)],
        )
        self.assertEqual(roots[0].children[0].label, "x/y/z/movie.iso")

    def test_a_file_sits_inside_the_remainder_not_beside_it(self):
        """나머지와 나란히 놓으면 같은 용량을 두 번 센 것처럼 읽힌다."""

        roots = tree_view.build_tree(
            [("/a", 900 * 1024), ("/a/b", 100 * 1024)],
            large_files=[("/a/movie.iso", 700 * 1024, None)],
        )
        rest = [n for n in roots[0].children if n.kind == tree_view.REST][0]
        self.assertEqual(labels(rest.children), ["movie.iso"])
        self.assertNotIn(tree_view.FILE, kinds(roots[0].children))

    def test_a_file_outside_every_known_directory_is_dropped(self):
        roots = tree_view.build_tree(
            [("/a", 900 * 1024)], large_files=[("/elsewhere/big", 700 * 1024, None)]
        )
        self.assertEqual(roots[0].children, [])


class CountTests(unittest.TestCase):
    def test_only_real_directories_are_counted(self):
        roots = tree_view.build_tree(
            [("/a", 900 * 1024), ("/a/b", 100 * 1024)],
            large_files=[("/a/movie.iso", 700 * 1024, None)],
        )
        self.assertEqual(tree_view.count_directories(roots), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
