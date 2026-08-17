"""PyQt5가 없는 장비에서 `gui`가 어떻게 실패하는지에 대한 테스트.

이 경로는 "화면이 안 뜬다"를 사용자가 스스로 짚을 수 있게 하려고 둔 것이다.
안내 문구 상수의 이름이 어긋나 `NameError`로 끝난 적이 있는데, 그러면 원래
막으려던 스택트레이스가 그대로 뜨면서 안내가 통째로 사라진다. 실제 실행 없이
잡히지 않는 실수라 여기서 고정한다.
"""

import io
import sys
import types
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

import smvwp_cli


class GuiWithoutPyQt5Tests(unittest.TestCase):
    def _run_without_pyqt5(self):
        args = types.SimpleNamespace(data_dir=None)
        stderr = io.StringIO()
        # sys.modules에 None을 넣으면 그 이름의 import가 ImportError로 끝난다.
        # PyQt5가 실제로 깔려 있는 개발 PC에서도 같은 결과를 얻기 위한 방법이다.
        with patch.dict(sys.modules, {"PyQt5": None, "PyQt5.QtWidgets": None}):
            with redirect_stderr(stderr):
                code = smvwp_cli.command_gui(args)
        return code, stderr.getvalue()

    def test_exits_with_code_2(self):
        code, _ = self._run_without_pyqt5()
        self.assertEqual(code, 2)

    def test_guidance_is_printed_instead_of_a_traceback(self):
        _, message = self._run_without_pyqt5()
        self.assertIn("PyQt5", message)
        # 다른 Python을 가리키게 하는 것이 이 상황의 유일한 해결책이라 반드시
        # 환경변수 이름이 보여야 한다.
        self.assertIn("STORAGE_MANAGER_PYTHON_BIN", message)
        # GUI가 안 떠도 수집은 돌아간다는 사실을 여기서 알려야 한다.
        self.assertIn("collect", message)

    def test_message_template_has_no_unfilled_placeholders(self):
        """포맷 키가 남아 있으면 안내가 `{python}` 같은 글자로 새어 나온다."""

        _, message = self._run_without_pyqt5()
        self.assertNotIn("{", message)


if __name__ == "__main__":
    unittest.main()
