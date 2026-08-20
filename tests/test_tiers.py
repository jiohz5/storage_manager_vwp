import unittest

from smvwp import i18n, tiers


class TierClassifyTests(unittest.TestCase):
    def test_normal_below_90(self):
        self.assertEqual(tiers.classify(0), tiers.NORMAL)
        self.assertEqual(tiers.classify(89.9), tiers.NORMAL)

    def test_warn_90_to_94(self):
        self.assertEqual(tiers.classify(90), tiers.WARN)
        self.assertEqual(tiers.classify(94.9), tiers.WARN)

    def test_alert_95_to_97(self):
        self.assertEqual(tiers.classify(95), tiers.ALERT)
        self.assertEqual(tiers.classify(97.9), tiers.ALERT)

    def test_emergency_98_to_99(self):
        self.assertEqual(tiers.classify(98), tiers.EMERGENCY)
        self.assertEqual(tiers.classify(99.9), tiers.EMERGENCY)

    def test_full_100_and_above(self):
        self.assertEqual(tiers.classify(100), tiers.FULL)
        self.assertEqual(tiers.classify(101), tiers.FULL)

    def test_unknown_when_none(self):
        self.assertEqual(tiers.classify(None), tiers.UNKNOWN)


class TierSeverityTests(unittest.TestCase):
    def test_ordering(self):
        order = [tiers.NORMAL, tiers.WARN, tiers.ALERT, tiers.EMERGENCY, tiers.FULL]
        severities = [tiers.severity(t) for t in order]
        self.assertEqual(severities, sorted(severities))

    def test_unknown_lowest(self):
        self.assertLess(tiers.severity(tiers.UNKNOWN), tiers.severity(tiers.NORMAL))

    def test_worse_picks_more_severe(self):
        self.assertEqual(tiers.worse(tiers.NORMAL, tiers.ALERT), tiers.ALERT)
        self.assertEqual(tiers.worse(tiers.FULL, tiers.WARN), tiers.FULL)
        self.assertEqual(tiers.worse(tiers.WARN, tiers.WARN), tiers.WARN)

    def test_is_at_least(self):
        self.assertTrue(tiers.is_at_least(tiers.ALERT, tiers.WARN))
        self.assertFalse(tiers.is_at_least(tiers.WARN, tiers.ALERT))
        self.assertFalse(tiers.is_at_least(tiers.UNKNOWN, tiers.NORMAL))


class TierDisplayTests(unittest.TestCase):
    def test_every_tier_has_label_and_color(self):
        for tier in (tiers.NORMAL, tiers.WARN, tiers.ALERT, tiers.EMERGENCY, tiers.FULL, tiers.UNKNOWN):
            self.assertTrue(tiers.label(tier))
            self.assertTrue(tiers.color(tier).startswith("#"))

    def test_display_text_includes_pct(self):
        text = tiers.display_text(tiers.ALERT, 95.234)
        self.assertIn("95.2%", text)
        self.assertIn(tiers.label(tiers.ALERT), text)

    def test_display_text_without_pct(self):
        text = tiers.display_text(tiers.UNKNOWN, None)
        self.assertEqual(text, tiers.label(tiers.UNKNOWN))


if __name__ == "__main__":
    unittest.main()


class RowBackgroundTests(unittest.TestCase):
    """표 행 배경색.

    색은 등급을 전달하는 유일한 수단이 아니라 보조 수단이다. 등급 자체는 항상
    텍스트 배지로도 보이므로(display_text), 색을 못 보는 환경에서도 정보가
    사라지지 않아야 한다.
    """

    def test_normal_is_not_painted(self):
        """대부분의 계정이 정상인데 전부 칠하면 색이 배경 소음이 된다."""

        self.assertIsNone(tiers.row_background(tiers.NORMAL))

    def test_unknown_is_not_painted(self):
        self.assertIsNone(tiers.row_background(tiers.UNKNOWN))

    def test_warn_and_worse_are_painted(self):
        for tier in (tiers.WARN, tiers.ALERT, tiers.EMERGENCY, tiers.FULL):
            with self.subTest(tier=tier):
                self.assertIsNotNone(tiers.row_background(tier))

    def test_backgrounds_are_distinct(self):
        painted = [
            tiers.row_background(t)
            for t in (tiers.WARN, tiers.ALERT, tiers.EMERGENCY, tiers.FULL)
        ]
        self.assertEqual(len(set(painted)), len(painted))

    def test_backgrounds_are_light_enough_for_dark_text(self):
        """옅은 톤이어야 검은 글자가 읽힌다 - 기준색을 그대로 깔면 안 된다."""

        for tier in (tiers.WARN, tiers.ALERT, tiers.EMERGENCY, tiers.FULL):
            value = tiers.row_background(tier).lstrip("#")
            red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
            # ITU-R BT.601 명도. 밝을수록 1에 가깝다.
            luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255
            with self.subTest(tier=tier):
                self.assertGreater(luminance, 0.85)

    def test_unknown_tier_string_is_safe(self):
        self.assertIsNone(tiers.row_background("bogus"))

    def test_label_still_carries_the_tier_without_color(self):
        """색을 빼도 등급을 알 수 있어야 한다."""

        for tier in (tiers.WARN, tiers.ALERT, tiers.EMERGENCY, tiers.FULL):
            with self.subTest(tier=tier):
                self.assertTrue(tiers.display_text(tier, 95.0).strip())


class FormatSizePairTests(unittest.TestCase):
    """사용량/총 용량을 한 칸에 보여주는 표기.

    두 값이 **같은 단위**여야 한다. 각자 단위를 고르면 `950.0 GB / 1.0 TB`처럼
    나와서, 비교하라고 붙여 놓은 표시가 오히려 암산을 요구한다.
    """

    def test_uses_one_shared_unit(self):
        from smvwp.gui import widgets

        # 950GB / 1TB - 예전이라면 단위가 갈렸을 조합
        text = widgets.format_size_pair(950 * 1024 ** 2, 1024 ** 3)
        self.assertEqual(text, "0.9 / 1.0 TB")

    def test_unit_follows_the_total_not_the_used(self):
        from smvwp.gui import widgets

        # 사용량이 아주 작아도 총량 단위를 따라간다 (열 안에서 자릿수가 맞도록)
        self.assertEqual(widgets.format_size_pair(1024, 40 * 1024 ** 3), "0.0 / 40.0 TB")

    def test_stops_at_tb_so_large_volumes_stay_readable(self):
        """실제 계정은 많아야 수십 TB다. PB로 올라가면 `0.0 / 0.0 PB`처럼
        뭉개져 아무것도 못 읽는다."""

        from smvwp.gui import widgets

        # 100TB 중 99.1% - 예전 표기라면 `1.5 / 1.5 PB`가 아니라 여기서도
        # TB로 남아야 남은 용량 차이가 보인다.
        text = widgets.format_size_pair(int(1024 ** 3 * 100 * 0.991), 1024 ** 3 * 100)
        self.assertEqual(text, "99.1 / 100.0 TB")

    def test_small_volumes_still_use_a_smaller_unit(self):
        """TB 상한이 작은 볼륨까지 TB로 끌어올리면 안 된다."""

        from smvwp.gui import widgets

        self.assertEqual(
            widgets.format_size_pair(int(1024 ** 2 * 500 * 0.6), 1024 ** 2 * 500),
            "300.0 / 500.0 GB",
        )

    def test_unknown_total_falls_back_to_used_only(self):
        from smvwp.gui import widgets

        self.assertEqual(widgets.format_size_pair(5000, None), "4.9 MB")

    def test_unknown_used_is_marked_not_guessed(self):
        """모르는 값을 0으로 채우면 '안 쓰고 있다'로 잘못 읽힌다."""

        from smvwp.gui import widgets

        self.assertEqual(widgets.format_size_pair(None, 1024 ** 3), "? / 1.0 TB")

    def test_both_unknown(self):
        from smvwp.gui import widgets

        self.assertEqual(widgets.format_size_pair(None, None), "-")


class ScanLabelTests(unittest.TestCase):
    """스캔을 가리키는 이름은 회차 번호가 아니라 날짜다.

    `3번째 스캔`은 사용자에게 아무 기준점이 못 되지만 `260819 스캔`은 그날
    무슨 일이 있었는지와 바로 연결된다.
    """

    def test_korean_uses_yymmdd(self):
        from smvwp.gui import widgets

        i18n.set_language(i18n.KOREAN)
        self.assertEqual(widgets.scan_label("2026-08-19T22:31:05+00:00"), "260819")

    def test_english_uses_iso_date(self):
        from smvwp.gui import widgets

        i18n.set_language(i18n.ENGLISH)
        try:
            self.assertEqual(widgets.scan_label("2026-08-19T22:31:05+00:00"), "2026-08-19")
        finally:
            i18n.set_language(i18n.KOREAN)

    def test_falls_back_to_ordinal_while_still_running(self):
        """진행 중인 스캔은 완료 날짜가 없다 - 날짜를 지어내면 안 된다."""

        from smvwp.gui import widgets

        i18n.set_language(i18n.KOREAN)
        self.assertEqual(widgets.scan_label(None, 3), "3번째 스캔")

    def test_no_date_and_no_number_is_a_dash(self):
        from smvwp.gui import widgets

        self.assertEqual(widgets.scan_label(None, None), i18n.t("common.none"))
