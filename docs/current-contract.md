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

## Runtime APIs and console

- Terminal and browser viewers always use identified instances. Anonymous
  `/api/ws/term` and singleton `/api/ws/browser` routes are removed. Terminal
  creation uses `command`; the anonymous route's `cmd` alias is rejected.
- Durable completion records drive notifications. Browser-reported completion
  `/api/notify/fire` and its deduplication cache are removed.
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

Scratch promotion is additive `workspace-move`: both execution runtimes serve
`POST /api/sessions/{sid}/workspace/move` with exactly `{destination: string}`
and return `{ok: true, session}`. See the [backend contract](../backend/README.md).
No persisted shape changes: the existing session changes from `temporary` to
`directory`; the transcript records a `workspace_move` info event.

The sidebar's Git mark is additive `session-git`: every session payload carries
`git` (`null` until the node has looked, then a `repo`/`checked_at` record, with
`error` when the directory could not be read; a repository also carries the
additive `changes` and `unpushed` counts, or `null` for both with `error`
when `git` would not read its state), both execution runtimes serve
`POST /api/sessions/{sid}/git/refresh`, and the node's `git_check_minutes` timer
rides the existing `timer-settings` payload. The node re-checks a directory
when a prompt finishes in it (never a task's), when a task's changes are
applied to it, and when its agent notes are written. A console reads a node
without the capability as before: no mark, and its six timers still editable;
a node from before the counts answers a repository without them, which is a
plain repository, never the warn tone. Nothing is persisted beyond the timer
value; nothing changes a backup shape except that timer, and no availability
verdict moves.

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

Cancellation is additive: `operation-cancel-v1` covers audited preparation
requests and authenticated operation controls; `engine-upgrade-cancel`,
`side-question-cancel` and `browser-navigation-stop` cover their native running
operations. See [the cancellation contract](cancellation.md). Existing persisted
shapes are unchanged; a commit boundary is never presented as rolled back.
