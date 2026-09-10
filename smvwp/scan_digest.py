"""스캔 결과를 **한눈에 읽히는 몇 줄로** 줄인다 (Qt 없음).

## 왜 필요한가

지금까지 상세 스캔 화면은 표 셋을 세로로 쌓아 놓은 것이었다. 계정별 현황,
증가 경로, 큰 파일. 셋 다 같은 무게라 **결론이 어디에도 없었다.** 아침에
창을 연 사람은 표 셋을 읽고 스스로 요약을 만들어야 했고, 그러면 대부분
안 읽는다.

여기서 만드는 것은 그 요약이다. 표를 없애는 것이 아니라 **그 위에 답을
얹는 것**이다 - 근거는 아래 표에 그대로 있어야 한다.

## 무엇에 답하는가

아침에 사람이 실제로 묻는 것은 넷뿐이다.

1. 어젯밤 스캔이 잘 돌았나.
2. 전부 합쳐 얼마나 늘었나.
3. 어느 계정이 제일 늘었나.
4. 손댈 것이 있나.

## 여기서 지키는 것

**모르는 것은 숫자를 만들지 않는다.** 이전 스캔이 없으면 증가량은 None 이고,
화면은 그것을 '-' 로 보여 준다. 0 으로 채우면 "안 늘었다"가 되는데 그것은
거짓이다.

**증가량은 계정 총량의 차이로 낸다.** 경로별 증감을 더하면 `du` 가 부모와
자식을 함께 주므로 같은 바이트를 여러 번 세게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import large_files as large_files_module

# 살펴볼 것 목록에 실을 최대 줄 수.
#
# 다 실으면 목록이 표가 되고, 표가 되면 아까와 같은 문제로 돌아간다. 여기
# 있는 것은 "먼저 볼 것"이지 전부가 아니다.
MAX_FINDINGS = 8

# 계정 증가를 살펴볼 것으로 올릴 최소 크기.
#
# 작은 계정이 조금 늘어난 것까지 올리면 목록이 잡음으로 찬다. 사람이 손댈
# 값어치가 있는 크기여야 알림이 알림 구실을 한다.
GROWTH_NOTICE_KB = 50 * 1024 * 1024   # 50GB

FINDING_LARGE_FILE = "large_file"
FINDING_GROWTH = "growth"
FINDING_FAILED = "failed"
FINDING_PARTIAL = "partial"


@dataclass
class Finding:
    """살펴볼 것 한 줄. 화면이 문구를 만들 수 있게 재료만 담는다."""

    kind: str
    account: str
    path: str = ""
    size_kb: Optional[int] = None
    delta_kb: Optional[int] = None
    count: int = 0
    # 눈에 띄게 칠할지. 실패처럼 사람이 반드시 봐야 하는 것만 참이다.
    urgent: bool = False


@dataclass
class AccountGrowth:
    """계정 하나가 지난 스캔 이후 얼마나 늘었나."""

    account_id: str
    name: str
    total_kb: Optional[int] = None
    delta_kb: Optional[int] = None

    @property
    def measured(self) -> bool:
        return self.delta_kb is not None


@dataclass
class Digest:
    """아침에 읽을 요약 한 벌."""

    # 1. 잘 돌았나
    scanned_at: Optional[str] = None
    run_status: Optional[str] = None
    run_seconds: Optional[float] = None
    is_running: bool = False

    # 2. 얼마나 늘었나
    total_delta_kb: Optional[int] = None
    accounts_compared: int = 0
    accounts_total: int = 0

    # 3. 어느 계정이
    growth: List[AccountGrowth] = field(default_factory=list)

    # 4. 손댈 것
    findings: List[Finding] = field(default_factory=list)

    @property
    def biggest(self) -> Optional[AccountGrowth]:
        """가장 많이 늘어난 계정. 잰 것이 없으면 None."""

        grown = [item for item in self.growth if item.delta_kb]
        if not grown:
            return None
        return max(grown, key=lambda item: item.delta_kb)

    @property
    def urgent_count(self) -> int:
        return sum(1 for item in self.findings if item.urgent)


def _run_seconds(run) -> Optional[float]:
    if not run:
        return None
    started, ended = run.get("started_at"), run.get("ended_at")
    if not started or not ended:
        return None
    from datetime import datetime

    try:
        return (
            datetime.fromisoformat(str(ended)) - datetime.fromisoformat(str(started))
        ).total_seconds()
    except (TypeError, ValueError):
        return None


def _as_dict(run):
    """sqlite3.Row 든 dict 든 같은 방식으로 읽게 한다."""

    if run is None:
        return {}
    if isinstance(run, dict):
        return run
    try:
        return {key: run[key] for key in run.keys()}
    except (AttributeError, TypeError):  # pragma: no cover - 방어적 처리
        return {}


def build(snapshot) -> Digest:
    """`nightly_scan.StatusSnapshot` 을 요약으로 줄인다."""

    run = _as_dict(getattr(snapshot, "latest_run", None))
    digest = Digest(
        run_status=run.get("status"),
        run_seconds=_run_seconds(run),
        is_running=bool(getattr(snapshot, "is_running", False)),
    )

    accounts = list(getattr(snapshot, "accounts", []) or [])
    digest.accounts_total = len(accounts)

    total_delta = 0
    compared = 0
    for entry in accounts:
        growth = AccountGrowth(
            account_id=entry.account_id,
            name=entry.account_name,
            total_kb=entry.measured_kb,
        )
        previous = getattr(entry, "previous_measured_kb", None)
        if entry.measured_kb is not None and previous is not None:
            growth.delta_kb = entry.measured_kb - previous
            total_delta += growth.delta_kb
            compared += 1
        digest.growth.append(growth)

        if entry.current_scan_at and not digest.scanned_at:
            digest.scanned_at = entry.current_scan_at

    digest.accounts_compared = compared
    # 견준 계정이 하나도 없으면 합계도 없다. 0 으로 두면 "안 늘었다"가 되는데
    # 사실은 "견줄 것이 없다"이다.
    digest.total_delta_kb = total_delta if compared else None
    digest.growth.sort(key=lambda item: -(item.delta_kb or 0))
    digest.findings = _findings(accounts)
    return digest


def _findings(accounts) -> List[Finding]:
    """살펴볼 것을 모은다 - 급한 것부터.

    순서가 곧 사람이 보는 순서다. 실패를 아래에 두면 표 밑으로 밀려 안 보인다.
    """

    urgent: List[Finding] = []
    normal: List[Finding] = []

    for entry in accounts:
        name = entry.account_name

        if entry.failed_count:
            urgent.append(
                Finding(
                    kind=FINDING_FAILED, account=name,
                    count=entry.failed_count, urgent=True,
                )
            )
        if entry.partial_paths:
            # 권한이 없어 덜 세어진 것이다. 모르고 보면 "안 늘었네"로 잘못
            # 읽으므로 반드시 알려야 한다.
            urgent.append(
                Finding(
                    kind=FINDING_PARTIAL, account=name,
                    count=len(entry.partial_paths), urgent=True,
                )
            )

        for item in large_files_module.build(entry.large_files, entry.measured_kb):
            if not item.is_notable:
                continue
            normal.append(
                Finding(
                    kind=FINDING_LARGE_FILE, account=name, path=item.path,
                    size_kb=item.size_kb, delta_kb=item.delta_kb,
                )
            )

        previous = getattr(entry, "previous_measured_kb", None)
        if entry.measured_kb is not None and previous is not None:
            delta = entry.measured_kb - previous
            if delta >= GROWTH_NOTICE_KB:
                normal.append(
                    Finding(
                        kind=FINDING_GROWTH, account=name,
                        size_kb=entry.measured_kb, delta_kb=delta,
                    )
                )

    # 급한 것 안에서도 순서가 있다. **아예 못 잰 것**이 덜 세어진 것보다
    # 먼저다 - 앞쪽은 숫자가 아예 없는 것이고, 뒤쪽은 있는데 작은 것이다.
    # 계정 순서대로 두면 우연히 이름이 앞선 계정의 경고가 위로 온다.
    urgent.sort(key=lambda item: 0 if item.kind == FINDING_FAILED else 1)
    # 나머지는 큰 것부터.
    normal.sort(key=lambda item: -(item.delta_kb or item.size_kb or 0))
    return (urgent + normal)[:MAX_FINDINGS]
