"""경량 15분 주기 수집기: `df` byte/inode 사용률만 본다.

CONCEPT.md 3절 "00:00~24:00 (15분 간격) — 초경량 df/inode/quota 표본만,
du/find 없음"에 해당하는 부분만 phase 1에서 구현한다. 야간 상세 스캔(`du`,
`find`)은 이후 단계 몫이다 (REBUILD_CONCEPT.md 8절).

설계 메모:
- `df`는 계정 경로 자체의 사용량이 아니라 그 경로가 속한 파일시스템 전체
  사용량이라는 점을 CONCEPT.md 1절이 강조한다. 이 모듈이 반환하는 값도
  "파일시스템 사용률"이며, 계정별 개별 사용량이 아니다 — 호출자(GUI 등)가
  이 사실을 반드시 함께 표시해야 한다.
- 계정 하나의 df 호출이 느리거나 실패해도 나머지 계정 수집이 막히면 안 된다
  (CONCEPT.md 7절 "한 계정이 느려도 다른 계정 보고가 막히지 않는다") — 그래서
  예외를 계정 단위로 잡아 SampleRecord(ok=False)로 남기고 계속 진행한다.
- 모니터링 대상에는 절대 쓰지 않는다: 이 모듈은 `df` 조회만 하고 파일 시스템에
  아무것도 쓰지 않는다.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from . import procio
from . import quota as quota_module
from . import tiers
from .config import Account
from .store import SampleRecord


class CollectorError(Exception):
    pass


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
        raise CollectorError(f"df 명령이 {timeout}초 내에 끝나지 않았습니다") from exc

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
        byte_result = query_bytes(account.path, timeout=df_timeout_seconds)
        inode_result = query_inodes(account.path, timeout=df_timeout_seconds)
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


def collect_all(
    accounts: List[Account],
    df_timeout_seconds: int = 10,
    quota_command: Optional[List[str]] = None,
) -> List[SampleRecord]:
    return [
        collect_account(account, df_timeout_seconds=df_timeout_seconds, quota_command=quota_command)
        for account in accounts
    ]
