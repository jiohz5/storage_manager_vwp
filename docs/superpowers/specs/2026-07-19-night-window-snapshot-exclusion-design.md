# Night Scan Window And Snapshot Exclusion Design

## Goal

Protect daytime project work from metadata-heavy storage scans while preserving
the existing 15-minute capacity warning. Exclude each account root's
`.snapshot` entry from detailed capacity analysis, changed-file activity, and
the administrator search index.

## Agreed Behavior

- Managed background detail work runs only inside the configured local-time
  window, which defaults to `22:00 <= time < 06:00`.
- At 06:00, an active `du`, `find`, or full search-index operation is terminated
  through the existing cooperative stop path. Durable checkpoints are kept and
  the next 22:00 run resumes or safely restarts the interrupted phase.
- The lightweight 15-minute `df` capacity watch continues all day.
- Heavy account work remains serial. At most one account has a detailed
  `du`, changed-file `find`, or full search-index traversal active in a nightly
  process, and the process lock rejects a second nightly run.
- The root entry `<account>/.snapshot` is ignored regardless of whether it is a
  directory, file, or symlink. Matching is case-sensitive, as on RHEL.
- Files or directories named `.snapshot` below another top-level project
  directory are not excluded. The special rule applies only to the account
  root entry requested by the operator.

## Trigger Policy

The scan window applies to managed background triggers:

- `cron`, installed for 22:00;
- `gui`, launched from the Tracking tab as a detached background process.

An explicit terminal invocation with the `command` or `direct` trigger remains
an operator-controlled diagnostic path and may run outside the window. This
keeps daytime debugging possible without exposing a GUI button that can
accidentally bypass the safety policy.

The configured start and end hours are interpreted using the RHEL host's local
time, the same clock used by cron. The helper supports windows that cross
midnight. Equal start and end hours mean a 24-hour window for compatibility.

## Window Enforcement

Window enforcement is cooperative and does not require a second 06:00 cron
entry. A scheduler predicate combines two independent reasons:

1. the existing user-created safe-stop request;
2. a managed run being outside its configured scan window.

The predicate is checked before each account and passed into every long-running
operation:

- resumable baseline `du` checks at least every 0.5 seconds and kills its child
  process when the window closes;
- changed-file `find` checks at least every 0.25 seconds and kills its child
  process;
- the Python search index checks between directory entries and batches.

After a window pause, the scheduler still finalizes the daily report, flushes
SQLite, and releases the process lock. It does not launch another detailed
account or search-index phase.

## Resume Semantics

- Resumable `du` keeps pending task rows and completed top-level results.
- A changed-file `find` does not advance the activity cursor unless it finishes,
  so it restarts from the same timestamp the following night.
- The full search index stores its current directory batch and paused state, then
  resumes from the remaining directory queue.
- Accounts not reached before 06:00 remain pending for the next nightly run.

No partial activity result is presented as a complete result.

## Runtime Status And GUI

An automatic window cutoff is recorded as `paused`, not as a user stop or
failure. The Tracking tab displays a translated state and message explaining
that the 06:00 boundary was reached and work will resume during the next scan
window. A manual stop continues to use the existing `stopped` state.

Report rows that were interrupted or not reached use a separate translated
`scan.window_closed` detail status. This avoids reporting a healthy automatic
pause as an error.

## `.snapshot` Exclusion

A shared path-policy helper identifies whether a candidate is the account
root's `.snapshot` entry or one of its descendants. All ingestion boundaries
apply the same policy:

- resumable `du` does not create or split work for the root `.snapshot` entry;
- any legacy `.snapshot` checkpoint task is skipped without traversing it and
  excluded from returned inventory totals;
- changed-file `find` uses a root-level `-path ... -prune` expression so the
  snapshot tree is never walked or emitted;
- incremental search-index batches defensively reject snapshot paths;
- full search indexing skips the root entry before stat calls or child-task
  creation;
- old search rows and paused directory tasks below `.snapshot` are removed when
  the account index is next opened for a run;
- the legacy top-level `du` backend also excludes `.snapshot` for behavioral
  consistency.

The `df` dashboard cannot and need not subtract a directory: `df` reports the
filesystem allocation rather than a directory tree. This design assumes the
operator's storage snapshot implementation does not charge `.snapshot` views
again in filesystem usage.

## Load Safety

The current `nice -n 10` and `ionice -c 2 -n 7` prefix remains in place. These
settings do not impose a hard throughput cap: the serial scanner can use idle
resources at night, but yields priority when the host or block device is busy.
`ionice` may have limited effect on network storage, so the 06:00 termination
boundary and serial account execution are the primary daytime safeguards.

The feature does not run multiple heavy accounts concurrently and does not load
file contents into memory. It can still generate substantial metadata I/O for
an account with millions of files during the night; preventing all storage-side
impact would require infrastructure-level I/O controls outside this application.

## Error Handling

- A cutoff is successful control flow, not an exception or failed health state.
- Manual safe-stop and automatic cutoff reasons remain distinguishable.
- An unreadable `.snapshot` does not fail a scan because it is rejected by name
  before traversal.
- Existing permission, command, database, and process-lock failures retain their
  current behavior.

## Tests

Automated tests will verify:

- overnight, daytime, non-crossing, and equal-hour window calculations;
- a managed scan started outside the window performs `df` but no detail work;
- a window closing during `du`, `find`, and search indexing returns a paused,
  resumable result and does not start the next account;
- direct diagnostic runs remain available outside the managed window;
- root `.snapshot` is absent from baseline inventory and legacy checkpoints;
- the `find` command prunes `.snapshot` before file predicates;
- incremental and full search indexing never stores `.snapshot` paths and
  removes legacy rows;
- similarly named nested directories remain included;
- runtime status, Korean/English text, cron, 15-minute watch, and all existing
  storage tests remain green.

Actual cutoff timing, process priority, and storage load should also be checked
on RHEL/MATE with a disposable account before enabling many production
accounts.

## Out Of Scope

- Per-account parallelism or concurrent detailed scans.
- Hard CPU, memory, or network-filesystem bandwidth quotas.
- Filesystem-specific snapshot accounting corrections to `df`.
- Automatic deletion of project data.
