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

from smvwp import health, priority, store

GB = 1024 * 1024


def FakeSample(account_id="a1", used_percent=None, ok=True):  # noqa: N802
    """**진짜 표본 레코드**를 만든다.

    예전에는 가짜 클래스에 `used_percent` 를 달아 썼는데, 진짜 레코드의 이름은
    `byte_pct` 다. 코드와 시험이 같은 틀린 이름을 봐서 시험은 통과했고, 운영에서는
    "스토리지가 찼다" 줄이 **한 번도 뜨지 않았다.** 가짜를 쓰면 같은 일이 또 난다."""

    return store.SampleRecord(
        account_id=account_id, collected_at="2026-09-20T00:00:00+00:00",
        ok=ok, byte_pct=used_percent,
    )


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

    def test_a_failed_collection_is_not_called_full(self):
        """실패한 수집의 숫자는 옛 값이다 - 그걸로 "지금 찼다" 고 하면 안 된다."""

        plan = priority.build(samples=[FakeSample(used_percent=99.0, ok=False)])
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


class SharedStorageTests(unittest.TestCase):
    """`df` 는 계정이 아니라 **그 경로가 속한 파일시스템**을 잰다.

    한 스토리지에 계정이 여덟이면 여덟 계정이 똑같은 숫자를 돌려주고, 그대로
    두면 똑같은 줄이 여덟 개 생겨 목록 상한 안에서 다른 종류(백업 없음, 큰
    파일)를 밀어낸다. 한 사람이 여러 스토리지를 볼 때 가장 먼저 무너지던 곳이다.
    """

    def sample(self, account_id, pct=96.0, total=40 * GB, mount="/ifs",
               filesystem="isilon:/ifs"):
        return store.SampleRecord(
            account_id=account_id, collected_at="2026-09-23T00:00:00+00:00", ok=True,
            filesystem=filesystem, mount_point=mount, total_kb=total,
            used_kb=int(total * pct / 100), avail_kb=1, byte_pct=pct,
        )

    def test_accounts_on_one_storage_make_one_line(self):
        plan = priority.build(
            samples=[self.sample(f"a{i}") for i in range(4)],
            accounts_by_id={f"a{i}": f"proj_{i}" for i in range(4)},
        )
        self.assertEqual(len(plan.actions), 1)
        self.assertEqual(plan.actions[0].shared_count, 4)
        self.assertEqual(plan.actions[0].mount_point, "/ifs")

    def test_different_storages_stay_apart(self):
        plan = priority.build(samples=[
            self.sample("a", mount="/ifs", filesystem="isilon:/ifs"),
            self.sample("b", mount="/ifs2", filesystem="isilon:/ifs2"),
        ])
        self.assertEqual(len(plan.actions), 2)

    def test_container_quotas_of_different_sizes_stay_apart(self):
        """같은 마운트라도 디렉터리 쿼터가 컨테이너면 계정마다 크기가 다르다."""

        plan = priority.build(samples=[
            self.sample("a", total=40 * GB), self.sample("b", total=10 * GB),
        ])
        self.assertEqual(len(plan.actions), 2)

    def test_the_same_place_with_very_different_numbers_is_not_merged(self):
        """크기가 같아도 사용률이 많이 다르면 같은 자리로 보기 어렵다."""

        plan = priority.build(samples=[
            self.sample("a", pct=96.0), self.sample("b", pct=91.0),
        ])
        self.assertEqual(len(plan.actions), 2)

    def test_a_few_seconds_of_drift_still_counts_as_one_place(self):
        """계정마다 `df` 를 부르는 시각이 어긋나 숫자가 딱 맞지는 않는다."""

        plan = priority.build(samples=[
            self.sample("a", pct=96.0), self.sample("b", pct=96.3),
        ])
        self.assertEqual(len(plan.actions), 1)
        # 그 스토리지의 값은 가장 높은 쪽으로 본다.
        self.assertAlmostEqual(plan.actions[0].pct, 96.3)

    def test_without_filesystem_information_nothing_is_merged(self):
        """모르는 것을 근거로 묶으면 남의 계정을 한 줄에 넣게 된다."""

        plan = priority.build(samples=[
            store.SampleRecord(account_id="a", collected_at="x", ok=True, byte_pct=96.0),
            store.SampleRecord(account_id="b", collected_at="x", ok=True, byte_pct=96.0),
        ])
        self.assertEqual(len(plan.actions), 2)

    def test_the_cleanup_total_covers_every_account_on_the_storage(self):
        plan = priority.build(
            samples=[self.sample("a"), self.sample("b")],
            health_summary=summary(
                run(account_id="a", backup_kb=300 * GB),
                run(account_id="b", backup_kb=200 * GB, label="과제B / 01_run"),
            ),
            accounts_by_id={"a": "proj_a", "b": "proj_b"},
        )
        action = plan.actions[0]
        self.assertEqual(action.reclaimable_kb, 500 * GB)
        # 대표 계정은 정리할 것이 가장 많은 쪽이다.
        self.assertEqual(action.account, "proj_a")
        self.assertEqual([item[0] for item in action.shared], ["proj_a", "proj_b"])

    def test_an_account_on_a_full_storage_does_not_get_a_second_cleanup_line(self):
        """같은 일이 두 줄이 되면 합계도 두 번 세인다."""

        plan = priority.build(
            samples=[self.sample("a"), self.sample("b")],
            health_summary=summary(
                run(account_id="a", backup_kb=300 * GB),
                run(account_id="b", backup_kb=300 * GB, label="과제B / 01_run"),
            ),
            accounts_by_id={"a": "proj_a", "b": "proj_b"},
        )
        self.assertEqual(kinds(plan), [priority.ACT_FULL])
        self.assertEqual(plan.reclaimable_kb, 600 * GB)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
