"""데이터 폴더에 무엇이, 얼마나, 언제부터 쌓였나 (읽기 전용, Qt 없음).

이 도구는 데이터 디렉터리 하나에 모든 것을 남긴다 - 15분 사용률 표본, 상세 스캔
결과, 검색 인덱스, 알림, 보고서, 로그. 무엇이 자리를 차지하는지, 언제부터
모였는지, 무엇이 저절로 정리되고 무엇은 쌓이기만 하는지를 한 장으로 본다.

**아무것도 지우지 않는다.** 보기만 한다. 오래된 것은 각자의 보관 규칙대로 수집과
스캔이 정리한다 (`보관` 칸).

데이터 디렉터리가 NFS 면 행을 세는 것만으로 몇 초가 걸릴 수 있다. 그래서 분류
하나씩 읽으며 진행을 알리고(`on_step`), 분류 하나가 실패해도 나머지는 보여 준다.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

from . import config as config_module, popup_queue, sqlite_db, usage_log

logger = logging.getLogger(__name__)

# 보관 규칙의 종류. 화면이 자기 언어로 말한다.
KEEP_DAYS = "days"              # 값: 일 수
KEEP_GENERATIONS = "generations"  # 값: 남기는 세대 수
KEEP_FOREVER = "forever"        # 지우지 않는다
KEEP_CAPPED = "capped"          # 크기 상한으로 돌려 쓴다 (값: 바이트)
KEEP_CURRENT = "current"        # 지금 상태 하나만 (설정, 잠금 등)
KEEP_PER_ITEM = "per_item"      # 안에 든 것마다 다르다 (아래 줄)


@dataclass
class Item:
    """한 줄. `key` 는 무엇인지 말하는 i18n 키다."""

    key: str
    size_bytes: Optional[int] = None   # 파일 단위로만 안다 - DB 안의 종류는 None
    count: Optional[int] = None
    count_unit: str = "rows"           # "rows" | "files" | "accounts"
    oldest: Optional[str] = None       # ISO (UTC)
    keep: str = KEEP_CURRENT
    keep_value: Optional[int] = None
    failed: bool = False
    children: List["Item"] = field(default_factory=list)


@dataclass
class Inventory:
    data_dir: str
    items: List[Item] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(item.size_bytes or 0 for item in self.items)

    @property
    def since(self) -> Optional[str]:
        """가장 오래된 데이터의 시각 - '언제부터 모았나'."""

        stamps = [
            stamp
            for item in self.items
            for stamp in [item.oldest] + [child.oldest for child in item.children]
            if stamp
        ]
        return min(stamps, key=_sort_key) if stamps else None


# 읽는 순서 (i18n 키). 화면이 "무엇을 읽는 중 (3/9)" 로 보여 준다.
STEPS = (
    "data.item.config",
    "data.item.samples",
    "data.item.scan",
    "data.item.search",
    "data.item.outbox",
    "data.item.audit",
    "data.item.reports",
    "data.item.logs",
    "data.item.state",
)


def build(data_dir: Path, config, on_step: Optional[Callable[[int, int, str], None]] = None) -> Inventory:
    """데이터 폴더를 한 장으로. 분류 하나가 실패해도 나머지는 낸다."""

    data_dir = Path(data_dir)
    settings = config.settings
    readers = {
        "data.item.config": lambda: _config_item(data_dir, config),
        "data.item.samples": lambda: _samples_item(data_dir, settings),
        "data.item.scan": lambda: _scan_item(data_dir, settings),
        "data.item.search": lambda: _search_item(data_dir),
        "data.item.outbox": lambda: _folder_item(
            "data.item.outbox", data_dir / "outbox", KEEP_DAYS, popup_queue.EVENT_RETENTION_DAYS
        ),
        "data.item.audit": lambda: _folder_item(
            "data.item.audit", data_dir / "notify_audit", KEEP_FOREVER, None
        ),
        "data.item.reports": lambda: _folder_item(
            "data.item.reports", data_dir / "reports", KEEP_DAYS, settings.report_retention_days
        ),
        "data.item.logs": lambda: _logs_item(data_dir),
        "data.item.state": lambda: _state_item(data_dir),
    }
    inventory = Inventory(data_dir=str(data_dir))
    for index, key in enumerate(STEPS, start=1):
        if on_step is not None:
            on_step(index, len(STEPS), key)
        try:
            item = readers[key]()
        except Exception:
            logger.warning("데이터 폴더의 '%s' 를 읽지 못했습니다", key, exc_info=True)
            item = Item(key=key, failed=True)
        if item is not None:
            inventory.items.append(item)
    return inventory


# -- 분류마다 ----------------------------------------------------------------

def _config_item(data_dir: Path, config) -> Item:
    path = config_module.config_file(data_dir)
    return Item(
        key="data.item.config",
        size_bytes=_size(path),
        count=len(config.accounts),
        count_unit="accounts",
        keep=KEEP_CURRENT,
    )


def _samples_item(data_dir: Path, settings) -> Item:
    from . import store

    path = store.db_path(data_dir)
    item = Item(key="data.item.samples", size_bytes=_db_size(path),
                keep=KEEP_DAYS, keep_value=settings.sample_retention_days)
    if not path.exists():
        return item
    with store.session(data_dir) as conn:
        item.count, item.oldest = _count_and_oldest(conn, "samples", "collected_at")
    return item


def _scan_item(data_dir: Path, settings) -> Item:
    from . import scan_store

    path = scan_store.db_path(data_dir)
    item = Item(key="data.item.scan", size_bytes=_db_size(path), keep=KEEP_PER_ITEM)
    if not path.exists():
        return item
    with scan_store.session(data_dir) as conn:
        runs, first_run = _count_and_oldest(conn, "scan_runs", "started_at")
        results, _ = _count_and_oldest(conn, "baseline_results", None)
        checkpoints, _ = _count_and_oldest(conn, "scan_checkpoints", None)
        large, _ = _count_and_oldest(conn, "baseline_large_files", None)
        loads, first_load = _count_and_oldest(conn, "scan_load_samples", "sampled_at")
        servers, first_server = _count_and_oldest(conn, "server_samples", "sampled_at")
        usage, first_usage = _count_and_oldest(conn, "usage_events", "happened_at")
    keep_generations = settings.detail_scan_keep_generations
    item.children = [
        Item(key="data.scan.results", count=results, oldest=first_run,
             keep=KEEP_GENERATIONS, keep_value=keep_generations),
        Item(key="data.scan.checkpoints", count=checkpoints,
             keep=KEEP_GENERATIONS, keep_value=keep_generations),
        Item(key="data.scan.large_files", count=large,
             keep=KEEP_GENERATIONS, keep_value=keep_generations),
        Item(key="data.scan.runs", count=runs, oldest=first_run, keep=KEEP_FOREVER),
        Item(key="data.scan.load_samples", count=loads, oldest=first_load,
             keep=KEEP_DAYS, keep_value=settings.load_sample_retention_days),
        Item(key="data.scan.server_samples", count=servers, oldest=first_server,
             keep=KEEP_DAYS, keep_value=settings.server_sample_retention_days),
        Item(key="data.scan.usage", count=usage, oldest=first_usage,
             keep=KEEP_DAYS, keep_value=usage_log.DEFAULT_RETENTION_DAYS),
    ]
    item.oldest = first_run
    return item


def _search_item(data_dir: Path) -> Item:
    from . import search_index

    path = search_index.db_path(data_dir)
    item = Item(key="data.item.search", size_bytes=_db_size(path), keep=KEEP_CURRENT)
    if not path.exists():
        return item
    with search_index.session(data_dir) as conn:
        item.count, _ = _count_and_oldest(conn, "search_entries", None)
    return item


def _folder_item(key: str, folder: Path, keep: str, keep_value: Optional[int]) -> Item:
    files, size, oldest = _walk(folder)
    return Item(key=key, size_bytes=size, count=files, count_unit="files",
                oldest=oldest, keep=keep, keep_value=keep_value)


def _logs_item(data_dir: Path) -> Item:
    from . import applog

    files, size, oldest = _walk(data_dir / "logs")
    # cron 로그는 덧붙이기만 하고(setup_cron.csh), 창의 기록은 크기 상한으로 돌려 쓴다.
    return Item(key="data.item.logs", size_bytes=size, count=files, count_unit="files",
                oldest=oldest, keep=KEEP_CAPPED,
                keep_value=applog.MAX_BYTES * (applog.BACKUP_COUNT + 1))


def _state_item(data_dir: Path) -> Item:
    names = ("notify_state.json", "popup_read_state.json",
             "nightly_scan.lock", "nightly_scan_stop.json")
    present = [data_dir / name for name in names if (data_dir / name).exists()]
    return Item(key="data.item.state", size_bytes=sum(_size(p) for p in present),
                count=len(present), count_unit="files", keep=KEEP_CURRENT)


# -- 재는 도구 ---------------------------------------------------------------

def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _db_size(path: Path) -> int:
    """DB 파일과 그 곁 파일(저널 등)을 합친 크기."""

    return sum(_size(Path(str(path) + suffix)) for suffix in ("", "-journal", "-wal", "-shm"))


def _count_and_oldest(conn, table: str, stamp_column: Optional[str]):
    try:
        if stamp_column:
            row = conn.execute(f"SELECT COUNT(*), MIN({stamp_column}) FROM {table}").fetchone()
            return row[0], row[1]
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], None
    except sqlite3.OperationalError as exc:
        if sqlite_db.is_busy(exc):
            raise
        return None, None   # 그 표가 아직 없다 (예전 DB)


def _walk(folder: Path):
    """`(파일 수, 바이트, 가장 오래된 파일 시각)`. 폴더가 없으면 0."""

    files = size = 0
    oldest = None
    if not folder.exists():
        return 0, 0, None
    for directory, _dirs, names in os.walk(folder):
        for name in names:
            try:
                info = os.stat(os.path.join(directory, name))
            except OSError:
                continue   # 세는 사이에 지워졌다
            files += 1
            size += info.st_size
            if oldest is None or info.st_mtime < oldest:
                oldest = info.st_mtime
    stamp = datetime.fromtimestamp(oldest, tz=timezone.utc).isoformat() if oldest else None
    return files, size, stamp


def _sort_key(stamp: str):
    try:
        value = datetime.fromisoformat(stamp)
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
