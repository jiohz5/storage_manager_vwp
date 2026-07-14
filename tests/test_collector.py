import subprocess
import unittest
from unittest.mock import patch

from storage_manager.collector import (
    delta_map,
    parse_df_output,
    parse_du_output,
    ranked_items,
    run_df,
    usage_color,
    usage_level,
)


class CollectorTests(unittest.TestCase):
    def test_parse_df_output(self):
        snapshot = parse_df_output(
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/mapper/data 100000 95000 5000 95% /user\n"
        )
        self.assertEqual(snapshot.use_pct, 95)
        self.assertEqual(snapshot.used_kb, 95000)

    @patch("storage_manager.collector.subprocess.run")
    def test_run_df_collects_byte_and_inode_usage(self, run_mock):
        run_mock.side_effect = [
            subprocess.CompletedProcess(
                ["df"],
                0,
                "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/a 1000 800 200 80% /data\n",
                "",
            ),
            subprocess.CompletedProcess(
                ["df"],
                0,
                "Filesystem Inodes IUsed IFree IUse% Mounted on\n/dev/a 10000 9100 900 91% /data\n",
                "",
            ),
        ]
        snapshot = run_df("/data")
        self.assertEqual(snapshot.use_pct, 80)
        self.assertEqual(snapshot.total_inodes, 10000)
        self.assertEqual(snapshot.inode_use_pct, 91)
        self.assertEqual(run_mock.call_args_list[1].args[0][1], "-Pi")

    def test_parse_du_output_handles_spaces_and_excludes_total(self):
        rows = parse_du_output(
            "20\t/user/a/small dir\n100\t/user/a/large\n120\t/user/a\n",
            "/user/a",
        )
        self.assertEqual(rows, [("/user/a/large", 100), ("/user/a/small dir", 20)])

    def test_delta_uses_full_baseline_and_tracks_deletion(self):
        rows = delta_map(
            [("/user/a/old", 100), ("/user/a/keep", 50)],
            [("/user/a/new", 30), ("/user/a/keep", 80)],
        )
        self.assertIn(("/user/a/keep", 30), rows)
        self.assertIn(("/user/a/new", 30), rows)
        self.assertIn(("/user/a/old", -100), rows)
        self.assertEqual(delta_map([], [("/user/a/new", 30)], baseline_exists=False), [])

    def test_rank_and_colors(self):
        self.assertEqual(ranked_items([("b", 2), ("a", 5)], 1), [("a", 5, 1)])
        self.assertEqual(usage_color(95), "#d9534f")
        self.assertEqual(usage_color(0, failed=True), "#b6bcc5")
        self.assertEqual(usage_level(89), "ok")
        self.assertEqual(usage_level(90), "warning")
        self.assertEqual(usage_level(95), "alert")


if __name__ == "__main__":
    unittest.main()
