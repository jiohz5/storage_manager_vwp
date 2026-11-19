"""바로 든 파일이 많은 폴더 - 작은 파일이 잔뜩 모인 곳을 찾는다.

## 왜

큰 파일 목록은 파일 하나하나가 커야 잡힌다. 100MB 짜리 수백 개, 작은 파일 수만 개가 한
폴더에 모이면 수백 GB 인데 어느 목록에도 안 든다 ("20T 30T 인데 큰 파일이 저것밖에 안
되는 게 말이 안 된다" - 실기). 순회는 폴더마다 바로 든 파일의 합을 이미 센다 - 큰 것만
붙잡는다. 폴더 기록은 깊이 3 까지지만 이것은 **깊이와 상관없다**.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import nightly_scan, scan_store, walker
from tests import support

GB = 1024 * 1024


class WalkerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "acct"
        self.deep = self.root / "a" / "b" / "c" / "d" / "e" / "many_small"
        self.deep.mkdir(parents=True)
        for index in range(40):
            (self.deep / f"part_{index:02d}.dat").write_bytes(b"x" * 20000)
        (self.root / "a" / "one.dat").write_bytes(b"x" * 20000)
        (self.root / "empty" / "nested").mkdir(parents=True)

    def walk(self, min_kb=100):
        with patch.object(walker, "_MIN_DIR_BLOCKS", min_kb * 2):
            return walker.walk_tree(str(self.root), max_depth=3, workers=2)

    def test_a_deep_folder_of_small_files_is_found(self):
        outcome = self.walk()
        found = {Path(path).name: (kb, count) for kb, path, count in outcome.largest_dirs}
        self.assertIn("many_small", found, outcome.largest_dirs)
        kb, count = found["many_small"]
        self.assertEqual(count, 40)
        self.assertGreaterEqual(kb, 40 * 20000 // 1024)
        # 깊이 6 - 폴더 기록(깊이 3)에는 없는 자리다.
        self.assertNotIn(str(self.deep), dict(outcome.entries))

    def test_only_files_directly_in_it_count(self):
        outcome = self.walk()
        names = [Path(path).name for _kb, path, _count in outcome.largest_dirs]
        # 위 폴더들은 합계가 커도 바로 든 파일은 작다 - 들지 않는다.
        self.assertNotIn("a", names)
        self.assertNotIn("e", names)
        self.assertNotIn("nested", names)

    def test_the_list_is_capped_and_keeps_the_biggest(self):
        for index in range(5):
            folder = self.root / f"f{index}"
            folder.mkdir()
            (folder / "x.dat").write_bytes(b"x" * (200000 + index * 50000))
        with patch.object(walker, "LARGEST_DIRS_KEPT", 3):
            outcome = self.walk()
        self.assertEqual(len(outcome.largest_dirs), 3)
        self.assertEqual([Path(path).name for _kb, path, _c in outcome.largest_dirs],
                         ["many_small", "f4", "f3"])

    def test_large_files_keep_far_more_than_fifty(self):
        self.assertGreaterEqual(walker.LARGEST_FILES_KEPT, 300)


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.conn = scan_store.connect(Path(tmp.name) / "data")
        self.addCleanup(self.conn.close)

    def test_changes_against_the_previous_generation(self):
        scan_store.save_large_dirs(self.conn, "a1", 1, [(100 * GB, "/acct/x", 900)])
        scan_store.save_large_dirs(self.conn, "a1", 2, [
            (300 * GB, "/acct/x", 2700), (50 * GB, "/acct/new", 10)])
        rows = scan_store.large_dir_changes(self.conn, "a1", 2, 1)
        self.assertEqual(rows, [["/acct/x", 300 * GB, 2700, 100 * GB, True],
                                ["/acct/new", 50 * GB, 10, None, True]])
        first = scan_store.large_dir_changes(self.conn, "a1", 1, None)
        self.assertEqual(first, [["/acct/x", 100 * GB, 900, None, False]])

    def test_they_go_with_the_generation(self):
        for generation in (1, 2, 3):
            scan_store.save_large_dirs(self.conn, "a1", generation, [(GB, "/acct/x", 1)])
            self.conn.execute(
                "INSERT OR REPLACE INTO baseline_results "
                "(account_id, generation, path, size_kb, completed_at, depth) VALUES (?, ?, ?, ?, ?, ?)",
                ("a1", generation, "/acct", GB, "2026-01-01T00:00:00+00:00", 0))
        self.conn.commit()
        scan_store.prune_old_generations(self.conn, "a1", keep_last=2)
        left = self.conn.execute("SELECT DISTINCT generation FROM baseline_large_dirs").fetchall()
        self.assertEqual(sorted(row[0] for row in left), [2, 3])


class SnapshotTests(unittest.TestCase):
    """밤 처리 경로로 저장되어 상세 스캔 상태까지 오는가."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"
        self.config = config_module.load_config(self.data_dir)
        self.config.accounts = [config_module.Account(name="proj", path="/acct", account_id="a1")]

    def pass_(self, generation, dirs):
        made = support.whole({"/acct/a": 900 * GB})
        made.largest_dirs = dirs
        with scan_store.session(self.data_dir) as conn:
            support.scan_pass(conn, "a1", generation, ["/acct/a"], {"/acct/a": made})

    def test_the_snapshot_carries_big_folders_with_their_change(self):
        self.pass_(1, [(100 * GB, "/acct/a/run/out", 5000)])
        self.pass_(2, [(400 * GB, "/acct/a/run/out", 20000)])
        snapshot = nightly_scan.get_status_snapshot(self.data_dir, self.config)
        self.assertEqual(snapshot.accounts[0].large_dirs,
                         [["/acct/a/run/out", 400 * GB, 20000, 100 * GB, True]])
