"""오래 안 바뀐 큰 폴더를 '살펴볼 것' 과 정리 후보의 근거로.

## 왜 이것이 생겼나

순회가 폴더마다 '오래 안 바뀐 양' 을 남기게 됐지만 폴더 펼쳐 보기를 열어 한 줄씩
훑어야 보였다. 그리고 정리 후보의 근거는 여전히 '두 바퀴 동안 크기가 같음' 하나였다 -
어제 만든 폴더도 두 바퀴 동안 안 건드리면 후보가 되고, 3년 묵은 폴더도 로그 한 줄이
붙으면 빠진다. 파일의 수정 시각이 훨씬 곧은 근거다.

**지워도 된다는 말이 아니다.** 테이프아웃 보관본처럼 오래됐어도 남겨야 하는 것이
있다 - 화면은 '살펴볼 것' 이라고만 한다.
"""

import tempfile
import unittest
from pathlib import Path

from smvwp import config as config_module
from smvwp import i18n, nightly_scan, reports, scan_digest, scan_store

GB = 1024 * 1024
YEAR_AGO = 1_700_000_000


class ColdFolderCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"
        self.config = config_module.load_config(self.data_dir)
        self.config.accounts = [config_module.Account(name="proj", path="/acct", account_id="a1")]

    def save(self, rows):
        """`{경로: (크기, 오래된 양)}` 을 한 바퀴의 결과로 (순회가 남기는 모양대로)."""

        entries = [(path, size) for path, (size, _cold) in rows.items()]
        details = {
            path: {"files": 10, "newest": YEAR_AGO, "cold_kb": cold, "own_kb": 0}
            for path, (_size, cold) in rows.items()
        }
        with scan_store.session(self.data_dir) as conn:
            for top in sorted(path for path in rows if path.count("/") == 2):
                under = [e for e in entries if e[0] == top or e[0].startswith(top + "/")]
                scan_store.save_tree_entries(conn, "a1", 1, under, top, details=details)
            scan_store.mark_generation_completed(conn, "a1", 1)

    def cold(self, **kwargs):
        with scan_store.session(self.data_dir) as conn:
            return scan_store.cold_folders(conn, "a1", 1, min_kb=100 * GB, **kwargs)


class ColdFoldersTests(ColdFolderCase):
    def test_big_and_almost_entirely_old(self):
        self.save({
            "/acct/tape": (900 * GB, 890 * GB),
            "/acct/live": (900 * GB, 100 * GB),     # 대부분은 요즘 것
            "/acct/tiny": (50 * GB, 50 * GB),       # 오래됐지만 작다
        })
        self.assertEqual([row["path"] for row in self.cold()], ["/acct/tape"])

    def test_the_topmost_folder_speaks_for_what_is_inside(self):
        """안쪽 폴더까지 따로 올리면 같은 덩어리가 여러 줄이 된다."""

        self.save({
            "/acct/tape": (900 * GB, 890 * GB),
            "/acct/tape/t0": (500 * GB, 500 * GB),
            "/acct/tape/t1": (400 * GB, 390 * GB),
        })
        self.assertEqual([row["path"] for row in self.cold()], ["/acct/tape"])

    def test_an_old_corner_of_a_live_folder_is_found(self):
        self.save({
            "/acct/sim": (2000 * GB, 600 * GB),
            "/acct/sim/regress_2401": (600 * GB, 600 * GB),
            "/acct/sim/regress_now": (1400 * GB, 0),
        })
        self.assertEqual([row["path"] for row in self.cold()], ["/acct/sim/regress_2401"])

    def test_biggest_first_and_capped(self):
        self.save({f"/acct/d{i}": ((200 + i) * GB, (200 + i) * GB) for i in range(8)})
        found = self.cold(limit=3)
        self.assertEqual([row["path"] for row in found], ["/acct/d7", "/acct/d6", "/acct/d5"])

    def test_a_scan_without_ages_has_nothing_to_say(self):
        with scan_store.session(self.data_dir) as conn:
            scan_store.save_tree_entries(conn, "a1", 1, [("/acct/x", 900 * GB)], "/acct/x")
            scan_store.mark_generation_completed(conn, "a1", 1)
        self.assertEqual(self.cold(), [])


class ShownTests(ColdFolderCase):
    def setUp(self):
        super().setUp()
        i18n.set_language("ko")
        self.save({"/acct/tape": (900 * GB, 890 * GB), "/acct/live": (900 * GB, 0)})
        nightly_scan.save_summaries(self.data_dir, self.config)

    def test_the_summary_lists_it_as_something_to_look_at(self):
        snapshot = nightly_scan.get_status_snapshot(self.data_dir, self.config)
        self.assertEqual([row["path"] for row in snapshot.accounts[0].cold_folders], ["/acct/tape"])
        digest = scan_digest.build(snapshot, self.config.settings)
        cold = [f for f in digest.findings if f.kind == scan_digest.FINDING_COLD]
        self.assertEqual([(f.path, f.size_kb, f.urgent) for f in cold],
                         [("/acct/tape", 890 * GB, False)])

    def test_the_cleanup_report_names_it_with_its_reason(self):
        text = reports.build_cleanup_report(self.data_dir, self.config)
        self.assertIn("/acct/tape", text)
        self.assertIn("180", text)
        self.assertNotIn("/acct/live", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
