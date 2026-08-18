"""계정 하나의 상세 스캔 진행 상황 (읽기 전용).

대시보드의 상태 줄은 "지금 어디를 보고 있나" 한 줄이면 충분하지만, 밤새 돈
결과가 이상할 때는 **어디까지 했고 무엇이 실패했는지**를 경로 단위로 봐야 한다.
그 용도의 화면이라 목록을 그대로 보여 주고 아무것도 실행하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import config as config_module
from .. import i18n, scan_store
from . import theme, widgets

COLUMN_KEYS = [
    "progress.col.path",
    "progress.col.status",
    "progress.col.result",
    "progress.col.scanned_at",
]
COL_PATH, COL_STATUS, COL_RESULT, COL_TIME = range(4)

# 상태별 표시. 색은 등급 색을 재사용하지 않는다 - 여기 상태는 '심각도'가 아니라
# '진행 단계'라서, 같은 색 체계를 쓰면 경고로 오해된다.
STATUS_KEYS = {
    scan_store.STATUS_PENDING: "progress.status.pending",
    scan_store.STATUS_DONE: "progress.status.done",
    scan_store.STATUS_SPLIT: "progress.status.split",
    scan_store.STATUS_ERROR: "progress.status.error",
}


class ScanProgressDialog(QDialog):
    def __init__(self, data_dir: Path, config: config_module.AppConfig, account_id=None, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._config = config
        self.setWindowTitle(i18n.t("progress.title"))
        self.resize(900, 620)
        self._build_ui()
        if account_id:
            index = self.account_combo.findData(account_id)
            if index >= 0:
                self.account_combo.setCurrentIndex(index)
        self._reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.account_combo = QComboBox()
        for account in self._config.accounts:
            self.account_combo.addItem(account.name, account.account_id)
        self.account_combo.currentIndexChanged.connect(self._reload)
        self.kind_combo = QComboBox()
        self.kind_combo.addItem(i18n.t("progress.kind.baseline"), scan_store.BASELINE)
        self.kind_combo.addItem(i18n.t("progress.kind.activity"), scan_store.ACTIVITY)
        self.kind_combo.currentIndexChanged.connect(self._reload)
        refresh_btn = QPushButton(i18n.t("progress.btn.refresh"))
        refresh_btn.clicked.connect(self._reload)
        top.addWidget(QLabel(i18n.t("scan.account_label")))
        top.addWidget(self.account_combo)
        top.addWidget(QLabel(i18n.t("progress.kind_label")))
        top.addWidget(self.kind_combo)
        top.addWidget(refresh_btn)
        top.addStretch(1)
        root.addLayout(top)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("muted")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        self.table = QTableWidget(0, len(COLUMN_KEYS))
        self.table.setHorizontalHeaderLabels([i18n.t(key) for key in COLUMN_KEYS])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_PATH, QHeaderView.Stretch)
        header.setHighlightSections(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setShowGrid(False)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        close_btn = QPushButton(i18n.t("common.close"))
        close_btn.clicked.connect(self.accept)
        bottom.addStretch(1)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def _reload(self) -> None:
        account_id = self.account_combo.currentData()
        kind = self.kind_combo.currentData()
        if not account_id:
            self.summary_label.setText(i18n.t("search.no_account"))
            self.table.setRowCount(0)
            return

        conn = scan_store.connect(self._data_dir)
        try:
            state = scan_store.get_account_state(conn, account_id)
            generation = (
                state.working_generation
                if kind == scan_store.BASELINE
                else state.working_activity_pass
            )
            counts = scan_store.checkpoint_progress(conn, account_id, kind, generation)
            rows = scan_store.recent_checkpoints(conn, account_id, kind, generation)
        finally:
            conn.close()

        self.summary_label.setText(
            i18n.t(
                "progress.summary",
                generation=generation,
                done=counts["done"],
                pending=counts["pending"],
                split=counts["split"],
                error=counts["error"],
                total=counts["total"],
            )
        )

        dash = i18n.t("common.none")
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            path_item = QTableWidgetItem(row["path"])
            path_item.setToolTip(row["path"])
            self.table.setItem(index, COL_PATH, path_item)

            status = row["status"]
            status_item = QTableWidgetItem(i18n.t(STATUS_KEYS.get(status, "common.none")))
            if status == scan_store.STATUS_ERROR:
                status_item.setForeground(QColor(theme.DANGER))
                if row["error_message"]:
                    status_item.setToolTip(row["error_message"])
            elif status == scan_store.STATUS_PENDING:
                status_item.setForeground(QColor(theme.TEXT_FAINT))
            self.table.setItem(index, COL_STATUS, status_item)

            # 기준선은 크기, 활동 스캔은 변경 파일 수가 결과다.
            if row["size_kb"] is not None:
                result = widgets.format_kb(row["size_kb"])
            elif row["changed_count"] is not None:
                result = i18n.t("progress.changed", count=row["changed_count"])
            else:
                result = dash
            result_item = QTableWidgetItem(result)
            result_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if result == dash:
                result_item.setForeground(QColor(theme.TEXT_FAINT))
            self.table.setItem(index, COL_RESULT, result_item)

            scanned = row["scanned_at"]
            time_item = QTableWidgetItem(str(scanned)[:19].replace("T", " ") if scanned else dash)
            if not scanned:
                time_item.setForeground(QColor(theme.TEXT_FAINT))
            self.table.setItem(index, COL_TIME, time_item)
