import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import storage_manager.search_index as search_index_module
from storage_manager.search_index import (
    SearchIndex,
    run_full_index,
    search_db_file,
    search_index_disk_bytes,
)


class SearchIndexTests(unittest.TestCase):
    def test_busy_timeout_is_configurable_for_noncritical_index_updates(self):
        with tempfile.TemporaryDirectory() as temp:
            index = SearchIndex(
                search_db_file(Path(temp)),
                timeout_seconds=0.75,
            )
            try:
                self.assertEqual(index.conn.execute("PRAGMA busy_timeout").fetchone()[0], 750)
            finally:
                index.close()

    def test_invalid_filesystem_bytes_have_unique_display_and_reversible_task_key(self):
        safe_text = getattr(search_index_module, "_safe_text")
        encode_task = getattr(search_index_module, "_encode_task_path", None)
        decode_task = getattr(search_index_module, "_decode_task_path", None)
        self.assertTrue(callable(encode_task))
        self.assertTrue(callable(decode_task))

        invalid_name = "bad_\udcff"
        escaped = safe_text(invalid_name)
        self.assertNotEqual(escaped, safe_text("bad_?"))
        self.assertNotIn("\udcff", escaped)
        self.assertIn("\\xff", escaped)
        self.assertEqual(decode_task(encode_task(invalid_name)), invalid_name)

    def test_full_index_supports_name_extension_and_type_queries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "data"
            account = root / "account"
            results = account / "Results"
            empty = account / "EmptyFolder"
            results.mkdir(parents=True)
            empty.mkdir()
            (account / "alpha.txt").write_text("a", encoding="ascii")
            (account / "beta.log").write_text("b", encoding="ascii")
            (results / "alpha_data.csv").write_text("c", encoding="ascii")

            index = SearchIndex(search_db_file(data_dir))
            try:
                outcome = run_full_index(
                    index,
                    "id-a",
                    account,
                    now=datetime(2026, 7, 14, 22, 0, 0),
                    force=True,
                )
                self.assertTrue(outcome.complete)
                self.assertEqual(
                    [row.relative_path for row in index.search("id-a", "alpha.txt", mode="exact")],
                    ["alpha.txt"],
                )
                self.assertEqual(
                    {row.name for row in index.search("id-a", "alpha", mode="prefix")},
                    {"alpha.txt", "alpha_data.csv"},
                )
                self.assertEqual(
                    [row.name for row in index.search("id-a", "_data", mode="contains")],
                    ["alpha_data.csv"],
                )
                self.assertEqual(
                    [row.name for row in index.search("id-a", extension=".csv")],
                    ["alpha_data.csv"],
                )
                directories = index.search(
                    "id-a",
                    entry_type="directory",
                    limit=500,
                )
                self.assertEqual(
                    {row.name for row in directories},
                    {"EmptyFolder", "Results"},
                )
                summary = index.summary()
                self.assertEqual(summary["total_entries"], 5)
                self.assertGreater(summary["db_bytes"], 0)
                status = index.account_status("id-a")
                self.assertEqual(status["state"], "complete")
                self.assertEqual(status["files_indexed"], 3)
                self.assertEqual(status["dirs_indexed"], 2)
            finally:
                index.close()

    def test_prefix_search_uses_basename_range_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            account.mkdir()
            for name in ("Alpha.txt", "alpha_data.csv", "alpha%literal.dat", "beta.txt"):
                (account / name).write_text("x", encoding="ascii")

            index = SearchIndex(search_db_file(root / "data"))
            try:
                run_full_index(index, "id-a", account, force=True)
                statements = []
                index.conn.set_trace_callback(statements.append)
                self.assertEqual(
                    [row.name for row in index.search("id-a", "alpha%", mode="prefix")],
                    ["alpha%literal.dat"],
                )
                select = next(
                    statement
                    for statement in statements
                    if statement.lstrip().upper().startswith("SELECT RELATIVE_PATH")
                )
                self.assertIn("basename >=", select)
                self.assertIn("basename <", select)
                plan = " ".join(
                    str(row[3])
                    for row in index.conn.execute(
                        "EXPLAIN QUERY PLAN " + select
                    ).fetchall()
                )
                self.assertIn("idx_search_name", plan)
                self.assertIn("basename>?", plan.replace(" ", ""))
            finally:
                index.close()

    def test_full_index_resumes_by_directory_and_reconciles_deletions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "data"
            account = root / "account"
            first = account / "first"
            second = account / "second"
            first.mkdir(parents=True)
            second.mkdir()
            deleted = first / "delete.me"
            deleted.write_text("x", encoding="ascii")
            (second / "keep.dat").write_text("y", encoding="ascii")

            calls = 0

            def stop_after_root():
                nonlocal calls
                calls += 1
                return calls > 1

            index = SearchIndex(search_db_file(data_dir))
            first_run = run_full_index(
                index,
                "id-a",
                account,
                stop_requested=stop_after_root,
                now=datetime(2026, 7, 14, 22, 0, 0),
                force=True,
            )
            self.assertFalse(first_run.complete)
            self.assertTrue(first_run.cancelled)
            index.close()

            index = SearchIndex(search_db_file(data_dir))
            try:
                resumed = run_full_index(
                    index,
                    "id-a",
                    account,
                    now=datetime(2026, 7, 15, 22, 0, 0),
                )
                self.assertTrue(resumed.complete)
                self.assertEqual(len(index.search("id-a", "delete.me", mode="exact")), 1)

                deleted.unlink()
                rebuilt = run_full_index(
                    index,
                    "id-a",
                    account,
                    now=datetime(2026, 7, 22, 22, 0, 0),
                    force=True,
                )
                self.assertTrue(rebuilt.complete)
                self.assertEqual(index.search("id-a", "delete.me", mode="exact"), [])
                self.assertEqual(len(index.search("id-a", "keep.dat", mode="exact")), 1)
            finally:
                index.close()

    def test_incremental_records_add_file_and_parent_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            account.mkdir()
            index = SearchIndex(search_db_file(root / "data"))
            try:
                changed = account / "new" / "nested" / "result.bin"
                index.upsert_changed_files(
                    "id-a",
                    account,
                    [(str(changed), 1024, 1_700_000_000.0)],
                    timestamp="2026-07-15 01:00:00",
                )
                self.assertEqual(
                    [row.relative_path for row in index.search("id-a", extension="bin")],
                    ["new/nested/result.bin"],
                )
                self.assertEqual(
                    {row.relative_path for row in index.search("id-a", entry_type="directory")},
                    {"new", "new/nested"},
                )
                self.assertEqual(
                    index.account_status("id-a")["last_incremental_at"],
                    "2026-07-15 01:00:00",
                )
            finally:
                index.close()

    def test_remove_and_prune_accounts_clear_entries_state_and_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first"
            second = root / "second"
            (first / "queued").mkdir(parents=True)
            second.mkdir()
            (first / "queued" / "one.dat").write_text("1", encoding="ascii")
            (second / "two.dat").write_text("2", encoding="ascii")
            index = SearchIndex(search_db_file(root / "data"))
            try:
                calls = 0

                def stop_after_root():
                    nonlocal calls
                    calls += 1
                    return calls > 1

                run_full_index(
                    index,
                    "id-a",
                    first,
                    stop_requested=stop_after_root,
                    force=True,
                )
                run_full_index(index, "id-b", second, force=True)

                remove_account = getattr(index, "remove_account", None)
                prune_accounts = getattr(index, "prune_accounts", None)
                self.assertTrue(callable(remove_account))
                self.assertTrue(callable(prune_accounts))
                remove_account("id-a")
                self.assertEqual(index.search("id-a"), [])
                self.assertEqual(index.account_status("id-a")["state"], "never")
                self.assertEqual(
                    index.conn.execute(
                        "SELECT COUNT(*) FROM search_scan_tasks WHERE account_id = 'id-a'"
                    ).fetchone()[0],
                    0,
                )

                removed = prune_accounts({"id-b": str(root / "different-path")})
                self.assertEqual(removed, ["id-b"])
                self.assertEqual(index.search("id-b"), [])
            finally:
                index.close()

    def test_symlink_is_indexed_but_target_is_not_traversed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            external = root / "external"
            account.mkdir()
            external.mkdir()
            (external / "secret.dat").write_text("secret", encoding="ascii")
            try:
                os.symlink(str(external), str(account / "external-link"), target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")

            index = SearchIndex(search_db_file(root / "data"))
            try:
                run_full_index(index, "id-a", account, force=True)
                self.assertEqual(
                    [row.name for row in index.search("id-a", entry_type="link")],
                    ["external-link"],
                )
                self.assertEqual(index.search("id-a", "secret.dat", mode="exact"), [])
            finally:
                index.close()

    def test_checkpointed_directory_is_revalidated_before_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            queued = account / "queued"
            queued.mkdir(parents=True)
            (queued / "must-not-leak.dat").write_text("x", encoding="ascii")

            calls = 0

            def stop_after_root():
                nonlocal calls
                calls += 1
                return calls > 1

            index = SearchIndex(search_db_file(root / "data"))
            try:
                paused = run_full_index(
                    index,
                    "id-a",
                    account,
                    stop_requested=stop_after_root,
                    force=True,
                )
                self.assertTrue(paused.cancelled)

                original_is_symlink = Path.is_symlink

                def replaced_with_symlink(path):
                    if path == queued:
                        return True
                    return original_is_symlink(path)

                with patch.object(
                    Path,
                    "is_symlink",
                    autospec=True,
                    side_effect=replaced_with_symlink,
                ):
                    resumed = run_full_index(index, "id-a", account)

                self.assertTrue(resumed.complete)
                self.assertEqual(
                    index.search("id-a", "must-not-leak.dat", mode="exact"),
                    [],
                )
            finally:
                index.close()

    def test_large_flat_directory_is_committed_in_batches_and_stops_mid_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            account.mkdir()
            for number in range(9):
                (account / f"item-{number}.dat").write_text("x", encoding="ascii")

            index = SearchIndex(search_db_file(root / "data"))
            try:
                batch_store = getattr(index, "_store_directory_batch", None)
                self.assertTrue(callable(batch_store))
                index._store_directory_batch = Mock(wraps=batch_store)
                checks = 0

                def stop_during_directory():
                    nonlocal checks
                    checks += 1
                    return checks > 6

                paused = run_full_index(
                    index,
                    "id-a",
                    account,
                    stop_requested=stop_during_directory,
                    force=True,
                    entry_batch_size=2,
                )
                self.assertTrue(paused.cancelled)
                self.assertGreaterEqual(index._store_directory_batch.call_count, 2)
                self.assertEqual(index.account_status("id-a")["state"], "paused")
                with index.conn:
                    index.conn.executescript(
                        """
                        CREATE TABLE write_audit(kind TEXT NOT NULL);
                        CREATE TRIGGER audit_search_insert
                        AFTER INSERT ON search_entries
                        BEGIN INSERT INTO write_audit(kind) VALUES('insert'); END;
                        CREATE TRIGGER audit_search_update
                        AFTER UPDATE ON search_entries
                        BEGIN INSERT INTO write_audit(kind) VALUES('update'); END;
                        """
                    )

                resumed = run_full_index(
                    index,
                    "id-a",
                    account,
                    entry_batch_size=2,
                )
                self.assertTrue(resumed.complete)
                self.assertEqual(
                    len(index.search("id-a", extension="dat")),
                    9,
                )
                self.assertEqual(
                    index.conn.execute("SELECT COUNT(*) FROM write_audit").fetchone()[0],
                    4,
                )
            finally:
                index.close()

    def test_recent_complete_index_is_not_rebuilt_until_due(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            account.mkdir()
            (account / "first.dat").write_text("x", encoding="ascii")
            index = SearchIndex(search_db_file(root / "data"))
            try:
                completed_at = datetime(2026, 7, 14, 22, 0, 0)
                run_full_index(index, "id-a", account, now=completed_at, force=True)
                (account / "later.dat").write_text("y", encoding="ascii")
                skipped = run_full_index(
                    index,
                    "id-a",
                    account,
                    now=completed_at + timedelta(days=1),
                    full_scan_days=7,
                )
                self.assertTrue(skipped.complete)
                self.assertTrue(skipped.skipped)
                self.assertEqual(index.search("id-a", "later.dat", mode="exact"), [])
            finally:
                index.close()

    def test_terminal_checkpoint_finishes_same_generation_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            account.mkdir()
            index = SearchIndex(search_db_file(root / "data"))
            try:
                generation, skipped = index._begin_full_scan(
                    "id-a",
                    account.resolve(),
                    datetime(2026, 7, 14, 22, 0, 0),
                    True,
                    7,
                )
                self.assertFalse(skipped)
                with index.conn:
                    index.conn.execute(
                        "UPDATE search_scan_tasks SET status = 'complete' WHERE account_id = ?",
                        ("id-a",),
                    )

                resumed = run_full_index(index, "id-a", account)
                self.assertTrue(resumed.complete)
                self.assertEqual(index.account_status("id-a")["generation"], generation)
            finally:
                index.close()

    def test_production_completion_timestamp_is_not_pinned_to_start(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            account = root / "account"
            account.mkdir()
            (account / "one.dat").write_text("x", encoding="ascii")
            index = SearchIndex(search_db_file(root / "data"))

            def timestamp(value=None):
                return "2026-07-14 22:00:00" if value is not None else "2026-07-15 08:30:00"

            try:
                with patch(
                    "storage_manager.search_index._timestamp",
                    side_effect=timestamp,
                ):
                    run_full_index(index, "id-a", account, force=True)
                status = index.account_status("id-a")
                self.assertEqual(status["started_at"], "2026-07-14 22:00:00")
                self.assertEqual(status["completed_at"], "2026-07-15 08:30:00")
            finally:
                index.close()

    def test_disk_size_includes_sqlite_sidecars(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            index = SearchIndex(search_db_file(data_dir))
            index.close()
            base_size = search_db_file(data_dir).stat().st_size
            journal = Path(str(search_db_file(data_dir)) + "-journal")
            journal.write_bytes(b"x" * 123)

            self.assertEqual(search_index_disk_bytes(data_dir), base_size + 123)


if __name__ == "__main__":
    unittest.main()
