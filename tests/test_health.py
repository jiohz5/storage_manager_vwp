"""프로젝트 계정 건강 검사 - 백업이 된 과제와 안 된 과제.

## 여기서 가장 조심하는 것

**"지워도 된다"고 말하지 않는다.** 우리가 대조하는 것은 이름과 크기지 내용이
아니다. 같은 이름이 비슷한 크기로 백업 계정에 있다는 것은 강한 정황이지만
증거는 아니고, 그것을 "안전하다"로 옮겼다가 틀리면 이 도구가 만들 수 있는
가장 나쁜 사고가 된다.

그래서 '정리 후보'까지만 말하고, 비울 수 있는 양도 **확인된 것만** 센다.

## 그리고 급한 쪽은 반대다

백업이 **안 된** run 디렉터리가 더 급하다. 지금 사라지면 복구할 곳이 없다.
목록에서 그것이 위로 오는지 본다.
"""

import unittest

from smvwp import health

GB = 1024 * 1024


def project(**extra):
    """`과제A/LAYOUT/01_run_0908` 한 벌."""

    base = {
        "/proj/과제A": 900 * GB,
        "/proj/과제A/LAYOUT": 800 * GB,
        "/proj/과제A/LAYOUT/01_run_0908": 700 * GB,
    }
    base.update(extra)
    return base


def check(project_sizes, backup_sizes):
    return health.check_account(
        "a1", "layout_proj", "/proj", project_sizes, backup_sizes
    )


class MatchingTests(unittest.TestCase):
    def test_a_backed_up_run_is_recognised(self):
        items = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/과제A/01_run_0908": 300 * GB},
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, health.BACKED_UP)

    def test_the_layout_of_the_backup_account_need_not_match(self):
        """백업 계정의 층 구조가 같다는 보장이 없다.

        상대 경로를 그대로 맞추면 구조가 조금만 달라도 전부 '백업 안 됨'으로
        뒤집힌다."""

        items = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/2026/아무데나/깊은곳/01_run_0908": 300 * GB},
        )
        self.assertEqual(items[0].status, health.BACKED_UP)

    def test_the_task_name_breaks_a_tie(self):
        """run 이름은 과제마다 다시 쓰일 수 있다 - 과제명까지 맞으면 확실하다."""

        items = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {
                "/bak/과제B/01_run_0908": 10 * GB,
                "/bak/과제A/01_run_0908": 300 * GB,
            },
        )
        self.assertEqual(items[0].mirror_path, "/bak/과제A/01_run_0908")
        self.assertEqual(items[0].status, health.BACKED_UP)

    def test_a_missing_mirror_is_flagged(self):
        """이쪽이 더 급하다 - 지금 사라지면 복구할 곳이 없다."""

        items = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/딴것/99_run_0101": 5 * GB},
        )
        self.assertEqual(items[0].status, health.MISSING)
        self.assertTrue(items[0].risky)

    def test_a_short_copy_is_flagged(self):
        items = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/과제A/01_run_0908": 40 * GB},
        )
        self.assertEqual(items[0].status, health.PARTIAL)
        self.assertTrue(items[0].risky)

    def test_a_slightly_different_size_is_still_backed_up(self):
        """몇 KB 어긋난다고 매일 경고하면 아무도 안 본다."""

        items = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/과제A/01_run_0908": int(300 * GB * 0.97)},
        )
        self.assertEqual(items[0].status, health.BACKED_UP)

    def test_a_bigger_backup_is_fine(self):
        items = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/과제A/01_run_0908": 400 * GB},
        )
        self.assertEqual(items[0].status, health.BACKED_UP)


class StageTests(unittest.TestCase):
    def test_a_run_without_a_backup_dir_is_not_a_problem(self):
        """아직 백업 단계 전이다. 진행 중인 과제의 정상 상태다."""

        items = check(project(), {"/bak/아무것": 1})
        self.assertEqual(items[0].status, health.NO_BACKUP_DIR)
        self.assertFalse(items[0].risky)

    def test_without_a_linked_account_no_judgement_is_made(self):
        """연결이 없는 것과 백업이 없는 것은 다른 이야기다."""

        items = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}), None
        )
        self.assertEqual(items[0].status, health.NO_LINK)
        self.assertFalse(items[0].risky)

    def test_a_linked_but_unscanned_account_is_not_the_same_as_unlinked(self):
        items = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}), {}
        )
        self.assertEqual(items[0].status, health.MISSING)

    def test_paths_that_are_not_run_dirs_are_ignored(self):
        items = check({"/proj/과제A": 900 * GB, "/proj/과제A/LAYOUT": 800 * GB}, {})
        self.assertEqual(items, [])


class ReclaimTests(unittest.TestCase):
    def test_only_confirmed_backups_count_towards_what_can_be_freed(self):
        """확인 안 된 것을 더하면 '이만큼 비울 수 있다'가 실제보다 커진다."""

        confirmed = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/과제A/01_run_0908": 300 * GB},
        )[0]
        risky = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/딴것": 1},
        )[0]
        self.assertEqual(confirmed.reclaimable_kb, 300 * GB)
        self.assertEqual(risky.reclaimable_kb, 0)

    def test_the_summary_adds_up_only_the_confirmed(self):
        good = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/과제A/01_run_0908": 300 * GB},
        )
        bad = health.check_account(
            "a2", "other", "/other",
            {
                "/other/과제B/LAYOUT/02_run_0101": 500 * GB,
                "/other/과제B/LAYOUT/02_run_0101/BACKUP": 200 * GB,
            },
            {},
        )
        summary = health.summarize([good, bad])
        self.assertEqual(summary.reclaimable_kb, 300 * GB)
        self.assertEqual(len(summary.cleanable), 1)
        self.assertEqual(len(summary.risky), 1)

    def test_coverage_is_reported_so_a_person_can_judge(self):
        """판정을 대신하지 않고 근거를 같이 준다."""

        item = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/과제A/01_run_0908": 150 * GB},
        )[0]
        self.assertAlmostEqual(item.coverage, 0.5)

    def test_coverage_is_unknown_when_there_is_nothing_to_compare(self):
        item = check(project(), {})[0]
        self.assertIsNone(item.coverage)


class OrderTests(unittest.TestCase):
    def test_risky_runs_come_first(self):
        """급한 것이 아래로 밀리면 목록 끝에서 안 보인다."""

        sizes = {
            "/proj/A/LAYOUT/01_run_0101": 100 * GB,
            "/proj/A/LAYOUT/01_run_0101/BACKUP": 90 * GB,
            "/proj/B/LAYOUT/02_run_0202": 100 * GB,
            "/proj/B/LAYOUT/02_run_0202/BACKUP": 90 * GB,
        }
        items = check(sizes, {"/bak/A/01_run_0101": 90 * GB})
        self.assertEqual(items[0].status, health.MISSING)
        self.assertEqual(items[0].label.split(" / ")[0], "B")

    def test_bigger_comes_first_within_a_status(self):
        sizes = {
            "/proj/small/LAYOUT/01_run_0101": 20 * GB,
            "/proj/small/LAYOUT/01_run_0101/BACKUP": 10 * GB,
            "/proj/huge/LAYOUT/02_run_0202": 900 * GB,
            "/proj/huge/LAYOUT/02_run_0202/BACKUP": 800 * GB,
        }
        items = check(sizes, {})
        self.assertEqual(items[0].label.split(" / ")[0], "huge")

    def test_the_label_says_which_task(self):
        """run 이름만 적으면 어느 과제인지 모른다."""

        items = check(project(), {})
        self.assertEqual(items[0].label, "과제A / 01_run_0908")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
