import tempfile
import unittest
from pathlib import Path

from smvwp import scan_store


class ScanStoreTestCase(unittest.TestCase):
    """공통 setUp/tearDown - SQLite 연결을 tearDown에서 명시적으로 닫아야
    Windows에서 임시 디렉터리 정리 시 "다른 프로세스가 파일을 사용 중"
    오류가 나지 않는다 (POSIX는 열린 파일도 지울 수 있어 문제가 안 되지만,
    실제 배포 대상인 RHEL과 개발 PC인 Windows 둘 다에서 깨끗하게 통과하도록
    맞춘다)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.conn = scan_store.connect(self.data_dir)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()


class CheckpointQueueTests(ScanStoreTestCase):
    def test_seed_is_idempotent(self):
        conn = self.conn
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a", "/b"])
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a", "/b", "/c"])
        count = conn.execute("SELECT COUNT(*) FROM scan_checkpoints").fetchone()[0]
        self.assertEqual(count, 2)  # 두 번째 seed는 무시됨

    def test_is_seeded_false_before_and_true_after(self):
        conn = self.conn
        self.assertFalse(scan_store.is_seeded(conn, "acct-1", scan_store.BASELINE, 1))
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a"])
        self.assertTrue(scan_store.is_seeded(conn, "acct-1", scan_store.BASELINE, 1))

    def test_next_pending_fifo_and_none_when_exhausted(self):
        conn = self.conn
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a", "/b"])
        first = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        self.assertEqual(first["path"], "/a")
        scan_store.mark_done(conn, first["id"], size_kb=100)
        second = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        self.assertEqual(second["path"], "/b")
        scan_store.mark_done(conn, second["id"], size_kb=200)
        self.assertIsNone(scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1))

    def test_split_inserts_children_and_marks_parent_split(self):
        conn = self.conn
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/big"])
        parent = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        scan_store.mark_split(conn, parent["id"])
        scan_store.insert_children(conn, parent, ["/big/sub1", "/big/sub2"])

        row = conn.execute("SELECT status FROM scan_checkpoints WHERE id = ?", (parent["id"],)).fetchone()
        self.assertEqual(row["status"], "split")

        child = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        self.assertEqual(child["path"], "/big/sub1")
        self.assertEqual(child["depth"], 1)
        self.assertEqual(child["parent_id"], parent["id"])

    def test_mark_error_records_message(self):
        conn = self.conn
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/denied"])
        checkpoint = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        scan_store.mark_error(conn, checkpoint["id"], "permission denied")
        row = conn.execute(
            "SELECT status, error_message FROM scan_checkpoints WHERE id = ?", (checkpoint["id"],)
        ).fetchone()
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error_message"], "permission denied")


class AccountScanStateTests(ScanStoreTestCase):
    def test_fresh_account_working_generation_is_1(self):
        state = scan_store.get_account_state(self.conn, "acct-1")
        self.assertEqual(state.working_generation, 1)
        self.assertEqual(state.working_activity_pass, 1)

    def test_working_generation_advances_after_completion(self):
        conn = self.conn
        scan_store.mark_generation_completed(conn, "acct-1", 1)
        state = scan_store.get_account_state(conn, "acct-1")
        self.assertEqual(state.last_completed_generation, 1)
        self.assertEqual(state.working_generation, 2)

    def test_activity_pass_completion_records_cursor_and_total(self):
        conn = self.conn
        scan_store.mark_activity_pass_completed(conn, "acct-1", 1, "2026-07-31T00:00:00", 42)
        state = scan_store.get_account_state(conn, "acct-1")
        self.assertEqual(state.last_completed_activity_pass, 1)
        self.assertEqual(state.activity_cursor, "2026-07-31T00:00:00")
        self.assertEqual(state.last_activity_total_changed, 42)
        self.assertEqual(state.working_activity_pass, 2)


class BaselineResultsAndGrowthDeltaTests(ScanStoreTestCase):
    def _seed_and_complete(self, account_id, generation, sizes_by_path):
        """한 세대의 최상위 경로들을 한 번에 seed하고 모두 done으로 만든다.

        `seed_checkpoints`는 (계정, 종류, 세대)에 이미 체크포인트가 있으면
        아무 것도 하지 않는 멱등 함수라서, 같은 세대에 경로를 하나씩 나눠
        seed하면 두 번째 호출부터는 무시된다 - 그래서 여기서는 항상 한 세대
        분량을 한 번의 호출로 통째로 seed한다."""

        conn = self.conn
        scan_store.seed_checkpoints(conn, account_id, scan_store.BASELINE, generation, list(sizes_by_path))
        for _ in sizes_by_path:
            checkpoint = scan_store.next_pending(conn, account_id, scan_store.BASELINE, generation)
            scan_store.mark_done(conn, checkpoint["id"], size_kb=sizes_by_path[checkpoint["path"]])

    def test_save_and_top_paths(self):
        conn = self.conn
        self._seed_and_complete("acct-1", 1, {"/user/a/big": 5000, "/user/a/small": 10})
        rows = scan_store.leaf_results(conn, "acct-1", 1)
        scan_store.save_baseline_results(conn, "acct-1", 1, rows)

        top = scan_store.top_paths(conn, "acct-1", 1, limit=1)
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0]["path"], "/user/a/big")

    def test_growth_delta_matches_by_path_not_rank(self):
        conn = self.conn
        # 세대 1: a=100, b=200 (b가 1등)
        self._seed_and_complete("acct-1", 1, {"/a": 100, "/b": 200})
        scan_store.save_baseline_results(conn, "acct-1", 1, scan_store.leaf_results(conn, "acct-1", 1))

        # 세대 2: a=500(폭증, 1등으로), b=210(거의 그대로)
        self._seed_and_complete("acct-1", 2, {"/a": 500, "/b": 210})
        scan_store.save_baseline_results(conn, "acct-1", 2, scan_store.leaf_results(conn, "acct-1", 2))

        delta = scan_store.growth_delta(conn, "acct-1", 2, 1)
        by_path = {row["path"]: (row["current_kb"], row["previous_kb"]) for row in delta}
        self.assertEqual(by_path["/a"], (500, 100))
        self.assertEqual(by_path["/b"], (210, 200))
        # a가 훨씬 많이 늘었으니 delta 내림차순 1등이어야 한다 (순위가 바뀐
        # top-N 비교가 아니라 경로 기준 대조라는 것을 확인).
        self.assertEqual(delta[0]["path"], "/a")

    def test_growth_delta_new_path_has_null_previous(self):
        conn = self.conn
        self._seed_and_complete("acct-1", 1, {"/a": 100})
        scan_store.save_baseline_results(conn, "acct-1", 1, scan_store.leaf_results(conn, "acct-1", 1))

        self._seed_and_complete("acct-1", 2, {"/a": 100, "/new_dir": 999})
        scan_store.save_baseline_results(conn, "acct-1", 2, scan_store.leaf_results(conn, "acct-1", 2))

        delta = scan_store.growth_delta(conn, "acct-1", 2, 1)
        by_path = {row["path"]: row["previous_kb"] for row in delta}
        self.assertIsNone(by_path["/new_dir"])

    def test_prune_old_generations_keeps_only_recent(self):
        conn = self.conn
        for generation in (1, 2, 3):
            self._seed_and_complete("acct-1", generation, {"/a": generation * 100})
            scan_store.save_baseline_results(
                conn, "acct-1", generation, scan_store.leaf_results(conn, "acct-1", generation)
            )

        scan_store.prune_old_generations(conn, "acct-1", keep_last=2)

        remaining_generations = {
            row["generation"]
            for row in conn.execute(
                "SELECT DISTINCT generation FROM baseline_results WHERE account_id = ?", ("acct-1",)
            ).fetchall()
        }
        self.assertEqual(remaining_generations, {2, 3})


if __name__ == "__main__":
    unittest.main()
