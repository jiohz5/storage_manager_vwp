"""외부 명령 입출력이 로케일과 무관하게 UTF-8인지 검증한다.

이 파일이 지키는 것은 하나다: cron 환경(`LANG` 없음 또는 `LANG=C`)에서도
한글이 든 알림과 비ASCII 경로가 깨지거나 예외를 내지 않아야 한다. 실제로
`subprocess.run(text=True)`를 쓰던 시절 한국어 Windows에서 CP949로 인코딩되어
수신 프로그램이 UnicodeDecodeError를 낸 적이 있다.
"""

from __future__ import annotations

import subprocess
import sys
import unittest

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
