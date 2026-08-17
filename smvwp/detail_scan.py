"""`du` 기반 기준선(baseline) 스캔 엔진 - 디렉터리 단위 체크포인트, 시간 초과
시 하위 분할, 최선 노력 nice/ionice.

DESIGN.md 1부 7절의 안전장치를 그대로 따른다:
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
    # 일부 하위 디렉터리를 못 읽어 실제보다 작게 측정된 값인지. 관리자가 아닌
    # 사용자가 남의 프로젝트 계정을 볼 때는 이쪽이 오히려 일반적이다.
    partial: bool = False


_priority_prefix_cache: Optional[List[str]] = None

# 우선순위 낮추기를 아예 끄고 싶을 때 쓰는 환경변수 (반입 장비에서 원인을
# 가르는 데 쓴다 - 이 값을 주고 되면 원인은 nice/ionice 쪽이다).
DISABLE_PRIORITY_ENV = "STORAGE_MANAGER_NO_NICE"


def _prefix_works(prefix: List[str]) -> bool:
    """접두사를 실제로 한 번 실행해 본다.

    `shutil.which`는 파일이 있는지만 본다. 그런데 `ionice -c3`은 커널/정책에
    따라 **설치돼 있어도 권한 오류로 실패**할 수 있고, 그러면 접두사가 붙은
    `du`가 통째로 exit 1로 죽는다 (du는 실행조차 안 된다). 스캔이 시작하자마자
    전부 실패하는 모습이 되므로, 있는지가 아니라 되는지를 확인해야 한다."""

    if not prefix:
        return True
    try:
        proc = procio.run_utf8(prefix + ["true"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def build_priority_prefix(force_recheck: bool = False) -> List[str]:
    """가능하면 `nice`/`ionice`로 우선순위를 낮춰 실행한다. 못 쓰면 빈 리스트를
    반환해 그냥 `du`/`find`를 직접 실행한다 - 있으면 좋고 없어도 동작은 해야
    한다 (하드 상한이 아니라 최선 노력).

    ionice가 실패하는 장비에서도 스캔은 돌아야 하므로, 둘을 한 벌로 검사하지
    않고 **하나씩 떼어 내며** 되는 조합을 찾는다."""

    global _priority_prefix_cache
    if _priority_prefix_cache is not None and not force_recheck:
        return _priority_prefix_cache

    if os.environ.get(DISABLE_PRIORITY_ENV):
        _priority_prefix_cache = []
        return _priority_prefix_cache

    ionice = ["ionice", "-c3"] if shutil.which("ionice") else []
    nice = ["nice", "-n", "19"] if shutil.which("nice") else []

    # 둘 다 -> ionice만 빼고 -> 둘 다 빼고 순으로 물러난다.
    for candidate in (ionice + nice, nice, []):
        if _prefix_works(candidate):
            _priority_prefix_cache = candidate
            return _priority_prefix_cache

    _priority_prefix_cache = []
    return _priority_prefix_cache


def reset_priority_prefix() -> None:
    """캐시를 비운다 (테스트/재검사용)."""

    global _priority_prefix_cache
    _priority_prefix_cache = None


def _parse_du_total(stdout: str) -> Optional[int]:
    """`du -sk` 출력의 마지막 줄에서 총계(KB)를 뽑는다."""

    text = (stdout or "").strip()
    if not text:
        return None
    parts = text.splitlines()[-1].split(None, 1)
    if not parts:
        return None
    try:
        return int(parts[0])
    except ValueError:
        return None


def _is_permission_error(stderr: str) -> bool:
    """읽기 권한이 없어서 난 오류인지.

    권한 문제는 '고장'이 아니라 '이 사용자로는 볼 수 없는 영역'이라는 뜻이라,
    사용자에게 다르게 안내해야 한다 (관리자에게 권한을 요청하거나 대상에서
    빼면 되는 일이지, 앱이나 설정이 잘못된 것이 아니다)."""

    lowered = (stderr or "").lower()
    return "permission denied" in lowered or "허가 거부" in (stderr or "")


def run_du(path: str, timeout_seconds: int) -> DuOutcome:
    """`du -sk <path>` 실행. 성공하면 size_kb, 시간 초과면 timed_out=True."""

    prefix = build_priority_prefix()
    try:
        # du 출력에는 파일 경로가 들어간다 - 비ASCII 경로가 로케일 인코딩으로
        # 깨지지 않도록 UTF-8을 명시한다 (smvwp.procio 참고).
        proc = procio.run_utf8(prefix + ["du", "-sk", "--", path], timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return DuOutcome(ok=False, timed_out=True)
    except FileNotFoundError:
        return DuOutcome(ok=False, error_message="du 명령을 찾을 수 없습니다")

    size_kb = _parse_du_total(proc.stdout)

    # 접두사(nice/ionice)가 붙은 실행만 실패했을 수 있다. 접두사 자체가 죽으면
    # du는 실행조차 안 되어 stdout이 비고 exit 1만 남는데, 그러면 스캔이
    # 시작하자마자 전부 실패한 것처럼 보인다. 한 번은 접두사 없이 다시 해 보고,
    # 그때 되면 접두사를 이번 실행 내내 쓰지 않는다.
    if size_kb is None and prefix and proc.returncode != 0:
        try:
            bare = procio.run_utf8(["du", "-sk", "--", path], timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            return DuOutcome(ok=False, timed_out=True)
        except FileNotFoundError:
            return DuOutcome(ok=False, error_message="du 명령을 찾을 수 없습니다")
        if _parse_du_total(bare.stdout) is not None:
            global _priority_prefix_cache
            _priority_prefix_cache = []
            proc = bare
            size_kb = _parse_du_total(bare.stdout)
        else:
            # 접두사 탓이 아니었다. 원인 판단에 쓸 메시지는 접두사 없는 쪽이
            # 깨끗하므로 그것을 남긴다.
            proc = bare

    if size_kb is None:
        stderr = (proc.stderr or "").strip()
        if _is_permission_error(stderr):
            return DuOutcome(
                ok=False,
                error_message=f"읽기 권한이 없어 크기를 잴 수 없습니다: {stderr}",
            )
        message = stderr or (proc.stdout or "").strip() or f"du exit={proc.returncode}"
        return DuOutcome(ok=False, error_message=message)

    # du는 읽을 수 없는 하위 디렉터리를 만나면 그것만 stderr로 알리고 나머지는
    # 계속 합산한 뒤 exit 1로 끝낸다. 여기서 결과를 통째로 버리면, 관리자가
    # 아닌 사용자가 남의 프로젝트 계정을 볼 때 사실상 모든 계정이 실패로
    # 기록되어 기준선이 영영 만들어지지 않는다. stdout에 총계가 있으면 부분
    # 결과로 받아들이고 `partial`로 표시한다 (activity_scan의 find 처리와
    # 같은 원칙).
    if proc.returncode != 0:
        return DuOutcome(
            ok=True,
            size_kb=size_kb,
            partial=True,
            error_message=(proc.stderr or "").strip() or f"du exit={proc.returncode}",
        )
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
        # 부분 측정이면 크기는 살리되 사유를 함께 남긴다 - 값이 실제보다 작다는
        # 사실을 사용자가 알아야 증가량 해석을 그르치지 않는다.
        scan_store.mark_done(
            conn,
            checkpoint["id"],
            size_kb=outcome.size_kb,
            error_message=outcome.error_message if outcome.partial else None,
        )
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
