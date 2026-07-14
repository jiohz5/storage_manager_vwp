from __future__ import annotations

import os
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple


SEARCH_DB_FILENAME = "search_index.db"
SEARCH_SIDECAR_SUFFIXES = ("", "-journal", "-wal", "-shm")
INDEX_ENTRY_BATCH_SIZE = 500
ASCII_UPPER_TO_LOWER = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)


@dataclass(frozen=True)
class SearchEntry:
    relative_path: str
    name: str
    extension: str
    entry_type: str


@dataclass(frozen=True)
class IndexRunResult:
    complete: bool
    cancelled: bool
    skipped: bool
    files_indexed: int
    dirs_indexed: int
    error: str = ""


def search_db_file(data_dir: Path) -> Path:
    return Path(data_dir) / SEARCH_DB_FILENAME


def search_index_disk_bytes(data_dir: Path) -> int:
    base = search_db_file(data_dir)
    total = 0
    for suffix in SEARCH_SIDECAR_SUFFIXES:
        path = Path(str(base) + suffix)
        try:
            total += path.stat().st_size
        except (FileNotFoundError, OSError):
            continue
    return total


def _timestamp(value: Optional[datetime] = None) -> str:
    return (value or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _safe_text(value: str) -> str:
    escaped = []
    for character in value:
        codepoint = ord(character)
        if character == "\\":
            escaped.append("\\\\")
        elif 0xDC80 <= codepoint <= 0xDCFF:
            escaped.append(f"\\x{codepoint - 0xDC00:02x}")
        elif 0xD800 <= codepoint <= 0xDFFF:
            escaped.append(f"\\u{codepoint:04x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _extension(name: str) -> str:
    suffix = Path(name).suffix
    return suffix[1:].casefold() if suffix.startswith(".") else suffix.casefold()


def _prefix_bounds(value: str) -> Tuple[str, Optional[str]]:
    lower = value.translate(ASCII_UPPER_TO_LOWER)
    for index in range(len(lower) - 1, -1, -1):
        codepoint = ord(lower[index])
        if codepoint < 0x10FFFF:
            return lower, lower[:index] + chr(codepoint + 1)
    return lower, None


def _safe_relative_path(value: str) -> str:
    return "/".join(_safe_text(part) for part in value.split("/"))


def _encode_task_path(value: str) -> bytes:
    return os.fsencode(value)


def _decode_task_path(value: object) -> str:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        return os.fsdecode(value)
    return str(value)


@contextmanager
def _same_device_scandir(directory: Path, root_device: int):
    descriptor = None
    scanner = None
    use_descriptor = (
        os.name != "nt"
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
    )
    try:
        if use_descriptor:
            descriptor = os.open(
                str(directory),
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            directory_stat = os.fstat(descriptor)
            if not stat.S_ISDIR(directory_stat.st_mode):
                raise OSError(f"Not a directory: {directory}")
            if int(directory_stat.st_dev) != root_device:
                raise OSError(f"Filesystem boundary blocked: {directory}")
            scanner = os.scandir(descriptor)
        else:
            if directory.is_symlink():
                raise OSError(f"Symlink traversal blocked: {directory}")
            directory_stat = os.stat(str(directory), follow_symlinks=False)
            if not stat.S_ISDIR(directory_stat.st_mode):
                raise OSError(f"Not a directory: {directory}")
            if int(directory_stat.st_dev) != root_device:
                raise OSError(f"Filesystem boundary blocked: {directory}")
            scanner = os.scandir(str(directory))
        yield scanner, use_descriptor
    finally:
        if scanner is not None:
            scanner.close()
        if descriptor is not None:
            os.close(descriptor)


class SearchIndex:
    def __init__(self, path: Path, timeout_seconds: float = 30.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        timeout_seconds = max(0.1, float(timeout_seconds))
        timeout_ms = max(1, int(timeout_seconds * 1000))
        self.conn = sqlite3.connect(str(self.path), timeout=timeout_seconds)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS search_entries (
              account_id TEXT NOT NULL,
              relative_path TEXT NOT NULL,
              basename TEXT NOT NULL COLLATE NOCASE,
              extension TEXT NOT NULL COLLATE NOCASE,
              entry_type TEXT NOT NULL,
              generation TEXT NOT NULL,
              PRIMARY KEY(account_id, relative_path)
            ) WITHOUT ROWID;

            CREATE TABLE IF NOT EXISTS search_scan_state (
              account_id TEXT PRIMARY KEY,
              account_path TEXT NOT NULL,
              generation TEXT NOT NULL,
              state TEXT NOT NULL,
              started_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              completed_at TEXT NOT NULL,
              last_incremental_at TEXT NOT NULL,
              files_indexed INTEGER NOT NULL DEFAULT 0,
              dirs_indexed INTEGER NOT NULL DEFAULT 0,
              error TEXT NOT NULL DEFAULT ''
            ) WITHOUT ROWID;

            CREATE TABLE IF NOT EXISTS search_scan_tasks (
              account_id TEXT NOT NULL,
              generation TEXT NOT NULL,
              relative_dir TEXT NOT NULL,
              status TEXT NOT NULL,
              PRIMARY KEY(account_id, generation, relative_dir)
            ) WITHOUT ROWID;

            CREATE INDEX IF NOT EXISTS idx_search_name
              ON search_entries(account_id, basename);
            CREATE INDEX IF NOT EXISTS idx_search_extension
              ON search_entries(account_id, extension, basename);
            CREATE INDEX IF NOT EXISTS idx_search_type
              ON search_entries(account_id, entry_type, basename);
            CREATE INDEX IF NOT EXISTS idx_search_tasks_pending
              ON search_scan_tasks(account_id, generation, status, relative_dir);
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def remove_account(self, account_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM search_scan_tasks WHERE account_id = ?",
                (account_id,),
            )
            self.conn.execute(
                "DELETE FROM search_entries WHERE account_id = ?",
                (account_id,),
            )
            self.conn.execute(
                "DELETE FROM search_scan_state WHERE account_id = ?",
                (account_id,),
            )

    def prune_accounts(self, valid_accounts: Dict[str, str]) -> List[str]:
        account_ids = {
            str(row[0])
            for row in self.conn.execute(
                "SELECT account_id FROM search_scan_state UNION SELECT DISTINCT account_id FROM search_entries"
            ).fetchall()
        }
        stored_paths = {
            str(row[0]): str(row[1])
            for row in self.conn.execute(
                "SELECT account_id, account_path FROM search_scan_state"
            ).fetchall()
        }
        removed = []
        for account_id in sorted(account_ids):
            expected = valid_accounts.get(account_id)
            stored = stored_paths.get(account_id, "")
            if expected is not None and (
                not stored
                or os.path.normcase(os.path.abspath(stored))
                == os.path.normcase(os.path.abspath(expected))
            ):
                continue
            self.remove_account(account_id)
            removed.append(account_id)
        return removed

    def summary(self) -> Dict[str, object]:
        row = self.conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT account_id) FROM search_entries"
        ).fetchone()
        return {
            "total_entries": int(row[0] or 0),
            "account_count": int(row[1] or 0),
            "db_bytes": search_index_disk_bytes(self.path.parent),
        }

    def account_status(self, account_id: str) -> Dict[str, object]:
        row = self.conn.execute(
            """
            SELECT account_path, generation, state, started_at, updated_at,
                   completed_at, last_incremental_at, files_indexed,
                   dirs_indexed, error
            FROM search_scan_state
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        entries = self.conn.execute(
            "SELECT COUNT(*) FROM search_entries WHERE account_id = ?",
            (account_id,),
        ).fetchone()[0]
        if row is None:
            return {
                "account_path": "",
                "generation": "",
                "state": "never",
                "started_at": "",
                "updated_at": "",
                "completed_at": "",
                "last_incremental_at": "",
                "files_indexed": 0,
                "dirs_indexed": 0,
                "error": "",
                "entries": int(entries or 0),
            }
        result = dict(row)
        result["files_indexed"] = int(result["files_indexed"] or 0)
        result["dirs_indexed"] = int(result["dirs_indexed"] or 0)
        result["entries"] = int(entries or 0)
        return result

    def search(
        self,
        account_id: str,
        name: str = "",
        *,
        mode: str = "prefix",
        extension: str = "",
        entry_type: str = "all",
        limit: int = 500,
    ) -> List[SearchEntry]:
        if mode not in {"exact", "prefix", "contains"}:
            raise ValueError("mode must be exact, prefix, or contains")
        if entry_type not in {"all", "file", "directory", "link", "other"}:
            raise ValueError("invalid entry_type")
        limit = max(1, min(int(limit), 500))
        clauses = ["account_id = ?"]
        values: List[object] = [account_id]
        query_name = name.strip()
        if query_name:
            if mode == "exact":
                clauses.append("basename = ? COLLATE NOCASE")
                values.append(query_name)
            elif mode == "prefix":
                lower_bound, upper_bound = _prefix_bounds(query_name)
                clauses.append("basename >= ? COLLATE NOCASE")
                values.append(lower_bound)
                if upper_bound is not None:
                    clauses.append("basename < ? COLLATE NOCASE")
                    values.append(upper_bound)
            else:
                escaped = (
                    query_name.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                pattern = f"%{escaped}%"
                clauses.append("basename LIKE ? ESCAPE '\\' COLLATE NOCASE")
                values.append(pattern)
        normalized_extension = extension.strip().lstrip(".").casefold()
        if normalized_extension:
            clauses.append("extension = ? COLLATE NOCASE")
            values.append(normalized_extension)
        if entry_type != "all":
            clauses.append("entry_type = ?")
            values.append(entry_type)
        values.append(limit)
        rows = self.conn.execute(
            """
            SELECT relative_path, basename, extension, entry_type
            FROM search_entries
            WHERE """
            + " AND ".join(clauses)
            + " ORDER BY basename COLLATE NOCASE, relative_path LIMIT ?",
            values,
        ).fetchall()
        return [
            SearchEntry(
                relative_path=str(row[0]),
                name=str(row[1]),
                extension=str(row[2]),
                entry_type=str(row[3]),
            )
            for row in rows
        ]

    def _begin_full_scan(
        self,
        account_id: str,
        account_path: Path,
        now: datetime,
        force: bool,
        full_scan_days: int,
    ) -> Tuple[str, bool]:
        path_text = str(account_path)
        status = self.account_status(account_id)
        active = status["state"] in {"running", "paused"}
        if active and status["account_path"] == path_text:
            generation = str(status["generation"])
            with self.conn:
                self.conn.execute(
                    "UPDATE search_scan_state SET state = 'running', updated_at = ? WHERE account_id = ?",
                    (_timestamp(now), account_id),
                )
            return generation, False

        completed_at = str(status.get("completed_at") or "")
        if (
            not force
            and status["state"] == "complete"
            and status["account_path"] == path_text
            and completed_at
        ):
            try:
                completed = datetime.strptime(completed_at, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                completed = now - timedelta(days=full_scan_days)
            if now < completed + timedelta(days=full_scan_days):
                return str(status["generation"]), True

        generation = uuid.uuid4().hex
        timestamp = _timestamp(now)
        path_changed = bool(
            status["account_path"] and status["account_path"] != path_text
        )
        with self.conn:
            self.conn.execute(
                "DELETE FROM search_scan_tasks WHERE account_id = ?",
                (account_id,),
            )
            if path_changed:
                self.conn.execute(
                    "DELETE FROM search_entries WHERE account_id = ?",
                    (account_id,),
                )
            self.conn.execute(
                """
                INSERT INTO search_scan_state(
                  account_id, account_path, generation, state, started_at,
                  updated_at, completed_at, last_incremental_at,
                  files_indexed, dirs_indexed, error
                ) VALUES(?, ?, ?, 'running', ?, ?, '', '', 0, 0, '')
                ON CONFLICT(account_id) DO UPDATE SET
                  account_path = excluded.account_path,
                  generation = excluded.generation,
                  state = 'running',
                  started_at = excluded.started_at,
                  updated_at = excluded.updated_at,
                  completed_at = '',
                  files_indexed = 0,
                  dirs_indexed = 0,
                  error = ''
                """,
                (account_id, path_text, generation, timestamp, timestamp),
            )
            self.conn.execute(
                """
                INSERT INTO search_scan_tasks(account_id, generation, relative_dir, status)
                VALUES(?, ?, '', 'pending')
                """,
                (account_id, generation),
            )
        return generation, False

    def _next_directory(
        self,
        account_id: str,
        generation: str,
    ) -> Optional[Tuple[str, object]]:
        row = self.conn.execute(
            """
            SELECT relative_dir
            FROM search_scan_tasks
            WHERE account_id = ? AND generation = ? AND status = 'pending'
            ORDER BY relative_dir
            LIMIT 1
            """,
            (account_id, generation),
        ).fetchone()
        return (_decode_task_path(row[0]), row[0]) if row is not None else None

    def _store_directory_batch(
        self,
        account_id: str,
        generation: str,
        rows: Sequence[Tuple[str, str, str, str, str]],
        child_dirs: Sequence[str],
        timestamp: str,
    ) -> None:
        if not rows and not child_dirs:
            return
        with self.conn:
            existing_values: Dict[str, Tuple[str, str, str]] = {}
            if rows:
                placeholders = ",".join("?" for _ in rows)
                existing_values = {
                    str(row[0]): (str(row[1]), str(row[2]), str(row[3]))
                    for row in self.conn.execute(
                        f"""
                        SELECT relative_path, basename, extension, entry_type
                        FROM search_entries
                        WHERE account_id = ? AND generation = ?
                          AND relative_path IN ({placeholders})
                        """,
                        [account_id, generation, *[row[0] for row in rows]],
                    ).fetchall()
                }
            rows_to_write = [
                row
                for row in rows
                if existing_values.get(row[0]) != (row[1], row[2], row[3])
            ]
            self.conn.executemany(
                """
                INSERT INTO search_entries(
                  account_id, relative_path, basename, extension,
                  entry_type, generation
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, relative_path) DO UPDATE SET
                  basename = excluded.basename,
                  extension = excluded.extension,
                  entry_type = excluded.entry_type,
                  generation = excluded.generation
                """,
                [
                    (account_id, relative_path, name, extension, entry_type, generation)
                    for relative_path, name, extension, entry_type, _ in rows_to_write
                ],
            )
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO search_scan_tasks(
                  account_id, generation, relative_dir, status
                ) VALUES(?, ?, ?, 'pending')
                """,
                [
                    (account_id, generation, _encode_task_path(child))
                    for child in child_dirs
                ],
            )
            file_delta = 0
            dir_delta = 0
            for relative_path, _name, _extension_value, entry_type, _generation in rows:
                previous = existing_values.get(relative_path)
                previous_type = previous[2] if previous is not None else None
                if previous is None:
                    if entry_type == "directory":
                        dir_delta += 1
                    else:
                        file_delta += 1
                elif previous_type != entry_type:
                    if previous_type == "directory":
                        dir_delta -= 1
                        file_delta += 1
                    elif entry_type == "directory":
                        file_delta -= 1
                        dir_delta += 1
            self.conn.execute(
                """
                UPDATE search_scan_state
                SET updated_at = ?,
                    files_indexed = files_indexed + ?,
                    dirs_indexed = dirs_indexed + ?
                WHERE account_id = ?
                """,
                (
                    timestamp,
                    file_delta,
                    dir_delta,
                    account_id,
                ),
            )

    def _finish_directory(
        self,
        account_id: str,
        generation: str,
        relative_dir_key: object,
        timestamp: str,
        error: str = "",
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE search_scan_tasks SET status = 'complete'
                WHERE account_id = ? AND generation = ? AND relative_dir = ?
                """,
                (account_id, generation, relative_dir_key),
            )
            self.conn.execute(
                """
                UPDATE search_scan_state
                SET updated_at = ?,
                    error = CASE WHEN ? = '' THEN error ELSE ? END
                WHERE account_id = ?
                """,
                (timestamp, error, error, account_id),
            )

    def _pause(self, account_id: str, timestamp: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE search_scan_state SET state = 'paused', updated_at = ? WHERE account_id = ?",
                (timestamp, account_id),
            )

    def _finish(self, account_id: str, generation: str, timestamp: str) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM search_entries WHERE account_id = ? AND generation <> ?",
                (account_id, generation),
            )
            file_count = self.conn.execute(
                """
                SELECT COUNT(*) FROM search_entries
                WHERE account_id = ? AND entry_type <> 'directory'
                """,
                (account_id,),
            ).fetchone()[0]
            dir_count = self.conn.execute(
                """
                SELECT COUNT(*) FROM search_entries
                WHERE account_id = ? AND entry_type = 'directory'
                """,
                (account_id,),
            ).fetchone()[0]
            self.conn.execute(
                "DELETE FROM search_scan_tasks WHERE account_id = ? AND generation = ?",
                (account_id, generation),
            )
            self.conn.execute(
                """
                UPDATE search_scan_state
                SET state = 'complete', updated_at = ?, completed_at = ?,
                    files_indexed = ?, dirs_indexed = ?
                WHERE account_id = ?
                """,
                (timestamp, timestamp, int(file_count), int(dir_count), account_id),
            )

    def upsert_changed_files(
        self,
        account_id: str,
        account_path: Path,
        records: Iterable[Tuple[str, int, float]],
        timestamp: Optional[str] = None,
    ) -> int:
        root = Path(account_path).expanduser().absolute()
        status = self.account_status(account_id)
        generation = str(status.get("generation") or f"incremental-{uuid.uuid4().hex}")
        entries: Dict[str, Tuple[str, str, str]] = {}
        for file_path, _size_bytes, _modified_at in records:
            candidate = Path(file_path).expanduser().absolute()
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            if not relative.parts:
                continue
            safe_parts = [_safe_text(part) for part in relative.parts]
            relative_path = "/".join(safe_parts)
            name = safe_parts[-1]
            entries[relative_path] = (name, _extension(name), "file")
            for end in range(1, len(safe_parts)):
                parent = "/".join(safe_parts[:end])
                entries[parent] = (safe_parts[end - 1], "", "directory")

        if not entries:
            return 0
        updated = timestamp or _timestamp()
        with self.conn:
            if status["state"] == "never" or status["account_path"] != str(root):
                if status["account_path"] and status["account_path"] != str(root):
                    self.conn.execute(
                        "DELETE FROM search_entries WHERE account_id = ?",
                        (account_id,),
                    )
                    self.conn.execute(
                        "DELETE FROM search_scan_tasks WHERE account_id = ?",
                        (account_id,),
                    )
                self.conn.execute(
                    """
                    INSERT INTO search_scan_state(
                      account_id, account_path, generation, state, started_at,
                      updated_at, completed_at, last_incremental_at,
                      files_indexed, dirs_indexed, error
                    ) VALUES(?, ?, ?, 'incremental', ?, ?, '', ?, 0, 0, '')
                    ON CONFLICT(account_id) DO UPDATE SET
                      account_path = excluded.account_path,
                      generation = excluded.generation,
                      state = 'incremental',
                      updated_at = excluded.updated_at,
                      last_incremental_at = excluded.last_incremental_at
                    """,
                    (account_id, str(root), generation, updated, updated, updated),
                )
            self.conn.executemany(
                """
                INSERT INTO search_entries(
                  account_id, relative_path, basename, extension,
                  entry_type, generation
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, relative_path) DO UPDATE SET
                  basename = excluded.basename,
                  extension = excluded.extension,
                  entry_type = excluded.entry_type,
                  generation = excluded.generation
                """,
                [
                    (account_id, path, values[0], values[1], values[2], generation)
                    for path, values in entries.items()
                ],
            )
            self.conn.execute(
                """
                UPDATE search_scan_state
                SET account_path = ?, updated_at = ?, last_incremental_at = ?
                WHERE account_id = ?
                """,
                (str(root), updated, updated, account_id),
            )
        return len(entries)


def run_full_index(
    index: SearchIndex,
    account_id: str,
    account_path: Path,
    stop_requested: Callable[[], bool] = lambda: False,
    now: Optional[datetime] = None,
    force: bool = False,
    full_scan_days: int = 7,
    entry_batch_size: int = INDEX_ENTRY_BATCH_SIZE,
) -> IndexRunResult:
    started = now or datetime.now()
    root = Path(account_path).expanduser().resolve()
    if not root.is_dir():
        return IndexRunResult(False, False, False, 0, 0, f"Directory not found: {root}")
    try:
        root_device = int(root.stat().st_dev)
    except OSError as exc:
        return IndexRunResult(False, False, False, 0, 0, str(exc))

    generation, skipped = index._begin_full_scan(
        account_id,
        root,
        started,
        force,
        full_scan_days,
    )
    if skipped:
        status = index.account_status(account_id)
        return IndexRunResult(
            True,
            False,
            True,
            int(status["files_indexed"]),
            int(status["dirs_indexed"]),
            str(status["error"]),
        )

    fixed_timestamp = _timestamp(started) if now is not None else None

    def current_timestamp() -> str:
        return fixed_timestamp or _timestamp()

    entry_batch_size = max(1, min(int(entry_batch_size), INDEX_ENTRY_BATCH_SIZE))
    while True:
        if stop_requested():
            index._pause(account_id, current_timestamp())
            status = index.account_status(account_id)
            return IndexRunResult(
                False,
                True,
                False,
                int(status["files_indexed"]),
                int(status["dirs_indexed"]),
                str(status["error"]),
            )
        directory_task = index._next_directory(account_id, generation)
        if directory_task is None:
            index._finish(account_id, generation, current_timestamp())
            status = index.account_status(account_id)
            return IndexRunResult(
                True,
                False,
                False,
                int(status["files_indexed"]),
                int(status["dirs_indexed"]),
                str(status["error"]),
            )
        relative_dir, relative_dir_key = directory_task

        directory = root / Path(relative_dir) if relative_dir else root
        rows: List[Tuple[str, str, str, str, str]] = []
        child_dirs: List[str] = []
        error = ""
        try:
            with _same_device_scandir(directory, root_device) as (
                entries,
                descriptor_relative,
            ):
                for entry in entries:
                    if stop_requested():
                        index._store_directory_batch(
                            account_id,
                            generation,
                            rows,
                            child_dirs,
                            current_timestamp(),
                        )
                        index._pause(account_id, current_timestamp())
                        status = index.account_status(account_id)
                        return IndexRunResult(
                            False,
                            True,
                            False,
                            int(status["files_indexed"]),
                            int(status["dirs_indexed"]),
                            str(status["error"]),
                        )
                    raw_name = entry.name
                    name = _safe_text(raw_name)
                    raw_relative_path = (
                        f"{relative_dir}/{raw_name}" if relative_dir else raw_name
                    )
                    relative_path = _safe_relative_path(raw_relative_path)
                    try:
                        if entry.is_symlink():
                            entry_type = "link"
                        elif entry.is_dir(follow_symlinks=False):
                            entry_type = "directory"
                            try:
                                child_stat = (
                                    entry.stat(follow_symlinks=False)
                                    if descriptor_relative
                                    else os.stat(
                                        str(directory / entry.name),
                                        follow_symlinks=False,
                                    )
                                )
                                same_device = int(child_stat.st_dev) == root_device
                            except OSError:
                                same_device = False
                            if same_device:
                                child_dirs.append(raw_relative_path)
                        elif entry.is_file(follow_symlinks=False):
                            entry_type = "file"
                        else:
                            entry_type = "other"
                    except OSError:
                        entry_type = "other"
                    rows.append(
                        (
                            relative_path,
                            name,
                            _extension(name) if entry_type != "directory" else "",
                            entry_type,
                            generation,
                        )
                    )
                    if len(rows) >= entry_batch_size:
                        index._store_directory_batch(
                            account_id,
                            generation,
                            rows,
                            child_dirs,
                            current_timestamp(),
                        )
                        rows = []
                        child_dirs = []
        except OSError as exc:
            error = f"{directory}: {exc}"
        index._store_directory_batch(
            account_id,
            generation,
            rows,
            child_dirs,
            current_timestamp(),
        )
        index._finish_directory(
            account_id,
            generation,
            relative_dir_key,
            current_timestamp(),
            error,
        )
