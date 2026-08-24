"""journal 모드 전환이 연결을 망가뜨리지 않아야 한다.

실기에서 "끊길 정도로 느리다"의 원인이 여기였다. `journal_mode`는 DB 파일에
영구 저장되는 값이라, 예전에 WAL로 만들어진 DB를 DELETE로 바꾸려면 **다른
연결이 하나도 없어야** 한다. 이 프로그램은 수집기·스캔·GUI·알림기가 같은 DB를
건드리므로 그 조건이 거의 안 맞는다.

그때 `PRAGMA journal_mode=DELETE`는 `timeout` 만큼 기다렸다가 예외를 던진다.
연결 함수가 그걸 그대로 올리면 **수집도 화면도 통째로 멈춘다.**
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import paths, scan_store, store


class JournalModeFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        scan_store._INITIALIZED.clear()
        store._INITIALIZED.clear()

    def tearDown(self):
        scan_store._INITIALIZED.clear()
        store._INITIALIZED.clear()
        self.tmp.cleanup()

    def test_local_path_uses_wal(self):
        conn = scan_store.connect(self.data_dir)
        conn.close()
        self.assertEqual(scan_store.journal_mode(self.data_dir).lower(), "wal")

    def test_network_path_uses_delete(self):
        with patch.object(paths, "journal_mode_for", return_value="DELETE"):
            conn = scan_store.connect(self.data_dir)
            conn.close()
        self.assertEqual(scan_store.journal_mode(self.data_dir).lower(), "delete")

    def test_connection_still_opens_when_the_mode_cannot_change(self):
        """다른 연결이 붙잡고 있어 모드를 못 바꿔도 연결은 되어야 한다.

        여기서 예외가 올라가면 GUI가 5초마다 실패하며 화면이 멈춘다 -
        실제로 그랬다."""

        # 먼저 WAL 로 만들어 둔다 (예전 DB 상태).
        first = scan_store.connect(self.data_dir)
        self.assertEqual(scan_store.journal_mode(self.data_dir).lower(), "wal")

        # 읽기 연결을 붙잡은 채로, NFS 인 것처럼 DELETE 를 요구한다.
        holder = sqlite3.connect(str(scan_store.db_path(self.data_dir)))
        holder.execute("SELECT COUNT(*) FROM scan_runs").fetchone()
        try:
            scan_store._INITIALIZED.clear()
            with patch.object(paths, "journal_mode_for", return_value="DELETE"):
                conn = scan_store.connect(self.data_dir)   # 예외가 나면 실패
            self.assertIsNotNone(conn)
            # 못 바꿨어도 쓸 수는 있어야 한다.
            conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()
            conn.close()
        finally:
            holder.close()
            first.close()

    def test_already_correct_mode_is_not_rewritten(self):
        """이미 원하는 모드면 쓰기 잠금을 잡으러 갈 이유가 없다."""

        scan_store.connect(self.data_dir).close()
        scan_store._INITIALIZED.clear()

        # sqlite3.Connection.execute 는 교체할 수 없으므로 얇은 대역을 쓴다.
        # `_apply_journal_mode` 가 쓰는 것은 execute 하나뿐이다.
        class Spy:
            def __init__(self, real):
                self._real = real
                self.executed = []

            def execute(self, sql, *args):
                self.executed.append(sql)
                return self._real.execute(sql, *args)

        conn = sqlite3.connect(str(scan_store.db_path(self.data_dir)))
        spy = Spy(conn)
        try:
            with patch.object(paths, "journal_mode_for", return_value="WAL"):
                scan_store._apply_journal_mode(spy, self.data_dir)
            executed = spy.executed
        finally:
            conn.close()

        writes = [sql for sql in executed if "journal_mode=" in sql.replace(" ", "")]
        self.assertEqual(writes, [], f"이미 WAL 인데 다시 설정했습니다: {writes}")

    def test_both_stores_expose_the_actual_mode(self):
        store.connect(self.data_dir).close()
        scan_store.connect(self.data_dir).close()
        self.assertIsNotNone(store.journal_mode(self.data_dir))
        self.assertIsNotNone(scan_store.journal_mode(self.data_dir))

    def test_missing_database_reports_nothing(self):
        self.assertIsNone(scan_store.journal_mode(self.data_dir / "nope"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
