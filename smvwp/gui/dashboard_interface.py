"""대시보드 서브 인터페이스 - 계정 현황과 야간 상세 스캔.

화면 구성은 Fluent를 쓰되, 표시 규칙과 판단 로직은 다음을 지킨다:

- 수집이 멈춰 있으면 사용률 요약보다 **그게 먼저** 보인다. 멈춘 데이터를 보고
  "정상"이라 판단하는 것이 가장 위험하기 때문.
- 주의 이상인 행만 옅게 칠한다. 정상까지 칠하면 색이 배경 소음이 되어 정작
  문제 있는 행이 묻힌다.
- 신선도는 **첫 수집을 돌리기 전에** 판정한다. GUI를 열면 스케줄러가 곧바로
  한 번 수집하므로, 그 뒤에 보면 늘 "방금"이 되어 "그동안 cron이 안 돌았다"는
  사실이 가려진다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    InfoBar,
    InfoBarPosition,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TableWidget,
)
from qfluentwidgets import FluentIcon as FIF

from .. import config as config_module
from .. import diagnostics, forecast_notify, freshness, i18n, nightly_scan, quota, store, tiers
from . import widgets
from .scheduler import CollectorScheduler, NightlyScanWorker

COLUMN_KEYS = [
    "dashboard.col.name",
    "dashboard.col.path",
    "dashboard.col.filesystem",
    "dashboard.col.byte_pct",
    "dashboard.col.inode_pct",
    "dashboard.col.quota",
    "dashboard.col.tier",
    "forecast.column",
    "dashboard.col.collected_at",
    "dashboard.col.status",
]
(
    COL_NAME,
    COL_PATH,
    COL_FS,
    COL_BYTE,
    COL_INODE,
    COL_QUOTA,
    COL_TIER,
    COL_FORECAST,
    COL_TIME,
    COL_STATUS,
) = range(10)

GROWTH_COLUMN_KEYS = ["scan.col.path", "scan.col.current_size", "scan.col.delta"]

SCAN_STATUS_REFRESH_MS = 5000

# 등급별 요약 배너 배색 (배경, 테두리). 글자는 등급 기준색을 쓴다.
BANNER_COLORS = {
    tiers.NORMAL: ("#1b3a20", "#2e7d32"),
    tiers.WARN: ("#3a3218", "#f9a825"),
    tiers.ALERT: ("#3a2612", "#ef6c00"),
    tiers.EMERGENCY: ("#3a1c1c", "#c62828"),
    tiers.FULL: ("#2e1a35", "#6a1b9a"),
    tiers.UNKNOWN: ("#2b2b2b", "#555555"),
}

# 다크 테마용 행 배경 - 밝은 톤을 그대로 쓰면 흰 글자가 안 보인다.
ROW_BACKGROUNDS_DARK = {
    tiers.WARN: "#3a3218",
    tiers.ALERT: "#3a2612",
    tiers.EMERGENCY: "#3a1c1c",
    tiers.FULL: "#2e1a35",
}


class DashboardInterface(QWidget):
    """계정 현황 + 야간 상세 스캔."""

    def __init__(self, data_dir: Path, config: config_module.AppConfig, parent=None):
        super().__init__(parent)
        self.setObjectName("dashboardInterface")
        self._data_dir = data_dir
        self._config = config
        self._latest_samples: Dict[str, store.SampleRecord] = {}
        self._forecasts: Dict[str, object] = {}
        self._freshness: Dict[str, object] = {}
        self._scan_snapshot = None

        self._build_ui()
        self.retranslate()

        # 신선도는 첫 수집 전에 판정해야 진실이 드러난다 (모듈 docstring 참고).
        self._freshness = self._evaluate_freshness()
        self._refresh_table_from_store()

        self._scheduler = CollectorScheduler(
            data_dir,
            get_config=lambda: self._config,
            on_finished=self._on_collection_finished,
            on_failed=self._on_collection_failed,
        )
        self._scheduler.start(run_immediately=True)

        self._scan_worker = NightlyScanWorker(data_dir, get_config=lambda: self._config)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.failed.connect(self._on_scan_failed)

        self._scan_status_timer = QTimer(self)
        self._scan_status_timer.timeout.connect(self._refresh_scan_section)
        self._scan_status_timer.start(SCAN_STATUS_REFRESH_MS)
        self._refresh_scan_section()

    # -- UI 구성 -----------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # 한눈 요약 배너
        self.summary_card = CardWidget(self)
        summary_layout = QVBoxLayout(self.summary_card)
        summary_layout.setContentsMargins(18, 14, 18, 14)
        self.summary_label = SubtitleLabel("", self.summary_card)
        self.summary_label.setWordWrap(True)
        summary_layout.addWidget(self.summary_label)
        root.addWidget(self.summary_card)

        self.caveat_label = CaptionLabel("", self)
        self.caveat_label.setWordWrap(True)
        root.addWidget(self.caveat_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.collect_btn = PrimaryPushButton(FIF.SYNC, "", self)
        self.collect_btn.clicked.connect(self._trigger_now)
        self.diagnose_btn = PushButton(FIF.DEVELOPER_TOOLS, "", self)
        self.diagnose_btn.clicked.connect(self._open_diagnostics)
        button_row.addWidget(self.collect_btn)
        button_row.addWidget(self.diagnose_btn)
        button_row.addStretch(1)
        root.addLayout(button_row)

        self.table = TableWidget(self)
        self.table.setColumnCount(len(COLUMN_KEYS))
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._refresh_growth_table)
        root.addWidget(self.table, 3)

        root.addWidget(self._build_scan_card(), 2)

        self.status_label = CaptionLabel("", self)
        root.addWidget(self.status_label)

    def _build_scan_card(self) -> CardWidget:
        card = CardWidget(self)
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(8)

        self.scan_title_label = StrongBodyLabel("", card)
        box.addWidget(self.scan_title_label)

        self.scan_status_label = BodyLabel("", card)
        self.scan_status_label.setWordWrap(True)
        box.addWidget(self.scan_status_label)

        scan_buttons = QHBoxLayout()
        scan_buttons.setSpacing(8)
        self.scan_run_btn = PushButton(FIF.PLAY, "", card)
        self.scan_run_btn.clicked.connect(self._trigger_scan_now)
        self.scan_stop_btn = PushButton(FIF.PAUSE, "", card)
        self.scan_stop_btn.clicked.connect(self._request_scan_stop)
        scan_buttons.addWidget(self.scan_run_btn)
        scan_buttons.addWidget(self.scan_stop_btn)
        scan_buttons.addStretch(1)
        box.addLayout(scan_buttons)

        self.growth_caption = CaptionLabel("", card)
        self.growth_caption.setWordWrap(True)
        box.addWidget(self.growth_caption)

        self.growth_table = TableWidget(card)
        self.growth_table.setColumnCount(len(GROWTH_COLUMN_KEYS))
        self.growth_table.setBorderVisible(True)
        self.growth_table.setBorderRadius(8)
        self.growth_table.verticalHeader().hide()
        self.growth_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.growth_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        box.addWidget(self.growth_table)
        return card

    # -- 다국어 ------------------------------------------------------
    def retranslate(self) -> None:
        self.caveat_label.setText(i18n.t("dashboard.df_caveat"))
        self.collect_btn.setText(i18n.t("dashboard.btn.collect_now"))
        self.diagnose_btn.setText(i18n.t("dashboard.btn.diagnose"))
        self.table.setHorizontalHeaderLabels([i18n.t(key) for key in COLUMN_KEYS])

        self.scan_title_label.setText(i18n.t("scan.section_title"))
        self.scan_run_btn.setText(i18n.t("scan.btn.run_now"))
        self.scan_run_btn.setToolTip(i18n.t("scan.btn.run_now_tooltip"))
        self.scan_stop_btn.setText(i18n.t("scan.btn.stop"))
        self.scan_stop_btn.setToolTip(i18n.t("scan.btn.stop_tooltip"))
        self.growth_table.setHorizontalHeaderLabels(
            [i18n.t(key) for key in GROWTH_COLUMN_KEYS]
        )

    def reload_config(self, config: config_module.AppConfig) -> None:
        """설정 화면에서 계정/설정이 바뀌면 호출된다."""

        self._config = config
        self.retranslate()
        self._refresh_table_from_store()
        self._refresh_scan_section()
        self._scheduler.restart_with_current_interval()

    # -- 데이터 갱신 ----------------------------------------------------
    def _refresh_table_from_store(self) -> None:
        conn = store.connect(self._data_dir)
        try:
            latest = store.latest_samples(conn)
        finally:
            conn.close()
        self._latest_samples = latest
        self._refresh_forecasts()
        self._render_table(latest)

    def _evaluate_freshness(self) -> Dict[str, object]:
        """수집 상태를 판정한다 (읽기 전용).

        판정 실패가 대시보드를 못 뜨게 하면 안 되므로 예외는 삼키고 빈 결과로
        둔다 - 그러면 경고만 안 뜰 뿐 나머지는 정상 동작한다."""

        if not self._config.settings.freshness_enabled:
            return {}
        try:
            return {
                item.account_id: item
                for item in freshness.evaluate_all(self._data_dir, self._config)
            }
        except Exception:  # pragma: no cover - 방어적 처리
            return {}

    def _freshness_warning(self) -> Optional[str]:
        problems = [f for f in self._freshness.values() if f.needs_attention]
        if not problems:
            return None

        stalled = [
            f for f in problems if f.status in (freshness.STATUS_STALE, freshness.STATUS_NEVER)
        ]
        if stalled:
            oldest = max(
                (f for f in stalled if f.age_seconds is not None),
                key=lambda f: f.age_seconds,
                default=None,
            )
            age_text = (
                freshness.format_age(oldest.age_seconds)
                if oldest is not None
                else i18n.t("freshness.never")
            )
            return i18n.t("freshness.stale_summary", count=len(stalled), age=age_text)

        gappy = [f for f in problems if f.status == freshness.STATUS_GAPPY]
        worst = min(gappy, key=lambda f: f.coverage_pct or 0)
        return i18n.t(
            "freshness.gappy_summary",
            count=len(gappy),
            coverage=int(worst.coverage_pct or 0),
            hours=self._config.settings.freshness_window_hours,
        )

    def _refresh_forecasts(self) -> None:
        """FULL 예측을 다시 계산한다 (읽기 전용).

        예측 실패가 대시보드 자체를 못 뜨게 만들면 안 되므로 예외를 삼키고
        빈 결과로 둔다 - 그러면 해당 칸만 '-'로 표시된다."""

        try:
            forecasts = forecast_notify.build_forecasts(self._data_dir, self._config)
            self._forecasts = {f.account_id: f for f in forecasts}
        except Exception:  # pragma: no cover - 방어적 처리
            self._forecasts = {}

    def _render_table(self, latest: Dict[str, store.SampleRecord]) -> None:
        accounts = self._config.accounts
        self.table.setRowCount(len(accounts))

        worst_tier = tiers.NORMAL
        worst_account_label: Optional[str] = None
        warn_or_worse_count = 0
        dash = i18n.t("common.none")

        for row, account in enumerate(accounts):
            sample = latest.get(account.account_id)
            self.table.setItem(row, COL_NAME, QTableWidgetItem(account.name))
            self.table.setItem(row, COL_PATH, QTableWidgetItem(account.path))

            if sample is None:
                for column in (COL_FS, COL_BYTE, COL_INODE, COL_QUOTA, COL_FORECAST, COL_TIME):
                    self.table.setItem(row, column, QTableWidgetItem(dash))
                self.table.setCellWidget(row, COL_TIER, widgets.TierBadge(tiers.UNKNOWN, None))
                self.table.setItem(
                    row, COL_STATUS, QTableWidgetItem(i18n.t("dashboard.not_collected"))
                )
                continue

            self.table.setItem(row, COL_FS, QTableWidgetItem(sample.filesystem or dash))
            byte_text = f"{sample.byte_pct:.1f}%" if sample.byte_pct is not None else dash
            inode_text = (
                f"{sample.inode_pct:.1f}%"
                if sample.inode_pct is not None
                else i18n.t("common.unknown_value")
            )
            self.table.setItem(row, COL_BYTE, QTableWidgetItem(byte_text))
            self.table.setItem(row, COL_INODE, QTableWidgetItem(inode_text))
            self.table.setItem(row, COL_QUOTA, QTableWidgetItem(quota.format_usage(sample)))
            self.table.setCellWidget(
                row, COL_TIER, widgets.TierBadge(sample.overall_tier, sample.byte_pct)
            )

            forecast = self._forecasts.get(account.account_id)
            forecast_item = QTableWidgetItem(widgets.format_forecast_cell(forecast))
            forecast_item.setToolTip(
                widgets.format_forecast_tooltip(
                    forecast, self._config.settings.full_prediction_window_hours
                )
            )
            if forecast is not None and forecast.imminent.ok:
                imminent_tier = forecast_notify.forecast_tier(
                    forecast.imminent.hours_to_full, self._config.settings
                )
                if imminent_tier is not None:
                    forecast_item.setForeground(QColor(tiers.color(imminent_tier)))
            self.table.setItem(row, COL_FORECAST, forecast_item)

            # 절대 시각보다 "얼마나 오래됐나"가 판단에 직접 쓰인다. 원본
            # 타임스탬프는 툴팁에 남긴다.
            status_info = self._freshness.get(account.account_id)
            time_item = QTableWidgetItem(
                freshness.format_age(status_info.age_seconds)
                if status_info is not None and status_info.age_seconds is not None
                else sample.collected_at
            )
            time_item.setToolTip(sample.collected_at)
            if status_info is not None and status_info.needs_attention:
                time_item.setForeground(QColor(tiers.color(tiers.ALERT)))
            self.table.setItem(row, COL_TIME, time_item)

            status_text = (
                i18n.t("dashboard.collect_ok")
                if sample.ok
                else i18n.t("dashboard.collect_failed", message=sample.error_message)
            )
            self.table.setItem(row, COL_STATUS, QTableWidgetItem(status_text))

            self._tint_row(row, sample.overall_tier if sample.ok else tiers.UNKNOWN)

            if sample.ok:
                if tiers.is_at_least(sample.overall_tier, "warn"):
                    warn_or_worse_count += 1
                if tiers.severity(sample.overall_tier) > tiers.severity(worst_tier):
                    worst_tier = sample.overall_tier
                    worst_account_label = (
                        f"{account.name} "
                        f"({tiers.display_text(sample.overall_tier, sample.byte_pct)})"
                    )

        self._update_summary(warn_or_worse_count, worst_account_label, worst_tier)

    def _tint_row(self, row: int, tier: str) -> None:
        """주의 이상인 행만 옅게 칠한다 (정상/확인불가는 칠하지 않음).

        등급은 배지 텍스트로도 보이므로, 색을 못 보는 환경에서도 정보가
        사라지지 않는다."""

        background = ROW_BACKGROUNDS_DARK.get(tier)
        if background is None:
            return
        brush = QBrush(QColor(background))
        for column in range(self.table.columnCount()):
            item = self.table.item(row, column)
            if item is not None:
                item.setBackground(brush)

    def _update_summary(
        self, warn_or_worse_count: int, worst_account_label: Optional[str], worst_tier: str
    ) -> None:
        if not self._config.accounts:
            self.summary_label.setText(i18n.t("dashboard.no_accounts"))
            self._apply_banner(tiers.UNKNOWN)
            return

        banner_tier = worst_tier if warn_or_worse_count else tiers.NORMAL
        if warn_or_worse_count == 0:
            text = i18n.t("dashboard.all_normal", count=len(self._config.accounts))
        else:
            text = i18n.t(
                "dashboard.warn_summary", count=warn_or_worse_count, worst=worst_account_label
            )

        # 수집이 멈춰 있으면 사용률 요약보다 그게 먼저다.
        warning = self._freshness_warning()
        if warning:
            text = f"{warning}\n{text}"
            banner_tier = tiers.worse(banner_tier, tiers.ALERT)

        self.summary_label.setText(text)
        self._apply_banner(banner_tier)

    def _apply_banner(self, tier: str) -> None:
        background, border = BANNER_COLORS.get(tier, BANNER_COLORS[tiers.UNKNOWN])
        self.summary_card.setStyleSheet(
            f"CardWidget {{ background: {background}; border: 1px solid {border};"
            "border-radius: 8px; }"
        )
        self.summary_label.setStyleSheet(f"color: {tiers.color(tier)};")

    # -- 이벤트 핸들러 --------------------------------------------------
    def _trigger_now(self) -> None:
        self.status_label.setText(i18n.t("dashboard.collecting"))
        self._scheduler.trigger_now()

    def _on_collection_finished(self, records) -> None:
        self._refresh_table_from_store()
        failed = sum(1 for r in records if not r.ok)
        if failed:
            message = i18n.t(
                "dashboard.collected_with_failures", count=len(records), failed=failed
            )
            self.status_label.setText(message)
            InfoBar.warning(
                title=i18n.t("app.title"),
                content=message,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000,
                parent=self,
            )
        else:
            message = i18n.t("dashboard.collected", count=len(records))
            self.status_label.setText(message)
            InfoBar.success(
                title=i18n.t("app.title"),
                content=message,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2500,
                parent=self,
            )

    def _on_collection_failed(self, message: str) -> None:
        text = i18n.t("dashboard.collect_error", message=message)
        self.status_label.setText(text)
        InfoBar.error(
            title=i18n.t("app.title"),
            content=text,
            position=InfoBarPosition.TOP_RIGHT,
            duration=5000,
            parent=self,
        )

    def _open_diagnostics(self) -> None:
        result = diagnostics.run_diagnostics(self._data_dir)
        report = diagnostics.format_report(result)
        box = MessageBox(i18n.t("diagnostics.title"), report, self.window())
        box.cancelButton.hide()
        box.exec()

    # -- 상세 스캔 영역 --------------------------------------------------
    def _selected_account(self) -> Optional[config_module.Account]:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._config.accounts):
            return None
        return self._config.accounts[row]

    def _refresh_scan_section(self) -> None:
        """스캔 상태를 DB에서 읽어 표시한다 (읽기 전용)."""

        try:
            snapshot = nightly_scan.get_status_snapshot(self._data_dir, self._config)
        except Exception as exc:  # pragma: no cover - 방어적 처리
            self.scan_status_label.setText(i18n.t("scan.status_error", message=exc))
            return

        self._scan_snapshot = snapshot
        running = snapshot.is_running or self._scan_worker.is_running()

        parts = [snapshot.window_description]
        parts.append(i18n.t("scan.running") if running else i18n.t("scan.not_running"))
        latest = snapshot.latest_run
        if latest:
            parts.append(
                i18n.t(
                    "scan.latest_run",
                    status=latest["status"],
                    started_at=latest["started_at"][:19],
                )
            )
        pending_total = sum(
            item.pending_baseline_count + item.pending_activity_count
            for item in snapshot.accounts
        )
        if pending_total:
            # 퍼센트로 부풀리지 않고 남은 작업 수 그대로 보여준다.
            parts.append(i18n.t("scan.pending_tasks", count=pending_total))
        self.scan_status_label.setText("  |  ".join(parts))

        self.scan_run_btn.setEnabled(not running)
        self.scan_stop_btn.setEnabled(running)
        self._refresh_growth_table()

    def _refresh_growth_table(self) -> None:
        snapshot = self._scan_snapshot
        account = self._selected_account()
        if snapshot is None or account is None:
            self.growth_table.setRowCount(0)
            self.growth_caption.setText(i18n.t("scan.select_account"))
            return

        entry = next(
            (item for item in snapshot.accounts if item.account_id == account.account_id),
            None,
        )
        if entry is None or entry.last_completed_generation is None:
            self.growth_table.setRowCount(0)
            self.growth_caption.setText(i18n.t("scan.no_baseline", account=account.name))
            return

        activity_note = ""
        if entry.last_activity_total_changed is not None:
            activity_note = i18n.t(
                "scan.activity_note", count=entry.last_activity_total_changed
            )
        # 권한 부족으로 축소 측정된 경로가 있으면 반드시 알린다.
        if entry.partial_paths:
            activity_note += "\n" + i18n.t(
                "scan.partial_warning", count=len(entry.partial_paths)
            )

        if entry.growth:
            self.growth_caption.setText(
                i18n.t(
                    "scan.growth_caption",
                    account=account.name,
                    generation=entry.last_completed_generation,
                    activity=activity_note,
                )
            )
            self.growth_table.setRowCount(len(entry.growth))
            for index, row in enumerate(entry.growth):
                current_kb = row["current_kb"]
                previous_kb = row["previous_kb"]
                self.growth_table.setItem(index, 0, QTableWidgetItem(row["path"]))
                self.growth_table.setItem(
                    index, 1, QTableWidgetItem(widgets.format_kb(current_kb))
                )
                if previous_kb is None:
                    delta_text = i18n.t("scan.new_path")
                else:
                    delta_text = widgets.format_kb_delta(current_kb - previous_kb)
                self.growth_table.setItem(index, 2, QTableWidgetItem(delta_text))
            return

        self.growth_caption.setText(
            i18n.t(
                "scan.baseline_only_caption",
                account=account.name,
                generation=entry.last_completed_generation,
                activity=activity_note,
            )
        )
        self.growth_table.setRowCount(len(entry.top_paths))
        dash = i18n.t("common.none")
        for index, row in enumerate(entry.top_paths):
            self.growth_table.setItem(index, 0, QTableWidgetItem(row["path"]))
            self.growth_table.setItem(
                index, 1, QTableWidgetItem(widgets.format_kb(row["size_kb"]))
            )
            self.growth_table.setItem(index, 2, QTableWidgetItem(dash))

    def _trigger_scan_now(self) -> None:
        if not self._config.accounts:
            InfoBar.warning(
                title=i18n.t("accounts.none_selected_title"),
                content=i18n.t("accounts.none_selected_body"),
                position=InfoBarPosition.TOP_RIGHT,
                parent=self,
            )
            return
        box = MessageBox(
            i18n.t("scan.confirm_title"), i18n.t("scan.confirm_body"), self.window()
        )
        if not box.exec():
            return
        if not self._scan_worker.run_async(bypass_window=True):
            self.status_label.setText(i18n.t("scan.already_running"))
            return
        self.status_label.setText(i18n.t("scan.started"))
        self._refresh_scan_section()

    def _request_scan_stop(self) -> None:
        if self._scan_worker.request_stop():
            self.status_label.setText(i18n.t("scan.stop_requested"))
        else:
            self.status_label.setText(i18n.t("scan.nothing_running"))
        self._refresh_scan_section()

    def _on_scan_finished(self, summary) -> None:
        if not summary.started:
            text = i18n.t("scan.not_started", reason=summary.reason)
        else:
            text = i18n.t("scan.finished", status=summary.status)
        self.status_label.setText(text)
        InfoBar.success(
            title=i18n.t("scan.section_title"),
            content=text,
            position=InfoBarPosition.TOP_RIGHT,
            duration=4000,
            parent=self,
        )
        self._refresh_scan_section()

    def _on_scan_failed(self, message: str) -> None:
        text = i18n.t("scan.failed", message=message)
        self.status_label.setText(text)
        InfoBar.error(
            title=i18n.t("scan.section_title"),
            content=text,
            position=InfoBarPosition.TOP_RIGHT,
            duration=5000,
            parent=self,
        )
        self._refresh_scan_section()

    def shutdown(self) -> None:
        """창을 닫을 때 호출. 실행 중인 스캔은 강제 종료하지 않고 안전 중지를
        요청해 둔다 (체크포인트를 남기고 스스로 멈추게 한다)."""

        self._scheduler.stop()
        self._scan_status_timer.stop()
        if self._scan_worker.is_running():
            self._scan_worker.request_stop()
