# Tasks within a session

**Enable tasks** in Main's session menu or its sidebar context menu controls
Tasks for that session, independently of **Show status bar**. Tasks start enabled;
turning them off hides the conversation strip. The setting follows the session
across consoles and restarts and is included in backups. Remove every task
conversation before disabling Tasks, including finished tasks whose tabs were
hidden. Hiding a tab alone does not close its conversation or stop its work.

**Show status bar** is likewise the session's setting: Main and every task
tab show or hide their head strips together. The row in a task's own menu
sets Main's setting, and the sidebar's context menu on the session is the
way back once the strips - and the menu buttons they carry - are hidden. A
task's session payload and list row report Main's value; a `show_meta` write
aimed at a task is refused (409), because only Main's row is ever read.

Open a session and select **+ Task** beside **Main**. The task box is the chat's
own prompt box: `@` offers Main's browsers, terminals, sessions and a spawn,
images and files can be pasted, dropped or attached with **+**, Enter starts the
task and Shift+Enter breaks the line. Attached files are copied into the task's
own storage as it starts, so they belong to the task like any message's
attachments. Optionally name the task; left unnamed, it takes its prompt's
first line, or - with Session titles configured and **Generate a title from
the task** left on - a title a model writes from the prompt, which replaces
that first line when it arrives (see [Session titles](session-titles.md)).
The engine initially follows Main, even
if another task tab is selected. Model, effort and permissions start with that
engine's saved defaults on the selected backend. Adjust any of them
before starting; these choices apply only to the new task. Switching engines
loads that engine's saved defaults, and effort choices follow the selected model.
Preparation, review, refresh and the checks before applying can be cancelled
through the shared progress dialog on backends advertising `operation-cancel-v1`.
Cancellation waits for copy/Git cleanup and never starts a task prompt. Once
applying or refreshing files or dispatching conflict resolution begins, that
step must finish; a running task has its own Stop control.
The task opens in its own inner tab; create another to work on a second
feature concurrently. Every device adds tasks beside Main when it receives the
session list, including tasks created while that device was away and tasks that
have already finished. Discovery keeps the selected conversation and appends
new tabs in creation order after any saved tab order.
Tasks inherit Main's Fast setting when keeping its engine,
plus bounded recent conversation excerpts. Each task
keeps its own conversation, draft, message queue, approvals and Stop control.
Sending a message targets the selected conversation. Each tab shows the
conversation's dot (spinning while it works) and the task's state; hiding a tab
with its close mark never stops the task. A tab stays visible until explicitly
hidden on that device or the task is removed; hiding is remembered across reloads
and does not hide it on other devices. Reopen hidden tabs from the **Tasks**
sheet, opened by the button at the right of the strip. Between that button and
**+** stand the selected task's own two verbs, shown only while a task is
selected, never for Main: the eye opens **Review changes** - the sheet the
task's menu row and the Tasks sheet open - and is greyed exactly when that menu
row is, since a task still running, queued, starting or held has nothing to
review yet; the bin removes the task through the same **Remove task** confirm
as the task's menu and the sheet's **Remove**, and is greyed while the task
cannot go yet - while it is running, waiting for an approval or queued - so a
task the node reports working again greys both verbs where they stand. Both
name the task in their hover, and the bin, the strip's one destructive verb,
turns red under the pointer like the menu's row; greyed, it takes no tone. The
sidebar lists the parent once; while Main is idle its activity slot reports
working tasks or an approval waiting for input. Task links and search results
open the corresponding inner conversation.

Drag task tabs to reorder them beside Main, using the workspace tabs' same drag
card and sliding animation. Main stays first. Reordering keeps the selected
conversation and saves the open-tab order in this browser across reloads;
reopened hidden tasks join the end. Tasks stay within their own session strip
and do not create workspace splits. Like the workspace tabs' strip, an
overflowing task strip scrolls sideways while the dragged tab is held near
either end or past it, over the strip's buttons, and a tab let go there lands
at the end it reached. Touch keeps the same horizontal swipe behavior as
workspace tabs.

The **Tasks** sheet lists every task with its state, latest answer and the
**Open**, **Review changes** and **Remove** actions, without scrolling Main's
chat. Review stays visible but disabled while a task or its queue is working and
becomes available as it finishes; a task's own menu offers the same review.
Remove is disabled exactly while the strip's bin would be greyed for that task:
a running or queued task must be stopped first.
**Review changes** shows the changed files and a coloured diff; **Apply to Main**
applies that task's delta to Main's working files. A review reads the task copy
through a private Git index, so it never stages files behind the engine's back.
This does not commit or deploy. It checks that the reviewed delta is still current
and refuses overlapping changes that cannot apply cleanly. Applied tasks can be
continued; the next review contains only changes since their last apply. Review
and apply again after resolving conflicts in the task or Main. Main does not
silently synchronize changes back into already-created task copies.

**Refresh from Main** sits immediately below **Review changes** in a task's
menu on backends advertising `session-task-refresh`. Use it after discussing a
task, before making its code changes, to continue on Main's latest files. It
captures the Main session's current local repository, whatever its branch or
subdirectory, including uncommitted changes, force-tracked ignored files and
nonignored untracked files. It does not fetch a remote branch. The task keeps
its directory, conversation, native engine session, settings, attachments and
draft. The task's current branch (or detached HEAD), index and review baseline
advance to the captured snapshot; subsequent reviews contain only new task edits.
Previously opened reviews must be refreshed before applying.

The task must have no changes since its review baseline and no staged edits,
including a staged edit hidden by restoring its working file. Running tasks,
queued or held work, unresolved Git conflicts and missing working copies are
refused. Main and other sessions using the source project must also be idle.
The command shares creation/apply's project guards and snapshot rules, including
the file/size limits and symlink/submodule restrictions. It retains ignored
local artifacts and refuses overlapping incoming paths instead of overwriting
them. An unchanged Main is a no-op. External editors remain the user's
responsibility, as with creation and apply.

A successful refresh appends a notice to the task's transcript. Its next ordinary
model turn receives a reminder that earlier file reads may be stale and that it
must re-read relevant files before editing, with a bounded list of changed paths.
The reminder survives restarts and failed turns until an ordinary turn succeeds;
maintenance actions such as compact do not consume it. Refresh does not start an
agent turn. Browser Back/Forward never repeat the refresh command; Back on its
delayed progress dialog uses the shared cancellation contract.

The review sheet's **Fold into Main after applying** checkbox is on for each
new review. When enabled, a successful apply (including **Mark as reviewed**)
keeps the condensed conversation in Main and removes the task and its private
working copy, using the same folding flow as **Remove task**. Its open tab and
the Tasks sheet close, and Main takes focus. A failed apply or a conflict
resolution follow-up leaves the task in place. If applying succeeds but folding
fails, the dialog reports that distinction and offers **Retry folding** without
applying the changes again.

The review sheet's **Resolve conflicts** switch is on for each new review.
When enabled, a conflicting **Apply to Main** sends one follow-up prompt to
that task's existing agent conversation. Puppy supplies snapshots of the old
baseline, the reviewed task, and Main's latest working files. The agent uses
the task's current engine settings, normal permissions and model quota to
reconcile both sets of changes inside the task copy. It may also inspect live
Main read-only when needed. Main is untouched by this
resolution attempt. Review the updated diff and apply again when the agent
finishes; the switch never approves unseen changes or starts an automatic
retry loop. Stop and approvals work as they do for other task turns. An engine
failure or an unresolved conflict may still need your input. Only a task set
to **Apply to Main when done** (below) is applied without that second review.

Applies to the same repository run one at a time, including when separate Main
sessions point to it. A later apply checks the files left by earlier applies;
if it conflicts, its resolution snapshot includes those changes. Main stays
available while agents resolve in their own copies. Another task or an external
editor can change Main again before the next apply, so another explicit review
and resolution may be necessary. Stale reviews, busy sessions, missing copies
and other operational failures do not trigger agent follow-ups.

The shared task guidance authorizes read-only inspection of Main when resolving
conflicts or source drift, including manual follow-ups such as “fix the drift.”
It supplies Main's current working-directory path on every turn, so the agent
does not need a separate user confirmation or a manually provided snapshot.
Engine permissions still apply. Supplied snapshots remain the preferred inputs;
the automatic resolution prompt also supplies Main's repository root and keeps
the pinned snapshot as the review baseline if live Main changes again. All edits,
generated files, Git writes and test runs stay in the task copy. Git inspection
of Main uses `--no-optional-locks` to avoid incidental index writes.

**Remove** asks whether to keep the task's conversation. The choice is one
checkbox that starts on: Puppy folds a condensed copy of the conversation into
Main's transcript as a single collapsible card at the point of removal, then
deletes the task and its working copy. The card carries the task's prompt,
outcome, engine, timestamps, the files its applies wrote into Main, files it
changed but never applied, and the exchange itself: every prompt and answer in
full, side questions with their answers, tool calls as one-line summaries,
errors and engine switches, without a size cap. Thinking, tool results and
turn results are not kept. A failed or stopped task folds the same way while
the box is on; turning it off removes the conversation permanently. The card is
searchable under Main and readable through the session tools; a folded task
cannot be reopened, continued or undone. Main's model is not told about folded
tasks unless **Model sees folded tasks** in Main's session menu is on, in which
case the newest folded tasks and their final answers are named at the start of
each of Main's turns. That setting is off by default, follows the session across
consoles and restarts, and is included in backups. Nodes without this version
keep the plain remove.

Main must use a local project directory on its executing node; linked remote
workspace mirrors are not supported for tasks yet. Each task uses an independent local Git clone in a Puppy-owned scratch workspace.
It starts with Main's working files, including uncommitted changes and nonignored
untracked files. Untracked ignored files/dependencies are omitted; root AGENTS.md and
CLAUDE.md are retained. Submodules and absolute or outside-project symlinks are
refused. Each source snapshot is bounded to 50,000 files / 512 MiB, reviews to
64 MiB, and each session to 64 tasks. Main and other Puppy sessions working in the
source directory must be idle during refresh and apply. Other isolated tasks can
continue running. External editors remain the user's responsibility; these
working copies are not an OS security sandbox.

Creation does not wait for an idle project when Git says its work tree has no
uncommitted changes. While a turn runs or a prompt waits in a queue anywhere in
the project, a fresh look at the repository (the Git mark's own check,
published like a focus refresh) decides: with nothing staged, unstaged or
untracked, the task starts from the last commit. Only this local state counts;
whether a remote holds the commit makes no difference. Git checks that commit
out into the clone itself, so a file the running turn is writing is never read;
only an ignored root AGENTS.md or CLAUDE.md is copied from Main, and the
checkout is held to the same file, size and symlink limits. Uncommitted changes
or a repository Git could not read keep the refusal, which names the cause. An
idle project is still copied from its working files, uncommitted changes
included, and no look is taken for it.

## Apply to Main when done

**Apply to Main when done** hands a task's last step to Puppy: once one of the
task's turns ends successfully, with nothing left in its queue, and Main is
idle, Puppy applies the task to Main the way the review sheet's **Apply to
Main** with **Fold into Main after applying** would, folds its conversation
into Main and closes it. It starts unchecked each time you open the **New task**
dialog. Turn it on there, in the task's own menu (right under **Refresh from
Main**), through Main's agent (`apply_when_done`), or from inside the task,
where the agent sets it for itself when you ask it to merge into Main once it
is done (**Apply when done** in the task's `@` menu inserts `@Apply when done`
for that). Backends advertise it as `session-task-auto-apply`.

How an apply goes:

1. The task's delta since its review baseline is computed as for a review. An
   empty delta - the task changed nothing, or its changes were applied
   already - is folded straight away, without an apply row.
2. When that patch still applies to Main's files as they stand, it is applied
   (`git apply`, never touching Main's index), the next baseline is the
   task's tree, and Main's transcript gets **Task changes applied
   automatically** with the file list the fold keeps.
3. When Main has moved under the task, Puppy snapshots Main's working files
   into the task's repository and lets Git make a real three-way merge
   (`git merge-tree --write-tree`, Git 2.40 or newer) of Main's newer changes
   with the task's, in that repository alone. A clean merge is applied as the
   difference between the snapshot and the merged tree - Main's own changes
   can never be undone by it - and the row says it was merged with Main's
   newer changes.
4. Where both sides changed the same lines, Puppy writes the merge into the
   task's copy as `git merge` would: merged files in place, conflicted files
   with standard markers labelled `refs/puppy/sync/task` and
   `refs/puppy/sync/main`, and their stages unmerged in the copy's own index.
   The review baseline moves to Main's snapshot, so the task's diff stays its
   own changes. Puppy then sends the task's agent one follow-up naming the
   conflicted files and how each conflicts, and asks it to resolve them, `git
   add` them, run its checks, and to switch Apply to Main when done off if it
   cannot resolve them safely. Main's transcript shows **Resolving conflicts
   with Main in task** with the round, and the task's transcript the merge.
5. When that turn ends successfully, the task is tried again from step 1. A
   copy that still has unmerged files, or added lines still carrying Puppy's
   own markers, is never applied: the next round asks for them again without
   merging again. Each round is one follow-up; after eight rounds Puppy turns
   the switch off and says so in Main (**Task not applied automatically**), as
   it does for a missing copy, a refused patch or a failed merge.

Several tasks set to apply when done settle one at a time per repository. Each
pass of the worker takes Main's finished tasks in the order they finished,
applies every one that fits or merges cleanly first, and only then starts
conflict rounds, each against Main as the others left it - so tasks touching
the same files cost the fewest rounds their overlaps allow, and a task whose
resolution meets newer changes from another simply gets another round.

Main is never written while any turn works in its project, and new turns there
wait while an apply runs. The one exception is Main's own agent waiting for a
task through the task tools' `wait`: its engine is blocked on that very call,
so Puppy may apply then, and the wait cannot return until the apply is over.
A task whose turn fails or is stopped waits for a successful one; held
messages wait for you. A task with the switch on shows an arrow turning into
Main (↳) on its tab and reads **Resolving** during a conflict round,
**Applying** once finished, or **Waiting** while Main is busy; the Tasks sheet
carries the same line. Switching it off leaves the task as it is for an
ordinary review.

## Task tools for agents

Every prompt turn of a session with Tasks on gets the task MCP bridge
(`puppy_tasks`, advertised as `session-task-agent`), and so does every task's
turn, with a different set of tools. Its editable policy is Settings → System
prompt → **Task guidance** (`system_prompt.tasks`).

In Main, the agent can list the session's tasks with each one's phase -
working, resolving conflicts with Main (round n of 8), waiting for an
approval, queued, held, finished with changes not yet applied, applied,
stopped or failed - and whether it applies to Main when done; read a task's
conversation a page at a time; see its changed files, its diff and whether
the changes would apply to Main as it stands; and, at the user's request,
create a task (`new_task`, engine, model and effort refused with the valid
values rather than replaced), send a task a message, stop it, refresh it from
Main, set it to apply when done, or remove it (folded by default; a task with
changes not applied to Main is refused unless the user wants them discarded).
`wait` blocks for at most 20 seconds per call until the named tasks are
finished, applied, or folded into Main and closed, and returns early for a
task that needs someone - an approval, held messages, a stopped turn, or a
task that will never fold because it is not set to apply when done. A task is
named by its number (`#12`), its exact name, or a mention; `all` names every
task. Main's own turn reads Main's files for `new_task` and `refresh` while it
waits on them.

In a task, the agent sees its own changes, whether they would apply to Main
and any conflicts still unresolved (`status`), merges Main's current files
into its copy as described in step 4 above (`sync_main` - from Main's last
commit when a turn works in Main and its work tree is clean), sets or clears
Apply to Main when done for itself, and lists, reads and waits for its
sibling tasks.

The console's `@` menu offers the matching mentions: in Main's chat, each
task as `@Task-NAME-12` (the name is only a label; the number is the task's
session id), **All tasks** (`@All tasks`), and **New task**, whose wizard -
the spawn wizard from its engine step on - inserts `@New task` (Main's engine
and saved defaults) or `@New task using <engine> [<model>] [at <effort>
effort]`; in a task's chat, **Apply when done** (`@Apply when done`).

## Storage

Task conversations, grouping and Git working copies are covered by the existing
full backup/restore. Copies live at `<data-dir>/workspaces/session-<random>/`
and survive service restarts and reboots. If a copy is missing, Puppy keeps the
transcript and refuses to silently recreate an empty task. Remove a task only
when its work is no longer needed; folding keeps its
conversation, never its working copy. Remove children before deleting their
parent. Applied changes remain in Main after removing a task.

## Persistence

Tasks are ordinary sessions grouped by exact-shape `session_task.<sid>` meta
records. The per-session Tasks toggle uses the `session_tasks_disabled.<sid>`
meta ledger: an exact `true` marker means off, and no entry means on.
**Model sees folded tasks** uses
the same pattern under `session_tasks_digest.<sid>`: an exact `true` marker
means on, and no entry means off. Startup and snapshot restore reject malformed
entries. A folded task is an ordinary `info` row of Main's transcript (subtype
`session_task_archive`). These records, preferences and transcript rows are
included in full backups.

**Apply to Main when done** is the optional `session_task_auto_apply.<sid>`
record, absent while off: exactly `{format: 1, armed_at, rounds, resolving}`
with a finite non-negative `armed_at`, `rounds` the conflict rounds already
started (0 to 8) and `resolving` true from a round's follow-up until the next
attempt. It must belong to an existing task; startup and snapshot restore
reject anything else rather than repairing it, and it is deleted with its
task. No earlier record exists, so nothing needs preparing. Why an apply is
waiting is held in memory only and published on the task payload's additive
`auto_apply` (`{armed_at, rounds, max_rounds, resolving, phase, note}`).

Tab order, selection and last-read result positions keep their exact
`puppy.sessionTasks.<backend-id>.<parent-id>` browser record. Explicitly hidden
tabs are a separate optional `puppy.sessionTasks.<backend-id>.<parent-id>.hidden`
record: an array of at most 64 unique positive task IDs, excluding Main's ID.
Both keys use the console's mount-path namespace and are included in the backup's
browser state. An absent hidden record means no tabs were explicitly hidden;
absence from the saved open-tab order alone does not hide a task. Malformed
hidden records are rejected by the console and backup validation without
replacing them. Older saved open-tab lists did not distinguish a hidden task
from one never discovered on that device, so previously hidden tabs can appear
once until explicitly closed again.

Each task copy also names its review baseline as the Git ref `refs/puppy/base`
so the engine's own history rewriting can never garbage-collect it.
Refresh updates that ref and the existing record's `base` together with the
task's files and Git starting point. Its reminder is an ordinary `info` event
with subtype `session_task_refresh` and Git name-status `files`; the existing
backup includes the event and the independent Git objects. No additional
persisted record shape or engine-specific state is used.
Conflict-resolution inputs are preserved at
`refs/puppy/resolve/<review-token>/{base,task,main}` inside that copy. The next
review uses the supplied Main snapshot as its baseline; preparation leaves the
task's working files and index intact for its agent to reconcile. These refs and
their independent Git objects are included in full backups. The switch is a
per-request choice and is not persisted.
A merge of Main into a task (a conflict round, or the task's own `sync_main`)
keeps its inputs at `refs/puppy/sync/{base,task,main}` inside that copy, moves
`refs/puppy/base` and the record's `base` to the Main snapshot, and appends an
`info` row of subtype `session_task_sync` (its text, Git name-status `files`
and the `conflicts` it left) to the task's transcript.
