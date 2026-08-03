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
