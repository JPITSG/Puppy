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
  no draft, config, database or backup format changes are needed.
- Internal browser bridge aliases and the obsolete engines-response `user`
  alias are removed.

This cleanup preserves transaction rollback, interrupted-operation recovery,
offline retry, historical transcript rendering, and external engine/Chromium
capability handling. Those remain part of normal operation.

Scratch promotion is additive `workspace-move`: both execution runtimes serve
`POST /api/sessions/{sid}/workspace/move` with exactly `{destination: string}`
and return `{ok: true, session}`. See the [backend contract](../backend/README.md).
No persisted shape changes: the existing session changes from `temporary` to
`directory`; the transcript records a `workspace_move` info event.
