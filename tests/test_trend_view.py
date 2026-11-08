"""추세선 위젯을 실제로 그려 본다 (화면 없이).

줄이는 계산은 `test_trend.py` 가 본다. 여기서 보는 것은 **그리는 쪽에서만
드러나는 것**이다 - 실제로 칠해지는가, 빈 이력에서 죽지 않는가, 구멍이 있다는
사실이 사람에게 전달되는가.
"""

import os
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5.QtGui import QPixmap
    from PyQt5.QtWidgets import QApplication
    HAVE_QT = True
except ImportError:  # pragma: no cover - PyQt5 없는 환경
    HAVE_QT = False

from smvwp import tiers, trend

START = datetime(2026, 6, 1, tzinfo=timezone.utc)
_APP = None


def _app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
        from smvwp.gui import theme

        theme.apply(_APP)
    return _APP


@dataclass
class Sample:
    collected_at: str
    byte_pct: Optional[float] = None


def at(hours, pct):
    return Sample((START + timedelta(hours=hours)).isoformat(), pct)


def rising():
    return trend.build([at(index, 50.0 + index) for index in range(30)], buckets=20)


def gapped():
    return trend.build([at(0, 40.0), at(1, 42.0), at(80, 90.0)], buckets=20)


def level(pct):
    """한 수준에 머문 이력 - 칸마다 값이 있어 끊기지 않는 선이 된다."""

    return trend.build([at(index, pct) for index in range(20)], buckets=20)


def SPARK_HEIGHT():  # noqa: N802 - 상수처럼 읽히게
    from smvwp.gui.trend_view import SPARK_HEIGHT as height

    return height


@unittest.skipUnless(HAVE_QT, "PyQt5 없음")
class SparklineTests(unittest.TestCase):
    def setUp(self):
        _app()
        from smvwp.gui.trend_view import Sparkline

        self.view = Sparkline()
        self.addCleanup(self.view.deleteLater)

    def paint(self):
        pixmap = QPixmap(self.view.size())
        self.view.render(pixmap)
        return pixmap

    def test_it_paints_with_no_history(self):
        """이력이 없는 계정에서 죽으면 표 전체가 안 뜬다."""

        self.view.set_series(None)
        self.assertFalse(self.paint().isNull())

    def test_it_paints_an_empty_series(self):
        self.view.set_series(trend.build([]))
        self.assertFalse(self.paint().isNull())

    def test_it_paints_data(self):
        self.view.set_series(rising(), tiers.WARN)
        self.assertFalse(self.paint().isNull())

    def test_it_paints_a_gapped_series(self):
        self.view.set_series(gapped(), tiers.ALERT)
        self.assertFalse(self.paint().isNull())

    def test_a_single_point_does_not_raise(self):
        """점 하나는 선이 안 된다 - 그래도 그려져야 한다."""

        self.view.set_series(trend.build([at(0, 50.0)]))
        self.assertFalse(self.paint().isNull())

    # -- 툴팁 -----------------------------------------------------------
    def test_the_tooltip_gives_the_numbers_the_line_cannot(self):
        self.view.set_series(rising(), tiers.WARN)
        tip = self.view.toolTip()
        self.assertIn("%", tip)

    def test_a_gap_is_said_out_loud(self):
        """구멍을 숨기면 평평한 선이 '안정적' 으로 읽힌다."""

        from smvwp import i18n

        self.view.set_series(gapped())
        self.assertIn(i18n.t("trend.gaps", count=gapped().gaps), self.view.toolTip())

    def test_no_history_says_so(self):
        from smvwp import i18n

        self.view.set_series(None)
        self.assertEqual(self.view.toolTip(), i18n.t("trend.no_data"))

    def test_it_fits_in_a_table_row(self):
        """행 높이를 넘으면 표가 통째로 늘어난다."""

        self.assertLessEqual(self.view.sizeHint().height(), 42)
        self.assertLessEqual(self.view.minimumSizeHint().height(), 42)

    # -- 칸 안에서의 자리 -------------------------------------------------
    CELL = (120, 44)

    def painted_rows(self, series, tier=tiers.NORMAL):
        """칸 크기로 늘인 추세선을 그려, 무언가 칠해진 줄 번호들을 준다."""

        from PyQt5.QtCore import Qt

        self.view.set_series(series, tier)
        self.view.resize(*self.CELL)
        pixmap = QPixmap(self.view.size())
        pixmap.fill(Qt.white)
        self.view.render(pixmap)
        image = pixmap.toImage()
        white = image.pixelColor(0, 0)
        return image, sorted({
            y for y in range(image.height()) for x in range(image.width())
            if image.pixelColor(x, y) != white
        })

    def test_the_cell_stretches_it_to_the_row(self):
        """표는 칸 위젯을 칸 크기로 늘인다. 크기를 못박아 두면 칸 왼쪽 위에
        붙어, 사용률이 높은 계정의 선이 **행 윗선처럼** 보인다."""

        self.view.resize(*self.CELL)
        self.assertEqual((self.view.width(), self.view.height()), self.CELL)

    def test_a_full_line_sits_in_the_middle_band_not_on_the_top_edge(self):
        _image, rows = self.painted_rows(level(100.0))
        band_top = (self.CELL[1] - SPARK_HEIGHT()) // 2
        self.assertTrue(rows)
        self.assertGreaterEqual(min(rows), band_top - 2, rows)
        self.assertLessEqual(max(rows), band_top + SPARK_HEIGHT() + 2, rows)

    def test_the_level_is_filled_below_the_line(self):
        """0~100 축이라 선만 있으면 90% 와 95% 가 모두 '위쪽 어딘가의 평평한 선' 이다.
        선 아래를 옅게 채우면 **얼마나 찼는지** 가 면적으로 읽힌다."""

        image, _rows = self.painted_rows(level(50.0))
        # 실제로 그린 크기에서 띠를 잡는다 - 칸 크기로 못 늘어난 위젯이면 띠가 곧 위젯이다.
        band_bottom = (image.height() + SPARK_HEIGHT()) // 2
        below = image.pixelColor(image.width() // 2, band_bottom - 3)
        self.assertTrue(below.isValid())
        self.assertNotEqual(below, image.pixelColor(0, 0))


@unittest.skipUnless(HAVE_QT, "PyQt5 없음")
class TrendChartTests(unittest.TestCase):
    def setUp(self):
        _app()
        from smvwp.gui.trend_view import TrendChart

        self.view = TrendChart()
        self.view.resize(700, 300)
        self.addCleanup(self.view.deleteLater)

    def paint(self):
        pixmap = QPixmap(self.view.size())
        self.view.render(pixmap)
        return pixmap

    def test_it_paints_with_no_data(self):
        self.view.set_series(trend.build([]))
        self.assertFalse(self.paint().isNull())

    def test_it_paints_data(self):
        self.view.set_series(rising(), tiers.WARN)
        self.assertFalse(self.paint().isNull())

    def test_it_paints_a_gapped_series(self):
        self.view.set_series(gapped(), tiers.ALERT)
        self.assertFalse(self.paint().isNull())

    def test_a_tiny_widget_does_not_raise(self):
        """창을 줄이다 보면 실제로 이만해진다."""

        self.view.set_series(rising(), tiers.WARN)
        self.view.resize(10, 10)
        self.assertIsNotNone(self.paint())

    def test_the_axis_is_fixed_not_stretched_to_the_data(self):
        """데이터에 맞춰 늘이면 78%에서 80%로 간 것이 화면을 꽉 채운다.

        같은 폭에서 값이 다르면 선의 높이도 달라야 한다 - 축이 데이터를 따라
        늘어나면 둘이 같은 자리에 그려진다."""

        from smvwp.gui import trend_view

        plot_top, plot_height = 0.0, 100.0
        low = trend_view._y_for(10.0, plot_top, plot_height)
        high = trend_view._y_for(90.0, plot_top, plot_height)
        self.assertAlmostEqual(low, 90.0)
        self.assertAlmostEqual(high, 10.0)

    def test_values_outside_the_range_are_clamped(self):
        """100%를 넘는 값이 오면 축 밖으로 그려 나간다."""

        from smvwp.gui import trend_view

        self.assertAlmostEqual(trend_view._y_for(140.0, 0.0, 100.0), 0.0)
        self.assertAlmostEqual(trend_view._y_for(-5.0, 0.0, 100.0), 100.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
