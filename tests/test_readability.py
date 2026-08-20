"""계정 등록 시 읽기 가능 범위 표본 조사, 그리고 데이터 경로 후보 제안.

둘 다 "관리자가 아닌 사용자"를 위한 장치다. 남의 프로젝트 계정은 최상위만
읽히고 하위가 막힌 경우가 흔한데, 등록 시점에 알려주지 않으면 며칠 뒤 야간
스캔 결과가 이상하게 작은 것으로만 드러난다.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smvwp import paths, readability


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ("alpha", "beta", "gamma"):
            (self.root / name / "sub").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _deny(self, *suffixes):
        real = os.scandir

        def flaky(path, *args, **kwargs):
            if str(path).endswith(suffixes):
                raise PermissionError(13, "Permission denied")
            return real(path, *args, **kwargs)

        return patch("smvwp.readability.os.scandir", side_effect=flaky)

    def test_fully_readable_tree(self):
        result = readability.probe(self.root)
        self.assertTrue(result.fully_readable)
        self.assertFalse(result.has_findings)
        self.assertEqual(result.unreadable, 0)
        self.assertGreater(result.checked, 0)

    def test_unreadable_subdirectories_are_counted(self):
        with self._deny("alpha", "beta"):
            result = readability.probe(self.root)

        self.assertTrue(result.has_findings)
        self.assertGreaterEqual(result.unreadable, 2)
        self.assertTrue(any("alpha" in p for p in result.unreadable_samples))

    def test_unreadable_root_is_reported_distinctly(self):
        with patch(
            "smvwp.readability.os.scandir", side_effect=PermissionError(13, "denied")
        ):
            result = readability.probe(self.root)

        self.assertFalse(result.root_readable)
        self.assertTrue(result.has_findings)
        self.assertIn("이 경로 자체", readability.describe(result))

    def test_budget_limits_are_respected(self):
        """GUI에서 도는 검사라 수십 TB를 다 훑으면 안 된다."""

        result = readability.probe(self.root, max_dirs=2)
        self.assertTrue(result.truncated)
        self.assertLessEqual(result.checked, 2)

    def test_truncation_is_disclosed_not_hidden(self):
        result = readability.probe(self.root, max_dirs=2)
        self.assertIn("표본", readability.describe(result))

    def test_symlinks_are_not_followed(self):
        """스캔 정책과 동일 - 링크를 따라가면 순환하거나 범위를 벗어난다."""

        target = self.root / "alpha"
        link = self.root / "link_to_alpha"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("이 환경에서는 심볼릭 링크를 만들 수 없습니다")

        result = readability.probe(self.root)
        self.assertNotIn(str(link), result.unreadable_samples)

    def test_sample_list_is_capped(self):
        many = self.root / "many"
        many.mkdir()
        for index in range(20):
            (many / f"d{index}").mkdir()

        with self._deny(*(f"d{i}" for i in range(20))):
            # 예산을 넉넉히 준다. 기본값(3초)에 맡기면 전체 스위트가 돌 때처럼
            # 장비가 바쁠 때 20개를 다 훑기 전에 시간이 끊겨 `truncated`가 되고,
            # 구현이 멀쩡한데도 테스트가 실패한다 - 이 테스트가 보려는 것은
            # 표본 개수 상한이지 순회 속도가 아니다.
            result = readability.probe(self.root, max_seconds=60.0)

        self.assertLessEqual(len(result.unreadable_samples), readability.SAMPLE_LIMIT)
        self.assertGreater(result.unreadable, len(result.unreadable_samples))
        self.assertIn("외", readability.describe(result))

    def test_describe_does_not_invent_a_percentage(self):
        """표본에서 전체 비율을 추정하지 않는다 - 없는 정밀도를 만들지 않기 위함."""

        with self._deny("alpha"):
            result = readability.probe(self.root)
        text = readability.describe(result)

        self.assertNotIn("%", text)
        self.assertIn(str(result.checked), text)
        self.assertIn(str(result.unreadable), text)


class DataDirSuggestionTests(unittest.TestCase):
    def test_suggests_a_home_based_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("STORAGE_MANAGER_DATA_DIR", None)
                suggestions = paths.suggest_data_dirs(home=Path(tmp))

            self.assertTrue(suggestions)
            self.assertEqual(suggestions[0].path, Path(tmp) / "storage-manager-data")
            self.assertTrue(suggestions[0].writable)

    def test_environment_value_is_offered_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            chosen = Path(tmp) / "explicit"
            with patch.dict(os.environ, {"STORAGE_MANAGER_DATA_DIR": str(chosen)}):
                suggestions = paths.suggest_data_dirs(home=Path(tmp))
            self.assertEqual(suggestions[0].path, chosen)

    def test_describe_location_reports_free_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = paths.describe_location(Path(tmp))
            self.assertTrue(info.exists)
            self.assertTrue(info.writable)
            self.assertIsNotNone(info.free_bytes)
            self.assertGreater(info.free_bytes, 0)

    def test_nonexistent_path_is_judged_by_nearest_parent(self):
        """아직 없는 경로를 고르는 것이 정상 흐름이다."""

        with tempfile.TemporaryDirectory() as tmp:
            info = paths.describe_location(Path(tmp) / "not" / "created" / "yet")
            self.assertFalse(info.exists)
            self.assertTrue(info.writable)  # 상위가 쓰기 가능하므로
            self.assertIsNotNone(info.free_bytes)

    def test_format_bytes_is_readable(self):
        self.assertEqual(paths.format_bytes(None), "-")
        self.assertIn("KB", paths.format_bytes(2048))
        self.assertIn("GB", paths.format_bytes(5 * 1024 ** 3))


if __name__ == "__main__":
    unittest.main()
