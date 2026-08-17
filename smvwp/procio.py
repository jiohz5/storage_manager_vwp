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

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional, Sequence

ENCODING = "utf-8"

# 지금 이 프로세스가 띄워 둔 자식들.
#
# 창을 닫을 때 `du`/`find`를 같이 정리하려면 핸들이 있어야 한다. 부모가 그냥
# 종료하면 자식은 죽지 않고 init에 재부모화되어 끝까지 도는데 (실제로 재현해
# 확인했다), 그러면 창을 껐는데도 파일서버 부하가 계속된다.
#
# DESIGN.md 1부 2-4절의 "강제 kill 없음"은 **임의의 PID에 신호를 보내지 않는다**는
# 뜻이고, 같은 문장이 "실행 중인 하위 프로세스만 정리한다"고 명시한다. 여기서
# 정리하는 것은 우리가 띄운 자식뿐이라 그 원칙 안에 있다.
PROC_DIR = Path("/proc")


def live_child_pids() -> "list":
    """지금 살아 있는 **내 직계 자식**들의 pid.

    `/proc/<pid>/status`의 `PPid`가 내 pid인 것만 고른다. Popen 핸들을 따로
    모아 두지 않는 이유는 이 저장소의 테스트가 `subprocess.run`을 모킹하는
    규약을 쓰고 있어서다 - 호출 방식을 바꾸면 무관한 테스트 수십 개가 같이
    깨진다. 커널이 이미 부모-자식 관계를 알고 있으니 그것을 읽는 편이 낫다.

    리눅스 전용이다. 다른 OS에서는 빈 목록을 돌려주고, 호출부는 "정리할 것이
    없었다"로 취급한다."""

    if not PROC_DIR.exists():
        return []
    me = os.getpid()
    children = []
    for entry in PROC_DIR.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            for line in (entry / "status").read_text(encoding="utf-8").splitlines():
                if line.startswith("PPid:"):
                    if int(line.split()[1]) == me:
                        children.append(int(entry.name))
                    break
        except (OSError, ValueError, IndexError):
            # 훑는 도중 사라지는 것은 정상이다 (경쟁 상태).
            continue
    return children


def terminate_children(timeout: float = 3.0) -> int:
    """내가 띄운 자식들을 정리하고 정리한 개수를 돌려준다.

    먼저 SIGTERM으로 부탁하고, 그래도 안 끝나면 SIGKILL로 끊는다. `du`는
    시그널을 받으면 바로 끝나므로 대개 첫 단계에서 정리된다.

    **다른 사람의 프로세스는 절대 건드리지 않는다** - 대상은 커널이 내
    자식이라고 말해 주는 pid뿐이다."""

    targets = live_child_pids()
    if not targets:
        return 0

    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:  # 이미 끝났음
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(pid in set(live_child_pids()) for pid in targets):
            return len(targets)
        time.sleep(0.1)

    for pid in live_child_pids():
        if pid in targets:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:  # pragma: no cover
                pass
    return len(targets)


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
