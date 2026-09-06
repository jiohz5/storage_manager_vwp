"""대시보드 한 장을 그리는 데 필요한 데이터를 모은다 (Qt 없음).

## 왜 GUI 밖에 있는가

두 가지다.

- **시험할 수 있어야 한다.** 개발 PC 에는 PyQt5 가 없어 `smvwp.gui` 는 임포트
  조차 안 된다. 화면 안에 로직이 있으면 그 로직은 영원히 시험 밖에 남는다.
- **화면 스레드에서 돌면 안 된다.** 표본 읽기는 쿼리 하나지만 FULL 예측은
  계정마다 이력을 다시 훑는다. 데이터 디렉터리가 NFS 위면 그동안 창이 통째로
  멈춘다 - 실기에서 실제로 그랬다. 여기 있으면 작업 스레드에서 부르기 쉽다.

`scheduler.DashboardWorker` 가 이 함수를 스레드에서 부르고, 결과만 화면으로
넘긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import forecast_notify, store


@dataclass
class DashboardData:
    """대시보드 한 장에 필요한 것 전부.

    `forecast_failed` 를 따로 두는 이유: 예측이 **없는 것**과 **실패한 것**은
    다르다. 표본이 모자라 아직 예측을 못 내는 것은 정상이지만, 계산이 터진
    것은 알아야 한다.
    """

    samples: dict = field(default_factory=dict)
    forecasts: dict = field(default_factory=dict)
    forecast_failed: bool = False


def read_dashboard(data_dir: Path, config) -> DashboardData:
    """최신 표본과 FULL 예측을 읽어 온다 (읽기 전용).

    예측이 터져도 표본은 돌려준다. 예측 하나 때문에 대시보드 전체가 안 뜨면
    "용량이 얼마인지"조차 못 보게 되는데, 그쪽이 훨씬 나쁜 실패다.
    """

    conn = store.connect(data_dir)
    try:
        samples = store.latest_samples(conn)
    finally:
        conn.close()

    try:
        forecasts = {
            item.account_id: item
            for item in forecast_notify.build_forecasts(data_dir, config)
        }
        failed = False
    except Exception:
        forecasts = {}
        failed = True
    return DashboardData(samples=samples, forecasts=forecasts, forecast_failed=failed)
