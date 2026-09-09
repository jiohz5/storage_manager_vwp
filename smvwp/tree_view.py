"""계정 안을 펼쳐 보는 트리를 만든다 (Qt 없음).

## 왜 필요한가

지금 화면은 계정 루트(깊이 0)만 보여 준다. 그래서 "LAYOUT 이 300GB" 까지는
알지만 **그 300GB 가 어디에 있는지** 는 알 수 없다. 정작 사람이 하고 싶은
일은 그다음이다 - 어느 폴더가 먹고 있는지 찾아 들어가는 것.

데이터는 이미 있다. 야간 스캔이 `du --max-depth=3` 으로 디렉터리마다 크기를
남기고, 시간 초과로 쪼개진 서브트리는 자기 루트에서 다시 3단계를 남기므로
깊은 곳도 덮여 있다. **다시 스캔하지 않고** 그 기록만 읽어 트리로 세운다.

## 여기서 조심하는 것

`du` 의 값은 **자기 자신을 포함한 합계**다. 부모 밑에 자식들을 그냥 늘어놓으면
자식 합이 부모보다 작아서 "나머지는 어디 갔나" 가 된다. 그 차이는 대개
**이 디렉터리에 직접 있는 파일**이므로, 지어내지 말고 한 줄로 세워 보여 준다.

그리고 모르는 것은 모른다고 둔다 - 나머지 줄의 '지난 스캔 대비'는 계산하지
않는다. 이전 세대의 자식 구성이 지금과 같다는 보장이 없어서, 그럴듯하지만
틀릴 수 있는 수가 되기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .large_files import GROWTH_ALERT_RATIO

# 줄의 성격. 화면은 이것으로 아이콘과 문구를 고른다.
DIRECTORY = "dir"
FILE = "file"
REST = "rest"     # 부모 크기에서 하위 디렉터리 합을 뺀 나머지
MORE = "more"     # 너무 많아 접은 하위 디렉터리들

# 한 디렉터리 밑에 펼쳐 보여 줄 하위 디렉터리 수.
#
# 계정 하나에 사용자 폴더가 수천 개인 경우가 있다. 전부 세우면 화면도 못 읽고
# 트리 위젯도 무거워진다. 큰 것부터 이만큼만 보여 주고 나머지는 한 줄로 접는다.
MAX_CHILDREN_SHOWN = 100

# 이보다 작은 '나머지'는 줄을 만들지 않는다.
#
# `du` 는 디렉터리 자기 inode 블록도 부모 합계에 넣으므로, 하위 폴더가 많으면
# 그것만으로 몇십 KB 씩 나머지가 생긴다. 300GB 옆에 붙은 40KB 짜리 줄은
# 읽는 사람의 눈만 뺏는다.
REST_MIN_KB = 1024   # 1MB

# '많이 늘었다'로 볼 최소 증가량.
#
# 비율만 보면 4KB -> 8KB 도 2배라 온 트리가 빨개진다. 사람이 손댈 값어치가
# 있는 크기여야 알림이 알림 구실을 한다.
GROWTH_MIN_DELTA_KB = 100 * 1024   # 100MB


def _parent(path: str) -> Optional[str]:
    """POSIX 경로의 부모. 더 올라갈 곳이 없으면 None.

    스캔 대상은 NFS 경로라 항상 `/` 구분자다 - 개발 PC 가 Windows 여도
    `os.path` 를 쓰면 안 된다."""

    trimmed = path.rstrip("/")
    if "/" not in trimmed:
        return None
    head = trimmed.rsplit("/", 1)[0]
    if not head:
        return "/" if trimmed != "/" else None
    return head


@dataclass
class TreeNode:
    """트리 한 줄."""

    path: str
    label: str
    size_kb: int
    kind: str = DIRECTORY
    previous_kb: Optional[int] = None
    # 이전 세대와 견줄 수 있었는가. False 면 `previous_kb` 가 None 인 것이
    # "그때 없었다"가 아니라 "볼 이전 스캔이 아예 없다"는 뜻이다.
    compared: bool = False
    children: List["TreeNode"] = field(default_factory=list)
    hidden_count: int = 0            # MORE 줄이 접고 있는 개수
    account_total_kb: Optional[int] = None

    @property
    def delta_kb(self) -> Optional[int]:
        if self.previous_kb is None:
            return None
        return self.size_kb - self.previous_kb

    @property
    def is_new(self) -> bool:
        """지난 스캔에는 없던 경로."""

        return self.compared and self.previous_kb is None

    @property
    def share_pct(self) -> Optional[float]:
        if not self.account_total_kb:
            return None
        return self.size_kb / self.account_total_kb * 100.0

    @property
    def grew_sharply(self) -> bool:
        delta = self.delta_kb
        if delta is None or delta < GROWTH_MIN_DELTA_KB:
            return False
        if not self.previous_kb:
            return False
        return self.size_kb >= self.previous_kb * GROWTH_ALERT_RATIO

    @property
    def is_notable(self) -> bool:
        """눈에 띄게 표시할 값어치가 있는가."""

        if self.kind in (REST, MORE):
            return False
        if self.grew_sharply:
            return True
        return self.is_new and self.size_kb >= GROWTH_MIN_DELTA_KB


def _as_pairs(source) -> Iterable:
    if source is None:
        return ()
    if hasattr(source, "items"):
        return source.items()
    return source


def _size_map(source) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in _as_pairs(source):
        path, size_kb = row[0], row[1]
        key = str(path).rstrip("/") or "/"
        out[key] = int(size_kb)
    return out


def build_tree(
    entries,
    previous=None,
    large_files=(),
    account_total_kb: Optional[int] = None,
) -> List[TreeNode]:
    """`(경로, KB)` 목록을 트리로 세운다.

    - `entries` - 이번 세대 (`scan_store.tree_entries` 가 주는 행도 그대로 받는다).
    - `previous` - 이전 세대. **None 이면 견줄 것이 없다**는 뜻이고, 빈 목록이면
      "이전 스캔은 있었는데 이 경로들이 없었다"는 뜻이다. 둘은 다르다.
    - `large_files` - `(경로, 지금 KB, 이전 KB)`. 자기 디렉터리 밑에 붙는다.
    - `account_total_kb` - 비중 계산 기준. 없으면 뿌리 합계를 쓴다.
    """

    sizes = _size_map(entries)
    if not sizes:
        return []
    previous_sizes = None if previous is None else _size_map(previous)

    nodes: Dict[str, TreeNode] = {}
    for path, size_kb in sizes.items():
        nodes[path] = TreeNode(
            path=path,
            label=path,
            size_kb=size_kb,
            previous_kb=None if previous_sizes is None else previous_sizes.get(path),
            compared=previous_sizes is not None,
        )

    # -- 부모 밑에 건다 --------------------------------------------------
    #
    # 깊이 칸이 아니라 **경로**로 세운다. 쪼개진 체크포인트는 자기 루트에서
    # 깊이를 다시 0부터 세기 때문에, 깊이 칸으로 세우면 트리가 어긋난다.
    roots: List[TreeNode] = []
    for path in sorted(nodes):
        node = nodes[path]
        ancestor = _parent(path)
        # 중간 디렉터리가 빠져 있어도 트리가 끊기지 않게 위로 올라가며 찾는다.
        while ancestor is not None and ancestor not in nodes:
            ancestor = _parent(ancestor)
        if ancestor is None:
            roots.append(node)
        else:
            node.label = path[len(ancestor):].lstrip("/") or path
            nodes[ancestor].children.append(node)

    if account_total_kb is None:
        account_total_kb = sum(node.size_kb for node in roots) or None

    # -- 큰 파일을 가장 가까운 조상 밑으로 -------------------------------
    files_by_owner: Dict[str, List[TreeNode]] = {}
    for row in large_files:
        raw_path, size_kb = str(row[0]), int(row[1])
        prev_kb = row[2] if len(row) > 2 else None
        owner = _parent(raw_path)
        while owner is not None and owner not in nodes:
            owner = _parent(owner)
        if owner is None:
            continue
        files_by_owner.setdefault(owner, []).append(
            TreeNode(
                path=raw_path,
                label=raw_path[len(owner):].lstrip("/") or raw_path,
                size_kb=size_kb,
                kind=FILE,
                previous_kb=prev_kb,
                compared=previous_sizes is not None,
            )
        )

    # -- 나머지 줄, 접은 줄, 파일 줄 -------------------------------------
    for path, node in nodes.items():
        node.account_total_kb = account_total_kb
        dir_children = sorted(
            node.children, key=lambda item: (-item.size_kb, item.label)
        )
        dir_total = sum(item.size_kb for item in dir_children)

        shown = dir_children[:MAX_CHILDREN_SHOWN]
        hidden = dir_children[MAX_CHILDREN_SHOWN:]
        if hidden:
            shown.append(
                TreeNode(
                    path=node.path,
                    label="",
                    size_kb=sum(item.size_kb for item in hidden),
                    kind=MORE,
                    hidden_count=len(hidden),
                    account_total_kb=account_total_kb,
                )
            )

        bucket = node
        if dir_children:
            remainder = node.size_kb - dir_total
            if remainder >= REST_MIN_KB:
                rest_node = TreeNode(
                    path=node.path,
                    label="",
                    size_kb=remainder,
                    kind=REST,
                    account_total_kb=account_total_kb,
                )
                shown.append(rest_node)
                bucket = rest_node

        files = files_by_owner.get(path)
        if files:
            files.sort(key=lambda item: (-item.size_kb, item.label))
            for item in files:
                item.account_total_kb = account_total_kb
            if bucket is node:
                shown.extend(files)
            else:
                bucket.children = files

        node.children = shown

    roots.sort(key=lambda item: (-item.size_kb, item.path))
    return roots


def count_directories(roots: List[TreeNode]) -> int:
    """실제 디렉터리 줄 수 (나머지/접은 줄은 빼고)."""

    total = 0
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node.kind == DIRECTORY:
            total += 1
        stack.extend(node.children)
    return total
