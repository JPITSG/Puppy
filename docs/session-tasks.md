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
Preparation, review and the checks before applying can be cancelled through
the shared progress dialog on backends advertising `operation-cancel-v1`.
Cancellation waits for copy/Git cleanup and never starts a task prompt. Once
applying files or dispatching conflict resolution begins, that step must finish;
a running task has its own Stop control.
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
selected, never for Main: the diff mark opens **Review changes** - the sheet the
task's menu row and the Tasks sheet open - and is greyed exactly when that menu
row is, since a task still running, queued, starting or held has nothing to
review yet; the bin removes the task through the same **Remove task** confirm
as the task's menu and the sheet's **Remove**, and is shown only while the task
can go - never while it is running, waiting for an approval or queued - so a
task the node reports working again greys Review and takes the bin away. Both
name the task in their hover, and the bin, the strip's one destructive verb,
turns red under the pointer like the menu's row. The sidebar lists the parent
once; while Main is idle its activity slot reports working tasks or an approval
waiting for input. Task links and search results open the corresponding inner
conversation.

Drag task tabs to reorder them beside Main, using the workspace tabs' same drag
card and sliding animation. Main stays first. Reordering keeps the selected
conversation and saves the open-tab order in this browser across reloads;
reopened hidden tasks join the end. Tasks stay within their own session strip
and do not create workspace splits. Touch keeps the same horizontal swipe
behavior as workspace tabs.

The **Tasks** sheet lists every task with its state, latest answer and the
**Open**, **Review changes** and **Remove** actions, without scrolling Main's
chat. Review stays visible but disabled while a task or its queue is working and
becomes available as it finishes; a task's own menu offers the same review.
Remove is disabled exactly while the strip's bin would be hidden for that task:
a running or queued task must be stopped first.
**Review changes** shows the changed files and a coloured diff; **Apply to Main**
applies that task's delta to Main's working files. A review reads the task copy
through a private Git index, so it never stages files behind the engine's back.
This does not commit or deploy. It checks that the reviewed delta is still current
and refuses overlapping changes that cannot apply cleanly. Applied tasks can be
continued; the next review contains only changes since their last apply. Review
and apply again after resolving conflicts in the task or Main. Main does not
silently synchronize changes back into already-created task copies.

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
failure or an unresolved conflict may still need your input.

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
untracked files. Ignored files/dependencies are omitted; root AGENTS.md and
CLAUDE.md are retained. Submodules and absolute or outside-project symlinks are
refused. Initial working files are bounded to 50,000 files / 512 MiB, reviews to
16 MiB, and each session to 64 tasks. Main and other Puppy sessions working in the
source directory must be idle during creation/apply. Existing isolated tasks can
continue running. External editors remain the user's responsibility; these
working copies are not an OS security sandbox.

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
Conflict-resolution inputs are preserved at
`refs/puppy/resolve/<review-token>/{base,task,main}` inside that copy. The next
review uses the supplied Main snapshot as its baseline; preparation leaves the
task's working files and index intact for its agent to reconcile. These refs and
their independent Git objects are included in full backups. The switch is a
per-request choice and is not persisted.
