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

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from . import dashboard, nightly_scan, store
from .cycle import run_collection_cycle

logger = logging.getLogger(__name__)


def emit_safely(owner, signal_name: str, *payload) -> None:
    """작업 스레드에서 신호를 보낸다. 받을 쪽이 이미 사라졌으면 조용히 만다.

    **작업 스레드에서 나가는 신호는 전부 이 통로를 거쳐야 한다.** 창을 닫는
    순간 스레드가 아직 돌고 있으면 `signal.emit()` 이 그대로 터진다. 새 창을
    만들 때마다 이것을 잊어서 같은 예외가 세 번 되살아났고, 그래서
    `test_gui_wiring` 이 구조로 막는다.

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
        getattr(owner, signal_name).emit(*payload)
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


class ThreadWorker(QObject):
    """작업 스레드에서 일 하나를 돌리고 결과를 신호로 돌려주는 바탕.

    ## 왜 한 곳에 모았나

    "잠금 + 도는 중 표시 + daemon 스레드 + 신호" 가 아홉 군데에 복사돼 있었다.
    복사본마다 조금씩 달랐고 그 차이가 사고가 됐다:

    - 새 창을 만들 때마다 `emit_safely` 를 잊어, 창을 닫으면 터졌다.
    - 대시보드 워커는 도는 중에 들어온 요청을 **버렸다.** 창을 열면 대시보드
      읽기와 첫 수집이 함께 시작되는데, 수집이 먼저 끝나면 그 뒤의 새로고침이
      버려져 표가 다음 수집(15분)까지 옛 값으로 남았다.
    - 몇은 신호를 먼저 쏘고 표시를 나중에 풀어서, 결과를 받은 쪽이 곧바로
      다시 요청하면 "아직 도는 중" 으로 버려질 수 있었다.

    이제 스레드에서 신호가 나가는 곳은 **여기 한 군데뿐**이다. 하위 클래스는
    `_work(*args)` 만 쓰면 된다.

    ## 겹쳐 돌리지 않는다

    도는 중에 들어온 요청은 기본적으로 건너뛴다 - 타이머로 자주 부르는 쪽은
    쌓아 봐야 더 느려질 뿐이고, 다음 주기가 최신값을 다시 읽는다.

    요청이 드문 쪽(`COALESCE = True`)은 버리지 않고 **마지막 요청 하나를**
    기억해 두었다가, 지금 것이 끝나면 한 번 더 돈다. 요청이 몇 번 쌓여도 한
    번만 더 돈다.
    """

    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    THREAD_NAME = "smvwp-worker"
    COALESCE = False

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._running = False
        self._pending = None

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(self, *args) -> bool:
        """일을 시작한다. 이미 돌고 있으면 False (COALESCE 면 끝난 뒤 한 번 더)."""

        with self._lock:
            if self._running:
                if self.COALESCE:
                    self._pending = args
                return False
            self._running = True
        threading.Thread(
            target=self._loop, args=(args,), name=self.THREAD_NAME, daemon=True
        ).start()
        return True

    def _work(self, *args):  # pragma: no cover - 하위 클래스가 채운다
        raise NotImplementedError

    def _compute(self, args):
        try:
            return ("finished", self._work(*args))
        except Exception as exc:  # 스레드 밖으로 새면 조용히 죽는다
            # 화면에는 한 줄만 간다. 어디서 났는지는 로그에만 남으므로 여기서
            # 전체를 남긴다 - 예전에는 str(exc) 한 줄 말고는 아무것도 없었다.
            logger.exception("%s 작업 실패", self.THREAD_NAME)
            return ("failed", str(exc))

    def _loop(self, args) -> None:
        while True:
            outcome = self._compute(args)
            # 표시를 **먼저** 푼다. 결과를 받은 쪽이 곧바로 다시 요청하면
            # 받아 줘야 한다.
            with self._lock:
                args, self._pending = self._pending, None
                if args is None:
                    self._running = False
            emit_safely(self, *outcome)
            if args is None:
                return

    def _run(self, *args) -> None:
        """같은 스레드에서 한 번 돌린다.

        시험이 쓴다 - 스레드를 기다리면 장비가 바쁠 때 결과가 들쭉날쭉해진다."""

        emit_safely(self, *self._compute(args))


# 다른 수집기(cron, 다른 장비의 창)가 수집 주기의 이 배 안에 수집했으면 창의
# 정기 수집은 건너뛴다. 1 보다 조금 크게 잡아 cron 이 몇 초 늦는 것을 덮는다.
RECENT_COLLECTION_FACTOR = 1.1


class CollectorWorker(ThreadWorker):
    """한 번의 수집 사이클을 백그라운드 스레드에서 실행한다.

    결과는 `List[store.SampleRecord]`, 건너뛰었으면 None. 이전 수집이 아직 끝나지
    않았으면 겹쳐 돌리지 않는다.

    **정기 수집은 남이 방금 했으면 건너뛴다.** 창의 수집 타이머는 cron 이 없을
    때의 대비인데, cron 이 있어도 창을 열자마자 한 번, 그 뒤 15분마다 돌았다 -
    15분에 두 번 수집해 표본과 99% 즉시 알림이 두 배가 되고, 두 수집이 겹치면
    알림 상태를 서로 덮어썼다. cron 이 멈추면 다음 주기부터 창이 이어받는다.
    사람이 누른 `지금 수집` 은 언제나 수집한다."""

    THREAD_NAME = "smvwp-collect"

    def __init__(self, data_dir: Path, get_config, parent: QObject = None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._get_config = get_config
        # 이 창이 마지막으로 남긴 표본 시각. 그보다 새 표본이 있으면 남이 한 것이다.
        self._own_last: Optional[datetime] = None

    def run_once_async(self, manual: bool = True) -> bool:
        return self.start(manual)

    def _work(self, manual: bool):
        config = self._get_config()
        if not manual and self._collected_elsewhere_recently(config):
            return None
        records = run_collection_cycle(self._data_dir, config)
        stamps = [_parse_utc(record.collected_at) for record in records]
        stamps = [stamp for stamp in stamps if stamp is not None]
        if stamps:
            self._own_last = max(stamps)
        return records

    def _collected_elsewhere_recently(self, config) -> bool:
        with store.session(self._data_dir) as conn:
            newest = _parse_utc(store.newest_collected_at(conn))
        if newest is None:
            return False
        if self._own_last is not None and newest <= self._own_last:
            return False   # 가장 최근 표본이 이 창의 것이다 (cron 없이 창만 쓰는 경우)
        window = config.settings.collector_interval_seconds * RECENT_COLLECTION_FACTOR
        return (datetime.now(timezone.utc) - newest).total_seconds() < window


def _parse_utc(stamp) -> Optional[datetime]:
    try:
        value = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class ScanStatusWorker(ThreadWorker):
    """스캔 상태 스냅샷을 **백그라운드 스레드에서** 읽는다.

    ## 왜 스레드로 빼는가

    `get_status_snapshot`은 계정마다 십여 개의 쿼리를 던진다. 로컬 디스크에서는
    무시할 만하지만, 데이터 디렉터리가 NFS 위에 있으면 캐시에 없는 페이지마다
    네트워크 읽기가 된다. 화면은 이것을 **5초마다** 부르므로, GUI 스레드에서
    돌리면 그 시간 동안 창이 통째로 멈춘다 - 실기에서 실제로 그랬다.

    ## 겹쳐 돌리지 않는다

    NFS가 느려 한 번이 5초를 넘기면 요청이 계속 쌓여 상황이 더 나빠진다.
    이전 조회가 끝나지 않았으면 이번 차례는 그냥 건너뛴다 - 타이머가 곧 다시
    부른다.
    """

    THREAD_NAME = "smvwp-scan-status"

    def __init__(self, data_dir: Path, get_config, parent: QObject = None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._get_config = get_config

    def refresh_async(self) -> bool:
        """조회를 시작한다. 이미 돌고 있으면 False (건너뜀)."""

        return self.start()

    def _work(self):
        return nightly_scan.get_status_snapshot(self._data_dir, self._get_config())


class DashboardWorker(ThreadWorker):
    """대시보드 데이터를 **백그라운드 스레드에서** 읽는다.

    ## 왜 스레드로 빼는가

    `ScanStatusWorker` 와 같은 이유인데, 이쪽이 더 무겁다. 표본을 읽는 것은
    쿼리 하나지만 그 뒤의 FULL 예측은 **계정마다 이력을 다시 훑어** 추세를
    맞춘다. 데이터 디렉터리가 NFS 위면 그 시간 동안 창이 통째로 멈춘다.

    ## 도는 중에 온 요청을 버리지 않는다

    스캔 상태와 달리 이쪽은 **타이머가 다시 불러 주지 않는다.** 수집이 끝날
    때(15분)와 사람이 새로고침을 누를 때만 불린다. 그래서 버리면 그 값이 다음
    수집까지 안 나온다 - 창을 열 때 실제로 그랬다 (대시보드 읽기가 도는 동안
    첫 수집이 끝나 그 새로고침이 버려졌다).
    """

    THREAD_NAME = "smvwp-dashboard"
    COALESCE = True

    def __init__(self, data_dir: Path, get_config, parent: QObject = None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._get_config = get_config

    def refresh_async(self) -> bool:
        """읽기를 시작한다. 이미 돌고 있으면 끝난 뒤 한 번 더 읽는다."""

        return self.start()

    def _work(self):
        # 읽는 방법 자체는 `dashboard` 에 있다 - Qt 없이 시험할 수 있어야 한다.
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
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._worker.run_once_async(manual=False)

    def start(self, run_immediately: bool = True) -> None:
        interval_seconds = self._get_config().settings.collector_interval_seconds
        self._timer.start(interval_seconds * 1000)
        if run_immediately:
            # 창을 열 때의 수집도 정기 수집으로 친다 - cron 이 방금 했으면 그 값을 보여 준다.
            self._worker.run_once_async(manual=False)

    def stop(self) -> None:
        self._timer.stop()

    def trigger_now(self) -> None:
        self._worker.run_once_async(manual=True)

    def restart_with_current_interval(self) -> None:
        if self._timer.isActive():
            interval_seconds = self._get_config().settings.collector_interval_seconds
            self._timer.start(interval_seconds * 1000)


class NightlyScanWorker(ThreadWorker):
    """야간 상세 스캔을 백그라운드 스레드에서 실행한다.

    상세 스캔은 15분 수집과 달리 몇 시간까지도 이어질 수 있으므로 GUI 스레드
    에서 절대 돌리면 안 된다. 중복 실행 방지는 이 워커의 도는 중 표시와
    `smvwp.scan_lock`의 파일 잠금 두 겹으로 막는다 - 앞의 것은 이 창 안에서,
    뒤의 것은 cron 등 다른 프로세스까지 포함해서.

    중지는 강제 종료가 아니라 `nightly_scan.request_stop`(run ID 매칭 파일)로
    요청만 하고, 스캐너가 다음 체크포인트에서 스스로 멈춘다 (DESIGN.md 1부 2-4).
    """

    THREAD_NAME = "smvwp-nightly-scan"

    def __init__(self, data_dir: Path, get_config, parent: QObject = None):
        super().__init__(parent)
        self._data_dir = data_dir
        self._get_config = get_config

    def run_async(self, bypass_window: bool = False) -> bool:
        """스캔을 시작한다. 이미 이 창에서 돌고 있으면 False."""

        return self.start(bypass_window)

    def _work(self, bypass_window: bool):
        return nightly_scan.run_nightly_scan(
            self._data_dir,
            self._get_config(),
            triggered_by="gui",
            bypass_window=bypass_window,
        )

    def request_stop(self) -> bool:
        return nightly_scan.request_stop(self._data_dir)
