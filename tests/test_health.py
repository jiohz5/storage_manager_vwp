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




class ItemMatchingTests(unittest.TestCase):
    """`BACKUP` 아래 항목 이름으로 맞추는 쪽 (정밀).

    백업 계정에 그대로 남는 이름이 이것이다. 항목마다 따로 찾으므로 **무엇이
    안 갔는지**까지 말할 수 있다.
    """

    def with_items(self, **items):
        sizes = project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": sum(items.values())})
        for name, size in items.items():
            sizes[f"/proj/과제A/LAYOUT/01_run_0908/BACKUP/{name}"] = size
        return sizes

    def test_items_are_matched_one_by_one(self):
        items = check(
            self.with_items(designA=200 * GB, designB=100 * GB),
            {"/bak/designA": 200 * GB, "/bak/designB": 100 * GB},
        )
        self.assertEqual(items[0].match_by, health.MATCH_ITEMS)
        self.assertEqual(items[0].status, health.BACKED_UP)
        self.assertEqual(items[0].missing_items, [])

    def test_a_missing_item_is_named(self):
        """'덜 갔다' 로 끝내지 않고 무엇이 안 갔는지까지 말한다."""

        items = check(
            self.with_items(designA=200 * GB, designB=100 * GB),
            {"/bak/designA": 200 * GB},
        )
        self.assertEqual(items[0].status, health.PARTIAL)
        self.assertEqual(items[0].missing_items, ["designB"])

    def test_nothing_found_is_missing_not_partial(self):
        items = check(
            self.with_items(designA=200 * GB),
            {"/bak/전혀다른것": 5 * GB},
        )
        self.assertEqual(items[0].status, health.MISSING)

    def test_a_short_item_is_partial_even_if_all_names_are_there(self):
        """이름만 있고 내용이 덜 갔을 수 있다."""

        items = check(
            self.with_items(designA=200 * GB),
            {"/bak/designA": 20 * GB},
        )
        self.assertEqual(items[0].status, health.PARTIAL)

    def test_a_precise_match_is_not_marked_coarse(self):
        items = check(
            self.with_items(designA=200 * GB), {"/bak/designA": 200 * GB}
        )
        self.assertFalse(items[0].coarse)


class CoarseTests(unittest.TestCase):
    """깊이 제한 때문에 `BACKUP` 아래가 기록에 없을 때."""

    def test_it_falls_back_to_the_run_name(self):
        """깊이 때문에 못 본 것이지 백업이 없어서가 아니다 - 여기서 '없음' 이라고
        하면 멀쩡한 백업이 전부 경고가 된다."""

        items = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/과제A/01_run_0908": 300 * GB},
        )
        self.assertEqual(items[0].match_by, health.MATCH_RUN_NAME)
        self.assertEqual(items[0].status, health.BACKED_UP)

    def test_a_coarse_verdict_says_so(self):
        """성긴 판정을 정밀한 것처럼 내놓으면 '확인됨' 이 실제보다 강하게 읽힌다."""

        items = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/과제A/01_run_0908": 300 * GB},
        )
        self.assertTrue(items[0].coarse)

    def test_a_run_before_backup_is_not_called_coarse(self):
        """아직 BACKUP 이 없는 것은 판정을 안 한 것이지 성기게 한 것이 아니다."""

        items = check(project(), {})
        self.assertFalse(items[0].coarse)

    def test_the_summary_counts_coarse_verdicts(self):
        coarse = check(
            project(**{"/proj/과제A/LAYOUT/01_run_0908/BACKUP": 300 * GB}),
            {"/bak/과제A/01_run_0908": 300 * GB},
        )
        summary = health.summarize([coarse])
        self.assertEqual(summary.coarse_count, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
