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
    capacity_cron_line,
    cron_line,
    health_cron_line,
    install_cron,
    run_nightly_scan,
)
from storage_manager.search_index import SearchIndex, run_full_index, search_db_file
from storage_manager.tracking import read_scan_status


class ReportAndSchedulerTests(unittest.TestCase):
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
            capacity = capacity_cron_line(
                Path(temp).resolve(),
                "/python/home/bin/python3",
            )
            self.assertTrue(capacity.startswith("7,22,37,52 * * * *"))
            self.assertIn("capacity_watch.py", capacity)

    def test_nightly_cron_uses_best_effort_low_priority_tools(self):
        prefix = [
            "/usr/bin/nice",
            "-n",
            "10",
            "/usr/bin/ionice",
            "-c",
            "2",
            "-n",
            "7",
        ]
        with tempfile.TemporaryDirectory() as temp, patch(
            "storage_manager.scheduler.low_priority_prefix",
            return_value=prefix,
            create=True,
        ):
            line = cron_line(Path(temp), "/python/bin/python3")

        self.assertIn("/usr/bin/nice -n 10 /usr/bin/ionice -c 2 -n 7", line)
        self.assertLess(line.index("/usr/bin/nice"), line.index("/python/bin/python3"))

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
        self.assertEqual(payload.count(CRON_MARKER), 3)
        self.assertIn("/other/job", payload)
        self.assertIn("nightly_scan.py", payload)
        self.assertIn("health_check.py", payload)
        self.assertIn("capacity_watch.py", payload)

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

    def test_search_index_reuses_daily_changed_file_stream(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            root = Path(temp) / "user"
            account_path = root / "project_a"
            top_path = account_path / "results"
            top_path.mkdir(parents=True)
            old_file = top_path / "old.dat"
            new_file = top_path / "new.csv"
            old_file.write_text("old", encoding="ascii")
            account = Account(
                "project_a",
                str(account_path),
                account_id="id-a",
                search_enabled=True,
            )
            save_store(
                data_dir,
                AccountStore(
                    Settings(monitored_roots=[str(root)], language="en"),
                    [account],
                ),
            )
            db = Database(db_file(data_dir))
            try:
                db.replace_inventory(account.account_id, "2026-07-12", [(str(top_path), 100)])
                db.set_activity_cursor(account.account_id, "2026-07-12 22:00:00")
            finally:
                db.close()
            index = SearchIndex(search_db_file(data_dir))
            try:
                run_full_index(
                    index,
                    account.account_id,
                    account_path,
                    now=datetime(2026, 7, 12, 22, 0, 0),
                    force=True,
                )
            finally:
                index.close()
            new_file.write_text("new", encoding="ascii")

            backend = StorageBackend(
                name="production-like",
                read_usage=Mock(return_value=UsageSnapshot("fs", 1000, 800, 200, 80)),
                scan_detail=Mock(side_effect=AssertionError("exact scan should not run")),
                test_mode=False,
            )

            def changed_scan(*args, **kwargs):
                kwargs["record_batch"]([(str(new_file), 3, 1001.0)])
                return ActivityScanResult(
                    [(str(top_path), 3, 1, 1001.0)],
                    True,
                    0.1,
                    files_seen=1,
                )

            with patch(
                "storage_manager.scheduler.scan_changed_file_activity",
                side_effect=changed_scan,
            ) as activity_mock:
                run_nightly_scan(
                    data_dir,
                    backend=backend,
                    now_override=datetime(2026, 7, 13, 22, 0, 0),
                )

            self.assertIsNotNone(activity_mock.call_args.kwargs.get("record_batch"))
            index = SearchIndex(search_db_file(data_dir))
            try:
                self.assertEqual(
                    [row.relative_path for row in index.search("id-a", extension="csv")],
                    ["results/new.csv"],
                )
            finally:
                index.close()

    def test_incremental_search_failure_is_not_retried_for_every_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            root = Path(temp) / "user"
            account_path = root / "project_a"
            top_path = account_path / "results"
            top_path.mkdir(parents=True)
            account = Account(
                "project_a",
                str(account_path),
                account_id="id-a",
                search_enabled=True,
            )
            save_store(
                data_dir,
                AccountStore(Settings(monitored_roots=[str(root)]), [account]),
            )
            db = Database(db_file(data_dir))
            try:
                db.replace_inventory(account.account_id, "2026-07-12", [(str(top_path), 1)])
                db.set_activity_cursor(account.account_id, "2026-07-12 22:00:00")
            finally:
                db.close()
            backend = StorageBackend(
                name="production-like",
                read_usage=Mock(return_value=UsageSnapshot("fs", 1000, 500, 500, 50)),
                scan_detail=Mock(side_effect=AssertionError("exact scan should not run")),
                test_mode=False,
            )

            def changed_scan(*args, **kwargs):
                callback = kwargs["record_batch"]
                callback([(str(top_path / "one.dat"), 1, 1.0)])
                callback([(str(top_path / "two.dat"), 1, 2.0)])
                callback([(str(top_path / "three.dat"), 1, 3.0)])
                return ActivityScanResult([], True, 0.1, files_seen=3)

            with patch(
                "storage_manager.scheduler.scan_changed_file_activity",
                side_effect=changed_scan,
            ), patch.object(
                SearchIndex,
                "upsert_changed_files",
                side_effect=RuntimeError("database is locked"),
            ) as upsert:
                report = run_nightly_scan(
                    data_dir,
                    backend=backend,
                    now_override=datetime(2026, 7, 13, 22, 0, 0),
                )

            self.assertTrue(report.exists())
            self.assertEqual(upsert.call_count, 1)

    def test_nightly_run_builds_initial_search_index_after_report(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            root = Path(temp) / "user"
            account_path = root / "project_a"
            account_path.mkdir(parents=True)
            (account_path / "indexed.txt").write_text("x", encoding="ascii")
            account = Account(
                "project_a",
                str(account_path),
                account_id="id-a",
                search_enabled=True,
            )
            save_store(
                data_dir,
                AccountStore(Settings(monitored_roots=[str(root)]), [account]),
            )
            backend = StorageBackend(
                name="test",
                read_usage=Mock(return_value=UsageSnapshot("fs", 1000, 500, 500, 50)),
                scan_detail=Mock(return_value=DetailScanResult([], True, 0.1)),
                test_mode=True,
            )

            report_path = run_nightly_scan(
                data_dir,
                backend=backend,
                now_override=datetime(2026, 7, 14, 22, 0, 0),
            )

            self.assertTrue(report_path.exists())
            index = SearchIndex(search_db_file(data_dir))
            try:
                self.assertEqual(
                    [row.name for row in index.search("id-a", "indexed.txt", mode="exact")],
                    ["indexed.txt"],
                )
            finally:
                index.close()

    def test_production_detail_scan_is_not_skipped_after_morning_cutoff(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            root = Path(temp) / "user"
            account_path = root / "project_a"
            account_path.mkdir(parents=True)
            account = Account("project_a", str(account_path), account_id="id-a")
            save_store(
                data_dir,
                AccountStore(
                    Settings(
                        monitored_roots=[str(root)],
                        nightly_detail_budget_seconds=300,
                    ),
                    [account],
                ),
            )
            backend = StorageBackend(
                name="production-like",
                read_usage=Mock(return_value=UsageSnapshot("fs", 1000, 500, 500, 50)),
                scan_detail=Mock(side_effect=AssertionError("legacy scan should not run")),
                test_mode=False,
            )
            detail = DetailScanResult([], True, 0.1, resumable=True)
            with patch(
                "storage_manager.scheduler.run_resumable_baseline",
                return_value=detail,
            ) as baseline:
                run_nightly_scan(
                    data_dir,
                    backend=backend,
                    now_override=datetime(2026, 7, 15, 12, 0, 0),
                )

            baseline.assert_called_once()
            self.assertIsNone(baseline.call_args.args[3])
            self.assertEqual(
                baseline.call_args.kwargs["task_timeout_seconds"],
                900,
            )

    def test_search_index_failure_does_not_fail_nightly_report(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "data"
            root = Path(temp) / "user"
            account_path = root / "project_a"
            account_path.mkdir(parents=True)
            account = Account(
                "project_a",
                str(account_path),
                account_id="id-a",
                search_enabled=True,
            )
            save_store(
                data_dir,
                AccountStore(Settings(monitored_roots=[str(root)]), [account]),
            )
            backend = StorageBackend(
                name="test",
                read_usage=Mock(return_value=UsageSnapshot("fs", 1000, 500, 500, 50)),
                scan_detail=Mock(return_value=DetailScanResult([], True, 0.1)),
                test_mode=True,
            )
            with patch(
                "storage_manager.scheduler.run_full_index",
                side_effect=RuntimeError("index unavailable"),
            ):
                report_path = run_nightly_scan(data_dir, backend=backend)

            self.assertTrue(report_path.exists())
            self.assertEqual(read_scan_status(data_dir)["state"], "succeeded")


if __name__ == "__main__":
    unittest.main()
