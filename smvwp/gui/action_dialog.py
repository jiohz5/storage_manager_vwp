"""'무엇부터 할까' 한 줄을 눌렀을 때 뜨는 창.

## 무엇을 보여 주나

목록의 한 줄은 "무엇이" 만 말한다. 여기서는 그 다음을 말한다:

- 왼쪽: **지금 상황 · 왜 먼저인가 · 무엇을 하면 되나** (읽는 순서대로)
- 오른쪽: **근거** (어느 과제·파일·경로인가)와 **확인 명령**

내용은 `action_guide` 가 만들고 여기서는 그리기만 한다 - 문구와 판단을 Qt
없이 시험할 수 있어야 한다.

## 근거는 늦게 와도 된다

근거는 NFS 위의 DB 를 몇 번 읽어야 온다 (`account_detail.read`). 줄에 담긴
숫자로 먼저 상황·이유·할 일을 그리고, 근거가 오면 다시 그린다. 사람은 그동안
이미 읽기 시작한다.

## 이 창은 지우지 않는다

버튼은 전부 **보거나 복사하는 것**이다. 확인 명령도 읽기만 하는 것만 낸다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import action_guide, config as config_module, i18n, priority, tiers
from .account_detail_dialog import DetailReader

# 급함 단계의 색. 목록 줄과 같은 색을 쓴다 - 창을 열어도 "아까 그 빨간 줄"
# 이라는 것이 이어져야 한다.
LEVEL_TIERS = {
    priority.LEVEL_CRITICAL: tiers.EMERGENCY,
    priority.LEVEL_HIGH: tiers.ALERT,
    priority.LEVEL_MEDIUM: tiers.WARN,
}

# 왼쪽 글 칸의 폭. 한 줄이 이보다 길면 읽는 눈이 줄 끝에서 길을 잃는다.
TEXT_COLUMN_WIDTH = 400


class ActionDialog(QDialog):
    """할 일 하나를 상황·이유·할 일·근거로 풀어 보여 준다."""

    # 창의 탭을 옮기는 것은 창의 일이다. 값만 내보낸다.
    open_scan_requested = pyqtSignal(str)
    # 상태 줄에 쓸 한 줄 (복사했다는 알림 등).
    status_message = pyqtSignal(str)

    def __init__(self, data_dir: Path, get_config, action=None, guide=None, parent=None):
        """`action`(목록의 할 일) 또는 `guide`(곁말 안내) 중 하나를 준다."""

        super().__init__(parent)
        self._data_dir = data_dir
        self._get_config = get_config
        self._action = action
        self.resize(1040, 660)

        self._reader = DetailReader(data_dir, get_config, self)
        self._reader.finished.connect(self._on_detail)
        self._reader.failed.connect(lambda _message: self._on_detail(None, failed=True))

        self._build()
        self.guide = guide if guide is not None else action_guide.build(action)
        self._render(self.guide)
        if action is not None and action.account_id:
            self._reader.run_async(action.account_id)

    # -- 만들기 ----------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.level_chip = QLabel()
        self.level_chip.setObjectName("levelChip")
        self.level_chip.setAlignment(Qt.AlignCenter)
        head.addWidget(self.level_chip, 0, Qt.AlignTop)
        self.headline = QLabel()
        self.headline.setObjectName("sectionTitle")
        self.headline.setWordWrap(True)
        head.addWidget(self.headline, 1)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(18)
        root.addLayout(body, 1)

        # -- 왼쪽: 읽는 순서대로 ------------------------------------------
        text = QWidget()
        text.setObjectName("subPage")
        text_box = QVBoxLayout(text)
        text_box.setContentsMargins(0, 0, 6, 0)
        text_box.setSpacing(6)
        self.situation = self._section(text_box, "guide.section.situation")
        self.why = self._section(text_box, "guide.section.why")
        self.steps = self._section(text_box, "guide.section.steps")
        self.warnings = QLabel()
        self.warnings.setObjectName("captionWarn")
        self.warnings.setWordWrap(True)
        text_box.addWidget(self.warnings)
        note = QLabel(i18n.t("guide.never_deletes"))
        note.setObjectName("caption")
        note.setWordWrap(True)
        text_box.addWidget(note)
        text_box.addStretch(1)
        # 글이 길어도 창이 늘지 않게 스크롤로 감싼다 (화면 세로가 1080 이다).
        scroll = QScrollArea()
        scroll.setObjectName("subPages")
        scroll.setWidget(text)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(TEXT_COLUMN_WIDTH)
        self.text_scroll = scroll
        body.addWidget(scroll)

        # -- 오른쪽: 근거와 확인 명령 ---------------------------------------
        self.right_panel = QWidget()
        self.right_panel.setObjectName("subPage")
        right = QVBoxLayout(self.right_panel)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        self.evidence_title = QLabel()
        self.evidence_title.setObjectName("sectionTitle")
        self.evidence_title.setWordWrap(True)
        right.addWidget(self.evidence_title)
        self.evidence_note = QLabel()
        self.evidence_note.setObjectName("muted")
        self.evidence_note.setWordWrap(True)
        right.addWidget(self.evidence_note)

        self.evidence = QTableWidget(0, 0)
        self.evidence.setEditTriggers(QTableWidget.NoEditTriggers)
        self.evidence.setSelectionBehavior(QTableWidget.SelectRows)
        self.evidence.setSelectionMode(QTableWidget.SingleSelection)
        self.evidence.verticalHeader().setVisible(False)
        self.evidence.verticalHeader().setDefaultSectionSize(30)
        self.evidence.setShowGrid(False)
        self.evidence.setWordWrap(False)
        # 경로는 가운데를 줄인다 - 오른쪽을 자르면 정작 다른 뒤쪽이 사라진다.
        self.evidence.setTextElideMode(Qt.ElideMiddle)
        self.evidence.horizontalHeader().setHighlightSections(False)
        self.evidence.itemSelectionChanged.connect(self._sync_copy_button)
        right.addWidget(self.evidence, 1)

        commands_head = QHBoxLayout()
        self.commands_title = QLabel(i18n.t("guide.section.commands"))
        self.commands_title.setObjectName("caption")
        commands_head.addWidget(self.commands_title, 1)
        self.copy_commands_btn = QPushButton(i18n.t("guide.btn.copy_commands"))
        self.copy_commands_btn.clicked.connect(self._copy_commands)
        commands_head.addWidget(self.copy_commands_btn)
        right.addLayout(commands_head)
        self.commands = QPlainTextEdit()
        self.commands.setReadOnly(True)
        # 줄은 칸 폭에서 접는다. 경로 두 개가 든 `du` 한 줄은 칸보다 길어서,
        # 안 접으면 가로 스크롤이 생기고 뒤쪽 경로가 안 보인다. 복사는 접기와
        # 상관없이 원래 줄 그대로 된다.
        self.commands.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.TypeWriter)
        self.commands.setFont(mono)
        self.commands.setFixedHeight(112)
        right.addWidget(self.commands)
        body.addWidget(self.right_panel, 1)

        # -- 동작 ----------------------------------------------------------
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.account_btn = QPushButton(i18n.t("guide.btn.account"))
        self.account_btn.clicked.connect(self._open_account)
        self.tree_btn = QPushButton(i18n.t("tree.btn.open"))
        self.tree_btn.clicked.connect(self._open_tree)
        self.scan_btn = QPushButton(i18n.t("guide.btn.scan"))
        self.scan_btn.clicked.connect(self._go_to_scan)
        self.copy_path_btn = QPushButton(i18n.t("guide.btn.copy_path"))
        self.copy_path_btn.clicked.connect(self._copy_path)
        for button in (self.account_btn, self.tree_btn, self.scan_btn, self.copy_path_btn):
            buttons.addWidget(button)
        buttons.addStretch(1)
        close_btn = QPushButton(i18n.t("common.close"))
        close_btn.setObjectName("primary")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

    @staticmethod
    def _section(box: QVBoxLayout, title_key: str) -> QLabel:
        title = QLabel(i18n.t(title_key))
        title.setObjectName("sectionTitle")
        box.addSpacing(4)
        box.addWidget(title)
        body = QLabel()
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(body)
        return body

    # -- 그리기 ----------------------------------------------------------
    def _render(self, guide) -> None:
        account = config_module.find_account(self._get_config(), guide.account_id)
        self.setWindowTitle(
            i18n.t("guide.title", account=account.name)
            if account else i18n.t("guide.title_general")
        )

        tier = LEVEL_TIERS.get(guide.level, tiers.WARN)
        self.level_chip.setText(guide.level_text)
        self.level_chip.setStyleSheet(
            f"background: {tiers.color(tier)}; color: #ffffff; border-radius: 9px;"
            f" padding: 3px 10px; font-weight: bold;"
        )
        self.headline.setText(guide.headline)

        self.situation.setText("\n".join(guide.situation))
        self.why.setText(guide.why)
        self.steps.setText(
            "\n".join(f"{index}. {step}" for index, step in enumerate(guide.steps, 1))
        )
        self.warnings.setText("\n".join(guide.warnings))
        self.warnings.setVisible(bool(guide.warnings))

        self._render_evidence(guide)
        # 근거도 명령도 없는 안내(스캔 안 된 계정 등)는 오른쪽을 통째로 접고
        # 글 칸을 넓힌다. 넓은 창 가운데 글만 떠 있으면 뭔가 빠진 화면으로 읽힌다.
        text_only = (
            not guide.evidence_columns and not guide.commands and not guide.loading
        )
        self.right_panel.setVisible(not text_only)
        if text_only:
            self.text_scroll.setMinimumWidth(0)
            self.text_scroll.setMaximumWidth(16777215)
            self.resize(620, 480)
        else:
            self.text_scroll.setFixedWidth(TEXT_COLUMN_WIDTH)
        self.commands.setPlainText("\n".join(guide.commands))
        has_commands = bool(guide.commands)
        for widget in (self.commands_title, self.commands, self.copy_commands_btn):
            widget.setVisible(has_commands)

        has_account = bool(guide.account_id)
        self.account_btn.setVisible(has_account)
        self.tree_btn.setVisible(has_account)
        # 스캔이 안 돈 계정 안내는 상세 스캔 탭이 곧 할 일이다.
        self.scan_btn.setVisible(
            has_account or guide.kind == action_guide.KIND_UNSCANNED
        )
        # 경로가 나올 수 없는 안내에는 복사 버튼을 두지 않는다.
        self.copy_path_btn.setVisible(bool(guide.evidence_columns or guide.path))
        self._sync_copy_button()

    def _render_evidence(self, guide) -> None:
        table = self.evidence
        table.clear()
        table.setRowCount(0)
        visible = bool(guide.evidence_columns)
        self.evidence_title.setText(guide.evidence_title)
        for widget in (self.evidence_title, table):
            widget.setVisible(visible)

        if guide.loading:
            self.evidence_note.setText(i18n.t("guide.loading"))
        elif visible and not guide.evidence_rows:
            self.evidence_note.setText(i18n.t("guide.no_evidence"))
        else:
            self.evidence_note.setText("")
        self.evidence_note.setVisible(bool(self.evidence_note.text()))
        if not visible:
            return

        table.setColumnCount(len(guide.evidence_columns))
        table.setHorizontalHeaderLabels(guide.evidence_columns)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        # 남는 폭은 **경로나 긴 목록이 든 열**이 가져간다. 첫 열을 늘이게 두었더니
        # 백업 위치 같은 긴 경로 열이 내용대로 폭을 먼저 가져가, 정작 과제
        # 이름이 `chip_…_0810` 으로 줄었다.
        last = len(guide.evidence_columns) - 1
        wide_last = last > 0 and guide.evidence_columns[last] in (
            i18n.t("guide.col.backup_at"), i18n.t("guide.col.missing")
        )
        header.setSectionResizeMode(last if wide_last else 0, QHeaderView.Stretch)
        table.setRowCount(len(guide.evidence_rows))
        warn = QColor(tiers.color(tiers.WARN))
        for row, evidence in enumerate(guide.evidence_rows):
            for column, text in enumerate(evidence.cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(Qt.UserRole, evidence.path)
                    if evidence.path:
                        item.setToolTip(evidence.path)
                if column > 0:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if evidence.warn:
                    item.setForeground(warn)
                table.setItem(row, column, item)
        # 마지막 칸이 경로·목록인 표는 왼쪽으로 붙이고 전체를 툴팁에 둔다.
        if wide_last:
            for row in range(table.rowCount()):
                cell = table.item(row, last)
                if cell is not None:
                    cell.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    cell.setToolTip(cell.text())

    def _on_detail(self, detail, failed: bool = False) -> None:
        if self._action is None:
            return
        # detail 이 None 이면 계정이 그사이 지워졌거나 읽기가 실패한 것이다 -
        # 줄에 담긴 숫자만으로 둔다.
        self.guide = action_guide.build(self._action, detail)
        if detail is None:
            self.guide.loading = False
            if failed:
                self.guide.warnings.append(i18n.t("detail.failed", error=""))
        self._render(self.guide)

    # -- 동작 ------------------------------------------------------------
    def _selected_path(self) -> str:
        row = self.evidence.currentRow()
        if row >= 0 and self.evidence.item(row, 0) is not None:
            path = self.evidence.item(row, 0).data(Qt.UserRole)
            if path:
                return path
        return self.guide.path

    def _sync_copy_button(self) -> None:
        self.copy_path_btn.setEnabled(bool(self._selected_path()))

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.status_message.emit(i18n.t("guide.copied", text=text.splitlines()[0]))

    def _copy_path(self) -> None:
        path = self._selected_path()
        if path:
            self._copy(path)

    def _copy_commands(self) -> None:
        if self.guide.commands:
            self._copy("\n".join(self.guide.commands))

    def _open_account(self) -> None:
        from .account_detail_dialog import AccountDetailDialog

        dialog = AccountDetailDialog(
            self._data_dir, self._get_config, self.guide.account_id, parent=self
        )
        dialog.open_scan_requested.connect(self._forward_scan)
        dialog.exec_()

    def _open_tree(self) -> None:
        from .tree_dialog import TreeDialog

        TreeDialog(
            self._data_dir, self._get_config(),
            account_id=self.guide.account_id, parent=self,
        ).exec_()

    def _forward_scan(self, account_id: str) -> None:
        self.open_scan_requested.emit(account_id)
        self.accept()

    def _go_to_scan(self) -> None:
        self._forward_scan(self.guide.account_id)
