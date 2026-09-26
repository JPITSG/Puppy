# Engine messages

Puppy uses the readable messages supplied by an engine and translates known
protocol codes where no display text is provided. The drivers own these
meanings; the console owns engine labels, local clock formatting, toasts and
notification history. No model calls, terminal-output scraping, network lookups
or extra dependencies are involved in formatting a notice.

## Coverage

- **Claude Code:** `rate_limit_event` distinguishes `allowed`,
  `allowed_warning` and `rejected`. A warning names the reported quota window
  when known, shows a valid utilization percentage, and carries the reported
  reset timestamp. Extra usage availability and use remain distinct from the
  included allowance. An unknown window is simply a usage limit; an unknown
  status becomes a neutral usage update, or uses supplied readable text.
  `system/notification` uses its text, and `system/informational` uses its
  content (`info` stays a transcript note). `system/api_retry` updates activity
  with the supplied attempt and delay. Result errors prefer the native result
  text or `errors` array, then a known result-code explanation.
- **Codex:** `warning` and `configWarning` preserve the engine's message or
  summary/details. Thread-targeted warnings for another thread are ignored.
  Error messages preserve their wording, with known `codexErrorInfo` codes as
  fallbacks. `willRetry` keeps a recoverable error in the activity line, subject
  to the existing startup grace; a failure remains an error. Rate-limit notices
  require an explicit `rateLimitReachedType`: a percentage alone does not
  establish a warning, a refusal or recovery. Window durations come from the
  payload, not an assumed plan. Missing metadata in a rolling update does not
  clear a previous notice or establish that usage is allowed again.
- **OpenCode:** ACP errors use the top-level readable `message`; `error.data`
  stays out of display text. Missing messages use documented ACP/JSON-RPC error
  categories or a general failure description. Stop reasons distinguish
  cancellation, refusal, response length and request limits. An unfamiliar or
  missing stop reason does not claim successful completion. The current ACP
  adapter does not forward every internal warning/retry update; Puppy does not
  invent notices for information that was not sent.

Missing or malformed optional figures are omitted, never converted to zero.
Reset timestamps are rendered through `fmtStamp` in the browser's timezone
and the controller's clock format. Past or invalid reset times are omitted;
reaching a reset time never implies recovery. Notices are plain text, including
vendor messages containing markup. Existing notification history is unchanged;
previously recorded text is not rewritten.

Each turn remembers at most 64 notice signatures. An identical native notice
is not emitted repeatedly in that turn. Quota signatures identify the reported
state/window/reset and, where provided, warning threshold: another percentage
sample alone is not another warning. This does not add a persisted quota state
machine. Notices emitted again in later turns use the usual counted toast and
history behavior.

## Shared runtime contract

`puppy/drivers/messages.py` supplies bounded plain text, number validation and
notice coalescing. Public code-to-meaning tables live in each engine's driver.
Unknown codes can be recorded at debug level without copying arbitrary vendor
diagnostic objects or private error bodies.

The runner on both full and headless nodes accepts these additive actions:

```text
{a:"notice", notice:{text, tone, resets_at?}}
{a:"rate_limit", info:<native sample>, notice:<display notice or null>}
```

The first becomes a session WebSocket `engine_notice` frame with `engine` and
`notice`. The second retains the existing `rate_limit` frame and native `info`,
with the additive `notice` field; explicit `null` means no toast. The raw quota
meta record and account monitoring keep their existing role and shape. Display
payloads pass through the runner's existing workspace-path presentation.

The console handles both through `showEngineNotice` and the existing
`toast`/history path. A protocol-2 node predating this addition can still send a
raw rate-limit status: the updated console shows a general usage update without
exposing or guessing the code. Upgrade that node to receive the detailed text.
No protocol bump, database migration, configuration or snapshot change is needed.

## Model substitutions

Both runtimes accept `{a:"model", model, note?}` for the model serving the main
conversation. The driver compares requested aliases with reported model IDs.
Claude Code ignores a subagent's `parent_tool_use_id` messages and synthetic
model IDs; its `system/model_fallback` and `model_consent_fallback` wording is
carried as `note` when that model reports, even when the catalog considers its
name equivalent to the previous model. Codex's `model/rerouted` reports the
destination model through the same action.

Session payloads add `model_substitute`: `null`, or
`{requested, served, baseline, note}`. It is live, in-memory state belonging to
the session's engine, requested model and `last_model`; changing those values
or restarting the backend clears its applicability. With an empty requested
model, the turn's first unqualified model report supplies `baseline`.
Transitions arrive through `session_meta`, and a late socket attachment gets
the same state in its snapshot. Older nodes can omit the field.

Persisted `info` events mark each transition as `model_switch`, with `state`
`substituted`, `resumed` or `changed`. A substitute's event carries `engine`,
`requested`, `baseline`, `served`, `note` and readable `text`; a resumed event
carries `engine`, `requested`, `served` and `text`; an ordinary change carries
`engine`, `from_model`, `to_model` and `text`. Older text-only events still render.
Before the final result, `model_substituted` summarizes `engine`, `requested`,
`baseline`, `models`, `throughout`, `note` and `text` (plus `tool` for an engine
tool turn). Model reports accumulate across retries of the same prompt.
`throughout` is false if any report served the requested model, including when
the turn finished on a substitute. Returning to the requested model at the
start of a later prompt clears the live warning without a resumed card.

The console colors the current model name and its corresponding Model control
amber, with switch and summary cards in the transcript. A queued selection
retains its pending appearance. `tests/model_substitute_test.py` covers both
runtimes, `tests/model_substitute_ui_test.js` covers rendering, and the console
browser suite's `--model-substitute-only` lane checks the socket, colors,
wrapping and reconnect behavior on desktop and phone in both themes.

## Checking engine upgrades

The fixtures in `tests/engine_messages_test.py` cover Claude Code 2.1.282,
codex-cli 0.157.0 and OpenCode 1.18.32. They exercise native frame parsing, added
fields, unknown codes, malformed optional values, retry/failure distinctions,
extra usage, bounded repetition, and real process-to-WebSocket delivery on both
Puppy runtimes. `tests/engine_messages_ui_test.js` exercises the real session
receiver and toast/history code, including safe text and reset formatting.

When an engine changes, compare its published types against these fixtures and
add cases for changed semantics. For Codex, export the installed binary's own
schema with `codex app-server generate-json-schema --out <scratch-directory>`;
inspect the warning/error notifications and rate-limit types. For OpenCode,
check ACP's negotiated protocol and the versioned OpenCode ACP adapter, not its
different HTTP event stream. For Claude Code, check the SDK message types and
the CLI's actual stream-json fields. Added fields and vendor wording need no
mapping change; an undocumented new code cannot safely be assigned a new
meaning automatically and gets the appropriate general description.

Primary references:

- [Claude SDK rate-limit definitions](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/types.py)
- [Published TypeScript SDK message types](https://app.unpkg.com/@anthropic-ai/claude-agent-sdk@0.3.185/files/sdk.d.ts)
- [Codex App Server protocol and schema generation](https://learn.chatgpt.com/docs/app-server)
- [ACP schema](https://agentclientprotocol.com/protocol/v1/schema)
- [OpenCode 1.18.32 ACP errors](https://github.com/anomalyco/opencode/blob/v1.18.32/packages/opencode/src/acp/error.ts)
- [OpenCode 1.18.32 ACP event forwarding](https://github.com/anomalyco/opencode/blob/v1.18.32/packages/opencode/src/acp/event.ts)
