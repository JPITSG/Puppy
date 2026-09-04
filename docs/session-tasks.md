# Tasks within a session

**Enable tasks** in Main's session menu or its sidebar context menu controls
Tasks for that session, independently of **Show status bar**. Tasks start enabled;
turning them off hides the conversation strip. The setting follows the session
across consoles and restarts and is included in backups. Remove every task
conversation before disabling Tasks, including finished tasks whose tabs were
hidden. Hiding a tab alone does not close its conversation or stop its work.

Open a session and select **+ Task** beside **Main**. Enter a prompt and optionally
name it. The task opens in its own inner tab; create another to work on a second
feature concurrently. Tasks inherit Main's engine, model, effort, permissions and
Fast setting at creation, plus bounded recent conversation excerpts. Each task
keeps its own conversation, draft, message queue, approvals and Stop control.
Sending a message targets the selected conversation. Each tab shows the
conversation's dot (spinning while it works) and the task's state; hiding a tab
with its close mark never stops the task. Reopen hidden tabs from the **Tasks**
sheet, opened by the button beside **+** on the strip. The sidebar lists the parent
once; while Main is idle its activity slot reports working tasks or an approval
waiting for input. Task links and search results open the corresponding inner
conversation.

The **Tasks** sheet lists every task with its state, latest answer and the
**Open**, **Review changes** and **Remove** actions, without scrolling Main's
chat. Review stays visible but disabled while a task or its queue is working and
becomes available as it finishes; a task's own menu offers the same review.
**Review changes** shows the changed files and a coloured diff; **Apply to Main**
applies that task's delta to Main's working files. A review reads the task copy
through a private Git index, so it never stages files behind the engine's back.
This does not commit or deploy. It checks that the reviewed delta is still current
and refuses overlapping changes that cannot apply cleanly. Applied tasks can be
continued; the next review contains only changes since their last apply. Review
and apply again after resolving conflicts in the task or Main. Main does not
silently synchronize changes back into already-created task copies.

Main must use a local project directory on its executing node; linked remote
workspace mirrors are not supported for tasks yet. Each task uses an independent local Git clone in a Puppy-owned scratch workspace.
It starts with Main's working files, including uncommitted changes and nonignored
untracked files. Ignored files/dependencies are omitted; root AGENTS.md and
CLAUDE.md are retained. Submodules and absolute or outside-project symlinks are
refused. Initial working files are bounded to 50,000 files / 512 MiB, reviews to
16 MiB, and each session to 64 tasks. Main and other Puppy sessions working in the
source directory must be idle during creation/apply. Existing isolated tasks can
continue running. External editors remain the user's responsibility; these
working copies are not an OS security sandbox.

Task conversations, grouping and Git working copies are covered by the existing
full backup/restore. Scratch storage may be cleared by the host; if a copy is
missing, Puppy keeps the transcript and refuses to silently recreate an empty
task. Remove a task only when its work is no longer needed. Remove children before
deleting their parent. Applied changes remain in Main after removing a task.

## Rollback

The pre-feature code is retained on branch `rollback/before-session-tasks` at
`e1389ca` (v1.0.434). This feature is landed in one commit so it can be reverted
without resetting unrelated later commits. There are no changes to existing
SQL tables or persisted configuration settings. The per-session Tasks toggle
uses a separate optional `session_tasks_disabled.<sid>` meta ledger: an exact
`true` marker means off, and no entry means on. Startup and snapshot restore
reject malformed entries; existing task records and config shapes are unchanged.
Each task copy also names its review baseline as the Git ref `refs/puppy/base`
so the engine's own history rewriting can never garbage-collect it.

For a rollback **preserving work**:

1. Finish/stop active turns, save a full Puppy backup, and separately back up any
   ordinary project directories you want to protect (full Puppy backups do not
   include those directories). Keep that backup outside disposable workspaces.
2. Stop each affected Puppy node gracefully, using the normal idle-gated service
   procedure. Do this from an operator shell, not a turn hosted by the stopping
   process. Queued/held messages remain owned by their original sessions.
3. With the feature code still present, run on each affected node:
   `PUPPY_DATA=/path/to/node/data /usr/local/bin/python3 -m puppy.session_tasks --detach-for-rollback`.
   It refuses a live configured listener or running work. It writes a private,
   fsync'd archive at `data/rollback/session-tasks-<id>.json` (the grouping
   records plus the ids whose Tasks toggle was off), then removes only that
   grouping metadata and toggle ledger in one database transaction. Keep this archive with
   the backup; it is a manual recovery record, outside full-backup coverage.
4. Revert the feature commit with `git revert --no-commit <feature-commit>`, set
   `puppy/__init__.py` to the next patch version, and commit the rollback under
   the repository's normal version rule. Resolve any later-code conflicts before
   deploying. Do not reset the branch or restore an old database over newer work.
5. Deploy/start the reverted build. Former tasks appear as ordinary sessions
   named `Main name / Task name`. Conversations, queues, native session ids and
   independent Git working copies remain; project edits already applied remain.

The detach operation is deliberate offline maintenance, never an automatic
migration. Re-enabling grouping later requires explicitly restoring the archived
records after checking that their session ids still exist; startup does not infer
or recreate them. If there are no created tasks, a code revert alone is enough.
