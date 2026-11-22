"""상세 스캔 상태 결과 파일 - 수집과 야간 스캔이 써 두고, 창은 그것만 읽는다 (Qt 없음).

## 왜 (재설계 검토 순서 4 의 둘째 단계)

창은 상세 스캔 탭을 그리려고 30초마다(스캔이 돌면 5초마다) NFS 위 스캔 DB 를 열었다.
요약을 남겨 둬 읽는 양은 줄였지만 잠금 왕복은 그대로였고, 밤 스캔이 DB 를 쥐고 있으면
최대 30초 기다렸다. SQLite 는 읽기만 해도 저널을 만들어 보기만 하는 사람에게도 쓰기
권한이 필요했다. 홈(`home_view`)과 같은 방식으로 푼다.

- **수집이 끝날 때**(15분, `cycle`) 한 장을 쓴다.
- **야간 스캔이 도는 동안** `WRITE_EVERY_SECONDS` 마다, 그리고 끝날 때 쓴다
  (`nightly_scan._ViewKeeper`).
- 창은 파일 하나를 통째로 읽는다 (`load`). '지금 도는가' 만은 잠금 파일로 늘 새로
  본다 - 파일보다 그쪽이 빠르고 정확하다.

## 믿을 수 없으면 예전처럼 DB 에서

없거나, 다른 판(`FORMAT`)이거나, 다른 계정 설정(`home_view.fingerprint`)이거나, 낡았으면
(스캔 중에 쓴 것은 `RUNNING_STALE_SECONDS`, 아니면 수집 간격의 두 배) DB 로 돌아간다.
값은 JSON 으로만 담는다 (`home_view` 의 "왜 JSON 인가" 와 같은 까닭).
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from . import atomic, home_view, scan_lock, scan_status

logger = logging.getLogger(__name__)

FORMAT = 1
# 야간 스캔이 도는 동안 파일을 쓰는 간격, 그리고 스캔 중에 쓴 파일을 믿는 시간.
WRITE_EVERY_SECONDS = 30
RUNNING_STALE_SECONDS = 180

PROBLEM_MISSING = home_view.PROBLEM_MISSING
PROBLEM_BROKEN = home_view.PROBLEM_BROKEN
PROBLEM_FORMAT = home_view.PROBLEM_FORMAT
PROBLEM_ACCOUNTS = home_view.PROBLEM_ACCOUNTS
PROBLEM_STALE = home_view.PROBLEM_STALE

# 되살릴 때 튜플이어야 하는 칸 (화면이 풀어 쓰거나 견준다).
_TUPLE_LISTS = ("large_files", "failed_paths")


def view_path(data_dir: Path) -> Path:
    return Path(data_dir) / "view" / "scan.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _plain(value):
    """스냅샷 안의 것을 JSON 에 담을 수 있게 (sqlite3.Row 는 dict 로)."""

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {spec.name: _plain(getattr(value, spec.name)) for spec in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "keys") and callable(value.keys):   # sqlite3.Row
        return {key: _plain(value[key]) for key in value.keys()}
    return value


def _account(body: dict) -> "scan_status.AccountScanSnapshot":
    names = {spec.name for spec in dataclasses.fields(scan_status.AccountScanSnapshot)}
    fields = {key: value for key, value in body.items() if key in names}
    for key in _TUPLE_LISTS:
        fields[key] = [tuple(item) for item in fields.get(key) or []]
    if fields.get("compared_kb") is not None:
        fields["compared_kb"] = tuple(fields["compared_kb"])
    fields["folder_trends"] = {
        path: [tuple(point) for point in points]
        for path, points in (fields.get("folder_trends") or {}).items()
    }
    return scan_status.AccountScanSnapshot(**fields)


def write(data_dir: Path, config, snapshot=None) -> Path:
    """상세 스캔 상태 한 장을 파일로. `snapshot` 이 없으면 DB 에서 읽어 만든다."""

    if snapshot is None:
        snapshot = scan_status.get_status_snapshot(data_dir, config)
    payload = {
        "format": FORMAT,
        "fingerprint": home_view.fingerprint(config),
        "written_at": _now().isoformat(),
        "running": bool(snapshot.is_running),
        "snapshot": _plain(snapshot),
    }
    path = view_path(data_dir)
    atomic.write_text(path, json.dumps(payload, ensure_ascii=False))
    return path


def _payload(data_dir: Path):
    path = view_path(data_dir)
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, PROBLEM_MISSING
    except (OSError, ValueError):
        logger.warning("상세 스캔 결과 파일을 읽지 못해 DB 에서 읽습니다: %s", path)
        return None, PROBLEM_BROKEN


def _problem_of(payload, config) -> Optional[str]:
    try:
        if payload.get("format") != FORMAT:
            return PROBLEM_FORMAT
        if payload.get("fingerprint") != home_view.fingerprint(config):
            return PROBLEM_ACCOUNTS
        written = datetime.fromisoformat(payload["written_at"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return PROBLEM_BROKEN
    if payload.get("running"):
        allowed = timedelta(seconds=RUNNING_STALE_SECONDS)
    else:
        allowed = timedelta(seconds=2 * max(60, int(config.settings.collector_interval_seconds)))
    if _now() - written > allowed:
        return PROBLEM_STALE
    return None


def problem(data_dir: Path, config) -> Optional[str]:
    """창이 이 파일을 쓰지 않을 까닭 (`PROBLEM_*`), 쓸 수 있으면 None."""

    payload, trouble = _payload(data_dir)
    return trouble or _problem_of(payload, config)


def _decode(payload) -> "scan_status.StatusSnapshot":
    body = dict(payload["snapshot"])
    body["accounts"] = [_account(item) for item in body.get("accounts") or []]
    names = {spec.name for spec in dataclasses.fields(scan_status.StatusSnapshot)}
    return scan_status.StatusSnapshot(**{key: value for key, value in body.items() if key in names})


def _read(data_dir: Path, config):
    """`(스냅샷 또는 None, 문제)` - 파일을 한 번만 읽는다."""

    payload, trouble = _payload(data_dir)
    trouble = trouble or _problem_of(payload, config)
    if trouble:
        return None, trouble
    try:
        return _decode(payload), None
    except (KeyError, TypeError, ValueError, AttributeError):
        logger.warning("상세 스캔 결과 파일의 모양이 맞지 않아 DB 에서 읽습니다: %s",
                       view_path(data_dir))
        return None, PROBLEM_BROKEN


def read(data_dir: Path, config) -> Optional["scan_status.StatusSnapshot"]:
    """믿을 수 있는 파일이면 그 스냅샷, 아니면 None."""

    return _read(data_dir, config)[0]


# 마지막으로 남긴 '파일을 못 쓴 까닭' - 같은 까닭을 30초마다 되풀이해 남기지 않는다.
_last_problem: dict = {}


def load(data_dir: Path, config, cache: Optional[dict] = None,
         on_account: Optional[Callable[[int, int], None]] = None):
    """창이 상세 스캔 상태를 읽는 길 - 결과 파일이 믿을 만하면 그것, 아니면 DB.

    '지금 도는가' 는 잠금 파일로 새로 본다 - 파일은 최대 `WRITE_EVERY_SECONDS` 묵었다.
    어느 길로 읽었는지는 바뀔 때만 기록에 남긴다."""

    snapshot, trouble = _read(data_dir, config)
    key = str(data_dir)
    if _last_problem.get(key, "?") != trouble:
        _last_problem[key] = trouble
        if trouble is None:
            logger.info("상세 스캔 상태를 결과 파일에서 읽습니다")
        else:
            logger.info("상세 스캔 상태를 DB 에서 읽습니다 (결과 파일: %s)", trouble)
    if snapshot is not None:
        snapshot.is_running = scan_lock.is_locked(data_dir)
        return snapshot
    return scan_status.get_status_snapshot(data_dir, config, cache=cache, on_account=on_account)
