import unittest

from smvwp import i18n, tiers


class LanguageSwitchingTests(unittest.TestCase):
    def setUp(self):
        self._original = i18n.get_language()

    def tearDown(self):
        i18n.set_language(self._original)

    def test_default_is_korean(self):
        i18n.set_language(i18n.KOREAN)
        self.assertEqual(i18n.t("tier.alert"), "경고")

    def test_english_switch(self):
        i18n.set_language(i18n.ENGLISH)
        self.assertEqual(i18n.t("tier.alert"), "Alert")

    def test_unsupported_language_falls_back_to_default(self):
        applied = i18n.set_language("fr")
        self.assertEqual(applied, i18n.DEFAULT_LANGUAGE)

    def test_missing_key_returns_key_itself(self):
        """번역 하나가 빠졌다고 화면이 죽으면 안 된다."""

        self.assertEqual(i18n.t("nonexistent.key.name"), "nonexistent.key.name")

    def test_format_arguments_are_substituted(self):
        i18n.set_language(i18n.KOREAN)
        text = i18n.t("dashboard.all_normal", count=3)
        self.assertIn("3", text)

    def test_bad_format_arguments_do_not_raise(self):
        """서식 인자가 안 맞아도 원문이라도 보여야 한다."""

        text = i18n.t("dashboard.all_normal")  # count 없이 호출
        self.assertIsInstance(text, str)
        self.assertTrue(text)

    def test_every_key_exists_in_all_languages(self):
        """한 언어에만 있는 키가 없어야 한다 (번역 누락 방지)."""

        korean_keys = set(i18n._CATALOG[i18n.KOREAN])
        english_keys = set(i18n._CATALOG[i18n.ENGLISH])
        self.assertEqual(korean_keys - english_keys, set(), "영어 번역 누락")
        self.assertEqual(english_keys - korean_keys, set(), "한국어 번역 누락")


class TierLabelLocalizationTests(unittest.TestCase):
    def setUp(self):
        self._original = i18n.get_language()

    def tearDown(self):
        i18n.set_language(self._original)

    def test_tier_label_follows_language(self):
        i18n.set_language(i18n.KOREAN)
        self.assertEqual(tiers.label(tiers.EMERGENCY), "긴급")
        i18n.set_language(i18n.ENGLISH)
        self.assertEqual(tiers.label(tiers.EMERGENCY), "Emergency")

    def test_tier_code_is_language_neutral(self):
        """저장/전송에 쓰는 코드 자체는 언어에 영향받지 않아야 한다."""

        i18n.set_language(i18n.ENGLISH)
        self.assertEqual(tiers.classify(99.0), "emergency")

    def test_color_is_language_independent(self):
        i18n.set_language(i18n.KOREAN)
        korean_color = tiers.color(tiers.ALERT)
        i18n.set_language(i18n.ENGLISH)
        self.assertEqual(tiers.color(tiers.ALERT), korean_color)

    def test_unknown_tier_falls_back_to_unknown_label(self):
        i18n.set_language(i18n.KOREAN)
        self.assertEqual(tiers.label("bogus"), "확인불가")


if __name__ == "__main__":
    unittest.main()
