"""수집 -> 저장 -> outbox 알림 -> 보존기간 정리, 한 사이클 전체.

이 모듈은 Qt에 의존하지 않는다 - GUI 내부 타이머(`smvwp.gui.scheduler`)와
cron 경로(`smvwp_cli.py collect`)가 이 함수 하나를 공유해서, 두
실행 경로("cron 또는 백그라운드 타이머", DESIGN.md 2부 7절 3번)의
동작이 어긋나지 않게 한다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from . import collector, config as config_module, forecast_notify, notifications, store

logger = logging.getLogger(__name__)


def run_collection_cycle(data_dir: Path, config: config_module.AppConfig) -> List[store.SampleRecord]:
    settings = config.settings
    accounts = config_module.enabled_accounts(config)
    records = collector.collect_all(
        accounts,
        df_timeout_seconds=settings.df_timeout_seconds,
        quota_command=settings.quota_command,
    )

    conn = store.connect(data_dir)
    try:
        for record in records:
            store.insert_sample(conn, record)
        store.prune_old_samples(conn, settings.sample_retention_days)
    finally:
        conn.close()

    accounts_by_id = {a.account_id: a for a in accounts}
    state = notifications.load_notify_state(data_dir)
    for record in records:
        account = accounts_by_id.get(record.account_id)
        if account is None:
            continue
        notifications.maybe_notify(
            data_dir,
            account,
            record,
            state,
            min_tier=settings.notification_min_tier,
            cooldown_minutes=settings.notification_cooldown_minutes,
            mode=settings.notification_mode,
            command=settings.notification_command,
            webhook_url=settings.notification_webhook_url,
            timeout_seconds=settings.notification_timeout_seconds,
        )
    notifications.save_notify_state(data_dir, state)

    # 예측/급증 알림은 수집이 끝난 뒤 별도로 처리한다. 여기서 실패해도 이미
    # 저장된 df 표본은 그대로 남아야 하므로 예외를 삼킨다 - 예측은 부가
    # 정보이고, 수집 자체가 이 사이클의 본질이다.
    try:
        forecasts = forecast_notify.build_forecasts(data_dir, config)
        forecast_notify.notify_forecasts(data_dir, config, forecasts)
    except Exception:  # pragma: no cover - 방어적 처리
        logger.exception("FULL 예측 알림 실패 (수집 결과는 이미 저장됨)")

    return records
