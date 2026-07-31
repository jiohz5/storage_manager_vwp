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
