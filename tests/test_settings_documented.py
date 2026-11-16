"""설정은 화면이나 문서 어딘가에 자리가 있어야 한다.

2026-11 검토에서 설정 58개 가운데 27개가 화면에도 README·OPERATIONS 에도 없었다. 그중
`cleanup_min_age_days`·`cleanup_idle_days` 둘은 보고서 머리말이 "정리 후보의 조건" 이라고
적었지만 **어디서도 읽히지 않았다** - 바꿔도 아무 일도 없었다. 문서에 적으려다 드러났다.
"""

import dataclasses
import glob
import io
import unittest
from pathlib import Path

from smvwp import config as config_module

ROOT = Path(__file__).resolve().parent.parent


def _read(paths):
    return "\n".join(io.open(p, encoding="utf-8").read() for p in paths)


class SettingsDocumentedTests(unittest.TestCase):
    def test_every_setting_is_on_a_screen_or_in_the_docs(self):
        places = _read([ROOT / "README.md", ROOT / "OPERATIONS.md"]
                       + glob.glob(str(ROOT / "smvwp" / "gui" / "*.py")))
        missing = [spec.name for spec in dataclasses.fields(config_module.Settings)
                   if f"`{spec.name}`" not in places and f'"{spec.name}"' not in places
                   and f".{spec.name}" not in places]
        self.assertEqual(missing, [], "OPERATIONS.md '화면에 없는 설정' 표에 더하세요")

    def test_every_setting_is_read_somewhere(self):
        """이름만 있고 아무도 읽지 않는 설정은 바꾼 사람을 속인다."""

        code = _read([p for p in glob.glob(str(ROOT / "smvwp" / "**" / "*.py"), recursive=True)
                      if not p.endswith("config.py") and "locales" not in p])
        unread = [spec.name for spec in dataclasses.fields(config_module.Settings)
                  if f".{spec.name}" not in code and f'"{spec.name}"' not in code]
        self.assertEqual(unread, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
