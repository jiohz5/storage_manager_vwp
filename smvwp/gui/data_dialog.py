"""데이터 폴더 창 - 무엇이, 얼마나, 언제부터 쌓였나 (보기만 한다).

읽기는 작업 스레드가 한다 (`smvwp.data_inventory`). 데이터 디렉터리가 NFS 면 행을
세는 것만으로 몇 초가 걸리므로, 그동안 무엇을 읽는지 진행 막대로 보여 준다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import widgets
from .. import data_inventory, formatting, i18n
from ..scheduler import ThreadWorker, emit_safely

COLUMN_KEYS = ["data.col.item", "data.col.size", "data.col.count", "data.col.since", "data.col.keep"]
(COL_ITEM, COL_SIZE, COL_COUNT, COL_SINCE, COL_KEEP) = range(5)


class _InventoryReader(ThreadWorker):
    THREAD_NAME = "smvwp-data-folder"
    progress = pyqtSignal(int, int, str)

    def __init__(self, data_dir: Path, get_config, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._get_config = get_config

    def _work(self):
        return data_inventory.build(
            self._data_dir, self._get_config(),
            on_step=lambda index, total, key: emit_safely(self, "progress", index, total, key),
        )


def count_text(item: data_inventory.Item) -> str:
    if item.failed:
        return i18n.t("data.failed")
    if item.count is None:
        return ""
    return i18n.t(f"data.count.{item.count_unit}", count=f"{item.count:,}")


def keep_text(item: data_inventory.Item) -> str:
    if item.keep == data_inventory.KEEP_DAYS:
        return i18n.t("data.keep.days", days=item.keep_value)
    if item.keep == data_inventory.KEEP_GENERATIONS:
        return i18n.t("data.keep.generations", count=item.keep_value)
    if item.keep == data_inventory.KEEP_FOREVER:
        return i18n.t("data.keep.forever")
    if item.keep == data_inventory.KEEP_PER_ITEM:
        return i18n.t("data.keep.per_item")
    if item.keep == data_inventory.KEEP_CAPPED:
        return i18n.t("data.keep.capped", size=formatting.format_bytes(item.keep_value))
    return i18n.t("data.keep.current")


class DataFolderDialog(QDialog):
    def __init__(self, data_dir: Path, config, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._config = config
        self.setWindowTitle(i18n.t("data.title"))
        self.resize(900, 560)
        self._build()

        self._reader = _InventoryReader(data_dir, lambda: self._config, self)
        self._reader.finished.connect(self._on_ready)
        self._reader.failed.connect(self._on_failed)
        self._reader.progress.connect(self._on_progress)
        self._reader.start()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        path_row = QHBoxLayout()
        self.path_label = QLabel(str(self._data_dir))
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setObjectName("sectionTitle")
        copy_btn = QPushButton(i18n.t("guide.btn.copy_path"))
        copy_btn.clicked.connect(self._copy_path)
        path_row.addWidget(self.path_label, 1)
        path_row.addWidget(copy_btn)
        root.addLayout(path_row)

        # 다 읽은 뒤에 채운다 - 읽는 동안은 아래 진행 줄이 말한다.
        self.summary_label = QLabel()
        self.summary_label.setObjectName("muted")
        self.summary_label.setVisible(False)
        root.addWidget(self.summary_label)

        self.loading_row = QWidget()
        loading_box = QHBoxLayout(self.loading_row)
        loading_box.setContentsMargins(0, 0, 0, 0)
        loading_box.setSpacing(10)
        self.loading_label = QLabel(i18n.t("data.loading_start"))
        self.loading_label.setObjectName("caption")
        self.loading_bar = QProgressBar()
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedHeight(6)
        self.loading_bar.setRange(0, 0)
        loading_box.addWidget(self.loading_label)
        loading_box.addWidget(self.loading_bar, 1)
        root.addWidget(self.loading_row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMN_KEYS))
        self.tree.setHeaderLabels([i18n.t(key) for key in COLUMN_KEYS])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setSectionResizeMode(COL_ITEM, QHeaderView.Stretch)
        for column in (COL_COUNT, COL_SINCE, COL_KEEP):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        # 크기 칸에는 가장 큰 것 대비 막대 - 무엇이 데이터 폴더를 먹는지 읽지 않고 보이게.
        header.setSectionResizeMode(COL_SIZE, QHeaderView.Interactive)
        self.tree.setColumnWidth(COL_SIZE, 180)
        self.tree.setItemDelegateForColumn(COL_SIZE, widgets.CellDelegate(self.tree))
        root.addWidget(self.tree, 1)

        note = QLabel(i18n.t("data.note"))
        note.setObjectName("caption")
        note.setWordWrap(True)
        root.addWidget(note)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        close_btn = QPushButton(i18n.t("common.close"))
        close_btn.clicked.connect(self.accept)
        button_row.addWidget(close_btn)
        root.addLayout(button_row)

    # -- 받기 -------------------------------------------------------------
    def _on_progress(self, index: int, total: int, key: str) -> None:
        self.loading_bar.setRange(0, total)
        self.loading_bar.setValue(index - 1)
        self.loading_label.setText(
            i18n.t("data.loading", item=i18n.t(key), index=index, total=total)
        )
        self.loading_row.setVisible(True)

    def _on_ready(self, inventory: data_inventory.Inventory) -> None:
        self.loading_row.setVisible(False)
        self.summary_label.setVisible(True)
        total = formatting.format_bytes(inventory.total_bytes)
        if inventory.since:
            self.summary_label.setText(i18n.t(
                "data.summary", total=total, since=formatting.local_date_text(inventory.since)
            ))
        else:
            self.summary_label.setText(i18n.t("data.summary_empty", total=total))

        self.tree.clear()
        biggest = max((item.size_bytes or 0 for item in inventory.items), default=0) or 1
        for item in inventory.items:
            node = self._row(item, biggest)
            self.tree.addTopLevelItem(node)
            # 안쪽 줄(표마다 행 수)은 크기가 없거나 부모의 몫이라 부모 대비로.
            for child in item.children:
                node.addChild(self._row(child, item.size_bytes or 1))
        self.tree.expandAll()

    def _on_failed(self, message: str) -> None:
        self.loading_row.setVisible(False)
        self.summary_label.setVisible(True)
        self.summary_label.setText(i18n.t("data.read_failed", message=message))

    def _row(self, item: data_inventory.Item, scale: int = 0) -> QTreeWidgetItem:
        node = QTreeWidgetItem([
            i18n.t(item.key),
            formatting.format_bytes(item.size_bytes) if item.size_bytes is not None else "",
            count_text(item),
            formatting.local_date_text(item.oldest, fallback="") if item.oldest else "",
            keep_text(item),
        ])
        for column in (COL_SIZE, COL_COUNT):
            node.setTextAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
        if item.size_bytes and scale:
            node.setData(COL_SIZE, widgets.CellDelegate.BAR_ROLE,
                         (min(1.0, item.size_bytes / scale), "accent"))
        return node

    def _copy_path(self) -> None:
        QApplication.clipboard().setText(str(self._data_dir))
        self.summary_label.setVisible(True)
        self.summary_label.setText(i18n.t("guide.copied", text=str(self._data_dir)))
