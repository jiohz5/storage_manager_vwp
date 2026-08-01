"""외부 명령 실행 시 **항상 UTF-8**을 쓰기 위한 얇은 래퍼.

`subprocess.run(..., text=True)`를 그대로 쓰면 파이썬이 stdin/stdout을
`locale.getpreferredencoding()`으로 인코딩/디코딩한다. 이게 이 앱에서는 실제
장애로 이어진다:

- cron 환경은 로그인 셸이 아니라 `LANG`이 없거나 `LANG=C`인 경우가 흔하다.
  그러면 선호 인코딩이 ASCII가 되어, 한글이 들어간 알림 JSON을 stdin으로 넘길
  때 `UnicodeEncodeError`로 알림 전송이 통째로 실패한다.
- 한국어 Windows 개발 PC에서는 CP949가 되어, UTF-8을 기대하는 수신 프로그램이
  깨진 바이트를 받는다.
- `du`/`find` 출력에는 파일 경로가 들어간다. 경로에 비ASCII 문자가 있으면 같은
  이유로 디코딩이 깨지거나 예외가 난다.

그래서 이 모듈은 바이트 모드로 실행하고 인코딩/디코딩을 UTF-8로 명시한다.
출력 디코딩은 `errors="replace"`를 쓴다 - 파일 이름이 UTF-8이 아닌 바이트열인
경우(리눅스에서는 가능하다) 예외로 스캔 전체를 죽이는 것보다, 해당 글자만
대체 문자로 두고 나머지 결과를 살리는 편이 낫다.
"""

from __future__ import annotations

import subprocess
from typing import Optional, Sequence

ENCODING = "utf-8"


def run_utf8(
    command: Sequence[str],
    timeout: Optional[int] = None,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """명령을 실행하고 stdout/stderr를 UTF-8 문자열로 디코딩해 돌려준다.

    `shell`은 절대 쓰지 않는다 (argv를 그대로 넘긴다) - 경로나 계정명에 공백·
    따옴표·세미콜론이 있어도 명령이 재해석되지 않아야 하기 때문.

    반환 타입은 `subprocess.run(text=True)`와 같아서 호출부는 그대로 `.stdout`,
    `.returncode`를 쓰면 된다. 예외(`TimeoutExpired`, `FileNotFoundError` 등)도
    그대로 올라가므로 각 호출부가 기존처럼 처리한다.
    """

    completed = subprocess.run(
        list(command),
        input=input_text.encode(ENCODING) if input_text is not None else None,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return subprocess.CompletedProcess(
        args=completed.args,
        returncode=completed.returncode,
        stdout=_decode(completed.stdout),
        stderr=_decode(completed.stderr),
    )


def _decode(raw: Optional[bytes]) -> str:
    if not raw:
        return ""
    return raw.decode(ENCODING, errors="replace")
