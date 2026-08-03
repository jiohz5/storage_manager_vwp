import tempfile
import unittest
from pathlib import Path

from smvwp import diagnostics


class CheckPythonVersionTests(unittest.TestCase):
    def test_ok_when_at_or_above_required(self):
        result = diagnostics.check_python_version(version_info=(3, 12, 0))
        self.assertTrue(result["ok"])

    def test_ok_when_above_required_minor(self):
        result = diagnostics.check_python_version(version_info=(3, 13, 1))
        self.assertTrue(result["ok"])

    def test_fails_when_below_required(self):
        result = diagnostics.check_python_version(version_info=(3, 10, 9))
        self.assertFalse(result["ok"])


class CheckModulesTests(unittest.TestCase):
    def test_known_stdlib_modules_ok(self):
        result = diagnostics.check_modules(["json", "sqlite3"])
        self.assertTrue(result["json"]["ok"])
        self.assertTrue(result["sqlite3"]["ok"])

    def test_missing_module_reported_as_failure(self):
        result = diagnostics.check_modules(["this_module_does_not_exist_xyz"])
        self.assertFalse(result["this_module_does_not_exist_xyz"]["ok"])
        self.assertIsNotNone(result["this_module_does_not_exist_xyz"]["error"])


class CheckDataDirTests(unittest.TestCase):
    def test_not_configured_when_none(self):
        result = diagnostics.check_data_dir(None)
        self.assertFalse(result["configured"])
        self.assertFalse(result["ok"])

    def test_ok_when_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = diagnostics.check_data_dir(Path(tmp) / "data")
            self.assertTrue(result["configured"])
            self.assertTrue(result["ok"])

    def test_fails_when_path_is_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "blocked"
            blocked.write_text("x", encoding="utf-8")
            result = diagnostics.check_data_dir(blocked)
            self.assertFalse(result["ok"])


class RunDiagnosticsTests(unittest.TestCase):
    def test_overall_ok_ignores_pyqt5_but_requires_python_modules_and_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = diagnostics.run_diagnostics(
                data_dir=Path(tmp) / "data",
                version_info=(3, 12, 0),
                include_pyqt5=False,
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["pyqt5"]["skipped"])

    def test_overall_fails_when_python_version_too_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = diagnostics.run_diagnostics(
                data_dir=Path(tmp) / "data",
                version_info=(3, 9, 0),
                include_pyqt5=False,
            )
            self.assertFalse(result["ok"])

    def test_format_report_mentions_overall_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = diagnostics.run_diagnostics(
                data_dir=Path(tmp) / "data",
                version_info=(3, 12, 0),
                include_pyqt5=False,
            )
            report = diagnostics.format_report(result)
            self.assertIn("종합 결과", report)


if __name__ == "__main__":
    unittest.main()
