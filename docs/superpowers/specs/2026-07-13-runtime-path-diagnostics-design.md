# Runtime Path And Diagnostics Design

## Goal

Make first-run failures on RHEL 8.1 easy to diagnose without requiring users to
understand Python path internals. Keep the read-only Python installation, writable
Storage Manager state, and read-only project accounts clearly separated.

## Supported Runtime

- Python 3.10 or newer is required; Python 3.10.9 is supported.
- The selected interpreter must import `json`, `sqlite3`, and `PyQt5`.
- SQLite 3.24 or newer is required.
- The Python installation may be read-only.
- The application data directory must be writable.

`PYTHONHOME` is a Python interpreter control variable, not this application's
Python selector. The launch scripts ignore an inherited `PYTHONHOME` so it cannot
redirect standard-library imports. The supported selectors are, in order:

1. `STORAGE_MANAGER_PYTHON_BIN=/absolute/path/to/python3`
2. `STORAGE_MANAGER_PYTHON_HOME=/absolute/python/prefix`
3. `python3` from `PATH`

## Data Directory

There is no implicit data directory in the 1GB personal account. Resolution uses
the following order:

1. explicit `--data-dir`
2. `STORAGE_MANAGER_DATA_DIR`
3. the previously selected global data path
4. a first-run directory selection dialog

The selected global path is remembered in
`$XDG_CONFIG_HOME/storage-manager-vwp/location.json`, or
`$HOME/.config/storage-manager-vwp/location.json` when `XDG_CONFIG_HOME` is not
set. This pointer is below 1KB; SQLite, reports, logs, notifications, and all other
state remain in the selected external directory. The source directory and Python
home are never used as fallback state locations.

The startup check creates the selected directory if possible, performs temporary
file and SQLite write probes, reports free filesystem space, measures current
application-state size, and records the result in a UTF-8 diagnostic JSON file. A
failure explains which environment variable or saved location must be corrected
before the GUI starts.

If a project account is chosen as the global data location, `newgrp` may be used
manually to create its private Storage Manager subdirectory. After that bootstrap,
the same path must pass a write probe from a normal shell without `newgrp`; otherwise
cron cannot be trusted to persist alerts and the path is rejected. The application
does not run `newgrp` itself.

The default soft warning size is 500MB. This is not a hard limit: collection is not
stopped and data is not automatically deleted. The health check reports a warning
when the state directory exceeds the threshold. Existing retention remains:

- 15-minute capacity samples: 30 days
- Daily history and reports: 365 days
- Rotated logs: bounded backups

Measured SQLite reference sizes using the production schema:

| Scenario | SQLite size |
|---|---:|
| 10 accounts, 30 days of 15-minute samples | 9.61MB |
| 20 accounts, 30 days of 15-minute samples | 19.18MB |
| 20 accounts and 1,000 top-level items per account | 24.98MB |
| 20 accounts and 10,000 top-level items per account | 77.21MB |

Reports, notifications, checkpoints, and logs make the expected operational range
about 50-150MB. Reserve 300MB for normal operation; investigate at the 500MB soft
warning before a 1GB personal quota is endangered.

## Startup Diagnostics

`run.csh` and `setup_cron.csh` print a compact preflight summary containing:

- selected Python executable and version
- `json` module path
- SQLite and PyQt5 availability
- selected data directory, write result, current size, and free space
- effective user and supplementary groups on RHEL

`run.csh --diagnose` runs the fuller existing environment verifier without opening
the GUI. If no global data directory has been selected yet, it reports that setup
condition without creating state in the source or personal account. Normal launch
failures point to the saved diagnostic file and the `--diagnose` command.

Configuration errors distinguish invalid JSON from read/write permission failures.
An invalid `accounts.json` is never silently replaced.

## Project Group Access

The application does not invoke `newgrp`, `sg`, `sudo`, or any other privilege
switch. Project paths are read-only and must already be readable by the process.

If `id -Gn` includes all required project groups, supplementary group membership is
sufficient and changing the primary group is normally unnecessary for read-only
monitoring. A manual `newgrp` affects only its child shell and is not reliably
inherited by cron, so it is not used as an automation mechanism.

Account registration only requires the equivalent of `cd /user/account` plus the
read access needed by `df`, `du`, and `find`. An inaccessible account produces a
per-account permission error while other accounts continue to be monitored. The
07:00 health check reports accounts whose 15-minute samples are missing or stale,
which also exposes cron-only group differences.

All accounts share one central SQLite database. Separate databases are never
created inside each monitored project account. When the selected data directory and
a monitored account are on the same filesystem, registration displays a strong
warning that a full filesystem can prevent alert persistence. The user may confirm
and continue, but a separate management filesystem remains recommended.

## Safety

- No files are written below monitored project paths except an explicitly selected
  private Storage Manager data directory.
- No project process is stopped and no project file is deleted.
- Diagnostic output includes paths and group names but no tokens, passwords, or
  full environment dump.
- Data-size checks do not follow symbolic links.
- A full or read-only data directory fails before cron installation or GUI startup.

## Verification

- Unit tests cover Python selector precedence and removal of `PYTHONHOME` fallback.
- Unit tests cover data-path resolution precedence, the sub-1KB pointer, first-run
  selection behavior, writable and invalid data paths, invalid JSON diagnostics,
  and state-size calculation without symlink traversal.
- Unit tests cover the 500MB health warning.
- Shell-source assertions confirm both csh launchers use the same preflight rules.
- The complete test suite and offline bundle inspection must pass again.
- Final RHEL acceptance runs `run.csh --diagnose`, `id -Gn`, GUI launch, cron
  installation, and one manual capacity watch.
