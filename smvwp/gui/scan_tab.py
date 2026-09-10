"""야간 상세 스캔 탭.

## 왜 창에서 떼어 냈는가

`main_window.py` 가 1,772줄에 클래스 하나였다. 그중 스캔 화면이 660줄로 가장
큰 덩어리인데, 홈 탭과 섞여 있어 어느 쪽을 고치든 다른 쪽을 읽어야 했다.

떼어 내면서 **경계를 신호로 바꿨다.** 예전에는 이 코드가 홈 탭의 배너 위젯과
창의 상태 줄을 직접 만졌는데, 그러면 둘을 따로 옮길 수 없다. 지금은 값만
내보내고 그리기는 창이 한다:

- `status_message` - 상태 줄에 쓸 한 줄
- `banner_changed` - 홈 탭 배너에 필요한 값 (`BannerState`)

## 설정을 붙잡아 두지 않는다

`self._config` 로 들고 있으면 계정 대화상자에서 설정이 바뀌었을 때 낡은 것을
계속 쓴다. `get_config()` 로 그때그때 받아 온다 - 작업 스레드들이 이미 같은
방식이다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from typing import Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import auto_scan, config as config_module, cron_status, formatting, i18n
from .. import large_files, scan_digest, usage_log
from .. import nightly_scan, procio, tiers
from ..scheduler import NightlyScanWorker, ScanStatusWorker
from . import theme, widgets
from .scan_progress_dialog import ScanProgressDialog

# 스캔이 도는 동안에는 촘촘히, 멈춰 있으면 느슨하게 본다. 조회 하나가 계정마다
# 십여 개의 쿼리라 NFS 위에서는 이 간격이 그대로 부담이 된다.
SCAN_STATUS_REFRESH_MS = 5000
SCAN_STATUS_IDLE_REFRESH_MS = 30000

# 시간창에 들어왔는지 확인하는 주기. 1분이면 22:00 시작이 최대 1분 늦을 뿐이라
# 밤 단위 작업에는 충분하고, 더 자주 볼 이유가 없다.
AUTO_SCAN_CHECK_MS = 60_000

# cron 등록 여부를 다시 확인하는 주기.
#
# `crontab -l` 은 로컬 명령이라 몇 밀리초면 끝나지만, 5초마다 프로세스를 띄울
# 이유는 없다. 사람이 터미널에서 등록하고 창으로 돌아오는 데 걸리는 시간을
# 생각하면 몇 분이면 충분하다. 스캔 버튼을 누를 때도 한 번 더 본다 - 방금
# 고치고 눌렀을 가능성이 가장 높은 순간이라서다.
CRON_RECHECK_MS = 3 * 60_000


GROWTH_COLUMN_KEYS = ["scan.col.path", "scan.col.current_size", "scan.col.delta"]

# 가장 큰 파일 표. 디렉터리 합계만 보면 "파일 하나가 유난히 크다"를 놓친다 -
# 300GB 짜리 디렉터리가 고른 파일 3천 개인지 한 파일이 280GB 인지 구분되지
# 않기 때문이다.
LARGE_COLUMN_KEYS = [
    "large.col.path",
    "large.col.size",
    "large.col.share",
    "large.col.change",
]
(LARGE_PATH, LARGE_SIZE, LARGE_SHARE, LARGE_CHANGE) = range(4)

# 화면에 띄우는 줄 수. 표는 훑어보는 것이지 뒤지는 것이 아니다.
LARGE_ROWS_SHOWN = 12

# 상세 스캔 탭 위쪽의 계정별 현황 표.
#
# 예전에는 이 탭이 "고른 계정 하나의 증가 경로"만 보여 줘서, 밤새 무슨 일이
# 있었는지 알려면 계정을 하나씩 눌러 봐야 했다. 아침에 이 화면을 여는 이유는
# 대개 "전체가 어디까지 갔나"이므로 그 답이 먼저 보여야 한다.
SCAN_ACCOUNT_COLUMN_KEYS = [
    "scan.acct.name",
    "scan.acct.kind",
    "scan.acct.progress",
    "scan.acct.pending",
    "scan.acct.measured",
    "scan.acct.eta",
    "scan.acct.last_scan",
    "scan.acct.note",
]
(
    SCAN_ACCT_NAME,
    SCAN_ACCT_KIND,
    SCAN_ACCT_PROGRESS,
    SCAN_ACCT_PENDING,
    SCAN_ACCT_MEASURED,
    SCAN_ACCT_ETA,
    SCAN_ACCT_LAST,
    SCAN_ACCT_NOTE,
) = range(8)


def _format_duration(seconds: float) -> str:
    """남은 시간을 사람이 읽는 단위로. 항상 **두 자리 이내**로 줄인다.

    `1시간 23분 45초`처럼 정밀하게 적지 않는 이유: 이 값은 관측한 속도에
    남은 개수를 곱한 어림이라 초 단위는 있지도 않은 정확도를 흉내 내는 것이다.
    실제로 쓸모 있는 판단은 "지금 기다릴까, 자고 올까" 수준이므로 그 정도만
    구분되면 된다."""

    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return i18n.t("duration.under_minute")
    minutes = int(seconds // 60)
    if minutes < 60:
        return i18n.t("duration.minutes", minutes=minutes)
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        if minutes:
            return i18n.t("duration.hours_minutes", hours=hours, minutes=minutes)
        return i18n.t("duration.hours", hours=hours)
    days, hours = divmod(hours, 24)
    return i18n.t("duration.days_hours", days=days, hours=hours)


@dataclass
class BannerState:
    """홈 탭 배너에 필요한 것 전부.

    위젯이 아니라 값으로 넘긴다 - 그래야 이 탭이 홈 탭의 위젯 이름을 몰라도
    된다."""

    running: bool = False
    path: str = ""
    done: int = 0
    total: int = 0


class ScanTab(QFrame):
    """야간 상세 스캔 화면 하나. 창은 이것을 탭에 붙이고 신호만 듣는다.

    `QWidget` 이 아니라 `QFrame` 인 이유: 테마의 카드 스타일(`QFrame#card`)이
    QFrame 에만 붙는다. 떼어 낼 때 QWidget 으로 두었더니 테두리와 배경이
    조용히 사라졌다 - 화면은 뜨고 동작도 하니 시험은 아무것도 못 잡는다.
    """

    status_message = pyqtSignal(str)
    banner_changed = pyqtSignal(object)   # BannerState

    def __init__(self, data_dir: Path, get_config, parent: QWidget = None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._get_config = get_config
        self._scan_snapshot = None
        self._growth_sort_touched = False
        self._cron_status = None
        self._auto_scan_started_key = None

        self._build()

        self._scan_worker = NightlyScanWorker(data_dir, get_config=get_config)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.failed.connect(self._on_scan_failed)

        # 스캔 상태 조회는 계정마다 십여 개의 쿼리를 던진다. NFS 위에서는
        # 그것만으로 창이 멈추므로 **GUI 스레드에서 하지 않는다.**
        self._status_worker = ScanStatusWorker(data_dir, get_config, self)
        self._status_worker.finished.connect(self._on_scan_status_ready)
        self._status_worker.failed.connect(self._on_scan_status_failed)

        self._scan_status_timer = QTimer(self)
        self._scan_status_timer.timeout.connect(self.refresh)
        self._scan_status_timer.start(SCAN_STATUS_IDLE_REFRESH_MS)

        self._cron_checking = False
        self._check_cron_async()

        # cron 은 이 창 **밖에서** 바뀐다. 사람이 안내를 보고 터미널에서
        # `setup_cron.csh` 를 돌리면, 창은 그 사실을 스스로 알아채야 한다.
        # 한 번만 읽고 끝내면 빨간 글씨가 사실과 어긋난 채 남는다.
        self._cron_timer = QTimer(self)
        self._cron_timer.timeout.connect(self._check_cron_async)
        self._cron_timer.start(CRON_RECHECK_MS)

        self._auto_scan_timer = QTimer(self)
        self._auto_scan_timer.timeout.connect(self._maybe_start_nightly_scan)
        self._auto_scan_timer.start(AUTO_SCAN_CHECK_MS)
        self._maybe_start_nightly_scan()

        self.refresh()

    # -- 창이 부르는 것들 ---------------------------------------------

    def is_scan_running(self) -> bool:
        return self._scan_worker.is_running()

    def select_account(self, account_id) -> None:
        """홈 표에서 고른 계정을 이쪽에도 맞춘다.

        콤보가 실제로 바뀌면 `currentIndexChanged` 가 증감 표까지 이어서
        갱신하므로 여기서 더 할 일이 없다. 안 바뀌었으면 직접 다시 그린다."""

        index = self.scan_account_combo.findData(account_id)
        if index >= 0 and index != self.scan_account_combo.currentIndex():
            self.scan_account_combo.setCurrentIndex(index)
            return
        self._refresh_growth_table()

    def shutdown(self) -> None:
        """창을 닫을 때. 타이머를 멈추고, 우리가 띄운 스캔을 정리한다."""

        self._scan_status_timer.stop()
        self._auto_scan_timer.stop()
        self._cron_timer.stop()
        if self._scan_worker.is_running():
            self._stop_scan_for_shutdown()

    def retranslate(self) -> None:
        self.scan_title_label.setText(i18n.t("scan.section_title"))
        self.scan_account_label.setText(i18n.t("scan.account_label"))
        self._sync_scan_account_combo()
        self.scan_run_btn.setText(i18n.t("scan.btn.run_now"))
        self.scan_run_btn.setToolTip(i18n.t("scan.btn.run_now_tooltip"))
        self.scan_stop_btn.setText(i18n.t("scan.btn.stop"))
        self.scan_stop_btn.setToolTip(i18n.t("scan.btn.stop_tooltip"))
        self.scan_detail_btn.setText(i18n.t("scan.btn.progress"))
        self.scan_detail_btn.setToolTip(i18n.t("scan.btn.progress_tooltip"))
        self.growth_table.setHorizontalHeaderLabels(
            [i18n.t(key) for key in GROWTH_COLUMN_KEYS]
        )
        self.large_table.setHorizontalHeaderLabels(
            [i18n.t(key) for key in LARGE_COLUMN_KEYS]
        )
        self.tree_btn.setText(i18n.t("tree.btn.open"))
        self.findings_caption.setText(i18n.t("digest.findings_heading"))

    def _build(self) -> None:
        """야간 상세 스캔 영역 - 탭을 새로 만들지 않고 같은 화면 아래쪽에
        붙인다 (DESIGN.md 2부 6절 "대시보드 단일 화면" 결정 유지)."""

        # 예전 `_build_scan_section` 이 만들던 QFrame 과 같은 모양.
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("card")

        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(9)

        # 제목과 동작 버튼을 한 줄에 둔다. 따로 두면 그것만으로 두 줄이
        # 나가는데, 이 화면은 세로가 모자란 쪽이다.
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.scan_title_label = QLabel()
        self.scan_title_label.setObjectName("sectionTitle")
        title_row.addWidget(self.scan_title_label)
        title_row.addStretch(1)
        box.addLayout(title_row)
        self._title_row = title_row

        # -- 요약 카드 --------------------------------------------------
        #
        # 표 셋을 읽고 스스로 요약을 만들라고 하면 대부분 안 읽는다. 아침에
        # 사람이 실제로 묻는 넷을 카드 하나씩으로 세운다: 잘 돌았나, 얼마나
        # 늘었나, 어느 계정이, 손댈 것이 있나. 근거는 아래 표에 그대로 있다.
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.card_run = widgets.StatCard()
        self.card_delta = widgets.StatCard()
        self.card_biggest = widgets.StatCard()
        self.card_findings = widgets.StatCard()
        for card in (
            self.card_run, self.card_delta, self.card_biggest, self.card_findings
        ):
            cards.addWidget(card, 1)
        box.addLayout(cards)

        self.scan_status_label = QLabel()
        self.scan_status_label.setWordWrap(True)
        box.addWidget(self.scan_status_label)

        # cron 등록 여부. 야간 스캔이 안 도는 가장 흔한 이유가 "등록이 안 된
        # 것"인데, 그 사실은 아무 데도 드러나지 않아 사람은 프로그램이 고장 난
        # 줄 안다. 다음 날 아침 보고서가 비어 있어야 알아채고, 그때는 이미
        # 하룻밤을 버린 뒤다.
        self.cron_status_label = QLabel()
        self.cron_status_label.setObjectName("caption")
        self.cron_status_label.setWordWrap(True)
        box.addWidget(self.cron_status_label)

        # 스캔이 도는 동안 좌우로 오가는 막대.
        #
        # 일부러 **불확정(indeterminate)** 막대를 쓴다. 남은 체크포인트 수는
        # 알지만 전체 분모는 모른다 - 디렉터리를 분할하면 작업이 늘어나서
        # 진행률이 뒤로 갈 수도 있다. 그럴 바에는 퍼센트를 지어내지 않고
        # "지금 일하는 중"만 정직하게 보여준다 (DESIGN.md 1부 "과장하지 않는
        # UI"). 남은 작업 수는 옆 상태 줄에 숫자 그대로 나온다.
        self.scan_current_label = QLabel()
        self.scan_current_label.setObjectName("caption")
        self.scan_current_label.setWordWrap(True)
        self.scan_current_label.setVisible(False)
        box.addWidget(self.scan_current_label)

        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setFixedHeight(6)
        self.scan_progress.setVisible(False)
        box.addWidget(self.scan_progress)

        scan_buttons = QHBoxLayout()
        scan_buttons.setSpacing(8)
        self.scan_run_btn = QPushButton()
        self.scan_run_btn.clicked.connect(self._trigger_scan_now)
        self.scan_stop_btn = QPushButton()
        # 중지는 되돌릴 수 없는 성격의 동작이라 색으로 구분해 둔다 (강제 종료는
        # 아니지만, 실수로 누르면 진행 중인 밤을 날린다).
        self.scan_stop_btn.setObjectName("danger")
        self.scan_stop_btn.clicked.connect(self._request_scan_stop)
        self.scan_detail_btn = QPushButton()
        self.scan_detail_btn.clicked.connect(self._open_scan_progress)
        # 계정 안을 파고드는 화면. 여기까지 와서 "그래서 그 300GB 가 어디야"를
        # 묻게 되는데, 지금까지는 답할 자리가 없었다.
        self.tree_btn = QPushButton(i18n.t("tree.btn.open"))
        self.tree_btn.clicked.connect(self._open_tree)
        # 동작 버튼은 제목 줄 오른쪽에 붙인다.
        for button in (
            self.scan_run_btn, self.scan_stop_btn,
            self.scan_detail_btn, self.tree_btn,
        ):
            self._title_row.addWidget(button)
        del scan_buttons

        # -- 살펴볼 것 ---------------------------------------------------
        #
        # 실패·권한 부족·튀는 파일·크게 는 계정을 한 목록으로 모은다. 지금까지는
        # 이것들이 표 셋에 흩어져 있어서, 급한 것이 표 밑으로 밀리면 아무도
        # 못 봤다.
        self.findings_caption = QLabel()
        self.findings_caption.setObjectName("sectionTitle")
        box.addWidget(self.findings_caption)

        self.findings_list = QListWidget()
        self.findings_list.setObjectName("findings")
        self.findings_list.setFrameShape(QListWidget.NoFrame)
        self.findings_list.setSelectionMode(QListWidget.NoSelection)
        self.findings_list.setFocusPolicy(Qt.NoFocus)
        self.findings_list.setMaximumHeight(150)
        box.addWidget(self.findings_list)

        # -- 계정별 현황 ------------------------------------------------
        self.scan_accounts_caption = QLabel()
        self.scan_accounts_caption.setObjectName("sectionTitle")
        box.addWidget(self.scan_accounts_caption)

        self.scan_accounts_table = QTableWidget(0, len(SCAN_ACCOUNT_COLUMN_KEYS))
        accounts_header = self.scan_accounts_table.horizontalHeader()
        accounts_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        accounts_header.setSectionResizeMode(SCAN_ACCT_NOTE, QHeaderView.Stretch)
        accounts_header.setHighlightSections(False)
        # 격자선을 끈 표라, 열이 내용 폭에 딱 붙으면 옆 칸 값과 한 덩어리로
        # 읽힌다 ("약 21분 아직 없음"). 최소 폭으로 숨 쉴 자리를 만든다.
        accounts_header.setMinimumSectionSize(96)
        self.scan_accounts_table.verticalHeader().setVisible(False)
        self.scan_accounts_table.setShowGrid(False)
        self.scan_accounts_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.scan_accounts_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.scan_accounts_table.verticalHeader().setDefaultSectionSize(30)
        # 이 표에서 행을 고르면 아래 증가 경로도 그 계정으로 바뀐다. 표를 보고
        # "이 계정이 이상한데" 싶을 때 곧바로 파고들 수 있어야 한다.
        self.scan_accounts_table.itemSelectionChanged.connect(
            self._on_scan_account_row_selected
        )
        self.scan_accounts_table.setMinimumHeight(120)
        box.addWidget(self.scan_accounts_table)

        # -- 세부 (한 계정) ----------------------------------------------
        #
        # 증가 경로와 큰 파일은 **같은 계정에 대한 두 관점**이라 나란히 두는
        # 편이 읽힌다. 세로로 쌓으면 둘을 견주려고 스크롤을 오가야 한다.
        detail_head = QHBoxLayout()
        detail_head.setSpacing(8)
        self.scan_account_label = QLabel()
        self.scan_account_label.setObjectName("muted")
        self.scan_account_combo = QComboBox()
        self.scan_account_combo.setMinimumWidth(200)
        self.scan_account_combo.currentIndexChanged.connect(self._on_scan_account_chosen)
        detail_head.addWidget(self.scan_account_label)
        detail_head.addWidget(self.scan_account_combo)
        detail_head.addStretch(1)
        box.addLayout(detail_head)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(12)
        growth_side = QVBoxLayout()
        growth_side.setSpacing(6)
        large_side = QVBoxLayout()
        large_side.setSpacing(6)
        detail_row.addLayout(growth_side, 1)
        detail_row.addLayout(large_side, 1)
        box.addLayout(detail_row, 1)

        self.growth_caption = QLabel()
        self.growth_caption.setObjectName("muted")
        self.growth_caption.setWordWrap(True)
        growth_side.addWidget(self.growth_caption)

        self.growth_table = QTableWidget(0, len(GROWTH_COLUMN_KEYS))
        growth_header = self.growth_table.horizontalHeader()
        growth_header.setSectionResizeMode(0, QHeaderView.Stretch)
        growth_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        growth_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        growth_header.setHighlightSections(False)
        self.growth_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.growth_table.verticalHeader().setVisible(False)
        self.growth_table.verticalHeader().setDefaultSectionSize(34)
        self.growth_table.setShowGrid(False)
        # 이 표에는 칸 위젯이 없어 Qt 내장 정렬을 그대로 쓸 수 있다.
        # (계정 표는 막대·배지가 칸 위젯이라 Qt 정렬을 켜면 위젯이 행을 따라
        #  움직이지 않아 어긋난다 - 그쪽은 직접 정렬해 다시 그린다.)
        self.growth_table.setSortingEnabled(True)
        self.growth_table.horizontalHeader().setSortIndicatorShown(True)
        # 사용자가 한 번이라도 헤더를 누르면 그 정렬을 존중한다 - 갱신할
        # 때마다 기본값으로 되돌리면 정렬을 바꾼 의미가 없다.
        self._growth_sort_touched = False
        self.growth_table.horizontalHeader().sectionClicked.connect(
            lambda _index: setattr(self, '_growth_sort_touched', True)
        )
        # 이 표는 보조 정보다. 최소 높이를 낮게 잡아 두지 않으면 계정 표(이
        # 화면의 주인공)를 아래에서 밀어 올려 행이 잘린다.
        self.growth_table.setMinimumHeight(120)
        growth_side.addWidget(self.growth_table, 1)

        self.large_caption = QLabel()
        self.large_caption.setObjectName("muted")
        self.large_caption.setWordWrap(True)
        self.large_caption.setToolTip(i18n.t("large.tip"))
        large_side.addWidget(self.large_caption)

        self.large_table = QTableWidget(0, len(LARGE_COLUMN_KEYS))
        large_header = self.large_table.horizontalHeader()
        large_header.setSectionResizeMode(LARGE_PATH, QHeaderView.Stretch)
        for column in (LARGE_SIZE, LARGE_SHARE, LARGE_CHANGE):
            large_header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        large_header.setHighlightSections(False)
        self.large_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.large_table.verticalHeader().setVisible(False)
        self.large_table.verticalHeader().setDefaultSectionSize(30)
        self.large_table.setShowGrid(False)
        self.large_table.setMinimumHeight(120)
        large_side.addWidget(self.large_table, 1)

    def _current_target_text(self, latest_run) -> str:
        """지금 훑고 있는 경로 한 줄."""

        if not latest_run:
            return ""
        try:
            path = latest_run["current_path"]
            account_id = latest_run["current_account_id"]
        except (KeyError, IndexError):
            return ""
        if not path:
            return ""
        account = config_module.find_account(self._get_config(), account_id) if account_id else None
        kind_text = i18n.t(
            "progress.kind.baseline"
        )
        return i18n.t(
            "scan.current_target",
            account=account.name if account else i18n.t("common.none"),
            kind=kind_text,
            path=path,
        )

    def _open_tree(self) -> None:
        """지금 고른 계정을 펼쳐 보는 창.

        다시 스캔하지 않는다 - 야간 스캔이 남긴 기록만 읽는다."""

        from .tree_dialog import TreeDialog

        TreeDialog(
            self._data_dir,
            self._get_config(),
            account_id=self.scan_account_combo.currentData() or "",
            parent=self,
        ).exec_()

    def _open_scan_progress(self) -> None:
        account = self._selected_account()
        dialog = ScanProgressDialog(
            self._data_dir,
            self._get_config(),
            account_id=account.account_id if account else None,
            parent=self,
        )
        dialog.exec_()
    @staticmethod

    def _scan_failure_text(entry, account_name: str) -> str:
        """스캔에서 재지 못한 경로를 사유와 함께 몇 개 보여 준다.

        개수만 알려 주면 "왜?"에 답하지 못해 사용자가 할 수 있는 일이 없다.
        경로와 사유를 함께 보여 주면 권한 요청이든 대상 제외든 바로 판단할 수
        있다."""

        lines = [i18n.t("scan.failed_warning", count=entry.failed_count)]
        for path, message in entry.failed_paths[:3]:
            first_line = (message or "").strip().splitlines()[0] if message else ""
            lines.append(f"  · {path} — {first_line}" if first_line else f"  · {path}")
        if entry.failed_count > 3:
            lines.append(i18n.t("scan.failed_more", count=entry.failed_count - 3))
        return "\n".join(lines)
    # -- 상세 스캔 영역 --------------------------------------------------

    def _selected_account(self) -> Optional[config_module.Account]:
        """증가 경로를 보여줄 계정.

        탭을 나누면서 선택 수단이 두 개가 됐다: 홈 탭의 표와 상세 스캔 탭의
        콤보. **콤보를 진실의 원천으로 삼고** 표에서 고르면 콤보를 따라오게
        한다 - 상세 스캔 탭만 열어 놓고도 계정을 바꿀 수 있어야 하기 때문이다
        (탭을 나눠 놓고 "선택은 저쪽 탭에서 하세요"는 말이 안 된다)."""

        account_id = self.scan_account_combo.currentData()
        return config_module.find_account(self._get_config(), account_id) if account_id else None

    def _sync_scan_account_combo(self) -> None:
        """계정 목록이 바뀌면 콤보를 다시 채운다 (현재 선택은 유지)."""

        current = self.scan_account_combo.currentData()
        self.scan_account_combo.blockSignals(True)
        self.scan_account_combo.clear()
        for account in self._get_config().accounts:
            self.scan_account_combo.addItem(account.name, account.account_id)
        index = self.scan_account_combo.findData(current)
        self.scan_account_combo.setCurrentIndex(index if index >= 0 else 0)
        self.scan_account_combo.blockSignals(False)

    def refresh(self) -> None:
        """스캔 상태 조회를 **요청만** 한다. 실제 읽기는 백그라운드에서 돈다.

        예전에는 여기서 바로 DB를 읽었는데, 계정마다 십여 개의 쿼리라 데이터
        디렉터리가 NFS 위면 그동안 창이 통째로 멈췄다. 결과는
        `_on_scan_status_ready`가 받는다."""

        self._render_cron_status()
        self._status_worker.refresh_async()

    def _on_scan_status_failed(self, message: str) -> None:
        self.scan_status_label.setText(i18n.t("scan.status_error", message=message))

    def _on_scan_status_ready(self, snapshot) -> None:
        """백그라운드가 읽어 온 스냅샷을 화면에 그린다 (GUI 스레드)."""

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
                    started_at=formatting.local_datetime_text(latest["started_at"]),
                )
            )
        pending_total = sum(
            item.pending_baseline_count for item in snapshot.accounts
        )
        done_total = sum(item.baseline_done for item in snapshot.accounts)
        total_total = sum(item.baseline_total for item in snapshot.accounts)
        if pending_total:
            parts.append(i18n.t("scan.pending_tasks", count=pending_total))

        # 진행률을 숫자와 막대로 함께 보여준다.
        #
        # 예전에는 남은 개수만 적고 막대는 불확정으로 뒀는데, 그러면 "얼마나
        # 남았나"에 답이 안 된다. 분모(total)는 진행 중 늘어날 수 있지만
        # (시간 초과로 디렉터리를 쪼개면 작업이 추가된다) 그 사실을 **문구에
        # 적어 두면** 숫자를 지어내는 것이 아니다. 아무것도 안 보여 주는 것보다
        # 낫다.
        if total_total:
            self.scan_progress.setRange(0, total_total)
            self.scan_progress.setValue(done_total)
            parts.append(
                i18n.t(
                    "scan.progress_counts",
                    done=done_total,
                    total=total_total,
                    percent=int(done_total * 100 / total_total),
                )
            )
        else:
            self.scan_progress.setRange(0, 0)
        cpu_text = formatting.scan_cpu_text(latest)
        if cpu_text:
            parts.append(cpu_text)
        self.scan_status_label.setText("  |  ".join(parts))

        # 지금 어느 경로를 훑고 있는지. `du` 하나가 몇 분씩 걸릴 수 있어서,
        # "실행 중"만 떠 있으면 멈춘 것인지 진행 중인지 구분되지 않는다.
        current = self._current_target_text(latest) if running else ""
        self.scan_current_label.setText(current)
        self.scan_current_label.setVisible(bool(current))

        # 홈 탭 배너는 창의 것이다. 값만 넘기고 그리기는 창이 한다 -
        # 탭이 다른 탭의 위젯을 직접 만지면 둘을 따로 옮길 수 없게 된다.
        path = ""
        if running and latest:
            try:
                path = latest["current_path"] or ""
            except (KeyError, IndexError):
                path = ""
        self.banner_changed.emit(
            BannerState(running=running, path=path, done=done_total, total=total_total)
        )

        # 막대는 도는 동안에만 보인다. 멈춰 있는데도 계속 떠 있으면 "뭔가
        # 돌고 있나?"라는 오해를 만든다.
        self.scan_progress.setVisible(running)

        # 도는 동안만 촘촘히 본다. 멈춰 있으면 30초로 늦춰 NFS 부담을 줄인다.
        wanted = SCAN_STATUS_REFRESH_MS if running else SCAN_STATUS_IDLE_REFRESH_MS
        if self._scan_status_timer.interval() != wanted:
            self._scan_status_timer.start(wanted)

        self.scan_run_btn.setEnabled(not running)
        self.scan_stop_btn.setEnabled(running)
        self._refresh_digest(snapshot)
        self._refresh_scan_accounts_table(snapshot, running)
        self._refresh_growth_table()
        self._refresh_large_files()

    # -- 요약 ------------------------------------------------------------
    def _refresh_digest(self, snapshot) -> None:
        """카드 넷과 살펴볼 것 목록.

        아침에 사람이 실제로 묻는 것은 넷뿐이다 - 잘 돌았나, 얼마나 늘었나,
        어느 계정이, 손댈 것이 있나. 표 셋을 읽고 스스로 요약을 만들게 하는
        대신 답을 먼저 세운다."""

        digest = scan_digest.build(snapshot)

        # 1. 잘 돌았나
        self.card_run.set_label(i18n.t("digest.run"))
        if digest.is_running:
            self.card_run.show_value(
                i18n.t("digest.running"), i18n.t("digest.running_detail")
            )
        elif digest.scanned_at:
            self.card_run.show_value(
                formatting.scan_label(digest.scanned_at),
                i18n.t(
                    "digest.run_detail",
                    status=self._status_text(digest.run_status),
                    duration=self._duration_text(digest.run_seconds),
                ),
            )
        else:
            # 아직 한 번도 안 끝났다. '-' 를 세우고 무엇을 하면 되는지 적는다 -
            # 빈 카드는 고장으로 읽힌다.
            self.card_run.show_value("-", i18n.t("digest.never_run"))

        # 2. 얼마나 늘었나
        self.card_delta.set_label(i18n.t("digest.delta"))
        if digest.total_delta_kb is None:
            self.card_delta.show_value("-", i18n.t("digest.no_compare"))
        else:
            self.card_delta.show_value(
                formatting.format_kb_delta(digest.total_delta_kb),
                i18n.t("digest.delta_detail", count=digest.accounts_compared),
                # 늘어난 것만 물들인다. 줄어든 것은 좋은 소식이라 경고가 아니다.
                color=(
                    tiers.color(tiers.WARN) if digest.total_delta_kb > 0 else None
                ),
            )

        # 3. 어느 계정이
        self.card_biggest.set_label(i18n.t("digest.biggest"))
        biggest = digest.biggest
        if biggest is None:
            self.card_biggest.show_value("-", i18n.t("digest.no_compare"))
        else:
            self.card_biggest.show_value(
                biggest.name,
                i18n.t(
                    "digest.biggest_detail",
                    delta=formatting.format_kb_delta(biggest.delta_kb),
                    total=formatting.format_kb(biggest.total_kb),
                ),
            )

        # 4. 손댈 것
        self.card_findings.set_label(i18n.t("digest.findings"))
        if not digest.findings:
            self.card_findings.show_value("0", i18n.t("digest.findings_none"))
        else:
            self.card_findings.show_value(
                str(len(digest.findings)),
                i18n.t("digest.findings_detail", urgent=digest.urgent_count),
                color=tiers.color(tiers.ALERT) if digest.urgent_count else None,
            )

        self._refresh_findings(digest)

    def _refresh_findings(self, digest) -> None:
        self.findings_caption.setText(i18n.t("digest.findings_heading"))
        self.findings_list.clear()
        if not digest.findings:
            item = QListWidgetItem(i18n.t("digest.findings_empty"))
            item.setForeground(QColor(theme.TEXT_MUTED))
            self.findings_list.addItem(item)
            return
        for finding in digest.findings:
            item = QListWidgetItem(self._finding_text(finding))
            if finding.urgent:
                item.setForeground(QColor(tiers.color(tiers.ALERT)))
            if finding.path:
                item.setToolTip(finding.path)
            self.findings_list.addItem(item)

    def _finding_text(self, finding) -> str:
        """살펴볼 것 한 줄을 사람 문장으로.

        표의 한 행이 아니라 문장으로 쓰는 것은 의도다 - 이 목록은 훑어보라고
        있는 것이고, 훑을 때는 열을 따라 읽는 것보다 문장이 빠르다."""

        if finding.kind == scan_digest.FINDING_FAILED:
            return i18n.t(
                "digest.item.failed", account=finding.account, count=finding.count
            )
        if finding.kind == scan_digest.FINDING_PARTIAL:
            return i18n.t(
                "digest.item.partial", account=finding.account, count=finding.count
            )
        if finding.kind == scan_digest.FINDING_LARGE_FILE:
            change = (
                formatting.format_kb_delta(finding.delta_kb)
                if finding.delta_kb is not None
                else i18n.t("large.new")
            )
            return i18n.t(
                "digest.item.large_file",
                account=finding.account,
                name=finding.path.rsplit("/", 1)[-1],
                size=formatting.format_kb(finding.size_kb),
                change=change,
            )
        return i18n.t(
            "digest.item.growth",
            account=finding.account,
            delta=formatting.format_kb_delta(finding.delta_kb),
            total=formatting.format_kb(finding.size_kb),
        )

    def _status_text(self, status) -> str:
        """실행 상태를 사람 말로. 모르는 값은 그대로 보여 준다."""

        if not status:
            return i18n.t("common.none")
        key = f"digest.status.{status}"
        text = i18n.t(key)
        return status if text == key else text

    def _duration_text(self, seconds) -> str:
        if seconds is None:
            return i18n.t("common.none")
        hours, rest = divmod(int(seconds), 3600)
        minutes = rest // 60
        if hours:
            return i18n.t("digest.duration_hm", hours=hours, minutes=minutes)
        return i18n.t("digest.duration_m", minutes=minutes)

    def _refresh_scan_accounts_table(self, snapshot, running: bool) -> None:
        """계정별 진행·측정량·예상 남은 시간을 한 표로 보여준다."""

        entries = {item.account_id: item for item in snapshot.accounts}
        accounts = self._get_config().accounts
        dash = i18n.t("common.none")
        self.scan_accounts_caption.setText(i18n.t("scan.acct.heading"))

        table = self.scan_accounts_table
        # 칸 위젯은 안 쓰지만, 행 수가 줄어든 경우 이전 내용이 남지 않도록
        # 매번 비우고 다시 채운다.
        table.setRowCount(0)
        table.setRowCount(len(accounts))
        table.setHorizontalHeaderLabels(
            [i18n.t(key) for key in SCAN_ACCOUNT_COLUMN_KEYS]
        )

        for row, account in enumerate(accounts):
            entry = entries.get(account.account_id)
            name_item = QTableWidgetItem(account.name)
            name_item.setData(Qt.UserRole, account.account_id)
            name_item.setToolTip(account.path)
            table.setItem(row, SCAN_ACCT_NAME, name_item)
            table.setItem(
                row, SCAN_ACCT_KIND, QTableWidgetItem(i18n.t(f"account.kind.{account.kind}"))
            )

            if entry is None:
                for column in (
                    SCAN_ACCT_PROGRESS,
                    SCAN_ACCT_PENDING,
                    SCAN_ACCT_MEASURED,
                    SCAN_ACCT_ETA,
                    SCAN_ACCT_LAST,
                    SCAN_ACCT_NOTE,
                ):
                    table.setItem(row, column, QTableWidgetItem(dash))
                continue

            if entry.baseline_total:
                percent = int(entry.baseline_done * 100 / entry.baseline_total)
                progress = f"{entry.baseline_done:,}/{entry.baseline_total:,}  ({percent}%)"
            else:
                progress = dash
            progress_item = QTableWidgetItem(progress)
            # 분모가 도중에 늘어날 수 있다는 사실은 숨기지 않는다 - 모르면
            # 진행률이 뒤로 가는 것을 고장으로 읽는다.
            progress_item.setToolTip(i18n.t("scan.acct.progress_tip"))
            progress_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, SCAN_ACCT_PROGRESS, progress_item)

            pending = entry.pending_baseline_count
            pending_item = QTableWidgetItem(f"{pending:,}" if pending else dash)
            pending_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, SCAN_ACCT_PENDING, pending_item)

            measured_item = QTableWidgetItem(
                formatting.format_kb(entry.measured_kb) if entry.measured_kb else dash
            )
            measured_item.setToolTip(i18n.t("scan.acct.measured_tip"))
            measured_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, SCAN_ACCT_MEASURED, measured_item)

            # 남은 시간은 **도는 중일 때만** 뜻이 있다. 멈춰 있는데 "약 2시간"이
            # 떠 있으면 지금도 돌고 있다는 오해를 만든다.
            eta_text = dash
            if running and entry.eta_seconds:
                eta_text = i18n.t(
                    "scan.acct.eta_value", duration=_format_duration(entry.eta_seconds)
                )
            elif running and pending:
                # 표본이 모자라 아직 못 재는 상태. 빈칸으로 두면 "안 나온다"와
                # "0이다"가 구분되지 않는다.
                eta_text = i18n.t("scan.acct.eta_unknown")
            eta_item = QTableWidgetItem(eta_text)
            eta_item.setToolTip(i18n.t("scan.acct.eta_tip"))
            eta_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, SCAN_ACCT_ETA, eta_item)

            last_item = QTableWidgetItem(
                formatting.scan_label(entry.current_scan_at, entry.last_completed_generation)
                if entry.last_completed_generation
                else i18n.t("scan.acct.never")
            )
            # 가운데 정렬로 왼쪽 숫자 열과 떼어 놓는다. 왼쪽 붙임으로 두면
            # 오른쪽 정렬된 '예상 남은 시간'과 붙어 한 덩어리로 읽힌다.
            last_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row, SCAN_ACCT_LAST, last_item)

            notes = []
            if entry.failed_count:
                notes.append(i18n.t("scan.acct.note_failed", count=entry.failed_count))
            if entry.partial_paths:
                notes.append(
                    i18n.t("scan.acct.note_partial", count=len(entry.partial_paths))
                )
            note_item = QTableWidgetItem("  ·  ".join(notes) if notes else "")
            if entry.failed_count or entry.partial_paths:
                note_item.setForeground(QColor(tiers.color(tiers.WARN)))
            table.setItem(row, SCAN_ACCT_NOTE, note_item)

    def _refresh_large_files(self) -> None:
        """가장 큰 파일 표.

        디렉터리 합계만 보면 300GB 짜리가 고른 파일 3천 개인지 한 파일이
        280GB 인지 구분되지 않는다. 후자는 사람이 바로 손댈 수 있는 것이라
        따로 보여 줄 값어치가 있다.
        """

        table = self.large_table
        table.setRowCount(0)
        snapshot = self._scan_snapshot
        account_id = self.scan_account_combo.currentData()
        if snapshot is None or not account_id:
            self.large_caption.setText(i18n.t("large.no_scan"))
            return
        entry = next(
            (item for item in snapshot.accounts if item.account_id == account_id), None
        )
        if entry is None:
            self.large_caption.setText(i18n.t("large.no_scan"))
            return

        files = large_files.build(entry.large_files, entry.measured_kb)
        if not files:
            self.large_caption.setText(
                i18n.t("large.none", account=entry.account_name)
            )
            return

        self.large_caption.setText(i18n.t("large.heading"))
        shown = files[:LARGE_ROWS_SHOWN]
        table.setRowCount(len(shown))
        dash = i18n.t("common.none")
        for row, item in enumerate(shown):
            path_item = QTableWidgetItem(item.path)
            reasons = [
                i18n.t(
                    key,
                    pct=f"{item.share_pct:.0f}" if item.share_pct else "-",
                    ratio=(
                        f"{item.size_kb / item.previous_kb:.1f}"
                        if item.previous_kb else "-"
                    ),
                )
                for key in item.reasons()
            ]
            if reasons:
                path_item.setToolTip("\n".join(reasons))
            table.setItem(row, LARGE_PATH, path_item)

            table.setItem(
                row, LARGE_SIZE,
                widgets.NumericItem(formatting.format_kb(item.size_kb), item.size_kb),
            )

            share = item.share_pct
            share_item = widgets.NumericItem(
                f"{share:.1f}%" if share is not None else dash, share or 0
            )
            table.setItem(row, LARGE_SHARE, share_item)

            if item.previous_kb is None:
                change_item = QTableWidgetItem(i18n.t("large.new"))
            else:
                change_item = widgets.NumericItem(
                    formatting.format_kb_delta(item.delta_kb), item.delta_kb
                )
            table.setItem(row, LARGE_CHANGE, change_item)

            # 눈에 띄는 것만 색을 준다. 전부 칠하면 아무것도 강조되지 않는다.
            if item.is_notable:
                for column in range(len(LARGE_COLUMN_KEYS)):
                    cell = table.item(row, column)
                    if cell is not None:
                        cell.setForeground(QColor(tiers.color(tiers.WARN)))

    def _on_scan_account_chosen(self) -> None:
        """콤보에서 계정을 고르면 현황 표의 해당 행도 함께 짚어 준다.

        예전에는 표 -> 콤보 방향만 맞췄다. 그러면 콤보로 계정을 바꿔도 표는
        엉뚱한 행이 선택된 채 남아, **두 조작이 서로 다른 것을 가리키는 것처럼**
        보였다. 표를 눌러서 바꿀 수 있다는 것도 그래서 눈에 안 들어왔다.
        """

        self._refresh_growth_table()
        self._refresh_large_files()

        account_id = self.scan_account_combo.currentData()
        table = self.scan_accounts_table
        for row in range(table.rowCount()):
            item = table.item(row, SCAN_ACCT_NAME)
            if item is not None and item.data(Qt.UserRole) == account_id:
                if table.currentRow() != row:
                    # 표 선택이 다시 콤보를 건드리지 않게 막는다 (무한 왕복).
                    table.blockSignals(True)
                    table.selectRow(row)
                    table.blockSignals(False)
                return

    def _on_scan_account_row_selected(self) -> None:
        """현황 표에서 고른 계정을 아래 증가 경로 콤보에도 맞춘다."""

        row = self.scan_accounts_table.currentRow()
        if row < 0:
            return
        item = self.scan_accounts_table.item(row, SCAN_ACCT_NAME)
        if item is None:
            return
        account_id = item.data(Qt.UserRole)
        index = self.scan_account_combo.findData(account_id)
        if index >= 0 and index != self.scan_account_combo.currentIndex():
            self.scan_account_combo.setCurrentIndex(index)

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
            # 기준선이 없는 이유가 "아직 안 돌았다"가 아니라 "돌았는데 전부
            # 실패했다"일 수 있다. 그 경우 실패 사유를 보여 주지 않으면 스캔이
            # 즉시 끝나 버린 것처럼만 보인다.
            if entry is not None and entry.failed_count:
                self.growth_caption.setText(self._scan_failure_text(entry, account.name))
            else:
                self.growth_caption.setText(i18n.t("scan.no_baseline", account=account.name))
            return

        # 권한 부족으로 축소 측정된 경로가 있으면 반드시 알린다 - 모르고 보면
        # "안 늘었네"로 잘못 읽는다.
        notice = ""
        if entry.partial_paths:
            notice += "\n" + i18n.t(
                "scan.partial_warning", count=len(entry.partial_paths)
            )
        if entry.failed_count:
            notice += "\n" + self._scan_failure_text(entry, account.name)

        if entry.growth:
            self.growth_caption.setText(
                i18n.t(
                    "scan.growth_caption",
                    account=account.name,
                    current=formatting.scan_label(
                        entry.current_scan_at, entry.last_completed_generation
                    ),
                    previous=formatting.scan_label(
                        entry.previous_scan_at,
                        (entry.last_completed_generation or 1) - 1,
                    ),
                    notice=notice,
                )
            )
            self.growth_table.setSortingEnabled(False)
            # 열 제목에 비교 대상 날짜를 박는다 - "이전 스캔 대비"보다
            # "260819 대비"가 무엇과 비교한 값인지 스스로 설명한다.
            self.growth_table.setHorizontalHeaderItem(
                2,
                QTableWidgetItem(
                    i18n.t(
                        "scan.col.delta_dated",
                        previous=formatting.scan_label(
                            entry.previous_scan_at,
                            (entry.last_completed_generation or 1) - 1,
                        ),
                    )
                    if entry.previous_scan_at
                    else i18n.t("scan.col.delta")
                ),
            )
            self.growth_table.setRowCount(len(entry.growth))
            for index, row in enumerate(entry.growth):
                current_kb = row["current_kb"]
                previous_kb = row["previous_kb"]
                self.growth_table.setItem(index, 0, QTableWidgetItem(row["path"]))
                self.growth_table.setItem(
                    index, 1, widgets.NumericItem(formatting.format_kb(current_kb), current_kb)
                )
                if previous_kb is None:
                    delta_text = i18n.t("scan.new_path")
                else:
                    delta_text = formatting.format_kb_delta(current_kb - previous_kb)
                delta_value = (
                    current_kb - previous_kb if previous_kb is not None else current_kb
                )
                self.growth_table.setItem(
                    index, 2, widgets.NumericItem(delta_text, delta_value)
                )
            return

        self.growth_caption.setText(
            i18n.t(
                "scan.baseline_only_caption",
                account=account.name,
                current=formatting.scan_label(
                    entry.current_scan_at, entry.last_completed_generation
                ),
                notice=notice,
            )
        )
        self.growth_table.setSortingEnabled(False)
        self.growth_table.setRowCount(len(entry.top_paths))
        dash = i18n.t("common.none")
        for index, row in enumerate(entry.top_paths):
            self.growth_table.setItem(index, 0, QTableWidgetItem(row["path"]))
            self.growth_table.setItem(
                index, 1, widgets.NumericItem(formatting.format_kb(row["size_kb"]), row["size_kb"])
            )
            self.growth_table.setItem(index, 2, widgets.NumericItem(dash, None))

        # 채우기가 끝난 뒤 정렬을 되살린다. 기본은 **크기 내림차순** -
        # 상세 스캔을 보는 이유가 '무엇이 제일 큰가'이기 때문이다.
        # 사용자가 헤더를 눌러 바꾼 정렬은 Qt가 유지해 준다.
        if not self._growth_sort_touched:
            self.growth_table.sortItems(1, Qt.DescendingOrder)
        self.growth_table.setSortingEnabled(True)

    def _trigger_scan_now(self) -> None:
        # 안내를 보고 방금 cron 을 등록한 뒤 누르는 경우가 많다. 그때 빨간
        # 글씨가 그대로 남아 있으면 등록이 안 된 줄 알고 또 돌리게 된다.
        self._check_cron_async()
        if not self._get_config().accounts:
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
            self.status_message.emit(i18n.t("scan.already_running"))
            return
        # 손으로 돌린 스캔만 남긴다. cron 이 돈 것은 `scan_runs` 에 이미 있고,
        # 여기서 알고 싶은 것은 **사람이 직접 돌리는 일이 있는가**다.
        usage_log.record(self._data_dir, usage_log.SCAN_STARTED)
        # 22시에 손으로 돌렸으면 그 밤은 처리된 것이다 - 표시해 두지 않으면
        # 자동 쪽이 같은 밤을 한 번 더 시작한다.
        self._remember_scan_window()
        self.status_message.emit(i18n.t("scan.started"))
        self.refresh()

    def _check_cron_async(self) -> None:
        """cron 상태를 백그라운드에서 다시 읽는다.

        겹쳐 돌리지 않는다. `crontab` 이 느린 장비에서 요청이 쌓이면 그때부터
        더 나빠질 뿐이고, 어차피 다음 주기에 최신값을 읽는다."""

        if self._cron_checking:
            return
        self._cron_checking = True
        threading.Thread(
            target=self._load_cron_status, name="smvwp-cron-check", daemon=True
        ).start()

    def _load_cron_status(self) -> None:
        """cron 상태를 읽어 둔다 (작업 스레드).

        위젯은 여기서 건드리지 않는다 - Qt 위젯은 GUI 스레드에서만 만져야
        한다. 값만 담아 두고 그리기는 다음 새로고침이 한다."""

        try:
            self._cron_status = cron_status.read_status()
        except Exception:  # pragma: no cover - 진단 표시가 창을 죽이면 안 된다
            self._cron_status = None
        finally:
            self._cron_checking = False

    def _render_cron_status(self) -> None:
        status = self._cron_status
        if status is None:
            self.cron_status_label.setVisible(False)
            return
        auto = bool(getattr(self._get_config().settings, "gui_auto_nightly_scan", False))
        key = cron_status.summary_key(status)
        # 창이 밤을 지키는 설정이면 야간 줄이 없는 것이 정상이다 - 그때까지
        # 경고하면 "고치라"는 잘못된 신호가 된다.
        if auto and key == "cron.nightly_missing":
            key = "cron.nightly_by_gui"
        self.cron_status_label.setText(i18n.t(key))
        warn = key in ("cron.none", "cron.nightly_missing")
        label = self.cron_status_label
        label.setObjectName("captionWarn" if warn else "caption")
        # objectName 을 바꿔도 **이미 그려진 위젯**은 스타일을 다시 안 고른다.
        # 이 라벨이 그렇다 - 첫 렌더 때는 cron 상태가 아직 안 와서 이름을 안
        # 바꾸고, 이름은 30초 뒤 타이머에서 바뀐다. `setStyleSheet("")` 로
        # 되물리려 했었는데 빈 스타일시트가 그대로 빈 것이면 Qt 는 아무것도
        # 하지 않는다. unpolish/polish 가 정식 방법이다.
        style = label.style()
        style.unpolish(label)
        style.polish(label)
        label.setVisible(True)

    def _maybe_start_nightly_scan(self) -> None:
        """시간창에 들어왔으면 스스로 시작한다 (밤마다 한 번).

        판단은 `auto_scan.should_start`에 있다 - Qt 없이 시험할 수 있어야
        하기 때문이다. 여기서는 그 답에 따라 실행만 한다.
        """

        now = datetime.now()
        if not auto_scan.should_start(
            now,
            self._get_config().settings,
            self._scan_worker.is_running(),
            self._auto_scan_started_key,
        ):
            return
        # 시작을 **먼저** 기록한다. 아래 호출이 잠금 때문에 실패하더라도(cron이
        # 이미 돌고 있는 경우) 이 밤은 이미 처리된 것으로 봐야 한다 - 안 그러면
        # 1분마다 다시 시도한다.
        self._remember_scan_window(now)
        if self._scan_worker.run_async(bypass_window=False):
            self.status_message.emit(i18n.t("scan.auto_started"))
            self.refresh()

    def _remember_scan_window(self, now=None) -> None:
        """이 밤에 스캔을 시작했다고 표시한다.

        수동 실행에서도 부른다 - 22시에 손으로 돌린 뒤 자동이 또 시작하면
        같은 밤을 두 번 훑게 된다."""

        settings = self._get_config().settings
        key = auto_scan.window_key(
            now or datetime.now(),
            settings.detail_scan_window_start_hour,
            settings.detail_scan_window_end_hour,
        )
        if key is not None:
            self._auto_scan_started_key = key

    def _request_scan_stop(self) -> None:
        if self._scan_worker.request_stop():
            usage_log.record(self._data_dir, usage_log.SCAN_STOPPED)
            self.status_message.emit(i18n.t("scan.stop_requested"))
        else:
            self.status_message.emit(i18n.t("scan.nothing_running"))
        self.refresh()

    def _on_scan_finished(self, summary) -> None:
        if not summary.started:
            self.status_message.emit(i18n.t("scan.not_started", reason=summary.reason))
        else:
            self.status_message.emit(i18n.t("scan.finished", status=summary.status))
        self.refresh()

    def _on_scan_failed(self, message: str) -> None:
        self.status_message.emit(i18n.t("scan.failed", message=message))
        self.refresh()

    def _stop_scan_for_shutdown(self) -> None:
        """스캔을 멈추고 흔적을 정리한다 (창을 닫는 경로 전용).

        순서가 중요하다. 먼저 중지 요청을 써 둬야, 자식을 죽인 뒤 스캐너가
        잠깐 더 진행하더라도 다음 체크포인트에서 확실히 멈춘다."""

        self._scan_worker.request_stop()
        # 진행 중이던 디렉터리 하나의 결과는 잃지만, 그 체크포인트는 pending으로
        # 남아 다음 스캔이 거기서 이어받는다. 창을 닫는 사람의 의도는 "그만"이다.
        terminated = procio.terminate_children()
        try:
            nightly_scan.mark_interrupted_run(self._data_dir)
        except Exception:  # pragma: no cover - 종료 경로에서 예외로 막히면 안 된다
            pass
        if terminated:
            self.status_message.emit(i18n.t("scan.stopped_on_close", count=terminated))
