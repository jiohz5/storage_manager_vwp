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


if __name__ == "__main__":
    unittest.main()
