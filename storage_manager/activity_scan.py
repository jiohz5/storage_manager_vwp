from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Tuple


@dataclass(frozen=True)
class ActivityScanResult:
    items: List[Tuple[str, int, int, float]]
    complete: bool
    duration_seconds: float
    files_seen: int = 0
    error: str = ""
    cancelled: bool = False


def _top_level_path(base: Path, file_path: str) -> str:
    candidate = Path(file_path)
    try:
        relative = candidate.relative_to(base)
    except ValueError:
        return str(base)
    return str(base / relative.parts[0]) if relative.parts else str(base)


def scan_changed_file_activity(
    account_path: str,
    since_timestamp: str,
    timeout_seconds: int,
    stop_requested: Callable[[], bool] = lambda: False,
) -> ActivityScanResult:
    started = time.monotonic()
    base = Path(account_path)
    if not base.is_dir():
        return ActivityScanResult([], False, 0.0, error=f"Directory not found: {base}")
    if stop_requested():
        return ActivityScanResult([], False, 0.0, error="stop requested", cancelled=True)

    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        process = subprocess.Popen(
            [
                "find",
                str(base),
                "-xdev",
                "-type",
                "f",
                "-newermt",
                since_timestamp,
                "-printf",
                "%s\\t%T@\\t%p\\0",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except OSError as exc:
        return ActivityScanResult([], False, time.monotonic() - started, error=str(exc))

    timed_out = threading.Event()
    cancelled = threading.Event()
    monitor_done = threading.Event()

    def terminate() -> None:
        timed_out.set()
        try:
            process.kill()
        except OSError:
            pass

    timer = threading.Timer(timeout_seconds, terminate)
    timer.daemon = True
    timer.start()

    def monitor_stop() -> None:
        while not monitor_done.wait(0.25):
            if stop_requested():
                cancelled.set()
                try:
                    process.kill()
                except OSError:
                    pass
                return

    monitor = threading.Thread(target=monitor_stop, daemon=True)
    monitor.start()
    aggregates: Dict[str, List[float]] = {}
    buffer = b""
    files_seen = 0
    try:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(64 * 1024)
            if not chunk:
                break
            buffer += chunk
            records = buffer.split(b"\0")
            buffer = records.pop()
            for record in records:
                fields = record.split(b"\t", 2)
                if len(fields) != 3:
                    continue
                try:
                    size_bytes = int(fields[0])
                    modified_at = float(fields[1])
                except ValueError:
                    continue
                file_path = os.fsdecode(fields[2])
                top_path = _top_level_path(base, file_path)
                aggregate = aggregates.setdefault(top_path, [0.0, 0.0, 0.0])
                aggregate[0] += size_bytes
                aggregate[1] += 1
                aggregate[2] = max(aggregate[2], modified_at)
                files_seen += 1
        return_code = process.wait()
    finally:
        timer.cancel()
        monitor_done.set()
        monitor.join(timeout=1.0)
        if process.stdout is not None:
            process.stdout.close()

    duration = time.monotonic() - started
    items = [
        (path, int(values[0]), int(values[1]), float(values[2]))
        for path, values in aggregates.items()
    ]
    items.sort(key=lambda row: row[1], reverse=True)
    if cancelled.is_set():
        return ActivityScanResult(
            items,
            False,
            duration,
            files_seen,
            "stop requested",
            True,
        )
    if timed_out.is_set():
        return ActivityScanResult(
            items,
            False,
            duration,
            files_seen,
            f"Changed-file scan exceeded {timeout_seconds} seconds",
        )
    if return_code != 0:
        return ActivityScanResult(
            items,
            False,
            duration,
            files_seen,
            f"find exited with code {return_code}; check read permissions",
        )
    return ActivityScanResult(items, True, duration, files_seen)
