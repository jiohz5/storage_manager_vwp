import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from storage_manager.database import CapacitySampleRecord, Database


class DatabaseTests(unittest.TestCase):
    def test_capacity_samples_keep_intraday_rows_and_purge_old(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "test.db")
            try:
                values = dict(
                    account_id="id-a",
                    account_name="a",
                    account_path="/user/a",
                    fs_key="11:fs-a",
                    fs_name="fs-a",
                    total_kb=1000,
                    avail_kb=200,
                    use_pct=80,
                )
                db.add_capacity_sample(
                    CapacitySampleRecord(
                        ts="2026-06-01 10:00:00",
                        used_kb=800,
                        **values,
                    )
                )
                db.add_capacity_sample(
                    CapacitySampleRecord(
                        ts="2026-07-12 10:00:00",
                        used_kb=800,
                        **values,
                    )
                )
                db.add_capacity_sample(
                    CapacitySampleRecord(
                        ts="2026-07-12 10:15:00",
                        used_kb=850,
                        avail_kb=150,
                        use_pct=85,
                        **{
                            key: value
                            for key, value in values.items()
                            if key not in {"avail_kb", "use_pct"}
                        },
                    )
                )

                self.assertEqual(db.capacity_sample_count("id-a"), 3)
                latest = db.latest_capacity_sample("id-a")
                self.assertIsNotNone(latest)
                self.assertEqual(latest.ts, "2026-07-12 10:15:00")
                self.assertEqual(latest.used_kb, 850)

                db.purge_capacity_samples(30, datetime(2026, 7, 12, 12, 0, 0))
                self.assertEqual(db.capacity_sample_count("id-a"), 2)
            finally:
                db.close()

    def test_daily_snapshot_is_bounded_by_source(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "test.db")
            try:
                values = dict(
                    day="2026-07-11",
                    account_id="id-a",
                    account_name="a",
                    account_path="/user/a",
                    fs_name="fs",
                    total_kb=100,
                    used_kb=80,
                    avail_kb=20,
                    use_pct=80,
                    source="gui",
                )
                db.upsert_snapshot(ts="2026-07-11 09:00:00", **values)
                values["used_kb"] = 90
                values["use_pct"] = 90
                db.upsert_snapshot(ts="2026-07-11 10:00:00", **values)
                count = db.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
                row = db.latest_snapshot("id-a")
                self.assertEqual(count, 1)
                self.assertEqual(row[3], 90)
            finally:
                db.close()

    def test_inventory_and_growth_are_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "test.db")
            try:
                db.replace_inventory("id-a", "2026-07-10", [("/user/a/x", 10)])
                db.replace_inventory("id-a", "2026-07-11", [("/user/a/x", 20)])
                self.assertEqual(db.current_inventory("id-a"), [("/user/a/x", 20)])
                db.replace_growth_items(
                    "2026-07-11 22:00:00",
                    "2026-07-11",
                    "id-a",
                    "a",
                    [("/user/a/x", 10, 1)],
                )
                self.assertEqual(db.growth_items_for_day("id-a", "2026-07-11")[0][1], 10)
            finally:
                db.close()

    def test_empty_inventory_still_records_completed_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "test.db")
            try:
                self.assertFalse(db.has_inventory("id-empty"))
                db.replace_inventory("id-empty", "2026-07-11", [])
                self.assertTrue(db.has_inventory("id-empty"))
                self.assertEqual(db.current_inventory("id-empty"), [])
            finally:
                db.close()

    def test_cleanup_candidates_require_age_size_and_inactivity(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "test.db")
            try:
                old_path = "/user/a/archive"
                active_path = "/user/a/results"
                db.replace_inventory(
                    "id-a",
                    "2026-06-01",
                    [(old_path, 200 * 1024 * 1024), (active_path, 300 * 1024 * 1024)],
                )
                db.update_item_activity("id-a", "2026-07-10", [(active_path, 1024)])
                rows = db.cleanup_candidates("2026-06-12", 100 * 1024 * 1024)
                self.assertEqual([row[1] for row in rows], [old_path])
            finally:
                db.close()

    def test_legacy_schema_is_migrated(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.db"
            conn = sqlite3.connect(str(path))
            conn.executescript(
                """
                CREATE TABLE snapshots (
                  id INTEGER PRIMARY KEY, ts TEXT, day TEXT, account_name TEXT,
                  account_path TEXT, fs_name TEXT, total_kb INTEGER,
                  used_kb INTEGER, avail_kb INTEGER, use_pct INTEGER, source TEXT
                );
                CREATE TABLE top_items (
                  id INTEGER PRIMARY KEY, ts TEXT, day TEXT, account_name TEXT,
                  item_path TEXT, size_kb INTEGER, rank_no INTEGER
                );
                """
            )
            conn.close()

            db = Database(path)
            try:
                snapshot_columns = {row[1] for row in db.conn.execute("PRAGMA table_info(snapshots)")}
                top_columns = {row[1] for row in db.conn.execute("PRAGMA table_info(top_items)")}
                self.assertIn("account_id", snapshot_columns)
                self.assertIn("inode_use_pct", snapshot_columns)
                self.assertIn("quota_use_pct", snapshot_columns)
                self.assertIn("account_id", top_columns)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
