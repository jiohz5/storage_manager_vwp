"""파이썬 병렬 디렉터리 순회 - `du -k --max-depth=N`의 대체 엔진.

## 왜 만들었나

반입 대상이 NFS다. 병목은 디스크 탐색이 아니라 **파일 하나당 `getattr` RPC
왕복**이고, 메타데이터 연산은 읽기·쓰기와 달리 파이프라이닝이 되지 않는다.
`du`는 디렉터리를 한 줄로 걷기 때문에 언제나 요청이 **하나만** 떠 있다 -
실기의 RPC 슬롯 상한이 128인데 그중 1개만 쓴다.

실측이 이를 확인해 준다: 같은 트리에서 병렬 walker(gdu)가 `du`보다 4배 빨랐다.

## 왜 스레드인가 (프로세스가 아니라)

`os.stat`은 I/O 대기라 호출 중에 GIL이 풀린다. NFS에서는 시간의 대부분이
네트워크 왕복이므로 스레드만으로 요청을 여러 개 띄울 수 있다. multiprocessing은
pickle 직렬화와 프로세스 생성 비용만 더하고, 얻는 것이 없다.

## 왜 순회 한 번이 세 번보다 훨씬 나은가

지금은 같은 트리를 밤마다 최대 세 번 걷는다 - `du`(크기), `find`(변경 파일),
검색 인덱스(이름). 한 번 걸으면 `st_blocks`·`st_mtime`·`st_ino`가 모두 함께
나온다. 병렬 이전에 **순회 횟수 자체가 1/3**이 된다.

## `du`와 같은 값을 내는 규칙

- 크기는 `st_size`가 아니라 **`st_blocks * 512`**다. 실제 점유 블록이라 희소
  파일과 파일시스템 오버헤드가 반영된다. `du`가 세는 것이 이것이다.
- **하드링크는 한 번만** 센다. `du`도 실행 하나 안에서는 그렇게 한다. 지금
  체크포인트 분할이 만들고 있는 이중 계산도 이 방식이면 사라진다.
- 심링크는 따라가지 않는다.
- 스냅샷 디렉터리는 내려가지 않는다.

## 시간 초과 때 무엇을 남기는가

**완료된 디렉터리만 낸다.** `du`가 디렉터리를 다 센 뒤에 그 줄을 찍는 것과 같은
성질이고, 야간 스캔의 재개 설계가 이 성질에 기대고 있다 (완료된 것은 신뢰할 수
있으므로 저장하고, 나머지만 다시 큐에 넣는다).

그래서 "이 디렉터리가 끝났는가"를 직접 센다 - 하위 디렉터리가 모두 끝나고 제
파일을 다 셌을 때 완료다. gdu가 못 하는 것이 정확히 이것이라(트리를 메모리에 다
만든 뒤 한 번에 내보낸다) 갈아탈 수 없었다.
"""

from __future__ import annotations

import os
import heapq
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Set

# `st_blocks`(512바이트 단위 실제 점유)는 POSIX 전용이다. 반입 대상은 리눅스라
# 실전에서는 항상 있지만, 개발 PC(Windows)에서 구조를 확인할 수 있어야 하므로
# 없으면 파일 크기를 블록 경계로 올림해 흉내 낸다. 그 값은 `du`와 일치하지
# 않으므로 개발 환경 전용이다.
HAVE_ST_BLOCKS = hasattr(os.stat(os.curdir), "st_blocks")
FALLBACK_BLOCK_SIZE = 4096

# 순회 하나가 기억할 "가장 큰 파일" 개수.
#
# 화면과 보고서가 보여 줄 수 있는 양이 상한이다 - 수천 개를 모아 봐야 아무도
# 안 읽고 메모리만 쓴다. 힙에 올려 두므로 이 수를 넘으면 가장 작은 것이 밀려
# 나간다(전체 정렬을 하지 않는다).
LARGEST_FILES_KEPT = 50

# 이보다 작은 파일은 후보로도 보지 않는다.
#
# 작은 파일이 수십만 개인 트리에서 힙을 계속 흔들면 순회 자체가 느려진다.
# 어차피 "비정상적으로 큰 파일"을 찾는 것이므로 바닥을 깔아 두는 편이 맞다.
LARGEST_FILE_MIN_KB = 100 * 1024   # 100MB
_MIN_BLOCKS = LARGEST_FILE_MIN_KB * 1024 // 512

# 알림을 놓쳤을 때를 대비한 안전망. 정상 흐름에서는 알림으로 깨어나므로
# 이 시간만큼 기다리는 일이 없다.
WAIT_TIMEOUT_SECONDS = 0.05


def disk_blocks(info) -> int:
    """`du`가 세는 512바이트 블록 수."""

    if HAVE_ST_BLOCKS:
        return info.st_blocks
    rounded = -(-info.st_size // FALLBACK_BLOCK_SIZE) * FALLBACK_BLOCK_SIZE
    return rounded // 512


@dataclass
class _Node:
    """순회 중인 디렉터리 하나의 상태."""

    path: str
    depth: int
    parent: Optional["_Node"]
    blocks: int = 0              # 자기 파일 + 완료된 자식들의 합
    pending_children: int = 0
    scanned: bool = False        # 자기 디렉터리를 읽었는가
    complete: bool = False


@dataclass
class WalkOutcome:
    """순회 한 번의 결과.

    `detail_scan.DuTreeOutcome`과 같은 모양이라 그대로 바꿔 끼울 수 있다.
    """

    entries: List[tuple] = field(default_factory=list)   # (경로, KB)
    root_size_kb: Optional[int] = None
    completed: bool = False
    timed_out: bool = False
    partial: bool = False         # 권한 등으로 일부를 못 읽었는가
    error_message: Optional[str] = None
    file_count: int = 0
    dir_count: int = 0
    unreadable: int = 0
    # 아래는 진단용 - 스캔 자체는 쓰지 않는다 (`probe` 참고).
    # 가장 큰 파일 `(KB, 경로)` 목록, 큰 것부터. 순회 중에 곁다리로 모은다.
    largest_files: List[tuple] = field(default_factory=list)
    hardlink_files: int = 0   # nlink>1 이라 중복 검사를 거친 파일 수
    logical_bytes: int = 0    # st_size 합. 점유 블록과 벌어지면 희소/작은 파일
    max_depth_seen: int = 0   # 루트를 0으로 본 가장 깊은 디렉터리


class ParallelWalker:
    """디렉터리 큐를 여러 스레드가 나눠 훑는다."""

    def __init__(
        self,
        root: str,
        max_depth: int = 3,
        workers: int = 4,
        timeout_seconds: Optional[float] = None,
        exclude_names: Optional[Set[str]] = None,
    ):
        self.root = root.rstrip("/") or root
        self.max_depth = max_depth
        self.workers = max(1, workers)
        self.timeout_seconds = timeout_seconds
        self.exclude_names = exclude_names or set()

        # 잠금을 둘로 나눈다. 하나로 두면 큐를 집는 스레드와 트리를 갱신하는
        # 스레드가 서로를 막는다.
        #
        # 그리고 **조건변수를 쓴다.** 예전에는 큐가 비면 5ms 자고 다시 잠금을
        # 잡는 폴링이었는데, 스레드가 늘수록 그 폴링만으로 초당 수천 번 잠금을
        # 다퉜다. 실측이 이를 드러냈다 - 한 프로세스 8스레드는 4스레드와 같은
        # 시간이었지만, 두 프로세스로 4스레드씩 돌리면 처리량이 2배였다.
        # 서버가 아니라 이쪽이 한계였다는 뜻이다.
        self._queue_lock = threading.Lock()
        self._cond = threading.Condition(self._queue_lock)
        self._tree_lock = threading.Lock()
        self._queue = deque()
        self._busy = 0
        self._deadline: Optional[float] = None
        self._stop = threading.Event()

        self._root_node = _Node(path=self.root, depth=0, parent=None)
        # 만들어진 노드 전부. 결과를 낼 때 한 번 훑으면 되므로 목록이면 충분하다
        # (경로를 키로 하는 사전은 노드 수만큼 해시 비용만 더한다).
        self._all_nodes: List[_Node] = [self._root_node]
        self._seen_inodes: Set[tuple] = set()

        self.file_count = 0
        self.dir_count = 0
        self.unreadable = 0
        self.hardlink_files = 0
        self.logical_bytes = 0
        # 가장 큰 파일 후보. **최소 힙**이라 [0]이 가장 작은 것이고, 상한을
        # 넘으면 그것을 밀어낸다. 전체를 모아 정렬하면 파일 수만큼 메모리를
        # 쓰는데, 우리가 필요한 것은 상위 몇 개뿐이다.
        self._largest: List[tuple] = []

    # -- 큐 -----------------------------------------------------------
    def _next(self) -> Optional[_Node]:
        """다음 디렉터리를 받는다. 더 없으면 None (그때가 순회의 끝).

        큐가 비었다고 바로 끝내지 않는다 - 다른 스레드가 처리 중인 디렉터리에서
        하위가 새로 나올 수 있기 때문이다. **아무도 일하지 않고 큐도 비었을
        때**만 진짜 끝이고, 그 사실을 확인하면 남은 스레드도 모두 깨워 내보낸다.

        `wait`에 시간 제한을 둔 것은 안전망이다 - 알림을 한 번 놓쳐도 영원히
        멈춰 있지 않는다.
        """

        with self._cond:
            while True:
                if self._stop.is_set():
                    return None
                if self._queue:
                    self._busy += 1
                    return self._queue.popleft()
                if self._busy == 0:
                    self._cond.notify_all()
                    return None
                self._cond.wait(timeout=WAIT_TIMEOUT_SECONDS)

    def _finish(self, children: List[_Node]) -> None:
        """스캔이 끝난 뒤 큐에 자식을 넣고 대기 중인 스레드를 깨운다."""

        with self._cond:
            self._queue.extend(children)
            self._busy -= 1
            self._cond.notify_all()

    def _wake_all(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def _expired(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    # -- 완료 전파 ----------------------------------------------------
    def _settle(self, node: _Node) -> None:
        """이 디렉터리가 끝났으면 부모로 합계를 올린다 (`_tree_lock` 안에서 부를 것).

        부모도 그 때문에 끝날 수 있으므로 위로 이어서 전파한다. 재귀 대신
        반복문을 쓴다 - 깊은 트리에서 스택이 넘치면 순회가 통째로 죽는다.
        """

        current = node
        while current is not None:
            if not (current.scanned and current.pending_children == 0):
                return
            if current.complete:
                return
            current.complete = True
            parent = current.parent
            if parent is None:
                return
            parent.blocks += current.blocks
            parent.pending_children -= 1
            current = parent

    # -- 작업자 -------------------------------------------------------
    def _scan_one(self, node: _Node) -> tuple:
        """디렉터리 하나를 읽는다. `(파일 수, 디렉터리 수, 못 읽은 수, 자식들)`.

        **큐는 건드리지 않는다.** 큐 반납은 호출자가 `finally`에서 하므로,
        여기서 예외가 나도 `_busy` 가 새지 않는다 (새면 아무도 일하지 않는데
        `_busy > 0` 이라 순회가 영원히 안 끝난다).
        """

        children: List[_Node] = []
        blocks = 0
        files = 0
        hardlinks: List[tuple] = []
        unreadable = 0
        logical = 0
        # 큰 파일 후보는 스레드 지역으로 모았다가 아래에서 한 번에 넘긴다 -
        # 파일마다 잠금을 잡을 이유가 없다.
        big: List[tuple] = []

        # 디렉터리 **자기 자신**이 차지하는 블록. `du` 는 이것을 센다.
        #
        # 빠뜨리고 있었다. 디렉터리를 만나면 자식 노드만 만들고 넘어갔기
        # 때문이다. 파일이 큰 트리에서는 티가 안 나지만, 디렉터리가 많으면
        # 그만큼 통째로 빠진다 - 실기에서 디렉터리 6만 개짜리 계정이 `du` 보다
        # 0.543% 작게 나왔고, 그 값이 두 번 다 소수점까지 같았다.
        #
        # 여기서 재면 루트를 포함해 **훑는 디렉터리마다 정확히 한 번** 세어진다.
        # 제외된 디렉터리(`.snapshot` 등)는 애초에 훑지 않으므로 세지 않는다 -
        # `du --exclude` 와 같은 결과다.
        try:
            blocks += disk_blocks(os.stat(node.path, follow_symlinks=False))
        except OSError:
            # 읽지 못한 것은 바로 아래 `scandir` 가 잡는다. 여기서 또 세면
            # 같은 실패가 두 번 계산된다.
            pass

        try:
            with os.scandir(node.path) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name in self.exclude_names:
                                continue
                            children.append(
                                _Node(path=entry.path, depth=node.depth + 1, parent=node)
                            )
                            continue
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        unreadable += 1
                        continue
                    # 대부분의 파일은 nlink==1 이라 중복 검사 자체를 건너뛴다.
                    if info.st_nlink > 1:
                        hardlinks.append(
                            ((info.st_dev, info.st_ino), disk_blocks(info), info.st_size)
                        )
                        continue
                    file_blocks = disk_blocks(info)
                    blocks += file_blocks
                    logical += info.st_size
                    files += 1
                    if file_blocks >= _MIN_BLOCKS:
                        big.append((file_blocks * 512 // 1024, entry.path))
        except OSError:
            unreadable += 1

        # 트리 갱신만 잠금 안에서 한다. 카운터는 스레드 지역으로 세고 끝에
        # 한 번만 합친다 - 디렉터리마다 잠금을 잡을 이유가 없다.
        with self._tree_lock:
            for key, block_count, size in hardlinks:
                if key in self._seen_inodes:
                    continue
                self._seen_inodes.add(key)
                blocks += block_count
                logical += size
                files += 1
            self.hardlink_files += len(hardlinks)
            self.logical_bytes += logical
            for candidate in big:
                self._offer_large(candidate)

            node.blocks += blocks
            node.pending_children += len(children)
            node.scanned = True
            self._all_nodes.extend(children)
            self._settle(node)

        return files, 1, unreadable, children

    def _offer_large(self, candidate: tuple) -> None:
        """큰 파일 후보 하나를 넣는다 (`_tree_lock` 안에서 부를 것).

        상한까지는 그냥 넣고, 넘으면 지금 가장 작은 것과만 견준다. 그래서
        파일이 몇 백만 개여도 비용이 일정하다."""

        if len(self._largest) < LARGEST_FILES_KEPT:
            heapq.heappush(self._largest, candidate)
        elif candidate[0] > self._largest[0][0]:
            heapq.heapreplace(self._largest, candidate)

    def _worker(self) -> None:
        files = dirs = unreadable = 0
        try:
            while True:
                if self._expired():
                    self._stop.set()
                    self._wake_all()   # 자고 있는 동료들도 내보낸다
                    return
                node = self._next()
                if node is None:
                    return
                children: List[_Node] = []
                try:
                    found, scanned, failed, children = self._scan_one(node)
                    files += found
                    dirs += scanned
                    unreadable += failed
                finally:
                    # 무슨 일이 있어도 큐를 반납한다. 안 하면 `_busy` 가 남아
                    # 순회가 끝나지 않는다.
                    self._finish(children)
        finally:
            # 지역으로 센 것을 끝에 한 번만 합친다.
            with self._tree_lock:
                self.file_count += files
                self.dir_count += dirs
                self.unreadable += unreadable

    # -- 실행 ---------------------------------------------------------
    def run(self) -> WalkOutcome:
        if self.timeout_seconds is not None:
            self._deadline = time.monotonic() + self.timeout_seconds

        with self._cond:
            self._queue.append(self._root_node)

        try:
            if self.workers == 1:
                self._worker()
            else:
                threads = [
                    threading.Thread(
                        target=self._worker, name=f"smvwp-walk-{index}", daemon=True
                    )
                    for index in range(self.workers)
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
        except OSError as exc:  # pragma: no cover - 방어적 처리
            return WalkOutcome(error_message=str(exc))

        return self._build_outcome()

    def _build_outcome(self) -> WalkOutcome:
        """완료된 디렉터리만 `du -k --max-depth=N` 모양으로 낸다."""

        entries = []
        for node in self._all_nodes:
            if not node.complete or node.depth > self.max_depth:
                continue
            entries.append((node.path, node.blocks * 512 // 1024))
        entries.sort(key=lambda item: item[0])

        root = self._root_node
        deepest = 0
        for node in self._all_nodes:
            if node.scanned and node.depth > deepest:
                deepest = node.depth
        return WalkOutcome(
            entries=entries,
            root_size_kb=(root.blocks * 512 // 1024) if root.complete else None,
            completed=root.complete,
            timed_out=self._stop.is_set() and not root.complete,
            partial=self.unreadable > 0,
            error_message=(
                f"읽지 못한 항목 {self.unreadable}개" if self.unreadable else None
            ),
            file_count=self.file_count,
            dir_count=self.dir_count,
            unreadable=self.unreadable,
            largest_files=sorted(self._largest, reverse=True),
            hardlink_files=self.hardlink_files,
            logical_bytes=self.logical_bytes,
            max_depth_seen=deepest,
        )


def walk_tree(
    path: str,
    timeout_seconds: Optional[float] = None,
    max_depth: int = 3,
    workers: int = 4,
    exclude_names: Optional[Set[str]] = None,
) -> WalkOutcome:
    """`du -k --max-depth=N` 자리에 그대로 쓸 수 있는 순회."""

    return ParallelWalker(
        path,
        max_depth=max_depth,
        workers=workers,
        timeout_seconds=timeout_seconds,
        exclude_names=exclude_names,
    ).run()
