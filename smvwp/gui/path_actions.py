"""경로 표(큰 파일·용량 변화)에서 바로 손댈 수 있게 - 터미널, 주인, 목록 넘기기.

## 왜

표에서 큰 파일이나 크게 는 폴더를 찾아도 그다음이 막혔다.

- 그 자리로 가려면 경로를 손으로 옮겨 적어 터미널에서 `cd` 해야 했다.
- 공용 프로젝트 계정이라 **누구 것인지**를 알아야 그 사람에게 말할 수 있는데,
  표에는 주인이 없었다.
- 남에게 "이것 좀 보고 지워 달라" 고 하려면 목록을 넘겨야 하는데, 복사도 저장도
  안 됐다.

경로 목록이 있는 표에는 늘 **목록 보기** 단추가 있다 - 표 전체를 글로 펼친 창
(`ListViewer`)이 떠서 마우스로 골라 긁거나 모두 복사하고, 필요하면 거기서 파일로
저장한다. 바로 복사하던 단추는 무엇이 복사됐는지 보이지 않았다.

## 지키는 것

- **이 도구는 지우지 않는다.** 터미널을 그 폴더에서 열어 줄 뿐이고, 무엇을 할지는
  사람이 정한다.
- 주인은 화면을 그릴 때 `stat` 해서 본다 (스캔 DB 에는 큰 파일의 주인이 없다).
  NFS 가 답하지 않아도 창이 멈추지 않게 작업 스레드에서 하고, 결과가 오는 대로
  칸을 채운다. 한 번 본 경로는 기억한다.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from PyQt5 import sip
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFontDatabase
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import i18n
from ..scheduler import ThreadWorker, emit_safely

logger = logging.getLogger(__name__)

# 경로 칸에 넣어 두는 **전체** 경로. 화면 글은 계정 기준으로 줄인 것이라 그것으로는
# 그 자리로 갈 수 없다.
FULL_PATH_ROLE = Qt.UserRole + 40
# 큰 파일 표는 파일이다 - 터미널은 그 파일이 든 폴더에서 연다.
IS_FILE_ROLE = Qt.UserRole + 41

# 이 순서로 찾는다. 실기(RHEL, MATE)는 mate-terminal 이다.
TERMINALS = (
    ("mate-terminal", "--working-directory={folder}"),
    ("gnome-terminal", "--working-directory={folder}"),
    ("xfce4-terminal", "--working-directory={folder}"),
    ("konsole", "--workdir={folder}"),
)


def terminal_command(folder: str) -> Optional[List[str]]:
    """그 폴더에서 여는 터미널 명령. 깔린 것이 없으면 None."""

    for name, option in TERMINALS:
        program = shutil.which(name)
        if program:
            return [program, option.format(folder=folder)]
    return None


def folder_for(path: str, is_file: bool) -> str:
    return os.path.dirname(path) if is_file else path


def open_terminal(path: str, is_file: bool, parent=None) -> bool:
    """`path` 자리에서 터미널을 연다. 못 열면 경로를 복사해 두고 그렇다고 말한다.

    터미널은 이 창과 따로 산다 (새 세션) - 창을 닫아도 터미널은 남는다."""

    folder = folder_for(path, is_file)
    command = terminal_command(folder)
    if command is not None:
        try:
            subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            return True
        except OSError:
            logger.exception("터미널을 열지 못했습니다: %s", command[0])
    QApplication.clipboard().setText(folder)
    QMessageBox.information(
        parent, i18n.t("paths.terminal.title"), i18n.t("paths.terminal.none", path=folder)
    )
    return False


# -- 주인 ---------------------------------------------------------------------

def stat_owner(path: str) -> str:
    """그 경로의 주인 이름. 없어졌으면 '없음', 못 읽으면 '?'."""

    from ..detail_scan import owner_name

    try:
        uid = os.lstat(path).st_uid
    except FileNotFoundError:
        return i18n.t("paths.owner.gone")
    except OSError:
        return "?"
    return owner_name(uid)


class OwnerLookup(ThreadWorker):
    """경로들의 주인을 작업 스레드에서 본다. 하나 알 때마다 `found(경로, 이름)`.

    신호는 GUI 스레드로 건너가 칸을 채운다. 도는 중에 표가 다시 그려지면 마지막
    요청 하나를 기억했다가 한 번 더 돈다 (`COALESCE`)."""

    THREAD_NAME = "smvwp-owner"
    COALESCE = True
    found = pyqtSignal(str, str)
    _known: Dict[str, str] = {}

    def known(self, path: str) -> Optional[str]:
        return self._known.get(path)

    def request(self, paths: Iterable[str]) -> bool:
        wanted = [path for path in dict.fromkeys(paths) if path not in self._known]
        return bool(wanted) and self.start(wanted)

    def _work(self, paths: List[str]):
        for path in paths:
            if path not in self._known:
                try:
                    self._known[path] = stat_owner(path)
                except Exception:   # 주인 하나 못 봐도 나머지는 본다
                    self._known[path] = "?"
            emit_safely(self, "found", path, self._known[path])
        return len(paths)


class PathTable:
    """경로 표 하나에 터미널·주인·목록 넘기기를 붙인다.

    - 두 번 누르면 그 자리에서 터미널 (파일이면 그 파일이 든 폴더).
    - 오른쪽 버튼: 터미널 열기 / 경로 복사.
    - `owner_column` 칸은 주인이 오는 대로 채운다.
    - `bar()` 가 돌려주는 줄: 목록 복사·파일로 저장 단추와 짧은 안내."""

    def __init__(self, table, path_column: int, owner_column: int,
                 title: Callable[[], str], file_stem: Callable[[], str]):
        self.table = table
        self.path_column = path_column
        self.owner_column = owner_column
        self._title = title
        self._file_stem = file_stem
        self.owners = OwnerLookup(table)
        self.owners.found.connect(self._fill_owner)
        self.viewer: Optional["ListViewer"] = None
        table.cellDoubleClicked.connect(self._on_double_click)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(self._on_menu)
        table.setToolTip(i18n.t("paths.table_tip"))

    # -- 줄 정보 --
    def path_at(self, row: int) -> Optional[str]:
        item = self.table.item(row, self.path_column)
        if item is None:
            return None
        return item.data(FULL_PATH_ROLE) or item.text()

    def is_file_at(self, row: int) -> bool:
        item = self.table.item(row, self.path_column)
        return bool(item is not None and item.data(IS_FILE_ROLE))

    # -- 채우기 (표를 다 채운 뒤에 부른다) --
    def start_owners(self) -> None:
        """주인 칸을 아는 것은 바로, 모르는 것은 '…' 로 두고 작업 스레드에 맡긴다."""

        table = self.table
        sorting = table.isSortingEnabled()
        table.setSortingEnabled(False)
        missing = []
        for row in range(table.rowCount()):
            path = self.path_at(row)
            if not path:
                continue
            known = self.owners.known(path)
            item = QTableWidgetItem(known if known is not None else "…")
            item.setData(FULL_PATH_ROLE, path)
            table.setItem(row, self.owner_column, item)
            if known is None:
                missing.append(path)
        table.setSortingEnabled(sorting)
        self.owners.request(missing)

    def _fill_owner(self, path: str, name: str) -> None:
        """주인 하나가 왔다 - 그 경로의 칸에 쓴다.

        표(와 그 자식인 `OwnerLookup`)가 지워진 뒤에도 이미 줄 선 신호는 온다 - 받는 쪽이
        Qt 객체가 아니라 끊기지 않는다. 그때 지워진 표를 만지면 RuntimeError 가 슬롯 밖으로
        나가고, PyQt 는 그것을 qFatal 로 받아 **프로그램이 통째로 죽는다** (창을 닫거나 쪽을
        다시 그린 직후 주인을 찾던 중이면). 표가 없으면 할 일도 없다."""

        table = self.table
        if sip.isdeleted(table):
            return
        for row in range(table.rowCount()):
            item = table.item(row, self.owner_column)
            if item is not None and item.data(FULL_PATH_ROLE) == path:
                item.setText(name)

    # -- 동작 --
    def _on_double_click(self, row: int, _column: int) -> None:
        path = self.path_at(row)
        if path:
            open_terminal(path, self.is_file_at(row), self.table)

    def _on_menu(self, point) -> None:
        row = self.table.rowAt(point.y())
        path = self.path_at(row) if row >= 0 else None
        if not path:
            return
        menu = QMenu(self.table)
        terminal = menu.addAction(i18n.t("paths.menu.terminal"))
        copy = menu.addAction(i18n.t("paths.menu.copy"))
        chosen = menu.exec_(self.table.viewport().mapToGlobal(point))
        if chosen is terminal:
            open_terminal(path, self.is_file_at(row), self.table)
        elif chosen is copy:
            QApplication.clipboard().setText(path)

    # -- 목록 넘기기 --
    def report_text(self) -> str:
        """남에게 넘길 목록 - 제목 줄, 칸 머리, 그리고 줄마다 칸 글과 **전체 경로**.

        경로를 맨 끝에 둔다 - 길이가 제각각이라 앞에 두면 다른 칸이 줄을 못 맞춘다."""

        table = self.table
        columns = [c for c in range(table.columnCount())
                   if c != self.path_column and not table.isColumnHidden(c)]
        heads = [self._header(c) for c in columns] + [i18n.t("paths.report.path")]
        rows = []
        for row in range(table.rowCount()):
            cells = []
            for column in columns:
                item = table.item(row, column)
                cells.append(item.text() if item is not None else "")
            rows.append(cells + [self.path_at(row) or ""])
        widths = [max([text_width(heads[i])] + [text_width(r[i]) for r in rows])
                  for i in range(len(columns))]

        def line(cells):
            return "  ".join(
                [cell + " " * (widths[i] - text_width(cell)) for i, cell in enumerate(cells[:-1])]
                + [cells[-1]]
            ).rstrip()

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        out = [self._title(), i18n.t("paths.report.made", stamp=stamp), "", line(heads)]
        out += [line(cells) for cells in rows]
        return "\n".join(out) + "\n"

    def _header(self, column: int) -> str:
        item = self.table.horizontalHeaderItem(column)
        return item.text() if item is not None else ""

    def copy_report(self) -> None:
        QApplication.clipboard().setText(self.report_text())

    def show_list(self) -> "ListViewer":
        """표 전체를 글로 펼친 창을 띄운다 (창을 막지 않는다 - 표를 보며 같이 쓴다)."""

        if self.viewer is not None:
            self.viewer.close()
        self.viewer = ListViewer(self.report_text(), self._title(), self._file_stem(),
                                 self.table.window())
        self.viewer.show()
        self.viewer.raise_()
        return self.viewer

    def save_report(self) -> Optional[Path]:
        return save_text(self.table, self.report_text(), self._file_stem())

    def bar(self, caption: QLabel) -> QHBoxLayout:
        """설명 줄과 같은 줄에 안내와 단추 - 1080 화면에서 표 자리를 한 줄 더 먹지 않게."""

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(caption, 1)
        hint = QLabel(i18n.t("paths.hint"))
        hint.setObjectName("muted")
        hint.setToolTip(i18n.t("paths.table_tip"))
        row.addWidget(hint)
        self.view_btn = QPushButton(i18n.t("paths.btn.view"))
        self.view_btn.setToolTip(i18n.t("paths.btn.view_tip"))
        self.view_btn.clicked.connect(self.show_list)
        self.save_btn = QPushButton(i18n.t("paths.btn.save"))
        self.save_btn.setToolTip(i18n.t("paths.btn.save_tip"))
        self.save_btn.clicked.connect(self.save_report)
        row.addWidget(self.view_btn)
        row.addWidget(self.save_btn)
        return row


def save_text(parent, text: str, file_stem: str) -> Optional[Path]:
    """글을 .txt 로 저장한다 (홈 폴더에 이름을 제안). 고른 곳, 안 골랐거나 못 썼으면 None."""

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    suggested = Path.home() / f"{file_stem}_{stamp}.txt"
    chosen, _ = QFileDialog.getSaveFileName(
        parent, i18n.t("paths.save.title"), str(suggested), "Text (*.txt)"
    )
    if not chosen:
        return None
    target = Path(chosen)
    try:
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        QMessageBox.warning(parent, i18n.t("paths.save.title"),
                            i18n.t("paths.save.failed", error=exc))
        return None
    return target


class ListViewer(QDialog):
    """경로 목록을 글로 보는 창 - 골라 긁기, 모두 복사, 파일로 저장.

    고정폭 글꼴에 줄 바꿈 없이 (칸이 줄을 맞춘다 - `text_width`). 고칠 수는 없다."""

    def __init__(self, text: str, title: str, file_stem: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title or i18n.t("paths.viewer.title"))
        self._file_stem = file_stem
        box = QVBoxLayout(self)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        heading.setWordWrap(True)
        box.addWidget(heading)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        # 앱 스타일시트의 글꼴이 위젯 글꼴을 덮는다 - 고정폭이 아니면 칸 줄이 어긋난다.
        self.text.setStyleSheet(
            'QPlainTextEdit { font-family: "DejaVu Sans Mono", "Liberation Mono", '
            '"D2Coding", "NanumGothicCoding", "Consolas", "Courier New", monospace; }')
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.text.setPlainText(text)
        box.addWidget(self.text, 1)

        row = QHBoxLayout()
        self.status = QLabel(i18n.t("paths.viewer.hint", count=max(0, len(text.splitlines()) - 4)))
        self.status.setObjectName("muted")
        row.addWidget(self.status, 1)
        self.copy_btn = QPushButton(i18n.t("paths.viewer.copy_all"))
        self.copy_btn.clicked.connect(self.copy_all)
        self.save_btn = QPushButton(i18n.t("paths.btn.save"))
        self.save_btn.setToolTip(i18n.t("paths.btn.save_tip"))
        self.save_btn.clicked.connect(self.save)
        close = QPushButton(i18n.t("common.close"))
        close.clicked.connect(self.close)
        for button in (self.copy_btn, self.save_btn, close):
            row.addWidget(button)
        box.addLayout(row)
        # 1080 화면 안에 들게 - 넓게, 높이는 화면의 3/4 까지.
        screen = QApplication.primaryScreen()
        height = int(screen.availableGeometry().height() * 0.75) if screen else 700
        self.resize(1100, min(720, height))

    def copy_all(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())
        self.status.setText(i18n.t("paths.viewer.copied"))

    def save(self) -> Optional[Path]:
        target = save_text(self, self.text.toPlainText(), self._file_stem)
        if target is not None:
            self.status.setText(i18n.t("paths.viewer.saved", path=target))
        return target


def text_width(text: str) -> int:
    """고정폭 글꼴에서 차지하는 칸 수 - 한글은 두 칸이다 (붙여 넣은 목록의 줄 맞춤)."""

    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def path_item(text: str, full_path: str, is_file: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setData(FULL_PATH_ROLE, full_path)
    if is_file:
        item.setData(IS_FILE_ROLE, True)
    return item


def fit_text_column(table, column: int, extra: int = 0) -> None:
    """칸 글이 잘리지 않게 칸을 넓힌다 (좁히지는 않는다). `extra` 는 막대 등 글 밖의 자리.

    실기의 글꼴이 개발 PC 보다 넓어 '1.2 T…' 처럼 단위가 잘렸다."""

    metrics = table.fontMetrics()
    widest = 0
    for row in range(table.rowCount()):
        item = table.item(row, column)
        if item is not None and item.text():
            widest = max(widest, metrics.horizontalAdvance(item.text()))
    header = table.horizontalHeaderItem(column)
    if header is not None:
        widest = max(widest, metrics.horizontalAdvance(header.text()) + 24 - extra)
    needed = widest + extra + 24
    if table.columnWidth(column) < needed:
        table.setColumnWidth(column, needed)
