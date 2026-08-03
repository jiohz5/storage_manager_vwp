"""테스트 공용 헬퍼.

## 왜 stdout을 bytes로 만드는가

앱은 외부 명령을 `smvwp.procio.run_utf8`로 실행한다. 이 함수는 `text=True`를
쓰지 않고 **바이트 모드**로 실행한 뒤 UTF-8로 직접 디코딩한다 (cron의 `LANG=C`
환경에서 로케일 인코딩에 맡기면 한글 알림·경로가 깨지기 때문 - 자세한 이유는
`smvwp/procio.py` 참고).

따라서 `subprocess.run`을 흉내 내는 가짜는 실제와 똑같이 **bytes**를 돌려줘야
한다. str을 돌려주면 테스트는 통과해도 실제 동작과 다른 것을 검증하게 된다.
`completed()`를 쓰면 이 부분을 매번 신경 쓰지 않아도 된다.

## 왜 명령을 보고 분기하는 단일 러너를 쓰는가

`smvwp.detail_scan.subprocess`, `smvwp.activity_scan.subprocess`,
`smvwp.quota.subprocess` 등은 전부 **같은 표준 subprocess 모듈 객체**다. 여러
경로를 각각 patch하면 나중에 적용된 patch가 앞의 것을 조용히 덮어써서, `du`
호출이 `find`용 가짜 결과를 받는 식의 혼선이 생긴다. 그래서 patch는 한 번만
걸고 명령 내용으로 분기한다.
"""

from __future__ import annotations

import subprocess
from typing import Callable, Dict, List, Optional, Sequence


def completed(
    command: Sequence[str],
    stdout: str = "",
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess:
    """`procio.run_utf8`가 실제로 받는 형태(bytes stdout/stderr)의 가짜 결과."""

    return subprocess.CompletedProcess(
        args=list(command),
        returncode=returncode,
        stdout=stdout.encode("utf-8"),
        stderr=stderr.encode("utf-8"),
    )


DF_BYTES_OUTPUT = (
    "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
    "/dev/sda1 1000 500 500 50% /user\n"
)
DF_INODE_OUTPUT = (
    "Filesystem Inodes IUsed IFree IUse% Mounted on\n"
    "/dev/sda1 1000 100 900 10% /user\n"
)


class CommandRunner:
    """명령 이름으로 분기하는 단일 가짜 러너.

    `handlers`는 {명령이름: 콜백} 이며, 콜백은 argv를 받아
    `subprocess.CompletedProcess`(bytes)를 돌려주거나 예외를 던진다. 등록되지
    않은 명령이 호출되면 즉시 실패시켜, 테스트가 의도치 않은 외부 명령을
    조용히 실행하는 일을 막는다.
    """

    def __init__(self, handlers: Dict[str, Callable[[List[str]], subprocess.CompletedProcess]]):
        self._handlers = handlers
        self.calls: List[List[str]] = []

    def __call__(self, command, **kwargs):
        argv = list(command)
        self.calls.append(argv)
        for name, handler in self._handlers.items():
            if name in argv:
                return handler(argv)
        raise AssertionError(f"예상치 못한 명령: {argv}")

    def calls_for(self, name: str) -> List[List[str]]:
        return [argv for argv in self.calls if name in argv]

    def count_for(self, name: str) -> int:
        return len(self.calls_for(name))


def df_runner(
    bytes_output: str = DF_BYTES_OUTPUT, inode_output: str = DF_INODE_OUTPUT
) -> Callable:
    """`df -Pk` / `df -Pi`를 구분해 응답하는 러너."""

    def handler(argv: List[str]) -> subprocess.CompletedProcess:
        return completed(argv, inode_output if "-Pi" in argv else bytes_output)

    return CommandRunner({"df": handler})
