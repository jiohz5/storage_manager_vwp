"""메인 창 - 홈(현황) / 상세 스캔 두 탭.

DESIGN.md 2부 6절은 "대시보드 단일 화면(탭 없음)"으로 결정했었다. 실제로 써
보니 **상세 스캔 영역이 세로 공간을 너무 많이 가져가** 매일 보는 현황이 가끔
보는 스캔에 밀렸다. 스플리터로 비율을 조절하게 해 봤지만 그건 "매번 사용자가
정리해야 한다"는 뜻이라 근본 해결이 아니었다. 그래서 탭 둘로 나눴다 (3부
"화면 구조 - 단일 화면에서 탭 둘로" 참고).

유지되는 것:
- "한눈 요약 우선": 홈 탭 최상단에 히어로(가장 높은 사용률 + 주의 이상 건수).
- 계정 등록/설정·보고서·검색은 여전히 다이얼로그다 (탭을 더 늘리지 않는다).

모든 표시 문자열은 `i18n.t`를 거친다. 언어를 바꾸면 `retranslate()`가 정적
문자열을, 이어지는 갱신이 동적 문자열을 다시 그린다 - 앱을 다시 시작할 필요가
없다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt
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
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_module
from .. import formatting
from .. import (
    diagnostics,
    forecast_notify,
    freshness,
    i18n,
    quota,
    store,
    tiers,
)
from ..scheduler import CollectorScheduler, DashboardWorker
from . import theme, widgets
from .scan_tab import ScanTab
from .account_dialog import AccountDialog
from .first_run import FirstRunDialog
from .reports_dialog import ReportsDialog
from .search_dialog import SearchDialog

COLUMN_KEYS = [
    "dashboard.col.name",
    # 성격(프로젝트/백업)을 이름 바로 옆에 둔다. 이 열이 없으면 백업 계정이
    # 계속 느는 것을 보고 매번 놀라게 된다 - 백업은 단조 증가가 정상이고
    # 프로젝트는 과제가 끝나면 줄어야 정상이라, 같은 숫자를 다르게 읽어야 한다.
    "dashboard.col.kind",
    "dashboard.col.path",
    "dashboard.col.size",
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
    COL_KIND,
    COL_PATH,
    COL_SIZE,
    COL_BYTE,
    COL_INODE,
    COL_QUOTA,
    COL_TIER,
    COL_FORECAST,
    COL_TIME,
    COL_STATUS,
) = range(11)

# `파일시스템` 열은 뺐다. 값이 거의 항상 같아서(계정 대부분이 같은 파일시스템에
# 있다) 열 하나를 통째로 쓰면서 정보는 거의 주지 않았다. 대신 경로 툴팁에
# 넣는다 - 필요한 순간(어느 파일시스템인지 확인할 때)에만 보면 되는 값이다.

# 경로 열의 최저 폭. 칸 좌우 여백(QSS `::item` padding 8px씩)을 빼고도 실제
# 운영 경로(`/user/project_a` 계열, 실측 약 110px)가 넉넉히 들어간다.
# 이보다 긴 경로는 잘리지만 툴팁에 전체가 남는다 - 열 하나가 표를 다 먹는
# 것보다는 낫다.
PATH_MIN_WIDTH = 240



# 스캔이 안 돌 때의 간격. 5초로 계속 두면 아무 일도 없는 시간에도 DB를 계속
# 두들긴다 - 데이터 디렉터리가 NFS 위면 그 자체가 부담이고, 정작 바뀌는 것도
# 없다. 스캔이 시작되면 위 간격으로 다시 좁힌다.
SCAN_STATUS_IDLE_REFRESH_MS = 30000

# 히어로 - 창을 열자마자 시선이 먼저 닿는 자리. "지금 가장 급한 것 하나"를
# 큰 숫자로 못박고, 나머지는 그 아래 작은 글씨로 둔다. 표를 훑기 전에 판단이
# 끝나는 것이 목표다.
#
# 등급별 왼쪽 띠 색만 여기서 정하고(등급에 따라 달라지므로), 나머지 외양은
# 전부 theme.py가 담당한다.
HERO_ACCENT_STYLE = "background: {color}; border-radius: 3px;"


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

        # 열이 10개인 표 + 히어로 + 스캔 섹션이 한 화면에 들어가려면 이 정도는
        # 필요하다. 최소 크기를 함께 못박아, 창을 줄였을 때 표가 한 줄도 안
        # 보이는 상태로 무너지지 않게 한다.
        self.resize(1200, 820)
        self.setMinimumSize(960, 700)
        self._build_ui()
        self.retranslate()

        # 수집 신선도는 **첫 수집을 돌리기 전에** 판정해야 한다. GUI를 열면
        # 스케줄러가 곧바로 한 번 수집하므로, 그 뒤에 보면 늘 "방금 수집됨"이
        # 되어 "그동안 cron이 안 돌았다"는 사실이 가려진다.
        self._freshness = self._evaluate_freshness()
        # 표본 읽기와 FULL 예측은 **GUI 스레드에서 하지 않는다.** 예측은
        # 계정마다 이력을 다시 훑으므로, 데이터 디렉터리가 NFS 위면 그동안
        # 창이 멈춘다 (스캔 상태 쪽은 이미 같은 이유로 스레드에 있다).
        self._dashboard_worker = DashboardWorker(
            data_dir, get_config=lambda: self._config, parent=self
        )
        self._dashboard_worker.finished.connect(self._on_dashboard_ready)
        self._dashboard_worker.failed.connect(self._on_dashboard_failed)
        self._refresh_table_from_store()

        self._scheduler = CollectorScheduler(
            data_dir,
            get_config=lambda: self._config,
            on_finished=self._on_collection_finished,
            on_failed=self._on_collection_failed,
        )
        self._scheduler.start(run_immediately=True)

        # 야간 상세 스캔.
        #
        # 두 갈래로 돌 수 있다. cron(`setup_cron.csh`)이 22:00에 띄우거나,
        # 이 창이 시간창을 지켜 스스로 시작하거나(`gui_auto_nightly_scan`).
        # 둘 다 켜 두어도 안전하다 - `scan_lock`이 프로세스를 가로질러 하나만
        # 돌게 막는다.
        #
        # 창이 시작한 스캔은 **창을 닫으면 함께 멈춘다**(`closeEvent`). 그것이
        # 이 경로를 쓰는 이유이자 대가다 - 아무도 창을 안 켜 둔 밤은 통째로 빈다.
        # 스캔 화면은 통째로 `ScanTab` 이 맡는다. 창은 신호만 듣는다.
        self._scan_tab = ScanTab(data_dir, lambda: self._config, self)
        self._scan_tab.status_message.connect(self.status_bar_label.setText)
        self._scan_tab.banner_changed.connect(self._update_home_scan_banner)



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

        # 탭 두 개: 홈(현황) / 상세 스캔.
        #
        # 원래는 한 화면에 세로로 쌓았는데(DESIGN.md 2부 6절 "대시보드 단일
        # 화면"), 실제로 써 보니 **상세 스캔 영역이 세로 공간을 너무 많이
        # 가져갔다.** 매일 보는 것은 현황이고 상세 스캔은 밤에 돌아간 결과를
        # 가끔 확인하는 것인데, 자주 보는 쪽이 가끔 보는 쪽에 밀리는 구조였다.
        # 스플리터로 비율을 조절하게 해 봤지만 그건 "매번 사용자가 정리해야
        # 한다"는 뜻이라 근본 해결이 아니었다.
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        home = QWidget()
        layout = QVBoxLayout(home)
        layout.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD)
        layout.setSpacing(theme.GAP_SECTION)

        layout.addWidget(self._build_hero())

        # 스캔이 도는 동안에는 홈에서도 보이게 한다. 탭을 나눈 뒤로 상세 스캔
        # 탭을 열지 않으면 밤새 뭐가 도는지 알 수 없어졌기 때문이다. 여기서는
        # 한 줄 + 막대까지만 보여 주고 자세한 것은 그 탭에서 본다.
        self.home_scan_banner = QFrame()
        self.home_scan_banner.setObjectName("card")
        self.home_scan_banner.setVisible(False)
        banner_box = QHBoxLayout(self.home_scan_banner)
        banner_box.setContentsMargins(14, 10, 14, 10)
        banner_box.setSpacing(12)
        self.home_scan_label = QLabel()
        self.home_scan_label.setObjectName("muted")
        self.home_scan_label.setWordWrap(True)
        self.home_scan_progress = QProgressBar()
        self.home_scan_progress.setRange(0, 0)
        self.home_scan_progress.setTextVisible(False)
        self.home_scan_progress.setFixedSize(120, 6)
        self.home_scan_link = QPushButton()
        self.home_scan_link.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        banner_box.addWidget(self.home_scan_progress)
        banner_box.addWidget(self.home_scan_label, 1)
        banner_box.addWidget(self.home_scan_link)
        layout.addWidget(self.home_scan_banner)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.collect_btn = QPushButton()
        # 가장 자주 쓰는 동작 하나만 강조한다 - 전부 강조하면 아무것도 강조되지
        # 않는다.
        self.collect_btn.setObjectName("primary")
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
        self._configure_table_columns()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(False)  # 등급 색과 겹치면 판독을 방해한다
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setShowGrid(False)
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        # 계정 표는 **Qt 내장 정렬을 쓰지 않는다.** 사용률 막대와 등급 배지가
        # 칸 위젯이라, Qt가 항목만 옮기고 위젯은 제자리에 두어 행과 위젯이
        # 어긋난다. 대신 정렬 키만 기억해 두고 표를 다시 그린다.
        self._sort_column = None
        self._sort_desc = True

        # 홈 탭에서는 계정 표가 세로 공간을 전부 가져간다.
        self.table.setMinimumHeight(140)
        layout.addWidget(self.table, 1)
        self.tabs.addTab(home, "")

        scan_tab = QWidget()
        scan_layout = QVBoxLayout(scan_tab)
        scan_layout.setContentsMargins(
            theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD
        )
        scan_layout.setSpacing(theme.GAP_SECTION)
        scan_layout.addWidget(self._scan_tab, 1)
        self.tabs.addTab(scan_tab, "")

        self.status_bar_label = QLabel("")
        self.statusBar().addPermanentWidget(self.status_bar_label)

    # 정렬 키를 만들 수 있는 열만 정렬을 허용한다. 막대·배지 열은 옆의 숫자
    # 열(사용량, 사용률)로 정렬하면 되므로 굳이 열지 않는다.
    SORTABLE_COLUMNS = (COL_NAME, COL_KIND, COL_PATH, COL_SIZE, COL_BYTE, COL_INODE, COL_TIME)

    def _on_header_clicked(self, column: int) -> None:
        if column not in self.SORTABLE_COLUMNS:
            return
        if self._sort_column == column:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_column = column
            # 숫자 열은 큰 것부터, 글자 열은 가나다순으로 시작하는 것이 자연스럽다.
            self._sort_desc = column not in (COL_NAME, COL_KIND, COL_PATH)
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
            if column == COL_PATH:
                return (0, account.path.lower())
            if sample is None:
                return (1, 0)
            if column == COL_SIZE:
                return (0, sample.used_kb or 0)
            if column == COL_BYTE:
                return (0, sample.byte_pct if sample.byte_pct is not None else -1)
            if column == COL_INODE:
                return (0, sample.inode_pct if sample.inode_pct is not None else -1)
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

    def _fit_path_column(self) -> None:
        """경로 열에 남는 폭을 몰아주되 최소 폭은 지킨다.

        다른 열은 내용에 맞춰 잡히므로, 그러고 남은 자리를 경로가 받는다.
        남은 자리가 최소 폭보다 좁으면 최소 폭을 쓰고 가로 스크롤을 감수한다 -
        경로가 'C:...'로 잘려 아무것도 안 보이는 것보다 낫다."""

        others = sum(
            self.table.columnWidth(column)
            for column in range(self.table.columnCount())
            if column != COL_PATH
        )
        available = self.table.viewport().width() - others
        self.table.setColumnWidth(COL_PATH, max(PATH_MIN_WIDTH, available))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        super().resizeEvent(event)
        self._fit_path_column()

    def _build_hero(self) -> QWidget:
        """맨 위 요약 카드.

        예전에는 색면 배너에 요약 문장 한 줄만 있었는데, 그러면 "지금 몇 %인가"
        라는 가장 궁금한 값이 본문과 같은 크기로 묻혔다. 가장 높은 사용률을
        큰 숫자로 못박고, 그 옆에 계정 수·주의 건수·마지막 수집을 붙인다."""

        card = QFrame()
        card.setObjectName("hero")
        outer = QHBoxLayout(card)
        outer.setContentsMargins(0, 0, theme.PAD_CARD, 0)
        outer.setSpacing(0)

        # 등급을 나타내는 왼쪽 띠. 색을 넓게 칠하는 대신 가늘게 세워 두면
        # 상태는 전달되면서 배경 소음이 되지 않는다.
        self.hero_accent = QFrame()
        self.hero_accent.setFixedWidth(6)
        outer.addWidget(self.hero_accent)

        body = QVBoxLayout()
        body.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD - 2, 0, theme.PAD_CARD - 2)
        body.setSpacing(2)
        outer.addLayout(body, 1)

        self.hero_state_label = QLabel()
        self.hero_state_label.setObjectName("caption")
        self.hero_state_label.setWordWrap(True)
        body.addWidget(self.hero_state_label)

        self.hero_value_label = QLabel()
        self.hero_value_label.setObjectName("display")
        body.addWidget(self.hero_value_label)

        self.hero_detail_label = QLabel()
        self.hero_detail_label.setObjectName("muted")
        self.hero_detail_label.setWordWrap(True)
        body.addWidget(self.hero_detail_label)

        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        body.addSpacing(10)
        body.addWidget(divider)
        body.addSpacing(8)

        stats = QHBoxLayout()
        stats.setSpacing(28)
        self.hero_stats = {}
        for key in ("accounts", "attention", "collected"):
            column = QVBoxLayout()
            column.setSpacing(1)
            caption = QLabel()
            caption.setObjectName("statLabel")
            value = QLabel()
            value.setObjectName("statValue")
            column.addWidget(caption)
            column.addWidget(value)
            stats.addLayout(column)
            self.hero_stats[key] = (caption, value)
        stats.addStretch(1)
        body.addLayout(stats)

        # df 특성 안내는 히어로 숫자의 해석에 직접 걸리는 단서라 같은 카드에 둔다.
        self.caveat_label = QLabel()
        self.caveat_label.setObjectName("caption")
        self.caveat_label.setWordWrap(True)
        body.addSpacing(8)
        body.addWidget(self.caveat_label)

        return card

    def _configure_table_columns(self) -> None:
        """열마다 폭 정책을 따로 준다.

        전부 `Stretch`로 두면 열 개수로 폭이 균등 분배되어, 값이 짧은 열
        (`파일시스템`='C:', `quota`='-')이 공간을 낭비하는 동안 정작 길이가
        필요한 `경로`는 'C:...'로 잘려 아무것도 안 보인다. 실제로 재 보면
        경로에 364px가 필요한데 균등 분배로는 166px밖에 못 받았다."""

        header = self.table.horizontalHeader()
        # 폭이 모자랄 때 어떤 열도 글자 한 자 폭까지 찌그러지지 않게 한다
        # (그 상태가 되면 가로 스크롤이 생기는데, 아무것도 안 보이는 것보다 낫다).
        header.setMinimumSectionSize(80)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        # 경로는 남는 폭을 가져가되 **최소 폭은 보장**한다. Stretch로 두면
        # 나머지 열이 내용대로 다 가져간 뒤 남은 것만 받아서, 창이 조금만
        # 좁아도 다시 'C:...'로 잘린다. 여기서는 직접 계산해 넣는다.
        header.setSectionResizeMode(COL_PATH, QHeaderView.Interactive)
        # 막대가 들어가는 칸은 내용 기준으로 재면 너무 좁아진다.
        header.setSectionResizeMode(COL_BYTE, QHeaderView.Fixed)
        self.table.setColumnWidth(COL_BYTE, 168)
        # 배지는 칸 위젯이라 ResizeToContents가 크기를 계산에 넣지 못한다.
        header.setSectionResizeMode(COL_TIER, QHeaderView.Fixed)
        self.table.setColumnWidth(
            COL_TIER, widgets.badge_column_width(self.table.fontMetrics())
        )
        header.setStretchLastSection(False)
        header.setHighlightSections(False)
        # 헤더를 눌러 정렬한다. Qt 내장 정렬 대신 직접 하는 이유는 위 주석 참고.
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
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
        self.caveat_label.setText(i18n.t("dashboard.df_caveat"))
        self.collect_btn.setText(i18n.t("dashboard.btn.collect_now"))
        self.collect_btn.setToolTip(i18n.t("dashboard.btn.collect_now_tooltip"))
        self.accounts_btn.setText(i18n.t("dashboard.btn.accounts"))
        self.reports_btn.setText(i18n.t("dashboard.btn.reports"))
        self.search_btn.setText(i18n.t("dashboard.btn.search"))
        self.diagnose_btn.setText(i18n.t("dashboard.btn.diagnose"))
        self.table.setHorizontalHeaderLabels([i18n.t(key) for key in COLUMN_KEYS])

        self.home_scan_link.setText(i18n.t("tab.scan"))
        self._scan_tab.retranslate()

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
    def _refresh_table_from_store(self) -> None:
        """대시보드 데이터를 **요청만** 한다. 실제 읽기는 백그라운드에서 돈다.

        예전에는 여기서 DB를 열고 FULL 예측까지 계산했다. 15분마다 수집이
        끝날 때 불리므로, 데이터 디렉터리가 NFS 위면 사용자는 15분마다 한 번씩
        멈추는 창을 보게 됐다. 결과는 `_on_dashboard_ready` 가 받는다."""

        self._dashboard_worker.refresh_async()

    def _on_dashboard_ready(self, data) -> None:
        self._latest_samples = data.samples
        self._forecasts = data.forecasts
        self._render_table(data.samples)
        if data.forecast_failed:
            # 예측 칸이 비는 이유가 "표본이 모자라서"인지 "계산이 터져서"인지
            # 화면만 봐서는 구분이 안 된다. 후자면 말해 준다.
            self.status_bar_label.setText(i18n.t("dashboard.forecast_failed"))

    def _on_dashboard_failed(self, message: str) -> None:
        # 읽기에 실패해도 이미 그려진 화면은 그대로 둔다 - 지우면 사용자는
        # 데이터가 사라진 줄 안다.
        self.status_bar_label.setText(
            i18n.t("dashboard.collect_error", message=message)
        )

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

    def _render_table(self, latest: Dict[str, store.SampleRecord]) -> None:
        accounts = self._sorted_accounts(latest)
        self.table.setRowCount(len(accounts))

        worst_tier = tiers.NORMAL
        worst_account_label: Optional[str] = None
        warn_or_worse_count = 0
        dash = i18n.t("common.none")
        # 히어로의 큰 숫자에 쓸 값 (가장 높은 사용률과 그 계정).
        self._worst_pct: Optional[float] = None
        self._worst_account_name: Optional[str] = None
        # 행 수가 줄어든 경우 이전 행의 색이 남지 않도록 매번 비운다.
        self._row_tints.clear()

        for row, account in enumerate(accounts):
            sample = latest.get(account.account_id)
            name_item = QTableWidgetItem(account.name)
            # 정렬을 켜면 행 번호가 설정 순서와 달라진다. 행에서 계정을 찾을 때는
            # 반드시 이 id를 쓴다 (이름은 겹칠 수 있어 식별자가 못 된다).
            name_item.setData(Qt.UserRole, account.account_id)
            self.table.setItem(row, COL_NAME, name_item)
            path_item = QTableWidgetItem(account.path)
            # 폭이 모자라 잘리더라도 전체 경로는 확인할 수 있어야 한다.
            # 파일시스템/마운트 지점도 여기 붙인다 (열을 없앤 대신).
            path_item.setToolTip(formatting.path_tooltip(account, sample))
            self.table.setItem(row, COL_PATH, path_item)
            # 성격은 수집 결과와 무관하므로 표본이 없어도 항상 채운다.
            kind_item = QTableWidgetItem(i18n.t(f"account.kind.{account.kind}"))
            kind_item.setToolTip(i18n.t("accounts.kind_hint"))
            self.table.setItem(row, COL_KIND, kind_item)

            if sample is None:
                for column in (COL_SIZE, COL_INODE, COL_QUOTA, COL_FORECAST, COL_TIME):
                    item = QTableWidgetItem(dash)
                    self._style_value_item(item, column)
                    self.table.setItem(row, column, item)
                self.table.setCellWidget(row, COL_BYTE, widgets.UsageBar(None, tiers.UNKNOWN))
                self.table.setCellWidget(row, COL_TIER, widgets.badge_cell(tiers.UNKNOWN, None))
                self.table.setItem(row, COL_STATUS, QTableWidgetItem(i18n.t("dashboard.not_collected")))
                continue

            # 퍼센트 왼쪽에 실제 크기를 둔다. "95%"만으로는 남은 것이 5GB인지
            # 5TB인지 알 수 없어 급한 정도를 판단할 수 없다.
            size_item = QTableWidgetItem(
                widgets.format_size_pair(sample.used_kb, sample.total_kb)
            )
            size_item.setToolTip(formatting.size_tooltip(sample))
            self._style_value_item(size_item, COL_SIZE)
            self.table.setItem(row, COL_SIZE, size_item)

            inode_text = (
                f"{sample.inode_pct:.1f}%"
                if sample.inode_pct is not None
                else i18n.t("common.unknown_value")
            )
            usage_bar = widgets.UsageBar(sample.byte_pct, sample.overall_tier)
            usage_bar.setToolTip(formatting.size_tooltip(sample))
            self.table.setCellWidget(row, COL_BYTE, usage_bar)
            inode_item = QTableWidgetItem(inode_text)
            self._style_value_item(inode_item, COL_INODE)
            self.table.setItem(row, COL_INODE, inode_item)

            quota_item = QTableWidgetItem(quota.format_usage(sample))
            self._style_value_item(quota_item, COL_QUOTA)
            self.table.setItem(row, COL_QUOTA, quota_item)

            self.table.setCellWidget(
                row, COL_TIER, widgets.badge_cell(sample.overall_tier, sample.byte_pct)
            )

            if sample.byte_pct is not None and (
                self._worst_pct is None or sample.byte_pct > self._worst_pct
            ):
                self._worst_pct = sample.byte_pct
                self._worst_account_name = account.name

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
            if self._is_placeholder(forecast_item.text()):
                forecast_item.setForeground(QColor(theme.TEXT_FAINT))
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

        self._fit_path_column()
        self._update_summary(warn_or_worse_count, worst_account_label, worst_tier)

    # 숫자 열은 오른쪽으로 붙인다. 여러 계정을 위에서 아래로 훑을 때 자릿수가
    # 맞아야 "어느 쪽이 큰가"가 읽지 않고도 보인다. 왼쪽 정렬이면 `0.8 TB`와
    # `12.4 TB`의 시작점이 같아 매번 숫자를 읽어야 한다.
    NUMERIC_COLUMNS = (COL_SIZE, COL_INODE, COL_QUOTA)

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
        if not self._config.accounts:
            self.hero_state_label.setText(i18n.t("dashboard.no_accounts"))
            self.hero_value_label.setText(i18n.t("common.none"))
            self.hero_detail_label.setText("")
            self._apply_hero_tier(tiers.UNKNOWN)
            self._update_hero_stats(0)
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
            text = f"{warning} · {text}"
            banner_tier = tiers.worse(banner_tier, tiers.ALERT)

        self.hero_state_label.setText(text)

        # 큰 숫자에는 "가장 높은 사용률"을 둔다. 여러 계정을 한 화면에서 볼 때
        # 사람이 실제로 찾는 값이 그것이다.
        self.hero_value_label.setText(
            f"{self._worst_pct:.1f}%" if self._worst_pct is not None else i18n.t("common.none")
        )
        self.hero_value_label.setStyleSheet(f"color: {tiers.color(banner_tier)};")
        self.hero_detail_label.setText(
            i18n.t("dashboard.hero_detail", account=self._worst_account_name)
            if self._worst_account_name
            else ""
        )
        self._apply_hero_tier(banner_tier)
        self._update_hero_stats(warn_or_worse_count)

    def _apply_hero_tier(self, tier: str) -> None:
        self.hero_accent.setStyleSheet(HERO_ACCENT_STYLE.format(color=tiers.color(tier)))

    def _update_hero_stats(self, warn_or_worse_count: int) -> None:
        newest = [
            info.age_seconds
            for info in self._freshness.values()
            if getattr(info, "age_seconds", None) is not None
        ]
        collected = freshness.format_age(min(newest)) if newest else i18n.t("common.none")

        for key, value in (
            ("accounts", str(len(self._config.accounts))),
            ("attention", str(warn_or_worse_count)),
            ("collected", collected),
        ):
            caption, value_label = self.hero_stats[key]
            caption.setText(i18n.t(f"dashboard.stat.{key}"))
            value_label.setText(value)

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
            self._scan_tab.refresh()
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
        """홈 탭 위쪽의 "지금 스캔 중" 배너.

        스캔 탭이 위젯을 직접 만지지 않고 값만 보내 온다 - 그래야 두 화면을
        따로 옮길 수 있다."""

        self.home_scan_banner.setVisible(state.running)
        if not state.running:
            return
        self.home_scan_label.setText(
            i18n.t("scan.scanning_now", path=state.path)
            if state.path
            else i18n.t("scan.started")
        )
        if state.total:
            self.home_scan_progress.setRange(0, state.total)
            self.home_scan_progress.setValue(state.done)
        else:
            self.home_scan_progress.setRange(0, 0)

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
        super().closeEvent(event)

