"""관리자 검색 서브 인터페이스 - 이름 검색 + PIN 변경.

PIN을 맞춰야 화면이 열린다. 다만 이것은 **화면 노출 제한**이지 보안 경계가
아니며(`smvwp/admin_auth.py`), 그 사실을 화면에도 그대로 적어 둔다 - 실제보다
강한 보호로 오해하면 안 되기 때문이다.

인덱싱은 파일시스템 전체를 훑을 수 있으므로 백그라운드 스레드에서 돌린다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    SearchLineEdit,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
)
from qfluentwidgets import FluentIcon as FIF

from .. import admin_auth, config as config_module
from .. import i18n, search_index
from . import widgets
from .scheduler import SearchIndexWorker


class SearchInterface(QWidget):
    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(parent)
        self.setObjectName("searchInterface")
        self._data_dir = data_dir
        self._config = config
        self._session = admin_auth.AdminSession()

        self._worker = SearchIndexWorker(data_dir, self)
        self._worker.finished.connect(self._on_index_finished)
        self._worker.failed.connect(self._on_index_failed)

        self._build_ui()
        self.retranslate()
        self._apply_lock_state()

    # -- UI 구성 -----------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        self.title_label = SubtitleLabel("", self)
        root.addWidget(self.title_label)

        # -- 잠금 카드 (PIN 입력) --
        self.lock_card = CardWidget(self)
        lock_layout = QVBoxLayout(self.lock_card)
        lock_layout.setContentsMargins(18, 14, 18, 14)
        lock_layout.setSpacing(10)
        self.pin_prompt_label = StrongBodyLabel("", self.lock_card)
        lock_layout.addWidget(self.pin_prompt_label)
        self.lock_caveat_label = CaptionLabel("", self.lock_card)
        self.lock_caveat_label.setWordWrap(True)
        lock_layout.addWidget(self.lock_caveat_label)

        pin_row = QHBoxLayout()
        pin_row.setSpacing(8)
        self.pin_edit = PasswordLineEdit(self.lock_card)
        self.pin_edit.returnPressed.connect(self._try_unlock)
        self.unlock_btn = PrimaryPushButton(FIF.FINGERPRINT, "", self.lock_card)
        self.unlock_btn.clicked.connect(self._try_unlock)
        pin_row.addWidget(self.pin_edit, 1)
        pin_row.addWidget(self.unlock_btn)
        lock_layout.addLayout(pin_row)
        root.addWidget(self.lock_card)

        # -- 검색 카드 --
        self.search_card = CardWidget(self)
        box = QVBoxLayout(self.search_card)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(10)

        caveat_row = QHBoxLayout()
        self.caveat_label = CaptionLabel("", self.search_card)
        self.caveat_label.setWordWrap(True)
        caveat_row.addWidget(self.caveat_label, 1)
        self.change_pin_btn = PushButton(FIF.VPN, "", self.search_card)
        self.change_pin_btn.clicked.connect(self._change_pin)
        caveat_row.addWidget(self.change_pin_btn)
        box.addLayout(caveat_row)

        self.pin_warning = BodyLabel("", self.search_card)
        self.pin_warning.setWordWrap(True)
        self.pin_warning.setStyleSheet("color: #ef6c00; font-weight: bold;")
        box.addWidget(self.pin_warning)

        account_row = QHBoxLayout()
        account_row.setSpacing(8)
        self.account_combo = ComboBox(self.search_card)
        for account in self._config.accounts:
            self.account_combo.addItem(account.name, userData=account.account_id)
        self.account_combo.currentIndexChanged.connect(self._refresh_state)
        account_row.addWidget(self.account_combo)
        self.indexing_check = CheckBox("", self.search_card)
        self.indexing_check.stateChanged.connect(self._toggle_indexing)
        account_row.addWidget(self.indexing_check)
        account_row.addStretch(1)
        box.addLayout(account_row)

        query_row = QHBoxLayout()
        query_row.setSpacing(8)
        self.query_edit = SearchLineEdit(self.search_card)
        self.query_edit.returnPressed.connect(self._run_search)
        self.query_edit.searchSignal.connect(lambda _text: self._run_search())
        self.mode_combo = ComboBox(self.search_card)
        for mode, key in (
            (search_index.MODE_EXACT, "search.mode.exact"),
            (search_index.MODE_PREFIX, "search.mode.prefix"),
            (search_index.MODE_CONTAINS, "search.mode.contains"),
        ):
            self.mode_combo.addItem(i18n.t(key), userData=mode)
        self.search_btn = PrimaryPushButton(FIF.SEARCH, "", self.search_card)
        self.search_btn.clicked.connect(self._run_search)
        query_row.addWidget(self.query_edit, 3)
        query_row.addWidget(self.mode_combo, 1)
        query_row.addWidget(self.search_btn)
        box.addLayout(query_row)

        self.status_label = CaptionLabel("", self.search_card)
        self.status_label.setWordWrap(True)
        box.addWidget(self.status_label)

        self.results = TableWidget(self.search_card)
        self.results.setColumnCount(2)
        self.results.setBorderVisible(True)
        self.results.setBorderRadius(8)
        self.results.verticalHeader().hide()
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        box.addWidget(self.results)
        root.addWidget(self.search_card)

    # -- 다국어 ------------------------------------------------------
    def retranslate(self) -> None:
        self.title_label.setText(i18n.t("search.title"))
        self.pin_prompt_label.setText(i18n.t("search.pin_prompt"))
        self.lock_caveat_label.setText(i18n.t("search.pin_caveat"))
        self.unlock_btn.setText(i18n.t("search.btn.run"))
        self.caveat_label.setText(i18n.t("search.pin_caveat"))
        self.change_pin_btn.setText(i18n.t("search.change_pin"))
        self.pin_warning.setText(i18n.t("search.pin_default_warning"))
        self.indexing_check.setText(i18n.t("search.enable_indexing"))
        self.query_edit.setPlaceholderText(i18n.t("search.query_placeholder"))
        self.search_btn.setText(i18n.t("search.btn.run"))
        for index, key in enumerate(
            ("search.mode.exact", "search.mode.prefix", "search.mode.contains")
        ):
            self.mode_combo.setItemText(index, i18n.t(key))
        self.results.setHorizontalHeaderLabels(
            [i18n.t("search.col.path"), i18n.t("search.col.kind")]
        )

    def reload_config(self, config: config_module.AppConfig) -> None:
        self._config = config
        self.account_combo.clear()
        for account in self._config.accounts:
            self.account_combo.addItem(account.name, userData=account.account_id)
        self.retranslate()
        if self._session.is_unlocked:
            self._refresh_state()

    # -- 잠금 --------------------------------------------------------
    def _apply_lock_state(self) -> None:
        unlocked = self._session.is_unlocked
        self.lock_card.setVisible(not unlocked)
        self.search_card.setVisible(unlocked)
        if unlocked:
            self.pin_warning.setVisible(
                admin_auth.is_using_default(self._config.settings.admin_pin_hash)
            )
            self._refresh_state()

    def _try_unlock(self) -> None:
        pin = self.pin_edit.text()
        self.pin_edit.clear()
        if not self._session.unlock(pin, self._config.settings.admin_pin_hash):
            InfoBar.error(
                title=i18n.t("search.pin_title"),
                content=i18n.t("search.pin_wrong"),
                position=InfoBarPosition.TOP_RIGHT,
                parent=self,
            )
            return
        self._apply_lock_state()

    def _change_pin(self) -> None:
        dialog = PinChangeDialog(self._data_dir, self._config, self.window())
        if dialog.exec():
            self.pin_warning.setVisible(
                admin_auth.is_using_default(self._config.settings.admin_pin_hash)
            )

    # -- 동작 ---------------------------------------------------------
    def _selected_account(self):
        account_id = self.account_combo.currentData()
        return (
            config_module.find_account(self._config, account_id) if account_id else None
        )

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
            "search.db_size",
            size=widgets.format_bytes(search_index.db_size_bytes(self._data_dir)),
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
            InfoBar.error(
                title=i18n.t("accounts.save_failed"),
                content=str(exc),
                position=InfoBarPosition.TOP_RIGHT,
                parent=self,
            )
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
        self.status_label.setText(
            i18n.t("search.result_count", count=len(hits), limit=limit)
        )

    def _on_index_finished(self, count: int) -> None:
        self._refresh_state()

    def _on_index_failed(self, message: str) -> None:
        self.status_label.setText(message)

    def shutdown(self) -> None:
        self._worker.request_stop()


class PinChangeDialog(MessageBox):
    """관리자 PIN 변경.

    PIN은 평문으로 저장하지 않고 해시만 남긴다. 다만 이것이 이 장치의 성격을
    바꾸지는 않는다 - 검색 인덱스 DB는 여전히 암호화되지 않으며, 파일을 직접
    읽을 수 있는 사람은 PIN과 무관하게 내용을 볼 수 있다.
    """

    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(i18n.t("pin.change_title"), i18n.t("search.pin_caveat"), parent)
        self._data_dir = data_dir
        self._config = config

        self.current_edit = PasswordLineEdit(self)
        self.current_edit.setPlaceholderText(i18n.t("pin.current"))
        self.new_edit = PasswordLineEdit(self)
        self.new_edit.setPlaceholderText(i18n.t("pin.new"))
        self.confirm_edit = PasswordLineEdit(self)
        self.confirm_edit.setPlaceholderText(i18n.t("pin.confirm"))

        for widget in (self.current_edit, self.new_edit, self.confirm_edit):
            self.textLayout.addWidget(widget)

        self.yesButton.setText(i18n.t("common.save"))
        self.cancelButton.setText(i18n.t("common.cancel"))
        self.yesButton.clicked.disconnect()
        self.yesButton.clicked.connect(self._save)

    def _save(self) -> None:
        stored = self._config.settings.admin_pin_hash
        if not admin_auth.verify_pin(self.current_edit.text(), stored):
            self._warn(i18n.t("pin.current_wrong"))
            return

        new_pin = self.new_edit.text()
        if len(new_pin) < admin_auth.MIN_PIN_LENGTH:
            self._warn(i18n.t("pin.too_short", min_length=admin_auth.MIN_PIN_LENGTH))
            return
        if new_pin != self.confirm_edit.text():
            self._warn(i18n.t("pin.mismatch"))
            return

        self._config.settings.admin_pin_hash = admin_auth.hash_pin(new_pin)
        try:
            config_module.save_config(self._data_dir, self._config)
        except config_module.ConfigError as exc:
            self._warn(str(exc))
            return
        self.accept()

    def _warn(self, message: str) -> None:
        InfoBar.warning(
            title=i18n.t("pin.change_title"),
            content=message,
            position=InfoBarPosition.TOP,
            parent=self,
        )
