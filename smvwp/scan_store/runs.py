"""실행 이력 (`scan_runs`) 과 스캔 중 리소스 시계열 (`scan_load_samples`).

한 밤의 실행 하나가 행 하나다. 언제 돌았고, 어떻게 끝났고, 그동안 이 장비를
얼마나 썼는지.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from .db import utc_now_iso


# -- 실행 이력 -----------------------------------------------------------

def start_run(conn: sqlite3.Connection, run_id: str, triggered_by: str) -> None:
    conn.execute(
        "INSERT INTO scan_runs (run_id, triggered_by, started_at, status) VALUES (?, ?, ?, 'running')",
        (run_id, triggered_by, utc_now_iso()),
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: str, status: str, load=None) -> None:
    """실행을 마감한다. `load`(loadstat.Summary)를 주면 CPU 점유도 함께 남긴다.

    점유율을 기록해 두는 이유: 상세 스캔이 얼마나 무거운 작업인지는 계정 크기·
    파일 수·파일시스템에 따라 달라서 **미리 예측할 수 없다.** 대신 실제로 돈
    기록이 쌓이면 "이 환경에서는 이 정도"라고 말할 수 있게 된다."""

    if load is None or not load.samples:
        conn.execute(
            "UPDATE scan_runs SET ended_at = ?, status = ? WHERE run_id = ?",
            (utc_now_iso(), status, run_id),
        )
    else:
        conn.execute(
            "UPDATE scan_runs SET ended_at = ?, status = ?, "
            "cpu_system_percent_avg = ?, cpu_system_percent_peak = ?, "
            "cpu_top_percent_avg = ?, cpu_top_percent_peak = ?, "
            "load_avg_1m = ?, cpu_count = ?, "
            "rss_peak_kb = ?, memory_total_kb = ?, memory_peak_percent = ? "
            "WHERE run_id = ?",
            (
                utc_now_iso(),
                status,
                load.system_percent_avg,
                load.system_percent_peak,
                load.top_percent_avg,
                load.top_percent_peak,
                load.load_avg_1m,
                load.cpu_count,
                load.rss_peak_kb,
                load.memory_total_kb,
                load.memory_peak_percent,
                run_id,
            ),
        )
    conn.commit()


def set_current_target(
    conn: sqlite3.Connection,
    run_id: str,
    account_id: Optional[str],
    kind: Optional[str],
    path: Optional[str],
) -> None:
    """지금 훑고 있는 경로를 실행 행에 적는다 (표시 전용).

    `du` 하나가 몇 분씩 걸릴 수 있어서, 그동안 화면에 "실행 중"만 떠 있으면
    멈춘 것인지 진행 중인지 구분할 수 없다. 어디를 보고 있는지가 보이면
    그 자체로 진행의 증거가 된다."""

    conn.execute(
        "UPDATE scan_runs SET current_account_id = ?, current_kind = ?, "
        "current_path = ?, current_started_at = ? WHERE run_id = ?",
        (account_id, kind, path, utc_now_iso() if path else None, run_id),
    )
    conn.commit()


def latest_run(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()


# -- 리소스 시계열 ---------------------------------------------------------

def record_parallelism(
    conn: sqlite3.Connection,
    run_id: str,
    parallel_accounts: int,
    weekend_night: bool = False,
) -> None:
    """이 실행의 병렬도와 밤 성격을 남긴다.

    시계열을 나중에 해석하려면 둘 다 필요하다. 숫자만 있으면 "동시 3개"가
    주말이라 그런 것인지 사람이 실험하려고 지정한 것인지 구분되지 않는다."""

    conn.execute(
        "UPDATE scan_runs SET parallel_accounts = ?, weekend_night = ? WHERE run_id = ?",
        (int(parallel_accounts), 1 if weekend_night else 0, run_id),
    )
    conn.commit()


def last_runs(conn: sqlite3.Connection, limit: int = 5) -> List[sqlite3.Row]:
    """가장 최근 실행 몇 개 (최신순).

    `recent_runs` 와 달리 **시각으로 거르지 않는다.** 보고서는 `now` 를 인자로
    받아 과거 날짜로도 다시 만들 수 있는데, 벽시계로 거르면 그때 아무것도 안
    잡힌다. "며칠 안"이 아니라 "몇 개"가 필요한 자리에 쓴다.
    """

    return conn.execute(
        "SELECT * FROM scan_runs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()


def recent_runs(conn: sqlite3.Connection, days: int = 30) -> List[sqlite3.Row]:
    """최근 N일 안에 시작한 실행들 (최신순).

    리소스 표본(`scan_load_samples`)의 보존 기간과 맞춰 쓰는 것이 전제다 -
    표본이 지워진 실행은 행만 남고 비교에 쓸 숫자가 없다."""

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return conn.execute(
        "SELECT * FROM scan_runs WHERE started_at >= ? ORDER BY started_at DESC",
        (cutoff,),
    ).fetchall()


def save_load_samples(conn: sqlite3.Connection, run_id: str, samples) -> int:
    """`loadstat.Recorder`가 모은 표본을 한 번에 저장한다.

    한 건씩 commit하지 않고 모아서 넣는 이유: 표본 저장은 스캔의 부산물이라
    실패해도 스캔 결과를 뒤집으면 안 되고, 쓰기 횟수도 최소여야 한다
    (계정 병렬 실행 중이면 같은 DB에 쓰기 잠금을 다투게 된다)."""

    rows = []
    for sample in samples:
        # 워밍업 표본은 델타 기준점을 잡으려고 버리는 것이라 저장하지 않는다.
        if getattr(sample, "phase", "") == "warmup":
            continue
        rows.append(
            (
                run_id,
                sample.sampled_at,
                sample.phase,
                sample.elapsed_seconds,
                sample.cpu_busy_percent,
                sample.cpu_iowait_percent,
                sample.cpu_scan_top_percent,
                sample.load_avg_1m,
                sample.memory_total_kb,
                sample.memory_available_kb,
                sample.memory_used_percent,
                sample.active_accounts,
            )
        )
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO scan_load_samples ("
        "run_id, sampled_at, phase, elapsed_seconds, cpu_busy_percent, "
        "cpu_iowait_percent, cpu_scan_top_percent, load_avg_1m, "
        "memory_total_kb, memory_available_kb, memory_used_percent, active_accounts"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def load_samples(conn: sqlite3.Connection, run_id: str) -> List[sqlite3.Row]:
    """한 실행의 리소스 표본 (시간순)."""

    return conn.execute(
        "SELECT * FROM scan_load_samples WHERE run_id = ? ORDER BY sampled_at",
        (run_id,),
    ).fetchall()


def prune_load_samples(conn: sqlite3.Connection, retention_days: int) -> int:
    """보존 기간이 지난 리소스 표본을 지운다 (자체 비대화 방지)."""

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    cursor = conn.execute("DELETE FROM scan_load_samples WHERE sampled_at < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount
