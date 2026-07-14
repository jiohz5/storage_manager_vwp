from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from storage_manager.analytics import CapacityForecast, GrowthAnomaly
from storage_manager.collector import usage_level
from storage_manager.config import Account, Settings, reports_dir
from storage_manager.database import Database
from storage_manager.i18n import tr
from storage_manager.i18n import SUPPORTED_LANGUAGES


@dataclass
class AccountReport:
    account: Account
    use_pct: Optional[int] = None
    used_kb: Optional[int] = None
    total_kb: Optional[int] = None
    used_delta_kb: Optional[int] = None
    status: str = "OK"
    detail_status: str = "not run"
    growth: List[Tuple[str, int]] = field(default_factory=list)
    activity: List[Tuple[str, int, int, float]] = field(default_factory=list)
    inode_use_pct: Optional[int] = None
    quota_used_kb: Optional[int] = None
    quota_limit_kb: Optional[int] = None
    quota_use_pct: Optional[int] = None
    quota_error: str = ""
    forecast_7: Optional[CapacityForecast] = None
    forecast_30: Optional[CapacityForecast] = None
    anomaly: Optional[GrowthAnomaly] = None


def _forecast_text(forecast: Optional[CapacityForecast], language: str) -> str:
    if forecast is None:
        return tr(language, "file.forecast.unavailable")
    alert_days = (
        tr(language, "file.forecast.now")
        if forecast.days_to_alert == 0
        else str(forecast.days_to_alert)
        if forecast.days_to_alert is not None
        else "-"
    )
    full_days = (
        tr(language, "file.forecast.now")
        if forecast.days_to_full == 0
        else str(forecast.days_to_full)
        if forecast.days_to_full is not None
        else "-"
    )
    return tr(
        language,
        "file.forecast.value",
        slope=forecast.slope_pct_per_day,
        alert=alert_days,
        full=full_days,
    )


def human_kb(value_kb: Optional[int]) -> str:
    if value_kb is None:
        return "-"
    value = float(abs(value_kb))
    units = ["KB", "MB", "GB", "TB", "PB"]
    unit = units[0]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            break
        value /= 1024.0
    sign = "-" if value_kb < 0 else ""
    return f"{sign}{value:.2f} {unit}"


def human_bytes(value_bytes: int) -> str:
    return human_kb(int(value_bytes / 1024))


def format_mtime(value: float) -> str:
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp), str(path))
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def build_daily_report(
    day: str,
    generated_at: str,
    entries: Iterable[AccountReport],
    alert_threshold: int,
    language: str = "en",
) -> str:
    rows = list(entries)
    lines = [
        tr(language, "file.daily_title"),
        tr(language, "file.day", day=day),
        tr(language, "file.generated", value=generated_at),
        tr(language, "file.warning_threshold"),
        tr(language, "file.threshold", value=alert_threshold),
        "",
    ]
    if not rows:
        lines.append(tr(language, "file.no_accounts"))

    for entry in rows:
        pct = "-" if entry.use_pct is None else f"{entry.use_pct}%"
        if entry.status != "OK":
            level = tr(language, "file.level.error")
        elif any(
            value is not None
            for value in (entry.use_pct, entry.inode_use_pct, entry.quota_use_pct)
        ):
            effective_pct = max(
                value
                for value in (entry.use_pct, entry.inode_use_pct, entry.quota_use_pct)
                if value is not None
            )
            level = tr(
                language,
                f"file.level.{usage_level(effective_pct, alert_threshold)}",
            )
        else:
            level = tr(language, "file.level.ok")
        delta = "-" if entry.used_delta_kb is None else f"{entry.used_delta_kb:+,} KB ({human_kb(entry.used_delta_kb)})"
        lines.extend(
            [
                f"[{level}] {entry.account.name}",
                tr(language, "file.path", value=entry.account.path),
                tr(
                    language,
                    "file.usage",
                    pct=pct,
                    used=human_kb(entry.used_kb),
                    total=human_kb(entry.total_kb),
                ),
                tr(language, "file.fs_change", value=delta),
                tr(
                    language,
                    "file.inode_usage",
                    value="-" if entry.inode_use_pct is None else f"{entry.inode_use_pct}%",
                ),
                tr(
                    language,
                    "file.quota_usage",
                    value=(
                        tr(language, "file.quota.not_configured")
                        if entry.quota_use_pct is None and not entry.quota_error
                        else tr(language, "file.quota.error", error=entry.quota_error)
                        if entry.quota_error
                        else f"{entry.quota_use_pct}% ({human_kb(entry.quota_used_kb)} / {human_kb(entry.quota_limit_kb)})"
                    ),
                ),
                tr(language, "file.forecast.7", value=_forecast_text(entry.forecast_7, language)),
                tr(language, "file.forecast.30", value=_forecast_text(entry.forecast_30, language)),
                tr(language, "file.detail", value=entry.detail_status),
            ]
        )
        if entry.anomaly is not None and entry.anomaly.detected:
            lines.append(
                tr(
                    language,
                    "file.anomaly",
                    latest=human_kb(entry.anomaly.latest_delta_kb),
                    baseline=human_kb(entry.anomaly.baseline_median_kb),
                )
            )
        if entry.status != "OK":
            lines.append(tr(language, "file.error", value=entry.status))
        if entry.growth:
            lines.append(tr(language, "file.largest_growth"))
            for item_path, delta_kb in entry.growth:
                lines.append(f"    {human_kb(delta_kb):>12}  {item_path}")
        if entry.activity:
            lines.append(tr(language, "file.changed_activity"))
            for item_path, changed_bytes, file_count, newest_mtime in entry.activity:
                lines.append(
                    tr(
                        language,
                        "file.activity_row",
                        size=human_bytes(changed_bytes),
                        count=file_count,
                        modified=format_mtime(newest_mtime),
                        path=item_path,
                    )
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_daily_report(
    data_dir: Path,
    day: str,
    generated_at: str,
    entries: Iterable[AccountReport],
    alert_threshold: int,
    language: str = "en",
) -> Path:
    materialized = list(entries)
    contents = {
        code: build_daily_report(
            day,
            generated_at,
            materialized,
            alert_threshold,
            code,
        )
        for code in SUPPORTED_LANGUAGES
    }
    content = contents.get(language, contents["en"])
    root = reports_dir(data_dir)
    dated = root / "daily" / f"{day}.txt"
    _atomic_write(dated, content)
    _atomic_write(root / "latest_daily.txt", content)
    for code, translated in contents.items():
        _atomic_write(root / "daily" / f"{day}_{code}.txt", translated)
        _atomic_write(root / f"latest_daily_{code}.txt", translated)
    return dated


def build_weekly_report(
    db: Database,
    accounts: Iterable[Account],
    end_day: str,
    alert_threshold: int,
    language: str = "en",
) -> str:
    end_date = datetime.strptime(end_day, "%Y-%m-%d").date()
    start_date = end_date - timedelta(days=6)
    start_day = start_date.isoformat()
    lines = [
        tr(language, "file.weekly_title"),
        tr(language, "file.period", start=start_day, end=end_day),
        tr(language, "file.warning_threshold"),
        tr(language, "file.threshold", value=alert_threshold),
        "",
    ]

    for account in accounts:
        rows = db.daily_snapshots_between(account.account_id, start_day, end_day)
        if not rows:
            lines.extend(
                [
                    f"[{tr(language, 'file.level.no_data')}] {account.name}",
                    tr(language, "file.path", value=account.path),
                    "",
                ]
            )
            continue

        first = rows[0]
        last = rows[-1]
        used_delta = int(last[1]) - int(first[1])
        peak_pct = max(int(row[3]) for row in rows)
        inode_values = [int(row[9]) for row in rows if row[9] is not None]
        quota_values = [int(row[12]) for row in rows if row[12] is not None]
        effective_peak = max([peak_pct, *inode_values, *quota_values])
        level = tr(
            language,
            f"file.level.{usage_level(effective_peak, alert_threshold)}",
        )
        lines.extend(
            [
                f"[{level}] {account.name}",
                tr(language, "file.path", value=account.path),
                tr(language, "file.samples", count=len(rows)),
                tr(language, "file.peak", value=peak_pct),
                tr(
                    language,
                    "file.inode_peak",
                    value=f"{max(inode_values)}%" if inode_values else "-",
                ),
                tr(
                    language,
                    "file.quota_peak",
                    value=f"{max(quota_values)}%" if quota_values else "-",
                ),
                tr(
                    language,
                    "file.fs_change",
                    value=f"{used_delta:+,} KB ({human_kb(used_delta)})",
                ),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_weekly_report(
    data_dir: Path,
    db: Database,
    accounts: Iterable[Account],
    end_day: str,
    alert_threshold: int,
    language: str = "en",
) -> Path:
    materialized = list(accounts)
    contents = {
        code: build_weekly_report(
            db,
            materialized,
            end_day,
            alert_threshold,
            code,
        )
        for code in SUPPORTED_LANGUAGES
    }
    content = contents.get(language, contents["en"])
    root = reports_dir(data_dir)
    dated = root / "weekly" / f"{end_day}.txt"
    _atomic_write(dated, content)
    _atomic_write(root / "latest_weekly.txt", content)
    for code, translated in contents.items():
        _atomic_write(root / "weekly" / f"{end_day}_{code}.txt", translated)
        _atomic_write(root / f"latest_weekly_{code}.txt", translated)
    return dated


def build_cleanup_report(
    db: Database,
    accounts: Iterable[Account],
    day: str,
    settings: Settings,
    language: str = "en",
) -> str:
    current = datetime.strptime(day, "%Y-%m-%d").date()
    cutoff = (current - timedelta(days=settings.cleanup_inactive_days)).isoformat()
    account_by_id = {account.account_id: account for account in accounts}
    candidates = db.cleanup_candidates(
        cutoff,
        settings.cleanup_min_size_gb * 1024 * 1024,
    )
    lines = [
        tr(language, "file.cleanup_title"),
        tr(language, "file.day", day=day),
        tr(
            language,
            "file.cleanup.criteria",
            size=settings.cleanup_min_size_gb,
            days=settings.cleanup_inactive_days,
        ),
        tr(language, "file.cleanup.warning"),
        "",
    ]
    if not candidates:
        lines.append(tr(language, "file.cleanup.none"))
    for account_id, item_path, size_kb, first_seen, last_activity in candidates:
        account = account_by_id.get(account_id)
        if account is None:
            continue
        lines.extend(
            [
                f"[REVIEW] {account.name}",
                tr(language, "file.path", value=item_path),
                tr(language, "file.cleanup.size", value=human_kb(int(size_kb))),
                tr(language, "file.cleanup.first", value=first_seen),
                tr(
                    language,
                    "file.cleanup.activity",
                    value=last_activity or tr(language, "file.cleanup.activity_none"),
                ),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def write_cleanup_report(
    data_dir: Path,
    db: Database,
    accounts: Iterable[Account],
    day: str,
    settings: Settings,
    language: str = "en",
) -> Path:
    materialized = list(accounts)
    contents = {
        code: build_cleanup_report(db, materialized, day, settings, code)
        for code in SUPPORTED_LANGUAGES
    }
    content = contents.get(language, contents["en"])
    root = reports_dir(data_dir)
    dated = root / "cleanup" / f"{day}.txt"
    _atomic_write(dated, content)
    _atomic_write(root / "latest_cleanup.txt", content)
    for code, translated in contents.items():
        _atomic_write(root / "cleanup" / f"{day}_{code}.txt", translated)
        _atomic_write(root / f"latest_cleanup_{code}.txt", translated)
    return dated


def purge_old_reports(data_dir: Path, keep_days: int, today: Optional[date] = None) -> None:
    cutoff = (today or date.today()) - timedelta(days=keep_days)
    root = reports_dir(data_dir)
    for category in ("daily", "weekly", "cleanup"):
        directory = root / category
        if not directory.exists():
            continue
        for path in directory.glob("*.txt"):
            try:
                report_day = datetime.strptime(path.stem.split("_", 1)[0], "%Y-%m-%d").date()
            except ValueError:
                continue
            if report_day < cutoff:
                path.unlink()
