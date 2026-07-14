import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import storage_manager.tracking as tracking
from storage_manager.scheduler import install_cron, read_cron_status, remove_cron
from storage_manager.tracking import (
    clear_scan_stop,
    launch_background_scan,
    next_capacity_run,
    next_scheduled_run,
    read_scan_status,
    request_scan_stop,
    scan_stop_requested,
    write_scan_status,
)


class TrackingTests(unittest.TestCase):
    def test_low_priority_prefix_uses_available_posix_tools_and_falls_back(self):
        builder = getattr(tracking, "low_priority_prefix", None)
        self.assertTrue(callable(builder))
        tools = {
            "nice": "/usr/bin/nice",
            "ionice": "/usr/bin/ionice",
        }
        self.assertEqual(
            builder(os_name="posix", which=tools.get),
            [
                "/usr/bin/nice",
                "-n",
                "10",
                "/usr/bin/ionice",
                "-c",
                "2",
                "-n",
                "7",
            ],
        )
        self.assertEqual(builder(os_name="posix", which=lambda _name: None), [])
        self.assertEqual(builder(os_name="nt", which=tools.get), [])

    def test_gui_background_scan_uses_low_priority_prefix(self):
        prefix = ["/usr/bin/nice", "-n", "10"]
        with tempfile.TemporaryDirectory() as temp, patch(
            "storage_manager.tracking.low_priority_prefix",
            return_value=prefix,
            create=True,
        ), patch("storage_manager.tracking.subprocess.Popen") as popen:
            popen.return_value.pid = 4321
            pid = launch_background_scan(Path(temp))

        self.assertEqual(pid, 4321)
        command = popen.call_args.args[0]
        self.assertEqual(command[: len(prefix)], prefix)
        self.assertIn("nightly_scan.py", " ".join(command))

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

    def test_next_capacity_run_uses_managed_quarter_schedule(self):
        self.assertEqual(
            next_capacity_run(datetime(2026, 7, 12, 10, 8, 0)),
            datetime(2026, 7, 12, 10, 22, 0),
        )
        self.assertEqual(
            next_capacity_run(datetime(2026, 7, 12, 10, 52, 0)),
            datetime(2026, 7, 12, 11, 7, 0),
        )

    def test_cron_status_and_remove_preserve_unmanaged_entry(self):
        existing = (
            "15 1 * * * /other/job\n"
            "0 22 * * * managed # storage-manager-vwp nightly\n"
            "7,22,37,52 * * * * capacity # storage-manager-vwp capacity\n"
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
            self.assertTrue(status.capacity_installed)
            self.assertTrue(remove_cron())
        payload = run_mock.call_args_list[-1].kwargs["input"]
        self.assertIn("/other/job", payload)
        self.assertNotIn("storage-manager-vwp", payload)

    def test_remove_cron_does_not_overwrite_when_reread_fails(self):
        existing = (
            "15 1 * * * /other/job\n"
            "0 22 * * * managed # storage-manager-vwp nightly\n"
        )
        responses = [
            subprocess.CompletedProcess(["crontab", "-l"], 0, existing, ""),
            subprocess.CompletedProcess(
                ["crontab", "-l"],
                1,
                "",
                "temporary crontab read failure",
            ),
            subprocess.CompletedProcess(["crontab", "-"], 0, "", ""),
        ]
        with patch(
            "storage_manager.scheduler.subprocess.run",
            side_effect=responses,
        ) as run_mock:
            with self.assertRaisesRegex(RuntimeError, "temporary crontab read failure"):
                remove_cron()

        self.assertEqual(run_mock.call_count, 2)
        self.assertNotIn(
            ["crontab", "-"],
            [call.args[0] for call in run_mock.call_args_list],
        )

    def test_install_cron_does_not_overwrite_when_read_fails(self):
        responses = [
            subprocess.CompletedProcess(
                ["crontab", "-l"],
                1,
                "",
                "temporary crontab read failure",
            ),
            subprocess.CompletedProcess(["crontab", "-"], 0, "", ""),
        ]
        with tempfile.TemporaryDirectory() as temp, patch(
            "storage_manager.scheduler.subprocess.run",
            side_effect=responses,
        ) as run_mock:
            with self.assertRaisesRegex(RuntimeError, "temporary crontab read failure"):
                install_cron(Path(temp), "/python/bin/python3")

        self.assertEqual(run_mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
