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


class ScheduledReportTests(unittest.TestCase):
    """일간 보고서는 **수집 경로**에서 만들어져야 한다.

    예전에는 야간 상세 스캔이 끝날 때만 만들었는데, 스캔은 시간창 밖이거나
    잠금이 잡혀 있으면 아예 시작하지 않고 그대로 돌아간다. 그러면 일간
    보고서까지 함께 사라졌다 - 정작 그 내용은 df 표본 요약이라 스캔과 아무
    상관이 없는데도.
    """

    def _config(self):
        return config_module.AppConfig(settings=config_module.Settings(), accounts=[])

    def test_daily_is_due_when_missing_and_not_after_creating(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config = self._config()
            now = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)

            self.assertIn(reports.DAILY, reports.due_kinds(data_dir, config, now))
            reports.ensure_scheduled(data_dir, config, now)
            self.assertNotIn(reports.DAILY, reports.due_kinds(data_dir, config, now))

    def test_weekly_catches_up_when_the_day_was_missed(self):
        """그날 장비가 꺼져 있었다고 그 주 보고서가 영영 사라지면 안 된다."""

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config = self._config()
            config.settings.weekly_report_weekday = 4  # 금요일

            # 금요일을 건너뛰고 일요일에 처음 열었다
            sunday = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)
            self.assertEqual(sunday.weekday(), 6)
            self.assertIn(reports.WEEKLY, reports.due_kinds(data_dir, config, sunday))

            reports.ensure_scheduled(data_dir, config, sunday)
            self.assertNotIn(reports.WEEKLY, reports.due_kinds(data_dir, config, sunday))

    def test_weekly_becomes_due_again_next_week(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config = self._config()
            config.settings.weekly_report_weekday = 4

            friday = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
            reports.ensure_scheduled(data_dir, config, friday)
            self.assertNotIn(reports.WEEKLY, reports.due_kinds(data_dir, config, friday))

            next_friday = friday + timedelta(days=7)
            self.assertIn(reports.WEEKLY, reports.due_kinds(data_dir, config, next_friday))

    def test_last_weekly_due_date_is_the_most_recent_target_weekday(self):
        config = self._config()
        config.settings.weekly_report_weekday = 4  # 금
        sunday = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(reports.last_weekly_due_date(config, sunday).isoformat(), "2026-08-21")
        friday = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(reports.last_weekly_due_date(config, friday).isoformat(), "2026-08-21")


class ScanProgressInReportTests(unittest.TestCase):
    """보고서에 `du` 진행 개수와 **마지막으로 처리한 경로**를 적는다.

    개수만 적으면 얼마나 남았는지는 알아도 어느 대목에서 멈췄는지는 모른다.
    밤새 돌다 끊긴 스캔을 아침에 볼 때 필요한 것은 후자다.
    """

    def _setup(self, tmp):
        data_dir = Path(tmp)
        config = config_module.AppConfig(settings=config_module.Settings(), accounts=[])
        watched = data_dir / "watched"
        watched.mkdir(parents=True)
        config_module.add_account(config, "project_a", str(watched))
        account = config.accounts[0]

        conn = scan_store.connect(data_dir)
        try:
            generation = scan_store.get_account_state(conn, account.account_id).working_generation
            paths = [f"/user/project_a/dir{i}" for i in range(4)]
            scan_store.seed_checkpoints(conn, account.account_id, scan_store.BASELINE, generation, paths)
            first = scan_store.next_pending(conn, account.account_id, scan_store.BASELINE, generation)
            scan_store.mark_done(conn, first["id"], size_kb=1024)
        finally:
            conn.close()
        return data_dir, config, account

    def test_daily_report_includes_progress_and_last_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir, config, _ = self._setup(tmp)
            text = reports.build_daily_report(data_dir, config, datetime.now(timezone.utc))

        self.assertIn("1/4", text)          # 완료 1 / 전체 4
        self.assertIn("/user/project_a/dir0", text)

    def test_no_scan_yet_adds_no_section(self):
        """스캔을 한 번도 안 돌린 상태에서 빈 절이 붙으면 지저분하다."""

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config = config_module.AppConfig(settings=config_module.Settings(), accounts=[])
            text = reports.build_daily_report(data_dir, config, datetime.now(timezone.utc))

        self.assertNotIn(i18n.t("reports.scan_progress_heading"), text)

    def test_weekly_uses_scan_wording_not_generation(self):
        """'세대'는 내부 개념이다 - 사용자는 '몇 번째 스캔'으로 읽는다."""

        with tempfile.TemporaryDirectory() as tmp:
            data_dir, config, account = self._setup(tmp)
            conn = scan_store.connect(data_dir)
            try:
                generation = scan_store.get_account_state(conn, account.account_id).working_generation
                while True:
                    checkpoint = scan_store.next_pending(
                        conn, account.account_id, scan_store.BASELINE, generation
                    )
                    if checkpoint is None:
                        break
                    scan_store.mark_done(conn, checkpoint["id"], size_kb=2048)
                scan_store.save_baseline_results(
                    conn, account.account_id, generation,
                    scan_store.leaf_results(conn, account.account_id, generation),
                )
                scan_store.mark_generation_completed(conn, account.account_id, generation)
            finally:
                conn.close()

            text = reports.build_weekly_report(data_dir, config, datetime.now(timezone.utc))

        self.assertIn("번째 스캔", text)
        self.assertNotIn("generation ", text)
