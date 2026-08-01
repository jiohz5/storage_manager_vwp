"""대시보드 단일 화면 (phase 1 GUI의 중심).

REBUILD_CONCEPT.md 6절 결정 사항을 반영:
- 화면 구조: 대시보드 단일 화면 (탭 없음). 계정 등록/설정은 다이얼로그.
- "한눈 요약 우선" (1차 채택): 최상단에 "경고 이상 계정 수 / 가장 급한 계정"
  요약을 배치해 표를 스크롤/정렬하지 않아도 위험 상태를 바로 파악할 수 있게
  한다.
- 이후 후보(최초 실행 마법사, 상태 통합, 클릭 수 최소화 등)는 지금 만들지
  않지만, 이 창 구조가 그런 확장을 막지 않도록 신경 썼다 (예: 요약 바/버튼
  영역을 별도 위젯으로 분리해 두면 나중에 "지금 스캔" 같은 버튼을 추가하기
  쉽다).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
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
from .. import diagnostics, nightly_scan, paths, store, tiers
from ..scheduler import CollectorScheduler, NightlyScanWorker
from . import widgets
from .account_dialog import AccountDialog

# df가 보고하는 값은 계정 자체가 아니라 그 경로가 속한 파일시스템 전체
# 사용량이라는 점을 항상 화면에 명시한다 (CONCEPT.md 1절).
DF_CAVEAT_TEXT = (
    "※ 사용률은 계정 경로가 속한 파일시스템 전체 사용량 기준입니다 "
    "(df 특성상 계정 단독 사용량이 아닙니다)."
)

COLUMNS = ["이름", "경로", "파일시스템", "용량 사용률", "inode 사용률", "종합 등급", "최근 수집", "상태"]

GROWTH_COLUMNS = ["경로", "현재 크기", "이전 세대 대비"]

# 스캔이 도는 동안 진행 상황(남은 체크포인트 수)을 주기적으로 다시 읽는 간격.
SCAN_STATUS_REFRESH_MS = 5000


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path, config: config_module.AppConfig):
        super().__init__()
        self._data_dir = data_dir
        self._config = config
        self.setWindowTitle("Storage Manager VWP")
        self.resize(980, 560)

        self._build_ui()
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

    # -- UI 구성 -----------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)

        # 한눈 요약 바
        self.summary_label = QLabel("수집 대기 중...")
        self.summary_label.setStyleSheet("font-size: 14pt; font-weight: bold; padding: 6px;")
        layout.addWidget(self.summary_label)

        caveat = QLabel(DF_CAVEAT_TEXT)
        caveat.setStyleSheet("color: #757575; font-size: 9pt;")
        layout.addWidget(caveat)

        button_row = QHBoxLayout()
        refresh_btn = QPushButton("지금 수집")
        refresh_btn.clicked.connect(self._trigger_now)
        manage_btn = QPushButton("계정 관리 / 설정...")
        manage_btn.clicked.connect(self._open_account_dialog)
        diagnose_btn = QPushButton("진단...")
        diagnose_btn.clicked.connect(self._open_diagnostics)
        button_row.addWidget(refresh_btn)
        button_row.addWidget(manage_btn)
        button_row.addWidget(diagnose_btn)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._refresh_growth_table)
        layout.addWidget(self.table, 3)

        layout.addWidget(self._build_scan_section(), 2)

        self.status_bar_label = QLabel("")
        self.statusBar().addPermanentWidget(self.status_bar_label)

        self.setCentralWidget(central)

    def _build_scan_section(self) -> QWidget:
        """야간 상세 스캔 영역 - 탭을 새로 만들지 않고 같은 화면 아래쪽에
        붙인다 (REBUILD_CONCEPT.md 6절 "대시보드 단일 화면" 결정 유지)."""

        section = QFrame()
        section.setFrameShape(QFrame.StyledPanel)
        box = QVBoxLayout(section)

        title = QLabel("상세 스캔 (야간 du/find 기반 증가 경로)")
        title.setStyleSheet("font-weight: bold; padding-top: 2px;")
        box.addWidget(title)

        self.scan_status_label = QLabel("스캔 상태 확인 중...")
        self.scan_status_label.setStyleSheet("color: #424242;")
        self.scan_status_label.setWordWrap(True)
        box.addWidget(self.scan_status_label)

        scan_buttons = QHBoxLayout()
        self.scan_run_btn = QPushButton("지금 상세 스캔 실행")
        self.scan_run_btn.setToolTip(
            "시간창(22:00~06:00)과 무관하게 지금 실행합니다. 대상 파일시스템에 "
            "부하를 줄 수 있으므로 업무 시간에는 주의해서 사용하세요."
        )
        self.scan_run_btn.clicked.connect(self._trigger_scan_now)
        self.scan_stop_btn = QPushButton("안전 중지")
        self.scan_stop_btn.setToolTip(
            "실행 중인 스캔에 중지를 요청합니다. 강제 종료가 아니라 다음 "
            "체크포인트에서 스스로 멈추고, 완료한 작업은 그대로 보존됩니다."
        )
        self.scan_stop_btn.clicked.connect(self._request_scan_stop)
        scan_buttons.addWidget(self.scan_run_btn)
        scan_buttons.addWidget(self.scan_stop_btn)
        scan_buttons.addStretch(1)
        box.addLayout(scan_buttons)

        self.growth_caption = QLabel("계정을 선택하면 증가 경로가 표시됩니다.")
        self.growth_caption.setStyleSheet("color: #757575; font-size: 9pt;")
        box.addWidget(self.growth_caption)

        self.growth_table = QTableWidget(0, len(GROWTH_COLUMNS))
        self.growth_table.setHorizontalHeaderLabels(GROWTH_COLUMNS)
        self.growth_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.growth_table.setEditTriggers(QTableWidget.NoEditTriggers)
        box.addWidget(self.growth_table)

        return section

    # -- 데이터 갱신 ----------------------------------------------------
    def _refresh_table_from_store(self) -> None:
        conn = store.connect(self._data_dir)
        try:
            latest = store.latest_samples(conn)
        finally:
            conn.close()
        self._render_table(latest)

    def _render_table(self, latest: Dict[str, store.SampleRecord]) -> None:
        accounts = self._config.accounts
        self.table.setRowCount(len(accounts))

        worst_tier = tiers.NORMAL
        worst_account_label: Optional[str] = None
        warn_or_worse_count = 0

        for row, account in enumerate(accounts):
            sample = latest.get(account.account_id)
            self.table.setItem(row, 0, QTableWidgetItem(account.name))
            self.table.setItem(row, 1, QTableWidgetItem(account.path))

            if sample is None:
                self.table.setItem(row, 2, QTableWidgetItem("-"))
                self.table.setItem(row, 3, QTableWidgetItem("-"))
                self.table.setItem(row, 4, QTableWidgetItem("-"))
                badge = widgets.TierBadge(tiers.UNKNOWN, None)
                self.table.setCellWidget(row, 5, badge)
                self.table.setItem(row, 6, QTableWidgetItem("-"))
                self.table.setItem(row, 7, QTableWidgetItem("아직 수집되지 않음"))
                continue

            self.table.setItem(row, 2, QTableWidgetItem(sample.filesystem or "-"))
            byte_text = f"{sample.byte_pct:.1f}%" if sample.byte_pct is not None else "-"
            inode_text = f"{sample.inode_pct:.1f}%" if sample.inode_pct is not None else "확인불가"
            self.table.setItem(row, 3, QTableWidgetItem(byte_text))
            self.table.setItem(row, 4, QTableWidgetItem(inode_text))

            badge = widgets.TierBadge(sample.overall_tier, sample.byte_pct)
            self.table.setCellWidget(row, 5, badge)

            self.table.setItem(row, 6, QTableWidgetItem(sample.collected_at))
            status_text = "정상 수집" if sample.ok else f"수집 실패: {sample.error_message}"
            self.table.setItem(row, 7, QTableWidgetItem(status_text))

            if sample.ok:
                if tiers.is_at_least(sample.overall_tier, "warn"):
                    warn_or_worse_count += 1
                if tiers.severity(sample.overall_tier) > tiers.severity(worst_tier):
                    worst_tier = sample.overall_tier
                    worst_account_label = f"{account.name} ({tiers.display_text(sample.overall_tier, sample.byte_pct)})"

        self._update_summary(warn_or_worse_count, worst_account_label, worst_tier)

    def _update_summary(self, warn_or_worse_count: int, worst_account_label: Optional[str], worst_tier: str) -> None:
        if not self._config.accounts:
            self.summary_label.setText("등록된 계정이 없습니다. '계정 관리'에서 추가하세요.")
            self.summary_label.setStyleSheet("font-size: 14pt; font-weight: bold; padding: 6px; color: #757575;")
            return

        color = tiers.color(worst_tier if warn_or_worse_count else tiers.NORMAL)
        if warn_or_worse_count == 0:
            text = f"모든 계정 정상 ({len(self._config.accounts)}개 계정)"
        else:
            text = f"주의 이상 계정 {warn_or_worse_count}개 - 가장 급함: {worst_account_label}"
        self.summary_label.setText(text)
        self.summary_label.setStyleSheet(
            f"font-size: 14pt; font-weight: bold; padding: 6px; color: {color};"
        )

    # -- 이벤트 핸들러 --------------------------------------------------
    def _trigger_now(self) -> None:
        self.status_bar_label.setText("수집 중...")
        self._scheduler.trigger_now()

    def _on_collection_finished(self, records: List[store.SampleRecord]) -> None:
        self._refresh_table_from_store()
        failed = sum(1 for r in records if not r.ok)
        if failed:
            self.status_bar_label.setText(f"수집 완료 ({len(records)}개 계정, 실패 {failed}건)")
        else:
            self.status_bar_label.setText(f"수집 완료 ({len(records)}개 계정)")

    def _on_collection_failed(self, message: str) -> None:
        self.status_bar_label.setText(f"수집 오류: {message}")

    def _open_account_dialog(self) -> None:
        dialog = AccountDialog(self._data_dir, self._config, parent=self)
        if dialog.exec_():
            self._config = config_module.load_config(self._data_dir)
            self._refresh_table_from_store()
            self._scheduler.restart_with_current_interval()

    def _open_diagnostics(self) -> None:
        result = diagnostics.run_diagnostics(self._data_dir)
        report = diagnostics.format_report(result)
        box = QMessageBox(self)
        box.setWindowTitle("진단 결과")
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
            self.scan_status_label.setText(f"스캔 상태를 읽을 수 없습니다: {exc}")
            return

        self._scan_snapshot = snapshot
        running = snapshot.is_running or self._scan_worker.is_running()

        parts = [snapshot.window_description]
        parts.append("실행 중" if running else "실행 중 아님")
        latest = snapshot.latest_run
        if latest:
            parts.append(f"최근 실행: {latest['status']} ({latest['started_at'][:19]})")
        pending_total = sum(
            item.pending_baseline_count + item.pending_activity_count for item in snapshot.accounts
        )
        if pending_total:
            # 퍼센트로 부풀리지 않고 남은 작업 수 그대로 보여준다 - 전체 분모를
            # 아직 모르기 때문 (CONCEPT.md "과장하지 않는 UI").
            parts.append(f"남은 디렉터리 작업 {pending_total}개")
        self.scan_status_label.setText("  |  ".join(parts))

        self.scan_run_btn.setEnabled(not running)
        self.scan_stop_btn.setEnabled(running)
        self._refresh_growth_table()

    def _refresh_growth_table(self) -> None:
        snapshot = getattr(self, "_scan_snapshot", None)
        account = self._selected_account()
        if snapshot is None or account is None:
            self.growth_table.setRowCount(0)
            self.growth_caption.setText("계정을 선택하면 증가 경로가 표시됩니다.")
            return

        entry = next(
            (item for item in snapshot.accounts if item.account_id == account.account_id), None
        )
        if entry is None or entry.last_completed_generation is None:
            self.growth_table.setRowCount(0)
            self.growth_caption.setText(
                f"{account.name}: 아직 완료된 기준선이 없습니다 "
                "(상세 스캔이 한 바퀴 끝나야 표시됩니다)."
            )
            return

        activity_note = ""
        if entry.last_activity_total_changed is not None:
            activity_note = f" · 최근 변경 파일 {entry.last_activity_total_changed:,}개"

        if entry.growth:
            self.growth_caption.setText(
                f"{account.name}: 세대 {entry.last_completed_generation} 기준, "
                f"직전 세대와 같은 경로끼리 비교{activity_note}"
            )
            self.growth_table.setRowCount(len(entry.growth))
            for index, row in enumerate(entry.growth):
                current_kb = row["current_kb"]
                previous_kb = row["previous_kb"]
                self.growth_table.setItem(index, 0, QTableWidgetItem(row["path"]))
                self.growth_table.setItem(index, 1, QTableWidgetItem(widgets.format_kb(current_kb)))
                if previous_kb is None:
                    delta_text = "신규 (이전 세대에 없음)"
                else:
                    delta_text = widgets.format_kb_delta(current_kb - previous_kb)
                self.growth_table.setItem(index, 2, QTableWidgetItem(delta_text))
            return

        self.growth_caption.setText(
            f"{account.name}: 세대 {entry.last_completed_generation} 기준선만 있습니다 "
            f"(비교할 이전 세대가 없어 증감은 다음 스캔부터 표시됩니다){activity_note}"
        )
        self.growth_table.setRowCount(len(entry.top_paths))
        for index, row in enumerate(entry.top_paths):
            self.growth_table.setItem(index, 0, QTableWidgetItem(row["path"]))
            self.growth_table.setItem(index, 1, QTableWidgetItem(widgets.format_kb(row["size_kb"])))
            self.growth_table.setItem(index, 2, QTableWidgetItem("-"))

    def _trigger_scan_now(self) -> None:
        if not self._config.accounts:
            QMessageBox.information(self, "계정 없음", "먼저 계정을 등록하세요.")
            return
        reply = QMessageBox.question(
            self,
            "상세 스캔 실행",
            "야간 시간창과 무관하게 지금 상세 스캔을 실행합니다.\n"
            "du/find가 대상 파일시스템을 훑으므로 부하가 생길 수 있습니다.\n\n"
            "계속할까요?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        if not self._scan_worker.run_async(bypass_window=True):
            self.status_bar_label.setText("상세 스캔이 이미 실행 중입니다.")
            return
        self.status_bar_label.setText("상세 스캔 실행 중...")
        self._refresh_scan_section()

    def _request_scan_stop(self) -> None:
        if self._scan_worker.request_stop():
            self.status_bar_label.setText("중지를 요청했습니다. 다음 체크포인트에서 안전하게 멈춥니다.")
        else:
            self.status_bar_label.setText("실행 중인 상세 스캔이 없습니다.")
        self._refresh_scan_section()

    def _on_scan_finished(self, summary) -> None:
        if not summary.started:
            self.status_bar_label.setText(f"상세 스캔 미실행: {summary.reason}")
        else:
            self.status_bar_label.setText(f"상세 스캔 종료 (상태: {summary.status})")
        self._refresh_scan_section()

    def _on_scan_failed(self, message: str) -> None:
        self.status_bar_label.setText(f"상세 스캔 오류: {message}")
        self._refresh_scan_section()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 콜백 이름 규칙)
        self._scheduler.stop()
        self._scan_status_timer.stop()
        # 실행 중인 상세 스캔이 있으면 안전 중지를 요청해 둔다 - 창을 닫는다고
        # 강제로 죽이지는 않는다 (체크포인트를 남기고 스스로 멈추게 한다).
        if self._scan_worker.is_running():
            self._scan_worker.request_stop()
        super().closeEvent(event)
