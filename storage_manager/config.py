from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Union

from storage_manager.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES


DEFAULT_THRESHOLD = 95
DEFAULT_HISTORY_DAYS = 365
DEFAULT_MONITORED_ROOT = "/user"


class ConfigError(ValueError):
    pass


@dataclass
class Settings:
    alert_threshold: int = DEFAULT_THRESHOLD
    history_days: int = DEFAULT_HISTORY_DAYS
    monitored_roots: List[str] = field(default_factory=lambda: [DEFAULT_MONITORED_ROOT])
    refresh_seconds: int = 300
    df_timeout_seconds: int = 15
    detail_scan_timeout_seconds: int = 3600
    detail_task_timeout_seconds: int = 900
    nightly_detail_budget_seconds: int = 28800
    detail_top_n: int = 15
    weekly_report_weekday: int = 4
    language: str = DEFAULT_LANGUAGE
    scan_window_start_hour: int = 22
    scan_window_end_hour: int = 6
    scan_safety_minutes: int = 15
    anomaly_multiplier: float = 3.0
    anomaly_min_growth_gb: int = 100
    freshness_warning_hours: int = 30
    quota_command: List[str] = field(default_factory=list)
    quota_timeout_seconds: int = 10
    notification_mode: str = "outbox"
    notification_command: List[str] = field(default_factory=list)
    notification_webhook_url: str = ""
    notification_timeout_seconds: int = 10
    notification_min_level: str = "warning"
    notification_cooldown_hours: int = 12
    cleanup_inactive_days: int = 30
    cleanup_min_size_gb: int = 100


@dataclass
class Account:
    name: str
    path: str
    enabled: bool = True
    account_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class AccountStore:
    settings: Settings
    accounts: List[Account]


def ensure_data_dir(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    if not data_dir.is_dir():
        raise ConfigError(f"Data path is not a directory: {data_dir}")
    return data_dir


def accounts_file(data_dir: Path) -> Path:
    return data_dir / "accounts.json"


def db_file(data_dir: Path) -> Path:
    return data_dir / "storage_manager.db"


def reports_dir(data_dir: Path) -> Path:
    path = data_dir / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def lock_file(data_dir: Path) -> Path:
    return data_dir / "nightly_scan.lock"


def default_data_dir(app_dir: Path) -> Path:
    configured = os.environ.get("STORAGE_MANAGER_DATA_DIR")
    return Path(configured).expanduser() if configured else app_dir / "data"


def normalize_account_path(
    value: str,
    monitored_roots: Union[str, Sequence[str]] = DEFAULT_MONITORED_ROOT,
    require_exists: bool = True,
) -> str:
    root_values = [monitored_roots] if isinstance(monitored_roots, str) else list(monitored_roots)
    if not root_values:
        raise ConfigError("At least one monitored root is required")
    roots = [
        Path(root).expanduser().resolve(strict=require_exists)
        for root in root_values
    ]
    entered = Path(value).expanduser()
    candidate = entered if entered.is_absolute() else roots[0] / entered
    resolved = candidate.resolve(strict=require_exists)

    matched_root = None
    for root in roots:
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        if len(relative.parts) == 1:
            matched_root = root
            break
    if matched_root is None:
        allowed = ", ".join(str(root) for root in roots)
        raise ConfigError(
            f"Use one direct account directory below an allowed root ({allowed}): {value}"
        )
    if require_exists and not resolved.is_dir():
        raise ConfigError(f"Account path is not a directory: {resolved}")
    if require_exists and not os.access(str(resolved), os.R_OK | os.X_OK):
        raise ConfigError(f"Account path is not readable: {resolved}")
    return str(resolved)


def _settings_from_dict(raw: dict) -> Settings:
    raw = dict(raw)
    legacy_root = raw.pop("monitored_root", None)
    if "monitored_roots" not in raw and legacy_root:
        raw["monitored_roots"] = [legacy_root]
    if isinstance(raw.get("monitored_roots"), str):
        raw["monitored_roots"] = [raw["monitored_roots"]]
    known = Settings.__dataclass_fields__
    values = {key: value for key, value in raw.items() if key in known}
    settings = Settings(**values)
    if not 50 <= settings.alert_threshold <= 100:
        raise ConfigError("alert_threshold must be between 50 and 100")
    if not 30 <= settings.history_days <= 3660:
        raise ConfigError("history_days must be between 30 and 3660")
    if settings.refresh_seconds < 30:
        raise ConfigError("refresh_seconds must be at least 30")
    if settings.df_timeout_seconds < 1:
        raise ConfigError("df_timeout_seconds must be positive")
    if settings.detail_scan_timeout_seconds < 60:
        raise ConfigError("detail_scan_timeout_seconds must be at least 60")
    if settings.detail_task_timeout_seconds < 30:
        raise ConfigError("detail_task_timeout_seconds must be at least 30")
    if settings.nightly_detail_budget_seconds < 300:
        raise ConfigError("nightly_detail_budget_seconds must be at least 300")
    if not 1 <= settings.detail_top_n <= 100:
        raise ConfigError("detail_top_n must be between 1 and 100")
    if not 0 <= settings.weekly_report_weekday <= 6:
        raise ConfigError("weekly_report_weekday must be between 0 and 6")
    if not settings.monitored_roots or not all(
        isinstance(root, str) and root.strip() for root in settings.monitored_roots
    ):
        raise ConfigError("monitored_roots must contain at least one path")
    if settings.language not in SUPPORTED_LANGUAGES:
        raise ConfigError("language must be 'ko' or 'en'")
    if not 0 <= settings.scan_window_start_hour <= 23:
        raise ConfigError("scan_window_start_hour must be between 0 and 23")
    if not 0 <= settings.scan_window_end_hour <= 23:
        raise ConfigError("scan_window_end_hour must be between 0 and 23")
    if not 0 <= settings.scan_safety_minutes <= 120:
        raise ConfigError("scan_safety_minutes must be between 0 and 120")
    if settings.anomaly_multiplier < 1.0:
        raise ConfigError("anomaly_multiplier must be at least 1.0")
    if settings.anomaly_min_growth_gb < 1:
        raise ConfigError("anomaly_min_growth_gb must be positive")
    if settings.freshness_warning_hours < 1:
        raise ConfigError("freshness_warning_hours must be positive")
    if not isinstance(settings.quota_command, list) or not all(
        isinstance(value, str) and value for value in settings.quota_command
    ):
        raise ConfigError("quota_command must be a list of non-empty arguments")
    if settings.quota_timeout_seconds < 1:
        raise ConfigError("quota_timeout_seconds must be positive")
    if settings.notification_mode not in {"disabled", "outbox", "command", "webhook"}:
        raise ConfigError("notification_mode must be disabled, outbox, command, or webhook")
    if not isinstance(settings.notification_command, list) or not all(
        isinstance(value, str) and value for value in settings.notification_command
    ):
        raise ConfigError("notification_command must be a list of non-empty arguments")
    if settings.notification_timeout_seconds < 1:
        raise ConfigError("notification_timeout_seconds must be positive")
    if settings.notification_min_level not in {"warning", "alert"}:
        raise ConfigError("notification_min_level must be warning or alert")
    if settings.notification_cooldown_hours < 0:
        raise ConfigError("notification_cooldown_hours cannot be negative")
    if settings.cleanup_inactive_days < 7:
        raise ConfigError("cleanup_inactive_days must be at least 7")
    if settings.cleanup_min_size_gb < 1:
        raise ConfigError("cleanup_min_size_gb must be positive")
    return settings


def load_store(data_dir: Path) -> AccountStore:
    ensure_data_dir(data_dir)
    path = accounts_file(data_dir)
    if not path.exists():
        store = AccountStore(settings=Settings(), accounts=[])
        save_store(data_dir, store)
        return store

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw_settings = raw.get("settings", {})
        settings = _settings_from_dict(raw_settings)
        settings_changed = (
            "monitored_root" in raw_settings
            and "monitored_roots" not in raw_settings
        )
        raw_accounts = raw.get("accounts", [])
        changed = settings_changed or any(
            not account.get("account_id") for account in raw_accounts
        )
        accounts = [Account(**account) for account in raw_accounts]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ConfigError(f"Cannot load {path}: {exc}") from exc

    seen_ids = set()
    for account in accounts:
        if not account.account_id or account.account_id in seen_ids:
            account.account_id = uuid.uuid4().hex
            changed = True
        seen_ids.add(account.account_id)

    store = AccountStore(settings=settings, accounts=accounts)
    if changed:
        save_store(data_dir, store)
    return store


def save_store(data_dir: Path, store: AccountStore) -> None:
    ensure_data_dir(data_dir)
    path = accounts_file(data_dir)
    temp_path = path.with_suffix(".json.tmp")
    payload = {
        "settings": asdict(store.settings),
        "accounts": [asdict(account) for account in store.accounts],
    }
    encoded = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"

    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(path))
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def find_account(store: AccountStore, account_id: str) -> Optional[Account]:
    return next((account for account in store.accounts if account.account_id == account_id), None)
