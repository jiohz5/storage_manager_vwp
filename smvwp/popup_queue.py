"""outbox 알림 파일을 읽고 "읽음" 상태를 관리하는 큐 (PyQt5 비의존).

트레이 notifier(`smvwp/notifier.py`)가 이 큐를 소비한다. GUI 코드와 분리해 둔
이유는 두 가지다:
1. 큐 동작(읽음 처리, 미확인 집계, 보존기간 정리)을 PyQt5 없이 단위 테스트할
   수 있어야 한다.
2. 로그아웃 중 cron이 쌓아 둔 알림을 다음 로그인 때 요약해 보여주는 흐름이
   GUI 생명주기와 무관해야 한다.

중요한 원칙: **읽음 처리는 사용자가 실제로 확인했을 때만 한다.** 파일을 읽어
팝업을 띄웠다는 사실만으로 읽음 처리하면, 자리를 비운 사이 뜬 팝업이 그대로
사라져 놓치게 된다. 그래서 `mark_read`는 notifier가 팝업을 띄울 때가 아니라
사용자가 확인했을 때 호출한다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from . import notifications


def read_state_file(data_dir: Path) -> Path:
    return data_dir / "popup_read_state.json"


@dataclass
class PendingPopup:
    path: Path
    event_id: str
    generated_at: str
    account_name: str
    tier: str
    tier_label: str
    message: str

    @property
    def sort_key(self) -> str:
        return self.generated_at


def _load_read_ids(data_dir: Path) -> set:
    path = read_state_file(data_dir)
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(raw.get("read_event_ids", []))


def _save_read_ids(data_dir: Path, read_ids: set) -> None:
    path = read_state_file(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    payload = {"read_event_ids": sorted(read_ids), "updated_at": datetime.now(timezone.utc).isoformat()}
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temp_path), str(path))


def list_pending(data_dir: Path, max_age_days: int = 7) -> List[PendingPopup]:
    """아직 확인하지 않은 알림을 오래된 순으로 반환한다.

    `max_age_days`보다 오래된 것은 보여주지 않는다 - 로그아웃이 길었을 때
    몇 주치 팝업이 한꺼번에 뜨는 것을 막기 위함이다."""

    outbox = notifications.outbox_dir(data_dir)
    if not outbox.exists():
        return []

    read_ids = _load_read_ids(data_dir)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    pending: List[PendingPopup] = []

    for path in sorted(outbox.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        event_id = raw.get("event_id")
        if not event_id or event_id in read_ids:
            continue
        generated_at = raw.get("generated_at", "")
        try:
            generated_dt = datetime.fromisoformat(generated_at)
        except ValueError:
            generated_dt = None
        if generated_dt is not None:
            if generated_dt.tzinfo is None:
                generated_dt = generated_dt.replace(tzinfo=timezone.utc)
            if generated_dt < cutoff:
                continue
        pending.append(
            PendingPopup(
                path=path,
                event_id=event_id,
                generated_at=generated_at,
                account_name=raw.get("account_name", "?"),
                tier=raw.get("tier", "unknown"),
                tier_label=raw.get("tier_label", ""),
                message=raw.get("message", ""),
            )
        )

    pending.sort(key=lambda item: item.sort_key)
    return pending


def unread_count(data_dir: Path, max_age_days: int = 7) -> int:
    return len(list_pending(data_dir, max_age_days))


def mark_read(data_dir: Path, event_ids: List[str]) -> None:
    """사용자가 확인한 알림만 읽음 처리한다 (팝업을 띄운 시점이 아니라)."""

    if not event_ids:
        return
    read_ids = _load_read_ids(data_dir)
    read_ids.update(event_ids)
    _save_read_ids(data_dir, read_ids)


def mark_all_read(data_dir: Path, max_age_days: int = 7) -> int:
    pending = list_pending(data_dir, max_age_days)
    mark_read(data_dir, [item.event_id for item in pending])
    return len(pending)


def prune_old_events(data_dir: Path, retention_days: int) -> int:
    """보존 기간이 지난 outbox 파일과 읽음 기록을 정리한다.

    이 앱이 만든 알림 파일만 지운다 - 모니터링 대상에는 손대지 않는다."""

    outbox = notifications.outbox_dir(data_dir)
    if not outbox.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    surviving_ids = set()

    for path in outbox.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            generated_dt = datetime.fromisoformat(raw.get("generated_at", ""))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if generated_dt.tzinfo is None:
            generated_dt = generated_dt.replace(tzinfo=timezone.utc)
        if generated_dt < cutoff:
            path.unlink()
            removed += 1
        elif raw.get("event_id"):
            surviving_ids.add(raw["event_id"])

    # 파일이 사라진 event_id는 읽음 기록에서도 빼서 상태 파일이 무한히 자라지
    # 않게 한다.
    read_ids = _load_read_ids(data_dir)
    trimmed = read_ids & surviving_ids
    if trimmed != read_ids:
        _save_read_ids(data_dir, trimmed)

    return removed
