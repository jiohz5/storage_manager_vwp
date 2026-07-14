from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib import request

from storage_manager.config import Settings


LEVEL_RANK = {
    "recovery": 1,
    "warning": 1,
    "alert": 2,
    "emergency": 3,
    "full": 4,
}


@dataclass(frozen=True)
class NotificationEvent:
    key: str
    level: str
    title: str
    message: str


@dataclass(frozen=True)
class NotificationResult:
    sent: int
    suppressed: int
    outbox_file: Optional[Path]
    error: str = ""


def notification_dir(data_dir: Path) -> Path:
    path = data_dir / "notifications"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_file(data_dir: Path) -> Path:
    return data_dir / "notification_state.json"


def _status_file(data_dir: Path) -> Path:
    return data_dir / "notification_status.json"


def read_notification_status(data_dir: Path) -> Dict[str, object]:
    try:
        payload = json.loads(_status_file(data_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {
            "updated_at": "",
            "mode": "",
            "sent": 0,
            "suppressed": 0,
            "outbox_file": "",
            "error": "",
        }
    return payload if isinstance(payload, dict) else {}


def _write_delivery_status(
    data_dir: Path,
    settings: Settings,
    current: datetime,
    sent: int,
    suppressed: int,
    outbox_file: Optional[Path],
    error: str = "",
) -> None:
    _atomic_json(
        _status_file(data_dir),
        {
            "updated_at": current.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": settings.notification_mode,
            "sent": sent,
            "suppressed": suppressed,
            "outbox_file": str(outbox_file) if outbox_file else "",
            "error": error,
        },
    )


def _read_state(data_dir: Path) -> Dict[str, dict]:
    try:
        payload = json.loads(_state_file(data_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


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


def _eligible_events(
    events: Iterable[NotificationEvent],
    settings: Settings,
    state: Dict[str, dict],
    now: datetime,
) -> Tuple[List[NotificationEvent], int]:
    minimum = LEVEL_RANK[settings.notification_min_level]
    eligible: List[NotificationEvent] = []
    suppressed = 0
    cooldown = timedelta(hours=settings.notification_cooldown_hours)
    for event in events:
        if event.level == "recovery":
            eligible.append(event)
            continue
        if LEVEL_RANK.get(event.level, 0) < minimum:
            continue
        previous = state.get(event.key, {})
        try:
            last_sent = datetime.strptime(str(previous.get("last_sent", "")), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            last_sent = None
        previous_rank = LEVEL_RANK.get(str(previous.get("level", "")), 0)
        if (
            last_sent is not None
            and now - last_sent < cooldown
            and LEVEL_RANK[event.level] <= previous_rank
        ):
            suppressed += 1
            continue
        eligible.append(event)
    return eligible, suppressed


def _send_command(settings: Settings, encoded: str) -> None:
    if not settings.notification_command:
        raise RuntimeError("notification_command is empty")
    subprocess.run(
        settings.notification_command,
        input=encoded,
        text=True,
        check=True,
        timeout=settings.notification_timeout_seconds,
    )


def _send_webhook(settings: Settings, encoded: str) -> None:
    if not settings.notification_webhook_url:
        raise RuntimeError("notification_webhook_url is empty")
    web_request = request.Request(
        settings.notification_webhook_url,
        data=encoded.encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with request.urlopen(web_request, timeout=settings.notification_timeout_seconds) as response:
        if not 200 <= int(response.status) < 300:
            raise RuntimeError(f"notification webhook returned HTTP {response.status}")


def dispatch_notifications(
    data_dir: Path,
    settings: Settings,
    events: Iterable[NotificationEvent],
    now: Optional[datetime] = None,
) -> NotificationResult:
    if settings.notification_mode == "disabled":
        current = now or datetime.now()
        _write_delivery_status(data_dir, settings, current, 0, 0, None)
        return NotificationResult(0, 0, None)
    current = now or datetime.now()
    state = _read_state(data_dir)
    state_cutoff = current - timedelta(days=settings.history_days)
    for key, previous in list(state.items()):
        try:
            last_sent = datetime.strptime(
                str(previous.get("last_sent", "")),
                "%Y-%m-%d %H:%M:%S",
            )
        except (AttributeError, ValueError):
            state.pop(key, None)
            continue
        if last_sent < state_cutoff:
            state.pop(key, None)
    eligible, suppressed = _eligible_events(events, settings, state, current)
    if not eligible:
        _write_delivery_status(data_dir, settings, current, 0, suppressed, None)
        return NotificationResult(0, suppressed, None)

    payload = {
        "source": "storage-manager-vwp",
        "generated_at": current.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": settings.notification_mode,
        "events": [asdict(event) for event in eligible],
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    filename = f"{current:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.json"
    outbox_path = notification_dir(data_dir) / filename
    _atomic_json(outbox_path, payload)

    try:
        if settings.notification_mode == "command":
            _send_command(settings, encoded)
        elif settings.notification_mode == "webhook":
            _send_webhook(settings, encoded)
        elif settings.notification_mode != "outbox":
            raise RuntimeError(f"Unsupported notification mode: {settings.notification_mode}")
    except Exception as exc:
        failed_path = outbox_path.with_name(outbox_path.stem + "_FAILED.json")
        _atomic_json(
            failed_path,
            {**payload, "delivery_error": str(exc)},
        )
        _write_delivery_status(
            data_dir,
            settings,
            current,
            0,
            suppressed,
            failed_path,
            str(exc),
        )
        return NotificationResult(0, suppressed, failed_path, str(exc))

    timestamp = current.strftime("%Y-%m-%d %H:%M:%S")
    for event in eligible:
        if event.level == "recovery":
            state.pop(event.key, None)
        else:
            state[event.key] = {"last_sent": timestamp, "level": event.level}
    _atomic_json(_state_file(data_dir), state)
    _write_delivery_status(
        data_dir,
        settings,
        current,
        len(eligible),
        suppressed,
        outbox_path,
    )
    return NotificationResult(len(eligible), suppressed, outbox_path)


def purge_notification_outbox(
    data_dir: Path,
    keep_days: int,
    now: Optional[datetime] = None,
) -> None:
    cutoff = (now or datetime.now()) - timedelta(days=keep_days)
    directory = notification_dir(data_dir)
    for path in directory.glob("*.json"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
        except OSError:
            continue
