import json
import os
import tempfile
import time
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


class OtherHostTests(unittest.TestCase):
    """데이터 디렉터리는 NFS 에서 여러 장비가 함께 본다.

    예전에는 잠금에 장비가 적히지 않아, 다른 장비가 쥔 잠금의 pid 를 이 장비에서
    찾고(당연히 없으니) 죽은 잠금으로 보고 가져갔다 - 두 장비에서 야간 스캔이 겹쳐
    돌며 같은 DB 와 같은 파일서버를 함께 두드렸다. 창은 로그인할 때마다 다른
    장비에 뜰 수 있고, cron 은 setup_cron 을 돌린 장비에 있다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)

    def write_lock(self, host=None, age_seconds=0):
        payload = {"run_id": "elsewhere", "pid": 4242, "triggered_by": "cron",
                   "started_at": "2026-10-05T13:00:00+00:00"}
        if host is not None:
            payload["host"] = host
        path = scan_lock.lock_file(self.data_dir)
        path.write_text(json.dumps(payload), encoding="utf-8")
        if age_seconds:
            then = time.time() - age_seconds
            os.utime(path, (then, then))

    def test_a_live_lock_on_another_host_is_respected(self):
        self.write_lock(host="other-host")
        # 이 장비에는 그 pid 가 없다 - 그래도 남의 장비에서는 살아 있다.
        with patch("smvwp.scan_lock._pid_alive", return_value=False):
            with self.assertRaises(scan_lock.LockBusyError):
                scan_lock.acquire_lock(self.data_dir, "cron")
            self.assertTrue(scan_lock.is_locked(self.data_dir))

    def test_a_lock_on_another_host_that_went_quiet_is_reclaimed(self):
        self.write_lock(host="other-host", age_seconds=scan_lock.FOREIGN_STALE_SECONDS + 60)
        run_id = scan_lock.acquire_lock(self.data_dir, "cron")
        self.assertNotEqual(run_id, "elsewhere")

    def test_an_old_lock_without_a_host_is_this_hosts(self):
        """판을 올린 날 남아 있던 잠금 파일. 예전처럼 pid 로 본다."""

        self.write_lock()
        with patch("smvwp.scan_lock._pid_alive", return_value=True):
            with self.assertRaises(scan_lock.LockBusyError):
                scan_lock.acquire_lock(self.data_dir, "cron")
        with patch("smvwp.scan_lock._pid_alive", return_value=False):
            self.assertNotEqual(scan_lock.acquire_lock(self.data_dir, "cron"), "elsewhere")

    def test_the_lock_names_this_host(self):
        scan_lock.acquire_lock(self.data_dir, "cron")
        self.assertEqual(scan_lock.read_lock(self.data_dir).host, scan_lock.this_host())

    def test_touch_tells_whether_the_lock_is_still_ours(self):
        run_id = scan_lock.acquire_lock(self.data_dir, "cron")
        self.assertTrue(scan_lock.touch(self.data_dir, run_id))
        self.assertFalse(scan_lock.touch(self.data_dir, "someone-else"))
        scan_lock.lock_file(self.data_dir).unlink()
        self.assertFalse(scan_lock.touch(self.data_dir, run_id))


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
