"""대시보드 단일 화면 (GUI의 중심).

DESIGN.md 2부 6절 결정 사항을 반영:
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
from PyQt5.QtGui import QBrush, QColor
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
from .. import diagnostics, forecast_notify, freshness, i18n, nightly_scan, quota, store, tiers
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

# 요약 바는 글자만 물들이지 않고 배너로 만든다 - 창을 열자마자 시선이 먼저
# 닿는 자리이므로, 상태가 색면으로 보이는 편이 판단이 빠르다.
SUMMARY_STYLE = (
    "font-size: 14pt; font-weight: bold; padding: 10px 12px;"
    "border-radius: 6px; border: 1px solid {border}; background: {background}; color: {text};"
)

# 등급별 배너 배색 (배경, 테두리). 글자는 등급 기준색을 쓴다.
BANNER_COLORS = {
    tiers.NORMAL: ("#e8f5e9", "#a5d6a7"),
    tiers.WARN: ("#fff8e1", "#ffe082"),
    tiers.ALERT: ("#fff3e0", "#ffcc80"),
    tiers.EMERGENCY: ("#ffebee", "#ef9a9a"),
    tiers.FULL: ("#f3e5f5", "#ce93d8"),
    tiers.UNKNOWN: ("#f5f5f5", "#e0e0e0"),
}

SECTION_TITLE_STYLE = (
    "font-weight: bold; padding: 4px 0; color: #37474f;"
    "border-bottom: 2px solid #cfd8dc;"
)

PRIMARY_BUTTON_STYLE = (
    "QPushButton { background: #1565c0; color: white; border: none;"
    " padding: 6px 14px; border-radius: 4px; font-weight: bold; }"
    "QPushButton:hover { background: #1976d2; }"
    "QPushButton:disabled { background: #b0bec5; color: #eceff1; }"
)

DANGER_BUTTON_STYLE = (
    "QPushButton { background: #fff; color: #c62828; border: 1px solid #ef9a9a;"
    " padding: 6px 14px; border-radius: 4px; }"
    "QPushButton:hover { background: #ffebee; }"
    "QPushButton:disabled { color: #bdbdbd; border-color: #e0e0e0; }"
)

TABLE_STYLE = (
    "QTableWidget { gridline-color: #eceff1; selection-background-color: #e3f2fd;"
    " selection-color: #0d47a1; }"
    "QHeaderView::section { background: #eceff1; padding: 5px;"
    " border: none; border-right: 1px solid #cfd8dc; font-weight: bold; color: #37474f; }"
)


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path, config: config_module.AppConfig):
        super().__init__()
        self._data_dir = data_dir
        self._config = config
        self._latest_samples: Dict[str, store.SampleRecord] = {}
        self._forecasts: Dict[str, object] = {}
        self._freshness: Dict[str, object] = {}
        self._scan_snapshot = None
        i18n.set_language(config.settings.language)

        self.resize(1040, 640)
        self._build_ui()
        self.retranslate()

        # 수집 신선도는 **첫 수집을 돌리기 전에** 판정해야 한다. GUI를 열면
        # 스케줄러가 곧바로 한 번 수집하므로, 그 뒤에 보면 늘 "방금 수집됨"이
        # 되어 "그동안 cron이 안 돌았다"는 사실이 가려진다.
        self._freshness = self._evaluate_freshness()
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
        마다 모달이 뜨면 곤란하기 때문. 호출은 `smvwp_cli.py gui`가 `show()` 뒤에 한다."""

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
        # 가장 자주 쓰는 동작 하나만 강조한다 - 전부 강조하면 아무것도 강조되지
        # 않는다.
        self.collect_btn.setStyleSheet(PRIMARY_BUTTON_STYLE)
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
        self.table.setAlternatingRowColors(False)  # 등급 색과 겹치면 판독을 방해한다
        self.table.setStyleSheet(TABLE_STYLE)
        self.table.verticalHeader().setVisible(False)
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
        붙인다 (DESIGN.md 2부 6절 "대시보드 단일 화면" 결정 유지)."""

        section = QFrame()
        section.setFrameShape(QFrame.StyledPanel)
        section.setStyleSheet(
            "QFrame { background: #fafafa; border: 1px solid #e0e0e0; border-radius: 6px; }"
        )
        box = QVBoxLayout(section)

        self.scan_title_label = QLabel()
        self.scan_title_label.setStyleSheet(SECTION_TITLE_STYLE)
        box.addWidget(self.scan_title_label)

        self.scan_status_label = QLabel()
        self.scan_status_label.setStyleSheet("color: #424242;")
        self.scan_status_label.setWordWrap(True)
        box.addWidget(self.scan_status_label)

        scan_buttons = QHBoxLayout()
        self.scan_run_btn = QPushButton()
        self.scan_run_btn.clicked.connect(self._trigger_scan_now)
        self.scan_stop_btn = QPushButton()
        # 중지는 되돌릴 수 없는 성격의 동작이라 색으로 구분해 둔다 (강제 종료는
        # 아니지만, 실수로 누르면 진행 중인 밤을 날린다).
        self.scan_stop_btn.setStyleSheet(DANGER_BUTTON_STYLE)
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
        """요약줄에 띄울 수집 경고. 없으면 None."""

        problems = [f for f in self._freshness.values() if f.needs_attention]
        if not problems:
            return None

        stalled = [f for f in problems if f.status in (freshness.STATUS_STALE, freshness.STATUS_NEVER)]
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

            # 주의 이상인 행만 옅게 칠한다. 정상까지 칠하면 색이 배경 소음이
            # 되어 정작 문제 있는 행이 묻힌다.
            self._tint_row(row, sample.overall_tier if sample.ok else tiers.UNKNOWN)

            if sample.ok:
                if tiers.is_at_least(sample.overall_tier, "warn"):
                    warn_or_worse_count += 1
                if tiers.severity(sample.overall_tier) > tiers.severity(worst_tier):
                    worst_tier = sample.overall_tier
                    worst_account_label = (
                        f"{account.name} ({tiers.display_text(sample.overall_tier, sample.byte_pct)})"
                    )

        self._update_summary(warn_or_worse_count, worst_account_label, worst_tier)

    def _tint_row(self, row: int, tier: str) -> None:
        """행 배경을 등급 색으로 옅게 칠한다 (정상/확인불가는 칠하지 않음).

        등급은 배지 텍스트로도 보이므로, 색을 못 보는 환경에서도 정보가
        사라지지 않는다 - 색은 어디를 먼저 볼지 알려주는 보조 수단이다."""

        background = tiers.row_background(tier)
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
            self.summary_label.setStyleSheet(SUMMARY_STYLE + " color: #757575;")
            return

        banner_tier = worst_tier if warn_or_worse_count else tiers.NORMAL
        if warn_or_worse_count == 0:
            text = i18n.t("dashboard.all_normal", count=len(self._config.accounts))
        else:
            text = i18n.t(
                "dashboard.warn_summary", count=warn_or_worse_count, worst=worst_account_label
            )

        # 수집이 멈춰 있으면 사용률 요약보다 그게 먼저다 - 멈춘 데이터를 보고
        # "정상"이라고 판단하는 것이 가장 위험하다.
        warning = self._freshness_warning()
        if warning:
            text = f"{warning}\n{text}"
            banner_tier = tiers.worse(banner_tier, tiers.ALERT)
            self.summary_label.setWordWrap(True)

        self.summary_label.setText(text)
        self._apply_banner(banner_tier)

    def _apply_banner(self, tier: str) -> None:
        background, border = BANNER_COLORS.get(tier, BANNER_COLORS[tiers.UNKNOWN])
        self.summary_label.setStyleSheet(
            SUMMARY_STYLE.format(
                background=background, border=border, text=tiers.color(tier)
            )
        )

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
            # 아직 모르기 때문 (DESIGN.md 1부 "과장하지 않는 UI").
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
        # 권한 부족으로 축소 측정된 경로가 있으면 반드시 알린다 - 모르고 보면
        # "안 늘었네"로 잘못 읽는다.
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
