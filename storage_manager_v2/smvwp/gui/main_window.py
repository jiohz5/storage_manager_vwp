"""대시보드 단일 화면 (GUI의 중심).

REBUILD_CONCEPT.md 6절 결정 사항을 반영:
- 화면 구조: 대시보드 단일 화면 (탭 없음). 계정 등록/설정은 다이얼로그.
- "한눈 요약 우선" (1차 채택): 최상단에 "경고 이상 계정 수 / 가장 급한 계정"
  요약을 배치해 표를 스크롤/정렬하지 않아도 위험 상태를 바로 파악할 수 있게
  한다.
- 상세 스캔(야간 du/find) 영역도 탭을 새로 만들지 않고 같은 화면 아래쪽에
  붙인다.

모든 표시 문자열은 `i18n.t`를 거친다. 언어를 바꾸면 `retranslate()`가 정적
문자열을, 이어지는 갱신이 동적 문자열을 다시 그린다 - 앱을 다시 시작할 필요가
없다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_module
from .. import diagnostics, forecast_notify, i18n, nightly_scan, quota, store, tiers
from ..scheduler import CollectorScheduler, NightlyScanWorker
from . import widgets
from .account_dialog import AccountDialog
from .first_run import FirstRunDialog
from .reports_dialog import ReportsDialog
from .search_dialog import SearchDialog

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

# 스캔이 도는 동안 진행 상황(남은 체크포인트 수)을 주기적으로 다시 읽는 간격.
SCAN_STATUS_REFRESH_MS = 5000

SUMMARY_STYLE = "font-size: 14pt; font-weight: bold; padding: 6px;"


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path, config: config_module.AppConfig):
        super().__init__()
        self._data_dir = data_dir
        self._config = config
        self._latest_samples: Dict[str, store.SampleRecord] = {}
        self._forecasts: Dict[str, object] = {}
        self._scan_snapshot = None
        i18n.set_language(config.settings.language)

        self.resize(1040, 640)
        self._build_ui()
        self.retranslate()
        self._refresh_table_from_store()

        self._scheduler = CollectorScheduler(
            data_dir,
            get_config=lambda: self._config,
            on_finished=self._on_collection_finished,
            on_failed=self._on_collection_failed,
        )
        self._scheduler.start(run_immediately=True)

        # 야간 상세 스캔 - GUI에서 수동 실행/중지할 수 있게만 하고, 정규 실행은
        # cron(`setup_cron.csh`)이 22:00에 띄운다.
        self._scan_worker = NightlyScanWorker(data_dir, get_config=lambda: self._config)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.failed.connect(self._on_scan_failed)

        self._scan_status_timer = QTimer(self)
        self._scan_status_timer.timeout.connect(self._refresh_scan_section)
        self._scan_status_timer.start(SCAN_STATUS_REFRESH_MS)
        self._refresh_scan_section()

    def show_first_run_if_needed(self) -> None:
        """계정이 하나도 없으면 시작 안내를 띄운다.

        생성자가 아니라 별도 메서드로 둔 이유: 창이 화면에 뜨기 전에 모달
        다이얼로그를 띄우면 부모 없는 창처럼 보이고, 테스트에서도 창을 만들 때
        마다 모달이 뜨면 곤란하기 때문. 호출은 `app.py`가 `show()` 뒤에 한다."""

        if self._config.accounts:
            return
        dialog = FirstRunDialog(self._data_dir, self._config, parent=self)
        dialog.exec_()
        if dialog.open_accounts:
            self._open_account_dialog()

    # -- UI 구성 -----------------------------------------------------
    def _build_ui(self) -> None:
        self._build_menu()

        central = QWidget()
        layout = QVBoxLayout(central)

        # 한눈 요약 바
        self.summary_label = QLabel()
        self.summary_label.setStyleSheet(SUMMARY_STYLE)
        layout.addWidget(self.summary_label)

        self.caveat_label = QLabel()
        self.caveat_label.setStyleSheet("color: #757575; font-size: 9pt;")
        self.caveat_label.setWordWrap(True)
        layout.addWidget(self.caveat_label)

        button_row = QHBoxLayout()
        self.collect_btn = QPushButton()
        self.collect_btn.clicked.connect(self._trigger_now)
        self.accounts_btn = QPushButton()
        self.accounts_btn.clicked.connect(self._open_account_dialog)
        self.reports_btn = QPushButton()
        self.reports_btn.clicked.connect(self._open_reports_dialog)
        self.search_btn = QPushButton()
        self.search_btn.clicked.connect(self._open_search_dialog)
        self.diagnose_btn = QPushButton()
        self.diagnose_btn.clicked.connect(self._open_diagnostics)
        for button in (
            self.collect_btn,
            self.accounts_btn,
            self.reports_btn,
            self.search_btn,
            self.diagnose_btn,
        ):
            button_row.addWidget(button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.table = QTableWidget(0, len(COLUMN_KEYS))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._refresh_growth_table)
        layout.addWidget(self.table, 3)

        layout.addWidget(self._build_scan_section(), 2)

        self.status_bar_label = QLabel("")
        self.statusBar().addPermanentWidget(self.status_bar_label)

        self.setCentralWidget(central)

    def _build_menu(self) -> None:
        """언어 메뉴 - 즉시 전환된다 (재시작 불필요)."""

        self.language_menu = self.menuBar().addMenu("")
        self._language_actions = {}
        group = QActionGroup(self)
        group.setExclusive(True)
        for code in i18n.available_languages():
            action = QAction(i18n.language_name(code), self, checkable=True)
            action.setChecked(code == i18n.get_language())
            action.triggered.connect(lambda _checked, c=code: self._change_language(c))
            group.addAction(action)
            self.language_menu.addAction(action)
            self._language_actions[code] = action

    def _build_scan_section(self) -> QWidget:
        """야간 상세 스캔 영역 - 탭을 새로 만들지 않고 같은 화면 아래쪽에
        붙인다 (REBUILD_CONCEPT.md 6절 "대시보드 단일 화면" 결정 유지)."""

        section = QFrame()
        section.setFrameShape(QFrame.StyledPanel)
        box = QVBoxLayout(section)

        self.scan_title_label = QLabel()
        self.scan_title_label.setStyleSheet("font-weight: bold; padding-top: 2px;")
        box.addWidget(self.scan_title_label)

        self.scan_status_label = QLabel()
        self.scan_status_label.setStyleSheet("color: #424242;")
        self.scan_status_label.setWordWrap(True)
        box.addWidget(self.scan_status_label)

        scan_buttons = QHBoxLayout()
        self.scan_run_btn = QPushButton()
        self.scan_run_btn.clicked.connect(self._trigger_scan_now)
        self.scan_stop_btn = QPushButton()
        self.scan_stop_btn.clicked.connect(self._request_scan_stop)
        scan_buttons.addWidget(self.scan_run_btn)
        scan_buttons.addWidget(self.scan_stop_btn)
        scan_buttons.addStretch(1)
        box.addLayout(scan_buttons)

        self.growth_caption = QLabel()
        self.growth_caption.setStyleSheet("color: #757575; font-size: 9pt;")
        self.growth_caption.setWordWrap(True)
        box.addWidget(self.growth_caption)

        self.growth_table = QTableWidget(0, len(GROWTH_COLUMN_KEYS))
        self.growth_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.growth_table.setEditTriggers(QTableWidget.NoEditTriggers)
        box.addWidget(self.growth_table)

        return section

    # -- 다국어 ------------------------------------------------------
    def retranslate(self) -> None:
        """언어에 따라 달라지는 정적 문자열을 다시 채운다. 동적 문자열(표 내용,
        상태 줄)은 이어지는 갱신 호출이 알아서 다시 그린다."""

        self.setWindowTitle(i18n.t("app.title"))
        self.language_menu.setTitle(i18n.t("menu.language"))
        self.caveat_label.setText(i18n.t("dashboard.df_caveat"))
        self.collect_btn.setText(i18n.t("dashboard.btn.collect_now"))
        self.accounts_btn.setText(i18n.t("dashboard.btn.accounts"))
        self.reports_btn.setText(i18n.t("dashboard.btn.reports"))
        self.search_btn.setText(i18n.t("dashboard.btn.search"))
        self.diagnose_btn.setText(i18n.t("dashboard.btn.diagnose"))
        self.table.setHorizontalHeaderLabels([i18n.t(key) for key in COLUMN_KEYS])

        self.scan_title_label.setText(i18n.t("scan.section_title"))
        self.scan_run_btn.setText(i18n.t("scan.btn.run_now"))
        self.scan_run_btn.setToolTip(i18n.t("scan.btn.run_now_tooltip"))
        self.scan_stop_btn.setText(i18n.t("scan.btn.stop"))
        self.scan_stop_btn.setToolTip(i18n.t("scan.btn.stop_tooltip"))
        self.growth_table.setHorizontalHeaderLabels([i18n.t(key) for key in GROWTH_COLUMN_KEYS])

    def _change_language(self, language: str) -> None:
        if language == i18n.get_language():
            return
        i18n.set_language(language)
        self._config.settings.language = language
        try:
            config_module.save_config(self._data_dir, self._config)
        except config_module.ConfigError:
            # 저장에 실패해도 이번 세션의 화면 전환은 그대로 진행한다 (표시
            # 설정일 뿐이라 데이터 무결성과 무관).
            pass
        for code, action in self._language_actions.items():
            action.setChecked(code == language)
        self.retranslate()
        self._render_table(self._latest_samples)
        self._refresh_scan_section()

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
                self.table.setItem(row, COL_STATUS, QTableWidgetItem(i18n.t("dashboard.not_collected")))
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

            self.table.setCellWidget(row, COL_TIER, widgets.TierBadge(sample.overall_tier, sample.byte_pct))

            forecast = self._forecasts.get(account.account_id)
            forecast_item = QTableWidgetItem(widgets.format_forecast_cell(forecast))
            forecast_item.setToolTip(
                widgets.format_forecast_tooltip(
                    forecast, self._config.settings.full_prediction_window_hours
                )
            )
            # 임박했으면 등급 색으로 눈에 띄게 한다.
            if forecast is not None and forecast.imminent.ok:
                imminent_tier = forecast_notify.forecast_tier(
                    forecast.imminent.hours_to_full, self._config.settings
                )
                if imminent_tier is not None:
                    forecast_item.setForeground(QColor(tiers.color(imminent_tier)))
            self.table.setItem(row, COL_FORECAST, forecast_item)

            self.table.setItem(row, COL_TIME, QTableWidgetItem(sample.collected_at))
            status_text = (
                i18n.t("dashboard.collect_ok")
                if sample.ok
                else i18n.t("dashboard.collect_failed", message=sample.error_message)
            )
            self.table.setItem(row, COL_STATUS, QTableWidgetItem(status_text))

            if sample.ok:
                if tiers.is_at_least(sample.overall_tier, "warn"):
                    warn_or_worse_count += 1
                if tiers.severity(sample.overall_tier) > tiers.severity(worst_tier):
                    worst_tier = sample.overall_tier
                    worst_account_label = (
                        f"{account.name} ({tiers.display_text(sample.overall_tier, sample.byte_pct)})"
                    )

        self._update_summary(warn_or_worse_count, worst_account_label, worst_tier)

    def _update_summary(
        self, warn_or_worse_count: int, worst_account_label: Optional[str], worst_tier: str
    ) -> None:
        if not self._config.accounts:
            self.summary_label.setText(i18n.t("dashboard.no_accounts"))
            self.summary_label.setStyleSheet(SUMMARY_STYLE + " color: #757575;")
            return

        color = tiers.color(worst_tier if warn_or_worse_count else tiers.NORMAL)
        if warn_or_worse_count == 0:
            text = i18n.t("dashboard.all_normal", count=len(self._config.accounts))
        else:
            text = i18n.t(
                "dashboard.warn_summary", count=warn_or_worse_count, worst=worst_account_label
            )
        self.summary_label.setText(text)
        self.summary_label.setStyleSheet(f"{SUMMARY_STYLE} color: {color};")

    # -- 이벤트 핸들러 --------------------------------------------------
    def _trigger_now(self) -> None:
        self.status_bar_label.setText(i18n.t("dashboard.collecting"))
        self._scheduler.trigger_now()

    def _on_collection_finished(self, records: List[store.SampleRecord]) -> None:
        self._refresh_table_from_store()
        failed = sum(1 for r in records if not r.ok)
        if failed:
            self.status_bar_label.setText(
                i18n.t("dashboard.collected_with_failures", count=len(records), failed=failed)
            )
        else:
            self.status_bar_label.setText(i18n.t("dashboard.collected", count=len(records)))

    def _on_collection_failed(self, message: str) -> None:
        self.status_bar_label.setText(i18n.t("dashboard.collect_error", message=message))

    def _open_account_dialog(self) -> None:
        dialog = AccountDialog(self._data_dir, self._config, parent=self)
        if dialog.exec_():
            self._config = config_module.load_config(self._data_dir)
            i18n.set_language(self._config.settings.language)
            for code, action in self._language_actions.items():
                action.setChecked(code == i18n.get_language())
            self.retranslate()
            self._refresh_table_from_store()
            self._refresh_scan_section()
            self._scheduler.restart_with_current_interval()

    def _open_reports_dialog(self) -> None:
        ReportsDialog(self._data_dir, self._config, parent=self).exec_()

    def _open_search_dialog(self) -> None:
        SearchDialog(self._data_dir, self._config, parent=self).exec_()

    def _open_diagnostics(self) -> None:
        result = diagnostics.run_diagnostics(self._data_dir)
        report = diagnostics.format_report(result)
        box = QMessageBox(self)
        box.setWindowTitle(i18n.t("diagnostics.title"))
        box.setIcon(QMessageBox.Information if result["ok"] else QMessageBox.Warning)
        box.setText(report)
        box.exec_()

    # -- 상세 스캔 영역 --------------------------------------------------
    def _selected_account(self) -> Optional[config_module.Account]:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._config.accounts):
            return None
        return self._config.accounts[row]

    def _refresh_scan_section(self) -> None:
        """스캔 상태를 DB에서 읽어 표시한다 (읽기 전용 - 여기서 스캔을 돌리지
        않는다)."""

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
                i18n.t("scan.latest_run", status=latest["status"], started_at=latest["started_at"][:19])
            )
        pending_total = sum(
            item.pending_baseline_count + item.pending_activity_count for item in snapshot.accounts
        )
        if pending_total:
            # 퍼센트로 부풀리지 않고 남은 작업 수 그대로 보여준다 - 전체 분모를
            # 아직 모르기 때문 (CONCEPT.md "과장하지 않는 UI").
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
            (item for item in snapshot.accounts if item.account_id == account.account_id), None
        )
        if entry is None or entry.last_completed_generation is None:
            self.growth_table.setRowCount(0)
            self.growth_caption.setText(i18n.t("scan.no_baseline", account=account.name))
            return

        activity_note = ""
        if entry.last_activity_total_changed is not None:
            activity_note = i18n.t("scan.activity_note", count=entry.last_activity_total_changed)

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
                self.growth_table.setItem(index, 1, QTableWidgetItem(widgets.format_kb(current_kb)))
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
            self.growth_table.setItem(index, 1, QTableWidgetItem(widgets.format_kb(row["size_kb"])))
            self.growth_table.setItem(index, 2, QTableWidgetItem(dash))

    def _trigger_scan_now(self) -> None:
        if not self._config.accounts:
            QMessageBox.information(
                self,
                i18n.t("accounts.none_selected_title"),
                i18n.t("accounts.none_selected_body"),
            )
            return
        reply = QMessageBox.question(
            self,
            i18n.t("scan.confirm_title"),
            i18n.t("scan.confirm_body"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if not self._scan_worker.run_async(bypass_window=True):
            self.status_bar_label.setText(i18n.t("scan.already_running"))
            return
        self.status_bar_label.setText(i18n.t("scan.started"))
        self._refresh_scan_section()

    def _request_scan_stop(self) -> None:
        if self._scan_worker.request_stop():
            self.status_bar_label.setText(i18n.t("scan.stop_requested"))
        else:
            self.status_bar_label.setText(i18n.t("scan.nothing_running"))
        self._refresh_scan_section()

    def _on_scan_finished(self, summary) -> None:
        if not summary.started:
            self.status_bar_label.setText(i18n.t("scan.not_started", reason=summary.reason))
        else:
            self.status_bar_label.setText(i18n.t("scan.finished", status=summary.status))
        self._refresh_scan_section()

    def _on_scan_failed(self, message: str) -> None:
        self.status_bar_label.setText(i18n.t("scan.failed", message=message))
        self._refresh_scan_section()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 콜백 이름 규칙)
        self._scheduler.stop()
        self._scan_status_timer.stop()
        # 실행 중인 상세 스캔이 있으면 안전 중지를 요청해 둔다 - 창을 닫는다고
        # 강제로 죽이지는 않는다 (체크포인트를 남기고 스스로 멈추게 한다).
        if self._scan_worker.is_running():
            self._scan_worker.request_stop()
        super().closeEvent(event)
