"""상세 스캔 현황 표가 쓰는 집계 - 측정량과 남은 시간 추정."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smvwp import scan_store


class MeasuredTotalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = scan_store.connect(Path(self.tmp.name) / "data")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _save(self, root, entries):
        scan_store.save_tree_entries(self.conn, "acct", 1, entries, root)

    def test_none_before_anything_is_measured(self):
        """0으로 주면 '0바이트를 찾았다'로 읽힌다 - '모른다'와는 다르다."""

        self.assertIsNone(scan_store.measured_total_kb(self.conn, "acct", 1))

    def test_sums_checkpoint_roots_only(self):
        """부모와 자식을 다 더하면 같은 바이트를 두 번 센다.

        `du -k`는 서브트리 전체를 주므로 `/a`(100)와 그 안의 `/a/b`(80)가 함께
        들어온다. 전부 더하면 180이 되는데 실제로 쓰는 용량은 100이다."""

        self._save("/acct/a", [("/acct/a", 100), ("/acct/a/b", 80)])
        self._save("/acct/c", [("/acct/c", 50)])
        self.assertEqual(scan_store.measured_total_kb(self.conn, "acct", 1), 150)

    def test_generation_scoped(self):
        self._save("/acct/a", [("/acct/a", 100)])
        scan_store.save_tree_entries(self.conn, "acct", 2, [("/acct/a", 999)], "/acct/a")
        self.assertEqual(scan_store.measured_total_kb(self.conn, "acct", 1), 100)
        self.assertEqual(scan_store.measured_total_kb(self.conn, "acct", 2), 999)


class RemainingEstimateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = scan_store.connect(Path(self.tmp.name) / "data")
        self.base = datetime(2026, 8, 22, 23, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _complete(self, offsets_minutes, status=scan_store.STATUS_DONE):
        """지정한 분 오프셋마다 체크포인트를 하나씩 완료시킨다."""

        paths = [f"/acct/dir{index}" for index in range(len(offsets_minutes))]
        scan_store.seed_checkpoints(self.conn, "acct", scan_store.BASELINE, 1, paths)
        rows = self.conn.execute(
            "SELECT id FROM scan_checkpoints WHERE account_id = 'acct' ORDER BY id"
        ).fetchall()
        for row, minutes in zip(rows, offsets_minutes):
            self.conn.execute(
                "UPDATE scan_checkpoints SET status = ?, scanned_at = ? WHERE id = ?",
                (status, (self.base + timedelta(minutes=minutes)).isoformat(), row["id"]),
            )
        self.conn.commit()

    def _estimate(self, pending):
        return scan_store.estimate_remaining_seconds(
            self.conn, "acct", scan_store.BASELINE, 1, pending
        )

    def test_no_estimate_without_enough_samples(self):
        """근거 없는 숫자를 내놓느니 '-'가 낫다."""

        self._complete([0, 5, 10])  # 간격 2개뿐
        self.assertIsNone(self._estimate(pending=5))

    def test_no_estimate_when_nothing_is_pending(self):
        self._complete([0, 5, 10, 15, 20, 25])
        self.assertIsNone(self._estimate(pending=0))

    def test_uses_observed_rate(self):
        # 5분 간격으로 6개 완료 -> 중앙값 5분. 남은 4개 = 20분.
        self._complete([0, 5, 10, 15, 20, 25])
        self.assertAlmostEqual(self._estimate(pending=4), 4 * 5 * 60)

    def test_overnight_gap_does_not_blow_up_the_estimate(self):
        """스캔은 밤을 걸쳐 이어진다 - 06:00에 멈췄다 다음 밤 22:00에 재개하면
        16시간짜리 간격이 표본에 섞인다.

        평균을 쓰면 그 하나가 전체를 지배해 '남은 시간 며칠'이 나온다. 중앙값은
        흔들리지 않는다. 이 테스트가 그 선택을 지킨다."""

        # 5분 간격 다섯 번 -> 16시간 공백 -> 다시 5분 간격 다섯 번
        offsets = [0, 5, 10, 15, 20, 25]
        offsets += [25 + 16 * 60 + 5 * n for n in range(1, 6)]
        self._complete(offsets)

        estimate = self._estimate(pending=4)
        self.assertAlmostEqual(estimate, 4 * 5 * 60)

        # 같은 표본을 평균으로 냈다면 훨씬 큰 값이 나왔을 것이다.
        intervals = scan_store.completion_intervals(
            self.conn, "acct", scan_store.BASELINE, 1
        )
        mean_based = sum(intervals) / len(intervals) * 4
        self.assertGreater(mean_based, estimate * 5)

    def test_errored_checkpoints_count_as_progress(self):
        """실패도 '지나간 작업'이다. 빼면 속도를 실제보다 느리게 본다."""

        self._complete([0, 5, 10, 15, 20, 25], status=scan_store.STATUS_ERROR)
        self.assertAlmostEqual(self._estimate(pending=2), 2 * 5 * 60)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
