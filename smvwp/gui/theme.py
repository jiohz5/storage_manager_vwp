"""애플리케이션 전역 테마 (폰트 + 팔레트 + 타이포 스케일 + QSS).

## 왜 폰트를 직접 싣는가

화면이 "직접 만든 티" 나는 가장 큰 원인은 QSS 문법이 아니라 **폰트**다.
시스템 기본 한글 폰트(맑은 고딕, RHEL 기본 고딕)는 자간·굵기 단계가 빈약해
무엇을 크게 써도 위계가 생기지 않는다. Pretendard를 앱이 직접 싣으면 어떤
장비에 반입해도 같은 화면이 나온다 - 폐쇄망에서 "그 장비엔 폰트가 없더라"를
겪지 않기 위한 선택이기도 하다.

`smvwp/gui/fonts/`에 Regular/Bold/ExtraBold 세 벌만 둔다 (각 ~1.5MB).
가변폰트(Variable) 한 벌이면 파일이 하나로 끝나지만, **Qt 5는 가변폰트를
Regular/Bold 두 단계로만 읽어들여** 굵기 축을 쓸 수 없다. 실제로 재 보면
weight를 Medium/Black으로 지정해도 렌더가 Normal/Bold와 픽셀 단위로 같다.
정적 세 벌이 오히려 파일 크기도 작고(4.5MB vs 6.4MB) 굵기도 세 단계 나온다.

굵기는 반드시 **weight**로 고른다 (`font-weight` / `QFont.setWeight`).
`setStyleName("Bold")`은 이 폰트 조합에서 Regular로 떨어진다.

## 타이포 스케일

크기를 몇 가지로 못박아 두고 그 안에서만 고른다. "이건 조금 더 크게"를
그때그때 정하면 화면마다 값이 달라져 결국 지저분해진다.

    DISPLAY 30 / TITLE 20 / SUBTITLE 15 / BODY 14 / CAPTION 12

## 색 고르는 규칙

- **회색조는 한 계열로 통일한다.** 파랑 끼가 도는 회색과 중성 회색을 섞으면
  이유 없이 지저분해 보인다.
- **강조색은 하나.** 파랑을 주요 동작(기본 버튼·선택·포커스)에만 쓴다.
- **등급 색은 건드리지 않는다.** 정상/주의/경고/긴급/FULL은 `tiers.py`가 정한
  의미 있는 색이므로 테마가 덮어쓰지 않는다.
- **그림자를 쓰지 않는다.** Qt QSS에 box-shadow가 없기도 하지만, 애초에
  경계는 1px 선과 여백으로 충분하다. 그림자를 흉내 내려고 테두리를 여러 겹
  두르면 그때부터 촌스러워진다.

## 접근성

색만으로 정보를 전달하지 않는다는 기존 원칙(DESIGN.md)을 그대로 지킨다. 이
테마는 "어디를 먼저 볼지" 돕는 보조 수단이고, 등급·상태는 항상 글자로도
보인다.
"""

from __future__ import annotations

from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_FAMILY = "Pretendard"

# 폰트를 못 실었을 때 쓸 대체 순서. 한글이 깨지는 것보다는 낫다.
FALLBACK_FAMILIES = ["Noto Sans KR", "Malgun Gothic", "AppleSDGothicNeo", "sans-serif"]

# -- 팔레트 ----------------------------------------------------------------
# 회색조는 푸른 기가 아주 약간 도는 한 계열로 통일한다.
BG = "#edf1f5"          # 창 배경 (카드가 떠 보이도록 흰색보다 약간 어둡게)
SURFACE = "#ffffff"     # 카드·입력칸 배경
SURFACE_SUNKEN = "#f5f8fa"  # 표 헤더처럼 한 단계 눌린 면
BORDER = "#dde4ec"      # 기본 테두리
BORDER_STRONG = "#c8d4e0"
TEXT = "#0a1829"        # 본문 (거의 검정이지만 남색 기를 남겨 딱딱함을 던다)
TEXT_MUTED = "#3d5e78"  # 보조 설명
TEXT_FAINT = "#8fa3b5"  # 플레이스홀더

ACCENT = "#0145f2"      # 강조 (주요 동작·선택·포커스)
ACCENT_HOVER = "#0139cc"
ACCENT_PRESSED = "#012fa8"
ACCENT_SOFT = "#e6edfe"  # 선택 행 배경
DANGER = "#c62828"
DANGER_SOFT = "#fdecec"

# -- 타이포 스케일 ----------------------------------------------------------
FONT_DISPLAY = 30   # 히어로 숫자
FONT_TITLE = 20     # 화면·섹션 제목
FONT_SUBTITLE = 15  # 카드 안 소제목
FONT_BODY = 14      # 본문·표
FONT_CAPTION = 12   # 보조 설명·단위

WEIGHT_REGULAR = "normal"
WEIGHT_BOLD = "bold"
WEIGHT_HEAVY = "900"  # -> ExtraBold

RADIUS = "10px"      # 버튼·입력칸
RADIUS_CARD = "16px"  # 카드

# 카드 안쪽 여백과 카드 사이 간격. 레이아웃 코드가 같은 값을 쓰도록 여기 둔다.
PAD_CARD = 20
GAP_SECTION = 14


def load_fonts() -> bool:
    """번들 폰트를 등록한다. 성공하면 True.

    `QApplication`이 만들어진 뒤에 불러야 한다 (Qt 요구사항)."""

    from PyQt5.QtGui import QFontDatabase

    loaded = False
    for path in sorted(FONT_DIR.glob("Pretendard-*.otf")):
        if QFontDatabase.addApplicationFont(str(path)) != -1:
            loaded = True
    return loaded


def font_stack() -> str:
    """QSS `font-family`에 넣을 후보 목록."""

    families = [FONT_FAMILY, *FALLBACK_FAMILIES]
    return ", ".join(f"'{name}'" for name in families)


def stylesheet() -> str:
    """앱 전체에 적용할 QSS."""

    stack = font_stack()
    return f"""
/* ---------- 전역 ---------- */
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: {stack};
    font-size: {FONT_BODY}px;
}}
QMainWindow, QDialog {{ background: {BG}; }}

/* 라벨은 자기 배경을 칠하지 않는다. 위의 QWidget 규칙을 그대로 두면 흰 카드
   위에 얹힌 글자마다 창 배경색 띠가 깔려 줄무늬처럼 보인다. */
QLabel {{ background: transparent; }}

QToolTip {{
    background: {TEXT};
    color: #ffffff;
    border: none;
    padding: 7px 10px;
    border-radius: 8px;
    font-size: {FONT_CAPTION}px;
}}

/* ---------- 메뉴 ---------- */
QMenuBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; padding: 3px 6px; }}
QMenuBar::item {{ padding: 6px 12px; border-radius: 8px; background: transparent; }}
QMenuBar::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS}; padding: 6px; }}
QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}

/* ---------- 카드 ---------- */
/* 관련 있는 것끼리 묶어 여백으로 구분한다. 테두리를 여러 겹 쓰지 않는다. */
QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CARD};
}}
/* 히어로: 화면에서 가장 먼저 봐야 하는 한 덩어리 */
QFrame#hero {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CARD};
}}

/* ---------- 타이포 ---------- */
QLabel#display {{ font-size: {FONT_DISPLAY}px; font-weight: {WEIGHT_HEAVY}; }}
QLabel#title {{ font-size: {FONT_TITLE}px; font-weight: {WEIGHT_HEAVY}; }}
QLabel#sectionTitle {{ font-size: {FONT_SUBTITLE}px; font-weight: {WEIGHT_BOLD}; color: {TEXT}; }}
QLabel#muted {{ color: {TEXT_MUTED}; }}
QLabel#caption {{ color: {TEXT_MUTED}; font-size: {FONT_CAPTION}px; }}
QLabel#statLabel {{ color: {TEXT_MUTED}; font-size: {FONT_CAPTION}px; }}
QLabel#statValue {{ font-size: {FONT_TITLE}px; font-weight: {WEIGHT_HEAVY}; }}

/* ---------- 버튼 ---------- */
QPushButton {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS};
    padding: 8px 16px;
    font-weight: {WEIGHT_BOLD};
    min-height: 20px;
}}
QPushButton:hover {{ background: {SURFACE_SUNKEN}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #e8eef4; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background: {SURFACE_SUNKEN}; border-color: {BORDER}; }}

/* 주요 동작 하나만 채운 버튼으로 둔다 - 전부 강조하면 아무것도 강조되지 않는다 */
QPushButton#primary {{
    background: {ACCENT};
    color: #ffffff;
    border: 1px solid {ACCENT};
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#primary:disabled {{ background: #a8bdf7; border-color: #a8bdf7; color: #eef2ff; }}

/* 되돌리기 어려운 동작은 색으로 구분한다 */
QPushButton#danger {{ color: {DANGER}; border-color: #eda9a9; }}
QPushButton#danger:hover {{ background: {DANGER_SOFT}; border-color: {DANGER}; }}
QPushButton#danger:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; background: {SURFACE_SUNKEN}; }}

/* ---------- 입력 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS};
    padding: 8px 12px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
    background: {SURFACE_SUNKEN}; color: {TEXT_FAINT};
}}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS};
    padding: 4px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {ACCENT};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; border: none; background: transparent; }}

/* ---------- 표 ---------- */
QTableWidget, QListWidget {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_CARD};
    outline: none;
}}
QTableWidget::item, QListWidget::item {{ padding: 9px 8px; border: none; }}
QTableWidget::item:selected, QListWidget::item:selected {{
    background: {ACCENT_SOFT};
    color: {TEXT};
}}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {SURFACE_SUNKEN};
    color: {TEXT_MUTED};
    padding: 11px 8px;
    border: none;
    border-bottom: 1px solid {BORDER};
    font-size: {FONT_CAPTION}px;
    font-weight: {WEIGHT_BOLD};
}}
QTableCornerButton::section {{ background: {SURFACE_SUNKEN}; border: none; }}

/* ---------- 진행 막대 ---------- */
/* 상세 스캔이 도는 동안만 보이는 불확정 막대. 얇게 둬서 "일하는 중"만
   알리고 화면의 주인공(계정 표)을 밀어내지 않게 한다. */
QProgressBar {{
    background: {BORDER};
    border: none;
    border-radius: 3px;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

/* ---------- 스크롤바 ---------- */
/* 기본 스크롤바가 가장 '초보 티' 나는 부분이라 손본다 */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 3px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT_FAINT}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 3px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_STRONG}; border-radius: 5px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {TEXT_FAINT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- 기타 ---------- */
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 17px; height: 17px; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_MUTED}; }}
QStatusBar::item {{ border: none; }}
QFrame#divider {{ background: {BORDER}; border: none; max-height: 1px; min-height: 1px; }}

/* 스플리터 손잡이는 끌 수 있다는 것만 알리면 된다 - 선을 그으면 카드 사이에
   경계가 하나 더 생겨 지저분해진다. */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {BORDER}; border-radius: 2px; }}
"""


def apply(app) -> None:
    """`QApplication`에 폰트와 테마를 적용한다.

    Fusion 스타일을 먼저 지정한다 - 플랫폼 기본 스타일은 QSS를 부분적으로만
    받아들여 장비마다 다르게 보이는데, Fusion은 어디서나 같게 그려진다.
    반입 대상이 여러 장비라 이 일관성이 중요하다."""

    from PyQt5.QtGui import QFont

    app.setStyle("Fusion")
    load_fonts()
    # QSS의 font-family만으로도 대부분 먹지만, 기본 폰트를 함께 지정해야
    # QSS가 닿지 않는 곳(네이티브 파일 대화상자 등)까지 일관되게 보인다.
    # 스케일 값은 QSS와 같은 px 기준이므로 setPixelSize를 쓴다 (setPointSize를
    # 쓰면 같은 숫자가 훨씬 큰 글자가 되어 QSS와 어긋난다).
    base_font = QFont(FONT_FAMILY)
    base_font.setPixelSize(FONT_BODY)
    app.setFont(base_font)
    app.setStyleSheet(stylesheet())
