"""저장은 UTC, 표시는 지역시간.

## 무엇이 잘못됐었나

모든 시각을 UTC로 저장하는 것은 맞다. 그런데 화면과 보고서가 그 문자열을
**그대로 잘라** 썼다 (`started_at[:19]`). 한국시간은 UTC+9 이라 오전 9시에
돌린 스캔이 `00:00` 으로 나왔고, 새벽 0~9시에 일어난 일은 **날짜까지 하루
전으로** 나왔다.

문자열 자르기가 이 오류를 눈에 안 띄게 만든다 - 어떤 ISO 문자열이든 그럴듯한
결과가 나오기 때문이다. 그래서 여기서는 "그럴듯한가"가 아니라 **같은 순간을
가리키는가**를 본다.

## 시험이 개발 PC 의 시간대에 기대지 않게

이 컴퓨터가 무슨 시간대든 참인 것으로 시험한다 - 같은 순간을 다르게 쓴 두
문자열은 반드시 같게 나와야 한다는 식이다. 한국시간 자체는 `tz` 를 넘겨 어디서든
돌린다. 그 이음매가 없으면 정작 문제가 났던 경우가 개발 PC 에서 건너뛰어지고,
반입 장비에서 처음 도는 코드가 된다.
"""

import os
import time
import unittest
from datetime import datetime, timedelta, timezone

from smvwp import formatting, i18n

KST = timezone(timedelta(hours=9))


class InstantTests(unittest.TestCase):
    """어떤 시간대에서 돌려도 참이어야 하는 것."""

    def test_the_instant_is_preserved(self):
        moment = formatting.to_local("2026-09-10T00:00:00+00:00")
        self.assertEqual(
            moment.astimezone(timezone.utc),
            datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc),
        )

    def test_two_spellings_of_the_same_instant_render_the_same(self):
        """09:00+09:00 과 00:00+00:00 은 같은 순간이다."""

        self.assertEqual(
            formatting.local_datetime_text("2026-09-10T09:00:00+09:00"),
            formatting.local_datetime_text("2026-09-10T00:00:00+00:00"),
        )

    def test_a_naive_string_is_read_as_utc(self):
        """이 프로그램이 쓰는 모든 시각이 UTC다. 지역시간으로 보면 9시간 더 밀린다."""

        self.assertEqual(
            formatting.local_datetime_text("2026-09-10T00:00:00"),
            formatting.local_datetime_text("2026-09-10T00:00:00+00:00"),
        )

    def test_the_result_carries_a_timezone(self):
        self.assertIsNotNone(formatting.to_local("2026-09-10T00:00:00+00:00").tzinfo)

    def test_microseconds_do_not_break_parsing(self):
        """실제로 저장되는 모양이 이렇다."""

        self.assertIsNotNone(formatting.to_local("2026-09-10T00:00:00.123456+00:00"))


class FallbackTests(unittest.TestCase):
    def test_nothing_in_fallback_out(self):
        self.assertEqual(formatting.local_datetime_text(None), "-")
        self.assertEqual(formatting.local_date_text(""), "-")

    def test_garbage_does_not_become_a_plausible_time(self):
        """자르기 방식은 쓰레기 입력도 그럴듯하게 보여 줬다."""

        self.assertEqual(formatting.local_clock_text("not a time"), "-")
        self.assertIsNone(formatting.to_local("2026-13-45"))

    def test_the_fallback_can_be_chosen(self):
        self.assertEqual(formatting.local_date_text(None, fallback=""), "")


class ShapeTests(unittest.TestCase):
    def test_each_helper_gives_the_shape_it_promises(self):
        moment = formatting.to_local("2026-09-10T00:00:00+00:00")
        self.assertEqual(
            formatting.local_datetime_text("2026-09-10T00:00:00+00:00"),
            moment.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.assertEqual(
            formatting.local_minute_text("2026-09-10T00:00:00+00:00"),
            moment.strftime("%Y-%m-%d %H:%M"),
        )
        self.assertEqual(
            formatting.local_clock_text("2026-09-10T00:00:00+00:00"),
            moment.strftime("%H:%M"),
        )


class KoreanTimeTests(unittest.TestCase):
    """한국시간을 실제로 걸어 본다 - 반입 장비가 도는 조건.

    `tz` 를 넘겨서 이 컴퓨터의 시간대와 무관하게 돌린다. 이 이음매가 없으면
    정작 문제가 났던 경우가 개발 PC 에서 건너뛰어진다."""

    def test_nine_in_the_morning_reads_as_nine(self):
        """이것이 신고된 증상이다 - 오전 9시가 00: 으로 나왔다."""

        self.assertEqual(
            formatting.local_clock_text("2026-09-10T00:00:00+00:00", tz=KST), "09:00"
        )

    def test_the_night_window_reads_as_the_night(self):
        """밤 10시 시작은 22:00 으로 보여야 한다 (UTC로는 13:00)."""

        self.assertEqual(
            formatting.local_clock_text("2026-09-09T13:00:00+00:00", tz=KST), "22:00"
        )

    def test_an_early_morning_scan_keeps_its_own_date(self):
        """UTC 날짜를 그대로 쓰면 새벽에 끝난 스캔이 전날 것으로 불린다."""

        # 2026-09-10 새벽 3시 KST = 2026-09-09 18:00 UTC
        self.assertEqual(
            formatting.local_date_text("2026-09-09T18:00:00+00:00", tz=KST),
            "2026-09-10",
        )

    def test_the_scan_name_follows_the_local_date(self):
        i18n.set_language(i18n.KOREAN)
        self.addCleanup(i18n.set_language, i18n.KOREAN)
        self.assertEqual(
            formatting.scan_label("2026-09-09T18:00:00+00:00", tz=KST), "260910"
        )

    def test_the_full_stamp_carries_both_halves(self):
        self.assertEqual(
            formatting.local_datetime_text("2026-09-10T00:00:00+00:00", tz=KST),
            "2026-09-10 09:00:00",
        )


class SystemTimezoneTests(unittest.TestCase):
    """장비의 시간대를 실제로 바꿔 본다 (바꿀 수 있는 곳에서만)."""

    @unittest.skipUnless(hasattr(time, "tzset"), "TZ를 바꿀 수 있는 곳에서만 (리눅스)")
    def test_the_default_follows_the_machine(self):
        saved = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Seoul"
        time.tzset()

        def restore():
            if saved is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved
            time.tzset()

        self.addCleanup(restore)
        self.assertEqual(
            formatting.local_clock_text("2026-09-10T00:00:00+00:00"), "09:00"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
