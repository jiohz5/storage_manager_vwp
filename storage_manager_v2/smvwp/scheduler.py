"""GUI 안에서 도는 15분 주기 수집 타이머 (백그라운드 타이머 경로).

REBUILD_CONCEPT.md 7절 3번: "15분 주기 경량 수집 (cron 또는 백그라운드
타이머)". phase 1은 두 경로를 모두 제공한다 - cron은 `collector_cli.py`
스크립트로, 백그라운드 타이머는 이 모듈로. GUI를 열어 두면 cron 설정 없이도
바로 동작하고, cron을 등록해 두면 GUI를 닫아도 수집은 계속된다.

df 호출이 느려질 수 있으므로(네트워크 파일시스템 등) 실제 수집은 별도
스레드에서 실행해 GUI가 멈추지 않게 한다. PyQt5 시그널은 스레드 간에 안전하게
쓸 수 있으므로, 작업 스레드가 끝나면 시그널로 결과를 메인 스레드에 전달한다.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from . import nightly_scan
from .cycle import run_collection_cycle


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

    상세 스캔은 15분 수집과 달리 몇 시간까지도 이어질 수 있으므로 GUI 스레드
    에서 절대 돌리면 안 된다. 중복 실행 방지는 이 클래스의 `_running` 플래그와
    `smvwp.scan_lock`의 파일 잠금 두 겹으로 막는다 - 앞의 것은 이 창 안에서,
    뒤의 것은 cron 등 다른 프로세스까지 포함해서.

    중지는 강제 종료가 아니라 `nightly_scan.request_stop`(run ID 매칭 파일)로
    요청만 하고, 스캐너가 다음 체크포인트에서 스스로 멈춘다 (CONCEPT.md 2-4).
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
