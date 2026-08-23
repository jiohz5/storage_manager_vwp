"""주간 보고서의 '평일 밤 vs 주말 밤' 부하 비교.

이 섹션의 목적은 하나다 - **보고서 숫자만 보고 주말 병렬을 유지할지 내릴지
판단할 수 있어야 한다.** 그래서 여기 테스트는 표가 그려지는지가 아니라
'판단을 그르치지 않는가'를 지킨다.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smvwp import config as config_module
from smvwp import i18n, loadstat, reports, scan_store


class NightComparisonTests(unittest.TestCase):
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
        self.now = datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc)

    def tearDown(self):
        i18n.set_language(self._original_language)
        self.tmp.cleanup()

    # -- 도우미 --------------------------------------------------------
    def _night(self, days_ago, weekend, parallel, before_load, peak_load, hours=6.0):
        start = self.now - timedelta(days=days_ago)
        run_id = f"run-{days_ago}"
        conn = scan_store.connect(self.data_dir)
        try:
            scan_store.start_run(conn, run_id, "cron")
            scan_store.record_parallelism(conn, run_id, parallel, weekend)
            scan_store.save_load_samples(conn, run_id, [
                loadstat.Snapshot(
                    sampled_at=start.isoformat(), elapsed_seconds=0.0,
                    phase=loadstat.PHASE_BEFORE, load_avg_1m=before_load,
                    cpu_iowait_percent=1.0,
                ),
                loadstat.Snapshot(
                    sampled_at=(start + timedelta(hours=1)).isoformat(),
                    elapsed_seconds=3600, phase=loadstat.PHASE_DURING,
                    load_avg_1m=peak_load, cpu_iowait_percent=peak_load * 6,
                    active_accounts=parallel,
                ),
            ])
            conn.execute(
                "UPDATE scan_runs SET started_at = ?, ended_at = ?, status = 'completed' "
                "WHERE run_id = ?",
                (start.isoformat(),
                 (start + timedelta(hours=hours)).isoformat(), run_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _report(self):
        return reports.build_weekly_report(self.data_dir, self.config, now=self.now)

    # -- 테스트 --------------------------------------------------------
    def test_section_absent_without_any_measurements(self):
        self.assertNotIn(i18n.t("reports.night_heading"), self._report())

    def test_groups_weekday_and_weekend_nights(self):
        self._night(1, weekend=True, parallel=3, before_load=0.4, peak_load=9.0)
        self._night(2, weekend=False, parallel=1, before_load=0.4, peak_load=3.0)
        text = self._report()
        self.assertIn(i18n.t("reports.night_heading"), text)
        self.assertIn(i18n.t("reports.night_weekday"), text)
        self.assertIn(i18n.t("reports.night_weekend"), text)

    def test_delta_cancels_out_a_different_ambient_load(self):
        """절대값으로 비교하면 그날 다른 배치가 돌았다는 이유만으로 스캔이
        무거웠다고 잘못 읽는다.

        아래 두 밤은 스캔이 만든 증가폭이 +2.0으로 똑같고, 평소 부하만 다르다.
        비교 표에는 같은 값이 나와야 한다."""

        self._night(1, weekend=False, parallel=1, before_load=0.5, peak_load=2.5)
        self._night(2, weekend=False, parallel=1, before_load=4.0, peak_load=6.0)
        nights = reports.collect_night_loads(self.data_dir)
        self.assertEqual([round(n.load_delta, 2) for n in nights], [2.0, 2.0])

    def test_uses_median_so_one_heavy_night_does_not_decide(self):
        """밤 하나가 유난히 무거울 수 있다 (큰 계정이 몰렸거나 다른 작업과 겹쳤거나).

        표본이 한 주에 몇 개뿐이라 평균이면 그 하나가 결론을 뒤집는다."""

        for index, peak in enumerate((2.0, 2.2, 2.1, 30.0), start=1):
            self._night(index, weekend=False, parallel=1, before_load=0.0, peak_load=peak)
        nights = [n for n in reports.collect_night_loads(self.data_dir) if n.has_numbers]
        deltas = [n.load_delta for n in nights]
        median = reports._median_of(deltas)
        mean = sum(deltas) / len(deltas)
        self.assertAlmostEqual(median, 2.15)
        # 평균이었다면 9를 넘어 "평일 밤이 주말보다 무겁다"는 엉뚱한 결론이 난다.
        self.assertGreater(mean, 8)

    def test_old_runs_fall_outside_the_window(self):
        self._night(2, weekend=False, parallel=1, before_load=0.4, peak_load=3.0)
        self._night(
            reports.NIGHT_COMPARE_DAYS + 5, weekend=True, parallel=3,
            before_load=0.4, peak_load=9.0,
        )
        nights = reports.collect_night_loads(self.data_dir)
        self.assertEqual(len(nights), 1)
        self.assertFalse(nights[0].weekend_night)

    def test_weekend_flag_survives_a_reopen(self):
        """밤 성격은 나중에 started_at으로 다시 계산하면 안 된다 - 시간창 설정이
        바뀌면 같은 밤이 다르게 판정된다. 기록한 사실이 그대로 남아야 한다."""

        self._night(1, weekend=True, parallel=3, before_load=0.4, peak_load=9.0)
        conn = scan_store.connect(self.data_dir)
        try:
            run = scan_store.latest_run(conn)
        finally:
            conn.close()
        self.assertEqual(run["weekend_night"], 1)
        self.assertEqual(run["parallel_accounts"], 3)

    def test_scan_duration_is_reported(self):
        """부하가 늘어도 시간이 줄었으면 이득이다 - 그 판단에 시간이 필요하다."""

        self._night(1, weekend=True, parallel=3, before_load=0.4, peak_load=9.0, hours=4.0)
        nights = reports.collect_night_loads(self.data_dir)
        self.assertAlmostEqual(nights[0].duration_hours, 4.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
