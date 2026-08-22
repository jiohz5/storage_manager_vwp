"""이 작업이 시스템 CPU의 몇 %를 쓰고 있는지 측정한다.

## 왜 `top`을 파싱하지 않는가

`top`은 사람이 읽으라고 만든 출력이라 로케일·버전·터미널 폭에 따라 열이
달라진다. cron(LANG=C)과 로그인 셸에서 다르게 나오는 것을 파싱으로 맞추는 것은
깨지기 쉽다. `top`이 읽는 원본인 `/proc`을 직접 보면 같은 값을 훨씬 안정적으로
얻는다 - 외부 명령도 하나 덜 띄운다.

## 무엇을 재는가

- `/proc/self/stat`의 `utime + stime + cutime + cstime`
  `cutime/cstime`는 **거둬들인 자식 프로세스**의 CPU 시간이다. 상세 스캔은
  `du`/`find`를 `subprocess.run`으로 끝까지 기다렸다가 거두므로, 이 값에 자식이
  쓴 CPU가 그대로 쌓인다. 즉 스캔 프로세스 하나만 봐도 실제 작업량이 잡힌다.
- `/proc/stat` 첫 줄의 합계 = 같은 기간 시스템 전체가 쓴 CPU 시간.

둘의 증분 비율이 곧 "전체 중 이 작업의 몫"이다.

## 두 가지 퍼센트를 함께 주는 이유

- `system_percent`: 머신 전체 용량(코어 전부) 대비 몫. "이 서버의 몇 %를
  먹고 있나"에 답한다.
- `top_percent`: `top`/`ps`와 같은 기준(코어 1개 포화 = 100%). 사용자가 `top`을
  같이 띄워 놓고 대조할 때 숫자가 맞아야 하므로 이쪽도 같이 낸다.

8코어에서 한 코어를 다 쓰면 system 12.5% / top 100%다. 같은 상태를 두 가지로
말하는 것뿐이니 둘 다 보여주고 어느 기준인지 라벨에 적는다.

## 한계 (반드시 같이 말해야 하는 것)

여기서 재는 것은 **이 클라이언트에서 본 CPU**뿐이다. 상세 스캔의 진짜 부담은
대개 CPU가 아니라 파일서버 쪽 I/O인데, 그것은 이 프로세스에서 관측할 수 없다
(DESIGN.md 1부 2절 "부하에 대한 겸손함"). 그래서 이 숫자를 "스토리지에 준 부하"
라고 부르지 않는다.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

PROC_STAT = Path("/proc/stat")
PROC_SELF_STAT = Path("/proc/self/stat")
PROC_SELF_STATUS = Path("/proc/self/status")
PROC_MEMINFO = Path("/proc/meminfo")


def available() -> bool:
    """`/proc` 기반 측정이 가능한 환경인지 (리눅스)."""

    return PROC_STAT.exists() and PROC_SELF_STAT.exists()


def _system_jiffies() -> Optional[int]:
    try:
        first = PROC_STAT.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    parts = first.split()
    if not parts or parts[0] != "cpu":
        return None
    try:
        return sum(int(value) for value in parts[1:])
    except ValueError:
        return None


def _self_jiffies() -> Optional[int]:
    """자기 자신 + 거둬들인 자식들의 CPU 시간 (jiffies)."""

    try:
        raw = PROC_SELF_STAT.read_text(encoding="utf-8")
    except OSError:
        return None
    # comm 필드에 공백/괄호가 들어갈 수 있어 마지막 ')' 뒤부터 자른다.
    close = raw.rfind(")")
    if close == -1:
        return None
    fields = raw[close + 2:].split()
    # stat(5) 기준 utime=14, stime=15, cutime=16, cstime=17 (1-based).
    # comm 뒤부터 자르면 state가 첫 항목(3번)이므로 인덱스는 -3 만큼 밀린다.
    try:
        return sum(int(fields[index]) for index in (11, 12, 13, 14))
    except (IndexError, ValueError):
        return None


@dataclass
class Usage:
    """한 구간 동안의 CPU 점유. 측정 불가면 percent가 None."""

    system_percent: Optional[float]  # 머신 전체 용량 대비
    top_percent: Optional[float]     # top/ps 기준 (코어 1개 = 100%)
    seconds: float
    load_avg_1m: Optional[float]

    @property
    def measured(self) -> bool:
        return self.system_percent is not None


class Sampler:
    """구간 CPU 점유를 재는 샘플러.

    `take()`를 부를 때마다 **직전 호출 이후 구간**의 점유율을 돌려준다.
    구간이 너무 짧으면(jiffies가 안 움직임) None을 돌려준다 - 0으로 채우면
    "부하가 없었다"로 잘못 읽히기 때문이다.
    """

    def __init__(self):
        self._cpu_count = os.cpu_count() or 1
        self._prev_self: Optional[int] = None
        self._prev_total: Optional[int] = None
        self._prev_time: Optional[float] = None
        self.reset()

    def reset(self) -> None:
        self._prev_self = _self_jiffies()
        self._prev_total = _system_jiffies()
        self._prev_time = time.monotonic()

    def take(self) -> Usage:
        now_self = _self_jiffies()
        now_total = _system_jiffies()
        now_time = time.monotonic()
        seconds = max(0.0, now_time - (self._prev_time or now_time))

        if (
            now_self is None
            or now_total is None
            or self._prev_self is None
            or self._prev_total is None
        ):
            return Usage(None, None, seconds, _load_avg())

        delta_self = now_self - self._prev_self
        delta_total = now_total - self._prev_total
        self._prev_self, self._prev_total, self._prev_time = now_self, now_total, now_time

        if delta_total <= 0:
            return Usage(None, None, seconds, _load_avg())

        share = delta_self / delta_total
        return Usage(
            system_percent=share * 100.0,
            top_percent=share * self._cpu_count * 100.0,
            seconds=seconds,
            load_avg_1m=_load_avg(),
        )


def _self_rss_kb() -> Optional[int]:
    """지금 이 프로세스가 실제로 물고 있는 메모리 (VmRSS, KB)."""

    try:
        for line in PROC_SELF_STATUS.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return None


def _peak_rss_kb() -> Optional[int]:
    """자기 자신과 **자식들**의 최대 RSS 중 큰 값 (KB).

    `du`/`find`는 자식으로 돌다가 끝나면 사라져서, 그 순간의 RSS를 나중에
    들여다볼 방법이 없다. `getrusage(RUSAGE_CHILDREN).ru_maxrss`는 거둬들인
    자식들의 **최고치**를 커널이 기억해 주므로, 사후에도 "가장 많이 썼을 때
    얼마였나"를 알 수 있다. 스캔이 메모리를 위협했는지는 평균이 아니라 최고치가
    답하는 질문이다.
    """

    try:
        import resource
    except ImportError:  # Windows
        return None
    try:
        me = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        kids = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    except (OSError, ValueError):  # pragma: no cover - 방어적 처리
        return None
    # 리눅스의 ru_maxrss 단위는 KB다 (macOS는 byte지만 반입 대상은 리눅스).
    return max(me, kids)


def _system_memory_kb() -> "tuple":
    """(전체, 사용가능) KB. 못 읽으면 (None, None).

    `MemFree`가 아니라 `MemAvailable`을 쓴다. 리눅스는 남는 메모리를 캐시로
    채워 두므로 MemFree는 거의 항상 작게 나오고, 그것을 부족으로 읽으면 매번
    거짓 경보가 된다."""

    total = available = None
    try:
        for line in PROC_MEMINFO.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                available = int(line.split()[1])
            if total is not None and available is not None:
                break
    except (OSError, IndexError, ValueError):
        return None, None
    return total, available


def _load_avg() -> Optional[float]:
    try:
        return os.getloadavg()[0]
    except (OSError, AttributeError):  # Windows 등
        return None


@dataclass
class Summary:
    """실행 한 번 전체의 점유 요약."""

    samples: int = 0
    system_percent_avg: Optional[float] = None
    system_percent_peak: Optional[float] = None
    top_percent_avg: Optional[float] = None
    top_percent_peak: Optional[float] = None
    load_avg_1m: Optional[float] = None
    cpu_count: int = 1
    # 메모리는 평균이 아니라 **최고치**를 남긴다. "스캔이 메모리를 위협했나"는
    # 순간 최대가 답하는 질문이고, du/find는 끝나면 사라져 사후 관측이 안 된다.
    rss_peak_kb: Optional[int] = None
    memory_total_kb: Optional[int] = None
    memory_peak_percent: Optional[float] = None


class Accumulator:
    """스캔이 도는 동안 표본을 모아 요약을 만든다.

    체크포인트 하나가 끝날 때마다 부르는 것을 전제로 한다 - 작은 파일 두 개를
    읽는 것이 전부라, 재는 행위 자체가 부하가 되지는 않는다.
    """

    def __init__(self):
        self._sampler = Sampler() if available() else None
        self._system: list = []
        self._top: list = []
        self._last_load: Optional[float] = None
        self._rss_peak: Optional[int] = None
        self.cpu_count = os.cpu_count() or 1
        # 계정 병렬 실행에서는 여러 작업 스레드가 같은 누적기를 부른다.
        # `Sampler.take()`는 직전 호출 시점을 들고 있는 상태 기계라 보호 없이
        # 겹치면 구간이 뒤엉켜 값이 못 쓰게 된다. 잠금 안에서 하는 일은 작은
        # 파일 두 개를 읽는 것뿐이라 경합 비용은 무시할 수 있다.
        self._lock = threading.Lock()

    def sample(self) -> Optional[float]:
        """한 표본을 모으고, 이 구간의 스캔 몫(top 기준 %)을 돌려준다.

        반환값은 배경 기록기(`Recorder`)가 같은 표본을 시계열에도 남기기
        위한 것이다. 잴 수 없으면 None."""

        if self._sampler is None:
            return None
        with self._lock:
            usage = self._sampler.take()
            self._last_load = usage.load_avg_1m

            # 현재 RSS와 (거둬들인 자식 포함) 최고 RSS 중 큰 값을 계속 갱신한다.
            for value in (_self_rss_kb(), _peak_rss_kb()):
                if value is not None and (self._rss_peak is None or value > self._rss_peak):
                    self._rss_peak = value
            if usage.system_percent is None:
                return None
            self._system.append(usage.system_percent)
            self._top.append(usage.top_percent)
            return usage.top_percent

    def summary(self) -> Summary:
        # 표본을 잠금 안에서 복사해 두고 계산은 밖에서 한다 - 계산 중에
        # 작업 스레드가 표본을 더 넣어도 목록이 바뀌지 않게.
        with self._lock:
            system = list(self._system)
            top = list(self._top)
            last_load = self._last_load
            rss_peak = self._rss_peak

        total_kb, _available = _system_memory_kb()
        peak_percent = None
        if rss_peak is not None and total_kb:
            peak_percent = rss_peak / total_kb * 100.0

        if not system:
            return Summary(
                samples=0,
                load_avg_1m=last_load,
                cpu_count=self.cpu_count,
                rss_peak_kb=rss_peak,
                memory_total_kb=total_kb,
                memory_peak_percent=peak_percent,
            )
        return Summary(
            samples=len(system),
            system_percent_avg=sum(system) / len(system),
            system_percent_peak=max(system),
            top_percent_avg=sum(top) / len(top),
            top_percent_peak=max(top),
            load_avg_1m=last_load,
            cpu_count=self.cpu_count,
            rss_peak_kb=rss_peak,
            memory_total_kb=total_kb,
            memory_peak_percent=peak_percent,
        )


# ===========================================================================
# 시스템 전체 관점 - "스캔이 이 서버를 얼마나 흔들었나"
# ===========================================================================
#
# 위쪽(Sampler/Accumulator)이 답하는 질문은 "이 작업이 쓴 몫"이다. 그것만으로는
# 야간 스캔을 켜도 되는지 판단할 수 없다. 판단에 필요한 것은 **스캔이 없을 때와
# 있을 때 서버가 어떻게 달라지는가**이므로, 프로세스 몫과 별개로 시스템 전체
# 수치를 같은 시각에 함께 남긴다.
#
# iowait을 busy와 나눠 재는 것이 여기서 특히 중요하다. du/find는 CPU를 거의
# 안 쓰고 I/O 대기를 만든다 - CPU 사용률만 보면 "부하가 거의 없다"는 결론이
# 나오는데, 정작 그 시간에 다른 사람의 job은 디스크를 기다리며 느려진다.
# 두 값을 나란히 놓아야 그 상황이 보인다.


def _cpu_fields():
    """/proc/stat 첫 줄에서 (전체, idle, iowait) jiffies. 못 읽으면 전부 None."""

    try:
        first = PROC_STAT.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None, None, None
    parts = first.split()
    if not parts or parts[0] != "cpu":
        return None, None, None
    try:
        values = [int(value) for value in parts[1:]]
    except ValueError:
        return None, None, None
    if len(values) < 5:
        return None, None, None
    # user nice system idle iowait irq softirq steal ...
    return sum(values), values[3], values[4]


@dataclass
class SystemCpu:
    """한 구간 동안의 시스템 전체 CPU. 못 재면 전부 None."""

    busy_percent: Optional[float] = None     # idle/iowait을 뺀 실제 연산
    iowait_percent: Optional[float] = None   # 디스크를 기다린 시간


class SystemSampler:
    """take() 사이 구간의 시스템 전체 CPU 사용률."""

    def __init__(self):
        self._prev = _cpu_fields()

    def take(self) -> SystemCpu:
        current = _cpu_fields()
        previous, self._prev = self._prev, current
        total, idle, iowait = current
        prev_total, prev_idle, prev_iowait = previous
        if total is None or prev_total is None:
            return SystemCpu()
        delta_total = total - prev_total
        if delta_total <= 0:
            # 구간이 너무 짧아 jiffies가 안 움직였다. 0으로 채우면 "한가했다"로
            # 잘못 읽히므로 모른다고 말한다.
            return SystemCpu()
        delta_idle = idle - prev_idle
        delta_iowait = iowait - prev_iowait
        busy = (delta_total - delta_idle - delta_iowait) / delta_total * 100.0
        return SystemCpu(
            busy_percent=max(0.0, busy),
            iowait_percent=max(0.0, delta_iowait / delta_total * 100.0),
        )


PHASE_BEFORE = "before"
PHASE_DURING = "during"
PHASE_WARMUP = "warmup"


@dataclass
class Snapshot:
    """한 시점의 리소스 상태. 보고서 시계열의 한 줄이 된다."""

    sampled_at: str
    elapsed_seconds: float
    phase: str = PHASE_DURING
    cpu_busy_percent: Optional[float] = None
    cpu_iowait_percent: Optional[float] = None
    cpu_scan_top_percent: Optional[float] = None
    load_avg_1m: Optional[float] = None
    memory_total_kb: Optional[int] = None
    memory_available_kb: Optional[int] = None
    memory_used_percent: Optional[float] = None
    active_accounts: int = 0

    @property
    def measured(self) -> bool:
        return self.cpu_busy_percent is not None or self.load_avg_1m is not None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Recorder:
    """스캔이 도는 동안 일정 주기로 리소스를 찍어 두는 배경 스레드.

    ## 왜 체크포인트마다가 아니라 주기인가

    체크포인트 하나는 최대 15분(detail_task_timeout_seconds)까지 간다.
    "체크포인트가 끝날 때마다" 재면 그 15분 안에서 부하가 어떻게 움직였는지는
    통째로 안 보인다. 부하가 튀는 것은 대개 구간 중간이다.

    ## 왜 DB에 바로 쓰지 않고 메모리에 모으는가

    배경 스레드에서 SQLite에 쓰려면 연결을 스레드별로 따로 만들어야 하고, 계정
    병렬 실행 중에는 작업 스레드들과 쓰기 잠금을 다투게 된다. **측정 장치가
    측정 대상에 부하를 주는 것**은 피해야 한다. 표본 한 줄은 수십 바이트라
    하룻밤(30초 주기 8시간)을 모아도 1,000개 남짓이므로, 메모리에 들고 있다가
    끝에 한 번에 쓰는 편이 안전하다.

    그 대가로 **프로세스가 강제 종료되면 그날 시계열은 사라진다.** 실행 요약
    행(scan_runs)도 같은 경우 남지 않으므로 새로 생긴 손실은 아니다.
    """

    def __init__(
        self,
        interval_seconds: float = 30.0,
        accumulator: Optional[Accumulator] = None,
        active_accounts: Optional[Callable[[], int]] = None,
    ):
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._accumulator = accumulator
        self._active_accounts = active_accounts or (lambda: 0)
        self._system = SystemSampler() if available() else None
        self._samples: List[Snapshot] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started_at = time.monotonic()

    # -- 표본 만들기 -------------------------------------------------
    def _take(self, phase: str) -> Snapshot:
        cpu = self._system.take() if self._system is not None else SystemCpu()
        total_kb, available_kb = _system_memory_kb()
        used_percent = None
        if total_kb and available_kb is not None:
            used_percent = (total_kb - available_kb) / total_kb * 100.0
        scan_share = self._accumulator.sample() if self._accumulator is not None else None
        return Snapshot(
            sampled_at=_utc_now_iso(),
            elapsed_seconds=max(0.0, time.monotonic() - self._started_at),
            phase=phase,
            cpu_busy_percent=cpu.busy_percent,
            cpu_iowait_percent=cpu.iowait_percent,
            cpu_scan_top_percent=scan_share,
            load_avg_1m=_load_avg(),
            memory_total_kb=total_kb,
            memory_available_kb=available_kb,
            memory_used_percent=used_percent,
            active_accounts=self._active_accounts(),
        )

    def baseline(self, warmup_seconds: float = 1.0) -> Snapshot:
        """스캔을 **시작하기 전**의 기준값.

        CPU 사용률은 두 시점의 차이로만 구할 수 있으므로 짧게 두 번 읽는다.
        이 1초가 없으면 첫 표본은 항상 '측정 불가'로 남고, 그러면 "스캔 때문에
        얼마나 튀었나"를 답할 기준선이 아예 없어진다.
        """

        if self._system is None:
            # /proc이 없는 환경(개발 PC 등)에서는 기다려도 얻을 것이 없다.
            # 잴 수 없는 값을 위해 스캔 시작을 늦추지는 않는다.
            warmup_seconds = 0.0
        self._take(PHASE_WARMUP)  # 델타 기준점만 잡고 버린다
        if warmup_seconds > 0:
            time.sleep(warmup_seconds)
        snapshot = self._take(PHASE_BEFORE)
        with self._lock:
            self._samples.append(snapshot)
        return snapshot

    # -- 스레드 -------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._started_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._loop, name="smvwp-loadstat", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        # Event.wait는 중지 요청이 오면 남은 대기를 건너뛴다. sleep으로 짜면
        # 스캔이 끝나도 마지막 주기만큼 프로세스가 더 붙잡혀 있다.
        while not self._stop.wait(self.interval_seconds):
            try:
                snapshot = self._take(PHASE_DURING)
            except Exception:  # pragma: no cover - 측정 실패가 스캔을 막으면 안 된다
                continue
            with self._lock:
                self._samples.append(snapshot)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)

    def samples(self) -> List[Snapshot]:
        with self._lock:
            return list(self._samples)


@dataclass
class Change:
    """스캔 전 대비 스캔 중 변화. 보고서의 "얼마나 튀었나" 한 줄."""

    metric: str
    before: Optional[float] = None
    average: Optional[float] = None
    peak: Optional[float] = None

    @property
    def delta(self) -> Optional[float]:
        if self.before is None or self.peak is None:
            return None
        return self.peak - self.before


# 보고서에 낼 지표와 순서. CPU busy를 맨 위에 두지 않은 것은 의도다 - du/find의
# 실제 영향은 iowait과 load에 먼저 나타나고, busy만 보면 "부하 없음"으로 잘못
# 읽힌다. 사람이 위에서부터 읽는다는 것을 감안한 순서다.
CHANGE_METRICS = (
    ("load_avg_1m", "load_avg"),
    ("cpu_iowait_percent", "cpu_iowait"),
    ("cpu_busy_percent", "cpu_busy"),
    ("memory_used_percent", "memory_used"),
    ("cpu_scan_top_percent", "scan_share"),
)


def _values(samples, attribute: str, phase: Optional[str] = None) -> List[float]:
    result = []
    for sample in samples:
        if phase is not None and sample.phase != phase:
            continue
        value = getattr(sample, attribute, None)
        if value is not None:
            result.append(float(value))
    return result


def changes(samples) -> List[Change]:
    """before 표본과 during 표본들을 대조해 지표별 변화를 만든다.

    기준값이 없으면(리눅스가 아니거나 baseline을 못 잡은 경우) before만 None으로
    남기고 나머지는 그대로 준다 - 스캔 중 절대값만으로도 읽을 것이 있으므로
    전부 버리지는 않는다.
    """

    result = []
    for attribute, name in CHANGE_METRICS:
        before_values = _values(samples, attribute, phase=PHASE_BEFORE)
        during_values = _values(samples, attribute, phase=PHASE_DURING)
        if not before_values and not during_values:
            continue
        result.append(
            Change(
                metric=name,
                before=before_values[0] if before_values else None,
                average=(sum(during_values) / len(during_values)) if during_values else None,
                peak=max(during_values) if during_values else None,
            )
        )
    return result
