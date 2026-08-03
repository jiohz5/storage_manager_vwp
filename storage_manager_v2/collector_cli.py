#!/usr/bin/env python3
"""cron에서 호출하는 1회성 수집 스크립트 (15분 주기 백그라운드 타이머의 대안).

GUI를 띄우지 않고 수집 -> 저장 -> outbox 알림 -> 보존기간 정리까지 한 번
수행한 뒤 종료한다. `smvwp.cycle.run_collection_cycle`을 GUI의
`CollectorScheduler`와 그대로 공유하므로, cron 경로와 GUI 내부 타이머 경로가
서로 다르게 동작할 일이 없다.

이 스크립트와 `smvwp.cycle`은 PyQt5를 import하지 않는다 - cron 환경(디스플레이
없음)에서도 문제없이 돌아가야 하기 때문이다 (PyQt5 의존은 `smvwp.gui`,
`smvwp.scheduler`에만 있다).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smvwp import config as config_module  # noqa: E402
from smvwp import paths  # noqa: E402
from smvwp.cycle import run_collection_cycle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Storage Manager VWP - 1회 수집 (cron용)")
    parser.add_argument("--data-dir", help="데이터 디렉터리 (미지정 시 환경변수/포인터 파일 사용)")
    args = parser.parse_args()

    data_dir = paths.resolve_data_dir(args.data_dir)
    if data_dir is None:
        print(
            "ERROR: 데이터 디렉터리를 찾을 수 없습니다. --data-dir을 지정하거나 "
            "STORAGE_MANAGER_DATA_DIR을 설정하거나, GUI를 한 번 실행해 위치를 저장하세요.",
            file=sys.stderr,
        )
        return 2

    try:
        config = config_module.load_config(data_dir)
    except config_module.ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    records = run_collection_cycle(data_dir, config)
    failed = [r for r in records if not r.ok]
    print(f"수집 완료: {len(records)}개 계정, 실패 {len(failed)}건")
    for record in failed:
        print(f"  - {record.account_id}: {record.error_message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
