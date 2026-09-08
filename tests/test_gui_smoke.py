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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
