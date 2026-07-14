import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from storage_manager.capacity_watch import (
    CAPACITY_LOCK_FILENAME,
    CapacityAccountResult,
    assess_capacity,
    build_capacity_events,
    read_capacity_watch_status,
    run_capacity_watch,
)
from storage_manager.collector import DetailScanResult, StorageBackend, UsageSnapshot
from storage_manager.config import Account, AccountStore, Settings, save_store
from storage_manager.database import CapacitySampleRecord, Database
from storage_manager.scheduler import ProcessLock, ScanAlreadyRunning


def usage(
    used_kb: int,
    avail_kb: int,
    use_pct: int,
    inode_use_pct=None,
) -> UsageSnapshot:
    return UsageSnapshot(
        "fs-a",
        used_kb + avail_kb,
        used_kb,
        avail_kb,
        use_pct,
        total_inodes=1000 if inode_use_pct is not None else None,
        used_inodes=inode_use_pct * 10 if inode_use_pct is not None else None,
        avail_inodes=(100 - inode_use_pct) * 10 if inode_use_pct is not None else None,
        inode_use_pct=inode_use_pct,
    )


def previous_sample(
    ts: str,
    used_kb: int,
    avail_kb: int,
    use_pct: int,
    fs_key: str = "1:fs-a",
) -> CapacitySampleRecord:
    return CapacitySampleRecord(
        ts=ts,
        account_id="id-a",
        account_name="a",
        account_path="/user/a",
        fs_key=fs_key,
        fs_name="fs-a",
        total_kb=used_kb + avail_kb,
        used_kb=used_kb,
        avail_kb=avail_kb,
        use_pct=use_pct,
    )


class CapacityAssessmentTests(unittest.TestCase):
    def test_growth_rate_predicts_emergency_before_percent_threshold(self):
        settings = Settings(rapid_growth_gb=10_000)
        previous = previous_sample(
            "2026-07-12 10:00:00",
            used_kb=8_000_000,
            avail_kb=2_000_000,
            use_pct=80,
        )
        current = usage(used_kb=9_000_000, avail_kb=1_000_000, use_pct=90)

        result = assess_capacity(
            current,
            previous,
            datetime(2026, 7, 12, 10, 15, 0),
            settings,
        )

        self.assertEqual(result.level, "emergency")
        self.assertEqual(result.growth_kb, 1_000_000)
        self.assertEqual(result.rate_kb_per_hour, 4_000_000)
        self.assertAlmostEqual(result.hours_to_full, 0.25)

    def test_rapid_growth_alert_uses_raw_kb_not_rounded_percent(self):
        settings = Settings(rapid_growth_gb=100)
        growth_kb = 100 * 1024 * 1024
        previous = previous_sample(
            "2026-07-12 10:00:00",
            used_kb=10_000_000_000,
            avail_kb=40_000_000_000,
            use_pct=20,
        )
        current = usage(
            used_kb=previous.used_kb + growth_kb,
            avail_kb=previous.avail_kb - growth_kb,
            use_pct=20,
        )

        result = assess_capacity(
            current,
            previous,
            datetime(2026, 7, 12, 10, 15, 0),
            settings,
        )

        self.assertTrue(result.rapid_growth)
        self.assertEqual(result.level, "alert")

    def test_old_or_different_filesystem_sample_is_not_used_for_rate(self):
        settings = Settings()
        old = previous_sample(
            "2026-07-12 06:00:00",
            used_kb=1,
            avail_kb=999,
            use_pct=1,
            fs_key="old:fs-a",
        )
        result = assess_capacity(
            usage(900, 100, 90),
            old,
            datetime(2026, 7, 12, 10, 15, 0),
            settings,
            fs_key="new:fs-a",
        )
        self.assertEqual(result.growth_kb, 0)
        self.assertEqual(result.rate_kb_per_hour, 0)
        self.assertIsNone(result.hours_to_full)

    def test_capacity_recovery_event_is_emitted_once_for_shared_filesystem(self):
        settings = Settings()
        assessment = assess_capacity(
            usage(800, 200, 80),
            previous_sample("2026-07-12 10:00:00", 920, 80, 92),
            datetime(2026, 7, 12, 10, 15, 0),
            settings,
        )
        first = CapacityAccountResult(
            Account("a", "/user/a", account_id="id-a"),
            "1:fs-a",
            usage(800, 200, 80),
            assessment,
        )
        second = CapacityAccountResult(
            Account("b", "/user/b", account_id="id-b"),
            "1:fs-a",
            usage(800, 200, 80),
            assessment,
        )

        events = build_capacity_events([first, second], settings, "en")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].key, "capacity:1:fs-a")
        self.assertEqual(events[0].level, "recovery")


class CapacityWatchRunTests(unittest.TestCase):
    def test_overlapping_watch_does_not_replace_active_status(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            (data_dir / "capacity_watch_status.json").write_text(
                '{"state":"running","run_id":"active-run"}\n',
                encoding="utf-8",
            )
            with ProcessLock(data_dir / CAPACITY_LOCK_FILENAME):
                with self.assertRaises(ScanAlreadyRunning):
                    run_capacity_watch(data_dir)

            status = read_capacity_watch_status(data_dir)
            self.assertEqual(status["state"], "running")
            self.assertEqual(status["run_id"], "active-run")

    def test_shared_filesystem_is_read_once_and_each_account_is_stored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "state"
            account_root = root / "user"
            paths = [account_root / "a", account_root / "b"]
            for path in paths:
                path.mkdir(parents=True)
            accounts = [
                Account(path.name, str(path), account_id=f"id-{path.name}")
                for path in paths
            ]
            save_store(
                data_dir,
                AccountStore(Settings(monitored_roots=[str(account_root)]), accounts),
            )
            read_usage = Mock(return_value=usage(960, 40, 96))
            scan_detail = Mock(return_value=DetailScanResult([], True, 0.0))
            backend = StorageBackend("test", read_usage, scan_detail, test_mode=True)

            result = run_capacity_watch(
                data_dir,
                backend=backend,
                now_override=datetime(2026, 7, 12, 10, 15, 0),
                device_reader=lambda _path: 77,
            )

            self.assertEqual(read_usage.call_count, 1)
            scan_detail.assert_not_called()
            self.assertEqual(result.samples_written, 2)
            self.assertEqual(result.filesystems_checked, 1)
            self.assertEqual(result.events_written, 1)
            db = Database(data_dir / "storage_manager.db")
            try:
                self.assertEqual(db.capacity_sample_count(), 2)
            finally:
                db.close()
            status = read_capacity_watch_status(data_dir)
            self.assertEqual(status["state"], "succeeded")
            self.assertEqual(status["samples_written"], 2)

    def test_failed_filesystem_does_not_block_other_groups(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "state"
            account_root = root / "user"
            paths = [account_root / "a", account_root / "b"]
            for path in paths:
                path.mkdir(parents=True)
            save_store(
                data_dir,
                AccountStore(
                    Settings(monitored_roots=[str(account_root)]),
                    [
                        Account("a", str(paths[0]), account_id="id-a"),
                        Account("b", str(paths[1]), account_id="id-b"),
                    ],
                ),
            )
            read_usage = Mock(side_effect=[OSError("mount timeout"), usage(500, 500, 50)])
            backend = StorageBackend(
                "test",
                read_usage,
                Mock(return_value=DetailScanResult([], True, 0.0)),
                test_mode=True,
            )
            devices = {str(paths[0].resolve()): 1, str(paths[1].resolve()): 2}

            result = run_capacity_watch(
                data_dir,
                backend=backend,
                now_override=datetime(2026, 7, 12, 10, 15, 0),
                device_reader=lambda path: devices[path],
            )

            self.assertEqual(result.samples_written, 1)
            self.assertEqual(result.filesystems_checked, 1)
            self.assertEqual(len(result.errors), 1)
            self.assertIn("mount timeout", result.errors[0])


if __name__ == "__main__":
    unittest.main()
