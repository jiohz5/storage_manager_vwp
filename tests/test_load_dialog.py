"""서버 부하 이력 창을 실제로 띄워 본다 (화면 없이).

## 여기서 지키는 것

- **아직 안 쌓인 것과 한가한 것을 구분한다.** 빈 표를 그냥 두면 "서버가
  한가하다"로 읽히는데, 사실은 아직 못 잰 것이다.
- 우리 스캔이 섞인 시간대를 표시한다. 안 하면 "새벽은 한가하다"를 그대로
  믿게 되는데 그 한가함 안에 우리가 들어 있다.
- 대기(queue)가 왕복(rtt)보다 큰 마운트를 눈에 띄게 한다 - 그때 병렬을 줄이면
  정확히 반대 처방이다.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover - PyQt5 없는 환경
    HAVE_QT = False

from smvwp import config as config_module, nfsstat, procstat, scan_store, servermon

_APP = None


def _app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
        from smvwp.gui import theme

        theme.apply(_APP)
    return _APP


def sample(hours_ago=1, busy=40.0, scanning=False, blocked_others=0,
           processes=(), mounts=()):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return servermon.ServerSample(
        sampled_at=stamp, interval_seconds=15.0, cpu_count=32,
        cpu_busy_percent=busy, cpu_iowait_percent=2.0, load_avg_1m=busy / 8,
        procs_blocked=blocked_others, blocked_others=blocked_others,
        memory_total_kb=1000, memory_available_kb=600, memory_used_percent=40.0,
        scan_cpu_percent=26.0 if scanning else None,
        scan_process_count=3 if scanning else 0,
        processes=list(processes), mounts=list(mounts),
    )


def process(pid, comm, user, cpu, rss_kb=1000, ours=False):
    return procstat.ProcessLoad(
        pid=pid, comm=comm, cpu_percent=cpu, rss_kb=rss_kb, state="R",
        user=user, is_ours=ours, cmdline=f"/tools/{comm} -run",
    )


def mount(point="/mnt/a", rtt=0.2, queue=0.05, ops=1000):
    item = nfsstat.MountDelta(
        device="filer:/ifs/a", mount_point=point, nfs_version="3",
        seconds=15.0, total_ops=ops, ops_per_second=ops / 15.0,
    )
    item.ops["GETATTR"] = nfsstat.OpDelta(
        name="GETATTR", ops=ops, avg_rtt_ms=rtt, avg_queue_ms=queue
    )
    return item


@unittest.skipUnless(HAVE_QT, "PyQt5 없음")
class LoadDialogTests(unittest.TestCase):
    def setUp(self):
        _app()
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.data_dir = Path(self.tmp.name) / "data"
        self.config = config_module.load_config(self.data_dir)
        self.conn = scan_store.connect(self.data_dir)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._close)

    def _close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def save(self, item, scanning=False):
        scan_store.save_server_sample(
            self.conn, item,
            source=scan_store.SOURCE_SCAN if scanning else scan_store.SOURCE_COLLECTOR,
            run_id="night-1" if scanning else None,
        )

    def dialog(self):
        from smvwp.gui import load_dialog

        # 읽기는 작업 스레드로 도는데, 시험에서 스레드를 기다리면 장비가 바쁠 때
        # 들쭉날쭉해진다. 같은 함수를 그 자리에서 부른다.
        #
        # 패치를 **시험이 끝날 때까지** 걸어 둔다. 만드는 동안만 걸면 콤보를
        # 바꿔 다시 읽는 순간 진짜 스레드로 새고, 결과가 시험이 끝난 뒤에 와서
        # 아무 것도 안 바뀐 것처럼 보인다 (실제로 그렇게 헛짚었다).
        def run_here(reader, days, by):
            reader._run(days, by)
            return True

        patcher = patch.object(
            load_dialog._LoadReader, "run_async", autospec=True, side_effect=run_here
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        dialog = load_dialog.LoadDialog(self.data_dir, self.config)
        self.addCleanup(dialog.close)
        return dialog

    def column(self, table, index):
        return [
            table.item(row, index).text() if table.item(row, index) else ""
            for row in range(table.rowCount())
        ]

    # -- 아직 없을 때 ---------------------------------------------------
    def test_no_samples_says_so_rather_than_showing_an_empty_table(self):
        """빈 표는 '서버가 한가하다'로 읽힌다 - 사실은 아직 못 잰 것이다."""

        from smvwp import i18n

        dialog = self.dialog()
        self.assertEqual(dialog.summary.text(), i18n.t("load.no_samples"))
        self.assertEqual(dialog.hours_table.rowCount(), 0)

    # -- 시간대 ---------------------------------------------------------
    def test_hours_are_listed(self):
        for hours in (1, 2, 3):
            self.save(sample(hours_ago=hours))
        dialog = self.dialog()
        self.assertEqual(dialog.hours_table.rowCount(), 3)

    def test_an_hour_we_were_scanning_is_marked(self):
        """이 표시가 없으면 그 한가함 안에 우리가 들어 있다는 걸 모른다."""

        from smvwp import i18n

        self.save(sample(hours_ago=1, busy=8.0, scanning=True), scanning=True)
        dialog = self.dialog()
        self.assertIn(
            i18n.t("load.mixed_suffix").strip(),
            " ".join(self.column(dialog.hours_table, 0)),
        )

    def test_a_quiet_hour_carries_no_mark(self):
        from smvwp import i18n

        self.save(sample(hours_ago=1, busy=8.0))
        dialog = self.dialog()
        self.assertNotIn(
            i18n.t("load.mixed_suffix").strip(),
            " ".join(self.column(dialog.hours_table, 0)),
        )

    def test_the_summary_separates_our_time_from_theirs(self):
        self.save(sample(hours_ago=1, busy=10.0))
        self.save(sample(hours_ago=2, busy=60.0, scanning=True), scanning=True)
        dialog = self.dialog()
        text = dialog.summary.text()
        self.assertIn("%", text)
        self.assertNotIn("load.", text)   # 키가 새어 나오지 않는다

    # -- 작업 -----------------------------------------------------------
    def test_jobs_are_listed_with_their_owner(self):
        self.save(sample(processes=[process(1, "virtuoso", "alice", 180.0)]))
        dialog = self.dialog()
        self.assertIn("alice", self.column(dialog.jobs_table, 0))
        self.assertIn("virtuoso", " ".join(self.column(dialog.jobs_table, 1)))

    def test_our_own_processes_are_marked_not_hidden(self):
        """숨기면 '우리가 얼마나 쓰는지'를 못 본다. 다만 남의 것과 구분은 되어야 한다."""

        from smvwp import i18n

        self.save(sample(processes=[
            process(1, "virtuoso", "alice", 180.0),
            process(999, "du", "svc", 26.0, ours=True),
        ]))
        dialog = self.dialog()
        jobs = " ".join(self.column(dialog.jobs_table, 1))
        self.assertIn("du", jobs)
        self.assertIn(i18n.t("load.ours_suffix").strip(), jobs)

    def test_memory_sorting_brings_up_what_cpu_sorting_misses(self):
        """CPU 는 안 쓰면서 메모리만 물고 있는 작업은 CPU 정렬에 영영 안 잡힌다."""

        self.save(sample(processes=[
            process(1, "busy", "alice", 300.0, rss_kb=1000),
            process(2, "hog", "bob", 1.0, rss_kb=90_000_000),
        ]))
        dialog = self.dialog()
        self.assertEqual(self.column(dialog.jobs_table, 1)[0].strip(), "busy")

        dialog.sort_combo.setCurrentIndex(1)   # 메모리 기준
        self.assertEqual(self.column(dialog.jobs_table, 1)[0].strip(), "hog")

    def test_the_command_line_rides_in_a_tooltip(self):
        """열을 하나 더 만들면 표가 안 읽힌다. 필요할 때만 보이게 둔다."""

        self.save(sample(processes=[process(1, "virtuoso", "alice", 10.0)]))
        dialog = self.dialog()
        self.assertIn("virtuoso", dialog.jobs_table.item(0, 1).toolTip())

    # -- 마운트 ---------------------------------------------------------
    def test_mounts_are_listed(self):
        self.save(sample(mounts=[mount("/mnt/caef1201")]))
        dialog = self.dialog()
        self.assertIn("/mnt/caef1201", self.column(dialog.mounts_table, 0))

    def test_a_queue_bigger_than_the_round_trip_is_flagged(self):
        """그때 병렬을 줄이면 정확히 반대 처방이다 - 눈에 띄어야 한다."""

        from smvwp import i18n

        self.save(sample(mounts=[mount(rtt=0.1, queue=0.9)]))
        dialog = self.dialog()
        item = dialog.mounts_table.item(0, 3)
        self.assertEqual(item.toolTip(), i18n.t("load.queue_tip"))

    def test_a_healthy_mount_is_not_flagged(self):
        self.save(sample(mounts=[mount(rtt=0.9, queue=0.02)]))
        dialog = self.dialog()
        self.assertEqual(dialog.mounts_table.item(0, 3).toolTip(), "")

    # -- 기간 -----------------------------------------------------------
    def test_the_range_narrows_what_is_read(self):
        self.save(sample(hours_ago=24 * 20))     # 20일 전
        self.save(sample(hours_ago=1))
        dialog = self.dialog()                   # 기본 30일
        self.assertEqual(dialog.hours_table.rowCount(), 2)

        dialog.range_combo.setCurrentIndex(0)    # 7일
        self.assertEqual(dialog.hours_table.rowCount(), 1)

    # -- 언어 -----------------------------------------------------------
    def test_english_does_not_leak_keys(self):
        from smvwp import i18n

        i18n.set_language(i18n.ENGLISH)
        self.addCleanup(i18n.set_language, i18n.KOREAN)
        self.save(sample(processes=[process(1, "virtuoso", "alice", 10.0)]))
        dialog = self.dialog()
        text = " ".join([
            dialog.summary.text(), dialog.jobs_note.text(), dialog.mounts_note.text()
        ])
        self.assertNotIn("load.", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
