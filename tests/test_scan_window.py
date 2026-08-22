import unittest
from datetime import datetime

from smvwp import config as config_module
from smvwp import nightly_scan, scan_window


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


class WeekendNightTests(unittest.TestCase):
    """주말 밤 판정 - 날짜가 아니라 **끝나는 아침**으로 정한다.

    야간 스캔은 자정을 넘어가므로 "오늘이 주말인가"는 틀린 질문이다. 스캔이
    만든 부하는 밤새 쌓였다가 그 아침에 출근하는 사람에게 청구된다.
    """

    # 2026-08-20(목) ~ 08-24(월)
    THURSDAY, FRIDAY, SATURDAY, SUNDAY, MONDAY = 20, 21, 22, 23, 24

    def _ends_on_weekend(self, day, hour):
        return scan_window.ends_on_weekend(datetime(2026, 8, day, hour, 0))

    def test_friday_night_counts_as_weekend(self):
        """금요일 22:00 -> 토요일 06:00. 토요일 아침은 한산하다."""

        self.assertTrue(self._ends_on_weekend(self.FRIDAY, 22))
        self.assertTrue(self._ends_on_weekend(self.FRIDAY, 23))

    def test_saturday_after_midnight_is_still_the_friday_night(self):
        """토요일 02:00은 금요일 밤의 뒷부분이고, 여전히 토요일 아침에 끝난다."""

        self.assertTrue(self._ends_on_weekend(self.SATURDAY, 2))

    def test_saturday_night_counts_as_weekend(self):
        """토요일 22:00 -> 일요일 06:00."""

        self.assertTrue(self._ends_on_weekend(self.SATURDAY, 22))
        self.assertTrue(self._ends_on_weekend(self.SUNDAY, 3))

    def test_sunday_night_is_a_weekday_night(self):
        """일요일 22:00 -> 월요일 06:00. 월요일 아침엔 전원이 출근한다.

        이것이 이 판정의 존재 이유다 - '오늘이 주말인가'로 하면 가장 붐비는
        월요일 아침 직전에 가장 세게 돌게 된다."""

        self.assertFalse(self._ends_on_weekend(self.SUNDAY, 22))
        self.assertFalse(self._ends_on_weekend(self.MONDAY, 3))

    def test_thursday_night_is_a_weekday_night(self):
        self.assertFalse(self._ends_on_weekend(self.THURSDAY, 23))
        self.assertFalse(self._ends_on_weekend(self.FRIDAY, 3))

    def test_matches_the_actual_stop_time(self):
        """판정 기준은 스캔이 실제로 멈추는 시각과 언제나 같아야 한다."""

        for day in range(17, 25):
            for hour in (22, 23, 0, 3, 5):
                now = datetime(2026, 8, day, hour, 0)
                with self.subTest(day=day, hour=hour):
                    self.assertEqual(
                        scan_window.ends_on_weekend(now),
                        scan_window.next_window_end(now).weekday() in (5, 6),
                    )


class ParallelResolutionTests(unittest.TestCase):
    """설정값 두 개(평일/주말) 중 어느 것을 쓸지."""

    def setUp(self):
        self.settings = config_module.Settings()
        self.settings.nightly_parallel_accounts = 1
        self.settings.weekend_parallel_accounts = 4

    def test_weekday_night_uses_the_weekday_value(self):
        value, weekend = nightly_scan.resolve_parallel_accounts(
            datetime(2026, 8, 24, 23, 0), self.settings  # 월요일 밤
        )
        self.assertEqual((value, weekend), (1, False))

    def test_weekend_night_uses_the_weekend_value(self):
        value, weekend = nightly_scan.resolve_parallel_accounts(
            datetime(2026, 8, 21, 23, 0), self.settings  # 금요일 밤
        )
        self.assertEqual((value, weekend), (4, True))

    def test_daytime_runs_stay_conservative_even_on_a_weekend(self):
        """토요일 낮에 손으로 돌리는 것은 진단 경로다.

        그 시간에 자리에 있는 사람은 지금 일하고 있는 사람이므로, 주말이라고
        세게 돌면 안 된다. 필요하면 --parallel로 의도를 밝히면 된다."""

        value, weekend = nightly_scan.resolve_parallel_accounts(
            datetime(2026, 8, 22, 14, 0), self.settings  # 토요일 오후
        )
        self.assertEqual((value, weekend), (1, False))


if __name__ == "__main__":
    unittest.main()
