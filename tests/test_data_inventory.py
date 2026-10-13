"""데이터 폴더 한 장 (`smvwp.data_inventory`).

무엇이, 얼마나, 언제부터 쌓였나. 보기만 하고 아무것도 만들거나 지우지 않는다.
"""

import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import data_inventory, scan_store, store


class _Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        (root / "a").mkdir()
        self.account = config_module.add_account(self.config, "a", str(root / "a"), data_dir=self.data_dir)
        config_module.save_config(self.data_dir, self.config)

    def items(self, inventory):
        return {item.key: item for item in inventory.items}


class EmptyFolderTests(_Case):
    def test_a_fresh_folder_is_described_without_creating_anything(self):
        inventory = data_inventory.build(self.data_dir, self.config)
        items = self.items(inventory)
        self.assertEqual(set(items), set(data_inventory.STEPS))
        self.assertFalse(any(item.failed for item in inventory.items))
        self.assertEqual(items["data.item.config"].count, 1)
        # 보기만 한다 - 없던 DB 를 만들지 않는다.
        self.assertFalse(store.db_path(self.data_dir).exists())
        self.assertFalse(scan_store.db_path(self.data_dir).exists())
        self.assertIsNone(inventory.since)


class FilledFolderTests(_Case):
    def setUp(self):
        super().setUp()
        self.first_sample = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
        with store.session(self.data_dir) as conn:
            for step in range(3):
                store.insert_sample(conn, store.SampleRecord(
                    account_id=self.account.account_id, ok=True,
                    collected_at=(self.first_sample + timedelta(minutes=15 * step)).isoformat(),
                ))
            conn.commit()
        with scan_store.session(self.data_dir) as conn:
            scan_store.start_run(conn, "run-1", "cron")
        outbox = self.data_dir / "outbox"
        outbox.mkdir()
        for name in ("a.json", "b.json"):
            (outbox / name).write_text("{}", encoding="utf-8")
        old = time.time() - 40 * 86400
        os.utime(outbox / "a.json", (old, old))

    def test_counts_sizes_and_since(self):
        inventory = data_inventory.build(self.data_dir, self.config)
        items = self.items(inventory)

        samples = items["data.item.samples"]
        self.assertEqual(samples.count, 3)
        self.assertEqual(samples.oldest, self.first_sample.isoformat())
        self.assertGreater(samples.size_bytes, 0)
        self.assertEqual((samples.keep, samples.keep_value),
                         (data_inventory.KEEP_DAYS, self.config.settings.sample_retention_days))

        scan = items["data.item.scan"]
        runs = next(child for child in scan.children if child.key == "data.scan.runs")
        self.assertEqual(runs.count, 1)

        outbox = items["data.item.outbox"]
        self.assertEqual((outbox.count, outbox.count_unit), (2, "files"))
        # 가장 오래된 것은 40일 전의 알림 파일이다.
        self.assertLess(datetime.fromisoformat(inventory.since),
                        datetime.now(timezone.utc) - timedelta(days=39))
        self.assertEqual(inventory.total_bytes,
                         sum(item.size_bytes or 0 for item in inventory.items))

    def test_each_step_is_announced(self):
        steps = []
        data_inventory.build(self.data_dir, self.config,
                             on_step=lambda i, n, key: steps.append((i, n, key)))
        self.assertEqual(steps, [(i + 1, len(data_inventory.STEPS), key)
                                 for i, key in enumerate(data_inventory.STEPS)])

    def test_one_part_failing_still_shows_the_rest(self):
        with patch.object(data_inventory, "_samples_item", side_effect=OSError("stale handle")):
            inventory = data_inventory.build(self.data_dir, self.config)
        items = self.items(inventory)
        self.assertTrue(items["data.item.samples"].failed)
        self.assertEqual(items["data.item.outbox"].count, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
