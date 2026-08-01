"""15분 주기 수집 결과(샘플)를 저장하는 SQLite 저장소.

CONCEPT.md 2-5 "자체 비대화 방지" 원칙에 따라, 이 모듈은 처음부터 보존 기간
기반 정리(prune)를 함께 제공한다. phase 1은 df/inode 표본만 다루므로 행 하나가
매우 작고(계정 수 x 하루 96회), 장기 보존을 하더라도 old 구현이 우려했던
"모니터링 툴이 스토리지를 잡아먹는" 문제가 생기기 어렵지만, 그래도 무한정
쌓이지 않도록 retention을 기본값으로 강제한다.

SQLite는 표준 라이브러리(`sqlite3`)만으로 충분해 폐쇄망 제약과 맞는다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    ok INTEGER NOT NULL,
    error_message TEXT,
    filesystem TEXT,
    mount_point TEXT,
    total_kb INTEGER,
    used_kb INTEGER,
    avail_kb INTEGER,
    byte_pct REAL,
    byte_tier TEXT,
    inode_total INTEGER,
    inode_used INTEGER,
    inode_avail INTEGER,
    inode_pct REAL,
    inode_tier TEXT,
    overall_tier TEXT
);
CREATE INDEX IF NOT EXISTS idx_samples_account_time
    ON samples(account_id, collected_at DESC);
"""

# 나중에 추가된 열. `CREATE TABLE IF NOT EXISTS`는 이미 있는 테이블을 바꾸지
# 않으므로, 기존 DB에는 ALTER TABLE로 따로 붙여 준다 (열 추가만 하고 기존
# 데이터는 건드리지 않으므로 되돌릴 필요가 없는 안전한 변경).
_ADDED_COLUMNS = (
    ("quota_used_kb", "INTEGER"),
    ("quota_limit_kb", "INTEGER"),
    ("quota_soft_limit_kb", "INTEGER"),
    ("quota_pct", "REAL"),
    ("quota_tier", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(samples)").fetchall()}
    for column, column_type in _ADDED_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE samples ADD COLUMN {column} {column_type}")
    conn.commit()


@dataclass
class SampleRecord:
    account_id: str
    collected_at: str  # ISO8601 UTC
    ok: bool
    error_message: Optional[str] = None
    filesystem: Optional[str] = None
    mount_point: Optional[str] = None
    total_kb: Optional[int] = None
    used_kb: Optional[int] = None
    avail_kb: Optional[int] = None
    byte_pct: Optional[float] = None
    byte_tier: str = "unknown"
    inode_total: Optional[int] = None
    inode_used: Optional[int] = None
    inode_avail: Optional[int] = None
    inode_pct: Optional[float] = None
    inode_tier: str = "unknown"
    overall_tier: str = "unknown"
    # quota는 선택 기능이라 설정하지 않으면 전부 None으로 남는다 (0으로 채우지
    # 않는다 - "모름"과 "0"은 다르다).
    quota_used_kb: Optional[int] = None
    quota_limit_kb: Optional[int] = None
    quota_soft_limit_kb: Optional[int] = None
    quota_pct: Optional[float] = None
    quota_tier: str = "unknown"
    id: Optional[int] = None


def db_path(data_dir: Path) -> Path:
    return data_dir / "samples.db"


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path(data_dir)), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    return conn


def insert_sample(conn: sqlite3.Connection, sample: SampleRecord) -> int:
    cursor = conn.execute(
        """
        INSERT INTO samples (
            account_id, collected_at, ok, error_message, filesystem, mount_point,
            total_kb, used_kb, avail_kb, byte_pct, byte_tier,
            inode_total, inode_used, inode_avail, inode_pct, inode_tier, overall_tier,
            quota_used_kb, quota_limit_kb, quota_soft_limit_kb, quota_pct, quota_tier
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sample.account_id,
            sample.collected_at,
            1 if sample.ok else 0,
            sample.error_message,
            sample.filesystem,
            sample.mount_point,
            sample.total_kb,
            sample.used_kb,
            sample.avail_kb,
            sample.byte_pct,
            sample.byte_tier,
            sample.inode_total,
            sample.inode_used,
            sample.inode_avail,
            sample.inode_pct,
            sample.inode_tier,
            sample.overall_tier,
            sample.quota_used_kb,
            sample.quota_limit_kb,
            sample.quota_soft_limit_kb,
            sample.quota_pct,
            sample.quota_tier,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _row_to_record(row: sqlite3.Row) -> SampleRecord:
    return SampleRecord(
        id=row["id"],
        account_id=row["account_id"],
        collected_at=row["collected_at"],
        ok=bool(row["ok"]),
        error_message=row["error_message"],
        filesystem=row["filesystem"],
        mount_point=row["mount_point"],
        total_kb=row["total_kb"],
        used_kb=row["used_kb"],
        avail_kb=row["avail_kb"],
        byte_pct=row["byte_pct"],
        byte_tier=row["byte_tier"],
        inode_total=row["inode_total"],
        inode_used=row["inode_used"],
        inode_avail=row["inode_avail"],
        inode_pct=row["inode_pct"],
        inode_tier=row["inode_tier"],
        overall_tier=row["overall_tier"],
        quota_used_kb=row["quota_used_kb"],
        quota_limit_kb=row["quota_limit_kb"],
        quota_soft_limit_kb=row["quota_soft_limit_kb"],
        quota_pct=row["quota_pct"],
        quota_tier=row["quota_tier"] or "unknown",
    )


def latest_samples(conn: sqlite3.Connection) -> Dict[str, SampleRecord]:
    """계정별 가장 최근 샘플 1건씩을 반환한다 (대시보드 표시용)."""

    rows = conn.execute(
        """
        SELECT s.* FROM samples s
        INNER JOIN (
            SELECT account_id, MAX(collected_at) AS max_ts
            FROM samples GROUP BY account_id
        ) latest
        ON s.account_id = latest.account_id AND s.collected_at = latest.max_ts
        """
    ).fetchall()
    result: Dict[str, SampleRecord] = {}
    for row in rows:
        result[row["account_id"]] = _row_to_record(row)
    return result


def history(conn: sqlite3.Connection, account_id: str, limit: int = 500) -> List[SampleRecord]:
    rows = conn.execute(
        """
        SELECT * FROM samples WHERE account_id = ?
        ORDER BY collected_at DESC LIMIT ?
        """,
        (account_id, limit),
    ).fetchall()
    return [_row_to_record(row) for row in rows]


def prune_old_samples(conn: sqlite3.Connection, retention_days: int, now: Optional[datetime] = None) -> int:
    """보존 기간보다 오래된 샘플을 지운다. 지워진 행 수를 반환한다."""

    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    cursor = conn.execute("DELETE FROM samples WHERE collected_at < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount
