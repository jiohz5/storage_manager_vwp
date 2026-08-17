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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROC_STAT = Path("/proc/stat")
PROC_SELF_STAT = Path("/proc/self/stat")


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
        self.cpu_count = os.cpu_count() or 1

    def sample(self) -> None:
        if self._sampler is None:
            return
        usage = self._sampler.take()
        self._last_load = usage.load_avg_1m
        if usage.system_percent is None:
            return
        self._system.append(usage.system_percent)
        self._top.append(usage.top_percent)

    def summary(self) -> Summary:
        if not self._system:
            return Summary(samples=0, load_avg_1m=self._last_load, cpu_count=self.cpu_count)
        return Summary(
            samples=len(self._system),
            system_percent_avg=sum(self._system) / len(self._system),
            system_percent_peak=max(self._system),
            top_percent_avg=sum(self._top) / len(self._top),
            top_percent_peak=max(self._top),
            load_avg_1m=self._last_load,
            cpu_count=self.cpu_count,
        )
