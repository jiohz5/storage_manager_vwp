"""파일을 통째로 바꿔 쓴다 - 읽는 쪽은 옛 내용이나 새 내용 중 하나만 본다.

임시 파일에 다 쓰고 fsync 한 뒤 이름을 바꾼다. `os.replace` 는 같은 디렉터리
안에서 원자적이다 (NFS 에서도 서버의 rename 한 번이다).

**임시 파일 이름은 쓸 때마다 새로 만든다.** 예전에는 대상 이름 + `.tmp` 로
고정했고, 같은 일을 하는 함수가 다섯 군데(설정, 알림 상태, 팝업, 보고서, 중지
요청)에 따로 있었다. 두 프로세스가 같은 파일을 동시에 쓰면 같은 임시 파일을
함께 열게 된다 - 늦게 연 쪽이 먼저 쓰던 내용을 잘라 먹고, 먼저 이름을 바꾼 쪽
때문에 늦은 쪽은 "파일 없음" 으로 실패한다. cron 의 수집과 야간 스캔은 22:00 에
함께 뜨고, 창의 수집과 cron 의 수집도 겹칠 수 있다.

임시 파일은 `0o666` 으로 만들어 umask 를 그대로 따른다 - 예전처럼 `open("w")` 로
만든 것과 권한이 같다. 팀이 함께 쓰는 데이터 디렉터리에서 설정·보고서가 갑자기
나만 읽을 수 있는 파일이 되면 안 된다.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    fd = os.open(str(temp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp), str(path))
    except BaseException:
        try:
            os.unlink(str(temp))
        except OSError:
            pass
        raise


def write_json(path: Path, payload) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
