import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
    def test_not_configured_is_not_a_failure(self):
        """미지정은 '아직 안 정함'이지 실패가 아니다.

        GUI가 최초 실행 때 한 번 물어보도록 설계했으므로, 설치 중에
        `./run.csh --diagnose`를 돌리는 시점에는 미지정이 정상이다. 여기서
        FAIL을 내면 정상 설치 중인 사용자가 뭔가 잘못된 줄 안다.
        (cron은 데이터 경로가 반드시 필요하지만 setup_cron.csh가 따로 막는다.)
        """

        result = diagnostics.check_data_dir(None)
        self.assertFalse(result["configured"])
        self.assertTrue(result["ok"])
        self.assertIsNone(result["error"])

    def test_unconfigured_data_dir_keeps_overall_ok(self):
        result = diagnostics.run_diagnostics(
            data_dir=None, version_info=(3, 12, 0), include_pyqt5=False
        )
        self.assertTrue(result["ok"])
        self.assertIn("미지정", diagnostics.format_report(result))

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

    def test_missing_gui_toolkit_report_explains_what_still_works(self):
        """'툴킷 사용 불가'와 '종합 OK'가 나란히 찍히면 모순처럼 보인다.

        종합 판정에서 GUI 툴킷을 빼는 것 자체는 의도한 동작이다(수집 전용 CLI는
        Qt 없이도 돌아야 하므로). 대신 무엇이 되고 무엇이 안 되는지 보고서에
        명시되어야 한다.
        """

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                diagnostics,
                "check_gui_toolkit",
                return_value={"available": False, "error": "No module named 'PyQt6'"},
            ):
                result = diagnostics.run_diagnostics(
                    data_dir=Path(tmp) / "data", version_info=(3, 12, 0)
                )
            report = diagnostics.format_report(result)

        self.assertTrue(result["ok"])
        self.assertIn("화면은 띄울 수 없습니다", report)
        self.assertIn("smvwp_cli.py", report)

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
