"""작업 스레드 바탕(`scheduler.ThreadWorker`)과 그 위의 워커들.

## 왜 이 파일이 있나

같은 모양의 "잠금 + 도는 중 표시 + daemon 스레드 + 신호" 가 아홉 군데에 복사돼
있었다. 복사본마다 조금씩 달랐고, 그 차이가 두 번 사고가 됐다:

- 새 창을 만들 때마다 `emit_safely` 를 잊어 창을 닫으면 터졌다.
- 대시보드 워커는 도는 중에 들어온 요청을 **버렸다.** 창을 열면 대시보드
  읽기와 첫 수집이 함께 시작되는데, 수집이 먼저 끝나면 그 뒤의 새로고침이
  버려져 표가 다음 수집(15분)까지 옛 값으로 남았다.

한 곳으로 모으고, 그 한 곳을 여기서 지킨다.
"""

import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PyQt5 import QtCore
except ImportError:  # pragma: no cover - PyQt5 없는 환경
    QtCore = None
HAVE_QT = QtCore is not None


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@unittest.skipUnless(HAVE_QT, "PyQt5 없음")
class ThreadWorkerTests(unittest.TestCase):
    def worker(self, work, coalesce=False):
        from smvwp.scheduler import ThreadWorker

        class Worker(ThreadWorker):
            COALESCE = coalesce

            def _work(self, *args):
                return work(*args)

        made = Worker()
        results = []
        made.finished.connect(results.append)
        made.failed.connect(lambda message: results.append(("failed", message)))
        return made, results

    def test_a_result_comes_back(self):
        worker, results = self.worker(lambda value: value * 2)
        worker._run(21)
        self.assertEqual(results, [42])

    def test_an_exception_becomes_a_failed_signal_not_a_dead_thread(self):
        def boom():
            raise RuntimeError("nope")

        worker, results = self.worker(boom)
        worker._run()
        self.assertEqual(results, [("failed", "nope")])

    def test_it_does_not_run_twice_at_once(self):
        gate = threading.Event()
        calls = []

        def slow():
            calls.append(1)
            gate.wait(5)

        worker, _ = self.worker(slow)
        self.assertTrue(worker.start())
        self.assertFalse(worker.start(), "도는 중에 또 시작하면 안 된다")
        gate.set()
        self.assertTrue(wait_until(lambda: not worker.is_running()))
        self.assertEqual(len(calls), 1)

    def test_a_request_during_a_run_is_kept_when_asked(self):
        """버리면 안 되는 워커가 있다 - 요청이 드문 쪽이다."""

        gate = threading.Event()
        calls = []

        def slow(tag):
            calls.append(tag)
            if tag == "first":
                gate.wait(5)

        worker, _ = self.worker(slow, coalesce=True)
        worker.start("first")
        worker.start("second")
        worker.start("third")   # 마지막 요청만 남는다
        gate.set()
        self.assertTrue(wait_until(lambda: not worker.is_running()))
        self.assertEqual(calls, ["first", "third"])

    def test_it_is_free_again_by_the_time_the_result_arrives(self):
        """결과를 받은 쪽이 곧바로 다시 요청하면 받아 줘야 한다.

        예전 워커 몇은 신호를 먼저 쏘고 표시를 나중에 풀어서, 받은 쪽의 재요청이
        "아직 도는 중" 으로 버려질 수 있었다."""

        from smvwp.scheduler import ThreadWorker

        seen = []

        class Worker(ThreadWorker):
            def _work(self):
                return 1

        worker = Worker()
        worker.finished.connect(lambda _value: seen.append(worker.is_running()))
        worker._run()
        self.assertEqual(seen, [False])

    def test_a_deleted_receiver_does_not_raise_from_the_thread(self):
        """창을 닫으면 C++ 쪽이 먼저 사라진다. 그때 쏘는 신호가 터지면 안 된다."""

        from smvwp.scheduler import ThreadWorker

        class Worker(ThreadWorker):
            def _work(self):
                return 1

        worker = Worker()
        with patch("smvwp.scheduler.emit_safely") as emit:
            worker._run()
        emit.assert_called_once()


@unittest.skipUnless(HAVE_QT, "PyQt5 없음")
class DashboardRefreshTests(unittest.TestCase):
    """창을 열면 대시보드 읽기와 첫 수집이 함께 시작된다.

    수집이 먼저 끝나면 그 뒤의 새로고침이 "이미 읽는 중" 으로 버려져, 방금
    수집한 값이 다음 수집(15분)까지 표에 안 나왔다. `새로고침` 버튼이 안 먹는
    것처럼 보이는 원인이기도 했다."""

    def test_a_refresh_asked_during_a_read_is_not_lost(self):
        from smvwp.scheduler import DashboardWorker

        gate = threading.Event()
        calls = []

        def read(data_dir, config):
            calls.append(len(calls))
            if len(calls) == 1:
                gate.wait(5)
            return object()

        with patch("smvwp.dashboard.read_dashboard", side_effect=read):
            worker = DashboardWorker(Path("."), get_config=lambda: None)
            worker.refresh_async()          # 창을 열 때
            worker.refresh_async()          # 첫 수집이 끝났을 때
            gate.set()
            self.assertTrue(wait_until(lambda: not worker.is_running()))
        self.assertEqual(len(calls), 2, "수집 뒤의 새로고침이 버려졌다")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
