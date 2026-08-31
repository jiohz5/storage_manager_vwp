"""GUI 가 밤을 스스로 지킬 때의 판단.

여기서 지켜야 할 핵심은 **밤마다 한 번**이다. 그냥 "시간창 안이면 시작"으로
두면 스캔이 01시에 끝난 뒤 1분 뒤 타이머가 또 시작해 밤새 같은 계정을 반복해서
훑는다 - 세대만 쌓이고 파일서버는 아침까지 두들겨 맞는다.
"""

import unittest
from datetime import datetime

from smvwp import auto_scan
from smvwp import config as config_module


def _settings(**kwargs):
    settings = config_module.Settings()
    settings.gui_auto_nightly_scan = True
    for key, value in kwargs.items():
        setattr(settings, key, value)
    return settings


class WindowKeyTests(unittest.TestCase):
    """밤의 이름은 **끝나는 아침**이다 - 자정을 넘어도 같은 밤이어야 한다."""

    def test_before_and_after_midnight_are_the_same_night(self):
        evening = auto_scan.window_key(datetime(2026, 8, 28, 23, 0))
        after_midnight = auto_scan.window_key(datetime(2026, 8, 29, 1, 0))
        self.assertEqual(evening, after_midnight)

    def test_the_name_is_the_morning_it_ends(self):
        self.assertEqual(auto_scan.window_key(datetime(2026, 8, 28, 23, 0)), "2026-08-29")

    def test_two_different_nights_differ(self):
        first = auto_scan.window_key(datetime(2026, 8, 28, 23, 0))
        second = auto_scan.window_key(datetime(2026, 8, 29, 23, 0))
        self.assertNotEqual(first, second)

    def test_daytime_has_no_night(self):
        self.assertIsNone(auto_scan.window_key(datetime(2026, 8, 28, 12, 0)))

    def test_the_boundary_hours_follow_the_window(self):
        # 기본 창은 22:00~06:00.
        self.assertIsNotNone(auto_scan.window_key(datetime(2026, 8, 28, 22, 0)))
        self.assertIsNotNone(auto_scan.window_key(datetime(2026, 8, 29, 5, 59)))
        self.assertIsNone(auto_scan.window_key(datetime(2026, 8, 29, 6, 0)))
        self.assertIsNone(auto_scan.window_key(datetime(2026, 8, 28, 21, 59)))


class ShouldStartTests(unittest.TestCase):
    NIGHT = datetime(2026, 8, 28, 23, 0)
    LATER_SAME_NIGHT = datetime(2026, 8, 29, 2, 0)
    NEXT_NIGHT = datetime(2026, 8, 29, 23, 0)

    def test_starts_once_when_the_window_opens(self):
        self.assertTrue(
            auto_scan.should_start(self.NIGHT, _settings(), False, None)
        )

    def test_does_not_start_again_the_same_night(self):
        """이 한 줄이 없으면 밤새 반복해서 훑는다."""

        key = auto_scan.window_key(self.NIGHT)
        self.assertFalse(
            auto_scan.should_start(self.LATER_SAME_NIGHT, _settings(), False, key)
        )

    def test_starts_again_the_next_night(self):
        key = auto_scan.window_key(self.NIGHT)
        self.assertTrue(
            auto_scan.should_start(self.NEXT_NIGHT, _settings(), False, key)
        )

    def test_never_starts_a_second_one_on_top(self):
        self.assertFalse(
            auto_scan.should_start(self.NIGHT, _settings(), True, None)
        )

    def test_daytime_never_starts(self):
        noon = datetime(2026, 8, 28, 12, 0)
        self.assertFalse(auto_scan.should_start(noon, _settings(), False, None))

    def test_turning_it_off_stops_everything(self):
        settings = _settings(gui_auto_nightly_scan=False)
        self.assertFalse(
            auto_scan.should_start(self.NIGHT, settings, False, None)
        )

    def test_a_custom_window_is_honoured(self):
        """시간창을 옮기면 자동 시작도 따라가야 한다."""

        settings = _settings(
            detail_scan_window_start_hour=1, detail_scan_window_end_hour=4
        )
        self.assertFalse(
            auto_scan.should_start(datetime(2026, 8, 28, 23, 0), settings, False, None)
        )
        self.assertTrue(
            auto_scan.should_start(datetime(2026, 8, 29, 2, 0), settings, False, None)
        )

    def test_old_settings_without_the_field_do_not_crash(self):
        """옛 설정 파일에는 이 값이 없다 - 없으면 자동 시작을 안 할 뿐이다."""

        class Old:
            detail_scan_window_start_hour = 22
            detail_scan_window_end_hour = 6

        self.assertFalse(auto_scan.should_start(self.NIGHT, Old(), False, None))


class SettingsTests(unittest.TestCase):
    def test_the_setting_survives_a_round_trip(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            config = config_module.load_config(data_dir)
            config.settings.gui_auto_nightly_scan = False
            config_module.save_config(data_dir, config)
            self.assertFalse(
                config_module.load_config(data_dir).settings.gui_auto_nightly_scan
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
