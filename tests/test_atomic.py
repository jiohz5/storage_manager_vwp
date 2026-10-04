"""같은 파일을 두 프로세스가 동시에 쓸 때 (`smvwp.atomic`).

실제 장비(Linux + NFS)에서 일어나는 순서를 그대로 재연한다: 한쪽이 임시 파일을
다 쓰고 이름을 바꾸기 직전에, 다른 쪽이 같은 파일을 처음부터 끝까지 쓴다.
예전에는 둘이 같은 임시 파일 이름을 써서 늦은 쪽이 "파일 없음" 으로 실패했다.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import atomic, config as config_module, notifications, paths

REAL_REPLACE = os.replace


def interleave_once(nested):
    """첫 이름 바꾸기 직전에 `nested()` 를 끼워 넣는 `os.replace`."""

    state = {"done": False}

    def replace(src, dst):
        if not state["done"]:
            state["done"] = True
            nested()
        return REAL_REPLACE(src, dst)

    return replace


class _Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)

    def leftovers(self):
        return sorted(p.name for p in self.data_dir.iterdir() if p.name.endswith(".tmp"))


class ConcurrentWriteTests(_Case):
    def test_two_config_saves_at_once_both_succeed(self):
        config = config_module.load_config(self.data_dir)
        other = config_module.load_config(self.data_dir)
        other.settings.collector_interval_seconds = 1800
        config.settings.collector_interval_seconds = 900

        other_saves = interleave_once(lambda: config_module.save_config(self.data_dir, other))
        with patch("os.replace", side_effect=other_saves):
            config_module.save_config(self.data_dir, config)

        saved = json.loads(config_module.config_file(self.data_dir).read_text(encoding="utf-8"))
        # 나중에 이름을 바꾼 쪽이 남는다. 어느 쪽이든 온전한 파일이어야 한다.
        self.assertEqual(saved["settings"]["collector_interval_seconds"], 900)
        self.assertEqual(self.leftovers(), [])

    def test_two_notify_state_saves_at_once(self):
        other_saves = interleave_once(
            lambda: notifications.save_notify_state(self.data_dir, {"b": {}})
        )
        with patch("os.replace", side_effect=other_saves):
            notifications.save_notify_state(self.data_dir, {"a": {}})
        self.assertEqual(notifications.load_notify_state(self.data_dir), {"a": {}})
        self.assertEqual(self.leftovers(), [])

    def test_failed_write_keeps_the_old_file_and_leaves_nothing(self):
        target = self.data_dir / "state.json"
        atomic.write_json(target, {"v": 1})
        with patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic.write_json(target, {"v": 2})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"v": 1})
        self.assertEqual(self.leftovers(), [])


class WriteProbeTests(_Case):
    def test_two_processes_checking_at_once(self):
        """cron 의 수집과 야간 스캔은 22:00 에 함께 뜨고, 둘 다 설정을 읽으며 이
        확인을 한다. 예전에는 같은 확인용 파일을 함께 써서, 먼저 지운 쪽 때문에
        늦은 쪽이 "쓸 수 없습니다" 로 멈췄다 - 그 밤의 스캔이 시작되지 않았다."""

        real_unlink = Path.unlink
        state = {"done": False}

        def unlink(path_self, *args, **kwargs):
            if not state["done"]:
                state["done"] = True
                paths.ensure_writable(self.data_dir)
            return real_unlink(path_self, *args, **kwargs)

        with patch.object(Path, "unlink", unlink):
            paths.ensure_writable(self.data_dir)
        self.assertEqual(sorted(p.name for p in self.data_dir.iterdir()), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
