# Explicit Full Exit Design

## Goal

Prevent users from accidentally stopping the management GUI through an unfamiliar
tray-style close flow. The native title bar must not expose a close button. Users
minimize the window during normal operation and use `File > Full Exit` only when
they intentionally want all Storage Manager background activity disabled.

## Window Behavior

- Keep the native MATE/Qt title bar and its minimize and maximize controls.
- Remove `Qt.WindowCloseButtonHint`; do not build a custom title bar.
- MATE controls the exact placement of the remaining native buttons. The product
  requirement is that no close button is available and a minimize button remains,
  not that Qt draws a replacement at an exact pixel position.
- A close request that did not originate from `File > Full Exit`, including
  `Alt+F4` or a window-manager close command, is ignored and the window is
  minimized instead.
- A new File menu contains `Minimize` and `Full Exit`. Both labels support Korean
  and English through the existing translation table.

## Full Exit Workflow

`File > Full Exit` displays a Yes/No confirmation that explicitly states that
future capacity checks, nightly scans, health checks, and popup delivery will stop.
Choosing No leaves the application unchanged.

Choosing Yes performs these operations:

1. Remove only Storage Manager's three managed cron entries. Unmanaged crontab
   entries are preserved by the existing `remove_cron` implementation.
2. Remove the MATE login autostart entry for `storage_notifier.py`.
3. If a notifier process is active, write its run-scoped safe-stop request.
4. If a nightly/detail scan is active, write its run-scoped safe-stop request.
5. Mark the close as explicit and run the existing GUI resource cleanup.

The short 15-minute capacity command may already be running when cron is removed.
It is not force-killed and may finish its current `df`; no future managed run is
scheduled.

The full exit does not delete settings, account configuration, history, reports,
notification outbox data, or search indexes. Reopening the GUI is allowed, but cron
and notifier autostart remain disabled until the user enables them again.

## Failure Handling

- Cron and autostart removal are attempted independently and failures are
  collected.
- If either removal fails, display the completed and failed steps and keep the GUI
  open. This avoids claiming a full exit when future background startup may still
  occur. Successful steps are not silently rolled back because restoring a
  partially modified crontab can itself fail.
- Safe-stop requests are best effort. A missing active process is a successful
  no-op. A request-file write failure is reported and keeps the GUI open.
- No arbitrary process is killed and no unmanaged cron entry is changed.

## Testing

Automated PyQt tests will verify:

- the close-button hint is absent while the minimize hint is present;
- the File menu and actions retranslate between Korean and English;
- an ordinary close request is ignored and minimizes the window;
- cancelling Full Exit changes no background state;
- successful Full Exit removes managed cron/autostart, requests active-process
  stops, and executes GUI cleanup;
- a removal or safe-stop error keeps the window open and reports the failure;
- late worker results remain unable to write after explicit shutdown begins.

Existing scheduler tests continue to verify that unmanaged crontab entries survive
managed cron removal. Final acceptance on RHEL 8.1/MATE verifies the native title
bar because Windows and MATE window managers may render hints differently.

## Documentation And Delivery

README and VWP acceptance guidance will describe minimize-first behavior, the
destructive scope of Full Exit, and how to re-enable monitoring. After automated
verification, the change will be committed and pushed directly to `main` as
requested. Windows VS Code launch and test instructions will be supplied with the
completion report.
