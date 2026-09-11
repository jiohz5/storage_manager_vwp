"""무엇부터 손대야 하는가 (Qt 없음).

## 왜 따로 만드는가

지금 이 도구가 아는 사실은 여러 곳에 흩어져 있다. 사용률은 홈 표에, 백업
상태는 헬스체크에, 튀는 파일은 상세 스캔 탭에, 급증은 알림에. 각각은 맞는
말인데, **"그래서 오늘 뭘 먼저 하지"** 에는 아무도 답하지 않는다.

여기서 그 하나에 답한다.

## 문제와 해법을 붙여 놓는다

이것이 이 파일의 핵심이다. 계정이 96% 인 것과 그 계정에 정리 가능한 800GB 가
있는 것은 **따로 놓으면 두 줄이지만 붙여 놓으면 한 줄이고 행동이 된다.**

    layout_proj 96% - 정리하면 800GB 빕니다 (백업 확인된 과제 6개)

따로 두면 사람이 두 화면을 오가며 스스로 이어 붙여야 하고, 그러면 대개 안 한다.

## 순서를 정하는 두 축

- **급한가** - 지금 안 하면 나빠지는가. 꽉 찬 스토리지와 백업 안 된 과제가
  여기 든다. 백업 안 된 쪽은 용량 문제가 아니라 **잃을 위험**이라 같은 칸에 둔다.
- **효과가 큰가** - 같은 급함 안에서는 비울 양이나 크기가 큰 것부터.

## 없는 근거로 순위를 만들지 않는다

스캔이 아직 안 돈 계정은 정리 후보를 알 수 없다. 그때 "정리할 것 없음" 이라고
하면 거짓이 된다 - 목록에서 빠질 뿐이고, 왜 비어 있는지는 화면이 따로 말한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from . import health as health_module
from . import large_files as large_files_module
from . import tiers

# 급함의 단계. 숫자가 작을수록 먼저다.
LEVEL_CRITICAL = 0   # 지금 조치해야 한다
LEVEL_HIGH = 1       # 오늘 안에 봐야 한다
LEVEL_MEDIUM = 2     # 알고는 있어야 한다

# 할 일의 종류.
ACT_FULL = "full"                # 스토리지가 찼다
ACT_NO_BACKUP = "no_backup"      # 백업이 안 된 과제가 있다
ACT_CLEANUP = "cleanup"          # 정리하면 자리가 빈다
ACT_BIG_FILE = "big_file"        # 파일 하나가 지나치게 크다
ACT_SURGE = "surge"              # 갑자기 늘었다

# 목록에 실을 최대 줄 수. 이보다 많으면 그것은 목록이 아니라 표이고, 표가
# 되는 순간 "무엇부터" 라는 물음이 다시 사라진다.
MAX_ACTIONS = 10

# 이만큼 이상 비울 수 있을 때만 정리를 따로 권한다. 몇 GB 를 위해 사람을
# 움직이게 하면 다음부터 이 목록을 안 믿는다.
CLEANUP_WORTH_KB = 50 * 1024 * 1024   # 50GB

# 이만큼 늘면 급증으로 본다.
SURGE_KB = 200 * 1024 * 1024          # 200GB


@dataclass
class Action:
    """무엇부터 손댈지 한 줄. 화면이 문구를 만들 재료만 담는다."""

    kind: str
    level: int
    account: str
    account_id: str = ""
    # 이 조치로 빌 양. 모르면 None (0 과 다르다 - 0 은 "비울 것이 없다"다).
    reclaimable_kb: Optional[int] = None
    size_kb: Optional[int] = None
    delta_kb: Optional[int] = None
    pct: Optional[float] = None
    path: str = ""
    count: int = 0

    @property
    def critical(self) -> bool:
        return self.level == LEVEL_CRITICAL


@dataclass
class Plan:
    """오늘 할 일."""

    actions: List[Action] = field(default_factory=list)
    # 판단에 못 쓴 것들. 비어 있는 목록이 "문제 없음"으로 읽히지 않게 한다.
    accounts_without_scan: List[str] = field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return sum(1 for item in self.actions if item.critical)

    @property
    def reclaimable_kb(self) -> int:
        """전부 정리하면 빌 양 (같은 계정을 두 번 세지 않는다).

        **종류를 가리지 않고 센다.** 정리량은 사용률 줄에 얹혀 오기도 하는데
        (문제와 해법을 붙여 놓았으므로), 정리 줄만 세면 그럴 때 합계가 0 이
        된다 - 실제로는 800GB 를 비울 수 있는데 화면은 없다고 말하게 된다."""

        seen = set()
        total = 0
        for item in self.actions:
            if not item.reclaimable_kb or item.account_id in seen:
                continue
            seen.add(item.account_id)
            total += item.reclaimable_kb
        return total


def _usage_level(pct: Optional[float]) -> Optional[int]:
    """사용률을 급함 단계로. 등급 기준을 다시 쓰지 않고 `tiers` 를 따른다."""

    if pct is None:
        return None
    tier = tiers.classify(pct)
    if tiers.is_at_least(tier, tiers.EMERGENCY):
        return LEVEL_CRITICAL
    if tiers.is_at_least(tier, tiers.ALERT):
        return LEVEL_HIGH
    if tiers.is_at_least(tier, tiers.WARN):
        return LEVEL_MEDIUM
    return None


def build(
    samples=(),
    health_summary: Optional[health_module.HealthSummary] = None,
    scan_accounts: Sequence = (),
    accounts_by_id: Optional[dict] = None,
) -> Plan:
    """흩어진 사실들을 "무엇부터" 하나로 모은다.

    - `samples` - 계정별 최신 df 표본 (`used_percent` 와 `account_id` 를 읽는다).
    - `health_summary` - `health.check_all` 결과.
    - `scan_accounts` - `nightly_scan.StatusSnapshot.accounts`.
    - `accounts_by_id` - 계정 이름을 찾기 위한 `{id: 이름}`.
    """

    names = dict(accounts_by_id or {})
    plan = Plan()
    actions: List[Action] = []

    # -- 정리 후보를 계정별로 미리 모아 둔다 ---------------------------
    #
    # 사용률 줄과 붙여 놓아야 "96% 인데 정리하면 800GB 빈다" 한 줄이 된다.
    cleanup_by_account = {}
    if health_summary is not None:
        for item in health_summary.cleanable:
            bucket = cleanup_by_account.setdefault(
                item.account_id, {"kb": 0, "count": 0, "name": item.account_name}
            )
            bucket["kb"] += item.reclaimable_kb
            bucket["count"] += 1

    # -- 1. 스토리지가 찼다 --------------------------------------------
    for sample in samples:
        pct = getattr(sample, "used_percent", None)
        level = _usage_level(pct)
        if level is None:
            continue
        account_id = getattr(sample, "account_id", "")
        relief = cleanup_by_account.get(account_id)
        actions.append(
            Action(
                kind=ACT_FULL,
                level=level,
                account=names.get(account_id, account_id),
                account_id=account_id,
                pct=pct,
                # 해법을 같은 줄에 붙인다.
                reclaimable_kb=relief["kb"] if relief else None,
                count=relief["count"] if relief else 0,
            )
        )

    # -- 2. 백업이 안 된 과제 -------------------------------------------
    #
    # 용량 문제가 아니라 **잃을 위험**이다. 그래서 사용률과 같은 칸에 둔다.
    if health_summary is not None:
        risky_by_account = {}
        for item in health_summary.risky:
            bucket = risky_by_account.setdefault(
                item.account_id,
                {"count": 0, "kb": 0, "name": item.account_name, "path": item.label},
            )
            bucket["count"] += 1
            bucket["kb"] += item.backup_size_kb or item.run_size_kb or 0
        for account_id, bucket in risky_by_account.items():
            actions.append(
                Action(
                    kind=ACT_NO_BACKUP,
                    level=LEVEL_HIGH,
                    account=bucket["name"],
                    account_id=account_id,
                    size_kb=bucket["kb"],
                    count=bucket["count"],
                    path=bucket["path"],
                )
            )

    # -- 3. 정리 후보 (사용률 줄에 안 붙은 것만) --------------------------
    #
    # 이미 붙은 계정을 또 내놓으면 같은 일이 두 줄이 되고, 합계도 두 번 세인다.
    mentioned = {item.account_id for item in actions if item.kind == ACT_FULL}
    for account_id, bucket in cleanup_by_account.items():
        if account_id in mentioned or bucket["kb"] < CLEANUP_WORTH_KB:
            continue
        actions.append(
            Action(
                kind=ACT_CLEANUP,
                level=LEVEL_MEDIUM,
                account=bucket["name"],
                account_id=account_id,
                reclaimable_kb=bucket["kb"],
                count=bucket["count"],
            )
        )

    # -- 4. 스캔이 준 것들 ----------------------------------------------
    for entry in scan_accounts:
        name = getattr(entry, "account_name", "")
        if getattr(entry, "last_completed_generation", None) is None:
            plan.accounts_without_scan.append(name)
            continue

        for item in large_files_module.build(
            getattr(entry, "large_files", []) or [], entry.measured_kb
        ):
            if not item.dominates_account:
                # 계정을 혼자 차지하는 것만 올린다. 그냥 큰 파일은 상세 스캔
                # 탭에서 보면 되고, 여기 올리면 목록이 파일 목록이 된다.
                continue
            actions.append(
                Action(
                    kind=ACT_BIG_FILE,
                    level=LEVEL_MEDIUM,
                    account=name,
                    account_id=entry.account_id,
                    size_kb=item.size_kb,
                    delta_kb=item.delta_kb,
                    pct=item.share_pct,
                    path=item.path,
                )
            )

        previous = getattr(entry, "previous_measured_kb", None)
        if entry.measured_kb is not None and previous is not None:
            delta = entry.measured_kb - previous
            if delta >= SURGE_KB:
                actions.append(
                    Action(
                        kind=ACT_SURGE,
                        level=LEVEL_MEDIUM,
                        account=name,
                        account_id=entry.account_id,
                        delta_kb=delta,
                        size_kb=entry.measured_kb,
                    )
                )

    actions.sort(key=_rank)
    plan.actions = actions[:MAX_ACTIONS]
    return plan


def _rank(action: Action):
    """급한 것부터, 같은 급함 안에서는 **효과가 큰 것**부터."""

    impact = max(
        action.reclaimable_kb or 0,
        action.size_kb or 0,
        action.delta_kb or 0,
    )
    return (action.level, -impact, action.account)
