from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QMenu,
    QMessageBox,
    QStyle,
    QSystemTrayIcon,
)

from storage_manager.config import load_store
from storage_manager.i18n import tr
from storage_manager.notifications import LEVEL_RANK
from storage_manager.popup_queue import (
    acknowledge_notifications,
    popup_summary,
    unread_notifications,
)
from storage_manager.scheduler import ProcessLock
from storage_manager.tracking import process_is_alive


AUTOSTART_FILENAME = "storage-manager-notifier.desktop"
NOTIFIER_STATUS_FILENAME = "notifier_status.json"
NOTIFIER_STOP_FILENAME = "notifier_stop.json"
NOTIFIER_LOCK_FILENAME = "notifier.lock"
ACTIVE_NOTIFIER_STATES = {"starting", "running", "waiting_for_tray", "stop_requested"}


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def _read_json(path: Path) -> Dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def notifier_status_file(data_dir: Path) -> Path:
    return data_dir / NOTIFIER_STATUS_FILENAME


def notifier_stop_file(data_dir: Path) -> Path:
    return data_dir / NOTIFIER_STOP_FILENAME


def _default_notifier_status() -> Dict[str, object]:
    return {
        "state": "never",
        "run_id": "",
        "pid": 0,
        "started_at": "",
        "updated_at": "",
        "finished_at": "",
        "unread": 0,
        "message": "",
    }


def write_notifier_status(data_dir: Path, payload: Dict[str, object]) -> None:
    status = _default_notifier_status()
    status.update(payload)
    status["updated_at"] = _now_text()
    _atomic_json(notifier_status_file(data_dir), status)


def process_is_notifier(pid: int, data_dir: Path) -> bool:
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
    except (OSError, PermissionError):
        return True
    has_script = any(Path(argument).name == "storage_notifier.py" for argument in arguments)
    try:
        data_index = arguments.index("--data-dir")
        same_data = Path(arguments[data_index + 1]).resolve() == data_dir.resolve()
    except (ValueError, IndexError, OSError):
        same_data = False
    return has_script and same_data


def read_notifier_status(data_dir: Path) -> Dict[str, object]:
    status = _default_notifier_status()
    status.update(_read_json(notifier_status_file(data_dir)))
    state = str(status.get("state", "never"))
    pid = int(status.get("pid", 0) or 0)
    if state in ACTIVE_NOTIFIER_STATES and not process_is_notifier(pid, data_dir):
        status["state"] = "interrupted"
        status["message"] = status.get("message") or "notifier process ended"
    return status


def request_notifier_stop(data_dir: Path) -> bool:
    status = read_notifier_status(data_dir)
    if str(status.get("state")) not in ACTIVE_NOTIFIER_STATES:
        return False
    run_id = str(status.get("run_id") or "")
    if not run_id:
        return False
    _atomic_json(
        notifier_stop_file(data_dir),
        {
            "run_id": run_id,
            "requested_at": _now_text(),
            "pid": int(status.get("pid", 0) or 0),
        },
    )
    status["state"] = "stop_requested"
    write_notifier_status(data_dir, status)
    return True


def notifier_stop_requested(data_dir: Path, run_id: str) -> bool:
    payload = _read_json(notifier_stop_file(data_dir))
    return bool(run_id and payload.get("run_id") == run_id)


def clear_notifier_stop(data_dir: Path, run_id: Optional[str] = None) -> None:
    path = notifier_stop_file(data_dir)
    if run_id:
        payload = _read_json(path)
        if payload.get("run_id") not in {None, run_id}:
            return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _validate_desktop_value(value: str) -> None:
    if any(character in value for character in ("\n", "\r", "%")):
        raise ValueError("Autostart paths cannot contain newlines or percent signs")


def _desktop_quote(value: Path) -> str:
    text = str(value)
    _validate_desktop_value(text)
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
    )
    return f'"{escaped}"'


def build_autostart_entry(
    python_bin: Path,
    script: Path,
    data_dir: Path,
) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Storage Manager Notifier\n"
        f"Exec={_desktop_quote(python_bin)} {_desktop_quote(script)} "
        f"--data-dir {_desktop_quote(data_dir)}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-MATE-Autostart-enabled=true\n"
    )


def _autostart_path(autostart_dir: Optional[Path] = None) -> Path:
    directory = autostart_dir or (Path.home() / ".config" / "autostart")
    return Path(directory) / AUTOSTART_FILENAME


def install_autostart(
    data_dir: Path,
    python_bin: Optional[Path] = None,
    app_dir: Optional[Path] = None,
    autostart_dir: Optional[Path] = None,
) -> Path:
    application_dir = app_dir or Path(__file__).resolve().parent.parent
    script = application_dir / "storage_notifier.py"
    if not script.is_file():
        raise FileNotFoundError(f"Notifier entry point not found: {script}")
    path = _autostart_path(autostart_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = build_autostart_entry(
        Path(python_bin or sys.executable),
        script,
        Path(data_dir).expanduser().resolve(),
    )
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def remove_autostart(autostart_dir: Optional[Path] = None) -> bool:
    path = _autostart_path(autostart_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def autostart_installed(autostart_dir: Optional[Path] = None) -> bool:
    return _autostart_path(autostart_dir).is_file()


def launch_notifier(
    data_dir: Path,
    python_bin: Optional[Path] = None,
    app_dir: Optional[Path] = None,
) -> int:
    data_dir = Path(data_dir).expanduser().resolve()
    status = read_notifier_status(data_dir)
    if str(status.get("state")) in ACTIVE_NOTIFIER_STATES:
        raise RuntimeError(f"Notifier is already running (PID {status.get('pid', 0)})")
    application_dir = app_dir or Path(__file__).resolve().parent.parent
    script = application_dir / "storage_notifier.py"
    if not script.is_file():
        raise FileNotFoundError(f"Notifier entry point not found: {script}")
    run_id = uuid.uuid4().hex
    command = [
        str(python_bin or sys.executable),
        str(script),
        "--data-dir",
        str(data_dir),
        "--run-id",
        run_id,
    ]
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "notifier.log"
    options = {
        "cwd": str(application_dir),
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
    write_notifier_status(
        data_dir,
        {
            "state": "starting",
            "run_id": run_id,
            "pid": int(process.pid),
            "started_at": _now_text(),
        },
    )
    return int(process.pid)


class StorageTrayNotifier(QObject):
    def __init__(self, app: QApplication, data_dir: Path, run_id: str):
        super().__init__()
        self.app = app
        self.data_dir = data_dir
        self.run_id = run_id
        self.app_dir = Path(__file__).resolve().parent.parent
        self.shown_files = set()
        self.paused = False
        self.language = load_store(data_dir).settings.language

        icon = QIcon.fromTheme("drive-harddisk")
        if icon.isNull():
            icon = app.style().standardIcon(QStyle.SP_DriveHDIcon)
        self.tray = QSystemTrayIcon(icon, self)
        self.menu = QMenu()
        self.open_action = QAction(tr(self.language, "notifier.menu.open"), self.menu)
        self.alerts_action = QAction(tr(self.language, "notifier.menu.alerts"), self.menu)
        self.pause_action = QAction(tr(self.language, "notifier.menu.pause"), self.menu)
        self.pause_action.setCheckable(True)
        self.quit_action = QAction(tr(self.language, "notifier.menu.quit"), self.menu)
        self.open_action.triggered.connect(self.open_manager)
        self.alerts_action.triggered.connect(self.show_alert_center)
        self.pause_action.toggled.connect(self.set_paused)
        self.quit_action.triggered.connect(self.app.quit)
        for action in (
            self.open_action,
            self.alerts_action,
            self.pause_action,
            self.quit_action,
        ):
            self.menu.addAction(action)
        self.tray.setContextMenu(self.menu)
        self.tray.messageClicked.connect(self.show_alert_center)

        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        QTimer.singleShot(0, self.poll)

    def set_paused(self, paused: bool) -> None:
        self.paused = bool(paused)

    def _tray_available(self) -> bool:
        available = QSystemTrayIcon.isSystemTrayAvailable()
        if available and not self.tray.isVisible():
            self.tray.show()
        return available

    def _show_popup(self, envelopes) -> None:
        title, message = popup_summary(envelopes, self.language)
        levels = [
            event.level
            for envelope in envelopes
            for event in envelope.events
        ]
        highest = max(levels, key=lambda level: LEVEL_RANK.get(level, 0))
        icon = (
            QSystemTrayIcon.Critical
            if highest in {"emergency", "full"}
            else QSystemTrayIcon.Warning
            if highest == "alert"
            else QSystemTrayIcon.Information
        )
        self.tray.showMessage(title, message, icon, 15000)

    def poll(self) -> None:
        if notifier_stop_requested(self.data_dir, self.run_id):
            self.app.quit()
            return
        try:
            store = load_store(self.data_dir)
            self.language = store.settings.language
            unread = unread_notifications(
                self.data_dir,
                store.settings.popup_backlog_days,
            )
        except Exception as exc:
            write_notifier_status(
                self.data_dir,
                {
                    "state": "running",
                    "run_id": self.run_id,
                    "pid": os.getpid(),
                    "started_at": read_notifier_status(self.data_dir).get("started_at", ""),
                    "message": str(exc),
                },
            )
            return
        available = self._tray_available()
        new_items = [item for item in unread if item.path.name not in self.shown_files]
        if available and not self.paused and new_items:
            self._show_popup(new_items)
            self.shown_files.update(item.path.name for item in new_items)
        self.tray.setToolTip(tr(self.language, "notifier.tooltip", count=len(unread)))
        write_notifier_status(
            self.data_dir,
            {
                "state": "running" if available else "waiting_for_tray",
                "run_id": self.run_id,
                "pid": os.getpid(),
                "started_at": read_notifier_status(self.data_dir).get("started_at", ""),
                "unread": len(unread),
                "message": "" if available else "system tray unavailable",
            },
        )

    def show_alert_center(self) -> None:
        store = load_store(self.data_dir)
        unread = unread_notifications(
            self.data_dir,
            store.settings.popup_backlog_days,
        )
        if unread:
            details = []
            for envelope in unread:
                for event in envelope.events:
                    details.append(
                        f"[{event.level.upper()}] {event.title}\n{event.message}"
                    )
            message = "\n\n".join(details)
        else:
            _, message = popup_summary([], self.language)
        QMessageBox.information(
            None,
            tr(self.language, "notifier.alert_center.title"),
            message,
        )
        if unread:
            acknowledge_notifications(
                self.data_dir,
                [item.path for item in unread],
            )
            self.shown_files.difference_update(item.path.name for item in unread)
        self.poll()

    def open_manager(self) -> None:
        subprocess.Popen(
            [
                sys.executable,
                str(self.app_dir / "app.py"),
                "--data-dir",
                str(self.data_dir),
            ],
            cwd=str(self.app_dir),
            close_fds=True,
        )


def run_notifier(data_dir: Path, run_id: Optional[str] = None) -> int:
    data_dir = Path(data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    active_run_id = run_id or uuid.uuid4().hex
    started_at = _now_text()
    with ProcessLock(data_dir / NOTIFIER_LOCK_FILENAME):
        clear_notifier_stop(data_dir)
        write_notifier_status(
            data_dir,
            {
                "state": "running",
                "run_id": active_run_id,
                "pid": os.getpid(),
                "started_at": started_at,
            },
        )
        app = QApplication.instance() or QApplication([sys.argv[0]])
        app.setQuitOnLastWindowClosed(False)
        controller = StorageTrayNotifier(app, data_dir, active_run_id)

        def finish() -> None:
            controller.timer.stop()
            controller.tray.hide()
            clear_notifier_stop(data_dir, active_run_id)
            write_notifier_status(
                data_dir,
                {
                    "state": "stopped",
                    "run_id": active_run_id,
                    "pid": 0,
                    "started_at": started_at,
                    "finished_at": _now_text(),
                    "unread": read_notifier_status(data_dir).get("unread", 0),
                },
            )

        app.aboutToQuit.connect(finish)
        return int(app.exec_())
