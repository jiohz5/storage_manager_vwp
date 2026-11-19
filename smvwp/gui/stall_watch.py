"""화면 스레드가 멈춘 때와 자리를 남긴다 - "버튼 사이가 버벅인다" 를 실기에서 짚으려고.

## 왜

창이 굳는 까닭은 늘 같았다 - 화면 스레드에서 NFS 위 DB 나 파일을 읽었다. 그런데 어디서
읽었는지는 사람이 "멈춘다" 고 말해 준 뒤에 코드를 뒤져서야 알았다. 이것은 멈춘 그
순간의 화면 스레드 스택을 떠서 로그에 남긴다. `smvwp_cli.py diagnose` 가 모아 요약에 낸다.

## 어떻게

- 화면 스레드의 타이머가 `BEAT_MS` 마다 '살아 있다' 를 찍는다.
- 따로 도는 스레드가 그것을 지켜보다 `STALL_SECONDS` 넘게 안 찍히면 그때의 화면 스레드
  스택(`sys._current_frames`)에서 이 도구의 줄만 떠 둔다. 다시 찍히면 멈춘 길이와 함께
  WARNING 한 줄: ``화면 멈춤 2.3초 - scan_tab.py:512 _on_scan_status_ready < ...``
- 창이 뜨기까지, 홈·상세 스캔 첫 데이터까지 걸린 시간도 한 번씩 남긴다 (`mark`).

지켜보는 스레드는 0.25초마다 시각 하나를 볼 뿐이라 부담이 없다. 멈춘 동안 스택은 한 번만 뜬다.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

BEAT_MS = 200
STALL_SECONDS = 1.0
CHECK_SECONDS = 0.25
# 로그 줄에 남길 이 도구의 스택 줄 수 (안쪽부터).
FRAMES_KEPT = 5

# diagnose 가 로그에서 찾는 말머리.
STALL_PREFIX = "화면 멈춤 "
STARTUP_PREFIX = "창 시작 뒤 "

_PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_started_at: Optional[float] = None
_marked: set = set()


def started(at: Optional[float] = None) -> None:
    """창을 띄우기 시작한 때 (`smvwp_cli.py gui` 가 맨 먼저 부른다)."""

    global _started_at
    _started_at = time.monotonic() if at is None else at
    _marked.clear()


def mark(what: str) -> Optional[float]:
    """창 시작 뒤 `what` 까지 걸린 시간을 **한 번만** 남긴다. 시작을 모르면 아무것도 안 한다."""

    if _started_at is None or what in _marked:
        return None
    _marked.add(what)
    seconds = time.monotonic() - _started_at
    logger.info("%s%s까지 %.1f초", STARTUP_PREFIX, what, seconds)
    return seconds


def where(frame) -> str:
    """스택에서 이 도구의 줄만, 안쪽부터 `파일:줄 함수 < ...`."""

    parts: List[str] = []
    while frame is not None and len(parts) < FRAMES_KEPT:
        code = frame.f_code
        if os.path.abspath(code.co_filename).startswith(_PACKAGE_DIR):
            parts.append(f"{os.path.basename(code.co_filename)}:{frame.f_lineno} {code.co_name}")
        frame = frame.f_back
    return " < ".join(parts) or "(이 도구 밖 - Qt 안쪽)"


class StallWatch:
    """화면 스레드에서 만든다 (`start`). 창을 닫을 때 `stop`."""

    def __init__(self, stall_seconds: float = STALL_SECONDS, check_seconds: float = CHECK_SECONDS):
        self.stall_seconds = stall_seconds
        self.check_seconds = check_seconds
        self._beat = time.monotonic()
        self._gui_thread = threading.get_ident()
        self._stop = threading.Event()
        self._timer = None
        self._thread: Optional[threading.Thread] = None
        self.stalls: List[tuple] = []   # (초, 자리) - 시험과 진단용

    def beat(self) -> None:
        self._beat = time.monotonic()

    def start(self, parent=None) -> "StallWatch":
        from PyQt5.QtCore import QTimer

        self._timer = QTimer(parent)
        self._timer.timeout.connect(self.beat)
        self._timer.start(BEAT_MS)
        self.beat()
        self._thread = threading.Thread(target=self._watch, name="smvwp-stall-watch", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._timer is not None:
            self._timer.stop()

    def _watch(self) -> None:
        stuck_at: Optional[str] = None
        stuck_since = 0.0
        while not self._stop.wait(self.check_seconds):
            quiet = time.monotonic() - self._beat
            if quiet > self.stall_seconds:
                if stuck_at is None:
                    stuck_since = self._beat
                    frame = sys._current_frames().get(self._gui_thread)
                    stuck_at = where(frame)
            elif stuck_at is not None:
                self.report(self._beat - stuck_since, stuck_at)
                stuck_at = None

    def report(self, seconds: float, at: str) -> None:
        self.stalls.append((seconds, at))
        logger.warning("%s%.1f초 - %s", STALL_PREFIX, seconds, at)
