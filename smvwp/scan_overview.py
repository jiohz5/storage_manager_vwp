"""상세 스캔이 어디까지 왔고 언제 끝나나 - 홈과 스캔 탭 '현황' 이 쓰는 숫자 (Qt 없음).

실기에서 나온 말: "날마다 스캔을 하는데 뭘 했는지 어디까지 했는지를 모르겠다,
어디까지 스캔되고 스토리지가 어떤 상황인지 판단이 힘들다." 화면에는 계정마다
진행 숫자만 있었고 전체 그림과 끝나는 날이 없었다.

## '모든 계정 한 바퀴'

상세 스캔은 계정마다 따로 돌고, 하룻밤에 다 못 돌면 다음 밤에 이어 간다. 그래서
"모든 계정을 한 번씩 다시 재는 일" 을 한 바퀴로 센다. 바퀴가 시작된 뒤에 끝낸
계정은 '끝남', 지금 재다 만 계정은 '하는 중', 아직이면 '대기', 지난번에 경로를
못 본 계정은 '못 봄' 이다. '못 봄' 은 바퀴가 끝나는 것을 막지 않는다 - 경로가
돌아오기 전에는 잴 수 없다.

바퀴의 시작은 야간 스캔이 스캔 DB 에 남긴다 (`nightly_scan.advance_pass`): 볼 수
있는 계정이 모두 끝나면 그때가 다음 바퀴의 시작이다. 아직 남긴 적이 없으면(이
판으로 업데이트한 직후) 가장 오래전에 끝낸 계정의 완료 시각으로 어림한다 - 그
계정은 '대기', 그 뒤에 끝낸 계정은 '끝남' 이 된다.

## 완료 예상

남은 계정을 야간 스캔이 도는 순서(`nightly_scan._fair_order` 와 같은 규칙: 하다 만
것, 한 번도 못 끝낸 것, 오래전에 끝낸 것)대로 밤 시간창에 채운다. 계정 하나의 남은
일은 남은 디렉터리 x 디렉터리 하나에 걸린 시간이다 - 그 계정이 지난번에 낸
중앙값, 없으면 다른 계정들의 중앙값. 지난 밤들의 관측으로 낸 어림이라 디렉터리
크기가 고르지 않으면 어긋난다. 화면도 그렇게 말한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Callable, Dict, List, Optional, Sequence

from . import formatting, i18n, scan_window

DONE = "done"          # 이번 바퀴에 끝냈다
DOING = "doing"        # 재다 말았다 (다음 밤에 이어서)
WAITING = "waiting"    # 이번 바퀴에 아직
UNSEEN = "unseen"      # 지난번에 경로를 못 봤다

# 근거가 하나도 없을 때의 어림. 디렉터리 하나 2분, 계정 하나에 디렉터리 40개.
FALLBACK_SECONDS_PER_DIR = 120.0
FALLBACK_DIRS = 40

# 일정은 이 밤 수까지만 채운다. 그보다 길면 "몇 밤 넘게" 로만 말한다.
MAX_NIGHTS = 14


@dataclass
class AccountCoverage:
    account_id: str
    name: str
    state: str
    done_dirs: int = 0                 # 이번 바퀴에 잰 디렉터리
    total_dirs: int = 0                # 이번 바퀴에 잴 디렉터리 (대기면 지난번 수로 어림)
    estimated_total: bool = False      # total_dirs 가 어림인가
    last_completed_at: Optional[datetime] = None   # 마지막으로 한 바퀴를 끝낸 때 (현지)
    period_start: Optional[datetime] = None        # 보여 주는 결과를 처음 잰 때
    period_end: Optional[datetime] = None          # ... 마지막으로 잰 때
    working_started_at: Optional[datetime] = None  # 하는 중이면 그 세대를 처음 잰 때
    measured_kb: Optional[int] = None  # 끝난 세대를 다 더한 양
    current_kb: Optional[int] = None   # 지금 쓰는 양 - 그 계정만의 값을 알 때만 (아래)
    work_seconds: float = 0.0          # 이번 바퀴에 남은 일 어림
    order: Optional[int] = None        # 남은 계정 가운데 몇 번째로 도나 (1부터)
    unavailable_reason: Optional[str] = None

    @property
    def fraction(self) -> float:
        if self.state == DONE:
            return 1.0
        return (self.done_dirs / self.total_dirs) if self.total_dirs else 0.0

    @property
    def changed_kb(self) -> Optional[int]:
        """잰 뒤로 바뀐 양 (지금 - 잰 합계). 둘 중 하나라도 모르면 None."""

        if self.current_kb is None or self.measured_kb is None:
            return None
        return self.current_kb - self.measured_kb

    def age_days(self, now: datetime) -> Optional[int]:
        if self.last_completed_at is None:
            return None
        return max(0, (now.date() - self.last_completed_at.date()).days)


@dataclass
class Slot:
    """한 밤에 계정 하나를 재는 칸."""

    account_id: str
    name: str
    start: datetime
    end: datetime
    resumes: bool = False         # 앞 밤에서 이어 온 것
    carries_over: bool = False    # 다음 밤으로 넘어가는 것


@dataclass
class Night:
    opens: datetime
    closes: datetime
    slots: List[Slot] = field(default_factory=list)

    @property
    def finished(self) -> int:
        return sum(1 for slot in self.slots if not slot.carries_over)


@dataclass
class RunNight:
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    status: str
    triggered_by: str
    dirs_done: int
    accounts_done: int


@dataclass
class Overview:
    now: datetime
    running: bool
    pass_started_at: Optional[datetime]
    pass_estimated: bool                # 바퀴 시작이 남긴 값이 아니라 어림인가
    accounts: List[AccountCoverage]
    nights: List[Night]
    finish_at: Optional[datetime]       # 남은 계정을 다 끝낼 어림. 다 끝났으면 None
    beyond: bool                        # MAX_NIGHTS 안에 다 못 채웠다
    runs: List[RunNight]

    def count(self, state: str) -> int:
        return sum(1 for item in self.accounts if item.state == state)

    @property
    def done_dirs(self) -> int:
        return sum(item.done_dirs for item in self.accounts if item.state != UNSEEN)

    @property
    def total_dirs(self) -> int:
        return sum(item.total_dirs for item in self.accounts if item.state != UNSEEN)

    @property
    def percent(self) -> int:
        """디렉터리 기준 진행률. 잴 것이 없으면 100."""

        total = self.total_dirs
        return int(round(100 * self.done_dirs / total)) if total else 100

    @property
    def remaining(self) -> List[AccountCoverage]:
        return sorted(
            (item for item in self.accounts if item.order is not None),
            key=lambda item: item.order,
        )

    @property
    def stalest(self) -> Optional[AccountCoverage]:
        """가장 오래 못 본 계정 - 한 번도 못 끝낸 계정이 있으면 그것."""

        candidates = [item for item in self.accounts if item.state != UNSEEN]
        if not candidates:
            return None
        never = [item for item in candidates if item.last_completed_at is None]
        if never:
            return never[0]
        return min(candidates, key=lambda item: item.last_completed_at)


def build(
    snapshot,
    config,
    latest_samples: Optional[Dict[str, object]] = None,
    now: Optional[datetime] = None,
    lanes_for: Optional[Callable[[Sequence[object]], int]] = None,
) -> Overview:
    """스캔 상태(`nightly_scan.get_status_snapshot`)와 최신 표본으로 현황을 낸다.

    `now` 는 현지 시각(naive) - 시간창이 현지 시각으로 돈다. `lanes_for(남은 계정들)`
    은 한 밤에 동시에 도는 갈래 수 (기본: 설정의 동시 볼륨 상한과 실제 볼륨 수 중
    작은 것, `nightly_scan.group_accounts_by_volume`)."""

    now = now or datetime.now()
    latest_samples = latest_samples or {}
    settings = config.settings
    by_id = {account.account_id: account for account in config.accounts}
    entries = [entry for entry in snapshot.accounts if entry.account_id in by_id]

    stored = getattr(snapshot, "pass_started_at", None)
    stored_start = scan_window.local_time_of(stored) if stored else None
    completions = [
        scan_window.local_time_of(entry.last_completed_at)
        for entry in entries
        if entry.last_completed_at and not entry.unavailable_reason
    ]
    completions = [stamp for stamp in completions if stamp is not None]
    pass_start = stored_start or (min(completions) if completions else None)

    accounts = [
        _coverage(entry, by_id[entry.account_id], pass_start, latest_samples.get(entry.account_id))
        for entry in entries
    ]
    _fill_work(accounts, entries)

    remaining = _fair_order([item for item in accounts if item.state in (DOING, WAITING)])
    for position, item in enumerate(remaining, start=1):
        item.order = position

    if lanes_for is None:
        lanes_for = lambda items: _default_lanes(items, by_id, settings)   # noqa: E731
    lanes = max(1, int(lanes_for([by_id[item.account_id] for item in remaining]) or 1))
    # 시간창 안인데 스캔이 돌고 있지 않으면 이 밤의 실행은 이미 끝났다(다 돌았거나
    # 누가 멈췄다) - 남은 일은 다음 밤부터다.
    nights, finish_at, beyond = _schedule(
        remaining, now, settings.detail_scan_window_start_hour,
        settings.detail_scan_window_end_hour, lanes, skip_current=not snapshot.is_running,
    )

    return Overview(
        now=now,
        running=bool(snapshot.is_running),
        pass_started_at=pass_start,
        pass_estimated=stored_start is None,
        accounts=accounts,
        nights=nights,
        finish_at=finish_at,
        beyond=beyond,
        runs=_runs(getattr(snapshot, "recent_runs", None), entries),
    )


# -- 계정마다 ---------------------------------------------------------------

def _coverage(entry, account, pass_start, sample) -> AccountCoverage:
    last = scan_window.local_time_of(entry.last_completed_at) if entry.last_completed_at else None
    in_progress = bool(entry.baseline_total) and entry.baseline_done < entry.baseline_total
    if entry.unavailable_reason:
        state = UNSEEN
    elif in_progress:
        state = DOING
    elif last is not None and (pass_start is None or last > pass_start):
        state = DONE
    else:
        state = WAITING

    item = AccountCoverage(
        account_id=entry.account_id,
        name=entry.account_name,
        state=state,
        last_completed_at=last,
        period_start=scan_window.local_time_of(entry.current_started_at) if entry.current_started_at else None,
        period_end=scan_window.local_time_of(entry.current_finished_at) if entry.current_finished_at else None,
        working_started_at=(
            scan_window.local_time_of(entry.working_started_at) if entry.working_started_at else None
        ),
        measured_kb=entry.current_measured_kb,
        current_kb=_current_kb(sample, account),
        unavailable_reason=entry.unavailable_reason,
    )
    if state == DONE:
        item.done_dirs = item.total_dirs = entry.current_dirs
    elif state == DOING:
        item.done_dirs, item.total_dirs = entry.baseline_done, entry.baseline_total
    else:
        item.total_dirs = entry.current_dirs
        item.estimated_total = True
    return item


def _current_kb(sample, account) -> Optional[int]:
    """그 계정만의 '지금 쓰는 양'. 모르면 None.

    df 의 사용량은 **파일시스템 전체**의 값이라(README 의 df 안내), 계정 경로가 그
    파일시스템의 마운트 지점일 때만 계정의 값이다. quota 를 쓰면 그것이 계정의 값이다.
    잰 합계와 견줄 수 없는 값을 견주면 "그 뒤 10TB 늘었다" 같은 거짓이 나온다."""

    if sample is None or not getattr(sample, "ok", False):
        return None
    if getattr(sample, "quota_used_kb", None) is not None:
        return sample.quota_used_kb
    mount = (getattr(sample, "mount_point", None) or "").rstrip("/")
    if mount and mount == (account.path or "").rstrip("/"):
        return sample.used_kb
    return None


def _fill_work(accounts: List[AccountCoverage], entries) -> None:
    """남은 일(초) 어림. 근거가 없는 계정은 다른 계정들의 중앙값을 빌린다."""

    by_id = {entry.account_id: entry for entry in entries}
    speeds = [entry.seconds_per_dir for entry in entries if entry.seconds_per_dir]
    dirs = [entry.current_dirs for entry in entries if entry.current_dirs]
    speed_fallback = median(speeds) if speeds else FALLBACK_SECONDS_PER_DIR
    dirs_fallback = int(median(dirs)) if dirs else FALLBACK_DIRS
    for item in accounts:
        entry = by_id[item.account_id]
        speed = entry.seconds_per_dir or speed_fallback
        if item.state == DOING:
            left = max(0, item.total_dirs - item.done_dirs)
            item.work_seconds = entry.eta_seconds if entry.eta_seconds else left * speed
        elif item.state == WAITING:
            if not item.total_dirs:
                item.total_dirs = dirs_fallback
            item.work_seconds = item.total_dirs * speed


def _fair_order(items: List[AccountCoverage]) -> List[AccountCoverage]:
    """`nightly_scan._fair_order` 와 같은 규칙 (설정 순서가 같은 조건의 순서)."""

    def rank(pair):
        index, item = pair
        if item.state == DOING:
            return (0, datetime.min, index)
        if item.last_completed_at is None:
            return (1, datetime.min, index)
        return (2, item.last_completed_at, index)

    return [item for _index, item in sorted(enumerate(items), key=rank)]


def _default_lanes(items, by_id, settings) -> int:
    from . import nightly_scan

    if not items:
        return 1
    limit = max(1, settings.nightly_parallel_accounts)
    if limit == 1:
        return 1
    return min(limit, len(nightly_scan.group_accounts_by_volume(list(items))))


# -- 밤 일정 ----------------------------------------------------------------

def windows(now: datetime, start_hour: int, end_hour: int):
    """`now` 부터 차례로 올 시간창 `(열림, 닫힘)`. 창 안이면 첫 창은 `now` 부터."""

    length = timedelta(hours=(end_hour - start_hour) % 24 or 24)
    day = (now - timedelta(days=1)).date()
    while True:
        opens = datetime.combine(day, datetime.min.time()).replace(hour=start_hour)
        closes = opens + length
        day += timedelta(days=1)
        if closes <= now:
            continue
        yield max(opens, now), closes


def _schedule(remaining, now, start_hour, end_hour, lanes, skip_current=False):
    """남은 계정을 순서대로 밤 시간창에 채운다. `(밤들, 다 끝나는 때, 넘쳤나)`.

    `skip_current` 면 지금 열려 있는 시간창은 건너뛴다 (그 밤의 실행이 이미 끝났다)."""

    if not remaining:
        return [], None, False
    upcoming = windows(now, start_hour, end_hour)
    if skip_current:
        upcoming = (window for window in upcoming if window[0] > now)
    nights: List[Night] = []

    def next_night() -> bool:
        if len(nights) >= MAX_NIGHTS:
            return False
        opens, closes = next(upcoming)
        nights.append(Night(opens, closes))
        free[:] = [opens] * lanes
        return True

    free: List[datetime] = []   # 갈래마다 이 밤 안에서 다음에 비는 때
    next_night()
    finish = None
    for item in remaining:
        work = timedelta(seconds=max(item.work_seconds, 60.0))
        resumed = False
        while True:
            lane = min(range(lanes), key=lambda index: free[index])
            night = nights[-1]
            if free[lane] >= night.closes:
                if not next_night():
                    return nights, None, True
                continue
            start = free[lane]
            room = night.closes - start
            if work <= room:
                end = start + work
                night.slots.append(Slot(item.account_id, item.name, start, end, resumes=resumed))
                free[lane] = end
                finish = end if finish is None or end > finish else finish
                break
            night.slots.append(Slot(
                item.account_id, item.name, start, night.closes,
                resumes=resumed, carries_over=True,
            ))
            work -= room
            resumed = True
            # 나머지는 다음 밤에 잇는다 - 같은 계정을 같은 밤의 다른 갈래로 옮기지
            # 않는다 (야간 스캔도 다음 밤에 하다 만 계정부터 잇는다). 그래서 다른
            # 갈래에 남은 이 밤의 틈은 뒤 계정이 쓰지 못하는 것으로 어림한다.
            if not next_night():
                return nights, None, True
    return nights, finish, False


# -- 지난 밤들 ---------------------------------------------------------------

def _runs(recent_runs, entries) -> List[RunNight]:
    """실행마다 한 일. 끝낸 계정 수는 계정의 마지막·그 앞 완료 시각으로 센다 (그보다
    오래된 완료는 남지 않는다 - 최근 며칠에 쓰는 값이다)."""

    finished = []
    for entry in entries:
        for stamp in (entry.last_completed_at, getattr(entry, "previous_scan_at", None)):
            moment = scan_window.local_time_of(stamp) if stamp else None
            if moment is not None:
                finished.append(moment)

    runs = []
    for run in recent_runs or []:
        started = scan_window.local_time_of(run.get("started_at"))
        ended = scan_window.local_time_of(run.get("ended_at")) if run.get("ended_at") else None
        until = ended or datetime.now()
        accounts_done = sum(
            1 for moment in finished
            if started is not None and started <= moment <= until
        )
        runs.append(RunNight(
            started_at=started, ended_at=ended, status=run.get("status") or "",
            triggered_by=run.get("triggered_by") or "", dirs_done=int(run.get("dirs_done") or 0),
            accounts_done=accounts_done,
        ))
    return runs


# -- 화면에 쓸 글 (Qt 없음 - 시험할 수 있게) -------------------------------------

# 마지막 완료가 이만큼 지나면 '오래됨' 으로 칠한다.
STALE_DAYS = 7


def day_text(moment: datetime) -> str:
    """`10/28(화)`"""

    weekdays = i18n.t("overview.weekdays").split(",")
    return i18n.t("overview.day", month=moment.month, day=moment.day,
                  weekday=weekdays[moment.weekday()])


def when_text(moment: datetime) -> str:
    """`10/28(화) 새벽` - 어림이라 분까지 말하지 않는다."""

    hour = moment.hour
    key = ("overview.part.dawn" if hour < 6 else "overview.part.morning" if hour < 12
           else "overview.part.afternoon" if hour < 20 else "overview.part.evening")
    return f"{day_text(moment)} {i18n.t(key)}"


def clock(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def night_name(night: Night, now: datetime) -> str:
    """`지금` / `오늘 밤` / `내일 밤` / `모레 밤` / `10/30(목) 밤`."""

    if night.opens <= now:
        return i18n.t("overview.night.now")
    days = (night.opens.date() - now.date()).days
    key = {0: "overview.night.tonight", 1: "overview.night.tomorrow",
           2: "overview.night.day_after"}.get(days)
    if key:
        return i18n.t(key)
    return i18n.t("overview.night.other", day=day_text(night.opens))


def night_label(night: Night, now: datetime) -> str:
    """밤 칸의 제목 - `오늘 밤 · 10/26(일)`, 하는 중이면 `지금 · 06:00 까지`."""

    if night.opens <= now:
        return i18n.t("overview.night.now_label", end=clock(night.closes))
    return f"{night_name(night, now)} · {day_text(night.opens)}"


def night_summary(night: Night) -> str:
    carry = sum(1 for slot in night.slots if slot.carries_over)
    if carry:
        return i18n.t("overview.night.summary_carry", finished=night.finished, carry=carry)
    return i18n.t("overview.night.summary", finished=night.finished)


def slot_text(slot: Slot) -> str:
    if slot.carries_over:
        return i18n.t("overview.slot.carry", start=clock(slot.start))
    return i18n.t("overview.slot.span", start=clock(slot.start), end=clock(slot.end))


def slot_name(slot: Slot) -> str:
    return i18n.t("overview.slot.resumed", name=slot.name) if slot.resumes else slot.name


def age_text(item: AccountCoverage, now: datetime) -> "tuple[str, str]":
    """마지막 완료가 언제였나 `(글, 알약 종류)`."""

    days = item.age_days(now)
    if days is None:
        return i18n.t("overview.age.never"), "stale"
    if days == 0:
        return i18n.t("overview.age.today"), "done"
    if days == 1:
        return i18n.t("overview.age.yesterday"), "done"
    if days == 2:
        return i18n.t("overview.age.two_days"), "soft"
    return i18n.t("overview.age.days", days=days), ("stale" if days >= STALE_DAYS else "soft")


def _span_text(start: Optional[datetime], end: Optional[datetime]) -> str:
    if start is None:
        return i18n.t("common.none")
    head = start.strftime("%m/%d %H:%M")
    if end is None:
        return head
    tail = end.strftime("%H:%M") if end.date() == start.date() else end.strftime("%m/%d %H:%M")
    return f"{head} ~ {tail}"


def period_text(item: AccountCoverage) -> str:
    """보여 주는 결과를 잰 기간. 하는 중이면 이번 세대를 처음 잰 때부터."""

    if item.state == DOING and item.working_started_at is not None:
        return i18n.t("overview.period.doing", start=item.working_started_at.strftime("%m/%d %H:%M"))
    return _span_text(item.period_start, item.period_end)


def progress_text(item: AccountCoverage) -> str:
    if item.state == UNSEEN:
        return i18n.t("overview.progress.unseen")
    if item.state == WAITING and item.estimated_total:
        return i18n.t("overview.progress.estimated", total=item.total_dirs)
    return f"{item.done_dirs} / {item.total_dirs}"


def size_text(item: AccountCoverage) -> str:
    """잰 합계, 그 계정만의 지금 쓰는 양을 알면 함께 (그 뒤 바뀐 양)."""

    if item.measured_kb is None:
        return i18n.t("common.none")
    measured = formatting.format_kb(item.measured_kb)
    if item.current_kb is None:
        return measured
    return i18n.t("overview.size.with_now", measured=measured,
                  current=formatting.format_kb(item.current_kb),
                  delta=formatting.format_kb_delta(item.changed_kb))


def next_text(item: AccountCoverage, overview: "Overview") -> str:
    if item.state == DONE:
        return i18n.t("overview.next.done")
    if item.state == UNSEEN:
        return i18n.t("overview.next.unseen")
    for night in overview.nights:
        if any(slot.account_id == item.account_id for slot in night.slots):
            return i18n.t("overview.next.order", night=night_name(night, overview.now), order=item.order)
    return i18n.t("overview.next.later")


def pass_counts_text(overview: "Overview") -> str:
    visible = [item for item in overview.accounts if item.state != UNSEEN]
    return i18n.t(
        "overview.pass.counts", done=f"{overview.done_dirs:,}", total=f"{overview.total_dirs:,}",
        finished=overview.count(DONE), accounts=len(visible),
    )


def started_text(overview: "Overview") -> str:
    if overview.pass_started_at is None:
        return i18n.t("common.none")
    text = when_text(overview.pass_started_at)
    return i18n.t("overview.pass.estimated", when=text) if overview.pass_estimated else text


def finish_text(overview: "Overview") -> str:
    if not overview.remaining:
        return i18n.t("overview.finish.done")
    if overview.beyond or overview.finish_at is None:
        return i18n.t("overview.finish.beyond", nights=MAX_NIGHTS)
    return i18n.t("overview.finish.value", when=when_text(overview.finish_at), nights=len(overview.nights))


def stalest_text(overview: "Overview") -> "tuple[str, str]":
    """가장 오래 못 본 계정 `(글, 알약 종류)`."""

    item = overview.stalest
    if item is None:
        return i18n.t("common.none"), "soft"
    age, kind = age_text(item, overview.now)
    return i18n.t("overview.stalest.value", name=item.name, age=age), kind


RUN_STATUS_KEYS = {
    "completed": ("overview.run.completed", "done"),
    "paused": ("overview.run.paused", "soft"),
    "stopped": ("overview.run.stopped", "stale"),
    "error": ("overview.run.error", "stale"),
    "running": ("overview.run.running", "doing"),
}


def run_why(run: RunNight) -> "tuple[str, str]":
    key, kind = RUN_STATUS_KEYS.get(run.status, (None, "soft"))
    return (i18n.t(key) if key else run.status), kind


def run_done_text(run: RunNight) -> str:
    return i18n.t("overview.run.done", accounts=run.accounts_done, dirs=f"{run.dirs_done:,}")


def run_night_axis(run: RunNight, start_hour: int, end_hour: int) -> "tuple[datetime, datetime]":
    """그 실행이 돈 밤의 시간창 - 지난 밤들을 같은 축(22~06)에 놓는다."""

    started = run.started_at or datetime.now()
    for opens, closes in windows(started - timedelta(days=1), start_hour, end_hour):
        if closes > started:
            return opens, closes
    return started, started + timedelta(hours=8)  # pragma: no cover - 위에서 늘 돌아온다

