"""cron 등록 확인.

야간 스캔이 안 도는 가장 흔한 이유가 "등록이 안 된 것"인데, 그 사실은 아무 데도
드러나지 않았다. 화면은 "최근 스캔 없음"이라고만 말하고 사람은 프로그램이 고장 난
줄 안다. 여기서 지켜야 할 것은 **모르는 것을 아는 척하지 않는 것**이다 -
`crontab` 을 못 부른 것과 "등록 안 됨"은 완전히 다른 이야기다.
"""

import subprocess
import unittest
from types import SimpleNamespace

from smvwp import cron_status

COLLECTOR = (
    "*/15 * * * * /py smvwp_cli.py collect --data-dir /d "
    + cron_status.COLLECTOR_MARKER
)
NIGHTLY = (
    "0 22 * * * /py smvwp_cli.py scan --data-dir /d " + cron_status.NIGHTLY_MARKER
)


def _runner(stdout="", returncode=0, stderr="", exc=None):
    def run(command, **kwargs):
        if exc is not None:
            raise exc
        return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)

    return run


class ReadStatusTests(unittest.TestCase):
    def test_both_entries_are_found(self):
        status = cron_status.read_status(
            _runner("\n".join([COLLECTOR, NIGHTLY]) + "\n")
        )
        self.assertTrue(status.available)
        self.assertTrue(status.both)

    def test_only_the_collector(self):
        status = cron_status.read_status(_runner(COLLECTOR + "\n"))
        self.assertTrue(status.collector)
        self.assertFalse(status.nightly)
        self.assertEqual(cron_status.summary_key(status), "cron.nightly_missing")

    def test_empty_crontab_is_a_real_answer(self):
        """비어 있는 것은 '모름'이 아니라 '없음'이다."""

        status = cron_status.read_status(_runner("", returncode=1))
        self.assertTrue(status.available)
        self.assertFalse(status.collector)
        self.assertEqual(cron_status.summary_key(status), "cron.none")

    def test_commented_out_lines_do_not_count(self):
        """`#` 로 꺼 둔 줄을 등록된 것으로 세면 안 된다.

        우리 표식 자체가 주석이라, 줄을 통째로 거르지 않으면 꺼 둔 줄도 걸린다."""

        status = cron_status.read_status(_runner("# " + NIGHTLY + "\n"))
        self.assertTrue(status.available)
        self.assertFalse(status.nightly)

    def test_missing_crontab_command_is_unknown_not_absent(self):
        """`crontab` 이 없는 환경에서 '등록 안 됨'이라고 하면 거짓말이다."""

        status = cron_status.read_status(_runner(exc=FileNotFoundError()))
        self.assertFalse(status.available)
        self.assertEqual(cron_status.summary_key(status), "cron.unknown")

    def test_timeout_is_unknown_not_absent(self):
        status = cron_status.read_status(
            _runner(exc=subprocess.TimeoutExpired(["crontab"], 5))
        )
        self.assertFalse(status.available)
        self.assertIsNotNone(status.error)

    def test_an_unexpected_failure_is_unknown(self):
        status = cron_status.read_status(
            _runner(stderr="not allowed", returncode=126)
        )
        self.assertFalse(status.available)
        self.assertEqual(cron_status.summary_key(status), "cron.unknown")

    def test_other_peoples_cron_lines_are_ignored(self):
        status = cron_status.read_status(
            _runner("0 3 * * * /usr/local/bin/backup.sh\n")
        )
        self.assertTrue(status.available)
        self.assertFalse(status.collector)
        self.assertFalse(status.nightly)


class MarkerAgreementTests(unittest.TestCase):
    def test_the_markers_match_the_setup_script(self):
        """표식이 어긋나면 등록해 놓고도 '없음'이라고 말한다."""

        import pathlib

        script = pathlib.Path(__file__).resolve().parent.parent / "setup_cron.csh"
        text = script.read_text(encoding="utf-8")
        self.assertIn(cron_status.COLLECTOR_MARKER, text)
        self.assertIn(cron_status.NIGHTLY_MARKER, text)


class SummaryKeyTests(unittest.TestCase):
    def test_every_key_exists_in_both_languages(self):
        from smvwp import i18n

        keys = {
            "cron.ok", "cron.none", "cron.nightly_missing",
            "cron.collector_missing", "cron.unknown", "cron.nightly_by_gui",
        }
        for language in ("ko", "en"):
            i18n.set_language(language)
            for key in keys:
                with self.subTest(language=language, key=key):
                    self.assertNotEqual(i18n.t(key), key, f"{language}에 {key} 없음")
        i18n.set_language("ko")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
