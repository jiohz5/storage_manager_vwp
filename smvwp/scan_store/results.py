"""기준선 결과 (`baseline_results`, `baseline_large_files`).

스캔이 실제로 걸어 들어간 디렉터리의 크기와, 그 밤의 큰 파일. 증가 경로·트리·
새로 생긴 과제 판정은 전부 이 결과를 세대끼리 견준다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .db import utc_now_iso


def _shallow(columns: str, max_depth: int, null_depth: int) -> str:
    """한 세대에서 깊이가 `max_depth` 이하인 행 - **깊이 인덱스를 타는 모양으로.**

    `COALESCE(depth, n) <= ?` 로 쓰면 SQLite 가 깊이 인덱스를 못 써서 그 세대의
    행을 **전부** 읽고 걸렀다. 경로가 수만 개인 계정이면 수 MB 이고, 화면은 이것을
    계정마다 불렀다 - 데이터 디렉터리가 NFS 면 스캔 탭을 처음 열 때와 스캔 중
    5초마다 그만큼을 네트워크로 읽었다. 깊이 범위와 '깊이 없음' 을 따로 묻고
    합치면 둘 다 인덱스 범위 하나로 끝난다.

    깊이가 없는 행(깊이 열이 생기기 전의 스캔)은 예전처럼 `null_depth` 로 친다.
    매개변수는 이름으로 받는다 (`:account`, `:generation`, `:max_depth`)."""

    sql = (
        f"SELECT {columns} FROM baseline_results "
        "WHERE account_id = :account AND generation = :generation AND depth <= :max_depth"
    )
    if null_depth <= max_depth:
        sql += (
            f" UNION ALL SELECT {columns} FROM baseline_results "
            "WHERE account_id = :account AND generation = :generation AND depth IS NULL"
        )
    return sql


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
    재측정이 겹칠 수 있고, 나중 값이 항상 더 정확하다.

    **루트 줄이 없으면** (시간 초과로 끊겼다) 바로 아래 자식들이 측정 단위다 -
    깊이를 한 단계씩 당긴다. 끊긴 체크포인트는 남은 자식만 다시 큐에 넣고 그것들은
    자기 루트에서 깊이 0 이 되는데, 먼저 끝난 자식만 깊이 1 로 남으면 깊이 0 을 더하는
    계정 합계(`measured_total_kb`)와 증가 경로 목록에서 그 자식이 통째로 빠진다.
    실제로 나눠 잰 밤마다 계정 합계가 그만큼 줄었다가 다음 밤 '늘었다'로 돌아왔다."""

    if not entries:
        return 0

    now = utc_now_iso()
    root = root_path.rstrip("/")
    unfinished = all(path.rstrip("/") != root for path, _ in entries)
    rows = []
    for path, size_kb in entries:
        normalized = path.rstrip("/")
        if normalized == root:
            depth = 0
        else:
            relative = normalized[len(root):].lstrip("/")
            depth = relative.count("/") + 1 if relative else 0
            if unfinished:
                depth = max(0, depth - 1)
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
        f"SELECT * FROM ({_shallow('*', max_depth, null_depth=1)}) "
        "ORDER BY size_kb DESC, path LIMIT :limit",
        {"account": account_id, "generation": generation, "max_depth": max_depth,
         "limit": limit},
    ).fetchall()


# `growth_delta` 줄의 `previous_note` - 지난 스캔에 같은 경로의 줄이 없었던 까닭.
#
# 줄이 없다고 다 새로 생긴 것이 아니다. 결과 표의 경로는 "파일시스템에 있는 것" 이
# 아니라 **그 밤에 잰 단위**라, 밤마다 다르게 나뉠 수 있다 (`is_confidently_new`
# 와 같은 이야기). 예전에는 이것을 가리지 않아 지난밤 나눠 잰 폴더가 오늘 통째로
# '새 경로'가 되고, 그 크기 전체가 증가로 잡혀 **경로 급증 알림**까지 갔다.
PREVIOUS_PIECED = "pieced"          # 지난번에는 나눠 쟀다 - previous_kb 는 조각의 합
PREVIOUS_UNMEASURED = "unmeasured"  # 지난번에 못 쟀다 - previous_kb 는 모른다 (None)

# `growth_delta` 의 순서.
ORDER_GROWTH = "growth"   # 많이 는 것부터 (알림·보고서 - "어디가 찼나")
ORDER_CHANGE = "change"   # 많이 변한 것부터, 늘든 줄든 (화면 - 정리한 곳도 보인다)


def _pieces_kb(conn: sqlite3.Connection, account_id: str, generation: int, path: str):
    """그 세대가 `path` 를 나눠 쟀으면 조각들의 합, 아니면 None.

    조각 = `path` 아래에서 위로 잰 조상이 없는 줄들. 바로 아래 자식만 더하면
    쪼개진 자식의 손주들을 놓치고, 다 더하면 부모와 자식을 겹쳐 센다. 쪼개진
    폴더에 직접 있는 파일은 그 밤에 아무도 안 셌으므로 이 합은 **하한**이다.
    `path/` 와 `path0` 사이의 경로 범위라 기본 열쇠 인덱스 범위 하나로 읽는다."""

    rows = conn.execute(
        "SELECT path, size_kb FROM baseline_results "
        "WHERE account_id = ? AND generation = ? AND path > ? AND path < ?",
        (account_id, generation, path + "/", path + "0"),
    ).fetchall()
    if not rows:
        return None
    sizes = {row["path"]: row["size_kb"] for row in rows}
    total = 0
    for piece, size_kb in sizes.items():
        parent = piece.rsplit("/", 1)[0]
        while len(parent) > len(path) and parent not in sizes:
            parent = parent.rsplit("/", 1)[0]
        if len(parent) <= len(path):
            total += size_kb
    return total


def growth_delta(
    conn: sqlite3.Connection,
    account_id: str,
    current_generation: int,
    previous_generation: int,
    limit: int = 15,
    max_depth: int = 1,
    order: str = ORDER_GROWTH,
) -> List[dict]:
    """같은 경로(path) 기준으로 현재/이전 세대를 대조한다 (top N 목록 자체를
    비교하지 않음 - "어제 top N vs 오늘 top N"은 순위가 바뀌면 다른 항목처럼
    보이는 문제가 있었다).

    줄은 `{path, current_kb, previous_kb, previous_note}`. 지난 세대에 같은 줄이
    없으면 그 자리를 나눠 쟀는지(`PREVIOUS_PIECED` - 조각의 합을 지난 값으로), 못
    쟀는지(`PREVIOUS_UNMEASURED` - 지난 값은 None) 보고, 둘 다 아니어야 새 경로다
    (`previous_note` None, `previous_kb` None). 새 경로는 크기 전체를 증가로 친다.

    `order` 가 `ORDER_GROWTH` 면 증가량, `ORDER_CHANGE` 면 증감의 크기 순으로 최대
    `limit` 개. 지난 값을 모르는 줄은 증감 0 으로 줄 세운다."""

    rows = conn.execute(
        f"""
        SELECT cur.path AS path, cur.size_kb AS current_kb, prev.size_kb AS previous_kb
        FROM ({_shallow('path, size_kb', max_depth, null_depth=1)}) cur
        LEFT JOIN baseline_results prev
            ON prev.account_id = :account AND prev.generation = :previous AND prev.path = cur.path
        """,
        {"account": account_id, "generation": current_generation,
         "previous": previous_generation, "max_depth": max_depth},
    ).fetchall()

    out = []
    unmeasured = None
    for row in rows:
        item = {
            "path": row["path"], "current_kb": row["current_kb"],
            "previous_kb": row["previous_kb"], "previous_note": None,
        }
        if item["previous_kb"] is None:
            pieces = _pieces_kb(conn, account_id, previous_generation, item["path"])
            if pieces is not None:
                item["previous_kb"], item["previous_note"] = pieces, PREVIOUS_PIECED
            else:
                if unmeasured is None:
                    unmeasured = {
                        found["path"].rstrip("/")
                        for found in conn.execute(
                            "SELECT path FROM scan_checkpoints WHERE account_id = ? "
                            "AND kind = 'baseline' AND generation = ? AND status = 'error'",
                            (account_id, previous_generation),
                        )
                    }
                if item["path"].rstrip("/") in unmeasured:
                    item["previous_note"] = PREVIOUS_UNMEASURED
        out.append(item)

    def change(item) -> int:
        if item["previous_note"] == PREVIOUS_UNMEASURED:
            return 0
        return item["current_kb"] - (item["previous_kb"] or 0)

    if order == ORDER_CHANGE:
        out.sort(key=lambda item: (-abs(change(item)), item["path"]))
    else:
        out.sort(key=lambda item: (-change(item), item["path"]))
    return out[:limit]


# -- 변화가 어디에 몰렸나 ----------------------------------------------------

# 따라 내려갈 때 자식 하나가 부모 변화의 이만큼을 넘게 설명해야 내려간다.
# 고르게 퍼진 변화를 한 곳으로 지목하면 틀린 안내가 된다.
FOCUS_SHARE = 0.5
# 이보다 작은 변화는 따라가지 않는다 (사람이 손댈 값어치가 있는 크기).
FOCUS_MIN_KB = 1024 * 1024   # 1GB

# `change_focus` 결과의 note.
FOCUS_NEW = "new"       # 지난 스캔에는 없던 폴더
FOCUS_GONE = "gone"     # 이번 스캔에는 없는 폴더 (지웠다)
FOCUS_FILES = "files"   # 그 폴더에 **직접 있는 파일**이 변했다 (하위 폴더가 아니라)
FOCUS_SPREAD = "spread" # 하위 폴더 여럿에 고르게 퍼졌다 - 한 곳을 지목하지 않는다


def _rows_under(conn: sqlite3.Connection, account_id: str, generation, path: str) -> Dict[str, int]:
    if not generation:
        return {}
    rows = conn.execute(
        "SELECT path, size_kb FROM baseline_results "
        "WHERE account_id = ? AND generation = ? AND path >= ? AND path < ?",
        (account_id, generation, path, path + "0"),
    ).fetchall()
    out = {}
    for row in rows:
        key = row["path"].rstrip("/")
        if key == path or key.startswith(path + "/"):
            out[key] = row["size_kb"]
    return out


def change_focus(
    conn: sqlite3.Connection,
    account_id: str,
    current_generation: int,
    previous_generation: int,
    path: str,
) -> Optional[dict]:
    """`path` 의 증감이 그 안의 어디에 몰렸나 - `{path, delta_kb, note}` 또는 None.

    증가 경로 목록은 측정 단위(계정 바로 아래 폴더)까지만 말한다 - "sim 이 3.2TB
    늘었다". 사람이 하려는 일은 그다음, 그 안의 **어디인지** 찾아 담당자에게 묻는
    것이다. 두 스캔의 기록을 따라, 자식 하나가 부모 변화의 반을 넘게 설명하는 동안
    내려간다.

    - 그 폴더에 **직접 있는 파일**도 후보다 (부모 크기에서 하위 폴더 합을 뺀 것의
      변화). 그쪽이 반을 넘으면 `FOCUS_FILES` - 하위 폴더를 지목하면 틀린 안내다.
    - 처음부터 하위 폴더 여럿에 고르게 퍼졌으면 `FOCUS_SPREAD`.
    - 더 말할 것이 없으면 None - 변화가 작거나, 하위 폴더 기록이 없는 폴더다.

    - 두 스캔 모두 그 폴더 **안을 기록했을 때만** 내려간다. 한쪽 기록이 거기서
      끝났으면(깊이 제한) 안쪽 폴더가 새로 생긴 것인지 알 수 없다.
    - 나눠 잰 폴더는 조각의 합으로 친다 (`growth_delta` 와 같다).
    - 새로 생긴 폴더(`FOCUS_NEW`)와 없어진 폴더(`FOCUS_GONE`)에서는 멈춘다.

    결과 표의 그 폴더 아래 줄을 두 세대 모두 읽는다 - 창을 열 때가 아니라 야간
    스캔이 요약을 남길 때(`nightly_scan.save_summaries`) 부른다."""

    path = path.rstrip("/")
    current = _rows_under(conn, account_id, current_generation, path)
    previous = _rows_under(conn, account_id, previous_generation, path)
    if not current and not previous:
        return None

    # 두 세대의 경로를 한 나무로 세운다 (가장 가까운 기록된 조상이 부모).
    known = set(current) | set(previous) | {path}
    children: Dict[str, List[str]] = {}
    for item in known:
        if item == path:
            continue
        parent = item.rsplit("/", 1)[0]
        while parent not in known and len(parent) > len(path):
            parent = parent.rsplit("/", 1)[0]
        children.setdefault(parent, []).append(item)

    def sizes(recorded: Dict[str, int]) -> Dict[str, int]:
        """기록된 값, 없으면 아래 조각들의 합 (아래에서 위로)."""

        out: Dict[str, int] = {}
        for item in sorted(known, key=lambda name: -name.count("/")):
            if item in recorded:
                out[item] = recorded[item]
            else:
                pieces = [out[kid] for kid in children.get(item, []) if kid in out]
                if pieces:
                    out[item] = sum(pieces)
        return out

    def looked_inside(recorded: Dict[str, int]) -> set:
        out = set()
        for item in recorded:
            parent = item.rsplit("/", 1)[0]
            while len(parent) >= len(path):
                out.add(parent)
                if parent == path:
                    break
                parent = parent.rsplit("/", 1)[0]
        return out

    now, before = sizes(current), sizes(previous)
    if path not in now or path not in before:
        return None
    opened_now, opened_before = looked_inside(current), looked_inside(previous)

    def files_change(node: str) -> Optional[int]:
        """그 폴더에 직접 있는 파일의 변화. 두 스캔 모두 그 폴더를 통째로 쟀을 때만."""

        if node not in current or node not in previous:
            return None
        kids = children.get(node, [])
        here_now = current[node] - sum(now[kid] for kid in kids if kid in now)
        here_before = previous[node] - sum(before[kid] for kid in kids if kid in before)
        return here_now - here_before

    node, delta, note = path, now[path] - before[path], None
    while abs(delta) >= FOCUS_MIN_KB:
        if node not in opened_now or node not in opened_before:
            break
        best, best_delta = None, 0
        for kid in children.get(node, []):
            if kid not in now and kid not in before:
                continue
            change = now.get(kid, 0) - before.get(kid, 0)
            if best is None or (change > best_delta if delta > 0 else change < best_delta):
                best, best_delta = kid, change
        here = files_change(node)
        if here is not None and (here > 0) == (delta > 0) and abs(here) >= FOCUS_SHARE * abs(delta) \
                and abs(here) >= FOCUS_MIN_KB and abs(here) >= abs(best_delta):
            return {"path": node, "delta_kb": here, "note": FOCUS_FILES}
        if (
            best is None
            or (best_delta > 0) != (delta > 0)
            or abs(best_delta) < FOCUS_MIN_KB
            or abs(best_delta) < FOCUS_SHARE * abs(delta)
        ):
            if node == path and best is not None:
                note = FOCUS_SPREAD
            break
        node, delta = best, best_delta
        if node not in before or node not in now:
            note = FOCUS_NEW if node not in before else FOCUS_GONE
            break

    if node == path and note is None:
        return None
    return {"path": node, "delta_kb": delta, "note": note}


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

    `(경로, 지금 KB, 이전 KB 또는 None, 견줬나)` 를 큰 것부터. 견줬는데 이전 값이
    None 이면 **지난 밤에는 없던(또는 그때는 크지 않던) 파일**이다.

    두 경우를 굳이 안 가른다 - 상위 목록에 없었다는 것만 알 수 있고, 그것이
    "새로 생겼다"인지 "그때는 작았다"인지는 이 데이터로 판단할 수 없다.
    지어내지 않는 편이 맞다.

    **견줄 목록이 아예 없으면** (첫 스캔, 또는 지난 바퀴가 큰 파일을 모으지 않는
    `du` 엔진이었다) 견줬나가 False 다. 예전에는 이 경우도 모든 파일이 '목록에 없던
    파일'이 되어 첫 화면이 온통 주황색이었다. 스캔 상태는 첫 세대의 이전을 0 으로
    넘기므로 0 도 없는 것으로 친다.
    """

    current = largest_files(conn, account_id, generation, limit=1000)
    previous = {}
    if previous_generation:
        previous = {
            row["path"]: row["size_kb"]
            for row in largest_files(conn, account_id, previous_generation, limit=1000)
        }
    compared = bool(previous)
    return [
        (row["path"], row["size_kb"], previous.get(row["path"]), compared)
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
        f"SELECT SUM(size_kb) AS total FROM ({_shallow('size_kb', 0, null_depth=0)})",
        {"account": account_id, "generation": generation, "max_depth": 0},
    ).fetchone()
    return row["total"] if row and row["total"] is not None else None


def comparable_change(
    conn: sqlite3.Connection,
    account_id: str,
    current_generation: int,
    previous_generation: int,
    current_kb: Optional[int] = None,
    previous_kb: Optional[int] = None,
) -> Optional[dict]:
    """끝난 두 바퀴 사이의 계정 증감 - **두 바퀴 모두 잰 곳끼리** 견준다.

    합계끼리 빼면 한쪽 바퀴에서 못 잰(권한 오류 등) 폴더가 통째로 늘거나 준 양이
    된다. 지난 바퀴에 못 잰 900GB 폴더가 이번에 재지면 실제로 10GB 늘었어도 홈의
    '무엇부터 할까' 에 "910 GB 늘었습니다" 가 떴다. 그래서 어느 쪽에서든 못 잰
    체크포인트가 있으면 그 자리를 **양쪽 합계에서** 뺀다.

    뺄 자리는 못 잰 경로 그대로가 아니라, 다른 바퀴가 그 위 폴더를 한 단위로 쟀으면
    그 폴더다 - 한 단위로 잰 값에서 못 잰 조각의 몫만 덜어 낼 수는 없다.

    `{"delta_kb", "current_kb", "previous_kb", "excluded": [뺀 자리]}` - 합계는 뺀 뒤의
    값이다. 어느 한쪽이라도 잰 것이 없으면 None. 합계(`measured_total_kb`)를 이미
    읽었으면 넘겨 같은 합을 두 번 묻지 않는다. 못 잰 곳이 없는 보통 밤에는 오류
    체크포인트를 찾는 질의 하나만 더 든다."""

    if current_kb is None:
        current_kb = measured_total_kb(conn, account_id, current_generation)
    if previous_kb is None:
        previous_kb = measured_total_kb(conn, account_id, previous_generation)
    if current_kb is None or previous_kb is None:
        return None
    generations = (current_generation, previous_generation)
    failed = sorted({
        row["path"].rstrip("/")
        for row in conn.execute(
            "SELECT path FROM scan_checkpoints WHERE account_id = ? AND kind = 'baseline' "
            "AND generation IN (?, ?) AND status = 'error'",
            (account_id, *generations),
        )
    })
    roots = set()
    for path in failed:
        parts = path.split("/")
        ancestors = ["/".join(parts[:end]) for end in range(2, len(parts))]
        units = set()
        if ancestors:
            marks = ", ".join("?" for _ in ancestors)
            units = {
                row["path"]
                for row in conn.execute(
                    "SELECT path FROM baseline_results WHERE account_id = ? "
                    f"AND generation IN (?, ?) AND path IN ({marks}) "
                    "AND (depth = 0 OR depth IS NULL)",
                    (account_id, *generations, *ancestors),
                )
            }
        # 가장 위의 단위 - 그 아래는 어느 바퀴에서도 따로 견줄 수 없다.
        roots.add(next((ancestor for ancestor in ancestors if ancestor in units), path))
    roots = sorted(
        root for root in roots
        if not any(root.startswith(other + "/") for other in roots)
    )
    left_out = {generation: 0 for generation in generations}
    for root in roots:
        for generation in generations:
            row = conn.execute(
                "SELECT SUM(size_kb) AS total FROM baseline_results WHERE account_id = ? "
                "AND generation = ? AND (depth = 0 OR depth IS NULL) "
                "AND (path = ? OR (path > ? AND path < ?))",
                (account_id, generation, root, root + "/", root + "0"),
            ).fetchone()
            left_out[generation] += row["total"] or 0
    current_kb -= left_out[current_generation]
    previous_kb -= left_out[previous_generation]
    return {
        "delta_kb": current_kb - previous_kb,
        "current_kb": current_kb,
        "previous_kb": previous_kb,
        "excluded": roots,
    }
