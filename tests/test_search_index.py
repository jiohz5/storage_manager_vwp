import tempfile
import unittest
from pathlib import Path

from smvwp import admin_auth, search_index


class WalkAndIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.account = self.root / "acct"

        (self.account / "results").mkdir(parents=True)
        (self.account / "results" / "run_1.dat").write_text("x", encoding="utf-8")
        (self.account / "notes.txt").write_text("y", encoding="utf-8")

        # 계정 루트 바로 아래의 .snapshot -> 제외되어야 한다.
        (self.account / ".snapshot").mkdir()
        (self.account / ".snapshot" / "ignored.dat").write_text("z", encoding="utf-8")

        # 중첩된 .snapshot -> 일반 데이터로 포함되어야 한다.
        (self.account / "results" / ".snapshot").mkdir()
        (self.account / "results" / ".snapshot" / "included.dat").write_text("w", encoding="utf-8")

        self.conn = search_index.connect(self.data_dir)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _indexed_paths(self):
        return {
            row["relative_path"]
            for row in self.conn.execute("SELECT relative_path FROM search_entries").fetchall()
        }

    def test_root_snapshot_excluded_but_nested_included(self):
        search_index.index_account(self.conn, "acct-1", self.account)
        paths = self._indexed_paths()

        self.assertNotIn(".snapshot", paths)
        self.assertNotIn(".snapshot/ignored.dat", paths)
        self.assertIn("results/.snapshot/included.dat", paths)
        self.assertIn("notes.txt", paths)
        self.assertIn("results/run_1.dat", paths)

    def test_extension_and_kind_are_recorded(self):
        search_index.index_account(self.conn, "acct-1", self.account)
        row = self.conn.execute(
            "SELECT * FROM search_entries WHERE relative_path = 'notes.txt'"
        ).fetchone()
        self.assertEqual(row["extension"], "txt")
        self.assertEqual(row["kind"], search_index.KIND_FILE)

        row = self.conn.execute(
            "SELECT * FROM search_entries WHERE relative_path = 'results'"
        ).fetchone()
        self.assertEqual(row["kind"], search_index.KIND_DIR)

    def test_no_file_content_is_stored(self):
        """이름 인덱스이지 내용 검색이 아니다 - 본문이 저장되면 안 된다."""

        search_index.index_account(self.conn, "acct-1", self.account)
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(search_entries)")}
        self.assertNotIn("content", columns)
        self.assertEqual(
            columns,
            {"account_id", "relative_path", "name", "extension", "kind", "indexed_at"},
        )

    def test_reindex_removes_deleted_entries(self):
        search_index.index_account(self.conn, "acct-1", self.account)
        self.assertIn("notes.txt", self._indexed_paths())

        (self.account / "notes.txt").unlink()
        search_index.index_account(self.conn, "acct-1", self.account)
        self.assertNotIn("notes.txt", self._indexed_paths())

    def test_interrupted_index_keeps_existing_entries(self):
        """중간에 멈추면 아직 못 본 항목까지 지워버리면 안 된다."""

        search_index.index_account(self.conn, "acct-1", self.account)
        before = self._indexed_paths()

        search_index.index_account(
            self.conn, "acct-1", self.account, should_stop=lambda: True
        )
        self.assertEqual(self._indexed_paths(), before)

    def test_clear_account_removes_only_that_account(self):
        search_index.index_account(self.conn, "acct-1", self.account)
        search_index.index_account(self.conn, "acct-2", self.account)

        search_index.clear_account(self.conn, "acct-1")
        self.assertEqual(search_index.entry_count(self.conn, "acct-1"), 0)
        self.assertGreater(search_index.entry_count(self.conn, "acct-2"), 0)

    def test_prune_orphans_removes_unknown_accounts(self):
        search_index.index_account(self.conn, "acct-1", self.account)
        search_index.index_account(self.conn, "gone", self.account)

        search_index.prune_orphans(self.conn, ["acct-1"])
        self.assertGreater(search_index.entry_count(self.conn, "acct-1"), 0)
        self.assertEqual(search_index.entry_count(self.conn, "gone"), 0)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.account = self.root / "acct"
        (self.account / "sub").mkdir(parents=True)
        for name in ("alpha.log", "alpha_backup.log", "beta.txt", "100%_report.txt"):
            (self.account / name).write_text("x", encoding="utf-8")
        self.conn = search_index.connect(self.root / "data")
        search_index.index_account(self.conn, "acct-1", self.account)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _names(self, hits):
        return sorted(hit.name for hit in hits)

    def test_exact_match(self):
        hits = search_index.search(self.conn, "acct-1", "alpha.log", mode=search_index.MODE_EXACT)
        self.assertEqual(self._names(hits), ["alpha.log"])

    def test_prefix_match(self):
        hits = search_index.search(self.conn, "acct-1", "alpha", mode=search_index.MODE_PREFIX)
        self.assertEqual(self._names(hits), ["alpha.log", "alpha_backup.log"])

    def test_contains_match(self):
        hits = search_index.search(self.conn, "acct-1", "backup", mode=search_index.MODE_CONTAINS)
        self.assertEqual(self._names(hits), ["alpha_backup.log"])

    def test_wildcard_in_query_is_escaped(self):
        """사용자가 친 '%'가 전체 조회로 바뀌면 안 된다."""

        hits = search_index.search(self.conn, "acct-1", "%", mode=search_index.MODE_CONTAINS)
        self.assertEqual(self._names(hits), ["100%_report.txt"])

    def test_underscore_in_query_is_escaped(self):
        """'_'는 LIKE에서 임의의 한 글자를 뜻하므로 이스케이프되어야 한다."""

        hits = search_index.search(self.conn, "acct-1", "alpha_", mode=search_index.MODE_PREFIX)
        self.assertEqual(self._names(hits), ["alpha_backup.log"])

    def test_extension_filter(self):
        hits = search_index.search(self.conn, "acct-1", "", extension="log")
        self.assertEqual(self._names(hits), ["alpha.log", "alpha_backup.log"])

    def test_kind_filter(self):
        hits = search_index.search(self.conn, "acct-1", "", kind=search_index.KIND_DIR)
        self.assertEqual(self._names(hits), ["sub"])

    def test_limit_is_respected(self):
        hits = search_index.search(self.conn, "acct-1", "", limit=2)
        self.assertEqual(len(hits), 2)

    def test_search_is_scoped_to_account(self):
        hits = search_index.search(self.conn, "other-account", "alpha.log")
        self.assertEqual(hits, [])


class AdminAuthTests(unittest.TestCase):
    def test_default_pin_works_when_nothing_stored(self):
        session = admin_auth.AdminSession()
        self.assertTrue(session.unlock(admin_auth.DEFAULT_PIN))
        self.assertTrue(session.is_unlocked)

    def test_wrong_pin_keeps_locked(self):
        session = admin_auth.AdminSession()
        self.assertFalse(session.unlock("0000"))
        self.assertFalse(session.is_unlocked)

    def test_hash_round_trip(self):
        stored = admin_auth.hash_pin("4321")
        self.assertTrue(admin_auth.verify_pin("4321", stored))
        self.assertFalse(admin_auth.verify_pin("1234", stored))

    def test_hash_is_salted(self):
        """같은 PIN이라도 저장값이 매번 달라야 한다."""

        self.assertNotEqual(admin_auth.hash_pin("1111"), admin_auth.hash_pin("1111"))

    def test_pin_is_not_stored_in_plaintext(self):
        """저장값은 hex(salt$digest)뿐이어서 PIN 문자가 남을 수 없다.

        예전에는 숫자 PIN("4321")이 저장값에 없는지를 봤는데, 저장값이 97자
        hex라 그 네 글자가 **우연히** 들어갈 확률이 약 0.14%였다. 구현이
        멀쩡해도 가끔 실패하는 테스트여서, hex에 나올 수 없는 문자를 쓰는
        방식으로 바꿨다 - 이제 통과/실패가 구현만으로 결정된다.
        """

        stored = admin_auth.hash_pin("pin-zzzz")
        self.assertNotIn("pin-zzzz", stored)
        self.assertNotIn("zzzz", stored)

        salt_hex, _, digest_hex = stored.partition("$")
        self.assertTrue(salt_hex and digest_hex)
        for part in (salt_hex, digest_hex):
            self.assertRegex(part, r"^[0-9a-f]+$")

    def test_malformed_stored_hash_is_rejected(self):
        self.assertFalse(admin_auth.verify_pin("4321", "garbage-without-separator"))

    def test_lock_clears_session(self):
        session = admin_auth.AdminSession()
        session.unlock(admin_auth.DEFAULT_PIN)
        session.lock()
        self.assertFalse(session.is_unlocked)


if __name__ == "__main__":
    unittest.main()
