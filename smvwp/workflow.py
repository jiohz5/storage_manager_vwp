"""의뢰서 기반 과제 작업 디렉터리 구조를 알아보는 규칙.

사내 작업은 의뢰서 하나가 곧 job 하나이고, 디스크에는 다음 모양으로 남는다:

    <과제명>/LAYOUT/<NN_run_MMDD>/{BACKUP, CROSSCHECK, SIGNOFF, OPUS, CCOM, ...}

여기서 이 프로그램이 알아야 하는 것은 사실상 하나다 - **`*_run_*` 디렉터리가
새로 생기면 과제가 하나 시작된 것.** 그 위아래 층(과제명, LAYOUT, 단계
디렉터리)은 현장마다 이름이 달라질 수 있으므로 규칙으로 못 박지 않는다.
단계 디렉터리 이름은 "있으면 알아본다" 수준으로만 쓰고, 없다고 해서 과제가
아니라고 판단하지 않는다.

## 왜 파일시스템을 다시 뒤지지 않는가

과제 생성을 알아내려고 `find`를 새로 돌 필요가 없다. 야간 기준선 스캔의
`du -k`가 이미 모든 하위 디렉터리 경로를 `baseline_results`에 넣어 두었으므로,
**직전 세대에 없던 경로 = 그 사이 새로 생긴 것**이다. 추가 I/O가 0이고,
mtime에 기대지 않으므로 "안의 파일만 바뀌고 디렉터리 mtime은 그대로"인 함정도
비켜 간다.

## 알고 있어야 할 제약

- `detail_scan_max_depth`(기본 3)보다 깊은 run 디렉터리는 애초에
  `baseline_results`에 없으므로 감지되지 않는다. 계정 루트가 과제명 바로
  위라면 run 디렉터리는 체크포인트 루트 기준 깊이 2라 기본값으로 잡힌다.
- 기준선 세대가 하나뿐인 계정(첫 스캔)은 비교 대상이 없다. 이때 "전부 새로
  생겼다"고 말하면 첫 보고서가 과제 수천 개로 뒤덮인다. 판정을 **보류**하는
  것이 맞다 (`scan_store.new_run_dirs`가 이 경우 빈 목록을 준다).
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

# `*_run_*`의 실질적 의미는 "이름 어딘가에 _run_ 이 있다"이다. glob으로 쓰면
# 양끝의 `*`가 빈 문자열도 받으므로 결국 부분 문자열 검사와 같다 - 그러면
# fnmatch를 거치지 않고 직접 검사하는 편이 읽기 쉽고 빠르다.
RUN_DIR_MARKER = "_run_"

# 의뢰서 워크플로의 고정 단계 디렉터리. 순서는 실제 작업 흐름을 따른다.
STAGE_DIR_NAMES = ("BACKUP", "CROSSCHECK", "SIGNOFF", "OPUS", "CCOM")

# 프로젝트 계정의 백업본이 놓이는 자리. 나중에 "이 내용이 연결된 백업 계정에도
# 있는가"를 대조할 때 기준이 되는 이름이라 따로 이름을 붙여 둔다.
BACKUP_STAGE_DIR = "BACKUP"


def normalize(path: str) -> str:
    """뒤쪽 구분자를 떼어 비교 가능한 형태로. 루트(`/`)는 그대로 둔다."""

    if not path:
        return ""
    trimmed = path.rstrip("/")
    return trimmed or "/"


def basename(path: str) -> str:
    normalized = normalize(path)
    if normalized in ("", "/"):
        return normalized
    return normalized.rsplit("/", 1)[-1]


def is_run_dir(name: str) -> bool:
    """디렉터리 **이름**이 과제 실행 디렉터리인가 (`00_run_0811` 등).

    경로가 아니라 이름을 받는다. 경로로 검사하면 상위 어딘가에 `_run_`이 든
    디렉터리가 있을 때 그 아래 전부가 과제로 잡힌다."""

    return RUN_DIR_MARKER in (name or "")


def is_run_path(path: str) -> bool:
    return is_run_dir(basename(path))


def relative_parts(path: str, root: str) -> List[str]:
    """`root` 기준 상대 경로 성분. `path`가 `root` 아래가 아니면 빈 목록."""

    normalized_path = normalize(path)
    normalized_root = normalize(root)
    if not normalized_path or not normalized_root:
        return []
    if normalized_path == normalized_root:
        return []
    prefix = normalized_root if normalized_root.endswith("/") else normalized_root + "/"
    if not normalized_path.startswith(prefix):
        return []
    return [part for part in normalized_path[len(prefix):].split("/") if part]


def task_name_for(run_path: str, account_path: str) -> str:
    """run 디렉터리 경로에서 과제명을 뽑는다.

    구조상 과제명은 계정 루트 바로 아래 첫 성분이다. 계정 루트 밖의 경로거나
    성분을 못 찾으면 빈 문자열을 준다 - 여기서 추측해서 지어내면 보고서에
    엉뚱한 과제명이 실린다."""

    parts = relative_parts(run_path, account_path)
    return parts[0] if parts else ""


def display_label(run_path: str, account_path: str) -> str:
    """보고서에 쓸 표시 이름. `과제명 / 00_run_0811` 형태.

    전체 경로를 그대로 적으면 줄이 길어져 표가 무너지고, run 디렉터리 이름만
    적으면 어느 과제인지 모른다. 사람이 실제로 쓰는 두 조각만 남긴다."""

    task = task_name_for(run_path, account_path)
    name = basename(run_path)
    if task and task != name:
        return f"{task} / {name}"
    return name or run_path


def stage_dirs_in(paths: Iterable[str], run_path: str) -> List[str]:
    """run 디렉터리 **바로 아래**에 있는 표준 단계 디렉터리 이름들.

    `STAGE_DIR_NAMES` 순서(실제 작업 흐름)를 유지한다 - 알파벳순으로 정렬하면
    사람이 아는 순서와 어긋나 읽는 데 품이 든다. 표준에 없는 작업 디렉터리는
    현장마다 달라 목록으로 만들 수 없으므로 여기서 세지 않는다."""

    run_normalized = normalize(run_path)
    prefix = run_normalized + "/"
    present = set()
    for path in paths:
        normalized = normalize(path)
        if not normalized.startswith(prefix):
            continue
        remainder = normalized[len(prefix):]
        if "/" in remainder:  # 한 단계 아래만 본다
            continue
        present.add(remainder)
    return [name for name in STAGE_DIR_NAMES if name in present]


def backup_dir_for(run_path: str) -> str:
    """run 디렉터리에 대응하는 BACKUP 경로 (존재 여부는 확인하지 않는다)."""

    return normalize(run_path) + "/" + BACKUP_STAGE_DIR
