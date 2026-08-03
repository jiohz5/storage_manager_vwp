"""관리자 전용 이름 검색 다이얼로그.

PIN을 맞춰야 열린다. 다만 이것은 **화면 노출 제한**이지 보안 경계가 아니며
(`smvwp.admin_auth` 참고), 그 사실을 화면에도 그대로 적어 둔다 - 실제보다 강한
보호로 오해하면 안 되기 때문이다.

인덱싱은 파일시스템 전체를 훑을 수 있으므로 백그라운드 스레드에서 돌린다.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QLineEdit as _QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import admin_auth, config as config_module
from .. import i18n, search_index
from . import widgets
from .pin_dialog import PinChangeDialog


class _IndexWorker(QObject):
    finished = pyqtSignal(int)
    failed = pyqtSignal(str)

    def __init__(self, data_dir: Path, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._lock = threading.Lock()
        self._running = False
        self._stop = False

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def request_stop(self) -> None:
        self._stop = True

    def run_async(self, account_id: str, account_path: Path) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._stop = False
        threading.Thread(
            target=self._run, args=(account_id, account_path), daemon=True
        ).start()
        return True

    def _run(self, account_id: str, account_path: Path) -> None:
        conn = None
        try:
            conn = search_index.connect(self._data_dir)
            count = search_index.index_account(
                conn, account_id, account_path, should_stop=lambda: self._stop
            )
            self.finished.emit(count)
        except Exception as exc:  # pragma: no cover - 방어적 처리
            self.failed.emit(str(exc))
        finally:
            if conn is not None:
                conn.close()
            with self._lock:
                self._running = False


class SearchDialog(QDialog):
    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._config = config
        self._session = admin_auth.AdminSession()
        self.setWindowTitle(i18n.t("search.title"))
        self.resize(860, 560)

        self._worker = _IndexWorker(data_dir, self)
        self._worker.finished.connect(self._on_index_finished)
        self._worker.failed.connect(self._on_index_failed)

        self._build_ui()

    def exec_(self):  # noqa: N802 (Qt 이름 규칙)
        if not self._prompt_for_pin():
            return QDialog.Rejected
        self._refresh_state()
        return super().exec_()

    # -- PIN ---------------------------------------------------------
    def _prompt_for_pin(self) -> bool:
        pin, ok = QInputDialog.getText(
            self.parent(),
            i18n.t("search.pin_title"),
            f"{i18n.t('search.pin_prompt')}\n\n{i18n.t('search.pin_caveat')}",
            _QLineEdit.Password,
        )
        if not ok:
            return False
        if not self._session.unlock(pin, self._config.settings.admin_pin_hash):
            QMessageBox.warning(
                self.parent(), i18n.t("search.pin_title"), i18n.t("search.pin_wrong")
            )
            return False
        return True

    # -- UI ----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        caveat_row = QHBoxLayout()
        caveat = QLabel(i18n.t("search.pin_caveat"))
        caveat.setStyleSheet("color: #757575; font-size: 9pt;")
        caveat.setWordWrap(True)
        caveat_row.addWidget(caveat, 1)
        change_pin_btn = QPushButton(i18n.t("search.change_pin"))
        change_pin_btn.clicked.connect(self._change_pin)
        caveat_row.addWidget(change_pin_btn)
        root.addLayout(caveat_row)

        # 기본 PIN을 그대로 쓰고 있으면 눈에 띄게 알린다.
        self.pin_warning = QLabel(i18n.t("search.pin_default_warning"))
        self.pin_warning.setStyleSheet("color: #ef6c00; font-size: 9pt; font-weight: bold;")
        self.pin_warning.setWordWrap(True)
        self.pin_warning.setVisible(
            admin_auth.is_using_default(self._config.settings.admin_pin_hash)
        )
        root.addWidget(self.pin_warning)

        account_row = QHBoxLayout()
        self.account_combo = QComboBox()
        for account in self._config.accounts:
            self.account_combo.addItem(account.name, account.account_id)
        self.account_combo.currentIndexChanged.connect(self._refresh_state)
        account_row.addWidget(self.account_combo)

        self.indexing_check = QCheckBox(i18n.t("search.enable_indexing"))
        self.indexing_check.stateChanged.connect(self._toggle_indexing)
        account_row.addWidget(self.indexing_check)
        account_row.addStretch(1)
        root.addLayout(account_row)

        query_row = QHBoxLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText(i18n.t("search.query_placeholder"))
        self.query_edit.returnPressed.connect(self._run_search)
        self.mode_combo = QComboBox()
        for mode, key in (
            (search_index.MODE_EXACT, "search.mode.exact"),
            (search_index.MODE_PREFIX, "search.mode.prefix"),
            (search_index.MODE_CONTAINS, "search.mode.contains"),
        ):
            self.mode_combo.addItem(i18n.t(key), mode)
        search_btn = QPushButton(i18n.t("search.btn.run"))
        search_btn.clicked.connect(self._run_search)
        query_row.addWidget(self.query_edit, 3)
        query_row.addWidget(self.mode_combo, 1)
        query_row.addWidget(search_btn)
        root.addLayout(query_row)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #424242; font-size: 9pt;")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        self.results = QTableWidget(0, 2)
        self.results.setHorizontalHeaderLabels(
            [i18n.t("search.col.path"), i18n.t("search.col.kind")]
        )
        self.results.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.results.setEditTriggers(QTableWidget.NoEditTriggers)
        root.addWidget(self.results)

        close_btn = QPushButton(i18n.t("common.close"))
        close_btn.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    # -- 동작 ---------------------------------------------------------
    def _selected_account(self):
        account_id = self.account_combo.currentData()
        return config_module.find_account(self._config, account_id) if account_id else None

    def _refresh_state(self) -> None:
        account = self._selected_account()
        if account is None:
            self.status_label.setText(i18n.t("accounts.none_selected_body"))
            return

        self.indexing_check.blockSignals(True)
        self.indexing_check.setChecked(account.search_indexing)
        self.indexing_check.blockSignals(False)

        conn = search_index.connect(self._data_dir)
        try:
            count = search_index.entry_count(conn, account.account_id)
        finally:
            conn.close()

        size_text = i18n.t(
            "search.db_size", size=widgets.format_bytes(search_index.db_size_bytes(self._data_dir))
        )
        if account.search_indexing:
            self.status_label.setText(f"{size_text}  |  {count:,}")
        else:
            self.status_label.setText(f"{i18n.t('search.not_indexed')}  |  {size_text}")

    def _toggle_indexing(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        account.search_indexing = self.indexing_check.isChecked()
        try:
            config_module.save_config(self._data_dir, self._config)
        except config_module.ConfigError as exc:
            QMessageBox.critical(self, i18n.t("accounts.save_failed"), str(exc))
            return

        if account.search_indexing:
            # 켜면 바로 한 번 인덱싱한다 (백그라운드).
            self._worker.run_async(account.account_id, Path(account.path))
            self.status_label.setText(i18n.t("scan.started"))
        else:
            conn = search_index.connect(self._data_dir)
            try:
                search_index.clear_account(conn, account.account_id)
            finally:
                conn.close()
            self._refresh_state()

    def _run_search(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        if not account.search_indexing:
            self.status_label.setText(i18n.t("search.not_indexed"))
            return

        limit = self._config.settings.search_result_limit
        conn = search_index.connect(self._data_dir)
        try:
            hits = search_index.search(
                conn,
                account.account_id,
                self.query_edit.text().strip(),
                mode=self.mode_combo.currentData(),
                limit=limit,
            )
        finally:
            conn.close()

        self.results.setRowCount(len(hits))
        for row, hit in enumerate(hits):
            self.results.setItem(row, 0, QTableWidgetItem(hit.relative_path))
            self.results.setItem(row, 1, QTableWidgetItem(hit.kind))
        self.status_label.setText(i18n.t("search.result_count", count=len(hits), limit=limit))

    def _change_pin(self) -> None:
        dialog = PinChangeDialog(self._data_dir, self._config, parent=self)
        if dialog.exec_():
            self.pin_warning.setVisible(
                admin_auth.is_using_default(self._config.settings.admin_pin_hash)
            )

    def _on_index_finished(self, count: int) -> None:
        self._refresh_state()

    def _on_index_failed(self, message: str) -> None:
        self.status_label.setText(message)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._worker.request_stop()
        super().closeEvent(event)
