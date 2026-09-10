"""누가 이 도구를 어떻게 쓰는지 남긴다 (Qt 없음).

## 왜 필요한가

이 도구를 어떻게 안착시킬지가 아직 미지수다 - 몇 명이 쓸지, 무엇을 보러
들어오는지, 스캔을 직접 돌리는 사람이 있는지. 그 답을 감으로 정하면 아무도
안 쓰는 기능에 공을 들이게 된다.

물어봐서 알 수 있는 것이 아니다. 사람은 자기가 무엇을 얼마나 쓰는지 잘
기억하지 못하고, 특히 "안 쓰는 것"은 화제에 오르지도 않는다. 그래서 쓰는
순간을 그때그때 남긴다.

## 무엇을 남기고 무엇을 안 남기는가

**굵직한 행동만** 남긴다 - 창을 열었다, 스캔을 손으로 돌렸다, 보고서를 봤다.
탭을 옮기거나 표를 정렬한 것까지 남기면 행 수만 불고, 정작 알고 싶은 "이
사람이 무엇을 하러 들어왔나"는 그 잡음에 묻힌다.

데이터 디렉터리가 NFS 라 쓰기 한 번이 왕복이다. 굵직한 것만 남기면 사람 하나가
하루에 몇 줄이므로 부담이 되지 않는다.

**내용은 남기지 않는다.** 검색을 했다는 사실은 남기지만 무엇을 검색했는지는
남기지 않는다. 어느 계정을 봤는지 정도는 운영에 필요해서 남기되, 그 이상
들여다볼 이유가 없다.

## 실패해도 조용히 넘어간다

기록은 부가 정보다. DB 가 잠겨 있거나 디렉터리가 안 보인다고 해서 사람이
하려던 일이 막히면 본말이 뒤집힌다. 여기서는 어떤 예외도 밖으로 내지 않는다.
"""

from __future__ import annotations

import getpass
import logging
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 남기는 행동들. 문자열을 직접 쓰지 않고 여기를 거치게 해서, 나중에 집계할 때
# 오타로 갈라진 항목이 생기지 않게 한다.
GUI_OPENED = "gui.open"
SCAN_STARTED = "scan.manual"        # 사람이 버튼으로 시작한 스캔
SCAN_STOPPED = "scan.stop"
REPORT_VIEWED = "report.open"
SEARCH_USED = "search.open"
ACCOUNTS_OPENED = "accounts.open"
ACCOUNT_CHANGED = "accounts.change"
TREE_VIEWED = "tree.open"
LOAD_VIEWED = "load.open"

# 기록을 얼마나 보관하는가.
#
# 행 하나가 수십 바이트고 하루에 몇 줄뿐이라 오래 둬도 부담이 없다. 오히려
# 짧게 두면 "작년보다 늘었나" 같은 것을 영영 못 본다.
DEFAULT_RETENTION_DAYS = 365


def current_user() -> str:
    """지금 이 프로그램을 돌리는 사용자.

    `getpass.getuser()` 는 환경변수를 먼저 보므로 cron 처럼 환경이 빈 곳에서
    실패할 수 있다. 그때는 uid 라도 남긴다 - 빈칸보다는 낫다."""

    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - 환경이 비었을 때
        try:
            return str(os.getuid())
        except AttributeError:   # Windows
            return "unknown"


def current_host() -> str:
    try:
        return socket.gethostname()
    except Exception:  # pragma: no cover - 방어적 처리
        return "unknown"


def record(
    data_dir: Path,
    action: str,
    detail: Optional[str] = None,
    user: Optional[str] = None,
) -> bool:
    """행동 하나를 남긴다. 남겼으면 True.

    **어떤 예외도 밖으로 내지 않는다.** 기록은 부가 정보라, 이것 때문에 사람이
    하려던 일이 막히면 본말이 뒤집힌다."""

    try:
        from . import scan_store

        conn = scan_store.connect(Path(data_dir))
        try:
            conn.execute(
                "INSERT INTO usage_events "
                "(happened_at, user_name, host_name, action, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    user or current_user(),
                    current_host(),
                    action,
                    detail,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception:
        logger.debug("사용 기록 실패 (%s)", action, exc_info=True)
        return False
