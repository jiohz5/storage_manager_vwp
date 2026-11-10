"""야간 상세 스캔이 계정을 도는 순서 (Qt 없음, DB 없음).

## 왜 따로 있는가

순서를 정하는 곳이 둘이다 - 실제로 도는 야간 스캔(`nightly_scan._fair_order`)과,
"오늘 밤 무엇이 몇 번째로 도나" 를 미리 보여 주는 화면(`scan_overview`). 둘이 같은
규칙을 쓰지 않으면 화면의 예고가 틀린다. 예전에는 같은 규칙을 두 곳에 따로 적어
두었다. 규칙만 여기 두고, 값(끝낸 때·사용량)은 각자 읽어서 넘긴다.

## 순서

1. **하다 만 계정** - 그 세대부터 끝낸다 (안 그러면 반쯤 잰 세대가 오래 남는다).
2. **한 번도 못 끝낸 계정.**
3. **너무 오래 못 본 계정** - 마지막으로 끝낸 지 `detail_scan_max_age_days` 가
   넘었다. 오래된 것부터. 아래 4번이 밤을 다 쓰더라도 굶는 계정이 없게 하는 줄이다.
4. **마지막 스캔 뒤로 많이 변한 계정** - 더 찬 스토리지부터, 같으면 더 많이 변한
   것부터.
5. **나머지** - 오래전에 끝낸 것부터.

## 왜 '변한 계정 먼저' 인가

예전에는 2번 다음이 곧장 5번이었다. 모든 계정을 같은 간격으로 도는 것이라 공평하지만,
계정이 백 개쯤이고 한 바퀴가 여러 밤이면 어제 500GB 늘어난 계정도 자기 차례까지 며칠을
기다렸다 - 그동안 화면의 '어디가 늘었나' 는 며칠 전 이야기다. 반대로 몇 주째 그대로인
보관 계정은 꼬박꼬박 다시 쟀다.

15분마다 모으는 표본이 마지막 스캔 뒤로 사용량이 얼마나 변했는지를 이미 알려 준다
(`drift_kb`). 스캔을 끝낼 때의 사용량을 남겨 두고(`account_scan_state.
used_kb_at_scan`) 지금 값과 견준다. '많이' 의 문턱은 화면과 같다
(`notable.growth_threshold_kb` - 계정 크기의 일정 비율, 바닥값 있음).

`df` 의 사용량은 계정이 아니라 파일시스템의 값이다. 한 스토리지를 여러 계정이 쓰면
그 스토리지가 변한 만큼 계정들이 **함께** '변한 계정' 이 된다 - 어느 계정 때문인지는
재 봐야 알기 때문이다. 그때 그들끼리의 순서는 예전처럼 오래된 순이다. quota 를 쓰면
그것이 계정의 값이다.

사용량을 모르는 계정(표본이 없거나, 이 규칙 전에 끝낸 스캔)은 4번에 들지 않을 뿐
5번에서 예전처럼 돈다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from . import tiers

REASON_RESUME = "resume"      # 하다 만 것
REASON_NEVER = "never"        # 한 번도 못 끝냄
REASON_OVERDUE = "overdue"    # 상한을 넘겨 못 봄
REASON_CHANGED = "changed"    # 마지막 스캔 뒤로 많이 변함
REASON_AGE = "age"            # 차례 (오래된 순)


@dataclass
class Candidate:
    """순서를 정할 계정 하나. 시각은 서로 견줄 수 있는 것이면 된다 (모두 현지 시각 등)."""

    account_id: str
    index: int                                   # 설정에 적힌 순서 (같은 조건의 순서)
    in_progress: bool = False                    # 다음 세대를 재다 말았다
    last_completed_at: Optional[datetime] = None
    drift_kb: Optional[int] = None               # 마지막 스캔 뒤 사용량이 변한 양. 모르면 None
    threshold_kb: int = 0                        # 이 계정에서 '많이 변했다' 의 문턱
    tier: str = tiers.UNKNOWN                    # 지금 등급 (찬 스토리지 먼저)


def usage_kb(sample) -> Optional[int]:
    """표본이 말하는 사용량 - quota 가 있으면 그 계정의 값, 없으면 파일시스템의 값.

    스캔을 끝낼 때와 지금, **같은 정의로** 읽어 견주기만 하면 된다. 수집이 실패한
    표본은 아무 말도 하지 않는다 (옛 값이거나 비어 있다)."""

    if sample is None or not getattr(sample, "ok", False):
        return None
    quota = getattr(sample, "quota_used_kb", None)
    if quota is not None:
        return quota
    return getattr(sample, "used_kb", None)


def drift_kb(sample, used_kb_at_scan: Optional[int]) -> Optional[int]:
    """마지막 스캔 뒤로 사용량이 변한 양 (늘었든 줄었든). 어느 쪽이든 모르면 None.

    줄어든 것도 변한 것이다 - 누가 크게 정리했으면 화면의 '어디가 큰가' 가 낡았다."""

    now = usage_kb(sample)
    if now is None or used_kb_at_scan is None:
        return None
    return abs(now - used_kb_at_scan)


def reason_for(candidate: Candidate, now: datetime, max_age_days: int) -> str:
    if candidate.in_progress:
        return REASON_RESUME
    if candidate.last_completed_at is None:
        return REASON_NEVER
    if now - candidate.last_completed_at >= timedelta(days=max(1, max_age_days)):
        return REASON_OVERDUE
    if (candidate.drift_kb is not None and candidate.threshold_kb > 0
            and candidate.drift_kb >= candidate.threshold_kb):
        return REASON_CHANGED
    return REASON_AGE


_GROUP = {REASON_RESUME: 0, REASON_NEVER: 1, REASON_OVERDUE: 2, REASON_CHANGED: 3, REASON_AGE: 4}


def order(candidates: Sequence[Candidate], now: datetime,
          max_age_days: int) -> List[Tuple[Candidate, str]]:
    """`[(계정, 까닭), ...]` - 이 밤에 도는 순서대로."""

    def rank(pair):
        candidate, reason = pair
        group = _GROUP[reason]
        if reason in (REASON_RESUME, REASON_NEVER):
            return (group, 0, 0, datetime.min, candidate.index)
        if reason == REASON_CHANGED:
            return (group, -tiers.severity(candidate.tier), -candidate.drift_kb,
                    candidate.last_completed_at, candidate.index)
        return (group, 0, 0, candidate.last_completed_at, candidate.index)

    pairs = [(candidate, reason_for(candidate, now, max_age_days)) for candidate in candidates]
    return sorted(pairs, key=rank)
