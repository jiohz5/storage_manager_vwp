"""최초 실행 안내 (DESIGN.md 2부 6절의 '이후 후보' 중 마법사 항목).

기존 구현에서는 README를 따라가며 데이터 경로 지정 -> 진단 -> 계정 등록을
사용자가 각각 수동으로 해야 했다. 여기서는 계정이 하나도 없을 때 딱 한 번,
"지금 무엇을 해야 하는지"를 한 화면에 모아 보여준다.

일부러 단계별 여러 페이지로 만들지 않았다. 실제로 해야 할 일이 두 가지
(진단 확인, 계정 등록)뿐이라 페이지를 나누면 클릭만 늘어난다 - 클릭 수를
줄이자는 같은 절의 다른 항목과도 어긋난다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .. import config as config_module
from .. import diagnostics, i18n


class FirstRunDialog(QDialog):
    """계정이 없을 때 뜨는 시작 안내. '계정 등록'을 누르면 계정 다이얼로그로
    바로 넘어간다."""

    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._config = config
        self.open_accounts = False
        self.setWindowTitle(i18n.t("firstrun.title"))
        self.resize(560, 360)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        heading = QLabel(i18n.t("firstrun.heading"))
        heading.setStyleSheet("font-size: 13pt; font-weight: bold;")
        heading.setWordWrap(True)
        root.addWidget(heading)

        body = QLabel(i18n.t("firstrun.body", path=self._data_dir))
        body.setWordWrap(True)
        root.addWidget(body)

        # 진단 결과를 미리 한 번 보여준다 - 여기서 막히면 계정을 등록해도
        # 수집이 안 되므로, 먼저 확인하는 편이 낫다.
        result = diagnostics.run_diagnostics(self._data_dir)
        status = QLabel(diagnostics.format_report(result))
        status.setWordWrap(True)
        status.setStyleSheet(
            "font-family: monospace; font-size: 9pt; padding: 6px; "
            f"background: {'#e8f5e9' if result['ok'] else '#fff3e0'};"
        )
        root.addWidget(status)
        root.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        later_btn = QPushButton(i18n.t("firstrun.later"))
        later_btn.clicked.connect(self.reject)
        add_btn = QPushButton(i18n.t("firstrun.add_account"))
        add_btn.setDefault(True)
        add_btn.clicked.connect(self._accept_and_open_accounts)
        buttons.addWidget(later_btn)
        buttons.addWidget(add_btn)
        root.addLayout(buttons)

    def _accept_and_open_accounts(self) -> None:
        self.open_accounts = True
        self.accept()
