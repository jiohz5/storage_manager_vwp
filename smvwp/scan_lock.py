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
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class LockError(Exception):
    pass


class LockBusyError(LockError):
    """다른(살아있는) 프로세스가 이미 야간 스캔 잠금을 쥐고 있음."""


def lock_file(data_dir: Path) -> Path:
    return data_dir / "nightly_scan.lock"


def stop_request_file(data_dir: Path) -> Path:
    return data_dir / "nightly_scan_stop.json"


@dataclass
class LockInfo:
    run_id: str
    pid: int
    triggered_by: str
    started_at: str


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temp_path), str(path))


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
        return LockInfo(**raw)
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def is_locked(data_dir: Path) -> bool:
    """살아있는 프로세스가 쥔 유효한 잠금이 있는지."""

    info = read_lock(data_dir)
    if info is None:
        return False
    return _pid_alive(info.pid)


def acquire_lock(data_dir: Path, triggered_by: str) -> str:
    """야간 스캔 잠금을 얻는다. 성공하면 새 run_id를 반환한다.

    이미 살아있는 프로세스가 잠금을 쥐고 있으면 LockBusyError. 죽은
    프로세스가 남긴 잠금이면 조용히 회수한다 (자기 자신을 대체).
    """

    existing = read_lock(data_dir)
    if existing is not None and _pid_alive(existing.pid):
        raise LockBusyError(
            f"이미 실행 중인 야간 스캔이 있습니다 (run_id={existing.run_id}, pid={existing.pid})"
        )

    info = LockInfo(
        run_id=uuid.uuid4().hex,
        pid=os.getpid(),
        triggered_by=triggered_by,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    _atomic_write_json(lock_file(data_dir), asdict(info))
    return info.run_id


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
    _atomic_write_json(
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
