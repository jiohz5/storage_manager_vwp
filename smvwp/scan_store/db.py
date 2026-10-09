"""연결과 스키마. 상수(세대 종류·체크포인트 상태)도 여기 둔다 - 모든 조각이 쓴다.

연결 규칙 자체(스키마는 프로세스당 한 번, 만드는 동안 잠금, journal 모드)는
`smvwp.sqlite_db` 에 있다. 여기는 이 DB 의 파일 위치와 스키마뿐이다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import sqlite_db


BASELINE = "baseline"

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_SPLIT = "split"
STATUS_ERROR = "error"



SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_runs (
    run_id TEXT PRIMARY KEY,
    triggered_by TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    generation INTEGER NOT NULL,
    parent_id INTEGER,
    path TEXT NOT NULL,
    depth INTEGER NOT NULL,
    status TEXT NOT NULL,
    size_kb INTEGER,
    changed_count INTEGER,   -- 없앤 활동 스캔이 쓰던 열. 기존 DB 호환 때문에 남긴다.
    error_message TEXT,
    scanned_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_lookup
    ON scan_checkpoints(account_id, kind, generation, status);

-- 계정별 "가장 큰 파일" 상위 몇 개.
--
-- 디렉터리 합계만으로는 "파일 하나가 비정상적으로 크다"를 볼 수 없다. 순회가
-- 어차피 모든 파일을 stat 하므로 지나가는 김에 붙잡아 둔다. 세대별로 남겨야
-- **지난 밤에 없던 파일**과 **갑자기 커진 파일**을 가릴 수 있다.
CREATE TABLE IF NOT EXISTS baseline_large_files (
    account_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    path TEXT NOT NULL,
    size_kb INTEGER NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (account_id, generation, path)
);
CREATE INDEX IF NOT EXISTS idx_large_files_gen
    ON baseline_large_files(account_id, generation, size_kb DESC);

CREATE TABLE IF NOT EXISTS baseline_results (
    account_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    path TEXT NOT NULL,
    size_kb INTEGER NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (account_id, generation, path)
);

-- 스캔이 도는 동안 일정 주기로 찍은 리소스 표본.
--
-- scan_runs에도 CPU 요약이 있지만 그것은 실행 하나당 한 줄이라 "밤새 평균
-- 이랬다"까지만 말한다. 정작 알고 싶은 것은 **언제 얼마나 튀었나**이고,
-- 그것은 시계열이 아니면 답할 수 없다. 행 하나가 작아(수십 바이트) 30초
-- 주기로 하룻밤을 채워도 1,000행 남짓이다.
--
-- phase가 'before'인 행은 스캔을 시작하기 전 기준값이다. 이 행이 있어야
-- "스캔 때문에" 튄 것인지 원래 그랬던 것인지 구분할 수 있다.
CREATE TABLE IF NOT EXISTS scan_load_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    sampled_at TEXT NOT NULL,
    phase TEXT NOT NULL,
    elapsed_seconds REAL,
    cpu_busy_percent REAL,
    cpu_iowait_percent REAL,
    cpu_scan_top_percent REAL,
    load_avg_1m REAL,
    memory_total_kb INTEGER,
    memory_available_kb INTEGER,
    memory_used_percent REAL,
    active_accounts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_load_samples_run
    ON scan_load_samples(run_id, sampled_at);

-- 서버 전체의 부하 이력 (스캔과 무관하게 상시).
--
-- `scan_load_samples` 는 **스캔이 도는 동안만** 남는다. 그것만으로는 정작
-- 물어야 할 것에 답할 수 없다 - "이 서버는 평소에 어떤 모습인가."
-- 비교 대상이 없으면 "스캔 중 load 8" 이 높은 것인지 원래 그런 것인지 모른다.
-- 낮에 상세 스캔을 돌려도 되는지 같은 판단은 특히 이 바탕이 없으면 감이 된다.
--
-- 수집기가 15분마다 어차피 돌므로 그때 한 벌씩 뜬다. 스캔 중에는 같은 표에
-- 30초 간격으로 더 촘촘히 들어간다(`source`, `run_id` 로 구분).
CREATE TABLE IF NOT EXISTS server_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sampled_at TEXT NOT NULL,
    source TEXT NOT NULL,
    run_id TEXT,
    interval_seconds REAL,
    cpu_count INTEGER,
    cpu_busy_percent REAL,
    cpu_iowait_percent REAL,
    cpu_steal_percent REAL,
    load_avg_1m REAL,
    load_avg_5m REAL,
    load_avg_15m REAL,
    procs_running INTEGER,
    procs_blocked INTEGER,
    blocked_ours INTEGER,
    blocked_others INTEGER,
    memory_total_kb INTEGER,
    memory_available_kb INTEGER,
    memory_used_percent REAL,
    swap_used_kb INTEGER,
    scan_cpu_percent REAL,
    scan_rss_kb INTEGER,
    scan_process_count INTEGER,
    nfs_ops_per_second REAL,
    nfs_read_bytes_per_second REAL
);
CREATE INDEX IF NOT EXISTS idx_server_samples_time
    ON server_samples(sampled_at);
CREATE INDEX IF NOT EXISTS idx_server_samples_run
    ON server_samples(run_id, sampled_at);

-- 그 시각에 서버에서 무엇이 돌고 있었나.
--
-- 부하 숫자만 남기면 "그래서 누가 그랬나"에 답할 수 없다. 상위 몇 개만
-- 남긴다 - 프로세스가 수백 개여도 부하를 만든 것은 늘 몇 개이고, 나머지
-- 대부분은 CPU 0.0% 의 잠든 데몬이다.
--
-- `cmdline` 에는 **다른 사용자의 작업 내용이 그대로 남는다.** 어떤 작업이
-- 서버를 쓰고 있었는지 알려면 이름만으로는 부족해서 기본으로 남기되,
-- 설정으로 끌 수 있다 (`record_process_cmdline`).
CREATE TABLE IF NOT EXISTS server_sample_processes (
    sample_id INTEGER NOT NULL,
    pid INTEGER NOT NULL,
    comm TEXT NOT NULL,
    user_name TEXT,
    cpu_percent REAL,
    rss_kb INTEGER,
    state TEXT,
    threads INTEGER,
    is_ours INTEGER NOT NULL DEFAULT 0,
    blkio_percent REAL,
    cmdline TEXT,
    PRIMARY KEY (sample_id, pid)
);

-- NFS 마운트마다 그 구간에 실제로 오간 것.
--
-- 상세 스캔은 CPU 를 거의 안 쓰고 메타데이터 조회를 만든다. CPU 만 남기면
-- "부하가 없었다"는 잘못된 결론이 나온다.
--
-- `avg_queue_ms` 를 `avg_rtt_ms` 와 따로 두는 것이 핵심이다. 왕복이 느린 것
-- (파일서버가 밀림)과 보내지도 못하고 기다린 것(우리 쪽 슬롯 부족)은 할 일이
-- 정반대다 - 뒤쪽인데 병렬을 줄이면 정확히 반대 처방이 된다.
CREATE TABLE IF NOT EXISTS server_sample_mounts (
    sample_id INTEGER NOT NULL,
    mount_point TEXT NOT NULL,
    device TEXT,
    nfs_version TEXT,
    ops INTEGER,
    ops_per_second REAL,
    read_bytes INTEGER,
    write_bytes INTEGER,
    avg_rtt_ms REAL,
    avg_queue_ms REAL,
    queue_share REAL,
    getattr_ops INTEGER,
    lookup_ops INTEGER,
    access_ops INTEGER,
    readdir_ops INTEGER,
    read_ops INTEGER,
    write_ops INTEGER,
    bad_xids INTEGER,
    max_slots INTEGER,
    PRIMARY KEY (sample_id, mount_point)
);

-- 누가 이 도구를 어떻게 쓰는지.
--
-- 몇 명이 쓸지, 무엇을 보러 들어오는지, 스캔을 직접 돌리는 사람이 있는지가
-- 아직 미지수다. 물어봐서 알 수 있는 것이 아니라(사람은 자기가 무엇을 얼마나
-- 쓰는지 잘 기억하지 못한다) 쓰는 순간을 그때그때 남긴다.
--
-- **굵직한 행동만** 남긴다. 탭을 옮긴 것까지 남기면 행 수만 불고 정작 알고
-- 싶은 것이 잡음에 묻힌다. 검색을 했다는 사실은 남기되 무엇을 검색했는지는
-- 남기지 않는다.
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    happened_at TEXT NOT NULL,
    user_name TEXT,
    host_name TEXT,
    action TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_events_time
    ON usage_events(happened_at);

CREATE TABLE IF NOT EXISTS account_scan_state (
    account_id TEXT PRIMARY KEY,
    last_completed_generation INTEGER,
    last_baseline_completed_at TEXT,
    unavailable_at TEXT,
    unavailable_reason TEXT,
    unavailable_detail TEXT
);
"""


def db_path(data_dir: Path) -> Path:
    return data_dir / "detail_scan.db"


# 나중에 추가된 scan_runs 열. `CREATE TABLE IF NOT EXISTS`는 이미 있는 표에
# 열을 더해 주지 않으므로, 기존 데이터 디렉터리에서도 열리도록 따로 채운다.
# (데이터를 지우고 다시 만들게 하면 그동안 쌓인 스캔 이력이 날아간다.)
_COLUMNS_ADDED = {
    "scan_runs": {
        "cpu_system_percent_avg": "REAL",
        "cpu_system_percent_peak": "REAL",
        "cpu_top_percent_avg": "REAL",
        "cpu_top_percent_peak": "REAL",
        "load_avg_1m": "REAL",
        "cpu_count": "INTEGER",
        "rss_peak_kb": "INTEGER",
        "memory_total_kb": "INTEGER",
        "memory_peak_percent": "REAL",
        # 지금 어느 경로를 훑고 있는지. **체크포인트에 'running' 상태를 새로
        # 만들지 않은 것은 의도다** - 재개 가능성이 "pending만 보면 된다"는
        # 단순한 불변식에 기대고 있는데(1부 7절), 중간 상태를 넣으면 프로세스가
        # 죽었을 때 그 행을 되돌리는 복구 로직이 따로 필요해진다. 실행 행에
        # 적어 두면 그런 위험 없이 표시용 정보만 얻는다.
        "current_path": "TEXT",
        "current_kind": "TEXT",
        "current_account_id": "TEXT",
        "current_started_at": "TEXT",
        # 이 실행이 계정을 몇 개씩 동시에 돌렸는가. 리소스 시계열을 나중에
        # 비교할 때 **가장 먼저 알아야 하는 조건**이라 실행 행에 박아 둔다
        # (설정값은 그 사이 바뀔 수 있어 나중에 config를 봐도 소용없다).
        "parallel_accounts": "INTEGER",
        # 이 밤이 주말 밤이었는가 (끝나는 아침이 토/일).
        #
        # 나중에 started_at으로 다시 계산하면 될 것 같지만 그러면 안 된다 -
        # 시간창 설정(detail_scan_window_end_hour)이 그 사이 바뀌면 같은 밤이
        # 다르게 판정된다. 판정한 시점의 사실을 그대로 박아 둔다.
        "weekend_night": "INTEGER",
    },
    "baseline_results": {
        # `du -k` 한 번이 서브트리 전체를 주므로 부모와 자식이 함께 들어온다.
        # 평평한 목록(증가 경로)은 얕은 것만 보여야 읽히고, 트리 화면은 전부
        # 필요하다 - 깊이를 저장해 두 용도를 한 표로 감당한다.
        "depth": "INTEGER",
    },
    "account_scan_state": {
        # 계정 목록에 "최근 스캔일"을 보여주려면 기준선을 언제 완주했는지가
        # 필요하다. 기존에는 세대 번호만 남기고 시각은 안 남겼다.
        "last_baseline_completed_at": "TEXT",
        # 계정 경로를 못 봐 그 밤을 건너뛴 때와 까닭 (`nightly_scan._unavailable_reason`).
        # 창은 cron 로그를 볼 수 없어서, 여기 남겨야 화면과 보고서가 알릴 수 있다.
        # 경로가 다시 보이면 지운다.
        "unavailable_at": "TEXT",
        "unavailable_reason": "TEXT",
        "unavailable_detail": "TEXT",
    },
}


def _initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # 걷어 낸 표. **밤마다 모든 경로의 증감을 기록하고 60세대를 보관**했는데
    # (경로 5만 개면 300만 줄) 읽는 화면이 없었다 - NFS 위에서 쓰기만 하는
    # 비용이었다. 기존 DB 에서는 한 번 지우고, 그 뒤로는 아무 일도 하지 않는다.
    conn.execute("DROP TABLE IF EXISTS growth_history")
    for table, columns in _COLUMNS_ADDED.items():
        sqlite_db.add_missing_columns(conn, table, columns)



def journal_mode(data_dir: Path) -> Optional[str]:
    """지금 DB가 실제로 쓰고 있는 journal 모드 (진단용). 못 읽으면 None."""

    return sqlite_db.journal_mode(db_path(data_dir))


def connect(data_dir: Path) -> sqlite3.Connection:
    """연결 규칙(스키마는 프로세스당 한 번, journal 모드)은 `sqlite_db` 에 있다."""

    return sqlite_db.connect(db_path(data_dir), data_dir, _initialize)


def session(data_dir: Path):
    """`with scan_store.session(data_dir) as conn:` - 블록이 끝나면 연결을 닫는다."""

    return sqlite_db.session(connect, data_dir)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
