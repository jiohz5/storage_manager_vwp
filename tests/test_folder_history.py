"""최상위 폴더의 장기 이력 - "언제부터 이렇게 늘었나".

## 왜 이것이 생겼나

스캔 결과는 두 바퀴만 남긴다 (`detail_scan_keep_generations`). 그래서 화면이 말할 수
있는 것은 "지난 바퀴보다 +3 TB" 까지였다 - 한 번에 늘었는지, 반년 동안 조금씩 늘었는지,
지난달 정리했다가 다시 찼는지는 모른다. 바퀴마다 계정 바로 아래 폴더의 크기 한 줄씩만
2년 남긴다 (계정 백 개 x 폴더 수십 개 x 바퀴 수백 번이라도 작다).

예전에 '쓰기만 하고 읽는 화면이 없던 표' 를 걷어 낸 적이 있다 (DESIGN.md). 그래서 읽는
화면과 함께 넣는다 - 용량 변화 표의 '추세' 칸.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smvwp import config as config_module
from smvwp import nightly_scan, scan_store
from tests import support

GB = 1024 * 1024


class HistoryCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"
        self.config = config_module.load_config(self.data_dir)
        self.account = config_module.Account(name="proj", path="/acct", account_id="a1")
        self.config.accounts = [self.account]

    def pass_(self, generation, sim_kb, lib_kb, at, failed_lib=False, split_sim=False):
        tops = ["/acct/lib", "/acct/sim"]
        outcomes = {
            "/acct/lib": support.failed() if failed_lib else support.whole({"/acct/lib": lib_kb}),
        }
        subdirs = {}
        if split_sim:
            # 시간 초과로 나눠 쟀다 - 계정 바로 아래 폴더의 줄이 없고 조각만 있다.
            outcomes["/acct/sim"] = support.cut({"/acct/sim/a": sim_kb // 2})
            outcomes["/acct/sim/b"] = support.whole({"/acct/sim/b": sim_kb // 2})
            subdirs["/acct/sim"] = ["/acct/sim/a", "/acct/sim/b"]
        else:
            outcomes["/acct/sim"] = support.whole({"/acct/sim": sim_kb})
        with scan_store.session(self.data_dir) as conn:
            support.scan_pass(conn, "a1", generation, tops, outcomes, subdirs=subdirs)
            scan_store.mark_generation_completed(conn, "a1", generation, completed_at=at)
            scan_store.record_folder_history(conn, "a1", generation, "/acct", at)

    def history(self, since="2000-01-01T00:00:00+00:00"):
        with scan_store.session(self.data_dir) as conn:
            return scan_store.folder_history(conn, "a1", since)


class RecordTests(HistoryCase):
    def test_one_line_per_top_folder_per_pass(self):
        self.pass_(1, 100 * GB, 50 * GB, "2026-01-01T03:00:00+00:00")
        self.pass_(2, 300 * GB, 50 * GB, "2026-06-01T03:00:00+00:00")
        found = self.history()
        self.assertEqual(found["/acct/sim"], [("2026-01-01T03:00:00+00:00", 100 * GB),
                                              ("2026-06-01T03:00:00+00:00", 300 * GB)])
        self.assertEqual(len(found["/acct/lib"]), 2)

    def test_it_outlives_the_scan_results(self):
        """결과는 두 바퀴만 남는다 - 이력은 그보다 오래 간다."""

        for generation in range(1, 5):
            self.pass_(generation, generation * 100 * GB, 50 * GB,
                       f"2026-0{generation}-01T03:00:00+00:00")
            with scan_store.session(self.data_dir) as conn:
                scan_store.prune_old_generations(conn, "a1", 2)
        self.assertEqual(len(self.history()["/acct/sim"]), 4)

    def test_a_split_folder_is_the_sum_of_its_pieces(self):
        self.pass_(1, 400 * GB, 50 * GB, "2026-01-01T03:00:00+00:00", split_sim=True)
        self.assertEqual(self.history()["/acct/sim"], [("2026-01-01T03:00:00+00:00", 400 * GB)])

    def test_a_folder_that_could_not_be_read_gets_no_line(self):
        """못 잰 폴더에 0 이나 반쪽 값을 남기면 추세선이 거짓으로 꺾인다."""

        self.pass_(1, 100 * GB, 50 * GB, "2026-01-01T03:00:00+00:00", failed_lib=True)
        self.assertNotIn("/acct/lib", self.history())

    def test_old_lines_are_pruned(self):
        self.pass_(1, 100 * GB, 50 * GB, "2023-01-01T03:00:00+00:00")
        self.pass_(2, 100 * GB, 50 * GB, "2026-01-01T03:00:00+00:00")
        with scan_store.session(self.data_dir) as conn:
            scan_store.prune_folder_history(
                conn, 730, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
        self.assertEqual([at for at, _ in self.history()["/acct/sim"]],
                         ["2026-01-01T03:00:00+00:00"])


class NightlyTests(HistoryCase):
    def test_the_nightly_scan_records_it_and_the_summary_carries_it(self):
        now = datetime.now(timezone.utc)
        self.pass_(1, 100 * GB, 50 * GB, (now - timedelta(days=60)).isoformat())
        # 야간 스캔이 둘째 바퀴를 끝내는 길 (`_process_baseline`) 을 그대로 탄다.
        tops = ["/acct/lib", "/acct/sim"]
        with scan_store.session(self.data_dir) as conn:
            support.scan_pass(conn, "a1", 2, tops, {
                "/acct/lib": support.whole({"/acct/lib": 50 * GB}),
                "/acct/sim": support.whole({"/acct/sim": 250 * GB}),
            }, stop_after=2)
            nightly_scan.finish_generation(conn, self.data_dir, self.account, 2,
                                           self.config.settings, now)
        nightly_scan.save_summaries(self.data_dir, self.config)
        snapshot = nightly_scan.get_status_snapshot(self.data_dir, self.config)
        trend = snapshot.accounts[0].folder_trends
        self.assertEqual([kb for _at, kb in trend["/acct/sim"]], [100 * GB, 250 * GB])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
