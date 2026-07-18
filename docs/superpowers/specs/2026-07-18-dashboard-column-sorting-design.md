# Dashboard Column Sorting Design

## Goal

Make every dashboard usage-table header clickable so an operator can sort the
current account rows. Preserve the selected column and direction across
automatic and manual dashboard refreshes during the current GUI session.

## Scope

- Change only the nine-column dashboard usage table.
- Keep the existing `QTableWidget`; do not migrate other tables or introduce a
  model/proxy layer.
- Do not persist sort state in `accounts.json`. A new application session starts
  in account-registration order with no active sort indicator.

## User Experience

- The initial row order remains the configured account order.
- The first click on a header sorts ascending.
- Repeated clicks on the same header alternate ascending and descending.
- Clicking another header starts ascending on that column.
- The active header displays Qt's native sort-direction indicator.
- Manual refresh, timer refresh, and language changes retain the active sort.

## Sort Semantics

Each displayed cell carries a separate sort key so formatted text does not
control numeric ordering.

| Column | Sort key |
| --- | --- |
| Account | Case-folded account name |
| Path | Case-folded absolute path |
| Usage | Raw byte-use percentage |
| Inode | Raw inode-use percentage |
| Quota | Raw quota-use percentage |
| Used | Raw used KiB |
| Total | Raw total KiB |
| Filesystem | Case-folded filesystem name |
| Status | Severity rank: checking/error, OK, WARN, ALERT, EMERGENCY, FULL |

Unavailable numeric values (`-` or `ERR`) receive a key lower than valid
measurements. They therefore appear before measurements in ascending order and
after measurements in descending order. This makes descending risk views place
real high-usage values first.

## Table Item And Sort State

A small `QTableWidgetItem` subclass compares an explicit sort key while keeping
the existing display text, colors, and tooltips unchanged. `MainWindow` stores
an optional active column and a Qt sort order. A header-click handler updates
that state, invokes `sortItems`, and controls the native indicator.

## Asynchronous Refresh Safety

The existing collector records account-to-row positions before starting
workers. Sorting invalidates those positions, so row numbers cannot remain the
identity source.

- Store the account ID on the account-name item using a dedicated Qt data role.
- Resolve the account's current row by scanning that role whenever a `df` result
  or error arrives.
- Temporarily suspend automatic sorting while replacing all cells in one row.
- Reapply the remembered sort after the row is complete.
- Rebuild rows with sorting suspended during a new refresh, then restore the
  remembered sort without clearing the selected column or direction.

Late results for removed or superseded accounts continue to be ignored. No
database write, alert decision, or storage command changes are part of this
feature.

## Error Handling

Collection failures remain displayed as `-` and the translated error status.
They retain the account ID and participate in sorting with unavailable numeric
keys. Sorting never launches a storage command and cannot alter account or
history data.

## Tests

PyQt unit tests will verify:

- headers are clickable and no sort is active initially;
- first and repeated clicks select ascending then descending order;
- percentages and capacities sort numerically rather than lexicographically;
- status values sort by severity rank;
- a `df` result updates the correct account after rows have moved;
- the active sort survives a dashboard refresh;
- existing language, close, cron, notifier, and search tests remain green.

Actual header rendering and indicator appearance remain part of the RHEL/MATE
acceptance pass because the native window theme is not available on Windows.

