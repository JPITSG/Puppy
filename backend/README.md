# Puppy headless backend

This directory contains the separately deployable, API-only Puppy runtime. It
shares the console's database, runner and engine drivers at build time, but the
resulting artifact exposes no web GUI, cookie login, settings, or backend proxy.

## Build

```bash
python3 backend/build.py
backend/dist/puppy-backend.pyz --version
```

The build emits `puppy-backend.pyz` plus the stdlib-only
`puppy-backend-launcher.py`. The zipapp contains all Puppy Python code required
at runtime; the stable launcher owns restart health checks and rollback. The
target still needs Python 3.9+, `aiohttp`, and whichever official engine CLIs
it will run. No frontend assets are included.

Run from the source tree without building:

```bash
python3 -m backend.puppy_backend serve --data-dir backend/data
```

## Deploy

Copy both generated files to the remote machine, install `aiohttp` (or use
`requirements.txt`), and run the engine login commands as the same unprivileged
Unix user that will run the service. Initialize the private configuration once.
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
includes a pin. `--disable-tls` retains legacy cleartext HTTP compatibility,
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
supplying a working directory. The backend creates a mode-0700 directory inside
a private namespace under the OS temporary directory (normally `/tmp`), scoped
to both the service user and backend data directory. It advertises this contract
with the `temporary-workspaces` capability, so controllers do not offer it for
older nodes that would ignore the field.

Scratch files survive an ordinary service restart but are deliberately not
durable host data. Deleting the session removes them; a reboot or the host's
temporary-file policy may remove them first. The SQLite session and transcript
remain in `data/`. A missing workspace is reported as `workspace_missing`, and
the reset endpoint—or the next turn—creates a new empty directory, clears the
engine-native session id, and seeds the fresh engine context with a notice that
the old files were cleared. Startup cleanup removes only unreferenced,
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
turns and is cleared only after both the active turn and queue are empty. This
lets a controller show one continuous elapsed time while compensating for clock
differences between the controller and backend. These fields are additive;
controllers can continue to attach older nodes that do not send them.

When a work block becomes idle, session lists, snapshots and `turn_done` also
carry the additive `completion_status`. A controller uses `interrupted` to
retire its activity timer without firing a configured completion command for a
prompt the user stopped. Queued work that continues after a stop remains one
activity block and can still notify when that later work actually finishes.

## Active-turn steering transport

`POST /api/sessions/{sid}/steer` sends an additional text instruction to the
engine turn which is already running. It is intentionally separate from the
ordinary message route: steering never joins the session queue or starts a new
native turn. The shared runner maps it to each pinned CLI protocol (streamed
user input, app-server `turn/steer`, or an active ACP prompt), persists the
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
Older nodes omit the capability, so controllers must not expose the route even
if they happen to run the earlier transport preview.

## Account usage refresh

The headless package stores the same per-node usage-refresh interval as the full
runtime. Set it while pairing or serving with `--usage-refresh-minutes N` (1 to
1440 minutes); use `0` to disable it. Once attached, the controller exposes the
same value in Settings → Usage refresh and can change it through the authenticated
`/api/engines/usage-refresh` endpoint. The node advertises this support with the
`engine-usage-refresh` capability, so older backends remain explicitly disabled
in the Settings UI.

New nodes also advertise `engine-usage-refresh-manual`. The authenticated POST
form of the same endpoint performs one immediate read even when the automatic
interval is disabled. Controllers use it for the compact refresh control beside
a ready Codex status; older nodes omit the capability, so the control is hidden
until they are upgraded.

The refresh is lazy: a due engine-status poll asks supported installed CLIs for
their current read-only account-limit snapshot. It does not start a turn or
consume model tokens, concurrent polls coalesce, and failed attempts are
rate-limited. Codex currently supplies a direct account snapshot; its existing
local rollout record remains the fallback if the account read is unavailable.

## Engine versions and upgrades

Every engine payload reports the installed CLI version, the latest published
version, and whether an update exists. Both sides refresh on their own timers:
installed `--version`/auth probes are cached for five minutes, and the latest
published version is re-checked every six hours (fifteen minutes after a failed
check). A registry outage only makes the latest value unavailable; it never
makes an installed engine unavailable.

Two authenticated routes complete that picture, and nodes advertise both with
the additive `engine-upgrade` capability:

- `POST /api/engines/refresh` forces one immediate installed-version re-probe
  and one latest-release check on this node.
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
different vendor CLIs can update concurrently; every run is bounded by a
fifteen-minute timeout, and its output is captured and truncated rather than
streamed.

It starts in the background and the POST returns immediately, so `upgrade_state`
in the engine payload is the progress signal and `upgrade_result` the outcome -
a reconnecting controller rejoins a run already in flight. Engines are spawned
per turn, so nothing restarts afterwards; the node re-probes the version itself
when the updater exits, because a zero exit status alone does not prove the
version moved.

## OpenCode models

OpenCode is registered in the headless artifact just like it is in the full
runtime. Its status is binary availability only: provider authentication is
model-specific, so a found binary reports `Ready` and an absent one reports
`No binary` rather than attempting one global auth verdict.

An installed node discovers its catalog from `opencode models --verbose` and
serves every reported choice through the ordinary engine `model_options` list.
The controller uses that list directly in new-session and chat model controls;
there is no controller-owned provider list or node-owned model allow-list.
Turns run through `opencode acp`, resume the native session ID, relay tool
approvals/cancellation, and receive the same turn-scoped managed-browser MCP
server when Browser is enabled on that node, plus the shared-terminal MCP
server when terminal support is enabled.

Puppy resolves OpenCode from the service's `PATH` first. It also checks the
official install script's per-user fallback at `$HOME/.opencode/bin/opencode`,
because non-interactive services normally do not source the shell file which
the installer updates. Run the backend with the same `HOME` as the account that
owns OpenCode's installation and provider credentials.

## System prompts

Each node stores its own custom prompt plus conditional remote-workspace,
Browser, Terminal, and spawned-agent guidance. The custom layer is added to
every new model turn the node starts. The Browser layer is added only when
Browser is enabled and the turn receives managed-browser tools. The Terminal
layer is added only when the node offers shared-terminal tools, and its
shipped policy tells models to use those tools only after an explicit user
request; ordinary shell work continues through the engine's normal tools. The
spawned-agent layer accompanies the always-offered spawn MCP bridge and keeps
delegation explicitly user-requested because spawned runs spend real
subscription quota. Active turns keep the prompt with which they started.

## Spawned agents

Every node advertises the additive `spawn-exec` capability: `POST /api/spawn`
starts one non-interactive engine run (engine, optional model/effort,
permission mode, prompt, working directory, hard timeout) after validating the
request against that node's installed engines, and `GET`/`DELETE
/api/spawn/{job_id}` poll or cancel it. Jobs are in-memory, deadline-bounded,
and never part of a snapshot. Engine turns receive a turn-scoped `puppy_spawn`
stdio MCP bridge (targets/spawn/wait/cancel); jobs it starts die with their
turn, approval requests inside a spawned run are auto-denied with an
explanation, and cross-node spawns exist only on the controller, which relays
them over its already-authenticated channels - nodes still never contact each
other, so a session hosted on a backend can spawn only onto its own node. A
running spawned agent also blocks that engine's CLI upgrade, and spawn
requests are refused while the engine's updater runs. A parallel fan-out
(spawn `count`, up to 12) is expanded by the bridge into independent jobs -
a remote fleet is simply that many relayed single starts, so any spawn-exec
node can host one - and `wait`/`cancel` operate on job-id lists with one
shared concurrent budget; each node also caps its total running spawned
agents. The console's composer
offers the request as an "@" mention: a "New spawn" wizard slides through
agent count, node, engine, model, and effort, then inserts the plain-text
directive `@Spawn <an agent|N agents> on <node> using <engine> [<model>]
[at <effort> effort] to
<task>`, whose exact meaning the spawn MCP guidance defines for the engine.

An attached console reads and edits these fields through authenticated
`GET/PATCH /api/system-prompt`. Nodes advertise the additive `system-prompt`
capability, so older backends remain visibly unavailable in the editor rather
than accepting a controller-only setting they would never send. The API limits
each field to 32,768 characters and returns Puppy's shipped conditional texts
so the console can implement Reset to default without embedding second copies.

## Shared terminals

Terminal-enabled nodes advertise `terminal-instances` and `terminal-handoff`
beside the legacy `terminal` capability. A controller creates a node-owned PTY
through `POST /api/terminal/instances`, then attaches xterm.js to its
ID-scoped WebSocket. Four-character Terminal IDs, process lifetime, transcript
replay, and session links therefore survive viewer reconnects; the anonymous
`/api/ws/term` create-on-connect route remains for older controllers.

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

## Durable message queues

Prompts queued behind a running turn are written through to the node's database
on every change, so they belong to the user rather than to the process. A
console connected to a node advertising `queue-pause` may pause any ordinary
queued prompt. The node publishes its indexes in the additive `paused` array;
automatic dequeue skips those prompts while continuing with the next runnable
one. Pending model/effort changes are not separately pausable; configuration
rows encountered before the selected runnable prompt still apply in their
visible order.

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
switch. Model/effort rows also carry the `engine` whose catalog validated
them: a row orphaned by cancelling or reordering away its switch is skipped
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
flight. Older nodes omit the capability and keep their existing local-browser
draft behavior.

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

## Unattended engine updates

A node can install its own engine CLI updates on a schedule. It adds no upgrade
machinery: the scheduler decides *when* to run the same vendor-delegated updater
`POST /api/engines/{key}/upgrade` runs by hand, so an automatic run is the
manual run with nobody clicking. `GET`/`PATCH /api/engines/auto-upgrade` read and
set `{enabled, mode, at}` - `mode` is `now` or `at`, and `at` is a local `HH:MM`.
The node advertises the routes with the additive `engine-auto-upgrade`
capability; older nodes simply keep updating by hand.

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
