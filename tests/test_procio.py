"""외부 명령 입출력이 로케일과 무관하게 UTF-8인지 검증한다.

이 파일이 지키는 것은 하나다: cron 환경(`LANG` 없음 또는 `LANG=C`)에서도
한글이 든 알림과 비ASCII 경로가 깨지거나 예외를 내지 않아야 한다. 실제로
`subprocess.run(text=True)`를 쓰던 시절 한국어 Windows에서 CP949로 인코딩되어
수신 프로그램이 UnicodeDecodeError를 낸 적이 있다.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import procio

KOREAN_TEXT = "경고 급증 감지 - 사용률 97%"


class Utf8RoundTripTests(unittest.TestCase):
    """실제 자식 프로세스를 띄워 왕복시킨다 (목이 아니라 진짜 파이프)."""

    def test_stdin_and_stdout_round_trip_korean(self):
        script = (
            "import sys;"
            "data = sys.stdin.buffer.read().decode('utf-8');"
            "sys.stdout.buffer.write(data.encode('utf-8'))"
        )
        result = procio.run_utf8([sys.executable, "-c", script], input_text=KOREAN_TEXT)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, KOREAN_TEXT)

    def test_stdout_is_decoded_as_str(self):
        script = "import sys; sys.stdout.buffer.write('가나다'.encode('utf-8'))"
        result = procio.run_utf8([sys.executable, "-c", script])

        self.assertIsInstance(result.stdout, str)
        self.assertEqual(result.stdout, "가나다")

    def test_stderr_is_decoded(self):
        script = "import sys; sys.stderr.buffer.write('오류'.encode('utf-8')); sys.exit(3)"
        result = procio.run_utf8([sys.executable, "-c", script])

        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stderr, "오류")

    def test_invalid_utf8_output_does_not_raise(self):
        """UTF-8이 아닌 파일 이름이 섞여도 스캔 전체가 죽으면 안 된다."""

        script = "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe done')"
        result = procio.run_utf8([sys.executable, "-c", script])

        self.assertIn("ok", result.stdout)
        self.assertIn("done", result.stdout)

    def test_empty_output_is_empty_string_not_none(self):
        result = procio.run_utf8([sys.executable, "-c", "pass"])
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_missing_binary_raises_file_not_found(self):
        """예외는 그대로 올려 호출부가 기존처럼 처리하게 한다."""

        with self.assertRaises(FileNotFoundError):
            procio.run_utf8(["/definitely/not/a/real/binary/xyz"])

    def test_timeout_propagates(self):
        script = "import time; time.sleep(5)"
        with self.assertRaises(subprocess.TimeoutExpired):
            procio.run_utf8([sys.executable, "-c", script], timeout=1)

    def test_shell_is_never_used(self):
        """argv를 그대로 넘긴다 - 세미콜론이 있어도 두 명령으로 쪼개지면 안 된다."""

        script = "import sys; sys.stdout.buffer.write(sys.argv[1].encode('utf-8'))"
        payload = "hello; rm -rf /"
        result = procio.run_utf8([sys.executable, "-c", script, payload])

        self.assertEqual(result.stdout, payload)


if __name__ == "__main__":
    unittest.main()


class ChildCleanupTests(unittest.TestCase):
    """창을 닫을 때 우리가 띄운 자식만 정리한다.

    부모가 그냥 종료하면 자식(du/find)은 죽지 않고 init에 재부모화되어 끝까지
    돈다 - 창을 껐는데도 파일서버 부하가 계속되는 상태다.
    """

    def test_lists_only_my_own_children(self):
        me = os.getpid()
        proc_dir = None
        with tempfile.TemporaryDirectory() as tmp:
            proc_dir = Path(tmp)
            # 내 자식 하나, 남의 자식 하나, 그리고 숫자가 아닌 항목
            for pid, ppid in (("111", me), ("222", 99999)):
                entry = proc_dir / pid
                entry.mkdir()
                (entry / "status").write_text(
                    f"Name:\tdu\nState:\tR\nPPid:\t{ppid}\n", encoding="utf-8"
                )
            (proc_dir / "self").mkdir()

            with patch.object(procio, "PROC_DIR", proc_dir):
                found = procio.live_child_pids()

        self.assertEqual(found, [111])

    def test_no_proc_filesystem_is_empty_not_error(self):
        """리눅스가 아니면 조용히 '정리할 것 없음'이어야 한다."""

        with patch.object(procio, "PROC_DIR", Path("/nonexistent-proc")):
            self.assertEqual(procio.live_child_pids(), [])
            self.assertEqual(procio.terminate_children(), 0)

    def test_terminate_signals_each_child_once(self):
        killed = []

        with patch.object(procio, "live_child_pids", side_effect=[[7, 9], [], []]):
            with patch("smvwp.procio.os.kill", side_effect=lambda pid, sig: killed.append((pid, sig))):
                count = procio.terminate_children(timeout=0.5)

        self.assertEqual(count, 2)
        self.assertEqual([pid for pid, _ in killed], [7, 9])
        self.assertTrue(all(sig == signal.SIGTERM for _, sig in killed))
