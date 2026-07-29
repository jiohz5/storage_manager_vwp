# Managed Night Window And Snapshot Exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pause managed deep scans at 06:00 with resumable state, keep the 15-minute `df` watch active, and exclude each account root's `.snapshot` tree from every detailed scan and search-index path.

**Architecture:** Add two dependency-free policy modules: one performs lexical root `.snapshot` checks and the other evaluates managed local-time scan windows. Existing resumable `du`, changed-file `find`, search-index, scheduler, tracking, and GUI boundaries consume those helpers; the scheduler keeps one sticky stop reason so an automatic window pause cannot be confused with a user stop.

**Tech Stack:** Python 3.10 standard library, SQLite via `sqlite3`, PyQt5 5.15, GNU `df`/`du`/`find` on RHEL 8.1, `unittest` and `unittest.mock`.

## Global Constraints

- Add no third-party dependency and retain Python 3.10 compatibility.
- Keep all heavy detailed work serial across accounts and preserve the existing process lock.
- Apply the automatic window only to `cron` and GUI background (`gui`) triggers.
- Interpret the configured hours using host local time; default allowance is `22:00 <= time < 06:00`, and equal hours mean a 24-hour window.
- Keep terminal `command` and `direct` runs available outside the managed window.
- Keep the 15-minute `df` capacity watch and 07:00 health check unchanged.
- Treat automatic cutoff as a successful `paused` state, not a failure or user stop.
- Exclude only the exact account-root `.snapshot` entry and descendants; include nested names such as `<account>/results/.snapshot`.
- Do not attempt to subtract `.snapshot` from filesystem-level `df` output.
- Retain `nice -n 10` and `ionice -c 2 -n 7`; do not add a hard resource quota.
- Push the verified result directly to `main`, matching the established repository workflow.

---

### Task 1: Shared Snapshot Policy And Detailed Scanners

**Files:**
- Create: `storage_manager/path_policy.py`
- Modify: `storage_manager/resumable_scan.py:1-260`
- Modify: `storage_manager/activity_scan.py:1-180`
- Modify: `storage_manager/collector.py:105-170`
- Test: `tests/test_resumable_activity.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Produces: `SNAPSHOT_ROOT_NAME: str`, `is_excluded_account_path(account_root: PathLike, candidate: PathLike) -> bool`, and `is_excluded_relative_path(relative_path: str) -> bool`.
- Consumes: existing `Database.reconcile_detail_scan_roots()`, `run_resumable_baseline()`, `scan_changed_file_activity()`, and `collect_top_level_sizes()` interfaces without changing their public call signatures.

- [x] **Step 1: Write failing root-policy and resumable-baseline tests**

Add imports and tests in `tests/test_resumable_activity.py`:

```python
from storage_manager.path_policy import (
    is_excluded_account_path,
    is_excluded_relative_path,
)


def test_root_snapshot_policy_does_not_exclude_nested_name(self):
    with tempfile.TemporaryDirectory() as temp:
        account = Path(temp) / "account"
        self.assertTrue(
            is_excluded_account_path(account, account / ".snapshot" / "old.dat")
        )
        self.assertTrue(is_excluded_relative_path(".snapshot/old.dat"))
        self.assertFalse(
            is_excluded_account_path(
                account,
                account / "results" / ".snapshot" / "current.dat",
            )
        )
        self.assertFalse(is_excluded_relative_path("results/.snapshot/current.dat"))


def test_resumable_baseline_excludes_root_snapshot_and_legacy_checkpoint(self):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        account = root / "account"
        snapshot = account / ".snapshot"
        nested = account / "results" / ".snapshot"
        snapshot.mkdir(parents=True)
        nested.mkdir(parents=True)
        db = Database(root / "test.db")
        calls = []
        try:
            first = run_resumable_baseline(
                db,
                "account-id",
                str(account),
                30,
                du_runner=lambda path, timeout: calls.append(path) or 10,
            )
            self.assertTrue(first.complete)
            self.assertNotIn(str(snapshot), calls)
            self.assertIn(str(account / "results"), calls)

            state = db.detail_scan_state("account-id")
            db.begin_detail_scan(
                "account-id",
                str(account),
                "legacy-cycle",
                "2026-07-29 22:00:00",
                [(str(snapshot), str(snapshot), "scan", 0, "pending", 0)],
            )
            resumed = run_resumable_baseline(
                db,
                "account-id",
                str(account),
                30,
                du_runner=lambda path, timeout: calls.append(path) or 10,
            )
            self.assertTrue(resumed.complete)
            self.assertNotIn(str(snapshot), dict(resumed.items))
        finally:
            db.close()
```

- [x] **Step 2: Run the policy/baseline tests and confirm RED**

Run:

```powershell
python -m unittest tests.test_resumable_activity.ResumableAndActivityTests.test_root_snapshot_policy_does_not_exclude_nested_name tests.test_resumable_activity.ResumableAndActivityTests.test_resumable_baseline_excludes_root_snapshot_and_legacy_checkpoint -v
```

Expected: import failure because `storage_manager.path_policy` does not exist.

- [x] **Step 3: Implement the shared path policy and baseline reconciliation**

Create `storage_manager/path_policy.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Union


PathLike = Union[str, Path]
SNAPSHOT_ROOT_NAME = ".snapshot"


def is_excluded_relative_path(relative_path: str) -> bool:
    normalized = str(relative_path).replace("\\", "/").lstrip("/")
    return normalized == SNAPSHOT_ROOT_NAME or normalized.startswith(
        f"{SNAPSHOT_ROOT_NAME}/"
    )


def is_excluded_account_path(account_root: PathLike, candidate: PathLike) -> bool:
    root = Path(account_root).expanduser().absolute()
    path = Path(candidate).expanduser().absolute()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return bool(relative.parts and relative.parts[0] == SNAPSHOT_ROOT_NAME)
```

In `resumable_scan.initial_tasks()`, reject the child by name before calling any stat-like method:

```python
for child in base.iterdir():
    if is_excluded_account_path(base, child):
        continue
    # Existing directory/direct task creation follows.
```

When `run_resumable_baseline()` resumes an existing cycle, immediately call `initial_tasks()` and `db.reconcile_detail_scan_roots()` before selecting the next task. This removes pending or completed legacy `.snapshot` top paths before `detail_scan_items()` can return them:

```python
else:
    cycle_id = str(state[1])
    try:
        current_tasks = initial_tasks(account_path)
    except OSError as exc:
        return DetailScanResult([], False, 0.0, str(exc), resumable=True)
    db.reconcile_detail_scan_roots(
        account_id,
        cycle_id,
        timestamp,
        current_tasks,
    )
```

- [x] **Step 4: Write failing changed-file and legacy collector tests**

Add to `tests/test_resumable_activity.py`:

```python
def test_changed_file_scan_prunes_only_root_snapshot(self):
    with tempfile.TemporaryDirectory() as temp:
        account = Path(temp) / "account"
        account.mkdir()
        root_snapshot_file = account / ".snapshot" / "old.dat"
        nested_snapshot_file = account / "results" / ".snapshot" / "new.dat"
        output = (
            b"100\t1000.0\t" + str(root_snapshot_file).encode() + b"\0"
            b"250\t1001.0\t" + str(nested_snapshot_file).encode() + b"\0"
        )
        received = []
        with patch(
            "storage_manager.activity_scan.subprocess.Popen",
            return_value=FakeFindProcess(output),
        ) as popen_mock:
            result = scan_changed_file_activity(
                str(account),
                "2026-07-28 22:00:00",
                30,
                record_batch=lambda rows: received.extend(rows),
            )

        self.assertTrue(result.complete)
        self.assertEqual(result.files_seen, 1)
        self.assertEqual([row[0] for row in received], [str(nested_snapshot_file)])
        command = popen_mock.call_args.args[0]
        snapshot_index = command.index(str(account / ".snapshot"))
        self.assertEqual(command[snapshot_index - 1 : snapshot_index + 3], [
            "-path", str(account / ".snapshot"), "-prune", "-o"
        ])
```

Add `Path` and `collect_top_level_sizes` to the imports in `tests/test_collector.py`, then add:

```python
@patch("storage_manager.collector.subprocess.run")
def test_top_level_collection_excludes_root_snapshot(self, run_mock):
    base = Path("/user/project_a")
    run_mock.return_value = subprocess.CompletedProcess(
        ["du"],
        0,
        f"10\t{base / '.snapshot'}\n"
        f"20\t{base / 'results'}\n"
        f"30\t{base}\n",
        "",
    )

    result = collect_top_level_sizes(str(base), 30)

    self.assertTrue(result.complete)
    self.assertEqual(result.items, [(str(base / "results"), 20)])
    command = run_mock.call_args.args[0]
    self.assertIn(f"--exclude={base / '.snapshot'}", command)
```

- [x] **Step 5: Run the scanner tests and confirm RED**

Run:

```powershell
python -m unittest tests.test_resumable_activity.ResumableAndActivityTests.test_changed_file_scan_prunes_only_root_snapshot tests.test_collector.CollectorTests.test_top_level_collection_excludes_root_snapshot -v
```

Expected: root `.snapshot` appears in parsed activity/collector results and the `find`/`du` commands lack prune/exclude arguments.

- [x] **Step 6: Implement `find` pruning and defensive collector filtering**

In `activity_scan.scan_changed_file_activity()`, place this expression before the file predicate:

```python
snapshot_path = str(base / SNAPSHOT_ROOT_NAME)
command = [
    "find", str(base), "-xdev",
    "-path", snapshot_path, "-prune", "-o",
    "-type", "f", "-newermt", since_timestamp,
    "-printf", "%s\\t%T@\\t%p\\0",
]
```

Before buffering, indexing, or aggregating a decoded record, reject leaked output defensively:

```python
file_path = os.fsdecode(fields[2])
if is_excluded_account_path(base, file_path):
    continue
```

In `collector.collect_top_level_sizes()`, add an exact root exclusion to the GNU `du` command and filter parsed rows with the same helper:

```python
snapshot_path = str(base / SNAPSHOT_ROOT_NAME)
command = [
    "du", "-a", "-x", "-k", "--max-depth=1",
    f"--exclude={snapshot_path}", "--", str(base),
]
items = [
    row for row in parse_du_output(result.stdout, str(base))
    if not is_excluded_account_path(base, row[0])
]
```

- [x] **Step 7: Run focused and module tests and confirm GREEN**

Run:

```powershell
python -m unittest tests.test_resumable_activity tests.test_collector -v
```

Expected: all resumable, activity, and collector tests pass.

- [x] **Step 8: Commit the detailed-scanner exclusion**

```powershell
git add storage_manager/path_policy.py storage_manager/resumable_scan.py storage_manager/activity_scan.py storage_manager/collector.py tests/test_resumable_activity.py tests/test_collector.py
git commit -m "Exclude snapshots from detail scans"
```

---

### Task 2: Search Index Snapshot Exclusion And Legacy Cleanup

**Files:**
- Modify: `storage_manager/search_index.py:1-830`
- Modify: `storage_manager/scheduler.py:310-340`
- Test: `tests/test_search_index.py`
- Test: `tests/test_reports_scheduler.py`

**Interfaces:**
- Consumes: `is_excluded_account_path()` and `is_excluded_relative_path()` from Task 1.
- Produces: `SearchIndex.prune_excluded_paths(account_id: str) -> int` and exclusion-safe `upsert_changed_files()` / `run_full_index()` behavior.

- [x] **Step 1: Write failing full and incremental index tests**

Add to `tests/test_search_index.py`:

```python
def test_full_and_incremental_index_exclude_only_root_snapshot(self):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        account = root / "account"
        root_snapshot = account / ".snapshot"
        nested_snapshot = account / "results" / ".snapshot"
        root_snapshot.mkdir(parents=True)
        nested_snapshot.mkdir(parents=True)
        (root_snapshot / "old.dat").write_text("old", encoding="ascii")
        (nested_snapshot / "current.dat").write_text("new", encoding="ascii")
        index = SearchIndex(search_db_file(root / "data"))
        try:
            result = run_full_index(index, "id-a", account, force=True)
            self.assertTrue(result.complete)
            self.assertEqual(index.search("id-a", "old.dat", mode="exact"), [])
            self.assertEqual(
                [row.relative_path for row in index.search(
                    "id-a", "current.dat", mode="exact"
                )],
                ["results/.snapshot/current.dat"],
            )

            inserted = index.upsert_changed_files(
                "id-a",
                account,
                [
                    (str(root_snapshot / "incremental.bin"), 1, 1.0),
                    (str(nested_snapshot / "incremental.bin"), 1, 1.0),
                ],
            )
            self.assertGreater(inserted, 0)
            self.assertEqual(
                [row.relative_path for row in index.search(
                    "id-a", "incremental.bin", mode="exact"
                )],
                ["results/.snapshot/incremental.bin"],
            )
        finally:
            index.close()
```

Add the legacy cleanup test:

```python
def test_prune_excluded_paths_removes_legacy_rows_and_tasks(self):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        account = root / "account"
        account.mkdir()
        (account / "keep.dat").write_text("keep", encoding="ascii")
        index = SearchIndex(search_db_file(root / "data"))
        try:
            run_full_index(index, "id-a", account, force=True)
            generation = str(index.account_status("id-a")["generation"])
            with index.conn:
                index.conn.execute(
                    """
                    INSERT INTO search_entries(
                      account_id, relative_path, basename, extension,
                      entry_type, generation
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "id-a", ".snapshot/legacy.dat", "legacy.dat",
                        "dat", "file", generation,
                    ),
                )
                index.conn.execute(
                    """
                    INSERT INTO search_scan_tasks(
                      account_id, generation, relative_dir, status
                    ) VALUES(?, ?, ?, 'pending')
                    """,
                    ("id-a", generation, ".snapshot"),
                )

            removed = index.prune_excluded_paths("id-a")

            self.assertEqual(removed, 1)
            self.assertEqual(index.search("id-a", "legacy.dat", mode="exact"), [])
            self.assertEqual(
                index.conn.execute(
                    "SELECT COUNT(*) FROM search_scan_tasks WHERE account_id = ?",
                    ("id-a",),
                ).fetchone()[0],
                0,
            )
            status = index.account_status("id-a")
            self.assertEqual(status["files_indexed"], 1)
            self.assertEqual(status["dirs_indexed"], 0)
        finally:
            index.close()
```

- [x] **Step 2: Run the search tests and confirm RED**

Run:

```powershell
python -m unittest tests.test_search_index.SearchIndexTests.test_full_and_incremental_index_exclude_only_root_snapshot tests.test_search_index.SearchIndexTests.test_prune_excluded_paths_removes_legacy_rows_and_tasks -v
```

Expected: the full/incremental index returns root snapshot rows and `prune_excluded_paths` is missing.

- [x] **Step 3: Implement index filtering and cleanup**

Import the Task 1 helpers. Add this method to `SearchIndex`:

```python
def prune_excluded_paths(self, account_id: str) -> int:
    exact = SNAPSHOT_ROOT_NAME
    descendants = f"{SNAPSHOT_ROOT_NAME}/*"
    with self.conn:
        removed = self.conn.execute(
            """
            DELETE FROM search_entries
            WHERE account_id = ?
              AND (relative_path = ? OR relative_path GLOB ?)
            """,
            (account_id, exact, descendants),
        ).rowcount
        self.conn.execute(
            """
            DELETE FROM search_scan_tasks
            WHERE account_id = ?
              AND (relative_dir = ? OR relative_dir GLOB ?)
            """,
            (account_id, exact, descendants),
        )
        files = self.conn.execute(
            "SELECT COUNT(*) FROM search_entries WHERE account_id = ? AND entry_type <> 'directory'",
            (account_id,),
        ).fetchone()[0]
        directories = self.conn.execute(
            "SELECT COUNT(*) FROM search_entries WHERE account_id = ? AND entry_type = 'directory'",
            (account_id,),
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE search_scan_state SET files_indexed = ?, dirs_indexed = ? WHERE account_id = ?",
            (int(files), int(directories), account_id),
        )
    return max(0, int(removed))
```

In `upsert_changed_files()`, reject a candidate immediately after it is made relative to the root:

```python
if is_excluded_account_path(root, candidate):
    continue
```

In `run_full_index()`, call `index.prune_excluded_paths(account_id)` before `_begin_full_scan()`. In the directory-entry loop, compute `raw_relative_path` and skip it before any `stat`, symlink, or directory call:

```python
if is_excluded_relative_path(raw_relative_path):
    continue
```

After `scheduler.py` opens and prunes the search database, call `prune_excluded_paths()` once for every enabled search account. This cleans legacy rows before incremental batches without adding one cleanup transaction per batch.

- [x] **Step 4: Add scheduler cleanup coverage**

Add this scheduler test; patching `run_full_index` prevents its direct cleanup call from obscuring the scheduler-open cleanup assertion:

```python
def test_scheduler_prunes_excluded_search_paths_when_index_opens(self):
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp) / "data"
        root = Path(temp) / "user"
        account_path = root / "project_a"
        account_path.mkdir(parents=True)
        account = Account(
            "project_a",
            str(account_path),
            account_id="id-a",
            search_enabled=True,
        )
        save_store(
            data_dir,
            AccountStore(Settings(monitored_roots=[str(root)]), [account]),
        )
        backend = StorageBackend(
            name="test",
            read_usage=Mock(
                return_value=UsageSnapshot("fs", 1000, 500, 500, 50)
            ),
            scan_detail=Mock(return_value=DetailScanResult([], True, 0.1)),
            test_mode=True,
        )
        with patch.object(
            SearchIndex,
            "prune_excluded_paths",
            autospec=True,
            return_value=0,
        ) as prune, patch(
            "storage_manager.scheduler.run_full_index",
            return_value=Mock(cancelled=False),
        ):
            run_nightly_scan(data_dir, backend=backend)

        prune.assert_called_once()
        self.assertEqual(prune.call_args.args[1], "id-a")
```

- [x] **Step 5: Run search and scheduler tests and confirm GREEN**

Run:

```powershell
python -m unittest tests.test_search_index tests.test_reports_scheduler -v
```

Expected: all search-index and scheduler tests pass, including resumed/paused index tests.

- [x] **Step 6: Commit search-index exclusion**

```powershell
git add storage_manager/search_index.py storage_manager/scheduler.py tests/test_search_index.py tests/test_reports_scheduler.py
git commit -m "Exclude snapshots from search index"
```

---

### Task 3: Managed Scan Window And Automatic Pause Status

**Files:**
- Create: `storage_manager/scan_window.py`
- Modify: `storage_manager/scheduler.py:281-843`
- Modify: `storage_manager/tracking.py:1-180`
- Modify: `storage_manager/gui.py:2040-2110`
- Modify: `storage_manager/i18n.py:180-245,398-405,565-575`
- Create: `tests/test_scan_window.py`
- Modify: `tests/test_reports_scheduler.py`
- Modify: `tests/test_tracking.py`
- Modify: `tests/test_gui_i18n.py`
- Modify: `tests/test_i18n.py`

**Interfaces:**
- Produces: `MANAGED_SCAN_TRIGGERS`, `is_within_scan_window(current: datetime, start_hour: int, end_hour: int) -> bool`, and `managed_scan_window_closed(trigger: str, current: datetime, start_hour: int, end_hour: int) -> bool`.
- Changes: `run_nightly_scan(..., clock: Optional[Callable[[], datetime]] = None) -> Path` for deterministic cutoff tests while preserving every existing caller.
- Consumes: existing cooperative `stop_requested` callbacks in resumable `du`, changed-file `find`, and `run_full_index()`.

- [x] **Step 1: Write failing pure window-policy tests**

Create `tests/test_scan_window.py`:

```python
import unittest
from datetime import datetime

from storage_manager.scan_window import (
    is_within_scan_window,
    managed_scan_window_closed,
)


class ScanWindowTests(unittest.TestCase):
    def test_overnight_window_boundaries(self):
        self.assertTrue(is_within_scan_window(datetime(2026, 7, 29, 22, 0), 22, 6))
        self.assertTrue(is_within_scan_window(datetime(2026, 7, 30, 5, 59), 22, 6))
        self.assertFalse(is_within_scan_window(datetime(2026, 7, 30, 6, 0), 22, 6))
        self.assertFalse(is_within_scan_window(datetime(2026, 7, 29, 12, 0), 22, 6))

    def test_day_window_equal_hours_and_trigger_scope(self):
        noon = datetime(2026, 7, 29, 12, 0)
        self.assertTrue(is_within_scan_window(noon, 9, 17))
        self.assertFalse(is_within_scan_window(datetime(2026, 7, 29, 18, 0), 9, 17))
        self.assertTrue(is_within_scan_window(noon, 6, 6))
        self.assertTrue(managed_scan_window_closed("cron", noon, 22, 6))
        self.assertTrue(managed_scan_window_closed("gui", noon, 22, 6))
        self.assertFalse(managed_scan_window_closed("command", noon, 22, 6))
        self.assertFalse(managed_scan_window_closed("direct", noon, 22, 6))


if __name__ == "__main__":
    unittest.main()
```

- [x] **Step 2: Run policy tests and confirm RED**

Run:

```powershell
python -m unittest tests.test_scan_window -v
```

Expected: import failure because `storage_manager.scan_window` does not exist.

- [x] **Step 3: Implement the pure window policy**

Create `storage_manager/scan_window.py`:

```python
from __future__ import annotations

from datetime import datetime


MANAGED_SCAN_TRIGGERS = frozenset({"cron", "gui"})


def is_within_scan_window(current: datetime, start_hour: int, end_hour: int) -> bool:
    if start_hour == end_hour:
        return True
    current_minute = current.hour * 60 + current.minute
    start_minute = int(start_hour) * 60
    end_minute = int(end_hour) * 60
    if start_minute < end_minute:
        return start_minute <= current_minute < end_minute
    return current_minute >= start_minute or current_minute < end_minute


def managed_scan_window_closed(
    trigger: str,
    current: datetime,
    start_hour: int,
    end_hour: int,
) -> bool:
    return trigger in MANAGED_SCAN_TRIGGERS and not is_within_scan_window(
        current,
        start_hour,
        end_hour,
    )
```

- [x] **Step 4: Run policy tests and confirm GREEN**

Run:

```powershell
python -m unittest tests.test_scan_window -v
```

Expected: both tests pass.

- [x] **Step 5: Write failing scheduler integration tests**

Replace the old noon behavior test with these explicit trigger tests and add the mid-account cutoff test in `tests/test_reports_scheduler.py`:

```python
def test_managed_daytime_run_collects_df_but_pauses_detail(self):
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp) / "data"
        root = Path(temp) / "user"
        account_path = root / "project_a"
        account_path.mkdir(parents=True)
        account = Account("project_a", str(account_path), account_id="id-a")
        save_store(
            data_dir,
            AccountStore(Settings(monitored_roots=[str(root)]), [account]),
        )
        backend = StorageBackend(
            name="test",
            read_usage=Mock(
                return_value=UsageSnapshot("fs", 1000, 500, 500, 50)
            ),
            scan_detail=Mock(return_value=DetailScanResult([], True, 0.1)),
            test_mode=True,
        )

        run_nightly_scan(
            data_dir,
            backend=backend,
            trigger="cron",
            clock=lambda: datetime(2026, 7, 29, 12, 0),
        )

        backend.read_usage.assert_called_once()
        backend.scan_detail.assert_not_called()
        status = read_scan_status(data_dir)
        self.assertEqual(status["state"], "paused")
        self.assertEqual(status["message"], "scan window closed")


def test_direct_daytime_run_can_execute_detail(self):
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp) / "data"
        root = Path(temp) / "user"
        account_path = root / "project_a"
        account_path.mkdir(parents=True)
        account = Account("project_a", str(account_path), account_id="id-a")
        save_store(
            data_dir,
            AccountStore(Settings(monitored_roots=[str(root)]), [account]),
        )
        backend = StorageBackend(
            name="test",
            read_usage=Mock(
                return_value=UsageSnapshot("fs", 1000, 500, 500, 50)
            ),
            scan_detail=Mock(return_value=DetailScanResult([], True, 0.1)),
            test_mode=True,
        )

        run_nightly_scan(
            data_dir,
            backend=backend,
            trigger="direct",
            clock=lambda: datetime(2026, 7, 29, 12, 0),
        )

        backend.scan_detail.assert_called_once()
        self.assertEqual(read_scan_status(data_dir)["state"], "succeeded")


def test_window_closing_during_baseline_pauses_before_next_account(self):
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp) / "data"
        root = Path(temp) / "user"
        accounts = []
        for account_id, name in (("id-a", "project_a"), ("id-b", "project_b")):
            account_path = root / name
            account_path.mkdir(parents=True)
            accounts.append(Account(name, str(account_path), account_id=account_id))
        save_store(
            data_dir,
            AccountStore(Settings(monitored_roots=[str(root)]), accounts),
        )
        backend = StorageBackend(
            name="production-like",
            read_usage=Mock(
                return_value=UsageSnapshot("fs", 1000, 500, 500, 50)
            ),
            scan_detail=Mock(side_effect=AssertionError("legacy detail should not run")),
            test_mode=False,
        )
        current = [datetime(2026, 7, 29, 22, 0)]

        def baseline(*args, **kwargs):
            self.assertFalse(kwargs["stop_requested"]())
            current[0] = datetime(2026, 7, 30, 6, 0)
            self.assertTrue(kwargs["stop_requested"]())
            return DetailScanResult(
                [],
                False,
                0.1,
                "stop requested",
                resumable=True,
                cancelled=True,
            )

        with patch(
            "storage_manager.scheduler.run_resumable_baseline",
            side_effect=baseline,
        ) as run:
            run_nightly_scan(
                data_dir,
                backend=backend,
                trigger="cron",
                clock=lambda: current[0],
            )

        self.assertEqual(run.call_count, 1)
        self.assertEqual(read_scan_status(data_dir)["state"], "paused")
```

- [x] **Step 6: Run scheduler integration tests and confirm RED**

Run:

```powershell
python -m unittest tests.test_reports_scheduler.ReportAndSchedulerTests.test_managed_daytime_run_collects_df_but_pauses_detail tests.test_reports_scheduler.ReportAndSchedulerTests.test_direct_daytime_run_can_execute_detail tests.test_reports_scheduler.ReportAndSchedulerTests.test_window_closing_during_baseline_pauses_before_next_account -v
```

Expected: `run_nightly_scan()` rejects the `clock` argument or executes managed daytime detail work.

- [x] **Step 7: Implement sticky scheduler stop reasons**

Add the optional clock without changing positional arguments:

```python
def run_nightly_scan(
    data_dir: Path,
    skip_detail: bool = False,
    force_weekly: bool = False,
    backend: StorageBackend = RHEL_BACKEND,
    now_override: Optional[datetime] = None,
    trigger: str = "direct",
    clock: Optional[Callable[[], datetime]] = None,
) -> Path:
```

After `run_id` creation, define a sticky reason:

```python
current_time = clock or datetime.now
stop_reason: Optional[str] = None

def requested_stop_reason() -> Optional[str]:
    nonlocal stop_reason
    if stop_reason is not None:
        return stop_reason
    if scan_stop_requested(data_dir, run_id):
        stop_reason = "user"
    elif managed_scan_window_closed(
        trigger,
        current_time(),
        store.settings.scan_window_start_hour,
        store.settings.scan_window_end_hour,
    ):
        stop_reason = "window"
    return stop_reason

def should_stop() -> bool:
    return requested_stop_reason() is not None
```

Preserve the inexpensive all-account `df` pass. If `skip_detail` is true, keep a successful df/report-only run. Otherwise check `requested_stop_reason()` before the detail phase, before every account, and before search indexing. Pass `should_stop` to `scan_changed_file_activity()`, `run_resumable_baseline()`, and `run_full_index()`.

Use a helper to assign remaining report statuses:

```python
def stop_status_key() -> str:
    return "scan.window_closed" if stop_reason == "window" else "scan.stop_requested"
```

Final runtime state and canonical message become:

```python
if stop_reason == "window":
    runtime_state = "paused"
    runtime_message = "scan window closed"
elif stop_reason == "user":
    runtime_state = "stopped"
    runtime_message = "stop requested by user"
else:
    runtime_state = "succeeded"
    runtime_message = str(daily_path)
```

- [x] **Step 8: Add paused tracking and bilingual GUI coverage**

Add to `tests/test_tracking.py`:

```python
def test_paused_window_status_is_terminal_and_complete(self):
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp)
        write_scan_status(
            data_dir,
            {
                "state": "paused",
                "run_id": "run-a",
                "pid": 123,
                "phase": "idle",
                "message": "scan window closed",
            },
        )
        status = read_scan_status(data_dir)
        self.assertEqual(status["state"], "paused")
        self.assertEqual(status["phase"], "complete")
        self.assertEqual(status["message"], "scan window closed")
```

Add to `tests/test_i18n.py`:

```python
def test_scan_window_pause_translations(self):
    self.assertIn("06:00", tr("ko", "tracking.message.window_paused"))
    self.assertIn("06:00", tr("en", "tracking.message.window_paused"))
    self.assertNotEqual(tr("ko", "tracking.state.paused"), "tracking.state.paused")
    self.assertNotEqual(tr("en", "scan.window_closed"), "scan.window_closed")
    self.assertIn(
        "22:00",
        tr("en", "settings.scan_window_value", start=22, end=6),
    )
    self.assertIn(
        "06:00",
        tr("ko", "settings.scan_window_value", start=22, end=6),
    )
```

Add this focused GUI mapping test to `tests/test_gui_i18n.py`:

```python
def test_tracking_translates_automatic_window_pause(self):
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp) / "data"
        save_store(data_dir, AccountStore(Settings(), []))
        paused = {
            "state": "paused",
            "run_id": "run-a",
            "pid": 0,
            "trigger": "cron",
            "started_at": "2026-07-29 22:00:00",
            "updated_at": "2026-07-30 06:00:00",
            "finished_at": "2026-07-30 06:00:00",
            "current_account": "project_a",
            "phase": "complete",
            "accounts_processed": 1,
            "accounts_total": 2,
            "message": "scan window closed",
        }
        with patch(
            "storage_manager.gui.read_cron_status",
            return_value=CronStatus(False, False, error="not available"),
        ):
            window = MainWindow(data_dir)
        try:
            with patch.object(
                window,
                "_tracking_runtime_status",
                return_value=paused,
            ):
                window.refresh_tracking()
                self.assertIn("06:00", window.lbl_tracking_last_value.text())
                self.assertNotIn("scan window closed", window.lbl_tracking_last_value.text())
                window.change_language("en")
                self.assertIn("resumes", window.lbl_tracking_last_value.text())
        finally:
            self.dispose_window(window)
```

- [x] **Step 9: Implement paused status and UI text**

In `tracking.read_scan_status()`, normalize `paused` as complete:

```python
if state in {"succeeded", "stopped", "paused"}:
    status["phase"] = "complete"
```

Add translations:

```python
"tracking.state.paused": {
    "ko": "시간창 종료로 일시정지",
    "en": "Paused at scan-window end",
},
"tracking.message.window_paused": {
    "ko": "06:00 상세 스캔 안전 중지; 다음 22:00에 이어서 수행",
    "en": "detail scan safely paused at 06:00; resumes after 22:00",
},
"scan.window_closed": {
    "ko": "06:00 시간창 종료로 저장 후 일시정지",
    "en": "saved and paused at the 06:00 scan-window end",
},
"settings.scan_window_value": {
    "ko": "{start:02d}:00~{end:02d}:00 상세 실행; 종료 시 저장 후 다음 밤 재개",
    "en": "Detail work {start:02d}:00-{end:02d}:00; saves and resumes next night",
},
```

Update the Tracking introduction that currently says the 22:00 job runs to completion. In `gui.refresh_tracking()`, translate the canonical message:

```python
elif result_message == "scan window closed":
    result_message = self.t("tracking.message.window_paused")
```

- [x] **Step 10: Run all window, scheduler, tracking, i18n, and GUI tests**

Run:

```powershell
python -m unittest tests.test_scan_window tests.test_reports_scheduler tests.test_tracking tests.test_i18n tests.test_gui_i18n -v
```

Expected: all selected tests pass, including existing manual-stop and process-interruption coverage.

- [x] **Step 11: Commit managed window behavior**

```powershell
git add storage_manager/scan_window.py storage_manager/scheduler.py storage_manager/tracking.py storage_manager/gui.py storage_manager/i18n.py tests/test_scan_window.py tests/test_reports_scheduler.py tests/test_tracking.py tests/test_gui_i18n.py tests/test_i18n.py
git commit -m "Pause managed scans outside night window"
```

---

### Task 4: Operator Documentation And RHEL Acceptance

**Files:**
- Modify: `README.md:150-180,325-355`
- Modify: `VWP_ACCEPTANCE.md:130-180`
- Modify: `docs/superpowers/plans/2026-07-29-night-window-snapshot-exclusion.md`

**Interfaces:**
- Consumes: final `.snapshot` policy and managed pause behavior from Tasks 1-3.
- Produces: deployment guidance and executable RHEL/MATE acceptance checks.

- [x] **Step 1: Update operator documentation**

Replace claims that the nightly job runs indefinitely with these exact operational facts:

- managed cron/GUI detail work runs from 22:00 until 06:00 and then exits with resumable state;
- terminal direct runs are the deliberate outside-window diagnostic path;
- 15-minute `df` continues all day and does not traverse files;
- heavy accounts remain serial and a second nightly process is lock-rejected;
- `nice`/`ionice` are priorities, not hard bandwidth limits;
- `<account>/.snapshot` is excluded from `du`, changed-file `find`, and search index, while `df` remains filesystem-level.

- [x] **Step 2: Add RHEL/MATE acceptance checks**

Add steps that create a disposable account with:

```text
<account>/.snapshot/ignored.dat
<account>/results/.snapshot/included.dat
```

Run a direct detail scan and search index, then verify only `included.dat` appears. Add a managed daytime invocation check that records `paused` after the df pass, a 22:00 run check, and a 06:00 cutoff check using a disposable tree rather than production accounts. Confirm the 15-minute `capacity_watch.py` cron line remains installed.

- [x] **Step 3: Run complete verification**

Run:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py storage_notifier.py storage_manager tests
python runtime_check.py --python-only
git diff --check
```

Expected: all tests pass; the two Windows symlink tests may remain skipped because creating symlinks requires extra privileges. Compilation, Python 3.10 runtime diagnostics, and diff checks exit 0.

- [x] **Step 4: Review scope and runtime-state exclusions**

Run:

```powershell
git status -sb
git diff --stat origin/main
git ls-files | Select-String -Pattern '\.(db|db-wal|db-shm|log|lock)$|(^|[\\/])notifications([\\/]|$)'
```

Expected: only source, tests, design/plan, README, and acceptance documentation changed; no runtime database, log, lock, status, checkpoint, report, or notification outbox is tracked.

- [x] **Step 5: Request an independent code review**

Ask the reviewer to inspect the complete `origin/main..HEAD` diff plus uncommitted documentation, prioritizing:

- accidental traversal or indexing of root `.snapshot`;
- incorrect exclusion of nested `results/.snapshot`;
- 06:00 races and loss of resumable checkpoints;
- manual stop being mislabeled as automatic pause;
- a second account or search phase starting after cutoff;
- regressions to 15-minute df, cron, health, GUI Tracking, and Python 3.10.

Resolve every critical or important finding, rerun affected focused tests, and rerun the complete verification command before delivery.

Review resolutions:

- Moved search DB opening and cleanup after the all-account `df` pass and managed-window check; scheduler cleanup runs once and `run_full_index()` skips the duplicate cleanup.
- Made legacy search-task cleanup handle SQLite BLOB and TEXT paths, use indexed exact/range deletion for entry rows, and recalculate progress from only the active generation.
- Added cooperative stop checks to initial and timeout-split directory enumeration while preserving the original pending task when a split is interrupted.
- Added a 06:00 guard between per-account search cleanups, POSIX backslash filename coverage, cleanup range-boundary coverage, and failure-preserving RHEL acceptance steps.

- [x] **Step 6: Commit documentation and completed plan state**

```powershell
git add README.md VWP_ACCEPTANCE.md docs/superpowers/plans/2026-07-29-night-window-snapshot-exclusion.md
git commit -m "Document managed night scan safety"
```

- [x] **Step 7: Push directly to GitHub main and verify equality**

```powershell
git push origin main
git status -sb
git rev-parse HEAD
git rev-parse origin/main
```

Expected: `main` is clean and local `HEAD` equals `origin/main`.
