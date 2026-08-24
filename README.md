# Puppy 🐾

Persistent AI coding session manager. A web console for organizing and driving
**Claude Code** and **Codex** CLI sessions (subscription-authenticated, official
binaries only), with cross-engine session switching, interactive permission
approvals, multi-backend support and built-in web terminals.

## How it works

- **Backend** (`puppy/`, Python 3.9+ / aiohttp): spawns the official `claude` /
  `codex` binaries per turn in their headless JSONL modes, normalizes their event
  streams into puppy's own transcript (sqlite), and streams everything to the
  browser over websockets. Engine-native sessions are resumed by id
  (`claude --resume`, `codex exec resume`); puppy's DB is the canonical record.
- **Frontend** (`puppy/static/`, vanilla JS SPA): left sidebar with sessions and
  backends, tabbed main area with concurrent chat sessions and xterm.js
  terminals. Responsive - works on mobile.
- **Engine switching**: a session can be moved claude ⇄ codex at any point. The
  new engine starts a fresh native session seeded with a transcript handoff in
  the same working directory.
- **Multi-backend**: pair full Puppy instances or the API-only headless package
  in `backend/` (Settings → Backends, using pairing JSON or URL + API token).
  The browser stays single-origin; this instance proxies HTTP + websockets to
  remotes, including terminals when that capability is enabled.

## Requirements

- Python 3.9+ with `aiohttp`
- `claude` (Claude Code) and/or `codex` CLIs installed and logged in
  (`claude login` / `codex login`) as the user running puppy
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

See `backend/README.md` for token pairing, private-network/TLS guidance and the
example systemd service. The artifact contains no frontend or cookie-login
surface and reports an independently versioned controller/backend protocol.

## Data & config

Everything private lives in `data/` (gitignored): `config.json` (instance name,
port, api token, terminal command), `puppy.db` (sessions, transcripts, users),
`puppy.log`. The repo itself is clean code, safe to publish.

## Compliance

Puppy only drives the unmodified official vendor binaries; login happens through
each vendor's own flow on the host (`claude login`, `codex login`). No tokens
are extracted or proxied to provider APIs. Intended for personal, single-user
use with your own subscriptions.
