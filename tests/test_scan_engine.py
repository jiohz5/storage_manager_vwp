"""파이썬 순회 엔진이 야간 스캔 자리에 그대로 들어가는지 - 실제 디렉터리로 본다.

다른 야간 스캔 시험들은 `du` 출력을 흉내 내지만, 엔진 교체에서 정작 확인해야
할 것은 흉내로는 안 나온다: **진짜 파일을 걸어 나온 숫자가 DB에 제대로
들어가는가**. 그래서 여기서는 아무것도 가로채지 않고 임시 트리를 실제로 훑는다.

크기의 절대값은 시험하지 않는다. `du` 와 값이 일치하는지는 반입 장비에서
실측으로 확인했고(합계가 정확히 같았다), 개발 PC 에서는 `st_blocks` 가 없어
근사값이 나오기 때문이다 (`walker.HAVE_ST_BLOCKS`). 여기서 못박는 것은
**배선**이다 - 엔진이 실제로 불렸는가, 결과가 저장됐는가, 되돌릴 수 있는가.
"""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import detail_scan, nightly_scan, scan_store


class EnginePlanTests(unittest.TestCase):
    """동시성의 총량은 같고, 그것을 내는 자리만 달라진다."""

    def _settings(self, engine, workers=4):
        settings = config_module.Settings()
        settings.scan_engine = engine
        settings.checkpoint_workers = workers
        return settings

    def test_du_spreads_across_checkpoints(self):
        engine, checkpoints, walk = nightly_scan._engine_plan(
            self._settings(config_module.SCAN_ENGINE_DU)
        )
        self.assertEqual(engine, detail_scan.ENGINE_DU)
        self.assertEqual((checkpoints, walk), (4, 1))

    def test_python_spreads_inside_one_checkpoint(self):
        """서브트리 하나짜리 계정에서도 동시성이 듣게 하려는 것이다.

        밤을 다 먹는 계정은 대개 그 모양이라, 체크포인트를 나눠 갖는 방식은
        정작 문제인 계정을 돕지 못한다."""

        engine, checkpoints, walk = nightly_scan._engine_plan(
            self._settings(config_module.SCAN_ENGINE_PYTHON)
        )
        self.assertEqual(engine, detail_scan.ENGINE_PYTHON)
        self.assertEqual((checkpoints, walk), (1, 4))

    def test_total_concurrency_is_the_same_either_way(self):
        for engine in config_module.SCAN_ENGINES:
            with self.subTest(engine=engine):
                _, checkpoints, walk = nightly_scan._engine_plan(
                    self._settings(engine, workers=4)
                )
                self.assertEqual(checkpoints * walk, 4)

    def test_missing_setting_falls_back_to_du(self):
        """옛 설정 파일에는 이 값이 없다 - 그때 죽지 말고 예전 동작을 한다."""

        class Old:
            checkpoint_workers = 2

        engine, checkpoints, walk = nightly_scan._engine_plan(Old())
        self.assertEqual(engine, detail_scan.ENGINE_DU)
        self.assertEqual((checkpoints, walk), (2, 1))


class PythonEngineNightlyScanTests(unittest.TestCase):
    """가로채는 것 없이 실제 트리를 훑는다."""

    NIGHT = datetime(2026, 8, 24, 23, 0)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"
        self.account_path = self.root / "acct"

        # 하위 디렉터리 두 개에 내용을 실제로 만든다. 크기가 서로 다르게
        # 해 두어야 "둘 다 0" 같은 거짓 통과를 잡을 수 있다.
        for name, count in (("dir1", 3), ("dir2", 1)):
            target = self.account_path / name / "inner"
            target.mkdir(parents=True)
            for index in range(count):
                (target / f"f{index}.dat").write_bytes(b"x" * 20000)

        self.config = config_module.load_config(self.data_dir)
        self.config.settings.scan_engine = config_module.SCAN_ENGINE_PYTHON
        self.account = config_module.add_account(
            self.config, "project_a", str(self.account_path), data_dir=self.data_dir
        )
        config_module.save_config(self.data_dir, self.config)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self):
        return nightly_scan.run_nightly_scan(
            self.data_dir,
            self.config,
            bypass_window=True,
            clock=lambda: self.NIGHT,
            baseline_warmup_seconds=0.0,
        )

    def _entries(self):
        conn = scan_store.connect(self.data_dir)
        try:
            state = scan_store.get_account_state(conn, self.account.account_id)
            generation = state.last_completed_generation
            return {
                row["path"]: row["size_kb"]
                for row in scan_store.top_paths(
                    conn, self.account.account_id, generation, limit=50, max_depth=3
                )
            }
        finally:
            conn.close()

    def test_scan_completes_without_running_du(self):
        """`du` 를 부르지 않는다는 것을 실제로 막아서 확인한다.

        "완료됐다"만 보면, 엔진이 조용히 예전 경로로 돌아가 있어도 통과한다."""

        def forbidden(*args, **kwargs):
            raise AssertionError("파이썬 엔진인데 du가 실행됐습니다")

        with patch.object(detail_scan, "run_du_tree", side_effect=forbidden):
            summary = self._run()
        self.assertEqual(summary.status, nightly_scan.STATUS_COMPLETED)
        self.assertEqual([o.baseline_status for o in summary.accounts], ["done"])

    def test_measured_sizes_reach_the_database(self):
        self._run()
        entries = self._entries()
        self.assertTrue(entries, "측정 결과가 하나도 저장되지 않았습니다")

        by_name = {Path(path).name: size for path, size in entries.items()}
        self.assertIn("dir1", by_name)
        self.assertIn("dir2", by_name)
        # 파일 3개짜리가 1개짜리보다 커야 한다. 절대값은 파일시스템마다 다르다.
        self.assertGreater(by_name["dir1"], by_name["dir2"])

    def test_switching_back_to_du_needs_no_other_change(self):
        """되돌릴 수 있어야 한다 - 밤에 문제가 생기면 값 하나로 돌아간다."""

        self.config.settings.scan_engine = config_module.SCAN_ENGINE_DU
        config_module.save_config(self.data_dir, self.config)
        reloaded = config_module.load_config(self.data_dir)
        self.assertEqual(reloaded.settings.scan_engine, config_module.SCAN_ENGINE_DU)

    def test_engine_value_is_validated(self):
        self.config.settings.scan_engine = "gdu"
        config_module.save_config(self.data_dir, self.config)
        with self.assertRaises(config_module.ConfigError):
            config_module.load_config(self.data_dir)


class MeasureTreeTests(unittest.TestCase):
    """두 엔진이 같은 모양의 결과를 준다 (부르는 쪽이 갈라 볼 필요가 없게)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a").mkdir()
        (self.root / "a" / "one.dat").write_bytes(b"x" * 4096)

    def tearDown(self):
        self._tmp.cleanup()

    def test_python_engine_returns_a_du_shaped_outcome(self):
        outcome = detail_scan.measure_tree(
            str(self.root), timeout_seconds=30,
            max_depth=3, engine=detail_scan.ENGINE_PYTHON, workers=2,
        )
        self.assertIsInstance(outcome, detail_scan.DuTreeOutcome)
        self.assertTrue(outcome.completed)
        self.assertIsNotNone(outcome.root_size_kb)
        self.assertIn(
            str(self.root / "a"), {path for path, _ in outcome.entries}
        )

    def test_snapshot_directories_are_skipped(self):
        """`.snapshot` 은 같은 데이터를 두 번 세게 만든다 - `du --exclude` 와 같아야 한다."""

        snapshot = self.root / ".snapshot"
        snapshot.mkdir()
        (snapshot / "old.dat").write_bytes(b"x" * 100000)

        outcome = detail_scan.measure_tree(
            str(self.root), timeout_seconds=30,
            max_depth=3, engine=detail_scan.ENGINE_PYTHON, workers=1,
        )
        measured = {path for path, _ in outcome.entries}
        self.assertNotIn(str(snapshot), measured)

    def test_unknown_engine_falls_back_to_du(self):
        """설정 검증을 통과하지 못한 값이 여기까지 오면 예전 동작을 한다.

        여기서 죽으면 밤 스캔이 통째로 안 도는데, 그게 가장 나쁜 실패다."""

        sentinel = detail_scan.DuTreeOutcome(root_size_kb=7, completed=True)
        with patch.object(
            detail_scan, "run_du_tree", return_value=sentinel
        ) as called:
            outcome = detail_scan.measure_tree(
                str(self.root), timeout_seconds=30, max_depth=3, engine="gdu"
            )
        self.assertIs(outcome, sentinel)
        self.assertEqual(called.call_count, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
