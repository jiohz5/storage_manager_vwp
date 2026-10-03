"""기준선 결과 (`baseline_results`, `baseline_large_files`).

스캔이 실제로 걸어 들어간 디렉터리의 크기와, 그 밤의 큰 파일. 증가 경로·트리·
새로 생긴 과제 판정은 전부 이 결과를 세대끼리 견준다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import List, Optional

from .db import utc_now_iso


def generation_completed_at(
    conn: sqlite3.Connection, account_id: str, generation: int
) -> Optional[str]:
    """그 스캔이 언제 끝났는지 (ISO 문자열). 결과가 없으면 None.

    화면과 보고서는 스캔을 **날짜로** 가리킨다. "3번째 스캔"은 내부 번호라
    사용자에게 아무 기준점이 못 되지만, "260819 스캔"은 그날 무슨 일이
    있었는지와 바로 연결된다."""

    row = conn.execute(
        "SELECT MAX(completed_at) AS at FROM baseline_results "
        "WHERE account_id = ? AND generation = ?",
        (account_id, generation),
    ).fetchone()
    return row["at"] if row and row["at"] else None


def save_tree_entries(
    conn: sqlite3.Connection,
    account_id: str,
    generation: int,
    entries: List["tuple"],
    root_path: str,
) -> int:
    """`du -k`가 준 (경로, KB) 목록을 기준선 결과로 저장한다.

    깊이는 **계정 루트가 아니라 이 체크포인트 루트 기준**이다. 시간 초과로
    쪼개진 서브트리는 자기 루트에서 다시 0부터 세는데, 그래야 "얕은 것만
    보여 달라"는 요구가 어느 서브트리에서든 같은 의미가 된다.

    같은 경로가 다시 들어오면 덮어쓴다 (`INSERT OR REPLACE`) - 쪼개기 뒤
    재측정이 겹칠 수 있고, 나중 값이 항상 더 정확하다."""

    if not entries:
        return 0

    now = utc_now_iso()
    root = root_path.rstrip("/")
    rows = []
    for path, size_kb in entries:
        normalized = path.rstrip("/")
        if normalized == root:
            depth = 0
        else:
            relative = normalized[len(root):].lstrip("/")
            depth = relative.count("/") + 1 if relative else 0
        rows.append((account_id, generation, normalized, size_kb, now, depth))

    conn.executemany(
        "INSERT OR REPLACE INTO baseline_results "
        "(account_id, generation, path, size_kb, completed_at, depth) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def tree_entries(
    conn: sqlite3.Connection, account_id: str, generation: int
) -> List[sqlite3.Row]:
    """트리 화면용 - 이 스캔이 남긴 모든 디렉터리 (경로순)."""

    return conn.execute(
        "SELECT path, size_kb, depth FROM baseline_results "
        "WHERE account_id = ? AND generation = ? ORDER BY path",
        (account_id, generation),
    ).fetchall()


def top_paths(
    conn: sqlite3.Connection,
    account_id: str,
    generation: int,
    limit: int = 15,
    max_depth: int = 1,
) -> List[sqlite3.Row]:
    """큰 경로 목록.

    깊이를 제한하는 이유: `du -k`는 부모와 자식을 함께 주므로, 거르지 않으면
    300GB짜리 부모와 그 안의 280GB짜리 자식이 나란히 나온다. 합계가 중복이라
    "무엇이 큰가"를 읽기 어렵다. 전체 트리는 트리 화면에서 본다."""

    return conn.execute(
        "SELECT * FROM baseline_results WHERE account_id = ? AND generation = ? "
        "AND COALESCE(depth, 1) <= ? ORDER BY size_kb DESC LIMIT ?",
        (account_id, generation, max_depth, limit),
    ).fetchall()


def growth_delta(conn: sqlite3.Connection, account_id: str, current_generation: int, previous_generation: int, limit: int = 15, max_depth: int = 1):
    """같은 경로(path) 기준으로 현재/이전 세대를 대조한다 (top N 목록 자체를
    비교하지 않음 - "어제 top N vs 오늘 top N"은 순위가 바뀌면 다른 항목처럼
    보이는 문제가 있었다). 증가량 기준 내림차순으로 최대 limit개 반환."""

    rows = conn.execute(
        """
        SELECT cur.path AS path, cur.size_kb AS current_kb, prev.size_kb AS previous_kb
        FROM baseline_results cur
        LEFT JOIN baseline_results prev
            ON prev.account_id = cur.account_id AND prev.generation = ? AND prev.path = cur.path
        WHERE cur.account_id = ? AND cur.generation = ? AND COALESCE(cur.depth, 1) <= ?
        ORDER BY (cur.size_kb - COALESCE(prev.size_kb, 0)) DESC
        LIMIT ?
        """,
        (previous_generation, account_id, current_generation, max_depth, limit),
    ).fetchall()
    return rows


def save_large_files(
    conn: sqlite3.Connection, account_id: str, generation: int, entries
) -> int:
    """순회가 붙잡은 `(KB, 경로)` 목록을 저장한다.

    체크포인트마다 불리므로 여러 번 들어온다. 같은 경로는 덮어쓴다 - 쪼개기
    뒤 재측정이 겹칠 수 있고 나중 값이 더 정확하다."""

    if not entries:
        return 0
    now = utc_now_iso()
    rows = [
        (account_id, generation, str(path), int(size_kb), now)
        for size_kb, path in entries
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO baseline_large_files "
        "(account_id, generation, path, size_kb, recorded_at) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def largest_files(
    conn: sqlite3.Connection, account_id: str, generation: int, limit: int = 20
):
    """이 세대에서 가장 큰 파일들 (큰 것부터)."""

    return conn.execute(
        "SELECT path, size_kb FROM baseline_large_files "
        "WHERE account_id = ? AND generation = ? ORDER BY size_kb DESC LIMIT ?",
        (account_id, generation, limit),
    ).fetchall()


def large_file_changes(
    conn: sqlite3.Connection, account_id: str, generation: int, previous_generation
):
    """이번 세대의 큰 파일들을 이전 세대와 견준다.

    `(경로, 지금 KB, 이전 KB 또는 None)` 을 큰 것부터. 이전 값이 None 이면
    **지난 밤에는 없던(또는 그때는 크지 않던) 파일**이다.

    두 경우를 굳이 안 가른다 - 상위 목록에 없었다는 것만 알 수 있고, 그것이
    "새로 생겼다"인지 "그때는 작았다"인지는 이 데이터로 판단할 수 없다.
    지어내지 않는 편이 맞다.
    """

    current = largest_files(conn, account_id, generation, limit=1000)
    if previous_generation is None:
        return [(row["path"], row["size_kb"], None) for row in current]
    previous = {
        row["path"]: row["size_kb"]
        for row in largest_files(conn, account_id, previous_generation, limit=1000)
    }
    return [
        (row["path"], row["size_kb"], previous.get(row["path"]))
        for row in current
    ]


def prune_old_generations(conn: sqlite3.Connection, account_id: str, keep_last: int = 2) -> int:
    """오래된 세대의 체크포인트/기준선 결과를 지운다 (DB 크기 예산 유지).

    최근 `keep_last`개의 "완료된" 세대만 baseline_results에 남기고, 그보다
    오래된 세대의 체크포인트/기준선 행을 모두 지운다. 진행 중인(아직 완료
    안 된) 세대는 절대 건드리지 않는다.
    """

    # 예전 '활동 스캔'이 남긴 체크포인트를 치운다. 그 기능은 없앴는데(변경 파일
    # 수를 세려고 트리를 한 번 더 완주했고, 그 숫자로 할 수 있는 일이 없었다)
    # 진행 중이던 행이 DB 에 남아 있을 수 있다.
    #
    # **아래 조기 반환보다 위에 둔다.** 지울 세대가 없으면 그대로 돌아가는데,
    # 그 뒤에 두면 계정이 세대를 여럿 쌓기 전까지 이 찌꺼기가 안 치워진다.
    conn.execute(
        "DELETE FROM scan_checkpoints WHERE account_id = ? AND kind = 'activity'",
        (account_id,),
    )
    conn.commit()

    completed = [
        row["generation"]
        for row in conn.execute(
            "SELECT DISTINCT generation FROM baseline_results WHERE account_id = ? ORDER BY generation DESC",
            (account_id,),
        ).fetchall()
    ]
    to_delete = completed[keep_last:]
    if not to_delete:
        return 0
    placeholders = ",".join("?" for _ in to_delete)
    cursor = conn.execute(
        f"DELETE FROM baseline_results WHERE account_id = ? AND generation IN ({placeholders})",
        (account_id, *to_delete),
    )
    conn.execute(
        f"DELETE FROM scan_checkpoints WHERE account_id = ? AND kind = 'baseline' AND generation IN ({placeholders})",
        (account_id, *to_delete),
    )
    # 큰 파일 목록도 같은 세대 기준으로 정리한다. 빠뜨리면 계정마다 세대마다
    # 50행씩 영원히 쌓인다.
    conn.execute(
        f"DELETE FROM baseline_large_files WHERE account_id = ? AND generation IN ({placeholders})",
        (account_id, *to_delete),
    )
    conn.commit()
    return cursor.rowcount


# -- 과제(run) 디렉터리 감지 ------------------------------------------------

@dataclass
class NewPaths:
    """이번 세대에만 있는 경로를 **확신할 수 있는 것과 아닌 것**으로 가른다.

    `confident` 만 "새로 생겼다" 고 말할 수 있다. `unverified` 는 직전 스캔이
    그 자리를 아예 안 들여다본 것이라 새것인지 옛것인지 알 수 없다."""

    confident: List[sqlite3.Row] = field(default_factory=list)
    unverified: List[sqlite3.Row] = field(default_factory=list)


def is_confidently_new(path: str, previous_paths, previous_parents) -> bool:
    """직전 세대가 **그 자리를 열어 봤는데 없었나**.

    ## 왜 단순 비교로는 안 되는가

    `baseline_results` 에 남는 경로는 "파일시스템에 있는 것" 이 아니라 **그
    밤에 스캔이 실제로 걸어 들어간 곳**이다. 그리고 그 범위는 밤마다 다르다:

    - 시간 초과로 쪼개진 서브트리는 자기 루트에서 깊이를 다시 0 부터 세므로,
      쪼개진 밤에만 더 깊은 곳까지 기록된다. 쪼개짐은 그날의 속도에 달렸다.
    - 권한 오류나 시간 초과로 통째로 못 잰 서브트리는 그 밤 기록이 없다.

    그래서 "이번엔 있고 지난번엔 없다" 만 보면 **옛날 과제가 새로 생긴 것으로
    올라온다.** 실제로 그렇게 보였다.

    ## 무엇을 근거로 삼는가

    직전 세대가 어떤 디렉터리의 **자식을 하나라도 기록했다면**, 그 디렉터리의
    내용을 열어 본 것이다. 그때 없었던 것은 그 뒤에 생긴 것이 맞다.

    후보에서 위로 올라가며 직전 세대가 열어 본 부모를 처음 만나는 지점을
    찾는다. 그 지점이 직전 세대에 없었으면 그 아래는 전부 새것이고, 있었으면
    (또는 끝까지 근거를 못 찾으면) 모르는 것이다.
    """

    current = (path or "").rstrip("/")
    while current:
        parent = current.rsplit("/", 1)[0] if "/" in current else ""
        if not parent or parent == current:
            return False
        if parent in previous_parents:
            return current not in previous_paths
        current = parent
    return False


def new_paths(
    conn: sqlite3.Connection,
    account_id: str,
    generation: int,
    previous_generation: int,
    like_pattern: Optional[str] = None,
) -> NewPaths:
    """직전 세대에는 없고 이번 세대에만 있는 경로들.

    **비교 대상 세대가 비어 있으면 빈 목록을 준다.** 첫 스캔이면 모든 경로가
    "새로 생긴 것"이 되는데, 그것을 그대로 보고하면 첫 보고서가 수천 줄로
    뒤덮이고 진짜 신규와 구분이 안 된다. 모를 때는 말하지 않는 쪽이 맞다.

    같은 이유로 **직전 스캔이 들여다보지 않은 자리**는 `unverified` 로 따로
    담는다 (`is_confidently_new` 참고). 조용히 버리지 않는 것은, 그 수가 많으면
    스캔 범위 자체가 밤마다 흔들린다는 뜻이라 사람이 알아야 하기 때문이다.

    `like_pattern`을 주면 SQL 단계에서 먼저 거른다. `_`는 LIKE에서 한 글자
    와일드카드라 반드시 ESCAPE와 함께 써야 한다 - 안 그러면 `_run_`이
    "아무 글자 + run + 아무 글자"가 되어 엉뚱한 디렉터리까지 걸린다.
    """

    if previous_generation < 0:
        return NewPaths()
    previous = [
        row["path"]
        for row in conn.execute(
            "SELECT path FROM baseline_results WHERE account_id = ? AND generation = ?",
            (account_id, previous_generation),
        ).fetchall()
    ]
    if not previous:
        return NewPaths()
    previous_paths = {path.rstrip("/") for path in previous}
    # 직전 세대가 **내용을 열어 본** 디렉터리들 = 기록된 경로들의 부모.
    previous_parents = {
        path.rsplit("/", 1)[0] for path in previous_paths if "/" in path
    }

    sql = (
        "SELECT path, size_kb, depth FROM baseline_results "
        "WHERE account_id = ? AND generation = ? "
    )
    params: List = [account_id, generation]
    if like_pattern:
        sql += "AND path LIKE ? ESCAPE '\\' "
        params.append(like_pattern)
    sql += (
        "AND path NOT IN ("
        "SELECT path FROM baseline_results WHERE account_id = ? AND generation = ?"
        ") ORDER BY path"
    )
    params += [account_id, previous_generation]

    found = NewPaths()
    for row in conn.execute(sql, params).fetchall():
        if is_confidently_new(row["path"], previous_paths, previous_parents):
            found.confident.append(row)
        else:
            found.unverified.append(row)
    return found


def generation_paths(
    conn: sqlite3.Connection, account_id: str, generation: int, under: Optional[str] = None
) -> List[str]:
    """이 세대가 남긴 경로 목록. `under`를 주면 그 아래만."""

    if under:
        prefix = under.rstrip("/") + "/"
        rows = conn.execute(
            "SELECT path FROM baseline_results "
            "WHERE account_id = ? AND generation = ? AND path LIKE ? ORDER BY path",
            (account_id, generation, prefix.replace("%", "") + "%"),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT path FROM baseline_results "
            "WHERE account_id = ? AND generation = ? ORDER BY path",
            (account_id, generation),
        ).fetchall()
    return [row["path"] for row in rows]


# -- 진행 현황용 집계 -------------------------------------------------------

def measured_total_kb(
    conn: sqlite3.Connection, account_id: str, generation: int
) -> Optional[int]:
    """이 세대에서 지금까지 **실제로 잰** 용량 합계 (KB).

    깊이 0만 더한다. `du -k`는 부모와 자식을 함께 주므로 전부 더하면 같은
    바이트를 여러 번 세게 된다 (`top_paths`가 깊이를 제한하는 것과 같은 이유).
    깊이 0은 체크포인트 루트 = 계정 바로 아래 최상위 디렉터리들이라, 이들의
    합이 곧 "지금까지 훑어서 찾아낸 양"이다.

    아직 한 개도 못 쟀으면 None을 준다 - 0으로 주면 "0바이트를 찾았다"로
    읽히는데, 그것과 "아직 모른다"는 다르다.
    """

    row = conn.execute(
        "SELECT SUM(size_kb) AS total FROM baseline_results "
        "WHERE account_id = ? AND generation = ? AND COALESCE(depth, 0) = 0",
        (account_id, generation),
    ).fetchone()
    return row["total"] if row and row["total"] is not None else None
