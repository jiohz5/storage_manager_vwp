"""이력을 그릴 수 있는 모양으로 줄인다 (Qt 없음).

## 왜 필요한가

df 표본을 90일치 쌓고 있는데 **어디에도 그려지지 않는다.** 용량 관리 도구에
추세선이 없는 것은 좀 이상한 일이다 - "지금 82%" 보다 "두 주 만에 60%에서
82%로 왔다" 가 훨씬 많은 것을 말한다.

## 표본을 그대로 그리지 않는다

15분마다 90일이면 8,640점이다. 폭 200픽셀짜리 칸에 그것을 다 찍어 봐야 한 픽셀에
40점이 겹칠 뿐이다. 시간으로 칸을 나누고 칸마다 한 점으로 줄인다.

칸의 대표값은 **마지막 값**이다. 최고값을 쓰면 잠깐 찼다가 정리된 순간이
계속 남아 "줄지 않는 것처럼" 보인다. 다만 그 최고값도 따로 들고 있어서,
그리는 쪽이 원하면 띠로 보여 줄 수 있다.

## 빈 칸을 이어 붙이지 않는다

수집기가 하루 멈췄으면 그 자리에 값이 없다. 그때 양옆을 직선으로 이으면
**없는 데이터를 있는 것처럼** 그리게 된다 - 그 직선 위의 어떤 지점을 짚어도
우리는 그 값을 모른다. 빈 칸은 None 으로 두고 선을 끊는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence

# 그릴 때 쓸 기본 칸 수. 폭이 얼마든 이보다 촘촘히 그려 봐야 눈에 안 보인다.
DEFAULT_BUCKETS = 120


@dataclass
class Point:
    """한 칸. 값이 없으면 `value` 가 None 이고 선이 끊긴다."""

    at: Optional[datetime] = None
    value: Optional[float] = None
    # 이 칸에서 가장 높았던 값. 선은 `value` 로 긋되, 잠깐 튄 것을 보고 싶을
    # 때 쓸 수 있게 따로 둔다.
    peak: Optional[float] = None
    samples: int = 0

    @property
    def measured(self) -> bool:
        return self.value is not None


@dataclass
class Series:
    """그릴 준비가 된 한 줄."""

    points: List[Point] = field(default_factory=list)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    @property
    def measured_points(self) -> List[Point]:
        return [point for point in self.points if point.measured]

    @property
    def empty(self) -> bool:
        return not self.measured_points

    @property
    def first(self) -> Optional[float]:
        measured = self.measured_points
        return measured[0].value if measured else None

    @property
    def last(self) -> Optional[float]:
        measured = self.measured_points
        return measured[-1].value if measured else None

    @property
    def low(self) -> Optional[float]:
        measured = self.measured_points
        return min(point.value for point in measured) if measured else None

    @property
    def high(self) -> Optional[float]:
        measured = self.measured_points
        return max(point.value for point in measured) if measured else None

    @property
    def change(self) -> Optional[float]:
        """처음과 마지막의 차이. 잴 수 없으면 None.

        **0 과 None 은 다르다** - 앞쪽은 "안 변했다", 뒤쪽은 "모른다"다."""

        if self.first is None or self.last is None:
            return None
        return self.last - self.first

    @property
    def gaps(self) -> int:
        """값이 없는 칸 수. 화면이 "이 기간에 구멍이 있다"를 말할 수 있게."""

        return sum(1 for point in self.points if not point.measured)


def _parse(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def build(
    samples: Sequence,
    field_name: str = "byte_pct",
    buckets: int = DEFAULT_BUCKETS,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> Series:
    """표본들을 칸으로 나눠 그릴 수 있는 줄로 만든다.

    `samples` 는 `collected_at` 과 `field_name` 을 가진 것이면 된다
    (`store.SampleRecord`). 순서는 상관없다.
    """

    pairs = []
    for sample in samples:
        moment = _parse(getattr(sample, "collected_at", None))
        value = getattr(sample, field_name, None)
        if moment is None or value is None:
            continue
        if since and moment < since:
            continue
        if until and moment > until:
            continue
        pairs.append((moment, float(value)))

    if not pairs:
        return Series()
    pairs.sort(key=lambda item: item[0])

    start = since or pairs[0][0]
    end = until or pairs[-1][0]
    span = (end - start).total_seconds()
    count = max(1, int(buckets))
    if span <= 0:
        # 표본이 하나이거나 전부 같은 시각이다. 칸을 나눌 것이 없다.
        moment, value = pairs[-1]
        return Series(
            points=[Point(at=moment, value=value, peak=value, samples=len(pairs))],
            started_at=start,
            ended_at=end,
        )

    slots: List[List[float]] = [[] for _ in range(count)]
    times: List[Optional[datetime]] = [None] * count
    for moment, value in pairs:
        index = int((moment - start).total_seconds() / span * count)
        index = min(count - 1, max(0, index))
        slots[index].append(value)
        times[index] = moment

    points = []
    for index in range(count):
        values = slots[index]
        if not values:
            # 수집기가 멈춰 있던 자리. 양옆을 이으면 없는 데이터를 그리게 된다.
            points.append(Point())
            continue
        points.append(
            Point(
                at=times[index],
                value=values[-1],   # 그 칸의 마지막 = 그때의 수준
                peak=max(values),
                samples=len(values),
            )
        )
    return Series(points=points, started_at=start, ended_at=end)


def segments(series: Series) -> List[List[int]]:
    """끊기지 않고 이어지는 구간들의 인덱스 묶음.

    그리는 쪽은 묶음마다 따로 선을 그으면 된다 - 빈 칸을 건너뛰며 한 줄로
    그으면 없는 데이터를 있는 것처럼 만든다."""

    runs: List[List[int]] = []
    current: List[int] = []
    for index, point in enumerate(series.points):
        if point.measured:
            current.append(index)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs
