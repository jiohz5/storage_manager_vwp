"""사용률 등급(정상/주의/경고/긴급/FULL) 계산.

DESIGN.md 1부 4절의 등급 기준을 그대로 따른다: 90% 미만 정상, 90~94% 주의,
95~97% 경고, 98~99% 긴급, 100% 이상 FULL. byte 등급과 inode 등급은 서로
독립적으로 계산한다 (inode가 가득 차면 용량이 남아도 파일을 만들 수 없기
때문). 화면에는 항상 색상 + 텍스트 라벨을 함께 쓴다 (색맹/흑백 출력 대비,
DESIGN.md 2부 6절 "1차 채택" 항목).

숫자를 과장하지 않는다는 원칙(DESIGN.md 1부 2-6)에 따라, 여기서는 df가 이미
계산해 준 사용률(%)을 그대로 등급 판정에 쓴다 — 별도로 재추정하지 않는다.
"""

from __future__ import annotations

from typing import Optional

from . import i18n

NORMAL = "normal"
WARN = "warn"
ALERT = "alert"
EMERGENCY = "emergency"
FULL = "full"
UNKNOWN = "unknown"

# 등급 순서 (심각도 오름차순). UNKNOWN은 "판단 불가"이며 정상보다 낮게 취급하지
# 않는다 — 표시에서는 별도로 다루고, 심각도 비교에서는 가장 낮은 취급으로 둔다.
_SEVERITY = {
    UNKNOWN: -1,
    NORMAL: 0,
    WARN: 1,
    ALERT: 2,
    EMERGENCY: 3,
    FULL: 4,
}

# 등급별 기준 색상 — 어두운 배경/밝은 배경 모두에서 무난하게 보이도록 중간
# 톤을 선택했다. 표시 라벨은 언어에 따라 달라지므로 여기 두지 않고
# `i18n.t("tier.<code>")`로 그때그때 가져온다 (`label()` 참고).
LABELS = {
    NORMAL: "#2e7d32",
    WARN: "#f9a825",
    ALERT: "#ef6c00",
    EMERGENCY: "#c62828",
    FULL: "#6a1b9a",
    UNKNOWN: "#757575",
}

# 표 행 배경에 쓸 옅은 색. 기준 색을 그대로 배경에 깔면 글자가 안 읽히므로
# 같은 계열의 아주 옅은 톤을 따로 둔다.
#
# 정상 등급에 색을 주지 않는 것은 의도다. 계정 대부분이 정상인 것이 보통인데
# 전부 칠하면 색이 배경 소음이 되어, 정작 문제 있는 행이 묻힌다. 색은 "눈이
# 가야 할 곳"에만 쓴다.
ROW_BACKGROUNDS = {
    WARN: "#fff8e1",
    ALERT: "#fff0e0",
    EMERGENCY: "#ffebee",
    FULL: "#f3e5f5",
}

WARN_THRESHOLD = 90
ALERT_THRESHOLD = 95
EMERGENCY_THRESHOLD = 98
FULL_THRESHOLD = 100


def classify(pct: Optional[float]) -> str:
    """사용률(%)을 등급 문자열로 변환한다. pct가 None이면 UNKNOWN."""

    if pct is None:
        return UNKNOWN
    if pct >= FULL_THRESHOLD:
        return FULL
    if pct >= EMERGENCY_THRESHOLD:
        return EMERGENCY
    if pct >= ALERT_THRESHOLD:
        return ALERT
    if pct >= WARN_THRESHOLD:
        return WARN
    return NORMAL


def severity(tier: str) -> int:
    return _SEVERITY.get(tier, -1)


def worse(tier_a: str, tier_b: str) -> str:
    """두 등급 중 더 심각한 쪽을 반환한다 (byte 등급 vs inode 등급 종합용)."""

    return tier_a if severity(tier_a) >= severity(tier_b) else tier_b


def is_at_least(tier: str, threshold_tier: str) -> bool:
    """tier가 threshold_tier 이상으로 심각한지. UNKNOWN은 항상 False."""

    if tier == UNKNOWN:
        return False
    return severity(tier) >= severity(threshold_tier)


def label(tier: str) -> str:
    """현재 언어의 등급 라벨. 등급 코드 자체(`normal` 등)는 언어 중립이므로
    저장/전송에는 코드를 쓰고, 라벨은 화면에 보일 때만 만든다."""

    key = tier if tier in LABELS else UNKNOWN
    return i18n.t(f"tier.{key}")


def color(tier: str) -> str:
    return LABELS.get(tier, LABELS[UNKNOWN])


def row_background(tier: str) -> Optional[str]:
    """표 행 배경색. 정상·확인불가는 None (칠하지 않는다).

    색은 등급을 전달하는 **유일한** 수단이 아니라 보조 수단이다. 등급 자체는
    항상 텍스트 배지로도 보이므로(`display_text`), 색을 못 보는 환경에서도
    정보가 사라지지 않는다."""

    return ROW_BACKGROUNDS.get(tier)


def display_text(tier: str, pct: Optional[float]) -> str:
    """색상 없이도 등급을 알 수 있는 텍스트 표현 (예: '경고 95.2%')."""

    if pct is None:
        return label(tier)
    return f"{label(tier)} {pct:.1f}%"
