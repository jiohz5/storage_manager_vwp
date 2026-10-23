import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import detail_scan
from smvwp import nightly_scan, scan_lock, scan_store
from tests import support


class FakeCommandRunner:
    """명령 내용으로 구분해 가짜 결과를 돌려주는 단일 러너.

    `find` 갈래는 **없앤 기능이 되살아나지 않는지 보려고 남겨 둔다.** 활동
    스캔을 걷어낸 뒤로 `find` 는 한 번도 돌면 안 되고, 여기에 걸리면 시험이
    그것을 알려 준다.

    주의(이 파일에서 한 번 실수했던 부분): 여러 모듈의 `subprocess` 는 서로
    다른 객체가 아니라 **같은 표준 subprocess 모듈 객체**다. 따라서 여러
    경로를 각각 patch 하면 나중에 적용된 patch 가 앞의 것을 그대로 덮어써서
    엉뚱한 가짜 결과를 받는다. 그래서 patch 는 한 번만 걸고, 그 하나가 명령을
    보고 분기하도록 했다.

    stdout은 bytes로 돌려준다 - 앱이 `procio.run_utf8`로 바이트 모드 실행 후
    직접 UTF-8 디코딩하기 때문 (tests/support.py 참고).

    `on_du`는 "N번째 du 호출 직후"에 끼어들 훅이다 - 스캔 도중 안전 중지를
    요청하는 상황을 만들 때 쓴다.
    """

    def __init__(self, on_du=None):
        self.du_calls = []
        self.find_calls = []
        self.on_du = on_du

    def __call__(self, command, **kwargs):
        if command and command[-1] == "true":
            # `nice`/`ionice` 접두사가 실제로 되는지 보는 탐색 호출
            # (`detail_scan._prefix_works`). 이 파일만 따로 돌리면 캐시가
            # 비어 있어 여기부터 들어온다 - 없으면 시험이 **실행 순서에**
            # 의존하게 된다.
            return support.completed(command)
        if "du" in command:
            self.du_calls.append(command)
            if self.on_du is not None:
                self.on_du(len(self.du_calls))
            return support.completed(command, stdout=f"100\t{command[-1]}\n")
        if "find" in command:
            self.find_calls.append(command)
            return support.completed(command)
        raise AssertionError(f"예상치 못한 명령: {command}")

    @property
    def du_count(self) -> int:
        return len(self.du_calls)


class SteppingClock:
    """호출될 때마다 미리 정해 둔 시각을 순서대로 돌려주는 가짜 시계.
    목록이 소진되면 마지막 값을 계속 반환한다."""

    def __init__(self, times):
        self._times = list(times)
        self._idx = 0

    def __call__(self):
        value = self._times[min(self._idx, len(self._times) - 1)]
        self._idx += 1
        return value


class NightlyScanOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.data_dir = self.tmp_root / "data"
        self.account_path = self.tmp_root / "acct"
        self.account_path.mkdir(parents=True)

        # 접두사 탐색 결과는 모듈 전역에 캐시된다. 지우고 시작해야 이 파일을
        # 혼자 돌리든 전체와 함께 돌리든 같은 경로를 지난다.
        detail_scan.reset_priority_prefix()
        self.config = config_module.load_config(self.data_dir)
        # 이 시험은 `du` 출력 흉내로 오케스트레이션을 본다 - 엔진을 못박지
        # 않으면 기본값(파이썬 순회)이 실제 디렉터리를 걸어 흉내가 무의미해진다.
        self.config.settings.scan_engine = config_module.SCAN_ENGINE_DU
        self.account = config_module.add_account(
            self.config, "project_a", str(self.account_path), data_dir=self.data_dir
        )
        config_module.save_config(self.data_dir, self.config)

        # 계정 루트 바로 아래 최상위 디렉터리 2개를 돌려주는 가짜 lister.
        self.top_dirs = [f"{self.account_path}/dir1", f"{self.account_path}/dir2"]
        self.lister = lambda path: list(self.top_dirs)

    def tearDown(self):
        self._tmp.cleanup()

    def _patch_commands(self, runner: FakeCommandRunner):
        # patch 대상은 하나뿐이다 (위 FakeCommandRunner 주석 참고).
        return patch("smvwp.detail_scan.subprocess.run", side_effect=runner)

    def test_not_started_when_outside_window_and_not_bypassed(self):
        daytime = datetime(2026, 7, 31, 14, 0)
        summary = nightly_scan.run_nightly_scan(
            self.data_dir, self.config, clock=lambda: daytime, top_level_lister=lambda p: []
        )
        self.assertFalse(summary.started)
        self.assertEqual(summary.status, nightly_scan.STATUS_NOT_STARTED)

    @patch("smvwp.scan_lock._pid_alive", return_value=True)
    def test_not_started_when_lock_busy(self, _mock_alive):
        scan_lock.acquire_lock(self.data_dir, "terminal")
        night = datetime(2026, 7, 31, 23, 0)
        summary = nightly_scan.run_nightly_scan(
            self.data_dir, self.config, clock=lambda: night, top_level_lister=lambda p: []
        )
        self.assertFalse(summary.started)
        self.assertIn("이미 실행 중", summary.reason)

    def test_bypass_window_completes_full_cycle(self):
        runner = FakeCommandRunner()
        with self._patch_commands(runner):
            summary = nightly_scan.run_nightly_scan(
                self.data_dir,
                self.config,
                bypass_window=True,
                clock=lambda: datetime(2026, 7, 31, 14, 0),  # 주간이어도 bypass면 무관
                top_level_lister=self.lister,
            )

        self.assertTrue(summary.started)
        self.assertEqual(summary.status, nightly_scan.STATUS_COMPLETED)
        self.assertEqual(len(summary.accounts), 1)
        outcome = summary.accounts[0]
        self.assertEqual(outcome.baseline_status, "done")

        # 최상위 디렉터리 2개에 대해 du가 한 번씩 실행되어야 한다.
        self.assertEqual(runner.du_count, 2)
        # find 는 이제 아예 돌지 않는다. 변경 파일 수를 세려고 트리를 한 번 더
        # 완주했는데, 그 숫자로 할 수 있는 일이 없었다.
        self.assertEqual(runner.find_calls, [])

        conn = scan_store.connect(self.data_dir)
        try:
            top = scan_store.top_paths(conn, self.account.account_id, 1)
            self.assertEqual(len(top), 2)
        finally:
            conn.close()

    def test_deadline_interrupts_and_next_call_resumes_without_redoing_work(self):
        t0 = datetime(2026, 7, 31, 23, 0)
        t_after_window = datetime(2026, 8, 1, 7, 0)  # 06:00 시간창을 이미 지난 시각
        # 시간창 판정/첫 체크포인트까지는 t0, 그 다음 판정부터는 시간창을 넘긴
        # 시각을 돌려줘서 "dir1까지 하고 06:00에 걸려 멈춤"을 재현한다.
        stepping_clock = SteppingClock([t0, t0, t0] + [t_after_window] * 50)

        runner = FakeCommandRunner()
        with self._patch_commands(runner):
            first_summary = nightly_scan.run_nightly_scan(
                self.data_dir,
                self.config,
                triggered_by="cron",
                clock=stepping_clock,
                top_level_lister=self.lister,
            )

        self.assertTrue(first_summary.started)
        self.assertEqual(first_summary.status, nightly_scan.STATUS_PAUSED)
        self.assertEqual(first_summary.accounts[0].baseline_status, "interrupted")
        # dir1까지만 처리되고 dir2는 아직 pending으로 남아 있어야 한다.
        self.assertEqual(runner.du_count, 1)

        conn = scan_store.connect(self.data_dir)
        try:
            state = scan_store.get_account_state(conn, self.account.account_id)
            self.assertIsNone(state.last_completed_generation)  # 아직 세대 완료 안 됨
            pending = scan_store.next_pending(conn, self.account.account_id, scan_store.BASELINE, 1)
            self.assertIsNotNone(pending)
            self.assertEqual(pending["path"], self.top_dirs[1])  # 남은 것은 dir2
        finally:
            conn.close()

        # 다음 밤(또는 재개 실행) - bypass_window로 끝까지 이어서 처리.
        resume_runner = FakeCommandRunner()
        with self._patch_commands(resume_runner):
            second_summary = nightly_scan.run_nightly_scan(
                self.data_dir,
                self.config,
                triggered_by="cron",
                bypass_window=True,
                clock=lambda: datetime(2026, 8, 1, 22, 0),
                top_level_lister=self.lister,
            )

        self.assertEqual(second_summary.status, nightly_scan.STATUS_COMPLETED)
        self.assertEqual(second_summary.accounts[0].baseline_status, "done")
        # 재개 실행에서는 이미 끝난 dir1을 다시 하지 않고 dir2 하나만 처리해야
        # 한다 - 재개가 완료된 작업을 되풀이하지 않는다는 것이 이 테스트의 핵심.
        self.assertEqual(resume_runner.du_count, 1)
        self.assertEqual(resume_runner.du_calls[0][-1], self.top_dirs[1])

        conn = scan_store.connect(self.data_dir)
        try:
            state = scan_store.get_account_state(conn, self.account.account_id)
            self.assertEqual(state.last_completed_generation, 1)
            top = scan_store.top_paths(conn, self.account.account_id, 1)
            self.assertEqual(len(top), 2)  # dir1(1차) + dir2(재개분)
        finally:
            conn.close()

    def test_stop_request_interrupts_and_lock_is_released(self):
        def request_stop_after_first_du(du_call_no):
            # 첫 du가 끝난 직후 "지금 실행 중인 run_id를 멈춰라"고 요청한다.
            if du_call_no != 1:
                return
            info = scan_lock.read_lock(self.data_dir)
            if info is not None:
                scan_lock.request_stop(self.data_dir, info.run_id)

        # 중지 경로를 결정적으로 보려고 작업자를 1개로 고정한다. 여러 개면
        # 중지 요청이 도착하기 전에 이미 시작된 것들이 있어 개수가 흔들린다
        # (그 상황은 아래 test_stop_works_with_multiple_workers 가 본다).
        self.config.settings.checkpoint_workers = 1

        runner = FakeCommandRunner(on_du=request_stop_after_first_du)
        with self._patch_commands(runner):
            summary = nightly_scan.run_nightly_scan(
                self.data_dir,
                self.config,
                bypass_window=True,
                clock=lambda: datetime(2026, 7, 31, 23, 0),
                top_level_lister=self.lister,
            )

        self.assertEqual(summary.status, nightly_scan.STATUS_STOPPED)
        # 중지 요청 이후로는 남은 디렉터리를 계속 처리하지 않아야 한다.
        self.assertEqual(runner.du_count, 1)
        # 잠금 파일은 어떤 종료 경로에서도 반드시 회수되어야 한다.
        self.assertIsNone(scan_lock.read_lock(self.data_dir))
        self.assertFalse(scan_lock.is_locked(self.data_dir))

    def test_stop_works_with_multiple_workers(self):
        """작업자가 여럿이어도 중지는 듣고 잠금은 회수된다.

        이미 시작된 `du`는 끝까지 간다 (강제 kill 없음 - DESIGN 1부 2절 4번).
        그래서 처리 개수는 작업자 수만큼 흔들릴 수 있고, 여기서 고정할 것은
        **개수가 아니라 상태와 잠금**이다."""

        self.config.settings.checkpoint_workers = 4
        self.top_dirs = [f"{self.account_path}/dir{n}" for n in range(1, 13)]

        def request_stop_after_first_du(du_call_no):
            if du_call_no != 1:
                return
            info = scan_lock.read_lock(self.data_dir)
            if info is not None:
                scan_lock.request_stop(self.data_dir, info.run_id)

        runner = FakeCommandRunner(on_du=request_stop_after_first_du)
        with self._patch_commands(runner):
            summary = nightly_scan.run_nightly_scan(
                self.data_dir,
                self.config,
                bypass_window=True,
                clock=lambda: datetime(2026, 7, 31, 23, 0),
                top_level_lister=self.lister,
            )

        self.assertEqual(summary.status, nightly_scan.STATUS_STOPPED)
        # 12개를 다 돌지는 않았어야 한다 (중지가 실제로 들었다는 뜻).
        self.assertLess(runner.du_count, len(self.top_dirs))
        self.assertFalse(scan_lock.is_locked(self.data_dir))

    def run_at(self, when, bypass=False):
        runner = FakeCommandRunner()
        with self._patch_commands(runner):
            summary = nightly_scan.run_nightly_scan(
                self.data_dir,
                self.config,
                bypass_window=bypass,
                clock=lambda: when,
                top_level_lister=self.lister,
            )
        return summary, runner

    def completed_generation(self):
        with scan_store.session(self.data_dir) as conn:
            return scan_store.get_account_state(conn, self.account.account_id).last_completed_generation

    def test_a_second_nightly_run_the_same_night_does_not_rescan(self):
        """같은 밤에 두 번 재면 아침 보고서의 비교 기준이 '어제' 가 아니라 '몇 시간 전' 이 된다.

        세대는 둘만 남기므로(기본) 어제 세대가 밀려나, 증가 경로·새로 생긴 과제·급증
        알림이 통째로 비어 보인다. 밤중에 창을 새로 열거나(창의 자동 스캔), cron 과
        창의 자동 스캔이 둘 다 켜져 있거나, 다른 장비에도 cron 이 있으면 그렇게 된다."""

        self.run_at(datetime(2026, 7, 31, 23, 0))
        summary, runner = self.run_at(datetime(2026, 8, 1, 2, 0))
        self.assertEqual(runner.du_count, 0)
        self.assertEqual(summary.accounts[0].baseline_status, "skipped")
        self.assertEqual(self.completed_generation(), 1)

    def test_a_manual_run_may_rescan_the_same_night(self):
        """사람이 손으로 돌린 것은 막지 않는다 - 다시 재려고 누른 것이다."""

        self.run_at(datetime(2026, 7, 31, 23, 0))
        _, runner = self.run_at(datetime(2026, 7, 31, 23, 30), bypass=True)
        self.assertEqual(runner.du_count, 2)
        self.assertEqual(self.completed_generation(), 2)

    def test_the_next_night_scans_again(self):
        self.run_at(datetime(2026, 7, 31, 23, 0))
        _, runner = self.run_at(datetime(2026, 8, 1, 23, 0))
        self.assertEqual(runner.du_count, 2)
        self.assertEqual(self.completed_generation(), 2)

    def test_an_account_that_cannot_be_seen_tonight_keeps_last_nights_results(self):
        """마운트가 빠졌거나 경로가 지워진 밤에 빈 세대를 '완료' 로 남기면, 다음 밤의
        비교 기준이 빈 세대가 된다 - 경로가 돌아오는 순간 모든 디렉터리가 '새로
        생김' 이 되고, 100GB 넘는 것마다 급증 알림이 나간다."""

        self.run_at(datetime(2026, 7, 31, 23, 0))
        # 마운트가 빠진 밤. 마운트 지점은 빈 디렉터리로 남는다.
        self.lister = lambda path: []
        summary, _ = self.run_at(datetime(2026, 8, 1, 23, 0))
        self.assertEqual(summary.accounts[0].baseline_status, "unavailable")
        self.assertEqual(self.completed_generation(), 1)

    def test_the_reason_is_kept_for_the_screen_and_cleared_when_the_path_returns(self):
        """창은 cron 로그를 못 본다 - 건너뛴 사유를 계정 상태에 남겨 화면이 읽게 한다."""

        self.run_at(datetime(2026, 7, 31, 23, 0))
        self.lister = lambda path: []
        self.run_at(datetime(2026, 8, 1, 23, 0))
        with scan_store.session(self.data_dir) as conn:
            state = scan_store.get_account_state(conn, self.account.account_id)
        self.assertEqual(state.unavailable_reason, nightly_scan.UNAVAILABLE_EMPTY)
        self.assertIsNotNone(state.unavailable_at)
        entry = nightly_scan.get_status_snapshot(self.data_dir, self.config).accounts[0]
        self.assertEqual(entry.unavailable_reason, nightly_scan.UNAVAILABLE_EMPTY)

        self.lister = lambda path: list(self.top_dirs)
        self.run_at(datetime(2026, 8, 2, 23, 0))
        with scan_store.session(self.data_dir) as conn:
            self.assertIsNone(scan_store.get_account_state(conn, self.account.account_id).unavailable_reason)

    def test_an_account_path_that_is_gone_is_not_scanned_as_empty(self):
        self.account_path.rmdir()
        self.lister = lambda path: []
        summary, _ = self.run_at(datetime(2026, 7, 31, 23, 0))
        self.assertEqual(summary.accounts[0].baseline_status, "unavailable")
        self.assertIsNone(self.completed_generation())

    def test_an_unseen_account_keeps_its_search_index(self):
        """경로를 못 보는 밤에 검색 인덱싱까지 돌면 '아무것도 없다' 로 완주해 그
        계정의 인덱스를 통째로 지운다."""

        self.account.search_indexing = True
        self.run_at(datetime(2026, 7, 31, 23, 0))
        self.lister = lambda path: []
        summary, _ = self.run_at(datetime(2026, 8, 1, 23, 0))
        self.assertEqual(summary.accounts[0].search_status, "skipped")

    def test_a_brand_new_empty_account_is_still_fine(self):
        self.lister = lambda path: []
        summary, _ = self.run_at(datetime(2026, 7, 31, 23, 0))
        self.assertEqual(summary.accounts[0].baseline_status, "done")

    def test_a_scan_that_loses_its_lock_stops_instead_of_running_alongside(self):
        """잠금을 잃은 실행은 멈춘다 - 다른 장비가 낡은 잠금으로 보고 가져갔거나,
        누가 잠금 파일을 지웠으면 둘이 겹쳐 돈다. 남의 잠금은 그대로 둔다."""

        import json

        self.config.settings.checkpoint_workers = 1

        def stolen_after_first_du(du_call_no):
            if du_call_no == 1:
                scan_lock.lock_file(self.data_dir).write_text(json.dumps({
                    "run_id": "intruder", "pid": 1, "triggered_by": "cron",
                    "started_at": "2026-07-31T14:00:00+00:00", "host": "other-host",
                }), encoding="utf-8")

        runner = FakeCommandRunner(on_du=stolen_after_first_du)
        with self._patch_commands(runner), patch("smvwp.scan_lock.HEARTBEAT_SECONDS", 0):
            summary = nightly_scan.run_nightly_scan(
                self.data_dir, self.config, bypass_window=True,
                clock=lambda: datetime(2026, 7, 31, 23, 0), top_level_lister=self.lister,
            )
        self.assertEqual(summary.status, nightly_scan.STATUS_STOPPED)
        self.assertEqual(runner.du_count, 1)
        self.assertEqual(scan_lock.read_lock(self.data_dir).run_id, "intruder")

    def test_status_remembers_what_only_changes_when_a_generation_completes(self):
        """창은 스캔 중 5초마다 상태를 읽는다. 상위 경로·증감 조인·큰 파일 변화처럼
        완료된 세대에만 매인 값을 매번 NFS 에서 다시 읽으면, 그 읽기가 스캔과 수집의
        커밋을 붙잡아 실기에서 "database is locked" 가 났다."""

        self.run_at(datetime(2026, 7, 31, 23, 0))
        self.run_at(datetime(2026, 8, 1, 23, 0))
        cache = {}
        # 야간 스캔이 남긴 요약이 없을 때 (업데이트 뒤 첫 밤 전) - 창이 스스로 읽는 쪽을 본다.
        with patch("smvwp.nightly_scan._stored_summary", return_value=None), \
                patch("smvwp.nightly_scan.scan_store.growth_delta",
                      wraps=scan_store.growth_delta) as growth:
            first = nightly_scan.get_status_snapshot(self.data_dir, self.config, cache=cache)
            again = nightly_scan.get_status_snapshot(self.data_dir, self.config, cache=cache)
            self.assertEqual(growth.call_count, 1)
            self.assertEqual(
                [tuple(row) for row in first.accounts[0].growth],
                [tuple(row) for row in again.accounts[0].growth],
            )
            self.run_at(datetime(2026, 8, 2, 23, 0))   # 세대가 바뀌면 다시 읽는다
            growth.reset_mock()   # 스캔 자신(급증 알림)이 부른 것은 세지 않는다
            nightly_scan.get_status_snapshot(self.data_dir, self.config, cache=cache)
            self.assertEqual(growth.call_count, 1)

    def snapshot_parts(self, **kwargs):
        """스캔 탭 요약에 드는 값만 견줄 수 있는 모양으로."""

        entry = nightly_scan.get_status_snapshot(self.data_dir, self.config, **kwargs).accounts[0]
        return {
            "top_paths": [dict(row) for row in entry.top_paths],
            "growth": [dict(row) for row in entry.growth],
            "large_files": [tuple(item) for item in entry.large_files],
            "previous_measured_kb": entry.previous_measured_kb,
            "partial_paths": list(entry.partial_paths),
            "current_scan_at": entry.current_scan_at,
            "previous_scan_at": entry.previous_scan_at,
        }

    def test_the_scan_tab_uses_the_summary_the_scan_left(self):
        """스캔 탭 요약(상위·증가 경로, 이전 크기, 큰 파일 변화, 덜 센 경로)은 세대가
        끝나면 바뀌지 않는다. 창은 열 때마다 그것을 결과 표에서 줄마다 찾아 읽었다
        (계정 20개면 14MB, NFS 요청 3천여 번 - 창을 열 때의 가장 큰 읽기)."""

        self.run_at(datetime(2026, 7, 31, 23, 0))
        self.run_at(datetime(2026, 8, 1, 23, 0))
        with patch("smvwp.nightly_scan._stored_summary", return_value=None):
            computed = self.snapshot_parts()
        with patch("smvwp.nightly_scan._settled_parts", side_effect=AssertionError("결과 표를 읽었다")):
            stored = self.snapshot_parts()
        self.assertEqual(stored, computed)
        self.assertTrue(stored["growth"] and stored["top_paths"])

    def test_the_summary_is_not_used_when_what_it_shows_changed(self):
        self.run_at(datetime(2026, 7, 31, 23, 0))
        self.run_at(datetime(2026, 8, 1, 23, 0))
        changes = {
            "상위 몇 개": lambda: setattr(self.config.settings, "detail_scan_top_n", 3),
            "증가 경로 깊이": lambda: setattr(self.config.settings, "growth_list_max_depth", 1),
        }
        for name, change in changes.items():
            with self.subTest(name):
                self.config = config_module.load_config(self.data_dir)   # 바꾸기 전 설정에서
                change()
                with patch("smvwp.nightly_scan._settled_parts",
                           wraps=nightly_scan._settled_parts) as parts:
                    self.snapshot_parts()
                self.assertEqual(parts.call_count, 1)

    def test_a_newer_generation_or_format_reads_the_results_again(self):
        self.run_at(datetime(2026, 7, 31, 23, 0))
        with patch.object(nightly_scan, "SUMMARY_FORMAT", nightly_scan.SUMMARY_FORMAT + 1), \
                patch("smvwp.nightly_scan._settled_parts", wraps=nightly_scan._settled_parts) as parts:
            self.snapshot_parts()
        self.assertEqual(parts.call_count, 1)

        with scan_store.session(self.data_dir) as conn:   # 요약을 남기기 전에 끝난 세대
            scan_store.mark_generation_completed(conn, self.account.account_id, 2)
        with patch("smvwp.nightly_scan._settled_parts", wraps=nightly_scan._settled_parts) as parts:
            self.snapshot_parts()
        self.assertEqual(parts.call_count, 1)

    def test_the_scan_saves_a_summary_only_when_the_generation_changed(self):
        self.run_at(datetime(2026, 7, 31, 23, 0))
        with patch("smvwp.nightly_scan._settled_parts", wraps=nightly_scan._settled_parts) as parts:
            nightly_scan._save_summaries(self.data_dir, self.config)
        self.assertEqual(parts.call_count, 0)

    def test_a_refresh_with_memory_does_not_read_the_summary(self):
        """창은 스캔 중 5초마다 상태를 읽는다 - 요약은 기억이 없을 때만 읽는다."""

        self.run_at(datetime(2026, 7, 31, 23, 0))
        cache = {}
        with patch("smvwp.nightly_scan._stored_summary", wraps=nightly_scan._stored_summary) as read:
            nightly_scan.get_status_snapshot(self.data_dir, self.config, cache=cache)
            nightly_scan.get_status_snapshot(self.data_dir, self.config, cache=cache)
        self.assertEqual(read.call_count, 1)

    def test_a_finished_scan_leaves_the_health_check_for_the_window(self):
        """대조(헬스체크)는 두 계정의 디렉터리를 전부 읽는다 - 창이 열 때마다 하지
        않도록 사람이 기다리지 않는 밤에 남겨 둔다."""

        from smvwp import health

        config_module.set_account_kind(
            self.config, self.account.account_id, config_module.ACCOUNT_KIND_PROJECT
        )
        self.run_at(datetime(2026, 7, 31, 23, 0))
        with scan_store.session(self.data_dir) as conn:
            saved = [row[0] for row in conn.execute("SELECT account_id FROM health_results")]
        self.assertEqual(saved, [self.account.account_id])
        with patch("smvwp.scan_store.tree_entries", side_effect=AssertionError("다시 대조했다")):
            health.check_all(self.data_dir, self.config)

    def two_generations(self, recorded_times=True):
        """세대 1, 2 를 결과 표에 바로 남긴다 (`dir1` 과 그 안의 `sub`)."""

        root, sub = f"{self.account_path}/dir1", f"{self.account_path}/dir1/sub"
        with scan_store.session(self.data_dir) as conn:
            for generation, (root_kb, sub_kb) in ((1, (100, 10)), (2, (150, 60))):
                scan_store.save_tree_entries(
                    conn, self.account.account_id, generation, [(root, root_kb), (sub, sub_kb)], root
                )
                if recorded_times:
                    scan_store.mark_generation_completed(
                        conn, self.account.account_id, generation,
                        completed_at=f"2026-10-0{generation}T21:00:00+00:00",
                    )
            if not recorded_times:
                conn.execute(
                    "INSERT OR REPLACE INTO account_scan_state (account_id, last_completed_generation) "
                    "VALUES (?, 2)", (self.account.account_id,),
                )
                conn.commit()
        return root, sub

    def test_the_growth_list_follows_the_depth_setting(self):
        """`growth_list_max_depth`(기본 0) 를 스캔 탭의 증가 경로에도 지킨다.

        보고서와 상위 경로는 이 설정을 받았는데 스캔 탭의 증가 경로만 빠져 늘 깊이
        1 까지 나왔다 - 부모와 그 안의 자식이 나란히 떠 합계가 겹쳐 보였다 (설정
        주석이 막으려던 바로 그것). 덤으로 그 세대의 행을 훨씬 많이 읽었다."""

        root, sub = self.two_generations()
        entry = nightly_scan.get_status_snapshot(self.data_dir, self.config).accounts[0]
        self.assertEqual([row["path"] for row in entry.growth], [root])

        self.config.settings.growth_list_max_depth = 1
        entry = nightly_scan.get_status_snapshot(self.data_dir, self.config).accounts[0]
        self.assertEqual({row["path"] for row in entry.growth}, {root, sub})

    def test_scan_dates_come_from_the_times_recorded_when_each_scan_finished(self):
        """두 스캔의 날짜를 결과 표에서 찾지 않는다 - 그러면 세대의 행을 전부 읽는다."""

        self.two_generations()
        with patch("smvwp.nightly_scan.scan_store.generation_completed_at",
                   side_effect=AssertionError("결과 표를 훑었다")):
            entry = nightly_scan.get_status_snapshot(self.data_dir, self.config).accounts[0]
        self.assertEqual(entry.current_scan_at, "2026-10-02T21:00:00+00:00")
        self.assertEqual(entry.previous_scan_at, "2026-10-01T21:00:00+00:00")

    def test_an_old_database_without_the_times_still_has_dates(self):
        self.two_generations(recorded_times=False)
        entry = nightly_scan.get_status_snapshot(self.data_dir, self.config).accounts[0]
        self.assertIsNotNone(entry.current_scan_at)
        self.assertIsNotNone(entry.previous_scan_at)

    def flaky_dir1(self):
        real = detail_scan.process_one_checkpoint

        def process(conn, checkpoint, *args, **kwargs):
            if checkpoint["path"].endswith("dir1"):
                raise sqlite3.OperationalError("database is locked")
            return real(conn, checkpoint, *args, **kwargs)

        return patch("smvwp.nightly_scan.detail_scan.process_one_checkpoint", side_effect=process)

    def test_a_worker_that_blows_up_leaves_the_account_unfinished(self):
        """작업자 하나가 DB 오류로 죽어도 그 세대를 '완료' 로 남기지 않는다.

        예전에는 작업자가 여럿이면 예외가 스레드만 조용히 끝냈고, 다 죽으면 아무도
        중단을 알리지 않아 체크포인트가 대기 중인데도 세대가 완료로 기록됐다 -
        다음 밤의 비교 기준이 반쪽 세대가 된다."""

        self.config.settings.checkpoint_workers = 4
        with self.flaky_dir1():
            summary, _ = self.run_at(datetime(2026, 7, 31, 23, 0))
        self.assertEqual(summary.accounts[0].baseline_status, "error")
        self.assertIsNone(self.completed_generation())

    def test_one_failing_account_does_not_end_the_night(self):
        """작업자가 하나일 때 예전에는 예외가 실행 전체를 끝냈다 - 남은 계정이 그 밤을 잃었다."""

        self.config.settings.checkpoint_workers = 1
        other_path = self.tmp_root / "acct2"
        other_path.mkdir()
        config_module.add_account(self.config, "project_b", str(other_path), data_dir=self.data_dir)
        lister = lambda path: [f"{path}/dir1"] if path == str(self.account_path) else [f"{path}/x"]
        runner = FakeCommandRunner()
        with self._patch_commands(runner), self.flaky_dir1():
            summary = nightly_scan.run_nightly_scan(
                self.data_dir, self.config, clock=lambda: datetime(2026, 7, 31, 23, 0),
                top_level_lister=lister,
            )
        statuses = {o.account_name: o.baseline_status for o in summary.accounts}
        self.assertEqual(statuses, {"project_a": "error", "project_b": "done"})

    def test_lock_is_released_even_when_closing_the_run_fails(self):
        """실행 기록을 마무리하다 터져도 잠금은 풀린다.

        예전에는 `finish_run` 등이 예외를 내면 잠금을 푸는 줄까지 오지 못했다.
        cron 실행이면 프로세스가 끝나며 낡은 잠금이 되지만, **창 안에서 돈
        실행은 창이 살아 있는 한 '실행 중'으로 남아** 그 창도 그날 밤 cron 도
        스캔을 시작하지 못했다. 이 시험 프로세스가 곧 '살아 있는 창'이다."""

        locked = sqlite3.OperationalError("database is locked")
        with self._patch_commands(FakeCommandRunner()), \
                patch("smvwp.nightly_scan.scan_store.finish_run", side_effect=locked):
            with self.assertRaises(sqlite3.OperationalError):
                nightly_scan.run_nightly_scan(
                    self.data_dir,
                    self.config,
                    bypass_window=True,
                    clock=lambda: datetime(2026, 7, 31, 23, 0),
                    top_level_lister=self.lister,
                )
        self.assertFalse(scan_lock.is_locked(self.data_dir))

    def test_lock_is_released_when_the_scan_db_cannot_open(self):
        broken = sqlite3.OperationalError("unable to open database file")
        # 패키지가 다시 내보낸 이름과 원래 자리를 둘 다 막는다 - 어느 쪽으로
        # 부르든 실패해야 한다.
        with patch("smvwp.scan_store.connect", side_effect=broken), \
                patch("smvwp.scan_store.db.connect", side_effect=broken):
            with self.assertRaises(sqlite3.OperationalError):
                nightly_scan.run_nightly_scan(
                    self.data_dir,
                    self.config,
                    bypass_window=True,
                    clock=lambda: datetime(2026, 7, 31, 23, 0),
                    top_level_lister=self.lister,
                )
        self.assertFalse(scan_lock.is_locked(self.data_dir))


class FairOrderTests(unittest.TestCase):
    """하룻밤에 다 못 도는 밤이 이어져도 어느 계정도 오래 비지 않는다.

    예전에는 날짜로 시작 위치를 하루 한 칸씩 돌렸다. 계정 109개에 밤마다 50개면 어떤
    계정은 50일 연속 스캔되고 그다음 59일은 한 번도 안 됐다. 하다 만 계정도 다음 밤에
    맨 뒤로 밀렸다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        for name in "abcdefg":
            (root / name).mkdir()
            config_module.add_account(self.config, name, str(root / name), data_dir=self.data_dir)
        self.accounts = list(self.config.accounts)

    def finish(self, account, at):
        with scan_store.session(self.data_dir) as conn:
            state = scan_store.get_account_state(conn, account.account_id)
            scan_store.mark_generation_completed(
                conn, account.account_id, state.working_generation, completed_at=at
            )

    def names(self, accounts):
        return [account.name for account in accounts]

    def test_unfinished_then_never_then_oldest(self):
        a, b, c, d = self.accounts[:4]
        self.finish(a, "2026-10-20T21:00:00+00:00")
        self.finish(b, "2026-10-01T21:00:00+00:00")
        self.finish(d, "2026-10-21T21:00:00+00:00")
        with scan_store.session(self.data_dir) as conn:   # d 는 다음 세대를 하다 말았다
            scan_store.seed_checkpoints(conn, d.account_id, scan_store.BASELINE, 2, ["/d/x"])
        order = self.names(nightly_scan._fair_order(self.data_dir, [a, b, c, d]))
        self.assertEqual(order, ["d", "c", "b", "a"])

    def test_no_account_waits_long_when_nights_are_short(self):
        """계정 7개, 하룻밤에 3개만 끝내는 밤이 14번 - 어느 계정도 3밤 넘게 비지 않는다
        (날짜로 한 칸씩 돌리던 예전 순서로는 5밤이 빈다)."""

        last_seen = {}
        gaps = []
        for night in range(1, 15):
            tonight = nightly_scan._fair_order(self.data_dir, self.accounts)[:3]
            for slot, account in enumerate(tonight):
                self.finish(account, f"2026-10-{night:02d}T21:00:{slot:02d}+00:00")
                if account.name in last_seen:
                    gaps.append(night - last_seen[account.name])
                last_seen[account.name] = night
        self.assertEqual(set(last_seen), set("abcdefg"))
        self.assertLessEqual(max(gaps), 3)


class StatusIsOneReadTests(unittest.TestCase):
    """스캔 상태는 **한 번의 읽기**다 (`sqlite_db.read_snapshot`).

    계정마다 문이 여럿이라 자동 커밋으로는 상태 한 번에 잠금 왕복이 120번을 넘었다
    (데이터 디렉터리가 NFS 면 fcntl 잠금 하나가 네트워크 왕복 몇 번, 스캔 중에는
    5초마다). 묶으면 잠금을 한 번 잡고, 모든 계정을 같은 시점으로 본다."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        self.accounts = []
        for name in ("a", "b"):
            (root / name).mkdir()
            self.accounts.append(
                config_module.add_account(self.config, name, str(root / name), data_dir=self.data_dir)
            )

    def test_every_account_is_seen_at_the_same_moment(self):
        """계정 a 를 읽은 뒤 스캔이 계정 b 를 써도, 이번 상태에는 섞이지 않는다."""

        with scan_store.session(self.data_dir):
            pass   # DB 를 만든다 - 이 위치에 맞는 저널 모드로
        if (scan_store.journal_mode(self.data_dir) or "").lower() != "wal":
            self.skipTest("WAL 에서만 읽는 동안 다른 연결이 커밋할 수 있다 (NFS 는 DELETE)")
        b = self.accounts[1].account_id

        def scan_writes_b(index, _total):
            if index == 2:
                with scan_store.session(self.data_dir) as writer:
                    scan_store.save_tree_entries(writer, b, 1, [("/b/top", 500)], "/b/top")

        snapshot = nightly_scan.get_status_snapshot(
            self.data_dir, self.config, on_account=scan_writes_b
        )
        self.assertIsNone(snapshot.accounts[1].measured_kb)
        again = nightly_scan.get_status_snapshot(self.data_dir, self.config)
        self.assertEqual(again.accounts[1].measured_kb, 500)

    def test_reading_the_status_writes_nothing(self):
        """한 읽기로 묶은 안에서 쓰면(아직 스캔 안 한 계정의 행 만들기) 다른 쓰기가
        기다리는 순간 곧바로 "database is locked" 가 난다. 읽기는 읽기만 한다."""

        nightly_scan.get_status_snapshot(self.data_dir, self.config)
        with scan_store.session(self.data_dir) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM account_scan_state").fetchone()[0], 0)


class MarkInterruptedRunTests(unittest.TestCase):
    """창을 닫아 스캔 스레드가 잘렸을 때 실행 상태를 마감한다.

    이걸 안 하면 `finally`가 못 돌아 실행 행이 영영 `running`으로 남고, 다음에
    GUI를 열었을 때 "지금 뭔가 돌고 있다"로 오해하게 된다.
    """

    def test_running_row_becomes_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            conn = scan_store.connect(data_dir)
            try:
                scan_store.start_run(conn, "run-1", "gui")
            finally:
                conn.close()

            self.assertTrue(nightly_scan.mark_interrupted_run(data_dir, "run-1"))

            conn = scan_store.connect(data_dir)
            try:
                row = scan_store.latest_run(conn)
                self.assertEqual(row["status"], "stopped")
                self.assertIsNotNone(row["ended_at"])
            finally:
                conn.close()

    def test_already_finished_run_is_left_alone(self):
        """정상 종료된 실행을 나중에 stopped로 덮어쓰면 이력이 거짓이 된다."""

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            conn = scan_store.connect(data_dir)
            try:
                scan_store.start_run(conn, "run-1", "cron")
                scan_store.finish_run(conn, "run-1", "completed")
            finally:
                conn.close()

            self.assertFalse(nightly_scan.mark_interrupted_run(data_dir, "run-1"))

            conn = scan_store.connect(data_dir)
            try:
                self.assertEqual(scan_store.latest_run(conn)["status"], "completed")
            finally:
                conn.close()

    def test_someone_elses_running_row_is_left_alone(self):
        """창을 닫을 때 마감하는 것은 **그 창이 시작한 실행**뿐이다.

        예전에는 '가장 최근 실행' 을 마감했다 - cron 이 돌고 있으면 그것을 stopped 로
        바꾸고 잠금까지 풀었다."""

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with scan_store.session(data_dir) as conn:
                scan_store.start_run(conn, "cron-run", "cron")

            self.assertFalse(nightly_scan.mark_interrupted_run(data_dir, "gui-run"))
            with scan_store.session(data_dir) as conn:
                self.assertEqual(scan_store.latest_run(conn)["status"], "running")

    def test_no_runs_at_all_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(nightly_scan.mark_interrupted_run(Path(tmp), "run-1"))


if __name__ == "__main__":
    unittest.main()
