import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import store, tiers
from smvwp.cycle import run_collection_cycle


class RunCollectionCycleTests(unittest.TestCase):
    @patch("smvwp.cycle.collector.collect_all")
    def test_stores_samples_and_writes_notification_for_bad_tier(self, mock_collect_all):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            account_path = Path(tmp) / "acct"
            account_path.mkdir()

            config = config_module.load_config(data_dir)
            account = config_module.add_account(config, "project_a", str(account_path))
            config_module.save_config(data_dir, config)

            sample = store.SampleRecord(
                account_id=account.account_id,
                collected_at=datetime.now(timezone.utc).isoformat(),
                ok=True,
                byte_pct=96.0,
                inode_pct=1.0,
                byte_tier=tiers.ALERT,
                inode_tier=tiers.NORMAL,
                overall_tier=tiers.ALERT,
            )
            mock_collect_all.return_value = [sample]

            records = run_collection_cycle(data_dir, config)

            self.assertEqual(len(records), 1)

            conn = store.connect(data_dir)
            try:
                latest = store.latest_samples(conn)
            finally:
                conn.close()
            self.assertIn(account.account_id, latest)

            outbox_files = list((data_dir / "outbox").glob("*.json"))
            self.assertEqual(len(outbox_files), 1)

    @patch("smvwp.cycle.collector.collect_all")
    def test_one_collection_is_stored_in_one_go(self, mock_collect_all):
        """계정마다 따로 커밋하지 않는다 - NFS 에서 커밋 하나가 저널 파일 왕복이라
        계정 스물이면 15분마다 스무 번 쓰기 잠금을 쥐었다 놓았다 했다."""

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            config = config_module.load_config(data_dir)
            ids = []
            for name in ("a", "b", "c"):
                (Path(tmp) / name).mkdir()
                ids.append(config_module.add_account(config, name, str(Path(tmp) / name)).account_id)
            now = datetime.now(timezone.utc).isoformat()
            mock_collect_all.return_value = [
                store.SampleRecord(account_id=account_id, collected_at=now, ok=True)
                for account_id in ids
            ]
            with patch("smvwp.cycle.store.insert_sample",
                       side_effect=AssertionError("줄마다 커밋했다")), \
                    patch("smvwp.cycle.store.insert_samples",
                          wraps=store.insert_samples) as insert:
                run_collection_cycle(data_dir, config)
            self.assertEqual(insert.call_count, 1)
            with store.session(data_dir) as conn:
                self.assertEqual(set(store.latest_samples(conn)), set(ids))

    @patch("smvwp.cycle.collector.collect_all")
    def test_only_enabled_accounts_are_collected(self, mock_collect_all):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            enabled_path = Path(tmp) / "enabled"
            enabled_path.mkdir()
            disabled_path = Path(tmp) / "disabled"
            disabled_path.mkdir()

            config = config_module.load_config(data_dir)
            config_module.add_account(config, "enabled_acct", str(enabled_path))
            disabled_account = config_module.add_account(config, "disabled_acct", str(disabled_path))
            disabled_account.enabled = False
            config_module.save_config(data_dir, config)

            mock_collect_all.return_value = []
            run_collection_cycle(data_dir, config)

            called_accounts = mock_collect_all.call_args[0][0]
            self.assertEqual(len(called_accounts), 1)
            self.assertEqual(called_accounts[0].name, "enabled_acct")

    @patch("smvwp.cycle.collector.collect_all")
    def test_old_notification_files_are_cleaned_up(self, mock_collect_all):
        """치우는 함수는 있었는데 부르는 곳이 없어 outbox 가 끝없이 자랐다.

        임계값을 넘은 계정은 15분마다 파일이 하나씩 생긴다. 한 달이면 계정당
        3천 개 가까이 되고, 팝업을 확인할 때마다 그 전부를 NFS 위에서 읽는다."""

        from datetime import timedelta

        from smvwp import notifications, popup_queue
        from smvwp.config import Account

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            config = config_module.load_config(data_dir)
            config_module.save_config(data_dir, config)

            now = datetime.now(timezone.utc)
            for name, age_days in (("old", popup_queue.EVENT_RETENTION_DAYS + 5), ("new", 1)):
                moment = now - timedelta(days=age_days)
                account = Account(name=name, path="/u/" + name, account_id=name)
                sample = store.SampleRecord(
                    account_id=name, collected_at=moment.isoformat(), ok=True,
                    byte_pct=97.0, overall_tier=tiers.ALERT,
                )
                notifications.write_event(
                    data_dir, notifications.build_event(account, sample, moment)
                )

            mock_collect_all.return_value = []
            run_collection_cycle(data_dir, config)

            left = list(notifications.outbox_dir(data_dir).glob("*.json"))
            self.assertEqual(len(left), 1)
            self.assertIn("new", left[0].read_text(encoding="utf-8"))


class OneHomeCalculationTests(unittest.TestCase):
    """예측 알림과 홈 결과 파일이 홈 계산 하나를 나눠 쓴다.

    예전에는 예측 알림이 계정마다 30일 표본을 읽고, 바로 뒤에 홈 결과 파일을 만들면서
    같은 것을 또 읽었다. cron 수집은 매번 새 프로세스라 15분마다 가장 무거운 읽기가
    두 번이었다 (계정 100개·90일: 79MB -> 42MB)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        for name in ("a", "b"):
            (root / name).mkdir()
            config_module.add_account(self.config, name, str(root / name))
        self.config.accounts[1].enabled = False
        config_module.save_config(self.data_dir, self.config)

    def run_cycle(self):
        with patch("smvwp.cycle.collector.collect_all", return_value=[]):
            run_collection_cycle(self.data_dir, self.config)

    def test_the_history_is_read_once(self):
        from smvwp import dashboard, forecast_notify, home_view

        with patch("smvwp.dashboard._read_history", wraps=dashboard._read_history) as history, \
                patch("smvwp.forecast_notify.build_forecasts",
                      wraps=forecast_notify.build_forecasts) as build, \
                patch("smvwp.cycle.forecast_notify.notify_forecasts",
                      wraps=forecast_notify.notify_forecasts) as notify:
            self.run_cycle()
        self.assertEqual(history.call_count, 1)
        # 예측은 한 번, 그 이력을 받아서 낸다 (스스로 DB 를 읽는 길로 가지 않는다).
        self.assertEqual(build.call_count, 1)
        self.assertIsNotNone(build.call_args.kwargs.get("history"))
        # 알림은 켜진 계정의 예측만 받는다 (예전과 같다).
        forecasts = notify.call_args[0][2]
        self.assertEqual([item.account_id for item in forecasts],
                         [self.config.accounts[0].account_id])
        self.assertIsNone(home_view.problem(self.data_dir, self.config))

    def test_alerts_still_go_out_when_the_home_cannot_be_calculated(self):
        from smvwp import forecast_notify

        with patch("smvwp.dashboard.read_dashboard", side_effect=RuntimeError("x")), \
                patch("smvwp.cycle.forecast_notify.build_forecasts",
                      wraps=forecast_notify.build_forecasts) as build, \
                patch("smvwp.cycle.forecast_notify.notify_forecasts", return_value=0) as notify:
            self.run_cycle()
        self.assertEqual(build.call_count, 1)
        self.assertEqual(notify.call_count, 1)


class CycleTimingTests(unittest.TestCase):
    """한 바퀴가 어디에 시간을 썼나를 남긴다 - `smvwp_cli.py status` 가 읽는다.

    "수집이 DB 를 여러 번 연다" 를 줄이려다 세어 보니 평소 7~8번이었다. 고칠 값어치가
    있는지는 실기에서 어느 단계가 느린지를 봐야 알아서, 재는 것부터 두었다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"
        self.config = config_module.load_config(self.data_dir)

    def test_a_cycle_leaves_its_timing(self):
        from smvwp import cycle

        self.assertIsNone(cycle.read_timing(self.data_dir))
        with patch("smvwp.cycle.collector.collect_all", return_value=[]):
            run_collection_cycle(self.data_dir, self.config)
        timing = cycle.read_timing(self.data_dir)
        self.assertIsNotNone(timing)
        for stage in ("df", "save", "notify", "forecast", "reports", "home"):
            self.assertIn(stage, timing["seconds"])
        self.assertLessEqual(set(timing["seconds"]), set(cycle.STAGES))
        self.assertGreaterEqual(timing["total"] + 0.01, sum(timing["seconds"].values()))

    def test_a_failed_stage_is_still_timed_and_the_cycle_goes_on(self):
        from smvwp import cycle

        with patch("smvwp.cycle.collector.collect_all", return_value=[]), \
                patch("smvwp.cycle.reports.ensure_scheduled", side_effect=RuntimeError("x")):
            run_collection_cycle(self.data_dir, self.config)
        timing = cycle.read_timing(self.data_dir)
        self.assertIn("reports", timing["seconds"])
        self.assertIn("home", timing["seconds"])

    def test_a_broken_file_reads_as_nothing(self):
        from smvwp import cycle

        path = cycle.timing_path(self.data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        for text in ("", "{", "[]", '{"total": "x", "seconds": {}, "finished_at": ""}',
                     '{"total": 1, "seconds": 3, "finished_at": ""}'):
            path.write_text(text, encoding="utf-8")
            self.assertIsNone(cycle.read_timing(self.data_dir), text)


if __name__ == "__main__":
    unittest.main()
