"""폴더 펼쳐 보기 창을 실제로 띄워 본다 (화면 없이).

## 이 화면이 답하는 것

계정 표는 "LAYOUT 이 300GB" 까지만 말한다. 사람이 그다음에 하려는 일은 **그
300GB 가 어디에 있는지** 찾아 들어가는 것인데, 지금까지 답할 자리가 없었다.

## 여기서 지키는 것

- **다시 스캔하지 않는다.** 야간 스캔이 남긴 기록만 읽는다. 창을 연다고
  파일서버에 부하가 가면 아무도 못 쓴다.
- 재지 못한 것과 비어 있는 것을 구분한다.
- 계산으로 만든 줄(나머지·접은 줄)은 진짜 폴더처럼 보이지 않는다.
- 자식은 **펼칠 때** 만든다. 미리 다 만들면 큰 계정에서 창이 굳는다.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover - PyQt5 없는 환경
    HAVE_QT = False

from smvwp import config as config_module, scan_store

_APP = None


def _app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
        from smvwp.gui import theme

        theme.apply(_APP)
    return _APP


def seed(conn, account_id, generation, rows, completed=True):
    now = "2026-09-10T00:00:00+00:00"
    for path, size_kb, depth in rows:
        conn.execute(
            "INSERT OR REPLACE INTO baseline_results "
            "(account_id, generation, path, size_kb, completed_at, depth) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, generation, path, size_kb, now, depth),
        )
    conn.commit()
    if completed:
        scan_store.mark_generation_completed(conn, account_id, generation)


@unittest.skipUnless(HAVE_QT, "PyQt5 없음")
class TreeDialogTests(unittest.TestCase):
    def setUp(self):
        _app()
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        path = root / "proj"
        path.mkdir()
        self.account = config_module.add_account(
            self.config, "proj", str(path), data_dir=self.data_dir
        )
        config_module.save_config(self.data_dir, self.config)
        self.conn = scan_store.connect(self.data_dir)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def dialog(self):
        from smvwp.gui.tree_dialog import TreeDialog

        # 불러오기는 작업 스레드로 도는데, 시험에서 스레드를 기다리면 장비가
        # 바쁠 때 들쭉날쭉해진다. 여기서는 같은 함수를 그 자리에서 부른다.
        from smvwp.gui import tree_dialog

        def run_here(loader, account_id):
            loader._run(account_id)
            return True

        with patch.object(
            tree_dialog._TreeLoader, "run_async", autospec=True, side_effect=run_here
        ):
            dialog = TreeDialog(
                self.data_dir, self.config, account_id=self.account.account_id
            )
        self.addCleanup(dialog.close)
        return dialog

    def tree_rows(self, dialog):
        """보이는 줄의 (폴더 칸, 크기 칸)."""

        rows = []
        stack = [dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            rows.append((item.text(0), item.text(1)))
            stack.extend(item.child(i) for i in range(item.childCount()))
        return rows

    # -- 아직 잰 것이 없을 때 -----------------------------------------
    def test_no_finished_scan_says_so_instead_of_showing_an_empty_tree(self):
        """빈 트리는 '이 계정은 비어 있다'로 읽힌다 - 사실은 아직 안 잰 것이다."""

        from smvwp import i18n

        dialog = self.dialog()
        self.assertEqual(dialog.tree.topLevelItemCount(), 0)
        self.assertEqual(dialog.caption.text(), i18n.t("tree.no_scan"))

    def test_a_generation_that_never_finished_is_not_shown(self):
        """완료 표시가 없으면 아직 도는 중이라 반쪽짜리 수치다."""

        from smvwp import i18n

        seed(self.conn, self.account.account_id, 1,
             [("/proj", 900 * 1024, 0)], completed=False)
        dialog = self.dialog()
        self.assertEqual(dialog.caption.text(), i18n.t("tree.no_scan"))

    # -- 트리 -----------------------------------------------------------
    def rows_for_tree(self):
        return [
            ("/proj", 900 * 1024, 0),
            ("/proj/layout", 500 * 1024, 1),
            ("/proj/sim", 200 * 1024, 1),
            ("/proj/layout/run1", 300 * 1024, 2),
        ]

    def test_the_account_root_appears(self):
        seed(self.conn, self.account.account_id, 1, self.rows_for_tree())
        dialog = self.dialog()
        self.assertEqual(dialog.tree.topLevelItemCount(), 1)
        self.assertEqual(dialog.tree.topLevelItem(0).text(0), "/proj")

    def test_children_are_shown_biggest_first(self):
        seed(self.conn, self.account.account_id, 1, self.rows_for_tree())
        dialog = self.dialog()
        root = dialog.tree.topLevelItem(0)
        names = [root.child(i).text(0) for i in range(root.childCount())]
        self.assertEqual(names[:2], ["layout", "sim"])

    def test_the_gap_between_parent_and_children_gets_its_own_row(self):
        """자식 합이 부모보다 작으면 '나머지는 어디 갔나'가 된다."""

        from smvwp import i18n

        seed(self.conn, self.account.account_id, 1, self.rows_for_tree())
        dialog = self.dialog()
        root = dialog.tree.topLevelItem(0)
        names = [root.child(i).text(0) for i in range(root.childCount())]
        self.assertIn(i18n.t("tree.rest"), names)

    def test_sizes_are_human_readable(self):
        seed(self.conn, self.account.account_id, 1, self.rows_for_tree())
        dialog = self.dialog()
        self.assertIn("MB", dialog.tree.topLevelItem(0).text(1))

    def test_the_caption_says_the_tree_is_not_being_measured_now(self):
        """창을 연다고 파일서버에 부하가 가는 것이 아님을 말해 준다."""

        seed(self.conn, self.account.account_id, 1, self.rows_for_tree())
        dialog = self.dialog()
        self.assertIn("다시 재지 않습니다", dialog.caption.text())

    def test_folders_are_named_inside_the_account_and_the_scan_is_dated(self):
        """뿌리마다 계정 경로 전체를 쓰면 글자가 경로로 다 차고 그림의 머리글은 잘린다.
        그리고 이 트리는 지금이 아니라 끝난 스캔의 기록이다 - 언제 것인지 말한다."""

        from smvwp import formatting

        root = self.account.path
        seed(self.conn, self.account.account_id, 1, [
            (f"{root}/layout", 500 * 1024, 0), (f"{root}/sim", 200 * 1024, 0),
        ])
        state = scan_store.get_account_state(self.conn, self.account.account_id)
        dialog = self.dialog()
        names = [dialog.tree.topLevelItem(i).text(0)
                 for i in range(dialog.tree.topLevelItemCount())]
        self.assertEqual(names, ["layout", "sim"])
        # 날짜는 요약 띠에 - 계정 합계 밑 한 줄.
        self.assertIn(formatting.scan_label(state.last_completed_at, 1), dialog.band.sub.text())

    def test_the_band_shows_the_total_and_how_the_top_folders_split_it(self):
        """글 한 문단이던 설명 대신 합계와 맨 위 폴더들의 몫을 쌓은 막대로 보인다."""

        from smvwp import formatting

        root = self.account.path
        seed(self.conn, self.account.account_id, 1, [
            (f"{root}/layout", 500 * 1024, 0), (f"{root}/sim", 200 * 1024, 0),
        ])
        dialog = self.dialog()
        self.assertEqual(dialog.band.value.text(), formatting.format_kb(700 * 1024))
        self.assertEqual([part[0] for part in dialog.stack.parts()], [500 * 1024, 200 * 1024])

    def test_what_the_walk_left_shows_beside_the_size(self):
        """실제 순회로 잰 계정 - 파일 수와 오래 안 바뀐 양이 폴더 옆에 나온다.

        크기가 같아도 전부 1년 넘게 그대로인 폴더와 어제도 쓰인 폴더는 정리할 때 전혀
        다른 물건인데, 예전에는 화면이 둘을 가르지 못했다."""

        import os
        import time

        from smvwp import detail_scan
        from smvwp.gui.tree_dialog import COL_COLD, COL_FILES
        from smvwp.gui.widgets import CellDelegate

        root = Path(self.account.path)
        old, fresh = root / "old", root / "fresh"
        for folder, days in ((old, 400), (fresh, 1)):
            folder.mkdir()
            for index in range(3):
                target = folder / f"f{index}.bin"
                target.write_bytes(b"x" * 8192)
                stamp = time.time() - days * 86400
                os.utime(target, (stamp, stamp))
        tops = [f"{self.account.path}/old", f"{self.account.path}/fresh"]
        scan_store.seed_checkpoints(self.conn, self.account.account_id, scan_store.BASELINE, 1, tops)
        while True:
            checkpoint = scan_store.next_pending(
                self.conn, self.account.account_id, scan_store.BASELINE, 1)
            if checkpoint is None:
                break
            detail_scan.process_one_checkpoint(
                self.conn, checkpoint, 60, generation=1, engine=detail_scan.ENGINE_PYTHON)
        scan_store.mark_generation_completed(self.conn, self.account.account_id, 1)

        dialog = self.dialog()
        rows = {dialog.tree.topLevelItem(i).text(0): dialog.tree.topLevelItem(i)
                for i in range(dialog.tree.topLevelItemCount())}
        self.assertEqual(rows["old"].text(COL_FILES), "3")
        fraction, _kind = rows["old"].data(COL_COLD, CellDelegate.BAR_ROLE)
        self.assertGreater(fraction, 0.5)
        self.assertIsNone(rows["fresh"].data(COL_COLD, CellDelegate.BAR_ROLE))
        self.assertEqual(rows["fresh"].text(COL_COLD), "-")

    def test_a_scan_without_those_values_leaves_the_cells_empty(self):
        from smvwp.gui.tree_dialog import COL_COLD, COL_FILES

        seed(self.conn, self.account.account_id, 1, self.rows_for_tree())
        dialog = self.dialog()
        item = dialog.tree.topLevelItem(0)
        self.assertEqual((item.text(COL_FILES), item.text(COL_COLD)), ("", ""))

    def test_share_and_change_cells_carry_bars(self):
        from smvwp.gui.tree_dialog import COL_SHARE
        from smvwp.gui.widgets import CellDelegate

        root = self.account.path
        seed(self.conn, self.account.account_id, 1, [
            (f"{root}/layout", 500 * 1024, 0), (f"{root}/sim", 500 * 1024, 0),
        ])
        dialog = self.dialog()
        fraction, _kind = dialog.tree.topLevelItem(0).data(COL_SHARE, CellDelegate.BAR_ROLE)
        self.assertAlmostEqual(fraction, 0.5)

    # -- 펼치기 ---------------------------------------------------------
    def test_deeper_levels_are_not_built_until_expanded(self):
        """미리 다 만들면 디렉터리 수만큼 위젯이 생겨 큰 계정에서 창이 굳는다."""

        seed(self.conn, self.account.account_id, 1, self.rows_for_tree())
        dialog = self.dialog()
        self.assertNotIn("run1", [text for text, _ in self.tree_rows(dialog)])

    def test_expanding_builds_them(self):
        seed(self.conn, self.account.account_id, 1, self.rows_for_tree())
        dialog = self.dialog()
        root = dialog.tree.topLevelItem(0)
        layout = next(
            root.child(i) for i in range(root.childCount())
            if root.child(i).text(0) == "layout"
        )
        layout.setExpanded(True)
        names = [layout.child(i).text(0) for i in range(layout.childCount())]
        self.assertIn("run1", names)

    def test_expanding_twice_does_not_duplicate(self):
        seed(self.conn, self.account.account_id, 1, self.rows_for_tree())
        dialog = self.dialog()
        root = dialog.tree.topLevelItem(0)
        layout = next(
            root.child(i) for i in range(root.childCount())
            if root.child(i).text(0) == "layout"
        )
        layout.setExpanded(True)
        first = layout.childCount()
        layout.setExpanded(False)
        layout.setExpanded(True)
        self.assertEqual(layout.childCount(), first)

    # -- 비교 -----------------------------------------------------------
    def test_without_a_previous_scan_nothing_is_called_new(self):
        """비교할 스캔이 아예 없는 것과 '그때 없었다'는 다른 이야기다."""

        seed(self.conn, self.account.account_id, 1, self.rows_for_tree())
        dialog = self.dialog()
        changes = {text: None for text, _ in self.tree_rows(dialog)}
        root = dialog.tree.topLevelItem(0)
        self.assertEqual(root.text(3), "-")
        self.assertTrue(changes)   # 줄은 있다

    def test_growth_against_the_previous_generation(self):
        account = self.account.account_id
        seed(self.conn, account, 1, [("/proj", 100 * 1024, 0)])
        seed(self.conn, account, 2, [("/proj", 900 * 1024, 0)])
        dialog = self.dialog()
        self.assertIn("+", dialog.tree.topLevelItem(0).text(3))

    def test_a_path_absent_last_time_says_so(self):
        from smvwp import i18n

        account = self.account.account_id
        seed(self.conn, account, 1, [("/proj", 900 * 1024, 0)])
        seed(self.conn, account, 2, [
            ("/proj", 900 * 1024, 0),
            ("/proj/brandnew", 400 * 1024, 1),
        ])
        dialog = self.dialog()
        root = dialog.tree.topLevelItem(0)
        texts = {root.child(i).text(0): root.child(i).text(3) for i in range(root.childCount())}
        self.assertEqual(texts.get("brandnew"), i18n.t("tree.new"))

    def test_computed_rows_make_no_comparison(self):
        """나머지 줄은 이전 하위 구성을 알 수 없어 증감을 지어내면 안 된다."""

        from smvwp import i18n

        account = self.account.account_id
        seed(self.conn, account, 1, self.rows_for_tree())
        seed(self.conn, account, 2, self.rows_for_tree())
        dialog = self.dialog()
        root = dialog.tree.topLevelItem(0)
        rest = next(
            root.child(i) for i in range(root.childCount())
            if root.child(i).text(0) == i18n.t("tree.rest")
        )
        self.assertEqual(rest.text(3), "-")

    # -- 큰 파일 --------------------------------------------------------
    def test_a_big_file_sits_inside_the_remainder_not_beside_it(self):
        """나머지와 나란히 놓으면 같은 용량을 두 번 센 것처럼 읽힌다.

        `layout` 은 500MB 인데 하위 폴더는 300MB 뿐이다. 남은 200MB 가
        '이 폴더에 직접 있는 파일'이고, 그 200MB 를 이루는 것이 이 파일이다."""

        from smvwp import i18n

        account = self.account.account_id
        seed(self.conn, account, 1, self.rows_for_tree())
        scan_store.save_large_files(
            self.conn, account, 1, [(150 * 1024, "/proj/layout/movie.iso")]
        )
        dialog = self.dialog()
        root = dialog.tree.topLevelItem(0)
        layout = next(
            root.child(i) for i in range(root.childCount())
            if root.child(i).text(0) == "layout"
        )
        layout.setExpanded(True)
        names = [layout.child(i).text(0) for i in range(layout.childCount())]
        # 파일은 layout 바로 밑이 아니라 나머지 줄 안에 있다.
        self.assertNotIn("movie.iso", " ".join(names))
        rest = next(
            layout.child(i) for i in range(layout.childCount())
            if layout.child(i).text(0) == i18n.t("tree.rest")
        )
        rest.setExpanded(True)
        inside = [rest.child(i).text(0) for i in range(rest.childCount())]
        self.assertTrue(any("movie.iso" in name for name in inside))

    # -- 언어 -----------------------------------------------------------
    def test_english_does_not_leak_keys(self):
        from smvwp import i18n

        i18n.set_language(i18n.ENGLISH)
        self.addCleanup(i18n.set_language, i18n.KOREAN)
        seed(self.conn, self.account.account_id, 1, self.rows_for_tree())
        dialog = self.dialog()
        text = dialog.caption.text() + dialog.legend.text()
        self.assertNotIn("tree.", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
