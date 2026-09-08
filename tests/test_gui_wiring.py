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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
