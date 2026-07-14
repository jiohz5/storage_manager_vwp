# Capacity Watch And Tray Notifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GUI-independent 15-minute capacity watcher that detects rapid growth and imminent full conditions, persists local alerts, and shows them through a MATE tray notifier while the main window is closed.

**Architecture:** A short-lived cron CLI collects only `df`/inode/quota data and stores 15-minute samples in SQLite. Pure analysis code derives severity, growth rate, and time-to-full events; the existing atomic outbox persists them. A separate PyQt5 tray process reads unread outbox files, displays local notifications, and is launched by MATE autostart.

**Tech Stack:** Python 3.10, standard library, SQLite through `sqlite3`, PyQt5, RHEL 8.1 coreutils, csh, cron, `unittest`.

## Global Constraints

- Runtime target is RHEL 8.1 with MATE under VWP/DCV; no Windows execution mode is shipped.
- The watcher runs at minutes `7,22,37,52` and never calls `du`, `find`, or a detailed scan.
- Severity is WARN at 90%, ALERT at 95% or 6 hours to full, EMERGENCY at 98% or 2 hours to full, and FULL at 100% or 0KB available.
- Rapid growth defaults to 100GB per sample interval.
- High-resolution samples are retained for 30 days; existing daily history remains at 365 days.
- Notification delivery defaults to local outbox with no network dependency.
- No automatic deletion, job termination, or quota modification is allowed.
- Runtime state, account configuration, databases, logs, and outbox files remain outside Git through `.gitignore`.

---

### Task 1: Configuration And High-Resolution Storage

**Files:**
- Modify: `storage_manager/config.py`
- Modify: `storage_manager/database.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_database.py`

**Interfaces:**
- Produces: `Settings.capacity_sample_days`, `rapid_growth_gb`, `forecast_alert_hours`, `forecast_emergency_hours`, `capacity_stale_minutes`, `popup_backlog_days`.
- Produces: `CapacitySampleRecord`, `Database.add_capacity_sample(record)`, `latest_capacity_sample(account_id)`, and `purge_capacity_samples(keep_days, now)`.

- [ ] **Step 1: Write failing configuration and database tests**

```python
def test_capacity_settings_are_validated(self):
    settings = Settings(capacity_sample_days=30, rapid_growth_gb=100)
    self.assertEqual(settings.forecast_alert_hours, 6)

def test_capacity_samples_keep_intraday_rows_and_purge_old(self):
    first = CapacitySampleRecord(
        ts="2026-07-12 10:00:00", account_id="a", account_name="A",
        account_path="/user/a", fs_key="1:fs", fs_name="fs",
        total_kb=1000, used_kb=800, avail_kb=200, use_pct=80,
    )
    db.add_capacity_sample(first)
    db.add_capacity_sample(replace(first, ts="2026-07-12 10:15:00", used_kb=850))
    self.assertEqual(db.capacity_sample_count("a"), 2)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_config tests.test_database -v`

Expected: import or attribute failures for the new settings and `CapacitySampleRecord`.

- [ ] **Step 3: Add validated settings and the `capacity_samples` table**

```python
@dataclass(frozen=True)
class CapacitySampleRecord:
    ts: str
    account_id: str
    account_name: str
    account_path: str
    fs_key: str
    fs_name: str
    total_kb: int
    used_kb: int
    avail_kb: int
    use_pct: int
    total_inodes: Optional[int] = None
    used_inodes: Optional[int] = None
    avail_inodes: Optional[int] = None
    inode_use_pct: Optional[int] = None
    quota_used_kb: Optional[int] = None
    quota_limit_kb: Optional[int] = None
    quota_use_pct: Optional[int] = None
    quota_error: str = ""
```

Use a unique index on `(ts, account_id)` and indexes on `(account_id, ts)` and `(fs_key, ts)`.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m unittest tests.test_config tests.test_database -v`

Expected: all configuration and database tests pass.

- [ ] **Step 5: Commit**

```bash
git add storage_manager/config.py storage_manager/database.py tests/test_config.py tests/test_database.py
git commit -m "feat: store high resolution capacity samples"
```

### Task 2: Capacity Assessment And Event Generation

**Files:**
- Create: `storage_manager/capacity_watch.py`
- Create: `tests/test_capacity_watch.py`
- Modify: `storage_manager/notifications.py`
- Modify: `tests/test_notifications.py`
- Modify: `storage_manager/i18n.py`

**Interfaces:**
- Produces: `CapacityAssessment`, `assess_capacity(current, previous, now, settings)`.
- Produces: `build_capacity_events(results, settings, language)` and `run_capacity_watch(data_dir, backend, now_override)`.
- Consumes: `CapacitySampleRecord` and `UsageSnapshot`.

- [ ] **Step 1: Write failing assessment tests**

```python
def test_growth_rate_predicts_emergency(self):
    previous = sample("2026-07-12 10:00:00", used_kb=8_000_000, avail_kb=2_000_000)
    current = usage(used_kb=9_000_000, avail_kb=1_000_000, use_pct=90)
    result = assess_capacity(current, previous, datetime(2026, 7, 12, 10, 15), settings)
    self.assertEqual(result.level, "emergency")
    self.assertAlmostEqual(result.hours_to_full, 0.25)

def test_safe_sample_after_warning_generates_one_recovery(self):
    events = build_capacity_events([result(previous_pct=92, current_pct=80)], settings, "en")
    self.assertEqual(events[0].key, "capacity-recovery:fs-1")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_capacity_watch tests.test_notifications -v`

Expected: `storage_manager.capacity_watch` is missing and emergency/full levels are unknown.

- [ ] **Step 3: Implement pure assessment and grouped events**

```python
@dataclass(frozen=True)
class CapacityAssessment:
    level: str
    growth_kb: int
    rate_kb_per_hour: float
    hours_to_full: Optional[float]
    rapid_growth: bool

def assess_capacity(
    current: UsageSnapshot,
    previous: Optional[CapacitySampleRecord],
    now: datetime,
    settings: Settings,
) -> CapacityAssessment:
    effective_pct = max(
        value for value in (current.use_pct, current.inode_use_pct) if value is not None
    )
    growth_kb = 0 if previous is None else current.used_kb - previous.used_kb
    elapsed_hours = 0.0 if previous is None else (
        now - datetime.strptime(previous.ts, "%Y-%m-%d %H:%M:%S")
    ).total_seconds() / 3600.0
    rate = growth_kb / elapsed_hours if growth_kb > 0 and 0 < elapsed_hours <= 2 else 0.0
    hours_to_full = current.avail_kb / rate if rate > 0 else None
    level = capacity_level(effective_pct, current.avail_kb, hours_to_full, settings)
    return CapacityAssessment(
        level=level,
        growth_kb=growth_kb,
        rate_kb_per_hour=rate,
        hours_to_full=hours_to_full,
        rapid_growth=growth_kb >= settings.rapid_growth_gb * 1024 * 1024,
    )
```

Only compare samples from the same `fs_key` and at most two hours apart. Extend notification rank to `warning < alert < emergency < full`; severity escalation bypasses cooldown. Recovery uses a distinct warning-level key so it is not suppressed by a previous full event.

- [ ] **Step 4: Implement one-`df`-per-device collection**

Normalize every enabled account path, group with `os.stat(path).st_dev`, call `backend.read_usage()` once per group, then collect optional quota per account. Continue after a group timeout, persist successful account rows, atomically update `capacity_watch_status.json`, and purge old rows.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest tests.test_capacity_watch tests.test_notifications -v`

Expected: assessment, grouping, partial failure, cooldown escalation, and recovery tests pass.

- [ ] **Step 6: Commit**

```bash
git add storage_manager/capacity_watch.py storage_manager/notifications.py storage_manager/i18n.py tests/test_capacity_watch.py tests/test_notifications.py
git commit -m "feat: detect rapid storage growth and imminent full"
```

### Task 3: Capacity CLI, Cron, And Health Monitoring

**Files:**
- Create: `capacity_watch.py`
- Modify: `storage_manager/scheduler.py`
- Modify: `storage_manager/health.py`
- Modify: `setup_cron.csh`
- Modify: `tests/test_reports_scheduler.py`
- Modify: `tests/test_health.py`
- Modify: `tests/test_tracking.py`

**Interfaces:**
- Produces: `capacity_cron_line(data_dir, python_bin)`.
- Extends: `CronStatus.capacity_installed` and `capacity_line`.
- Consumes: `Database.latest_capacity_sample(account_id)` for freshness checks.

- [ ] **Step 1: Write failing cron and freshness tests**

```python
def test_capacity_cron_runs_four_times_per_hour(self):
    line = capacity_cron_line(Path("/state"), "/python/bin/python3")
    self.assertTrue(line.startswith("7,22,37,52 * * * * "))
    self.assertIn("capacity_watch.py", line)

def test_stale_capacity_sample_creates_alert(self):
    events = build_freshness_events(store, db, datetime(2026, 7, 12, 8, 0))
    self.assertTrue(any(event.key == "capacity-freshness:stale" for event in events))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_reports_scheduler tests.test_health tests.test_tracking -v`

Expected: missing capacity cron/status fields and no capacity freshness event.

- [ ] **Step 3: Add the CLI and managed cron entry**

```python
def capacity_cron_line(data_dir: Path, python_bin: str) -> str:
    return (
        "7,22,37,52 * * * * "
        f"{safe_python} {safe_script} --data-dir {safe_data} "
        f">> {safe_log} 2>&1 {CAPACITY_CRON_MARKER}"
    )
```

Install, detect, and remove nightly, capacity, and health entries as one managed set. `--print-cron` prints all three lines.

- [ ] **Step 4: Add 45-minute capacity freshness health events**

Missing or stale capacity samples are reported at 07:00 through the same local outbox. Nightly freshness behavior remains unchanged.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest tests.test_reports_scheduler tests.test_health tests.test_tracking -v`

Expected: all cron installation/removal and health tests pass.

- [ ] **Step 6: Commit**

```bash
git add capacity_watch.py storage_manager/scheduler.py storage_manager/health.py setup_cron.csh tests/test_reports_scheduler.py tests/test_health.py tests/test_tracking.py
git commit -m "feat: schedule capacity guard every fifteen minutes"
```

### Task 4: Persistent Local Popup Queue

**Files:**
- Create: `storage_manager/popup_queue.py`
- Create: `tests/test_popup_queue.py`

**Interfaces:**
- Produces: `PopupEnvelope`, `unread_notifications(data_dir, backlog_days, now)`, `acknowledge_notifications(data_dir, paths, now)`, and `popup_summary(envelopes, language)`.
- Consumes: atomic outbox JSON from `dispatch_notifications`.

- [ ] **Step 1: Write failing queue tests**

```python
def test_unread_outbox_survives_process_restart(self):
    create_outbox(data_dir, level="full")
    unread = unread_notifications(data_dir, 7, now)
    self.assertEqual(len(unread), 1)
    acknowledge_notifications(data_dir, [unread[0].path], now)
    self.assertEqual(unread_notifications(data_dir, 7, now), [])

def test_backlog_is_summarized_by_highest_severity(self):
    title, message = popup_summary([warning, full], "en")
    self.assertIn("FULL", title)
    self.assertIn("2", message)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_popup_queue -v`

Expected: `storage_manager.popup_queue` is missing.

- [ ] **Step 3: Implement parsing, acknowledgement, and summary**

Ignore malformed files, filter by `generated_at` within seven days, and atomically write `popup_state.json`. Prune acknowledgements whose outbox file no longer exists.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m unittest tests.test_popup_queue -v`

Expected: queue persistence, malformed file, age cutoff, acknowledgement, and summary tests pass.

- [ ] **Step 5: Commit**

```bash
git add storage_manager/popup_queue.py tests/test_popup_queue.py
git commit -m "feat: persist unread local storage alerts"
```

### Task 5: MATE Tray Notifier And Autostart

**Files:**
- Create: `storage_notifier.py`
- Create: `storage_manager/notifier.py`
- Create: `tests/test_notifier.py`
- Modify: `storage_manager/i18n.py`

**Interfaces:**
- Produces: `build_autostart_entry(python_bin: Path, script: Path, data_dir: Path) -> str`, `install_autostart(data_dir: Path, python_bin: Path, app_dir: Path, autostart_dir: Optional[Path] = None) -> Path`, `remove_autostart(autostart_dir: Optional[Path] = None) -> bool`, `read_notifier_status(data_dir: Path) -> Dict[str, object]`, `launch_notifier(data_dir: Path) -> int`, and `request_notifier_stop(data_dir: Path) -> bool`.
- Consumes: `unread_notifications` and `acknowledge_notifications`.

- [ ] **Step 1: Write failing autostart and lifecycle tests**

```python
def test_autostart_entry_uses_absolute_python_and_data_paths(self):
    entry = build_autostart_entry(Path("/opt/py/bin/python3"), Path("/app/storage_notifier.py"), Path("/state"))
    self.assertIn('Exec="/opt/py/bin/python3" "/app/storage_notifier.py" --data-dir "/state"', entry)

def test_stop_request_is_bound_to_running_notifier(self):
    write_notifier_status(data_dir, pid=123, run_id="abc", state="running")
    self.assertTrue(request_notifier_stop(data_dir))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_notifier -v`

Expected: notifier module and lifecycle functions are missing.

- [ ] **Step 3: Implement control files and MATE autostart**

Write `~/.config/autostart/storage-manager-notifier.desktop` without root. Reject newline and `%` in desktop command paths. Use absolute `sys.executable`, script, and data directory paths.

- [ ] **Step 4: Implement `QSystemTrayIcon` process**

Poll outbox and stop state every two seconds. Show one startup summary for pending alerts, then one popup for each newly created envelope. Tray actions open the manager, show and acknowledge the alert center, pause polling, and quit. If the tray is unavailable, retain unread events and retry without acknowledging them.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest tests.test_notifier tests.test_popup_queue -v`

Expected: pure lifecycle/autostart tests pass without requiring a visible desktop.

- [ ] **Step 6: Commit**

```bash
git add storage_notifier.py storage_manager/notifier.py storage_manager/i18n.py tests/test_notifier.py
git commit -m "feat: add persistent MATE tray notifications"
```

### Task 6: GUI Tracking And Settings Integration

**Files:**
- Modify: `storage_manager/gui.py`
- Modify: `storage_manager/i18n.py`
- Modify: `tests/test_gui_i18n.py`

**Interfaces:**
- Consumes: capacity status, extended `CronStatus`, notifier status/control, and new `Settings` fields.
- Produces: Tracking controls for notifier start/stop/restart and autostart install/remove.

- [ ] **Step 1: Write failing offscreen GUI tests**

```python
def test_tracking_tab_exposes_capacity_and_notifier_state(self):
    window = MainWindow(data_dir, backend=fake_backend)
    self.assertIn("15", window.lbl_capacity_watch.text())
    self.assertTrue(window.btn_notifier_start.isEnabled())

def test_capacity_settings_persist(self):
    window.spin_rapid_growth.setValue(250)
    self.assertEqual(load_store(data_dir).settings.rapid_growth_gb, 250)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_gui_i18n -v`

Expected: new labels, buttons, and spinboxes are missing.

- [ ] **Step 3: Add Tracking status and notifier controls**

Show the managed capacity cron line, next capacity run, last watcher result, failures, notifier PID/state, autostart status, and unread count. Keep existing nightly run/stop/restart controls unchanged.

- [ ] **Step 4: Add settings and KOR/ENG strings**

Add spinboxes for rapid growth GB, alert forecast hours, emergency forecast hours, and high-resolution retention days. Persist each change immediately through `save_store`.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m unittest tests.test_gui_i18n tests.test_i18n -v`

Expected: offscreen GUI and translation tests pass.

- [ ] **Step 6: Commit**

```bash
git add storage_manager/gui.py storage_manager/i18n.py tests/test_gui_i18n.py
git commit -m "feat: manage capacity guard and notifier from GUI"
```

### Task 7: Packaging, Documentation, And End-To-End Verification

**Files:**
- Modify: `make_bundle.py`
- Modify: `README.md`
- Modify: `VWP_ACCEPTANCE.md`
- Modify: `verify_environment.py`
- Modify: `tests/test_verify_environment.py`

**Interfaces:**
- Packages: `capacity_watch.py`, `storage_notifier.py`, and all runtime modules.
- Documents: cron, MATE autostart, local-only popup semantics, retention, and RHEL acceptance steps.

- [ ] **Step 1: Write failing package and verification tests**

```python
def test_runtime_manifest_contains_capacity_and_notifier_entry_points(self):
    self.assertIn("capacity_watch.py", RUNTIME_FILES)
    self.assertIn("storage_notifier.py", RUNTIME_FILES)

def test_autostart_check_reports_writable_directory(self):
    with tempfile.TemporaryDirectory() as temp:
        result = check_autostart_directory(Path(temp))
    self.assertEqual(result.level, "OK")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_verify_environment -v`

Expected: runtime manifest or autostart verification assertion fails.

- [ ] **Step 3: Update package and operator documentation**

Document `setup_cron.csh`, `storage_notifier.py --install-autostart`, local popup behavior during DCV disconnect/logout, unread backlog, and the requirement to keep data state on a writable filesystem that is preferably different from monitored storage.

- [ ] **Step 4: Run the full verification suite**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 5: Build and inspect the offline source archive**

Run: `python make_bundle.py`

Expected: archive and SHA-256 file are created. Inspect the tar listing and confirm it contains both new CLIs and no `data/`, database, log, cache, or account configuration.

- [ ] **Step 6: Commit**

```bash
git add make_bundle.py README.md VWP_ACCEPTANCE.md verify_environment.py tests/test_verify_environment.py
git commit -m "docs: package and verify capacity guard"
```
