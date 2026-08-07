import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import detail_scan, scan_store
from tests import support


def _completed(stdout, returncode=0, stderr=""):
    # stdout은 bytes여야 한다 (procio가 바이트 모드로 읽는다) - tests/support.py 참고.
    return support.completed(["du"], stdout=stdout, returncode=returncode, stderr=stderr)


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


class PermissionDeniedTests(unittest.TestCase):
    """관리자가 아닌 사용자를 위한 부분 측정 처리.

    남의 프로젝트 계정을 모니터링할 때 읽을 수 없는 하위 디렉터리는 예외가
    아니라 일상이다. `du`는 그런 디렉터리만 stderr로 알리고 나머지는 계속
    합산한 뒤 exit 1로 끝내므로, 여기서 결과를 통째로 버리면 사실상 모든
    계정이 실패로 기록되어 기준선이 영영 만들어지지 않는다.
    """

    def _denied(self, path, size_kb=1234):
        return support.completed(
            ["du", "-sk", "--", path],
            stdout=f"{size_kb}\t{path}\n",
            stderr=f"du: cannot read directory '{path}/private': Permission denied\n",
            returncode=1,
        )

    def test_partial_result_is_kept_not_discarded(self):
        with patch("smvwp.detail_scan.subprocess.run", return_value=self._denied("/acct/a")):
            outcome = detail_scan.run_du("/acct/a", 60)

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.size_kb, 1234)
        self.assertTrue(outcome.partial)
        self.assertIn("Permission denied", outcome.error_message)

    def test_full_success_is_not_marked_partial(self):
        with patch(
            "smvwp.detail_scan.subprocess.run",
            return_value=support.completed(["du"], stdout="42\t/acct/a\n"),
        ):
            outcome = detail_scan.run_du("/acct/a", 60)

        self.assertTrue(outcome.ok)
        self.assertFalse(outcome.partial)
        self.assertIsNone(outcome.error_message)

    def test_no_usable_total_is_still_an_error(self):
        """총계조차 못 얻으면 그건 진짜 실패다."""

        with patch(
            "smvwp.detail_scan.subprocess.run",
            return_value=support.completed(
                ["du"], stdout="", stderr="du: cannot access '/acct/a'\n", returncode=1
            ),
        ):
            outcome = detail_scan.run_du("/acct/a", 60)

        self.assertFalse(outcome.ok)
        self.assertIsNone(outcome.size_kb)

    def test_checkpoint_records_size_and_partial_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = scan_store.connect(Path(tmp) / "data")
            try:
                scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/acct/a"])
                checkpoint = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
                with patch(
                    "smvwp.detail_scan.subprocess.run", return_value=self._denied("/acct/a")
                ):
                    status = detail_scan.process_one_checkpoint(conn, checkpoint, 60)

                self.assertEqual(status, scan_store.STATUS_DONE)
                self.assertEqual(
                    scan_store.partial_paths(conn, "acct-1", 1), ["/acct/a"]
                )
                row = conn.execute(
                    "SELECT size_kb FROM scan_checkpoints WHERE id = ?", (checkpoint["id"],)
                ).fetchone()
                self.assertEqual(row["size_kb"], 1234)
            finally:
                conn.close()

    def test_fully_readable_account_reports_no_partial_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = scan_store.connect(Path(tmp) / "data")
            try:
                scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/acct/a"])
                checkpoint = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
                with patch(
                    "smvwp.detail_scan.subprocess.run",
                    return_value=support.completed(["du"], stdout="42\t/acct/a\n"),
                ):
                    detail_scan.process_one_checkpoint(conn, checkpoint, 60)
                self.assertEqual(scan_store.partial_paths(conn, "acct-1", 1), [])
            finally:
                conn.close()
