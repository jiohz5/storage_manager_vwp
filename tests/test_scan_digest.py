"""스캔 결과를 한눈에 읽히는 몇 줄로 줄이는 부분.

## 왜 이것이 생겼나

상세 스캔 화면은 표 셋을 세로로 쌓아 놓은 것이었다. 셋 다 같은 무게라
**결론이 어디에도 없었다.** 아침에 창을 연 사람이 표 셋을 읽고 스스로 요약을
만들어야 했고, 그러면 대부분 안 읽는다.

## 여기서 지키는 것

- **모르는 것을 0 으로 채우지 않는다.** 견줄 이전 스캔이 없으면 증가량은
  없는 것이지 0 이 아니다.
- **증가량은 계정 총량의 차이로 낸다.** 경로별 증감을 더하면 `du` 가 부모와
  자식을 함께 주므로 같은 바이트를 여러 번 센다.
- 급한 것(실패·권한)이 목록 아래로 밀리지 않는다.
"""

import unittest
from dataclasses import dataclass, field
from typing import List, Optional

from smvwp import scan_digest

GB = 1024 * 1024


@dataclass
class FakeAccount:
    account_id: str = "a1"
    account_name: str = "layout_proj"
    measured_kb: Optional[int] = None
    previous_measured_kb: Optional[int] = None
    current_scan_at: Optional[str] = "2026-09-10T00:00:00+00:00"
    large_files: List[tuple] = field(default_factory=list)
    failed_count: int = 0
    partial_paths: List[str] = field(default_factory=list)


@dataclass
class FakeSnapshot:
    accounts: List[FakeAccount] = field(default_factory=list)
    latest_run: Optional[dict] = None
    is_running: bool = False


def snapshot(*accounts, run=None, running=False):
    return FakeSnapshot(accounts=list(accounts), latest_run=run, is_running=running)


class GrowthTests(unittest.TestCase):
    def test_growth_is_the_difference_of_totals(self):
        """경로별 증감을 더하면 부모와 자식을 겹쳐 센다."""

        digest = scan_digest.build(snapshot(
            FakeAccount(measured_kb=900 * GB, previous_measured_kb=700 * GB)
        ))
        self.assertEqual(digest.total_delta_kb, 200 * GB)
        self.assertEqual(digest.accounts_compared, 1)

    def test_several_accounts_add_up(self):
        digest = scan_digest.build(snapshot(
            FakeAccount("a", "one", 900 * GB, 700 * GB),
            FakeAccount("b", "two", 300 * GB, 250 * GB),
        ))
        self.assertEqual(digest.total_delta_kb, 250 * GB)
        self.assertEqual(digest.accounts_compared, 2)

    def test_shrinking_counts_too(self):
        digest = scan_digest.build(snapshot(
            FakeAccount(measured_kb=500 * GB, previous_measured_kb=700 * GB)
        ))
        self.assertEqual(digest.total_delta_kb, -200 * GB)

    def test_nothing_to_compare_is_not_zero(self):
        """0 으로 두면 '안 늘었다'가 되는데 사실은 '견줄 것이 없다'이다."""

        digest = scan_digest.build(snapshot(FakeAccount(measured_kb=900 * GB)))
        self.assertIsNone(digest.total_delta_kb)
        self.assertEqual(digest.accounts_compared, 0)
        self.assertEqual(digest.accounts_total, 1)

    def test_an_account_without_a_baseline_does_not_block_the_others(self):
        digest = scan_digest.build(snapshot(
            FakeAccount("a", "measured", 900 * GB, 700 * GB),
            FakeAccount("b", "fresh", 300 * GB, None),
        ))
        self.assertEqual(digest.total_delta_kb, 200 * GB)
        self.assertEqual(digest.accounts_compared, 1)
        self.assertEqual(digest.accounts_total, 2)

    def test_accounts_come_back_biggest_growth_first(self):
        digest = scan_digest.build(snapshot(
            FakeAccount("a", "small", 110 * GB, 100 * GB),
            FakeAccount("b", "big", 900 * GB, 200 * GB),
        ))
        self.assertEqual([item.name for item in digest.growth], ["big", "small"])

    def test_the_biggest_is_named(self):
        digest = scan_digest.build(snapshot(
            FakeAccount("a", "small", 110 * GB, 100 * GB),
            FakeAccount("b", "big", 900 * GB, 200 * GB),
        ))
        self.assertEqual(digest.biggest.name, "big")
        self.assertEqual(digest.biggest.delta_kb, 700 * GB)

    def test_with_nothing_measured_there_is_no_biggest(self):
        digest = scan_digest.build(snapshot(FakeAccount(measured_kb=900 * GB)))
        self.assertIsNone(digest.biggest)

    def test_an_empty_snapshot_does_not_raise(self):
        digest = scan_digest.build(snapshot())
        self.assertEqual(digest.accounts_total, 0)
        self.assertIsNone(digest.total_delta_kb)
        self.assertEqual(digest.findings, [])


class RunTests(unittest.TestCase):
    def test_the_run_status_and_length_are_carried(self):
        digest = scan_digest.build(snapshot(run={
            "status": "completed",
            "started_at": "2026-09-09T13:00:00+00:00",
            "ended_at": "2026-09-09T20:10:00+00:00",
        }))
        self.assertEqual(digest.run_status, "completed")
        self.assertAlmostEqual(digest.run_seconds, 7 * 3600 + 600)

    def test_a_running_scan_has_no_length_yet(self):
        digest = scan_digest.build(snapshot(run={
            "status": "running", "started_at": "2026-09-09T13:00:00+00:00",
            "ended_at": None,
        }, running=True))
        self.assertIsNone(digest.run_seconds)
        self.assertTrue(digest.is_running)

    def test_no_run_at_all(self):
        digest = scan_digest.build(snapshot())
        self.assertIsNone(digest.run_status)

    def test_a_row_like_object_is_read_the_same_way(self):
        """DB 는 sqlite3.Row 를 준다 - dict 가 아니다."""

        class Row:
            def keys(self):
                return ["status", "started_at", "ended_at"]

            def __getitem__(self, key):
                return {"status": "paused", "started_at": None, "ended_at": None}[key]

        digest = scan_digest.build(snapshot(run=Row()))
        self.assertEqual(digest.run_status, "paused")


class FindingTests(unittest.TestCase):
    def test_a_failure_is_urgent(self):
        digest = scan_digest.build(snapshot(FakeAccount(failed_count=3)))
        self.assertEqual(digest.findings[0].kind, scan_digest.FINDING_FAILED)
        self.assertTrue(digest.findings[0].urgent)
        self.assertEqual(digest.urgent_count, 1)

    def test_a_partial_read_is_urgent_because_it_misleads(self):
        """덜 세어진 것을 모르고 보면 '안 늘었네'로 잘못 읽는다."""

        digest = scan_digest.build(snapshot(FakeAccount(partial_paths=["/a", "/b"])))
        self.assertEqual(digest.findings[0].kind, scan_digest.FINDING_PARTIAL)
        self.assertEqual(digest.findings[0].count, 2)

    def test_urgent_things_come_before_the_rest(self):
        """실패가 목록 아래로 밀리면 표 밑으로 사라진다."""

        digest = scan_digest.build(snapshot(FakeAccount(
            measured_kb=900 * GB, previous_measured_kb=100 * GB, failed_count=1,
        )))
        self.assertEqual(digest.findings[0].kind, scan_digest.FINDING_FAILED)

    def test_what_could_not_be_measured_comes_before_what_was_measured_short(self):
        """앞쪽은 숫자가 아예 없는 것이고, 뒤쪽은 있는데 작은 것이다.

        계정 순서대로 두면 우연히 이름이 앞선 계정의 경고가 위로 온다."""

        digest = scan_digest.build(snapshot(
            FakeAccount("a", "partial_one", partial_paths=["/x"]),
            FakeAccount("b", "failed_one", failed_count=1),
        ))
        self.assertEqual(digest.findings[0].kind, scan_digest.FINDING_FAILED)
        self.assertEqual(digest.findings[1].kind, scan_digest.FINDING_PARTIAL)

    def test_a_notable_large_file_shows_up(self):
        digest = scan_digest.build(snapshot(FakeAccount(
            measured_kb=1000 * GB,
            large_files=[("/a/movie.iso", 300 * GB, 50 * GB)],
        )))
        kinds = [item.kind for item in digest.findings]
        self.assertIn(scan_digest.FINDING_LARGE_FILE, kinds)

    def test_an_ordinary_file_does_not(self):
        """전부 올리면 목록이 표가 되고, 표가 되면 원래 문제로 돌아간다."""

        digest = scan_digest.build(snapshot(FakeAccount(
            measured_kb=100_000 * GB,
            large_files=[("/a/steady.dat", 100 * GB, 100 * GB)],
        )))
        self.assertEqual(digest.findings, [])

    def test_a_big_account_jump_is_worth_a_line(self):
        digest = scan_digest.build(snapshot(FakeAccount(
            measured_kb=900 * GB, previous_measured_kb=100 * GB
        )))
        kinds = [item.kind for item in digest.findings]
        self.assertIn(scan_digest.FINDING_GROWTH, kinds)

    def test_a_small_jump_is_not(self):
        """작은 계정이 조금 는 것까지 올리면 목록이 잡음으로 찬다."""

        digest = scan_digest.build(snapshot(FakeAccount(
            measured_kb=101 * GB, previous_measured_kb=100 * GB
        )))
        self.assertEqual(digest.findings, [])

    def test_the_list_is_capped(self):
        accounts = [
            FakeAccount(f"a{i}", f"acct{i}", 900 * GB, 100 * GB)
            for i in range(scan_digest.MAX_FINDINGS + 5)
        ]
        digest = scan_digest.build(snapshot(*accounts))
        self.assertEqual(len(digest.findings), scan_digest.MAX_FINDINGS)

    def test_bigger_things_come_first_among_the_rest(self):
        digest = scan_digest.build(snapshot(
            FakeAccount("a", "small", 160 * GB, 100 * GB),
            FakeAccount("b", "huge", 5000 * GB, 100 * GB),
        ))
        growth = [f for f in digest.findings if f.kind == scan_digest.FINDING_GROWTH]
        self.assertEqual(growth[0].account, "huge")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
