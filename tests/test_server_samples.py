"""서버 부하 이력 - 저장, 조회, 보존, 그리고 두 수집 경로.

## 이것이 답하려는 질문

지금까지 남은 것은 "우리 스캔이 CPU 몇 %를 썼나"뿐이었다. 그것만으로는
판단이 안 된다:

- 그 시각에 서버에서 **다른 무엇이** 돌고 있었나.
- 스캔이 없는 **평소** 이 서버는 어떤 모습인가 (비교 대상).
- NFS 쪽으로는 얼마나 나갔고, 느렸다면 파일서버 탓인가 우리 슬롯 탓인가.

## 여기서 지키는 것

- 못 잰 표본을 저장하지 않는다. 빈 행이 쌓이면 "그 시각엔 한가했다"로 읽힌다.
- 재는 쪽이 실패해도 수집/스캔은 계속된다.
- 촘촘한 표본과 성긴 표본의 보존 기간이 서로를 깎지 않는다.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from smvwp import nfsstat, procstat, scan_store, servermon


def iso(days_ago=0, hours=0):
    moment = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours)
    return moment.isoformat()


def process(pid=1, comm="virtuoso", user="alice", cpu=50.0, rss_kb=1000, ours=False):
    return procstat.ProcessLoad(
        pid=pid, comm=comm, cpu_percent=cpu, rss_kb=rss_kb, state="R",
        user=user, is_ours=ours, cmdline=f"/bin/{comm} --run",
    )


def mount(point="/mnt/a", ops=1000, rtt=2.0, queue=0.5):
    item = nfsstat.MountDelta(
        device="filer:/ifs/a", mount_point=point, nfs_version="3",
        seconds=10.0, total_ops=ops, ops_per_second=ops / 10.0,
        read_bytes=5_000_000, write_bytes=1000,
    )
    item.ops["GETATTR"] = nfsstat.OpDelta(
        name="GETATTR", ops=ops, avg_rtt_ms=rtt, avg_queue_ms=queue
    )
    return item


def sample(sampled_at=None, busy=40.0, ours_cpu=20.0, processes=(), mounts=()):
    return servermon.ServerSample(
        sampled_at=sampled_at or iso(),
        interval_seconds=10.0,
        cpu_count=32,
        cpu_busy_percent=busy,
        cpu_iowait_percent=5.0,
        load_avg_1m=3.0,
        procs_blocked=2,
        blocked_ours=1,
        blocked_others=1,
        memory_total_kb=1000,
        memory_available_kb=400,
        memory_used_percent=60.0,
        scan_cpu_percent=ours_cpu,
        scan_rss_kb=500,
        scan_process_count=2,
        processes=list(processes),
        mounts=list(mounts),
    )


class StoreBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.conn = scan_store.connect(self.data_dir)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)


class SaveTests(StoreBase):
    def test_round_trip(self):
        scan_store.save_server_sample(self.conn, sample())
        rows = scan_store.server_samples(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["cpu_busy_percent"], 40.0)
        self.assertEqual(rows[0]["source"], scan_store.SOURCE_COLLECTOR)

    def test_an_unmeasured_sample_is_not_stored(self):
        """빈 행이 쌓이면 나중에 '그 시각엔 한가했다'로 잘못 읽힌다."""

        blank = servermon.ServerSample(sampled_at=iso(), interval_seconds=1.0)
        self.assertIsNone(scan_store.save_server_sample(self.conn, blank))
        self.assertEqual(scan_store.server_samples(self.conn), [])

    def test_nothing_at_all_is_not_an_error(self):
        self.assertIsNone(scan_store.save_server_sample(self.conn, None))

    def test_processes_hang_off_the_sample(self):
        sample_id = scan_store.save_server_sample(
            self.conn, sample(processes=[process(1), process(2, comm="du", ours=True)])
        )
        rows = scan_store.sample_processes(self.conn, sample_id)
        self.assertEqual([row["comm"] for row in rows], ["virtuoso", "du"])
        self.assertEqual(rows[1]["is_ours"], 1)

    def test_mount_traffic_is_stored_with_both_latencies(self):
        """왕복과 큐를 갈라 두지 않으면 처방이 정반대로 나온다."""

        sample_id = scan_store.save_server_sample(
            self.conn, sample(mounts=[mount(rtt=2.0, queue=8.0)])
        )
        row = scan_store.sample_mounts(self.conn, sample_id)[0]
        self.assertAlmostEqual(row["avg_rtt_ms"], 2.0)
        self.assertAlmostEqual(row["avg_queue_ms"], 8.0)
        self.assertGreater(row["queue_share"], 0.7)
        self.assertEqual(row["getattr_ops"], 1000)

    def test_the_run_it_belonged_to_is_kept(self):
        scan_store.save_server_sample(
            self.conn, sample(), source=scan_store.SOURCE_SCAN, run_id="run-1"
        )
        rows = scan_store.server_samples(self.conn, run_id="run-1")
        self.assertEqual(len(rows), 1)


class RangeTests(StoreBase):
    def setUp(self):
        super().setUp()
        for days in (0, 2, 10):
            scan_store.save_server_sample(self.conn, sample(sampled_at=iso(days)))

    def test_a_window_selects_only_what_is_inside(self):
        rows = scan_store.server_samples(self.conn, since=iso(3))
        self.assertEqual(len(rows), 2)

    def test_results_come_back_in_time_order(self):
        rows = scan_store.server_samples(self.conn)
        stamps = [row["sampled_at"] for row in rows]
        self.assertEqual(stamps, sorted(stamps))

    def test_the_source_can_be_narrowed(self):
        scan_store.save_server_sample(
            self.conn, sample(), source=scan_store.SOURCE_SCAN, run_id="r"
        )
        rows = scan_store.server_samples(self.conn, source=scan_store.SOURCE_SCAN)
        self.assertEqual(len(rows), 1)


class BusiestTests(StoreBase):
    """이 시간대에 서버를 쓴 것이 무엇인가."""

    def setUp(self):
        super().setUp()
        scan_store.save_server_sample(self.conn, sample(processes=[
            process(1, comm="virtuoso", user="alice", cpu=200.0, rss_kb=100),
            process(2, comm="du", user="svc", cpu=20.0, rss_kb=50, ours=True),
        ]))
        scan_store.save_server_sample(self.conn, sample(processes=[
            # 같은 작업이 죽었다 살아나 pid 가 바뀌었다.
            process(9, comm="virtuoso", user="alice", cpu=100.0, rss_kb=90),
            process(3, comm="hog", user="bob", cpu=1.0, rss_kb=9_000_000),
        ]))

    def test_the_same_job_is_one_row_even_with_a_new_pid(self):
        """사람이 알고 싶은 것은 '누구의 무슨 작업'이지 번호가 아니다."""

        rows = scan_store.busiest_processes(self.conn)
        virtuoso = [row for row in rows if row["comm"] == "virtuoso"]
        self.assertEqual(len(virtuoso), 1)
        self.assertEqual(virtuoso[0]["samples"], 2)
        self.assertAlmostEqual(virtuoso[0]["cpu_peak"], 200.0)

    def test_sorted_by_cpu(self):
        rows = scan_store.busiest_processes(self.conn, by=scan_store.BUSIEST_BY_CPU)
        self.assertEqual(rows[0]["comm"], "virtuoso")

    def test_sorted_by_memory(self):
        """CPU 는 안 쓰면서 메모리만 물고 있는 것은 CPU 정렬에 영영 안 잡힌다."""

        rows = scan_store.busiest_processes(self.conn, by=scan_store.BUSIEST_BY_MEMORY)
        self.assertEqual(rows[0]["comm"], "hog")

    def test_ours_is_marked_so_the_load_can_be_attributed(self):
        rows = scan_store.busiest_processes(self.conn)
        ours = {row["comm"]: row["is_ours"] for row in rows}
        self.assertEqual(ours["du"], 1)
        self.assertEqual(ours["virtuoso"], 0)

    def test_a_window_outside_the_data_gives_nothing(self):
        self.assertEqual(scan_store.busiest_processes(self.conn, since=iso(-1)), [])


class MountActivityTests(StoreBase):
    def test_mounts_are_summed_and_ranked(self):
        scan_store.save_server_sample(self.conn, sample(mounts=[
            mount("/mnt/busy", ops=5000), mount("/mnt/quiet", ops=10),
        ]))
        scan_store.save_server_sample(self.conn, sample(mounts=[mount("/mnt/busy", ops=3000)]))
        rows = scan_store.mount_activity(self.conn)
        self.assertEqual(rows[0]["mount_point"], "/mnt/busy")
        self.assertEqual(rows[0]["total_ops"], 8000)
        self.assertEqual(rows[0]["samples"], 2)


class PruneTests(StoreBase):
    def test_old_samples_and_their_children_go_together(self):
        old = scan_store.save_server_sample(
            self.conn, sample(sampled_at=iso(100), processes=[process()])
        )
        scan_store.save_server_sample(self.conn, sample(processes=[process()]))
        scan_store.prune_server_samples(self.conn, retention_days=90)
        self.assertEqual(len(scan_store.server_samples(self.conn)), 1)
        # 자식을 남기면 어느 것이 고아인지 알 방법이 없어진다.
        self.assertEqual(scan_store.sample_processes(self.conn, old), [])

    def test_the_two_kinds_do_not_prune_each_other(self):
        """촘촘한 쪽과 성긴 쪽은 값어치가 달라 보존 기간이 다르다."""

        scan_store.save_server_sample(self.conn, sample(sampled_at=iso(40)))
        scan_store.save_server_sample(
            self.conn, sample(sampled_at=iso(40)),
            source=scan_store.SOURCE_SCAN, run_id="r",
        )
        scan_store.prune_server_samples(
            self.conn, retention_days=30, source=scan_store.SOURCE_SCAN
        )
        left = scan_store.server_samples(self.conn)
        self.assertEqual([row["source"] for row in left], [scan_store.SOURCE_COLLECTOR])


class RecorderTests(unittest.TestCase):
    """스캔 중 표본 - 부하 숫자보다 뜸하게 '무엇이 돌았나'를 뜬다."""

    def recorder(self, interval=30.0, server_interval=120.0, monitor=None):
        from smvwp import loadstat

        return loadstat.Recorder(
            interval_seconds=interval,
            server_interval_seconds=server_interval,
            server_monitor=monitor,
        )

    class FakeMonitor:
        def __init__(self):
            self.primed = 0
            self.taken = 0

        def prime(self):
            self.primed += 1

        def sample(self):
            self.taken += 1
            return sample()

    def test_the_process_list_is_taken_less_often_than_the_numbers(self):
        """`/proc` 을 수백 번 읽는 일이라 30초마다 밤새 하면 재는 쪽이 부담이 된다."""

        monitor = self.FakeMonitor()
        recorder = self.recorder(interval=30.0, server_interval=120.0, monitor=monitor)
        for _ in range(8):
            recorder.tick_once()
        self.assertEqual(monitor.taken, 2)          # 4틱마다 한 번
        self.assertEqual(len(recorder.samples()), 8)

    def test_a_slow_interval_still_samples_every_tick(self):
        """주기가 이미 길면 더 띄엄띄엄 볼 이유가 없다."""

        monitor = self.FakeMonitor()
        recorder = self.recorder(interval=300.0, server_interval=120.0, monitor=monitor)
        recorder.tick_once()
        self.assertEqual(monitor.taken, 1)

    def test_the_baseline_primes_the_monitor(self):
        """기준점을 안 잡으면 첫 표본이 비고 스캔 초반이 시계열에서 사라진다."""

        monitor = self.FakeMonitor()
        recorder = self.recorder(monitor=monitor)
        recorder.baseline(warmup_seconds=0.0)
        self.assertEqual(monitor.primed, 1)

    def test_a_failing_monitor_does_not_stop_the_recording(self):
        """재는 쪽 때문에 스캔이 멈추면 본말이 뒤집힌다."""

        class Broken(self.FakeMonitor):
            def sample(self):
                raise OSError("boom")

        recorder = self.recorder(interval=30.0, server_interval=30.0, monitor=Broken())
        recorder.tick_once()
        self.assertEqual(len(recorder.samples()), 1)
        self.assertEqual(recorder.server_samples(), [])

    def test_without_a_monitor_nothing_extra_happens(self):
        recorder = self.recorder()
        recorder.tick_once()
        self.assertEqual(recorder.server_samples(), [])


class CollectorWiringTests(unittest.TestCase):
    """수집기가 도는 김에 한 벌 남기는가."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)

    def config(self):
        from smvwp import config as config_module

        return config_module.AppConfig(settings=config_module.Settings(), accounts=[])

    def run_cycle(self, take_one):
        from smvwp import cycle

        with patch.object(cycle.servermon, "take_one", side_effect=take_one), \
             patch.object(cycle.collector, "collect_all", return_value=[]), \
             patch.object(cycle.reports, "ensure_scheduled"), \
             patch.object(cycle.forecast_notify, "build_forecasts", return_value=[]), \
             patch.object(cycle.forecast_notify, "notify_forecasts"):
            cycle.run_collection_cycle(self.data_dir, self.config())

    def test_a_sample_lands_in_the_scan_database(self):
        self.run_cycle(lambda work, with_cmdline: (work(), sample())[1])
        conn = scan_store.connect(self.data_dir)
        self.addCleanup(conn.close)
        rows = scan_store.server_samples(conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], scan_store.SOURCE_COLLECTOR)

    def test_the_df_work_still_runs_when_measuring_blows_up(self):
        """부가 정보 때문에 수집 자체가 실패로 끝나면 안 된다."""

        done = []

        def explode(work, with_cmdline):
            raise OSError("no /proc")

        from smvwp import cycle

        with patch.object(cycle.servermon, "take_one", side_effect=explode), \
             patch.object(cycle.collector, "collect_all", side_effect=lambda *a, **k: done.append(1) or []), \
             patch.object(cycle.reports, "ensure_scheduled"), \
             patch.object(cycle.forecast_notify, "build_forecasts", return_value=[]), \
             patch.object(cycle.forecast_notify, "notify_forecasts"):
            cycle.run_collection_cycle(self.data_dir, self.config())
        self.assertEqual(done, [1])

    def test_the_command_line_switch_is_honoured(self):
        """다른 사용자의 작업 내용이 그대로 남는 곳이라 끌 수 있어야 한다."""

        from smvwp import config as config_module, cycle

        seen = {}

        def remember(work, with_cmdline):
            seen["cmdline"] = with_cmdline
            work()
            return None

        settings = config_module.Settings()
        settings.record_process_cmdline = False
        config = config_module.AppConfig(settings=settings, accounts=[])
        with patch.object(cycle.servermon, "take_one", side_effect=remember), \
             patch.object(cycle.collector, "collect_all", return_value=[]), \
             patch.object(cycle.reports, "ensure_scheduled"), \
             patch.object(cycle.forecast_notify, "build_forecasts", return_value=[]), \
             patch.object(cycle.forecast_notify, "notify_forecasts"):
            cycle.run_collection_cycle(self.data_dir, config)
        self.assertFalse(seen["cmdline"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
