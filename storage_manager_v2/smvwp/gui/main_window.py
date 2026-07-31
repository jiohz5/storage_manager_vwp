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

from PyQt5.QtWidgets import (
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
from .. import diagnostics, paths, store, tiers
from ..scheduler import CollectorScheduler
from . import widgets
from .account_dialog import AccountDialog

# df가 보고하는 값은 계정 자체가 아니라 그 경로가 속한 파일시스템 전체
# 사용량이라는 점을 항상 화면에 명시한다 (CONCEPT.md 1절).
DF_CAVEAT_TEXT = (
    "※ 사용률은 계정 경로가 속한 파일시스템 전체 사용량 기준입니다 "
    "(df 특성상 계정 단독 사용량이 아닙니다)."
)

COLUMNS = ["이름", "경로", "파일시스템", "용량 사용률", "inode 사용률", "종합 등급", "최근 수집", "상태"]


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
        layout.addWidget(self.table)

        self.status_bar_label = QLabel("")
        self.statusBar().addPermanentWidget(self.status_bar_label)

        self.setCentralWidget(central)

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

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 콜백 이름 규칙)
        self._scheduler.stop()
        super().closeEvent(event)
