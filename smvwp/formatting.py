"""숫자와 상태를 사람이 읽는 문자열로 바꾼다 (Qt 없음).

## 왜 GUI 밖에 있는가

예전에는 `gui/widgets.py` 안에서 Qt 위젯들과 섞여 있었다. 그러면 이 함수들을
시험하려고 해도 **PyQt5 를 임포트해야** 하는데, 개발 PC 에는 없다. 서식은
어긋나기 쉬운 곳인데(단위, 반올림, 빈 값 표기) 정작 시험이 하나도 없었다.

Qt 를 쓰지 않는 코드가 Qt 파일 안에 있으면 그 코드는 영원히 시험 밖에 남는다.
`gui/widgets.py` 는 이제 여기서 가져다 쓰고, 자기는 그리는 일만 한다.
"""

from __future__ import annotations

from typing import Optional

from . import i18n, tiers


def tier_badge_text(tier: str, pct: Optional[float]) -> str:
    return tiers.display_text(tier, pct)


def format_kb(size_kb: Optional[int]) -> str:
    """KB 정수를 사람이 읽는 크기 문자열로. 값이 없으면 '-'.

    `du -sk`가 KB 단위로 주므로 KB를 기준 단위로 삼는다. 1024 배수를 쓰되
    소수점 한 자리까지만 보여준다 (정밀도를 실제보다 높아 보이게 하지 않기
    위해 유효숫자를 늘리지 않는다)."""

    if size_kb is None:
        return "-"
    value = float(size_kb)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024 or unit == "PB":
            if unit == "KB":
                return f"{int(value):,} KB"
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} PB"  # pragma: no cover - 위 루프에서 반환됨




def scan_label(completed_at, fallback_generation=None) -> str:
    """스캔을 가리키는 이름. 완료 시각이 있으면 **날짜**로, 없으면 회차 번호로.

    "3번째 스캔"은 내부 번호라 사용자에게 기준점이 못 된다. "260819 스캔"은
    그날 무슨 일이 있었는지와 바로 연결된다 - 사람이 실제로 기억하는 단위다.

    한국어는 사내 관례대로 `YYMMDD`, 영어는 오해가 없도록 `YYYY-MM-DD`를 쓴다.
    """

    if completed_at:
        text = str(completed_at)
        try:
            year, month, day = text[:4], text[5:7], text[8:10]
            if i18n.get_language() == i18n.KOREAN:
                return f"{year[2:]}{month}{day}"
            return f"{year}-{month}-{day}"
        except (IndexError, ValueError):  # pragma: no cover - 방어적 처리
            pass
    if fallback_generation is not None:
        return i18n.t("scan.nth", n=fallback_generation)
    return i18n.t("common.none")


def format_size_pair(used_kb: Optional[int], total_kb: Optional[int]) -> str:
    """`17.2 / 40.0 TB` 처럼 사용량과 총 용량을 한 칸에 보여준다.

    **두 값에 같은 단위를 쓴다.** 각자 알아서 단위를 고르게 하면
    `950.0 GB / 1.0 TB`처럼 나와서, 한눈에 비교하라고 붙여 놓은 표시가 오히려
    암산을 요구하게 된다. 큰 쪽(총 용량)의 단위로 맞춘다.

    **단위는 TB에서 멈춘다.** 실제 계정은 많아야 수십 TB라 PB로 올라가면
    `0.0 / 0.0 PB`처럼 뭉개져 아무것도 못 읽는다. 100TB대까지는 TB로 두는 편이
    훨씬 잘 읽힌다 (`99.1 / 100.0 TB`).
    """

    if total_kb is None:
        return format_kb(used_kb) if used_kb is not None else "-"

    unit, divisor = _unit_for(total_kb, max_unit="TB")
    used_text = f"{used_kb / divisor:,.1f}" if used_kb is not None else "?"
    return f"{used_text} / {total_kb / divisor:,.1f} {unit}"


_UNITS = ("KB", "MB", "GB", "TB", "PB")


def _unit_for(size_kb: int, max_unit: str = "PB") -> "tuple":
    """이 크기를 읽기 좋은 단위와 그 나눗수. `max_unit`에서 올라가기를 멈춘다."""

    limit = _UNITS.index(max_unit)
    value = float(size_kb)
    divisor = 1.0
    for index, unit in enumerate(_UNITS):
        if abs(value) < 1024 or index >= limit:
            return unit, divisor
        value /= 1024
        divisor *= 1024
    return max_unit, divisor  # pragma: no cover - 위 루프에서 반환됨


def _unavailable_text(prediction) -> str:
    reason_key = f"forecast.reason.{prediction.reason}" if prediction.reason else ""
    reason = i18n.t(reason_key) if reason_key else ""
    unavailable = i18n.t("forecast.unavailable")
    # 번역 키가 없으면 t()가 키를 그대로 돌려주므로, 그럴 땐 사유를 붙이지 않는다.
    if reason and reason != reason_key:
        return f"{unavailable}({reason})"
    return unavailable


def format_prediction(prediction) -> str:
    """예측 하나를 짧은 표시 문자열로. 실패 사유도 사람이 읽게 옮긴다."""

    if not prediction.ok or prediction.hours_to_full is None:
        return _unavailable_text(prediction)
    if prediction.hours_to_full < 1:
        # 반올림해서 '약 0시간'이 되면 "이미 찼다"는 뜻인지 "곧"인지 모호하다.
        return i18n.t("forecast.within_hour")
    if prediction.hours_to_full < 48:
        return i18n.t("forecast.hours", hours=f"{prediction.hours_to_full:.0f}")
    return i18n.t("forecast.days", days=f"{prediction.days_to_full:.0f}")


def format_forecast_cell(forecast, imminent_hours: float = 48.0) -> str:
    """대시보드 `FULL 예상` 칸.

    임박했을 때는 시간 단위 하나만 크게 보여주고(그 순간엔 그게 유일하게
    중요한 정보다), 평상시에는 7일/30일 추세를 나란히 보여줘 추세가 빨라졌는지
    느려졌는지를 사람이 판단하게 한다."""

    if forecast is None:
        return i18n.t("common.none")
    if forecast.imminent.ok and forecast.imminent.hours_to_full is not None:
        if forecast.imminent.hours_to_full < imminent_hours:
            return format_prediction(forecast.imminent)

    short, long = forecast.short_trend, forecast.long_trend
    # 둘 다 같은 이유로 불가면 같은 문구를 두 번 쓰지 않는다 - 칸만 길어지고
    # 읽는 사람이 얻는 정보는 같다.
    if not short.ok and not long.ok and short.reason == long.reason:
        return _unavailable_text(short)
    return i18n.t(
        "forecast.pair", short=format_prediction(short), long=format_prediction(long)
    )


def format_forecast_tooltip(forecast, window_hours: int) -> str:
    if forecast is None:
        return ""
    slope = forecast.imminent.slope_kb_per_hour
    slope_text = f"{slope:,.0f} KB/h" if slope else i18n.t("common.none")
    return i18n.t(
        "forecast.tooltip",
        short=format_prediction(forecast.short_trend),
        long=format_prediction(forecast.long_trend),
        window=window_hours,
        slope=slope_text,
    )


def format_bytes(size_bytes: Optional[int]) -> str:
    """byte 단위 크기를 사람이 읽는 문자열로 (파일 실제 크기 표시용)."""

    if size_bytes is None:
        return "-"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value):,} B"
            return f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} TB"  # pragma: no cover - 위 루프에서 반환됨


def format_kb_delta(delta_kb: Optional[int]) -> str:
    """증감량 표시 - 부호를 명시하고 0은 '변화 없음'으로."""

    if delta_kb is None:
        return "-"
    if delta_kb == 0:
        return "변화 없음"
    sign = "+" if delta_kb > 0 else "-"
    return f"{sign}{format_kb(abs(delta_kb))}"


def path_tooltip(account, sample) -> str:
    """경로 칸 툴팁: 전체 경로 + 파일시스템/마운트 지점.

    `파일시스템` 열을 없앤 대신이다. 계정 대부분이 같은 파일시스템에 있어
    열로 두면 같은 값이 반복되며 자리만 차지했는데, 정작 확인하고 싶은
    순간(이 계정이 어느 볼륨인가)에는 여기 있으면 충분하다."""

    lines = [account.path]
    if sample is not None:
        if sample.filesystem:
            lines.append(i18n.t("dashboard.tip.filesystem", value=sample.filesystem))
        if sample.mount_point:
            lines.append(i18n.t("dashboard.tip.mount", value=sample.mount_point))
    return "\n".join(lines)


def size_tooltip(sample) -> str:
    """사용량/총량/남은 용량. 막대와 크기 칸 양쪽에 붙인다."""

    avail = sample.avail_kb
    return "\n".join(
        [
            i18n.t("dashboard.tip.used", value=format_kb(sample.used_kb)),
            i18n.t("dashboard.tip.total", value=format_kb(sample.total_kb)),
            i18n.t("dashboard.tip.free", value=format_kb(avail)),
        ]
    )


def scan_cpu_text(latest_run) -> str:
    """직전 스캔이 이 장비 CPU를 얼마나 썼는지.

    상세 스캔이 얼마나 무거운지는 계정 크기·파일 수·파일시스템에 따라
    달라서 **미리 예측할 수 없다.** 대신 실제로 돈 결과를 남겨 두면 다음
    실행 전에 "지난번엔 이 정도였다"로 판단할 수 있다.

    `top` 기준(코어 1개 = 100%)을 함께 적는 이유: 사용자가 top을 띄워 놓고
    대조할 때 숫자가 맞아야 하기 때문."""

    if not latest_run:
        return ""
    try:
        avg = latest_run["cpu_top_percent_avg"]
        peak = latest_run["cpu_top_percent_peak"]
        system_avg = latest_run["cpu_system_percent_avg"]
    except (KeyError, IndexError):
        return ""
    if avg is None or peak is None:
        return ""
    text = i18n.t(
        "scan.cpu_usage",
        avg=f"{avg:.0f}",
        peak=f"{peak:.0f}",
        system=f"{system_avg:.1f}" if system_avg is not None else "-",
    )

    # 메모리는 최고치만 붙인다. "스캔이 메모리를 위협했나"에 답하는 것은
    # 평균이 아니라 순간 최대다.
    try:
        rss_kb = latest_run["rss_peak_kb"]
        mem_pct = latest_run["memory_peak_percent"]
    except (KeyError, IndexError):
        return text
    if rss_kb:
        text += "  |  " + i18n.t(
            "scan.memory_usage",
            peak=format_kb(rss_kb),
            percent=f"{mem_pct:.1f}" if mem_pct is not None else "-",
        )
    return text
