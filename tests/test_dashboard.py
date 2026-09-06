"""대시보드 데이터 읽기.

이 로직이 화면 안에 있었을 때는 시험할 수 없었다 - 개발 PC 에 PyQt5 가 없어
`smvwp.gui` 는 임포트조차 안 되기 때문이다. Qt 밖으로 뺀 이유의 절반이 그것이고,
나머지 절반은 이 읽기가 **화면 스레드에서 돌면 안 된다**는 것이다 (FULL 예측은
계정마다 이력을 다시 훑는다).
"""

import tempfile
import unittest
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
