from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    QRunnable,
    QThreadPool,
    QTimer,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QActionGroup,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from storage_manager.collector import (
    RHEL_BACKEND,
    StorageBackend,
    UsageSnapshot,
    usage_color,
    usage_level,
)
from storage_manager.admin_auth import verify_admin_pin
from storage_manager.analytics import capacity_forecast, detect_growth_anomaly
from storage_manager.capacity_watch import read_capacity_watch_status
from storage_manager.config import (
    Account,
    AccountStore,
    ConfigError,
    db_file,
    find_account,
    load_store,
    normalize_account_path,
    reports_dir,
    save_store,
)
from storage_manager.database import Database
from storage_manager.i18n import tr
from storage_manager.notifications import (
    NotificationEvent,
    dispatch_notifications,
    read_notification_status,
)
from storage_manager.notifier import (
    ACTIVE_NOTIFIER_STATES,
    autostart_installed,
    install_autostart as install_notifier_autostart,
    launch_notifier,
    read_notifier_status,
    remove_autostart as remove_notifier_autostart,
    request_notifier_stop,
)
from storage_manager.reports import format_mtime, human_bytes, human_kb
from storage_manager.search_index import SearchIndex, search_db_file
from storage_manager.runtime import (
    RuntimePathError,
    collect_runtime_diagnostics,
    config_location_file,
    current_user_id,
    directory_size_bytes,
    inspect_data_directory,
    resolve_data_dir,
    save_data_dir_location,
    same_filesystem,
    suggested_data_dir,
    write_runtime_diagnostics,
)
from storage_manager.quota import collect_quota
from storage_manager.scheduler import (
    CronStatus,
    install_cron,
    read_cron_status,
    remove_cron,
)
from storage_manager.tracking import (
    ACTIVE_STATES,
    launch_background_scan,
    next_capacity_run,
    next_scheduled_run,
    process_is_alive,
    read_scan_status,
    request_scan_stop,
)


def prompt_data_directory(
    parent,
    language: str,
    initial: Path,
    user_id: str,
    pointer_path: Path,
) -> Optional[str]:
    dialog = QDialog(parent)
    dialog.setWindowTitle(tr(language, "bootstrap.title"))
    dialog.setMinimumWidth(640)
    layout = QVBoxLayout(dialog)
    info = QLabel(
        tr(
            language,
            "bootstrap.info",
            user=user_id,
            pointer=pointer_path,
        )
    )
    info.setWordWrap(True)
    layout.addWidget(info)
    path_label = QLabel(tr(language, "bootstrap.select"))
    layout.addWidget(path_label)
    path_input = QLineEdit(str(initial))
    path_input.selectAll()
    layout.addWidget(path_input)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec_() != QDialog.Accepted:
        return None
    return path_input.text().strip() or None


def choose_initial_data_dir(
    parent,
    language: str,
    home: Optional[Path] = None,
    environ=None,
) -> Optional[Path]:
    values = os.environ if environ is None else environ
    home_path = Path(home or Path.home())
    user_id = current_user_id()
    pointer_path = config_location_file(home=home_path, environ=values)
    initial = suggested_data_dir(user_id, home_path)
    while True:
        selected = prompt_data_directory(
            parent,
            language,
            initial=initial,
            user_id=user_id,
            pointer_path=pointer_path,
        )
        if not selected:
            return None
        try:
            status = inspect_data_directory(Path(selected), measure_size=False)
            save_data_dir_location(status.path, home=home, environ=environ)
            payload = collect_runtime_diagnostics(
                status.path,
                measure_data_size=False,
            )
            write_runtime_diagnostics(status.path, payload)
        except RuntimePathError as exc:
            QMessageBox.warning(
                parent,
                tr(language, "bootstrap.invalid_title"),
                tr(
                    language,
                    "bootstrap.invalid",
                    path=selected,
                    error=exc,
                ),
            )
            initial = Path(selected)
            continue
        return status.path


def resolve_gui_data_dir(
    explicit: Optional[str],
    parent,
    language: str,
) -> Optional[Path]:
    try:
        candidate = (
            resolve_data_dir(Path(explicit))
            if explicit
            else resolve_data_dir(environ=os.environ)
        )
        if candidate is None:
            return choose_initial_data_dir(parent, language)
        return inspect_data_directory(candidate, measure_size=False).path
    except RuntimePathError as exc:
        raise ConfigError(str(exc)) from exc


class DfWorkerSignals(QObject):
    result = pyqtSignal(str, object)
    error = pyqtSignal(str, str)
    finished = pyqtSignal()


class DfWorker(QRunnable):
    def __init__(
        self,
        account: Account,
        timeout_seconds: int,
        backend: StorageBackend,
        quota_command: List[str],
        quota_timeout_seconds: int,
    ):
        super().__init__()
        self.account = account
        self.timeout_seconds = timeout_seconds
        self.backend = backend
        self.quota_command = quota_command
        self.quota_timeout_seconds = quota_timeout_seconds
        self.signals = DfWorkerSignals()

    @pyqtSlot()
    def run(self) -> None:
        try:
            snapshot = self.backend.read_usage(self.account.path, self.timeout_seconds)
            try:
                quota = collect_quota(
                    self.quota_command,
                    self.account.name,
                    self.account.path,
                    self.quota_timeout_seconds,
                )
            except Exception as exc:
                snapshot = replace(snapshot, quota_error=str(exc))
            else:
                if quota is not None:
                    snapshot = replace(
                        snapshot,
                        quota_used_kb=quota.used_kb,
                        quota_limit_kb=quota.limit_kb,
                        quota_use_pct=quota.use_pct,
                    )
        except Exception as exc:
            self.signals.error.emit(self.account.account_id, str(exc))
        else:
            self.signals.result.emit(self.account.account_id, snapshot)
        finally:
            self.signals.finished.emit()


class SearchWorkerSignals(QObject):
    result = pyqtSignal(int, str, str, object)
    error = pyqtSignal(int, str)


class SearchStatusWorkerSignals(QObject):
    result = pyqtSignal(int, str, object, object)
    error = pyqtSignal(int, str)


class SearchStatusWorker(QRunnable):
    def __init__(self, data_dir: Path, request_id: int, account_id: str):
        super().__init__()
        self.data_dir = data_dir
        self.request_id = request_id
        self.account_id = account_id
        self.signals = SearchStatusWorkerSignals()

    @pyqtSlot()
    def run(self) -> None:
        index = None
        try:
            index = SearchIndex(
                search_db_file(self.data_dir),
                timeout_seconds=1.0,
            )
            summary = index.summary()
            status = index.account_status(self.account_id) if self.account_id else None
        except Exception as exc:
            self.signals.error.emit(self.request_id, str(exc))
        else:
            self.signals.result.emit(
                self.request_id,
                self.account_id,
                summary,
                status,
            )
        finally:
            if index is not None:
                index.close()


class SearchCleanupWorkerSignals(QObject):
    result = pyqtSignal(str)
    error = pyqtSignal(str, str)


class SearchCleanupWorker(QRunnable):
    def __init__(self, data_dir: Path, account_id: str):
        super().__init__()
        self.data_dir = data_dir
        self.account_id = account_id
        self.signals = SearchCleanupWorkerSignals()

    @pyqtSlot()
    def run(self) -> None:
        index = None
        try:
            index = SearchIndex(
                search_db_file(self.data_dir),
                timeout_seconds=1.0,
            )
            index.remove_account(self.account_id)
        except Exception as exc:
            self.signals.error.emit(self.account_id, str(exc))
        else:
            self.signals.result.emit(self.account_id)
        finally:
            if index is not None:
                index.close()


class SearchWorker(QRunnable):
    def __init__(
        self,
        data_dir: Path,
        request_id: int,
        account_id: str,
        account_path: str,
        name: str,
        mode: str,
        extension: str,
        entry_type: str,
    ):
        super().__init__()
        self.data_dir = data_dir
        self.request_id = request_id
        self.account_id = account_id
        self.account_path = account_path
        self.name = name
        self.mode = mode
        self.extension = extension
        self.entry_type = entry_type
        self.signals = SearchWorkerSignals()

    @pyqtSlot()
    def run(self) -> None:
        index = None
        try:
            index = SearchIndex(
                search_db_file(self.data_dir),
                timeout_seconds=2.0,
            )
            status = index.account_status(self.account_id)
            indexed_path = str(status.get("account_path") or "")
            if indexed_path and os.path.normcase(os.path.abspath(indexed_path)) != os.path.normcase(
                os.path.abspath(self.account_path)
            ):
                raise RuntimeError("Search index path changed; rebuild the index")
            rows = index.search(
                self.account_id,
                self.name,
                mode=self.mode,
                extension=self.extension,
                entry_type=self.entry_type,
                limit=500,
            )
        except Exception as exc:
            self.signals.error.emit(self.request_id, str(exc))
        else:
            self.signals.result.emit(
                self.request_id,
                self.account_id,
                indexed_path or self.account_path,
                rows,
            )
        finally:
            if index is not None:
                index.close()


class TrendChartWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.points: List[Tuple[str, int]] = []
        self.language = "ko"
        self.setMinimumHeight(280)

    def set_points(self, points: List[Tuple[str, int]]) -> None:
        self.points = points
        self.update()

    def set_language(self, language: str) -> None:
        self.language = language
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#fbfaf5"))

        left, top, right, bottom = 52, 22, 22, 38
        plot_width = max(1, self.width() - left - right)
        plot_height = max(1, self.height() - top - bottom)

        for percent in (0, 25, 50, 75, 100):
            y = top + plot_height - int(percent / 100.0 * plot_height)
            painter.setPen(QPen(QColor("#d9ded8"), 1))
            painter.drawLine(left, y, left + plot_width, y)
            painter.setPen(QPen(QColor("#56605c"), 1))
            painter.drawText(7, y + 4, f"{percent}%")

        painter.setPen(QPen(QColor("#56605c"), 1))
        painter.drawLine(left, top, left, top + plot_height)
        painter.drawLine(left, top + plot_height, left + plot_width, top + plot_height)

        if not self.points:
            painter.drawText(left + 12, top + 24, tr(self.language, "chart.no_data"))
            return

        coordinates = []
        denominator = max(1, len(self.points) - 1)
        for index, (day, percent) in enumerate(self.points):
            x = left + int(index / denominator * plot_width)
            bounded = max(0, min(100, percent))
            y = top + plot_height - int(bounded / 100.0 * plot_height)
            coordinates.append((x, y, day))

        painter.setPen(QPen(QColor("#087f5b"), 3))
        for index in range(1, len(coordinates)):
            painter.drawLine(
                coordinates[index - 1][0],
                coordinates[index - 1][1],
                coordinates[index][0],
                coordinates[index][1],
            )
        for x, y, _ in coordinates:
            painter.drawPoint(x, y)

        painter.setPen(QPen(QColor("#35403b"), 1))
        painter.drawText(left, top + plot_height + 23, coordinates[0][2])
        if len(coordinates) > 1:
            painter.drawText(left + plot_width - 88, top + plot_height + 23, coordinates[-1][2])


class SortableTableWidgetItem(QTableWidgetItem):
    def __init__(self, text: str, sort_key: object):
        super().__init__(text)
        self.sort_key = sort_key

    def __lt__(self, other) -> bool:
        if isinstance(other, SortableTableWidgetItem):
            return self.sort_key < other.sort_key
        return super().__lt__(other)


def dashboard_status_rank(use_pct: int, alert_threshold: int) -> int:
    if use_pct >= alert_threshold:
        return 3
    if use_pct >= 90:
        return 2
    return 1


DASHBOARD_ACCOUNT_ID_ROLE = Qt.UserRole + 1
DASHBOARD_UNAVAILABLE_SORT_KEY = -1


class MainWindow(QMainWindow):
    def __init__(
        self,
        data_dir: Path,
        backend: StorageBackend = RHEL_BACKEND,
    ):
        super().__init__()
        self.data_dir = data_dir
        self.backend = backend
        self.store: AccountStore = load_store(data_dir)
        self.language = self.store.settings.language
        self.db = Database(db_file(data_dir))
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(4)
        self.refresh_pending = 0
        self.refresh_again = False
        self.refresh_alerts: List[str] = []
        self.alerted_accounts = set()
        self.current_snapshots: Dict[str, UsageSnapshot] = {}
        self.dashboard_sort_column: Optional[int] = None
        self.dashboard_sort_order = Qt.AscendingOrder
        self.cron_status = CronStatus(False, False, error="not checked")
        self.restart_pending = False
        self.notifier_restart_pending = False
        self.launch_pending_pid = 0
        self.admin_unlocked = False
        self.search_request_id = 0
        self.search_status_request_id = 0
        self._closing = False
        self._explicit_exit = False

        for account in self.store.accounts:
            self.db.backfill_account(account.account_id, account.name, account.path)

        self._configure_window_controls()
        self.resize(1280, 780)
        self._apply_style()
        self._build_file_menu()
        self._build_language_menu()
        self._build_admin_menu()

        self.tabs = QTabWidget()
        self.dashboard_tab = QWidget()
        self.accounts_tab = QWidget()
        self.tracking_tab = QWidget()
        self.trend_tab = QWidget()
        self.reports_tab = QWidget()
        self.settings_tab = QWidget()
        self.search_tab = QWidget()
        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        self.tabs.addTab(self.accounts_tab, "Accounts")
        self.tabs.addTab(self.tracking_tab, "Tracking")
        self.tabs.addTab(self.trend_tab, "Trend")
        self.tabs.addTab(self.reports_tab, "Reports")
        self.tabs.addTab(self.settings_tab, "Setup")
        self.setCentralWidget(self.tabs)

        self._build_dashboard_tab()
        self._build_accounts_tab()
        self._build_tracking_tab()
        self._build_trend_tab()
        self._build_reports_tab()
        self._build_settings_tab()
        self._build_search_tab()
        self._retranslate_ui()
        self.refresh_accounts_table()
        self.refresh_tracking(check_cron=True)
        self.refresh_trend_account_list()
        self.refresh_report_view()

        self.timer = QTimer(self)
        self.timer.setInterval(self.store.settings.refresh_seconds * 1000)
        self.timer.timeout.connect(self.refresh_dashboard)
        self.timer.start()
        self.tracking_timer = QTimer(self)
        self.tracking_timer.setInterval(2000)
        self.tracking_timer.timeout.connect(self.refresh_tracking)
        self.tracking_timer.start()
        self.initial_refresh_timer = QTimer(self)
        self.initial_refresh_timer.setSingleShot(True)
        self.initial_refresh_timer.timeout.connect(self.refresh_dashboard)
        self.initial_refresh_timer.start(0)

    def _apply_style(self) -> None:
        families = set(QFontDatabase().families())
        if self.language == "ko":
            candidates = ["Malgun Gothic", "Noto Sans CJK KR", "NanumGothic", "Noto Sans KR"]
        else:
            candidates = ["DejaVu Sans", "Noto Sans"]
        font_family = next((name for name in candidates if name in families), candidates[0])
        self.setFont(QFont(font_family, 10))
        stylesheet = (
            """
            QMainWindow, QWidget { background: #eef1eb; color: #24302b; font-family: "__FONT__"; }
            QTabWidget::pane { border: 1px solid #c9d0c7; background: #f7f6f0; }
            QTabBar::tab { background: #dfe5dc; padding: 10px 18px; margin-right: 2px; }
            QTabBar::tab:selected { background: #087f5b; color: white; }
            QPushButton { background: #174c3c; color: white; border: 0; padding: 7px 12px; }
            QPushButton:hover { background: #087f5b; }
            QPushButton:disabled { background: #9aa59f; }
            QLineEdit, QSpinBox, QComboBox, QTextEdit { background: white; border: 1px solid #aeb8b1; padding: 5px; }
            QTableWidget { background: #fbfaf5; gridline-color: #d7ddd6; alternate-background-color: #f0f3ed; }
            QHeaderView::section { background: #263c34; color: white; padding: 7px; border: 0; }
            """
        ).replace("__FONT__", font_family)
        self.setStyleSheet(stylesheet)

    def t(self, key: str, **values: object) -> str:
        return tr(self.language, key, **values)

    def _configure_window_controls(self) -> None:
        flags = self.windowFlags()
        flags |= Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        flags &= ~Qt.WindowCloseButtonHint
        self.setWindowFlags(flags)

    def _build_file_menu(self) -> None:
        self.file_menu = self.menuBar().addMenu("")
        self.action_minimize = QAction(self)
        self.action_full_exit = QAction(self)
        self.action_minimize.triggered.connect(self.minimize_window)
        self.action_full_exit.triggered.connect(lambda: self.request_full_exit())
        self.file_menu.addAction(self.action_minimize)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.action_full_exit)

    def minimize_window(self) -> None:
        self.showMinimized()

    def _run_full_exit_steps(self) -> Tuple[List[str], List[str]]:
        completed: List[str] = []
        failed: List[str] = []

        def run_step(key, operation) -> None:
            label = self.t(key)
            try:
                operation()
                completed.append(label)
            except Exception as exc:
                failed.append(f"{label}: {exc}")

        run_step("exit.step.cron", remove_cron)
        run_step("exit.step.autostart", remove_notifier_autostart)

        def stop_notifier_if_active() -> None:
            status = read_notifier_status(self.data_dir)
            if str(status.get("state") or "never") in ACTIVE_NOTIFIER_STATES:
                requested = request_notifier_stop(self.data_dir)
                if not requested:
                    latest = read_notifier_status(self.data_dir)
                    if str(latest.get("state") or "never") in ACTIVE_NOTIFIER_STATES:
                        raise RuntimeError(self.t("exit.stop_request_failed"))

        def stop_scan_if_active() -> None:
            status = self._tracking_runtime_status()
            if str(status.get("state") or "never") in ACTIVE_STATES:
                requested = request_scan_stop(self.data_dir)
                if not requested:
                    latest = self._tracking_runtime_status()
                    if str(latest.get("state") or "never") in ACTIVE_STATES:
                        raise RuntimeError(self.t("exit.stop_request_failed"))

        run_step("exit.step.notifier", stop_notifier_if_active)
        run_step("exit.step.scan", stop_scan_if_active)
        return completed, failed

    def request_full_exit(self) -> None:
        answer = QMessageBox.question(
            self,
            self.t("exit.title"),
            self.t("exit.confirm"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        completed, failed = self._run_full_exit_steps()
        if failed:
            QMessageBox.critical(
                self,
                self.t("exit.failed.title"),
                self.t(
                    "exit.failed.message",
                    completed="\n".join(completed) or self.t("exit.none"),
                    failed="\n".join(failed),
                ),
            )
            self.refresh_tracking(check_cron=True)
            return

        self._explicit_exit = True
        self.close()

    def _build_language_menu(self) -> None:
        self.language_menu = self.menuBar().addMenu("")
        self.language_group = QActionGroup(self)
        self.language_group.setExclusive(True)
        self.action_ko = QAction(self)
        self.action_ko.setCheckable(True)
        self.action_ko.setData("ko")
        self.action_en = QAction(self)
        self.action_en.setCheckable(True)
        self.action_en.setData("en")
        for action in (self.action_ko, self.action_en):
            self.language_group.addAction(action)
            self.language_menu.addAction(action)
        self.language_group.triggered.connect(
            lambda action: self.change_language(str(action.data()))
        )

    def _build_admin_menu(self) -> None:
        self.admin_menu = self.menuBar().addMenu("")
        self.action_admin_unlock = QAction(self)
        self.action_admin_lock = QAction(self)
        self.action_admin_unlock.triggered.connect(self.unlock_admin_mode)
        self.action_admin_lock.triggered.connect(self.lock_admin_mode)
        self.admin_menu.addAction(self.action_admin_unlock)
        self.admin_menu.addAction(self.action_admin_lock)
        self.action_admin_lock.setEnabled(False)

    def change_language(self, language: str) -> None:
        if language == self.language:
            return
        self.language = language
        self.store.settings.language = language
        save_store(self.data_dir, self.store)
        self._apply_style()
        self._retranslate_ui()
        self.refresh_dashboard()
        self.refresh_tracking()
        self.refresh_trend_data()
        self.refresh_report_view()

    def unlock_admin_mode(self) -> None:
        if self.admin_unlocked:
            self.tabs.setCurrentWidget(self.search_tab)
            return
        pin, accepted = QInputDialog.getText(
            self,
            self.t("admin.title"),
            self.t("admin.prompt"),
            QLineEdit.Password,
        )
        if not accepted:
            return
        if not verify_admin_pin(pin):
            QMessageBox.warning(
                self,
                self.t("admin.denied_title"),
                self.t("admin.denied"),
            )
            return
        self.admin_unlocked = True
        self.tabs.addTab(self.search_tab, self.t("tab.search"))
        self.action_admin_unlock.setEnabled(False)
        self.action_admin_lock.setEnabled(True)
        self.refresh_search_accounts()
        self.refresh_search_status()
        self.tabs.setCurrentWidget(self.search_tab)

    def lock_admin_mode(self) -> None:
        self.search_request_id += 1
        self.search_status_request_id += 1
        index = self.tabs.indexOf(self.search_tab)
        if index >= 0:
            self.tabs.removeTab(index)
        self.admin_unlocked = False
        self.action_admin_unlock.setEnabled(True)
        self.action_admin_lock.setEnabled(False)
        self.input_search_name.clear()
        self.input_search_extension.clear()
        self.table_search.setRowCount(0)

    def refresh_search_accounts(self) -> None:
        current_id = self.combo_search_account.currentData()
        self.combo_search_account.blockSignals(True)
        self.combo_search_account.clear()
        for account in self.store.accounts:
            self.combo_search_account.addItem(account.name, account.account_id)
        if current_id:
            selected = self.combo_search_account.findData(current_id)
            if selected >= 0:
                self.combo_search_account.setCurrentIndex(selected)
        self.combo_search_account.blockSignals(False)

    def _selected_search_account(self):
        account_id = self.combo_search_account.currentData()
        return find_account(self.store, str(account_id)) if account_id else None

    def refresh_search_status(self, *_args) -> None:
        if not self.admin_unlocked or self._closing:
            return
        account = self._selected_search_account()
        account_id = account.account_id if account else ""
        self.search_status_request_id += 1
        request_id = self.search_status_request_id
        self.lbl_search_status.setText(self.t("search.status_loading"))
        self.btn_search_index_toggle.setEnabled(False)
        self.btn_search_run.setEnabled(False)
        worker = SearchStatusWorker(self.data_dir, request_id, account_id)
        worker.signals.result.connect(self.on_search_status_result)
        worker.signals.error.connect(self.on_search_status_error)
        self.thread_pool.start(worker)

    @pyqtSlot(int, str, object, object)
    def on_search_status_result(
        self,
        request_id: int,
        account_id: str,
        summary,
        status,
    ) -> None:
        if (
            self._closing
            or not self.admin_unlocked
            or request_id != self.search_status_request_id
        ):
            return
        account = self._selected_search_account()
        current_id = account.account_id if account else ""
        if account_id != current_id:
            return

        if account is None or status is None:
            self.lbl_search_status.setText(
                self.t(
                    "search.status_no_account",
                    size=human_bytes(int(summary["db_bytes"])),
                    total=int(summary["total_entries"]),
                )
            )
            self.btn_search_index_toggle.setEnabled(False)
            self.btn_search_run.setEnabled(False)
            return

        state_key = f"search.state.{status['state']}"
        self.lbl_search_status.setText(
            self.t(
                "search.status",
                size=human_bytes(int(summary["db_bytes"])),
                total=int(summary["total_entries"]),
                selected=int(status["entries"]),
                state=self.t(state_key),
                files=int(status["files_indexed"]),
                dirs=int(status["dirs_indexed"]),
                full=status["completed_at"] or "-",
                incremental=status["last_incremental_at"] or "-",
                error=status["error"] or "-",
            )
        )
        self.btn_search_index_toggle.setEnabled(True)
        self.btn_search_run.setEnabled(bool(status["entries"]))
        self.btn_search_index_toggle.setText(
            self.t(
                "search.disable_index"
                if account.search_enabled
                else "search.enable_index"
            )
        )

    @pyqtSlot(int, str)
    def on_search_status_error(self, request_id: int, error: str) -> None:
        if (
            self._closing
            or not self.admin_unlocked
            or request_id != self.search_status_request_id
        ):
            return
        self.lbl_search_status.setText(
            self.t(
                "search.locked"
                if "locked" in error.casefold()
                else "search.status_error",
                error=error,
            )
        )
        self.btn_search_index_toggle.setEnabled(False)
        self.btn_search_run.setEnabled(False)

    def toggle_search_indexing(self) -> None:
        account = self._selected_search_account()
        if account is None:
            return
        was_enabled = account.search_enabled
        account.search_enabled = not account.search_enabled
        save_store(self.data_dir, self.store)
        self.search_request_id += 1
        self.table_search.setRowCount(0)
        if was_enabled:
            self._schedule_search_cleanup(account.account_id)
        else:
            self.refresh_search_status()

    def _schedule_search_cleanup(self, account_id: str) -> None:
        worker = SearchCleanupWorker(self.data_dir, account_id)
        worker.signals.result.connect(self.on_search_cleanup_finished)
        worker.signals.error.connect(self.on_search_cleanup_error)
        self.thread_pool.start(worker)

    @pyqtSlot(str)
    def on_search_cleanup_finished(self, _account_id: str) -> None:
        if self.admin_unlocked and not self._closing:
            self.refresh_search_status()

    @pyqtSlot(str, str)
    def on_search_cleanup_error(self, _account_id: str, error: str) -> None:
        if self.admin_unlocked and not self._closing:
            self.lbl_search_status.setText(
                self.t("search.cleanup_error", error=error)
            )

    def run_search(self) -> None:
        account = self._selected_search_account()
        if account is None:
            return
        self.search_request_id += 1
        request_id = self.search_request_id
        self.btn_search_run.setEnabled(False)
        self.lbl_search_results.setText(self.t("search.running"))
        worker = SearchWorker(
            self.data_dir,
            request_id,
            account.account_id,
            account.path,
            self.input_search_name.text().strip(),
            str(self.combo_search_mode.currentData()),
            self.input_search_extension.text().strip(),
            str(self.combo_search_type.currentData()),
        )
        worker.signals.result.connect(self.on_search_results)
        worker.signals.error.connect(self.on_search_error)
        self.thread_pool.start(worker)

    @pyqtSlot(int, str, str, object)
    def on_search_results(
        self,
        request_id: int,
        account_id: str,
        indexed_path: str,
        rows,
    ) -> None:
        if (
            self._closing
            or not self.admin_unlocked
            or request_id != self.search_request_id
        ):
            return
        account = find_account(self.store, account_id)
        selected = self._selected_search_account()
        if (
            account is None
            or selected is None
            or selected.account_id != account_id
            or os.path.normcase(os.path.abspath(account.path))
            != os.path.normcase(os.path.abspath(indexed_path))
        ):
            return
        self.btn_search_run.setEnabled(True)
        self.table_search.setRowCount(len(rows))
        for row_number, row in enumerate(rows):
            full_path = str(Path(indexed_path) / Path(row.relative_path))
            values = [row.name, row.entry_type, row.extension or "-", full_path]
            for column, value in enumerate(values):
                self.table_search.setItem(row_number, column, QTableWidgetItem(value))
        self.lbl_search_results.setText(self.t("search.results", count=len(rows)))

    @pyqtSlot(int, str)
    def on_search_error(self, request_id: int, error: str) -> None:
        if (
            self._closing
            or not self.admin_unlocked
            or request_id != self.search_request_id
        ):
            return
        self.btn_search_run.setEnabled(True)
        if "locked" in error.casefold():
            self.lbl_search_results.setText(self.t("search.locked"))
        else:
            self.lbl_search_results.setText(self.t("search.failed", error=error))

    def closeEvent(self, event) -> None:
        if not self._explicit_exit:
            event.ignore()
            self.showMinimized()
            return
        self._closing = True
        self.search_request_id += 1
        self.search_status_request_id += 1
        self.timer.stop()
        self.tracking_timer.stop()
        self.initial_refresh_timer.stop()
        self.thread_pool.waitForDone(2000)
        QCoreApplication.sendPostedEvents(self, QEvent.MetaCall)
        QCoreApplication.removePostedEvents(self)
        self.db.close()
        super().closeEvent(event)

    def sort_dashboard(self, column: int) -> None:
        if self.dashboard_sort_column == column:
            self.dashboard_sort_order = (
                Qt.DescendingOrder
                if self.dashboard_sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            self.dashboard_sort_column = column
            self.dashboard_sort_order = Qt.AscendingOrder
        self._apply_dashboard_sort()

    def _apply_dashboard_sort(self) -> None:
        header = self.table_usage.horizontalHeader()
        if self.dashboard_sort_column is None:
            header.setSortIndicatorShown(False)
            return
        header.setSortIndicator(
            self.dashboard_sort_column,
            self.dashboard_sort_order,
        )
        header.setSortIndicatorShown(True)
        self.table_usage.sortItems(
            self.dashboard_sort_column,
            self.dashboard_sort_order,
        )

    def _dashboard_item(
        self,
        text: str,
        sort_key: object,
        account_id: Optional[str] = None,
    ) -> SortableTableWidgetItem:
        item = SortableTableWidgetItem(text, sort_key)
        if account_id is not None:
            item.setData(DASHBOARD_ACCOUNT_ID_ROLE, account_id)
        return item

    def _dashboard_row(self, account_id: str) -> Optional[int]:
        for row in range(self.table_usage.rowCount()):
            item = self.table_usage.item(row, 0)
            if (
                item is not None
                and item.data(DASHBOARD_ACCOUNT_ID_ROLE) == account_id
            ):
                return row
        return None

    def _build_dashboard_tab(self) -> None:
        layout = QVBoxLayout(self.dashboard_tab)
        controls = QHBoxLayout()
        self.btn_refresh = QPushButton()
        self.btn_refresh.clicked.connect(self.refresh_dashboard)
        controls.addWidget(self.btn_refresh)
        controls.addStretch(1)
        self.lbl_last_update = QLabel()
        controls.addWidget(self.lbl_last_update)
        layout.addLayout(controls)
        self.lbl_shared_filesystems = QLabel()
        self.lbl_shared_filesystems.setWordWrap(True)
        layout.addWidget(self.lbl_shared_filesystems)

        self.table_usage = QTableWidget(0, 9)
        self.table_usage.setAlternatingRowColors(True)
        self.table_usage.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_usage.setSortingEnabled(False)
        header = self.table_usage.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.sectionClicked.connect(self.sort_dashboard)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(8, QHeaderView.Stretch)
        layout.addWidget(self.table_usage)

    def _build_accounts_tab(self) -> None:
        layout = QVBoxLayout(self.accounts_tab)
        self.lbl_allowed_roots = QLabel()
        self.lbl_allowed_roots.setWordWrap(True)
        layout.addWidget(self.lbl_allowed_roots)

        form = QGridLayout()
        self.input_name = QLineEdit()
        self.input_path = QLineEdit()
        self.input_name.textEdited.connect(self.sync_path_from_name)
        self.input_path.textEdited.connect(self.sync_name_from_path)
        self.btn_add = QPushButton()
        self.btn_add.clicked.connect(self.add_account)
        self.btn_update = QPushButton()
        self.btn_update.clicked.connect(self.update_selected_account)
        self.btn_delete = QPushButton()
        self.btn_delete.clicked.connect(self.delete_selected_account)
        self.btn_clear = QPushButton()
        self.btn_clear.clicked.connect(self.clear_account_form)
        self.lbl_name = QLabel()
        self.lbl_path = QLabel()
        form.addWidget(self.lbl_name, 0, 0)
        form.addWidget(self.input_name, 0, 1)
        form.addWidget(self.lbl_path, 1, 0)
        form.addWidget(self.input_path, 1, 1)
        form.addWidget(self.btn_add, 0, 2)
        form.addWidget(self.btn_update, 0, 3)
        form.addWidget(self.btn_delete, 1, 2)
        form.addWidget(self.btn_clear, 1, 3)
        layout.addLayout(form)

        self.table_accounts = QTableWidget(0, 3)
        self.table_accounts.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_accounts.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_accounts.setAlternatingRowColors(True)
        self.table_accounts.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_accounts.itemSelectionChanged.connect(self.load_selected_account)
        self.table_accounts.itemChanged.connect(self.on_enabled_changed)
        layout.addWidget(self.table_accounts)

    def _build_tracking_tab(self) -> None:
        layout = QVBoxLayout(self.tracking_tab)
        self.lbl_tracking_info = QLabel()
        self.lbl_tracking_info.setWordWrap(True)
        self.lbl_tracking_info.setStyleSheet(
            "background: #dce9e2; border-left: 5px solid #087f5b; padding: 10px;"
        )
        layout.addWidget(self.lbl_tracking_info)

        status_grid = QGridLayout()
        self.lbl_tracking_cron = QLabel()
        self.lbl_tracking_cron_value = QLabel()
        self.lbl_tracking_next = QLabel()
        self.lbl_tracking_next_value = QLabel()
        self.lbl_tracking_process = QLabel()
        self.lbl_tracking_process_value = QLabel()
        self.lbl_tracking_current = QLabel()
        self.lbl_tracking_current_value = QLabel()
        self.lbl_tracking_last = QLabel()
        self.lbl_tracking_last_value = QLabel()
        self.lbl_tracking_notification = QLabel()
        self.lbl_tracking_notification_value = QLabel()
        self.lbl_capacity_watch = QLabel()
        self.lbl_capacity_watch_value = QLabel()
        self.lbl_notifier = QLabel()
        self.lbl_notifier_value = QLabel()
        self.lbl_notifier_autostart = QLabel()
        self.lbl_notifier_autostart_value = QLabel()
        status_grid.addWidget(self.lbl_tracking_cron, 0, 0)
        status_grid.addWidget(self.lbl_tracking_cron_value, 0, 1)
        status_grid.addWidget(self.lbl_tracking_next, 0, 2)
        status_grid.addWidget(self.lbl_tracking_next_value, 0, 3)
        status_grid.addWidget(self.lbl_tracking_process, 1, 0)
        status_grid.addWidget(self.lbl_tracking_process_value, 1, 1)
        status_grid.addWidget(self.lbl_tracking_current, 1, 2)
        status_grid.addWidget(self.lbl_tracking_current_value, 1, 3)
        status_grid.addWidget(self.lbl_tracking_last, 2, 0)
        status_grid.addWidget(self.lbl_tracking_last_value, 2, 1, 1, 3)
        status_grid.addWidget(self.lbl_tracking_notification, 3, 0)
        status_grid.addWidget(self.lbl_tracking_notification_value, 3, 1, 1, 3)
        status_grid.addWidget(self.lbl_capacity_watch, 4, 0)
        status_grid.addWidget(self.lbl_capacity_watch_value, 4, 1, 1, 3)
        status_grid.addWidget(self.lbl_notifier, 5, 0)
        status_grid.addWidget(self.lbl_notifier_value, 5, 1)
        status_grid.addWidget(self.lbl_notifier_autostart, 5, 2)
        status_grid.addWidget(self.lbl_notifier_autostart_value, 5, 3)
        status_grid.setColumnStretch(1, 1)
        status_grid.setColumnStretch(3, 1)
        layout.addLayout(status_grid)

        controls = QHBoxLayout()
        self.btn_tracking_refresh = QPushButton()
        self.btn_tracking_refresh.clicked.connect(lambda: self.refresh_tracking(check_cron=True))
        self.btn_tracking_cron_toggle = QPushButton()
        self.btn_tracking_cron_toggle.clicked.connect(self.toggle_tracking_cron)
        self.btn_tracking_scan_toggle = QPushButton()
        self.btn_tracking_scan_toggle.clicked.connect(self.toggle_tracking_scan)
        self.btn_notifier_toggle = QPushButton()
        self.btn_notifier_toggle.clicked.connect(self.toggle_notifier)
        self.check_notifier_autostart = QCheckBox()
        self.check_notifier_autostart.stateChanged.connect(
            self.toggle_notifier_autostart
        )
        for button in (
            self.btn_tracking_refresh,
            self.btn_tracking_cron_toggle,
            self.btn_tracking_scan_toggle,
            self.btn_notifier_toggle,
        ):
            controls.addWidget(button)
        controls.addWidget(self.check_notifier_autostart)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.table_tracking = QTableWidget(0, 7)
        self.table_tracking.setAlternatingRowColors(True)
        self.table_tracking.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_tracking.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_tracking.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        layout.addWidget(self.table_tracking)

    def _build_trend_tab(self) -> None:
        layout = QVBoxLayout(self.trend_tab)
        header = QHBoxLayout()
        self.combo_trend_account = QComboBox()
        self.combo_trend_account.currentIndexChanged.connect(self.refresh_trend_data)
        self.lbl_trend_account = QLabel()
        header.addWidget(self.lbl_trend_account)
        header.addWidget(self.combo_trend_account)
        self.btn_trend_refresh = QPushButton()
        self.btn_trend_refresh.clicked.connect(self.refresh_trend_data)
        header.addWidget(self.btn_trend_refresh)
        header.addStretch(1)
        layout.addLayout(header)
        self.chart = TrendChartWidget()
        layout.addWidget(self.chart)
        self.baseline_progress_bar = QProgressBar()
        self.baseline_progress_bar.setTextVisible(True)
        self.baseline_progress_bar.hide()
        layout.addWidget(self.baseline_progress_bar)
        self.lbl_baseline_progress = QLabel()
        layout.addWidget(self.lbl_baseline_progress)
        self.lbl_growth = QLabel()
        self.lbl_growth.setWordWrap(True)
        layout.addWidget(self.lbl_growth)
        self.lbl_activity = QLabel()
        self.lbl_activity.setWordWrap(True)
        layout.addWidget(self.lbl_activity)
        self.lbl_forecast = QLabel()
        self.lbl_forecast.setWordWrap(True)
        layout.addWidget(self.lbl_forecast)
        self.lbl_anomaly = QLabel()
        self.lbl_anomaly.setWordWrap(True)
        layout.addWidget(self.lbl_anomaly)

    def _build_reports_tab(self) -> None:
        layout = QVBoxLayout(self.reports_tab)
        header = QHBoxLayout()
        self.combo_report = QComboBox()
        self.combo_report.addItem("", "latest_daily.txt")
        self.combo_report.addItem("", "latest_weekly.txt")
        self.combo_report.addItem("", "latest_cleanup.txt")
        self.combo_report.currentIndexChanged.connect(self.refresh_report_view)
        header.addWidget(self.combo_report)
        self.btn_report_refresh = QPushButton()
        self.btn_report_refresh.clicked.connect(self.refresh_report_view)
        header.addWidget(self.btn_report_refresh)
        header.addStretch(1)
        layout.addLayout(header)
        self.report_text = QTextEdit()
        self.report_text.setReadOnly(True)
        layout.addWidget(self.report_text)

    def _build_settings_tab(self) -> None:
        layout = QGridLayout(self.settings_tab)
        self.spin_threshold = QSpinBox()
        self.spin_threshold.setRange(50, 100)
        self.spin_threshold.setValue(self.store.settings.alert_threshold)
        self.spin_threshold.valueChanged.connect(self.on_threshold_changed)
        self.spin_history = QSpinBox()
        self.spin_history.setRange(30, 3660)
        self.spin_history.setValue(self.store.settings.history_days)
        self.spin_history.valueChanged.connect(self.on_history_changed)
        self.spin_refresh = QSpinBox()
        self.spin_refresh.setRange(30, 3600)
        self.spin_refresh.setValue(self.store.settings.refresh_seconds)
        self.spin_refresh.valueChanged.connect(self.on_refresh_changed)
        self.combo_notification_mode = QComboBox()
        for mode in ("disabled", "outbox", "command", "webhook"):
            self.combo_notification_mode.addItem("", mode)
        self.combo_notification_mode.setCurrentIndex(
            self.combo_notification_mode.findData(self.store.settings.notification_mode)
        )
        self.combo_notification_mode.currentIndexChanged.connect(
            self.on_notification_mode_changed
        )
        self.input_notification_target = QLineEdit()
        self.input_notification_target.setText(self._notification_target_text())
        self.input_notification_target.editingFinished.connect(
            self.on_notification_target_changed
        )
        self.spin_notification_cooldown = QSpinBox()
        self.spin_notification_cooldown.setRange(0, 168)
        self.spin_notification_cooldown.setValue(
            self.store.settings.notification_cooldown_hours
        )
        self.spin_notification_cooldown.valueChanged.connect(
            self.on_notification_cooldown_changed
        )
        self.btn_test_notification = QPushButton()
        self.btn_test_notification.clicked.connect(self.test_notification)
        self.input_quota_command = QLineEdit(
            json.dumps(self.store.settings.quota_command, ensure_ascii=False)
        )
        self.input_quota_command.editingFinished.connect(self.on_quota_command_changed)
        self.spin_freshness = QSpinBox()
        self.spin_freshness.setRange(1, 168)
        self.spin_freshness.setValue(self.store.settings.freshness_warning_hours)
        self.spin_freshness.valueChanged.connect(self.on_freshness_changed)
        self.spin_cleanup_days = QSpinBox()
        self.spin_cleanup_days.setRange(7, 3650)
        self.spin_cleanup_days.setValue(self.store.settings.cleanup_inactive_days)
        self.spin_cleanup_days.valueChanged.connect(self.on_cleanup_changed)
        self.spin_cleanup_size = QSpinBox()
        self.spin_cleanup_size.setRange(1, 1_000_000)
        self.spin_cleanup_size.setValue(self.store.settings.cleanup_min_size_gb)
        self.spin_cleanup_size.valueChanged.connect(self.on_cleanup_changed)
        self.spin_rapid_growth = QSpinBox()
        self.spin_rapid_growth.setRange(1, 1_000_000)
        self.spin_rapid_growth.setValue(self.store.settings.rapid_growth_gb)
        self.spin_rapid_growth.valueChanged.connect(self.on_capacity_settings_changed)
        self.spin_forecast_alert = QSpinBox()
        self.spin_forecast_alert.setRange(1, 168)
        self.spin_forecast_alert.setValue(self.store.settings.forecast_alert_hours)
        self.spin_forecast_alert.valueChanged.connect(self.on_capacity_settings_changed)
        self.spin_forecast_emergency = QSpinBox()
        self.spin_forecast_emergency.setRange(
            1,
            self.store.settings.forecast_alert_hours,
        )
        self.spin_forecast_emergency.setValue(
            self.store.settings.forecast_emergency_hours
        )
        self.spin_forecast_emergency.valueChanged.connect(
            self.on_capacity_settings_changed
        )
        self.spin_capacity_history = QSpinBox()
        self.spin_capacity_history.setRange(1, 3650)
        self.spin_capacity_history.setValue(self.store.settings.capacity_sample_days)
        self.spin_capacity_history.valueChanged.connect(
            self.on_capacity_settings_changed
        )
        self.spin_data_size_warning = QSpinBox()
        self.spin_data_size_warning.setRange(50, 1_000_000)
        self.spin_data_size_warning.setValue(
            self.store.settings.data_size_warning_mb
        )
        self.spin_data_size_warning.valueChanged.connect(
            self.on_data_size_warning_changed
        )
        self.btn_data_size_refresh = QPushButton()
        self.btn_data_size_refresh.clicked.connect(self.refresh_data_directory_info)
        self.btn_install_cron = QPushButton()
        self.btn_install_cron.clicked.connect(self.install_cron_from_gui)

        self.lbl_setting_alert = QLabel()
        self.lbl_setting_history = QLabel()
        self.lbl_setting_refresh = QLabel()
        self.lbl_setting_data = QLabel()
        self.lbl_setting_python = QLabel()
        self.lbl_setting_collector = QLabel()
        self.lbl_setting_window = QLabel()
        self.lbl_setting_notification_mode = QLabel()
        self.lbl_setting_notification_target = QLabel()
        self.lbl_setting_notification_cooldown = QLabel()
        self.lbl_setting_quota = QLabel()
        self.lbl_setting_freshness = QLabel()
        self.lbl_setting_cleanup = QLabel()
        self.lbl_setting_rapid_growth = QLabel()
        self.lbl_setting_forecast_alert = QLabel()
        self.lbl_setting_forecast_emergency = QLabel()
        self.lbl_setting_capacity_history = QLabel()
        self.lbl_setting_data_size_warning = QLabel()
        layout.addWidget(self.lbl_setting_alert, 0, 0)
        layout.addWidget(self.spin_threshold, 0, 1)
        layout.addWidget(self.lbl_setting_history, 1, 0)
        layout.addWidget(self.spin_history, 1, 1)
        layout.addWidget(self.lbl_setting_refresh, 2, 0)
        layout.addWidget(self.spin_refresh, 2, 1)
        layout.addWidget(self.lbl_setting_data, 3, 0)
        data_row = QHBoxLayout()
        self.lbl_data_dir_value = QLabel()
        self.lbl_data_dir_value.setWordWrap(True)
        data_row.addWidget(self.lbl_data_dir_value, 1)
        data_row.addWidget(self.btn_data_size_refresh)
        layout.addLayout(data_row, 3, 1)
        layout.addWidget(self.lbl_setting_python, 4, 0)
        layout.addWidget(QLabel(sys.executable), 4, 1)
        layout.addWidget(self.lbl_setting_collector, 5, 0)
        self.lbl_collector_value = QLabel()
        layout.addWidget(self.lbl_collector_value, 5, 1)
        layout.addWidget(self.lbl_setting_window, 6, 0)
        self.lbl_scan_window_value = QLabel()
        layout.addWidget(self.lbl_scan_window_value, 6, 1)
        layout.addWidget(self.lbl_setting_notification_mode, 7, 0)
        layout.addWidget(self.combo_notification_mode, 7, 1)
        layout.addWidget(self.lbl_setting_notification_target, 8, 0)
        layout.addWidget(self.input_notification_target, 8, 1)
        layout.addWidget(self.lbl_setting_notification_cooldown, 9, 0)
        cooldown_row = QHBoxLayout()
        cooldown_row.addWidget(self.spin_notification_cooldown)
        cooldown_row.addWidget(self.btn_test_notification)
        cooldown_row.addStretch(1)
        layout.addLayout(cooldown_row, 9, 1)
        layout.addWidget(self.lbl_setting_quota, 10, 0)
        layout.addWidget(self.input_quota_command, 10, 1)
        layout.addWidget(self.lbl_setting_freshness, 11, 0)
        layout.addWidget(self.spin_freshness, 11, 1)
        layout.addWidget(self.lbl_setting_cleanup, 12, 0)
        cleanup_row = QHBoxLayout()
        cleanup_row.addWidget(self.spin_cleanup_days)
        cleanup_row.addWidget(self.spin_cleanup_size)
        cleanup_row.addStretch(1)
        layout.addLayout(cleanup_row, 12, 1)
        layout.addWidget(self.lbl_setting_rapid_growth, 13, 0)
        layout.addWidget(self.spin_rapid_growth, 13, 1)
        layout.addWidget(self.lbl_setting_forecast_alert, 14, 0)
        layout.addWidget(self.spin_forecast_alert, 14, 1)
        layout.addWidget(self.lbl_setting_forecast_emergency, 15, 0)
        layout.addWidget(self.spin_forecast_emergency, 15, 1)
        layout.addWidget(self.lbl_setting_capacity_history, 16, 0)
        layout.addWidget(self.spin_capacity_history, 16, 1)
        layout.addWidget(self.lbl_setting_data_size_warning, 17, 0)
        layout.addWidget(self.spin_data_size_warning, 17, 1)
        layout.addWidget(self.btn_install_cron, 18, 0, 1, 2)
        layout.setRowStretch(19, 1)

    def _build_search_tab(self) -> None:
        layout = QVBoxLayout(self.search_tab)
        self.lbl_search_info = QLabel()
        self.lbl_search_info.setWordWrap(True)
        self.lbl_search_info.setStyleSheet(
            "background: #fff3bf; border-left: 5px solid #e67700; padding: 10px;"
        )
        layout.addWidget(self.lbl_search_info)

        account_row = QHBoxLayout()
        self.lbl_search_account = QLabel()
        self.combo_search_account = QComboBox()
        self.combo_search_account.currentIndexChanged.connect(
            self.refresh_search_status
        )
        self.btn_search_index_toggle = QPushButton()
        self.btn_search_index_toggle.clicked.connect(self.toggle_search_indexing)
        self.btn_search_refresh = QPushButton()
        self.btn_search_refresh.clicked.connect(self.refresh_search_status)
        account_row.addWidget(self.lbl_search_account)
        account_row.addWidget(self.combo_search_account, 1)
        account_row.addWidget(self.btn_search_index_toggle)
        account_row.addWidget(self.btn_search_refresh)
        layout.addLayout(account_row)

        self.lbl_search_status = QLabel()
        self.lbl_search_status.setWordWrap(True)
        self.lbl_search_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_search_status.setStyleSheet(
            "background: #e7f5ff; border: 1px solid #a5d8ff; padding: 9px;"
        )
        layout.addWidget(self.lbl_search_status)

        query = QGridLayout()
        self.lbl_search_name = QLabel()
        self.input_search_name = QLineEdit()
        self.input_search_name.returnPressed.connect(self.run_search)
        self.lbl_search_extension = QLabel()
        self.input_search_extension = QLineEdit()
        self.input_search_extension.returnPressed.connect(self.run_search)
        self.lbl_search_mode = QLabel()
        self.combo_search_mode = QComboBox()
        for mode in ("prefix", "exact", "contains"):
            self.combo_search_mode.addItem("", mode)
        self.lbl_search_type = QLabel()
        self.combo_search_type = QComboBox()
        for entry_type in ("all", "file", "directory", "link"):
            self.combo_search_type.addItem("", entry_type)
        self.btn_search_run = QPushButton()
        self.btn_search_run.clicked.connect(self.run_search)
        query.addWidget(self.lbl_search_name, 0, 0)
        query.addWidget(self.input_search_name, 0, 1)
        query.addWidget(self.lbl_search_extension, 0, 2)
        query.addWidget(self.input_search_extension, 0, 3)
        query.addWidget(self.lbl_search_mode, 1, 0)
        query.addWidget(self.combo_search_mode, 1, 1)
        query.addWidget(self.lbl_search_type, 1, 2)
        query.addWidget(self.combo_search_type, 1, 3)
        query.addWidget(self.btn_search_run, 0, 4, 2, 1)
        query.setColumnStretch(1, 1)
        query.setColumnStretch(3, 1)
        layout.addLayout(query)

        self.lbl_search_results = QLabel()
        layout.addWidget(self.lbl_search_results)
        self.table_search = QTableWidget(0, 4)
        self.table_search.setAlternatingRowColors(True)
        self.table_search.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_search.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.table_search)
        self.refresh_search_accounts()

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(self.t("app.title"))
        self.file_menu.setTitle(self.t("menu.file"))
        self.action_minimize.setText(self.t("menu.minimize"))
        self.action_full_exit.setText(self.t("menu.full_exit"))
        self.language_menu.setTitle(self.t("menu.language"))
        self.action_ko.setText(self.t("language.ko"))
        self.action_en.setText(self.t("language.en"))
        self.action_ko.setChecked(self.language == "ko")
        self.action_en.setChecked(self.language == "en")
        self.admin_menu.setTitle(self.t("menu.admin"))
        self.action_admin_unlock.setText(self.t("admin.unlock"))
        self.action_admin_lock.setText(self.t("admin.lock"))

        tab_keys = (
            "tab.dashboard",
            "tab.accounts",
            "tab.tracking",
            "tab.trend",
            "tab.reports",
            "tab.setup",
        )
        for index, key in enumerate(tab_keys):
            self.tabs.setTabText(index, self.t(key))
        search_index = self.tabs.indexOf(self.search_tab)
        if search_index >= 0:
            self.tabs.setTabText(search_index, self.t("tab.search"))

        self.btn_refresh.setText(self.t("button.refresh"))
        self.lbl_last_update.setText(self.t("last_update.none"))
        self.table_usage.setHorizontalHeaderLabels(
            [
                self.t("header.account"),
                self.t("header.path"),
                self.t("header.use"),
                self.t("header.inode"),
                self.t("header.quota"),
                self.t("header.used"),
                self.t("header.total"),
                self.t("header.filesystem"),
                self.t("header.status"),
            ]
        )
        self.table_usage.horizontalHeaderItem(3).setToolTip(
            self.t("tooltip.inode")
        )
        self.lbl_shared_filesystems.setText(self.t("dashboard.shared.none"))

        roots = ", ".join(self.store.settings.monitored_roots)
        self.lbl_allowed_roots.setText(
            self.t(
                "accounts.allowed_roots",
                roots=roots,
                data=self.data_dir,
            )
        )
        self.lbl_name.setText(self.t("field.name"))
        self.lbl_path.setText(self.t("field.path"))
        self.input_name.setPlaceholderText(self.t("placeholder.name"))
        self.input_path.setPlaceholderText(
            self.t("placeholder.path", root=self.store.settings.monitored_roots[0])
        )
        self.btn_add.setText(self.t("button.add"))
        self.btn_update.setText(self.t("button.update"))
        self.btn_delete.setText(self.t("button.delete"))
        self.btn_clear.setText(self.t("button.clear"))
        self.table_accounts.setHorizontalHeaderLabels(
            [self.t("field.name"), self.t("header.path"), self.t("header.enabled")]
        )

        self.lbl_tracking_info.setText(self.t("tracking.info"))
        self.lbl_tracking_cron.setText(self.t("tracking.cron"))
        self.lbl_tracking_next.setText(self.t("tracking.next"))
        self.lbl_tracking_process.setText(self.t("tracking.process"))
        self.lbl_tracking_current.setText(self.t("tracking.current"))
        self.lbl_tracking_last.setText(self.t("tracking.last"))
        self.lbl_tracking_notification.setText(self.t("tracking.notification"))
        self.lbl_capacity_watch.setText(self.t("tracking.capacity_watch"))
        self.lbl_notifier.setText(self.t("tracking.notifier"))
        self.lbl_notifier_autostart.setText(self.t("tracking.notifier.autostart"))
        self.btn_tracking_refresh.setText(self.t("button.refresh"))
        self.btn_tracking_cron_toggle.setText(self.t("tracking.button.enable_auto"))
        self.btn_tracking_scan_toggle.setText(self.t("tracking.button.run"))
        self.btn_notifier_toggle.setText(self.t("tracking.notifier.start"))
        self.check_notifier_autostart.setText(
            self.t("tracking.notifier.autostart_checkbox")
        )
        self.table_tracking.setHorizontalHeaderLabels(
            [
                self.t("header.account"),
                self.t("header.path"),
                self.t("tracking.header.tracked"),
                self.t("tracking.header.capacity"),
                self.t("tracking.header.forecast"),
                self.t("tracking.header.last"),
                self.t("tracking.header.baseline"),
            ]
        )
        self.table_tracking.horizontalHeaderItem(3).setToolTip(
            self.t("tooltip.inode")
        )

        self.lbl_trend_account.setText(self.t("trend.account"))
        self.btn_trend_refresh.setText(self.t("button.refresh"))
        self.chart.set_language(self.language)
        self.baseline_progress_bar.setFormat(self.t("trend.baseline_bar"))
        self.lbl_baseline_progress.setText(self.t("trend.baseline_none"))
        self.lbl_growth.setText(self.t("trend.largest"))
        self.lbl_activity.setText(self.t("trend.activity_none"))
        self.lbl_forecast.setText(self.t("trend.forecast.none"))
        self.lbl_anomaly.setText(self.t("trend.anomaly.none"))
        self.combo_report.setItemText(0, self.t("report.latest_daily"))
        self.combo_report.setItemText(1, self.t("report.latest_weekly"))
        self.combo_report.setItemText(2, self.t("report.latest_cleanup"))
        self.btn_report_refresh.setText(self.t("button.refresh"))

        self.lbl_setting_alert.setText(self.t("settings.alert"))
        self.lbl_setting_history.setText(self.t("settings.history"))
        self.lbl_setting_refresh.setText(self.t("settings.refresh"))
        self.lbl_setting_data.setText(self.t("settings.data"))
        self.lbl_setting_python.setText(self.t("settings.python"))
        self.lbl_setting_collector.setText(self.t("settings.collector"))
        self.lbl_setting_window.setText(self.t("settings.scan_window"))
        self.lbl_setting_notification_mode.setText(self.t("settings.notification_mode"))
        self.lbl_setting_notification_target.setText(self.t("settings.notification_target"))
        self.lbl_setting_notification_cooldown.setText(
            self.t("settings.notification_cooldown")
        )
        self.lbl_setting_quota.setText(self.t("settings.quota_command"))
        self.lbl_setting_freshness.setText(self.t("settings.freshness"))
        self.lbl_setting_cleanup.setText(self.t("settings.cleanup"))
        self.lbl_setting_rapid_growth.setText(self.t("settings.rapid_growth"))
        self.lbl_setting_forecast_alert.setText(self.t("settings.forecast_alert"))
        self.lbl_setting_forecast_emergency.setText(
            self.t("settings.forecast_emergency")
        )
        self.lbl_setting_capacity_history.setText(
            self.t("settings.capacity_history")
        )
        self.lbl_setting_data_size_warning.setText(
            self.t("settings.data_size_warning")
        )
        self.btn_data_size_refresh.setText(self.t("settings.data_refresh"))
        for index, mode in enumerate(("disabled", "outbox", "command", "webhook")):
            self.combo_notification_mode.setItemText(
                index,
                self.t(f"settings.notification.{mode}"),
            )
        self.input_notification_target.setPlaceholderText(
            self.t("settings.notification_target_placeholder")
        )
        self.input_quota_command.setPlaceholderText(self.t("settings.quota_placeholder"))
        self.btn_test_notification.setText(self.t("settings.notification_test"))
        safe_minutes = (
            self.store.settings.scan_window_end_hour * 60
            - self.store.settings.scan_safety_minutes
        ) % (24 * 60)
        self.lbl_scan_window_value.setText(
            self.t(
                "settings.scan_window_value",
                start=self.store.settings.scan_window_start_hour,
                end=self.store.settings.scan_window_end_hour,
                safe=f"{safe_minutes // 60:02d}:{safe_minutes % 60:02d}",
            )
        )
        self.lbl_collector_value.setText(self.t("collector.rhel"))
        self.btn_install_cron.setText(self.t("settings.install_cron"))
        self.lbl_search_info.setText(self.t("search.info"))
        self.lbl_search_account.setText(self.t("search.account"))
        self.btn_search_refresh.setText(self.t("button.refresh"))
        self.lbl_search_name.setText(self.t("search.name"))
        self.lbl_search_extension.setText(self.t("search.extension"))
        self.lbl_search_mode.setText(self.t("search.mode"))
        self.lbl_search_type.setText(self.t("search.type"))
        self.btn_search_run.setText(self.t("search.run"))
        self.input_search_name.setPlaceholderText(self.t("search.name_placeholder"))
        self.input_search_extension.setPlaceholderText(
            self.t("search.extension_placeholder")
        )
        for index, mode in enumerate(("prefix", "exact", "contains")):
            self.combo_search_mode.setItemText(index, self.t(f"search.mode.{mode}"))
        for index, entry_type in enumerate(("all", "file", "directory", "link")):
            self.combo_search_type.setItemText(
                index,
                self.t(f"search.type.{entry_type}"),
            )
        self.table_search.setHorizontalHeaderLabels(
            [
                self.t("search.header.name"),
                self.t("search.header.type"),
                self.t("search.header.extension"),
                self.t("search.header.path"),
            ]
        )
        self.lbl_search_results.setText(self.t("search.results", count=0))
        if self.admin_unlocked:
            self.refresh_search_status()
        self.refresh_data_directory_info()
        self.refresh_tracking()

    def on_threshold_changed(self, value: int) -> None:
        self.store.settings.alert_threshold = int(value)
        save_store(self.data_dir, self.store)
        self.refresh_tracking()

    def on_history_changed(self, value: int) -> None:
        self.store.settings.history_days = int(value)
        save_store(self.data_dir, self.store)

    def on_refresh_changed(self, value: int) -> None:
        self.store.settings.refresh_seconds = int(value)
        self.timer.setInterval(int(value) * 1000)
        save_store(self.data_dir, self.store)

    def on_capacity_settings_changed(self) -> None:
        alert_hours = self.spin_forecast_alert.value()
        self.spin_forecast_emergency.setMaximum(alert_hours)
        self.store.settings.rapid_growth_gb = self.spin_rapid_growth.value()
        self.store.settings.forecast_alert_hours = alert_hours
        self.store.settings.forecast_emergency_hours = (
            self.spin_forecast_emergency.value()
        )
        self.store.settings.capacity_sample_days = self.spin_capacity_history.value()
        save_store(self.data_dir, self.store)

    def on_data_size_warning_changed(self, value: int) -> None:
        self.store.settings.data_size_warning_mb = int(value)
        save_store(self.data_dir, self.store)
        self.refresh_data_directory_info()

    def refresh_data_directory_info(self) -> None:
        size = directory_size_bytes(self.data_dir)
        self.lbl_data_dir_value.setText(
            self.t(
                "settings.data_value",
                user=current_user_id(),
                path=self.data_dir,
                pointer=config_location_file(),
                size=human_bytes(size),
                threshold=self.store.settings.data_size_warning_mb,
            )
        )

    def _notification_target_text(self) -> str:
        if self.store.settings.notification_mode == "command":
            return json.dumps(
                self.store.settings.notification_command,
                ensure_ascii=False,
            )
        if self.store.settings.notification_mode == "webhook":
            return self.store.settings.notification_webhook_url
        return ""

    def on_notification_mode_changed(self) -> None:
        mode = str(self.combo_notification_mode.currentData())
        if not mode:
            return
        self.store.settings.notification_mode = mode
        self.input_notification_target.setText(self._notification_target_text())
        save_store(self.data_dir, self.store)

    def on_notification_target_changed(self) -> None:
        mode = self.store.settings.notification_mode
        value = self.input_notification_target.text().strip()
        try:
            if mode == "command":
                parsed = json.loads(value or "[]")
                if not isinstance(parsed, list) or not all(
                    isinstance(argument, str) and argument for argument in parsed
                ):
                    raise ValueError(self.t("settings.command_invalid"))
                self.store.settings.notification_command = parsed
            elif mode == "webhook":
                if value and not value.startswith(("http://", "https://")):
                    raise ValueError(self.t("settings.webhook_invalid"))
                self.store.settings.notification_webhook_url = value
            save_store(self.data_dir, self.store)
        except (ValueError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, self.t("settings.invalid_title"), str(exc))
            self.input_notification_target.setText(self._notification_target_text())

    def on_notification_cooldown_changed(self, value: int) -> None:
        self.store.settings.notification_cooldown_hours = int(value)
        save_store(self.data_dir, self.store)

    def on_quota_command_changed(self) -> None:
        value = self.input_quota_command.text().strip()
        try:
            parsed = json.loads(value or "[]")
            if not isinstance(parsed, list) or not all(
                isinstance(argument, str) and argument for argument in parsed
            ):
                raise ValueError(self.t("settings.command_invalid"))
        except (ValueError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, self.t("settings.invalid_title"), str(exc))
            self.input_quota_command.setText(
                json.dumps(self.store.settings.quota_command, ensure_ascii=False)
            )
            return
        self.store.settings.quota_command = parsed
        save_store(self.data_dir, self.store)

    def on_freshness_changed(self, value: int) -> None:
        self.store.settings.freshness_warning_hours = int(value)
        save_store(self.data_dir, self.store)
        self.refresh_tracking()

    def on_cleanup_changed(self) -> None:
        self.store.settings.cleanup_inactive_days = self.spin_cleanup_days.value()
        self.store.settings.cleanup_min_size_gb = self.spin_cleanup_size.value()
        save_store(self.data_dir, self.store)

    def test_notification(self) -> None:
        self.on_notification_target_changed()
        event = NotificationEvent(
            key=f"test:{datetime.now():%Y%m%d%H%M%S%f}",
            level="alert",
            title=self.t("settings.notification_test_title"),
            message=self.t("settings.notification_test_message"),
        )
        try:
            result = dispatch_notifications(
                self.data_dir,
                self.store.settings,
                [event],
            )
        except Exception as exc:
            QMessageBox.critical(self, self.t("settings.notification_test_failed"), str(exc))
            return
        if result.error:
            QMessageBox.critical(
                self,
                self.t("settings.notification_test_failed"),
                result.error,
            )
            return
        QMessageBox.information(
            self,
            self.t("settings.notification_test_title"),
            self.t(
                "settings.notification_test_done",
                path=result.outbox_file or "-",
            ),
        )

    def sync_path_from_name(self, name: str) -> None:
        account_name = name.strip()
        if not account_name:
            self.input_path.clear()
            return
        roots = self.store.settings.monitored_roots
        if roots:
            self.input_path.setText(str(Path(roots[0]) / account_name))

    def sync_name_from_path(self, path: str) -> None:
        normalized = path.strip().rstrip("/\\")
        self.input_name.setText(Path(normalized).name if normalized else "")

    def _validated_account_values(self, current_id: str = "") -> Tuple[str, str]:
        name = self.input_name.text().strip()
        entered_path = self.input_path.text().strip() or name
        if not name:
            raise ConfigError(self.t("validation.name_required"))
        normalized = normalize_account_path(
            entered_path,
            self.store.settings.monitored_roots,
            require_exists=True,
        )
        for account in self.store.accounts:
            if account.account_id == current_id:
                continue
            if account.name == name:
                raise ConfigError(self.t("validation.duplicate_name", name=name))
            if account.path == normalized:
                raise ConfigError(self.t("validation.duplicate_path", path=normalized))
        return name, normalized

    def add_account(self) -> None:
        try:
            name, path = self._validated_account_values()
        except ConfigError as exc:
            QMessageBox.warning(self, self.t("dialog.cannot_add"), str(exc))
            return
        if not self._confirm_account_storage_risk(path):
            return
        self.store.accounts.append(Account(name=name, path=path))
        save_store(self.data_dir, self.store)
        self.clear_account_form()
        self._after_accounts_changed()

    def update_selected_account(self) -> None:
        account = self._selected_account()
        if account is None:
            QMessageBox.information(
                self,
                self.t("dialog.select"),
                self.t("dialog.select_first"),
            )
            return
        try:
            name, path = self._validated_account_values(account.account_id)
        except ConfigError as exc:
            QMessageBox.warning(self, self.t("dialog.cannot_update"), str(exc))
            return
        if not self._confirm_account_storage_risk(path):
            return
        path_changed = account.path != path
        account.name = name
        account.path = path
        save_store(self.data_dir, self.store)
        self.db.backfill_account(account.account_id, account.name, account.path)
        if path_changed:
            self.search_request_id += 1
            self._schedule_search_cleanup(account.account_id)
        self._after_accounts_changed()

    def _confirm_account_storage_risk(self, account_path: str) -> bool:
        try:
            shared = same_filesystem(self.data_dir, Path(account_path))
        except OSError:
            return True
        if not shared:
            return True
        answer = QMessageBox.question(
            self,
            self.t("dialog.same_filesystem.title"),
            self.t(
                "dialog.same_filesystem.question",
                data=self.data_dir,
                account=account_path,
            ),
        )
        return answer == QMessageBox.Yes

    def delete_selected_account(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        answer = QMessageBox.question(
            self,
            self.t("dialog.delete"),
            self.t("dialog.delete_question", name=account.name),
        )
        if answer != QMessageBox.Yes:
            return
        self.db.cancel_detail_scan(account.account_id)
        account_id = account.account_id
        self.store.accounts = [item for item in self.store.accounts if item.account_id != account.account_id]
        save_store(self.data_dir, self.store)
        self.search_request_id += 1
        self._schedule_search_cleanup(account_id)
        self.clear_account_form()
        self._after_accounts_changed()

    def clear_account_form(self) -> None:
        self.table_accounts.clearSelection()
        self.input_name.clear()
        self.input_path.clear()

    def _selected_account(self):
        row = self.table_accounts.currentRow()
        if row < 0:
            return None
        item = self.table_accounts.item(row, 0)
        return find_account(self.store, item.data(Qt.UserRole)) if item else None

    def load_selected_account(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        self.input_name.setText(account.name)
        self.input_path.setText(account.path)

    def refresh_accounts_table(self) -> None:
        self.table_accounts.blockSignals(True)
        self.table_accounts.setRowCount(len(self.store.accounts))
        for row, account in enumerate(self.store.accounts):
            name_item = QTableWidgetItem(account.name)
            name_item.setData(Qt.UserRole, account.account_id)
            path_item = QTableWidgetItem(account.path)
            enabled_item = QTableWidgetItem()
            enabled_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            enabled_item.setCheckState(Qt.Checked if account.enabled else Qt.Unchecked)
            self.table_accounts.setItem(row, 0, name_item)
            self.table_accounts.setItem(row, 1, path_item)
            self.table_accounts.setItem(row, 2, enabled_item)
        self.table_accounts.blockSignals(False)

    def on_enabled_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 2:
            return
        name_item = self.table_accounts.item(item.row(), 0)
        account = find_account(self.store, name_item.data(Qt.UserRole)) if name_item else None
        if account is None:
            return
        account.enabled = item.checkState() == Qt.Checked
        save_store(self.data_dir, self.store)
        self._after_accounts_changed(refresh_table=False)

    def _after_accounts_changed(self, refresh_table: bool = True) -> None:
        if refresh_table:
            self.refresh_accounts_table()
        self.refresh_trend_account_list()
        self.refresh_search_accounts()
        if self.admin_unlocked:
            self.refresh_search_status()
        self.refresh_tracking()
        self.refresh_dashboard()

    def _tracking_runtime_status(self) -> Dict[str, object]:
        status = read_scan_status(self.data_dir)
        if not self.launch_pending_pid:
            return status

        status_pid = int(status.get("pid", 0) or 0)
        pending_alive = process_is_alive(self.launch_pending_pid)
        if status_pid == self.launch_pending_pid:
            self.launch_pending_pid = 0
            return status
        if not pending_alive:
            self.launch_pending_pid = 0
            return status

        pending = dict(status)
        pending.update(
            {
                "state": "starting",
                "pid": self.launch_pending_pid,
                "trigger": "gui",
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "current_account": "",
                "phase": "idle",
                "accounts_processed": 0,
                "accounts_total": len(
                    [account for account in self.store.accounts if account.enabled]
                ),
            }
        )
        return pending

    def refresh_tracking(self, check_cron: bool = False) -> None:
        if check_cron:
            self.cron_status = read_cron_status()

        if not self.cron_status.available:
            cron_text = self.t(
                "tracking.cron.unavailable",
                error=self.cron_status.error or "-",
            )
        elif self.cron_status.installed:
            cron_text = self.t("tracking.cron.installed")
        elif (
            self.cron_status.line
            or self.cron_status.capacity_line
            or self.cron_status.health_line
        ):
            cron_text = self.t(
                "tracking.cron.partial",
                error=self.cron_status.error or "-",
            )
        else:
            cron_text = self.t("tracking.cron.not_installed")
        self.lbl_tracking_cron_value.setText(cron_text)
        self.lbl_tracking_cron_value.setToolTip(
            "\n".join(
                value
                for value in (
                    self.cron_status.line,
                    self.cron_status.capacity_line,
                    self.cron_status.health_line,
                    self.cron_status.error,
                )
                if value
            )
        )

        if self.cron_status.line or self.cron_status.capacity_line:
            next_nightly = next_scheduled_run(22)
            next_capacity = next_capacity_run()
            self.lbl_tracking_next_value.setText(
                self.t(
                    "tracking.next.value",
                    capacity=f"{next_capacity:%Y-%m-%d %H:%M}",
                    nightly=f"{next_nightly:%Y-%m-%d %H:%M}",
                )
            )
        else:
            self.lbl_tracking_next_value.setText(self.t("tracking.next.none"))

        status = self._tracking_runtime_status()
        state = str(status.get("state", "never"))
        state_text = self.t(f"tracking.state.{state}")
        trigger = str(status.get("trigger", ""))
        if trigger:
            state_text += f" / {self.t(f'tracking.trigger.{trigger}')}"
        self.lbl_tracking_process_value.setText(
            self.t(
                "tracking.process.value",
                state=state_text,
                phase=self.t(f"tracking.phase.{status.get('phase') or 'idle'}"),
                pid=int(status.get("pid", 0) or 0) or "-",
                processed=int(status.get("accounts_processed", 0) or 0),
                total=int(status.get("accounts_total", 0) or 0),
                started=status.get("started_at") or "-",
            )
        )
        self.lbl_tracking_current_value.setText(
            str(status.get("current_account") or "-")
        )
        result_time = status.get("finished_at") or status.get("updated_at") or "-"
        raw_result_message = str(status.get("message") or "-")
        result_message = raw_result_message
        if result_message == "stop requested by user":
            result_message = self.t("tracking.message.stopped")
        elif result_message == "process ended without a completion record":
            result_message = self.t("tracking.message.interrupted")
        elif state == "succeeded" and result_message != "-":
            result_message = self.t("tracking.message.succeeded")
        self.lbl_tracking_last_value.setText(
            self.t(
                "tracking.last.value",
                time=result_time,
                message=result_message,
            )
        )
        self.lbl_tracking_last_value.setToolTip(raw_result_message)
        notification = read_notification_status(self.data_dir)
        notification_mode = str(notification.get("mode") or "-")
        if notification_mode in {"disabled", "outbox", "command", "webhook"}:
            notification_mode = self.t(f"settings.notification.{notification_mode}")
        notification_error = str(notification.get("error") or "")
        self.lbl_tracking_notification_value.setText(
            self.t(
                "tracking.notification.value",
                time=notification.get("updated_at") or "-",
                mode=notification_mode,
                sent=int(notification.get("sent", 0) or 0),
                suppressed=int(notification.get("suppressed", 0) or 0),
                error=notification_error or "-",
            )
        )
        self.lbl_tracking_notification_value.setToolTip(
            str(notification.get("outbox_file") or "")
        )

        capacity_status = read_capacity_watch_status(self.data_dir)
        capacity_errors = capacity_status.get("errors", [])
        if not isinstance(capacity_errors, list):
            capacity_errors = [str(capacity_errors)]
        self.lbl_capacity_watch_value.setText(
            self.t(
                "tracking.capacity_watch.value",
                state=self.t(
                    f"tracking.state.{capacity_status.get('state') or 'never'}"
                ),
                time=capacity_status.get("finished_at")
                or capacity_status.get("started_at")
                or "-",
                filesystems=int(capacity_status.get("filesystems_checked", 0) or 0),
                samples=int(capacity_status.get("samples_written", 0) or 0),
                events=int(capacity_status.get("events_written", 0) or 0),
                errors=len(capacity_errors),
            )
        )
        self.lbl_capacity_watch_value.setToolTip("\n".join(capacity_errors))

        notifier_status = read_notifier_status(self.data_dir)
        notifier_state = str(notifier_status.get("state") or "never")
        self.lbl_notifier_value.setText(
            self.t(
                "tracking.notifier.value",
                state=self.t(f"tracking.notifier.state.{notifier_state}"),
                pid=int(notifier_status.get("pid", 0) or 0) or "-",
                unread=int(notifier_status.get("unread", 0) or 0),
                message=notifier_status.get("message") or "-",
            )
        )
        notifier_autostart = autostart_installed()
        self.lbl_notifier_autostart_value.setText(
            self.t(
                "tracking.notifier.autostart_on"
                if notifier_autostart
                else "tracking.notifier.autostart_off"
            )
        )

        active = state in ACTIVE_STATES
        enabled_accounts = [account for account in self.store.accounts if account.enabled]
        cron_active = bool(
            self.cron_status.installed
            or self.cron_status.line
            or self.cron_status.capacity_line
            or self.cron_status.health_line
        )
        self.btn_tracking_cron_toggle.setText(
            self.t(
                "tracking.button.disable_auto"
                if cron_active
                else "tracking.button.enable_auto"
            )
        )
        self.btn_tracking_cron_toggle.setEnabled(self.cron_status.available)
        self.btn_tracking_scan_toggle.setText(
            self.t("tracking.button.stop" if active else "tracking.button.run")
        )
        self.btn_tracking_scan_toggle.setEnabled(
            (active and state != "starting") or (bool(enabled_accounts) and not active)
        )
        notifier_active = notifier_state in ACTIVE_NOTIFIER_STATES
        self.btn_notifier_toggle.setText(
            self.t(
                "tracking.notifier.stop"
                if notifier_active
                else "tracking.notifier.start"
            )
        )
        self.btn_notifier_toggle.setEnabled(notifier_state != "stop_requested")
        self.check_notifier_autostart.blockSignals(True)
        self.check_notifier_autostart.setChecked(notifier_autostart)
        self.check_notifier_autostart.blockSignals(False)

        self.table_tracking.setRowCount(len(self.store.accounts))
        for row, account in enumerate(self.store.accounts):
            snapshot = self.db.latest_snapshot(account.account_id)
            detail_state = self.db.detail_scan_state(account.account_id)
            if account.enabled:
                tracked_text = self.t("tracking.enabled")
            else:
                tracked_text = self.t("tracking.disabled")
            if snapshot is None:
                use_text = "-"
                inode_text = "-"
                quota_text = "-"
                last_text = self.t("tracking.waiting")
            else:
                last_text = str(snapshot[0])
                use_pct = int(snapshot[5])
                use_text = f"{use_pct}% / {self.t(f'status.{usage_level(use_pct, self.store.settings.alert_threshold)}')}"
                inode_text = "-" if snapshot[9] is None else f"{int(snapshot[9])}%"
                quota_text = (
                    self.t("status.quota_error")
                    if snapshot[13]
                    else "-"
                    if snapshot[12] is None
                    else f"{int(snapshot[12])}%"
                )
            forecast = capacity_forecast(
                self.db.trend_points(account.account_id, 45),
                30,
                self.store.settings.alert_threshold,
            )
            forecast_text = (
                "-"
                if forecast is None or forecast.days_to_full is None
                else self.t("tracking.forecast.now")
                if forecast.days_to_full == 0
                else self.t("tracking.forecast.days", days=forecast.days_to_full)
            )
            nightly = self.db.latest_nightly_snapshot(account.account_id)
            freshness_stale = False
            if nightly is None:
                freshness_text = self.t("tracking.fresh.missing")
                freshness_stale = True
            else:
                try:
                    collected_at = datetime.strptime(str(nightly[0]), "%Y-%m-%d %H:%M:%S")
                    freshness_hours = max(
                        0,
                        int((datetime.now() - collected_at).total_seconds() / 3600),
                    )
                except ValueError:
                    freshness_text = self.t("tracking.fresh.missing")
                    freshness_stale = True
                else:
                    freshness_stale = (
                        freshness_hours >= self.store.settings.freshness_warning_hours
                    )
                    freshness_text = self.t(
                        "tracking.fresh.stale" if freshness_stale else "tracking.fresh.ok",
                        hours=freshness_hours,
                    )
            if detail_state is None:
                baseline_text = self.t("tracking.baseline.none")
            else:
                completed, total = self.db.detail_scan_progress(
                    account.account_id,
                    str(detail_state[1]),
                )
                baseline_text = self.t(
                    "tracking.baseline.value",
                    completed=completed,
                    total=total,
                )
            values = [
                account.name,
                account.path,
                tracked_text,
                self.t(
                    "tracking.capacity.value",
                    use=use_text,
                    inode=inode_text,
                    quota=quota_text,
                ),
                forecast_text,
                self.t(
                    "tracking.recent.value",
                    time=last_text,
                    freshness=freshness_text,
                ),
                baseline_text,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3 and snapshot is not None:
                    metrics = [
                        int(metric)
                        for metric in (snapshot[5], snapshot[9], snapshot[12])
                        if metric is not None
                    ]
                    metric_pct = max(metrics) if metrics else None
                    item.setBackground(
                        QColor(
                            usage_color(
                                int(metric_pct or 0),
                                failed=metric_pct is None,
                                alert_threshold=self.store.settings.alert_threshold,
                            )
                        )
                    )
                if column == 5 and freshness_stale:
                    item.setBackground(QColor("#f0ad4e"))
                if column == 2 and not account.enabled:
                    item.setForeground(QColor("#7a8580"))
                self.table_tracking.setItem(row, column, item)

        if self.restart_pending:
            if active:
                if state != "stop_requested":
                    request_scan_stop(self.data_dir)
            else:
                self.restart_pending = False
                QTimer.singleShot(0, self.start_tracking_scan)
        if self.notifier_restart_pending:
            if notifier_active:
                if notifier_state != "stop_requested":
                    request_notifier_stop(self.data_dir)
            else:
                self.notifier_restart_pending = False
                QTimer.singleShot(0, self.start_notifier)

    def toggle_tracking_cron(self) -> None:
        cron_active = bool(
            self.cron_status.installed
            or self.cron_status.line
            or self.cron_status.capacity_line
            or self.cron_status.health_line
        )
        if cron_active:
            self.remove_cron_from_gui()
        else:
            self.install_cron_from_gui()

    def toggle_tracking_scan(self) -> None:
        state = str(self._tracking_runtime_status().get("state") or "never")
        if state in ACTIVE_STATES:
            self.stop_tracking_scan()
        else:
            self.start_tracking_scan()

    def toggle_notifier(self) -> None:
        state = str(read_notifier_status(self.data_dir).get("state") or "never")
        if state in ACTIVE_NOTIFIER_STATES:
            self.stop_notifier()
        else:
            self.start_notifier()

    def toggle_notifier_autostart(self, state: int) -> None:
        if state == Qt.Checked:
            self.install_notifier_autostart()
        else:
            self.remove_notifier_autostart()

    def start_tracking_scan(self) -> None:
        if not any(account.enabled for account in self.store.accounts):
            QMessageBox.information(
                self,
                self.t("tracking.launch.title"),
                self.t("tracking.no_accounts"),
            )
            return
        try:
            pid = launch_background_scan(self.data_dir)
        except Exception as exc:
            QMessageBox.critical(self, self.t("tracking.launch.failed"), str(exc))
            return
        self.launch_pending_pid = pid
        self.lbl_tracking_process_value.setText(
            self.t("tracking.launch.value", pid=pid)
        )
        QTimer.singleShot(500, self.refresh_tracking)

    def stop_tracking_scan(self) -> None:
        if request_scan_stop(self.data_dir):
            self.lbl_tracking_last_value.setText(self.t("tracking.stop.sent"))
        else:
            QMessageBox.information(
                self,
                self.t("tracking.launch.title"),
                self.t("tracking.stop.none"),
            )
        self.refresh_tracking()

    def restart_tracking_scan(self) -> None:
        status = self._tracking_runtime_status()
        if str(status.get("state")) in ACTIVE_STATES:
            self.restart_pending = True
            request_scan_stop(self.data_dir)
            self.lbl_tracking_last_value.setText(self.t("tracking.restart.wait"))
            self.refresh_tracking()
            return
        self.start_tracking_scan()

    def start_notifier(self) -> None:
        try:
            pid = launch_notifier(self.data_dir)
        except Exception as exc:
            QMessageBox.critical(
                self,
                self.t("tracking.notifier.error"),
                str(exc),
            )
            return
        self.lbl_notifier_value.setText(
            self.t("tracking.notifier.starting", pid=pid)
        )
        QTimer.singleShot(500, self.refresh_tracking)

    def stop_notifier(self) -> None:
        if not request_notifier_stop(self.data_dir):
            QMessageBox.information(
                self,
                self.t("tracking.notifier.title"),
                self.t("tracking.notifier.not_running"),
            )
        self.refresh_tracking()

    def restart_notifier(self) -> None:
        status = read_notifier_status(self.data_dir)
        if str(status.get("state")) in ACTIVE_NOTIFIER_STATES:
            self.notifier_restart_pending = True
            request_notifier_stop(self.data_dir)
            self.refresh_tracking()
            return
        self.start_notifier()

    def install_notifier_autostart(self) -> None:
        try:
            path = install_notifier_autostart(self.data_dir)
        except Exception as exc:
            QMessageBox.critical(
                self,
                self.t("tracking.notifier.error"),
                str(exc),
            )
            return
        QMessageBox.information(
            self,
            self.t("tracking.notifier.title"),
            self.t("tracking.notifier.autostart_installed", path=path),
        )
        self.refresh_tracking()

    def remove_notifier_autostart(self) -> None:
        removed = remove_notifier_autostart()
        QMessageBox.information(
            self,
            self.t("tracking.notifier.title"),
            self.t(
                "tracking.notifier.autostart_removed"
                if removed
                else "tracking.notifier.autostart_missing"
            ),
        )
        self.refresh_tracking()

    def remove_cron_from_gui(self) -> None:
        answer = QMessageBox.question(
            self,
            self.t("tracking.remove.title"),
            self.t("tracking.remove.question"),
        )
        if answer != QMessageBox.Yes:
            return
        try:
            remove_cron()
        except Exception as exc:
            QMessageBox.critical(self, self.t("cron.failed"), str(exc))
            return
        self.cron_status = read_cron_status()
        self.refresh_tracking()
        QMessageBox.information(
            self,
            self.t("tracking.remove.title"),
            self.t("tracking.remove.done"),
        )

    def refresh_dashboard(self) -> None:
        if self._closing:
            return
        if self.refresh_pending:
            self.refresh_again = True
            return
        self.refresh_again = False
        accounts = [account for account in self.store.accounts if account.enabled]
        self.table_usage.setRowCount(len(accounts))
        self.refresh_alerts = []
        self.current_snapshots = {}
        self.refresh_pending = len(accounts)
        self.btn_refresh.setEnabled(not accounts)

        if not accounts:
            self.lbl_last_update.setText(self.t("last_update.no_accounts"))
            self.lbl_shared_filesystems.setText(self.t("dashboard.shared.none"))
            return

        for row, account in enumerate(accounts):
            values_and_keys = [
                (account.name, account.name.casefold()),
                (account.path, account.path.casefold()),
                ("-", DASHBOARD_UNAVAILABLE_SORT_KEY),
                ("-", DASHBOARD_UNAVAILABLE_SORT_KEY),
                ("-", DASHBOARD_UNAVAILABLE_SORT_KEY),
                ("-", DASHBOARD_UNAVAILABLE_SORT_KEY),
                ("-", DASHBOARD_UNAVAILABLE_SORT_KEY),
                ("-", ""),
                (
                    self.t("status.checking"),
                    DASHBOARD_UNAVAILABLE_SORT_KEY,
                ),
            ]
            for column, (value, sort_key) in enumerate(values_and_keys):
                item = self._dashboard_item(
                    value,
                    sort_key,
                    account.account_id if column == 0 else None,
                )
                if column in (2, 3, 4):
                    item.setBackground(QColor(usage_color(0, failed=True)))
                self.table_usage.setItem(row, column, item)

        self._apply_dashboard_sort()

        for account in accounts:
            try:
                normalized = normalize_account_path(
                    account.path,
                    self.store.settings.monitored_roots,
                    require_exists=True,
                )
            except ConfigError as exc:
                self.on_df_error(account.account_id, str(exc))
                self.on_df_finished()
                continue
            worker_account = Account(
                name=account.name,
                path=normalized,
                enabled=account.enabled,
                account_id=account.account_id,
            )
            worker = DfWorker(
                worker_account,
                self.store.settings.df_timeout_seconds,
                self.backend,
                self.store.settings.quota_command,
                self.store.settings.quota_timeout_seconds,
            )
            worker.signals.result.connect(self.on_df_result)
            worker.signals.error.connect(self.on_df_error)
            worker.signals.finished.connect(self.on_df_finished)
            self.thread_pool.start(worker)

    @pyqtSlot(str, object)
    def on_df_result(self, account_id: str, snapshot: UsageSnapshot) -> None:
        if self._closing:
            return
        account = find_account(self.store, account_id)
        row = self._dashboard_row(account_id)
        if account is None or row is None:
            return
        now = datetime.now()
        self.db.upsert_snapshot(
            ts=now.strftime("%Y-%m-%d %H:%M:%S"),
            day=now.strftime("%Y-%m-%d"),
            account_id=account.account_id,
            account_name=account.name,
            account_path=account.path,
            fs_name=snapshot.fs_name,
            total_kb=snapshot.total_kb,
            used_kb=snapshot.used_kb,
            avail_kb=snapshot.avail_kb,
            use_pct=snapshot.use_pct,
            total_inodes=snapshot.total_inodes,
            used_inodes=snapshot.used_inodes,
            avail_inodes=snapshot.avail_inodes,
            inode_use_pct=snapshot.inode_use_pct,
            quota_used_kb=snapshot.quota_used_kb,
            quota_limit_kb=snapshot.quota_limit_kb,
            quota_use_pct=snapshot.quota_use_pct,
            quota_error=snapshot.quota_error,
            source="gui",
        )
        effective_pct = max(
            value
            for value in (
                snapshot.use_pct,
                snapshot.inode_use_pct,
                snapshot.quota_use_pct,
            )
            if value is not None
        )
        status_text = self.t(
            f"status.{usage_level(effective_pct, self.store.settings.alert_threshold)}"
        )
        if snapshot.quota_error:
            status_text += f" | {self.t('status.quota_error')}"
        detail_state = self.db.detail_scan_state(account.account_id)
        if detail_state is not None:
            completed, total = self.db.detail_scan_progress(
                account.account_id,
                str(detail_state[1]),
            )
            status_text = self.t(
                "status.baseline_progress",
                status=status_text,
                completed=completed,
                total=total,
            )
        values_and_keys = [
            (account.name, account.name.casefold()),
            (account.path, account.path.casefold()),
            (f"{snapshot.use_pct}%", snapshot.use_pct),
            (
                "-"
                if snapshot.inode_use_pct is None
                else f"{snapshot.inode_use_pct}%",
                DASHBOARD_UNAVAILABLE_SORT_KEY
                if snapshot.inode_use_pct is None
                else snapshot.inode_use_pct,
            ),
            (
                "ERR"
                if snapshot.quota_error
                else "-"
                if snapshot.quota_use_pct is None
                else f"{snapshot.quota_use_pct}%",
                DASHBOARD_UNAVAILABLE_SORT_KEY
                if snapshot.quota_error or snapshot.quota_use_pct is None
                else snapshot.quota_use_pct,
            ),
            (human_kb(snapshot.used_kb), snapshot.used_kb),
            (human_kb(snapshot.total_kb), snapshot.total_kb),
            (snapshot.fs_name, snapshot.fs_name.casefold()),
            (
                status_text,
                dashboard_status_rank(
                    effective_pct,
                    self.store.settings.alert_threshold,
                ),
            ),
        ]
        for column, (value, sort_key) in enumerate(values_and_keys):
            item = self._dashboard_item(
                value,
                sort_key,
                account.account_id if column == 0 else None,
            )
            if column in (2, 3, 4):
                metric_pct = {
                    2: snapshot.use_pct,
                    3: snapshot.inode_use_pct,
                    4: snapshot.quota_use_pct,
                }[column]
                item.setBackground(
                    QColor(
                        usage_color(
                            int(metric_pct or 0),
                            failed=metric_pct is None,
                            alert_threshold=self.store.settings.alert_threshold,
                        )
                    )
                )
            self.table_usage.setItem(row, column, item)
        self._apply_dashboard_sort()
        self.current_snapshots[account_id] = snapshot

        if effective_pct >= self.store.settings.alert_threshold:
            if account_id not in self.alerted_accounts:
                metrics = [f"byte {snapshot.use_pct}%"]
                if snapshot.inode_use_pct is not None:
                    metrics.append(f"inode {snapshot.inode_use_pct}%")
                if snapshot.quota_use_pct is not None:
                    metrics.append(f"quota {snapshot.quota_use_pct}%")
                self.refresh_alerts.append(f"{account.name}: {', '.join(metrics)}")
            self.alerted_accounts.add(account_id)
        else:
            self.alerted_accounts.discard(account_id)

    @pyqtSlot(str, str)
    def on_df_error(self, account_id: str, message: str) -> None:
        if self._closing:
            return
        row = self._dashboard_row(account_id)
        if row is None:
            return
        for column in (2, 3, 4):
            percent_item = self._dashboard_item(
                "-",
                DASHBOARD_UNAVAILABLE_SORT_KEY,
            )
            percent_item.setBackground(QColor(usage_color(0, failed=True)))
            self.table_usage.setItem(row, column, percent_item)
        self.table_usage.setItem(
            row,
            8,
            self._dashboard_item(
                self.t("status.error", error=message),
                DASHBOARD_UNAVAILABLE_SORT_KEY,
            ),
        )
        self._apply_dashboard_sort()
        self.alerted_accounts.discard(account_id)

    @pyqtSlot()
    def on_df_finished(self) -> None:
        self.refresh_pending = max(0, self.refresh_pending - 1)
        if self._closing:
            return
        if self.refresh_pending:
            return
        self.btn_refresh.setEnabled(True)
        self.lbl_last_update.setText(
            self.t("last_update.value", value=f"{datetime.now():%Y-%m-%d %H:%M:%S}")
        )
        self._refresh_shared_filesystems()
        self.refresh_trend_data()
        if self.refresh_alerts:
            QMessageBox.warning(
                self,
                self.t("alert.title"),
                self.t("alert.message", accounts="\n".join(self.refresh_alerts)),
            )
        if self.refresh_again:
            QTimer.singleShot(0, self.refresh_dashboard)

    def _refresh_shared_filesystems(self) -> None:
        groups: Dict[str, List[str]] = {}
        for account_id, snapshot in self.current_snapshots.items():
            account = find_account(self.store, account_id)
            if account is not None:
                groups.setdefault(snapshot.fs_name, []).append(account.name)
        shared = [
            f"{filesystem}: {', '.join(sorted(names))}"
            for filesystem, names in sorted(groups.items())
            if len(names) > 1
        ]
        self.lbl_shared_filesystems.setText(
            self.t("dashboard.shared.value", groups=" | ".join(shared))
            if shared
            else self.t("dashboard.shared.none")
        )

    def refresh_trend_account_list(self) -> None:
        current_id = self.combo_trend_account.currentData()
        self.combo_trend_account.blockSignals(True)
        self.combo_trend_account.clear()
        for account in self.store.accounts:
            self.combo_trend_account.addItem(account.name, account.account_id)
        index = self.combo_trend_account.findData(current_id)
        if index >= 0:
            self.combo_trend_account.setCurrentIndex(index)
        self.combo_trend_account.blockSignals(False)
        self.refresh_trend_data()

    def refresh_trend_data(self) -> None:
        account_id = self.combo_trend_account.currentData()
        if not account_id:
            self.chart.set_points([])
            self.baseline_progress_bar.hide()
            self.lbl_baseline_progress.setText(self.t("trend.baseline_none"))
            self.lbl_growth.setText(self.t("trend.largest"))
            self.lbl_activity.setText(self.t("trend.activity_none"))
            self.lbl_forecast.setText(self.t("trend.forecast.none"))
            self.lbl_anomaly.setText(self.t("trend.anomaly.none"))
            return
        trend_points = self.db.trend_points(account_id, self.store.settings.history_days)
        self.chart.set_points(trend_points)
        forecast_7 = capacity_forecast(
            trend_points,
            7,
            self.store.settings.alert_threshold,
        )
        forecast_30 = capacity_forecast(
            trend_points,
            30,
            self.store.settings.alert_threshold,
        )
        if forecast_7 is None and forecast_30 is None:
            self.lbl_forecast.setText(self.t("trend.forecast.none"))
        else:
            def format_forecast(forecast):
                if forecast is None:
                    return "-"
                alert = (
                    self.t("file.forecast.now")
                    if forecast.days_to_alert == 0
                    else forecast.days_to_alert
                    if forecast.days_to_alert is not None
                    else "-"
                )
                full = (
                    self.t("file.forecast.now")
                    if forecast.days_to_full == 0
                    else forecast.days_to_full
                    if forecast.days_to_full is not None
                    else "-"
                )
                return self.t(
                    "trend.forecast.row",
                    alert=alert,
                    full=full,
                    slope=forecast.slope_pct_per_day,
                )

            self.lbl_forecast.setText(
                self.t(
                    "trend.forecast.value",
                    seven=format_forecast(forecast_7),
                    thirty=format_forecast(forecast_30),
                )
            )
        anomaly = detect_growth_anomaly(
            self.db.recent_used_points(account_id, 45),
            self.store.settings.anomaly_multiplier,
            self.store.settings.anomaly_min_growth_gb * 1024 * 1024,
        )
        self.lbl_anomaly.setText(
            self.t(
                "trend.anomaly.value",
                latest=human_kb(anomaly.latest_delta_kb),
                baseline=human_kb(anomaly.baseline_median_kb),
            )
            if anomaly.detected
            else self.t("trend.anomaly.none")
        )
        detail_state = self.db.detail_scan_state(account_id)
        if detail_state is None:
            self.baseline_progress_bar.hide()
            self.lbl_baseline_progress.setText(self.t("trend.baseline_none"))
        else:
            completed, total = self.db.detail_scan_progress(
                account_id,
                str(detail_state[1]),
            )
            self.lbl_baseline_progress.setText(
                self.t(
                    "trend.baseline_progress",
                    completed=completed,
                    total=total,
                    started=detail_state[2],
                )
            )
            self.baseline_progress_bar.setRange(0, max(1, total))
            self.baseline_progress_bar.setValue(completed)
            self.baseline_progress_bar.show()
        growth_day = self.db.latest_growth_day(account_id)
        if not growth_day:
            self.lbl_growth.setText(self.t("trend.waiting"))
        else:
            growth = self.db.growth_items_for_day(account_id, growth_day)[:5]
            if not growth:
                self.lbl_growth.setText(self.t("trend.no_change", day=growth_day))
            else:
                lines = [
                    f"{human_kb(int(delta_kb))}: {path}"
                    for path, delta_kb, _ in growth
                ]
                self.lbl_growth.setText(
                    self.t("trend.value", day=growth_day, lines="\n".join(lines))
                )

        activity_day = self.db.latest_activity_day(account_id)
        if not activity_day:
            self.lbl_activity.setText(self.t("trend.activity_none"))
            return
        activity_rows = self.db.activity_items_for_day(account_id, activity_day)[:5]
        activity_lines = [
            self.t(
                "trend.activity_row",
                size=human_bytes(int(changed_bytes)),
                count=int(file_count),
                modified=format_mtime(float(newest_mtime)),
                path=path,
            )
            for path, changed_bytes, file_count, newest_mtime, _ in activity_rows
        ]
        self.lbl_activity.setText(
            self.t(
                "trend.activity_value",
                day=activity_day,
                lines="\n".join(activity_lines),
            )
        )

    def refresh_report_view(self) -> None:
        filename = self.combo_report.currentData()
        if not filename:
            return
        path = reports_dir(self.data_dir) / filename
        localized = path.with_name(f"{path.stem}_{self.language}{path.suffix}")
        if localized.exists():
            path = localized
        if not path.exists():
            self.report_text.setPlainText(self.t("report.none"))
            return
        try:
            self.report_text.setPlainText(path.read_text(encoding="utf-8"))
        except OSError as exc:
            self.report_text.setPlainText(self.t("report.read_error", error=exc))

    def install_cron_from_gui(self) -> None:
        answer = QMessageBox.question(
            self,
            self.t("cron.title"),
            self.t("cron.question"),
        )
        if answer != QMessageBox.Yes:
            return
        try:
            line = install_cron(self.data_dir, sys.executable)
        except Exception as exc:
            QMessageBox.critical(self, self.t("cron.failed"), str(exc))
            return
        QMessageBox.information(self, self.t("cron.installed"), line)
        self.cron_status = read_cron_status()
        self.refresh_tracking()


def run_app() -> None:
    parser = argparse.ArgumentParser(description="Storage Manager GUI")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Directory for state, database, and reports",
    )
    args, qt_args = parser.parse_known_args()
    app = QApplication([sys.argv[0], *qt_args])
    try:
        data_dir = resolve_gui_data_dir(args.data_dir, None, "ko")
        if data_dir is None:
            raise ConfigError("Data directory selection was cancelled")
        window = MainWindow(data_dir)
    except ConfigError as exc:
        QMessageBox.critical(None, "Storage Manager configuration error", str(exc))
        raise SystemExit(1) from exc
    window.show()
    raise SystemExit(app.exec_())
