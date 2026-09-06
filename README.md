<p align="center">
  <img src="assets/desktop-dark.png" alt="Puppy console on a desktop: session sidebar, running task tabs, a transcript with tool cards, a compact composer, and a full-history search pane" width="900">
</p>

<h1 align="center">Puppy 🐾</h1>

<p align="center"><strong>One persistent web console for every coding agent you run.</strong><br>
Drive <b>Claude Code</b>, <b>Codex</b> and <b>OpenCode</b> from your browser, from your phone, across every machine you own.</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#the-tour">The tour</a> ·
  <a href="#multi-machine">Multi-machine</a> ·
  <a href="#settings">Settings</a> ·
  <a href="#data-privacy-and-security">Security</a> ·
  <a href="#tests">Tests</a> ·
  <a href="#license">License</a>
</p>

---

Puppy runs the official `claude`, `codex` and `opencode` CLIs for you, turns their
raw event streams into one clean, searchable transcript, and keeps that transcript
and every session forever in its own SQLite database. Close the laptop, open your
phone, and the same conversation is waiting for you, with your half-typed prompt
still in the box.

There is no build step, no npm, no daemon besides Puppy itself: Python 3.9+,
`aiohttp`, and the agent CLIs you already have.

## Why Puppy

- **Every engine, one console.** Claude Code, Codex and OpenCode sessions sit side
  by side. A session can switch engines at any time and the new engine picks up
  the story from a transcript handoff.
- **Talk to the agent while it works.** Steer a running turn, ask it a side
  question it answers without derailing its work, queue the next prompts, approve
  or deny tool calls, all from the same box.
- **Tasks in parallel.** Spin off tasks that work in isolated Git clones of your
  project, review their diffs, and apply the winners to your working tree.
- **A fleet, not a box.** Pair other machines as backends, run sessions there,
  attach shared terminals and managed browsers on any of them, and let agents
  delegate one-shot jobs or talk to other sessions across the fleet.
- **Nothing gets lost.** Durable message queues, drafts that follow you between
  devices, full-history search, and one-click backup and restore.
- **Type together without losing your place.** On backends with typing presence,
  the chat shows when another console is typing. Concurrent edits keep your
  text, caret, and attachments in place; **Review drafts** lets you compare
  your local version with the shared one, keep editing, use the shared draft,
  or share yours. A conflicting local draft survives reloads in that browser's
  draft journal. Sending it submits your version and preserves a different
  shared draft. The indicator stops after a pause or disconnect and respects
  reduced motion.
- **Built for the phone.** A responsive layout, a swipeable sidebar drawer, and
  light and dark themes. Overflowing tab and chip strips fade their contents at
  the edges while surrounding borders stay visible. Menus close when you tap or
  move focus elsewhere, including when you open the sidebar.

<p align="center">
  <img src="assets/mobile-dark.png" alt="Puppy on a phone in dark mode, showing a transcript with tool cards and the composer" width="270">
  &nbsp;&nbsp;
  <img src="assets/mobile-light.png" alt="Puppy on a phone in light mode, showing the same transcript" width="270">
  &nbsp;&nbsp;
  <img src="assets/mobile-dark-sidebar.png" alt="Puppy on a phone with the session drawer open, listing demo projects and the engine status footer" width="270">
</p>

## Quick start

```sh
git clone <repository-url> puppy
cd puppy
./run.sh                       # listens on http://127.0.0.1:10888
```

Open `http://127.0.0.1:10888` on the host and create the administrator account.
Fresh installs listen on loopback only; move the listener to another address
from Settings once the account exists. Sign in to the engines with their own
tools (`claude login`, `codex login`, OpenCode's provider authentication) as
the user that runs Puppy.

**Requirements**

- Linux with Bash.
- Python 3.9 or newer with `aiohttp`. No other Python packages.
- One or more of the `claude`, `codex` and `opencode` CLIs on the service's
  `PATH` (OpenCode is also found at `$HOME/.opencode/bin/opencode`).
- Git, if you want to use Tasks.
- Optional: a Chromium or Chrome binary (major version 112 or newer, on `PATH`
  or named by `PUPPY_BROWSER_BIN`) for managed browsers.

`run.sh` uses `python3` from `PATH`; set `PUPPY_PYTHON` to an absolute
interpreter path to use a specific virtual environment. Run it under whichever
service manager your host uses.

## The tour

<p align="center">
  <img src="assets/desktop-light.png" alt="The same desktop console in the light theme" width="900">
</p>

### Sessions that live as long as you want

A session is a working directory plus an engine, a model, a reasoning effort
and a permission mode. Puppy spawns the engine CLI for every turn, resumes the
engine's own native session by id, and records everything the engine says as a
normalized transcript: prompts, replies, thinking, tool calls with their results,
side questions, engine switches and a result line for every turn with elapsed
time, tokens in and out, and, when the engine reports it, how full the model's
context is.

- **Three workspaces to choose from.** Point a session at a project directory,
  start it in a private disposable **Scratch** workspace with no folder to
  choose, or work on a project that lives on another backend
  (see [Remote workspaces](#remote-workspaces)).
- **Model, effort and permissions per session.** Each engine's model catalog
  and per-model effort levels are discovered from the CLI itself, never
  hard-coded. Claude offers its aliases and effort levels, Codex its models
  and reasoning efforts (plus **Fast mode** where the catalog offers a Fast
  tier), OpenCode every provider model that installation knows. Change any of
  them mid-conversation; while work is pending the change waits its turn in
  the queue and applies in order.
- **Engine defaults per backend.** Save the starting permission, model and
  effort for each engine; new sessions, tasks and engine switches start from them.
- **Switch engines any time.** A session can move between installed engines.
  The new engine starts a fresh native session seeded with a handoff of the
  conversation so far, in the same working directory.
- **Session tools.** Claude and Codex sessions can **Compact context** using
  the engine's own compaction, and **Undo last turn** to drop the last prompt
  and reply from the engine's memory without touching your files.
- **Pins, colors, archive, drag-and-drop.** Sessions carry a color, can be
  pinned to the top, archived out of the way, renamed, and reordered by
  dragging. The order belongs to the backend, so every console sees the same
  list. Session context menus stay within the screen and scroll when there
  are more actions than the available height.
- **Agent notes.** A mark on every session row shows whether its directory has
  an `AGENTS.md` or `CLAUDE.md`, and opens a small editor for exactly those two
  files.
- **Session titles come free.** An unnamed session takes its title from the
  first line of its first prompt.

### Talk to it while it works

The composer changes shape with the session's state. While a turn is running
you can:

- **Steer** – send an extra instruction into the running turn on any engine.
- **Ask** – put a side question to Claude Code beside its turn. The answer comes
  from a tool-less fork of the live conversation, so the engine's own work never
  sees the exchange. Follow-ups thread; the card sits in the transcript next to
  the work it was about.
- **Queue** – line up the next prompts. The queue is written to the backend on
  every change, can be paused, reordered by dragging, edited back into the
  composer, and survives restarts (a restart parks it as *held* work you
  re-send explicitly).
- **Approve or deny** – engine permission requests appear as cards with the
  proposed action, plus the engine's own suggestions such as *Always allow* or
  allow-and-switch-mode. Cards stay visible until the backend confirms the
  response; their controls are disabled while reconnecting or awaiting
  confirmation.
- **Stop** – interrupt the turn; the engine gets an orderly shutdown and the
  answer so far is kept.

In narrow desktop panes, Ask, Steer and Queue use icons so Stop stays visible.
The controls wrap onto another row when the pane is too narrow for one line.

Everything typed at an agent goes through one shared prompt box: `Enter` sends,
`Shift+Enter` or `Ctrl+J` breaks a line, `↑` at the start of the box recalls
earlier prompts, and `@` opens the mention list (browsers, terminals, sessions,
a new spawn). Recall reads the session's stored prompts, loading older pages as
needed with no history limit, all the way back to its first prompt, on any
device. `↓` at the end walks forward again and restores your unsent draft and
attachments after the newest prompt. Each new recall walk picks up prompts
sent from other devices too. Drafts are shared through the session's backend, so the
half-finished prompt on your desktop is on your phone too. On backends with
typing presence, overlapping edits stay local until you review the drafts or
send your message; another console's edits never interrupt your typing.
The status row below the tools appears only for typing activity, a draft conflict,
or a save/send error; it takes no space when idle.

Ask and Steer accept text only. Text edits and attachments added while their
send is awaiting acknowledgement remain in the composer for the next message.

**Attachments** go in with the `+` button, a paste, or a drop from your file
manager: images, documents, archives, source, anything. Files stream straight to
the session's backend and stay private under `data/uploads/`. Images preview in
the transcript. Images in Markdown replies shrink to fit the available width
while keeping their proportions; smaller images keep their natural size.

Engines that keep working after answering are handled too: Claude Code's
background commands, agents and monitors keep their turn alive until they end,
and transient engine failures (such as an OAuth refresh lock) retry on their own
with a back-off note in the transcript.

### Tasks: parallel work in isolated copies

Open a session and press **+ Task** beside **Main**. Each task is its own
conversation with its own engine choice, queue, approvals and Stop control,
working in an independent Git clone of Main's project that starts from Main's
current files, uncommitted changes included. Run several at once on different
features. New tasks initially select Main's engine and use that backend's saved
model, effort and permission defaults. Adjust these choices before starting.
Attachments are locked while the task is preparing. Closing the dialog after
submission leaves its files available for the backend to finish creating the task.

- Tasks appear beside Main on every device, including tasks created while a
  device was away, without switching the selected conversation. They stay there
  until closed on that device or removed. Closing a tab keeps the task running;
  reopen it from the **Tasks** sheet.
- Drag task tabs to reorder them with the same drag card and sliding animation
  as workspace tabs. Main stays first; the order is saved in this browser.
- Running task tabs animate one, two, then three dots in a fixed-width slot,
  keeping the tab and its contents still. Reduced motion shows three static dots.
- The **Tasks** sheet lists every task with its state and latest answer. Tasks
  ready for review show **Review** in the sheet and their tabs.
- **Review changes** shows the changed files with a coloured diff; **Apply to
  Main** writes that delta into your working tree, refusing overlapping edits
  that would not apply cleanly. Applied tasks can keep going; the next review
  contains only what changed since.
- **Resolve conflicts** (on by default) lets a conflicting apply send one
  follow-up to the task's own agent with snapshots of the baseline, the task
  and Main, so the agent reconciles both sides in its copy for you to review
  again. Nothing is ever applied unseen.
- **Fold into Main** keeps a condensed, searchable copy of the task's
  conversation as a collapsible card in Main's transcript when the task is
  removed, with the files it applied. Optionally let Main's model see the
  newest folded tasks at the start of its turns.

Tasks need a Git repository. Limits: 64 tasks per session, 50,000 files or
512 MiB of initial working files, 16 MiB per review. Details and the exact
persistence contract are in [docs/session-tasks.md](docs/session-tasks.md).

### Terminals and browsers, shared with the agent

- **Shared terminals.** Open a terminal tab on any backend (xterm.js over a
  node-owned PTY, your configured shell command). Every terminal has a
  four-character ID and can be linked to one chat. Turns on a terminal-enabled
  backend carry a private bridge into these same PTYs (snapshot, type, press
  keys, wait for output), and the shipped guidance tells the model to use it
  only when you ask, with `@Terminal A8AR` or `@New terminal`. You both see and
  type in one screen. Ordinary shell work still uses the engine's own tools.
- **Managed browsers.** Enable Browser on a backend that has Chromium and Puppy
  runs isolated headless instances, each with a four-character ID and its own
  profile. You get a live view in a tab with an address bar, back, forward and
  reload, and full mouse and keyboard input. The agent gets a high-level
  toolset for the same browser: navigate, snapshot the accessibility tree,
  click, type, hover, press, scroll, select, check, screenshot, switch pages,
  read console messages and network failures, upload one of the session's own
  attachments into a file input, and inspect downloads. A page the agent opens
  appears as a tab beside the chat without stealing focus, pages follow your
  light or dark theme, and an optional shared sign-in store carries cookies
  across all of a backend's browsers.

### Agents that delegate and collaborate

- **Spawned agents.** `@Spawn an agent on build-node using codex at high effort
  to …` starts a one-shot,
  non-interactive engine run on any backend, or up to twelve in parallel. The
  `@` menu's wizard inserts the selected settings without a trailing “to”,
  leaving the task wording to you. A
  session hosted on a backend spawns on that backend only. The spawning turn
  waits for the answers and acts on them; jobs die with their turn, renew
  their inactivity timer only on real progress, and use the executing backend's
  timeout settings unless a limit is explicitly supplied. Defaults are ten
  minutes without progress and two hours total; either can be set to unlimited.
- **Session references.** `@session` selects one, several or all sessions,
  including archived ones. The agent can then read their transcripts, search
  their histories with exact-message links, and, when you ask, send them a
  question, a task, a steering instruction or a stop.
- **Coordination.** Ask for a plan and the agent can build a finite workflow of
  up to 24 question or task steps with dependencies across sessions, watched
  from a **View workflow** card in the transcript. Workflows survive restarts
  and never create new sessions on their own. One request can address up to
  512 sessions; requests default to a one-hour deadline and workflows to two
  hours, never more than two hours from creation.

All of this reaches the model through turn-bound MCP bridges Puppy starts for
each turn, on every engine. Their policy texts live in Settings → System prompt.
The API-only headless package provides the same bridges without running a web
UI, including the backend's configured browser, terminal, and spawn policies.

### Find anything, on any backend

The sidebar box filters sessions by title, location and backend as you type.
The advanced **Search** tab searches the full history of every online backend:
prompts, replies, thinking, tool activity and system notes, with phrases,
`-exclusion`, `OR`, kind filters, a time window, relevance or newest ordering,
and grouped results. Opening a hit lands on that exact message with a window of
history around it.

### Never lose your place

- **Tabs and splits.** Sessions, terminals, browsers and search open as tabs;
  drag a tab to the edge of a pane to split the workspace. Tab layout is
  remembered per browser.
- **Status at a glance.** Every session row shows a spinner and a running clock
  while its agent works, the backend it runs on when idle, and task activity or
  a waiting approval. The footer lists every backend with its Puppy version
  and each of its engines with sign-in state, an orange *Ready* when a newer
  CLI is published, and the remaining weekly quota where the engine reports
  it.
- **Completion alerts.** Run separate success and failure commands on a chosen
  backend when a session finishes everything it had queued (play a sound, ping
  your home automation). Leave either command empty to skip that outcome;
  stopped turns stay silent. Test either command before saving.
  Placeholders and `PUPPY_*` environment variables carry the session, engine,
  model, status, duration and directory. Arm or silence it with the bell in the
  footer.
- **Backup and restore.** Export one `.tar.gz` with settings, accounts, backend
  registrations, sessions and transcripts, uploads, scratch workspaces, task
  copies, tabs and drafts. Import validates the whole archive first, only runs
  while the instance is idle, and rolls back if the install fails.

## Multi-machine

Puppy's console is a **controller**. Any number of other machines can be paired
as **backends** from Settings → Backends, either full Puppy instances or the
API-only headless package, by pasting a pairing block or entering a URL and
API token. Enter in an Add backend text field submits the form. Failed adds
keep their inline error and entered values; failed removals keep the backend
and show a named error so you can retry. Your browser only ever talks to the
controller, which proxies HTTP and WebSocket traffic to the backends it
authenticates with their API tokens and pinned TLS certificates.

- Sessions on a backend appear in the same sidebar, merged with local ones by
  activity, and open in the same tabs.
- Terminals, browsers, spawned agents, search and session references work on
  every capable backend.
- An unreachable backend is detected by the controller alone, shown muted with
  its last known sessions, and probed with back-off until it returns.

### The headless backend

`backend/` builds a single-file, API-only runtime with no web UI and no cookie
login:

```sh
python3 backend/build.py
backend/dist/puppy-backend.pyz --version
```

On the remote machine, create its private configuration once with the
`pairing` command (it prints a pasteable pairing block with the API token and a
SHA-256 certificate pin), then run it through the bundled launcher. Fresh
backends default to HTTPS with a self-signed identity Puppy pins; a CA-issued
certificate or explicit cleartext are options. With the launcher in place the
controller can **upgrade backends from Settings**: it builds a matching
artifact, self-tests and signs it, the backend replaces itself atomically only
when idle, and the launcher health-checks the new version and rolls back if it
does not come up. An optional **Auto-upgrade when idle** switch does the same
without you.

[backend/README.md](backend/README.md) covers pairing, TLS choices, the systemd
example, and every wire contract; [docs/current-contract.md](docs/current-contract.md)
lists the protocol requirements. Only API protocol 2 peers are accepted.

### Remote workspaces

A session on one backend can work on a project directory that lives on another.
The engine works in a private mirror on its own machine; the controller
synchronizes the two between turns, byte-verified and crash-safe, and never
lets the backends talk to each other. Edits that diverge on both sides become
conflicts that keep both versions until you pick a side in the **Linked
workspace** sheet; the losing bytes are preserved so nothing is silently
overwritten. On phones, the sheet stacks its action buttons so the terminal's
backend name stays readable; long button labels wrap within the button.

## Settings

- **Instance** – name, default working directory, terminal command, the
  Browser switch and shared cookies switch, bind IP and port, HTTP or HTTPS with
  a generated or imported certificate.
- **Engines** – per backend: sign-in state, installed and latest versions, a
  **Refresh** and an **Update** button that runs each vendor's own updater
  (`claude update`, `codex update`, `opencode upgrade`), plus an **Engine
  updates** schedule for unattended updates that tries each new version once.
- **Usage refresh** – how often each backend refreshes its read-only account
  usage snapshot without starting a model turn (Codex today), with an on-demand
  refresh control.
- **File uploads** – the per-file attachment limit on each backend.
- **Completion alerts** – success and failure commands, their shared backend,
  and a test button for each outcome. The enabled switch and sidebar bell
  control both commands; tests run the entered draft even while alerts are off.
  Both commands are included in Backup & restore. See
  [completion alert configuration](docs/completion-alerts.md) for the saved shape.
- **Timers** – release checks, model catalog and sign-in caching, and the
  controller's polling and synchronization intervals. Validation and save errors
  appear beneath the affected field; edit it or press Escape to clear the error.
- **Timeouts** – per backend: maximum agent turn duration (2 hours), spawned-agent
  runtime (2 hours) and inactivity (10 minutes), and unattended terminal and
  browser timeouts (15 minutes each). Values are seconds; **0 means unlimited**.
  Turn and spawn settings initialize new runs; changing an unattended timeout
  restarts that timer immediately. Agent interaction renews unattended timers.
  Spawned agents still stop with their owning turn. Each field has an Apply
  control, and Reset to defaults restores the selected backend's five defaults.
- **System prompt** – a custom text added to every turn on that backend, and the
  conditional guidance for remote workspaces, browsers, terminals and spawned
  agents, each with a reset to Puppy's default.
- **Backends** – add, edit, test, upgrade and auto-upgrade paired machines.
- **Security** – change your password.
- **Backup & restore** – export and import the full-instance archive.

Engine defaults are edited from the composer's dropdowns or the session menu.

Timeout persistence and manual preparation of existing installations are
documented in [Timeout settings](docs/timeouts.md).

## Data, privacy and security

- **Self-hosted, single-user.** Accounts are stored with PBKDF2; sign-in
  cookies last 30 days and are `__Host-` secured over HTTPS; failed sign-ins are
  rate limited. Puppy drives the installed CLIs and leaves their logins to
  them: no credentials are extracted or proxied to provider APIs.
- **Unbranded sign-in.** The sign-in and first-run setup page uses neutral
  wording and a lock icon. Its HTML, styles, scripts and favicon contain no
  product branding; console assets and the instance name require authentication.
  The page shares the console's form styling and remembers its theme within
  the current tab. This reduces casual identification; existing TLS certificate
  metadata and authenticated console branding are unchanged.
- **Loopback first.** A new install only listens on `127.0.0.1`. If an operator
  configures another address before the first account exists, setup requires a
  high-entropy bootstrap code printed to the startup output and the private
  `data/puppy.log`, or supplied through `PUPPY_SETUP_CODE` (16 to 128
  non-whitespace ASCII characters). A generated code rotates on every restart,
  and setup stops accepting either form once an administrator exists.
- **Lockout-safe listener changes.** A new bind address or port is saved only
  after the browser you are using proves it can reach the new endpoint, and the
  page carries your sign-in, tabs and drafts over to the new origin when Puppy
  restarts. Automatic activation is deployment-owned: set `PUPPY_RESTART_HOOK`
  to an absolute executable implementing two fixed commands, `hook probe PID`
  (exit zero only when it can restart that exact Puppy process) and
  `hook restart PID` (queue an idle-aware, graceful restart). Puppy invokes
  both without a shell; the hook must be a regular, executable, non-symlink
  file owned by root or the service user and not group- or world-writable.
  Without a hook the verified setting is saved and waits for a manual restart.
  An HTTPS reverse-proxy page cannot prove a plain-HTTP endpoint (browsers
  block it as mixed content), so it fails closed.
- **Everything private lives in `data/`** (gitignored): `config.json`,
  `puppy.db`, uploads, scratch and task workspaces, browser profiles, TLS
  identities, the search index and logs. `PUPPY_DATA` relocates it. Backup
  archives contain password hashes, API tokens and TLS material: treat them as
  credentials.
- **Backends never talk to each other** and never learn each other's tokens;
  the controller relays everything over channels it already authenticates.
  Managed browsers are reachable only through Puppy's private debugging pipe,
  never a DevTools port.
- **Clocks follow the server.** Times in the console use the Puppy process's
  locale hour cycle, not the browser's.

## Tests

The suites run without a browser, a real engine, network access or quota unless
noted:

```sh
node tests/sidebar_ui_test.js        # sidebar ordering, pins, reorders, filtering
node tests/tab_drag_ui_test.js       # task discovery, saved visibility and tab dragging
node tests/menu_dismiss_ui_test.js   # outside focus/taps, hamburger and menu toggles
node tests/composer_ui_test.js       # the shared prompt box and its "@" list
node tests/backend_settings_ui_test.js # backend forms, removal errors and retry
node tests/task_config_ui_test.js    # the New task dialog
node tests/task_fold_ui_test.js      # task removal and the folded archive card
python3 tests/backend_test.py        # headless package, auth, protocol, capabilities
python3 tests/snapshot_test.py       # backup export/import and rollback
python3 tests/search_test.py         # the search index and query language
python3 tests/spawn_test.py          # spawned agents against a stub engine
python3 tests/mcp_startup_test.py     # source/zipapp MCP startup with filtered environments
python3 tests/workspace_sync_test.py # remote workspace sync and conflicts
python3 tests/browser_test.py        # managed browsers against a stub Chromium
python3 tests/cli_upgrade_test.py    # engine CLI updates against a stub updater
python3 tests/session_links_test.py  # session references, requests, workflows
```

`tests/integration_test.py` and `tests/side_question_test.py` drive real engines
and spend a little subscription quota; run them deliberately. The remaining
`tests/*_test.py` and `tests/*_test.js` files cover engine defaults, model
catalogs, agent notes, timers, terminals, workspaces, tasks and the live
controls in the same no-quota style.

## License

Puppy is released under the [MIT License](LICENSE). Vendored front-end
libraries in `puppy/static/vendor/` (xterm.js, marked, DOMPurify) keep their own
license notices.
