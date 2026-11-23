"""경량 15분 주기 수집기: `df` byte/inode 사용률만 본다.

DESIGN.md 1부 3절 "00:00~24:00 (15분 간격) — 초경량 df/inode/quota 표본만,
트리 순회 없음"에 해당하는 부분만 phase 1에서 구현한다. 야간 상세 스캔은
이후 단계 몫이다 (DESIGN.md 2부 8절).

설계 메모:
- `df`는 계정 경로 자체의 사용량이 아니라 그 경로가 속한 파일시스템 전체
  사용량이라는 점을 DESIGN.md 1부 1절이 강조한다. 이 모듈이 반환하는 값도
  "파일시스템 사용률"이며, 계정별 개별 사용량이 아니다 — 호출자(GUI 등)가
  이 사실을 반드시 함께 표시해야 한다.
- 계정 하나의 df 호출이 느리거나 실패해도 나머지 계정 수집이 막히면 안 된다
  (DESIGN.md 1부 7절 "한 계정이 느려도 다른 계정 보고가 막히지 않는다") — 그래서
  예외를 계정 단위로 잡아 SampleRecord(ok=False)로 남기고 계속 진행한다.
- 모니터링 대상에는 절대 쓰지 않는다: 이 모듈은 `df` 조회만 하고 파일 시스템에
  아무것도 쓰지 않는다.
"""

from __future__ import annotations

import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from . import procio
from . import quota as quota_module
from . import tiers
from .config import Account
from .store import SampleRecord

logger = logging.getLogger(__name__)


class CollectorError(Exception):
    pass


class DfTimeout(CollectorError):
    """`df` 가 제한 시간 안에 끝나지 않았다 - 다른 방법으로 다시 묻지 않는다."""


@dataclass
class DfBytesResult:
    filesystem: str
    mount_point: str
    total_kb: int
    used_kb: int
    avail_kb: int
    pct: float


@dataclass
class DfInodeResult:
    filesystem: str
    mount_point: str
    total: Optional[int]
    used: Optional[int]
    avail: Optional[int]
    pct: Optional[float]


def _run_df(args: List[str], timeout: int) -> str:
    try:
        # 마운트 지점 이름에 비ASCII가 들어갈 수 있으므로 UTF-8을 명시한다
        # (smvwp.procio 참고).
        proc = procio.run_utf8(args, timeout=timeout)
    except FileNotFoundError as exc:
        raise CollectorError("df 명령을 찾을 수 없습니다") from exc
    except subprocess.TimeoutExpired as exc:
        raise DfTimeout(f"df 명령이 {timeout}초 내에 끝나지 않았습니다") from exc

    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip()
        raise CollectorError(f"df 명령 실패(exit={proc.returncode}): {message}")
    return proc.stdout


def _parse_df_line(output: str) -> List[str]:
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        raise CollectorError(f"df 출력을 해석할 수 없습니다: {output!r}")
    # 헤더 다음 줄을 데이터로 사용. maxsplit=5로 나누면 마지막 필드(Mounted on)에
    # 공백이 있어도 안전하다.
    parts = lines[1].split(None, 5)
    if len(parts) < 6:
        raise CollectorError(f"df 출력 필드 수가 부족합니다: {lines[1]!r}")
    return parts


def query_bytes(path: str, timeout: int = 10) -> DfBytesResult:
    """`df -Pk <path>`로 byte 사용률을 조회한다."""

    output = _run_df(["df", "-Pk", "--", path], timeout)
    filesystem, total_str, used_str, avail_str, capacity_str, mount_point = _parse_df_line(output)
    try:
        total_kb = int(total_str)
        used_kb = int(used_str)
        avail_kb = int(avail_str)
        pct = float(capacity_str.rstrip("%"))
    except ValueError as exc:
        raise CollectorError(f"df byte 출력 숫자 파싱 실패: {output!r}") from exc
    return DfBytesResult(
        filesystem=filesystem,
        mount_point=mount_point,
        total_kb=total_kb,
        used_kb=used_kb,
        avail_kb=avail_kb,
        pct=pct,
    )


def query_inodes(path: str, timeout: int = 10) -> DfInodeResult:
    """`df -Pi <path>`로 inode 사용률을 조회한다.

    일부 파일시스템(NFS 등)은 inode 사용률을 보고하지 않고 '-'로 표시한다 —
    이 경우 total/used/avail/pct를 모두 None으로 두고 호출자가 "확인불가"로
    표시하게 한다 (숫자를 지어내지 않는다는 원칙).
    """

    output = _run_df(["df", "-Pi", "--", path], timeout)
    filesystem, total_str, used_str, avail_str, capacity_str, mount_point = _parse_df_line(output)

    def _maybe_int(value: str) -> Optional[int]:
        try:
            return int(value)
        except ValueError:
            return None

    total = _maybe_int(total_str)
    used = _maybe_int(used_str)
    avail = _maybe_int(avail_str)
    try:
        pct: Optional[float] = float(capacity_str.rstrip("%"))
    except ValueError:
        pct = None

    return DfInodeResult(
        filesystem=filesystem,
        mount_point=mount_point,
        total=total,
        used=used,
        avail=avail,
        pct=pct,
    )


# 용량과 inode 를 `df` 한 번에 묻는 열. GNU coreutils 8.21 부터 있다 (RHEL 7 이
# 8.22, RHEL 8 이 8.30). 마운트 지점은 공백이 있을 수 있어 맨 끝에 둔다.
DF_OUTPUT_COLUMNS = "source,size,used,avail,pcent,itotal,iused,iavail,ipcent,target"


def _parse_combined(output: str) -> Optional[Tuple[DfBytesResult, DfInodeResult]]:
    """`df -k --output=...` 출력을 푼다. 모양이 다르면 None (예전 두 번 묻기로)."""

    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) != 2:
        return None
    parts = lines[1].split(None, 9)
    if len(parts) != 10:
        return None
    source, size, used, avail, pcent, itotal, iused, iavail, ipcent, target = parts

    def _maybe_int(value: str) -> Optional[int]:
        try:
            return int(value)
        except ValueError:
            return None

    def _maybe_pct(value: str) -> Optional[float]:
        try:
            return float(value.rstrip("%"))
        except ValueError:
            return None

    total_kb, used_kb, avail_kb = _maybe_int(size), _maybe_int(used), _maybe_int(avail)
    pct = _maybe_pct(pcent)
    if total_kb is None or used_kb is None or avail_kb is None or pct is None:
        return None
    return (
        DfBytesResult(filesystem=source, mount_point=target, total_kb=total_kb,
                      used_kb=used_kb, avail_kb=avail_kb, pct=pct),
        DfInodeResult(filesystem=source, mount_point=target, total=_maybe_int(itotal),
                      used=_maybe_int(iused), avail=_maybe_int(iavail),
                      pct=_maybe_pct(ipcent)),
    )


def query_usage(path: str, timeout: int = 10) -> Tuple[DfBytesResult, DfInodeResult]:
    """용량과 inode 를 `df` 한 번으로 묻는다.

    예전에는 계정마다 `df -Pk` 와 `df -Pi` 두 번이었다. `--output` 이 없는 `df`
    (GNU 가 아니거나 아주 오래된 것)나 출력 모양이 다르면 예전 두 번으로 묻는다.
    제한 시간을 넘긴 것은 다시 묻지 않는다 - 파일서버가 답하지 않는데 두 번
    기다리면 수집이 그만큼 늦는다."""

    try:
        output = _run_df(["df", "-k", f"--output={DF_OUTPUT_COLUMNS}", "--", path], timeout)
    except DfTimeout:
        raise
    except CollectorError:
        output = None
    if output is not None:
        parsed = _parse_combined(output)
        if parsed is not None:
            return parsed
    return query_bytes(path, timeout=timeout), query_inodes(path, timeout=timeout)


def collect_account(
    account: Account,
    df_timeout_seconds: int = 10,
    quota_command: Optional[List[str]] = None,
) -> SampleRecord:
    """계정 하나에 대해 byte+inode(+선택적 quota) 표본을 수집한다. 실패해도
    예외를 던지지 않고 ok=False인 SampleRecord를 반환한다 (호출자가 다른 계정을
    계속 수집할 수 있도록)."""

    collected_at = datetime.now(timezone.utc).isoformat()
    try:
        byte_result, inode_result = query_usage(account.path, timeout=df_timeout_seconds)
    except CollectorError as exc:
        return SampleRecord(
            account_id=account.account_id,
            collected_at=collected_at,
            ok=False,
            error_message=str(exc),
        )

    byte_tier = tiers.classify(byte_result.pct)
    inode_tier = tiers.classify(inode_result.pct)
    overall_tier = tiers.worse(byte_tier, inode_tier)

    # quota는 선택 기능이다. 조회에 실패해도 df 결과는 그대로 살린다 - quota
    # wrapper 하나 때문에 용량 모니터링 전체가 멈추면 안 된다.
    quota_result = None
    if quota_module.is_configured(quota_command):
        quota_result = quota_module.query(
            quota_command, account.name, account.path, timeout_seconds=df_timeout_seconds
        )
        if quota_result.ok:
            overall_tier = tiers.worse(overall_tier, quota_module.tier_for(quota_result))

    return SampleRecord(
        account_id=account.account_id,
        collected_at=collected_at,
        ok=True,
        filesystem=byte_result.filesystem,
        mount_point=byte_result.mount_point,
        total_kb=byte_result.total_kb,
        used_kb=byte_result.used_kb,
        avail_kb=byte_result.avail_kb,
        byte_pct=byte_result.pct,
        byte_tier=byte_tier,
        inode_total=inode_result.total,
        inode_used=inode_result.used,
        inode_avail=inode_result.avail,
        inode_pct=inode_result.pct,
        inode_tier=inode_tier,
        overall_tier=overall_tier,
        quota_used_kb=quota_result.used_kb if quota_result and quota_result.ok else None,
        quota_limit_kb=quota_result.limit_kb if quota_result and quota_result.ok else None,
        quota_soft_limit_kb=quota_result.soft_limit_kb if quota_result and quota_result.ok else None,
        quota_pct=quota_result.pct if quota_result and quota_result.ok else None,
        quota_tier=quota_module.tier_for(quota_result) if quota_result else tiers.UNKNOWN,
    )


# 한꺼번에 `df` 를 묻는 계정 수.
#
# 예전에는 계정을 하나씩 돌았다 - 계정마다 `df` 두 번(용량·inode)이라 계정 100개면
# 200번을 줄 서서 불렀다. 더 나쁜 것은 파일서버가 답하지 않을 때다: 계정마다 제한
# 시간(기본 10초)을 통째로 기다려 100개면 17분, 수집 간격(15분)을 넘겨 다음 수집과
# 겹쳤다. 여덟씩 물으면 그것이 2분 남짓이다.
#
# `df` 는 자식 프로세스를 기다리는 일이라 스레드로 충분하다 (GIL 을 놓는다). 여덟은
# 장비에 부담이 없는 수다 - `df` 하나는 statfs 한 번이다.
COLLECT_WORKERS = 8


def collect_all(
    accounts: List[Account],
    df_timeout_seconds: int = 10,
    quota_command: Optional[List[str]] = None,
    on_record=None,
) -> List[SampleRecord]:
    """모든 계정의 표본을 **계정 순서대로** 돌려준다 (묻는 것은 여럿이 함께).

    `on_record(record)` 를 주면 계정 하나의 `df` 가 끝나는 대로 (끝난 순서로) 부른다 -
    창이 수집 한 바퀴(저장·알림·예측·홈 계산)를 다 기다리지 않고 그 계정 줄부터 바로
    고친다. **부르는 것은 이 함수를 부른 스레드다** (`df` 를 묻는 일꾼 스레드가 아니다) -
    창의 신호는 다른 작업자처럼 수집 스레드 하나에서만 나간다 (`scheduler.emit_safely`).
    거기서 난 예외는 수집을 막지 않는다."""

    def one(account: Account) -> SampleRecord:
        return collect_account(
            account, df_timeout_seconds=df_timeout_seconds, quota_command=quota_command
        )

    def tell(record: SampleRecord) -> None:
        if on_record is None:
            return
        try:
            on_record(record)
        except Exception:  # 화면 쪽 사정이 수집을 막으면 안 된다
            logger.exception("계정 하나 끝남 알리기 실패 (수집은 계속)")

    accounts = list(accounts)
    if len(accounts) < 2:
        records = []
        for account in accounts:
            records.append(one(account))
            tell(records[-1])
        return records
    with ThreadPoolExecutor(
        max_workers=min(COLLECT_WORKERS, len(accounts)), thread_name_prefix="smvwp-df"
    ) as pool:
        futures = [pool.submit(one, account) for account in accounts]
        for future in as_completed(futures):
            tell(future.result())
        return [future.result() for future in futures]
