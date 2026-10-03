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
    # 같은 스토리지를 쓰는 계정들 `[(이름, id, 정리가능KB, 과제수), ...]`.
    # 하나뿐이면 예전과 같은 한 계정짜리 줄이다.
    shared: List["tuple"] = field(default_factory=list)
    mount_point: str = ""

    @property
    def critical(self) -> bool:
        return self.level == LEVEL_CRITICAL

    @property
    def shared_count(self) -> int:
        return len(self.shared)


@dataclass
class Plan:
    """오늘 할 일."""

    actions: List[Action] = field(default_factory=list)
    # 판단에 못 쓴 것들. 비어 있는 목록이 "문제 없음"으로 읽히지 않게 한다.
    accounts_without_scan: List[str] = field(default_factory=list)
    # 성기게 판정한 과제 수 (`health.RunHealth.coarse`). 화면이 이 수를 밝혀야
    # 한다 - 성긴 판정을 정밀한 것처럼 내놓으면 "확인됨"이 실제보다 강하게
    # 읽히고, 그 위에서 정리 결정이 내려진다.
    coarse_count: int = 0

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


# 같은 스토리지로 볼 사용률 차이(%p).
#
# 계정마다 `df` 를 부르는 시각이 몇 초씩 어긋나므로 같은 파일시스템이라도
# 숫자가 딱 맞지는 않는다. 그 정도 흔들림만 허용한다.
SAME_STORAGE_PCT = 0.5


def _storage_key(sample) -> "tuple":
    """같은 스토리지로 볼 것인가.

    `df` 는 계정이 아니라 **그 경로가 속한 파일시스템**을 잰다. 한 스토리지에
    계정이 여덟이면 여덟 계정이 똑같은 숫자를 돌려주므로, 그대로 두면 똑같은
    줄이 여덟 개 생기고 목록 상한(MAX_ACTIONS) 안에서 다른 종류(백업 없음,
    큰 파일)를 밀어낸다.

    파일시스템·마운트 지점·전체 크기가 같으면 같은 자리로 본다. 전체 크기를
    넣는 이유: 디렉터리 쿼터가 컨테이너로 걸려 있으면 같은 마운트라도 계정마다
    다른 크기가 돌아온다 - 그때는 갈라야 맞다.

    셋 중 하나라도 모르면 묶지 않는다 - 모르는 것을 근거로 묶으면 남의 계정을
    한 줄에 넣게 된다.

    ## `paths.volume_key` 와 다른 것이다 (합치지 말 것)

    야간 스캔은 `paths.volume_key` 로 계정을 묶어 병렬을 정한다. 그쪽이 묻는
    것은 **처리 능력을 나눠 쓰는 단위**(NetApp 볼륨, NFS export)이고, 여기서
    묻는 것은 **용량을 나눠 쓰는 단위**다. 디렉터리 쿼터가 컨테이너로 걸리면
    같은 볼륨 안에서도 용량은 따로 차므로 둘은 갈린다 - 같은 뜻으로 보고
    하나로 합치면 한쪽이 반드시 틀린다."""

    filesystem = getattr(sample, "filesystem", None)
    mount_point = getattr(sample, "mount_point", None)
    total_kb = getattr(sample, "total_kb", None)
    if not filesystem or not mount_point or not total_kb:
        return ("account", getattr(sample, "account_id", ""))
    return (filesystem, mount_point, total_kb)


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

    - `samples` - 계정별 최신 df 표본 (`store.SampleRecord` 의 `byte_pct`,
      `ok`, `account_id` 를 읽는다).
    - `health_summary` - `health.check_all` 결과.
    - `scan_accounts` - `nightly_scan.StatusSnapshot.accounts`.
    - `accounts_by_id` - 계정 이름을 찾기 위한 `{id: 이름}`.

    종류마다 함수 하나가 줄을 만들고, 여기서는 모아서 순서만 정한다. 넣는
    순서(사용률 → 백업 → 정리 → 스캔)는 순위가 같을 때의 순서가 되므로 바꾸지
    않는다.
    """

    names = dict(accounts_by_id or {})
    plan = Plan()
    if health_summary is not None:
        plan.coarse_count = health_summary.coarse_count
    cleanup = _cleanup_by_account(health_summary)

    actions = _full_actions(samples, cleanup, names)
    actions += _backup_actions(health_summary)
    actions += _cleanup_actions(cleanup, already=_accounts_on_full_lines(actions))
    scan_lines, plan.accounts_without_scan = _scan_actions(scan_accounts)
    actions += scan_lines

    actions.sort(key=_rank)
    plan.actions = actions[:MAX_ACTIONS]
    return plan


def _cleanup_by_account(health_summary) -> dict:
    """정리 후보를 계정별로 모은다 `{계정id: {"kb", "count", "name"}}`.

    사용률 줄과 붙여 놓아야 "96% 인데 정리하면 800GB 빈다" 한 줄이 된다."""

    result = {}
    if health_summary is None:
        return result
    for item in health_summary.cleanable:
        bucket = result.setdefault(
            item.account_id, {"kb": 0, "count": 0, "name": item.account_name}
        )
        bucket["kb"] += item.reclaimable_kb
        bucket["count"] += 1
    return result


# -- 1. 스토리지가 찼다 ------------------------------------------------------
def _full_actions(samples, cleanup: dict, names: dict) -> List[Action]:
    """**계정이 아니라 스토리지 하나에 한 줄.** `_storage_key` 참고."""

    actions = []
    for bucket in _group_by_storage(samples, cleanup, names):
        members = sorted(bucket["members"], key=lambda item: (-item[2], item[0]))
        freeable = sum(item[2] for item in members)
        # 대표 계정은 **정리할 것이 가장 많은 쪽**이다. 창의 '계정 상세' 가
        # 그 계정을 연다 - 손댈 곳이 있는 계정이 먼저 나와야 한다.
        primary = members[0]
        actions.append(
            Action(
                kind=ACT_FULL,
                level=bucket["level"],
                account=primary[0],
                account_id=primary[1],
                pct=bucket["pct"],
                # 해법을 같은 줄에 붙인다.
                reclaimable_kb=freeable or None,
                count=sum(item[3] for item in members),
                shared=members,
                mount_point=bucket["mount_point"],
            )
        )
    return actions


def _group_by_storage(samples, cleanup: dict, names: dict) -> List[dict]:
    """급한 표본을 스토리지별로 묶는다 (넣은 순서를 지킨다)."""

    groups = {}
    for sample in samples:
        # 수집이 실패한 표본의 숫자는 옛 값이거나 비어 있다 - 그걸로 "지금
        # 찼다" 고 말하면 안 된다. 실패 자체는 홈 표의 '최근 수집' 칸이 말한다.
        if getattr(sample, "ok", True) is False:
            continue
        # `store.SampleRecord` 의 이름은 `byte_pct` 다. 예전에는 없는 이름
        # (`used_percent`)을 읽어서 이 줄이 운영에서 한 번도 뜨지 않았다.
        pct = getattr(sample, "byte_pct", None)
        level = _usage_level(pct)
        if level is None:
            continue
        account_id = getattr(sample, "account_id", "")
        key = _storage_key(sample)
        bucket = groups.get(key)
        if bucket is not None and abs(bucket["pct"] - pct) > SAME_STORAGE_PCT:
            # 같은 자리로 보이는데 숫자가 많이 다르면 묶지 않는다.
            key = ("account", account_id)
            bucket = groups.get(key)
        if bucket is None:
            bucket = groups.setdefault(
                key,
                {
                    "pct": pct, "level": level, "members": [],
                    "mount_point": getattr(sample, "mount_point", "") or "",
                },
            )
        # 같은 스토리지 안에서는 가장 높은 값을 그 스토리지의 값으로 본다.
        if pct > bucket["pct"]:
            bucket["pct"], bucket["level"] = pct, level
        relief = cleanup.get(account_id)
        bucket["members"].append((
            names.get(account_id, account_id), account_id,
            relief["kb"] if relief else 0, relief["count"] if relief else 0,
        ))
    return list(groups.values())


def _accounts_on_full_lines(actions) -> set:
    """사용률 줄에 이미 올라간 계정들 (묶인 계정 전부)."""

    return {
        member[1]
        for item in actions if item.kind == ACT_FULL
        for member in item.shared
    }


# -- 2. 백업이 안 된 과제 ----------------------------------------------------
def _backup_actions(health_summary) -> List[Action]:
    """용량 문제가 아니라 **잃을 위험**이다. 그래서 사용률과 같은 칸에 둔다."""

    if health_summary is None:
        return []
    risky_by_account = {}
    for item in health_summary.risky:
        bucket = risky_by_account.setdefault(
            item.account_id,
            {"count": 0, "kb": 0, "name": item.account_name, "path": item.label},
        )
        bucket["count"] += 1
        bucket["kb"] += item.backup_size_kb or item.run_size_kb or 0
    return [
        Action(
            kind=ACT_NO_BACKUP,
            level=LEVEL_HIGH,
            account=bucket["name"],
            account_id=account_id,
            size_kb=bucket["kb"],
            count=bucket["count"],
            path=bucket["path"],
        )
        for account_id, bucket in risky_by_account.items()
    ]


# -- 3. 정리 후보 (사용률 줄에 안 붙은 것만) ---------------------------------
def _cleanup_actions(cleanup: dict, already: set) -> List[Action]:
    """이미 사용률 줄에 붙은 계정을 또 내놓으면 같은 일이 두 줄이 되고,
    합계도 두 번 세인다."""

    return [
        Action(
            kind=ACT_CLEANUP,
            level=LEVEL_MEDIUM,
            account=bucket["name"],
            account_id=account_id,
            reclaimable_kb=bucket["kb"],
            count=bucket["count"],
        )
        for account_id, bucket in cleanup.items()
        if account_id not in already and bucket["kb"] >= CLEANUP_WORTH_KB
    ]


# -- 4. 스캔이 준 것들 ---------------------------------------------------------
def _scan_actions(scan_accounts) -> "tuple":
    """큰 파일과 급증. 스캔이 한 번도 안 끝난 계정은 따로 돌려준다
    `(줄들, 스캔 없는 계정 이름들)` - 빈 목록이 "문제 없음" 으로 읽히면 안 된다."""

    actions: List[Action] = []
    unscanned: List[str] = []
    for entry in scan_accounts:
        name = getattr(entry, "account_name", "")
        if getattr(entry, "last_completed_generation", None) is None:
            unscanned.append(name)
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
    return actions, unscanned


def _rank(action: Action):
    """급한 것부터, 같은 급함 안에서는 **효과가 큰 것**부터."""

    impact = max(
        action.reclaimable_kb or 0,
        action.size_kb or 0,
        action.delta_kb or 0,
    )
    return (action.level, -impact, action.account)
