import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storage_manager.runtime import (
    MIN_PYTHON,
    RuntimePathError,
    collect_runtime_diagnostics,
    config_location_file,
    directory_size_bytes,
    inspect_data_directory,
    read_saved_data_dir,
    resolve_data_dir,
    save_data_dir_location,
    same_filesystem,
    suggested_data_dir,
)


class RuntimePathTests(unittest.TestCase):
    def test_same_filesystem_compares_device_ids(self):
        class Stat:
            def __init__(self, device):
                self.st_dev = device

        data = Path("/data")
        project_a = Path("/project-a")
        project_b = Path("/project-b")
        values = {str(data): Stat(7), str(project_a): Stat(7), str(project_b): Stat(8)}
        reader = lambda value: values[str(value)]
        self.assertTrue(same_filesystem(data, project_a, reader))
        self.assertFalse(same_filesystem(data, project_b, reader))

    def test_csh_launchers_use_explicit_python_and_runtime_preflight(self):
        root = Path(__file__).resolve().parent.parent
        for name in ("run.csh", "setup_cron.csh"):
            source = (root / name).read_text(encoding="utf-8")
            self.assertIn("STORAGE_MANAGER_PYTHON_BIN", source)
            self.assertIn("STORAGE_MANAGER_PYTHON_HOME", source)
            self.assertIn("unsetenv PYTHONHOME", source)
            self.assertNotIn('$PYTHONHOME/bin/python3', source)
            self.assertIn("runtime_check.py", source)
            self.assertIn("--allow-missing", source)
            self.assertNotIn("2> /dev/null", source)
        self.assertIn("--diagnose", (root / "run.csh").read_text(encoding="utf-8"))
        self.assertIn(
            "--resolve-data-dir",
            (root / "setup_cron.csh").read_text(encoding="utf-8"),
        )

    def test_data_path_resolution_precedence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            saved = root / "saved"
            environment = root / "environment"
            explicit = root / "explicit"
            save_data_dir_location(saved, home=home, environ={})

            self.assertEqual(
                resolve_data_dir(home=home, environ={}),
                saved.resolve(),
            )
            self.assertEqual(
                resolve_data_dir(
                    home=home,
                    environ={"STORAGE_MANAGER_DATA_DIR": str(environment)},
                ),
                environment.resolve(),
            )
            self.assertEqual(
                resolve_data_dir(
                    explicit,
                    home=home,
                    environ={"STORAGE_MANAGER_DATA_DIR": str(environment)},
                ),
                explicit.resolve(),
            )

    def test_saved_pointer_is_atomic_utf8_and_below_one_kb(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            data_dir = root / "프로젝트 데이터"
            pointer = save_data_dir_location(data_dir, home=home, environ={})

            self.assertEqual(pointer, config_location_file(home=home, environ={}))
            self.assertLess(pointer.stat().st_size, 1024)
            self.assertEqual(read_saved_data_dir(home=home, environ={}), data_dir.resolve())
            self.assertEqual(
                json.loads(pointer.read_text(encoding="utf-8"))["data_dir"],
                str(data_dir.resolve()),
            )

    def test_directory_probe_checks_file_and_sqlite_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "state"
            status = inspect_data_directory(data_dir)

            self.assertTrue(status.writable)
            self.assertTrue(status.sqlite_writable)
            self.assertEqual(status.path, data_dir.resolve())
            self.assertGreaterEqual(status.free_bytes, 0)
            self.assertFalse(any(data_dir.glob(".storage-manager-probe-*")))

    def test_directory_probe_can_skip_recursive_size_measurement(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "storage_manager.runtime.directory_size_bytes"
        ) as size_reader:
            status = inspect_data_directory(
                Path(temp) / "state",
                measure_size=False,
            )

        size_reader.assert_not_called()
        self.assertEqual(status.size_bytes, 0)

    def test_data_dir_suggestion_uses_user_directory_when_available(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            user_root = root / "user"
            home = root / "home"
            (user_root / "tester").mkdir(parents=True)

            self.assertEqual(
                suggested_data_dir("tester", home, user_root=user_root),
                user_root / "tester" / ".storage-manager-vwp",
            )
            self.assertEqual(
                suggested_data_dir("missing", home, user_root=user_root),
                home / ".storage-manager-vwp",
            )

    def test_invalid_data_path_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as temp:
            blocker = Path(temp) / "not-a-directory"
            blocker.write_text("x", encoding="ascii")

            with self.assertRaisesRegex(
                RuntimePathError,
                "STORAGE_MANAGER_DATA_DIR",
            ):
                inspect_data_directory(blocker / "state")

    def test_directory_size_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "state"
            external = root / "external"
            state.mkdir()
            external.mkdir()
            (state / "local.bin").write_bytes(b"a" * 1024)
            (external / "large.bin").write_bytes(b"b" * 1024 * 1024)
            try:
                os.symlink(str(external), str(state / "linked"), target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")

            self.assertLess(directory_size_bytes(state), 4096)

    def test_runtime_diagnostics_report_supported_python_and_modules(self):
        payload = collect_runtime_diagnostics()

        self.assertGreaterEqual(tuple(payload["python"]["version_info"]), MIN_PYTHON)
        self.assertTrue(payload["python"]["supported"])
        self.assertTrue(payload["json"]["available"])
        self.assertTrue(payload["sqlite"]["available"])
        self.assertIn("executable", payload["python"])


if __name__ == "__main__":
    unittest.main()
