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
            data_dir = Path(tmp)
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
