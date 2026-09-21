# Puppy headless backend

Nodes advertising `engine-defaults` offer authenticated GET/PUT
`/api/engines/{key}/defaults` and publish `session_defaults`/`factory_defaults` in
each engine payload. New sessions, tasks and requested engine switches use those
node-owned choices; existing sessions and queued switches retain theirs. See
[Engine defaults](../docs/engine-defaults.md) for the exact API and the manual
config-shape update required before starting this version on an existing node.

This directory contains the separately deployable, API-only Puppy runtime. It
shares the console's database, runner and engine drivers at build time, but the
resulting artifact exposes no web GUI, cookie login, settings, or backend proxy.

VNC viewer sockets include an additive `throughput` message about once a second
for active viewers. Its `bytes_per_second` is incoming encoded RFB traffic at
this backend, excluding TCP/IP overhead and the viewer WebSocket stream. See
[VNC viewer protocol](../docs/vnc.md#viewer-protocol).

Nodes advertising `session-tasks-toggle` accept a boolean `tasks_enabled` as a
standalone `PATCH /api/sessions/{sid}` update and publish it on session payloads.
Tasks default to enabled. Disabling requires a Main session with no child task
conversations (including finished or hidden ones), and is serialized with task
creation. Disabled sessions reject new tasks until re-enabled. This preference
is node-owned, survives restarts and travels in full-WebUI backups. Deleting a
session or resetting its scratch workspace is refused with 409 only while that
session's own tasks exist or a task copy, review or apply is using its files.
The status bar (`show_meta`, a boolean `PATCH /api/sessions/{sid}` field) is
likewise the session's: a task's session payload and list row report its Main
session's value, and a `show_meta` write aimed at a task is refused with 409.

Nodes advertising `workspace-move` accept authenticated
`POST /api/sessions/{sid}/workspace/move` with `{"destination":"/absolute/new/folder"}`.
The destination must not exist, its parent must exist, and it must be outside
Puppy’s data directory. Only idle scratch sessions with no queued/held work or
child tasks can move; task copies cannot move. Files (including Git and symlinks)
are copied before the session becomes a directory session and the scratch copy
is reclaimed. The transcript is kept, native context is cleared for a handoff
on the next turn, and `session_meta` plus session-list updates publish the new
path. Moves share task-operation guards and shutdown draining. Permanent
project files are outside snapshot coverage and survive session deletion.

Nodes advertising `session-task-fold` accept
`POST /api/sessions/{sid}/tasks/{tid}/remove` with `{"fold": true|false}`
(default true). With `fold` on, the node appends one `info` event of subtype
`session_task_archive` to the Main session before deleting the task: `name`,
`prompt`, `engine`/`model`/`effort`, `outcome` and last `state`, timestamps,
`applied_files`/`unapplied_files` (git name-status text), `turns`, the final
`summary`, and uncapped `entries` (`user`, `assistant`, `tool`, `aside`,
`aside_answer`, `error` and `switch` rows with timestamps; thinking, tool results
and turn results are omitted). The reply carries `folded`, the archive's `seq`
and `workspace_removed`; a retry after a failed deletion finds the existing
archive instead of writing another. Applied rows (`info`/`session_task`) also
carry their `files` list. The same nodes accept a standalone boolean
`tasks_digest` `PATCH /api/sessions/{sid}` (published on session payloads,
default false) controlling whether Main's turns are told about its folded tasks.
Refusals are 409; the snapshot guard answers 503.

Nodes advertising `session-task-config` accept optional `engine`, `model`,
`effort` and `permission_mode` fields on `POST /api/sessions/{sid}/tasks`.
Omitting the engine selects Main's engine. Omitted model, effort and permissions
use the selected engine's saved defaults on this node. Fast follows Main when
keeping its engine and resets when changing engines. Explicit empty model/effort
values mean engine default. All choices are validated against that node's selected
engine and model before allocating a task copy. The first turn uses those
choices without editing Main.

Nodes advertising `session-task-attachments` accept attachment marker lines in a
task `prompt` that name files staged under Main (`data/uploads/<main sid>/…`,
as the console's prompt box writes them). Before the first turn the node copies
each such file into the new task's own private upload storage under the same
upload id, rewrites those marker paths to the copies, and then discards Main's
copies that nothing of Main's (draft, queue, transcript) still names. A retry
carrying the same `request_id` may still name Main's original paths. A staged
file that is missing or fails validation refuses the task with 409 and leaves
Main untouched.

## Current controller contract

Only Puppy API protocol 2 peers are accepted. All instances in a deployment must
speak this contract; protocol 0/1 inference and retired routes are removed.
[Current contract](../docs/current-contract.md) lists the required formats and
the coordinated installation boundary for this change.

## Build

```bash
python3 backend/build.py
backend/dist/puppy-backend.pyz --version
```

The build emits `puppy-backend.pyz`, the stdlib-only
`puppy-backend-launcher.py`, and a copy of `LICENSE`. The zipapp also carries the
license and all Puppy Python code required at runtime; the stable launcher owns
restart health checks and rollback. The target still needs Linux, Python 3.9+
with SQLite FTS5 support, `aiohttp`, and whichever official engine CLIs it will
run. Generating a self-signed TLS identity also needs the `openssl` command;
imported certificate/key pairs do not. No frontend assets are included.

Run from the source tree without building:

```bash
python3 -m backend.puppy_backend serve --data-dir backend/data
```

## Deploy

Copy the generated zipapp, launcher, and `LICENSE` to the remote machine, install
`aiohttp` (or use `requirements.txt`), and run the engine login commands as the
same unprivileged Unix user that will run the service. Initialize the private
configuration once.
The address below is reserved for documentation; replace it with an address
actually assigned to the backend host.

```bash
./puppy-backend.pyz pairing \
  --name my-build-node \
  --data-dir ./data \
  --bind 192.0.2.10 \
  --port 10888 \
  --advertise-url https://192.0.2.10:10888 \
  --auto-tls \
  --default-cwd /srv/projects \
  --usage-refresh-minutes 15 \
  --max-upload-size-mb 8 \
  --enable-remote-upgrade
```

This generates and prints a pasteable pairing block containing an API token and
the SHA-256 pin for a persistent, self-signed TLS identity. The private key and
token are stored only in the private data directory; the public fingerprint is
what lets the controller authenticate the certificate without a public CA.
`--auto-tls` requires the `openssl` command once, when the identity is created.
Run the backend through the launcher:

```bash
python3 ./puppy-backend-launcher.py \
  --artifact ./puppy-backend.pyz \
  --state-dir ./data/upgrade -- \
  serve --data-dir ./data
```

`puppy-backend.service` is an optional systemd example; other deployments can
run the same launcher command through their own service lifecycle. The launcher
must remain the long-running parent process: launching the zipapp directly
deliberately suppresses the `remote-upgrade` capability, even if it was enabled
in configuration. The zipapp and its containing directory must be writable by
the service user so it can retain and atomically replace the artifact; the
stable launcher can remain root-owned and read-only. Artifact upgrades
intentionally do not replace that stable launcher. When enabling TLS on an
installation created before pinned health checks existed, copy the newly built
launcher once before changing the backend to HTTPS.

Fresh headless data directories default to automatic TLS; `--auto-tls` is kept
explicit in deployment commands so the intended transport is visible. HTTPS
and secure WebSockets share the same bind port. The API token remains
application-layer authentication; standard TLS provides traffic encryption and
the certificate pin provides backend identity. Paste the complete pairing JSON
in Settings so the controller stores and enforces the pin on probes, proxied API
requests, WebSockets, upgrades, and launcher health checks. Redirects are never
followed with backend credentials.

For a CA-issued certificate, use `--tls-cert` and `--tls-key` instead of
`--auto-tls`; these paths are persisted in backend configuration. Pairing still
includes a pin. `--disable-tls` explicitly selects cleartext HTTP,
which the WebUI labels explicitly and which should only be used over a trusted,
encrypted private network such as WireGuard. The API token grants the authority
of the Unix account running this service; never expose token-authenticated
cleartext HTTP to an untrusted network. Terminal WebSockets are enabled by
default for feature parity and can be removed with `--disable-terminal`.

The automatic identity survives backend artifact upgrades because it lives in
`data/tls/`. If that directory is lost or a custom leaf certificate is rotated,
the fingerprint changes: remove and re-add the backend using fresh pairing JSON.
Pin changes are intentionally never accepted automatically.

Adjust the template user, paths, bind address, and network protection before
installation.

## Scratch workspaces

Controllers can create a session with `workspace_kind: "temporary"` instead of
supplying a working directory. The backend creates a mode-0700 directory at
`<data-dir>/workspaces/session-<random>/`, using its configured data directory.
It advertises this contract with the `temporary-workspaces` capability.

Scratch files survive service restarts and reboots. Deleting the session or
resetting its workspace removes them. The SQLite session and transcript
remain in the data directory. A missing workspace is reported as
`workspace_missing`, and the reset endpoint—or the next turn—creates a new empty
directory, clears the engine-native session id, and seeds the fresh engine context with a notice that
the old files were unavailable. Startup cleanup removes only unreferenced,
service-owned directories inside the validated private namespace; normal
working directories are never removed.

## Remote workspaces

Two additive capabilities let a controller pair one node's session with
another node's project directory. `workspace-provider` means this node can
lease a local directory to its controller and serve the streamed
manifest/fetch/apply protocol over it (`/api/workspace/leases…`), with every
write staged beside its target, SHA-256 verified, compare-and-swap checked
against what the sender scanned, and committed under an fsync'd crash journal;
paths are resolved descriptor-relative with `O_NOFOLLOW`, so no symlink can
escape the leased root, and symlinks themselves travel verbatim as entries
that are never followed. `workspace-mirror` means this node can host linked
sessions: session create accepts a `workspace` descriptor, the engine runs in
a stable private mirror under `data/mirrors/`, and each turn holds at pre/post
sync barriers (`/api/sessions/{sid}/workspace/…` plus the `grant` route) until
the controller reports the reconcile durable. The mirror only accepts applies
at a barrier or while the session is idle, and a mirror holding changes its
project has not accepted yet is flagged `ws_dirty`, retried by the controller,
and blocks backup export until resolved.

Nodes never contact each other and never see each other's tokens: the
controller relays every byte of both directions over the same authenticated,
optionally TLS-pinned channels it already uses. Divergent edits to the same
path are never merged or overwritten silently - they surface as conflicts that
keep both versions until the user picks a side. Mirrors and lease metadata are
rebuildable and stay outside snapshot coverage, like the engines' native
session stores.

## Session activity timing

Session-list responses and update events include `server_time`. A running
session also includes `active_since`, the start of its current uninterrupted
work block. That start is retained while queued messages flow into subsequent
turns and is cleared when no runnable work remains (paused or held prompts do
not keep it running). This lets a controller show one continuous elapsed time
while compensating for clock differences between the controller and backend.
Both timing fields are part of the current contract; the console does not
estimate a missing start time.

The state-stream writer advances `server_time` by the monotonic time a snapshot
spent in the backend's cache and viewer queue before delivering it. The
controller does the same for relayed session snapshots using the originating
backend's clock, including on attach and reconnect. Cached starts and topic
revisions stay unchanged, so refreshing or opening another console preserves
elapsed time without extra polling or browser storage. This timing bookkeeping
is transient; restarting the executing node ends its active work blocks.

When a work block becomes idle, session lists, snapshots and `turn_done` also
carry the additive `completion_status`. A controller uses `interrupted` to
retire its activity timer without firing a configured completion command for a
prompt the user stopped. Queued work that continues after a stop remains one
activity block and can still notify when that later work actually finishes.
The controller selects its configured success command for `ok` and failure
command for `error`, skipping an empty command. Both use the same selected
execution backend and the existing authenticated `/api/notify/exec` route.
Headless nodes share the exact [notification config shape](../docs/completion-alerts.md)
even though command selection and the enabled switch belong to the controller.

The order of `GET /api/sessions` (and of every `sessions` broadcast) is the
node's durable `sort_order`, and the console preserves each node's relative
order when merging the sidebar across backends. All pinned sessions form a
manually ordered block first. The
node moves an ordinary session to the head below that block every time it goes
from idle to running - the start of an activity block, not a queued
continuation or a completion - and `POST /api/sessions/reorder` (drag-and-drop
in the console) edits the same order within each block. Nothing about the order
lives in the browser, so every console and every reload sees the same list.

Nodes advertising `session-order-recency` include `order_at` on session
payloads: a durable ordinary-slot timestamp in UTC seconds, or zero for a slot
that has never been promoted. Consoles merge ordinary node lists by descending
`order_at`; saved backend order breaks ties and orders the pinned blocks.
Backend clocks should be synchronized. Idle-to-running advances recency even
for a session already first on its node; queued continuations and completion
leave it alone. A task promotes its parent. Manual reorder transfers the
timestamps with their slots, retaining other backends' intervening positions.
Pinning clears recency; unpinning assigns a fresh value.

Reorder callers may send `expected_recency`, aligned with `expected_order`,
to reject a drag overtaken by fresh activity even when the node's ID order
stayed identical (409). Invalid timestamp vectors return 400. The exact optional
`session_order_at.<id>` meta records contain positive finite JSON numbers,
belong only to ordinary sessions, descend in node order, and are covered by
backup/restore and session deletion. There is no backfill or schema migration.
Peers without the capability retain their node order with zero recency until
upgraded; activity across those peers cannot be ordered durably.

## Transient engine failures

When an engine's own result says the failure is transient (claude's OAuth
refresh lock timeout: "another Claude Code process is refreshing it or exited
mid-refresh ... retry in a minute"), the node does not record it as lost
login and does not end the prompt. It emits an `info` event with subtype
`engine_retry` (`attempt`, `delay`), waits out the back-off (30 s, then 60 s;
three attempts in all) with the session still `running`, and runs the same
prompt again without a second `user` event. Only the final attempt's outcome
becomes the `result`. Stopping during the back-off cancels the retry like any
interrupted turn; a restart parks the prompt as held. Synthetic API error
messages the CLI emits in the model's place arrive as `error` events with
subtype `engine_api_error` and the vendor's `code`, never as assistant text,
and never count as a model change.

## Engine background work

Claude's `run_in_background` commands, Monitor waits and backgrounded agents
are tasks of the CLI process itself. When the model answers while such tasks
are still running, the node keeps that process alive instead of ending the
turn: the CLI wakes the model on its own when a task ends and the answer
continues within the same turn, whereas closing its stdin would kill the
tasks. The session stays `running` for the whole wait (bounded by
`sessions.turn_timeout`). A persisted `info` event with subtype
`background_wait` (`tasks: [{id, type, description}]`) marks the pause, an
`info` event with subtype `task` (`status`, `task_id`) records each task that
ends. When the CLI supplies its originating `tool_use_id`, that provenance is
retained on the task event so consoles can attach the update to its tool card;
no association is inferred when the native ID is absent.
`background_wait_stopped` records a wait the node ended itself. One
`result` closes the turn, with `usage` and `num_turns` summed over every
wake-up and an additive `wakeups` count. Stopping the turn during the wait
closes the engine's stdin so it ends its tasks gracefully; the answer given
so far becomes the result and the completion status is `interrupted`.

Session sockets receive an additive `background_tasks` message
(`{tasks, waiting, text}`) whenever the live set changes or a wait starts or
ends, and snapshots carry the same object as `background_tasks`; while
`waiting` is true, `text` is the status line to show.

## Active-turn steering transport

`POST /api/sessions/{sid}/steer` sends an additional instruction to the
engine turn which is already running. Its `text` may include the same uploaded
file/image marker lines as an ordinary message, including a message containing
only attachment markers. Upload the files to that session's backend first;
the engine inspects their paths using its existing tools. It is intentionally
separate from the ordinary message route: steering never joins the session queue
or starts a new native turn. The shared runner maps it to each pinned CLI protocol
(streamed user input, app-server `turn/steer`, or an active ACP prompt), persists the
accepted text as a user event with `steering: true`, and refuses idle, starting,
stopping, approval-waiting, or unsupported turns. The route is authenticated
and is present in both the full runtime and the headless package.

Nodes advertise the hardened contract as `active-turn-steering`. Session lists,
session reads, and WebSocket snapshots expose `{supported, ready, turn_id}`;
the caller must echo that exact `turn_id` as `expected_turn_id`. This prevents a
late request from reaching a queued successor. An optional restricted
`request_id` is idempotent within the named turn: an exact retry replays its
delivery state, while reuse with different text or another turn is refused.
Writes are serialized with approvals and interrupts and revalidated under that
lock. `steer_status` WebSocket frames distinguish `sent`, `accepted`, and
`rejected`; protocol rejections also become durable transcript errors. Each
turn has a bounded receipt ledger which is discarded before its successor.
Claude runs with input replay enabled, so `accepted` means its stream protocol
replayed that exact steering message rather than merely draining bytes into the
CLI pipe. Any engine result which arrives before its acknowledgement resolves
the outstanding receipt as `rejected`.

## Account usage refresh

The headless package stores the same per-node usage-refresh interval as the full
runtime. Set it while pairing or serving with `--usage-refresh-minutes N` (1 to
1440 minutes); use `0` to disable it. Once attached, the controller exposes the
same value in Settings → Usage refresh and can change it through the authenticated
`/api/engines/usage-refresh` endpoint. The node advertises this support with the
`engine-usage-refresh` capability.

Nodes also advertise `engine-usage-refresh-manual`. The authenticated POST
form of the same endpoint performs one immediate read even when the automatic
interval is disabled. Controllers use it for the compact refresh control beside
a ready Codex status.

The refresh is lazy: a due engine-status poll asks supported installed CLIs for
their current read-only account-limit snapshot. It does not start a turn or
consume model tokens, concurrent polls coalesce, and failed attempts are
rate-limited. Codex currently supplies a direct account snapshot; its existing
local rollout record remains the fallback if the account read is unavailable.

## Engine versions and upgrades

Every engine payload reports the installed CLI version, the latest published
version, whether an update exists, and the engine's model catalog. Installed
`--version`/auth probes and model lists are cached for five minutes; the latest
published version is re-checked every six hours (fifteen minutes after a failed
check). Catalog requests are single-flight and run concurrently across engines.
A failed discovery keeps the last good choices visible, reports its error and
backs off from 30 seconds up to five minutes. A registry or catalog outage never
makes an installed engine unavailable.

Two authenticated routes complete that picture, and nodes advertise both with
the additive `engine-upgrade` capability:

- `POST /api/engines/refresh` concurrently forces an immediate installed-version,
  sign-in, latest-release, and model-catalog check on this node. This is the
  Settings → Engines refresh button; it does not start a model turn.
- `POST /api/engines/{key}/upgrade` runs that engine's own updater
  (`claude update`, `codex update`, `opencode upgrade`).

The command is fixed by the driver, never by the request: the body is ignored
and the engine key must already be registered on the node. Puppy deliberately
does not detect how an engine was installed - the vendor updater already knows
its npm, native, brew or standalone layout, and delegating keeps that knowledge
with the CLI it ships in.

The run is refused with 409 while any session on that engine is running or has
queued work, because the updater rewrites the installed package in place. Other
engines' sessions are unaffected and do not block it. New turns on the engine
are refused until its update finishes. Each engine has one upgrade slot, so
different vendor CLIs can update concurrently. Every run is bounded twice: its
output is streamed into a capped transcript and its whole process group is
sampled through `/proc`, so an updater that shows no output, CPU, I/O, or
process-tree change for 90 seconds (240 while it holds open sockets, since it
may be waiting on the network) is stopped and reported as making no progress
with whatever it said until then, and a fifteen-minute hard cap remains the
backstop for one that stays busy without finishing. The detection is
engine-, host-, and install-method-agnostic.

It starts in the background and the POST returns immediately, so `upgrade_state`
in the engine payload is the progress signal and `upgrade_result` the outcome -
a reconnecting controller rejoins a run already in flight. Engines are spawned
per turn, so nothing restarts afterwards; the node re-probes the version and
model catalog itself when the updater exits, because a zero exit status alone
does not prove the version moved or the available choices stayed the same.

## Engine model catalogs

Claude discovers its current aliases and each alias's supported effort levels
from a no-turn stream-json `initialize` exchange. Codex uses its app-server
`model/list` method, follows pagination, excludes hidden entries, and carries
each model's own reasoning-effort choices. Its validated on-disk model cache is
only a cold-start fallback. OpenCode reads `opencode models --verbose`; an
explicit Settings refresh adds the CLI's `--refresh` flag so provider metadata
is fetched now, while ordinary five-minute checks remain read-only. Older
OpenCode versions that do not support that flag fall back to re-reading their
current list and report that qualification in the Engines card.

The same payload drives new-session cards and active-session selectors. A live
catalog update preserves a still-valid selection, does not rebuild a native
mobile picker while it is open, and never offers an effort level that the
selected catalog model did not report.

Named Claude rows also publish `aliases`: resolved model IDs, the CLI's
`[1m]` request spellings, every spelling the CLI has shown for the same
resolved model in an earlier discovery or a turn's own picker, and the saved
default or session models the node had the CLI resolve to that model.
Controllers can show the matching model name and
effort levels without rewriting a saved request. Exact catalog values take
precedence over aliases. `model_catalog_loaded` is true only after a successful
discovery; a failed first check still serves a provisional fallback list.

OpenCode is registered in the headless artifact just like it is in the full
runtime. Its status is binary availability only: provider authentication is
model-specific, so a found binary reports `Ready` and an absent one reports
`No binary` rather than attempting one global auth verdict.

An installed node serves every reported OpenCode choice through the ordinary
engine `model_options` list. The controller uses that list directly in
new-session and chat model controls; there is no controller-owned provider list
or node-owned model allow-list.
Turns run through `opencode acp`, resume the native session ID, relay tool
approvals/cancellation, and receive the same turn-scoped managed-browser MCP
server when Browser is enabled on that node, plus the shared-terminal MCP
server when terminal support is enabled.

Puppy resolves OpenCode from the service's `PATH` first. It also checks the
official install script's per-user fallback at `$HOME/.opencode/bin/opencode`,
because non-interactive services normally do not source the shell file which
the installer updates. Run the backend with the same `HOME` as the account that
owns OpenCode's installation and provider credentials.

## Managed browsers

The headless artifact serves the same node-owned managed-browser execution
surface as the full runtime. Browser viewer WebSockets accept the additive
`viewer_active` boolean message: inactive sockets retain the page and binding
but receive no frames, and Chromium's screencast pauses when no viewer is
active. Reactivation starts the stream and sends an explicit fresh frame. A
newly attached viewer defaults to active.

The additive `browser-cursor` capability supplies best-effort standard CSS
cursor hints. The initial viewer status includes `cursor_supported: true`;
only then does the console send `{type:"cursor", id, nx, ny}` on that socket
(positive safe integer id, normalized coordinates). Replies are
`{type:"cursor", id, value}` to the requesting viewer only. Reads are bounded,
coalesced per viewer, and never block input; inactive/disconnected viewers
cancel their reads. The console refreshes only while hovering, rejects stale
replies, and accepts only standard cursor keywords. Custom images use their
keyword fallback; cross-process frames, native widgets and drag feedback are
best effort. This is transient state, with no configuration or snapshot change.

Each entry in the Browser status `instances` array includes ephemeral
`frame_flow` counters and rates for screencast frames received, screenshots
captured, frames actually written to viewers, and drops caused by a stale
surface, decode failure, inactivity, or viewer backpressure. These metrics are
diagnostic process state, are never persisted or backed up, and exist so ACK
pacing or image-quality changes can be based on measured behavior.

Agent accessibility snapshots use bounded root/child traversal under one
time/node budget, retaining a bounded full-tree fallback for compatible older
Chromium. An individual CDP response that exceeds the pipe safety limit is
discarded through its NUL boundary and fails only the matching pending request;
it does not terminate the managed Browser or strand direct viewer input.

Viewer navigation never blocks the input socket: address-bar, back/forward,
and reload requests run as bounded background tasks, so mouse, key, and later
navigation messages are processed while a slow site is still committing.
Status broadcasts carry an additive `loading` boolean driven by main-frame
lifecycle events (with an optimistic set when a viewer requests a navigation),
and a refused or failed navigation is reported as a non-terminal `error`
message on the same socket.

`browser.shared_storage` is node-owned config behind the additive
`browser-shared-storage` capability: `POST /api/browser/shared-storage`
toggles it and `/api/browser/status` reports it. While on, every managed
browser on the node three-way merges its cookie jar against one private store
under `data/browser/shared/` (mode 0600; per-browser baselines; local changes
win conflicts; deletions propagate live to running peers; partitioned cookies
are never exported), and page localStorage is captured best-effort and seeded
only into documents that do not already hold a key. The store persists across
browser and node restarts, survives disabling the toggle, and - like the rest
of `data/browser/` - is deliberately outside snapshot coverage, equivalent to
the engines' native credential stores.

## System prompts

Each node stores its own custom prompt plus conditional remote-workspace,
Browser, Terminal, remote-screen, and spawned-agent guidance. The custom layer
is added to every new model turn the node starts. The Browser layer is added
only when Browser is enabled and the turn receives managed-browser tools. The
Terminal layer is added only when the node offers shared-terminal tools, and its
shipped policy tells models to use those tools only after an explicit user
request; ordinary shell work continues through the engine's normal tools. The
remote-screen layer accompanies the VNC MCP bridge every execution node offers
and keeps the tools to screens the user actually named. The
spawned-agent layer accompanies the always-offered spawn MCP bridge and keeps
delegation explicitly user-requested because spawned runs spend real
subscription quota. Active turns keep the prompt with which they started.

`config.system_prompt` holds exactly `custom`, `remote_workspace`, `browser`,
`terminal`, `vnc`, and `spawn`. A `config.json` written before the remote-screen
policy must have `system_prompt.vnc` added by hand before that node starts;
startup and backup validation reject the earlier shape rather than filling it in.

## Timeout settings

Both runtimes expose `GET`/`PATCH /api/timeouts` behind `timeout-settings`.
This node owns its maximum turn duration, spawned-agent runtime/inactivity,
and unattended terminal/browser limits. Values are whole seconds; zero is
unlimited. Defaults are 7200, 7200, 600, 900, and 900 seconds respectively.
The CLI also accepts `--turn-timeout 0`. Settings initialize new turns/jobs;
unattended timer changes rearm existing unviewed instances immediately.
See [the complete contract and required manual config preparation](../docs/timeouts.md).

## Host activity

Every node serves `GET /api/host/metrics` behind the additive `host-metrics-v1`
capability. It answers with that node's own CPU history (the samples its
process has taken, oldest first, inside the requested `window` seconds - a
missing, hostile or oversized window falls back to the default 900), its core
count and sampling interval, its load average, memory and uptime, and the
trimmed tree of processes descending from the node's own Puppy process. Every
value is read from `/proc`; a process's CPU share is the delta between two
scans, so the first read of a fresh process reports `null` rather than a guess.

The tree is trimmed, not dumped: identical childless siblings become one row
carrying `count` (the five per-turn MCP bridges, a browser's renderers),
children are capped per parent, depth and total nodes are bounded, and every
omission is counted in that branch's `more` and the reply's `hidden`. Nothing
is persisted or published on the state stream by a headless node, so a restart
forgets it and no backup contains it.

Round-trip latency belongs to the controller, not to a node: it times
`GET /api/ping` on each backend it has already marked online and reports an
offline one from its own health verdict without probing it. A failed
measurement never changes availability.

## Spawned agents

Every node advertises the additive `spawn-exec` capability: `POST /api/spawn`
starts one non-interactive engine run (engine, optional model/effort,
permission mode, prompt, working directory, inactivity limit, absolute runtime)
after validating the request against that node's installed engines, and
`GET`/`PATCH`/`DELETE /api/spawn/{job_id}` poll, replace live limits, or cancel
it. Omitted limits use that executing node's Settings → Timeouts values,
initially a 600-second sliding silence limit, renewed by positive normalized
engine progress, and a 7200-second runtime measured from job creation.
Either limit accepts zero for unlimited, behind `timeout-settings`.
Identical repeating status noise does not renew the lease. The PATCH
route is advertised as `spawn-progress-limits`. The retired `timeout_s` request
field is rejected; use `idle_timeout_s` and `max_runtime_s`. `POST
/api/spawn` also honors a controller-chosen `job_id`, advertised as
`spawn-client-job-ids`: the start is idempotent for that id (the same id
answered again returns the job it already started) and the poll/cancel routes
wait behind an in-flight start of it, so a controller registers its relay
handle before transmitting and a start whose answer is lost still names a job
that can be waited for, cancelled, and reaped; a later 404 from the node ends
that job as `lost`, while an unreachable node keeps the handle for retry.
Every relayed start carries `lease_s` under `spawn-owner-lease`: the
controller renews the lease in the background (`POST /api/spawn/renew` with
a batch of ids; a poll or limit change renews too), and a job whose
controller stops renewing - a crash or a partition, never a brief blip - is
stopped by the node as `abandoned` once the lease lapses instead of running
unobserved to its own limits. Jobs are
in-memory and never part of a snapshot. Engine turns receive a turn-scoped
`puppy_spawn` stdio MCP bridge (targets/spawn/wait/update_limits/cancel); jobs it starts die with their
turn - the runner reaps them synchronously before the post-turn workspace
sync and the next queued prompt, and a node's shutdown kills its local jobs
and cancels the ones it relayed while its channels are still open - approval
requests inside a spawned run are auto-denied with an
explanation, and cross-node spawns exist only on the controller, which relays
them over its already-authenticated channels - nodes still never contact each
other, so a session hosted on a backend can spawn only onto its own node.

The spawn, browser, terminal, VNC, and session MCP descriptors explicitly pass
the node's absolute `PUPPY_DATA` path alongside the package path. This keeps their
configuration tied to the backend even when an engine filters inherited
environment variables or runs a bridge in a different working directory.
The backend remains API-only: these bridges need no web UI or additional
network listener.

A running spawned agent also blocks that engine's CLI upgrade and (including
one still tearing down) the backend's own signed self-upgrade, and spawn
requests are refused while the engine's updater runs. A parallel fan-out
(spawn `count`, up to 12) is expanded by the bridge into independent jobs -
a remote fleet is simply that many relayed single starts, so any spawn-exec
node can host one - `wait` operates on job-id lists with one shared concurrent
budget, while `update_limits` and `cancel` accept the same list shape (`cancel`
runs concurrently and reports each job's outcome; a relayed verdict stays
readable for the rest of the turn, and a combined result too large for one
bridge response is shortened per answer with a hint to re-read that agent
alone rather than replaced by an error). A node is addressed by its display
name or the `#id` that `targets` lists; a name fitting more than one node is
refused rather than resolved to the first match, and the controller keeps
backend names unique, distinct from its own instance name, and free of the
reserved local aliases when backends are paired or renamed. Each node
also caps its total running spawned agents. Limit changes remain
ownership-checked, accept whole seconds from 0 through 2147483647 (0 is
unlimited), and are offered to the orchestrating engine only for explicit user
steering while jobs remain attached to that turn. The console's composer
offers the request as an "@" mention: a "New spawn" wizard slides through
agent count, node, engine, model, and effort, then inserts the plain-text
directive `@Spawn <an agent|N agents> [on <node>] using <engine> [<model>]
[at <effort> effort]`. No “to” separator is required: the spawn MCP guidance
tells the engine to take the task from the surrounding user request, before
or after the directive. The sent-message token also accepts optional “to”
or “and” wording.

An attached console reads and edits these fields through authenticated
`GET/PATCH /api/system-prompt`. Nodes advertise the additive `system-prompt`
capability. The console requires every current prompt and default field. The API limits
each field to 32,768 characters and returns Puppy's shipped conditional texts
so the console can implement Reset to default without embedding second copies.

## Shared terminals

Terminal-enabled nodes advertise `terminal-instances` and `terminal-handoff`
beside the `terminal` capability. A controller creates a node-owned PTY
through `POST /api/terminal/instances`, then attaches xterm.js to its
ID-scoped WebSocket. Four-character Terminal IDs, process lifetime, transcript
replay, and session links therefore survive viewer reconnects. Viewer sockets
always name an existing terminal.

Behind the additive `terminal-engine-cli` capability the same POST accepts
`engine` (an engine key such as `claude`) instead of `command` and `cwd`: the
node runs that engine's own interactive CLI - the installed binary its driver
resolves, exactly as a turn would spawn it, with no arguments - in a private
scratch home of its own, `<system temp>/puppy-cli-<uid>/<engine>/`, created
mode 0700 on first use and kept for the next (the CLI's folder trust and
resumable sessions belong to that directory). A request naming `engine`
beside a command or directory, an unknown engine, an engine whose binary is
missing, or one whose vendor updater is running is refused with 409 and the
reason. Instance and status payloads carry the additive `engine` (`null` for
a shell), the CLI's exit ends the terminal with the reason `<Engine> ended`,
and while it runs the terminal counts as that engine's live process: the
engine's CLI upgrade is refused (`blockers` names `Terminal <ID>`) and the
scheduler waits. The scratch home holds nothing Puppy owns and is outside
backups; only a directory this account owns is ever used - a symlink, a file
or another account's directory in its place is refused, never replaced.

A terminal can be linked to exactly one chat and a chat to one current
terminal. Every engine turn on a terminal-enabled node receives a private stdio
MCP server backed by a mode-0600, same-uid Unix socket into that node-owned PTY.
It offers bounded snapshots and high-level typing/key/wait operations, never a
raw PTY file descriptor. Initialization alone creates nothing and changes no
UI. The first real terminal tool call in a turn emits an identified activity
event so the controller inserts that Terminal tab beside the chat without
taking focus. The bridge rejects calls after the originating turn ends, and an
unviewed terminal is stopped after its idle grace period. PTYs, replay buffers,
and links are memory-only: a node restart or full state restore closes and
forgets them rather than placing terminal contents in a backup.

## Remote screens

Every execution node is its own VNC client: `GET`/`POST /api/vnc/instances`,
`DELETE /api/vnc/instances/{id}` and the ID-scoped viewer socket
`GET /api/ws/vnc/{id}` sit behind the additive `vnc` and `vnc-instances`
capabilities, and the catalog rides the node state stream as `vnc_instances`.
Connections are memory-only with four-character IDs: a restart or a full state
restore forgets them rather than putting a remote screen in a backup, and a
password lives only in the process that dialled the server. `vnc.idle_timeout`
drops an unwatched connection while keeping its identity, so reattaching
redials. A VNC connection never blocks a turn, a backup or a node upgrade.
The additive `vnc-connect-cancel` capability accepts a `request_id` on the create
POST and `DELETE /api/vnc/connect/{request_id}` to abort its dial or handshake,
including cancellation arriving before the POST or after a successful reply.
These bounded, short-lived request records stay in memory. Disconnect discards
queued and cached frames before notifying viewers, so a stopped server cannot
appear connected again because of stale pixels. See the
[lifecycle contract](../docs/vnc.md#lifecycle).

Every engine turn on such a node also receives a private stdio MCP server,
backed by a mode-0600, same-uid Unix socket, that offers the screens the node
already holds: list, connect, screenshot, move, click, drag or swipe, scroll,
type, press and disconnect, plus a wait that returns once the picture settles.
Screenshots are PNGs the node builds from the decoded framebuffer with `zlib`
alone - no image library is installed - and every coordinate is a remote screen
pixel. The bridge never exposes RFB itself, initialization alone connects
nothing, the first real tool call in a turn emits an identified activity event
so the controller inserts that VNC tab beside the chat without taking focus,
and calls after the originating turn ends are rejected.

## Durable message queues

Prompts queued behind a running turn are written through to the node's database
on every change, so they belong to the user rather than to the process. A
console connected to a node advertising `queue-pause` may pause any ordinary
queued prompt. The node publishes its indexes in the additive `paused` array;
automatic dequeue skips those prompts while continuing with the next runnable
one. Pending model/effort/permission changes are not separately pausable;
configuration rows encountered before the selected runnable prompt still
apply in their visible order.

Nodes advertising `queue-reorder` also accept a duplicate-safe full
permutation guarded by the queue's additive process-local revision. A console
acquires a short per-socket scheduler hold before a row becomes draggable and
commits or cancels it on drop. If the active turn finishes while the row is in
flight, no queued prompt starts until that decision arrives. Socket disconnect
releases the hold immediately and a 30-second lease is the final fail-open, so
a vanished browser cannot strand work. Prompt rows can move across the visible
configuration rows, making the resulting execution order explicit.

Nodes advertising `queued-engine-switch` extend the same ordering to engine
changes: a switch requested while a turn runs or prompts wait joins the queue
as an additive `{kind:"engine"}` row instead of being refused with 409, and
the switch response carries the additive `queued` flag. Prompts sent before
the row keep the engine they were written under; when its turn comes the row
emits the ordinary `engine_switch` divider, resets the session to the target's
defaults and starts a fresh native conversation, exactly like an immediate
switch. Nodes advertising `queued-permission-config` include the target
engine's permission default on that switch row and put later permission picks
through the same ordered configuration path as model/effort. All configuration
rows carry the `engine` whose catalog validated them: a row orphaned by
cancelling or reordering away its switch is skipped
with a transcript note rather than applied to another engine, and re-sending
a held setting from a previous engine is refused. Engine-CLI upgrades count
queued switch targets as busy sessions, and a switch aimed at an engine whose
updater is running is refused either way.

A shutdown or engine kill parks whatever had not started as *held* items, and a
restart restores them as held: visible in the console with a warning mark, run
again only on an explicit re-send, never automatically - the transcript they
were queued behind may have ended mid-thought. The session snapshot and queue
broadcasts carry an additive `held` array old consoles simply ignore, and the
session websocket accepts `requeue_held` / `discard_held` with the same
stale-index guard as `unqueue`. A prompt is consumed durably the moment its
turn starts, so a crash never runs one twice.

## Pinned sessions

Nodes advertising `session-pinning` accept a boolean `pinned` field on
`PATCH /api/sessions/{sid}` and carry that flag on every session payload. A new
pin joins the bottom of the existing pinned block; unpinning places it at the
top of the ordinary block. Activity never rearranges pins. Reorder requests
are full permutations, are stable-partitioned against the node's authoritative
pin state, and may include `expected_order` plus `expected_pinned` compare
values so simultaneous consoles fail with 409 instead of overwriting one
another. Malformed or duplicate lists return 400; changed membership, order,
or pin state returns 409.

## Agent notes

Nodes advertising `session-agent-notes` serve the AGENTS.md and CLAUDE.md
files in a session's working directory: `GET /api/sessions/{sid}/agent-notes`
returns both (existence, text up to 256 KiB, symlink target) and
`PUT /api/sessions/{sid}/agent-notes` with `{"name", "text"}` replaces one
atomically - through a symlink to its target, so a `CLAUDE.md -> AGENTS.md`
layout survives - or removes it with `{"name", "delete": true}`. Only those
two names are accepted. Every session payload carries the additive
`agent_notes` list naming which of them exist; the console shows it as the
mark at the bottom right of each sidebar row, which opens the editor. A note
edited inside a linked workspace's mirror marks the session dirty so the
controller carries the change to the authoritative project at its next
reconcile.

## Session titles

Nodes advertising `session-titles` keep a session's request for a generated
title: `POST /api/sessions` and task creation accept `auto_title: true`
(ignored with a `name`), every session payload carries the additive
`auto_title` (`null`, or `{"state": "armed"|"requested", "requested_at"}`),
`GET /api/sessions/{sid}/title` returns the request with its text (`404`
without one), and `POST /api/sessions/{sid}/title` with `{"requested_at",
"name"}` or `{"requested_at", "error"}` settles it - the name lands only
while the session still carries the placeholder the request recorded - and
answers `{"ok": true, "applied", "name"}` (`409` once it is no longer
pending). `POST /api/titles/jobs` with `{"engine", "model", "effort",
"prompt", "wait_s"}` starts one spawn job of that engine in the node's empty
`data/titles/` directory, without the node's system prompt, under a 60-second
silence limit and a 180-second runtime, answering `{"ok": true, "job"}` in the
spawn payload shape; `GET`/`DELETE /api/spawn/{job_id}` poll and cancel it.
The settings and the consumer that runs jobs live on the controller; a
headless node only keeps the records and runs the jobs it is asked to. The
`titles` config section is required on every runtime. See
[Session titles](../docs/session-titles.md).

## Git repositories

Nodes advertising `session-git` carry the additive `git` field on every
session payload: `null` until the node has looked at the session's working
directory, then `{"repo": false, "checked_at": <seconds>}`,
`{"repo": true, "checked_at": <seconds>, "changes": <n>, "unpushed": <n>|null,
"branch": "<name>"|null, "staged": <n>, "unstaged": <n>, "untracked": <n>,
"conflicts": <n>}`,
`{"repo": true, "checked_at": <seconds>, "changes": null, "unpushed": null,
"error": "…"}` when `git` would not read the repository's state, or
`{"repo": null, "checked_at": <seconds>, "error": "…"}` for a directory it
could not read. The answer says whether the directory is inside a Git work
tree, found the way `git` finds it - a `.git` directory or gitfile in the
directory or a parent, stopping at the filesystem root, a mount boundary or
`GIT_CEILING_DIRECTORIES` - without running `git`; inside one, `git` itself
is asked how many paths its status lists (`changes`), sorted by what it would
do with them (`staged`, `unstaged`, `untracked` and `conflicts`, each path
counted once, adding up to `changes`), which branch is checked out (`branch`,
`null` with `HEAD` detached), and how many commits on `HEAD` no
remote-tracking branch holds (`unpushed`, `null` when no remote is
configured), read-only, without the optional index lock and bounded to 30
seconds. It is cached in memory per directory, re-checked by the node's
worker every `git_check_minutes` (`/api/timers`, default 15), at once by
`POST /api/sessions/{sid}/git/refresh`, which answers `{"ok": true, "git":
<record>}` and publishes the session list when any field of the record
changed, and again when a prompt finishes in the directory (a task's prompt
excepted - its clone is not the project), when a task's changes are applied
to it, and when its agent notes are written. Nothing is persisted or backed
up. The console shows the record as the branch mark between each sidebar
row's pin and notes - in the warn tone with the counts in its label and the
branch, kinds and commits in its hover tooltip when there is uncommitted or
unpushed work - and posts the refresh when a session is brought into focus.

Nodes advertising `session-git-detail` also serve
`GET /api/sessions/{sid}/git`, the read behind the sheet a repository's mark
opens: one more inspection of the directory answering `{"ok": true, "git":
<record>, "root": "<work tree>"|null, "detail": <listing>|null}` - the fresh
record (the cache and the published mark move with it), the resolved work
tree the listed paths are relative to, and the listing `git` gave: `head`
(`null` on an unborn branch), `upstream` with `ahead`/`behind` (`null`
without one), `remotes`, `paths` (`{"kind", "code", "path"}` plus `"from"`
for a rename: the kind the record counts it under, git's own two-column
porcelain code and the path exactly as git holds it), `commits` (`{"hash",
"subject", "author", "at"}`, newest first, the commits no remote-tracking
branch holds) and `more_paths`/`more_commits` for what the bounds of 500
paths and 200 commits cut. `root` and `detail` are `null` outside a
repository, `detail` alone when `git` refused (the reason in the record's
`error`). Concurrent reads of one directory share one inspection; the read
is never counted as a mutation. The console makes a repository's mark a
button that opens the sheet only for nodes that advertise this.

Nodes advertising `session-git-log` also serve the sheet's History, `GET
/api/sessions/{sid}/git/log?skip=N&limit=M`: one page of the short log of
everything on `HEAD` as `{"ok": true, "total", "skip", "commits": [{"hash",
"subject", "author", "at"}, …], "more"}`, newest first, `limit` 1 to 100 (the
default) and `skip` any count from 0 (anything else `400`), git's refusal a
`409` with its reason, an unborn branch a `total` of 0. It is a read like the
sheet's, never counted as a mutation.

Nodes advertising `session-git-actions` also serve the sheet's two writes,
`POST /api/sessions/{sid}/git/push` and `POST /api/sessions/{sid}/git/revert`
(an empty JSON body). Push sends the checked-out branch's commits where a
push would go - its upstream, or the one remote (`remote.pushDefault` or the
only one) with the upstream set by the push, never forced, with whatever
credentials `git` finds non-interactively; the listing's additive
`detail.push_to` names that target (`origin/main`) or is `null` when there is
none (`HEAD` detached, no remote, several remotes and nothing choosing).
Revert runs `reset --hard HEAD` (the index emptied instead on an unborn
branch) and `clean -fd` from the work tree's root: tracked paths back to the
last commit, untracked paths removed, ignored files and nested repositories
kept, and the session's own directory put back if it went with them. Both
answer `{"ok": true, "git": <record>, "root": …, "detail": <listing>}` like
the read plus `"pushed": {"to": "origin/main", "commits": 1}` or
`"reverted": {"changes": 3}`, and refuse with `409` and a reason: a task's
copy, a mirrored workspace, Puppy draining, no repository or one `git` would
not read, `Nothing to push`, `Nothing to revert`, a detached `HEAD` or no push
target, a rejected push (`git`'s own reason, such as `[rejected] main -> main
(fetch first)`), and a turn running or queued in any session inside the
project or a task being prepared or applied to it - both run under the
project lock an apply takes, a prompt sent meanwhile waits for them, and
the node is busy for backups and upgrades while they run. A push honours the
`X-Puppy-Operation` cancel header; each `git` command is bounded to 300
seconds. See [Git repositories](../docs/session-git.md).

## Session tools

Nodes advertising `session-tools` accept `POST /api/sessions/{sid}/tool` with
`{"tool": "compact"}` or `{"tool": "undo"}`, and every entry of `/api/engines`
carries the additive `tool_options` list naming what that engine can run.
OpenCode offers compaction; Claude and Codex offer both actions. All are
engine-native: Claude compacts by running its `/compact` local command
inside an ordinary stream-json turn and undoes by resuming the next prompt
with `--resume-session-at`/`--resume-drops-turn`, which branches its
transcript so the dropped turn stays orphaned for every later resume; Codex
compacts with `thread/compact/start` (a turn of its own) and undoes with
`thread/revert`. OpenCode compacts through its public HTTP summarize API in
a temporary, authenticated loopback server, bypassing custom slash commands.
Undo is conversation-only on both engines that offer it: files changed by the
dropped turn are left alone.

OpenCode's HTTP success reply alone does not prove compaction succeeded.
Puppy verifies a new, completed, nonempty summary linked to this operation's
manual compaction request. Before submitting that request it durably detaches
the native session ID, restoring it only after verification. A failure, stop,
timeout or crash after this checkpoint therefore leaves fresh native context
for the next prompt, seeded by Puppy's existing bounded transcript handoff;
queued work is held for explicit resend. A preflight refusal leaves the native
context unchanged. The native history and Puppy's transcript remain stored.
The maintenance server and its children end with the turn, including when
the owning process is killed. No new persisted format or dependency is needed.
See [the compaction contract](../docs/opencode-compaction.md) for verification,
limits and reproducible tests.

Compaction while a turn runs or prompts wait joins the message queue as a
runnable, additive `{kind:"tool"}` row tagged with the engine in force at the
tail; it runs in visible order, can be cancelled like any row, and is parked
as held work by a restart. Undo needs an idle session with an empty queue and
a completed last turn whose result recorded the engine's native identities
(`native_session_id` plus `native_prompt_id`/`native_tail_id` for Claude or
`native_turn_id` for Codex); an older, interrupted, or first turn answers 409
with the reason. The response's `queued` flag says whether compaction waited,
and `restore_text` hands an undone prompt back to the composer. Tool turns
persist an `info` event (`subtype: "tool"`) and a `result` stamped with
`tool`; they receive no agent bridges, guidance, or steering.

## Shared composer drafts

Nodes advertising the additive `session-drafts` capability persist one
versioned composer value per session. The initial session-WebSocket snapshot
carries that value and later `draft` frames are written through to SQLite and
broadcast in a single revision order, so two open consoles follow one another
and a different device can resume after a power cycle. Sending a prompt clears
only the exact value submitted; an edit accepted from another console in the
meantime is preserved.

Attachment marker lines are part of the same value, so staged chips and image
previews restore from the node-owned upload rather than a browser-only blob.
Draft-only files are retained until an unambiguous lifecycle boundary instead
of being eagerly deleted while another console may still have an update in
flight.

Prompt recall uses the same stored transcript on every device. The additive
`session-prompt-history` capability enables `GET
/api/sessions/{sid}/events?kind=user`: the filter applies before `limit`
(1–500, default 200), with the ordinary exclusive `before_seq` / `after_seq`
cursors and ascending event order. There is no total history cap or separate
recall store. The console fetches prompts on demand; older peers use unfiltered
event pages until upgraded. Queued prompts enter recall when they start and
become transcript rows; folded task archives are not prompts in Main.

The additive `session-draft-presence` capability adds `draft_presence:
{version:1,count:N}` to the socket's initial snapshot. Clients send
`{type:"typing",active:true|false}` over that same authenticated session socket;
viewers receive `{type:"typing",count:N}` excluding their own socket. Presence
is per conversation (including tasks), anonymous, memory-only, and removed on
disconnect or six seconds without renewal. The console throttles renewals to
one per 1.5 seconds of input and sends stop after three seconds without input,
on blur, hiding the page/conversation, and Send. It adds no polling or engine
work.

On this capability, a `draft` write includes the integer `expected_revision`.
The hub checks it inside the same lock as persistence and ordered broadcast;
a stale writer receives a private `draft_conflict` with the current draft and
its request identity, without changing the shared value. A focused local edit,
IME composition, pending upload, or unacknowledged edit is never replaced by
a peer. Divergent drafts remain in the existing browser journal until the
user reviews the two versions or sends their own. Review writes compare the
revision actually shown. There is no automatic text merge or persisted-shape
change; backups retain their existing draft coverage. Older clients keep
the original wire contract, and older backends do not offer presence.

A `draft` write may also carry `caret: [start, end]`, the writer's selection
over the value it sent in the console's own UTF-16 offsets. The hub relays it
on the accepted broadcast frame exactly as sent - two ordered non-negative
integers within the text, anything else is dropped from an otherwise accepted
write - and never stores it: the draft record, the snapshot and a
`draft_conflict` reply carry none. A console that adopts a peer's frame puts
its own caret there (clamped to its prose) and scrolls to it; its own echo,
a kept local draft and an IME composition are never moved. Older backends
ignore the field and older clients never send it.

Negotiated draft failures use correlated `draft_error` replies. Messages sent
with `draft_guarded:true` receive `draft_send_error` on refusal (or with
`accepted:true` if the prompt was accepted but clearing its draft failed).
The console keeps the editor until acknowledgement, exposes inline retry, and
leaves edits made during that wait intact. A send never consumes a different
peer draft; a coalesced saved prefix is cleared afterwards by a guarded write.

## Session search

Every node advertises the additive `session-search` capability: `GET
/api/search` answers full-history queries over this node's own transcripts
(user prompts, replies, thinking, tool activity, system notes, plus one
synthetic title document per session) from a node-local SQLite FTS5 index at
`data/search/index.db`. The index is a derived, rebuildable cache fed by a
background reconcile worker - it is never migrated (an unknown shape is
deleted and rebuilt from the events table), never snapshotted, and a restored
backup triggers a full re-derivation. Queries support implicit AND, quoted
phrases, `-` exclusion, `OR`, kind/time/session filters, relevance or recency
order, and grouped-by-session or per-session paginated responses with
control-character-delimited snippet highlights. A controller fans one query
out to itself and its online capable nodes and merges results; offline or
offline or search-incapable nodes contribute nothing, so no transcript is mirrored for
search. Opening a hit lands on that exact message: nodes advertising the
additive `session-event-window` capability accept an `after_seq` cursor on
`GET /api/sessions/{sid}/events` (oldest first), so the console loads a
window of history around the target rather than paging back from the tail,
and offers Load newer / Jump to latest to return.

## Graceful shutdown notice

Headless nodes advertise the additive `shutdown-notice` capability. While
authenticated update and session WebSockets are still open, a graceful stop
sends one `node_stopping` event with `reason: "shutdown"` (or `"restart"` for a
managed upgrade) before entering the ordinary turn grace window. Connected
consoles can immediately mark that backend unavailable, retire cached running
session state, and disable live controls instead of waiting for their next
poll or TCP timeout. The write is best effort and bounded to one second, so a
slow browser can never hold up machine shutdown. Once the application-level
turn drain is complete, the node explicitly closes every remaining Puppy
WebSocket within a one-second bound; this keeps aiohttp's generic request drain
from applying its shutdown timeout twice to long-lived channels. Abrupt power
loss or a laptop
whose network disappears before the OS runs service shutdown cannot emit the
notice and is detected by the controller's health probe.

## Controller-owned availability

The full WebUI controller is the only component that discovers whether a
paired backend is reachable. It publishes a transient `availability` object on
each backend row and admits proxy traffic only while that state is `online`.
The browser therefore does not send session, engine, browser, or WebSocket
requests to a backend already known to be offline. A lightweight authenticated
`/api/ping` probe is the sole recovery traffic; failures use exponential
backoff capped at eight seconds, while an explicit **Test** can retry
immediately. This state is not persisted and does not change the
controller/backend protocol version. The controller does retain the last
authenticated session-list observation in a separate exact-version,
rebuildable cache: an unavailable node's sessions remain visible and openable
in the sidebar, visibly muted until the node is healthy again.

## Timer settings

Every node exposes its refresh/cache settings through `GET`/`PATCH /api/timers`
and advertises the additive `timer-settings` capability. The response includes
the current values, defaults, units, and accepted ranges. Published CLI release
checks, model-catalog caching, installed-version/sign-in caching, and the Git
repository check (`git_check_minutes`, below) are owned by the node running
those sessions. Remote session polling, remote node/engine polling, and
completion synchronization are controller-owned; their persisted fields remain
in the headless node's exact config shape for portable, strict backup
validation but are not scheduled there.

## Unattended engine updates

A node can install its own engine CLI updates on a schedule. It adds no upgrade
machinery: the scheduler decides *when* to run the same vendor-delegated updater
`POST /api/engines/{key}/upgrade` runs by hand, so an automatic run is the
manual run with nobody clicking. `GET`/`PATCH /api/engines/auto-upgrade` read and
set `{enabled, mode, at}` - `mode` is `now` or `at`, and `at` is a local `HH:MM`.
The node advertises the routes with the additive `engine-auto-upgrade`
capability.

Two rules bound the damage an unattended updater can do:

- **One attempt per version pair.** A pair is (installed version -> latest
  version). Once the updater has *run* for a pair it never runs again for that
  same pair, successful or not, so a broken release cannot be retried in a loop.
  The accepted start reserves the durable ledger before Puppy waits for the
  result, so a restart does not grant a fresh attempt. Being
  refused - busy sessions on that engine, its updater already running, or a
  backup in flight - is not an attempt and consumes nothing. One scan starts
  every eligible engine before awaiting results, so independent vendors update
  concurrently while retaining separate ledgers.
- **A window, not a moment.** `at` permits a start during the two hours after
  the given time, so a node busy at the stroke of the hour still updates that
  night while one busy all window waits for the next day rather than replacing
  an engine mid-afternoon.

## File uploads

The headless package accepts streamed session attachments of any file type and
stores them privately under `data/uploads/`. Files are created mode 0600 inside
mode-0700, session-specific directories; uploaded executables are therefore not
made executable merely by transferring them. Set the per-file limit with
`--max-upload-size-mb N` (maximum 1024 MiB); `0` disables uploads. The same
setting is available for every current attached node in Settings → File uploads
through the authenticated `/api/uploads/settings` endpoint. The receiver checks
both declared and actual byte counts, so controller or client-side checks are
only conveniences and cannot bypass the node's limit.

`GET /api/sessions/{sid}/upload/{upload_id}` reads one stored image back, so a
console that has reloaded can still show its attachment previews instead of bare
file names. It is deliberately narrow: the node advertises it with the additive
`upload-preview` capability, serves only PNG/JPEG/WebP/GIF (never SVG, which is
scriptable) with an explicit content type and `nosniff`, and takes no
caller-supplied filename - an upload directory holds exactly one file. Older
nodes omit the capability and their previews stay as named cards.

Controllers stream remote uploads rather than buffering them and preserve TLS
pinning, token authentication, redirect rejection, and the receiving node's
authority over the limit. Active transfers temporarily make upgrade readiness
busy so an artifact restart cannot interrupt a partially written file. Older
nodes retain their image-only endpoint and remain explicitly marked unsupported
for arbitrary-file settings until upgraded.

## CLI release status

The full runtime and headless package periodically read bounded `latest`
metadata for each engine's vendor-published package over HTTPS. Successful
results are cached for six hours; failures keep the last known result and retry
after 15 minutes. This is advisory only: it never installs software, never
starts an engine turn, and a registry or network failure cannot disable an
installed engine. `/api/engines` reports the latest version and whether the
installed semantic version is older, allowing the controller to mark only the
existing version pill as outdated.

## Remote upgrades

After the one-time launcher bootstrap, the attached WebUI can upgrade an older
backend from Settings. The controller builds its matching backend artifact,
self-tests it, and signs the canonical manifest plus bytes with an HMAC key
derived from that backend's API token. The backend then:

1. requires an idle node (no running/queued turns or terminal sessions),
2. verifies the signature, monotonic version, size, and SHA-256,
3. validates the ZIP and runs its isolated `self-test`,
4. retains `puppy-backend.previous.pyz` and atomically replaces the live file,
5. exits with the launcher's reserved upgrade status,
6. lets the launcher health-check the new API and either commit or restore the
   previous artifact before restarting it.

Pending state and the last result live under `data/upgrade/` with private
permissions, so a launcher crash or host reboot during replacement continues
the same validation or rollback on the next start. Replays and
downgrades are rejected because the target version must be newer than the
running version. TLS-enabled nodes advertise remote upgrade support only when
the active launcher declares pinned-certificate health-check support.

`GET /api/node/upgrade` reports live readiness as `ready`, `busy`, `upgrading`,
`blocked`, or `unsupported`, including active session/queue blockers and the
terminal count. Settings polls this lightweight status while visible: the
Upgrade button is enabled only when the node reports that it can accept the
request. The POST repeats the same check and can still reject a race or any
runtime blocker before staging; it checks the workload again after candidate
validation. Older upgrade-capable nodes are supported for their first upgrade
by conservatively inferring session activity, while their existing POST gate
remains authoritative for terminals and races.

Each attached headless backend also has an opt-in **Auto-upgrade when idle**
policy in the controller's Backends card. It is controller-owned (and therefore
included in a WebUI backup), not a setting stored on the remote node. When the
controller version moves ahead, its background worker checks the node's live
readiness and starts the same signed, self-tested, health-checked upgrade flow.
Busy nodes are left alone and checked again shortly; unavailable or blocked
nodes back off and retry. The backend still repeats its idle/readiness check at
POST time, so a turn or terminal that starts during artifact preparation wins
the race and safely rejects the automatic attempt.


### Session references, communication, and coordination

The shared execution API includes additive `session-references`,
`session-communication`, and `session-coordination` capabilities. All three engines
receive the turn-bound `puppy_session` stdio bridge. Stable references are
`<node_uuid>/<session_id>`; composer mentions also carry the selecting controller's
UUID. References remain valid across node/session renames.

`GET /api/session-links/catalog` lists this runtime's available sessions and named
coverage gaps. `POST /api/session-links/node` is the authenticated node-local
read/search/request/status/cancel surface. `POST /api/session-links/action` exposes
source-owned operations to consoles. A controller opens
`/api/ws/session-links?controller=<uuid>` on each online capable backend to relay
its active turns' requests back through that controller. The channel reuses the
paired API token and certificate pin; nodes never learn other nodes' credentials.

Questions use the native side-question channel when ready, or join the normal
queue as conversational questions. Tasks always follow ordinary queue ordering.
Steer/stop compare the destination's active turn id. Client-chosen request ids are
idempotent and replies identify individual outcomes. Queues remain prompt strings;
a visible request marker ties a prompt to its durable receipt. Requests and finite
DAG workflows survive the requesting turn. A removed/held/restarted destination
is reported explicitly; completed prerequisites alone release dependent steps.

State is stored under exact-format `session_references.*`, `session_inbox.*`,
`session_outbox.*`, and `session_workflow.*` meta namespaces, included in snapshots
without automatic migrations. See the main README for tool behavior and limits.

### Cancellable console operations

Both runtimes advertise `operation-cancel-v1`. Audited requests accept a fresh
`X-Puppy-Operation` identity and expose authenticated
`GET`/`DELETE /api/operations/{id}`. Cancellation stops preparation and drains
cleanup before releasing ownership; an atomic commit refuses cancellation.
The controller forwards the header and cancellation through its ordinary
pinned, authenticated proxy. No persisted state changes are involved.

`engine-upgrade-cancel` adds `DELETE /api/engines/{key}/upgrade` with the exact
observed `{started_at}` from `upgrade_started_at`; `upgrade_stopping` reports
accepted stops. The updater's process group is ended, output retained and the
installed version rechecked. `side-question-cancel` adds
`DELETE /api/sessions/{sid}/ask` with `request_id` and `expected_turn_id`, and
readiness exposes `pending_request_id`. It withdraws only that native side
question. `browser-navigation-stop` accepts `{type:"stop_loading"}` on the
browser viewer socket. See [cancellation behavior](../docs/cancellation.md) for
supported phases, compatibility and tests.
