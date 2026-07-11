import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from storage_manager.config import Account, AccountStore, Settings, db_file, save_store
from storage_manager.database import Database
from storage_manager.health import build_freshness_events, run_health_check


class HealthTests(unittest.TestCase):
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
            self.assertEqual(result.sent, 1)
            self.assertTrue(result.outbox_file.exists())


if __name__ == "__main__":
    unittest.main()
