# Git repositories

Every session row in the sidebar carries a branch mark between its pin and its
agent notes. It says two things: the session's working directory is inside a
Git repository, or it is not; and, inside one, whether that repository is
holding work - uncommitted changes, or commits no remote has - which turns the
mark orange, the console's warn tone, with the counts in its label. It is the
heads-up that there is something to commit or push here, and hovering an
orange mark says why: the branch, the changes sorted by what `git` would do
with them, and the commits no remote has. Nothing opens from it yet.

## What is checked, and where

The answer belongs to the node that runs the session, because the directory
lives there. `puppy/session_git.py` looks for a repository the way `git`
itself does, without running it: from the working directory upward, a `.git`
directory holding a `HEAD` is a git dir, a `.git` file starting with `gitdir:`
is the gitfile of a linked worktree or submodule and counts when it names one,
and the walk stops at the filesystem root, at a mount boundary, or below a
`GIT_CEILING_DIRECTORIES` entry. A bare repository is not a work tree and does
not count. A directory that is missing or unreadable answers "could not be
checked" with the reason. No process runs for this part, so a directory whose
owner Git would call dubious still answers truthfully about what is on disk.

Inside a repository, the work state is the one thing `git` itself is asked, in
the same check:

- `changes` is the number of paths `git status --porcelain` lists - staged,
  unstaged and untracked alike, an untracked directory as one entry - for the
  whole work tree, whatever subdirectory the session sits in. Ignored files
  are not changes.
- `unpushed` is the number of commits on `HEAD` that no remote-tracking branch
  holds (`git rev-list --count HEAD --not --remotes`): a branch without an
  upstream is unpushed work like any other until some remote has its commits,
  and a commit pushed to any remote is pushed. It is `null` when the
  repository has no remote configured - there is nowhere to push to, so its
  commits are never a heads-up - and `0` on an unborn branch.
- The rundown behind those counts comes from the same `git status` call
  (`--porcelain=v2 --branch`, the stable format every `git` since 2.11
  writes): `branch` is the checked-out branch - an unborn
  one included - or `null` with `HEAD` detached, and `staged`, `unstaged`,
  `untracked` and `conflicts` sort the listed paths by what `git` would do
  with them. Each path is counted once: one staged and then edited again is
  staged, an unmerged path is a conflict whatever else it holds, so the four
  add up to `changes`.

Every `git` run is read-only and bounded: no prompt, no pager, no optional
index lock (`--no-optional-locks`, so an engine mid-turn is never blocked),
no file-monitor daemon left behind, and a 30-second limit that ends the whole
process group. A `git` that will not answer - not installed, timed out, or
refusing, as it does for a directory owned by another user - does not unmake
the repository: the record keeps `repo` true and carries the reason in
`error` instead of the counts, and the console shows the plain repository
mark with that reason in its label.

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
- Every finished prompt re-checks its session's directory: the turn may have
  edited, committed or pushed, so the runner asks the worker to look again
  the moment the engine's turn ends (`session_git.turn_finished`), whether
  it finished, failed or was stopped. A task's prompt does not: a task works
  in its own clone, which never changes the project's repository. The
  project's mark moves instead when a task's changes are applied to Main,
  and when its agent notes are written or removed through the console - both
  ask the same way (`session_git.request`). Compaction and undo touch no
  files and ask nothing.
- Directories no session uses any more are forgotten at the next pass.

A changed answer - the mark, a count, the branch or a kind - is published
with the session list, so every console sees the new mark and tooltip,
including consoles that did not ask.

## Wire contract

Both runtimes advertise the additive `session-git` capability and register the
refresh route in `register_execution_api`; a controller reaches a backend's
copy through the ordinary proxy. Every session payload carries the additive
`git` field:

| Value | Meaning |
| --- | --- |
| `null` | the node has not looked at this directory yet |
| `{"repo": true, "checked_at": <seconds>, "changes": <n>, "unpushed": <n> or null, "branch": "<name>" or null, "staged": <n>, "unstaged": <n>, "untracked": <n>, "conflicts": <n>}` | inside a Git work tree, holding that much uncommitted and unpushed work (`unpushed` is `null` with no remote, `branch` with `HEAD` detached; the four kinds add up to `changes`) |
| `{"repo": true, "checked_at": <seconds>, "changes": null, "unpushed": null, "error": "…"}` | inside a Git work tree whose state `git` would not read |
| `{"repo": false, "checked_at": <seconds>}` | not inside one |
| `{"repo": null, "checked_at": <seconds>, "error": "…"}` | could not be checked |

`POST /api/sessions/{sid}/git/refresh` answers `{"ok": true, "git": <record>}`
with a fresh record (404 for an unknown session). Concurrent refreshes of the
same directory share one inspection; a re-check asked for after a change (a
finished prompt, an apply) waits for an inspection already under way and
looks once more, so it never answers from a read that began before the
change. A record is published as changed when any of its fields moved - the
mark, a count, the branch or a kind - because each of them is something a
console draws. On the full runtime the refresh is left out of the snapshot
guard's mutation count - it changes nothing a backup could copy - while the
busy refusal during a backup or restore still applies.

The console draws the mark only for nodes that advertise the capability: a
repository at full strength like present agent notes, in the warn tone when
`changes` or `unpushed` is above zero, no repository as the same faint
outline, and an unanswered or unreadable directory faint with the reason in
its label. The label carries the counts ("Git repository · 3 uncommitted
changes · 1 unpushed commit", "… · nothing to commit · no remote", "… ·
nothing to commit or push"). An orange mark also carries a tooltip - the
console's own bubble, on hover - saying why in a few lines: one naming the
work and the branch holding it ("Uncommitted and unpushed work on main",
"Unpushed work on a detached HEAD"), then one for each cause that applies -
the changes with their kinds ("3 changes · 1 staged, 1 unstaged, 1
untracked", listing only the kinds with anything in them) and the commits
("1 unpushed commit"). A plain mark carries no tooltip; its label already
says all there is. A node from before the work state answers a repository
without `changes`/`unpushed`, which the console reads as a plain repository,
never a heads-up; one from before the rundown answers counts without the
branch or the kinds, and the tooltip says only what it knows ("Uncommitted
work" over "3 uncommitted changes"). It is a labelled image, not a button,
and a press on it is a press on the row.

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
