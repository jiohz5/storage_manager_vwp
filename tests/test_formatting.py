"""서식 함수들 - 이제야 시험할 수 있게 됐다.

이 함수들은 `gui/widgets.py` 안에서 Qt 위젯과 섞여 있었다. 시험하려면 PyQt5 를
임포트해야 하는데 개발 PC 에는 없어서, **서식 전체가 시험 밖에** 있었다.
단위·반올림·빈 값 표기처럼 조용히 어긋나기 쉬운 것들인데도.

여기서 못박는 것은 세 가지다.

- 값이 **없을 때** 무엇을 보여 주는가 (0 과 '모름'을 섞으면 안 된다).
- 단위 경계에서 숫자가 튀지 않는가.
- 화면이 부르는 인자 모양이 그대로인가.
"""

import unittest

from smvwp import formatting, i18n, large_files


class FormatKbTests(unittest.TestCase):
    def setUp(self):
        i18n.set_language("ko")

    def test_none_is_not_zero(self):
        """'0바이트'와 '모름'은 완전히 다른 이야기다."""

        text = formatting.format_kb(None)
        self.assertNotIn("0", text)

    def test_zero_stays_zero(self):
        self.assertIn("0", formatting.format_kb(0))

    def test_unit_climbs_with_size(self):
        small = formatting.format_kb(512)
        medium = formatting.format_kb(512 * 1024)
        large = formatting.format_kb(512 * 1024 * 1024)
        self.assertIn("KB", small)
        self.assertIn("MB", medium)
        self.assertIn("GB", large)

    def test_the_boundary_does_not_jump(self):
        """1023KB 다음이 1MB 여야 한다 - 여기서 어긋나면 표가 이상해 보인다."""

        self.assertIn("KB", formatting.format_kb(1023))
        self.assertIn("MB", formatting.format_kb(1024))

    def test_very_large_values_stay_readable(self):
        text = formatting.format_kb(5 * 1024 ** 4)   # 5 PB
        self.assertLess(len(text), 16, f"너무 깁니다: {text}")


class FormatBytesTests(unittest.TestCase):
    def test_none_is_handled(self):
        self.assertIsInstance(formatting.format_bytes(None), str)

    def test_kilobyte_boundary(self):
        self.assertIn("KB", formatting.format_bytes(1024))


class FormatKbDeltaTests(unittest.TestCase):
    """증감은 부호가 붙어야 한다 - 안 그러면 늘었는지 줄었는지 모른다."""

    def test_growth_is_marked(self):
        self.assertTrue(formatting.format_kb_delta(1024).startswith("+"))

    def test_shrink_is_marked(self):
        text = formatting.format_kb_delta(-1024)
        self.assertTrue(text.startswith("-") or "−" in text, text)

    def test_no_change_is_not_a_plus_zero(self):
        text = formatting.format_kb_delta(0)
        self.assertNotEqual(text.strip(), "+0")

    def test_none_is_handled(self):
        self.assertIsInstance(formatting.format_kb_delta(None), str)

    def test_no_change_follows_the_language(self):
        """영어 화면의 큰 파일 표에 '변화 없음' 이 한글로 떴다."""

        try:
            i18n.set_language("en")
            self.assertEqual(formatting.format_kb_delta(0), i18n.t("scan.no_change"))
            self.assertNotIn("변화", formatting.format_kb_delta(0))
        finally:
            i18n.set_language("ko")


class LargeFileChangeTests(unittest.TestCase):
    """큰 파일의 '지난 스캔 대비' 칸. 화면 셋과 보고서·안내가 같은 말을 쓰게 한곳에서."""

    def setUp(self):
        i18n.set_language("ko")

    def test_a_file_missing_from_the_last_list(self):
        item = large_files.LargeFile("/a/x", 300, previous_kb=None, compared=True)
        self.assertEqual(formatting.format_large_file_change(item), i18n.t("large.new"))

    def test_with_no_list_to_compare_it_says_nothing(self):
        """첫 스캔에는 견줄 목록이 없다 - '목록에 없던 파일'은 지어낸 말이 된다."""

        item = large_files.LargeFile("/a/x", 300, previous_kb=None, compared=False)
        self.assertEqual(formatting.format_large_file_change(item), i18n.t("common.none"))

    def test_a_change(self):
        item = large_files.LargeFile("/a/x", 3 * 1024, previous_kb=1024)
        self.assertEqual(formatting.format_large_file_change(item), formatting.format_kb_delta(2048))


class FormatSizePairTests(unittest.TestCase):
    def test_the_pair_shares_one_unit(self):
        """`0.5 / 1.0 GB` 처럼 단위를 한 번만 쓴다.

        각자 제 단위로 쓰면(`512 MB / 1.0 GB`) 두 숫자를 눈으로 못 비교한다."""

        text = formatting.format_size_pair(512 * 1024, 1024 * 1024)
        self.assertEqual(text.count("B"), 1, text)
        self.assertIn("0.5", text)
        self.assertIn("1.0", text)

    def test_missing_total_does_not_crash(self):
        self.assertIsInstance(formatting.format_size_pair(100, None), str)

    def test_missing_both_does_not_crash(self):
        self.assertIsInstance(formatting.format_size_pair(None, None), str)


class TierBadgeTests(unittest.TestCase):
    def test_percent_is_shown_when_known(self):
        self.assertIn("90", formatting.tier_badge_text("danger", 90.0))

    def test_unknown_percent_does_not_print_none(self):
        text = formatting.tier_badge_text("normal", None)
        self.assertNotIn("None", text)


class ScanCpuTextTests(unittest.TestCase):
    """직전 스캔의 CPU 사용량 한 줄."""

    def test_no_run_is_empty(self):
        self.assertEqual(formatting.scan_cpu_text(None), "")

    def test_missing_columns_are_empty_not_a_crash(self):
        """옛 DB 에는 이 열들이 없다 - 그때 창이 죽으면 안 된다."""

        self.assertEqual(formatting.scan_cpu_text({}), "")

    def test_null_values_are_empty(self):
        row = {
            "cpu_top_percent_avg": None,
            "cpu_top_percent_peak": None,
            "cpu_system_percent_avg": None,
        }
        self.assertEqual(formatting.scan_cpu_text(row), "")

    def test_cpu_numbers_appear(self):
        row = {
            "cpu_top_percent_avg": 42.4,
            "cpu_top_percent_peak": 180.6,
            "cpu_system_percent_avg": 3.25,
        }
        text = formatting.scan_cpu_text(row)
        self.assertIn("42", text)
        self.assertIn("181", text)   # 반올림
        self.assertNotIn("None", text)

    def test_memory_is_appended_when_present(self):
        row = {
            "cpu_top_percent_avg": 10.0,
            "cpu_top_percent_peak": 20.0,
            "cpu_system_percent_avg": 1.0,
            "rss_peak_kb": 150 * 1024,
            "memory_peak_percent": 0.5,
        }
        text = formatting.scan_cpu_text(row)
        self.assertIn("MB", text)

    def test_memory_columns_missing_still_gives_the_cpu_part(self):
        row = {
            "cpu_top_percent_avg": 10.0,
            "cpu_top_percent_peak": 20.0,
            "cpu_system_percent_avg": 1.0,
        }
        self.assertIn("10", formatting.scan_cpu_text(row))


class TooltipTests(unittest.TestCase):
    class FakeAccount:
        path = "/user/cae/projA"

    class FakeSample:
        filesystem = "nfs4"
        mount_point = "/user/cae"
        used_kb = 1024
        total_kb = 4096
        avail_kb = 3072

    def test_path_tooltip_always_has_the_path(self):
        text = formatting.path_tooltip(self.FakeAccount(), None)
        self.assertIn("/user/cae/projA", text)

    def test_path_tooltip_adds_filesystem_when_known(self):
        text = formatting.path_tooltip(self.FakeAccount(), self.FakeSample())
        self.assertIn("nfs4", text)
        self.assertIn("/user/cae", text)

    def test_size_tooltip_has_all_three_numbers(self):
        text = formatting.size_tooltip(self.FakeSample())
        self.assertEqual(len(text.splitlines()), 3)


class RelativePathTests(unittest.TestCase):
    """한 계정의 표에서 모든 행이 같은 앞머리로 시작하면, 그 앞머리가 칸을
    다 먹어 정작 다른 뒤쪽이 잘린다. 줄이되 **거짓말은 하지 않는다**."""

    ROOT = "/ifs/proj/design/layout_a"

    def test_a_path_inside_the_account_loses_the_account_prefix(self):
        self.assertEqual(
            formatting.relative_path(self.ROOT + "/LAYOUT/run_03/BACKUP", self.ROOT),
            "LAYOUT/run_03/BACKUP",
        )

    def test_the_account_root_itself_gets_the_label(self):
        self.assertEqual(formatting.relative_path(self.ROOT, self.ROOT), ".")
        self.assertEqual(
            formatting.relative_path(self.ROOT + "/", self.ROOT, root_label="(top)"),
            "(top)",
        )

    def test_a_sibling_that_merely_shares_the_prefix_is_left_whole(self):
        """`/ifs/a` 계정에서 `/ifs/ab/x` 가 `b/x` 로 줄면 다른 곳으로 읽힌다."""

        sibling = "/ifs/proj/design/layout_ab/x"
        self.assertEqual(formatting.relative_path(sibling, self.ROOT), sibling)

    def test_a_path_outside_the_account_is_left_whole(self):
        self.assertEqual(formatting.relative_path("/tmp/x", self.ROOT), "/tmp/x")

    def test_a_trailing_slash_on_the_root_does_not_matter(self):
        self.assertEqual(
            formatting.relative_path(self.ROOT + "/a", self.ROOT + "/"), "a"
        )

    def test_missing_values_do_not_raise(self):
        self.assertEqual(formatting.relative_path("/x", None), "/x")
        self.assertEqual(formatting.relative_path("/x", ""), "/x")
        self.assertEqual(formatting.relative_path(None, self.ROOT), "")
        # 루트가 `/` 뿐이면 줄일 것이 없다 - 전부를 "." 로 만들면 안 된다.
        self.assertEqual(formatting.relative_path("/x", "/"), "/x")


def _prediction(value):
    """숫자면 그 날 수 뒤에 찬다는 예측, 글자면 그 이유로 못 낸 예측."""

    from smvwp.analytics import Prediction

    if isinstance(value, str):
        return Prediction(ok=False, reason=value)
    return Prediction(ok=True, hours_to_full=value * 24)


def _forecast(short, long, imminent="insufficient_samples"):
    from smvwp.analytics import CapacityForecast

    return CapacityForecast(account_id="a", imminent=_prediction(imminent),
                            short_trend=_prediction(short), long_trend=_prediction(long))


class ForecastCellTests(unittest.TestCase):
    """홈 표의 `FULL 예상` 칸.

    이 칸은 표에서 남는 폭을 받는 칸이라, 긴 문구는 두 줄로 접혀 행 하나만 키가
    커진다 (`예측 불가(예측 범위 초과) / 약 2105일`). 칸에는 짧게, 긴 설명은
    툴팁에 둔다."""

    def setUp(self):
        i18n.set_language("ko")
        self.addCleanup(i18n.set_language, "ko")

    def test_the_same_number_is_not_written_twice(self):
        """`약 15일 / 약 15일` - 두 추세가 같으면 한 번이면 된다."""

        self.assertEqual(formatting.format_forecast_cell(_forecast(15.2, 14.9)), "약 15일")

    def test_two_different_numbers_stay_side_by_side(self):
        """다르면 둘 다 둔다 - 빨라졌는지 느려졌는지가 그 차이에 있다."""

        self.assertEqual(formatting.format_forecast_cell(_forecast(98, 104)), "약 98일 / 약 104일")

    def test_a_year_or_more_is_said_in_years(self):
        """`약 2105일` 은 세어 봐야 안다. `약 5.8년` 은 바로 읽힌다."""

        self.assertEqual(formatting.format_prediction(_prediction(2105)), "약 5.8년")
        self.assertEqual(formatting.format_prediction(_prediction(364)), "약 364일")
        self.assertEqual(formatting.format_prediction(_prediction(365)), "약 1.0년")
        self.assertEqual(formatting.format_prediction(_prediction(4400)), "약 12년")

    def test_a_missing_estimate_is_a_short_word_in_the_cell(self):
        text = formatting.format_forecast_cell(_forecast("too_far", 2105))
        self.assertEqual(text, "아주 느림 / 약 5.8년")
        self.assertNotIn(i18n.t("forecast.unavailable"), text)

    def test_both_missing_for_the_same_reason_is_one_word(self):
        self.assertEqual(formatting.format_forecast_cell(_forecast("not_growing", "not_growing")),
                         "늘지 않음")

    def test_an_unknown_reason_still_says_no_estimate(self):
        self.assertEqual(formatting.format_forecast_cell(_forecast("", "")),
                         i18n.t("forecast.unavailable"))

    def test_the_tooltip_keeps_the_whole_reason(self):
        tip = formatting.format_forecast_tooltip(_forecast("too_far", 2105), 48)
        self.assertIn(i18n.t("forecast.reason.too_far"), tip)

    def test_an_imminent_fill_is_still_hours(self):
        self.assertEqual(formatting.format_forecast_cell(_forecast(3, 4, imminent=1.25)), "약 30시간")

    def test_english_cells_are_short_too(self):
        i18n.set_language("en")
        self.assertEqual(formatting.format_forecast_cell(_forecast("too_far", 2105)), "Very slow / ~5.8y")
        self.assertEqual(formatting.format_forecast_cell(_forecast(15.2, 14.9)), "~15d")

    def test_has_estimate(self):
        """칸을 흐리게 둘지는 글자가 아니라 예측이 하나라도 섰는지로 정한다."""

        self.assertFalse(formatting.forecast_has_estimate(None))
        self.assertFalse(formatting.forecast_has_estimate(_forecast("too_far", "not_growing")))
        self.assertTrue(formatting.forecast_has_estimate(_forecast("too_far", 2105)))


class LanguageTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("ko")

    def test_formatting_follows_the_current_language(self):
        """언어를 바꿨는데 서식만 옛 언어로 남으면 화면이 섞인다."""

        row = {
            "cpu_top_percent_avg": 10.0,
            "cpu_top_percent_peak": 20.0,
            "cpu_system_percent_avg": 1.0,
        }
        i18n.set_language("ko")
        korean = formatting.scan_cpu_text(row)
        i18n.set_language("en")
        english = formatting.scan_cpu_text(row)
        self.assertNotEqual(korean, english)

    def test_a_dash_stays_a_dash_in_both_languages(self):
        """빈 값 표기는 번역하지 않는다 - 표에서 자리만 흔들린다."""

        i18n.set_language("ko")
        korean = formatting.format_kb(None)
        i18n.set_language("en")
        self.assertEqual(korean, formatting.format_kb(None))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
