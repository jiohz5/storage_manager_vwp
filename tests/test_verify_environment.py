import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_environment
import make_bundle
from make_bundle import RUNTIME_FILES


class VerifyEnvironmentTests(unittest.TestCase):
    def test_python_310_is_the_minimum_runtime(self):
        self.assertEqual(verify_environment.MIN_PYTHON, (3, 10, 0))

    def test_runtime_manifest_contains_capacity_and_notifier_entry_points(self):
        self.assertIn("capacity_watch.py", RUNTIME_FILES)
        self.assertIn("storage_notifier.py", RUNTIME_FILES)
        self.assertIn("runtime_check.py", RUNTIME_FILES)

    def test_source_bundle_contains_runtime_check_but_no_runtime_state(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "storage-manager.tar.gz"
            checksum = Path(temp) / "storage-manager.tar.gz.sha256"
            with patch.object(make_bundle, "OUTPUT", output), patch.object(
                make_bundle,
                "CHECKSUM",
                checksum,
            ):
                make_bundle.main()
            with tarfile.open(output, "r:gz") as archive:
                names = archive.getnames()

        self.assertIn("storage_manager_vwp/runtime_check.py", names)
        self.assertIn("storage_manager_vwp/storage_manager/runtime.py", names)
        self.assertIn("storage_manager_vwp/storage_manager/admin_auth.py", names)
        self.assertIn("storage_manager_vwp/storage_manager/search_index.py", names)
        self.assertIn("storage_manager_vwp/tests/test_admin_auth.py", names)
        self.assertIn("storage_manager_vwp/tests/test_search_index.py", names)
        forbidden = (
            "location.json",
            "runtime_diagnostics.json",
            "accounts.json",
            "storage_manager.db",
            "search_index.db",
            "search_index.db-journal",
            "search_index.db-wal",
            "search_index.db-shm",
            "search_scan_tasks",
            ".log",
            "__pycache__",
        )
        self.assertFalse(any(token in name for name in names for token in forbidden))

    def test_search_database_has_an_explicit_ignore_rule(self):
        ignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(
            encoding="utf-8"
        )
        self.assertIn("search_index.db*", ignore.splitlines())

    def test_bundle_validator_rejects_search_runtime_state(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "unsafe.tar.gz"
            payload = Path(temp) / "search_index.db"
            payload.write_bytes(b"indexed project path")
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(
                    payload,
                    arcname="storage_manager_vwp/data/search_index.db",
                )

            validator = getattr(make_bundle, "validate_source_archive", None)
            self.assertTrue(callable(validator))
            with self.assertRaises(ValueError):
                validator(archive_path)

    def test_autostart_check_reports_writable_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            result = verify_environment.check_autostart_directory(Path(temp))
        self.assertEqual(result.level, "OK")
        self.assertIn("autostart", result.message.lower())

    def test_compact_error_and_human_bytes(self):
        self.assertEqual(verify_environment._compact_error("a\n b"), "a b")
        self.assertEqual(verify_environment._human_bytes(1024 * 1024), "1.0 MB")

    @patch("verify_environment.subprocess.run")
    @patch("verify_environment.shutil.which", return_value="/usr/bin/findmnt")
    def test_network_filesystem_is_warning(self, which_mock, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["findmnt"],
            0,
            stdout="nfs4 server:/storage\n",
            stderr="",
        )
        result = verify_environment._filesystem_check(verify_environment.Path("/data"))
        self.assertEqual(result.level, "WARN")
        self.assertIn("do not share SQLite", result.message)

    @patch("verify_environment.subprocess.run")
    def test_qt_platform_probe_failure_is_reported(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["python"],
            1,
            stdout="",
            stderr="platform plugin xcb failed",
        )
        result = verify_environment._qt_platform_check()
        self.assertEqual(result.level, "FAIL")
        self.assertIn("xcb", result.message)

    @patch("verify_environment.subprocess.run")
    def test_qt_probe_requires_a_korean_font(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["python"],
            0,
            stdout="xcb|DejaVu Sans|0\n",
            stderr="",
        )
        result = verify_environment._qt_platform_check()
        self.assertEqual(result.level, "FAIL")
        self.assertIn("cannot render Korean", result.message)


if __name__ == "__main__":
    unittest.main()
