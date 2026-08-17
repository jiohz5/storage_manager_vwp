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

from PyQt5.QtCore import Qt, QTimer
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_module
from .. import diagnostics, forecast_notify, freshness, i18n, nightly_scan, quota, store, tiers
from ..scheduler import CollectorScheduler, NightlyScanWorker
from . import theme, widgets
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

# 경로 열의 최저 폭. 칸 좌우 여백(QSS `::item` padding 8px씩)을 빼고도 실제
# 운영 경로(`/user/project_a` 계열, 실측 약 110px)가 넉넉히 들어간다.
# 이보다 긴 경로는 잘리지만 툴팁에 전체가 남는다 - 열 하나가 표를 다 먹는
# 것보다는 낫다.
PATH_MIN_WIDTH = 240

GROWTH_COLUMN_KEYS = ["scan.col.path", "scan.col.current_size", "scan.col.delta"]

# 스캔이 도는 동안 진행 상황(남은 체크포인트 수)을 주기적으로 다시 읽는 간격.
SCAN_STATUS_REFRESH_MS = 5000

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
        layout.setContentsMargins(theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD, theme.PAD_CARD)
        layout.setSpacing(theme.GAP_SECTION)

        layout.addWidget(self._build_hero())

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
        self.table.itemSelectionChanged.connect(self._refresh_growth_table)

        # 계정 표와 스캔 섹션을 스플리터로 묶는다.
        #
        # 그냥 세로로 쌓으면 스캔 섹션이 자기 sizeHint(365px)를 먼저 받아 가고
        # 계정 표에는 남는 것만 돌아온다 - 실제로 재 보니 계정 5개에 260px가
        # 필요한데 190px만 받아 행이 중간에서 잘렸다. 계정 수는 현장마다 다르니
        # 비율을 코드로 못박기보다 사용자가 끌어서 정하게 두는 편이 낫다.
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)
        # 헤더 + 두 행 정도는 어떤 창 크기에서도 남긴다.
        self.table.setMinimumHeight(140)
        splitter.addWidget(self.table)
        splitter.addWidget(self._build_scan_section())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([520, 280])
        layout.addWidget(splitter, 1)

        self.status_bar_label = QLabel("")
        self.statusBar().addPermanentWidget(self.status_bar_label)

        self.setCentralWidget(central)

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

    def _build_scan_section(self) -> QWidget:
        """야간 상세 스캔 영역 - 탭을 새로 만들지 않고 같은 화면 아래쪽에
        붙인다 (DESIGN.md 2부 6절 "대시보드 단일 화면" 결정 유지)."""

        section = QFrame()
        section.setFrameShape(QFrame.StyledPanel)
        section.setObjectName("card")
        box = QVBoxLayout(section)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(9)

        self.scan_title_label = QLabel()
        self.scan_title_label.setObjectName("sectionTitle")
        box.addWidget(self.scan_title_label)

        self.scan_status_label = QLabel()
        self.scan_status_label.setWordWrap(True)
        box.addWidget(self.scan_status_label)

        # 스캔이 도는 동안 좌우로 오가는 막대.
        #
        # 일부러 **불확정(indeterminate)** 막대를 쓴다. 남은 체크포인트 수는
        # 알지만 전체 분모는 모른다 - 디렉터리를 분할하면 작업이 늘어나서
        # 진행률이 뒤로 갈 수도 있다. 그럴 바에는 퍼센트를 지어내지 않고
        # "지금 일하는 중"만 정직하게 보여준다 (DESIGN.md 1부 "과장하지 않는
        # UI"). 남은 작업 수는 옆 상태 줄에 숫자 그대로 나온다.
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
        scan_buttons.addWidget(self.scan_run_btn)
        scan_buttons.addWidget(self.scan_stop_btn)
        scan_buttons.addStretch(1)
        box.addLayout(scan_buttons)

        self.growth_caption = QLabel()
        self.growth_caption.setObjectName("muted")
        self.growth_caption.setWordWrap(True)
        box.addWidget(self.growth_caption)

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
        # 이 표는 보조 정보다. 최소 높이를 낮게 잡아 두지 않으면 계정 표(이
        # 화면의 주인공)를 아래에서 밀어 올려 행이 잘린다.
        self.growth_table.setMinimumHeight(80)
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
        self.collect_btn.setToolTip(i18n.t("dashboard.btn.collect_now_tooltip"))
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
        # 히어로의 큰 숫자에 쓸 값 (가장 높은 사용률과 그 계정).
        self._worst_pct: Optional[float] = None
        self._worst_account_name: Optional[str] = None
        # 행 수가 줄어든 경우 이전 행의 색이 남지 않도록 매번 비운다.
        self._row_tints.clear()

        for row, account in enumerate(accounts):
            sample = latest.get(account.account_id)
            name_item = QTableWidgetItem(account.name)
            self.table.setItem(row, COL_NAME, name_item)
            path_item = QTableWidgetItem(account.path)
            # 폭이 모자라 잘리더라도 전체 경로는 확인할 수 있어야 한다.
            path_item.setToolTip(account.path)
            self.table.setItem(row, COL_PATH, path_item)

            if sample is None:
                for column in (COL_FS, COL_INODE, COL_QUOTA, COL_FORECAST, COL_TIME):
                    self.table.setItem(row, column, QTableWidgetItem(dash))
                self.table.setCellWidget(row, COL_BYTE, widgets.UsageBar(None, tiers.UNKNOWN))
                self.table.setCellWidget(row, COL_TIER, widgets.badge_cell(tiers.UNKNOWN, None))
                self.table.setItem(row, COL_STATUS, QTableWidgetItem(i18n.t("dashboard.not_collected")))
                continue

            self.table.setItem(row, COL_FS, QTableWidgetItem(sample.filesystem or dash))
            inode_text = (
                f"{sample.inode_pct:.1f}%"
                if sample.inode_pct is not None
                else i18n.t("common.unknown_value")
            )
            self.table.setCellWidget(
                row, COL_BYTE, widgets.UsageBar(sample.byte_pct, sample.overall_tier)
            )
            self.table.setItem(row, COL_INODE, QTableWidgetItem(inode_text))
            self.table.setItem(row, COL_QUOTA, QTableWidgetItem(quota.format_usage(sample)))

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

    @staticmethod
    def _scan_cpu_text(latest_run) -> str:
        """직전 스캔이 이 장비 CPU를 얼마나 썼는지.

        상세 스캔이 얼마나 무거운지는 계정 크기·파일 수·파일시스템에 따라
        달라서 **미리 예측할 수 없다.** 대신 실제로 돈 결과를 남겨 두면 다음
        실행 전에 "지난번엔 이 정도였다"로 판단할 수 있다.

        `top` 기준(코어 1개 = 100%)을 함께 적는 이유: 사용자가 top을 띄워 놓고
        대조할 때 숫자가 맞아야 하기 때문."""

        if not latest_run:
            return ""
        try:
            avg = latest_run["cpu_top_percent_avg"]
            peak = latest_run["cpu_top_percent_peak"]
            system_avg = latest_run["cpu_system_percent_avg"]
        except (KeyError, IndexError):
            return ""
        if avg is None or peak is None:
            return ""
        return i18n.t(
            "scan.cpu_usage",
            avg=f"{avg:.0f}",
            peak=f"{peak:.0f}",
            system=f"{system_avg:.1f}" if system_avg is not None else "-",
        )

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
        cpu_text = self._scan_cpu_text(latest)
        if cpu_text:
            parts.append(cpu_text)
        self.scan_status_label.setText("  |  ".join(parts))

        # 막대는 도는 동안에만 보인다. 멈춰 있는데도 계속 떠 있으면 "뭔가
        # 돌고 있나?"라는 오해를 만든다.
        self.scan_progress.setVisible(running)

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
            # 기준선이 없는 이유가 "아직 안 돌았다"가 아니라 "돌았는데 전부
            # 실패했다"일 수 있다. 그 경우 실패 사유를 보여 주지 않으면 스캔이
            # 즉시 끝나 버린 것처럼만 보인다.
            if entry is not None and entry.failed_count:
                self.growth_caption.setText(self._scan_failure_text(entry, account.name))
            else:
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
        if entry.failed_count:
            activity_note += "\n" + self._scan_failure_text(entry, account.name)

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
