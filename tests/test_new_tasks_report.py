"""일간 보고서의 '과제 생성' 항목.

감지는 파일시스템을 다시 뒤지지 않고 `baseline_results`의 세대 차이만 본다
(`smvwp/workflow.py` 참고). 그래서 여기 테스트도 du를 흉내 내지 않고 스캔이
남겼을 경로를 직접 넣는다.
"""

import tempfile
import unittest
from pathlib import Path

from smvwp import config as config_module
from smvwp import i18n, reports, scan_store


class NewTaskSectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.account_path = self.root / "proj"
        self.account_path.mkdir(parents=True)

        self.config = config_module.load_config(self.data_dir)
        self.account = config_module.add_account(
            self.config,
            "project_a",
            str(self.account_path),
            data_dir=self.data_dir,
            kind=config_module.ACCOUNT_KIND_PROJECT,
        )
        config_module.save_config(self.data_dir, self.config)
        self._original_language = i18n.get_language()
        i18n.set_language(i18n.KOREAN)

    def tearDown(self):
        i18n.set_language(self._original_language)
        self.tmp.cleanup()

    # -- 도우미 --------------------------------------------------------
    def _p(self, *parts) -> str:
        return "/".join([str(self.account_path).replace("\\", "/"), *parts])

    def _store_generation(self, generation, relative_paths, completed=True):
        conn = scan_store.connect(self.data_dir)
        try:
            entries = [(self._p(*path.split("/")), 100) for path in relative_paths]
            scan_store.save_tree_entries(
                conn,
                self.account.account_id,
                generation,
                entries,
                str(self.account_path).replace("\\", "/"),
            )
            if completed:
                scan_store.mark_generation_completed(
                    conn, self.account.account_id, generation
                )
        finally:
            conn.close()

    def _report(self) -> str:
        # 계정 경로 비교가 되도록 config의 경로도 같은 구분자로 맞춘다.
        self.account.path = str(self.account_path).replace("\\", "/")
        return reports.build_daily_report(self.data_dir, self.config)

    # -- 테스트 --------------------------------------------------------
    def test_first_scan_reports_nothing(self):
        """비교 대상이 없으면 판정을 보류한다.

        첫 스캔에서 '전부 새로 생겼다'고 말하면 보고서가 과제 수천 개로
        뒤덮이고 진짜 신규와 구분이 안 된다."""

        self._store_generation(1, ["과제A/LAYOUT/00_run_0811"])
        text = self._report()
        self.assertIn(i18n.t("reports.new_tasks_heading"), text)
        self.assertIn(i18n.t("reports.new_tasks_none"), text)

    def test_detects_a_run_dir_that_appeared_since_the_previous_scan(self):
        self._store_generation(1, ["과제A/LAYOUT/00_run_0811"])
        self._store_generation(
            2,
            [
                "과제A/LAYOUT/00_run_0811",
                "과제A/LAYOUT/01_run_0902",
                "과제A/LAYOUT/01_run_0902/BACKUP",
                "과제A/LAYOUT/01_run_0902/SIGNOFF",
            ],
        )
        text = self._report()
        self.assertIn("과제A / 01_run_0902", text)
        # 이미 있던 과제는 다시 보고하지 않는다.
        self.assertNotIn("00_run_0811", text)

    def test_lists_the_standard_stage_dirs_that_exist(self):
        self._store_generation(1, ["과제A/LAYOUT/00_run_0811"])
        self._store_generation(
            2,
            [
                "과제A/LAYOUT/00_run_0811",
                "과제A/LAYOUT/01_run_0902",
                "과제A/LAYOUT/01_run_0902/BACKUP",
                "과제A/LAYOUT/01_run_0902/CROSSCHECK",
            ],
        )
        text = self._report()
        self.assertIn("BACKUP, CROSSCHECK", text)

    def test_children_of_a_new_run_dir_are_not_counted_as_tasks(self):
        """run 디렉터리 아래의 단계 디렉터리까지 '과제 생성'이 되면 안 된다."""

        self._store_generation(1, ["과제A/LAYOUT/00_run_0811"])
        self._store_generation(
            2,
            [
                "과제A/LAYOUT/00_run_0811",
                "과제A/LAYOUT/01_run_0902",
                "과제A/LAYOUT/01_run_0902/BACKUP",
                "과제A/LAYOUT/01_run_0902/CCOM",
            ],
        )
        text = self._report()
        self.assertIn(i18n.t("reports.new_tasks_count", count=1), text)

    def test_backup_accounts_are_not_scanned_for_new_tasks(self):
        """백업 계정에는 프로젝트의 사본이 들어와 같은 과제가 한 번 더 잡힌다."""

        self.account.kind = config_module.ACCOUNT_KIND_BACKUP
        self._store_generation(1, ["과제A/LAYOUT/00_run_0811"])
        self._store_generation(
            2, ["과제A/LAYOUT/00_run_0811", "과제A/LAYOUT/01_run_0902"]
        )
        text = self._report()
        self.assertIn(i18n.t("reports.new_tasks_no_project_accounts"), text)
        self.assertNotIn("01_run_0902", text)

    def test_says_what_to_do_when_no_project_account_exists(self):
        """섹션을 통째로 빼면 사용자는 기능이 고장 난 줄 안다."""

        self.account.kind = config_module.ACCOUNT_KIND_UNSET
        text = self._report()
        self.assertIn(i18n.t("reports.new_tasks_no_project_accounts"), text)

    def test_non_run_directories_are_ignored(self):
        self._store_generation(1, ["과제A/LAYOUT"])
        self._store_generation(2, ["과제A/LAYOUT", "과제A/LAYOUT/새폴더", "과제B"])
        text = self._report()
        self.assertIn(i18n.t("reports.new_tasks_none"), text)


class RunDirLikePatternTests(unittest.TestCase):
    """`_`가 LIKE의 와일드카드라는 점 때문에 생기는 오탐을 막는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.conn = scan_store.connect(self.data_dir)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _save(self, generation, paths):
        scan_store.save_tree_entries(
            self.conn, "acct", generation, [(p, 1) for p in paths], "/root"
        )

    def test_escaped_underscore_does_not_match_arbitrary_characters(self):
        self._save(1, ["/root/keep"])
        self._save(2, ["/root/keep", "/root/xrunY", "/root/00_run_0811"])
        rows = scan_store.new_paths(
            self.conn, "acct", 2, 1, like_pattern="%\\_run\\_%"
        )
        self.assertEqual([row["path"] for row in rows], ["/root/00_run_0811"])

    def test_empty_previous_generation_yields_nothing(self):
        self._save(5, ["/root/00_run_0811"])
        self.assertEqual(scan_store.new_paths(self.conn, "acct", 5, 4), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
