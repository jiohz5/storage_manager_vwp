"""계정 경로를 얼마나 읽을 수 있는지 등록 시점에 표본 조사한다.

## 왜 필요한가

관리자가 아닌 사용자가 남의 프로젝트 계정을 모니터링하면, 최상위는 읽히는데
하위는 막혀 있는 경우가 흔하다. 등록 검사는 최상위 `R_OK`만 보므로 그런 경로도
그대로 등록되고, 문제는 며칠 뒤 야간 스캔이 돌고 나서야 "크기가 실제보다 작다"는
형태로 드러난다. 등록하는 그 자리에서 알려주는 편이 낫다.

## 왜 표본 조사인가

계정 하나가 수십 TB일 수 있어 전체 순회는 GUI에서 할 수 없다. 그래서 디렉터리
수와 시간에 예산을 두고 넓이 우선으로 훑는다.

## 표본에서 전체 비율을 추정하지 않는다

"이 계정의 63%를 읽을 수 없습니다" 같은 문장은 만들지 않는다. 200곳을 봤는데
그중 30곳이 막혔다면 그대로 "확인한 200곳 중 30곳"이라고 말한다. 표본이 트리
전체를 대표한다는 보장이 없는데 퍼센트를 붙이면 없는 정밀도를 지어내는 것이고,
이 프로젝트가 예측·진행률에서 지켜 온 원칙과도 어긋난다.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

# GUI에서 돌아야 하므로 예산을 짧게 잡는다. 넘으면 truncated로 표시하고 멈춘다.
DEFAULT_MAX_DIRS = 200
DEFAULT_MAX_SECONDS = 3.0
# 사용자에게 보여줄 예시 개수 - 다 나열하면 읽히지 않는다.
SAMPLE_LIMIT = 5


@dataclass
class ReadabilityProbe:
    checked: int = 0
    unreadable: int = 0
    unreadable_samples: List[str] = field(default_factory=list)
    truncated: bool = False
    root_readable: bool = True

    @property
    def fully_readable(self) -> bool:
        return self.root_readable and self.unreadable == 0

    @property
    def has_findings(self) -> bool:
        return not self.root_readable or self.unreadable > 0


def probe(
    root: Path,
    max_dirs: int = DEFAULT_MAX_DIRS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> ReadabilityProbe:
    """`root` 아래를 넓이 우선으로 훑으며 읽을 수 없는 디렉터리를 센다.

    심볼릭 링크는 따라가지 않는다 (스캔 정책과 동일). 예산을 넘으면 거기서
    멈추고 `truncated=True`로 표시한다 - 끝까지 못 봤다는 사실을 숨기지 않기
    위함이다.
    """

    result = ReadabilityProbe()
    deadline = time.monotonic() + max_seconds

    try:
        os.scandir(root).close()
    except OSError:
        result.root_readable = False
        return result

    queue = deque([Path(root)])
    while queue:
        if result.checked >= max_dirs or time.monotonic() > deadline:
            result.truncated = True
            break

        current = queue.popleft()
        result.checked += 1
        try:
            with os.scandir(current) as entries:
                children = [
                    Path(entry.path)
                    for entry in entries
                    if entry.is_dir(follow_symlinks=False)
                ]
        except OSError:
            # 목록 자체를 못 읽음 = 이 하위는 통째로 측정에서 빠진다.
            result.unreadable += 1
            if len(result.unreadable_samples) < SAMPLE_LIMIT:
                result.unreadable_samples.append(str(current))
            continue

        queue.extend(children)

    if queue:
        result.truncated = True
    return result


def describe(probe_result: ReadabilityProbe) -> str:
    """사람이 읽는 요약. 표본에서 전체 비율을 추정하지 않는다."""

    from . import i18n

    if not probe_result.root_readable:
        return i18n.t("readability.root_unreadable")
    if probe_result.fully_readable:
        key = "readability.all_ok_partial" if probe_result.truncated else "readability.all_ok"
        return i18n.t(key, checked=probe_result.checked)

    text = i18n.t(
        "readability.some_unreadable",
        checked=probe_result.checked,
        unreadable=probe_result.unreadable,
    )
    if probe_result.truncated:
        text += " " + i18n.t("readability.truncated_note")
    if probe_result.unreadable_samples:
        text += "\n" + "\n".join(f"  - {p}" for p in probe_result.unreadable_samples)
        remaining = probe_result.unreadable - len(probe_result.unreadable_samples)
        if remaining > 0:
            text += "\n  " + i18n.t("readability.more", count=remaining)
    return text
