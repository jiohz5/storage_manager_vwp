"""메인 창 - 홈(현황) / 상세 스캔 두 탭.

DESIGN.md 2부 6절은 "대시보드 단일 화면(탭 없음)"으로 결정했었다. 실제로 써
보니 **상세 스캔 영역이 세로 공간을 너무 많이 가져가** 매일 보는 현황이 가끔
보는 스캔에 밀렸다. 스플리터로 비율을 조절하게 해 봤지만 그건 "매번 사용자가
정리해야 한다"는 뜻이라 근본 해결이 아니었다. 그래서 탭 둘로 나눴다 (3부
"화면 구조 - 단일 화면에서 탭 둘로" 참고).

유지되는 것:
- "한눈 요약 우선": 홈 최상단에 타일 넷(가장 찬 곳, 손볼 계정, 상세 스캔 한 바퀴,
  끝 예상) - 2026-11 개편 전에는 히어로 하나였다 (`_build_ui` 참고).
- 계정 등록/설정·보고서·검색은 여전히 다이얼로그다 (탭을 더 늘리지 않는다).

모든 표시 문자열은 `i18n.t`를 거친다. 언어를 바꾸면 `retranslate()`가 정적
문자열을, 이어지는 갱신이 동적 문자열을 다시 그린다 - 앱을 다시 시작할 필요가
없다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QShortcut,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_module
from .. import formatting
from .. import action_guide
from .. import priority
from .. import usage_log
from .. import (
    dashboard,
    scan_overview,
    diagnostics,
    forecast_notify,
    freshness,
    i18n,
    store,
    tiers,
)
from ..scheduler import CollectorScheduler, DashboardWorker, ThreadWorker
from . import theme, widgets
from .overview_widgets import Chip, SegmentBar, TimelineBar
from .scan_tab import ScanTab
from .account_dialog import AccountDialog
from .first_run import FirstRunDialog
from .reports_dialog import ReportsDialog
from .search_dialog import SearchDialog

logger = logging.getLogger(__name__)

# 홈 오른쪽 칸('무엇부터 할까', '오늘 밤 상세 스캔')의 폭. 남는 가로는 계정 표가 쓴다.
RIGHT_COLUMN_WIDTH = 400

# 처음 여는 창 크기. 오른쪽 칸(400)을 빼고도 계정 표 여덟 열이 가로 스크롤 없이
# 들어가는 폭이다 (1,200 에서는 표가 750 폭이라 옆으로 밀렸다). 화면이 그보다
# 작으면 화면에 맞춘다 - 실기 화면은 세로 1,080 이 최대다.
DEFAULT_SIZE = (1600, 960)
MINIMUM_SIZE = (960, 700)


def default_size(screen) -> tuple:
    """`screen` 의 쓸 수 있는 영역 안에 드는 처음 창 크기 (가장자리 40 은 남긴다)."""

    width, height = DEFAULT_SIZE
    if screen is None:
        return DEFAULT_SIZE
    available = screen.availableGeometry()
    return (
        max(MINIMUM_SIZE[0], min(width, available.width() - 40)),
        max(MINIMUM_SIZE[1], min(height, available.height() - 40)),
    )

COLUMN_KEYS = [
    "dashboard.col.name",
    # 성격(프로젝트/백업)을 이름 바로 옆에 둔다. 이 열이 없으면 백업 계정이
    # 계속 느는 것을 보고 매번 놀라게 된다 - 백업은 단조 증가가 정상이고
    # 프로젝트는 과제가 끝나면 줄어야 정상이라, 같은 숫자를 다르게 읽어야 한다.
    "dashboard.col.kind",
    "dashboard.col.size",
    "dashboard.col.byte_pct",
    # 사용률 옆에 추세선을 둔다. "지금 82%" 보다 "두 주 만에 60%에서 82%로
    # 왔다" 가 훨씬 많은 것을 말하는데, 그 둘은 붙어 있어야 한 번에 읽힌다.
    "trend.col",
    "forecast.column",
    # 그 계정의 상세 스캔이 어디쯤인가 (`scan_overview.home_chip`). 실기에서 "어디까지
    # 스캔됐는지 모르겠다" 고 했다 - 계정을 훑는 자리에서 함께 보여야 한다.
    "dashboard.col.scan",
    # 수집이 멈춘 계정을 알리는 유일한 칸이라 남긴다 (시안에는 머리줄의 '마지막
    # 수집' 하나뿐이었다).
    "dashboard.col.collected_at",
]
(
    COL_NAME,
    COL_KIND,
    COL_SIZE,
    COL_BYTE,
    COL_TREND,
    COL_FORECAST,
    COL_SCAN,
    COL_TIME,
) = range(8)

# 이 표는 **훑어보는 목록**이다.
#
# 예전에는 열이 열둘이라 폭이 1,400px 필요했고, 창이 1,200 이면 그것만으로
# 가로 스크롤이 났다. 홈을 두 칸으로 나눈 뒤로는 표가 쓸 수 있는 폭이 더
# 줄어 더 심해졌다.
#
# 그래서 "어느 계정을 지금 봐야 하나"에 답하는 것만 남겼다. 나머지(경로,
# inode, quota, 수집 상태, 등급 글자)는 **계정을 두 번 누르면 열리는 상세
# 창**에 전부 있다 - 그것들은 한 계정을 정한 다음에 보는 값이다.

# `파일시스템` 열은 뺐다. 값이 거의 항상 같아서(계정 대부분이 같은 파일시스템에
# 있다) 열 하나를 통째로 쓰면서 정보는 거의 주지 않았다. 대신 경로 툴팁에
# 넣는다 - 필요한 순간(어느 파일시스템인지 확인할 때)에만 보면 되는 값이다.

class _Tile(QFrame):
    """홈 맨 위의 타일 하나 - 이름, 큰 값, 그 옆 한마디, 막대나 알약, 설명 한두 줄.

    값을 먼저 세우고 설명은 작게 둔다. 표를 읽고 스스로 요약을 만들게 하는 대신
    답을 먼저 보여 주기 위한 자리다 (`widgets.StatCard` 와 같은 생각, 칸이 더 많다).
    `link` 면 눌러서 다른 쪽으로 간다 - 손 모양 커서와 테두리로 알린다."""

    clicked = pyqtSignal()

    def __init__(self, link: bool = False, parent=None):
        super().__init__(parent)
        self._link = link
        self.setObjectName("tileLink" if link else "card")
        if link:
            self.setCursor(Qt.PointingHandCursor)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 14, 18, 14)
        box.setSpacing(6)
        self.caption = QLabel()
        self.caption.setObjectName("statLabel")
        box.addWidget(self.caption)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.value = QLabel("-")
        self.value.setObjectName("display")
        top.addWidget(self.value, 0, Qt.AlignBottom)
        self.side = QLabel()
        self.side.setObjectName("muted")
        self.side.setVisible(False)   # 쓰는 타일만 켠다 (빈 칸 몫 간격이 생기지 않게)
        top.addWidget(self.side, 0, Qt.AlignBottom)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        top.addLayout(self.chips)
        top.addStretch(1)
        box.addLayout(top)
        self.bar = SegmentBar(8)
        self.bar.setVisible(False)
        box.addWidget(self.bar)
        self.detail = QLabel()
        self.detail.setObjectName("caption")
        self.detail.setWordWrap(True)
        box.addWidget(self.detail)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        box.addWidget(self.note)
        box.addStretch(1)

    def set_chips(self, chips) -> None:
        """`[(글, 알약 종류), ...]` - 지난 것은 지운다."""

        while self.chips.count():
            item = self.chips.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for text, kind in chips:
            self.chips.addWidget(Chip(text, kind))

    def set_note(self, text: str, color: str = "") -> None:
        self.note.setText(text)
        self.note.setStyleSheet(
            f"font-weight: {theme.WEIGHT_BOLD}; color: {color or theme.TEXT_MUTED};"
            f" font-size: {theme.FONT_CAPTION + 1}px;"
        )
        self.note.setVisible(bool(text))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        if self._link and event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def evaluate_freshness(data_dir: Path, config) -> Dict[str, object]:
    """계정별 수집 신선도 `{계정id: 판정}` (읽기 전용). 꺼져 있으면 빈 dict."""

    if not config.settings.freshness_enabled:
        return {}
    return {item.account_id: item for item in freshness.evaluate_all(data_dir, config)}


class _FreshnessReader(ThreadWorker):
    """수집 신선도를 작업 스레드에서 판정한다.

    24시간치 표본을 계정마다 훑으므로, 데이터 디렉터리가 NFS 면 화면 스레드에서
    했을 때 창이 그만큼 늦게 떴다. 실패하면 경고가 안 뜰 뿐이다 (작업 스레드가
    로그에 남긴다)."""

    THREAD_NAME = "smvwp-freshness"

    def __init__(self, data_dir: Path, get_config, parent=None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._get_config = get_config

    def _work(self):
        return evaluate_freshness(self._data_dir, self._get_config())


class MainWindow(QMainWindow):
    def __init__(self, data_dir: Path, config: config_module.AppConfig):
        super().__init__()
        self._data_dir = data_dir
        self._config = config
        self._latest_samples: Dict[str, store.SampleRecord] = {}
        self._forecasts: Dict[str, object] = {}
        self._freshness: Dict[str, object] = {}
        self._trends: Dict[str, object] = {}
        i18n.set_language(config.settings.language)

        # 최소 크기를 함께 못박아, 창을 줄였을 때 표가 한 줄도 안 보이는 상태로
        # 무너지지 않게 한다. 그보다 좁으면 표가 옆으로 밀린다 (안 보이는 것보다 낫다).
        self.resize(*default_size(QApplication.primaryScreen()))
        self.setMinimumSize(*MINIMUM_SIZE)
        # 야간 상세 스캔.
        #
        # 두 갈래로 돌 수 있다. cron(`setup_cron.csh`)이 22:00에 띄우거나,
        # 이 창이 시간창을 지켜 스스로 시작하거나(`gui_auto_nightly_scan`).
        # 둘 다 켜 두어도 안전하다 - `scan_lock`이 프로세스를 가로질러 하나만
        # 돌게 막는다.
        #
        # 창이 시작한 스캔은 **창을 닫으면 함께 멈춘다**(`closeEvent`).
        #
        # **`_build_ui` 보다 먼저 만든다.** 화면을 짜는 쪽이 이 탭을 탭 띠에
        # 붙이고, `retranslate` 도 곧바로 이 탭에게 자기 문구를 채우라고 한다.
        self._scan_tab = ScanTab(data_dir, lambda: self._config, self)
        # 스캔 탭이 넘겨 준 상세 스캔 현황과 "지금 도는가" (홈 타일·오늘 밤 카드가 쓴다).
        self._overview = None
        self._scan_banner = None

        self._build_ui()

        # 신호는 화면을 짠 **뒤에** 잇는다 - 상태 줄 위젯이 `_build_ui` 안에서
        # 만들어지기 때문이다.
        self._scan_tab.status_message.connect(self.status_bar_label.setText)
        self._scan_tab.banner_changed.connect(self._update_home_scan_banner)
        self._scan_tab.overview_changed.connect(self._on_overview_changed)

        self.retranslate()

        # 수집 신선도는 **첫 수집을 돌리기 전에** 판정해야 한다. GUI를 열면
        # 스케줄러가 곧바로 한 번 수집하므로, 그 뒤에 보면 늘 "방금 수집됨"이
        # 되어 "그동안 cron이 안 돌았다"는 사실이 가려진다. 판정은 작업 스레드가
        # 하고(화면 스레드에서 하면 창이 늦게 뜬다), 첫 수집은 그 판정이 끝난 뒤에
        # 시작한다 (`_start_collecting`) - 작업자는 아래 수집기를 만든 뒤에 띄운다.
        self._collecting = False
        # 표본 읽기와 FULL 예측은 **GUI 스레드에서 하지 않는다.** 예측은
        # 계정마다 이력을 다시 훑으므로, 데이터 디렉터리가 NFS 위면 그동안
        # 창이 멈춘다 (스캔 상태 쪽은 이미 같은 이유로 스레드에 있다).
        self._dashboard_worker = DashboardWorker(
            data_dir, get_config=lambda: self._config, parent=self
        )
        self._dashboard_worker.finished.connect(self._on_dashboard_ready)
        self._dashboard_worker.failed.connect(self._on_dashboard_failed)
        self._dashboard_worker.progress.connect(self._on_dashboard_progress)
        # 홈이 보여 주는 가장 최근 표본 시각, 그리고 홈 읽기가 끝나면 견줄 시각
        # (`_on_collected_elsewhere`).
        self._dashboard_newest: Optional[str] = None
        self._newest_wanted: Optional[str] = None
        self._refresh_table_from_store()

        self._scheduler = CollectorScheduler(
            data_dir,
            get_config=lambda: self._config,
            on_finished=self._on_collection_finished,
            on_failed=self._on_collection_failed,
        )
        self._freshness_reader = _FreshnessReader(
            data_dir, get_config=lambda: self._config, parent=self
        )
        self._freshness_reader.finished.connect(self._on_freshness_ready)
        self._freshness_reader.failed.connect(lambda _message: self._start_collecting())
        self._freshness_reader.start()


    def show_first_run_if_needed(self) -> None:
        """계정이 하나도 없으면 시작 안내를 띄운다.

        생성자가 아니라 별도 메서드로 둔 이유: 창이 화면에 뜨기 전에 모달
        다이얼로그를 띄우면 부모 없는 창처럼 보이고, 테스트에서도 창을 만들 때
        마다 모달이 뜨면 곤란하기 때문. 호출은 `smvwp_cli.py gui`가 `show()` 뒤에 한다."""

        # 창을 실제로 띄운 경로에서만 부르는 메서드라(생성자는 시험도 부른다)
        # "사람이 이 도구를 열었다"를 여기서 남긴다. 시험이 창을 만들 때마다
        # 기록이 쌓이면 집계가 사람 것이 아니게 된다.
        usage_log.record_soon(self._data_dir, usage_log.GUI_OPENED)

        if self._config.accounts:
            return
        dialog = FirstRunDialog(self._data_dir, self._config, parent=self)
        dialog.exec_()
        if dialog.open_accounts:
            self._open_account_dialog()

    # -- UI 구성 -----------------------------------------------------
    def _build_ui(self) -> None:
        """머리줄 하나와 두 쪽(홈 / 상세 스캔).

        ## 시안대로 (2026-11 개편)

        실기에서 "어디까지 스캔되고 스토리지가 어떤 상황인지 판단이 힘들다" 고 했다.
        홈은 이제 위에서부터 묻는 순서대로 놓는다: 타일 넷(가장 찬 곳, 손볼 계정,
        상세 스캔 한 바퀴, 끝 예상) - 계정 표 - 오른쪽에 무엇부터 할까와 오늘 밤
        상세 스캔. 탭 띠 대신 머리줄의 두 단추로 오가고, 자주 안 쓰는 창들(검색·
        추세·서버 부하·진단·데이터 폴더)은 '도구' 메뉴로 모은다.

        ## 탭 둘은 그대로다

        예전에 한 화면에 세로로 쌓았더니 상세 스캔이 세로를 너무 가져갔다 (DESIGN.md
        2부 6절). 쪽은 여전히 `QTabWidget` 이고 띠만 숨긴다 - 쪽을 찾는 코드
        (`indexOf(self._scan_page)`)가 그대로 돈다.
        """

        self._build_menu()

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(theme.PAD_CARD, 14, theme.PAD_CARD, theme.PAD_CARD)
        outer.setSpacing(theme.GAP_SECTION)
        outer.addWidget(self._build_header())
        self.tabs = QTabWidget()
        self.tabs.tabBar().hide()
        outer.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        home = QWidget()
        layout = QVBoxLayout(home)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.GAP_SECTION)
        layout.addLayout(self._build_tiles())

        columns = QHBoxLayout()
        columns.setSpacing(theme.GAP_SECTION)
        left = QVBoxLayout()
        left.setSpacing(8)

        list_head = QHBoxLayout()
        list_head.setSpacing(10)
        self.list_title = QLabel()
        self.list_title.setObjectName("sectionTitle")
        list_head.addWidget(self.list_title)
        self.list_hint = QLabel()
        self.list_hint.setObjectName("caption")
        self.list_hint.setWordWrap(True)
        list_head.addWidget(self.list_hint, 1)
        left.addLayout(list_head)

        # 데이터를 읽는 동안의 진행. 실기(NFS)에서 창은 바로 떴지만 홈 데이터가
        # 보이기까지 15초쯤 걸렸고, 그동안 아무 표시가 없어 멈춘 줄 알았다.
        self.loading_row = QWidget()
        loading_box = QHBoxLayout(self.loading_row)
        loading_box.setContentsMargins(0, 0, 0, 0)
        loading_box.setSpacing(10)
        self.loading_label = QLabel()
        self.loading_label.setObjectName("caption")
        self.loading_bar = QProgressBar()
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedHeight(6)
        loading_box.addWidget(self.loading_label)
        loading_box.addWidget(self.loading_bar, 1)
        self.loading_row.setVisible(False)
        left.addWidget(self.loading_row)

        self.table = QTableWidget(0, len(COLUMN_KEYS))
        self.table.setObjectName(theme.TABLE_WITH_WIDGETS)
        self._configure_table_columns()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(False)  # 등급 색과 겹치면 판독을 방해한다
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setShowGrid(False)
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        # 한 계정에 대한 사실은 네 군데에 흩어져 있다 (사용률·추세·스캔·백업).
        # 두 번 누르면 그 넷을 한 창에 모아 준다.
        self.table.cellDoubleClicked.connect(self._open_account_detail)
        # 목록을 화살표로 훑다가 Enter 로 여는 사람이 있다. 마우스로만 열 수
        # 있으면 계정이 많을수록 손이 많이 간다.
        open_shortcut = QShortcut(QKeySequence(Qt.Key_Return), self.table)
        open_shortcut.setContext(Qt.WidgetShortcut)
        open_shortcut.activated.connect(
            lambda: self._open_account_detail(self.table.currentRow())
        )
        # 계정 표는 **Qt 내장 정렬을 쓰지 않는다.** 사용률 막대와 등급 배지가
        # 칸 위젯이라, Qt가 항목만 옮기고 위젯은 제자리에 두어 행과 위젯이
        # 어긋난다. 대신 정렬 키만 기억해 두고 표를 다시 그린다.
        # **기본은 사용률 내림차순.** 등록 순서로 두면 스무 개 넘는 목록에서
        # 정작 급한 계정이 가운데 어딘가에 있게 된다. 이 화면을 여는 이유가
        # "지금 어디가 급한가" 이므로 그 순서가 기본이어야 한다.
        self._sort_column = COL_BYTE
        self._sort_desc = True
        self._filter_text = ""
        self.table.setMinimumHeight(140)
        left.addWidget(self.table, 1)
        columns.addLayout(left, 1)

        right_column = QWidget()
        right_column.setFixedWidth(RIGHT_COLUMN_WIDTH)
        right = QVBoxLayout(right_column)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(theme.GAP_SECTION)
        right.addWidget(self._build_priority_card(), 1)
        right.addWidget(self._build_tonight_card())
        columns.addWidget(right_column)
        layout.addLayout(columns, 1)
        self.tabs.addTab(home, "")

        scan_tab = QWidget()
        scan_layout = QVBoxLayout(scan_tab)
        scan_layout.setContentsMargins(0, 0, 0, 0)
        scan_layout.addWidget(self._scan_tab, 1)
        # 쪽 묶음에 붙는 것은 이 컨테이너다 - `indexOf` 는 이것으로 해야 한다.
        # `self._scan_tab` 으로 물으면 -1 이 나온다 (실제로 그랬다).
        self._scan_page = scan_tab
        self.tabs.addTab(scan_tab, "")
        self.tabs.currentChanged.connect(self._on_page_changed)

        self.status_bar_label = QLabel("")
        self.statusBar().addPermanentWidget(self.status_bar_label)

    def _build_header(self) -> QWidget:
        """제목·계정 수, 홈/상세 스캔 이동, 마지막 수집, 찾기, 동작 단추."""

        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        self.app_title = QLabel()
        self.app_title.setObjectName("title")
        row.addWidget(self.app_title)
        self.app_counts = QLabel()
        self.app_counts.setObjectName("caption")
        row.addWidget(self.app_counts)

        nav = QFrame()
        nav.setObjectName("nav")
        nav_box = QHBoxLayout(nav)
        nav_box.setContentsMargins(4, 4, 4, 4)
        nav_box.setSpacing(4)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_home = QPushButton()
        self.nav_scan = QPushButton()
        for index, button in enumerate((self.nav_home, self.nav_scan)):
            button.setObjectName("navButton")
            button.setCheckable(True)
            self.nav_group.addButton(button, index)
            nav_box.addWidget(button)
        self.nav_home.setChecked(True)
        self.nav_group.buttonClicked.connect(
            lambda button: self.tabs.setCurrentIndex(self.nav_group.id(button))
        )
        row.addWidget(nav)

        # 마지막 수집 - 표마다 '최근 수집' 을 읽지 않아도 지금 값이 언제 것인지 보인다.
        # 수집이 멈췄으면 여기가 주황으로 그 사실을 먼저 말한다.
        self.collected_label = widgets.ElidedLabel()
        self.collected_label.setObjectName("muted")
        row.addWidget(self.collected_label, 1)

        # 계정이 스물을 넘으면 눈으로 찾는 것이 일이 된다. 이름이나 경로 조각을
        # 치면 그것만 남는다 - 표를 다시 그리기만 하므로 DB 를 다시 읽지 않는다.
        self.filter_edit = QLineEdit()
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setMinimumWidth(160)
        self.filter_edit.setMaximumWidth(220)
        self.filter_edit.textChanged.connect(self._on_filter_changed)
        row.addWidget(self.filter_edit)

        self.reports_btn = QPushButton()
        self.reports_btn.clicked.connect(self._open_reports_dialog)
        self.accounts_btn = QPushButton()
        self.accounts_btn.clicked.connect(self._open_account_dialog)
        # 자주 쓰지 않는 창들은 한 메뉴로 모은다 - 단추 일곱이 머리줄 폭을 정하게
        # 두지 않는다.
        self.tools_btn = QToolButton()
        self.tools_btn.setObjectName("tools")
        self.tools_btn.setPopupMode(QToolButton.InstantPopup)
        tools = QMenu(self.tools_btn)
        self.search_action = tools.addAction("", self._open_search_dialog)
        self.trend_action = tools.addAction("", self._open_trend_dialog)
        # 서버 부하는 계정이 아니라 **장비**에 대한 것이다 - 정작 알고 싶은 것은
        # 스캔 옆에서 다른 작업이 얼마나 쓰고 있었나다.
        self.load_action = tools.addAction("", self._open_load_dialog)
        self.diagnose_action = tools.addAction("", self._open_diagnostics)
        # 이 도구가 데이터 폴더에 무엇을 얼마나 언제부터 쌓았나 (보기만 한다).
        self.data_action = tools.addAction("", self._open_data_dialog)
        self.tools_btn.setMenu(tools)
        # 가장 자주 쓰는 동작 하나만 강조한다 - 전부 강조하면 아무것도 강조되지 않는다.
        self.collect_btn = QPushButton()
        self.collect_btn.setObjectName("primary")
        self.collect_btn.clicked.connect(self._trigger_now)
        for button in (self.reports_btn, self.accounts_btn, self.tools_btn, self.collect_btn):
            row.addWidget(button)
        return header

    def _build_tiles(self) -> QHBoxLayout:
        """타일 넷 - 가장 찬 곳, 손볼 계정, 상세 스캔 한 바퀴, 끝 예상."""

        row = QHBoxLayout()
        row.setSpacing(theme.GAP_SECTION)
        self.tiles: Dict[str, _Tile] = {}
        for key in ("fullest", "attention", "scan", "finish"):
            tile = _Tile(link=(key == "scan"))
            row.addWidget(tile, 1)
            self.tiles[key] = tile
        self.tiles["fullest"].bar.setVisible(True)
        self.tiles["scan"].bar.setVisible(True)
        self.tiles["finish"].value.setStyleSheet(
            f"font-size: 22px; font-weight: {theme.WEIGHT_HEAVY};"
        )
        self.tiles["scan"].clicked.connect(
            lambda: self.tabs.setCurrentIndex(self.tabs.indexOf(self._scan_page))
        )
        return row

    def _build_priority_card(self) -> QFrame:
        """처리 우선순위.

        사용률은 표에, 백업 상태는 헬스체크에, 튀는 파일은 상세 스캔 탭에 있었다.
        각각은 맞는 말인데 **"그래서 오늘 뭘 먼저 하지"** 에는 아무도 답하지
        않았다. 그 한 줄을 모은다."""

        self.priority_card = QFrame()
        self.priority_card.setObjectName("card")
        priority_box = QVBoxLayout(self.priority_card)
        priority_box.setContentsMargins(16, 12, 16, 12)
        priority_box.setSpacing(4)
        self.priority_title = QLabel()
        self.priority_title.setObjectName("sectionTitle")
        priority_box.addWidget(self.priority_title)
        self.priority_summary = QLabel()
        self.priority_summary.setObjectName("muted")
        self.priority_summary.setWordWrap(True)
        priority_box.addWidget(self.priority_summary)
        self.priority_hint = QLabel()
        self.priority_hint.setObjectName("caption")
        self.priority_hint.setWordWrap(True)
        priority_box.addWidget(self.priority_hint)
        self.priority_list = QListWidget()
        self.priority_list.setObjectName("findings")
        self.priority_list.setFrameShape(QListWidget.NoFrame)
        self.priority_list.setSelectionMode(QListWidget.NoSelection)
        self.priority_list.setFocusPolicy(Qt.NoFocus)
        # 문장이 폭을 넘으면 옆으로 밀지 않고 접는다 - 가로 스크롤로 두면 문장
        # 뒤쪽("정리하면 800GB 빕니다")이 통째로 안 보인다.
        self.priority_list.setWordWrap(True)
        self.priority_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.priority_list.setResizeMode(QListWidget.Adjust)
        # 줄을 누르면 상황과 할 일이 뜬다. 손 모양 커서와 안내 한 줄로 누를 수
        # 있다는 것을 알린다 - 반응만 하고 아무 일이 없으면 고장으로 읽힌다.
        self.priority_list.viewport().setCursor(Qt.PointingHandCursor)
        self.priority_list.itemClicked.connect(self._on_priority_clicked)
        priority_box.addWidget(self.priority_list, 1)
        return self.priority_card

    def _build_tonight_card(self) -> QFrame:
        """오늘 밤 상세 스캔 - 22~06 막대 위에 도는 순서와 계정마다 끝날 어림."""

        self.tonight_card = QFrame()
        self.tonight_card.setObjectName("card")
        box = QVBoxLayout(self.tonight_card)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(8)
        head = QHBoxLayout()
        self.tonight_title = QLabel()
        self.tonight_title.setObjectName("sectionTitle")
        head.addWidget(self.tonight_title)
        head.addStretch(1)
        self.tonight_window = QLabel()
        self.tonight_window.setObjectName("caption")
        head.addWidget(self.tonight_window)
        box.addLayout(head)
        self.tonight_bar = TimelineBar()
        box.addWidget(self.tonight_bar)
        self.tonight_rows = QVBoxLayout()
        self.tonight_rows.setSpacing(6)
        box.addLayout(self.tonight_rows)
        self.tonight_note = QLabel()
        self.tonight_note.setObjectName("caption")
        self.tonight_note.setWordWrap(True)
        box.addWidget(self.tonight_note)
        return self.tonight_card

    # 정렬 키를 만들 수 있는 열만 정렬을 허용한다. 막대·배지 열은 옆의 숫자
    # 열(사용량, 사용률)로 정렬하면 되므로 굳이 열지 않는다.
    SORTABLE_COLUMNS = (COL_NAME, COL_KIND, COL_SIZE, COL_BYTE, COL_TIME)

    def _on_header_clicked(self, column: int) -> None:
        if column not in self.SORTABLE_COLUMNS:
            return
        if self._sort_column == column:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_column = column
            # 숫자 열은 큰 것부터, 글자 열은 가나다순으로 시작하는 것이 자연스럽다.
            self._sort_desc = column not in (COL_NAME, COL_KIND)
        self.table.horizontalHeader().setSortIndicator(
            column, Qt.DescendingOrder if self._sort_desc else Qt.AscendingOrder
        )
        # 다시 **그리기만** 한다. 예전에는 `_refresh_table_from_store()`를 불러
        # 표본을 다시 읽고 FULL 예측까지 새로 계산했는데, 예측은 30일치 표본
        # (계정당 약 2,880행)을 읽는 작업이라 데이터 디렉터리가 NFS 위면
        # **헤더를 누를 때마다 창이 멈췄다.** 정렬은 이미 들고 있는 값의
        # 순서만 바꾸는 일이라 다시 읽을 이유가 없다.
        self._render_table(self._latest_samples)

    def _sorted_accounts(self, latest) -> list:
        """정렬 기준에 맞춰 계정 순서를 정한다.

        값이 없는 계정(아직 수집 전)은 항상 뒤로 보낸다 - 오름차순에서 빈 것이
        맨 위를 차지하면 정작 보려던 것이 밀린다."""

        accounts = list(self._config.accounts)
        column = self._sort_column
        if column is None:
            return accounts

        def key(account):
            sample = latest.get(account.account_id)
            if column == COL_KIND:
                # 성격은 표시 라벨이 아니라 코드로 정렬한다. 언어를 바꾸면
                # 정렬 순서가 따라 바뀌는 것은 사용자가 기대하지 않는 동작이다.
                # 다른 갈래와 같은 (그룹, 값) 모양을 유지한다 - 비교 대상이
                # 섞이면 파이썬이 타입 오류를 낸다.
                return (0, account.kind)
            if column == COL_NAME:
                return (0, account.name.lower())
            if sample is None:
                return (1, 0)
            if column == COL_SIZE:
                return (0, sample.used_kb or 0)
            if column == COL_BYTE:
                return (0, sample.byte_pct if sample.byte_pct is not None else -1)
            if column == COL_TIME:
                return (0, sample.collected_at or "")
            return (0, 0)

        ordered = sorted(accounts, key=key)
        if self._sort_desc:
            # 값 없음(1, ...)은 뒤에 그대로 두고 값 있는 것만 뒤집는다.
            with_value = [a for a in ordered if key(a)[0] == 0]
            without = [a for a in ordered if key(a)[0] == 1]
            ordered = list(reversed(with_value)) + without
        return ordered


    def _configure_table_columns(self) -> None:
        """열마다 폭 정책을 따로 준다.

        전부 `Stretch`로 두면 열 개수로 폭이 균등 분배되어, 값이 짧은 열이
        공간을 낭비하는 동안 정작 긴 이름이 잘린다. 남는 폭은 **이름**이
        가져간다 - 사람이 계정을 가리킬 때 쓰는 유일한 이름이라 잘리면 곤란한
        유일한 열이다."""

        header = self.table.horizontalHeader()
        # 폭이 모자랄 때 어떤 열도 글자 한 자 폭까지 찌그러지지 않게 한다
        # (그 상태가 되면 가로 스크롤이 생기는데, 아무것도 안 보이는 것보다 낫다).
        header.setMinimumSectionSize(72)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        # 남는 폭은 **FULL 예상**이 가져간다. 이름을 늘이려고 Stretch 로 뒀더니
        # 오히려 반대가 됐다 - Stretch 는 '남은 것'을 받는 열이라, 옆 열들이
        # 내용대로 가져간 뒤 이름만 최소 폭(80px)으로 찌그러져 `tc_layout...`
        # 이 됐다. 이름은 내용대로 잡고, 길면 줄여도 되는 쪽(예측 문구, 툴팁에
        # 전체가 있다)에 나머지를 준다.
        # 막대가 들어가는 칸은 내용 기준으로 재면 너무 좁아진다.
        header.setSectionResizeMode(COL_BYTE, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_BYTE, 200)
        # '상세 스캔' 칸도 위젯(알약 + 옆 글)이라 내용 기준 폭이 머리글 폭으로 줄어든다.
        header.setSectionResizeMode(COL_SCAN, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_SCAN, 230)
        header.setSectionResizeMode(COL_FORECAST, QHeaderView.Stretch)
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        # 헤더를 눌러 정렬한다. Qt 내장 정렬 대신 직접 하는 이유는 위 주석 참고.
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(COL_BYTE, Qt.DescendingOrder)
        header.sectionClicked.connect(self._on_header_clicked)

        # 행 배경(등급 색)은 델리게이트가 그린다. 자세한 이유는
        # widgets.TierRowDelegate 참고.
        self._row_tints: Dict[int, str] = {}
        self.table.setItemDelegate(
            widgets.TierRowDelegate(self._row_tints.get, self.table)
        )

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


    # -- 다국어 ------------------------------------------------------
    def retranslate(self) -> None:
        """언어에 따라 달라지는 정적 문자열을 다시 채운다. 동적 문자열(표 내용,
        상태 줄)은 이어지는 갱신 호출이 알아서 다시 그린다."""

        self.setWindowTitle(i18n.t("app.title"))
        self.language_menu.setTitle(i18n.t("menu.language"))
        self.tabs.setTabText(0, i18n.t("tab.home"))
        self.tabs.setTabText(1, i18n.t("tab.scan"))
        self.app_title.setText(i18n.t("home.title"))
        self.nav_home.setText(i18n.t("tab.home"))
        self.nav_scan.setText(i18n.t("tab.scan"))
        self.list_title.setText(i18n.t("dashboard.list_title"))
        self.list_hint.setText(i18n.t("dashboard.list_hint"))
        self.filter_edit.setPlaceholderText(i18n.t("dashboard.filter"))
        self.collect_btn.setText(i18n.t("dashboard.btn.collect_now"))
        self.collect_btn.setToolTip(i18n.t("dashboard.btn.collect_now_tooltip"))
        self.accounts_btn.setText(i18n.t("dashboard.btn.accounts"))
        self.reports_btn.setText(i18n.t("dashboard.btn.reports"))
        self.tools_btn.setText(i18n.t("home.tools"))
        self.search_action.setText(i18n.t("dashboard.btn.search"))
        self.load_action.setText(i18n.t("load.btn.open"))
        self.trend_action.setText(i18n.t("trend.btn.open"))
        self.diagnose_action.setText(i18n.t("dashboard.btn.diagnose"))
        self.data_action.setText(i18n.t("data.btn.open"))
        for key, caption_key in (("fullest", "home.tile.fullest"), ("attention", "home.tile.attention"),
                                 ("scan", "home.tile.scan"), ("finish", "home.tile.finish")):
            self.tiles[key].caption.setText(i18n.t(caption_key))
        self.tiles["attention"].setToolTip(i18n.t("dashboard.df_caveat"))
        self.tonight_note.setText(i18n.t("home.tonight.note"))
        self.table.setHorizontalHeaderLabels([i18n.t(key) for key in COLUMN_KEYS])
        self._scan_tab.retranslate()
        if self._overview is not None:
            self._show_overview(self._overview)

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
        self._scan_tab.refresh()

    # -- 데이터 갱신 ----------------------------------------------------
    def _refresh_priority(self, plan) -> None:
        """무엇부터 손댈지.

        급한 것이 위다. **빈 목록을 '문제 없음' 으로 읽히게 두지 않는다** -
        아직 스캔이 안 돈 계정이 있으면 그 사실을 적는다."""

        self.priority_title.setText(i18n.t("priority.heading"))
        self.priority_hint.setText(i18n.t("priority.hint"))
        self.priority_list.clear()

        if plan is None:
            self.priority_summary.setText(i18n.t("priority.unavailable"))
            return

        if plan.actions:
            self.priority_summary.setText(
                i18n.t(
                    "priority.summary",
                    count=len(plan.actions),
                    critical=plan.critical_count,
                    freeable=formatting.format_kb(plan.reclaimable_kb)
                    if plan.reclaimable_kb else i18n.t("common.none"),
                )
            )
        else:
            self.priority_summary.setText(i18n.t("priority.nothing"))

        for action in plan.actions:
            item = QListWidgetItem(self._priority_text(action))
            if action.critical:
                item.setForeground(QColor(tiers.color(tiers.EMERGENCY)))
            elif action.level == priority.LEVEL_HIGH:
                item.setForeground(QColor(tiers.color(tiers.ALERT)))
            if action.path:
                item.setToolTip(action.path)
            item.setData(Qt.UserRole, action)
            self.priority_list.addItem(item)

        if getattr(plan, "coarse_count", 0):
            # 판정의 정밀도를 밝힌다. 성긴 판정을 정밀한 것처럼 내놓으면
            # "확인됨"이 실제보다 강하게 읽히고, 그 위에서 정리가 결정된다.
            note = QListWidgetItem(
                i18n.t("priority.coarse", count=plan.coarse_count)
            )
            note.setForeground(QColor(theme.TEXT_MUTED))
            note.setData(Qt.UserRole, action_guide.for_coarse(plan.coarse_count))
            self.priority_list.addItem(note)

        if plan.accounts_without_scan:
            # 판단에 못 쓴 것이 있으면 반드시 말한다. 안 그러면 짧은 목록이
            # "볼 것이 없다" 로 읽힌다.
            note = QListWidgetItem(
                i18n.t(
                    "priority.unscanned",
                    count=len(plan.accounts_without_scan),
                    names=", ".join(plan.accounts_without_scan[:3]),
                )
            )
            note.setForeground(QColor(theme.TEXT_MUTED))
            note.setData(
                Qt.UserRole, action_guide.for_unscanned(plan.accounts_without_scan)
            )
            self.priority_list.addItem(note)

    def _priority_text(self, action) -> str:
        """할 일 한 줄을 사람 문장으로. 안내 창과 같은 문장을 쓴다."""

        return action_guide.headline(action)

    def _on_priority_clicked(self, item) -> None:
        """한 줄을 누르면 그 상황과 할 일을 자세히 보여 준다.

        커서를 올리면 줄이 반응하는데 눌러도 아무 일이 없었다 - 사람은 거기서
        "그래서 뭘 하라는 거지" 를 묻는다."""

        data = item.data(Qt.UserRole)
        if data is None:
            return
        from .action_dialog import ActionDialog

        if isinstance(data, action_guide.Guide):
            dialog = ActionDialog(
                self._data_dir, lambda: self._config, guide=data, parent=self
            )
            kind = data.kind
        else:
            dialog = ActionDialog(
                self._data_dir, lambda: self._config, action=data, parent=self
            )
            kind = data.kind
        usage_log.record_soon(self._data_dir, usage_log.GUIDE_OPENED, detail=kind)
        dialog.open_scan_requested.connect(self._show_account_in_scan_tab)
        dialog.status_message.connect(self.status_bar_label.setText)
        dialog.exec_()

    def _refresh_table_from_store(self) -> None:
        """대시보드 데이터를 **요청만** 한다. 실제 읽기는 백그라운드에서 돈다.

        예전에는 여기서 DB를 열고 FULL 예측까지 계산했다. 15분마다 수집이
        끝날 때 불리므로, 데이터 디렉터리가 NFS 위면 사용자는 15분마다 한 번씩
        멈추는 창을 보게 됐다. 결과는 `_on_dashboard_ready` 가 받는다."""

        self._dashboard_worker.refresh_async()
        # 단계가 오기 전에는 '읽고 있다' 만 보여 준다 (막대가 흐른다).
        self.loading_bar.setRange(0, 0)
        self.loading_label.setText(i18n.t("dashboard.loading_start"))
        self.loading_row.setVisible(True)
        if not self._latest_samples:
            self.tiles["fullest"].detail.setText(i18n.t("dashboard.loading_start"))

    def _on_dashboard_progress(self, index: int, total: int, key: str) -> None:
        self.loading_bar.setRange(0, total)
        self.loading_bar.setValue(index - 1)
        self.loading_label.setText(
            i18n.t("dashboard.loading", step=i18n.t(key), index=index, total=total)
        )
        self.loading_row.setVisible(True)

    def _on_dashboard_ready(self, data) -> None:
        self.loading_row.setVisible(False)
        self._latest_samples = data.samples
        self._forecasts = data.forecasts
        # 표를 그리기 **전에** 받아 둔다 - 그리는 쪽이 칸마다 여기서 꺼내 간다.
        self._trends = getattr(data, "trends", {}) or {}
        self._render_table(data.samples)
        self._refresh_priority(getattr(data, "plan", None))
        # 상세 스캔 현황의 '지금 쓰는 양' 도 같은 표본으로 본다 (따로 읽지 않는다).
        self._scan_tab.set_latest_samples(data.samples)
        self._dashboard_newest = getattr(data, "newest", None)
        wanted, self._newest_wanted = self._newest_wanted, None
        if wanted is not None and not dashboard.already_shows(self._dashboard_newest, wanted):
            self._refresh_table_from_store()
        if data.forecast_failed:
            # 예측 칸이 비는 이유가 "표본이 모자라서"인지 "계산이 터져서"인지
            # 화면만 봐서는 구분이 안 된다. 후자면 말해 준다.
            self.status_bar_label.setText(i18n.t("dashboard.forecast_failed"))

    def _on_dashboard_failed(self, message: str) -> None:
        self.loading_row.setVisible(False)
        # 읽기에 실패해도 이미 그려진 화면은 그대로 둔다 - 지우면 사용자는
        # 데이터가 사라진 줄 안다.
        self.status_bar_label.setText(
            i18n.t("dashboard.collect_error", message=message)
        )

    def _on_freshness_ready(self, result) -> None:
        self._freshness = result or {}
        # 표가 이미 그려졌으면 경고를 얹어 다시 그린다 (표본은 이미 손에 있다).
        if self._latest_samples:
            self._render_table(self._latest_samples)
        self._start_collecting()

    def _start_collecting(self) -> None:
        """첫 수집과 15분 타이머를 시작한다 - 신선도 판정이 끝난 뒤 한 번만."""

        if self._collecting:
            return
        self._collecting = True
        self._scheduler.start(run_immediately=True)

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

    def _on_filter_changed(self, text: str) -> None:
        self._filter_text = (text or "").strip().lower()
        self._render_table(self._latest_samples)

    def _visible_accounts(self, accounts) -> list:
        """찾기 칸에 친 글자가 이름이나 경로에 든 계정만."""

        if not self._filter_text:
            return accounts
        needle = self._filter_text
        return [
            account for account in accounts
            if needle in account.name.lower() or needle in account.path.lower()
        ]

    def _render_table(self, latest: Dict[str, store.SampleRecord]) -> None:
        every = self._sorted_accounts(latest)
        accounts = self._visible_accounts(every)
        # 거른 상태를 말해 준다. 안 그러면 "계정이 사라졌다" 로 읽힌다.
        self.list_hint.setText(
            i18n.t("dashboard.list_filtered", shown=len(accounts), total=len(every))
            if self._filter_text else i18n.t("dashboard.list_hint")
        )
        self.table.setRowCount(len(accounts))

        dash = i18n.t("common.none")
        # 행 수가 줄어든 경우 이전 행의 색이 남지 않도록 매번 비운다.
        self._row_tints.clear()

        for row, account in enumerate(accounts):
            sample = latest.get(account.account_id)
            name_item = QTableWidgetItem(account.name)
            # 정렬을 켜면 행 번호가 설정 순서와 달라진다. 행에서 계정을 찾을 때는
            # 반드시 이 id를 쓴다 (이름은 겹칠 수 있어 식별자가 못 된다).
            name_item.setData(Qt.UserRole, account.account_id)
            # 경로 열을 뺀 대신 경로·파일시스템·마운트 지점을 여기 붙인다.
            # 전체는 계정 상세 창에 그대로 있다.
            name_item.setToolTip(formatting.path_tooltip(account, sample))
            self.table.setItem(row, COL_NAME, name_item)
            # 성격은 수집 결과와 무관하므로 표본이 없어도 항상 채운다.
            kind_item = QTableWidgetItem(i18n.t(f"account.kind.{account.kind}"))
            kind_item.setToolTip(i18n.t("accounts.kind_hint"))
            self.table.setItem(row, COL_KIND, kind_item)

            if sample is None:
                for column in (COL_SIZE, COL_FORECAST):
                    item = QTableWidgetItem(dash)
                    self._style_value_item(item, column)
                    self.table.setItem(row, column, item)
                self.table.setCellWidget(row, COL_BYTE, widgets.UsageBar(None, tiers.UNKNOWN))
                self.table.setCellWidget(row, COL_TREND, self._spark_for(account.account_id))
                self.table.setCellWidget(row, COL_SCAN, self._scan_cell(account.account_id))
                # "아직 수집 전"은 '-' 로 두면 고장으로 읽힌다.
                self.table.setItem(
                    row, COL_TIME, QTableWidgetItem(i18n.t("dashboard.not_collected"))
                )
                continue

            # 퍼센트 왼쪽에 실제 크기를 둔다. "95%"만으로는 남은 것이 5GB인지
            # 5TB인지 알 수 없어 급한 정도를 판단할 수 없다.
            size_item = QTableWidgetItem(
                formatting.format_size_pair(sample.used_kb, sample.total_kb)
            )
            size_item.setToolTip(formatting.size_tooltip(sample))
            self._style_value_item(size_item, COL_SIZE)
            self.table.setItem(row, COL_SIZE, size_item)

            usage_bar = widgets.UsageBar(sample.byte_pct, sample.overall_tier, show_tier=True)
            # 등급 배지 열을 뺀 대신 등급 글자를 여기 붙인다 - 색만으로는
            # 경계값이 어느 쪽인지 말해 주지 못한다.
            usage_bar.setToolTip(
                tiers.display_text(sample.overall_tier, sample.byte_pct)
                + "\n" + formatting.size_tooltip(sample)
            )
            self.table.setCellWidget(row, COL_BYTE, usage_bar)
            self.table.setCellWidget(
                row, COL_TREND, self._spark_for(account.account_id, sample.overall_tier)
            )
            forecast = self._forecasts.get(account.account_id)
            forecast_item = QTableWidgetItem(formatting.format_forecast_cell(forecast))
            forecast_item.setToolTip(
                formatting.format_forecast_tooltip(
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
            if self._is_placeholder(forecast_item.text()):
                forecast_item.setForeground(QColor(theme.TEXT_FAINT))
            self.table.setItem(row, COL_FORECAST, forecast_item)
            self.table.setCellWidget(row, COL_SCAN, self._scan_cell(account.account_id))

            # 절대 시각보다 "얼마나 오래됐나"가 판단에 직접 쓰인다. 원본
            # 타임스탬프는 툴팁에 남긴다.
            status_info = self._freshness.get(account.account_id)
            time_item = QTableWidgetItem(
                freshness.format_age(status_info.age_seconds)
                if status_info is not None and status_info.age_seconds is not None
                else formatting.local_minute_text(sample.collected_at)
            )
            time_item.setToolTip(formatting.local_datetime_text(sample.collected_at))
            if status_info is not None and status_info.needs_attention:
                time_item.setForeground(QColor(tiers.color(tiers.ALERT)))
            # 수집 실패는 열을 따로 두지 않고 여기서 말한다. 실패하면 숫자가
            # 낡은 것이므로, 사람이 보는 곳은 결국 "언제 잰 값인가" 다.
            if not sample.ok:
                time_item.setText(i18n.t("dashboard.collect_error_short"))
                time_item.setForeground(QColor(tiers.color(tiers.ALERT)))
                time_item.setToolTip(
                    i18n.t("dashboard.collect_failed", message=sample.error_message)
                )
            self.table.setItem(row, COL_TIME, time_item)

            # 주의 이상인 행만 옅게 칠한다. 정상까지 칠하면 색이 배경 소음이
            # 되어 정작 문제 있는 행이 묻힌다.
            self._tint_row(row, sample.overall_tier if sample.ok else tiers.UNKNOWN)

        self._summarize(every, latest)

    def _summarize(self, accounts, latest) -> None:
        """히어로의 요약을 낸다.

        **거른 목록이 아니라 늘 전체를 본다.** 찾기 칸에 한 계정만 남겨 놓고
        "모든 계정 정상" 을 읽으면, 보이지 않는 곳이 꽉 차 있어도 괜찮은 줄
        안다 - 멈춘 데이터를 보고 정상이라고 판단하는 것과 같은 종류의 사고다.
        """

        worst_tier = tiers.NORMAL
        worst_account_label: Optional[str] = None
        warn_or_worse_count = 0
        # 타일의 큰 숫자에 쓸 값 (가장 높은 사용률과 그 계정), 등급마다 계정 수.
        self._worst_pct: Optional[float] = None
        self._worst_account_name: Optional[str] = None
        self._worst_account_id: Optional[str] = None
        self._worst_tier = tiers.UNKNOWN
        self._tier_counts: Dict[str, int] = {}
        self._ok_count = 0

        for account in accounts:
            sample = latest.get(account.account_id)
            if sample is None:
                continue
            if sample.byte_pct is not None and (
                self._worst_pct is None or sample.byte_pct > self._worst_pct
            ):
                self._worst_pct = sample.byte_pct
                self._worst_account_name = account.name
                self._worst_account_id = account.account_id
                self._worst_tier = sample.overall_tier
            if not sample.ok:
                continue
            self._ok_count += 1
            self._tier_counts[sample.overall_tier] = self._tier_counts.get(sample.overall_tier, 0) + 1
            if tiers.is_at_least(sample.overall_tier, "warn"):
                warn_or_worse_count += 1
            if tiers.severity(sample.overall_tier) > tiers.severity(worst_tier):
                worst_tier = sample.overall_tier
                worst_account_label = (
                    f"{account.name} "
                    f"({tiers.display_text(sample.overall_tier, sample.byte_pct)})"
                )

        self._update_summary(warn_or_worse_count, worst_account_label, worst_tier)

    # 숫자 열은 오른쪽으로 붙인다. 여러 계정을 위에서 아래로 훑을 때 자릿수가
    # 맞아야 "어느 쪽이 큰가"가 읽지 않고도 보인다. 왼쪽 정렬이면 `0.8 TB`와
    # `12.4 TB`의 시작점이 같아 매번 숫자를 읽어야 한다.
    NUMERIC_COLUMNS = (COL_SIZE,)

    # 값이 없다는 뜻의 문구들. 실제 값과 같은 색으로 두면 눈이 먼저 가는 곳이
    # 흐려진다 - 대부분의 행이 `확인불가`인 환경(inode 미지원 파일시스템)에서
    # 특히 그렇다.
    @staticmethod
    def _is_placeholder(text: str) -> bool:
        return text in {
            i18n.t("common.none"),
            i18n.t("common.unknown_value"),
        } or text.startswith(i18n.t("forecast.unavailable"))

    def _style_value_item(self, item, column: int) -> None:
        if column in self.NUMERIC_COLUMNS:
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if self._is_placeholder(item.text()):
            item.setForeground(QColor(theme.TEXT_FAINT))




    def _tint_row(self, row: int, tier: str) -> None:
        """행 배경 색을 기록한다 (실제로 칠하는 것은 델리게이트).

        정상/확인불가는 칠하지 않는다 - 계정 대부분이 정상인 것이 보통이라
        전부 칠하면 색이 배경 소음이 되어 문제 있는 행이 오히려 묻힌다."""

        background = tiers.row_background(tier)
        if background is None:
            self._row_tints.pop(row, None)
        else:
            self._row_tints[row] = background

    def _update_summary(
        self, warn_or_worse_count: int, worst_account_label: Optional[str], worst_tier: str
    ) -> None:
        """머리줄의 계정 수·마지막 수집과 타일 둘(가장 찬 곳, 손볼 계정).

        **거른 목록이 아니라 늘 전체를 본다** (`_summarize`)."""

        self.app_counts.setText(i18n.t(
            "home.counts", accounts=len(self._config.accounts), storages=self._storage_count() or "-",
        ))
        self._update_collected_label()

        fullest = self.tiles["fullest"]
        attention = self.tiles["attention"]
        if not self._config.accounts:
            fullest.value.setText(i18n.t("common.none"))
            fullest.detail.setText(i18n.t("dashboard.no_accounts"))
            fullest.set_chips([])
            fullest.bar.set_segments([])
            attention.value.setText("0")
            attention.set_chips([])
            return

        pct = self._worst_pct
        fullest.value.setText(f"{pct:.1f}%" if pct is not None else i18n.t("common.none"))
        tier = self._worst_tier
        kind = widgets.UsageBar.PILL_TIERS.get(tier, "tier_normal")
        fullest.set_chips([(tiers.label(tier), kind)] if pct is not None else [])
        fullest.bar.set_segments(
            [(1, max(0.0, min(1.0, (pct or 0) / 100)), tiers.color(tier), theme.BORDER, "")]
            if pct is not None else []
        )
        forecast = self._forecasts.get(self._worst_account_id) if self._worst_account_id else None
        fullest.detail.setText(
            i18n.t("home.tile.fullest_detail", account=self._worst_account_name,
                   forecast=formatting.format_forecast_cell(forecast))
            if self._worst_account_name else ""
        )

        attention.value.setText(str(warn_or_worse_count))
        attention.side.setVisible(True)
        attention.side.setText(i18n.t("home.tile.attention_of", total=len(self._config.accounts)))
        chips = []
        for tier_code in (tiers.FULL, tiers.EMERGENCY, tiers.ALERT, tiers.WARN):
            count = self._tier_counts.get(tier_code, 0)
            if count or tier_code in (tiers.EMERGENCY, tiers.ALERT, tiers.WARN):
                chips.append((
                    i18n.t("home.tile.attention_chip", tier=tiers.label(tier_code), count=count),
                    widgets.UsageBar.PILL_TIERS[tier_code],
                ))
        attention.set_chips(chips)
        attention.detail.setText(i18n.t(
            "home.tile.attention_detail", normal=self._tier_counts.get(tiers.NORMAL, 0),
        ))

    def _update_collected_label(self) -> None:
        """머리줄의 '마지막 수집'. 수집이 멈췄으면 그 경고가 먼저다 - 멈춘 데이터를
        보고 "정상" 이라고 판단하는 것이 가장 위험하다."""

        warning = self._freshness_warning()
        if warning:
            self.collected_label.setText(warning)
            self.collected_label.setStyleSheet(
                f"color: {tiers.color(tiers.ALERT)}; font-weight: {theme.WEIGHT_BOLD};"
            )
            return
        stamps = [sample.collected_at for sample in self._latest_samples.values() if sample.collected_at]
        newest = max(stamps, key=lambda stamp: formatting.to_local(stamp) or datetime.min) if stamps else None
        moment = formatting.to_local(newest) if newest else None
        if moment is None:
            self.collected_label.setText(i18n.t("home.collected_never"))
        else:
            age = (datetime.now(moment.tzinfo) - moment).total_seconds()
            self.collected_label.setText(i18n.t(
                "home.collected", time=moment.strftime("%H:%M"), age=freshness.format_age(age),
            ))
        self.collected_label.setStyleSheet("")

    def _storage_count(self) -> int:
        """지금 보고 있는 **스토리지** 수 (계정이 아니라).

        `priority._storage_key` 와 같은 기준으로 센다 - 같은 파일시스템·마운트·
        크기면 한 자리다. 판단이 두 곳에서 갈리면 우선순위 줄과 이 숫자가
        서로 다른 말을 하게 된다."""

        keys = set()
        for sample in self._latest_samples.values():
            if getattr(sample, "ok", True) is False:
                continue
            keys.add(priority._storage_key(sample))
        return len(keys)

    # -- 상세 스캔 현황 (스캔 탭이 넘겨 준다) -------------------------------
    def _on_overview_changed(self, overview) -> None:
        self._overview = overview
        self._show_overview(overview)

    def _show_overview(self, overview) -> None:
        """타일 둘(한 바퀴, 끝 예상), 오늘 밤 카드, 표의 '상세 스캔' 칸."""

        scan = self.tiles["scan"]
        scan.value.setText(f"{overview.percent}%")
        scan.side.setVisible(True)
        visible = [item for item in overview.accounts if item.state != scan_overview.UNSEEN]
        scan.side.setText(i18n.t(
            "home.tile.scan_counts", finished=overview.count(scan_overview.DONE), accounts=len(visible),
        ))
        from .overview_page import STATE_COLORS

        scan.bar.set_segments([
            (1, 1.0 if item.state in (scan_overview.DONE, scan_overview.UNSEEN) else item.fraction,
             STATE_COLORS[item.state][0], STATE_COLORS[item.state][1], item.name)
            for item in overview.accounts
        ])
        self._show_scan_tile_detail()

        finish = self.tiles["finish"]
        if overview.remaining and not overview.beyond and overview.finish_at is not None:
            finish.value.setText(scan_overview.when_text(overview.finish_at))
        else:
            finish.value.setText(scan_overview.finish_text(overview))
        finish.detail.setText(scan_overview.finish_detail(overview))
        stalest = overview.stalest
        if stalest is not None:
            age, kind = scan_overview.age_text(stalest, overview.now)
            finish.set_note(
                i18n.t("home.tile.stalest", name=stalest.name, age=age),
                theme.CHIPS["stale"][1] if kind == "stale" else "",
            )
        else:
            finish.set_note("")

        self._show_tonight(overview)
        for row in range(self.table.rowCount()):
            account_id = self._account_id_at_row(row)
            if account_id:
                self.table.setCellWidget(row, COL_SCAN, self._scan_cell(account_id))

    def _show_scan_tile_detail(self) -> None:
        """한 바퀴 타일의 설명 - 도는 중이면 어디를 재는지, 아니면 상태별 계정 수."""

        scan = self.tiles["scan"]
        state = self._scan_banner
        if state is not None and state.running:
            scan.detail.setText(
                i18n.t("scan.scanning_now", path=state.path) if state.path else i18n.t("scan.started")
            )
            return
        overview = self._overview
        if overview is None:
            scan.detail.setText("")
            return
        scan.detail.setText(i18n.t(
            "home.tile.scan_legend",
            done=overview.count(scan_overview.DONE), doing=overview.count(scan_overview.DOING),
            waiting=overview.count(scan_overview.WAITING), unseen=overview.count(scan_overview.UNSEEN),
        ))

    def _show_tonight(self, overview) -> None:
        while self.tonight_rows.count():
            item = self.tonight_rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if not overview.nights:
            self.tonight_title.setText(
                i18n.t("home.tonight.title", night=i18n.t("overview.night.tonight"))
            )
            self.tonight_window.setText("")
            self.tonight_bar.set_timeline(overview.now, overview.now, [])
            empty = QLabel(i18n.t("home.tonight.empty"))
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self.tonight_rows.addWidget(empty)
            return
        night = overview.nights[0]
        settings = self._config.settings
        length = timedelta(hours=(settings.detail_scan_window_end_hour
                                  - settings.detail_scan_window_start_hour) % 24 or 24)
        axis_start = night.closes - length
        self.tonight_title.setText(i18n.t(
            "home.tonight.title", night=scan_overview.night_name(night, overview.now),
        ))
        self.tonight_window.setText(i18n.t(
            "home.tonight.window", start=scan_overview.clock(axis_start),
            end=scan_overview.clock(night.closes),
        ))
        coverage = {item.account_id: item for item in overview.accounts}
        colors: Dict[str, str] = {}
        spans = []
        for slot in night.slots:
            color = colors.setdefault(slot.account_id, theme.NIGHT_SLOTS[len(colors) % len(theme.NIGHT_SLOTS)])
            spans.append((slot.start, slot.end, color))
        self.tonight_bar.set_timeline(axis_start, night.closes, spans)
        for slot in night.slots:
            self.tonight_rows.addWidget(self._tonight_row(
                slot, coverage.get(slot.account_id), colors[slot.account_id], overview.now,
            ))

    def _tonight_row(self, slot, item, color: str, now) -> QWidget:
        row = QWidget()
        # 카드 위에 얹히는 줄이라 자기 바탕(창 바탕색)을 그리면 회색 띠가 된다.
        row.setStyleSheet("background: transparent;")
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        number = QLabel(str(item.order) if item is not None and item.order else "·")
        number.setAlignment(Qt.AlignCenter)
        number.setFixedSize(24, 24)
        light = color in theme.NIGHT_SLOTS[2:]
        number.setStyleSheet(
            f"background: {color}; color: {theme.TEXT if light else '#ffffff'}; "
            f"border-radius: 6px; font-weight: {theme.WEIGHT_HEAVY};"
        )
        box.addWidget(number)
        text = QVBoxLayout()
        text.setSpacing(0)
        name = QLabel(scan_overview.slot_name(slot))
        name.setStyleSheet(f"font-weight: {theme.WEIGHT_BOLD};")
        text.addWidget(name)
        why = QLabel(scan_overview.tonight_why(item, now) if item is not None else "")
        why.setObjectName("caption")
        text.addWidget(why)
        box.addLayout(text, 1)
        end = QLabel(
            i18n.t("home.tonight.carry") if slot.carries_over
            else i18n.t("home.tonight.end", time=scan_overview.clock(slot.end))
        )
        end.setStyleSheet(
            f"font-weight: {theme.WEIGHT_BOLD}; color: "
            f"{theme.CHIPS['stale'][1] if slot.carries_over else theme.TEXT};"
        )
        box.addWidget(end)
        return row

    def _scan_cell(self, account_id: str) -> QWidget:
        """표의 '상세 스캔' 칸 - 알약(어디쯤인가)과 옆 글(오늘 밤 몇 번째 등)."""

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        box = QHBoxLayout(container)
        box.setContentsMargins(4, 8, 4, 8)
        box.setSpacing(8)
        overview = self._overview
        item = None
        if overview is not None:
            item = next((entry for entry in overview.accounts if entry.account_id == account_id), None)
        if item is None:
            dash = QLabel(i18n.t("common.none"))
            dash.setObjectName("muted")
            box.addWidget(dash)
        else:
            text, kind, note = scan_overview.home_chip(item, overview)
            box.addWidget(Chip(text, kind))
            if note:
                label = QLabel(note)
                label.setObjectName("caption")
                box.addWidget(label)
        box.addStretch(1)
        return container

    # -- 이벤트 핸들러 --------------------------------------------------
    def _trigger_now(self) -> None:
        self.status_bar_label.setText(i18n.t("dashboard.collecting"))
        self._scheduler.trigger_now()

    def _on_collection_finished(self, records: Optional[List[store.SampleRecord]]) -> None:
        if records is None:
            # 다른 곳(cron 등)이 방금 수집해서 건너뛰었다 (`CollectorWorker`).
            self.status_bar_label.setText(i18n.t("dashboard.collected_elsewhere"))
            self._on_collected_elsewhere(self._scheduler.newest_elsewhere())
            return
        self._refresh_table_from_store()
        failed = sum(1 for r in records if not r.ok)
        if failed:
            self.status_bar_label.setText(
                i18n.t("dashboard.collected_with_failures", count=len(records), failed=failed)
            )
        else:
            self.status_bar_label.setText(i18n.t("dashboard.collected", count=len(records)))

    def _on_collected_elsewhere(self, newest: Optional[str]) -> None:
        """남이 방금 수집한 표본을 홈이 이미 보여 주고 있으면 다시 읽지 않는다.

        창을 열면 홈 읽기와 첫 수집이 함께 시작되는데, cron 이 방금 수집했으면 창의
        수집은 건너뛴다. 그래도 늘 새로고침해서 창을 열 때마다 홈을 두 번 읽었다.
        홈 읽기가 아직 도는 중이면(창을 열 때는 대개 그렇다) 끝난 뒤에 견준다."""

        if newest is not None and self._dashboard_worker.is_running():
            self._newest_wanted = newest
            return
        if not dashboard.already_shows(self._dashboard_newest, newest):
            self._refresh_table_from_store()

    def _on_collection_failed(self, message: str) -> None:
        self.status_bar_label.setText(i18n.t("dashboard.collect_error", message=message))

    def _open_account_dialog(self) -> None:
        usage_log.record_soon(self._data_dir, usage_log.ACCOUNTS_OPENED)
        dialog = AccountDialog(self._data_dir, self._config, parent=self)
        before = len(self._config.accounts)
        if dialog.exec_():
            self._config = config_module.load_config(self._data_dir)
            # 계정 수가 언제 어떻게 늘었나. "몇 명이 쓰나" 와 함께 이 도구가
            # 자리를 잡는지 보여 주는 숫자다 - 이름은 정의만 되어 있고 한 번도
            # 기록되지 않았다.
            usage_log.record_soon(
                self._data_dir, usage_log.ACCOUNT_CHANGED,
                detail=f"{before}->{len(self._config.accounts)}",
            )
            i18n.set_language(self._config.settings.language)
            for code, action in self._language_actions.items():
                action.setChecked(code == i18n.get_language())
            self.retranslate()
            self._refresh_table_from_store()
            self._scan_tab.refresh()
            self._scheduler.restart_with_current_interval()

    def _open_reports_dialog(self) -> None:
        usage_log.record_soon(self._data_dir, usage_log.REPORT_VIEWED)
        ReportsDialog(self._data_dir, self._config, parent=self).exec_()

    def _open_search_dialog(self) -> None:
        # 검색을 **했다는 사실**만 남긴다. 무엇을 찾았는지는 남기지 않는다 -
        # 운영 판단에 필요한 것은 이 기능을 쓰는 사람이 있느냐까지다.
        usage_log.record_soon(self._data_dir, usage_log.SEARCH_USED)
        SearchDialog(self._data_dir, self._config, parent=self).exec_()

    def _spark_for(self, account_id: str, tier: str = tiers.UNKNOWN):
        """표 칸에 들어갈 작은 추세선.

        이력이 없으면 빈 것을 준다 - 칸을 비워 두면 열이 들쭉날쭉해 보이고,
        "이 계정만 뭔가 잘못됐나" 로 읽힌다."""

        from .trend_view import Sparkline

        spark = Sparkline()
        spark.set_series(self._trends.get(account_id), tier)
        return spark

    def _open_trend_dialog(self) -> None:
        """지금 고른 계정의 용량 추세.

        표의 작은 선은 모양만 말한다 - 숫자와 축이 필요하면 여기로 온다."""

        from .trend_dialog import TrendDialog

        TrendDialog(
            self._data_dir, self._config,
            account_id=self._account_id_at_row(self.table.currentRow()) or "",
            parent=self,
        ).exec_()

    def _open_data_dialog(self) -> None:
        """데이터 폴더 창. 읽기는 창 안의 작업 스레드가 한다."""

        from .data_dialog import DataFolderDialog

        usage_log.record_soon(self._data_dir, usage_log.DATA_VIEWED)
        DataFolderDialog(self._data_dir, self._config, parent=self).exec_()

    def _open_load_dialog(self) -> None:
        """서버 부하 이력 창.

        수집기가 남긴 기록만 읽는다 - 창을 연다고 새로 재지 않는다."""

        from .load_dialog import LoadDialog

        LoadDialog(self._data_dir, self._config, parent=self).exec_()

    def _open_diagnostics(self) -> None:
        result = diagnostics.run_diagnostics(self._data_dir)
        report = diagnostics.format_report(result)
        box = QMessageBox(self)
        box.setWindowTitle(i18n.t("diagnostics.title"))
        box.setIcon(QMessageBox.Information if result["ok"] else QMessageBox.Warning)
        box.setText(report)
        box.exec_()



    def _open_account_detail(self, row: int, _column: int = 0) -> None:
        """계정 하나에 대해 아는 것 전부를 한 창에 모아 보여 준다."""

        account_id = self._account_id_at_row(row)
        if not account_id:
            return
        from .account_detail_dialog import AccountDetailDialog

        usage_log.record_soon(self._data_dir, usage_log.ACCOUNT_VIEWED)
        dialog = AccountDetailDialog(
            self._data_dir, lambda: self._config, account_id, parent=self
        )
        # 창의 탭을 옮기는 것은 창의 일이다 - 대화상자가 직접 만지면 둘을
        # 따로 옮길 수 없게 된다.
        dialog.open_scan_requested.connect(self._show_account_in_scan_tab)
        dialog.exec_()

    def _show_account_in_scan_tab(self, account_id: str) -> None:
        self._scan_tab.select_account(account_id)
        self.tabs.setCurrentIndex(self.tabs.indexOf(self._scan_page))

    def _account_id_at_row(self, row: int):
        """표의 행 번호로 계정 id를 찾는다 (정렬돼 있어도 안전)."""

        if row < 0:
            return None
        item = self.table.item(row, COL_NAME)
        return item.data(Qt.UserRole) if item is not None else None

    def _on_table_selection_changed(self) -> None:
        """홈 표에서 행을 고르면 상세 스캔 탭의 계정도 같이 맞춘다."""

        account_id = self._account_id_at_row(self.table.currentRow())
        if account_id:
            # 콤보를 맞추는 것도, 증감 표를 다시 그리는 것도 탭이 알아서 한다.
            self._scan_tab.select_account(account_id)
















    def _update_home_scan_banner(self, state) -> None:
        """스캔 탭이 보내 온 "지금 도는가" - 한 바퀴 타일의 설명 줄에 쓴다.

        스캔 탭이 위젯을 직접 만지지 않고 값만 보내 온다 - 그래야 두 화면을
        따로 옮길 수 있다."""

        self._scan_banner = state
        self._show_scan_tile_detail()

    def _on_page_changed(self, index: int) -> None:
        """머리줄의 이동 단추를 지금 쪽에 맞춘다. 찾기 칸은 홈 표에만 듣는다."""

        button = self.nav_group.button(index)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        self.filter_edit.setVisible(index == 0)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 콜백 이름 규칙)
        """창을 닫을 때 우리가 띄운 작업을 확실히 정리한다.

        예전에는 중지 '요청'만 써 두고 바로 닫았는데, 그러면 `du`가 계속 돌았다.
        요청은 체크포인트 **사이**에서만 확인되는데 시간의 대부분은 `du`가 도는
        중이고, 창이 닫히면 파이썬이 종료되면서 daemon 스레드는 잘리지만 자식
        프로세스는 init에 재부모화되어 끝까지 돈다. 사용자에게는 "껐는데도
        파일서버가 계속 느린" 상태로 나타난다.
        """

        if self._scan_tab.is_scan_running():
            answer = QMessageBox.question(
                self,
                i18n.t("scan.close_while_running_title"),
                i18n.t("scan.close_while_running_body"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return

        self._scheduler.stop()
        self._scan_tab.shutdown()
        # 맡겨 둔 사용 기록을 마저 쓴다 (`usage_log.record_soon`). 오래 붙잡지는 않는다.
        usage_log.flush(timeout=2.0)
        super().closeEvent(event)

