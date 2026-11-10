"""야간 상세 스캔이 계정을 도는 순서.

## 왜 이것이 생겼나

순서는 '오래전에 끝낸 계정부터' 하나였다. 모든 계정을 같은 간격으로 도는 것이라
공평하지만, 계정이 백 개쯤이고 한 바퀴가 여러 밤이면 **어제 500GB 늘어난 계정도
자기 차례가 올 때까지 며칠을 기다렸다.** 그동안 화면의 '어디가 늘었나' 는 며칠 전
이야기다. 반대로 몇 주째 그대로인 보관 계정은 꼬박꼬박 다시 쟀다.

15분마다 모으는 `df` 표본은 마지막 스캔 뒤로 사용량이 얼마나 변했는지를 이미
알려 준다. 많이 변한 계정을 먼저 재고, 그대로인 계정은 뒤로 미루되 **상한
(`detail_scan_max_age_days`)을 넘기지는 않는다.**
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from smvwp import config as config_module
from smvwp import nightly_scan, scan_order, scan_store, store, tiers
from tests import support

GB = 1024 * 1024
TB = 1024 * GB
NOW = datetime(2026, 11, 10, 21, 0)


def cand(name, index=0, days_ago=None, drift=None, threshold=100 * GB,
         tier=tiers.NORMAL, in_progress=False):
    return scan_order.Candidate(
        account_id=name, index=index, in_progress=in_progress,
        last_completed_at=NOW - timedelta(days=days_ago) if days_ago is not None else None,
        drift_kb=drift, threshold_kb=threshold, tier=tier,
    )


def ordered(candidates, max_age_days=14):
    return [(item.account_id, reason) for item, reason
            in scan_order.order(candidates, NOW, max_age_days)]


class OrderTests(unittest.TestCase):
    def test_a_changed_account_goes_before_an_older_quiet_one(self):
        result = ordered([
            cand("quiet", 0, days_ago=6, drift=2 * GB),
            cand("hot", 1, days_ago=1, drift=500 * GB),
        ])
        self.assertEqual(result, [("hot", scan_order.REASON_CHANGED),
                                  ("quiet", scan_order.REASON_AGE)])

    def test_nothing_waits_past_the_limit(self):
        """그대로인 계정도 상한을 넘기면 변한 계정보다 먼저다 - 그래야 굶지 않는다."""

        result = ordered([
            cand("hot", 0, days_ago=1, drift=500 * GB),
            cand("stale", 1, days_ago=15, drift=0),
        ], max_age_days=14)
        self.assertEqual([name for name, _ in result], ["stale", "hot"])
        self.assertEqual(result[0][1], scan_order.REASON_OVERDUE)

    def test_unfinished_and_never_scanned_still_come_first(self):
        result = ordered([
            cand("hot", 0, days_ago=1, drift=900 * GB),
            cand("never", 1),
            cand("half", 2, days_ago=3, in_progress=True),
            cand("stale", 3, days_ago=30),
        ])
        self.assertEqual([name for name, _ in result], ["half", "never", "stale", "hot"])
        self.assertEqual([reason for _, reason in result][:2],
                         [scan_order.REASON_RESUME, scan_order.REASON_NEVER])

    def test_among_changed_the_fuller_storage_first_then_the_bigger_change(self):
        result = ordered([
            cand("big_change", 0, days_ago=2, drift=900 * GB, tier=tiers.NORMAL),
            cand("nearly_full", 1, days_ago=1, drift=150 * GB, tier=tiers.ALERT),
            cand("small_change", 2, days_ago=3, drift=200 * GB, tier=tiers.NORMAL),
        ])
        self.assertEqual([name for name, _ in result],
                         ["nearly_full", "big_change", "small_change"])

    def test_the_bar_is_the_accounts_own(self):
        """30TB 계정의 300GB 는 문턱(600GB) 아래다 - 변한 계정으로 치지 않는다."""

        result = ordered([
            cand("huge", 0, days_ago=2, drift=300 * GB, threshold=600 * GB),
            cand("small", 1, days_ago=1, drift=150 * GB, threshold=100 * GB),
        ])
        self.assertEqual(result, [("small", scan_order.REASON_CHANGED),
                                  ("huge", scan_order.REASON_AGE)])

    def test_without_usage_it_is_oldest_first_as_before(self):
        result = ordered([cand("b", 0, days_ago=2), cand("a", 1, days_ago=5), cand("c", 2, days_ago=3)])
        self.assertEqual([name for name, _ in result], ["a", "c", "b"])

    def test_ties_keep_the_configured_order(self):
        result = ordered([cand("x", 1), cand("y", 0)])
        self.assertEqual([name for name, _ in result], ["y", "x"])


class UsageTests(unittest.TestCase):
    def sample(self, used=None, quota=None, ok=True):
        return SimpleNamespace(ok=ok, used_kb=used, quota_used_kb=quota)

    def test_quota_is_the_accounts_own_number(self):
        self.assertEqual(scan_order.usage_kb(self.sample(used=900, quota=300)), 300)

    def test_otherwise_the_filesystem(self):
        self.assertEqual(scan_order.usage_kb(self.sample(used=900)), 900)

    def test_a_failed_sample_says_nothing(self):
        self.assertIsNone(scan_order.usage_kb(self.sample(used=900, ok=False)))
        self.assertIsNone(scan_order.usage_kb(None))

    def test_drift_is_how_far_it_moved_either_way(self):
        self.assertEqual(scan_order.drift_kb(self.sample(used=700), 900), 200)
        self.assertEqual(scan_order.drift_kb(self.sample(used=900), 700), 200)
        self.assertIsNone(scan_order.drift_kb(self.sample(used=900), None))


class NightlyOrderTests(unittest.TestCase):
    """야간 스캔이 실제로 그 순서로 도는가 - 표본 DB 와 스캔 DB 를 함께 본다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        for name in ("quiet", "hot"):
            (root / name).mkdir()
            config_module.add_account(self.config, name, str(root / name), data_dir=self.data_dir)
        self.quiet, self.hot = self.config.accounts
        self.now = datetime.now()

    def utc(self, local):
        return local.astimezone(timezone.utc).isoformat()

    def sample(self, account, used_kb, at):
        with store.session(self.data_dir) as conn:
            store.insert_samples(conn, [store.SampleRecord(
                account_id=account.account_id, collected_at=self.utc(at), ok=True,
                filesystem=f"fs:/{account.name}", mount_point=account.path,
                total_kb=10 * TB, used_kb=used_kb, avail_kb=10 * TB - used_kb,
                byte_pct=used_kb / (10 * TB) * 100, overall_tier=tiers.NORMAL,
            )])

    def scan(self, account, size_kb, finished):
        """계정 하나를 한 바퀴 재고, 그때의 사용량을 남긴다 (야간 스캔이 하는 대로)."""

        top = f"{account.path}/top"
        with scan_store.session(self.data_dir) as conn:
            state = scan_store.get_account_state(conn, account.account_id)
            generation = state.working_generation
            support.scan_pass(conn, account.account_id, generation, [top],
                              {top: support.whole({top: size_kb})})
            scan_store.mark_generation_completed(
                conn, account.account_id, generation, completed_at=self.utc(finished),
            )
        nightly_scan.record_scan_usage(self.data_dir, account.account_id)

    def names(self):
        return [account.name for account in nightly_scan._fair_order(
            self.data_dir, self.config.accounts, self.config.settings, self.now)]

    def test_the_account_that_grew_since_its_scan_goes_first(self):
        self.sample(self.quiet, 2 * TB, self.now - timedelta(days=6, hours=1))
        self.scan(self.quiet, 2 * TB, self.now - timedelta(days=6))
        self.sample(self.hot, 2 * TB, self.now - timedelta(days=1, hours=1))
        self.scan(self.hot, 2 * TB, self.now - timedelta(days=1))
        # 그 뒤로 hot 만 500GB 늘었다.
        self.sample(self.quiet, 2 * TB, self.now - timedelta(minutes=5))
        self.sample(self.hot, 2 * TB + 500 * GB, self.now - timedelta(minutes=5))
        self.assertEqual(self.names(), ["hot", "quiet"])

    def test_when_nothing_moved_it_is_oldest_first(self):
        self.sample(self.quiet, 2 * TB, self.now - timedelta(days=1, hours=1))
        self.scan(self.quiet, 2 * TB, self.now - timedelta(days=1))
        self.sample(self.hot, 2 * TB, self.now - timedelta(days=6, hours=1))
        self.scan(self.hot, 2 * TB, self.now - timedelta(days=6))
        self.sample(self.quiet, 2 * TB, self.now - timedelta(minutes=5))
        self.sample(self.hot, 2 * TB + 3 * GB, self.now - timedelta(minutes=5))
        self.assertEqual(self.names(), ["hot", "quiet"])   # hot 이 더 오래됐다

    def test_usage_at_the_scan_is_kept_with_the_scan_state(self):
        self.sample(self.hot, 3 * TB, self.now - timedelta(hours=2))
        self.scan(self.hot, 3 * TB, self.now - timedelta(hours=1))
        with scan_store.session(self.data_dir) as conn:
            state = scan_store.get_account_state(conn, self.hot.account_id)
        self.assertEqual(state.used_kb_at_scan, 3 * TB)

    def test_scans_from_before_this_rule_get_their_usage_filled_in(self):
        """업데이트 전에 끝난 스캔에는 그때의 사용량이 없다 - 표본 이력에서 찾아 채운다."""

        self.sample(self.hot, 2 * TB, self.now - timedelta(days=2, hours=1))
        top = f"{self.hot.path}/top"
        with scan_store.session(self.data_dir) as conn:
            support.scan_pass(conn, self.hot.account_id, 1, [top], {top: support.whole({top: 2 * TB})})
            scan_store.mark_generation_completed(
                conn, self.hot.account_id, 1,
                completed_at=self.utc(self.now - timedelta(days=2)),
            )
        self.sample(self.hot, 2 * TB + 700 * GB, self.now - timedelta(minutes=5))
        self.assertEqual(nightly_scan.backfill_scan_usage(self.data_dir, self.config), 1)
        with scan_store.session(self.data_dir) as conn:
            state = scan_store.get_account_state(conn, self.hot.account_id)
        self.assertEqual(state.used_kb_at_scan, 2 * TB)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
