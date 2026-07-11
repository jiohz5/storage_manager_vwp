from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from storage_manager.config import AccountStore, db_file, load_store
from storage_manager.database import Database
from storage_manager.i18n import tr
from storage_manager.notifications import (
    NotificationEvent,
    NotificationResult,
    dispatch_notifications,
    purge_notification_outbox,
)
from storage_manager.tracking import read_scan_status


def build_freshness_events(
    store: AccountStore,
    db: Database,
    now: Optional[datetime] = None,
) -> List[NotificationEvent]:
    current = now or datetime.now()
    language = store.settings.language
    threshold = store.settings.freshness_warning_hours
    events: List[NotificationEvent] = []
    stale_accounts = []
    missing_accounts = []
    for account in store.accounts:
        if not account.enabled:
            continue
        snapshot = db.latest_nightly_snapshot(account.account_id)
        if snapshot is None:
            missing_accounts.append(account.name)
            continue
        try:
            collected_at = datetime.strptime(str(snapshot[0]), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            missing_accounts.append(account.name)
            continue
        age_hours = (current - collected_at).total_seconds() / 3600
        if age_hours >= threshold:
            stale_accounts.append((account.name, int(age_hours)))

    if missing_accounts:
        events.append(
            NotificationEvent(
                key="freshness:missing",
                level="alert",
                title=tr(language, "notify.freshness.title"),
                message=tr(
                    language,
                    "notify.freshness.missing",
                    accounts=", ".join(sorted(missing_accounts)),
                ),
            )
        )
    if stale_accounts:
        level = "alert" if max(age for _, age in stale_accounts) >= threshold * 2 else "warning"
        details = ", ".join(f"{name}({age}h)" for name, age in sorted(stale_accounts))
        events.append(
            NotificationEvent(
                key="freshness:stale",
                level=level,
                title=tr(language, "notify.freshness.title"),
                message=tr(language, "notify.freshness.stale", details=details),
            )
        )

    runtime = read_scan_status(db.path.parent)
    runtime_state = str(runtime.get("state", "never"))
    if runtime_state in {"failed", "interrupted"}:
        events.append(
            NotificationEvent(
                key="runtime:failed",
                level="alert",
                title=tr(language, "notify.runtime.title"),
                message=tr(
                    language,
                    "notify.runtime.failed",
                    state=runtime_state,
                    message=runtime.get("message") or "-",
                ),
            )
        )
    return events


def run_health_check(
    data_dir: Path,
    now: Optional[datetime] = None,
) -> NotificationResult:
    store = load_store(data_dir)
    db = Database(db_file(data_dir))
    try:
        events = build_freshness_events(store, db, now)
    finally:
        db.close()
    result = dispatch_notifications(data_dir, store.settings, events, now)
    purge_notification_outbox(data_dir, store.settings.history_days, now)
    return result
