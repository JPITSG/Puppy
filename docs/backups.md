# Backup and restore

Settings → **Backup & restore** can export an archive to your browser or save
compressed archives on the instance's machine, manually or on a daily schedule.
The controls use the same settings form and status rows as the other cards.

## Scheduled and saved backups

Scheduling starts **off**. Choose **Daily at**, an absolute **Save to directory**
path on the instance's machine, and **Copies to keep** (1–100, initially 7).
The initial time is 03:00 and the initial directory is `backups/` inside the
instance's data directory. Set **Scheduled** and press **Save**; the switch and
all three fields take effect together. The time uses the instance's local clock,
with the same 12/24-hour presentation as the other time settings.

**Run now** queues a backup using the saved directory and retention count. It
works while scheduling is off; save any edits first. A repeated request while
one is pending coalesces into that request. The instance must be running to make
backups. Busy sessions, queued messages, terminals and other snapshot blockers
make the backup wait for idle time. Nothing is stopped to make it run. If a daily
run is overdue after downtime, one backup runs when possible, followed by the
next local day's scheduled time. A failed attempt is logged; the next daily run
still takes place. Turning scheduling off withdraws a scheduled backup still
waiting for idle time, but does not cancel a queued Run now or an active backup.

The destination must be a writable absolute directory without symbolic links.
Missing directories are created. Destinations inside backup contents or staging
are refused to prevent recursive archives. Each saved file is a gzip-compressed
`.tar.gz`, with private file permissions. Saved copies use the same archive
validation and restore path as **Export backup**. They are not encrypted.

Rotation runs only after a new archive has been successfully published. It keeps
the configured number of newest copies across this feature's saved directories;
changing the directory does not forget previous copies. Lowering the count takes
effect after the next successful backup. Only catalogued files with matching
file identities and sizes can be deleted. Other files in the directory are left
alone. A rotation failure is a warning alongside the new backup, and older files
that could not be removed remain listed.

**Recent backups** shows the next scheduled run or current waiting/running state,
the last 20 outcomes, and any older copies still retained. **Download** links are
authenticated and can be used repeatedly. Missing or changed files are marked
**Unavailable**; copies removed by rotation are marked **Rotated**. The card
refreshes while visible, preserving unsaved settings and an unchanged history's
scroll and focus. Browser Back/Forward preserves the form and never runs a backup
or replays Save.

## Coverage and restore

All archives include this instance's settings, users, backend registrations,
local sessions and transcripts, uploads, notification history, spelling additions,
token ledger, managed scratch/task copies, TLS identities and saved workspace
conflict copies. The schedule, saved-file catalogue and outcome history travel
with the database. An in-flight backup request is omitted from the exported copy
so restoring it cannot replay a Run now.

Saved archive files themselves are excluded; there are no archives inside each
new archive. Restoring the catalogue does not restore those files or relocate the
configured directory. Copies that no longer match the files at their original
locations are unavailable to download. A saved `.tar.gz` can still be selected
through **Import backup** on another instance.

**Export backup** additionally includes the exporting browser's tabs and local
drafts. Scheduled backups and Run now have no browser-local state. Ordinary
project directories, remote backend data, managed-browser profiles and shared
sign-ins, and external engine credentials/native histories are excluded.

Import validates the entire archive and requires the instance to be idle. It
then replaces live state atomically, rolling back if installation fails. Export
preparation and import upload/validation can be cancelled; once restore starts
replacing live state, it finishes or rolls back. Existing limits apply: 512 MiB
compressed, 2 GiB extracted, 1 GiB per file and 50,000 archive entries. Only the
current database, configuration and archive shapes are accepted.

## Implementation

`puppy/backup_schedule.py` owns one daily worker in the full runtime, plus the
strict, versioned `backup_schedule` meta record. There is no new database table
or configuration migration. Startup and snapshot validation reject malformed
records. The headless backend does not run this controller-only worker.

Routes, all authenticated and uncached:

- `GET /api/snapshot/backups`: settings, current status, outcomes and saved copies.
- `PUT /api/snapshot/backups`: replace the four settings together.
- `POST /api/snapshot/backups/run`: enqueue one saved backup; returns 202.
- `GET /api/snapshot/saved/{id}`: download one retained file.

Manual export and the scheduler share the snapshot freeze and archive builder.
Publication records the completed temporary file before linking its final name,
so restart recovery can finish that publication without building another copy.
Downloads hold a verified open file descriptor; concurrent rotation cannot swap
the bytes being downloaded. Shutdown waits for current backup work to finish.

Archive protection has a single extension point, `ARCHIVE_WRITERS`, after
compression and before publication. Only `none` is accepted today, recorded as
the file's `protection` value. Future encryption can wrap this compressed stream
and add the corresponding validation, decryption and key handling. No encryption,
password controls or key storage are enabled now.

Checks: `python3 tests/backup_schedule_test.py`,
`python3 tests/snapshot_test.py`, `node tests/backup_schedule_ui_test.js`, and
`python3 tests/console_browser_test.py --backup-schedule-only`.
