from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class QuotaSnapshot:
    used_kb: int
    limit_kb: int
    use_pct: int
    soft_limit_kb: Optional[int] = None


def collect_quota(
    command_template: Iterable[str],
    account_name: str,
    account_path: str,
    timeout_seconds: int,
) -> Optional[QuotaSnapshot]:
    template = list(command_template)
    if not template:
        return None
    command = [
        argument.replace("{account}", account_name).replace("{path}", account_path)
        for argument in template
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=True,
    )
    try:
        payload = json.loads(result.stdout)
        used_kb = int(payload["used_kb"])
        limit_kb = int(payload["limit_kb"])
        soft_limit = payload.get("soft_limit_kb")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Quota command must output JSON with used_kb and limit_kb") from exc
    if used_kb < 0 or limit_kb <= 0:
        raise ValueError("Quota values must be non-negative and limit_kb must be positive")
    return QuotaSnapshot(
        used_kb=used_kb,
        limit_kb=limit_kb,
        use_pct=min(999, int(round(used_kb * 100 / limit_kb))),
        soft_limit_kb=int(soft_limit) if soft_limit is not None else None,
    )
