"""`/proc` 기반 CPU 점유 측정 테스트.

리눅스가 아닌 개발 PC에서도 파싱 로직을 검증할 수 있도록, 실제 `/proc` 대신
가짜 파일을 가리키게 해서 확인한다. 필드 위치를 잘못 세는 실수는 눈으로는
안 보이고 숫자만 조용히 틀리므로 여기서 고정한다.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import loadstat


def _write(tmp: Path, name: str, text: str) -> Path:
    target = tmp / name
    target.write_text(text, encoding="utf-8")
    return target


# stat(5) 필드: pid (comm) state ppid pgrp session tty tpgid flags
#               minflt cminflt majflt cmajflt utime stime cutime cstime ...
def _self_stat(utime: int, stime: int, cutime: int, cstime: int) -> str:
    head = "1234 (python3) S 1 1234 1234 0 -1 4194304 100 200 0 0"
    tail = " 20 0 1 0 900 0 0"
    return f"{head} {utime} {stime} {cutime} {cstime}{tail}\n"


class SelfJiffiesTests(unittest.TestCase):
    def test_sums_own_and_reaped_children(self):
        """자식(du/find)의 CPU가 빠지면 스캔 작업량이 통째로 안 잡힌다."""

        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), "self_stat", _self_stat(10, 5, 100, 50))
            with patch.object(loadstat, "PROC_SELF_STAT", path):
                self.assertEqual(loadstat._self_jiffies(), 165)

    def test_handles_space_and_parens_in_process_name(self):
        """comm에 공백/괄호가 들어가도 필드가 밀리면 안 된다."""

        raw = "1234 (my (weird) proc) S 1 1234 1234 0 -1 0 0 0 0 0 7 3 0 0 20 0\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), "self_stat", raw)
            with patch.object(loadstat, "PROC_SELF_STAT", path):
                self.assertEqual(loadstat._self_jiffies(), 10)

    def test_missing_file_is_none_not_zero(self):
        """못 읽었을 때 0을 돌려주면 '부하 없음'으로 잘못 읽힌다."""

        with patch.object(loadstat, "PROC_SELF_STAT", Path("/nonexistent/stat")):
            self.assertIsNone(loadstat._self_jiffies())


class SystemJiffiesTests(unittest.TestCase):
    def test_sums_all_cpu_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), "stat", "cpu  100 20 30 800 10 0 5 0 0 0\ncpu0 1 2\n")
            with patch.object(loadstat, "PROC_STAT", path):
                self.assertEqual(loadstat._system_jiffies(), 965)


class SamplerTests(unittest.TestCase):
    def _sampler_over(self, tmp: Path, first, second):
        """두 시점의 /proc 값을 순서대로 보여주며 한 구간을 측정한다."""

        self_path = _write(tmp, "self_stat", _self_stat(*first[0]))
        stat_path = _write(tmp, "stat", f"cpu  {first[1]} 0 0 0 0 0 0 0 0 0\n")
        with patch.object(loadstat, "PROC_SELF_STAT", self_path), \
                patch.object(loadstat, "PROC_STAT", stat_path):
            sampler = loadstat.Sampler()
            self_path.write_text(_self_stat(*second[0]), encoding="utf-8")
            stat_path.write_text(f"cpu  {second[1]} 0 0 0 0 0 0 0 0 0\n", encoding="utf-8")
            return sampler.take()

    def test_share_of_system_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 이 작업 25 jiffies / 시스템 전체 100 jiffies = 25%
            usage = self._sampler_over(
                Path(tmp), ((0, 0, 0, 0), 1000), ((10, 5, 7, 3), 1100)
            )
        self.assertAlmostEqual(usage.system_percent, 25.0)

    def test_top_percent_is_scaled_by_core_count(self):
        """top은 코어 1개를 100%로 센다 - 두 기준을 섞으면 오해가 생긴다."""

        with tempfile.TemporaryDirectory() as tmp:
            with patch("os.cpu_count", return_value=8):
                usage = self._sampler_over(
                    Path(tmp), ((0, 0, 0, 0), 1000), ((10, 5, 7, 3), 1100)
                )
        self.assertAlmostEqual(usage.system_percent, 25.0)
        self.assertAlmostEqual(usage.top_percent, 200.0)  # 8코어 중 2개어치

    def test_zero_length_interval_reports_unmeasured(self):
        """구간이 안 움직였으면 0%가 아니라 '측정 안 됨'이어야 한다."""

        with tempfile.TemporaryDirectory() as tmp:
            usage = self._sampler_over(
                Path(tmp), ((10, 5, 0, 0), 1000), ((10, 5, 0, 0), 1000)
            )
        self.assertIsNone(usage.system_percent)
        self.assertFalse(usage.measured)


class AccumulatorTests(unittest.TestCase):
    def test_no_samples_yields_empty_summary(self):
        """리눅스가 아니면 조용히 비어 있어야 한다 (예외로 스캔을 죽이지 않는다)."""

        with patch.object(loadstat, "available", return_value=False):
            accumulator = loadstat.Accumulator()
            accumulator.sample()
            summary = accumulator.summary()
        self.assertEqual(summary.samples, 0)
        self.assertIsNone(summary.system_percent_avg)

    def test_summary_reports_average_and_peak(self):
        accumulator = loadstat.Accumulator()
        accumulator._system = [10.0, 30.0, 20.0]
        accumulator._top = [40.0, 120.0, 80.0]
        summary = accumulator.summary()
        self.assertEqual(summary.samples, 3)
        self.assertAlmostEqual(summary.system_percent_avg, 20.0)
        self.assertAlmostEqual(summary.system_percent_peak, 30.0)
        self.assertAlmostEqual(summary.top_percent_peak, 120.0)



class MemoryTests(unittest.TestCase):
    """메모리는 최고치를 남긴다 - du/find는 끝나면 사라져 사후 관측이 안 된다."""

    def test_reads_vmrss_from_status(self):
        status = (
            "Name:\tpython3\nState:\tS (sleeping)\nVmPeak:\t  900000 kB\n"
            "VmRSS:\t  412345 kB\nThreads:\t4\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), "status", status)
            with patch.object(loadstat, "PROC_SELF_STATUS", path):
                self.assertEqual(loadstat._self_rss_kb(), 412345)

    def test_missing_status_is_none(self):
        with patch.object(loadstat, "PROC_SELF_STATUS", Path("/nonexistent/status")):
            self.assertIsNone(loadstat._self_rss_kb())

    def test_meminfo_uses_available_not_free(self):
        """MemFree는 캐시 때문에 거의 항상 작게 나온다 - 그걸 부족으로 읽으면
        매번 거짓 경보가 된다."""

        meminfo = (
            "MemTotal:       32000000 kB\nMemFree:          500000 kB\n"
            "MemAvailable:   20000000 kB\nBuffers:          100000 kB\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), "meminfo", meminfo)
            with patch.object(loadstat, "PROC_MEMINFO", path):
                total, available = loadstat._system_memory_kb()
        self.assertEqual(total, 32000000)
        self.assertEqual(available, 20000000)

    def test_summary_reports_peak_and_percent(self):
        accumulator = loadstat.Accumulator()
        accumulator._rss_peak = 320000            # 320MB
        with patch.object(loadstat, "_system_memory_kb", return_value=(32000000, None)):
            summary = accumulator.summary()
        self.assertEqual(summary.rss_peak_kb, 320000)
        self.assertAlmostEqual(summary.memory_peak_percent, 1.0)

    def test_percent_is_none_when_total_unknown(self):
        """전체 메모리를 모르면 퍼센트를 지어내지 않는다."""

        accumulator = loadstat.Accumulator()
        accumulator._rss_peak = 320000
        with patch.object(loadstat, "_system_memory_kb", return_value=(None, None)):
            summary = accumulator.summary()
        self.assertIsNone(summary.memory_peak_percent)

if __name__ == "__main__":
    unittest.main()
