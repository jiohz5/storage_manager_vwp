"""쌓인 서버 부하 표본을 판단에 쓸 수 있는 모양으로 묶는다 (Qt 없음).

## 이것이 답하려는 질문

표본을 아무리 많이 쌓아도 그대로는 판단이 안 된다. 실제로 물어야 할 것은
셋뿐이다.

1. **낮에 상세 스캔을 돌려도 되나** - 낮 시간대에 이 서버가 이미 얼마나
   바쁜지, 남는 여유가 얼마인지.
2. **몇 갈래까지 동시에 돌려도 되나** - 갈래를 늘렸을 때 실제로 무엇이
   늘어났고 무엇이 안 늘어났는지.
3. **그때 누가 서버를 쓰고 있었나** - 부하가 우리 것인지 남의 것인지.

## 왜 '우리를 뺀 나머지'로 보는가

스캔을 돌려도 되는지 판단할 때 필요한 것은 우리가 쓴 양이 아니라 **남은
여유**다. 전체 사용률만 보면 우리 스캔이 만든 부하까지 "서버가 원래 바쁘다"로
읽혀서, 돌릴수록 못 돌릴 것 같아지는 이상한 결론이 나온다.

## 왜 시간대별로 자르는가

하루 평균은 아무 말도 안 해 준다. 낮 두 시와 새벽 세 시가 같은 서버일 리
없는데 평균은 그 둘을 섞어 버린다. 판단은 "내가 돌리려는 그 시간대"에
대해서만 뜻이 있다.

시간대는 **지역시간**으로 자른다. UTC 시간대로 자르면 사람이 아는 "오후"와
9시간 어긋난 칸이 나온다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .formatting import to_local

# 이 값들은 '남의 부하'가 이미 높다고 볼 기준이다.
#
# 절대적인 선이 아니라 **이야기를 시작할 지점**이다. 코어가 32개인 서버에서
# busy 50% 는 절반이 놀고 있다는 뜻이라 스캔 하나 더 얹을 자리가 있다.
BUSY_CROWDED_PERCENT = 50.0
# 막힌 프로세스가 이만큼 있으면 I/O 가 이미 밀리는 중이다. CPU 와 달리 이쪽은
# 작은 수부터 뜻이 있다 - 하나라도 있으면 누군가는 기다리고 있는 것이다.
BLOCKED_CROWDED = 2.0


def _value(row, name: str) -> Optional[float]:
    """sqlite3.Row 든 dict 든 dataclass 든 같은 방식으로 읽는다."""

    if hasattr(row, "keys"):
        try:
            value = row[name]
        except (IndexError, KeyError):
            return None
    else:
        value = getattr(row, name, None)
    return None if value is None else float(value)


def others_cpu_percent(row) -> Optional[float]:
    """전체 CPU 사용률에서 우리 몫을 뺀 것 (머신 전체 용량 대비 %).

    우리 몫은 `top` 기준(코어 하나 = 100%)이라 코어 수로 나눠 기준을 맞춘다."""

    busy = _value(row, "cpu_busy_percent")
    if busy is None:
        return None
    ours = _value(row, "scan_cpu_percent")
    cpu_count = _value(row, "cpu_count")
    if ours is None or not cpu_count:
        return busy
    return max(0.0, busy - ours / cpu_count)


def scan_was_running(row) -> bool:
    """이 표본을 뜰 때 우리 스캔이 돌고 있었나."""

    if _value(row, "scan_process_count"):
        return True
    if hasattr(row, "keys"):
        try:
            return bool(row["run_id"])
        except (IndexError, KeyError):
            return False
    return bool(getattr(row, "run_id", None))


@dataclass
class Stat:
    """한 지표의 평균과 최고. 표본이 없으면 둘 다 None."""

    average: Optional[float] = None
    peak: Optional[float] = None
    samples: int = 0

    @classmethod
    def of(cls, values: Sequence[float]) -> "Stat":
        clean = [value for value in values if value is not None]
        if not clean:
            return cls()
        return cls(
            average=sum(clean) / len(clean), peak=max(clean), samples=len(clean)
        )


@dataclass
class Bucket:
    """한 묶음(시간대 하나, 또는 '스캔 중')의 요약."""

    label: str
    samples: int = 0
    # 우리를 뺀 나머지가 쓴 CPU. 판단에 직접 쓰이는 값이라 맨 앞에 둔다.
    others_cpu: Stat = field(default_factory=Stat)
    cpu_busy: Stat = field(default_factory=Stat)
    cpu_iowait: Stat = field(default_factory=Stat)
    load_avg: Stat = field(default_factory=Stat)
    blocked: Stat = field(default_factory=Stat)
    blocked_others: Stat = field(default_factory=Stat)
    memory_used: Stat = field(default_factory=Stat)
    nfs_ops: Stat = field(default_factory=Stat)
    # 이 묶음의 표본 중 우리 스캔이 돌고 있던 것의 수. 낮 시간대 프로파일을
    # 읽을 때 "이 숫자에 우리가 섞여 있나"를 먼저 알아야 한다.
    scan_samples: int = 0

    @property
    def scan_free(self) -> bool:
        """우리 스캔이 전혀 안 돌던 묶음인가 - 순수한 '평소' 모습."""

        return self.scan_samples == 0

    @property
    def crowded(self) -> bool:
        """이미 붐비는 시간대인가."""

        if self.blocked_others.average is not None:
            if self.blocked_others.average >= BLOCKED_CROWDED:
                return True
        average = self.others_cpu.average
        return average is not None and average >= BUSY_CROWDED_PERCENT


def summarize(rows: Sequence, label: str = "") -> Bucket:
    """표본 묶음 하나를 요약한다."""

    rows = list(rows)
    bucket = Bucket(label=label, samples=len(rows))
    if not rows:
        return bucket
    bucket.others_cpu = Stat.of([others_cpu_percent(row) for row in rows])
    bucket.cpu_busy = Stat.of([_value(row, "cpu_busy_percent") for row in rows])
    bucket.cpu_iowait = Stat.of([_value(row, "cpu_iowait_percent") for row in rows])
    bucket.load_avg = Stat.of([_value(row, "load_avg_1m") for row in rows])
    bucket.blocked = Stat.of([_value(row, "procs_blocked") for row in rows])
    bucket.blocked_others = Stat.of([_value(row, "blocked_others") for row in rows])
    bucket.memory_used = Stat.of([_value(row, "memory_used_percent") for row in rows])
    bucket.nfs_ops = Stat.of([_value(row, "nfs_ops_per_second") for row in rows])
    bucket.scan_samples = sum(1 for row in rows if scan_was_running(row))
    return bucket


def hourly_profile(rows: Sequence, tz=None) -> List[Bucket]:
    """지역시간 기준 시간대별 요약 (표본이 있는 시간대만, 0시부터).

    하루 평균이 아니라 시간대별로 보는 이유: 낮 두 시와 새벽 세 시가 같은
    서버일 리 없는데 평균은 그 둘을 섞는다. 판단은 "내가 돌리려는 그 시간대"에
    대해서만 뜻이 있다."""

    by_hour: Dict[int, List] = {}
    for row in rows:
        moment = to_local(_stamp(row), tz)
        if moment is None:
            continue
        by_hour.setdefault(moment.hour, []).append(row)
    return [
        summarize(by_hour[hour], label=f"{hour:02d}시")
        for hour in sorted(by_hour)
    ]


def _stamp(row):
    if hasattr(row, "keys"):
        try:
            return row["sampled_at"]
        except (IndexError, KeyError):
            return None
    return getattr(row, "sampled_at", None)


def split_by_scan(rows: Sequence) -> Dict[str, Bucket]:
    """스캔이 돌 때와 안 돌 때를 갈라 요약한다.

    "스캔이 서버를 얼마나 흔드나"에 답하는 가장 곧은 방법이다 - 같은 서버의
    두 상태를 나란히 놓는 것."""

    with_scan, without = [], []
    for row in rows:
        (with_scan if scan_was_running(row) else without).append(row)
    return {
        "with_scan": summarize(with_scan, label="스캔 중"),
        "without_scan": summarize(without, label="스캔 없음"),
    }


@dataclass
class Verdict:
    """이 시간대에 스캔을 얹어도 되겠는가 - 그리고 왜."""

    label: str
    room: bool
    reason: str
    others_cpu: Optional[float] = None
    blocked_others: Optional[float] = None
    samples: int = 0


def daytime_verdict(bucket: Bucket) -> Verdict:
    """묶음 하나를 보고 '여유가 있나'를 말한다.

    **판단을 대신하지 않고 근거를 함께 준다.** 표본이 적으면 그 사실부터
    말한다 - 두세 벌로 "여유 있음"이라고 하면 그것은 측정이 아니라 추측이다.
    """

    if bucket.samples < 4:
        return Verdict(
            label=bucket.label,
            room=False,
            reason="표본이 모자랍니다",
            samples=bucket.samples,
        )
    others = bucket.others_cpu.average
    blocked = bucket.blocked_others.average
    if bucket.crowded:
        if blocked is not None and blocked >= BLOCKED_CROWDED:
            reason = "이미 I/O 를 기다리는 작업이 있습니다"
        else:
            reason = "다른 작업이 CPU 를 이미 쓰고 있습니다"
        return Verdict(bucket.label, False, reason, others, blocked, bucket.samples)
    return Verdict(
        bucket.label, True, "여유가 있습니다", others, blocked, bucket.samples
    )


def busiest_hours(buckets: Sequence[Bucket], limit: int = 3) -> List[Bucket]:
    """남의 부하가 가장 높은 시간대부터."""

    ranked = [item for item in buckets if item.others_cpu.average is not None]
    ranked.sort(key=lambda item: -(item.others_cpu.average or 0.0))
    return ranked[:limit]


def quietest_hours(
    buckets: Sequence[Bucket], limit: int = 3, scan_free_only: bool = True
) -> List[Bucket]:
    """스캔을 얹기 좋은 시간대부터.

    기본으로 **우리 스캔이 돌던 시간대는 뺀다.** 새벽 세 시가 한가한 것은
    당연한데, 그 시간대는 이미 우리가 쓰고 있어서 "여기에 더 얹으세요"라는
    추천이 되지 않는다. 이 목록이 답해야 하는 것은 "지금 아무도 안 쓰는
    시간이 언제인가"이므로 섞인 칸은 근거가 못 된다.

    표본이 모자란 시간대도 뺀다 - 두세 벌로 고른 "가장 한가한 때"는 측정이
    아니라 우연이다."""

    ranked = [
        item
        for item in buckets
        if item.others_cpu.average is not None
        and item.samples >= 4
        and (item.scan_free or not scan_free_only)
    ]
    ranked.sort(key=lambda item: (item.others_cpu.average or 0.0))
    return ranked[:limit]
