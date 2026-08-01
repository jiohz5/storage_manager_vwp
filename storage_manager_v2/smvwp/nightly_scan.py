"""야간 상세 스캔 오케스트레이터 - 계정을 돌아가며(공정성) 직렬로 처리하고,
시간창/안전 중지/잠금을 모두 여기서 조율한다.

CONCEPT.md 7절이 요구하는 안전장치들을 한 곳에 모았다:
- "한 계정이 느려도 다른 계정 보고가 막히지 않는다" -> 계정 하나가 밤을 다
  써도(=deadline 도달) 그 계정 체크포인트에 정확히 표시해 두고 조용히
  멈춘다. 예외를 밖으로 던지지 않는다.
- "여러 계정의 상세 작업은 동시에 두 개 이상 돌리지 않고 직렬 처리" ->
  `scan_lock`으로 전체 실행 자체를 하나만 허용하고, 계정도 한 번에 하나씩만
  처리한다 (병렬 처리 없음).
- "완료 순서를 날짜별로 순환시켜 특정 계정이 계속 뒤로 밀리지 않게 공정성
  확보" -> `_rotate_accounts`가 오늘 날짜를 시드로 시작 위치를 돌린다.
- 06:00에는 "완료된 체크포인트를 남기고 paused로 종료" -> `status`가
  completed/paused/stopped/error로 구분되어 강제 종료와 다르다는 게 남는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from . import activity_scan, config as config_module, detail_scan, scan_lock, scan_store, scan_window

logger = logging.getLogger(__name__)

STATUS_COMPLETED = "completed"
STATUS_PAUSED = "paused"
STATUS_STOPPED = "stopped"
STATUS_ERROR = "error"
STATUS_NOT_STARTED = "not_started"  # 시간창 밖이거나 이미 잠겨 있어 아예 시작 못 함


@dataclass
class AccountOutcome:
    account_id: str
    account_name: str
    baseline_status: str  # 'done' | 'interrupted'
    baseline_generation: int
    activity_status: str  # 'done' | 'interrupted'
    activity_pass: int


@dataclass
class RunSummary:
    started: bool
    status: str
    run_id: Optional[str] = None
    reason: Optional[str] = None
    accounts: List[AccountOutcome] = field(default_factory=list)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _rotate_accounts(accounts, today: datetime):
    """오늘 날짜를 시드로 시작 인덱스를 돌려, 특정 계정이 항상 먼저(또는
    항상 나중에) 처리되지 않게 한다."""

    if not accounts:
        return []
    start = today.toordinal() % len(accounts)
    return list(accounts[start:]) + list(accounts[:start])


def _default_top_level_lister(account_path: str) -> List[str]:
    return detail_scan.list_immediate_subdirs(account_path)


def _process_baseline(
    conn,
    account: config_module.Account,
    settings: config_module.Settings,
    clock: Callable[[], datetime],
    should_stop: Callable[[], bool],
    deadline_reached: Callable[[], bool],
    top_level_lister: Callable[[str], List[str]],
) -> "tuple[str, int]":
    state = scan_store.get_account_state(conn, account.account_id)
    generation = state.working_generation

    if not scan_store.is_seeded(conn, account.account_id, scan_store.BASELINE, generation):
        top_dirs = top_level_lister(account.path)
        scan_store.seed_checkpoints(conn, account.account_id, scan_store.BASELINE, generation, top_dirs)

    while True:
        if should_stop():
            return "interrupted", generation
        if deadline_reached():
            return "interrupted", generation
        checkpoint = scan_store.next_pending(conn, account.account_id, scan_store.BASELINE, generation)
        if checkpoint is None:
            break
        detail_scan.process_one_checkpoint(conn, checkpoint, settings.detail_task_timeout_seconds)

    rows = scan_store.leaf_results(conn, account.account_id, generation)
    if rows:
        scan_store.save_baseline_results(conn, account.account_id, generation, rows)
    scan_store.mark_generation_completed(conn, account.account_id, generation)
    scan_store.prune_old_generations(conn, account.account_id, settings.detail_scan_keep_generations)
    return "done", generation


def _process_activity(
    conn,
    account: config_module.Account,
    settings: config_module.Settings,
    clock: Callable[[], datetime],
    should_stop: Callable[[], bool],
    deadline_reached: Callable[[], bool],
    top_level_lister: Callable[[str], List[str]],
) -> "tuple[str, int]":
    state = scan_store.get_account_state(conn, account.account_id)
    pass_no = state.working_activity_pass
    since_iso = state.activity_cursor or _iso(
        clock() - timedelta(days=settings.activity_initial_lookback_days)
    )

    if not scan_store.is_seeded(conn, account.account_id, scan_store.ACTIVITY, pass_no):
        top_dirs = top_level_lister(account.path)
        scan_store.seed_checkpoints(conn, account.account_id, scan_store.ACTIVITY, pass_no, top_dirs)

    while True:
        if should_stop():
            return "interrupted", pass_no
        if deadline_reached():
            return "interrupted", pass_no
        checkpoint = scan_store.next_pending(conn, account.account_id, scan_store.ACTIVITY, pass_no)
        if checkpoint is None:
            break
        activity_scan.process_one_checkpoint(conn, checkpoint, since_iso, settings.detail_task_timeout_seconds)

    total = activity_scan.total_changed(conn, account.account_id, pass_no)
    new_cursor = _iso(clock())
    scan_store.mark_activity_pass_completed(conn, account.account_id, pass_no, new_cursor, total)
    scan_store.prune_completed_activity_checkpoints(conn, account.account_id, pass_no)
    return "done", pass_no


def _process_account(
    conn,
    account: config_module.Account,
    settings: config_module.Settings,
    clock: Callable[[], datetime],
    should_stop: Callable[[], bool],
    deadline_reached: Callable[[], bool],
    top_level_lister: Callable[[str], List[str]],
) -> AccountOutcome:
    baseline_status, generation = _process_baseline(
        conn, account, settings, clock, should_stop, deadline_reached, top_level_lister
    )
    if baseline_status == "interrupted":
        return AccountOutcome(
            account_id=account.account_id,
            account_name=account.name,
            baseline_status=baseline_status,
            baseline_generation=generation,
            activity_status="interrupted",
            activity_pass=scan_store.get_account_state(conn, account.account_id).working_activity_pass,
        )

    activity_status, pass_no = _process_activity(
        conn, account, settings, clock, should_stop, deadline_reached, top_level_lister
    )
    return AccountOutcome(
        account_id=account.account_id,
        account_name=account.name,
        baseline_status=baseline_status,
        baseline_generation=generation,
        activity_status=activity_status,
        activity_pass=pass_no,
    )


def run_nightly_scan(
    data_dir: Path,
    config: config_module.AppConfig,
    triggered_by: str = "cron",
    bypass_window: bool = False,
    clock: Optional[Callable[[], datetime]] = None,
    top_level_lister: Optional[Callable[[str], List[str]]] = None,
) -> RunSummary:
    """야간 상세 스캔 한 번(하룻밤 분량)을 실행한다.

    `bypass_window=True`는 "터미널에서 직접 실행"(CONCEPT.md 3절의 의도적
    진단/복구 경로)에 해당 - 시간창 제한 없이 완료되거나 명시적 중지 요청이
    올 때까지 계속 처리한다.

    `clock`/`top_level_lister`를 주입할 수 있게 해서, 테스트가 실제 벽시계나
    실제 파일시스템 없이도 전체 흐름(시간창 초과로 중단 -> 다음 호출에서
    재개 등)을 검증할 수 있게 했다.
    """

    clock = clock or datetime.now
    top_level_lister = top_level_lister or _default_top_level_lister
    settings = config.settings

    local_now = clock()
    if not bypass_window and not scan_window.is_within_window(
        local_now, settings.detail_scan_window_start_hour, settings.detail_scan_window_end_hour
    ):
        return RunSummary(started=False, status=STATUS_NOT_STARTED, reason="야간 시간창이 아닙니다")

    try:
        run_id = scan_lock.acquire_lock(data_dir, triggered_by)
    except scan_lock.LockBusyError as exc:
        return RunSummary(started=False, status=STATUS_NOT_STARTED, reason=str(exc))

    scan_lock.clear_stop_request(data_dir)
    conn = scan_store.connect(data_dir)
    outcomes: List[AccountOutcome] = []
    status = STATUS_COMPLETED
    try:
        scan_store.start_run(conn, run_id, triggered_by)

        if bypass_window:
            deadline_reached = lambda: False
        else:
            window_end = scan_window.next_window_end(local_now, settings.detail_scan_window_end_hour)
            deadline_reached = lambda: clock() >= window_end

        should_stop = lambda: scan_lock.is_stop_requested(data_dir, run_id)

        accounts = _rotate_accounts(config_module.enabled_accounts(config), local_now)
        for account in accounts:
            if should_stop():
                status = STATUS_STOPPED
                break
            if deadline_reached():
                status = STATUS_PAUSED
                break
            outcome = _process_account(
                conn, account, settings, clock, should_stop, deadline_reached, top_level_lister
            )
            outcomes.append(outcome)
            if outcome.baseline_status == "interrupted" or outcome.activity_status == "interrupted":
                status = STATUS_STOPPED if should_stop() else STATUS_PAUSED
                break
    except Exception:  # pragma: no cover - 방어적 처리, 다음 실행에서 체크포인트로 재개
        logger.exception("야간 상세 스캔 중 예외 발생")
        status = STATUS_ERROR
    finally:
        scan_store.finish_run(conn, run_id, status)
        conn.close()
        scan_lock.release_lock(data_dir, run_id)

    return RunSummary(started=True, status=status, run_id=run_id, accounts=outcomes)


@dataclass
class AccountScanSnapshot:
    account_id: str
    account_name: str
    last_completed_generation: Optional[int]
    top_paths: list  # List[sqlite3.Row] (path, size_kb)
    growth: list  # List[sqlite3.Row] (path, current_kb, previous_kb) - 이전 세대가 없으면 빈 리스트
    last_activity_total_changed: Optional[int]
    last_activity_completed_at: Optional[str]
    pending_baseline_count: int
    pending_activity_count: int


@dataclass
class StatusSnapshot:
    is_running: bool
    window_description: str
    latest_run: Optional[dict]
    accounts: List[AccountScanSnapshot]


def get_status_snapshot(
    data_dir: Path,
    config: config_module.AppConfig,
    clock: Optional[Callable[[], datetime]] = None,
    top_n: Optional[int] = None,
) -> StatusSnapshot:
    """GUI가 스캔을 직접 돌리지 않고도 현재 상태를 읽기만 해서 보여줄 때
    쓴다. 여기서는 아무 것도 쓰지 않는다 (읽기 전용 조회)."""

    clock = clock or datetime.now
    top_n = top_n or config.settings.detail_scan_top_n
    now = clock()

    conn = scan_store.connect(data_dir)
    try:
        latest_run_row = scan_store.latest_run(conn)
        latest_run = dict(latest_run_row) if latest_run_row is not None else None

        accounts_snapshot = []
        for account in config.accounts:
            state = scan_store.get_account_state(conn, account.account_id)
            current_gen = state.last_completed_generation
            previous_gen = current_gen - 1 if current_gen else None
            top = scan_store.top_paths(conn, account.account_id, current_gen, top_n) if current_gen else []
            growth = (
                scan_store.growth_delta(conn, account.account_id, current_gen, previous_gen, top_n)
                if current_gen and previous_gen
                else []
            )
            pending_baseline_count = conn.execute(
                "SELECT COUNT(*) FROM scan_checkpoints WHERE account_id = ? AND kind = 'baseline' "
                "AND generation = ? AND status = 'pending'",
                (account.account_id, state.working_generation),
            ).fetchone()[0]
            pending_activity_count = conn.execute(
                "SELECT COUNT(*) FROM scan_checkpoints WHERE account_id = ? AND kind = 'activity' "
                "AND generation = ? AND status = 'pending'",
                (account.account_id, state.working_activity_pass),
            ).fetchone()[0]
            accounts_snapshot.append(
                AccountScanSnapshot(
                    account_id=account.account_id,
                    account_name=account.name,
                    last_completed_generation=current_gen,
                    top_paths=top,
                    growth=growth,
                    last_activity_total_changed=state.last_activity_total_changed,
                    last_activity_completed_at=state.last_activity_completed_at,
                    pending_baseline_count=pending_baseline_count,
                    pending_activity_count=pending_activity_count,
                )
            )
    finally:
        conn.close()

    return StatusSnapshot(
        is_running=scan_lock.is_locked(data_dir),
        window_description=scan_window.describe(
            now, config.settings.detail_scan_window_start_hour, config.settings.detail_scan_window_end_hour
        ),
        latest_run=latest_run,
        accounts=accounts_snapshot,
    )


def request_stop(data_dir: Path) -> bool:
    """현재 실행 중인 야간 스캔에 안전 중지를 요청한다. 실행 중인 게 없으면
    False."""

    info = scan_lock.read_lock(data_dir)
    if info is None:
        return False
    scan_lock.request_stop(data_dir, info.run_id)
    return True
