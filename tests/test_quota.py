import subprocess
import unittest
from unittest.mock import patch

from storage_manager.quota import collect_quota


class QuotaTests(unittest.TestCase):
    @patch("storage_manager.quota.subprocess.run")
    def test_quota_command_substitutes_without_shell_and_parses_json(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(
            ["quota-json"], 0, '{"used_kb": 950, "limit_kb": 1000}', ""
        )
        result = collect_quota(
            ["/opt/quota-json", "{account}", "{path}"],
            "project_a",
            "/user/project_a",
            10,
        )
        self.assertEqual(result.use_pct, 95)
        self.assertEqual(
            run_mock.call_args.args[0],
            ["/opt/quota-json", "project_a", "/user/project_a"],
        )
        self.assertNotIn("shell", run_mock.call_args.kwargs)

    def test_empty_quota_command_is_disabled(self):
        self.assertIsNone(collect_quota([], "a", "/a", 10))


if __name__ == "__main__":
    unittest.main()
