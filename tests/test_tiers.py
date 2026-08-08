import unittest

from smvwp import tiers


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
