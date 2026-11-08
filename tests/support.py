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

`smvwp.detail_scan.subprocess`, `smvwp.quota.subprocess` 등은 전부 **같은 표준
subprocess 모듈 객체**다. 여러 경로를 각각 patch하면 나중에 적용된 patch가 앞의
것을 조용히 덮어써서, `du` 호출이 다른 명령용 가짜 결과를 받는 식의 혼선이
생긴다. 그래서 patch는 한 번만 걸고 명령 내용으로 분기한다.
"""

from __future__ import annotations

import subprocess
from typing import Callable, Dict, List, Sequence


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


# -- 스캔 DB 시험용 ------------------------------------------------------------
#
# 운영 코드에는 시험에서만 쓰는 함수를 두지 않는다. 아래는 시험이 데이터를
# 심거나 저장된 것을 들여다볼 때만 쓰는 것들이다.


def save_checkpoint_results(conn, account_id: str, generation: int) -> None:
    """끝난(done) 체크포인트의 크기를 기준선 결과로 옮긴다.

    운영에서는 체크포인트를 처리하면서 `save_tree_entries` 가 곧바로 넣는다.
    시험은 체크포인트만 심고 결과를 한 번에 만들 때 이것을 쓴다."""

    from smvwp import scan_store

    rows = conn.execute(
        "SELECT path, size_kb FROM scan_checkpoints "
        "WHERE account_id = ? AND kind = 'baseline' AND generation = ? AND status = 'done'",
        (account_id, generation),
    ).fetchall()
    conn.executemany(
        "INSERT OR REPLACE INTO baseline_results "
        "(account_id, generation, path, size_kb, completed_at) VALUES (?, ?, ?, ?, ?)",
        [(account_id, generation, row[0], row[1], scan_store.utc_now_iso()) for row in rows],
    )
    conn.commit()


def whole(sizes, large=()):
    """끝까지 잰 체크포인트 하나의 결과 (`du` 처럼 자식이 먼저, 루트가 마지막).

    `sizes` 는 `{경로: KB}` - **마지막 항목이 루트**다. `large` 는 `(KB, 경로)`."""

    from smvwp import detail_scan

    entries = list(sizes.items())
    return detail_scan.DuTreeOutcome(
        entries=entries, largest_files=list(large),
        root_size_kb=entries[-1][1], completed=True,
    )


def cut(sizes, large=()):
    """시간 초과로 끊긴 체크포인트 - 루트 줄이 안 나왔다."""

    from smvwp import detail_scan

    return detail_scan.DuTreeOutcome(
        entries=list(sizes.items()), largest_files=list(large), timed_out=True,
    )


def failed(message="Permission denied"):
    from smvwp import detail_scan

    return detail_scan.DuTreeOutcome(error_message=message)


def scan_pass(conn, account_id, generation, tops, outcomes, subdirs=None,
              stop_after=None):
    """한 세대를 **실제 처리 경로**(`process_one_checkpoint`)로 돈다.

    결과 표에 행을 직접 넣으면 쪼개진 밤에 무엇이 어떤 깊이로 남는지를 시험이
    지어내게 된다 - 그 지어낸 모양과 운영의 모양이 다르면 시험은 통과하고
    화면은 틀린다. `outcomes` 는 `{체크포인트 경로: whole(...)/cut(...)/failed()}`,
    `subdirs` 는 끊긴 체크포인트의 바로 아래 폴더들. `stop_after` 를 주면 그만큼만
    재고 세대를 끝내지 않는다 (하는 중인 바퀴)."""

    from unittest.mock import patch

    from smvwp import detail_scan, scan_store

    subdirs = subdirs or {}
    scan_store.seed_checkpoints(conn, account_id, scan_store.BASELINE, generation, list(tops))
    done = 0
    while stop_after is None or done < stop_after:
        checkpoint = scan_store.next_pending(conn, account_id, scan_store.BASELINE, generation)
        if checkpoint is None:
            break
        path = checkpoint["path"]
        with patch("smvwp.detail_scan.measure_tree", return_value=outcomes[path]),                 patch("smvwp.detail_scan.list_immediate_subdirs",
                      return_value=list(subdirs.get(path, []))),                 patch("smvwp.detail_scan.os.path.lexists", return_value=True):
            detail_scan.process_one_checkpoint(conn, checkpoint, 60, generation=generation)
        done += 1
    if stop_after is None:
        scan_store.mark_generation_completed(conn, account_id, generation)


GB = 1024 * 1024


def two_passes_and_one_running(conn, account_id, root):
    """끝난 바퀴 둘(1000GB -> 1500GB)과 하는 중인 셋째 바퀴(900GB 까지 잼).

    셋째 바퀴의 '지금까지 잰 양'은 진행 정도일 뿐이다 - 계정의 크기나 증감으로
    쓰면 바퀴가 도는 동안(큰 계정은 며칠) 숫자가 통째로 틀린다.
    큰 파일 `{root}/a/big` 은 200GB -> 300GB."""

    tops = [f"{root}/a", f"{root}/b"]
    big = f"{root}/a/big"
    scan_pass(conn, account_id, 1, tops, {
        tops[0]: whole({tops[0]: 600 * GB}, large=[(200 * GB, big)]),
        tops[1]: whole({tops[1]: 400 * GB}),
    })
    scan_pass(conn, account_id, 2, tops, {
        tops[0]: whole({tops[0]: 900 * GB}, large=[(300 * GB, big)]),
        tops[1]: whole({tops[1]: 600 * GB}),
    })
    scan_pass(conn, account_id, 3, tops, {
        tops[0]: whole({tops[0]: 900 * GB}, large=[(300 * GB, big)]),
    }, stop_after=1)


def unmeasured_then_measured(conn, account_id, root):
    """지난 바퀴에 권한 오류로 못 잰 폴더가 이번 바퀴에 재진 계정.

    `{root}/a` 는 못 잼 -> 900GB, `{root}/b` 는 400GB -> 410GB. 실제로 는 것은 10GB 다 -
    합계끼리 빼면 910GB 가 '늘어난 양' 이 된다."""

    tops = [f"{root}/a", f"{root}/b"]
    scan_pass(conn, account_id, 1, tops, {
        tops[0]: failed(), tops[1]: whole({tops[1]: 400 * GB}),
    })
    scan_pass(conn, account_id, 2, tops, {
        tops[0]: whole({tops[0]: 900 * GB}), tops[1]: whole({tops[1]: 410 * GB}),
    })


def sample_processes(conn, sample_id: int):
    """서버 표본 하나에 딸린 프로세스 줄들 (CPU 많은 순)."""

    return conn.execute(
        "SELECT * FROM server_sample_processes WHERE sample_id = ? ORDER BY cpu_percent DESC",
        (sample_id,),
    ).fetchall()


def sample_mounts(conn, sample_id: int):
    """서버 표본 하나에 딸린 마운트 줄들 (요청 많은 순)."""

    return conn.execute(
        "SELECT * FROM server_sample_mounts WHERE sample_id = ? ORDER BY ops DESC",
        (sample_id,),
    ).fetchall()


class VmSteps:
    """블록 안에서 SQLite 가상 머신이 밟은 단계 수 (`with VmSteps(conn) as steps:`).

    읽은 행 수에 비례한다 - 인덱스를 타면 찾는 행만큼, 못 타면 표나 세대 전체만큼
    커진다. 데이터 디렉터리가 NFS 면 그 차이가 그대로 네트워크 읽기라, "필요한
    만큼만 읽는다" 를 시간 대신 이것으로 확인한다 (시간은 장비마다 흔들린다)."""

    def __init__(self, conn):
        self._conn = conn
        self.count = 0

    def _tick(self) -> int:
        self.count += 1
        return 0

    def __enter__(self) -> "VmSteps":
        self._conn.set_progress_handler(self._tick, 1)
        return self

    def __exit__(self, *exc) -> None:
        self._conn.set_progress_handler(None, 1)
