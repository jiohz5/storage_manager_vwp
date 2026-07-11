import subprocess
import unittest
from unittest.mock import patch

import verify_environment


class VerifyEnvironmentTests(unittest.TestCase):
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
