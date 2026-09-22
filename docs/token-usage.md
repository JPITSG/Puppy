# Token usage

Every node keeps a ledger of the tokens its engines used, and the console
draws the ledgers of the instance and its backends side by side. The chart
button in the sidebar's footer, between the bell and the notification tray,
opens the **Token usage** sheet.

## What is counted

A row is written the moment an engine run ends, on the node that ran it:

- **A session's turn**: the runner's persisted `result` event, with the
  engine and the model the session was using. A turn that waits on
  background work is counted once, when its folded result lands, with the
  tokens of every wake-up in it. A compaction or other tool turn counts like
  any turn.
- **A spawned agent**, when its job ends: attributed to the session whose
  turn started it when that session is on the same node, otherwise (a job a
  controller relayed for a session elsewhere) to no session.
- **A session-title job**, when it ends, attributed to no session.

A turn whose engine breaks its tokens down by model - Claude Code reports the
models its subagents and helper calls ran on in `modelUsage` - is split into
one row per model, all under the one turn. Nothing is ever derived from a
transcript at read time: deleting or renaming a session never rewrites what
it used.

The counts are the drivers' usage vocabulary (see `drivers/base.py`) made
disjoint, so they add up:

| Count | What it is |
|---|---|
| input | fresh input, read without the cache |
| cache read | input served from the prompt cache (Claude's `cache_read_input_tokens`, Codex's `cached_input_tokens`) |
| cache write | input written into the cache (Claude's `cache_creation_input_tokens`) |
| output | everything generated |
| reasoning | the part of the output spent thinking, where the engine reports it |

The cost is the engine's own estimate where it reports one (Claude Code's
`total_cost_usd`, per model when it splits one) and nothing otherwise. It is
an estimate, not a bill: a subscription is not charged by it.

## The ledger

One exact-shape meta record per UTC day, `token_usage.<YYYY-MM-DD>`:

```json
{"format": 1, "rows": [[ref, at, session, source, engine, model,
                        input, output, cache_read, cache_write, reasoning, cost]]}
```

- `ref` is `turn:<session>:<seq>` for a turn (the result event's own
  sequence number) or `spawn:<job>:<created>` for a spawned agent or title
  job, at most 200 characters.
- `at` is the run's end in epoch seconds, inside the record's own UTC day.
- `session` is the session's id, or `null` for a run that belongs to none.
- `source` is `turn`, `spawn` or `title`.
- `engine` is the engine's key (1 to 64 characters); `model` is the model
  the engine reported serving, else the one the session or job asked for (at
  most 200 characters, empty for the engine's default).
- The five counts are whole numbers from 0, and at least one of input,
  output, cache read and cache write is not 0; `cost` is `null` or a finite
  number from 0.
- `rows` is not empty, is ordered by `at`, then `ref`, then `model`, and
  holds a `(ref, model)` pair once.

A ref already present in its day is never counted again, whoever reports it
first; one immediate transaction covers every day a write touches. Records
are validated at startup and when a backup is restored, and a record that is
not exactly this shape is refused - the node does not start, the archive is
not restored - never repaired. There is no manual preparation: a node that
has counted nothing simply has no records.

The ledger lives in the node's database. The instance's own ledger travels
with its backup like the transcripts; a backend's ledger is that backend's
data, outside the instance's backup like the rest of it.

## The route

Both runtimes serve `GET /api/token-usage?since=&until=&step=&offset=` behind
the additive `token-usage` capability:

- `since` and `until` are epoch seconds, `since` above 0 and below `until`,
  at most five years apart (`until` defaults to now, `since` to thirty days
  before it).
- `step` is `3600` (hours, which a console folds into its own local days
  exactly) or `86400` (days, starting at `offset` seconds from UTC midnight -
  whole minutes within fourteen hours).

Anything else is a `400`; a ledger record of the wrong shape is a `500`. The
answer is:

```json
{"ok": true, "since": 1787443200, "until": 1790035260, "step": 3600, "offset": 0,
 "first_at": 1785024000.5,
 "columns": ["at", "engine", "model", "input", "output", "cache_read",
             "cache_write", "reasoning", "turns", "cost"],
 "buckets": [[1790031600, "claude", "claude-opus-5-5[1m]", 12, 340, 5600, 78, 0, 1, 0.0412]],
 "totals": {"input": 12, "output": 340, "cache_read": 5600, "cache_write": 78,
            "reasoning": 0, "cost": 0.0412, "turns": 1},
 "sessions": [{"id": 4, "name": "Harbor dashboard", "color": "#e0784f",
               "deleted": false, "parent": null, "parent_name": "",
               "engines": ["claude"], "last_at": 1790033412.25,
               "input": 12, "output": 340, "cache_read": 5600, "cache_write": 78,
               "reasoning": 0, "cost": 0.0412, "turns": 1}],
 "session_count": 1,
 "jobs": {}}
```

Every amount object - `totals`, each session, each of `jobs`' `spawn` and
`title` entries - carries the five counts, `cost` and `turns`.

`buckets` sums the rows by time bucket, engine and model; `totals` sums them
all; `sessions` lists the 20 heaviest sessions (a deleted session by its id,
a task with its Main's name) and `session_count` how many sessions used any
tokens; `jobs` sums the runs that belong to no session, by source. `turns`
counts turns only - a spawned agent or a title job is a run of its own,
counted with its job entry - and a turn split by model is still one turn.
`first_at` is the earliest row the node keeps, whatever the span. The read is
never counted as a mutation.

## The sheet

The console asks the instance and every backend at once for the chosen
range. A backend that cannot be reached, or that is too old to keep a
ledger, is named under the range with the reason rather than silently left
out; the sheet reports an error only when no node answered.

- **Range**: 7, 30 or 90 days, each from its first local midnight to now and
  drawn a day per column - from the first day any node counted, when that is
  later, since nothing before it was counted - or **All**, up to five years of the ledger drawn
  by the day while it holds 120 days or fewer, by the week (from Monday) up
  to two years, and by the month beyond.
- **Tiles**: the total, the fresh input, the cache (read and written), the
  output (with its reasoning part), the turns and how many sessions they were
  in, and the estimated cost when any engine reported one.
- **By**: the columns are split per **Backend**, **Engine** or **Model**.
  A model is one series per engine under the name its engine's catalog
  gives it - a requested alias, the id the engine resolved it to, and one
  model two backends' catalogs file under different rows are one series -
  or under its id where the catalog does not name it, and "*Engine*
  default" for a turn on the engine's default model.
  **Tokens** counts **All** of them, or only the input, the cache or the
  output: the chart, its readout, the breakdown and the sessions follow it,
  while the tiles always show every kind.
- **The chart** is stacked columns on the palette validated for the
  console's two surfaces. A series keeps its colour while the page lives,
  across ranges and measures; an engine always wears its own (Claude's
  orange, Codex's teal, OpenCode's violet); past seven series the smallest
  fold into *Other*. The line above the plot reads the range - its total and
  its busiest column - and hovering, tapping or the arrow keys (Home, End,
  Escape) read one column at a time. Every series keeps its place in that
  line whatever the column holds - a series the column did not use reads
  nought in the help colour - so reading the chart never moves it.
- **Breakdown** is the chart's table: every series with its total, its share
  and its input, cache and output (on a phone, under its name).
- **Sessions** lists the twelve heaviest sessions across the nodes with
  their backend and engines, a task with its Main's name; a press opens a
  session the sidebar lists, while a deleted one is named as such and opens
  nothing. Session titles and agents spawned for another backend's sessions
  close the list as runs of their own.

The browser keeps only the three choices - `puppy.usage.range`,
`puppy.usage.by` and `puppy.usage.measure`, exact strings or the default.
The sheet is a dialog on the browser's history: Back closes it and Forward
opens a fresh one, read anew.

## Tests

`python3 tests/token_usage_test.py` covers the vocabulary, the day records,
the persisted shape, the report, the route on both runtimes and fixture
turns through the real runner. `node tests/token_usage_ui_test.js` runs the
sheet against the fake DOM, and the console browser suite's lane runs it in
a real browser on both themes and a phone. No engine, network or quota.
