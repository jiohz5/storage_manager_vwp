"""쌓인 부하 표본을 판단에 쓸 모양으로 묶는 부분.

## 여기서 지키는 것

- **우리를 뺀 나머지**로 본다. 전체만 보면 우리 스캔이 만든 부하까지 "서버가
  원래 바쁘다"로 읽혀서, 돌릴수록 못 돌릴 것 같아지는 결론이 나온다.
- 하루 평균으로 뭉개지 않는다. 낮 두 시와 새벽 세 시는 같은 서버가 아니다.
- 표본이 모자라면 그 사실부터 말한다. 두세 벌로 내린 판정은 측정이 아니라 추측이다.
"""

import unittest
from datetime import timedelta, timezone

from smvwp import loadreport

KST = timezone(timedelta(hours=9))


def row(**kwargs):
    base = {
        "sampled_at": "2026-09-10T00:00:00+00:00",
        "cpu_count": 32,
        "cpu_busy_percent": 20.0,
        "cpu_iowait_percent": 2.0,
        "load_avg_1m": 2.0,
        "procs_blocked": 0,
        "blocked_others": 0,
        "memory_used_percent": 50.0,
        "nfs_ops_per_second": 100.0,
        "scan_cpu_percent": None,
        "scan_process_count": 0,
        "run_id": None,
    }
    base.update(kwargs)
    return base


class OthersTests(unittest.TestCase):
    def test_our_share_is_taken_out_on_the_right_scale(self):
        """우리 몫은 top 기준(코어 하나=100%)이라 코어 수로 나눠야 맞춰진다."""

        # 32코어에서 코어 2개를 다 쓰면 전체의 6.25%.
        value = loadreport.others_cpu_percent(
            row(cpu_busy_percent=20.0, scan_cpu_percent=200.0, cpu_count=32)
        )
        self.assertAlmostEqual(value, 20.0 - 6.25)

    def test_without_our_share_the_whole_is_used(self):
        self.assertAlmostEqual(loadreport.others_cpu_percent(row()), 20.0)

    def test_it_never_goes_negative(self):
        value = loadreport.others_cpu_percent(
            row(cpu_busy_percent=1.0, scan_cpu_percent=3200.0, cpu_count=32)
        )
        self.assertEqual(value, 0.0)

    def test_unmeasured_stays_unmeasured(self):
        self.assertIsNone(loadreport.others_cpu_percent(row(cpu_busy_percent=None)))


class ScanDetectionTests(unittest.TestCase):
    def test_a_running_scan_is_seen_from_the_process_count(self):
        self.assertTrue(loadreport.scan_was_running(row(scan_process_count=3)))

    def test_or_from_the_run_it_belongs_to(self):
        self.assertTrue(loadreport.scan_was_running(row(run_id="night-1")))

    def test_an_idle_sample_is_not_a_scan(self):
        self.assertFalse(loadreport.scan_was_running(row()))


class SummaryTests(unittest.TestCase):
    def test_average_and_peak(self):
        bucket = loadreport.summarize(
            [row(cpu_busy_percent=10.0), row(cpu_busy_percent=50.0)]
        )
        self.assertAlmostEqual(bucket.cpu_busy.average, 30.0)
        self.assertAlmostEqual(bucket.cpu_busy.peak, 50.0)
        self.assertEqual(bucket.samples, 2)

    def test_missing_values_do_not_drag_the_average_to_zero(self):
        """None 을 0 으로 세면 '한가했다'가 되어 버린다."""

        bucket = loadreport.summarize(
            [row(load_avg_1m=None), row(load_avg_1m=4.0)]
        )
        self.assertAlmostEqual(bucket.load_avg.average, 4.0)
        self.assertEqual(bucket.load_avg.samples, 1)

    def test_a_bucket_knows_whether_we_were_in_it(self):
        """'평소 모습'인 줄 알고 읽었는데 우리 스캔이 섞여 있으면 안 된다."""

        clean = loadreport.summarize([row(), row()])
        mixed = loadreport.summarize([row(), row(run_id="night")])
        self.assertTrue(clean.scan_free)
        self.assertFalse(mixed.scan_free)
        self.assertEqual(mixed.scan_samples, 1)

    def test_nothing_in_nothing_out(self):
        bucket = loadreport.summarize([])
        self.assertEqual(bucket.samples, 0)
        self.assertIsNone(bucket.cpu_busy.average)


class CrowdedTests(unittest.TestCase):
    def test_high_cpu_from_others_is_crowded(self):
        bucket = loadreport.summarize([row(cpu_busy_percent=70.0)])
        self.assertTrue(bucket.crowded)

    def test_waiting_processes_count_even_when_cpu_is_low(self):
        """상세 스캔의 영향은 CPU 가 아니라 여기에 먼저 나타난다."""

        bucket = loadreport.summarize(
            [row(cpu_busy_percent=3.0, blocked_others=4)]
        )
        self.assertTrue(bucket.crowded)

    def test_a_quiet_hour_is_not_crowded(self):
        self.assertFalse(loadreport.summarize([row()]).crowded)


class HourlyTests(unittest.TestCase):
    def test_hours_are_local_not_utc(self):
        """UTC 로 자르면 사람이 아는 '오후'와 아홉 칸 어긋난다."""

        buckets = loadreport.hourly_profile(
            [row(sampled_at="2026-09-10T00:00:00+00:00")], tz=KST
        )
        self.assertEqual([item.label for item in buckets], ["09시"])

    def test_samples_land_in_their_own_hour(self):
        rows = [
            row(sampled_at="2026-09-10T00:00:00+00:00", cpu_busy_percent=10.0),
            row(sampled_at="2026-09-10T00:30:00+00:00", cpu_busy_percent=30.0),
            row(sampled_at="2026-09-10T05:00:00+00:00", cpu_busy_percent=80.0),
        ]
        buckets = {b.label: b for b in loadreport.hourly_profile(rows, tz=KST)}
        self.assertEqual(sorted(buckets), ["09시", "14시"])
        self.assertAlmostEqual(buckets["09시"].cpu_busy.average, 20.0)
        self.assertEqual(buckets["09시"].samples, 2)

    def test_hours_without_samples_are_not_invented(self):
        """빈 칸을 0 으로 채우면 '그 시간엔 한가하다'는 거짓말이 된다."""

        buckets = loadreport.hourly_profile([row()], tz=KST)
        self.assertEqual(len(buckets), 1)

    def test_an_unreadable_stamp_is_skipped(self):
        buckets = loadreport.hourly_profile([row(sampled_at="쓰레기")], tz=KST)
        self.assertEqual(buckets, [])


class SplitTests(unittest.TestCase):
    def test_the_two_states_are_summarized_side_by_side(self):
        rows = [
            row(cpu_busy_percent=10.0),
            row(cpu_busy_percent=60.0, run_id="night", scan_process_count=4),
        ]
        split = loadreport.split_by_scan(rows)
        self.assertAlmostEqual(split["with_scan"].cpu_busy.average, 60.0)
        self.assertAlmostEqual(split["without_scan"].cpu_busy.average, 10.0)

    def test_an_empty_side_is_still_reported(self):
        """'스캔 중 표본이 없다'는 것도 알아야 할 사실이다."""

        split = loadreport.split_by_scan([row()])
        self.assertEqual(split["with_scan"].samples, 0)


class VerdictTests(unittest.TestCase):
    def test_a_quiet_hour_has_room(self):
        bucket = loadreport.summarize([row()] * 8, label="14시")
        verdict = loadreport.daytime_verdict(bucket)
        self.assertTrue(verdict.room)

    def test_too_few_samples_is_said_out_loud(self):
        """두세 벌로 '여유 있음'이라고 하면 측정이 아니라 추측이다."""

        bucket = loadreport.summarize([row()] * 2, label="14시")
        verdict = loadreport.daytime_verdict(bucket)
        self.assertFalse(verdict.room)
        self.assertIn("표본", verdict.reason)

    def test_a_busy_hour_says_why(self):
        bucket = loadreport.summarize([row(cpu_busy_percent=80.0)] * 8, label="10시")
        verdict = loadreport.daytime_verdict(bucket)
        self.assertFalse(verdict.room)
        self.assertIn("CPU", verdict.reason)

    def test_waiting_processes_give_their_own_reason(self):
        """'CPU 가 바쁘다'와 'I/O 를 기다린다'는 다른 이야기다."""

        bucket = loadreport.summarize(
            [row(cpu_busy_percent=3.0, blocked_others=5)] * 8, label="10시"
        )
        verdict = loadreport.daytime_verdict(bucket)
        self.assertIn("I/O", verdict.reason)

    def test_the_numbers_come_with_the_verdict(self):
        """판단을 대신하지 않고 근거를 같이 준다."""

        bucket = loadreport.summarize([row()] * 8, label="14시")
        verdict = loadreport.daytime_verdict(bucket)
        self.assertIsNotNone(verdict.others_cpu)
        self.assertEqual(verdict.samples, 8)


class RankingTests(unittest.TestCase):
    def buckets(self):
        return [
            loadreport.summarize([row(cpu_busy_percent=80.0)] * 8, label="10시"),
            loadreport.summarize([row(cpu_busy_percent=5.0)] * 8, label="03시"),
            loadreport.summarize([row(cpu_busy_percent=40.0)] * 8, label="15시"),
        ]

    def test_busiest_first(self):
        picked = loadreport.busiest_hours(self.buckets(), limit=2)
        self.assertEqual([item.label for item in picked], ["10시", "15시"])

    def test_quietest_first(self):
        picked = loadreport.quietest_hours(self.buckets(), limit=2)
        self.assertEqual([item.label for item in picked], ["03시", "15시"])

    def test_hours_we_were_already_scanning_are_not_recommended(self):
        """새벽이 한가한 것은 이미 우리가 그 자리를 쓰고 있어서다.

        그 칸을 "여기에 더 얹으세요"로 내밀면 근거가 못 된다."""

        # 이름이 겹치지 않게 둔다 - `buckets()` 에 이미 03시가 있어서, 같은
        # 이름을 쓰면 무엇이 걸러졌는지 구분이 안 된다.
        night = loadreport.summarize(
            [row(cpu_busy_percent=1.0, run_id="night")] * 8, label="02시"
        )
        picked = loadreport.quietest_hours(self.buckets() + [night], limit=1)
        # 02시가 가장 한가하지만 그 한가함 안에 우리가 들어 있다.
        self.assertEqual(picked[0].label, "03시")

    def test_they_can_still_be_asked_for(self):
        night = loadreport.summarize(
            [row(cpu_busy_percent=1.0, run_id="night")] * 8, label="02시"
        )
        picked = loadreport.quietest_hours(
            self.buckets() + [night], limit=1, scan_free_only=False
        )
        self.assertEqual(picked[0].label, "02시")

    def test_thin_hours_are_not_recommended(self):
        """표본 둘짜리 시간대가 '가장 한가한 때'로 추천되면 안 된다."""

        thin = loadreport.summarize([row(cpu_busy_percent=0.0)] * 2, label="04시")
        picked = loadreport.quietest_hours(self.buckets() + [thin], limit=1)
        self.assertEqual(picked[0].label, "03시")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
