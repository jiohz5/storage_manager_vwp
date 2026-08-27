#!/usr/bin/env python3
"""파이썬 병렬 순회가 `du`를 대체할 만한지 실측한다.

    ./bench_walker.py /user/계정/디렉터리
    ./bench_walker.py /user/계정/디렉터리 --workers 1,2,4,8,16

`du -sk`와 파이썬 스레드 순회를 같은 디렉터리에서 재고, **크기가 일치하는지도**
확인한다. 숫자가 다르면 속도는 의미가 없다.

## 왜 스레드인가 (프로세스가 아니라)

`os.stat`은 I/O 대기라 호출 중에 GIL이 풀린다. NFS에서는 시간의 대부분이
네트워크 왕복이므로 스레드만으로 요청을 여러 개 띄울 수 있다. multiprocessing은
pickle 직렬화와 프로세스 생성 비용만 더한다.

## `du`와 같은 값을 내는 법

- 크기는 `st_size`가 아니라 **`st_blocks * 512`** 다. 실제로 차지한 블록 수라
  희소 파일(sparse)과 파일시스템 오버헤드가 반영된다. `du`가 세는 것이 이것이다.
- **하드링크는 한 번만** 센다. `(st_dev, st_ino)`로 이미 본 것을 건너뛴다.
  `du`도 실행 하나 안에서는 그렇게 한다.
- 심링크는 따라가지 않는다 (`follow_symlinks=False`).

이 스크립트는 아무것도 쓰지 않는다. 읽기만 한다.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import time


# 순회 구현은 `smvwp.walker` 하나만 쓴다.
#
# 예전에는 이 파일이 자체 복사본을 들고 있었는데, walker 의 잠금 경합을 고친
# 뒤에도 벤치마크는 옛 코드를 재고 있었다. **재는 대상과 실제로 쓰는 대상이
# 다르면 측정이 거짓말이 된다.**
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from smvwp import walker as walker_module  # noqa: E402

HAVE_ST_BLOCKS = walker_module.HAVE_ST_BLOCKS


def walk_once(path: str, workers: int):
    """한 번 훑고 `(걸린 시간, KB, 못 읽은 수)`를 준다."""

    started = time.monotonic()
    outcome = walker_module.walk_tree(path, max_depth=0, workers=workers)
    elapsed = time.monotonic() - started
    total = outcome.root_size_kb
    if total is None:
        # 완주하지 못했으면 크기를 말할 수 없다 (0 으로 채우면 오해를 만든다).
        total = 0
    return elapsed, total, outcome.unreadable


def run_du(path: str):
    started = time.monotonic()
    proc = subprocess.run(["du", "-sk", "--", path], capture_output=True)
    elapsed = time.monotonic() - started
    try:
        size_kb = int(proc.stdout.split()[0])
    except (IndexError, ValueError):
        size_kb = None
    return elapsed, size_kb


def main() -> int:
    parser = argparse.ArgumentParser(description="파이썬 병렬 순회 vs du 실측")
    parser.add_argument("path")
    parser.add_argument(
        "--workers", default="1,2,4,8,16",
        help="시험할 스레드 수 (쉼표 구분). 기본 1,2,4,8,16",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.path):
        print(f"디렉터리가 아닙니다: {args.path}", file=sys.stderr)
        return 1

    print(f"대상: {args.path}\n")
    if not HAVE_ST_BLOCKS:
        print("주의: 이 플랫폼에는 st_blocks 가 없어 크기를 근사합니다.")
        print("      du 와 일치하지 않는 것이 정상이며 속도만 참고하세요.")
        print("      실제 판단은 리눅스에서 하세요.\n")

    # `du` 를 **먼저 한 번, 파이썬 뒤에 또 한 번** 잰다.
    #
    # 처음 것은 차가운 캐시를 다 물고, 뒤 것은 파이썬과 같은(따뜻한) 조건이다.
    # 이걸 안 하면 파이썬이 캐시 덕을 본 것을 실력으로 착각한다 - 실제로
    # 첫 측정에서 x1 이 du 보다 4배 빠르게 나왔는데, 그 대부분이 캐시였다.
    cold_seconds, du_kb = run_du(args.path)
    print(f"{'du -sk (차가움)':<20}{cold_seconds:8.1f}s   "
          f"{du_kb if du_kb is not None else '?':>14,} KB")
    print("-" * 68)

    results = []
    baseline = None
    for text in args.workers.split(","):
        text = text.strip()
        if not text.isdigit():
            continue
        workers = int(text)
        elapsed, kilobytes, unreadable = walk_once(args.path, workers)
        if baseline is None:
            baseline = elapsed

        speed = f"{cold_seconds / elapsed:.1f}x" if elapsed > 0 else "-"
        match = ""
        if du_kb:
            gap = abs(kilobytes - du_kb) / du_kb * 100
            match = "일치" if gap < 1 else f"차이 {gap:.1f}%"
        print(
            f"python x{workers:<11}{elapsed:8.1f}s   {kilobytes:>14,} KB"
            f"   {speed:>6}   {match}"
        )
        results.append((workers, elapsed))
        if unreadable:
            print(f"{'':16}읽지 못한 항목 {unreadable:,}개 (권한 등)")

    warm_seconds, _ = run_du(args.path)
    print("-" * 68)
    print(f"{'du -sk (따뜻함)':<20}{warm_seconds:8.1f}s"
          f"   <- 파이썬과 같은 조건에서의 du")

    print()
    print("읽는 법")
    if cold_seconds > 0 and warm_seconds * 2 < cold_seconds:
        fair = warm_seconds
        print(f"  · 캐시 영향이 큽니다 (du 가 {cold_seconds:.1f}s -> {warm_seconds:.1f}s).")
        print(f"    **공정한 기준은 따뜻한 du {warm_seconds:.1f}s** 입니다:")
    else:
        fair = cold_seconds
        print("  · 캐시 영향이 작습니다. 위 배수를 그대로 읽으면 됩니다.")
    for workers, elapsed in results:
        if elapsed > 0:
            print(f"      x{workers:<3} {fair / elapsed:5.1f}x")
    print("  · 크기가 '일치'해야 의미가 있습니다. 차이가 크면 세는 기준이")
    print("    어긋난 것이니 그대로 쓰면 안 됩니다.")
    print("  · 스레드를 늘려도 안 빨라지는 지점이 이 서버의 한계입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
