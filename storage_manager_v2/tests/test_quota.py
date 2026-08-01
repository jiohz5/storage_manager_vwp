import subprocess
import unittest
from unittest.mock import patch

from smvwp import quota, tiers
from smvwp.store import SampleRecord
from tests import support


class BuildCommandTests(unittest.TestCase):
    def test_substitutes_account_and_path(self):
        argv = quota.build_command(
            ["/opt/company/bin/quota-json", "{account}", "{path}"], "project_a", "/user/project_a"
        )
        self.assertEqual(argv, ["/opt/company/bin/quota-json", "project_a", "/user/project_a"])

    def test_path_with_braces_is_not_treated_as_format_spec(self):
        """경로에 중괄호가 있어도 서식 지시자로 오해하지 않아야 한다."""

        argv = quota.build_command(["q", "{path}"], "a", "/user/{weird}/data")
        self.assertEqual(argv, ["q", "/user/{weird}/data"])

    def test_special_characters_stay_in_one_argument(self):
        """shell을 쓰지 않으므로 공백/세미콜론이 있어도 인자 하나로 남는다."""

        argv = quota.build_command(["q", "{path}"], "a", "/user/pro ject; rm -rf /")
        self.assertEqual(argv[1], "/user/pro ject; rm -rf /")
        self.assertEqual(len(argv), 2)


class ParseOutputTests(unittest.TestCase):
    def test_parses_used_limit_and_soft_limit(self):
        result = quota.parse_output('{"used_kb": 950000, "limit_kb": 1000000, "soft_limit_kb": 900000}')
        self.assertTrue(result.ok)
        self.assertEqual(result.used_kb, 950000)
        self.assertEqual(result.limit_kb, 1000000)
        self.assertEqual(result.soft_limit_kb, 900000)
        self.assertEqual(result.pct, 95.0)

    def test_zero_limit_does_not_produce_percentage(self):
        """한도가 0이면 나눗셈으로 100%처럼 보이게 만들지 않는다."""

        result = quota.parse_output('{"used_kb": 500, "limit_kb": 0}')
        self.assertTrue(result.ok)
        self.assertIsNone(result.pct)

    def test_missing_limit_keeps_used_only(self):
        result = quota.parse_output('{"used_kb": 500}')
        self.assertTrue(result.ok)
        self.assertEqual(result.used_kb, 500)
        self.assertIsNone(result.pct)

    def test_missing_used_kb_is_an_error(self):
        result = quota.parse_output('{"limit_kb": 100}')
        self.assertFalse(result.ok)

    def test_non_json_output_is_an_error(self):
        result = quota.parse_output("quota: user has no limits")
        self.assertFalse(result.ok)
        self.assertIn("JSON", result.error_message)


class QueryTests(unittest.TestCase):
    @patch("smvwp.quota.subprocess.run")
    def test_successful_query(self, mock_run):
        mock_run.return_value = support.completed(["q"], stdout='{"used_kb": 10, "limit_kb": 100}')
        result = quota.query(["q", "{account}"], "project_a", "/user/project_a")
        self.assertTrue(result.ok)
        self.assertEqual(result.pct, 10.0)

    @patch("smvwp.quota.subprocess.run", side_effect=FileNotFoundError())
    def test_missing_binary_is_reported_not_raised(self, _mock_run):
        result = quota.query(["/missing/q"], "a", "/p")
        self.assertFalse(result.ok)
        self.assertIn("찾을 수 없습니다", result.error_message)

    @patch("smvwp.quota.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="q", timeout=10))
    def test_timeout_is_reported_not_raised(self, _mock_run):
        result = quota.query(["q"], "a", "/p", timeout_seconds=10)
        self.assertFalse(result.ok)

    @patch("smvwp.quota.subprocess.run")
    def test_nonzero_exit_is_reported(self, mock_run):
        mock_run.return_value = support.completed(["q"], returncode=2, stderr="permission denied")
        result = quota.query(["q"], "a", "/p")
        self.assertFalse(result.ok)
        self.assertIn("permission denied", result.error_message)


class TierAndFormatTests(unittest.TestCase):
    def test_tier_uses_same_thresholds_as_capacity(self):
        self.assertEqual(quota.tier_for(quota.QuotaResult(ok=True, pct=96.0)), tiers.ALERT)
        self.assertEqual(quota.tier_for(quota.QuotaResult(ok=True, pct=10.0)), tiers.NORMAL)

    def test_unknown_when_limit_missing_or_failed(self):
        self.assertEqual(quota.tier_for(quota.QuotaResult(ok=True, pct=None)), tiers.UNKNOWN)
        self.assertEqual(quota.tier_for(quota.QuotaResult(ok=False)), tiers.UNKNOWN)

    def test_format_usage_shows_dash_when_not_configured(self):
        self.assertEqual(quota.format_usage(SampleRecord(account_id="a", collected_at="t", ok=True)), "-")

    def test_format_usage_shows_percentage(self):
        sample = SampleRecord(account_id="a", collected_at="t", ok=True, quota_pct=93.4)
        self.assertEqual(quota.format_usage(sample), "93.4%")

    def test_format_usage_shows_used_when_no_limit(self):
        sample = SampleRecord(account_id="a", collected_at="t", ok=True, quota_used_kb=2048)
        self.assertEqual(quota.format_usage(sample), "2,048 KB")


class CollectorIntegrationTests(unittest.TestCase):
    """quota 실패가 df 수집을 막지 않는지 - 이게 핵심 불변식.

    주의: `smvwp.quota.subprocess`와 `smvwp.collector.subprocess`는 같은 표준
    모듈 객체다. 둘을 각각 patch하면 나중 patch가 앞의 것을 덮어써서 엉뚱한
    가짜 결과가 섞인다 (tests/test_nightly_scan.py에도 같은 주석이 있다).
    그래서 patch는 한 번만 걸고, 명령을 보고 df인지 quota인지 분기한다.
    """

    def _make_runner(self, quota_behavior):
        def runner(command, **kwargs):
            if "df" in command:
                if "-Pi" in command:
                    stdout = (
                        "Filesystem Inodes IUsed IFree IUse% Mounted on\n"
                        "/dev/sda1 1000 100 900 10% /user\n"
                    )
                else:
                    stdout = (
                        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                        "/dev/sda1 1000 500 500 50% /user\n"
                    )
                return support.completed(command, stdout=stdout)
            return quota_behavior(command)

        return runner

    def _collect(self, quota_behavior, quota_command):
        from smvwp.collector import collect_account
        from smvwp.config import Account

        with patch("smvwp.collector.subprocess.run", side_effect=self._make_runner(quota_behavior)):
            return collect_account(
                Account(name="a", path="/user/a", account_id="id-1"), quota_command=quota_command
            )

    def test_quota_failure_keeps_df_results(self):
        def failing_quota(command):
            raise FileNotFoundError()

        record = self._collect(failing_quota, ["/missing/q"])

        self.assertTrue(record.ok)
        self.assertEqual(record.byte_pct, 50.0)  # df 결과는 그대로 살아 있어야 한다
        self.assertIsNone(record.quota_pct)
        self.assertEqual(record.quota_tier, tiers.UNKNOWN)

    def test_quota_tier_can_raise_overall_tier(self):
        def near_limit_quota(command):
            return support.completed(command, stdout='{"used_kb": 99, "limit_kb": 100}')

        record = self._collect(near_limit_quota, ["q"])

        # df는 50%(정상)이지만 quota가 99% -> 종합 등급은 긴급이어야 한다.
        self.assertEqual(record.byte_tier, tiers.NORMAL)
        self.assertEqual(record.quota_pct, 99.0)
        self.assertEqual(record.overall_tier, tiers.EMERGENCY)

    def test_no_quota_command_skips_quota_entirely(self):
        def should_not_run(command):
            raise AssertionError(f"quota 명령이 실행되면 안 된다: {command}")

        record = self._collect(should_not_run, [])
        self.assertTrue(record.ok)
        self.assertEqual(record.overall_tier, tiers.NORMAL)


if __name__ == "__main__":
    unittest.main()
