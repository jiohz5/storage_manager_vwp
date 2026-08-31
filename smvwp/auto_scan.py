"""GUI가 야간 시간창을 스스로 지켜, cron 없이 밤 스캔을 시작한다.

## 왜 이 판단이 따로 있는가

"창을 켜 두면 밤에 알아서 돌고, 창을 닫으면 같이 멈춘다"를 만들려면 **밤마다
정확히 한 번**만 시작해야 한다. 그냥 "시간창 안이면 시작"으로 두면 스캔이
01시에 끝난 뒤 01분 뒤 타이머가 또 시작해 버린다 - 밤새 같은 계정을 반복해서
훑으면서 세대만 쌓인다.

그래서 **어느 밤인지**를 값으로 만들어(`window_key`) 그 밤에 이미 시작했는지를
기억한다. 자정을 넘어가므로 "오늘 날짜"로는 안 된다 - 22시와 그다음 01시는
날짜가 다르지만 같은 밤이다. 끝나는 아침을 밤의 이름으로 쓴다.

판단만 여기 두고 실행은 두지 않는다. Qt 없이 시험할 수 있어야 하기 때문이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from . import scan_window


def window_key(
    now: datetime,
    start_hour: int = scan_window.DEFAULT_START_HOUR,
    end_hour: int = scan_window.DEFAULT_END_HOUR,
) -> Optional[str]:
    """이 시각이 속한 밤의 이름. 시간창 밖이면 None.

    **끝나는 아침의 날짜**를 이름으로 쓴다. 22시와 그다음 01시는 날짜가 다르지만
    같은 밤이므로, 시작 날짜로 이름을 지으면 자정에 밤이 두 개로 갈라진다.
    """

    if not scan_window.is_within_window(now, start_hour, end_hour):
        return None
    morning = now if now.hour < end_hour else now + timedelta(days=1)
    return morning.strftime("%Y-%m-%d")


def should_start(
    now: datetime,
    settings,
    already_running: bool,
    last_started_key: Optional[str],
) -> bool:
    """지금 야간 스캔을 시작해야 하는가.

    넷 다 만족해야 한다 - 켜져 있고, 시간창 안이고, 지금 도는 것이 없고,
    **이 밤에 아직 시작한 적이 없어야** 한다. 마지막 조건이 없으면 밤새
    반복해서 훑는다.
    """

    if not getattr(settings, "gui_auto_nightly_scan", False):
        return False
    if already_running:
        return False
    key = window_key(
        now,
        settings.detail_scan_window_start_hour,
        settings.detail_scan_window_end_hour,
    )
    if key is None:
        return False
    return key != last_started_key
