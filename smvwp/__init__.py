"""Storage Manager VWP - 재구현판(phase 1) 패키지.

이 패키지는 DESIGN.md에 정리된 설계 철학만 계승하고,
코드는 저장소 루트의 기존 `storage_manager/` 구현과 완전히 분리해 새로 작성했다.
기존 패키지와 이름이 겹치지 않도록 `smvwp`(Storage Manager VWP)로 명명했다 —
혹시라도 두 코드베이스가 같은 PYTHONPATH에 함께 놓이더라도 import가 섞이지
않게 하기 위함이다.

Phase 1 범위 밖의 책임(야간 상세 스캔, 검색 인덱스, quota 연동, 다국어 등)은
아직 이 패키지에 없다. DESIGN.md 2부 8~9절 참고.
"""

from __future__ import annotations

# 판 - 고친 날짜 (같은 날 두 번이면 `.2`). 반입은 ZIP 을 풀어 `smvwp/` 를 갈아 끼우는
# 것이라 실기에서 git 으로 "그 고친 것이 들어갔나" 를 볼 수 없다 - 창 제목과
# `smvwp_cli.py --version` 이 이 값을 보인다. **고쳐서 내보낼 때마다 올린다.**
__version__ = "2026.11.14.3"
