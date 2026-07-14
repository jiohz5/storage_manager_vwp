# Explicit Full Exit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove accidental GUI exit paths, make normal close requests minimize the window, and provide a bilingual File menu whose Full Exit action disables all managed background activity before closing.

**Architecture:** `MainWindow` retains the native Qt/MATE frame but removes `Qt.WindowCloseButtonHint`. A guarded `closeEvent` minimizes unless `_explicit_exit` was set by the Full Exit workflow; that workflow reuses existing cron, notifier, and scan control functions and reports partial failures without pretending shutdown succeeded.

**Tech Stack:** Python 3.10, PyQt5 5.15, `unittest`, existing csh/RHEL cron and MATE notifier integration.

## Global Constraints

- Preserve Python 3.10 compatibility and introduce no dependency.
- Keep Korean and English text in the existing UTF-8 translation table.
- Preserve unmanaged crontab entries and never kill arbitrary processes.
- Do not delete settings, account data, history, reports, outbox files, or search indexes.
- Native MATE controls may choose button placement; the close hint must be absent and the minimize hint present.
- Push the verified final change directly to `main`.

---

### Task 1: Minimize-First Window And File Menu

**Files:**
- Modify: `storage_manager/gui.py:466-600,860-894,1326-1340`
- Modify: `storage_manager/i18n.py:39-50,294-314`
- Test: `tests/test_gui_i18n.py:103-200,246-273`

**Interfaces:**
- Produces: `MainWindow._configure_window_controls()`, `MainWindow._build_file_menu()`, `MainWindow.minimize_window()`, and `_explicit_exit: bool`.
- Consumes: existing `MainWindow._retranslate_ui()` and `tr(language, key)`.

- [x] **Step 1: Write failing window/menu tests**

Add `Qt` to the test imports and replace the old X-exit test with assertions equivalent to:

```python
self.assertTrue(window.windowFlags() & Qt.WindowMinimizeButtonHint)
self.assertFalse(window.windowFlags() & Qt.WindowCloseButtonHint)
self.assertEqual(window.file_menu.title(), "파일")
self.assertEqual(window.action_minimize.text(), "최소화")
self.assertEqual(window.action_full_exit.text(), "전체 종료")
window.change_language("en")
self.assertEqual(window.file_menu.title(), "File")

window.show()
window.close()
self.app.processEvents()
self.assertFalse(window._closing)
self.assertTrue(window.isMinimized())
```

Add a test helper that performs cleanup without invoking background shutdown:

```python
def dispose_window(self, window):
    window._explicit_exit = True
    window.close()
    self.app.processEvents()
```

Use it in all existing `finally` blocks so minimize-first behavior does not leak test windows.

- [x] **Step 2: Run the focused tests and confirm RED**

Run: `python -m unittest tests.test_gui_i18n.GuiI18nTests.test_language_menu_switches_ui_and_persists tests.test_gui_i18n.GuiI18nTests.test_window_close_minimizes_without_exiting -v`

Expected: FAIL because `file_menu`, actions, window flags, and guarded close behavior do not exist.

- [x] **Step 3: Implement the minimal native window and menu behavior**

Initialize and build controls before the existing language/admin menus:

```python
self._explicit_exit = False
self._configure_window_controls()
self._build_file_menu()

def _configure_window_controls(self) -> None:
    flags = self.windowFlags()
    flags |= Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
    flags &= ~Qt.WindowCloseButtonHint
    self.setWindowFlags(flags)

def _build_file_menu(self) -> None:
    self.file_menu = self.menuBar().addMenu("")
    self.action_minimize = QAction(self)
    self.action_full_exit = QAction(self)
    self.action_minimize.triggered.connect(self.minimize_window)
    self.action_full_exit.triggered.connect(self.request_full_exit)
    self.file_menu.addAction(self.action_minimize)
    self.file_menu.addSeparator()
    self.file_menu.addAction(self.action_full_exit)

def minimize_window(self) -> None:
    self.showMinimized()
```

Guard `closeEvent` before existing cleanup:

```python
if not self._explicit_exit:
    event.ignore()
    self.showMinimized()
    return
```

Add `menu.file`, `menu.minimize`, and `menu.full_exit` Korean/English keys and set all three labels in `_retranslate_ui()`.

- [x] **Step 4: Run focused GUI tests and confirm GREEN**

Run: `python -m unittest tests.test_gui_i18n.GuiI18nTests.test_language_menu_switches_ui_and_persists tests.test_gui_i18n.GuiI18nTests.test_window_close_minimizes_without_exiting -v`

Expected: 2 tests pass.

- [x] **Step 5: Commit the independently usable minimize-first UI**

```powershell
git add storage_manager/gui.py storage_manager/i18n.py tests/test_gui_i18n.py
git commit -m "Add minimize-first window controls"
```

### Task 2: Full Exit Background Shutdown

**Files:**
- Modify: `storage_manager/gui.py:590-650,860-894`
- Modify: `storage_manager/i18n.py:39-70`
- Test: `tests/test_gui_i18n.py`

**Interfaces:**
- Produces: `MainWindow.request_full_exit() -> None` and `MainWindow._run_full_exit_steps() -> tuple[list[str], list[str]]`.
- Consumes: `remove_cron()`, `remove_notifier_autostart()`, `read_notifier_status()`, `request_notifier_stop()`, `read_scan_status()`, `request_scan_stop()`, `ACTIVE_NOTIFIER_STATES`, and `ACTIVE_STATES`.

- [x] **Step 1: Write failing cancellation, success, and failure tests**

Cancellation must assert no background mutator runs. Success must patch active notifier/scan states and assert all four controls are called before `_closing` becomes true. Failure must raise from cron removal, allow the other independent steps to run, assert `QMessageBox.critical` contains completed and failed sections, and assert `_closing` remains false.

```python
with patch("storage_manager.gui.QMessageBox.question", return_value=QMessageBox.Yes), \
     patch("storage_manager.gui.remove_cron") as remove_cron_mock, \
     patch("storage_manager.gui.remove_notifier_autostart") as remove_auto, \
     patch("storage_manager.gui.read_notifier_status", return_value={"state": "running"}), \
     patch("storage_manager.gui.request_notifier_stop", return_value=True) as stop_notifier, \
     patch("storage_manager.gui.read_scan_status", return_value={"state": "running"}), \
     patch("storage_manager.gui.request_scan_stop", return_value=True) as stop_scan:
    window.request_full_exit()

remove_cron_mock.assert_called_once_with()
remove_auto.assert_called_once_with()
stop_notifier.assert_called_once_with(data_dir)
stop_scan.assert_called_once_with(data_dir)
self.assertTrue(window._closing)
```

- [x] **Step 2: Run full-exit tests and confirm RED**

Run: `python -m unittest tests.test_gui_i18n.GuiI18nTests.test_full_exit_cancel_changes_nothing tests.test_gui_i18n.GuiI18nTests.test_full_exit_stops_all_managed_background_activity tests.test_gui_i18n.GuiI18nTests.test_full_exit_failure_keeps_gui_open -v`

Expected: FAIL because `request_full_exit` and the step runner do not exist.

- [x] **Step 3: Implement independent shutdown steps and guarded explicit close**

The step runner records translated completion labels and exception messages:

```python
def _run_full_exit_steps(self):
    completed, failed = [], []
    def run_step(key, operation):
        label = self.t(key)
        try:
            operation()
            completed.append(label)
        except Exception as exc:
            failed.append(f"{label}: {exc}")

    run_step("exit.step.cron", remove_cron)
    run_step("exit.step.autostart", remove_notifier_autostart)

    def stop_notifier_if_active():
        status = read_notifier_status(self.data_dir)
        if str(status.get("state") or "never") in ACTIVE_NOTIFIER_STATES:
            request_notifier_stop(self.data_dir)

    def stop_scan_if_active():
        status = read_scan_status(self.data_dir)
        if str(status.get("state") or "never") in ACTIVE_STATES:
            request_scan_stop(self.data_dir)

    run_step("exit.step.notifier", stop_notifier_if_active)
    run_step("exit.step.scan", stop_scan_if_active)
    return completed, failed
```

`request_full_exit()` asks for confirmation, runs every independent step, keeps the GUI open on any failure, and otherwise sets `_explicit_exit = True` before `close()`:

```python
def request_full_exit(self) -> None:
    answer = QMessageBox.question(
        self,
        self.t("exit.title"),
        self.t("exit.confirm"),
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if answer != QMessageBox.Yes:
        return
    completed, failed = self._run_full_exit_steps()
    if failed:
        QMessageBox.critical(
            self,
            self.t("exit.failed.title"),
            self.t(
                "exit.failed.message",
                completed="\n".join(completed) or self.t("exit.none"),
                failed="\n".join(failed),
            ),
        )
        self.refresh_tracking(check_cron=True)
        return
    self._explicit_exit = True
    self.close()
```

Add bilingual keys for the confirmation, completed/failed headings, step names, and failure dialog. Remove the obsolete X-exit informational copy.

- [x] **Step 4: Run all GUI tests and confirm GREEN**

Run: `python -m unittest tests.test_gui_i18n -v`

Expected: all GUI tests pass with no leaked window or closed-database callback.

- [x] **Step 5: Commit the full-exit workflow**

```powershell
git add storage_manager/gui.py storage_manager/i18n.py tests/test_gui_i18n.py
git commit -m "Add explicit full background shutdown"
```

### Task 3: Documentation, Regression Verification, And Delivery

**Files:**
- Modify: `README.md:186-215`
- Modify: `VWP_ACCEPTANCE.md`
- Modify: `docs/superpowers/plans/2026-07-15-explicit-full-exit.md` (check completed steps)

**Interfaces:**
- Consumes: final GUI and background shutdown behavior from Tasks 1 and 2.
- Produces: operator guidance and verified `main` delivery.

- [x] **Step 1: Update operator and acceptance documentation**

Document that the title bar has no X, normal close requests minimize, and `File > Full Exit` removes managed cron and notifier autostart, sends safe-stop requests, preserves data, and requires explicit re-enabling on next launch. Add an RHEL/MATE acceptance item to visually confirm native title-bar hints.

- [x] **Step 2: Run complete verification**

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py storage_notifier.py storage_manager tests
python runtime_check.py --python-only
git diff --check
```

Expected: all discovered tests pass; the two Windows symlink tests may remain skipped. Compilation, runtime checks, and diff checks exit 0.

- [x] **Step 3: Review the complete diff and runtime-state exclusions**

```powershell
git status -sb
git diff --stat origin/main...HEAD
git ls-files | Select-String -Pattern '\.(db|log|lock)$|notifications/'
```

Expected: only source, tests, specs/plans, and docs are tracked; no runtime database, log, lock, or notification outbox file appears.

- [x] **Step 4: Commit documentation and push directly to main**

```powershell
git add README.md VWP_ACCEPTANCE.md docs/superpowers/plans/2026-07-15-explicit-full-exit.md
git commit -m "Document explicit full exit workflow"
git push origin main
```

- [x] **Step 5: Verify remote and provide VS Code test commands**

```powershell
git status -sb
git rev-parse HEAD
git rev-parse '@{upstream}'
code C:\edu\storage_manager_vwp
```

Expected: local and upstream SHAs match. Completion guidance explains selecting the Python 3.10.9 interpreter, setting `QT_QPA_PLATFORM` only for automated headless tests, launching `python app.py --data-dir <temporary-path>` from the VS Code terminal, and using a disposable data directory so no production cron is modified during Windows testing.
