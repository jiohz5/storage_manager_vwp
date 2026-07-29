from __future__ import annotations

from datetime import datetime


MANAGED_SCAN_TRIGGERS = frozenset({"cron", "gui"})


def is_within_scan_window(
    current: datetime,
    start_hour: int,
    end_hour: int,
) -> bool:
    if start_hour == end_hour:
        return True
    current_minute = current.hour * 60 + current.minute
    start_minute = int(start_hour) * 60
    end_minute = int(end_hour) * 60
    if start_minute < end_minute:
        return start_minute <= current_minute < end_minute
    return current_minute >= start_minute or current_minute < end_minute


def managed_scan_window_closed(
    trigger: str,
    current: datetime,
    start_hour: int,
    end_hour: int,
) -> bool:
    return trigger in MANAGED_SCAN_TRIGGERS and not is_within_scan_window(
        current,
        start_hour,
        end_hour,
    )
