"""데이터 디렉터리가 네트워크 파일시스템 위인지 판정한다.

이 판정이 틀리면 결과가 느려지는 정도가 아니라 **DB가 깨진다.** SQLite의 WAL은
여러 프로세스가 공유 메모리(`-shm`)를 함께 보는 것을 전제로 하는데 NFS에서는
그게 성립하지 않는다 (SQLite 공식 문서). 그래서 여기 테스트는 두 방향을 다
지킨다 - NFS를 놓치지 않을 것, 그리고 로컬을 NFS로 오판하지 않을 것.
"""

import tempfile
import unittest
from pathlib import Path

from smvwp import paths

MOUNTS = """\
rootfs / rootfs rw 0 0
/dev/sda2 / xfs rw,relatime 0 0
/dev/sda1 /boot xfs rw,relatime 0 0
nas01:/vol/home /home nfs4 rw,relatime,vers=4.1 0 0
nas01:/vol/proj /user nfs4 rw,relatime,vers=4.1 0 0
tmpfs /dev/shm tmpfs rw,nosuid 0 0
/dev/sdb1 /var/lib/smvwp ext4 rw,relatime 0 0
"""


class FilesystemTypeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mounts = Path(self.tmp.name) / "mounts"
        self.mounts.write_text(MOUNTS, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _type(self, path):
        return paths.filesystem_type(Path(path), source=self.mounts)

    def test_longest_mount_point_wins(self):
        """`/`와 `/user`가 둘 다 접두사면 `/user`가 실제 마운트다.

        짧은 쪽을 고르면 NFS 경로가 로컬(xfs)로 판정돼 WAL이 켜진다."""

        self.assertEqual(self._type("/user/project_a"), "nfs4")
        self.assertEqual(self._type("/var/lib/smvwp"), "ext4")
        self.assertEqual(self._type("/boot/grub"), "xfs")

    def test_mount_point_itself(self):
        self.assertEqual(self._type("/user"), "nfs4")

    def test_prefix_must_end_at_a_path_boundary(self):
        """`/users_backup`은 `/user` 아래가 아니다.

        단순 문자열 startswith로 비교하면 여기서 틀린다."""

        self.assertEqual(self._type("/users_backup/x"), "xfs")

    def test_unreadable_source_gives_nothing(self):
        self.assertIsNone(
            paths.filesystem_type(Path("/user"), source=Path("/does/not/exist"))
        )


class JournalModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mounts = Path(self.tmp.name) / "mounts"
        self.mounts.write_text(MOUNTS, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _mode(self, path):
        return paths.journal_mode_for(Path(path), source=self.mounts)

    def test_network_path_falls_back_to_delete(self):
        """NFS에서 WAL을 쓰면 조용히 깨진다 - 느린 쪽을 택한다."""

        self.assertEqual(self._mode("/user/project_a"), "DELETE")
        self.assertEqual(self._mode("/home/jioh5/smvwp-data"), "DELETE")

    def test_local_path_keeps_wal(self):
        self.assertEqual(self._mode("/var/lib/smvwp"), "WAL")

    def test_unknown_environment_keeps_wal(self):
        """판단이 안 되면 WAL을 유지한다.

        모르는 상태에서 DELETE로 물러서면 개발 PC처럼 멀쩡한 환경이 이유 없이
        느려진다. NFS 판정은 **확실할 때만** 한다."""

        self.assertEqual(
            paths.journal_mode_for(Path("/anywhere"), source=Path("/does/not/exist")),
            "WAL",
        )

    def test_unlisted_filesystem_is_treated_as_local(self):
        """모르는 종류를 네트워크라고 단정하지 않는다."""

        self.assertFalse(
            paths.is_network_filesystem(Path("/boot"), source=self.mounts)
        )


class NetworkFilesystemListTests(unittest.TestCase):
    def test_covers_the_types_we_actually_meet(self):
        for fs_type in ("nfs", "nfs4", "cifs"):
            with self.subTest(fs_type=fs_type):
                self.assertIn(fs_type, paths.NETWORK_FILESYSTEMS)

    def test_local_types_are_not_listed(self):
        for fs_type in ("xfs", "ext4", "btrfs", "zfs", "tmpfs", "overlay"):
            with self.subTest(fs_type=fs_type):
                self.assertNotIn(fs_type, paths.NETWORK_FILESYSTEMS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
