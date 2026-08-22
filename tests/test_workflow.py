"""과제(`*_run_*`) 디렉터리 인식 규칙."""

import unittest

from smvwp import workflow


class RunDirNameTests(unittest.TestCase):
    def test_recognizes_run_directory(self):
        self.assertTrue(workflow.is_run_dir("00_run_0811"))
        self.assertTrue(workflow.is_run_dir("01_run_1203"))

    def test_rejects_names_without_the_marker(self):
        # `_run_`이 통째로 있어야 한다. 아래는 사람이 보기에도 과제가 아니다.
        for name in ("LAYOUT", "BACKUP", "run", "rerun", "run_0811", "00_run"):
            with self.subTest(name=name):
                self.assertFalse(workflow.is_run_dir(name))

    def test_checks_the_basename_not_the_whole_path(self):
        """상위에 run 디렉터리가 있다고 그 아래가 전부 과제가 되면 안 된다.

        이것이 무너지면 과제 하나가 생길 때마다 그 안의 BACKUP, CROSSCHECK...
        까지 전부 '과제 생성'으로 보고되어 개수를 믿을 수 없게 된다."""

        self.assertTrue(workflow.is_run_path("/user/p/과제A/LAYOUT/00_run_0811"))
        self.assertFalse(workflow.is_run_path("/user/p/과제A/LAYOUT/00_run_0811/BACKUP"))


class TaskNameTests(unittest.TestCase):
    def test_task_name_is_first_component_under_account_root(self):
        self.assertEqual(
            workflow.task_name_for("/user/proj/과제A/LAYOUT/00_run_0811", "/user/proj"),
            "과제A",
        )

    def test_trailing_slash_on_account_path_does_not_break_it(self):
        self.assertEqual(
            workflow.task_name_for("/user/proj/과제A/LAYOUT/00_run_0811", "/user/proj/"),
            "과제A",
        )

    def test_path_outside_the_account_gives_nothing(self):
        # 추측해서 지어내면 보고서에 엉뚱한 과제명이 실린다.
        self.assertEqual(workflow.task_name_for("/other/과제A/00_run_1", "/user/proj"), "")

    def test_display_label_pairs_task_and_run(self):
        self.assertEqual(
            workflow.display_label("/user/proj/과제A/LAYOUT/00_run_0811", "/user/proj"),
            "과제A / 00_run_0811",
        )

    def test_display_label_falls_back_to_run_name_alone(self):
        self.assertEqual(
            workflow.display_label("/elsewhere/00_run_0811", "/user/proj"), "00_run_0811"
        )


class StageDirTests(unittest.TestCase):
    def setUp(self):
        self.run_path = "/user/proj/과제A/LAYOUT/00_run_0811"
        self.paths = [
            self.run_path,
            self.run_path + "/CROSSCHECK",
            self.run_path + "/BACKUP",
            self.run_path + "/OPUS",
            self.run_path + "/작업탑1",
            self.run_path + "/BACKUP/더깊은곳",
            "/user/proj/과제A/LAYOUT/00_run_0811x/BACKUP",
        ]

    def test_returns_standard_stages_in_workflow_order(self):
        # 알파벳순이 아니라 실제 작업 흐름 순서여야 읽는 품이 들지 않는다.
        self.assertEqual(
            workflow.stage_dirs_in(self.paths, self.run_path),
            ["BACKUP", "CROSSCHECK", "OPUS"],
        )

    def test_ignores_deeper_levels(self):
        # BACKUP 아래의 것은 단계가 아니다.
        self.assertNotIn("더깊은곳", workflow.stage_dirs_in(self.paths, self.run_path))

    def test_does_not_leak_from_a_sibling_with_a_longer_name(self):
        """`00_run_0811`과 `00_run_0811x`는 다른 디렉터리다.

        접두사 비교를 `/` 없이 하면 후자의 BACKUP이 전자의 것으로 잡힌다."""

        result = workflow.stage_dirs_in(
            ["/user/proj/과제A/LAYOUT/00_run_0811x/BACKUP"], self.run_path
        )
        self.assertEqual(result, [])

    def test_backup_dir_for(self):
        self.assertEqual(workflow.backup_dir_for(self.run_path), self.run_path + "/BACKUP")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
