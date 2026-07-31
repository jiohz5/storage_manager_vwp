"""로컬 파일 outbox 알림 (CONCEPT.md 5절의 가장 단순한 알림 경로).

cron 또는 GUI 내부 타이머가 주기 수집을 할 때마다 이 모듈이 JSON 파일을
`outbox/` 아래에 쌓는다. 별도의 트레이/notifier 프로세스가 이 파일들을 읽어
팝업을 띄우는 것은 phase 1 범위 밖이다 (REBUILD_CONCEPT.md 7절 4번 — "필요한
경우가 아니면 트레이 알림기 자체는 만들지 않아도 됨"). 다만 파일 포맷은
나중에 그 notifier가 그대로 소비할 수 있도록 스키마를 명확히 남긴다.

cooldown 규칙은 CONCEPT.md 5절을 따른다:
- 동일 계정 + 동일 등급은 cooldown 시간 동안 재알림하지 않는다.
- 심각도가 상승하면 cooldown을 무시하고 즉시 재알림한다.
- 등급이 정상으로 돌아오면 상태를 리셋한다 (다음에 다시 나빠지면 새로 카운트).

명령/webhook 알림 채널은 phase 1 범위 밖이다 (REBUILD_CONCEPT.md 8절 2번).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

from . import tiers
from .config import Account
from .store import SampleRecord

NOTIFICATION_SCHEMA_VERSION = 1


def outbox_dir(data_dir: Path) -> Path:
    return data_dir / "outbox"


def notify_state_file(data_dir: Path) -> Path:
    return data_dir / "notify_state.json"


@dataclass
class NotificationEvent:
    schema_version: int
    event_id: str
    generated_at: str
    account_id: str
    account_name: str
    account_path: str
    tier: str
    tier_label: str
    byte_pct: Optional[float]
    inode_pct: Optional[float]
    message: str


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temp_path), str(path))


def write_event(data_dir: Path, event: NotificationEvent) -> Path:
    target_dir = outbox_dir(data_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_ts = event.generated_at.replace(":", "").replace("+", "_")
    filename = f"{safe_ts}_{event.account_id}_{event.tier}_{event.event_id[:8]}.json"
    path = target_dir / filename
    _atomic_write_json(path, asdict(event))
    return path


def load_notify_state(data_dir: Path) -> Dict[str, dict]:
    path = notify_state_file(data_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_notify_state(data_dir: Path, state: Dict[str, dict]) -> None:
    _atomic_write_json(notify_state_file(data_dir), state)


def maybe_notify(
    data_dir: Path,
    account: Account,
    sample: SampleRecord,
    state: Dict[str, dict],
    min_tier: str = "warn",
    cooldown_minutes: int = 60,
    now: Optional[datetime] = None,
) -> Optional[Path]:
    """조건이 맞으면 outbox에 알림 파일을 쓰고 state를 갱신한다 (in-place).

    반환값은 실제로 쓰여진 알림 파일 경로 (안 썼으면 None). state 딕셔너리는
    호출자가 수집 루프 전체에 걸쳐 재사용하고, 끝나면 save_notify_state로
    저장해야 한다.
    """

    now = now or datetime.now(timezone.utc)
    tier = sample.overall_tier
    previous = state.get(account.account_id)

    if not sample.ok or not tiers.is_at_least(tier, min_tier):
        # 정상으로 복귀했거나 수집 실패 -> 다음에 다시 나빠지면 새로 시작하도록 리셋.
        if previous is not None:
            del state[account.account_id]
        return None

    if previous is not None:
        previous_tier = previous.get("tier")
        last_notified_at = previous.get("last_notified_at")
        severity_increased = tiers.severity(tier) > tiers.severity(previous_tier)
        if not severity_increased and last_notified_at:
            try:
                last_dt = datetime.fromisoformat(last_notified_at)
            except ValueError:
                last_dt = None
            if last_dt is not None and now - last_dt < timedelta(minutes=cooldown_minutes):
                # cooldown 중이며 심각도 상승도 없음 -> 억제.
                state[account.account_id] = {"tier": tier, "last_notified_at": last_notified_at}
                return None

    message = (
        f"[{tiers.label(tier)}] {account.name} ({account.path}) - "
        f"용량 {sample.byte_pct if sample.byte_pct is not None else '?'}% / "
        f"inode {sample.inode_pct if sample.inode_pct is not None else '?'}%"
    )
    event = NotificationEvent(
        schema_version=NOTIFICATION_SCHEMA_VERSION,
        event_id=uuid.uuid4().hex,
        generated_at=now.isoformat(),
        account_id=account.account_id,
        account_name=account.name,
        account_path=account.path,
        tier=tier,
        tier_label=tiers.label(tier),
        byte_pct=sample.byte_pct,
        inode_pct=sample.inode_pct,
        message=message,
    )
    path = write_event(data_dir, event)
    state[account.account_id] = {"tier": tier, "last_notified_at": now.isoformat()}
    return path
