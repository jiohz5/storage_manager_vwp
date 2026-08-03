"""FULL 도달 예측과 급증 판정.

이 파일이 지키는 핵심은 "모르면 모른다고 말한다"이다. 표본이 모자라거나,
줄고 있거나, 예상이 터무니없이 먼 미래면 숫자를 만들어내지 않는다.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smvwp import analytics
from smvwp import config as config_module
from smvwp import forecast_notify, notifications, store, tiers
from smvwp.store import SampleRecord

GB_KB = 1024 * 1024
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def _sample(minutes_ago, used_kb, total_kb=1000 * GB_KB, ok=True, filesystem="/dev/sda1"):
    collected = NOW - timedelta(minutes=minutes_ago)
    return SampleRecord(
        account_id="acct-1",
        collected_at=collected.isoformat(),
        ok=ok,
        filesystem=filesystem,
        total_kb=total_kb,
        used_kb=used_kb,
        avail_kb=total_kb - used_kb,
        byte_pct=round(used_kb / total_kb * 100, 1),
        overall_tier=tiers.NORMAL,
    )


def _series(count, start_used_kb, step_kb, interval_minutes=15):
    """오래된 것부터 일정하게 증가하는 표본열."""

    samples = []
    for index in range(count):
        minutes_ago = (count - 1 - index) * interval_minutes
        samples.append(_sample(minutes_ago, start_used_kb + step_kb * index))
    return samples


class LinearSlopeTests(unittest.TestCase):
    def test_perfect_line(self):
        points = [(0.0, 0.0), (1.0, 10.0), (2.0, 20.0)]
        self.assertAlmostEqual(analytics.linear_slope(points), 10.0)

    def test_noise_does_not_dominate(self):
        """두 점만 보면 마지막 표본의 튐에 휘둘리지만 회귀는 견딘다."""

        steady = [(float(i), float(i * 10)) for i in range(10)]
        steady[-1] = (9.0, 0.0)  # 마지막에 큰 파일이 지워진 상황
        slope = analytics.linear_slope(steady)
        self.assertGreater(slope, 0)  # 여전히 증가 추세로 본다

    def test_single_point_is_none(self):
        self.assertIsNone(analytics.linear_slope([(1.0, 1.0)]))

    def test_identical_x_is_none(self):
        self.assertIsNone(analytics.linear_slope([(1.0, 1.0), (1.0, 2.0)]))


class PredictTests(unittest.TestCase):
    def test_steady_growth_predicts_full(self):
        # 15분마다 1GB씩 = 4GB/시간. 남은 공간으로 도달 시간이 나와야 한다.
        samples = _series(12, start_used_kb=900 * GB_KB, step_kb=1 * GB_KB)
        result = analytics.predict(samples, NOW, window_hours=3, min_samples=4)

        self.assertTrue(result.ok)
        self.assertAlmostEqual(result.slope_kb_per_hour, 4 * GB_KB, delta=1000)
        # 마지막 시점 여유 = 1000 - 911 = 89GB, 4GB/h -> 약 22시간
        self.assertAlmostEqual(result.hours_to_full, 22.25, delta=0.5)

    def test_insufficient_samples(self):
        samples = _series(3, start_used_kb=900 * GB_KB, step_kb=1 * GB_KB)
        result = analytics.predict(samples, NOW, window_hours=3, min_samples=4)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, analytics.REASON_INSUFFICIENT)
        self.assertIsNone(result.hours_to_full)

    def test_shrinking_is_not_predicted(self):
        samples = _series(12, start_used_kb=900 * GB_KB, step_kb=-1 * GB_KB)
        result = analytics.predict(samples, NOW, window_hours=3, min_samples=4)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, analytics.REASON_NOT_GROWING)

    def test_flat_is_not_predicted(self):
        samples = _series(12, start_used_kb=900 * GB_KB, step_kb=0)
        result = analytics.predict(samples, NOW, window_hours=3, min_samples=4)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, analytics.REASON_NOT_GROWING)

    def test_negligible_growth_is_too_far(self):
        """기울기가 미미하면 산술적으로 수만 년이 나오는데 이건 예측이 아니다."""

        samples = _series(12, start_used_kb=1 * GB_KB, step_kb=1)  # 15분에 1KB
        result = analytics.predict(samples, NOW, window_hours=3, min_samples=4, max_years=10)

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, analytics.REASON_TOO_FAR)

    def test_samples_outside_window_are_ignored(self):
        old = _series(12, start_used_kb=100 * GB_KB, step_kb=50 * GB_KB, interval_minutes=15)
        # 창 밖(10일 전)으로 밀어낸다.
        old = [
            SampleRecord(**{**vars(s), "collected_at": (NOW - timedelta(days=10)).isoformat()})
            for s in old
        ]
        result = analytics.predict(old, NOW, window_hours=3, min_samples=4)
        self.assertEqual(result.reason, analytics.REASON_INSUFFICIENT)

    def test_failed_samples_are_ignored(self):
        samples = _series(12, start_used_kb=900 * GB_KB, step_kb=1 * GB_KB)
        for sample in samples[:9]:
            sample.ok = False
        result = analytics.predict(samples, NOW, window_hours=3, min_samples=4)
        self.assertEqual(result.reason, analytics.REASON_INSUFFICIENT)

    def test_future_samples_are_ignored(self):
        """시계가 어긋난 표본이 섞여도 예측을 오염시키지 않아야 한다."""

        samples = _series(12, start_used_kb=900 * GB_KB, step_kb=1 * GB_KB)
        future = _sample(-600, 999 * GB_KB)  # 10시간 뒤
        result = analytics.predict(samples + [future], NOW, window_hours=3, min_samples=4)
        self.assertTrue(result.ok)
        self.assertEqual(result.sample_count, 12)


class SurgeTests(unittest.TestCase):
    def test_measures_actual_increase(self):
        samples = _series(12, start_used_kb=100 * GB_KB, step_kb=10 * GB_KB)
        surge = analytics.measure_surge(samples, NOW, window_hours=3)
        self.assertEqual(surge, 110 * GB_KB)

    def test_needs_two_samples(self):
        self.assertIsNone(analytics.measure_surge(_series(1, 100 * GB_KB, 0), NOW, 3))

    def test_decrease_is_negative(self):
        samples = _series(12, start_used_kb=200 * GB_KB, step_kb=-5 * GB_KB)
        self.assertLess(analytics.measure_surge(samples, NOW, window_hours=3), 0)


class ForecastGroupingTests(unittest.TestCase):
    def test_same_filesystem_is_grouped(self):
        a = analytics.CapacityForecast(account_id="a", filesystem="/dev/sda1")
        b = analytics.CapacityForecast(account_id="b", filesystem="/dev/sda1")
        c = analytics.CapacityForecast(account_id="c", filesystem="/dev/sdb1")

        grouped = analytics.group_by_filesystem([a, b, c])
        self.assertEqual(len(grouped), 2)
        self.assertEqual(len(grouped["/dev/sda1"]), 2)

    def test_unknown_filesystem_stays_separate(self):
        """파일시스템을 모를 때 임의로 묶으면 잘못된 중복 제거가 된다."""

        a = analytics.CapacityForecast(account_id="a", filesystem=None)
        b = analytics.CapacityForecast(account_id="b", filesystem=None)
        grouped = analytics.group_by_filesystem([a, b])
        self.assertEqual(len(grouped), 2)


class ForecastTierTests(unittest.TestCase):
    def setUp(self):
        self.settings = config_module.Settings()

    def test_two_hours_is_emergency(self):
        self.assertEqual(forecast_notify.forecast_tier(1.5, self.settings), tiers.EMERGENCY)

    def test_six_hours_is_alert(self):
        self.assertEqual(forecast_notify.forecast_tier(5.0, self.settings), tiers.ALERT)

    def test_beyond_warn_window_is_silent(self):
        self.assertIsNone(forecast_notify.forecast_tier(48.0, self.settings))

    def test_boundaries_are_inclusive(self):
        self.assertEqual(forecast_notify.forecast_tier(2.0, self.settings), tiers.EMERGENCY)
        self.assertEqual(forecast_notify.forecast_tier(6.0, self.settings), tiers.ALERT)


class ForecastNotifyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.account_path = self.root / "acct"
        self.account_path.mkdir(parents=True)

        self.config = config_module.load_config(self.data_dir)
        self.account = config_module.add_account(
            self.config, "project_a", str(self.account_path), data_dir=self.data_dir
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

    def _events(self, kind):
        outbox = notifications.outbox_dir(self.data_dir)
        if not outbox.exists():
            return []
        events = [json.loads(p.read_text(encoding="utf-8")) for p in outbox.glob("*.json")]
        return [e for e in events if e.get("kind") == kind]

    def test_imminent_full_triggers_alert(self):
        # 3시간 창에서 15분마다 10GB -> 40GB/h. 여유 50GB -> 약 1.2시간 -> 긴급
        self._store(_series(12, start_used_kb=940 * GB_KB, step_kb=10 * GB_KB))
        forecasts = forecast_notify.build_forecasts(self.data_dir, self.config, now=NOW)
        sent = forecast_notify.notify_forecasts(self.data_dir, self.config, forecasts, now=NOW)

        self.assertGreaterEqual(sent, 1)
        events = self._events(notifications.KIND_FULL_FORECAST)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["tier"], tiers.EMERGENCY)
        self.assertIn("project_a", events[0]["details"]["accounts"][0]["name"])

    def test_slow_growth_does_not_alert(self):
        # 15분마다 1MB -> FULL까지 수년. 경고 기준 밖.
        self._store(_series(12, start_used_kb=100 * GB_KB, step_kb=1024))
        forecasts = forecast_notify.build_forecasts(self.data_dir, self.config, now=NOW)
        forecast_notify.notify_forecasts(self.data_dir, self.config, forecasts, now=NOW)

        self.assertEqual(self._events(notifications.KIND_FULL_FORECAST), [])

    def test_surge_alert_on_large_increase(self):
        # 3시간 동안 120GB 증가 -> 100GB 임계 초과
        self._store(_series(12, start_used_kb=100 * GB_KB, step_kb=11 * GB_KB))
        forecasts = forecast_notify.build_forecasts(self.data_dir, self.config, now=NOW)
        forecast_notify.notify_forecasts(self.data_dir, self.config, forecasts, now=NOW)

        events = self._events(notifications.KIND_SURGE)
        self.assertEqual(len(events), 1)
        self.assertGreaterEqual(events[0]["details"]["delta_kb"], 100 * GB_KB)

    def test_shared_filesystem_alerts_once(self):
        """같은 파일시스템의 계정이 여럿이어도 알림은 1건이어야 한다."""

        second = config_module.add_account(
            self.config, "project_b", str(self.root / "acct2"), require_exists=False
        )
        config_module.save_config(self.data_dir, self.config)

        conn = store.connect(self.data_dir)
        try:
            for sample in _series(12, start_used_kb=940 * GB_KB, step_kb=10 * GB_KB):
                for account_id in (self.account.account_id, second.account_id):
                    record = SampleRecord(**{**vars(sample), "account_id": account_id})
                    store.insert_sample(conn, record)
        finally:
            conn.close()

        forecasts = forecast_notify.build_forecasts(self.data_dir, self.config, now=NOW)
        forecast_notify.notify_forecasts(self.data_dir, self.config, forecasts, now=NOW)

        events = self._events(notifications.KIND_FULL_FORECAST)
        self.assertEqual(len(events), 1)
        # 관련 계정은 둘 다 실려야 한다.
        names = {a["name"] for a in events[0]["details"]["accounts"]}
        self.assertEqual(names, {"project_a", "project_b"})

    def test_cooldown_suppresses_repeat(self):
        self._store(_series(12, start_used_kb=940 * GB_KB, step_kb=10 * GB_KB))
        forecasts = forecast_notify.build_forecasts(self.data_dir, self.config, now=NOW)
        forecast_notify.notify_forecasts(self.data_dir, self.config, forecasts, now=NOW)
        forecast_notify.notify_forecasts(
            self.data_dir, self.config, forecasts, now=NOW + timedelta(minutes=5)
        )
        self.assertEqual(len(self._events(notifications.KIND_FULL_FORECAST)), 1)

    def test_recovery_clears_state_for_immediate_realert(self):
        """정상으로 돌아왔다가 다시 임박하면 cooldown 없이 즉시 알려야 한다."""

        self._store(_series(12, start_used_kb=940 * GB_KB, step_kb=10 * GB_KB))
        forecasts = forecast_notify.build_forecasts(self.data_dir, self.config, now=NOW)
        forecast_notify.notify_forecasts(self.data_dir, self.config, forecasts, now=NOW)

        state = notifications.load_notify_state(self.data_dir)
        key = notifications.forecast_state_key(
            notifications.KIND_FULL_FORECAST, "/dev/sda1"
        )
        self.assertIn(key, state)

        # 증가가 멈춘 상황 -> 상태가 지워져야 한다.
        calm = [analytics.CapacityForecast(account_id=self.account.account_id, filesystem="/dev/sda1")]
        forecast_notify.notify_forecasts(
            self.data_dir, self.config, calm, now=NOW + timedelta(hours=1)
        )
        self.assertNotIn(key, notifications.load_notify_state(self.data_dir))

    def test_disabled_prediction_sends_nothing(self):
        self.config.settings.full_prediction_enabled = False
        self._store(_series(12, start_used_kb=940 * GB_KB, step_kb=10 * GB_KB))
        forecasts = forecast_notify.build_forecasts(self.data_dir, self.config, now=NOW)
        sent = forecast_notify.notify_forecasts(self.data_dir, self.config, forecasts, now=NOW)
        self.assertEqual(sent, 0)


if __name__ == "__main__":
    unittest.main()
