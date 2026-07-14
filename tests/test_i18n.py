import unittest

from storage_manager.i18n import tr


class I18nTests(unittest.TestCase):
    def test_korean_and_english_translations(self):
        self.assertEqual(tr("ko", "tab.dashboard"), "대시보드")
        self.assertEqual(tr("en", "tab.dashboard"), "Dashboard")

    def test_translation_formats_values(self):
        self.assertIn("97", tr("ko", "file.threshold", value=97))
        self.assertEqual(
            tr("en", "last_update.value", value="22:00"),
            "Last update: 22:00",
        )


if __name__ == "__main__":
    unittest.main()
