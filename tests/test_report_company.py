"""보고서의 '그때 서버에서 무엇이 돌았나' 절.

부하 숫자만 있으면 "스캔 중 load 8" 이 스캔이 만든 것인지 마침 다른 사람의
작업 때문인지 가릴 수 없다. 아침에 보고서를 여는 사람이 가장 먼저 묻는 것이
그것이라, 명령을 따로 치지 않아도 보이게 보고서에 싣는다.

## 여기서 지키는 것

- **재지 못한 것과 아무 일도 없던 것을 구분한다.** 빈 표는 "아무것도 안
  돌았다"로 읽히는데 사실은 "아직 못 재고 있다"일 수 있다.
- 우리 것은 '다른 작업' 목록에 넣지 않는다.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from smvwp import i18n, nfsstat, procstat, reports, scan_store, servermon

RUN_ID = "night-1"


def process(pid, comm, user, cpu, rss_kb=1000, ours=False):
    return procstat.ProcessLoad(
        pid=pid, comm=comm, cpu_percent=cpu, rss_kb=rss_kb, state="R",
        user=user, is_ours=ours,
    )


def mount(rtt=0.16, queue=0.31, ops=1000):
    item = nfsstat.MountDelta(
        device="filer:/ifs/a", mount_point="/mnt/caef1201", nfs_version="3",
        seconds=120.0, total_ops=ops, ops_per_second=ops / 120.0,
    )
    item.ops["GETATTR"] = nfsstat.OpDelta(
        name="GETATTR", ops=ops, avg_rtt_ms=rtt, avg_queue_ms=queue
    )
    return item


def sample(minutes_ago=10, processes=(), mounts=()):
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return servermon.ServerSample(
        sampled_at=stamp, interval_seconds=120.0, cpu_count=32,
        cpu_busy_percent=12.0, cpu_iowait_percent=4.5, load_avg_1m=2.4,
        procs_blocked=3, blocked_ours=2, blocked_others=1,
        memory_total_kb=1000, memory_available_kb=600, memory_used_percent=40.0,
        scan_cpu_percent=26.0, scan_rss_kb=12_000, scan_process_count=3,
        processes=list(processes), mounts=list(mounts),
    )


class SectionTests(unittest.TestCase):
    def setUp(self):
        i18n.set_language(i18n.KOREAN)
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.conn = scan_store.connect(self.data_dir)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._close)
        self._add_run()

    def _close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def _add_run(self):
        started = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
        ended = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.conn.execute(
            "INSERT INTO scan_runs (run_id, triggered_by, started_at, ended_at, status)"
            " VALUES (?, ?, ?, ?, ?)",
            (RUN_ID, "cron", started, ended, "completed"),
        )
        self.conn.commit()

    def save(self, item):
        scan_store.save_server_sample(
            self.conn, item, source=scan_store.SOURCE_SCAN, run_id=RUN_ID
        )

    def render(self):
        self.conn.commit()
        lines = []
        reports._append_company_section(lines, self.data_dir)
        return "\n".join(lines)

    def test_other_peoples_jobs_are_listed(self):
        self.save(sample(processes=[
            process(1, "virtuoso", "alice", 180.0, rss_kb=9_000_000),
            process(2, "calibre", "bob", 95.0),
        ]))
        text = self.render()
        self.assertIn("virtuoso", text)
        self.assertIn("alice", text)
        self.assertIn("calibre", text)

    def test_our_own_processes_are_not_listed_as_other_jobs(self):
        """우리가 만든 부하를 '남이 서버를 쓰고 있었다'로 읽으면 안 된다."""

        self.save(sample(processes=[
            process(1, "virtuoso", "alice", 180.0),
            process(999, "du", "svcstore", 26.0, ours=True),
        ]))
        text = self.render()
        self.assertIn("virtuoso", text)
        self.assertNotIn("svcstore", text)

    def test_when_nobody_else_was_there_it_says_so(self):
        """'아무도 없었다'는 것도 판단에 쓰이는 사실이다."""

        self.save(sample(processes=[process(999, "du", "svc", 26.0, ours=True)]))
        self.assertIn(i18n.t("reports.company_alone"), self.render())

    def test_nothing_measured_makes_no_section_at_all(self):
        """빈 표는 '아무것도 안 돌았다'로 읽힌다 - 사실은 못 잰 것이다."""

        self.assertEqual(self.render(), "")

    def test_the_share_that_was_not_ours_is_stated(self):
        self.save(sample(processes=[process(1, "virtuoso", "alice", 180.0)]))
        text = self.render()
        self.assertIn("우리 스캔을 뺀 나머지", text)

    def test_mount_traffic_shows_both_latencies(self):
        self.save(sample(mounts=[mount(rtt=0.16, queue=0.31)]))
        text = self.render()
        self.assertIn("/mnt/caef1201", text)
        self.assertIn("0.16", text)
        self.assertIn("0.31", text)
        # 어느 쪽이 병목인지 읽는 법을 같이 적어 둔다.
        self.assertIn("RPC 슬롯", text)

    def test_the_row_count_is_capped(self):
        """다 실으면 아침에 아무도 안 읽는다."""

        self.save(sample(processes=[
            process(index, f"job{index}", f"user{index}", 100.0 - index)
            for index in range(1, 12)
        ]))
        text = self.render()
        listed = [line for line in text.splitlines() if line.startswith("user")]
        self.assertLessEqual(len(listed), reports.COMPANY_ROWS)

    def test_english_renders_too(self):
        i18n.set_language(i18n.ENGLISH)
        self.addCleanup(i18n.set_language, i18n.KOREAN)
        self.save(sample(processes=[process(1, "virtuoso", "alice", 180.0)]))
        text = self.render()
        self.assertIn("What else the server was doing", text)
        # 키가 그대로 새어 나오지 않는지.
        self.assertNotIn("reports.company", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
