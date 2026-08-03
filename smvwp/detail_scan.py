"""`du` 기반 기준선(baseline) 스캔 엔진 - 디렉터리 단위 체크포인트, 시간 초과
시 하위 분할, 최선 노력 nice/ionice.

CONCEPT.md 7절의 안전장치를 그대로 따른다:
- 디렉터리 단위 타임아웃을 넘기면 그 디렉터리를 포기하지 않고, 바로 아래
  자식 디렉터리들로 쪼개 다시 큐에 넣는다 (`_split_timed_out_directory`).
- `nice`/`ionice`는 있으면 쓰고 없으면 그냥 진행한다 - 우선순위 조정일 뿐
  처리량의 절대 상한이 아니라는 점을 문서에도, 여기 코드에도 남긴다
  (`build_priority_prefix`).
- 이 모듈은 절대 쓰지 않는다 - `du`만 호출한다. 모니터링 대상에 대한 읽기
  전용 불변식은 여기서도 깨지지 않는다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from . import procio, scan_store


class DetailScanError(Exception):
    pass


@dataclass
class DuOutcome:
    ok: bool
    size_kb: Optional[int] = None
    timed_out: bool = False
    error_message: Optional[str] = None


_priority_prefix_cache: Optional[List[str]] = None


def build_priority_prefix(force_recheck: bool = False) -> List[str]:
    """가능하면 `nice`/`ionice`로 우선순위를 낮춰 실행한다. 둘 다 없으면 빈
    리스트를 반환해 그냥 `du`/`find`를 직접 실행한다 - 있으면 좋고 없어도
    동작은 해야 한다 (하드 상한이 아니라 최선 노력)."""

    global _priority_prefix_cache
    if _priority_prefix_cache is not None and not force_recheck:
        return _priority_prefix_cache

    prefix: List[str] = []
    if shutil.which("ionice"):
        prefix += ["ionice", "-c3"]
    if shutil.which("nice"):
        prefix += ["nice", "-n", "19"]
    _priority_prefix_cache = prefix
    return prefix


def run_du(path: str, timeout_seconds: int) -> DuOutcome:
    """`du -sk <path>` 실행. 성공하면 size_kb, 시간 초과면 timed_out=True."""

    command = build_priority_prefix() + ["du", "-sk", "--", path]
    try:
        # du 출력에는 파일 경로가 들어간다 - 비ASCII 경로가 로케일 인코딩으로
        # 깨지지 않도록 UTF-8을 명시한다 (smvwp.procio 참고).
        proc = procio.run_utf8(command, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return DuOutcome(ok=False, timed_out=True)
    except FileNotFoundError:
        return DuOutcome(ok=False, error_message="du 명령을 찾을 수 없습니다")

    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip() or f"du exit={proc.returncode}"
        return DuOutcome(ok=False, error_message=message)

    line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout.strip() else ""
    parts = line.split(None, 1)
    if not parts:
        return DuOutcome(ok=False, error_message=f"du 출력을 해석할 수 없습니다: {proc.stdout!r}")
    try:
        size_kb = int(parts[0])
    except ValueError:
        return DuOutcome(ok=False, error_message=f"du 출력 숫자 파싱 실패: {line!r}")
    return DuOutcome(ok=True, size_kb=size_kb)


def list_immediate_subdirs(path: str) -> List[str]:
    """분할 시 다음 큐에 넣을 바로 아래 자식 디렉터리 목록. 권한 오류 등은
    조용히 빈 리스트로 처리한다 (그 지점에서는 더 쪼갤 수 없다는 뜻)."""

    try:
        with os.scandir(path) as entries:
            return sorted(
                entry.path for entry in entries if entry.is_dir(follow_symlinks=False)
            )
    except OSError:
        return []


def process_one_checkpoint(conn, checkpoint, timeout_seconds: int) -> str:
    """체크포인트 하나(디렉터리 하나)를 처리하고 결과 상태 문자열을 반환한다
    ('done' | 'split' | 'error') - 호출자(오케스트레이터)의 로깅/판단용."""

    outcome = run_du(checkpoint["path"], timeout_seconds)
    if outcome.ok:
        scan_store.mark_done(conn, checkpoint["id"], size_kb=outcome.size_kb)
        return scan_store.STATUS_DONE

    if outcome.timed_out:
        children = list_immediate_subdirs(checkpoint["path"])
        if children:
            scan_store.mark_split(conn, checkpoint["id"])
            scan_store.insert_children(conn, checkpoint, children)
            return scan_store.STATUS_SPLIT
        scan_store.mark_error(
            conn, checkpoint["id"], f"{timeout_seconds}초 내에 끝나지 않았고 하위 디렉터리도 없습니다"
        )
        return scan_store.STATUS_ERROR

    scan_store.mark_error(conn, checkpoint["id"], outcome.error_message or "알 수 없는 오류")
    return scan_store.STATUS_ERROR
