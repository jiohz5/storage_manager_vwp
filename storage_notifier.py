from __future__ import annotations

import argparse
import json
from pathlib import Path

from storage_manager.notifier import (
    install_autostart,
    read_notifier_status,
    remove_autostart,
    run_notifier,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Storage Manager local tray notifier.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--install-autostart", action="store_true")
    parser.add_argument("--remove-autostart", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    data_dir = Path(args.data_dir).expanduser().resolve()
    if args.install_autostart:
        print(install_autostart(data_dir))
        return
    if args.remove_autostart:
        print("removed" if remove_autostart() else "not installed")
        return
    if args.status:
        print(json.dumps(read_notifier_status(data_dir), ensure_ascii=False, indent=2))
        return
    raise SystemExit(run_notifier(data_dir, args.run_id))


if __name__ == "__main__":
    main()
