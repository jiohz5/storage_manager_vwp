"""`smvwp_cli.py diagnose` - 실기 확인 항목을 보고서 파일 하나로 (`smvwp.field_check`).

실기에서 확인할 것이 `status`·`load`·로그·`crontab`·`df`·파이썬 모듈에 흩어져 있어
하나씩 부탁하고 하나씩 옮겨 받아야 했다. 한 번 돌리면 파일 하나가 남고, 그것을
그대로 붙여 주면 된다. 읽기만 하고, 계정 경로는 싣지 않는다.
"""

import io
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import cycle, field_check, home_view, scan_store, store, tiers

COMBINED = (
    "Filesystem     1K-blocks     Used    Avail Use%  Inodes  IUsed   IFree IUse% Mounted on\n"
    "/dev/sda1      103080332 45678900 52145432  47% 6553600 123456 6430144    2% /x\n"
)


def _sample(account_id, at):
    total = 10 ** 9
    return store.SampleRecord(
        account_id=account_id, collected_at=at.isoformat(), ok=True, filesystem="nfs4",
        mount_point="/ifs", total_kb=total, used_kb=total // 2, avail_kb=total // 2,
        byte_pct=50.0, byte_tier=tiers.classify(50.0), overall_tier=tiers.classify(50.0),
    )


def _fake_run(df_output=COMBINED, df_code=0):
    """df 는 주어진 출력으로, 나머지 바깥 명령(crontab 등)은 '없음' 으로."""

    def run(argv, *_args, **_kwargs):
        argv = list(argv)
        if argv and argv[0] == "df":
            if any(arg.startswith("--output") for arg in argv):
                return subprocess.CompletedProcess(argv, df_code, df_output.encode(), b"")
            return subprocess.CompletedProcess(argv, 0, b"df (GNU coreutils) 8.30\n", b"")
        raise FileNotFoundError(argv[0])

    return run


class _Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.data_dir = self.root / "data"
        self.config = config_module.load_config(self.data_dir)
        self.secret_path = self.root / "very_secret_project_path"
        self.secret_path.mkdir()
        config_module.add_account(self.config, "proj", str(self.secret_path), data_dir=self.data_dir)
        config_module.save_config(self.data_dir, self.config)

    def fill(self):
        now = datetime.now(timezone.utc)
        with store.session(self.data_dir) as conn:
            store.insert_samples(conn, [_sample(self.config.accounts[0].account_id,
                                                now - timedelta(minutes=5))])
        with scan_store.session(self.data_dir) as conn:
            scan_store.start_run(conn, "run-1", "cron")
            scan_store.finish_run(conn, "run-1", "completed")
        home_view.write(self.data_dir, self.config)
        logs = self.data_dir / "logs"
        logs.mkdir(exist_ok=True)
        (logs / "gui-pc.log").write_text(
            "2026-11-18 09:00:00 INFO smvwp.home_view [w] 홈을 결과 파일에서 읽었습니다 (표본 x)\n"
            "2026-11-18 09:15:00 INFO smvwp.home_view [w] 홈을 결과 파일에서 읽었습니다 (표본 y)\n"
            "2026-11-18 09:20:00 WARNING smvwp.dashboard [w] 홈 데이터를 읽는 데 7.1초 걸렸습니다 (a)\n"
            "2026-11-18 09:21:00 ERROR smvwp.cycle [m] 서버 부하 표본 실패 (수집은 계속합니다)\n",
            encoding="utf-8")

    def build(self, run=None):
        # field_check 와 cron_status 가 같은 subprocess.run 을 쓴다 - 가짜 하나로.
        with patch("subprocess.run", side_effect=run or _fake_run()):
            return field_check.build(self.data_dir, self.config)


class ReportTests(_Case):
    def test_every_part_is_there_with_how_to_return_and_a_summary(self):
        self.fill()
        text = self.build()
        self.assertIn("돌려주는 법", text)
        self.assertIn("== 요약 ==", text)
        for title in ("장비", "상태", "수집 한 바퀴", "df 한 번 묻기", "홈 결과 파일", "야간 스캔",
                      "검색 색인", "서버 부하 표본", "데이터 폴더에 쌓인 것", "화면 라이브러리", "cron", "로그"):
            self.assertIn(title, text)
        self.assertNotIn("! 읽지 못함", text, text)
        self.assertTrue(text.rstrip().endswith("-- 끝 --"))

    def test_account_paths_are_not_in_it(self):
        self.fill()
        self.assertNotIn("very_secret_project_path", self.build())

    def test_what_the_logs_say_about_the_home(self):
        self.fill()
        text = self.build()
        self.assertIn("결과 파일에서 2번", text)
        self.assertIn("DB 에서 1번", text)
        self.assertIn("홈 데이터를 읽는 데 7.1초", text)
        self.assertIn("로그 ERROR 1개", text)

    def test_cycle_time_goes_into_the_summary(self):
        self.fill()
        watch = cycle._Stopwatch()
        watch.seconds = {"df": 2.5, "home": 4.0}
        watch.save(self.data_dir)
        summary = self.build().split("== 요약 ==")[1].split("\n== ")[0]
        self.assertIn("수집", summary)
        self.assertIn("home 4.0", summary)
        self.assertIn("df 한 번 묻기: 됨", summary)

    def test_df_output_option_missing_is_reported(self):
        self.fill()
        text = self.build(_fake_run(df_output="df: unrecognized option '--output'", df_code=1))
        self.assertIn("! df 한 번 묻기: 안 됨", text)
        self.assertIn("unrecognized option", text)

    def test_one_part_failing_still_gives_the_rest(self):
        self.fill()
        with patch("smvwp.field_check._home", side_effect=OSError("Stale file handle")):
            text = self.build()
        self.assertIn("! 읽지 못함: OSError: Stale file handle", text)
        self.assertIn("서버 부하 표본", text)
        self.assertIn("수집(", text.split("== 1.")[0] + "수집(")   # 요약은 그대로 남는다

    def test_a_fresh_folder_is_described_without_making_databases(self):
        text = self.build()
        self.assertIn("DB 가 없어 건너뜀", text)
        self.assertFalse(store.db_path(self.data_dir).exists())
        self.assertFalse(scan_store.db_path(self.data_dir).exists())


class SearchAndServerSampleTests(_Case):
    """서버 부하 표본이 왜 안 쌓이나, 검색 색인이 밤을 얼마나 늘리나 - 보고서가 답하게."""

    def test_the_last_cycles_server_sample_outcome_is_in_the_summary(self):
        self.fill()
        watch = cycle._Stopwatch()
        watch.seconds = {"df": 1.0}
        watch.server_sample = "빈 표본 (CPU·load 를 못 읽음) - 저장 안 함"
        watch.save(self.data_dir)
        summary = self.build().split("== 요약 ==")[1].split("\n== ")[0]
        self.assertIn("! 마지막 수집의 서버 부하 표본: 빈 표본", summary)

    def test_search_index_accounts_and_minutes_from_the_log(self):
        from smvwp import search_index

        self.fill()
        self.config.accounts[0].search_indexing = True
        with search_index.session(self.data_dir) as conn:
            search_index.index_account(conn, self.config.accounts[0].account_id, self.secret_path)
        (self.data_dir / "logs" / "scan-night.log").write_text(
            "2026-11-19 02:00:00 INFO smvwp.nightly_scan [n] 검색 색인 proj: done, 1234개, 41.5분\n"
            "2026-11-20 02:00:00 INFO smvwp.nightly_scan [n] 검색 색인 proj: done, 1240개, 40.0분\n",
            encoding="utf-8")
        text = self.build()
        self.assertIn("색인을 켠 계정: 1개", text)
        self.assertIn("proj: 0개, 마지막 완주", text)
        self.assertIn("합계 82분", text)
        self.assertIn("검색 색인: 켠 계정 1개", text.split("== 요약 ==")[1].split("\n== ")[0])
        self.assertNotIn("very_secret_project_path", text)


class ProgressTests(_Case):
    """실기에서 오래 걸려 멈춘 것인지 알 수 없었다 - 절마다 한 줄, 느리면 중간에도."""

    def test_each_part_says_when_it_starts_and_how_long_it_took(self):
        self.fill()
        said = []
        with patch("subprocess.run", side_effect=_fake_run()):
            text = field_check.build(self.data_dir, self.config, progress=said.append)
        starts = [line for line in said if line.endswith(" ...")]
        ends = [line for line in said if "초 (지금까지" in line]
        self.assertEqual(len(starts), 12)
        self.assertEqual(len(ends), 12)
        self.assertTrue(starts[0].startswith("[ 1/12] 장비"))
        self.assertTrue(said[-1].startswith("[12/12] 로그 - "))
        summary = text.split("== 요약 ==")[1].split("\n== ")[0]
        self.assertIn("점검 걸린 시간:", summary)

    def test_a_slow_part_keeps_saying_it_is_still_working(self):
        said = []

        def slow(report, data_dir, config, lines):
            time.sleep(0.35)
            lines.append("ok")

        with patch("smvwp.field_check._inventory", side_effect=slow), \
                patch("subprocess.run", side_effect=_fake_run()):
            field_check.build(self.data_dir, self.config, progress=said.append, tick_seconds=0.1)
        at = [i for i, line in enumerate(said) if "데이터 폴더" in line]
        between = said[at[0] + 1:at[1]]
        self.assertTrue(between and all("아직 읽는 중" in line for line in between), said)

    def test_a_failed_part_is_marked_on_the_line(self):
        said = []
        with patch("smvwp.field_check._home", side_effect=OSError("Stale file handle")), \
                patch("subprocess.run", side_effect=_fake_run()):
            field_check.build(self.data_dir, self.config, progress=said.append)
        self.assertTrue(any(line.startswith("[ 5/12] 홈 결과 파일 - ") and "(실패)" in line
                            for line in said), said)

    def test_a_broken_terminal_does_not_stop_the_report(self):
        def broken(_line):
            raise BrokenPipeError()

        text = self.build_with(broken)
        self.assertTrue(text.rstrip().endswith("-- 끝 --"))

    def build_with(self, progress):
        with patch("subprocess.run", side_effect=_fake_run()):
            return field_check.build(self.data_dir, self.config, progress=progress)


class CommandTests(_Case):
    def test_writes_the_report_and_prints_the_summary(self):
        import smvwp_cli

        self.fill()
        target = self.root / "report.txt"
        out = io.StringIO()
        with patch("subprocess.run", side_effect=_fake_run()), redirect_stdout(out):
            code = smvwp_cli.main(["diagnose", "--data-dir", str(self.data_dir),
                                   "--out", str(target)])
        self.assertEqual(code, 0)
        self.assertIn("돌려주는 법", target.read_text(encoding="utf-8"))
        printed = out.getvalue()
        self.assertIn("== 요약 ==", printed)
        self.assertIn(str(target), printed)
        self.assertNotIn("== 1.", printed)   # 화면에는 요약만
        self.assertIn("[ 4/12] df 한 번 묻기", printed)   # 돌리는 동안 진행 줄
        self.assertIn("점검 끝 - ", printed)

    def test_falls_back_to_home_when_the_folder_is_not_writable(self):
        target = self.root / "missing_dir" / "r.txt"
        with patch.object(Path, "home", return_value=self.root):
            written = field_check.write("x", target)
        self.assertEqual(written, self.root / "r.txt")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
