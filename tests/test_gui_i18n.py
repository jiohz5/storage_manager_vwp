import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QHeaderView, QMessageBox

from storage_manager.collector import DetailScanResult, StorageBackend, UsageSnapshot
from storage_manager.config import (
    Account,
    AccountStore,
    Settings,
    load_store,
    reports_dir,
    save_store,
)
from storage_manager.gui import MainWindow, choose_initial_data_dir
from storage_manager.runtime import config_location_file, read_saved_data_dir
from storage_manager.scheduler import CronStatus
from storage_manager.search_index import (
    SearchEntry,
    SearchIndex,
    run_full_index,
    search_db_file,
)


class GuiI18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_first_run_directory_selection_saves_global_pointer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "project" / ".storage-manager-vwp"
            data_dir.mkdir(parents=True)
            with patch(
                "storage_manager.gui.prompt_data_directory",
                return_value=str(data_dir),
            ) as prompt, patch(
                "storage_manager.gui.inspect_data_directory",
                wraps=__import__(
                    "storage_manager.runtime",
                    fromlist=["inspect_data_directory"],
                ).inspect_data_directory,
            ) as inspect:
                selected = choose_initial_data_dir(
                    None,
                    "ko",
                    home=root / "home",
                    environ={},
                )

            self.assertEqual(selected, data_dir.resolve())
            self.assertEqual(
                read_saved_data_dir(home=root / "home", environ={}),
                data_dir.resolve(),
            )
            self.assertEqual(prompt.call_args.kwargs["user_id"], os.environ.get("USERNAME", "") or prompt.call_args.kwargs["user_id"])
            self.assertEqual(
                prompt.call_args.kwargs["pointer_path"],
                config_location_file(home=root / "home", environ={}),
            )
            self.assertFalse(inspect.call_args.kwargs["measure_size"])
            self.assertFalse((Path(__file__).resolve().parent.parent / "data").exists())

    def test_first_run_rejects_unwritable_selection_and_can_cancel(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            blocker = root / "not-a-directory"
            blocker.write_text("x", encoding="ascii")
            with patch(
                "storage_manager.gui.prompt_data_directory",
                side_effect=[str(blocker / "state"), ""],
            ), patch(
                "storage_manager.gui.QMessageBox.warning"
            ) as warning:
                selected = choose_initial_data_dir(
                    None,
                    "ko",
                    home=root / "home",
                    environ={},
                )

            self.assertIsNone(selected)
            self.assertTrue(warning.called)
            self.assertIsNone(read_saved_data_dir(home=root / "home", environ={}))

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
                self.assertIn("15분", window.lbl_capacity_watch.text())
                self.assertTrue(hasattr(window, "btn_tracking_scan_toggle"))
                self.assertTrue(hasattr(window, "btn_tracking_cron_toggle"))
                self.assertTrue(hasattr(window, "btn_notifier_toggle"))
                self.assertFalse(hasattr(window, "btn_notifier_start"))
                self.assertIn(str(data_dir), window.lbl_data_dir_value.text())
                self.assertIn("500", window.lbl_data_dir_value.text())

                window.spin_rapid_growth.setValue(250)
                window.spin_forecast_alert.setValue(8)
                window.spin_forecast_emergency.setValue(3)
                window.spin_capacity_history.setValue(45)
                window.spin_data_size_warning.setValue(600)
                persisted = load_store(data_dir).settings
                self.assertEqual(persisted.rapid_growth_gb, 250)
                self.assertEqual(persisted.forecast_alert_hours, 8)
                self.assertEqual(persisted.forecast_emergency_hours, 3)
                self.assertEqual(persisted.capacity_sample_days, 45)
                self.assertEqual(persisted.data_size_warning_mb, 600)

                project_b = account_root / "project_b"
                project_b.mkdir()
                window.input_name.setText("project_b")
                window.input_path.setText(str(project_b))
                with patch(
                    "storage_manager.gui.same_filesystem",
                    return_value=True,
                ), patch(
                    "storage_manager.gui.QMessageBox.question",
                    return_value=QMessageBox.No,
                ):
                    window.add_account()
                self.assertNotIn("project_b", [item.name for item in window.store.accounts])

                with patch(
                    "storage_manager.gui.same_filesystem",
                    return_value=True,
                ), patch(
                    "storage_manager.gui.QMessageBox.question",
                    return_value=QMessageBox.Yes,
                ):
                    window.add_account()
                self.assertIn("project_b", [item.name for item in window.store.accounts])

                window.change_language("en")
                self.assertEqual(window.tabs.tabText(0), "Dashboard")
                self.assertEqual(window.tabs.tabText(2), "Tracking")
                self.assertEqual(window.btn_add.text(), "Add")
                self.assertEqual(window.btn_notifier_toggle.text(), "Start popup alerts")
                self.assertIn("Capacity watch", window.lbl_capacity_watch.text())
                self.assertTrue(window.action_en.isChecked())
                self.assertIn("Daily Report", window.report_text.toPlainText())
                self.assertEqual(load_store(data_dir).settings.language, "en")
            finally:
                window.close()

    def test_account_autofill_inode_help_and_compact_tracking_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "data"
            account_root = root / "user"
            account_root.mkdir()
            save_store(
                data_dir,
                AccountStore(
                    Settings(monitored_roots=[str(account_root)]),
                    [],
                ),
            )
            with patch(
                "storage_manager.gui.read_cron_status",
                return_value=CronStatus(False, False, error="not available"),
            ):
                window = MainWindow(data_dir)
            try:
                window.input_name.setText("project_a")
                window.input_name.textEdited.emit("project_a")
                self.assertEqual(
                    window.input_path.text(),
                    str(account_root / "project_a"),
                )

                path_b = str(account_root / "project_b")
                window.input_path.setText(path_b)
                window.input_path.textEdited.emit(path_b)
                self.assertEqual(window.input_name.text(), "project_b")

                inode_header = window.table_usage.horizontalHeaderItem(3)
                self.assertEqual(inode_header.text(), "파일 수 한도 (inode)")
                self.assertIn("새 파일", inode_header.toolTip())
                self.assertEqual(window.table_tracking.columnCount(), 7)
                self.assertEqual(
                    window.table_tracking.horizontalHeader().sectionResizeMode(5),
                    QHeaderView.Stretch,
                )
                self.assertFalse(hasattr(window, "btn_tracking_restart"))
                self.assertFalse(hasattr(window, "btn_notifier_restart"))
            finally:
                window.close()

    def test_visible_window_close_explains_background_tray_and_cron(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            save_store(data_dir, AccountStore(Settings(), []))
            with patch(
                "storage_manager.gui.read_cron_status",
                return_value=CronStatus(True, True),
            ):
                window = MainWindow(data_dir)
            window.show()
            self.app.processEvents()
            with patch(
                "storage_manager.gui.read_notifier_status",
                return_value={"state": "running", "pid": 123},
            ), patch(
                "storage_manager.gui.QMessageBox.information"
            ) as information:
                window.close()

            self.assertTrue(information.called)
            message = str(information.call_args.args[2])
            self.assertIn("GUI만 종료", message)
            self.assertIn("트레이", message)
            self.assertIn("디스크 아이콘", message)
            self.assertIn("다시 실행", message)
            self.assertIn("cron", message)
            self.assertIn("GUI 없이도 계속", message)

    def test_late_df_result_after_close_start_does_not_write_database(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "data"
            account_path = root / "user" / "project_a"
            account_path.mkdir(parents=True)
            account = Account("project_a", str(account_path), account_id="id-a")
            save_store(
                data_dir,
                AccountStore(
                    Settings(monitored_roots=[str(account_path.parent)]),
                    [account],
                ),
            )
            with patch(
                "storage_manager.gui.read_cron_status",
                return_value=CronStatus(False, False, error="not available"),
            ):
                window = MainWindow(data_dir)
            try:
                window._closing = True
                window.row_by_account_id = {"id-a": 0}
                window.table_usage.setRowCount(1)
                snapshot = UsageSnapshot("fs", 1000, 500, 500, 50)
                with patch.object(window.db, "upsert_snapshot") as upsert:
                    window.on_df_result("id-a", snapshot)
                upsert.assert_not_called()
            finally:
                window.close()

    def test_admin_pin_reveals_and_hides_search_tab_for_current_session(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            save_store(data_dir, AccountStore(Settings(), []))
            with patch(
                "storage_manager.gui.read_cron_status",
                return_value=CronStatus(False, False, error="not available"),
            ):
                window = MainWindow(data_dir)
            try:
                self.assertEqual(window.tabs.indexOf(window.search_tab), -1)
                with patch(
                    "storage_manager.gui.QInputDialog.getText",
                    return_value=("wrong", True),
                ), patch("storage_manager.gui.QMessageBox.warning") as warning:
                    window.unlock_admin_mode()
                self.assertEqual(window.tabs.indexOf(window.search_tab), -1)
                self.assertTrue(warning.called)

                with patch(
                    "storage_manager.gui.QInputDialog.getText",
                    return_value=("6368", True),
                ):
                    window.unlock_admin_mode()
                self.assertGreaterEqual(window.tabs.indexOf(window.search_tab), 0)
                self.assertEqual(
                    window.tabs.tabText(window.tabs.indexOf(window.search_tab)),
                    "검색",
                )
                window.on_search_error(window.search_request_id, "database is locked")
                self.assertIn("인덱싱 중", window.lbl_search_results.text())
                self.assertIn("잠시 후", window.lbl_search_results.text())

                window.lock_admin_mode()
                self.assertEqual(window.tabs.indexOf(window.search_tab), -1)
            finally:
                window.close()

    def test_search_tab_shows_total_database_and_selected_account_size(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "data"
            account_path = root / "user" / "project_a"
            account_path.mkdir(parents=True)
            (account_path / "indexed.txt").write_text("x", encoding="ascii")
            account = Account(
                "project_a",
                str(account_path),
                account_id="id-a",
                search_enabled=True,
            )
            save_store(
                data_dir,
                AccountStore(
                    Settings(monitored_roots=[str(root / "user")]),
                    [account],
                ),
            )
            index = SearchIndex(search_db_file(data_dir))
            try:
                run_full_index(index, "id-a", account_path, force=True)
                expected_size = index.summary()["db_bytes"]
            finally:
                index.close()
            with patch(
                "storage_manager.gui.read_cron_status",
                return_value=CronStatus(False, False, error="not available"),
            ):
                window = MainWindow(data_dir)
            try:
                with patch(
                    "storage_manager.gui.QInputDialog.getText",
                    return_value=("6368", True),
                ):
                    window.unlock_admin_mode()
                window.refresh_search_status()
                self.assertTrue(
                    self.wait_for(lambda: "전체 항목 1" in window.lbl_search_status.text())
                )

                status_text = window.lbl_search_status.text()
                self.assertIn("전체 항목 1", status_text)
                self.assertIn("선택 계정 1", status_text)
                self.assertIn("완전 인덱스", status_text)
                self.assertGreater(expected_size, 0)
                self.assertTrue(window.btn_search_index_toggle.isEnabled())

                with patch.object(
                    window.thread_pool,
                    "start",
                    side_effect=lambda worker: worker.run(),
                ):
                    window.toggle_search_indexing()
                self.assertFalse(load_store(data_dir).accounts[0].search_enabled)
                index = SearchIndex(search_db_file(data_dir))
                try:
                    self.assertEqual(index.search("id-a"), [])
                finally:
                    index.close()
            finally:
                window.close()

    def test_search_status_refresh_is_dispatched_to_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            save_store(data_dir, AccountStore(Settings(), []))
            with patch(
                "storage_manager.gui.read_cron_status",
                return_value=CronStatus(False, False, error="not available"),
            ):
                window = MainWindow(data_dir)
            try:
                window.admin_unlocked = True
                with patch.object(window.thread_pool, "start") as start:
                    window.refresh_search_status()
                self.assertTrue(start.called)
                self.assertEqual(
                    type(start.call_args.args[0]).__name__,
                    "SearchStatusWorker",
                )
            finally:
                window.close()

    def test_stale_or_locked_search_results_do_not_repopulate_table(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            account_path = Path(temp) / "user" / "project_a"
            account_path.mkdir(parents=True)
            save_store(
                data_dir,
                AccountStore(
                    Settings(monitored_roots=[str(account_path.parent)]),
                    [Account("project_a", str(account_path), account_id="id-a")],
                ),
            )
            with patch(
                "storage_manager.gui.read_cron_status",
                return_value=CronStatus(False, False, error="not available"),
            ):
                window = MainWindow(data_dir)
            try:
                window.admin_unlocked = True
                window.search_request_id = 4
                rows = [SearchEntry("old.dat", "old.dat", "dat", "file")]
                window.on_search_results(3, "id-a", str(account_path), rows)
                self.assertEqual(window.table_search.rowCount(), 0)

                window.lock_admin_mode()
                window.on_search_results(4, "id-a", str(account_path), rows)
                self.assertEqual(window.table_search.rowCount(), 0)
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
