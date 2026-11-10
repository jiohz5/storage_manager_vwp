"""'눈에 띈다' 의 문턱은 한 곳에서 정하고, 화면들이 같은 답을 낸다.

## 왜 이것이 생겼나

"이 계정이 지난 스캔보다 많이 늘었다" 를 화면마다 다른 값으로 판정했다 - 홈의
'무엇부터 할까' 는 200GB, 상세 스캔 요약의 '살펴볼 것' 은 50GB, 경로 급증 알림은
100GB. 같은 계정이 150GB 늘면 요약에는 뜨고 홈에는 안 떴다.

그리고 전부 **절대값**이었다. 30TB 계정에서 50GB 는 0.2% 라 바퀴마다 뜨는
소음이고, 500GB 계정에서 200GB 는 40% 인데도 홈은 199GB 까지 조용했다.

이제 문턱은 **계정 크기의 일정 비율**(기본 2%)이고 바닥값(기본 100GB) 아래로는
내려가지 않는다 (`notable.growth_threshold_kb`).
"""

import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from smvwp import config as config_module
from smvwp import nightly_scan, notable, priority, scan_digest, scan_store
from tests import support

GB = 1024 * 1024
TB = 1024 * GB


@dataclass
class FakeEntry:
    """끝난 두 바퀴를 가진 계정 하나 - 우선순위와 스캔 요약이 같이 읽는 모양."""

    current_measured_kb: Optional[int] = None
    previous_measured_kb: Optional[int] = None
    account_id: str = "a1"
    account_name: str = "layout_proj"
    last_completed_generation: Optional[int] = 2
    current_scan_at: Optional[str] = "2026-09-10T00:00:00+00:00"
    large_files: List[tuple] = field(default_factory=list)
    failed_count: int = 0
    partial_paths: List[str] = field(default_factory=list)
    unavailable_reason: Optional[str] = None
    unavailable_detail: str = ""
    unavailable_at: Optional[str] = None


@dataclass
class FakeSnapshot:
    accounts: List[FakeEntry] = field(default_factory=list)
    latest_run: Optional[dict] = None
    is_running: bool = False


def grew(size_kb: int, delta_kb: int) -> FakeEntry:
    return FakeEntry(current_measured_kb=size_kb, previous_measured_kb=size_kb - delta_kb)


def on_home(entry) -> bool:
    plan = priority.build(scan_accounts=[entry])
    return any(action.kind == priority.ACT_SURGE for action in plan.actions)


def in_summary(entry) -> bool:
    digest = scan_digest.build(FakeSnapshot(accounts=[entry]))
    return any(finding.kind == scan_digest.FINDING_GROWTH for finding in digest.findings)


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        self.settings = config_module.Settings()

    def test_small_accounts_use_the_floor(self):
        self.assertEqual(notable.growth_threshold_kb(3 * TB, self.settings), 100 * GB)

    def test_big_accounts_use_their_share(self):
        """30TB 의 2% 는 600GB - 그 아래는 바퀴마다 있는 출렁임이다."""

        self.assertEqual(notable.growth_threshold_kb(30 * TB, self.settings),
                         int(30 * TB * 0.02))

    def test_an_unknown_size_falls_back_to_the_floor(self):
        self.assertEqual(notable.growth_threshold_kb(None, self.settings), 100 * GB)

    def test_both_knobs_are_settings(self):
        self.settings.growth_alert_min_kb = 10 * GB
        self.settings.growth_notice_pct = 10.0
        self.assertEqual(notable.growth_threshold_kb(50 * GB, self.settings), 10 * GB)
        self.assertEqual(notable.growth_threshold_kb(1 * TB, self.settings), int(TB * 0.10))

    def test_zero_share_means_the_floor_only(self):
        self.settings.growth_notice_pct = 0
        self.assertEqual(notable.growth_threshold_kb(30 * TB, self.settings), 100 * GB)


class ScreensAgreeTests(unittest.TestCase):
    """홈과 상세 스캔 요약은 같은 계정에 같은 답을 낸다."""

    CASES = [
        # (계정 크기, 증가) - 예전에는 150GB 가 요약에만 떴다 (50GB < 150GB < 200GB).
        (3 * TB, 150 * GB),
        (3 * TB, 60 * GB),
        (500 * GB, 120 * GB),
        (30 * TB, 300 * GB),
        (30 * TB, 700 * GB),
    ]

    def test_the_same_growth_gets_the_same_verdict(self):
        for size, delta in self.CASES:
            entry = grew(size, delta)
            self.assertEqual(on_home(entry), in_summary(entry),
                             f"{size / TB:.1f}TB +{delta / GB:.0f}GB")

    def test_150gb_on_a_3tb_account_is_notable_everywhere(self):
        entry = grew(3 * TB, 150 * GB)
        self.assertTrue(on_home(entry))
        self.assertTrue(in_summary(entry))

    def test_300gb_on_a_30tb_account_is_ordinary_everywhere(self):
        """1% - 예전에는 홈과 요약 양쪽에 '급증' 으로 떴다."""

        entry = grew(30 * TB, 300 * GB)
        self.assertFalse(on_home(entry))
        self.assertFalse(in_summary(entry))

    def test_settings_move_both_screens_together(self):
        settings = config_module.Settings()
        settings.growth_alert_min_kb = 10 * GB
        settings.growth_notice_pct = 0
        entry = grew(3 * TB, 20 * GB)
        plan = priority.build(scan_accounts=[entry], settings=settings)
        digest = scan_digest.build(FakeSnapshot(accounts=[entry]), settings=settings)
        self.assertTrue(any(a.kind == priority.ACT_SURGE for a in plan.actions))
        self.assertTrue(any(f.kind == scan_digest.FINDING_GROWTH for f in digest.findings))


class CleanupWorthTests(unittest.TestCase):
    """정리를 따로 권할 값어치도 설정 하나다 (`cleanup_min_size_kb`).

    홈은 50GB(박힌 값), 보고서의 정리 후보는 100GB(설정)였다."""

    def _plan(self, reclaim_kb, settings=None):
        from smvwp import health

        item = health.RunHealth(
            account_id="a1", account_name="layout_proj",
            run_path="/proj/과제A/LAYOUT/01_run_0908", label="과제A / 01_run_0908",
            run_size_kb=reclaim_kb * 2, backup_size_kb=reclaim_kb, status=health.BACKED_UP,
        )
        return priority.build(health_summary=health.summarize([[item]]), settings=settings)

    def test_below_the_setting_is_not_worth_a_line(self):
        self.assertEqual(self._plan(60 * GB).actions, [])

    def test_at_the_setting_it_is(self):
        kinds = [action.kind for action in self._plan(100 * GB).actions]
        self.assertEqual(kinds, [priority.ACT_CLEANUP])

    def test_the_setting_moves_it(self):
        settings = config_module.Settings()
        settings.cleanup_min_size_kb = 50 * GB
        kinds = [action.kind for action in self._plan(60 * GB, settings).actions]
        self.assertEqual(kinds, [priority.ACT_CLEANUP])


class PathAlertTests(unittest.TestCase):
    """경로 급증 알림도 같은 문턱이다 - 계정 크기의 2% 또는 100GB 중 큰 쪽.

    30TB 계정의 폴더 하나가 150GB 늘면(0.5%) 예전에는 알림이 갔다."""

    def _alerts(self, big_kb: int, grown_kb: int) -> int:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            data_dir = Path(tmp)
            config = config_module.load_config(data_dir)
            account = config_module.Account(name="layout_proj", path="/acct", account_id="a1")
            config.accounts = [account]
            tops = ["/acct/big", "/acct/hot"]
            with scan_store.session(data_dir) as conn:
                support.scan_pass(conn, "a1", 1, tops, {
                    tops[0]: support.whole({tops[0]: big_kb}),
                    tops[1]: support.whole({tops[1]: 200 * GB}),
                })
                support.scan_pass(conn, "a1", 2, tops, {
                    tops[0]: support.whole({tops[0]: big_kb}),
                    tops[1]: support.whole({tops[1]: 200 * GB + grown_kb}),
                })
            outcome = nightly_scan.AccountOutcome(
                account_id="a1", account_name="layout_proj",
                baseline_status="done", baseline_generation=2,
            )
            return nightly_scan._notify_growth(
                data_dir, config, [outcome], datetime(2026, 9, 10, 6, 0, tzinfo=timezone.utc)
            )

    def test_150gb_in_a_3tb_account_alerts(self):
        self.assertEqual(self._alerts(3 * TB, 150 * GB), 1)

    def test_150gb_in_a_30tb_account_does_not(self):
        self.assertEqual(self._alerts(30 * TB, 150 * GB), 0)

    def test_700gb_in_a_30tb_account_alerts(self):
        self.assertEqual(self._alerts(30 * TB, 700 * GB), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
