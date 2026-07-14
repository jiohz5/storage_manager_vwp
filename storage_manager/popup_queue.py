from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from storage_manager.i18n import tr
from storage_manager.notifications import LEVEL_RANK, NotificationEvent, notification_dir


POPUP_STATE_FILENAME = "popup_state.json"


@dataclass(frozen=True)
class PopupEnvelope:
    path: Path
    generated_at: datetime
    events: Tuple[NotificationEvent, ...]

    @property
    def highest_level(self) -> str:
        return max(self.events, key=lambda event: LEVEL_RANK.get(event.level, 0)).level


def popup_state_file(data_dir: Path) -> Path:
    return data_dir / POPUP_STATE_FILENAME


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp), str(path))
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _read_state(data_dir: Path) -> Dict[str, object]:
    try:
        payload = json.loads(popup_state_file(data_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"acknowledged": {}}
    if not isinstance(payload, dict):
        return {"acknowledged": {}}
    acknowledged = payload.get("acknowledged")
    if not isinstance(acknowledged, dict):
        payload["acknowledged"] = {}
    return payload


def _parse_envelope(path: Path) -> Optional[PopupEnvelope]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        generated_at = datetime.strptime(
            str(payload["generated_at"]),
            "%Y-%m-%d %H:%M:%S",
        )
        raw_events = payload["events"]
    except (FileNotFoundError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw_events, list):
        return None
    events: List[NotificationEvent] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        try:
            event = NotificationEvent(
                key=str(raw["key"]),
                level=str(raw["level"]),
                title=str(raw["title"]),
                message=str(raw["message"]),
            )
        except KeyError:
            continue
        if event.level not in LEVEL_RANK:
            continue
        events.append(event)
    if not events:
        return None
    return PopupEnvelope(path=path, generated_at=generated_at, events=tuple(events))


def unread_notifications(
    data_dir: Path,
    backlog_days: int,
    now: Optional[datetime] = None,
) -> List[PopupEnvelope]:
    current = now or datetime.now()
    cutoff = current - timedelta(days=backlog_days)
    state = _read_state(data_dir)
    acknowledged = set(str(name) for name in state.get("acknowledged", {}))
    envelopes: List[PopupEnvelope] = []
    for path in notification_dir(data_dir).glob("*.json"):
        if path.name in acknowledged:
            continue
        envelope = _parse_envelope(path)
        if envelope is None or envelope.generated_at < cutoff:
            continue
        envelopes.append(envelope)
    envelopes.sort(key=lambda item: (item.generated_at, item.path.name))
    return envelopes


def acknowledge_notifications(
    data_dir: Path,
    paths: Iterable[Path],
    now: Optional[datetime] = None,
) -> None:
    current = now or datetime.now()
    directory = notification_dir(data_dir).resolve()
    state = _read_state(data_dir)
    raw_acknowledged = state.get("acknowledged", {})
    acknowledged = {
        str(name): str(timestamp)
        for name, timestamp in raw_acknowledged.items()
        if (directory / str(name)).is_file()
    }
    timestamp = current.strftime("%Y-%m-%d %H:%M:%S")
    for path in paths:
        candidate = Path(path).resolve()
        if candidate.parent == directory and candidate.is_file():
            acknowledged[candidate.name] = timestamp
    _atomic_json(
        popup_state_file(data_dir),
        {
            "updated_at": timestamp,
            "acknowledged": acknowledged,
        },
    )


def popup_summary(
    envelopes: Iterable[PopupEnvelope],
    language: str,
) -> Tuple[str, str]:
    items = list(envelopes)
    if not items:
        return tr(language, "popup.none.title"), tr(language, "popup.none.message")
    events = [event for envelope in items for event in envelope.events]
    highest = max(events, key=lambda event: LEVEL_RANK.get(event.level, 0)).level
    details = "\n".join(event.title for event in events[:5])
    if len(events) > 5:
        details += f"\n+{len(events) - 5}"
    return (
        tr(language, "popup.summary.title", level=highest.upper()),
        tr(language, "popup.summary.message", count=len(events), details=details),
    )
