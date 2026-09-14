# Git repositories

Every session row in the sidebar carries a branch mark between its pin and its
agent notes. It says one thing: the session's working directory is inside a
Git repository, or it is not. Nothing opens from it yet.

## What is checked, and where

The answer belongs to the node that runs the session, because the directory
lives there. `puppy/session_git.py` looks for a repository the way `git`
itself does, without running it: from the working directory upward, a `.git`
directory holding a `HEAD` is a git dir, a `.git` file starting with `gitdir:`
is the gitfile of a linked worktree or submodule and counts when it names one,
and the walk stops at the filesystem root, at a mount boundary, or below a
`GIT_CEILING_DIRECTORIES` entry. A bare repository is not a work tree and does
not count. A directory that is missing or unreadable answers "could not be
checked" with the reason. No process runs, so a directory whose owner Git
would call dubious still answers truthfully about what is on disk.

The answer is cached in memory, one record per working directory, never
persisted and outside every backup:

- A worker on each node re-checks every session's directory on the node's
  `git_check_minutes` timer (15 minutes by default, Settings → Timers).
- A directory no session payload has an answer for yet - a new session, a
  moved workspace, a fresh start - wakes the worker, so it is answered within
  moments rather than at the next scheduled pass.
- Bringing a session into focus re-checks it at once: the console posts
  `POST /api/sessions/{sid}/git/refresh` when a session other than the one
  focused before is selected in the sidebar, activated as a tab, focused as a
  pane, or restored by a reload. Re-selecting the current session sends
  nothing, and a backend the health worker calls unreachable is never probed.
- Directories no session uses any more are forgotten at the next pass.

A changed answer is published with the session list, so every console sees
the new mark, including consoles that did not ask.

## Wire contract

Both runtimes advertise the additive `session-git` capability and register the
refresh route in `register_execution_api`; a controller reaches a backend's
copy through the ordinary proxy. Every session payload carries the additive
`git` field:

| Value | Meaning |
| --- | --- |
| `null` | the node has not looked at this directory yet |
| `{"repo": true, "checked_at": <seconds>}` | inside a Git work tree |
| `{"repo": false, "checked_at": <seconds>}` | not inside one |
| `{"repo": null, "checked_at": <seconds>, "error": "…"}` | could not be checked |

`POST /api/sessions/{sid}/git/refresh` answers `{"ok": true, "git": <record>}`
with a fresh record (404 for an unknown session). Concurrent refreshes of the
same directory share one inspection. On the full runtime the refresh is left
out of the snapshot guard's mutation count - it changes nothing a backup could
copy - while the busy refusal during a backup or restore still applies.

The console draws the mark only for nodes that advertise the capability: a
repository at full strength like present agent notes, no repository as the
same faint outline, and an unanswered or unreadable directory faint with the
reason in its label. It is a labelled image, not a button, and a press on it
is a press on the row.

## The timer

`git_check_minutes` is one more key in the exact-shape `config.timers` map
(default 15, 1 to 10080 minutes), served and edited through the existing
`timer-settings` `GET`/`PATCH /api/timers` like the other backend timers, and
included in backup archives. Changing it wakes the sleeping worker so the new
interval applies at once. Settings → Timers shows it under Backend checks; a
backend from before this timer serves the other six and keeps them editable,
with the Git row disabled for it, and fleet propagation of the value skips
such backends.

## Existing installations and backups

Config and snapshot import require the complete current config shape. No
runtime migration, missing-field defaults, or previous-shape compatibility is
provided. Before applying this persisted-shape change to a running
installation, the operator must manually edit **each in-scope node's private
config.json**:

- Add `"git_check_minutes": 15` (or another whole number of minutes from 1 to
  10080) inside the existing `timers` object, preserving its other six fields.

A node started without it refuses to start (`config.timers is missing
git_check_minutes`), and a backup archive exported before this change is
refused without altering live data. New installations initialize the
complete shape directly. Take a fresh backup after updating the live
configuration.
