import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from storage_manager.activity_scan import ActivityScanResult
from storage_manager.collector import DetailScanResult, StorageBackend, UsageSnapshot
from storage_manager.config import Account, AccountStore, Settings, db_file, save_store
from storage_manager.database import Database
from storage_manager.reports import AccountReport, build_daily_report, human_kb
from storage_manager.scheduler import (
    CRON_MARKER,
    ProcessLock,
    ScanAlreadyRunning,
    cron_line,
    health_cron_line,
    install_cron,
    overnight_seconds_remaining,
    run_nightly_scan,
)
from storage_manager.tracking import read_scan_status


class ReportAndSchedulerTests(unittest.TestCase):
    def test_overnight_budget_never_crosses_0545(self):
        self.assertEqual(
            overnight_seconds_remaining(datetime(2026, 7, 11, 22, 0, 0)),
            7 * 3600 + 45 * 60,
        )
        self.assertEqual(
            overnight_seconds_remaining(datetime(2026, 7, 12, 1, 0, 0)),
            4 * 3600 + 45 * 60,
        )
        self.assertEqual(
            overnight_seconds_remaining(datetime(2026, 7, 11, 12, 0, 0)),
            0,
        )
        self.assertEqual(
            overnight_seconds_remaining(datetime(2026, 7, 12, 5, 50, 0)),
            0,
        )

    def test_daily_report_contains_alert_and_growth(self):
        account = Account("project_a", "/user/project_a", account_id="id-a")
        report = AccountReport(
            account=account,
            use_pct=96,
            used_kb=96 * 1024 * 1024,
            total_kb=100 * 1024 * 1024,
            used_delta_kb=1024,
            detail_status="complete",
            growth=[("/user/project_a/db", 2048)],
        )
        content = build_daily_report("2026-07-11", "2026-07-11 22:00:00", [report], 95)
        self.assertIn("[ALERT] project_a", content)
        self.assertIn("Warning threshold: 90%", content)
        self.assertIn("/user/project_a/db", content)
        self.assertEqual(human_kb(1024), "1.00 MB")

        korean = build_daily_report(
            "2026-07-11",
            "2026-07-11 22:00:00",
            [report],
            95,
            language="ko",
        )
        self.assertIn("일간 보고서", korean)
        self.assertIn("[경고]", korean)

        warning_report = AccountReport(
            account=account,
            use_pct=92,
            used_kb=92,
            total_kb=100,
            detail_status="complete",
        )
        warning = build_daily_report(
            "2026-07-11",
            "2026-07-11 22:00:00",
            [warning_report],
            95,
            language="en",
        )
        self.assertIn("[WARN] project_a", warning)

        inode_warning = build_daily_report(
            "2026-07-11",
            "2026-07-11 22:00:00",
            [
                AccountReport(
                    account=account,
                    use_pct=70,
                    inode_use_pct=96,
                    used_kb=70,
                    total_kb=100,
                )
            ],
            95,
            language="en",
        )
        self.assertIn("[ALERT] project_a", inode_warning)
        self.assertIn("Inode usage: 96%", inode_warning)

    def test_cron_line_is_absolute_and_marked(self):
        with tempfile.TemporaryDirectory() as temp:
            line = cron_line(Path(temp).resolve(), "/python/home/bin/python3")
            self.assertTrue(line.startswith("0 22 * * *"))
            self.assertIn(CRON_MARKER, line)
            self.assertIn("nightly_scan.py", line)
            self.assertIn("--trigger cron", line)
            health = health_cron_line(Path(temp).resolve(), "/python/home/bin/python3")
            self.assertTrue(health.startswith("0 7 * * *"))
            self.assertIn("health_check.py", health)

    def test_cron_install_replaces_only_managed_entry(self):
        existing = f"15 1 * * * /other/job\n0 1 * * * old {CRON_MARKER}\n"
        responses = [
            subprocess.CompletedProcess(["crontab", "-l"], 0, existing, ""),
            subprocess.CompletedProcess(["crontab", "-"], 0, "", ""),
        ]
        with tempfile.TemporaryDirectory() as temp, patch(
            "storage_manager.scheduler.subprocess.run", side_effect=responses
        ) as run_mock:
            install_cron(Path(temp), "/python/bin/python3")
        payload = run_mock.call_args_list[1].kwargs["input"]
        self.assertEqual(payload.count(CRON_MARKER), 2)
        self.assertIn("/other/job", payload)
        self.assertIn("nightly_scan.py", payload)
        self.assertIn("health_check.py", payload)

    def test_process_lock_rejects_second_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scan.lock"
            with ProcessLock(path):
                with self.assertRaises(ScanAlreadyRunning):
                    with ProcessLock(path):
                        self.fail("second lock unexpectedly acquired")

    def test_nightly_scan_writes_bounded_snapshot_and_growth_report(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            root = Path(temp) / "user"
            account_path = root / "project_a"
            account_path.mkdir(parents=True)
            account = Account("project_a", str(account_path), account_id="id-a")
            settings = Settings(
                monitored_roots=[str(root)],
                detail_scan_timeout_seconds=60,
                nightly_detail_budget_seconds=300,
            )
            save_store(data_dir, AccountStore(settings, [account]))
            snapshot = UsageSnapshot("fs", 1000, 800, 200, 80)
            scans = [
                DetailScanResult([(str(account_path / "db"), 100)], True, 0.1),
                DetailScanResult([(str(account_path / "db"), 150)], True, 0.1),
            ]

            backend = StorageBackend(
                name="test",
                read_usage=Mock(return_value=snapshot),
                scan_detail=Mock(side_effect=scans),
                test_mode=True,
            )
            report_path = run_nightly_scan(
                data_dir,
                force_weekly=True,
                backend=backend,
            )
            run_nightly_scan(data_dir, backend=backend)

            self.assertTrue(report_path.exists())
            self.assertIn("project_a", report_path.read_text(encoding="utf-8"))
            self.assertTrue((data_dir / "reports" / "latest_weekly.txt").exists())
            self.assertTrue((data_dir / "reports" / "latest_cleanup.txt").exists())
            db = Database(db_file(data_dir))
            try:
                snapshot_count = db.conn.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE source = 'nightly'"
                ).fetchone()[0]
                growth_count = db.conn.execute("SELECT COUNT(*) FROM growth_items").fetchone()[0]
                self.assertEqual(snapshot_count, 1)
                self.assertEqual(growth_count, 1)
            finally:
                db.close()
            self.assertEqual(read_scan_status(data_dir)["state"], "succeeded")
            self.assertEqual(read_scan_status(data_dir)["phase"], "complete")

    def test_stop_request_finishes_report_and_records_stopped_state(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            root = Path(temp) / "user"
            account_path = root / "project_a"
            account_path.mkdir(parents=True)
            account = Account("project_a", str(account_path), account_id="id-a")
            save_store(
                data_dir,
                AccountStore(
                    Settings(monitored_roots=[str(root)]),
                    [account],
                ),
            )
            backend = StorageBackend(
                name="test",
                read_usage=Mock(return_value=UsageSnapshot("fs", 1000, 900, 100, 90)),
                scan_detail=Mock(side_effect=AssertionError("detail should be stopped")),
                test_mode=True,
            )
            with patch(
                "storage_manager.scheduler.scan_stop_requested",
                return_value=True,
            ):
                report_path = run_nightly_scan(data_dir, backend=backend)
            self.assertTrue(report_path.exists())
            self.assertIn("안전 중지", report_path.read_text(encoding="utf-8"))
            self.assertEqual(read_scan_status(data_dir)["state"], "stopped")

    def test_existing_baseline_uses_daily_changed_file_activity(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            root = Path(temp) / "user"
            account_path = root / "project_a"
            top_path = account_path / "results"
            top_path.mkdir(parents=True)
            account = Account("project_a", str(account_path), account_id="id-a")
            settings = Settings(
                monitored_roots=[str(root)],
                language="en",
                detail_scan_timeout_seconds=60,
                nightly_detail_budget_seconds=300,
            )
            save_store(data_dir, AccountStore(settings, [account]))
            db = Database(db_file(data_dir))
            try:
                db.replace_inventory(account.account_id, "2026-07-12", [(str(top_path), 100)])
                db.set_activity_cursor(account.account_id, "2026-07-12 22:00:00")
            finally:
                db.close()

            backend = StorageBackend(
                name="production-like",
                read_usage=Mock(return_value=UsageSnapshot("fs", 1000, 800, 200, 80)),
                scan_detail=Mock(side_effect=AssertionError("exact scan should not run")),
                test_mode=False,
            )
            activity = ActivityScanResult(
                [(str(top_path), 4096, 2, 1000.0)],
                True,
                0.2,
                files_seen=2,
            )
            with patch(
                "storage_manager.scheduler.scan_changed_file_activity",
                return_value=activity,
            ) as activity_mock:
                report_path = run_nightly_scan(
                    data_dir,
                    backend=backend,
                    now_override=datetime(2026, 7, 13, 22, 0, 0),
                )
            activity_mock.assert_called_once()
            content = report_path.read_text(encoding="utf-8")
            self.assertIn("Changed-file activity", content)
            db = Database(db_file(data_dir))
            try:
                rows = db.activity_items_for_day(account.account_id, "2026-07-13")
                self.assertEqual(rows[0][1:3], (4096, 2))
            finally:
                db.close()

    def test_initial_production_baseline_reports_resumable_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            root = Path(temp) / "user"
            account_path = root / "project_a"
            account_path.mkdir(parents=True)
            account = Account("project_a", str(account_path), account_id="id-a")
            settings = Settings(
                monitored_roots=[str(root)],
                language="en",
                detail_scan_timeout_seconds=60,
                nightly_detail_budget_seconds=300,
            )
            save_store(data_dir, AccountStore(settings, [account]))
            backend = StorageBackend(
                name="production-like",
                read_usage=Mock(return_value=UsageSnapshot("fs", 1000, 500, 500, 50)),
                scan_detail=Mock(side_effect=AssertionError("legacy exact scan should not run")),
                test_mode=False,
            )
            progress = DetailScanResult(
                [],
                False,
                10.0,
                completed_tasks=2,
                total_tasks=10,
                resumable=True,
            )
            with patch(
                "storage_manager.scheduler.run_resumable_baseline",
                return_value=progress,
            ):
                report_path = run_nightly_scan(
                    data_dir,
                    backend=backend,
                    now_override=datetime(2026, 7, 13, 22, 0, 0),
                )
            content = report_path.read_text(encoding="utf-8")
            self.assertIn("baseline progress 2/10", content)
            self.assertIn("resumes next night", content)

    def test_completed_resumable_baseline_keeps_activity_cursor_at_cycle_start(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            root = Path(temp) / "user"
            account_path = root / "project_a"
            top_path = account_path / "results"
            top_path.mkdir(parents=True)
            account = Account("project_a", str(account_path), account_id="id-a")
            settings = Settings(
                monitored_roots=[str(root)],
                language="en",
                detail_scan_timeout_seconds=60,
                nightly_detail_budget_seconds=300,
            )
            save_store(data_dir, AccountStore(settings, [account]))
            cycle_start = "2026-07-10 22:00:00"
            db = Database(db_file(data_dir))
            try:
                db.begin_detail_scan(
                    account.account_id,
                    str(account_path),
                    "cycle-a",
                    cycle_start,
                    [(str(top_path), str(top_path), "scan", 0, "complete", 100)],
                )
            finally:
                db.close()

            backend = StorageBackend(
                name="production-like",
                read_usage=Mock(return_value=UsageSnapshot("fs", 1000, 500, 500, 50)),
                scan_detail=Mock(side_effect=AssertionError("legacy exact scan should not run")),
                test_mode=False,
            )
            detail = DetailScanResult(
                [(str(top_path), 100)],
                True,
                1.0,
                completed_tasks=1,
                total_tasks=1,
                resumable=True,
            )
            with patch(
                "storage_manager.scheduler.run_resumable_baseline",
                return_value=detail,
            ):
                run_nightly_scan(
                    data_dir,
                    backend=backend,
                    now_override=datetime(2026, 7, 13, 22, 0, 0),
                )

            db = Database(db_file(data_dir))
            try:
                self.assertEqual(db.last_activity_ts(account.account_id), cycle_start)
                self.assertIsNone(db.detail_scan_state(account.account_id))
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
