"""walker 의 동시성 - 결과가 흔들리지 않고, 멈추지도 않아야 한다.

잠금을 둘로 나누고 폴링을 조건변수로 바꾼 뒤에 붙인 시험이다. 실측 근거:
한 프로세스 8스레드가 4스레드와 같은 시간이었는데 두 프로세스로 4스레드씩
돌리면 처리량이 2배였다 - 서버가 아니라 walker 안쪽이 한계였다는 뜻이다.

여기서 지키는 것은 성능이 아니라 **정확성**이다. 병렬 순회에서 값이 한 번이라도
흔들리면 증가 경로 비교가 통째로 거짓이 되고, 그건 느린 것보다 훨씬 나쁘다.
"""

import tempfile
import threading
import unittest
from pathlib import Path

from smvwp import walker

WORKER_COUNTS = (1, 2, 4, 8, 16)


def build_tree(root: Path, width: int, depth: int, files_per_dir: int) -> None:
    """폭과 깊이가 있는 트리를 만든다 (분기가 있어야 경합이 생긴다)."""

    def make(path: Path, level: int) -> None:
        path.mkdir(parents=True, exist_ok=True)
        for index in range(files_per_dir):
            (path / f"f{index}.bin").write_bytes(b"x" * (500 + index * 37))
        if level >= depth:
            return
        for index in range(width):
            make(path / f"d{index}", level + 1)

    make(root, 0)


class DeterminismTests(unittest.TestCase):
    """스레드 수와 실행 횟수가 결과를 바꾸면 안 된다."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "tree"
        build_tree(cls.root, width=3, depth=4, files_per_dir=6)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _walk(self, workers):
        return walker.walk_tree(str(self.root), max_depth=99, workers=workers)

    def test_same_total_for_every_worker_count(self):
        totals = {}
        for workers in WORKER_COUNTS:
            outcome = self._walk(workers)
            self.assertTrue(outcome.completed, f"x{workers} 가 완주하지 못했습니다")
            totals[workers] = outcome.root_size_kb
        distinct = set(totals.values())
        self.assertEqual(len(distinct), 1, f"스레드 수마다 합계가 달랐습니다: {totals}")

    def test_same_file_and_dir_counts(self):
        """카운터를 스레드 지역으로 세고 끝에 합치므로 특히 확인한다."""

        counts = {}
        for workers in WORKER_COUNTS:
            outcome = self._walk(workers)
            counts[workers] = (outcome.file_count, outcome.dir_count)
        self.assertEqual(
            len(set(counts.values())), 1, f"스레드 수마다 개수가 달랐습니다: {counts}"
        )

    def test_same_entries_for_every_worker_count(self):
        shapes = {}
        for workers in WORKER_COUNTS:
            outcome = self._walk(workers)
            shapes[workers] = tuple(sorted(outcome.entries))
        self.assertEqual(
            len(set(shapes.values())), 1, "스레드 수마다 출력이 달랐습니다"
        )

    def test_repeated_runs_are_stable(self):
        """경합은 가끔만 드러난다 - 같은 설정을 여러 번 돌려 본다."""

        results = [self._walk(8).root_size_kb for _ in range(8)]
        self.assertEqual(
            len(set(results)), 1, f"같은 설정인데 결과가 흔들렸습니다: {results}"
        )

    def test_every_directory_is_counted_exactly_once(self):
        """두 스레드가 같은 디렉터리를 집으면 그만큼 두 번 세어진다."""

        outcome = self._walk(16)
        paths = [path for path, _ in outcome.entries]
        self.assertEqual(len(paths), len(set(paths)), "같은 경로가 두 번 나왔습니다")


class TerminationTests(unittest.TestCase):
    """멈추지 않는 것이 잘못된 값보다 나쁠 때가 있다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "tree"
        build_tree(self.root, width=2, depth=3, files_per_dir=3)

    def tearDown(self):
        self.tmp.cleanup()

    def _run_with_deadline(self, workers, timeout_seconds=None, limit=30.0):
        """순회를 별도 스레드에서 돌리고 제한 시간 안에 끝나는지 본다."""

        done = threading.Event()
        box = {}

        def run():
            box["outcome"] = walker.walk_tree(
                str(self.root),
                timeout_seconds=timeout_seconds,
                max_depth=99,
                workers=workers,
            )
            done.set()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        finished = done.wait(timeout=limit)
        self.assertTrue(finished, f"x{workers} 순회가 {limit}초 안에 끝나지 않았습니다")
        return box["outcome"]

    def test_terminates_with_many_workers(self):
        """작업자가 대상 디렉터리보다 많아도 끝나야 한다.

        큐가 비어도 '다른 스레드가 일하는 중'이면 기다리는 구조라, 종료 조건이
        틀리면 여기서 영원히 멈춘다."""

        for workers in (16, 32):
            with self.subTest(workers=workers):
                outcome = self._run_with_deadline(workers)
                self.assertTrue(outcome.completed)

    def test_terminates_when_timeout_hits(self):
        outcome = self._run_with_deadline(8, timeout_seconds=0)
        self.assertFalse(outcome.completed)

    def test_single_directory_terminates(self):
        """자식이 하나도 없는 경우 - 종료 조건의 경계."""

        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        outcome = walker.walk_tree(str(empty), max_depth=9, workers=8)
        self.assertTrue(outcome.completed)
        self.assertEqual(outcome.dir_count, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
