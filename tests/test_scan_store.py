import tempfile
import unittest
from pathlib import Path

from smvwp import scan_store
from tests import support


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

    def test_two_threads_creating_the_same_row_do_not_collide(self):
        """읽고 나서 넣기 사이에 다른 연결이 먼저 넣는 경우.

        창을 열면 헬스체크(대시보드)와 스캔 상태 조회가 새 계정에 대해 동시에
        여기로 온다. 늦은 쪽이 UNIQUE 위반으로 죽어 홈의 우선순위 카드가
        "계산하지 못했습니다" 로 떴다."""

        # addCleanup 으로 닫으면 tearDown 의 임시 디렉터리 삭제보다 늦어,
        # 윈도우에서는 열린 DB 파일을 못 지워 실패한다.
        other = scan_store.connect(self.data_dir)

        class Cursor:
            def __init__(self, rows):
                self.rows = rows

            def fetchone(self):
                return self.rows[0] if self.rows else None

        class Racing:
            """첫 SELECT 가 '없다' 를 돌려준 직후에 다른 연결이 끼어든다."""

            def __init__(self, conn):
                self.conn = conn
                self.raced = False

            def execute(self, sql, params=()):
                cursor = self.conn.execute(sql, params)
                if self.raced or not sql.lstrip().upper().startswith("SELECT"):
                    return cursor
                self.raced = True
                rows = cursor.fetchall()
                other.execute(
                    "INSERT INTO account_scan_state (account_id) VALUES (?)", ("acct-1",)
                )
                other.commit()
                return Cursor(rows)

            def commit(self):
                self.conn.commit()

        try:
            state = scan_store.get_account_state(Racing(self.conn), "acct-1")
        finally:
            other.close()
        self.assertEqual(state.working_generation, 1)

    def test_working_generation_advances_after_completion(self):
        conn = self.conn
        scan_store.mark_generation_completed(conn, "acct-1", 1)
        state = scan_store.get_account_state(conn, "acct-1")
        self.assertEqual(state.last_completed_generation, 1)
        self.assertEqual(state.working_generation, 2)

    def test_leftover_activity_checkpoints_are_cleaned_up(self):
        """없앤 기능이 남긴 행을 치운다.

        연결할 때마다 지우면 GUI 가 5초마다 쓰기 트랜잭션을 여는데, NFS 위에서는
        그 하나가 왕복이다. 밤마다 계정별로 한 번 도는 정리 자리에 붙였다."""

        conn = self.conn
        conn.execute(
            "INSERT INTO scan_checkpoints "
            "(account_id, kind, generation, path, depth, status) "
            "VALUES (?, 'activity', 1, '/a', 0, 'pending')",
            ("acct-1",),
        )
        conn.commit()
        scan_store.prune_old_generations(conn, "acct-1", keep_last=2)
        left = conn.execute(
            "SELECT COUNT(*) FROM scan_checkpoints WHERE kind = 'activity'"
        ).fetchone()[0]
        self.assertEqual(left, 0)


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
        support.save_checkpoint_results(conn, "acct-1", 1)

        top = scan_store.top_paths(conn, "acct-1", 1, limit=1)
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0]["path"], "/user/a/big")

    def test_growth_delta_matches_by_path_not_rank(self):
        conn = self.conn
        # 세대 1: a=100, b=200 (b가 1등)
        self._seed_and_complete("acct-1", 1, {"/a": 100, "/b": 200})
        support.save_checkpoint_results(conn, "acct-1", 1)

        # 세대 2: a=500(폭증, 1등으로), b=210(거의 그대로)
        self._seed_and_complete("acct-1", 2, {"/a": 500, "/b": 210})
        support.save_checkpoint_results(conn, "acct-1", 2)

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
        support.save_checkpoint_results(conn, "acct-1", 1)

        self._seed_and_complete("acct-1", 2, {"/a": 100, "/new_dir": 999})
        support.save_checkpoint_results(conn, "acct-1", 2)

        delta = scan_store.growth_delta(conn, "acct-1", 2, 1)
        by_path = {row["path"]: row["previous_kb"] for row in delta}
        self.assertIsNone(by_path["/new_dir"])

    def test_prune_old_generations_keeps_only_recent(self):
        conn = self.conn
        for generation in (1, 2, 3):
            self._seed_and_complete("acct-1", generation, {"/a": generation * 100})
            support.save_checkpoint_results(conn, "acct-1", generation)

        scan_store.prune_old_generations(conn, "acct-1", keep_last=2)

        remaining_generations = {
            row["generation"]
            for row in conn.execute(
                "SELECT DISTINCT generation FROM baseline_results WHERE account_id = ?", ("acct-1",)
            ).fetchall()
        }
        self.assertEqual(remaining_generations, {2, 3})


class SplitNightTests(ScanStoreTestCase):
    """시간 초과로 나눠 잰 밤.

    `/acct/a` 가 시간 초과로 끊겨 먼저 끝난 `x` 만 남고 `y`, `z` 는 다시 큐에 들어가
    따로 재진다. 그 밤에는 `/acct/a` 자체의 줄이 없다. 실제 크기는 두 밤 모두 같다
    (아무것도 안 늘었다) - 그런데 예전에는 나눠 잰 밤의 합계에서 `x` 가 빠졌고,
    다음 밤에는 `/acct/a` 가 통째로 '새 경로'가 되어 급증 알림까지 갔다."""

    X, Y, Z, B, A_OWN = 3000, 2000, 1000, 500, 50
    TOPS = ["/acct/a", "/acct/b"]

    def _split_night(self, generation):
        X, Y, Z, B = self.X, self.Y, self.Z, self.B
        support.scan_pass(
            self.conn, "acct-1", generation, self.TOPS,
            {
                "/acct/a": support.cut({"/acct/a/x/p": X, "/acct/a/x": X}),
                "/acct/a/y": support.whole({"/acct/a/y": Y}),
                "/acct/a/z": support.whole({"/acct/a/z": Z}),
                "/acct/b": support.whole({"/acct/b": B}),
            },
            subdirs={"/acct/a": ["/acct/a/x", "/acct/a/y", "/acct/a/z"]},
        )

    def _whole_night(self, generation, b=None):
        X, Y, Z = self.X, self.Y, self.Z
        support.scan_pass(
            self.conn, "acct-1", generation, self.TOPS,
            {
                "/acct/a": support.whole({
                    "/acct/a/x/p": X, "/acct/a/x": X, "/acct/a/y": Y, "/acct/a/z": Z,
                    "/acct/a": X + Y + Z + self.A_OWN,
                }),
                "/acct/b": support.whole({"/acct/b": b if b is not None else self.B}),
            },
        )

    def _rows(self, generation):
        return {
            row["path"]: row
            for row in scan_store.growth_delta(self.conn, "acct-1", generation, generation - 1, 15, max_depth=0)
        }

    def test_a_split_night_still_adds_up_to_the_whole_account(self):
        """먼저 끝난 `x` 도 다시 큐에 넣은 형제들처럼 잰 단위다."""

        self._split_night(1)
        self.assertEqual(
            scan_store.measured_total_kb(self.conn, "acct-1", 1),
            # 쪼개진 `/acct/a` 에 직접 있는 파일은 그 밤에 아무도 안 셌다 - 모른다.
            self.X + self.Y + self.Z + self.B,
        )

    def test_the_units_of_a_split_night_are_all_in_the_list(self):
        self._whole_night(1)
        self._split_night(2)
        self.assertEqual(
            set(self._rows(2)), {"/acct/a/x", "/acct/a/y", "/acct/a/z", "/acct/b"}
        )

    def test_a_folder_measured_in_pieces_last_time_is_not_new(self):
        """지난번에 나눠 쟀을 뿐이다. 통째로 '새 경로'로 보면 알림까지 간다."""

        self._split_night(1)
        self._whole_night(2)
        row = self._rows(2)["/acct/a"]
        self.assertEqual(row["previous_kb"], self.X + self.Y + self.Z)
        self.assertEqual(row["previous_note"], scan_store.PREVIOUS_PIECED)

    def test_a_folder_that_could_not_be_measured_last_time_is_not_new(self):
        """지난번에 못 잰 것이다 - 얼마였는지 모르지, 새로 생긴 것이 아니다."""

        support.scan_pass(
            self.conn, "acct-1", 1, self.TOPS,
            {"/acct/a": support.failed(), "/acct/b": support.whole({"/acct/b": self.B})},
        )
        self._whole_night(2)
        row = self._rows(2)["/acct/a"]
        self.assertIsNone(row["previous_kb"])
        self.assertEqual(row["previous_note"], scan_store.PREVIOUS_UNMEASURED)

    def test_a_really_new_folder_is_still_new(self):
        support.scan_pass(
            self.conn, "acct-1", 1, ["/acct/b"], {"/acct/b": support.whole({"/acct/b": self.B})},
        )
        self._whole_night(2)
        row = self._rows(2)["/acct/a"]
        self.assertIsNone(row["previous_kb"])
        self.assertIsNone(row["previous_note"])

    def test_the_change_order_keeps_a_big_drop(self):
        """증가 순으로 자르면 크게 줄어든 곳(정리한 곳)이 목록에서 빠진다."""

        self._whole_night(1, b=900_000)
        self._whole_night(2, b=100)
        rows = scan_store.growth_delta(self.conn, "acct-1", 2, 1, 1, max_depth=0,
                                       order=scan_store.ORDER_CHANGE)
        self.assertEqual([row["path"] for row in rows], ["/acct/b"])
        rows = scan_store.growth_delta(self.conn, "acct-1", 2, 1, 1, max_depth=0)
        self.assertEqual([row["path"] for row in rows], ["/acct/a"])


class ChangeFocusTests(ScanStoreTestCase):
    """증가 경로 한 줄의 변화가 그 안의 어디에 몰렸나.

    목록은 측정 단위(계정 바로 아래 폴더)까지만 말한다 - "sim 이 3.2TB 늘었다".
    사람이 하려는 일은 그다음, **그 안의 어디인지** 찾아 담당자에게 묻는 것이다.
    그 답이 한 곳으로 모이면 그곳을, 고르게 퍼졌으면 아무 데도 지목하지 않는다."""

    GB = 1024 * 1024

    def _night(self, generation, sizes):
        """`{경로: KB}` 를 측정 단위 `/acct/<이름>` 별로 나눠 한 바퀴를 돈다."""

        units = sorted({"/".join(path.split("/")[:3]) for path in sizes})
        outcomes = {}
        for unit in units:
            rows = {path: kb for path, kb in sizes.items() if path == unit or path.startswith(unit + "/")}
            ordered = dict(sorted(rows.items(), key=lambda item: -item[0].count("/")))
            outcomes[unit] = support.whole(ordered)
        support.scan_pass(self.conn, "acct-1", generation, units, outcomes)

    def _focus(self, path):
        return scan_store.change_focus(self.conn, "acct-1", 2, 1, path)

    def test_the_change_is_followed_down_to_where_it_happened(self):
        G = self.GB
        before = {"/acct/layout": 1000 * G, "/acct/layout/cpu": 600 * G,
                  "/acct/layout/cpu/gds": 500 * G, "/acct/layout/cpu/lvs": 100 * G,
                  "/acct/layout/gpu": 400 * G}
        after = dict(before)
        after.update({"/acct/layout": 1300 * G, "/acct/layout/cpu": 900 * G,
                      "/acct/layout/cpu/gds": 800 * G})
        self._night(1, before)
        self._night(2, after)
        focus = self._focus("/acct/layout")
        self.assertEqual(focus["path"], "/acct/layout/cpu/gds")
        self.assertEqual(focus["delta_kb"], 300 * G)
        self.assertIsNone(focus["note"])

    def test_a_new_folder_is_named_as_new(self):
        G = self.GB
        before = {"/acct/sim": 4700 * G, "/acct/sim/r1031": 2500 * G, "/acct/sim/waves": 2200 * G}
        after = dict(before, **{"/acct/sim": 7900 * G, "/acct/sim/r1107": 3200 * G})
        self._night(1, before)
        self._night(2, after)
        focus = self._focus("/acct/sim")
        self.assertEqual((focus["path"], focus["note"]), ("/acct/sim/r1107", scan_store.FOCUS_NEW))

    def test_a_drop_is_followed_too(self):
        """정리한 곳도 찾는다 - 지운 폴더는 '없어짐'."""

        G = self.GB
        before = {"/acct/scratch": 3000 * G, "/acct/scratch/a": 1600 * G, "/acct/scratch/b": 1400 * G}
        after = {"/acct/scratch": 1500 * G, "/acct/scratch/a": 1500 * G}
        self._night(1, before)
        self._night(2, after)
        focus = self._focus("/acct/scratch")
        self.assertEqual((focus["path"], focus["note"]), ("/acct/scratch/b", scan_store.FOCUS_GONE))
        self.assertEqual(focus["delta_kb"], -1400 * G)

    def test_a_spread_change_is_not_pinned_on_one_folder(self):
        """고르게 퍼진 변화를 한 곳으로 지목하면 틀린 안내가 된다."""

        G = self.GB
        before = {"/acct/users": 400 * G, "/acct/users/kim": 100 * G, "/acct/users/lee": 100 * G,
                  "/acct/users/park": 100 * G, "/acct/users/choi": 100 * G}
        after = {"/acct/users": 800 * G, "/acct/users/kim": 200 * G, "/acct/users/lee": 200 * G,
                 "/acct/users/park": 200 * G, "/acct/users/choi": 200 * G}
        self._night(1, before)
        self._night(2, after)
        self.assertEqual(self._focus("/acct/users")["note"], scan_store.FOCUS_SPREAD)

    def test_a_change_in_the_folders_own_files_is_named_so(self):
        """하위 폴더는 그대로인데 폴더에 직접 있는 파일이 늘었으면 하위 폴더를 지목하면
        안 된다 - 그 폴더 자신의 파일이다."""

        G = self.GB
        self._night(1, {"/acct/proj": 300 * G, "/acct/proj/a": 100 * G, "/acct/proj/b": 100 * G})
        self._night(2, {"/acct/proj": 800 * G, "/acct/proj/a": 110 * G, "/acct/proj/b": 100 * G})
        focus = self._focus("/acct/proj")
        self.assertEqual((focus["path"], focus["note"]), ("/acct/proj", scan_store.FOCUS_FILES))
        self.assertEqual(focus["delta_kb"], 490 * G)

    def test_a_folder_with_no_subfolders_has_nothing_more_to_say(self):
        """하위 폴더 기록이 없으면 '여러 곳에 고르게'도 아니다 - 그 폴더 자체다."""

        G = self.GB
        self._night(1, {"/acct/lib": 2000 * G})
        self._night(2, {"/acct/lib": 1950 * G})
        self.assertIsNone(self._focus("/acct/lib"))

    def test_it_does_not_go_where_last_night_did_not_look(self):
        """지난밤 기록이 그 폴더 안까지 내려가지 않았으면, 안쪽 폴더가 '새로' 생긴 것인지
        알 수 없다 - 그 폴더에서 멈춘다."""

        G = self.GB
        self._night(1, {"/acct/lib": 100 * G, "/acct/lib/std": 100 * G})
        self._night(2, {"/acct/lib": 900 * G, "/acct/lib/std": 900 * G,
                        "/acct/lib/std/v2": 800 * G})
        focus = self._focus("/acct/lib")
        self.assertEqual((focus["path"], focus["note"]), ("/acct/lib/std", None))


class ComparableChangeTests(ScanStoreTestCase):
    """계정 전체 증감은 **두 바퀴 모두 잰 곳끼리** 견준다.

    지난 바퀴에 권한 오류로 못 잰 900GB 폴더가 이번에 재지면, 합계끼리 빼서는 그
    900GB 가 통째로 '늘어난 양' 이 된다 - 실제로는 10GB 늘었는데 홈의 '무엇부터
    할까' 에 "지난 스캔보다 910 GB 늘었습니다" 가 떴다. 반대로 이번에 못 재면 계정이
    통째로 줄어든 것처럼 보인다."""

    G = support.GB
    TOPS = ["/acct/a", "/acct/b"]

    def _pass(self, generation, a, b):
        outcomes = {
            "/acct/a": support.failed() if a is None else support.whole({"/acct/a": a}),
            "/acct/b": support.whole({"/acct/b": b}),
        }
        support.scan_pass(self.conn, "acct-1", generation, self.TOPS, outcomes)

    def test_folder_unmeasured_last_time_is_left_out_of_both_sides(self):
        self._pass(1, None, 400 * self.G)
        self._pass(2, 900 * self.G, 410 * self.G)

        change = scan_store.comparable_change(self.conn, "acct-1", 2, 1)

        self.assertEqual(change["delta_kb"], 10 * self.G)
        self.assertEqual(change["excluded"], ["/acct/a"])

    def test_folder_unmeasured_this_time_is_not_a_drop(self):
        self._pass(1, 900 * self.G, 400 * self.G)
        self._pass(2, None, 410 * self.G)

        change = scan_store.comparable_change(self.conn, "acct-1", 2, 1)

        self.assertEqual(change["delta_kb"], 10 * self.G)
        self.assertEqual(change["excluded"], ["/acct/a"])

    def test_without_failures_it_is_the_plain_difference(self):
        self._pass(1, 900 * self.G, 400 * self.G)
        self._pass(2, 950 * self.G, 410 * self.G)

        change = scan_store.comparable_change(self.conn, "acct-1", 2, 1)

        self.assertEqual(change["delta_kb"], 60 * self.G)
        self.assertEqual(change["excluded"], [])

    def test_failed_piece_of_a_split_folder_leaves_out_the_whole_folder(self):
        """지난밤 `/acct/a` 를 나눠 재다 `y` 를 못 쟀고, 오늘은 `/acct/a` 를 한 번에
        쟀다. 오늘 값에서 `y` 몫만 덜어 낼 수 없으므로 `/acct/a` 를 양쪽에서 뺀다."""

        G = self.G
        support.scan_pass(
            self.conn, "acct-1", 1, self.TOPS,
            {
                "/acct/a": support.cut({"/acct/a/x": 300 * G}),
                "/acct/a/y": support.failed(),
                "/acct/b": support.whole({"/acct/b": 400 * G}),
            },
            subdirs={"/acct/a": ["/acct/a/x", "/acct/a/y"]},
        )
        support.scan_pass(
            self.conn, "acct-1", 2, self.TOPS,
            {
                "/acct/a": support.whole({"/acct/a/x": 300 * G, "/acct/a/y": 600 * G,
                                          "/acct/a": 900 * G}),
                "/acct/b": support.whole({"/acct/b": 410 * G}),
            },
        )

        change = scan_store.comparable_change(self.conn, "acct-1", 2, 1)

        self.assertEqual(change["delta_kb"], 10 * G)
        self.assertEqual(change["excluded"], ["/acct/a"])

    def test_nothing_measured_on_one_side_means_no_comparison(self):
        self._pass(1, 900 * self.G, 400 * self.G)

        self.assertIsNone(scan_store.comparable_change(self.conn, "acct-1", 2, 1))


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


class GenerationDateTests(ScanStoreTestCase):
    """스캔을 날짜로 가리키려면 그 스캔이 언제 끝났는지가 필요하다.

    `baseline_results.completed_at`에 이미 세대별로 들어 있어서 새 열 없이
    꺼낼 수 있다.
    """

    def test_returns_completion_time_of_that_scan(self):
        conn = self.conn
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a"])
        checkpoint = scan_store.next_pending(conn, "acct-1", scan_store.BASELINE, 1)
        scan_store.mark_done(conn, checkpoint["id"], size_kb=100)
        support.save_checkpoint_results(conn, "acct-1", 1)

        completed = scan_store.generation_completed_at(conn, "acct-1", 1)
        self.assertIsNotNone(completed)
        self.assertIn("T", completed)

    def test_none_for_a_scan_still_in_progress(self):
        """진행 중인 스캔은 날짜가 없다 - 화면은 회차 번호로 물러나야 한다."""

        conn = self.conn
        scan_store.seed_checkpoints(conn, "acct-1", scan_store.BASELINE, 1, ["/a"])
        self.assertIsNone(scan_store.generation_completed_at(conn, "acct-1", 1))



class ShallowRowsTests(ScanStoreTestCase):
    """상위 경로·증가 경로·잰 용량 합계는 얕은 행 몇 개만 본다 - 세대 전체를 읽지 않는다.

    셋은 `COALESCE(depth, n) <= ?` 로 걸렀는데, 그 모양은 인덱스를 못 타서 세대의
    행을 **전부** 읽었다. 경로가 수만 개인 계정이면 계정마다 수 MB 이고, 창은
    스캔 탭을 열 때와 스캔 중 5초마다 그것을 계정 수만큼 읽었다 (NFS 에서는
    그대로 네트워크 읽기). 실측: 계정 20개 기준 스캔 탭 첫 읽기 430MB, 스캔 중
    5초마다 36MB."""

    DEEP_ROWS = 3000

    def _generation(self, generation, root_kb, deep_kb=1):
        entries = [("/acct/a", root_kb), ("/acct/a/kid", root_kb // 2)]
        entries += [(f"/acct/a/kid/d{n}", deep_kb) for n in range(self.DEEP_ROWS)]
        scan_store.save_tree_entries(self.conn, "acct-1", generation, entries, "/acct/a")

    def test_reading_does_not_grow_with_the_deep_rows(self):
        self._generation(1, 1000)
        self._generation(2, 1500)
        calls = {
            "top_paths": lambda: scan_store.top_paths(self.conn, "acct-1", 2, 15, max_depth=0),
            "growth_delta": lambda: scan_store.growth_delta(self.conn, "acct-1", 2, 1, 15, max_depth=0),
            "measured_total_kb": lambda: scan_store.measured_total_kb(self.conn, "acct-1", 2),
        }
        for name, call in calls.items():
            with self.subTest(name):
                with support.VmSteps(self.conn) as steps:
                    call()
                self.assertLess(steps.count, self.DEEP_ROWS)

    def test_the_answers_are_the_same_as_before(self):
        self._generation(1, 1000)
        self._generation(2, 1500)
        self.assertEqual(
            [row["path"] for row in scan_store.top_paths(self.conn, "acct-1", 2, 15, max_depth=1)],
            ["/acct/a", "/acct/a/kid"],
        )
        self.assertEqual(
            [(row["path"], row["current_kb"], row["previous_kb"])
             for row in scan_store.growth_delta(self.conn, "acct-1", 2, 1, 15, max_depth=0)],
            [("/acct/a", 1500, 1000)],
        )
        self.assertEqual(scan_store.measured_total_kb(self.conn, "acct-1", 2), 1500)

    def test_rows_from_before_the_depth_column_keep_their_old_meaning(self):
        """깊이 열이 생기기 전의 행(깊이 없음)은 상위·증가 경로에서는 깊이 1, 잰
        용량 합계에서는 깊이 0 으로 쳤다. 인덱스를 타게 바꾸면서 그 뜻을 잃으면
        안 된다."""

        for generation, size in ((1, 100), (2, 300)):
            self.conn.execute(
                "INSERT INTO baseline_results (account_id, generation, path, size_kb, completed_at) "
                "VALUES ('acct-1', ?, '/old', ?, '2026-01-01T00:00:00+00:00')",
                (generation, size),
            )
        self.conn.commit()

        self.assertEqual(scan_store.top_paths(self.conn, "acct-1", 2, 15, max_depth=0), [])
        self.assertEqual(
            [row["path"] for row in scan_store.top_paths(self.conn, "acct-1", 2, 15, max_depth=1)],
            ["/old"],
        )
        self.assertEqual(scan_store.growth_delta(self.conn, "acct-1", 2, 1, 15, max_depth=0), [])
        self.assertEqual(
            [(row["path"], row["current_kb"], row["previous_kb"])
             for row in scan_store.growth_delta(self.conn, "acct-1", 2, 1, 15, max_depth=1)],
            [("/old", 300, 100)],
        )
        self.assertEqual(scan_store.measured_total_kb(self.conn, "acct-1", 2), 300)


class CompletionTimeTests(ScanStoreTestCase):
    """세대를 끝낼 때 앞 세대의 완료 시각을 옮겨 둔다.

    화면은 증가 경로를 두 스캔의 날짜로 말한다. 그 날짜를 결과 표에서 찾으면
    (`generation_completed_at`) 세대의 행을 전부 읽어야 했다 - 창을 열 때마다
    계정 수 x 2 번."""

    def test_the_previous_time_moves_along(self):
        scan_store.mark_generation_completed(self.conn, "acct-1", 1, completed_at="2026-10-01T21:00:00+00:00")
        scan_store.mark_generation_completed(self.conn, "acct-1", 2, completed_at="2026-10-02T21:00:00+00:00")
        state = scan_store.get_account_state(self.conn, "acct-1")
        self.assertEqual(state.last_completed_at, "2026-10-02T21:00:00+00:00")
        self.assertEqual(state.previous_completed_at, "2026-10-01T21:00:00+00:00")

    def test_a_skipped_generation_leaves_the_previous_time_unknown(self):
        """앞 값이 바로 앞 세대 것이 아니면 옮기지 않는다 - 엉뚱한 날짜보다 '모름' 이
        낫다 (읽는 쪽이 결과 표에서 찾는다)."""

        scan_store.mark_generation_completed(self.conn, "acct-1", 1, completed_at="2026-10-01T21:00:00+00:00")
        scan_store.mark_generation_completed(self.conn, "acct-1", 3, completed_at="2026-10-03T21:00:00+00:00")
        self.assertIsNone(scan_store.get_account_state(self.conn, "acct-1").previous_completed_at)

    def test_the_first_generation_has_no_previous(self):
        scan_store.mark_generation_completed(self.conn, "acct-1", 1)
        self.assertIsNone(scan_store.get_account_state(self.conn, "acct-1").previous_completed_at)
