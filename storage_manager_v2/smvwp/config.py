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

from . import i18n, paths


class ConfigError(ValueError):
    pass


DEFAULT_COLLECTOR_INTERVAL_SECONDS = 15 * 60  # 15분 (REBUILD_CONCEPT.md 7절)
DEFAULT_NOTIFICATION_COOLDOWN_MINUTES = 60
DEFAULT_NOTIFICATION_MIN_TIER = "warn"
DEFAULT_SAMPLE_RETENTION_DAYS = 90

# 야간 상세 스캔(REBUILD_CONCEPT.md 8절 1번) 관련 기본값. CONCEPT.md 3절의
# 22:00~06:00 시간창 정책을 원칙만 계승해 새로 구현한다.
DEFAULT_DETAIL_SCAN_WINDOW_START_HOUR = 22
DEFAULT_DETAIL_SCAN_WINDOW_END_HOUR = 6
DEFAULT_DETAIL_TASK_TIMEOUT_SECONDS = 15 * 60  # 디렉터리 하나당 du/find 예산
DEFAULT_DETAIL_SCAN_KEEP_GENERATIONS = 2
DEFAULT_DETAIL_SCAN_TOP_N = 15
DEFAULT_ACTIVITY_INITIAL_LOOKBACK_DAYS = 2

# 표시 언어 (i18n). 저장은 언어 코드로만 하고 라벨은 그때그때 만든다.
DEFAULT_LANGUAGE = i18n.DEFAULT_LANGUAGE

# 알림 전송 방식. 기본은 폐쇄망에서 가장 안전한 로컬 파일 outbox.
NOTIFY_MODE_OUTBOX = "outbox"
NOTIFY_MODE_COMMAND = "command"
NOTIFY_MODE_WEBHOOK = "webhook"
NOTIFY_MODE_DISABLED = "disabled"
NOTIFICATION_MODES = (
    NOTIFY_MODE_OUTBOX,
    NOTIFY_MODE_COMMAND,
    NOTIFY_MODE_WEBHOOK,
    NOTIFY_MODE_DISABLED,
)
DEFAULT_NOTIFICATION_MODE = NOTIFY_MODE_OUTBOX


@dataclass
class Settings:
    collector_interval_seconds: int = DEFAULT_COLLECTOR_INTERVAL_SECONDS
    notification_cooldown_minutes: int = DEFAULT_NOTIFICATION_COOLDOWN_MINUTES
    notification_min_tier: str = DEFAULT_NOTIFICATION_MIN_TIER
    sample_retention_days: int = DEFAULT_SAMPLE_RETENTION_DAYS
    df_timeout_seconds: int = 10
    detail_scan_window_start_hour: int = DEFAULT_DETAIL_SCAN_WINDOW_START_HOUR
    detail_scan_window_end_hour: int = DEFAULT_DETAIL_SCAN_WINDOW_END_HOUR
    detail_task_timeout_seconds: int = DEFAULT_DETAIL_TASK_TIMEOUT_SECONDS
    detail_scan_keep_generations: int = DEFAULT_DETAIL_SCAN_KEEP_GENERATIONS
    detail_scan_top_n: int = DEFAULT_DETAIL_SCAN_TOP_N
    activity_initial_lookback_days: int = DEFAULT_ACTIVITY_INITIAL_LOOKBACK_DAYS
    language: str = DEFAULT_LANGUAGE
    # 알림 채널. command/webhook은 사내 endpoint가 있을 때만 쓰고, 설정하지
    # 않으면 outbox 그대로 동작한다.
    notification_mode: str = DEFAULT_NOTIFICATION_MODE
    notification_command: List[str] = field(default_factory=list)
    notification_webhook_url: str = ""
    notification_timeout_seconds: int = 10
    # quota 조회 argv (shell 없이 실행). 비어 있으면 quota 열은 '-'로 남는다.
    quota_command: List[str] = field(default_factory=list)
    # 보고서 / 정리 후보 기준
    weekly_report_weekday: int = 4  # 월=0 ... 금=4 (Python weekday 기준)
    cleanup_min_size_kb: int = 100 * 1024 * 1024  # 100GB
    cleanup_min_age_days: int = 30
    cleanup_idle_days: int = 30
    report_retention_days: int = 365
    # 검색 인덱스 (계정별 opt-in, 기본 꺼짐)
    search_result_limit: int = 500


@dataclass
class Account:
    name: str
    path: str
    enabled: bool = True
    account_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # 검색 인덱싱은 계정별 opt-in (기본 꺼짐) - 이름 인덱스는 별도 DB를 꽤
    # 차지할 수 있으므로 필요한 계정만 켠다.
    search_indexing: bool = False


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
    if not 0 <= settings.detail_scan_window_start_hour <= 23:
        raise ConfigError("detail_scan_window_start_hour는 0~23이어야 합니다")
    if not 0 <= settings.detail_scan_window_end_hour <= 23:
        raise ConfigError("detail_scan_window_end_hour는 0~23이어야 합니다")
    if settings.detail_task_timeout_seconds < 10:
        raise ConfigError("detail_task_timeout_seconds는 10 이상이어야 합니다")
    if settings.detail_scan_keep_generations < 1:
        raise ConfigError("detail_scan_keep_generations는 1 이상이어야 합니다")
    if not 1 <= settings.detail_scan_top_n <= 200:
        raise ConfigError("detail_scan_top_n은 1~200이어야 합니다")
    if settings.activity_initial_lookback_days < 1:
        raise ConfigError("activity_initial_lookback_days는 1 이상이어야 합니다")
    if not i18n.is_supported(settings.language):
        # 언어는 잘못돼도 앱을 막지 않고 기본값으로 되돌린다 - 표시 문제일 뿐
        # 데이터 무결성 문제가 아니기 때문.
        settings.language = i18n.DEFAULT_LANGUAGE
    if settings.notification_mode not in NOTIFICATION_MODES:
        raise ConfigError(
            f"notification_mode는 {', '.join(NOTIFICATION_MODES)} 중 하나여야 합니다"
        )
    if not isinstance(settings.notification_command, list) or not all(
        isinstance(part, str) for part in settings.notification_command
    ):
        raise ConfigError("notification_command는 문자열 배열이어야 합니다")
    if settings.notification_mode == NOTIFY_MODE_COMMAND and not settings.notification_command:
        raise ConfigError("notification_mode가 command면 notification_command가 필요합니다")
    if settings.notification_mode == NOTIFY_MODE_WEBHOOK and not settings.notification_webhook_url:
        raise ConfigError("notification_mode가 webhook이면 notification_webhook_url이 필요합니다")
    if not isinstance(settings.quota_command, list) or not all(
        isinstance(part, str) for part in settings.quota_command
    ):
        raise ConfigError("quota_command는 문자열 배열이어야 합니다")
    if settings.notification_timeout_seconds < 1:
        raise ConfigError("notification_timeout_seconds는 1 이상이어야 합니다")
    if not 0 <= settings.weekly_report_weekday <= 6:
        raise ConfigError("weekly_report_weekday는 0(월)~6(일)이어야 합니다")
    if settings.cleanup_min_size_kb < 0:
        raise ConfigError("cleanup_min_size_kb는 음수일 수 없습니다")
    if settings.cleanup_min_age_days < 0 or settings.cleanup_idle_days < 0:
        raise ConfigError("cleanup 기간 설정은 음수일 수 없습니다")
    if settings.report_retention_days < 1:
        raise ConfigError("report_retention_days는 1 이상이어야 합니다")
    if not 1 <= settings.search_result_limit <= 10000:
        raise ConfigError("search_result_limit은 1~10000이어야 합니다")
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
    _guard_read_only_invariant(data_dir, config)
    if changed:
        save_config(data_dir, config)
    return config


def _guard_read_only_invariant(data_dir: Path, config: AppConfig) -> None:
    """데이터 디렉터리가 모니터링 대상 계정 경로와 겹치지 않는지 매번 확인한다.

    CONCEPT.md 1절의 읽기 전용 불변식(데이터 디렉터리 외에는 절대 쓰지 않음)을
    설정을 불러올 때마다 강제한다 - GUI, cron 수집기, 야간 스캔이 모두
    `load_config`를 거치므로 세 경로 전부에서 이 점검이 실행된다.
    """

    try:
        paths.assert_not_inside_monitored_paths(
            data_dir, [account.path for account in config.accounts]
        )
    except paths.DataDirError as exc:
        raise ConfigError(str(exc)) from exc


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


def add_account(
    config: AppConfig,
    name: str,
    path: str,
    require_exists: bool = True,
    data_dir: Optional[Path] = None,
) -> Account:
    """계정을 추가한다. `data_dir`을 넘기면 그 자리에서 바로 읽기 전용
    불변식(데이터 디렉터리와 겹치지 않음)을 확인해, 저장 후 다음 실행에서야
    발견되는 것보다 즉시 사용자에게 알려준다."""

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

    if data_dir is not None:
        try:
            paths.assert_not_inside_monitored_paths(data_dir, [str(resolved)])
        except paths.DataDirError as exc:
            raise ConfigError(str(exc)) from exc

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
