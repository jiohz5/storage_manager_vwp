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

from types import SimpleNamespace
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

from .. import formatting, i18n, large_files, scan_digest, scan_store, tiers
from . import theme, visuals, widgets

# 세부 탭. 순서가 곧 화면 순서다 - 현황(`overview_page`)이 먼저 열린다.
TAB_KEYS = ["scan.tab.overview", "scan.tab.summary", "scan.tab.accounts", "scan.tab.growth", "scan.tab.large"]
(TAB_OVERVIEW, TAB_SUMMARY, TAB_ACCOUNTS, TAB_GROWTH, TAB_LARGE) = range(5)

# 살펴볼 것 한 줄을 누르면 어느 탭으로 가나. 못 잰 것·덜 센 것은 증가 경로
# 탭 설명에 사유가 나오므로 그쪽이다.
FINDING_TABS = {
    scan_digest.FINDING_LARGE_FILE: TAB_LARGE,
    scan_digest.FINDING_GROWTH: TAB_GROWTH,
    # 오래된 폴더는 용량 변화 탭에서 그 계정을 고른 채 '폴더 펼쳐 보기' 로 이어 본다.
    scan_digest.FINDING_COLD: TAB_GROWTH,
    scan_digest.FINDING_FAILED: TAB_GROWTH,
    scan_digest.FINDING_PARTIAL: TAB_GROWTH,
    # 계정을 통째로 건너뛴 것은 계정 탭에서 그 계정의 지난 결과와 함께 본다.
    scan_digest.FINDING_UNAVAILABLE: TAB_ACCOUNTS,
}
FINDING_ACCOUNT_ROLE = Qt.UserRole
FINDING_TAB_ROLE = Qt.UserRole + 1

GROWTH_COLUMN_KEYS = ["scan.col.path", "scan.col.current_size", "scan.col.delta",
                      "scan.col.trend", "scan.col.focus"]
(GROWTH_PATH, GROWTH_SIZE, GROWTH_DELTA, GROWTH_TREND, GROWTH_FOCUS) = range(5)

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


# -- 용량 변화 한 줄 (상세 스캔 탭과 계정 상세 창이 같이 쓴다) -------------------
def growth_change(row) -> Optional[int]:
    """그 줄의 증감 (KB). 새 경로는 크기 전체, 지난번에 못 잰 곳은 None (모른다)."""

    row = dict(row)
    if row.get("previous_note") == scan_store.PREVIOUS_UNMEASURED:
        return None
    previous = row["previous_kb"]
    return row["current_kb"] - (previous if previous is not None else 0)


def growth_delta_item(row) -> QTableWidgetItem:
    """증감 칸 - 글자, 정렬 값, 막대 값, 색, 툴팁.

    늘어난 것은 청록, 줄어든 것은 회청 (`theme.CHANGE_*` - 위험 색이 아니다). 지난번에 못 잰
    곳은 '지난번 못 잼' - 크기 전체를 증가로 치면 없던 급증이 생긴다. 지난번에
    나눠 잰 곳은 조각의 합과 견준 것이라 그렇다고 툴팁에 적는다."""

    row = dict(row)
    note = row.get("previous_note")
    change = growth_change(row)
    if note == scan_store.PREVIOUS_UNMEASURED:
        # 정렬은 '변화 0' 으로 - 값 없음(None)으로 두면 내림차순에서 맨 위로 뜬다.
        item = widgets.NumericItem(i18n.t("growth.unmeasured"), 0)
        item.setToolTip(i18n.t("growth.unmeasured_tip"))
        item.setForeground(QColor(theme.TEXT_MUTED))
        item.setData(widgets.CellDelegate.CHIP_ROLE, (item.text(), "soft"))
    else:
        text = (
            i18n.t("scan.new_path") if row["previous_kb"] is None
            else formatting.format_kb_delta(change)
        )
        item = widgets.NumericItem(text, change)
        item.setData(widgets.ChangeBarDelegate.VALUE_ROLE, change)
        if note == scan_store.PREVIOUS_PIECED:
            item.setToolTip(i18n.t("tree.note.previous_pieced"))
        if change > 0:
            item.setForeground(QColor(theme.CHANGE_UP))
        elif change < 0:
            item.setForeground(QColor(theme.CHANGE_DOWN))
        else:
            item.setForeground(QColor(theme.TEXT_MUTED))
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return item


def growth_focus_item(row) -> QTableWidgetItem:
    """'가장 많이 변한 곳' 칸 - 그 줄의 변화가 안쪽 어디에 몰렸나.

    야간 스캔이 요약을 남길 때 따라 내려가 둔 것이다 (`scan_store.change_focus`).
    요약 없이 창이 스스로 읽었으면 비어 있다. 변화가 한 곳으로 모이지 않았으면
    '여러 곳에 고르게' - 아무 데나 지목하면 틀린 안내가 된다. 하위 폴더가 아니라
    폴더에 직접 있는 파일이 변했으면 그렇다고 쓴다."""

    row = dict(row)
    item = QTableWidgetItem("")
    focus = row.get("focus")
    if not focus:
        return item
    change = growth_change(row)
    note = focus.get("note")
    if note == scan_store.FOCUS_SPREAD:
        item.setText(i18n.t("growth.focus.spread"))
        item.setForeground(QColor(theme.TEXT_MUTED))
        item.setData(widgets.CellDelegate.CHIP_ROLE, (item.text(), "soft"))
        return item
    where = formatting.relative_path(focus["path"], row["path"])
    if focus["path"].rstrip("/") == row["path"].rstrip("/"):
        where = ""
    delta = formatting.format_kb_delta(focus["delta_kb"])
    key = {
        scan_store.FOCUS_NEW: "growth.focus.new",
        scan_store.FOCUS_GONE: "growth.focus.gone",
        scan_store.FOCUS_FILES: "growth.focus.files_in" if where else "growth.focus.files",
    }.get(note, "growth.focus")
    sentence = i18n.t(key, where=where, delta=delta)
    # 새 폴더·없어짐·직접 있는 파일은 알약으로 세우고 칸의 글은 '어디 · 얼마' 만 둔다 -
    # 괄호 속 말로 두면 줄마다 읽어야 무슨 일인지 안다. 문장 전체는 툴팁에 있다.
    chip = {
        scan_store.FOCUS_NEW: ("growth.chip.new", "change_up"),
        scan_store.FOCUS_GONE: ("growth.chip.gone", "change_down"),
        scan_store.FOCUS_FILES: ("growth.chip.files", "soft"),
    }.get(note)
    if chip is not None:
        item.setData(widgets.CellDelegate.CHIP_ROLE, (i18n.t(chip[0]), chip[1]))
        item.setText(i18n.t("growth.focus.short", where=where, delta=delta).strip() if where else delta)
    else:
        item.setText(sentence)
    tip = [sentence, focus["path"]]
    if change:
        tip.append(i18n.t(
            "growth.focus.tip", total=formatting.format_kb_delta(change), delta=delta,
            pct=f"{focus['delta_kb'] / change * 100:.0f}",
        ))
    item.setToolTip("\n".join(tip))
    return item


def trend_item(points) -> QTableWidgetItem:
    """'추세' 칸 - 그 폴더의 바퀴마다 크기 `[(끝낸 때, KB), ...]` 를 작은 선으로.

    둘 넘게 있어야 선이다. 툴팁은 처음과 지금 (`2025-11-02 3.1 TB → 2026-11-08 7.9 TB`).
    정렬은 처음 대비 늘어난 양으로."""

    points = list(points or [])
    if len(points) < 2:
        return widgets.NumericItem("", None)
    (first_at, first_kb), (last_at, last_kb) = points[0], points[-1]
    item = widgets.NumericItem("", last_kb - first_kb)
    item.setData(widgets.CellDelegate.SPARK_ROLE, [kb for _at, kb in points])
    item.setToolTip(i18n.t(
        "scan.trend_tip", first=formatting.local_date_text(first_at),
        first_size=formatting.format_kb(first_kb), last=formatting.local_date_text(last_at),
        last_size=formatting.format_kb(last_kb), count=len(points),
    ))
    return item


def growth_totals(entry):
    """견준 두 합계 `(지난번, 이번)` - 한쪽에서 못 잰 곳을 뺀 것. 모르면 None."""

    compared = getattr(entry, "compared_kb", None)
    if compared:
        return tuple(compared)
    current = getattr(entry, "current_measured_kb", None)
    previous = getattr(entry, "previous_measured_kb", None)
    if current is None or previous is None or scan_digest.account_change(entry) is None:
        return None
    return previous, current


def mark_large_file_row(table, row: int, item, path_column: int,
                        share_column: int, change_column: int) -> None:
    """눈에 띄는 큰 파일은 **까닭이 된 칸**을 칠한다 (경로 칸은 눈이 가게 함께).

    예전에는 줄 전체를 칠하고 설명에 "주황색은 계정 안에서 혼자 두드러지는 파일" 이라고
    썼는데, 실제로는 지난 스캔보다 크게 늘었거나 목록에 없던 파일도 같은 색이었다.
    칸을 칠하면 무엇 때문인지가 색만으로 보인다."""

    if not item.is_notable:
        return
    color = QColor(theme.tier_text(tiers.WARN))
    cells = [path_column]
    if item.dominates_account:
        cells.append(share_column)
    if item.grew_sharply or item.is_new_to_the_list:
        cells.append(change_column)
    for column in cells:
        cell = table.item(row, column)
        if cell is not None:
            cell.setForeground(color)


def large_file_reasons(item) -> list:
    """왜 눈에 띄는지 - 사람 말로 (툴팁)."""

    return [
        i18n.t(
            key,
            pct=f"{item.share_pct:.0f}" if item.share_pct else "-",
            ratio=f"{item.size_kb / item.previous_kb:.1f}" if item.previous_kb else "-",
        )
        for key in item.reasons()
    ]


def large_files_legend() -> str:
    return i18n.t(
        "large.legend",
        share=f"{large_files.SHARE_ALERT_PCT:g}", ratio=f"{large_files.GROWTH_ALERT_RATIO:g}",
    )


def _size_item(size_kb, account_kb) -> QTableWidgetItem:
    """크기 칸 - 계정에서의 몫만큼 가는 막대를 글자 왼쪽에 (`CellDelegate.BAR_ROLE`)."""

    item = widgets.NumericItem(formatting.format_kb(size_kb), size_kb)
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    if account_kb and size_kb is not None:
        share = size_kb / account_kb
        item.setData(widgets.CellDelegate.BAR_ROLE, (share, "accent"))
        item.setToolTip(i18n.t("scan.size_share", pct=f"{share * 100:.1f}"))
    return item


def fit_table(table, most: int = 0) -> None:
    """줄이 몇 개뿐인 표가 칸을 다 차지해 빈 흰 면이 되지 않게 높이를 줄 수에 맞춘다.

    줄이 많으면 부모 틀이 허락하는 만큼 서고 나머지는 스크롤이다. `most` 를 주면 그
    줄 수까지만 세운다. 줄이 없으면 머리줄과 한 줄 자리만 남긴다."""

    rows = table.rowCount() if not most else min(table.rowCount(), most)
    height = table.horizontalHeader().sizeHint().height() + 2 * table.frameWidth() + 4
    if rows:
        height += sum(table.rowHeight(row) for row in range(rows))
    else:
        height += table.verticalHeader().defaultSectionSize()
    height += table.horizontalScrollBar().sizeHint().height()
    table.setMaximumHeight(height)


def scan_warning_chips(entry, account) -> list:
    """덜 센 경로·못 잰 경로를 알약으로 `[(글, 종류, 툴팁)]` - 경로와 사유는 툴팁에."""

    chips = []
    if entry.partial_paths:
        chips.append((
            i18n.t("scan.band.partial", count=len(entry.partial_paths)), "stale",
            i18n.t("scan.partial_warning", count=len(entry.partial_paths)) + "\n" + "\n".join(
                formatting.relative_path(path, account.path) for path in entry.partial_paths[:10]
            ),
        ))
    if entry.failed_count:
        chips.append((
            i18n.t("scan.band.failed", count=entry.failed_count), "tier_alert",
            scan_failure_text(entry, account.name),
        ))
    return chips


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
                    theme.CHANGE_UP if digest.total_delta_kb > 0 else None
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
                color=theme.tier_text(tiers.ALERT) if digest.urgent_count else None,
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
                item.setForeground(QColor(theme.tier_text(tiers.ALERT)))
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
            if finding.delta_kb is not None:
                change = formatting.format_kb_delta(finding.delta_kb)
            elif finding.compared:
                change = i18n.t("large.new")
            else:
                # 첫 스캔 - 견줄 목록이 없다. 눈에 띈 까닭은 비중뿐이다.
                change = i18n.t("large.reason.share", pct=f"{finding.pct or 0:.0f}")
            return i18n.t(
                "digest.item.large_file",
                account=finding.account,
                name=finding.path.rsplit("/", 1)[-1],
                size=formatting.format_kb(finding.size_kb),
                change=change,
            )
        if finding.kind == scan_digest.FINDING_COLD:
            return i18n.t(
                "digest.item.cold",
                account=finding.account,
                name=finding.path.rsplit("/", 1)[-1],
                size=formatting.format_kb(finding.size_kb),
                days=finding.count,
                newest=formatting.epoch_date_text(finding.when),
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
                note_item.setForeground(QColor(theme.tier_text(tiers.WARN)))
            table.setItem(row, SCAN_ACCT_NOTE, note_item)


# -- 3. 증가 경로 ---------------------------------------------------------------
class GrowthPage(QWidget):
    """고른 계정의 경로별 증감. 비교할 이전 스캔이 없으면 큰 경로."""

    def __init__(self, parent=None, compact: bool = False):
        super().__init__(parent)
        # 좁은 자리(계정 상세 창) - 칸을 좁히고, 위에 이미 있는 계정 경로는 되풀이하지 않는다.
        self._compact = compact
        layout = _page_box(self)
        # 계정 전체가 얼마나 변했나 - 큰 값과 전후 막대 (`visuals.SummaryBand`).
        self.delta_bar = visuals.DeltaBar()
        self.band = visuals.SummaryBand(self.delta_bar)
        layout.addWidget(self.band)
        self.caption = _caption()
        layout.addWidget(self.caption)
        self._warning_chips = []

        table = _table(len(GROWTH_COLUMN_KEYS))
        header = table.horizontalHeader()
        header.setSectionResizeMode(GROWTH_PATH, QHeaderView.Stretch)
        # 크기 칸에는 계정에서의 몫만큼 가는 막대, 증감 칸에는 증감만큼의 옅은 막대가
        # 들어간다 - 내용 폭에 맞추면 막대가 글자 밑에 숨는다.
        header.setSectionResizeMode(GROWTH_SIZE, QHeaderView.Interactive)
        table.setColumnWidth(GROWTH_SIZE, 130 if compact else 170)
        header.setSectionResizeMode(GROWTH_DELTA, QHeaderView.Interactive)
        table.setColumnWidth(GROWTH_DELTA, 150 if compact else 190)
        header.setSectionResizeMode(GROWTH_FOCUS, QHeaderView.Stretch)
        # 계정 바로 아래 폴더의 장기 이력 (`folder_history`) - "언제부터 이렇게 늘었나".
        header.setSectionResizeMode(GROWTH_TREND, QHeaderView.Fixed)
        table.setColumnWidth(GROWTH_TREND, 80 if compact else 110)
        self.bars = widgets.ChangeBarDelegate(table)
        table.setItemDelegateForColumn(GROWTH_DELTA, self.bars)
        self.cells = widgets.CellDelegate(table)
        for column in (GROWTH_PATH, GROWTH_SIZE, GROWTH_TREND, GROWTH_FOCUS):
            table.setItemDelegateForColumn(column, self.cells)
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
        # 줄이 몇 개뿐이면 표가 줄 수만큼만 서고 남는 자리는 바탕이다 - 빈 흰 면이
        # 화면 반을 차지하지 않게 (`fit_table`).
        layout.addStretch(0)

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
            fit_table(table)
            self.band.setVisible(False)
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
        # 알린다 - 모르고 보면 "안 늘었네"로 잘못 읽는다. 예전에는 설명 줄 밑에
        # 문장으로 붙였다 - 이제 요약 띠의 알약으로 세우고 경로와 사유는 툴팁에 둔다.
        notice = "" if self._compact else _root_note(account.path)
        self.band.setVisible(True)
        self._warning_chips = scan_warning_chips(entry, account)

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
        fit_table(table)

    def _show_growth(self, entry, account, notice: str) -> int:
        previous = formatting.scan_label(
            entry.previous_scan_at, (entry.last_completed_generation or 1) - 1
        )
        current = formatting.scan_label(entry.current_scan_at, entry.last_completed_generation)
        self.caption.setText(
            i18n.t(
                "scan.growth_caption",
                account=account.name, current=current, previous=previous, notice=notice,
            )
        )
        self._show_band(entry, account, previous, current)
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
        changes = [abs(growth_change(row) or 0) for row in entry.growth]
        self.bars.scale = max(changes, default=0)
        account_kb = getattr(entry, "current_measured_kb", None)
        for index, row in enumerate(entry.growth):
            current_kb = row["current_kb"]
            self.table.setItem(index, GROWTH_PATH, _path_item(row["path"], account.path))
            self.table.setItem(index, GROWTH_SIZE, _size_item(current_kb, account_kb))
            # 새 경로는 통째로 늘어난 것으로 본다 (저장소가 고른 순서와 같다).
            self.table.setItem(index, GROWTH_DELTA, growth_delta_item(row))
            self.table.setItem(index, GROWTH_TREND, trend_item(
                (getattr(entry, "folder_trends", None) or {}).get(row["path"])))
            self.table.setItem(index, GROWTH_FOCUS, growth_focus_item(row))
        return GROWTH_DELTA

    def _show_band(self, entry, account, previous: str, current: str) -> None:
        """요약 띠 - 계정 전체 증감(두 바퀴 모두 잰 곳끼리), 전후 막대, 경고 알약."""

        band = self.band
        band.caption.setText(i18n.t("scan.band.total"))
        change = scan_digest.account_change(entry)
        totals = growth_totals(entry)
        if change is None or totals is None:
            band.set_value(formatting.format_kb(getattr(entry, "current_measured_kb", None)))
            band.sub.setText(current)
            self.delta_bar.set_values(None, None)
        else:
            band.set_value(
                formatting.format_kb_delta(change),
                theme.CHANGE_UP if change > 0
                else theme.CHANGE_DOWN if change < 0 else theme.TEXT_MUTED,
            )
            # 어느 두 스캔인지는 바로 밑 설명 줄에 있다 - 띠에는 두 합계만.
            band.sub.setText(i18n.t(
                "scan.band.totals", previous=formatting.format_kb(totals[0]),
                current=formatting.format_kb(totals[1]),
            ))
            self.delta_bar.set_values(*totals)
        chips = list(self._warning_chips)
        excluded = list(getattr(entry, "delta_excluded", None) or [])
        if excluded and change is not None:
            chips.insert(0, (
                i18n.t("scan.band.excluded", count=len(excluded)), "stale",
                i18n.t("scan.band.excluded_tip", paths="\n".join(
                    formatting.relative_path(path, account.path) for path in excluded[:10]
                )),
            ))
        band.set_chips(chips)

    def _show_biggest(self, entry, account, notice: str) -> int:
        current = formatting.scan_label(entry.current_scan_at, entry.last_completed_generation)
        self.caption.setText(
            i18n.t(
                "scan.baseline_only_caption",
                account=account.name, current=current, notice=notice,
            )
        )
        self._show_band(entry, account, "", current)
        self.table.setRowCount(len(entry.top_paths))
        self.bars.scale = 0
        dash = i18n.t("common.none")
        account_kb = getattr(entry, "current_measured_kb", None)
        for index, row in enumerate(entry.top_paths):
            self.table.setItem(index, GROWTH_PATH, _path_item(row["path"], account.path))
            self.table.setItem(index, GROWTH_SIZE, _size_item(row["size_kb"], account_kb))
            self.table.setItem(index, GROWTH_DELTA, widgets.NumericItem(dash, None))
            self.table.setItem(index, GROWTH_TREND, trend_item(
                (getattr(entry, "folder_trends", None) or {}).get(row["path"])))
            self.table.setItem(index, GROWTH_FOCUS, QTableWidgetItem(""))
        return GROWTH_SIZE


# -- 4. 큰 파일 -------------------------------------------------------------------
class LargeFilesPage(QWidget):
    """가장 큰 파일 표.

    디렉터리 합계만 보면 300GB 짜리가 고른 파일 3천 개인지 한 파일이
    280GB 인지 구분되지 않는다. 후자는 사람이 바로 손댈 수 있는 것이라
    따로 보여 줄 값어치가 있다."""

    def __init__(self, parent=None, compact: bool = False):
        super().__init__(parent)
        self._compact = compact
        layout = _page_box(self)
        # 큰 파일들이 계정에서 얼마를 차지하나 - 큰 값과 쌓은 막대, 까닭별 알약.
        self.stack = visuals.ShareStack()
        self.band = visuals.SummaryBand(self.stack)
        layout.addWidget(self.band)
        self.caption = _caption()
        self.caption.setToolTip(i18n.t("large.tip"))
        layout.addWidget(self.caption)

        table = _table(len(LARGE_COLUMN_KEYS))
        header = table.horizontalHeader()
        header.setSectionResizeMode(LARGE_PATH, QHeaderView.Stretch)
        # 크기·비중 칸에는 막대가, 증감 칸에는 증감 막대나 알약이 들어간다 - 내용 폭에
        # 맞추면 막대가 글자 밑에 숨는다.
        widths = ((LARGE_SIZE, 140), (LARGE_SHARE, 120), (LARGE_CHANGE, 140)) if compact else             ((LARGE_SIZE, 170), (LARGE_SHARE, 150), (LARGE_CHANGE, 170))
        for column, width in widths:
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            table.setColumnWidth(column, width)
        self.cells = widgets.ChangeBarDelegate(table)
        table.setItemDelegate(self.cells)
        self.table = table
        layout.addWidget(table, 1)
        layout.addStretch(0)

    def retranslate(self) -> None:
        self.table.setHorizontalHeaderLabels(
            [i18n.t(key) for key in LARGE_COLUMN_KEYS]
        )

    def show_account(self, snapshot, account: Optional[object]) -> None:
        table = self.table
        table.setRowCount(0)
        fit_table(table)
        self.band.setVisible(False)
        entry = _entry_for(snapshot, account.account_id if account else None)
        if entry is None:
            self.caption.setText(i18n.t("large.no_scan"))
            return

        # 비중은 그 파일들을 잰 바퀴(끝난 것)의 합계로 나눈다. `measured_kb` 는
        # 하는 중인 바퀴의 '지금까지 잰 양'이라 그것으로 나누면 비중이 부푼다.
        files = large_files.build(entry.large_files, entry.current_measured_kb)
        self.show_files(files, entry.account_name, account.path if account else "",
                        entry.current_measured_kb)

    def show_files(self, files, account_name: str, root: str, total_kb, limit: int = 0) -> None:
        """이미 만든 큰 파일 줄(`large_files.build`)을 그린다. 계정 상세 창도 이것을 쓴다 -
        같은 표가 두 벌이면 한쪽만 고쳐지고(실제로 그랬다), 그림도 한쪽에만 붙는다."""

        table = self.table
        table.setRowCount(0)
        fit_table(table)
        self.band.setVisible(False)
        if not files:
            self.caption.setText(i18n.t("large.none", account=account_name))
            return

        shown = files[:limit or LARGE_ROWS_SHOWN]
        entry = SimpleNamespace(current_measured_kb=total_kb)
        notable = sum(1 for item in shown if item.is_notable)
        heading = i18n.t("large.heading", account=account_name, count=len(shown))
        if notable:
            heading += i18n.t("large.heading_notable", notable=notable)
        # 범례 문장("주황 칸이 눈에 띈 까닭입니다 - ...")은 요약 띠의 알약 툴팁으로 갔다.
        self.caption.setText(heading + ("" if self._compact else _root_note(root)))
        self._show_band(entry, shown, root)
        table.setRowCount(len(shown))
        dash = i18n.t("common.none")
        biggest = max((item.size_kb for item in shown), default=0) or 1
        share_scale = max(
            [large_files.SHARE_ALERT_PCT * 2] + [item.share_pct or 0 for item in shown]
        )
        self.cells.scale = max((abs(item.delta_kb or 0) for item in shown if item.compared), default=0)
        for row, item in enumerate(shown):
            path_item = QTableWidgetItem(formatting.relative_path(item.path, root))
            reasons = large_file_reasons(item)
            # 줄여 보여 준 경로는 전체를 툴팁에 둔다 - 사람이 그 경로로 가야 한다.
            tip = reasons + ([item.path] if path_item.text() != item.path else [])
            if tip:
                path_item.setToolTip("\n".join(tip))
            # 폴더는 흐리게, 파일 이름은 굵게 - 경로 전체가 같은 회색 글이면 어느
            # 파일인지 끝까지 읽어야 안다.
            path_item.setData(widgets.CellDelegate.PATH_ROLE, True)
            table.setItem(row, LARGE_PATH, path_item)

            size_item = widgets.NumericItem(formatting.format_kb(item.size_kb), item.size_kb)
            size_item.setData(widgets.CellDelegate.BAR_ROLE, (item.size_kb / biggest, "accent"))
            table.setItem(row, LARGE_SIZE, size_item)

            share = item.share_pct
            share_item = widgets.NumericItem(
                f"{share:.1f}%" if share is not None else dash, share or 0
            )
            if share is not None:
                # 기준(5%)의 두 배가 칸 끝이다 - 기준을 넘은 파일은 막대가 반을 넘는다.
                share_item.setData(widgets.CellDelegate.BAR_ROLE, (
                    share / share_scale, "attention" if item.dominates_account else "neutral",
                ))
            table.setItem(row, LARGE_SHARE, share_item)

            if item.previous_kb is None:
                change_item = QTableWidgetItem(formatting.format_large_file_change(item))
                if item.is_new_to_the_list:
                    change_item.setData(
                        widgets.CellDelegate.CHIP_ROLE, (change_item.text(), "change_up")
                    )
            else:
                change_item = widgets.NumericItem(
                    formatting.format_large_file_change(item), item.delta_kb
                )
                change_item.setData(widgets.CellDelegate.VALUE_ROLE, item.delta_kb)
            table.setItem(row, LARGE_CHANGE, change_item)
            for column in (LARGE_SIZE, LARGE_SHARE, LARGE_CHANGE):
                table.item(row, column).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            # 눈에 띄는 것만, 그 까닭이 된 칸에 색을 준다. 전부 칠하면 아무것도
            # 강조되지 않는다.
            mark_large_file_row(table, row, item, LARGE_PATH, LARGE_SHARE, LARGE_CHANGE)
        fit_table(table)

    def _show_band(self, entry, shown, root: str) -> None:
        """요약 띠 - 큰 파일들이 계정에서 차지하는 몫과 까닭별 알약."""

        band = self.band
        total = entry.current_measured_kb
        summed = sum(item.size_kb for item in shown)
        band.setVisible(True)
        band.caption.setText(i18n.t("large.band.title", count=len(shown)))
        if total:
            band.set_value(f"{summed / total * 100:.1f}%")
            band.sub.setText(i18n.t(
                "large.band.of", sum=formatting.format_kb(summed), total=formatting.format_kb(total),
            ))
        else:
            band.set_value(formatting.format_kb(summed))
            band.sub.setText("")
        self.stack.set_parts([
            (
                item.size_kb, "attention" if item.is_notable else "accent",
                i18n.t(
                    "large.share_tip", path=formatting.relative_path(item.path, root),
                    size=formatting.format_kb(item.size_kb),
                    pct=f"{item.share_pct:.1f}" if item.share_pct is not None else "-",
                ),
            )
            for item in shown
        ], total)
        legend = large_files_legend()
        counts = (
            ("large.chip.dominant", "tier_alert", sum(1 for item in shown if item.dominates_account)),
            ("large.chip.grew", "change_up", sum(1 for item in shown if item.grew_sharply)),
            ("large.chip.new", "change_up", sum(1 for item in shown if item.is_new_to_the_list)),
        )
        band.set_chips([
            (i18n.t(key, count=count), kind, legend) for key, kind, count in counts if count
        ])
