from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from storage_manager.activity_scan import scan_changed_file_activity
from storage_manager.analytics import capacity_forecast, detect_growth_anomaly
from storage_manager.collector import (
    RHEL_BACKEND,
    StorageBackend,
    UsageSnapshot,
    delta_map,
    ranked_items,
    usage_level,
)
from storage_manager.config import (
    ConfigError,
    db_file,
    load_store,
    lock_file,
    normalize_account_path,
)
from storage_manager.database import Database
from storage_manager.i18n import tr
from storage_manager.notifications import (
    NotificationEvent,
    dispatch_notifications,
    purge_notification_outbox,
)
from storage_manager.reports import (
    AccountReport,
    purge_old_reports,
    write_cleanup_report,
    write_daily_report,
    write_weekly_report,
)
from storage_manager.quota import collect_quota
from storage_manager.resumable_scan import run_resumable_baseline
from storage_manager.tracking import (
    clear_scan_stop,
    scan_stop_requested,
    update_scan_status,
    write_scan_status,
)


CRON_MARKER = "# storage-manager-vwp"
NIGHTLY_CRON_MARKER = f"{CRON_MARKER} nightly"
HEALTH_CRON_MARKER = f"{CRON_MARKER} health"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3


def _build_notification_events(
    reports: List[AccountReport],
    snapshots: Dict[str, UsageSnapshot],
    alert_threshold: int,
    language: str,
    day: str,
) -> List[NotificationEvent]:
    events: List[NotificationEvent] = []
    filesystem_groups: Dict[str, List[AccountReport]] = {}
    for report in reports:
        snapshot = snapshots.get(report.account.account_id)
        if snapshot is not None:
            filesystem_groups.setdefault(snapshot.fs_name, []).append(report)

    for filesystem, entries in filesystem_groups.items():
        group_snapshots = [snapshots[entry.account.account_id] for entry in entries]
        byte_pct = max(snapshot.use_pct for snapshot in group_snapshots)
        inode_values = [
            snapshot.inode_use_pct
            for snapshot in group_snapshots
            if snapshot.inode_use_pct is not None
        ]
        inode_pct = max(inode_values) if inode_values else None
        effective = max([byte_pct, *inode_values])
        if effective >= 90:
            events.append(
                NotificationEvent(
                    key=f"filesystem:{filesystem}",
                    level=usage_level(effective, alert_threshold),
                    title=tr(language, "notify.capacity.title"),
                    message=tr(
                        language,
                        "notify.capacity.filesystem",
                        filesystem=filesystem,
                        accounts=", ".join(sorted(entry.account.name for entry in entries)),
                        byte=byte_pct,
                        inode="-" if inode_pct is None else f"{inode_pct}%",
                    ),
                )
            )
        anomalous = [
            entry
            for entry in entries
            if entry.anomaly is not None and entry.anomaly.detected
        ]
        if anomalous:
            latest = max(entry.anomaly.latest_delta_kb for entry in anomalous)
            baseline = max(entry.anomaly.baseline_median_kb for entry in anomalous)
            events.append(
                NotificationEvent(
                    key=f"anomaly:{filesystem}:{day}",
                    level="warning",
                    title=tr(language, "notify.anomaly.title"),
                    message=tr(
                        language,
                        "notify.anomaly.message",
                        account=", ".join(sorted(entry.account.name for entry in anomalous)),
                        latest=latest,
                        baseline=baseline,
                    ),
                )
            )
        forecast_entries = [
            entry
            for entry in entries
            if entry.forecast_30 is not None
            and (
                (
                    entry.forecast_30.days_to_alert is not None
                    and entry.forecast_30.days_to_alert <= 7
                )
                or (
                    entry.forecast_30.days_to_full is not None
                    and entry.forecast_30.days_to_full <= 14
                )
            )
        ]
        if forecast_entries:
            alert_days = [
                entry.forecast_30.days_to_alert
                for entry in forecast_entries
                if entry.forecast_30.days_to_alert is not None
            ]
            full_days = [
                entry.forecast_30.days_to_full
                for entry in forecast_entries
                if entry.forecast_30.days_to_full is not None
            ]
            alert_day = min(alert_days) if alert_days else None
            full_day = min(full_days) if full_days else None
            level = (
                "alert"
                if alert_day == 0 or (full_day is not None and full_day <= 3)
                else "warning"
            )
            events.append(
                NotificationEvent(
                    key=f"forecast:{filesystem}",
                    level=level,
                    title=tr(language, "notify.forecast.title"),
                    message=tr(
                        language,
                        "notify.forecast.message",
                        account=", ".join(
                            sorted(entry.account.name for entry in forecast_entries)
                        ),
                        alert=alert_day if alert_day is not None else "-",
                        full=full_day if full_day is not None else "-",
                    ),
                )
            )

    for report in reports:
        snapshot = snapshots.get(report.account.account_id)
        if snapshot is None:
            continue
        if snapshot.quota_use_pct is not None and snapshot.quota_use_pct >= 90:
            events.append(
                NotificationEvent(
                    key=f"quota:{report.account.account_id}",
                    level=usage_level(snapshot.quota_use_pct, alert_threshold),
                    title=tr(language, "notify.quota.title"),
                    message=tr(
                        language,
                        "notify.quota.message",
                        account=report.account.name,
                        percent=snapshot.quota_use_pct,
                    ),
                )
            )
    return events


@dataclass(frozen=True)
class CronStatus:
    available: bool
    installed: bool
    line: str = ""
    error: str = ""
    health_installed: bool = False
    health_line: str = ""


def overnight_seconds_remaining(
    now: datetime,
    start_hour: int = 22,
    end_hour: int = 6,
    safety_minutes: int = 15,
) -> int:
    in_window = now.hour >= start_hour or now.hour < end_hour
    if not in_window:
        return 0
    end = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if now.hour >= start_hour:
        end += timedelta(days=1)
    deadline = end - timedelta(minutes=safety_minutes)
    return max(0, int((deadline - now).total_seconds()))


class ScanAlreadyRunning(RuntimeError):
    pass


class ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="ascii")
        try:
            self.handle.seek(0)
            if self.handle.read(1) == "":
                self.handle.write("0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.handle.close()
            self.handle = None
            raise ScanAlreadyRunning(f"Another nightly scan is already running: {self.path}") from exc

        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(str(os.getpid()))
        self.handle.flush()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


def rotate_cron_log(data_dir: Path) -> None:
    path = data_dir / "nightly_scan.log"
    try:
        if not path.exists() or path.stat().st_size < MAX_LOG_BYTES:
            return
        oldest = path.with_name(f"{path.name}.{LOG_BACKUPS}")
        if oldest.exists():
            oldest.unlink()
        for number in range(LOG_BACKUPS - 1, 0, -1):
            source = path.with_name(f"{path.name}.{number}")
            if source.exists():
                source.replace(path.with_name(f"{path.name}.{number + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
    except OSError as exc:
        print(f"[WARN] log rotation failed: {exc}", file=sys.stderr)


def run_nightly_scan(
    data_dir: Path,
    skip_detail: bool = False,
    force_weekly: bool = False,
    backend: StorageBackend = RHEL_BACKEND,
    now_override: Optional[datetime] = None,
    trigger: str = "direct",
) -> Path:
    store = load_store(data_dir)
    with ProcessLock(lock_file(data_dir)):
        run_id = uuid.uuid4().hex
        runtime_state = "failed"
        runtime_message = ""
        stopped = False
        enabled_accounts = [account for account in store.accounts if account.enabled]
        write_scan_status(
            data_dir,
            {
                "state": "running",
                "run_id": run_id,
                "pid": os.getpid(),
                "trigger": trigger,
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "phase": "df",
                "accounts_total": len(enabled_accounts),
            },
        )
        db = None
        try:
            rotate_cron_log(data_dir)
            db = Database(db_file(data_dir))
            now = now_override or datetime.now()
            ts = now.strftime("%Y-%m-%d %H:%M:%S")
            day = now.strftime("%Y-%m-%d")
            reports = [
                AccountReport(
                    account=account,
                    detail_status=tr(store.settings.language, "scan.not_run"),
                )
                for account in enabled_accounts
            ]
            report_by_id = {report.account.account_id: report for report in reports}
            validated_paths = {}
            snapshots: Dict[str, UsageSnapshot] = {}

            # Always finish the inexpensive df pass for every account before any deep scan.
            for account_index, account in enumerate(enabled_accounts, start=1):
                update_scan_status(
                    data_dir,
                    run_id,
                    current_account=account.name,
                    accounts_processed=account_index - 1,
                )
                report = report_by_id[account.account_id]
                db.backfill_account(account.account_id, account.name, account.path)

                try:
                    normalized = normalize_account_path(
                        account.path,
                        store.settings.monitored_roots,
                        require_exists=True,
                    )
                    snapshot = backend.read_usage(
                        normalized,
                        store.settings.df_timeout_seconds,
                    )
                    try:
                        quota = collect_quota(
                            store.settings.quota_command,
                            account.name,
                            normalized,
                            store.settings.quota_timeout_seconds,
                        )
                    except Exception as quota_exc:
                        snapshot = replace(snapshot, quota_error=str(quota_exc))
                    else:
                        if quota is not None:
                            snapshot = replace(
                                snapshot,
                                quota_used_kb=quota.used_kb,
                                quota_limit_kb=quota.limit_kb,
                                quota_use_pct=quota.use_pct,
                            )
                except Exception as exc:
                    report.status = f"ERROR: {exc}"
                    report.detail_status = tr(store.settings.language, "scan.skip_df")
                    print(f"[WARN] {account.name}: {exc}", file=sys.stderr)
                    update_scan_status(
                        data_dir,
                        run_id,
                        accounts_processed=account_index,
                    )
                    continue

                validated_paths[account.account_id] = normalized
                snapshots[account.account_id] = snapshot

                previous = db.previous_snapshot(account.account_id, day)
                report.use_pct = snapshot.use_pct
                report.used_kb = snapshot.used_kb
                report.total_kb = snapshot.total_kb
                report.inode_use_pct = snapshot.inode_use_pct
                report.quota_used_kb = snapshot.quota_used_kb
                report.quota_limit_kb = snapshot.quota_limit_kb
                report.quota_use_pct = snapshot.quota_use_pct
                report.quota_error = snapshot.quota_error
                if previous:
                    report.used_delta_kb = snapshot.used_kb - int(previous[3])

                db.upsert_snapshot(
                    ts=ts,
                    day=day,
                    account_id=account.account_id,
                    account_name=account.name,
                    account_path=normalized,
                    fs_name=snapshot.fs_name,
                    total_kb=snapshot.total_kb,
                    used_kb=snapshot.used_kb,
                    avail_kb=snapshot.avail_kb,
                    use_pct=snapshot.use_pct,
                    total_inodes=snapshot.total_inodes,
                    used_inodes=snapshot.used_inodes,
                    avail_inodes=snapshot.avail_inodes,
                    inode_use_pct=snapshot.inode_use_pct,
                    quota_used_kb=snapshot.quota_used_kb,
                    quota_limit_kb=snapshot.quota_limit_kb,
                    quota_use_pct=snapshot.quota_use_pct,
                    quota_error=snapshot.quota_error,
                    source="nightly",
                )
                update_scan_status(
                    data_dir,
                    run_id,
                    accounts_processed=account_index,
                )
                trend_points = db.trend_points(account.account_id, 45)
                report.forecast_7 = capacity_forecast(
                    trend_points,
                    7,
                    store.settings.alert_threshold,
                )
                report.forecast_30 = capacity_forecast(
                    trend_points,
                    30,
                    store.settings.alert_threshold,
                )
                report.anomaly = detect_growth_anomaly(
                    db.recent_used_points(account.account_id, 45),
                    store.settings.anomaly_multiplier,
                    store.settings.anomaly_min_growth_gb * 1024 * 1024,
                )

            if scan_stop_requested(data_dir, run_id):
                stopped = True
                for report in reports:
                    if report.detail_status == tr(store.settings.language, "scan.not_run"):
                        report.detail_status = tr(store.settings.language, "scan.stop_requested")
            elif skip_detail:
                for report in reports:
                    if report.detail_status == tr(store.settings.language, "scan.not_run"):
                        report.detail_status = tr(store.settings.language, "scan.skip_option")
            else:
                update_scan_status(data_dir, run_id, phase="detail")
                candidates = [
                    account for account in enabled_accounts if account.account_id in validated_paths
                ]
                if candidates:
                    offset = now.date().toordinal() % len(candidates)
                    candidates = candidates[offset:] + candidates[:offset]
                    candidates.sort(
                        key=lambda account: (
                            max(
                                value
                                for value in (
                                    report_by_id[account.account_id].use_pct,
                                    report_by_id[account.account_id].inode_use_pct,
                                    report_by_id[account.account_id].quota_use_pct,
                                )
                                if value is not None
                            )
                            < store.settings.alert_threshold
                        )
                    )

                detail_budget_seconds = store.settings.nightly_detail_budget_seconds
                if not backend.test_mode:
                    window_reference = now if now_override is not None else datetime.now()
                    detail_budget_seconds = min(
                        detail_budget_seconds,
                        overnight_seconds_remaining(
                            window_reference,
                            store.settings.scan_window_start_hour,
                            store.settings.scan_window_end_hour,
                            store.settings.scan_safety_minutes,
                        ),
                    )
                if detail_budget_seconds < 60:
                    for candidate in candidates:
                        report_by_id[candidate.account_id].detail_status = tr(
                            store.settings.language,
                            "scan.outside_window",
                        )
                    candidates = []

                detail_started = time.monotonic()
                for index, account in enumerate(candidates):
                    if scan_stop_requested(data_dir, run_id):
                        stopped = True
                        for skipped in candidates[index:]:
                            report_by_id[skipped.account_id].detail_status = tr(
                                store.settings.language,
                                "scan.stop_requested",
                            )
                        break
                    report = report_by_id[account.account_id]
                    update_scan_status(
                        data_dir,
                        run_id,
                        current_account=account.name,
                    )
                    remaining = (
                        detail_budget_seconds
                        - (time.monotonic() - detail_started)
                    )
                    if remaining < 60:
                        for skipped in candidates[index:]:
                            report_by_id[skipped.account_id].detail_status = tr(
                                store.settings.language,
                                "scan.budget_exhausted",
                            )
                        break

                    timeout_seconds = min(
                        store.settings.detail_scan_timeout_seconds,
                        max(60, int(remaining)),
                    )
                    baseline_exists = db.has_inventory(account.account_id)
                    baseline = db.current_inventory(account.account_id)
                    active_baseline = db.detail_scan_state(account.account_id) is not None
                    exact_due = (
                        not baseline_exists
                        or active_baseline
                        or force_weekly
                        or now.weekday() == store.settings.weekly_report_weekday
                    )
                    if not backend.test_mode and not exact_due:
                        since = db.last_activity_ts(account.account_id)
                        if since is None:
                            inventory_day = db.inventory_scan_day(account.account_id) or day
                            since = f"{inventory_day} 00:00:00"
                        activity = scan_changed_file_activity(
                            validated_paths[account.account_id],
                            since,
                            timeout_seconds,
                            stop_requested=lambda: scan_stop_requested(data_dir, run_id),
                        )
                        if activity.cancelled:
                            report.detail_status = tr(
                                store.settings.language,
                                "scan.stop_requested",
                            )
                            stopped = True
                            for skipped in candidates[index + 1 :]:
                                report_by_id[skipped.account_id].detail_status = tr(
                                    store.settings.language,
                                    "scan.stop_requested",
                                )
                            break
                        if activity.complete:
                            ranked_activity = [
                                (path, changed_bytes, file_count, newest_mtime, rank_no)
                                for rank_no, (
                                    path,
                                    changed_bytes,
                                    file_count,
                                    newest_mtime,
                                ) in enumerate(
                                    activity.items[: store.settings.detail_top_n],
                                    start=1,
                                )
                            ]
                            db.replace_activity_items(
                                day,
                                ts,
                                account.account_id,
                                account.name,
                                ranked_activity,
                            )
                            db.update_item_activity(
                                account.account_id,
                                day,
                                [
                                    (path, changed_bytes)
                                    for path, changed_bytes, _, _ in activity.items
                                ],
                            )
                            report.activity = [
                                (path, changed_bytes, file_count, newest_mtime)
                                for path, changed_bytes, file_count, newest_mtime in activity.items[:5]
                            ]
                            report.detail_status = tr(
                                store.settings.language,
                                "scan.activity_complete",
                                count=activity.files_seen,
                                seconds=activity.duration_seconds,
                            )
                        else:
                            report.detail_status = tr(
                                store.settings.language,
                                "scan.activity_failed",
                                error=activity.error,
                            )
                        continue
                    if backend.test_mode:
                        detail = backend.scan_detail(
                            validated_paths[account.account_id],
                            timeout_seconds,
                        )
                    else:
                        detail = run_resumable_baseline(
                            db,
                            account.account_id,
                            validated_paths[account.account_id],
                            timeout_seconds,
                            task_timeout_seconds=min(
                                store.settings.detail_task_timeout_seconds,
                                timeout_seconds,
                            ),
                            stop_requested=lambda: scan_stop_requested(data_dir, run_id),
                        )
                    if detail.cancelled:
                        report.detail_status = tr(
                            store.settings.language,
                            "scan.stop_requested",
                        )
                        stopped = True
                        for skipped in candidates[index + 1 :]:
                            report_by_id[skipped.account_id].detail_status = tr(
                                store.settings.language,
                                "scan.stop_requested",
                            )
                        break
                    if not detail.complete:
                        if detail.resumable and not detail.error:
                            report.detail_status = tr(
                                store.settings.language,
                                "scan.progress",
                                completed=detail.completed_tasks,
                                total=detail.total_tasks,
                            )
                        else:
                            report.detail_status = tr(
                                store.settings.language,
                                "scan.failed",
                                seconds=detail.duration_seconds,
                                error=detail.error,
                            )
                            print(
                                f"[WARN] {account.name} detail scan: {detail.error}",
                                file=sys.stderr,
                            )
                        continue

                    deltas = delta_map(
                        baseline,
                        detail.items,
                        baseline_exists=baseline_exists,
                    )
                    growth = [
                        row for row in deltas if row[1] > 0
                    ][: store.settings.detail_top_n]
                    report.growth = growth[:5]
                    baseline_note = (
                        tr(store.settings.language, "scan.baseline")
                        if not baseline_exists
                        else ""
                    )
                    report.detail_status = baseline_note + tr(
                        store.settings.language,
                        "scan.complete",
                        seconds=detail.duration_seconds,
                        count=len(detail.items),
                    )

                    db.replace_top_items(
                        ts,
                        day,
                        account.account_id,
                        account.name,
                        ranked_items(detail.items, store.settings.detail_top_n),
                    )
                    db.replace_growth_items(
                        ts,
                        day,
                        account.account_id,
                        account.name,
                        [
                            (item_path, delta_kb, rank_no)
                            for rank_no, (item_path, delta_kb) in enumerate(growth, start=1)
                        ],
                    )
                    db.replace_inventory(account.account_id, day, detail.items)
                    db.update_item_activity(
                        account.account_id,
                        day,
                        [
                            (item_path, abs(delta_kb) * 1024)
                            for item_path, delta_kb in deltas
                            if delta_kb != 0
                        ],
                    )
                    detail_state = (
                        db.detail_scan_state(account.account_id)
                        if detail.resumable
                        else None
                    )
                    activity_cursor = str(detail_state[2]) if detail_state else ts
                    db.set_activity_cursor(account.account_id, activity_cursor)
                    if detail_state:
                        db.finish_detail_scan(account.account_id, str(detail_state[1]))

            update_scan_status(data_dir, run_id, phase="report", current_account="")
            daily_path = write_daily_report(
                data_dir,
                day,
                ts,
                reports,
                store.settings.alert_threshold,
                store.settings.language,
            )
            if force_weekly or now.weekday() == store.settings.weekly_report_weekday:
                weekly_path = write_weekly_report(
                    data_dir,
                    db,
                    enabled_accounts,
                    day,
                    store.settings.alert_threshold,
                    store.settings.language,
                )
                print(f"[INFO] weekly report: {weekly_path}")
            cleanup_path = write_cleanup_report(
                data_dir,
                db,
                enabled_accounts,
                day,
                store.settings,
                store.settings.language,
            )
            print(f"[INFO] cleanup review report: {cleanup_path}")

            notification_events = _build_notification_events(
                reports,
                snapshots,
                store.settings.alert_threshold,
                store.settings.language,
                day,
            )
            try:
                notification_result = dispatch_notifications(
                    data_dir,
                    store.settings,
                    notification_events,
                    now,
                )
                if notification_result.error:
                    print(
                        f"[WARN] notification delivery: {notification_result.error}",
                        file=sys.stderr,
                    )
                elif notification_result.sent:
                    print(
                        f"[INFO] notifications sent: {notification_result.sent}; "
                        f"outbox={notification_result.outbox_file}"
                    )
            except OSError as exc:
                print(f"[WARN] notification outbox failed: {exc}", file=sys.stderr)

            db.purge_old(store.settings.history_days)
            purge_old_reports(data_dir, store.settings.history_days, now.date())
            purge_notification_outbox(data_dir, store.settings.history_days, now)
            db.checkpoint()
            print(f"[INFO] daily report: {daily_path}")
            runtime_state = "stopped" if stopped else "succeeded"
            runtime_message = "stop requested by user" if stopped else str(daily_path)
            return daily_path
        except Exception as exc:
            runtime_message = str(exc)
            raise
        finally:
            if db is not None:
                db.close()
            clear_scan_stop(data_dir, run_id)
            update_scan_status(
                data_dir,
                run_id,
                state=runtime_state,
                phase="complete" if runtime_state != "failed" else "failed",
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                current_account="",
                message=runtime_message,
            )


def _safe_cron_value(value: str) -> str:
    if "\n" in value or "\r" in value or "%" in value:
        raise ValueError("Cron paths cannot contain newlines or percent signs")
    return shlex.quote(value)


def cron_line(data_dir: Path, python_bin: str) -> str:
    script = Path(__file__).resolve().parent.parent / "nightly_scan.py"
    log_path = data_dir / "nightly_scan.log"
    return (
        "0 22 * * * "
        f"{_safe_cron_value(python_bin)} {_safe_cron_value(str(script))} "
        f"--data-dir {_safe_cron_value(str(data_dir))} "
        "--trigger cron "
        f">> {_safe_cron_value(str(log_path))} 2>&1 {NIGHTLY_CRON_MARKER}"
    )


def health_cron_line(data_dir: Path, python_bin: str) -> str:
    script = Path(__file__).resolve().parent.parent / "health_check.py"
    log_path = data_dir / "nightly_scan.log"
    return (
        "0 7 * * * "
        f"{_safe_cron_value(python_bin)} {_safe_cron_value(str(script))} "
        f"--data-dir {_safe_cron_value(str(data_dir))} "
        f">> {_safe_cron_value(str(log_path))} 2>&1 {HEALTH_CRON_MARKER}"
    )


def read_cron_status() -> CronStatus:
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return CronStatus(False, False, error="crontab command not found")
    except (OSError, subprocess.SubprocessError) as exc:
        return CronStatus(False, False, error=str(exc))

    line = next(
        (
            row
            for row in result.stdout.splitlines()
            if NIGHTLY_CRON_MARKER in row and not row.lstrip().startswith("#")
        ),
        "",
    )
    health_line = next(
        (
            row
            for row in result.stdout.splitlines()
            if HEALTH_CRON_MARKER in row and not row.lstrip().startswith("#")
        ),
        "",
    )
    if line or health_line:
        return CronStatus(
            True,
            bool(line and health_line),
            line=line,
            error="" if line and health_line else "managed cron entries are incomplete",
            health_installed=bool(health_line),
            health_line=health_line,
        )
    error = result.stderr.strip()
    if result.returncode != 0 and error and "no crontab" not in error.lower():
        return CronStatus(False, False, error=error)
    return CronStatus(True, False)


def remove_cron() -> bool:
    status = read_cron_status()
    if not status.available:
        raise RuntimeError(status.error or "crontab is unavailable")
    result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    existing = result.stdout if result.returncode == 0 else ""
    lines = [line for line in existing.splitlines() if CRON_MARKER not in line]
    payload = "\n".join(lines).rstrip()
    if payload:
        payload += "\n"
    subprocess.run(
        ["crontab", "-"],
        input=payload,
        text=True,
        check=True,
        timeout=10,
    )
    return status.installed


def install_cron(data_dir: Path, python_bin: str) -> str:
    existing_result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    existing = existing_result.stdout if existing_result.returncode == 0 else ""
    lines = [line for line in existing.splitlines() if CRON_MARKER not in line]
    line = cron_line(data_dir, python_bin)
    health_line = health_cron_line(data_dir, python_bin)
    lines.extend([line, health_line])
    payload = "\n".join(lines).rstrip() + "\n"
    subprocess.run(
        ["crontab", "-"],
        input=payload,
        text=True,
        check=True,
        timeout=10,
    )
    return line


def run_nightly_cli() -> None:
    parser = argparse.ArgumentParser(description="Run the Storage Manager nightly scan.")
    parser.add_argument("--data-dir", required=True, help="Directory for state and reports")
    parser.add_argument("--skip-detail", action="store_true", help="Run df/report only")
    parser.add_argument(
        "--force-weekly",
        action="store_true",
        help="Generate the weekly report regardless of weekday",
    )
    parser.add_argument("--print-cron", action="store_true", help="Print a 22:00 crontab line")
    parser.add_argument("--install-cron", action="store_true", help="Install/update the cron entry")
    parser.add_argument(
        "--python",
        dest="python_bin",
        default=sys.executable,
        help="Python executable used by cron",
    )
    parser.add_argument(
        "--trigger",
        choices=("command", "cron", "gui", "direct"),
        default="command",
        help="Execution source recorded in runtime status",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.print_cron:
            print(cron_line(data_dir, args.python_bin))
            print(health_cron_line(data_dir, args.python_bin))
            return
        if args.install_cron:
            print(install_cron(data_dir, args.python_bin))
            return
        run_nightly_scan(
            data_dir,
            skip_detail=args.skip_detail,
            force_weekly=args.force_weekly,
            backend=RHEL_BACKEND,
            trigger=args.trigger,
        )
    except (ConfigError, ScanAlreadyRunning, subprocess.SubprocessError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
