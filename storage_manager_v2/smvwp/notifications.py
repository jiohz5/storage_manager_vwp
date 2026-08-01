"""알림 생성과 전송 (파일 outbox / 사내 command / 내부 webhook).

CONCEPT.md 5절의 폐쇄망 알림 정책을 따른다.

- **outbox (기본)**: `outbox/` 아래에 JSON 파일을 쌓는다. 별도 트레이 프로세스
  (`smvwp.notifier`)가 이 파일을 읽어 팝업을 띄운다. 네트워크가 전혀 필요
  없으므로 가장 안전한 기본값이다.
- **command**: 사내 전송 프로그램을 **shell 없이** argv 그대로 실행하고 알림
  JSON을 stdin(UTF-8)으로 넘긴다. shell을 거치지 않으므로 계정명·경로에
  특수문자가 있어도 명령이 재해석되지 않는다.
- **webhook**: 사내 HTTP(S) endpoint에 UTF-8 JSON을 POST한다. 표준 라이브러리
  `urllib`만 쓴다 (폐쇄망에 requests 같은 외부 패키지를 깔 수 없다).
- **disabled**: 이벤트 생성/전송을 모두 끈다.

어떤 모드든 **감사 기록은 항상 남긴다**: 보낸 알림 원문과 전송 결과를
`notify_audit/`에 기록한다. 전송에 실패해도 무엇을 보내려 했는지는 남아야
운영자가 나중에 추적할 수 있다.

cooldown 규칙:
- 동일 계정 + 동일 등급은 cooldown 시간 동안 재알림하지 않는다.
- 심각도가 상승하면 cooldown을 무시하고 즉시 재알림한다.
- 등급이 정상으로 돌아오면 상태를 리셋한다 (다음에 다시 나빠지면 새로 카운트).
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Sequence

from . import config as config_module
from . import i18n, procio, tiers
from .config import Account
from .store import SampleRecord

NOTIFICATION_SCHEMA_VERSION = 1


def outbox_dir(data_dir: Path) -> Path:
    return data_dir / "outbox"


def audit_dir(data_dir: Path) -> Path:
    return data_dir / "notify_audit"


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


def _event_filename(event: NotificationEvent) -> str:
    safe_ts = event.generated_at.replace(":", "").replace("+", "_")
    return f"{safe_ts}_{event.account_id}_{event.tier}_{event.event_id[:8]}.json"


def write_event(data_dir: Path, event: NotificationEvent) -> Path:
    target_dir = outbox_dir(data_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / _event_filename(event)
    _atomic_write_json(path, asdict(event))
    return path


@dataclass
class DeliveryResult:
    mode: str
    ok: bool
    detail: str = ""
    outbox_path: Optional[str] = None


def _send_via_command(
    event: NotificationEvent, command: Sequence[str], timeout_seconds: int
) -> DeliveryResult:
    """알림 JSON을 stdin으로 넘겨 사내 프로그램을 실행한다 (shell 사용 안 함).

    `{account}` 같은 치환은 하지 않는다 - 명령은 고정 argv이고 내용은 전부
    stdin JSON으로 전달한다. 인자에 사용자 데이터를 끼워 넣지 않는 편이
    안전하다."""

    payload = json.dumps(asdict(event), ensure_ascii=False)
    try:
        # UTF-8을 명시한다 - cron의 LANG=C 환경에서 로케일 인코딩에 맡기면
        # 한글이 든 알림이 인코딩 단계에서 실패한다 (smvwp.procio 참고).
        proc = procio.run_utf8(command, timeout=timeout_seconds, input_text=payload)
    except FileNotFoundError:
        return DeliveryResult(
            mode=config_module.NOTIFY_MODE_COMMAND,
            ok=False,
            detail=f"명령을 찾을 수 없습니다: {command[0]}",
        )
    except subprocess.TimeoutExpired:
        return DeliveryResult(
            mode=config_module.NOTIFY_MODE_COMMAND,
            ok=False,
            detail=f"{timeout_seconds}초 내에 끝나지 않았습니다",
        )
    except OSError as exc:
        return DeliveryResult(
            mode=config_module.NOTIFY_MODE_COMMAND, ok=False, detail=f"실행 실패: {exc}"
        )

    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip() or f"exit={proc.returncode}"
        return DeliveryResult(mode=config_module.NOTIFY_MODE_COMMAND, ok=False, detail=message)
    return DeliveryResult(mode=config_module.NOTIFY_MODE_COMMAND, ok=True, detail="전송 성공")


def _send_via_webhook(event: NotificationEvent, url: str, timeout_seconds: int) -> DeliveryResult:
    """사내 HTTP(S) endpoint에 UTF-8 JSON을 POST한다 (표준 urllib만 사용)."""

    payload = json.dumps(asdict(event), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", None) or response.getcode()
            return DeliveryResult(
                mode=config_module.NOTIFY_MODE_WEBHOOK, ok=True, detail=f"HTTP {status}"
            )
    except urllib.error.HTTPError as exc:
        return DeliveryResult(
            mode=config_module.NOTIFY_MODE_WEBHOOK, ok=False, detail=f"HTTP {exc.code}"
        )
    except urllib.error.URLError as exc:
        return DeliveryResult(
            mode=config_module.NOTIFY_MODE_WEBHOOK, ok=False, detail=f"연결 실패: {exc.reason}"
        )
    except (OSError, ValueError) as exc:  # pragma: no cover - 방어적 처리
        return DeliveryResult(mode=config_module.NOTIFY_MODE_WEBHOOK, ok=False, detail=str(exc))


def write_audit(data_dir: Path, event: NotificationEvent, result: DeliveryResult) -> Path:
    """알림 원문 + 전송 결과를 감사 기록으로 남긴다.

    전송이 실패해도 "무엇을 보내려 했는지"는 반드시 남아야 한다 - 그래야
    나중에 운영자가 누락을 추적할 수 있다."""

    target_dir = audit_dir(data_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / _event_filename(event)
    _atomic_write_json(path, {"event": asdict(event), "delivery": asdict(result)})
    return path


def deliver(
    data_dir: Path,
    event: NotificationEvent,
    mode: str = config_module.NOTIFY_MODE_OUTBOX,
    command: Optional[Sequence[str]] = None,
    webhook_url: str = "",
    timeout_seconds: int = 10,
) -> DeliveryResult:
    """설정된 모드로 알림을 전송하고 감사 기록을 남긴다.

    전송 실패는 예외로 올리지 않는다 - 알림 하나 못 보냈다고 수집 사이클
    전체가 죽으면 정작 중요한 용량 데이터까지 잃는다."""

    if mode == config_module.NOTIFY_MODE_DISABLED:
        result = DeliveryResult(mode=mode, ok=True, detail="알림이 꺼져 있습니다")
    elif mode == config_module.NOTIFY_MODE_COMMAND and command:
        result = _send_via_command(event, command, timeout_seconds)
    elif mode == config_module.NOTIFY_MODE_WEBHOOK and webhook_url:
        result = _send_via_webhook(event, webhook_url, timeout_seconds)
    else:
        # 기본값이자 최종 안전망: 파일 outbox. command/webhook이 설정 미비로
        # 선택될 수 없을 때도 알림을 잃지 않도록 여기로 떨어진다.
        path = write_event(data_dir, event)
        result = DeliveryResult(
            mode=config_module.NOTIFY_MODE_OUTBOX,
            ok=True,
            detail="outbox 기록",
            outbox_path=str(path),
        )

    write_audit(data_dir, event, result)
    return result


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


def build_event(
    account: Account, sample: SampleRecord, now: datetime
) -> NotificationEvent:
    tier = sample.overall_tier

    def _pct(value: Optional[float]) -> str:
        # 값이 없을 때 "확인불가%"처럼 되지 않도록 % 기호까지 여기서 붙인다.
        return f"{value:.1f}%" if value is not None else i18n.t("common.unknown_value")

    message = i18n.t(
        "notify.message",
        tier=tiers.label(tier),
        account=account.name,
        path=account.path,
        byte_pct=_pct(sample.byte_pct),
        inode_pct=_pct(sample.inode_pct),
    )
    return NotificationEvent(
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


def maybe_notify(
    data_dir: Path,
    account: Account,
    sample: SampleRecord,
    state: Dict[str, dict],
    min_tier: str = "warn",
    cooldown_minutes: int = 60,
    now: Optional[datetime] = None,
    mode: str = config_module.NOTIFY_MODE_OUTBOX,
    command: Optional[Sequence[str]] = None,
    webhook_url: str = "",
    timeout_seconds: int = 10,
) -> Optional[DeliveryResult]:
    """조건이 맞으면 알림을 전송하고 state를 갱신한다 (in-place).

    반환값은 전송 결과 (알림을 안 보냈으면 None). state 딕셔너리는 호출자가
    수집 루프 전체에 걸쳐 재사용하고, 끝나면 save_notify_state로 저장해야
    한다.
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

    event = build_event(account, sample, now)
    result = deliver(
        data_dir,
        event,
        mode=mode,
        command=command,
        webhook_url=webhook_url,
        timeout_seconds=timeout_seconds,
    )
    # 전송 실패도 "보냈다"로 기록한다 - 실패했다고 cooldown을 무시하면 매
    # 수집 주기마다 같은 실패를 반복해 로그와 endpoint를 두드리게 된다.
    # 실패 사실 자체는 감사 기록(notify_audit)에 남는다.
    state[account.account_id] = {"tier": tier, "last_notified_at": now.isoformat()}
    return result
