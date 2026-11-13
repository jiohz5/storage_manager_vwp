"""밝은 화면과 어두운 화면 - 색 묶음 둘이 같은 약속을 지키는가.

어두운 화면은 색을 뒤집는 것이 아니라 **같은 이름에 다른 값**을 주는 것이다
(`theme.LIGHT` / `theme.DARK`). 화면 코드는 이름만 쓴다. 그래서 여기서 보는
것은 셋이다.

- 두 묶음이 같은 이름을 갖는가 (한쪽에만 있는 이름은 다른 쪽에서 옛 값으로 남는다).
- 글자가 그 바탕 위에서 읽히는가 (WCAG 4.5:1 - 본문, 알약, 등급 글자 모두).
- 화면 코드가 색을 직접 박지 않는가 - 박아 둔 색은 어두운 화면에서 혼자 밝게 남는다.

Qt 없이 돈다 (`theme` 은 PyQt5 를 함수 안에서만 가져온다).
"""

import re
import unittest
from pathlib import Path

from smvwp import tiers
from smvwp.gui import theme

GUI_DIR = Path(__file__).resolve().parents[1] / "smvwp" / "gui"
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class _BothModes(unittest.TestCase):
    def setUp(self):
        self.addCleanup(theme.use, theme.LIGHT)

    def modes(self):
        for mode in theme.MODES:
            theme.use(mode)
            yield mode


class TokenTests(_BothModes):
    def test_both_palettes_name_the_same_things(self):
        self.assertEqual(set(theme.LIGHT_TOKENS), set(theme.DARK_TOKENS))
        self.assertEqual(set(theme.LIGHT_TOKENS["CHIPS"]), set(theme.DARK_TOKENS["CHIPS"]))

    def test_use_switches_the_module_values(self):
        theme.use(theme.DARK)
        self.assertTrue(theme.is_dark())
        self.assertEqual(theme.BG, theme.DARK_TOKENS["BG"])
        theme.use(theme.LIGHT)
        self.assertFalse(theme.is_dark())
        self.assertEqual(theme.BG, theme.LIGHT_TOKENS["BG"])

    def test_an_unknown_mode_is_light(self):
        theme.use("purple")
        self.assertEqual(theme.mode(), theme.LIGHT)

    def test_light_fills_keep_the_tier_colors(self):
        """막대·선의 등급 색은 밝은 화면에서 `tiers.py` 그대로다 - 보고서와 같은 색."""

        theme.use(theme.LIGHT)
        for tier in tiers.LABELS:
            self.assertEqual(theme.tier_color(tier), tiers.color(tier))


class ReadabilityTests(_BothModes):
    def check(self, fg, bg, at_least, what):
        self.assertGreaterEqual(
            contrast(fg, bg), at_least, f"{theme.mode()}: {what} {fg} on {bg} = {contrast(fg, bg):.2f}"
        )

    def test_body_text(self):
        for _ in self.modes():
            for ground in ("BG", "SURFACE", "SURFACE_SUNKEN"):
                self.check(theme.TEXT, getattr(theme, ground), 7.0, f"TEXT/{ground}")
                self.check(theme.TEXT_MUTED, getattr(theme, ground), 4.5, f"TEXT_MUTED/{ground}")
            # 플레이스홀더는 흐린 것이 일이다 - 다만 안 보일 만큼은 아니다.
            self.check(theme.TEXT_FAINT, theme.SURFACE, 2.4, "TEXT_FAINT")

    def test_accent_text_and_buttons(self):
        for _ in self.modes():
            self.check(theme.ACCENT, theme.SURFACE, 4.5, "ACCENT text")
            self.check(theme.ACCENT, theme.ACCENT_SOFT, 4.5, "selected menu item")
            self.check(theme.ON_ACCENT, theme.ACCENT_FILL, 4.5, "primary button")
            self.check(theme.ON_ACCENT, theme.ACCENT_HOVER, 4.5, "primary button hover")
            self.check(theme.DANGER, theme.SURFACE, 4.5, "danger")
            self.check(theme.DANGER, theme.DANGER_SOFT, 4.5, "danger hover")
            self.check(theme.TOOLTIP_TEXT, theme.TOOLTIP_BG, 4.5, "tooltip")
            self.check(theme.GOOD_TEXT, theme.SURFACE, 4.5, "good")

    def test_every_chip(self):
        for _ in self.modes():
            for kind, (background, foreground) in theme.CHIPS.items():
                self.check(foreground, background, 4.5, f"chip {kind}")

    def test_tier_words_are_readable_on_the_card_and_on_their_row(self):
        """노랑(#f9a825) 글자는 흰 바탕에서 1.9:1 이다 - 등급 글자는 같은 색조의
        짙은 쪽(밝은 화면) / 옅은 쪽(어두운 화면)을 쓴다."""

        for _ in self.modes():
            for tier in tiers.LABELS:
                self.check(theme.tier_text(tier), theme.SURFACE, 4.5, f"tier text {tier}")
                tint = theme.row_tint(tier)
                if tint:
                    self.check(theme.tier_text(tier), tint, 4.5, f"tier text {tier} on its row")
                    self.check(theme.TEXT, tint, 7.0, f"TEXT on {tier} row")

    def test_tier_badges(self):
        for _ in self.modes():
            for tier in tiers.LABELS:
                background, foreground = theme.tier_chip(tier)
                self.check(foreground, background, 4.5, f"badge {tier}")

    def test_ink_on_colored_squares(self):
        for _ in self.modes():
            for color in theme.NIGHT_SLOTS:
                self.check(theme.ink_on(color), color, 4.5, "night slot number")


class ColorRolesTests(ReadabilityTests):
    """색 하나에 뜻 하나 - 위험(사용률 등급), 변화(늘고 준 양), 자료 품질(오래된 스캔·덜
    센 곳)을 다른 색으로.

    주황이 다섯 가지를 겸했다: 경고 등급, 지난 스캔보다 늘었음, 눈에 띄는 큰 파일, 며칠
    전에 끝난 스캔, 못 잰 곳. "주황 = 손봐야 함" 으로 읽은 사람이 그냥 는 폴더를 위험으로
    봤다. 등급은 이미 초록·노랑·주황·빨강·보라를 다 쓰므로, 변화는 청록(는 것)과 회청
    (준 것), 자료 품질은 돌빛 회색으로 둔다."""

    def test_change_colors_read_on_cards(self):
        for _ in self.modes():
            self.check(theme.CHANGE_UP, theme.SURFACE, 4.5, "CHANGE_UP")
            self.check(theme.CHANGE_DOWN, theme.SURFACE, 4.5, "CHANGE_DOWN")

    def test_change_colors_are_not_tier_colors(self):
        for _ in self.modes():
            tier_colors = {theme.tier_color(t).lower() for t in tiers.LABELS} | {
                theme.tier_text(t).lower() for t in tiers.LABELS}
            for name in ("CHANGE_UP", "CHANGE_UP_FILL", "CHANGE_DOWN", "CHANGE_DOWN_FILL"):
                self.assertNotIn(getattr(theme, name).lower(), tier_colors, name)

    def test_quality_chips_are_not_risk_chips(self):
        for _ in self.modes():
            risk = {theme.CHIPS[kind] for kind in ("tier_warn", "tier_alert", "tier_emergency")}
            for kind in ("stale", "change_up", "change_down"):
                self.assertNotIn(theme.CHIPS[kind], risk, kind)


class StylesheetTests(_BothModes):
    def test_the_stylesheet_uses_only_palette_values(self):
        """QSS 안에 박힌 색(`#ffffff`, `#e8eef4` ...)은 어두운 화면에서 혼자 밝게 남는다."""

        for mode in self.modes():
            allowed = set()
            for value in theme.palette_values(mode):
                allowed.add(value.lower())
            used = {value.lower() for value in HEX.findall(theme.stylesheet())}
            self.assertFalse(used - allowed, f"{mode}: {sorted(used - allowed)}")

    def test_dark_stylesheet_has_dark_ground(self):
        theme.use(theme.DARK)
        sheet = theme.stylesheet()
        self.assertIn(theme.DARK_TOKENS["BG"], sheet)
        self.assertNotIn(theme.LIGHT_TOKENS["BG"], sheet)


class GuiSourceTests(unittest.TestCase):
    """화면 코드는 색을 이름으로만 쓴다."""

    def gui_sources(self):
        for path in sorted(GUI_DIR.glob("*.py")):
            if path.name != "theme.py":
                yield path, path.read_text(encoding="utf-8")

    def test_no_literal_colors_outside_the_theme(self):
        found = []
        for path, text in self.gui_sources():
            for number, line in enumerate(text.splitlines(), 1):
                if HEX.search(line) or re.search(r"\b(Qt\.white|Qt\.black)\b|:\s*white;", line):
                    found.append(f"{path.name}:{number}: {line.strip()}")
        self.assertEqual(found, [])

    def test_tier_colors_come_through_the_theme(self):
        """`tiers.color` 는 밝은 바탕용 한 벌뿐이다 - 화면은 `theme.tier_color` /
        `theme.tier_text` 로 지금 화면에 맞는 쪽을 받는다."""

        found = []
        for path, text in self.gui_sources():
            for number, line in enumerate(text.splitlines(), 1):
                if re.search(r"tiers\.(color|row_background)\(", line):
                    found.append(f"{path.name}:{number}: {line.strip()}")
        self.assertEqual(found, [])

    def test_no_import_time_color_tables(self):
        """모듈을 읽을 때 색을 표에 담아 두면 (`STATE_COLORS = {..: theme.ACCENT}`) 화면을
        바꿔도 그 표만 옛 색으로 남는다."""

        import ast

        found = []
        for path, text in self.gui_sources():
            for node in ast.parse(text).body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                for inner in ast.walk(node.value):
                    if (isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name)
                            and inner.value.id == "theme" and inner.attr.isupper()):
                        found.append(f"{path.name}:{node.lineno}: theme.{inner.attr}")
        self.assertEqual(found, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
