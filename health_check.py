from __future__ import annotations

import argparse
from pathlib import Path

from storage_manager.config import ConfigError
from storage_manager.health import run_health_check


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Storage Manager collection freshness.")
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    data_dir = Path(args.data_dir).expanduser().resolve()
    try:
        result = run_health_check(data_dir)
    except (ConfigError, OSError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(
        f"health notifications: sent={result.sent}, "
        f"suppressed={result.suppressed}, error={result.error or '-'}"
    )


if __name__ == "__main__":
    main()
