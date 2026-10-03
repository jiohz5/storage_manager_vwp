"""창의 오류 기록 (`smvwp.applog`).

창의 표준 오류는 run.csh 를 띄운 터미널로만 나가서, 터미널을 닫으면 무엇이
잘못됐는지 남는 것이 없었다. 처리되지 않은 예외는 PyQt5 가 앱을 통째로 끝냈다.
"""

import ast
import logging
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import applog


class _Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.path = Path(tmp.name) / "logs" / "gui-test.log"
        root = logging.getLogger()
        before = list(root.handlers)
        hooks = (sys.excepthook, threading.excepthook)

        def restore():
            # 붙인 처리기를 닫고 나서 지운다 - 열린 파일은 윈도우에서 못 지운다.
            for handler in list(root.handlers):
                if handler not in before:
                    root.removeHandler(handler)
                    handler.close()
            sys.excepthook, threading.excepthook = hooks
            tmp.cleanup()

        self.addCleanup(restore)

    def read(self) -> str:
        for handler in logging.getLogger().handlers:
            handler.flush()
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""


class LogToFileTests(_Case):
    def test_problems_are_kept_and_routine_is_not(self):
        applog.log_to_file(self.path)
        logging.getLogger("smvwp.sample").info("평소 일")
        logging.getLogger("smvwp.sample").warning("문제")
        text = self.read()
        self.assertIn("문제", text)
        self.assertNotIn("평소 일", text)

    def test_no_file_until_something_goes_wrong(self):
        applog.log_to_file(self.path)
        self.assertFalse(self.path.exists())

    def test_attaching_twice_writes_once(self):
        applog.log_to_file(self.path)
        applog.log_to_file(self.path)
        logging.getLogger("smvwp.sample").error("한 번만")
        self.assertEqual(self.read().count("한 번만"), 1)

    def test_size_is_capped(self):
        handler = applog.log_to_file(self.path)
        self.assertEqual(handler.maxBytes, applog.MAX_BYTES)
        self.assertEqual(handler.backupCount, applog.BACKUP_COUNT)

    def test_each_host_gets_its_own_file(self):
        """데이터 디렉터리는 NFS 에서 여러 장비가 함께 쓴다 - 한 파일에 덧붙이지 않는다."""

        with patch("smvwp.applog.socket.gethostname", return_value="node07.corp.example"):
            self.assertEqual(applog.gui_log_path(Path("data")).name, "gui-node07.log")


class KeepRunningTests(_Case):
    def test_uncaught_error_is_written_down(self):
        applog.log_to_file(self.path)
        applog.keep_running_on_errors()
        try:
            raise ValueError("슬롯에서 터짐")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        text = self.read()
        self.assertIn("처리되지 않은 예외", text)
        self.assertIn("ValueError: 슬롯에서 터짐", text)

    def test_thread_error_is_written_down(self):
        applog.log_to_file(self.path)
        applog.keep_running_on_errors()

        def boom():
            raise RuntimeError("스레드에서 터짐")

        worker = threading.Thread(target=boom, name="smvwp-sample")
        worker.start()
        worker.join()
        text = self.read()
        self.assertIn("smvwp-sample", text)
        self.assertIn("RuntimeError: 스레드에서 터짐", text)


ROOT = Path(__file__).resolve().parent.parent

try:
    import PyQt5  # noqa: F401 - 있는지만 본다
except ImportError:  # pragma: no cover - PyQt5 없는 환경
    PyQt5 = None

SLOT_PROBE = """
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, {root!r})
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
app = QApplication([])
if {hook!r}:
    from smvwp import applog
    applog.keep_running_on_errors()
def broken():
    raise ValueError("slot failed")
def alive():
    print("ALIVE", flush=True)
    app.quit()
QTimer.singleShot(0, broken)
QTimer.singleShot(50, alive)
app.exec_()
"""


@unittest.skipIf(PyQt5 is None, "PyQt5 없음")
class SlotErrorTests(unittest.TestCase):
    """슬롯 하나의 예외에 창 전체가 꺼지지 않는다. 따로 띄운 프로세스에서 본다."""

    def run_probe(self, hook: bool):
        code = SLOT_PROBE.format(root=str(ROOT), hook=hook)
        return subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
        )

    def test_window_survives_an_error_in_a_slot(self):
        result = self.run_probe(True)
        self.assertIn("ALIVE", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_without_the_hook_pyqt_ends_the_app(self):
        """위 시험이 뜻을 갖는 근거. PyQt5 의 기본 동작이 바뀌면 여기서 드러난다."""

        result = self.run_probe(False)
        self.assertNotIn("ALIVE", result.stdout)
        self.assertNotEqual(result.returncode, 0)


class GuiInstallsItTests(unittest.TestCase):
    def test_gui_entry_point_installs_both(self):
        tree = ast.parse((ROOT / "smvwp_cli.py").read_text(encoding="utf-8"))
        command_gui = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "command_gui"
        )
        called = {
            node.func.attr for node in ast.walk(command_gui)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "applog"
        }
        self.assertLessEqual({"log_to_file", "keep_running_on_errors"}, called)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
