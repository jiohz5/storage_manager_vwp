"""화면 스레드가 멈춘 때와 자리를 남기는가 (`smvwp.gui.stall_watch`)."""

import os
import threading
import time
import unittest
from unittest.mock import patch

from smvwp.gui import stall_watch


class StallWatchTests(unittest.TestCase):
    def watch(self):
        watch = stall_watch.StallWatch(stall_seconds=0.15, check_seconds=0.02)
        thread = threading.Thread(target=watch._watch, daemon=True)
        thread.start()
        self.addCleanup(watch.stop)
        return watch

    def busy_screen_work(self, seconds):
        time.sleep(seconds)   # 화면 스레드가 NFS 를 읽느라 굳은 것

    def test_a_stall_is_logged_with_how_long_and_where(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with patch.object(stall_watch, "_PACKAGE_DIR", here), \
                self.assertLogs("smvwp.gui.stall_watch", "WARNING") as logs:
            watch = self.watch()
            self.busy_screen_work(0.5)
            watch.beat()
            deadline = time.monotonic() + 2
            while not watch.stalls and time.monotonic() < deadline:
                time.sleep(0.02)
        self.assertEqual(len(watch.stalls), 1)
        seconds, at = watch.stalls[0]
        self.assertGreaterEqual(seconds, 0.4)
        self.assertIn("busy_screen_work", at)
        self.assertIn(stall_watch.STALL_PREFIX, logs.output[0])

    def test_a_quiet_screen_logs_nothing(self):
        watch = self.watch()
        for _ in range(10):
            watch.beat()
            time.sleep(0.03)
        self.assertEqual(watch.stalls, [])

    def test_startup_marks_are_logged_once(self):
        stall_watch.started(time.monotonic() - 3)
        self.addCleanup(lambda: setattr(stall_watch, "_started_at", None))
        with self.assertLogs("smvwp.gui.stall_watch", "INFO") as logs:
            self.assertGreaterEqual(stall_watch.mark("홈 데이터"), 3)
            self.assertIsNone(stall_watch.mark("홈 데이터"))
        self.assertEqual(len(logs.output), 1)
        self.assertIn("창 시작 뒤 홈 데이터까지 3.", logs.output[0])


if __name__ == "__main__":
    unittest.main()
