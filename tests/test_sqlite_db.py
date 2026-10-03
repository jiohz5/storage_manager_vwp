"""SQLite 연결의 공통 규칙 (`smvwp.sqlite_db`).

세 DB(표본·스캔·검색 색인)가 이 규칙을 같이 쓴다. 예전에는 앞의 둘이 같은 코드를
복사해 쓰고 있어서 같은 경합 버그를 두 곳에서 따로 고쳐야 했고, 검색 색인은 그
개선을 하나도 받지 못했다.
"""

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import scan_store, search_index, sqlite_db, store


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.data_dir = Path(self.tmp.name) / "data"
        self.path = self.data_dir / "x.db"
        sqlite_db._INITIALIZED.clear()

    def tearDown(self):
        sqlite_db._INITIALIZED.clear()
        self.tmp.cleanup()


class InitializeOnceTests(_Case):
    def test_the_schema_is_made_once_per_process(self):
        """연결마다 스키마를 만들고 커밋하면 NFS 위에서 그 커밋 하나가 왕복이다."""

        calls = []

        def initialize(conn):
            calls.append(1)
            conn.execute("CREATE TABLE IF NOT EXISTS t (x)")

        for _ in range(3):
            sqlite_db.connect(self.path, self.data_dir, initialize).close()
        self.assertEqual(len(calls), 1)

    def test_every_store_goes_through_the_shared_rule(self):
        """검색 색인만 따로 짠 연결 함수를 쓰고 있었다."""

        for module in (store, scan_store, search_index):
            calls = []
            original = module._initialize

            def counting(conn, original=original):
                calls.append(1)
                original(conn)

            with patch.object(module, "_initialize", counting):
                module.connect(self.data_dir).close()
                module.connect(self.data_dir).close()
            self.assertEqual(len(calls), 1, module.__name__)

    def test_threads_arriving_together_all_get_the_tables(self):
        """먼저 "만들었다" 표시를 하고 잠금을 놓으면 늦게 온 쪽이 빈 DB 를 받는다."""

        def initialize(conn):
            # 스키마 만들기가 느린 상황 (NFS) 을 흉내 낸다.
            import time

            time.sleep(0.05)
            conn.execute("CREATE TABLE IF NOT EXISTS t (x)")

        start = threading.Barrier(6)
        errors = []

        def worker():
            start.wait()
            conn = None
            try:
                conn = sqlite_db.connect(self.path, self.data_dir, initialize)
                conn.execute("SELECT COUNT(*) FROM t").fetchone()
            except Exception as exc:  # pragma: no cover - 실패를 모아 본다
                errors.append(repr(exc))
            finally:
                if conn is not None:
                    conn.close()

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])

    def test_a_failed_initialize_is_tried_again_next_time(self):
        """처음에 실패했다고 "만들었다" 로 남으면 그 프로세스는 영영 빈 DB 를 쓴다."""

        attempts = []

        def flaky(conn):
            attempts.append(1)
            if len(attempts) == 1:
                raise sqlite3.OperationalError("database is locked")
            conn.execute("CREATE TABLE IF NOT EXISTS t (x)")

        with self.assertRaises(sqlite3.OperationalError):
            sqlite_db.connect(self.path, self.data_dir, flaky)
        conn = sqlite_db.connect(self.path, self.data_dir, flaky)
        conn.execute("SELECT COUNT(*) FROM t").fetchone()
        conn.close()
        self.assertEqual(len(attempts), 2)


class SessionTests(_Case):
    """`with 모듈.session(...)` 은 블록이 끝나면 **닫는다.**

    sqlite3 연결의 `with conn:` 은 커밋만 하고 닫지 않는다 - 그걸 믿고 쓰면
    연결이 남아 윈도우에서는 파일을 못 지우고 NFS 위에서는 잠금이 오래 남는다."""

    def test_the_connection_is_closed_after_the_block(self):
        for module in (store, scan_store, search_index):
            with module.session(self.data_dir) as conn:
                conn.execute("SELECT 1").fetchone()
            with self.assertRaises(sqlite3.ProgrammingError, msg=module.__name__):
                conn.execute("SELECT 1")

    def test_the_connection_is_closed_when_the_block_fails(self):
        with self.assertRaises(RuntimeError):
            with scan_store.session(self.data_dir) as conn:
                raise RuntimeError("boom")
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


class RetiredTableTests(_Case):
    """걷어 낸 표가 기존 DB 에 남지 않는다.

    `growth_history` 는 밤마다 모든 경로의 증감을 기록하고 60세대를 보관했는데
    읽는 화면이 없었다. 기록을 멈추는 것만으로는 이미 쌓인 것이 그대로 남는다."""

    def test_an_old_database_loses_the_retired_table(self):
        self.data_dir.mkdir(parents=True)
        old = sqlite3.connect(str(scan_store.db_path(self.data_dir)))
        old.execute("CREATE TABLE growth_history (account_id TEXT, path TEXT)")
        old.execute("INSERT INTO growth_history VALUES ('a', '/x')")
        old.commit()
        old.close()

        with scan_store.session(self.data_dir) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
        self.assertNotIn("growth_history", tables)
        self.assertIn("scan_runs", tables)


class AddMissingColumnsTests(_Case):
    def test_only_missing_columns_are_added(self):
        conn = sqlite3.connect(str(self.path.parent.mkdir(parents=True) or self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (a INTEGER)")
        sqlite_db.add_missing_columns(conn, "t", {"a": "INTEGER", "b": "TEXT"})
        sqlite_db.add_missing_columns(conn, "t", {"a": "INTEGER", "b": "TEXT"})
        names = [row[1] for row in conn.execute("PRAGMA table_info(t)")]
        conn.close()
        self.assertEqual(names, ["a", "b"])


class JournalModeReportTests(_Case):
    def test_every_store_reports_its_mode(self):
        """진단이 셋 다 보여 줘야 NFS 위에서 WAL 로 남은 DB 를 찾는다."""

        for module in (store, scan_store, search_index):
            module.connect(self.data_dir).close()
            self.assertIsNotNone(module.journal_mode(self.data_dir), module.__name__)

    def test_a_missing_file_reports_nothing(self):
        self.assertIsNone(sqlite_db.journal_mode(self.data_dir / "nope.db"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
