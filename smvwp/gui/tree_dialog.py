"""계정 안을 펼쳐 보는 트리 화면.

## 왜 대화상자인가

상세 스캔 탭은 이미 표가 셋이다. 트리는 한 번에 한 계정을 **깊이 파고드는**
화면이라 성격이 다르고, 자리도 넓게 써야 읽힌다. 늘 보는 것이 아니라 "저
계정이 왜 300GB 지"가 궁금할 때 여는 것이므로 따로 뺀다.

## 다시 스캔하지 않는다

야간 스캔이 이미 디렉터리마다 크기를 남겼다. 여기서는 그 기록만 읽는다 -
화면을 연다고 파일서버에 부하가 가지 않는다. 사람이 트리를 펼쳐 볼 때마다
`du` 가 도는 화면이었다면 아무도 못 쓰게 했을 것이다.

## 읽기는 작업 스레드에서

데이터 디렉터리가 NFS 위라 SQLite 읽기 한 번이 왕복이다. 계정이 크면 행이
수만 개일 수 있어, GUI 스레드에서 읽으면 창이 그동안 굳는다.

## 펼칠 때 만든다

노드를 미리 다 만들면 디렉터리 수만큼 위젯이 생긴다. 수만 개짜리 계정에서
그것만으로 몇 초가 걸리고 메모리도 그만큼 쓴다. 자식은 **처음 펼칠 때** 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QComboBox,
    QTabWidget,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_module
from .. import formatting, i18n, scan_store, tiers, tree_view, usage_log
from ..scheduler import ThreadWorker
from . import theme, visuals, widgets
from .treemap_view import TreemapView

COLUMN_KEYS = ("tree.col.path", "tree.col.size", "tree.col.share", "tree.col.change",
               "tree.col.files", "tree.col.cold")
(COL_PATH, COL_SIZE, COL_SHARE, COL_CHANGE, COL_FILES, COL_COLD) = range(6)

# 처음 열 때 저절로 펼쳐 둘 깊이.
#
# 뿌리만 보여 주면 "그래서 뭘 하라는 거지"가 되고, 다 펼치면 화면이 넘친다.
# 한 단계는 곧바로 "이 계정 안에 뭐가 있나"에 답한다.
AUTO_EXPAND_DEPTH = 1

# 트리 노드에 원본을 매달아 둘 자리 (Qt 사용자 데이터 슬롯).
NODE_ROLE = Qt.UserRole
LOADED_ROLE = Qt.UserRole + 1


@dataclass
class TreeResult:
    """불러온 것 - 트리와, 그것이 **언제 잰 것이고 무엇과 견줬는지**.

    트리는 지금 재는 것이 아니라 끝난 스캔의 기록이다. 그 스캔이 며칠 전일 수
    있으므로 날짜를 함께 말해야 숫자를 제대로 읽는다."""

    roots: List[object]
    total_kb: Optional[int]
    current_at: Optional[str] = None
    previous_at: Optional[str] = None
    generation: Optional[int] = None


class _TreeLoader(ThreadWorker):
    """DB 읽기를 작업 스레드로 뺀다.

    결과는 `TreeResult`. 아직 끝난 스캔이 없으면 **None** - 빈 트리와 "아직 안
    쟀다" 는 다른 이야기라 화면이 갈라 말한다."""

    THREAD_NAME = "smvwp-tree"

    def __init__(self, data_dir: Path, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        # 계정 id -> 계정 경로. 뿌리를 계정 안의 이름으로 부르고 나눠 잰 폴더를
        # 다시 세우는 데 쓴다 (`tree_view.build_tree` 의 `root_path`).
        self.account_paths = {}

    def run_async(self, account_id: str) -> bool:
        return self.start(account_id)

    def _work(self, account_id: str):
        with scan_store.session(self._data_dir) as conn:
            state = scan_store.get_account_state(conn, account_id)
            generation = state.last_completed_generation
            if not generation:
                return None
            previous = generation - 1 if generation > 1 else None

            rows = scan_store.tree_entries(conn, account_id, generation)
            entries = [(row["path"], row["size_kb"]) for row in rows]
            if not entries:
                return None
            # 이전 세대가 없으면 **None** 을 넘긴다. 빈 목록으로 넘기면 모든
            # 경로가 "지난 스캔에 없던 것"으로 표시된다 - 비교할 스캔이 아예
            # 없는 것과 그때 없었던 것은 다른 이야기다.
            previous_entries = None
            if previous:
                previous_entries = [
                    (row["path"], row["size_kb"])
                    for row in scan_store.tree_entries(conn, account_id, previous)
                ]
            large = scan_store.large_file_changes(
                conn, account_id, generation, previous
            )

        # 계정 합계는 뿌리(계정 바로 아래 폴더들)의 합이다. 나눠 잰 폴더를 다시
        # 세우므로 그 밤에 잰 것을 겹치지도 빠뜨리지도 않고 한 번씩 센다 - 비중을 다
        # 더하면 100% 가 된다.
        roots = tree_view.build_tree(
            entries,
            previous=previous_entries,
            large_files=large,
            root_path=self.account_paths.get(account_id),
        )
        # 순회가 같이 남긴 파일 수·오래된 양을 폴더에 붙인다 (없으면 그 칸은 빈다).
        tree_view.attach_details(roots, rows)
        return TreeResult(
            roots=roots,
            total_kb=sum(node.size_kb for node in roots) if roots else None,
            current_at=state.last_completed_at,
            previous_at=state.previous_completed_at if previous else None,
            generation=generation,
        )


class TreeDialog(QDialog):
    """계정 하나를 펼쳐 보는 창."""

    def __init__(self, data_dir: Path, config: config_module.AppConfig,
                 account_id: str = "", parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._config = config
        self.setWindowTitle(i18n.t("tree.title"))
        self.resize(940, 620)

        self._loader = _TreeLoader(data_dir, self)
        self._loader.account_paths = {
            account.account_id: account.path for account in config.accounts
        }
        self._loader.finished.connect(self._on_result)
        self._loader.failed.connect(self._on_failed)

        self._build_ui()
        if account_id:
            index = self.account_combo.findData(account_id)
            if index >= 0:
                self.account_combo.setCurrentIndex(index)
        usage_log.record_soon(data_dir, usage_log.TREE_VIEWED)
        self._reload()

    # -- 화면 ---------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QLabel(i18n.t("scan.account_label")))
        self.account_combo = QComboBox()
        self.account_combo.setMinimumWidth(220)
        for account in self._config.accounts:
            self.account_combo.addItem(account.name, account.account_id)
        self.account_combo.currentIndexChanged.connect(self._reload)
        top.addWidget(self.account_combo)
        top.addStretch(1)
        root.addLayout(top)

        # 계정 합계와 맨 위 폴더들의 몫을 한 줄로 (`visuals.SummaryBand`). 예전에는 그
        # 자리가 "261109 스캔 기준 (261108 스캔과 견줌) · 디렉터리 28개 · 계정 합계 32.8 TB
        # · 야간 스캔이 남긴 기록을 읽은 것이라 ..." 한 문단이었다.
        self.stack = visuals.ShareStack()
        self.band = visuals.SummaryBand(self.stack)
        self.band.setVisible(False)
        root.addWidget(self.band)
        self.caption = QLabel()
        self.caption.setObjectName("muted")
        self.caption.setWordWrap(True)
        root.addWidget(self.caption)

        # 같은 데이터를 목록으로 볼지 그림으로 볼지 고른다. 목록은 정확한
        # 숫자를 읽는 데 좋고, 그림은 "무엇이 큰가"를 한눈에 보는 데 좋다 -
        # 어느 하나가 다른 하나를 대신하지 못한다.
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        list_page = QWidget()
        list_box = QVBoxLayout(list_page)
        list_box.setContentsMargins(0, 8, 0, 0)
        list_box.setSpacing(8)
        list_tools = QHBoxLayout()
        self.expand_btn = QPushButton(i18n.t("tree.btn.expand"))
        self.expand_btn.clicked.connect(self._expand_more)
        list_tools.addWidget(self.expand_btn)
        list_tools.addStretch(1)
        list_box.addLayout(list_tools)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMN_KEYS))
        self.tree.setHeaderLabels([
            i18n.t(key, days=self._config.settings.detail_scan_cold_days) for key in COLUMN_KEYS
        ])
        self.tree.setUniformRowHeights(True)   # 행 높이를 재지 않아 훨씬 가볍다
        self.tree.setAlternatingRowColors(False)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.Stretch)
        header.setSectionResizeMode(COL_SIZE, header.ResizeToContents)
        # 비중·증감 칸에는 막대가 들어간다 (`widgets.CellDelegate`) - 내용 폭에 맞추면
        # 막대가 글자 밑에 숨는다.
        header.setSectionResizeMode(COL_FILES, header.ResizeToContents)
        for column, width in ((COL_SHARE, 150), (COL_CHANGE, 170), (COL_COLD, 170)):
            header.setSectionResizeMode(column, header.Interactive)
            self.tree.setColumnWidth(column, width)
        self.cells = widgets.ChangeBarDelegate(self.tree)
        for column in (COL_SHARE, COL_CHANGE, COL_COLD):
            self.tree.setItemDelegateForColumn(column, self.cells)
        self.tree.itemExpanded.connect(self._on_expanded)
        list_box.addWidget(self.tree, 1)
        self.tabs.addTab(list_page, i18n.t("tree.tab.list"))

        # -- 그림 ------------------------------------------------------
        map_page = QWidget()
        map_box = QVBoxLayout(map_page)
        map_box.setContentsMargins(0, 8, 0, 0)
        map_box.setSpacing(8)
        map_tools = QHBoxLayout()
        self.map_up_btn = QPushButton(i18n.t("treemap.btn.up"))
        self.map_up_btn.clicked.connect(self._map_up)
        self.map_home_btn = QPushButton(i18n.t("treemap.btn.home"))
        self.map_home_btn.clicked.connect(self._map_home)
        map_tools.addWidget(self.map_up_btn)
        map_tools.addWidget(self.map_home_btn)
        self.map_path_label = QLabel()
        self.map_path_label.setObjectName("muted")
        map_tools.addWidget(self.map_path_label, 1)
        map_box.addLayout(map_tools)

        self.treemap = TreemapView()
        self.treemap.zoomed.connect(self._on_map_zoomed)
        map_box.addWidget(self.treemap, 1)
        self.map_hint = QLabel(i18n.t("treemap.hint"))
        self.map_hint.setObjectName("caption")
        self.map_hint.setWordWrap(True)
        map_box.addWidget(self.map_hint)
        self.tabs.addTab(map_page, i18n.t("tree.tab.map"))

        # 목록의 회색 줄·나머지 줄을 설명하는 말이라 목록 쪽에 둔다. 창 아래에 두면
        # 그림 탭에서도 보여, 그림의 안내와 두 덩어리가 겹쳐 읽혔다.
        # 두 줄 문단이던 것을 한 줄로 줄이고 나머지는 툴팁에 둔다.
        self.legend = QLabel(i18n.t("tree.legend_short"))
        self.legend.setObjectName("caption")
        self.legend.setToolTip(i18n.t("tree.legend"))
        list_box.addWidget(self.legend)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_btn = QPushButton(i18n.t("common.close"))
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    # -- 불러오기 -----------------------------------------------------
    def _reload(self) -> None:
        account_id = self.account_combo.currentData()
        self.tree.clear()
        self.band.setVisible(False)
        if not account_id:
            self.caption.setText(i18n.t("tree.no_account"))
            return
        self.caption.setText(i18n.t("tree.loading"))
        if not self._loader.run_async(account_id):
            self.caption.setText(i18n.t("tree.busy"))

    def _on_failed(self, message: str) -> None:
        self.caption.setText(i18n.t("tree.failed", error=message))

    def _on_result(self, result) -> None:
        if result is None:
            self._on_empty()
        else:
            self._on_loaded(result)

    def _on_empty(self) -> None:
        """완료된 스캔이 없다.

        빈 트리를 그냥 두면 "이 계정은 비어 있다"로 읽힌다 - 사실은 아직 재지
        않은 것이다."""

        self.tree.clear()
        self.treemap.set_roots([])
        self.band.setVisible(False)
        self.caption.setText(i18n.t("tree.no_scan"))

    def _on_loaded(self, result: TreeResult) -> None:
        roots = result.roots
        self.tree.clear()
        self.treemap.set_roots(roots)
        self._on_map_zoomed()
        if not roots:
            self._on_empty()
            return
        # '지난 스캔 대비' 대신 견준 스캔의 날짜를 열 제목에 - 증가 경로 표와 같다.
        previous = (
            formatting.scan_label(result.previous_at, (result.generation or 1) - 1)
            if result.previous_at else None
        )
        self.tree.headerItem().setText(
            COL_CHANGE,
            i18n.t("scan.col.delta_dated", previous=previous) if previous
            else i18n.t("tree.col.change"),
        )
        # 증감 막대는 맨 위 폴더들 가운데 가장 큰 변화가 칸을 다 채운다 - 안쪽 폴더는
        # 그보다 짧다 (같은 자로 재야 어디가 컸는지 견줄 수 있다).
        self.cells.scale = max(
            (abs(node.delta_kb or 0) for node in roots if node.compared), default=0
        )
        for node in roots:
            item = self._make_item(node)
            self.tree.addTopLevelItem(item)
        self._fill_children(self.tree.invisibleRootItem(), depth=AUTO_EXPAND_DEPTH)
        for index in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(index).setExpanded(True)
        when = formatting.scan_label(result.current_at, result.generation)
        self._show_band(result, when, previous)
        self.caption.setText(i18n.t("tree.caption_short"))

    def _show_band(self, result: TreeResult, when: str, previous: Optional[str]) -> None:
        """계정 합계, 맨 위 폴더들의 몫(쌓은 막대), 두 스캔의 날짜와 디렉터리 수 알약."""

        band = self.band
        band.setVisible(True)
        band.caption.setText(i18n.t("tree.band.title"))
        band.set_value(formatting.format_kb(result.total_kb))
        band.sub.setText(
            i18n.t("tree.chip.when", when=when)
            + ("  ·  " + i18n.t("tree.chip.compared", previous=previous) if previous else "")
        )
        total = result.total_kb or 0
        ordered = sorted(result.roots, key=lambda node: -(node.size_kb or 0))
        self.stack.set_parts([
            (
                node.size_kb or 0, "attention" if node.is_notable else "accent",
                i18n.t(
                    "tree.root_tip", name=self._label_for(node),
                    size=formatting.format_kb(node.size_kb),
                    pct=f"{(node.size_kb or 0) / total * 100:.1f}" if total else "-",
                ),
            )
            for node in ordered
        ], total)
        band.set_chips([
            (i18n.t("tree.chip.dirs", count=f"{tree_view.count_directories(result.roots):,}"),
             "soft", ""),
        ])

    # -- 노드 ---------------------------------------------------------
    def _make_item(self, node) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setText(COL_PATH, self._label_for(node))
        notes = tree_view.notes(node)
        item.setToolTip(COL_PATH, "\n".join([node.path] + [i18n.t(key) for key in notes]))
        item.setText(COL_SIZE, formatting.format_kb(node.size_kb))
        item.setTextAlignment(COL_SIZE, Qt.AlignRight | Qt.AlignVCenter)
        if node.pieced:
            item.setToolTip(COL_SIZE, i18n.t("tree.note.pieced"))

        share = node.share_pct
        item.setText(COL_SHARE, f"{share:.1f}%" if share is not None else "-")
        item.setTextAlignment(COL_SHARE, Qt.AlignRight | Qt.AlignVCenter)
        if share is not None and node.kind not in (tree_view.REST, tree_view.MORE):
            # 계정에서의 몫 - 0~100% 가 칸 하나다 (안쪽 폴더는 그만큼 짧다). 계산으로
            # 만든 줄(직접 있는 파일, 그 밖 폴더)에는 막대를 두지 않는다 - 흐린 줄이다.
            item.setData(COL_SHARE, widgets.CellDelegate.BAR_ROLE, (
                share / 100, "attention" if node.is_notable else "accent",
            ))

        item.setText(COL_CHANGE, formatting.format_tree_change(node))
        item.setTextAlignment(COL_CHANGE, Qt.AlignRight | Qt.AlignVCenter)
        if node.kind not in (tree_view.REST, tree_view.MORE) and node.compared:
            if node.previous_kb is None:
                # '지난 스캔에 없던 것' / '목록에 없던 파일' 은 알약으로.
                item.setData(COL_CHANGE, widgets.CellDelegate.CHIP_ROLE,
                             (item.text(COL_CHANGE), "change_up"))
            elif node.delta_kb:
                item.setData(COL_CHANGE, widgets.CellDelegate.VALUE_ROLE, node.delta_kb)
        if node.compared and node.previous_kb is not None and node.delta_kb == 0:
            # '변화 없음' 이 줄마다 같은 색이면 변한 줄이 묻힌다.
            item.setForeground(COL_CHANGE, QColor(theme.TEXT_MUTED))
        if node.previous_pieced:
            item.setToolTip(COL_CHANGE, i18n.t("tree.note.previous_pieced"))

        self._fill_scan_extras(item, node)

        if node.is_notable:
            for column in range(len(COLUMN_KEYS)):
                item.setForeground(column, QColor(theme.tier_text(tiers.WARN)))
        elif node.kind in (tree_view.REST, tree_view.MORE):
            # 진짜 디렉터리가 아니라 계산으로 만든 줄이다. 같은 색이면 사람이
            # 눌러서 들어가려 한다.
            for column in range(len(COLUMN_KEYS)):
                item.setForeground(column, QColor(theme.TEXT_MUTED))

        item.setData(COL_PATH, NODE_ROLE, node)
        item.setData(COL_PATH, LOADED_ROLE, False)
        if node.children:
            # 자식을 아직 안 만들었어도 펼침 화살표는 있어야 한다. 빈 자리를
            # 하나 넣어 두고 펼칠 때 진짜로 채운다.
            item.addChild(QTreeWidgetItem([""]))
        return item

    def _fill_scan_extras(self, item: QTreeWidgetItem, node) -> None:
        """파일 수와 오래 안 바뀐 양 - 순회가 크기와 같이 남긴 것 (없으면 빈칸).

        오래된 양은 그 폴더에서 차지하는 몫만큼의 막대다. 크기가 같아도 전부 1년 넘게
        그대로인 폴더와 어제도 쓰인 폴더는 정리할 때 전혀 다른 물건이다."""

        for column in (COL_FILES, COL_COLD):
            item.setTextAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
        if node.file_count is not None:
            item.setText(COL_FILES, f"{node.file_count:,}")
        share = node.cold_share
        if share is None:
            return
        days = self._config.settings.detail_scan_cold_days
        item.setText(COL_COLD, formatting.format_kb(node.cold_kb) if node.cold_kb else "-")
        if node.cold_kb:
            item.setData(COL_COLD, widgets.CellDelegate.BAR_ROLE, (share, "neutral"))
        else:
            item.setForeground(COL_COLD, QColor(theme.TEXT_MUTED))
        newest = (
            datetime.fromtimestamp(node.newest_mtime).strftime("%Y-%m-%d")
            if node.newest_mtime else i18n.t("common.none")
        )
        item.setToolTip(COL_COLD, i18n.t(
            "tree.cold_tip", pct=f"{share * 100:.0f}", days=days, newest=newest,
        ))

    def _label_for(self, node) -> str:
        if node.kind == tree_view.REST:
            return i18n.t("tree.rest")
        if node.kind == tree_view.MORE:
            return i18n.t("tree.more", count=f"{node.hidden_count:,}")
        if node.kind == tree_view.FILE:
            return i18n.t("tree.file", name=node.label)
        return node.label

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        self._fill_children(item, depth=1)

    def _fill_children(self, item, depth: int) -> None:
        """이 항목의 자식을 실제로 만든다 (이미 만들었으면 아무 것도 안 한다)."""

        if depth <= 0:
            return
        targets = []
        if item is self.tree.invisibleRootItem():
            targets = [item.child(index) for index in range(item.childCount())]
        else:
            targets = [item]

        for target in targets:
            if target.data(COL_PATH, LOADED_ROLE):
                self._fill_children_of_children(target, depth - 1)
                continue
            node = target.data(COL_PATH, NODE_ROLE)
            target.takeChildren()          # 자리 채우던 빈 줄을 걷어낸다
            target.setData(COL_PATH, LOADED_ROLE, True)
            if node is None:
                continue
            for child in node.children:
                target.addChild(self._make_item(child))
            self._fill_children_of_children(target, depth - 1)

    def _fill_children_of_children(self, item, depth: int) -> None:
        if depth <= 0:
            return
        for index in range(item.childCount()):
            self._fill_children(item.child(index), depth)

    def _map_up(self) -> None:
        self.treemap.go_up()

    def _map_home(self) -> None:
        self.treemap.go_home()

    def _on_map_zoomed(self) -> None:
        """어디까지 들어왔는지 위에 적는다.

        그림만 보면 지금 무엇의 안인지 알 수 없다 - 조각 이름은 상대 이름이라
        같은 이름이 여러 군데에 있다."""

        labels = self.treemap.path_labels()
        self.map_path_label.setText(" / ".join(labels) if labels else "")
        self.map_up_btn.setEnabled(self.treemap.can_go_up())
        self.map_home_btn.setEnabled(self.treemap.can_go_up())

    def _expand_more(self) -> None:
        """보이는 것 한 단계 더 펼치기.

        `expandAll()` 을 쓰지 않는 이유: 자식을 그때 만드는 구조라 전부 펼치면
        디렉터리 수만큼 위젯이 한 번에 생긴다. 수만 개짜리 계정에서 창이 굳는다.
        """

        expanded = []
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item.isExpanded():
                stack.extend(item.child(i) for i in range(item.childCount()))
            elif item.childCount():
                expanded.append(item)
        for item in expanded:
            self._fill_children(item, depth=1)
            item.setExpanded(True)
