"""파일 하나가 '비정상적으로' 큰지 가린다 (Qt 없음).

## 무엇을 '튄다'고 볼 것인가

셋 다 사람이 설명할 수 있는 규칙이다. 통계 모형을 쓰지 않는 이유는, 왜
걸렸는지 물었을 때 "중앙값의 3배"라고 답할 수 있어야 하기 때문이다.

- **계정 안에서 비중이 크다** - 파일 하나가 계정 전체의 몇 %를 차지한다.
- **지난 밤보다 많이 컸다** - 같은 경로가 눈에 띄게 늘었다.
- **지난 밤 상위 목록에 없었다** - 새로 생겼거나 그때는 작았다.

세 번째를 "새 파일"이라고 부르지 않는 것에 유의. 우리가 아는 것은 "상위
목록에 없었다"까지다 - 새로 생긴 것인지 그때는 작았던 것인지는 이 데이터로
가릴 수 없고, 지어내면 안 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

# 계정 전체에서 이 비율을 넘게 차지하면 그 자체로 눈에 띈다.
SHARE_ALERT_PCT = 5.0

# 지난 밤 대비 이만큼 늘면 튄 것으로 본다.
GROWTH_ALERT_RATIO = 1.5


@dataclass
class LargeFile:
    """큰 파일 하나와 그것을 어떻게 볼지."""

    path: str
    size_kb: int
    previous_kb: Optional[int] = None
    account_total_kb: Optional[int] = None

    @property
    def share_pct(self) -> Optional[float]:
        """계정 전체에서 차지하는 비율."""

        if not self.account_total_kb:
            return None
        return self.size_kb / self.account_total_kb * 100.0

    @property
    def delta_kb(self) -> Optional[int]:
        if self.previous_kb is None:
            return None
        return self.size_kb - self.previous_kb

    @property
    def is_new_to_the_list(self) -> bool:
        """지난 밤 상위 목록에 없었다.

        '새로 생겼다'가 아니다 - 그때는 작았을 수도 있다."""

        return self.previous_kb is None

    @property
    def grew_sharply(self) -> bool:
        if self.previous_kb is None or self.previous_kb <= 0:
            return False
        return self.size_kb >= self.previous_kb * GROWTH_ALERT_RATIO

    @property
    def dominates_account(self) -> bool:
        share = self.share_pct
        return share is not None and share >= SHARE_ALERT_PCT

    @property
    def is_notable(self) -> bool:
        """화면에서 눈에 띄게 표시할 값어치가 있는가."""

        return self.is_new_to_the_list or self.grew_sharply or self.dominates_account

    def reasons(self) -> List[str]:
        """왜 눈에 띄는지 - i18n 키로. 없으면 빈 목록."""

        out = []
        if self.dominates_account:
            out.append("large.reason.share")
        if self.grew_sharply:
            out.append("large.reason.grew")
        if self.is_new_to_the_list:
            out.append("large.reason.new")
        return out


def build(rows, account_total_kb: Optional[int] = None) -> List[LargeFile]:
    """`(경로, 지금 KB, 이전 KB)` 목록을 `LargeFile` 로.

    `scan_store.large_file_changes` 가 주는 모양을 그대로 받는다.
    """

    return [
        LargeFile(
            path=path,
            size_kb=size_kb,
            previous_kb=previous_kb,
            account_total_kb=account_total_kb,
        )
        for path, size_kb, previous_kb in rows
    ]
