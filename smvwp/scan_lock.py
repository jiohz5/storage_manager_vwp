"""야간 상세 스캔의 실행 잠금 + 안전 중지(run ID 매칭) 요청.

DESIGN.md 1부 2-4 "안전한 중지, 강제 kill 없음" 원칙: 임의 PID에 signal을 보내지
않는다. 대신:

- 잠금 파일에 이번 실행의 run_id + pid를 적어 두고, 여러 야간 스캔이
  동시에(직렬 원칙 위반) 돌지 못하게 막는다. 잠금을 쥔 프로세스가 이미 죽어
  있으면(예: 강제 종료된 이전 실행) "죽은 잠금"으로 보고 회수한다 - 이때도
  그 PID에 아무 신호도 보내지 않고, 단순히 살아있는지만 확인한다
  (`os.kill(pid, 0)`은 신호 0번이라 실제로 아무 것도 죽이지 않는다).
- 중지 요청은 "이 run_id를 멈춰라"는 파일만 남긴다. 스캐너는 매 작업 단위
  전에 이 파일이 자신의 run_id와 일치하는지 확인하고, 일치하면 체크포인트를
  남긴 채로 스스로 멈춘다. 잘못된 PID를 죽일 위험이 원천적으로 없다.

## 다른 장비

데이터 디렉터리는 NFS 에 있어 여러 장비가 함께 본다 (창은 로그인할 때마다 다른
장비에 뜰 수 있고, cron 은 setup_cron 을 돌린 장비에 있다). pid 는 그 장비
안에서만 뜻이 있으므로 잠금에 **장비 이름**을 함께 적는다.

- 같은 장비의 잠금은 지금처럼 pid 로 살아 있는지 본다.
- 다른 장비의 잠금은 pid 로 알 수 없다. 쥔 쪽이 `HEARTBEAT_SECONDS` 마다 잠금
  파일의 시각을 갱신하므로, 그것이 `FOREIGN_STALE_SECONDS` 넘게 멈춰 있으면 죽은
  것으로 본다. 예전에는 다른 장비의 pid 를 이 장비에서 찾아(당연히 없으니) 죽은
  잠금으로 보고 가져갔다 - 두 장비에서 스캔이 겹쳐 돌았다.
- 쥔 쪽은 갱신할 때마다 잠금이 아직 자기 것인지 본다(`touch`). 잃었으면(지워졌거나
  남이 가져갔으면) 스스로 멈춘다 - 어떤 경로로 잠금이 둘로 갈라져도 겹쳐 도는
  시간이 갱신 주기를 넘지 않는다.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import atomic


class LockError(Exception):
    pass


class LockBusyError(LockError):
    """다른(살아있는) 프로세스가 이미 야간 스캔 잠금을 쥐고 있음."""


def lock_file(data_dir: Path) -> Path:
    return data_dir / "nightly_scan.lock"


def stop_request_file(data_dir: Path) -> Path:
    return data_dir / "nightly_scan_stop.json"


# 쥔 쪽이 잠금 파일 시각을 갱신하는 주기, 그리고 다른 장비의 잠금을 죽은 것으로
# 볼 때까지 기다리는 시간. 갱신은 따로 도는 스레드가 하므로 체크포인트가 길어도
# 밀리지 않는다 - 10분은 장비 사이 시계 차이까지 넉넉히 덮는다.
HEARTBEAT_SECONDS = 60
FOREIGN_STALE_SECONDS = 10 * 60


def this_host() -> str:
    return socket.gethostname()


@dataclass
class LockInfo:
    run_id: str
    pid: int
    triggered_by: str
    started_at: str
    # 예전 잠금 파일에는 없다 - 없으면 이 장비의 것으로 본다.
    host: str = ""

    @property
    def on_this_host(self) -> bool:
        return not self.host or self.host == this_host()


def _pid_alive(pid: int) -> bool:
    """pid가 살아 있는지만 확인한다 (신호 0번 - 실제로 아무 것도 죽이지 않음).

    테스트에서는 이 함수를 그대로 몽키패치해서 플랫폼에 상관없이 동작을
    검증한다 (Windows 개발 PC에서는 POSIX와 signal 0 처리 방식이 달라서).
    """

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 존재는 하지만 신호 보낼 권한이 없음 -> 살아있는 것으로 취급
    except OSError:
        return False
    return True


def read_lock(data_dir: Path) -> Optional[LockInfo]:
    path = lock_file(data_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        # 아는 항목만 읽는다. 모르는 항목 하나 때문에 잠금을 '없음' 으로 읽으면
        # 살아 있는 잠금을 가져가게 된다.
        return LockInfo(**{key: raw[key] for key in LockInfo.__dataclass_fields__ if key in raw})
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def is_mine(info: LockInfo) -> bool:
    """이 프로세스가 쥔 잠금인가."""

    return info.on_this_host and info.pid == os.getpid()


def _holder_alive(data_dir: Path, info: LockInfo) -> bool:
    if info.on_this_host:
        return _pid_alive(info.pid)
    try:
        age = time.time() - lock_file(data_dir).stat().st_mtime
    except OSError:
        return False
    return age < FOREIGN_STALE_SECONDS


def is_locked(data_dir: Path) -> bool:
    """살아있는 프로세스가 쥔 유효한 잠금이 있는지."""

    info = read_lock(data_dir)
    if info is None:
        return False
    return _holder_alive(data_dir, info)


def touch(data_dir: Path, run_id: str) -> bool:
    """이 run_id 가 쥔 잠금이면 시각을 갱신하고 True. 잃었으면 False.

    잃었다는 것은 잠금 파일이 없거나 다른 run_id 가 적혀 있다는 뜻이다. 읽다가
    잠깐 실패한 것(NFS)은 잃은 것으로 치지 않는다 - 그것으로 스캔을 멈추면 안 된다."""

    path = lock_file(data_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    if raw.get("run_id") != run_id:
        return False
    try:
        os.utime(str(path), None)
    except OSError:
        pass   # 갱신을 못 해도 잠금은 여전히 우리 것이다
    return True


def acquire_lock(data_dir: Path, triggered_by: str) -> str:
    """야간 스캔 잠금을 얻는다. 성공하면 새 run_id를 반환한다.

    이미 살아있는 프로세스가 잠금을 쥐고 있으면 LockBusyError. 죽은
    프로세스가 남긴 잠금이면 조용히 회수한다 (자기 자신을 대체).
    """

    path = lock_file(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # **`O_EXCL` 로 만든다.** 예전에는 "읽어 보고 없으면 쓴다" 였는데, 그 사이가
    # 비어 있다 - cron 이 22:00:00 에 뜨는 순간 사람이 GUI 에서 버튼을 누르면
    # 둘 다 "없음"을 읽고 둘 다 써서, 둘 다 자기가 잠금을 쥔 줄 알고 돈다.
    #
    # NFS 위에서도 안전하다. NFSv3 의 EXCLUSIVE create 는 verifier 로 원자성을
    # 보장한다 - 이 프로그램의 데이터 디렉터리가 NFS 라 그 점이 중요하다.
    for attempt in (0, 1):
        info = LockInfo(
            run_id=uuid.uuid4().hex,
            pid=os.getpid(),
            triggered_by=triggered_by,
            started_at=datetime.now(timezone.utc).isoformat(),
            host=this_host(),
        )
        try:
            handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            existing = read_lock(data_dir)
            if existing is not None and _holder_alive(data_dir, existing):
                raise LockBusyError(
                    f"이미 실행 중인 야간 스캔이 있습니다 "
                    f"(run_id={existing.run_id}, pid={existing.pid}, "
                    f"장비={existing.host or this_host()})"
                )
            # 죽은 프로세스가 남긴 잠금이다. 지우고 딱 한 번 더 해 본다 - 그
            # 사이에 다른 실행이 먼저 쥐었다면 위에서 LockBusyError 가 난다.
            if attempt == 0:
                try:
                    os.unlink(str(path))
                except OSError:  # pragma: no cover - 남이 먼저 지웠을 뿐
                    pass
                continue
            raise LockBusyError("잠금 파일을 회수하지 못했습니다")

        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(asdict(info), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return info.run_id

    raise LockBusyError("잠금을 얻지 못했습니다")  # pragma: no cover - 위에서 끝난다


def release_lock(data_dir: Path, run_id: str) -> None:
    """이 run_id가 쥔 잠금일 때만 지운다 (그 사이 다른 실행이 잠금을 새로
    얻었다면 건드리지 않는다 - 남의 잠금을 실수로 지우지 않기 위한 안전망)."""

    info = read_lock(data_dir)
    if info is not None and info.run_id == run_id:
        try:
            lock_file(data_dir).unlink()
        except FileNotFoundError:
            pass


def request_stop(data_dir: Path, run_id: str) -> None:
    atomic.write_json(
        stop_request_file(data_dir),
        {"run_id": run_id, "requested_at": datetime.now(timezone.utc).isoformat()},
    )


def is_stop_requested(data_dir: Path, run_id: str) -> bool:
    path = stop_request_file(data_dir)
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return raw.get("run_id") == run_id


def clear_stop_request(data_dir: Path) -> None:
    try:
        stop_request_file(data_dir).unlink()
    except FileNotFoundError:
        pass
