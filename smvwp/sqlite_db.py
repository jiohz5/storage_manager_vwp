"""SQLite 연결의 공통 규칙 (DB 셋이 같은 규칙을 따르게).

## 왜 한 곳에 모았나

표본(`store`)·스캔(`scan_store`)·검색 색인(`search_index`) DB 가 각자 연결
함수를 갖고 있었다. 앞의 둘은 거의 같은 코드를 복사해 쓰고 있어서 **같은
경합 버그를 두 곳에서 따로 고쳐야 했고**(스키마를 만들기 전에 "만들었다"
표시를 하던 것), 검색 색인은 그 사이 나온 개선을 하나도 받지 못해 연결할
때마다 스키마를 다시 만들고 커밋했다 - NFS 위에서 앞의 둘이 이미 없앤 바로
그 비용이다.

DB 마다 다른 것은 **파일 위치와 스키마**뿐이다. 나머지 규칙은 여기 한 곳에
둔다:

- 스키마 확인은 프로세스당 한 번 (아래 `_INITIALIZED` 참고)
- 만드는 동안 잠금을 쥔다
- 위치에 맞는 journal 모드 (로컬은 WAL, 네트워크 파일시스템은 DELETE)
- 나중에 추가된 열을 채운다 (`add_missing_columns`)
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Dict, Optional

from . import paths

logger = logging.getLogger(__name__)

# 이미 스키마를 확인한 DB 파일. **이 프로세스 안에서만** 유효하다.
#
# 연결은 원래 부를 때마다 `executescript(SCHEMA)` + 이행 + `commit()`을 했다.
# 로컬 디스크에서는 무시할 만한 비용이지만, 데이터 디렉터리가 NFS 에 있고 저널이
# DELETE 모드면 그 commit 하나가 **저널 파일 생성·fsync·삭제 왕복**이 된다.
# GUI 는 5초마다, 스캔 작업자들은 체크포인트마다 연결하므로 그 비용이 그대로
# 화면 멈춤과 스캔 지연으로 나타났다.
#
# 스키마는 멱등이라 프로세스당 한 번이면 충분하다. 프로세스가 여럿이어도 각자
# 한 번씩 하므로 여전히 안전하다.
_INITIALIZED = set()
_INIT_LOCK = threading.Lock()

# 다른 연결이 쓰기 잠금을 쥐고 있을 때 기다리는 시간(초).
CONNECT_TIMEOUT_SECONDS = 10


def connect(
    path: Path,
    data_dir: Path,
    initialize: Callable[[sqlite3.Connection], None],
) -> sqlite3.Connection:
    """DB 하나에 연결한다. 이 프로세스에서 처음이면 `initialize` 로 스키마를 만든다.

    **만드는 동안 잠금을 쥐고 있어야 한다.** 예전에는 표시만 먼저 해 두고
    잠금을 놓았는데, 그 사이에 들어온 다른 스레드는 "이미 됐다"고 보고 빈 DB 를
    그대로 받아 갔다 - 새 데이터 디렉터리에서 창을 처음 열면 작업 스레드 몇이
    동시에 들어오므로 실제로 `no such table` 이 났다.
    """

    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=CONNECT_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    key = str(path)
    try:
        with _INIT_LOCK:
            if key not in _INITIALIZED:
                apply_journal_mode(conn, data_dir)
                initialize(conn)
                conn.commit()
                _INITIALIZED.add(key)
    except BaseException:
        # 실패하면 "만들었다" 로 남기지 않는다 (다음 연결이 다시 해 본다).
        # 열어 둔 연결은 닫는다 - 윈도우에서는 열린 파일을 못 지운다.
        conn.close()
        raise
    return conn


def apply_journal_mode(conn: sqlite3.Connection, data_dir: Path) -> str:
    """이 위치에 안전한 journal 모드로 맞춘다. 최종 모드를 돌려준다.

    ## 왜 먼저 읽어 보는가

    `journal_mode`는 **DB 파일에 영구 저장되는 값**이다. 이미 원하는 모드면
    바꿀 이유가 없는데, 그냥 `PRAGMA journal_mode=...`를 쓰면 쓰기 잠금을
    잡으려 든다.

    ## 왜 예외를 삼키는가

    WAL에서 다른 모드로 바꾸려면 **다른 연결이 하나도 없어야** 한다. 있으면
    `database is locked`가 난다. 이 프로그램은 수집기(cron)·야간 스캔·GUI·
    알림기가 같은 DB를 건드리므로 그 상황이 일상이다.

    여기서 예외를 올리면 **연결 자체가 실패해 수집도 화면도 통째로 멈춘다.**
    실제로 그랬다 - GUI가 5초마다 연결하면서 매번 `timeout=10`만큼 기다렸다
    실패해 화면이 끊겼다. 모드를 못 바꾼 것은 다음 기회에 바꾸면 되는 일이고,
    지금 할 일은 있는 그대로라도 여는 것이다. 실제 모드는 진단이 보여 준다.
    """

    desired = paths.journal_mode_for(data_dir)
    try:
        current = conn.execute("PRAGMA journal_mode").fetchone()[0]
    except sqlite3.Error:  # pragma: no cover - 방어적 처리
        current = ""

    if str(current).lower() == desired.lower():
        return desired

    try:
        return conn.execute(f"PRAGMA journal_mode={desired}").fetchone()[0]
    except sqlite3.Error as exc:
        logger.warning(
            "journal 모드를 %s로 바꾸지 못했습니다 (%s). 지금은 %s로 씁니다 - "
            "데이터 디렉터리가 네트워크 파일시스템이면 다른 프로세스를 모두 "
            "멈춘 뒤 다시 열어야 바뀝니다.",
            desired, exc, current or "알 수 없음",
        )
        return str(current)


def journal_mode(path: Path) -> Optional[str]:
    """지금 DB가 실제로 쓰고 있는 journal 모드 (진단용). 못 읽으면 None."""

    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path), timeout=2)
    except sqlite3.Error:
        return None
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    except sqlite3.Error:  # pragma: no cover
        return None
    finally:
        conn.close()


def add_missing_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    """나중에 추가된 열을 채운다.

    `CREATE TABLE IF NOT EXISTS`는 이미 있는 표에 열을 더해 주지 않으므로, 기존
    데이터 디렉터리에서도 열리도록 따로 채운다. (데이터를 지우고 다시 만들게
    하면 그동안 쌓인 이력이 날아간다.)

    표와 열 이름은 코드에 박힌 상수만 받는다 - SQL 에 그대로 들어가기 때문이다.
    """

    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for column, sql_type in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
