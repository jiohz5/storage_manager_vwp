"""진단 모드 - 코드가 뜻하는 바가 표(`PROBE_CODES.md`)와 어긋나지 않게 못박는다.

여기서 지켜야 할 것이 보통 시험과 다르다. 이 코드는 **사람이 손으로 옮겨
적어 나에게 전달**되므로, 값이 조용히 바뀌면 옛 결과를 새 표로 읽어 정반대
결론이 난다. 그래서 구간 경계와 `0`(재지 못함)의 뜻을 직접 시험한다.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import probe

# 사내 실제 모양을 본떴다.
MOUNTS = """\
/dev/sda2 / xfs rw,relatime 0 0
ecfiler:/vol/cae /user/cae nfs4 rw,relatime,vers=4.1,rsize=1048576,wsize=1048576,nconnect=4 0 0
ecfiler:/vol/old /user/old nfs rw,relatime,vers=3,rsize=65536,noac,nordirplus 0 0
nas03:/vol/backup /user/backup nfs4 rw,relatime,vers=4.0,rsize=131072 0 0
"""


class BucketTests(unittest.TestCase):
    """구간 나누기. 여기가 틀리면 모든 코드가 한 칸씩 밀린다."""

    def test_edges_are_exclusive_upper_bounds(self):
        self.assertEqual(probe.bucket(0, [10, 20, 30]), 1)
        self.assertEqual(probe.bucket(9.99, [10, 20, 30]), 1)
        self.assertEqual(probe.bucket(10, [10, 20, 30]), 2)
        self.assertEqual(probe.bucket(29.99, [10, 20, 30]), 3)
        self.assertEqual(probe.bucket(30, [10, 20, 30]), 4)

    def test_missing_value_is_zero_not_one(self):
        """'재지 못함'과 '가장 낮은 구간'을 섞으면 없는 사실을 지어내게 된다."""

        self.assertEqual(probe.bucket(None, [10, 20]), 0)


class MountReadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name) / "mounts"
        self.source.write_text(MOUNTS, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _codes(self, path):
        checks = probe.environment_checks(Path(path), mount_source=self.source)
        return {check.letter: check.digit for check in checks}

    def test_longest_mount_point_wins(self):
        mount = probe.mount_for(Path("/user/cae/deep/x"), source=self.source)
        self.assertEqual(mount["origin"], "ecfiler:/vol/cae")

    def test_modern_nfs4_reads_as_expected(self):
        codes = self._codes("/user/cae/과제A")
        self.assertEqual(codes["A"], 1)   # nfs4
        self.assertEqual(codes["B"], 1)   # v4.1
        self.assertEqual(codes["C"], 3)   # nconnect=4
        self.assertEqual(codes["D"], 4)   # rsize 1M
        self.assertEqual(codes["F"], 1)   # 속성 캐시 기본
        self.assertEqual(codes["G"], 1)   # readdirplus 사용

    def test_the_slow_combination_is_visible(self):
        """noac + nordirplus + v3 - 느린 이유를 코드만 보고 짚을 수 있어야 한다."""

        codes = self._codes("/user/old/x")
        self.assertEqual(codes["A"], 2)   # nfs3
        self.assertEqual(codes["B"], 3)   # v3
        self.assertEqual(codes["C"], 1)   # nconnect 없음
        self.assertEqual(codes["F"], 2)   # noac
        self.assertEqual(codes["G"], 2)   # nordirplus

    def test_local_filesystem_is_not_reported_as_nfs(self):
        codes = self._codes("/var/tmp")
        self.assertEqual(codes["A"], 3)
        self.assertEqual(codes["C"], 4)   # NFS 아님
        self.assertEqual(codes["G"], 4)

    def test_unknown_environment_says_so_instead_of_guessing(self):
        missing = Path(self.tmp.name) / "nope"
        checks = probe.environment_checks(Path("/user/cae/x"), mount_source=missing)
        codes = {check.letter: check.digit for check in checks}
        self.assertEqual(codes["A"], 0)
        self.assertEqual(codes["B"], 0)


class FilerTests(unittest.TestCase):
    def test_filer_is_the_part_before_the_colon(self):
        self.assertEqual(probe.filer_of("ecfiler:/vol/cae"), "ecfiler")
        self.assertEqual(probe.filer_of("ecfiler2:/vol/cae"), "ecfiler2")

    def test_local_device_has_no_filer_part(self):
        self.assertEqual(probe.filer_of("/dev/sdb1"), "/dev/sdb1")

    def test_peers_are_chosen_by_volume_and_filer(self):
        class FakeAccount:
            def __init__(self, path):
                self.path = path

        volumes = {
            "/t": "ecfiler:/vol/cae",
            "/same-vol": "ecfiler:/vol/cae",      # 같은 볼륨 - 짝이 아니다
            "/same-filer": "ecfiler:/vol/proj",   # 같은 파일러 다른 볼륨
            "/other": "nas03:/vol/backup",        # 다른 파일러
        }
        # 개발 PC 에서는 `str(Path("/t"))` 가 `	` 가 된다 - 구분자만 맞춰 준다.
        def lookup(path):
            return volumes[str(path).replace("\\", "/")]

        existing = set(volumes)

        with patch.object(
            Path, "exists", lambda self: str(self).replace("\\", "/") in existing
        ):
            same, other = probe.find_peers(
                Path("/t"),
                [FakeAccount(p) for p in ("/same-vol", "/same-filer", "/other")],
                lookup,
            )
        self.assertEqual(same, "/same-filer")
        self.assertEqual(other, "/other")

    def test_no_peer_means_none_not_a_wrong_guess(self):
        same, other = probe.find_peers(
            Path("/t"), [], lambda path: "ecfiler:/vol/cae"
        )
        self.assertIsNone(same)
        self.assertIsNone(other)


class CodeStringTests(unittest.TestCase):
    def _result(self, pairs):
        return probe.ProbeResult(
            checks=[probe.Check(letter, "", digit) for letter, digit in pairs]
        )

    def test_codes_are_grouped_in_fours(self):
        result = self._result([("A", 1), ("B", 2), ("C", 3), ("D", 4), ("E", 5)])
        self.assertEqual(probe.code_string(result), "A1B2C3D4 E5")

    def test_checksum_catches_a_single_wrong_character(self):
        first = probe.checksum("A1B2C3D4")
        second = probe.checksum("A1B2C4D4")
        self.assertNotEqual(first, second)

    def test_checksum_catches_transposed_characters(self):
        """자리를 바꿔 적는 것이 손으로 옮길 때 가장 흔한 실수다."""

        self.assertNotEqual(probe.checksum("A1B2"), probe.checksum("B2A1"))

    def test_checksum_ignores_spacing_only(self):
        """읽기 좋으라고 넣은 띄어쓰기가 검사합을 바꾸면 안 된다."""

        self.assertEqual(probe.checksum("A1B2C3D4 E5"), probe.checksum("A1B2C3D4E5"))

    def test_transfer_block_carries_the_version(self):
        """표가 바뀌면 옛 코드를 새 표로 읽게 된다 - 버전이 그것을 막는다."""

        text = probe.format_transfer(self._result([("A", 1)]))
        self.assertIn(f"PROBE v{probe.CODE_VERSION}", text)
        self.assertIn("CODE A1 #", text)

    def test_values_appear_only_when_present(self):
        result = probe.ProbeResult(checks=[
            probe.Check("A", "", 1, raw="ecfiler:/vol/cae"),
            probe.Check("B", "", 2),
        ])
        self.assertEqual(probe.value_line(result), "A=ecfiler:/vol/cae")


class SubdirPickingTests(unittest.TestCase):
    def test_only_immediate_children_are_candidates(self):
        entries = [
            ("/acct", 900),
            ("/acct/a", 500),
            ("/acct/b", 300),
            ("/acct/a/deep", 400),   # 손자 - 짝이 되면 안 된다
        ]
        picked = probe.largest_subdirs(entries, "/acct", 2)
        self.assertEqual(picked, ["/acct/a", "/acct/b"])

    def test_largest_first_so_the_pair_is_comparable(self):
        entries = [("/acct/small", 1), ("/acct/big", 100), ("/acct/mid", 50)]
        self.assertEqual(
            probe.largest_subdirs(entries, "/acct", 2), ["/acct/big", "/acct/mid"]
        )

    def test_too_few_children_gives_what_exists(self):
        self.assertEqual(probe.largest_subdirs([("/acct/only", 5)], "/acct", 2),
                         ["/acct/only"])


class TimedWalkTests(unittest.TestCase):
    """조각은 항상 같은 길이여야 비교가 성립한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("a", "b"):
            target = self.root / name
            target.mkdir()
            for index in range(20):
                (target / f"f{index}").write_bytes(b"x" * 100)

    def tearDown(self):
        self.tmp.cleanup()

    def test_small_tree_still_fills_the_slice(self):
        """작은 트리가 순식간에 끝나면 동시 실행이 겹치지도 않는다.

        그러면 "간섭이 없다"는 잘못된 결론이 나므로, 시간이 남으면 다시 걷는다."""

        piece = probe.timed_walk(str(self.root), workers=1, limit_seconds=0.5)
        self.assertGreaterEqual(piece.seconds, 0.4)
        self.assertGreater(piece.items, 40)

    def test_rate_is_items_per_second(self):
        piece = probe.Slice(seconds=2.0, items=100)
        self.assertEqual(piece.rate, 50.0)

    def test_zero_length_slice_does_not_divide_by_zero(self):
        self.assertEqual(probe.Slice(seconds=0.0, items=10).rate, 0.0)


class FullProbeTests(unittest.TestCase):
    """전체가 한 번은 실제로 돌아야 한다 (조각을 아주 짧게 해서)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("alpha", "beta"):
            target = self.root / name / "inner"
            target.mkdir(parents=True)
            for index in range(10):
                (target / f"f{index}.dat").write_bytes(b"x" * 2048)

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_letter_appears_exactly_once(self):
        result = probe.run_probe(
            str(self.root), slice_seconds=0.05, quick=True, du_timeout=20
        )
        letters = [check.letter for check in result.checks]
        self.assertEqual(letters, sorted(letters), "코드는 알파벳 순이어야 한다")
        self.assertEqual(len(letters), len(set(letters)), "중복된 글자가 있다")
        self.assertEqual(set(letters), set("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))

    def test_digits_stay_in_range(self):
        result = probe.run_probe(
            str(self.root), slice_seconds=0.05, quick=True, du_timeout=20
        )
        for check in result.checks:
            with self.subTest(letter=check.letter):
                self.assertIn(check.digit, range(0, 6), f"{check.letter}={check.digit}")

    def test_peer_checks_are_zero_when_there_is_no_peer(self):
        """짝이 없으면 짐작으로 채우지 않는다."""

        result = probe.run_probe(
            str(self.root), slice_seconds=0.05, quick=True, du_timeout=20
        )
        self.assertEqual(result.by_letter("L").digit, 0)
        self.assertEqual(result.by_letter("M").digit, 0)

    def test_tree_shape_is_measured(self):
        result = probe.run_probe(
            str(self.root), slice_seconds=0.05, quick=True, du_timeout=20
        )
        # 파일 20개 - 가장 낮은 구간
        self.assertEqual(result.by_letter("Q").digit, 1)
        self.assertEqual(result.by_letter("T").digit, 1)   # 하드링크 없음
        self.assertEqual(result.by_letter("U").digit, 1)   # 읽기 실패 없음

    def test_transfer_block_is_short_enough_to_copy_by_hand(self):
        result = probe.run_probe(
            str(self.root), slice_seconds=0.05, quick=True, du_timeout=20
        )
        text = probe.format_transfer(result)
        self.assertLessEqual(len(text.splitlines()), 3)
        for line in text.splitlines():
            self.assertLess(len(line), 200, f"옮겨 적기에 너무 깁니다: {line}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
