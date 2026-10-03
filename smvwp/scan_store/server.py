"""서버 부하 표본과 사용 기록 (`server_samples` 와 딸린 표, `usage_events`).

15분 상시 수집과 스캔 중 촘촘한 표본이 같은 표에 출처(`source`)를 달고 쌓인다.
누가 이 도구를 어떻게 쓰는지도 여기 남긴다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Optional


# ---------------------------------------------------------------------------
# 서버 전체 부하 이력
# ---------------------------------------------------------------------------

SOURCE_COLLECTOR = "collector"   # 15분 상시 수집
SOURCE_SCAN = "scan"             # 스캔이 도는 동안 촘촘히

_SAMPLE_COLUMNS = (
    "sampled_at", "source", "run_id", "interval_seconds", "cpu_count",
    "cpu_busy_percent", "cpu_iowait_percent", "cpu_steal_percent",
    "load_avg_1m", "load_avg_5m", "load_avg_15m",
    "procs_running", "procs_blocked", "blocked_ours", "blocked_others",
    "memory_total_kb", "memory_available_kb", "memory_used_percent",
    "swap_used_kb", "scan_cpu_percent", "scan_rss_kb", "scan_process_count",
    "nfs_ops_per_second", "nfs_read_bytes_per_second",
)


def _op_count(mount, name: str) -> int:
    entry = mount.ops.get(name)
    return entry.ops if entry is not None else 0


def save_server_sample(
    conn: sqlite3.Connection,
    sample,
    source: str = SOURCE_COLLECTOR,
    run_id: Optional[str] = None,
) -> Optional[int]:
    """`servermon.ServerSample` 한 벌을 저장한다. 표본 id를 돌려준다.

    아무것도 못 잰 표본은 저장하지 않는다 - `/proc` 이 없는 환경에서 빈 행만
    쌓이면 나중에 "그 시각엔 한가했다"로 잘못 읽힌다."""

    if sample is None or not sample.measured:
        return None

    values = (
        sample.sampled_at,
        source,
        run_id,
        sample.interval_seconds,
        sample.cpu_count,
        sample.cpu_busy_percent,
        sample.cpu_iowait_percent,
        sample.cpu_steal_percent,
        sample.load_avg_1m,
        sample.load_avg_5m,
        sample.load_avg_15m,
        sample.procs_running,
        sample.procs_blocked,
        sample.blocked_ours,
        sample.blocked_others,
        sample.memory_total_kb,
        sample.memory_available_kb,
        sample.memory_used_percent,
        sample.swap_used_kb,
        sample.scan_cpu_percent,
        sample.scan_rss_kb,
        sample.scan_process_count,
        sample.nfs_ops_per_second,
        sample.nfs_read_bytes_per_second,
    )
    placeholders = ", ".join("?" for _ in _SAMPLE_COLUMNS)
    columns = ", ".join(_SAMPLE_COLUMNS)
    cursor = conn.execute(
        "INSERT INTO server_samples (" + columns + ") VALUES (" + placeholders + ")",
        values,
    )
    sample_id = cursor.lastrowid

    if sample.processes:
        conn.executemany(
            "INSERT OR REPLACE INTO server_sample_processes ("
            "sample_id, pid, comm, user_name, cpu_percent, rss_kb, state, "
            "threads, is_ours, blkio_percent, cmdline"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    sample_id, item.pid, item.comm, item.user, item.cpu_percent,
                    item.rss_kb, item.state, item.threads,
                    1 if item.is_ours else 0, item.blkio_percent, item.cmdline,
                )
                for item in sample.processes
            ],
        )
    if sample.mounts:
        conn.executemany(
            "INSERT OR REPLACE INTO server_sample_mounts ("
            "sample_id, mount_point, device, nfs_version, ops, ops_per_second, "
            "read_bytes, write_bytes, avg_rtt_ms, avg_queue_ms, queue_share, "
            "getattr_ops, lookup_ops, access_ops, readdir_ops, read_ops, "
            "write_ops, bad_xids, max_slots"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    sample_id, mount.mount_point, mount.device, mount.nfs_version,
                    mount.total_ops, mount.ops_per_second,
                    mount.read_bytes, mount.write_bytes,
                    mount.avg_rtt_ms, mount.avg_queue_ms, mount.queue_share,
                    _op_count(mount, "GETATTR"), _op_count(mount, "LOOKUP"),
                    _op_count(mount, "ACCESS"),
                    _op_count(mount, "READDIR") + _op_count(mount, "READDIRPLUS"),
                    _op_count(mount, "READ"), _op_count(mount, "WRITE"),
                    mount.bad_xids, mount.max_slots,
                )
                for mount in sample.mounts
            ],
        )
    conn.commit()
    return sample_id


def _time_clauses(since, until, source=None, run_id=None, prefix=""):
    """공통 시간 범위 조건. `prefix` 는 조인할 때의 표 별칭(`s.`)."""

    clauses, params = [], []
    if since:
        clauses.append(prefix + "sampled_at >= ?")
        params.append(since)
    if until:
        clauses.append(prefix + "sampled_at < ?")
        params.append(until)
    if source:
        clauses.append(prefix + "source = ?")
        params.append(source)
    if run_id:
        clauses.append(prefix + "run_id = ?")
        params.append(run_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def server_samples(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
    until: Optional[str] = None,
    source: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = 2000,
) -> List[sqlite3.Row]:
    """시간 범위 안의 서버 표본 (시간순)."""

    where, params = _time_clauses(since, until, source, run_id)
    params.append(limit)
    return conn.execute(
        "SELECT * FROM server_samples " + where + " ORDER BY sampled_at LIMIT ?",
        params,
    ).fetchall()


BUSIEST_BY_CPU = "cpu"
BUSIEST_BY_MEMORY = "mem"


def busiest_processes(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
    until: Optional[str] = None,
    by: str = BUSIEST_BY_CPU,
    limit: int = 20,
    run_id: Optional[str] = None,
) -> List[sqlite3.Row]:
    """이 시간대에 서버를 쓴 작업들 - 사용자와 이름으로 묶어서.

    pid 로 묶지 않는 이유: 같은 작업이 죽었다 살아나면 pid 가 바뀌어 따로
    세어진다. 사람이 알고 싶은 것은 "누구의 무슨 작업"이지 번호가 아니다.

    **`cpu_avg` 는 상위 목록에 들었던 표본들만의 평균이다.** 한가할 때는
    목록에 못 들어 빠지므로 하루 종일의 평균보다 높게 나온다. 이 값을 "이
    작업이 평소 쓰는 양"으로 읽으면 안 된다 - `samples`(몇 번이나 상위에
    들었나)를 같이 주는 것도 그래서다."""

    where, params = _time_clauses(since, until, run_id=run_id, prefix="s.")
    order = "rss_peak DESC" if by == BUSIEST_BY_MEMORY else "cpu_peak DESC"
    params.append(limit)
    return conn.execute(
        "SELECT p.user_name AS user_name, p.comm AS comm, p.is_ours AS is_ours, "
        "COUNT(*) AS samples, AVG(p.cpu_percent) AS cpu_avg, "
        "MAX(p.cpu_percent) AS cpu_peak, MAX(p.rss_kb) AS rss_peak, "
        "MAX(p.cmdline) AS cmdline "
        "FROM server_sample_processes p "
        "JOIN server_samples s ON s.id = p.sample_id " + where + " "
        "GROUP BY p.user_name, p.comm, p.is_ours "
        "ORDER BY " + order + " LIMIT ?",
        params,
    ).fetchall()


def mount_activity(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
    until: Optional[str] = None,
    run_id: Optional[str] = None,
) -> List[sqlite3.Row]:
    """마운트별 요약 - 어디에 얼마나 실었고 얼마나 느렸나."""

    where, params = _time_clauses(since, until, run_id=run_id, prefix="s.")
    return conn.execute(
        "SELECT m.mount_point AS mount_point, m.device AS device, "
        "COUNT(*) AS samples, SUM(m.ops) AS total_ops, "
        "AVG(m.ops_per_second) AS ops_avg, MAX(m.ops_per_second) AS ops_peak, "
        "SUM(m.read_bytes) AS read_bytes, "
        "AVG(m.avg_rtt_ms) AS rtt_avg, MAX(m.avg_rtt_ms) AS rtt_peak, "
        "AVG(m.avg_queue_ms) AS queue_avg, "
        "MAX(m.queue_share) AS queue_share_peak "
        "FROM server_sample_mounts m "
        "JOIN server_samples s ON s.id = m.sample_id " + where + " "
        "GROUP BY m.mount_point, m.device ORDER BY total_ops DESC",
        params,
    ).fetchall()


def prune_server_samples(
    conn: sqlite3.Connection, retention_days: int, source: Optional[str] = None
) -> int:
    """보존 기간이 지난 서버 표본과 그 자식 행들을 지운다.

    `source` 로 나눠 지울 수 있다. 스캔 중 표본은 촘촘해서(2분 간격) 금방
    쌓이지만 "그날 밤 어디서 튀었나"를 보는 것이라 지나면 값이 줄고, 상시
    표본은 성기지만 **평소 이 서버가 어떤 모습인가**를 쌓는 것이라 길수록
    낫다. 같은 기간으로 묶으면 한쪽이 반드시 손해를 본다."""

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    scope = " AND source = ?" if source else ""
    extra = (source,) if source else ()
    # 자식을 먼저 지운다. 부모를 먼저 지우면 어느 자식이 고아인지 알 방법이
    # 없어진다 (SQLite 의 외래키 연쇄 삭제는 기본으로 꺼져 있다).
    conn.execute(
        "DELETE FROM server_sample_processes WHERE sample_id IN "
        "(SELECT id FROM server_samples WHERE sampled_at < ?" + scope + ")",
        (cutoff,) + extra,
    )
    conn.execute(
        "DELETE FROM server_sample_mounts WHERE sample_id IN "
        "(SELECT id FROM server_samples WHERE sampled_at < ?" + scope + ")",
        (cutoff,) + extra,
    )
    cursor = conn.execute(
        "DELETE FROM server_samples WHERE sampled_at < ?" + scope, (cutoff,) + extra
    )
    conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# 사용 기록
# ---------------------------------------------------------------------------


def usage_events(
    conn: sqlite3.Connection,
    since: Optional[str] = None,
    limit: int = 500,
) -> List[sqlite3.Row]:
    """최근 사용 기록 (최신순)."""

    where, params = ("WHERE happened_at >= ?", [since]) if since else ("", [])
    params.append(limit)
    return conn.execute(
        "SELECT * FROM usage_events " + where + " ORDER BY happened_at DESC LIMIT ?",
        params,
    ).fetchall()


def _local_offset_modifier() -> str:
    """저장된 UTC 시각을 지역시간으로 옮기는 SQLite 수정자 (`'+9 hours'`).

    날짜별로 묶을 때 필요하다. UTC 날짜로 묶으면 한국시간 기준 새벽 0~9시에
    한 일이 **전날 것으로 세어진다** - 아침 일찍 쓰는 사람의 '연속으로 쓴 날'이
    실제보다 적게 나온다.

    고정 오프셋이라 서머타임이 있는 지역에서는 경계에서 어긋난다. 한국은
    서머타임이 없어 문제가 없고, 이 값은 '며칠 썼나'를 세는 데만 쓰이므로
    하루 어긋나도 해석이 무너지지는 않는다."""

    offset = datetime.now().astimezone().utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset else 0
    return f"{minutes:+d} minutes"


def usage_by_user(
    conn: sqlite3.Connection, since: Optional[str] = None
) -> List[sqlite3.Row]:
    """사람별 요약 - 몇 번, 며칠, 언제부터 언제까지.

    **며칠 썼는가(`days`)를 횟수와 따로 센다.** 하루에 창을 열 번 연 사람과
    열흘 동안 매일 한 번씩 연 사람은 횟수가 같아도 전혀 다른 이야기다 -
    뒤쪽만이 이 도구가 일과에 들어갔다는 뜻이다.

    날짜는 **지역시간 기준**으로 센다 (`_local_offset_modifier` 참고)."""

    where, params = ("WHERE happened_at >= ?", [since]) if since else ("", [])
    params = list(params)
    params.insert(0, _local_offset_modifier())
    return conn.execute(
        "SELECT user_name, COUNT(*) AS events, "
        "COUNT(DISTINCT date(happened_at, ?)) AS days, "
        "MIN(happened_at) AS first_seen, MAX(happened_at) AS last_seen "
        "FROM usage_events " + where + " "
        "GROUP BY user_name ORDER BY events DESC",
        params,
    ).fetchall()


def usage_by_action(
    conn: sqlite3.Connection, since: Optional[str] = None
) -> List[sqlite3.Row]:
    """무엇을 하러 들어오는가.

    쓰는 사람 수(`users`)를 함께 센다 - 한 사람이 백 번 쓴 기능과 열 사람이
    열 번씩 쓴 기능은 같은 숫자라도 뜻이 다르다."""

    where, params = ("WHERE happened_at >= ?", [since]) if since else ("", [])
    return conn.execute(
        "SELECT action, COUNT(*) AS events, "
        "COUNT(DISTINCT user_name) AS users, MAX(happened_at) AS last_seen "
        "FROM usage_events " + where + " "
        "GROUP BY action ORDER BY events DESC",
        params,
    ).fetchall()


def prune_usage_events(conn: sqlite3.Connection, retention_days: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    cursor = conn.execute("DELETE FROM usage_events WHERE happened_at < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount
