"""GUI 클래스의 배선을 **소스만 읽어서** 확인한다.

개발 PC 에 PyQt5 가 없어 GUI 는 임포트조차 못 한다. 그렇다고 배선을 아무것도
확인하지 않으면, 창을 쪼개는 것 같은 큰 변경에서 `self.없는것` 하나가 반입
장비에 가서야 터진다 - 그때는 원인을 짚기가 훨씬 어렵다.

여기서는 소스를 AST 로 읽어 다음을 본다.

- 어떤 클래스가 자기 것이 아닌 `self.속성` 을 쓰지 않는가.
- 신호로 갈라 놓은 경계가 다시 붙지 않았는가 (탭이 다른 탭 위젯을 직접
  만지면 둘을 따로 옮길 수 없게 된다).

실행 시 동작까지 보증하지는 못한다. 그건 반입 장비에서 창을 띄워 봐야 한다.
"""

import ast
import pathlib
import unittest

GUI = pathlib.Path(__file__).resolve().parent.parent / "smvwp" / "gui"

# Qt 가 물려주는 것들. 우리 클래스가 정의하지 않아도 쓸 수 있다.
QT_INHERITED = {
    "setWindowTitle", "setCentralWidget", "setLayout", "layout", "close",
    "setMinimumSize", "resize", "width", "height", "show", "hide", "setVisible",
    "isVisible", "setObjectName", "setStyleSheet", "style", "font", "fontMetrics",
    "setEnabled", "isEnabled", "parent", "children", "deleteLater", "update",
    "setToolTip", "menuBar", "statusBar", "setWindowIcon", "windowTitle",
    "setSizePolicy", "sizeHint", "setContentsMargins", "setProperty", "property",
    "setFocus", "raise_", "activateWindow", "setWindowState", "windowState",
    # QDialog 쪽
    "accept", "reject", "done", "exec_", "setModal", "result",
    # QFrame 쪽
    "setFrameShape", "setFrameShadow", "setLineWidth",
}


def _classes(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]


def _self_attrs(cls):
    """`self.X` 로 **읽는** 것과 **쓰는** 것을 갈라서 모은다."""

    read, written = set(), set()
    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id != "self":
                continue
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                written.add(node.attr)
            else:
                read.add(node.attr)
    # 메서드와 클래스 변수도 정의된 것으로 본다.
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            written.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    written.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            written.add(node.target.id)
    return read, written


class SelfAttributeTests(unittest.TestCase):
    """`self.없는것` 을 반입 장비 가기 전에 잡는다."""

    def _check(self, filename, class_name):
        path = GUI / filename
        cls = next(c for c in _classes(path) if c.name == class_name)
        read, written = _self_attrs(cls)
        undefined = sorted(read - written - QT_INHERITED)
        self.assertEqual(
            undefined, [],
            f"{class_name} 이 정의하지 않은 self 속성을 씁니다: {undefined}",
        )

    def test_main_window(self):
        self._check("main_window.py", "MainWindow")

    def test_scan_tab(self):
        self._check("scan_tab.py", "ScanTab")

    def test_account_dialog(self):
        self._check("account_dialog.py", "AccountDialog")


class TabBoundaryTests(unittest.TestCase):
    """탭이 다른 탭의 위젯을 직접 만지면 안 된다.

    직접 만지기 시작하면 둘을 따로 옮길 수 없게 되고, 창을 쪼갠 의미가
    사라진다. 경계는 신호로만 넘는다."""

    def _source(self, filename):
        return (GUI / filename).read_text(encoding="utf-8")

    def test_scan_tab_does_not_touch_home_widgets(self):
        text = self._source("scan_tab.py")
        for name in ("home_scan_banner", "home_scan_label", "home_scan_progress"):
            self.assertNotIn(name, text, f"스캔 탭이 홈 탭 위젯({name})을 직접 만집니다")

    def test_scan_tab_does_not_touch_the_status_bar(self):
        """상태 줄은 창의 것이다 - `status_message` 신호로 넘긴다."""

        self.assertNotIn("status_bar_label", self._source("scan_tab.py"))

    def test_scan_tab_does_not_touch_the_tab_strip(self):
        self.assertNotIn("self.tabs", self._source("scan_tab.py"))

    def test_main_window_does_not_reach_into_scan_widgets(self):
        """창이 탭 내부 위젯을 직접 만지면 탭을 고칠 때마다 창이 깨진다."""

        text = self._source("main_window.py")
        for name in ("scan_status_label", "scan_progress", "scan_run_btn",
                     "growth_table", "scan_accounts_table"):
            self.assertNotIn(
                f"self.{name}", text, f"창이 스캔 탭 위젯({name})을 직접 만집니다"
            )


class ConfigFreshnessTests(unittest.TestCase):
    def test_scan_tab_reads_config_through_a_callable(self):
        """설정을 붙잡아 두면 계정 대화상자에서 바뀐 뒤 낡은 것을 계속 쓴다."""

        text = (GUI / "scan_tab.py").read_text(encoding="utf-8")
        self.assertNotIn("self._config.", text)
        self.assertIn("self._get_config()", text)


class SelfCallArityTests(unittest.TestCase):
    """`self.메서드(...)` 를 실제 인자 수와 맞춰 본다.

    창을 쪼갤 때 실제로 걸린 실수다 - 인자 없는 `_sync_scan_account_combo()` 를
    새 코드에서 `(account_id)` 로 불렀다. 속성 존재만 보는 검사로는 안 잡히고,
    반입 장비에서 계정을 고르는 순간에야 TypeError 로 터진다.
    """

    def _defs(self, cls):
        out = {}
        for node in cls.body:
            if isinstance(node, ast.FunctionDef):
                args = node.args
                if any(isinstance(d, ast.Name) and d.id == "staticmethod"
                       for d in node.decorator_list):
                    positional = len(args.args)
                else:
                    positional = len(args.args) - 1   # self
                required = positional - len(args.defaults)
                has_varargs = args.vararg is not None
                out[node.name] = (required, positional, has_varargs)
        return out

    def _check(self, filename, class_name):
        path = GUI / filename
        cls = next(c for c in _classes(path) if c.name == class_name)
        defs = self._defs(cls)
        problems = []
        for node in ast.walk(cls):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "self"):
                continue
            if func.attr not in defs:
                continue
            required, positional, has_varargs = defs[func.attr]
            given = len(node.args)
            if any(isinstance(a, ast.Starred) for a in node.args):
                continue
            if given < required or (not has_varargs and given > positional):
                problems.append(
                    f"{func.attr}: {given}개를 넘겼는데 {required}~{positional}개를 받습니다"
                    f" (줄 {node.lineno})"
                )
        self.assertEqual(problems, [], f"{class_name}: " + "; ".join(problems))

    def test_main_window(self):
        self._check("main_window.py", "MainWindow")

    def test_scan_tab(self):
        self._check("scan_tab.py", "ScanTab")

    def test_account_dialog(self):
        self._check("account_dialog.py", "AccountDialog")


class TranslationKeyTests(unittest.TestCase):
    """GUI 가 부르는 i18n 키가 실제로 있는가.

    없는 키는 예외가 아니라 **키 문자열 그대로** 화면에 뜬다. 조용히 못생겨질
    뿐이라 눈으로 보기 전에는 모른다."""

    def test_every_literal_key_exists(self):
        import re

        from smvwp import i18n

        missing = []
        for path in sorted(GUI.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for key in re.findall(r'i18n\.t\(\s*"([a-z0-9_.]+)"', text):
                for language in ("ko", "en"):
                    i18n.set_language(language)
                    if i18n.t(key) == key:
                        missing.append(f"{path.name}: {key} ({language})")
        i18n.set_language("ko")
        self.assertEqual(missing, [], "없는 번역 키: " + ", ".join(missing[:10]))


class InitOrderTests(unittest.TestCase):
    """`__init__` 에서 만들기 전에 쓰지 않는가.

    실제로 반입 장비에서 터진 실수다 - `_build_ui()` 가 탭 띠에 스캔 탭을
    붙이는데, 그 탭을 그보다 **뒤에서** 만들고 있었다. 속성이 어딘가에
    대입되기만 하면 통과하는 검사로는 안 잡히고, 창을 띄우는 순간
    `AttributeError` 로 죽는다.

    `__init__` 이 부르는 자기 메서드까지 한 단계 따라 들어가 본다 - 위의
    경우가 정확히 그 모양이었다(직접 쓴 것이 아니라 `_build_ui` 안에서 썼다).
    """

    def _check(self, filename, class_name):
        path = GUI / filename
        cls = next(c for c in _classes(path) if c.name == class_name)
        methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
        init = methods.get("__init__")
        if init is None:
            return

        # 메서드마다 "이 안에서 읽는 self 속성"을 미리 모아 둔다.
        reads = {}
        for name, node in methods.items():
            reads[name] = {
                a.attr for a in ast.walk(node)
                if isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name)
                and a.value.id == "self" and isinstance(a.ctx, ast.Load)
            }

        assigned = set()
        problems = []
        for stmt in init.body:
            # 이 문장이 읽는 것들 - 자기 메서드를 부르면 그 안에서 읽는 것까지.
            used = set()
            for node in ast.walk(stmt):
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id == "self" and isinstance(node.ctx, ast.Load)):
                    used.add(node.attr)
                # **부를 때만** 안으로 따라 들어간다. `connect(self._on_x)` 는
                # 메서드를 넘기는 것이지 지금 부르는 것이 아니다 - 그때 안에서
                # 읽는 것까지 "지금 필요하다"고 보면 멀쩡한 코드가 걸린다.
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "self"
                        and node.func.attr in methods):
                    used |= reads[node.func.attr] - {node.func.attr}
            for name in sorted(used):
                # 메서드와 클래스 변수, Qt 가 물려준 것은 이미 있다.
                if name in methods or name in QT_INHERITED:
                    continue
                if name in assigned:
                    continue
                # `__init__` 어딘가에서 대입되는데 아직 안 된 것만 문제다.
                if any(
                    isinstance(a, ast.Attribute) and isinstance(a.value, ast.Name)
                    and a.value.id == "self" and a.attr == name
                    and isinstance(a.ctx, ast.Store)
                    for a in ast.walk(init)
                ):
                    problems.append(f"{name} (줄 {stmt.lineno})")
            for node in ast.walk(stmt):
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id == "self" and isinstance(node.ctx, ast.Store)):
                    assigned.add(node.attr)

        self.assertEqual(
            problems, [],
            f"{class_name}.__init__ 이 만들기 전에 씁니다: {problems}",
        )

    def test_main_window(self):
        self._check("main_window.py", "MainWindow")

    def test_scan_tab(self):
        self._check("scan_tab.py", "ScanTab")

    def test_account_dialog(self):
        self._check("account_dialog.py", "AccountDialog")


class ComboColumnWidthTests(unittest.TestCase):
    """콤보가 든 열이 잘리지 않는가 - 여러 번 되살아난 문제다.

    ## 왜 자꾸 되살아났나

    `QHeaderView.ResizeToContents` 는 **칸 위젯(`setCellWidget`)을 보지
    않는다.** 열 폭을 정할 때 항목 대리자에게 각 행의 크기를 묻는데, 콤보를
    넣은 칸의 항목은 비어 있어 "아주 좁아도 된다"는 답이 돌아온다.

    그래서 `combo.setMinimumWidth(...)` 로는 안 고쳐진다 - **위젯**은 넓어지고
    **열**은 그대로라, 칸이 위젯을 잘라 낸다. 고치는 방법은 그 열을
    `ResizeToContents` 에서 빼고 폭을 직접 지정하는 것뿐이다.

    소스만 읽는 검사라 픽셀까지 보증하지는 못한다. 다만 **잘리게 만드는 그
    조합**이 다시 들어오는 것은 잡는다.
    """

    def _source(self):
        return (GUI / "account_dialog.py").read_text(encoding="utf-8")

    def test_combo_columns_are_not_resize_to_contents(self):
        """이 한 줄이 잘림의 원인이었다."""

        text = self._source()
        self.assertIn("for column in COMBO_COLUMNS:", text)
        self.assertIn("QHeaderView.Interactive", text)

    def test_the_width_is_set_on_the_column_not_only_the_widget(self):
        """위젯만 넓히면 칸이 잘라 낸다."""

        text = self._source()
        self.assertIn("setColumnWidth(column, widest)", text)

    def test_the_row_is_tall_enough_for_the_combo(self):
        """행이 콤보보다 낮으면 **글자가 세로로 잘린다.**

        가로 잘림보다 알아채기 어렵다 - 뒷글자가 사라지는 것이 아니라 위아래
        획이 깎여 "글자가 조금만 보인다"가 된다. 실기에서 행 34px 에 콤보가
        37px 를 요구하고 있었다."""

        text = self._source()
        self.assertIn("sizeHint().height()", text)
        self.assertIn("setRowHeight(row, height)", text)

    def test_the_height_is_measured_not_hardcoded(self):
        """상수로 박아 두면 글꼴이나 테마 여백이 바뀔 때 또 잘린다."""

        tree = ast.parse(self._source())
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_fit_combo_cells"
        )
        measured = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "height"
            for n in ast.walk(fn)
        )
        self.assertTrue(measured, "높이를 위젯에게 묻지 않고 있습니다")

    def test_every_combo_column_is_measured(self):
        """열을 더하면서 `COMBO_COLUMNS` 를 잊으면 그 열만 다시 잘린다."""

        tree = ast.parse(self._source())
        combo_columns = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(x, ast.Name) and x.id == "COMBO_COLUMNS"
                            for x in node.targets)):
                combo_columns = {
                    e.id for e in ast.walk(node.value) if isinstance(e, ast.Name)
                }
        self.assertIsNotNone(combo_columns, "COMBO_COLUMNS 가 없습니다")

        # `setCellWidget(row, X, ...)` 로 위젯을 넣는 열은 전부 목록에 있어야 한다.
        placed = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setCellWidget" and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Name)):
                placed.add(node.args[1].id)
        self.assertEqual(
            placed - combo_columns, set(),
            f"칸 위젯을 넣는데 COMBO_COLUMNS 에 없는 열: {placed - combo_columns}",
        )

    def test_the_measurement_runs_after_the_rows_are_filled(self):
        """행을 채우기 전에 재면 잴 콤보가 없어 머리글 폭만 나온다."""

        tree = ast.parse(self._source())
        reload_fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_reload_list"
        )
        calls = [
            n.lineno for n in ast.walk(reload_fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_fit_combo_cells"
        ]
        self.assertTrue(calls, "_reload_list 가 _fit_combo_cells 를 부르지 않습니다")
        widget_calls = [
            n.lineno for n in ast.walk(reload_fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "setCellWidget"
        ]
        self.assertTrue(widget_calls)
        self.assertGreater(
            min(calls), max(widget_calls),
            "행을 채우기 전에 열 폭을 재고 있습니다",
        )


class CardStyleTests(unittest.TestCase):
    """테마의 카드 스타일(`QFrame#card`)은 QFrame 에만 붙는다.

    스캔 탭을 떼어 낼 때 QWidget 으로 두었더니 테두리와 배경이 조용히 사라졌다.
    화면은 뜨고 동작도 하니 다른 검사는 아무것도 못 잡는다 - 그래서 실제로 걸린
    곳 하나를 못박아 둔다.
    """

    def test_scan_tab_is_a_frame(self):
        tree = ast.parse((GUI / "scan_tab.py").read_text(encoding="utf-8"))
        cls = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef) and n.name == "ScanTab")
        bases = {b.id for b in cls.bases if isinstance(b, ast.Name)}
        self.assertIn("QFrame", bases, "ScanTab 이 QFrame 이 아니면 카드 스타일이 안 붙습니다")

    def test_scan_tab_asks_for_the_card_style(self):
        text = (GUI / "scan_tab.py").read_text(encoding="utf-8")
        self.assertIn('self.setObjectName("card")', text)

    def test_card_style_targets_qframe_only(self):
        """전제 확인 - 테마가 바뀌어 QWidget 에도 붙게 되면 위 검사는 뜻을 잃는다."""

        theme = (GUI / "theme.py").read_text(encoding="utf-8")
        self.assertIn("QFrame#card", theme)


class RepolishTests(unittest.TestCase):
    """objectName 을 바꾼 뒤에는 unpolish/polish 로 되물려야 한다.

    `setStyleSheet("")` 는 빈 스타일시트가 그대로 빈 것이면 Qt 가 아무것도
    하지 않아, cron 경고가 빨간색으로 안 켜졌다."""

    def test_cron_label_is_repolished_after_renaming(self):
        text = (GUI / "scan_tab.py").read_text(encoding="utf-8")
        self.assertIn("style.unpolish(label)", text)
        self.assertIn("style.polish(label)", text)
        # 주석이 아니라 **코드**에서 빈 스타일시트 호출이 없어야 한다.
        tree = ast.parse(text)
        empty_calls = [
            n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "setStyleSheet"
            and n.args and isinstance(n.args[0], ast.Constant)
            and n.args[0].value == ""
        ]
        self.assertEqual(empty_calls, [], f"빈 setStyleSheet 호출: 줄 {empty_calls}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
