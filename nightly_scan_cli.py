#!/usr/bin/env python3
"""야간 상세 스캔 cron 진입점 (`du`/`find` 기반, 22:00~06:00 시간창).

cron이 22:00에 한 번 이 스크립트를 띄우면, 내부적으로 계정을 돌아가며
처리하다가 시간창이 끝나거나(06:00) 안전 중지 요청이 오면 스스로 멈춘다
(강제 kill 아님 - `smvwp.nightly_scan`/`smvwp.scan_lock` 참고). 터미널에서
사람이 `--now`로 직접 실행하면 시간창을 무시하고 끝까지(또는 명시적 중지
요청까지) 돈다 - CONCEPT.md 3절의 "의도적 진단/복구 경로".

PyQt5를 import하지 않는다 - cron/터미널 어디서나 디스플레이 없이 동작해야
한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smvwp import config as config_module  # noqa: E402
from smvwp import nightly_scan, paths  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Storage Manager VWP - 야간 상세 스캔")
    parser.add_argument("--data-dir", help="데이터 디렉터리 (미지정 시 환경변수/포인터 파일 사용)")
    parser.add_argument(
        "--now",
        action="store_true",
        help="시간창(22:00~06:00)을 무시하고 지금 바로 실행 - 터미널 직접 실행용 진단/복구 경로",
    )
    parser.add_argument("--stop", action="store_true", help="현재 실행 중인 야간 스캔에 안전 중지를 요청하고 종료")
    args = parser.parse_args()

    data_dir = paths.resolve_data_dir(args.data_dir)
    if data_dir is None:
        print(
            "ERROR: 데이터 디렉터리를 찾을 수 없습니다. --data-dir을 지정하거나 "
            "STORAGE_MANAGER_DATA_DIR을 설정하거나, GUI를 한 번 실행해 위치를 저장하세요.",
            file=sys.stderr,
        )
        return 2

    if args.stop:
        requested = nightly_scan.request_stop(data_dir)
        if requested:
            print("중지를 요청했습니다. 실행 중인 스캔이 다음 체크포인트에서 안전하게 멈춥니다.")
            return 0
        print("현재 실행 중인 야간 스캔이 없습니다.")
        return 1

    try:
        config = config_module.load_config(data_dir)
    except config_module.ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    summary = nightly_scan.run_nightly_scan(
        data_dir, config, triggered_by="terminal" if args.now else "cron", bypass_window=args.now
    )

    if not summary.started:
        print(f"실행하지 않음: {summary.reason}")
        return 0

    print(f"야간 상세 스캔 종료 (run_id={summary.run_id}, 상태={summary.status})")
    for outcome in summary.accounts:
        print(
            f"  - {outcome.account_name}: 기준선 세대 {outcome.baseline_generation} "
            f"({outcome.baseline_status}), 활동 스캔 pass {outcome.activity_pass} "
            f"({outcome.activity_status})"
        )
    return 0 if summary.status in (nightly_scan.STATUS_COMPLETED, nightly_scan.STATUS_PAUSED) else 1


if __name__ == "__main__":
    raise SystemExit(main())
