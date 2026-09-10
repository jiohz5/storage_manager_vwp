"""서버 한 시점의 상태를 한 벌로 묶는 부분.

## 여기서 지키는 것

- **우리 것과 남의 것을 가른다.** 그러지 않으면 "스캔 때문에 바빴나, 원래
  바빴나"에 영영 답할 수 없다.
- 잴 수 없는 것을 0 으로 채우지 않는다.
- 재는 행위가 부하가 되지 않는다 - 사용자 이름과 명령줄은 고른 몇 개에만.
"""

import unittest
from unittest.mock import patch

from smvwp import nfsstat, procstat, servermon


def counters(total, idle, iowait=0, steal=0, running=1, blocked=0, cpus=4):
    return procstat.SystemCounters(
        total_ticks=total,
        idle_ticks=idle,
        iowait_ticks=iowait,
        steal_ticks=steal,
        cpu_count=cpus,
        procs_running=running,
        procs_blocked=blocked,
    )


def proc(pid, ticks, rss_kb=1000, state="S", start=1):
    return procstat.ProcInfo(
        pid=pid,
        comm=f"p{pid}",
        state=state,
        ppid=1,
        cpu_ticks=ticks,
        rss_kb=rss_kb,
        threads=1,
        start_ticks=start,
    )


class ShareTests(unittest.TestCase):
    """우리 몫과 남의 몫."""

    def test_others_is_the_whole_minus_ours(self):
        """스캔이 코어 4개 중 1개를 다 썼으면 전체의 25% 다."""

        sample = servermon.ServerSample(
            sampled_at="x", interval_seconds=10,
            cpu_count=4, cpu_busy_percent=40.0, scan_cpu_percent=100.0,
        )
        self.assertAlmostEqual(sample.others_cpu_percent, 15.0)

    def test_without_our_share_the_whole_is_reported_as_is(self):
        sample = servermon.ServerSample(
            sampled_at="x", interval_seconds=10, cpu_count=4, cpu_busy_percent=40.0
        )
        self.assertAlmostEqual(sample.others_cpu_percent, 40.0)

    def test_it_never_goes_negative(self):
        """반올림이나 표본 어긋남으로 우리 몫이 더 커 보일 수 있다."""

        sample = servermon.ServerSample(
            sampled_at="x", interval_seconds=10,
            cpu_count=4, cpu_busy_percent=1.0, scan_cpu_percent=100.0,
        )
        self.assertEqual(sample.others_cpu_percent, 0.0)

    def test_unmeasured_stays_unmeasured(self):
        sample = servermon.ServerSample(sampled_at="x", interval_seconds=10)
        self.assertIsNone(sample.others_cpu_percent)
        self.assertFalse(sample.measured)


class SampleTests(unittest.TestCase):
    """표본 한 벌 만들기."""

    def monitor(self, **kwargs):
        kwargs.setdefault("with_nfs", False)
        return servermon.ServerMonitor(**kwargs)

    def run_two(self, before, after, our=(), monitor=None, seconds=10.0):
        monitor = monitor or self.monitor()
        cpu = [counters(0, 0), counters(1000, 700, iowait=200)]
        with patch.object(procstat, "read_system_counters", side_effect=cpu), \
             patch.object(procstat, "read_memory", return_value=procstat.Memory(
                 total_kb=1000, available_kb=400, swap_total_kb=10, swap_free_kb=10)), \
             patch.object(procstat, "read_loadavg", return_value=procstat.LoadAverage(
                 one=3.5, five=2.0, fifteen=1.0)), \
             patch.object(procstat, "snapshot", side_effect=[before, after]), \
             patch.object(procstat, "our_pids", return_value=list(our)), \
             patch.object(procstat, "enrich") as enrich, \
             patch.object(servermon.time, "monotonic", side_effect=[0.0, seconds]):
            monitor.prime()
            sample = monitor.sample()
        self.enrich = enrich
        return sample

    def test_the_system_numbers_are_carried_over(self):
        sample = self.run_two({}, {})
        self.assertAlmostEqual(sample.cpu_busy_percent, 10.0)
        self.assertAlmostEqual(sample.cpu_iowait_percent, 20.0)
        self.assertAlmostEqual(sample.load_avg_1m, 3.5)
        self.assertAlmostEqual(sample.memory_used_percent, 60.0)
        self.assertEqual(sample.interval_seconds, 10.0)

    def test_our_processes_are_summed_apart(self):
        before = {1: proc(1, 0), 2: proc(2, 0)}
        after = {
            1: proc(1, procstat.CLOCK_TICKS * 10),
            2: proc(2, procstat.CLOCK_TICKS * 5),
        }
        sample = self.run_two(before, after, our=[2])
        self.assertEqual(sample.scan_process_count, 1)
        self.assertAlmostEqual(sample.scan_cpu_percent, 50.0)

    def test_waiting_processes_are_counted_on_both_sides(self):
        """우리 때문에 남이 기다렸는지는 이 구분이 없으면 못 가린다."""

        after = {
            1: proc(1, 0, state="D"),
            2: proc(2, 0, state="D"),
            3: proc(3, 0, state="S"),
        }
        sample = self.run_two({}, after, our=[2])
        self.assertEqual(sample.blocked_ours, 1)
        self.assertEqual(sample.blocked_others, 1)

    def test_only_the_chosen_few_get_names_and_command_lines(self):
        """수백 개를 다 읽으면 재는 행위 자체가 부하가 된다."""

        after = {pid: proc(pid, pid) for pid in range(1, 60)}
        sample = self.run_two({}, after, monitor=self.monitor(top_processes=5))
        self.assertLessEqual(len(sample.processes), 10)   # cpu 상위 + 메모리 상위
        self.enrich.assert_called_once()
        self.assertIs(self.enrich.call_args[0][0], sample.processes)

    def test_the_command_line_can_be_turned_off(self):
        """다른 사용자의 작업 내용이 그대로 남는 곳이다."""

        self.run_two({}, {1: proc(1, 0)}, monitor=self.monitor(with_cmdline=False))
        self.assertFalse(self.enrich.call_args[1]["with_cmdline"])

    def test_processes_can_be_skipped_entirely(self):
        monitor = self.monitor(with_processes=False)
        cpu = [counters(0, 0), counters(1000, 900)]
        with patch.object(procstat, "read_system_counters", side_effect=cpu), \
             patch.object(procstat, "read_memory", return_value=procstat.Memory()), \
             patch.object(procstat, "read_loadavg", return_value=procstat.LoadAverage()), \
             patch.object(procstat, "our_pids", return_value=[]), \
             patch.object(procstat, "snapshot") as snapshot:
            monitor.prime()
            sample = monitor.sample()
        snapshot.assert_not_called()
        self.assertEqual(sample.processes, [])

    def test_sampling_without_priming_does_not_raise(self):
        """부르는 쪽이 순서를 틀려도 측정이 스캔을 막으면 안 된다."""

        monitor = self.monitor(with_processes=False)
        with patch.object(procstat, "read_system_counters", return_value=counters(1, 1)), \
             patch.object(procstat, "read_memory", return_value=procstat.Memory()), \
             patch.object(procstat, "read_loadavg", return_value=procstat.LoadAverage()), \
             patch.object(procstat, "our_pids", return_value=[]):
            sample = monitor.sample()
        self.assertIsNotNone(sample.sampled_at)


class MountTests(unittest.TestCase):
    def test_quiet_mounts_are_not_carried(self):
        """마운트가 수십 개면 대부분이 0 이고, 그 행들이 정작 움직인 것을 묻는다."""

        monitor = servermon.ServerMonitor(with_processes=False)
        busy = nfsstat.MountDelta(device="a:/b", mount_point="/busy", total_ops=500)
        quiet = nfsstat.MountDelta(device="c:/d", mount_point="/quiet", total_ops=0)
        with patch.object(procstat, "read_system_counters", return_value=counters(1, 1)), \
             patch.object(procstat, "read_memory", return_value=procstat.Memory()), \
             patch.object(procstat, "read_loadavg", return_value=procstat.LoadAverage()), \
             patch.object(procstat, "our_pids", return_value=[]), \
             patch.object(nfsstat, "available", return_value=True), \
             patch.object(nfsstat, "read_mountstats", return_value={}), \
             patch.object(nfsstat, "delta", return_value={"/busy": busy, "/quiet": quiet}):
            monitor.prime()
            sample = monitor.sample()
        self.assertEqual([item.mount_point for item in sample.mounts], ["/busy"])

    def test_totals_add_up_across_mounts(self):
        sample = servermon.ServerSample(
            sampled_at="x", interval_seconds=1,
            mounts=[
                nfsstat.MountDelta(device="a", mount_point="/a", ops_per_second=100.0),
                nfsstat.MountDelta(device="b", mount_point="/b", ops_per_second=50.0),
            ],
        )
        self.assertAlmostEqual(sample.nfs_ops_per_second, 150.0)


class TakeOneTests(unittest.TestCase):
    """수집기처럼 잠깐 떴다 사라지는 경우."""

    def test_the_work_still_runs_where_proc_is_missing(self):
        """잴 수 없는 값을 위해 일을 늦추지 않는다 (개발 PC)."""

        done = []
        with patch.object(procstat, "available", return_value=False):
            result = servermon.take_one(work=lambda: done.append(1))
        self.assertIsNone(result)
        self.assertEqual(done, [1])

    def test_the_work_becomes_the_measurement_window(self):
        """수집기는 어차피 df 를 읽느라 몇 초를 쓴다 - 따로 기다릴 이유가 없다."""

        order = []
        monitor = servermon.ServerMonitor(with_processes=False, with_nfs=False)
        with patch.object(procstat, "available", return_value=True), \
             patch.object(servermon, "ServerMonitor", return_value=monitor), \
             patch.object(monitor, "prime", side_effect=lambda: order.append("prime")), \
             patch.object(monitor, "sample", side_effect=lambda: order.append("sample")), \
             patch.object(servermon.time, "sleep"):
            servermon.take_one(work=lambda: order.append("work"), minimum_seconds=0.0)
        self.assertEqual(order, ["prime", "work", "sample"])


class SummaryTests(unittest.TestCase):
    """스캔을 돌려도 되는지 판단할 때 필요한 것은 우리 몫이 아니라 남은 여유다."""

    def samples(self):
        return [
            servermon.ServerSample(
                sampled_at="a", interval_seconds=1, cpu_count=4,
                cpu_busy_percent=40.0, scan_cpu_percent=0.0,
                load_avg_1m=1.0, procs_blocked=2, blocked_others=2,
            ),
            servermon.ServerSample(
                sampled_at="b", interval_seconds=1, cpu_count=4,
                cpu_busy_percent=80.0, scan_cpu_percent=0.0,
                load_avg_1m=5.0, procs_blocked=8, blocked_others=6,
            ),
        ]

    def test_average_and_peak(self):
        summary = servermon.summarize_others(self.samples())
        self.assertAlmostEqual(summary["cpu_avg"], 60.0)
        self.assertAlmostEqual(summary["cpu_peak"], 80.0)
        self.assertAlmostEqual(summary["load_peak"], 5.0)
        self.assertAlmostEqual(summary["blocked_peak"], 6.0)

    def test_nothing_measured_gives_nothing(self):
        empty = [servermon.ServerSample(sampled_at="a", interval_seconds=1)]
        self.assertEqual(servermon.summarize_others(empty), {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
