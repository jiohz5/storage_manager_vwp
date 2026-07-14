from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from storage_manager.collector import RHEL_BACKEND, StorageBackend, UsageSnapshot
from storage_manager.config import (
    Account,
    Settings,
    db_file,
    load_store,
    normalize_account_path,
)
from storage_manager.database import CapacitySampleRecord, Database
from storage_manager.i18n import tr
from storage_manager.notifications import NotificationEvent, dispatch_notifications
from storage_manager.quota import collect_quota
from storage_manager.scheduler import ProcessLock, ScanAlreadyRunning


CAPACITY_STATUS_FILENAME = "capacity_watch_status.json"
CAPACITY_LOCK_FILENAME = "capacity_watch.lock"
MAX_RATE_GAP_HOURS = 2.0
LEVEL_RANK = {"ok": 0, "warning": 1, "alert": 2, "emergency": 3, "full": 4}


@dataclass(frozen=True)
class CapacityAssessment:
    level: str
    effective_pct: int
    growth_kb: int
    rate_kb_per_hour: float
    hours_to_full: Optional[float]
    rapid_growth: bool
    recovered: bool


@dataclass(frozen=True)
class CapacityAccountResult:
    account: Account
    fs_key: str
    snapshot: UsageSnapshot
    assessment: CapacityAssessment
    quota_assessment: Optional[CapacityAssessment] = None


@dataclass(frozen=True)
class CapacityWatchResult:
    samples_written: int
    filesystems_checked: int
    events_written: int
    errors: Tuple[str, ...]
    duration_seconds: float


def capacity_status_file(data_dir: Path) -> Path:
    return data_dir / CAPACITY_STATUS_FILENAME


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp), str(path))
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def read_capacity_watch_status(data_dir: Path) -> Dict[str, object]:
    try:
        payload = json.loads(capacity_status_file(data_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {
            "state": "never",
            "started_at": "",
            "finished_at": "",
            "samples_written": 0,
            "filesystems_checked": 0,
            "events_written": 0,
            "errors": [],
        }
    return payload if isinstance(payload, dict) else {}


def _write_capacity_status(data_dir: Path, payload: Dict[str, object]) -> None:
    _atomic_json(capacity_status_file(data_dir), payload)


def _effective_pct(snapshot: UsageSnapshot) -> int:
    return max(
        value
        for value in (snapshot.use_pct, snapshot.inode_use_pct)
        if value is not None
    )


def _previous_effective_pct(previous: CapacitySampleRecord) -> int:
    return max(
        value
        for value in (previous.use_pct, previous.inode_use_pct)
        if value is not None
    )


def _capacity_level(
    effective_pct: int,
    avail_kb: int,
    hours_to_full: Optional[float],
    rapid_growth: bool,
    settings: Settings,
) -> str:
    level = "ok"
    if effective_pct >= 100 or avail_kb <= 0:
        level = "full"
    elif effective_pct >= 98:
        level = "emergency"
    elif effective_pct >= settings.alert_threshold:
        level = "alert"
    elif effective_pct >= 90:
        level = "warning"

    if hours_to_full is not None:
        if hours_to_full <= settings.forecast_emergency_hours:
            level = max(level, "emergency", key=LEVEL_RANK.get)
        elif hours_to_full <= settings.forecast_alert_hours:
            level = max(level, "alert", key=LEVEL_RANK.get)
    if rapid_growth:
        level = max(level, "alert", key=LEVEL_RANK.get)
    return level


def assess_capacity(
    current: UsageSnapshot,
    previous: Optional[CapacitySampleRecord],
    now: datetime,
    settings: Settings,
    fs_key: Optional[str] = None,
) -> CapacityAssessment:
    effective_pct = _effective_pct(current)
    valid_previous = previous
    if valid_previous is not None and fs_key is not None and valid_previous.fs_key != fs_key:
        valid_previous = None

    growth_kb = 0
    rate_kb_per_hour = 0.0
    recovered = False
    if valid_previous is not None:
        try:
            previous_time = datetime.strptime(valid_previous.ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            valid_previous = None
        else:
            elapsed_hours = (now - previous_time).total_seconds() / 3600.0
            if not 0 < elapsed_hours <= MAX_RATE_GAP_HOURS:
                valid_previous = None
            else:
                growth_kb = current.used_kb - valid_previous.used_kb
                if growth_kb > 0:
                    rate_kb_per_hour = growth_kb / elapsed_hours
                recovered = (
                    _previous_effective_pct(valid_previous) >= 90
                    and effective_pct < 90
                )

    hours_to_full = (
        current.avail_kb / rate_kb_per_hour
        if rate_kb_per_hour > 0
        else None
    )
    rapid_growth = growth_kb >= settings.rapid_growth_gb * 1024 * 1024
    level = _capacity_level(
        effective_pct,
        current.avail_kb,
        hours_to_full,
        rapid_growth,
        settings,
    )
    return CapacityAssessment(
        level=level,
        effective_pct=effective_pct,
        growth_kb=growth_kb,
        rate_kb_per_hour=rate_kb_per_hour,
        hours_to_full=hours_to_full,
        rapid_growth=rapid_growth,
        recovered=recovered,
    )


def _hours_text(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.2f}h"


def build_capacity_events(
    results: Sequence[CapacityAccountResult],
    settings: Settings,
    language: str,
) -> List[NotificationEvent]:
    events: List[NotificationEvent] = []
    groups: Dict[str, List[CapacityAccountResult]] = {}
    for result in results:
        groups.setdefault(result.fs_key, []).append(result)

    for fs_key, entries in groups.items():
        worst = max(entries, key=lambda item: LEVEL_RANK[item.assessment.level])
        accounts = ", ".join(sorted(entry.account.name for entry in entries))
        if worst.assessment.level != "ok":
            assessment = worst.assessment
            events.append(
                NotificationEvent(
                    key=f"capacity:{fs_key}",
                    level=assessment.level,
                    title=tr(
                        language,
                        "notify.capacity_watch.title",
                        level=assessment.level.upper(),
                    ),
                    message=tr(
                        language,
                        "notify.capacity_watch.message",
                        filesystem=worst.snapshot.fs_name,
                        accounts=accounts,
                        percent=assessment.effective_pct,
                        available=worst.snapshot.avail_kb,
                        growth=max(0, assessment.growth_kb),
                        rate=int(assessment.rate_kb_per_hour),
                        hours=_hours_text(assessment.hours_to_full),
                    ),
                )
            )
        elif any(entry.assessment.recovered for entry in entries):
            events.append(
                NotificationEvent(
                    key=f"capacity:{fs_key}",
                    level="recovery",
                    title=tr(language, "notify.capacity_watch.recovery_title"),
                    message=tr(
                        language,
                        "notify.capacity_watch.recovery_message",
                        filesystem=worst.snapshot.fs_name,
                        accounts=accounts,
                        percent=worst.assessment.effective_pct,
                    ),
                )
            )

        for entry in entries:
            quota = entry.quota_assessment
            if quota is None:
                continue
            if quota.level != "ok":
                events.append(
                    NotificationEvent(
                        key=f"capacity-quota:{entry.account.account_id}",
                        level=quota.level,
                        title=tr(
                            language,
                            "notify.capacity_watch.title",
                            level=quota.level.upper(),
                        ),
                        message=tr(
                            language,
                            "notify.capacity_watch.quota_message",
                            account=entry.account.name,
                            percent=quota.effective_pct,
                            available=max(
                                0,
                                int(entry.snapshot.quota_limit_kb or 0)
                                - int(entry.snapshot.quota_used_kb or 0),
                            ),
                            hours=_hours_text(quota.hours_to_full),
                        ),
                    )
                )
            elif quota.recovered:
                events.append(
                    NotificationEvent(
                        key=f"capacity-quota:{entry.account.account_id}",
                        level="recovery",
                        title=tr(language, "notify.capacity_watch.recovery_title"),
                        message=tr(
                            language,
                            "notify.capacity_watch.recovery_message",
                            filesystem=f"quota:{entry.account.name}",
                            accounts=entry.account.name,
                            percent=quota.effective_pct,
                        ),
                    )
                )
    return events


def _device_id(path: str) -> int:
    return int(os.stat(path).st_dev)


def _quota_assessment(
    account: Account,
    snapshot: UsageSnapshot,
    previous: Optional[CapacitySampleRecord],
    now: datetime,
    settings: Settings,
) -> Optional[CapacityAssessment]:
    if snapshot.quota_use_pct is None or snapshot.quota_limit_kb is None:
        return None
    quota_key = f"quota:{account.account_id}"
    current = UsageSnapshot(
        fs_name=quota_key,
        total_kb=int(snapshot.quota_limit_kb),
        used_kb=int(snapshot.quota_used_kb or 0),
        avail_kb=max(
            0,
            int(snapshot.quota_limit_kb) - int(snapshot.quota_used_kb or 0),
        ),
        use_pct=int(snapshot.quota_use_pct),
    )
    quota_previous = None
    if previous is not None and previous.quota_use_pct is not None:
        quota_previous = replace(
            previous,
            fs_key=quota_key,
            fs_name=quota_key,
            total_kb=int(previous.quota_limit_kb or 0),
            used_kb=int(previous.quota_used_kb or 0),
            avail_kb=max(
                0,
                int(previous.quota_limit_kb or 0) - int(previous.quota_used_kb or 0),
            ),
            use_pct=int(previous.quota_use_pct),
            inode_use_pct=None,
        )
    return assess_capacity(current, quota_previous, now, settings, quota_key)


def run_capacity_watch(
    data_dir: Path,
    backend: StorageBackend = RHEL_BACKEND,
    now_override: Optional[datetime] = None,
    device_reader: Callable[[str], int] = _device_id,
) -> CapacityWatchResult:
    data_dir = Path(data_dir).expanduser().resolve()
    started = time.monotonic()
    now = now_override or datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    run_id = uuid.uuid4().hex
    initial_status: Dict[str, object] = {
        "state": "running",
        "run_id": run_id,
        "pid": os.getpid(),
        "started_at": ts,
        "finished_at": "",
        "samples_written": 0,
        "filesystems_checked": 0,
        "events_written": 0,
        "errors": [],
    }
    try:
        with ProcessLock(data_dir / CAPACITY_LOCK_FILENAME):
            _write_capacity_status(data_dir, initial_status)
            store = load_store(data_dir)
            groups: Dict[int, List[Tuple[Account, str]]] = {}
            errors: List[str] = []
            for account in store.accounts:
                if not account.enabled:
                    continue
                try:
                    normalized = normalize_account_path(
                        account.path,
                        store.settings.monitored_roots,
                        require_exists=True,
                    )
                    device = int(device_reader(normalized))
                except Exception as exc:
                    errors.append(f"{account.name}: {exc}")
                    continue
                groups.setdefault(device, []).append((account, normalized))

            db = Database(db_file(data_dir))
            results: List[CapacityAccountResult] = []
            filesystems_checked = 0
            samples_written = 0
            try:
                for device, entries in groups.items():
                    representative = entries[0][1]
                    try:
                        base_snapshot = backend.read_usage(
                            representative,
                            store.settings.df_timeout_seconds,
                        )
                    except Exception as exc:
                        names = ", ".join(account.name for account, _ in entries)
                        errors.append(f"{names}: {exc}")
                        continue
                    filesystems_checked += 1
                    fs_key = f"{device}:{base_snapshot.fs_name}"
                    for account, normalized in entries:
                        previous = db.latest_capacity_sample(account.account_id)
                        snapshot = base_snapshot
                        try:
                            quota = collect_quota(
                                store.settings.quota_command,
                                account.name,
                                normalized,
                                store.settings.quota_timeout_seconds,
                            )
                        except Exception as exc:
                            snapshot = replace(snapshot, quota_error=str(exc))
                        else:
                            if quota is not None:
                                snapshot = replace(
                                    snapshot,
                                    quota_used_kb=quota.used_kb,
                                    quota_limit_kb=quota.limit_kb,
                                    quota_use_pct=quota.use_pct,
                                )
                        assessment = assess_capacity(
                            base_snapshot,
                            previous,
                            now,
                            store.settings,
                            fs_key,
                        )
                        quota_assessment = _quota_assessment(
                            account,
                            snapshot,
                            previous,
                            now,
                            store.settings,
                        )
                        db.add_capacity_sample(
                            CapacitySampleRecord(
                                ts=ts,
                                account_id=account.account_id,
                                account_name=account.name,
                                account_path=normalized,
                                fs_key=fs_key,
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
                            )
                        )
                        samples_written += 1
                        results.append(
                            CapacityAccountResult(
                                account,
                                fs_key,
                                snapshot,
                                assessment,
                                quota_assessment,
                            )
                        )
                events = build_capacity_events(
                    results,
                    store.settings,
                    store.settings.language,
                )
                delivery = dispatch_notifications(
                    data_dir,
                    store.settings,
                    events,
                    now,
                )
                db.purge_capacity_samples(store.settings.capacity_sample_days, now)
                db.checkpoint()
            finally:
                db.close()

            duration = time.monotonic() - started
            result = CapacityWatchResult(
                samples_written=samples_written,
                filesystems_checked=filesystems_checked,
                events_written=delivery.sent,
                errors=tuple(errors),
                duration_seconds=duration,
            )
            _write_capacity_status(
                data_dir,
                {
                    **initial_status,
                    "state": "succeeded",
                    "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "samples_written": result.samples_written,
                    "filesystems_checked": result.filesystems_checked,
                    "events_written": result.events_written,
                    "errors": list(result.errors),
                    "duration_seconds": round(result.duration_seconds, 3),
                },
            )
            return result
    except ScanAlreadyRunning:
        raise
    except Exception as exc:
        _write_capacity_status(
            data_dir,
            {
                **initial_status,
                "state": "failed",
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "errors": [str(exc)],
                "duration_seconds": round(time.monotonic() - started, 3),
            },
        )
        raise
