"""계정을 볼륨 단위로 묶는다.

실측이 이 단위를 정했다. **한 디렉터리를 프로세스로 쪼개도 빨라지지 않는데**,
서로 다른 디렉터리 둘을 동시에 돌리면 처리량이 배가 됐다. 차이는 볼륨이다 -
NetApp 은 볼륨 단위로 처리 능력이 갈리므로 같은 볼륨 안에서는 아무리 나눠도
그 볼륨의 한계를 넘지 못한다.

  같은 볼륨: 동시 4 -> 36.0s / 동시 8 -> 35.7s / 동시 16 -> 37.1s (안 늘어남)
  다른 볼륨: 각각 36s 로 처리량 2배

그래서 "계정 N개 동시"가 아니라 **"볼륨마다 하나씩, 볼륨끼리 동시에"** 가 맞다.
"""

import tempfile
import unittest
from pathlib import Path

from smvwp import paths

# 사내 실제 모양을 본떴다 - filer 이름이 섞여 있고 일부 계정은 볼륨을 공유한다.
MOUNTS = """\
/dev/sda2 / xfs rw,relatime 0 0
ecfiler:/vol/cae /user/cae nfs4 rw,relatime 0 0
ecfiler:/vol/cae /user/cae_sub nfs4 rw,relatime 0 0
ecfiler2:/vol/proj /user/proj nfs4 rw,relatime 0 0
nas03:/vol/backup /user/backup nfs4 rw,relatime 0 0
/dev/sdb1 /var/lib/smvwp ext4 rw,relatime 0 0
"""


class VolumeKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mounts = Path(self.tmp.name) / "mounts"
        self.mounts.write_text(MOUNTS, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _key(self, path):
        return paths.volume_key(Path(path), source=self.mounts)

    def test_export_is_the_volume(self):
        self.assertEqual(self._key("/user/cae/projA"), "ecfiler:/vol/cae")
        self.assertEqual(self._key("/user/proj/projB"), "ecfiler2:/vol/proj")

    def test_same_export_mounted_twice_is_one_volume(self):
        """마운트 지점이 달라도 같은 export 면 같은 저장소다.

        여기서 갈라 보면 같은 볼륨을 동시에 두들기면서 빨라졌다고 착각한다."""

        self.assertEqual(
            self._key("/user/cae/x"), self._key("/user/cae_sub/y")
        )

    def test_different_filers_are_different_volumes(self):
        keys = {
            self._key("/user/cae/x"),
            self._key("/user/proj/x"),
            self._key("/user/backup/x"),
        }
        self.assertEqual(len(keys), 3)

    def test_local_path_uses_its_device(self):
        self.assertEqual(self._key("/var/lib/smvwp"), "/dev/sdb1")

    def test_longest_mount_point_wins(self):
        """`/` 와 `/user/cae` 가 둘 다 접두사면 긴 쪽이 실제 마운트다."""

        self.assertEqual(self._key("/user/cae/deep/path"), "ecfiler:/vol/cae")

    def test_unknown_environment_falls_back_to_the_path(self):
        """모를 때는 **서로 다른 볼륨으로** 본다.

        같은 볼륨을 다르게 보면 조금 느려질 뿐이지만, 다른 볼륨을 같다고 보면
        병렬을 통째로 포기하게 된다."""

        first = paths.volume_key(Path("/a/x"), source=Path("/nope"))
        second = paths.volume_key(Path("/b/y"), source=Path("/nope"))
        self.assertNotEqual(first, second)


class GroupingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mounts = Path(self.tmp.name) / "mounts"
        self.mounts.write_text(MOUNTS, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_accounts_group_by_storage_not_by_name(self):
        grouped = paths.group_by_volume(
            [
                ("acct_a", "/user/cae/a"),
                ("acct_b", "/user/cae_sub/b"),     # 같은 export
                ("acct_c", "/user/proj/c"),
                ("acct_d", "/user/backup/d"),
            ],
            source=self.mounts,
        )
        self.assertEqual(len(grouped), 3)
        self.assertEqual(grouped["ecfiler:/vol/cae"], ["acct_a", "acct_b"])
        self.assertEqual(grouped["ecfiler2:/vol/proj"], ["acct_c"])

    def test_order_is_preserved_within_a_group(self):
        """공정성 순환이 정한 순서를 뒤집으면 안 된다."""

        grouped = paths.group_by_volume(
            [("second", "/user/cae/2"), ("first", "/user/cae/1")],
            source=self.mounts,
        )
        self.assertEqual(grouped["ecfiler:/vol/cae"], ["second", "first"])

    def test_empty_input(self):
        self.assertEqual(paths.group_by_volume([], source=self.mounts), {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
