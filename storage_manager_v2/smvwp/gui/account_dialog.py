"""계정 등록/설정 다이얼로그.

REBUILD_CONCEPT.md 6절 결정: 계정 등록/설정처럼 자주 쓰지 않는 동작은
대시보드가 아니라 별도 다이얼로그로 분리한다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import (
    QAbstractItemView,
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


class AccountDialog(QDialog):
    """계정 목록 관리 + 수집 주기/알림 cooldown 등 전역 설정."""

    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._config = config
        self.setWindowTitle("계정 관리 / 설정")
        self.resize(560, 420)
        self._build_ui()
        self._reload_list()

    # -- UI 구성 -----------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        root.addWidget(QLabel("등록된 계정"))
        self.account_list = QListWidget()
        self.account_list.setSelectionMode(QAbstractItemView.SingleSelection)
        root.addWidget(self.account_list)

        add_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("계정 이름 (예: project_a)")
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("모니터링 대상 경로 (예: /user/project_a)")
        browse_btn = QPushButton("경로 찾기...")
        browse_btn.clicked.connect(self._browse_path)
        add_btn = QPushButton("계정 추가")
        add_btn.clicked.connect(self._add_account)
        add_row.addWidget(self.name_edit)
        add_row.addWidget(self.path_edit)
        add_row.addWidget(browse_btn)
        add_row.addWidget(add_btn)
        root.addLayout(add_row)

        remove_btn = QPushButton("선택한 계정 삭제")
        remove_btn.clicked.connect(self._remove_selected)
        root.addWidget(remove_btn)

        root.addWidget(QLabel("전역 설정"))
        form = QFormLayout()
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 24 * 60)
        self.interval_spin.setSuffix(" 분")
        self.interval_spin.setValue(self._config.settings.collector_interval_seconds // 60)
        form.addRow("수집 주기", self.interval_spin)

        self.cooldown_spin = QSpinBox()
        self.cooldown_spin.setRange(0, 24 * 60)
        self.cooldown_spin.setSuffix(" 분")
        self.cooldown_spin.setValue(self._config.settings.notification_cooldown_minutes)
        form.addRow("알림 재발송 대기(cooldown)", self.cooldown_spin)

        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(1, 3650)
        self.retention_spin.setSuffix(" 일")
        self.retention_spin.setValue(self._config.settings.sample_retention_days)
        form.addRow("표본 보존 기간", self.retention_spin)
        root.addLayout(form)

        button_row = QHBoxLayout()
        save_btn = QPushButton("저장")
        save_btn.clicked.connect(self._save_and_close)
        cancel_btn = QPushButton("취소")
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
        selected = QFileDialog.getExistingDirectory(self, "모니터링 대상 디렉터리 선택")
        if selected:
            self.path_edit.setText(selected)

    def _add_account(self) -> None:
        name = self.name_edit.text().strip()
        path = self.path_edit.text().strip()
        if not name or not path:
            QMessageBox.warning(self, "입력 필요", "계정 이름과 경로를 모두 입력하세요.")
            return
        try:
            config_module.add_account(self._config, name, path)
        except config_module.ConfigError as exc:
            QMessageBox.critical(self, "계정 추가 실패", str(exc))
            return
        self.name_edit.clear()
        self.path_edit.clear()
        self._reload_list()

    def _remove_selected(self) -> None:
        item = self.account_list.currentItem()
        if item is None:
            return
        account_id = item.data(1000)
        account = config_module.find_account(self._config, account_id)
        label = account.name if account else account_id
        confirm = QMessageBox.question(
            self,
            "계정 삭제",
            f"'{label}' 계정을 목록에서 삭제할까요? (수집 이력은 남아 있습니다)",
        )
        if confirm != QMessageBox.Yes:
            return
        config_module.remove_account(self._config, account_id)
        self._reload_list()

    def _save_and_close(self) -> None:
        self._config.settings.collector_interval_seconds = self.interval_spin.value() * 60
        self._config.settings.notification_cooldown_minutes = self.cooldown_spin.value()
        self._config.settings.sample_retention_days = self.retention_spin.value()
        try:
            config_module.save_config(self._data_dir, self._config)
        except config_module.ConfigError as exc:
            QMessageBox.critical(self, "저장 실패", str(exc))
            return
        self.accept()
