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

        self.assertLessEqual(self.view.height(), 42)


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
