"""계정 등록/설정 다이얼로그.

REBUILD_CONCEPT.md 6절 결정: 계정 등록/설정처럼 자주 쓰지 않는 동작은
대시보드가 아니라 별도 다이얼로그로 분리한다.

알림 command와 quota command는 JSON 배열로 입력받는다. shell 문자열이 아니라
argv 배열이어야 특수문자가 재해석되지 않기 때문이고, 잘못된 JSON은 저장 시점에
막아 나중에 cron에서 조용히 실패하는 일을 방지한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .. import config as config_module
from .. import i18n, readability


class AccountDialog(QDialog):
    """계정 목록 관리 + 수집 주기/알림/quota 등 전역 설정."""

    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._config = config
        self.setWindowTitle(i18n.t("accounts.title"))
        self.resize(620, 620)
        self._build_ui()
        self._reload_list()

    # -- UI 구성 -----------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        root.addWidget(QLabel(i18n.t("accounts.registered")))
        self.account_list = QListWidget()
        self.account_list.setSelectionMode(QAbstractItemView.SingleSelection)
        root.addWidget(self.account_list)

        add_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(i18n.t("accounts.name_placeholder"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(i18n.t("accounts.path_placeholder"))
        browse_btn = QPushButton(i18n.t("accounts.btn.browse"))
        browse_btn.clicked.connect(self._browse_path)
        add_btn = QPushButton(i18n.t("accounts.btn.add"))
        add_btn.clicked.connect(self._add_account)
        add_row.addWidget(self.name_edit)
        add_row.addWidget(self.path_edit)
        add_row.addWidget(browse_btn)
        add_row.addWidget(add_btn)
        root.addLayout(add_row)

        remove_btn = QPushButton(i18n.t("accounts.btn.remove"))
        remove_btn.clicked.connect(self._remove_selected)
        root.addWidget(remove_btn)

        root.addWidget(QLabel(i18n.t("accounts.global_settings")))
        form = QFormLayout()
        settings = self._config.settings

        self.language_combo = QComboBox()
        for code in i18n.available_languages():
            self.language_combo.addItem(i18n.language_name(code), code)
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(settings.language))
        )
        form.addRow(i18n.t("accounts.language"), self.language_combo)

        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 24 * 60)
        self.interval_spin.setSuffix(i18n.t("accounts.suffix.minutes"))
        self.interval_spin.setValue(settings.collector_interval_seconds // 60)
        form.addRow(i18n.t("accounts.interval"), self.interval_spin)

        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(0, 24 * 60)
        self.cooldown_spin.setSuffix(i18n.t("accounts.suffix.minutes"))
        self.cooldown_spin.setValue(settings.notification_cooldown_minutes)
        form.addRow(i18n.t("accounts.cooldown"), self.cooldown_spin)

        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(1, 3650)
        self.retention_spin.setSuffix(i18n.t("accounts.suffix.days"))
        self.retention_spin.setValue(settings.sample_retention_days)
        form.addRow(i18n.t("accounts.retention"), self.retention_spin)

        self.mode_combo = QComboBox()
        for mode, key in (
            (config_module.NOTIFY_MODE_OUTBOX, "notify.mode.outbox"),
            (config_module.NOTIFY_MODE_COMMAND, "notify.mode.command"),
            (config_module.NOTIFY_MODE_WEBHOOK, "notify.mode.webhook"),
            (config_module.NOTIFY_MODE_DISABLED, "notify.mode.disabled"),
        ):
            self.mode_combo.addItem(i18n.t(key), mode)
        self.mode_combo.setCurrentIndex(
            max(0, self.mode_combo.findData(settings.notification_mode))
        )
        form.addRow(i18n.t("accounts.notification_mode"), self.mode_combo)

        self.command_edit = QLineEdit(json.dumps(settings.notification_command, ensure_ascii=False))
        self.command_edit.setPlaceholderText('["/opt/company/bin/send", "storage-alert"]')
        form.addRow(i18n.t("accounts.notification_command"), self.command_edit)

        self.webhook_edit = QLineEdit(settings.notification_webhook_url)
        self.webhook_edit.setPlaceholderText("https://internal.example/message/storage")
        form.addRow(i18n.t("accounts.notification_webhook"), self.webhook_edit)

        self.quota_edit = QLineEdit(json.dumps(settings.quota_command, ensure_ascii=False))
        self.quota_edit.setPlaceholderText('["/opt/company/bin/quota-json", "{account}", "{path}"]')
        form.addRow(i18n.t("accounts.quota_command"), self.quota_edit)

        root.addLayout(form)

        button_row = QHBoxLayout()
        save_btn = QPushButton(i18n.t("common.save"))
        save_btn.clicked.connect(self._save_and_close)
        cancel_btn = QPushButton(i18n.t("common.cancel"))
        cancel_btn.clicked.connect(self.reject)
        button_row.addStretch(1)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(save_btn)
        root.addLayout(button_row)

    # -- 동작 ---------------------------------------------------------
    def _reload_list(self) -> None:
        self.account_list.clear()
        for account in self._config.accounts:
            item = QListWidgetItem(f"{account.name}  -  {account.path}")
            item.setData(1000, account.account_id)
            self.account_list.addItem(item)

    def _browse_path(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, i18n.t("accounts.browse_title"))
        if selected:
            self.path_edit.setText(selected)

    def _add_account(self) -> None:
        name = self.name_edit.text().strip()
        path = self.path_edit.text().strip()
        if not name or not path:
            QMessageBox.warning(
                self,
                i18n.t("accounts.input_required_title"),
                i18n.t("accounts.input_required_body"),
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
            QMessageBox.critical(self, i18n.t("accounts.add_failed"), str(exc))
            return
        self.name_edit.clear()
        self.path_edit.clear()
        self._reload_list()

    def _confirm_readability(self, path: str) -> bool:
        """읽기 권한을 표본 조사하고, 문제가 있으면 계속할지 묻는다.

        막힌 하위가 있어도 등록 자체를 막지는 않는다 - df 기반 사용률과 알림은
        정상 동작하므로 여전히 쓸모가 있고, 비관리자에게는 그게 유일한 선택지일
        수 있다. 다만 무엇이 부정확해지는지는 미리 알아야 한다.
        """

        try:
            result = readability.probe(Path(path).expanduser())
        except OSError:
            return True  # 조사 자체가 실패하면 기존 검증에 맡긴다

        if not result.has_findings:
            return True

        reply = QMessageBox.question(
            self,
            i18n.t("readability.title"),
            f"{readability.describe(result)}\n\n{i18n.t('readability.register_anyway')}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        return reply == QMessageBox.Yes

    def _remove_selected(self) -> None:
        item = self.account_list.currentItem()
        if item is None:
            return
        account_id = item.data(1000)
        account = config_module.find_account(self._config, account_id)
        label = account.name if account else account_id
        confirm = QMessageBox.question(
            self, i18n.t("accounts.remove_title"), i18n.t("accounts.remove_body", name=label)
        )
        if confirm != QMessageBox.Yes:
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
            raise config_module.ConfigError(f"{field_label}: JSON 형식이 아닙니다 ({exc})") from exc
        if not isinstance(value, list) or not all(isinstance(part, str) for part in value):
            raise config_module.ConfigError(f"{field_label}: 문자열 배열이어야 합니다")
        return value

    def _save_and_close(self) -> None:
        settings = self._config.settings
        try:
            notification_command = self._parse_json_argv(
                self.command_edit.text(), i18n.t("accounts.notification_command")
            )
            quota_command = self._parse_json_argv(
                self.quota_edit.text(), i18n.t("accounts.quota_command")
            )
        except config_module.ConfigError as exc:
            QMessageBox.critical(self, i18n.t("accounts.save_failed"), str(exc))
            return

        mode = self.mode_combo.currentData()
        webhook_url = self.webhook_edit.text().strip()
        # 저장 전에 조합을 검사한다 - 여기서 막지 않으면 나중에 cron이 조용히
        # outbox로 떨어져 "왜 알림이 안 오지"가 된다.
        if mode == config_module.NOTIFY_MODE_COMMAND and not notification_command:
            QMessageBox.critical(
                self,
                i18n.t("accounts.save_failed"),
                "notification_mode가 command면 notification_command가 필요합니다",
            )
            return
        if mode == config_module.NOTIFY_MODE_WEBHOOK and not webhook_url:
            QMessageBox.critical(
                self,
                i18n.t("accounts.save_failed"),
                "notification_mode가 webhook이면 notification_webhook_url이 필요합니다",
            )
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
            QMessageBox.critical(self, i18n.t("accounts.save_failed"), str(exc))
            return
        self.accept()
