"""파일/디렉터리 **이름** 검색 인덱스 (내용은 절대 저장하지 않는다).

DESIGN.md 1부 6절: 목적은 "어디 있는지 찾기"이지 내용 검색이 아니다. 그래서 저장
하는 것은 계정 루트 기준 상대 경로, 이름, 확장자, 종류(파일/디렉터리/링크)
뿐이다. 파일을 열지 않으므로 읽기 전용 원칙도 자연스럽게 지켜진다.

설계 메모:
- **별도 DB 파일**(`search_index.db`)을 쓴다. 이름 인덱스는 항목 수가 수백만
  단위로 갈 수 있어 15분 표본이나 스캔 체크포인트와 크기 특성이 완전히
  다르다. 파일이 분리돼 있으면 "검색만 끄고 공간을 회수"하는 것도 쉽다.
- **계정별 opt-in**(`Account.search_indexing`). 기본은 꺼짐.
- **재개 가능**: 디렉터리 단위로 커밋하며, 한 디렉터리 안에서도 일정 개수마다
  커밋해 대형 flat 디렉터리에서 메모리가 계속 늘지 않게 한다.
- 심볼릭 링크를 따라가지 않고, 다른 파일시스템으로 넘어가지 않으며, 계정 루트
  바로 아래의 `.snapshot`은 제외한다 (스냅샷 사본을 실데이터로 세지 않기 위함).
- 인덱싱을 끄거나 계정을 지우면 해당 행을 정리한다. SQLite는 지운 page를
  재사용하므로 파일 크기가 즉시 줄지 않을 수 있다 - 그래서 GUI에는 항목 수와
  **파일 실제 크기**를 함께 보여준다.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

KIND_FILE = "file"
KIND_DIR = "dir"
KIND_LINK = "link"

# 계정 루트 바로 아래에서만 제외하는 디렉터리 (중첩된 같은 이름은 일반 데이터).
ROOT_EXCLUDED_NAMES = {".snapshot"}

# 한 번에 커밋할 항목 수 - 대형 flat 디렉터리에서 메모리가 무한히 늘지 않도록.
COMMIT_BATCH = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS search_entries (
    account_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    name TEXT NOT NULL,
    extension TEXT,
    kind TEXT NOT NULL,
    indexed_at TEXT NOT NULL,
    PRIMARY KEY (account_id, relative_path)
);
CREATE INDEX IF NOT EXISTS idx_search_name ON search_entries(account_id, name);
CREATE INDEX IF NOT EXISTS idx_search_ext ON search_entries(account_id, extension);

CREATE TABLE IF NOT EXISTS search_state (
    account_id TEXT PRIMARY KEY,
    account_path TEXT,
    last_full_index_at TEXT,
    entry_count INTEGER
);
"""


@dataclass
class SearchHit:
    relative_path: str
    name: str
    extension: Optional[str]
    kind: str


def db_path(data_dir: Path) -> Path:
    return data_dir / "search_index.db"


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path(data_dir)), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _classify(entry: os.DirEntry) -> str:
    if entry.is_symlink():
        return KIND_LINK
    if entry.is_dir(follow_symlinks=False):
        return KIND_DIR
    return KIND_FILE


def _same_filesystem(directory: Path, root_device: int) -> bool:
    """`directory`가 계정 루트와 같은 파일시스템인지.

    `os.DirEntry.stat()`을 쓰지 않고 `os.stat`을 직접 호출한다 - Windows에서는
    DirEntry가 디렉터리 항목의 캐시 정보를 돌려주면서 `st_dev`를 0으로 채우기
    때문에, 그대로 비교하면 모든 하위 디렉터리가 "다른 파일시스템"으로 잘못
    판정된다. 디렉터리에 대해서만 호출하므로 syscall 비용도 크지 않다.

    `st_dev`를 알 수 없는 플랫폼(0)에서는 경계 검사를 포기하고 진행한다 -
    검사할 수 없다고 순회를 멈추면 인덱스가 통째로 비어버린다."""

    try:
        device = os.stat(directory, follow_symlinks=False).st_dev
    except OSError:
        return False
    if not device or not root_device:
        return True
    return device == root_device


def walk_account(
    account_path: Path, should_stop=None
) -> Iterator[tuple]:
    """계정 루트 아래를 훑으며 (상대경로, 이름, 확장자, 종류)를 내놓는다.

    심볼릭 링크는 따라가지 않고, 다른 파일시스템으로 넘어가지 않는다. 계정 루트
    바로 아래의 `.snapshot`만 제외하고, 그 아래에 중첩된 같은 이름은 일반
    데이터로 취급한다."""

    try:
        root_device = account_path.stat().st_dev
    except OSError:
        return

    stack: List[Path] = [account_path]
    while stack:
        if should_stop is not None and should_stop():
            return
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    entry_path = Path(entry.path)
                    try:
                        relative = entry_path.relative_to(account_path)
                    except ValueError:  # pragma: no cover - 방어적 처리
                        continue

                    # 계정 루트 바로 아래의 .snapshot만 제외한다.
                    if len(relative.parts) == 1 and entry.name in ROOT_EXCLUDED_NAMES:
                        continue

                    kind = _classify(entry)
                    extension = entry_path.suffix.lstrip(".").lower() or None
                    yield (relative.as_posix(), entry.name, extension, kind)

                    if kind == KIND_DIR and _same_filesystem(entry_path, root_device):
                        stack.append(entry_path)
        except OSError:
            # 권한 없는 디렉터리 등은 조용히 건너뛴다 - 한 곳 때문에 전체
            # 인덱싱이 멈추면 안 된다.
            continue


def index_account(
    conn: sqlite3.Connection,
    account_id: str,
    account_path: Path,
    should_stop=None,
) -> int:
    """계정 하나를 인덱싱하고 저장한 항목 수를 반환한다.

    중간에 멈춰도(should_stop) 그때까지 커밋한 항목은 남는다. 전체를 다시 훑는
    방식이므로 이름이 바뀌거나 지워진 항목은 이 함수가 끝까지 완주할 때 정리
    된다(오래된 indexed_at 행 삭제)."""

    started_at = _utc_now()
    batch: List[tuple] = []
    count = 0

    for relative_path, name, extension, kind in walk_account(account_path, should_stop):
        batch.append((account_id, relative_path, name, extension, kind, started_at))
        if len(batch) >= COMMIT_BATCH:
            _flush(conn, batch)
            count += len(batch)
            batch = []

    if batch:
        _flush(conn, batch)
        count += len(batch)

    interrupted = should_stop is not None and should_stop()
    if not interrupted:
        # 완주했을 때만 정리한다 - 중간에 멈춘 상태에서 지우면 아직 못 본
        # 항목까지 사라진다.
        conn.execute(
            "DELETE FROM search_entries WHERE account_id = ? AND indexed_at < ?",
            (account_id, started_at),
        )
        conn.execute(
            "INSERT INTO search_state (account_id, account_path, last_full_index_at, entry_count) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(account_id) DO UPDATE SET "
            "account_path = excluded.account_path, "
            "last_full_index_at = excluded.last_full_index_at, "
            "entry_count = excluded.entry_count",
            (account_id, str(account_path), started_at, count),
        )
    conn.commit()
    return count


def _flush(conn: sqlite3.Connection, batch: List[tuple]) -> None:
    conn.executemany(
        "INSERT INTO search_entries "
        "(account_id, relative_path, name, extension, kind, indexed_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(account_id, relative_path) DO UPDATE SET "
        "name = excluded.name, extension = excluded.extension, "
        "kind = excluded.kind, indexed_at = excluded.indexed_at",
        batch,
    )
    conn.commit()


MODE_EXACT = "exact"
MODE_PREFIX = "prefix"
MODE_CONTAINS = "contains"


def search(
    conn: sqlite3.Connection,
    account_id: str,
    query: str,
    mode: str = MODE_EXACT,
    kind: Optional[str] = None,
    extension: Optional[str] = None,
    limit: int = 500,
) -> List[SearchHit]:
    """이름으로 검색한다. `contains`는 인덱스를 못 써서 느릴 수 있다."""

    clauses = ["account_id = ?"]
    params: List = [account_id]

    if query:
        if mode == MODE_PREFIX:
            clauses.append("name LIKE ? ESCAPE '\\'")
            params.append(_escape_like(query) + "%")
        elif mode == MODE_CONTAINS:
            clauses.append("name LIKE ? ESCAPE '\\'")
            params.append("%" + _escape_like(query) + "%")
        else:
            clauses.append("name = ?")
            params.append(query)

    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if extension:
        clauses.append("extension = ?")
        params.append(extension.lstrip(".").lower())

    params.append(limit)
    rows = conn.execute(
        f"SELECT relative_path, name, extension, kind FROM search_entries "
        f"WHERE {' AND '.join(clauses)} ORDER BY relative_path LIMIT ?",
        params,
    ).fetchall()
    return [
        SearchHit(
            relative_path=row["relative_path"],
            name=row["name"],
            extension=row["extension"],
            kind=row["kind"],
        )
        for row in rows
    ]


def _escape_like(value: str) -> str:
    """LIKE 와일드카드를 이스케이프한다 - 사용자가 친 `%`가 전체 검색이 되면
    검색이 아니라 전량 조회가 되어버린다."""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def entry_count(conn: sqlite3.Connection, account_id: Optional[str] = None) -> int:
    if account_id:
        row = conn.execute(
            "SELECT COUNT(*) FROM search_entries WHERE account_id = ?", (account_id,)
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM search_entries").fetchone()
    return int(row[0])


def clear_account(conn: sqlite3.Connection, account_id: str) -> int:
    """계정의 인덱스를 지운다 (검색을 끄거나 계정을 삭제할 때).

    모니터링 대상 파일은 건드리지 않는다 - 지우는 것은 이 앱이 만든 인덱스
    행뿐이다."""

    cursor = conn.execute("DELETE FROM search_entries WHERE account_id = ?", (account_id,))
    conn.execute("DELETE FROM search_state WHERE account_id = ?", (account_id,))
    conn.commit()
    return cursor.rowcount


def prune_orphans(conn: sqlite3.Connection, active_account_ids: List[str]) -> int:
    """설정에 없는(삭제된) 계정의 인덱스를 정리한다."""

    rows = conn.execute("SELECT DISTINCT account_id FROM search_entries").fetchall()
    removed = 0
    for row in rows:
        if row["account_id"] not in active_account_ids:
            removed += clear_account(conn, row["account_id"])
    return removed


def db_size_bytes(data_dir: Path) -> int:
    """`search_index.db`와 sidecar(-wal/-shm) 실제 크기 합계.

    SQLite는 삭제한 page를 재사용하므로 항목을 지워도 파일 크기가 바로 줄지
    않는다. 그래서 항목 수와 실제 크기를 둘 다 보여줘야 오해가 없다."""

    total = 0
    base = db_path(data_dir)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(base) + suffix)
        if candidate.exists():
            total += candidate.stat().st_size
    return total
