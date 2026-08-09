import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smvwp import notifications, notifier, popup_queue, tiers
from smvwp.config import Account
from smvwp.store import SampleRecord


def _event(data_dir, account_name, tier, generated_at):
    account = Account(name=account_name, path=f"/user/{account_name}", account_id=account_name)
    sample = SampleRecord(
        account_id=account_name,
        collected_at=generated_at.isoformat(),
        ok=True,
        byte_pct=97.0,
        overall_tier=tier,
    )
    event = notifications.build_event(account, sample, generated_at)
    notifications.write_event(data_dir, event)
    return event


class PopupQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_when_no_outbox(self):
        self.assertEqual(popup_queue.list_pending(self.data_dir), [])
        self.assertEqual(popup_queue.unread_count(self.data_dir), 0)

    def test_lists_unread_events_oldest_first(self):
        _event(self.data_dir, "b", tiers.ALERT, self.now - timedelta(minutes=1))
        _event(self.data_dir, "a", tiers.WARN, self.now - timedelta(minutes=5))

        pending = popup_queue.list_pending(self.data_dir)
        self.assertEqual([item.account_name for item in pending], ["a", "b"])

    def test_marking_read_removes_from_pending(self):
        event = _event(self.data_dir, "a", tiers.ALERT, self.now)
        self.assertEqual(popup_queue.unread_count(self.data_dir), 1)

        popup_queue.mark_read(self.data_dir, [event.event_id])
        self.assertEqual(popup_queue.unread_count(self.data_dir), 0)

    def test_read_state_survives_restart(self):
        """읽음 상태는 파일에 남아야 재실행 시 다시 뜨지 않는다."""

        event = _event(self.data_dir, "a", tiers.ALERT, self.now)
        popup_queue.mark_read(self.data_dir, [event.event_id])

        # 새 프로세스인 것처럼 다시 조회.
        self.assertEqual(popup_queue.unread_count(self.data_dir), 0)

    def test_mark_all_read(self):
        _event(self.data_dir, "a", tiers.ALERT, self.now)
        _event(self.data_dir, "b", tiers.WARN, self.now)

        marked = popup_queue.mark_all_read(self.data_dir)
        self.assertEqual(marked, 2)
        self.assertEqual(popup_queue.unread_count(self.data_dir), 0)

    def test_events_older_than_max_age_are_not_shown(self):
        """로그아웃이 길었어도 몇 주치가 한꺼번에 뜨지는 않아야 한다."""

        _event(self.data_dir, "old", tiers.ALERT, self.now - timedelta(days=30))
        _event(self.data_dir, "recent", tiers.ALERT, self.now - timedelta(days=1))

        pending = popup_queue.list_pending(self.data_dir, max_age_days=7)
        self.assertEqual([item.account_name for item in pending], ["recent"])

    def test_prune_removes_old_files_only(self):
        _event(self.data_dir, "old", tiers.ALERT, self.now - timedelta(days=400))
        _event(self.data_dir, "recent", tiers.ALERT, self.now)

        removed = popup_queue.prune_old_events(self.data_dir, retention_days=365)
        self.assertEqual(removed, 1)
        remaining = list(notifications.outbox_dir(self.data_dir).glob("*.json"))
        self.assertEqual(len(remaining), 1)

    def test_prune_trims_read_state_of_deleted_events(self):
        """읽음 기록이 무한히 자라지 않아야 한다."""

        old_event = _event(self.data_dir, "old", tiers.ALERT, self.now - timedelta(days=400))
        popup_queue.mark_read(self.data_dir, [old_event.event_id])

        popup_queue.prune_old_events(self.data_dir, retention_days=365)
        state = popup_queue.read_state_file(self.data_dir).read_text(encoding="utf-8")
        self.assertNotIn(old_event.event_id, state)

    def test_corrupt_outbox_file_is_skipped(self):
        _event(self.data_dir, "good", tiers.ALERT, self.now)
        (notifications.outbox_dir(self.data_dir) / "broken.json").write_text("{not json", encoding="utf-8")

        pending = popup_queue.list_pending(self.data_dir)
        self.assertEqual([item.account_name for item in pending], ["good"])


class AutostartTests(unittest.TestCase):
    def test_desktop_entry_uses_absolute_paths(self):
        """로그인 시점에는 PATH나 작업 디렉터리를 신뢰할 수 없다."""

        entry = notifier.build_autostart_entry(
            "/installed/python/3.12/bin/python3", Path("/opt/smvwp"), Path("/data/sm")
        )
        self.assertIn("[Desktop Entry]", entry)
        self.assertIn("/installed/python/3.12/bin/python3", entry)
        self.assertIn("smvwp_cli.py", entry)
        self.assertIn("notify", entry)
        self.assertIn("/data/sm", entry)
        self.assertIn("Type=Application", entry)

    def test_paths_with_spaces_are_quoted(self):
        entry = notifier.build_autostart_entry(
            "/usr/bin/python3", Path("/opt/storage manager"), Path("/data/my data")
        )
        exec_line = next(line for line in entry.splitlines() if line.startswith("Exec="))
        self.assertIn('"/opt/storage manager', exec_line)
        self.assertIn('"/data/my data"', exec_line)


if __name__ == "__main__":
    unittest.main()
