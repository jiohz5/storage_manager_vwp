"""GUI 가 밤을 스스로 지킬 때의 판단.

여기서 지켜야 할 핵심은 **밤마다 한 번**이다. 그냥 "시간창 안이면 시작"으로
두면 스캔이 01시에 끝난 뒤 1분 뒤 타이머가 또 시작해 밤새 같은 계정을 반복해서
훑는다 - 세대만 쌓이고 파일서버는 아침까지 두들겨 맞는다.
"""

import unittest
from datetime import datetime, timezone

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


def _utc(local: datetime) -> str:
    """실행 기록은 UTC 로 남는다. 시험 장비의 시간대와 무관하게 만든다."""

    return local.astimezone(timezone.utc).isoformat()


class DeferToCronTests(unittest.TestCase):
    """창의 자동 스캔은 cron 에 자리를 내준다.

    정규 경로는 cron 이고 창은 cron 이 없을 때의 대비다. 그런데 22:00 에 둘이
    함께 뜨면 먼저 잠금을 잡은 쪽이 밤을 맡는다 - 창이 잡으면 cron 은 20분 기다리다
    물러나고, 그 뒤 창을 닫는 순간 그 밤이 끝난다. 밤중에 창을 새로 열면 창은 이
    밤에 시작한 적이 없다고 여겨 이미 끝난 밤을 처음부터 다시 쟀다."""

    def test_waits_a_few_minutes_after_the_window_opens(self):
        self.assertFalse(
            auto_scan.should_start(datetime(2026, 8, 28, 22, 1), _settings(), False, None)
        )
        self.assertTrue(
            auto_scan.should_start(datetime(2026, 8, 28, 22, 10), _settings(), False, None)
        )

    def test_a_run_already_started_tonight_counts(self):
        """cron 이(어느 장비에서든) 이미 시작했으면 창은 또 시작하지 않는다."""

        tonight = _utc(datetime(2026, 8, 28, 22, 0))
        self.assertFalse(auto_scan.should_start(
            datetime(2026, 8, 28, 23, 0), _settings(), False, None, last_run_started_at=tonight
        ))

    def test_runs_from_other_nights_or_daytime_do_not_count(self):
        for started in (datetime(2026, 8, 27, 22, 0), datetime(2026, 8, 28, 15, 0)):
            with self.subTest(started=started):
                self.assertTrue(auto_scan.should_start(
                    datetime(2026, 8, 28, 23, 0), _settings(), False, None,
                    last_run_started_at=_utc(started),
                ))

    def test_a_broken_timestamp_is_not_a_reason_to_stop(self):
        self.assertTrue(auto_scan.should_start(
            datetime(2026, 8, 28, 23, 0), _settings(), False, None, last_run_started_at="?"
        ))


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

    def test_old_settings_without_the_field_do_not_start(self):
        """옛 설정 파일에는 이 값이 없다 - 읽을 때 기본값(꺼짐)이 채워져 자동
        시작을 안 할 뿐이다."""

        settings = config_module._settings_from_dict(
            {"detail_scan_window_start_hour": 22, "detail_scan_window_end_hour": 6}
        )
        self.assertFalse(auto_scan.should_start(self.NIGHT, settings, False, None))


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
