"""상세 스캔 탭의 하위 탭 네 장 - 요약 / 계정별 / 증가 경로 / 큰 파일.

## 왜 따로 떼었나

`scan_tab.py` 가 1,400줄에 클래스 하나였다. 머리(지금 상태·cron·동작 버튼)와
작업 스레드·자동 시작, 그리고 쪽 네 장의 그리기가 한 클래스에 섞여 있어서,
표 하나를 고치려 해도 스캔 시작 코드 사이를 지나가야 했다.

쪽마다 클래스 하나다. **쪽은 그리기만 한다** - 무엇을 그릴지(스냅샷, 고른
계정)는 탭이 넘겨 주고, 사람이 쪽에서 무엇을 고르면 신호로 탭에 알린다.
쪽은 다른 쪽도, 탭의 계정 선택 콤보도 모른다. 창↔탭 경계와 같은 방식이다.

- `SummaryPage.finding_chosen(계정id, 탭)` - 살펴볼 것 한 줄을 눌렀다
- `AccountsPage.account_selected(계정id)` - 행을 골랐다
- `AccountsPage.account_opened(계정id)` - 행을 두 번 눌렀다
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import formatting, i18n, large_files, scan_digest, tiers
from . import theme, widgets

# 세부 탭. 순서가 곧 화면 순서다 - 요약이 먼저 열린다.
TAB_KEYS = ["scan.tab.summary", "scan.tab.accounts", "scan.tab.growth", "scan.tab.large"]
(TAB_SUMMARY, TAB_ACCOUNTS, TAB_GROWTH, TAB_LARGE) = range(4)

# 살펴볼 것 한 줄을 누르면 어느 탭으로 가나. 못 잰 것·덜 센 것은 증가 경로
# 탭 설명에 사유가 나오므로 그쪽이다.
FINDING_TABS = {
    scan_digest.FINDING_LARGE_FILE: TAB_LARGE,
    scan_digest.FINDING_GROWTH: TAB_GROWTH,
    scan_digest.FINDING_FAILED: TAB_GROWTH,
    scan_digest.FINDING_PARTIAL: TAB_GROWTH,
    # 계정을 통째로 건너뛴 것은 계정 탭에서 그 계정의 지난 결과와 함께 본다.
    scan_digest.FINDING_UNAVAILABLE: TAB_ACCOUNTS,
}
FINDING_ACCOUNT_ROLE = Qt.UserRole
FINDING_TAB_ROLE = Qt.UserRole + 1

GROWTH_COLUMN_KEYS = ["scan.col.path", "scan.col.current_size", "scan.col.delta"]
(GROWTH_PATH, GROWTH_SIZE, GROWTH_DELTA) = range(3)

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

# 화면에 띄우는 줄 수. 반쪽 폭을 나눠 쓸 때는 12 줄로 묶었는데, 탭을 통째로
# 쓰게 된 뒤로는 묶을 까닭이 없다. 저장소가 계정마다 20 개쯤 남기므로 사실상
# 전부 보인다.
LARGE_ROWS_SHOWN = 50

# 계정별 현황 표.
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


# -- 쪽들이 같이 쓰는 것 ---------------------------------------------------------
def status_text(status) -> str:
    """실행 상태를 사람 말로. 모르는 값은 그대로 보여 준다.

    탭 머리("대기 중 · 지난 실행: …")와 요약 카드가 같이 쓴다."""

    if not status:
        return i18n.t("common.none")
    key = f"digest.status.{status}"
    text = i18n.t(key)
    return status if text == key else text


def format_duration(seconds: float) -> str:
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


def _page_box(page: QWidget) -> QVBoxLayout:
    """쪽 하나의 틀. 위쪽 설명 한 줄 + 나머지 전부를 쓰는 내용."""

    page.setObjectName("subPage")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 12, 0, 0)
    layout.setSpacing(8)
    return layout


def _caption() -> QLabel:
    label = QLabel()
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


def _table(columns: int) -> QTableWidget:
    # 폭 정책은 표마다 다르게 정하므로 여기서는 정하지 않는다.
    return widgets.read_only_table(columns, row_height=34, stretch_column=None)


def _root_note(root) -> str:
    """설명 끝에 붙이는 "경로는 어디 기준" 한 마디."""

    return "  ·  " + i18n.t("scan.paths_under", root=root) if root else ""


def _path_item(path, root) -> QTableWidgetItem:
    """계정 기준으로 줄인 경로 칸. 줄였으면 전체 경로를 툴팁에 둔다."""

    item = QTableWidgetItem(formatting.relative_path(path, root))
    if item.text() != path:
        item.setToolTip(path)
    return item


def _entry_for(snapshot, account_id):
    if snapshot is None or not account_id:
        return None
    return next(
        (item for item in snapshot.accounts if item.account_id == account_id), None
    )


def scan_failure_text(entry, account_name: str) -> str:
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


# -- 1. 요약 -------------------------------------------------------------------
class SummaryPage(QWidget):
    """카드 넷과 살펴볼 것.

    표를 읽고 스스로 요약을 만들라고 하면 대부분 안 읽는다. 아침에 사람이
    실제로 묻는 넷을 카드 하나씩으로 세운다: 잘 돌았나, 얼마나 늘었나, 어느
    계정이, 손댈 것이 있나. 근거는 다른 탭에 그대로 있다."""

    # 살펴볼 것 한 줄을 눌렀다 (계정 id, 갈 탭).
    finding_chosen = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = _page_box(self)

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
        layout.addLayout(cards)

        layout.addSpacing(6)
        self.findings_caption = QLabel()
        self.findings_caption.setObjectName("sectionTitle")
        layout.addWidget(self.findings_caption)
        self.findings_hint = QLabel()
        self.findings_hint.setObjectName("caption")
        self.findings_hint.setWordWrap(True)
        layout.addWidget(self.findings_hint)

        # 실패·권한 부족·튀는 파일·크게 는 계정을 한 목록으로 모은다. 한 줄을
        # 누르면 그 계정의 해당 탭으로 간다 - 요약이 근거로 가는 입구다.
        self.findings_list = QListWidget()
        self.findings_list.setObjectName("findings")
        self.findings_list.setFrameShape(QListWidget.NoFrame)
        self.findings_list.setSelectionMode(QListWidget.NoSelection)
        self.findings_list.setFocusPolicy(Qt.NoFocus)
        # 문장이 폭을 넘으면 옆으로 밀지 않고 접는다.
        self.findings_list.setWordWrap(True)
        self.findings_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.findings_list.setResizeMode(QListWidget.Adjust)
        self.findings_list.viewport().setCursor(Qt.PointingHandCursor)
        self.findings_list.itemClicked.connect(self._on_finding_clicked)
        layout.addWidget(self.findings_list, 1)

    def retranslate(self) -> None:
        self.findings_caption.setText(i18n.t("digest.findings_heading"))
        self.findings_hint.setText(i18n.t("digest.findings_hint"))

    def show_digest(self, digest) -> None:
        """아침에 사람이 실제로 묻는 것은 넷뿐이다 - 잘 돌았나, 얼마나 늘었나,
        어느 계정이, 손댈 것이 있나. 표 셋을 읽고 스스로 요약을 만들게 하는
        대신 답을 먼저 세운다."""

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
                    status=status_text(digest.run_status),
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

        self._show_findings(digest)

    def _show_findings(self, digest) -> None:
        self.retranslate()
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
            item.setData(FINDING_ACCOUNT_ROLE, finding.account_id)
            item.setData(FINDING_TAB_ROLE, FINDING_TABS.get(finding.kind, TAB_GROWTH))
            self.findings_list.addItem(item)

    def _on_finding_clicked(self, item) -> None:
        """살펴볼 것 한 줄에서 그 계정의 해당 탭으로 간다.

        요약만 있고 근거로 가는 길이 없으면, 사람이 계정 이름을 외워 콤보에서
        다시 찾아야 한다. 그러면 요약은 읽고 끝나는 글이 된다."""

        account_id = item.data(FINDING_ACCOUNT_ROLE)
        if not account_id:
            return
        self.finding_chosen.emit(account_id, item.data(FINDING_TAB_ROLE) or TAB_GROWTH)

    @staticmethod
    def _finding_text(finding) -> str:
        """살펴볼 것 한 줄을 사람 문장으로.

        표의 한 행이 아니라 문장으로 쓰는 것은 의도다 - 이 목록은 훑어보라고
        있는 것이고, 훑을 때는 열을 따라 읽는 것보다 문장이 빠르다."""

        if finding.kind == scan_digest.FINDING_UNAVAILABLE:
            return formatting.unavailable_text(
                finding.reason, finding.detail, finding.when, finding.account
            )
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

    @staticmethod
    def _duration_text(seconds) -> str:
        if seconds is None:
            return i18n.t("common.none")
        hours, rest = divmod(int(seconds), 3600)
        minutes = rest // 60
        if hours:
            return i18n.t("digest.duration_hm", hours=hours, minutes=minutes)
        return i18n.t("digest.duration_m", minutes=minutes)


# -- 2. 계정별 -------------------------------------------------------------------
class AccountsPage(QWidget):
    """계정별 진행·측정량·예상 남은 시간."""

    # 행을 골랐다 / 두 번 눌렀다 (계정 id). 고르면 탭 줄의 계정 선택이 따라오고,
    # 두 번 누르면 그 계정의 증가 경로로 간다 - "이 계정이 이상한데" 에서
    # 곧장 파고들 수 있게.
    account_selected = pyqtSignal(str)
    account_opened = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = _page_box(self)
        self.caption = _caption()
        layout.addWidget(self.caption)

        table = _table(len(SCAN_ACCOUNT_COLUMN_KEYS))
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(SCAN_ACCT_NOTE, QHeaderView.Stretch)
        # 격자선을 끈 표라, 열이 내용 폭에 딱 붙으면 옆 칸 값과 한 덩어리로
        # 읽힌다 ("약 21분 아직 없음"). 최소 폭으로 숨 쉴 자리를 만든다.
        header.setMinimumSectionSize(80)
        table.itemSelectionChanged.connect(self._on_row_selected)
        table.cellDoubleClicked.connect(self._on_row_opened)
        self.table = table
        layout.addWidget(table, 1)

    def retranslate(self) -> None:
        self.caption.setText(i18n.t("scan.acct.heading"))
        self.table.setHorizontalHeaderLabels(
            [i18n.t(key) for key in SCAN_ACCOUNT_COLUMN_KEYS]
        )

    def _account_at(self, row: int) -> str:
        item = self.table.item(row, SCAN_ACCT_NAME) if row >= 0 else None
        return item.data(Qt.UserRole) if item is not None else ""

    def _on_row_selected(self) -> None:
        account_id = self._account_at(self.table.currentRow())
        if account_id:
            self.account_selected.emit(account_id)

    def _on_row_opened(self, row: int, _column: int) -> None:
        account_id = self._account_at(row)
        if account_id:
            self.account_opened.emit(account_id)

    def mark_account(self, account_id) -> None:
        """다른 곳(콤보)에서 고른 계정의 행을 짚는다. 신호는 다시 내지 않는다.

        예전에는 표 -> 콤보 방향만 맞췄다. 그러면 콤보로 계정을 바꿔도 표는
        엉뚱한 행이 선택된 채 남아, **두 조작이 서로 다른 것을 가리키는 것처럼**
        보였다."""

        for row in range(self.table.rowCount()):
            if self._account_at(row) == account_id:
                if self.table.currentRow() != row:
                    # 표 선택이 다시 콤보를 건드리지 않게 막는다 (무한 왕복).
                    self.table.blockSignals(True)
                    self.table.selectRow(row)
                    self.table.blockSignals(False)
                return

    def show_accounts(self, snapshot, accounts, running: bool) -> None:
        entries = {item.account_id: item for item in snapshot.accounts}
        dash = i18n.t("common.none")
        self.retranslate()

        table = self.table
        # 칸 위젯은 안 쓰지만, 행 수가 줄어든 경우 이전 내용이 남지 않도록
        # 매번 비우고 다시 채운다.
        table.setRowCount(0)
        table.setRowCount(len(accounts))

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
                    "scan.acct.eta_value", duration=format_duration(entry.eta_seconds)
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


# -- 3. 증가 경로 ---------------------------------------------------------------
class GrowthPage(QWidget):
    """고른 계정의 경로별 증감. 비교할 이전 스캔이 없으면 큰 경로."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = _page_box(self)
        self.caption = _caption()
        layout.addWidget(self.caption)

        table = _table(len(GROWTH_COLUMN_KEYS))
        header = table.horizontalHeader()
        header.setSectionResizeMode(GROWTH_PATH, QHeaderView.Stretch)
        header.setSectionResizeMode(GROWTH_SIZE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(GROWTH_DELTA, QHeaderView.ResizeToContents)
        # 이 표에는 칸 위젯이 없어 Qt 내장 정렬을 그대로 쓸 수 있다.
        # (계정 표는 막대·배지가 칸 위젯이라 Qt 정렬을 켜면 위젯이 행을 따라
        #  움직이지 않아 어긋난다 - 그쪽은 직접 정렬해 다시 그린다.)
        table.setSortingEnabled(True)
        header.setSortIndicatorShown(True)
        # 사용자가 한 번이라도 헤더를 누르면 그 정렬을 존중한다 - 갱신할
        # 때마다 기본값으로 되돌리면 정렬을 바꾼 의미가 없다.
        self._sort_touched = False
        header.sectionClicked.connect(self._on_header_clicked)
        self.table = table
        layout.addWidget(table, 1)

    def _on_header_clicked(self, _index: int) -> None:
        self._sort_touched = True

    def retranslate(self) -> None:
        self.table.setHorizontalHeaderLabels(
            [i18n.t(key) for key in GROWTH_COLUMN_KEYS]
        )

    def show_account(self, snapshot, account) -> None:
        table = self.table
        if snapshot is None or account is None:
            table.setRowCount(0)
            self.caption.setText(i18n.t("scan.select_account"))
            return

        entry = _entry_for(snapshot, account.account_id)
        if entry is None or entry.last_completed_generation is None:
            table.setRowCount(0)
            # 기준선이 없는 이유가 "아직 안 돌았다"가 아니라 "돌았는데 전부
            # 실패했다"일 수 있다. 그 경우 실패 사유를 보여 주지 않으면 스캔이
            # 즉시 끝나 버린 것처럼만 보인다.
            if entry is not None and entry.failed_count:
                self.caption.setText(scan_failure_text(entry, account.name))
            else:
                self.caption.setText(i18n.t("scan.no_baseline", account=account.name))
            return

        # 표의 경로는 계정 기준으로 줄여 보여 준다. 무엇을 기준으로 줄였는지는
        # 여기 한 번만 적는다. 권한 부족으로 축소 측정된 경로가 있으면 반드시
        # 알린다 - 모르고 보면 "안 늘었네"로 잘못 읽는다.
        notice = _root_note(account.path)
        if entry.partial_paths:
            notice += "\n" + i18n.t(
                "scan.partial_warning", count=len(entry.partial_paths)
            )
        if entry.failed_count:
            notice += "\n" + scan_failure_text(entry, account.name)

        # 채우는 동안은 정렬을 끈다 (행마다 다시 정렬하면 칸이 엉뚱한 행에 꽂힌다).
        table.setSortingEnabled(False)
        if entry.growth:
            default_column = self._show_growth(entry, account, notice)
        else:
            default_column = self._show_biggest(entry, account, notice)

        # 다 채운 뒤 정렬을 되살린다. **두 경우 모두.** 예전에는 증감이 있는
        # 쪽(= 스캔이 두 번 이상 돈 평소의 경우)이 정렬을 되살리기 전에 먼저
        # 돌아가 버려서, 헤더를 눌러도 정렬이 안 됐다.
        #
        # 기본은 증감이 있으면 **많이 는 순**, 없으면 **큰 순**이다 - 이 화면을
        # 보는 이유가 그것이다. 사람이 헤더를 눌러 바꾼 정렬은 Qt 가 지켜 준다.
        if not self._sort_touched:
            table.sortItems(default_column, Qt.DescendingOrder)
        table.setSortingEnabled(True)

    def _show_growth(self, entry, account, notice: str) -> int:
        previous = formatting.scan_label(
            entry.previous_scan_at, (entry.last_completed_generation or 1) - 1
        )
        self.caption.setText(
            i18n.t(
                "scan.growth_caption",
                account=account.name,
                current=formatting.scan_label(
                    entry.current_scan_at, entry.last_completed_generation
                ),
                previous=previous,
                notice=notice,
            )
        )
        # 열 제목에 비교 대상 날짜를 박는다 - "이전 스캔 대비"보다
        # "260819 대비"가 무엇과 비교한 값인지 스스로 설명한다.
        self.table.setHorizontalHeaderItem(
            GROWTH_DELTA,
            QTableWidgetItem(
                i18n.t("scan.col.delta_dated", previous=previous)
                if entry.previous_scan_at
                else i18n.t("scan.col.delta")
            ),
        )
        self.table.setRowCount(len(entry.growth))
        for index, row in enumerate(entry.growth):
            current_kb = row["current_kb"]
            previous_kb = row["previous_kb"]
            self.table.setItem(index, GROWTH_PATH, _path_item(row["path"], account.path))
            self.table.setItem(
                index, GROWTH_SIZE,
                widgets.NumericItem(formatting.format_kb(current_kb), current_kb),
            )
            if previous_kb is None:
                delta_text = i18n.t("scan.new_path")
            else:
                delta_text = formatting.format_kb_delta(current_kb - previous_kb)
            # 새 경로는 통째로 늘어난 것으로 본다 (저장소가 고른 순서와 같다).
            delta_value = (
                current_kb - previous_kb if previous_kb is not None else current_kb
            )
            self.table.setItem(
                index, GROWTH_DELTA, widgets.NumericItem(delta_text, delta_value)
            )
        return GROWTH_DELTA

    def _show_biggest(self, entry, account, notice: str) -> int:
        self.caption.setText(
            i18n.t(
                "scan.baseline_only_caption",
                account=account.name,
                current=formatting.scan_label(
                    entry.current_scan_at, entry.last_completed_generation
                ),
                notice=notice,
            )
        )
        self.table.setRowCount(len(entry.top_paths))
        dash = i18n.t("common.none")
        for index, row in enumerate(entry.top_paths):
            self.table.setItem(index, GROWTH_PATH, _path_item(row["path"], account.path))
            self.table.setItem(
                index, GROWTH_SIZE,
                widgets.NumericItem(formatting.format_kb(row["size_kb"]), row["size_kb"]),
            )
            self.table.setItem(index, GROWTH_DELTA, widgets.NumericItem(dash, None))
        return GROWTH_SIZE


# -- 4. 큰 파일 -------------------------------------------------------------------
class LargeFilesPage(QWidget):
    """가장 큰 파일 표.

    디렉터리 합계만 보면 300GB 짜리가 고른 파일 3천 개인지 한 파일이
    280GB 인지 구분되지 않는다. 후자는 사람이 바로 손댈 수 있는 것이라
    따로 보여 줄 값어치가 있다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = _page_box(self)
        self.caption = _caption()
        self.caption.setToolTip(i18n.t("large.tip"))
        layout.addWidget(self.caption)

        table = _table(len(LARGE_COLUMN_KEYS))
        header = table.horizontalHeader()
        header.setSectionResizeMode(LARGE_PATH, QHeaderView.Stretch)
        for column in (LARGE_SIZE, LARGE_SHARE, LARGE_CHANGE):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table = table
        layout.addWidget(table, 1)

    def retranslate(self) -> None:
        self.table.setHorizontalHeaderLabels(
            [i18n.t(key) for key in LARGE_COLUMN_KEYS]
        )

    def show_account(self, snapshot, account: Optional[object]) -> None:
        table = self.table
        table.setRowCount(0)
        entry = _entry_for(snapshot, account.account_id if account else None)
        if entry is None:
            self.caption.setText(i18n.t("large.no_scan"))
            return

        files = large_files.build(entry.large_files, entry.measured_kb)
        if not files:
            self.caption.setText(i18n.t("large.none", account=entry.account_name))
            return

        root = account.path if account else ""
        shown = files[:LARGE_ROWS_SHOWN]
        self.caption.setText(
            i18n.t("large.heading", account=entry.account_name, count=len(shown))
            + _root_note(root)
        )
        table.setRowCount(len(shown))
        dash = i18n.t("common.none")
        for row, item in enumerate(shown):
            path_item = QTableWidgetItem(formatting.relative_path(item.path, root))
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
            # 줄여 보여 준 경로는 전체를 툴팁에 둔다 - 사람이 그 경로로 가야 한다.
            tip = reasons + ([item.path] if path_item.text() != item.path else [])
            if tip:
                path_item.setToolTip("\n".join(tip))
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
