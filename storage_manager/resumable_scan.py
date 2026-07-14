from __future__ import annotations

import os
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from storage_manager.collector import DetailScanResult
from storage_manager.database import Database


Task = Tuple[str, str, str, int, str, int]


class TaskTimeout(RuntimeError):
    pass


class TaskCancelled(RuntimeError):
    pass


def _allocated_kb(path: Path) -> int:
    stat = os.stat(str(path), follow_symlinks=False)
    blocks = int(getattr(stat, "st_blocks", 0))
    if blocks:
        return max(1, blocks // 2)
    return max(1, (int(stat.st_size) + 1023) // 1024)


def initial_tasks(account_path: str) -> List[Task]:
    base = Path(account_path)
    tasks: List[Task] = []
    for child in base.iterdir():
        child_path = str(child)
        if child.is_dir() and not child.is_symlink():
            tasks.append((child_path, child_path, "scan", 0, "pending", 0))
        else:
            tasks.append(
                (
                    child_path,
                    child_path,
                    "direct",
                    0,
                    "complete",
                    _allocated_kb(child),
                )
            )
    return tasks


def split_directory_task(task_path: str, top_path: str, depth: int) -> List[Task]:
    directory = Path(task_path)
    direct_size_kb = _allocated_kb(directory)
    child_tasks: List[Task] = []
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            child_tasks.append(
                (top_path, str(child), "scan", depth + 1, "pending", 0)
            )
        else:
            direct_size_kb += _allocated_kb(child)
    child_tasks.append(
        (top_path, task_path, "direct", depth, "complete", direct_size_kb)
    )
    return child_tasks


def run_du_task(
    path: str,
    timeout_seconds: int,
    stop_requested: Callable[[], bool] = lambda: False,
) -> int:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        process = subprocess.Popen(
            ["du", "-sk", "--", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )
    except OSError:
        raise

    deadline = time.monotonic() + timeout_seconds
    while True:
        if stop_requested():
            try:
                process.kill()
            except OSError:
                pass
            process.communicate()
            raise TaskCancelled(path)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            try:
                process.kill()
            except OSError:
                pass
            process.communicate()
            raise TaskTimeout(path)
        try:
            stdout, _ = process.communicate(timeout=min(0.5, remaining))
            break
        except subprocess.TimeoutExpired:
            continue

    if process.returncode != 0:
        raise RuntimeError(f"du failed for {path} with code {process.returncode}")
    try:
        return int(stdout.split(maxsplit=1)[0])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(f"Cannot parse du result for {path}") from exc


def run_resumable_baseline(
    db: Database,
    account_id: str,
    account_path: str,
    budget_seconds: Optional[int],
    task_timeout_seconds: int = 900,
    max_split_depth: int = 12,
    du_runner: Callable[[str, int], int] = run_du_task,
    stop_requested: Callable[[], bool] = lambda: False,
) -> DetailScanResult:
    started = time.monotonic()
    state = db.detail_scan_state(account_id)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if state is None or state[0] != account_path:
        cycle_id = uuid.uuid4().hex
        try:
            tasks = initial_tasks(account_path)
        except OSError as exc:
            return DetailScanResult([], False, 0.0, str(exc), resumable=True)
        db.begin_detail_scan(
            account_id,
            account_path,
            cycle_id,
            timestamp,
            tasks,
        )
    else:
        cycle_id = str(state[1])

    while True:
        completed, total = db.detail_scan_progress(account_id, cycle_id)
        if stop_requested():
            return DetailScanResult(
                db.detail_scan_items(account_id, cycle_id),
                False,
                time.monotonic() - started,
                "stop requested",
                completed,
                total,
                True,
                True,
            )
        task = db.next_detail_task(account_id, cycle_id)
        remaining = (
            None
            if budget_seconds is None
            else budget_seconds - (time.monotonic() - started)
        )
        if remaining is not None and remaining < 2:
            return DetailScanResult(
                db.detail_scan_items(account_id, cycle_id),
                False,
                time.monotonic() - started,
                completed_tasks=completed,
                total_tasks=total,
                resumable=True,
            )

        if task is None:
            try:
                current_tasks = initial_tasks(account_path)
            except OSError as exc:
                return DetailScanResult(
                    db.detail_scan_items(account_id, cycle_id),
                    False,
                    time.monotonic() - started,
                    str(exc),
                    completed,
                    total,
                    True,
                )
            if db.reconcile_detail_scan_roots(
                account_id,
                cycle_id,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                current_tasks,
            ):
                continue
            items = db.detail_scan_items(account_id, cycle_id)
            return DetailScanResult(
                items,
                True,
                time.monotonic() - started,
                completed_tasks=completed,
                total_tasks=total,
                resumable=True,
            )

        task_id, top_path, task_path, depth = task
        timeout = (
            task_timeout_seconds
            if remaining is None
            else min(task_timeout_seconds, max(1, int(remaining)))
        )
        try:
            if du_runner is run_du_task:
                size_kb = run_du_task(str(task_path), timeout, stop_requested)
            else:
                size_kb = du_runner(str(task_path), timeout)
        except TaskCancelled:
            return DetailScanResult(
                db.detail_scan_items(account_id, cycle_id),
                False,
                time.monotonic() - started,
                "stop requested",
                completed,
                total,
                True,
                True,
            )
        except TaskTimeout:
            if int(depth) >= max_split_depth:
                return DetailScanResult(
                    db.detail_scan_items(account_id, cycle_id),
                    False,
                    time.monotonic() - started,
                    f"Timed out at maximum split depth: {task_path}",
                    completed,
                    total,
                    True,
                )
            try:
                split_tasks = split_directory_task(
                    str(task_path),
                    str(top_path),
                    int(depth),
                )
            except FileNotFoundError:
                db.complete_detail_task(
                    int(task_id),
                    account_id,
                    0,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
            except OSError as exc:
                return DetailScanResult(
                    db.detail_scan_items(account_id, cycle_id),
                    False,
                    time.monotonic() - started,
                    str(exc),
                    completed,
                    total,
                    True,
                )
            else:
                db.split_detail_task(
                    int(task_id),
                    account_id,
                    cycle_id,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    split_tasks,
                )
        except (OSError, RuntimeError) as exc:
            return DetailScanResult(
                db.detail_scan_items(account_id, cycle_id),
                False,
                time.monotonic() - started,
                str(exc),
                completed,
                total,
                True,
            )
        else:
            db.complete_detail_task(
                int(task_id),
                account_id,
                size_kb,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
