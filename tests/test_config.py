import json
import tempfile
import unittest
from pathlib import Path

from storage_manager.config import (
    Account,
    AccountStore,
    ConfigError,
    Settings,
    load_store,
    normalize_account_path,
    save_store,
)


class ConfigTests(unittest.TestCase):
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
