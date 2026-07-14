from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class CapacitySampleRecord:
    ts: str
    account_id: str
    account_name: str
    account_path: str
    fs_key: str
    fs_name: str
    total_kb: int
    used_kb: int
    avail_kb: int
    use_pct: int
    total_inodes: Optional[int] = None
    used_inodes: Optional[int] = None
    avail_inodes: Optional[int] = None
    inode_use_pct: Optional[int] = None
    quota_used_kb: Optional[int] = None
    quota_limit_kb: Optional[int] = None
    quota_use_pct: Optional[int] = None
    quota_error: str = ""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(str(path), timeout=30.0)
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.conn.execute("PRAGMA synchronous=FULL")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
              id INTEGER PRIMARY KEY,
              ts TEXT NOT NULL,
              day TEXT NOT NULL,
              account_id TEXT NOT NULL DEFAULT '',
              account_name TEXT NOT NULL,
              account_path TEXT NOT NULL,
              fs_name TEXT,
              total_kb INTEGER,
              used_kb INTEGER,
              avail_kb INTEGER,
              use_pct INTEGER,
              total_inodes INTEGER,
              used_inodes INTEGER,
              avail_inodes INTEGER,
              inode_use_pct INTEGER,
              quota_used_kb INTEGER,
              quota_limit_kb INTEGER,
              quota_use_pct INTEGER,
              quota_error TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS top_items (
              id INTEGER PRIMARY KEY,
              ts TEXT NOT NULL,
              day TEXT NOT NULL,
              account_id TEXT NOT NULL DEFAULT '',
              account_name TEXT NOT NULL,
              item_path TEXT NOT NULL,
              size_kb INTEGER NOT NULL,
              rank_no INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS current_inventory (
              account_id TEXT NOT NULL,
              item_path TEXT NOT NULL,
              size_kb INTEGER NOT NULL,
              scan_day TEXT NOT NULL,
              PRIMARY KEY(account_id, item_path)
            );

            CREATE TABLE IF NOT EXISTS inventory_scans (
              account_id TEXT PRIMARY KEY,
              scan_day TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS growth_items (
              id INTEGER PRIMARY KEY,
              ts TEXT NOT NULL,
              day TEXT NOT NULL,
              account_id TEXT NOT NULL,
              account_name TEXT NOT NULL,
              item_path TEXT NOT NULL,
              delta_kb INTEGER NOT NULL,
              rank_no INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS detail_scan_state (
              account_id TEXT PRIMARY KEY,
              account_path TEXT NOT NULL,
              cycle_id TEXT NOT NULL,
              started_ts TEXT NOT NULL,
              updated_ts TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS detail_scan_tasks (
              id INTEGER PRIMARY KEY,
              account_id TEXT NOT NULL,
              cycle_id TEXT NOT NULL,
              top_path TEXT NOT NULL,
              task_path TEXT NOT NULL,
              task_kind TEXT NOT NULL,
              depth INTEGER NOT NULL,
              status TEXT NOT NULL,
              size_kb INTEGER NOT NULL DEFAULT 0,
              UNIQUE(account_id, cycle_id, task_path, task_kind)
            );

            CREATE TABLE IF NOT EXISTS activity_state (
              account_id TEXT PRIMARY KEY,
              last_success_ts TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activity_items (
              id INTEGER PRIMARY KEY,
              day TEXT NOT NULL,
              ts TEXT NOT NULL,
              account_id TEXT NOT NULL,
              account_name TEXT NOT NULL,
              item_path TEXT NOT NULL,
              changed_bytes INTEGER NOT NULL,
              file_count INTEGER NOT NULL,
              newest_mtime REAL NOT NULL,
              rank_no INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inventory_item_state (
              account_id TEXT NOT NULL,
              item_path TEXT NOT NULL,
              first_seen_day TEXT NOT NULL,
              last_seen_day TEXT NOT NULL,
              PRIMARY KEY(account_id, item_path)
            );

            CREATE TABLE IF NOT EXISTS item_activity_state (
              account_id TEXT NOT NULL,
              item_path TEXT NOT NULL,
              last_activity_day TEXT NOT NULL,
              last_changed_bytes INTEGER NOT NULL,
              PRIMARY KEY(account_id, item_path)
            );

            CREATE TABLE IF NOT EXISTS capacity_samples (
              id INTEGER PRIMARY KEY,
              ts TEXT NOT NULL,
              account_id TEXT NOT NULL,
              account_name TEXT NOT NULL,
              account_path TEXT NOT NULL,
              fs_key TEXT NOT NULL,
              fs_name TEXT NOT NULL,
              total_kb INTEGER NOT NULL,
              used_kb INTEGER NOT NULL,
              avail_kb INTEGER NOT NULL,
              use_pct INTEGER NOT NULL,
              total_inodes INTEGER,
              used_inodes INTEGER,
              avail_inodes INTEGER,
              inode_use_pct INTEGER,
              quota_used_kb INTEGER,
              quota_limit_kb INTEGER,
              quota_use_pct INTEGER,
              quota_error TEXT NOT NULL DEFAULT '',
              UNIQUE(ts, account_id)
            );
            """
        )
        self._add_column_if_missing("snapshots", "account_id", "TEXT NOT NULL DEFAULT ''")
        self._add_column_if_missing("snapshots", "total_inodes", "INTEGER")
        self._add_column_if_missing("snapshots", "used_inodes", "INTEGER")
        self._add_column_if_missing("snapshots", "avail_inodes", "INTEGER")
        self._add_column_if_missing("snapshots", "inode_use_pct", "INTEGER")
        self._add_column_if_missing("snapshots", "quota_used_kb", "INTEGER")
        self._add_column_if_missing("snapshots", "quota_limit_kb", "INTEGER")
        self._add_column_if_missing("snapshots", "quota_use_pct", "INTEGER")
        self._add_column_if_missing("snapshots", "quota_error", "TEXT NOT NULL DEFAULT ''")
        self._add_column_if_missing("top_items", "account_id", "TEXT NOT NULL DEFAULT ''")
        self._deduplicate_legacy_snapshots()
        self.conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_snap_day_account
              ON snapshots(day, account_id);
            CREATE INDEX IF NOT EXISTS idx_snap_account_ts
              ON snapshots(account_id, ts);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_snap_daily_source
              ON snapshots(day, account_id, source)
              WHERE account_id <> '';
            CREATE INDEX IF NOT EXISTS idx_top_day_account
              ON top_items(day, account_id);
            CREATE INDEX IF NOT EXISTS idx_growth_day_account
              ON growth_items(day, account_id);
            CREATE INDEX IF NOT EXISTS idx_detail_tasks_pending
              ON detail_scan_tasks(account_id, cycle_id, status, id);
            CREATE INDEX IF NOT EXISTS idx_activity_day_account
              ON activity_items(day, account_id, rank_no);
            CREATE INDEX IF NOT EXISTS idx_capacity_account_ts
              ON capacity_samples(account_id, ts);
            CREATE INDEX IF NOT EXISTS idx_capacity_fs_ts
              ON capacity_samples(fs_key, ts);
            """
        )
        self.conn.commit()

    def _add_column_if_missing(self, table: str, column: str, sql_type: str) -> None:
        columns = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    def _deduplicate_legacy_snapshots(self) -> None:
        self.conn.execute(
            """
            DELETE FROM snapshots
            WHERE id NOT IN (
              SELECT MAX(id)
              FROM snapshots
              GROUP BY day,
                       CASE WHEN account_id <> '' THEN account_id ELSE account_name || account_path END,
                       source
            )
            """
        )

    def close(self) -> None:
        self.conn.close()

    def backfill_account(self, account_id: str, account_name: str, account_path: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE snapshots
                SET account_id = ?
                WHERE account_id = '' AND account_name = ? AND account_path = ?
                """,
                (account_id, account_name, account_path),
            )
            self.conn.execute(
                """
                UPDATE top_items
                SET account_id = ?
                WHERE account_id = '' AND account_name = ?
                """,
                (account_id, account_name),
            )

    def upsert_snapshot(
        self,
        ts: str,
        day: str,
        account_id: str,
        account_name: str,
        account_path: str,
        fs_name: str,
        total_kb: int,
        used_kb: int,
        avail_kb: int,
        use_pct: int,
        source: str,
        total_inodes: Optional[int] = None,
        used_inodes: Optional[int] = None,
        avail_inodes: Optional[int] = None,
        inode_use_pct: Optional[int] = None,
        quota_used_kb: Optional[int] = None,
        quota_limit_kb: Optional[int] = None,
        quota_use_pct: Optional[int] = None,
        quota_error: str = "",
    ) -> None:
        values = (
            ts,
            account_name,
            account_path,
            fs_name,
            total_kb,
            used_kb,
            avail_kb,
            use_pct,
            total_inodes,
            used_inodes,
            avail_inodes,
            inode_use_pct,
            quota_used_kb,
            quota_limit_kb,
            quota_use_pct,
            quota_error,
            day,
            account_id,
            source,
        )
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE snapshots
                SET ts = ?, account_name = ?, account_path = ?, fs_name = ?,
                    total_kb = ?, used_kb = ?, avail_kb = ?, use_pct = ?,
                    total_inodes = ?, used_inodes = ?, avail_inodes = ?,
                    inode_use_pct = ?, quota_used_kb = ?, quota_limit_kb = ?,
                    quota_use_pct = ?, quota_error = ?
                WHERE day = ? AND account_id = ? AND source = ?
                """,
                values,
            )
            if cursor.rowcount == 0:
                self.conn.execute(
                    """
                    INSERT INTO snapshots(
                      ts, day, account_id, account_name, account_path, fs_name,
                      total_kb, used_kb, avail_kb, use_pct,
                      total_inodes, used_inodes, avail_inodes, inode_use_pct,
                      quota_used_kb, quota_limit_kb, quota_use_pct, quota_error, source
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        day,
                        account_id,
                        account_name,
                        account_path,
                        fs_name,
                        total_kb,
                        used_kb,
                        avail_kb,
                        use_pct,
                        total_inodes,
                        used_inodes,
                        avail_inodes,
                        inode_use_pct,
                        quota_used_kb,
                        quota_limit_kb,
                        quota_use_pct,
                        quota_error,
                        source,
                    ),
                )

    def replace_top_items(
        self,
        ts: str,
        day: str,
        account_id: str,
        account_name: str,
        rows: Iterable[Tuple[str, int, int]],
    ) -> None:
        materialized = list(rows)
        with self.conn:
            self.conn.execute(
                "DELETE FROM top_items WHERE day = ? AND account_id = ?",
                (day, account_id),
            )
            self.conn.executemany(
                """
                INSERT INTO top_items(
                  ts, day, account_id, account_name, item_path, size_kb, rank_no
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (ts, day, account_id, account_name, item_path, size_kb, rank_no)
                    for item_path, size_kb, rank_no in materialized
                ],
            )

    def current_inventory(self, account_id: str) -> List[Tuple[str, int]]:
        cursor = self.conn.execute(
            """
            SELECT item_path, size_kb
            FROM current_inventory
            WHERE account_id = ?
            """,
            (account_id,),
        )
        return [(row[0], int(row[1])) for row in cursor.fetchall()]

    def has_inventory(self, account_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM inventory_scans WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        return row is not None

    def replace_inventory(
        self,
        account_id: str,
        scan_day: str,
        rows: Iterable[Tuple[str, int]],
    ) -> None:
        materialized = list(rows)
        with self.conn:
            self.conn.execute(
                "DELETE FROM current_inventory WHERE account_id = ?",
                (account_id,),
            )
            self.conn.executemany(
                """
                INSERT INTO current_inventory(account_id, item_path, size_kb, scan_day)
                VALUES(?, ?, ?, ?)
                """,
                [
                    (account_id, item_path, size_kb, scan_day)
                    for item_path, size_kb in materialized
                ],
            )
            self.conn.execute(
                """
                INSERT INTO inventory_scans(account_id, scan_day)
                VALUES(?, ?)
                ON CONFLICT(account_id) DO UPDATE SET scan_day = excluded.scan_day
                """,
                (account_id, scan_day),
            )
            self.conn.executemany(
                """
                INSERT INTO inventory_item_state(
                  account_id, item_path, first_seen_day, last_seen_day
                ) VALUES(?, ?, ?, ?)
                ON CONFLICT(account_id, item_path) DO UPDATE SET
                  last_seen_day = excluded.last_seen_day
                """,
                [
                    (account_id, item_path, scan_day, scan_day)
                    for item_path, _ in materialized
                ],
            )

    def replace_growth_items(
        self,
        ts: str,
        day: str,
        account_id: str,
        account_name: str,
        rows: Iterable[Tuple[str, int, int]],
    ) -> None:
        materialized = list(rows)
        with self.conn:
            self.conn.execute(
                "DELETE FROM growth_items WHERE day = ? AND account_id = ?",
                (day, account_id),
            )
            self.conn.executemany(
                """
                INSERT INTO growth_items(
                  ts, day, account_id, account_name, item_path, delta_kb, rank_no
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (ts, day, account_id, account_name, item_path, delta_kb, rank_no)
                    for item_path, delta_kb, rank_no in materialized
                ],
            )

    def detail_scan_state(self, account_id: str):
        return self.conn.execute(
            """
            SELECT account_path, cycle_id, started_ts, updated_ts
            FROM detail_scan_state
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()

    def begin_detail_scan(
        self,
        account_id: str,
        account_path: str,
        cycle_id: str,
        timestamp: str,
        tasks: Iterable[Tuple[str, str, str, int, str, int]],
    ) -> None:
        materialized = list(tasks)
        with self.conn:
            self.conn.execute(
                "DELETE FROM detail_scan_tasks WHERE account_id = ?",
                (account_id,),
            )
            self.conn.execute(
                """
                INSERT INTO detail_scan_state(
                  account_id, account_path, cycle_id, started_ts, updated_ts
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                  account_path = excluded.account_path,
                  cycle_id = excluded.cycle_id,
                  started_ts = excluded.started_ts,
                  updated_ts = excluded.updated_ts
                """,
                (account_id, account_path, cycle_id, timestamp, timestamp),
            )
            self.conn.executemany(
                """
                INSERT INTO detail_scan_tasks(
                  account_id, cycle_id, top_path, task_path, task_kind,
                  depth, status, size_kb
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        account_id,
                        cycle_id,
                        top_path,
                        task_path,
                        task_kind,
                        depth,
                        status,
                        size_kb,
                    )
                    for top_path, task_path, task_kind, depth, status, size_kb in materialized
                ],
            )

    def reconcile_detail_scan_roots(
        self,
        account_id: str,
        cycle_id: str,
        timestamp: str,
        tasks: Iterable[Tuple[str, str, str, int, str, int]],
    ) -> bool:
        """Add new top-level entries and discard entries removed during a long scan."""
        desired = {task[0]: task for task in tasks}
        rows = self.conn.execute(
            """
            SELECT top_path, task_path, task_kind, depth, status, size_kb
            FROM detail_scan_tasks
            WHERE account_id = ? AND cycle_id = ?
            """,
            (account_id, cycle_id),
        ).fetchall()
        existing = {}
        for row in rows:
            existing.setdefault(row[0], []).append(row[1:])

        changed = False
        with self.conn:
            for top_path in existing.keys() - desired.keys():
                self.conn.execute(
                    """
                    DELETE FROM detail_scan_tasks
                    WHERE account_id = ? AND cycle_id = ? AND top_path = ?
                    """,
                    (account_id, cycle_id, top_path),
                )
                changed = True

            for top_path, task in desired.items():
                current_rows = existing.get(top_path, [])
                desired_row = task[1:]
                if not current_rows:
                    self.conn.execute(
                        """
                        INSERT INTO detail_scan_tasks(
                          account_id, cycle_id, top_path, task_path, task_kind,
                          depth, status, size_kb
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (account_id, cycle_id, *task),
                    )
                    changed = True
                    continue

                # Top-level regular files are cheap to restat and may grow while the
                # multi-night directory scan is in progress.
                if task[2] == "direct" and current_rows != [desired_row]:
                    self.conn.execute(
                        """
                        DELETE FROM detail_scan_tasks
                        WHERE account_id = ? AND cycle_id = ? AND top_path = ?
                        """,
                        (account_id, cycle_id, top_path),
                    )
                    self.conn.execute(
                        """
                        INSERT INTO detail_scan_tasks(
                          account_id, cycle_id, top_path, task_path, task_kind,
                          depth, status, size_kb
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (account_id, cycle_id, *task),
                    )
                    changed = True

            if changed:
                self.conn.execute(
                    "UPDATE detail_scan_state SET updated_ts = ? WHERE account_id = ?",
                    (timestamp, account_id),
                )
        return changed

    def next_detail_task(self, account_id: str, cycle_id: str):
        return self.conn.execute(
            """
            SELECT id, top_path, task_path, depth
            FROM detail_scan_tasks
            WHERE account_id = ? AND cycle_id = ?
              AND task_kind = 'scan' AND status = 'pending'
            ORDER BY depth ASC, id ASC
            LIMIT 1
            """,
            (account_id, cycle_id),
        ).fetchone()

    def complete_detail_task(
        self,
        task_id: int,
        account_id: str,
        size_kb: int,
        timestamp: str,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE detail_scan_tasks
                SET status = 'complete', size_kb = ?
                WHERE id = ? AND account_id = ?
                """,
                (size_kb, task_id, account_id),
            )
            self.conn.execute(
                "UPDATE detail_scan_state SET updated_ts = ? WHERE account_id = ?",
                (timestamp, account_id),
            )

    def split_detail_task(
        self,
        task_id: int,
        account_id: str,
        cycle_id: str,
        timestamp: str,
        tasks: Iterable[Tuple[str, str, str, int, str, int]],
    ) -> None:
        materialized = list(tasks)
        with self.conn:
            self.conn.execute(
                "DELETE FROM detail_scan_tasks WHERE id = ? AND account_id = ?",
                (task_id, account_id),
            )
            self.conn.executemany(
                """
                INSERT OR IGNORE INTO detail_scan_tasks(
                  account_id, cycle_id, top_path, task_path, task_kind,
                  depth, status, size_kb
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        account_id,
                        cycle_id,
                        top_path,
                        task_path,
                        task_kind,
                        depth,
                        status,
                        size_kb,
                    )
                    for top_path, task_path, task_kind, depth, status, size_kb in materialized
                ],
            )
            self.conn.execute(
                "UPDATE detail_scan_state SET updated_ts = ? WHERE account_id = ?",
                (timestamp, account_id),
            )

    def detail_scan_progress(self, account_id: str, cycle_id: str) -> Tuple[int, int]:
        row = self.conn.execute(
            """
            SELECT
              SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END),
              COUNT(*)
            FROM detail_scan_tasks
            WHERE account_id = ? AND cycle_id = ?
            """,
            (account_id, cycle_id),
        ).fetchone()
        return int(row[0] or 0), int(row[1] or 0)

    def detail_scan_items(self, account_id: str, cycle_id: str):
        cursor = self.conn.execute(
            """
            SELECT top_path, SUM(size_kb)
            FROM detail_scan_tasks
            WHERE account_id = ? AND cycle_id = ? AND status = 'complete'
            GROUP BY top_path
            ORDER BY SUM(size_kb) DESC
            """,
            (account_id, cycle_id),
        )
        return [(row[0], int(row[1])) for row in cursor.fetchall()]

    def finish_detail_scan(self, account_id: str, cycle_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM detail_scan_tasks WHERE account_id = ? AND cycle_id = ?",
                (account_id, cycle_id),
            )
            self.conn.execute(
                "DELETE FROM detail_scan_state WHERE account_id = ? AND cycle_id = ?",
                (account_id, cycle_id),
            )

    def cancel_detail_scan(self, account_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM detail_scan_tasks WHERE account_id = ?",
                (account_id,),
            )
            self.conn.execute(
                "DELETE FROM detail_scan_state WHERE account_id = ?",
                (account_id,),
            )

    def last_activity_ts(self, account_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT last_success_ts FROM activity_state WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        return row[0] if row else None

    def inventory_scan_day(self, account_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT scan_day FROM inventory_scans WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        return row[0] if row else None

    def set_activity_cursor(self, account_id: str, timestamp: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO activity_state(account_id, last_success_ts)
                VALUES(?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                  last_success_ts = excluded.last_success_ts
                """,
                (account_id, timestamp),
            )

    def replace_activity_items(
        self,
        day: str,
        timestamp: str,
        account_id: str,
        account_name: str,
        rows: Iterable[Tuple[str, int, int, float, int]],
    ) -> None:
        materialized = list(rows)
        with self.conn:
            self.conn.execute(
                "DELETE FROM activity_items WHERE day = ? AND account_id = ?",
                (day, account_id),
            )
            self.conn.executemany(
                """
                INSERT INTO activity_items(
                  day, ts, account_id, account_name, item_path,
                  changed_bytes, file_count, newest_mtime, rank_no
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        day,
                        timestamp,
                        account_id,
                        account_name,
                        item_path,
                        changed_bytes,
                        file_count,
                        newest_mtime,
                        rank_no,
                    )
                    for item_path, changed_bytes, file_count, newest_mtime, rank_no in materialized
                ],
            )
            self.conn.executemany(
                """
                INSERT INTO item_activity_state(
                  account_id, item_path, last_activity_day, last_changed_bytes
                ) VALUES(?, ?, ?, ?)
                ON CONFLICT(account_id, item_path) DO UPDATE SET
                  last_activity_day = excluded.last_activity_day,
                  last_changed_bytes = excluded.last_changed_bytes
                """,
                [
                    (account_id, item_path, day, changed_bytes)
                    for item_path, changed_bytes, _, _, _ in materialized
                ],
            )
            self.conn.execute(
                """
                INSERT INTO activity_state(account_id, last_success_ts)
                VALUES(?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                  last_success_ts = excluded.last_success_ts
                """,
                (account_id, timestamp),
            )

    def update_item_activity(
        self,
        account_id: str,
        day: str,
        rows: Iterable[Tuple[str, int]],
    ) -> None:
        materialized = list(rows)
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO item_activity_state(
                  account_id, item_path, last_activity_day, last_changed_bytes
                ) VALUES(?, ?, ?, ?)
                ON CONFLICT(account_id, item_path) DO UPDATE SET
                  last_activity_day = excluded.last_activity_day,
                  last_changed_bytes = excluded.last_changed_bytes
                """,
                [
                    (account_id, item_path, day, int(changed_bytes))
                    for item_path, changed_bytes in materialized
                ],
            )

    def cleanup_candidates(
        self,
        cutoff_day: str,
        min_size_kb: int,
    ):
        cursor = self.conn.execute(
            """
            SELECT ci.account_id, ci.item_path, ci.size_kb,
                   state.first_seen_day, activity.last_activity_day
            FROM current_inventory AS ci
            JOIN inventory_item_state AS state
              ON state.account_id = ci.account_id
             AND state.item_path = ci.item_path
            LEFT JOIN item_activity_state AS activity
              ON activity.account_id = ci.account_id
             AND activity.item_path = ci.item_path
            WHERE ci.size_kb >= ?
              AND state.first_seen_day <= ?
              AND (activity.last_activity_day IS NULL OR activity.last_activity_day < ?)
            ORDER BY ci.size_kb DESC
            """,
            (min_size_kb, cutoff_day, cutoff_day),
        )
        return cursor.fetchall()

    def latest_activity_day(self, account_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT MAX(day) FROM activity_items WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        return row[0] if row and row[0] else None

    def activity_items_for_day(self, account_id: str, day: str):
        return self.conn.execute(
            """
            SELECT item_path, changed_bytes, file_count, newest_mtime, rank_no
            FROM activity_items
            WHERE account_id = ? AND day = ?
            ORDER BY rank_no ASC
            """,
            (account_id, day),
        ).fetchall()

    def growth_items_for_day(self, account_id: str, day: str):
        cursor = self.conn.execute(
            """
            SELECT item_path, delta_kb, rank_no
            FROM growth_items
            WHERE account_id = ? AND day = ?
            ORDER BY rank_no ASC
            """,
            (account_id, day),
        )
        return cursor.fetchall()

    def latest_growth_day(self, account_id: str) -> Optional[str]:
        cursor = self.conn.execute(
            "SELECT MAX(day) FROM growth_items WHERE account_id = ?",
            (account_id,),
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def previous_snapshot(self, account_id: str, before_day: str):
        cursor = self.conn.execute(
            """
            SELECT day, ts, total_kb, used_kb, avail_kb, use_pct, source,
                   total_inodes, used_inodes, avail_inodes, inode_use_pct
                   , quota_used_kb, quota_limit_kb, quota_use_pct, quota_error
            FROM snapshots
            WHERE account_id = ? AND day < ?
            ORDER BY day DESC,
                     CASE source WHEN 'nightly' THEN 0 ELSE 1 END,
                     ts DESC
            LIMIT 1
            """,
            (account_id, before_day),
        )
        return cursor.fetchone()

    def latest_snapshot(self, account_id: str):
        cursor = self.conn.execute(
            """
            SELECT ts, fs_name, total_kb, used_kb, avail_kb, use_pct,
                   total_inodes, used_inodes, avail_inodes, inode_use_pct
                   , quota_used_kb, quota_limit_kb, quota_use_pct, quota_error
            FROM snapshots
            WHERE account_id = ?
            ORDER BY ts DESC
            LIMIT 1
            """,
            (account_id,),
        )
        return cursor.fetchone()

    def latest_nightly_snapshot(self, account_id: str):
        cursor = self.conn.execute(
            """
            SELECT ts, fs_name, total_kb, used_kb, avail_kb, use_pct,
                   total_inodes, used_inodes, avail_inodes, inode_use_pct,
                   quota_used_kb, quota_limit_kb, quota_use_pct, quota_error
            FROM snapshots
            WHERE account_id = ? AND source = 'nightly'
            ORDER BY ts DESC
            LIMIT 1
            """,
            (account_id,),
        )
        return cursor.fetchone()

    def add_capacity_sample(self, record: CapacitySampleRecord) -> None:
        values = (
            record.ts,
            record.account_id,
            record.account_name,
            record.account_path,
            record.fs_key,
            record.fs_name,
            record.total_kb,
            record.used_kb,
            record.avail_kb,
            record.use_pct,
            record.total_inodes,
            record.used_inodes,
            record.avail_inodes,
            record.inode_use_pct,
            record.quota_used_kb,
            record.quota_limit_kb,
            record.quota_use_pct,
            record.quota_error,
        )
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO capacity_samples(
                  ts, account_id, account_name, account_path, fs_key, fs_name,
                  total_kb, used_kb, avail_kb, use_pct,
                  total_inodes, used_inodes, avail_inodes, inode_use_pct,
                  quota_used_kb, quota_limit_kb, quota_use_pct, quota_error
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ts, account_id) DO UPDATE SET
                  account_name=excluded.account_name,
                  account_path=excluded.account_path,
                  fs_key=excluded.fs_key,
                  fs_name=excluded.fs_name,
                  total_kb=excluded.total_kb,
                  used_kb=excluded.used_kb,
                  avail_kb=excluded.avail_kb,
                  use_pct=excluded.use_pct,
                  total_inodes=excluded.total_inodes,
                  used_inodes=excluded.used_inodes,
                  avail_inodes=excluded.avail_inodes,
                  inode_use_pct=excluded.inode_use_pct,
                  quota_used_kb=excluded.quota_used_kb,
                  quota_limit_kb=excluded.quota_limit_kb,
                  quota_use_pct=excluded.quota_use_pct,
                  quota_error=excluded.quota_error
                """,
                values,
            )

    @staticmethod
    def _capacity_record(row) -> Optional[CapacitySampleRecord]:
        return CapacitySampleRecord(*row) if row is not None else None

    def latest_capacity_sample(self, account_id: str) -> Optional[CapacitySampleRecord]:
        row = self.conn.execute(
            """
            SELECT ts, account_id, account_name, account_path, fs_key, fs_name,
                   total_kb, used_kb, avail_kb, use_pct,
                   total_inodes, used_inodes, avail_inodes, inode_use_pct,
                   quota_used_kb, quota_limit_kb, quota_use_pct, quota_error
            FROM capacity_samples
            WHERE account_id = ?
            ORDER BY ts DESC
            LIMIT 1
            """,
            (account_id,),
        ).fetchone()
        return self._capacity_record(row)

    def capacity_sample_count(self, account_id: Optional[str] = None) -> int:
        if account_id is None:
            row = self.conn.execute("SELECT COUNT(*) FROM capacity_samples").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM capacity_samples WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return int(row[0])

    def purge_capacity_samples(
        self,
        keep_days: int,
        now: Optional[datetime] = None,
    ) -> None:
        cutoff = ((now or datetime.now()) - timedelta(days=keep_days)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with self.conn:
            self.conn.execute("DELETE FROM capacity_samples WHERE ts < ?", (cutoff,))

    def trend_points(self, account_id: str, days: int = 365) -> List[Tuple[str, int]]:
        start_day = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        cursor = self.conn.execute(
            """
            SELECT day, MAX(use_pct) AS use_pct
            FROM snapshots
            WHERE account_id = ? AND day >= ?
            GROUP BY day
            ORDER BY day ASC
            """,
            (account_id, start_day),
        )
        return [(row[0], int(row[1])) for row in cursor.fetchall()]

    def recent_used_points(self, account_id: str, days: int = 45) -> List[Tuple[str, int]]:
        start_day = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        cursor = self.conn.execute(
            """
            SELECT day, used_kb, source, ts
            FROM snapshots
            WHERE account_id = ? AND day >= ?
            ORDER BY day ASC,
                     CASE source WHEN 'nightly' THEN 0 ELSE 1 END,
                     ts DESC
            """,
            (account_id, start_day),
        )
        selected = {}
        for row in cursor.fetchall():
            selected.setdefault(row[0], int(row[1]))
        return [(day, selected[day]) for day in sorted(selected)]

    def daily_snapshots_between(self, account_id: str, start_day: str, end_day: str):
        cursor = self.conn.execute(
            """
            SELECT day, used_kb, total_kb, use_pct, source, ts,
                   total_inodes, used_inodes, avail_inodes, inode_use_pct
                   , quota_used_kb, quota_limit_kb, quota_use_pct, quota_error
            FROM snapshots
            WHERE account_id = ? AND day BETWEEN ? AND ?
            ORDER BY day ASC,
                     CASE source WHEN 'nightly' THEN 0 ELSE 1 END,
                     ts DESC
            """,
            (account_id, start_day, end_day),
        )
        selected = {}
        for row in cursor.fetchall():
            selected.setdefault(row[0], row)
        return [selected[day] for day in sorted(selected)]

    def top_items_for_day(self, account_id: str, day: str):
        cursor = self.conn.execute(
            """
            SELECT item_path, size_kb, rank_no
            FROM top_items
            WHERE account_id = ? AND day = ?
            ORDER BY rank_no ASC
            """,
            (account_id, day),
        )
        return cursor.fetchall()

    def latest_top_day(self, account_id: str, on_or_before: Optional[str] = None) -> Optional[str]:
        if on_or_before is None:
            cursor = self.conn.execute(
                "SELECT MAX(day) FROM top_items WHERE account_id = ?",
                (account_id,),
            )
        else:
            cursor = self.conn.execute(
                "SELECT MAX(day) FROM top_items WHERE account_id = ? AND day <= ?",
                (account_id, on_or_before),
            )
        row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def previous_top_items(self, account_id: str, day: str):
        cursor = self.conn.execute(
            """
            SELECT MAX(day)
            FROM top_items
            WHERE account_id = ? AND day < ?
            """,
            (account_id, day),
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            return []
        return self.top_items_for_day(account_id, row[0])

    def purge_old(self, keep_days: int) -> None:
        cutoff_day = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        with self.conn:
            self.conn.execute("DELETE FROM snapshots WHERE day < ?", (cutoff_day,))
            self.conn.execute("DELETE FROM top_items WHERE day < ?", (cutoff_day,))
            self.conn.execute("DELETE FROM growth_items WHERE day < ?", (cutoff_day,))
            self.conn.execute("DELETE FROM activity_items WHERE day < ?", (cutoff_day,))
            self.conn.execute(
                "DELETE FROM item_activity_state WHERE last_activity_day < ?",
                (cutoff_day,),
            )
            self.conn.execute(
                "DELETE FROM inventory_item_state WHERE last_seen_day < ?",
                (cutoff_day,),
            )

    def checkpoint(self) -> None:
        self.conn.execute("PRAGMA optimize")
