"""스캔 중 리소스 계측 - 시계열 기록, 보고서 반영, 계정 병렬 실행."""

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import i18n, loadstat, nightly_scan, reports, scan_store
from tests import support


def _snapshot(phase, **kwargs):
    values = dict(
        sampled_at=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=0.0,
        phase=phase,
    )
    values.update(kwargs)
    return loadstat.Snapshot(**values)


class SystemSamplerTests(unittest.TestCase):
    def test_reports_nothing_when_the_interval_is_too_short(self):
        """jiffies가 안 움직인 구간을 0%로 채우면 '한가했다'로 잘못 읽힌다."""

        sampler = loadstat.SystemSampler()
        with patch.object(loadstat, "_cpu_fields", return_value=(100, 50, 10)):
            sampler._prev = (100, 50, 10)
            result = sampler.take()
        self.assertIsNone(result.busy_percent)
        self.assertIsNone(result.iowait_percent)

    def test_separates_iowait_from_busy(self):
        """du/find는 CPU가 아니라 I/O 대기를 만든다 - 둘을 합치면 그게 안 보인다."""

        sampler = loadstat.SystemSampler()
        sampler._prev = (0, 0, 0)
        with patch.object(loadstat, "_cpu_fields", return_value=(100, 40, 50)):
            result = sampler.take()
        # 전체 100 중 idle 40, iowait 50 -> 실제 연산은 10.
        self.assertAlmostEqual(result.busy_percent, 10.0)
        self.assertAlmostEqual(result.iowait_percent, 50.0)


class ChangeTests(unittest.TestCase):
    def test_before_and_during_are_compared(self):
        samples = [
            _snapshot(loadstat.PHASE_BEFORE, load_avg_1m=0.5),
            _snapshot(loadstat.PHASE_DURING, load_avg_1m=2.0),
            _snapshot(loadstat.PHASE_DURING, load_avg_1m=4.0),
        ]
        changes = {change.metric: change for change in loadstat.changes(samples)}
        load = changes["load_avg"]
        self.assertEqual(load.before, 0.5)
        self.assertEqual(load.peak, 4.0)
        self.assertEqual(load.average, 3.0)
        self.assertEqual(load.delta, 3.5)

    def test_missing_baseline_keeps_the_rest(self):
        """기준값이 없어도 스캔 중 절대값만으로 읽을 것이 있다."""

        samples = [_snapshot(loadstat.PHASE_DURING, load_avg_1m=2.0)]
        change = loadstat.changes(samples)[0]
        self.assertIsNone(change.before)
        self.assertIsNone(change.delta)
        self.assertEqual(change.peak, 2.0)

    def test_metric_order_puts_io_before_cpu(self):
        """CPU만 보면 'du는 부하가 없다'는 잘못된 결론에 이른다."""

        samples = [
            _snapshot(loadstat.PHASE_DURING, load_avg_1m=1.0, cpu_iowait_percent=2.0, cpu_busy_percent=3.0)
        ]
        metrics = [change.metric for change in loadstat.changes(samples)]
        self.assertEqual(metrics[:3], ["load_avg", "cpu_iowait", "cpu_busy"])


class AccumulatorThreadSafetyTests(unittest.TestCase):
    def test_concurrent_sampling_does_not_corrupt_the_summary(self):
        """계정 병렬 실행에서는 여러 스레드가 같은 누적기를 부른다."""

        accumulator = loadstat.Accumulator()
        errors = []

        def hammer():
            try:
                for _ in range(200):
                    accumulator.sample()
                    accumulator.summary()
            except Exception as exc:  # pragma: no cover - 실패 시 원인 보고용
                errors.append(exc)

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])


class LoadSampleStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.conn = scan_store.connect(self.data_dir)
        scan_store.start_run(self.conn, "run-1", "terminal")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_round_trip(self):
        saved = scan_store.save_load_samples(
            self.conn,
            "run-1",
            [
                _snapshot(loadstat.PHASE_BEFORE, load_avg_1m=0.4),
                _snapshot(loadstat.PHASE_DURING, load_avg_1m=3.1, active_accounts=3),
            ],
        )
        self.assertEqual(saved, 2)
        rows = scan_store.load_samples(self.conn, "run-1")
        self.assertEqual([row["phase"] for row in rows], ["before", "during"])
        self.assertEqual(rows[1]["active_accounts"], 3)

    def test_warmup_samples_are_not_stored(self):
        """워밍업은 델타 기준점을 잡으려고 버리는 표본이다."""

        saved = scan_store.save_load_samples(
            self.conn, "run-1", [_snapshot("warmup"), _snapshot(loadstat.PHASE_BEFORE)]
        )
        self.assertEqual(saved, 1)

    def test_parallelism_is_recorded_on_the_run(self):
        scan_store.record_parallelism(self.conn, "run-1", 4)
        run = scan_store.latest_run(self.conn)
        self.assertEqual(run["parallel_accounts"], 4)

    def test_pruning_respects_retention(self):
        old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        scan_store.save_load_samples(
            self.conn,
            "run-1",
            [
                _snapshot(loadstat.PHASE_DURING, sampled_at=old),
                _snapshot(loadstat.PHASE_DURING),
            ],
        )
        removed = scan_store.prune_load_samples(self.conn, retention_days=30)
        self.assertEqual(removed, 1)
        self.assertEqual(len(scan_store.load_samples(self.conn, "run-1")), 1)


class ResourceReportSectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        account_path = self.root / "acct"
        account_path.mkdir(parents=True)
        self.config = config_module.load_config(self.data_dir)
        config_module.add_account(
            self.config, "project_a", str(account_path), data_dir=self.data_dir
        )
        self._original_language = i18n.get_language()
        i18n.set_language(i18n.KOREAN)

    def tearDown(self):
        i18n.set_language(self._original_language)
        self.tmp.cleanup()

    def _seed(self, samples, parallel=1):
        conn = scan_store.connect(self.data_dir)
        try:
            scan_store.start_run(conn, "run-1", "terminal")
            scan_store.record_parallelism(conn, "run-1", parallel)
            scan_store.save_load_samples(conn, "run-1", samples)
        finally:
            conn.close()

    def test_section_shows_before_and_peak(self):
        self._seed(
            [
                _snapshot(loadstat.PHASE_BEFORE, load_avg_1m=0.20, cpu_iowait_percent=1.0),
                _snapshot(loadstat.PHASE_DURING, load_avg_1m=5.50, cpu_iowait_percent=40.0),
                _snapshot(loadstat.PHASE_DURING, load_avg_1m=3.50, cpu_iowait_percent=20.0),
            ],
            parallel=3,
        )
        text = reports.build_daily_report(self.data_dir, self.config)
        self.assertIn(i18n.t("reports.resource_heading"), text)
        self.assertIn("0.20", text)   # 스캔 전
        self.assertIn("5.50", text)   # 최고
        self.assertIn("+5.30", text)  # 증가폭
        self.assertIn(i18n.t("reports.resource_context", samples=3, parallel=3), text)

    def test_section_states_the_measurement_limit(self):
        """CPU가 낮다고 부하가 없었다고 읽으면 안 된다는 점을 반드시 적는다."""

        self._seed([_snapshot(loadstat.PHASE_DURING, load_avg_1m=1.0)])
        text = reports.build_daily_report(self.data_dir, self.config)
        self.assertIn(i18n.t("reports.resource_caveat"), text)

    def test_section_is_absent_when_nothing_was_measured(self):
        text = reports.build_daily_report(self.data_dir, self.config)
        self.assertNotIn(i18n.t("reports.resource_heading"), text)

    def test_timeline_is_thinned_out(self):
        """표본 수백 개를 그대로 적으면 보고서가 시계열로 뒤덮인다."""

        self._seed(
            [_snapshot(loadstat.PHASE_DURING, load_avg_1m=float(n)) for n in range(60)]
        )
        text = reports.build_daily_report(self.data_dir, self.config)
        timeline = text.split(i18n.t("reports.resource_timeline_heading"))[1]
        rows = [line for line in timeline.splitlines() if line.strip().endswith("0")]
        self.assertLessEqual(len(rows), reports.LOAD_TIMELINE_ROWS + 1)


class ParallelScanTests(unittest.TestCase):
    """계정 병렬 실행 (기본 꺼짐, 부하 실측용 경로)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.config = config_module.load_config(self.data_dir)
        self.accounts = []
        for name in ("a", "b", "c"):
            path = self.root / name
            path.mkdir(parents=True)
            self.accounts.append(
                config_module.add_account(
                    self.config, name, str(path), data_dir=self.data_dir
                )
            )
        config_module.save_config(self.data_dir, self.config)
        self.lister = lambda path: [f"{path}/dir1", f"{path}/dir2"]

    def tearDown(self):
        self.tmp.cleanup()

    # 2026-08-24는 월요일. 월요일 밤 -> 화요일 아침에 끝나므로 평일 밤이다.
    WEEKDAY_NIGHT = datetime(2026, 8, 24, 23, 0)
    # 2026-08-21은 금요일. 금요일 밤 -> 토요일 아침에 끝나므로 주말 밤이다.
    FRIDAY_NIGHT = datetime(2026, 8, 21, 23, 0)
    # 2026-08-23은 일요일. 일요일 밤 -> 월요일 아침에 끝나므로 평일 밤이다.
    SUNDAY_NIGHT = datetime(2026, 8, 23, 23, 0)

    def _run(self, now=None, **kwargs):
        def runner(command, **_kwargs):
            # `nice`/`ionice` 접두사가 실제로 되는지 보는 탐색 호출
            # (`detail_scan._prefix_works`). 이 파일만 따로 돌리면 캐시가
            # 비어 있어 여기부터 들어온다.
            if command[-1] == "true":
                return support.completed(command)
            if "du" in command:
                return support.completed(command, stdout=f"100\t{command[-1]}\n")
            if "find" in command:
                return support.completed(command)
            raise AssertionError(f"예상치 못한 명령: {command}")

        with patch("smvwp.detail_scan.subprocess.run", side_effect=runner):
            return nightly_scan.run_nightly_scan(
                self.data_dir,
                self.config,
                bypass_window=True,
                clock=lambda: now or self.WEEKDAY_NIGHT,
                top_level_lister=self.lister,
                baseline_warmup_seconds=0.0,
                **kwargs,
            )

    def test_parallel_run_completes_every_account(self):
        summary = self._run(parallel_accounts=3)
        self.assertEqual(summary.status, nightly_scan.STATUS_COMPLETED)
        self.assertEqual(summary.parallel_accounts, 3)
        self.assertEqual(
            sorted(outcome.account_name for outcome in summary.accounts), ["a", "b", "c"]
        )

    def test_parallel_run_writes_every_account_baseline(self):
        """스레드마다 연결을 따로 열어도 결과는 한 DB에 모여야 한다."""

        self._run(parallel_accounts=3)
        conn = scan_store.connect(self.data_dir)
        try:
            for account in self.accounts:
                state = scan_store.get_account_state(conn, account.account_id)
                self.assertEqual(
                    state.last_completed_generation,
                    1,
                    f"{account.name}의 기준선이 저장되지 않았습니다",
                )
        finally:
            conn.close()

    def test_weekday_night_stays_serial(self):
        """평일 밤은 지금까지와 똑같이 하나씩 돈다."""

        summary = self._run()
        self.assertEqual(summary.parallel_accounts, 1)
        self.assertFalse(summary.weekend_night)

    def test_friday_night_is_a_weekend_night(self):
        """금요일 밤의 부하는 토요일 아침에 청구된다 - 그날은 한산하다."""

        summary = self._run(now=self.FRIDAY_NIGHT)
        self.assertTrue(summary.weekend_night)
        self.assertEqual(
            summary.parallel_accounts,
            self.config.settings.weekend_parallel_accounts,
        )

    def test_sunday_night_is_not_a_weekend_night(self):
        """일요일 밤의 부하는 월요일 아침에 전원이 출근한 상태로 청구된다.

        '오늘이 주말인가'로 판정하면 이 밤이 정확히 뒤집힌다."""

        summary = self._run(now=self.SUNDAY_NIGHT)
        self.assertFalse(summary.weekend_night)
        self.assertEqual(summary.parallel_accounts, 1)

    def test_explicit_parallel_beats_the_weekend_rule(self):
        """실측하려면 사람이 지정한 값이 이겨야 한다."""

        summary = self._run(now=self.FRIDAY_NIGHT, parallel_accounts=1)
        self.assertEqual(summary.parallel_accounts, 1)
        # 그래도 '어떤 밤이었나'는 사실대로 남아야 나중에 비교가 된다.
        self.assertTrue(summary.weekend_night)

    def test_parallelism_is_recorded_for_later_comparison(self):
        summary = self._run(parallel_accounts=2)
        conn = scan_store.connect(self.data_dir)
        try:
            run = scan_store.latest_run(conn)
        finally:
            conn.close()
        self.assertEqual(run["run_id"], summary.run_id)
        self.assertEqual(run["parallel_accounts"], 2)

    def test_out_of_range_values_are_clamped_not_rejected(self):
        """야간 cron이 설정 오타 하나로 통째로 안 도는 것이 더 나쁘다."""

        summary = self._run(parallel_accounts=999)
        self.assertEqual(summary.status, nightly_scan.STATUS_COMPLETED)
        self.assertEqual(summary.parallel_accounts, 16)

    def test_baseline_sample_is_recorded_before_work_starts(self):
        summary = self._run(parallel_accounts=2)
        conn = scan_store.connect(self.data_dir)
        try:
            rows = scan_store.load_samples(conn, summary.run_id)
        finally:
            conn.close()
        # /proc이 없는 개발 PC에서도 'before' 행 자체는 남아야 한다 - 값이
        # 비어 있는 것과 표본이 아예 없는 것은 다르다.
        self.assertEqual([row["phase"] for row in rows][:1], ["before"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
