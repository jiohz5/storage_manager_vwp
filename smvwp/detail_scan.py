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


def du_command(path: str) -> List[str]:
    """`du -sk` 명령 argv.

    스냅샷 디렉터리를 `--exclude`로 뺀다. 큐에서 빼는 것만으로는 부족한데,
    `du`는 주어진 디렉터리 아래를 스스로 전부 걸어 내려가므로 큐와 무관하게
    `.snapshot` 안까지 세기 때문이다. 즉 제외는 **명령 자체에** 걸어야 한다.

    `--exclude`는 경로 전체가 아니라 이름(basename)에 대해 모든 깊이에서
    맞춰진다. NetApp처럼 디렉터리마다 `.snapshot`이 붙는 파일시스템을
    감안한 것이다."""

    command = ["du", "-sk"]
    for name in sorted(SNAPSHOT_DIR_NAMES):
        command.append(f"--exclude={name}")
    command += ["--", path]
    return command


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
        proc = procio.run_utf8(prefix + du_command(path), timeout=timeout_seconds)
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
            bare = procio.run_utf8(du_command(path), timeout=timeout_seconds)
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


# 스냅샷/백업 산출물 디렉터리. 스캔에서 제외한다.
#
# 이것들은 파일시스템이 만들어 둔 과거 시점의 사본이라, 세면 같은 데이터를 두
# 번(스냅샷 세대만큼 여러 번) 세게 되어 계정 크기가 실제보다 몇 배로 부풀고
# "증가 경로"도 엉뚱하게 잡힌다. 게다가 대개 읽기 전용이라 사용자가 정리할 수
# 있는 대상도 아니다 - 즉 세어 봐야 부하만 늘고 판단에는 해롭다.
SNAPSHOT_DIR_NAMES = {".snapshot", ".zfs", ".ckpt"}


def is_snapshot_dir(name: str) -> bool:
    return name in SNAPSHOT_DIR_NAMES


@dataclass
class DuTreeOutcome:
    """`du -k` 한 번의 결과.

    `entries`는 `(경로, KB)` 목록이고 **출력된 것은 모두 완료된 값**이다.
    `du`는 깊이 우선으로 돌면서 디렉터리를 다 센 뒤에 그 줄을 찍기 때문에,
    중간에 죽어도 이미 나온 줄은 신뢰할 수 있다. 이 성질 덕분에 시간 초과가
    나도 그때까지의 작업을 버리지 않는다.
    """

    entries: List["tuple"] = None
    root_size_kb: Optional[int] = None
    completed: bool = False      # 루트까지 찍혔는가 (=서브트리 전체 완료)
    timed_out: bool = False
    partial: bool = False        # 권한 등으로 일부를 못 읽었는가
    error_message: Optional[str] = None

    def __post_init__(self):
        if self.entries is None:
            self.entries = []


def du_tree_command(path: str, max_depth: int) -> List[str]:
    """`du -k --max-depth=N` argv.

    **`-s`를 쓰지 않는 것이 이 설계의 핵심이다.** `du -sk`는 숫자 하나만
    돌려주므로, 시간이 오래 걸려 중간에 끊기면 얻는 것이 전혀 없고 하위로
    쪼개서 **같은 파일을 처음부터 다시** 걸어야 했다. 한 단계 쪼갤 때마다 그
    서브트리를 한 번 더 걷는 셈이었다.

    `-s`를 빼면 한 번 걷는 동안 **모든 하위 디렉터리 크기가 함께** 나온다.
    실측(같은 트리, warm cache): 개별 `du -sk` 6회가 131ms에 최상위 6개
    합계를 주는 동안, `du -k` 1회는 191ms에 283개 디렉터리를 준다.

    `--max-depth`로 **출력만** 제한한다. 크기 계산은 그대로 전체를 돌기 때문에
    루트 값은 정확하고, DB에 쌓이는 행 수만 통제된다 (DESIGN 1부 2-5
    "자체 비대화 방지").
    """

    command = ["du", "-k", f"--max-depth={max_depth}"]
    for name in sorted(SNAPSHOT_DIR_NAMES):
        command.append(f"--exclude={name}")
    command += ["--", path]
    return command


def _parse_du_tree(stdout: str) -> List["tuple"]:
    """`du -k` 출력을 `(경로, KB)` 목록으로. 깨진 줄은 조용히 건너뛴다."""

    entries = []
    for line in (stdout or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("	", 1)
        if len(parts) != 2:
            parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            size_kb = int(parts[0])
        except ValueError:
            continue
        entries.append((parts[1].strip(), size_kb))
    return entries


def run_du_tree(path: str, timeout_seconds: int, max_depth: int = 3) -> DuTreeOutcome:
    """서브트리 하나를 한 번의 `du -k`로 잰다.

    시간 초과여도 그때까지 출력된 디렉터리는 **완료된 값**이므로 살려서
    돌려준다 (`DuTreeOutcome.entries`). 예전에는 이 경우 15분이 통째로
    버려졌다."""

    prefix = build_priority_prefix()
    command = du_tree_command(path, max_depth)
    timed_out = False
    try:
        proc = procio.run_utf8(prefix + command, timeout=timeout_seconds)
        stdout, stderr, returncode = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        # 죽이기 전까지 찍힌 줄은 그대로 쓸 수 있다 (위 docstring 참고).
        timed_out = True
        stdout = _decode_stream(exc.stdout)
        stderr = _decode_stream(exc.stderr)
        returncode = None
    except FileNotFoundError:
        return DuTreeOutcome(error_message="du 명령을 찾을 수 없습니다")

    entries = _parse_du_tree(stdout)

    # 접두사(nice/ionice)가 못 도는 장비에서는 du가 실행조차 안 된다.
    # 결과가 하나도 없고 시간 초과도 아니면 접두사 없이 한 번 더 해 본다.
    if not entries and not timed_out and prefix and returncode not in (0, None):
        try:
            bare = procio.run_utf8(command, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            bare = None
            stdout = _decode_stream(exc.stdout)
            entries = _parse_du_tree(stdout)
        except FileNotFoundError:
            return DuTreeOutcome(error_message="du 명령을 찾을 수 없습니다")
        if bare is not None:
            entries = _parse_du_tree(bare.stdout)
            stderr, returncode = bare.stderr, bare.returncode
            if entries:
                global _priority_prefix_cache
                _priority_prefix_cache = []

    if not entries:
        message = (stderr or "").strip()
        if timed_out:
            return DuTreeOutcome(
                timed_out=True,
                error_message=f"{timeout_seconds}초 안에 첫 결과도 나오지 않았습니다",
            )
        if _is_permission_error(message):
            return DuTreeOutcome(
                error_message=f"읽기 권한이 없어 크기를 잴 수 없습니다: {message}"
            )
        return DuTreeOutcome(error_message=message or f"du exit={returncode}")

    # 루트 줄은 서브트리를 다 센 뒤에야 찍힌다 - 그게 있으면 완주한 것이다.
    root = _find_root_entry(entries, path)
    return DuTreeOutcome(
        entries=entries,
        root_size_kb=root,
        completed=root is not None and not timed_out,
        timed_out=timed_out,
        partial=bool(returncode) and _is_permission_error(stderr or ""),
        error_message=(stderr or "").strip() or None,
    )


def _decode_stream(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _find_root_entry(entries: List["tuple"], path: str) -> Optional[int]:
    """루트 경로의 크기. `du`가 인자를 그대로 되돌려주므로 문자열로 맞춘다."""

    normalized = path.rstrip("/")
    for entry_path, size_kb in reversed(entries):
        if entry_path.rstrip("/") == normalized:
            return size_kb
    return None


def list_immediate_subdirs(path: str, skip_snapshots: bool = True) -> List[str]:
    """분할 시 다음 큐에 넣을 바로 아래 자식 디렉터리 목록. 권한 오류 등은
    조용히 빈 리스트로 처리한다 (그 지점에서는 더 쪼갤 수 없다는 뜻).

    `skip_snapshots`면 `.snapshot` 같은 스냅샷 디렉터리를 뺀다."""

    try:
        with os.scandir(path) as entries:
            return sorted(
                entry.path
                for entry in entries
                if entry.is_dir(follow_symlinks=False)
                and not (skip_snapshots and is_snapshot_dir(entry.name))
            )
    except OSError:
        return []


def process_one_checkpoint(
    conn, checkpoint, timeout_seconds: int, max_depth: int = 3, generation: int = None
) -> str:
    """체크포인트 하나(서브트리 하나)를 처리하고 상태 문자열을 반환한다.

    예전에는 `du -sk`로 **숫자 하나**만 받았다. 그래서 시간 초과가 나면 그때까지
    쓴 시간이 통째로 버려지고, 하위로 쪼개서 같은 파일을 처음부터 다시 걸어야
    했다 - 한 단계 쪼갤 때마다 그 서브트리를 한 번 더 걷는 셈이었다.

    이제 `du -k` 한 번으로 서브트리 전체를 받는다. 시간 초과가 나도 **이미
    출력된 디렉터리는 완료된 값**이므로 저장하고, 아직 안 나온 자식만 다시
    큐에 넣는다. 버려지는 작업이 없다.
    """

    generation = generation if generation is not None else checkpoint["generation"]
    account_id = checkpoint["account_id"]
    path = checkpoint["path"]

    outcome = run_du_tree(path, timeout_seconds, max_depth=max_depth)

    if outcome.entries:
        scan_store.save_tree_entries(conn, account_id, generation, outcome.entries, path)

    if outcome.completed:
        scan_store.mark_done(
            conn,
            checkpoint["id"],
            size_kb=outcome.root_size_kb,
            error_message=outcome.error_message if outcome.partial else None,
        )
        return scan_store.STATUS_DONE

    if outcome.timed_out:
        # 아직 안 끝난 자식만 다시 큐에 넣는다. 이미 나온 것은 저장됐으므로
        # 다시 걷지 않는다 - 예전 방식과 갈리는 지점이다.
        measured = {entry_path.rstrip("/") for entry_path, _ in outcome.entries}
        remaining = [
            child for child in list_immediate_subdirs(path)
            if child.rstrip("/") not in measured
        ]
        if remaining:
            scan_store.mark_split(conn, checkpoint["id"])
            scan_store.insert_children(conn, checkpoint, remaining)
            return scan_store.STATUS_SPLIT
        # 자식은 다 쟀는데 루트 줄이 안 나온 경우 - 합계는 자식들로 낼 수 있다.
        if outcome.entries:
            scan_store.mark_done(conn, checkpoint["id"], size_kb=outcome.root_size_kb)
            return scan_store.STATUS_DONE
        scan_store.mark_error(
            conn, checkpoint["id"],
            f"{timeout_seconds}초 내에 끝나지 않았고 하위 디렉터리도 없습니다",
        )
        return scan_store.STATUS_ERROR

    scan_store.mark_error(conn, checkpoint["id"], outcome.error_message or "알 수 없는 오류")
    return scan_store.STATUS_ERROR
