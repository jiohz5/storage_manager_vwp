"""cron 에 우리 항목이 걸려 있는지 확인한다.

## 왜 화면에 띄우는가

야간 스캔이 안 도는 가장 흔한 이유는 **cron 에 등록이 안 된 것**이다. 그런데
그 사실은 아무 데도 드러나지 않는다 - 화면은 "최근 스캔 없음"이라고만 말하고,
사람은 프로그램이 고장 난 줄 안다. 다음 날 아침 보고서가 비어 있어야 알아채고,
그때는 이미 하룻밤을 버린 뒤다.

`crontab -l` 한 번이면 알 수 있는 것을 안 보여 줄 이유가 없다.

## 표식으로 찾는다

`setup_cron.csh` 가 각 줄 끝에 주석 표식을 붙여 둔다. 명령줄 문자열을 통째로
비교하면 파이썬 경로나 데이터 디렉터리가 조금만 달라도 못 찾으므로, 표식만
본다.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

# `setup_cron.csh` 가 붙이는 표식. 여기를 고치면 저쪽도 같이 고쳐야 한다.
COLLECTOR_MARKER = "# storage_manager_vwp_v2_collector"
NIGHTLY_MARKER = "# storage_manager_vwp_v2_nightly_scan"

# `crontab -l` 은 로컬 명령이라 금방 끝난다. 그래도 시간 제한을 두는 것은,
# 이 호출 하나가 멈추면 화면이 통째로 멈추기 때문이다.
TIMEOUT_SECONDS = 5.0


@dataclass
class CronStatus:
    """`crontab -l` 을 읽어 본 결과.

    `available` 이 False 면 **"등록 안 됨"이 아니라 "모른다"** 이다. 둘을 섞으면
    cron 이 잘 돌고 있는데도 경고를 띄우게 된다 - 컨테이너나 권한 문제로
    `crontab` 을 못 부르는 경우가 그렇다.
    """

    available: bool = False
    collector: bool = False
    nightly: bool = False
    error: Optional[str] = None

    @property
    def both(self) -> bool:
        return self.collector and self.nightly


def read_status(runner=None) -> CronStatus:
    """지금 사용자의 crontab 에 우리 항목이 있는지 본다.

    `runner` 는 시험용 주입점이다 (`subprocess.run` 과 같은 모양).
    """

    run = runner or subprocess.run
    try:
        proc = run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return CronStatus(error="crontab 명령이 없습니다")
    except (OSError, subprocess.SubprocessError) as exc:
        return CronStatus(error=str(exc))

    text = proc.stdout or ""
    if proc.returncode not in (0, 1):
        # 1 은 "등록된 항목 없음"이라 정상 판정에 쓴다. 그 밖의 실패는 모른다.
        return CronStatus(error=(proc.stderr or "").strip() or f"exit={proc.returncode}")

    # 주석으로 꺼 둔 줄은 등록된 것이 아니다. `#` 로 시작하는 줄은 뺀다 -
    # 우리 표식 자체가 주석이라 그냥 찾으면 꺼 둔 줄도 걸린다.
    active = [
        line for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    joined = "\n".join(active)
    return CronStatus(
        available=True,
        collector=COLLECTOR_MARKER in joined,
        nightly=NIGHTLY_MARKER in joined,
    )


def summary_key(status: CronStatus) -> str:
    """화면에 쓸 i18n 키. 판정을 한 곳에 모아 둔다."""

    if not status.available:
        return "cron.unknown"
    if status.both:
        return "cron.ok"
    if status.nightly and not status.collector:
        return "cron.collector_missing"
    if status.collector and not status.nightly:
        return "cron.nightly_missing"
    return "cron.none"
