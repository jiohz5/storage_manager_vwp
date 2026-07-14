import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from storage_manager.config import Settings
from storage_manager.notifications import NotificationEvent, dispatch_notifications
from storage_manager.popup_queue import (
    acknowledge_notifications,
    popup_summary,
    unread_notifications,
)


class PopupQueueTests(unittest.TestCase):
    def test_unread_outbox_survives_restart_until_acknowledged(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            now = datetime(2026, 7, 12, 10, 0, 0)
            result = dispatch_notifications(
                data_dir,
                Settings(notification_mode="outbox"),
                [NotificationEvent("capacity:fs", "full", "FULL", "100%")],
                now,
            )

            first_read = unread_notifications(data_dir, 7, now)
            second_read = unread_notifications(data_dir, 7, now)
            self.assertEqual([item.path for item in first_read], [result.outbox_file])
            self.assertEqual([item.path for item in second_read], [result.outbox_file])

            acknowledge_notifications(data_dir, [result.outbox_file], now)
            self.assertEqual(unread_notifications(data_dir, 7, now), [])

    def test_backlog_summary_uses_highest_severity_and_count(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            settings = Settings(notification_mode="outbox", notification_cooldown_hours=0)
            now = datetime(2026, 7, 12, 10, 0, 0)
            dispatch_notifications(
                data_dir,
                settings,
                [NotificationEvent("a", "warning", "WARN A", "90%")],
                now,
            )
            dispatch_notifications(
                data_dir,
                settings,
                [NotificationEvent("b", "full", "FULL B", "100%")],
                now + timedelta(minutes=1),
            )

            title, message = popup_summary(
                unread_notifications(data_dir, 7, now + timedelta(minutes=2)),
                "en",
            )

            self.assertIn("FULL", title)
            self.assertIn("2", message)
            self.assertIn("FULL B", message)

    def test_old_and_malformed_outbox_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            directory = data_dir / "notifications"
            directory.mkdir(parents=True)
            (directory / "broken.json").write_text("{", encoding="utf-8")
            (directory / "old.json").write_text(
                json.dumps(
                    {
                        "generated_at": "2026-06-01 10:00:00",
                        "events": [
                            {
                                "key": "old",
                                "level": "alert",
                                "title": "old",
                                "message": "old",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            unread = unread_notifications(
                data_dir,
                7,
                datetime(2026, 7, 12, 10, 0, 0),
            )

            self.assertEqual(unread, [])


if __name__ == "__main__":
    unittest.main()
