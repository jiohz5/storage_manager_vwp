"""'눈에 띈다' 의 문턱 - 화면과 알림이 같은 답을 내도록 한 곳에서 정한다 (Qt 없음).

## 왜 따로 있는가

"이 계정이 지난 스캔보다 많이 늘었다" 를 화면마다 다른 값으로 판정했다. 홈의
'무엇부터 할까' 는 200GB(박힌 값), 상세 스캔 요약의 '살펴볼 것' 은 50GB(박힌 값),
경로 급증 알림은 100GB(설정). 같은 계정이 150GB 늘면 요약에는 뜨고 홈에는 안 떴다.

## 왜 비율인가

전부 절대값이었다. 계정이 수십 TB 면 50GB 는 0.2% 라 바퀴마다 있는 출렁임이고,
그것이 매번 '살펴볼 것' 에 오르면 목록을 안 믿게 된다. 반대로 500GB 계정에서
199GB 는 40% 인데도 홈은 조용했다. 그래서 문턱은 **계정 크기의 일정 비율**이고,
작은 계정에서 몇 GB 로 사람을 부르지 않도록 **바닥값** 아래로는 내려가지 않는다.

기준이 스토리지(파일시스템) 크기가 아니라 계정 크기인 까닭: 야간 스캔 쪽은 `df`
표본을 모르고, 한 스토리지를 여러 계정이 나눠 쓰면 스토리지 크기는 그 계정의
크기를 말해 주지 않는다. 계정 크기는 증감을 낸 바로 그 스캔이 잰 값이라 늘 있다.

3시간 창의 `df` 급증(`capacity_surge_min_kb`)은 여기 넣지 않았다 - 스캔 두 번
사이가 아니라 몇 시간 사이의 속도를 보는 다른 물음이다.
"""

from __future__ import annotations

from typing import Optional


def growth_threshold_kb(account_kb: Optional[int], settings) -> int:
    """이 계정에서 '많이 늘었다' 고 볼 증가량 (KB).

    계정 크기의 `growth_notice_pct` % 와 바닥값 `growth_alert_min_kb` 가운데 큰 쪽.
    계정 크기를 모르면 바닥값이다. 계정 전체의 증감에도, 그 안 폴더 하나의 증감에도
    같은 값을 쓴다 - 알림이 간 증가를 화면이 '별일 아님' 으로 보이면 안 된다."""

    floor = int(settings.growth_alert_min_kb)
    if not account_kb:
        return floor
    return max(floor, int(account_kb * settings.growth_notice_pct / 100))


def cleanup_threshold_kb(settings) -> int:
    """이만큼 이상 비울 수 있을 때만 정리를 따로 권한다 (KB).

    홈의 정리 줄(50GB, 박힌 값)과 보고서의 정리 후보(`cleanup_min_size_kb`, 100GB)가
    서로 다른 값을 썼다. 설정 하나로 맞춘다. 꽉 찬 스토리지 줄에 붙는 정리량에는
    문턱이 없다 - 급할 때는 작은 것도 말한다."""

    return int(settings.cleanup_min_size_kb)
