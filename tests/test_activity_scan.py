import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import activity_scan, scan_store
from tests import support


def _completed(stdout, returncode=0, stderr=""):
    # stdout은 bytes여야 한다 (procio가 바이트 모드로 읽는다) - tests/support.py 참고.
    return support.completed(["find"], stdout=stdout, returncode=returncode, stderr=stderr)


class RunFindChangedTests(unittest.TestCase):
    @patch("smvwp.activity_scan.subprocess.run")
    def test_counts_matched_lines(self, mock_run):
        mock_run.return_value = _completed("/user/a/f1\n/user/a/f2\n/user/a/f3\n")
        outcome = activity_scan.run_find_changed("/user/a", "2026-07-01T00:00:00", timeout_seconds=60)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.changed_count, 3)

    @patch("smvwp.activity_scan.subprocess.run")
    def test_no_matches_is_zero_not_error(self, mock_run):
        mock_run.return_value = _completed("")
        outcome = activity_scan.run_find_changed("/user/a", "2026-07-01T00:00:00", timeout_seconds=60)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.changed_count, 0)

    @patch("smvwp.activity_scan.subprocess.run")
    def test_timeout_reported(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="find", timeout=60)
        outcome = activity_scan.run_find_changed("/slow", "2026-07-01T00:00:00", timeout_seconds=60)
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.timed_out)

    @patch("smvwp.activity_scan.subprocess.run")
    def test_partial_output_with_nonzero_exit_still_counted(self, mock_run):
        # find는 하위 디렉터리 하나가 권한 오류여도 나머지는 계속 진행하고
        # exit code만 0이 아니게 남기는 일이 흔하다 - 부분 결과를 버리면 안 된다.
        mock_run.return_value = _completed("/user/a/f1\n", returncode=1, stderr="find: /user/a/locked: Permission denied")
        outcome = activity_scan.run_find_changed("/user/a", "2026-07-01T00:00:00", timeout_seconds=60)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.changed_count, 1)

    @patch("smvwp.activity_scan.subprocess.run")
    def test_complete_failure_with_no_output_is_error(self, mock_run):
        mock_run.return_value = _completed("", returncode=1, stderr="find: No such file or directory")
        outcome = activity_scan.run_find_changed("/gone", "2026-07-01T00:00:00", timeout_seconds=60)
        self.assertFalse(outcome.ok)
        self.assertIn("No such file", outcome.error_message)


class ProcessOneCheckpointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = scan_store.connect(Path(self._tmp.name))

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    @patch("smvwp.activity_scan.subprocess.run")
    def test_success_marks_done_with_count(self, mock_run):
        mock_run.return_value = _completed("/user/a/f1\n/user/a/f2\n")
        scan_store.seed_checkpoints(self.conn, "acct-1", scan_store.ACTIVITY, 1, ["/user/a"])
        checkpoint = scan_store.next_pending(self.conn, "acct-1", scan_store.ACTIVITY, 1)

        status = activity_scan.process_one_checkpoint(self.conn, checkpoint, "2026-07-01T00:00:00", timeout_seconds=60)

        self.assertEqual(status, scan_store.STATUS_DONE)
        row = self.conn.execute(
            "SELECT status, changed_count FROM scan_checkpoints WHERE id = ?", (checkpoint["id"],)
        ).fetchone()
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["changed_count"], 2)

    @patch("smvwp.activity_scan.list_immediate_subdirs")
    @patch("smvwp.activity_scan.subprocess.run")
    def test_timeout_splits_into_children(self, mock_run, mock_list_subdirs):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="find", timeout=1)
        mock_list_subdirs.return_value = ["/user/a/sub1"]

        scan_store.seed_checkpoints(self.conn, "acct-1", scan_store.ACTIVITY, 1, ["/user/a"])
        checkpoint = scan_store.next_pending(self.conn, "acct-1", scan_store.ACTIVITY, 1)

        status = activity_scan.process_one_checkpoint(self.conn, checkpoint, "2026-07-01T00:00:00", timeout_seconds=1)

        self.assertEqual(status, scan_store.STATUS_SPLIT)

    def test_total_changed_sums_done_checkpoints_only(self):
        scan_store.seed_checkpoints(self.conn, "acct-1", scan_store.ACTIVITY, 1, ["/a", "/b"])
        first = scan_store.next_pending(self.conn, "acct-1", scan_store.ACTIVITY, 1)
        scan_store.mark_done(self.conn, first["id"], changed_count=5)
        second = scan_store.next_pending(self.conn, "acct-1", scan_store.ACTIVITY, 1)
        scan_store.mark_error(self.conn, second["id"], "권한 없음")

        total = activity_scan.total_changed(self.conn, "acct-1", 1)
        self.assertEqual(total, 5)


if __name__ == "__main__":
    unittest.main()


class SnapshotExclusionTests(unittest.TestCase):
    def test_find_prunes_snapshot_dirs(self):
        """`-prune`은 그 디렉터리로 내려가지 않는다는 뜻이다. 스냅샷 안의
        파일은 과거 사본이라 '최근 변경'으로 셀 대상이 아니고, 세대만큼
        중복으로 잡혀 변경 건수를 부풀린다."""

        command = activity_scan.find_command("/user/project_a", "2026-01-01T00:00:00")
        self.assertIn("-prune", command)
        self.assertIn(".snapshot", command)
        # prune 대상이 -newermt 조건보다 앞에 와야 실제로 가지치기가 된다.
        self.assertLess(command.index("-prune"), command.index("-newermt"))
        self.assertEqual(command[-1], "-print")

    def test_run_find_passes_the_prune(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["argv"] = list(command)
            return support.completed(list(command), stdout="")

        with patch("smvwp.activity_scan.subprocess.run", side_effect=fake_run):
            activity_scan.run_find_changed("/acct/a", "2026-01-01T00:00:00", 60)

        self.assertIn("-prune", captured["argv"])
