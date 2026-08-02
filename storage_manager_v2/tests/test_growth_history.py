"""경로별 증감 이력 축적.

이상탐지(중앙값·MAD 등)는 아직 구현하지 않았지만, 나중에 붙이려면 이력이
먼저 쌓여 있어야 한다. `baseline_results`는 DB 크기 때문에 최근 2세대만
남기므로, 숫자만 담은 별도 테이블에 훨씬 길게 보관한다.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import nightly_scan, scan_store
from tests import support


class RecordGrowthHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.conn = scan_store.connect(self.data_dir)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _baseline(self, generation, path, size_kb):
        self.conn.execute(
            "INSERT OR REPLACE INTO baseline_results "
            "(account_id, generation, path, size_kb, completed_at) VALUES (?, ?, ?, ?, ?)",
            ("acct-1", generation, path, size_kb, "2026-08-03T00:00:00+00:00"),
        )
        self.conn.commit()

    def _history(self, path="/acct/data"):
        return scan_store.growth_history_for_path(self.conn, "acct-1", path)

    def test_records_delta_between_generations(self):
        self._baseline(1, "/acct/data", 100)
        self._baseline(2, "/acct/data", 350)

        written = scan_store.record_growth_history(self.conn, "acct-1", 2, 1)
        self.assertEqual(written, 1)

        rows = self._history()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delta_kb"], 250)
        self.assertEqual(rows[0]["current_kb"], 350)

    def test_first_generation_records_nothing(self):
        """첫 기준선은 전부 '신규'라 통계를 왜곡한다."""

        self._baseline(1, "/acct/data", 100)
        self.assertEqual(scan_store.record_growth_history(self.conn, "acct-1", 1, 0), 0)
        self.assertEqual(self._history(), [])

    def test_new_path_counts_full_size(self):
        self._baseline(1, "/acct/old", 100)
        self._baseline(2, "/acct/new", 500)

        scan_store.record_growth_history(self.conn, "acct-1", 2, 1)
        rows = self._history("/acct/new")
        self.assertEqual(rows[0]["delta_kb"], 500)

    def test_shrinking_path_records_negative_delta(self):
        self._baseline(1, "/acct/data", 500)
        self._baseline(2, "/acct/data", 200)

        scan_store.record_growth_history(self.conn, "acct-1", 2, 1)
        self.assertEqual(self._history()[0]["delta_kb"], -300)

    def test_history_is_ordered_oldest_first(self):
        for generation, size in ((1, 100), (2, 200), (3, 400)):
            self._baseline(generation, "/acct/data", size)
        scan_store.record_growth_history(self.conn, "acct-1", 2, 1)
        scan_store.record_growth_history(self.conn, "acct-1", 3, 2)

        rows = self._history()
        self.assertEqual([r["generation"] for r in rows], [2, 3])
        self.assertEqual([r["delta_kb"] for r in rows], [100, 200])

    def test_prune_keeps_recent_generations(self):
        for generation in range(1, 11):
            self._baseline(generation, "/acct/data", generation * 100)
            scan_store.record_growth_history(self.conn, "acct-1", generation, generation - 1)

        scan_store.prune_growth_history(self.conn, "acct-1", keep_generations=3)
        rows = self._history()
        self.assertEqual([r["generation"] for r in rows], [8, 9, 10])

    def test_prune_is_noop_when_within_limit(self):
        self._baseline(1, "/acct/data", 100)
        self._baseline(2, "/acct/data", 200)
        scan_store.record_growth_history(self.conn, "acct-1", 2, 1)

        self.assertEqual(scan_store.prune_growth_history(self.conn, "acct-1", 60), 0)
        self.assertEqual(len(self._history()), 1)


class NightlyScanRecordsHistoryTests(unittest.TestCase):
    """기준선이 정리되어도 이력은 남아야 한다 - 이게 이 기능의 요점."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.account_path = self.root / "acct"
        (self.account_path / "dir1").mkdir(parents=True)

        self.config = config_module.load_config(self.data_dir)
        self.account = config_module.add_account(
            self.config, "project_a", str(self.account_path), data_dir=self.data_dir
        )
        # 기준선은 2세대만 남기고, 이력은 길게 남기는 기본 구성을 그대로 쓴다.
        config_module.save_config(self.data_dir, self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, size_kb):
        def runner(command, **kwargs):
            if "du" in command:
                return support.completed(command, stdout=f"{size_kb}\t{command[-1]}\n")
            return support.completed(command)

        with patch("smvwp.detail_scan.subprocess.run", side_effect=runner):
            nightly_scan.run_nightly_scan(
                self.data_dir,
                self.config,
                bypass_window=True,
                top_level_lister=lambda p: [f"{self.account_path}/dir1"],
            )

    def test_history_survives_baseline_pruning(self):
        for size in (100, 200, 400, 800):
            self._run(size)

        conn = scan_store.connect(self.data_dir)
        try:
            baseline_generations = {
                row["generation"]
                for row in conn.execute(
                    "SELECT DISTINCT generation FROM baseline_results WHERE account_id = ?",
                    (self.account.account_id,),
                ).fetchall()
            }
            history = scan_store.growth_history_for_path(
                conn, self.account.account_id, f"{self.account_path}/dir1"
            )
        finally:
            conn.close()

        # 기준선은 최근 2세대만 남는다.
        self.assertEqual(len(baseline_generations), 2)
        # 이력은 첫 세대를 제외한 나머지 전부(2,3,4세대)가 남아 있어야 한다.
        self.assertEqual([r["generation"] for r in history], [2, 3, 4])
        self.assertEqual([r["delta_kb"] for r in history], [100, 200, 400])


if __name__ == "__main__":
    unittest.main()
