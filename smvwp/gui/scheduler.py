"""PyQt6용 주기 수집 타이머와 백그라운드 워커.

무거운 작업을 GUI 스레드에서 돌리지 않는다는 원칙도 그대로다:
- `df` 수집은 네트워크 파일시스템에서 느려질 수 있다.
- 야간 상세 스캔은 몇 시간까지 갈 수 있다.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .. import nightly_scan
from ..cycle import run_collection_cycle


class CollectorWorker(QObject):
    """한 번의 수집 사이클을 백그라운드 스레드에서 실행한다."""

    finished = pyqtSignal(list)  # List[store.SampleRecord]
    failed = pyqtSignal(str)

    def __init__(self, data_dir: Path, get_config, parent: QObject = None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._get_config = get_config
        self._lock = threading.Lock()
        self._running = False

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def run_once_async(self) -> None:
        with self._lock:
            if self._running:
                return  # 이전 수집이 아직 끝나지 않았으면 겹쳐 돌리지 않는다.
            self._running = True
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def _run(self) -> None:
        try:
            records = run_collection_cycle(self._data_dir, self._get_config())
            self.finished.emit(records)
        except Exception as exc:  # pragma: no cover - 방어적 처리
            self.failed.emit(str(exc))
        finally:
            with self._lock:
                self._running = False


class CollectorScheduler:
    """QTimer로 `run_collection_cycle`을 주기적으로 호출하는 얇은 래퍼."""

    def __init__(self, data_dir: Path, get_config, on_finished=None, on_failed=None):
        self._data_dir = data_dir
        self._get_config = get_config
        self._worker = CollectorWorker(data_dir, get_config)
        if on_finished is not None:
            self._worker.finished.connect(on_finished)
        if on_failed is not None:
            self._worker.failed.connect(on_failed)
        self._timer = QTimer()
        self._timer.timeout.connect(self._worker.run_once_async)

    def start(self, run_immediately: bool = True) -> None:
        interval_seconds = self._get_config().settings.collector_interval_seconds
        self._timer.start(interval_seconds * 1000)
        if run_immediately:
            self._worker.run_once_async()

    def stop(self) -> None:
        self._timer.stop()

    def trigger_now(self) -> None:
        self._worker.run_once_async()

    def restart_with_current_interval(self) -> None:
        if self._timer.isActive():
            interval_seconds = self._get_config().settings.collector_interval_seconds
            self._timer.start(interval_seconds * 1000)


class NightlyScanWorker(QObject):
    """야간 상세 스캔(`du`/`find`)을 백그라운드 스레드에서 실행한다.

    중복 실행 방지는 이 클래스의 `_running` 플래그와 `smvwp.scan_lock`의 파일
    잠금 두 겹으로 막는다 - 앞의 것은 이 창 안에서, 뒤의 것은 cron 등 다른
    프로세스까지 포함해서.

    중지는 강제 종료가 아니라 run ID 매칭 요청 파일만 남기고, 스캐너가 다음
    체크포인트에서 스스로 멈춘다.
    """

    finished = pyqtSignal(object)  # nightly_scan.RunSummary
    failed = pyqtSignal(str)

    def __init__(self, data_dir: Path, get_config, parent: QObject = None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._get_config = get_config
        self._lock = threading.Lock()
        self._running = False

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def run_async(self, bypass_window: bool = False) -> bool:
        """스캔을 시작한다. 이미 이 창에서 돌고 있으면 False."""

        with self._lock:
            if self._running:
                return False
            self._running = True
        thread = threading.Thread(target=self._run, args=(bypass_window,), daemon=True)
        thread.start()
        return True

    def _run(self, bypass_window: bool) -> None:
        try:
            summary = nightly_scan.run_nightly_scan(
                self._data_dir,
                self._get_config(),
                triggered_by="gui",
                bypass_window=bypass_window,
            )
            self.finished.emit(summary)
        except Exception as exc:  # pragma: no cover - 방어적 처리
            self.failed.emit(str(exc))
        finally:
            with self._lock:
                self._running = False

    def request_stop(self) -> bool:
        return nightly_scan.request_stop(self._data_dir)


class SearchIndexWorker(QObject):
    """검색 인덱싱을 백그라운드에서 실행한다 (파일시스템 전체 순회)."""

    finished = pyqtSignal(int)
    failed = pyqtSignal(str)

    def __init__(self, data_dir: Path, parent: QObject = None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._lock = threading.Lock()
        self._running = False
        self._stop = False

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def request_stop(self) -> None:
        self._stop = True

    def run_async(self, account_id: str, account_path: Path) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._stop = False
        threading.Thread(
            target=self._run, args=(account_id, account_path), daemon=True
        ).start()
        return True

    def _run(self, account_id: str, account_path: Path) -> None:
        from .. import search_index

        conn = None
        try:
            conn = search_index.connect(self._data_dir)
            count = search_index.index_account(
                conn, account_id, account_path, should_stop=lambda: self._stop
            )
            self.finished.emit(count)
        except Exception as exc:  # pragma: no cover - 방어적 처리
            self.failed.emit(str(exc))
        finally:
            if conn is not None:
                conn.close()
            with self._lock:
                self._running = False
