"""애플리케이션 전역 QSS 테마.

## 왜 직접 쓰는가

`qdarkstyle`/`qt-material` 같은 외부 테마 패키지도 있지만, 이 앱은 폐쇄망에
반입되므로 의존성을 하나 늘리면 곧 설치 절차가 하나 늘어난다. QSS는 결국
문자열 하나라서 파일로 들고 있는 편이 훨씬 가볍고, 이 앱이 실제로 쓰는 위젯만
겨냥해 다듬을 수 있다.

## 색 고르는 규칙

- **회색조는 한 계열로 통일한다.** 파랑 끼가 도는 회색과 중성 회색을 섞으면
  이유 없이 지저분해 보인다. 여기서는 slate 계열 하나만 쓴다.
- **강조색은 하나.** 파랑을 주요 동작(기본 버튼·선택·포커스)에만 쓴다. 여기저기
  칠하면 정작 눌러야 할 곳이 묻힌다.
- **등급 색은 건드리지 않는다.** 정상/주의/경고/긴급/FULL은 `tiers.py`가 정한
  의미 있는 색이므로 테마가 덮어쓰지 않는다.

## 접근성

색만으로 정보를 전달하지 않는다는 기존 원칙(DESIGN.md)을 그대로 지킨다. 이
테마는 "어디를 먼저 볼지" 돕는 보조 수단이고, 등급·상태는 항상 글자로도
보인다.
"""

from __future__ import annotations

# -- 팔레트 ----------------------------------------------------------------
# slate 계열 한 벌. 이름은 밝기 순서(낮을수록 밝음)로 붙였다.
BG = "#f4f6f8"          # 창 배경
SURFACE = "#ffffff"     # 카드·입력칸 배경
BORDER = "#dfe3e8"      # 기본 테두리
BORDER_STRONG = "#c4cad2"
TEXT = "#1f2933"        # 본문
TEXT_MUTED = "#6b7280"  # 보조 설명
TEXT_FAINT = "#9aa3ad"  # 플레이스홀더

ACCENT = "#2563eb"      # 강조 (주요 동작·선택·포커스)
ACCENT_HOVER = "#1d4ed8"
ACCENT_PRESSED = "#1e40af"
ACCENT_SOFT = "#e8effd"  # 선택 행 배경
DANGER = "#c62828"
DANGER_SOFT = "#fdecec"

RADIUS = "6px"


def stylesheet() -> str:
    """앱 전체에 적용할 QSS."""

    return f"""
/* ---------- 전역 ---------- */
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 10pt;
}}
QMainWindow, QDialog {{ background: {BG}; }}

QToolTip {{
    background: {TEXT};
    color: #ffffff;
    border: none;
    padding: 6px 8px;
    border-radius: 4px;
}}

/* ---------- 메뉴 ---------- */
QMenuBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; padding: 2px; }}
QMenuBar::item {{ padding: 6px 12px; border-radius: 4px; background: transparent; }}
QMenuBar::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}

/* ---------- 카드 ---------- */
/* 관련 있는 것끼리 묶어 여백으로 구분한다. 테두리를 여러 겹 쓰지 않는다. */
QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}

/* ---------- 버튼 ---------- */
QPushButton {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS};
    padding: 7px 16px;
    min-height: 18px;
}}
QPushButton:hover {{ background: #f0f3f7; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #e6ebf2; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background: #f2f4f6; border-color: {BORDER}; }}

/* 주요 동작 하나만 채운 버튼으로 둔다 - 전부 강조하면 아무것도 강조되지 않는다 */
QPushButton#primary {{
    background: {ACCENT};
    color: #ffffff;
    border: 1px solid {ACCENT};
    font-weight: bold;
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#primary:disabled {{ background: #a9bced; border-color: #a9bced; color: #eef2ff; }}

/* 되돌리기 어려운 동작은 색으로 구분한다 */
QPushButton#danger {{ color: {DANGER}; border-color: #eda9a9; }}
QPushButton#danger:hover {{ background: {DANGER_SOFT}; border-color: {DANGER}; }}
QPushButton#danger:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; background: #f2f4f6; }}

/* ---------- 입력 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS};
    padding: 6px 10px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
    background: #f2f4f6; color: {TEXT_FAINT};
}}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {ACCENT};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}

/* ---------- 표 ---------- */
QTableWidget, QListWidget {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS};
    gridline-color: #eef1f4;
    outline: none;
}}
QTableWidget::item, QListWidget::item {{ padding: 7px 6px; border: none; }}
QTableWidget::item:selected, QListWidget::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT};
}}
QHeaderView::section {{
    background: #eef1f4;
    color: #4b5563;
    padding: 8px 6px;
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    font-weight: bold;
}}
QHeaderView::section:last {{ border-right: none; }}
QTableCornerButton::section {{ background: #eef1f4; border: none; }}

/* ---------- 스크롤바 ---------- */
/* 기본 스크롤바가 가장 '초보 티' 나는 부분이라 손본다 */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #c4cad2; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: #a7aeb8; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #c4cad2; border-radius: 5px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: #a7aeb8; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- 기타 ---------- */
QCheckBox, QRadioButton {{ spacing: 7px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_MUTED}; }}
QStatusBar::item {{ border: none; }}
QLabel#muted {{ color: {TEXT_MUTED}; }}
QLabel#sectionTitle {{ font-weight: bold; font-size: 11pt; color: #374151; }}
"""


def apply(app) -> None:
    """`QApplication`에 테마를 적용한다.

    Fusion 스타일을 먼저 지정한다 - 플랫폼 기본 스타일은 QSS를 부분적으로만
    받아들여 장비마다 다르게 보이는데, Fusion은 어디서나 같게 그려진다.
    반입 대상이 여러 장비라 이 일관성이 중요하다."""

    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
