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

# `df -k --output=source,size,used,avail,pcent,itotal,iused,iavail,ipcent,target`
DF_COMBINED_OUTPUT = (
    "Filesystem     1K-blocks     Used    Avail Use%  Inodes  IUsed   IFree IUse% Mounted on\n"
    "/dev/sda1      103080332 45678900 52145432  47% 6553600 123456 6430144    2% /user/project a\n"
)

DF_COMBINED_NFS_OUTPUT = (
    "Filesystem     1K-blocks     Used    Avail Use% Inodes IUsed IFree IUse% Mounted on\n"
    "nfsserver:/vol 103080332 45678900 52145432  47%      -     -     -     - /user/project_a\n"
)


def _df(combined=None, bytes_output=DF_BYTES_OUTPUT, inode_output=DF_INODES_OUTPUT, calls=None):
    """`--output` 은 combined 로 (없으면 그런 옵션이 없다는 df), -Pk·-Pi 는 예전 출력으로."""

    def run(args, *_args, **_kwargs):
        if calls is not None:
            calls.append(list(args))
        if any(arg.startswith("--output") for arg in args):
            if combined is None:
                return _completed("", returncode=1, stderr="df: unrecognized option '--output'")
            return _completed(combined)
        return _completed(inode_output if "-Pi" in args else bytes_output)

    return run


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


class QueryUsageTests(unittest.TestCase):
    """용량과 inode 를 `df` 한 번에 묻는다 (예전에는 계정마다 두 번)."""

    def test_one_df_answers_both(self):
        calls = []
        with patch("smvwp.collector.subprocess.run", side_effect=_df(DF_COMBINED_OUTPUT, calls=calls)):
            byte_result, inode_result = collector.query_usage("/user/project a")
        self.assertEqual(len(calls), 1)
        self.assertEqual((byte_result.filesystem, byte_result.mount_point),
                         ("/dev/sda1", "/user/project a"))
        self.assertEqual((byte_result.total_kb, byte_result.used_kb, byte_result.avail_kb,
                          byte_result.pct), (103080332, 45678900, 52145432, 47.0))
        self.assertEqual((inode_result.total, inode_result.used, inode_result.avail,
                          inode_result.pct), (6553600, 123456, 6430144, 2.0))
        self.assertEqual(inode_result.mount_point, "/user/project a")

    def test_unreported_inodes_stay_unknown(self):
        with patch("smvwp.collector.subprocess.run", side_effect=_df(DF_COMBINED_NFS_OUTPUT)):
            byte_result, inode_result = collector.query_usage("/user/project_a")
        self.assertEqual(byte_result.pct, 47.0)
        self.assertIsNone(inode_result.total)
        self.assertIsNone(inode_result.pct)

    def test_df_without_output_option_asks_twice_as_before(self):
        calls = []
        with patch("smvwp.collector.subprocess.run", side_effect=_df(None, calls=calls)):
            byte_result, inode_result = collector.query_usage("/user/project_a")
        self.assertEqual(len(calls), 3)
        self.assertEqual((byte_result.pct, inode_result.pct), (47.0, 2.0))

    def test_odd_output_asks_twice_as_before(self):
        odd = DF_COMBINED_OUTPUT.replace("47%", "-")
        with patch("smvwp.collector.subprocess.run", side_effect=_df(odd)):
            byte_result, inode_result = collector.query_usage("/user/project_a")
        self.assertEqual((byte_result.pct, inode_result.pct), (47.0, 2.0))

    def test_timeout_is_not_asked_again(self):
        calls = []

        def hung(args, *_args, **kwargs):
            calls.append(args)
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))

        with patch("smvwp.collector.subprocess.run", side_effect=hung):
            with self.assertRaises(collector.CollectorError):
                collector.query_usage("/slow/mount")
        self.assertEqual(len(calls), 1)

    def test_real_failure_keeps_the_old_message(self):
        def denied(args, *_args, **_kwargs):
            return _completed("", returncode=1, stderr="df: /user/x: Permission denied")

        with patch("smvwp.collector.subprocess.run", side_effect=denied):
            with self.assertRaisesRegex(collector.CollectorError, "Permission denied"):
                collector.query_usage("/user/x")


class CollectAccountTests(unittest.TestCase):
    def test_one_df_per_account(self):
        calls = []
        with patch("smvwp.collector.subprocess.run", side_effect=_df(DF_COMBINED_OUTPUT, calls=calls)):
            record = collector.collect_account(Account(name="project_a", path="/user/project_a"))
        self.assertTrue(record.ok)
        self.assertEqual((record.byte_pct, record.inode_pct), (47.0, 2.0))
        self.assertEqual(len(calls), 1)

    @patch("smvwp.collector.subprocess.run")
    def test_success_sets_ok_and_tiers(self, mock_run):
        mock_run.side_effect = _df()
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
        mock_run.side_effect = _df(bytes_output=byte_output, inode_output=inode_output)
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

    def test_each_account_is_told_as_soon_as_its_df_ends(self):
        """새로고침이 df 몇 초 뒤에 줄을 고치게 - 느린 계정 하나가 나머지를 붙잡지 않는다."""

        import threading

        from smvwp.store import SampleRecord

        told, others_told = [], threading.Event()

        def uneven(account, **_kwargs):
            if account.account_id == "a0":
                # 나머지가 다 알려진 뒤에야 끝난다 - 다 끝난 뒤 한꺼번에 알리면 여기서 막힌다.
                self.assertTrue(others_told.wait(10), "끝난 계정을 바로 알리지 않았다")
            return SampleRecord(account_id=account.account_id, collected_at="t", ok=True)

        def on_record(record):
            told.append(record.account_id)
            if len(told) == 5:
                others_told.set()

        with patch("smvwp.collector.collect_account", side_effect=uneven):
            records = collector.collect_all(self.accounts(6), on_record=on_record)
        self.assertEqual(told[-1], "a0")
        self.assertEqual(sorted(told), sorted(record.account_id for record in records))

    def test_it_is_told_on_the_calling_thread_not_a_df_worker(self):
        """창의 신호는 수집 스레드에서만 나간다 (잠깐 떴다 사라지는 일꾼 스레드가 아니라)."""

        import threading

        from smvwp.store import SampleRecord

        threads = set()
        with patch("smvwp.collector.collect_account",
                   side_effect=lambda account, **_k: SampleRecord(
                       account_id=account.account_id, collected_at="t", ok=True)):
            collector.collect_all(self.accounts(6),
                                  on_record=lambda _r: threads.add(threading.get_ident()))
        self.assertEqual(threads, {threading.get_ident()})

    def test_a_broken_listener_does_not_stop_collecting(self):
        from smvwp.store import SampleRecord

        with patch("smvwp.collector.collect_account",
                   side_effect=lambda account, **_k: SampleRecord(
                       account_id=account.account_id, collected_at="t", ok=True)):
            records = collector.collect_all(self.accounts(3), on_record=lambda _r: 1 / 0)
        self.assertEqual(len(records), 3)

    def test_nothing_and_one(self):
        self.assertEqual(collector.collect_all([]), [])
        with patch("smvwp.collector.ThreadPoolExecutor",
                   side_effect=AssertionError("계정 하나에 스레드를 띄웠다")), \
                patch("smvwp.collector.collect_account", return_value="x"):
            self.assertEqual(collector.collect_all(self.accounts(1)), ["x"])


if __name__ == "__main__":
    unittest.main()
