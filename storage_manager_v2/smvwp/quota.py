"""사내 quota 조회 어댑터 (선택 기능).

스토리지마다 quota 출력 형식이 제각각이라 파서를 앱에 박아 넣지 않는다. 대신
설정에 **shell 없이 실행할 argv 배열**을 두고, 그 명령이 아래 JSON을 stdout에
출력하기만 하면 된다:

    {"used_kb": 950000, "limit_kb": 1000000, "soft_limit_kb": 900000}

`{account}`와 `{path}`는 실행 직전에 치환된다. 예:

    ["/opt/company/bin/quota-json", "{account}", "{path}"]

설계 메모:
- **shell을 쓰지 않는다** (`shell=True` 금지). 계정 이름이나 경로에 공백·따옴표·
  세미콜론이 들어가도 명령이 재해석되지 않아야 하기 때문. argv 배열을 그대로
  넘기면 치환된 값은 항상 인자 하나로 취급된다.
- quota 조회에 실패해도 `df` 수집은 계속되어야 한다 (CONCEPT.md 7절 "한 계정이
  느려도 다른 계정 보고가 막히지 않는다"의 연장). 그래서 예외를 던지지 않고
  실패 사유만 담아 돌려준다.
- `limit_kb`가 0이거나 없으면 사용률을 계산하지 않는다 - 0으로 나눠 100%처럼
  보이게 만들지 않는다 (숫자를 지어내지 않는 원칙).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence

from . import i18n, procio, tiers


@dataclass
class QuotaResult:
    ok: bool
    used_kb: Optional[int] = None
    limit_kb: Optional[int] = None
    soft_limit_kb: Optional[int] = None
    pct: Optional[float] = None
    error_message: Optional[str] = None


def is_configured(command: Optional[Sequence[str]]) -> bool:
    return bool(command)


def build_command(command: Sequence[str], account_name: str, account_path: str) -> List[str]:
    """`{account}`/`{path}`를 치환한 argv를 만든다.

    `str.replace`를 쓰는 이유: `str.format`은 경로에 들어 있는 중괄호를 서식
    지시자로 오해할 수 있다."""

    return [
        part.replace("{account}", account_name).replace("{path}", account_path)
        for part in command
    ]


def query(
    command: Sequence[str],
    account_name: str,
    account_path: str,
    timeout_seconds: int = 10,
) -> QuotaResult:
    argv = build_command(command, account_name, account_path)
    try:
        # 사내 wrapper가 UTF-8 JSON을 낸다고 가정한다 - 로케일 인코딩에 맡기면
        # cron(LANG=C)에서 비ASCII 출력이 깨진다 (smvwp.procio 참고).
        proc = procio.run_utf8(argv, timeout=timeout_seconds)
    except FileNotFoundError:
        return QuotaResult(ok=False, error_message=f"quota 명령을 찾을 수 없습니다: {argv[0]}")
    except subprocess.TimeoutExpired:
        return QuotaResult(ok=False, error_message=f"quota 명령이 {timeout_seconds}초 내에 끝나지 않았습니다")
    except OSError as exc:
        return QuotaResult(ok=False, error_message=f"quota 명령 실행 실패: {exc}")

    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip() or f"exit={proc.returncode}"
        return QuotaResult(ok=False, error_message=f"quota 명령 실패: {message}")

    return parse_output(proc.stdout)


def parse_output(stdout: str) -> QuotaResult:
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError:
        return QuotaResult(ok=False, error_message=f"quota 출력이 JSON이 아닙니다: {stdout[:120]!r}")
    if not isinstance(raw, dict):
        return QuotaResult(ok=False, error_message="quota 출력의 최상위는 JSON 객체여야 합니다")

    def _maybe_int(key: str) -> Optional[int]:
        value = raw.get(key)
        if isinstance(value, bool) or value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    used_kb = _maybe_int("used_kb")
    limit_kb = _maybe_int("limit_kb")
    soft_limit_kb = _maybe_int("soft_limit_kb")

    if used_kb is None:
        return QuotaResult(ok=False, error_message="quota 출력에 used_kb가 없습니다")

    pct: Optional[float] = None
    if limit_kb:  # 0이나 None이면 사용률을 계산하지 않는다.
        pct = round(used_kb / limit_kb * 100, 1)

    return QuotaResult(
        ok=True,
        used_kb=used_kb,
        limit_kb=limit_kb,
        soft_limit_kb=soft_limit_kb,
        pct=pct,
    )


def tier_for(result: QuotaResult) -> str:
    """quota 사용률 등급. 한도를 모르면 판단하지 않는다(UNKNOWN)."""

    if not result.ok or result.pct is None:
        return tiers.UNKNOWN
    return tiers.classify(result.pct)


def format_usage(sample) -> str:
    """대시보드 quota 열 표시 문자열.

    quota를 설정하지 않았거나 조회에 실패했으면 '-'로 둔다 - 값이 없다는 것과
    0%인 것은 다르므로 절대 0으로 채우지 않는다."""

    pct = getattr(sample, "quota_pct", None)
    used_kb = getattr(sample, "quota_used_kb", None)
    limit_kb = getattr(sample, "quota_limit_kb", None)

    if pct is not None:
        return f"{pct:.1f}%"
    if used_kb is not None and not limit_kb:
        # 사용량은 알지만 한도가 없는 구성 - 퍼센트 대신 사용량만 보여준다.
        return f"{used_kb:,} KB"
    return i18n.t("common.none")
