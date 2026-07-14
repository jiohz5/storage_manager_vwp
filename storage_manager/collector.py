from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class UsageSnapshot:
    fs_name: str
    total_kb: int
    used_kb: int
    avail_kb: int
    use_pct: int
    total_inodes: Optional[int] = None
    used_inodes: Optional[int] = None
    avail_inodes: Optional[int] = None
    inode_use_pct: Optional[int] = None
    quota_used_kb: Optional[int] = None
    quota_limit_kb: Optional[int] = None
    quota_use_pct: Optional[int] = None
    quota_error: str = ""


@dataclass(frozen=True)
class DetailScanResult:
    items: List[Tuple[str, int]]
    complete: bool
    duration_seconds: float
    error: str = ""
    completed_tasks: int = 0
    total_tasks: int = 0
    resumable: bool = False
    cancelled: bool = False


def parse_df_output(output: str) -> UsageSnapshot:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError(f"Unexpected df output: {output}")

    columns = lines[-1].split()
    if len(columns) < 6:
        raise ValueError(f"Cannot parse df row: {lines[-1]}")

    return UsageSnapshot(
        fs_name=columns[0],
        total_kb=int(columns[1]),
        used_kb=int(columns[2]),
        avail_kb=int(columns[3]),
        use_pct=int(columns[4].rstrip("%")),
    )


def run_df(path: str, timeout_seconds: int = 15) -> UsageSnapshot:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    result = subprocess.run(
        ["df", "-Pk", "--", path],
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout_seconds,
        env=env,
    )
    snapshot = parse_df_output(result.stdout)
    try:
        inode_result = subprocess.run(
            ["df", "-Pi", "--", path],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout_seconds,
            env=env,
        )
        inode_snapshot = parse_df_output(inode_result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return snapshot
    return UsageSnapshot(
        fs_name=snapshot.fs_name,
        total_kb=snapshot.total_kb,
        used_kb=snapshot.used_kb,
        avail_kb=snapshot.avail_kb,
        use_pct=snapshot.use_pct,
        total_inodes=inode_snapshot.total_kb,
        used_inodes=inode_snapshot.used_kb,
        avail_inodes=inode_snapshot.avail_kb,
        inode_use_pct=inode_snapshot.use_pct,
    )


def parse_du_output(output: str, base_path: str) -> List[Tuple[str, int]]:
    base = base_path.rstrip("/\\")
    items: List[Tuple[str, int]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            continue
        size_text, item_path = fields
        if item_path.rstrip("/\\") == base:
            continue
        try:
            items.append((item_path, int(size_text)))
        except ValueError:
            continue
    items.sort(key=lambda row: row[1], reverse=True)
    return items


def collect_top_level_sizes(path: str, timeout_seconds: int = 3600) -> DetailScanResult:
    started = time.monotonic()
    base = Path(path)
    if not base.is_dir():
        return DetailScanResult([], False, 0.0, f"Directory not found: {path}")

    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            ["du", "-a", "-x", "-k", "--max-depth=1", "--", str(base)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - started
        return DetailScanResult(
            [],
            False,
            duration,
            f"Detail scan exceeded {timeout_seconds} seconds",
        )
    except OSError as exc:
        return DetailScanResult([], False, time.monotonic() - started, str(exc))

    duration = time.monotonic() - started
    items = parse_du_output(result.stdout, str(base))
    if result.returncode != 0:
        return DetailScanResult(
            items,
            False,
            duration,
            f"du exited with code {result.returncode}; check read permissions",
        )
    return DetailScanResult(items, True, duration)


def ranked_items(items: Iterable[Tuple[str, int]], top_n: int) -> List[Tuple[str, int, int]]:
    ordered = sorted(items, key=lambda row: row[1], reverse=True)[:top_n]
    return [
        (item_path, size_kb, rank_no)
        for rank_no, (item_path, size_kb) in enumerate(ordered, start=1)
    ]


def delta_map(
    previous: Iterable[Tuple[str, int]],
    current: Iterable[Tuple[str, int]],
    baseline_exists: bool = True,
) -> List[Tuple[str, int]]:
    if not baseline_exists:
        return []

    previous_map: Dict[str, int] = {row[0]: int(row[1]) for row in previous}
    current_map: Dict[str, int] = {row[0]: int(row[1]) for row in current}
    all_paths = previous_map.keys() | current_map.keys()
    deltas = [
        (item_path, current_map.get(item_path, 0) - previous_map.get(item_path, 0))
        for item_path in all_paths
    ]
    deltas.sort(key=lambda row: row[1], reverse=True)
    return deltas


def usage_color(use_pct: int, failed: bool = False, alert_threshold: int = 95) -> str:
    if failed:
        return "#b6bcc5"
    if use_pct >= alert_threshold:
        return "#d9534f"
    if use_pct >= 90:
        return "#f0ad4e"
    if use_pct >= 80:
        return "#f7e463"
    return "#5cb85c"


def usage_level(use_pct: int, alert_threshold: int = 95) -> str:
    if use_pct >= alert_threshold:
        return "alert"
    if use_pct >= 90:
        return "warning"
    return "ok"


@dataclass(frozen=True)
class StorageBackend:
    name: str
    read_usage: Callable[[str, int], UsageSnapshot]
    scan_detail: Callable[[str, int], DetailScanResult]
    test_mode: bool = False


RHEL_BACKEND = StorageBackend(
    name="RHEL df/du",
    read_usage=run_df,
    scan_detail=collect_top_level_sizes,
)
