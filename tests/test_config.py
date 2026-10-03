import json
import tempfile
import unittest
from dataclasses import asdict, fields
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module, i18n


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

    def test_a_setting_that_was_removed_does_not_break_an_old_config(self):
        """기능을 걷어 내도 반입 장비의 옛 설정 파일은 그대로 열려야 한다.

        `growth_history_keep_generations` 는 경로별 증감 이력을 걷어 내며 없앤
        설정이다. 남아 있는 키 때문에 프로그램이 안 뜨면 설정 파일을 손으로
        고쳐야 하는데, 폐쇄망 장비에서 그것은 사람을 부르는 일이다."""

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config_module.load_config(data_dir)   # 기본 파일을 만든다
            path = config_module.config_file(data_dir)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["settings"]["growth_history_keep_generations"] = 60
            path.write_text(json.dumps(raw), encoding="utf-8")

            config = config_module.load_config(data_dir)
            self.assertFalse(hasattr(config.settings, "growth_history_keep_generations"))
            # 다음에 저장하면 낡은 키도 사라진다.
            config_module.save_config(data_dir, config)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("growth_history_keep_generations", saved["settings"])

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


class SettingsCheckTests(unittest.TestCase):
    """손으로 고친 config.json 이 잘못됐을 때 - 무엇이 틀렸는지 말하고 멈춘다."""

    def _check(self, **changes):
        raw = asdict(config_module.Settings())
        raw.update(changes)
        return config_module._settings_from_dict(raw)

    def test_defaults_pass(self):
        self._check()

    def test_every_number_setting_has_a_range(self):
        """숫자 설정을 새로 더하면 범위표에 한 줄을 더해야 한다.

        빠뜨린 것이 셋 있었다. `sample_retention_days` 에 0 이 들어가면 수집할
        때마다 df 이력이 전부 지워지고, `immediate_notify_pct` 가 음수면 경고
        이상인 계정 전부가 매 수집마다 울렸다. 표에 없어도 되는 것은 두 값의
        관계로만 보는 넷뿐이다."""

        ranged = {name for name, *_ in config_module._NUMBER_RANGES}
        related = {"full_warn_hours", "full_critical_hours", "trend_short_days", "trend_long_days"}
        numbers = {
            spec.name for spec in fields(config_module.Settings)
            if isinstance(spec.default, (int, float)) and not isinstance(spec.default, bool)
        }
        self.assertEqual(numbers - ranged - related, set())
        self.assertEqual(ranged - numbers, set())   # 표에 오타가 없다

    def test_each_range_holds_at_its_edges(self):
        for name, low, high, _note in config_module._NUMBER_RANGES:
            with self.subTest(name=name):
                self._check(**{name: low})
                with self.assertRaises(config_module.ConfigError) as caught:
                    self._check(**{name: low - 1})
                self.assertIn(name, str(caught.exception))
                if high is not None:
                    self._check(**{name: high})
                    with self.assertRaises(config_module.ConfigError):
                        self._check(**{name: high + 1})

    def test_quoted_number_is_named_instead_of_crashing(self):
        """예전에는 `"900"` 이 비교에서 TypeError 를 내 프로그램이 그냥 죽었다."""

        with self.assertRaises(config_module.ConfigError) as caught:
            self._check(collector_interval_seconds="900")
        self.assertIn("collector_interval_seconds", str(caught.exception))
        self.assertIn("따옴표", str(caught.exception))

    def test_quoted_false_does_not_turn_a_feature_on(self):
        """`"false"` 는 글자라서 참으로 읽혔다 - 끄려던 기능을 켰다."""

        with self.assertRaises(config_module.ConfigError) as caught:
            self._check(gui_auto_nightly_scan="false")
        self.assertIn("gui_auto_nightly_scan", str(caught.exception))

    def test_zero_and_one_are_read_as_off_and_on(self):
        settings = self._check(growth_alert_enabled=0, freshness_enabled=1)
        self.assertIs(settings.growth_alert_enabled, False)
        self.assertIs(settings.freshness_enabled, True)

    def test_true_is_not_a_number(self):
        with self.assertRaises(config_module.ConfigError):
            self._check(checkpoint_workers=True)

    def test_relations_are_still_checked(self):
        with self.assertRaises(config_module.ConfigError):
            self._check(full_critical_hours=6, full_warn_hours=6)
        with self.assertRaises(config_module.ConfigError):
            self._check(trend_short_days=30, trend_long_days=30)

    def test_wrong_language_falls_back_instead_of_stopping(self):
        self.assertEqual(self._check(language="fr").language, i18n.DEFAULT_LANGUAGE)

    def test_load_names_the_file_to_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            config_module.load_config(data_dir)
            path = config_module.config_file(data_dir)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["settings"]["sample_retention_days"] = 0
            path.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaises(config_module.ConfigError) as caught:
                config_module.load_config(data_dir)
        self.assertIn(str(path), str(caught.exception))
        self.assertIn("sample_retention_days", str(caught.exception))


class AccountOwnershipTests(unittest.TestCase):
    """계정을 누가 언제 넣었는지 남긴다.

    파트별 담당자가 계정을 나눠 관리하는 형태라, 남이 등록한 계정을 볼 때
    "이거 누가 넣었지"를 물어보지 않아도 되어야 한다. 인증 수단이 아니라
    메모다 - OS 사용자명은 위조할 수 있고 앱은 그것을 검증하지 않는다.
    """

    def test_new_account_records_creator_and_date(self):
        with patch("smvwp.config._current_user", return_value="hong"):
            account = config_module.Account(name="project_a", path="/user/project_a")
        self.assertEqual(account.created_by, "hong")
        self.assertTrue(account.created_at)

    def test_survives_round_trip_through_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            watched = Path(tmp) / "watched"
            watched.mkdir(parents=True)
            cfg = config_module.AppConfig(settings=config_module.Settings(), accounts=[])
            with patch("smvwp.config._current_user", return_value="hong"):
                config_module.add_account(cfg, "project_a", str(watched))
            config_module.save_config(data_dir, cfg)

            reloaded = config_module.load_config(data_dir)
        self.assertEqual(reloaded.accounts[0].created_by, "hong")

    def test_old_config_without_creator_still_loads(self):
        """기존 설치의 config.json에는 이 필드가 없다 - 열리기만 하면 된다."""

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            raw = {
                "settings": {},
                "accounts": [
                    {"name": "old", "path": "/user/old", "account_id": "abc",
                     "created_at": "2026-01-01T00:00:00+00:00"}
                ],
            }
            config_module.config_file(data_dir).write_text(
                json.dumps(raw, ensure_ascii=False), encoding="utf-8"
            )
            loaded = config_module.load_config(data_dir)

        self.assertEqual(loaded.accounts[0].name, "old")
        # 모르는 값을 지어내지 않는다 - 빈 값이면 화면에서 '-'로 보인다.
        self.assertFalse(loaded.accounts[0].created_by)


if __name__ == "__main__":
    unittest.main()
