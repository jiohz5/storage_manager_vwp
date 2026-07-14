import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from storage_manager.config import Account, AccountStore, Settings, db_file, save_store
from storage_manager.database import CapacitySampleRecord, Database
from storage_manager.health import (
    build_data_directory_events,
    build_freshness_events,
    run_health_check,
)


class HealthTests(unittest.TestCase):
    def test_data_directory_size_warning_uses_configured_budget(self):
        settings = Settings(data_size_warning_mb=500)
        data_dir = Path("/state")

        safe = build_data_directory_events(
            data_dir,
            settings,
            size_reader=lambda _: 499 * 1024 * 1024,
        )
        warning = build_data_directory_events(
            data_dir,
            settings,
            size_reader=lambda _: 500 * 1024 * 1024,
        )

        self.assertEqual(safe, [])
        self.assertEqual(len(warning), 1)
        self.assertEqual(warning[0].key, "data-directory:size")
        self.assertEqual(warning[0].level, "warning")
        self.assertIn("500", warning[0].message)

    def test_stale_capacity_sample_creates_warning(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            account = Account("project_a", "/user/project_a", account_id="id-a")
            store = AccountStore(
                Settings(capacity_stale_minutes=45),
                [account],
            )
            save_store(data_dir, store)
            db = Database(db_file(data_dir))
            try:
                db.upsert_snapshot(
                    ts="2026-07-12 06:30:00",
                    day="2026-07-12",
                    account_id=account.account_id,
                    account_name=account.name,
                    account_path=account.path,
                    fs_name="fs",
                    total_kb=1000,
                    used_kb=800,
                    avail_kb=200,
                    use_pct=80,
                    source="nightly",
                )
                db.add_capacity_sample(
                    CapacitySampleRecord(
                        ts="2026-07-12 05:30:00",
                        account_id=account.account_id,
                        account_name=account.name,
                        account_path=account.path,
                        fs_key="1:fs",
                        fs_name="fs",
                        total_kb=1000,
                        used_kb=800,
                        avail_kb=200,
                        use_pct=80,
                    )
                )
                events = build_freshness_events(
                    store,
                    db,
                    datetime(2026, 7, 12, 7, 0, 0),
                )
            finally:
                db.close()
            self.assertTrue(
                any(event.key == "capacity-freshness:stale" for event in events)
            )
    def test_stale_nightly_snapshot_creates_warning(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            account = Account("project_a", "/user/project_a", account_id="id-a")
            store = AccountStore(Settings(freshness_warning_hours=30), [account])
            save_store(data_dir, store)
            db = Database(db_file(data_dir))
            try:
                db.upsert_snapshot(
                    ts="2026-07-10 22:00:00",
                    day="2026-07-10",
                    account_id=account.account_id,
                    account_name=account.name,
                    account_path=account.path,
                    fs_name="fs",
                    total_kb=1000,
                    used_kb=800,
                    avail_kb=200,
                    use_pct=80,
                    source="nightly",
                )
                events = build_freshness_events(
                    store, db, datetime(2026, 7, 12, 7, 0, 0)
                )
            finally:
                db.close()
            self.assertTrue(any(event.key == "freshness:stale" for event in events))

    def test_health_check_dispatches_without_gui(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            account = Account("project_a", "/user/project_a", account_id="id-a")
            save_store(
                data_dir,
                AccountStore(Settings(notification_mode="outbox"), [account]),
            )
            result = run_health_check(data_dir, datetime(2026, 7, 12, 7, 0, 0))
            self.assertEqual(result.sent, 2)
            self.assertTrue(result.outbox_file.exists())


if __name__ == "__main__":
    unittest.main()
