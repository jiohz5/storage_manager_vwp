import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import scan_lock


class AcquireReleaseLockTests(unittest.TestCase):
    def test_acquire_then_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            run_id = scan_lock.acquire_lock(data_dir, "cron")
            self.assertTrue(scan_lock.is_locked(data_dir))
            scan_lock.release_lock(data_dir, run_id)
            self.assertFalse(scan_lock.is_locked(data_dir))

    @patch("smvwp.scan_lock._pid_alive", return_value=True)
    def test_busy_when_live_process_holds_lock(self, _mock_alive):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            scan_lock.acquire_lock(data_dir, "cron")
            with self.assertRaises(scan_lock.LockBusyError):
                scan_lock.acquire_lock(data_dir, "cron")

    @patch("smvwp.scan_lock._pid_alive", return_value=False)
    def test_stale_lock_from_dead_process_is_reclaimed(self, _mock_alive):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            first_run_id = scan_lock.acquire_lock(data_dir, "cron")
            second_run_id = scan_lock.acquire_lock(data_dir, "cron")
            self.assertNotEqual(first_run_id, second_run_id)

    def test_release_only_removes_matching_run_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            first_run_id = scan_lock.acquire_lock(data_dir, "cron")
            scan_lock.release_lock(data_dir, first_run_id)
            second_run_id = scan_lock.acquire_lock(data_dir, "terminal")
            # 이제 예전 run_id로 release를 다시 불러도 새 잠금은 안 지워져야 한다.
            scan_lock.release_lock(data_dir, first_run_id)
            self.assertTrue(scan_lock.is_locked(data_dir))
            info = scan_lock.read_lock(data_dir)
            self.assertEqual(info.run_id, second_run_id)


class StopRequestTests(unittest.TestCase):
    def test_stop_requested_matches_run_id_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            scan_lock.request_stop(data_dir, "run-a")
            self.assertTrue(scan_lock.is_stop_requested(data_dir, "run-a"))
            self.assertFalse(scan_lock.is_stop_requested(data_dir, "run-b"))

    def test_no_request_file_means_not_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.assertFalse(scan_lock.is_stop_requested(data_dir, "run-a"))

    def test_clear_stop_request_removes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            scan_lock.request_stop(data_dir, "run-a")
            scan_lock.clear_stop_request(data_dir)
            self.assertFalse(scan_lock.is_stop_requested(data_dir, "run-a"))
            # 존재하지 않는 상태에서 다시 clear해도 에러가 나면 안 된다.
            scan_lock.clear_stop_request(data_dir)


if __name__ == "__main__":
    unittest.main()
