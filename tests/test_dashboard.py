"""대시보드 데이터 읽기.

이 로직이 화면 안에 있었을 때는 시험할 수 없었다 - 개발 PC 에 PyQt5 가 없어
`smvwp.gui` 는 임포트조차 안 되기 때문이다. Qt 밖으로 뺀 이유의 절반이 그것이고,
나머지 절반은 이 읽기가 **화면 스레드에서 돌면 안 된다**는 것이다 (FULL 예측은
계정마다 이력을 다시 훑는다).
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import dashboard


class ReadDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.account_path = self.root / "acct"
        self.account_path.mkdir()

        self.config = config_module.load_config(self.data_dir)
        self.account = config_module.add_account(
            self.config, "project_a", str(self.account_path), data_dir=self.data_dir
        )
        config_module.save_config(self.data_dir, self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_store_is_not_an_error(self):
        """아직 아무것도 수집 안 된 상태에서도 화면은 떠야 한다."""

        data = dashboard.read_dashboard(self.data_dir, self.config)
        self.assertEqual(data.samples, {})
        # 표본이 없어도 계정마다 예측 항목 자체는 나온다 (판정만 "모름"이다).
        # 실패와 섞이지 않는 것이 여기서 볼 것이다.
        self.assertFalse(data.forecast_failed)
        self.assertIn(self.account.account_id, data.forecasts)

    def test_a_broken_forecast_still_returns_the_samples(self):
        """예측 하나 때문에 용량 숫자까지 못 보게 되면 안 된다.

        그쪽이 훨씬 나쁜 실패다 - 사용자는 "용량이 얼마인가"를 보러 온다."""

        with patch.object(
            dashboard.forecast_notify, "build_forecasts", side_effect=RuntimeError("boom")
        ):
            data = dashboard.read_dashboard(self.data_dir, self.config)
        self.assertTrue(data.forecast_failed)
        self.assertEqual(data.forecasts, {})
        self.assertIsInstance(data.samples, dict)

    def test_missing_and_failed_forecasts_are_different(self):
        """표본이 모자라 못 내는 것과 계산이 터진 것은 다르다."""

        healthy = dashboard.read_dashboard(self.data_dir, self.config)
        self.assertFalse(healthy.forecast_failed)

        with patch.object(
            dashboard.forecast_notify, "build_forecasts", side_effect=ValueError("x")
        ):
            broken = dashboard.read_dashboard(self.data_dir, self.config)
        self.assertTrue(broken.forecast_failed)
        # 터진 쪽은 비어 있고, 정상 쪽은 항목이 있다 - 화면이 둘을 구분할 수
        # 있어야 "예측 준비 중"과 "예측 고장"을 다르게 말할 수 있다.
        self.assertEqual(broken.forecasts, {})
        self.assertTrue(healthy.forecasts)

    def test_forecasts_are_keyed_by_account_id(self):
        """화면이 계정 행마다 바로 찾아 쓰므로 키가 맞아야 한다."""

        class FakeForecast:
            def __init__(self, account_id):
                self.account_id = account_id

        with patch.object(
            dashboard.forecast_notify,
            "build_forecasts",
            return_value=[FakeForecast(self.account.account_id)],
        ):
            data = dashboard.read_dashboard(self.data_dir, self.config)
        self.assertIn(self.account.account_id, data.forecasts)

    def test_the_connection_is_closed_even_when_reading_fails(self):
        """연결이 새면 NFS 위에서 잠금과 파일 핸들이 쌓인다."""

        closed = []

        real_connect = dashboard.store.connect

        class Spy:
            """`sqlite3.Connection.close` 는 재정의할 수 없어 감싼다."""

            def __init__(self, conn):
                self._conn = conn

            def __getattr__(self, name):
                return getattr(self._conn, name)

            def close(self):
                closed.append(True)
                self._conn.close()

        def spy_connect(data_dir):
            return Spy(real_connect(data_dir))

        with patch.object(dashboard.store, "connect", side_effect=spy_connect):
            with patch.object(
                dashboard.store, "latest_samples", side_effect=RuntimeError("db down")
            ):
                with self.assertRaises(RuntimeError):
                    dashboard.read_dashboard(self.data_dir, self.config)
        self.assertTrue(closed, "실패해도 연결은 닫아야 한다")


class ProgressAndSharedReadTests(unittest.TestCase):
    """실기(NFS)에서 창은 바로 떴지만 홈 데이터가 보이기까지 15초쯤 걸렸고, 그동안
    아무 표시가 없었다. 단계를 알려 기다릴 수 있게 하고, 같은 표본을 두 번 읽지
    않는다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        for name in ("a", "b"):
            (root / name).mkdir()
            config_module.add_account(self.config, name, str(root / name), data_dir=self.data_dir)

    def test_each_step_is_announced_in_order(self):
        steps = []
        dashboard.read_dashboard(
            self.data_dir, self.config, on_step=lambda i, n, key: steps.append((i, n, key))
        )
        self.assertEqual(steps, [(i + 1, len(dashboard.STEPS), key)
                                 for i, key in enumerate(dashboard.STEPS)])

    def test_each_accounts_history_is_read_once(self):
        """추세와 FULL 예측이 계정마다 같은 30일치를 따로 읽었다."""

        from smvwp import store

        with patch("smvwp.store.samples_since", wraps=store.samples_since) as reads:
            dashboard.read_dashboard(self.data_dir, self.config)
        self.assertEqual(reads.call_count, len(self.config.accounts))

    def test_a_slow_read_leaves_the_time_of_each_step(self):
        """어디를 줄일지 실기의 숫자로 정한다."""

        with patch.object(dashboard, "SLOW_READ_SECONDS", 0.0):
            with self.assertLogs("smvwp.dashboard", level="WARNING") as logs:
                dashboard.read_dashboard(self.data_dir, self.config)
        self.assertIn("초", logs.output[0])


class RememberedScanPartsTests(unittest.TestCase):
    """홈은 15분마다 다시 읽지만 스캔은 하루 한 번 끝난다. `cache` 를 넘기면 스캔이
    남긴 것(헬스체크, 상위 경로·증감)은 세대가 바뀔 때만 다시 읽는다.

    실측(계정 20개): 홈 한 번에 파일에서 733MB 를 읽었고 그 가운데 500MB 가 이
    둘이었다. 데이터 디렉터리가 NFS 면 그만큼이 15분마다 네트워크 읽기였다."""

    def setUp(self):
        from smvwp import scan_store

        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data_dir = root / "data"
        (root / "proj").mkdir()
        self.config = config_module.load_config(self.data_dir)
        account = config_module.add_account(
            self.config, "proj", str(root / "proj"), data_dir=self.data_dir,
            kind=config_module.ACCOUNT_KIND_PROJECT,
        )
        with scan_store.session(self.data_dir) as conn:
            for generation in (1, 2):
                scan_store.save_tree_entries(
                    conn, account.account_id, generation,
                    [("/proj/t", 100 * generation), ("/proj/t/01_run_0908", 90)], "/proj/t",
                )
                scan_store.mark_generation_completed(conn, account.account_id, generation)

    def scan_reads(self, cache):
        from smvwp import nightly_scan, scan_store

        with patch("smvwp.scan_store.tree_entries", wraps=scan_store.tree_entries) as trees,                 patch("smvwp.nightly_scan._settled_parts", wraps=nightly_scan._settled_parts) as parts:
            dashboard.read_dashboard(self.data_dir, self.config, cache=cache)
        return trees.call_count + parts.call_count

    def test_a_second_read_does_not_read_the_scan_results_again(self):
        cache = {}
        self.assertGreater(self.scan_reads(cache), 0)
        self.assertEqual(self.scan_reads(cache), 0)

    def test_without_a_cache_it_reads_every_time(self):
        self.assertGreater(self.scan_reads(None), 0)
        self.assertGreater(self.scan_reads(None), 0)


class HistoryKeptBetweenReadsTests(unittest.TestCase):
    """홈은 15분마다 30일 이력을 처음부터 다시 읽었다 (계정 20개면 18MB). 그 사이 새로
    들어온 표본은 계정 수만큼뿐이다 - 이력을 들고 있다가 새 줄만 읽어 붙인다."""

    T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def setUp(self):
        from smvwp import store

        self.store = store
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        for name in ("a", "b"):
            (root / name).mkdir()
            config_module.add_account(self.config, name, str(root / name), data_dir=self.data_dir)
        # 창(30일) 끝자락부터 T0 까지 6시간 간격
        self.insert(self.T0 - timedelta(days=31), self.T0)

    def insert(self, start, end, step=timedelta(hours=6)):
        records, at = [], start
        while at <= end:
            for account in self.config.accounts:
                records.append(self.store.SampleRecord(
                    account_id=account.account_id, collected_at=at.isoformat(), ok=True,
                    used_kb=int(at.timestamp()) % 100000, avail_kb=1, filesystem="fs:/x",
                ))
            at += step
        with self.store.session(self.data_dir) as conn:
            self.store.insert_samples(conn, records)

    def history(self, remembered, now, config=None):
        with self.store.session(self.data_dir) as conn:
            found = dashboard._read_history(conn, config or self.config, remembered, now=now)
        return {key: [(r.collected_at, r.used_kb) for r in rows] for key, rows in found.items()}

    def reads_everything(self, remembered, now, config=None):
        with patch("smvwp.store.samples_since", wraps=self.store.samples_since) as full:
            self.history(remembered, now, config)
        return full.call_count > 0

    def test_only_new_rows_are_read_and_the_answer_is_the_same(self):
        remembered = {}
        self.history(remembered, self.T0)
        later = self.T0 + timedelta(days=2)          # 앞쪽 이틀치가 창 밖으로 밀려난다
        self.insert(self.T0 + timedelta(hours=6), later)
        with patch("smvwp.store.samples_since", side_effect=AssertionError("처음부터 읽었다")):
            kept = self.history(remembered, later)
        self.assertEqual(kept, self.history(None, later))

    def test_the_home_reads_only_new_rows_the_second_time(self):
        cache = {}
        dashboard.read_dashboard(self.data_dir, self.config, cache=cache)
        with patch("smvwp.store.samples_since", wraps=self.store.samples_since) as full:
            dashboard.read_dashboard(self.data_dir, self.config, cache=cache)
        self.assertEqual(full.call_count, 0)

    def test_a_recreated_database_is_read_from_scratch(self):
        """DB 파일을 지우고 다시 만들면 id 가 처음부터 다시 붙는다."""

        remembered = {}
        self.history(remembered, self.T0)
        remembered["last_id"] += 10**6
        self.assertTrue(self.reads_everything(remembered, self.T0))

    def test_changed_accounts_are_read_from_scratch(self):
        from dataclasses import replace

        remembered = {}
        self.history(remembered, self.T0)
        fewer = replace(self.config, accounts=self.config.accounts[:1])
        self.assertTrue(self.reads_everything(remembered, self.T0, config=fewer))

    def test_a_retention_shorter_than_the_window_is_always_read_from_scratch(self):
        """정리가 창 안의 줄을 지우면 들고 있는 이력과 DB 가 어긋난다."""

        self.config.settings.sample_retention_days = 7
        remembered = {}
        self.history(remembered, self.T0)
        self.assertTrue(self.reads_everything(remembered, self.T0))


class NewestShownTests(unittest.TestCase):
    """홈이 보여 주는 가장 최근 표본 - 남이 방금 수집했을 때 다시 읽을지 가른다."""

    def test_the_read_says_how_new_its_samples_are(self):
        from smvwp import store

        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        data_dir = root / "data"
        config = config_module.load_config(data_dir)
        stamps = ["2026-10-16T02:45:00+00:00", "2026-10-16T03:00:00+00:00"]
        for name, stamp in zip(("a", "b"), stamps):
            (root / name).mkdir()
            account = config_module.add_account(config, name, str(root / name), data_dir=data_dir)
            with store.session(data_dir) as conn:
                store.insert_samples(conn, [store.SampleRecord(
                    account_id=account.account_id, collected_at=stamp, ok=True)])
        self.assertEqual(dashboard.read_dashboard(data_dir, config).newest, stamps[1])

    def test_already_shows(self):
        shown = "2026-10-16T03:00:00+00:00"
        self.assertTrue(dashboard.already_shows(shown, "2026-10-16T12:00:00+09:00"))
        self.assertFalse(dashboard.already_shows(shown, "2026-10-16T03:15:00+00:00"))
        self.assertFalse(dashboard.already_shows(None, shown))
        self.assertFalse(dashboard.already_shows(shown, None))


class SharedHistoryForecastTests(unittest.TestCase):
    def test_same_forecast_whether_it_reads_or_is_given_the_history(self):
        from smvwp import forecast_notify, store

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            config = config_module.load_config(data_dir)
            (root / "a").mkdir()
            account = config_module.add_account(config, "a", str(root / "a"), data_dir=data_dir)
            now = datetime.now(timezone.utc)
            with store.session(data_dir) as conn:
                for step in range(200):
                    at = now - timedelta(minutes=15 * step)
                    used = 900_000 - step * 100
                    store.insert_sample(conn, store.SampleRecord(
                        account_id=account.account_id, collected_at=at.isoformat(), ok=True,
                        total_kb=1_000_000, used_kb=used, avail_kb=1_000_000 - used,
                        byte_pct=used / 10_000, byte_tier="normal", overall_tier="normal",
                    ))
                conn.commit()
                history = {account.account_id: store.samples_since(
                    conn, account.account_id, now - timedelta(days=60))}
            alone = forecast_notify.build_forecasts(data_dir, config, now=now)
            shared = forecast_notify.build_forecasts(data_dir, config, now=now, history=history)
        self.assertEqual(alone, shared)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
