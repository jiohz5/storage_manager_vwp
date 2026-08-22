"""계정 성격(프로젝트/백업)과 연결 백업 계정."""

import json
import tempfile
import unittest
from pathlib import Path

from smvwp import config as config_module


class AccountKindTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.config = config_module.load_config(self.data_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _add(self, name, kind=config_module.ACCOUNT_KIND_UNSET):
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        return config_module.add_account(
            self.config, name, str(path), data_dir=self.data_dir, kind=kind
        )

    def test_default_kind_is_unset(self):
        """모르는 값을 지어내지 않는다.

        기본값을 'project'로 두면 사용자는 그것이 확인된 분류인 줄 안다 -
        `created_by`를 비워 두는 것과 같은 이유다."""

        account = self._add("a")
        self.assertEqual(account.kind, config_module.ACCOUNT_KIND_UNSET)
        self.assertEqual(account.backup_account_id, "")

    def test_add_with_kind(self):
        account = self._add("b", config_module.ACCOUNT_KIND_PROJECT)
        self.assertTrue(account.kind_is_project)
        self.assertFalse(account.kind_is_backup)

    def test_unknown_kind_is_rejected_at_add(self):
        path = self.root / "c"
        path.mkdir()
        with self.assertRaises(config_module.ConfigError):
            config_module.add_account(
                self.config, "c", str(path), data_dir=self.data_dir, kind="nonsense"
            )

    def test_kind_survives_save_and_load(self):
        self._add("proj", config_module.ACCOUNT_KIND_PROJECT)
        self._add("bak", config_module.ACCOUNT_KIND_BACKUP)
        config_module.save_config(self.data_dir, self.config)

        reloaded = config_module.load_config(self.data_dir)
        kinds = {account.name: account.kind for account in reloaded.accounts}
        self.assertEqual(
            kinds,
            {
                "proj": config_module.ACCOUNT_KIND_PROJECT,
                "bak": config_module.ACCOUNT_KIND_BACKUP,
            },
        )

    def test_old_config_without_kind_loads_as_unset(self):
        """이 열이 없던 시절의 config.json도 그대로 열려야 한다."""

        self._add("old")
        config_module.save_config(self.data_dir, self.config)
        path = config_module.config_file(self.data_dir)
        raw = json.loads(path.read_text(encoding="utf-8"))
        for account in raw["accounts"]:
            account.pop("kind", None)
            account.pop("backup_account_id", None)
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        reloaded = config_module.load_config(self.data_dir)
        self.assertEqual(reloaded.accounts[0].kind, config_module.ACCOUNT_KIND_UNSET)

    def test_broken_kind_falls_back_instead_of_raising(self):
        """성격 값이 깨졌다고 앱이 안 열리면 안 된다 (language와 같은 톤)."""

        self._add("weird")
        config_module.save_config(self.data_dir, self.config)
        path = config_module.config_file(self.data_dir)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["accounts"][0]["kind"] = "지어낸값"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        reloaded = config_module.load_config(self.data_dir)
        self.assertEqual(reloaded.accounts[0].kind, config_module.ACCOUNT_KIND_UNSET)

    def test_filters(self):
        project = self._add("p", config_module.ACCOUNT_KIND_PROJECT)
        backup = self._add("b", config_module.ACCOUNT_KIND_BACKUP)
        self._add("u")

        self.assertEqual(
            [a.account_id for a in config_module.project_accounts(self.config)],
            [project.account_id],
        )
        self.assertEqual(
            [a.account_id for a in config_module.backup_accounts(self.config)],
            [backup.account_id],
        )

    def test_disabled_accounts_are_excluded_by_default(self):
        project = self._add("p", config_module.ACCOUNT_KIND_PROJECT)
        project.enabled = False
        self.assertEqual(config_module.project_accounts(self.config), [])
        self.assertEqual(len(config_module.project_accounts(self.config, enabled_only=False)), 1)


class BackupLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.config = config_module.load_config(self.data_dir)
        self.project = self._add("proj", config_module.ACCOUNT_KIND_PROJECT)
        self.backup = self._add("bak", config_module.ACCOUNT_KIND_BACKUP)

    def tearDown(self):
        self.tmp.cleanup()

    def _add(self, name, kind):
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        return config_module.add_account(
            self.config, name, str(path), data_dir=self.data_dir, kind=kind
        )

    def test_link_project_to_backup(self):
        changed = config_module.set_backup_link(
            self.config, self.project.account_id, self.backup.account_id
        )
        self.assertTrue(changed)
        self.assertEqual(self.project.backup_account_id, self.backup.account_id)

    def test_cannot_link_to_a_non_backup_account(self):
        other = self._add("other", config_module.ACCOUNT_KIND_PROJECT)
        with self.assertRaises(config_module.ConfigError):
            config_module.set_backup_link(
                self.config, self.project.account_id, other.account_id
            )

    def test_cannot_link_to_itself(self):
        with self.assertRaises(config_module.ConfigError):
            config_module.set_backup_link(
                self.config, self.project.account_id, self.project.account_id
            )

    def test_changing_kind_away_from_project_clears_the_link(self):
        """'백업 계정의 백업 계정'은 이 모델에 없는 개념이다."""

        config_module.set_backup_link(
            self.config, self.project.account_id, self.backup.account_id
        )
        config_module.set_account_kind(
            self.config, self.project.account_id, config_module.ACCOUNT_KIND_BACKUP
        )
        self.assertEqual(self.project.backup_account_id, "")

    def test_dangling_link_is_cleaned_on_load(self):
        """연결 대상이 삭제된 뒤에도 id가 남아 있으면 나중에 헷갈린다."""

        config_module.set_backup_link(
            self.config, self.project.account_id, self.backup.account_id
        )
        config_module.remove_account(self.config, self.backup.account_id)
        config_module.save_config(self.data_dir, self.config)

        reloaded = config_module.load_config(self.data_dir)
        project = config_module.find_account(reloaded, self.project.account_id)
        self.assertEqual(project.backup_account_id, "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
