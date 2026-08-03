"""야간 상세 스캔의 급증 알림과 검색 인덱스 통합."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import admin_auth
from smvwp import config as config_module
from smvwp import nightly_scan, notifications, scan_store, search_index, tiers
from smvwp.config import Account
from tests import support

GB_KB = 1024 * 1024  # 1GB를 KB로


class GrowthEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.account = Account(name="project_a", path="/user/project_a", account_id="acct-1")
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_event_records_path_and_delta(self):
        event = notifications.build_growth_event(
            self.account, "/user/project_a/results", 300 * GB_KB, 100 * GB_KB, self.now
        )
        self.assertEqual(event.kind, notifications.KIND_GROWTH)
        self.assertEqual(event.details["path"], "/user/project_a/results")
        self.assertEqual(event.details["delta_kb"], 200 * GB_KB)
        self.assertIn("results", event.message)

    def test_new_path_counts_full_size_as_growth(self):
        event = notifications.build_growth_event(
            self.account, "/user/project_a/new", 150 * GB_KB, None, self.now
        )
        self.assertEqual(event.details["delta_kb"], 150 * GB_KB)
        self.assertIsNone(event.details["previous_kb"])

    def test_capacity_event_keeps_default_kind(self):
        """기존 용량 알림은 종류가 capacity로 남아야 한다 (하위 호환)."""

        from smvwp.store import SampleRecord

        sample = SampleRecord(
            account_id="acct-1", collected_at="t", ok=True, byte_pct=97.0, overall_tier=tiers.ALERT
        )
        event = notifications.build_event(self.account, sample, self.now)
        self.assertEqual(event.kind, notifications.KIND_CAPACITY)


class MaybeNotifyGrowthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.account = Account(name="project_a", path="/user/project_a", account_id="acct-1")
        self.now = datetime.now(timezone.utc)
        self.threshold = 100 * GB_KB

    def tearDown(self):
        self.tmp.cleanup()

    def _notify(self, current_kb, previous_kb, state, now=None):
        return notifications.maybe_notify_growth(
            self.data_dir,
            self.account,
            "/user/project_a/results",
            current_kb,
            previous_kb,
            state,
            min_increase_kb=self.threshold,
            cooldown_minutes=60,
            now=now or self.now,
        )

    def test_growth_above_threshold_notifies(self):
        state = {}
        result = self._notify(250 * GB_KB, 100 * GB_KB, state)
        self.assertIsNotNone(result)
        self.assertTrue(result.ok)

    def test_growth_below_threshold_is_silent(self):
        state = {}
        self.assertIsNone(self._notify(150 * GB_KB, 100 * GB_KB, state))
        self.assertEqual(state, {})

    def test_shrinking_path_is_silent(self):
        state = {}
        self.assertIsNone(self._notify(50 * GB_KB, 300 * GB_KB, state))

    def test_cooldown_suppresses_repeat_for_same_path(self):
        state = {}
        self._notify(250 * GB_KB, 100 * GB_KB, state)
        second = self._notify(
            400 * GB_KB, 100 * GB_KB, state, now=self.now + timedelta(minutes=5)
        )
        self.assertIsNone(second)

    def test_cooldown_expiry_allows_new_alert(self):
        state = {}
        self._notify(250 * GB_KB, 100 * GB_KB, state)
        later = self._notify(
            400 * GB_KB, 100 * GB_KB, state, now=self.now + timedelta(hours=2)
        )
        self.assertIsNotNone(later)

    def test_growth_state_does_not_collide_with_capacity_state(self):
        """용량 알림이 정상 복귀로 리셋될 때 급증 기록까지 지워지면 안 된다."""

        state = {}
        self._notify(250 * GB_KB, 100 * GB_KB, state)
        growth_key = notifications.growth_state_key("acct-1", "/user/project_a/results")
        self.assertIn(growth_key, state)
        self.assertNotIn("acct-1", state)

    def test_separate_paths_have_separate_cooldowns(self):
        state = {}
        self._notify(250 * GB_KB, 100 * GB_KB, state)
        other = notifications.maybe_notify_growth(
            self.data_dir,
            self.account,
            "/user/project_a/other",
            250 * GB_KB,
            100 * GB_KB,
            state,
            min_increase_kb=self.threshold,
            cooldown_minutes=60,
            now=self.now,
        )
        self.assertIsNotNone(other)


class NightlyGrowthIntegrationTests(unittest.TestCase):
    """야간 스캔 -> 급증 알림 -> 검색 인덱스 갱신까지 한 흐름."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.account_path = self.root / "acct"
        (self.account_path / "dir1").mkdir(parents=True)
        (self.account_path / "dir2").mkdir(parents=True)
        (self.account_path / "dir1" / "file.dat").write_text("x", encoding="utf-8")

        self.config = config_module.load_config(self.data_dir)
        self.account = config_module.add_account(
            self.config, "project_a", str(self.account_path), data_dir=self.data_dir
        )
        self.config.settings.growth_alert_min_kb = 100 * GB_KB
        config_module.save_config(self.data_dir, self.config)

        self.top_dirs = [f"{self.account_path}/dir1", f"{self.account_path}/dir2"]

    def tearDown(self):
        self.tmp.cleanup()

    def _run_scan(self, dir1_kb):
        def runner(command, **kwargs):
            if "du" in command:
                size = dir1_kb if command[-1].endswith("dir1") else 10
                return support.completed(command, stdout=f"{size}\t{command[-1]}\n")
            if "find" in command:
                return support.completed(command)
            raise AssertionError(f"예상치 못한 명령: {command}")

        with patch("smvwp.detail_scan.subprocess.run", side_effect=runner):
            return nightly_scan.run_nightly_scan(
                self.data_dir,
                self.config,
                bypass_window=True,
                top_level_lister=lambda p: list(self.top_dirs),
            )

    def _growth_events(self):
        outbox = notifications.outbox_dir(self.data_dir)
        if not outbox.exists():
            return []
        events = [json.loads(p.read_text(encoding="utf-8")) for p in outbox.glob("*.json")]
        return [e for e in events if e.get("kind") == notifications.KIND_GROWTH]

    def test_first_baseline_does_not_alert(self):
        """비교할 이전 세대가 없으면 전부 '신규'라 알림 폭탄이 된다."""

        self._run_scan(dir1_kb=500 * GB_KB)
        self.assertEqual(self._growth_events(), [])

    def test_large_growth_between_generations_alerts(self):
        self._run_scan(dir1_kb=10)
        self._run_scan(dir1_kb=500 * GB_KB)

        events = self._growth_events()
        self.assertEqual(len(events), 1)
        self.assertIn("dir1", events[0]["details"]["path"])
        self.assertGreaterEqual(events[0]["details"]["delta_kb"], 100 * GB_KB)

    def test_small_growth_does_not_alert(self):
        self._run_scan(dir1_kb=10)
        self._run_scan(dir1_kb=20)
        self.assertEqual(self._growth_events(), [])

    def test_growth_alerts_can_be_disabled(self):
        self.config.settings.growth_alert_enabled = False
        self._run_scan(dir1_kb=10)
        self._run_scan(dir1_kb=500 * GB_KB)
        self.assertEqual(self._growth_events(), [])

    def test_search_index_updates_only_when_enabled(self):
        summary = self._run_scan(dir1_kb=10)
        self.assertEqual(summary.accounts[0].search_status, "skipped")
        self.assertFalse(search_index.db_path(self.data_dir).exists())

        self.account.search_indexing = True
        config_module.save_config(self.data_dir, self.config)
        summary = self._run_scan(dir1_kb=10)

        self.assertEqual(summary.accounts[0].search_status, "done")
        self.assertGreater(summary.accounts[0].search_entries, 0)

        conn = search_index.connect(self.data_dir)
        try:
            hits = search_index.search(conn, self.account.account_id, "file.dat")
            self.assertEqual([h.relative_path for h in hits], ["dir1/file.dat"])
        finally:
            conn.close()

    def test_orphan_search_index_is_pruned_on_next_scan(self):
        self.account.search_indexing = True
        config_module.save_config(self.data_dir, self.config)
        self._run_scan(dir1_kb=10)

        # 설정에 없는 계정의 인덱스를 심어 두고 다음 실행에서 정리되는지 본다.
        conn = search_index.connect(self.data_dir)
        try:
            search_index.index_account(conn, "removed-account", self.account_path)
            self.assertGreater(search_index.entry_count(conn, "removed-account"), 0)
        finally:
            conn.close()

        self._run_scan(dir1_kb=10)

        conn = search_index.connect(self.data_dir)
        try:
            self.assertEqual(search_index.entry_count(conn, "removed-account"), 0)
            self.assertGreater(search_index.entry_count(conn, self.account.account_id), 0)
        finally:
            conn.close()

    def test_scan_still_succeeds_when_search_indexing_fails(self):
        """부가 기능 실패가 이미 저장된 기준선을 무효로 만들면 안 된다."""

        self.account.search_indexing = True
        config_module.save_config(self.data_dir, self.config)

        with patch("smvwp.search_index.index_account", side_effect=OSError("디스크 오류")):
            summary = self._run_scan(dir1_kb=10)

        self.assertEqual(summary.status, nightly_scan.STATUS_COMPLETED)
        self.assertEqual(summary.accounts[0].baseline_status, "done")
        self.assertEqual(summary.accounts[0].search_status, "error")


class AdminPinChangeTests(unittest.TestCase):
    def test_default_detection(self):
        self.assertTrue(admin_auth.is_using_default(""))
        self.assertFalse(admin_auth.is_using_default(admin_auth.hash_pin("1234")))

    def test_changed_pin_replaces_default(self):
        stored = admin_auth.hash_pin("9182")
        self.assertTrue(admin_auth.verify_pin("9182", stored))
        # 기본 PIN은 더 이상 통하지 않아야 한다.
        self.assertFalse(admin_auth.verify_pin(admin_auth.DEFAULT_PIN, stored))

    def test_session_uses_stored_hash(self):
        stored = admin_auth.hash_pin("9182")
        session = admin_auth.AdminSession()
        self.assertFalse(session.unlock(admin_auth.DEFAULT_PIN, stored))
        self.assertTrue(session.unlock("9182", stored))

    def test_pin_hash_persists_through_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            config = config_module.load_config(data_dir)
            config.settings.admin_pin_hash = admin_auth.hash_pin("5555")
            config_module.save_config(data_dir, config)

            reloaded = config_module.load_config(data_dir)
            self.assertTrue(admin_auth.verify_pin("5555", reloaded.settings.admin_pin_hash))
            # 평문이 설정 파일에 남지 않아야 한다.
            raw = config_module.config_file(data_dir).read_text(encoding="utf-8")
            self.assertNotIn("5555", raw)


if __name__ == "__main__":
    unittest.main()
