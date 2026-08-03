#!/usr/bin/env python3
"""트레이 알림기 진입점 (로그인 자동 시작 대상).

메인 GUI(`app.py`)와 독립된 프로세스다 - 관리 창을 닫아도 cron이 쌓은 알림은
이 프로세스가 계속 표시한다.

    ./notifier_cli.py --data-dir /path/to/data              # 트레이 실행
    ./notifier_cli.py --data-dir /path/to/data --install-autostart
    ./notifier_cli.py --data-dir /path/to/data --status
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smvwp.notifier import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
