"""상세 스캔 현황 - 한 바퀴 진행률, 완료 일정, 지난 밤들 (`smvwp.scan_overview`).

실기에서 나온 말: "날마다 스캔을 하는데 뭘 했는지 어디까지 했는지를 모르겠다." 화면에는
계정마다 진행 숫자만 있었고 전체 그림과 끝나는 날이 없었다. 여기서 보는 것은 그 숫자가
야간 스캔이 실제로 도는 방식과 맞는가다.

시각은 시간대 없는 현지 시각으로 준다 - 시험 장비의 시간대와 상관없이 같게.
"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from smvwp import config as config_module
from smvwp import nightly_scan, scan_overview, scan_store

NIGHT = SimpleNamespace(detail_scan_window_start_hour=22, detail_scan_window_end_hour=6,
                        nightly_parallel_accounts=1)


def entry(name, last=None, done=0, total=0, dirs=40, speed=60.0, unseen=None, **extra):
    """스캔 상태 한 줄 (`nightly_scan.AccountScanSnapshot` 의 쓰는 칸만)."""

    values = dict(
        account_id=name, account_name=name, last_completed_at=last,
        baseline_done=done, baseline_total=total, unavailable_reason=unseen,
        current_started_at=None, current_finished_at=None, working_started_at=None,
        current_measured_kb=None, current_dirs=dirs if last else 0, seconds_per_dir=speed if last else None,
        eta_seconds=None, previous_scan_at=None,
    )
    values.update(extra)
    return SimpleNamespace(**values)


def overview(entries, now, pass_started_at=None, runs=(), samples=None, settings=NIGHT):
    config = SimpleNamespace(
        accounts=[SimpleNamespace(account_id=e.account_id, name=e.account_name, path=f"/u/{e.account_id}")
                  for e in entries],
        settings=settings,
    )
    snapshot = SimpleNamespace(accounts=entries, is_running=False, pass_started_at=pass_started_at,
                               recent_runs=list(runs))
    return scan_overview.build(snapshot, config, latest_samples=samples, now=now)


class PassTests(unittest.TestCase):
    NOW = datetime(2026, 10, 26, 14, 0)

    def states(self, result):
        return {item.account_id: item.state for item in result.accounts}

    def test_each_account_has_one_state(self):
        result = overview([
            entry("a", last="2026-10-25T03:00:00"),
            entry("b", last="2026-10-18T03:00:00"),
            entry("c", last="2026-10-24T03:00:00", done=17, total=40),
            entry("d", last="2026-10-25T03:00:00", unseen="unreadable"),
            entry("e"),
        ], self.NOW, pass_started_at="2026-10-22T22:00:00")
        self.assertEqual(self.states(result), {
            "a": scan_overview.DONE,     # 바퀴가 시작된 뒤에 끝냈다
            "b": scan_overview.WAITING,  # 그 전에 끝냈다
            "c": scan_overview.DOING,    # 재다 말았다
            "d": scan_overview.UNSEEN,   # 경로를 못 봤다
            "e": scan_overview.WAITING,  # 한 번도 못 끝냈다
        })

    def test_without_a_recorded_start_the_oldest_finish_is_the_start(self):
        """업데이트 직후 - 가장 오래전에 끝낸 계정은 '대기', 그 뒤에 끝낸 계정은 '끝남'."""

        result = overview([
            entry("a", last="2026-10-25T03:00:00"),
            entry("b", last="2026-10-18T03:00:00"),
        ], self.NOW)
        self.assertTrue(result.pass_estimated)
        self.assertEqual(self.states(result), {"a": scan_overview.DONE, "b": scan_overview.WAITING})

    def test_progress_counts_directories_and_leaves_out_what_cannot_be_seen(self):
        result = overview([
            entry("a", last="2026-10-25T03:00:00", dirs=30),
            entry("c", last="2026-10-20T03:00:00", done=10, total=40),
            entry("w", last="2026-10-19T03:00:00", dirs=30),
            entry("u", last="2026-10-25T03:00:00", dirs=500, unseen="empty"),
        ], self.NOW, pass_started_at="2026-10-22T22:00:00")
        self.assertEqual((result.done_dirs, result.total_dirs), (30 + 10, 30 + 40 + 30))
        self.assertEqual(result.percent, 40)

    def test_remaining_accounts_go_in_the_nightly_order(self):
        """하다 만 것, 한 번도 못 끝낸 것, 오래전에 끝낸 것 (`nightly_scan._fair_order`)."""

        result = overview([
            entry("old", last="2026-10-17T03:00:00"),
            entry("older", last="2026-10-15T03:00:00"),
            entry("never"),
            entry("half", last="2026-10-24T03:00:00", done=5, total=40),
            entry("fresh", last="2026-10-25T03:00:00"),
        ], self.NOW, pass_started_at="2026-10-22T22:00:00")
        self.assertEqual([item.account_id for item in result.remaining], ["half", "never", "older", "old"])

    def test_the_stalest_is_the_one_never_finished_or_finished_longest_ago(self):
        result = overview([entry("a", last="2026-10-25T03:00:00"), entry("b", last="2026-10-17T03:00:00")],
                          self.NOW)
        self.assertEqual(result.stalest.account_id, "b")
        self.assertEqual(result.stalest.age_days(self.NOW), 9)
        result = overview([entry("a", last="2026-10-25T03:00:00"), entry("new")], self.NOW)
        self.assertEqual(result.stalest.account_id, "new")


class ScheduleTests(unittest.TestCase):
    def test_tonight_starts_at_the_window_and_spills_into_the_next_night(self):
        """밤 시간창(22~06, 8시간)에 남은 일을 순서대로 채운다 - 넘치면 다음 밤에 잇는다."""

        now = datetime(2026, 10, 26, 14, 0)
        result = overview([
            entry("a", last="2026-10-20T03:00:00", dirs=60, speed=180.0),   # 3시간
            entry("b", last="2026-10-21T03:00:00", dirs=100, speed=216.0),  # 6시간
        ], now, pass_started_at="2026-10-22T22:00:00")
        first, second = result.nights
        self.assertEqual(first.opens, datetime(2026, 10, 26, 22, 0))
        self.assertEqual([(s.account_id, s.start.hour, s.end.hour, s.carries_over) for s in first.slots],
                         [("a", 22, 1, False), ("b", 1, 6, True)])
        self.assertEqual([(s.account_id, s.resumes) for s in second.slots], [("b", True)])
        self.assertEqual(result.finish_at, datetime(2026, 10, 27, 23, 0))
        self.assertEqual(first.finished, 1)

    def test_inside_the_window_the_first_night_starts_now(self):
        now = datetime(2026, 10, 27, 1, 30)
        result = overview([entry("a", last="2026-10-20T03:00:00", dirs=10, speed=60.0)], now,
                          pass_started_at="2026-10-22T22:00:00")
        night = result.nights[0]
        self.assertEqual((night.opens, night.closes), (now, datetime(2026, 10, 27, 6, 0)))
        self.assertEqual(result.finish_at, now + timedelta(minutes=10))

    def test_a_half_done_account_uses_what_is_left(self):
        now = datetime(2026, 10, 26, 14, 0)
        result = overview([entry("a", last="2026-10-20T03:00:00", done=30, total=40, speed=60.0)], now,
                          pass_started_at="2026-10-22T22:00:00")
        self.assertEqual(result.finish_at, datetime(2026, 10, 26, 22, 10))

    def test_accounts_without_their_own_speed_borrow_the_others(self):
        now = datetime(2026, 10, 26, 14, 0)
        result = overview([
            entry("a", last="2026-10-25T03:00:00", dirs=40, speed=90.0),
            entry("new"),
        ], now, pass_started_at="2026-10-22T22:00:00")
        new = next(item for item in result.accounts if item.account_id == "new")
        self.assertEqual(new.total_dirs, 40)
        self.assertEqual(new.work_seconds, 40 * 90.0)

    def test_nothing_left_means_no_nights(self):
        result = overview([entry("a", last="2026-10-25T03:00:00")], datetime(2026, 10, 26, 14, 0),
                          pass_started_at="2026-10-22T22:00:00")
        self.assertEqual(result.nights, [])
        self.assertIsNone(result.finish_at)

    def test_too_much_work_says_so_instead_of_a_date(self):
        result = overview([entry("huge", last="2026-10-20T03:00:00", dirs=10_000, speed=600.0)],
                          datetime(2026, 10, 26, 14, 0), pass_started_at="2026-10-22T22:00:00")
        self.assertTrue(result.beyond)
        self.assertIsNone(result.finish_at)
        self.assertEqual(len(result.nights), scan_overview.MAX_NIGHTS)


class AgeOfTheMapTests(unittest.TestCase):
    """'1~50 은 어제 값' - 잰 합계를 지금 쓰는 양과 견준다. 그 계정만의 값일 때만."""

    def sample(self, used, mount, quota=None):
        return SimpleNamespace(ok=True, used_kb=used, mount_point=mount, quota_used_kb=quota)

    def changed(self, sample):
        result = overview([entry("a", last="2026-10-25T03:00:00", current_measured_kb=1000)],
                          datetime(2026, 10, 26, 14, 0), samples={"a": sample})
        return result.accounts[0].changed_kb

    def test_a_dedicated_mount_is_the_accounts_own_usage(self):
        self.assertEqual(self.changed(self.sample(1300, "/u/a")), 300)

    def test_quota_is_the_accounts_own_usage(self):
        self.assertEqual(self.changed(self.sample(9_999_999, "/u", quota=1200)), 200)

    def test_a_shared_filesystem_is_not_compared(self):
        """df 는 파일시스템 전체의 값이다 - 견주면 "그 뒤 10TB 늘었다" 같은 거짓이 나온다."""

        self.assertIsNone(self.changed(self.sample(9_999_999, "/u")))


class RunsTests(unittest.TestCase):
    def test_each_night_says_what_it_finished(self):
        runs = [{"run_id": "r1", "started_at": "2026-10-25T22:00:00", "ended_at": "2026-10-26T06:00:00",
                 "status": "paused", "triggered_by": "cron", "dirs_done": 312}]
        result = overview([
            entry("a", last="2026-10-26T01:00:00"),
            entry("b", last="2026-10-26T05:00:00"),
            entry("c", last="2026-10-20T05:00:00"),
        ], datetime(2026, 10, 26, 14, 0), runs=runs)
        run = result.runs[0]
        self.assertEqual((run.status, run.dirs_done, run.accounts_done), ("paused", 312, 2))
        self.assertEqual(run.ended_at, datetime(2026, 10, 26, 6, 0))


class AdvancePassTests(unittest.TestCase):
    """야간 스캔이 끝날 때 '한 바퀴' 의 시작을 남긴다 (`nightly_scan.advance_pass`)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        for name in ("a", "b", "c"):
            (root / name).mkdir()
            config_module.add_account(self.config, name, str(root / name), data_dir=self.data_dir)
        self.ids = [account.account_id for account in self.config.accounts]

    def finish(self, index, at, generation=1):
        with scan_store.session(self.data_dir) as conn:
            scan_store.mark_generation_completed(conn, self.ids[index], generation, completed_at=at)

    def stored(self):
        with scan_store.session(self.data_dir) as conn:
            return scan_store.get_meta(conn, nightly_scan.PASS_STARTED_KEY)

    def test_nothing_finished_leaves_nothing(self):
        self.assertIsNone(nightly_scan.advance_pass(self.data_dir, self.config))
        self.assertIsNone(self.stored())

    def test_the_first_record_is_the_oldest_finish(self):
        self.finish(0, "2026-10-20T03:00:00")
        self.finish(1, "2026-10-18T03:00:00")
        self.assertEqual(nightly_scan.advance_pass(self.data_dir, self.config), "2026-10-18T03:00:00")
        self.assertEqual(self.stored(), "2026-10-18T03:00:00")

    def test_when_every_account_has_finished_the_next_pass_starts(self):
        for index, day in enumerate((20, 18, 19)):
            self.finish(index, f"2026-10-{day}T03:00:00")
        nightly_scan.advance_pass(self.data_dir, self.config)
        self.finish(1, "2026-10-21T03:00:00", generation=2)          # 가장 오래된 것을 끝냈다
        now = datetime(2026, 10, 21, 6, 0)
        started = nightly_scan.advance_pass(self.data_dir, self.config, now=now)
        self.assertEqual(scan_overview.scan_window.local_time_of(started), now)

    def test_an_unseen_account_does_not_hold_the_pass(self):
        for index, day in enumerate((20, 18, 19)):
            self.finish(index, f"2026-10-{day}T03:00:00")
        nightly_scan.advance_pass(self.data_dir, self.config)
        with scan_store.session(self.data_dir) as conn:
            scan_store.mark_unavailable(conn, self.ids[1], "empty", "", "2026-10-21T03:00:00")
        before = self.stored()
        self.assertNotEqual(nightly_scan.advance_pass(self.data_dir, self.config,
                                                      now=datetime(2026, 10, 21, 6, 0)), before)


class SnapshotFactsTests(unittest.TestCase):
    """스캔 상태가 현황에 드는 사실을 싣는다 - 측정 기간, 디렉터리 수, 지난 밤들."""

    def test_a_finished_generation_carries_its_period_and_size(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        data_dir = root / "data"
        (root / "a").mkdir()
        config = config_module.load_config(data_dir)
        account = config_module.add_account(config, "a", str(root / "a"), data_dir=data_dir)
        with scan_store.session(data_dir) as conn:
            scan_store.start_run(conn, "r1", "cron")
            scan_store.seed_checkpoints(conn, account.account_id, scan_store.BASELINE, 1, ["/a/x", "/a/y"])
            for path, size in (("/a/x", 100), ("/a/y", 50)):
                checkpoint = scan_store.next_pending(conn, account.account_id, scan_store.BASELINE, 1)
                scan_store.save_tree_entries(conn, account.account_id, 1, [(checkpoint["path"], size)],
                                             checkpoint["path"])
                scan_store.mark_done(conn, checkpoint["id"], size_kb=size)
            scan_store.mark_generation_completed(conn, account.account_id, 1)
            scan_store.finish_run(conn, "r1", "completed")
        snapshot = nightly_scan.get_status_snapshot(data_dir, config)
        item = snapshot.accounts[0]
        self.assertEqual((item.current_dirs, item.current_measured_kb), (2, 150))
        self.assertIsNotNone(item.current_started_at)
        self.assertIsNotNone(item.last_completed_at)
        self.assertEqual([run["run_id"] for run in snapshot.recent_runs], ["r1"])
        self.assertEqual(snapshot.recent_runs[0]["dirs_done"], 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
