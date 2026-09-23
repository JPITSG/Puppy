# OpenCode compaction

OpenCode's **Compact context** action uses native compaction while ordinary
prompts use ACP. The shared composer, authenticated `session-tools` route on
both runtimes, engine-tagged queue rows, duplicate suppression, stop control
and turn timeout apply unchanged. OpenCode does not offer Undo in Puppy.

## Native contract

The executable contract was tested on OpenCode **1.18.30** with an isolated
loopback model. The public
[summarize endpoint](https://opencode.ai/docs/server/#sessions) is
`POST /session/{id}/summarize` with the selected `providerID`, `modelID` and
`auto: false`. It runs OpenCode's compaction agent without coding tools or an
automatic continuation. A configured compaction-agent model override remains
native behavior. A custom command named `compact` cannot intercept this API.

`opencode_compact.py` starts a per-turn `opencode serve` on `127.0.0.1`, with
mDNS disabled, port `0`, and a fresh password in the child's environment.
It accepts only the CLI's explicit loopback readiness report. The HTTP client
does not use environment proxies or follow redirects. A fixed, stdlib-only
supervisor watches the runner's stdin pipe and reaps the detached process group
on EOF, even after a hard-killed owner or a vendor launcher leaving children.
The same command works from the backend zipapp without importing Python
modules from the session's working directory. No server is kept between turns.

Preflight checks the native session ID, working directory, absence of a pending
native revert, and existing messages through the public API. A saved explicit
model is split at its first `/`; the engine-native Default is read from the
native session itself. No provider or model is built into Puppy.

The summarize HTTP boolean is not an outcome: OpenCode 1.18.30 can return
`true` for a provider error, cancellation, or an empty summary. After the call,
Puppy pages `GET /session/{id}/message` back to the preflight boundary and
requires exactly one new manual compaction request and its one assistant
summary, with matching parent/request-model identities, no native error,
`finish: stop`, a completion time, no tool parts, and nonempty text.
Missing, malformed or ambiguous evidence fails verification. A lost POST
reply is never retried; a committed summary can still be verified by reading
the messages. The summary's own token counters are reported when valid;
its input count is not displayed as the reduced context size.

Readiness is bounded to 20 seconds, ordinary API requests to 10 seconds,
response bodies to 8 MiB, and verification to eight pages of 16 messages.
Large or changed native responses fail explicitly. Summarization itself uses
the configured session turn timeout; Stop requests native `/abort`, bounded
to two seconds, with process-group cleanup as the backstop.

## Recovery

Immediately before the POST, the transport requests an acknowledged runner
checkpoint. The runner commits an empty native session ID before acknowledging;
only a verified successful result restores the original ID. A crash cannot
therefore cause Puppy to resume an incomplete or empty summary on restart.
The existing empty-ID and held-queue contracts provide recovery without a new
database or queue shape.

An observed failure after that checkpoint holds all queued prompts and
changes, records a context-reset notice, and leaves the next explicitly sent
prompt to start a fresh native session. The handoff uses Puppy's existing
transcript limits (up to 400 events and 16,000 characters, preserving the start
and recent end when shortened); it is not a lossless reconstruction of all
native context. Native history and the displayed transcript are retained.
After a process crash, startup restores queued work as held and the empty
native ID triggers the same handoff. A preflight failure occurs before the
checkpoint and preserves the native ID.

## Checks

- `python3 tests/opencode_compact_test.py`: authenticated HTTP fixtures,
  selected/default models, command setup, outcome verification, pagination,
  response bounds, lost replies, concurrent sessions, cancellation, timeout,
  process death, runner cancellation, durable checkpoint/queue recovery, owner
  death with a stubborn grandchild, and both runtime API surfaces.
- `python3 tests/opencode_compact_live_test.py --binary /path/to/opencode`:
  opt-in installed CLI probe, entirely isolated from account/config/history,
  using an invented conversation and local model. Exercises successful, failed,
  empty and cancelled compaction, custom-command collision, compaction-model
  override, and subsequent ACP resume or transcript-seeded recovery.
  Add `--package /path/to/puppy-backend.pyz` to exercise the packaged runtime.
- `python3 tests/backend_test.py`: existing engine/queue contracts and the
  built backend package. `node tests/live_controls_ui_test.js` covers the
  composer's capability-driven action availability.
