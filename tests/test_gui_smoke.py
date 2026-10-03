"""창을 **실제로** 띄워 본다 (화면 없이, offscreen).

## 왜 이제야 생겼나

개발 PC 에 PyQt5 가 없는 줄 알고 GUI 는 소스만 읽는 검사로 대신해 왔다. 잘못
읽은 것이었다 - 있었다. 그 사이 반입 장비에서 세 번 터졌다: 탭을 만들기 전에
쓰기(`AttributeError`), 콤보 칸이 세로로 잘리기, 탭 인덱스를 잘못 묻기(-1).
셋 다 창을 한 번만 만들어 봤으면 잡혔을 것들이다.

소스 검사(`test_gui_wiring.py`)는 그대로 둔다 - 여기서 못 보는 것(경계 침범,
번역 키)을 그쪽이 본다. 이 파일은 **실행해야만 드러나는 것**만 본다.

## 이 파일이 지키는 것

- 창이 만들어지고 탭이 제자리에 있다.
- 홈 배너 링크가 스캔 탭으로 간다 (인덱스 -1 사건).
- 콤보가 든 칸이 **폭도 높이도** 콤보보다 작지 않다 (세 번 되살아난 사건).
- 언어를 바꿔도 죽지 않는다.
- cron 경고가 경고 스타일 이름을 단다.

`QT_QPA_PLATFORM=offscreen` 은 QApplication 을 만들기 **전에** 정해야 한다.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtWidgets import QApplication, QComboBox
    HAVE_QT = True
except ImportError:  # pragma: no cover - PyQt5 없는 환경
    HAVE_QT = False

from smvwp import config as config_module

_APP = None


def _app():
    """QApplication 은 프로세스에 하나뿐이어야 한다.

    **테마를 반드시 입힌다.** 처음에는 안 입히고 재다가 크기 문제를 통째로
    놓쳤다 - 스타일시트가 없으면 콤보에 안쪽 여백이 붙지 않아 아무 데도 안
    끼이고, 시험은 통과하는데 실제 화면만 잘렸다. 실기와 같은 조건이 아니면
    재는 의미가 없다."""

    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
        from smvwp.gui import theme

        theme.apply(_APP)
    return _APP


# 창이 **스스로** 띄우는 배경 작업. 시험에서는 멈춰 둔다.
#
# 창을 만들면 첫 수집(진짜 `df`)·대시보드 읽기·스캔 상태 조회가 작업 스레드로
# 돈다. 시험은 보통 가짜 표본이나 스냅샷을 넣고 `processEvents()` 를 부른 뒤
# 확인하는데, 그 사이에 배경 결과가 도착하면 시험이 넣은 것을 **진짜(빈) 값으로
# 덮어쓴다.** 몇 ms 차이로 결과가 갈려 시험이 들쭉날쭉했다 - 클래스마다 하나씩
# 막다가 놓친 곳에서 계속 났다. 한 곳에서 다 막는다.
#
# 배경 작업 자체는 `test_thread_worker` 가 따로 본다.
_BACKGROUND = (
    "smvwp.scheduler.CollectorScheduler.start",
    "smvwp.scheduler.DashboardWorker.refresh_async",
    "smvwp.scheduler.ScanStatusWorker.refresh_async",
)


@unittest.skipUnless(HAVE_QT, "PyQt5 없음")
class _GuiCase(unittest.TestCase):
    def setUp(self):
        _app()
        for target in _BACKGROUND:
            patcher = patch(target, lambda *args, **kwargs: None)
            patcher.start()
            self.addCleanup(patcher.stop)
        # 창이 띄우는 작업 스레드(대시보드, 스캔 상태)는 daemon 이라 창을 닫아도
        # 곧바로 끝나지 않고, 그동안 SQLite 파일을 쥐고 있다. 윈도우는 열린
        # 파일을 못 지우므로 정리에서 죽는다 - 리눅스에서는 나지 않는 일이다.
        # 시험이 보는 것은 창의 동작이지 임시 디렉터리 삭제가 아니다.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.data_dir = root / "data"
        self.config = config_module.load_config(self.data_dir)
        # 성격이 다른 계정 둘 - 연결 백업 콤보에 실제 선택지가 생기게.
        for name, kind in (("proj", config_module.ACCOUNT_KIND_PROJECT),
                           ("bak", config_module.ACCOUNT_KIND_BACKUP)):
            path = root / name
            path.mkdir()
            account = config_module.add_account(
                self.config, name, str(path), data_dir=self.data_dir
            )
            config_module.set_account_kind(self.config, account.account_id, kind)
        config_module.save_config(self.data_dir, self.config)

    def tearDown(self):
        _app().processEvents()
        self.tmp.cleanup()


class MainWindowSmokeTests(_GuiCase):
    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)

    def tearDown(self):
        self.window.close()
        super().tearDown()

    def test_window_builds_with_both_tabs(self):
        self.assertEqual(self.window.tabs.count(), 2)

    def test_scan_page_is_findable(self):
        """`indexOf(self._scan_tab)` 가 -1 을 돌려주던 사건."""

        index = self.window.tabs.indexOf(self.window._scan_page)
        self.assertGreaterEqual(index, 0)

    def test_home_banner_link_switches_to_the_scan_tab(self):
        self.window.tabs.setCurrentIndex(0)
        self.window.home_scan_link.click()
        _app().processEvents()
        self.assertEqual(
            self.window.tabs.currentWidget(), self.window._scan_page,
            "홈 배너 링크가 스캔 탭으로 가지 않습니다",
        )

    def test_banner_signal_reaches_the_window(self):
        """탭이 값만 보내고 창이 그린다 - 그 신호 경계가 실제로 이어져 있는가."""

        from smvwp.gui.scan_tab import BannerState

        self.window._scan_tab.banner_changed.emit(
            BannerState(running=True, path="/x", done=3, total=10)
        )
        _app().processEvents()
        self.assertTrue(self.window.home_scan_banner.isVisibleTo(self.window))
        self.assertEqual(self.window.home_scan_progress.maximum(), 10)

        self.window._scan_tab.banner_changed.emit(BannerState(running=False))
        _app().processEvents()
        self.assertFalse(self.window.home_scan_banner.isVisibleTo(self.window))

    def test_status_message_signal_reaches_the_status_bar(self):
        self.window._scan_tab.status_message.emit("hello")
        _app().processEvents()
        self.assertEqual(self.window.status_bar_label.text(), "hello")

    def test_switching_language_does_not_crash(self):
        from smvwp import i18n

        try:
            i18n.set_language("en")
            self.window.retranslate()
            i18n.set_language("ko")
            self.window.retranslate()
        finally:
            i18n.set_language("ko")

    def test_saving_the_account_dialog_is_recorded(self):
        """계정 수가 언제 늘었나 - 이 도구가 자리를 잡는지 보여 주는 숫자다.

        이름(`ACCOUNT_CHANGED`)은 정의만 되어 있고 한 번도 기록되지 않았다."""

        from smvwp import usage_log

        recorded = []
        with patch("smvwp.gui.main_window.AccountDialog") as dialog, patch.object(
            usage_log, "record", side_effect=lambda *a, **k: recorded.append((a, k))
        ):
            dialog.return_value.exec_.return_value = True
            self.window._open_account_dialog()
        actions = [args[1] for args, _ in recorded]
        self.assertIn(usage_log.ACCOUNT_CHANGED, actions)
        detail = next(
            kwargs.get("detail") for args, kwargs in recorded
            if args[1] == usage_log.ACCOUNT_CHANGED
        )
        self.assertEqual(detail, "2->2")

    def test_selecting_an_account_reaches_the_scan_tab(self):
        account = self.config.accounts[0]
        self.window._scan_tab.select_account(account.account_id)
        _app().processEvents()
        combo = self.window._scan_tab.scan_account_combo
        self.assertEqual(combo.currentData(), account.account_id)

    def test_cron_warning_gets_the_warning_style_name(self):
        from smvwp import cron_status

        tab = self.window._scan_tab
        tab._cron_status = cron_status.CronStatus(available=True, collector=True, nightly=False)
        tab._render_cron_status()
        self.assertEqual(tab.cron_status_label.objectName(), "captionWarn")

        tab._cron_status = cron_status.CronStatus(available=True, collector=True, nightly=True)
        tab._render_cron_status()
        self.assertEqual(tab.cron_status_label.objectName(), "caption")

    def test_scan_tab_has_the_card_look(self):
        """QWidget 으로 두었더니 카드 테두리가 조용히 사라졌던 사건."""

        from PyQt5.QtWidgets import QFrame

        self.assertIsInstance(self.window._scan_tab, QFrame)
        self.assertEqual(self.window._scan_tab.objectName(), "card")


class AccountDialogComboTests(_GuiCase):
    """콤보가 든 칸이 잘리지 않는가 - 세 번 되살아난 문제를 픽셀로 못박는다."""

    def setUp(self):
        super().setUp()
        from smvwp.gui.account_dialog import AccountDialog

        self.dialog = AccountDialog(self.data_dir, self.config)
        self.dialog.show()
        _app().processEvents()

    def tearDown(self):
        self.dialog.close()
        super().tearDown()

    def _combos(self):
        from smvwp.gui.account_dialog import COMBO_COLUMNS

        table = self.dialog.account_table
        for column in COMBO_COLUMNS:
            for row in range(table.rowCount()):
                widget = table.cellWidget(row, column)
                if isinstance(widget, QComboBox):
                    yield row, column, widget

    def test_there_are_combos_to_measure(self):
        self.assertGreaterEqual(len(list(self._combos())), 2)

    def test_combos_actually_receive_the_size_they_need(self):
        """**칸이 아니라 위젯이 실제로 받은 크기**를 잰다.

        칸만 재면 놓친다. 행을 42px 로 키워 놓고도 콤보는 23px 만 받고 있었다 -
        `QTableWidget::item` 의 세로 패딩이 글자뿐 아니라 칸 위젯의 기하까지
        밀어 넣기 때문이다. 칸 높이만 보는 시험은 그때도 통과했다."""

        for row, column, combo in self._combos():
            with self.subTest(row=row, column=column):
                self.assertGreaterEqual(
                    combo.height(), combo.sizeHint().height(),
                    f"콤보가 필요한 높이({combo.sizeHint().height()})보다 "
                    f"작게({combo.height()}) 놓였습니다",
                )
                self.assertGreaterEqual(
                    combo.width(), combo.sizeHint().width(),
                    f"콤보가 필요한 폭보다 좁게 놓였습니다",
                )

    def test_the_text_has_room_left_after_padding(self):
        """마지막으로 남는 것이 글자 자리다 - 여기가 음수면 글자가 잘린다."""

        for row, column, combo in self._combos():
            # 테마의 콤보 여백: 위아래 8px, 테두리 1px.
            room = combo.height() - (8 + 8) - (1 + 1)
            with self.subTest(row=row, column=column):
                self.assertGreaterEqual(
                    room, combo.fontMetrics().height(),
                    f"글자 자리가 {room}px 뿐입니다 "
                    f"(글자 높이 {combo.fontMetrics().height()}px)",
                )

    def test_every_option_fits_in_the_column(self):
        """가장 긴 선택지 기준이어야 한다 - 짧은 값이 선택된 행에 맞추면 긴 값이 잘린다."""

        table = self.dialog.account_table
        for row, column, combo in self._combos():
            metrics = combo.fontMetrics()
            longest = max(
                (metrics.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())),
                default=0,
            )
            with self.subTest(row=row, column=column):
                self.assertGreater(table.columnWidth(column), longest)


class CronStatusRefreshTests(_GuiCase):
    """cron 안내가 사실과 어긋난 채 남지 않는가.

    실제로 걸린 일이다. 안내를 보고 터미널에서 `setup_cron.csh` 를 돌리고 창으로
    돌아왔는데 빨간 글씨가 그대로였다 - 창이 cron 을 **열 때 한 번만** 읽고
    캐시했기 때문이다. 사람은 등록이 안 된 줄 알고 또 돌리게 된다.

    안내가 틀린 채 남는 것은 안내가 없는 것보다 나쁘다.
    """

    def setUp(self):
        super().setUp()
        from smvwp.gui.scan_tab import ScanTab

        self.tab = ScanTab(self.data_dir, lambda: self.config)
        self._settle()

    def tearDown(self):
        self.tab.close()
        super().tearDown()

    def _settle(self, timeout=2.0):
        """백그라운드 확인이 끝날 때까지 기다린다."""

        import time

        deadline = time.time() + timeout
        while self.tab._cron_checking and time.time() < deadline:
            _app().processEvents()
            time.sleep(0.01)
        _app().processEvents()

    def _status(self, nightly):
        from smvwp import cron_status

        return cron_status.CronStatus(available=True, collector=True, nightly=nightly)

    def test_the_warning_clears_once_cron_is_registered(self):
        """등록 전 -> 등록 후. 이것이 사용자가 겪은 그 흐름이다."""

        from smvwp import cron_status

        with patch.object(cron_status, "read_status", return_value=self._status(False)):
            self.tab._check_cron_async()
            self._settle()
        self.tab._render_cron_status()
        self.assertEqual(self.tab.cron_status_label.objectName(), "captionWarn")

        with patch.object(cron_status, "read_status", return_value=self._status(True)):
            self.tab._check_cron_async()
            self._settle()
        self.tab._render_cron_status()
        self.assertEqual(self.tab.cron_status_label.objectName(), "caption")

    def test_pressing_scan_rechecks_cron(self):
        """방금 등록하고 누르는 경우가 가장 흔하다 - 그 순간 다시 봐야 한다."""

        from smvwp import cron_status

        calls = []

        def counted():
            calls.append(1)
            return self._status(True)

        with patch.object(cron_status, "read_status", side_effect=counted):
            # 계정이 없으면 안내만 띄우고 끝나지만, cron 확인은 그 전에 돈다.
            with patch.object(self.tab, "_get_config", return_value=_EmptyConfig()):
                with patch("smvwp.gui.scan_tab.QMessageBox.information"):
                    self.tab._trigger_scan_now()
            self._settle()
        self.assertTrue(calls, "스캔 버튼이 cron 을 다시 확인하지 않습니다")

    def test_checks_do_not_pile_up(self):
        """`crontab` 이 느린 장비에서 요청이 쌓이면 그때부터 더 나빠진다."""

        self.tab._cron_checking = True
        from smvwp import cron_status

        with patch.object(cron_status, "read_status") as read:
            self.tab._check_cron_async()
            self.assertEqual(read.call_count, 0)
        self.tab._cron_checking = False

    def test_a_timer_keeps_it_fresh(self):
        """cron 은 이 창 밖에서 바뀐다 - 스스로 알아채야 한다."""

        self.assertTrue(self.tab._cron_timer.isActive())
        self.assertLessEqual(
            self.tab._cron_timer.interval(), 10 * 60_000,
            "너무 뜸하면 안내가 한참 틀린 채 남는다",
        )


class _EmptyConfig:
    accounts = []


class LargeFilesTableTests(_GuiCase):
    """'파일 하나가 유난히 크다' 를 화면이 실제로 보여 주는가."""

    def setUp(self):
        super().setUp()
        from smvwp.gui.scan_tab import ScanTab

        self.tab = ScanTab(self.data_dir, lambda: self.config)
        # 계정 콤보는 `retranslate` 가 채운다 - 평소에는 창이 불러 준다.
        # 안 부르면 콤보가 비어 고른 계정이 없고, 표는 그릴 대상을 못 찾는다.
        self.tab.retranslate()
        _app().processEvents()

    def tearDown(self):
        self.tab.close()
        super().tearDown()

    def _snapshot_with(self, rows, measured_kb):
        """큰 파일이 들어 있는 스냅샷을 흉내 낸다."""

        account = self.config.accounts[0]

        class FakeEntry:
            account_id = account.account_id
            account_name = account.name
            large_files = rows
            measured_kb = None

        class FakeSnapshot:
            accounts = [FakeEntry()]

        FakeEntry.measured_kb = measured_kb
        self.tab._scan_snapshot = FakeSnapshot()
        index = self.tab.scan_account_combo.findData(account.account_id)
        if index >= 0:
            self.tab.scan_account_combo.setCurrentIndex(index)
        self.tab._refresh_large_files()
        _app().processEvents()

    def test_rows_appear_biggest_first(self):
        self._snapshot_with(
            [("/a/huge", 900_000, 100_000), ("/a/mid", 400_000, 400_000)],
            measured_kb=2_000_000,
        )
        table = self.tab.large_page.table
        self.assertEqual(table.rowCount(), 2)
        self.assertIn("huge", table.item(0, 0).text())

    def test_a_dominant_file_is_marked(self):
        """계정의 절반을 차지하는 파일이 눈에 안 띄면 표의 뜻이 없다."""

        from smvwp import tiers
        from PyQt5.QtGui import QColor
        from smvwp.gui.scan_pages import LARGE_PATH

        self._snapshot_with([("/a/huge", 500_000, 500_000)], measured_kb=1_000_000)
        item = self.tab.large_page.table.item(0, LARGE_PATH)
        self.assertEqual(
            item.foreground().color().name(), QColor(tiers.color(tiers.WARN)).name()
        )
        self.assertTrue(item.toolTip(), "왜 눈에 띄는지 설명이 있어야 한다")

    def test_an_ordinary_file_is_not_marked(self):
        """전부 칠하면 아무것도 강조되지 않는다."""

        from smvwp.gui.scan_pages import LARGE_PATH

        from smvwp import tiers
        from PyQt5.QtGui import QColor

        self._snapshot_with([("/a/ok", 1_000, 1_000)], measured_kb=10_000_000)
        item = self.tab.large_page.table.item(0, LARGE_PATH)
        self.assertNotEqual(
            item.foreground().color().name(), QColor(tiers.color(tiers.WARN)).name()
        )

    def test_a_file_missing_from_the_previous_list_says_so(self):
        from smvwp import i18n
        from smvwp.gui.scan_pages import LARGE_CHANGE

        i18n.set_language("ko")
        self._snapshot_with([("/a/new", 500_000, None)], measured_kb=10_000_000)
        self.assertEqual(
            self.tab.large_page.table.item(0, LARGE_CHANGE).text(), i18n.t("large.new")
        )

    def test_no_files_gives_a_plain_message_not_an_empty_table(self):
        self._snapshot_with([], measured_kb=1_000_000)
        self.assertEqual(self.tab.large_page.table.rowCount(), 0)
        self.assertTrue(self.tab.large_page.caption.text())

    def test_unknown_account_total_does_not_show_a_fake_share(self):
        """총량을 모를 때 0% 로 쓰면 '작다'고 잘못 읽힌다."""

        from smvwp import i18n
        from smvwp.gui.scan_pages import LARGE_SHARE

        i18n.set_language("ko")
        self._snapshot_with([("/a/x", 500_000, None)], measured_kb=None)
        self.assertEqual(
            self.tab.large_page.table.item(0, LARGE_SHARE).text(), i18n.t("common.none")
        )


class ScanDigestSmokeTests(_GuiCase):
    """상세 스캔 탭 위의 요약 층.

    표 셋을 읽고 스스로 요약을 만들라고 하면 대부분 안 읽는다. 카드 넷과
    한 목록이 그 요약이고, **여기서 보는 것은 그것이 실제로 채워지는가**다."""

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)
        self.tab = self.window._scan_tab

    def tearDown(self):
        self.window.close()
        super().tearDown()

    def snapshot(self, *accounts, run=None, running=False):
        from dataclasses import dataclass, field
        from typing import List, Optional

        @dataclass
        class Snap:
            accounts: list
            latest_run: Optional[dict] = None
            is_running: bool = False
            window_description: str = ""

        return Snap(list(accounts), run, running)

    def account(self, name="proj", measured=None, previous=None, **kwargs):
        from dataclasses import dataclass, field
        from typing import List, Optional

        @dataclass
        class Acct:
            account_id: str = "a1"
            account_name: str = "proj"
            measured_kb: Optional[int] = None
            previous_measured_kb: Optional[int] = None
            current_scan_at: Optional[str] = "2026-09-10T00:00:00+00:00"
            large_files: List[tuple] = field(default_factory=list)
            failed_count: int = 0
            partial_paths: List[str] = field(default_factory=list)

        return Acct(
            account_name=name, measured_kb=measured, previous_measured_kb=previous,
            **kwargs
        )

    def findings_text(self):
        return [
            self.tab.summary_page.findings_list.item(row).text()
            for row in range(self.tab.summary_page.findings_list.count())
        ]

    def test_the_four_cards_exist(self):
        for card in (
            self.tab.summary_page.card_run, self.tab.summary_page.card_delta,
            self.tab.summary_page.card_biggest, self.tab.summary_page.card_findings,
        ):
            self.assertTrue(card.isVisibleTo(self.tab))

    def test_growth_lands_in_the_card(self):
        gb = 1024 * 1024
        self.tab._refresh_digest(
            self.snapshot(self.account(measured=900 * gb, previous=700 * gb))
        )
        self.assertIn("GB", self.tab.summary_page.card_delta.value.text())
        self.assertIn("+", self.tab.summary_page.card_delta.value.text())

    def test_without_a_previous_scan_the_card_says_dash_not_zero(self):
        """0 을 세우면 '안 늘었다'가 되는데 사실은 '견줄 것이 없다'이다."""

        gb = 1024 * 1024
        self.tab._refresh_digest(self.snapshot(self.account(measured=900 * gb)))
        self.assertEqual(self.tab.summary_page.card_delta.value.text(), "-")

    def test_the_biggest_account_is_named(self):
        gb = 1024 * 1024
        self.tab._refresh_digest(self.snapshot(
            self.account(name="small", measured=110 * gb, previous=100 * gb),
            self.account(name="huge", measured=900 * gb, previous=200 * gb),
        ))
        self.assertEqual(self.tab.summary_page.card_biggest.value.text(), "huge")

    def test_a_failure_shows_up_in_the_list(self):
        self.tab._refresh_digest(
            self.snapshot(self.account(name="broken", failed_count=2))
        )
        self.assertTrue(any("broken" in line for line in self.findings_text()))

    def test_nothing_to_report_still_says_something(self):
        """빈 목록은 고장으로 읽힌다 - 없다는 것도 말해 줘야 한다."""

        self.tab._refresh_digest(self.snapshot())
        self.assertEqual(len(self.findings_text()), 1)
        self.assertTrue(self.findings_text()[0])

    def test_no_translation_keys_leak(self):
        gb = 1024 * 1024
        self.tab._refresh_digest(self.snapshot(
            self.account(measured=900 * gb, previous=100 * gb, failed_count=1),
            run={"status": "completed",
                 "started_at": "2026-09-09T13:00:00+00:00",
                 "ended_at": "2026-09-09T20:10:00+00:00"},
        ))
        text = " ".join([
            self.tab.summary_page.card_run.value.text(), self.tab.summary_page.card_run.detail.text(),
            self.tab.summary_page.card_delta.detail.text(), self.tab.summary_page.card_biggest.detail.text(),
            self.tab.summary_page.card_findings.detail.text(), self.tab.summary_page.findings_caption.text(),
        ] + self.findings_text())
        self.assertNotIn("digest.", text)

    def test_an_unknown_run_status_is_shown_as_is(self):
        """번역이 없는 상태값이 `digest.status.xyz` 로 뜨면 읽는 사람이 당황한다."""

        from smvwp.gui.scan_pages import status_text

        self.assertEqual(status_text("weird_state"), "weird_state")


class PriorityPanelTests(_GuiCase):
    """홈 맨 위의 "무엇부터 할까".

    사용률은 표에, 백업 상태는 헬스체크에, 튀는 파일은 상세 스캔 탭에 있었다.
    각각은 맞는 말인데 **"그래서 오늘 뭘 먼저 하지"** 에는 아무도 답하지
    않았다. 여기서 보는 것은 그 한 줄이 실제로 채워지는가다."""

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)

    def tearDown(self):
        self.window.close()
        super().tearDown()

    def lines(self):
        return [
            self.window.priority_list.item(row).text()
            for row in range(self.window.priority_list.count())
        ]

    def plan(self, *actions, unscanned=()):
        from smvwp import priority

        made = priority.Plan()
        made.actions = list(actions)
        made.accounts_without_scan = list(unscanned)
        return made

    def test_a_full_account_is_listed_with_its_remedy(self):
        """문제와 해법을 따로 두면 사람이 두 화면을 오가야 하고, 그러면 안 한다."""

        from smvwp import priority

        self.window._refresh_priority(self.plan(priority.Action(
            kind=priority.ACT_FULL, level=priority.LEVEL_CRITICAL,
            account="layout_proj", account_id="a1", pct=96.0,
            reclaimable_kb=800 * 1024 * 1024, count=6,
        )))
        line = self.lines()[0]
        self.assertIn("layout_proj", line)
        self.assertIn("96", line)
        # 비울 양이 **같은 줄에** 있다 - 이것이 이 카드의 핵심이다.
        self.assertIn("800.0 GB", line)

    def test_a_missing_backup_says_what_is_at_stake(self):
        from smvwp import priority

        self.window._refresh_priority(self.plan(priority.Action(
            kind=priority.ACT_NO_BACKUP, level=priority.LEVEL_HIGH,
            account="layout_proj", account_id="a1", count=3,
            size_kb=500 * 1024 * 1024,
        )))
        self.assertIn("복구", self.lines()[0])

    def test_nothing_to_do_still_says_something(self):
        """빈 목록은 고장으로 읽힌다."""

        from smvwp import i18n

        self.window._refresh_priority(self.plan())
        self.assertEqual(self.window.priority_summary.text(), i18n.t("priority.nothing"))

    def test_accounts_left_out_are_named(self):
        """짧은 목록이 '볼 것이 없다' 로 읽히면 안 된다."""

        self.window._refresh_priority(self.plan(unscanned=["fresh_one"]))
        self.assertTrue(any("fresh_one" in line for line in self.lines()))

    def test_a_failed_calculation_does_not_blank_the_screen(self):
        from smvwp import i18n

        self.window._refresh_priority(None)
        self.assertEqual(
            self.window.priority_summary.text(), i18n.t("priority.unavailable")
        )

    def test_no_translation_keys_leak(self):
        from smvwp import priority

        self.window._refresh_priority(self.plan(
            priority.Action(kind=priority.ACT_CLEANUP, level=priority.LEVEL_MEDIUM,
                            account="a", reclaimable_kb=100 * 1024 * 1024, count=2),
            priority.Action(kind=priority.ACT_BIG_FILE, level=priority.LEVEL_MEDIUM,
                            account="b", path="/x/huge.dat",
                            size_kb=300 * 1024 * 1024, pct=30.0),
            priority.Action(kind=priority.ACT_SURGE, level=priority.LEVEL_MEDIUM,
                            account="c", delta_kb=500 * 1024 * 1024,
                            size_kb=900 * 1024 * 1024),
            unscanned=["z"],
        ))
        text = " ".join(self.lines() + [self.window.priority_summary.text()])
        self.assertNotIn("priority.", text)


class ScanTabHeightTests(_GuiCase):
    """1080 세로에서 눌리지 않는가.

    반입 장비의 화면이 1080 이 최대다. 작업 표시줄과 창 테두리를 빼면 쓸 수
    있는 세로가 1000px 남짓인데, 세부 표 넷을 쌓았더니 창 최소 높이가 791px 이
    되어 남는 여유를 넷이 나눠 갖느라 표마다 서너 줄만 보였다.

    세부를 하위 탭으로 나눈 뒤의 예산을 못박는다. 칸마다 자리가 충분한지는
    `ScanTabLayoutTests` 가 실제 크기로 띄워 잰다."""

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)

    def tearDown(self):
        self.window.close()
        super().tearDown()

    def test_the_window_fits_a_1080_screen_with_room_to_spare(self):
        needed = self.window.minimumSizeHint().height()
        # 1080 - 작업 표시줄 - 창 테두리 ~= 1000. 세부 표가 실제로 보이려면
        # 최소치가 그보다 한참 낮아야 한다.
        self.assertLess(needed, 760, f"창 최소 세로 {needed}px")

    def test_the_detail_areas_are_tabs_not_a_stack(self):
        """요약·계정별·증가 경로·큰 파일 - 한 범주가 한 탭."""

        tabs = self.window._scan_tab.detail_tabs
        self.assertEqual(tabs.count(), 4)

    def test_each_detail_tab_has_a_name(self):
        tabs = self.window._scan_tab.detail_tabs
        for index in range(tabs.count()):
            text = tabs.tabText(index)
            self.assertTrue(text)
            self.assertNotIn("scan.tab.", text)

    def test_the_findings_list_is_no_longer_capped(self):
        """탭 하나를 통째로 쓰므로 남는 만큼 보여 준다."""

        listing = self.window._scan_tab.summary_page.findings_list
        self.assertGreater(listing.maximumHeight(), 1000)

    def test_the_summary_tab_opens_first(self):
        """카드는 늘 보이던 자리에서 첫 탭으로 내려왔다 - 위에 늘 떠 있으면
        세부 탭마다 300px 씩 빼앗았다. 대신 창을 열면 요약이 먼저 보인다."""

        from smvwp.gui.scan_pages import TAB_SUMMARY

        tab = self.window._scan_tab
        self.assertEqual(tab.detail_tabs.currentIndex(), TAB_SUMMARY)
        for card in (tab.summary_page.card_run, tab.summary_page.card_delta, tab.summary_page.card_biggest,
                     tab.summary_page.card_findings):
            self.assertTrue(card.isVisibleTo(tab))


class ScanTabLayoutTests(_GuiCase):
    """상세 스캔 탭이 범주마다 자리를 **충분히** 주는가 - 실제 크기로 띄워 잰다.

    탭으로 나누기만 해서는 모자랐다. 위에 카드와 상태가 300px 을 먹고, 증가
    경로와 큰 파일은 반폭씩 나눠 경로 뒤쪽(정작 다른 부분)이 잘렸다. "여러
    칸이 한눈에 보이되 칸마다 몇 줄 안 보이고, 전부 가로·세로 스크롤" 이라는
    말을 들었다. 소스를 읽어서는 알 수 없는 것들이라 재는 수밖에 없다.
    """

    GB = 1024 * 1024

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)
        self.tab = self.window._scan_tab
        self.tab.retranslate()
        self.window.tabs.setCurrentIndex(self.window.tabs.indexOf(self.window._scan_page))
        self.tab._on_scan_status_ready(self._snapshot())
        self.show(1200, 820)

    def tearDown(self):
        self.window.close()
        super().tearDown()

    # -- 준비 ------------------------------------------------------------
    def _entry(self, account, big_file_kb):
        from smvwp.nightly_scan import AccountScanSnapshot

        root = account.path
        growth = [
            {"path": f"{root}/LAYOUT/run_{i:02d}_postlayout_extract/BACKUP/lvs_{i}",
             "current_kb": (400 - i) * self.GB, "previous_kb": (300 - i) * self.GB}
            for i in range(30)
        ]
        large = [(f"{root}/LAYOUT/run_00/SIM/psf/tran_0.tr0", big_file_kb, None)]
        return AccountScanSnapshot(
            account_id=account.account_id, account_name=account.name,
            last_completed_generation=2, top_paths=[], growth=growth,
            pending_baseline_count=120, baseline_done=900, baseline_total=1000,
            current_scan_at="2026-09-15T15:10:00+00:00",
            previous_scan_at="2026-09-14T15:05:00+00:00",
            large_files=large, measured_kb=1000 * self.GB,
            previous_measured_kb=900 * self.GB, eta_seconds=5400,
        )

    def _snapshot(self):
        from smvwp.nightly_scan import StatusSnapshot

        proj, bak = self.config.accounts
        return StatusSnapshot(
            is_running=True,
            window_description="22:00~06:00",
            latest_run={
                "status": "running", "started_at": "2026-09-15T13:00:00+00:00",
                "current_path": proj.path + "/LAYOUT", "current_account_id": proj.account_id,
            },
            # bak 쪽 파일만 계정의 60% 라 눈에 띈다 - 살펴볼 것에 오른다.
            accounts=[self._entry(proj, 1 * self.GB), self._entry(bak, 600 * self.GB)],
        )

    def show(self, width, height):
        self.window.resize(width, height)
        self.window.show()
        for _ in range(3):
            _app().processEvents()

    def page(self, index):
        self.tab.detail_tabs.setCurrentIndex(index)
        for _ in range(3):
            _app().processEvents()

    # -- 자리 ------------------------------------------------------------
    def test_growth_and_large_files_each_get_the_full_width(self):
        """반폭씩 나누면 경로 뒤쪽이 잘린다."""

        from smvwp.gui.scan_pages import TAB_GROWTH, TAB_LARGE

        full = self.tab.detail_stack.width()
        for index, table in ((TAB_GROWTH, self.tab.growth_page.table),
                             (TAB_LARGE, self.tab.large_page.table)):
            self.page(index)
            self.assertGreater(table.width(), full * 0.9, table)

    def test_the_fixed_header_leaves_most_of_the_height_to_the_tabs(self):
        """늘 떠 있는 부분이 크면 어느 탭을 열어도 몇 줄만 보인다.

        예전에는 카드·두 줄 상태·진행 막대가 820px 창에서 절반을 먹었다."""

        ratio = self.tab.detail_stack.height() / self.tab.height()
        self.assertGreater(ratio, 0.65, f"탭 자리 {ratio:.0%}")

    def test_no_sideways_scrolling_even_at_the_minimum_window(self):
        from PyQt5.QtWidgets import QAbstractScrollArea

        minimum = self.window.minimumSize()
        self.show(minimum.width(), max(minimum.height(), 700))
        offenders = []
        for index in range(self.tab.detail_tabs.count()):
            self.page(index)
            for view in self.tab.detail_stack.currentWidget().findChildren(
                QAbstractScrollArea
            ):
                if view.isVisible() and view.horizontalScrollBar().isVisible():
                    offenders.append(f"{index}:{view.__class__.__name__}")
        self.assertEqual(offenders, [])

    def test_the_account_picker_is_not_clipped(self):
        """콤보가 세로로 잘리는 사고가 세 번 있었다. 탭 줄에 올리면서 다시 잰다."""

        combo = self.tab.scan_account_combo
        self.assertGreaterEqual(combo.height(), combo.sizeHint().height())

    def test_a_long_finding_wraps_instead_of_scrolling_sideways(self):
        from smvwp.gui.scan_pages import TAB_SUMMARY

        self.show(self.window.minimumSize().width(), 760)
        self.page(TAB_SUMMARY)
        listing = self.tab.summary_page.findings_list
        listing.addItem("아주 긴 문장 " * 40)
        for _ in range(3):
            _app().processEvents()
        short = listing.visualItemRect(listing.item(0)).height()
        long_ = listing.visualItemRect(listing.item(listing.count() - 1)).height()
        self.assertGreater(long_, short * 1.5)
        self.assertFalse(listing.horizontalScrollBar().isVisible())

    # -- 경로 ------------------------------------------------------------
    def test_paths_are_shown_under_the_account_with_the_full_path_on_hover(self):
        from smvwp.gui.scan_pages import TAB_GROWTH

        self.page(TAB_GROWTH)
        account = self.tab._selected_account()
        cell = self.tab.growth_page.table.item(0, 0)
        self.assertTrue(cell.text().startswith("LAYOUT/"), cell.text())
        self.assertTrue(cell.toolTip().startswith(account.path), cell.toolTip())
        # 무엇을 기준으로 줄였는지는 설명 줄에 한 번 적는다.
        self.assertIn(account.path, self.tab.growth_page.caption.text())

    # -- 오가기 ------------------------------------------------------------
    def test_clicking_a_finding_opens_that_accounts_tab(self):
        """요약에서 근거로 곧장 가는 길이 없으면 계정 이름을 외워 다시 찾아야 한다."""

        from smvwp.gui.scan_pages import FINDING_ACCOUNT_ROLE, FINDING_TAB_ROLE, TAB_LARGE

        bak = self.config.accounts[1]
        listing = self.tab.summary_page.findings_list
        target = next(
            listing.item(row) for row in range(listing.count())
            if listing.item(row).data(FINDING_ACCOUNT_ROLE) == bak.account_id
            and listing.item(row).data(FINDING_TAB_ROLE) == TAB_LARGE
        )
        listing.itemClicked.emit(target)
        self.assertEqual(self.tab.detail_tabs.currentIndex(), TAB_LARGE)
        self.assertEqual(self.tab.scan_account_combo.currentData(), bak.account_id)
        self.assertIn(bak.name, self.tab.large_page.caption.text())

    def test_double_clicking_an_account_row_opens_its_growth_paths(self):
        from smvwp.gui.scan_pages import TAB_ACCOUNTS, TAB_GROWTH

        bak = self.config.accounts[1]
        self.page(TAB_ACCOUNTS)
        table = self.tab.accounts_page.table
        table.selectRow(1)
        table.cellDoubleClicked.emit(1, 0)
        self.assertEqual(self.tab.detail_tabs.currentIndex(), TAB_GROWTH)
        self.assertEqual(self.tab.scan_account_combo.currentData(), bak.account_id)

    def test_the_headline_says_running_with_the_percentage(self):
        self.assertIn("90%", self.tab.scan_headline_label.text())
        self.assertNotIn("scan.", self.tab.scan_headline_label.text())
        self.assertNotIn("scan.", self.tab.scan_status_label.text())


class HomeLayoutTests(_GuiCase):
    """홈은 두 칸이다 - 왼쪽에 요약, 오른쪽에 계정 목록.

    예전에는 히어로·우선순위·버튼·표를 세로로 쌓아 위 셋이 세로의 절반을
    먹었고, 열이 열둘인 표는 1,200 창에서 그것만으로 가로 스크롤이 났다.
    소스를 읽어서는 알 수 없는 것들이라 실제 크기로 띄워 잰다.
    """

    GB = 1024 * 1024

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)
        self._fill_samples()
        self.window.resize(1200, 820)
        self.window.show()
        for _ in range(4):
            _app().processEvents()

    def tearDown(self):
        self.window.close()
        super().tearDown()

    def _fill_samples(self):
        """표에 실제로 행이 그려진 상태로 만든다 (수집을 기다리지 않는다)."""

        from datetime import datetime, timezone

        from smvwp import store

        samples = {}
        for index, account in enumerate(self.config.accounts):
            total = (20 + index * 5) * 1024 * self.GB
            pct = 75.0 + index * 8
            samples[account.account_id] = store.SampleRecord(
                account_id=account.account_id,
                collected_at=datetime.now(timezone.utc).isoformat(),
                ok=True, filesystem="nfs4", mount_point="/ifs",
                total_kb=total, used_kb=int(total * pct / 100), avail_kb=1,
                byte_pct=pct, byte_tier="warn", overall_tier="warn",
            )
        self.window._render_table(samples)
        for _ in range(3):
            _app().processEvents()

    # -- 배치 ------------------------------------------------------------
    def test_the_account_list_sits_beside_the_summary_not_below_it(self):
        table = self.window.table
        hero = self.window._hero_card
        self.assertGreaterEqual(
            table.mapTo(self.window, table.rect().topLeft()).x(),
            hero.mapTo(self.window, hero.rect().topRight()).x(),
            "계정 목록이 요약 오른쪽에 있어야 한다",
        )

    def test_the_summary_cards_are_stacked_in_the_left_column(self):
        hero = self.window._hero_card
        priority = self.window.priority_card
        self.assertGreater(
            priority.mapTo(self.window, priority.rect().topLeft()).y(),
            hero.mapTo(self.window, hero.rect().topLeft()).y(),
            "우선순위 카드는 히어로 아래에 있어야 한다",
        )
        self.assertEqual(hero.width(), priority.width())

    def test_the_list_gets_most_of_the_height(self):
        """표가 세로를 거의 다 써야 한 화면에 계정이 다 들어온다."""

        page = self.window.tabs.widget(0)
        ratio = self.window.table.height() / page.height()
        self.assertGreater(ratio, 0.7, f"표 자리 {ratio:.0%}")

    def test_the_refresh_button_is_with_the_list(self):
        """목록을 보다가 누르는 버튼이라, 목록에서 멀면 찾지 못한다."""

        button = self.window.collect_btn
        table = self.window.table
        self.assertGreaterEqual(
            button.mapTo(self.window, button.rect().center()).x(),
            table.mapTo(self.window, table.rect().topLeft()).x(),
        )

    # -- 가로 스크롤 ------------------------------------------------------
    def test_the_list_does_not_scroll_sideways_at_the_default_size(self):
        self.assertFalse(self.window.table.horizontalScrollBar().isVisible())

    def test_nothing_in_the_left_column_scrolls_sideways(self):
        self.assertFalse(self.window.priority_list.horizontalScrollBar().isVisible())

    def test_the_forecast_column_is_not_squeezed_to_nothing(self):
        """예측 칸이 `예측 ...` 으로 줄면 그 열은 자리만 차지한다."""

        from smvwp.gui.main_window import COL_FORECAST

        width = self.window.table.horizontalHeader().sectionSize(COL_FORECAST)
        self.assertGreater(width, 120, f"FULL 예상 열 {width}px")

    def test_the_name_is_never_the_column_that_gets_squeezed(self):
        """이름을 Stretch 로 뒀더니 오히려 반대가 됐다 - Stretch 는 남은 것을
        받는 열이라, 옆 열들이 내용대로 가져간 뒤 이름만 최소 폭으로 찌그러져
        `tc_layout...` 이 됐다. 이름은 계정을 가리키는 유일한 말이다."""

        from smvwp.gui.main_window import COL_NAME

        table = self.window.table
        self.assertGreaterEqual(
            table.horizontalHeader().sectionSize(COL_NAME),
            table.sizeHintForColumn(COL_NAME),
        )

    # -- 옮겨 간 값들 ------------------------------------------------------
    def test_the_path_moved_to_the_name_tooltip(self):
        """경로 열을 뺐다고 경로를 못 보게 되면 안 된다."""

        from PyQt5.QtCore import Qt

        from smvwp.gui.main_window import COL_NAME

        account = self.config.accounts[0]
        table = self.window.table
        # 행 순서를 가정하지 않는다 - 기본 정렬이 사용률 내림차순이다.
        item = next(
            table.item(row, COL_NAME) for row in range(table.rowCount())
            if table.item(row, COL_NAME).data(Qt.UserRole) == account.account_id
        )
        self.assertIn(account.path, item.toolTip())

    def test_a_collection_failure_still_shows_up_somewhere(self):
        """상태 열을 뺐다 - 실패를 아무 데서도 못 보면 숫자를 믿게 된다."""

        from datetime import datetime, timezone

        from smvwp import i18n, store
        from smvwp.gui.main_window import COL_TIME

        account = self.config.accounts[0]
        self.window._render_table({account.account_id: store.SampleRecord(
            account_id=account.account_id,
            collected_at=datetime.now(timezone.utc).isoformat(),
            ok=False, error_message="df: permission denied",
            total_kb=None, used_kb=None,
        )})
        item = self.window.table.item(0, COL_TIME)
        self.assertEqual(item.text(), i18n.t("dashboard.collect_error_short"))
        self.assertIn("permission denied", item.toolTip())


class AccountDetailDialogTests(_GuiCase):
    """계정 하나에 대한 것을 한 창에 모은다 (홈에서 두 번 누르면)."""

    GB = 1024 * 1024

    def setUp(self):
        super().setUp()
        # 읽기는 스레드에서 돈다 - 시험에서는 우리가 값을 직접 넣는다.
        patcher = patch(
            "smvwp.gui.account_detail_dialog.DetailReader.run_async",
            lambda self, account_id: True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        from smvwp.gui.account_detail_dialog import AccountDetailDialog

        self.account = self.config.accounts[0]
        self.dialog = AccountDetailDialog(
            self.data_dir, lambda: self.config, self.account.account_id
        )
        self.addCleanup(self.dialog.close)

    def detail(self, **kwargs):
        from datetime import datetime, timezone

        from smvwp import account_detail, health, store

        made = account_detail.AccountDetail(
            account_id=self.account.account_id, name=self.account.name,
            path=self.account.path, kind=self.account.kind,
        )
        made.sample = store.SampleRecord(
            account_id=self.account.account_id,
            collected_at=datetime.now(timezone.utc).isoformat(), ok=True,
            filesystem="nfs4", mount_point="/ifs",
            total_kb=1000 * self.GB, used_kb=910 * self.GB, avail_kb=90 * self.GB,
            byte_pct=91.0, inode_pct=12.0, byte_tier="alert", overall_tier="alert",
        )
        made.health = [
            health.RunHealth(
                self.account.account_id, self.account.name,
                self.account.path + "/task/01_run_0908", "task / 01_run_0908",
                run_size_kb=300 * self.GB, backup_size_kb=300 * self.GB,
                mirror_size_kb=300 * self.GB, status=health.BACKED_UP,
            ),
        ]
        for key, value in kwargs.items():
            setattr(made, key, value)
        return made

    def load(self, **kwargs):
        self.dialog._on_loaded(self.detail(**kwargs))
        _app().processEvents()

    # -- 채워지는가 --------------------------------------------------------
    def test_the_numbers_land_in_the_left_column(self):
        self.load()
        self.assertIn("91", self.dialog.usage_label.text())
        self.assertIn("910.0", self.dialog._fact_rows["capacity"][1].text())
        self.assertIn("12", self.dialog._fact_rows["inode"][1].text())

    def test_a_quota_without_a_limit_still_shows_the_usage(self):
        """한도 없이 사용량만 오는 구성. 홈 표의 quota 열을 뺄 때 이 경우를
        '-' 로 떨어뜨렸다 - 값을 받고도 버리는 셈이었다."""

        detail = self.detail()
        detail.sample.quota_used_kb = 5_000
        detail.sample.quota_limit_kb = None
        detail.sample.quota_pct = None
        self.dialog._on_loaded(detail)
        self.assertIn("5,000", self.dialog._fact_rows["quota"][1].text())

    def test_the_backup_summary_says_what_can_be_cleaned(self):
        self.load()
        text = self.dialog.health_label.text()
        self.assertIn("300", text)

    def test_the_tables_say_so_when_there_is_nothing_yet(self):
        """빈 표는 고장으로 읽힌다."""

        from smvwp import i18n

        self.load()
        self.assertEqual(self.dialog.large_table.rowCount(), 1)
        self.assertEqual(
            self.dialog.large_table.item(0, 0).text(), i18n.t("detail.no_rows")
        )

    def test_a_missing_account_is_said_out_loud(self):
        from smvwp import i18n

        self.dialog._on_loaded(None)
        self.assertEqual(self.dialog.status_label.text(), i18n.t("detail.gone"))

    def test_what_could_not_be_read_is_named(self):
        """조용히 비우면 "없다"로 읽힌다."""

        from smvwp import account_detail, i18n

        self.load(failures=[account_detail.PART_HEALTH])
        self.assertIn(
            i18n.t("detail.part.health"), self.dialog.status_label.text()
        )

    def test_paths_are_shown_under_the_account(self):
        from smvwp import large_files

        rows = large_files.build(
            [(self.account.path + "/task/01_run/psf/tran.tr0", 500 * self.GB, None)],
            1000 * self.GB,
        )
        self.load(large_files=rows)
        cell = self.dialog.large_table.item(0, 0)
        self.assertTrue(cell.text().startswith("task/"), cell.text())
        self.assertIn(self.account.path, cell.toolTip())

    def test_no_translation_keys_leak(self):
        self.load()
        text = " ".join([
            self.dialog.health_label.text(), self.dialog.scan_label.text(),
            self.dialog.forecast_label.text(), self.dialog.trend_summary.text(),
            self.dialog.path_label.text(),
        ])
        self.assertNotIn("detail.", text)
        self.assertNotIn("health.status.", text)

    def test_it_fits_a_1080_screen(self):
        needed = self.dialog.minimumSizeHint().height()
        self.assertLess(needed, 700, f"창 최소 세로 {needed}px")

    # -- 창과의 경계 --------------------------------------------------------
    def test_going_to_the_scan_tab_is_the_windows_job(self):
        """대화상자가 창의 탭을 직접 만지면 둘을 따로 옮길 수 없게 된다."""

        seen = []
        self.dialog.open_scan_requested.connect(seen.append)
        self.dialog._go_to_scan()
        self.assertEqual(seen, [self.account.account_id])


class HomeToDetailTests(_GuiCase):
    """홈 표에서 두 번 누르면 그 계정의 창이 열린다."""

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)
        self.window._render_table({})
        _app().processEvents()

    def tearDown(self):
        self.window.close()
        super().tearDown()

    def test_double_clicking_a_row_opens_that_account(self):
        opened = []

        def fake_exec(dialog):
            opened.append(dialog._account_id)
            return 0

        with patch(
            "smvwp.gui.account_detail_dialog.AccountDetailDialog.exec_", fake_exec
        ), patch(
            "smvwp.gui.account_detail_dialog.DetailReader.run_async",
            lambda self, account_id: True,
        ):
            self.window.table.cellDoubleClicked.emit(0, 0)
        self.assertEqual(opened, [self.config.accounts[0].account_id])

    def test_the_window_moves_to_the_scan_tab_when_asked(self):
        account = self.config.accounts[1]
        self.window._show_account_in_scan_tab(account.account_id)
        self.assertEqual(
            self.window.tabs.currentIndex(),
            self.window.tabs.indexOf(self.window._scan_page),
        )
        self.assertEqual(
            self.window._scan_tab.scan_account_combo.currentData(), account.account_id
        )


class PriorityClickTests(_GuiCase):
    """'무엇부터 할까' 한 줄을 누르면 상황과 할 일이 뜬다.

    커서를 올리면 줄이 반응하는데 눌러도 아무 일이 없었다 - 반응만 하고 아무
    일이 없으면 고장으로 읽힌다."""

    GB = 1024 * 1024

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)
        self.account = self.config.accounts[0]

    def tearDown(self):
        self.window.close()
        super().tearDown()

    def plan(self):
        from smvwp import priority

        made = priority.Plan()
        made.actions = [priority.Action(
            kind=priority.ACT_FULL, level=priority.LEVEL_CRITICAL,
            account=self.account.name, account_id=self.account.account_id,
            pct=96.0, reclaimable_kb=800 * self.GB, count=3,
        )]
        made.accounts_without_scan = ["fresh"]
        made.coarse_count = 2
        return made

    def click(self, row):
        opened = []

        def fake_exec(dialog):
            opened.append(dialog)
            return 0

        with patch("smvwp.gui.action_dialog.ActionDialog.exec_", fake_exec), patch(
            "smvwp.gui.account_detail_dialog.DetailReader.run_async",
            lambda self, account_id: True,
        ):
            self.window._refresh_priority(self.plan())
            item = self.window.priority_list.item(row)
            self.window.priority_list.itemClicked.emit(item)
        return opened

    def test_clicking_an_action_opens_its_guide(self):
        from smvwp import priority

        opened = self.click(0)
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0].guide.kind, priority.ACT_FULL)
        self.assertEqual(opened[0].guide.account_id, self.account.account_id)

    def test_the_side_notes_are_clickable_too(self):
        """'판단에 못 넣었다' 는 줄도 누르면 왜 그런지와 할 일이 나와야 한다."""

        from smvwp import action_guide

        kinds = [self.click(row)[0].guide.kind for row in (1, 2)]
        self.assertEqual(
            sorted(kinds), sorted([action_guide.KIND_COARSE, action_guide.KIND_UNSCANNED])
        )

    def test_the_list_says_its_lines_can_be_clicked(self):
        from smvwp import i18n

        self.window._refresh_priority(self.plan())
        self.assertEqual(self.window.priority_hint.text(), i18n.t("priority.hint"))

    def test_opening_a_guide_is_recorded(self):
        """어떤 경고가 실제로 사람을 움직이는지 알고 싶다."""

        from smvwp import priority, usage_log

        recorded = []
        with patch.object(
            usage_log, "record", side_effect=lambda *a, **k: recorded.append((a, k))
        ):
            self.click(0)
        self.assertIn(
            (usage_log.GUIDE_OPENED, priority.ACT_FULL),
            [(args[1], kwargs.get("detail")) for args, kwargs in recorded],
        )


class ActionDialogTests(_GuiCase):
    """안내 창 자체 - 무엇이 보이고, 무엇을 누를 수 있나."""

    GB = 1024 * 1024

    def setUp(self):
        super().setUp()
        patcher = patch(
            "smvwp.gui.account_detail_dialog.DetailReader.run_async",
            lambda self, account_id: True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.account = self.config.accounts[0]

    def dialog(self, action=None, guide=None):
        from smvwp.gui.action_dialog import ActionDialog

        made = ActionDialog(
            self.data_dir, lambda: self.config, action=action, guide=guide
        )
        self.addCleanup(made.close)
        made.show()
        _app().processEvents()
        return made

    def full(self):
        from smvwp import priority

        return priority.Action(
            kind=priority.ACT_FULL, level=priority.LEVEL_CRITICAL,
            account=self.account.name, account_id=self.account.account_id,
            pct=96.0, reclaimable_kb=300 * self.GB, count=1,
        )

    def detail(self):
        from smvwp import account_detail, health

        made = account_detail.AccountDetail(
            self.account.account_id, self.account.name, self.account.path
        )
        made.health = [health.RunHealth(
            self.account.account_id, self.account.name,
            self.account.path + "/task/01_run", "task / 01_run",
            run_size_kb=300 * self.GB, backup_size_kb=300 * self.GB,
            mirror_path="/backup/task/01_run", mirror_size_kb=300 * self.GB,
            status=health.BACKED_UP, match_by=health.MATCH_ITEMS,
        )]
        return made

    def test_it_shows_the_level_and_the_same_sentence_as_the_list(self):
        from smvwp import action_guide, i18n

        action = self.full()
        dialog = self.dialog(action=action)
        self.assertEqual(dialog.level_chip.text(), i18n.t("guide.level.critical"))
        self.assertEqual(dialog.headline.text(), action_guide.headline(action))

    def test_steps_are_there_before_the_evidence_arrives(self):
        from smvwp import i18n

        dialog = self.dialog(action=self.full())
        self.assertTrue(dialog.steps.text().startswith("1. "))
        self.assertEqual(dialog.evidence_note.text(), i18n.t("guide.loading"))

    def test_the_evidence_fills_in_when_it_arrives(self):
        dialog = self.dialog(action=self.full())
        dialog._on_detail(self.detail())
        _app().processEvents()
        self.assertEqual(dialog.evidence.rowCount(), 1)
        self.assertEqual(dialog.evidence.item(0, 0).text(), "task / 01_run")
        self.assertIn("du -sh", dialog.commands.toPlainText())

    def test_copy_path_copies_the_selected_row(self):
        from PyQt5.QtWidgets import QApplication

        dialog = self.dialog(action=self.full())
        dialog._on_detail(self.detail())
        self.assertFalse(dialog.copy_path_btn.isEnabled(), "고른 줄이 없으면 복사할 것도 없다")
        dialog.evidence.selectRow(0)
        self.assertTrue(dialog.copy_path_btn.isEnabled())
        dialog.copy_path_btn.click()
        self.assertEqual(
            QApplication.clipboard().text(), self.account.path + "/task/01_run"
        )

    def test_nothing_scrolls_sideways(self):
        from PyQt5.QtWidgets import QAbstractScrollArea

        dialog = self.dialog(action=self.full())
        dialog._on_detail(self.detail())
        _app().processEvents()
        sideways = [
            view.__class__.__name__ for view in dialog.findChildren(QAbstractScrollArea)
            if view.isVisible() and view.horizontalScrollBar().isVisible()
        ]
        self.assertEqual(sideways, [])

    def test_a_text_only_guide_folds_the_right_side_away(self):
        """넓은 창 가운데 글만 떠 있으면 뭔가 빠진 화면으로 읽힌다."""

        from smvwp import action_guide

        dialog = self.dialog(guide=action_guide.for_unscanned(["fresh"]))
        self.assertFalse(dialog.right_panel.isVisible())
        self.assertTrue(dialog.scan_btn.isVisible())
        self.assertFalse(dialog.copy_path_btn.isVisible())

    def test_going_to_the_scan_tab_is_the_windows_job(self):
        seen = []
        dialog = self.dialog(action=self.full())
        dialog.open_scan_requested.connect(seen.append)
        dialog.scan_btn.click()
        self.assertEqual(seen, [self.account.account_id])

    def test_it_fits_a_1080_screen(self):
        dialog = self.dialog(action=self.full())
        self.assertLess(dialog.minimumSizeHint().height(), 700)


class ManyAccountsTests(_GuiCase):
    """한 사람이 계정 여럿을 볼 때.

    스무 개가 넘어가면 "어느 계정이 급한가" 와 "그 계정이 어디 있나" 가 둘 다
    일이 된다. 급한 것이 위에 오고, 이름 조각으로 좁힐 수 있어야 한다."""

    GB = 1024 * 1024

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)
        self.window._render_table(self.samples())
        _app().processEvents()

    def tearDown(self):
        self.window.close()
        super().tearDown()

    def samples(self):
        from datetime import datetime, timezone

        from smvwp import store

        # proj 은 한산하고 bak 이 꽉 찼다 - 정렬이 실제로 도는지 보려면 순서가
        # 등록 순서와 달라야 한다.
        percentages = {"proj": 40.0, "bak": 96.0}
        made = {}
        for account in self.config.accounts:
            pct = percentages[account.name]
            total = 10 * 1024 * self.GB
            made[account.account_id] = store.SampleRecord(
                account_id=account.account_id,
                collected_at=datetime.now(timezone.utc).isoformat(), ok=True,
                filesystem="nfs4", mount_point="/ifs", total_kb=total,
                used_kb=int(total * pct / 100), avail_kb=1, byte_pct=pct,
                overall_tier="alert" if pct > 90 else "normal",
            )
        return made

    def names_in_order(self):
        from smvwp.gui.main_window import COL_NAME

        table = self.window.table
        return [table.item(row, COL_NAME).text() for row in range(table.rowCount())]

    # -- 급한 것부터 ------------------------------------------------------
    def test_the_fullest_account_is_on_top_without_touching_anything(self):
        """등록 순서로 두면 스무 개 넘는 목록에서 급한 계정이 가운데 묻힌다."""

        self.assertEqual(self.names_in_order()[0], "bak")

    def test_the_header_shows_which_column_it_is_sorted_by(self):
        from PyQt5.QtCore import Qt

        from smvwp.gui.main_window import COL_BYTE

        header = self.window.table.horizontalHeader()
        self.assertEqual(header.sortIndicatorSection(), COL_BYTE)
        self.assertEqual(header.sortIndicatorOrder(), Qt.DescendingOrder)

    # -- 찾기 --------------------------------------------------------------
    def test_typing_a_name_narrows_the_list(self):
        self.window.filter_edit.setText("ba")
        _app().processEvents()
        self.assertEqual(self.names_in_order(), ["bak"])

    def test_the_path_counts_too(self):
        """이름을 기억 못 해도 경로 조각으로 찾을 수 있어야 한다."""

        self.window.filter_edit.setText(self.config.accounts[0].path[-4:].lower())
        _app().processEvents()
        self.assertEqual(self.names_in_order(), [self.config.accounts[0].name])

    def test_it_says_how_many_are_hidden(self):
        """줄어든 목록이 "계정이 사라졌다" 로 읽히면 안 된다."""

        from smvwp import i18n

        self.window.filter_edit.setText("ba")
        _app().processEvents()
        self.assertEqual(
            self.window.list_hint.text(),
            i18n.t("dashboard.list_filtered", shown=1, total=2),
        )

    def test_clearing_the_box_brings_everyone_back(self):
        from smvwp import i18n

        self.window.filter_edit.setText("ba")
        self.window.filter_edit.setText("")
        _app().processEvents()
        self.assertEqual(len(self.names_in_order()), 2)
        self.assertEqual(self.window.list_hint.text(), i18n.t("dashboard.list_hint"))

    def test_the_summary_still_describes_everyone_while_filtered(self):
        """한 계정만 남겨 놓고 "모든 계정 정상" 을 읽으면, 보이지 않는 곳이
        꽉 차 있어도 괜찮은 줄 안다."""

        self.window._latest_samples = self.samples()
        self.window.filter_edit.setText("proj")   # 한산한 계정만 남긴다
        _app().processEvents()
        self.assertEqual(self.names_in_order(), ["proj"])
        # 히어로는 여전히 꽉 찬 쪽을 말한다.
        self.assertIn("96", self.window.hero_value_label.text())
        self.assertEqual(self.window.hero_stats["attention"][1].text(), "1")

    def test_a_filter_that_matches_nothing_is_not_a_crash(self):
        self.window.filter_edit.setText("없는계정")
        _app().processEvents()
        self.assertEqual(self.names_in_order(), [])

    # -- 키보드 -------------------------------------------------------------
    def test_enter_opens_the_selected_account(self):
        """화살표로 훑다가 Enter 로 여는 사람이 있다."""

        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QKeySequence
        from PyQt5.QtWidgets import QShortcut

        opened = []
        shortcut = next(
            item for item in self.window.table.findChildren(QShortcut)
            if item.key() == QKeySequence(Qt.Key_Return)
        )
        self.window.table.selectRow(0)
        with patch.object(
            self.window, "_open_account_detail", side_effect=opened.append
        ):
            shortcut.activated.emit()
        self.assertEqual(opened, [0])


class GrowthPageSortTests(_GuiCase):
    """증가 경로 표는 헤더를 눌러 정렬할 수 있어야 한다.

    채우는 동안 정렬을 껐다가 다시 켜야 하는데, 증감이 있는 경우(= 스캔이 두 번
    이상 돈 평소의 경우)에는 다시 켜기 전에 함수가 먼저 돌아가 버려서 **정렬이
    꺼진 채 남았다.** 헤더를 눌러도 아무 일이 없었고, 정렬이 되는 것은 스캔이 한
    번뿐인 계정뿐이었다."""

    GB = 1024 * 1024

    def setUp(self):
        super().setUp()
        from smvwp.gui.scan_pages import GrowthPage

        self.page = GrowthPage()
        self.page.retranslate()
        self.addCleanup(self.page.close)
        self.account = self.config.accounts[0]

    def snapshot(self, growth=(), top=()):
        from smvwp.nightly_scan import AccountScanSnapshot, StatusSnapshot

        entry = AccountScanSnapshot(
            account_id=self.account.account_id, account_name=self.account.name,
            last_completed_generation=3, top_paths=list(top), growth=list(growth),
            pending_baseline_count=0, current_scan_at="2026-10-01T00:00:00+00:00",
            previous_scan_at="2026-09-30T00:00:00+00:00",
        )
        return StatusSnapshot(
            is_running=False, window_description="", latest_run=None, accounts=[entry]
        )

    def growth(self):
        root = self.account.path
        return self.snapshot(growth=[
            {"path": f"{root}/small", "current_kb": 100 * self.GB, "previous_kb": 90 * self.GB},
            {"path": f"{root}/big", "current_kb": 500 * self.GB, "previous_kb": 100 * self.GB},
            {"path": f"{root}/new", "current_kb": 50 * self.GB, "previous_kb": None},
        ])

    def first_column(self):
        table = self.page.table
        return [table.item(row, 0).text() for row in range(table.rowCount())]

    def test_sorting_is_back_on_after_showing_growth(self):
        self.page.show_account(self.growth(), self.account)
        self.assertTrue(self.page.table.isSortingEnabled())

    def test_the_default_order_is_most_grown_first(self):
        """새 경로는 통째로 는 것으로 본다 (저장소가 고른 순서와 같다)."""

        self.page.show_account(self.growth(), self.account)
        self.assertEqual(self.first_column(), ["big", "new", "small"])

    def test_a_sort_the_person_chose_survives_a_refresh(self):
        """갱신할 때마다 기본값으로 되돌리면 헤더를 누른 의미가 없다."""

        from PyQt5.QtCore import Qt

        from smvwp.gui.scan_pages import GROWTH_PATH

        self.page.show_account(self.growth(), self.account)
        # 사람이 경로 헤더를 눌러 내림차순으로 바꾼다 (기본과 확실히 다른 순서).
        self.page.table.horizontalHeader().sectionClicked.emit(GROWTH_PATH)
        self.page.table.sortByColumn(GROWTH_PATH, Qt.DescendingOrder)
        self.assertEqual(self.first_column(), ["small", "new", "big"])

        self.page.show_account(self.growth(), self.account)
        self.assertEqual(self.first_column(), ["small", "new", "big"])

    def test_without_a_previous_scan_the_biggest_comes_first(self):
        root = self.account.path
        self.page.show_account(self.snapshot(top=[
            {"path": f"{root}/x", "size_kb": 10 * self.GB},
            {"path": f"{root}/y", "size_kb": 90 * self.GB},
        ]), self.account)
        self.assertTrue(self.page.table.isSortingEnabled())
        self.assertEqual(self.first_column(), ["y", "x"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
