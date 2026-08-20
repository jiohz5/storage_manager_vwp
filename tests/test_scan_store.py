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


class FailedPathsTests(ScanStoreTestCase):
    """재지 못한 경로를 사유와 함께 돌려주는지.

    이게 없으면 화면에는 "기준선이 아직 없습니다"만 뜬다 - 스캔이 돌긴 했고
    전부 실패했다는 사실도, 그 이유도 사용자에게 닿지 않는다."""

    def test_reports_errored_paths_with_reason(self):
        conn = self.conn
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a", "/b"])
        first = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        scan_store.mark_error(conn, first["id"], "읽기 권한이 없어 크기를 잴 수 없습니다")
        second = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        scan_store.mark_done(conn, second["id"], size_kb=10)

        failed = scan_store.failed_paths(conn, "acct-1", 1)
        self.assertEqual(len(failed), 1)
        path, message = failed[0]
        self.assertEqual(path, first["path"])
        self.assertIn("읽기 권한", message)
        self.assertEqual(scan_store.failed_count(conn, "acct-1", 1), 1)

    def test_no_failures_reports_nothing(self):
        conn = self.conn
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a"])
        checkpoint = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        scan_store.mark_done(conn, checkpoint["id"], size_kb=10)

        self.assertEqual(scan_store.failed_paths(conn, "acct-1", 1), [])
        self.assertEqual(scan_store.failed_count(conn, "acct-1", 1), 0)

    def test_limit_caps_the_listing_but_not_the_count(self):
        """전부 실패했을 때 목록이 화면을 뒤덮지 않아야 한다 (개수는 정확히)."""

        conn = self.conn
        paths = [f"/a/{i}" for i in range(30)]
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, paths)
        while True:
            checkpoint = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
            if checkpoint is None:
                break
            scan_store.mark_error(conn, checkpoint["id"], "Permission denied")

        self.assertEqual(len(scan_store.failed_paths(conn, "acct-1", 1, limit=5)), 5)
        self.assertEqual(scan_store.failed_count(conn, "acct-1", 1), 30)


if __name__ == "__main__":
    unittest.main()


class CurrentTargetTests(ScanStoreTestCase):
    """지금 훑고 있는 경로를 실행 행에 남긴다.

    체크포인트에 'running' 상태를 새로 만들지 않은 것은 의도다 - 재개
    가능성이 "pending만 보면 된다"는 단순한 불변식에 기대고 있는데, 중간
    상태를 넣으면 프로세스가 죽었을 때 그 행을 되돌리는 복구 로직이 따로
    필요해진다.
    """

    def test_records_and_clears_current_path(self):
        conn = self.conn
        scan_store.start_run(conn, "run-1", "gui")

        scan_store.set_current_target(conn, "run-1", "acct-1", scan_store.BASELINE, "/user/a/x")
        row = scan_store.latest_run(conn)
        self.assertEqual(row["current_path"], "/user/a/x")
        self.assertEqual(row["current_kind"], scan_store.BASELINE)
        self.assertEqual(row["current_account_id"], "acct-1")
        self.assertIsNotNone(row["current_started_at"])

        # 끝나면 비운다 - 남아 있으면 "아직 그걸 하고 있다"로 오해된다.
        scan_store.set_current_target(conn, "run-1", None, None, None)
        row = scan_store.latest_run(conn)
        self.assertIsNone(row["current_path"])
        self.assertIsNone(row["current_started_at"])

    def test_checkpoint_status_is_untouched(self):
        """위치 기록이 체크포인트 상태를 건드리면 재개 규칙이 깨진다."""

        conn = self.conn
        scan_store.start_run(conn, "run-1", "gui")
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a", "/b"])
        before = scan_store.checkpoint_progress(conn, "acct-1", scan_store.BASELINE, 1)

        scan_store.set_current_target(conn, "run-1", "acct-1", scan_store.BASELINE, "/a")
        after = scan_store.checkpoint_progress(conn, "acct-1", scan_store.BASELINE, 1)

        self.assertEqual(before, after)
        self.assertEqual(after["pending"], 2)


class CheckpointProgressTests(ScanStoreTestCase):
    def test_counts_by_status(self):
        conn = self.conn
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a", "/b", "/c"])
        first = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        scan_store.mark_done(conn, first["id"], size_kb=100)
        second = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        scan_store.mark_error(conn, second["id"], "권한 없음")

        counts = scan_store.checkpoint_progress(conn, "acct-1", scan_store.BASELINE, 1)
        self.assertEqual(counts["done"], 1)
        self.assertEqual(counts["error"], 1)
        self.assertEqual(counts["pending"], 1)
        self.assertEqual(counts["total"], 3)

    def test_recent_puts_processed_first_and_pending_last(self):
        """알고 싶은 것은 '어디까지 했나'이고 대기 목록은 그 다음이다."""

        conn = self.conn
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a", "/b", "/c"])
        first = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        scan_store.mark_done(conn, first["id"], size_kb=100)

        rows = scan_store.recent_checkpoints(conn, "acct-1", scan_store.BASELINE, 1)
        self.assertEqual(rows[0]["path"], "/a")
        self.assertEqual([r["status"] for r in rows[1:]], ["pending", "pending"])


class LastProcessedTests(ScanStoreTestCase):
    def test_returns_most_recent_processed_checkpoint(self):
        conn = self.conn
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a", "/b", "/c"])
        first = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        scan_store.mark_done(conn, first["id"], size_kb=100)
        second = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        scan_store.mark_error(conn, second["id"], "권한 없음")

        last = scan_store.last_processed(conn, "acct-1", scan_store.BASELINE, 1)
        self.assertEqual(last["path"], "/b")
        self.assertEqual(last["status"], "error")

    def test_none_when_nothing_processed_yet(self):
        conn = self.conn
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a"])
        self.assertIsNone(scan_store.last_processed(conn, "acct-1", scan_store.BASELINE, 1))
