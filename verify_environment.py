from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List


MIN_SQLITE = (3, 24, 0)
NETWORK_FILESYSTEMS = ("nfs", "cifs", "smb", "lustre", "sshfs")


@dataclass(frozen=True)
class CheckResult:
    level: str
    message: str


def _compact_error(value: str, limit: int = 300) -> str:
    text = " ".join(value.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _qt_platform_check() -> CheckResult:
    script = (
        "from PyQt5.QtWidgets import QApplication; "
        "from PyQt5.QtGui import QFont,QFontDatabase,QFontMetrics; "
        "app=QApplication([]); families=set(QFontDatabase().families()); "
        "names=['Malgun Gothic','Noto Sans CJK KR','NanumGothic','Noto Sans KR']; "
        "name=next((n for n in names if n in families),app.font().family()); "
        "font=QFont(name); korean=QFontMetrics(font).inFontUcs4(ord('한')); "
        "print(f'{app.platformName()}|{name}|{int(korean)}')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult("FAIL", f"Qt platform probe failed: {exc}")
    if result.returncode != 0:
        detail = _compact_error(result.stderr) or f"exit code {result.returncode}"
        return CheckResult("FAIL", f"Qt platform plugin failed: {detail}")
    output = result.stdout.strip().split("|")
    platform = output[0] if output else "unknown"
    font_name = output[1] if len(output) > 1 else "unknown"
    korean_supported = len(output) > 2 and output[2] == "1"
    display = os.environ.get("DISPLAY", "not set")
    if not korean_supported:
        return CheckResult(
            "FAIL",
            f"Qt platform {platform} loaded, but font '{font_name}' cannot render Korean",
        )
    return CheckResult(
        "OK",
        f"Qt platform: {platform}; Korean font: {font_name}; DISPLAY={display}",
    )


def _filesystem_check(data_dir: Path) -> CheckResult:
    findmnt = shutil.which("findmnt")
    if not findmnt:
        return CheckResult("WARN", "findmnt not found; data filesystem type was not detected")
    try:
        result = subprocess.run(
            [findmnt, "-T", str(data_dir), "-n", "-o", "FSTYPE,SOURCE"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult("WARN", f"Cannot detect data filesystem: {exc}")
    if result.returncode != 0:
        return CheckResult("WARN", f"Cannot detect data filesystem: {_compact_error(result.stderr)}")
    description = result.stdout.strip()
    fs_type = description.split(maxsplit=1)[0].lower() if description else "unknown"
    if fs_type.startswith(NETWORK_FILESYSTEMS):
        return CheckResult(
            "WARN",
            f"Data filesystem is {description}; use a private data directory and do not share SQLite",
        )
    return CheckResult("OK", f"Data filesystem: {description}")


def check_environment(data_dir: Path, monitored_root: Path = Path("/user")) -> int:
    checks: List[CheckResult] = []
    python_ok = sys.version_info >= (3, 8)
    checks.append(
        CheckResult(
            "OK" if python_ok else "FAIL",
            f"Python {sys.version.split()[0]} at {sys.executable} (3.8+ required)",
        )
    )

    try:
        from PyQt5.QtCore import PYQT_VERSION_STR
    except Exception as exc:
        checks.append(CheckResult("FAIL", f"PyQt5 import failed: {exc}"))
    else:
        checks.append(CheckResult("OK", f"PyQt5 {PYQT_VERSION_STR}"))
        checks.append(_qt_platform_check())

    sqlite_ok = sqlite3.sqlite_version_info >= MIN_SQLITE
    checks.append(
        CheckResult(
            "OK" if sqlite_ok else "FAIL",
            f"SQLite {sqlite3.sqlite_version} (3.24+ required)",
        )
    )

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".storage-manager-probe-", dir=str(data_dir)) as temp:
            probe_dir = Path(temp)
            probe_file = probe_dir / "probe file.txt"
            probe_file.write_bytes(b"storage-manager-probe\n")

            from storage_manager.database import Database

            database = Database(probe_dir / "probe.db")
            database.close()
    except Exception as exc:
        checks.append(CheckResult("FAIL", f"Data/SQLite write probe failed: {exc}"))
    else:
        free = shutil.disk_usage(str(data_dir)).free
        level = "OK" if free >= 100 * 1024 * 1024 else "WARN"
        checks.append(
            CheckResult(level, f"Data directory writable: {data_dir}; free {_human_bytes(free)}")
        )

    root_ok = (
        monitored_root.is_dir()
        and os.access(str(monitored_root), os.R_OK | os.X_OK)
    )
    checks.append(
        CheckResult(
            "OK" if root_ok else "FAIL",
            f"Monitored root readable: {monitored_root}" if root_ok else f"Monitored root missing/unreadable: {monitored_root}",
        )
    )

    df_path = shutil.which("df")
    if not df_path:
        checks.append(CheckResult("FAIL", "df not found"))
    else:
        try:
            from storage_manager.collector import run_df

            snapshot = run_df(str(data_dir), timeout_seconds=15)
        except Exception as exc:
            checks.append(CheckResult("FAIL", f"df option/parse probe failed: {exc}"))
        else:
            if snapshot.inode_use_pct is None:
                checks.append(
                    CheckResult("FAIL", "df -Pi inode option/parse probe failed")
                )
            else:
                checks.append(
                    CheckResult(
                        "OK",
                        f"df probe: {snapshot.fs_name}, byte {snapshot.use_pct}%, "
                        f"inode {snapshot.inode_use_pct}% used",
                    )
                )

    du_path = shutil.which("du")
    if not du_path:
        checks.append(CheckResult("FAIL", "du not found"))
    else:
        try:
            from storage_manager.collector import collect_top_level_sizes

            with tempfile.TemporaryDirectory(prefix=".du-probe-", dir=str(data_dir)) as temp:
                probe_dir = Path(temp)
                (probe_dir / "probe file.txt").write_bytes(b"probe\n")
                detail = collect_top_level_sizes(str(probe_dir), timeout_seconds=15)
            if not detail.complete:
                raise RuntimeError(detail.error)
            if not any(Path(item_path).name == "probe file.txt" for item_path, _ in detail.items):
                raise RuntimeError("du output did not include a top-level file")
        except Exception as exc:
            checks.append(CheckResult("FAIL", f"du option/parse probe failed: {exc}"))
        else:
            checks.append(CheckResult("OK", "du -a -x -k --max-depth=1 probe completed"))

    find_path = shutil.which("find")
    if not find_path:
        checks.append(CheckResult("FAIL", "find not found"))
    else:
        try:
            from storage_manager.activity_scan import scan_changed_file_activity

            with tempfile.TemporaryDirectory(prefix=".find-probe-", dir=str(data_dir)) as temp:
                probe_dir = Path(temp)
                (probe_dir / "probe file.txt").write_bytes(b"probe\n")
                activity = scan_changed_file_activity(
                    str(probe_dir),
                    "2000-01-01 00:00:00",
                    15,
                )
            if not activity.complete or activity.files_seen != 1:
                raise RuntimeError(activity.error or "find did not return the probe file")
        except Exception as exc:
            checks.append(CheckResult("FAIL", f"find -newermt/-printf probe failed: {exc}"))
        else:
            checks.append(CheckResult("OK", "find -newermt -printf probe completed"))

    crontab_path = shutil.which("crontab")
    checks.append(
        CheckResult(
            "OK" if crontab_path else "FAIL",
            f"crontab: {crontab_path or 'not found'}",
        )
    )
    csh_path = shutil.which("csh") or shutil.which("tcsh")
    checks.append(
        CheckResult(
            "OK" if csh_path else "FAIL",
            f"csh: {csh_path or 'not found'}",
        )
    )
    checks.append(_filesystem_check(data_dir))

    for result in checks:
        print(f"[{result.level}] {result.message}")
    failures = sum(result.level == "FAIL" for result in checks)
    warnings = sum(result.level == "WARN" for result in checks)
    print(f"Summary: {len(checks) - failures - warnings} OK, {warnings} WARN, {failures} FAIL")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the offline RHEL/VWP runtime.")
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument("--monitored-root", default="/user")
    args = parser.parse_args()
    raise SystemExit(
        check_environment(
            Path(args.data_dir).expanduser().resolve(),
            Path(args.monitored_root).expanduser().resolve(),
        )
    )


if __name__ == "__main__":
    main()
