"""체크포인트 큐 (`scan_checkpoints`) 와 계정별 스캔 상태.

디렉터리 하나가 행 하나다. 처음엔 계정 바로 아래 최상위 디렉터리만 깔리고,
시간 초과로 쪼개지면 그 자식이 새 행으로 붙는다. 중단된 지점은 이 표만으로
항상 복원된다. 진행률·남은 시간 어림·"왜 더딘가" 진단도 이 표에서 나온다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from .db import (
    BASELINE,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_PENDING,
    STATUS_SPLIT,
    utc_now_iso,
)


def checkpoint_progress(
    conn: sqlite3.Connection, account_id: str, kind: str, generation: int
) -> dict:
    """계정 하나의 체크포인트 진행 상황 (상태별 개수)."""

    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM scan_checkpoints "
        "WHERE account_id = ? AND kind = ? AND generation = ? GROUP BY status",
        (account_id, kind, generation),
    ).fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    return {
        "pending": counts.get(STATUS_PENDING, 0),
        "done": counts.get(STATUS_DONE, 0),
        "split": counts.get(STATUS_SPLIT, 0),
        "error": counts.get(STATUS_ERROR, 0),
        "total": sum(counts.values()),
    }


def recent_checkpoints(
    conn: sqlite3.Connection, account_id: str, kind: str, generation: int, limit: int = 200
) -> List[sqlite3.Row]:
    """최근에 처리한 체크포인트부터 (진행 상황 보기용).

    아직 처리 안 한 것(`scanned_at`이 없는 것)은 뒤로 보낸다 - 사용자가 알고
    싶은 것은 "어디까지 했나"이고, 대기 목록은 그 다음이다."""

    return conn.execute(
        "SELECT path, status, size_kb, error_message, scanned_at "
        "FROM scan_checkpoints WHERE account_id = ? AND kind = ? AND generation = ? "
        "ORDER BY (scanned_at IS NULL), scanned_at DESC, id LIMIT ?",
        (account_id, kind, generation, limit),
    ).fetchall()


# -- 계정별 스캔 상태 ------------------------------------------------------

@dataclass
class AccountScanState:
    account_id: str
    last_completed_generation: Optional[int] = None

    @property
    def working_generation(self) -> int:
        """지금 작업 중이거나 다음에 작업할 기준선 세대 번호.

        완료된 세대 + 1로 그때그때 계산한다 (별도로 저장된 "진행 중" 포인터가
        없다 - 완료 여부만 진실의 원천으로 삼아야 재시작 시 어긋날 일이
        없다)."""

        return (self.last_completed_generation or 0) + 1



def get_account_state(conn: sqlite3.Connection, account_id: str) -> AccountScanState:
    row = conn.execute(
        "SELECT * FROM account_scan_state WHERE account_id = ?", (account_id,)
    ).fetchone()
    if row is None:
        # `OR IGNORE` - 읽고 나서 넣기 사이에 다른 스레드가 먼저 넣을 수 있다.
        # 창을 열면 대시보드(헬스체크)와 스캔 상태 조회가 같은 새 계정에 대해
        # 동시에 여기로 와서, 늦은 쪽이 UNIQUE 위반으로 죽었다. 그러면 홈의
        # 우선순위 카드가 "계산하지 못했습니다" 로 떴다.
        conn.execute(
            "INSERT OR IGNORE INTO account_scan_state (account_id) VALUES (?)",
            (account_id,),
        )
        conn.commit()
        return AccountScanState(account_id=account_id)
    return AccountScanState(
        account_id=row["account_id"],
        last_completed_generation=row["last_completed_generation"],
    )


def mark_generation_completed(conn: sqlite3.Connection, account_id: str, generation: int) -> None:
    get_account_state(conn, account_id)  # 행이 없으면 만들어 둠
    conn.execute(
        "UPDATE account_scan_state SET last_completed_generation = ?, "
        "last_baseline_completed_at = ? WHERE account_id = ?",
        (generation, utc_now_iso(), account_id),
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


def next_pending(
    conn: sqlite3.Connection,
    account_id: str,
    kind: str,
    generation: int,
    exclude_ids=None,
) -> Optional[sqlite3.Row]:
    """다음에 처리할 대기 체크포인트 하나.

    `exclude_ids`는 **다른 작업자가 지금 붙잡고 있는 것**을 건너뛰기 위한
    것이다. 여러 작업자가 같은 계정의 체크포인트를 나눠 처리할 때 같은 행을
    두 번 집지 않게 한다.

    **DB에 'running' 상태를 만들지 않은 것은 의도다** (DESIGN.md 1부 7절).
    중간 상태를 두면 프로세스가 죽었을 때 그 행을 되돌리는 복구 로직이
    따로 필요해진다. 배정 정보는 이 프로세스 메모리에만 두므로, 죽으면 함께
    사라지고 DB에는 여전히 pending만 남아 다음 실행이 그대로 이어받는다.
    """

    if not exclude_ids:
        return conn.execute(
            """
            SELECT * FROM scan_checkpoints
            WHERE account_id = ? AND kind = ? AND generation = ? AND status = 'pending'
            ORDER BY id LIMIT 1
            """,
            (account_id, kind, generation),
        ).fetchone()

    ids = list(exclude_ids)
    placeholders = ",".join("?" for _ in ids)
    return conn.execute(
        f"""
        SELECT * FROM scan_checkpoints
        WHERE account_id = ? AND kind = ? AND generation = ? AND status = 'pending'
          AND id NOT IN ({placeholders})
        ORDER BY id LIMIT 1
        """,
        (account_id, kind, generation, *ids),
    ).fetchone()


def mark_done(
    conn: sqlite3.Connection,
    checkpoint_id: int,
    *,
    size_kb: int = None,
    error_message: str = None,
) -> None:
    """완료 처리. `error_message`는 "값은 얻었지만 일부만 읽었다"는 부분 측정
    사유를 남길 때 쓴다 (권한 없는 하위 디렉터리 등)."""

    conn.execute(
        "UPDATE scan_checkpoints SET status = 'done', size_kb = ?, "
        "error_message = ?, scanned_at = ? WHERE id = ?",
        (size_kb, error_message, utc_now_iso(), checkpoint_id),
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


def last_processed(
    conn: sqlite3.Connection, account_id: str, kind: str, generation: int
):
    """가장 최근에 처리한 체크포인트 한 건 (없으면 None).

    보고서에 "어디까지 갔나"를 적으려면 진행 개수만으로는 부족하다 - 숫자는
    얼마나 남았는지만 말하고, 경로는 **어느 대목에서 멈췄는지**를 말한다.
    밤새 돌다 끊긴 스캔을 다음 날 아침에 볼 때 필요한 것은 후자다."""

    return conn.execute(
        "SELECT path, status, size_kb, scanned_at FROM scan_checkpoints "
        "WHERE account_id = ? AND kind = ? AND generation = ? AND scanned_at IS NOT NULL "
        "ORDER BY scanned_at DESC, id DESC LIMIT 1",
        (account_id, kind, generation),
    ).fetchone()


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


# 남은 시간 추정에 쓸 최근 완료 개수. 너무 적으면 우연에 흔들리고, 너무 많으면
# 옛날(다른 밤, 다른 계정 상태)의 속도가 섞인다.
ETA_SAMPLE_SIZE = 30
# 중앙값을 내려면 최소 이만큼의 간격이 있어야 한다. 이보다 적으면 숫자를
# 내놓지 않는다 - 근거 없는 예상 시간은 없느니만 못하다.
ETA_MIN_INTERVALS = 4


def completion_intervals(
    conn: sqlite3.Connection,
    account_id: str,
    kind: str,
    generation: int,
    limit: int = ETA_SAMPLE_SIZE,
) -> List[float]:
    """최근 완료된 체크포인트들 **사이의 간격**(초) 목록.

    평균이 아니라 간격 목록을 그대로 주는 이유는 호출부에서 중앙값을 쓰기
    위해서다. 스캔은 밤을 걸쳐 이어지므로 "06:00에 멈췄다가 다음 날 22:00에
    재개" 같은 16시간짜리 간격이 섞인다. 평균을 내면 그 하나가 전체를 지배해
    남은 시간이 며칠로 나온다. 중앙값은 그런 이상치에 흔들리지 않는다.
    """

    rows = conn.execute(
        "SELECT scanned_at FROM scan_checkpoints "
        "WHERE account_id = ? AND kind = ? AND generation = ? "
        "AND status IN (?, ?) AND scanned_at IS NOT NULL "
        "ORDER BY scanned_at DESC LIMIT ?",
        (account_id, kind, generation, STATUS_DONE, STATUS_ERROR, limit),
    ).fetchall()
    if len(rows) < 2:
        return []

    stamps = []
    for row in rows:
        try:
            stamps.append(datetime.fromisoformat(str(row["scanned_at"])))
        except (TypeError, ValueError):  # pragma: no cover - 깨진 값은 건너뛴다
            continue
    if len(stamps) < 2:
        return []

    stamps.sort()
    return [
        (later - earlier).total_seconds()
        for earlier, later in zip(stamps, stamps[1:])
        if (later - earlier).total_seconds() > 0
    ]


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def estimate_remaining_seconds(
    conn: sqlite3.Connection,
    account_id: str,
    kind: str,
    generation: int,
    pending: int,
) -> Optional[float]:
    """남은 체크포인트를 다 도는 데 걸릴 대략의 시간(초). 모르면 None.

    ## 이 값이 '예측'이 아닌 이유

    README가 "얼마나 걸릴지 미리 예측하지 않는다"고 못 박은 것은 **돌기 전에**
    내놓는 숫자를 말한다. 여기 값은 반대로 **지금 이 스캔이 실제로 낸 속도**를
    관측해서 남은 개수에 곱한 것이다. 근거가 이 실행 안에 있다.

    그래도 어긋날 수 있는 이유가 두 가지 있어 화면에서 '대략'이라고 말해야 한다:

    1. 디렉터리마다 크기가 천차만별이라 남은 것이 지나온 것보다 무거울 수 있다.
    2. **분모가 늘어난다.** 시간 초과로 디렉터리를 쪼개면 작업이 추가되므로
       남은 개수 자체가 도중에 커진다.

    표본이 모자라면(`ETA_MIN_INTERVALS` 미만) None을 준다. 근거 없는 숫자를
    내놓느니 '-'가 낫다.
    """

    if pending <= 0:
        return None
    intervals = completion_intervals(conn, account_id, kind, generation)
    if len(intervals) < ETA_MIN_INTERVALS:
        return None
    return _median(intervals) * pending


# -- 스캔이 왜 더딘지 진단 --------------------------------------------------
#
# "밤새 돌았는데 한 계정만 갔고 뭐가 됐는지 모르겠다"에 답하기 위한 집계다.
# 화면과 보고서는 "얼마나 갔나"를 보여 주지만, **왜 안 갔나**는 안 보여 준다.
# 그 답은 대부분 두 숫자에 있다 - 분할 횟수와 체크포인트 하나에 걸린 시간.

@dataclass
class AccountDiagnosis:
    """계정 하나의 스캔 진행 해부."""

    account_id: str
    generation: int
    total: int = 0
    done: int = 0
    split: int = 0
    error: int = 0
    pending: int = 0
    max_depth_seen: int = 0
    # 분할로 생겨난 체크포인트 수 (parent_id가 있는 것). 이 값이 크면 시간 초과가
    # 반복됐다는 뜻이고, 그때마다 그 서브트리를 **처음부터 다시 걸었다**.
    from_split: int = 0
    slowest_seconds: Optional[float] = None
    median_seconds: Optional[float] = None
    last_path: Optional[str] = None
    last_scanned_at: Optional[str] = None

    @property
    def split_ratio(self) -> float:
        return (self.from_split / self.total) if self.total else 0.0


def diagnose_account(
    conn: sqlite3.Connection, account_id: str, generation: int
) -> AccountDiagnosis:
    """진행 중인 세대의 체크포인트를 해부한다 (읽기 전용)."""

    result = AccountDiagnosis(account_id=account_id, generation=generation)

    rows = conn.execute(
        "SELECT status, depth, parent_id, path, scanned_at FROM scan_checkpoints "
        "WHERE account_id = ? AND kind = ? AND generation = ?",
        (account_id, BASELINE, generation),
    ).fetchall()
    if not rows:
        return result

    stamps = []
    for row in rows:
        result.total += 1
        status = row["status"]
        if status == STATUS_DONE:
            result.done += 1
        elif status == STATUS_SPLIT:
            result.split += 1
        elif status == STATUS_ERROR:
            result.error += 1
        elif status == STATUS_PENDING:
            result.pending += 1
        if row["parent_id"] is not None:
            result.from_split += 1
        depth = row["depth"] or 0
        result.max_depth_seen = max(result.max_depth_seen, depth)
        if row["scanned_at"]:
            try:
                stamps.append((datetime.fromisoformat(str(row["scanned_at"])), row["path"]))
            except (TypeError, ValueError):  # pragma: no cover
                pass

    if len(stamps) >= 2:
        stamps.sort()
        gaps = [
            (later[0] - earlier[0]).total_seconds()
            for earlier, later in zip(stamps, stamps[1:])
            if (later[0] - earlier[0]).total_seconds() > 0
        ]
        if gaps:
            ordered = sorted(gaps)
            middle = len(ordered) // 2
            result.median_seconds = (
                ordered[middle]
                if len(ordered) % 2
                else (ordered[middle - 1] + ordered[middle]) / 2
            )
            result.slowest_seconds = ordered[-1]
    if stamps:
        result.last_scanned_at = stamps[-1][0].isoformat()
        result.last_path = stamps[-1][1]
    return result
