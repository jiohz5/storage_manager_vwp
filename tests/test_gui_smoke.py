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
    "smvwp.gui.main_window._FreshnessReader.start",
    "smvwp.gui.data_dialog._InventoryReader.start",
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
        # 화면이 맡긴 사용 기록을 다 쓰고 나서 임시 디렉터리를 지운다.
        from smvwp import usage_log

        usage_log.flush(timeout=5.0)
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

    def test_the_scan_tile_switches_to_the_scan_tab(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest

        self.window.tabs.setCurrentIndex(0)
        QTest.mouseClick(self.window.tiles["scan"], Qt.LeftButton)
        _app().processEvents()
        self.assertEqual(
            self.window.tabs.currentWidget(), self.window._scan_page,
            "상세 스캔 타일이 스캔 탭으로 가지 않습니다",
        )

    def test_the_header_buttons_move_between_pages_and_follow_them(self):
        """탭 띠를 숨겼다 - 머리줄의 두 단추가 그 일을 한다. 다른 길(타일, 계정
        더블클릭)로 쪽이 바뀌어도 단추가 지금 쪽을 가리켜야 한다."""

        self.window.nav_scan.click()
        _app().processEvents()
        self.assertEqual(self.window.tabs.currentWidget(), self.window._scan_page)
        self.window.tabs.setCurrentIndex(0)
        _app().processEvents()
        self.assertTrue(self.window.nav_home.isChecked())
        self.assertFalse(self.window.nav_scan.isChecked())

    def test_banner_signal_reaches_the_window(self):
        """탭이 값만 보내고 창이 그린다 - 그 신호 경계가 실제로 이어져 있는가."""

        from smvwp.gui.scan_tab import BannerState

        self.window._scan_tab.banner_changed.emit(
            BannerState(running=True, path="/x/top7", done=3, total=10)
        )
        _app().processEvents()
        self.assertIn("/x/top7", self.window.tiles["scan"].detail.text())

        self.window._scan_tab.banner_changed.emit(BannerState(running=False))
        _app().processEvents()
        self.assertNotIn("/x/top7", self.window.tiles["scan"].detail.text())

    def test_status_message_signal_reaches_the_status_bar(self):
        self.window._scan_tab.status_message.emit("hello")
        _app().processEvents()
        self.assertEqual(self.window.status_bar_label.text(), "hello")

    def test_there_is_no_menu_bar_row_and_language_is_in_tools(self):
        """메뉴 줄에 `언어` 하나만 있어 창 맨 위 한 줄(약 35px)을 통째로 썼다.
        언어는 도구 메뉴 안으로 - 다른 말을 쓰는 사람도 찾게 제목에 두 말을 다 둔다."""

        from smvwp import i18n

        self.assertIsNone(self.window.menuWidget())
        tools = self.window.tools_btn.menu()
        self.assertIn(self.window.language_menu.menuAction(), tools.actions())
        self.addCleanup(i18n.set_language, "ko")
        for code in ("ko", "en"):
            i18n.set_language(code)
            self.window.retranslate()
            title = self.window.language_menu.title()
            self.assertIn("Language", title)
            self.assertIn("언어", title)

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
            usage_log, "record_soon", side_effect=lambda *a, **k: recorded.append((a, k))
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
                    "콤보가 필요한 폭보다 좁게 놓였습니다",
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
            # 끝난 바퀴의 합계 - 비중의 분모다 (하는 중인 바퀴의 `measured_kb` 가 아니라).
            current_measured_kb = None

        class FakeSnapshot:
            accounts = [FakeEntry()]

        FakeEntry.current_measured_kb = measured_kb
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
        from smvwp.gui import theme
        from smvwp.gui.scan_pages import LARGE_PATH

        self._snapshot_with([("/a/huge", 500_000, 500_000)], measured_kb=1_000_000)
        item = self.tab.large_page.table.item(0, LARGE_PATH)
        # 글자에는 등급 색의 '글자용' 쪽을 쓴다 - 노랑 그대로는 흰 바탕에서 안 읽힌다.
        self.assertEqual(
            item.foreground().color().name(), QColor(theme.tier_text(tiers.WARN)).name()
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

    def test_only_the_cells_that_explain_why_are_colored(self):
        """줄 전체를 칠하면 무엇 때문인지 모른다. 계정을 혼자 먹지만 지난번과 같은 파일은
        비중 칸이 칠해지고 증감 칸은 그대로다."""

        from PyQt5.QtGui import QColor

        from smvwp import tiers
        from smvwp.gui import theme
        from smvwp.gui.scan_pages import LARGE_CHANGE, LARGE_SHARE

        self._snapshot_with([("/a/huge", 500_000, 500_000)], measured_kb=1_000_000)
        table = self.tab.large_page.table
        warn = QColor(theme.tier_text(tiers.WARN)).name()
        self.assertEqual(table.item(0, LARGE_SHARE).foreground().color().name(), warn)
        self.assertNotEqual(table.item(0, LARGE_CHANGE).foreground().color().name(), warn)

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

        from smvwp.gui.scan_pages import TAB_SUMMARY

        self.window = MainWindow(self.data_dir, self.config)
        self.tab = self.window._scan_tab
        # 요약 쪽을 본다 - 이 탭은 이제 현황이 먼저 열린다.
        self.tab.detail_tabs.setCurrentIndex(TAB_SUMMARY)

    def tearDown(self):
        self.window.close()
        super().tearDown()

    def snapshot(self, *accounts, run=None, running=False):
        from dataclasses import dataclass
        from typing import Optional

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
            current_measured_kb: Optional[int] = None
            previous_measured_kb: Optional[int] = None
            current_scan_at: Optional[str] = "2026-09-10T00:00:00+00:00"
            large_files: List[tuple] = field(default_factory=list)
            failed_count: int = 0
            partial_paths: List[str] = field(default_factory=list)

        return Acct(
            account_name=name, current_measured_kb=measured, previous_measured_kb=previous,
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
        """현황·요약·계정별·증가 경로·큰 파일 - 한 범주가 한 탭."""

        from smvwp.gui.scan_pages import TAB_KEYS

        tabs = self.window._scan_tab.detail_tabs
        self.assertEqual(tabs.count(), len(TAB_KEYS))
        self.assertEqual(len(TAB_KEYS), 6)   # 현황·요약·계정별·용량 변화·큰 파일·사람별

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

    def test_the_overview_tab_opens_first(self):
        """"어디까지 스캔됐고 언제 끝나나" 가 이 탭을 여는 까닭이라 현황이 먼저
        열린다. 요약(카드 넷과 살펴볼 것)은 바로 옆 탭이다."""

        from smvwp.gui.scan_pages import TAB_OVERVIEW, TAB_SUMMARY

        tab = self.window._scan_tab
        self.assertEqual(tab.detail_tabs.currentIndex(), TAB_OVERVIEW)
        self.assertTrue(tab.overview_page.isVisibleTo(tab))
        tab.detail_tabs.setCurrentIndex(TAB_SUMMARY)
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
            large_files=large, measured_kb=1000 * self.GB, current_measured_kb=1000 * self.GB,
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
    """홈은 타일 한 줄 아래에 두 칸이다 - 왼쪽에 계정 목록, 오른쪽에 무엇부터
    할까와 오늘 밤 상세 스캔 (2026-11 개편).

    예전에는 히어로·우선순위·버튼·표를 세로로 쌓아 위 셋이 세로의 절반을
    먹었고, 열이 열둘인 표는 1,200 창에서 그것만으로 가로 스크롤이 났다.
    소스를 읽어서는 알 수 없는 것들이라 처음 여는 크기로 띄워 잰다.
    """

    GB = 1024 * 1024

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        from smvwp.gui.main_window import DEFAULT_SIZE

        self.window = MainWindow(self.data_dir, self.config)
        self._fill_samples()
        self.window.resize(*DEFAULT_SIZE)
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
    def _top(self, widget):
        return widget.mapTo(self.window, widget.rect().topLeft())

    def _bottom_right(self, widget):
        return widget.mapTo(self.window, widget.rect().bottomRight())

    def test_the_tiles_come_before_the_list(self):
        """답(가장 찬 곳, 손볼 계정, 한 바퀴, 끝 예상)이 먼저, 표는 그 아래."""

        table_top = self._top(self.window.table).y()
        for key, tile in self.window.tiles.items():
            self.assertLessEqual(self._bottom_right(tile).y(), table_top, key)

    def test_the_four_tiles_share_one_row(self):
        tops = {self._top(tile).y() for tile in self.window.tiles.values()}
        self.assertEqual(len(tops), 1, tops)

    def test_the_right_column_sits_beside_the_list(self):
        table = self.window.table
        priority = self.window.priority_card
        tonight = self.window.tonight_card
        self.assertGreaterEqual(self._top(priority).x(), self._bottom_right(table).x())
        self.assertGreater(self._top(tonight).y(), self._top(priority).y(),
                           "오늘 밤 카드는 무엇부터 할까 아래에 있어야 한다")
        self.assertEqual(priority.width(), tonight.width())

    def test_the_list_gets_most_of_the_height(self):
        """타일 줄을 뺀 세로는 표가 거의 다 쓴다."""

        page = self.window.tabs.widget(0)
        ratio = self.window.table.height() / page.height()
        self.assertGreater(ratio, 0.55, f"표 자리 {ratio:.0%}")

    def test_the_refresh_button_is_in_the_header(self):
        """가장 자주 누르는 단추라 늘 같은 자리(머리줄)에 있어야 한다 - 쪽을
        바꿔도 사라지지 않는다."""

        button = self.window.collect_btn
        self.assertLess(self._bottom_right(button).y(), self._top(self.window.tiles["fullest"]).y())
        self.window.tabs.setCurrentIndex(self.window.tabs.indexOf(self.window._scan_page))
        _app().processEvents()
        self.assertTrue(button.isVisible())

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
        # 상세 스캔 탭과 같은 쪽이다 - 빈 표 대신 설명 줄이 말한다.
        self.assertEqual(self.dialog.large_table.rowCount(), 0)
        self.assertEqual(
            self.dialog.large_page.caption.text(),
            i18n.t("large.none", account=self.account.name),
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
            usage_log, "record_soon", side_effect=lambda *a, **k: recorded.append((a, k))
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
        # 타일은 여전히 꽉 찬 쪽을 말한다.
        self.assertIn("96", self.window.tiles["fullest"].value.text())
        self.assertEqual(self.window.tiles["attention"].value.text(), "1")

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

    # -- 용량 변화 표의 칸들 ----------------------------------------------
    def cell(self, label, column):
        table = self.page.table
        for row in range(table.rowCount()):
            if table.item(row, 0).text() == label:
                return table.item(row, column)
        raise AssertionError(f"{label} 줄 없음")

    def test_a_folder_not_measured_last_time_is_not_counted_as_growth(self):
        """지난번에 못 잰 곳의 크기 전체를 증가로 치면 없던 급증이 맨 위에 선다."""

        from smvwp import i18n, scan_store
        from smvwp.gui.scan_pages import GROWTH_DELTA

        root = self.account.path
        self.page.show_account(self.snapshot(growth=[
            {"path": f"{root}/big", "current_kb": 500 * self.GB, "previous_kb": 100 * self.GB},
            {"path": f"{root}/hidden", "current_kb": 900 * self.GB, "previous_kb": None,
             "previous_note": scan_store.PREVIOUS_UNMEASURED},
        ]), self.account)
        self.assertEqual(self.cell("hidden", GROWTH_DELTA).text(), i18n.t("growth.unmeasured"))
        self.assertEqual(self.first_column(), ["big", "hidden"])
        # 막대의 기준은 아는 변화 중 가장 큰 것이다.
        self.assertEqual(self.page.bars.scale, 400 * self.GB)

    def test_a_drop_reads_as_a_drop(self):
        """정리한 곳도 보여야 한다 - 늘어난 것과 다른 색으로."""

        from PyQt5.QtGui import QColor

        from smvwp import tiers
        from smvwp.gui import theme
        from smvwp.gui.scan_pages import GROWTH_DELTA

        root = self.account.path
        self.page.show_account(self.snapshot(growth=[
            {"path": f"{root}/up", "current_kb": 300 * self.GB, "previous_kb": 100 * self.GB},
            {"path": f"{root}/down", "current_kb": 100 * self.GB, "previous_kb": 900 * self.GB},
        ]), self.account)
        # 변화 색이다 - 위험(등급) 색이 아니다 (`theme.CHANGE_*`).
        self.assertEqual(self.cell("down", GROWTH_DELTA).foreground().color().name(),
                         QColor(theme.CHANGE_DOWN).name())
        self.assertEqual(self.cell("up", GROWTH_DELTA).foreground().color().name(),
                         QColor(theme.CHANGE_UP).name())
        self.assertNotEqual(QColor(theme.CHANGE_UP).name(),
                            QColor(theme.tier_text(tiers.WARN)).name())
        self.assertEqual(self.page.bars.scale, 800 * self.GB)

    def test_the_focus_column_says_where_inside(self):
        from smvwp import i18n, scan_store
        from smvwp.gui.scan_pages import GROWTH_FOCUS

        i18n.set_language("ko")
        root = self.account.path
        self.page.show_account(self.snapshot(growth=[
            {"path": f"{root}/sim", "current_kb": 7900 * self.GB, "previous_kb": 4700 * self.GB,
             "previous_note": None,
             "focus": {"path": f"{root}/sim/regress_1107", "delta_kb": 3200 * self.GB,
                       "note": scan_store.FOCUS_NEW}},
            {"path": f"{root}/users", "current_kb": 800 * self.GB, "previous_kb": 400 * self.GB,
             "previous_note": None,
             "focus": {"path": f"{root}/users", "delta_kb": 400 * self.GB,
                       "note": scan_store.FOCUS_SPREAD}},
            {"path": f"{root}/lib", "current_kb": 900 * self.GB, "previous_kb": 950 * self.GB,
             "previous_note": None, "focus": None},
        ]), self.account)
        from smvwp.gui.widgets import CellDelegate

        sim = self.cell("sim", GROWTH_FOCUS)
        self.assertTrue(sim.text().startswith("regress_1107"), sim.text())
        # '새 폴더' 는 괄호 속 말이 아니라 알약이다 - 문장 전체는 툴팁에.
        self.assertEqual(sim.data(CellDelegate.CHIP_ROLE), ("새 폴더", "change_up"))
        self.assertIn("새 폴더", sim.toolTip())
        self.assertIn(f"{root}/sim/regress_1107", sim.toolTip())
        # 크게 변했는데 한 곳으로 모이지 않았으면 그렇다고 말한다.
        self.assertEqual(self.cell("users", GROWTH_FOCUS).text(), i18n.t("growth.focus.spread"))
        # 하위 폴더 기록이 없는 폴더는 더 말할 것이 없다 ('고르게'가 아니다).
        self.assertEqual(self.cell("lib", GROWTH_FOCUS).text(), "")

    def test_the_caption_gives_the_whole_account_change(self):
        """줄마다의 증감은 위에서 자른 목록이다 - 계정 전체는 따로 말한다."""

        from smvwp import formatting

        snap = self.growth()
        snap.accounts[0].current_measured_kb = 2000 * self.GB
        snap.accounts[0].previous_measured_kb = 1500 * self.GB
        snap.accounts[0].measured_delta_kb = 500 * self.GB
        self.page.show_account(snap, self.account)
        # 설명 줄의 글이 아니라 요약 띠의 큰 값과 전후 막대로 세운다.
        self.assertEqual(self.page.band.value.text(), formatting.format_kb_delta(500 * self.GB))
        self.assertEqual(self.page.delta_bar.values(), (1500 * self.GB, 2000 * self.GB))

    def test_places_unmeasured_in_one_pass_are_named_on_the_band(self):
        """두 바퀴 모두 잰 곳끼리 견줬다는 것을 띠에서 말한다 - 뺀 자리는 알약 툴팁에."""

        from smvwp import formatting, i18n

        i18n.set_language("ko")
        root = self.account.path
        snap = self.growth()
        entry = snap.accounts[0]
        entry.current_measured_kb, entry.previous_measured_kb = 1310 * self.GB, 400 * self.GB
        entry.measured_delta_kb = 10 * self.GB
        entry.compared_kb = (400 * self.GB, 410 * self.GB)
        entry.delta_excluded = [f"{root}/a"]
        self.page.show_account(snap, self.account)
        self.assertEqual(self.page.band.value.text(), formatting.format_kb_delta(10 * self.GB))
        self.assertEqual(self.page.delta_bar.values(), (400 * self.GB, 410 * self.GB))
        self.assertIn(i18n.t("scan.band.excluded", count=1), self.page.band.chip_texts())

    def test_a_short_list_does_not_leave_a_white_field(self):
        """줄이 셋이면 표도 세 줄 높이다 - 빈 흰 면이 화면 반을 차지하지 않는다."""

        self.page.resize(1000, 700)
        self.page.show_account(self.growth(), self.account)
        table = self.page.table
        self.assertLess(table.maximumHeight(), 400)

    def test_without_a_previous_scan_the_biggest_comes_first(self):
        root = self.account.path
        self.page.show_account(self.snapshot(top=[
            {"path": f"{root}/x", "size_kb": 10 * self.GB},
            {"path": f"{root}/y", "size_kb": 90 * self.GB},
        ]), self.account)
        self.assertTrue(self.page.table.isSortingEnabled())
        self.assertEqual(self.first_column(), ["y", "x"])


class WindowStartsWithoutTouchingTheDbTests(_GuiCase):
    """창을 여는 동안 화면 스레드는 DB 를 열지 않는다.

    실기(NFS)에서 창이 늦게 뜨고 버튼마다 굳었다. 화면 스레드가 표본 DB 를 읽어
    수집 신선도를 판정하고, 버튼마다 스캔 DB 에 사용 기록을 써서 커밋했기
    때문이다 - 그 순간 다른 작업이 DB 를 잡고 있으면 잠금 대기만큼 멈춘다."""

    def test_no_db_on_the_window_thread_and_collecting_waits_for_freshness(self):
        import threading
        import time

        from smvwp import sqlite_db
        from smvwp.gui.main_window import MainWindow

        opened = []
        real = sqlite_db.connect

        def recording(path, data_dir, initialize):
            if threading.current_thread() is threading.main_thread():
                opened.append(Path(path).name)
            return real(path, data_dir, initialize)

        from smvwp.gui.main_window import _FreshnessReader
        from smvwp.scheduler import ThreadWorker

        started = []
        # 이 시험만 신선도 작업자를 실제로 띄운다 (_GuiCase 는 배경 작업을 멈춰 둔다).
        with patch("smvwp.sqlite_db.connect", side_effect=recording), \
                patch.object(_FreshnessReader, "start", ThreadWorker.start), \
                patch("smvwp.scheduler.CollectorScheduler.start",
                      side_effect=lambda *a, **k: started.append(1)):
            window = MainWindow(self.data_dir, self.config)
            self.addCleanup(window.close)
            self.assertEqual(started, [], "신선도 판정 전에 수집을 시작했다")
            deadline = time.monotonic() + 5
            while not started and time.monotonic() < deadline:
                _app().processEvents()
                time.sleep(0.01)
        self.assertEqual(opened, [], "화면 스레드에서 DB 를 열었다")
        self.assertEqual(started, [1])


class LoadingProgressTests(_GuiCase):
    """데이터를 읽는 동안 무엇을 하는지 보여 준다 - 실기에서 15초 동안 아무 표시가
    없었다."""

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)
        self.window.show()
        _app().processEvents()

    def tearDown(self):
        self.window.close()
        super().tearDown()

    def test_home_shows_the_step_while_reading_and_hides_when_done(self):
        from smvwp import dashboard, i18n

        self.window._refresh_table_from_store()
        self.assertTrue(self.window.loading_row.isVisible())
        self.window._on_dashboard_progress(2, len(dashboard.STEPS), dashboard.STEPS[1])
        self.assertIn(i18n.t(dashboard.STEPS[1]), self.window.loading_label.text())
        self.assertIn(f"2/{len(dashboard.STEPS)}", self.window.loading_label.text())
        self.assertEqual(self.window.loading_bar.value(), 1)
        self.window._on_dashboard_ready(dashboard.DashboardData())
        self.assertFalse(self.window.loading_row.isVisible())

    def test_a_failed_read_also_hides_it(self):
        self.window._refresh_table_from_store()
        self.window._on_dashboard_failed("boom")
        self.assertFalse(self.window.loading_row.isVisible())

    def test_the_scan_tab_counts_accounts_on_its_first_read(self):
        tab = self.window._scan_tab
        tab._on_scan_status_progress(3, 12, "")
        self.assertFalse(tab.first_load_bar.isHidden())
        self.assertEqual(tab.first_load_bar.value(), 2)
        self.assertIn("3/12", tab.scan_status_label.text())


class OverviewPageTests(_GuiCase):
    """상세 스캔 탭의 '현황' - 실기: "날마다 스캔을 하는데 뭘 했는지 어디까지
    했는지를 모르겠다." 진짜 스캔 기록으로 채워 본다."""

    def setUp(self):
        super().setUp()
        from smvwp import nightly_scan, scan_store
        from smvwp.gui.main_window import MainWindow

        with scan_store.session(self.data_dir) as conn:
            for index, account in enumerate(self.config.accounts):
                tops = [f"{account.path}/d{n}" for n in range(3 + index)]
                scan_store.seed_checkpoints(conn, account.account_id, scan_store.BASELINE, 1, tops)
                for _ in tops:
                    checkpoint = scan_store.next_pending(conn, account.account_id, scan_store.BASELINE, 1)
                    scan_store.mark_done(conn, checkpoint["id"], size_kb=10)
                scan_store.mark_generation_completed(conn, account.account_id, 1)
        self.window = MainWindow(self.data_dir, self.config)
        self.addCleanup(self.window.close)
        self.tab = self.window._scan_tab
        self.seen = []
        self.tab.overview_changed.connect(self.seen.append)
        self.tab._on_scan_status_ready(nightly_scan.get_status_snapshot(self.data_dir, self.config))

    def test_it_fills_from_the_scan_records(self):
        page = self.tab.overview_page
        self.assertTrue(page.pass_percent.text().endswith("%"))
        self.assertIn("/", page.pass_counts.text())
        self.assertEqual(page.table.rowCount(), len(self.config.accounts))
        self.assertEqual(len(page.segments.segments()), len(self.config.accounts))
        self.assertTrue(page.pass_stats["finish"][1].text())

    def test_the_home_gets_the_same_numbers(self):
        self.assertEqual(len(self.seen), 1)
        self.assertIs(self.seen[0], self.tab.overview)

    def test_double_clicking_an_account_shows_its_growth(self):
        from smvwp.gui.overview_page import ACCOUNT_ROLE
        from smvwp.gui.scan_pages import TAB_GROWTH

        page = self.tab.overview_page
        page.table.cellDoubleClicked.emit(0, 0)
        self.assertEqual(self.tab.detail_tabs.currentIndex(), TAB_GROWTH)
        opened = page.table.item(0, 0).data(ACCOUNT_ROLE)
        self.assertEqual(self.tab.scan_account_combo.currentData(), opened)


class HomeOverviewTests(_GuiCase):
    """스캔 탭이 만든 상세 스캔 현황이 홈(타일 둘, 계정 표 칸, 오늘 밤 카드)에
    닿는가. 실기: "어디까지 스캔되고 스토리지가 어떤 상황인지 판단이 힘들다" -
    홈에서 그 답이 보여야 한다. 한 계정은 다시 재다 말았다."""

    def setUp(self):
        super().setUp()
        from smvwp import nightly_scan, scan_store
        from smvwp.gui.main_window import MainWindow

        with scan_store.session(self.data_dir) as conn:
            for account in self.config.accounts:
                tops = [f"{account.path}/d{n}" for n in range(4)]
                scan_store.seed_checkpoints(conn, account.account_id, scan_store.BASELINE, 1, tops)
                for _ in tops:
                    checkpoint = scan_store.next_pending(conn, account.account_id, scan_store.BASELINE, 1)
                    scan_store.mark_done(conn, checkpoint["id"], size_kb=10)
                scan_store.mark_generation_completed(conn, account.account_id, 1)
            self.halfway = self.config.accounts[0]
            tops = [f"{self.halfway.path}/d{n}" for n in range(4)]
            scan_store.seed_checkpoints(conn, self.halfway.account_id, scan_store.BASELINE, 2, tops)
            checkpoint = scan_store.next_pending(conn, self.halfway.account_id, scan_store.BASELINE, 2)
            scan_store.mark_done(conn, checkpoint["id"], size_kb=10)
        self.window = MainWindow(self.data_dir, self.config)
        self.addCleanup(self.window.close)
        self.window._scan_tab._on_scan_status_ready(
            nightly_scan.get_status_snapshot(self.data_dir, self.config)
        )
        _app().processEvents()
        self.overview = self.window._scan_tab.overview

    def test_the_scan_tiles_show_the_pass(self):
        tiles = self.window.tiles
        self.assertEqual(tiles["scan"].value.text(), f"{self.overview.percent}%")
        self.assertEqual(len(tiles["scan"].bar.segments()), len(self.config.accounts))
        self.assertTrue(tiles["finish"].value.text())
        self.assertTrue(tiles["finish"].detail.text())

    def test_each_account_row_says_where_its_detail_scan_is(self):
        from PyQt5.QtCore import Qt

        from smvwp.gui.main_window import COL_NAME, COL_SCAN
        from smvwp.gui.overview_widgets import Chip

        table = self.window.table
        self.window._render_table({})
        kinds = {}
        for row in range(table.rowCount()):
            account_id = table.item(row, COL_NAME).data(Qt.UserRole)
            chips = table.cellWidget(row, COL_SCAN).findChildren(Chip)
            kinds[account_id] = chips[0].kind if chips else None
        self.assertEqual(kinds[self.halfway.account_id], "doing")
        self.assertTrue(all(kinds.values()), kinds)

    def test_dark_mode_switches_live_and_leaves_no_light_color_behind(self):
        """도구 메뉴의 '어두운 화면' 은 창을 다시 열지 않고 바로 바뀐다.

        그릴 때 정한 색(표 글자색, 알약, 행 색, 타일 막대, 오늘 밤 번호)이 밝은
        쪽으로 남으면 어두운 화면에서 그 칸만 하얗게 뜬다. 창 안의 모든 위젯의
        스타일시트와 표 글자색에 밝은 화면에만 있는 값이 하나도 없어야 한다."""

        from datetime import datetime, timezone

        from PyQt5.QtGui import QColor
        from PyQt5.QtWidgets import QTableWidget, QWidget

        from smvwp import store
        from smvwp.gui import theme

        app = _app()

        def back_to_light():
            theme.use(theme.LIGHT)
            theme.restyle(app)

        self.addCleanup(back_to_light)
        total = 10 * 1024 ** 3
        samples = {}
        for index, account in enumerate(self.config.accounts):
            pct = (96.0, 91.0, 40.0)[index % 3]
            samples[account.account_id] = store.SampleRecord(
                account_id=account.account_id, collected_at=datetime.now(timezone.utc).isoformat(),
                ok=True, filesystem="nfs4", mount_point="/ifs", total_kb=total,
                used_kb=int(total * pct / 100), avail_kb=1, byte_pct=pct,
                overall_tier="alert" if pct > 95 else "warn" if pct > 90 else "normal",
            )
        self.window._latest_samples = samples
        self.window._render_table(samples)
        _app().processEvents()

        with patch.object(theme, "save_mode") as saved:
            self.window.dark_action.trigger()
        _app().processEvents()
        saved.assert_called_once_with(theme.DARK)
        self.assertTrue(theme.is_dark())
        self.assertTrue(self.window.dark_action.isChecked())
        self.assertIn(theme.DARK_TOKENS["BG"], app.styleSheet())

        light_only = {value.lower() for value in theme.palette_values(theme.LIGHT)} - {
            value.lower() for value in theme.palette_values(theme.DARK)
        }
        stale = []
        for widget in [self.window, *self.window.findChildren(QWidget)]:
            sheet = widget.styleSheet().lower()
            stale += [f"{type(widget).__name__}: {value}" for value in light_only if value in sheet]
        for table in self.window.findChildren(QTableWidget):
            for row in range(table.rowCount()):
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    if item is None or item.foreground().style() == 0:
                        continue
                    color = item.foreground().color().name().lower()
                    if color in light_only:
                        stale.append(f"{table.objectName() or 'table'}[{row},{column}]: {color}")
        self.assertEqual(stale, [])
        self.assertFalse(self.window.grab().isNull())

        with patch.object(theme, "save_mode"):
            self.window.dark_action.trigger()
        self.assertFalse(theme.is_dark())
        self.assertIn(theme.LIGHT_TOKENS["BG"], app.styleSheet())
        self.assertNotEqual(QColor(theme.BG), QColor(theme.DARK_TOKENS["BG"]))

    def test_tonight_lists_the_half_done_account_first(self):
        from PyQt5.QtWidgets import QLabel

        rows = [self.window.tonight_rows.itemAt(i).widget() for i in range(self.window.tonight_rows.count())]
        self.assertTrue(rows)
        names = [label.text() for label in rows[0].findChildren(QLabel)]
        self.assertIn(self.halfway.name, names)
        self.assertTrue(self.window.tonight_window.text())

    def test_switching_language_redraws_the_tiles(self):
        from smvwp import i18n

        self.window._change_language("en")
        self.addCleanup(i18n.set_language, "ko")
        self.assertEqual(self.window.tiles["scan"].caption.text(), i18n.t("home.tile.scan"))
        self.assertEqual(self.window.tiles["finish"].value.text() != "", True)


class HomePicturesTests(_GuiCase):
    """홈이 흰 바탕에 글자만 가득하지 않게 - 분포 막대, 24시간 칸, 할 일 카드, 용량
    변화 막대가 실제 값으로 채워지는가."""

    GB = 1024 * 1024

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)
        self.addCleanup(self.window.close)

    def sample(self, account, pct, ok=True):
        from datetime import datetime, timezone

        from smvwp import store, tiers

        return store.SampleRecord(
            account_id=account.account_id, collected_at=datetime.now(timezone.utc).isoformat(),
            ok=ok, total_kb=1000, used_kb=int(pct * 10), avail_kb=1000 - int(pct * 10),
            byte_pct=pct if ok else None, overall_tier=tiers.classify(pct) if ok else tiers.UNKNOWN,
        )

    def test_the_attention_tile_shows_how_accounts_split_by_tier(self):
        from smvwp import tiers

        proj, bak = self.config.accounts
        latest = {proj.account_id: self.sample(proj, 96.0), bak.account_id: self.sample(bak, 40.0)}
        self.window._latest_samples = latest
        self.window._render_table(latest)
        segments = self.window.tiles["attention"].bar.segments()
        self.assertEqual([segment[0] for segment in segments], [1, 1])
        self.assertIn(tiers.label(tiers.classify(96.0)), segments[0][4])

    def test_the_collection_cell_shows_the_gaps_instead_of_an_orange_just_now(self):
        """24시간 중 26% 만 모였을 때 '방금' 을 주황으로 칠했다 - 이제는 빈 시간이 칸으로
        보이고 글자는 보통 색이다. 멈췄을 때(오래됨)만 경고 색."""

        from types import SimpleNamespace

        from smvwp import freshness
        from smvwp.gui.main_window import COL_TIME
        from smvwp.gui.visuals import CollectStrip

        proj, bak = self.config.accounts
        self.window._freshness = {
            proj.account_id: SimpleNamespace(
                needs_attention=True, status=freshness.STATUS_GAPPY, age_seconds=10,
                coverage_pct=26, hourly=[0.0] * 18 + [1.0] * 6,
            ),
            bak.account_id: SimpleNamespace(
                needs_attention=True, status=freshness.STATUS_STALE, age_seconds=7200,
                coverage_pct=0, hourly=[1.0] * 20 + [0.0] * 4,
            ),
        }
        latest = {proj.account_id: self.sample(proj, 50.0), bak.account_id: self.sample(bak, 40.0)}
        self.window._render_table(latest)
        strips = {}
        for row in range(self.window.table.rowCount()):
            strip = self.window.table.cellWidget(row, COL_TIME)
            self.assertIsInstance(strip, CollectStrip)
            strips[self.window._account_id_at_row(row)] = strip
        self.assertFalse(strips[proj.account_id].alert)
        self.assertEqual(strips[proj.account_id].hours[:18], [0.0] * 18)
        self.assertTrue(strips[bak.account_id].alert)

    def test_each_thing_to_do_is_a_card_with_a_kind_and_a_number(self):
        from smvwp import i18n, priority
        from smvwp.gui.visuals import ActionDelegate

        made = priority.Plan()
        made.actions = [priority.Action(
            kind=priority.ACT_FULL, level=priority.LEVEL_CRITICAL,
            account="layout_proj", account_id="a1", pct=96.0,
        )]
        self.window._refresh_priority(made)
        card = self.window.priority_list.item(0).data(ActionDelegate.CARD_ROLE)
        self.assertEqual(card["metric"], "96%")
        self.assertEqual(card["title"], "layout_proj")
        self.assertEqual(card["detail"], i18n.t("priority.detail.full"))
        # 비울 양을 모르면 그 말을 뺀다 - "정리하면 - 빕니다" 는 고장으로 읽혔다.
        self.assertEqual(self.window.priority_summary.text(),
                         i18n.t("priority.summary_short", count=1, critical=1))

    def test_a_short_window_gives_the_room_to_the_things_to_do(self):
        """1366x768 에서 카드 셋이 세로를 나누면 할 일이 한 줄 반만 보였다 - 낮은 창에서는
        '용량 변화' 카드를 접는다 (같은 내용이 상세 스캔 탭에 있다)."""

        from smvwp import nightly_scan, scan_store
        from smvwp.gui.main_window import MINIMUM_SIZE
        from tests import support

        proj, bak = self.config.accounts
        with scan_store.session(self.data_dir) as conn:
            support.two_passes_and_one_running(conn, proj.account_id, proj.path)
            support.unmeasured_then_measured(conn, bak.account_id, bak.path)
        self.window._scan_tab._on_scan_status_ready(
            nightly_scan.get_status_snapshot(self.data_dir, self.config)
        )
        self.window.show()
        self.window.resize(*MINIMUM_SIZE)
        _app().processEvents()
        self.assertFalse(self.window.changes_card.isVisible())
        self.window.resize(1600, 1000)
        _app().processEvents()
        self.assertTrue(self.window.changes_card.isVisible())

    def test_size_changes_since_the_last_scan_are_bars_by_account(self):
        """지난 스캔 대비 증감이 큰 계정부터 - 두 바퀴 모두 잰 곳끼리다 (못 잰 폴더가
        나중에 재진 계정은 +910GB 가 아니라 +10GB). 줄을 누르면 그 계정의 용량 변화."""

        from smvwp import nightly_scan, scan_store
        from smvwp.gui.scan_pages import TAB_GROWTH
        from tests import support

        proj, bak = self.config.accounts
        with scan_store.session(self.data_dir) as conn:
            support.two_passes_and_one_running(conn, proj.account_id, proj.path)
            support.unmeasured_then_measured(conn, bak.account_id, bak.path)
        self.window._scan_tab._on_scan_status_ready(
            nightly_scan.get_status_snapshot(self.data_dir, self.config)
        )
        _app().processEvents()
        rows = self.window.changes_list.rows()
        self.assertEqual([(row[0], row[2]) for row in rows],
                         [(proj.account_id, 500 * self.GB), (bak.account_id, 10 * self.GB)])
        self.window.changes_list.chosen.emit(bak.account_id)
        self.assertEqual(self.window.tabs.currentIndex(), self.window.tabs.indexOf(self.window._scan_page))
        self.assertEqual(self.window._scan_tab.detail_tabs.currentIndex(), TAB_GROWTH)
        self.assertEqual(self.window._scan_tab.scan_account_combo.currentData(), bak.account_id)


class TrendColumnTests(_GuiCase):
    """용량 변화 표의 '추세' 칸 - 폴더의 장기 이력 (`tests/test_folder_history.py`)."""

    GB = 1024 * 1024

    def test_the_growth_table_draws_the_history_of_each_folder(self):
        from smvwp.gui.scan_pages import GROWTH_TREND, GrowthPage
        from smvwp.gui.widgets import CellDelegate
        from smvwp.nightly_scan import AccountScanSnapshot, StatusSnapshot

        page = GrowthPage()
        page.retranslate()
        self.addCleanup(page.close)
        account = config_module.Account(name="proj", path="/acct", account_id="a1")
        entry = AccountScanSnapshot(
            account_id="a1", account_name="proj", last_completed_generation=2, top_paths=[],
            growth=[{"path": "/acct/sim", "current_kb": 300 * self.GB, "previous_kb": 200 * self.GB},
                    {"path": "/acct/lib", "current_kb": 50 * self.GB, "previous_kb": 50 * self.GB}],
            pending_baseline_count=0, current_scan_at="2026-10-01T00:00:00+00:00",
            folder_trends={"/acct/sim": [("2025-11-01T03:00:00+00:00", 100 * self.GB),
                                         ("2026-05-01T03:00:00+00:00", 200 * self.GB),
                                         ("2026-10-01T03:00:00+00:00", 300 * self.GB)]},
        )
        page.show_account(StatusSnapshot(is_running=False, window_description="", latest_run=None,
                                         accounts=[entry]), account)
        cells = {page.table.item(r, 0).text(): page.table.item(r, GROWTH_TREND)
                 for r in range(page.table.rowCount())}
        self.assertEqual(cells["sim"].data(CellDelegate.SPARK_ROLE), [100 * self.GB, 200 * self.GB, 300 * self.GB])
        self.assertIn("2025-11-01", cells["sim"].toolTip())
        self.assertIsNone(cells["lib"].data(CellDelegate.SPARK_ROLE))


class StorageGroupTests(_GuiCase):
    """같은 스토리지를 쓰는 계정들은 스토리지 줄 하나 아래로 묶는다.

    `df` 는 계정이 아니라 파일시스템을 잰다 - 한 스토리지에 계정이 여럿이면 표에 똑같은
    96% 가 여러 줄 떴고, 어느 계정이 그 스토리지를 채웠는지는 어디에도 없었다. 묶으면
    사용률·추세·FULL 예상은 스토리지 줄에 한 번, 계정 줄에는 상세 스캔이 잰 그 계정의
    크기와 스토리지 안 비중이 선다. 묶는 기준은 '무엇부터 할까' 와 같다
    (`priority.storage_groups`)."""

    GB = 1024 * 1024
    TB = 1024 * GB

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)
        self.addCleanup(self.window.close)

    def samples(self, same=True):
        from datetime import datetime, timezone

        from smvwp import store

        made = {}
        for index, account in enumerate(self.config.accounts):
            pct = 96.0 if same else (96.0, 40.0)[index]
            total = 10 * self.TB
            made[account.account_id] = store.SampleRecord(
                account_id=account.account_id, collected_at=datetime.now(timezone.utc).isoformat(),
                ok=True, filesystem="nfs4:/vol/shared", mount_point="/ifs", total_kb=total,
                used_kb=int(total * pct / 100), avail_kb=1, byte_pct=pct,
                overall_tier="alert" if pct > 90 else "normal",
            )
        return made

    def rows(self):
        from PyQt5.QtCore import Qt

        from smvwp.gui.main_window import COL_NAME

        table = self.window.table
        return [(table.item(row, COL_NAME).text().strip(), table.item(row, COL_NAME).data(Qt.UserRole))
                for row in range(table.rowCount())]

    def test_accounts_on_one_storage_sit_under_one_storage_row(self):
        proj, bak = self.config.accounts
        self.window._render_table(self.samples())
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertIsNone(rows[0][1])          # 스토리지 줄 - 계정이 아니다
        self.assertIn("/ifs", rows[0][0])
        self.assertEqual({rows[1][1], rows[2][1]}, {proj.account_id, bak.account_id})

    def test_the_storage_numbers_are_shown_once(self):
        """사용률 막대·추세는 스토리지 줄에만 - 계정 줄에 같은 96% 를 되풀이하지 않는다."""

        from smvwp.gui import widgets
        from smvwp.gui.main_window import COL_BYTE

        self.window._render_table(self.samples())
        table = self.window.table
        self.assertIsInstance(table.cellWidget(0, COL_BYTE), widgets.UsageBar)
        for row in (1, 2):
            self.assertNotIsInstance(table.cellWidget(row, COL_BYTE), widgets.UsageBar)

    def test_different_storages_stay_one_row_each(self):
        self.window._render_table(self.samples(same=False))
        self.assertEqual(len(self.rows()), 2)
        self.assertTrue(all(account_id for _name, account_id in self.rows()))

    def test_an_account_row_shows_its_own_measured_size(self):
        """상세 스캔이 잰 그 계정의 크기와 스토리지 사용량 안의 비중."""

        from types import SimpleNamespace

        from smvwp import formatting
        from smvwp.gui.main_window import COL_SIZE

        proj, bak = self.config.accounts
        self.window._overview = SimpleNamespace(accounts=[
            SimpleNamespace(account_id=proj.account_id, measured_kb=int(7.2 * self.TB)),
            SimpleNamespace(account_id=bak.account_id, measured_kb=None),
        ])
        from PyQt5.QtWidgets import QWidget

        self.window._scan_cell = lambda _account_id: QWidget()   # 상세 스캔 칸은 따로 본다
        self.window._render_table(self.samples())
        from smvwp.gui.main_window import COL_BYTE

        cells, bars = {}, {}
        for row in range(1, 3):
            account_id = self.window._account_id_at_row(row)
            cells[account_id] = self.window.table.item(row, COL_SIZE)
            bars[account_id] = self.window.table.cellWidget(row, COL_BYTE)
        self.assertEqual(cells[proj.account_id].text(), formatting.format_kb(int(7.2 * self.TB)))
        self.assertEqual(bars[proj.account_id].text, "75%")   # 9.6 TB 가운데 7.2 TB
        self.assertEqual(cells[bak.account_id].text(), "-")
        self.assertIsNone(bars[bak.account_id])

    def test_double_clicking_the_storage_row_opens_nothing(self):
        from unittest.mock import patch

        self.window._render_table(self.samples())
        with patch("smvwp.gui.account_detail_dialog.AccountDetailDialog") as dialog:
            self.window._open_account_detail(0)
        dialog.assert_not_called()

    def test_the_tiles_still_count_accounts(self):
        """묶어도 '손볼 계정' 은 계정 수다 (스토리지 줄을 세지 않는다)."""

        self.window._render_table(self.samples())
        self.assertEqual(self.window.tiles["attention"].value.text(), "2")


class OwnersPageTests(_GuiCase):
    """사람별 쪽 - 가장 많이 쓰는 사람을 띠에 크게, 사람마다 크기 막대와 지난 스캔 대비."""

    GB = 1024 * 1024

    def test_owners_are_drawn_biggest_first_with_bars_and_change(self):
        from smvwp.gui.scan_pages import OWNER_DELTA, OWNER_NAME, OWNER_SIZE, OwnersPage
        from smvwp.gui.widgets import CellDelegate

        page = OwnersPage()
        page.retranslate()
        self.addCleanup(page.close)
        page.show_owners([
            {"uid": 1002, "name": "lee", "size_kb": 100 * self.GB, "file_count": 5, "previous_kb": None},
            {"uid": 1001, "name": "kim", "size_kb": 600 * self.GB, "file_count": 3,
             "previous_kb": 400 * self.GB},
        ], "proj", 1000 * self.GB)
        table = page.table
        self.assertEqual([table.item(r, OWNER_NAME).text() for r in range(2)], ["kim", "lee"])
        self.assertAlmostEqual(table.item(0, OWNER_SIZE).data(CellDelegate.BAR_ROLE)[0], 0.6)
        self.assertEqual(table.item(0, OWNER_DELTA).data(CellDelegate.VALUE_ROLE), 200 * self.GB)
        self.assertEqual(page.band.value.text(), "kim")

    def test_no_owners_says_why(self):
        from smvwp import i18n
        from smvwp.gui.scan_pages import OwnersPage

        page = OwnersPage()
        self.addCleanup(page.close)
        page.show_owners([], "proj", None)
        self.assertEqual(page.table.rowCount(), 0)
        self.assertEqual(page.caption.text(), i18n.t("owners.none", account="proj"))

    def test_the_scan_tab_and_the_detail_window_both_have_it(self):
        from smvwp.gui.main_window import MainWindow
        from smvwp.gui.scan_pages import TAB_KEYS, TAB_OWNERS

        window = MainWindow(self.data_dir, self.config)
        self.addCleanup(window.close)
        self.assertEqual(TAB_KEYS[TAB_OWNERS], "scan.tab.owners")
        self.assertIs(window._scan_tab.detail_stack.widget(TAB_OWNERS), window._scan_tab.owners_page)


class HomeHeaderTests(_GuiCase):
    """머리줄의 '마지막 수집' - 수집이 멈췄으면 그 경고가 그 자리에 먼저 온다."""

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)
        self.addCleanup(self.window.close)

    def test_a_stalled_collection_takes_the_place_of_the_time(self):
        from types import SimpleNamespace

        from smvwp import freshness

        self.window._freshness = {
            account.account_id: SimpleNamespace(
                needs_attention=True, status=freshness.STATUS_STALE, age_seconds=7200, coverage_pct=0,
            )
            for account in self.config.accounts
        }
        self.window._update_collected_label()
        warning = self.window._freshness_warning()
        self.assertTrue(warning)
        self.assertEqual(self.window.collected_label.text(), warning)

    def test_a_long_warning_is_shortened_with_the_whole_in_the_tooltip(self):
        """머리줄 단추를 밀어내거나 중간에서 잘려 뜻이 끊기지 않게."""

        from smvwp.gui.widgets import ElidedLabel

        label = ElidedLabel()
        self.addCleanup(label.close)
        label.resize(80, 20)
        label.show()   # 숨은 위젯은 보일 때에야 크기 바뀜을 받는다 - 창에서는 늘 보인다
        long_text = "계정 12개는 최근 24시간 중 26%만 수집됐습니다. cron 이 돌지 않을 수 있습니다"
        label.setText(long_text)
        self.assertEqual(label.text(), long_text)
        self.assertNotEqual(QLabel_text(label), long_text)
        self.assertEqual(label.toolTip(), long_text)
        label.resize(2000, 20)
        _app().processEvents()
        self.assertEqual(QLabel_text(label), long_text)
        self.assertEqual(label.toolTip(), "")


class CountingLabelTests(unittest.TestCase):
    """타일의 큰 숫자는 바뀔 때 잠깐 세어 간다 - 방금 새 값을 읽었다는 표시.

    세는 동안의 글자는 그림일 뿐이다. `text()` 는 늘 마지막에 받은 값이어야 한다 -
    화면 코드와 시험이 그 값을 읽는다."""

    def setUp(self):
        _app()
        from smvwp.gui.widgets import CountingLabel

        self.label = CountingLabel("-")
        self.addCleanup(self.label.close)
        self.label.show()

    def pump(self, seconds):
        import time

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            _app().processEvents()
            time.sleep(0.01)

    def test_the_value_is_final_at_once_and_the_display_counts_to_it(self):
        self.label.setText("96.0%")
        self.assertEqual(self.label.text(), "96.0%")
        self.pump(0.12)
        shown = QLabel_text(self.label)
        self.assertRegex(shown, r"^\d+\.\d%$")
        self.assertNotEqual(shown, "96.0%", "세는 중이어야 합니다")
        self.pump(self.label.DURATION_MS / 1000 + 0.3)
        self.assertEqual(QLabel_text(self.label), "96.0%")

    def test_it_counts_from_the_last_number_not_from_zero(self):
        self.label.setText("40")
        self.pump(self.label.DURATION_MS / 1000 + 0.3)
        self.label.setText("44")
        self.pump(0.05)
        self.assertGreaterEqual(int(QLabel_text(self.label)), 40)

    def test_words_change_at_once(self):
        """`11/11(수) 새벽` 같은 글은 셀 것이 없다."""

        self.label.setText("11/11(수) 새벽")
        self.assertEqual(QLabel_text(self.label), "11/11(수) 새벽")
        self.label.setText("-")
        self.assertEqual(QLabel_text(self.label), "-")

    def test_a_hidden_label_does_not_count(self):
        self.label.hide()
        self.label.setText("67%")
        self.assertEqual(QLabel_text(self.label), "67%")

    def test_the_width_does_not_wobble_while_counting(self):
        """세는 동안 글자 폭이 바뀌면 옆의 알약이 따라 흔들린다."""

        self.label.setText("96.0%")
        self.pump(0.05)
        self.assertGreaterEqual(self.label.minimumWidth(),
                                self.label.fontMetrics().horizontalAdvance("96.0%"))
        self.pump(self.label.DURATION_MS / 1000 + 0.3)
        self.assertEqual(self.label.minimumWidth(), 0)


def QLabel_text(label):  # noqa: N802 - 화면에 실제로 그리는 글 (ElidedLabel.text 가 아니라)
    from PyQt5.QtWidgets import QLabel

    return QLabel.text(label)


class DefaultSizeTests(unittest.TestCase):
    """처음 창 크기 - 시안 폭(1600)이 들어가면 그대로, 화면이 작으면 화면에 맞춘다."""

    def screen(self, width, height):
        from types import SimpleNamespace

        from PyQt5.QtCore import QRect

        return SimpleNamespace(availableGeometry=lambda: QRect(0, 0, width, height))

    def test_a_full_hd_screen_gets_the_designed_size(self):
        from smvwp.gui.main_window import DEFAULT_SIZE, default_size

        self.assertEqual(default_size(self.screen(1920, 1040)), DEFAULT_SIZE)

    def test_a_small_screen_gets_what_fits_but_not_below_the_minimum(self):
        from smvwp.gui.main_window import MINIMUM_SIZE, default_size

        self.assertEqual(default_size(self.screen(1366, 728)), (1326, MINIMUM_SIZE[1]))

    def test_no_screen_is_the_designed_size(self):
        from smvwp.gui.main_window import DEFAULT_SIZE, default_size

        self.assertEqual(default_size(None), DEFAULT_SIZE)


class CollectedElsewhereTests(_GuiCase):
    """창을 열면 홈 읽기와 첫 수집이 함께 시작된다. cron 이 방금 수집했으면 창의
    수집은 건너뛰는데, 그래도 늘 홈을 새로 읽어 창을 열 때마다 홈을 두 번 읽었다.
    남이 수집한 표본을 홈이 이미 보여 주고 있으면 다시 읽지 않는다."""

    SHOWN = "2026-10-16T03:00:00+00:00"

    def setUp(self):
        super().setUp()
        from smvwp.gui.main_window import MainWindow

        self.window = MainWindow(self.data_dir, self.config)
        self.addCleanup(self.window.close)
        worker = self.window._dashboard_worker
        self.reads = patch.object(worker, "refresh_async").start()
        self.running = patch.object(worker, "is_running", return_value=False).start()
        self.addCleanup(patch.stopall)

    def ready(self, newest):
        from smvwp import dashboard

        self.window._on_dashboard_ready(dashboard.DashboardData(newest=newest))

    def skipped(self, newest):
        with patch.object(self.window._scheduler, "newest_elsewhere", return_value=newest):
            self.window._on_collection_finished(None)

    def test_what_the_home_already_shows_is_not_read_again(self):
        self.ready(self.SHOWN)
        self.reads.reset_mock()
        self.skipped(self.SHOWN)
        self.reads.assert_not_called()

    def test_a_newer_sample_from_elsewhere_is_read(self):
        self.ready(self.SHOWN)
        self.reads.reset_mock()
        self.skipped("2026-10-16T03:15:00+00:00")
        self.reads.assert_called_once()

    def test_while_the_home_is_still_reading_it_decides_afterwards(self):
        """창을 열 때는 대개 홈 읽기가 아직 돌고 있다. 그 읽기가 남의 표본을 담았으면
        끝, 못 담았으면(그보다 먼저 읽었으면) 한 번 더."""

        self.running.return_value = True
        self.reads.reset_mock()
        self.skipped(self.SHOWN)
        self.reads.assert_not_called()
        self.running.return_value = False
        self.ready(self.SHOWN)
        self.reads.assert_not_called()

        self.running.return_value = True
        self.skipped("2026-10-16T03:15:00+00:00")
        self.running.return_value = False
        self.ready(self.SHOWN)
        self.reads.assert_called_once()

    def test_its_own_collection_is_always_shown(self):
        self.ready(self.SHOWN)
        self.reads.reset_mock()
        self.window._on_collection_finished([])
        self.reads.assert_called_once()


class DataFolderDialogTests(_GuiCase):
    """데이터 폴더 창 - 무엇이 얼마나 언제부터 쌓였나, 읽는 동안 무엇을 하나."""

    def setUp(self):
        super().setUp()
        from smvwp.gui.data_dialog import DataFolderDialog

        self.dialog = DataFolderDialog(self.data_dir, self.config)
        self.dialog.show()
        _app().processEvents()

    def tearDown(self):
        self.dialog.close()
        super().tearDown()

    def test_progress_while_reading(self):
        from smvwp import data_inventory

        self.dialog._on_progress(3, len(data_inventory.STEPS), data_inventory.STEPS[2])
        self.assertIn(f"3/{len(data_inventory.STEPS)}", self.dialog.loading_label.text())
        self.assertFalse(self.dialog.loading_row.isHidden())

    def test_every_item_becomes_a_row_and_the_scan_db_opens_up(self):
        from smvwp import data_inventory, i18n, scan_store

        with scan_store.session(self.data_dir) as conn:
            scan_store.start_run(conn, "run-1", "cron")
        inventory = data_inventory.build(self.data_dir, self.config)
        self.dialog._on_ready(inventory)

        tree = self.dialog.tree
        self.assertEqual(tree.topLevelItemCount(), len(inventory.items))
        scan_row = next(
            tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).text(0) == i18n.t("data.item.scan")
        )
        self.assertEqual(scan_row.childCount(), 7)
        self.assertTrue(self.dialog.loading_row.isHidden())
        # 크기는 막대로도 - 무엇이 데이터 폴더를 먹는지 읽지 않고 보이게.
        from smvwp.gui.data_dialog import COL_SIZE
        from smvwp.gui.widgets import CellDelegate

        bars = [tree.topLevelItem(i).data(COL_SIZE, CellDelegate.BAR_ROLE)
                for i in range(tree.topLevelItemCount())]
        sized = [bar for bar in bars if bar]
        self.assertTrue(sized)
        self.assertAlmostEqual(max(bar[0] for bar in sized), 1.0)
        self.assertTrue(self.dialog.summary_label.text())

    def test_the_main_window_has_a_way_to_it(self):
        from smvwp.gui.main_window import MainWindow

        window = MainWindow(self.data_dir, self.config)
        self.addCleanup(window.close)
        self.assertTrue(window.data_action.text())
        self.assertIn(window.data_action, window.tools_btn.menu().actions())


class AccountDialogScanTimesTests(_GuiCase):
    """계정 관리 창은 마지막 스캔 시각을 작업 스레드에서 읽는다.

    화면 스레드에서 읽으면 야간 스캔이 스캔 DB 에 쓰는 동안(NFS 위에서는 잠금
    대기가 최대 10초) 창이 그만큼 굳는다. 목록은 바로 그리고, 그 칸만 나중에 찬다."""

    def test_scan_times_are_read_off_the_window_thread_and_filled_in(self):
        import threading
        import time

        from smvwp import scan_store
        from smvwp.gui.account_dialog import ACCOUNT_COL_SCANNED, AccountDialog

        account = self.config.accounts[0]
        with scan_store.session(self.data_dir) as conn:
            scan_store.mark_generation_completed(
                conn, account.account_id, 1, completed_at="2026-10-08T13:00:00+00:00"
            )
        readers = []
        real = scan_store.last_baseline_times

        def recording(conn):
            readers.append(threading.current_thread() is threading.main_thread())
            return real(conn)

        with patch("smvwp.scan_store.last_baseline_times", side_effect=recording):
            dialog = AccountDialog(self.data_dir, self.config)
            self.addCleanup(dialog.close)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and dialog.account_table.item(
                0, ACCOUNT_COL_SCANNED
            ).text() in ("", "-"):
                _app().processEvents()
                time.sleep(0.01)

        self.assertEqual(readers, [False], "화면 스레드에서 스캔 DB 를 읽었다")
        self.assertIn("2026", dialog.account_table.item(0, ACCOUNT_COL_SCANNED).text())


class ShutdownStopsOnlyItsOwnScanTests(_GuiCase):
    """창을 닫으면 **이 창이 시작한 스캔만** 멈춘다.

    예전에는 잠금을 쥔 실행이면 누구 것이든 멈추고, '가장 최근 실행' 을 stopped 로
    바꾸고, 그 잠금을 풀었다. 창의 작업 스레드가 후처리(보고서)를 하는 사이 cron 이
    잠금을 잡았다면, 그때 창을 닫는 것만으로 그 밤의 cron 스캔이 멈췄다."""

    def hold_lock(self, run_id, pid, triggered_by):
        import json

        from smvwp import scan_lock, scan_store

        scan_lock.lock_file(self.data_dir).write_text(json.dumps({
            "run_id": run_id, "pid": pid, "triggered_by": triggered_by,
            "started_at": "2026-10-05T13:00:00+00:00",
        }), encoding="utf-8")
        with scan_store.session(self.data_dir) as conn:
            scan_store.start_run(conn, run_id, triggered_by)

    def close_window_scan(self):
        from smvwp.gui.scan_tab import ScanTab

        tab = ScanTab(self.data_dir, lambda: self.config)
        self.addCleanup(tab.deleteLater)
        # Windows 에서 os.kill(pid, 0) 은 확인이 아니라 그 프로세스를 끝낸다.
        with patch("smvwp.scan_lock._pid_alive", return_value=True), \
                patch("smvwp.procio.live_child_pids", return_value=[]):
            tab._stop_scan_for_shutdown()

    def latest_status(self):
        from smvwp import scan_store

        with scan_store.session(self.data_dir) as conn:
            return scan_store.latest_run(conn)["status"]

    def test_a_cron_scan_keeps_running(self):
        import os

        from smvwp import scan_lock

        self.hold_lock("cron-run", os.getpid() + 1, "cron")
        self.close_window_scan()
        self.assertFalse(scan_lock.is_stop_requested(self.data_dir, "cron-run"))
        self.assertEqual(self.latest_status(), "running")
        self.assertIsNotNone(scan_lock.read_lock(self.data_dir))

    def test_its_own_scan_stops(self):
        import os

        from smvwp import scan_lock

        self.hold_lock("gui-run", os.getpid(), "gui")
        self.close_window_scan()
        self.assertTrue(scan_lock.is_stop_requested(self.data_dir, "gui-run"))
        self.assertEqual(self.latest_status(), "stopped")
        self.assertIsNone(scan_lock.read_lock(self.data_dir))


class SearchPinTests(_GuiCase):
    """검색 창은 열 때마다 PIN 을 묻고, 틀리면 열리지 않는다."""

    ASK = "smvwp.gui.search_dialog.QInputDialog.getText"
    WARN = "smvwp.gui.search_dialog.QMessageBox.warning"

    def dialog(self):
        from smvwp.gui.search_dialog import SearchDialog

        dialog = SearchDialog(self.data_dir, self.config)
        self.addCleanup(dialog.close)
        return dialog

    def test_default_pin_opens_when_none_is_set(self):
        from smvwp import admin_auth

        with patch(self.ASK, return_value=(admin_auth.DEFAULT_PIN, True)):
            self.assertTrue(self.dialog()._prompt_for_pin())

    def test_wrong_pin_warns_and_stays_shut(self):
        from PyQt5.QtWidgets import QDialog

        with patch(self.ASK, return_value=("0000", True)), patch(self.WARN) as warned:
            self.assertEqual(self.dialog().exec_(), QDialog.Rejected)
        warned.assert_called_once()

    def test_changed_pin_replaces_the_default(self):
        from smvwp import admin_auth

        self.config.settings.admin_pin_hash = admin_auth.hash_pin("9182")
        dialog = self.dialog()
        with patch(self.ASK, return_value=(admin_auth.DEFAULT_PIN, True)), patch(self.WARN):
            self.assertFalse(dialog._prompt_for_pin())
        with patch(self.ASK, return_value=("9182", True)):
            self.assertTrue(dialog._prompt_for_pin())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
