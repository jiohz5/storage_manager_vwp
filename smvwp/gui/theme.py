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

## 어두운 화면

색을 뒤집는 것이 아니라 **같은 이름에 다른 값**을 준다 (`LIGHT_TOKENS` /
`DARK_TOKENS`). 화면 코드는 `theme.BG` 처럼 이름만 쓰고, `use()` 가 이 모듈의
값을 갈아 끼운다. 그래서 지킬 것이 셋이다 (`tests/test_theme.py` 가 본다).

- 화면 코드에 색을 박지 않는다 (`#ffffff`, `Qt.white`). 박은 색은 어두운 화면에서
  혼자 밝게 남는다.
- 모듈을 읽을 때 색을 표에 담아 두지 않는다 (`STATE_COLORS = {..: theme.ACCENT}`).
  그 표만 옛 색으로 남는다 - 그릴 때 이름으로 꺼낸다.
- 등급 색은 `tiers.color` 가 아니라 `tier_color` (막대·선) / `tier_text` (글자) 로
  받는다. `tiers.py` 의 색은 흰 바탕용 한 벌이고 보고서도 쓴다 - 거기는 그대로 두고,
  색조는 같게 밝기만 화면에 맞춘다. 노랑(#f9a825) 글자는 흰 바탕에서 1.9:1 이라
  밝은 화면에서도 글자에는 짙은 쪽을 쓴다.

강조색은 둘로 나뉜다. 어두운 바탕 위의 파랑 **글자**는 밝아야 읽히고, 흰 글자를
얹는 파랑 **면**(주요 단추)은 어두워야 읽힌다 - `ACCENT` 와 `ACCENT_FILL`.
밝은 화면에서는 둘이 같다.

창을 다시 열지 않고 바뀐다 (`MainWindow._set_dark`). 고른 것은 이 PC 사용자의
설정 파일(`QSettings`, 리눅스면 `~/.config/smvwp/`)에 남긴다 - 데이터 폴더의
`config.json` 은 여러 사람이 같이 쓰므로, 한 사람이 어둡게 바꿨다고 남의 화면까지
바뀌면 안 된다.
"""

from __future__ import annotations

from pathlib import Path

from .. import tiers

FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_FAMILY = "Pretendard"

# 폰트를 못 실었을 때 쓸 대체 순서. 한글이 깨지는 것보다는 낫다.
FALLBACK_FAMILIES = ["Noto Sans KR", "Malgun Gothic", "AppleSDGothicNeo", "sans-serif"]

# -- 팔레트 ----------------------------------------------------------------
# 아래 값은 밝은 화면이다. 어두운 화면 값은 `DARK_TOKENS`, 바꾸는 것은 `use()`.
# 회색조는 푸른 기가 아주 약간 도는 한 계열로 통일한다.
BG = "#edf1f5"          # 창 배경 (카드가 떠 보이도록 흰색보다 약간 어둡게)
SURFACE = "#ffffff"     # 카드·입력칸 배경
SURFACE_SUNKEN = "#f5f8fa"  # 표 헤더처럼 한 단계 눌린 면
BORDER = "#dde4ec"      # 기본 테두리
BORDER_STRONG = "#c8d4e0"
TEXT = "#0a1829"        # 본문 (거의 검정이지만 남색 기를 남겨 딱딱함을 던다)
TEXT_MUTED = "#3d5e78"  # 보조 설명
TEXT_FAINT = "#8fa3b5"  # 플레이스홀더

ACCENT = "#0145f2"      # 강조 글자·선 (선택·포커스·링크)
ACCENT_FILL = "#0145f2"  # 강조 면 - 흰 글자를 얹는 주요 단추 (어두운 화면에서는 ACCENT 보다 짙다)
ACCENT_HOVER = "#0139cc"
ACCENT_PRESSED = "#012fa8"
ACCENT_SOFT = "#e6edfe"  # 선택 행 배경
ON_ACCENT = "#ffffff"    # 강조 면 위의 글자
PRIMARY_DISABLED = "#a8bdf7"
PRIMARY_DISABLED_TEXT = "#eef2ff"
BUTTON_PRESSED = "#e8eef4"
DANGER = "#c62828"
DANGER_SOFT = "#fdecec"
DANGER_BORDER = "#eda9a9"
TOOLTIP_BG = "#0a1829"
TOOLTIP_TEXT = "#ffffff"
GOOD_TEXT = "#1e5e23"    # '끝남' 처럼 잘 된 것을 말하는 글자
GOOD_SOFT = "#e8f5e9"
WARN_SOFT = "#fff3e0"

# 상세 스캔 현황의 상태 색. 등급 색(초록·노랑·주황·빨강·보라)과 섞이지 않게 파랑
# 계열과 회색으로 짓고, 서로는 밝기로도 갈린다 (색만으로 구분하지 않는다 - 글자가
# 늘 같이 있다). '못 봄' 만 주황이다 - 사람이 손봐야 하는 유일한 상태라서다.
SCAN_DONE = ACCENT
SCAN_DOING = "#7da2ff"
SCAN_WAITING = "#d5dde6"
SCAN_UNSEEN = "#ef6c00"
# 한 밤에 차례로 도는 계정들 - 같은 파랑의 짙고 옅은 단계 (바탕 쪽으로 갈수록 뒤).
NIGHT_SLOTS = ("#0145f2", "#3d6cf5", "#7da2ff", "#b7caff")
# 알약(칩) `(배경, 글자)`. 글자는 배경 위에서 4.5:1 이 넘는 것으로 골랐다.
CHIPS = {
    "done": ("#e6edfe", "#0139cc"),
    "doing": (ACCENT_FILL, ON_ACCENT),
    "soft": ("#eef2f6", TEXT_MUTED),
    "stale": ("#ffe8d6", "#8a3800"),
    "unseen": ("#ffffff", "#3d4b57"),
    # 등급 알약 (홈의 '손볼 계정', 사용률 칸, 계정 상세). 기준 색의 옅은 바탕에 짙은 글자.
    "tier_normal": ("#e7f4e8", "#1e5e23"),
    "tier_warn": ("#fff4cf", "#6b4e00"),
    "tier_alert": ("#ffe8d6", "#8a3800"),
    "tier_emergency": ("#ffe1e1", "#8e1c1c"),
    "tier_full": ("#f3e5f5", "#5a1780"),
}
# 등급 색 - 막대·선(`TIER_FILL`)은 `tiers.py` 그대로, 글자(`TIER_TEXT`)는 같은 색조의
# 짙은 쪽, 행 바탕(`ROW_TINTS`)은 `tiers.py` 그대로.
TIER_FILL = dict(tiers.LABELS)
TIER_TEXT = {
    tiers.NORMAL: "#2e7d32",
    tiers.WARN: "#7d5c00",
    tiers.ALERT: "#b04600",
    tiers.EMERGENCY: "#c62828",
    tiers.FULL: "#6a1b9a",
    tiers.UNKNOWN: "#5f6b75",
}
ROW_TINTS = dict(tiers.ROW_BACKGROUNDS)
# 트리맵 덩어리의 채도·명도 `(채도, 깊이마다 채도, 채도 한계, 명도, 깊이마다 명도, 명도 한계)`.
# 밝은 화면은 깊을수록 옅게(흰 바탕 쪽), 어두운 화면은 깊을수록 어둡게(바탕 쪽).
TREEMAP = (105, -34, 24, 205, 18, 252)

# 이름 목록 - 두 화면이 같은 이름을 갖는다.
TOKEN_NAMES = (
    "BG", "SURFACE", "SURFACE_SUNKEN", "BORDER", "BORDER_STRONG",
    "TEXT", "TEXT_MUTED", "TEXT_FAINT",
    "ACCENT", "ACCENT_FILL", "ACCENT_HOVER", "ACCENT_PRESSED", "ACCENT_SOFT", "ON_ACCENT",
    "PRIMARY_DISABLED", "PRIMARY_DISABLED_TEXT", "BUTTON_PRESSED",
    "DANGER", "DANGER_SOFT", "DANGER_BORDER", "TOOLTIP_BG", "TOOLTIP_TEXT",
    "GOOD_TEXT", "GOOD_SOFT", "WARN_SOFT",
    "SCAN_DONE", "SCAN_DOING", "SCAN_WAITING", "SCAN_UNSEEN", "NIGHT_SLOTS",
    "CHIPS", "TIER_FILL", "TIER_TEXT", "ROW_TINTS", "TREEMAP",
)
LIGHT_TOKENS = {name: globals()[name] for name in TOKEN_NAMES}

# 어두운 화면. 바탕은 같은 푸른 회색 계열을 어둡게 내린 것이고, 글자·선 색은
# 그 위에서 4.5:1 이 넘게 밝게 올렸다 (`tests/test_theme.py`).
_DARK_ACCENT = "#6b98ff"
_DARK_ACCENT_FILL = "#2d5fe0"
_DARK_TEXT_MUTED = "#9fb2c5"
DARK_TOKENS = {
    "BG": "#0f151c",
    "SURFACE": "#171f29",
    "SURFACE_SUNKEN": "#1d2733",
    "BORDER": "#283544",
    "BORDER_STRONG": "#36475b",
    "TEXT": "#e6edf5",
    "TEXT_MUTED": _DARK_TEXT_MUTED,
    "TEXT_FAINT": "#66798d",
    "ACCENT": _DARK_ACCENT,
    "ACCENT_FILL": _DARK_ACCENT_FILL,
    "ACCENT_HOVER": "#3a6cf0",
    "ACCENT_PRESSED": "#244fc0",
    "ACCENT_SOFT": "#1c2d52",
    "ON_ACCENT": "#ffffff",
    "PRIMARY_DISABLED": "#25385f",
    "PRIMARY_DISABLED_TEXT": "#7f93b8",
    "BUTTON_PRESSED": "#222e3b",
    "DANGER": "#ff6b6b",
    "DANGER_SOFT": "#3a1d22",
    "DANGER_BORDER": "#7a3a40",
    "TOOLTIP_BG": "#283442",
    "TOOLTIP_TEXT": "#e6edf5",
    "GOOD_TEXT": "#7bd389",
    "GOOD_SOFT": "#16291b",
    "WARN_SOFT": "#33240f",
    "SCAN_DONE": _DARK_ACCENT,
    "SCAN_DOING": "#3f63b8",
    "SCAN_WAITING": "#2b3846",
    "SCAN_UNSEEN": "#ff8a3d",
    "NIGHT_SLOTS": (_DARK_ACCENT, "#3f69cf", "#2f4f9a", "#253c70"),
    "CHIPS": {
        "done": ("#1c2d52", "#a9c2ff"),
        "doing": (_DARK_ACCENT_FILL, "#ffffff"),
        "soft": ("#212c38", _DARK_TEXT_MUTED),
        "stale": ("#3a2512", "#ffb27a"),
        "unseen": ("#171f29", "#b7c3cf"),
        "tier_normal": ("#173022", "#7bd389"),
        "tier_warn": ("#3a3112", "#f2c94c"),
        "tier_alert": ("#3a2512", "#ffb27a"),
        "tier_emergency": ("#3d1c20", "#ff9e9e"),
        "tier_full": ("#2f1f40", "#d7a8f7"),
    },
    "TIER_FILL": {
        tiers.NORMAL: "#43a85a",
        tiers.WARN: "#e0b23a",
        tiers.ALERT: "#f08a3c",
        tiers.EMERGENCY: "#f25f5c",
        tiers.FULL: "#b07ae0",
        tiers.UNKNOWN: "#7f8c99",
    },
    "TIER_TEXT": {
        tiers.NORMAL: "#7bd389",
        tiers.WARN: "#f2c94c",
        tiers.ALERT: "#ffb27a",
        tiers.EMERGENCY: "#ff9e9e",
        tiers.FULL: "#d7a8f7",
        tiers.UNKNOWN: "#a7b3bf",
    },
    "ROW_TINTS": {
        tiers.WARN: "#2a2513",
        tiers.ALERT: "#2e2014",
        tiers.EMERGENCY: "#321c20",
        tiers.FULL: "#291e36",
    },
    "TREEMAP": (110, -22, 40, 104, -14, 52),
}

LIGHT = "light"
DARK = "dark"
MODES = (LIGHT, DARK)
_mode = LIGHT

# 등급 -> 알약 종류. 정상도 알약이 있다 (계정 상세의 등급 배지).
_TIER_CHIPS = {
    tiers.NORMAL: "tier_normal", tiers.WARN: "tier_warn", tiers.ALERT: "tier_alert",
    tiers.EMERGENCY: "tier_emergency", tiers.FULL: "tier_full",
}


def use(name: str) -> None:
    """화면 색을 바꾼다 - 이 모듈의 이름들에 그 화면의 값을 넣는다.

    이미 그려진 위젯은 저절로 안 바뀐다. 앱에는 `restyle()`, 창에는 다시 그리기가
    따라야 한다 (`MainWindow._set_dark`)."""

    global _mode
    _mode = DARK if name == DARK else LIGHT
    globals().update(DARK_TOKENS if _mode == DARK else LIGHT_TOKENS)


def mode() -> str:
    return _mode


def is_dark() -> bool:
    return _mode == DARK


def tier_color(tier: str) -> str:
    """막대·선·점에 쓰는 등급 색."""

    return TIER_FILL.get(tier, TIER_FILL[tiers.UNKNOWN])


def tier_text(tier: str) -> str:
    """글자에 쓰는 등급 색 - 같은 색조에서 지금 바탕 위에 읽히는 쪽."""

    return TIER_TEXT.get(tier, TIER_TEXT[tiers.UNKNOWN])


def row_tint(tier: str):
    """표 행 바탕. 정상·모름은 칠하지 않는다 (None)."""

    return ROW_TINTS.get(tier)


def tier_chip(tier: str):
    """등급 알약 `(배경, 글자)`."""

    return CHIPS[_TIER_CHIPS.get(tier, "soft")]


def _luminance(color: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def ink_on(color: str) -> str:
    """색 면(오늘 밤 순번 네모 등) 위의 글자 - 흰 글자와 짙은 글자 중 더 읽히는 쪽.
    짙은 글자는 밝은 화면이면 본문색, 어두운 화면이면 창 바탕색 (오려 낸 듯 보인다)."""

    ground = _luminance(color)

    def contrast(ink: str) -> float:
        high, low = sorted((ground, _luminance(ink)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    return max((ON_ACCENT, BG if is_dark() else TEXT), key=contrast)


def treemap_fill(hue: int, depth: int):
    """트리맵 덩어리 칠 `(색조, 채도, 명도)` - 깊을수록 바탕 쪽으로."""

    sat, sat_step, sat_bound, val, val_step, val_bound = TREEMAP
    saturation = max(sat_bound, sat + depth * sat_step)
    value = val + depth * val_step
    value = min(val_bound, value) if val_step > 0 else max(val_bound, value)
    return hue, saturation, value


def recede(color, amount: int):
    """`QColor` 를 바탕 쪽으로 한 단계 - 밝은 화면은 어둡게, 어두운 화면은 밝게
    (흰 바탕 위 회색은 짙어져야, 검은 바탕 위 회색은 옅어져야 눈에 띈다)."""

    return color.lighter(amount) if is_dark() else color.darker(amount)


def palette_values(name: str) -> list:
    """그 화면이 쓰는 색 값 전부 (시험이 '박힌 색이 없는가' 를 볼 때 쓴다)."""

    tokens = DARK_TOKENS if name == DARK else LIGHT_TOKENS
    values = []

    def collect(value) -> None:
        if isinstance(value, str) and value.startswith("#"):
            values.append(value)
        elif isinstance(value, dict):
            for inner in value.values():
                collect(inner)
        elif isinstance(value, (tuple, list)):
            for inner in value:
                collect(inner)

    collect(list(tokens.values()))
    return values


# -- 타이포 스케일 ----------------------------------------------------------
FONT_DISPLAY = 30   # 히어로 숫자
FONT_TITLE = 20     # 화면·섹션 제목
FONT_SUBTITLE = 15  # 카드 안 소제목
FONT_BODY = 14      # 본문·표
FONT_CAPTION = 12   # 보조 설명·단위

WEIGHT_BOLD = "bold"
WEIGHT_HEAVY = "900"  # -> ExtraBold

RADIUS = "10px"      # 버튼·입력칸
RADIUS_CARD = "16px"  # 카드

# 칸에 위젯(콤보, 배지, 막대)을 넣는 표의 objectName. 이 이름을 단 표는 아래
# `::item` 세로 패딩에서 빠진다 - 그 패딩이 글자뿐 아니라 **칸 위젯의 기하까지**
# 밀어 넣기 때문이다. 테마와 화면이 같은 문자열을 봐야 하므로 여기 한 곳에 둔다.
TABLE_WITH_WIDGETS = "cellWidgetTable"

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
    background: {TOOLTIP_BG};
    color: {TOOLTIP_TEXT};
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
QLabel#captionWarn {{ color: {DANGER}; font-size: {FONT_CAPTION}px; }}
QLabel#statLabel {{ color: {TEXT_MUTED}; font-size: {FONT_CAPTION}px; }}
QLabel#statValue {{ font-size: {FONT_TITLE}px; font-weight: {WEIGHT_HEAVY}; }}
/* 살펴볼 것 목록: 카드 위에 얹히므로 자기 배경과 테두리를 갖지 않는다. */
QListWidget#findings {{ background: transparent; border: none; }}
QListWidget#findings::item {{ padding: 9px 4px; border-bottom: 1px solid {BORDER}; }}
QListWidget#findings::item:hover {{ background: {SURFACE_SUNKEN}; }}

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
QPushButton:pressed {{ background: {BUTTON_PRESSED}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; background: {SURFACE_SUNKEN}; border-color: {BORDER}; }}

/* 주요 동작 하나만 채운 버튼으로 둔다 - 전부 강조하면 아무것도 강조되지 않는다 */
QPushButton#primary {{
    background: {ACCENT_FILL};
    color: {ON_ACCENT};
    border: 1px solid {ACCENT_FILL};
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#primary:disabled {{
    background: {PRIMARY_DISABLED}; border-color: {PRIMARY_DISABLED}; color: {PRIMARY_DISABLED_TEXT};
}}

/* 되돌리기 어려운 동작은 색으로 구분한다 */
QPushButton#danger {{ color: {DANGER}; border-color: {DANGER_BORDER}; }}
QPushButton#danger:hover {{ background: {DANGER_SOFT}; border-color: {DANGER}; }}
QPushButton#danger:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; background: {SURFACE_SUNKEN}; }}

/* ---------- 입력 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {SURFACE};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS};
    padding: 8px 12px;
    selection-background-color: {ACCENT_FILL};
    selection-color: {ON_ACCENT};
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
/* 칸에 위젯을 넣는 표는 세로 패딩을 뺀다.
 *
 * `::item` 의 패딩은 글자만 밀어 넣는 것이 아니라 **칸 위젯의 기하까지** 밀어
 * 넣는다. 9px 짜리 위아래 패딩이면 41px 칸에 위젯이 23px 만 남고, 콤보는 자기
 * 안쪽 여백에 밀려 글자가 몇 픽셀만 보인다. 행 높이를 아무리 올려도 패딩이
 * 같은 비율로 먹으므로 해결되지 않는다 - 빼는 것이 답이다.
 *
 * 가로 패딩은 남긴다. 글자 칸이 격자에 딱 붙는 것을 막아 준다.
 * 글자는 어차피 세로 가운데 정렬이라 세로 패딩이 없어도 자리가 같다. */
QTableWidget#{TABLE_WITH_WIDGETS}::item {{ padding: 0px 8px; }}
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

/* ---------- 탭 ---------- */
/* 선택된 탭만 카드처럼 띄우고 나머지는 배경에 눕힌다. 탭이 둘뿐이라
   지금 어디에 있는지가 한눈에 보여야 한다. */
QTabWidget::pane {{
    border: none;
    background: {BG};
    top: -1px;
}}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_MUTED};
    border: none;
    padding: 10px 20px;
    margin-right: 2px;
    font-weight: {WEIGHT_BOLD};
    border-top-left-radius: {RADIUS};
    border-top-right-radius: {RADIUS};
}}
QTabBar::tab:selected {{ background: {BG}; color: {ACCENT}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

/* 카드 안의 하위 탭. 바깥 탭처럼 면을 띄우면 흰 카드 위에 회색 덩어리가
   생겨 어수선하다 - 밑줄 하나로 지금 어디인지만 알린다. */
QTabBar#subTabs::tab {{
    background: transparent;
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    padding: 8px 14px;
    margin-right: 6px;
}}
QTabBar#subTabs::tab:selected {{
    background: transparent;
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar#subTabs::tab:hover:!selected {{ color: {TEXT}; }}
QFrame#subTabsRule {{ background: {BORDER}; border: none; }}
/* 하위 탭의 쪽들은 카드의 흰 면을 그대로 쓴다. 전역 `QWidget` 규칙을 두면
   쪽마다 창 배경색이 깔려 카드 안에 회색 판이 하나 더 생긴다. */
QStackedWidget#subPages, QWidget#subPage {{ background: transparent; }}

/* ---------- 머리줄 (홈·상세 스캔 이동) ---------- */
/* 탭 띠 대신 머리줄의 두 단추로 오간다. 지금 있는 쪽만 흰 면으로 띄운다. */
QFrame#nav {{ background: {BORDER}; border: none; border-radius: 12px; }}
QPushButton#navButton {{
    background: transparent; border: none; border-radius: 9px;
    padding: 7px 18px; color: {TEXT_MUTED}; min-height: 18px;
}}
QPushButton#navButton:checked {{ background: {SURFACE}; color: {TEXT}; }}
QPushButton#navButton:hover:!checked {{ color: {TEXT}; background: transparent; }}
QToolButton#tools {{
    background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS};
    padding: 8px 14px; font-weight: {WEIGHT_BOLD};
}}
QToolButton#tools:hover {{ background: {SURFACE_SUNKEN}; border-color: {ACCENT}; }}
QToolButton#tools::menu-indicator {{ subcontrol-position: right center; right: 6px; }}
/* 홈 타일 - 눌러서 상세 스캔으로 가는 타일은 손 모양과 테두리로 알린다. */
QFrame#tileLink {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_CARD};
}}
QFrame#tileLink:hover {{ border-color: {ACCENT}; }}

/* ---------- 펼침 버튼 ---------- */
/* 접힌 설정을 여는 버튼. 일반 버튼처럼 보이면 "눌러야 하는 것"으로 읽히므로,
   테두리 없이 제목처럼 두고 화살표로만 상태를 알린다. */
QToolButton#disclosure {{
    border: none;
    background: transparent;
    padding: 6px 2px;
    font-size: {FONT_SUBTITLE}px;
    font-weight: {WEIGHT_BOLD};
    color: {TEXT};
}}
QToolButton#disclosure:hover {{ color: {ACCENT}; }}

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


def qpalette():
    """Qt 팔레트 - QSS 가 닿지 않는 곳(체크 상자 안, 메시지 상자, 비활성 글자)도
    같은 색을 쓰게 한다. 없으면 어두운 화면에서 체크 상자만 흰 네모로 뜬다."""

    from PyQt5.QtGui import QColor, QPalette

    palette = QPalette()
    roles = {
        QPalette.Window: BG, QPalette.WindowText: TEXT,
        QPalette.Base: SURFACE, QPalette.AlternateBase: SURFACE_SUNKEN,
        QPalette.Text: TEXT, QPalette.Button: SURFACE, QPalette.ButtonText: TEXT,
        QPalette.BrightText: DANGER, QPalette.Highlight: ACCENT_FILL,
        QPalette.HighlightedText: ON_ACCENT, QPalette.Link: ACCENT,
        QPalette.ToolTipBase: TOOLTIP_BG, QPalette.ToolTipText: TOOLTIP_TEXT,
        QPalette.Light: SURFACE, QPalette.Midlight: BORDER, QPalette.Mid: BORDER_STRONG,
        QPalette.Dark: BORDER_STRONG, QPalette.Shadow: BORDER_STRONG,
    }
    placeholder = getattr(QPalette, "PlaceholderText", None)  # Qt 5.12+
    if placeholder is not None:
        roles[placeholder] = TEXT_FAINT
    for role, value in roles.items():
        palette.setColor(role, QColor(value))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(TEXT_FAINT))
    return palette


def restyle(app) -> None:
    """지금 화면 색으로 앱 팔레트와 QSS 를 다시 입힌다."""

    app.setPalette(qpalette())
    app.setStyleSheet(stylesheet())


def _settings():
    from PyQt5.QtCore import QSettings

    return QSettings(QSettings.IniFormat, QSettings.UserScope, "smvwp", "storage_manager_vwp")


def saved_mode() -> str:
    """이 PC 사용자가 고른 화면 (없거나 못 읽으면 밝은 화면)."""

    try:
        value = _settings().value("theme", LIGHT)
    except Exception:  # noqa: BLE001 - 표시 설정일 뿐, 못 읽어도 창은 떠야 한다
        return LIGHT
    return value if value in MODES else LIGHT


def save_mode(name: str) -> None:
    """고른 화면을 남긴다. 못 남겨도 이번 창은 그대로 바뀐다."""

    try:
        settings = _settings()
        settings.setValue("theme", name)
        settings.sync()
    except Exception:  # noqa: BLE001 - 표시 설정일 뿐
        pass


def apply(app, name: str = LIGHT) -> None:
    """`QApplication`에 폰트와 테마를 적용한다.

    Fusion 스타일을 먼저 지정한다 - 플랫폼 기본 스타일은 QSS를 부분적으로만
    받아들여 장비마다 다르게 보이는데, Fusion은 어디서나 같게 그려진다.
    반입 대상이 여러 장비라 이 일관성이 중요하다."""

    from PyQt5.QtGui import QFont

    use(name)
    app.setStyle("Fusion")
    load_fonts()
    # QSS의 font-family만으로도 대부분 먹지만, 기본 폰트를 함께 지정해야
    # QSS가 닿지 않는 곳(네이티브 파일 대화상자 등)까지 일관되게 보인다.
    # 스케일 값은 QSS와 같은 px 기준이므로 setPixelSize를 쓴다 (setPointSize를
    # 쓰면 같은 숫자가 훨씬 큰 글자가 되어 QSS와 어긋난다).
    base_font = QFont(FONT_FAMILY)
    base_font.setPixelSize(FONT_BODY)
    app.setFont(base_font)
    restyle(app)
