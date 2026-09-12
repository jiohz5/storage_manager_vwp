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

import logging
from dataclasses import dataclass, field
from pathlib import Path

from . import forecast_notify, store

logger = logging.getLogger(__name__)


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
    # "그래서 오늘 뭘 먼저 하지" 한 줄. 여기서 함께 읽는 이유는 재료가 이미
    # 손에 있기 때문이다 - 표본은 방금 읽었고, 나머지는 스캔 DB 한 번이다.
    plan: object = None
    # 계정별 추세선 `{계정id: trend.Series}`. 표 칸에 그린다.
    trends: dict = field(default_factory=dict)


def read_dashboard(data_dir: Path, config) -> DashboardData:
    """최신 표본과 FULL 예측을 읽어 온다 (읽기 전용).

    예측이 터져도 표본은 돌려준다. 예측 하나 때문에 대시보드 전체가 안 뜨면
    "용량이 얼마인지"조차 못 보게 되는데, 그쪽이 훨씬 나쁜 실패다.
    """

    conn = store.connect(data_dir)
    try:
        samples = store.latest_samples(conn)
        trends = _read_trends(conn, config)
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

    return DashboardData(
        samples=samples,
        forecasts=forecasts,
        forecast_failed=failed,
        plan=_read_plan(data_dir, config, samples),
        trends=trends,
    )


# 표 칸에 그릴 추세의 기간.
#
# 30일이면 "이번 달에 어떻게 왔나"가 보인다. 90일까지 넣으면 칸이 좁아 최근
# 변화가 뭉개지고, 7일이면 주말 하나에 모양이 흔들린다.
TREND_DAYS = 30

# 칸 하나에 그릴 점의 수. 폭이 110픽셀이라 이보다 촘촘히 그려 봐야 안 보인다.
TREND_BUCKETS = 60


def _read_trends(conn, config) -> dict:
    """계정별 추세선. 실패해도 표는 떠야 하므로 통째로 감싼다."""

    from datetime import datetime, timedelta, timezone

    from . import trend

    result = {}
    try:
        since = datetime.now(timezone.utc) - timedelta(days=TREND_DAYS)
        now = datetime.now(timezone.utc)
        for account in config.accounts:
            samples = store.samples_since(conn, account.account_id, since)
            # 창을 명시한다. 안 그러면 표본이 하루치뿐인 계정의 하루가 칸을
            # 꽉 채워, 옆 계정의 30일과 나란히 놓였을 때 축이 서로 다른
            # 그래프를 같은 것처럼 보게 된다.
            result[account.account_id] = trend.build(
                samples, since=since, until=now, buckets=TREND_BUCKETS
            )
    except Exception:
        logger.exception("추세 읽기 실패 (대시보드는 계속합니다)")
    return result


def _read_plan(data_dir, config, samples):
    """처리 우선순위. 터져도 대시보드는 떠야 한다.

    용량 표는 이 프로그램의 본체다. 우선순위 계산 하나 때문에 그것까지 못 보게
    되면 훨씬 나쁜 실패가 된다 - 예측을 그렇게 다루는 것과 같은 이유다."""

    from . import health, nightly_scan, priority

    try:
        summary = health.check_all(data_dir, config)
    except Exception:
        logger.exception("헬스체크 실패 (대시보드는 계속합니다)")
        summary = None

    scan_accounts = []
    try:
        scan_accounts = nightly_scan.get_status_snapshot(data_dir, config).accounts
    except Exception:
        logger.exception("스캔 상태 읽기 실패 (대시보드는 계속합니다)")

    try:
        return priority.build(
            samples=list(samples.values()),
            health_summary=summary,
            scan_accounts=scan_accounts,
            accounts_by_id={
                account.account_id: account.name for account in config.accounts
            },
        )
    except Exception:
        logger.exception("처리 우선순위 계산 실패 (대시보드는 계속합니다)")
        return None
