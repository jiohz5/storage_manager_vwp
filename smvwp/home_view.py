"""홈 화면용 결과 파일 - 수집이 써 두고, 창은 그것만 읽는다 (Qt 없음).

## 왜 있는가 (재설계 검토 순서 4 의 첫 단계)

창을 열면 장비마다 NFS 위 SQLite 를 열고 30일 이력을 읽어 추세와 FULL 예측을 계산했다.
대응책을 스무 가지 넘게 쌓았지만(DESIGN.md "NFS 위에서 창이 느렸던 이유") 첫 홈은 몇
초였고, 수집·스캔이 쓰는 DB 와 잠금을 다투어 실기에서 "database is locked" 가 났다.
그리고 SQLite 는 읽기만 해도 저널을 만들어, 보기만 하는 사람에게도 데이터 폴더 쓰기
권한이 필요했다.

이제 **수집(15분)이 끝날 때 홈 한 장을 파일로** 써 둔다 (`write`, `cycle.
run_collection_cycle` 의 끝 - cron 이든 창의 수집이든). 창은 그 파일만 읽는다 (`load`).
파일 하나를 통째로 읽는 것이라 잠금을 다투지 않고, 읽기 권한만 있으면 된다.

## 믿을 수 없는 파일이면 예전처럼 DB 에서

없거나(업데이트 직후, 아직 한 번도 수집 전), 다른 판이거나(`FORMAT`), 다른 계정 설정
으로 쓴 것이거나(`fingerprint`), 낡았으면(수집 간격의 두 배를 넘겼다 - cron 이 멈췄다)
`dashboard.read_dashboard` 로 돌아간다. 틀린 숫자보다 느린 숫자가 낫다.

## 왜 JSON 인가 (pickle 이 아니라)

데이터 폴더는 여럿이 같이 쓴다. pickle 을 읽으면 그 폴더에 쓸 수 있는 누구든 모두의
창에서 코드를 돌릴 수 있다. JSON 은 값만 들고, 되살릴 수 있는 모양은 `_TYPES` 에 정해
둔 몇 가지뿐이다 - 그 밖의 이름이 들어 있으면 파일을 버린다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from . import analytics, atomic, dashboard, priority, store, trend

logger = logging.getLogger(__name__)

# 파일 모양의 판. 담는 것이나 그 모양이 바뀌면 올린다 - 옛 판의 창이 새 파일을(또는
# 새 판의 창이 옛 파일을) 잘못 읽지 않고 DB 로 돌아간다.
FORMAT = 1

# 되살릴 수 있는 모양들. 이 밖의 이름은 받지 않는다 (위 "왜 JSON 인가").
_TYPES = {
    cls.__name__: cls
    for cls in (
        store.SampleRecord, analytics.Prediction, analytics.CapacityForecast,
        trend.Point, trend.Series, priority.Plan, priority.Action,
    )
}


# 파일을 쓰지 않는 까닭 (`problem`) - `smvwp_cli.py status` 가 보여 준다.
PROBLEM_MISSING = "없음"
PROBLEM_BROKEN = "깨짐"
PROBLEM_FORMAT = "다른 판"
PROBLEM_ACCOUNTS = "다른 계정 설정"
PROBLEM_STALE = "낡음"


class _Refused(ValueError):
    """정해 둔 모양 밖의 것이 들어 있다."""


def view_path(data_dir: Path) -> Path:
    return Path(data_dir) / "view" / "home.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint(config) -> str:
    """이 홈을 만든 계정 설정의 지문 - 계정이 늘거나 설정이 바뀌면 달라진다."""

    accounts = [
        [account.account_id, account.name, account.path, account.kind, account.enabled]
        for account in config.accounts
    ]
    settings = dataclasses.asdict(config.settings)
    text = json.dumps([accounts, settings], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# -- 값 <-> JSON ---------------------------------------------------------------

def _encode(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        body = {spec.name: _encode(getattr(value, spec.name)) for spec in dataclasses.fields(value)}
        body["__t"] = type(value).__name__
        return body
    if isinstance(value, datetime):
        return {"__dt": value.isoformat()}
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return value


def _decode(value):
    if isinstance(value, dict):
        if "__dt" in value:
            return datetime.fromisoformat(value["__dt"])
        if "__t" in value:
            cls = _TYPES.get(value["__t"])
            if cls is None:
                raise _Refused(value["__t"])
            names = {spec.name for spec in dataclasses.fields(cls)}
            return cls(**{key: _decode(item) for key, item in value.items() if key in names})
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


# -- 쓰기 / 읽기 ---------------------------------------------------------------

def write(data_dir: Path, config, data: Optional["dashboard.DashboardData"] = None) -> Path:
    """홈 한 장을 파일로 (수집이 끝날 때). `data` 가 없으면 DB 에서 읽어 만든다."""

    if data is None:
        data = dashboard.read_dashboard(data_dir, config)
    payload = {
        "format": FORMAT,
        "fingerprint": fingerprint(config),
        "written_at": _now().isoformat(),
        "data": {
            "samples": _encode(data.samples),
            "forecasts": _encode(data.forecasts),
            "forecast_failed": data.forecast_failed,
            "plan": _encode(data.plan),
            "trends": _encode(data.trends),
            "newest": data.newest,
        },
    }
    path = view_path(data_dir)
    atomic.write_text(path, json.dumps(payload, ensure_ascii=False))
    return path


def _payload(data_dir: Path):
    """`(payload, 문제)` - 읽지 못했으면 payload 는 None."""

    path = view_path(data_dir)
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, PROBLEM_MISSING
    except (OSError, ValueError):
        logger.warning("홈 결과 파일을 읽지 못해 DB 에서 읽습니다: %s", path)
        return None, PROBLEM_BROKEN


def _problem_of(payload, config) -> Optional[str]:
    try:
        if payload.get("format") != FORMAT:
            return PROBLEM_FORMAT
        if payload.get("fingerprint") != fingerprint(config):
            return PROBLEM_ACCOUNTS
        written = datetime.fromisoformat(payload["written_at"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return PROBLEM_BROKEN
    allowed = timedelta(seconds=2 * max(60, int(config.settings.collector_interval_seconds)))
    if _now() - written > allowed:
        return PROBLEM_STALE
    return None


def problem(data_dir: Path, config) -> Optional[str]:
    """창이 이 파일을 쓰지 않을 까닭 (`PROBLEM_*`), 쓸 수 있으면 None (속 모양까지는 안 본다)."""

    payload, trouble = _payload(data_dir)
    return trouble or _problem_of(payload, config)


def read(data_dir: Path, config) -> Optional["dashboard.DashboardData"]:
    """믿을 수 있는 파일이면 그 홈, 아니면 None (부르는 쪽이 DB 로 간다)."""

    path = view_path(data_dir)
    payload, trouble = _payload(data_dir)
    if trouble or _problem_of(payload, config):
        return None
    try:
        body = payload["data"]
        plan = _decode(body.get("plan"))
        if plan is not None:
            for action in plan.actions:
                action.shared = [tuple(member) for member in action.shared]
        return dashboard.DashboardData(
            samples=_decode(body.get("samples") or {}),
            forecasts=_decode(body.get("forecasts") or {}),
            forecast_failed=bool(body.get("forecast_failed")),
            plan=plan,
            trends=_decode(body.get("trends") or {}),
            newest=body.get("newest"),
        )
    except _Refused as exc:
        logger.warning("홈 결과 파일에 모르는 모양(%s)이 있어 버리고 DB 에서 읽습니다", exc)
        return None
    except (KeyError, TypeError, ValueError, AttributeError):
        logger.warning("홈 결과 파일의 모양이 맞지 않아 DB 에서 읽습니다: %s", path)
        return None


def load(data_dir: Path, config, on_step=None, cache=None,
         at_least: Optional[str] = None) -> "dashboard.DashboardData":
    """창이 홈을 읽는 길 - 결과 파일이 믿을 만하면 그것, 아니면 DB.

    `at_least` 는 창이 이미 아는 가장 새 표본 시각이다 (방금 수집했거나, 남이 수집한 것을
    봤다). 파일이 그보다 옛것이면 DB 에서 읽는다 - 수집은 표본을 DB 에 넣고 한참 뒤에야
    파일을 쓰므로, 그 사이에 읽으면 지난번 파일을 집는다. 창과 cron 의 주기가 같아 한 번
    그 틈에 걸린 창은 계속 한 바퀴 늦은 홈을 보였을 것이다.

    어느 길로 읽었는지 기록에 남긴다 - 실기에서 파일이 실제로 쓰이는지 알 수 있어야 한다."""

    data = read(data_dir, config)
    if data is not None and at_least and not dashboard.already_shows(data.newest, at_least):
        logger.info("홈 결과 파일이 방금 본 표본(%s)보다 옛것(%s)이라 DB 에서 읽습니다",
                    at_least, data.newest)
        data = None
    if data is not None:
        logger.info("홈을 결과 파일에서 읽었습니다 (표본 %s)", data.newest)
        return data
    logger.info("홈을 DB 에서 읽습니다 (결과 파일이 없거나 믿을 수 없음: %s)", view_path(data_dir))
    return dashboard.read_dashboard(data_dir, config, on_step=on_step, cache=cache)
