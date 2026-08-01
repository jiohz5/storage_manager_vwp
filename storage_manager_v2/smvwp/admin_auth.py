"""관리자 PIN - **화면 노출 제한**이지 보안 경계가 아니다.

이 점을 코드와 UI 양쪽에 분명히 남긴다. 기존 구현의 README도 "고정 비밀번호
방식은 소수 운영자를 위한 UI 노출 제한이며 운영체제 권한 통제나 암호화가
아니다"라고 못박았고, 그 성격은 재구현에서도 그대로다:

- 검색 인덱스 DB는 이 PIN으로 암호화되지 않는다. 파일을 직접 읽을 수 있는
  사람은 PIN과 무관하게 내용을 볼 수 있다.
- 진짜 접근 통제는 데이터 디렉터리의 파일시스템 권한으로 해야 한다.
- 따라서 이 PIN의 목적은 "옆자리 사람이 실수로 전체 경로 목록을 열어보는 일"을
  막는 정도이며, 그 이상을 기대하면 안 된다.

PIN은 평문으로 비교하지 않고 해시로 저장한다. 이것이 위 성격을 바꾸지는
않지만(공격자는 DB를 직접 읽으면 되므로), 설정 파일을 어깨너머로 봤다고 바로
PIN이 노출되지는 않게 하는 최소한의 조치다.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

# 기본 PIN. 운영에서는 반드시 바꾸도록 GUI/문서에서 안내한다.
DEFAULT_PIN = "6368"

# PIN 최소 길이. 4자리는 짧지만, 이 장치의 목적 자체가 강한 인증이 아니라
# 화면 노출 제한이므로 현장에서 쓰기 불편할 정도로 강제하지는 않는다.
MIN_PIN_LENGTH = 4

_ITERATIONS = 120_000


def is_using_default(stored: str) -> bool:
    """아직 기본 PIN을 쓰고 있는지 (변경 권고를 띄우기 위한 확인)."""

    return not stored


def hash_pin(pin: str, salt: Optional[bytes] = None) -> str:
    """PIN을 `salt$hash` 형태 문자열로 만든다 (PBKDF2-HMAC-SHA256)."""

    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    """저장된 해시와 대조한다. 형식이 깨졌으면 조용히 False.

    비교는 `hmac.compare_digest`로 한다 - 타이밍 차이로 정보가 새는 것을 막는
    표준 관행이다."""

    if not stored:
        # 아직 설정된 PIN이 없으면 기본 PIN과 비교한다 (최초 사용 편의).
        return hmac.compare_digest(pin, DEFAULT_PIN)
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _ITERATIONS)
    return hmac.compare_digest(candidate.hex(), digest_hex)


class AdminSession:
    """현재 실행 세션에서만 유효한 잠금 해제 상태.

    프로세스가 끝나면 사라진다 - 디스크에 "해제됨"을 남기지 않는다. 앱을 다시
    켜면 다시 PIN을 물어야 한다."""

    def __init__(self) -> None:
        self._unlocked = False

    @property
    def is_unlocked(self) -> bool:
        return self._unlocked

    def unlock(self, pin: str, stored_hash: str = "") -> bool:
        if verify_pin(pin, stored_hash):
            self._unlocked = True
        return self._unlocked

    def lock(self) -> None:
        self._unlocked = False
