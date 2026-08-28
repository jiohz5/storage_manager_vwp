"""진단 모드 - 긴 로그 대신 **짧은 코드 몇 줄**로 상황을 되돌린다.

## 왜 이런 모양인가

반입 장비는 폐쇄망이라 화면에 뜬 것을 사람이 옮겨 적어야 한다. 그래서 진단
결과를 문장으로 늘어놓으면 정작 필요한 것이 전달되지 않는다. 여기서는 각
점검을 **글자 하나 + 숫자 하나**(`A1`, `K2`)로 줄여, 26개 점검 전체가 한 줄에
들어가게 한다. 숫자로 담을 수 없는 값(초, 배수 등)만 두 번째 줄에 짧게 붙인다.

    CODE A1B2C1D2 E3F1G1H3 ... #7c
    CPU  A3B2C1D2 E2F3G2H3
    VAL  H=87.8/36.4 I=1.00/1.62/1.95 K=0.52 ...

`CODE` 와 `CPU` 는 **서로 다른 표**다 (같은 `A` 라도 뜻이 다르다). 줄 이름을
같이 적어야 하는 이유가 그것이다.

코드의 뜻은 `PROBE_CODES.md` 에 있다. **버전(`CODE_VERSION`)을 같이 찍는
이유**가 그것이다 - 표를 고치면 옛 코드를 잘못 읽게 되므로, 뜻이 바뀌면
버전을 올린다.

끝의 `#7c` 는 검사합이다. 손으로 옮겨 적다가 한 글자가 틀리면 여기서 걸린다.

## 이 모듈이 하지 않는 것

- **아무것도 쓰지 않는다.** 대상 경로는 물론이고 스캔 DB 도 건드리지 않는다.
  설정은 이웃 계정을 찾을 때 읽기만 한다.
- 판정을 대신하지 않는다. 코드는 "무엇이 관측됐는가"까지이고, "그래서 무엇을
  바꿀 것인가"는 사람이 정한다.

## 무엇을 재는가

크게 넷이다.

1. **환경**(A~G) - 마운트 옵션과 RPC 설정. 실행 비용이 없다.
2. **속도**(H~J, N~P) - 순회와 `du` 를 실제로 돌려 비교한다.
3. **동시성**(K~M) - 이게 이 진단의 핵심 질문이다. 같은 볼륨을 나눠도
   안 빨라진다는 것은 이미 실측했고, **같은 파일러의 다른 볼륨이 어떤지**가
   아직 답이 없다. `L` 이 그 답이다.
4. **트리 모양과 장비**(Q~Z) - 왜 그런 숫자가 나왔는지 설명하는 값들.
5. **CPU 와 프로세스**(`CPU` 줄) - "안 빨라진다"의 원인을 가른다. 순회 중
   우리가 CPU 를 얼마나 태웠는지(C·D), 기다리기만 했는지(E), 그리고 스레드
   대신 프로세스로 나누면 달라지는지(F·G). 스레드가 GIL 에 막힌 것이라면
   프로세스는 늘어나고, 서버가 한계라면 프로세스도 그대로다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import loadstat, walker

# 코드표 버전. `PROBE_CODES.md` 와 짝이다 - 글자/숫자의 뜻을 바꾸면 반드시
# 올린다. 안 올리면 옛 결과를 새 표로 읽어 반대 결론이 난다.
CODE_VERSION = 3

# 시간을 재는 조각 하나의 길이(초). 동시성 비교는 트리를 끝까지 걷지 않고
# 이만큼씩 잘라서 처리량으로 비교한다 - 큰 계정에서도 시간이 예측 가능하다.
DEFAULT_SLICE_SECONDS = 15

# 스레드 확장을 볼 때 시험할 수. 실측에서 4 위로는 늘지 않았지만, 장비가
# 바뀌면 달라질 수 있으므로 확인 자체는 남긴다.
THREAD_SWEEP = (1, 2, 4, 8)
QUICK_THREAD_SWEEP = (1, 4)

# 프로세스로 나눠 볼 수. 스레드 축과 나란히 읽으려고 같은 모양으로 둔다.
PROCESS_SWEEP = (1, 2, 4)
QUICK_PROCESS_SWEEP = (1, 4)

# 프로세스에 나눠 줄 최상위 디렉터리 수의 상한. 다 나눠 주면 작은 것 수백 개가
# pickle 로 오가느라 측정이 오염된다.
MAX_PROCESS_SUBTREES = 64

# 이 배속 미만이면 "더 늘려도 의미 없다"고 본다. 측정 흔들림이 10% 안팎이라
# 그보다 넉넉히 잡았다.
SCALING_GAIN = 1.15


@dataclass
class Check:
    """점검 하나. 코드 한 글자와 숫자 하나로 줄어든다."""

    letter: str
    title: str
    digit: int = 0          # 0 = 재지 못함 (없음/불가/건너뜀)
    note: str = ""          # 사람이 읽을 한 줄 (전달용 아님)
    raw: Optional[str] = None   # 코드로 못 담는 원값. 짧게.


@dataclass
class ProbeResult:
    checks: List[Check] = field(default_factory=list)
    # `CPU` 줄은 **다른 표**다. 같은 글자라도 뜻이 다르므로 섞어 두지 않는다.
    cpu: List[Check] = field(default_factory=list)
    path: str = ""
    warnings: List[str] = field(default_factory=list)

    def by_letter(self, letter: str, group: str = "CODE") -> Optional[Check]:
        source = self.cpu if group == "CPU" else self.checks
        for check in source:
            if check.letter == letter:
                return check
        return None


def bucket(value: Optional[float], edges: Sequence[float]) -> int:
    """값을 1부터 시작하는 구간 번호로 바꾼다.

    `edges=[a, b, c]` 면 `value < a` -> 1, `< b` -> 2, `< c` -> 3, 나머지 4.
    값이 없으면 0 (= 재지 못함). **0을 1과 섞지 않는 것이 중요하다** - "쟀는데
    가장 낮은 구간"과 "재지 못함"은 완전히 다른 이야기다.
    """

    if value is None:
        return 0
    for index, edge in enumerate(edges):
        if value < edge:
            return index + 1
    return len(edges) + 1


# ---------------------------------------------------------------- 환경 읽기


def _mount_lines(source: Optional[Path] = None) -> List[str]:
    for candidate in ([source] if source else [Path("/proc/mounts"), Path("/etc/mtab")]):
        try:
            return candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
    return []


def mount_for(path: Path, source: Optional[Path] = None) -> Optional[dict]:
    """이 경로가 올라탄 마운트 한 줄을 `{origin, point, fstype, options}` 로.

    가장 **긴** 마운트 지점이 실제 마운트다 (`/` 는 언제나 접두사이므로).
    """

    # 심링크는 POSIX 에서만 푼다. 개발 PC 에서 `/user/cae` 를 풀면
    # `C:\user\cae` 가 되어 마운트 지점과 비교 자체가 깨진다
    # (`paths._mount_comparable` 이 같은 이유로 같은 판단을 한다).
    target = str(path)
    if os.name == "posix":
        try:
            target = os.path.realpath(target)
        except OSError:  # pragma: no cover - 방어적 처리
            pass
    target = target.replace("\\", "/").rstrip("/") or "/"

    best = None
    for line in _mount_lines(source):
        parts = line.split()
        if len(parts) < 4:
            continue
        origin, point, fstype, options = parts[0], parts[1], parts[2], parts[3]
        point = point.replace("\\040", " ")
        if target == point or target.startswith(point.rstrip("/") + "/"):
            if best is None or len(point) >= len(best["point"]):
                best = {
                    "origin": origin,
                    "point": point,
                    "fstype": fstype,
                    "options": options.split(","),
                }
    return best


def _option_value(options: Sequence[str], name: str) -> Optional[str]:
    prefix = name + "="
    for option in options:
        if option == name:
            return ""
        if option.startswith(prefix):
            return option[len(prefix):]
    return None


def _read_int(path: str) -> Optional[int]:
    try:
        return int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def environment_checks(path: Path, mount_source: Optional[Path] = None) -> List[Check]:
    """A~G. 실행 비용이 없는 것들 - 파일 몇 개를 읽는 게 전부다."""

    mount = mount_for(path, mount_source)
    options = mount["options"] if mount else []
    fstype = (mount or {}).get("fstype", "")

    # A: 대상이 무엇 위에 있는가. 뒤의 모든 숫자를 읽는 전제다.
    if not mount:
        a = Check("A", "대상 파일시스템", 0, "마운트 정보를 읽지 못함 (리눅스가 아니거나 /proc 없음)")
    elif fstype == "nfs4":
        a = Check("A", "대상 파일시스템", 1, f"nfs4 ({mount['origin']})", mount["origin"])
    elif fstype == "nfs":
        a = Check("A", "대상 파일시스템", 2, f"nfs3 ({mount['origin']})", mount["origin"])
    elif fstype in ("xfs", "ext4", "ext3", "btrfs", "zfs", "overlay"):
        a = Check("A", "대상 파일시스템", 3, f"로컬 {fstype}", mount["origin"])
    else:
        a = Check("A", "대상 파일시스템", 4, f"기타 {fstype}", mount["origin"])

    # B: NFS 버전. 4.1+ 라야 세션/트렁킹 이야기를 할 수 있다.
    version = _option_value(options, "vers") or _option_value(options, "nfsvers")
    if version is None:
        if mount and fstype not in ("nfs", "nfs4"):
            b = Check("B", "NFS 버전", 4, "NFS 아님")
        else:
            b = Check("B", "NFS 버전", 0, "확인 불가")
    elif version.startswith("4.1") or version.startswith("4.2"):
        b = Check("B", "NFS 버전", 1, f"v{version}", version)
    elif version.startswith("4"):
        b = Check("B", "NFS 버전", 2, f"v{version}", version)
    else:
        b = Check("B", "NFS 버전", 3, f"v{version}", version)

    # C: nconnect. 커널이 서버당 TCP 연결을 몇 개 쓰는가 - 병렬 요청이 실제로
    # 여러 갈래로 나가는지를 가르는 값이다. 1이면 아무리 스레드를 늘려도
    # 한 연결에 줄을 선다.
    nconnect = _option_value(options, "nconnect")
    if not mount or fstype not in ("nfs", "nfs4"):
        c = Check("C", "nconnect", 4, "NFS 아님")
    elif nconnect is None:
        c = Check("C", "nconnect", 1, "설정 없음 (연결 1개)", "1")
    else:
        value = int(nconnect) if nconnect.isdigit() else 0
        c = Check("C", "nconnect", 2 if value <= 3 else 3, f"nconnect={value}", str(value))

    # D: rsize. 메타데이터에는 덜 중요하지만, 큰 파일을 읽는 다른 작업과
    # 같은 마운트를 쓰는지 가늠하는 데 쓴다.
    rsize = _option_value(options, "rsize")
    if rsize is None or not rsize.isdigit():
        d = Check("D", "rsize", 4 if mount else 0, "확인 불가 또는 NFS 아님")
    else:
        kb = int(rsize) // 1024
        d = Check("D", "rsize", bucket(kb, [65, 129, 257]), f"rsize={kb}K", f"{kb}K")

    # E: RPC 슬롯 상한. 동시에 떠 있을 수 있는 요청 수의 천장이다.
    slots = _read_int("/proc/sys/sunrpc/tcp_max_slot_table_entries")
    if slots is None:
        e = Check("E", "RPC 슬롯 상한", 0, "확인 불가")
    else:
        e = Check("E", "RPC 슬롯 상한", bucket(slots, [17, 65, 129]), f"{slots}", str(slots))

    # F: 속성 캐시. `noac`/`actimeo=0` 이면 stat 마다 서버에 묻는다 - 순회
    # 속도가 통째로 달라지므로 숫자가 이상할 때 가장 먼저 볼 곳이다.
    if not mount or fstype not in ("nfs", "nfs4"):
        f = Check("F", "속성 캐시", 4, "NFS 아님")
    elif "noac" in options or _option_value(options, "actimeo") == "0":
        f = Check("F", "속성 캐시", 2, "noac/actimeo=0 - stat 마다 서버 왕복", "off")
    elif _option_value(options, "actimeo"):
        f = Check("F", "속성 캐시", 3, f"actimeo={_option_value(options, 'actimeo')}")
    else:
        f = Check("F", "속성 캐시", 1, "기본값")

    # G: readdirplus. 끄면 디렉터리를 읽은 뒤 파일마다 stat 을 또 보낸다.
    if not mount or fstype not in ("nfs", "nfs4"):
        g = Check("G", "readdirplus", 4, "NFS 아님")
    elif "nordirplus" in options:
        g = Check("G", "readdirplus", 2, "nordirplus - 항목마다 stat 왕복", "off")
    else:
        g = Check("G", "readdirplus", 1, "사용")

    return [a, b, c, d, e, f, g]


# ---------------------------------------------------------------- CPU 계측


def cpu_topology() -> dict:
    """`{logical, physical, threads_per_core}`. 모르면 값이 None.

    논리 CPU 수만으로는 "스레드를 몇 개까지 늘려도 되나"를 못 정한다.
    하이퍼스레딩이면 논리 32개라도 실제 연산 자원은 16개다 - 순회처럼 대기가
    많은 일에는 논리 수가 유효하지만, CPU 를 태우는 구간에서는 물리 수가
    천장이다. 둘을 구분해서 남긴다.
    """

    logical = os.cpu_count()
    physical = None
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"logical": logical, "physical": None, "threads_per_core": None}

    # (물리 소켓 id, 코어 id) 쌍의 가짓수가 물리 코어 수다. 소켓이 여럿인
    # 장비에서 `cpu cores` 하나만 보면 소켓 하나 분량만 세게 된다.
    pairs = set()
    physical_id = core_id = None
    for line in text.splitlines():
        if ":" not in line:
            if physical_id is not None and core_id is not None:
                pairs.add((physical_id, core_id))
            physical_id = core_id = None
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "physical id":
            physical_id = value
        elif key == "core id":
            core_id = value
    if physical_id is not None and core_id is not None:
        pairs.add((physical_id, core_id))

    if pairs:
        physical = len(pairs)
    per_core = (logical / physical) if (logical and physical) else None
    return {"logical": logical, "physical": physical, "threads_per_core": per_core}


@dataclass
class CpuUse:
    """한 측정 구간 동안 실제로 쓴 CPU.

    `cores` 는 **코어 환산**이다 - 2.0 이면 두 코어를 꽉 채워 쓴 것이고,
    0.2 면 대부분 기다린 것이다. 이 값 하나가 "우리가 느린가, 서버가
    느린가"를 가른다.
    """

    cores: Optional[float] = None          # 우리 프로세스(+자식)가 쓴 코어 수
    system_busy: Optional[float] = None    # 장비 전체 CPU 사용률 %
    system_iowait: Optional[float] = None  # 장비 전체 iowait %


class CpuMeter:
    """`with` 구간 동안 우리가 쓴 CPU 와 장비 전체 상태를 함께 잰다.

    우리 것과 장비 전체를 **둘 다** 봐야 한다. 우리 CPU 만 보면 옆에서 도는
    다른 작업 때문에 느린 것을 우리 탓으로 돌리고, 장비 전체만 보면 그 반대가
    된다.
    """

    def __init__(self):
        self.result = CpuUse()
        self._sampler = None
        self._started = 0.0
        self._cpu_started = 0.0

    def _process_cpu(self) -> float:
        total = time.process_time()   # 이 프로세스의 모든 스레드
        try:
            import resource

            children = resource.getrusage(resource.RUSAGE_CHILDREN)
            total += children.ru_utime + children.ru_stime
        except Exception:  # pragma: no cover - 윈도우 등
            pass
        return total

    def __enter__(self):
        self._sampler = loadstat.SystemSampler()
        self._started = time.perf_counter()
        self._cpu_started = self._process_cpu()
        return self

    def __exit__(self, *exc_info):
        elapsed = time.perf_counter() - self._started
        used = self._process_cpu() - self._cpu_started
        system = self._sampler.take()
        self.result = CpuUse(
            cores=(used / elapsed) if elapsed > 0 else None,
            system_busy=system.busy_percent,
            system_iowait=system.iowait_percent,
        )
        return False


# ---------------------------------------------------------------- 측정 도구


@dataclass
class Slice:
    """시간을 잘라 잰 한 조각."""

    seconds: float
    items: int

    @property
    def rate(self) -> float:
        return self.items / self.seconds if self.seconds > 0 else 0.0


def timed_walk(
    path: str, workers: int, limit_seconds: Optional[float], repeat: bool = True
) -> Slice:
    """`limit_seconds` 동안 걷고 초당 항목 수를 낸다.

    ## 왜 다 걷고 나서도 반복하는가

    조각이 **항상 같은 길이**여야 비교가 성립한다. 트리가 작아 3초 만에 끝나
    버리면, 동시 실행 비교에서 두 쪽이 서로 겹치지도 않은 채 끝나 "간섭이
    없다"는 잘못된 결론이 난다. 그래서 시간이 남으면 다시 걷는다.

    큰 트리에서는 첫 순회가 마감에 잘려 그대로 끝나므로 동작이 같다.

    끝까지 못 걸어도 상관없다 - 여기서 보는 것은 총량이 아니라 **속도**다.
    """

    # `perf_counter` 를 쓴다. `monotonic` 은 윈도우에서 15.6ms 단위라 빠른
    # 순회가 통째로 "0초"가 되고, 그러면 처리량이 0으로 잘못 보고된다.
    started = time.perf_counter()
    items = 0
    while True:
        remaining = None
        if limit_seconds is not None:
            remaining = limit_seconds - (time.perf_counter() - started)
            if remaining <= 0:
                break
        outcome = walker.walk_tree(
            path, timeout_seconds=remaining, max_depth=0, workers=workers
        )
        items += outcome.file_count + outcome.dir_count
        if not repeat or limit_seconds is None:
            break
    elapsed = time.perf_counter() - started
    return Slice(seconds=elapsed, items=items)


def run_du_sk(path: str, timeout_seconds: float) -> "tuple[Optional[float], Optional[int]]":
    """`du -sk` 를 한 번 돌려 `(걸린 시간, KB)`. 못 돌면 `(None, None)`."""

    try:
        started = time.perf_counter()
        proc = subprocess.run(
            ["du", "-sk", path],
            capture_output=True, text=True, timeout=timeout_seconds,
        )
        elapsed = time.perf_counter() - started
    except (OSError, subprocess.SubprocessError):
        return None, None
    first = (proc.stdout or "").split("\t")[0].strip()
    if not first.isdigit():
        return elapsed, None
    return elapsed, int(first)


def concurrent_rates(
    left: str, right: str, workers: int, slice_seconds: float
) -> "tuple[Slice, Slice]":
    """두 경로를 **동시에** 걷고 각각의 처리량을 돌려준다."""

    results = {}

    def work(key, path):
        results[key] = timed_walk(path, workers, slice_seconds)

    threads = [
        threading.Thread(target=work, args=("left", left), name="probe-left"),
        threading.Thread(target=work, args=("right", right), name="probe-right"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results["left"], results["right"]


def pair_efficiency(
    left: str, right: str, workers: int, slice_seconds: float, log=None
) -> Optional[float]:
    """두 경로를 동시에 돌렸을 때 **각자 속도를 얼마나 지키는가** (1.0 = 그대로).

    ## 순서를 이렇게 짠 이유

        혼자 A -> 혼자 B -> 동시 A·B -> 혼자 A -> 혼자 B

    한 번씩만 재면 캐시가 점점 더워지는 흐름과 구분이 안 된다. 앞뒤로 한 번씩
    재서 평균을 쓰면 그 흐름이 상쇄된다. 실측이 이 함정을 이미 한 번
    보여줬다 - 처음에 `du` 를 먼저 돌렸더니 뒤따른 순회가 실제보다 빨라 보였다.

    0.5 근처면 "합이 그대로" = 이미 포화, 1.0 근처면 "각자 제 속도" = 여유.
    """

    def measure(label, path):
        result = timed_walk(path, workers, slice_seconds)
        if log:
            log(f"    {label}: {result.rate:,.0f} 항목/초")
        return result

    alone_left_1 = measure("혼자 A", left)
    alone_right_1 = measure("혼자 B", right)
    both_left, both_right = concurrent_rates(left, right, workers, slice_seconds)
    if log:
        log(f"    동시 A: {both_left.rate:,.0f} 항목/초")
        log(f"    동시 B: {both_right.rate:,.0f} 항목/초")
    alone_left_2 = measure("혼자 A(재확인)", left)
    alone_right_2 = measure("혼자 B(재확인)", right)

    left_alone = (alone_left_1.rate + alone_left_2.rate) / 2
    right_alone = (alone_right_1.rate + alone_right_2.rate) / 2
    if left_alone <= 0 or right_alone <= 0:
        return None
    return (both_left.rate / left_alone + both_right.rate / right_alone) / 2


# ------------------------------------------------------------ 프로세스 측정


def _walk_share(job) -> "tuple":
    """프로세스 하나가 맡은 서브트리들을 훑는다. `(항목 수, 못 읽은 수)`.

    **모듈 최상위 함수여야 한다** - 프로세스 풀이 함수를 pickle 로 보내므로
    지역 함수나 람다는 쓸 수 없다.

    돌려주는 것은 숫자 둘뿐이다. 경로 목록을 통째로 넘기면 pickle 비용이
    측정 자체를 오염시킨다.
    """

    subtrees, workers, deadline = job
    items = 0
    unreadable = 0
    # 스레드 쪽과 같은 조건이어야 비교가 성립한다 - 거기서도 시간이 남으면
    # 다시 걷는다(`timed_walk`). 여기만 일찍 끝내면 프로세스가 불리해진다.
    while True:
        for path in subtrees:
            remaining = deadline - time.monotonic() if deadline else None
            if remaining is not None and remaining <= 0:
                return items, unreadable
            outcome = walker.walk_tree(
                path, timeout_seconds=remaining, max_depth=0, workers=workers
            )
            items += outcome.file_count + outcome.dir_count
            unreadable += outcome.unreadable
        if deadline is None:
            return items, unreadable


def can_fork() -> bool:
    """프로세스 측정을 할 수 있는가.

    `fork` 만 쓴다. `spawn`(윈도우 기본)은 자식이 주 모듈을 다시 임포트하므로,
    진단을 돌린 방식에 따라 예상 못 한 재실행이 일어난다. 반입 장비는
    리눅스라 `fork` 가 있고, 없으면 이 측정만 건너뛴다(코드 0).
    """

    try:
        import multiprocessing

        return "fork" in multiprocessing.get_all_start_methods()
    except Exception:  # pragma: no cover - 방어적 처리
        return False


def process_slice(
    subtrees: List[str], procs: int, workers: int, slice_seconds: float
) -> Slice:
    """최상위 디렉터리를 프로세스 `procs` 개에 나눠 `slice_seconds` 동안 훑는다.

    나누는 단위를 야간 스캔의 체크포인트와 같게 두었다 - 실제로 병렬화할 수
    있는 단위가 그것이라, 여기서만 되는 방식으로 재면 쓸모가 없다.
    """

    import multiprocessing

    buckets = [[] for _ in range(procs)]
    for index, subtree in enumerate(subtrees):
        buckets[index % procs].append(subtree)
    deadline = time.monotonic() + slice_seconds
    jobs = [(bucket, workers, deadline) for bucket in buckets if bucket]

    started = time.perf_counter()
    items = 0
    context = multiprocessing.get_context("fork")
    with context.Pool(processes=len(jobs)) as pool:
        for count, _unreadable in pool.map(_walk_share, jobs):
            items += count
    return Slice(seconds=time.perf_counter() - started, items=items)


# ---------------------------------------------------------------- 이웃 찾기


def filer_of(volume: str) -> str:
    """`ecfiler:/vol/cae` -> `ecfiler`. 로컬 장치면 그대로.

    이름이 다르다고 반드시 다른 장비인 것은 아니다 (한 클러스터의 여러 LIF 일
    수 있다). 그래서 이 값으로 판정하지 않고, **무엇과 무엇을 짝지어 재볼지**를
    고르는 데만 쓴다.
    """

    return volume.split(":", 1)[0] if ":" in volume else volume


def find_peers(target: Path, accounts, volume_key) -> "tuple[Optional[str], Optional[str]]":
    """`(같은 파일러 다른 볼륨, 다른 파일러)` 경로를 계정 목록에서 고른다.

    없으면 None - 그 점검은 `0`(재지 못함)으로 남는다. 짐작으로 채우면 안 되는
    자리다.
    """

    target_volume = volume_key(target)
    target_filer = filer_of(target_volume)

    same_filer = None
    other_filer = None
    for account in accounts or []:
        try:
            path = Path(account.path)
        except (TypeError, ValueError):
            continue
        if not path.exists():
            continue
        volume = volume_key(path)
        if volume == target_volume:
            continue
        # 설정에 적힌 문자열을 그대로 돌려준다. `Path` 를 거치면 구분자가
        # 바뀌어(개발 PC), 사람이 준 경로와 화면에 뜨는 경로가 달라진다.
        if filer_of(volume) == target_filer:
            same_filer = same_filer or account.path
        else:
            other_filer = other_filer or account.path
    return same_filer, other_filer


def largest_subdirs(entries, root: str, count: int = 2) -> List[str]:
    """루트 바로 아래에서 큰 디렉터리를 고른다 (같은 볼륨 짝짓기에 쓴다).

    비슷한 크기끼리 재야 한쪽이 먼저 끝나 버리는 일이 없다. 그래서 큰 것부터
    이어진 것으로 고른다.
    """

    prefix = root.rstrip("/").rstrip("\\")
    children = []
    for path, size_kb in entries:
        normalized = path.replace("\\", "/").rstrip("/")
        base = prefix.replace("\\", "/")
        if not normalized.startswith(base + "/"):
            continue
        rest = normalized[len(base) + 1:]
        if "/" in rest:
            continue
        children.append((size_kb, path))
    children.sort(reverse=True)
    return [path for _, path in children[:count]]




def cpu_checks(
    topology: dict,
    single: Optional[CpuUse],
    parallel: Optional[CpuUse],
    thread_rates: Sequence[float],
    thread_counts: Sequence[int],
    process_rates: Sequence[float],
    process_counts: Sequence[int],
) -> List[Check]:
    """`CPU` 줄. "안 빨라진다"의 원인을 가르는 값들.

    ## 읽는 법

    - **C·D 가 낮고 E 가 높다** -> 우리는 기다리고만 있다. 스레드를 늘리는 것이
      맞는 방향이고, 안 늘어난다면 서버나 연결이 천장이다.
    - **D 가 코어 수에 가깝다** -> 우리가 CPU 를 태우고 있다. 이때는 프로세스로
      나누는 것이 듣는다(GIL).
    - **G1 (프로세스가 확실히 빠름)** -> GIL 이 병목이었다는 뜻.
    - **G2 (비슷)** -> 파이썬 쪽이 아니라 저장소/연결이 천장이다.
    """

    logical = topology.get("logical")
    physical = topology.get("physical")
    per_core = topology.get("threads_per_core")

    checks = [
        Check(
            "A", "논리 CPU 수",
            bucket(logical, [8, 32, 64]),
            (
                f"논리 {logical} / 물리 {physical}" if (logical and physical)
                else (f"논리 {logical} (물리 코어 수 확인 불가)" if logical else "확인 불가")
            ),
            f"{logical}/{physical}" if logical and physical else (str(logical) if logical else None),
        ),
        Check(
            "B", "하이퍼스레딩",
            0 if not per_core else (1 if per_core < 1.5 else (2 if per_core < 2.5 else 3)),
            f"코어당 스레드 {per_core:.1f}" if per_core else "확인 불가",
        ),
    ]

    # C: 스레드 하나로 돌 때 우리가 태운 CPU. 1코어에 가까우면 파이썬 쪽이
    # 이미 한 코어를 다 쓰는 것이고, 낮으면 대부분 기다린 것이다.
    single_cores = single.cores if single else None
    checks.append(Check(
        "C", "x1 CPU 점유",
        bucket(single_cores, [0.25, 0.7, 1.0]),
        f"{single_cores:.2f} 코어" if single_cores is not None else "확인 불가",
        f"{single_cores:.2f}" if single_cores is not None else None,
    ))

    # D: 스레드를 늘렸을 때 실제로 몇 코어를 태웠는가. 여기가 낮은데 속도도
    # 안 늘면 병목은 우리 CPU 가 아니다.
    parallel_cores = parallel.cores if parallel else None
    checks.append(Check(
        "D", "병렬 CPU 점유",
        bucket(parallel_cores, [1.0, 2.0, 4.0]),
        f"{parallel_cores:.2f} 코어" if parallel_cores is not None else "확인 불가",
        f"{parallel_cores:.2f}" if parallel_cores is not None else None,
    ))

    # E: 장비 전체 iowait. 우리 CPU 가 낮은 이유가 "기다리는 중"인지 확인한다.
    iowait = parallel.system_iowait if parallel else None
    checks.append(Check(
        "E", "iowait",
        bucket(iowait, [5, 20, 50]),
        f"{iowait:.1f}%" if iowait is not None else "확인 불가 (/proc 없음)",
        f"{iowait:.0f}%" if iowait is not None else None,
    ))

    # F: 프로세스를 늘리면 어디까지 늘어나는가.
    saturated = _saturation_point(process_rates, process_counts)
    checks.append(Check(
        "F", "프로세스 확장 포화점",
        {1: 1, 2: 2, 4: 3, 8: 4, 16: 5}.get(saturated, 0),
        f"x{saturated} 이후로는 늘지 않음" if saturated else "재지 못함 (fork 불가 또는 건너뜀)",
        "/".join(f"{rate / process_rates[0]:.2f}" for rate in process_rates)
        if process_rates and process_rates[0] > 0 else None,
    ))

    # G: 같은 동시성에서 프로세스가 스레드보다 나은가. **이 한 칸이 GIL
    # 가설의 답이다.**
    best_threads = max(thread_rates) if thread_rates else None
    best_procs = max(process_rates) if process_rates else None
    if best_threads and best_procs:
        ratio = best_procs / best_threads
        digit = 1 if ratio >= 1.2 else (2 if ratio >= 0.8 else 3)
        checks.append(Check(
            "G", "프로세스 대 스레드",
            digit,
            f"프로세스가 스레드의 {ratio:.2f}배",
            f"{ratio:.2f}",
        ))
    else:
        checks.append(Check("G", "프로세스 대 스레드", 0, "재지 못함"))

    # H: 어떤 방식으로든 x1 대비 얼마나 벌었는가. 병렬화 전체의 성적표다.
    base = thread_rates[0] if thread_rates else None
    best = max([rate for rate in list(thread_rates) + list(process_rates) if rate], default=None)
    gain = (best / base) if (base and best) else None
    checks.append(Check(
        "H", "최대 달성 배율",
        bucket(gain, [1.2, 2.0, 4.0]),
        f"x1 대비 {gain:.2f}배" if gain else "재지 못함",
        f"{gain:.2f}" if gain else None,
    ))
    return checks


def _saturation_point(rates: Sequence[float], counts: Sequence[int]) -> Optional[int]:
    """더 늘려도 의미가 없어지는 지점. 재지 못했으면 None."""

    if not rates or not counts or rates[0] <= 0:
        return None
    index = 0
    for position in range(1, len(rates)):
        if rates[position] > rates[position - 1] * SCALING_GAIN:
            index = position
    return counts[index]


# ---------------------------------------------------------------- 장비 상태


def system_checks(peak_rss_kb: Optional[int]) -> List[Check]:
    """W~Z. 숫자를 해석할 때 필요한 배경."""

    # W: 재는 동안 장비가 한가했는가. 이미 바빴다면 아래 속도들을 낮춰 읽어야
    # 한다 - 이 값 없이는 "느리다"의 원인을 우리 쪽으로 잘못 돌린다.
    load = None
    cpus = os.cpu_count() or 1
    try:
        load = float(Path("/proc/loadavg").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        load = None
    ratio = (load / cpus) if load is not None else None
    w = Check(
        "W", "장비 부하 여유",
        bucket(ratio, [0.3, 0.7]),
        f"load {load} / cpu {cpus}" if load is not None else "확인 불가",
        f"{load}/{cpus}" if load is not None else None,
    )

    # X: 순회가 쓴 최대 메모리. 트리가 커지면 노드 수만큼 늘어나므로, 아주 큰
    # 계정에서 이 값이 문제가 되는지 미리 본다.
    x = Check(
        "X", "순회 최대 메모리",
        bucket(peak_rss_kb / 1024 if peak_rss_kb else None, [200, 1024]),
        f"{peak_rss_kb // 1024}MB" if peak_rss_kb else "확인 불가",
        f"{peak_rss_kb // 1024}M" if peak_rss_kb else None,
    )

    # Y: 우선순위를 낮출 수단이 있는가. 파이썬 순회에는 이 접두사가 듣지
    # 않지만, cron 항목에 걸 수 있는지는 여전히 이 값으로 판단한다.
    has_nice = shutil.which("nice") is not None
    has_ionice = shutil.which("ionice") is not None
    y = Check(
        "Y", "nice/ionice",
        1 if (has_nice and has_ionice) else (2 if has_nice else 3),
        f"nice={has_nice} ionice={has_ionice}",
    )

    # Z: `st_blocks` 가 없으면 크기가 근사값이다 (개발 PC 가 그렇다).
    z = Check(
        "Z", "st_blocks 지원",
        1 if walker.HAVE_ST_BLOCKS else 2,
        "정확" if walker.HAVE_ST_BLOCKS else "근사값 - du 와 다를 수 있음",
    )
    return [w, x, y, z]


def _peak_rss_kb() -> Optional[int]:
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        return None
    # 리눅스는 KB, macOS 는 바이트로 준다. 큰 값이면 바이트로 본다.
    return value // 1024 if value > 10_000_000 else value


# ---------------------------------------------------------------- 본체


def run_probe(
    path: str,
    accounts=None,
    volume_key=None,
    slice_seconds: float = DEFAULT_SLICE_SECONDS,
    workers: int = 4,
    quick: bool = False,
    process_threads: int = 2,
    peer_same_filer: Optional[str] = None,
    peer_other_filer: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
    du_timeout: float = 3600.0,
) -> ProbeResult:
    """전체 진단을 돌리고 `ProbeResult` 를 준다.

    순서에 뜻이 있다. `du` -> 순회 -> `du` 로 **du 를 앞뒤로 한 번씩** 돌린다.
    한쪽만 재면 나중에 돈 쪽이 캐시 덕을 보고, 그 상태에서 나온 승패는 아무것도
    말해 주지 않는다. 앞뒤 평균을 쓰면 그 흐름이 상쇄된다.

    (예전에는 순회를 먼저 하고 `du` 를 나중에 한 번만 돌렸다. "그래도 순회가
    빠르면 보수적으로 안전하다"고 적어 두었는데, **반대 결과가 나오면 아무
    결론도 못 낸다**는 점을 놓친 설계였다.)
    """

    say = log or (lambda _message: None)
    result = ProbeResult(path=str(path))
    target = Path(path)

    checks: List[Check] = []
    checks.extend(environment_checks(target))

    # --- 0. du -sk (순회 앞) ----------------------------------------------
    #
    # 이 첫 번째가 가장 차가운 상태에서 도는 값이다. 야간 스캔이 실제로 만나는
    # 조건이 이쪽이라, 절대값으로는 이 값이 가장 쓸모 있다.
    say("[0/5] du -sk 기준 (순회 앞, 가장 차가운 상태)")
    du_before, du_kb_before = run_du_sk(str(target), du_timeout)
    if du_before is None:
        say("      du 를 실행하지 못했습니다 (없거나 실패)")
    else:
        say(f"      {du_before:,.1f}초" + (f" · {du_kb_before:,} KB" if du_kb_before else ""))

    # --- 1. 전체 순회 한 번 (모양과 크기는 여기서 다 나온다) --------------
    say("[1/5] 전체 순회 (스레드 %d)" % workers)
    started = time.perf_counter()
    full = walker.walk_tree(str(target), max_depth=1, workers=workers)
    walk_seconds = time.perf_counter() - started
    say(f"      {walk_seconds:,.1f}초 · 파일 {full.file_count:,} · 디렉터리 {full.dir_count:,}")

    if not full.completed:
        result.warnings.append(
            "순회가 완주하지 못했습니다 - 아래 숫자는 일부만 본 값입니다."
        )

    # --- 2. du -sk (순회 뒤에 한 번 더) -----------------------------------
    #
    # 앞의 `du` 는 순회보다 먼저 돌았고 이 `du` 는 나중에 돈다. 둘의 평균을
    # 써야 순회와 조건이 맞는다 - 한쪽만 재면 나중에 돈 쪽이 캐시 덕을 보고,
    # 그 상태에서 나온 승패는 아무것도 말해 주지 않는다.
    say("[2/5] du -sk 비교 (순회 뒤)")
    du_after, du_kb_after = run_du_sk(str(target), du_timeout)
    if du_after is None:
        say("      du 를 실행하지 못했습니다 (없거나 실패)")
    else:
        say(f"      {du_after:,.1f}초" + (f" · {du_kb_after:,} KB" if du_kb_after else ""))

    du_times = [value for value in (du_before, du_after) if value]
    du_seconds = sum(du_times) / len(du_times) if du_times else None
    du_kb = du_kb_after or du_kb_before

    # H: 같은 트리에서 순회가 du 보다 몇 배인가 (앞뒤 du 평균 기준).
    speed = (du_seconds / walk_seconds) if (du_seconds and walk_seconds > 0) else None
    if du_before and du_after:
        raw = f"{du_before:.1f}+{du_after:.1f}/{walk_seconds:.1f}"
    elif du_seconds:
        raw = f"{du_seconds:.1f}/{walk_seconds:.1f}"
    else:
        raw = None
    checks.append(Check(
        "H", "순회 대 du 배속",
        0 if speed is None else (5 if speed < 1.0 else bucket(speed, [1.2, 2.0, 4.0])),
        (
            f"{speed:.2f}배 (du {du_before:.1f}초/{du_after:.1f}초 평균)"
            if speed and du_before and du_after
            else (f"{speed:.2f}배" if speed else "du 실행 불가")
        ),
        raw,
    ))

    # J: 크기가 같은가. 이게 어긋나면 위의 속도 이야기는 의미가 없다.
    if du_kb and full.root_size_kb:
        gap = abs(full.root_size_kb - du_kb) / du_kb * 100
        checks.append(Check(
            "J", "크기 일치",
            bucket(gap, [0.001, 0.1, 1.0]),
            f"차이 {gap:.3f}%",
            f"{gap:.3f}%",
        ))
    else:
        checks.append(Check("J", "크기 일치", 0 if du_kb is None else 5, "du 값 없음"))

    # --- 3. 스레드 확장 ----------------------------------------------------
    sweep = QUICK_THREAD_SWEEP if quick else THREAD_SWEEP
    say(f"[3/5] 스레드 확장 {sweep} (조각당 {slice_seconds:.0f}초)")
    rates = []
    cpu_single = None
    cpu_parallel = None
    for count in sweep:
        # 조각마다 CPU 를 함께 잰다. 속도만 보면 "안 빨라졌다"까지밖에 모르고,
        # 그것이 우리가 CPU 를 다 쓴 탓인지 기다린 탓인지는 여기서 갈린다.
        with CpuMeter() as meter:
            piece = timed_walk(str(target), count, slice_seconds)
        rates.append(piece.rate)
        if count == sweep[0]:
            cpu_single = meter.result
        cpu_parallel = meter.result   # 마지막(=가장 많은 스레드) 구간이 남는다
        cores = meter.result.cores
        say(
            f"      x{count}: {piece.rate:,.0f} 항목/초"
            + (f" · CPU {cores:.2f}코어" if cores is not None else "")
        )

    base = rates[0] if rates else 0
    saturated_at = _saturation_point(rates, sweep)
    checks.append(Check(
        "I", "스레드 확장 포화점",
        {1: 1, 2: 2, 4: 3, 8: 4, 16: 5}.get(saturated_at, 0),
        f"x{saturated_at} 이후로는 늘지 않음" if saturated_at else "재지 못함",
        "/".join(f"{rate / base:.2f}" for rate in rates) if base else None,
    ))

    # --- 3b. 프로세스 축 -------------------------------------------------
    #
    # 스레드가 안 늘 때 원인이 둘이다: 파이썬(GIL)이거나 저장소다. 프로세스로
    # 나눠 보면 갈린다 - GIL 이면 늘어나고, 저장소면 그대로다.
    process_rates: List[float] = []
    process_counts = list(QUICK_PROCESS_SWEEP if quick else PROCESS_SWEEP)
    subtrees = largest_subdirs(full.entries, str(target), MAX_PROCESS_SUBTREES)
    if not can_fork():
        say("[3b] 프로세스 축: fork 를 쓸 수 없어 건너뜁니다")
        process_counts = []
    elif len(subtrees) < 2:
        say("[3b] 프로세스 축: 나눌 하위 디렉터리가 부족해 건너뜁니다")
        process_counts = []
    else:
        say(
            f"[3b] 프로세스 확장 {tuple(process_counts)}"
            f" (프로세스마다 스레드 {process_threads}개)"
        )
        for count in process_counts:
            piece = process_slice(subtrees, count, process_threads, slice_seconds)
            process_rates.append(piece.rate)
            say(f"      proc x{count}: {piece.rate:,.0f} 항목/초")

    # P: 처음 x1 과 마지막 x1 의 차이. 캐시가 데워지면서 뒤 측정이 유리해지는
    # 흐름이 얼마나 되는지 - 위 확장 결과를 얼마나 믿을지가 여기서 갈린다.
    say("[4/5] 캐시 영향 재확인 (x1 다시)")
    again = timed_walk(str(target), sweep[0], slice_seconds)
    drift = abs(again.rate - base) / base * 100 if base else None
    say(f"      x{sweep[0]} 재측정: {again.rate:,.0f} 항목/초")
    checks.append(Check(
        "P", "캐시 영향",
        bucket(drift, [10, 30]),
        f"첫 측정 대비 {drift:.0f}% 차이" if drift is not None else "재지 못함",
        f"{drift:.0f}%" if drift is not None else None,
    ))

    # N/O: 절대 속도. 용량 산정("이 계정 500만 개면 몇 분")에 그대로 쓴다.
    single_rate = rates[0] if rates else None
    latency_ms = (1000.0 / single_rate) if single_rate else None
    checks.append(Check(
        "N", "단일 스레드 지연",
        bucket(latency_ms, [0.02, 0.05, 0.2]),
        f"{latency_ms:.3f} ms/항목" if latency_ms else "재지 못함",
        f"{latency_ms:.3f}ms" if latency_ms else None,
    ))
    best_rate = max(rates) if rates else None
    checks.append(Check(
        "O", "최대 처리량",
        0 if best_rate is None else (5 - bucket(best_rate, [2000, 10000, 50000])),
        f"{best_rate:,.0f} 항목/초" if best_rate else "재지 못함",
        f"{best_rate:,.0f}/s" if best_rate else None,
    ))

    # --- 4. 동시성 (이 진단의 핵심) ---------------------------------------
    say(f"[5/5] 동시성 비교 (조각당 {slice_seconds:.0f}초, 짝마다 5조각)")

    # K: 같은 볼륨 두 갈래. 이미 실측으로 "안 늘어난다"를 봤지만, 이 장비에서
    # 다시 확인해 두어야 아래 L·M 을 비교할 기준이 생긴다.
    pair = largest_subdirs(full.entries, str(target), 2)
    if len(pair) == 2:
        say("  K 같은 볼륨 두 갈래")
        # K·L·M 은 **같은 방식으로** 재야 나란히 놓고 읽을 수 있다 - 셋 다
        # "각자 `workers` 스레드로 혼자 vs 동시"다. 그래야 0.5/0.8/1.0 같은
        # 값들이 서로 비교되는 숫자가 된다.
        efficiency = pair_efficiency(pair[0], pair[1], workers, slice_seconds, say)
        checks.append(Check(
            "K", "같은 볼륨 동시",
            bucket(efficiency, [0.6, 0.85]),
            f"각자 속도의 {efficiency:.2f}배 유지" if efficiency else "재지 못함",
            f"{efficiency:.2f}" if efficiency else None,
        ))
    else:
        checks.append(Check("K", "같은 볼륨 동시", 0, "하위 디렉터리가 2개 미만"))

    # 자동 탐색은 **비어 있는 쪽만** 채운다. 하나만 직접 준 경우에도 나머지
    # 하나를 찾아 주어야 한다 (예전에는 둘 다 비었을 때만 찾아, `--peer` 만
    # 주면 M 이 통째로 빠졌다).
    if volume_key is not None and (peer_same_filer is None or peer_other_filer is None):
        found_same, found_other = find_peers(target, accounts, volume_key)
        peer_same_filer = peer_same_filer or found_same
        peer_other_filer = peer_other_filer or found_other

    # L: 같은 파일러의 다른 볼륨. **아직 답이 없는 질문이 이것이다** - 볼륨
    # 한도는 각자 안 넘는데 파일러 한 대의 NIC/CPU 를 나눠 쓰기 때문이다.
    if peer_same_filer:
        say(f"  L 같은 파일러 다른 볼륨 ({peer_same_filer})")
        efficiency = pair_efficiency(str(target), peer_same_filer, workers, slice_seconds, say)
        checks.append(Check(
            "L", "같은 파일러 다른 볼륨 동시",
            bucket(efficiency, [0.6, 0.85]),
            f"각자 속도의 {efficiency:.2f}배 유지" if efficiency else "재지 못함",
            f"{efficiency:.2f}" if efficiency else None,
        ))
    else:
        checks.append(Check("L", "같은 파일러 다른 볼륨 동시", 0, "짝이 될 계정이 없음"))

    # M: 다른 파일러. 여기서도 안 늘면 병목은 저장소가 아니라 이 서버 쪽이다
    # (NIC, CPU, 커널 RPC 계층).
    if peer_other_filer:
        say(f"  M 다른 파일러 ({peer_other_filer})")
        efficiency = pair_efficiency(str(target), peer_other_filer, workers, slice_seconds, say)
        checks.append(Check(
            "M", "다른 파일러 동시",
            bucket(efficiency, [0.6, 0.85]),
            f"각자 속도의 {efficiency:.2f}배 유지" if efficiency else "재지 못함",
            f"{efficiency:.2f}" if efficiency else None,
        ))
    else:
        checks.append(Check("M", "다른 파일러 동시", 0, "짝이 될 계정이 없음"))

    # --- 5. 트리 모양 ------------------------------------------------------
    files = full.file_count
    dirs = full.dir_count
    checks.append(Check(
        "Q", "파일 수 규모",
        bucket(files, [100_000, 1_000_000, 10_000_000]),
        f"{files:,}개",
        f"{files:,}",
    ))
    fanout = (files / dirs) if dirs else None
    checks.append(Check(
        "R", "디렉터리당 평균 파일",
        bucket(fanout, [10, 100, 1000]),
        f"{fanout:.1f}개" if fanout else "재지 못함",
        f"{fanout:.0f}" if fanout else None,
    ))
    checks.append(Check(
        "S", "최대 깊이",
        bucket(full.max_depth_seen, [6, 11, 21]),
        f"{full.max_depth_seen}단",
        str(full.max_depth_seen),
    ))
    hardlink_ratio = (full.hardlink_files / files * 100) if files else None
    checks.append(Check(
        "T", "하드링크 비중",
        1 if not full.hardlink_files else bucket(hardlink_ratio, [1, 10]) + 1,
        f"{hardlink_ratio:.2f}%" if hardlink_ratio is not None else "재지 못함",
        f"{hardlink_ratio:.2f}%" if hardlink_ratio else None,
    ))
    unread_ratio = (full.unreadable / (files + dirs) * 100) if (files + dirs) else None
    checks.append(Check(
        "U", "읽기 실패 비중",
        1 if not full.unreadable else bucket(unread_ratio, [0.1, 5]) + 1,
        f"{full.unreadable:,}개 ({unread_ratio:.2f}%)" if unread_ratio is not None else "재지 못함",
        f"{full.unreadable:,}" if full.unreadable else None,
    ))
    occupied = (full.root_size_kb or 0) * 1024
    logical = full.logical_bytes
    if logical and occupied:
        ratio = occupied / logical
        checks.append(Check(
            "V", "논리 크기 대 점유",
            1 if 0.95 <= ratio <= 1.05 else (2 if ratio > 1.05 else 3),
            f"점유/논리 = {ratio:.2f}",
            f"{ratio:.2f}",
        ))
    else:
        checks.append(Check("V", "논리 크기 대 점유", 0, "재지 못함"))

    checks.extend(system_checks(_peak_rss_kb()))

    result.checks = sorted(checks, key=lambda check: check.letter)
    result.cpu = cpu_checks(
        cpu_topology(),
        cpu_single,
        cpu_parallel,
        rates,
        sweep,
        process_rates,
        process_counts,
    )
    return result


# ---------------------------------------------------------------- 출력


def code_string(checks: Sequence[Check]) -> str:
    """`A1B2C1D2 E3F1G1H3 ...` - 손으로 옮겨 적기 좋게 넷씩 끊는다."""

    tokens = [f"{check.letter}{check.digit}" for check in checks]
    groups = [
        "".join(tokens[index:index + 4]) for index in range(0, len(tokens), 4)
    ]
    return " ".join(groups)


def checksum(codes: str) -> str:
    """옮겨 적다 틀린 것을 잡는 두 글자.

    한 글자만 바뀌어도 값이 달라진다. 자리를 바꿔 적은 경우까지 잡으려고
    자리마다 다른 가중치를 준다."""

    total = 0
    for index, char in enumerate(codes.replace(" ", "")):
        total = (total + (index + 1) * ord(char)) % 251
    return f"{total:02x}"


def value_line(checks: Sequence[Check], prefix: str = "") -> str:
    """코드로 못 담는 원값. 있는 것만 짧게."""

    return " ".join(
        f"{prefix}{check.letter}={check.raw}" for check in checks if check.raw
    )


def format_transfer(result: ProbeResult) -> str:
    """옮겨 적을 줄들. 표가 둘이면 줄도 둘이다.

    `CODE` 와 `CPU` 는 서로 다른 표다 - 같은 글자라도 뜻이 다르므로 **줄
    이름을 반드시 함께** 적어야 한다.
    """

    codes = code_string(result.checks)
    lines = [
        f"== SMVWP PROBE v{CODE_VERSION} ==",
        f"CODE {codes} #{checksum(codes)}",
    ]
    if result.cpu:
        cpu_codes = code_string(result.cpu)
        lines.append(f"CPU  {cpu_codes} #{checksum(cpu_codes)}")

    values = " ".join(
        piece for piece in (
            value_line(result.checks),
            value_line(result.cpu, prefix="c"),
        ) if piece
    )
    if values:
        lines.append(f"VAL  {values}")
    return "\n".join(lines)


def format_detail(result: ProbeResult) -> str:
    """옮겨 적을 필요 없는, 옆에서 확인하는 용도의 표."""

    from .reports import pad

    lines = ["", "상세 (옮겨 적지 않아도 됩니다)", "-" * 60]
    for group, checks in (("CODE", result.checks), ("CPU", result.cpu)):
        if not checks:
            continue
        lines.append(f"[{group}]")
        for check in checks:
            mark = f"{check.letter}{check.digit}"
            # 한글은 두 칸을 차지한다. `%-22s` 로는 줄이 어긋나 읽기 어려워진다.
            lines.append(f"  {mark:<4} {pad(check.title, 24)} {check.note}")
    if result.warnings:
        lines.append("")
        for warning in result.warnings:
            lines.append(f"  ! {warning}")
    return "\n".join(lines)
