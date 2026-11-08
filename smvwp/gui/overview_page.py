"""상세 스캔 탭의 '현황' - 모든 계정 한 바퀴, 완료 일정, 계정별, 지난 밤들.

실기에서 나온 말: "날마다 스캔을 하는데 뭘 했는지 어디까지 했는지를 모르겠다,
어디까지 스캔되고 스토리지가 어떤 상황인지 판단이 힘들다." 그래서 이 탭을 열면
가장 먼저 이 쪽이 보인다. 위에서부터 묻는 순서대로 놓는다 - 전체가 어디까지 왔나,
언제 끝나나, 계정마다는 어떤가, 밤마다 무엇을 했나.

숫자는 `smvwp.scan_overview` 가 내고 여기는 그리기만 한다.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import i18n, scan_overview
from . import theme, widgets
from .overview_widgets import Chip, MiniBar, SegmentBar, Swatch, TimelineBar

def state_colors(state: str):
    """상태의 `(채운 색, 바탕 색)`. 그릴 때마다 묻는다 - 모듈을 읽을 때 표로 담아 두면
    어두운 화면으로 바꿔도 그 표만 밝은 색으로 남는다."""

    return {
        scan_overview.DONE: (theme.SCAN_DONE, theme.SCAN_DONE),
        scan_overview.DOING: (theme.SCAN_DOING, theme.ACCENT_SOFT),
        scan_overview.WAITING: (theme.SCAN_WAITING, theme.SCAN_WAITING),
        scan_overview.UNSEEN: (theme.SCAN_UNSEEN, theme.SCAN_UNSEEN),
    }[state]

STATE_KEYS = {
    scan_overview.DONE: "overview.state.done",
    scan_overview.DOING: "overview.state.doing",
    scan_overview.WAITING: "overview.state.waiting",
    scan_overview.UNSEEN: "overview.state.unseen",
}

COLUMN_KEYS = (
    "overview.col.account", "overview.col.progress", "overview.col.period",
    "overview.col.last", "overview.col.size", "overview.col.next",
)
(COL_ACCOUNT, COL_PROGRESS, COL_PERIOD, COL_LAST, COL_SIZE, COL_NEXT) = range(6)

# 일정은 이만큼의 밤만 칸으로 보이고, 나머지는 "그 뒤 N밤 더" 로 줄인다.
NIGHTS_SHOWN = 3
ACCOUNT_ROLE = Qt.UserRole + 1


def _label(object_name: str = "", text: str = "", wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if object_name:
        label.setObjectName(object_name)
    label.setWordWrap(wrap)
    return label


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())


class _NightBox(QFrame):
    """한 밤의 칸 - 제목, 시간 막대, 계정마다 언제."""

    def __init__(self, night, now, start_hour: int, end_hour: int, parent=None):
        super().__init__(parent)
        self.setObjectName("sunken")
        self.setStyleSheet(
            f"QFrame#sunken {{ background: {theme.SURFACE_SUNKEN}; border: 1px solid {theme.BORDER}; "
            f"border-radius: 12px; }}"
        )
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(8)

        head = QHBoxLayout()
        self.title = _label("sectionTitle", scan_overview.night_label(night, now))
        head.addWidget(self.title)
        head.addStretch(1)
        self.summary = _label("caption", scan_overview.night_summary(night))
        head.addWidget(self.summary)
        box.addLayout(head)

        axis_start, axis_end = _night_axis(night, start_hour, end_hour)
        self.bar = TimelineBar()
        colors = {}
        spans = []
        for slot in night.slots:
            color = colors.setdefault(slot.account_id, theme.NIGHT_SLOTS[len(colors) % len(theme.NIGHT_SLOTS)])
            spans.append((slot.start, slot.end, color))
        self.bar.set_timeline(axis_start, axis_end, spans)
        box.addWidget(self.bar)

        self.rows: List[QLabel] = []
        for slot in night.slots:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(Swatch(colors[slot.account_id]))
            name = _label("", scan_overview.slot_name(slot))
            name.setStyleSheet(f"font-weight: {theme.WEIGHT_BOLD};")
            row.addWidget(name, 1)
            when = _label("muted", scan_overview.slot_text(slot))
            row.addWidget(when)
            box.addLayout(row)
            self.rows.append(name)
        box.addStretch(1)


def _night_axis(night, start_hour: int, end_hour: int):
    """그 밤의 시간창 전체 (지금 도는 밤이면 열린 때부터) - 막대의 축."""

    from datetime import timedelta

    length = timedelta(hours=(end_hour - start_hour) % 24 or 24)
    return night.closes - length, night.closes


class OverviewPage(QWidget):
    """상세 스캔 현황 한 장. 세로로 길어서 스크롤 안에 둔다."""

    account_selected = pyqtSignal(str)
    account_opened = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("subPage")
        self._overview: Optional[scan_overview.Overview] = None
        self._settings = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        outer.addWidget(self.scroll)

        body = QWidget()
        body.setObjectName("subPage")
        self.scroll.setWidget(body)
        box = QVBoxLayout(body)
        box.setContentsMargins(4, 12, 8, 12)
        box.setSpacing(22)

        box.addLayout(self._build_pass())
        box.addLayout(self._build_schedule())
        box.addLayout(self._build_accounts())
        box.addLayout(self._build_runs())
        box.addStretch(1)
        self.retranslate()

    # -- 짜기 ------------------------------------------------------------
    def _build_pass(self):
        section = QVBoxLayout()
        section.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(28)
        left = QVBoxLayout()
        left.setSpacing(2)
        self.pass_title = _label("sectionTitle")
        left.addWidget(self.pass_title)
        number = QHBoxLayout()
        number.setSpacing(12)
        self.pass_percent = _label("display", "-")
        number.addWidget(self.pass_percent, 0, Qt.AlignBottom)
        self.pass_counts = _label("muted")
        number.addWidget(self.pass_counts, 0, Qt.AlignBottom)
        number.addStretch(1)
        left.addLayout(number)
        top.addLayout(left, 1)

        self.pass_stats = {}
        for key in ("started", "finish", "stalest"):
            column = QVBoxLayout()
            column.setSpacing(2)
            caption = _label("statLabel")
            value = _label("")
            value.setStyleSheet(f"font-weight: {theme.WEIGHT_BOLD}; font-size: {theme.FONT_SUBTITLE}px;")
            column.addWidget(caption)
            column.addWidget(value)
            top.addLayout(column)
            self.pass_stats[key] = (caption, value)
        section.addLayout(top)

        self.segments = SegmentBar(28)
        section.addWidget(self.segments)

        legend = QHBoxLayout()
        legend.setSpacing(6)
        self.legend_labels = {}
        for state, key in ((scan_overview.DONE, "overview.legend.done"),
                           (scan_overview.DOING, "overview.legend.doing"),
                           (scan_overview.WAITING, "overview.legend.waiting"),
                           (scan_overview.UNSEEN, "overview.legend.unseen")):
            legend.addWidget(Swatch(lambda state=state: state_colors(state)[0]))
            label = _label("caption")
            legend.addWidget(label)
            legend.addSpacing(10)
            self.legend_labels[key] = label
        self.legend_width = _label("caption")
        legend.addWidget(self.legend_width)
        legend.addStretch(1)
        section.addLayout(legend)
        return section

    def _build_schedule(self):
        section = QVBoxLayout()
        section.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(12)
        self.schedule_title = _label("sectionTitle")
        head.addWidget(self.schedule_title)
        self.schedule_hint = _label("caption", wrap=True)
        head.addWidget(self.schedule_hint, 1)
        section.addLayout(head)
        self.nights_row = QHBoxLayout()
        self.nights_row.setSpacing(14)
        section.addLayout(self.nights_row)
        self.schedule_more = _label("caption")
        section.addWidget(self.schedule_more)
        self.schedule_note = _label("caption", wrap=True)
        section.addWidget(self.schedule_note)
        self.night_boxes: List[_NightBox] = []
        return section

    def _build_accounts(self):
        section = QVBoxLayout()
        section.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(12)
        self.accounts_title = _label("sectionTitle")
        head.addWidget(self.accounts_title)
        self.accounts_hint = _label("caption", wrap=True)
        head.addWidget(self.accounts_hint, 1)
        section.addLayout(head)

        self.table = widgets.read_only_table(len(COLUMN_KEYS), row_height=40, stretch_column=None)
        self.table.setObjectName(theme.TABLE_WITH_WIDGETS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_PROGRESS, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_PROGRESS, 240)
        header.setSectionResizeMode(COL_NEXT, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        section.addWidget(self.table)
        return section

    def _build_runs(self):
        section = QVBoxLayout()
        section.setSpacing(8)
        self.runs_title = _label("sectionTitle")
        section.addWidget(self.runs_title)
        self.runs_grid = QGridLayout()
        self.runs_grid.setHorizontalSpacing(14)
        self.runs_grid.setVerticalSpacing(8)
        self.runs_grid.setColumnStretch(1, 1)
        section.addLayout(self.runs_grid)
        self.runs_empty = _label("caption")
        section.addWidget(self.runs_empty)
        return section

    def retranslate(self) -> None:
        self.pass_title.setText(i18n.t("overview.pass.title"))
        for key, caption_key in (("started", "overview.pass.started"),
                                 ("finish", "overview.pass.finish"),
                                 ("stalest", "overview.pass.stalest")):
            self.pass_stats[key][0].setText(i18n.t(caption_key))
        for key, label in self.legend_labels.items():
            label.setText(i18n.t(key))
        self.legend_width.setText("· " + i18n.t("overview.legend.width"))
        self.schedule_title.setText(i18n.t("overview.schedule.title"))
        self.schedule_hint.setText(i18n.t("overview.schedule.hint"))
        self.schedule_note.setText(i18n.t("overview.schedule.note"))
        self.accounts_title.setText(i18n.t("overview.accounts.title"))
        self.accounts_hint.setText(i18n.t("overview.accounts.hint"))
        self.table.setHorizontalHeaderLabels([i18n.t(key) for key in COLUMN_KEYS])
        self.table.horizontalHeaderItem(COL_SIZE).setToolTip(i18n.t("overview.size.tip"))
        self.runs_title.setText(i18n.t("overview.runs.title"))
        self.runs_empty.setText(i18n.t("overview.runs.empty"))
        if self._overview is not None:
            self.show_overview(self._overview, self._settings)

    # -- 채우기 ----------------------------------------------------------
    def show_overview(self, overview: scan_overview.Overview, settings) -> None:
        self._overview, self._settings = overview, settings
        self._show_pass(overview)
        self._show_schedule(overview, settings)
        self._show_accounts(overview)
        self._show_runs(overview, settings)

    def _show_pass(self, overview) -> None:
        self.pass_percent.setText(f"{overview.percent}%")
        self.pass_counts.setText(scan_overview.pass_counts_text(overview))
        self.pass_stats["started"][1].setText(scan_overview.started_text(overview))
        finish = self.pass_stats["finish"][1]
        finish.setText(scan_overview.finish_text(overview))
        finish.setStyleSheet(
            f"font-weight: {theme.WEIGHT_HEAVY}; font-size: {theme.FONT_SUBTITLE}px; color: {theme.ACCENT};"
        )
        stalest_text, kind = scan_overview.stalest_text(overview)
        stalest = self.pass_stats["stalest"][1]
        stalest.setText(stalest_text)
        color = theme.CHIPS["stale"][1] if kind == "stale" else theme.TEXT
        stalest.setStyleSheet(
            f"font-weight: {theme.WEIGHT_BOLD}; font-size: {theme.FONT_SUBTITLE}px; color: {color};"
        )
        segments = []
        for item in overview.accounts:
            color, track = state_colors(item.state)
            tip = i18n.t(
                "overview.segment.tip", name=item.name,
                state=i18n.t(STATE_KEYS[item.state]),
                progress=scan_overview.progress_text(item),
            )
            segments.append((max(1, item.total_dirs), item.fraction if item.state != scan_overview.UNSEEN else 1.0,
                             color, track, tip))
        self.segments.set_segments(segments)

    def _show_schedule(self, overview, settings) -> None:
        _clear(self.nights_row)
        self.night_boxes = []
        start_hour = settings.detail_scan_window_start_hour
        end_hour = settings.detail_scan_window_end_hour
        if not overview.nights:
            empty = _label("muted", i18n.t("overview.schedule.empty"))
            self.nights_row.addWidget(empty)
        for night in overview.nights[:NIGHTS_SHOWN]:
            box = _NightBox(night, overview.now, start_hour, end_hour)
            self.nights_row.addWidget(box, 1)
            self.night_boxes.append(box)
        extra = len(overview.nights) - NIGHTS_SHOWN
        if overview.beyond:
            self.schedule_more.setText(scan_overview.finish_text(overview))
        elif extra > 0:
            self.schedule_more.setText(i18n.t("overview.schedule.more", count=extra))
        else:
            self.schedule_more.setText("")
        self.schedule_more.setVisible(bool(self.schedule_more.text()))

    def _show_accounts(self, overview) -> None:
        rank = {scan_overview.DOING: 0, scan_overview.WAITING: 1, scan_overview.UNSEEN: 2, scan_overview.DONE: 3}
        items = sorted(overview.accounts, key=lambda item: (rank[item.state], item.order or 0))
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            name = QTableWidgetItem(item.name)
            name.setData(ACCOUNT_ROLE, item.account_id)
            self.table.setItem(row, COL_ACCOUNT, name)
            color, _track = state_colors(item.state)
            self.table.setCellWidget(row, COL_PROGRESS, MiniBar(
                item.fraction if item.state != scan_overview.UNSEEN else 1.0, color,
                scan_overview.progress_text(item),
            ))
            self.table.setItem(row, COL_PERIOD, QTableWidgetItem(scan_overview.period_text(item)))
            age, kind = scan_overview.age_text(item, overview.now)
            self.table.setCellWidget(row, COL_LAST, _chip_cell(age, kind))
            size = QTableWidgetItem(scan_overview.size_text(item))
            size.setToolTip(i18n.t("overview.size.tip"))
            self.table.setItem(row, COL_SIZE, size)
            self.table.setItem(row, COL_NEXT, QTableWidgetItem(scan_overview.next_text(item, overview)))
        # 표가 스크롤 안에 있으므로 제 높이를 다 쓴다 (표 안에 스크롤을 또 두지 않는다).
        height = self.table.horizontalHeader().height() + 40 * len(items) + 6
        self.table.setFixedHeight(max(height, 80))

    def _show_runs(self, overview, settings) -> None:
        _clear(self.runs_grid)
        self.run_rows = []
        start_hour = settings.detail_scan_window_start_hour
        end_hour = settings.detail_scan_window_end_hour
        for row, run in enumerate(overview.runs):
            day = scan_overview.day_text(run.started_at) if run.started_at else "-"
            date = _label("", day)
            date.setStyleSheet(f"font-weight: {theme.WEIGHT_BOLD};")
            bar = TimelineBar(bar_height=14, show_axis=False)
            if run.started_at is not None:
                axis_start, axis_end = scan_overview.run_night_axis(run, start_hour, end_hour)
                why, kind = scan_overview.run_why(run)
                color = {"done": theme.SCAN_DONE, "stale": theme.SCAN_UNSEEN, "doing": theme.ACCENT}.get(
                    kind, theme.SCAN_DOING)
                end = run.ended_at or overview.now
                bar.set_timeline(axis_start, axis_end, [(run.started_at, end, color)])
            else:
                why, kind = scan_overview.run_why(run)
            span = "-" if run.started_at is None else (
                f"{scan_overview.clock(run.started_at)} ~ "
                f"{scan_overview.clock(run.ended_at) if run.ended_at else '…'}"
            )
            time_label = _label("muted", span)
            why_label = _label("", why)
            why_color = theme.CHIPS["stale"][1] if kind == "stale" else (
                theme.GOOD_TEXT if kind == "done" else theme.TEXT_MUTED)
            why_label.setStyleSheet(f"font-weight: {theme.WEIGHT_BOLD}; color: {why_color};")
            done = _label("muted", scan_overview.run_done_text(run))
            for column, widget in enumerate((date, bar, time_label, why_label, done)):
                self.runs_grid.addWidget(widget, row, column, Qt.AlignVCenter)
            self.run_rows.append((date, bar, time_label, why_label, done))
        self.runs_empty.setVisible(not overview.runs)

    # -- 고르기 ----------------------------------------------------------
    def _account_at(self, row: int) -> str:
        item = self.table.item(row, COL_ACCOUNT)
        return item.data(ACCOUNT_ROLE) if item is not None else ""

    def _on_selection(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.account_selected.emit(self._account_at(row))

    def _on_double_click(self, row: int, _column: int) -> None:
        self.account_opened.emit(self._account_at(row))


def _chip_cell(text: str, kind: str) -> QWidget:
    container = QWidget()
    container.setStyleSheet("background: transparent;")
    box = QHBoxLayout(container)
    box.setContentsMargins(4, 6, 4, 6)
    box.addWidget(Chip(text, kind))
    box.addStretch(1)
    return container
