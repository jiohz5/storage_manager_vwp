"""서버 한 시점의 상태를 한 벌로 묶는다 (Qt 없음).

## 이것이 답하려는 질문

- 지금 이 서버가 얼마나 바쁜가 - **우리 것과 남의 것을 갈라서.**
- 그 시각에 어떤 작업이 돌고 있었나.
- NFS 쪽으로는 얼마나 나갔고, 느렸다면 파일서버 탓인가 우리 슬롯 탓인가.

이 세 가지를 **같은 시각에 한 벌로** 남기는 것이 핵심이다. 따로 남기면
나중에 시각을 맞춰 보는 일이 추측이 된다.

## 두 가지 주기로 쓴다

- **상시(15분)** - 수집기가 어차피 도는 김에 한 벌씩. 스캔이 없는 낮에도
  계속 쌓이므로 "평소 이 서버는 어떤 모습인가"의 바탕이 된다. 낮에 상세
  스캔을 돌려도 되는지 같은 판단은 이 바탕이 없으면 감으로 하는 수밖에 없다.
- **스캔 중(30초)** - 촘촘히. 밤새 어디서 튀었는지는 시계열이 아니면 못 본다.

## 재는 값이 부하가 되지 않게

한 표본에 드는 일은 작은 파일 몇 개와 프로세스 수백 개의 `/proc` 항목을 읽는
것이다. `/proc` 은 디스크가 아니라 커널이 만들어 주는 것이라 I/O 가 없다.
사용자 이름과 명령줄은 **상위 몇 개에만** 채운다 - 수백 개를 다 읽으면 그때
부터는 재는 행위 자체가 부하가 된다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from . import nfsstat, procstat

# 상시 표본을 뜰 때 기준점과 실제 표본 사이의 최소 간격.
#
# CPU 는 두 시점의 차이로만 구할 수 있어서 간격이 0 이면 아무것도 못 잰다.
# `top` 도 첫 화면은 버리고 두 번째부터 보여 준다 - 같은 이유다.
MIN_INTERVAL_SECONDS = 2.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ServerSample:
    """한 시점의 서버 상태 한 벌."""

    sampled_at: str
    interval_seconds: float

    cpu_count: int = 0
    cpu_busy_percent: Optional[float] = None
    cpu_iowait_percent: Optional[float] = None
    cpu_steal_percent: Optional[float] = None

    load_avg_1m: Optional[float] = None
    load_avg_5m: Optional[float] = None
    load_avg_15m: Optional[float] = None

    # 지금 막혀 있는 프로세스 수. `du`/`find` 의 영향은 CPU 보다 여기에 먼저
    # 나타난다 - 다른 사람의 작업이 디스크를 기다리며 느려지는 상태다.
    procs_running: Optional[int] = None
    procs_blocked: Optional[int] = None
    blocked_ours: int = 0
    blocked_others: int = 0

    memory_total_kb: Optional[int] = None
    memory_available_kb: Optional[int] = None
    memory_used_percent: Optional[float] = None
    swap_used_kb: Optional[int] = None

    # 우리 몫. `top` 기준(코어 하나 = 100%)이라 사용자가 top 을 같이 띄워
    # 놓고 대조할 수 있다.
    scan_cpu_percent: Optional[float] = None
    # 우리 프로세스들의 RSS 합. 공유 페이지를 여러 번 세므로 **상한**이다.
    scan_rss_kb: Optional[int] = None
    scan_process_count: int = 0

    processes: List[procstat.ProcessLoad] = field(default_factory=list)
    mounts: List[nfsstat.MountDelta] = field(default_factory=list)

    @property
    def measured(self) -> bool:
        return self.cpu_busy_percent is not None or self.load_avg_1m is not None

    @property
    def others_cpu_percent(self) -> Optional[float]:
        """우리 것을 뺀 나머지가 쓴 CPU (전체 용량 대비 %).

        "스캔 때문에 바빴나, 원래 바빴나"에 바로 답하는 값이다."""

        if self.cpu_busy_percent is None:
            return None
        if self.scan_cpu_percent is None or not self.cpu_count:
            return self.cpu_busy_percent
        ours = self.scan_cpu_percent / self.cpu_count
        return max(0.0, self.cpu_busy_percent - ours)

    @property
    def nfs_ops_per_second(self) -> float:
        return sum(mount.ops_per_second for mount in self.mounts)

    @property
    def nfs_read_bytes_per_second(self) -> float:
        return sum(mount.read_bytes_per_second for mount in self.mounts)


class ServerMonitor:
    """기준점을 들고 있다가 `sample()` 때 그 사이의 차이를 낸다.

    한 번 읽어서 나오는 값은 대부분 **부팅 이후 누적**이라 그 자체로는
    "지금 얼마나"가 아니다. 그래서 반드시 두 시점이 필요하다.
    """

    def __init__(
        self,
        with_processes: bool = True,
        with_cmdline: bool = True,
        with_nfs: bool = True,
        top_processes: int = procstat.TOP_PROCESSES_KEPT,
    ):
        self.with_processes = with_processes
        self.with_cmdline = with_cmdline
        self.with_nfs = with_nfs
        self.top_processes = top_processes
        self._cpu_before = procstat.SystemCounters()
        self._procs_before: Dict[int, procstat.ProcInfo] = {}
        self._mounts_before: Dict[str, nfsstat.MountCounters] = {}
        self._primed_at: Optional[float] = None
        self._our_pids: List[int] = []

    def available(self) -> bool:
        return procstat.available()

    def prime(self) -> None:
        """기준점을 잡는다. 이 시점부터 다음 `sample()` 까지가 한 구간이다."""

        self._cpu_before = procstat.read_system_counters()
        if self.with_processes:
            self._procs_before = procstat.snapshot()
        if self.with_nfs and nfsstat.available():
            self._mounts_before = nfsstat.read_mountstats()
        self._our_pids = procstat.our_pids()
        self._primed_at = time.monotonic()

    def sample(self, run_id: Optional[str] = None) -> ServerSample:
        """기준점 이후 구간의 표본 한 벌. 다음 구간의 기준점도 여기서 잡는다."""

        if self._primed_at is None:
            self.prime()
        now = time.monotonic()
        # `self._primed_at or now` 로 쓰면 안 된다 - 기준 시각이 정확히 0.0 일 때
        # 거짓으로 취급돼 구간이 0 이 되고, 그러면 그 표본이 통째로 비어 버린다.
        # `time.monotonic()` 의 기준점은 플랫폼마다 달라서 0.0 이 실제로 나온다.
        seconds = max(0.0, now - (now if self._primed_at is None else self._primed_at))

        cpu_after = procstat.read_system_counters()
        usage = procstat.cpu_usage(self._cpu_before, cpu_after)
        memory = procstat.read_memory()
        load = procstat.read_loadavg()

        sample = ServerSample(
            sampled_at=_utc_now_iso(),
            interval_seconds=seconds,
            cpu_count=cpu_after.cpu_count,
            cpu_busy_percent=usage.busy_percent,
            cpu_iowait_percent=usage.iowait_percent,
            cpu_steal_percent=usage.steal_percent,
            load_avg_1m=load.one,
            load_avg_5m=load.five,
            load_avg_15m=load.fifteen,
            procs_running=cpu_after.procs_running,
            procs_blocked=cpu_after.procs_blocked,
            memory_total_kb=memory.total_kb,
            memory_available_kb=memory.available_kb,
            memory_used_percent=memory.used_percent,
            swap_used_kb=memory.swap_used_kb,
        )

        if self.with_processes:
            self._fill_processes(sample, seconds)
        if self.with_nfs:
            self._fill_mounts(sample, seconds)

        # 다음 구간의 기준점.
        self._cpu_before = cpu_after
        self._primed_at = now
        return sample

    # -- 프로세스 -------------------------------------------------------
    def _fill_processes(self, sample: ServerSample, seconds: float) -> None:
        after = procstat.snapshot()
        our_pids = procstat.our_pids()
        loads = procstat.diff(self._procs_before, after, seconds, our_pids=our_pids)
        self._procs_before = after
        self._our_pids = our_pids
        if not loads:
            return

        ours = [item for item in loads if item.is_ours]
        sample.scan_process_count = len(ours)
        sample.scan_cpu_percent = sum(item.cpu_percent for item in ours)
        sample.scan_rss_kb = sum(item.rss_kb for item in ours)
        # D 상태 = 디스크/네트워크 대기. 우리 것과 남의 것을 갈라 세지 않으면
        # "우리 때문에 남이 기다렸나"를 영영 못 가린다.
        sample.blocked_ours = sum(
            1 for item in loads if item.state == "D" and item.is_ours
        )
        sample.blocked_others = sum(
            1 for item in loads if item.state == "D" and not item.is_ours
        )

        picked = procstat.merge_top(loads, limit=self.top_processes)
        procstat.enrich(picked, with_cmdline=self.with_cmdline)
        sample.processes = picked

    # -- NFS ------------------------------------------------------------
    def _fill_mounts(self, sample: ServerSample, seconds: float) -> None:
        if not nfsstat.available():
            return
        after = nfsstat.read_mountstats()
        deltas = nfsstat.delta(self._mounts_before, after, seconds)
        self._mounts_before = after
        # 이 구간에 아무 일도 없던 마운트는 싣지 않는다 - 서버에 NFS 마운트가
        # 수십 개면 대부분이 그렇고, 0 만 담은 행으로 표를 채우면 정작 움직인
        # 마운트가 묻힌다.
        sample.mounts = [item for item in deltas.values() if item.total_ops > 0]


def take_one(
    work=None,
    minimum_seconds: float = MIN_INTERVAL_SECONDS,
    with_cmdline: bool = True,
) -> Optional[ServerSample]:
    """한 벌만 뜬다 (수집기처럼 잠깐 떴다 사라지는 프로세스용).

    `work` 를 주면 기준점과 표본 사이에 그것을 실행한다. 수집기는 어차피 df 를
    읽느라 몇 초를 쓰므로, 그 시간을 측정 구간으로 삼으면 **표본을 위해 따로
    기다리지 않아도 된다.** 그 구간에는 우리 수집 작업도 들어가는데, 그것 역시
    이 도구가 서버에 주는 부하의 일부라 빼는 것이 오히려 사실과 멀다.

    `/proc` 이 없는 환경(개발 PC)에서는 None. 잴 수 없는 값을 위해 일을
    늦추지는 않는다."""

    monitor = ServerMonitor(with_cmdline=with_cmdline)
    if not monitor.available():
        if work is not None:
            work()
        return None
    monitor.prime()
    started = time.monotonic()
    if work is not None:
        work()
    remaining = minimum_seconds - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)
    return monitor.sample()


def busiest_processes(
    sample: ServerSample, by: str = procstat.BY_CPU, limit: int = 5
) -> List[procstat.ProcessLoad]:
    return procstat.top(sample.processes, by=by, limit=limit)


def summarize_others(samples: Sequence[ServerSample]) -> Dict[str, float]:
    """여러 표본에서 '우리가 아닌 것들'이 만든 부하를 요약한다.

    스캔을 돌려도 되는지 판단할 때 필요한 것은 우리 몫이 아니라 **남은 여유**다.
    """

    others = [
        sample.others_cpu_percent
        for sample in samples
        if sample.others_cpu_percent is not None
    ]
    loads = [sample.load_avg_1m for sample in samples if sample.load_avg_1m is not None]
    blocked = [
        sample.blocked_others for sample in samples if sample.procs_blocked is not None
    ]
    result: Dict[str, float] = {}
    if others:
        result["cpu_avg"] = sum(others) / len(others)
        result["cpu_peak"] = max(others)
    if loads:
        result["load_avg"] = sum(loads) / len(loads)
        result["load_peak"] = max(loads)
    if blocked:
        result["blocked_avg"] = sum(blocked) / len(blocked)
        result["blocked_peak"] = float(max(blocked))
    return result
