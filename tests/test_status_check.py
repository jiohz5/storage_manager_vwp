"""`smvwp_cli.py status` - 지금 이 도구가 제대로 돌고 있나를 한 화면에.

## 왜 생겼나

실기에서 "cron 이 도나", "홈이 결과 파일에서 읽히나", "업데이트 뒤 prepare 를 했나" 를
확인하려면 로그 몇 개와 DB 를 따로 뒤져야 했다. 판·마지막 수집·홈 결과 파일·마지막 야간
스캔·스캔 탭 요약·DB 크기를 한 번에 보이고, 손볼 것이 있으면 무엇을 하면 되는지 말한다.
문제가 하나라도 있으면 종료 코드 1 (cron·감시 스크립트가 쓸 수 있게).
"""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import home_view, scan_store, status_check, store, tiers


def _sample(account_id, at):
    total = 10 ** 9
    return store.SampleRecord(
        account_id=account_id, collected_at=at.isoformat(), ok=True, filesystem="nfs4",
        mount_point="/ifs", total_kb=total, used_kb=total // 2, avail_kb=total // 2,
        byte_pct=50.0, byte_tier=tiers.classify(50.0), overall_tier=tiers.classify(50.0),
    )


class StatusCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        (root / "proj").mkdir()
        config_module.add_account(self.config, "proj", str(root / "proj"), data_dir=self.data_dir)
        config_module.save_config(self.data_dir, self.config)
        self.now = datetime.now(timezone.utc)

    def collected(self, minutes_ago):
        with store.session(self.data_dir) as conn:
            store.insert_samples(conn, [_sample(self.config.accounts[0].account_id,
                                                self.now - timedelta(minutes=minutes_ago))])

    def items(self):
        return {item.key: item for item in status_check.gather(self.data_dir, self.config,
                                                               now=self.now)}


class CollectionTests(StatusCase):
    def test_never_collected_is_a_problem(self):
        self.assertEqual(self.items()["collect"].level, status_check.BAD)

    def test_a_recent_collection_is_fine(self):
        self.collected(5)
        self.assertEqual(self.items()["collect"].level, status_check.OK)

    def test_a_stopped_cron_shows(self):
        self.collected(3 * 60)
        item = self.items()["collect"]
        self.assertEqual(item.level, status_check.WARN)
        self.assertIn("cron", item.hint)


class CycleTimeTests(StatusCase):
    def timed(self, total, **seconds):
        import json

        from smvwp import cycle

        path = cycle.timing_path(self.data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"finished_at": self.now.isoformat(), "host": "h",
                                    "total": total, "seconds": seconds}), encoding="utf-8")

    def test_no_record_yet_is_not_a_warning(self):
        self.assertEqual(self.items()["cycle_time"].level, status_check.INFO)

    def test_shows_the_biggest_stage_first(self):
        self.timed(12.5, df=1.0, home=11.0, save=0.01)
        item = self.items()["cycle_time"]
        self.assertEqual(item.level, status_check.OK)
        self.assertIn("12.5초", item.value)
        self.assertLess(item.value.index("홈 11.0"), item.value.index("df 1.0"))
        self.assertNotIn("저장", item.value)

    def test_a_cycle_longer_than_half_the_interval_warns(self):
        interval = self.config.settings.collector_interval_seconds
        self.timed(interval / 2 + 1, df=interval / 2)
        item = self.items()["cycle_time"]
        self.assertEqual(item.level, status_check.WARN)
        self.assertIn("절반", item.hint)


class HomeFileTests(StatusCase):
    def test_no_file_yet_says_when_it_comes(self):
        self.collected(5)
        item = self.items()["home"]
        self.assertEqual(item.level, status_check.WARN)
        self.assertIn("collect", item.hint)

    def test_a_good_file(self):
        self.collected(5)
        home_view.write(self.data_dir, self.config)
        self.assertEqual(self.items()["home"].level, status_check.OK)

    def test_says_why_a_file_is_not_used(self):
        self.collected(5)
        home_view.write(self.data_dir, self.config)
        later = datetime.now(timezone.utc) + timedelta(hours=2)
        with patch("smvwp.home_view._now", return_value=later):
            item = self.items()["home"]
        self.assertEqual(item.level, status_check.WARN)
        self.assertIn(home_view.PROBLEM_STALE, item.value)


class SummaryTests(StatusCase):
    def test_outdated_summaries_ask_for_prepare(self):
        with patch("smvwp.nightly_scan.outdated_summaries", return_value=2):
            item = self.items()["summaries"]
        self.assertEqual(item.level, status_check.WARN)
        self.assertIn("prepare", item.hint)


class OutdatedSummaryCountTests(StatusCase):
    """`nightly_scan.outdated_summaries` 를 진짜로 - 완료 세대가 있는데 요약이 없거나 옛것."""

    def complete(self, generation):
        account_id = self.config.accounts[0].account_id
        root = self.config.accounts[0].path
        with scan_store.session(self.data_dir) as conn:
            scan_store.save_tree_entries(conn, account_id, generation, [(root, 1024)], root)
            scan_store.mark_generation_completed(conn, account_id, generation)

    def test_counts(self):
        from smvwp import nightly_scan

        self.assertEqual(nightly_scan.outdated_summaries(self.data_dir, self.config), 0)
        self.complete(1)
        self.assertEqual(nightly_scan.outdated_summaries(self.data_dir, self.config), 1)
        nightly_scan.save_summaries(self.data_dir, self.config)
        self.assertEqual(nightly_scan.outdated_summaries(self.data_dir, self.config), 0)
        self.complete(2)   # 새 세대가 끝났는데 요약을 아직 안 남겼다
        self.assertEqual(nightly_scan.outdated_summaries(self.data_dir, self.config), 1)
        with patch("smvwp.nightly_scan.SUMMARY_FORMAT", 999):   # 판이 바뀌었다
            nightly_scan.save_summaries(self.data_dir, self.config)
        self.assertEqual(nightly_scan.outdated_summaries(self.data_dir, self.config), 1)


class CommandTests(StatusCase):
    def run_cli(self):
        import smvwp_cli

        out = io.StringIO()
        with redirect_stdout(out):
            code = smvwp_cli.main(["status", "--data-dir", str(self.data_dir)])
        return code, out.getvalue()

    def test_exit_code_tells_a_problem(self):
        code, text = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("문제", text)

    def test_everything_listed_once(self):
        self.collected(5)
        home_view.write(self.data_dir, self.config)
        code, text = self.run_cli()
        for label in ("판", "마지막 수집", "수집 걸린 시간", "홈 결과 파일", "야간 스캔", "DB"):
            self.assertIn(label, text)
        self.assertNotIn("문제", text.split("\n", 1)[1] if code == 0 else "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
