# Dashboard Column Sorting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all dashboard usage-table headers sort numerically or textually on click while retaining the selected sort across dashboard refreshes and safely routing asynchronous results to the correct account row.

**Architecture:** Keep the existing `QTableWidget` and add a `QTableWidgetItem` subclass with an explicit sort key. Store the active sort in `MainWindow`, use one-shot `sortItems()` calls instead of permanent automatic sorting, and replace stale row-number identity with an account ID stored on each account cell.

**Tech Stack:** Python 3.10, PyQt5 5.15, `unittest`, existing offscreen Qt GUI tests.

## Global Constraints

- Introduce no dependency and preserve Python 3.10 compatibility.
- Change only the dashboard usage table; other tables retain their current behavior.
- Start each new GUI session in configured account order with no visible sort indicator.
- First click sorts ascending, repeated click toggles descending, and a different column starts ascending.
- Retain sort column and direction across manual, timer, and language-triggered dashboard refreshes in the current GUI session only.
- Sort percentages and KiB values numerically, text case-insensitively, and status by severity.
- Preserve all existing cell text, colors, tooltips, database writes, alerts, and storage commands.
- Push the verified result directly to `main` as previously requested.

---

### Task 1: Sortable Items And Header State

**Files:**
- Modify: `storage_manager/gui.py:23-46,467-505,964-983`
- Test: `tests/test_gui_i18n.py:1-35,240-300`

**Interfaces:**
- Produces: `SortableTableWidgetItem(text: str, sort_key: object)`, `dashboard_status_rank(use_pct: int, alert_threshold: int) -> int`, `MainWindow.sort_dashboard(column: int) -> None`, and `MainWindow._apply_dashboard_sort() -> None`.
- Consumes: `Qt.AscendingOrder`, `Qt.DescendingOrder`, `QHeaderView`, and the existing dashboard `QTableWidget`.

- [ ] **Step 1: Write failing item and header tests**

Import `storage_manager.gui as gui` so the missing interfaces produce a clear assertion failure instead of an import error. Add a GUI test that creates a no-account window, stops its initial refresh timer, inserts rows whose display text would sort incorrectly as strings, and exercises the connected header signal:

```python
def test_dashboard_headers_toggle_numeric_and_status_sorting(self):
    item_type = getattr(gui, "SortableTableWidgetItem", None)
    ranker = getattr(gui, "dashboard_status_rank", None)
    self.assertTrue(callable(item_type))
    self.assertTrue(callable(ranker))
    with tempfile.TemporaryDirectory() as temp:
        data_dir = Path(temp) / "data"
        save_store(data_dir, AccountStore(Settings(), []))
        with patch(
            "storage_manager.gui.read_cron_status",
            return_value=CronStatus(False, False, error="not available"),
        ):
            window = MainWindow(data_dir)
        try:
            window.initial_refresh_timer.stop()
            header = window.table_usage.horizontalHeader()
            self.assertTrue(header.sectionsClickable())
            self.assertFalse(header.isSortIndicatorShown())

            rows = [
                ("nine", "9%", 9, "정상", 1),
                ("one_hundred", "100%", 100, "FULL", 5),
                ("eighty", "80%", 80, "주의", 2),
            ]
            window.table_usage.setRowCount(len(rows))
            for row, (name, use_text, use_key, status_text, status_key) in enumerate(rows):
                window.table_usage.setItem(
                    row, 0, item_type(name, name.casefold())
                )
                window.table_usage.setItem(
                    row, 2, item_type(use_text, use_key)
                )
                window.table_usage.setItem(
                    row, 8, item_type(status_text, status_key)
                )

            header.sectionClicked.emit(2)
            self.assertEqual(window.dashboard_sort_column, 2)
            self.assertEqual(window.dashboard_sort_order, Qt.AscendingOrder)
            self.assertEqual(
                [window.table_usage.item(row, 0).text() for row in range(3)],
                ["nine", "eighty", "one_hundred"],
            )
            self.assertTrue(header.isSortIndicatorShown())
            self.assertEqual(header.sortIndicatorSection(), 2)

            header.sectionClicked.emit(2)
            self.assertEqual(window.dashboard_sort_order, Qt.DescendingOrder)
            self.assertEqual(window.table_usage.item(0, 0).text(), "one_hundred")

            header.sectionClicked.emit(8)
            self.assertEqual(window.dashboard_sort_order, Qt.AscendingOrder)
            self.assertEqual(window.table_usage.item(0, 0).text(), "nine")
            self.assertEqual(ranker(100, 95), 5)
            self.assertGreater(
                ranker(98, 95),
                ranker(95, 95),
            )
        finally:
            self.dispose_window(window)
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run:

```powershell
python -m unittest tests.test_gui_i18n.GuiI18nTests.test_dashboard_headers_toggle_numeric_and_status_sorting -v
```

Expected: FAIL on `callable(item_type)` because the sortable item and related interfaces do not exist.

- [ ] **Step 3: Implement explicit sort keys and session sort state**

Add the item and severity helper above `MainWindow`:

```python
class SortableTableWidgetItem(QTableWidgetItem):
    def __init__(self, text: str, sort_key: object):
        super().__init__(text)
        self.sort_key = sort_key

    def __lt__(self, other) -> bool:
        if isinstance(other, SortableTableWidgetItem):
            return self.sort_key < other.sort_key
        return super().__lt__(other)


def dashboard_status_rank(use_pct: int, alert_threshold: int) -> int:
    if use_pct >= 100:
        return 5
    if use_pct >= 98:
        return 4
    if use_pct >= alert_threshold:
        return 3
    if use_pct >= 90:
        return 2
    return 1
```

Initialize state before `_build_dashboard_tab()` is called:

```python
self.dashboard_sort_column: Optional[int] = None
self.dashboard_sort_order = Qt.AscendingOrder
```

Configure and connect the header in `_build_dashboard_tab()`:

```python
self.table_usage.setSortingEnabled(False)
header = self.table_usage.horizontalHeader()
header.setSectionsClickable(True)
header.setSortIndicatorShown(False)
header.sectionClicked.connect(self.sort_dashboard)
```

Add the stateful handler:

```python
def sort_dashboard(self, column: int) -> None:
    if self.dashboard_sort_column == column:
        self.dashboard_sort_order = (
            Qt.DescendingOrder
            if self.dashboard_sort_order == Qt.AscendingOrder
            else Qt.AscendingOrder
        )
    else:
        self.dashboard_sort_column = column
        self.dashboard_sort_order = Qt.AscendingOrder
    self._apply_dashboard_sort()

def _apply_dashboard_sort(self) -> None:
    header = self.table_usage.horizontalHeader()
    if self.dashboard_sort_column is None:
        header.setSortIndicatorShown(False)
        return
    header.setSortIndicator(
        self.dashboard_sort_column,
        self.dashboard_sort_order,
    )
    header.setSortIndicatorShown(True)
    self.table_usage.sortItems(
        self.dashboard_sort_column,
        self.dashboard_sort_order,
    )
```

- [ ] **Step 4: Run the focused test and confirm GREEN**

Run the command from Step 2. Expected: 1 test passes.

- [ ] **Step 5: Commit the independently testable sorting infrastructure**

```powershell
git add storage_manager/gui.py tests/test_gui_i18n.py
git commit -m "Add dashboard header sorting"
```

### Task 2: Refresh-Safe Account Routing And Real Sort Keys

**Files:**
- Modify: `storage_manager/gui.py:467-505,2398-2598`
- Test: `tests/test_gui_i18n.py`

**Interfaces:**
- Consumes: `SortableTableWidgetItem`, `dashboard_status_rank()`, `MainWindow._apply_dashboard_sort()`, `UsageSnapshot`, and `Account.account_id`.
- Produces: `DASHBOARD_ACCOUNT_ID_ROLE`, `MainWindow._dashboard_item()`, and `MainWindow._dashboard_row(account_id: str) -> Optional[int]`.

- [ ] **Step 1: Write a failing sorted-row asynchronous update test**

Add a test with configured order `zeta`, then `alpha`. Suppress worker execution, sort account names, deliver results by account ID, then refresh again:

```python
def test_dashboard_sort_routes_results_by_account_and_survives_refresh(self):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        data_dir = root / "data"
        account_root = root / "user"
        zeta_path = account_root / "zeta"
        alpha_path = account_root / "alpha"
        zeta_path.mkdir(parents=True)
        alpha_path.mkdir()
        accounts = [
            Account("zeta", str(zeta_path), account_id="id-z"),
            Account("alpha", str(alpha_path), account_id="id-a"),
        ]
        save_store(
            data_dir,
            AccountStore(Settings(monitored_roots=[str(account_root)]), accounts),
        )
        with patch(
            "storage_manager.gui.read_cron_status",
            return_value=CronStatus(False, False, error="not available"),
        ):
            window = MainWindow(data_dir)
        try:
            window.initial_refresh_timer.stop()
            with patch.object(window.thread_pool, "start"):
                window.refresh_dashboard()

            window.sort_dashboard(0)
            self.assertEqual(window.table_usage.item(0, 0).text(), "alpha")

            window.on_df_result(
                "id-z",
                UsageSnapshot("fs-z", 1000, 950, 50, 95),
            )
            self.assertTrue(callable(getattr(window, "_dashboard_row", None)))
            zeta_row = window._dashboard_row("id-z")
            self.assertIsNotNone(zeta_row)
            self.assertEqual(window.table_usage.item(zeta_row, 0).text(), "zeta")
            self.assertEqual(window.table_usage.item(zeta_row, 2).text(), "95%")
            self.assertEqual(window.table_usage.item(zeta_row, 7).text(), "fs-z")

            window.sort_dashboard(2)
            self.assertEqual(window.dashboard_sort_column, 2)
            self.assertEqual(window.dashboard_sort_order, Qt.AscendingOrder)
            window.refresh_pending = 0
            with patch.object(window.thread_pool, "start"):
                window.refresh_dashboard()
            self.assertEqual(window.dashboard_sort_column, 2)
            self.assertEqual(window.dashboard_sort_order, Qt.AscendingOrder)
            self.assertEqual(
                window.table_usage.horizontalHeader().sortIndicatorSection(),
                2,
            )
        finally:
            self.dispose_window(window)
```

- [ ] **Step 2: Run the asynchronous test and confirm RED**

Run:

```powershell
python -m unittest tests.test_gui_i18n.GuiI18nTests.test_dashboard_sort_routes_results_by_account_and_survives_refresh -v
```

Expected: FAIL on the `_dashboard_row` callable assertion because sorted rows still use `row_by_account_id` and the current-row resolver does not exist.

- [ ] **Step 3: Add account identity and keyed item helpers**

Add a dedicated role and helpers:

```python
DASHBOARD_ACCOUNT_ID_ROLE = Qt.UserRole + 1
DASHBOARD_UNAVAILABLE_SORT_KEY = -1

def _dashboard_item(
    self,
    text: str,
    sort_key: object,
    account_id: Optional[str] = None,
) -> SortableTableWidgetItem:
    item = SortableTableWidgetItem(text, sort_key)
    if account_id is not None:
        item.setData(DASHBOARD_ACCOUNT_ID_ROLE, account_id)
    return item

def _dashboard_row(self, account_id: str) -> Optional[int]:
    for row in range(self.table_usage.rowCount()):
        item = self.table_usage.item(row, 0)
        if item is not None and item.data(DASHBOARD_ACCOUNT_ID_ROLE) == account_id:
            return row
    return None
```

Remove `row_by_account_id` initialization and assignments. In `refresh_dashboard()`, create all nine initial cells with these keys, putting the ID on column zero:

```python
values_and_keys = [
    (account.name, account.name.casefold()),
    (account.path, account.path.casefold()),
    ("-", DASHBOARD_UNAVAILABLE_SORT_KEY),
    ("-", DASHBOARD_UNAVAILABLE_SORT_KEY),
    ("-", DASHBOARD_UNAVAILABLE_SORT_KEY),
    ("-", DASHBOARD_UNAVAILABLE_SORT_KEY),
    ("-", DASHBOARD_UNAVAILABLE_SORT_KEY),
    ("-", ""),
    (self.t("status.checking"), DASHBOARD_UNAVAILABLE_SORT_KEY),
]
for column, (value, sort_key) in enumerate(values_and_keys):
    item = self._dashboard_item(
        value,
        sort_key,
        account.account_id if column == 0 else None,
    )
    if column in (2, 3, 4):
        item.setBackground(QColor(usage_color(0, failed=True)))
    self.table_usage.setItem(row, column, item)
self._apply_dashboard_sort()
```

In `on_df_result()`, obtain the current row with `_dashboard_row(account_id)`, replace all cells with keys below, retain the existing backgrounds, and reapply sorting after the complete row is written:

```python
values_and_keys = [
    (account.name, account.name.casefold()),
    (account.path, account.path.casefold()),
    (f"{snapshot.use_pct}%", snapshot.use_pct),
    (
        "-" if snapshot.inode_use_pct is None else f"{snapshot.inode_use_pct}%",
        DASHBOARD_UNAVAILABLE_SORT_KEY
        if snapshot.inode_use_pct is None
        else snapshot.inode_use_pct,
    ),
    (
        "ERR"
        if snapshot.quota_error
        else "-"
        if snapshot.quota_use_pct is None
        else f"{snapshot.quota_use_pct}%",
        DASHBOARD_UNAVAILABLE_SORT_KEY
        if snapshot.quota_error or snapshot.quota_use_pct is None
        else snapshot.quota_use_pct,
    ),
    (human_kb(snapshot.used_kb), snapshot.used_kb),
    (human_kb(snapshot.total_kb), snapshot.total_kb),
    (snapshot.fs_name, snapshot.fs_name.casefold()),
    (
        status_text,
        dashboard_status_rank(
            effective_pct,
            self.store.settings.alert_threshold,
        ),
    ),
]
```

Use `_dashboard_item()` for every value, attach the account ID to column zero, then call `self._apply_dashboard_sort()`. In `on_df_error()`, resolve the row by account ID, replace columns 2/3/4 and 8 with unavailable keyed items, retain failure colors, and reapply the active sort.

- [ ] **Step 4: Run both dashboard sorting tests and confirm GREEN**

Run:

```powershell
python -m unittest tests.test_gui_i18n.GuiI18nTests.test_dashboard_headers_toggle_numeric_and_status_sorting tests.test_gui_i18n.GuiI18nTests.test_dashboard_sort_routes_results_by_account_and_survives_refresh -v
```

Expected: 2 tests pass with no row misrouting or closed-database callback.

- [ ] **Step 5: Run all GUI tests and commit the refresh-safe integration**

```powershell
python -m unittest tests.test_gui_i18n -v
git add storage_manager/gui.py tests/test_gui_i18n.py
git commit -m "Keep dashboard sorting safe during refresh"
```

Expected: all GUI tests pass.

### Task 3: Operator Documentation, Full Verification, And Delivery

**Files:**
- Modify: `README.md:9-32,120-145`
- Modify: `VWP_ACCEPTANCE.md:82-100`
- Modify: `docs/superpowers/plans/2026-07-19-dashboard-column-sorting.md` (check completed steps)

**Interfaces:**
- Consumes: final dashboard sorting behavior from Tasks 1 and 2.
- Produces: operator guidance, RHEL/MATE acceptance coverage, and verified GitHub delivery.

- [ ] **Step 1: Document sorting behavior and acceptance checks**

Add a README feature bullet stating that all dashboard headers support numeric/text sorting and retain the active order across refreshes. Add VWP acceptance bullets that verify header arrows, ascending/descending toggling, numeric `9% < 80% < 100%` behavior, and correct account updates while refresh results arrive.

- [ ] **Step 2: Run complete verification**

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py storage_notifier.py storage_manager tests
python runtime_check.py --python-only
git diff --check
```

Expected: all discovered tests pass; the two Windows symlink tests may remain skipped. Compilation, runtime, and diff checks exit 0.

- [ ] **Step 3: Review scope and runtime-state exclusions**

```powershell
git status -sb
git diff --stat origin/main
git ls-files | Select-String -Pattern '\.(db|log|lock)$|(^|/)notifications/'
```

Expected: only source, tests, design/plan, and operator documentation changed; no runtime database, log, lock, or notification outbox is tracked.

- [ ] **Step 4: Commit documentation and completed plan state**

```powershell
git add README.md VWP_ACCEPTANCE.md docs/superpowers/plans/2026-07-19-dashboard-column-sorting.md
git commit -m "Document dashboard column sorting"
```

- [ ] **Step 5: Push directly to GitHub main and verify equality**

```powershell
git push origin main
git status -sb
git rev-parse HEAD
git rev-parse origin/main
```

Expected: `main` is clean and local HEAD equals `origin/main`.
