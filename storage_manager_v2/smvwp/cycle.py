"""수집 -> 저장 -> outbox 알림 -> 보존기간 정리, 한 사이클 전체.

이 모듈은 PyQt5에 의존하지 않는다 - GUI 내부 타이머(`smvwp.scheduler`)와
cron용 헤드리스 스크립트(`collector_cli.py`)가 이 함수 하나를 공유해서, 두
실행 경로("cron 또는 백그라운드 타이머", REBUILD_CONCEPT.md 7절 3번)의
동작이 어긋나지 않게 한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from . import collector, config as config_module, notifications, store


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

    return records
