"""스캔 탭이 읽는 것 - 계정마다의 스캔 상태와, 야간 스캔이 남겨 두는 요약.

`nightly_scan.py` 가 실행기(밤에 도는 것)와 읽기 모델(창이 보는 것)을 한 파일에 담고
2,000줄에 이르렀다. 읽는 쪽을 여기로 옮겼다 (2026-11). 실행기는 끝날 때 `save_summaries`
를 부르고, 창은 `get_status_snapshot` 을 부른다 - 둘 다 `nightly_scan` 의 이름으로도
그대로 불린다 (예전 호출이 깨지지 않게 거기서 다시 내보낸다).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import (
    config as config_module,
    scan_lock,
    scan_store,
    scan_window,
    sqlite_db,
)

logger = logging.getLogger(__name__)


# 남겨 둔 스캔 탭 요약의 형식. `_settled_parts` 가 내는 것이나 그 규칙이 바뀌면 올린다 -
# 옛 것은 쓰지 않고 창이 스스로 읽는다. 깜빡해도 세대가 바뀌는 다음 밤에 새로 남는다.
# 3: 증가 경로를 증감의 크기 순으로(줄어든 곳도), 지난번 나눠 쟀거나 못 잰 경로를 가림
#    (`previous_note`), 변화가 몰린 곳(`focus`), 큰 파일의 '견줬나'.
SUMMARY_FORMAT = 7   # 2: 측정 기간·디렉터리 수·잰 합계·디렉터리당 시간을 보탰다
                     # 3: 용량 변화 줄의 '가장 많이 변한 곳'
                     # 4: 계정 증감을 두 바퀴 모두 잰 곳끼리 (`comparable_change`)
                     # 5: 크고 오래 안 바뀐 폴더 (`cold_folders`)
                     # 6: 계정 바로 아래 폴더의 장기 이력 (`folder_trends`)
                     # 7: 사람(파일 주인)별 사용량 (`owners`)


def _summary_key(account_id: str, generation: int, top_n: int, max_depth: int) -> str:
    """스캔 탭 요약을 정하는 것 전부 - 완료 세대와, 몇 개·얼마나 깊이 보여 주나."""

    return json.dumps([SUMMARY_FORMAT, account_id, generation, top_n, max_depth])


def outdated_summaries(data_dir: Path, config: config_module.AppConfig) -> int:
    """남겨 둔 스캔 탭 요약이 지금 판·설정·세대와 맞지 않는 계정 수 (`smvwp_cli.py status`).

    0 이 아니면 창이 그 계정들을 열 때마다 스스로 읽는다 - 업데이트 뒤 `prepare` 를 안
    돌렸거나 야간 스캔이 요약을 못 남겼다."""

    settings = config.settings
    with scan_store.session(data_dir) as conn:
        states = scan_store.account_states(conn)
        saved = {
            row["account_id"]: row["input_key"]
            for row in conn.execute("SELECT account_id, input_key FROM scan_summaries")
        }
    count = 0
    for account in config.accounts:
        state = states.get(account.account_id)
        generation = state.last_completed_generation if state else None
        if generation and saved.get(account.account_id) != _summary_key(
                account.account_id, generation, settings.detail_scan_top_n,
                settings.growth_list_max_depth):
            count += 1
    return count


def save_summaries(data_dir: Path, config: config_module.AppConfig) -> int:
    """계정마다 완료된 세대의 스캔 탭 요약을 남긴다 - 창이 열 때마다 읽지 않게.

    세대가 그대로인 계정은 건너뛴다 (이미 남겼다). 설정에서 빠진 계정의 줄은 지운다.
    새로 남긴 계정 수를 준다. 야간 스캔이 끝날 때와 `smvwp_cli.py prepare` 가 부른다."""

    settings = config.settings
    top_n, max_depth = settings.detail_scan_top_n, settings.growth_list_max_depth
    with scan_store.session(data_dir) as conn:
        states = scan_store.account_states(conn)
        saved = {
            row["account_id"]: row["input_key"]
            for row in conn.execute("SELECT account_id, input_key FROM scan_summaries")
        }
        rows = []
        for account in config.accounts:
            state = states.get(account.account_id)
            current_gen = state.last_completed_generation if state else None
            if not current_gen:
                continue
            key = _summary_key(account.account_id, current_gen, top_n, max_depth)
            if saved.get(account.account_id) == key:
                continue
            parts = _settled_parts(conn, state, current_gen, current_gen - 1, top_n, max_depth,
                                   focus=True, cold_min_kb=settings.cleanup_min_size_kb,
                                   trend_days=settings.folder_history_days)
            rows.append((account.account_id, key, json.dumps(_plain(parts), ensure_ascii=False),
                         scan_store.utc_now_iso()))
        ids = [account.account_id for account in config.accounts]
        conn.execute(
            f"DELETE FROM scan_summaries WHERE account_id NOT IN ({','.join('?' * len(ids))})",
            ids,
        )
        conn.executemany("INSERT OR REPLACE INTO scan_summaries VALUES (?, ?, ?, ?)", rows)
        conn.commit()
    return len(rows)


def _stored_summary(conn, account_id: str, key: str) -> Optional[dict]:
    """남겨 둔 요약이 이 열쇠의 것이면 `_settled_parts` 와 같은 모양으로, 아니면 None.

    줄들(상위·증가 경로)은 sqlite3.Row 대신 dict 로 온다 - 화면과 보고서는 이름으로만
    꺼내 쓴다 (`row["path"]`, `row.keys()`)."""

    row = conn.execute(
        "SELECT input_key, payload FROM scan_summaries WHERE account_id = ?", (account_id,)
    ).fetchone()
    if row is None or row["input_key"] != key:
        return None
    try:
        parts = json.loads(row["payload"])
        parts["large_files"] = [tuple(item) for item in parts["large_files"]]
        return parts
    except (ValueError, TypeError, KeyError):
        return None


def _plain(parts: dict) -> dict:
    """`_settled_parts` 의 값을 JSON 으로 남길 수 있게."""

    return {
        **parts,
        "top_paths": [dict(row) for row in parts["top_paths"]],
        "growth": [dict(row) for row in parts["growth"]],
        "large_files": [list(item) for item in parts["large_files"]],
        "partial_paths": list(parts["partial_paths"]),
    }


@dataclass
class AccountScanSnapshot:
    account_id: str
    account_name: str
    last_completed_generation: Optional[int]
    top_paths: list  # List[sqlite3.Row] (path, size_kb)
    growth: list  # List[sqlite3.Row] (path, current_kb, previous_kb) - 이전 세대가 없으면 빈 리스트
    pending_baseline_count: int
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
    # 이 계정에서 가장 큰 파일들과 지난 세대 대비 변화
    # (`large_files.build` 에 그대로 넣을 수 있는 모양).
    large_files: List[tuple] = field(default_factory=list)
    # 크고 거의 통째로 오래 안 바뀐 폴더 `[{path, size_kb, cold_kb, newest_mtime}]`
    # (`scan_store.cold_folders`). 야간 스캔이 요약을 남길 때만 채운다.
    cold_folders: List[dict] = field(default_factory=list)
    # 사람(파일 주인)별 사용량 `[{uid, name, size_kb, file_count, previous_kb}]`.
    owners: List[dict] = field(default_factory=list)
    # 계정 바로 아래 폴더들의 장기 이력 `{폴더: [(끝낸 때, KB), ...]}`. 요약에만 있다.
    folder_trends: dict = field(default_factory=dict)
    # 지금까지 실제로 잰 용량 (KB). 진행 중인 세대 기준이라 스캔이 도는 동안
    # 계속 커진다 - "얼마나 찾았나"에 답하는 값이다. 아직 하나도 못 쟀으면
    # None (0과 '모름'은 다르다).
    measured_kb: Optional[int] = None
    # 직전 세대의 같은 값 (끝난 바퀴의 합계).
    previous_measured_kb: Optional[int] = None
    # 끝난 두 바퀴 사이의 계정 증감 - 어느 바퀴에서든 못 잰 곳은 양쪽에서 뺀 것
    # (`scan_store.comparable_change`). 두 합계를 그냥 빼면 지난번에 못 잰 폴더가
    # 통째로 '늘어난 양' 이 된다. 견줄 것이 없으면 None. `delta_excluded` 는 뺀 자리,
    # `compared_kb` 는 뺀 뒤의 두 합계 `(지난번, 이번)` - 화면의 전후 막대가 쓴다.
    measured_delta_kb: Optional[int] = None
    delta_excluded: List[str] = field(default_factory=list)
    compared_kb: Optional[Tuple[int, int]] = None
    # 남은 체크포인트를 다 도는 데 걸릴 **대략의** 시간(초). 이 실행이 실제로
    # 낸 속도의 중앙값 x 남은 개수다. 표본이 모자라면 None
    # (`scan_store.estimate_remaining_seconds` 참고).
    eta_seconds: Optional[float] = None
    # 계정 경로를 못 봐 건너뛴 까닭·세부·때 (`UNAVAILABLE_*`). 없으면 None.
    unavailable_reason: Optional[str] = None
    unavailable_detail: str = ""
    unavailable_at: Optional[str] = None
    # 상세 스캔 현황(`scan_overview`)이 쓰는 것. `current_*` 는 끝난 세대의 것이다
    # (`_generation_facts`). `working_started_at` 은 하는 중인 세대를 처음 잰 때.
    last_completed_at: Optional[str] = None
    current_dirs: int = 0
    current_started_at: Optional[str] = None
    current_finished_at: Optional[str] = None
    current_measured_kb: Optional[int] = None
    seconds_per_dir: Optional[float] = None
    working_started_at: Optional[str] = None
    # 마지막 세대를 끝낼 때 표본이 말하던 사용량 (KB). 화면이 지금 값과 견줘 오늘 밤
    # 순서를 예고한다 (`scan_order`). 모르면 None.
    used_kb_at_scan: Optional[int] = None


@dataclass
class StatusSnapshot:
    is_running: bool
    window_description: str
    latest_run: Optional[dict]
    accounts: List[AccountScanSnapshot]
    # '모든 계정 한 바퀴' 를 시작한 때 (UTC ISO). 야간 스캔이 남긴다 - 아직 없으면
    # None 이고 `scan_overview` 가 어림한다.
    pass_started_at: Optional[str] = None
    # 최근 실행들 (최신순) - `{run_id, triggered_by, started_at, ended_at, status,
    # dirs_done}`. 밤마다 한 일을 보여 준다.
    recent_runs: List[dict] = field(default_factory=list)


def _settled_parts(conn, state, current_gen, previous_gen, top_n, max_depth,
                   focus: bool = False, cold_min_kb: Optional[int] = None,
                   trend_days: int = 730) -> dict:
    """완료된 세대에만 매인 값들. 세대가 바뀌기 전에는 달라지지 않는다.

    두 스캔의 날짜는 세대를 끝낼 때 남긴 시각을 먼저 쓴다. 결과 표에서 찾으면
    (`generation_completed_at`) 세대의 행을 전부 읽는다 - 계정마다 두 번, 창을
    열 때마다. 남긴 시각이 없는 예전 DB 에서만 결과 표로 간다.

    증가 경로는 **증감의 크기 순**이다 - 증가 순으로 자르면 크게 정리한 곳이 목록에서
    빠진다. `focus` 면 줄마다 변화가 그 안의 어디에 몰렸는지(`scan_store.change_focus`)
    를 붙인다. 그 폴더 아래 기록을 두 세대 모두 읽으므로 야간 스캔이 요약을 남길 때만
    한다 - 창이 스스로 읽을 때(요약이 없을 때)는 붙이지 않는다."""

    account_id = state.account_id
    growth = (
        scan_store.growth_delta(
            conn, account_id, current_gen, previous_gen, top_n, max_depth=max_depth,
            order=scan_store.ORDER_CHANGE,
        )
        if current_gen and previous_gen else []
    )
    if focus:
        for row in growth:
            if row["previous_kb"] is not None and row["previous_note"] != scan_store.PREVIOUS_UNMEASURED:
                row["focus"] = scan_store.change_focus(
                    conn, account_id, current_gen, previous_gen, row["path"]
                )

    facts = _generation_facts(conn, account_id, current_gen)
    previous_total = (
        scan_store.measured_total_kb(conn, account_id, previous_gen) if previous_gen else None
    )
    compared = (
        scan_store.comparable_change(
            conn, account_id, current_gen, previous_gen,
            current_kb=facts["current_measured_kb"], previous_kb=previous_total,
        )
        if current_gen and previous_gen and facts["current_measured_kb"] is not None
        and previous_total is not None else None
    )
    return {
        "top_paths": (
            scan_store.top_paths(conn, account_id, current_gen, top_n, max_depth=max_depth)
            if current_gen else []
        ),
        "growth": growth,
        "previous_measured_kb": previous_total,
        "measured_delta_kb": compared["delta_kb"] if compared else None,
        "delta_excluded": compared["excluded"] if compared else [],
        "compared_kb": [compared["previous_kb"], compared["current_kb"]] if compared else None,
        "current_scan_at": (
            (state.last_completed_at
             or scan_store.generation_completed_at(conn, account_id, current_gen))
            if current_gen else None
        ),
        "previous_scan_at": (
            (state.previous_completed_at
             or scan_store.generation_completed_at(conn, account_id, previous_gen))
            if previous_gen else None
        ),
        "large_files": (
            scan_store.large_file_changes(conn, account_id, current_gen, previous_gen)
            if current_gen else []
        ),
        # 크고 오래 안 바뀐 폴더. 세대의 줄을 훑으므로 요약을 남길 때만 (`focus` 와 같은
        # 까닭) - 창이 스스로 읽을 때는 비워 둔다.
        "cold_folders": (
            scan_store.cold_folders(conn, account_id, current_gen, cold_min_kb)
            if focus and current_gen and cold_min_kb else []
        ),
        # 사람별 사용량 (이번 세대, 지난 세대 값을 곁들여). 사람 수만큼 몇 줄이라 늘 읽는다.
        "owners": _owners_with_change(conn, account_id, current_gen, previous_gen),
        # 계정 바로 아래 폴더들의 장기 이력 (용량 변화 표의 '추세' 칸). 요약을 남길 때만.
        "folder_trends": (
            scan_store.folder_history(
                conn, account_id,
                (datetime.now(timezone.utc) - timedelta(days=trend_days)).isoformat(),
            )
            if focus and current_gen else {}
        ),
        "partial_paths": (
            scan_store.partial_paths(conn, account_id, current_gen) if current_gen else []
        ),
        # 상세 스캔 현황(`scan_overview`)이 쓰는 것: 그 세대를 언제부터 언제까지
        # 쟀나(여러 밤에 걸치면 디렉터리마다 잰 날이 다르다), 디렉터리 몇 개였나,
        # 다 더하면 얼마였나, 디렉터리 하나에 얼마나 걸렸나(완료 일정 어림).
        **facts,
    }


# 화면에 싣는 사람 수 - 그 아래는 '그 밖 N명' 으로 묶는다.
OWNERS_SHOWN = 15


def _owners_with_change(conn, account_id: str, current_gen, previous_gen) -> List[dict]:
    """`[{uid, name, size_kb, file_count, previous_kb}]` - 큰 것부터, `OWNERS_SHOWN` 명."""

    if not current_gen:
        return []
    rows = scan_store.owner_totals(conn, account_id, current_gen)
    previous = {
        row["uid"]: row["size_kb"]
        for row in (scan_store.owner_totals(conn, account_id, previous_gen) if previous_gen else [])
    }
    for row in rows:
        row["previous_kb"] = previous.get(row["uid"]) if previous_gen else None
    return rows[:OWNERS_SHOWN]


def _generation_facts(conn, account_id: str, generation) -> dict:
    if not generation:
        return {"current_dirs": 0, "current_started_at": None, "current_finished_at": None,
                "current_measured_kb": None, "seconds_per_dir": None}
    dirs, started, finished = scan_store.generation_span(
        conn, account_id, scan_store.BASELINE, generation
    )
    return {
        "current_dirs": dirs,
        "current_started_at": started,
        "current_finished_at": finished,
        "current_measured_kb": scan_store.measured_total_kb(conn, account_id, generation),
        "seconds_per_dir": scan_store.seconds_per_checkpoint(
            conn, account_id, scan_store.BASELINE, generation
        ),
    }


def get_status_snapshot(
    data_dir: Path,
    config: config_module.AppConfig,
    clock: Optional[Callable[[], datetime]] = None,
    top_n: Optional[int] = None,
    cache: Optional[dict] = None,
    on_account: Optional[Callable[[int, int], None]] = None,
) -> StatusSnapshot:
    """GUI가 스캔을 직접 돌리지 않고도 현재 상태를 읽기만 해서 보여줄 때
    쓴다. 여기서는 아무 것도 쓰지 않는다 (읽기 전용 조회).

    `cache` 를 주면 **완료된 세대에만 매인 값**(상위 경로, 증감, 큰 파일 변화, 이전
    크기, 완료 시각, 덜 센 경로)을 세대가 바뀔 때까지 기억한다. 창은 스캔 중
    5초마다 이것을 부르는데, 이 값들을 매번 NFS 에서 다시 읽으면 그 읽기가 스캔과
    수집의 커밋을 붙잡아 실기에서 "database is locked" 가 났다."""

    clock = clock or datetime.now
    top_n = top_n or config.settings.detail_scan_top_n
    now = clock()

    # 한 번의 읽기로 묶는다 (`sqlite_db.read_snapshot`): 계정마다 문이 여럿이라 스캔
    # 중 5초마다 120개 넘는 잠금 왕복이었고, 계정 사이에 스캔이 쓴 것이 섞였다.
    # 그래서 여기서는 쓰지 않는다 - 상태는 행을 만들지 않는 쪽으로 한 번에 읽는다.
    with scan_store.session(data_dir) as conn, sqlite_db.read_snapshot(conn):
        latest_run_row = scan_store.latest_run(conn)
        latest_run = dict(latest_run_row) if latest_run_row is not None else None
        states = scan_store.account_states(conn)

        accounts_snapshot = []
        for index, account in enumerate(config.accounts, start=1):
            # 화면이 첫 읽기 동안 "계정 3/12" 로 보여 준다 (작업 스레드에서 불린다).
            if on_account is not None:
                on_account(index, len(config.accounts))
            state = states.get(account.account_id) or scan_store.AccountScanState(
                account_id=account.account_id
            )
            current_gen = state.last_completed_generation
            previous_gen = current_gen - 1 if current_gen else None
            key = (current_gen, top_n, config.settings.growth_list_max_depth)
            remembered = cache.get(account.account_id) if cache is not None else None
            if remembered is None or remembered[0] != key:
                # 기억이 없으면(창을 처음 열었거나 세대가 바뀌었으면) 야간 스캔이 남긴
                # 요약부터 본다 - 결과 표를 줄마다 찾는 것보다 한 줄 읽기가 훨씬 싸다.
                settled = _stored_summary(
                    conn, account.account_id,
                    _summary_key(account.account_id, current_gen, top_n,
                                 config.settings.growth_list_max_depth),
                ) if current_gen else None
                if settled is None:
                    settled = _settled_parts(
                        conn, state, current_gen, previous_gen, top_n,
                        config.settings.growth_list_max_depth,
                    )
                remembered = (key, settled)
                if cache is not None:
                    cache[account.account_id] = remembered
            settled = remembered[1]
            pending_baseline_count = conn.execute(
                "SELECT COUNT(*) FROM scan_checkpoints WHERE account_id = ? AND kind = 'baseline' "
                "AND generation = ? AND status = 'pending'",
                (account.account_id, state.working_generation),
            ).fetchone()[0]
            baseline_counts = scan_store.checkpoint_progress(
                conn, account.account_id, scan_store.BASELINE, state.working_generation
            )
            accounts_snapshot.append(
                AccountScanSnapshot(
                    account_id=account.account_id,
                    account_name=account.name,
                    last_completed_generation=current_gen,
                    unavailable_reason=state.unavailable_reason,
                    unavailable_detail=state.unavailable_detail,
                    unavailable_at=state.unavailable_at,
                    previous_measured_kb=settled["previous_measured_kb"],
                    measured_delta_kb=settled.get("measured_delta_kb"),
                    delta_excluded=list(settled.get("delta_excluded") or []),
                    compared_kb=(
                        tuple(settled["compared_kb"]) if settled.get("compared_kb") else None
                    ),
                    current_scan_at=settled["current_scan_at"],
                    previous_scan_at=settled["previous_scan_at"],
                    top_paths=settled["top_paths"],
                    growth=settled["growth"],
                    large_files=settled["large_files"],
                    cold_folders=list(settled.get("cold_folders") or []),
                    owners=list(settled.get("owners") or []),
                    folder_trends={
                        path: [tuple(point) for point in points]
                        for path, points in (settled.get("folder_trends") or {}).items()
                    },
                    pending_baseline_count=pending_baseline_count,
                    baseline_done=baseline_counts["done"] + baseline_counts["error"],
                    baseline_total=baseline_counts["total"],
                    partial_paths=settled["partial_paths"],
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
                    last_completed_at=state.last_completed_at,
                    used_kb_at_scan=state.used_kb_at_scan,
                    current_dirs=settled.get("current_dirs", 0),
                    current_started_at=settled.get("current_started_at"),
                    current_finished_at=settled.get("current_finished_at"),
                    current_measured_kb=settled.get("current_measured_kb"),
                    seconds_per_dir=settled.get("seconds_per_dir"),
                    # 하는 중인 세대를 처음 잰 때 - 하다 만 계정만 읽는다 (한둘).
                    working_started_at=(
                        scan_store.generation_span(
                            conn, account.account_id, scan_store.BASELINE,
                            state.working_generation,
                        )[1]
                        if baseline_counts["total"] else None
                    ),
                )
            )
        pass_started_at = scan_store.get_meta(conn, PASS_STARTED_KEY)
        recent_runs = _recent_runs(conn, cache)

    return StatusSnapshot(
        is_running=scan_lock.is_locked(data_dir),
        window_description=scan_window.describe(
            now, config.settings.detail_scan_window_start_hour, config.settings.detail_scan_window_end_hour
        ),
        latest_run=latest_run,
        accounts=accounts_snapshot,
        pass_started_at=pass_started_at,
        recent_runs=recent_runs,
    )


# 스캔 DB `scan_meta` 에서 '모든 계정 한 바퀴' 를 시작한 때를 담는 이름.
PASS_STARTED_KEY = "pass_started_at"

# 밤마다 한 일을 몇 밤치 보여 주나.
RECENT_RUNS = 7


def _recent_runs(conn, cache: Optional[dict]) -> List[dict]:
    """최근 실행들과 그 실행이 재 낸 디렉터리 수.

    끝난 실행의 수는 다시 바뀌지 않으므로 `cache` 에 기억한다 - 디렉터리 수는
    체크포인트 표를 훑어야 해서, 창이 30초(스캔 중 5초)마다 일곱 번씩 하면 안 된다."""

    runs = []
    for row in scan_store.last_runs(conn, RECENT_RUNS):
        run = {key: row[key] for key in ("run_id", "triggered_by", "started_at", "ended_at", "status")}
        key = ("run", run["run_id"])
        remembered = cache.get(key) if cache is not None and run["ended_at"] else None
        if remembered is None:
            until = run["ended_at"] or datetime.now(timezone.utc).isoformat()
            remembered = scan_store.checkpoints_done_between(conn, run["started_at"], until)
            if cache is not None and run["ended_at"]:
                cache[key] = remembered
        run["dirs_done"] = remembered
        runs.append(run)
    return runs
