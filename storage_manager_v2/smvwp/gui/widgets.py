"""등급 배지 등 작은 재사용 위젯.

색상 + 텍스트 라벨을 항상 함께 쓴다 (REBUILD_CONCEPT.md 6절 "1차 채택" —
색상만으로 등급을 구분하지 않는다).
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel

from .. import tiers


def tier_badge_text(tier: str, pct: Optional[float]) -> str:
    return tiers.display_text(tier, pct)


def format_kb(size_kb: Optional[int]) -> str:
    """KB 정수를 사람이 읽는 크기 문자열로. 값이 없으면 '-'.

    `du -sk`가 KB 단위로 주므로 KB를 기준 단위로 삼는다. 1024 배수를 쓰되
    소수점 한 자리까지만 보여준다 (정밀도를 실제보다 높아 보이게 하지 않기
    위해 유효숫자를 늘리지 않는다)."""

    if size_kb is None:
        return "-"
    value = float(size_kb)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024 or unit == "PB":
            if unit == "KB":
                return f"{int(value):,} KB"
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} PB"  # pragma: no cover - 위 루프에서 반환됨


def format_bytes(size_bytes: Optional[int]) -> str:
    """byte 단위 크기를 사람이 읽는 문자열로 (파일 실제 크기 표시용)."""

    if size_bytes is None:
        return "-"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value):,} B"
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} TB"  # pragma: no cover - 위 루프에서 반환됨


def format_kb_delta(delta_kb: Optional[int]) -> str:
    """증감량 표시 - 부호를 명시하고 0은 '변화 없음'으로."""

    if delta_kb is None:
        return "-"
    if delta_kb == 0:
        return "변화 없음"
    sign = "+" if delta_kb > 0 else "-"
    return f"{sign}{format_kb(abs(delta_kb))}"


class TierBadge(QLabel):
    """등급을 배경색 + 텍스트로 함께 보여주는 라벨."""

    def __init__(self, tier: str = tiers.UNKNOWN, pct: Optional[float] = None, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.set_tier(tier, pct)

    def set_tier(self, tier: str, pct: Optional[float]) -> None:
        self.setText(tier_badge_text(tier, pct))
        color = tiers.color(tier)
        self.setStyleSheet(
            f"background-color: {color}; color: white; border-radius: 4px; "
            "padding: 2px 8px; font-weight: bold;"
        )
