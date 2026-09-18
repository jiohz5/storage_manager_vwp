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

    @patch("smvwp.cycle.collector.collect_all")
    def test_old_notification_files_are_cleaned_up(self, mock_collect_all):
        """치우는 함수는 있었는데 부르는 곳이 없어 outbox 가 끝없이 자랐다.

        임계값을 넘은 계정은 15분마다 파일이 하나씩 생긴다. 한 달이면 계정당
        3천 개 가까이 되고, 팝업을 확인할 때마다 그 전부를 NFS 위에서 읽는다."""

        from datetime import timedelta

        from smvwp import notifications, popup_queue
        from smvwp.config import Account

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            config = config_module.load_config(data_dir)
            config_module.save_config(data_dir, config)

            now = datetime.now(timezone.utc)
            for name, age_days in (("old", popup_queue.EVENT_RETENTION_DAYS + 5), ("new", 1)):
                moment = now - timedelta(days=age_days)
                account = Account(name=name, path="/u/" + name, account_id=name)
                sample = store.SampleRecord(
                    account_id=name, collected_at=moment.isoformat(), ok=True,
                    byte_pct=97.0, overall_tier=tiers.ALERT,
                )
                notifications.write_event(
                    data_dir, notifications.build_event(account, sample, moment)
                )

            mock_collect_all.return_value = []
            run_collection_cycle(data_dir, config)

            left = list(notifications.outbox_dir(data_dir).glob("*.json"))
            self.assertEqual(len(left), 1)
            self.assertIn("new", left[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
