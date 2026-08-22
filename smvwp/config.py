"""설정 파일(JSON) 로드/저장 및 계정 등록.

설정 포맷은 DESIGN.md 2부 4절 결정에 따라 JSON (stdlib만으로 충분,
`tomllib`은 검토 후 채택하지 않음). 데이터 디렉터리 안의 `config.json` 하나에
전역 설정(Settings)과 계정 목록(Account)을 함께 담는다.

쓰기는 항상 임시 파일 작성 후 `os.replace`로 교체하는 원자적 방식을 쓴다 —
쓰다가 중단돼도 기존 config.json이 깨지지 않도록 하기 위함 (DESIGN.md 1부의
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


DEFAULT_COLLECTOR_INTERVAL_SECONDS = 15 * 60  # 15분 (DESIGN.md 2부 7절)
DEFAULT_NOTIFICATION_COOLDOWN_MINUTES = 60
DEFAULT_NOTIFICATION_MIN_TIER = "warn"
DEFAULT_SAMPLE_RETENTION_DAYS = 90

# 야간 상세 스캔(DESIGN.md 2부 8절 1번) 관련 기본값. DESIGN.md 1부 3절의
# 22:00~06:00 시간창 정책을 원칙만 계승해 새로 구현한다.
DEFAULT_DETAIL_SCAN_WINDOW_START_HOUR = 22
DEFAULT_DETAIL_SCAN_WINDOW_END_HOUR = 6
DEFAULT_DETAIL_TASK_TIMEOUT_SECONDS = 15 * 60  # 디렉터리 하나당 du/find 예산
DEFAULT_DETAIL_SCAN_KEEP_GENERATIONS = 2
DEFAULT_DETAIL_SCAN_TOP_N = 15
DEFAULT_DETAIL_SCAN_MAX_DEPTH = 3
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

# -- 계정 성격 -------------------------------------------------------------
#
# 프로젝트 계정과 백업 계정은 **무엇이 정상인가가 반대다.** 프로젝트 계정은
# 과제가 생겼다 끝나면서 오르내리는 것이 정상이고, 백업 계정은 단조 증가가
# 정상이다. 같은 눈으로 보면 둘 중 하나는 반드시 오독된다 - 백업 계정이 계속
# 는다고 놀라거나, 프로젝트 계정이 안 준다고 방치하거나.
#
# 지금 이 값이 실제로 갈라 놓는 것은 두 가지다:
#   1. 과제 생성(`*_run_*`) 감지 - 프로젝트 계정에서만 본다
#   2. (예정) 프로젝트 계정의 BACKUP 하위가 연결된 백업 계정에 잘 들어갔는지
#
# 기존 설정에는 이 값이 없으므로 기본은 `unset`이다. 여기서 임의로 'project'를
# 기본값으로 두지 않는 이유는 `created_by`와 같다 - **모르는 값을 지어내면
# 사용자는 그것이 확인된 값인 줄 안다.**
ACCOUNT_KIND_UNSET = "unset"
ACCOUNT_KIND_PROJECT = "project"
ACCOUNT_KIND_BACKUP = "backup"
ACCOUNT_KINDS = (ACCOUNT_KIND_UNSET, ACCOUNT_KIND_PROJECT, ACCOUNT_KIND_BACKUP)

# 야간 스캔을 계정 몇 개까지 동시에 돌릴지. 기본 1 = 지금까지와 똑같은 직렬.
#
# 1보다 크게 두는 것은 **상시 운용이 아니라 부하 실측을 위한 실험 장치**다.
# 계정 대부분이 같은 파일시스템에 있으면 병렬은 seek 경합만 늘려 오히려
# 느려질 수 있고, nice/ionice로 얌전히 도는 기존 전제도 깨진다. 그래서 값을
# 올리는 것은 `--parallel`로 의도를 밝힌 실행에서만 하도록 기본을 1로 둔다.
DEFAULT_NIGHTLY_PARALLEL_ACCOUNTS = 1

# 주말 밤에는 계정을 몇 개까지 동시에 돌릴지.
#
# 주말 아침에 출근하는 인원이 평일의 10~20% 수준이라, 평일 밤보다 세게 돌아도
# 영향을 받는 사람이 그만큼 적다. 여기서 "주말 밤"은 **끝나는 아침이 주말인
# 밤**이다 - 금요일 밤은 주말이고 일요일 밤은 평일이다
# (`scan_window.ends_on_weekend` 참고).
#
# 3으로 둔 것은 측정 전의 보수적 출발점이지 최적값이 아니다. 계정 대부분이
# 같은 파일시스템에 있으면 병렬은 seek 경합만 늘려 오히려 느려질 수 있으므로,
# 실기에서 리소스 보고서를 보고 올리거나 내리는 것을 전제로 한다. 주말에도
# 사람이 아예 없는 것은 아니라는 점(10~20%)이 상한을 정하는 근거다.
DEFAULT_WEEKEND_PARALLEL_ACCOUNTS = 3

# 스캔 중 리소스를 몇 초마다 찍을지. 체크포인트 하나가 15분까지 갈 수 있어
# "체크포인트가 끝날 때마다"로는 그 15분 안에서 무슨 일이 있었는지 못 본다.
DEFAULT_LOAD_SAMPLE_INTERVAL_SECONDS = 30
DEFAULT_LOAD_SAMPLE_RETENTION_DAYS = 30


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
    # `du -k --max-depth=N`. 출력(=DB 행)만 제한하고 크기 계산은 전체를
    # 돈다. 깊게 잡을수록 트리 화면이 자세해지지만 행이 기하급수로 는다.
    detail_scan_max_depth: int = DEFAULT_DETAIL_SCAN_MAX_DEPTH
    # 평평한 목록(증가 경로)에 보여줄 최대 깊이.
    #
    # 0 = 측정 단위(계정 바로 아래 디렉터리)만. 1 이상으로 올리면 부모와 자식이
    # 나란히 나와 **합계가 중복**된다 - 300GB짜리 부모와 그 안의 280GB짜리
    # 자식이 같은 목록에 뜨면 "무엇이 큰가"를 읽을 수 없다. 파고드는 것은
    # 평평한 목록이 아니라 트리 화면이 할 일이다.
    growth_list_max_depth: int = 0
    # 야간 스캔 동시 실행 계정 수 (1 = 직렬). 위 상수의 주석 참고 - 부하
    # 실측용이지 상시 운용값이 아니다.
    nightly_parallel_accounts: int = DEFAULT_NIGHTLY_PARALLEL_ACCOUNTS
    # 주말 밤(=끝나는 아침이 토/일인 밤)에 쓸 동시 실행 계정 수. 위 상수 참고.
    weekend_parallel_accounts: int = DEFAULT_WEEKEND_PARALLEL_ACCOUNTS
    # 스캔 중 리소스 표본 주기와 보관 기간.
    load_sample_interval_seconds: int = DEFAULT_LOAD_SAMPLE_INTERVAL_SECONDS
    load_sample_retention_days: int = DEFAULT_LOAD_SAMPLE_RETENTION_DAYS
    activity_initial_lookback_days: int = DEFAULT_ACTIVITY_INITIAL_LOOKBACK_DAYS
    language: str = DEFAULT_LANGUAGE
    # 알림 채널. command/webhook은 사내 endpoint가 있을 때만 쓰고, 설정하지
    # 않으면 outbox 그대로 동작한다.
    notification_mode: str = DEFAULT_NOTIFICATION_MODE
    notification_command: List[str] = field(default_factory=list)
    notification_webhook_url: str = ""
    notification_timeout_seconds: int = 10
    # 이 사용률 이상이면 cooldown을 무시하고 **매 수집마다 즉시** 알린다.
    # 등급(긴급 98%)과 따로 두는 이유: 등급은 '어느 색으로 보여줄까'의 기준이고
    # 이 값은 '조용히 있어도 되는가'의 기준이라 성격이 다르다. 98%에서 한 시간에
    # 한 번은 적절하지만 99%를 넘으면 그 한 시간 안에 꽉 찬다.
    immediate_notify_pct: float = 99.0
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
    # 야간 상세 스캔에서 경로 하나가 직전 세대 대비 이만큼 늘면 알림을 보낸다.
    # 통계 추정이 아니라 설명 가능한 단순 절대 임계치다.
    growth_alert_min_kb: int = 100 * 1024 * 1024  # 100GB
    growth_alert_enabled: bool = True
    # 관리자 PIN 해시 (평문 아님). 비어 있으면 기본 PIN을 쓴다.
    admin_pin_hash: str = ""
    # FULL 도달 예측 (15분 표본 최소제곱 회귀). 최소 표본을 기대치보다 낮게
    # 잡은 이유는 cron 누락이나 재시작으로 표본이 빠져도 예측이 통째로 멈추지
    # 않게 하기 위함이다.
    full_prediction_enabled: bool = True
    full_prediction_window_hours: int = 3       # 임박 판정 창 (기대 12표본)
    full_prediction_min_samples: int = 4
    full_warn_hours: int = 6                    # 이 시간 내 FULL -> 경고
    full_critical_hours: int = 2                # 이 시간 내 FULL -> 긴급
    trend_short_days: int = 7                   # 단기 추세 (기대 672표본)
    trend_short_min_samples: int = 24           # 6시간치
    trend_long_days: int = 30                   # 장기 추세 (기대 2880표본)
    trend_long_min_samples: int = 96            # 1일치
    full_prediction_max_years: int = 10         # 이보다 먼 예상은 '예측 불가'
    # 급증 판정도 같은 3시간 창을 쓴다 (창을 나누면 설명만 어려워진다).
    capacity_surge_min_kb: int = 100 * 1024 * 1024  # 100GB
    # 경로별 증감 이력 - 이상탐지는 아직 없지만 나중에 붙일 수 있게 숫자만
    # 축적한다. 행이 작아 기준선 세대보다 훨씬 오래 남길 수 있다.
    growth_history_keep_generations: int = 60
    # 수집 신선도 감시. cron이 조용히 안 도는 상황을 잡기 위한 것이라 판정을
    # 넉넉하게 잡는다 - 한두 번 밀린 것으로 경고하면 경고가 일상이 되어
    # 아무도 안 본다.
    freshness_enabled: bool = True
    freshness_stale_multiplier: int = 4      # 최신 표본이 간격의 N배보다 오래되면 지연
    freshness_window_hours: int = 24         # 커버리지를 볼 창
    freshness_min_coverage_pct: int = 50     # 이 미만이면 수집기가 안 도는 것으로 의심
    freshness_min_expected_samples: int = 8  # 창 안 기대 표본이 이보다 적으면 판정 보류


def _current_user() -> str:
    """등록한 사람으로 기록할 OS 계정명. 못 알아내면 빈 문자열."""

    try:
        import getpass

        return getpass.getuser()
    except Exception:  # pragma: no cover - 환경변수가 전혀 없는 경우
        return ""


@dataclass
class Account:
    name: str
    path: str
    enabled: bool = True
    account_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # 등록한 사람의 OS 계정. 파트마다 담당자가 한둘씩 있는 운영 형태라, 나중에
    # "이 계정 누가 넣었지"를 물어볼 곳이 필요하다. 인증 수단이 아니라 메모다
    # (OS 사용자명은 위조할 수 있고, 이 앱은 그것을 검증하지 않는다).
    created_by: str = field(default_factory=lambda: _current_user())
    # 검색 인덱싱은 계정별 opt-in (기본 꺼짐) - 이름 인덱스는 별도 DB를 꽤
    # 차지할 수 있으므로 필요한 계정만 켠다.
    search_indexing: bool = False
    # 이 계정이 프로젝트 계정인지 백업 계정인지 (ACCOUNT_KINDS 참고).
    kind: str = ACCOUNT_KIND_UNSET
    # 프로젝트 계정일 때, 이 계정의 백업이 들어가는 백업 계정의 account_id.
    # 백업 계정 쪽에는 채우지 않는다 (방향이 있는 관계다). 지금은 화면과
    # 보고서에 표시만 하고, 나중에 "BACKUP 하위가 저기에 정말 들어갔는가"를
    # 대조하는 데 쓴다.
    backup_account_id: str = ""

    @property
    def kind_is_project(self) -> bool:
        return self.kind == ACCOUNT_KIND_PROJECT

    @property
    def kind_is_backup(self) -> bool:
        return self.kind == ACCOUNT_KIND_BACKUP


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
    if not 1 <= settings.detail_scan_max_depth <= 12:
        raise ConfigError("detail_scan_max_depth는 1~12여야 합니다")
    if settings.activity_initial_lookback_days < 1:
        raise ConfigError("activity_initial_lookback_days는 1 이상이어야 합니다")
    # 상한을 16으로 둔 것은 임의값이 아니다 - 계정 수만큼 du를 동시에 띄우면
    # 파일서버가 감당하는 범위를 넘어설 수 있고, 이 프로그램이 장애의 원인이
    # 되는 것이 가장 나쁜 실패다. 실측용으로 충분히 넓으면서 사고는 막는 선.
    if not 1 <= settings.nightly_parallel_accounts <= 16:
        raise ConfigError("nightly_parallel_accounts는 1~16이어야 합니다")
    if not 1 <= settings.weekend_parallel_accounts <= 16:
        raise ConfigError("weekend_parallel_accounts는 1~16이어야 합니다")
    if settings.load_sample_interval_seconds < 5:
        raise ConfigError("load_sample_interval_seconds는 5 이상이어야 합니다")
    if settings.load_sample_retention_days < 1:
        raise ConfigError("load_sample_retention_days는 1 이상이어야 합니다")
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
    if settings.growth_alert_min_kb < 0:
        raise ConfigError("growth_alert_min_kb는 음수일 수 없습니다")
    if settings.full_prediction_window_hours < 1:
        raise ConfigError("full_prediction_window_hours는 1 이상이어야 합니다")
    if settings.full_critical_hours >= settings.full_warn_hours:
        raise ConfigError("full_critical_hours는 full_warn_hours보다 작아야 합니다")
    if settings.trend_short_days >= settings.trend_long_days:
        raise ConfigError("trend_short_days는 trend_long_days보다 작아야 합니다")
    for name in (
        "full_prediction_min_samples",
        "trend_short_min_samples",
        "trend_long_min_samples",
    ):
        if getattr(settings, name) < 2:
            raise ConfigError(f"{name}은 2 이상이어야 합니다 (회귀에 최소 2점 필요)")
    if settings.full_prediction_max_years < 1:
        raise ConfigError("full_prediction_max_years는 1 이상이어야 합니다")
    if settings.capacity_surge_min_kb < 0:
        raise ConfigError("capacity_surge_min_kb는 음수일 수 없습니다")
    if settings.growth_history_keep_generations < 1:
        raise ConfigError("growth_history_keep_generations는 1 이상이어야 합니다")
    if settings.freshness_stale_multiplier < 2:
        raise ConfigError("freshness_stale_multiplier는 2 이상이어야 합니다 (cron 지연 여유)")
    if settings.freshness_window_hours < 1:
        raise ConfigError("freshness_window_hours는 1 이상이어야 합니다")
    if not 0 <= settings.freshness_min_coverage_pct <= 100:
        raise ConfigError("freshness_min_coverage_pct는 0~100이어야 합니다")
    if settings.freshness_min_expected_samples < 1:
        raise ConfigError("freshness_min_expected_samples는 1 이상이어야 합니다")
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
        # `created_by`가 없는 예전 설정을 읽을 때 기본값(현재 사용자)이 채워지면
        # **남이 등록한 계정이 지금 파일을 연 사람 것으로 둔갑한다.** 모르는
        # 값은 지어내지 않고 비워 두고, 화면에서 '-'로 보여 준다.
        item = {"created_by": "", **item}
        account = Account(**item)
        if not account.account_id or account.account_id in seen_ids:
            account.account_id = uuid.uuid4().hex
            changed = True
        if account.kind not in ACCOUNT_KINDS:
            # 성격 값이 깨졌다고 앱을 못 열게 하지는 않는다 (language와 같은
            # 톤). 표시와 분류의 문제일 뿐 데이터 무결성 문제가 아니다.
            account.kind = ACCOUNT_KIND_UNSET
            changed = True
        seen_ids.add(account.account_id)
        accounts.append(account)

    # 연결된 백업 계정이 그 사이 삭제됐을 수 있다. 없는 id를 들고 있으면
    # 화면에는 빈칸으로 보이는데 파일에는 남아 있어 나중에 "왜 연결이 안
    # 보이지"로 헷갈린다. 읽는 김에 정리한다.
    for account in accounts:
        if account.backup_account_id and account.backup_account_id not in seen_ids:
            account.backup_account_id = ""
            changed = True

    config = AppConfig(settings=settings, accounts=accounts)
    _guard_read_only_invariant(data_dir, config)
    if changed:
        save_config(data_dir, config)
    return config


def _guard_read_only_invariant(data_dir: Path, config: AppConfig) -> None:
    """데이터 디렉터리가 모니터링 대상 계정 경로와 겹치지 않는지 매번 확인한다.

    DESIGN.md 1부 1절의 읽기 전용 불변식(데이터 디렉터리 외에는 절대 쓰지 않음)을
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
    kind: str = ACCOUNT_KIND_UNSET,
) -> Account:
    """계정을 추가한다. `data_dir`을 넘기면 그 자리에서 바로 읽기 전용
    불변식(데이터 디렉터리와 겹치지 않음)을 확인해, 저장 후 다음 실행에서야
    발견되는 것보다 즉시 사용자에게 알려준다."""

    name = name.strip()
    if not name:
        raise ConfigError("계정 이름을 입력하세요")
    if kind not in ACCOUNT_KINDS:
        raise ConfigError(f"알 수 없는 계정 성격입니다: {kind}")

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

    account = Account(name=name, path=str(resolved), kind=kind)
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


def accounts_of_kind(config: AppConfig, kind: str, enabled_only: bool = True) -> List[Account]:
    source = enabled_accounts(config) if enabled_only else config.accounts
    return [a for a in source if a.kind == kind]


def project_accounts(config: AppConfig, enabled_only: bool = True) -> List[Account]:
    return accounts_of_kind(config, ACCOUNT_KIND_PROJECT, enabled_only)


def backup_accounts(config: AppConfig, enabled_only: bool = True) -> List[Account]:
    return accounts_of_kind(config, ACCOUNT_KIND_BACKUP, enabled_only)


def set_account_kind(config: AppConfig, account_id: str, kind: str) -> bool:
    """계정 성격을 바꾼다. 바뀌었으면 True.

    백업 계정으로 바꾸면 연결된 백업 계정 정보는 지운다 - "백업 계정의 백업
    계정"은 이 모델에 없는 개념이라, 남겨 두면 화면에 뜻 없는 값이 보인다."""

    if kind not in ACCOUNT_KINDS:
        raise ConfigError(f"알 수 없는 계정 성격입니다: {kind}")
    account = find_account(config, account_id)
    if account is None or account.kind == kind:
        return False
    account.kind = kind
    if kind != ACCOUNT_KIND_PROJECT:
        account.backup_account_id = ""
    return True


def set_backup_link(config: AppConfig, account_id: str, backup_account_id: str) -> bool:
    """프로젝트 계정에 연결 백업 계정을 지정한다. 바뀌었으면 True."""

    account = find_account(config, account_id)
    if account is None:
        return False
    if backup_account_id:
        target = find_account(config, backup_account_id)
        if target is None:
            raise ConfigError("연결할 백업 계정을 찾을 수 없습니다")
        if target.account_id == account_id:
            raise ConfigError("자기 자신을 백업 계정으로 연결할 수 없습니다")
        if target.kind != ACCOUNT_KIND_BACKUP:
            raise ConfigError("연결 대상은 성격이 '백업'인 계정이어야 합니다")
    if account.backup_account_id == backup_account_id:
        return False
    account.backup_account_id = backup_account_id
    return True
