from __future__ import annotations

import argparse
import sys
from pathlib import Path

from storage_manager.capacity_watch import run_capacity_watch
from storage_manager.config import ConfigError
from storage_manager.scheduler import ScanAlreadyRunning


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the lightweight Storage Manager capacity watch."
    )
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    data_dir = Path(args.data_dir).expanduser().resolve()
    try:
        result = run_capacity_watch(data_dir)
    except (ConfigError, ScanAlreadyRunning, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        "capacity watch: "
        f"filesystems={result.filesystems_checked}, "
        f"samples={result.samples_written}, "
        f"events={result.events_written}, "
        f"errors={len(result.errors)}, "
        f"seconds={result.duration_seconds:.3f}"
    )
    for error in result.errors:
        print(f"WARN: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
