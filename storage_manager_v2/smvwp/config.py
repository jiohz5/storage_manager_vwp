"""설정 파일(JSON) 로드/저장 및 계정 등록.

설정 포맷은 REBUILD_CONCEPT.md 4절 결정에 따라 JSON (stdlib만으로 충분,
`tomllib`은 검토 후 채택하지 않음). 데이터 디렉터리 안의 `config.json` 하나에
전역 설정(Settings)과 계정 목록(Account)을 함께 담는다.

쓰기는 항상 임시 파일 작성 후 `os.replace`로 교체하는 원자적 방식을 쓴다 —
쓰다가 중단돼도 기존 config.json이 깨지지 않도록 하기 위함 (CONCEPT.md의
"중단 시점이 언제든 재실행이 항상 안전해야 한다"는 불변식을 설정 파일에도
동일하게 적용).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import paths


class ConfigError(ValueError):
    pass


DEFAULT_COLLECTOR_INTERVAL_SECONDS = 15 * 60  # 15분 (REBUILD_CONCEPT.md 7절)
DEFAULT_NOTIFICATION_COOLDOWN_MINUTES = 60
DEFAULT_NOTIFICATION_MIN_TIER = "warn"
DEFAULT_SAMPLE_RETENTION_DAYS = 90


@dataclass
class Settings:
    collector_interval_seconds: int = DEFAULT_COLLECTOR_INTERVAL_SECONDS
    notification_cooldown_minutes: int = DEFAULT_NOTIFICATION_COOLDOWN_MINUTES
    notification_min_tier: str = DEFAULT_NOTIFICATION_MIN_TIER
    sample_retention_days: int = DEFAULT_SAMPLE_RETENTION_DAYS
    df_timeout_seconds: int = 10


@dataclass
class Account:
    name: str
    path: str
    enabled: bool = True
    account_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class AppConfig:
    settings: Settings
    accounts: List[Account]


def config_file(data_dir: Path) -> Path:
    return data_dir / "config.json"


def _settings_from_dict(raw: dict) -> Settings:
    known = Settings.__dataclass_fields__
    values = {key: value for key, value in raw.items() if key in known}
    settings = Settings(**values)
    if settings.collector_interval_seconds < 60:
        raise ConfigError("collector_interval_seconds는 60 이상이어야 합니다")
    if settings.notification_cooldown_minutes < 0:
        raise ConfigError("notification_cooldown_minutes는 음수일 수 없습니다")
    from .tiers import LABELS

    if settings.notification_min_tier not in LABELS:
        raise ConfigError("notification_min_tier가 올바른 등급이 아닙니다")
    if settings.df_timeout_seconds < 1:
        raise ConfigError("df_timeout_seconds는 1 이상이어야 합니다")
    return settings


def load_config(data_dir: Path) -> AppConfig:
    """설정을 읽는다. 파일이 없으면 기본값으로 새로 만들어 저장한다."""

    paths.ensure_writable(data_dir)
    file_path = config_file(data_dir)
    if not file_path.exists():
        config = AppConfig(settings=Settings(), accounts=[])
        save_config(data_dir, config)
        return config

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{file_path}의 JSON 형식이 올바르지 않습니다: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{file_path}를 읽을 수 없습니다: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{file_path}의 최상위 요소는 JSON 객체여야 합니다")

    settings = _settings_from_dict(raw.get("settings", {}))
    accounts = []
    seen_ids = set()
    changed = False
    for item in raw.get("accounts", []):
        account = Account(**item)
        if not account.account_id or account.account_id in seen_ids:
            account.account_id = uuid.uuid4().hex
            changed = True
        seen_ids.add(account.account_id)
        accounts.append(account)

    config = AppConfig(settings=settings, accounts=accounts)
    if changed:
        save_config(data_dir, config)
    return config


def save_config(data_dir: Path, config: AppConfig) -> None:
    paths.ensure_writable(data_dir)
    file_path = config_file(data_dir)
    temp_path = file_path.with_suffix(".json.tmp")
    payload = {
        "settings": asdict(config.settings),
        "accounts": [asdict(account) for account in config.accounts],
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp_path), str(file_path))
    except OSError as exc:
        raise ConfigError(f"{file_path}에 쓸 수 없습니다: {exc}") from exc
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def add_account(config: AppConfig, name: str, path: str, require_exists: bool = True) -> Account:
    name = name.strip()
    if not name:
        raise ConfigError("계정 이름을 입력하세요")

    resolved = Path(path).expanduser()
    if require_exists:
        if not resolved.exists():
            raise ConfigError(f"경로가 존재하지 않습니다: {resolved}")
        if not resolved.is_dir():
            raise ConfigError(f"경로가 디렉터리가 아닙니다: {resolved}")
        if not os.access(str(resolved), os.R_OK):
            raise ConfigError(f"경로를 읽을 수 없습니다: {resolved}")
        resolved = resolved.resolve()

    if any(existing.path == str(resolved) for existing in config.accounts):
        raise ConfigError(f"이미 등록된 경로입니다: {resolved}")

    account = Account(name=name, path=str(resolved))
    config.accounts.append(account)
    return account


def remove_account(config: AppConfig, account_id: str) -> bool:
    before = len(config.accounts)
    config.accounts = [a for a in config.accounts if a.account_id != account_id]
    return len(config.accounts) != before


def find_account(config: AppConfig, account_id: str) -> Optional[Account]:
    return next((a for a in config.accounts if a.account_id == account_id), None)


def enabled_accounts(config: AppConfig) -> List[Account]:
    return [a for a in config.accounts if a.enabled]
