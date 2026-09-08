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

## 병렬 실행 - 단위는 계정이 아니라 **볼륨**이다

`nightly_parallel_accounts` / `weekend_parallel_accounts`, 그리고 `--parallel N`
은 **동시에 도는 볼륨 수의 상한**이다. 계정 수가 아니다 - 실측이 그렇게 정했다:

    같은 볼륨: 동시 4 -> 36.0초 / 8 -> 35.7초 / 16 -> 37.1초 (안 줄어든다)
    다른 볼륨: 각각 36초로 처리량이 배가 된다

NetApp 은 볼륨 단위로 처리 능력이 갈리므로, 같은 볼륨을 여럿이 두들기면 서로를
방해할 뿐이다. 그래서 `group_accounts_by_volume`이 계정을 볼륨별로 묶고
**묶음끼리는 동시에, 묶음 안에서는 하나씩** 돈다. 계정이 전부 한 볼륨에 있으면
상한을 아무리 올려도 직렬이다 (그게 가장 빠른 길이라서다).

평일 밤과 주말 밤은 지금 같은 값을 쓴다 - 야간 결재가 없는 한 어느 쪽이든
사람이 없기 때문이다. 구분 자체는 남겨 두었다: "주말 밤"은 날짜가 아니라
**끝나는 아침**으로 정하므로 금요일 밤은 주말, 일요일 밤은 평일이다. 이유는
`scan_window.ends_on_weekend` 참고.

어느 경로로 돌았든 리소스 시계열(`scan_load_samples`)과 함께 **실제로** 동시에
돈 갈래 수를 기록한다 - 설정한 상한이 아니라 실측치여야 나중에 "동시 N일 때
부하가 이랬다"를 옳게 읽는다. 자세한 득실은 `_run_accounts_parallel` 참고.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from . import (
    activity_scan,
    config as config_module,
    detail_scan,
    notifications,
    paths,
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
    # 이 실행이 **실제로** 몇 갈래를 동시에 돌렸는가 (1 = 직렬). 설정한
    # 상한이 아니라 실측치다 - 계정이 전부 한 볼륨에 있으면 상한이 4여도
    # 1이 된다. 부하 시계열을 읽을 때 가장 먼저 필요한 조건이라 요약에도
    # 싣는다.
    parallel_accounts: int = 1
    # 그 값이 주말 밤이라서 정해졌는가. 숫자만 보면 "왜 오늘은 3이지"를 알 수
    # 없어 설정이 잘못된 줄 안다.
    weekend_night: bool = False


# 스캔 시작 전 기준값을 잡을 때 두 번 재는 사이의 간격. CPU 사용률은 두 시점의
# 차이로만 구할 수 있어 최소한의 대기가 필요하다. 테스트는 이 값을 0으로 낮춰
# 부른다 (모듈 상수로 둔 것은 그래서다).
BASELINE_WARMUP_SECONDS = 1.0

# 잠금이 이미 잡혀 있을 때 얼마나 기다렸다 포기하는가.
#
# **기다리는 것은 cron 경로뿐이다** (`run_nightly_scan` 의 기본값은 0). GUI 에서
# 버튼을 누른 사람은 20분을 기다리는 것이 아니라 "이미 실행 중"이라는 답을
# 즉시 들어야 한다. 기다림이 필요한 쪽은 밤을 놓치면 안 되는 cron 뿐이다.
#
# 낮에 사람이 눌러 둔 스캔이 22:00 에 아직 돌고 있을 수 있다. 그것은 야간 창이
# 열린 것을 보고 스스로 물러나지만, 즉시는 아니다 - 재고 있던 디렉터리 하나를
# 마저 끝내고 멈추므로 최대 `detail_task_timeout_seconds`(기본 15분)가 걸린다.
# 그보다 넉넉히 기다려야 그날 밤을 잃지 않는다.
#
# 기다리는 동안 하는 일은 없다. 잠금 파일을 몇 번 들여다보는 것이 전부다.
DEFAULT_LOCK_WAIT_SECONDS = 20 * 60
LOCK_POLL_SECONDS = 20.0


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


class _CheckpointDispatcher:
    """대기 중인 체크포인트를 작업자들에게 겹치지 않게 나눠 준다.

    ## 왜 이게 필요한가 - `du`는 혼자서는 NFS를 못 채운다

    반입 대상이 NFS라 병목은 파일 하나당 `getattr` RPC 왕복이다. `du`는
    디렉터리를 한 줄로 걷기 때문에 **언제나 요청이 하나만** 떠 있고, 실기의
    RPC 슬롯 상한 128 중 1개만 쓴다.

    실측이 이를 확인해 준다 - 같은 트리에서 병렬 walker(`gdu`)가 `du`보다
    **4배 빨랐다.** 서버가 아니라 우리 요청 방식이 병목이라는 뜻이다.

    그런데 `gdu`로 갈아탈 수는 없다. 트리를 메모리에 다 만든 뒤 내보내므로
    시간 초과 시 부분 결과가 통째로 사라지고, 그러면 재개 설계가 무너진다
    (`detail_scan.process_one_checkpoint` 참고). 대신 **이미 쪼개 둔 체크포인트
    여러 개를 동시에 `du`로 돌린다** - 같은 동시성을 얻으면서 `du`의 점진적
    출력은 그대로 유지된다.

    ## DB에 상태를 추가하지 않는다

    배정 정보는 이 프로세스 메모리에만 둔다. DESIGN.md 1부 7절이 체크포인트에
    'running' 상태를 만들지 않기로 한 것을 지키기 위해서다 - 중간 상태가 있으면
    프로세스가 죽었을 때 되돌리는 복구 로직이 필요해진다. 여기서는 죽으면 이
    집합도 함께 사라지고 DB에는 pending만 남으므로, 다음 실행이 아무 조치 없이
    이어받는다.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._claimed = set()

    def claim(self, conn, account_id: str, kind: str, generation: int):
        """다음 체크포인트를 잡는다. 남은 것이 없으면 None."""

        with self._lock:
            row = scan_store.next_pending(
                conn, account_id, kind, generation, exclude_ids=self._claimed
            )
            if row is not None:
                self._claimed.add(row["id"])
            return row

    def release(self, checkpoint_id) -> None:
        with self._lock:
            self._claimed.discard(checkpoint_id)

    def busy(self) -> int:
        with self._lock:
            return len(self._claimed)


def _drain_checkpoints(
    data_dir: Path,
    account: config_module.Account,
    kind: str,
    generation: int,
    workers: int,
    handle,
    should_stop,
    deadline_reached,
    load,
    run_id,
) -> bool:
    """이 계정의 대기 체크포인트를 작업자 `workers`개로 비운다.

    끝까지 비웠으면 True, 중지·시간창으로 중단됐으면 False.

    `handle(conn, checkpoint)`가 체크포인트 하나를 처리한다 (`du` 또는 `find`).
    """

    dispatcher = _CheckpointDispatcher()
    interrupted = threading.Event()

    def worker():
        conn = scan_store.connect(data_dir)
        try:
            while True:
                if should_stop() or deadline_reached():
                    interrupted.set()
                    return
                checkpoint = dispatcher.claim(conn, account.account_id, kind, generation)
                if checkpoint is None:
                    # 큐가 잠깐 비었다고 끝난 것이 아니다. 다른 작업자가 붙잡고
                    # 있는 체크포인트가 시간 초과로 **분할되면 자식이 새로
                    # 들어온다.** 아무도 일하고 있지 않을 때만 진짜 끝이다.
                    if dispatcher.busy() == 0:
                        return
                    time.sleep(0.2)
                    continue
                # "지금 이 경로"는 작업자가 하나일 때만 적는다.
                #
                # 여럿이면 값이 애초에 뜻을 잃는다 - 넷이 동시에 도는데 그중
                # 마지막에 쓴 하나만 남으니 화면이 "하나씩 돈다"고 오해하게
                # 만든다. 게다가 이 UPDATE는 체크포인트마다 commit을 부르는데,
                # 데이터 디렉터리가 NFS면 그 하나가 저널 파일 왕복이라
                # 작업자 수만큼 쓰기 경합이 늘어난다. 표시용 정보를 위해
                # 스캔을 느리게 만들 이유가 없다.
                if run_id and workers <= 1:
                    scan_store.set_current_target(
                        conn, run_id, account.account_id, kind, checkpoint["path"]
                    )
                try:
                    handle(conn, checkpoint)
                finally:
                    dispatcher.release(checkpoint["id"])
                if load is not None:
                    load.sample()
        finally:
            conn.close()

    if workers <= 1:
        worker()
    else:
        threads = [
            threading.Thread(target=worker, name=f"smvwp-cp-{index}", daemon=True)
            for index in range(workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    return not interrupted.is_set()


def _engine_plan(settings) -> "tuple[str, int, int]":
    """`(엔진, 동시 체크포인트 수, 순회 스레드 수)`.

    동시에 띄우는 요청의 총량은 엔진과 무관하게 `checkpoint_workers` 하나로
    정한다. 다만 **어디서 동시성을 내는지가 다르다.**

    - `du`: 체크포인트를 N개 동시에 처리하고, 각 `du` 는 한 줄로 걷는다.
    - 파이썬 순회: 체크포인트는 **하나씩** 처리하되 그 안에서 N개 스레드로
      걷는다.

    순회 쪽을 이렇게 나눈 이유는 큰 계정 때문이다. 체크포인트를 나눠 갖는
    방식은 서브트리가 여러 개일 때만 듣지만, 순회는 **서브트리 하나짜리
    계정에서도** 그 안을 동시에 판다 - 밤을 다 먹는 계정이 늘 그런 모양이다.

    덤으로 진행 표시가 되살아난다. 체크포인트를 하나씩 처리하면 "지금 이
    경로"가 뜻을 되찾기 때문이다 (`_drain_checkpoints` 의 `workers <= 1` 조건).
    """

    workers = max(1, settings.checkpoint_workers)
    if getattr(settings, "scan_engine", config_module.SCAN_ENGINE_DU) == config_module.SCAN_ENGINE_PYTHON:
        return detail_scan.ENGINE_PYTHON, 1, workers
    return detail_scan.ENGINE_DU, workers, 1


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
    data_dir: Path,
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

    engine, checkpoint_workers, walk_workers = _engine_plan(settings)

    def handle(worker_conn, checkpoint):
        detail_scan.process_one_checkpoint(
            worker_conn,
            checkpoint,
            settings.detail_task_timeout_seconds,
            max_depth=settings.detail_scan_max_depth,
            generation=generation,
            engine=engine,
            workers=walk_workers,
        )

    completed = _drain_checkpoints(
        data_dir,
        account,
        scan_store.BASELINE,
        generation,
        checkpoint_workers,
        handle,
        should_stop,
        deadline_reached,
        load,
        run_id,
    )
    if not completed:
        return "interrupted", generation

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
        conn, data_dir, account, settings, clock, should_stop, deadline_reached,
        top_level_lister, load, run_id
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


def group_accounts_by_volume(
    accounts: List[config_module.Account],
) -> List[List[config_module.Account]]:
    """같은 저장소(볼륨)에 있는 계정들을 한 묶음으로 만든다.

    ## 왜 계정이 아니라 볼륨인가 - 실측이 정했다

    같은 디렉터리를 여러 갈래로 쪼개도 빨라지지 않았다:

        동시 4 -> 36.0초 / 동시 8 -> 35.7초 / 동시 16 -> 37.1초

    그런데 **서로 다른** 디렉터리 둘을 동시에 돌리면 각각 36초로 처리량이
    배가 됐다. 차이는 볼륨이다 - NetApp 은 볼륨 단위로 처리 능력이 갈리므로,
    한 볼륨 안에서는 아무리 나눠도 그 볼륨의 한계를 넘지 못한다.

    그래서 "계정 N개를 동시에"는 틀린 단위다. 같은 볼륨의 계정 둘을 동시에
    돌리면 서로를 방해할 뿐이고, 다른 볼륨이면 그만큼 그대로 벌어진다.
    **묶음끼리는 동시에, 묶음 안에서는 하나씩**이 맞다.

    묶음 안의 순서는 유지한다 - 공정성 순환(`_rotate_accounts`)이 정한 순서를
    여기서 뒤집으면 특정 계정이 계속 뒤로 밀린다.
    """

    grouped = paths.group_by_volume(
        [(account.account_id, account.path) for account in accounts]
    )
    by_id = {account.account_id: account for account in accounts}
    return [
        [by_id[account_id] for account_id in ids]
        for ids in grouped.values()
        if ids
    ]


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
    groups: List[List[config_module.Account]],
) -> str:
    """**볼륨 묶음끼리** 동시에 돈다 (묶음 안에서는 하나씩).

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

    def work(group: List[config_module.Account]) -> List[AccountOutcome]:
        """한 볼륨의 계정들을 **하나씩** 처리한다.

        같은 볼륨을 동시에 두들겨 봐야 서로를 방해할 뿐이라 순서대로 간다.
        볼륨끼리의 동시성은 바깥 풀이 낸다."""

        produced: List[AccountOutcome] = []
        worker_conn = scan_store.connect(data_dir)
        active.enter()
        try:
            for account in group:
                # 순서를 기다리는 동안 시간창이 끝났을 수 있다.
                if should_stop() or deadline_reached():
                    break
                produced.append(_process_account(
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
                ))
            return produced
        finally:
            active.leave()
            worker_conn.close()

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(groups), thread_name_prefix="smvwp-vol"
    ) as pool:
        futures = {pool.submit(work, group): group for group in groups}
        for future in concurrent.futures.as_completed(futures):
            group = futures[future]
            try:
                produced = future.result()
            except Exception:  # pragma: no cover - 볼륨 하나의 실패가 전체를 죽이지 않는다
                logger.exception(
                    "볼륨 스캔 실패: %s", ", ".join(a.name for a in group)
                )
                continue
            for outcome in produced:
                outcomes.append(outcome)
                if _outcome_interrupted(outcome):
                    interrupted_status[0] = (
                        STATUS_STOPPED if should_stop() else STATUS_PAUSED
                    )

    if interrupted_status[0]:
        return interrupted_status[0]
    if len(outcomes) < len(accounts):
        # 시작도 못 한 계정이 남았다면 완주가 아니다. 다음 실행이 이어받는다.
        return STATUS_STOPPED if should_stop() else STATUS_PAUSED
    return STATUS_COMPLETED


def _acquire_with_wait(
    data_dir: Path, triggered_by: str, wait_seconds: float, clock
) -> Optional[str]:
    """잠금을 잡는다. 이미 잡혀 있으면 `wait_seconds` 동안 기다려 본다.

    ## 왜 기다리는가

    낮에 사람이 GUI 에서 눌러 둔 스캔이 22:00 에도 돌고 있을 수 있다. 예전에는
    그때 cron 이 조용히 물러나고 **그날 밤이 통째로 날아갔다** - 로그에 한 줄
    남을 뿐이라 아무도 몰랐다.

    이제 낮 실행이 야간 창을 보고 스스로 물러나므로, 잠깐만 기다리면 자리가
    난다. 다만 즉시는 아니다 - 재고 있던 디렉터리를 마저 끝내고 멈추기
    때문이다. 그래서 기다린다.

    기다리는 동안 아무 일도 하지 않는다. 잠금 파일을 20초에 한 번 들여다보는
    것이 전부라 그 자체가 부담이 되지는 않는다.
    """

    deadline = time.monotonic() + max(0.0, wait_seconds)
    first = True
    while True:
        try:
            return scan_lock.acquire_lock(data_dir, triggered_by)
        except scan_lock.LockBusyError:
            if time.monotonic() >= deadline:
                return None
            if first:
                holder = scan_lock.read_lock(data_dir)
                logger.info(
                    "잠금을 쥔 실행이 있어 최대 %.0f분 기다립니다 (%s)",
                    wait_seconds / 60,
                    holder.triggered_by if holder else "알 수 없음",
                )
                first = False
            time.sleep(min(LOCK_POLL_SECONDS, max(0.0, deadline - time.monotonic())))


def run_nightly_scan(
    data_dir: Path,
    config: config_module.AppConfig,
    triggered_by: str = "cron",
    bypass_window: bool = False,
    clock: Optional[Callable[[], datetime]] = None,
    top_level_lister: Optional[Callable[[str], List[str]]] = None,
    parallel_accounts: Optional[int] = None,
    baseline_warmup_seconds: float = BASELINE_WARMUP_SECONDS,
    lock_wait_seconds: float = 0.0,
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

    run_id = _acquire_with_wait(data_dir, triggered_by, lock_wait_seconds, clock)
    if run_id is None:
        existing = scan_lock.read_lock(data_dir)
        holder = f" (run_id={existing.run_id}, {existing.triggered_by})" if existing else ""
        return RunSummary(
            started=False,
            status=STATUS_NOT_STARTED,
            reason=f"이미 실행 중인 스캔이 있어 시작하지 못했습니다{holder}",
        )

    scan_lock.clear_stop_request(data_dir)

    accounts = _rotate_accounts(config_module.enabled_accounts(config), local_now)
    # 계정을 저장소(볼륨)별로 묶는다. 같은 볼륨을 동시에 두들겨 봐야 서로를
    # 방해할 뿐이고, 다른 볼륨이면 그만큼 그대로 벌어진다
    # (`group_accounts_by_volume` 의 실측 근거 참고).
    #
    # `parallel_accounts` 는 이제 **동시에 도는 볼륨 수의 상한**이다. 볼륨이
    # 그보다 많으면 앞에서부터 그만큼만 동시에 가고, 나머지는 그 묶음 안에서
    # 순서를 기다린다.
    groups = group_accounts_by_volume(accounts)
    run_parallel = parallel_accounts > 1 and len(groups) > 1
    if run_parallel and parallel_accounts < len(groups):
        # 상한을 넘는 묶음은 앞쪽 묶음에 이어 붙인다 - 버리지 않는다.
        merged = groups[:parallel_accounts]
        for index, extra in enumerate(groups[parallel_accounts:]):
            merged[index % parallel_accounts].extend(extra)
        groups = merged
    # 기록에 남기는 것은 **설정값이 아니라 실제로 동시에 돈 수**다. 둘은
    # 볼륨 묶음이 생기면서 갈라졌다 - `--parallel 4` 로 시작해도 계정이 전부
    # 한 볼륨에 있으면 실제로는 하나씩 돈다. 이 값은 리소스 시계열을 읽는
    # 기준이라, 설정값을 남기면 "동시 4일 때 부하가 이랬다"고 잘못 읽는다.
    effective_parallel = len(groups) if run_parallel else 1

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
        scan_store.record_parallelism(conn, run_id, effective_parallel, weekend_night)
        # 기준값은 반드시 **스캔을 시작하기 전에** 잡는다. 이 표본이 없으면
        # 나중에 "스캔 때문에 튄 것"과 "원래 그랬던 것"을 구분할 수 없다.
        recorder.baseline(warmup_seconds=baseline_warmup_seconds)
        recorder.start()

        if bypass_window:
            # 낮에 손으로 시작한 스캔은 시간창을 무시하고 돈다. 그런데
            # **야간 창이 열리면 물러나야 한다** - 안 그러면 22:00 에 뜬 cron 이
            # 잠금을 못 잡고 그날 밤이 통째로 날아간다. 사람이 낮에 눌러 둔 것
            # 하나가 밤 전체를 먹는 셈이다.
            #
            # 물러나도 잃는 것은 없다. 진행한 체크포인트는 이미 저장돼 있고,
            # 곧이어 뜨는 야간 실행이 거기서 이어받는다.
            night_start = settings.detail_scan_window_start_hour
            night_end = settings.detail_scan_window_end_hour
            if scan_window.is_within_window(local_now, night_start, night_end):
                # 야간 창 **안에서** 시작한 수동 실행은 양보할 상대가 없다 -
                # 이것이 곧 그날 밤의 실행이다. 여기서 물러나면 눌러도 아무
                # 일이 일어나지 않는 것처럼 보인다.
                deadline_reached = lambda: False
            else:
                deadline_reached = lambda: scan_window.is_within_window(
                    clock(), night_start, night_end
                )
        else:
            window_end = scan_window.next_window_end(local_now, settings.detail_scan_window_end_hour)
            deadline_reached = lambda: clock() >= window_end

        should_stop = lambda: scan_lock.is_stop_requested(data_dir, run_id)

        if run_parallel:
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
                groups=groups,
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
    #
    # **시작 시각이 아니라 끝난 시각을 넘긴다.** 야간 스캔은 자정을 넘어가므로
    # 둘은 다른 날이다. 시작 시각(목요일 22:00)으로 보고서 날짜를 매기면 그
    # 결과가 목요일자 파일에 들어가는데, 정작 그것을 읽는 사람은 금요일 아침에
    # 금요일자 보고서를 연다 - 리소스 수치가 통째로 안 보인다.
    # 알림도 마찬가지다: 06:00에 나가는 알림에 22:00 시각을 찍으면 8시간 묵은
    # 것으로 보이고 cooldown 계산도 그만큼 어긋난다.
    # (주말 밤 판정이 '끝나는 아침'을 보는 것과 같은 이유다.)
    finished_at = clock()
    _notify_growth(data_dir, config, outcomes, finished_at)
    _prune_orphan_search_indexes(data_dir, config)
    _generate_reports(data_dir, config, finished_at)
    return RunSummary(
        started=True,
        status=status,
        run_id=run_id,
        accounts=outcomes,
        parallel_accounts=effective_parallel,
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
    # 지금까지 실제로 잰 용량 (KB). 진행 중인 세대 기준이라 스캔이 도는 동안
    # 계속 커진다 - "얼마나 찾았나"에 답하는 값이다. 아직 하나도 못 쟀으면
    # None (0과 '모름'은 다르다).
    measured_kb: Optional[int] = None
    # 남은 체크포인트를 다 도는 데 걸릴 **대략의** 시간(초). 이 실행이 실제로
    # 낸 속도의 중앙값 x 남은 개수다. 표본이 모자라면 None
    # (`scan_store.estimate_remaining_seconds` 참고).
    eta_seconds: Optional[float] = None


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
                    # 진행 중인 세대로 본다. 완료 세대로 보면 스캔이 도는
                    # 동안에는 어제 값이 그대로 떠 있어 "지금 얼마나 찾았나"에
                    # 답하지 못한다.
                    measured_kb=scan_store.measured_total_kb(
                        conn, account.account_id, state.working_generation
                    ),
                    eta_seconds=scan_store.estimate_remaining_seconds(
                        conn,
                        account.account_id,
                        scan_store.BASELINE,
                        state.working_generation,
                        pending_baseline_count,
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
