"""`smvwp_cli.py status` - 지금 이 도구가 제대로 돌고 있나를 한 화면에 (Qt 없음).

실기에서 "cron 이 도나", "홈이 결과 파일에서 읽히나", "업데이트 뒤 prepare 를 했나" 를
보려면 로그 몇 개와 DB 를 따로 뒤져야 했다. 여기서 한 번에 보이고, 손볼 것이 있으면
무엇을 하면 되는지(`hint`) 말한다. 아무것도 고치지 않는다 - 읽기만 한다.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import __version__, config as config_module, home_view, scan_lock, scan_store, store

OK, WARN, BAD, INFO = "정상", "주의", "문제", "-"

# 데이터 폴더 여유가 이보다 적으면 주의 (smvwp_cli 의 MIN_RECOMMENDED_FREE_BYTES 와 같다).
MIN_FREE_BYTES = 500 * 1024 * 1024
# 야간 스캔이 이보다 오래 안 돌았으면 주의 - 주말을 쉬는 설정도 있어 넉넉히.
SCAN_QUIET_HOURS = 72


@dataclass
class Item:
    key: str
    label: str
    level: str
    value: str
    hint: str = ""


def _utc(stamp) -> Optional[datetime]:
    try:
        value = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _ago(then: datetime, now: datetime) -> str:
    minutes = max(0, int((now - then).total_seconds() // 60))
    if minutes < 90:
        return f"{minutes}분 전"
    if minutes < 48 * 60:
        return f"{minutes // 60}시간 전"
    return f"{minutes // (24 * 60)}일 전"


def _size(path: Path) -> int:
    total = 0
    for suffix in ("", "-wal"):
        try:
            total += os.stat(str(path) + suffix).st_size
        except OSError:
            pass
    return total


def _mb(size: int) -> str:
    from .formatting import format_kb

    return format_kb(size // 1024)


def _collection(data_dir: Path, settings, now: datetime) -> Item:
    with store.session(data_dir) as conn:
        newest = _utc(store.newest_collected_at(conn))
    interval = max(60, int(settings.collector_interval_seconds))
    if newest is None:
        return Item("collect", "마지막 수집", BAD, "표본이 없습니다",
                    "smvwp_cli.py collect 를 한 번 돌리고 cron 을 확인하세요")
    late = (now - newest).total_seconds() > 2 * interval
    hint = (f"수집 간격({interval // 60}분)의 두 배를 넘었습니다 - cron 이 도는지 확인하세요 "
            "(crontab -l)") if late else ""
    return Item("collect", "마지막 수집", WARN if late else OK, _ago(newest, now), hint)


_STAGE_NAMES = {"df": "df", "save": "저장", "server": "서버 부하", "notify": "알림",
                "forecast": "예측", "reports": "보고서", "home": "홈"}


def _cycle_time(data_dir: Path, settings) -> Item:
    """마지막 수집 한 바퀴가 어디에 시간을 썼나 (`cycle.read_timing`)."""

    from . import cycle

    timing = cycle.read_timing(data_dir)
    if timing is None:
        return Item("cycle_time", "수집 걸린 시간", INFO, "기록 없음 (다음 수집 뒤에 생깁니다)")
    parts = sorted(timing["seconds"].items(), key=lambda pair: -pair[1])
    shown = " · ".join(f"{_STAGE_NAMES.get(name, name)} {value:.1f}"
                       for name, value in parts if value >= 0.05) or "모두 0.1초 미만"
    interval = max(60, int(settings.collector_interval_seconds))
    slow = timing["total"] > interval / 2
    return Item("cycle_time", "수집 걸린 시간", WARN if slow else OK,
                f"{timing['total']:.1f}초  ({shown})",
                f"수집 간격({interval // 60}분)의 절반을 넘습니다 - 가장 큰 단계부터 보세요"
                if slow else "")


_HOME_HINTS = {
    home_view.PROBLEM_MISSING: "다음 수집(smvwp_cli.py collect) 뒤에 생깁니다",
    home_view.PROBLEM_STALE: "수집이 멈췄습니다 - cron 을 확인하세요",
    home_view.PROBLEM_ACCOUNTS: "계정·설정이 바뀌었습니다 - 다음 수집 뒤 맞춰집니다",
    home_view.PROBLEM_FORMAT: "판이 바뀌었습니다 - 다음 수집 뒤 맞춰집니다",
}


def _home_file(data_dir: Path, config) -> Item:
    trouble = home_view.problem(data_dir, config)
    if trouble is not None:
        return Item("home", "홈 결과 파일", WARN, f"{trouble} - 창이 DB 에서 읽습니다",
                    _HOME_HINTS.get(trouble, "다음 수집 뒤 다시 써집니다"))
    try:
        others_can_read = os.name != "posix" or bool(
            os.stat(str(home_view.view_path(data_dir))).st_mode & 0o004)
    except OSError:
        others_can_read = True
    if not others_can_read:
        return Item("home", "홈 결과 파일", WARN, "다른 사용자가 읽을 수 없습니다",
                    "수집하는 계정의 umask 를 022 로 - 그동안 다른 사람의 창은 DB 에서 읽습니다")
    return Item("home", "홈 결과 파일", OK, "창이 이 파일을 읽습니다")


def _scan_file(data_dir: Path, config) -> Item:
    from . import scan_view

    trouble = scan_view.problem(data_dir, config)
    if trouble is not None:
        return Item("scan_view", "상세 스캔 결과 파일", WARN, f"{trouble} - 창이 스캔 DB 에서 읽습니다",
                    _HOME_HINTS.get(trouble, "다음 수집 뒤 다시 써집니다"))
    return Item("scan_view", "상세 스캔 결과 파일", OK, "창이 이 파일을 읽습니다")


def _night_scan(data_dir: Path, now: datetime) -> Item:
    if scan_lock.is_locked(data_dir):
        return Item("scan", "야간 스캔", INFO, "지금 돌고 있습니다")
    with scan_store.session(data_dir) as conn:
        run = scan_store.latest_run(conn)
    if run is None:
        return Item("scan", "야간 스캔", WARN, "아직 한 번도 돌지 않았습니다",
                    "cron 에 smvwp_cli.py scan 이 있는지 확인하세요")
    started = _utc(run["started_at"])
    quiet = started is None or (now - started).total_seconds() > SCAN_QUIET_HOURS * 3600
    failed = run["status"] == "error"
    value = f"{_ago(started, now) if started else '?'} 시작, {run['status']}"
    if failed:
        hint = "마지막 실행이 오류로 끝났습니다 - logs/ 의 스캔 기록을 보세요"
    elif quiet:
        hint = f"{SCAN_QUIET_HOURS}시간 넘게 돌지 않았습니다 - cron 을 확인하세요"
    else:
        hint = ""
    return Item("scan", "야간 스캔", WARN if hint else OK, value, hint)


def gather(data_dir: Path, config, now: Optional[datetime] = None) -> List[Item]:
    from . import nightly_scan

    now = now or datetime.now(timezone.utc)
    items = [Item("version", "판", INFO, __version__)]

    try:
        free = shutil.disk_usage(str(data_dir)).free
        low = free < MIN_FREE_BYTES
        items.append(Item("data_dir", "데이터 폴더", WARN if low else OK,
                          f"{data_dir}  (여유 {_mb(free)})",
                          "데이터 폴더 여유가 적습니다" if low else ""))
    except OSError as exc:
        items.append(Item("data_dir", "데이터 폴더", BAD, f"{data_dir}  ({exc})"))

    enabled = config_module.enabled_accounts(config)
    items.append(Item("accounts", "계정", OK if enabled else WARN,
                      f"{len(enabled)}개 켜짐 / 전체 {len(config.accounts)}개",
                      "" if enabled else "GUI 의 '계정 관리' 에서 계정을 더하세요"))
    items.append(_collection(data_dir, config.settings, now))
    items.append(_cycle_time(data_dir, config.settings))
    items.append(_home_file(data_dir, config))
    items.append(_scan_file(data_dir, config))
    items.append(_night_scan(data_dir, now))

    outdated = nightly_scan.outdated_summaries(data_dir, config)
    items.append(Item("summaries", "스캔 탭 요약", WARN if outdated else OK,
                      f"{outdated}개 계정이 옛 요약" if outdated else "맞음",
                      "cron 을 내리고 창을 닫은 채 smvwp_cli.py prepare 를 한 번 돌리세요"
                      if outdated else ""))

    sizes = [("표본", store.db_path(data_dir)), ("스캔", scan_store.db_path(data_dir))]
    items.append(Item("db", "DB 크기", INFO,
                      ", ".join(f"{name} {_mb(_size(path))}" for name, path in sizes)))
    return items


def render(items: List[Item]) -> str:
    """터미널용 - 한글은 고정폭에서 두 칸이라 `reports.pad` 로 맞춘다."""

    from .reports import display_width, pad

    width = max(display_width(item.label) for item in items)
    blank = " " * (8 + width + 2)
    lines = []
    for item in items:
        mark = f"[{item.level}]" if item.level != INFO else ""
        lines.append(f"{pad(mark, 7)} {pad(item.label, width)}  {item.value}")
        if item.hint:
            lines.append(f"{blank}-> {item.hint}")
    return "\n".join(lines)


def worst(items: List[Item]) -> str:
    levels = {item.level for item in items}
    return BAD if BAD in levels else WARN if WARN in levels else OK
