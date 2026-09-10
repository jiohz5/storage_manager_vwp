"""서버 부하 이력을 보는 창.

## 왜 화면이 필요한가

같은 내용을 `smvwp_cli.py load` 가 이미 낸다. 그런데 폐쇄망이라 터미널 글자를
긁어 내오는 것 자체가 일이고, 무엇보다 **표를 정렬해 가며 보는 일**이 터미널
출력으로는 안 된다. "메모리 큰 것부터"와 "CPU 큰 것부터"를 오가는 것이 이
데이터를 읽는 방식의 절반이다.

## 이 창이 답하는 것

1. **낮에 상세 스캔을 돌려도 되나** - 시간대마다 다른 작업이 이미 얼마나 쓰나.
2. **그때 누가 서버를 썼나** - 우리 것과 남의 것을 갈라서.
3. **NFS 쪽은 어땠나** - 느렸다면 파일서버 탓인가 우리 슬롯 탓인가.

## 우리를 뺀 나머지로 본다

돌려도 되는지 판단할 때 필요한 것은 우리가 쓴 양이 아니라 **남은 여유**다.
전체 사용률만 보면 우리 스캔이 만든 부하까지 "서버가 원래 바쁘다"로 읽혀서,
돌릴수록 못 돌릴 것 같아지는 이상한 결론이 나온다.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal
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
from .. import formatting, i18n, loadreport, scan_store, tiers, usage_log
from . import theme, widgets

HOUR_COLUMNS = (
    "load.col.hour",
    "load.col.others_cpu",
    "load.col.load",
    "load.col.waiting",
    "load.col.samples",
)
JOB_COLUMNS = (
    "load.col.user",
    "load.col.job",
    "load.col.cpu_peak",
    "load.col.cpu_avg",
    "load.col.mem_peak",
    "load.col.samples",
)
MOUNT_COLUMNS = (
    "load.col.mount",
    "load.col.ops",
    "load.col.rtt",
    "load.col.queue",
)

# 고를 수 있는 기간. 90일은 상시 표본의 보존 기간과 맞춘다.
RANGE_DAYS = (7, 30, 90)


class _LoadReader(QObject):
    """DB 읽기를 작업 스레드로 뺀다 (NFS 위라 왕복이 있다)."""

    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, data_dir: Path, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._lock = threading.Lock()
        self._running = False

    def run_async(self, days: int, by: str) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
        threading.Thread(
            target=self._run, args=(days, by), name="smvwp-load", daemon=True
        ).start()
        return True

    def _run(self, days: int, by: str) -> None:
        conn = None
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            conn = scan_store.connect(self._data_dir)
            samples = scan_store.server_samples(conn, since=since, limit=200000)
            payload = {
                "samples": len(samples),
                "split": loadreport.split_by_scan(samples),
                "hours": loadreport.hourly_profile(samples),
                "jobs": scan_store.busiest_processes(conn, since=since, by=by, limit=30),
                "mounts": scan_store.mount_activity(conn, since=since),
            }
        except Exception as exc:  # pragma: no cover - 방어적 처리
            self.failed.emit(str(exc))
            return
        finally:
            if conn is not None:
                conn.close()
            with self._lock:
                self._running = False
        self.finished.emit(payload)


def _table(column_keys, height: int) -> QTableWidget:
    table = QTableWidget(0, len(column_keys))
    table.setHorizontalHeaderLabels([i18n.t(key) for key in column_keys])
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    for column in range(1, len(column_keys)):
        table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
    table.setMinimumHeight(height)
    return table


class LoadDialog(QDialog):
    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._config = config
        self.setWindowTitle(i18n.t("load.title"))
        self.resize(1000, 720)

        self._reader = _LoadReader(data_dir, self)
        self._reader.finished.connect(self._on_loaded)
        self._reader.failed.connect(self._on_failed)

        self._build_ui()
        usage_log.record(data_dir, usage_log.LOAD_VIEWED)
        self._reload()

    # -- 화면 ---------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(9)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QLabel(i18n.t("load.range_label")))
        self.range_combo = QComboBox()
        for days in RANGE_DAYS:
            self.range_combo.addItem(i18n.t("load.range_days", days=days), days)
        self.range_combo.setCurrentIndex(1)
        self.range_combo.currentIndexChanged.connect(self._reload)
        top.addWidget(self.range_combo)

        top.addWidget(QLabel(i18n.t("load.sort_label")))
        self.sort_combo = QComboBox()
        self.sort_combo.addItem(i18n.t("load.sort.cpu"), scan_store.BUSIEST_BY_CPU)
        self.sort_combo.addItem(i18n.t("load.sort.mem"), scan_store.BUSIEST_BY_MEMORY)
        self.sort_combo.currentIndexChanged.connect(self._reload)
        top.addWidget(self.sort_combo)

        refresh = QPushButton(i18n.t("load.btn.refresh"))
        refresh.clicked.connect(self._reload)
        top.addWidget(refresh)
        top.addStretch(1)
        root.addLayout(top)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        self.hours_caption = QLabel(i18n.t("load.hours_heading"))
        self.hours_caption.setObjectName("muted")
        root.addWidget(self.hours_caption)
        self.hours_table = _table(HOUR_COLUMNS, 150)
        root.addWidget(self.hours_table)

        self.jobs_caption = QLabel(i18n.t("load.jobs_heading"))
        self.jobs_caption.setObjectName("muted")
        root.addWidget(self.jobs_caption)
        self.jobs_table = _table(JOB_COLUMNS, 150)
        root.addWidget(self.jobs_table)
        self.jobs_note = QLabel(i18n.t("load.jobs_note"))
        self.jobs_note.setObjectName("caption")
        self.jobs_note.setWordWrap(True)
        root.addWidget(self.jobs_note)

        self.mounts_caption = QLabel(i18n.t("load.mounts_heading"))
        self.mounts_caption.setObjectName("muted")
        root.addWidget(self.mounts_caption)
        self.mounts_table = _table(MOUNT_COLUMNS, 110)
        root.addWidget(self.mounts_table)
        self.mounts_note = QLabel(i18n.t("load.mounts_note"))
        self.mounts_note.setObjectName("caption")
        self.mounts_note.setWordWrap(True)
        root.addWidget(self.mounts_note)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_btn = QPushButton(i18n.t("common.close"))
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    # -- 불러오기 -----------------------------------------------------
    def _reload(self) -> None:
        self.summary.setText(i18n.t("load.loading"))
        self._reader.run_async(
            self.range_combo.currentData(), self.sort_combo.currentData()
        )

    def _on_failed(self, message: str) -> None:
        self.summary.setText(i18n.t("load.failed", error=message))

    def _on_loaded(self, payload) -> None:
        if not payload["samples"]:
            # 빈 표를 그냥 두면 "서버가 한가하다"로 읽힌다. 사실은 아직 안 쌓인
            # 것이라, 그 둘을 반드시 갈라 말해야 한다.
            self.summary.setText(i18n.t("load.no_samples"))
            for table in (self.hours_table, self.jobs_table, self.mounts_table):
                table.setRowCount(0)
            return

        self._fill_summary(payload)
        self._fill_hours(payload["hours"])
        self._fill_jobs(payload["jobs"])
        self._fill_mounts(payload["mounts"])

    def _fill_summary(self, payload) -> None:
        split = payload["split"]
        quiet = loadreport.quietest_hours(payload["hours"], limit=1)
        best = i18n.t("common.none")
        if quiet:
            verdict = loadreport.daytime_verdict(quiet[0])
            best = i18n.t(
                "load.best_hour",
                hour=verdict.label,
                verdict=i18n.t("load.has_room" if verdict.room else "load.no_room"),
            )
        self.summary.setText(
            i18n.t(
                "load.summary",
                samples=f"{payload['samples']:,}",
                idle=_pct(split["without_scan"].others_cpu.average),
                busy=_pct(split["with_scan"].others_cpu.average),
                best=best,
            )
        )

    def _fill_hours(self, buckets) -> None:
        table = self.hours_table
        table.setRowCount(len(buckets))
        for row, bucket in enumerate(buckets):
            label = bucket.label
            if not bucket.scan_free:
                # 이 표시가 없으면 "새벽은 한가하다"를 그대로 믿게 되는데,
                # 사실 그 한가함 안에 우리 스캔이 들어 있다.
                label += i18n.t("load.mixed_suffix")
            table.setItem(row, 0, QTableWidgetItem(label))
            table.setItem(row, 1, _num(bucket.others_cpu.average, "%"))
            table.setItem(row, 2, _num(bucket.load_avg.average))
            table.setItem(row, 3, _num(bucket.blocked_others.average))
            table.setItem(row, 4, _num(bucket.samples, digits=0))
            if bucket.crowded:
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    if item is not None:
                        item.setForeground(QColor(tiers.color(tiers.WARN)))
            elif not bucket.scan_free:
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    if item is not None:
                        item.setForeground(QColor(theme.TEXT_MUTED))

    def _fill_jobs(self, rows) -> None:
        table = self.jobs_table
        table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            name = str(entry["comm"] or "-")
            if entry["is_ours"]:
                name += i18n.t("load.ours_suffix")
            table.setItem(row, 0, QTableWidgetItem(str(entry["user_name"] or "-")))
            job_item = QTableWidgetItem(name)
            if entry["cmdline"]:
                job_item.setToolTip(str(entry["cmdline"]))
            table.setItem(row, 1, job_item)
            table.setItem(row, 2, _num(entry["cpu_peak"], "%", digits=0))
            table.setItem(row, 3, _num(entry["cpu_avg"], "%", digits=0))
            table.setItem(
                row, 4,
                widgets.NumericItem(
                    formatting.format_kb(entry["rss_peak"]), entry["rss_peak"] or 0
                ),
            )
            table.setItem(row, 5, _num(entry["samples"], digits=0))
            if entry["is_ours"]:
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    if item is not None:
                        item.setForeground(QColor(theme.TEXT_MUTED))

    def _fill_mounts(self, rows) -> None:
        table = self.mounts_table
        table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            table.setItem(row, 0, QTableWidgetItem(str(entry["mount_point"])))
            table.setItem(row, 1, _num(entry["total_ops"], digits=0))
            table.setItem(row, 2, _num(entry["rtt_avg"], "ms", digits=2))
            queue_item = _num(entry["queue_avg"], "ms", digits=2)
            # 대기가 왕복보다 크면 병목이 파일서버가 아니라 우리 쪽 슬롯이다.
            # 그 구분이 다음에 병렬을 올릴지 내릴지를 가른다.
            rtt, queue = entry["rtt_avg"], entry["queue_avg"]
            if rtt is not None and queue is not None and queue > rtt:
                queue_item.setForeground(QColor(tiers.color(tiers.WARN)))
                queue_item.setToolTip(i18n.t("load.queue_tip"))
            table.setItem(row, 3, queue_item)


def _pct(value) -> str:
    return f"{value:.1f}%" if value is not None else i18n.t("common.unknown_value")


def _num(value, suffix: str = "", digits: int = 1) -> widgets.NumericItem:
    """정렬이 되는 숫자 칸. 값이 없으면 '-' 로 두되 정렬용 값은 0."""

    if value is None:
        return widgets.NumericItem("-", 0)
    text = f"{value:,.{digits}f}{suffix}"
    return widgets.NumericItem(text, value)
