# Scheduled prompts

The clock in the sidebar's footer opens **Schedules**: prompts this instance
sends on its own, each on the backend, engine, model, effort and permissions
its schedule names. Think of it as cron for prompts. Every run is a new
conversation on that backend, kept out of the sidebar and opened from the
schedule's history.

The controller (the full WebUI runtime) owns every schedule and its history.
Backends keep nothing about a schedule except a mark on the sessions it starts
there; a backend only needs the additive `session-schedule-runs` capability.

## What a schedule says

| Field | Meaning |
| --- | --- |
| Name | Up to 80 characters on one line; runs are named `<name> · run <n>` |
| Prompt | Up to 20,000 characters, typed in the shared prompt box (text only) |
| Backend, engine, model, effort, permissions | Where and how each run works. An empty model or effort is the engine's own default. This instance checks the choices against its catalog when the schedule is saved; a backend answers for its own when a run starts |
| Workspace | A working directory on that backend, or **Scratch**: a new private empty workspace for each run, deleted with its conversation |
| Repeats | **Once** (a date and time), **Every** N minutes, hours or days from a start date and time, **Every day** at one or more times, **On days of the week**, **On days of the month** (numbers and **last**), or a five-field **Cron expression** (`*`, ranges, lists, steps, month and weekday names, `@hourly`, `@daily`, `@weekly`, `@monthly`, `@yearly`; Vixie cron's day rule: when both day fields are restricted either may match) |
| Ends | Never, on a date (no run after that day), or after a number of runs. A schedule that runs once has neither |
| When a run cannot start on time | **Run it as soon as it can** (the default) or **Skip it** |
| Stop a run after | 0 to 1440 minutes, 60 by default; 0 leaves only the session's own turn timeout |
| Notify me | **Never**, **When a run fails or needs me**, **When the agent flags something**, or **After every run** |
| Also run the completion alert command | Sends the notice through Settings → Completion alerts while alerts are armed |
| Conversations to keep | 1 to 50, 10 by default; older run conversations are deleted |
| Run it on this schedule | The switch on the schedule's card; off, it only runs by hand |

Times are wall clocks in this instance's own time zone (the editor names it),
kept as written - `06:00` stays at six across a change of daylight-saving time.
A daily time the change to summer time skips runs once, an hour later; an
interval keeps its length in minutes. The editor shows the instance's own
answer to "when next?" for the settings as typed.

## How runs happen

One worker on the controller decides when an occurrence is due and starts it:

1. It creates the session on the schedule's backend through the same node API
   a console uses (`POST /api/sessions` with the run's `schedule` mark), in
   process for this instance and over the authenticated node request for a
   backend.
2. It sends the prompt as an ordinary message. A short note follows the
   schedule's own text, visible in the transcript: which schedule and run this
   is, when it started and when the previous run started and how it ended,
   that nobody is watching, and - for **When the agent flags something** - how
   to raise an alert: a last line `NOTIFY: <one sentence>`.
3. It follows the session through the backend's session lists (and reads the
   list itself when nothing has reported for 30 seconds) until its activity
   block ends, then reads the final answer through the node's prompt index and
   keeps up to 4,000 characters of it, the agent's `NOTIFY` line apart.

A run's state is **Starting**, **Working** (or **Waiting for your approval**),
**Finished**, **Failed**, **Stopped**, **Missed**, **Skipped** or
**Conversation deleted** (its session vanished while it worked).

- Only one run of a schedule works at a time. An occurrence that comes due
  while the previous run is still working waits for it (**Run it as soon as it
  can**) or is recorded as skipped (**Skip it**). An occurrence that waits past
  the next one is recorded as missed.
- A backend that is offline, gone or not upgraded holds the occurrence the same
  way, as does a backup or restore in progress.
- Occurrences that came due while Puppy was not running are recorded as one
  missed entry; with **Run it as soon as it can** the newest of them runs, late.
- The time limit stops a run that works too long; **Stop** on the card or in
  the history stops the working run by hand. **Run now** starts a run at once,
  whether the schedule is on or off.
- Deleting a backend turns its schedules off. A restore makes each schedule's
  next occurrence count from the moment of the restore.

A run's conversation is an ordinary session: open it from the history (or the
card while it works) to read every step, answer an approval, or carry on the
conversation. Its chat head shows the schedule and run; that chip opens the
history. A console keeps a run's session out of the sidebar exactly while its
schedule still keeps the conversation: a conversation whose schedule was deleted
(with **Also delete its conversations** unticked) is an ordinary session again.

## Notifications

A notice goes into the notification history (the footer's tray) and appears on
every open console; nothing needs a console open for it to be kept:

- **When a run fails or needs me**: a failed run, a run stopped at its time
  limit, a missed run (once per streak of misses), and a run waiting for an
  approval.
- **When the agent flags something**: those, plus the agent's `NOTIFY` line.
- **After every run**: those, plus every finished run with the first line of
  its answer.

With **Also run the completion alert command**, the same notice runs the
completion alert command: **On success** for a finished run, **On failure** for
everything else. The `{message}` placeholder (`PUPPY_MESSAGE`) carries the
notice's text; `{session}` is the schedule's name. A scheduled run's completion
never fires the completion alert by itself - its schedule decides what is told.

## Routes (full runtime only)

| Route | |
| --- | --- |
| `GET /api/schedules` | Every schedule's public view (as the `schedules` topic), the time zone and the limits |
| `POST /api/schedules` | Create from exactly the settings fields; answers the full view |
| `POST /api/schedules/preview` | `{timing, until, max_runs}` → the next occurrences, or a 400 naming what is wrong |
| `GET /api/schedules/{id}` | The full view (with the prompt) and every run with its answer, newest first |
| `PUT /api/schedules/{id}` | Replace the settings; a changed timing counts from now |
| `PATCH /api/schedules/{id}` | `{enabled}` |
| `DELETE /api/schedules/{id}?conversations=1` | Delete the schedule, and its kept conversations when asked; refused while a run works |
| `POST /api/schedules/{id}/run` | Start a run now |
| `POST /api/schedules/{id}/stop` | Stop the working run |
| `DELETE /api/schedules/{id}/runs/{n}` | Remove one entry and its conversation |

The `schedules` state topic carries the public views on every change and rides
`/api/state`. A view never carries the prompt (only its first line, `excerpt`);
`kept_runs` names the runs whose conversations the schedule keeps.

## Persisted shapes

All three are meta records, validated at startup and on restore, refused rather
than repaired. They need no change to `config.json` on any node.

On the controller, `schedule.<id>` (`id`: 12 lowercase hex characters):

```json
{"format": 1, "id": "…", "name": "Log review", "prompt": "…", "enabled": true,
 "backend": 0, "engine": "claude", "model": "", "effort": "",
 "permission_mode": "default", "workspace_kind": "directory", "cwd": "/var/log",
 "timing": {"kind": "daily", "times": ["06:00"]}, "until": "", "max_runs": 0,
 "late": "run", "limit_minutes": 60, "notify": "agent", "alert_command": false,
 "keep": 10, "created_at": 0.0, "updated_at": 0.0, "next_at": 0.0,
 "pending_at": 0.0, "scheduled_runs": 0}
```

`timing` is one of `{kind: "once", date, time}`, `{kind: "interval", minutes,
date, time}`, `{kind: "daily", times}`, `{kind: "weekly", days (1-7, Monday
first), times}`, `{kind: "monthly", days (1-31, -1 for the last day), times}`
and `{kind: "cron", expr}`; dates are `YYYY-MM-DD`, times canonical `HH:MM`,
lists sorted without repeats. `next_at` and `pending_at` are 0 while the
schedule is off.

`schedule_runs.<id>` holds at most 50 runs, oldest first, at most one of them
starting or working:

```json
{"format": 1, "next_run": 3, "runs": [
  {"n": 1, "trigger": "schedule", "due_at": 0.0, "started_at": 0.0,
   "finished_at": 0.0, "backend": 0, "session": 12, "state": "ok", "reason": "",
   "answer": "…", "flag": "", "notified": false, "kept": true}]}
```

On every node, a run's session carries `session_schedule.<sid>`:

```json
{"format": 1, "schedule": "…", "name": "Log review", "run": 1}
```

It is deleted with its session, and every session payload carries it as the
additive `schedule` field `{id, name, run, waiting}`.

Schedules and their histories are in the controller's database, so backups
carry them; a run's conversation is backed up with the node that holds it.

## Tests

`python3 tests/schedules_test.py` covers the timing (daylight-saving changes,
cron), the exact shapes and their refusals, the mark on both runtimes, runs
through the real runner with a fixture engine on this instance and on a backend
reached through the node requests, and the routes. `node
tests/schedules_ui_test.js` runs the sheet, the history and the editor against
the fake DOM; the console browser suite's `--schedules-only` lane measures them
in real layout.
