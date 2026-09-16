# Session titles

A session or task started without a name can be titled by a model. Settings →
Session titles holds the switch, the backend, engine, model and effort that do
the naming, and the prompt they are given; the New session and New task dialogs
carry the choice for each one, on by default while the feature is configured
and yielding to a typed name, which is never replaced.

## What happens

1. A session is created unnamed with the choice on. The backend that holds the
   session records the intent - the session is *armed*.
2. Its first message is sent. As it always has, the message's first line
   becomes the session's name at once, so the sidebar never shows an unnamed
   row. The armed record becomes a *request* carrying a bounded copy of the
   message (4,000 characters; attachment marker lines keep their file names,
   not the backend's private upload paths) and the placeholder name.
   A task is named at creation from its prompt's first line, so a task created
   with the choice on records its request right there.
3. The controller sees the request - immediately for its own sessions, and on
   the next sessions payload it receives from a backend (its state stream, or
   the reads a console polls through the proxy) - and runs one title job on
   the chosen backend: a spawned-agent run of the chosen engine, model and
   effort in an empty private directory (`data/titles/`, so no agent notes and
   no repository are read), without the node's system prompt, bounded by a
   60-second silence limit and a 180-second runtime. The prompt is the saved
   text with the message in place of `{message}` (or after the text, when the
   placeholder is absent).
4. The answer is cleaned into a title - the first line that is not a
   preamble, without a `Title:` label, quotes, emphasis or a trailing period,
   cut on a word to the 80-character name width - and settled on the
   session's backend. The title lands only while the session still carries
   its placeholder: a name typed meanwhile is kept. A title that does not
   come (a refusal, no answer, a timeout) keeps the placeholder, clears the
   request, and raises one warn toast in every open console; the model is
   never asked again for it.

Turning the switch off, or leaving the engine unchosen, makes the feature
inert: requests that are still waiting are settled without a title. A backend
that cannot be reached - the session's or the title model's - keeps the
request and is retried by the controller's sweep; a decided verdict is kept
until the session's backend has taken it, so an unreachable node is told
again rather than the model asked twice. One job runs per session at a time.

While a request is pending, the session's name shimmers in the sidebar, the
tab strip and the task strip - a band of light through dimmed text, held
still and dimmed under reduced motion.

## Settings

The **Session titles** card:

- **Enabled** applies immediately, like the completion-alert switch.
- **Run on** lists this instance and every backend advertising both
  `spawn-exec` and `session-titles`; a backend without them is disabled with
  "upgrade to enable".
- **Engine** lists the chosen backend's engines, with the ones it does not
  have installed disabled; **Model** and **Effort** are that engine's catalog
  and the chosen model's efforts, with Custom… for a model the engine does not
  offer, exactly as in the engine-defaults editor. The choices are saved as
  typed and validated by the backend that runs the job, which refuses a model
  or effort its engine does not offer.
- **Prompt** is the instruction the model receives, with **Reset to default**.
  Up to 4,000 characters; the default asks for the title alone, at most six
  words, without tools.
- **Try it** titles the sample message with the values entered on the card,
  saved or not, even while titles are off, and shows the title, the model that
  answered and how long it took. It is cancellable like any long operation.
- **Save** persists the backend, engine, model, effort and prompt together;
  the switch is never part of a save.

## Persisted configuration

Every full instance and headless backend requires exactly this `titles` section
in its private `data/config.json` (or the data directory selected by
`PUPPY_DATA`), whether or not the node ever runs a title job:

```json
"titles": {
  "enabled": false,
  "backend": 0,
  "engine": "",
  "model": "",
  "effort": "",
  "prompt": "Write a title for a coding session that begins with the message below. Reply with the title alone: at most six words, sentence case, no quotes, no trailing period and no explanation. Do not use any tools.\n\n{message}"
}
```

`backend` is a backend id (0 for this instance); `engine` is empty or one of
`claude`, `codex`, `opencode`; `model` and `effort` are canonical text of at
most 256 characters (empty means that engine's default); `prompt` is non-empty
text of at most 4,000 characters without surrounding whitespace. Fresh
installations initialize this shape. Before deploying this change to an
existing runtime, add the section to each runtime being upgraded - including
headless nodes, where it is unused - by hand. Runtime loading and backup import
reject a config without it; there is no automatic conversion. Newly exported
backups carry the section, and failed saves and restores retain the prior
values.

The per-session record is the exact `session_title.<sid>` meta row on the
backend that holds the session:

```json
{"format": 1, "state": "armed", "text": "", "placeholder": "", "requested_at": 0}
{"format": 1, "state": "requested", "text": "<the first message>", "placeholder": "<its first line>", "requested_at": 1789555000.02}
```

It is written when a session is created armed, replaced by the request when
the first prompt arrives (or at a task's creation), deleted when the request
is settled and when the session is deleted, validated at startup and on
snapshot restore (a record must belong to an existing session and match one
of the two shapes exactly), and covered by backups with the rest of the
database. Nothing else is persisted: a title job leaves nothing behind, and
the controller's memory of jobs, retries and verdicts is rebuilt from the
records after a restart.

## Wire contract

Nodes advertising the additive `session-titles` capability:

- accept `auto_title: true` on `POST /api/sessions` and on task creation
  (`POST /api/sessions/{sid}/tasks`); with a `name` supplied it is ignored;
- carry the additive `auto_title` on every session payload: `null`, or
  `{"state": "armed"|"requested", "requested_at": <seconds>}` - never the text;
- serve `GET /api/sessions/{sid}/title` (the record with its text, `404`
  without one) and `POST /api/sessions/{sid}/title` with `{"requested_at",
  "name"}` or `{"requested_at", "error"}`, answering `{"ok": true, "applied",
  "name"}` - `409` for a request no longer pending or a stale `requested_at`;
- serve `POST /api/titles/jobs` with `{"engine", "model", "effort", "prompt",
  "wait_s"}`, starting one title job and answering `{"ok": true, "job"}` in the
  spawn job payload shape; the existing `GET`/`DELETE /api/spawn/{job_id}`
  poll and cancel it.

The controller alone serves `GET`/`PUT /api/titles` (the settings, with
`default_prompt`, `placeholder` and `max_prompt_chars`), `POST
/api/titles/toggle` (`{"enabled"}`), and the cancellable `POST
/api/titles/test` (`{"backend", "engine", "model", "effort", "prompt",
"message"}` → `{"ok": true, "title", "answer", "elapsed_s", "model"}`). The
public state rides `/api/state` and the `titles` topic of the updates stream
as `{"enabled", "configured", "backend", "engine", "model"}`, and a failed
title is one `{"type": "toast", "level": "warn", "text"}` frame on the same
stream.
