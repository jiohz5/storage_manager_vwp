"""서버에서 무엇이 돌고 있는지 읽는 부분.

## 여기서 지키는 것

- **파싱은 개발 PC 에서도 시험할 수 있어야 한다.** `/proc` 이 없는 곳에서
  못 도는 시험은 반입 장비에서 처음 도는 코드가 된다.
- 두 시점의 차이로만 사용률을 낼 수 있다. 기준점이 없을 때 0 을 써서
  "한가했다"로 읽히게 하면 안 된다.
- pid 는 돌려 쓰인다. 같은 번호가 같은 프로세스라는 보장이 없다.
"""

import unittest

from smvwp import procstat


PROC_STAT = """cpu  1000 20 300 8000 500 0 30 5 0 0
cpu0 500 10 150 4000 250 0 15 2 0 0
cpu1 500 10 150 4000 250 0 15 3 0 0
intr 12345
ctxt 999999
procs_running 3
procs_blocked 7
"""

MEMINFO = """MemTotal:       65805864 kB
MemFree:         1234567 kB
MemAvailable:   40000000 kB
Buffers:          200000 kB
SwapTotal:       8388604 kB
SwapFree:        8000000 kB
"""


class SystemCounterTests(unittest.TestCase):
    def test_cpu_totals_and_core_count(self):
        counters = procstat.parse_system_counters(PROC_STAT)
        self.assertEqual(counters.total_ticks, 1000 + 20 + 300 + 8000 + 500 + 0 + 30 + 5)
        self.assertEqual(counters.idle_ticks, 8000)
        self.assertEqual(counters.iowait_ticks, 500)
        self.assertEqual(counters.steal_ticks, 5)
        self.assertEqual(counters.cpu_count, 2)

    def test_blocked_processes_are_read(self):
        """D 상태로 막힌 개수가 I/O 압박의 가장 곧은 신호다."""

        counters = procstat.parse_system_counters(PROC_STAT)
        self.assertEqual(counters.procs_blocked, 7)
        self.assertEqual(counters.procs_running, 3)

    def test_garbage_does_not_raise(self):
        counters = procstat.parse_system_counters("cpu not numbers here\n")
        self.assertIsNone(counters.total_ticks)


class CpuUsageTests(unittest.TestCase):
    def test_busy_excludes_idle_and_iowait(self):
        before = procstat.SystemCounters(total_ticks=0, idle_ticks=0, iowait_ticks=0, steal_ticks=0)
        after = procstat.SystemCounters(
            total_ticks=1000, idle_ticks=700, iowait_ticks=200, steal_ticks=0
        )
        usage = procstat.cpu_usage(before, after)
        self.assertAlmostEqual(usage.busy_percent, 10.0)
        self.assertAlmostEqual(usage.iowait_percent, 20.0)

    def test_a_still_interval_says_unknown_not_zero(self):
        """0 으로 채우면 '한가했다'로 잘못 읽힌다."""

        same = procstat.SystemCounters(total_ticks=500, idle_ticks=400, iowait_ticks=0)
        usage = procstat.cpu_usage(same, same)
        self.assertIsNone(usage.busy_percent)

    def test_without_proc_everything_is_unknown(self):
        usage = procstat.cpu_usage(procstat.SystemCounters(), procstat.SystemCounters())
        self.assertIsNone(usage.busy_percent)


class MemoryTests(unittest.TestCase):
    def test_available_not_free(self):
        """리눅스는 남는 메모리를 캐시로 채운다 - MemFree 를 부족으로 읽으면 거짓 경보다."""

        memory = procstat.parse_memory(MEMINFO)
        self.assertEqual(memory.available_kb, 40000000)
        self.assertAlmostEqual(memory.used_percent, (65805864 - 40000000) / 65805864 * 100)

    def test_swap_is_read(self):
        memory = procstat.parse_memory(MEMINFO)
        self.assertEqual(memory.swap_used_kb, 8388604 - 8000000)

    def test_missing_fields_stay_unknown(self):
        memory = procstat.parse_memory("MemTotal: 100 kB\n")
        self.assertIsNone(memory.used_percent)
        self.assertIsNone(memory.swap_used_kb)


class LoadAverageTests(unittest.TestCase):
    def test_all_three_windows(self):
        load = procstat.parse_loadavg("0.52 1.31 2.04 3/1234 56789\n")
        self.assertAlmostEqual(load.one, 0.52)
        self.assertAlmostEqual(load.fifteen, 2.04)
        self.assertEqual(load.runnable, 3)
        self.assertEqual(load.total_procs, 1234)

    def test_garbage_is_not_fatal(self):
        self.assertIsNone(procstat.parse_loadavg("").one)


class ProcessLineTests(unittest.TestCase):
    """`/proc/<pid>/stat` 한 줄 읽기."""

    def line(self, comm="python", utime=100, stime=50, rss_pages=1000, start=777):
        fields = [str(index) for index in range(3, 53)]
        fields[14 - 3] = str(utime)
        fields[15 - 3] = str(stime)
        fields[20 - 3] = "8"           # threads
        fields[22 - 3] = str(start)
        fields[24 - 3] = str(rss_pages)
        fields[42 - 3] = "900"         # blkio ticks
        fields[0] = "R"                # state
        fields[1] = "42"               # ppid
        return f"1234 ({comm}) " + " ".join(fields)

    def test_it_reads_the_fields_we_use(self):
        info = procstat.parse_proc_stat(1234, self.line())
        self.assertEqual(info.comm, "python")
        self.assertEqual(info.state, "R")
        self.assertEqual(info.ppid, 42)
        self.assertEqual(info.cpu_ticks, 150)
        self.assertEqual(info.threads, 8)
        self.assertEqual(info.rss_kb, 1000 * procstat.PAGE_KB)
        self.assertEqual(info.blkio_ticks, 900)

    def test_a_name_with_spaces_and_brackets_does_not_shift_the_columns(self):
        """`(a b)` 같은 이름이 실제로 있다. 앞에서부터 split 하면 전부 밀린다."""

        info = procstat.parse_proc_stat(1234, self.line(comm="my (odd) name"))
        self.assertEqual(info.comm, "my (odd) name")
        self.assertEqual(info.cpu_ticks, 150)

    def test_a_truncated_line_is_skipped_not_guessed(self):
        self.assertIsNone(procstat.parse_proc_stat(1, "1 (x) R 2 3"))

    def test_a_line_without_the_name_is_skipped(self):
        self.assertIsNone(procstat.parse_proc_stat(1, "garbage"))


def info(pid, ticks, rss_kb=1000, start=100, blkio=None, comm="job"):
    return procstat.ProcInfo(
        pid=pid,
        comm=comm,
        state="R",
        ppid=1,
        cpu_ticks=ticks,
        rss_kb=rss_kb,
        threads=1,
        start_ticks=start,
        blkio_ticks=blkio,
    )


class DiffTests(unittest.TestCase):
    def test_cpu_percent_is_the_top_convention(self):
        """코어 하나를 다 쓰면 100%. top 을 같이 띄운 사람과 숫자가 맞아야 한다."""

        before = {7: info(7, 0)}
        after = {7: info(7, procstat.CLOCK_TICKS * 10)}
        loads = procstat.diff(before, after, seconds=10.0)
        self.assertAlmostEqual(loads[0].cpu_percent, 100.0)

    def test_a_process_born_mid_interval_has_no_baseline(self):
        """누적값을 구간값으로 실으면 그 순간 엄청난 부하가 있었던 것처럼 보인다."""

        loads = procstat.diff({}, {7: info(7, 999999)}, seconds=10.0)
        self.assertEqual(loads[0].cpu_percent, 0.0)
        # 메모리는 그대로 싣는다 - 방금 뜬 거대한 프로세스가 통째로 빠지면 안 된다.
        self.assertEqual(loads[0].rss_kb, 1000)

    def test_a_recycled_pid_is_not_compared(self):
        """같은 번호라도 시작 시각이 다르면 다른 프로세스다 - 음수 사용률이 나온다."""

        before = {7: info(7, 5000, start=100)}
        after = {7: info(7, 10, start=999)}
        loads = procstat.diff(before, after, seconds=10.0)
        self.assertEqual(loads[0].cpu_percent, 0.0)

    def test_a_process_that_went_away_is_dropped(self):
        loads = procstat.diff({7: info(7, 0)}, {}, seconds=10.0)
        self.assertEqual(loads, [])

    def test_our_own_processes_are_marked(self):
        """이 표시가 없으면 나중에 부하가 우리 것인지 남의 것인지 못 가린다."""

        after = {7: info(7, 0), 8: info(8, 0)}
        loads = procstat.diff({}, after, seconds=1.0, our_pids=[8])
        marked = {item.pid: item.is_ours for item in loads}
        self.assertEqual(marked, {7: False, 8: True})

    def test_io_wait_is_read_when_the_kernel_offers_it(self):
        before = {7: info(7, 0, blkio=0)}
        after = {7: info(7, 0, blkio=procstat.CLOCK_TICKS * 5)}
        loads = procstat.diff(before, after, seconds=10.0)
        self.assertAlmostEqual(loads[0].blkio_percent, 50.0)

    def test_a_kernel_without_delay_accounting_says_unknown(self):
        before = {7: info(7, 0, blkio=None)}
        after = {7: info(7, 0, blkio=None)}
        self.assertIsNone(procstat.diff(before, after, 10.0)[0].blkio_percent)

    def test_a_zero_interval_gives_nothing(self):
        self.assertEqual(procstat.diff({}, {7: info(7, 0)}, seconds=0.0), [])


class TopTests(unittest.TestCase):
    def loads(self):
        return [
            procstat.ProcessLoad(pid=1, comm="idle", cpu_percent=0.0, rss_kb=50, state="S"),
            procstat.ProcessLoad(pid=2, comm="busy", cpu_percent=90.0, rss_kb=100, state="R"),
            procstat.ProcessLoad(pid=3, comm="fat", cpu_percent=1.0, rss_kb=99999, state="S"),
        ]

    def test_sorted_by_cpu(self):
        picked = procstat.top(self.loads(), procstat.BY_CPU, limit=2)
        self.assertEqual([item.pid for item in picked], [2, 3])

    def test_sorted_by_memory(self):
        picked = procstat.top(self.loads(), procstat.BY_MEMORY, limit=2)
        self.assertEqual([item.pid for item in picked], [3, 2])

    def test_merging_keeps_the_memory_hog_that_cpu_would_miss(self):
        """CPU 는 안 쓰면서 메모리만 물고 있는 프로세스는 CPU 상위에 영영 안 잡힌다."""

        picked = procstat.merge_top(self.loads(), limit=1)
        self.assertEqual(sorted(item.pid for item in picked), [2, 3])

    def test_merging_does_not_duplicate(self):
        one = [procstat.ProcessLoad(pid=9, comm="x", cpu_percent=5.0, rss_kb=5, state="R")]
        self.assertEqual(len(procstat.merge_top(one, limit=5)), 1)


class UserNameTests(unittest.TestCase):
    def test_an_unknown_uid_falls_back_to_the_number(self):
        procstat._USER_CACHE.clear()
        self.addCleanup(procstat._USER_CACHE.clear)
        self.assertEqual(procstat.user_name(4294967000), "4294967000")

    def test_no_uid_no_name(self):
        self.assertIsNone(procstat.user_name(None))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
