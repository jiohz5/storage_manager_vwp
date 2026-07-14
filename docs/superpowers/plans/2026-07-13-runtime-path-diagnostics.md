# Runtime Path And Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate the read-only Python installation from a user-selected writable global data directory and make RHEL startup, JSON, permission, capacity, and group failures directly diagnosable.

**Architecture:** A standard-library runtime module owns the tiny per-user data-location pointer, path resolution, write/SQLite probes, size measurement, and diagnostic JSON. The PyQt GUI provides first-run directory selection, while csh launchers perform Python preflight and never use inherited `PYTHONHOME`; cron always receives the resolved absolute data path.

**Tech Stack:** Python 3.10, standard library, SQLite through `sqlite3`, PyQt5, RHEL 8.1 csh/coreutils, `unittest`.

## Global Constraints

- Python 3.10 or newer and SQLite 3.24 or newer are required.
- The Python installation may be read-only; the selected data directory must be writable.
- No application data defaults to the 1GB personal account; only a sub-1KB location pointer is stored there.
- One global SQLite database is used for all monitored accounts.
- `newgrp`, `sg`, `sudo`, automatic deletion, and privilege escalation are never invoked.
- A project data directory is accepted only after a normal-process write probe succeeds.
- Same-filesystem data and monitored paths are warned about but may be explicitly accepted.
- Diagnostics never dump the full environment or secrets.

---

### Task 1: Data Location And Runtime Diagnostics Core

**Files:**
- Create: `storage_manager/runtime.py`
- Create: `runtime_check.py`
- Create: `tests/test_runtime.py`
- Modify: `storage_manager/config.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `config_location_file(home=None, environ=None) -> Path`.
- Produces: `read_saved_data_dir(...) -> Optional[Path]`, `save_data_dir_location(path, ...) -> Path`.
- Produces: `resolve_data_dir(explicit=None, environ=None, home=None) -> Optional[Path]` with explicit, environment, saved-pointer precedence.
- Produces: `directory_size_bytes(path) -> int` without following symbolic links.
- Produces: `inspect_data_directory(path, create=True) -> DataDirectoryStatus` with file and SQLite probes.
- Produces: `collect_runtime_diagnostics(data_dir=None) -> dict` and `write_runtime_diagnostics(data_dir, payload) -> Path`.

- [ ] **Step 1: Write failing runtime and configuration tests**

Add tests proving resolution precedence, atomic pointer round-trip, pointer size below 1KB, symlink exclusion, write/SQLite probe failure messages, Python 3.10 metadata, invalid JSON distinction, and writable-data errors wrapped as `ConfigError`.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_runtime tests.test_config -v`

Expected: `storage_manager.runtime` is missing and existing configuration errors do not provide the required distinctions.

- [ ] **Step 3: Implement the minimal runtime module and clearer config errors**

Use atomic UTF-8 JSON for the location pointer and diagnostics. Store only `{"data_dir": "/absolute/path"}` in the pointer. Probe with a temporary file and temporary SQLite database in the selected directory, then remove both. `directory_size_bytes` must use `os.scandir(..., follow_symlinks=False)` behavior and tolerate disappearing files.

- [ ] **Step 4: Add the `runtime_check.py` CLI**

Support `--python-only`, `--data-dir`, `--resolve-data-dir`, and `--set-data-dir`. The normal output prints executable, version, JSON path, SQLite version, PyQt5 result, user/groups, data path, current size, free space, and diagnostic-file location. Missing saved data is a setup condition, not a fallback to source or home data.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest tests.test_runtime tests.test_config -v`

Expected: all path, probe, diagnostic, and configuration tests pass.

### Task 2: csh Preflight And First-Run GUI Selection

**Files:**
- Modify: `run.csh`
- Modify: `setup_cron.csh`
- Modify: `storage_manager/gui.py`
- Modify: `storage_manager/i18n.py`
- Modify: `tests/test_runtime.py`
- Modify: `tests/test_gui_i18n.py`

**Interfaces:**
- Consumes: Task 1 `resolve_data_dir`, `inspect_data_directory`, `save_data_dir_location`.
- Produces: `choose_initial_data_dir(parent, language) -> Optional[Path]`.
- Produces: `resolve_gui_data_dir(explicit, parent, language) -> Path`.

- [ ] **Step 1: Write failing launcher and GUI bootstrap tests**

Assert both csh scripts prefer `STORAGE_MANAGER_PYTHON_BIN`, then `STORAGE_MANAGER_PYTHON_HOME`, never use `$PYTHONHOME/bin/python3`, unset inherited `PYTHONHOME`, invoke `runtime_check.py`, and use an absolute resolved data path for cron. Add an offscreen GUI test with patched directory selection proving a valid first-run path is saved and an invalid path is rejected without creating source-local data.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_runtime tests.test_gui_i18n -v`

Expected: launcher assertions and GUI bootstrap imports fail.

- [ ] **Step 3: Implement csh selector and preflight behavior**

Normal `run.csh` performs `runtime_check.py --python-only`, prints selected paths, and lets the GUI bootstrap when no data path exists. `run.csh --diagnose` invokes the full runtime check and existing environment verifier when a data path is available. `setup_cron.csh` refuses installation until `--resolve-data-dir` returns one absolute writable path.

- [ ] **Step 4: Implement first-run PyQt directory selection**

Create `QApplication` before path resolution. Explicit CLI/environment paths fail clearly instead of silently changing. A missing saved pointer opens an explanatory directory picker; successful validation saves the pointer, while failure offers retry/cancel and displays manual `newgrp` bootstrap guidance without executing it.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest tests.test_runtime tests.test_gui_i18n -v`

Expected: launcher source assertions and offscreen bootstrap behavior pass.

### Task 3: Data Budget And Same-Filesystem Safety

**Files:**
- Modify: `storage_manager/config.py`
- Modify: `storage_manager/health.py`
- Modify: `storage_manager/gui.py`
- Modify: `storage_manager/i18n.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_health.py`
- Modify: `tests/test_gui_i18n.py`

**Interfaces:**
- Produces: `Settings.data_size_warning_mb` defaulting to `500`.
- Produces: `build_data_directory_events(data_dir, settings, size_reader=directory_size_bytes) -> List[NotificationEvent]`.
- Produces: `same_filesystem(first, second, stat_reader=os.stat) -> bool`.

- [ ] **Step 1: Write failing budget and filesystem-warning tests**

Test the 500MB default and validation, one health warning above the threshold, no event below it, same-device detection, account-add confirmation, and Setup-tab display of absolute data path plus current size.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_config tests.test_health tests.test_gui_i18n -v`

Expected: the setting, health event, and filesystem warning are missing.

- [ ] **Step 3: Implement data-size health warning**

Add one cooldown-compatible `data-directory:size` warning event containing current MB, configured warning MB, and absolute path. Calculate it only at startup, explicit refresh, and the 07:00 health run, never in the two-second tracking timer.

- [ ] **Step 4: Implement GUI visibility and registration warning**

Show the selected global path, measured size, and 500MB threshold in Setup. Before adding or changing an account on the same device, require a Yes/No confirmation explaining that a full monitored filesystem can block DB and alert writes. The warning does not write into the project account or alter permissions.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest tests.test_config tests.test_health tests.test_gui_i18n -v`

Expected: budget events and GUI safety checks pass.

### Task 4: Documentation, Packaging, And Regression Verification

**Files:**
- Modify: `verify_environment.py`
- Modify: `README.md`
- Modify: `VWP_ACCEPTANCE.md`
- Modify: `make_bundle.py`
- Modify: `tests/test_verify_environment.py`

**Interfaces:**
- Packages: `runtime_check.py`, runtime module, updated csh launchers, and no data pointer/runtime state.

- [ ] **Step 1: Write failing package and verifier tests**

Require Python 3.10 in environment output, include runtime entry points in `RUNTIME_FILES`, and confirm the archive excludes `location.json`, diagnostics, DB, logs, caches, and account settings.

- [ ] **Step 2: Update operator documentation**

Document Python selector precedence, why `PYTHONHOME` is ignored, first-run global path selection, 50-150MB expected and 300MB reserved capacity, 500MB warning, manual one-time `newgrp` directory bootstrap, normal-shell write probe, same-filesystem risk, and `run.csh --diagnose` output collection.

- [ ] **Step 3: Run the complete test suite**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass with zero failures and errors.

- [ ] **Step 4: Compile and smoke-test CLIs**

Run: `python -m compileall -q storage_manager app.py runtime_check.py verify_environment.py`

Run: `python runtime_check.py --python-only`

Expected: compilation succeeds and runtime metadata reports Python 3.10, JSON, SQLite, and PyQt5.

- [ ] **Step 5: Rebuild and inspect the offline archive**

Run: `python make_bundle.py`

Expected: `dist/storage_manager_vwp-source.tar.gz` and its SHA-256 sidecar exist; required runtime files are present and runtime data is absent.

## Execution Note

This workspace has an empty `.git` directory rather than a valid Git repository, so the plan uses test checkpoints instead of commits and does not initialize, push, or alter Git metadata.
