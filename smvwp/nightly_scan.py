"""야간 상세 스캔 오케스트레이터 - 계정을 돌아가며(공정성) 처리하고,
시간창/안전 중지/잠금을 모두 여기서 조율한다.

DESIGN.md 1부 7절이 요구하는 안전장치들을 한 곳에 모았다:
- "한 계정이 느려도 다른 계정 보고가 막히지 않는다" -> 계정 하나가 밤을 다
  써도(=deadline 도달) 그 계정 체크포인트에 정확히 표시해 두고 조용히
  멈춘다. 예외를 밖으로 던지지 않는다.
- "여러 계정의 상세 작업은 동시에 두 개 이상 돌리지 않고 직렬 처리" ->
  `scan_lock`으로 전체 실행 자체를 하나만 허용하고, **기본값에서는** 계정도
  한 번에 하나씩만 처리한다.
- "완료 순서를 날짜별로 순환시켜 특정 계정이 계속 뒤로 밀리지 않게 공정성
  확보" -> `_rotate_accounts`가 오늘 날짜를 시드로 시작 위치를 돌린다.
- 06:00에는 "완료된 체크포인트를 남기고 paused로 종료" -> `status`가
  completed/paused/stopped/error로 구분되어 강제 종료와 다르다는 게 남는다.

## 계정 병렬 실행 - 평일 밤과 주말 밤이 다르다

위의 "직렬 처리" 원칙은 **평일 밤 기준**이다. 주말 아침에 출근하는 인원은
평일의 10~20% 수준이라 같은 부하가 훨씬 적은 사람에게만 닿으므로, 주말 밤에는
계정을 동시에 여러 개 돈다 (`weekend_parallel_accounts`, 기본 3).

여기서 "주말 밤"은 날짜가 아니라 **끝나는 아침**으로 정한다 - 금요일 밤은
주말이고 일요일 밤은 평일이다. 이유는 `scan_window.ends_on_weekend` 참고.

평일 밤은 기존과 같은 직렬(`nightly_parallel_accounts`, 기본 1)이고,
`--parallel N`은 둘 다 무시하고 그 값을 쓴다 (실측용).

병렬이 이 장비에서 실제로 이득인지는 미리 알 수 없다. 그래서 어느 경로로
돌았든 같은 실행에서 리소스 시계열(`scan_load_samples`)과 그때의 동시 계정
수를 함께 기록해, 다음 판단의 근거가 남게 한다. 자세한 득실은
`_run_accounts_parallel` 참고.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
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
    # 이 실행이 계정을 몇 개씩 동시에 돌렸는가 (1 = 직렬). 부하 실측 결과를
    # 읽을 때 가장 먼저 필요한 조건이라 요약에도 싣는다.
    parallel_accounts: int = 1
    # 그 값이 주말 밤이라서 정해졌는가. 숫자만 보면 "왜 오늘은 3이지"를 알 수
    # 없어 설정이 잘못된 줄 안다.
    weekend_night: bool = False


# 스캔 시작 전 기준값을 잡을 때 두 번 재는 사이의 간격. CPU 사용률은 두 시점의
# 차이로만 구할 수 있어 최소한의 대기가 필요하다. 테스트는 이 값을 0으로 낮춰
# 부른다 (모듈 상수로 둔 것은 그래서다).
BASELINE_WARMUP_SECONDS = 1.0


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class _ActiveCounter:
    """지금 몇 개 계정이 동시에 돌고 있는지 세는 카운터.

    리소스 표본에 이 값을 같이 남겨야 나중에 시계열을 읽을 수 있다 - 부하가
    튄 구간이 계정 하나 때문인지 여섯 개가 겹친 탓인지는 이 숫자 없이는
    구분되지 않는다."""

    def __init__(self):
        self._count = 0
        self._lock = threading.Lock()

    def __call__(self) -> int:
        with self._lock:
            return self._count

    def enter(self) -> None:
        with self._lock:
            self._count += 1

    def leave(self) -> None:
        with self._lock:
            self._count = max(0, self._count - 1)


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
        detail_scan.process_one_checkpoint(
            conn,
            checkpoint,
            settings.detail_task_timeout_seconds,
            max_depth=settings.detail_scan_max_depth,
            generation=generation,
        )
        if load is not None:
            load.sample()

    # 결과는 체크포인트를 처리하면서 이미 baseline_results에 들어갔다
    # (`du -k` 한 번이 서브트리 전체를 주므로). 예전처럼 끝에서 체크포인트를
    # 훑어 만들 필요가 없다 - 그렇게 하면 쪼개진 서브트리의 내부가 통째로
    # 빠진다.
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


def resolve_parallel_accounts(
    local_now: datetime, settings: config_module.Settings
) -> "tuple[int, bool]":
    """이 밤에 쓸 동시 실행 계정 수와, 그것이 주말 값인지를 돌려준다.

    주말 밤에는 아침에 출근하는 인원이 평일의 10~20% 수준이라 더 세게 돌 수
    있다. "주말 밤"의 정의는 `scan_window.ends_on_weekend`에 있다 - 금요일
    밤은 주말이고 일요일 밤은 평일이다.

    **시간창 밖(=낮)에서 도는 실행에는 주말 규칙을 적용하지 않는다.** 낮에
    손으로 돌리는 것은 진단/복구 경로인데(DESIGN.md 1부 3절), 토요일 오후라도
    그때 자리에 있는 사람은 지금 일하고 있는 사람이다. 세게 돌려야 할 이유가
    있으면 `--parallel`로 의도를 밝히면 된다.
    """

    within_window = scan_window.is_within_window(
        local_now,
        settings.detail_scan_window_start_hour,
        settings.detail_scan_window_end_hour,
    )
    if within_window and scan_window.ends_on_weekend(
        local_now, settings.detail_scan_window_end_hour
    ):
        return settings.weekend_parallel_accounts, True
    return settings.nightly_parallel_accounts, False


def _outcome_interrupted(outcome: AccountOutcome) -> bool:
    return outcome.baseline_status == "interrupted" or outcome.activity_status == "interrupted"


def _run_accounts_serial(
    conn,
    data_dir: Path,
    accounts: List[config_module.Account],
    settings: config_module.Settings,
    clock,
    should_stop,
    deadline_reached,
    top_level_lister,
    load,
    run_id: str,
    outcomes: List[AccountOutcome],
    active: _ActiveCounter,
) -> str:
    """지금까지의 동작 그대로 - 계정을 하나씩 순서대로."""

    for account in accounts:
        if should_stop():
            return STATUS_STOPPED
        if deadline_reached():
            return STATUS_PAUSED
        active.enter()
        try:
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
        finally:
            active.leave()
        outcomes.append(outcome)
        if _outcome_interrupted(outcome):
            return STATUS_STOPPED if should_stop() else STATUS_PAUSED
    return STATUS_COMPLETED


def _run_accounts_parallel(
    data_dir: Path,
    accounts: List[config_module.Account],
    settings: config_module.Settings,
    clock,
    should_stop,
    deadline_reached,
    top_level_lister,
    load,
    run_id: str,
    outcomes: List[AccountOutcome],
    active: _ActiveCounter,
    workers: int,
) -> str:
    """계정 여러 개를 동시에 돈다. **부하 실측용 경로다** (기본은 직렬).

    ## 왜 연결을 스레드마다 새로 여는가

    `sqlite3` 연결은 만든 스레드에서만 쓰는 것이 기본값이다
    (`check_same_thread`). 하나를 공유하려면 그 검사를 끄고 모든 호출을 직접
    직렬화해야 하는데, 그러면 병렬로 만든 의미가 절반 사라지고 잠금 버그의
    여지만 생긴다. WAL에서는 연결이 여러 개여도 읽기는 서로 막지 않고 쓰기만
    순서를 기다린다 (`connect`가 이미 `timeout=10`을 준다).

    ## 얻는 것과 잃는 것 (쓰기 전에 알아야 할 것)

    - 얻는 것: 계정들이 **서로 다른 볼륨**에 있으면 벽시계 시간이 준다.
    - 잃는 것: `nice`/`ionice`로 한 번에 하나만 얌전히 돌던 전제가 깨진다.
      같은 볼륨에 몰려 있으면 seek 경합으로 **오히려 느려질 수 있다.**

    어느 쪽인지는 장비마다 다르고 미리 알 수 없다 - 그래서 이 경로는 기본이
    아니라 `--parallel`로 의도를 밝혔을 때만 열리고, 같은 실행에서 리소스
    시계열을 남겨 다음 판단의 근거가 되게 한다.

    ## 진행 표시에 대한 주의

    `set_current_target`은 "지금 이 경로"를 실행 행 하나에 적는다. 병렬에서는
    마지막에 쓴 스레드의 경로만 남으므로, 진행 화면에 보이는 경로는 **여럿 중
    하나**다. 표시용 정보라 틀려도 데이터에는 영향이 없지만, 그 화면을 보고
    "하나씩 돌고 있구나"라고 오해하지 않도록 표본의 `active_accounts`를 함께
    남긴다.
    """

    interrupted_status: List[Optional[str]] = [None]

    def work(account: config_module.Account) -> Optional[AccountOutcome]:
        # 큐에 미리 다 넣어 두므로, 순서를 기다리는 동안 시간창이 끝났을 수
        # 있다. 시작 직전에 다시 확인한다.
        if should_stop() or deadline_reached():
            return None
        worker_conn = scan_store.connect(data_dir)
        active.enter()
        try:
            return _process_account(
                worker_conn,
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
        finally:
            active.leave()
            worker_conn.close()

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="smvwp-scan"
    ) as pool:
        futures = {pool.submit(work, account): account for account in accounts}
        for future in concurrent.futures.as_completed(futures):
            account = futures[future]
            try:
                outcome = future.result()
            except Exception:  # pragma: no cover - 계정 하나의 실패가 전체를 죽이지 않는다
                logger.exception("계정 스캔 실패: %s", account.name)
                continue
            if outcome is None:  # 시간창/중지로 시작조차 못 함
                continue
            outcomes.append(outcome)
            if _outcome_interrupted(outcome):
                interrupted_status[0] = STATUS_STOPPED if should_stop() else STATUS_PAUSED

    if interrupted_status[0]:
        return interrupted_status[0]
    if len(outcomes) < len(accounts):
        # 시작도 못 한 계정이 남았다면 완주가 아니다. 다음 실행이 이어받는다.
        return STATUS_STOPPED if should_stop() else STATUS_PAUSED
    return STATUS_COMPLETED


def run_nightly_scan(
    data_dir: Path,
    config: config_module.AppConfig,
    triggered_by: str = "cron",
    bypass_window: bool = False,
    clock: Optional[Callable[[], datetime]] = None,
    top_level_lister: Optional[Callable[[str], List[str]]] = None,
    parallel_accounts: Optional[int] = None,
    baseline_warmup_seconds: float = BASELINE_WARMUP_SECONDS,
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

    # `--parallel`이 설정값을 이긴다. 설정은 "평소 이렇게 돈다"이고 명령줄
    # 인자는 "이번엔 이렇게 돌려 보겠다"라, 실측을 하려면 후자가 이겨야 한다.
    # 값을 안 주면 이 밤이 주말 밤인지에 따라 평일값/주말값이 갈린다.
    # 판정은 `--parallel` 여부와 무관하게 항상 한다. `weekend_night`은 "몇 개로
    # 돌았나"가 아니라 "어떤 밤이었나"를 기록하는 값이라, 사람이 값을 지정한
    # 실행에서도 사실대로 남아야 나중에 비교가 된다.
    resolved, weekend_night = resolve_parallel_accounts(local_now, settings)
    if parallel_accounts is None:
        parallel_accounts = resolved
    parallel_accounts = max(1, min(16, int(parallel_accounts)))

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
    active = _ActiveCounter()
    # 체크포인트 주기와 별개로 **일정 시간마다** 시스템 전체 리소스를 찍는다.
    # 체크포인트 하나가 15분까지 갈 수 있어, 그 안에서 부하가 어떻게 움직였는지는
    # 주기 표본이 아니면 볼 수 없다.
    recorder = loadstat.Recorder(
        interval_seconds=settings.load_sample_interval_seconds,
        accumulator=load,
        active_accounts=active,
    )
    try:
        scan_store.start_run(conn, run_id, triggered_by)
        scan_store.record_parallelism(conn, run_id, parallel_accounts)
        # 기준값은 반드시 **스캔을 시작하기 전에** 잡는다. 이 표본이 없으면
        # 나중에 "스캔 때문에 튄 것"과 "원래 그랬던 것"을 구분할 수 없다.
        recorder.baseline(warmup_seconds=baseline_warmup_seconds)
        recorder.start()

        if bypass_window:
            deadline_reached = lambda: False
        else:
            window_end = scan_window.next_window_end(local_now, settings.detail_scan_window_end_hour)
            deadline_reached = lambda: clock() >= window_end

        should_stop = lambda: scan_lock.is_stop_requested(data_dir, run_id)

        accounts = _rotate_accounts(config_module.enabled_accounts(config), local_now)
        if parallel_accounts > 1 and len(accounts) > 1:
            status = _run_accounts_parallel(
                data_dir,
                accounts,
                settings,
                clock,
                should_stop,
                deadline_reached,
                top_level_lister,
                load,
                run_id,
                outcomes,
                active,
                workers=min(parallel_accounts, len(accounts)),
            )
        else:
            status = _run_accounts_serial(
                conn,
                data_dir,
                accounts,
                settings,
                clock,
                should_stop,
                deadline_reached,
                top_level_lister,
                load,
                run_id,
                outcomes,
                active,
            )
    except Exception:  # pragma: no cover - 방어적 처리, 다음 실행에서 체크포인트로 재개
        logger.exception("야간 상세 스캔 중 예외 발생")
        status = STATUS_ERROR
    finally:
        # 기록기를 먼저 세운다. 스캔이 끝난 뒤의 표본은 "스캔 중"이 아니므로
        # 시계열에 섞이면 평균과 최고치를 함께 흐린다.
        recorder.stop()
        scan_store.set_current_target(conn, run_id, None, None, None)
        scan_store.finish_run(conn, run_id, status, load=load.summary())
        try:
            scan_store.save_load_samples(conn, run_id, recorder.samples())
            scan_store.prune_load_samples(conn, settings.load_sample_retention_days)
        except Exception:  # pragma: no cover - 측정 기록 실패가 스캔을 실패로 만들지 않는다
            logger.exception("리소스 표본 저장 실패")
        conn.close()
        scan_lock.release_lock(data_dir, run_id)

    # 아래 후처리는 모두 "이미 저장된 스캔 결과"를 소비하기만 한다. 여기서
    # 실패해도 스캔 자체의 성패를 뒤집지 않는다.
    _notify_growth(data_dir, config, outcomes, local_now)
    _prune_orphan_search_indexes(data_dir, config)
    _generate_reports(data_dir, config, local_now)
    return RunSummary(
        started=True,
        status=status,
        run_id=run_id,
        accounts=outcomes,
        parallel_accounts=parallel_accounts,
        weekend_night=weekend_night,
    )


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
            top = (
                scan_store.top_paths(
                    conn, account.account_id, current_gen, top_n,
                    max_depth=config.settings.growth_list_max_depth,
                )
                if current_gen else []
            )
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
