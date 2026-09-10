"""이 서버에서 **무엇이** 돌고 있는지 읽는다 (`/proc`).

## 왜 필요한가

지금까지 남긴 것은 "우리 스캔이 CPU 몇 %를 썼나"뿐이다. 그것만으로는 정작
물어야 할 것에 답할 수 없다 - **그 시각에 서버에서 다른 무엇이 돌고 있었나.**

"스캔 중 load 가 8 이었다"는 스캔이 만든 것일 수도 있고, 마침 다른 사람이
돌린 작업 때문일 수도 있다. 그 자리에 무엇이 있었는지 같이 남기지 않으면
나중에 아무리 들여다봐도 가릴 수 없다. 낮에 상세 스캔을 돌려도 되는지 같은
판단은 특히 이 구분에 걸려 있다.

## 왜 `top`을 부르지 않는가

`top`/`ps` 출력은 사람이 읽으라고 만든 것이라 로케일·버전·터미널 폭에 따라
열이 달라진다. cron(LANG=C)과 로그인 셸에서 다르게 나오는 것을 파싱으로
맞추는 일은 깨지기 쉽다. `top` 자신이 읽는 원본을 직접 보면 같은 값을 더
안정적으로 얻고, 15분마다 프로세스를 하나 더 띄우지 않아도 된다.

## 무엇을 남기는가 (그리고 남기지 않는가)

CPU 는 **두 시점의 차이**로만 구할 수 있다. 한 번 읽어서 나오는 값은 그
프로세스가 태어난 뒤 누적된 시간이라 "지금 얼마나 쓰고 있나"가 아니다.
그래서 두 벌을 떠서 `diff()` 로 견준다.

D 상태(디스크 대기)와 `procs_blocked` 를 함께 센다. `du`/`find` 는 CPU 를
거의 안 쓰고 I/O 대기를 만든다 - CPU 사용률만 보면 "부하가 거의 없다"는
결론이 나오는데, 정작 그 시간에 다른 사람의 작업은 디스크를 기다리며
느려진다. 두 값을 나란히 놓아야 그 상황이 보인다.

명령줄(`cmdline`)은 **다른 사용자의 작업 내용이 그대로 남는다.** 어떤 작업이
서버를 쓰고 있었는지 알려면 이름만으로는 부족해서 기본으로 남기되, 설정으로
끌 수 있게 두고 길이도 자른다. 켜 둔 채로 쓸지는 운영하는 쪽이 정할 일이다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

PROC = Path("/proc")
PROC_STAT = PROC / "stat"
PROC_MEMINFO = PROC / "meminfo"
PROC_LOADAVG = PROC / "loadavg"

# 명령줄을 이만큼만 남긴다. 어떤 작업인지 알아보는 데 필요한 앞부분은 남기되,
# 긴 인자 목록으로 DB 를 채우지는 않는다.
CMDLINE_MAX_CHARS = 200

# 한 표본에서 남길 프로세스 수.
#
# 서버에 프로세스가 수백 개 있어도 부하를 만든 것은 늘 몇 개다. 전부 남기면
# 15분마다 수백 행이라 한 달이면 수백만 행이 되는데, 그 대부분은 CPU 0.0% 의
# 잠든 데몬이다.
TOP_PROCESSES_KEPT = 10


def available() -> bool:
    """`/proc` 기반 관측이 가능한 환경인지 (리눅스)."""

    return PROC_STAT.exists() and (PROC / "self" / "stat").exists()


def _clock_ticks() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK")) or 100
    except (ValueError, OSError, AttributeError):
        return 100


def _page_kb() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) // 1024 or 4
    except (ValueError, OSError, AttributeError):
        return 4


CLOCK_TICKS = _clock_ticks()
PAGE_KB = _page_kb()


# ===========================================================================
# 시스템 전체
# ===========================================================================


@dataclass
class SystemCounters:
    """`/proc/stat` 한 번 읽은 값 (누적). 사용률은 두 벌의 차이로 구한다."""

    total_ticks: Optional[int] = None
    idle_ticks: Optional[int] = None
    iowait_ticks: Optional[int] = None
    steal_ticks: Optional[int] = None
    cpu_count: int = 0
    # 지금 실행 대기 중인 것과 **I/O 를 기다리며 막힌 것**. 누적이 아니라
    # 그 순간의 개수라 차이를 낼 필요가 없다.
    procs_running: Optional[int] = None
    procs_blocked: Optional[int] = None


def parse_system_counters(text: str) -> SystemCounters:
    """`/proc/stat` 본문을 읽는다."""

    result = SystemCounters()
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        key = parts[0]
        if key == "cpu":
            try:
                values = [int(value) for value in parts[1:]]
            except ValueError:
                continue
            if len(values) < 5:
                continue
            # user nice system idle iowait irq softirq steal guest guest_nice
            result.total_ticks = sum(values)
            result.idle_ticks = values[3]
            result.iowait_ticks = values[4]
            result.steal_ticks = values[7] if len(values) > 7 else 0
        elif key.startswith("cpu") and key[3:].isdigit():
            result.cpu_count += 1
        elif key == "procs_running":
            result.procs_running = _int_or_none(parts, 1)
        elif key == "procs_blocked":
            result.procs_blocked = _int_or_none(parts, 1)
    return result


def _int_or_none(parts: Sequence[str], index: int) -> Optional[int]:
    try:
        return int(parts[index])
    except (IndexError, ValueError):
        return None


@dataclass
class CpuUsage:
    """두 표본 사이의 시스템 전체 CPU. 못 재면 전부 None."""

    busy_percent: Optional[float] = None
    iowait_percent: Optional[float] = None
    steal_percent: Optional[float] = None


def cpu_usage(before: SystemCounters, after: SystemCounters) -> CpuUsage:
    if before.total_ticks is None or after.total_ticks is None:
        return CpuUsage()
    delta_total = after.total_ticks - before.total_ticks
    if delta_total <= 0:
        # 구간이 너무 짧아 값이 안 움직였다. 0으로 채우면 "한가했다"로 잘못
        # 읽히므로 모른다고 말한다.
        return CpuUsage()
    delta_idle = (after.idle_ticks or 0) - (before.idle_ticks or 0)
    delta_iowait = (after.iowait_ticks or 0) - (before.iowait_ticks or 0)
    delta_steal = (after.steal_ticks or 0) - (before.steal_ticks or 0)
    busy = delta_total - delta_idle - delta_iowait
    return CpuUsage(
        busy_percent=max(0.0, busy / delta_total * 100.0),
        iowait_percent=max(0.0, delta_iowait / delta_total * 100.0),
        steal_percent=max(0.0, delta_steal / delta_total * 100.0),
    )


@dataclass
class Memory:
    total_kb: Optional[int] = None
    available_kb: Optional[int] = None
    swap_total_kb: Optional[int] = None
    swap_free_kb: Optional[int] = None

    @property
    def used_percent(self) -> Optional[float]:
        if not self.total_kb or self.available_kb is None:
            return None
        return (self.total_kb - self.available_kb) / self.total_kb * 100.0

    @property
    def swap_used_kb(self) -> Optional[int]:
        if self.swap_total_kb is None or self.swap_free_kb is None:
            return None
        return self.swap_total_kb - self.swap_free_kb


def parse_memory(text: str) -> Memory:
    """`/proc/meminfo` 본문.

    `MemFree` 가 아니라 `MemAvailable` 을 쓴다. 리눅스는 남는 메모리를 캐시로
    채워 두므로 MemFree 는 거의 항상 작게 나오고, 그것을 부족으로 읽으면 매번
    거짓 경보가 된다. 스왑을 함께 보는 것은, 메모리가 진짜 모자랄 때 가장 먼저
    나타나는 곳이 거기라서다."""

    keys = {
        "MemTotal:": "total_kb",
        "MemAvailable:": "available_kb",
        "SwapTotal:": "swap_total_kb",
        "SwapFree:": "swap_free_kb",
    }
    result = Memory()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] not in keys:
            continue
        try:
            setattr(result, keys[parts[0]], int(parts[1]))
        except ValueError:
            continue
    return result


@dataclass
class LoadAverage:
    one: Optional[float] = None
    five: Optional[float] = None
    fifteen: Optional[float] = None
    runnable: Optional[int] = None
    total_procs: Optional[int] = None


def parse_loadavg(text: str) -> LoadAverage:
    parts = text.split()
    if len(parts) < 3:
        return LoadAverage()
    try:
        result = LoadAverage(
            one=float(parts[0]), five=float(parts[1]), fifteen=float(parts[2])
        )
    except ValueError:
        return LoadAverage()
    if len(parts) >= 4 and "/" in parts[3]:
        runnable, _, total = parts[3].partition("/")
        try:
            result.runnable = int(runnable)
            result.total_procs = int(total)
        except ValueError:
            pass
    return result


# ===========================================================================
# 프로세스
# ===========================================================================


@dataclass
class ProcInfo:
    """`/proc/<pid>/stat` 한 줄. 누적값이라 그 자체로는 사용률이 아니다."""

    pid: int
    comm: str
    state: str
    ppid: int
    cpu_ticks: int
    rss_kb: int
    threads: int
    start_ticks: int
    # 블록 I/O 를 기다린 누적 시간. 커널 설정(CONFIG_TASK_DELAY_ACCT)에 따라
    # 없을 수 있어 None 을 허용한다.
    blkio_ticks: Optional[int] = None
    uid: Optional[int] = None


def parse_proc_stat(pid: int, raw: str) -> Optional[ProcInfo]:
    """`/proc/<pid>/stat` 본문을 읽는다.

    `comm` 에는 공백도 괄호도 들어갈 수 있어서 (`(a b)` 같은 이름이 실제로
    있다) 마지막 `)` 를 기준으로 자른다. 앞에서부터 split 하면 이름에 공백이
    있는 프로세스에서 모든 열이 밀린다."""

    close = raw.rfind(")")
    open_paren = raw.find("(")
    if close == -1 or open_paren == -1 or close < open_paren:
        return None
    comm = raw[open_paren + 1:close]
    fields = raw[close + 2:].split()
    # 잘라낸 뒤로는 state 가 첫 항목(stat(5) 3번)이므로 1-based N -> N-3.
    try:
        return ProcInfo(
            pid=pid,
            comm=comm,
            state=fields[0],
            ppid=int(fields[1]),
            cpu_ticks=int(fields[11]) + int(fields[12]),
            rss_kb=int(fields[21]) * PAGE_KB,
            threads=int(fields[17]),
            start_ticks=int(fields[19]),
            blkio_ticks=int(fields[39]) if len(fields) > 39 else None,
        )
    except (IndexError, ValueError):
        return None


def snapshot() -> Dict[int, ProcInfo]:
    """지금 돌고 있는 모든 프로세스의 누적값.

    pid 하나마다 작은 파일 하나를 읽는 것이 전부다 (`/proc` 은 디스크가 아니라
    커널이 만들어 주는 것이라 I/O 가 없다). 사용자 이름과 명령줄은 여기서 읽지
    않는다 - 상위 몇 개만 나중에 채우면 되는데, 수백 개를 다 읽으면 그때부터는
    재는 행위 자체가 부하가 된다."""

    result: Dict[int, ProcInfo] = {}
    try:
        names = os.listdir(str(PROC))
    except OSError:
        return result
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        stat_path = PROC / name / "stat"
        try:
            raw = stat_path.read_text(encoding="utf-8", errors="replace")
            uid = os.stat(str(PROC / name)).st_uid
        except (OSError, ValueError):
            # 표본을 뜨는 사이에 끝난 프로세스. 정상이다.
            continue
        info = parse_proc_stat(pid, raw)
        if info is None:
            continue
        info.uid = uid
        result[pid] = info
    return result


@dataclass
class ProcessLoad:
    """두 표본 사이에 이 프로세스가 만든 부하."""

    pid: int
    comm: str
    # `top`/`ps` 와 같은 기준: 코어 하나를 다 쓰면 100%.
    cpu_percent: float
    rss_kb: int
    state: str
    threads: int = 1
    uid: Optional[int] = None
    user: Optional[str] = None
    cmdline: Optional[str] = None
    # 우리 스캔이 만든 프로세스인가. 이것이 없으면 나중에 시계열을 봐도
    # "이 부하가 우리 것인지 남의 것인지"를 가릴 수 없다.
    is_ours: bool = False
    blkio_percent: Optional[float] = None

    @property
    def rss_mb(self) -> float:
        return self.rss_kb / 1024.0


def diff(
    before: Dict[int, ProcInfo],
    after: Dict[int, ProcInfo],
    seconds: float,
    our_pids: Sequence[int] = (),
) -> List[ProcessLoad]:
    """두 표본을 견줘 프로세스별 CPU 사용률을 낸다.

    구간 중에 새로 생긴 프로세스는 **기준점이 없어 사용률을 지어낼 수 없다.**
    그래서 CPU 0% 로 두되 메모리는 그대로 싣는다 - 방금 뜬 거대한 프로세스가
    목록에서 통째로 빠지는 것이 더 나쁘기 때문이다.

    pid 는 돌려 쓰인다. 시작 시각(`start_ticks`)이 다르면 같은 번호라도 다른
    프로세스이므로 견주지 않는다 - 안 그러면 음수 사용률이 나온다."""

    if seconds <= 0:
        return []
    ours = set(our_pids)
    result: List[ProcessLoad] = []
    for pid, now in after.items():
        was = before.get(pid)
        if was is not None and was.start_ticks != now.start_ticks:
            was = None
        if was is None:
            cpu_percent = 0.0
            blkio_percent = None
        else:
            cpu_percent = max(
                0.0,
                (now.cpu_ticks - was.cpu_ticks) / CLOCK_TICKS / seconds * 100.0,
            )
            blkio_percent = None
            if now.blkio_ticks is not None and was.blkio_ticks is not None:
                blkio_percent = max(
                    0.0,
                    (now.blkio_ticks - was.blkio_ticks) / CLOCK_TICKS / seconds * 100.0,
                )
        result.append(
            ProcessLoad(
                pid=pid,
                comm=now.comm,
                cpu_percent=cpu_percent,
                rss_kb=now.rss_kb,
                state=now.state,
                threads=now.threads,
                uid=now.uid,
                is_ours=pid in ours,
                blkio_percent=blkio_percent,
            )
        )
    return result


BY_CPU = "cpu"
BY_MEMORY = "mem"


def top(
    loads: Sequence[ProcessLoad], by: str = BY_CPU, limit: int = TOP_PROCESSES_KEPT
) -> List[ProcessLoad]:
    """부하가 큰 것부터 몇 개."""

    if by == BY_MEMORY:
        key = lambda item: (item.rss_kb, item.cpu_percent)   # noqa: E731
    else:
        key = lambda item: (item.cpu_percent, item.rss_kb)   # noqa: E731
    return sorted(loads, key=key, reverse=True)[:limit]


def merge_top(
    loads: Sequence[ProcessLoad], limit: int = TOP_PROCESSES_KEPT
) -> List[ProcessLoad]:
    """CPU 상위와 메모리 상위를 합친 목록 (중복 제거).

    한 기준만 남기면 나중에 다른 기준으로 정렬할 수 없다. CPU 는 안 쓰면서
    메모리를 20GB 물고 있는 프로세스는 CPU 상위에 영영 안 잡히는데, 야간
    스캔이 메모리 때문에 밀렸는지 보려면 바로 그 프로세스가 필요하다."""

    picked: Dict[int, ProcessLoad] = {}
    for item in top(loads, BY_CPU, limit) + top(loads, BY_MEMORY, limit):
        picked.setdefault(item.pid, item)
    return sorted(picked.values(), key=lambda item: -item.cpu_percent)


_USER_CACHE: Dict[int, str] = {}


def user_name(uid: Optional[int]) -> Optional[str]:
    """uid -> 사용자 이름. 못 찾으면 숫자를 문자열로.

    폐쇄망에서 NIS/LDAP 조회가 느릴 수 있어 캐시한다 - 같은 uid 를 15분마다
    다시 묻지 않는다."""

    if uid is None:
        return None
    if uid in _USER_CACHE:
        return _USER_CACHE[uid]
    name = str(uid)
    try:
        import pwd

        name = pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError, OSError):  # Windows, 없는 uid, 조회 실패
        pass
    _USER_CACHE[uid] = name
    return name


def read_cmdline(pid: int) -> Optional[str]:
    """`/proc/<pid>/cmdline`. 인자 구분자가 NUL 이라 공백으로 바꾼다.

    커널 스레드는 비어 있다 (그때는 `comm` 이 유일한 단서다)."""

    try:
        raw = (PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    text = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    if not text:
        return None
    if len(text) > CMDLINE_MAX_CHARS:
        text = text[:CMDLINE_MAX_CHARS] + "..."
    return text


def enrich(loads: Sequence[ProcessLoad], with_cmdline: bool = True) -> None:
    """고른 몇 개에만 사용자 이름과 명령줄을 채운다 (제자리 수정)."""

    for item in loads:
        item.user = user_name(item.uid)
        if with_cmdline:
            item.cmdline = read_cmdline(item.pid)


def our_pids() -> List[int]:
    """우리 프로세스와 그 자식들의 pid.

    `du` 를 자식으로 띄우므로 자기 pid 하나만 보면 정작 일하는 쪽을 놓친다.
    같은 프로세스 그룹으로 묶여 있으니 그것으로 찾는다."""

    try:
        my_group = os.getpgid(0)
    except (OSError, AttributeError):  # Windows
        return [os.getpid()]
    found = []
    try:
        names = os.listdir(str(PROC))
    except OSError:
        return [os.getpid()]
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            if os.getpgid(pid) == my_group:
                found.append(pid)
        except OSError:
            continue
    return found or [os.getpid()]


# ===========================================================================
# 파일에서 읽기 (파싱과 나눠 둔다 - 그래야 파싱을 개발 PC 에서도 시험한다)
# ===========================================================================


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_system_counters() -> SystemCounters:
    text = _read(PROC_STAT)
    return parse_system_counters(text) if text is not None else SystemCounters()


def read_memory() -> Memory:
    text = _read(PROC_MEMINFO)
    return parse_memory(text) if text is not None else Memory()


def read_loadavg() -> LoadAverage:
    text = _read(PROC_LOADAVG)
    return parse_loadavg(text) if text is not None else LoadAverage()
