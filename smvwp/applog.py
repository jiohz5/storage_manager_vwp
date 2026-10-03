"""창(GUI)의 오류 기록을 파일로 남기고, 처리되지 않은 예외에 창이 꺼지지 않게 한다.

cron 으로 도는 수집과 야간 스캔은 출력이 이미 `logs/*_cron.log` 로 간다
(`setup_cron.csh`). 창은 그렇지 않았다 - 표준 오류가 run.csh 를 띄운 터미널로만
나가서, 터미널을 닫으면 무엇이 잘못됐는지 남는 것이 없었다. 폐쇄망 장비에서는
그 기록이 원인을 짚을 거의 유일한 단서다.

- **문제만** 남긴다 (WARNING 이상). 정상 동작은 남기지 않는다.
- **크기에 상한이 있다** (`MAX_BYTES` x `BACKUP_COUNT + 1`). 데이터 디렉터리가 이
  기록으로 불어나면 안 된다.
- **호스트마다 따로** 쓴다. 데이터 디렉터리는 NFS 에 있어 여러 장비가 함께 쓰는데,
  NFS 에서는 여러 장비가 한 파일 끝에 덧붙이는 것이 안전하지 않다.
- 터미널에도 지금처럼 찍는다.
"""

from __future__ import annotations

import logging
import logging.handlers
import socket
import sys
import threading
from pathlib import Path
from typing import Optional

MAX_BYTES = 1_000_000
BACKUP_COUNT = 2
FORMAT = "%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"

logger = logging.getLogger(__name__)


def gui_log_path(data_dir: Path) -> Path:
    host = socket.gethostname().split(".")[0] or "host"
    return data_dir / "logs" / f"gui-{host}.log"


def log_to_file(path: Path) -> Optional[logging.Handler]:
    """루트 로거에 파일 기록을 붙인다. 같은 파일이면 두 번 붙이지 않는다.

    기록할 곳을 못 만들면 None - 기록을 못 남기는 것이 창을 막으면 안 된다.
    파일은 처음 남길 것이 생길 때 만든다 (`delay`) - 문제가 없으면 빈 파일도 없다."""

    root = logging.getLogger()
    target = str(path.absolute())
    for handler in root.handlers:
        if getattr(handler, "baseFilename", None) == target:
            return handler
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("오류 기록 위치를 만들지 못했습니다: %s", path.parent, exc_info=True)
        return None
    handler = logging.handlers.RotatingFileHandler(
        target, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8", delay=True
    )
    handler.setLevel(logging.WARNING)
    handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(handler)
    if not any(type(h) is logging.StreamHandler for h in root.handlers):
        # 루트에 처리기가 하나라도 생기면 파이썬이 대신 찍어 주던 것이 멈춘다.
        # 터미널에서 띄운 사람은 지금처럼 보이게 둔다.
        stream = logging.StreamHandler()
        stream.setLevel(logging.WARNING)
        stream.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(stream)
    return handler


def keep_running_on_errors() -> None:
    """처리되지 않은 예외를 남기고, 창은 그대로 둔다.

    PyQt5 는 슬롯에서 처리되지 않은 예외가 나면 traceback 을 찍고 qFatal 로 앱을
    끝낸다 - 창 안에서 스캔이 돌고 있었다면 그것도 함께 멈춘다. 앱이 예외 처리
    함수를 걸어 두면 끝내지 않고 그 함수에 맡긴다. 여기서는 남기기만 한다."""

    uncaught = logging.getLogger("smvwp.uncaught")

    def hook(kind, value, traceback) -> None:
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, value, traceback)
            return
        uncaught.critical("처리되지 않은 예외", exc_info=(kind, value, traceback))

    def thread_hook(args) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        uncaught.critical(
            "작업 스레드의 처리되지 않은 예외 (%s)", name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = hook
    threading.excepthook = thread_hook
