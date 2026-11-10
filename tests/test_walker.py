"""파이썬 병렬 순회가 `du -k --max-depth=N`을 대체할 수 있는가.

속도보다 **값이 같은가**가 먼저다. 크기가 어긋나면 증가 경로 비교가 통째로
거짓이 되고, 그건 느린 것보다 훨씬 나쁘다.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import walker


def make_file(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


class WalkShapeTests(unittest.TestCase):
    """`du -k --max-depth=N`과 같은 모양으로 내는가."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "acct"
        make_file(self.root / "top.bin", 1000)
        make_file(self.root / "a" / "f1.bin", 2000)
        make_file(self.root / "a" / "b" / "f2.bin", 3000)
        make_file(self.root / "a" / "b" / "c" / "f3.bin", 4000)
        make_file(self.root / "a" / "b" / "c" / "d" / "f4.bin", 5000)

    def tearDown(self):
        self.tmp.cleanup()

    def _walk(self, **kwargs):
        return walker.walk_tree(str(self.root), **kwargs)

    def _paths(self, outcome):
        return sorted(p.replace("\\", "/") for p, _ in outcome.entries)

    def test_depth_limits_output_not_counting(self):
        """깊이는 **출력만** 제한한다. 크기 계산은 끝까지 간다.

        `du --max-depth`가 그렇게 동작하고, 야간 스캔의 DB 크기 예산이 이
        성질에 기대고 있다 (깊은 곳까지 세되 행은 얕은 것만 남긴다)."""

        shallow = self._walk(max_depth=1)
        deep = self._walk(max_depth=9)

        # 출력 개수는 다르지만
        self.assertLess(len(shallow.entries), len(deep.entries))
        # 루트 합계는 같아야 한다
        self.assertEqual(shallow.root_size_kb, deep.root_size_kb)

    def test_reports_every_directory_up_to_depth(self):
        outcome = self._walk(max_depth=2)
        root = str(self.root).replace("\\", "/")
        self.assertEqual(
            self._paths(outcome), sorted([root, f"{root}/a", f"{root}/a/b"])
        )

    def test_parent_total_includes_children(self):
        outcome = self._walk(max_depth=9)
        sizes = {p.replace("\\", "/"): kb for p, kb in outcome.entries}
        root = str(self.root).replace("\\", "/")
        self.assertGreaterEqual(sizes[root], sizes[f"{root}/a"])
        self.assertGreaterEqual(sizes[f"{root}/a"], sizes[f"{root}/a/b"])

    def test_worker_count_does_not_change_the_answer(self):
        """스레드 수는 속도만 바꿔야 한다. 값이 흔들리면 쓸 수 없다."""

        results = [self._walk(max_depth=9, workers=n) for n in (1, 2, 4, 8)]
        totals = {outcome.root_size_kb for outcome in results}
        self.assertEqual(len(totals), 1, f"스레드 수마다 합계가 달랐습니다: {totals}")
        shapes = {tuple(self._paths(outcome)) for outcome in results}
        self.assertEqual(len(shapes), 1, "스레드 수마다 출력 경로가 달랐습니다")

    def test_completed_when_nothing_interrupts(self):
        outcome = self._walk(max_depth=9)
        self.assertTrue(outcome.completed)
        self.assertFalse(outcome.timed_out)
        self.assertIsNotNone(outcome.root_size_kb)

    def test_counts_files_and_directories(self):
        outcome = self._walk(max_depth=9)
        self.assertEqual(outcome.file_count, 5)
        self.assertEqual(outcome.dir_count, 5)   # acct, a, b, c, d


class MemoryTests(unittest.TestCase):
    """깊이 제한 아래의 디렉터리는 끝난 뒤에 쥐고 있지 않는다.

    결과로 내는 것은 깊이 `max_depth` 까지다. 그런데 순회는 만난 디렉터리 노드를
    **전부** 목록에 담아 체크포인트가 끝날 때까지 쥐고 있었다 - 재 보니 디렉터리
    하나에 366바이트(경로 117자), 100만 개면 350MiB 이고 계정을 병렬로 돌면 그
    배수다. 수십 TB 계정의 최상위 폴더 하나가 디렉터리 수백만 개일 수 있다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "acct"
        # 깊이 1 에 폴더 둘, 그 아래로 깊이 5 까지 폴더 40개.
        for top in ("a", "b"):
            for leaf in range(5):
                make_file(self.root / top / f"l2_{leaf}" / "l3" / "l4" / "l5" / "f.bin", 1000)

    def tearDown(self):
        self.tmp.cleanup()

    def _walker(self, **kwargs):
        made = walker.ParallelWalker(str(self.root), **kwargs)
        return made, made.run()

    def test_only_reportable_directories_are_kept(self):
        made, outcome = self._walker(max_depth=1, workers=4)
        self.assertTrue(outcome.completed)
        self.assertEqual(outcome.dir_count, 1 + 2 + 2 * 5 * 4)
        # 쥐고 있는 것은 낼 것(루트와 깊이 1 의 둘)뿐이다.
        self.assertEqual(made.kept_nodes, 3)

    def test_the_answer_does_not_change(self):
        _made, shallow = self._walker(max_depth=1, workers=4)
        _made, deep = self._walker(max_depth=9, workers=4)
        sizes = dict(deep.entries)
        for path, size_kb in shallow.entries:
            self.assertEqual(size_kb, sizes[path], path)
        self.assertEqual(shallow.root_size_kb, deep.root_size_kb)
        # 가장 깊이 본 곳은 내는 깊이와 무관하게 센다 (진단이 쓴다).
        self.assertEqual(shallow.max_depth_seen, 5)
        self.assertEqual(deep.max_depth_seen, 5)


class ExclusionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "acct"
        make_file(self.root / "real" / "f.bin", 4000)
        make_file(self.root / ".snapshot" / "old" / "f.bin", 100000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_excluded_directory_is_not_descended(self):
        """스냅샷을 세면 같은 데이터를 세대 수만큼 중복해서 센다."""

        with_snap = walker.walk_tree(str(self.root), max_depth=9)
        without = walker.walk_tree(
            str(self.root), max_depth=9, exclude_names={".snapshot"}
        )
        self.assertGreater(with_snap.root_size_kb, without.root_size_kb)
        self.assertNotIn(
            ".snapshot", " ".join(p for p, _ in without.entries)
        )


class HardlinkTests(unittest.TestCase):
    """하드링크는 한 번만 센다 - `du`도 실행 하나 안에서는 그렇게 한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "acct"
        make_file(self.root / "a" / "original.bin", 20000)
        (self.root / "b").mkdir(parents=True, exist_ok=True)
        self.linked = False
        try:
            os.link(self.root / "a" / "original.bin", self.root / "b" / "linked.bin")
        except (OSError, NotImplementedError, AttributeError):
            return

        # 링크를 만들었다고 끝이 아니다. **`scandir`이 `st_nlink`/`st_ino`를
        # 채워 주는 플랫폼이어야** 중복을 가려낼 수 있다. Windows 의
        # `DirEntry.stat()`은 디렉터리 조회 때 받은 값을 그대로 주는데 거기에는
        # 이 둘이 없어(0) 판별이 불가능하다. 반입 대상인 리눅스에서는 실제
        # `stat()`을 하므로 제대로 나온다.
        with os.scandir(self.root / "a") as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                self.linked = info.st_nlink > 1 and info.st_ino != 0

    def tearDown(self):
        self.tmp.cleanup()

    def test_hardlink_counted_once(self):
        if not self.linked:
            self.skipTest(
                "이 플랫폼의 scandir 은 st_nlink/st_ino 를 주지 않습니다 "
                "(리눅스에서 확인해야 합니다)"
            )
        outcome = walker.walk_tree(str(self.root), max_depth=9)
        sizes = {p.replace("\\", "/"): kb for p, kb in outcome.entries}
        root = str(self.root).replace("\\", "/")
        # 두 번 셌다면 루트 합계가 두 배 가까이 나온다.
        self.assertLess(sizes[root], sizes[f"{root}/a"] * 2)
        self.assertEqual(outcome.file_count, 1)


class TimeoutTests(unittest.TestCase):
    """시간 초과여도 **완료된 디렉터리**는 살려야 한다.

    야간 스캔의 재개 설계가 이 성질에 기대고 있다. gdu 로 갈아탈 수 없었던
    이유도 이것이다 (트리를 다 만든 뒤 한 번에 내보내므로 잘리면 전손)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "acct"
        for index in range(40):
            make_file(self.root / f"d{index}" / "f.bin", 1000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_zero_timeout_does_not_crash_and_reports_incomplete(self):
        outcome = walker.walk_tree(str(self.root), timeout_seconds=0, max_depth=9)
        self.assertFalse(outcome.completed)
        self.assertIsNone(outcome.root_size_kb)

    def test_generous_timeout_completes(self):
        outcome = walker.walk_tree(str(self.root), timeout_seconds=120, max_depth=9)
        self.assertTrue(outcome.completed)

    def test_only_complete_directories_are_reported(self):
        """미완성 디렉터리가 섞여 나오면 그 값을 저장했다가 틀린 증가를 본다."""

        outcome = walker.walk_tree(str(self.root), timeout_seconds=0, max_depth=9)
        # 완료 안 된 루트는 목록에 없어야 한다.
        root = str(self.root).replace("\\", "/")
        self.assertNotIn(root, [p.replace("\\", "/") for p, _ in outcome.entries])


class TimeoutKeepsFinishedSubtreesTests(unittest.TestCase):
    """시간이 다 돼 끊겨도, 그때까지 **끝난 하위 폴더는 남아야** 한다.

    야간 스캔은 끊긴 체크포인트에서 끝난 것을 저장하고 안 끝난 자식만 다시 잰다
    (`detail_scan.process_one_checkpoint` - "버려지는 작업이 없다"). `du` 는 깊이
    우선이라 그 말이 맞는데, 이 순회는 큐 앞에서 꺼내는 **넓이 우선**이었다. 얕은
    층부터 훑으므로 가장 깊은 층을 다 볼 때까지 어떤 폴더도 끝나지 않는다 - 15분을
    쓰고 끊기면 끝난 것이 거의 없어 그 15분(과 파일서버가 한 일)이 통째로 버려지고,
    자식들을 처음부터 다시 쟀다.

    뒤에서 꺼내면(깊이 우선) 하위 폴더를 하나씩 끝내 가므로, 끊겨도 끝난 만큼 남는다."""

    DEPTH = 4

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "acct"
        # 최상위 폴더 셋, 저마다 깊이 4 의 사슬 하나.
        for top in ("a", "b", "c"):
            chain = self.root / top
            for level in range(1, self.DEPTH):
                chain = chain / f"d{level}"
            make_file(chain / "f.bin", 1000)

    def tearDown(self):
        self.tmp.cleanup()

    def _walk_until(self, scans: int):
        """디렉터리를 `scans` 개 읽고 시간이 다 된 것으로 친다 (한 스레드라 순서가 정해진다)."""

        made = walker.ParallelWalker(str(self.root), max_depth=1, workers=1)
        calls = {"n": 0}

        def expired():
            calls["n"] += 1
            return calls["n"] > scans

        with patch.object(made, "_expired", side_effect=expired):
            return made.run()

    def test_a_finished_top_folder_survives_the_timeout(self):
        # 루트 + 사슬 하나(깊이 4) = 5 개를 읽으면 최상위 폴더 하나는 끝났어야 한다.
        outcome = self._walk_until(1 + self.DEPTH)
        self.assertTrue(outcome.timed_out)
        finished = [Path(path).name for path, _kb in outcome.entries]
        self.assertEqual(len(finished), 1, finished)
        self.assertIn(finished[0], ("a", "b", "c"))

    def test_each_further_chain_finishes_another(self):
        outcome = self._walk_until(1 + 2 * self.DEPTH)
        self.assertEqual(len(outcome.entries), 2)

    def test_the_waiting_line_stays_short(self):
        """넓이 우선이면 가장 넓은 층이 통째로 줄을 선다 (디렉터리 수백만 개짜리 층이면
        그만큼의 메모리). 깊이 우선이면 지금 내려가는 길의 형제들뿐이다."""

        wide = Path(self.tmp.name) / "wide"
        for index in range(50):
            for branch in range(4):
                make_file(wide / f"t{index:02d}" / f"x{branch}" / "y" / "f.bin", 10)
        made = walker.ParallelWalker(str(wide), max_depth=1, workers=1)
        longest = {"n": 0}
        original = made._next

        def watching():
            longest["n"] = max(longest["n"], len(made._queue))
            return original()

        with patch.object(made, "_next", side_effect=watching):
            outcome = made.run()
        self.assertTrue(outcome.completed)
        # 최상위 50 개와 지금 내려가는 폴더의 형제 넷까지다. 넓이 우선이면 둘째 층
        # 200 개가 통째로 줄을 선다.
        self.assertLessEqual(longest["n"], 60)


class UnreadableTests(unittest.TestCase):
    def test_missing_path_is_reported_not_raised(self):
        """읽을 수 없는 곳이 있어도 순회 전체가 죽으면 안 된다."""

        outcome = walker.walk_tree("/definitely/not/here", max_depth=2)
        self.assertTrue(outcome.partial)
        self.assertGreater(outcome.unreadable, 0)


class VanishedEntriesTests(unittest.TestCase):
    """훑는 사이에 지워진 것은 '읽지 못한 것' 이 아니다.

    밤에 도는 작업이 임시 파일을 만들고 지우는 트리에서는 늘 있는 일이다. 예전에는
    이것을 권한 문제와 같이 세어 그 경로가 '일부만 읽힘' 이 되었고, 화면과 문서는
    "권한이 없는 폴더가 섞여 있다" 며 관리자에게 권한을 요청하라고 안내했다.
    없어진 것은 세지 않는 것이 맞는 값이다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        make_file(self.root / "a" / "keep.dat", 4096)
        make_file(self.root / "a" / "gone.dat", 4096)
        (self.root / "b").mkdir()

    def walk_with(self, failing):
        """`failing(경로)` 가 예외를 주면 그 항목을 읽을 때 그 예외가 난다."""

        real_scandir = os.scandir

        class Entry:
            def __init__(self, entry):
                self._entry = entry
                self.name, self.path = entry.name, entry.path

            def is_dir(self, follow_symlinks=True):
                return self._entry.is_dir(follow_symlinks=follow_symlinks)

            def stat(self, follow_symlinks=True):
                error = failing(self.path)
                if error is not None:
                    raise error
                return self._entry.stat(follow_symlinks=follow_symlinks)

        class Listing:
            def __init__(self, path):
                error = failing(str(path))
                if error is not None:
                    raise error
                self._inner = real_scandir(path)

            def __enter__(self):
                return (Entry(entry) for entry in self._inner.__enter__())

            def __exit__(self, *exc):
                return self._inner.__exit__(*exc)

        with patch("smvwp.walker.os.scandir", side_effect=Listing):
            return walker.walk_tree(str(self.root), max_depth=2, workers=1)

    def test_a_file_deleted_mid_walk_is_not_unreadable(self):
        gone = str(self.root / "a" / "gone.dat")
        outcome = self.walk_with(lambda p: FileNotFoundError(p) if p == gone else None)
        self.assertEqual(outcome.unreadable, 0)
        self.assertFalse(outcome.partial)
        self.assertEqual(outcome.file_count, 1)

    def test_a_directory_deleted_after_it_was_listed_is_not_unreadable(self):
        gone = str(self.root / "b")
        outcome = self.walk_with(lambda p: FileNotFoundError(p) if p == gone else None)
        self.assertEqual(outcome.unreadable, 0)
        self.assertFalse(outcome.partial)

    def test_permission_problems_still_count(self):
        locked = str(self.root / "b")
        outcome = self.walk_with(lambda p: PermissionError(p) if p == locked else None)
        self.assertEqual(outcome.unreadable, 1)
        self.assertTrue(outcome.partial)


class DirectoryOwnBlocksTests(unittest.TestCase):
    """디렉터리 자기 자신이 차지하는 블록도 세야 `du` 와 맞는다.

    빠뜨리고 있었다. 파일이 큰 트리에서는 티가 안 나지만 디렉터리가 많으면
    그만큼 통째로 빠진다 - 실기에서 디렉터리 6만 개짜리 계정이 `du` 보다
    0.543% 작게 나왔고, 그 값이 두 번 다 소수점까지 같았다(= 우연이 아니라
    규칙적인 누락).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "tree"

    def tearDown(self):
        self.tmp.cleanup()

    def _expected_kb(self, root):
        """`du` 가 세는 것과 같은 방식으로 손으로 더한 값."""

        total = 0
        for current, dirnames, filenames in os.walk(root):
            total += walker.disk_blocks(os.stat(current))
            for name in filenames:
                total += walker.disk_blocks(os.stat(os.path.join(current, name)))
        return total * 512 // 1024

    def test_empty_directories_are_not_free(self):
        for index in range(5):
            (self.root / f"d{index}").mkdir(parents=True)
        outcome = walker.walk_tree(str(self.root), max_depth=0, workers=2)
        self.assertEqual(outcome.root_size_kb, self._expected_kb(self.root))

    def test_nested_tree_matches_a_manual_sum(self):
        for a in range(3):
            for b in range(3):
                target = self.root / f"a{a}" / f"b{b}"
                target.mkdir(parents=True)
                (target / "f.dat").write_bytes(b"x" * 3000)
        outcome = walker.walk_tree(str(self.root), max_depth=0, workers=4)
        self.assertEqual(outcome.root_size_kb, self._expected_kb(self.root))

    def test_each_directory_is_counted_once_not_per_thread(self):
        """스레드 수를 바꿔도 합계는 같아야 한다."""

        for index in range(12):
            (self.root / f"d{index}" / "inner").mkdir(parents=True)
        sizes = {
            workers: walker.walk_tree(
                str(self.root), max_depth=0, workers=workers
            ).root_size_kb
            for workers in (1, 2, 8)
        }
        self.assertEqual(len(set(sizes.values())), 1, sizes)

    def test_excluded_directories_add_nothing(self):
        """`du --exclude` 와 같아야 한다 - 건너뛴 디렉터리는 자기 블록도 안 센다."""

        (self.root / "real").mkdir(parents=True)
        plain = walker.walk_tree(str(self.root), max_depth=0, workers=1).root_size_kb

        (self.root / ".snapshot" / "old").mkdir(parents=True)
        with_snapshot = walker.walk_tree(
            str(self.root), max_depth=0, workers=1, exclude_names={".snapshot"}
        ).root_size_kb
        self.assertEqual(with_snapshot, plain)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
