import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from storage_manager.notifier import (
    NOTIFIER_LOCK_FILENAME,
    build_autostart_entry,
    install_autostart,
    notifier_stop_requested,
    read_notifier_status,
    remove_autostart,
    request_notifier_stop,
    run_notifier,
    write_notifier_status,
)
from storage_manager.scheduler import ProcessLock, ScanAlreadyRunning


class NotifierControlTests(unittest.TestCase):
    def test_autostart_entry_uses_absolute_python_script_and_data_paths(self):
        entry = build_autostart_entry(
            PurePosixPath("/opt/python/3.10/bin/python3"),
            PurePosixPath("/opt/storage manager/storage_notifier.py"),
            PurePosixPath("/state/storage manager"),
        )

        self.assertIn(
            'Exec="/opt/python/3.10/bin/python3" '
            '"/opt/storage manager/storage_notifier.py" '
            '--data-dir "/state/storage manager"',
            entry,
        )
        self.assertIn("Terminal=false", entry)
        self.assertIn("X-GNOME-Autostart-enabled=true", entry)

    def test_autostart_install_and_remove_are_user_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            autostart_dir = root / ".config" / "autostart"
            app_dir = root / "app"
            app_dir.mkdir()
            (app_dir / "storage_notifier.py").write_text("# notifier\n", encoding="utf-8")

            installed = install_autostart(
                root / "state",
                python_bin=Path("/opt/python/bin/python3"),
                app_dir=app_dir,
                autostart_dir=autostart_dir,
            )

            self.assertEqual(
                installed,
                autostart_dir / "storage-manager-notifier.desktop",
            )
            self.assertTrue(installed.exists())
            self.assertTrue(remove_autostart(autostart_dir))
            self.assertFalse(installed.exists())
            self.assertFalse(remove_autostart(autostart_dir))

    def test_autostart_rejects_desktop_field_codes(self):
        with self.assertRaises(ValueError):
            build_autostart_entry(
                Path("/opt/python/bin/python3"),
                Path("/app/storage_notifier.py"),
                Path("/state/%u"),
            )

    def test_stop_request_is_bound_to_current_run_id(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "storage_manager.notifier.process_is_notifier",
            return_value=True,
        ):
            data_dir = Path(temp)
            write_notifier_status(
                data_dir,
                {
                    "state": "running",
                    "run_id": "run-a",
                    "pid": 123,
                },
            )

            self.assertTrue(request_notifier_stop(data_dir))
            self.assertTrue(notifier_stop_requested(data_dir, "run-a"))
            self.assertFalse(notifier_stop_requested(data_dir, "run-b"))

    def test_dead_notifier_is_reported_as_interrupted(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "storage_manager.notifier.process_is_notifier",
            return_value=False,
        ):
            data_dir = Path(temp)
            write_notifier_status(
                data_dir,
                {
                    "state": "running",
                    "run_id": "run-a",
                    "pid": 123,
                },
            )

            status = read_notifier_status(data_dir)

            self.assertEqual(status["state"], "interrupted")

    def test_second_notifier_does_not_clear_active_stop_request(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "storage_manager.notifier.process_is_notifier",
            return_value=True,
        ):
            data_dir = Path(temp)
            write_notifier_status(
                data_dir,
                {
                    "state": "running",
                    "run_id": "run-a",
                    "pid": 123,
                },
            )
            self.assertTrue(request_notifier_stop(data_dir))

            with ProcessLock(data_dir / NOTIFIER_LOCK_FILENAME):
                with self.assertRaises(ScanAlreadyRunning):
                    run_notifier(data_dir, "run-b")

            self.assertTrue(notifier_stop_requested(data_dir, "run-a"))


if __name__ == "__main__":
    unittest.main()
