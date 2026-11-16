import subprocess
import unittest
from unittest.mock import patch

from smvwp import collector, tiers
from smvwp.config import Account
from tests import support


def _completed(stdout, returncode=0, stderr=""):
    # stdout은 반드시 bytes여야 한다 (procio가 바이트 모드로 읽는다) -
    # 자세한 이유는 tests/support.py 참고.
    return support.completed(["df"], stdout=stdout, returncode=returncode, stderr=stderr)


DF_BYTES_OUTPUT = (
    "Filesystem     1024-blocks      Used Available Capacity Mounted on\n"
    "/dev/sda1        103080332  45678900  52145432      47% /user/project_a\n"
)

DF_INODES_OUTPUT = (
    "Filesystem      Inodes   IUsed   IFree IUse% Mounted on\n"
    "/dev/sda1      6553600  123456 6430144    2% /user/project_a\n"
)

DF_INODES_UNKNOWN_OUTPUT = (
    "Filesystem      Inodes   IUsed   IFree IUse% Mounted on\n"
    "nfsserver:/vol         -       -       -     - /user/project_a\n"
)


class QueryBytesTests(unittest.TestCase):
    @patch("smvwp.collector.subprocess.run")
    def test_parses_df_bytes_output(self, mock_run):
        mock_run.return_value = _completed(DF_BYTES_OUTPUT)
        result = collector.query_bytes("/user/project_a")
        self.assertEqual(result.filesystem, "/dev/sda1")
        self.assertEqual(result.total_kb, 103080332)
        self.assertEqual(result.used_kb, 45678900)
        self.assertEqual(result.avail_kb, 52145432)
        self.assertEqual(result.pct, 47.0)
        self.assertEqual(result.mount_point, "/user/project_a")

    @patch("smvwp.collector.subprocess.run")
    def test_nonzero_exit_raises(self, mock_run):
        mock_run.return_value = _completed("", returncode=1, stderr="df: cannot access")
        with self.assertRaises(collector.CollectorError):
            collector.query_bytes("/nowhere")

    @patch("smvwp.collector.subprocess.run")
    def test_timeout_raises_collector_error(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="df", timeout=10)
        with self.assertRaises(collector.CollectorError):
            collector.query_bytes("/slow/mount")


class QueryInodesTests(unittest.TestCase):
    @patch("smvwp.collector.subprocess.run")
    def test_parses_df_inodes_output(self, mock_run):
        mock_run.return_value = _completed(DF_INODES_OUTPUT)
        result = collector.query_inodes("/user/project_a")
        self.assertEqual(result.total, 6553600)
        self.assertEqual(result.used, 123456)
        self.assertEqual(result.pct, 2.0)

    @patch("smvwp.collector.subprocess.run")
    def test_unsupported_inode_reporting_is_none_not_zero(self, mock_run):
        mock_run.return_value = _completed(DF_INODES_UNKNOWN_OUTPUT)
        result = collector.query_inodes("/user/project_a")
        self.assertIsNone(result.total)
        self.assertIsNone(result.used)
        self.assertIsNone(result.pct)


class CollectAccountTests(unittest.TestCase):
    @patch("smvwp.collector.subprocess.run")
    def test_success_sets_ok_and_tiers(self, mock_run):
        mock_run.side_effect = [
            _completed(DF_BYTES_OUTPUT),
            _completed(DF_INODES_OUTPUT),
        ]
        account = Account(name="project_a", path="/user/project_a")
        record = collector.collect_account(account)
        self.assertTrue(record.ok)
        self.assertEqual(record.byte_pct, 47.0)
        self.assertEqual(record.byte_tier, tiers.NORMAL)
        self.assertEqual(record.inode_tier, tiers.NORMAL)
        self.assertEqual(record.overall_tier, tiers.NORMAL)

    @patch("smvwp.collector.subprocess.run")
    def test_failure_does_not_raise_and_marks_not_ok(self, mock_run):
        mock_run.return_value = _completed("", returncode=1, stderr="permission denied")
        account = Account(name="broken", path="/user/broken")
        record = collector.collect_account(account)
        self.assertFalse(record.ok)
        self.assertIn("permission denied", record.error_message)

    @patch("smvwp.collector.subprocess.run")
    def test_overall_tier_is_worse_of_byte_and_inode(self, mock_run):
        byte_output = DF_BYTES_OUTPUT.replace("47%", "96%")
        inode_output = DF_INODES_OUTPUT  # 2%, normal
        mock_run.side_effect = [_completed(byte_output), _completed(inode_output)]
        account = Account(name="project_a", path="/user/project_a")
        record = collector.collect_account(account)
        self.assertEqual(record.byte_tier, tiers.ALERT)
        self.assertEqual(record.inode_tier, tiers.NORMAL)
        self.assertEqual(record.overall_tier, tiers.ALERT)


class CollectAllTests(unittest.TestCase):
    """계정을 여럿이 함께 묻는다.

    예전에는 하나씩 돌았다. 계정마다 `df` 두 번이라 100개면 200번을 줄 서서 불렀고,
    파일서버가 답하지 않으면 계정마다 제한 시간(10초)을 통째로 기다려 17분 - 수집
    간격(15분)을 넘겨 다음 수집과 겹쳤다."""

    def accounts(self, count):
        return [Account(name=f"p{index}", path=f"/user/p{index}", account_id=f"a{index}")
                for index in range(count)]

    def test_accounts_are_asked_together(self):
        import threading

        from smvwp.store import SampleRecord

        # 둘이 동시에 들어와야 지나가는 문. 하나씩 돌면 첫 계정이 혼자 기다리다 깨진다.
        gate = threading.Barrier(2, timeout=10)

        def slow(account, **_kwargs):
            gate.wait()
            return SampleRecord(account_id=account.account_id, collected_at="t", ok=True)

        with patch("smvwp.collector.collect_account", side_effect=slow):
            records = collector.collect_all(self.accounts(6))
        self.assertEqual(len(records), 6)

    def test_results_keep_the_account_order(self):
        import time

        from smvwp.store import SampleRecord

        def uneven(account, **_kwargs):
            time.sleep(0.05 if account.account_id == "a0" else 0)   # 첫 계정이 가장 늦게 끝난다
            return SampleRecord(account_id=account.account_id, collected_at="t", ok=True)

        with patch("smvwp.collector.collect_account", side_effect=uneven):
            records = collector.collect_all(self.accounts(12))
        self.assertEqual([record.account_id for record in records],
                         [f"a{index}" for index in range(12)])

    def test_never_more_than_the_limit_at_once(self):
        import threading
        import time

        from smvwp.store import SampleRecord

        lock, state = threading.Lock(), {"now": 0, "most": 0}

        def counted(account, **_kwargs):
            with lock:
                state["now"] += 1
                state["most"] = max(state["most"], state["now"])
            time.sleep(0.01)
            with lock:
                state["now"] -= 1
            return SampleRecord(account_id=account.account_id, collected_at="t", ok=True)

        with patch("smvwp.collector.collect_account", side_effect=counted):
            collector.collect_all(self.accounts(40))
        self.assertLessEqual(state["most"], collector.COLLECT_WORKERS)

    def test_one_failing_account_does_not_stop_the_others(self):
        def run(args, *_args, **kwargs):
            if args[-1] == "/user/p1":
                raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
            return _completed(DF_INODES_OUTPUT if "-Pi" in args else DF_BYTES_OUTPUT)

        with patch("smvwp.collector.subprocess.run", side_effect=run):
            records = collector.collect_all(self.accounts(3))
        self.assertEqual([record.ok for record in records], [True, False, True])

    def test_nothing_and_one(self):
        self.assertEqual(collector.collect_all([]), [])
        with patch("smvwp.collector.ThreadPoolExecutor",
                   side_effect=AssertionError("계정 하나에 스레드를 띄웠다")), \
                patch("smvwp.collector.collect_account", return_value="x"):
            self.assertEqual(collector.collect_all(self.accounts(1)), ["x"])


if __name__ == "__main__":
    unittest.main()
