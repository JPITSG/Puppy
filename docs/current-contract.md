# Current contract

Puppy supports the current installed state and API protocol 2. Existing databases,
config, backup archives, browser catalogs, and client storage must already match
their current shapes. Fresh installations initialize those shapes; startup and
restore never convert an outdated format.

## Installing this change

Update the controller and every backend together from the reviewed release.
Protocol 0/1 peers are rejected, including peers that omit or stringify their
protocol number. Capabilities still describe available services and engine
features, and HTTP polling still handles a disconnected state stream.

The protocol 1 signed updater rejects protocol 2 artifacts. This boundary requires
installing the new backend package through the node's normal local installation
procedure (or updating its checkout), then restarting it with the current
controller. Subsequent compatible releases retain the signed updater and its
health-checked rollback. The cleanup does not change live files or deploy services.

Before applying, verify each node's persisted state against the current contract.
If a value is outdated, stop the affected service and manually repair it first;
do not add a conversion path to startup. A node running the latest release can
still contain a value written before that release.

## Durable state

- `session_queue.<id>` records contain exactly `queue`, `held`, and `paused` lists.
  Prompts are nonempty strings; structured items contain exactly `kind`, `fields`,
  and their matching `key`. Engine switches contain all captured target choices,
  configuration rows name their validating engine, and tool rows name their
  engine and action. Paused indexes are unique, sorted, and point to queue prompts.
  Empty records are absent. Startup and backup validation reject invalid records
  without dropping work. Restart still parks valid queued work as held.
- `data/browser/shared/state.json` contains exactly `v`, `serial`, `cookies`, and
  `storage`. Version is integer 1; serial is a nonnegative integer. Cookie records
  contain canonical cookies and float `seen` timestamps; storage records contain
  string maps under `items` and float `ts` timestamps. Invalid files make shared
  storage unavailable and remain untouched until repaired. Normal expiry, merge,
  size limits, and retry after a failed save remain active.
- Browser catalogs accept `agent` and `user` origins. The retired `legacy`
  singleton origin is rejected. Closed-browser storage cleanup remains crash
  recovery, and closed IDs still retain their reuse protection.
- Tasks retain their durable relationships and isolated copies. The feature's
  detach-for-rollback command and rollback-only instructions are removed.
- `config.system_prompt` contains exactly `custom`, `remote_workspace`,
  `browser`, `terminal`, `vnc`, and `spawn`. A `config.json` written before the
  remote-screen policy must have `system_prompt.vnc` added by hand before that
  node starts - any string, or Puppy's `DEFAULT_VNC_SYSTEM_PROMPT`. Startup and
  backup validation reject the earlier shape instead of filling it in, and a
  backup archive exported before this change is refused for the same reason.
- `config.timers` contains exactly `cli_release_minutes`,
  `model_catalog_minutes`, `cli_status_minutes`, `git_check_minutes`,
  `remote_session_seconds`, `remote_engine_seconds` and
  `completion_sync_seconds`. A `config.json` written before the Git repository
  check must have `"git_check_minutes": 15` (1 to 10080) added to `timers` by
  hand before that node starts; startup and backup validation reject the
  six-field shape, and an archive exported before this change is refused.
  See [Git repositories](session-git.md).
- `config.titles` contains exactly `enabled`, `backend`, `engine`, `model`,
  `effort` and `prompt`. A `config.json` written before generated session
  titles must have the section added by hand before that node starts - every
  runtime, headless nodes included; startup and backup validation reject a
  config without it, and an archive exported before this change is refused.
  `session_title.<sid>` records are exact `{format: 1, state, text,
  placeholder, requested_at}` rows belonging to existing sessions. See
  [Session titles](session-titles.md).
- `token_usage.<YYYY-MM-DD>` records are exact `{format: 2, rows}` days of the
  token ledger: each row `[ref, at, session, source, engine, model, input,
  output, cache_read, cache_write, reasoning]`, ordered by time, ref and
  model, inside its UTC day. Startup and backup validation reject any other
  shape. Existing ledgers require manual preparation for format 2 before
  upgrade; older ledger formats in snapshot archives are also refused. A new
  node has no records. See [Token usage](token-usage.md).

## Runtime APIs and console

- Terminal and browser viewers always use identified instances. Anonymous
  `/api/ws/term` and singleton `/api/ws/browser` routes are removed. Terminal
  creation uses `command`; the anonymous route's `cmd` alias is rejected.
  Behind `terminal-engine-cli`, `engine` in its place runs that engine's own
  interactive CLI in the node's scratch home for it, and instances carry the
  additive `engine` field.
- Durable completion records drive notifications. Browser-reported completion
  `/api/notify/fire` and its deduplication cache are removed.
- Engine notices are additive session-socket messages: `engine_notice` carries
  `{engine, notice:{text,tone,resets_at?}}`, while `rate_limit` keeps native
  `info` and adds a readable `notice` or explicit null for a quiet sample.
  There is no durable-shape change. Older nodes' raw statuses get a general
  usage update in the console. See [Engine messages](engine-messages.md).
- Session payloads add `model_substitute`, either null or
  `{requested, served, baseline, note}`, for another model serving the request.
  This state is in memory and clears on backend restart; `model_switch` and
  `model_substituted` info events remain in the transcript. No protocol,
  database or snapshot shape changes are required. See
  [Model substitutions](engine-messages.md#model-substitutions).
- Session reordering requires both starting-order and pin-cohort compare tokens.
  `session-order-recency` adds durable `order_at` timestamps for merging the
  sidebar across backends below all pins, and an optional `expected_recency`
  compare token for activity that leaves the node's ID order unchanged.
- Remote spawned jobs always use controller-chosen IDs and ownership leases.
  Use `idle_timeout_s` and `max_runtime_s`; the old `timeout_s` field is rejected.
  With `timeout-settings`, omitted limits use the executing node's settings;
  zero removes a deadline and positive limits may exceed two hours. Older
  peers retain their advertised spawn contract. Ownership leases still apply.
- Upgrade readiness and backend availability come from their current owners;
  missing fields are not inferred from session lists or browser polls.
- The console requires shared drafts, upload IDs, engine defaults, complete
  system-prompt settings including the remote-screen policy, current queued
  choices, and task configuration, attachments, conflict resolution, and
  conversation folding. Search jumps use the forward event cursor. Session
  elapsed time requires server timing fields.
- `session-draft-presence` adds expiring, anonymous typing hints to the existing
  session socket and optional revision comparisons for shared-draft writes.
  Conflicts retain the local editor and its existing journal until reviewed;
  no draft, config, database or backup format changes are needed. A draft
  write's optional `caret` is relayed on the accepted frame, never stored, so
  consoles that are not editing can follow the writer's caret.
- Internal browser bridge aliases and the obsolete engines-response `user`
  alias are removed.
- Final turn completion refuses new steering and side questions, while
  already-sent steering may acknowledge for up to two seconds afterward.
  Native refusals and unconfirmed handoffs remain distinct in their error
  wording. Steering and side-question reply deadlines are independent;
  background pauses continue accepting side questions. This changes no stored
  format or wire capability.

This cleanup preserves transaction rollback, interrupted-operation recovery,
offline retry, historical transcript rendering, and external engine/Chromium
capability handling. Those remain part of normal operation.

A question the engine asks the person is the ordinary approval with two
additive fields: the claude driver marks an `AskUserQuestion` permission
request `kind: "question"` and carries `questions` (the rows `puppy/questions.py`
read from the tool's input, each with its `index`, `key` - the engine's own
question text - `question`, `header`, `description`, `kind`, `multi` and
`options`) and `question_title`; `approval_request` and the attach snapshot's
`pending_approval` carry them alike. The console's `approval_response` may
add `answers`, a map from row index to a label, a list of labels or the
person's text; the driver keys it by question text into the allow's
`updatedInput.answers`, an empty map is a deliberate skip, and a reply
without one is the plain allow. No persisted shape or capability changes.

Scratch promotion is additive `workspace-move`: both execution runtimes serve
`POST /api/sessions/{sid}/workspace/move` with `{destination: string}` and
return `{ok: true, session}`. See the [backend contract](../backend/README.md).
No persisted shape changes: the existing session changes from `temporary` to
`directory`; the transcript records a `workspace_move` info event.

Project moves are additive `project-move`: the same route takes an ordinary
`directory` session and answers `{ok: true, session, moved, retained}`. On such
nodes the body may add an `expected_cwd` string for either kind of session,
naming the folder the request was made about; a folder that has moved since is
refused with 409, and any other key is still a 400. `moved` holds the ids of
every session that followed the folder, `retained` the original's path when a
cross-filesystem copy could not remove it completely, otherwise empty. Every
directory session on the node working in the folder or below it follows in one
transaction with its native context cleared and a `workspace_move` info event.
A console shows **Move project** only for nodes that advertise this; the scratch
contract above is unchanged. No persisted shape changes.

The sidebar's Git mark is additive `session-git`: every session payload carries
`git` (`null` until the node has looked, then a `repo`/`checked_at` record, with
`error` when the directory could not be read; a repository also carries the
additive `changes` and `unpushed` counts - beside them the additive rundown
an orange mark's tooltip reads, `branch` (`null` with `HEAD` detached) and
the `staged`/`unstaged`/`untracked`/`conflicts` kinds that add up to
`changes` - or `null` for both counts with `error` and no rundown when `git`
would not read its state), both execution runtimes serve
`POST /api/sessions/{sid}/git/refresh`, and the node's `git_check_minutes` timer
rides the existing `timer-settings` payload. The node re-checks a directory
when a prompt finishes in it (never a task's), when a task's changes are
applied to it, and when its agent notes are written. A console reads a node
without the capability as before: no mark, and its six timers still editable;
a node from before the counts answers a repository without them, which is a
plain repository, never the warn tone, and one from before the rundown
answers counts alone, whose tooltip says only what it knows. Nothing is
persisted beyond the timer value; nothing changes a backup shape except that
timer, and no availability verdict moves.

The sheet behind a repository's mark is additive `session-git-detail`: both
execution runtimes serve `GET /api/sessions/{sid}/git`, one more inspection
of the directory that answers the fresh `git` record (the cache and the
published mark move with it), the work tree's `root`, and the `detail` behind
the counts - `head`, `upstream` with `ahead`/`behind`, `remotes`, the first
500 `paths` (`kind`, git's two-column `code`, `path`, a rename's `from`) and
the first 200 `commits` (`hash`, `subject`, `author`, `at`), with
`more_paths`/`more_commits` counting what the bounds cut; `root` and
`detail` are `null` outside a repository, `detail` alone when `git` refused.
A console makes a repository's mark a button that opens the sheet only for
nodes that advertise this; on every other node the mark stays the labelled
image it was. Nothing is persisted, nothing enters the cache or a payload
beyond the record, and no backup shape changes.

Generated session titles are additive `session-titles`: both execution
runtimes accept `auto_title` on session and task creation, carry the additive
`auto_title` record (`null`, or `{state, requested_at}` - never the message)
on every session payload, serve `GET`/`POST /api/sessions/{sid}/title` to
read a request's text and settle it with a `name` or an `error`, and serve
`POST /api/titles/jobs`, one spawn job of the chosen engine in an empty
private directory that the spawn job routes then poll and cancel. The
controller alone owns the settings (`GET`/`PUT /api/titles`, `POST
/api/titles/toggle`, the cancellable `POST /api/titles/test`), publishes their
public state on `/api/state` and the `titles` stream topic, consumes requests
from its own sessions and from every sessions payload a backend answers with,
and raises a failed title as a `toast` frame on the updates stream. A console
offers the creation dialogs' choice only for nodes that advertise this; a
node without it names a session from its first line as before. The `titles`
config section is a persisted-shape change (above); nothing else changes a
backup shape.

The sheet's History is additive `session-git-log`: both execution runtimes
serve `GET /api/sessions/{sid}/git/log?skip=N&limit=M`, one page of the
short log of everything on `HEAD` - `{ok, total, skip, commits: [{hash,
subject, author, at}], more}`, newest first, `limit` 1 to 100 (the default)
and `skip` from 0, anything else `400`, git's refusal `409` with its reason,
an unborn branch a `total` of 0 - read from the session's own directory. A
console draws the History list only for nodes that advertise this. Nothing
is persisted and the read is never counted as a mutation.

The sheet's actions are additive `session-git-actions`: both execution
runtimes serve `POST /api/sessions/{sid}/git/push` (the checked-out branch's
commits sent to its upstream, or to the one remote - `remote.pushDefault`
or the only one - with the upstream set by the push; never forced) and
`POST /api/sessions/{sid}/git/revert` (`reset --hard HEAD` then `clean -fd`
from the work tree's root: tracked paths back to the last commit, untracked
paths removed, ignored files and nested repositories kept, the session's own
directory put back if it went). The listing carries the additive
`detail.push_to` (`origin/main`, or `null` with `HEAD` detached, no remote,
or several remotes and nothing choosing) so a console knows whether to offer
Push. Both answer the read's fresh `git`/`root`/`detail` plus `pushed`
(`to`, `commits`) or `reverted` (`changes`), refuse with `409` for a task's
copy, a mirrored workspace, a repository `git` would not read, nothing to
do, no push target, and a turn running or queued anywhere in the project or
a task being prepared or applied to it (the same project lock as an apply,
which a prompt sent meanwhile waits for), and honour the
`operation-cancel-v1` header on a push. A console offers Push and Revert
only for nodes that advertise this. Nothing is persisted and no backup shape
changes; while either runs the node is busy like a task apply.

Host activity is additive `host-metrics-v1`: both execution runtimes serve
`GET /api/host/metrics`, returning that node's in-memory CPU history, its
cores/load/memory/uptime and the trimmed process tree below its own Puppy
process. The controller samples already-online backends every four seconds,
independently of viewers. Its `GET /api/backends/latency` returns cached readings
without probing: each measured row includes `measured_at` and `history` (up to
40 successful `[seconds, milliseconds]` pairs). Offline rows have empty history;
newly online nodes collect a fresh series. Removal, connection changes and
snapshot restore discard old observations, including in-flight results. Neither
surface adds persisted state, changes a backup shape, or moves an availability
verdict. Host metrics requests require the capability; latency uses the existing
ping route and is controller-only.

Token usage is additive `token-usage`: both execution runtimes serve
`GET /api/token-usage?since=&until=&step=&offset=`, the node's own ledger
summed per hour (`step=3600`) or per day at a fixed offset (`step=86400`),
per engine and model, in total, per session and per job source, with `turns`
counting turns alone. Every runtime records its own turns, spawned agents and
title jobs as they end. A console asks every node that advertises this and
names the rest; the read is never counted as a mutation, and only the
instance's own ledger is in its backup.

Cancellation is additive: `operation-cancel-v1` covers audited preparation
requests and authenticated operation controls; `engine-upgrade-cancel`,
`side-question-cancel` and `browser-navigation-stop` cover their native running
operations. See [the cancellation contract](cancellation.md). Existing persisted
shapes are unchanged; a commit boundary is never presented as rolled back.
