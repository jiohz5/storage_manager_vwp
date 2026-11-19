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
    count_more: bool = False           # 행이 `count`(= COUNT_CAP) 보다 많다 - 끝까지 세지 않았다
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
        item.count, item.count_more, item.oldest = _count_and_oldest(conn, "samples", "collected_at")
    return item


def _scan_item(data_dir: Path, settings) -> Item:
    from . import scan_store

    path = scan_store.db_path(data_dir)
    item = Item(key="data.item.scan", size_bytes=_db_size(path), keep=KEEP_PER_ITEM)
    if not path.exists():
        return item
    with scan_store.session(data_dir) as conn:
        runs = _count_and_oldest(conn, "scan_runs", "started_at")
        results = _count_and_oldest(conn, "baseline_results", None)
        checkpoints = _count_and_oldest(conn, "scan_checkpoints", None)
        large = _count_and_oldest(conn, "baseline_large_files", None)
        loads = _count_and_oldest(conn, "scan_load_samples", "sampled_at")
        servers = _count_and_oldest(conn, "server_samples", "sampled_at")
        usage = _count_and_oldest(conn, "usage_events", "happened_at")
    first_run = runs[2]
    keep_generations = settings.detail_scan_keep_generations

    def child(key, counted, keep, keep_value=None, oldest=None):
        count, more, first = counted
        return Item(key=key, count=count, count_more=more, oldest=oldest or first,
                    keep=keep, keep_value=keep_value)

    item.children = [
        child("data.scan.results", results, KEEP_GENERATIONS, keep_generations, oldest=first_run),
        child("data.scan.checkpoints", checkpoints, KEEP_GENERATIONS, keep_generations),
        child("data.scan.large_files", large, KEEP_GENERATIONS, keep_generations),
        child("data.scan.runs", runs, KEEP_FOREVER),
        child("data.scan.load_samples", loads, KEEP_DAYS, settings.load_sample_retention_days),
        child("data.scan.server_samples", servers, KEEP_DAYS,
              settings.server_sample_retention_days),
        child("data.scan.usage", usage, KEEP_DAYS, usage_log.DEFAULT_RETENTION_DAYS),
    ]
    item.oldest = first_run
    return item


def _search_item(data_dir: Path) -> Item:
    from . import search_index

    path = search_index.db_path(data_dir)
    item = Item(key="data.item.search", size_bytes=_db_size(path), keep=KEEP_CURRENT)
    if search_index.SHELVED or not path.exists():
        return item   # 보류 중에는 열지 않는다 - 남은 파일의 크기만
    with search_index.session(data_dir) as conn:
        item.count, item.count_more, _ = _count_and_oldest(conn, "search_entries", None)
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


# 행은 여기까지만 센다. 그 너머는 "넘음" 이라고만 한다.
#
# 끝까지 세면 표 전체(또는 가장 작은 인덱스 전체)를 읽는다 - 스캔 DB 가 5GB 면 창을
# 한 번 여는 데 600MB 였다 (`tests/read_cost.py`). 이 창이 답하려는 것은 "무엇이
# 자리를 차지하나, 언제부터 쌓였나" 이고 그것은 파일 크기와 가장 오래된 시각이
# 말한다. 수십만 줄이 정확히 몇 줄인지는 그 값을 치를 만큼 쓸모 있지 않다.
# 근삿값(페이지 수로 어림 등)은 내지 않는다 - 지운 자리가 많으면 크게 빗나간다.
COUNT_CAP = 100_000


def _count_and_oldest(conn, table: str, stamp_column: Optional[str]):
    """`(행 수, 상한을 넘었나, 가장 이른 시각)`. 표가 없으면 `(None, False, None)`."""

    try:
        count = conn.execute(
            f"SELECT COUNT(*) FROM (SELECT 1 FROM {table} LIMIT ?)", (COUNT_CAP + 1,)
        ).fetchone()[0]
        oldest = None
        if stamp_column:
            oldest = conn.execute(f"SELECT MIN({stamp_column}) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError as exc:
        if sqlite_db.is_busy(exc):
            raise
        return None, False, None   # 그 표가 아직 없다 (예전 DB)
    if count > COUNT_CAP:
        return COUNT_CAP, True, oldest
    return count, False, oldest


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
