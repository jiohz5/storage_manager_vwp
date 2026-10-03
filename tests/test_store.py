import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smvwp import store


def _sample(account_id="acct-1", collected_at=None, overall_tier="normal", byte_pct=50.0):
    return store.SampleRecord(
        account_id=account_id,
        collected_at=collected_at or datetime.now(timezone.utc).isoformat(),
        ok=True,
        filesystem="/dev/sda1",
        mount_point="/user/project_a",
        total_kb=1000,
        used_kb=500,
        avail_kb=500,
        byte_pct=byte_pct,
        byte_tier=overall_tier,
        inode_total=1000,
        inode_used=10,
        inode_avail=990,
        inode_pct=1.0,
        inode_tier="normal",
        overall_tier=overall_tier,
    )


class StoreTests(unittest.TestCase):
    def test_insert_and_latest_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            conn = store.connect(data_dir)
            try:
                store.insert_sample(conn, _sample(collected_at="2026-01-01T00:00:00+00:00"))
                store.insert_sample(conn, _sample(collected_at="2026-01-01T00:15:00+00:00", byte_pct=60.0))
                latest = store.latest_samples(conn)
                self.assertEqual(len(latest), 1)
                self.assertEqual(latest["acct-1"].byte_pct, 60.0)
            finally:
                conn.close()

    def test_latest_samples_one_row_per_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            conn = store.connect(data_dir)
            try:
                store.insert_sample(conn, _sample(account_id="a", collected_at="2026-01-01T00:00:00+00:00"))
                store.insert_sample(conn, _sample(account_id="b", collected_at="2026-01-01T00:00:00+00:00"))
                latest = store.latest_samples(conn)
                self.assertEqual(set(latest.keys()), {"a", "b"})
            finally:
                conn.close()

    def test_history_returns_descending_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            conn = store.connect(data_dir)
            try:
                store.insert_sample(conn, _sample(collected_at="2026-01-01T00:00:00+00:00", byte_pct=10.0))
                store.insert_sample(conn, _sample(collected_at="2026-01-01T00:15:00+00:00", byte_pct=20.0))
                rows = store.history(conn, "acct-1")
                self.assertEqual([r.byte_pct for r in rows], [20.0, 10.0])
            finally:
                conn.close()

    def test_prune_old_samples_removes_only_stale_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            conn = store.connect(data_dir)
            try:
                now = datetime(2026, 6, 1, tzinfo=timezone.utc)
                old_ts = (now - timedelta(days=200)).isoformat()
                recent_ts = (now - timedelta(days=1)).isoformat()
                store.insert_sample(conn, _sample(collected_at=old_ts))
                store.insert_sample(conn, _sample(collected_at=recent_ts))

                deleted = store.prune_old_samples(conn, retention_days=90, now=now)
                self.assertEqual(deleted, 1)
                remaining = store.history(conn, "acct-1")
                self.assertEqual(len(remaining), 1)
                self.assertEqual(remaining[0].collected_at, recent_ts)
            finally:
                conn.close()


class ConcurrentConnectTests(unittest.TestCase):
    """새 데이터 디렉터리에 여러 스레드가 동시에 붙는 경우.

    창을 처음 열면 작업 스레드 몇(대시보드·스캔 상태·계정 상세)이 거의 같은
    순간에 연결한다. 예전에는 "내가 만들겠다" 표시만 먼저 해 두고 잠금을
    놓았기 때문에, 그 사이에 들어온 스레드가 **표가 하나도 없는 DB** 를 그대로
    받아 `no such table` 로 죽었다."""

    def test_every_thread_gets_a_usable_schema(self):
        import tempfile
        import threading

        from smvwp import store

        with tempfile.TemporaryDirectory() as name:
            data_dir = Path(name) / "data"
            # 이 경로는 이 프로세스에서 처음이다 - 그래야 만드는 경로를 탄다.
            from smvwp import sqlite_db

            sqlite_db._INITIALIZED.discard(str(store.db_path(data_dir)))

            start = threading.Barrier(6)
            errors = []
            lock = threading.Lock()

            def worker():
                # 연결은 만든 스레드에서만 쓸 수 있으므로 여기서 닫는다.
                start.wait()
                conn = None
                try:
                    conn = store.connect(data_dir)
                    conn.execute("SELECT COUNT(*) FROM samples").fetchone()
                except Exception as exc:  # pragma: no cover - 실패를 모아 본다
                    with lock:
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


if __name__ == "__main__":
    unittest.main()
