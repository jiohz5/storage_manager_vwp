"""창이 여는 읽기마다 무엇을 얼마나 읽나 - 시간, SQL 문 수, 연결 수, 읽은 바이트.

저장소 루트에서 돌린다 (pytest 가 모으지 않는 수동 실험이다):
    python3 tests/read_cost.py <빈 디렉터리> [--accounts 20] [--days 90]

<빈 디렉터리> 에 시험 데이터를 만들고(계정마다 표본 N일 x 15분, 스캔 결과 2세대),
DESIGN.md "NFS 위에서 창이 느렸던 이유" 의 표와 같은 경로를 잰다. 그 디렉터리를
NFS 위에 두면 '초' 칸이 실기에 가까워진다. 만든 데이터는 지우지 않는다 - 다 보면
손으로 지운다.

칸의 뜻 (DESIGN.md 의 비용 모델):
  SQL문  - 자동 커밋이면 문 하나가 공유 잠금 잡고 놓기 한 벌 = NFS 왕복 여러 번
  연결   - open + journal 모드 확인 (스키마는 프로세스당 한 번)
  읽은MB - read() 로 받은 바이트. 캐시가 비었으면 그만큼이 네트워크로 온다 (최악)
"""

import argparse
import random
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smvwp import config as config_module, scan_store, sqlite_db, store  # noqa: E402


# -- 재는 도구 ---------------------------------------------------------------

def read_bytes() -> int:
    """이 프로세스가 지금까지 read() 로 받은 바이트."""

    if sys.platform.startswith("linux"):
        with open("/proc/self/io", encoding="ascii") as handle:
            fields = dict(line.split(":", 1) for line in handle)
        return int(fields["rchar"])
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "reads", "writes", "others", "read_bytes", "write_bytes", "other_bytes")]

        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessIoCounters.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters)]
        counters = Counters()
        kernel.GetProcessIoCounters(kernel.GetCurrentProcess(), ctypes.byref(counters))
        return counters.read_bytes
    return 0


COUNTS = {"statements": 0, "connections": 0}
_connect = sqlite3.connect


def _counting_connect(*args, **kwargs):
    conn = _connect(*args, **kwargs)
    COUNTS["connections"] += 1
    conn.set_trace_callback(lambda _sql: COUNTS.__setitem__("statements", COUNTS["statements"] + 1))
    return conn


sqlite_db.sqlite3.connect = _counting_connect
ROWS = []


@contextmanager
def measure(name):
    COUNTS["statements"] = COUNTS["connections"] = 0
    before, started = read_bytes(), time.perf_counter()
    yield
    ROWS.append((name, time.perf_counter() - started, COUNTS["statements"],
                 COUNTS["connections"], read_bytes() - before))


def report():
    print(f"{'경로':<38}{'초':>8}{'SQL문':>8}{'연결':>6}{'읽은MB':>10}")
    for name, seconds, statements, connections, read in ROWS:
        print(f"{name:<38}{seconds:>8.2f}{statements:>8}{connections:>6}{read / 1e6:>10.1f}")


# -- 시험 데이터 -------------------------------------------------------------

def build(root: Path, accounts: int, days: int):
    """계정 절반은 프로젝트, 절반은 그 백업. 디렉터리 수는 계정마다 2천~."""

    rnd = random.Random(1)
    data_dir = root / "data"
    config = config_module.load_config(data_dir)
    for i in range(accounts):
        (root / f"acct{i}").mkdir()
        kind = config_module.ACCOUNT_KIND_PROJECT if i % 2 == 0 else config_module.ACCOUNT_KIND_BACKUP
        config_module.add_account(config, f"acct{i}", str(root / f"acct{i}"),
                                  data_dir=data_dir, kind=kind)
    for i in range(0, accounts - 1, 2):
        config_module.set_backup_link(config, config.accounts[i].account_id,
                                      config.accounts[i + 1].account_id)
    config_module.save_config(data_dir, config)

    now = datetime.now(timezone.utc)
    with store.session(data_dir) as conn:
        conn.executemany(
            "INSERT INTO samples (account_id, collected_at, ok, filesystem, mount_point, total_kb,"
            " used_kb, avail_kb, byte_pct, byte_tier, inode_total, inode_used, inode_avail,"
            " inode_pct, inode_tier, overall_tier, quota_tier)"
            " VALUES (?, ?, 1, 'fs:/vol/x', '/user', ?, ?, ?, ?, 'normal', 10000000, 5000000,"
            " 5000000, 50.0, 'normal', 'normal', 'unknown')",
            [(account.account_id, (now - timedelta(minutes=15 * step)).isoformat(),
              10**9, 8 * 10**8 - step * 1000, 2 * 10**8 + step * 1000, 80.0 - step * 1e-4)
             for step in range(days * 96, -1, -1) for account in config.accounts],
        )
        conn.commit()

    stamp = now.isoformat()
    with scan_store.session(data_dir) as conn:
        for index, account in enumerate(config.accounts):
            dirs = 2000 + index * 1000
            tops = [f"{account.path}/top{t:03d}" for t in range(40)]
            for generation in (1, 2):
                rows = []
                for top in tops:
                    rows.append((account.account_id, generation, top, rnd.randint(10**6, 10**8), stamp, 0))
                    for d in range(dirs // len(tops)):
                        depth = 1 + d % 3
                        path = f"{top}/" + "/".join(f"d{d}_{k}" for k in range(depth))
                        rows.append((account.account_id, generation, path, rnd.randint(1, 10**6), stamp, depth))
                conn.executemany(
                    "INSERT OR REPLACE INTO baseline_results (account_id, generation, path, size_kb,"
                    " completed_at, depth) VALUES (?, ?, ?, ?, ?, ?)", rows)
                conn.executemany(
                    "INSERT INTO scan_checkpoints (account_id, kind, generation, path, depth, status,"
                    " size_kb, scanned_at) VALUES (?, 'baseline', ?, ?, 0, 'done', 1000, ?)",
                    [(account.account_id, generation, top, stamp) for top in tops])
                conn.executemany(
                    "INSERT INTO baseline_large_files (account_id, generation, path, size_kb,"
                    " recorded_at) VALUES (?, ?, ?, ?, ?)",
                    [(account.account_id, generation, f"{tops[0]}/big{n}.dat", 10**6 + n, stamp)
                     for n in range(50)])
            scan_store.mark_generation_completed(conn, account.account_id, 1, completed_at=stamp)
            scan_store.mark_generation_completed(conn, account.account_id, 2, completed_at=stamp)
        conn.execute("INSERT INTO scan_runs (run_id, triggered_by, started_at, ended_at, status)"
                     " VALUES ('r1', 'cron', ?, ?, 'completed')", (stamp, stamp))
        conn.commit()
    return data_dir, config_module.load_config(data_dir)


def fill_working_generation(data_dir):
    """스캔이 도는 중처럼 - 작업 세대(3)를 절반쯤 채운다."""

    with scan_store.session(data_dir) as conn:
        conn.execute(
            "INSERT INTO baseline_results SELECT account_id, 3, path, size_kb, completed_at, depth"
            " FROM baseline_results WHERE generation = 2 AND rowid % 2 = 0")
        conn.commit()


# -- 잰다 --------------------------------------------------------------------

def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path, help="시험 데이터를 만들 빈 디렉터리")
    parser.add_argument("--accounts", type=int, default=20)
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()
    root = args.directory
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        print(f"{root} 가 비어 있지 않습니다 - 빈 디렉터리를 주세요.", file=sys.stderr)
        return 2

    from smvwp import account_detail, dashboard, data_inventory, freshness, health, nightly_scan

    started = time.perf_counter()
    data_dir, config = build(root, args.accounts, args.days)
    print(f"만들기 {time.perf_counter() - started:.0f}초 - 표본 DB "
          f"{store.db_path(data_dir).stat().st_size / 1e6:.0f}MB, 스캔 DB "
          f"{scan_store.db_path(data_dir).stat().st_size / 1e6:.0f}MB\n")

    with measure("홈 첫 읽기 (밤 스캔이 대조를 남기기 전)"):
        dashboard.read_dashboard(data_dir, config, cache={})
    health.save_results(data_dir, config)   # 야간 스캔이 끝날 때 하는 일

    home, scan_tab = {}, {}
    with measure("홈 첫 읽기"):
        dashboard.read_dashboard(data_dir, config, cache=home)
    with measure("홈 다음 읽기 (15분마다)"):
        dashboard.read_dashboard(data_dir, config, cache=home)
    with measure("스캔 탭 첫 읽기"):
        nightly_scan.get_status_snapshot(data_dir, config, cache=scan_tab)
    with measure("스캔 탭 다음 읽기 (30초마다)"):
        nightly_scan.get_status_snapshot(data_dir, config, cache=scan_tab)
    with measure("수집 신선도 (창 열 때)"):
        freshness.evaluate_all(data_dir, config)
    with measure("계정 상세 창"):
        account_detail.read(data_dir, config, config.accounts[0].account_id)
    with measure("데이터 폴더 창"):
        data_inventory.build(data_dir, config)
    with measure("수집: 남이 방금 했나"):
        with store.session(data_dir) as conn:
            store.newest_collected_at(conn)
    with measure("수집: 표본 저장 + 오래된 것 정리"):
        now = datetime.now(timezone.utc).isoformat()
        with store.session(data_dir) as conn:
            store.insert_samples(conn, [
                store.SampleRecord(account_id=account.account_id, collected_at=now, ok=True)
                for account in config.accounts
            ])
            store.prune_old_samples(conn, config.settings.sample_retention_days)
    fill_working_generation(data_dir)
    nightly_scan.get_status_snapshot(data_dir, config, cache=scan_tab)
    with measure("스캔 중 다음 읽기 (5초마다)"):
        nightly_scan.get_status_snapshot(data_dir, config, cache=scan_tab)

    report()
    print(f"\n데이터는 {root} 에 남겼습니다 - 다 보면 손으로 지우세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
