"""무엇부터 손대야 하는가.

## 왜 이것이 생겼나

사용률은 홈 표에, 백업 상태는 헬스체크에, 튀는 파일은 상세 스캔 탭에 있었다.
각각은 맞는 말인데 **"그래서 오늘 뭘 먼저 하지"** 에는 아무도 답하지 않았다.

## 여기서 지키는 것

- **문제와 해법을 붙인다.** 96% 인 것과 그 계정에 정리 가능한 800GB 가 있는
  것은 따로 놓으면 두 줄이지만 붙이면 한 줄이고 행동이 된다.
- 같은 일을 두 줄로 내지 않는다 - 합계까지 두 번 세인다.
- 없는 근거로 순위를 만들지 않는다. 스캔이 안 돈 계정은 빠질 뿐이고, 왜
  비어 있는지는 따로 말한다.
"""

import unittest
from dataclasses import dataclass, field
from typing import List, Optional

from smvwp import health, priority

GB = 1024 * 1024


@dataclass
class FakeSample:
    account_id: str = "a1"
    used_percent: Optional[float] = None


@dataclass
class FakeScanAccount:
    account_id: str = "a1"
    account_name: str = "layout_proj"
    last_completed_generation: Optional[int] = 2
    measured_kb: Optional[int] = None
    previous_measured_kb: Optional[int] = None
    large_files: List[tuple] = field(default_factory=list)


def run(account_id="a1", name="layout_proj", status=health.BACKED_UP,
        backup_kb=300 * GB, label="과제A / 01_run_0908"):
    return health.RunHealth(
        account_id=account_id, account_name=name,
        run_path="/proj/과제A/LAYOUT/01_run_0908", label=label,
        run_size_kb=backup_kb * 2, backup_size_kb=backup_kb, status=status,
    )


def summary(*items):
    return health.summarize([list(items)])


def kinds(plan):
    return [action.kind for action in plan.actions]


class UsageTests(unittest.TestCase):
    def test_a_nearly_full_account_is_critical(self):
        plan = priority.build(
            samples=[FakeSample(used_percent=99.0)],
            accounts_by_id={"a1": "layout_proj"},
        )
        self.assertEqual(plan.actions[0].kind, priority.ACT_FULL)
        self.assertTrue(plan.actions[0].critical)

    def test_a_merely_warned_account_is_lower(self):
        plan = priority.build(
            samples=[FakeSample(used_percent=91.0)],
            accounts_by_id={"a1": "layout_proj"},
        )
        self.assertEqual(plan.actions[0].level, priority.LEVEL_MEDIUM)

    def test_a_healthy_account_is_not_listed(self):
        plan = priority.build(samples=[FakeSample(used_percent=40.0)])
        self.assertEqual(plan.actions, [])

    def test_an_unmeasured_account_is_not_listed(self):
        """사용률을 모르는 것을 '괜찮다'로도 '문제'로도 쓰면 안 된다."""

        plan = priority.build(samples=[FakeSample(used_percent=None)])
        self.assertEqual(plan.actions, [])


class RemedyTests(unittest.TestCase):
    """문제와 해법을 한 줄에."""

    def test_the_cleanup_rides_along_with_the_full_warning(self):
        plan = priority.build(
            samples=[FakeSample(used_percent=96.0)],
            health_summary=summary(run(backup_kb=800 * GB)),
            accounts_by_id={"a1": "layout_proj"},
        )
        full = plan.actions[0]
        self.assertEqual(full.kind, priority.ACT_FULL)
        self.assertEqual(full.reclaimable_kb, 800 * GB)
        self.assertEqual(full.count, 1)

    def test_the_same_account_is_not_listed_twice(self):
        """같은 일이 두 줄이 되면 합계도 두 번 세인다."""

        plan = priority.build(
            samples=[FakeSample(used_percent=96.0)],
            health_summary=summary(run(backup_kb=800 * GB)),
            accounts_by_id={"a1": "layout_proj"},
        )
        self.assertEqual(kinds(plan).count(priority.ACT_CLEANUP), 0)
        self.assertEqual(plan.reclaimable_kb, 800 * GB)

    def test_a_cleanup_without_a_capacity_problem_stands_alone(self):
        plan = priority.build(
            health_summary=summary(run(backup_kb=800 * GB)),
        )
        self.assertEqual(kinds(plan), [priority.ACT_CLEANUP])

    def test_a_tiny_cleanup_is_not_worth_a_line(self):
        """몇 GB 를 위해 사람을 움직이게 하면 다음부터 이 목록을 안 믿는다."""

        plan = priority.build(health_summary=summary(run(backup_kb=2 * GB)))
        self.assertEqual(plan.actions, [])

    def test_nothing_confirmed_means_no_number_is_offered(self):
        """확인 안 된 것을 더하면 '이만큼 비울 수 있다'가 실제보다 커진다."""

        plan = priority.build(
            samples=[FakeSample(used_percent=96.0)],
            health_summary=summary(run(status=health.MISSING)),
            accounts_by_id={"a1": "layout_proj"},
        )
        full = next(a for a in plan.actions if a.kind == priority.ACT_FULL)
        self.assertIsNone(full.reclaimable_kb)


class BackupRiskTests(unittest.TestCase):
    def test_a_run_without_a_backup_is_high_not_low(self):
        """용량 문제가 아니라 잃을 위험이다."""

        plan = priority.build(health_summary=summary(run(status=health.MISSING)))
        action = plan.actions[0]
        self.assertEqual(action.kind, priority.ACT_NO_BACKUP)
        self.assertEqual(action.level, priority.LEVEL_HIGH)

    def test_several_risky_runs_become_one_line_per_account(self):
        plan = priority.build(health_summary=summary(
            run(status=health.MISSING), run(status=health.PARTIAL),
        ))
        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].count, 2)

    def test_a_run_still_in_progress_is_not_a_risk(self):
        plan = priority.build(health_summary=summary(
            run(status=health.NO_BACKUP_DIR)
        ))
        self.assertEqual(plan.actions, [])


class ScanTests(unittest.TestCase):
    def test_a_file_that_dominates_its_account_is_listed(self):
        plan = priority.build(scan_accounts=[FakeScanAccount(
            measured_kb=1000 * GB,
            large_files=[("/a/huge.dat", 300 * GB, 100 * GB)],
        )])
        self.assertEqual(kinds(plan), [priority.ACT_BIG_FILE])

    def test_a_merely_large_file_is_not(self):
        """여기까지 올리면 목록이 파일 목록이 된다 - 상세 스캔 탭에서 보면 된다."""

        plan = priority.build(scan_accounts=[FakeScanAccount(
            measured_kb=100_000 * GB,
            large_files=[("/a/big.dat", 300 * GB, 100 * GB)],
        )])
        self.assertEqual(plan.actions, [])

    def test_a_big_jump_is_listed(self):
        plan = priority.build(scan_accounts=[FakeScanAccount(
            measured_kb=1000 * GB, previous_measured_kb=500 * GB
        )])
        self.assertEqual(kinds(plan), [priority.ACT_SURGE])

    def test_a_small_jump_is_not(self):
        plan = priority.build(scan_accounts=[FakeScanAccount(
            measured_kb=520 * GB, previous_measured_kb=500 * GB
        )])
        self.assertEqual(plan.actions, [])

    def test_an_unscanned_account_is_named_not_silently_dropped(self):
        """빈 목록이 '문제 없음'으로 읽히면 안 된다."""

        plan = priority.build(scan_accounts=[
            FakeScanAccount(account_name="fresh", last_completed_generation=None)
        ])
        self.assertEqual(plan.accounts_without_scan, ["fresh"])
        self.assertEqual(plan.actions, [])


class OrderTests(unittest.TestCase):
    def test_urgent_before_important(self):
        plan = priority.build(
            samples=[FakeSample(account_id="full", used_percent=99.0)],
            health_summary=summary(run(account_id="risk", status=health.MISSING)),
            accounts_by_id={"full": "full_one"},
        )
        self.assertEqual(kinds(plan), [priority.ACT_FULL, priority.ACT_NO_BACKUP])

    def test_bigger_effect_first_within_a_level(self):
        plan = priority.build(health_summary=health.summarize([
            [run(account_id="a", name="small", backup_kb=100 * GB)],
            [run(account_id="b", name="huge", backup_kb=900 * GB)],
        ]))
        self.assertEqual([a.account for a in plan.actions], ["huge", "small"])

    def test_the_list_is_capped(self):
        samples = [
            FakeSample(account_id=f"a{i}", used_percent=99.0) for i in range(20)
        ]
        plan = priority.build(samples=samples)
        self.assertEqual(len(plan.actions), priority.MAX_ACTIONS)

    def test_critical_items_are_counted(self):
        plan = priority.build(samples=[
            FakeSample(account_id="a", used_percent=99.0),
            FakeSample(account_id="b", used_percent=91.0),
        ])
        self.assertEqual(plan.critical_count, 1)

    def test_nothing_at_all(self):
        plan = priority.build()
        self.assertEqual(plan.actions, [])
        self.assertEqual(plan.reclaimable_kb, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
