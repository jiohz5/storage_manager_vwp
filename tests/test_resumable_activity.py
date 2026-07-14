import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storage_manager.activity_scan import scan_changed_file_activity
from storage_manager.database import Database
from storage_manager.resumable_scan import TaskTimeout, run_resumable_baseline


class FakeFindProcess:
    def __init__(self, output: bytes, return_code: int = 0):
        self.stdout = io.BytesIO(output)
        self.return_code = return_code

    def wait(self):
        return self.return_code

    def kill(self):
        self.return_code = -9


class ResumableAndActivityTests(unittest.TestCase):
    def test_unbounded_production_budget_keeps_per_task_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            top = account / "results"
            top.mkdir(parents=True)
            db = Database(root / "test.db")
            timeouts = []
            try:
                result = run_resumable_baseline(
                    db,
                    "account-id",
                    str(account),
                    None,
                    task_timeout_seconds=900,
                    du_runner=lambda _path, timeout: timeouts.append(timeout) or 10,
                )
                self.assertTrue(result.complete)
                self.assertEqual(timeouts, [900])
            finally:
                db.close()

    def test_changed_file_scan_without_global_timeout_does_not_start_timer(self):
        with tempfile.TemporaryDirectory() as temp:
            account = Path(temp) / "account"
            account.mkdir()
            with patch(
                "storage_manager.activity_scan.subprocess.Popen",
                return_value=FakeFindProcess(b""),
            ), patch("storage_manager.activity_scan.threading.Timer") as timer:
                result = scan_changed_file_activity(
                    str(account),
                    "2026-07-10 22:00:00",
                    None,
                )
            self.assertTrue(result.complete)
            timer.assert_not_called()

    def test_baseline_keeps_checkpoint_after_error_and_resumes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            top = account / "results"
            top.mkdir(parents=True)
            db = Database(root / "test.db")
            try:
                def fail_once(path, timeout):
                    raise RuntimeError("temporary failure")

                first = run_resumable_baseline(
                    db,
                    "account-id",
                    str(account),
                    30,
                    du_runner=fail_once,
                )
                self.assertFalse(first.complete)
                self.assertTrue(first.resumable)
                self.assertIsNotNone(db.detail_scan_state("account-id"))

                second = run_resumable_baseline(
                    db,
                    "account-id",
                    str(account),
                    30,
                    du_runner=lambda path, timeout: 1234,
                )
                self.assertTrue(second.complete)
                self.assertEqual(second.items, [(str(top), 1234)])
                self.assertIsNotNone(db.detail_scan_state("account-id"))
                state = db.detail_scan_state("account-id")
                db.finish_detail_scan("account-id", str(state[1]))
                self.assertIsNone(db.detail_scan_state("account-id"))
            finally:
                db.close()

    def test_timeout_splits_large_directory_into_smaller_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            top = account / "results"
            child_a = top / "a"
            child_b = top / "b"
            child_a.mkdir(parents=True)
            child_b.mkdir()
            (top / "direct.dat").write_bytes(b"x" * 2048)
            db = Database(root / "test.db")
            calls = []

            def split_then_finish(path, timeout):
                calls.append(path)
                if path == str(top):
                    raise TaskTimeout(path)
                return 100 if path == str(child_a) else 200

            try:
                result = run_resumable_baseline(
                    db,
                    "account-id",
                    str(account),
                    30,
                    du_runner=split_then_finish,
                )
                self.assertTrue(result.complete)
                self.assertGreater(result.items[0][1], 300)
                self.assertIn(str(child_a), calls)
                self.assertIn(str(child_b), calls)
            finally:
                db.close()

    def test_baseline_adds_top_level_entries_created_during_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            first_top = account / "first"
            first_top.mkdir(parents=True)
            db = Database(root / "test.db")

            try:
                first = run_resumable_baseline(
                    db,
                    "account-id",
                    str(account),
                    30,
                    du_runner=lambda path, timeout: (_ for _ in ()).throw(
                        RuntimeError("pause")
                    ),
                )
                self.assertFalse(first.complete)

                second_top = account / "second"
                second_top.mkdir()
                second = run_resumable_baseline(
                    db,
                    "account-id",
                    str(account),
                    30,
                    du_runner=lambda path, timeout: 200 if path == str(second_top) else 100,
                )

                self.assertTrue(second.complete)
                self.assertEqual(dict(second.items), {str(first_top): 100, str(second_top): 200})
            finally:
                db.close()

    def test_changed_file_stream_is_aggregated_without_file_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            account = Path(temp) / "account"
            top = account / "results"
            top.mkdir(parents=True)
            file_a = str(top / "a.dat").encode()
            file_b = str(top / "b.dat").encode()
            output = (
                b"100\t1000.0\t" + file_a + b"\0"
                b"250\t1001.0\t" + file_b + b"\0"
            )
            fake = FakeFindProcess(output)
            with patch(
                "storage_manager.activity_scan.subprocess.Popen",
                return_value=fake,
            ) as popen_mock:
                result = scan_changed_file_activity(
                    str(account),
                    "2026-07-10 22:00:00",
                    30,
                )
            self.assertTrue(result.complete)
            self.assertEqual(result.files_seen, 2)
            self.assertEqual(result.items[0][:3], (str(top), 350, 2))
            command = popen_mock.call_args.args[0]
            self.assertNotIn("\0", command[-1])
            self.assertIn("\\0", command[-1])

    def test_changed_file_stream_can_forward_bounded_record_batches(self):
        with tempfile.TemporaryDirectory() as temp:
            account = Path(temp) / "account"
            account.mkdir()
            file_a = str(account / "a.dat").encode()
            file_b = str(account / "b.log").encode()
            output = (
                b"100\t1000.0\t" + file_a + b"\0"
                b"250\t1001.0\t" + file_b + b"\0"
            )
            received = []
            with patch(
                "storage_manager.activity_scan.subprocess.Popen",
                return_value=FakeFindProcess(output),
            ):
                result = scan_changed_file_activity(
                    str(account),
                    "2026-07-10 22:00:00",
                    30,
                    record_batch=lambda rows: received.extend(rows),
                    record_batch_size=1,
                )

            self.assertTrue(result.complete)
            self.assertEqual(
                received,
                [
                    (str(account / "a.dat"), 100, 1000.0),
                    (str(account / "b.log"), 250, 1001.0),
                ],
            )

    def test_stop_request_preserves_baseline_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            (account / "results").mkdir(parents=True)
            db = Database(root / "test.db")
            try:
                result = run_resumable_baseline(
                    db,
                    "account-id",
                    str(account),
                    30,
                    stop_requested=lambda: True,
                )
                self.assertTrue(result.cancelled)
                self.assertFalse(result.complete)
                self.assertIsNotNone(db.detail_scan_state("account-id"))
            finally:
                db.close()

    def test_activity_stop_before_launch_is_cancelled(self):
        with tempfile.TemporaryDirectory() as temp:
            account = Path(temp) / "account"
            account.mkdir()
            result = scan_changed_file_activity(
                str(account),
                "2026-07-10 22:00:00",
                30,
                stop_requested=lambda: True,
            )
            self.assertTrue(result.cancelled)
            self.assertFalse(result.complete)


if __name__ == "__main__":
    unittest.main()
