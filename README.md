# Puppy 🐾

Persistent AI coding session manager. A web console for organizing and driving
**Claude Code**, **Codex**, and **OpenCode** CLI sessions, with cross-engine
session switching, interactive permission approvals, multi-backend support and
built-in web terminals.

Tasks can run concurrently inside one session, with separate conversations and
review/apply controls. See [session tasks and rollback](docs/session-tasks.md).

## How it works

- **Backend** (`puppy/`, Python 3.9+ / aiohttp): spawns the installed `claude`,
  `codex`, or `opencode` binary per turn, drives its structured JSONL/ACP
  interface, normalizes events into Puppy's own transcript (sqlite), and streams
  everything to the browser over websockets. Engine-native sessions are resumed
  by id; Puppy's DB remains the canonical record.
- **Frontend** (`puppy/static/`, vanilla JS SPA): left sidebar with sessions and
  backends, tabbed main area with concurrent chat sessions and xterm.js
  terminals. Responsive - works on mobile.
- **Shared terminals**: every WebUI terminal has a four-character Terminal ID
  and can be linked to one chat. When the user explicitly asks the model to
  work in that terminal, Puppy exposes a turn-bound MCP bridge into the same
  PTY, so user and model see and type in one session. Ordinary model shell and
  file operations continue to use the engine's normal tools.
- **Shared drafts**: each chat composer is written through to its session node
  and streamed to every open console. Unsent prose and staged attachment chips
  therefore follow the same chat across desktop/mobile browsers and survive a
  browser, device, or Puppy restart. A small local journal covers edits whose
  server acknowledgement was interrupted.
- **Engine switching**: a session can move between any installed engines at any
  point. The new engine starts a fresh native session seeded with a transcript
  handoff in the same working directory.
- **OpenCode providers**: each node discovers its own OpenCode model catalog and
  presents every reported choice in the ordinary per-session model control.
  Provider credentials, plugins, project rules, and native sessions stay owned
  by that node's ordinary OpenCode installation.
- **Scratch workspaces**: a session can start in a private, disposable workspace
  without choosing a project directory. Its transcript remains durable while
  its files live under the host's temporary filesystem and are removed with the
  session. If the host clears them (commonly on reboot), Puppy marks the
  workspace expired and safely starts fresh instead of resuming stale context.
- **Multi-backend**: pair full Puppy instances or the API-only headless package
  in `backend/` (Settings → Backends, using pairing JSON or URL + API token).
  The browser stays single-origin; this instance proxies HTTP + websockets to
  remotes, including terminals when that capability is enabled. Headless nodes
  default to one-port HTTPS/WSS with a persistent certificate pin enforced by
  the controller. Launcher-managed nodes can also be upgraded and health-checked
  from Settings, with automatic rollback if the replacement does not start cleanly.
- **Backup and restore**: Settings can download a versioned `.tar.gz` snapshot
  containing Puppy settings, accounts, backend registrations, sessions and
  transcripts, uploads, scratch workspaces, and browser tabs/drafts. Import is
  idle-only, validates the complete archive before changing live state, and
  rolls back a failed install. Ordinary project directories and external engine
  credentials/native caches stay on their respective hosts. On a controller,
  remote registrations are included while node-owned sessions remain on those
  registered backends.
- **Lockout-safe binding**: a new WebUI bind IP or port is committed only after
  the current browser reaches a short-lived, one-use proof on that exact
  endpoint. Puppy then queues its idle-aware deployment restart; the page waits
  for the replacement listener and securely carries its login, tabs, layout and
  ordinary drafts to the new origin before reconnecting. Failed verification
  leaves the setting unchanged, and an already-pending listener can be proved
  and activated from Settings. An HTTPS reverse-proxy page fails closed because
  browsers forbid the direct HTTP proof as mixed content. Headless backend ports
  remain deployment-time settings.
- **Fresh usage status**: Settings → Usage refresh controls a separate interval
  for this instance and every attached backend. Supported engines refresh their
  read-only account-limit snapshot without starting a model turn, so the weekly
  quota shown in the sidebar stays current even when that engine is idle. Zero
  disables automatic refresh on that node; a ready Codex row retains a compact
  on-demand refresh control.
- **CLI freshness**: each runtime periodically compares installed engine CLI
  versions with bounded `latest` metadata from the vendor-published package.
  An outdated version pill turns orange; failures retain the last good result
  and never interfere with engine availability. No package manager is invoked
  and Puppy never installs the update itself.
- **File attachments**: use the `+` picker, clipboard paste, or drag files from
  the operating system onto the composer. Images, documents, archives, source,
  binaries, and other files upload directly to the session's execution node.
  Transfers stream through controllers, remain private under `data/uploads/`,
  and obey the receiver's configurable per-file limit; zero disables uploads.

## Requirements

- Linux with Bash
- Python 3.9+ with `aiohttp`
- One or more supported CLIs installed for the user running Puppy: `claude`
  (Claude Code), `codex`, and/or `opencode`. Claude/Codex use their normal login
  flow; OpenCode uses whichever providers that installation has authenticated.
- No other daemons, no build step, no npm. Vendored JS libs live in
  `puppy/static/vendor/`.

## Run

```
./run.sh                      # listens on 127.0.0.1:10888 by default
```

`run.sh` locates its own checkout and uses `python3` from `PATH`. Set
`PUPPY_PYTHON` to an absolute interpreter path when the service should use a
specific virtual environment. Run it under whichever service lifecycle your
host uses.

Automatic activation after a verified WebUI bind change is optional and
deployment-owned. Set `PUPPY_RESTART_HOOK` to an absolute executable implementing
two fixed commands: `hook probe PID` must return zero only when it can restart
that exact Puppy process, and `hook restart PID` must queue an idle-aware,
graceful restart. Puppy invokes both without a shell. The hook must be a regular,
executable, non-symlink file owned by root or the Puppy service user and must not
be group- or world-writable. Without a hook, the verified listener setting is
saved and remains pending until the operator restarts Puppy manually.

Fresh installs are loopback-only. Open `http://127.0.0.1:10888` on the host and
create the administrator account before exposing Puppy to another device. Use
Settings to verify and activate a different listener, and prefer HTTPS whenever
the connection leaves the host.

If an operator deliberately configures a non-loopback listener before creating
the first account, Puppy prints a high-entropy bootstrap code to its startup
output and private `data/puppy.log`; the setup form requires that code. Set
`PUPPY_SETUP_CODE` to 16-128 non-whitespace ASCII characters when an externally
managed bootstrap credential is preferable. Setup stops accepting either form
once an administrator exists; an automatically generated code also rotates on
every restart.

Headless remote backend artifact:

```
python3 backend/build.py
backend/dist/puppy-backend.pyz serve --help
```

See `backend/README.md` for pinned-TLS pairing, legacy network guidance,
an optional systemd unit, and the one-time launcher bootstrap needed for remote
upgrades. The artifact contains no frontend or cookie-login surface and reports
an independently versioned controller/backend protocol.

## Data & config

Persistent private state lives in `data/` (gitignored): `config.json` (instance
name, bind host/port, api token, default working directory, terminal command,
usage-refresh interval, and upload-size limit),
`puppy.db` (sessions, transcripts, shared drafts, users), backend TLS identities,
and `puppy.log`. Scratch-session files are the
intentional exception: they live in a mode-0700, instance-specific namespace
under the OS temporary directory and are disposable. Public source releases
must include tracked files only: never package the working directory or its
ignored `data/` and `backend/dist/data/` trees. Export archives contain password
hashes, API/backend tokens, and TLS material; treat them as private credentials
and only import archives from a trusted source.

## Compliance

Puppy drives installed CLI interfaces and leaves login/provider setup with those
CLIs (`claude login`, `codex login`, or OpenCode's provider auth). No credentials
are extracted or proxied to provider APIs. Intended for personal, single-user
use with your own accounts.

## License

Puppy is released under the [MIT License](LICENSE). Vendored dependencies retain
their own license notices and terms.


## Session references and coordination

Type `@session` in a chat composer to select one or several sessions, or choose
**All sessions**. The picker searches titles, project folders, and node names,
including archived conversations. Sent references display as links; their stable
node/session identities survive renames and engine switches. A subsequent prompt
without new mentions keeps the previous selection.

The `puppy_session` MCP bridge offers three layers on every supported engine:

- **References:** discover sessions, search their histories, and read transcript
  pages with exact-message source links. Search pages both across sessions and
  within a session's matches. Long messages have a character continuation cursor.
  All excludes the originating session; offline or older nodes are reported as
  unavailable instead of being silently included in the result.
- **Communication:** explicitly send a question, task, steering update, or stop.
  A question uses the engine's native side channel when ready; otherwise it
  queues a conversational question. Tasks join the destination's normal queue.
  Steering and stopping require its current turn token. Requests have durable
  ids, per-destination statuses and answers, and cancellation affects only the
  request's own queued or active work. Repeating a request id with different
  content is rejected. An uncertain network response retains the original id.
- **Coordination:** create up to 24 named question/task steps with explicit
  dependencies. Independent steps start together, successful prerequisite answers
  accompany dependent steps, and failed prerequisites block their dependants.
  Plans and request ids survive turns and restarts. Use the transcript's **View
  workflow** or **View request** button for results, refresh, and cancellation.

Requests default to a one-hour deadline and workflows to two hours; the absolute
maximum is two hours from creation, including time queued. A workflow never
creates new agents or sessions. Up to 512 destinations can be selected for one
request. Source sessions cannot target themselves, and unresolved requests may
not form waiting cycles. Ordinary queue holds remain under the user's control.
References authorize reading; cross-session actions require an explicit request.

The controller brokers access to paired nodes over its existing authenticated,
TLS-pinned channels. It opens a bounded relay socket so backend-hosted sessions
can use references selected in the controller's console. Backend nodes never
receive peer tokens or initiate direct peer connections. Older nodes remain
usable but cannot offer these new features until upgraded.

Reference selections, inbox/outbox receipts, and workflows live in the existing
SQLite `meta` table and are included in full backups. Their records have exact
current shapes with no runtime migration. Unfinished outgoing requests and
workflows block snapshot export/import. Conversation citations describe recorded
history; they do not verify that files still have the recorded contents.

Run `python3 tests/session_links_test.py` for the no-quota reference, routing,
queue, idempotency, cancellation, workflow, driver and MCP contract checks.
