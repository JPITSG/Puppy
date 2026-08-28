# Puppy 🐾

Persistent AI coding session manager. A web console for organizing and driving
**Claude Code**, **Codex**, and **OpenCode** CLI sessions, with cross-engine
session switching, interactive permission approvals, multi-backend support and
built-in web terminals.

## How it works

- **Backend** (`puppy/`, Python 3.9+ / aiohttp): spawns the installed `claude`,
  `codex`, or `opencode` binary per turn, drives its structured JSONL/ACP
  interface, normalizes events into Puppy's own transcript (sqlite), and streams
  everything to the browser over websockets. Engine-native sessions are resumed
  by id; Puppy's DB remains the canonical record.
- **Frontend** (`puppy/static/`, vanilla JS SPA): left sidebar with sessions and
  backends, tabbed main area with concurrent chat sessions and xterm.js
  terminals. Responsive - works on mobile.
- **Engine switching**: a session can move between any installed engines at any
  point. The new engine starts a fresh native session seeded with a transcript
  handoff in the same working directory.
- **OpenCode providers**: each node discovers its own OpenCode model catalog.
  Settings → Engines lets the operator choose the subset exposed by Puppy;
  provider credentials, plugins, project rules, and native sessions stay owned
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
  endpoint. Puppy then queues its idle-aware supervised restart; the page waits
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

- Python 3.9+ with `aiohttp`
- One or more supported CLIs installed for the user running Puppy: `claude`
  (Claude Code), `codex`, and/or `opencode`. Claude/Codex use their normal login
  flow; OpenCode uses whichever providers that installation has authenticated.
- No other daemons, no build step, no npm. Vendored JS libs live in
  `puppy/static/vendor/`.

## Run

```
./run.sh                      # listens on 0.0.0.0:10888 by default
```

Supervisord deployment:

```
cp puppy.supervisor.conf /etc/supervisor/conf.d/puppy.conf
supervisorctl update
```

First visit prompts for the creation of the admin account.

Headless remote backend artifact:

```
python3 backend/build.py
backend/dist/puppy-backend.pyz serve --help
```

See `backend/README.md` for pinned-TLS pairing, legacy network guidance,
Supervisor/systemd templates, and the one-time launcher bootstrap needed for
remote upgrades. The artifact contains no frontend or cookie-login surface and
reports an independently versioned controller/backend protocol.

## Data & config

Persistent private state lives in `data/` (gitignored): `config.json` (instance
name, bind host/port, api token, terminal command, usage-refresh interval,
upload-size limit, and each node's selected OpenCode model IDs),
`puppy.db` (sessions, transcripts, users), backend TLS identities, and `puppy.log`. Scratch-session files are the
intentional exception: they live in a mode-0700, instance-specific namespace
under the OS temporary directory and are disposable. The repo itself is clean
code, safe to publish. Export archives contain password hashes, API/backend
tokens, and TLS material; treat them as private credentials and only import
archives from a trusted source.

## Compliance

Puppy drives installed CLI interfaces and leaves login/provider setup with those
CLIs (`claude login`, `codex login`, or OpenCode's provider auth). No credentials
are extracted or proxied to provider APIs. Intended for personal, single-user
use with your own accounts.
