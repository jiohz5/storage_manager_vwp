import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import nightly_scan, scan_lock, scan_store


class FakeCommandRunner:
    """`du`와 `find`를 명령 내용으로 구분해 가짜 결과를 돌려주는 단일 러너.

    주의(이 파일에서 한 번 실수했던 부분): `smvwp.detail_scan.subprocess`와
    `smvwp.activity_scan.subprocess`는 서로 다른 객체가 아니라 **같은 표준
    subprocess 모듈 객체**다. 따라서 두 경로를 각각 patch하면 나중에 적용된
    patch가 앞의 것을 그대로 덮어써서, `du` 호출이 `find`용 가짜 결과(빈
    stdout)를 받아 엉뚱하게 실패한다. 그래서 patch는 한 번만 걸고, 그 하나가
    명령을 보고 분기하도록 했다.

    `on_du`는 "N번째 du 호출 직후"에 끼어들 훅이다 - 스캔 도중 안전 중지를
    요청하는 상황을 만들 때 쓴다.
    """

    def __init__(self, on_du=None):
        self.du_calls = []
        self.find_calls = []
        self.on_du = on_du

    def __call__(self, command, **kwargs):
        if "du" in command:
            self.du_calls.append(command)
            if self.on_du is not None:
                self.on_du(len(self.du_calls))
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=f"100\t{command[-1]}\n", stderr=""
            )
        if "find" in command:
            self.find_calls.append(command)
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")
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

        self.config = config_module.load_config(self.data_dir)
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
        self.assertEqual(outcome.activity_status, "done")

        # 최상위 디렉터리 2개에 대해 du/find가 각각 한 번씩 실행되어야 한다.
        self.assertEqual(runner.du_count, 2)
        self.assertEqual(len(runner.find_calls), 2)

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


if __name__ == "__main__":
    unittest.main()
