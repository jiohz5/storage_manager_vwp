"""'무엇부터 할까' 한 줄을 상황 · 이유 · 할 일 · 근거로 푸는 부분 (Qt 없음).

여기서 지키는 것:

- **이 도구는 지우지 않는다.** 확인 명령은 읽기만 하는 것뿐이다 - 어떤 종류의
  안내에서도 `rm` 같은 것이 나오면 안 된다.
- 근거가 오기 전에도 상황·이유·할 일은 선다 (사람은 기다리는 동안 읽는다).
- 근거는 그 줄이 말한 것을 가리킨다 - 정리 후보면 백업 확인된 과제, 백업 없음이면
  빠진 항목, 큰 파일이면 그 파일.
"""

import shlex
import unittest

from smvwp import account_detail, action_guide, health, i18n, large_files, priority

GB = 1024 * 1024
ROOT = "/ifs/proj/design/layout_a"

# 확인 명령으로 허락하는 것. 이 목록 밖의 명령이 나오면 시험이 깨져야 한다.
READ_ONLY = {"ls", "du", "df", "stat"}


def action(kind, **kwargs):
    defaults = dict(
        kind=kind, level=priority.LEVEL_MEDIUM, account="layout_a", account_id="a1",
    )
    defaults.update(kwargs)
    return priority.Action(**defaults)


def run(name, status=health.BACKED_UP, size=100, missing=(), match=health.MATCH_ITEMS):
    item = health.RunHealth(
        "a1", "layout_a", f"{ROOT}/{name}/LAYOUT/01_run", f"{name} / 01_run",
        run_size_kb=size * GB, backup_size_kb=size * GB,
        mirror_path=f"/ifs/backup/{name}/01_run" if status == health.BACKED_UP else "",
        mirror_size_kb=size * GB if status == health.BACKED_UP else None,
        status=status, match_by=match,
    )
    item.missing_items = list(missing)
    return item


def detail(**kwargs):
    made = account_detail.AccountDetail("a1", "layout_a", ROOT, backup_name="backup_a")
    for key, value in kwargs.items():
        setattr(made, key, value)
    return made


FULL = action(priority.ACT_FULL, level=priority.LEVEL_CRITICAL, pct=96.4,
              reclaimable_kb=300 * GB, count=2)
FULL_NO_FIX = action(priority.ACT_FULL, level=priority.LEVEL_HIGH, pct=91.0)
NO_BACKUP = action(priority.ACT_NO_BACKUP, level=priority.LEVEL_HIGH,
                   size_kb=500 * GB, count=2)
CLEANUP = action(priority.ACT_CLEANUP, reclaimable_kb=300 * GB, count=2)
BIG = action(priority.ACT_BIG_FILE, size_kb=900 * GB, delta_kb=500 * GB, pct=40.0,
             path=f"{ROOT}/sim/psf/tran full.tr0")
SURGE = action(priority.ACT_SURGE, delta_kb=400 * GB, size_kb=2000 * GB)
ALL = (FULL, FULL_NO_FIX, NO_BACKUP, CLEANUP, BIG, SURGE)


def rich_detail():
    return detail(
        health=[
            run("small", size=100), run("big", size=200),
            run("lost", status=health.MISSING, size=300, missing=["lvs", "drc"]),
            run("half", status=health.PARTIAL, size=200),
        ],
        large_files=large_files.build(
            [(BIG.path, 900 * GB, 400 * GB), (f"{ROOT}/other.dat", 10 * GB, 10 * GB)],
            2250 * GB,
        ),
        scan=type("Scan", (), {
            "growth": [
                {"path": f"{ROOT}/a", "current_kb": 500 * GB, "previous_kb": 100 * GB},
                {"path": f"{ROOT}/b", "current_kb": 50 * GB, "previous_kb": 40 * GB},
            ],
            "top_paths": [], "measured_kb": 2000 * GB, "previous_measured_kb": 1600 * GB,
        })(),
    )


class ReadOnlyTests(unittest.TestCase):
    def test_every_command_only_reads(self):
        """정리는 사람이 한다. 이 도구가 내놓는 명령은 보기만 한다."""

        for item in ALL:
            guide = action_guide.build(item, rich_detail())
            for line in guide.commands:
                program = shlex.split(line)[0]
                self.assertIn(program, READ_ONLY, f"{item.kind}: {line}")

    def test_paths_with_spaces_stay_one_argument(self):
        guide = action_guide.build(BIG, rich_detail())
        for line in guide.commands:
            self.assertEqual(shlex.split(line)[-1], BIG.path)

    def test_the_guide_never_says_delete(self):
        for language in (i18n.KOREAN, "en"):
            i18n.set_language(language)
            try:
                for item in ALL:
                    guide = action_guide.build(item, rich_detail())
                    text = " ".join(guide.steps + guide.situation + [guide.why]).lower()
                    self.assertNotIn("rm -", text)
                    self.assertNotIn("지워도 된", text)
            finally:
                i18n.set_language(i18n.KOREAN)


class BeforeTheEvidenceArrivesTests(unittest.TestCase):
    def test_situation_why_and_steps_are_there_straight_away(self):
        for item in ALL:
            guide = action_guide.build(item)
            self.assertTrue(guide.loading)
            self.assertTrue(guide.situation, item.kind)
            self.assertTrue(guide.why, item.kind)
            self.assertTrue(guide.steps, item.kind)
            self.assertEqual(guide.evidence_rows, [])

    def test_the_headline_is_the_same_sentence_as_the_list(self):
        """창을 열어도 "아까 그 줄" 이라는 것이 이어져야 한다."""

        for item in ALL:
            self.assertEqual(action_guide.build(item).headline, action_guide.headline(item))


class EvidenceTests(unittest.TestCase):
    def test_a_full_account_with_a_fix_lists_the_confirmed_backups_biggest_first(self):
        guide = action_guide.build(FULL, rich_detail())
        labels = [row.cells[0] for row in guide.evidence_rows]
        self.assertEqual(labels, ["big / 01_run", "small / 01_run"])
        self.assertTrue(guide.commands[0].startswith("df -h"))

    def test_a_full_account_without_a_fix_shows_where_the_space_went(self):
        guide = action_guide.build(FULL_NO_FIX, rich_detail())
        self.assertEqual(guide.evidence_rows[0].cells[0], "a")
        self.assertEqual(guide.evidence_rows[0].path, f"{ROOT}/a")

    def test_missing_backups_name_what_is_missing(self):
        guide = action_guide.build(NO_BACKUP, rich_detail())
        lost = next(row for row in guide.evidence_rows if row.cells[0] == "lost / 01_run")
        self.assertIn("lvs", lost.cells[3])
        self.assertTrue(lost.warn)
        # '일부만' 은 있기는 한 것이라 칠하지 않는다.
        half = next(row for row in guide.evidence_rows if row.cells[0] == "half / 01_run")
        self.assertFalse(half.warn)

    def test_the_big_file_is_marked_among_the_others(self):
        guide = action_guide.build(BIG, rich_detail())
        marked = [row.path for row in guide.evidence_rows if row.warn]
        self.assertEqual(marked, [BIG.path])
        # 경로는 계정 기준으로 줄여 보여 준다.
        self.assertTrue(guide.evidence_rows[0].cells[0].startswith("sim/"))

    def test_a_surge_shows_the_biggest_growth_first(self):
        guide = action_guide.build(SURGE, rich_detail())
        self.assertEqual(guide.evidence_rows[0].path, f"{ROOT}/a")

    def test_a_coarse_match_is_warned_about(self):
        """성긴 판정을 정밀한 것처럼 내놓으면 '확인됨'이 실제보다 강하게 읽힌다."""

        coarse = run("rough", size=100, match=health.MATCH_RUN_NAME)
        guide = action_guide.build(CLEANUP, detail(health=[coarse]))
        self.assertIn(i18n.t("guide.coarse_warning"), guide.warnings)

    def test_a_part_that_could_not_be_read_is_said(self):
        guide = action_guide.build(
            FULL, detail(failures=[account_detail.PART_HEALTH])
        )
        self.assertTrue(guide.warnings)
        self.assertFalse(guide.loading)

    def test_no_evidence_rows_does_not_raise(self):
        for item in ALL:
            action_guide.build(item, detail())


class SideNoteTests(unittest.TestCase):
    def test_unscanned_accounts_get_a_guide(self):
        guide = action_guide.for_unscanned(["a", "b"])
        self.assertIn("a, b", " ".join(guide.situation))
        self.assertEqual(len(guide.steps), 3)
        self.assertEqual(guide.evidence_columns, [])

    def test_coarse_matching_gets_a_guide(self):
        guide = action_guide.for_coarse(4)
        self.assertEqual(len(guide.steps), 3)


class NoKeyLeakTests(unittest.TestCase):
    def test_nothing_shows_a_raw_translation_key(self):
        for language in (i18n.KOREAN, "en"):
            i18n.set_language(language)
            try:
                guides = [action_guide.build(item, rich_detail()) for item in ALL]
                guides += [action_guide.build(item) for item in ALL]
                guides += [action_guide.for_unscanned(["x"]), action_guide.for_coarse(1)]
                for guide in guides:
                    text = " ".join(
                        [guide.headline, guide.why, guide.evidence_title, guide.level_text]
                        + guide.situation + guide.steps + guide.warnings
                        + guide.evidence_columns
                        + [cell for row in guide.evidence_rows for cell in row.cells]
                    )
                    for prefix in ("guide.", "priority.", "health.status.", "detail."):
                        self.assertNotIn(prefix, text, f"{language} {guide.kind}")
            finally:
                i18n.set_language(i18n.KOREAN)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
