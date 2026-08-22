"""22:00~06:00 야간 실행 시간창 계산.

DESIGN.md 2부 9절의 열린 질문 - "기존 22:00~06:00 시간창 정책을 그대로
가져올지" - 에 대한 결정: 원칙(야간에만 무거운 상세 스캔, 06:00에는 강제
종료가 아니라 체크포인트를 남기고 안전하게 멈춤)을 그대로 계승하되 코드는
새로 짠다. 시간은 서버의 로컬 벽시계 기준이다 (cron도 로컬 시간으로 도니까).

터미널에서 사람이 직접 실행하는 스캔은 이 시간창의 적용을 받지 않는다
(DESIGN.md 1부 3절 "의도적 진단/복구 경로") - 호출자가 `bypass=True`로 넘기면
된다. 이 모듈 자체는 그 판단을 하지 않고, 창 여부와 남은 시간만 계산해 준다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from . import i18n

DEFAULT_START_HOUR = 22
DEFAULT_END_HOUR = 6


def is_within_window(now: datetime, start_hour: int = DEFAULT_START_HOUR, end_hour: int = DEFAULT_END_HOUR) -> bool:
    """`now`가 [start_hour, end_hour) 야간 시간창 안에 있는지 (자정을 넘어가는
    경우도 처리)."""

    hour = now.hour
    if start_hour == end_hour:
        return True  # 24시간 창 (설정 실수 방지용 안전한 기본 동작)
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def next_window_end(now: datetime, end_hour: int = DEFAULT_END_HOUR) -> datetime:
    """지금 진행 중이거나 다음에 올 시간창이 끝나는 시각(그날의 end_hour:00).

    `now`가 이미 자정을 넘어 창 뒤쪽(예: 02:00)에 있으면 오늘의 end_hour가
    경계이고, 그렇지 않으면(자정 전이거나 주간) 내일의 end_hour가 경계다.
    """

    end_today = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if now.hour < end_hour:
        return end_today
    return end_today + timedelta(days=1)


# 토(5), 일(6). `datetime.weekday()` 기준.
WEEKEND_WEEKDAYS = (5, 6)


def ends_on_weekend(now: datetime, end_hour: int = DEFAULT_END_HOUR) -> bool:
    """이 스캔이 **끝나는 아침**이 주말 아침인가.

    ## 왜 "지금 무슨 요일인가"를 묻지 않는가

    야간 스캔은 자정을 넘어간다. 그래서 "오늘이 주말인가"는 틀린 질문이고,
    맞는 질문은 **"내일 아침에 누가 출근하는가"**다. 스캔이 만든 부하는 밤새
    쌓였다가 그 아침에 자리에 앉는 사람에게 청구되기 때문이다.

    두 밤이 정반대가 된다:

    - **금요일 22:00** 시작 -> 토요일 06:00 종료. 토요일 아침은 한산하다.
      **주말 밤이다.**
    - **일요일 22:00** 시작 -> 월요일 06:00 종료. 월요일 아침에 전원이
      출근한다. **평일 밤이다.**

    "오늘이 주말인가"로 판정하면 이 둘이 정확히 뒤집힌다 - 가장 한산한 금요일
    밤을 얌전히 돌고, 가장 붐비는 월요일 아침 직전에 세게 돌게 된다.

    `next_window_end`를 그대로 재사용하므로, 판정 기준이 **스캔이 실제로 멈추는
    시각**과 언제나 일치한다 (두 곳에 같은 계산을 따로 두면 어긋난다).
    """

    return next_window_end(now, end_hour).weekday() in WEEKEND_WEEKDAYS


def seconds_remaining(now: datetime, end_hour: int = DEFAULT_END_HOUR) -> float:
    """시간창이 끝날 때까지 남은 초. 이미 지났으면 0."""

    remaining = (next_window_end(now, end_hour) - now).total_seconds()
    return max(0.0, remaining)


def describe(now: datetime, start_hour: int = DEFAULT_START_HOUR, end_hour: int = DEFAULT_END_HOUR) -> str:
    """진단/GUI 표시용 사람이 읽는 상태 문자열 (현재 언어로)."""

    if is_within_window(now, start_hour, end_hour):
        return i18n.t("scan.window_open", minutes=f"{seconds_remaining(now, end_hour) / 60:.0f}")
    return i18n.t("scan.window_closed", start=start_hour, end=end_hour)
