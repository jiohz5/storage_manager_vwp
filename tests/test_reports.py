import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smvwp import config as config_module
from smvwp import i18n, reports, scan_store, store, tiers


def _sample(account_id, byte_pct, tier):
    return store.SampleRecord(
        account_id=account_id,
        collected_at=datetime.now(timezone.utc).isoformat(),
        ok=True,
        filesystem="/dev/sda1",
        byte_pct=byte_pct,
        byte_tier=tier,
        inode_pct=10.0,
        inode_tier=tiers.NORMAL,
        overall_tier=tier,
    )


class ReportBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.account_path = self.root / "acct"
        self.account_path.mkdir(parents=True)

        self.config = config_module.load_config(self.data_dir)
        self.account = config_module.add_account(
            self.config, "project_a", str(self.account_path), data_dir=self.data_dir
        )
        config_module.save_config(self.data_dir, self.config)

        self._original_language = i18n.get_language()

    def tearDown(self):
        i18n.set_language(self._original_language)
        self.tmp.cleanup()

    def _store_sample(self, byte_pct, tier):
        conn = store.connect(self.data_dir)
        try:
            store.insert_sample(conn, _sample(self.account.account_id, byte_pct, tier))
        finally:
            conn.close()

    def test_daily_report_lists_account_and_tier(self):
        self._store_sample(96.0, tiers.ALERT)
        text = reports.build_daily_report(self.data_dir, self.config)
        self.assertIn("project_a", text)
        self.assertIn("96.0%", text)
        self.assertIn(tiers.label(tiers.ALERT), text)

    def test_daily_report_includes_df_caveat(self):
        """df가 파일시스템 전체 값이라는 주의는 보고서에도 있어야 한다."""

        self._store_sample(50.0, tiers.NORMAL)
        text = reports.build_daily_report(self.data_dir, self.config)
        self.assertIn("df", text)

    def test_generate_writes_both_languages(self):
        self._store_sample(50.0, tiers.NORMAL)
        reports.generate(self.data_dir, self.config, kinds=[reports.DAILY])

        for language in (i18n.KOREAN, i18n.ENGLISH):
            self.assertTrue(reports.latest_path(self.data_dir, reports.DAILY, language).exists())

    def test_generate_restores_original_language(self):
        i18n.set_language(i18n.KOREAN)
        self._store_sample(50.0, tiers.NORMAL)
        reports.generate(self.data_dir, self.config, kinds=[reports.DAILY])
        self.assertEqual(i18n.get_language(), i18n.KOREAN)

    def test_weekly_scheduled_on_configured_weekday(self):
        self.config.settings.weekly_report_weekday = 4  # 금요일
        friday = datetime(2026, 7, 31, 23, 0)  # 금요일
        thursday = friday - timedelta(days=1)
        self.assertTrue(reports.should_build_weekly(self.config, friday))
        self.assertFalse(reports.should_build_weekly(self.config, thursday))

    def test_prune_removes_only_old_dated_reports(self):
        self._store_sample(50.0, tiers.NORMAL)
        now = datetime.now(timezone.utc)
        reports.generate(self.data_dir, self.config, kinds=[reports.DAILY], now=now)

        old_day = (now - timedelta(days=400)).date()
        old_path = reports.report_path(self.data_dir, reports.DAILY, old_day, i18n.KOREAN)
        old_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.write_text("old", encoding="utf-8")

        removed = reports.prune_old_reports(self.data_dir, 365, now)
        self.assertEqual(removed, 1)
        self.assertFalse(old_path.exists())
        # latest_* 는 날짜 파일이 아니므로 남아야 한다.
        self.assertTrue(reports.latest_path(self.data_dir, reports.DAILY, i18n.KOREAN).exists())


class CleanupCandidateTests(unittest.TestCase):
    """정리 후보는 '지워도 될 것 같은 목록'일 뿐 - 절대 지우지 않는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.account_path = self.root / "acct"
        self.account_path.mkdir(parents=True)

        self.config = config_module.load_config(self.data_dir)
        self.account = config_module.add_account(
            self.config, "project_a", str(self.account_path), data_dir=self.data_dir
        )
        self.config.settings.cleanup_min_size_kb = 1000
        config_module.save_config(self.data_dir, self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def _record(self, generation, path, size_kb):
        conn = scan_store.connect(self.data_dir)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO baseline_results "
                "(account_id, generation, path, size_kb, completed_at) VALUES (?, ?, ?, ?, ?)",
                (self.account.account_id, generation, path, size_kb, "2026-07-31T00:00:00+00:00"),
            )
            conn.commit()
            scan_store.mark_generation_completed(conn, self.account.account_id, generation)
        finally:
            conn.close()

    def test_large_and_unchanged_over_two_generations_is_a_candidate(self):
        self._record(1, "/acct/archive", 5000)
        self._record(2, "/acct/archive", 5000)

        candidates = reports.build_cleanup_candidates(self.data_dir, self.config)
        self.assertEqual([c.path for c in candidates], ["/acct/archive"])
        self.assertTrue(candidates[0].unchanged)

    def test_changed_size_is_not_a_candidate(self):
        """최근에 변한 것은 '지금 쓰는 데이터'일 수 있으므로 제외."""

        self._record(1, "/acct/active", 5000)
        self._record(2, "/acct/active", 6000)

        self.assertEqual(reports.build_cleanup_candidates(self.data_dir, self.config), [])

    def test_single_generation_is_not_a_candidate(self):
        """관찰 이력이 한 세대뿐이면 '변화 없음'을 주장할 수 없다."""

        self._record(1, "/acct/fresh", 5000)
        self.assertEqual(reports.build_cleanup_candidates(self.data_dir, self.config), [])

    def test_small_path_is_not_a_candidate(self):
        self._record(1, "/acct/small", 10)
        self._record(2, "/acct/small", 10)
        self.assertEqual(reports.build_cleanup_candidates(self.data_dir, self.config), [])

    def test_path_missing_from_latest_generation_is_not_a_candidate(self):
        """이미 사라진 경로를 후보로 올리지 않는다."""

        self._record(1, "/acct/gone", 5000)
        self._record(2, "/acct/other", 5000)
        self._record(3, "/acct/other", 5000)

        candidates = reports.build_cleanup_candidates(self.data_dir, self.config)
        self.assertNotIn("/acct/gone", [c.path for c in candidates])

    def test_report_states_nothing_is_deleted(self):
        self._record(1, "/acct/archive", 5000)
        self._record(2, "/acct/archive", 5000)

        text = reports.build_cleanup_report(self.data_dir, self.config)
        self.assertIn("삭제하지 않습니다", text)
        self.assertIn("/acct/archive", text)

    def test_report_handles_no_candidates(self):
        text = reports.build_cleanup_report(self.data_dir, self.config)
        self.assertIn("정리 후보가 없습니다", text)


if __name__ == "__main__":
    unittest.main()
