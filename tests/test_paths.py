import tempfile
import unittest
from pathlib import Path

from smvwp import paths


class ResolveDataDirTests(unittest.TestCase):
    def test_explicit_wins_over_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            explicit = Path(tmp) / "explicit"
            result = paths.resolve_data_dir(
                explicit=str(explicit),
                environ={"STORAGE_MANAGER_DATA_DIR": str(Path(tmp) / "env")},
                home=Path(tmp) / "home",
            )
            self.assertEqual(result, explicit.resolve())

    def test_env_var_used_when_no_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_dir = Path(tmp) / "env"
            result = paths.resolve_data_dir(
                explicit=None,
                environ={"STORAGE_MANAGER_DATA_DIR": str(env_dir)},
                home=Path(tmp) / "home",
            )
            self.assertEqual(result, env_dir.resolve())

    def test_pointer_file_used_when_no_explicit_or_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            remembered = Path(tmp) / "remembered_data"
            paths.remember_data_dir(remembered, home=home)

            result = paths.resolve_data_dir(explicit=None, environ={}, home=home)
            self.assertEqual(result, remembered)

    def test_none_when_nothing_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home_without_pointer"
            result = paths.resolve_data_dir(explicit=None, environ={}, home=home)
            self.assertIsNone(result)


class EnsureWritableTests(unittest.TestCase):
    def test_creates_missing_directory_and_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "data"
            paths.ensure_writable(target)
            self.assertTrue(target.is_dir())

    def test_raises_when_path_is_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "blocked"
            blocked.write_text("i am a file, not a directory", encoding="utf-8")
            with self.assertRaises(paths.DataDirError):
                paths.ensure_writable(blocked)


class AssertNotInsideMonitoredPathsTests(unittest.TestCase):
    def test_ok_for_sibling_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            monitored = Path(tmp) / "user_project_a"
            monitored.mkdir()
            paths.assert_not_inside_monitored_paths(data_dir, [str(monitored)])

    def test_rejects_data_dir_nested_inside_monitored_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitored = Path(tmp) / "user_project_a"
            monitored.mkdir()
            data_dir = monitored / "storage_manager_data"
            data_dir.mkdir()
            with self.assertRaises(paths.DataDirError):
                paths.assert_not_inside_monitored_paths(data_dir, [str(monitored)])

    def test_rejects_monitored_path_nested_inside_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            monitored = data_dir / "user_project_a"
            monitored.mkdir()
            with self.assertRaises(paths.DataDirError):
                paths.assert_not_inside_monitored_paths(data_dir, [str(monitored)])


if __name__ == "__main__":
    unittest.main()
