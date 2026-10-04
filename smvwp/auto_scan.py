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

# 이 시각이 속한 밤의 이름 (야간 스캔도 같은 이름을 쓴다).
window_key = scan_window.night_key

# 시간창이 열리고 이만큼은 창이 스스로 시작하지 않는다.
#
# 정규 경로는 cron 이고 창은 cron 이 없을 때의 대비다. 그런데 22:00 에 둘이 함께
# 뜨면 먼저 잠금을 잡은 쪽이 밤을 맡는다 - 창이 잡으면 cron 은 기다리다 물러나고,
# 그 뒤 창을 닫는 순간 그 밤이 끝난다. cron 이 있으면 이 사이에 실행 기록이
# 생기고, 창은 그것을 보고 물러난다 (`last_run_started_at`).
CRON_HEAD_START = timedelta(minutes=5)


def should_start(
    now: datetime,
    settings,
    already_running: bool,
    last_started_key: Optional[str],
    last_run_started_at=None,
) -> bool:
    """지금 야간 스캔을 시작해야 하는가.

    모두 만족해야 한다 - 켜져 있고, 시간창 안이고, 지금 도는 것이 없고, **이
    밤에 아직 시작한 적이 없고**, 창이 열린 지 `CRON_HEAD_START` 가 지났고,
    누구도(cron 이든 앞서 열었던 창이든 다른 장비든) 이 밤에 실행을 시작하지
    않았어야 한다.

    `last_started_key` 는 이 창이 기억하는 것이라 창을 새로 열면 사라진다.
    `last_run_started_at`(가장 최근 실행이 시작한 시각, 실행 기록에서)이 그것을
    메운다 - 예전에는 밤중에 창을 새로 열면 이미 끝난 밤을 처음부터 다시 쟀다.
    """

    if not settings.gui_auto_nightly_scan:
        return False
    if already_running:
        return False
    start = settings.detail_scan_window_start_hour
    end = settings.detail_scan_window_end_hour
    key = window_key(now, start, end)
    if key is None or key == last_started_key:
        return False
    opened = scan_window.window_opened_at(now, start, end)
    if opened is not None and now - opened < CRON_HEAD_START:
        return False
    started = scan_window.local_time_of(last_run_started_at) if last_run_started_at else None
    if started is not None and window_key(started, start, end) == key:
        return False
    return True
