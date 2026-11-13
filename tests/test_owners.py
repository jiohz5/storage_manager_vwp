"""사람(파일 주인)별 사용량 - "누가 채웠나".

## 왜 이것이 생겼나

한 계정을 여럿이 쓰면 "이 계정이 32TB" 다음 질문은 늘 "누가?" 였다. 확인 명령
(`ls -la`)을 돌려 주인을 하나씩 봐야 했다. 순회는 파일마다 이미 stat 을 하고 거기에
주인(uid)이 있다 - 파일서버에 더 묻지 않고 센다 (`walker.WalkOutcome.owners`).

이름은 스캔하는 장비에서 바꿔 남긴다 (`pwd`). 창은 다른 장비에 뜰 수 있어 거기에
같은 사용자가 없을 수 있다. 이름이 없는 uid(퇴사자 등)는 숫자 그대로다.
"""

import tempfile
import unittest
from pathlib import Path

from smvwp import config as config_module
from smvwp import detail_scan, nightly_scan, scan_store
from tests import support

GB = 1024 * 1024


def outcome(sizes, owners, root):
    made = support.whole(sizes)
    made.owners = owners
    return made


class OwnersCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"
        self.config = config_module.load_config(self.data_dir)
        self.config.accounts = [config_module.Account(name="proj", path="/acct", account_id="a1")]

    def pass_(self, generation, owners_a, owners_b):
        tops = ["/acct/a", "/acct/b"]
        with scan_store.session(self.data_dir) as conn:
            support.scan_pass(conn, "a1", generation, tops, {
                tops[0]: outcome({tops[0]: 900 * GB}, owners_a, tops[0]),
                tops[1]: outcome({tops[1]: 300 * GB}, owners_b, tops[1]),
            })

    def owners(self, generation):
        with scan_store.session(self.data_dir) as conn:
            return scan_store.owner_totals(conn, "a1", generation)


class StoreTests(OwnersCase):
    def test_owners_add_up_across_the_measured_pieces(self):
        self.pass_(1, {1001: (800 * GB, 10), 1002: (100 * GB, 5)}, {1001: (300 * GB, 3)})
        found = {row["uid"]: (row["size_kb"], row["file_count"]) for row in self.owners(1)}
        self.assertEqual(found, {1001: (1100 * GB, 13), 1002: (100 * GB, 5)})

    def test_biggest_first_with_a_name(self):
        self.pass_(1, {1001: (800 * GB, 10), 1002: (100 * GB, 5)}, {})
        rows = self.owners(1)
        self.assertEqual([row["uid"] for row in rows], [1001, 1002])
        self.assertTrue(all(row["name"] for row in rows))

    def test_they_go_with_the_generation(self):
        for generation in (1, 2, 3):
            self.pass_(generation, {1001: (100 * GB, 1)}, {})
            with scan_store.session(self.data_dir) as conn:
                scan_store.prune_old_generations(conn, "a1", 2)
        self.assertEqual(self.owners(1), [])
        self.assertEqual(len(self.owners(3)), 1)

    def test_an_unknown_uid_keeps_its_number(self):
        self.assertEqual(detail_scan.owner_name(987654), "987654")


class SnapshotTests(OwnersCase):
    def test_the_snapshot_carries_owners_with_their_change(self):
        self.pass_(1, {1001: (800 * GB, 10), 1002: (100 * GB, 5)}, {})
        self.pass_(2, {1001: (1000 * GB, 12)}, {1002: (100 * GB, 5)})
        snapshot = nightly_scan.get_status_snapshot(self.data_dir, self.config)
        rows = {row["uid"]: row for row in snapshot.accounts[0].owners}
        self.assertEqual(rows[1001]["size_kb"], 1000 * GB)
        self.assertEqual(rows[1001]["previous_kb"], 800 * GB)
        self.assertEqual(rows[1002]["previous_kb"], 100 * GB)


class GuideTests(unittest.TestCase):
    def test_the_guide_names_who_to_ask(self):
        from types import SimpleNamespace

        from smvwp import action_guide, i18n, priority

        i18n.set_language("ko")
        detail = SimpleNamespace(
            sample=None, forecast=None, measured_kb=1000 * GB, path="/acct", failures=[],
            scan=SimpleNamespace(owners=[{"uid": 1001, "name": "kim", "size_kb": 600 * GB,
                                          "file_count": 3, "previous_kb": None}]),
        )
        guide = action_guide.Guide(kind=priority.ACT_SURGE, level=2, headline="")
        action_guide._owner_facts(guide, detail)
        self.assertEqual(len(guide.situation), 1)
        self.assertIn("kim 600.0 GB (60%)", guide.situation[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
