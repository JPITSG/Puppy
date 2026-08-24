# Puppy headless backend

This directory contains the separately deployable, API-only Puppy runtime. It
shares the console's database, runner and engine drivers at build time, but the
resulting artifact exposes no web GUI, cookie login, settings, or backend proxy.

## Build

```bash
python3 backend/build.py
backend/dist/puppy-backend.pyz --version
```

The zipapp is one file and contains all Puppy Python code required at runtime.
The target still needs Python 3.9+, `aiohttp`, and whichever official engine
CLIs it will run. No frontend assets are included.

Run from the source tree without building:

```bash
python3 -m backend.puppy_backend serve --data-dir backend/data
```

## Deploy

Copy `dist/puppy-backend.pyz` to the remote machine, install `aiohttp` (or use
`requirements.txt`), and run
the engine login commands as the same unprivileged Unix user that will run the
service. A typical first launch is:

```bash
PUPPY_BACKEND_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  ./puppy-backend.pyz serve \
  --name buildbox-01 \
  --data-dir ./data \
  --bind 100.64.0.12 \
  --port 10888 \
  --advertise-url http://100.64.0.12:10888 \
  --default-cwd /srv/projects
```

The generated or supplied token is persisted inside the private data directory.
Retrieve a pasteable configuration for the full Puppy console with:

```bash
./puppy-backend.pyz pairing --data-dir ./data
```

Use a private encrypted network such as Tailscale/WireGuard, or supply
`--tls-cert` and `--tls-key`. The API token grants the authority of the Unix
account running this service; do not expose token-authenticated plain HTTP to
the public internet. Terminal websockets are enabled by default for feature
parity and can be removed with `--disable-terminal`.

`puppy-backend.service` is a starting systemd unit. Adjust its user, paths,
bind address and network protection before installation.

## Upgrade seam

The artifact is built via an atomic replacement and the service unit uses an
external restart manager. `/api/node` reports artifact/build metadata plus an
explicit `upgrade.supported: false` descriptor. The future signed remote-update
implementation has a reserved `/api/node/upgrade` path and a dedicated module
at `puppy_backend/upgrade.py`; no remote mutation endpoint is enabled today.
