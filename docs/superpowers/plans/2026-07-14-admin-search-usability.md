# Admin Search And Usability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a password-gated search tab with resumable per-account path indexing while fixing data-path startup hangs and simplifying the account, tracking, inode, and close experiences.

**Architecture:** Keep monitoring history in `storage_manager.db` and all searchable path metadata in a separate `search_index.db`. Build full indexes directory-by-directory with SQLite checkpoints, feed nightly changed-file records into the same index without a second `find`, and reveal search controls only for an authenticated GUI session.

**Tech Stack:** Python 3.10, standard-library SQLite/os.scandir/hashlib, PyQt5, unittest, RHEL cron/csh.

## Global Constraints

- Do not require new Python packages.
- Never invoke `newgrp`, `sg`, or `sudo` from the program.
- Do not follow symlinked directories or cross filesystem device boundaries during full indexing.
- Store relative indexed paths, never file contents.
- Default per-account indexing to disabled.
- Keep 15-minute capacity monitoring independent from heavy work.
- Start nightly work at 22:00 and allow it to finish after 06:00.
- The workspace is not a valid Git repository, so commit steps are recorded as unavailable rather than attempted.

---

### Task 1: Fast Data-Path Bootstrap And Identity Guidance

**Files:**
- Modify: `storage_manager/runtime.py`
- Modify: `storage_manager/gui.py`
- Modify: `storage_manager/i18n.py`
- Modify: `tests/test_runtime.py`
- Modify: `tests/test_gui_i18n.py`

**Interfaces:**
- Produces: `current_user_id() -> str`
- Produces: `suggested_data_dir(user_id: str, home: Path) -> Path`
- Extends: `inspect_data_directory(path, create=True, measure_size=True)`
- Produces: `prompt_data_directory(parent, language, initial, user_id, pointer_path) -> Optional[str]`

- [ ] **Step 1: Write failing runtime tests**

Add tests proving `inspect_data_directory(..., measure_size=False)` never calls `directory_size_bytes`, and that user-specific suggestions prefer `/user/<id>/.storage-manager-vwp` only when the user directory exists.

```python
with patch("storage_manager.runtime.directory_size_bytes") as size:
    status = inspect_data_directory(data_dir, measure_size=False)
size.assert_not_called()
assert status.size_bytes == 0
```

- [ ] **Step 2: Run runtime tests and verify RED**

Run: `python -m unittest tests.test_runtime -v`

Expected: failure because `measure_size` and suggestion helpers do not exist.

- [ ] **Step 3: Implement fast probes and identity helpers**

Add the optional size flag without changing CLI diagnostics defaults. Return the effective login ID through the existing identity logic and derive an uncreated suggestion path safely.

- [ ] **Step 4: Write failing GUI bootstrap tests**

Patch `prompt_data_directory` rather than `QFileDialog.getExistingDirectory`. Assert the first-run flow displays the user and pointer path, saves the pointer, and calls the non-recursive probe.

- [ ] **Step 5: Replace native `/user` browsing with direct path input**

Create a compact `QDialog` with a path `QLineEdit`, user/pointer guidance, OK, and Cancel. Keep validation retry behavior and remove `QFileDialog` from first-run startup.

- [ ] **Step 6: Verify Task 1**

Run: `python -m unittest tests.test_runtime tests.test_gui_i18n -v`

Expected: all Task 1 tests pass.

### Task 2: Account Search Flag And Fixed Admin Authentication

**Files:**
- Create: `storage_manager/admin_auth.py`
- Modify: `storage_manager/config.py`
- Create: `tests/test_admin_auth.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `verify_admin_pin(candidate: str) -> bool`
- Extends: `Account.search_enabled: bool = False`

- [ ] **Step 1: Write failing authentication and migration tests**

```python
assert verify_admin_pin("6368")
assert not verify_admin_pin("6369")
assert not verify_admin_pin("")
assert "6368" not in Path("storage_manager/admin_auth.py").read_text(encoding="utf-8")
```

Also load a legacy `accounts.json` without `search_enabled` and verify it defaults to false and round-trips when enabled.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_admin_auth tests.test_config -v`

- [ ] **Step 3: Implement hashed PIN verification**

Use a fixed salt, PBKDF2-HMAC-SHA256, a fixed iteration count, and `hmac.compare_digest`. Keep only the derived digest in source.

- [ ] **Step 4: Add the account flag**

Add the dataclass field at the end for JSON compatibility and rely on filtered dataclass loading for legacy stores.

- [ ] **Step 5: Verify Task 2**

Run: `python -m unittest tests.test_admin_auth tests.test_config -v`

### Task 3: Search Index Database And Resumable Scanner

**Files:**
- Create: `storage_manager/search_index.py`
- Create: `tests/test_search_index.py`

**Interfaces:**
- Produces: `search_db_file(data_dir: Path) -> Path`
- Produces: `search_index_disk_bytes(data_dir: Path) -> int`
- Produces: `SearchIndex(path: Path)` with `search`, `summary`, `account_status`, `set_incremental_records`, and `close`
- Produces: `run_full_index(index, account_id, account_path, stop_requested, now=None, force=False) -> IndexRunResult`

- [ ] **Step 1: Write failing schema and search tests**

Create nested files and assert exact, prefix, contains, extension, and entry-type queries return relative paths with a hard limit of 500. Assert DB size includes synthetic journal sidecars.

- [ ] **Step 2: Run search tests and verify RED**

Run: `python -m unittest tests.test_search_index -v`

- [ ] **Step 3: Implement the separate SQLite store**

Use `WITHOUT ROWID` primary tables for `(account_id, relative_path)` and `(account_id, generation, relative_dir)`. Add account/name, extension/name, and type/name indexes. Use `journal_mode=DELETE`, `synchronous=FULL`, and a 30-second busy timeout.

- [ ] **Step 4: Write failing resume/reconcile tests**

Stop after the first directory, reopen the DB, resume, and assert all entries appear. Delete a file before a forced next generation and assert it is pruned only after that generation completes. Add a symlink and a nested mount-device fake to prove neither is traversed.

- [ ] **Step 5: Implement directory checkpoints**

Scan one `os.scandir` directory per transaction. Upsert visible entries, enqueue same-device child directories, mark the task complete, and update counts. On no pending tasks, delete old-generation rows and mark complete.

- [ ] **Step 6: Implement incremental upserts and status summaries**

Convert absolute changed-file paths to account-relative paths, infer parent directory rows, normalize extensions, and record `last_incremental_at`. Expose total entries, account entries, DB bytes, state, and timestamps.

- [ ] **Step 7: Verify Task 3**

Run: `python -m unittest tests.test_search_index -v`

### Task 4: Nightly Integration And Run-To-Completion Policy

**Files:**
- Modify: `storage_manager/activity_scan.py`
- Modify: `storage_manager/scheduler.py`
- Modify: `storage_manager/tracking.py`
- Modify: `storage_manager/i18n.py`
- Modify: `tests/test_resumable_activity.py`
- Modify: `tests/test_reports_scheduler.py`

**Interfaces:**
- Extends: `scan_changed_file_activity(..., record_batch=None)`
- Produces: scheduler phase `search_index`
- Keeps: `run_nightly_scan(...)` public signature

- [ ] **Step 1: Write failing changed-record batch test**

Pass a callback, stream two fake `find` records, and assert one or more callback batches preserve path, byte size, and mtime while top-level aggregation remains unchanged.

- [ ] **Step 2: Run activity tests and verify RED**

Run: `python -m unittest tests.test_resumable_activity -v`

- [ ] **Step 3: Add optional batching without a second traversal**

Flush changed records in bounded batches and once at EOF. Callback failures become a warning owned by search integration and must not alter activity aggregation.

- [ ] **Step 4: Write failing scheduler tests**

Assert enabled search accounts receive incremental records, an initial/full-due index runs after reports, search failures do not fail the nightly report, and the production detail budget is no longer capped at 05:45.

- [ ] **Step 5: Integrate `SearchIndex` with the nightly scheduler**

Open the search DB only when at least one enabled account has `search_enabled`. Feed daily change batches for those accounts. After reports, run/resume full indexing when missing or seven days old. Publish the `search_index` phase and honor the existing safe-stop token.

- [ ] **Step 6: Remove the global morning cutoff**

Do not cap detail work by `overnight_seconds_remaining` in production. Keep per-task split timeouts, process locking, and manual stop. Update status copy to state that 22:00 work continues until complete.

- [ ] **Step 7: Verify Task 4**

Run: `python -m unittest tests.test_resumable_activity tests.test_reports_scheduler -v`

### Task 5: Password-Gated Search GUI

**Files:**
- Modify: `storage_manager/gui.py`
- Modify: `storage_manager/i18n.py`
- Modify: `tests/test_gui_i18n.py`

**Interfaces:**
- Produces: `MainWindow.unlock_admin_mode()` and `lock_admin_mode()`
- Produces: hidden `search_tab` inserted only while unlocked
- Produces: `refresh_search_status()` and asynchronous `run_search()`

- [ ] **Step 1: Write failing admin visibility tests**

Assert no search tab at startup, wrong PIN leaves it hidden, correct PIN inserts it, and lock removes it. Patch the password dialog rather than exposing the PIN field.

- [ ] **Step 2: Write failing search UI status tests**

Seed `search_index.db`, unlock, and assert the tab displays total DB bytes, total entries, selected-account entries, timestamps, and enabled state.

- [ ] **Step 3: Implement admin menu and tab lifecycle**

Create `관리자/Administrator` actions for unlock and lock. Prompt with `QLineEdit.Password`, verify through `admin_auth`, and keep authorization in memory only.

- [ ] **Step 4: Implement search controls**

Add account, mode, type, name, extension, enable/disable, status, refresh, and results widgets. Limit results to 500 and show complete reconstructed paths. Run queries in a `QRunnable` with result/error signals.

- [ ] **Step 5: Verify Task 5**

Run: `python -m unittest tests.test_gui_i18n -v`

### Task 6: Account Form, Inode Help, Tracking Simplification, And Close Notice

**Files:**
- Modify: `storage_manager/gui.py`
- Modify: `storage_manager/i18n.py`
- Modify: `tests/test_gui_i18n.py`

**Interfaces:**
- Produces: `sync_path_from_name(text)` and `sync_name_from_path(text)` slots
- Produces: one stateful cron button, one stateful scan button, and one stateful notifier button
- Produces: explicit close information based on cron/notifier state

- [ ] **Step 1: Write failing account synchronization tests**

Simulate user edits with `QTest.keyClicks` or emit `textEdited`; verify `project_a` becomes `/user/project_a` and `/user/project_b` becomes `project_b` without signal recursion.

- [ ] **Step 2: Write failing inode and tracking layout tests**

Assert the Korean inode header and tooltip explain file-count exhaustion. Assert the tracking table has seven columns, recent collection is stretch-sized, and obsolete restart/install/remove button attributes are absent.

- [ ] **Step 3: Simplify the tracking controls and rows**

Combine use/inode/quota into one cell, keep the recent collection timestamp in its own wide cell, and make each action button switch label and handler behavior from current state.

- [ ] **Step 4: Write failing close-notice tests**

Send a close event to a visible window with mocked notifier status and assert the message distinguishes an active tray notifier from cron-only operation.

- [ ] **Step 5: Implement close guidance and responsive sizing**

Display the close message before cleanup, then stop timers and close the DB. Resize the initial window to a VWP-friendly width and use stretch headers.

- [ ] **Step 6: Verify Task 6**

Run: `python -m unittest tests.test_gui_i18n -v`

### Task 7: Documentation, Privacy, And Source Bundle

**Files:**
- Modify: `README.md`
- Modify: `VWP_ACCEPTANCE.md`
- Modify: `FEATURE_ROADMAP.md`
- Modify: `.gitignore`
- Modify: `make_bundle.py`
- Modify: `tests/test_verify_environment.py`

**Interfaces:**
- Bundle includes `storage_manager/admin_auth.py` and `storage_manager/search_index.py`
- Bundle excludes `search_index.db` and all sidecars

- [ ] **Step 1: Write failing bundle/privacy tests**

Assert new modules are present in the generated archive and no search DB, journal, WAL, SHM, runtime index task, or actual indexed path is packaged.

- [ ] **Step 2: Update ignore and bundle manifests**

Exclude search runtime state by filename/pattern and explicitly include source modules.

- [ ] **Step 3: Update operator documentation**

Document fixed UI-level administrator gating, per-account opt-in, index size estimates and actual size display, seven-day deletion reconciliation, run-to-completion behavior, and the non-recursive data-path prompt.

- [ ] **Step 4: Verify Task 7**

Run: `python -m unittest tests.test_verify_environment -v`

### Task 8: Full Verification And Delivery Bundle

**Files:**
- Verify all modified source and tests
- Regenerate: `dist/storage_manager_vwp-source.tar.gz`
- Regenerate: `dist/storage_manager_vwp-source.tar.gz.sha256`

- [ ] **Step 1: Run complete unit tests**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass; only the existing Windows symlink privilege skip is allowed.

- [ ] **Step 2: Compile all Python entry points**

Run: `python -m compileall -q storage_manager tests app.py capacity_watch.py health_check.py nightly_scan.py runtime_check.py storage_notifier.py verify_environment.py make_bundle.py`

- [ ] **Step 3: Run CLI smoke checks**

Run `python runtime_check.py --python-only`, `python app.py --help`, and `python nightly_scan.py --help`.

- [ ] **Step 4: Build and inspect the source archive**

Run: `python make_bundle.py`

Inspect archive entries, verify the checksum sidecar, and confirm no runtime DB or indexed path is present.

- [ ] **Step 5: Review against the approved design**

Check every section in `docs/superpowers/specs/2026-07-14-admin-search-usability-design.md` against implementation and report any RHEL-only acceptance gap honestly.

- [ ] **Step 6: Git checkpoint unavailable**

Record that `.git` is not a valid repository and do not claim a commit, branch, or push.
