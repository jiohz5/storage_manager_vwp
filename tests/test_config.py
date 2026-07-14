import json
import tempfile
import unittest
from pathlib import Path

from storage_manager.config import (
    Account,
    AccountStore,
    ConfigError,
    Settings,
    default_data_dir,
    load_store,
    normalize_account_path,
    save_store,
)


class ConfigTests(unittest.TestCase):
    def test_default_data_dir_never_falls_back_to_source_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ConfigError, "No global data directory"):
                default_data_dir(
                    root / "source",
                    home=root / "home",
                    environ={},
                )
            self.assertFalse((root / "source" / "data").exists())

    def test_invalid_json_error_is_distinct_from_path_error(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            (data_dir / "accounts.json").write_text("{broken", encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "Invalid JSON.*line 1"):
                load_store(data_dir)

    def test_non_directory_data_path_has_writable_path_guidance(self):
        with tempfile.TemporaryDirectory() as temp:
            data_path = Path(temp) / "state"
            data_path.write_text("not a directory", encoding="utf-8")

            with self.assertRaisesRegex(ConfigError, "writable data directory"):
                load_store(data_path)

    def test_capacity_settings_defaults_and_validation(self):
        settings = Settings()
        self.assertEqual(settings.capacity_sample_days, 30)
        self.assertEqual(settings.rapid_growth_gb, 100)
        self.assertEqual(settings.forecast_alert_hours, 6)
        self.assertEqual(settings.forecast_emergency_hours, 2)
        self.assertEqual(settings.capacity_stale_minutes, 45)
        self.assertEqual(settings.popup_backlog_days, 7)
        self.assertEqual(settings.data_size_warning_mb, 500)

        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            (data_dir / "accounts.json").write_text(
                json.dumps(
                    {
                        "settings": {"capacity_sample_days": 0},
                        "accounts": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_store(data_dir)

            (data_dir / "accounts.json").write_text(
                json.dumps(
                    {
                        "settings": {"data_size_warning_mb": 0},
                        "accounts": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_store(data_dir)

    def test_account_path_must_be_directly_below_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "user"
            account = root / "project_a"
            nested = account / "work"
            outside = Path(temp) / "outside"
            nested.mkdir(parents=True)
            outside.mkdir()

            self.assertEqual(
                normalize_account_path("project_a", str(root)),
                str(account.resolve()),
            )
            with self.assertRaises(ConfigError):
                normalize_account_path(str(root), str(root))
            with self.assertRaises(ConfigError):
                normalize_account_path(str(nested), str(root))
            with self.assertRaises(ConfigError):
                normalize_account_path(str(outside), str(root))

    def test_store_round_trip_preserves_account_id(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            account = Account("project_a", "/user/project_a")
            store = AccountStore(Settings(), [account])
            save_store(data_dir, store)

            loaded = load_store(data_dir)
            self.assertEqual(loaded.accounts[0].account_id, account.account_id)
            self.assertEqual(json.loads((data_dir / "accounts.json").read_text())["accounts"][0]["name"], "project_a")

    def test_search_index_flag_defaults_off_and_round_trips(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            (data_dir / "accounts.json").write_text(
                json.dumps(
                    {
                        "settings": {},
                        "accounts": [
                            {
                                "name": "legacy",
                                "path": "/user/legacy",
                                "enabled": True,
                                "account_id": "legacy-id",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            store = load_store(data_dir)
            self.assertFalse(store.accounts[0].search_enabled)
            store.accounts[0].search_enabled = True
            save_store(data_dir, store)
            self.assertTrue(load_store(data_dir).accounts[0].search_enabled)

    def test_legacy_store_persists_generated_account_id(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            (data_dir / "accounts.json").write_text(
                json.dumps(
                    {
                        "settings": {},
                        "accounts": [
                            {"name": "legacy", "path": "/user/legacy", "enabled": True}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            first = load_store(data_dir).accounts[0].account_id
            second = load_store(data_dir).accounts[0].account_id
            self.assertEqual(first, second)

    def test_legacy_root_is_migrated_to_allowed_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            legacy = {
                "settings": {"monitored_root": "/user"},
                "accounts": [],
            }
            (data_dir / "accounts.json").write_text(
                json.dumps(legacy),
                encoding="utf-8",
            )
            store = load_store(data_dir)
            saved = json.loads((data_dir / "accounts.json").read_text(encoding="utf-8"))
            self.assertEqual(store.settings.monitored_roots, ["/user"])
            self.assertEqual(saved["settings"]["monitored_roots"], ["/user"])
            self.assertNotIn("monitored_root", saved["settings"])

    def test_absolute_path_can_match_any_allowed_root(self):
        with tempfile.TemporaryDirectory() as temp:
            first_root = Path(temp) / "first"
            second_root = Path(temp) / "second"
            account = second_root / "project_b"
            first_root.mkdir()
            account.mkdir(parents=True)
            normalized = normalize_account_path(
                str(account),
                [str(first_root), str(second_root)],
            )
            self.assertEqual(normalized, str(account.resolve()))


if __name__ == "__main__":
    unittest.main()
