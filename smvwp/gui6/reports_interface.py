"""보고서 서브 인터페이스 - 일간/주간/정리 후보.

보고서는 텍스트 파일이므로 여기서는 읽어서 그대로 보여주고 저장 위치를 알려
주기만 한다. **이 화면이 파일을 지우는 일은 없다** (정리 후보 보고서도 목록일
뿐 삭제를 실행하지 않는다).
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFontDatabase
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    CardWidget,
    ComboBox,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    SubtitleLabel,
    TextEdit,
)
from qfluentwidgets import FluentIcon as FIF

from .. import config as config_module
from .. import i18n, reports


class ReportsInterface(QWidget):
    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(parent)
        self.setObjectName("reportsInterface")
        self._data_dir = data_dir
        self._config = config
        self._build_ui()
        self.retranslate()
        self._load_selected()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        self.title_label = SubtitleLabel("", self)
        root.addWidget(self.title_label)

        card = CardWidget(self)
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)
        self.kind_combo = ComboBox(card)
        for kind, key in (
            (reports.DAILY, "reports.daily"),
            (reports.WEEKLY, "reports.weekly"),
            (reports.CLEANUP, "reports.cleanup"),
        ):
            self.kind_combo.addItem(i18n.t(key), userData=kind)
        self.kind_combo.currentIndexChanged.connect(self._load_selected)
        top_row.addWidget(self.kind_combo)

        self.generate_btn = PrimaryPushButton(FIF.SYNC, "", card)
        self.generate_btn.clicked.connect(self._generate)
        top_row.addWidget(self.generate_btn)
        top_row.addStretch(1)
        box.addLayout(top_row)

        self.path_label = CaptionLabel("", card)
        self.path_label.setWordWrap(True)
        box.addWidget(self.path_label)

        self.viewer = TextEdit(card)
        self.viewer.setReadOnly(True)
        # 보고서는 열을 맞춰 쓰므로 고정폭 글꼴이 아니면 표가 어긋난다.
        self.viewer.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        box.addWidget(self.viewer)

        root.addWidget(card)

    def retranslate(self) -> None:
        self.title_label.setText(i18n.t("reports.title"))
        self.generate_btn.setText(i18n.t("reports.generate"))
        for index, key in enumerate(("reports.daily", "reports.weekly", "reports.cleanup")):
            self.kind_combo.setItemText(index, i18n.t(key))
        self._load_selected()

    def reload_config(self, config: config_module.AppConfig) -> None:
        self._config = config
        self.retranslate()

    def _selected_kind(self) -> str:
        return self.kind_combo.currentData()

    def _load_selected(self) -> None:
        kind = self._selected_kind()
        if kind is None:
            return
        path = reports.latest_path(self._data_dir, kind, i18n.get_language())
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
            InfoBar.success(
                title=i18n.t("reports.title"),
                content=i18n.t("reports.generated", path=path),
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000,
                parent=self,
            )
        self._load_selected()
