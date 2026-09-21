# Git repositories

Every session row in the sidebar carries a branch mark between its pin and its
agent notes. It says two things: the session's working directory is inside a
Git repository, or it is not; and, inside one, whether that repository is
holding work - uncommitted changes, or commits no remote has - which turns the
mark orange, the console's warn tone, with the counts in its label. It is the
heads-up that there is something to commit or push here, and hovering an
orange mark says why: the branch, the changes sorted by what `git` would do
with them, and the commits no remote has. Clicking a repository's mark opens
its sheet, which lists exactly what those counts summarise - the paths and
the commits themselves - and acts on them: Push for the commits, Revert for
the changes.

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

## The sheet

Nodes advertising the additive `session-git-detail` capability also serve
`GET /api/sessions/{sid}/git`, the read behind the sheet a repository's mark
opens. It is one more inspection of the same directory - the same discovery,
the same `git status` and `rev-list` - that this time keeps what the record
only counts, and it answers

```json
{"ok": true, "git": <record>, "root": "<work tree>" | null, "detail": <listing> | null}
```

where `git` is the fresh record exactly as the refresh would answer it (and
the cache and every console's mark move with it, published when it changed),
`root` is the work tree the discovery stopped at - the directory holding
`.git`, resolved, which the listed paths are relative to whatever
subdirectory the session sits in - and `detail`, present when `git` read the
repository, is:

| Field | Meaning |
| --- | --- |
| `head` | the commit `HEAD` is at, `null` on an unborn branch |
| `upstream` | the branch's remote-tracking upstream (`origin/main`), `null` without one |
| `ahead`, `behind` | how far `HEAD` is from that upstream, `null` without one |
| `remotes` | the configured remotes, in `git remote` order |
| `paths` | the paths `git status` lists, in its order: `{"kind", "code", "path"}` plus `"from"` for a rename or copy - `kind` one of `staged`, `unstaged`, `untracked`, `conflicts` (empty for an entry kind Puppy does not know, still a listed path), `code` git's own two-column porcelain code (`M.`, `.M`, `A.`, `RM`, `??`, `UU`, …), and `path` exactly as git holds it, never C-quoted (`-z`) |
| `commits` | the commits on `HEAD` no remote-tracking branch holds, newest first: `{"hash", "subject", "author", "at"}` - the abbreviated hash git chooses for the repository, the subject, the author's name and the commit time in seconds |
| `more_paths`, `more_commits` | what the bounds cut: at most 500 paths and 200 commits are listed, and these count the rest |

Outside a repository `root` and `detail` are `null`; when `git` refuses,
`root` is still named and `detail` is `null`, the reason riding in the
record's `error` as ever. Concurrent reads of one directory share one
inspection, and a plain refresh that meets a listing inspection joins it -
a listing serves a plain check - while a read that meets a plain inspection
waits for it and looks once more. The read is a `GET`, so it never counts as
a mutation a backup would wait for; a restore refuses it like every other
read. The paths never enter the cache or a payload: the record the cache
keeps is exactly what the refresh answers.

The console opens the sheet from a repository's mark, which is a button
(role `button`, focusable, Enter or Space) only where the node advertises
`session-git-detail`; every other mark - no repository, not looked at yet,
could not be checked - stays a labelled image, and a press on it is a press
on the row. The sheet names the session and its directory under its title,
as the agent-notes editor does and at that editor's steps, then stands in the
review sheet's voice, its facts on that sheet's column and at its steps - the
work tree's root when the session sits below it, the branch (`main`, `main · no commits yet`, `HEAD
detached at 3f9c2a1`), the upstream with `up to date`, `1 ahead`, `2 behind`
or `1 ahead, 2 behind` (`not set` with remotes but no upstream, `no remote`
without any), the State in the mark's own tone (`Uncommitted and unpushed
work`, `Uncommitted work` or `Unpushed work` in the warn tone; `Nothing to
commit or push` or `Nothing to commit · no remote` in the ok tone; `Could not
be read · <reason>` in the bad tone), and when the node looked - then two
captioned lists on the review sheet's surface. **Changes** carries the count
and the kinds after a separator (`Changes · 3 · 1 staged, 1 unstaged, 1
untracked`, or `Changes · 0`)
and groups the paths as `git status` groups them - Staged, Unstaged,
Untracked, Conflicts - each path with the verb git would use for it
(`modified`, `new file`, `deleted`, `renamed` with `old → new`, `copied`,
`type changed`; a staged path edited again as `modified · edited since
staging`; a conflict as `both modified`, `deleted by them`, …; an untracked
path with none), the columns of one group aligned like git aligns its own.
**Unpushed commits** carries the count and which remote lacks them (`· 1 ·
not on origin`, `· 2 · not on any remote`, `· 0`, `· no remote`) and lists each
commit as its hash, subject and, in the help colour, author and time. A
clean repository says `Nothing to commit` and `Nothing to push`; one without
a remote, `No remote to push to`; what the bounds cut ends a list as `… and
40 more paths`. **History**, on nodes advertising `session-git-log`, is the
short log of everything on `HEAD`: its caption carries the total, each line
is one commit's abbreviated hash (in the help colour) and subject and never
wraps - the box scrolls sideways for a long subject - and it is read a page
of 100 at a time through `GET /api/sessions/{sid}/git/log?skip=N&limit=100`
(`{"ok": true, "total", "skip", "commits": [{"hash", "subject", "author",
"at"}, …], "more"}`; `limit` is 1 to 100, `skip` any count from 0, anything
else a `400`; git's refusal - no repository, a directory that is not there -
a `409` with its reason; an unborn branch a total of 0). The first page is
asked for once the sheet's read has answered, never before it; the next
whenever the list is scrolled to within 40px of its foot or the foot's
**Load N more** is pressed, one page at a time; a page that fails keeps
what was listed with `Could not read the history · <reason>` and **Try
again** at the foot; `No commits yet` is the foot of an empty log; a fresh
read (opening, Refresh, an action's answer) starts the history over and a
page from before it is dropped. The sheet reads on opening and on **Refresh** (one press, one
read, the button held while it runs), keeps the facts and captions from the
row's record until the read answers, hands the fresh record to the row so the
mark never disagrees with the sheet, keeps the last listing and reports
inline when a read fails, and is a dialog like the agent-notes editor: Back
closes it, Forward opens a fresh one for the session as it is then.

## The actions

Nodes advertising the additive `session-git-actions` capability also serve
the two writes behind the sheet's buttons, under `/api/sessions/{sid}/git/`:

- `POST …/push` sends the checked-out branch's commits where a push would
  go. That target is the branch's upstream (`branch.<name>.remote` and
  `.merge`) when it has one; otherwise `remote.pushDefault` if set, or the
  only remote there is, and the push sets the upstream as it goes
  (`--set-upstream`). With `HEAD` detached, without a remote, or with several
  remotes and nothing saying which, there is no target. The listing names it
  as the additive `detail.push_to` (`origin/main`, `null` when there is
  none), which is how the sheet knows whether to offer Push. The push is an
  explicit refspec (`git push origin main:refs/heads/main`), never forced;
  hooks run as they would from a terminal, and whatever credentials `git`
  finds non-interactively are the ones used - there is no terminal to prompt
  on and `GIT_TERMINAL_PROMPT` is off, so a push that would need one fails
  with `git`'s reason. A rejected push answers that reason too, taken from
  the rejected ref's own line (`[rejected] main -> main (fetch first)`).
- `POST …/revert` discards every uncommitted change in the work tree, from
  its root whatever subdirectory the session sits in: `git reset --hard HEAD`
  (a merge stopped on a conflict is abandoned with it; on an unborn branch,
  which has no `HEAD`, the index is emptied instead) followed by `git clean
  -fd` - untracked paths removed, ignored files kept because they were never
  part of the work, and a nested repository left alone as `git clean` leaves
  it. The session's own directory is put back if it went with them (an
  untracked directory the session was created in): empty, it is no change
  to `git`, and the session keeps a place to work.

Both answer exactly what the read answers - the fresh `git` record (the
cache and every console's mark move with it), `root` and `detail` - plus
`pushed` (`{"to": "origin/main", "commits": 2}`) or `reverted`
(`{"changes": 3}`) counting what the action took off the record (a nested
repository a revert leaves alone stays counted). Every refusal is a `409`
with its reason: a task's copy (reviewed and applied from Main, never pushed
or reverted), a session mirroring another node's project (acted on there),
Puppy draining, no repository or one `git` would not read, `Nothing to push`
/ `Nothing to revert`, a detached `HEAD` or no target for a push, and -
because both run under the project lock a task apply takes, keyed by the
work tree's real root - a turn running or queued in any session inside that
project (`Main or another session is using this project; try again when it
is idle`) or a task being prepared or applied to it. A prompt sent to a
session in the project while an action runs waits for it, as it waits for an
apply. A push honours the `operation-cancel-v1` header: cancelling ends
`git`'s process group while it still runs, and once it has succeeded the
operation commits so a late cancel cannot misreport it. Ownership outlives a
caller that disconnects, so a closed tab never abandons a push halfway;
while either runs the node counts as busy for backups and upgrades like a
task apply, and both count as mutations a backup waits for. Each command is
bounded by `ACTION_TIMEOUT` (300 seconds) rather than the reads' 30.

In the sheet, Push - the primary - stands beside Close and Refresh at the
end of the button row exactly while `unpushed` is above zero and the read
named a `push_to`; Revert stands on the row's other side, in the danger
tone, exactly while `changes` is above zero (on a phone the four make two
lines of two: Revert and Close, then Refresh and Push). Push is one press one
request; Revert asks first, with a destructive confirm ("Revert 3 changes?"
over the work tree's path, saying which kinds go, that untracked paths are
deleted and ignored files kept, that a merge is abandoned when a conflict is
among them, and that it cannot be undone; Cancel has first focus). The
answer lands like a read's - the facts, the captions, the lists and the
buttons move together, the row's mark with them - with a toast for what was
done ("Pushed 2 commits to origin/main", "Reverted 3 changes"); a refusal or
a failure stays inline in the sheet; a push cancelled from the operation
dialog is followed by a fresh read, because what it managed is unknown. The
row is held while any request runs, and focus returns to the pressed button
afterwards, or to Close when the press removed its own button. A node
without the capability shows Close and Refresh alone.

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
work" over "3 uncommitted changes"). A repository's mark is a button that
opens the sheet where the node serves its read (see above); the other marks
are labelled images, and a press on one of those is a press on the row.

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
