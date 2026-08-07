"""수집이 실제로 돌고 있는지 감시.

가장 중요한 시나리오: cron이 조용히 안 도는데 사용자가 GUI를 열면 시작하자마자
한 번 수집돼 "방금 수집됨"이 된다. 마지막 수집 시각만 보면 늘 최신이라
"그동안 cron이 안 돌았다"는 사실이 영영 드러나지 않는다. 커버리지를 함께 보는
이유가 이것이고, 아래 테스트가 그걸 고정한다.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smvwp import config as config_module
from smvwp import freshness, store, tiers
from smvwp.store import SampleRecord

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def _sample(minutes_ago, ok=True):
    return SampleRecord(
        account_id="acct-1",
        collected_at=(NOW - timedelta(minutes=minutes_ago)).isoformat(),
        ok=ok,
        filesystem="/dev/sda1",
        total_kb=1000,
        used_kb=500,
        avail_kb=500,
        byte_pct=50.0,
        overall_tier=tiers.NORMAL,
    )


def _every_15_min(count):
    return [_sample(15 * i) for i in range(count)]


class EvaluateAccountTests(unittest.TestCase):
    def setUp(self):
        self.settings = config_module.Settings()

    def _evaluate(self, samples):
        return freshness.evaluate_account("acct-1", samples, self.settings, NOW)

    def test_no_samples_is_never(self):
        result = self._evaluate([])
        self.assertEqual(result.status, freshness.STATUS_NEVER)
        self.assertTrue(result.needs_attention)

    def test_regular_collection_is_ok(self):
        result = self._evaluate(_every_15_min(96))  # 24시간치
        self.assertEqual(result.status, freshness.STATUS_OK)
        self.assertFalse(result.needs_attention)
        self.assertGreaterEqual(result.coverage_pct, 95)

    def test_stopped_collection_is_stale(self):
        # 마지막 수집이 3시간 전 (간격 15분 x 4배 = 1시간을 초과)
        result = self._evaluate([_sample(180), _sample(195), _sample(210)])
        self.assertEqual(result.status, freshness.STATUS_STALE)

    def test_a_couple_of_missed_runs_is_not_stale(self):
        """cron은 몇 분씩 밀린다. 한두 번 놓친 것으로 경고하면 안 된다."""

        samples = _every_15_min(96)
        samples[0] = _sample(45)  # 최근 두 번 걸렀음
        result = self._evaluate(samples)
        self.assertEqual(result.status, freshness.STATUS_OK)

    def test_gui_only_collection_is_detected_as_gappy(self):
        """이 테스트가 이 기능의 존재 이유다.

        cron이 죽고 사용자가 가끔 GUI만 열면, 마지막 수집은 '방금'이라
        지연으로는 안 잡힌다. 커버리지가 이를 드러내야 한다.
        """

        samples = [_sample(0), _sample(20 * 60), _sample(44 * 60)]
        result = self._evaluate(samples)

        self.assertEqual(result.status, freshness.STATUS_GAPPY)
        self.assertLess(result.age_seconds, 60)  # 최신 표본은 방금 것
        self.assertLess(result.coverage_pct, 10)

    def test_freshly_registered_account_is_not_flagged(self):
        """막 등록해 표본이 몇 개뿐인 계정을 지연으로 몰면 안 된다."""

        result = self._evaluate(_every_15_min(3))
        self.assertEqual(result.status, freshness.STATUS_OK)

    def test_failed_samples_still_count_as_collection_attempts(self):
        """여기서 보는 것은 값의 정상 여부가 아니라 수집기가 도는지다."""

        result = self._evaluate([_sample(15 * i, ok=False) for i in range(96)])
        self.assertEqual(result.status, freshness.STATUS_OK)

    def test_future_timestamps_are_ignored(self):
        samples = _every_15_min(96) + [_sample(-600)]  # 10시간 뒤 표본
        result = self._evaluate(samples)
        self.assertEqual(result.status, freshness.STATUS_OK)
        self.assertGreaterEqual(result.age_seconds, 0)

    def test_malformed_timestamp_does_not_crash(self):
        broken = _sample(0)
        broken.collected_at = "not-a-timestamp"
        result = self._evaluate([broken] + _every_15_min(96))
        self.assertEqual(result.status, freshness.STATUS_OK)

    def test_disabled_by_wider_coverage_threshold(self):
        """임계치를 낮추면 같은 데이터가 정상으로 판정되어야 한다."""

        samples = [_sample(0), _sample(20 * 60), _sample(44 * 60)]
        self.assertEqual(self._evaluate(samples).status, freshness.STATUS_GAPPY)

        self.settings.freshness_min_coverage_pct = 0
        self.assertEqual(self._evaluate(samples).status, freshness.STATUS_OK)


class EvaluateAllTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data_dir = root / "data"
        account_path = root / "acct"
        account_path.mkdir(parents=True)

        self.config = config_module.load_config(self.data_dir)
        self.account = config_module.add_account(
            self.config, "project_a", str(account_path), data_dir=self.data_dir
        )
        config_module.save_config(self.data_dir, self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def _store(self, samples):
        conn = store.connect(self.data_dir)
        try:
            for sample in samples:
                sample.account_id = self.account.account_id
                store.insert_sample(conn, sample)
        finally:
            conn.close()

    def test_reports_per_account_status(self):
        self._store(_every_15_min(96))
        results = freshness.evaluate_all(self.data_dir, self.config, now=NOW)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].account_id, self.account.account_id)
        self.assertEqual(results[0].status, freshness.STATUS_OK)

    def test_account_without_samples_is_never(self):
        results = freshness.evaluate_all(self.data_dir, self.config, now=NOW)
        self.assertEqual(results[0].status, freshness.STATUS_NEVER)


class FormatAgeTests(unittest.TestCase):
    def test_readable_forms(self):
        self.assertIn("방금", freshness.format_age(30))
        self.assertIn("5", freshness.format_age(5 * 60))
        self.assertIn("3", freshness.format_age(3 * 3600))
        self.assertIn("2", freshness.format_age(2 * 86400))

    def test_none_is_dash(self):
        self.assertEqual(freshness.format_age(None), "-")


if __name__ == "__main__":
    unittest.main()
