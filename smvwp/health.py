"""프로젝트 계정이 건강한가 - 백업이 된 과제와 안 된 과제를 가른다 (Qt 없음).

## 무엇을 보는가

의뢰서 작업은 이 모양으로 남는다 (`workflow` 참고):

    <과제명>/LAYOUT/<NN_run_MMDD>/{BACKUP, CROSSCHECK, SIGNOFF, ...}

과제가 끝나면 `BACKUP` 아래 내용이 백업 계정으로 넘어간다. 그러면 프로젝트
계정 쪽은 정리할 수 있게 된다.

여기서 하는 일은 그 대조다. run 디렉터리마다 **연결된 백업 계정에 대응하는
것이 있는가**를 보고, 세 갈래로 가른다.

- 백업이 확인됨 - 프로젝트 쪽을 정리하면 자리가 빈다.
- 백업이 안 보임 - **이쪽이 더 급하다.** 지금 사라지면 복구할 곳이 없다.
- 아직 백업 단계 전 - `BACKUP` 디렉터리가 아직 없다. 정상이다.

## "지워도 된다"고 말하지 않는다

이것이 이 파일에서 가장 중요한 선이다. 우리가 대조하는 것은 **이름과 크기**지
내용이 아니다. 같은 이름의 디렉터리가 비슷한 크기로 백업 계정에 있다는 것은
강한 정황이지만 증거는 아니다.

"지워도 됩니다"라고 말했다가 그것이 틀리면, 이 도구가 만들 수 있는 가장 나쁜
사고가 된다. 그래서 상태 이름을 `backed_up`(백업이 확인됨)으로 두고, 화면과
보고서도 **정리 후보**까지만 말한다. 지우는 것은 언제나 사람이 확인하고 하는
일이다 (이 프로그램은 어떤 경우에도 파일을 지우지 않는다).

## 다시 뒤지지 않는다

야간 스캔이 이미 두 계정의 디렉터리 경로와 크기를 `baseline_results` 에 넣어
두었다. 대조는 그 기록끼리 한다 - 파일시스템에 추가 부하가 0 이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from . import workflow

# 백업 쪽이 프로젝트 쪽의 이 비율 이상이면 "확인됨"으로 본다.
#
# 똑같기를 요구하지 않는 이유: 복사 시점이 달라 몇 파일이 더 붙거나, 블록
# 단위 반올림으로 몇 KB 가 어긋나는 일이 흔하다. 그 정도로 "백업이 덜 됐다"고
# 하면 경고가 매일 떠서 아무도 안 보게 된다.
BACKED_UP_RATIO = 0.95

# 상태값.
NO_LINK = "no_link"              # 연결된 백업 계정이 없다 - 판정할 수 없다
NO_BACKUP_DIR = "no_backup_dir"  # 아직 BACKUP 단계 전 (정상)
MISSING = "missing"              # BACKUP 은 있는데 백업 계정에서 못 찾았다
PARTIAL = "partial"              # 찾았는데 크기가 많이 모자란다
BACKED_UP = "backed_up"          # 확인됨 - 정리 후보

# 사람이 먼저 봐야 하는 순서. 위험한 것이 위다.
STATUS_ORDER = (MISSING, PARTIAL, BACKED_UP, NO_BACKUP_DIR, NO_LINK)

# 위험으로 볼 상태들.
RISKY = (MISSING, PARTIAL)


@dataclass
class RunHealth:
    """과제 실행 디렉터리 하나의 건강 상태."""

    account_id: str
    account_name: str
    run_path: str
    label: str                      # `과제명 / 01_run_0908`
    run_size_kb: int = 0
    # 프로젝트 계정 안의 BACKUP 디렉터리 크기. 없으면 None.
    backup_size_kb: Optional[int] = None
    # 백업 계정에서 찾은 대응 경로와 그 크기.
    mirror_path: str = ""
    mirror_size_kb: Optional[int] = None
    status: str = NO_LINK

    @property
    def risky(self) -> bool:
        return self.status in RISKY

    @property
    def reclaimable_kb(self) -> int:
        """정리하면 빌 양.

        **백업이 확인된 것만** 센다. 확인 안 된 것을 더하면 "이만큼 비울 수
        있다"는 숫자가 실제보다 커지고, 그 숫자를 보고 계획을 세운다."""

        if self.status != BACKED_UP:
            return 0
        return self.backup_size_kb or 0

    @property
    def coverage(self) -> Optional[float]:
        """백업 쪽이 프로젝트 쪽의 몇 배인가. 잴 수 없으면 None."""

        if not self.backup_size_kb or self.mirror_size_kb is None:
            return None
        return self.mirror_size_kb / self.backup_size_kb


def _index_by_name(paths: Sequence[str]) -> Dict[str, List[str]]:
    """백업 계정의 경로를 **마지막 성분 이름**으로 묶는다.

    상대 경로를 그대로 맞추지 않는 이유: 백업 계정의 층 구조가 프로젝트 계정과
    같다는 보장이 없다. 현장마다 다르고, 규칙으로 못 박으면 구조가 조금만
    달라도 전부 '백업 안 됨'으로 뒤집힌다.

    run 디렉터리 이름(`01_run_0908`)은 날짜가 들어가 충분히 구별되므로 이것을
    열쇠로 쓴다."""

    index: Dict[str, List[str]] = {}
    for path in paths:
        name = workflow.basename(path)
        if not name:
            continue
        index.setdefault(name, []).append(workflow.normalize(path))
    return index


def _pick_mirror(candidates: Sequence[str], task: str) -> str:
    """같은 이름이 여럿이면 **과제명이 경로에 든 것**을 고른다.

    run 디렉터리 이름은 과제마다 다시 쓰일 수 있다(`01_run_0908` 이 두 과제에
    있을 수 있다). 과제명까지 맞으면 훨씬 확실하다."""

    if not candidates:
        return ""
    if task:
        for path in candidates:
            if f"/{task}/" in path + "/":
                return path
    return candidates[0]


def check_account(
    account_id: str,
    account_name: str,
    account_path: str,
    project_sizes: Dict[str, int],
    backup_sizes: Optional[Dict[str, int]],
) -> List[RunHealth]:
    """프로젝트 계정 하나를 대조한다.

    - `project_sizes` - 이 계정의 `{경로: KB}` (야간 스캔이 남긴 것).
    - `backup_sizes` - 연결된 백업 계정의 같은 것. **None 이면 연결이 없다**는
      뜻이고, 빈 사전이면 연결은 있는데 아직 스캔된 것이 없다는 뜻이다.
      둘은 다른 이야기라 갈라서 받는다.
    """

    results: List[RunHealth] = []
    mirror_index = _index_by_name(list(backup_sizes)) if backup_sizes else {}

    for path, size_kb in project_sizes.items():
        if not workflow.is_run_path(path):
            continue
        item = RunHealth(
            account_id=account_id,
            account_name=account_name,
            run_path=workflow.normalize(path),
            label=workflow.display_label(path, account_path),
            run_size_kb=size_kb,
        )

        backup_dir = workflow.backup_dir_for(path)
        item.backup_size_kb = project_sizes.get(backup_dir)

        if backup_sizes is None:
            item.status = NO_LINK
        elif item.backup_size_kb is None:
            # 아직 BACKUP 을 만들지 않았다. 진행 중인 과제의 정상 상태다.
            item.status = NO_BACKUP_DIR
        else:
            task = workflow.task_name_for(path, account_path)
            name = workflow.basename(path)
            item.mirror_path = _pick_mirror(mirror_index.get(name, []), task)
            if not item.mirror_path:
                item.status = MISSING
            else:
                item.mirror_size_kb = backup_sizes.get(item.mirror_path, 0)
                covered = item.coverage
                if covered is not None and covered >= BACKED_UP_RATIO:
                    item.status = BACKED_UP
                else:
                    item.status = PARTIAL
        results.append(item)

    results.sort(key=_sort_key)
    return results


def _sort_key(item: RunHealth):
    """위험한 것부터, 같은 상태 안에서는 큰 것부터."""

    try:
        rank = STATUS_ORDER.index(item.status)
    except ValueError:  # pragma: no cover - 방어적 처리
        rank = len(STATUS_ORDER)
    return (rank, -(item.backup_size_kb or item.run_size_kb or 0))


@dataclass
class HealthSummary:
    """여러 계정을 합친 요약."""

    items: List[RunHealth]

    @property
    def risky(self) -> List[RunHealth]:
        return [item for item in self.items if item.risky]

    @property
    def cleanable(self) -> List[RunHealth]:
        return [item for item in self.items if item.status == BACKED_UP]

    @property
    def reclaimable_kb(self) -> int:
        return sum(item.reclaimable_kb for item in self.items)

    def count(self, status: str) -> int:
        return sum(1 for item in self.items if item.status == status)


def summarize(per_account: Sequence[Sequence[RunHealth]]) -> HealthSummary:
    merged: List[RunHealth] = []
    for group in per_account:
        merged.extend(group)
    merged.sort(key=_sort_key)
    return HealthSummary(items=merged)


# ---------------------------------------------------------------------------
# 저장된 스캔 결과로 대조하기
# ---------------------------------------------------------------------------


def _sizes_for(conn, account_id: str) -> Optional[Dict[str, int]]:
    """이 계정의 마지막 완료 세대 `{경로: KB}`. 완료된 스캔이 없으면 None."""

    from . import scan_store

    state = scan_store.get_account_state(conn, account_id)
    generation = state.last_completed_generation
    if not generation:
        return None
    return {
        row["path"]: row["size_kb"]
        for row in scan_store.tree_entries(conn, account_id, generation)
    }


def check_all(data_dir, config) -> HealthSummary:
    """설정에 있는 프로젝트 계정들을 한 번에 대조한다.

    파일시스템을 다시 뒤지지 않는다 - 야간 스캔이 남긴 기록끼리 맞춘다.
    다만 계정마다 DB 조회가 한 번씩 있으므로, 화면에서는 작업 스레드로 부른다.
    """

    from . import config as config_module
    from . import scan_store

    conn = scan_store.connect(data_dir)
    try:
        cache: Dict[str, Optional[Dict[str, int]]] = {}

        def sizes(account_id: str):
            if account_id not in cache:
                cache[account_id] = _sizes_for(conn, account_id)
            return cache[account_id]

        groups = []
        for account in config_module.enabled_accounts(config):
            if account.kind != config_module.ACCOUNT_KIND_PROJECT:
                # 백업 계정에는 프로젝트의 사본이 들어온다. 거기서도 run
                # 디렉터리를 찾으면 같은 과제가 두 번 보고된다.
                continue
            project_sizes = sizes(account.account_id)
            if not project_sizes:
                continue

            backup_sizes = None
            if account.backup_account_id:
                # 연결은 있는데 아직 스캔이 없으면 **빈 사전**이다. 연결이
                # 없는 것(None)과 달라야 판정 문구가 갈린다.
                backup_sizes = sizes(account.backup_account_id) or {}

            groups.append(
                check_account(
                    account.account_id, account.name, account.path,
                    project_sizes, backup_sizes,
                )
            )
    finally:
        conn.close()
    return summarize(groups)
