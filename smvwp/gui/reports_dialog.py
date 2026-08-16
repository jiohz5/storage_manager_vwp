"""보고서 보기/생성 다이얼로그.

대시보드는 단일 화면을 유지하고(DESIGN.md 2부 6절), 가끔 쓰는 보고서는
다이얼로그로 뺐다. 보고서는 텍스트 파일이므로 여기서는 읽어서 그대로 보여주고
저장 위치를 알려 주기만 한다 - 이 화면이 파일을 지우는 일은 없다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .. import config as config_module
from .. import i18n, reports


class ReportsDialog(QDialog):
    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._config = config
        self.setWindowTitle(i18n.t("reports.title"))
        self.resize(820, 560)
        self._build_ui()
        self._load_selected()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        top_row = QHBoxLayout()
        self.kind_combo = QComboBox()
        for kind, key in (
            (reports.DAILY, "reports.daily"),
            (reports.WEEKLY, "reports.weekly"),
            (reports.CLEANUP, "reports.cleanup"),
        ):
            self.kind_combo.addItem(i18n.t(key), kind)
        self.kind_combo.currentIndexChanged.connect(self._load_selected)
        top_row.addWidget(self.kind_combo)

        self.generate_btn = QPushButton(i18n.t("reports.generate"))
        self.generate_btn.setObjectName("primary")
        self.generate_btn.clicked.connect(self._generate)
        top_row.addWidget(self.generate_btn)
        top_row.addStretch(1)
        root.addLayout(top_row)

        self.path_label = QLabel("")
        self.path_label.setStyleSheet("color: #757575; font-size: 9pt;")
        self.path_label.setWordWrap(True)
        root.addWidget(self.path_label)

        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        # 보고서는 열을 맞춰 쓰므로 고정폭 글꼴이 아니면 표가 어긋난다.
        self.viewer.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        root.addWidget(self.viewer)

        close_btn = QPushButton(i18n.t("common.close"))
        close_btn.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def _selected_kind(self) -> str:
        return self.kind_combo.currentData()

    def _load_selected(self) -> None:
        path = reports.latest_path(self._data_dir, self._selected_kind(), i18n.get_language())
        if not path.exists():
            self.viewer.setPlainText(i18n.t("reports.none"))
            self.path_label.setText("")
            return
        self.viewer.setPlainText(path.read_text(encoding="utf-8"))
        self.path_label.setText(str(path))

    def _generate(self) -> None:
        created = reports.generate(
            self._data_dir, self._config, kinds=[self._selected_kind()]
        )
        path = created.get(self._selected_kind())
        if path is not None:
            self.path_label.setText(i18n.t("reports.generated", path=path))
        self._load_selected()
