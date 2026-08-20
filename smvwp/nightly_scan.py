"""야간 상세 스캔 오케스트레이터 - 계정을 돌아가며(공정성) 직렬로 처리하고,
시간창/안전 중지/잠금을 모두 여기서 조율한다.

DESIGN.md 1부 7절이 요구하는 안전장치들을 한 곳에 모았다:
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

from . import (
    activity_scan,
    config as config_module,
    detail_scan,
    notifications,
    reports,
    loadstat,
    scan_lock,
    scan_store,
    scan_window,
    search_index,
)

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
    # 검색 인덱싱을 켠 계정만 해당. 'skipped' | 'done' | 'interrupted' | 'error'
    search_status: str = "skipped"
    search_entries: int = 0


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
    load=None,
    run_id: Optional[str] = None,
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
        if run_id:
            scan_store.set_current_target(
                conn, run_id, account.account_id, scan_store.BASELINE, checkpoint["path"]
            )
        detail_scan.process_one_checkpoint(conn, checkpoint, settings.detail_task_timeout_seconds)
        if load is not None:
            load.sample()

    rows = scan_store.leaf_results(conn, account.account_id, generation)
    if rows:
        scan_store.save_baseline_results(conn, account.account_id, generation, rows)
    scan_store.mark_generation_completed(conn, account.account_id, generation)
    # 기준선을 정리하기 **전에** 증감 숫자를 이력으로 남긴다 - 정리하고 나면
    # 비교 대상이 사라져 이력을 만들 수 없다.
    scan_store.record_growth_history(conn, account.account_id, generation, generation - 1)
    scan_store.prune_growth_history(
        conn, account.account_id, settings.growth_history_keep_generations
    )
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
    load=None,
    run_id: Optional[str] = None,
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
        if run_id:
            scan_store.set_current_target(
                conn, run_id, account.account_id, scan_store.ACTIVITY, checkpoint["path"]
            )
        activity_scan.process_one_checkpoint(conn, checkpoint, since_iso, settings.detail_task_timeout_seconds)
        if load is not None:
            load.sample()

    total = activity_scan.total_changed(conn, account.account_id, pass_no)
    new_cursor = _iso(clock())
    scan_store.mark_activity_pass_completed(conn, account.account_id, pass_no, new_cursor, total)
    scan_store.prune_completed_activity_checkpoints(conn, account.account_id, pass_no)
    return "done", pass_no


def _process_account(
    conn,
    data_dir: Path,
    account: config_module.Account,
    settings: config_module.Settings,
    clock: Callable[[], datetime],
    should_stop: Callable[[], bool],
    deadline_reached: Callable[[], bool],
    top_level_lister: Callable[[str], List[str]],
    load=None,
    run_id: Optional[str] = None,
) -> AccountOutcome:
    baseline_status, generation = _process_baseline(
        conn, account, settings, clock, should_stop, deadline_reached, top_level_lister, load, run_id
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
        conn, account, settings, clock, should_stop, deadline_reached, top_level_lister, load, run_id
    )
    search_status, search_entries = _process_search_index(
        data_dir, account, should_stop, deadline_reached
    )
    return AccountOutcome(
        account_id=account.account_id,
        account_name=account.name,
        baseline_status=baseline_status,
        baseline_generation=generation,
        activity_status=activity_status,
        activity_pass=pass_no,
        search_status=search_status,
        search_entries=search_entries,
    )


def _process_search_index(
    data_dir: Path,
    account: config_module.Account,
    should_stop: Callable[[], bool],
    deadline_reached: Callable[[], bool],
) -> "tuple[str, int]":
    """검색 인덱싱을 켠 계정만 이름 인덱스를 갱신한다.

    시간창/안전 중지 규칙은 `du`/`find`와 똑같이 적용한다 - 인덱싱도 파일
    시스템을 훑는 무거운 작업이기 때문. 중간에 멈추면 그때까지 커밋한 항목은
    남고, 완주하지 못했으므로 사라진 항목 정리는 다음 실행으로 미룬다
    (`search_index.index_account` 참고).

    인덱싱 실패가 스캔 전체를 실패로 만들지는 않는다. 용량 기준선이라는 핵심
    데이터는 이미 저장된 뒤이므로, 부가 기능 하나 때문에 그것을 무효로 만들
    이유가 없다."""

    if not account.search_indexing:
        return "skipped", 0

    stop_or_deadline = lambda: should_stop() or deadline_reached()
    if stop_or_deadline():
        return "interrupted", 0

    conn = None
    try:
        conn = search_index.connect(data_dir)
        count = search_index.index_account(
            conn, account.account_id, Path(account.path), should_stop=stop_or_deadline
        )
        return ("interrupted" if stop_or_deadline() else "done"), count
    except Exception:  # pragma: no cover - 방어적 처리
        logger.exception("검색 인덱싱 실패: %s", account.name)
        return "error", 0
    finally:
        if conn is not None:
            conn.close()


def run_nightly_scan(
    data_dir: Path,
    config: config_module.AppConfig,
    triggered_by: str = "cron",
    bypass_window: bool = False,
    clock: Optional[Callable[[], datetime]] = None,
    top_level_lister: Optional[Callable[[str], List[str]]] = None,
) -> RunSummary:
    """야간 상세 스캔 한 번(하룻밤 분량)을 실행한다.

    `bypass_window=True`는 "터미널에서 직접 실행"(DESIGN.md 1부 3절의 의도적
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
    # 체크포인트 하나가 끝날 때마다 CPU 점유를 표본으로 모은다 (작은 파일
    # 두 개를 읽는 것이 전부라 재는 행위가 부하가 되지는 않는다).
    load = loadstat.Accumulator()
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
                conn,
                data_dir,
                account,
                settings,
                clock,
                should_stop,
                deadline_reached,
                top_level_lister,
                load,
                run_id,
            )
            outcomes.append(outcome)
            if outcome.baseline_status == "interrupted" or outcome.activity_status == "interrupted":
                status = STATUS_STOPPED if should_stop() else STATUS_PAUSED
                break
    except Exception:  # pragma: no cover - 방어적 처리, 다음 실행에서 체크포인트로 재개
        logger.exception("야간 상세 스캔 중 예외 발생")
        status = STATUS_ERROR
    finally:
        scan_store.set_current_target(conn, run_id, None, None, None)
        scan_store.finish_run(conn, run_id, status, load=load.summary())
        conn.close()
        scan_lock.release_lock(data_dir, run_id)

    # 아래 후처리는 모두 "이미 저장된 스캔 결과"를 소비하기만 한다. 여기서
    # 실패해도 스캔 자체의 성패를 뒤집지 않는다.
    _notify_growth(data_dir, config, outcomes, local_now)
    _prune_orphan_search_indexes(data_dir, config)
    _generate_reports(data_dir, config, local_now)
    return RunSummary(started=True, status=status, run_id=run_id, accounts=outcomes)


def _notify_growth(
    data_dir: Path,
    config: config_module.AppConfig,
    outcomes: List[AccountOutcome],
    now: datetime,
) -> int:
    """이번에 기준선을 완주한 계정에 대해 급증 경로 알림을 보낸다.

    직전 완료 세대와 **같은 경로끼리** 비교한 증가량만 본다 (순위 비교가 아님).
    비교할 이전 세대가 없는 첫 기준선에서는 알리지 않는다 - 전부 '신규'로
    잡혀 의미 없는 알림 폭탄이 되기 때문."""

    settings = config.settings
    if not settings.growth_alert_enabled:
        return 0

    accounts_by_id = {a.account_id: a for a in config.accounts}
    state = notifications.load_notify_state(data_dir)
    sent = 0

    conn = scan_store.connect(data_dir)
    try:
        for outcome in outcomes:
            if outcome.baseline_status != "done":
                continue
            account = accounts_by_id.get(outcome.account_id)
            if account is None:
                continue
            current = outcome.baseline_generation
            previous = current - 1
            if previous < 1:
                continue  # 첫 기준선 - 비교 대상이 없다

            rows = scan_store.growth_delta(
                conn, account.account_id, current, previous, settings.detail_scan_top_n
            )
            for row in rows:
                # previous_kb가 None이면 이전 세대에 없던 새 경로다. 새로 생긴
                # 큰 디렉터리도 알릴 가치가 있으므로 현재 크기 전체를 증가분
                # 으로 본다 (maybe_notify_growth가 그렇게 처리한다).
                result = notifications.maybe_notify_growth(
                    data_dir,
                    account,
                    row["path"],
                    row["current_kb"],
                    row["previous_kb"],
                    state,
                    min_increase_kb=settings.growth_alert_min_kb,
                    cooldown_minutes=settings.notification_cooldown_minutes,
                    now=now,
                    mode=settings.notification_mode,
                    command=settings.notification_command,
                    webhook_url=settings.notification_webhook_url,
                    timeout_seconds=settings.notification_timeout_seconds,
                )
                if result is not None:
                    sent += 1
    except Exception:  # pragma: no cover - 방어적 처리
        logger.exception("급증 알림 발송 실패 (스캔 결과는 이미 저장됨)")
    finally:
        conn.close()

    notifications.save_notify_state(data_dir, state)
    return sent


def _prune_orphan_search_indexes(data_dir: Path, config: config_module.AppConfig) -> None:
    """설정에서 사라진 계정의 검색 인덱스를 정리한다.

    GUI에서 계정을 지울 때도 정리하지만, 그때 GUI가 먼저 닫히는 등으로 정리가
    끝나지 않을 수 있다. 야간 실행마다 한 번 더 대조해 orphan이 영구히 남지
    않게 한다."""

    if not search_index.db_path(data_dir).exists():
        return
    conn = None
    try:
        conn = search_index.connect(data_dir)
        active = [account.account_id for account in config.accounts if account.search_indexing]
        search_index.prune_orphans(conn, active)
    except Exception:  # pragma: no cover - 방어적 처리
        logger.exception("검색 인덱스 정리 실패")
    finally:
        if conn is not None:
            conn.close()


def _generate_reports(data_dir: Path, config: config_module.AppConfig, now: datetime) -> None:
    """스캔이 끝나면 보고서를 갱신한다.

    보고서 생성 실패가 스캔 결과 자체를 무효로 만들면 안 된다 - 스캔 데이터는
    이미 DB에 안전하게 들어갔으므로, 여기서 나는 오류는 기록만 하고 넘어간다."""

    kinds = [reports.DAILY, reports.CLEANUP]
    if reports.should_build_weekly(config, now):
        kinds.append(reports.WEEKLY)
    try:
        reports.generate(data_dir, config, kinds=kinds, now=now)
    except Exception:  # pragma: no cover - 방어적 처리
        logger.exception("보고서 생성 실패 (스캔 결과는 이미 저장됨)")


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
    # 진행률을 보여주기 위한 개수. 분모(total)는 **진행 중 늘어날 수 있다** -
    # 시간 초과로 디렉터리를 쪼개면 작업이 추가되기 때문이다. 그래서 화면에
    # 그 사실을 함께 적는다 (숨기면 진행률이 뒤로 가는 것처럼 보인다).
    baseline_done: int = 0
    baseline_total: int = 0
    # 화면과 보고서는 스캔을 **날짜로** 가리킨다 ("260819 스캔"). 회차
    # 번호는 내부 값이라 사용자에게 기준점이 못 된다.
    current_scan_at: Optional[str] = None
    previous_scan_at: Optional[str] = None
    # 권한 등으로 일부만 읽어 실제보다 작게 측정된 경로들. 비어 있지 않으면
    # 화면에 알려야 한다 - 모르고 보면 증가량을 잘못 해석하게 된다.
    partial_paths: List[str] = field(default_factory=list)
    # 아예 재지 못한 경로와 사유 `[(path, message), ...]`. 이것을 안 보여 주면
    # 사용자에게는 "스캔이 그냥 실패했다"로만 보인다.
    failed_paths: List[tuple] = field(default_factory=list)
    failed_count: int = 0


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
            baseline_counts = scan_store.checkpoint_progress(
                conn, account.account_id, scan_store.BASELINE, state.working_generation
            )
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
                    current_scan_at=(
                        scan_store.generation_completed_at(conn, account.account_id, current_gen)
                        if current_gen else None
                    ),
                    previous_scan_at=(
                        scan_store.generation_completed_at(conn, account.account_id, previous_gen)
                        if previous_gen else None
                    ),
                    top_paths=top,
                    growth=growth,
                    last_activity_total_changed=state.last_activity_total_changed,
                    last_activity_completed_at=state.last_activity_completed_at,
                    pending_baseline_count=pending_baseline_count,
                    pending_activity_count=pending_activity_count,
                    baseline_done=baseline_counts["done"] + baseline_counts["error"],
                    baseline_total=baseline_counts["total"],
                    partial_paths=(
                        scan_store.partial_paths(conn, account.account_id, current_gen)
                        if current_gen
                        else []
                    ),
                    # 실패는 **작업 중인 세대** 기준으로 본다. 전부 실패하면
                    # 완료 세대가 아예 생기지 않아, 완료 세대로 조회하면 정작
                    # 사용자가 겪는 실패가 하나도 안 보인다.
                    failed_paths=scan_store.failed_paths(
                        conn, account.account_id, state.working_generation
                    ),
                    failed_count=scan_store.failed_count(
                        conn, account.account_id, state.working_generation
                    ),
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


def mark_interrupted_run(data_dir: Path) -> bool:
    """진행 중이던 실행을 `stopped`로 마감하고 잠금을 푼다.

    창을 닫아 스캔 스레드가 잘리면 `run_nightly_scan`의 `finally`가 돌지 못해
    실행 행이 영영 `running`으로 남는다. 다음에 GUI를 열었을 때 "최근 실행:
    running"이 계속 보이면 지금 뭔가 돌고 있는 것으로 오해하게 된다.

    잠금도 함께 푼다. `is_locked`가 pid 생존을 확인하므로 남아 있어도 다음
    스캔을 막지는 않지만, 실제 상태와 파일을 굳이 어긋나게 둘 이유가 없다.
    """

    conn = scan_store.connect(data_dir)
    try:
        row = scan_store.latest_run(conn)
        if row is None or row["status"] != "running":
            return False
        run_id = row["run_id"]
        scan_store.finish_run(conn, run_id, STATUS_STOPPED)
    finally:
        conn.close()

    scan_lock.release_lock(data_dir, run_id)
    return True


def request_stop(data_dir: Path) -> bool:
    """현재 실행 중인 야간 스캔에 안전 중지를 요청한다. 실행 중인 게 없으면
    False."""

    info = scan_lock.read_lock(data_dir)
    if info is None:
        return False
    scan_lock.request_stop(data_dir, info.run_id)
    return True
