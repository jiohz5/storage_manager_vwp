"""NFS 마운트마다 **실제로 오간 것**을 읽는다 (`/proc/self/mountstats`).

## 왜 CPU 로는 부족한가

상세 스캔이 하는 일은 거의 전부 메타데이터 조회다. CPU 는 32코어 중 0.26개만
쓰는데(진단 결과) 정작 파일서버 쪽에서는 초당 수천 건의 GETATTR/READDIRPLUS 가
오간다. CPU 사용률만 남기면 "부하가 없었다"는 잘못된 결론이 나온다. 진짜 부하가
어디에 걸렸는지는 이 수치가 아니면 답할 수 없다.

## 무엇을 보고 무엇을 판단할 수 있나

- **ops/초, bytes/초** - 이 마운트에 얼마나 실어 보냈나.
- **rtt (왕복 시간)** - 파일서버가 얼마나 빨리 답했나. 이것이 늘면 서버 쪽이
  밀리는 것이다.
- **queue (대기 시간)** - 요청이 **보내지기도 전에** 클라이언트에서 기다린
  시간. 이것이 늘면 서버가 아니라 **우리 쪽 RPC 슬롯이 모자란 것**이다.

이 둘을 가르는 것이 여기서 가장 중요하다. 진단에서 한 마운트의 처리량이
x1 에서 x32 까지 1.65배 남짓으로 눌린 것을 봤는데, 그 원인이 파일서버인지
클라이언트인지에 따라 할 일이 정반대다 (병렬을 줄일 것인가, 슬롯을 늘릴
것인가). 평생 누적값이 아니라 **구간 평균**을 내는 것도 그래서다 - 지난
한 달의 평균 rtt 는 지난 밤에 무슨 일이 있었는지 말해 주지 않는다.

## 이 값은 프로세스가 아니라 마운트의 것이다

`/proc/self/mountstats` 는 이름과 달리 "이 프로세스가 쓴 양"이 아니라 **그
마운트 전체**의 누적값을 준다 (superblock 에 달려 있다). 즉 여기 잡히는 것은
우리 스캔만이 아니라 이 서버가 그 마운트에 준 부하 전부다. 서버 전체 관점에서
보고 싶었던 것이 정확히 그것이라 오히려 맞다 - 다만 "스캔이 만든 양"으로
읽으면 안 되므로 이름과 문구를 그렇게 적지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

MOUNTSTATS = Path("/proc/self/mountstats")

# `events:` 줄에서 남길 것과 그 위치. 27개 전부를 남길 값어치는 없고, 메타데이터
# 순회에서 실제로 움직이는 것은 몇 개뿐이다.
EVENT_FIELDS = {
    "inode_revalidate": 0,   # 속성 캐시가 빗나가 서버에 다시 물은 횟수
    "dentry_revalidate": 1,
    "vfs_lookup": 5,
    "vfs_access": 6,
    "vfs_getdents": 12,      # 디렉터리 읽기 - 순회가 만드는 바로 그것
    "delay": 24,
}

# `per-op statistics` 에서 남길 연산. 순회가 만드는 것과, 그 옆에서 다른
# 사람이 만들었을 읽기/쓰기를 함께 본다.
TRACKED_OPS = ("GETATTR", "LOOKUP", "ACCESS", "READDIR", "READDIRPLUS", "READ", "WRITE")


@dataclass
class OpCounters:
    """한 연산의 누적값. 시간 단위는 밀리초."""

    ops: int = 0
    transmissions: int = 0
    timeouts: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    queue_ms: int = 0      # 보내지기 전 큐에서 기다린 누적 시간
    rtt_ms: int = 0        # 보낸 뒤 답이 올 때까지의 누적 시간
    execute_ms: int = 0    # 요청 전체의 누적 시간


@dataclass
class MountCounters:
    """마운트 하나의 누적값 한 벌."""

    device: str
    mount_point: str
    nfs_version: Optional[str] = None
    read_bytes: int = 0       # 서버에서 실제로 읽어온 양
    write_bytes: int = 0
    sends: int = 0
    receives: int = 0
    bad_xids: int = 0
    backlog_util: int = 0     # 누적 백로그 - 슬롯을 기다린 정도
    max_slots: Optional[int] = None
    events: Dict[str, int] = field(default_factory=dict)
    ops: Dict[str, OpCounters] = field(default_factory=dict)

    @property
    def total_ops(self) -> int:
        return sum(item.ops for item in self.ops.values())


def _ints(parts: List[str]) -> List[int]:
    out = []
    for value in parts:
        try:
            out.append(int(value))
        except ValueError:
            out.append(0)
    return out


def _version_from_opts(line: str) -> Optional[str]:
    for token in line.split(","):
        token = token.strip()
        if token.startswith("vers="):
            return token[5:]
    return None


def parse_mountstats(text: str) -> Dict[str, MountCounters]:
    """`/proc/self/mountstats` 본문을 마운트 지점별로 읽는다.

    NFS 가 아닌 마운트(ext4 등)는 통계 줄이 없으므로 건너뛴다."""

    mounts: Dict[str, MountCounters] = {}
    current: Optional[MountCounters] = None
    in_ops = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("device "):
            in_ops = False
            current = None
            # device <dev> mounted on <point> with fstype <type> [statvers=..]
            parts = line.split()
            try:
                fstype_index = parts.index("fstype")
                fstype = parts[fstype_index + 1]
            except (ValueError, IndexError):
                continue
            if not fstype.startswith("nfs"):
                continue
            try:
                on_index = parts.index("on")
            except ValueError:
                continue
            device = parts[1]
            mount_point = parts[on_index + 1]
            current = MountCounters(device=device, mount_point=mount_point)
            mounts[mount_point] = current
            continue

        if current is None:
            continue

        if line.startswith("opts:"):
            current.nfs_version = _version_from_opts(line)
        elif line.startswith("bytes:"):
            values = _ints(line.split()[1:])
            # normalread normalwrite directread directwrite serverread serverwrite ...
            # 앞의 둘은 **응용이 요청한 양**이고 캐시에서 답한 것도 들어간다.
            # 실제로 네트워크를 탄 것은 server* 쪽이라 그것을 쓴다.
            if len(values) >= 6:
                current.read_bytes = values[4]
                current.write_bytes = values[5]
        elif line.startswith("events:"):
            values = _ints(line.split()[1:])
            for name, index in EVENT_FIELDS.items():
                if index < len(values):
                    current.events[name] = values[index]
        elif line.startswith("xprt:"):
            parts = line.split()[1:]
            if not parts:
                continue
            transport, values = parts[0], _ints(parts[1:])
            if transport == "tcp" and len(values) >= 10:
                # port bind connect connect_time idle sends recvs bad_xids req_u bklog_u [max_slots ...]
                current.sends = values[5]
                current.receives = values[6]
                current.bad_xids = values[7]
                current.backlog_util = values[9]
                if len(values) >= 11:
                    current.max_slots = values[10]
            elif transport == "udp" and len(values) >= 6:
                # port bind sends recvs bad_xids req_u [bklog_u]
                current.sends = values[2]
                current.receives = values[3]
                current.bad_xids = values[4]
                if len(values) >= 7:
                    current.backlog_util = values[6]
        elif line.startswith("per-op statistics"):
            in_ops = True
        elif in_ops and ":" in line:
            name, _, rest = line.partition(":")
            name = name.strip()
            if name not in TRACKED_OPS:
                continue
            values = _ints(rest.split())
            if len(values) < 8:
                continue
            current.ops[name] = OpCounters(
                ops=values[0],
                transmissions=values[1],
                timeouts=values[2],
                bytes_sent=values[3],
                bytes_received=values[4],
                queue_ms=values[5],
                rtt_ms=values[6],
                execute_ms=values[7],
            )
    return mounts


@dataclass
class OpDelta:
    """한 구간 동안의 연산 하나."""

    name: str
    ops: int = 0
    ops_per_second: float = 0.0
    # 구간 평균이다. 평생 누적 평균은 지난 밤에 무슨 일이 있었는지 말해 주지
    # 않는다. 이 구간에 이 연산이 한 번도 없었으면 None - 0ms 로 쓰면
    # "아주 빨랐다"로 잘못 읽힌다.
    avg_rtt_ms: Optional[float] = None
    avg_queue_ms: Optional[float] = None
    avg_execute_ms: Optional[float] = None
    timeouts: int = 0


@dataclass
class MountDelta:
    """한 구간 동안 이 마운트에 오간 것."""

    device: str
    mount_point: str
    nfs_version: Optional[str] = None
    seconds: float = 0.0
    total_ops: int = 0
    ops_per_second: float = 0.0
    read_bytes: int = 0
    write_bytes: int = 0
    read_bytes_per_second: float = 0.0
    write_bytes_per_second: float = 0.0
    bad_xids: int = 0
    backlog_util: int = 0
    max_slots: Optional[int] = None
    events: Dict[str, int] = field(default_factory=dict)
    ops: Dict[str, OpDelta] = field(default_factory=dict)

    @property
    def avg_rtt_ms(self) -> Optional[float]:
        """전체 연산의 구간 평균 왕복 시간."""

        total_ops = sum(item.ops for item in self.ops.values())
        if not total_ops:
            return None
        weighted = sum(
            (item.avg_rtt_ms or 0.0) * item.ops for item in self.ops.values()
        )
        return weighted / total_ops

    @property
    def avg_queue_ms(self) -> Optional[float]:
        total_ops = sum(item.ops for item in self.ops.values())
        if not total_ops:
            return None
        weighted = sum(
            (item.avg_queue_ms or 0.0) * item.ops for item in self.ops.values()
        )
        return weighted / total_ops

    @property
    def queue_share(self) -> Optional[float]:
        """전체 대기 중 **클라이언트에서 기다린** 몫 (0~1).

        이것이 크면 파일서버가 아니라 우리 쪽 슬롯이 병목이다. 그때 병렬을
        줄이는 것은 정확히 반대 처방이 된다."""

        rtt = self.avg_rtt_ms
        queue = self.avg_queue_ms
        if rtt is None or queue is None or (rtt + queue) <= 0:
            return None
        return queue / (rtt + queue)


def _op_delta(name: str, before: OpCounters, after: OpCounters, seconds: float) -> OpDelta:
    ops = max(0, after.ops - before.ops)
    result = OpDelta(
        name=name,
        ops=ops,
        ops_per_second=ops / seconds if seconds > 0 else 0.0,
        timeouts=max(0, after.timeouts - before.timeouts),
    )
    if ops:
        result.avg_rtt_ms = max(0, after.rtt_ms - before.rtt_ms) / ops
        result.avg_queue_ms = max(0, after.queue_ms - before.queue_ms) / ops
        result.avg_execute_ms = max(0, after.execute_ms - before.execute_ms) / ops
    return result


def delta(
    before: Dict[str, MountCounters],
    after: Dict[str, MountCounters],
    seconds: float,
) -> Dict[str, MountDelta]:
    """두 표본을 견줘 마운트별 구간 값을 낸다.

    구간 중에 새로 마운트된 것은 기준점이 없으므로 건너뛴다 - 평생 누적값을
    한 구간의 값으로 실으면 그 시각에 엄청난 부하가 있었던 것처럼 보인다."""

    if seconds <= 0:
        return {}
    result: Dict[str, MountDelta] = {}
    for mount_point, now in after.items():
        was = before.get(mount_point)
        if was is None:
            continue
        item = MountDelta(
            device=now.device,
            mount_point=mount_point,
            nfs_version=now.nfs_version,
            seconds=seconds,
            read_bytes=max(0, now.read_bytes - was.read_bytes),
            write_bytes=max(0, now.write_bytes - was.write_bytes),
            bad_xids=max(0, now.bad_xids - was.bad_xids),
            backlog_util=max(0, now.backlog_util - was.backlog_util),
            max_slots=now.max_slots,
        )
        item.read_bytes_per_second = item.read_bytes / seconds
        item.write_bytes_per_second = item.write_bytes / seconds
        for name, counters in now.ops.items():
            item.ops[name] = _op_delta(
                name, was.ops.get(name, OpCounters()), counters, seconds
            )
        item.total_ops = sum(op.ops for op in item.ops.values())
        item.ops_per_second = item.total_ops / seconds
        for name, value in now.events.items():
            item.events[name] = max(0, value - was.events.get(name, 0))
        result[mount_point] = item
    return result


def available() -> bool:
    return MOUNTSTATS.exists()


def read_mountstats() -> Dict[str, MountCounters]:
    try:
        text = MOUNTSTATS.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return parse_mountstats(text)
