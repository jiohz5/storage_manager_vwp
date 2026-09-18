"""계정 하나를 한 창에서 보는 곳 (홈 표에서 두 번 누르면 열린다).

## 왜 필요한가

한 계정에 대한 사실이 화면 네 군데에 흩어져 있었다 - 사용률과 예측은 홈 표,
추세는 추세 창, 밤새 잰 것과 큰 파일은 상세 스캔 탭, 백업 여부는 우선순위
카드. "이 계정 왜 이래?" 하나에 답하려면 네 군데를 오가야 했다.

여기서는 그 넷을 한 화면에 놓는다. 왼쪽은 **숫자 몇 개**(지금·예측·정리),
오른쪽은 **시간과 목록**(추세선과 표들)이다.

## 읽기는 작업 스레드에서

표본·추세·예측·스캔 상태·헬스체크를 한 번에 읽는다. 데이터 디렉터리가 NFS
위면 그 전부가 왕복이라 GUI 스레드에서 하면 창이 굳는다. 무엇을 못 읽었는지는
`account_detail` 이 알려 주고, 여기서는 그대로 말한다 - 조용히 비워 두면
"없다"로 읽힌다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import account_detail, config as config_module
from .. import formatting, i18n, tiers
from ..scheduler import ThreadWorker
from . import theme, widgets
from .trend_view import TrendChart

TAB_KEYS = ("detail.tab.large", "detail.tab.growth", "detail.tab.health")
(TAB_LARGE, TAB_GROWTH, TAB_HEALTH) = range(3)

LARGE_COLUMN_KEYS = ("large.col.path", "large.col.size", "large.col.share",
                     "large.col.change")
GROWTH_COLUMN_KEYS = ("scan.col.path", "scan.col.current_size", "scan.col.delta")
HEALTH_COLUMN_KEYS = ("detail.health.col.task", "detail.health.col.status",
                      "detail.health.col.size", "detail.health.col.reclaim")


class _DetailReader(ThreadWorker):
    THREAD_NAME = "smvwp-account-detail"

    def __init__(self, data_dir: Path, get_config, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._get_config = get_config

    def run_async(self, account_id: str) -> bool:
        return self.start(account_id)

    def _work(self, account_id: str):
        return account_detail.read(self._data_dir, self._get_config(), account_id)


class AccountDetailDialog(QDialog):
    """계정 하나에 대해 아는 것 전부."""

    # 이 창은 탭을 바꿀 수 없다 (창의 것이다). 값만 내보내고 창이 옮긴다.
    open_scan_requested = pyqtSignal(str)

    def __init__(self, data_dir: Path, get_config, account_id: str, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._get_config = get_config
        self._account_id = account_id
        self._detail = None
        # 1080 세로에서도 들어가야 한다.
        self.resize(1000, 660)

        self._reader = _DetailReader(data_dir, get_config, self)
        self._reader.finished.connect(self._on_loaded)
        self._reader.failed.connect(self._on_failed)

        self._build()
        self._show_account_name()
        self.status_label.setText(i18n.t("detail.loading"))
        self._reader.run_async(account_id)

    # -- 만들기 ----------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        self.name_label = QLabel()
        self.name_label.setObjectName("title")
        head.addWidget(self.name_label)
        self.badge_holder = QHBoxLayout()
        head.addLayout(self.badge_holder)
        head.addStretch(1)

        self.tree_btn = QPushButton(i18n.t("tree.btn.open"))
        self.tree_btn.clicked.connect(self._open_tree)
        self.trend_btn = QPushButton(i18n.t("detail.btn.trend"))
        self.trend_btn.clicked.connect(self._open_trend)
        self.scan_btn = QPushButton(i18n.t("detail.btn.scan"))
        self.scan_btn.clicked.connect(self._go_to_scan)
        close_btn = QPushButton(i18n.t("common.close"))
        close_btn.clicked.connect(self.accept)
        for button in (self.tree_btn, self.trend_btn, self.scan_btn, close_btn):
            head.addWidget(button)
        root.addLayout(head)

        self.path_label = QLabel()
        self.path_label.setObjectName("caption")
        self.path_label.setWordWrap(True)
        # 경로는 복사해서 터미널로 가져가는 일이 잦다.
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.path_label)

        self.status_label = QLabel()
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addLayout(self._build_left(), 0)
        body.addLayout(self._build_right(), 1)
        root.addLayout(body, 1)

    def _card(self, title: str):
        """제목 한 줄 + 값들이 들어가는 카드 하나."""

        card = QFrame()
        card.setObjectName("card")
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        box.addWidget(label)
        return card, box, label

    def _build_left(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(10)
        # 왼쪽은 숫자 몇 개다. 넓힐 값이 없으므로 폭을 묶고 남는 가로는
        # 추세선과 표에 준다.
        column.setContentsMargins(0, 0, 0, 0)

        card, box, self.now_title = self._card(i18n.t("detail.now_heading"))
        card.setFixedWidth(330)
        self.usage_label = QLabel("-")
        self.usage_label.setObjectName("display")
        box.addWidget(self.usage_label)
        self.usage_bar = widgets.UsageBar(None, tiers.UNKNOWN)
        self.usage_bar.setFixedHeight(18)
        box.addWidget(self.usage_bar)
        # 막대는 값이 올 때 새로 만들어 끼운다 (색과 비율을 생성자에서 받는다).
        self._now_box = box
        self.facts = QGridLayout()
        self.facts.setHorizontalSpacing(10)
        self.facts.setVerticalSpacing(3)
        self.facts.setColumnStretch(1, 1)
        box.addLayout(self.facts)
        self._fact_rows = {}
        for key in ("capacity", "free", "inode", "quota", "collected"):
            caption = QLabel(i18n.t(f"detail.{key}"))
            caption.setObjectName("caption")
            value = QLabel("-")
            row = self.facts.rowCount()
            self.facts.addWidget(caption, row, 0)
            self.facts.addWidget(value, row, 1)
            self._fact_rows[key] = (caption, value)
        self.sample_error = QLabel()
        self.sample_error.setObjectName("captionWarn")
        self.sample_error.setWordWrap(True)
        self.sample_error.setVisible(False)
        box.addWidget(self.sample_error)
        column.addWidget(card)

        card, box, self.forecast_title = self._card(i18n.t("detail.forecast_heading"))
        card.setFixedWidth(330)
        self.forecast_label = QLabel("-")
        self.forecast_label.setWordWrap(True)
        box.addWidget(self.forecast_label)
        column.addWidget(card)

        card, box, self.scan_title = self._card(i18n.t("detail.scan_heading"))
        card.setFixedWidth(330)
        self.scan_label = QLabel("-")
        self.scan_label.setWordWrap(True)
        box.addWidget(self.scan_label)
        self.scan_trouble = QLabel()
        self.scan_trouble.setObjectName("captionWarn")
        self.scan_trouble.setWordWrap(True)
        self.scan_trouble.setVisible(False)
        box.addWidget(self.scan_trouble)
        column.addWidget(card)

        card, box, self.health_title = self._card(i18n.t("detail.health_heading"))
        card.setFixedWidth(330)
        self.health_label = QLabel("-")
        self.health_label.setWordWrap(True)
        box.addWidget(self.health_label)
        column.addWidget(card)

        column.addStretch(1)
        return column

    def _build_right(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(8)

        self.trend_summary = QLabel()
        self.trend_summary.setObjectName("muted")
        self.trend_summary.setWordWrap(True)
        column.addWidget(self.trend_summary)

        self.chart = TrendChart()
        self.chart.setMinimumHeight(150)
        column.addWidget(self.chart)

        strip = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setObjectName("subTabs")
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        strip.addWidget(self.tabs, 0, Qt.AlignBottom)
        strip.addStretch(1)
        column.addLayout(strip)

        rule = QFrame()
        rule.setObjectName("subTabsRule")
        rule.setFixedHeight(1)
        column.addWidget(rule)

        self.stack = QStackedWidget()
        self.stack.setObjectName("subPages")
        column.addWidget(self.stack, 1)
        self.tabs.currentChanged.connect(self.stack.setCurrentIndex)

        self.large_table = self._table(LARGE_COLUMN_KEYS, TAB_KEYS[TAB_LARGE])
        self.growth_table = self._table(GROWTH_COLUMN_KEYS, TAB_KEYS[TAB_GROWTH])
        self.health_table = self._table(HEALTH_COLUMN_KEYS, TAB_KEYS[TAB_HEALTH])
        return column

    def _table(self, column_keys, tab_key: str) -> QTableWidget:
        page = QWidget()
        page.setObjectName("subPage")
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 10, 0, 0)

        table = QTableWidget(0, len(column_keys))
        table.setHorizontalHeaderLabels([i18n.t(key) for key in column_keys])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        table.setShowGrid(False)
        table.setWordWrap(False)
        # 경로는 가운데를 줄인다 - 오른쪽을 자르면 정작 다른 뒤쪽이 사라진다.
        table.setTextElideMode(Qt.ElideMiddle)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setHighlightSections(False)
        box.addWidget(table, 1)

        self.stack.addWidget(page)
        self.tabs.addTab(i18n.t(tab_key))
        return table

    # -- 채우기 ----------------------------------------------------------
    def _show_account_name(self) -> None:
        account = config_module.find_account(self._get_config(), self._account_id)
        name = account.name if account else self._account_id
        self.name_label.setText(name)
        self.setWindowTitle(i18n.t("detail.title", account=name))
        if account:
            self.path_label.setText(account.path)

    def _on_failed(self, message: str) -> None:
        self.status_label.setText(i18n.t("detail.failed", error=message))

    def _on_loaded(self, detail) -> None:
        if detail is None:
            self.status_label.setText(i18n.t("detail.gone"))
            return
        self._detail = detail
        self._show_header(detail)
        self._show_now(detail)
        self._show_forecast(detail)
        self._show_scan(detail)
        self._show_health(detail)
        self._show_trend(detail)
        self._fill_large(detail)
        self._fill_growth(detail)
        self._fill_health_table(detail)

    def _show_header(self, detail) -> None:
        self.name_label.setText(detail.name)
        parts = [i18n.t(f"account.kind.{detail.kind}")]
        if detail.backup_name:
            parts.append(i18n.t("detail.backup_link", account=detail.backup_name))
        parts.append(detail.path)
        self.path_label.setText("  ·  ".join(parts))

        # 등급 배지는 값이 올 때 만든다 (만들어 두고 바꾸면 폭이 어긋난다).
        while self.badge_holder.count():
            item = self.badge_holder.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        pct = detail.sample.byte_pct if detail.sample else None
        self.badge_holder.addWidget(widgets.badge_cell(detail.tier, pct))

        if detail.failures:
            names = ", ".join(
                i18n.t(f"detail.part.{part}") for part in detail.failures
            )
            self.status_label.setText(i18n.t("detail.partial_failure", parts=names))
        else:
            self.status_label.setText("")
            self.status_label.setVisible(False)

    def _fact(self, key: str, text: str) -> None:
        self._fact_rows[key][1].setText(text)

    def _show_now(self, detail) -> None:
        sample = detail.sample
        dash = i18n.t("common.none")
        if sample is None:
            self.usage_label.setText(dash)
            self.usage_label.setStyleSheet("")
            for key in self._fact_rows:
                self._fact(key, dash)
            self.sample_error.setText(i18n.t("detail.no_sample"))
            self.sample_error.setVisible(True)
            return

        self.usage_label.setText(
            f"{sample.byte_pct:.1f}%" if sample.byte_pct is not None else dash
        )
        self.usage_label.setStyleSheet(f"color: {tiers.color(detail.tier)};")
        old = self.usage_bar
        self.usage_bar = widgets.UsageBar(sample.byte_pct, detail.tier)
        self.usage_bar.setFixedHeight(18)
        # 숫자 바로 밑, 있던 자리에 끼운다.
        self._now_box.insertWidget(self._now_box.indexOf(old), self.usage_bar)
        old.setParent(None)
        old.deleteLater()

        self._fact("capacity", formatting.format_size_pair(sample.used_kb, sample.total_kb))
        self._fact("free", formatting.format_kb(sample.avail_kb))
        self._fact(
            "inode",
            f"{sample.inode_pct:.1f}%" if sample.inode_pct is not None else dash,
        )
        self._fact(
            "quota",
            f"{sample.quota_pct:.1f}%" if sample.quota_pct is not None else dash,
        )
        self._fact("collected", formatting.local_minute_text(sample.collected_at))
        if sample.ok:
            self.sample_error.setVisible(False)
        else:
            self.sample_error.setText(
                i18n.t("detail.error", message=sample.error_message or "")
            )
            self.sample_error.setVisible(True)

    def _show_forecast(self, detail) -> None:
        self.forecast_label.setText(formatting.format_forecast_cell(detail.forecast))
        if detail.forecast is not None:
            window_hours = getattr(detail.forecast, "window_hours", 0) or 0
            try:
                self.forecast_label.setToolTip(
                    formatting.format_forecast_tooltip(detail.forecast, window_hours)
                )
            except Exception:  # pragma: no cover - 툴팁이 창을 죽이면 안 된다
                pass

    def _show_scan(self, detail) -> None:
        scan = detail.scan
        if scan is None or scan.last_completed_generation is None:
            self.scan_label.setText(i18n.t("detail.scan_never"))
            self.scan_trouble.setVisible(False)
            return
        parts = [
            i18n.t(
                "detail.scan_line",
                when=formatting.scan_label(
                    scan.current_scan_at, scan.last_completed_generation
                ),
                size=formatting.format_kb(scan.measured_kb),
            )
        ]
        delta = detail.measured_delta_kb
        if delta is not None:
            parts.append(
                i18n.t("detail.scan_delta", delta=formatting.format_kb_delta(delta))
            )
        self.scan_label.setText("  ·  ".join(parts))

        # 못 잰 곳이 있으면 숫자를 그대로 믿으면 안 된다.
        troubles = []
        if scan.failed_count:
            troubles.append(i18n.t("scan.acct.note_failed", count=scan.failed_count))
        if scan.partial_paths:
            troubles.append(
                i18n.t("scan.acct.note_partial", count=len(scan.partial_paths))
            )
        self.scan_trouble.setText("  ·  ".join(troubles))
        self.scan_trouble.setVisible(bool(troubles))

    def _show_health(self, detail) -> None:
        if not detail.health:
            self.health_label.setText(i18n.t("detail.health_none"))
            return
        if not detail.has_backup_link:
            self.health_label.setText(i18n.t("detail.health_no_link"))
            return
        parts = [
            i18n.t(
                "detail.health_line",
                done=len(detail.backed_up), risky=len(detail.at_risk),
            )
        ]
        if detail.reclaimable_kb:
            parts.append(
                i18n.t(
                    "detail.reclaimable",
                    size=formatting.format_kb(detail.reclaimable_kb),
                )
            )
        self.health_label.setText("\n".join(parts))

    def _show_trend(self, detail) -> None:
        series = detail.series
        self.chart.set_series(series, detail.tier)
        if series is None or series.empty:
            self.trend_summary.setText(i18n.t("trend.no_data_in_range"))
            return
        parts = [
            i18n.t(
                "trend.summary",
                now=f"{series.last:.1f}", low=f"{series.low:.1f}",
                high=f"{series.high:.1f}",
            )
        ]
        if series.change is not None:
            parts.append(i18n.t("trend.change", delta=f"{series.change:+.1f}"))
        if series.gaps:
            parts.append(i18n.t("trend.gaps", count=series.gaps))
        self.trend_summary.setText("  ·  ".join(parts))

    # -- 표 --------------------------------------------------------------
    def _path_item(self, path: str) -> QTableWidgetItem:
        """계정 기준으로 줄인 경로 칸. 줄였으면 전체는 툴팁에 둔다."""

        root = self._detail.path if self._detail else ""
        item = QTableWidgetItem(formatting.relative_path(path, root))
        if item.text() != path:
            item.setToolTip(path)
        return item

    @staticmethod
    def _empty_row(table: QTableWidget, text: str) -> None:
        table.setRowCount(1)
        item = QTableWidgetItem(text)
        item.setForeground(QColor(theme.TEXT_MUTED))
        table.setItem(0, 0, item)
        for column in range(1, table.columnCount()):
            table.setItem(0, column, QTableWidgetItem(""))

    def _fill_large(self, detail) -> None:
        table = self.large_table
        table.setRowCount(0)
        rows = detail.large_files[: account_detail.ROWS_SHOWN]
        if not rows:
            self._empty_row(table, i18n.t("detail.no_rows"))
            return
        table.setRowCount(len(rows))
        dash = i18n.t("common.none")
        for row, item in enumerate(rows):
            table.setItem(row, 0, self._path_item(item.path))
            table.setItem(
                row, 1,
                widgets.NumericItem(formatting.format_kb(item.size_kb), item.size_kb),
            )
            share = item.share_pct
            table.setItem(
                row, 2,
                widgets.NumericItem(
                    f"{share:.1f}%" if share is not None else dash, share or 0
                ),
            )
            if item.previous_kb is None:
                change = QTableWidgetItem(i18n.t("large.new"))
            else:
                change = widgets.NumericItem(
                    formatting.format_kb_delta(item.delta_kb), item.delta_kb
                )
            table.setItem(row, 3, change)
            if item.is_notable:
                for column in range(table.columnCount()):
                    cell = table.item(row, column)
                    if cell is not None:
                        cell.setForeground(QColor(tiers.color(tiers.WARN)))

    def _fill_growth(self, detail) -> None:
        table = self.growth_table
        table.setRowCount(0)
        rows = detail.growth_rows[: account_detail.ROWS_SHOWN]
        if not rows:
            self._empty_row(table, i18n.t("detail.no_rows"))
            return
        table.setRowCount(len(rows))
        dash = i18n.t("common.none")
        for index, row in enumerate(rows):
            keys = row.keys() if hasattr(row, "keys") else []
            table.setItem(index, 0, self._path_item(row["path"]))
            if "current_kb" in keys:
                current = row["current_kb"]
                previous = row["previous_kb"]
            else:
                # 기준선만 있는 계정은 "큰 경로" 목록이 온다 - 견줄 것이 없다.
                current, previous = row["size_kb"], None
            table.setItem(
                index, 1,
                widgets.NumericItem(formatting.format_kb(current), current),
            )
            if "current_kb" not in keys:
                table.setItem(index, 2, widgets.NumericItem(dash, None))
            elif previous is None:
                table.setItem(index, 2, QTableWidgetItem(i18n.t("scan.new_path")))
            else:
                table.setItem(
                    index, 2,
                    widgets.NumericItem(
                        formatting.format_kb_delta(current - previous),
                        current - previous,
                    ),
                )

    def _fill_health_table(self, detail) -> None:
        table = self.health_table
        table.setRowCount(0)
        rows = detail.health[: account_detail.ROWS_SHOWN]
        if not rows:
            self._empty_row(table, i18n.t("detail.health_none"))
            return
        table.setRowCount(len(rows))
        dash = i18n.t("common.none")
        for index, item in enumerate(rows):
            label = QTableWidgetItem(item.label)
            label.setToolTip(item.run_path)
            table.setItem(index, 0, label)
            status = QTableWidgetItem(i18n.t(f"health.status.{item.status}"))
            if item.risky:
                status.setForeground(QColor(tiers.color(tiers.WARN)))
            table.setItem(index, 1, status)
            table.setItem(
                index, 2,
                widgets.NumericItem(
                    formatting.format_kb(item.run_size_kb), item.run_size_kb
                ),
            )
            # 정리 가능은 **백업이 확인된 것만** 센다. 확인 안 된 것을 더하면
            # "이만큼 비울 수 있다"가 실제보다 커진다.
            table.setItem(
                index, 3,
                widgets.NumericItem(
                    formatting.format_kb(item.reclaimable_kb)
                    if item.reclaimable_kb else dash,
                    item.reclaimable_kb,
                ),
            )

    # -- 동작 ------------------------------------------------------------
    def _open_tree(self) -> None:
        from .tree_dialog import TreeDialog

        TreeDialog(
            self._data_dir, self._get_config(),
            account_id=self._account_id, parent=self,
        ).exec_()

    def _open_trend(self) -> None:
        from .trend_dialog import TrendDialog

        TrendDialog(
            self._data_dir, self._get_config(),
            account_id=self._account_id, parent=self,
        ).exec_()

    def _go_to_scan(self) -> None:
        self.open_scan_requested.emit(self._account_id)
        self.accept()
