"""계정 하나에 대해 아는 것을 모으는 부분 (Qt 없음).

여기서 지키는 것은 하나다: **한 조각이 터져도 나머지는 온다.** 헬스체크가
실패했다고 사용률까지 못 보게 되면 훨씬 나쁜 실패다. 무엇을 못 읽었는지는
조용히 삼키지 않고 `failures` 로 말한다 - 빈 칸은 "없다"로 읽힌다.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import account_detail, config as config_module, health, store

GB = 1024 * 1024


class AccountDetailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        path = root / "proj"
        path.mkdir()
        self.account = config_module.add_account(
            self.config, "layout_proj", str(path), data_dir=self.data_dir
        )
        config_module.save_config(self.data_dir, self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def _sample(self, hours_ago=1, pct=82.0):
        now = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        conn = store.connect(self.data_dir)
        try:
            store.insert_sample(conn, store.SampleRecord(
                account_id=self.account.account_id,
                collected_at=now.isoformat(), ok=True,
                filesystem="nfs4", mount_point="/ifs",
                total_kb=1000 * GB, used_kb=int(1000 * GB * pct / 100),
                avail_kb=1, byte_pct=pct, byte_tier="warn", overall_tier="warn",
            ))
        finally:
            conn.close()

    def read(self):
        return account_detail.read(self.data_dir, self.config, self.account.account_id)

    # -- 기본 ------------------------------------------------------------
    def test_an_unknown_account_gives_nothing(self):
        self.assertIsNone(
            account_detail.read(self.data_dir, self.config, "does-not-exist")
        )

    def test_a_fresh_install_does_not_look_like_a_failure(self):
        """아무것도 없는 상태에서 실패 목록이 차면 사람이 고장으로 읽는다."""

        detail = self.read()
        self.assertEqual(detail.failures, [])
        self.assertIsNone(detail.sample)
        self.assertEqual(detail.large_files, [])

    def test_the_latest_sample_comes_back(self):
        self._sample(pct=82.0)
        detail = self.read()
        self.assertIsNotNone(detail.sample)
        self.assertAlmostEqual(detail.sample.byte_pct, 82.0)
        self.assertEqual(detail.tier, "warn")

    def test_the_trend_is_limited_to_the_window(self):
        """창을 안 주면 표본 하루치가 90일 그래프를 꽉 채운다."""

        self._sample(hours_ago=1)
        detail = account_detail.read(
            self.data_dir, self.config, self.account.account_id, trend_days=7
        )
        self.assertIsNotNone(detail.series)
        self.assertFalse(detail.series.empty)

    # -- 한 조각이 터져도 -------------------------------------------------
    def test_a_broken_health_check_does_not_take_the_rest_down(self):
        self._sample(pct=91.0)
        with patch("smvwp.health.check_all", side_effect=RuntimeError("nope")):
            detail = self.read()
        self.assertIn(account_detail.PART_HEALTH, detail.failures)
        # 나머지는 그대로다.
        self.assertIsNotNone(detail.sample)
        self.assertEqual(detail.health, [])

    def test_a_broken_forecast_does_not_take_the_rest_down(self):
        self._sample()
        with patch(
            "smvwp.forecast_notify.build_forecasts", side_effect=RuntimeError("nope")
        ):
            detail = self.read()
        self.assertIn(account_detail.PART_FORECAST, detail.failures)
        self.assertIsNotNone(detail.sample)

    def test_a_broken_scan_read_does_not_take_the_rest_down(self):
        self._sample()
        with patch(
            "smvwp.nightly_scan.get_status_snapshot", side_effect=RuntimeError("nope")
        ):
            detail = self.read()
        self.assertIn(account_detail.PART_SCAN, detail.failures)
        self.assertIsNotNone(detail.sample)

    # -- 파생값 -----------------------------------------------------------
    def test_growth_falls_back_to_the_biggest_paths(self):
        """비교할 이전 스캔이 없으면 증감 대신 큰 경로를 보여 준다."""

        class Scan:
            growth = []
            top_paths = [{"path": "/a", "size_kb": 10}]
            measured_kb = None
            previous_measured_kb = None

        detail = account_detail.AccountDetail("id", "name", "/acct")
        detail.scan = Scan()
        self.assertEqual(len(detail.growth_rows), 1)
        self.assertIsNone(detail.measured_delta_kb)

    def test_only_confirmed_backups_count_as_reclaimable(self):
        """확인 안 된 것을 더하면 "이만큼 비울 수 있다"가 실제보다 커진다."""

        detail = account_detail.AccountDetail("id", "name", "/acct")
        detail.health = [
            health.RunHealth("id", "name", "/acct/t/01_run", "t / 01_run",
                             run_size_kb=100 * GB, backup_size_kb=100 * GB,
                             mirror_size_kb=100 * GB, status=health.BACKED_UP),
            health.RunHealth("id", "name", "/acct/t/02_run", "t / 02_run",
                             run_size_kb=500 * GB, status=health.MISSING),
        ]
        self.assertEqual(len(detail.backed_up), 1)
        self.assertEqual(len(detail.at_risk), 1)
        self.assertEqual(detail.reclaimable_kb, 100 * GB)
        self.assertTrue(detail.has_backup_link)

    def test_without_a_linked_backup_nothing_is_claimed(self):
        detail = account_detail.AccountDetail("id", "name", "/acct")
        detail.health = [
            health.RunHealth("id", "name", "/acct/t/01_run", "t / 01_run",
                             status=health.NO_LINK),
        ]
        self.assertFalse(detail.has_backup_link)
        self.assertEqual(detail.reclaimable_kb, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
