import unittest
from datetime import datetime

from smvwp import scan_window


class IsWithinWindowTests(unittest.TestCase):
    def test_before_midnight_inside_window(self):
        now = datetime(2026, 7, 31, 23, 0)
        self.assertTrue(scan_window.is_within_window(now))

    def test_after_midnight_inside_window(self):
        now = datetime(2026, 8, 1, 3, 0)
        self.assertTrue(scan_window.is_within_window(now))

    def test_exactly_at_start_hour_is_inside(self):
        now = datetime(2026, 7, 31, 22, 0)
        self.assertTrue(scan_window.is_within_window(now))

    def test_exactly_at_end_hour_is_outside(self):
        now = datetime(2026, 8, 1, 6, 0)
        self.assertFalse(scan_window.is_within_window(now))

    def test_daytime_outside_window(self):
        now = datetime(2026, 7, 31, 14, 30)
        self.assertFalse(scan_window.is_within_window(now))

    def test_custom_non_wrapping_window(self):
        now = datetime(2026, 7, 31, 10, 0)
        self.assertTrue(scan_window.is_within_window(now, start_hour=9, end_hour=17))
        self.assertFalse(scan_window.is_within_window(now, start_hour=18, end_hour=20))


class NextWindowEndTests(unittest.TestCase):
    def test_before_midnight_end_is_tomorrow(self):
        now = datetime(2026, 7, 31, 23, 0)
        end = scan_window.next_window_end(now)
        self.assertEqual(end, datetime(2026, 8, 1, 6, 0))

    def test_after_midnight_end_is_today(self):
        now = datetime(2026, 8, 1, 3, 0)
        end = scan_window.next_window_end(now)
        self.assertEqual(end, datetime(2026, 8, 1, 6, 0))

    def test_seconds_remaining_never_negative(self):
        # 정각 종료 시각 자체는 "다음 창"의 종료 시각(내일)까지 남은 시간을
        # 뜻하므로 0이 아니다 - 여기서는 음수가 절대 나오지 않는다는 것만
        # 확인한다 (창 안/밖 어디서든).
        for hour in range(24):
            now = datetime(2026, 8, 1, hour, 0)
            self.assertGreaterEqual(scan_window.seconds_remaining(now), 0.0)

    def test_seconds_remaining_positive_within_window(self):
        now = datetime(2026, 8, 1, 5, 30)
        remaining = scan_window.seconds_remaining(now)
        self.assertAlmostEqual(remaining, 1800, delta=1)


class DescribeTests(unittest.TestCase):
    def test_describe_mentions_window_state(self):
        inside = scan_window.describe(datetime(2026, 7, 31, 23, 0))
        outside = scan_window.describe(datetime(2026, 7, 31, 14, 0))
        self.assertIn("진행 중", inside)
        self.assertIn("아님", outside)


if __name__ == "__main__":
    unittest.main()
