"""데이터 디렉터리 경로 결정 및 쓰기 안전성 점검.

읽기 전용 원칙(DESIGN.md 1부 1, 5절)의 핵심: 모니터링 대상 계정 경로에는 절대
쓰지 않고, 오직 사용자가 명시적으로 지정한 별도의 데이터 디렉터리에만 쓴다.
이 모듈이 그 "별도 디렉터리"를 어떻게 찾고 기억하는지를 담당한다.

우선순위:
1. 함수 인자로 명시된 경로 (예: `--data-dir` CLI 옵션)
2. `STORAGE_MANAGER_DATA_DIR` 환경 변수
3. 홈 디렉터리의 작은 포인터 파일 (`~/.storage_manager_vwp/data_dir`) —
   최초 실행 시 GUI에서 지정한 경로를 기억해 두는 용도.
4. 위 셋 다 없으면 None을 반환하고, 호출자가 최초 실행 안내를 하도록 한다.

포인터 파일을 배포 폴더가 아니라 홈 디렉터리에 두는 이유: 반입된 배포
디렉터리 자체는 배포 절차상 다시 덮어써질 수 있어 "매번 잊혀지지 않는 기억
장소"로 적합하지 않다.
"""

from __future__ import annotations

import os
import posixpath
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional


class DataDirError(Exception):
    """데이터 디렉터리를 확보/검증하지 못했을 때 발생."""


def pointer_dir(home: Optional[Path] = None) -> Path:
    home = home if home is not None else Path.home()
    return home / ".storage_manager_vwp"


def pointer_file(home: Optional[Path] = None) -> Path:
    return pointer_dir(home) / "data_dir"


def resolve_data_dir(
    explicit: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Optional[Path]:
    """데이터 디렉터리를 우선순위에 따라 찾는다. 못 찾으면 None."""

    environ = environ if environ is not None else os.environ

    if explicit:
        return Path(explicit).expanduser().resolve()

    env_value = environ.get("STORAGE_MANAGER_DATA_DIR")
    if env_value:
        return Path(env_value).expanduser().resolve()

    candidate = pointer_file(home)
    if candidate.exists():
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text:
            return Path(text).expanduser()

    return None


def remember_data_dir(data_dir: Path, home: Optional[Path] = None) -> Path:
    """최초 지정한 데이터 디렉터리를 포인터 파일에 기억해 둔다."""

    target_dir = pointer_dir(home)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        pointer_file(home).write_text(str(data_dir), encoding="utf-8")
    except OSError as exc:
        raise DataDirError(f"포인터 파일을 쓸 수 없습니다: {target_dir}: {exc}") from exc
    return data_dir


def ensure_writable(data_dir: Path) -> None:
    """데이터 디렉터리가 생성 가능하고 실제로 쓰기 가능한지 확인한다.

    임시 파일을 하나 만들었다가 바로 지우는 방식으로 실제 쓰기 권한을 검증한다
    (디렉터리 존재만으로는 권한을 확신할 수 없기 때문).
    """

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DataDirError(f"데이터 디렉터리를 만들 수 없습니다: {data_dir}: {exc}") from exc

    probe = data_dir / ".write_probe.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise DataDirError(f"데이터 디렉터리에 쓸 수 없습니다: {data_dir}: {exc}") from exc


@dataclass
class LocationInfo:
    """데이터 경로 후보 하나에 대한 판단 재료."""

    path: Path
    exists: bool
    writable: bool
    free_bytes: Optional[int]
    total_bytes: Optional[int]
    error: Optional[str] = None

    @property
    def usable(self) -> bool:
        return self.writable and self.error is None


def describe_location(path: Path) -> LocationInfo:
    """경로의 여유 공간과 쓰기 가능 여부를 조사한다 (실제로 쓰지는 않는다).

    아직 없는 경로면 만들 수 있는지를 가장 가까운 상위 디렉터리로 판단한다 -
    사용자가 `~/storage-manager-data`처럼 아직 없는 경로를 고르는 것이 정상
    흐름이기 때문."""

    probe_target = path
    while not probe_target.exists() and probe_target != probe_target.parent:
        probe_target = probe_target.parent

    free_bytes = total_bytes = None
    error = None
    try:
        usage = shutil.disk_usage(str(probe_target))
        free_bytes, total_bytes = usage.free, usage.total
    except OSError as exc:
        error = str(exc)

    writable = os.access(str(probe_target), os.W_OK) if probe_target.exists() else False

    return LocationInfo(
        path=path,
        exists=path.exists(),
        writable=writable,
        free_bytes=free_bytes,
        total_bytes=total_bytes,
        error=error,
    )


def suggest_data_dirs(home: Optional[Path] = None) -> List[LocationInfo]:
    """데이터 경로 후보를 여유 공간이 많은 순으로 제안한다.

    폐쇄망에서 관리자가 아니면 쓸 수 있는 곳이 몇 군데 없다. 시스템 경로를
    추측해 늘어놓기보다, 확실히 사용자 것인 위치만 제시하고 각각의 여유
    공간을 보여줘서 **사용자가 판단하게** 한다. 모니터링 대상 계정 내부는
    읽기 전용 원칙상 후보가 될 수 없으므로 아예 제안하지 않는다.
    """

    home = home if home is not None else Path.home()
    candidates = [home / "storage-manager-data"]

    # 환경변수로 이미 정해 뒀다면 그것을 첫 후보로 올린다.
    env_value = os.environ.get("STORAGE_MANAGER_DATA_DIR")
    if env_value:
        candidates.insert(0, Path(env_value).expanduser())

    seen = set()
    results: List[LocationInfo] = []
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        results.append(describe_location(candidate))
    return results


def format_bytes(size_bytes: Optional[int]) -> str:
    if size_bytes is None:
        return "-"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024 or unit == "PB":
            return f"{int(value):,} B" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} PB"  # pragma: no cover


def assert_not_inside_monitored_paths(data_dir: Path, monitored_paths) -> None:
    """데이터 디렉터리가 모니터링 대상 계정 경로 내부(또는 그 자체)가 아님을
    확인한다. 읽기 전용 불변식을 설정 단계에서부터 강제하기 위한 안전망이다.
    """

    resolved_data_dir = data_dir.resolve()
    for raw_path in monitored_paths:
        try:
            monitored = Path(raw_path).resolve()
        except OSError:
            continue
        if resolved_data_dir == monitored or resolved_data_dir.is_relative_to(monitored):
            raise DataDirError(
                f"데이터 디렉터리({data_dir})가 모니터링 대상 경로({raw_path}) 내부에 "
                "있습니다. 반드시 분리된 경로를 사용하세요."
            )
        if monitored.is_relative_to(resolved_data_dir):
            raise DataDirError(
                f"모니터링 대상 경로({raw_path})가 데이터 디렉터리({data_dir}) 내부에 "
                "있습니다. 반드시 분리된 경로를 사용하세요."
            )


# -- 네트워크 파일시스템 감지 ----------------------------------------------
#
# ## 왜 이걸 봐야 하는가
#
# SQLite의 WAL 모드는 **네트워크 파일시스템에서 동작하지 않는다.** WAL은 여러
# 프로세스가 작은 공유 메모리(`-shm`)를 함께 보는 것을 전제로 하는데, 호스트가
# 다르면 메모리를 공유할 수 없기 때문이다 (SQLite 공식 문서). 게다가 NFS의
# `fcntl` 권고 잠금은 구현마다 어긋나기로 유명하다.
#
# 그 결과는 느려지는 것이 아니라 **DB가 깨지는 것**이고, 깨지면 그동안 쌓은
# 이력이 통째로 날아간다. 그래서 이 프로그램은 데이터 디렉터리가 네트워크
# 파일시스템 위에 있으면 WAL을 쓰지 않고 물러선다.
#
# ## 왜 마운트 지점을 직접 찾는가
#
# `statvfs`는 파일시스템 종류 이름을 주지 않는다. `df -T`를 부르면 외부 명령이
# 하나 더 늘고 로케일에 따라 출력이 달라진다. `/proc/mounts`는 커널이 주는
# 원본이라 가장 안정적이다 (`loadstat`이 `top` 대신 `/proc`을 읽는 것과 같은
# 이유다).

# 네트워크 파일시스템 종류. 여기 없는 종류는 로컬로 본다 - 모르는 것을
# 네트워크라고 단정해 WAL을 끄면, 멀쩡한 로컬 환경에서 이유 없이 느려진다.
NETWORK_FILESYSTEMS = frozenset({
    "nfs", "nfs4", "cifs", "smbfs", "smb3", "afs", "9p",
    "fuse.sshfs", "glusterfs", "lustre", "beegfs",
})

PROC_MOUNTS = Path("/proc/mounts")


def _mount_entries(source: Optional[Path] = None) -> List["tuple"]:
    """`/proc/mounts`에서 (마운트 지점, 종류) 목록. 못 읽으면 빈 목록."""

    source = source or PROC_MOUNTS
    try:
        raw = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    entries = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        # /proc/mounts는 공백 등을 8진 이스케이프로 적는다 (\040 = 공백).
        mount_point = parts[1].replace("\\040", " ")
        entries.append((mount_point, parts[2]))
    return entries


def _mount_comparable(path: Path) -> str:
    """마운트 지점과 견줄 수 있는 표기로 바꾼다.

    **심링크는 반드시 풀어야 한다** - `/user/proj`가 NFS 마운트를 가리키는
    심링크면, 풀지 않고는 그 경로가 NFS 위라는 사실을 알 수 없다. 다만 그
    해석은 POSIX에서만 뜻이 있다 (`/proc/mounts`가 있는 곳). 개발 PC에서는
    `/user`가 `C:\\user`로 바뀌어 오히려 비교가 깨지므로 문자열만 다듬는다.
    """

    text = str(path)
    if os.name == "posix":
        try:
            text = os.path.realpath(text)
        except OSError:  # pragma: no cover - 방어적 처리
            pass
    return posixpath.normpath(text.replace("\\", "/"))


def filesystem_type(path: Path, source: Optional[Path] = None) -> Optional[str]:
    """이 경로가 올라가 있는 파일시스템 종류 (`nfs4`, `xfs` 등). 모르면 None.

    가장 긴 마운트 지점이 이긴다 - `/`와 `/user`가 둘 다 접두사면 `/user`가
    실제로 그 경로를 담고 있는 마운트다.
    """

    entries = _mount_entries(source)
    if not entries:
        return None

    target = _mount_comparable(path)

    best_point = ""
    best_type = None
    for mount_point, fs_type in entries:
        if target == mount_point or target.startswith(mount_point.rstrip("/") + "/"):
            if len(mount_point) >= len(best_point):
                best_point, best_type = mount_point, fs_type
    return best_type


def is_network_filesystem(path: Path, source: Optional[Path] = None) -> bool:
    """이 경로가 네트워크 파일시스템 위인가.

    판단이 안 되면(리눅스가 아니거나 `/proc/mounts`를 못 읽으면) False를 준다 -
    모르는 상태에서 WAL을 끄면 개발 PC 같은 정상 환경이 이유 없이 느려진다.
    """

    fs_type = filesystem_type(path, source)
    return bool(fs_type) and fs_type.lower() in NETWORK_FILESYSTEMS


def journal_mode_for(path: Path, source: Optional[Path] = None) -> str:
    """이 경로에서 안전한 SQLite journal 모드.

    네트워크 파일시스템이면 `DELETE`(고전 롤백 저널)로 물러선다. 느리지만
    `-shm` 공유 메모리를 요구하지 않아, WAL처럼 조용히 깨지지 않는다.
    SQLite 문서가 네트워크 저장소에 권하는 방식이기도 하다.
    """

    return "DELETE" if is_network_filesystem(path, source) else "WAL"
