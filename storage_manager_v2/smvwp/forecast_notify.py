"""FULL 도달 예측·급증 결과를 알림으로 내보낸다.

`cycle.run_collection_cycle`이 15분마다 호출한다. `analytics`가 계산한 결과를
받아 파일시스템 단위로 묶고, 임계치를 넘으면 알림을 보낸다.

핵심은 **파일시스템 단위 중복 제거**다. `df`는 계정이 아니라 파일시스템 전체
값을 주므로, 같은 파일시스템에 계정이 5개면 똑같은 "FULL 임박" 경고가 5번
나간다. 여기서 한 번으로 합치고 관련 계정 목록을 메시지에 담는다.

`cycle`에서 분리한 이유: 수집(collect -> store)과 판단(predict -> notify)은
실패했을 때의 의미가 다르다. 예측 계산이 잘못돼도 수집된 df 값은 그대로
남아야 하므로, 호출부가 이 모듈의 예외를 따로 감쌀 수 있어야 한다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import analytics, config as config_module, i18n, notifications, store, tiers

logger = logging.getLogger(__name__)


def _format_kb(size_kb: Optional[int]) -> str:
    if size_kb is None:
        return "-"
    value = float(size_kb)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024 or unit == "PB":
            return f"{int(value):,} KB" if unit == "KB" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} PB"  # pragma: no cover


def forecast_tier(hours_to_full: float, settings: config_module.Settings) -> Optional[str]:
    """남은 시간을 등급으로. 경고 기준 밖이면 None (알리지 않음)."""

    if hours_to_full <= settings.full_critical_hours:
        return tiers.EMERGENCY
    if hours_to_full <= settings.full_warn_hours:
        return tiers.ALERT
    return None


def build_forecasts(
    data_dir: Path,
    config: config_module.AppConfig,
    now: Optional[datetime] = None,
) -> List[analytics.CapacityForecast]:
    """활성 계정별 예측을 만든다 (읽기 전용)."""

    now = now or datetime.now(timezone.utc)
    settings = config.settings
    # 가장 긴 창(장기 추세)만큼만 읽으면 세 예측을 모두 낼 수 있다.
    window_days = max(settings.trend_long_days, settings.trend_short_days, 1)
    since = now - timedelta(days=window_days)

    forecasts: List[analytics.CapacityForecast] = []
    conn = store.connect(data_dir)
    try:
        for account in config_module.enabled_accounts(config):
            samples = store.samples_since(conn, account.account_id, since)
            forecasts.append(
                analytics.build_forecast(account.account_id, samples, settings, now)
            )
    finally:
        conn.close()
    return forecasts


def notify_forecasts(
    data_dir: Path,
    config: config_module.AppConfig,
    forecasts: Sequence[analytics.CapacityForecast],
    now: Optional[datetime] = None,
) -> int:
    """임박 예측과 급증을 파일시스템 단위로 알린다. 보낸 건수를 반환."""

    settings = config.settings
    if not settings.full_prediction_enabled:
        return 0

    now = now or datetime.now(timezone.utc)
    accounts_by_id = {a.account_id: a for a in config.accounts}
    state = notifications.load_notify_state(data_dir)
    sent = 0

    for key, group in analytics.group_by_filesystem(forecasts).items():
        accounts = [
            accounts_by_id[f.account_id] for f in group if f.account_id in accounts_by_id
        ]
        if not accounts:
            continue
        filesystem = group[0].filesystem or key
        account_names = ", ".join(a.name for a in accounts)

        sent += _notify_imminent(
            data_dir, settings, group, accounts, filesystem, account_names, state, now
        )
        sent += _notify_surge(
            data_dir, settings, group, accounts, filesystem, account_names, state, now
        )

    notifications.save_notify_state(data_dir, state)
    return sent


def _notify_imminent(
    data_dir: Path,
    settings: config_module.Settings,
    group: Sequence[analytics.CapacityForecast],
    accounts: Sequence,
    filesystem: str,
    account_names: str,
    state: Dict[str, dict],
    now: datetime,
) -> int:
    # 같은 파일시스템이면 예측이 같지만, 표본 수가 달라 일부만 예측에 성공할
    # 수 있다. 가장 임박한(= 가장 보수적인) 값을 대표로 쓴다.
    candidates = [f.imminent for f in group if f.imminent.ok]
    if not candidates:
        notifications.clear_forecast_state(
            state, notifications.KIND_FULL_FORECAST, filesystem
        )
        return 0

    best = min(candidates, key=lambda p: p.hours_to_full)
    tier = forecast_tier(best.hours_to_full, settings)
    if tier is None:
        # 경고 기준 밖으로 회복 - 다음에 다시 임박하면 즉시 알리도록 리셋.
        notifications.clear_forecast_state(
            state, notifications.KIND_FULL_FORECAST, filesystem
        )
        return 0

    message = i18n.t(
        "notify.full_forecast_message",
        filesystem=filesystem,
        hours=f"{best.hours_to_full:.1f}",
        accounts=account_names,
    )
    result = notifications.maybe_notify_forecast(
        data_dir,
        accounts,
        filesystem,
        tier,
        message,
        notifications.KIND_FULL_FORECAST,
        {
            "hours_to_full": round(best.hours_to_full, 2),
            "slope_kb_per_hour": best.slope_kb_per_hour,
            "sample_count": best.sample_count,
        },
        state,
        cooldown_minutes=settings.notification_cooldown_minutes,
        now=now,
        mode=settings.notification_mode,
        command=settings.notification_command,
        webhook_url=settings.notification_webhook_url,
        timeout_seconds=settings.notification_timeout_seconds,
    )
    return 1 if result is not None else 0


def _notify_surge(
    data_dir: Path,
    settings: config_module.Settings,
    group: Sequence[analytics.CapacityForecast],
    accounts: Sequence,
    filesystem: str,
    account_names: str,
    state: Dict[str, dict],
    now: datetime,
) -> int:
    surging = [f for f in group if f.is_surge and f.surge_kb is not None]
    if not surging:
        notifications.clear_forecast_state(state, notifications.KIND_SURGE, filesystem)
        return 0

    delta_kb = max(f.surge_kb for f in surging)
    message = i18n.t(
        "notify.surge_message",
        filesystem=filesystem,
        window=settings.full_prediction_window_hours,
        delta=_format_kb(delta_kb),
        accounts=account_names,
    )
    result = notifications.maybe_notify_forecast(
        data_dir,
        accounts,
        filesystem,
        tiers.WARN,
        message,
        notifications.KIND_SURGE,
        {
            "delta_kb": delta_kb,
            "window_hours": settings.full_prediction_window_hours,
        },
        state,
        cooldown_minutes=settings.notification_cooldown_minutes,
        now=now,
        mode=settings.notification_mode,
        command=settings.notification_command,
        webhook_url=settings.notification_webhook_url,
        timeout_seconds=settings.notification_timeout_seconds,
    )
    return 1 if result is not None else 0
