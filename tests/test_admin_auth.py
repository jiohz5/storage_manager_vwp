import unittest
from pathlib import Path

from storage_manager.admin_auth import verify_admin_pin


class AdminAuthTests(unittest.TestCase):
    def test_only_configured_pin_is_accepted_without_plaintext_in_source(self):
        self.assertTrue(verify_admin_pin("6368"))
        self.assertFalse(verify_admin_pin("6369"))
        self.assertFalse(verify_admin_pin(""))

        source = (
            Path(__file__).resolve().parent.parent
            / "storage_manager"
            / "admin_auth.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('"6368"', source)
        self.assertNotIn("'6368'", source)


if __name__ == "__main__":
    unittest.main()
