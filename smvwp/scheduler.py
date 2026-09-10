"""GUI 안에서 도는 15분 주기 수집 타이머 (백그라운드 타이머 경로).

DESIGN.md 2부 7절 3번: "15분 주기 경량 수집 (cron 또는 백그라운드
타이머)". phase 1은 두 경로를 모두 제공한다 - cron은 `smvwp_cli.py collect`
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

from . import dashboard, nightly_scan
from .cycle import run_collection_cycle


def _emit(owner, signal_name: str, payload) -> None:
    """작업 스레드에서 신호를 보낸다. 받을 쪽이 이미 사라졌으면 조용히 만다.

    창을 닫는 순간 흔히 일어난다 - 위젯(과 그 자식인 워커)의 C++ 쪽은 이미
    지워졌는데 daemon 스레드는 아직 돌고 있다. 그때 `emit` 이 RuntimeError 를
    던지고, 그것이 스레드 밖으로 나가면 종료 중에 스택트레이스가 찍힌다.
    사용자에게는 "닫았더니 뭔가 터졌다"로 보인다.

    **신호를 객체째 받지 않고 이름으로 받는 이유가 있다.** 예전에는
    신호 객체를 그대로 넘겼는데, 그 인자를 만들며 속성을 읽는 것 자체가 이미
    지워진 객체에서는 RuntimeError 를 던진다. 인자를 만드는 동안 터지므로
    이 함수 안에 들어오지도 못했고, 결국 막으려던 스택트레이스가 그대로
    찍혔다 - 게다가 except 절의 `self.failed` 에서 한 번 더 터져
    "During handling of the above exception" 까지 붙었다.

    할 일이 없어진 것뿐이므로 삼키는 것이 맞다.
    """

    try:
        getattr(owner, signal_name).emit(payload)
    except Exception:
        # RuntimeError("wrapped C/C++ object has been deleted") 가 대표적이지만,
        # 종료 중에는 다른 모양으로도 나온다. 어느 쪽이든 **받을 쪽이 사라진
        # 것**이라 할 일이 없다 - 여기서 예외가 새면 daemon 스레드가 죽으면서
        # 종료 중에 스택트레이스가 찍히고, 사용자에게는 "닫았더니 터졌다"로
        # 보인다.
        #
        # 슬롯 안의 진짜 오류를 삼키지는 않는다. 이 신호들은 작업 스레드에서
        # GUI 스레드로 가는 큐 연결이라, 슬롯 예외는 애초에 여기로 돌아오지
        # 않는다.
        pass


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
            _emit(self, "finished", records)
        except Exception as exc:  # pragma: no cover - 방어적 처리
            _emit(self, "failed", str(exc))
        finally:
            with self._lock:
                self._running = False


class ScanStatusWorker(QObject):
    """스캔 상태 스냅샷을 **백그라운드 스레드에서** 읽는다.

    ## 왜 스레드로 빼는가

    `get_status_snapshot`은 계정마다 십여 개의 쿼리를 던진다. 로컬 디스크에서는
    무시할 만하지만, 데이터 디렉터리가 NFS 위에 있으면 캐시에 없는 페이지마다
    네트워크 읽기가 된다. 화면은 이것을 **5초마다** 부르므로, GUI 스레드에서
    돌리면 그 시간 동안 창이 통째로 멈춘다 - 실기에서 실제로 그랬다.

    ## 겹쳐 돌리지 않는다

    NFS가 느려 한 번이 5초를 넘기면 요청이 계속 쌓여 상황이 더 나빠진다.
    이전 조회가 끝나지 않았으면 이번 차례는 그냥 건너뛴다 - 어차피 다음
    주기에 최신값을 다시 읽는다.
    """

    finished = pyqtSignal(object)  # nightly_scan.StatusSnapshot
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

    def refresh_async(self) -> bool:
        """조회를 시작한다. 이미 돌고 있으면 False (건너뜀)."""

        with self._lock:
            if self._running:
                return False
            self._running = True
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            snapshot = nightly_scan.get_status_snapshot(self._data_dir, self._get_config())
            _emit(self, "finished", snapshot)
        except Exception as exc:  # pragma: no cover - 방어적 처리
            _emit(self, "failed", str(exc))
        finally:
            with self._lock:
                self._running = False


class DashboardWorker(QObject):
    """대시보드 데이터를 **백그라운드 스레드에서** 읽는다.

    ## 왜 스레드로 빼는가

    `ScanStatusWorker` 와 같은 이유인데, 이쪽이 더 무겁다. 표본을 읽는 것은
    쿼리 하나지만 그 뒤의 FULL 예측은 **계정마다 이력을 다시 훑어** 추세를
    맞춘다. 데이터 디렉터리가 NFS 위면 그 시간 동안 창이 통째로 멈춘다.

    수집이 끝날 때마다(15분) 불리므로, 사용자는 15분마다 한 번씩 멈추는 창을
    보게 된다. 스캔 상태 쪽은 이미 스레드로 뺐는데 여기만 남아 있었다.

    ## 겹쳐 돌리지 않는다

    이전 계산이 안 끝났으면 이번 차례는 건너뛴다 - 쌓아 봐야 더 느려질 뿐이고,
    어차피 다음 갱신이 최신값을 다시 읽는다.
    """

    finished = pyqtSignal(object)  # DashboardData
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

    def refresh_async(self) -> bool:
        """읽기를 시작한다. 이미 돌고 있으면 False (건너뜀)."""

        with self._lock:
            if self._running:
                return False
            self._running = True
        threading.Thread(target=self._run, name="smvwp-dashboard", daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            _emit(self, "finished", self._read())
        except Exception as exc:  # pragma: no cover - 방어적 처리
            _emit(self, "failed", str(exc))
        finally:
            with self._lock:
                self._running = False

    def _read(self) -> dashboard.DashboardData:
        # 읽는 방법 자체는 `dashboard` 에 있다 - 개발 PC 에 PyQt5 가 없어
        # 이 파일은 임포트조차 안 되므로, 로직이 여기 있으면 영원히 시험
        # 밖에 남는다.
        return dashboard.read_dashboard(self._data_dir, self._get_config())


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
    요청만 하고, 스캐너가 다음 체크포인트에서 스스로 멈춘다 (DESIGN.md 1부 2-4).
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
            _emit(self, "finished", summary)
        except Exception as exc:  # pragma: no cover - 방어적 처리
            _emit(self, "failed", str(exc))
        finally:
            with self._lock:
                self._running = False

    def request_stop(self) -> bool:
        return nightly_scan.request_stop(self._data_dir)
