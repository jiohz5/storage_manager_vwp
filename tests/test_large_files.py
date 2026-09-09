"""파일 하나가 비정상적으로 큰 것을 가려낸다.

## 왜 이것이 없었나

지금까지 파일 하나하나의 크기는 어디에도 남지 않았다. 순회는 디렉터리 합계만
저장하고 검색 인덱스는 이름만 담는다. 그래서 300GB 짜리 디렉터리가 고른 파일
3천 개인지 **한 파일이 280GB** 인지 구분할 수 없었다. 뒤쪽은 사람이 바로 손댈
수 있는 것이라 값어치가 다르다.

## 여기서 지키는 것

- 판정 규칙이 **사람이 설명할 수 있는 것**인가. "왜 걸렸나"에 답해야 한다.
- **모르는 것을 지어내지 않는가.** 지난 상위 목록에 없었다는 사실과 "새로
  생겼다"는 다른 이야기다.
"""

import tempfile
import unittest
from pathlib import Path

from smvwp import large_files, scan_store


class ShareTests(unittest.TestCase):
    """계정에서 차지하는 비중."""

    def test_share_is_a_percentage_of_the_account(self):
        item = large_files.LargeFile("/a/big", size_kb=200, account_total_kb=1000)
        self.assertAlmostEqual(item.share_pct, 20.0)

    def test_a_dominant_file_is_flagged(self):
        item = large_files.LargeFile("/a/big", size_kb=200, account_total_kb=1000)
        self.assertTrue(item.dominates_account)

    def test_a_small_share_is_not_flagged(self):
        item = large_files.LargeFile("/a/x", size_kb=5, account_total_kb=1000)
        self.assertFalse(item.dominates_account)

    def test_unknown_total_gives_no_share(self):
        """계정 총량을 모를 때 0% 로 쓰면 '작다'고 잘못 읽힌다."""

        item = large_files.LargeFile("/a/x", size_kb=200, account_total_kb=None)
        self.assertIsNone(item.share_pct)
        self.assertFalse(item.dominates_account)

    def test_a_zero_total_does_not_divide_by_zero(self):
        item = large_files.LargeFile("/a/x", size_kb=200, account_total_kb=0)
        self.assertIsNone(item.share_pct)


class GrowthTests(unittest.TestCase):
    def test_a_file_that_grew_sharply_is_flagged(self):
        item = large_files.LargeFile("/a/x", size_kb=300, previous_kb=100)
        self.assertTrue(item.grew_sharply)
        self.assertEqual(item.delta_kb, 200)

    def test_mild_growth_is_not_flagged(self):
        item = large_files.LargeFile("/a/x", size_kb=110, previous_kb=100)
        self.assertFalse(item.grew_sharply)

    def test_a_file_that_shrank_is_not_flagged_as_growth(self):
        item = large_files.LargeFile("/a/x", size_kb=50, previous_kb=100)
        self.assertFalse(item.grew_sharply)
        self.assertEqual(item.delta_kb, -50)

    def test_no_previous_value_means_no_growth_claim(self):
        """이전 값을 모르면 '늘었다'고 말할 수 없다."""

        item = large_files.LargeFile("/a/x", size_kb=300, previous_kb=None)
        self.assertFalse(item.grew_sharply)
        self.assertIsNone(item.delta_kb)

    def test_a_previous_zero_does_not_divide_by_zero(self):
        item = large_files.LargeFile("/a/x", size_kb=300, previous_kb=0)
        self.assertFalse(item.grew_sharply)


class HonestyTests(unittest.TestCase):
    """모르는 것을 지어내지 않는가."""

    def test_absence_from_the_list_is_not_called_new(self):
        """지난 상위 목록에 없었다 != 새로 생겼다.

        그때는 작아서 목록에 못 들었을 수도 있다. 이 데이터로는 가릴 수 없다."""

        item = large_files.LargeFile("/a/x", size_kb=300, previous_kb=None)
        self.assertTrue(item.is_new_to_the_list)
        # 이름이 사실을 넘어서지 않는지 - 속성 이름 자체를 못박는다.
        self.assertFalse(hasattr(item, "is_new_file"))

    def test_the_reason_key_says_list_not_file(self):
        item = large_files.LargeFile("/a/x", size_kb=300, previous_kb=None)
        self.assertIn("large.reason.new", item.reasons())

    def test_reasons_are_empty_when_nothing_stands_out(self):
        item = large_files.LargeFile(
            "/a/x", size_kb=100, previous_kb=100, account_total_kb=100000
        )
        self.assertEqual(item.reasons(), [])
        self.assertFalse(item.is_notable)

    def test_all_three_reasons_can_stack(self):
        item = large_files.LargeFile(
            "/a/x", size_kb=900, previous_kb=None, account_total_kb=1000
        )
        self.assertEqual(len(item.reasons()), 2)   # 비중 + 목록에 없던 것
        self.assertTrue(item.is_notable)


class BuildTests(unittest.TestCase):
    def test_it_takes_the_shape_the_store_gives(self):
        rows = [("/a/big", 500, 100), ("/a/new", 300, None)]
        built = large_files.build(rows, account_total_kb=1000)
        self.assertEqual([f.path for f in built], ["/a/big", "/a/new"])
        self.assertEqual(built[0].previous_kb, 100)
        self.assertTrue(built[1].is_new_to_the_list)

    def test_empty_input(self):
        self.assertEqual(large_files.build([], 1000), [])


class StoreTests(unittest.TestCase):
    """저장과 세대 비교."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.conn = scan_store.connect(self.data_dir)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def test_round_trip_biggest_first(self):
        scan_store.save_large_files(
            self.conn, "acct", 1, [(500, "/a/mid"), (900, "/a/big")]
        )
        rows = scan_store.largest_files(self.conn, "acct", 1)
        self.assertEqual([r["path"] for r in rows], ["/a/big", "/a/mid"])

    def test_the_same_path_is_replaced_not_duplicated(self):
        """쪼개기 뒤 재측정이 겹칠 수 있다 - 나중 값이 더 정확하다."""

        scan_store.save_large_files(self.conn, "acct", 1, [(500, "/a/x")])
        scan_store.save_large_files(self.conn, "acct", 1, [(700, "/a/x")])
        rows = scan_store.largest_files(self.conn, "acct", 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["size_kb"], 700)

    def test_changes_against_the_previous_generation(self):
        scan_store.save_large_files(self.conn, "acct", 1, [(100, "/a/x")])
        scan_store.save_large_files(
            self.conn, "acct", 2, [(400, "/a/x"), (300, "/a/y")]
        )
        changes = dict(
            (path, (now, before))
            for path, now, before in scan_store.large_file_changes(
                self.conn, "acct", 2, 1
            )
        )
        self.assertEqual(changes["/a/x"], (400, 100))
        self.assertEqual(changes["/a/y"], (300, None))

    def test_the_first_generation_has_nothing_to_compare(self):
        scan_store.save_large_files(self.conn, "acct", 1, [(400, "/a/x")])
        changes = scan_store.large_file_changes(self.conn, "acct", 1, None)
        self.assertEqual(changes, [("/a/x", 400, None)])

    def test_old_generations_are_pruned(self):
        """빠뜨리면 계정마다 세대마다 수십 행씩 영원히 쌓인다."""

        for generation in (1, 2, 3):
            scan_store.save_large_files(
                self.conn, "acct", generation, [(100, "/a/x")]
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO baseline_results "
                "(account_id, generation, path, size_kb, completed_at, depth) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("acct", generation, "/a", 100, "2026-01-01T00:00:00+00:00", 0),
            )
        self.conn.commit()

        scan_store.prune_old_generations(self.conn, "acct", keep_last=2)
        left = self.conn.execute(
            "SELECT DISTINCT generation FROM baseline_large_files WHERE account_id = ?",
            ("acct",),
        ).fetchall()
        self.assertEqual(sorted(row[0] for row in left), [2, 3])

    def test_saving_nothing_is_not_an_error(self):
        self.assertEqual(scan_store.save_large_files(self.conn, "acct", 1, []), 0)


class WalkerCollectionTests(unittest.TestCase):
    """순회가 지나가는 김에 모으는가 - 디스크를 다시 읽지 않는다."""

    def setUp(self):
        from smvwp import walker

        self.walker = walker
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "t"
        (self.root / "sub").mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)

    def _walk(self, min_kb=1):
        from unittest.mock import patch

        with patch.object(self.walker, "_MIN_BLOCKS", min_kb * 1024 // 512):
            return self.walker.walk_tree(str(self.root), max_depth=0, workers=2)

    def test_big_files_come_back_biggest_first(self):
        for name, size in (("a", 30000), ("b", 90000), ("c", 60000)):
            (self.root / "sub" / name).write_bytes(b"x" * size)
        outcome = self._walk()
        names = [Path(path).name for _kb, path in outcome.largest_files]
        self.assertEqual(names, ["b", "c", "a"])

    def test_small_files_are_not_candidates(self):
        """작은 파일 수십만 개에서 힙을 흔들면 순회가 느려진다."""

        (self.root / "sub" / "tiny").write_bytes(b"x" * 10)
        (self.root / "sub" / "big").write_bytes(b"x" * 300000)
        outcome = self._walk(min_kb=100)
        names = [Path(path).name for _kb, path in outcome.largest_files]
        self.assertEqual(names, ["big"])

    def test_the_list_is_capped(self):
        """수천 개를 모아 봐야 아무도 안 읽고 메모리만 쓴다."""

        for index in range(self.walker.LARGEST_FILES_KEPT + 10):
            (self.root / "sub" / f"f{index}").write_bytes(b"x" * (2000 + index))
        outcome = self._walk()
        self.assertEqual(
            len(outcome.largest_files), self.walker.LARGEST_FILES_KEPT
        )

    def test_the_cap_keeps_the_biggest_not_the_first_seen(self):
        for index in range(self.walker.LARGEST_FILES_KEPT + 5):
            (self.root / "sub" / f"f{index}").write_bytes(b"x" * (2000 + index * 100))
        outcome = self._walk()
        kept = {Path(path).name for _kb, path in outcome.largest_files}
        # 가장 작은 다섯은 밀려나야 한다.
        self.assertNotIn("f0", kept)
        self.assertIn(f"f{self.walker.LARGEST_FILES_KEPT + 4}", kept)

    def test_collecting_does_not_change_the_total(self):
        """곁다리로 모으는 것이지 크기 계산을 건드리면 안 된다."""

        for name in ("a", "b"):
            (self.root / "sub" / name).write_bytes(b"x" * 50000)
        with_collection = self._walk().root_size_kb
        plain = self._walk(min_kb=10 ** 9).root_size_kb
        self.assertEqual(with_collection, plain)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
