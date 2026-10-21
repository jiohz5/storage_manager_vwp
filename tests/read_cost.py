"""창이 여는 읽기마다 무엇을 얼마나 읽나 - 시간, SQL 문 수, 연결 수, 읽은 바이트, NFS 요청.

저장소 루트에서 돌린다 (pytest 가 모으지 않는 수동 실험이다):
    python3 tests/read_cost.py <빈 디렉터리> [--accounts 20] [--days 90] [--cold] [--code <저장소>]

<빈 디렉터리> 에 시험 데이터를 만들고(계정마다 표본 N일 x 15분, 스캔 결과 2세대),
DESIGN.md "NFS 위에서 창이 느렸던 이유" 의 표와 같은 경로를 잰다. 그 디렉터리를
NFS 위에 두면 '초' 칸이 실기에 가까워진다. 만든 데이터는 지우지 않는다 - 다 보면
손으로 지운다.

  --cold   경로마다 재기 전에 DB 파일을 이 장비의 페이지 캐시에서 내린다 (리눅스).
           데이터를 방금 만든 프로세스라 그냥 재면 캐시가 데워진 채로 잰다 - 실기는
           다른 장비(cron, 스캔)가 파일을 바꿔 캐시가 자주 버려진다.
  --code   잴 코드가 있는 저장소 (기본: 이 파일이 든 저장소). 커밋 앞뒤를 견줄 때
           `git worktree add` 로 받은 디렉터리를 준다. 이 파일 하나로 예전 코드도 잰다.

칸의 뜻 (DESIGN.md 의 비용 모델):
  SQL문    자동 커밋이면 문 하나가 공유 잠금 잡고 놓기 한 벌
  연결     open + journal 모드 확인 (스키마는 프로세스당 한 번)
  읽은MB   read() 로 받은 바이트. 캐시가 비었으면 그만큼이 네트워크로 온다
  NFS요청  데이터 디렉터리 마운트의 NFS 연산 수 (/proc/self/mountstats, 마운트 전체 값이라
           조용한 장비에서 잰다). 잠금은 NFSv4 면 LOCK/LOCKU 로 여기 잡히고, NFSv3 면
           lockd 로 따로 가서 안 잡힌다 - 머리말의 NFS 버전을 함께 본다.
"""

import argparse
import os
import platform
import random
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 아래 모듈들은 `--code` 를 읽은 뒤에 불러온다 (`_load`).
config_module = scan_store = sqlite_db = store = None


def _load(code: Path) -> None:
    global config_module, scan_store, sqlite_db, store
    sys.path.insert(0, str(code))
    from smvwp import config as config_module, scan_store, sqlite_db, store  # noqa: F811

    sqlite_db.sqlite3.connect = _counting_connect


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


def _mount_of(path: Path):
    """`(마운트 지점, 종류, 옵션)` - 이 경로를 담은 가장 긴 마운트. 모르면 None."""

    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    target = os.path.realpath(str(path))
    best = None
    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        point = parts[1].replace("\\040", " ")
        if target == point or target.startswith(point.rstrip("/") + "/"):
            if best is None or len(point) >= len(best[0]):
                best = (point, parts[2], parts[3])
    return best


def nfs_ops(mount_point) -> dict:
    """그 마운트의 NFS 연산별 누적 수 `{연산: 수}`. NFS 가 아니거나 못 읽으면 {}."""

    if not mount_point:
        return {}
    try:
        text = Path("/proc/self/mountstats").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    ops, inside, in_ops = {}, False, False
    for line in text.splitlines():
        if line.startswith("device "):
            parts = line.split()
            inside = len(parts) > 4 and parts[4] == mount_point
            in_ops = False
            continue
        if not inside:
            continue
        stripped = line.strip()
        if stripped.startswith("per-op statistics"):
            in_ops = True
            continue
        if in_ops and ":" in stripped:
            name, _, rest = stripped.partition(":")
            values = rest.split()
            if values and values[0].isdigit():
                ops[name] = int(values[0])
    return ops


def evict(data_dir: Path) -> bool:
    """DB 파일을 이 장비의 페이지 캐시에서 내린다 (리눅스). 할 수 없으면 False.

    **연결을 모두 닫은 뒤에만 부른다.** 같은 프로세스가 DB 파일을 따로 열었다 닫으면
    그 파일에 SQLite 가 잡은 POSIX 잠금이 함께 풀린다 - 여기서는 경로마다 세션이
    닫힌 뒤라 괜찮다."""

    if not hasattr(os, "posix_fadvise"):
        return False
    for path in data_dir.glob("*.db*"):
        try:
            fd = os.open(str(path), os.O_RDONLY)
        except OSError:
            continue
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)
    return True


COUNTS = {"statements": 0, "connections": 0}
_connect = sqlite3.connect


def _counting_connect(*args, **kwargs):
    conn = _connect(*args, **kwargs)
    COUNTS["connections"] += 1
    conn.set_trace_callback(lambda _sql: COUNTS.__setitem__("statements", COUNTS["statements"] + 1))
    return conn


ROWS = []
SETTINGS = {"cold": False, "data_dir": None, "mount": None}


@contextmanager
def measure(name):
    if SETTINGS["cold"]:
        evict(SETTINGS["data_dir"])
    COUNTS["statements"] = COUNTS["connections"] = 0
    ops_before = nfs_ops(SETTINGS["mount"])
    before, started = read_bytes(), time.perf_counter()
    yield
    elapsed, read = time.perf_counter() - started, read_bytes() - before
    ops_after = nfs_ops(SETTINGS["mount"])
    delta = {op: ops_after.get(op, 0) - ops_before.get(op, 0) for op in ops_after}
    ROWS.append((name, elapsed, COUNTS["statements"], COUNTS["connections"], read, delta))


def report():
    print(f"{'경로':<38}{'초':>8}{'SQL문':>8}{'연결':>6}{'읽은MB':>10}{'NFS요청':>9}  NFS 주요 연산")
    for name, seconds, statements, connections, read, delta in ROWS:
        total = sum(delta.values())
        top = sorted(((count, op) for op, count in delta.items() if count), reverse=True)[:4]
        detail = " ".join(f"{op}={count}" for count, op in top)
        nfs = f"{total:>9}" if delta else f"{'-':>9}"
        print(f"{name:<38}{seconds:>8.2f}{statements:>8}{connections:>6}{read / 1e6:>10.1f}{nfs}  {detail}")


def header(code: Path, data_dir: Path) -> None:
    try:
        commit = (subprocess.run(
            ["git", "-C", str(code), "log", "-1", "--format=%h %s"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=10,
        ).stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        commit = ""
    mount = _mount_of(data_dir)
    print(f"코드      {code}  ({commit or '커밋 모름'})")
    print(f"장비      {platform.platform()}  Python {platform.python_version()}  "
          f"SQLite {sqlite3.sqlite_version}")
    if mount:
        print(f"데이터    {data_dir}  ({mount[1]} @ {mount[0]})")
        print(f"마운트    {mount[2]}")
    else:
        print(f"데이터    {data_dir}  (마운트 모름)")
    modes = [f"{label}={module.journal_mode(data_dir) or '?'}"
             for label, module in (("표본", store), ("스캔", scan_store))]
    cold = "예" if SETTINGS["cold"] and hasattr(os, "posix_fadvise") else "아니오"
    print(f"저널      {'  '.join(modes)}   경로마다 캐시 비움: {cold}\n")


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
    parser.add_argument("--cold", action="store_true", help="경로마다 DB 파일을 캐시에서 내린다 (리눅스)")
    parser.add_argument("--code", type=Path, default=Path(__file__).resolve().parents[1],
                        help="잴 코드가 있는 저장소 (기본: 이 파일이 든 저장소)")
    args = parser.parse_args()
    root = args.directory
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        print(f"{root} 가 비어 있지 않습니다 - 빈 디렉터리를 주세요.", file=sys.stderr)
        return 2

    _load(args.code.resolve())
    from smvwp import account_detail, dashboard, data_inventory, freshness, health, nightly_scan

    started = time.perf_counter()
    data_dir, config = build(root, args.accounts, args.days)
    built = time.perf_counter() - started
    mount = _mount_of(data_dir)
    SETTINGS.update(cold=args.cold, data_dir=data_dir, mount=mount[0] if mount else None)
    header(args.code.resolve(), data_dir)
    print(f"만들기 {built:.0f}초 - 계정 {args.accounts}개, 표본 {args.days}일 - 표본 DB "
          f"{store.db_path(data_dir).stat().st_size / 1e6:.0f}MB, 스캔 DB "
          f"{scan_store.db_path(data_dir).stat().st_size / 1e6:.0f}MB\n")

    # 야간 스캔이 끝날 때 남기는 것 - 헬스체크 대조(23a2d92 부터)와 스캔 탭 요약(그 뒤).
    # 그 앞 코드면 없는 것은 건너뛴다.
    nightly = [save for save in (getattr(health, "save_results", None),
                                 getattr(nightly_scan, "_save_summaries", None)) if save]
    if nightly:
        with measure("홈 첫 읽기 (밤 스캔이 남기기 전)"):
            dashboard.read_dashboard(data_dir, config, cache={})
        with measure("스캔 탭 첫 읽기 (밤 스캔이 남기기 전)"):
            nightly_scan.get_status_snapshot(data_dir, config, cache={})
        for save in nightly:
            save(data_dir, config)

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
