"""데이터 디렉터리 경로 결정 및 쓰기 안전성 점검.

읽기 전용 원칙(CONCEPT.md 1, 5절)의 핵심: 모니터링 대상 계정 경로에는 절대
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
from pathlib import Path
from typing import Mapping, Optional


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
