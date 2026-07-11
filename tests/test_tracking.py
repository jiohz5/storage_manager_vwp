import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from storage_manager.scheduler import read_cron_status, remove_cron
from storage_manager.tracking import (
    clear_scan_stop,
    next_scheduled_run,
    read_scan_status,
    request_scan_stop,
    scan_stop_requested,
    write_scan_status,
)


class TrackingTests(unittest.TestCase):
    def test_runtime_status_and_stop_request_are_bound_to_run_id(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "storage_manager.tracking.process_is_alive",
            return_value=True,
        ):
            data_dir = Path(temp)
            write_scan_status(
                data_dir,
                {
                    "state": "running",
                    "run_id": "run-a",
                    "pid": 123,
                    "started_at": "2026-07-12 22:00:00",
                },
            )
            self.assertTrue(request_scan_stop(data_dir))
            self.assertTrue(scan_stop_requested(data_dir, "run-a"))
            self.assertFalse(scan_stop_requested(data_dir, "run-b"))
            self.assertEqual(read_scan_status(data_dir)["state"], "stop_requested")
            clear_scan_stop(data_dir, "run-b")
            self.assertTrue(scan_stop_requested(data_dir, "run-a"))
            clear_scan_stop(data_dir, "run-a")
            self.assertFalse(scan_stop_requested(data_dir, "run-a"))

    def test_dead_running_process_is_reported_as_interrupted(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "storage_manager.tracking.process_is_alive",
            return_value=False,
        ):
            data_dir = Path(temp)
            write_scan_status(
                data_dir,
                {"state": "running", "run_id": "run-a", "pid": 999999},
            )
            self.assertEqual(read_scan_status(data_dir)["state"], "interrupted")

    def test_next_schedule_rolls_to_tomorrow_after_22(self):
        before = next_scheduled_run(22, datetime(2026, 7, 12, 21, 0, 0))
        after = next_scheduled_run(22, datetime(2026, 7, 12, 22, 1, 0))
        self.assertEqual(before, datetime(2026, 7, 12, 22, 0, 0))
        self.assertEqual(after, datetime(2026, 7, 13, 22, 0, 0))

    def test_cron_status_and_remove_preserve_unmanaged_entry(self):
        existing = (
            "15 1 * * * /other/job\n"
            "0 22 * * * managed # storage-manager-vwp nightly\n"
            "0 7 * * * health # storage-manager-vwp health\n"
        )
        responses = [
            subprocess.CompletedProcess(["crontab", "-l"], 0, existing, ""),
            subprocess.CompletedProcess(["crontab", "-l"], 0, existing, ""),
            subprocess.CompletedProcess(["crontab", "-l"], 0, existing, ""),
            subprocess.CompletedProcess(["crontab", "-"], 0, "", ""),
        ]
        with patch("storage_manager.scheduler.subprocess.run", side_effect=responses) as run_mock:
            status = read_cron_status()
            self.assertTrue(status.available)
            self.assertTrue(status.installed)
            self.assertTrue(remove_cron())
        payload = run_mock.call_args_list[-1].kwargs["input"]
        self.assertIn("/other/job", payload)
        self.assertNotIn("storage-manager-vwp", payload)


if __name__ == "__main__":
    unittest.main()
