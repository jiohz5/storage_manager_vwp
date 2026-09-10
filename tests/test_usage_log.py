"""누가 이 도구를 어떻게 쓰는지.

## 왜 이것을 남기나

이 도구를 어떻게 안착시킬지가 아직 미지수다 - 몇 명이 쓸지, 무엇을 보러
들어오는지, 스캔을 직접 돌리는 사람이 있는지. 물어봐서 알 수 있는 것이
아니다. 사람은 자기가 무엇을 얼마나 쓰는지 잘 기억하지 못하고, 특히
"안 쓰는 것"은 화제에 오르지도 않는다.

## 여기서 지키는 것

- **기록이 사람이 하려던 일을 막지 않는다.** 어떤 예외도 밖으로 내지 않는다.
- 내용은 남기지 않는다. 검색을 했다는 사실은 남기되 무엇을 찾았는지는 아니다.
- 날짜는 지역시간으로 센다 - UTC 로 세면 아침 일찍 쓰는 사람의 '연속으로 쓴
  날'이 실제보다 적게 나온다.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import scan_store, usage_log


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.addCleanup(self.tmp.cleanup)

    def events(self):
        conn = scan_store.connect(self.data_dir)
        try:
            return scan_store.usage_events(conn)
        finally:
            conn.close()

    def test_an_action_is_stored_with_who_and_when(self):
        self.assertTrue(usage_log.record(self.data_dir, usage_log.GUI_OPENED))
        rows = self.events()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], usage_log.GUI_OPENED)
        self.assertTrue(rows[0]["user_name"])
        self.assertTrue(rows[0]["happened_at"])

    def test_a_detail_can_ride_along(self):
        usage_log.record(self.data_dir, usage_log.SCAN_STARTED, detail="layout_proj")
        self.assertEqual(self.events()[0]["detail"], "layout_proj")

    def test_a_broken_database_does_not_stop_the_person(self):
        """기록은 부가 정보다. 이것 때문에 하려던 일이 막히면 본말이 뒤집힌다."""

        with patch.object(scan_store, "connect", side_effect=OSError("잠김")):
            self.assertFalse(usage_log.record(self.data_dir, usage_log.GUI_OPENED))

    def test_a_write_failure_does_not_raise(self):
        """디스크가 가득 찼거나 권한이 없을 때.

        실제로 없는 경로를 넘겨 시험하지 않는다 - `connect` 는 디렉터리를
        만들어 버려서, 시험이 개발 PC 에 엉뚱한 폴더를 남긴다 (실제로 그랬다)."""

        class Exploding:
            def execute(self, *args):
                raise OSError("디스크 가득 참")

            def close(self):
                pass

        with patch.object(scan_store, "connect", return_value=Exploding()):
            self.assertFalse(usage_log.record(self.data_dir, usage_log.GUI_OPENED))

    def test_the_user_can_be_given(self):
        usage_log.record(self.data_dir, usage_log.GUI_OPENED, user="bob")
        self.assertEqual(self.events()[0]["user_name"], "bob")


class IdentityTests(unittest.TestCase):
    def test_there_is_always_a_name(self):
        """cron 처럼 환경이 빈 곳에서도 빈칸보다는 무언가가 낫다."""

        with patch.object(usage_log.getpass, "getuser", side_effect=OSError):
            self.assertTrue(usage_log.current_user())

    def test_there_is_always_a_host(self):
        with patch.object(usage_log.socket, "gethostname", side_effect=OSError):
            self.assertEqual(usage_log.current_host(), "unknown")


class SummaryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.conn = scan_store.connect(self.data_dir)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def add(self, stamp, user="alice", action=usage_log.GUI_OPENED):
        self.conn.execute(
            "INSERT INTO usage_events (happened_at, user_name, host_name, action) "
            "VALUES (?, ?, ?, ?)",
            (stamp, user, "host", action),
        )
        self.conn.commit()


class ByUserTests(SummaryBase):
    def test_days_are_counted_apart_from_events(self):
        """하루에 열 번 연 사람과 열흘 동안 매일 한 번 연 사람은 다른 이야기다."""

        for hour in (1, 2, 3):
            self.add(f"2026-09-0{hour}T02:00:00+00:00")
        row = scan_store.usage_by_user(self.conn)[0]
        self.assertEqual(row["events"], 3)
        self.assertEqual(row["days"], 3)

    def test_the_same_day_is_one_day(self):
        self.add("2026-09-01T02:00:00+00:00")
        self.add("2026-09-01T03:00:00+00:00")
        self.assertEqual(scan_store.usage_by_user(self.conn)[0]["days"], 1)

    def test_days_follow_the_local_calendar(self):
        """한국시간 같은 날 아침과 낮인데 UTC 로는 날짜가 갈린다.

        UTC 로 세면 하루를 이틀로 세어, 아침 일찍 쓰는 사람이 실제보다 더
        꾸준히 쓴 것처럼 보인다."""

        # 2026-09-10 03:00 KST 와 11:00 KST - 지역시간으로는 같은 날.
        self.add("2026-09-09T18:00:00+00:00")
        self.add("2026-09-10T02:00:00+00:00")
        with patch.object(scan_store, "_local_offset_modifier", return_value="+540 minutes"):
            row = scan_store.usage_by_user(self.conn)[0]
        self.assertEqual(row["events"], 2)
        self.assertEqual(row["days"], 1)

    def test_people_are_separated(self):
        self.add("2026-09-01T02:00:00+00:00", user="alice")
        self.add("2026-09-01T02:00:00+00:00", user="bob")
        rows = scan_store.usage_by_user(self.conn)
        self.assertEqual(sorted(row["user_name"] for row in rows), ["alice", "bob"])

    def test_a_window_narrows_it(self):
        self.add("2026-01-01T02:00:00+00:00")
        self.add("2026-09-01T02:00:00+00:00")
        rows = scan_store.usage_by_user(self.conn, since="2026-06-01T00:00:00+00:00")
        self.assertEqual(rows[0]["events"], 1)


class ByActionTests(SummaryBase):
    def test_how_many_people_use_each_thing(self):
        """한 사람이 백 번 쓴 기능과 열 사람이 열 번씩 쓴 기능은 뜻이 다르다."""

        self.add("2026-09-01T02:00:00+00:00", user="alice", action="report.open")
        self.add("2026-09-01T03:00:00+00:00", user="alice", action="report.open")
        self.add("2026-09-01T02:00:00+00:00", user="bob", action="search.open")
        rows = {row["action"]: row for row in scan_store.usage_by_action(self.conn)}
        self.assertEqual(rows["report.open"]["events"], 2)
        self.assertEqual(rows["report.open"]["users"], 1)
        self.assertEqual(rows["search.open"]["users"], 1)

    def test_busiest_action_first(self):
        for _ in range(3):
            self.add("2026-09-01T02:00:00+00:00", action="gui.open")
        self.add("2026-09-01T02:00:00+00:00", action="search.open")
        rows = scan_store.usage_by_action(self.conn)
        self.assertEqual(rows[0]["action"], "gui.open")

    def test_nothing_recorded_gives_nothing(self):
        self.assertEqual(scan_store.usage_by_action(self.conn), [])


class PruneTests(SummaryBase):
    def test_old_records_go(self):
        self.add("2020-01-01T02:00:00+00:00")
        self.add("2026-09-01T02:00:00+00:00")
        scan_store.prune_usage_events(self.conn, retention_days=365)
        rows = scan_store.usage_events(self.conn)
        self.assertEqual(len(rows), 1)


class ActionNameTests(unittest.TestCase):
    def test_the_names_are_constants_not_loose_strings(self):
        """문자열을 직접 쓰면 오타로 갈라진 항목이 생기고 집계가 무너진다."""

        names = [
            usage_log.GUI_OPENED, usage_log.SCAN_STARTED, usage_log.SCAN_STOPPED,
            usage_log.REPORT_VIEWED, usage_log.SEARCH_USED,
            usage_log.ACCOUNTS_OPENED, usage_log.TREE_VIEWED, usage_log.LOAD_VIEWED,
        ]
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            self.assertRegex(name, r"^[a-z]+\.[a-z]+$")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
