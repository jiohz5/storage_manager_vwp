import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from storage_manager.collector import DetailScanResult, StorageBackend, UsageSnapshot
from storage_manager.config import (
    Account,
    AccountStore,
    Settings,
    load_store,
    reports_dir,
    save_store,
)
from storage_manager.gui import MainWindow
from storage_manager.scheduler import CronStatus


class GuiI18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_language_menu_switches_ui_and_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "data"
            account_root = root / "accounts"
            account_path = account_root / "project_a"
            account_path.mkdir(parents=True)
            save_store(
                data_dir,
                AccountStore(
                    Settings(monitored_roots=[str(account_root)]),
                    [Account("project_a", str(account_path), account_id="id-a")],
                ),
            )
            report_root = reports_dir(data_dir)
            (report_root / "latest_daily_ko.txt").write_text(
                "일간 보고서",
                encoding="utf-8",
            )
            (report_root / "latest_daily_en.txt").write_text(
                "Daily Report",
                encoding="utf-8",
            )
            backend = StorageBackend(
                name="unit-test",
                read_usage=Mock(
                    return_value=UsageSnapshot("test-fs", 1000, 920, 80, 92)
                ),
                scan_detail=Mock(
                    return_value=DetailScanResult([], True, 0.0)
                ),
                test_mode=True,
            )
            with patch(
                "storage_manager.gui.read_cron_status",
                return_value=CronStatus(False, False, error="not available"),
            ):
                window = MainWindow(data_dir, backend=backend)
            try:
                self.assertEqual(window.tabs.tabText(0), "대시보드")
                self.assertEqual(window.tabs.tabText(2), "추적")
                self.assertTrue(window.action_ko.isChecked())
                self.assertIn("일간 보고서", window.report_text.toPlainText())
                window.change_language("en")
                self.assertEqual(window.tabs.tabText(0), "Dashboard")
                self.assertEqual(window.tabs.tabText(2), "Tracking")
                self.assertEqual(window.btn_add.text(), "Add")
                self.assertTrue(window.action_en.isChecked())
                self.assertIn("Daily Report", window.report_text.toPlainText())
                self.assertEqual(load_store(data_dir).settings.language, "en")
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
