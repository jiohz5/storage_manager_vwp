"""야간 상세 스캔의 체크포인트/기준선/실행 이력을 담는 SQLite 저장소.

phase 1의 `store.py`(15분 표본)와는 별도 파일(`detail_scan.db`)을 쓴다 -
책임이 다른 데이터를 한 파일에 섞지 않아야 각 데이터의 보존/정리 정책을
독립적으로 관리하기 쉽고, "자체 비대화 방지"(DESIGN.md 1부 2-5) 예산도 따로
따지기 쉽다.

핵심 개념:
- **체크포인트(scan_checkpoints)**: 디렉터리 하나 = 행 하나. `du`/`find`
  대상이 될 디렉터리 큐를 담는다. 처음엔 계정 루트 바로 아래 최상위
  디렉터리들만 pending으로 깔리고, 시간 초과로 "분할"되면 그 자식
  디렉터리들이 새 pending 행으로 추가된다 (DESIGN.md 1부 7절 "디렉터리 단위
  타임아웃 -> 넘으면 하위 디렉터리로 분할해 재시도"). 중단된 지점은 이
  테이블 상태만으로 항상 복원 가능하다.
- **세대(generation)**: 계정별 "기준선 스캔 한 바퀴"를 세는 정수. 한 세대가
  완전히 끝나야(대기 중인 체크포인트가 하나도 없어야) baseline_results에
  결과를 저장하고 다음 세대로 넘어간다. 증가 경로 비교는 항상 "완료된 세대
  vs 그 이전 완료된 세대"를 같은 경로(path) 기준으로 대조한다 - "어제 top N
  vs 오늘 top N"처럼 순위가 바뀌면 다른 항목이 되어버리는 비교(REVIEW.md가
  지적했던 문제)를 피하기 위함이다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASELINE = "baseline"
ACTIVITY = "activity"

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_SPLIT = "split"
STATUS_ERROR = "error"

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_runs (
    run_id TEXT PRIMARY KEY,
    triggered_by TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    generation INTEGER NOT NULL,
    parent_id INTEGER,
    path TEXT NOT NULL,
    depth INTEGER NOT NULL,
    status TEXT NOT NULL,
    size_kb INTEGER,
    changed_count INTEGER,
    error_message TEXT,
    scanned_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_lookup
    ON scan_checkpoints(account_id, kind, generation, status);

CREATE TABLE IF NOT EXISTS baseline_results (
    account_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    path TEXT NOT NULL,
    size_kb INTEGER NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (account_id, generation, path)
);

-- 경로별 증감 이력. baseline_results는 DB 크기 때문에 최근 몇 세대만
-- 남기지만, 이상탐지(중앙값·MAD 등)를 나중에 붙이려면 더 긴 이력이 필요하다.
-- 여기에는 경로와 숫자만 담아 행을 작게 유지하고, 그만큼 오래 보관한다.
CREATE TABLE IF NOT EXISTS growth_history (
    account_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    path TEXT NOT NULL,
    delta_kb INTEGER NOT NULL,
    current_kb INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (account_id, generation, path)
);
CREATE INDEX IF NOT EXISTS idx_growth_history_path
    ON growth_history(account_id, path, generation);

CREATE TABLE IF NOT EXISTS account_scan_state (
    account_id TEXT PRIMARY KEY,
    last_completed_generation INTEGER,
    last_completed_activity_pass INTEGER,
    activity_cursor TEXT,
    last_activity_total_changed INTEGER,
    last_activity_completed_at TEXT
);
"""


def db_path(data_dir: Path) -> Path:
    return data_dir / "detail_scan.db"


# 나중에 추가된 scan_runs 열. `CREATE TABLE IF NOT EXISTS`는 이미 있는 표에
# 열을 더해 주지 않으므로, 기존 데이터 디렉터리에서도 열리도록 따로 채운다.
# (데이터를 지우고 다시 만들게 하면 그동안 쌓인 스캔 이력이 날아간다.)
_COLUMNS_ADDED = {
    "scan_runs": {
        "cpu_system_percent_avg": "REAL",
        "cpu_system_percent_peak": "REAL",
        "cpu_top_percent_avg": "REAL",
        "cpu_top_percent_peak": "REAL",
        "load_avg_1m": "REAL",
        "cpu_count": "INTEGER",
        "rss_peak_kb": "INTEGER",
        "memory_total_kb": "INTEGER",
        "memory_peak_percent": "REAL",
    },
    "account_scan_state": {
        # 계정 목록에 "최근 스캔일"을 보여주려면 기준선을 언제 완주했는지가
        # 필요하다. 기존에는 세대 번호만 남기고 시각은 안 남겼다.
        "last_baseline_completed_at": "TEXT",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _COLUMNS_ADDED.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, sql_type in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path(data_dir)), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- 실행 이력 -----------------------------------------------------------

def start_run(conn: sqlite3.Connection, run_id: str, triggered_by: str) -> None:
    conn.execute(
        "INSERT INTO scan_runs (run_id, triggered_by, started_at, status) VALUES (?, ?, ?, 'running')",
        (run_id, triggered_by, utc_now_iso()),
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: str, status: str, load=None) -> None:
    """실행을 마감한다. `load`(loadstat.Summary)를 주면 CPU 점유도 함께 남긴다.

    점유율을 기록해 두는 이유: 상세 스캔이 얼마나 무거운 작업인지는 계정 크기·
    파일 수·파일시스템에 따라 달라서 **미리 예측할 수 없다.** 대신 실제로 돈
    기록이 쌓이면 "이 환경에서는 이 정도"라고 말할 수 있게 된다."""

    if load is None or not load.samples:
        conn.execute(
            "UPDATE scan_runs SET ended_at = ?, status = ? WHERE run_id = ?",
            (utc_now_iso(), status, run_id),
        )
    else:
        conn.execute(
            "UPDATE scan_runs SET ended_at = ?, status = ?, "
            "cpu_system_percent_avg = ?, cpu_system_percent_peak = ?, "
            "cpu_top_percent_avg = ?, cpu_top_percent_peak = ?, "
            "load_avg_1m = ?, cpu_count = ?, "
            "rss_peak_kb = ?, memory_total_kb = ?, memory_peak_percent = ? "
            "WHERE run_id = ?",
            (
                utc_now_iso(),
                status,
                load.system_percent_avg,
                load.system_percent_peak,
                load.top_percent_avg,
                load.top_percent_peak,
                load.load_avg_1m,
                load.cpu_count,
                load.rss_peak_kb,
                load.memory_total_kb,
                load.memory_peak_percent,
                run_id,
            ),
        )
    conn.commit()


def latest_run(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()


# -- 계정별 스캔 상태 ------------------------------------------------------

@dataclass
class AccountScanState:
    account_id: str
    last_completed_generation: Optional[int] = None
    last_completed_activity_pass: Optional[int] = None
    activity_cursor: Optional[str] = None
    last_activity_total_changed: Optional[int] = None
    last_activity_completed_at: Optional[str] = None

    @property
    def working_generation(self) -> int:
        """지금 작업 중이거나 다음에 작업할 기준선 세대 번호.

        완료된 세대 + 1로 그때그때 계산한다 (별도로 저장된 "진행 중" 포인터가
        없다 - 완료 여부만 진실의 원천으로 삼아야 재시작 시 어긋날 일이
        없다)."""

        return (self.last_completed_generation or 0) + 1

    @property
    def working_activity_pass(self) -> int:
        return (self.last_completed_activity_pass or 0) + 1


def get_account_state(conn: sqlite3.Connection, account_id: str) -> AccountScanState:
    row = conn.execute(
        "SELECT * FROM account_scan_state WHERE account_id = ?", (account_id,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO account_scan_state (account_id) VALUES (?)",
            (account_id,),
        )
        conn.commit()
        return AccountScanState(account_id=account_id)
    return AccountScanState(
        account_id=row["account_id"],
        last_completed_generation=row["last_completed_generation"],
        last_completed_activity_pass=row["last_completed_activity_pass"],
        activity_cursor=row["activity_cursor"],
        last_activity_total_changed=row["last_activity_total_changed"],
        last_activity_completed_at=row["last_activity_completed_at"],
    )


def mark_generation_completed(conn: sqlite3.Connection, account_id: str, generation: int) -> None:
    get_account_state(conn, account_id)  # 행이 없으면 만들어 둠
    conn.execute(
        "UPDATE account_scan_state SET last_completed_generation = ?, "
        "last_baseline_completed_at = ? WHERE account_id = ?",
        (generation, utc_now_iso(), account_id),
    )
    conn.commit()


def mark_activity_pass_completed(
    conn: sqlite3.Connection,
    account_id: str,
    pass_no: int,
    cursor_iso: str,
    total_changed: int,
) -> None:
    get_account_state(conn, account_id)  # 행이 없으면 만들어 둠
    conn.execute(
        """
        UPDATE account_scan_state
        SET last_completed_activity_pass = ?, activity_cursor = ?,
            last_activity_total_changed = ?, last_activity_completed_at = ?
        WHERE account_id = ?
        """,
        (pass_no, cursor_iso, total_changed, utc_now_iso(), account_id),
    )
    conn.commit()


# -- 체크포인트 큐 ---------------------------------------------------------

def seed_checkpoints(
    conn: sqlite3.Connection,
    account_id: str,
    kind: str,
    generation: int,
    paths: List[str],
) -> None:
    """이 (계정, 종류, 세대)에 아직 아무 체크포인트도 없을 때만 최상위
    디렉터리들을 depth 0 pending으로 깐다 (멱등적 - 이미 있으면 아무 것도
    안 함, 재실행해도 중복 삽입되지 않는다)."""

    existing = conn.execute(
        "SELECT COUNT(*) FROM scan_checkpoints WHERE account_id = ? AND kind = ? AND generation = ?",
        (account_id, kind, generation),
    ).fetchone()[0]
    if existing:
        return
    conn.executemany(
        "INSERT INTO scan_checkpoints (account_id, kind, generation, parent_id, path, depth, status) "
        "VALUES (?, ?, ?, NULL, ?, 0, 'pending')",
        [(account_id, kind, generation, path) for path in paths],
    )
    conn.commit()


def is_seeded(conn: sqlite3.Connection, account_id: str, kind: str, generation: int) -> bool:
    """이 (계정, 종류, 세대)에 체크포인트가 이미 한 번이라도 깔렸는지.

    빈 디렉터리(최상위 하위 디렉터리가 하나도 없는 계정)처럼 seed할 대상이
    0개인 경우는 계속 False로 남는다 - 그 경우 매 호출마다 다시 목록을
    조회하지만 결과가 항상 비어 있으므로(os.scandir 한 번, 비용 미미) 안전
    하다."""

    count = conn.execute(
        "SELECT COUNT(*) FROM scan_checkpoints WHERE account_id = ? AND kind = ? AND generation = ?",
        (account_id, kind, generation),
    ).fetchone()[0]
    return count > 0


def next_pending(conn: sqlite3.Connection, account_id: str, kind: str, generation: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM scan_checkpoints
        WHERE account_id = ? AND kind = ? AND generation = ? AND status = 'pending'
        ORDER BY id LIMIT 1
        """,
        (account_id, kind, generation),
    ).fetchone()


def has_pending(conn: sqlite3.Connection, account_id: str, kind: str, generation: int) -> bool:
    return next_pending(conn, account_id, kind, generation) is not None


def mark_done(
    conn: sqlite3.Connection,
    checkpoint_id: int,
    *,
    size_kb: int = None,
    changed_count: int = None,
    error_message: str = None,
) -> None:
    """완료 처리. `error_message`는 "값은 얻었지만 일부만 읽었다"는 부분 측정
    사유를 남길 때 쓴다 (권한 없는 하위 디렉터리 등)."""

    conn.execute(
        "UPDATE scan_checkpoints SET status = 'done', size_kb = ?, changed_count = ?, "
        "error_message = ?, scanned_at = ? WHERE id = ?",
        (size_kb, changed_count, error_message, utc_now_iso(), checkpoint_id),
    )
    conn.commit()


def partial_paths(conn: sqlite3.Connection, account_id: str, generation: int) -> List[str]:
    """이번 세대에서 일부만 읽어 실제보다 작게 측정된 경로들."""

    rows = conn.execute(
        "SELECT path FROM scan_checkpoints WHERE account_id = ? AND kind = 'baseline' "
        "AND generation = ? AND status = 'done' AND error_message IS NOT NULL "
        "ORDER BY path",
        (account_id, generation),
    ).fetchall()
    return [row["path"] for row in rows]


def failed_paths(
    conn: sqlite3.Connection, account_id: str, generation: int, limit: int = 20
) -> List[tuple]:
    """이번 세대에서 아예 재지 못한 경로와 사유 `[(path, message), ...]`.

    `partial_paths`(일부만 읽혀 값은 남은 경우)와 구분한다. 이쪽은 값이 아예
    없으므로, 왜 못 쟀는지 사용자에게 보여 주지 않으면 "스캔이 그냥 실패했다"로만
    보인다."""

    rows = conn.execute(
        "SELECT path, error_message FROM scan_checkpoints WHERE account_id = ? "
        "AND kind = 'baseline' AND generation = ? AND status = 'error' "
        "ORDER BY path LIMIT ?",
        (account_id, generation, limit),
    ).fetchall()
    return [(row["path"], row["error_message"] or "") for row in rows]


def failed_count(conn: sqlite3.Connection, account_id: str, generation: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM scan_checkpoints WHERE account_id = ? AND kind = 'baseline' "
        "AND generation = ? AND status = 'error'",
        (account_id, generation),
    ).fetchone()[0]


def mark_error(conn: sqlite3.Connection, checkpoint_id: int, message: str) -> None:
    conn.execute(
        "UPDATE scan_checkpoints SET status = 'error', error_message = ?, scanned_at = ? WHERE id = ?",
        (message, utc_now_iso(), checkpoint_id),
    )
    conn.commit()


def mark_split(conn: sqlite3.Connection, checkpoint_id: int) -> None:
    conn.execute(
        "UPDATE scan_checkpoints SET status = 'split', scanned_at = ? WHERE id = ?",
        (utc_now_iso(), checkpoint_id),
    )
    conn.commit()


def insert_children(conn: sqlite3.Connection, parent: sqlite3.Row, child_paths: List[str]) -> None:
    if not child_paths:
        return
    conn.executemany(
        "INSERT INTO scan_checkpoints (account_id, kind, generation, parent_id, path, depth, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
        [
            (parent["account_id"], parent["kind"], parent["generation"], parent["id"], child, parent["depth"] + 1)
            for child in child_paths
        ],
    )
    conn.commit()


def leaf_results(conn: sqlite3.Connection, account_id: str, generation: int) -> List[sqlite3.Row]:
    """이번 세대에서 실제로 크기가 측정된(done) 항목들 - "성장 경로"로 보여줄
    후보. split된 상위 디렉터리는 자기 자신의 크기를 갖지 않으므로 제외된다
    (그 자리는 자식들의 done 결과가 대신한다)."""

    return conn.execute(
        """
        SELECT * FROM scan_checkpoints
        WHERE account_id = ? AND kind = 'baseline' AND generation = ? AND status = 'done'
        ORDER BY size_kb DESC
        """,
        (account_id, generation),
    ).fetchall()


def save_baseline_results(conn: sqlite3.Connection, account_id: str, generation: int, rows: List[sqlite3.Row]) -> None:
    now = utc_now_iso()
    conn.executemany(
        "INSERT OR REPLACE INTO baseline_results (account_id, generation, path, size_kb, completed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(account_id, generation, row["path"], row["size_kb"], now) for row in rows],
    )
    conn.commit()


def top_paths(conn: sqlite3.Connection, account_id: str, generation: int, limit: int = 15) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM baseline_results WHERE account_id = ? AND generation = ? ORDER BY size_kb DESC LIMIT ?",
        (account_id, generation, limit),
    ).fetchall()


def growth_delta(conn: sqlite3.Connection, account_id: str, current_generation: int, previous_generation: int, limit: int = 15):
    """같은 경로(path) 기준으로 현재/이전 세대를 대조한다 (top N 목록 자체를
    비교하지 않음 - "어제 top N vs 오늘 top N"은 순위가 바뀌면 다른 항목처럼
    보이는 문제가 있었다). 증가량 기준 내림차순으로 최대 limit개 반환."""

    rows = conn.execute(
        """
        SELECT cur.path AS path, cur.size_kb AS current_kb, prev.size_kb AS previous_kb
        FROM baseline_results cur
        LEFT JOIN baseline_results prev
            ON prev.account_id = cur.account_id AND prev.generation = ? AND prev.path = cur.path
        WHERE cur.account_id = ? AND cur.generation = ?
        ORDER BY (cur.size_kb - COALESCE(prev.size_kb, 0)) DESC
        LIMIT ?
        """,
        (previous_generation, account_id, current_generation, limit),
    ).fetchall()
    return rows


def record_growth_history(
    conn: sqlite3.Connection,
    account_id: str,
    generation: int,
    previous_generation: Optional[int],
) -> int:
    """완료된 세대의 경로별 증감을 이력 테이블에 남긴다.

    `baseline_results`는 곧 정리되어 사라지므로, 이상탐지에 쓸 수 있도록 숫자만
    따로 남긴다. 비교할 이전 세대가 없으면(첫 기준선) 아무것도 쓰지 않는다 -
    전부 '신규'로 기록되면 나중 통계가 왜곡된다.
    """

    if not previous_generation or previous_generation < 1:
        return 0

    rows = conn.execute(
        """
        SELECT cur.path AS path, cur.size_kb AS current_kb, prev.size_kb AS previous_kb
        FROM baseline_results cur
        LEFT JOIN baseline_results prev
            ON prev.account_id = cur.account_id AND prev.generation = ? AND prev.path = cur.path
        WHERE cur.account_id = ? AND cur.generation = ?
        """,
        (previous_generation, account_id, generation),
    ).fetchall()
    if not rows:
        return 0

    now = utc_now_iso()
    conn.executemany(
        "INSERT OR REPLACE INTO growth_history "
        "(account_id, generation, path, delta_kb, current_kb, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                account_id,
                generation,
                row["path"],
                row["current_kb"] - (row["previous_kb"] or 0),
                row["current_kb"],
                now,
            )
            for row in rows
        ],
    )
    conn.commit()
    return len(rows)


def growth_history_for_path(
    conn: sqlite3.Connection, account_id: str, path: str, limit: int = 60
) -> List[sqlite3.Row]:
    """한 경로의 최근 증감 이력 (오래된 것부터). 이상탐지를 붙일 때 쓸 입력."""

    rows = conn.execute(
        "SELECT generation, delta_kb, current_kb, recorded_at FROM growth_history "
        "WHERE account_id = ? AND path = ? ORDER BY generation DESC LIMIT ?",
        (account_id, path, limit),
    ).fetchall()
    return list(reversed(rows))


def prune_growth_history(
    conn: sqlite3.Connection, account_id: str, keep_generations: int = 60
) -> int:
    """오래된 세대의 증감 이력을 지운다.

    `prune_old_generations`(기준선, 기본 2세대)와 별개로 훨씬 길게 남긴다 -
    행이 작아 오래 들고 있어도 부담이 적고, 이력이 짧으면 이상탐지 자체가
    불가능하기 때문."""

    generations = [
        row["generation"]
        for row in conn.execute(
            "SELECT DISTINCT generation FROM growth_history WHERE account_id = ? "
            "ORDER BY generation DESC",
            (account_id,),
        ).fetchall()
    ]
    to_delete = generations[keep_generations:]
    if not to_delete:
        return 0
    placeholders = ",".join("?" for _ in to_delete)
    cursor = conn.execute(
        f"DELETE FROM growth_history WHERE account_id = ? AND generation IN ({placeholders})",
        (account_id, *to_delete),
    )
    conn.commit()
    return cursor.rowcount


def prune_old_generations(conn: sqlite3.Connection, account_id: str, keep_last: int = 2) -> int:
    """오래된 세대의 체크포인트/기준선 결과를 지운다 (DB 크기 예산 유지).

    최근 `keep_last`개의 "완료된" 세대만 baseline_results에 남기고, 그보다
    오래된 세대의 체크포인트/기준선 행을 모두 지운다. 진행 중인(아직 완료
    안 된) 세대는 절대 건드리지 않는다.
    """

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
    conn.commit()
    return cursor.rowcount


def prune_completed_activity_checkpoints(conn: sqlite3.Connection, account_id: str, pass_no: int) -> int:
    """완료된 activity pass의 체크포인트 상세는 요약(account_scan_state)만
    남기고 지운다 - 파일 하나하나를 오래 들고 있을 필요가 없다."""

    cursor = conn.execute(
        "DELETE FROM scan_checkpoints WHERE account_id = ? AND kind = 'activity' AND generation = ?",
        (account_id, pass_no),
    )
    conn.commit()
    return cursor.rowcount
