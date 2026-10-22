# Timeout settings

Settings → Timeouts edits the selected backend's seven execution/unattended
limits. All values are whole seconds from 0 through 2147483647. Zero means
unlimited for that deadline. Defaults:

| API field | Stored in | Default |
| --- | --- | --- |
| `turn_seconds` | `sessions.turn_timeout` | 7200 |
| `background_wait_seconds` | meta record `background_wait_limit` | 86400 |
| `spawn_runtime_seconds` | `spawn.max_runtime` | 7200 |
| `spawn_idle_seconds` | `spawn.idle_timeout` | 600 |
| `terminal_idle_seconds` | `terminal.idle_timeout` | 900 |
| `browser_idle_seconds` | `browser.idle_timeout` | 900 |
| `vnc_idle_seconds` | `vnc.idle_timeout` | 900 |

Both runtimes advertise the additive `timeout-settings` capability and serve
`GET`/`PATCH /api/timeouts`. The response's `timeouts` object contains complete
`values` and `defaults` maps with the seven API fields, and `max_seconds`.
PATCH accepts a nonempty subset, validates every value before writing any,
saves atomically, and retains the previous values on a failed save - a
failed config.json save puts the background record back as it was. A
backend from before the background task limit answers the other six; the
console keeps them editable, disables that row with "Not offered by this
backend's version." and never sends it the field. The same object rides
engine state snapshots/updates, full-WebUI bootstrap and Settings responses.
Remote editing uses the controller's ordinary authenticated proxy; offline or
incapable backends cannot be edited. Reset fetches the selected backend's own
fresh defaults before applying all of them. No fleet propagation is implicit.

The runner captures both turn limits once per engine attempt. The turn limit
counts elapsed time, including approvals and background work while the model
is still working, but not a wait on engine background tasks after the model
has answered: that pause is bounded by the background task limit alone, and
when the engine wakes the model for a finished task the pause's length is
added to the turn's deadline. When the background task limit runs out, the
wait ends through stdin EOF, the engine stops its tasks and the answer stands
as the result; the model's own check can end the wait sooner when it says the
work is no longer needed. A zero turn limit leaves the turn running until
completion, Stop, shutdown, or another independent failure; a zero background
limit leaves a wait to that check, Stop and shutdown.
The headless CLI's `--turn-timeout` accepts the same values, including zero.

Spawn limits are captured at job creation. An omitted limit uses the executing
backend's setting, including on relayed starts. Explicit `idle_timeout_s` and
`max_runtime_s` still override those defaults, and the ownership-checked live
PATCH can change either limit. Progress renews only a positive inactivity
limit; absolute runtime always counts from job creation. For a disabled limit,
its `*_remaining_s` field is JSON null and the MCP text says unlimited.
Unlimited jobs remain owned by their spawning turn, block engine updates,
and are cancelled when that turn ends. Remote ownership leases and their
renewal/cancellation rules remain active. Old peers accept only their existing
30–7200-second explicit limits; a controller refuses other explicit values
before sending to a peer without `timeout-settings`.

Browser, terminal and VNC idle settings count time without connected viewers;
agent interaction restarts the timer. A settings change cancels the previous
timer and starts a full interval for each running, unviewed instance, or leaves
no timer for zero. Connected viewers prevent unattended shutdown. These are
not an absence-of-output detector: an unattended terminal command may still
be running when a finite timeout stops it. A lapsed VNC timer drops the remote
session but keeps its identity and its tab: reattaching a viewer dials the same
server again, so unlike a terminal nothing is lost.

## Existing installations and backups

The background task limit needs no preparation: it is the optional exact
`background_wait_limit` meta record (`{"format": 1, "seconds": N}`), absent
until first saved and read as 86400 while absent, rather than a config.json
key, so no node's config shape changed for it. Any other shape refuses to
start and is refused in a backup, never repaired. Full-state backups carry it
with the database; an archive from before it restores with the default.

Config and snapshot import require the complete current config shape. No
runtime migration, missing-field defaults, or previous-shape compatibility is
provided. Before applying this persisted-shape change to a running installation,
the operator must manually edit **each in-scope node's private config.json**:

- Add the exact top-level object `"spawn": {"max_runtime": 7200, "idle_timeout": 600}`.
- Add `"idle_timeout": 900` inside the existing `terminal` object, preserving `command`.
- Add `"idle_timeout": 900` inside the existing `browser` object, preserving its other fields.
- Add the exact top-level object `"vnc": {"idle_timeout": 900}`.
- Preserve `sessions.turn_timeout`; its current value must be a nonnegative
  whole number of seconds within the supported range.

All other current config fields must remain intact. These manual edits belong
to deployment/application, not to the isolated task copy's implementation.
New installations initialize the complete shape directly. Current full-state
backups include all six config fields and preserve zero values; old archives missing
these fields are rejected without altering live data. Take a fresh backup
after updating the live configuration.

## Panel previews

The previews use invented demo data. Desktop captures are 1440×900; phone
captures show a 390×844 layout at 2× resolution.

[Desktop, dark](../assets/timeouts-desktop-dark.png) ·
[Desktop, light](../assets/timeouts-desktop-light.png) ·
[Phone, dark](../assets/timeouts-mobile-dark.png) ·
[Phone, light](../assets/timeouts-mobile-light.png)
