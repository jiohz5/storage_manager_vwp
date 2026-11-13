"""홈 화면용 결과 파일 - 창이 DB 를 열지 않고 홈을 그린다 (재설계 검토 순서 4 첫 단계).

## 왜 이것이 생겼나

창을 열면 장비마다 NFS 위 SQLite 를 열고 30일 이력을 읽어 추세와 FULL 예측을 계산했다.
대응책을 스무 가지 넘게 쌓았지만 첫 홈은 여전히 몇 초였고, 수집·스캔이 쓰는 DB 와 잠금을
다투어 실기에서 "database is locked" 가 났다. 보기만 하는 사람도 SQLite 때문에 데이터
폴더 쓰기 권한이 필요했다.

이제 수집(15분, cron 이든 창이든)이 끝날 때 홈 한 장을 JSON 파일로 써 두고, 창은 그것만
읽는다 (`home_view`). 파일이 없거나 낡았거나 다른 판·다른 계정 설정의 것이면 예전처럼 DB
에서 읽는다 - cron 이 없는 환경과 업데이트 직후가 그렇다.

pickle 이 아니라 JSON 인 까닭: 데이터 폴더는 여럿이 같이 쓴다. pickle 을 읽으면 그 폴더에
쓸 수 있는 누구든 모두의 창에서 코드를 돌릴 수 있다. 읽어 들일 수 있는 모양은 정해 둔
몇 가지(`home_view._TYPES`)뿐이다.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import dashboard, home_view, store, tiers


def _sample(account_id, pct, at):
    total = 10 ** 10
    return store.SampleRecord(
        account_id=account_id, collected_at=at.isoformat(), ok=True,
        filesystem="nfs4", mount_point="/ifs", total_kb=total,
        used_kb=int(total * pct / 100), avail_kb=total - int(total * pct / 100),
        byte_pct=pct, byte_tier=tiers.classify(pct), overall_tier=tiers.classify(pct),
    )


class HomeViewCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        for name in ("proj", "bak"):
            (root / name).mkdir()
            config_module.add_account(self.config, name, str(root / name), data_dir=self.data_dir)
        config_module.save_config(self.data_dir, self.config)
        now = datetime.now(timezone.utc)
        rows = []
        for hours in range(48, -1, -1):
            for index, account in enumerate(self.config.accounts):
                rows.append(_sample(account.account_id, 80.0 + index * 10 - hours * 0.05,
                                    now - timedelta(hours=hours)))
        with store.session(self.data_dir) as conn:
            store.insert_samples(conn, rows)

    def write(self):
        return home_view.write(self.data_dir, self.config)


class RoundTripTests(HomeViewCase):
    def test_what_the_window_reads_is_what_the_db_says(self):
        direct = dashboard.read_dashboard(self.data_dir, self.config)
        self.write()
        loaded = home_view.read(self.data_dir, self.config)
        self.assertIsNotNone(loaded)
        self.assertEqual(
            {k: (v.byte_pct, v.overall_tier, v.collected_at) for k, v in loaded.samples.items()},
            {k: (v.byte_pct, v.overall_tier, v.collected_at) for k, v in direct.samples.items()},
        )
        for account_id, forecast in direct.forecasts.items():
            self.assertEqual(loaded.forecasts[account_id].short_trend.hours_to_full,
                             forecast.short_trend.hours_to_full)
        for account_id, series in direct.trends.items():
            self.assertEqual([p.value for p in loaded.trends[account_id].points],
                             [p.value for p in series.points])
            self.assertEqual(loaded.trends[account_id].points[-1].at,
                             series.points[-1].at)
        self.assertEqual([a.kind for a in loaded.plan.actions], [a.kind for a in direct.plan.actions])
        self.assertEqual(loaded.newest, direct.newest)

    def test_the_window_does_not_open_the_db_when_the_file_is_good(self):
        self.write()
        with patch("smvwp.dashboard.read_dashboard", side_effect=AssertionError("DB 를 열었다")):
            data = home_view.load(self.data_dir, self.config)
        self.assertEqual(len(data.samples), 2)


class FallbackTests(HomeViewCase):
    """믿을 수 없는 파일이면 DB 에서 읽는다 - 틀린 숫자보다 느린 숫자가 낫다."""

    def assert_falls_back(self):
        self.assertIsNone(home_view.read(self.data_dir, self.config))
        with patch("smvwp.dashboard.read_dashboard", wraps=dashboard.read_dashboard) as direct:
            data = home_view.load(self.data_dir, self.config)
        direct.assert_called_once()
        self.assertEqual(len(data.samples), 2)

    def test_no_file_yet(self):
        self.assert_falls_back()

    def test_a_file_from_another_format(self):
        self.write()
        path = home_view.view_path(self.data_dir)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["format"] = home_view.FORMAT + 99
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assert_falls_back()

    def test_a_file_written_for_other_accounts(self):
        self.write()
        (self.data_dir.parent / "new").mkdir()
        config_module.add_account(self.config, "new", str(self.data_dir.parent / "new"),
                                  data_dir=self.data_dir)
        self.assert_falls_back()

    def test_a_stale_file(self):
        """수집이 멈췄으면(cron 이 죽었다) 파일이 낡는다 - 낡은 홈을 지금 것처럼 보이면 안 된다."""

        self.write()
        later = datetime.now(timezone.utc) + timedelta(hours=2)
        with patch("smvwp.home_view._now", return_value=later):
            self.assertIsNone(home_view.read(self.data_dir, self.config))

    def test_a_broken_file(self):
        home_view.view_path(self.data_dir).parent.mkdir(parents=True, exist_ok=True)
        home_view.view_path(self.data_dir).write_text("{not json", encoding="utf-8")
        self.assert_falls_back()

    def test_unknown_shapes_are_refused_not_built(self):
        """정해 둔 모양 밖의 것이 들어 있으면 만들지 않고 버린다."""

        self.write()
        path = home_view.view_path(self.data_dir)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["data"]["samples"]["x"] = {"__t": "os.system", "command": "echo hi"}
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(home_view.read(self.data_dir, self.config))


class CollectorWritesItTests(HomeViewCase):
    def test_a_collection_cycle_leaves_the_file(self):
        from smvwp import cycle

        with patch("smvwp.collector.collect_all", return_value=[]):
            cycle.run_collection_cycle(self.data_dir, self.config)
        self.assertIsNotNone(home_view.read(self.data_dir, self.config))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
