"""`smvwp_cli.py diagnose` - 실기 확인 항목을 한 번에 읽어 보고서 파일 하나로 (Qt 화면 없음).

## 왜

실기(폐쇄망 RHEL)에서 확인할 것이 여러 군데에 흩어져 있었다 - `status`, `load`,
로그 몇 개, `crontab -l`, `df`, 파이썬 모듈. 하나씩 부탁하고 하나씩 옮겨 받으면
몇 번을 오가야 한다. 이 명령 한 번이면 개발 쪽이 다음 결정을 내리는 데 필요한
숫자가 파일 하나에 모이고, 그 파일을 그대로 붙여 주면 된다.

## 지키는 것

- **읽기만 한다.** 데이터를 지우거나 바꾸지 않는다 (보고서는 지금 폴더나 집 폴더에).
  이 모듈이 직접 여는 DB 는 읽기 전용(`mode=ro`)이고, 없는 DB 는 만들지 않는다.
  `status` 와 같은 읽기(상태·스캔 순서·데이터 폴더)는 평소 길로 열므로, 업데이트 직후
  처음이면 DB 가 새 판의 인덱스를 거기서 만든다 - `status` 를 돌린 것과 같다.
- 항목 하나가 실패해도(NFS 가 답하지 않아도) 나머지는 낸다 - 실패한 자리에 까닭을 적는다.
  바깥 명령에는 모두 제한 시간이 있다.
- 계정 **경로**·파일 이름·다른 사람의 프로세스 명령줄은 넣지 않는다. 계정 이름과
  호스트 이름은 들어간다 (밤 순서와 수집 장비를 보려면 필요하다).
- 돌리는 동안 절마다 터미널에 한 줄씩 (몇 번째·무엇·걸린 시간), 오래 걸리는 절은
  10초마다 "아직 읽는 중" 을 낸다 - 멈춘 것인지 느린 것인지, 어디서 느린지 보이게.
  보고서 요약에도 총 시간과 가장 오래 걸린 절이 들어간다.
- 맨 위 '요약' 은 짧은 줄 몇 개다. 폐쇄망이라 복사가 안 되면 그것만 옮겨 적어도
  대부분의 결정을 내릴 수 있게 한다.
"""

from __future__ import annotations

import glob
import importlib.util
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import __version__

# 로그에서 볼 꼬리 (파일마다). cron 로그는 덧붙이기만 해서 클 수 있다.
LOG_TAIL_BYTES = 2 * 1024 * 1024
# 바깥 명령의 제한 시간 - NFS 가 답하지 않으면 df 가 멈춘다.
COMMAND_TIMEOUT = 15
# 보고서에 싣는 최근 야간 실행 수와 밤 순서 줄 수.
RUNS_SHOWN = 7
QUEUE_SHOWN = 15

HOW_TO_RETURN = """\
돌려주는 법
  1. 이 파일을 통째로 복사해 Claude 대화창에 붙여 넣어 주세요.
     너무 길어 안 들어가면 '요약' 절과 '!' 로 시작하는 줄만.
  2. 복사가 안 되면(폐쇄망) '요약' 절만 사진으로 찍거나 옮겨 적어 주세요.
  3. 붙이기 전에 한 번 훑어 주세요. 계정 이름·호스트 이름은 들어 있고,
     계정 경로·파일 이름·남의 프로세스 명령줄은 넣지 않았습니다.
  이 명령은 읽기만 했습니다 - 데이터를 지우거나 바꾸지 않았습니다."""

_REASONS = {"resume": "하다 만", "never": "한 번도 못 끝냄", "overdue": "오래 못 봄",
            "changed": "많이 변함", "age": "차례"}
_KEEP = {"days": "{}일", "generations": "세대 {}개", "forever": "지우지 않음",
         "capped": "최대 {}", "current": "지금 것만", "per_item": "항목마다"}
_STATES = {"done": "끝", "doing": "하는 중", "waiting": "대기", "unseen": "못 봄"}


TICK_SECONDS = 10.0


class Report:
    """절(section)마다 줄을 모은다. 요약 줄은 따로 모아 맨 위에 둔다."""

    def __init__(self, progress: Optional[Callable[[str], None]] = None, total: int = 0,
                 tick_seconds: float = TICK_SECONDS) -> None:
        self.summary: List[str] = []
        self.sections: List[Tuple[str, List[str]]] = []
        self.spent: List[Tuple[str, float]] = []
        self.progress = progress
        self.total = total
        self.tick_seconds = tick_seconds
        self.started = time.monotonic()

    def _say(self, line: str) -> None:
        if self.progress is not None:
            try:
                self.progress(line)
            except Exception:   # 터미널이 닫혀도 보고서는 끝까지 만든다
                pass

    def section(self, title: str, fill: Callable[[List[str]], None]) -> None:
        lines: List[str] = []
        number = len(self.sections) + 1
        tag = f"[{number:>2}/{self.total}]" if self.total else f"[{number:>2}]"
        self._say(f"{tag} {title} ...")
        started = time.monotonic()
        done = threading.Event()

        def tick() -> None:   # 오래 걸리는 절: 멈춘 것이 아님을 보인다
            while not done.wait(self.tick_seconds):
                self._say(f"      ... 아직 읽는 중 ({time.monotonic() - started:.0f}초)")

        ticker = threading.Thread(target=tick, daemon=True)
        if self.progress is not None:
            ticker.start()
        try:
            fill(lines)
        except Exception as exc:   # 한 절이 실패해도 나머지는 낸다
            lines.append(f"! 읽지 못함: {type(exc).__name__}: {exc}")
        finally:
            done.set()
        spent = time.monotonic() - started
        if spent >= 1.0:
            lines.append(f"(이 절을 읽는 데 {spent:.1f}초)")
        self.sections.append((title, lines))
        self.spent.append((title, spent))
        failed = any(line.startswith("! 읽지 못함") for line in lines)
        self._say(f"{tag} {title} - {spent:.1f}초{' (실패)' if failed else ''}"
                  f" (지금까지 {time.monotonic() - self.started:.0f}초)")

    def timing_note(self) -> None:
        """요약에 총 시간과 오래 걸린 절 - 점검 자체가 느리면 어디인지 보이게."""

        total = time.monotonic() - self.started
        slow = sorted(self.spent, key=lambda item: item[1], reverse=True)[:3]
        slow = [f"{title.split(' (')[0]} {spent:.0f}초" for title, spent in slow if spent >= 1.0]
        self.note(f"점검 걸린 시간: {total:.0f}초" + (f" (오래: {', '.join(slow)})" if slow else ""))

    def note(self, line: str) -> None:
        self.summary.append(line)

    def render(self, header: str) -> str:
        out = [header, "", HOW_TO_RETURN, "", "== 요약 =="]
        out += [f"  {line}" for line in self.summary] or ["  (없음)"]
        for index, (title, lines) in enumerate(self.sections, start=1):
            out += ["", f"== {index}. {title} =="]
            out += [f"  {line}" for line in lines] or ["  (없음)"]
        out += ["", "-- 끝 --", ""]
        return "\n".join(out)


# -- 도구 --------------------------------------------------------------------

def _utc(stamp) -> Optional[datetime]:
    try:
        value = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _ago(stamp, now: datetime) -> str:
    then = _utc(stamp)
    if then is None:
        return "?"
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 0:
        return "앞날(시계?)"
    if minutes < 90:
        return f"{minutes}분 전"
    if minutes < 48 * 60:
        return f"{minutes // 60}시간 전"
    return f"{minutes // (24 * 60)}일 전"


def _local(stamp) -> str:
    then = _utc(stamp)
    return then.astimezone().strftime("%m-%d %H:%M") if then else "?"


def _mb(size: Optional[int]) -> str:
    if size is None:
        return "?"
    return f"{size / 1024 / 1024:.1f}MB"


def _run(argv: List[str]) -> Tuple[int, str]:
    """`(종료 코드, 출력)` - 실패해도 예외를 내지 않는다."""

    try:
        done = subprocess.run(argv, capture_output=True, timeout=COMMAND_TIMEOUT, check=False)
    except FileNotFoundError:
        return 127, "명령 없음"
    except subprocess.TimeoutExpired:
        return 124, f"{COMMAND_TIMEOUT}초 안에 끝나지 않음"
    text = (done.stdout or b"") + (done.stderr or b"")
    return done.returncode, text.decode("utf-8", errors="replace").strip()


@contextmanager
def read_only(path: Path):
    """DB 를 읽기 전용으로 열고 닫는다. 없으면 FileNotFoundError - 새로 만들지 않는다."""

    if not path.exists():
        raise FileNotFoundError(f"{path.name} 없음")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _columns(conn, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _tail_lines(path: Path) -> List[str]:
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - LOG_TAIL_BYTES))
            raw = stream.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if size > LOG_TAIL_BYTES else lines


def _mount_of(path: Path) -> Optional[Tuple[str, str, str]]:
    """`(장치, 종류, 옵션)` - /proc/mounts 에서 가장 긴 접두어. 리눅스가 아니면 None."""

    try:
        mounts = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    target = os.path.realpath(str(path))
    best = None
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        point = parts[1].replace("\\040", " ")
        if target == point or target.startswith(point.rstrip("/") + "/"):
            if best is None or len(point) > len(best[0]):
                best = (point, parts[0], parts[2], parts[3])
    return best[1:] if best else None


# -- 절마다 -------------------------------------------------------------------

def _machine(report: Report, data_dir: Path, lines: List[str]) -> None:
    host = socket.gethostname()
    release = ""
    for name in ("/etc/redhat-release", "/etc/os-release"):
        try:
            release = Path(name).read_text(encoding="utf-8").splitlines()[0]
            break
        except (OSError, IndexError):
            continue
    libc = " ".join(platform.libc_ver()) or "?"
    lines.append(f"호스트 {host}  /  {release or platform.platform()}  /  glibc {libc}")
    lines.append(f"python {platform.python_version()}  ({sys.executable})")
    lines.append(f"판 {__version__}  /  CPU {os.cpu_count()}개")
    mount = _mount_of(data_dir)
    if mount:
        device, kind, options = mount
        version = next((part for part in options.split(",") if part.startswith("vers=")), "")
        lines.append(f"데이터 폴더의 파일시스템: {kind} {version}".rstrip())
    old = os.umask(0o022)
    os.umask(old)
    lines.append(f"이 셸의 umask {old:03o}")
    report.note(f"판 {__version__} | 호스트 {host.split('.')[0]} | python "
                f"{platform.python_version()} | 데이터 {mount[1] if mount else '?'}")


def _status(report: Report, data_dir: Path, config, lines: List[str]) -> None:
    from . import scan_store, status_check, store

    missing = [path.name for path in (store.db_path(data_dir), scan_store.db_path(data_dir))
               if not path.exists()]
    if missing:
        lines.append(f"! DB 가 없어 건너뜀: {', '.join(missing)}")
        report.note("상태: DB 없음 - 수집이 한 번도 안 돈 데이터 폴더")
        return
    items = status_check.gather(data_dir, config)
    lines.extend(status_check.render(items).splitlines())
    worst = status_check.worst(items)
    flagged = [item.label for item in items if item.level in (status_check.WARN, status_check.BAD)]
    report.note(f"상태 {worst}" + (f" ({', '.join(flagged)})" if flagged else ""))
    for item in items:
        if item.key == "summaries" and item.level != status_check.OK:
            report.note("! prepare 필요: " + item.value)


def _cycle(report: Report, data_dir: Path, config, lines: List[str]) -> None:
    from . import cycle

    timing = cycle.read_timing(data_dir)
    if timing is None:
        lines.append("기록 없음 (view/cycle.json) - 이 판으로 수집이 아직 한 번도 안 돌았다")
        report.note("수집 시간: 기록 없음")
        return
    interval = max(60, int(config.settings.collector_interval_seconds))
    lines.append(f"마지막 한 바퀴: {_local(timing['finished_at'])} 끝, 장비 {timing['host']}, "
                 f"합계 {timing['total']:.1f}초 (간격 {interval // 60}분)")
    parts = sorted(timing["seconds"].items(), key=lambda pair: -pair[1])
    for name, seconds in parts:
        lines.append(f"  {name:<9} {seconds:7.2f}초")
    shown = " ".join(f"{name} {seconds:.1f}" for name, seconds in parts if seconds >= 0.05)
    report.note(f"수집 {timing['total']:.1f}초 / 간격 {interval // 60}분 ({shown or '모두 0.1 미만'})")
    outcome = timing.get("server_sample") or "기록 없음 (이 판 이전 수집)"
    mark = "" if outcome.startswith("저장 (") else "! "
    lines.append(f"{mark}이 바퀴의 서버 부하 표본: {outcome}")
    report.note(f"{mark}마지막 수집의 서버 부하 표본: {outcome[:120]}")


def _df(report: Report, config, lines: List[str]) -> None:
    from . import collector
    from .config import enabled_accounts

    code, text = _run(["df", "--version"])
    lines.append(f"df: {text.splitlines()[0] if text else '?'}")
    accounts = enabled_accounts(config)
    if not accounts:
        lines.append("켜진 계정이 없어 건너뜀")
        return
    account = accounts[0]
    started = time.monotonic()
    code, text = _run(["df", "-k", f"--output={collector.DF_OUTPUT_COLUMNS}", "--", account.path])
    one = time.monotonic() - started
    parsed = collector._parse_combined(text) if code == 0 else None
    if parsed is not None:
        lines.append(f"한 번 묻기(--output): 됨  ({one:.2f}초, 계정 '{account.name}')")
        bytes_part, inode_part = parsed
        lines.append(f"  용량 {bytes_part.pct:.0f}%  inode "
                     f"{'모름(-)' if inode_part.pct is None else f'{inode_part.pct:.0f}%'}")
        report.note("df 한 번 묻기: 됨")
    else:
        reason = text.splitlines()[-1] if text else f"exit={code}"
        lines.append(f"! 한 번 묻기(--output): 안 됨 - {reason[:200]}")
        lines.append("  (그래도 수집은 예전 두 번 묻기로 돈다)")
        report.note("! df 한 번 묻기: 안 됨")
    started = time.monotonic()
    code_k, _ = _run(["df", "-Pk", "--", account.path])
    code_i, _ = _run(["df", "-Pi", "--", account.path])
    lines.append(f"예전 두 번 묻기: {time.monotonic() - started:.2f}초 (exit {code_k}, {code_i})")


def _home(report: Report, data_dir: Path, config, lines: List[str]) -> None:
    from . import home_view

    path = home_view.view_path(data_dir)
    try:
        info = path.stat()
    except FileNotFoundError:
        lines.append("! view/home.json 없음 - 창은 DB 에서 읽는다")
        report.note("! 홈 결과 파일 없음")
        return
    readable = bool(info.st_mode & 0o004) if os.name == "posix" else True
    problem = home_view.problem(data_dir, config)
    lines.append(f"view/home.json {_mb(info.st_size)}, 고친 때 "
                 f"{_ago(datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(), datetime.now(timezone.utc))}, "
                 f"권한 {oct(info.st_mode & 0o777)}, 남이 읽기 {'됨' if readable else '안 됨'}")
    lines.append(f"창이 쓸 수 있나: {'예' if problem is None else '아니오 - ' + problem}")
    from_file = from_db = 0
    slow = []
    for log in sorted((data_dir / "logs").glob("gui-*.log*")):
        for line in _tail_lines(log):
            if "홈을 결과 파일에서 읽었습니다" in line:
                from_file += 1
            elif "홈 데이터를 읽는 데" in line:
                from_db += 1
                slow.append(line)
    lines.append(f"창 기록(gui-*.log): 결과 파일에서 {from_file}번, 5초 넘게 DB 에서 {from_db}번")
    for line in slow[-3:]:
        lines.append(f"  {line[:240]}")
    report.note(f"홈 파일 {'정상' if problem is None else problem}, 남이 읽기 "
                f"{'됨' if readable else '안 됨'}, 창이 파일에서 {from_file}번 / 느린 DB {from_db}번")

    from . import scan_view

    scan_path = scan_view.view_path(data_dir)
    scan_problem = scan_view.problem(data_dir, config)
    try:
        scan_info = scan_path.stat()
        lines.append(f"view/scan.json {_mb(scan_info.st_size)}, 고친 때 "
                     f"{_ago(datetime.fromtimestamp(scan_info.st_mtime, timezone.utc).isoformat(), datetime.now(timezone.utc))}"
                     f", 창이 쓸 수 있나: {'예' if scan_problem is None else '아니오 - ' + scan_problem}")
    except FileNotFoundError:
        lines.append("! view/scan.json 없음 - 창은 상세 스캔 상태를 스캔 DB 에서 읽는다")
    report.note(f"{'' if scan_problem is None else '! '}상세 스캔 파일 "
                f"{'정상' if scan_problem is None else scan_problem}")


def _night(report: Report, data_dir: Path, config, lines: List[str]) -> None:
    from . import scan_store

    with read_only(scan_store.db_path(data_dir)) as conn:
        columns = _columns(conn, "scan_runs")
        total, first = conn.execute("SELECT COUNT(*), MIN(started_at) FROM scan_runs").fetchone()
        lines.append(f"실행 기록 {total}줄, 처음 {_local(first) if first else '-'}")
        wanted = [name for name in ("cpu_system_percent_avg", "cpu_system_percent_peak",
                                    "load_avg_1m", "memory_peak_percent", "rss_peak_kb",
                                    "parallel_accounts") if name in columns]
        rows = conn.execute(
            "SELECT run_id, triggered_by, started_at, ended_at, status"
            + "".join(f", {name}" for name in wanted)
            + " FROM scan_runs ORDER BY started_at DESC LIMIT ?", (RUNS_SHOWN,)).fetchall()
        lines.append("최근 실행 (시작 / 걸린 시간 / 결과 / 누가 / 잰 디렉터리 / 시간당 / CPU평균·최고 / load / 메모리최고 / RSS최고)")
        for row in rows:
            start, end = _utc(row["started_at"]), _utc(row["ended_at"])
            hours = (end - start).total_seconds() / 3600 if start and end else None
            dirs = scan_store.checkpoints_done_between(
                conn, row["started_at"], row["ended_at"] or datetime.now(timezone.utc).isoformat())
            rate = f"{dirs / hours:.0f}" if hours else "-"

            def value(name, fmt):
                return fmt.format(row[name]) if name in wanted and row[name] is not None else "-"

            lines.append(
                f"  {_local(row['started_at'])} / {f'{hours:.1f}h' if hours is not None else '도는 중'}"
                f" / {row['status']} / {row['triggered_by']} / {dirs} / {rate}"
                f" / {value('cpu_system_percent_avg', '{:.0f}')}·{value('cpu_system_percent_peak', '{:.0f}')}%"
                f" / {value('load_avg_1m', '{:.1f}')} / {value('memory_peak_percent', '{:.0f}')}%"
                f" / {value('rss_peak_kb', '{:.0f}')}KB")
        if rows:
            last = rows[0]
            report.note(f"야간 마지막 {_local(last['started_at'])} {last['status']}, 기록 {total}줄")
    _night_order(report, data_dir, config, lines)


def _night_order(report: Report, data_dir: Path, config, lines: List[str]) -> None:
    from . import nightly_scan, scan_overview, store

    snapshot = nightly_scan.get_status_snapshot(data_dir, config)
    with read_only(store.db_path(data_dir)) as conn:
        samples = store.latest_samples(conn, [account.account_id for account in config.accounts])
    overview = scan_overview.build(snapshot, config, samples)
    lines.append(f"이번 바퀴: 끝 {overview.count(scan_overview.DONE)} · 하는 중 "
                 f"{overview.count(scan_overview.DOING)} · 대기 {overview.count(scan_overview.WAITING)}"
                 f" · 못 봄 {overview.count(scan_overview.UNSEEN)}  (디렉터리 {overview.percent}%)")
    queue = overview.queue
    lines.append(f"다음에 도는 순서 (앞 {min(QUEUE_SHOWN, len(queue))}개 / {len(queue)}개)")
    for item in queue[:QUEUE_SHOWN]:
        lines.append(f"  {item.order:>3}. {item.name}  {_STATES.get(item.state, item.state)}"
                     f"  까닭={_REASONS.get(item.reason, item.reason)}"
                     f"  디렉터리 {item.done_dirs}/{item.total_dirs}{'(어림)' if item.estimated_total else ''}")
    report.note(f"스캔 바퀴: 끝 {overview.count(scan_overview.DONE)} / 남음 {len(overview.remaining)}"
                f" / 못 봄 {overview.count(scan_overview.UNSEEN)}")


def _search_shelved() -> bool:
    from . import search_index

    return search_index.SHELVED   # 보류 중에는 이 절도 없다 - 그 DB 를 열지 않는다


def _search(report: Report, data_dir: Path, config, lines: List[str]) -> None:
    """검색 색인 - 켠 계정은 밤마다 크기 순회와 따로 한 번 더 훑는다. 그 몫이 얼마인가."""

    from . import search_index

    on = [account for account in config.accounts if account.search_indexing]
    path = search_index.db_path(data_dir)
    size = sum(Path(str(path) + suffix).stat().st_size
               for suffix in ("", "-wal", "-journal") if Path(str(path) + suffix).exists())
    lines.append(f"색인을 켠 계정: {len(on)}개 / {len(config.accounts)}개 · search_index.db {_mb(size)}")
    if path.exists() and on:
        with read_only(path) as conn:
            state = {row[0]: (row[1], row[2]) for row in conn.execute(
                "SELECT account_id, entry_count, last_full_index_at FROM search_state")}
        for account in on:
            count, at = state.get(account.account_id, (None, None))
            lines.append(f"  {account.name}: "
                         + (f"{count:,}개, 마지막 완주 {_local(at)}" if count is not None
                            else "한 번도 완주 못 함"))
    spent = []
    for log in sorted((data_dir / "logs").glob("*.log*")):
        spent += [line for line in _tail_lines(log) if "검색 색인 " in line and "분" in line]
    minutes = []
    for line in spent:
        try:
            minutes.append(float(line.rsplit(",", 1)[1].strip().rstrip("분")))
        except (IndexError, ValueError):
            pass
    lines.append(f"로그의 '검색 색인' 줄: {len(spent)}개, 합계 {sum(minutes):.0f}분")
    for line in spent[-8:]:
        lines.append(f"  {line[:200]}")
    report.note(f"검색 색인: 켠 계정 {len(on)}개 · DB {_mb(size)}"
                + (f" · 로그상 {sum(minutes):.0f}분 ({len(minutes)}번)" if minutes else ""))


def _server_load(report: Report, data_dir: Path, lines: List[str]) -> None:
    from . import scan_store

    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    with read_only(scan_store.db_path(data_dir)) as conn:
        rows = conn.execute(
            "SELECT source, COUNT(*), MIN(sampled_at), MAX(sampled_at) FROM server_samples "
            "WHERE sampled_at >= ? GROUP BY source", (since,)).fetchall()
        newest = conn.execute("SELECT MAX(sampled_at) FROM server_samples").fetchone()[0]
    counts = {row[0]: row[1] for row in rows}
    for row in rows:
        lines.append(f"최근 7일 {row[0]}: {row[1]}개 ({_local(row[2])} ~ {_local(row[3])})")
    if not rows:
        lines.append("! 최근 7일 표본 없음")
    lines.append(f"가장 최근 표본: {_ago(newest, datetime.now(timezone.utc)) if newest else '한 번도 없음'}")
    mentions = []
    for log in sorted((data_dir / "logs").glob("*.log*")):
        mentions += [line for line in _tail_lines(log) if "서버 부하 표본" in line]
    lines.append(f"로그의 '서버 부하 표본' 줄: {len(mentions)}개")
    for line in mentions[-5:]:
        lines.append(f"  {line[:240]}")
    expected = 7 * 96
    report.note(f"부하 표본 7일: 수집기 {counts.get('collector', 0)}개 (기대 약 {expected})"
                f" · 스캔 {counts.get('scan', 0)}개 · 마지막 "
                f"{_ago(newest, datetime.now(timezone.utc)) if newest else '없음'}")


USAGE_DAYS = 30


def _usage(report: Report, data_dir: Path, lines: List[str]) -> None:
    """무엇이 실제로 쓰이나 - 다음에 무엇을 앞으로 빼고 무엇을 접을지의 근거.

    보기 전용 창(데이터 폴더에 쓸 수 없는 사람)은 기록을 남기지 못한다 - 그 사람들은
    여기 없다."""

    from . import scan_store, usage_log

    since = (datetime.now(timezone.utc) - timedelta(days=USAGE_DAYS)).isoformat()
    with read_only(scan_store.db_path(data_dir)) as conn:
        people = conn.execute(
            "SELECT user_name, COUNT(*) AS events, COUNT(DISTINCT substr(happened_at, 1, 10)) AS days "
            "FROM usage_events WHERE happened_at >= ? GROUP BY user_name ORDER BY events DESC",
            (since,)).fetchall()
        actions = conn.execute(
            "SELECT action, COUNT(*) AS events, COUNT(DISTINCT user_name) AS users, "
            "MAX(happened_at) AS last FROM usage_events WHERE happened_at >= ? "
            "GROUP BY action ORDER BY events DESC", (since,)).fetchall()
    if not people:
        lines.append(f"! 최근 {USAGE_DAYS}일 사용 기록 없음")
        report.note(f"사용 {USAGE_DAYS}일: 기록 없음")
        return
    lines.append(f"최근 {USAGE_DAYS}일 - 쓴 사람 {len(people)}명")
    for row in people:
        lines.append(f"  {str(row['user_name'] or '-'):<14} {row['events']:>6,}번  {row['days']:>3}일")
    lines.append("무엇을 (횟수 · 사람 · 마지막):")
    used = set()
    for row in actions:
        used.add(row["action"])
        label = usage_log.LABELS.get(row["action"], row["action"])
        lines.append(f"  {label:<16} {row['events']:>6,}번  {row['users']:>3}명  {_ago(row['last'], datetime.now(timezone.utc))}")
    unused = [label for action, label in usage_log.LABELS.items() if action not in used]
    if unused:
        lines.append("안 쓰임: " + ", ".join(unused))
    top = " · ".join(f"{usage_log.LABELS.get(row['action'], row['action'])} {row['events']}"
                     for row in actions[:4])
    report.note(f"사용 {USAGE_DAYS}일: {len(people)}명 · 많이: {top}"
                + (f" · 안 쓰임 {len(unused)}개" if unused else ""))


def _inventory(report: Report, data_dir: Path, config, lines: List[str]) -> None:
    from . import data_inventory

    inventory = data_inventory.build(data_dir, config)

    def describe(item, indent=""):
        count = "" if item.count is None else f"{item.count}{'+' if item.count_more else ''} {item.count_unit}"
        size = "" if item.size_bytes is None else _mb(item.size_bytes)
        since = f"{_local(item.oldest)}부터" if item.oldest else ""
        keep = "보관 " + _KEEP.get(item.keep, "{}").format(
            _mb(item.keep_value) if item.keep == data_inventory.KEEP_CAPPED else item.keep_value)
        mark = "! 읽지 못함" if item.failed else ""
        lines.append(f"{indent}{item.key.split('.')[-1]:<16} {size:>9} {count:>18} {since:>14}  {keep} {mark}".rstrip())
        for child in item.children:
            describe(child, indent + "  ")

    for item in inventory.items:
        describe(item)
    lines.append(f"합계 {_mb(inventory.total_bytes)}, 가장 오래된 것 {_local(inventory.since) if inventory.since else '-'}")
    by_key = {item.key: item for item in inventory.items}
    audit = by_key.get("data.item.audit")
    runs = next((child for item in inventory.items for child in item.children
                 if child.key == "data.scan.runs"), None)
    report.note(f"데이터 폴더 {_mb(inventory.total_bytes)} | 알림 감사 "
                f"{audit.count if audit else '?'}개 {_mb(audit.size_bytes) if audit else ''}"
                f" | 스캔 실행 기록 {runs.count if runs else '?'}줄")


def _qt(report: Report, lines: List[str]) -> None:
    pyqt5 = pyside6 = "없음"
    try:
        from PyQt5 import QtCore  # type: ignore

        pyqt5 = f"{QtCore.PYQT_VERSION_STR} (Qt {QtCore.QT_VERSION_STR})"
    except Exception as exc:
        pyqt5 = f"없음 ({type(exc).__name__})"
    if importlib.util.find_spec("PySide6") is not None:
        code, text = _run([sys.executable, "-c",
                           "import PySide6, PySide6.QtCore as c; print(PySide6.__version__, c.qVersion())"])
        pyside6 = text.splitlines()[-1] if text else f"exit={code}"
    cursor = sorted(set(glob.glob("/usr/lib64/libxcb-cursor.so*") + glob.glob("/usr/lib/libxcb-cursor.so*")
                        + glob.glob("/usr/lib/x86_64-linux-gnu/libxcb-cursor.so*")))
    lines.append(f"PyQt5: {pyqt5}")
    lines.append(f"PySide6: {pyside6}")
    lines.append(f"libxcb-cursor: {'있음 (' + ', '.join(Path(p).name for p in cursor) + ')' if cursor else '없음'}")
    lines.append(f"DISPLAY={os.environ.get('DISPLAY', '')!r}  QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM', '')!r}")
    report.note(f"Qt: PyQt5 {pyqt5.split(' ')[0]} | PySide6 {pyside6.split(' ')[0]} | xcb-cursor "
                f"{'있음' if cursor else '없음'}")


def _cron(report: Report, lines: List[str]) -> None:
    from . import cron_status

    status = cron_status.read_status()
    if not status.available:
        lines.append(f"crontab 을 읽지 못함: {status.error}")
        report.note("cron: 모름")
        return
    lines.append(f"수집(15분) 항목: {'있음' if status.collector else '없음'}")
    lines.append(f"야간 스캔 항목: {'있음' if status.nightly else '없음'}")
    report.note(f"cron(이 사용자): 수집 {'있음' if status.collector else '없음'}"
                f" · 야간 {'있음' if status.nightly else '없음'}")


def _logs(report: Report, data_dir: Path, lines: List[str]) -> None:
    folder = data_dir / "logs"
    files = sorted(folder.glob("*.log*")) if folder.exists() else []
    if not files:
        lines.append("logs/ 에 기록 없음")
        return
    errors = []
    for log in files:
        try:
            info = log.stat()
        except OSError:
            continue
        lines.append(f"{log.name:<40} {_mb(info.st_size):>8}  고친 때 "
                     f"{_ago(datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(), datetime.now(timezone.utc))}")
        errors += [(log.name, line) for line in _tail_lines(log)
                   if " ERROR " in line or " CRITICAL " in line]
    lines.append(f"ERROR 줄 (꼬리 {LOG_TAIL_BYTES // 1024 // 1024}MB 안): {len(errors)}개, 마지막 8개:")
    for name, line in errors[-8:]:
        lines.append(f"  [{name}] {line[:240]}")
    report.note(f"로그 ERROR {len(errors)}개")
    _gui_speed(report, files, lines)


def _gui_speed(report: Report, files, lines: List[str]) -> None:
    """창이 뜨기까지 걸린 시간과 화면이 멈춘 자리 (`gui.stall_watch` 가 남긴 줄)."""

    from .gui.stall_watch import STALL_PREFIX, STARTUP_PREFIX

    startup, stalls = [], []
    for log in files:
        if not log.name.startswith("gui"):
            continue
        for line in _tail_lines(log):
            if STARTUP_PREFIX in line:
                startup.append(line.split(STARTUP_PREFIX, 1)[1].strip())
            elif STALL_PREFIX in line:
                text = line.split(STALL_PREFIX, 1)[1].strip()
                try:
                    seconds = float(text.split("초", 1)[0])
                except ValueError:
                    continue
                stalls.append((seconds, text.split(" - ", 1)[-1]))
    lines.append("")
    lines.append(f"창 시작 (최근 6줄): {len(startup)}줄")
    for line in startup[-6:]:
        lines.append(f"  {line[:200]}")
    lines.append(f"화면 멈춤 (1초 넘게): {len(stalls)}번")
    places: dict = {}
    for seconds, at in stalls:
        total, count, longest = places.get(at, (0.0, 0, 0.0))
        places[at] = (total + seconds, count + 1, max(longest, seconds))
    for at, (total, count, longest) in sorted(places.items(), key=lambda kv: -kv[1][0])[:8]:
        lines.append(f"  {count}번 합 {total:.0f}초 (가장 긴 것 {longest:.1f}초) - {at[:200]}")
    if startup:
        report.note("창 시작: " + " / ".join(startup[-3:]))
    if stalls:
        worst = max(places.items(), key=lambda kv: kv[1][0])
        report.note(f"! 화면 멈춤 {len(stalls)}번, 가장 긴 것 {max(s for s, _ in stalls):.1f}초"
                    f" - 가장 많이 멈춘 자리: {worst[0][:160]}")


# -- 묶기 ---------------------------------------------------------------------

def build(data_dir: Path, config, progress: Optional[Callable[[str], None]] = None,
          tick_seconds: float = TICK_SECONDS) -> str:
    """보고서 글 전체. 읽기만 한다. `progress` 가 있으면 절마다 진행 한 줄을 넘긴다."""

    data_dir = Path(data_dir)
    steps = [
        ("장비·파이썬", lambda lines: _machine(report, data_dir, lines)),
        ("상태 (smvwp_cli.py status 와 같음)",
         lambda lines: _status(report, data_dir, config, lines)),
        ("수집 한 바퀴가 쓴 시간", lambda lines: _cycle(report, data_dir, config, lines)),
        ("df 한 번 묻기 (--output)", lambda lines: _df(report, config, lines)),
        ("홈 결과 파일", lambda lines: _home(report, data_dir, config, lines)),
        ("야간 스캔 - 최근 실행과 다음 순서",
         lambda lines: _night(report, data_dir, config, lines)),
    ] + ([] if _search_shelved() else [
        ("검색 색인 (밤에 따로 한 번 더 훑는 몫)",
         lambda lines: _search(report, data_dir, config, lines)),
    ]) + [
        ("서버 부하 표본", lambda lines: _server_load(report, data_dir, lines)),
        (f"사용 기록 (최근 {USAGE_DAYS}일, 무엇이 실제로 쓰이나)",
         lambda lines: _usage(report, data_dir, lines)),
        ("데이터 폴더에 쌓인 것 (보관 기간 정하기용)",
         lambda lines: _inventory(report, data_dir, config, lines)),
        ("화면 라이브러리 (PyQt5 / PySide6)", lambda lines: _qt(report, lines)),
        ("cron", lambda lines: _cron(report, lines)),
        ("로그", lambda lines: _logs(report, data_dir, lines)),
    ]
    report = Report(progress, total=len(steps), tick_seconds=tick_seconds)
    for title, fill in steps:
        report.section(title, fill)
    report.timing_note()
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return report.render(f"##### Storage Manager VWP 실기 점검 ({stamp}) #####")


def default_report_path(folder: Optional[Path] = None) -> Path:
    host = socket.gethostname().split(".")[0] or "host"
    name = f"smvwp_check_{host}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    return Path(folder or Path.cwd()) / name


def write(text: str, path: Path) -> Path:
    """보고서를 쓴다. 지금 폴더에 못 쓰면 집 폴더에."""

    try:
        path.write_text(text, encoding="utf-8")
        return path
    except OSError:
        fallback = Path.home() / path.name
        fallback.write_text(text, encoding="utf-8")
        return fallback
