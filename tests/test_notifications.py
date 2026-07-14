import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from storage_manager.config import Settings
from storage_manager.notifications import (
    NotificationEvent,
    dispatch_notifications,
    read_notification_status,
)


class NotificationTests(unittest.TestCase):
    def test_outbox_dispatch_and_cooldown(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            settings = Settings(notification_mode="outbox", notification_cooldown_hours=12)
            event = NotificationEvent("disk:a", "alert", "Disk alert", "97% used")
            first = dispatch_notifications(
                data_dir,
                settings,
                [event],
                datetime(2026, 7, 12, 7, 0, 0),
            )
            second = dispatch_notifications(
                data_dir,
                settings,
                [event],
                datetime(2026, 7, 12, 8, 0, 0),
            )
            self.assertEqual(first.sent, 1)
            self.assertTrue(first.outbox_file.exists())
            payload = json.loads(first.outbox_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["events"][0]["message"], "97% used")
            self.assertEqual(second.sent, 0)
            self.assertEqual(second.suppressed, 1)
            status = read_notification_status(data_dir)
            self.assertEqual(status["suppressed"], 1)

    def test_alert_escalation_bypasses_warning_cooldown(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            settings = Settings(notification_mode="outbox", notification_cooldown_hours=24)
            now = datetime(2026, 7, 12, 7, 0, 0)
            dispatch_notifications(
                data_dir,
                settings,
                [NotificationEvent("disk:a", "warning", "Warn", "90%")],
                now,
            )
            result = dispatch_notifications(
                data_dir,
                settings,
                [NotificationEvent("disk:a", "alert", "Alert", "96%")],
                now + timedelta(hours=1),
            )
            self.assertEqual(result.sent, 1)

    def test_emergency_and_full_escalations_bypass_cooldown(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            settings = Settings(notification_mode="outbox", notification_cooldown_hours=24)
            now = datetime(2026, 7, 12, 7, 0, 0)
            levels = ["warning", "alert", "emergency", "full"]
            for offset, level in enumerate(levels):
                result = dispatch_notifications(
                    data_dir,
                    settings,
                    [NotificationEvent("disk:a", level, level.upper(), level)],
                    now + timedelta(minutes=offset),
                )
                self.assertEqual(result.sent, 1, level)

            repeated = dispatch_notifications(
                data_dir,
                settings,
                [NotificationEvent("disk:a", "full", "FULL", "full")],
                now + timedelta(minutes=5),
            )
            self.assertEqual(repeated.sent, 0)
            self.assertEqual(repeated.suppressed, 1)

    def test_recovery_clears_cooldown_for_a_new_warning_cycle(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            settings = Settings(notification_mode="outbox", notification_cooldown_hours=24)
            now = datetime(2026, 7, 12, 7, 0, 0)
            key = "capacity:fs-a"
            levels = ["warning", "full", "recovery", "warning"]
            for offset, level in enumerate(levels):
                result = dispatch_notifications(
                    data_dir,
                    settings,
                    [NotificationEvent(key, level, level.upper(), level)],
                    now + timedelta(minutes=offset),
                )
                self.assertEqual(result.sent, 1, level)

    def test_recovery_clears_state_even_when_minimum_level_is_alert(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            settings = Settings(
                notification_mode="outbox",
                notification_min_level="alert",
                notification_cooldown_hours=24,
            )
            now = datetime(2026, 7, 12, 7, 0, 0)
            key = "capacity:fs-a"
            for offset, level in enumerate(("alert", "full", "recovery", "alert")):
                result = dispatch_notifications(
                    data_dir,
                    settings,
                    [NotificationEvent(key, level, level.upper(), level)],
                    now + timedelta(minutes=offset),
                )
                self.assertEqual(result.sent, 1, level)

    @patch("storage_manager.notifications.subprocess.run")
    def test_command_adapter_uses_stdin_without_shell(self, run_mock):
        run_mock.return_value = subprocess.CompletedProcess(["sender"], 0, "", "")
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                notification_mode="command",
                notification_command=["/opt/company/send-message", "storage"],
            )
            result = dispatch_notifications(
                Path(temp),
                settings,
                [NotificationEvent("disk:a", "alert", "Alert", "96%")],
            )
        self.assertEqual(result.sent, 1)
        self.assertEqual(run_mock.call_args.args[0][0], "/opt/company/send-message")
        self.assertNotIn("shell", run_mock.call_args.kwargs)
        self.assertIn('"96%"', run_mock.call_args.kwargs["input"])

    @patch("storage_manager.notifications.request.urlopen")
    def test_webhook_adapter_posts_utf8_json(self, urlopen_mock):
        response = urlopen_mock.return_value.__enter__.return_value
        response.status = 204
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(
                notification_mode="webhook",
                notification_webhook_url="https://internal.example/storage",
            )
            result = dispatch_notifications(
                Path(temp),
                settings,
                [NotificationEvent("disk:a", "alert", "경고", "사용률 96%")],
            )
        self.assertEqual(result.sent, 1)
        web_request = urlopen_mock.call_args.args[0]
        self.assertEqual(web_request.get_method(), "POST")
        self.assertIn("사용률 96%", web_request.data.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
