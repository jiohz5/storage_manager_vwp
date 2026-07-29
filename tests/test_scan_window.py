import unittest
from datetime import datetime

from storage_manager.scan_window import (
    is_within_scan_window,
    managed_scan_window_closed,
)


class ScanWindowTests(unittest.TestCase):
    def test_overnight_window_boundaries(self):
        self.assertTrue(
            is_within_scan_window(
                datetime(2026, 7, 29, 22, 0),
                22,
                6,
            )
        )
        self.assertTrue(
            is_within_scan_window(
                datetime(2026, 7, 30, 5, 59),
                22,
                6,
            )
        )
        self.assertFalse(
            is_within_scan_window(
                datetime(2026, 7, 30, 6, 0),
                22,
                6,
            )
        )
        self.assertFalse(
            is_within_scan_window(
                datetime(2026, 7, 29, 12, 0),
                22,
                6,
            )
        )

    def test_day_window_equal_hours_and_trigger_scope(self):
        noon = datetime(2026, 7, 29, 12, 0)
        self.assertTrue(is_within_scan_window(noon, 9, 17))
        self.assertFalse(
            is_within_scan_window(
                datetime(2026, 7, 29, 18, 0),
                9,
                17,
            )
        )
        self.assertTrue(is_within_scan_window(noon, 6, 6))
        self.assertTrue(
            managed_scan_window_closed("cron", noon, 22, 6)
        )
        self.assertTrue(
            managed_scan_window_closed("gui", noon, 22, 6)
        )
        self.assertFalse(
            managed_scan_window_closed("command", noon, 22, 6)
        )
        self.assertFalse(
            managed_scan_window_closed("direct", noon, 22, 6)
        )


if __name__ == "__main__":
    unittest.main()
