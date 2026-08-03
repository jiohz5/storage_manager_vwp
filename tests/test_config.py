import tempfile
import unittest
from pathlib import Path

from smvwp import config as config_module


class LoadSaveConfigTests(unittest.TestCase):
    def test_load_creates_default_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config = config_module.load_config(data_dir)
            self.assertEqual(config.accounts, [])
            self.assertTrue(config_module.config_file(data_dir).exists())

    def test_round_trip_preserves_accounts_and_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            # data_dir과 account_path는 반드시 형제 디렉터리여야 한다 - 데이터
            # 디렉터리가 계정 경로 내부(또는 그 반대)에 있으면 읽기 전용
            # 불변식 위반으로 load_config가 거부한다 (config._guard_read_only_invariant).
            data_dir = Path(tmp) / "sm_data"
            account_path = Path(tmp) / "acct"
            account_path.mkdir()

            config = config_module.load_config(data_dir)
            config_module.add_account(config, "project_a", str(account_path))
            config.settings.collector_interval_seconds = 1800
            config_module.save_config(data_dir, config)

            reloaded = config_module.load_config(data_dir)
            self.assertEqual(len(reloaded.accounts), 1)
            self.assertEqual(reloaded.accounts[0].name, "project_a")
            self.assertEqual(reloaded.settings.collector_interval_seconds, 1800)

    def test_rejects_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config_module.config_file(data_dir).write_text("{not json", encoding="utf-8")
            with self.assertRaises(config_module.ConfigError):
                config_module.load_config(data_dir)


class AddAccountTests(unittest.TestCase):
    def test_rejects_empty_name(self):
        config = config_module.AppConfig(settings=config_module.Settings(), accounts=[])
        with self.assertRaises(config_module.ConfigError):
            config_module.add_account(config, "   ", "/tmp")

    def test_rejects_nonexistent_path(self):
        config = config_module.AppConfig(settings=config_module.Settings(), accounts=[])
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist"
            with self.assertRaises(config_module.ConfigError):
                config_module.add_account(config, "acct", str(missing))

    def test_rejects_duplicate_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            account_path = Path(tmp) / "acct"
            account_path.mkdir()
            config = config_module.AppConfig(settings=config_module.Settings(), accounts=[])
            config_module.add_account(config, "acct1", str(account_path))
            with self.assertRaises(config_module.ConfigError):
                config_module.add_account(config, "acct2", str(account_path))

    def test_assigns_unique_account_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path_a = Path(tmp) / "a"
            path_a.mkdir()
            path_b = Path(tmp) / "b"
            path_b.mkdir()
            config = config_module.AppConfig(settings=config_module.Settings(), accounts=[])
            account_a = config_module.add_account(config, "a", str(path_a))
            account_b = config_module.add_account(config, "b", str(path_b))
            self.assertNotEqual(account_a.account_id, account_b.account_id)


class ReadOnlyInvariantGuardTests(unittest.TestCase):
    """읽기 전용 불변식(paths.assert_not_inside_monitored_paths)이 실제
    config 로드/계정 추가 경로에서 강제되는지 확인한다."""

    def test_add_account_rejects_path_that_would_nest_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitored = Path(tmp) / "user_project_a"
            monitored.mkdir()
            data_dir = monitored / "sm_data"
            data_dir.mkdir()

            config = config_module.AppConfig(settings=config_module.Settings(), accounts=[])
            with self.assertRaises(config_module.ConfigError):
                config_module.add_account(config, "project_a", str(monitored), data_dir=data_dir)

    def test_load_config_rejects_previously_saved_violating_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            monitored = Path(tmp) / "user_project_a"
            monitored.mkdir()
            data_dir = monitored / "sm_data"
            data_dir.mkdir()

            # data_dir 없이 계정을 추가한 뒤 강제로 저장해 "이미 저장된 위반
            # 상태"를 재현한다 (예: 나중에 데이터 디렉터리를 계정 내부로
            # 옮긴 경우).
            config = config_module.AppConfig(settings=config_module.Settings(), accounts=[])
            config_module.add_account(config, "project_a", str(monitored))
            config_module.save_config(data_dir, config)

            with self.assertRaises(config_module.ConfigError):
                config_module.load_config(data_dir)


class RemoveAndFilterAccountTests(unittest.TestCase):
    def test_remove_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            account_path = Path(tmp) / "acct"
            account_path.mkdir()
            config = config_module.AppConfig(settings=config_module.Settings(), accounts=[])
            account = config_module.add_account(config, "acct", str(account_path))
            self.assertTrue(config_module.remove_account(config, account.account_id))
            self.assertEqual(config.accounts, [])
            self.assertFalse(config_module.remove_account(config, account.account_id))

    def test_enabled_accounts_filters_disabled(self):
        account_enabled = config_module.Account(name="on", path="/x", enabled=True)
        account_disabled = config_module.Account(name="off", path="/y", enabled=False)
        config = config_module.AppConfig(
            settings=config_module.Settings(), accounts=[account_enabled, account_disabled]
        )
        result = config_module.enabled_accounts(config)
        self.assertEqual(result, [account_enabled])


if __name__ == "__main__":
    unittest.main()
