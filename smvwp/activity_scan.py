"""`find -newermt` 기반 변경 파일 활동 스캔 - 기준선(du)과 같은 방식의
디렉터리 체크포인트 큐로 재개 가능하게 만든다.

DESIGN.md 2부의 요구: "activity cursor는 한 번에 다 끝내지 않아도 되고,
디렉터리 큐 체크포인트로 재개 가능해야 한다." 즉:
- 한 pass(이번에 볼 기준 시각 이후 변경분 전체 훑기)는 여러 밤에 걸쳐 나눠
  끝나도 된다 - 디렉터리별 체크포인트가 그 진행 상황을 기억한다.
- 커서(다음 pass의 기준 시각)는 그 pass가 "완전히" 끝난 다음에만
  전진한다 - 중간에 끊기면 다음 실행이 같은 기준 시각으로 이어서 훑는다.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import List, Optional

from . import detail_scan, procio, scan_store
from .detail_scan import build_priority_prefix, list_immediate_subdirs


class ActivityScanError(Exception):
    pass


@dataclass
class FindOutcome:
    ok: bool
    changed_count: Optional[int] = None
    timed_out: bool = False
    error_message: Optional[str] = None


# 출력이 무한정 커지는 것을 막기 위한 상한 - 개수만 필요하므로 이 이상은
# 세지 않고 "그 이상"으로만 표시한다 (숫자를 지어내지 않되, 무제한 메모리
# 사용도 피한다).
MAX_COUNTED_FILES = 5000


def find_command(path: str, since_iso: str) -> List[str]:
    """변경 파일을 세는 `find` argv.

    스냅샷 디렉터리는 `-prune`으로 아예 내려가지 않는다. 스냅샷 안의 파일은
    과거 시점의 사본이라 "최근 변경"으로 셀 대상이 아니고, 세대만큼 중복으로
    잡혀 변경 건수를 크게 부풀린다 (`detail_scan.SNAPSHOT_DIR_NAMES` 참고).

    `-prune`은 그 디렉터리로 **내려가지 않는다**는 뜻이라, 안쪽을 훑고 버리는
    것이 아니라 처음부터 읽지 않는다 - 부하도 그만큼 준다."""

    names: List[str] = []
    for index, name in enumerate(sorted(detail_scan.SNAPSHOT_DIR_NAMES)):
        if index:
            names.append("-o")
        names += ["-name", name]

    return (
        ["find", path, "("]
        + names
        + [")", "-prune", "-o", "-newermt", since_iso, "-type", "f", "-print"]
    )


def run_find_changed(path: str, since_iso: str, timeout_seconds: int) -> FindOutcome:
    """`find`로 변경된 파일 개수를 센다 (스냅샷 디렉터리는 제외)."""

    command = build_priority_prefix() + find_command(path, since_iso)
    try:
        # find는 경로를 그대로 출력한다 - UTF-8을 명시해 비ASCII 경로에서
        # 디코딩이 깨지지 않게 한다 (smvwp.procio 참고).
        proc = procio.run_utf8(command, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return FindOutcome(ok=False, timed_out=True)
    except FileNotFoundError:
        return FindOutcome(ok=False, error_message="find 명령을 찾을 수 없습니다")

    # find는 권한 오류가 있는 하위 디렉터리를 만나도 나머지는 계속 진행하고
    # exit code만 0이 아니게 남기는 경우가 흔하다 - stdout이 있으면 부분
    # 결과라도 신뢰하고 쓴다 (완전히 실패한 것과 구분).
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if proc.returncode != 0 and not lines:
        message = (proc.stderr or "").strip() or f"find exit={proc.returncode}"
        return FindOutcome(ok=False, error_message=message)

    count = min(len(lines), MAX_COUNTED_FILES)
    return FindOutcome(ok=True, changed_count=count)


def process_one_checkpoint(conn, checkpoint, since_iso: str, timeout_seconds: int) -> str:
    outcome = run_find_changed(checkpoint["path"], since_iso, timeout_seconds)
    if outcome.ok:
        scan_store.mark_done(conn, checkpoint["id"], changed_count=outcome.changed_count)
        return scan_store.STATUS_DONE

    if outcome.timed_out:
        children = list_immediate_subdirs(checkpoint["path"])
        if children:
            scan_store.mark_split(conn, checkpoint["id"])
            scan_store.insert_children(conn, checkpoint, children)
            return scan_store.STATUS_SPLIT
        scan_store.mark_error(
            conn, checkpoint["id"], f"{timeout_seconds}초 내에 끝나지 않았고 하위 디렉터리도 없습니다"
        )
        return scan_store.STATUS_ERROR

    scan_store.mark_error(conn, checkpoint["id"], outcome.error_message or "알 수 없는 오류")
    return scan_store.STATUS_ERROR


def total_changed(conn, account_id: str, pass_no: int) -> int:
    rows = conn.execute(
        "SELECT changed_count FROM scan_checkpoints "
        "WHERE account_id = ? AND kind = 'activity' AND generation = ? AND status = 'done'",
        (account_id, pass_no),
    ).fetchall()
    return sum(row["changed_count"] or 0 for row in rows)
