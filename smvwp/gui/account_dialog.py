"""계정 등록/설정 다이얼로그.

DESIGN.md 2부 6절 결정: 계정 등록/설정처럼 자주 쓰지 않는 동작은
대시보드가 아니라 별도 다이얼로그로 분리한다.

알림 command와 quota command는 JSON 배열로 입력받는다. shell 문자열이 아니라
argv 배열이어야 특수문자가 재해석되지 않기 때문이고, 잘못된 JSON은 저장 시점에
막아 나중에 cron에서 조용히 실패하는 일을 방지한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_module
from .. import i18n, readability, scan_store

# 계정 경로의 관례 접두사. 이름을 입력하면 `<접두사><이름>`으로 경로를 채워
# 준다. 사내 관례가 다른 곳에 반입한다면 이 한 줄만 고치면 된다 (설정 항목으로
# 빼지 않은 이유: 한 장비에서 두 관례를 섞어 쓸 일이 없고, 설정이 하나 늘면
# 최초 설치 때 사용자가 판단할 것도 하나 는다).
ACCOUNT_PATH_PREFIX = "/user/"

# 계정 표의 열. "누가 언제 넣었고 마지막 스캔이 언제인가"까지 한눈에 보이도록
# 이름/경로만 있던 목록을 표로 바꿨다.
ACCOUNT_COLUMN_KEYS = [
    "accounts.col.name",
    "accounts.col.path",
    "accounts.col.owner",
    "accounts.col.added",
    "accounts.col.scanned",
]
(
    ACCOUNT_COL_NAME,
    ACCOUNT_COL_PATH,
    ACCOUNT_COL_OWNER,
    ACCOUNT_COL_ADDED,
    ACCOUNT_COL_SCANNED,
) = range(5)

# 행에 account_id를 숨겨 두는 Qt 데이터 롤. 이름은 겹칠 수 있어 식별자가 될 수
# 없으므로 표시값이 아니라 id로 계정을 찾는다.
ACCOUNT_ID_ROLE = Qt.UserRole


def _short_date(iso_text) -> str:
    """ISO 시각을 `YYYY-MM-DD`로 줄인다. 값이 없으면 빈 문자열.

    이 표에서 필요한 것은 "언제쯤"이지 초 단위가 아니다. 시각까지 보여주면
    열만 넓어지고 읽기는 더 어렵다."""

    if not iso_text:
        return ""
    return str(iso_text)[:10]


class AccountDialog(QDialog):
    """계정 목록 관리 + 수집 주기/알림/quota 등 전역 설정."""

    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._config = config
        self.setWindowTitle(i18n.t("accounts.title"))
        # 계정 표가 5열(이름/경로/추가한 사람/추가일/최근 스캔일)이라 예전
        # 폭(620)으로는 경로가 'C:...'로 뭉개진다.
        self.resize(880, 620)
        self._build_ui()
        self._reload_list()

    # -- UI 구성 -----------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        registered_label = QLabel(i18n.t("accounts.registered"))
        registered_label.setObjectName("sectionTitle")
        root.addWidget(registered_label)

        # 한 줄짜리 목록에서 표로 바꿨다. 파트별 담당자가 계정을 나눠 관리하는
        # 형태라 "누가 언제 넣었고 마지막으로 언제 스캔됐나"가 이 화면에서 바로
        # 보여야 한다 - 안 그러면 남이 등록한 계정을 볼 때마다 물어봐야 한다.
        self.account_table = QTableWidget(0, len(ACCOUNT_COLUMN_KEYS))
        self.account_table.setHorizontalHeaderLabels(
            [i18n.t(key) for key in ACCOUNT_COLUMN_KEYS]
        )
        header = self.account_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(ACCOUNT_COL_PATH, QHeaderView.Stretch)
        header.setHighlightSections(False)
        self.account_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.account_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.account_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.account_table.verticalHeader().setVisible(False)
        self.account_table.verticalHeader().setDefaultSectionSize(34)
        self.account_table.setShowGrid(False)
        # 목록이 이 화면의 본체다. 아래 설정에 밀려 두어 줄만 보이면 등록된
        # 계정을 확인하려고 매번 스크롤해야 한다.
        self.account_table.setMinimumHeight(200)
        root.addWidget(self.account_table, 1)

        add_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(i18n.t("accounts.name_placeholder"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(i18n.t("accounts.path_placeholder"))
        # 이름을 치면 경로를 관례대로 채워 준다. 대부분의 계정이
        # `<접두사>/<이름>` 형태라 매번 같은 것을 두 번 입력하게 되기 때문.
        # `textEdited`는 사용자가 직접 친 경우에만 발생하므로(setText로는 안
        # 울린다) 자동 채움이 자기 자신을 다시 트리거하지 않는다.
        self._path_touched = False
        self.name_edit.textEdited.connect(self._autofill_path)
        self.path_edit.textEdited.connect(self._on_path_edited)
        browse_btn = QPushButton(i18n.t("accounts.btn.browse"))
        browse_btn.clicked.connect(self._browse_path)
        add_btn = QPushButton(i18n.t("accounts.btn.add"))
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._add_account)
        add_row.addWidget(self.name_edit)
        add_row.addWidget(self.path_edit)
        add_row.addWidget(browse_btn)
        add_row.addWidget(add_btn)
        root.addLayout(add_row)

        remove_btn = QPushButton(i18n.t("accounts.btn.remove"))
        remove_btn.setObjectName("danger")
        remove_btn.clicked.connect(self._remove_selected)
        root.addWidget(remove_btn)

        # 상세 설정은 기본으로 접어 둔다.
        #
        # 이 화면을 매일 쓰는 일은 계정 등록/확인이고, 알림 채널이나 quota 명령은
        # 최초 한 번 정하면 몇 달을 안 건드린다. 늘 펼쳐 두면 자주 쓰는 것(목록)이
        # 아래로 밀리고, 처음 여는 사람은 "이걸 다 정해야 하나" 싶어진다.
        self.settings_toggle = QToolButton()
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.setChecked(False)
        self.settings_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.settings_toggle.setArrowType(Qt.RightArrow)
        self.settings_toggle.setObjectName("disclosure")
        self.settings_toggle.setText(i18n.t("accounts.advanced_settings"))
        self.settings_toggle.toggled.connect(self._toggle_settings)
        root.addWidget(self.settings_toggle)

        self.settings_panel = QWidget()
        self.settings_panel.setVisible(False)
        panel_box = QVBoxLayout(self.settings_panel)
        panel_box.setContentsMargins(0, 4, 0, 0)
        root.addWidget(self.settings_panel)

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

        panel_box.addLayout(form)

        button_row = QHBoxLayout()
        save_btn = QPushButton(i18n.t("common.save"))
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save_and_close)
        cancel_btn = QPushButton(i18n.t("common.cancel"))
        cancel_btn.clicked.connect(self.reject)
        button_row.addStretch(1)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(save_btn)
        root.addLayout(button_row)

    # -- 동작 ---------------------------------------------------------
    def _reload_list(self) -> None:
        last_scans = self._last_scan_times()
        dash = i18n.t("common.none")
        self.account_table.setRowCount(len(self._config.accounts))
        for row, account in enumerate(self._config.accounts):
            path_item = QTableWidgetItem(account.path)
            path_item.setToolTip(account.path)
            cells = {
                ACCOUNT_COL_NAME: QTableWidgetItem(account.name),
                ACCOUNT_COL_PATH: path_item,
                ACCOUNT_COL_OWNER: QTableWidgetItem(account.created_by or dash),
                ACCOUNT_COL_ADDED: QTableWidgetItem(_short_date(account.created_at) or dash),
                ACCOUNT_COL_SCANNED: QTableWidgetItem(
                    _short_date(last_scans.get(account.account_id)) or dash
                ),
            }
            # 계정 식별자는 이름이 아니라 account_id다 (이름은 겹칠 수 있다).
            cells[ACCOUNT_COL_NAME].setData(ACCOUNT_ID_ROLE, account.account_id)
            for column, item in cells.items():
                self.account_table.setItem(row, column, item)

    def _last_scan_times(self) -> dict:
        """계정별 마지막 기준선 완주 시각. 스캔 DB가 없으면 빈 dict.

        스캔 DB를 못 열어도 계정 관리 자체는 되어야 하므로 조용히 넘어간다 -
        이 열은 참고 정보이지 등록/삭제의 전제가 아니다."""

        try:
            conn = scan_store.connect(self._data_dir)
        except Exception:  # pragma: no cover - 방어적 처리
            return {}
        try:
            return {
                row["account_id"]: row["last_baseline_completed_at"]
                for row in conn.execute(
                    "SELECT account_id, last_baseline_completed_at FROM account_scan_state"
                )
            }
        except Exception:  # pragma: no cover
            return {}
        finally:
            conn.close()

    def _toggle_settings(self, expanded: bool) -> None:
        self.settings_panel.setVisible(expanded)
        self.settings_toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        # 높이만 내용에 맞춘다. `adjustSize()`는 폭까지 sizeHint로 되돌려서
        # 계정 표(5열)가 다시 뭉개진다 - 접었다 폈다 했더니 창이 좁아지는
        # 동작은 사용자 입장에서 고장으로 보인다.
        self.resize(self.width(), self.sizeHint().height())

    def _browse_path(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, i18n.t("accounts.browse_title"))
        if selected:
            self.path_edit.setText(selected)

    def _autofill_path(self, name: str) -> None:
        """이름에서 관례 경로를 만들어 경로 칸에 채운다.

        사용자가 경로를 한 번이라도 직접 고쳤으면 더 이상 건드리지 않는다 -
        입력한 것을 앱이 덮어쓰는 것만큼 짜증나는 동작이 없다. 경로를 비우면
        "관례대로 해 달라"는 뜻으로 보고 자동 채움을 다시 켠다."""

        if self._path_touched:
            return
        name = name.strip()
        self.path_edit.setText(f"{ACCOUNT_PATH_PREFIX}{name}" if name else "")

    def _on_path_edited(self, text: str) -> None:
        self._path_touched = bool(text.strip())

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
        self._path_touched = False  # 다음 계정도 관례대로 채워 준다
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
        row = self.account_table.currentRow()
        if row < 0:
            return
        item = self.account_table.item(row, ACCOUNT_COL_NAME)
        if item is None:
            return
        account_id = item.data(ACCOUNT_ID_ROLE)
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
