import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import store, tiers
from smvwp.cycle import run_collection_cycle


class RunCollectionCycleTests(unittest.TestCase):
    @patch("smvwp.cycle.collector.collect_all")
    def test_stores_samples_and_writes_notification_for_bad_tier(self, mock_collect_all):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            account_path = Path(tmp) / "acct"
            account_path.mkdir()

            config = config_module.load_config(data_dir)
            account = config_module.add_account(config, "project_a", str(account_path))
            config_module.save_config(data_dir, config)

            sample = store.SampleRecord(
                account_id=account.account_id,
                collected_at=datetime.now(timezone.utc).isoformat(),
                ok=True,
                byte_pct=96.0,
                inode_pct=1.0,
                byte_tier=tiers.ALERT,
                inode_tier=tiers.NORMAL,
                overall_tier=tiers.ALERT,
            )
            mock_collect_all.return_value = [sample]

            records = run_collection_cycle(data_dir, config)

            self.assertEqual(len(records), 1)

            conn = store.connect(data_dir)
            try:
                latest = store.latest_samples(conn)
            finally:
                conn.close()
            self.assertIn(account.account_id, latest)

            outbox_files = list((data_dir / "outbox").glob("*.json"))
            self.assertEqual(len(outbox_files), 1)

    @patch("smvwp.cycle.collector.collect_all")
    def test_only_enabled_accounts_are_collected(self, mock_collect_all):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            enabled_path = Path(tmp) / "enabled"
            enabled_path.mkdir()
            disabled_path = Path(tmp) / "disabled"
            disabled_path.mkdir()

            config = config_module.load_config(data_dir)
            config_module.add_account(config, "enabled_acct", str(enabled_path))
            disabled_account = config_module.add_account(config, "disabled_acct", str(disabled_path))
            disabled_account.enabled = False
            config_module.save_config(data_dir, config)

            mock_collect_all.return_value = []
            run_collection_cycle(data_dir, config)

            called_accounts = mock_collect_all.call_args[0][0]
            self.assertEqual(len(called_accounts), 1)
            self.assertEqual(called_accounts[0].name, "enabled_acct")


if __name__ == "__main__":
    unittest.main()
