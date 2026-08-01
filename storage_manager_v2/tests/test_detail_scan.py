import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import detail_scan, scan_store


def _completed(stdout, returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["du"], returncode=returncode, stdout=stdout, stderr=stderr)


class RunDuTests(unittest.TestCase):
    @patch("smvwp.detail_scan.subprocess.run")
    def test_parses_size_kb(self, mock_run):
        mock_run.return_value = _completed("123456\t/user/project_a/data\n")
        outcome = detail_scan.run_du("/user/project_a/data", timeout_seconds=60)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.size_kb, 123456)

    @patch("smvwp.detail_scan.subprocess.run")
    def test_timeout_reported_not_raised(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="du", timeout=60)
        outcome = detail_scan.run_du("/slow", timeout_seconds=60)
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.timed_out)

    @patch("smvwp.detail_scan.subprocess.run")
    def test_permission_error_reported_as_failure_not_timeout(self, mock_run):
        mock_run.return_value = _completed("", returncode=1, stderr="du: cannot read directory: Permission denied")
        outcome = detail_scan.run_du("/denied", timeout_seconds=60)
        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.timed_out)
        self.assertIn("Permission denied", outcome.error_message)


class ListImmediateSubdirsTests(unittest.TestCase):
    def test_lists_only_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "subdir_a").mkdir()
            (root / "subdir_b").mkdir()
            (root / "file.txt").write_text("x", encoding="utf-8")
            result = detail_scan.list_immediate_subdirs(str(root))
            self.assertEqual(len(result), 2)
            self.assertTrue(all("subdir" in path for path in result))

    def test_permission_error_returns_empty_list(self):
        result = detail_scan.list_immediate_subdirs("/definitely/does/not/exist/xyz")
        self.assertEqual(result, [])


class ProcessOneCheckpointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = scan_store.connect(Path(self._tmp.name))

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    @patch("smvwp.detail_scan.subprocess.run")
    def test_success_marks_done_with_size(self, mock_run):
        mock_run.return_value = _completed("777\t/user/a\n")
        scan_store.seed_checkpoints(self.conn, "acct-1", scan_store.BASELINE, 1, ["/user/a"])
        checkpoint = scan_store.next_pending(self.conn, "acct-1", scan_store.BASELINE, 1)

        status = detail_scan.process_one_checkpoint(self.conn, checkpoint, timeout_seconds=60)

        self.assertEqual(status, scan_store.STATUS_DONE)
        row = self.conn.execute("SELECT status, size_kb FROM scan_checkpoints WHERE id = ?", (checkpoint["id"],)).fetchone()
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["size_kb"], 777)

    @patch("smvwp.detail_scan.list_immediate_subdirs")
    @patch("smvwp.detail_scan.subprocess.run")
    def test_timeout_with_children_splits(self, mock_run, mock_list_subdirs):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="du", timeout=1)
        mock_list_subdirs.return_value = ["/user/a/sub1", "/user/a/sub2"]

        scan_store.seed_checkpoints(self.conn, "acct-1", scan_store.BASELINE, 1, ["/user/a"])
        checkpoint = scan_store.next_pending(self.conn, "acct-1", scan_store.BASELINE, 1)

        status = detail_scan.process_one_checkpoint(self.conn, checkpoint, timeout_seconds=1)

        self.assertEqual(status, scan_store.STATUS_SPLIT)
        remaining = self.conn.execute(
            "SELECT path, depth, parent_id FROM scan_checkpoints WHERE status = 'pending' ORDER BY id"
        ).fetchall()
        self.assertEqual([r["path"] for r in remaining], ["/user/a/sub1", "/user/a/sub2"])
        self.assertTrue(all(r["depth"] == 1 for r in remaining))
        self.assertTrue(all(r["parent_id"] == checkpoint["id"] for r in remaining))

    @patch("smvwp.detail_scan.list_immediate_subdirs", return_value=[])
    @patch("smvwp.detail_scan.subprocess.run")
    def test_timeout_without_children_becomes_error(self, mock_run, _mock_list_subdirs):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="du", timeout=1)

        scan_store.seed_checkpoints(self.conn, "acct-1", scan_store.BASELINE, 1, ["/user/leaf"])
        checkpoint = scan_store.next_pending(self.conn, "acct-1", scan_store.BASELINE, 1)

        status = detail_scan.process_one_checkpoint(self.conn, checkpoint, timeout_seconds=1)

        self.assertEqual(status, scan_store.STATUS_ERROR)


if __name__ == "__main__":
    unittest.main()
