"""`smvwp_cli.py prepare` - 업데이트 뒤 한 번, 창을 열기 전에.

실기에서 업데이트 뒤 창을 처음 열자 홈이 115초 걸렸고, 그사이 수집은 "스캔 DB 가
잠겨" 를 만났다. 새 인덱스를 DB 를 처음 여는 프로세스가 만드는데 그것이 창이었고,
헬스체크 대조와 스캔 탭 요약은 다음 야간 스캔 전까지 남아 있지 않아 창이 스스로
읽었다. cron 을 내린 채 이것을 한 번 돌리면 둘 다 미리 끝난다.
"""

import io
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import smvwp_cli
from smvwp import config as config_module
from smvwp import health, nightly_scan, scan_lock, scan_store


class PrepareTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        accounts = {}
        for name, kind in (("proj", config_module.ACCOUNT_KIND_PROJECT),
                           ("bak", config_module.ACCOUNT_KIND_BACKUP)):
            (root / name).mkdir()
            accounts[name] = config_module.add_account(
                self.config, name, str(root / name), data_dir=self.data_dir, kind=kind
            )
        config_module.set_backup_link(self.config, accounts["proj"].account_id, accounts["bak"].account_id)
        config_module.save_config(self.data_dir, self.config)
        with scan_store.session(self.data_dir) as conn:
            for account in accounts.values():
                for generation in (1, 2):
                    scan_store.save_tree_entries(
                        conn, account.account_id, generation,
                        [("/x/t", 100 * generation), ("/x/t/01_run_0908", 90)], "/x/t",
                    )
                    scan_store.mark_generation_completed(conn, account.account_id, generation)

    def prepare(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = smvwp_cli.command_prepare(types.SimpleNamespace(data_dir=str(self.data_dir)))
        return code, out.getvalue(), err.getvalue()

    def test_afterwards_the_window_reads_neither_trees_nor_results(self):
        code, out, _ = self.prepare()
        self.assertEqual(code, 0)
        self.assertIn("헬스체크", out)
        self.assertIn("스캔 탭 요약", out)
        with patch("smvwp.scan_store.tree_entries", side_effect=AssertionError("대조를 다시 했다")), \
                patch("smvwp.nightly_scan._settled_parts", side_effect=AssertionError("결과 표를 읽었다")):
            health.check_all(self.data_dir, self.config)
            snapshot = nightly_scan.get_status_snapshot(self.data_dir, self.config)
        self.assertTrue(all(entry.top_paths for entry in snapshot.accounts))

    def test_running_it_again_has_nothing_new_to_leave(self):
        self.prepare()
        with patch("smvwp.nightly_scan._settled_parts", wraps=nightly_scan._settled_parts) as parts:
            code, out, _ = self.prepare()
        self.assertEqual(code, 0)
        self.assertEqual(parts.call_count, 0)
        self.assertIn("0개 계정 새로", out)

    @patch("smvwp.scan_lock._pid_alive", return_value=True)
    def test_it_waits_for_a_running_scan(self, _alive):
        scan_lock.acquire_lock(self.data_dir, "cron")
        code, _, err = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("스캔이 돌고", err)
        with scan_store.session(self.data_dir) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scan_summaries").fetchone()[0], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
