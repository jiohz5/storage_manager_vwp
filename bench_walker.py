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
import subprocess
import sys
import threading
import time
from collections import deque


# `st_blocks`(512바이트 단위 실제 점유)는 POSIX 에만 있다. 반입 대상은 리눅스라
# 실전에서는 항상 있지만, 개발 PC(Windows)에서도 구조를 확인할 수 있어야 하므로
# 없으면 파일 크기를 블록 경계로 올림해 흉내 낸다.
#
# **흉내 값은 `du` 와 일치하지 않는다.** 그래서 아래에서 경고를 띄운다 -
# 이 스크립트의 목적이 "값이 맞는가"를 확인하는 것이기 때문이다.
HAVE_ST_BLOCKS = hasattr(os.stat(os.curdir), "st_blocks")
FALLBACK_BLOCK_SIZE = 4096


def disk_blocks(info) -> int:
    """`du` 가 세는 512바이트 블록 수."""

    if HAVE_ST_BLOCKS:
        return info.st_blocks
    rounded = -(-info.st_size // FALLBACK_BLOCK_SIZE) * FALLBACK_BLOCK_SIZE
    return rounded // 512


class ParallelWalker:
    """디렉터리 큐를 여러 스레드가 나눠 훑는다.

    큐가 잠깐 비어도 끝내지 않는다 - 다른 스레드가 처리 중인 디렉터리에서
    하위 디렉터리가 새로 나올 수 있기 때문이다. 아무도 일하고 있지 않을 때만
    진짜 끝이다 (야간 스캔의 체크포인트 작업자와 같은 규칙).
    """

    def __init__(self, root: str, workers: int):
        self.root = root
        self.workers = max(1, workers)
        self._queue = deque([root])
        self._lock = threading.Lock()
        self._busy = 0
        self.blocks = 0          # 512바이트 블록 수 (du 와 같은 기준)
        self.files = 0
        self.dirs = 0
        self.errors = 0
        self._seen_inodes = set()  # 하드링크를 한 번만 세기 위해

    def _take(self):
        with self._lock:
            if self._queue:
                self._busy += 1
                return self._queue.popleft()
            return None

    def _done(self, found_dirs):
        with self._lock:
            self._queue.extend(found_dirs)
            self._busy -= 1

    def _idle(self) -> bool:
        with self._lock:
            return not self._queue and self._busy == 0

    def _scan_one(self, path: str):
        """디렉터리 하나를 훑고, 그 안에서 찾은 하위 디렉터리를 돌려준다."""

        found_dirs = []
        blocks = files = 0
        new_inodes = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            found_dirs.append(entry.path)
                            continue
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        with self._lock:
                            self.errors += 1
                        continue
                    # 하드링크가 있는 파일만 중복 검사를 한다 (대부분은 nlink==1).
                    if info.st_nlink > 1:
                        new_inodes.append(((info.st_dev, info.st_ino), disk_blocks(info)))
                        continue
                    blocks += disk_blocks(info)
                    files += 1
        except OSError:
            with self._lock:
                self.errors += 1

        with self._lock:
            self.blocks += blocks
            self.files += files
            self.dirs += 1
            for key, block_count in new_inodes:
                if key in self._seen_inodes:
                    continue
                self._seen_inodes.add(key)
                self.blocks += block_count
                self.files += 1
        return found_dirs

    def _worker(self):
        while True:
            path = self._take()
            if path is None:
                if self._idle():
                    return
                time.sleep(0.005)
                continue
            found = self._scan_one(path)
            self._done(found)

    def run(self) -> None:
        if self.workers == 1:
            self._worker()
            return
        threads = [
            threading.Thread(target=self._worker, daemon=True)
            for _ in range(self.workers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    @property
    def kilobytes(self) -> int:
        return self.blocks * 512 // 1024


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
        walker = ParallelWalker(args.path, workers)
        started = time.monotonic()
        walker.run()
        elapsed = time.monotonic() - started
        if baseline is None:
            baseline = elapsed

        speed = f"{cold_seconds / elapsed:.1f}x" if elapsed > 0 else "-"
        match = ""
        if du_kb:
            gap = abs(walker.kilobytes - du_kb) / du_kb * 100
            match = "일치" if gap < 1 else f"차이 {gap:.1f}%"
        print(
            f"python x{workers:<11}{elapsed:8.1f}s   {walker.kilobytes:>14,} KB"
            f"   {speed:>6}   {match}"
        )
        results.append((workers, elapsed))
        if walker.errors:
            print(f"{'':16}읽지 못한 항목 {walker.errors:,}개 (권한 등)")

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
