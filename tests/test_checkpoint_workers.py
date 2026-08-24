"""계정 하나 안에서 체크포인트를 동시에 처리한다.

대상이 NFS라 병목은 파일당 `getattr` RPC 왕복인데, `du`는 한 줄로 걸어
언제나 요청이 하나만 떠 있다 (실기 RPC 슬롯 상한 128 중 1개). 같은 트리에서
병렬 walker(gdu)가 `du`보다 4배 빨랐던 것이 그 증거다.

계정 단위 병렬로는 이 문제가 안 풀린다 - 밤을 다 먹는 큰 계정 **하나**가
문제였고, 계정 병렬은 그 계정을 전혀 도와주지 못한다. 그래서 계정 안에서
체크포인트를 나눠 돈다.
"""

import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from smvwp import config as config_module
from smvwp import nightly_scan, scan_store
from tests import support


class ConcurrencyRecorder:
    """`du`가 실제로 겹쳐서 도는지 본다.

    호출이 들어오면 잠깐 머무르면서 그 순간 몇 개가 함께 떠 있는지 기록한다.
    머무르지 않으면 순식간에 끝나 겹칠 기회 자체가 없다."""

    def __init__(self, dwell=0.15):
        self.dwell = dwell
        self.peak = 0
        self.calls = 0
        self._active = 0
        self._lock = threading.Lock()

    def __call__(self, command, **kwargs):
        if command[-1] == "true":          # nice/ionice 탐색
            return support.completed(command)
        if "find" in command:
            return support.completed(command)
        if "du" not in command:
            raise AssertionError(f"예상치 못한 명령: {command}")

        with self._lock:
            self._active += 1
            self.calls += 1
            self.peak = max(self.peak, self._active)
        try:
            time.sleep(self.dwell)
            return support.completed(command, stdout=f"100\t{command[-1]}\n")
        finally:
            with self._lock:
                self._active -= 1


class CheckpointWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.account_path = self.root / "acct"
        self.account_path.mkdir(parents=True)

        self.config = config_module.load_config(self.data_dir)
        self.account = config_module.add_account(
            self.config, "big_account", str(self.account_path), data_dir=self.data_dir
        )
        config_module.save_config(self.data_dir, self.config)
        self.top_dirs = [f"{self.account_path}/dir{n}" for n in range(1, 9)]

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, workers):
        self.config.settings.checkpoint_workers = workers
        recorder = ConcurrencyRecorder()
        with patch("smvwp.detail_scan.subprocess.run", side_effect=recorder):
            summary = nightly_scan.run_nightly_scan(
                self.data_dir,
                self.config,
                bypass_window=True,
                clock=lambda: datetime(2026, 8, 24, 23, 0),
                top_level_lister=lambda path: list(self.top_dirs),
                baseline_warmup_seconds=0.0,
            )
        return summary, recorder

    # -- 테스트 --------------------------------------------------------
    def test_one_worker_never_overlaps(self):
        """기존 동작 - 한 번에 하나씩."""

        summary, recorder = self._run(workers=1)
        self.assertEqual(summary.status, nightly_scan.STATUS_COMPLETED)
        self.assertEqual(recorder.peak, 1)

    def test_workers_actually_overlap(self):
        """이게 이 기능의 존재 이유다 - 동시에 여러 `du`가 떠 있어야 한다."""

        summary, recorder = self._run(workers=4)
        self.assertEqual(summary.status, nightly_scan.STATUS_COMPLETED)
        self.assertGreater(
            recorder.peak, 1, "작업자를 4개로 뒀는데 du 가 겹치지 않았습니다"
        )
        self.assertLessEqual(recorder.peak, 4, "설정한 작업자 수를 넘었습니다")

    def test_every_checkpoint_runs_exactly_once(self):
        """두 작업자가 같은 체크포인트를 집으면 같은 곳을 두 번 센다."""

        summary, recorder = self._run(workers=4)
        self.assertEqual(recorder.calls, len(self.top_dirs))

        conn = scan_store.connect(self.data_dir)
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM scan_checkpoints "
                "WHERE account_id = ? GROUP BY status",
                (self.account.account_id,),
            ).fetchall()
        finally:
            conn.close()
        counts = {row["status"]: row["n"] for row in rows}
        self.assertEqual(counts.get("done"), len(self.top_dirs))
        self.assertIsNone(counts.get("pending"))

    def test_baseline_still_completes_the_generation(self):
        """병렬로 돌아도 세대 마감·이력 기록은 그대로여야 한다."""

        self._run(workers=4)
        conn = scan_store.connect(self.data_dir)
        try:
            state = scan_store.get_account_state(conn, self.account.account_id)
        finally:
            conn.close()
        self.assertEqual(state.last_completed_generation, 1)


class DispatcherTests(unittest.TestCase):
    """배정기 단위 - DB에 상태를 만들지 않고 중복 배정만 막는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.conn = scan_store.connect(self.data_dir)
        scan_store.seed_checkpoints(
            self.conn, "acct", scan_store.BASELINE, 1,
            [f"/acct/dir{n}" for n in range(1, 4)],
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _claim(self, dispatcher):
        return dispatcher.claim(self.conn, "acct", scan_store.BASELINE, 1)

    def test_never_hands_out_the_same_checkpoint_twice(self):
        dispatcher = nightly_scan._CheckpointDispatcher()
        first = self._claim(dispatcher)
        second = self._claim(dispatcher)
        third = self._claim(dispatcher)
        ids = {first["id"], second["id"], third["id"]}
        self.assertEqual(len(ids), 3)
        self.assertIsNone(self._claim(dispatcher), "3개뿐인데 4번째가 나왔습니다")

    def test_released_checkpoint_can_be_handed_out_again(self):
        dispatcher = nightly_scan._CheckpointDispatcher()
        first = self._claim(dispatcher)
        self._claim(dispatcher)
        self._claim(dispatcher)
        dispatcher.release(first["id"])
        again = self._claim(dispatcher)
        self.assertEqual(again["id"], first["id"])

    def test_busy_count_tracks_outstanding_work(self):
        """큐가 비어도 남이 일하는 중이면 끝난 게 아니다 - 분할로 자식이 는다."""

        dispatcher = nightly_scan._CheckpointDispatcher()
        self.assertEqual(dispatcher.busy(), 0)
        row = self._claim(dispatcher)
        self.assertEqual(dispatcher.busy(), 1)
        dispatcher.release(row["id"])
        self.assertEqual(dispatcher.busy(), 0)

    def test_no_running_state_is_written_to_the_db(self):
        """DESIGN 1부 7절: 체크포인트에 중간 상태를 만들지 않는다.

        만들면 프로세스가 죽었을 때 되돌리는 복구 로직이 필요해진다."""

        dispatcher = nightly_scan._CheckpointDispatcher()
        self._claim(dispatcher)
        statuses = {
            row["status"]
            for row in self.conn.execute("SELECT status FROM scan_checkpoints")
        }
        self.assertEqual(statuses, {scan_store.STATUS_PENDING})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
