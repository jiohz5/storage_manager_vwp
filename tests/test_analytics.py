import unittest
from datetime import date, timedelta

from storage_manager.analytics import capacity_forecast, detect_growth_anomaly


class AnalyticsTests(unittest.TestCase):
    def test_capacity_forecast_projects_alert_and_full(self):
        start = date(2026, 7, 1)
        points = [
            ((start + timedelta(days=index)).isoformat(), 80 + index)
            for index in range(10)
        ]
        forecast = capacity_forecast(points, 30, alert_threshold=95)
        self.assertIsNotNone(forecast)
        self.assertAlmostEqual(forecast.slope_pct_per_day, 1.0)
        self.assertEqual(forecast.days_to_alert, 6)
        self.assertEqual(forecast.days_to_full, 11)

    def test_flat_capacity_has_no_projected_date(self):
        points = [(f"2026-07-{day:02d}", 80) for day in range(1, 8)]
        forecast = capacity_forecast(points, 7)
        self.assertIsNotNone(forecast)
        self.assertIsNone(forecast.days_to_alert)
        self.assertIsNone(forecast.days_to_full)

    def test_growth_anomaly_uses_relative_and_absolute_thresholds(self):
        normal = [(f"2026-07-{day:02d}", day * 10_000) for day in range(1, 9)]
        normal.append(("2026-07-09", normal[-1][1] + 1_000_000))
        result = detect_growth_anomaly(normal, multiplier=3.0, min_growth_kb=100_000)
        self.assertTrue(result.detected)
        self.assertEqual(result.latest_delta_kb, 1_000_000)


if __name__ == "__main__":
    unittest.main()
