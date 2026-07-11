from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional


ACTIVE_STATES = {"starting", "running", "stop_requested"}
STATUS_FILENAME = "nightly_scan_status.json"
STOP_FILENAME = "nightly_scan_stop.json"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def status_file(data_dir: Path) -> Path:
    return data_dir / STATUS_FILENAME


def stop_file(data_dir: Path) -> Path:
    return data_dir / STOP_FILENAME


def _default_status() -> Dict[str, object]:
    return {
        "state": "never",
        "run_id": "",
        "pid": 0,
        "trigger": "",
        "started_at": "",
        "updated_at": "",
        "finished_at": "",
        "current_account": "",
        "phase": "idle",
        "accounts_total": 0,
        "accounts_processed": 0,
        "message": "",
    }


def _read_json(path: Path) -> Dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(20):
            try:
                os.replace(str(temp), str(path))
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not process:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259
            finally:
                ctypes.windll.kernel32.CloseHandle(process)
        except (AttributeError, OSError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def process_is_scan(pid: int, data_dir: Path) -> bool:
    if not process_is_alive(pid):
        return False
    if os.name == "nt":
        return True
    command_path = Path(f"/proc/{pid}/cmdline")
    if not command_path.exists():
        return True
    try:
        arguments = [
            os.fsdecode(part)
            for part in command_path.read_bytes().split(b"\0")
            if part
        ]
    except PermissionError:
        return True
    except OSError:
        return False
    has_script = any(Path(argument).name == "nightly_scan.py" for argument in arguments)
    has_data_dir = str(data_dir.resolve()) in arguments
    return has_script and has_data_dir


def read_scan_status(data_dir: Path) -> Dict[str, object]:
    status = _default_status()
    status.update(_read_json(status_file(data_dir)))
    state = str(status.get("state", "never"))
    if status.get("phase") == "idle":
        if state in {"succeeded", "stopped"}:
            status["phase"] = "complete"
        elif state in {"failed", "interrupted"}:
            status["phase"] = "failed"
    pid = int(status.get("pid", 0) or 0)
    if state in ACTIVE_STATES and not process_is_scan(pid, data_dir):
        status["state"] = "interrupted"
        if not status.get("message"):
            status["message"] = "process ended without a completion record"
    return status


def write_scan_status(data_dir: Path, payload: Dict[str, object]) -> None:
    status = _default_status()
    status.update(payload)
    status["updated_at"] = _now_text()
    _write_json(status_file(data_dir), status)


def update_scan_status(data_dir: Path, run_id: str, **updates: object) -> None:
    status = _default_status()
    status.update(_read_json(status_file(data_dir)))
    if status.get("run_id") != run_id:
        return
    status.update(updates)
    status["updated_at"] = _now_text()
    _write_json(status_file(data_dir), status)


def request_scan_stop(data_dir: Path) -> bool:
    status = read_scan_status(data_dir)
    if str(status.get("state")) not in ACTIVE_STATES:
        return False
    run_id = str(status.get("run_id", ""))
    if not run_id:
        return False
    _write_json(
        stop_file(data_dir),
        {
            "run_id": run_id,
            "pid": int(status.get("pid", 0) or 0),
            "requested_at": _now_text(),
        },
    )
    update_scan_status(data_dir, run_id, state="stop_requested")
    return True


def scan_stop_requested(data_dir: Path, run_id: str) -> bool:
    request = _read_json(stop_file(data_dir))
    return bool(run_id and request.get("run_id") == run_id)


def clear_scan_stop(data_dir: Path, run_id: Optional[str] = None) -> None:
    path = stop_file(data_dir)
    if run_id is not None:
        request = _read_json(path)
        if request and request.get("run_id") != run_id:
            return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def next_scheduled_run(start_hour: int, now: Optional[datetime] = None) -> datetime:
    current = now or datetime.now()
    candidate = current.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if candidate <= current:
        candidate += timedelta(days=1)
    return candidate


def launch_background_scan(data_dir: Path) -> int:
    status = read_scan_status(data_dir)
    if str(status.get("state")) in ACTIVE_STATES:
        raise RuntimeError(f"Nightly scan is already running (PID {status.get('pid', 0)})")

    app_dir = Path(__file__).resolve().parent.parent
    script = app_dir / "nightly_scan.py"
    command = [
        sys.executable,
        str(script),
        "--data-dir",
        str(data_dir),
        "--trigger",
        "gui",
    ]
    clear_scan_stop(data_dir)
    log_path = data_dir / "nightly_scan.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    options = {
        "cwd": str(app_dir),
        "env": env,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        options["start_new_session"] = True
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **options,
        )
    return int(process.pid)
