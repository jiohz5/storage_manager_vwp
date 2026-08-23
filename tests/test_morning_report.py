"""하룻밤 스캔의 결과가 **다음날 아침** 일간 보고서에 들어가는가.

야간 스캔은 자정을 넘어간다. 그래서 "언제 시작했나"와 "언제 끝났나"는 다른
날이고, 보고서 날짜를 시작 시각으로 매기면 목요일 밤 결과가 목요일자 파일로
가는데 정작 그것을 읽는 사람은 금요일 아침에 금요일자 보고서를 연다.

한 번 실제로 그랬다. 이 파일은 그 회귀를 막는다.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smvwp import config as config_module
from smvwp import i18n, loadstat, nightly_scan, reports, scan_store, store, tiers

# 목요일 밤에 시작해 금요일 아침에 끝나는 한 밤.
SCAN_START = datetime(2026, 8, 20, 22, 0, tzinfo=timezone.utc)
COLLECT_AT = datetime(2026, 8, 21, 0, 15, tzinfo=timezone.utc)
SCAN_END = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)


class MorningReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        account_path = self.root / "proj"
        account_path.mkdir(parents=True)

        self.config = config_module.load_config(self.data_dir)
        account = config_module.add_account(
            self.config, "layout_proj", str(account_path), data_dir=self.data_dir
        )
        conn = store.connect(self.data_dir)
        try:
            store.insert_sample(conn, store.SampleRecord(
                account_id=account.account_id,
                collected_at=SCAN_START.isoformat(), ok=True,
                filesystem="/dev/sdb1", total_kb=1000, used_kb=880,
                byte_pct=88.0, byte_tier=tiers.WARN, inode_pct=11.0,
                inode_tier=tiers.NORMAL, overall_tier=tiers.WARN,
            ))
        finally:
            conn.close()

        self._original_language = i18n.get_language()
        i18n.set_language(i18n.KOREAN)

    def tearDown(self):
        i18n.set_language(self._original_language)
        self.tmp.cleanup()

    def _run_one_night(self):
        """실제 순서 그대로: 스캔 시작 -> 수집기가 오늘자 보고서 생성 ->
        스캔 종료(표본 저장) -> 스캔이 보고서 재생성."""

        conn = scan_store.connect(self.data_dir)
        try:
            scan_store.start_run(conn, "run-night", "cron")
            scan_store.record_parallelism(conn, "run-night", 1, False)
            conn.execute(
                "UPDATE scan_runs SET started_at = ? WHERE run_id = ?",
                (SCAN_START.isoformat(), "run-night"),
            )
            conn.commit()
        finally:
            conn.close()

        # 수집기는 15분마다 돈다. 스캔이 아직 도는 00:15에 오늘자 보고서를
        # 먼저 만들어 버린다 - 이때는 표본이 아직 메모리에 있어 DB에 없다.
        reports.ensure_scheduled(self.data_dir, self.config, now=COLLECT_AT)

        conn = scan_store.connect(self.data_dir)
        try:
            samples = [loadstat.Snapshot(
                sampled_at=SCAN_START.isoformat(), elapsed_seconds=0.0,
                phase=loadstat.PHASE_BEFORE, load_avg_1m=0.31,
                cpu_iowait_percent=0.4, cpu_busy_percent=3.1,
            )]
            for step in range(1, 5):
                samples.append(loadstat.Snapshot(
                    sampled_at=(SCAN_START + timedelta(hours=step)).isoformat(),
                    elapsed_seconds=3600 * step, phase=loadstat.PHASE_DURING,
                    load_avg_1m=1.0 + step, cpu_iowait_percent=10.0 * step,
                    cpu_busy_percent=2.0 + step, active_accounts=1,
                ))
            scan_store.save_load_samples(conn, "run-night", samples)
            scan_store.finish_run(conn, "run-night", "completed")
        finally:
            conn.close()

        nightly_scan._generate_reports(self.data_dir, self.config, SCAN_END)

    def _daily_text(self, day):
        path = reports.report_path(self.data_dir, reports.DAILY, day, i18n.KOREAN)
        return path.read_text(encoding="utf-8") if path.exists() else None

    # -- 테스트 --------------------------------------------------------
    def test_resource_numbers_land_in_the_morning_report(self):
        """금요일 아침에 금요일자 보고서를 열면 간밤의 수치가 있어야 한다."""

        self._run_one_night()
        text = self._daily_text(SCAN_END.date())
        self.assertIsNotNone(text, "금요일자 일간 보고서가 없습니다")
        self.assertIn(i18n.t("reports.resource_heading"), text)

    def test_one_night_alone_gives_before_and_peak(self):
        """비교용 과거 데이터 없이 **하룻밤만으로도** 숫자가 읽혀야 한다."""

        self._run_one_night()
        text = self._daily_text(SCAN_END.date())
        self.assertIn("0.31", text)   # 스캔 전 기준값
        self.assertIn("5.00", text)   # 스캔 중 최고 load
        self.assertIn("+4.69", text)  # 증가폭

    def test_late_collector_run_does_not_wipe_the_numbers(self):
        """스캔이 끝난 뒤 수집기가 또 돌아도 수치가 사라지면 안 된다.

        `ensure_scheduled`는 이미 만든 보고서를 다시 만들지 않으므로 그대로
        남아야 한다 - 다시 만들면 오히려 정상이지만, '안 만든다'에 기대고
        있으므로 그 가정을 고정해 둔다."""

        self._run_one_night()
        reports.ensure_scheduled(
            self.data_dir, self.config, now=SCAN_END + timedelta(hours=2)
        )
        self.assertIn(
            i18n.t("reports.resource_heading"), self._daily_text(SCAN_END.date())
        )

    def test_report_is_not_filed_under_the_start_date(self):
        """시작 날짜(목요일)로 매기면 아무도 안 여는 파일에 들어간다."""

        self._run_one_night()
        self.assertIsNone(
            self._daily_text(SCAN_START.date()),
            "스캔 시작 날짜로 보고서가 만들어졌습니다 - 아침에 여는 것은 종료 날짜입니다",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
