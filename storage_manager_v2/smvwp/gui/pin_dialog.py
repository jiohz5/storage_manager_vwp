"""관리자 PIN 변경 다이얼로그.

PIN은 평문으로 저장하지 않고 해시(`admin_auth.hash_pin`)만 설정에 남긴다.
다만 이것이 이 장치의 성격을 바꾸지는 않는다 - 검색 인덱스 DB는 여전히
암호화되지 않으며, 파일을 직접 읽을 수 있는 사람은 PIN과 무관하게 내용을 볼
수 있다 (`smvwp/admin_auth.py` 참고). 해시는 "설정 파일을 어깨너머로 봤다고
바로 PIN이 노출되지는 않게" 하는 최소한의 조치일 뿐이다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .. import admin_auth, config as config_module
from .. import i18n


class PinChangeDialog(QDialog):
    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._config = config
        self.setWindowTitle(i18n.t("pin.change_title"))
        self.resize(420, 240)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        caveat = QLabel(i18n.t("search.pin_caveat"))
        caveat.setStyleSheet("color: #757575; font-size: 9pt;")
        caveat.setWordWrap(True)
        root.addWidget(caveat)

        form = QFormLayout()
        self.current_edit = QLineEdit()
        self.current_edit.setEchoMode(QLineEdit.Password)
        form.addRow(i18n.t("pin.current"), self.current_edit)

        self.new_edit = QLineEdit()
        self.new_edit.setEchoMode(QLineEdit.Password)
        form.addRow(i18n.t("pin.new"), self.new_edit)

        self.confirm_edit = QLineEdit()
        self.confirm_edit.setEchoMode(QLineEdit.Password)
        self.confirm_edit.returnPressed.connect(self._save)
        form.addRow(i18n.t("pin.confirm"), self.confirm_edit)
        root.addLayout(form)
        root.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton(i18n.t("common.cancel"))
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton(i18n.t("common.save"))
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        root.addLayout(buttons)

    def _save(self) -> None:
        stored = self._config.settings.admin_pin_hash
        if not admin_auth.verify_pin(self.current_edit.text(), stored):
            QMessageBox.warning(self, i18n.t("pin.change_title"), i18n.t("pin.current_wrong"))
            return

        new_pin = self.new_edit.text()
        if len(new_pin) < admin_auth.MIN_PIN_LENGTH:
            QMessageBox.warning(
                self,
                i18n.t("pin.change_title"),
                i18n.t("pin.too_short", min_length=admin_auth.MIN_PIN_LENGTH),
            )
            return
        if new_pin != self.confirm_edit.text():
            QMessageBox.warning(self, i18n.t("pin.change_title"), i18n.t("pin.mismatch"))
            return

        self._config.settings.admin_pin_hash = admin_auth.hash_pin(new_pin)
        try:
            config_module.save_config(self._data_dir, self._config)
        except config_module.ConfigError as exc:
            QMessageBox.critical(self, i18n.t("accounts.save_failed"), str(exc))
            return

        QMessageBox.information(self, i18n.t("pin.change_title"), i18n.t("pin.changed"))
        self.accept()
