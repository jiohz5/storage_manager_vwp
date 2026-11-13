"""어느 판이 깔려 있나 - 폐쇄망에서는 화면과 명령에서 판을 알아야 한다.

반입은 ZIP 을 풀어 `smvwp/` 를 통째로 갈아 끼우는 것이라, 실기에서 "그 고친 것이
들어갔나" 를 git 으로 확인할 수 없다. 예전 `__version__` 은 `0.1.0-phase1` 그대로
어디에서도 보이지 않았다."""

import io
import re
import unittest
from contextlib import redirect_stdout

import smvwp


class VersionTests(unittest.TestCase):
    def test_the_version_is_a_date(self):
        self.assertRegex(smvwp.__version__, r"^\d{4}\.\d{2}\.\d{2}(\.\d+)?$")

    def test_the_cli_says_it(self):
        import smvwp_cli

        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            smvwp_cli.build_parser().parse_args(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn(smvwp.__version__, out.getvalue())

    def test_the_window_title_says_it(self):
        from smvwp import i18n

        for language in ("ko", "en"):
            i18n.set_language(language)
            self.assertIn("{version}", i18n.t("app.title_versioned", version="{version}"))
        i18n.set_language("ko")
        self.assertTrue(re.search(re.escape(smvwp.__version__),
                                  i18n.t("app.title_versioned", version=smvwp.__version__)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
