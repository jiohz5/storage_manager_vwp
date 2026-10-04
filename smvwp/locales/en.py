"""English UI strings.

Keys must match `ko.py` (`test_i18n` compares them). 한국어가 기본이고,
영어에 빠진 키는 한국어로 되돌아간다 (`i18n.t`).
"""

STRINGS = {
    # -- Tiers -------------------------------------------------
    "tier.normal": "Normal",
    "tier.warn": "Warning",
    "tier.alert": "Alert",
    "tier.emergency": "Emergency",
    "tier.full": "Full",
    "tier.unknown": "Unknown",
    # -- Common ------------------------------------------------
    "common.none": "-",
    "common.save": "Save",
    "common.cancel": "Cancel",
    "common.close": "Close",
    # -- Account detail dialog ------------------------------------
    "detail.title": "{account} - account detail",
    "detail.loading": "Reading...",
    "detail.failed": "Could not read: {error}",
    "detail.gone": "That account was not found (it may have been removed).",
    "detail.partial_failure": "Could not read: {parts}. The rest is unaffected.",
    "detail.part.sample": "current usage",
    "detail.part.trend": "trend",
    "detail.part.forecast": "FULL forecast",
    "detail.part.scan": "detail scan results",
    "detail.part.health": "backup check",
    "detail.backup_link": "backup: {account}",
    "detail.now_heading": "Now",
    "detail.capacity": "Used / total",
    "detail.free": "Free",
    "detail.inode": "inode usage",
    "detail.quota": "quota",
    "detail.collected": "Last collected",
    "detail.error": "Collection error: {message}",
    "detail.no_sample": "No sample yet (one collection run fills this in).",
    "detail.forecast_heading": "FULL forecast",
    "detail.scan_heading": "Nightly detail scan",
    "detail.scan_line": "{when} scan · measured {size}",
    "detail.scan_delta": "{delta} vs the previous scan",
    "detail.scan_never": "No completed detail scan yet.",
    "detail.health_heading": "Task backups",
    "detail.health_line": "{done} confirmed · {risky} not confirmed",
    "detail.health_none": "No run directories to check.",
    "detail.health_no_link": "No linked backup account, so nothing was judged.",
    "detail.reclaimable": "{size} would be freed (confirmed backups only)",
    "detail.tab.large": "Large files",
    "detail.tab.growth": "Growth paths",
    "detail.tab.health": "Backups by task",
    "detail.health.col.task": "Task / run",
    "detail.health.col.status": "Backup",
    "detail.health.col.size": "Size",
    "detail.health.col.reclaim": "Reclaimable",
    "detail.no_rows": "Nothing to show yet (one detail scan fills this in).",
    "detail.btn.trend": "Trend",
    "detail.btn.scan": "Open in detail scan",
    "health.status.backed_up": "confirmed",
    "health.status.missing": "not backed up",
    "health.status.partial": "partial",
    "health.status.no_backup_dir": "before BACKUP",
    "health.status.no_link": "no link",
    "scan.tab.summary": "Summary",
    "scan.tab.accounts": "By account",
    "scan.tab.growth": "Growth paths",
    "scan.tab.large": "Large files",
    "trend.title": "Capacity trend",
    "trend.btn.open": "Trend",
    "trend.col": "Trend",
    "trend.range_label": "Range",
    "trend.range_days": "last {days} days",
    "trend.loading": "Loading...",
    "trend.failed": "Could not load: {error}",
    "trend.no_account": "Pick an account to see its trend.",
    "trend.no_data": "no history",
    "trend.no_data_in_range": "No samples in this range. They build up once the collector runs.",
    "trend.summary": "now {now}% - low {low}% / high {high}% over this range",
    "trend.range": "low {low}% / high {high}%",
    "trend.change": "{delta}%p over the range",
    "trend.gaps": "{count} buckets with no samples (where the line breaks)",
    "trend.note": (
        "The vertical axis is fixed at 0-100%. Stretching it to the data would make "
        "78% to 80% fill the screen and look like a crisis. The dashed lines are "
        "warning (90%) and alert (95%).\n"
        "A break in the line means the collector was not running then - joining it "
        "up would show values we do not have."
    ),
    # -- What to do first: guide shown on click -------------------
    "priority.hint": "Click a line to see the situation and what to do",
    "guide.title": "{account} - what to do",
    "guide.title_general": "What to do",
    "guide.level.critical": "Act now",
    "guide.level.high": "Today",
    "guide.level.medium": "Good to know",
    "guide.section.situation": "Situation",
    "guide.section.why": "Why it comes first",
    "guide.section.steps": "What to do",
    "guide.section.commands": "Check commands - read-only",
    "guide.loading": "Reading the evidence...",
    "guide.no_evidence": "No evidence yet (one nightly detail scan fills this in).",
    "guide.never_deletes": "This tool never deletes files. Ask the task owner to clean up.",
    "guide.btn.copy_commands": "Copy commands",
    "guide.btn.copy_path": "Copy path",
    "guide.btn.account": "Account detail",
    "guide.btn.scan": "Go to detail scan",
    "guide.copied": "Copied: {text}",
    "guide.partial": "Could not read {parts}; some evidence is missing.",
    "guide.more": " and {count} more",
    "guide.coarse_warning": (
        "Some of these were matched by run folder name and size only, without seeing "
        "inside BACKUP. Open the backup side once before asking for a cleanup."
    ),
    "guide.col.task": "Task / run",
    "guide.col.reclaim": "Frees",
    "guide.col.backup_size": "Backup size",
    "guide.col.backup_at": "Backup location",
    "guide.col.path": "Path",
    "guide.col.size": "Size",
    "guide.col.delta": "vs previous scan",
    "guide.col.status": "Backup",
    "guide.col.missing": "Missing from backup",
    "guide.col.file": "File",
    "guide.col.share": "Share",
    "guide.evidence.cleanup": "Cleanup candidates - found in the backup account with the same names and sizes",
    "guide.evidence.where": "What takes the space - paths that grew most",
    "guide.evidence.no_backup": "Tasks without a confirmed backup",
    "guide.evidence.big_file": "Large files in this account (orange is the one on this line)",
    "guide.evidence.surge": "Where it grew - paths that grew most",
    "guide.full.situation": "The storage holding {account} is {pct}% full.",
    "guide.full.numbers": "Used {used} · free {free}",
    "guide.full.forecast": "At the current rate, FULL in: {forecast}",
    "guide.full.why_now": (
        "At 100% every write to this storage fails. Layouts being saved and running "
        "simulations break halfway, and other accounts on the same storage are "
        "blocked too. That is why it is at the top."
    ),
    "guide.full.why_soon": (
        "Not urgent yet, but in the warning range. Freeing space while there is time "
        "avoids rushed mistakes later."
    ),
    "guide.full.fix.step1": (
        "The {count} candidates below ({freeable} in total) are confirmed in the backup "
        "account with the same names and sizes. Ask the task owners, biggest first."
    ),
    "guide.full.fix.step2": "Before asking, compare the original and backup sizes with the check commands.",
    "guide.full.fix.step3": "After the cleanup, press Refresh on the home screen to see the new usage.",
    "guide.full.nofix.step1": (
        "Look at the paths that grew most below and ask their owners what can go. "
        "Account detail has the large files and growth paths."
    ),
    "guide.full.nofix.step2": (
        "The usual reason for no candidates is a missing backup link. Link a backup "
        "account in Account settings and the next scan will find candidates."
    ),
    "guide.full.nofix.step3": "If nothing can be freed now, ask the storage admin for more space or a quota change.",
    "guide.no_backup.situation": (
        "{count} tasks in {account} that reached the BACKUP stage ({size}) were not found "
        "in the linked backup account ({backup})."
    ),
    "guide.no_backup.why": (
        "These tasks exist in one copy only. If they are deleted by mistake or the storage "
        "fails, there is nothing to restore from. It is a loss risk, not a space problem."
    ),
    "guide.no_backup.step1": "'Missing from backup' lists folder names not found in the backup account. Check with whoever runs the backup.",
    "guide.no_backup.step2": "'partial' means the backup is under 95% of the original - the copy may have been cut off.",
    "guide.no_backup.step3": "Keep these tasks out of any cleanup until the backup is confirmed.",
    "guide.no_backup.step4": "Once backed up, this line disappears after the next nightly scan.",
    "guide.cleanup.situation": "{account} has {count} tasks with a confirmed backup. Cleaning them up frees {freeable}.",
    "guide.cleanup.why": (
        "The storage is not urgent, but backed-up tasks still hold their original space. "
        "Cleaning up early avoids rushing later."
    ),
    "guide.cleanup.step1": "These tasks have their BACKUP folders in the backup account with the same names and 95%+ of the size.",
    "guide.cleanup.step2": "Ask the task owners whether the originals can be removed.",
    "guide.cleanup.step3": "Before asking, compare the original and backup sizes with the check commands.",
    "guide.big_file.situation": "One file, {name}, is {size} - {pct}% of the whole account.",
    "guide.big_file.grew": "It grew {delta} since the previous scan.",
    "guide.big_file.new": "It was not on the previous scan's large-file list.",
    "guide.big_file.why": (
        "A single file this big is usually an accident - unfinished simulation output, a "
        "leftover core dump or log. Cleaning up one file makes a big difference."
    ),
    "guide.big_file.step1": "Use the check commands to see the owner and last modified time.",
    "guide.big_file.step2": "If it was modified just now, a running job may still be growing it - tell the owner.",
    "guide.big_file.step3": "If it is not needed, ask the owner to clean it up.",
    "guide.surge.situation": "{account} grew {delta} since the previous scan (now {total}).",
    "guide.surge.why": "Growth this fast overnight usually comes from one job. If it continues, FULL comes that much sooner.",
    "guide.surge.step1": "See below where it grew.",
    "guide.surge.step2": "If it is planned (a new task, a big simulation batch), that is fine.",
    "guide.surge.step3": "If not, ask the owner of that path what is piling up.",
    "guide.unscanned.situation": "Accounts with no finished detail scan: {names}",
    "guide.unscanned.why": (
        "Cleanup candidates, large files and surges only come from the nightly detail scan. "
        "An empty list for these accounts means 'unknown', not 'fine'."
    ),
    "guide.unscanned.step1": "Check the cron status on the Detail scan tab (red means it does not run at night).",
    "guide.unscanned.step2": "Large accounts may need more than one night - see progress on the By account tab.",
    "guide.unscanned.step3": "If urgent, use Run detail scan now.",
    "guide.coarse.situation": "{count} tasks were judged without seeing inside the BACKUP folder.",
    "guide.coarse.why": (
        "At scan depth 3 only the BACKUP folder itself is recorded, not the folders inside it. "
        "They were matched by run folder name and total size only, so 'confirmed' may read stronger than it is."
    ),
    "guide.coarse.step1": "Raise the scan depth to 4 in Account settings.",
    "guide.coarse.step2": "From the next nightly scan, folders inside BACKUP are matched one by one.",
    "guide.coarse.step3": "Until then, open the backup side yourself before asking for a cleanup.",
    "priority.heading": "What to do first",
    "priority.summary": "{count} things - {critical} need action now - {freeable} could be freed",
    "priority.nothing": "Nothing needs attention first right now.",
    "priority.unavailable": "Could not work out the priorities. The table below still works.",
    "priority.unscanned": "{count} accounts have no finished detail scan yet and were left out ({names})",
    "priority.coarse": "{count} tasks were judged without seeing inside BACKUP (raise the scan depth setting to 4 for a precise check)",
    "priority.item.full_shared_with_fix": (
        "{mount} · {accounts} accounts {pct}% - cleaning up frees {freeable} ({count} tasks with a confirmed backup)"
    ),
    "priority.item.full_shared": (
        "{mount} · {accounts} accounts {pct}% - nothing found yet that could be freed"
    ),
    "guide.col.account": "Account",
    "guide.col.tasks": "Cleanup candidates",
    "guide.evidence.shared": "Accounts on this storage - most to clean up first",
    "guide.full.shared": (
        "{count} accounts share {mount}, now {pct}% full: {names}"
    ),
    "guide.full.shared_how": (
        "`df` returns the same filesystem and size for these accounts, so they are treated as one place."
    ),
    "guide.full.why_shared": (
        "Cleaning up one account gives the others on the same storage room too."
    ),
    "guide.full.shared.step": (
        "Start with the account that has the most to clean up. Account detail opens its task list."
    ),
    "priority.item.full_with_fix": "{account} {pct}% - cleaning up frees {freeable} ({count} tasks with a confirmed backup)",
    "priority.item.full": "{account} {pct}% - nothing found yet that could be freed",
    "priority.item.no_backup": "{account}: {count} tasks with no confirmed backup ({size}) - nothing to restore from if they go",
    "priority.item.cleanup": "{account}: cleaning up frees {freeable} ({count} tasks with a confirmed backup)",
    "priority.item.big_file": "{account}: {name} alone is {size}, {pct}% of the account",
    "priority.item.surge": "{account}: grew {delta} since the last scan (now {total})",
    "tree.tab.list": "List",
    "tree.tab.map": "Map",
    "treemap.btn.up": "Up",
    "treemap.btn.home": "Top",
    "treemap.rest": "files here",
    "treemap.share": "{pct}% of the account",
    "treemap.empty": "Nothing to draw.",
    "treemap.hint": (
        "Area is size. Double-click a tile to go inside it. Grey tiles are "
        "computed, not real folders, so they cannot be opened. A red border "
        "marks something worth a look."
    ),
    "digest.run": "Last scan",
    "digest.run_detail": "{status} - {duration}",
    "digest.running": "running",
    "digest.running_detail": "results appear here when it finishes",
    "digest.never_run": "none has finished yet",
    "digest.status.completed": "ran to the end",
    "digest.status.paused": "stopped at morning",
    "digest.status.stopped": "stopped on request",
    "digest.status.error": "stopped on error",
    "digest.status.running": "running",
    "digest.duration_hm": "{hours}h {minutes}m",
    "digest.duration_m": "{minutes}m",
    "digest.delta": "Total growth",
    "digest.delta_detail": "{count} accounts compared with the last scan",
    "digest.no_compare": "no previous scan to compare",
    "digest.biggest": "Grew the most",
    "digest.biggest_detail": "{delta} - now {total}",
    "digest.findings": "Worth a look",
    "digest.findings_none": "nothing stands out right now",
    "digest.findings_detail": "{urgent} of them need attention",
    "digest.findings_heading": "Worth a look",
    "digest.findings_empty": "Nothing stands out. The other tabs have the detail.",
    "digest.findings_hint": "Click a line to open that account's tab.",
    "digest.item.failed": "{account}: {count} paths could not be measured - check permissions or errors",
    "digest.item.partial": "{account}: {count} paths were read only partly - the real size is larger",
    "digest.item.large_file": "{account}: {name} is {size} ({change})",
    "digest.item.growth": "{account}: grew {delta} since the last scan (now {total})",
    "load.title": "Server load history",
    "load.btn.open": "Server load",
    "load.btn.refresh": "Refresh",
    "load.range_label": "Range",
    "load.range_days": "last {days} days",
    "load.sort_label": "Sort jobs by",
    "load.sort.cpu": "CPU first",
    "load.sort.mem": "Memory first",
    "load.loading": "Loading...",
    "load.failed": "Could not load: {error}",
    "load.no_samples": (
        "No load samples yet. The collector (every 15 minutes) has to run at least "
        "once before anything is recorded, and judging by hour needs a few days."
    ),
    "load.summary": (
        "{samples} samples - other jobs used {idle} CPU while our scan was idle, "
        "{busy} while it ran - {best}"
    ),
    "load.best_hour": "quietest hour {hour} ({verdict})",
    "load.has_room": "has room",
    "load.no_room": "no room",
    "load.mixed_suffix": "  (our scan mixed in)",
    "load.ours_suffix": "  (ours)",
    "load.hours_heading": "By hour - CPU used by everything except us. Red rows are already crowded",
    "load.jobs_heading": "Jobs that used the server then",
    "load.jobs_note": (
        "The CPU average covers only the samples where the job made the top list. "
        "It drops out when idle, so it reads higher than a true daily average - do "
        "not read it as what the job normally uses. Click a header to sort."
    ),
    "load.mounts_heading": "NFS mounts",
    "load.mounts_note": (
        "Round trip is how long the filer took to answer; queue is how long the "
        "request waited on our side before it was even sent. A larger queue means "
        "the bottleneck is our RPC slots, not the filer - cutting parallelism there "
        "is exactly the wrong move."
    ),
    "load.queue_tip": "Queue is larger than round trip - the bottleneck is our RPC slots.",
    "load.col.hour": "Hour",
    "load.col.others_cpu": "Other jobs CPU",
    "load.col.load": "load",
    "load.col.waiting": "Jobs waiting on I/O",
    "load.col.samples": "Samples",
    "load.col.user": "User",
    "load.col.job": "Job",
    "load.col.cpu_peak": "CPU peak",
    "load.col.cpu_avg": "CPU avg",
    "load.col.mem_peak": "Memory peak",
    "load.col.mount": "Mount",
    "load.col.ops": "Requests",
    "load.col.rtt": "Round trip",
    "load.col.queue": "Queued",
    "tree.title": "Browse folders",
    "tree.btn.open": "Browse folders",
    "tree.btn.expand": "Expand one more level",
    "tree.col.path": "Folder",
    "tree.col.size": "Size",
    "tree.col.share": "Share of account",
    "tree.col.change": "vs last scan",
    "tree.caption": "{count} directories - {total} total - read from what the nightly scan already recorded, nothing is measured again now",
    "tree.loading": "Loading...",
    "tree.busy": "Already loading.",
    "tree.failed": "Could not load: {error}",
    "tree.no_account": "Pick an account to see its folders.",
    "tree.no_scan": "No detail scan has finished yet. One has to complete before the inside can be shown.",
    "tree.rest": "(files directly in this folder)",
    "tree.more": "({count} more folders)",
    "tree.file": "file: {name}",
    "tree.new": "not in the last scan",
    "tree.legend": (
        "Sizes include the folder itself. When the subfolders add up to less "
        "than their parent, the gap is the files sitting directly in it.\n"
        "Grey rows are computed, not real folders, so they cannot be opened. That "
        "is also why they carry no comparison - there is no guarantee the previous "
        "scan had the same subfolders, so the number would look right and be wrong."
    ),
    "common.yes": "Yes",
    "common.no": "No",
    "common.unknown_value": "Unknown",
    # -- Dashboard ---------------------------------------------
    "app.title": "Storage Manager VWP",
    "dashboard.df_caveat": (
        "* Usage reflects the whole filesystem containing the account path "
        "(df does not report per-account usage)."
    ),
    "dashboard.col.name": "Name",
    "dashboard.col.size": "Used / Total",
    "dashboard.tip.filesystem": "Filesystem: {value}",
    "dashboard.tip.mount": "Mount: {value}",
    "dashboard.tip.used": "Used: {value}",
    "dashboard.tip.total": "Total: {value}",
    "dashboard.tip.free": "Free: {value}",
    "dashboard.col.byte_pct": "Capacity used",
    "dashboard.col.collected_at": "Last collected",
    "dashboard.col.kind": "Kind",
    "dashboard.collect_error_short": "collection failed",
    "dashboard.filter": "Filter by name or path",
    "dashboard.list_filtered": "showing {shown} of {total}",
    "dashboard.list_title": "Accounts",
    "dashboard.list_hint": "Double-click a row for everything about that account",
    "dashboard.btn.collect_now": "Refresh",
    "dashboard.btn.collect_now_tooltip": (
        "Re-reads usage with df and updates the table. Finishes immediately and puts "
        "no load on the monitored filesystem (directory walks are the 'Detail scan' tab)."
    ),
    "dashboard.btn.accounts": "Accounts / Settings...",
    "dashboard.btn.diagnose": "Diagnostics...",
    "dashboard.btn.reports": "Reports...",
    "dashboard.btn.search": "Search...",
    "dashboard.no_accounts": "No accounts registered. Add one from 'Accounts / Settings'.",
    "dashboard.all_normal": "All accounts normal ({count} accounts)",
    "dashboard.warn_summary": "{count} account(s) at warning or worse - most urgent: {worst}",
    "dashboard.hero_detail": "Highest usage · {account}",
    "dashboard.stat.accounts": "Accounts",
    "dashboard.stat.storages": "Storages",
    "dashboard.stat.attention": "Warning+",
    "dashboard.stat.collected": "Last collected",
    "dashboard.not_collected": "Not collected yet",
    "dashboard.collect_failed": "Collection failed: {message}",
    "dashboard.collecting": "Collecting...",
    "dashboard.collected": "Collection done ({count} accounts)",
    "dashboard.collected_elsewhere": "Showing what was just collected elsewhere (cron)",
    "dashboard.collected_with_failures": "Collection done ({count} accounts, {failed} failed)",
    "dashboard.collect_error": "Collection error: {message}",
    # -- Collection freshness ----------------------------------
    "freshness.just_now": "just now",
    "freshness.minutes_ago": "{minutes} min ago",
    "freshness.hours_ago": "{hours} h ago",
    "freshness.days_ago": "{days} d ago",
    "freshness.never": "never collected",
    "freshness.stale_summary": "! Collection stopped for {count} account(s) (oldest: {age})",
    "freshness.gappy_summary": (
        "! {count} account(s) collected only {coverage}% of expected samples in the "
        "last {hours}h. cron may not be running, so data is only collected when the "
        "GUI is open (check: crontab -l)"
    ),
    # -- Menu --------------------------------------------------
    "tab.home": "Home",
    "tab.scan": "Detail scan",
    "menu.language": "Language",
    # -- Detail scan -------------------------------------------
    "scan.headline.running": "Running",
    "scan.headline.running_pct": "Running · {percent}%",
    "scan.headline.idle": "Idle · last run: {status}",
    "scan.headline.never": "Idle · never run",
    "scan.btn.run_now": "Run detail scan now",
    "scan.btn.run_now_tooltip": (
        "Runs immediately regardless of the 22:00-06:00 window. This can load "
        "the target filesystem, so use it carefully during business hours."
    ),
    "scan.btn.stop": "Safe stop",
    "scan.btn.stop_tooltip": (
        "Requests a stop for the running scan. This is not a forced kill - the "
        "scan stops at its next checkpoint and completed work is preserved."
    ),
    "scan.latest_started": "started {started_at}",
    "scan.pending_tasks": "{count:,} task(s) remaining",
    "scan.progress_counts": "{done:,}/{total:,} directories done ({percent}%)",
    "scan.progress_tip": (
        "When a directory times out it is split, which adds tasks and grows the total. "
        "Progress can therefore step back for a moment - that is expected."
    ),
    "scan.paths_under": "paths relative to {root}",
    "scan.status_error": "Cannot read scan status: {message}",
    "scan.account_label": "Account",
    "scan.col.path": "Path",
    "scan.col.current_size": "Current size",
    "scan.col.delta": "vs previous scan",
    "scan.col.delta_dated": "vs {previous}",
    "scan.nth": "scan #{n}",
    "scan.select_account": "Select an account to see its growth paths.",
    "scan.no_baseline": "{account}: no completed baseline yet (one full detail scan is required).",
    "scan.growth_caption": "{account}: {current} scan, compared path-by-path with the {previous} scan{notice}",
    "scan.baseline_only_caption": (
        "{account}: only the {current} scan exists so far "
        "(no previous scan to compare, deltas appear after the next scan){notice}"
    ),
    "scan.partial_warning": (
        "! {count} path(s) contain unreadable subdirectories, so their sizes are "
        "under-measured (insufficient permissions). Growth figures may be understated."
    ),
    "scan.cpu_usage": (
        "Last scan CPU: {avg}% avg / {peak}% peak (top scale, 1 core = 100%) "
        "- {system}% of the whole machine"
    ),
    "scan.memory_usage": "Memory peak {peak} ({percent}% of the machine)",
    "scan.failed_warning": (
        "! {count} path(s) could not be measured. Check the reasons below "
        "(if it is a permission problem, request read access or drop the path)."
    ),
    "scan.failed_more": "  ... and {count} more (see the weekly report for the full list)",
    "reports.scan_progress_heading": "[Detail scan progress]",
    # -- Detail scan tab: per-account status --------------------
    "scan.acct.heading": (
        "Select a row to pick that account (top right); double-click to open its growth paths."
    ),
    "scan.acct.name": "Account",
    "scan.acct.kind": "Kind",
    "scan.acct.progress": "Progress",
    "scan.acct.pending": "Pending",
    "scan.acct.measured": "Measured",
    "scan.acct.eta": "Est. remaining",
    "scan.acct.last_scan": "Last scan",
    "scan.acct.note": "Notes",
    "scan.acct.never": "never",
    "scan.acct.progress_tip": (
        "Directories done / total. The total can GROW while the scan runs - "
        "slow directories get split into more work. Progress moving backwards "
        "is not a bug."
    ),
    "scan.acct.measured_tip": (
        "How much this scan has actually measured so far. It keeps growing "
        "while the scan runs. This is not the account total - it is what has "
        "been counted up to now."
    ),
    "scan.acct.eta_value": "~{duration}",
    "scan.acct.eta_unknown": "measuring",
    "scan.acct.eta_tip": (
        "A rough figure: the median time this scan actually took per directory, "
        "multiplied by the number left. It is not a precomputed prediction.\n\n"
        "It grows if the remaining directories are heavier than the ones already "
        "done, or if splitting adds work. With too few samples it stays "
        "'measuring'."
    ),
    "scan.acct.note_failed": "{count} failed",
    "scan.acct.note_partial": "{count} partially read",
    "reports.large_heading": "[Files worth a look]",
    "reports.large_caveat": (
        "Listed only when a single file takes 5% or more of the account, "
        "grew 1.5x or more since the last scan, or was absent from the "
        "previous scan's top list. Absent does not mean new - it may have "
        "been smaller then."
    ),
    "large.heading": "{account}: {count} largest files  ·  orange ones stand out within the account",
    "large.col.path": "File",
    "large.col.size": "Size",
    "large.col.share": "Share of account",
    "large.col.change": "vs last scan",
    "large.none": "{account}: no file above 100MB yet.",
    "large.no_scan": "Pick an account to see its largest files.",
    "large.new": "not in the previous list",
    "large.reason.share": "takes {pct}% of the account on its own",
    "large.reason.grew": "{ratio}x the previous scan",
    "large.reason.new": "was not in the previous scan's top list",
    "large.tip": (
        "Collected while the walk stats files anyway - the biggest ones "
        "above 100MB.\n\n"
        "'Not in the previous list' does not mean the file is new. All we "
        "know is that it was absent from the previous scan's top list; it "
        "may simply have been smaller then."
    ),
    # -- Durations ---------------------------------------------
    "duration.under_minute": "under a minute",
    "duration.minutes": "{minutes} min",
    "duration.hours": "{hours} h",
    "duration.hours_minutes": "{hours} h {minutes} min",
    "duration.days_hours": "{days} d {hours} h",
    # -- New tasks (request-driven workflow) --------------------
    "reports.new_tasks_heading": "[New tasks]",
    "reports.new_tasks_none": "No new tasks since the previous scan.",
    "reports.new_tasks_no_project_accounts": (
        "No account is marked as a project account, so new tasks were not "
        "checked (set the account kind in Account management and it will "
        "appear from the next report)."
    ),
    "reports.new_tasks_count": "{count} new task(s)",
    "reports.new_tasks_basis": (
        "  Rule: a `*_run_*` directory the previous scan looked for and did not find, "
        "present in this scan"
    ),
    "reports.new_tasks_unverified": (
        "{count} place(s) the previous scan never opened are left undecided "
        "(permission errors, or split subtrees where the scan reached a different depth)."
    ),
    "reports.new_task_stages": "Stages: {stages}",
    "reports.new_tasks_truncated": "  ... showing at most {shown} per account",
    # -- Resource change during the scan ------------------------
    "reports.company_heading": "[What else the server was doing]",
    "reports.company_context": (
        "CPU used by everything except our scan: {cpu} average, {peak} peak - "
        "{blocked} other jobs waiting on I/O on average"
    ),
    "reports.company_col.user": "User",
    "reports.company_col.job": "Job",
    "reports.company_col.cpu_peak": "CPU peak",
    "reports.company_col.mem_peak": "Memory peak",
    "reports.company_alone": "No other job was seen using the server at that time.",
    "reports.company_mount": (
        "{mount} - {ops} ops, {rtt}ms round trip, {queue}ms queued"
    ),
    "reports.company_mount_note": (
        "  Queue larger than round trip means the bottleneck is our RPC slots,"
        " not the filer."
    ),
    "reports.resource_heading": "[Resource change during scan]",
    "reports.resource_run": "{started} · {trigger} · {duration} · {status}",
    "reports.resource_other_runs": "Other runs in the same period (they added load too):",
    "reports.trigger.cron": "automatic (cron)",
    "reports.trigger.gui": "started by hand from the window",
    "reports.trigger.terminal": "started by hand from a terminal",
    "reports.duration_minutes": "{minutes} min",
    "reports.duration_hours": "{hours}h {minutes}m",
    # -- Weekday vs weekend night load comparison ---------------
    "reports.night_heading": "[Night load - weekday vs weekend]",
    "reports.night_intro": (
        "Last {days} days. 'Delta' is the peak measured against that night's "
        "own pre-scan baseline, so nights with different ambient load are "
        "still comparable. A night is classified by the MORNING IT ENDS ON - "
        "Friday night counts as weekend, Sunday night does not."
    ),
    "reports.night_weekday": "Weekday",
    "reports.night_weekend": "Weekend",
    "reports.night_col.kind": "Kind",
    "reports.night_col.count": "Nights",
    "reports.night_col.parallel": "Parallel",
    "reports.night_col.load_delta": "load delta",
    "reports.night_col.iowait_delta": "iowait delta",
    "reports.night_col.load_peak": "load peak",
    "reports.night_col.iowait_peak": "iowait peak",
    "reports.night_col.duration": "Scan time",
    "reports.night_col.date": "Date",
    "reports.night_col.status": "Status",
    "reports.night_hours": "{hours} h",
    "reports.night_caveat": (
        "Figures are medians across nights, so one unusually heavy night does "
        "not skew them. A larger weekend delta is fine IF scan time dropped "
        "too - the same work finished sooner. If load rose and scan time did "
        "not fall, parallel is not paying off on this machine: lower "
        "weekend_parallel_accounts."
    ),
    "reports.night_detail_heading": "Per night",
    "reports.night_detail_more": "  ... and {count} more nights",
    "reports.resource_context": (
        "{samples} samples - {parallel} volume(s) scanned concurrently"
    ),
    "reports.resource_caveat": (
        "These numbers are what this server saw. The real cost of a detail scan is "
        "usually I/O on the file server, which this process cannot observe. "
        "Low CPU with raised iowait and load does not mean there was no load."
    ),
    "reports.resource_timeline_heading": "Timeline (elapsed since scan start)",
    "reports.resource_col.metric": "Metric",
    "reports.resource_col.before": "Before",
    "reports.resource_col.average": "Avg during",
    "reports.resource_col.peak": "Peak",
    "reports.resource_col.delta": "Delta",
    "reports.resource_col.elapsed": "Elapsed",
    "reports.resource_col.accounts": "Accounts",
    "reports.resource_metric.load_avg": "load(1m)",
    "reports.resource_metric.cpu_iowait": "iowait",
    "reports.resource_metric.cpu_busy": "CPU",
    "reports.resource_metric.memory_used": "Memory",
    "reports.resource_metric.scan_share": "Scan share",
    "reports.scan_compared_with": "Baseline: compared with the {previous} scan",
    "reports.scan_heading": "{account} ({run} scan)",
    "reports.scan_progress": (
        "Progress: {done:,}/{total:,} directories done ({percent}%) - {pending:,} pending"
    ),
    "reports.scan_last_path": "Last processed: {when} - {size}",
    "reports.scan_failed_header": "[!] {count} path(s) could not be measured:",
    "scan.close_while_running_title": "Detail scan running",
    "scan.close_while_running_body": (
        "A detail scan is running. Closing the window stops it too.\n\n"
        "Directories already finished are kept; only the one in progress is "
        "redone on the next scan.\n\nClose?"
    ),
    "scan.stopped_on_close": "Stopped {count} running task(s).",
    "scan.btn.progress": "View progress...",
    "scan.btn.progress_tooltip": (
        "Shows how far the selected account has been walked, path by path (read-only)."
    ),
    "scan.current_target": "Now: {account} - {path}",
    "scan.scanning_now": "Detail scan running - {path}",
    "progress.title": "Detail scan progress",
    "progress.btn.refresh": "Refresh",
    "progress.summary": (
        "{generation} scan - done {done} / pending {pending} / split {split} / "
        "failed {error} (total {total})"
    ),
    "progress.col.path": "Path",
    "progress.col.status": "Status",
    "progress.col.result": "Result",
    "progress.col.scanned_at": "Processed at",
    "progress.status.pending": "Pending",
    "progress.status.done": "Done",
    "progress.status.split": "Split",
    "progress.status.error": "Failed",
    "notify.urgent_prefix": "Act now",
    "scan.new_path": "New (absent in previous scan)",
    "scan.no_change": "No change",
    "scan.confirm_title": "Run detail scan",
    "scan.confirm_body": (
        "This runs the detail scan now, regardless of the nightly window.\n"
        "The scan walks the whole target filesystem and may add load.\n\n"
        "Continue?"
    ),
    "scan.already_running": "A detail scan is already running.",
    "scan.started": "Detail scan running...",
    "scan.auto_started": "Entered the night window - detail scan started.",
    "cron.ok": "cron: both the collector and the nightly scan are registered.",
    "dashboard.forecast_failed": "Could not compute the FULL forecast (samples are fine).",
    "cron.none": (
        "Nothing is registered in cron - close this window and neither "
        "collection nor the nightly scan will run. Run setup_cron.csh once."
    ),
    "cron.nightly_missing": (
        "The nightly scan is not in cron - with this window closed, nothing "
        "runs at night. Run setup_cron.csh, or turn on 'Nightly auto scan' "
        "in settings."
    ),
    "cron.collector_missing": (
        "The 15-minute collector is not in cron - usage history has gaps "
        "whenever this window is closed, which skews the forecast."
    ),
    "cron.nightly_by_gui": (
        "This window handles the nightly scan (not in cron). Close it and "
        "that night is skipped."
    ),
    "cron.unknown": "Could not check what is registered in cron.",
    "scan.stop_requested": "Stop requested. The scan will stop safely at its next checkpoint.",
    "scan.nothing_running": "No detail scan is running.",
    "scan.not_started": "Detail scan not started: {reason}",
    "scan.finished": "Detail scan finished (status: {status})",
    "scan.failed": "Detail scan error: {message}",
    "scan.window_open": "Nightly window open (about {minutes} min remaining)",
    "scan.window_closed": "Outside nightly window (starts {start:02d}:00, ends {end:02d}:00)",
    # -- Accounts dialog ---------------------------------------
    "accounts.title": "Accounts / Settings",
    "accounts.registered": "Registered accounts",
    "accounts.col.name": "Name",
    "accounts.col.path": "Path",
    "accounts.col.owner": "Added by",
    "accounts.col.added": "Added",
    "accounts.col.scanned": "Last scan",
    "accounts.col.kind": "Kind",
    "accounts.parallel_weekday": "Concurrent scans (weekday)",
    "accounts.parallel_weekend": "Concurrent scans (weekend)",
    "accounts.parallel_hint": (
        "How many separate volumes to scan at the same time. 1 keeps the "
        "current one-at-a-time behaviour. Accounts that live on the SAME "
        "volume always run one at a time no matter how high this is - "
        "measurements showed that hammering one volume from several "
        "threads does not go any faster. A weekend night is one that ENDS "
        "on a Saturday or Sunday morning - Friday night counts as "
        "weekend, Sunday night does not (everyone is back on Monday "
        "morning)."
    ),
    "accounts.suffix.accounts": "",
    "accounts.auto_scan": "Nightly auto scan (while this window is open)",
    "accounts.auto_scan_hint": (
        "While this window is open, the detail scan starts by itself once "
        "the night window opens (22:00 by default). It starts once per "
        "night and **stops when you close the window**.\n\n"
        "The cost: if nobody leaves the window open, that night is skipped "
        "entirely. For scans that run with nobody logged in, turn this off "
        "and register cron instead (setup_cron.csh).\n\n"
        "Leaving both on is safe - a lock keeps only one running."
    ),
    "accounts.engine": "Measured by",
    "accounts.engine.python": "Python walk (faster)",
    "accounts.engine.du": "Run du (the old way)",
    "accounts.engine_hint": (
        "How the detail scan measures sizes. The Python walk keeps several "
        "requests in flight so the NFS round trip stops being the limit - "
        "on the target machine the same account took 75-88s with du and "
        "36s with the walk, and the totals matched exactly.\n\n"
        "du runs as a separate process, so nice/ionice can lower its "
        "priority. The walk runs inside this program, where that prefix "
        "does not apply - put 'nice -n 10 ...' on the cron entry "
        "instead.\n\n"
        "If a night goes wrong, switch back to du here; nothing else needs "
        "to change."
    ),
    "accounts.col.backup_link": "Backup account",
    "accounts.kind": "Account kind",
    "accounts.kind_hint": (
        "New tasks (*_run_*) are only detected on project accounts. "
        "Backup accounts grow monotonically by design and must not be read "
        "with the same eye."
    ),
    "accounts.backup_link_none": "(not linked)",
    "accounts.backup_link_needs_backup_account": (
        "No backup account available. Register an account whose kind is "
        "'backup' first."
    ),
    "account.kind.unset": "Unset",
    "account.kind.project": "Project",
    "account.kind.backup": "Data backup",
    "accounts.advanced_settings": "Advanced settings",
    "accounts.name_placeholder": "Account name (e.g. project_a)",
    "accounts.path_placeholder": "Monitored path (e.g. /user/project_a)",
    "accounts.btn.browse": "Browse...",
    "accounts.btn.add": "Add account",
    "accounts.btn.remove": "Remove selected account",
    "accounts.browse_title": "Select monitored directory",
    "accounts.input_required_title": "Input required",
    "accounts.input_required_body": "Enter both an account name and a path.",
    "accounts.add_failed": "Could not add account",
    "accounts.remove_title": "Remove account",
    "accounts.remove_body": "Remove account '{name}' from the list? (collected history is kept)",
    "accounts.save_failed": "Save failed",
    "accounts.interval": "Collection interval",
    "accounts.cooldown": "Notification cooldown",
    "accounts.retention": "Sample retention",
    "accounts.language": "Display language",
    "accounts.notification_mode": "Notification mode",
    "accounts.notification_command": "Notification command (JSON array)",
    "accounts.notification_webhook": "Notification webhook URL",
    "accounts.quota_command": "Quota command (JSON array)",
    "accounts.suffix.minutes": " min",
    "accounts.suffix.days": " days",
    "accounts.none_selected_title": "No accounts",
    "accounts.none_selected_body": "Register an account first.",
    # -- Readability -------------------------------------------
    "readability.title": "Read permission check",
    "readability.all_ok": "All {checked} directories checked are readable.",
    "readability.all_ok_partial": (
        "All {checked} directories checked are readable (sampled subset only)."
    ),
    "readability.some_unreadable": (
        "{unreadable} of {checked} directories checked are not readable. "
        "Their contents are excluded from size measurement, so sizes will be understated."
    ),
    "readability.truncated_note": "(Large path - only a sample was checked.)",
    "readability.more": "... and {count} more",
    "readability.root_unreadable": "This path itself cannot be read.",
    "readability.register_anyway": (
        "Register anyway? df-based usage and alerts still work correctly; "
        "only detail-scan sizes should be read as a lower bound."
    ),
    # -- Notifications -----------------------------------------
    "notify.mode.outbox": "File outbox",
    "notify.mode.command": "Internal command (stdin)",
    "notify.mode.webhook": "Internal webhook",
    "notify.mode.disabled": "Disabled",
    "notify.message": "[{tier}] {account} ({path}) - capacity {byte_pct} / inode {inode_pct}",
    "notify.growth_message": (
        "[Path growth] {account} - {path} grew by {delta} since the last scan (now {current})"
    ),
    "notify.full_forecast_message": (
        "[FULL imminent] {filesystem} - expected to fill in about {hours}h "
        "(accounts: {accounts})"
    ),
    "notify.surge_message": (
        "[Account surge] {filesystem} - grew {delta} in the last {window}h "
        "(accounts: {accounts})"
    ),
    # -- Forecast display --------------------------------------
    "forecast.column": "Full ETA",
    "forecast.unavailable": "No estimate",
    "forecast.hours": "~{hours}h",
    "forecast.days": "~{days}d",
    "forecast.within_hour": "within 1h",
    "forecast.pair": "{short} / {long}",
    "forecast.tooltip": (
        "7-day trend: {short}\n30-day trend: {long}\n"
        "Slope over last {window}h: {slope}\n"
        "* Filesystem-wide usage; this is an estimate."
    ),
    "forecast.reason.insufficient_samples": "Not enough samples",
    "forecast.reason.not_growing": "Not trending up",
    "forecast.reason.too_far": "Beyond forecast range",
    # -- First run ---------------------------------------------
    "firstrun.title": "Getting started",
    "firstrun.heading": "Welcome to Storage Manager VWP",
    "firstrun.body": (
        "No accounts are registered yet. Review the diagnostics below, then "
        "register the account path you want to monitor.\n\n"
        "Collected data is stored separately from monitored accounts, at:\n{path}\n\n"
        "Monitored paths are never written to or deleted from."
    ),
    "firstrun.add_account": "Register an account",
    "firstrun.later": "Later",
    # -- Diagnostics -------------------------------------------
    "diagnostics.title": "Diagnostics",
    # -- Reports -----------------------------------------------
    "reports.title": "Reports",
    "reports.daily": "Daily report",
    "reports.weekly": "Weekly report",
    "reports.cleanup": "Cleanup candidates",
    "reports.generate": "Generate now",
    "reports.generated": "Report generated: {path}",
    "reports.none": "No reports generated yet.",
    # -- Search ------------------------------------------------
    "search.title": "Search (admin)",
    "search.pin_title": "Admin check",
    "search.pin_prompt": "Enter the admin PIN:",
    "search.pin_wrong": "Incorrect PIN.",
    "search.pin_caveat": (
        "* The PIN limits UI exposure only; it is not an OS permission "
        "boundary or encryption."
    ),
    "search.query_placeholder": "File / directory name",
    "search.btn.run": "Search",
    "search.mode.exact": "Exact match",
    "search.mode.prefix": "Prefix",
    "search.mode.contains": "Contains",
    "search.enable_indexing": "Enable search indexing for this account",
    "search.not_indexed": "Search indexing is off for this account.",
    "search.not_indexed_hint": (
        "Search indexing is off for this account. Turn on 'Enable search indexing' above "
        "to start (only names, extensions and paths are stored - never file contents)."
    ),
    "search.no_account": "No account to search. Register one from 'Accounts / Settings' first.",
    "search.empty_query": "Type a query, then press Enter or click Search.",
    "search.searching": "Searching...",
    "search.no_results": "Nothing matches '{query}' (out of {indexed:,} indexed entries). Try the 'contains' mode.",
    "search.index_empty": "The index is empty. Search again once indexing finishes.",
    "search.indexing_started": "Started indexing {account}. You will be notified when it finishes.",
    "search.indexing_in_progress": "Indexing is in progress. Search after it finishes (results may be partial now).",
    "search.index_done": "Indexing finished: {count:,} entries",
    "search.index_failed": "Indexing failed: {message}",
    "search.col.path": "Relative path",
    "search.col.kind": "Kind",
    "search.result_count": "{count} result(s) (showing up to {limit})",
    "search.db_size": "Search DB size on disk: {size}",
    "search.change_pin": "Change PIN...",
    "search.pin_default_warning": "Still using the default PIN. Changing it is recommended.",
    "pin.change_title": "Change admin PIN",
    "pin.current": "Current PIN",
    "pin.new": "New PIN",
    "pin.confirm": "Confirm new PIN",
    "pin.mismatch": "The new PIN entries do not match.",
    "pin.too_short": "The PIN must be at least {min_length} characters.",
    "pin.current_wrong": "The current PIN is incorrect.",
    "pin.changed": "PIN changed.",
}
