import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smvwp import notifications, tiers
from smvwp.config import Account
from smvwp.store import SampleRecord


def _sample(overall_tier, ok=True):
    return SampleRecord(
        account_id="acct-1",
        collected_at=datetime.now(timezone.utc).isoformat(),
        ok=ok,
        byte_pct=96.0,
        inode_pct=5.0,
        byte_tier=overall_tier,
        inode_tier=tiers.NORMAL,
        overall_tier=overall_tier,
    )


class MaybeNotifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.account = Account(name="project_a", path="/user/project_a", account_id="acct-1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_event_when_at_or_above_min_tier(self):
        state = {}
        path = notifications.maybe_notify(
            self.data_dir, self.account, _sample(tiers.ALERT), state, min_tier="warn", cooldown_minutes=60
        )
        self.assertIsNotNone(path)
        self.assertTrue(path.exists())
        self.assertIn("acct-1", state)

    def test_below_min_tier_does_not_notify(self):
        state = {}
        path = notifications.maybe_notify(
            self.data_dir, self.account, _sample(tiers.NORMAL), state, min_tier="warn", cooldown_minutes=60
        )
        self.assertIsNone(path)
        self.assertEqual(state, {})

    def test_cooldown_suppresses_repeat_same_tier(self):
        state = {}
        now = datetime.now(timezone.utc)
        first = notifications.maybe_notify(
            self.data_dir, self.account, _sample(tiers.ALERT), state,
            min_tier="warn", cooldown_minutes=60, now=now,
        )
        second = notifications.maybe_notify(
            self.data_dir, self.account, _sample(tiers.ALERT), state,
            min_tier="warn", cooldown_minutes=60, now=now + timedelta(minutes=5),
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_severity_increase_bypasses_cooldown(self):
        state = {}
        now = datetime.now(timezone.utc)
        notifications.maybe_notify(
            self.data_dir, self.account, _sample(tiers.WARN), state,
            min_tier="warn", cooldown_minutes=60, now=now,
        )
        escalated = notifications.maybe_notify(
            self.data_dir, self.account, _sample(tiers.EMERGENCY), state,
            min_tier="warn", cooldown_minutes=60, now=now + timedelta(minutes=1),
        )
        self.assertIsNotNone(escalated)

    def test_returning_to_normal_resets_state(self):
        state = {}
        now = datetime.now(timezone.utc)
        notifications.maybe_notify(
            self.data_dir, self.account, _sample(tiers.ALERT), state,
            min_tier="warn", cooldown_minutes=60, now=now,
        )
        self.assertIn("acct-1", state)
        notifications.maybe_notify(
            self.data_dir, self.account, _sample(tiers.NORMAL), state,
            min_tier="warn", cooldown_minutes=60, now=now + timedelta(minutes=1),
        )
        self.assertNotIn("acct-1", state)

        # 다시 나빠지면 cooldown 없이 즉시 재알림되어야 한다 (리셋됐으므로).
        renotified = notifications.maybe_notify(
            self.data_dir, self.account, _sample(tiers.ALERT), state,
            min_tier="warn", cooldown_minutes=60, now=now + timedelta(minutes=2),
        )
        self.assertIsNotNone(renotified)

    def test_failed_collection_does_not_notify(self):
        state = {}
        path = notifications.maybe_notify(
            self.data_dir, self.account, _sample(tiers.ALERT, ok=False), state,
            min_tier="warn", cooldown_minutes=60,
        )
        self.assertIsNone(path)


if __name__ == "__main__":
    unittest.main()
