"""추세를 그리는 위젯 둘 - 칸에 들어가는 작은 것과, 창에 들어가는 큰 것.

## 왜 선이 끊기는가

수집기가 멈췄던 자리는 값이 없다. 거기를 이어 그으면 **없는 데이터를 있는
것처럼** 만든다 - 그 직선 위의 어떤 지점을 짚어도 우리는 그 값을 모른다.
`trend.segments` 가 끊기지 않는 구간을 주고, 여기서는 구간마다 따로 긋는다.

## 왜 축을 0~100 으로 고정하는가

사용률 그래프의 세로축을 데이터에 맞춰 늘이면 **78%에서 80%로 간 것이 화면을
꽉 채운다.** 보는 사람은 큰일이 난 줄 안다. 퍼센트는 기준이 정해진 값이므로
축도 그 기준을 따라야 한다.

경고선(90/95%)을 같이 긋는 것도 그래서다. 선 하나가 "이게 어느 정도인가"를
숫자보다 빨리 말한다.
"""

from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QWidget

from .. import formatting, i18n, tiers, trend
from . import theme

# 작은 그래프의 기본 크기. 표 칸에 들어가는 것이라 행 높이를 넘지 않는다.
SPARK_WIDTH = 110
SPARK_HEIGHT = 26

# 큰 그래프의 안쪽 여백 (왼쪽은 축 글자 자리).
CHART_LEFT = 44
CHART_RIGHT = 12
CHART_TOP = 10
CHART_BOTTOM = 26


def _y_for(value: float, top: float, height: float) -> float:
    """0~100% 를 화면 세로 좌표로. 위가 100% 다."""

    ratio = max(0.0, min(100.0, value)) / 100.0
    return top + height * (1.0 - ratio)


class Sparkline(QWidget):
    """표 칸에 들어가는 작은 추세선. 축도 눈금도 없다.

    여기서 말하려는 것은 **모양 하나**다 - 오르고 있나, 평평한가, 들쭉날쭉한가.
    정확한 값은 옆 칸의 숫자가 말한다.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._series: Optional[trend.Series] = None
        self._tier = tiers.UNKNOWN
        self.setFixedSize(SPARK_WIDTH, SPARK_HEIGHT)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 규약
        return QSize(SPARK_WIDTH, SPARK_HEIGHT)

    def set_series(self, series, tier: str = tiers.UNKNOWN) -> None:
        self._series = series
        self._tier = tier or tiers.UNKNOWN
        self.setToolTip(self._tooltip())
        self.update()

    def _tooltip(self) -> str:
        series = self._series
        if series is None or series.empty:
            return i18n.t("trend.no_data")
        lines = [
            i18n.t(
                "trend.range",
                low=f"{series.low:.1f}", high=f"{series.high:.1f}",
            )
        ]
        if series.change is not None:
            lines.append(i18n.t("trend.change", delta=f"{series.change:+.1f}"))
        if series.gaps:
            # 구멍이 있다는 사실을 숨기면 평평한 선이 "안정적"으로 읽힌다.
            lines.append(i18n.t("trend.gaps", count=series.gaps))
        return "\n".join(lines)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        series = self._series
        if series is None or series.empty:
            painter.setPen(QColor(theme.TEXT_FAINT))
            painter.drawText(self.rect(), Qt.AlignCenter, "-")
            painter.end()
            return

        width = self.width()
        height = self.height() - 4
        top = 2.0
        count = max(1, len(series.points) - 1)

        pen = QPen(QColor(tiers.color(self._tier)))
        pen.setWidth(2)
        painter.setPen(pen)
        for run in trend.segments(series):
            if len(run) < 2:
                # 점 하나짜리 구간은 선이 안 된다. 그래도 있었다는 것은
                # 보여야 하므로 점으로 찍는다.
                index = run[0]
                x = width * index / count
                y = _y_for(series.points[index].value, top, height)
                painter.drawPoint(QPointF(x, y))
                continue
            polygon = QPolygonF([
                QPointF(width * index / count,
                        _y_for(series.points[index].value, top, height))
                for index in run
            ])
            painter.drawPolyline(polygon)
        painter.end()


class TrendChart(QWidget):
    """창에 들어가는 큰 추세선. 축과 경고선이 있다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._series: Optional[trend.Series] = None
        self._tier = tiers.UNKNOWN
        self.setMinimumHeight(220)

    def set_series(self, series, tier: str = tiers.UNKNOWN) -> None:
        self._series = series
        self._tier = tier or tiers.UNKNOWN
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 규약
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(theme.SURFACE))

        plot = QRectF(
            CHART_LEFT, CHART_TOP,
            max(1, self.width() - CHART_LEFT - CHART_RIGHT),
            max(1, self.height() - CHART_TOP - CHART_BOTTOM),
        )
        self._draw_grid(painter, plot)

        series = self._series
        if series is None or series.empty:
            painter.setPen(QColor(theme.TEXT_FAINT))
            painter.drawText(plot, Qt.AlignCenter, i18n.t("trend.no_data"))
            painter.end()
            return

        self._draw_line(painter, plot, series)
        self._draw_dates(painter, plot, series)
        painter.end()

    def _draw_grid(self, painter: QPainter, plot: QRectF) -> None:
        """0/50/100% 눈금과 경고선.

        축을 데이터에 맞춰 늘이지 않는다 - 78%에서 80%로 간 것이 화면을 꽉
        채우면 보는 사람은 큰일이 난 줄 안다."""

        painter.setPen(QColor(theme.TEXT_MUTED))
        for value in (0, 50, 100):
            y = _y_for(value, plot.top(), plot.height())
            painter.drawText(
                QRectF(0, y - 8, CHART_LEFT - 6, 16),
                Qt.AlignRight | Qt.AlignVCenter, f"{value}%",
            )
            line = QPen(QColor(theme.BORDER))
            line.setWidth(1)
            painter.setPen(line)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(theme.TEXT_MUTED))

        # 경고선. 선 하나가 "이게 어느 정도인가"를 숫자보다 빨리 말한다.
        for threshold, tier in (
            (tiers.WARN_THRESHOLD, tiers.WARN),
            (tiers.ALERT_THRESHOLD, tiers.ALERT),
        ):
            y = _y_for(threshold, plot.top(), plot.height())
            pen = QPen(QColor(tiers.color(tier)))
            pen.setWidth(1)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

    def _draw_line(self, painter: QPainter, plot: QRectF, series) -> None:
        """데이터 선.

        **등급 색을 쓰지 않는다.** 이 그래프에는 경고선(90/95%)이 함께 있는데,
        그것도 등급 색이라 선이 둘 다 주황이 되어 어느 쪽이 데이터인지 구분이
        안 됐다 (실제로 그랬다). 여기서는 선이 '데이터', 점선이 '기준'이라는
        역할 구분이 색보다 중요하다 - 지금 등급은 옆 칸 배지가 이미 말한다."""

        count = max(1, len(series.points) - 1)
        pen = QPen(QColor(theme.ACCENT))
        pen.setWidth(2)
        painter.setPen(pen)
        for run in trend.segments(series):
            points = [
                QPointF(
                    plot.left() + plot.width() * index / count,
                    _y_for(series.points[index].value, plot.top(), plot.height()),
                )
                for index in run
            ]
            if len(points) < 2:
                painter.drawPoint(points[0])
                continue
            painter.drawPolyline(QPolygonF(points))

    def _draw_dates(self, painter: QPainter, plot: QRectF, series) -> None:
        """양끝 날짜만 적는다.

        눈금을 촘촘히 달면 좁은 창에서 글자가 겹친다. 이 그래프가 답하는 것은
        "어디서 어디로 왔나"라 양끝이면 충분하다."""

        painter.setPen(QColor(theme.TEXT_MUTED))
        left = formatting.local_date_text(
            series.started_at.isoformat() if series.started_at else None, fallback=""
        )
        right = formatting.local_date_text(
            series.ended_at.isoformat() if series.ended_at else None, fallback=""
        )
        y = plot.bottom() + 2
        painter.drawText(
            QRectF(plot.left(), y, plot.width() / 2, CHART_BOTTOM - 2),
            Qt.AlignLeft | Qt.AlignVCenter, left,
        )
        painter.drawText(
            QRectF(plot.center().x(), y, plot.width() / 2, CHART_BOTTOM - 2),
            Qt.AlignRight | Qt.AlignVCenter, right,
        )
