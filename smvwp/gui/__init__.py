"""PyQt5 기반 GUI (대시보드 단일 화면 + 계정 관리 다이얼로그).

DESIGN.md 2부 6절 결정: 기존처럼 여러 탭으로 나누지 않고 대시보드
하나에 계정 목록/요약/상태를 모으고, 계정 등록/설정처럼 자주 쓰지 않는
동작만 다이얼로그로 분리한다.

이 서브패키지는 PyQt5를 최상위에서 import하므로, PyQt5가 없는 환경(예: 이
저장소를 검토만 하는 개발 PC)에서는 `smvwp.gui`를 import하는 순간 실패한다.
`smvwp` 최상위 패키지와 비-GUI 모듈(config/collector/store/notifications/
diagnostics)은 PyQt5 없이도 그대로 동작하고 단위 테스트가 가능하도록
의도적으로 분리했다.
"""

from __future__ import annotations
