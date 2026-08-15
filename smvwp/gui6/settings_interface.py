"""설정 서브 인터페이스 - 계정 관리 + 전역 설정 + 언어.

PyQt5 판의 `AccountDialog`에 해당한다. 다이얼로그였던 것을 좌측 네비게이션의
독립 화면으로 올렸다.

알림 command와 quota command는 JSON 배열로 입력받는다. shell 문자열이 아니라
argv 배열이어야 특수문자가 재해석되지 않고, 잘못된 JSON은 저장 시점에 막아
나중에 cron에서 조용히 실패하는 일을 방지한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ListWidget,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    SubtitleLabel,
)
from qfluentwidgets import FluentIcon as FIF

from .. import config as config_module
from .. import i18n, readability

ACCOUNT_ID_ROLE = 1000


class SettingsInterface(QWidget):
    """계정 목록 관리 + 전역 설정."""

    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsInterface")
        self._data_dir = data_dir
        self._config = config
        self._on_saved = None
        self._build_ui()
        self.retranslate()
        self._reload_list()

    def set_saved_callback(self, callback) -> None:
        """저장이 끝나면 호출할 콜백 (메인 창이 다른 화면을 갱신하도록)."""

        self._on_saved = callback

    # -- UI 구성 -----------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        self.title_label = SubtitleLabel("", self)
        root.addWidget(self.title_label)

        # -- 계정 카드 --
        account_card = CardWidget(self)
        account_layout = QVBoxLayout(account_card)
        account_layout.setContentsMargins(18, 14, 18, 14)
        account_layout.setSpacing(10)

        self.accounts_label = StrongBodyLabel("", account_card)
        account_layout.addWidget(self.accounts_label)

        self.account_list = ListWidget(account_card)
        self.account_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        account_layout.addWidget(self.account_list)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.name_edit = LineEdit(account_card)
        self.path_edit = LineEdit(account_card)
        self.browse_btn = PushButton(FIF.FOLDER, "", account_card)
        self.browse_btn.clicked.connect(self._browse_path)
        self.add_btn = PrimaryPushButton(FIF.ADD, "", account_card)
        self.add_btn.clicked.connect(self._add_account)
        add_row.addWidget(self.name_edit, 2)
        add_row.addWidget(self.path_edit, 3)
        add_row.addWidget(self.browse_btn)
        add_row.addWidget(self.add_btn)
        account_layout.addLayout(add_row)

        self.remove_btn = PushButton(FIF.DELETE, "", account_card)
        self.remove_btn.clicked.connect(self._remove_selected)
        account_layout.addWidget(self.remove_btn)
        root.addWidget(account_card)

        # -- 전역 설정 카드 --
        settings_card = CardWidget(self)
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(18, 14, 18, 14)
        settings_layout.setSpacing(10)

        self.global_label = StrongBodyLabel("", settings_card)
        settings_layout.addWidget(self.global_label)

        form = QFormLayout()
        form.setSpacing(10)
        settings = self._config.settings

        self.language_combo = ComboBox(settings_card)
        for code in i18n.available_languages():
            self.language_combo.addItem(i18n.language_name(code), userData=code)
        index = self.language_combo.findData(settings.language)
        self.language_combo.setCurrentIndex(max(0, index))

        self.interval_spin = SpinBox(settings_card)
        self.interval_spin.setRange(1, 24 * 60)
        self.interval_spin.setValue(settings.collector_interval_seconds // 60)

        self.cooldown_spin = SpinBox(settings_card)
        self.cooldown_spin.setRange(0, 24 * 60)
        self.cooldown_spin.setValue(settings.notification_cooldown_minutes)

        self.retention_spin = SpinBox(settings_card)
        self.retention_spin.setRange(1, 3650)
        self.retention_spin.setValue(settings.sample_retention_days)

        self.mode_combo = ComboBox(settings_card)
        for mode, key in (
            (config_module.NOTIFY_MODE_OUTBOX, "notify.mode.outbox"),
            (config_module.NOTIFY_MODE_COMMAND, "notify.mode.command"),
            (config_module.NOTIFY_MODE_WEBHOOK, "notify.mode.webhook"),
            (config_module.NOTIFY_MODE_DISABLED, "notify.mode.disabled"),
        ):
            self.mode_combo.addItem(i18n.t(key), userData=mode)
        index = self.mode_combo.findData(settings.notification_mode)
        self.mode_combo.setCurrentIndex(max(0, index))

        self.command_edit = LineEdit(settings_card)
        self.command_edit.setText(
            json.dumps(settings.notification_command, ensure_ascii=False)
        )
        self.command_edit.setPlaceholderText('["/opt/company/bin/send", "storage-alert"]')

        self.webhook_edit = LineEdit(settings_card)
        self.webhook_edit.setText(settings.notification_webhook_url)
        self.webhook_edit.setPlaceholderText("https://internal.example/message/storage")

        self.quota_edit = LineEdit(settings_card)
        self.quota_edit.setText(json.dumps(settings.quota_command, ensure_ascii=False))
        self.quota_edit.setPlaceholderText(
            '["/opt/company/bin/quota-json", "{account}", "{path}"]'
        )

        self._form_labels = {}
        for key, widget in (
            ("accounts.language", self.language_combo),
            ("accounts.interval", self.interval_spin),
            ("accounts.cooldown", self.cooldown_spin),
            ("accounts.retention", self.retention_spin),
            ("accounts.notification_mode", self.mode_combo),
            ("accounts.notification_command", self.command_edit),
            ("accounts.notification_webhook", self.webhook_edit),
            ("accounts.quota_command", self.quota_edit),
        ):
            label = BodyLabel("", settings_card)
            self._form_labels[key] = label
            form.addRow(label, widget)
        settings_layout.addLayout(form)
        root.addWidget(settings_card)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.save_btn = PrimaryPushButton(FIF.SAVE, "", self)
        self.save_btn.clicked.connect(self._save)
        button_row.addWidget(self.save_btn)
        root.addLayout(button_row)
        root.addStretch(1)

    # -- 다국어 ------------------------------------------------------
    def retranslate(self) -> None:
        self.title_label.setText(i18n.t("accounts.title"))
        self.accounts_label.setText(i18n.t("accounts.registered"))
        self.global_label.setText(i18n.t("accounts.global_settings"))
        self.name_edit.setPlaceholderText(i18n.t("accounts.name_placeholder"))
        self.path_edit.setPlaceholderText(i18n.t("accounts.path_placeholder"))
        self.browse_btn.setText(i18n.t("accounts.btn.browse"))
        self.add_btn.setText(i18n.t("accounts.btn.add"))
        self.remove_btn.setText(i18n.t("accounts.btn.remove"))
        self.save_btn.setText(i18n.t("common.save"))
        for key, label in self._form_labels.items():
            label.setText(i18n.t(key))

    # -- 동작 ---------------------------------------------------------
    def _reload_list(self) -> None:
        self.account_list.clear()
        for account in self._config.accounts:
            self.account_list.addItem(f"{account.name}  -  {account.path}")
            item = self.account_list.item(self.account_list.count() - 1)
            item.setData(ACCOUNT_ID_ROLE, account.account_id)

    def _browse_path(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, i18n.t("accounts.browse_title")
        )
        if selected:
            self.path_edit.setText(selected)

    def _add_account(self) -> None:
        name = self.name_edit.text().strip()
        path = self.path_edit.text().strip()
        if not name or not path:
            InfoBar.warning(
                title=i18n.t("accounts.input_required_title"),
                content=i18n.t("accounts.input_required_body"),
                position=InfoBarPosition.TOP_RIGHT,
                parent=self,
            )
            return

        # 읽기 권한은 등록하는 이 자리에서 알려준다. 최상위만 R_OK로 보고
        # 넘기면, 하위가 막혀 크기가 축소 측정된다는 사실이 며칠 뒤 야간 스캔
        # 이후에야 드러난다.
        if not self._confirm_readability(path):
            return

        try:
            config_module.add_account(self._config, name, path, data_dir=self._data_dir)
        except config_module.ConfigError as exc:
            InfoBar.error(
                title=i18n.t("accounts.add_failed"),
                content=str(exc),
                position=InfoBarPosition.TOP_RIGHT,
                duration=6000,
                parent=self,
            )
            return
        self.name_edit.clear()
        self.path_edit.clear()
        self._reload_list()

    def _confirm_readability(self, path: str) -> bool:
        """읽기 권한을 표본 조사하고, 문제가 있으면 계속할지 묻는다.

        막힌 하위가 있어도 등록 자체를 막지는 않는다 - df 기반 사용률과 알림은
        정상 동작하므로 여전히 쓸모가 있고, 비관리자에게는 그게 유일한 선택지일
        수 있다. 다만 무엇이 부정확해지는지는 미리 알아야 한다."""

        try:
            result = readability.probe(Path(path).expanduser())
        except OSError:
            return True  # 조사 자체가 실패하면 기존 검증에 맡긴다

        if not result.has_findings:
            return True

        box = MessageBox(
            i18n.t("readability.title"),
            f"{readability.describe(result)}\n\n{i18n.t('readability.register_anyway')}",
            self.window(),
        )
        return bool(box.exec())

    def _remove_selected(self) -> None:
        item = self.account_list.currentItem()
        if item is None:
            return
        account_id = item.data(ACCOUNT_ID_ROLE)
        account = config_module.find_account(self._config, account_id)
        label = account.name if account else account_id
        box = MessageBox(
            i18n.t("accounts.remove_title"),
            i18n.t("accounts.remove_body", name=label),
            self.window(),
        )
        if not box.exec():
            return
        config_module.remove_account(self._config, account_id)
        self._reload_list()

    def _parse_json_argv(self, text: str, field_label: str) -> List[str]:
        """JSON 배열 문자열을 argv 리스트로. 비면 빈 리스트."""

        text = text.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise config_module.ConfigError(
                f"{field_label}: JSON 형식이 아닙니다 ({exc})"
            ) from exc
        if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
            raise config_module.ConfigError(f"{field_label}: 문자열 배열이어야 합니다")
        return value

    def _save(self) -> None:
        settings = self._config.settings
        try:
            notification_command = self._parse_json_argv(
                self.command_edit.text(), i18n.t("accounts.notification_command")
            )
            quota_command = self._parse_json_argv(
                self.quota_edit.text(), i18n.t("accounts.quota_command")
            )
        except config_module.ConfigError as exc:
            InfoBar.error(
                title=i18n.t("accounts.save_failed"),
                content=str(exc),
                position=InfoBarPosition.TOP_RIGHT,
                duration=6000,
                parent=self,
            )
            return

        mode = self.mode_combo.currentData()
        webhook_url = self.webhook_edit.text().strip()
        # 저장 전에 조합을 검사한다 - 여기서 막지 않으면 나중에 cron이 조용히
        # outbox로 떨어져 "왜 알림이 안 오지"가 된다.
        if mode == config_module.NOTIFY_MODE_COMMAND and not notification_command:
            self._save_error("notification_mode가 command면 notification_command가 필요합니다")
            return
        if mode == config_module.NOTIFY_MODE_WEBHOOK and not webhook_url:
            self._save_error("notification_mode가 webhook이면 notification_webhook_url이 필요합니다")
            return

        settings.language = self.language_combo.currentData()
        settings.collector_interval_seconds = self.interval_spin.value() * 60
        settings.notification_cooldown_minutes = self.cooldown_spin.value()
        settings.sample_retention_days = self.retention_spin.value()
        settings.notification_mode = mode
        settings.notification_command = notification_command
        settings.notification_webhook_url = webhook_url
        settings.quota_command = quota_command

        try:
            config_module.save_config(self._data_dir, self._config)
        except config_module.ConfigError as exc:
            self._save_error(str(exc))
            return

        i18n.set_language(settings.language)
        InfoBar.success(
            title=i18n.t("accounts.title"),
            content=i18n.t("common.save"),
            position=InfoBarPosition.TOP_RIGHT,
            parent=self,
        )
        if self._on_saved is not None:
            self._on_saved()

    def _save_error(self, message: str) -> None:
        InfoBar.error(
            title=i18n.t("accounts.save_failed"),
            content=message,
            position=InfoBarPosition.TOP_RIGHT,
            duration=6000,
            parent=self,
        )
