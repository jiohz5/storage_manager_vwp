"""NFS 마운트마다 실제로 오간 것.

## 여기서 지키는 것

- **rtt 와 queue 를 가른다.** 왕복이 느린 것(파일서버)과 보내지도 못하고
  기다린 것(우리 쪽 슬롯)은 처방이 정반대다.
- **구간 평균**이어야 한다. 평생 누적 평균은 지난 밤을 말해 주지 않는다.
- 없는 것을 0 으로 채우지 않는다. 연산이 한 번도 없었는데 `0ms` 로 쓰면
  "아주 빨랐다"로 읽힌다.
"""

import unittest

from smvwp import nfsstat


def mountstats(getattr_ops=1000, getattr_rtt=2000, getattr_queue=100,
               server_read=500000, sends=1200, backlog=700):
    """statvers 1.1 형태의 `/proc/self/mountstats` 한 토막."""

    events = ["0"] * 27
    events[0] = "150"     # inode_revalidate
    events[5] = "900"     # vfs_lookup
    events[6] = "800"     # vfs_access
    events[12] = "400"    # vfs_getdents
    return f"""device /dev/sda1 mounted on / with fstype ext4
device ecfilerf1201:/ifs/caef1201 mounted on /mnt/caef1201 with fstype nfs statvers=1.1
\topts:\trw,vers=3,rsize=131072,wsize=131072,namlen=255,hard,proto=tcp
\tage:\t123456
\tcaps:\tcaps=0xf,wtmult=512,dtsize=32768,bsize=0,namlen=255
\tsec:\tflavor=1,pseudoflavor=1
\tevents:\t{" ".join(events)}
\tbytes:\t111 222 0 0 {server_read} 40000 0 0
\tRPC iostats version: 1.0  p/v: 100003 (nfs)
\txprt:\ttcp 745 1 6 0 15 {sends} {sends} 3 900 {backlog} 128 0 0
\tper-op statistics
\t        NULL: 1 1 0 44 44 0 0 0
\t     GETATTR: {getattr_ops} {getattr_ops} 0 120000 140000 {getattr_queue} {getattr_rtt} 2500
\t      LOOKUP: 200 200 0 30000 40000 20 600 700
\t READDIRPLUS: 50 50 0 5000 900000 5 1500 1600
\t       WRITE: 0 0 0 0 0 0 0 0
"""


class ParseTests(unittest.TestCase):
    def test_non_nfs_mounts_are_skipped(self):
        mounts = nfsstat.parse_mountstats(mountstats())
        self.assertEqual(list(mounts), ["/mnt/caef1201"])

    def test_the_device_and_version_are_kept(self):
        mount = nfsstat.parse_mountstats(mountstats())["/mnt/caef1201"]
        self.assertEqual(mount.device, "ecfilerf1201:/ifs/caef1201")
        self.assertEqual(mount.nfs_version, "3")

    def test_server_bytes_not_application_bytes(self):
        """앞의 두 칸은 캐시에서 답한 것도 들어간다 - 네트워크를 탄 것은 뒤쪽이다."""

        mount = nfsstat.parse_mountstats(mountstats(server_read=500000))["/mnt/caef1201"]
        self.assertEqual(mount.read_bytes, 500000)
        self.assertEqual(mount.write_bytes, 40000)

    def test_transport_counters(self):
        mount = nfsstat.parse_mountstats(mountstats(sends=1200, backlog=700))["/mnt/caef1201"]
        self.assertEqual(mount.sends, 1200)
        self.assertEqual(mount.bad_xids, 3)
        self.assertEqual(mount.backlog_util, 700)
        self.assertEqual(mount.max_slots, 128)

    def test_only_the_operations_we_track(self):
        mount = nfsstat.parse_mountstats(mountstats())["/mnt/caef1201"]
        self.assertEqual(
            sorted(mount.ops), ["GETATTR", "LOOKUP", "READDIRPLUS", "WRITE"]
        )
        self.assertEqual(mount.ops["GETATTR"].rtt_ms, 2000)

    def test_the_events_that_a_walk_moves(self):
        mount = nfsstat.parse_mountstats(mountstats())["/mnt/caef1201"]
        self.assertEqual(mount.events["vfs_getdents"], 400)
        self.assertEqual(mount.events["vfs_lookup"], 900)

    def test_empty_input(self):
        self.assertEqual(nfsstat.parse_mountstats(""), {})

    def test_a_truncated_block_does_not_raise(self):
        text = "device a:/b mounted on /m with fstype nfs\n\tbytes:\t1 2\n"
        mounts = nfsstat.parse_mountstats(text)
        self.assertEqual(mounts["/m"].read_bytes, 0)


class DeltaTests(unittest.TestCase):
    def deltas(self, seconds=10.0, **kwargs):
        before = nfsstat.parse_mountstats(mountstats())
        after = nfsstat.parse_mountstats(mountstats(**kwargs))
        return nfsstat.delta(before, after, seconds)

    def test_rates_are_per_second(self):
        result = self.deltas(getattr_ops=1000 + 500)["/mnt/caef1201"]
        self.assertEqual(result.ops["GETATTR"].ops, 500)
        self.assertAlmostEqual(result.ops["GETATTR"].ops_per_second, 50.0)

    def test_latency_is_the_interval_average_not_the_lifetime_one(self):
        """평생 평균은 2.0ms 지만, 이 구간에서는 10ms 였다."""

        result = self.deltas(getattr_ops=1100, getattr_rtt=2000 + 1000)["/mnt/caef1201"]
        self.assertAlmostEqual(result.ops["GETATTR"].avg_rtt_ms, 10.0)

    def test_an_operation_that_did_not_happen_says_unknown(self):
        """0ms 로 쓰면 '아주 빨랐다'로 읽힌다."""

        result = self.deltas()["/mnt/caef1201"]
        self.assertEqual(result.ops["WRITE"].ops, 0)
        self.assertIsNone(result.ops["WRITE"].avg_rtt_ms)

    def test_queue_share_separates_the_client_from_the_server(self):
        """대기가 큐 쪽에 몰리면 병목은 파일서버가 아니라 우리 쪽 슬롯이다."""

        result = self.deltas(
            getattr_ops=1100, getattr_rtt=2000 + 100, getattr_queue=100 + 900
        )["/mnt/caef1201"]
        # 이 구간의 GETATTR: rtt 1ms, queue 9ms -> 큐가 90%
        self.assertGreater(result.queue_share, 0.8)

    def test_a_healthy_mount_has_a_small_queue_share(self):
        result = self.deltas(getattr_ops=1100, getattr_rtt=2000 + 900, getattr_queue=100 + 100)
        self.assertLess(result["/mnt/caef1201"].queue_share, 0.2)

    def test_throughput_is_a_rate(self):
        result = self.deltas(server_read=500000 + 10_000_000)["/mnt/caef1201"]
        self.assertAlmostEqual(result.read_bytes_per_second, 1_000_000.0)

    def test_a_mount_that_appeared_mid_interval_is_skipped(self):
        """평생 누적값을 한 구간에 실으면 엄청난 부하가 있었던 것처럼 보인다."""

        after = nfsstat.parse_mountstats(mountstats())
        self.assertEqual(nfsstat.delta({}, after, 10.0), {})

    def test_counters_that_went_backwards_do_not_become_negative(self):
        """마운트를 다시 걸면 카운터가 0 부터 시작한다."""

        before = nfsstat.parse_mountstats(mountstats(getattr_ops=5000))
        after = nfsstat.parse_mountstats(mountstats(getattr_ops=10))
        result = nfsstat.delta(before, after, 10.0)["/mnt/caef1201"]
        self.assertEqual(result.ops["GETATTR"].ops, 0)

    def test_a_zero_interval_gives_nothing(self):
        self.assertEqual(self.deltas(seconds=0.0), {})

    def test_events_are_deltas_too(self):
        result = self.deltas()["/mnt/caef1201"]
        self.assertEqual(result.events["vfs_getdents"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
