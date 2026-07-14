from __future__ import annotations

import getpass
import json
import os
import shutil
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional


MIN_PYTHON = (3, 10, 0)
MIN_SQLITE = (3, 24, 0)
LOCATION_FILENAME = "location.json"
DIAGNOSTIC_FILENAME = "runtime_diagnostics.json"


class RuntimePathError(RuntimeError):
    pass


@dataclass(frozen=True)
class DataDirectoryStatus:
    path: Path
    writable: bool
    sqlite_writable: bool
    size_bytes: int
    free_bytes: int


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
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


def config_location_file(
    home: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Path:
    values = os.environ if environ is None else environ
    configured = values.get("XDG_CONFIG_HOME", "").strip()
    base = Path(configured).expanduser() if configured else Path(home or Path.home()) / ".config"
    return base / "storage-manager-vwp" / LOCATION_FILENAME


def _absolute_path(value: Path) -> Path:
    return Path(value).expanduser().resolve()


def save_data_dir_location(
    data_dir: Path,
    home: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Path:
    path = config_location_file(home=home, environ=environ)
    try:
        _atomic_json(path, {"data_dir": str(_absolute_path(data_dir))})
    except OSError as exc:
        raise RuntimePathError(f"Cannot save the data location pointer {path}: {exc}") from exc
    return path


def read_saved_data_dir(
    home: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[Path]:
    path = config_location_file(home=home, environ=environ)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise RuntimePathError(
            f"Invalid JSON in data location pointer {path}, line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc
    except OSError as exc:
        raise RuntimePathError(f"Cannot read data location pointer {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data_dir"), str):
        raise RuntimePathError(f"Data location pointer has no string data_dir: {path}")
    value = Path(payload["data_dir"]).expanduser()
    if not value.is_absolute():
        raise RuntimePathError(f"Saved data directory must be absolute: {value}")
    return value.resolve()


def resolve_data_dir(
    explicit: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Optional[Path]:
    if explicit is not None:
        return _absolute_path(explicit)
    values = os.environ if environ is None else environ
    configured = values.get("STORAGE_MANAGER_DATA_DIR", "").strip()
    if configured:
        return _absolute_path(Path(configured))
    return read_saved_data_dir(home=home, environ=values)


def current_user_id() -> str:
    return getpass.getuser()


def suggested_data_dir(
    user_id: str,
    home: Path,
    user_root: Path = Path("/user"),
) -> Path:
    account_home = Path(user_root) / user_id
    base = account_home if account_home.is_dir() else Path(home)
    return base / ".storage-manager-vwp"


def directory_size_bytes(path: Path) -> int:
    root = Path(path)
    total = 0
    try:
        entries = list(os.scandir(str(root)))
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return 0
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                total += directory_size_bytes(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return total


def same_filesystem(first: Path, second: Path, stat_reader=os.stat) -> bool:
    return int(stat_reader(str(first)).st_dev) == int(stat_reader(str(second)).st_dev)


def _remove_probe(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def inspect_data_directory(
    path: Path,
    create: bool = True,
    measure_size: bool = True,
) -> DataDirectoryStatus:
    resolved = _absolute_path(path)
    try:
        if create:
            resolved.mkdir(parents=True, exist_ok=True)
        if not resolved.is_dir():
            raise NotADirectoryError(str(resolved))
    except OSError as exc:
        raise RuntimePathError(
            f"STORAGE_MANAGER_DATA_DIR must be a writable directory: {resolved}: {exc}"
        ) from exc

    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    file_probe = resolved / f".storage-manager-probe-{token}.tmp"
    sqlite_probe = resolved / f".storage-manager-probe-{token}.db"
    sqlite_connection = None
    try:
        with file_probe.open("wb") as handle:
            handle.write(b"storage-manager-write-probe\n")
            handle.flush()
            os.fsync(handle.fileno())
        sqlite_connection = sqlite3.connect(str(sqlite_probe), timeout=5.0)
        sqlite_connection.execute("CREATE TABLE write_probe(value INTEGER NOT NULL)")
        sqlite_connection.execute("INSERT INTO write_probe(value) VALUES(1)")
        sqlite_connection.commit()
    except (OSError, sqlite3.Error) as exc:
        raise RuntimePathError(
            f"STORAGE_MANAGER_DATA_DIR is not writable for files and SQLite: "
            f"{resolved}: {exc}. Create a private directory first or select another path."
        ) from exc
    finally:
        if sqlite_connection is not None:
            sqlite_connection.close()
        _remove_probe(file_probe)
        _remove_probe(sqlite_probe)
        _remove_probe(sqlite_probe.with_name(sqlite_probe.name + "-journal"))

    try:
        free_bytes = int(shutil.disk_usage(str(resolved)).free)
    except OSError:
        free_bytes = 0
    return DataDirectoryStatus(
        path=resolved,
        writable=True,
        sqlite_writable=True,
        size_bytes=directory_size_bytes(resolved) if measure_size else 0,
        free_bytes=free_bytes,
    )


def _identity_payload() -> Dict[str, object]:
    payload: Dict[str, object] = {"user": getpass.getuser(), "groups": []}
    if os.name == "nt":
        return payload
    try:
        import grp
        import pwd

        payload["uid"] = os.getuid()
        payload["gid"] = os.getgid()
        payload["user"] = pwd.getpwuid(os.getuid()).pw_name
        payload["groups"] = [
            grp.getgrgid(group_id).gr_name for group_id in os.getgroups()
        ]
    except (ImportError, KeyError, OSError):
        payload["groups"] = [str(group_id) for group_id in getattr(os, "getgroups", lambda: [])()]
    return payload


def collect_runtime_diagnostics(
    data_dir: Optional[Path] = None,
    measure_data_size: bool = True,
) -> Dict[str, object]:
    python_supported = sys.version_info[:3] >= MIN_PYTHON
    sqlite_supported = sqlite3.sqlite_version_info >= MIN_SQLITE
    try:
        from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
    except Exception as exc:
        pyqt = {"available": False, "error": str(exc)}
    else:
        pyqt = {
            "available": True,
            "pyqt_version": PYQT_VERSION_STR,
            "qt_version": QT_VERSION_STR,
        }

    payload: Dict[str, object] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": {
            "available": True,
            "executable": sys.executable,
            "version": sys.version.splitlines()[0],
            "version_info": list(sys.version_info[:3]),
            "prefix": sys.prefix,
            "base_prefix": getattr(sys, "base_prefix", sys.prefix),
            "supported": python_supported,
        },
        "json": {
            "available": True,
            "path": str(getattr(json, "__file__", "built-in")),
        },
        "sqlite": {
            "available": True,
            "version": sqlite3.sqlite_version,
            "supported": sqlite_supported,
        },
        "pyqt5": pyqt,
        "identity": _identity_payload(),
        "pythonhome_ignored": bool(os.environ.get("PYTHONHOME")),
        "data": None,
    }
    if data_dir is not None:
        try:
            status = inspect_data_directory(
                data_dir,
                measure_size=measure_data_size,
            )
        except RuntimePathError as exc:
            payload["data"] = {
                "path": str(_absolute_path(data_dir)),
                "writable": False,
                "error": str(exc),
            }
        else:
            data_payload = asdict(status)
            data_payload["path"] = str(status.path)
            payload["data"] = data_payload
    payload["ok"] = bool(
        python_supported
        and sqlite_supported
        and pyqt.get("available")
        and (
            data_dir is None
            or bool(isinstance(payload["data"], dict) and payload["data"].get("writable"))
        )
    )
    return payload


def write_runtime_diagnostics(data_dir: Path, payload: Dict[str, object]) -> Path:
    path = _absolute_path(data_dir) / DIAGNOSTIC_FILENAME
    try:
        _atomic_json(path, payload)
    except OSError as exc:
        raise RuntimePathError(f"Cannot write runtime diagnostics {path}: {exc}") from exc
    return path
