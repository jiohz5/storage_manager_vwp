"""두 프로세스가 같은 데이터 디렉터리에 동시에 붙을 때 생기는 실패를 센다.

저장소 루트에서 돌린다 (pytest 가 모으지 않는 수동 실험이다):
    python3 tests/race_check.py <모드> <빈 데이터 디렉터리>

  writable : 쓰기 확인을 반복 (cron 의 collect 와 scan 이 22:00 에 함께 뜨는 경우)
  config   : 설정 저장을 반복하며, 틈틈이 설정을 읽는다
  state    : 알림 상태 저장/읽기를 반복

Linux 에서는 세 모드 모두 실패가 0 이어야 한다. (Windows 에서는 다른 프로세스가
열어 둔 파일 위로 이름을 바꿀 수 없어서 config/state 에 실패가 남는다 - 운영
장비와는 상관없는 제약이다.) 'JSON' 이 들어간 실패가 하나라도 있으면 내용이 깨진
것이다.
"""

import sys
import time
from multiprocessing import Process, Queue
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROUNDS = 1500


def worker(mode, data_dir, out):
    from smvwp import config as config_module, notifications, paths

    data_dir = Path(data_dir)
    failures = {}

    def note(exc):
        text = str(exc)
        key = f"{type(exc).__name__}{' [JSON]' if 'JSON' in text else ''}: {text[-80:]}"
        failures[key] = failures.get(key, 0) + 1

    config = config_module.load_config(data_dir) if mode == "config" else None
    for i in range(ROUNDS):
        try:
            if mode == "writable":
                paths.ensure_writable(data_dir)
            elif mode == "config":
                config.settings.collector_interval_seconds = 900 + (i % 7)
                config_module.save_config(data_dir, config)
                if i % 3 == 0:
                    config_module.load_config(data_dir)
            elif mode == "state":
                notifications.save_notify_state(data_dir, {"a": {"tier": "warn", "i": i}})
                notifications.load_notify_state(data_dir)
        except Exception as exc:  # noqa: BLE001 - 세려고 받는다
            note(exc)
    out.put(failures)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("writable", "config", "state"):
        sys.exit(__doc__)
    mode, data_dir = sys.argv[1], sys.argv[2]
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    if mode == "config":
        from smvwp import config as config_module

        config_module.load_config(Path(data_dir))
    out = Queue()
    procs = [Process(target=worker, args=(mode, data_dir, out)) for _ in range(2)]
    started = time.perf_counter()
    for p in procs:
        p.start()
    results = [out.get() for _ in procs]
    for p in procs:
        p.join()
    total = {}
    for r in results:
        for k, v in r.items():
            total[k] = total.get(k, 0) + v
    leftovers = sorted(p.name for p in Path(data_dir).iterdir() if p.name.endswith(".tmp"))
    print(f"[{mode}] {2 * ROUNDS} rounds in {time.perf_counter() - started:.1f}s, "
          f"failures: {sum(total.values())}, leftover temp files: {len(leftovers)}")
    for k, v in sorted(total.items(), key=lambda kv: -kv[1])[:5]:
        print(f"   {v:5}  {k}")
    sys.exit(1 if total or leftovers else 0)
