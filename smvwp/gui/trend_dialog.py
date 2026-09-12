"""계정 하나의 추세를 보는 창.

## 왜 따로 창인가

홈 표는 "지금 얼마인가"를 한 화면에 놓는 자리다. 추세는 계정 **하나**를
깊이 보는 것이라 성격이 다르고, 가로를 넓게 써야 읽힌다.

표에는 작은 추세선(`Sparkline`)만 두어 모양을 보여 주고, 숫자와 축이 필요하면
여기로 들어온다.

## 읽기는 작업 스레드에서

90일치면 표본이 8,600개까지 간다. 데이터 디렉터리가 NFS 위라 그 조회 하나가
왕복이고, GUI 스레드에서 하면 창이 그동안 굳는다.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .. import config as config_module
from .. import formatting, i18n, store, tiers, trend
from .trend_view import TrendChart

# 고를 수 있는 기간. 90일은 표본 보존 기간(`sample_retention_days`)과 맞춘다 -
# 더 긴 창을 내놓아 봐야 앞쪽이 통째로 빈 그래프가 된다.
RANGE_DAYS = (7, 30, 90)


class _HistoryReader(QObject):
    finished = pyqtSignal(object, object)   # (series, tier)
    failed = pyqtSignal(str)

    def __init__(self, data_dir: Path, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._lock = threading.Lock()
        self._running = False

    def run_async(self, account_id: str, days: int) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
        threading.Thread(
            target=self._run, args=(account_id, days), name="smvwp-trend", daemon=True
        ).start()
        return True

    def _run(self, account_id: str, days: int) -> None:
        conn = None
        try:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            conn = store.connect(self._data_dir)
            samples = store.samples_since(conn, account_id, since)
            # 창을 명시해서 넘긴다. 안 그러면 표본이 하루치뿐일 때 그 하루가
            # 화면을 꽉 채워 "90일 추세" 라며 하루를 보여 준다.
            series = trend.build(
                samples, since=since, until=datetime.now(timezone.utc)
            )
            tier = tiers.UNKNOWN
            for sample in reversed(samples):
                if sample.overall_tier and sample.overall_tier != tiers.UNKNOWN:
                    tier = sample.overall_tier
                    break
        except Exception as exc:  # pragma: no cover - 방어적 처리
            self.failed.emit(str(exc))
            return
        finally:
            if conn is not None:
                conn.close()
            with self._lock:
                self._running = False
        self.finished.emit(series, tier)


class TrendDialog(QDialog):
    def __init__(self, data_dir: Path, config: config_module.AppConfig,
                 account_id: str = "", parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._config = config
        self.setWindowTitle(i18n.t("trend.title"))
        self.resize(860, 480)

        self._reader = _HistoryReader(data_dir, self)
        self._reader.finished.connect(self._on_loaded)
        self._reader.failed.connect(self._on_failed)

        self._build_ui()
        if account_id:
            index = self.account_combo.findData(account_id)
            if index >= 0:
                self.account_combo.setCurrentIndex(index)
        self._reload()

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

        top.addWidget(QLabel(i18n.t("trend.range_label")))
        self.range_combo = QComboBox()
        for days in RANGE_DAYS:
            self.range_combo.addItem(i18n.t("trend.range_days", days=days), days)
        self.range_combo.setCurrentIndex(1)
        self.range_combo.currentIndexChanged.connect(self._reload)
        top.addWidget(self.range_combo)
        top.addStretch(1)
        root.addLayout(top)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        self.chart = TrendChart()
        root.addWidget(self.chart, 1)

        self.note = QLabel(i18n.t("trend.note"))
        self.note.setObjectName("caption")
        self.note.setWordWrap(True)
        root.addWidget(self.note)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close_btn = QPushButton(i18n.t("common.close"))
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def _reload(self) -> None:
        account_id = self.account_combo.currentData()
        if not account_id:
            self.summary.setText(i18n.t("trend.no_account"))
            self.chart.set_series(None)
            return
        self.summary.setText(i18n.t("trend.loading"))
        self._reader.run_async(account_id, self.range_combo.currentData())

    def _on_failed(self, message: str) -> None:
        self.summary.setText(i18n.t("trend.failed", error=message))

    def _on_loaded(self, series, tier) -> None:
        self.chart.set_series(series, tier)
        if series.empty:
            # 빈 그래프를 그냥 두면 "용량이 0" 으로 읽힌다. 사실은 이 기간에
            # 표본이 없는 것이다.
            self.summary.setText(i18n.t("trend.no_data_in_range"))
            return

        parts = [
            i18n.t(
                "trend.summary",
                now=f"{series.last:.1f}",
                low=f"{series.low:.1f}",
                high=f"{series.high:.1f}",
            )
        ]
        if series.change is not None:
            parts.append(i18n.t("trend.change", delta=f"{series.change:+.1f}"))
        if series.gaps:
            # 구멍을 숨기면 평평한 선이 "안정적" 으로 읽힌다.
            parts.append(i18n.t("trend.gaps", count=series.gaps))
        self.summary.setText("  ·  ".join(parts))
        # 마지막 값이 곧 지금 상태라, 서식은 표와 같은 것을 쓴다.
        self.setWindowTitle(
            i18n.t("trend.title") + "  -  " + formatting.tier_badge_text(
                tier, series.last
            )
        )
