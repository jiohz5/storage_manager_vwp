"""이력을 그릴 모양으로 줄이는 부분.

## 여기서 가장 조심하는 것

**빈 칸을 이어 붙이지 않는다.** 수집기가 하루 멈췄으면 그 자리에 값이 없는데,
양옆을 직선으로 이으면 없는 데이터를 있는 것처럼 그리게 된다 - 그 직선 위의
어떤 지점을 짚어도 우리는 그 값을 모른다.

## 그리고 칸의 대표값

**마지막 값**을 쓴다. 최고값을 쓰면 잠깐 찼다가 정리된 순간이 계속 남아
"줄지 않는 것처럼" 보인다.
"""

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from smvwp import trend

START = datetime(2026, 6, 1, tzinfo=timezone.utc)


@dataclass
class FakeSample:
    collected_at: str
    byte_pct: Optional[float] = None
    used_kb: Optional[int] = None


def at(hours: float, pct=None, used=None):
    return FakeSample(
        collected_at=(START + timedelta(hours=hours)).isoformat(),
        byte_pct=pct, used_kb=used,
    )


class ShapeTests(unittest.TestCase):
    def test_it_reduces_to_the_bucket_count(self):
        """8,640점을 200픽셀에 찍어 봐야 한 픽셀에 40점이 겹칠 뿐이다."""

        samples = [at(index * 0.25, pct=50.0) for index in range(2000)]
        series = trend.build(samples, buckets=100)
        self.assertEqual(len(series.points), 100)

    def test_the_bucket_value_is_the_last_not_the_peak(self):
        """최고값을 쓰면 잠깐 찼다가 정리된 순간이 계속 남는다."""

        samples = [at(0, pct=10.0), at(1, pct=90.0), at(2, pct=20.0)]
        series = trend.build(samples, buckets=1)
        self.assertEqual(series.points[0].value, 20.0)
        self.assertEqual(series.points[0].peak, 90.0)

    def test_order_does_not_matter(self):
        forward = trend.build([at(0, pct=10.0), at(10, pct=90.0)], buckets=2)
        backward = trend.build([at(10, pct=90.0), at(0, pct=10.0)], buckets=2)
        self.assertEqual(
            [p.value for p in forward.points], [p.value for p in backward.points]
        )

    def test_another_field_can_be_charted(self):
        series = trend.build([at(0, used=100), at(5, used=300)],
                             field_name="used_kb", buckets=2)
        self.assertEqual(series.last, 300)

    def test_a_single_sample_gives_one_point(self):
        series = trend.build([at(0, pct=42.0)], buckets=50)
        self.assertEqual(len(series.points), 1)
        self.assertEqual(series.last, 42.0)


class GapTests(unittest.TestCase):
    """수집기가 멈췄던 자리."""

    def test_a_gap_stays_empty(self):
        """양옆을 이으면 없는 데이터를 그리게 된다."""

        samples = [at(0, pct=10.0), at(100, pct=90.0)]
        series = trend.build(samples, buckets=10)
        self.assertGreater(series.gaps, 0)
        self.assertFalse(series.points[5].measured)

    def test_the_line_is_broken_into_segments(self):
        samples = [at(0, pct=10.0), at(100, pct=90.0)]
        series = trend.build(samples, buckets=10)
        runs = trend.segments(series)
        self.assertEqual(len(runs), 2)

    def test_a_continuous_series_is_one_segment(self):
        samples = [at(index, pct=50.0) for index in range(20)]
        series = trend.build(samples, buckets=10)
        self.assertEqual(len(trend.segments(series)), 1)

    def test_gaps_are_counted_so_the_screen_can_say_so(self):
        samples = [at(0, pct=10.0), at(100, pct=90.0)]
        series = trend.build(samples, buckets=10)
        self.assertEqual(series.gaps, len(series.points) - 2)


class SummaryTests(unittest.TestCase):
    def test_first_last_low_high(self):
        samples = [at(0, pct=10.0), at(5, pct=90.0), at(10, pct=40.0)]
        series = trend.build(samples, buckets=3)
        self.assertEqual(series.first, 10.0)
        self.assertEqual(series.last, 40.0)
        self.assertEqual(series.low, 10.0)
        self.assertEqual(series.high, 90.0)

    def test_change_is_first_to_last(self):
        series = trend.build([at(0, pct=10.0), at(10, pct=40.0)], buckets=3)
        self.assertAlmostEqual(series.change, 30.0)

    def test_no_change_is_not_the_same_as_unknown(self):
        """0 은 '안 변했다', None 은 '모른다' 다."""

        flat = trend.build([at(0, pct=50.0), at(10, pct=50.0)], buckets=3)
        self.assertEqual(flat.change, 0.0)
        self.assertIsNone(trend.build([]).change)

    def test_an_empty_series_says_so(self):
        series = trend.build([])
        self.assertTrue(series.empty)
        self.assertIsNone(series.last)
        self.assertEqual(trend.segments(series), [])

    def test_samples_without_a_value_are_skipped(self):
        """수집이 실패한 표본은 값이 없다 - 0 으로 세면 그래프가 바닥을 찍는다."""

        series = trend.build([at(0, pct=None), at(5, pct=80.0)], buckets=2)
        self.assertEqual(series.last, 80.0)
        self.assertEqual(series.low, 80.0)

    def test_a_broken_timestamp_is_skipped(self):
        series = trend.build(
            [FakeSample(collected_at="쓰레기", byte_pct=50.0), at(5, pct=80.0)],
            buckets=2,
        )
        self.assertEqual(series.last, 80.0)


class WindowTests(unittest.TestCase):
    def test_a_window_narrows_what_is_charted(self):
        samples = [at(index, pct=float(index)) for index in range(20)]
        series = trend.build(samples, buckets=5, since=START + timedelta(hours=10))
        self.assertGreaterEqual(series.low, 10.0)

    def test_the_window_defines_the_axis_not_the_data(self):
        """창을 정했으면 데이터가 그 안 어디에 있든 축은 창 전체다.

        안 그러면 표본이 하루치뿐일 때 '90일 추세'라며 하루를 꽉 채워 그린다."""

        since = START
        until = START + timedelta(days=90)
        series = trend.build([at(1, pct=50.0), at(2, pct=52.0)],
                             buckets=90, since=since, until=until)
        self.assertEqual(series.started_at, since)
        self.assertEqual(series.ended_at, until)
        self.assertGreater(series.gaps, 80)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
