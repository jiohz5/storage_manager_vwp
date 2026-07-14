from __future__ import annotations

import argparse
import sys
from pathlib import Path

from storage_manager.runtime import (
    RuntimePathError,
    collect_runtime_diagnostics,
    resolve_data_dir,
    save_data_dir_location,
    write_runtime_diagnostics,
)


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def _print_summary(payload: dict) -> None:
    python = payload["python"]
    sqlite = payload["sqlite"]
    pyqt = payload["pyqt5"]
    identity = payload["identity"]
    print(f"Python: {python['executable']}")
    print(f"Python version: {python['version']}")
    print(f"json: {payload['json']['path']}")
    print(f"SQLite: {sqlite['version']} ({'OK' if sqlite['supported'] else 'UNSUPPORTED'})")
    print(
        "PyQt5: "
        + (
            f"{pyqt['pyqt_version']} / Qt {pyqt['qt_version']}"
            if pyqt.get("available")
            else f"ERROR: {pyqt.get('error', 'not available')}"
        )
    )
    groups = ", ".join(str(value) for value in identity.get("groups", [])) or "-"
    print(f"User/groups: {identity.get('user', '-')} / {groups}")
    data = payload.get("data")
    if isinstance(data, dict):
        print(f"Data directory: {data.get('path', '-')}")
        if data.get("writable"):
            print(
                f"Data usage/free: {_human_bytes(int(data.get('size_bytes', 0)))} / "
                f"{_human_bytes(int(data.get('free_bytes', 0)))}"
            )
        else:
            print(f"Data error: {data.get('error', 'not writable')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Storage Manager runtime diagnostics")
    parser.add_argument("--data-dir")
    parser.add_argument("--python-only", action="store_true")
    parser.add_argument("--resolve-data-dir", action="store_true")
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--set-data-dir")
    args = parser.parse_args()

    try:
        if args.set_data_dir:
            data_dir = Path(args.set_data_dir).expanduser().resolve()
            payload = collect_runtime_diagnostics(data_dir)
            if not payload["ok"]:
                _print_summary(payload)
                raise SystemExit(1)
            save_data_dir_location(data_dir)
            diagnostic_path = write_runtime_diagnostics(data_dir, payload)
            _print_summary(payload)
            print(f"Saved data location: {data_dir}")
            print(f"Diagnostics: {diagnostic_path}")
            return

        explicit = Path(args.data_dir) if args.data_dir else None
        data_dir = None if args.python_only else resolve_data_dir(explicit)
        if args.resolve_data_dir:
            if data_dir is None:
                if args.allow_missing:
                    return
                print(
                    "ERROR: No global data directory is configured. Set "
                    "STORAGE_MANAGER_DATA_DIR or run the GUI once.",
                    file=sys.stderr,
                )
                raise SystemExit(2)
            payload = collect_runtime_diagnostics(data_dir)
            if not payload["ok"]:
                print(str(payload.get("data", {}).get("error", "runtime check failed")), file=sys.stderr)
                raise SystemExit(1)
            print(data_dir)
            return

        payload = collect_runtime_diagnostics(data_dir)
        _print_summary(payload)
        if data_dir is None and not args.python_only:
            print(
                "Data directory: NOT CONFIGURED (select it on first GUI launch or set "
                "STORAGE_MANAGER_DATA_DIR)"
            )
        elif data_dir is not None and payload["ok"]:
            diagnostic_path = write_runtime_diagnostics(data_dir, payload)
            print(f"Diagnostics: {diagnostic_path}")
        raise SystemExit(0 if payload["ok"] else 1)
    except RuntimePathError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc


if __name__ == "__main__":
    main()
