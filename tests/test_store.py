import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smvwp import store


def _sample(account_id="acct-1", collected_at=None, overall_tier="normal", byte_pct=50.0):
    return store.SampleRecord(
        account_id=account_id,
        collected_at=collected_at or datetime.now(timezone.utc).isoformat(),
        ok=True,
        filesystem="/dev/sda1",
        mount_point="/user/project_a",
        total_kb=1000,
        used_kb=500,
        avail_kb=500,
        byte_pct=byte_pct,
        byte_tier=overall_tier,
        inode_total=1000,
        inode_used=10,
        inode_avail=990,
        inode_pct=1.0,
        inode_tier="normal",
        overall_tier=overall_tier,
    )


class StoreTests(unittest.TestCase):
    def test_insert_and_latest_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            conn = store.connect(data_dir)
            try:
                store.insert_sample(conn, _sample(collected_at="2026-01-01T00:00:00+00:00"))
                store.insert_sample(conn, _sample(collected_at="2026-01-01T00:15:00+00:00", byte_pct=60.0))
                latest = store.latest_samples(conn)
                self.assertEqual(len(latest), 1)
                self.assertEqual(latest["acct-1"].byte_pct, 60.0)
            finally:
                conn.close()

    def test_latest_samples_one_row_per_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            conn = store.connect(data_dir)
            try:
                store.insert_sample(conn, _sample(account_id="a", collected_at="2026-01-01T00:00:00+00:00"))
                store.insert_sample(conn, _sample(account_id="b", collected_at="2026-01-01T00:00:00+00:00"))
                latest = store.latest_samples(conn)
                self.assertEqual(set(latest.keys()), {"a", "b"})
            finally:
                conn.close()

    def test_history_returns_descending_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            conn = store.connect(data_dir)
            try:
                store.insert_sample(conn, _sample(collected_at="2026-01-01T00:00:00+00:00", byte_pct=10.0))
                store.insert_sample(conn, _sample(collected_at="2026-01-01T00:15:00+00:00", byte_pct=20.0))
                rows = store.history(conn, "acct-1")
                self.assertEqual([r.byte_pct for r in rows], [20.0, 10.0])
            finally:
                conn.close()

    def test_prune_old_samples_removes_only_stale_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            conn = store.connect(data_dir)
            try:
                now = datetime(2026, 6, 1, tzinfo=timezone.utc)
                old_ts = (now - timedelta(days=200)).isoformat()
                recent_ts = (now - timedelta(days=1)).isoformat()
                store.insert_sample(conn, _sample(collected_at=old_ts))
                store.insert_sample(conn, _sample(collected_at=recent_ts))

                deleted = store.prune_old_samples(conn, retention_days=90, now=now)
                self.assertEqual(deleted, 1)
                remaining = store.history(conn, "acct-1")
                self.assertEqual(len(remaining), 1)
                self.assertEqual(remaining[0].collected_at, recent_ts)
            finally:
                conn.close()


class ReadsWhatItNeedsTests(unittest.TestCase):
    """표본은 15분마다 모든 계정 것이 한꺼번에 들어가 90일이면 십수만 줄이다.

    가장 최근 표본 찾기, "가장 최근 수집은 언제", 오래된 표본 정리가 모두 표나
    인덱스를 처음부터 끝까지 훑었다 (실측 계정 20개: 각각 24MB, 24MB, 30MB). 홈을
    읽을 때마다, 수집할 때마다(15분) 그만큼이었고, 데이터 디렉터리가 NFS 면 그대로
    네트워크 읽기다."""

    ACCOUNTS = 5
    STEPS = 300

    def setUp(self):
        from tests import support

        self.support = support
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conn = store.connect(Path(tmp.name))
        self.addCleanup(self.conn.close)
        self.now = datetime(2026, 10, 15, tzinfo=timezone.utc)
        # 넣는 쪽은 여기서 보는 것이 아니다 - 줄을 직접 넣는다.
        self.conn.executemany(
            "INSERT INTO samples (account_id, collected_at, ok, byte_pct) VALUES (?, ?, 1, ?)",
            [(f"acct-{a}", (self.now - timedelta(minutes=15 * (self.STEPS - step))).isoformat(),
              float(step))
             for step in range(self.STEPS) for a in range(self.ACCOUNTS)],
        )
        self.conn.commit()
        self.rows = self.ACCOUNTS * self.STEPS

    def steps(self, call):
        with self.support.VmSteps(self.conn) as steps:
            result = call()
        return steps.count, result

    def test_latest_samples_for_given_accounts_reads_one_row_each(self):
        ids = [f"acct-{a}" for a in range(self.ACCOUNTS)]
        count, latest = self.steps(lambda: store.latest_samples(self.conn, ids))
        self.assertLess(count, self.rows)
        everything = store.latest_samples(self.conn)
        self.assertEqual(set(latest), set(ids))
        for account_id in ids:
            self.assertEqual(latest[account_id], everything[account_id])
            self.assertEqual(latest[account_id].byte_pct, float(self.STEPS - 1))

    def test_an_account_without_samples_is_simply_absent(self):
        self.assertEqual(store.latest_samples(self.conn, ["nobody"]), {})

    def test_the_newest_collection_time_is_found_without_a_scan(self):
        count, newest = self.steps(lambda: store.newest_collected_at(self.conn))
        self.assertLess(count, self.rows)
        self.assertEqual(newest, (self.now - timedelta(minutes=15)).isoformat())

    def test_pruning_with_nothing_old_reads_almost_nothing(self):
        count, deleted = self.steps(
            lambda: store.prune_old_samples(self.conn, retention_days=90, now=self.now)
        )
        self.assertEqual(deleted, 0)
        self.assertLess(count, self.rows)

    def test_one_collection_is_one_commit(self):
        """줄마다 커밋하면 계정 수만큼 커밋한다. NFS 에서 DELETE 저널 커밋 하나는
        저널 파일 생성·fsync·삭제 왕복이다."""

        commits = []
        self.conn.set_trace_callback(
            lambda sql: commits.append(sql) if sql.strip().upper().startswith("COMMIT") else None
        )
        try:
            store.insert_samples(self.conn, [_sample(account_id=f"new-{n}") for n in range(4)])
        finally:
            self.conn.set_trace_callback(None)
        self.assertEqual(len(commits), 1)
        self.assertEqual(len(store.latest_samples(self.conn, [f"new-{n}" for n in range(4)])), 4)


class TrendColumnsTests(unittest.TestCase):
    """추세·FULL 예측·수집 신선도는 표본의 몇 열만 본다 (`store.TREND_COLUMNS`).

    표본은 15분마다 모든 계정 것이 한꺼번에 들어가 한 계정의 줄이 표 전체에 흩어져
    있다. 한 계정의 90일(계정 상세)을 읽어도 표 대부분의 페이지를 읽었다 (계정
    20개면 31MB). 그 열만 담은 인덱스로 읽으면 표를 건드리지 않는다 (0.9MB)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        self.conn = store.connect(self.data_dir)
        self.addCleanup(self.conn.close)
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        records = []
        for step in range(40):
            for account_id in ("a", "b"):
                record = _sample(account_id=account_id, byte_pct=float(step),
                                 collected_at=(start + timedelta(hours=step)).isoformat())
                record.ok = step % 7 != 3
                record.used_kb = 1000 + step
                records.append(record)
        store.insert_samples(self.conn, records)
        self.since = start + timedelta(hours=10)

    def test_the_same_rows_and_values_as_a_full_read(self):
        full = store.samples_since(self.conn, "a", self.since)
        light = store.trend_samples_since(self.conn, "a", self.since)
        self.assertEqual(len(light), len(full))
        for whole, part in zip(full, light):
            self.assertEqual(part.account_id, whole.account_id)
            for column in store.TREND_COLUMNS:
                self.assertEqual(getattr(part, column), getattr(whole, column), column)

    def test_it_reads_only_the_index(self):
        plan = " ".join(
            row[3] for row in self.conn.execute(
                "EXPLAIN QUERY PLAN " + store.TREND_SINCE_SQL, ("a", self.since.isoformat())
            )
        )
        self.assertIn("COVERING INDEX", plan)

    def test_forecast_trend_and_freshness_need_nothing_else(self):
        """빠진 열이 None 으로 오므로, 셋 중 하나라도 다른 열을 보기 시작하면 답이
        달라진다 - 그러면 `TREND_COLUMNS` 와 인덱스를 함께 늘려야 한다."""

        from smvwp import analytics, config as config_module, freshness, trend

        settings = config_module.Settings()
        now = self.since + timedelta(hours=30)
        full = store.samples_since(self.conn, "a", self.since)
        light = store.trend_samples_since(self.conn, "a", self.since)
        self.assertEqual(analytics.build_forecast("a", light, settings, now),
                         analytics.build_forecast("a", full, settings, now))
        self.assertEqual(trend.build(light, since=self.since, until=now),
                         trend.build(full, since=self.since, until=now))
        self.assertEqual(freshness.evaluate_account("a", light, settings, now),
                         freshness.evaluate_account("a", full, settings, now))


class ConcurrentConnectTests(unittest.TestCase):
    """새 데이터 디렉터리에 여러 스레드가 동시에 붙는 경우.

    창을 처음 열면 작업 스레드 몇(대시보드·스캔 상태·계정 상세)이 거의 같은
    순간에 연결한다. 예전에는 "내가 만들겠다" 표시만 먼저 해 두고 잠금을
    놓았기 때문에, 그 사이에 들어온 스레드가 **표가 하나도 없는 DB** 를 그대로
    받아 `no such table` 로 죽었다."""

    def test_every_thread_gets_a_usable_schema(self):
        import tempfile
        import threading

        from smvwp import store

        with tempfile.TemporaryDirectory() as name:
            data_dir = Path(name) / "data"
            # 이 경로는 이 프로세스에서 처음이다 - 그래야 만드는 경로를 탄다.
            from smvwp import sqlite_db

            sqlite_db._INITIALIZED.discard(str(store.db_path(data_dir)))

            start = threading.Barrier(6)
            errors = []
            lock = threading.Lock()

            def worker():
                # 연결은 만든 스레드에서만 쓸 수 있으므로 여기서 닫는다.
                start.wait()
                conn = None
                try:
                    conn = store.connect(data_dir)
                    conn.execute("SELECT COUNT(*) FROM samples").fetchone()
                except Exception as exc:  # pragma: no cover - 실패를 모아 본다
                    with lock:
                        errors.append(repr(exc))
                finally:
                    if conn is not None:
                        conn.close()

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
