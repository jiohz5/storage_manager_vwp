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
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from .. import auto_scan, config as config_module, cron_status, formatting, i18n
from .. import nightly_scan, procio, scan_digest, usage_log
from ..scheduler import NightlyScanWorker, ScanStatusWorker
from .scan_pages import (
    TAB_GROWTH,
    TAB_KEYS,
    TAB_SUMMARY,
    AccountsPage,
    GrowthPage,
    LargeFilesPage,
    SummaryPage,
    status_text,
)
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
        self.scan_account_label.setText(i18n.t("scan.account_label"))
        self._sync_scan_account_combo()
        self.scan_run_btn.setText(i18n.t("scan.btn.run_now"))
        self.scan_run_btn.setToolTip(i18n.t("scan.btn.run_now_tooltip"))
        self.scan_stop_btn.setText(i18n.t("scan.btn.stop"))
        self.scan_stop_btn.setToolTip(i18n.t("scan.btn.stop_tooltip"))
        self.scan_detail_btn.setText(i18n.t("scan.btn.progress"))
        self.scan_detail_btn.setToolTip(i18n.t("scan.btn.progress_tooltip"))
        self.tree_btn.setText(i18n.t("tree.btn.open"))
        for page in self._pages:
            page.retranslate()
        for index, key in enumerate(TAB_KEYS):
            self.detail_tabs.setTabText(index, i18n.t(key))

    def _build(self) -> None:
        """상세 스캔 화면.

        ## 한 범주가 한 탭을 통째로 쓴다

        처음에는 표 넷을 세로로 쌓았고, 다음에는 세부만 탭으로 나눴다. 둘 다
        "여러 칸이 한눈에 보이되 칸마다 몇 줄 안 보이는" 화면이었다 - 위에
        카드와 상태가 300px 을 먹고, 증가 경로와 큰 파일은 반씩 나눠 경로
        뒤쪽(정작 다른 부분)이 잘렸다.

        그래서 **늘 보이는 것은 지금 상태 몇 줄뿐**이고, 나머지는 범주마다
        한 탭씩 전폭·전고를 쓴다:

        - 요약: 카드 넷과 살펴볼 것. 누르면 해당 계정의 세부 탭으로 간다.
        - 계정별: 진행·측정량·남은 시간.
        - 증가 경로 / 큰 파일: 고른 계정 하나.

        ## 계정 선택은 탭 줄 오른쪽에

        증가 경로·큰 파일·폴더 펼쳐 보기가 모두 "고른 계정" 을 쓴다. 그 선택이
        어느 한 탭 안에 있으면 다른 탭에서 무엇을 보고 있는지 모른다.

        `QTabWidget` 의 모서리 위젯으로 두지 않는다. 모서리 위젯의 높이는 탭
        높이로 잘리는데, 콤보가 세로로 잘리는 사고가 이미 세 번 있었다. 탭 띠
        (`QTabBar`)와 쪽 묶음(`QStackedWidget`)을 따로 두고 한 줄에 놓으면 줄
        높이가 둘 중 큰 쪽을 따른다.
        """

        # 예전 `_build_scan_section` 이 만들던 QFrame 과 같은 모양.
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("card")

        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 12)
        box.setSpacing(4)

        # -- 머리: 지금 상태 한 줄 + 동작 --------------------------------
        #
        # 예전에는 "상세 스캔 - 밤마다 ..." 제목을 세웠는데 바깥 탭 이름과 같은
        # 말이었다. 그 자리에 **지금 상태**를 세운다 - 이 화면을 열면 가장
        # 먼저 묻는 것이다.
        head = QHBoxLayout()
        head.setSpacing(8)
        self.scan_headline_label = QLabel()
        self.scan_headline_label.setObjectName("sectionTitle")
        head.addWidget(self.scan_headline_label)
        head.addStretch(1)

        self.scan_detail_btn = QPushButton()
        self.scan_detail_btn.clicked.connect(self._open_scan_progress)
        self.scan_run_btn = QPushButton()
        self.scan_run_btn.clicked.connect(self._trigger_scan_now)
        self.scan_stop_btn = QPushButton()
        # 중지는 되돌릴 수 없는 성격의 동작이라 색으로 구분해 둔다 (강제 종료는
        # 아니지만, 실수로 누르면 진행 중인 밤을 날린다).
        self.scan_stop_btn.setObjectName("danger")
        self.scan_stop_btn.clicked.connect(self._request_scan_stop)
        for button in (self.scan_detail_btn, self.scan_run_btn, self.scan_stop_btn):
            head.addWidget(button)
        box.addLayout(head)

        # 숫자들. 한 줄에 모자라면 접힌다 - 잘라 버리면 뒤쪽 CPU 가 안 보인다.
        self.scan_status_label = QLabel()
        self.scan_status_label.setObjectName("muted")
        self.scan_status_label.setWordWrap(True)
        box.addWidget(self.scan_status_label)

        # 지금 어느 경로를 훑고 있는지. 한 디렉터리가 몇 분씩 걸릴 수 있어서
        # "실행 중"만 떠 있으면 멈춘 것인지 진행 중인지 구분되지 않는다.
        self.scan_current_label = QLabel()
        self.scan_current_label.setObjectName("caption")
        self.scan_current_label.setWordWrap(True)
        self.scan_current_label.setVisible(False)
        box.addWidget(self.scan_current_label)

        # cron 등록 여부. 야간 스캔이 안 도는 가장 흔한 이유가 "등록이 안 된
        # 것"인데, 그 사실은 아무 데도 드러나지 않아 사람은 프로그램이 고장 난
        # 줄 안다. 다음 날 아침 보고서가 비어 있어야 알아채고, 그때는 이미
        # 하룻밤을 버린 뒤다.
        self.cron_status_label = QLabel()
        self.cron_status_label.setObjectName("caption")
        self.cron_status_label.setWordWrap(True)
        box.addWidget(self.cron_status_label)

        # 도는 동안만 보이는 진행 막대. 분모가 도중에 늘 수 있다는 사실은
        # 상태 줄 툴팁에 적는다 (DESIGN.md 1부 "과장하지 않는 UI").
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 0)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setFixedHeight(6)
        self.scan_progress.setVisible(False)
        box.addWidget(self.scan_progress)

        box.addSpacing(8)

        # -- 탭 줄: 탭 + 계정 선택 ----------------------------------------
        strip = QHBoxLayout()
        strip.setSpacing(8)
        self.detail_tabs = QTabBar()
        self.detail_tabs.setObjectName("subTabs")
        self.detail_tabs.setDrawBase(False)
        self.detail_tabs.setExpanding(False)
        strip.addWidget(self.detail_tabs, 0, Qt.AlignBottom)
        strip.addStretch(1)

        self.scan_account_label = QLabel()
        self.scan_account_label.setObjectName("muted")
        self.scan_account_combo = QComboBox()
        self.scan_account_combo.setMinimumWidth(220)
        self.scan_account_combo.currentIndexChanged.connect(self._on_scan_account_chosen)
        # 계정 안을 파고드는 화면. 증가 경로를 보다가 "그래서 그 300GB 가
        # 어디야" 를 묻게 되므로 계정 선택 바로 옆에 둔다.
        self.tree_btn = QPushButton(i18n.t("tree.btn.open"))
        self.tree_btn.clicked.connect(self._open_tree)
        strip.addWidget(self.scan_account_label, 0, Qt.AlignVCenter)
        strip.addWidget(self.scan_account_combo, 0, Qt.AlignVCenter)
        strip.addWidget(self.tree_btn, 0, Qt.AlignVCenter)
        box.addLayout(strip)

        rule = QFrame()
        rule.setObjectName("subTabsRule")
        rule.setFixedHeight(1)
        box.addWidget(rule)

        self.detail_stack = QStackedWidget()
        self.detail_stack.setObjectName("subPages")
        box.addWidget(self.detail_stack, 1)
        self.detail_tabs.currentChanged.connect(self.detail_stack.setCurrentIndex)

        # 쪽은 그리기만 한다. 사람이 쪽에서 무엇을 고르면 신호로 알려 오고,
        # 계정 선택과 탭 옮기기는 여기서 한다 (`scan_pages` 참고).
        self.summary_page = SummaryPage()
        self.accounts_page = AccountsPage()
        self.growth_page = GrowthPage()
        self.large_page = LargeFilesPage()
        self._pages = (
            self.summary_page, self.accounts_page, self.growth_page, self.large_page,
        )
        for page, key in zip(self._pages, TAB_KEYS):
            self.detail_stack.addWidget(page)
            self.detail_tabs.addTab(i18n.t(key))
        self.summary_page.finding_chosen.connect(self._on_finding_chosen)
        self.accounts_page.account_selected.connect(self._on_account_selected)
        self.accounts_page.account_opened.connect(self._on_account_opened)
        self.detail_tabs.setCurrentIndex(TAB_SUMMARY)

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
        return i18n.t(
            "scan.current_target",
            account=account.name if account else i18n.t("common.none"),
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

        latest = snapshot.latest_run
        pending_total = sum(
            item.pending_baseline_count for item in snapshot.accounts
        )
        done_total = sum(item.baseline_done for item in snapshot.accounts)
        total_total = sum(item.baseline_total for item in snapshot.accounts)
        percent = int(done_total * 100 / total_total) if total_total else None

        # 머리 한 줄 - 지금 도는가, 안 돈다면 지난번은 어떻게 끝났나.
        if running:
            headline = (
                i18n.t("scan.headline.running_pct", percent=percent)
                if percent is not None
                else i18n.t("scan.headline.running")
            )
        elif latest:
            headline = i18n.t(
                "scan.headline.idle", status=status_text(latest["status"])
            )
        else:
            headline = i18n.t("scan.headline.never")
        self.scan_headline_label.setText(headline)

        # 그 밑의 숫자들.
        #
        # 진행률을 숫자와 막대로 함께 보여준다. 분모(total)는 진행 중 늘어날
        # 수 있지만 (시간 초과로 디렉터리를 쪼개면 작업이 추가된다) 그 사실을
        # **툴팁에 적어 두면** 숫자를 지어내는 것이 아니다. 문장에 매번 붙여
        # 두었더니 상태 줄이 두 줄로 접혀 탭 자리를 먹었다.
        parts = []
        if total_total:
            self.scan_progress.setRange(0, total_total)
            self.scan_progress.setValue(done_total)
            parts.append(
                i18n.t(
                    "scan.progress_counts",
                    done=done_total, total=total_total, percent=percent,
                )
            )
        else:
            self.scan_progress.setRange(0, 0)
        # 멈춰 있어도 남은 작업은 보여 준다 - 아침에 멈춘 밤은 다음 밤에
        # 이어서 돈다는 뜻이다.
        if pending_total:
            parts.append(i18n.t("scan.pending_tasks", count=pending_total))
        if latest:
            parts.append(
                i18n.t(
                    "scan.latest_started",
                    started_at=formatting.local_minute_text(latest["started_at"]),
                )
            )
        if snapshot.window_description:
            parts.append(snapshot.window_description)
        cpu_text = formatting.scan_cpu_text(latest)
        if cpu_text:
            parts.append(cpu_text)
        self.scan_status_label.setText("  ·  ".join(parts))
        self.scan_status_label.setToolTip(
            i18n.t("scan.progress_tip") if total_total else ""
        )

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
        self.accounts_page.show_accounts(snapshot, self._get_config().accounts, running)
        self._refresh_growth_table()
        self._refresh_large_files()

    # -- 요약 ------------------------------------------------------------
    def _refresh_digest(self, snapshot) -> None:
        """카드 넷과 살펴볼 것 목록."""

        self.summary_page.show_digest(scan_digest.build(snapshot))

    def _refresh_large_files(self) -> None:
        self.large_page.show_account(self._scan_snapshot, self._selected_account())

    def _refresh_growth_table(self) -> None:
        self.growth_page.show_account(self._scan_snapshot, self._selected_account())

    # -- 쪽에서 온 신호 ---------------------------------------------------
    def _on_finding_chosen(self, account_id: str, tab: int) -> None:
        """살펴볼 것 한 줄 -> 그 계정의 해당 탭."""

        self.select_account(account_id)
        self.detail_tabs.setCurrentIndex(tab)

    def _on_account_selected(self, account_id: str) -> None:
        """현황 표에서 고른 계정을 탭 줄의 계정 선택에도 맞춘다."""

        index = self.scan_account_combo.findData(account_id)
        if index >= 0 and index != self.scan_account_combo.currentIndex():
            self.scan_account_combo.setCurrentIndex(index)

    def _on_account_opened(self, account_id: str) -> None:
        """현황 표에서 두 번 누른 계정의 증가 경로로 간다."""

        self.select_account(account_id)
        self.detail_tabs.setCurrentIndex(TAB_GROWTH)

    def _on_scan_account_chosen(self) -> None:
        """콤보에서 계정을 고르면 현황 표의 해당 행도 함께 짚어 준다.

        예전에는 표 -> 콤보 방향만 맞췄다. 그러면 콤보로 계정을 바꿔도 표는
        엉뚱한 행이 선택된 채 남아, **두 조작이 서로 다른 것을 가리키는 것처럼**
        보였다. 표를 눌러서 바꿀 수 있다는 것도 그래서 눈에 안 들어왔다.
        """

        self._refresh_growth_table()
        self._refresh_large_files()
        self.accounts_page.mark_account(self.scan_account_combo.currentData())

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
