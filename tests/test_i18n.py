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

    def test_scan_window_pause_translations(self):
        self.assertIn("06:00", tr("ko", "tracking.message.window_paused"))
        self.assertIn("06:00", tr("en", "tracking.message.window_paused"))
        self.assertNotEqual(
            tr("ko", "tracking.state.paused"),
            "tracking.state.paused",
        )
        self.assertNotEqual(
            tr("en", "scan.window_closed"),
            "scan.window_closed",
        )
        self.assertIn(
            "22:00",
            tr("en", "settings.scan_window_value", start=22, end=6),
        )
        self.assertIn(
            "06:00",
            tr("ko", "settings.scan_window_value", start=22, end=6),
        )


if __name__ == "__main__":
    unittest.main()
