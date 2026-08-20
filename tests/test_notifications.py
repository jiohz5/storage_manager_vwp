import json
import subprocess
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import notifications, tiers
from smvwp.config import Account
from smvwp.store import SampleRecord
from tests import support


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
        result = notifications.maybe_notify(
            self.data_dir, self.account, _sample(tiers.ALERT), state, min_tier="warn", cooldown_minutes=60
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, config_module.NOTIFY_MODE_OUTBOX)
        self.assertTrue(Path(result.outbox_path).exists())
        self.assertIn("acct-1", state)
        # 감사 기록은 모드와 무관하게 항상 남아야 한다.
        self.assertEqual(len(list(notifications.audit_dir(self.data_dir).glob("*.json"))), 1)

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


class DeliveryModeTests(unittest.TestCase):
    """outbox 이외의 전송 채널. 실제 프로세스/네트워크는 쓰지 않는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        account = Account(name="project_a", path="/user/project_a", account_id="acct-1")
        self.event = notifications.build_event(
            account, _sample(tiers.ALERT), datetime.now(timezone.utc)
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _audit_entries(self):
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in notifications.audit_dir(self.data_dir).glob("*.json")
        ]

    @patch("smvwp.notifications.subprocess.run")
    def test_command_mode_passes_json_on_stdin_without_shell(self, mock_run):
        mock_run.return_value = support.completed(["send"])
        result = notifications.deliver(
            self.data_dir,
            self.event,
            mode=config_module.NOTIFY_MODE_COMMAND,
            command=["/opt/company/bin/send", "storage-alert"],
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.mode, config_module.NOTIFY_MODE_COMMAND)
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0], ["/opt/company/bin/send", "storage-alert"])
        # shell을 절대 쓰지 않는다 - 경로/계정명의 특수문자가 재해석되면 안 된다.
        self.assertNotIn("shell", kwargs)
        payload = json.loads(kwargs["input"])
        self.assertEqual(payload["account_name"], "project_a")
        self.assertEqual(payload["tier"], tiers.ALERT)
        # command 모드에서는 outbox 파일을 만들지 않는다.
        self.assertFalse(notifications.outbox_dir(self.data_dir).exists())

    @patch("smvwp.notifications.subprocess.run")
    def test_command_stdin_is_utf8_bytes_regardless_of_locale(self, mock_run):
        """cron의 LANG=C 환경에서도 한글 알림이 전송되어야 한다.

        `text=True`로 넘기면 파이썬이 로케일 인코딩으로 stdin을 인코딩해서,
        선호 인코딩이 ASCII인 cron에서는 한글 메시지가 UnicodeEncodeError로
        통째로 실패한다. 그래서 UTF-8 바이트로 넘기는지 명시적으로 못박는다.
        """

        mock_run.return_value = support.completed(["send"])
        notifications.deliver(
            self.data_dir,
            self.event,
            mode=config_module.NOTIFY_MODE_COMMAND,
            command=["/opt/company/bin/send"],
        )

        _args, kwargs = mock_run.call_args
        stdin_payload = kwargs["input"]
        self.assertIsInstance(stdin_payload, bytes)
        # 로케일에 맡기지 않았음을 확인 - text 모드를 켜면 안 된다.
        self.assertNotIn("text", kwargs)
        self.assertNotIn("encoding", kwargs)
        decoded = json.loads(stdin_payload.decode("utf-8"))
        self.assertIn(tiers.label(tiers.ALERT), decoded["message"])

    @patch("smvwp.notifications.subprocess.run", side_effect=FileNotFoundError())
    def test_command_failure_is_recorded_but_not_raised(self, _mock_run):
        result = notifications.deliver(
            self.data_dir,
            self.event,
            mode=config_module.NOTIFY_MODE_COMMAND,
            command=["/missing/binary"],
        )
        self.assertFalse(result.ok)
        # 실패해도 "무엇을 보내려 했는지"는 감사 기록에 남아야 한다.
        entries = self._audit_entries()
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0]["delivery"]["ok"])
        self.assertEqual(entries[0]["event"]["account_name"], "project_a")

    @patch("smvwp.notifications.urllib.request.urlopen")
    def test_webhook_mode_posts_utf8_json(self, mock_urlopen):
        response = mock_urlopen.return_value.__enter__.return_value
        response.status = 200
        result = notifications.deliver(
            self.data_dir,
            self.event,
            mode=config_module.NOTIFY_MODE_WEBHOOK,
            webhook_url="https://internal.example/storage",
        )

        self.assertTrue(result.ok)
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.get_method(), "POST")
        self.assertIn("charset=utf-8", request.headers["Content-type"])
        self.assertEqual(json.loads(request.data.decode("utf-8"))["account_name"], "project_a")

    @patch(
        "smvwp.notifications.urllib.request.urlopen",
        side_effect=urllib.error.URLError("연결 거부"),
    )
    def test_webhook_failure_is_recorded_but_not_raised(self, _mock_urlopen):
        result = notifications.deliver(
            self.data_dir,
            self.event,
            mode=config_module.NOTIFY_MODE_WEBHOOK,
            webhook_url="https://internal.example/storage",
        )
        self.assertFalse(result.ok)
        self.assertEqual(len(self._audit_entries()), 1)

    def test_disabled_mode_sends_nothing_but_still_audits(self):
        result = notifications.deliver(
            self.data_dir, self.event, mode=config_module.NOTIFY_MODE_DISABLED
        )
        self.assertTrue(result.ok)
        self.assertFalse(notifications.outbox_dir(self.data_dir).exists())
        self.assertEqual(len(self._audit_entries()), 1)

    def test_command_mode_without_command_falls_back_to_outbox(self):
        """설정이 덜 된 상태에서도 알림을 잃지 않아야 한다."""

        result = notifications.deliver(
            self.data_dir, self.event, mode=config_module.NOTIFY_MODE_COMMAND, command=[]
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, config_module.NOTIFY_MODE_OUTBOX)
        self.assertTrue(Path(result.outbox_path).exists())


if __name__ == "__main__":
    unittest.main()


class ImmediateNotifyTests(unittest.TestCase):
    """임계 사용률을 넘으면 cooldown을 무시하고 매 수집마다 알린다.

    15분 표본이 이미 99%를 넘었다면 "한 시간 뒤에 다시 알려 주기"는 늦다 -
    그 한 시간 안에 꽉 차서 쓰기가 실패한다. 조용한 것이 더 위험한 구간이다.
    """

    def _sample(self, account_id, pct, now):
        tier = tiers.classify(pct)
        return SampleRecord(
            account_id=account_id, collected_at=now.isoformat(), ok=True,
            byte_pct=pct, byte_tier=tier, overall_tier=tier,
            total_kb=1000, used_kb=int(10 * pct),
        )

    def test_below_threshold_is_still_suppressed_by_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            account = config_module.Account(name="a", path="/user/a")
            now = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
            state = {}

            first = notifications.maybe_notify(
                data_dir, account, self._sample(account.account_id, 98.0, now), state,
                cooldown_minutes=60, now=now, immediate_pct=99.0,
            )
            second = notifications.maybe_notify(
                data_dir, account, self._sample(account.account_id, 98.0, now), state,
                cooldown_minutes=60, now=now + timedelta(minutes=15), immediate_pct=99.0,
            )
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_at_or_above_threshold_notifies_every_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            account = config_module.Account(name="a", path="/user/a")
            now = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
            state = {}
            results = [
                notifications.maybe_notify(
                    data_dir, account,
                    self._sample(account.account_id, 99.5, now), state,
                    cooldown_minutes=60, now=now + timedelta(minutes=15 * i),
                    immediate_pct=99.0,
                )
                for i in range(3)
            ]
        self.assertTrue(all(r is not None for r in results))

    def test_event_is_marked_urgent_for_the_tray(self):
        account = config_module.Account(name="a", path="/user/a")
        now = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
        event = notifications.build_event(
            account, self._sample(account.account_id, 99.5, now), now, urgent=True
        )
        self.assertTrue(event.urgent)

    def test_no_threshold_configured_keeps_old_behaviour(self):
        """설정이 없으면(None) 예전처럼 cooldown이 그대로 걸려야 한다."""

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            account = config_module.Account(name="a", path="/user/a")
            now = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
            state = {}
            notifications.maybe_notify(
                data_dir, account, self._sample(account.account_id, 99.9, now), state,
                cooldown_minutes=60, now=now, immediate_pct=None,
            )
            second = notifications.maybe_notify(
                data_dir, account, self._sample(account.account_id, 99.9, now), state,
                cooldown_minutes=60, now=now + timedelta(minutes=15), immediate_pct=None,
            )
        self.assertIsNone(second)
