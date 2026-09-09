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


@unittest.skipUnless(HAVE_QT, "PyQt5 없음")
class _GuiCase(unittest.TestCase):
    def setUp(self):
        _app()
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
        table = self.tab.large_table
        self.assertEqual(table.rowCount(), 2)
        self.assertIn("huge", table.item(0, 0).text())

    def test_a_dominant_file_is_marked(self):
        """계정의 절반을 차지하는 파일이 눈에 안 띄면 표의 뜻이 없다."""

        from smvwp import tiers
        from PyQt5.QtGui import QColor
        from smvwp.gui.scan_tab import LARGE_PATH

        self._snapshot_with([("/a/huge", 500_000, 500_000)], measured_kb=1_000_000)
        item = self.tab.large_table.item(0, LARGE_PATH)
        self.assertEqual(
            item.foreground().color().name(), QColor(tiers.color(tiers.WARN)).name()
        )
        self.assertTrue(item.toolTip(), "왜 눈에 띄는지 설명이 있어야 한다")

    def test_an_ordinary_file_is_not_marked(self):
        """전부 칠하면 아무것도 강조되지 않는다."""

        from smvwp.gui.scan_tab import LARGE_PATH

        self._snapshot_with([("/a/ok", 1_000, 1_000)], measured_kb=10_000_000)
        item = self.tab.large_table.item(0, LARGE_PATH)
        self.assertFalse(item.toolTip())

    def test_a_file_missing_from_the_previous_list_says_so(self):
        from smvwp import i18n
        from smvwp.gui.scan_tab import LARGE_CHANGE

        i18n.set_language("ko")
        self._snapshot_with([("/a/new", 500_000, None)], measured_kb=10_000_000)
        self.assertEqual(
            self.tab.large_table.item(0, LARGE_CHANGE).text(), i18n.t("large.new")
        )

    def test_no_files_gives_a_plain_message_not_an_empty_table(self):
        self._snapshot_with([], measured_kb=1_000_000)
        self.assertEqual(self.tab.large_table.rowCount(), 0)
        self.assertTrue(self.tab.large_caption.text())

    def test_unknown_account_total_does_not_show_a_fake_share(self):
        """총량을 모를 때 0% 로 쓰면 '작다'고 잘못 읽힌다."""

        from smvwp import i18n
        from smvwp.gui.scan_tab import LARGE_SHARE

        i18n.set_language("ko")
        self._snapshot_with([("/a/x", 500_000, None)], measured_kb=None)
        self.assertEqual(
            self.tab.large_table.item(0, LARGE_SHARE).text(), i18n.t("common.none")
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
