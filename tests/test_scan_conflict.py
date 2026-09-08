"""GUI 낮 실행과 cron 야간 실행이 부딪히는 자리.

## 무엇이 문제였나

사람이 낮에 GUI 에서 `지금 스캔` 을 누르면 그 실행은 시간창을 무시하고 돈다
(`bypass_window`). 큰 계정이면 몇 시간씩 간다. 그러다 22:00 이 되면 cron 이
뜨는데, 잠금은 낮 실행이 쥐고 있다. 예전에는 cron 이 조용히 물러났고 **그날
밤이 통째로 날아갔다** - 로그에 한 줄 남을 뿐이라 아무도 몰랐다.

## 어떻게 메웠나

두 쪽에서 만난다.

- 낮에 시작한 실행은 **야간 창이 열리면 스스로 물러난다.** 진행한 것은 이미
  저장돼 있으므로 잃는 것이 없고, 곧 뜨는 야간 실행이 이어받는다.
- cron 은 잠금이 안 잡히면 **잠깐 기다린다.** 낮 실행이 물러나는 데 시간이
  걸리기 때문이다(재던 디렉터리를 마저 끝낸다).

기다리는 것은 cron 뿐이다. GUI 에서 버튼을 누른 사람은 20분을 기다리는 것이
아니라 "이미 실행 중"이라는 답을 즉시 들어야 한다.
"""

import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import nightly_scan, scan_lock


class _Base(unittest.TestCase):
    # 2026-08-24는 월요일. 창은 기본 22:00~06:00.
    DAYTIME = datetime(2026, 8, 24, 14, 0)
    EVENING = datetime(2026, 8, 24, 21, 30)
    NIGHT = datetime(2026, 8, 24, 23, 0)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.config = config_module.load_config(self.data_dir)
        path = self.root / "acct"
        path.mkdir()
        config_module.add_account(
            self.config, "a", str(path), data_dir=self.data_dir
        )
        config_module.save_config(self.data_dir, self.config)

    def tearDown(self):
        self.tmp.cleanup()


class DaytimeRunYieldsToTheNightTests(_Base):
    """낮에 눌러 둔 스캔이 밤 차례를 막지 않는가."""

    def _run(self, start, clock_values):
        """`clock_values` 를 차례로 돌려주는 시계로 한 번 돌린다."""

        ticks = iter(clock_values)
        last = [start]

        def clock():
            try:
                last[0] = next(ticks)
            except StopIteration:
                pass
            return last[0]

        return nightly_scan.run_nightly_scan(
            self.data_dir,
            self.config,
            triggered_by="gui",
            bypass_window=True,
            clock=clock,
            top_level_lister=lambda path: [],
            baseline_warmup_seconds=0.0,
        )

    def test_a_daytime_run_stops_once_the_night_window_opens(self):
        """22:00 이 되면 물러난다 - 안 그러면 cron 이 잠금을 못 잡는다."""

        summary = self._run(self.DAYTIME, [self.DAYTIME, self.NIGHT, self.NIGHT])
        self.assertTrue(summary.started)
        self.assertEqual(summary.status, nightly_scan.STATUS_PAUSED)

    def test_a_daytime_run_keeps_going_while_it_is_still_daytime(self):
        summary = self._run(self.DAYTIME, [self.DAYTIME] * 6)
        self.assertEqual(summary.status, nightly_scan.STATUS_COMPLETED)

    def test_a_run_started_inside_the_window_does_not_yield(self):
        """야간 안에서 누른 것은 양보할 상대가 없다 - 그것이 그날 밤의 실행이다.

        여기서 물러나면 눌러도 아무 일이 안 일어나는 것처럼 보인다."""

        summary = self._run(self.NIGHT, [self.NIGHT] * 6)
        self.assertEqual(summary.status, nightly_scan.STATUS_COMPLETED)

    def test_the_boundary_hour_counts_as_night(self):
        summary = self._run(self.EVENING, [self.EVENING, datetime(2026, 8, 24, 22, 0)])
        self.assertEqual(summary.status, nightly_scan.STATUS_PAUSED)


class LockWaitTests(_Base):
    """잠금이 잡혀 있을 때 누가 기다리고 누가 바로 물러나는가."""

    def _busy(self):
        """다른 살아 있는 프로세스가 잠금을 쥔 상태로 만든다."""

        scan_lock.acquire_lock(self.data_dir, "gui")

    def test_without_waiting_it_gives_up_at_once(self):
        """GUI 에서 누른 사람은 즉시 답을 들어야 한다."""

        self._busy()
        summary = nightly_scan.run_nightly_scan(
            self.data_dir, self.config,
            clock=lambda: self.NIGHT, top_level_lister=lambda p: [],
        )
        self.assertFalse(summary.started)
        self.assertIn("이미 실행 중", summary.reason)

    def test_the_reason_says_who_is_holding_it(self):
        """'왜 안 도는가'에 답이 되어야 한다 - 누가 쥐고 있는지까지."""

        self._busy()
        summary = nightly_scan.run_nightly_scan(
            self.data_dir, self.config,
            clock=lambda: self.NIGHT, top_level_lister=lambda p: [],
        )
        self.assertIn("gui", summary.reason)

    def test_cron_waits_and_then_starts_when_the_lock_frees_up(self):
        """낮 실행이 물러나는 데 시간이 걸린다 - 그동안 기다려야 밤을 안 잃는다."""

        self._busy()
        holder = scan_lock.read_lock(self.data_dir)

        def free_it_soon():
            scan_lock.release_lock(self.data_dir, holder.run_id)

        timer = threading.Timer(0.3, free_it_soon)
        timer.start()
        self.addCleanup(timer.cancel)

        with patch.object(nightly_scan, "LOCK_POLL_SECONDS", 0.05):
            summary = nightly_scan.run_nightly_scan(
                self.data_dir, self.config,
                clock=lambda: self.NIGHT, top_level_lister=lambda p: [],
                lock_wait_seconds=10.0,
            )
        self.assertTrue(summary.started, summary.reason)

    def test_waiting_gives_up_eventually(self):
        """영원히 기다리면 cron 프로세스가 쌓인다."""

        self._busy()
        with patch.object(nightly_scan, "LOCK_POLL_SECONDS", 0.02):
            summary = nightly_scan.run_nightly_scan(
                self.data_dir, self.config,
                clock=lambda: self.NIGHT, top_level_lister=lambda p: [],
                lock_wait_seconds=0.1,
            )
        self.assertFalse(summary.started)


class DefaultsTests(unittest.TestCase):
    def test_only_cron_waits_by_default(self):
        """라이브러리 기본값은 기다리지 않는다 - 기다림은 cron 만의 사정이다."""

        import inspect

        signature = inspect.signature(nightly_scan.run_nightly_scan)
        self.assertEqual(signature.parameters["lock_wait_seconds"].default, 0.0)

    def test_the_wait_covers_a_checkpoint_timeout(self):
        """낮 실행이 물러나는 데 최대 한 체크포인트가 걸린다 - 그보다 길어야 한다."""

        settings = config_module.Settings()
        self.assertGreater(
            nightly_scan.DEFAULT_LOCK_WAIT_SECONDS,
            settings.detail_task_timeout_seconds,
        )


class ReportPicksTheRightRunTests(unittest.TestCase):
    """아침 보고서가 **어젯밤** 부하를 보여 주는가.

    예전에는 그냥 "가장 최근 실행"을 실었다. 그러면 낮에 사람이 한 번 눌러 본
    5분짜리가 밤 부하를 덮어 버리고, 읽는 사람은 그것이 밤 것인 줄 안다.
    """

    def setUp(self):
        from smvwp import scan_store

        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.conn = scan_store.connect(self.data_dir)
        # `addCleanup` 은 **역순**으로 돈다. 디렉터리 삭제를 먼저 걸어야
        # 연결이 그보다 나중에 닫히는 일이 없다 - 윈도우는 열린 파일을 못 지운다.
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def _add_run(self, run_id, trigger, started, ended):
        self.conn.execute(
            "INSERT INTO scan_runs (run_id, triggered_by, started_at, ended_at, status)"
            " VALUES (?, ?, ?, ?, ?)",
            (run_id, trigger, started, ended, "completed"),
        )
        self.conn.commit()
        return self.conn.execute(
            "SELECT * FROM scan_runs WHERE run_id = ?", (run_id,)
        ).fetchone()

    def test_the_long_night_run_wins_over_a_short_daytime_one(self):
        from smvwp import reports

        night = self._add_run(
            "night", "cron", "2026-08-24T22:00:00+00:00", "2026-08-25T05:10:00+00:00"
        )
        daytime = self._add_run(
            "day", "gui", "2026-08-25T14:00:00+00:00", "2026-08-25T14:05:00+00:00"
        )
        picked = reports._pick_scan_run([daytime, night])   # 최신순으로 들어온다
        self.assertEqual(picked["run_id"], "night")

    def test_a_run_without_an_end_time_does_not_win(self):
        """아직 안 끝난 실행은 길이를 모른다 - 그것으로 밤을 덮으면 안 된다."""

        from smvwp import reports

        night = self._add_run(
            "night", "cron", "2026-08-24T22:00:00+00:00", "2026-08-25T05:10:00+00:00"
        )
        running = self._add_run("now", "gui", "2026-08-25T14:00:00+00:00", None)
        self.assertEqual(reports._pick_scan_run([running, night])["run_id"], "night")

    def test_no_runs_at_all(self):
        from smvwp import reports

        self.assertIsNone(reports._pick_scan_run([]))

    def test_the_headline_says_which_run_it_was(self):
        """'이 숫자가 무엇의 것인가'에 답이 되어야 한다."""

        from smvwp import i18n, reports

        i18n.set_language("ko")
        run = self._add_run(
            "night", "cron", "2026-08-24T22:00:00+00:00", "2026-08-25T05:10:00+00:00"
        )
        line = reports._run_headline(run)
        self.assertIn("2026-08-24 22:00", line)
        self.assertIn("cron", line)
        self.assertIn("7시간", line)      # 22:00 -> 05:10
        self.assertIn("completed", line)

    def test_a_hand_started_run_is_labelled_as_such(self):
        from smvwp import i18n, reports

        i18n.set_language("ko")
        run = self._add_run(
            "day", "gui", "2026-08-25T14:00:00+00:00", "2026-08-25T14:05:00+00:00"
        )
        line = reports._run_headline(run)
        self.assertIn("수동", line)
        self.assertIn("5분", line)

    def test_an_unknown_trigger_is_shown_as_is(self):
        """모르는 값에 `reports.trigger.xyz` 가 뜨면 읽는 사람이 당황한다."""

        from smvwp import reports

        self.assertEqual(reports._trigger_text("something_new"), "something_new")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
