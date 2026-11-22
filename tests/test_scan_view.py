"""상세 스캔 상태 결과 파일 - 창이 스캔 DB 를 열지 않게 (`smvwp.scan_view`)."""

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import scan_status, scan_store, scan_view
from tests import support

GB = 1024 * 1024


class _Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"
        self.config = config_module.load_config(self.data_dir)
        self.config.accounts = [config_module.Account(name="proj", path="/acct", account_id="a1")]

    def pass_(self, generation, size_gb, large=()):
        made = support.whole({"/acct/a": size_gb * GB})
        made.largest_files = list(large)
        made.largest_dirs = [(size_gb * GB // 2, "/acct/a/run", 5000)]
        with scan_store.session(self.data_dir) as conn:
            support.scan_pass(conn, "a1", generation, ["/acct/a"], {"/acct/a": made})

    def age(self, seconds, running=False):
        path = scan_view.view_path(self.data_dir)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["written_at"] = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        payload["running"] = running
        path.write_text(json.dumps(payload), encoding="utf-8")


class RoundTripTests(_Case):
    def test_the_file_gives_back_what_the_database_said(self):
        self.pass_(1, 900, [(300 * GB, "/acct/a/big.dat")])
        self.pass_(2, 1000, [(400 * GB, "/acct/a/big.dat")])
        from_db = scan_status.get_status_snapshot(self.data_dir, self.config)
        scan_view.write(self.data_dir, self.config, from_db)
        from_file = scan_view.read(self.data_dir, self.config)
        self.assertIsNotNone(from_file)
        self.assertEqual(scan_view._plain(from_file), scan_view._plain(from_db))
        entry = from_file.accounts[0]
        self.assertEqual(entry.large_files[0][:2], ("/acct/a/big.dat", 400 * GB))
        self.assertIsInstance(entry.large_files[0], tuple)
        self.assertEqual(entry.large_dirs[0][0], "/acct/a/run")
        self.assertEqual(entry.growth[0]["path"], from_db.accounts[0].growth[0]["path"])


class LoadTests(_Case):
    def setUp(self):
        super().setUp()
        self.pass_(1, 900)
        scan_view.write(self.data_dir, self.config)

    def test_a_good_file_is_read_without_opening_the_scan_database(self):
        with patch("smvwp.scan_status.get_status_snapshot", side_effect=AssertionError("DB")), \
                patch("smvwp.scan_store.session", side_effect=AssertionError("DB")):
            snapshot = scan_view.load(self.data_dir, self.config)
        self.assertEqual(snapshot.accounts[0].account_id, "a1")

    def test_whether_a_scan_runs_comes_from_the_lock_not_the_file(self):
        with patch("smvwp.scan_view.scan_lock.is_locked", return_value=True):
            self.assertTrue(scan_view.load(self.data_dir, self.config).is_running)

    def test_an_old_file_goes_back_to_the_database(self):
        interval = self.config.settings.collector_interval_seconds
        self.age(2 * interval + 60)
        self.assertEqual(scan_view.problem(self.data_dir, self.config), scan_view.PROBLEM_STALE)
        with patch("smvwp.scan_status.get_status_snapshot",
                   wraps=scan_status.get_status_snapshot) as from_db:
            scan_view.load(self.data_dir, self.config)
        self.assertEqual(from_db.call_count, 1)

    def test_a_file_written_during_a_scan_is_trusted_only_briefly(self):
        self.age(scan_view.RUNNING_STALE_SECONDS - 30, running=True)
        self.assertIsNone(scan_view.problem(self.data_dir, self.config))
        self.age(scan_view.RUNNING_STALE_SECONDS + 30, running=True)
        self.assertEqual(scan_view.problem(self.data_dir, self.config), scan_view.PROBLEM_STALE)

    def test_other_accounts_or_another_format_are_not_used(self):
        self.config.accounts.append(config_module.Account(name="b", path="/b", account_id="b1"))
        self.assertEqual(scan_view.problem(self.data_dir, self.config), scan_view.PROBLEM_ACCOUNTS)
        self.config.accounts.pop()
        with patch.object(scan_view, "FORMAT", scan_view.FORMAT + 1):
            self.assertEqual(scan_view.problem(self.data_dir, self.config), scan_view.PROBLEM_FORMAT)

    def test_a_missing_file_goes_to_the_database(self):
        scan_view.view_path(self.data_dir).unlink()
        snapshot = scan_view.load(self.data_dir, self.config)
        self.assertEqual(snapshot.accounts[0].account_id, "a1")

    def test_the_window_worker_reads_the_file(self):
        from smvwp.scheduler import ScanStatusWorker

        worker = ScanStatusWorker(self.data_dir, lambda: self.config)
        with patch("smvwp.scan_status.get_status_snapshot", side_effect=AssertionError("DB")):
            snapshot = worker._work()
        self.assertEqual(snapshot.accounts[0].account_id, "a1")


class WritersTests(_Case):
    def test_a_collection_cycle_writes_it(self):
        from smvwp.cycle import run_collection_cycle

        with patch("smvwp.cycle.collector.collect_all", return_value=[]):
            run_collection_cycle(self.data_dir, self.config)
        self.assertIsNone(scan_view.problem(self.data_dir, self.config))

    def test_a_collection_cycle_leaves_it_to_a_running_scan(self):
        from smvwp.cycle import run_collection_cycle

        with patch("smvwp.cycle.collector.collect_all", return_value=[]), \
                patch("smvwp.scan_lock.is_locked", return_value=True):
            run_collection_cycle(self.data_dir, self.config)
        self.assertFalse(scan_view.view_path(self.data_dir).exists())

    def test_the_night_scan_keeps_writing_while_it_runs(self):
        from smvwp import nightly_scan

        written = []
        with patch.object(scan_view, "WRITE_EVERY_SECONDS", 0.05), \
                patch("smvwp.scan_view.write", side_effect=lambda *a, **k: written.append(1)):
            keeper = nightly_scan._ViewKeeper(self.data_dir, self.config)
            keeper.start()
            deadline = time.monotonic() + 3
            while len(written) < 3 and time.monotonic() < deadline:
                time.sleep(0.02)
            keeper.stop()
        self.assertGreaterEqual(len(written), 3)
