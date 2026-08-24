# Puppy headless backend

This directory contains the separately deployable, API-only Puppy runtime. It
shares the console's database, runner and engine drivers at build time, but the
resulting artifact exposes no web GUI, cookie login, settings, or backend proxy.

## Build

```bash
python3 backend/build.py
backend/dist/puppy-backend.pyz --version
```

The build emits `puppy-backend.pyz` plus the stdlib-only
`puppy-backend-launcher.py`. The zipapp contains all Puppy Python code required
at runtime; the stable launcher owns restart health checks and rollback. The
target still needs Python 3.9+, `aiohttp`, and whichever official engine CLIs
it will run. No frontend assets are included.

Run from the source tree without building:

```bash
python3 -m backend.puppy_backend serve --data-dir backend/data
```

## Deploy

Copy both generated files to the remote machine, install `aiohttp` (or use
`requirements.txt`), and run the engine login commands as the same unprivileged
Unix user that will run the service. Initialize the private configuration once:

```bash
./puppy-backend.pyz pairing \
  --name buildbox-01 \
  --data-dir ./data \
  --bind 100.64.0.12 \
  --port 10888 \
  --advertise-url http://100.64.0.12:10888 \
  --default-cwd /srv/projects \
  --enable-remote-upgrade
```

This generates and prints a pasteable token, persisted only inside the private
data directory. Run the backend through the launcher:

```bash
python3 ./puppy-backend-launcher.py \
  --artifact ./puppy-backend.pyz \
  --state-dir ./data/upgrade -- \
  serve --data-dir ./data
```

Use `puppy-backend.supervisor.conf` or `puppy-backend.service` as the service
manager template. The launcher must remain the supervised process: launching
the zipapp directly deliberately suppresses the `remote-upgrade` capability,
even if it was enabled in configuration. The zipapp and its containing
directory must be writable by the service user so it can retain and atomically
replace the artifact; the stable launcher can remain root-owned and read-only.

Use a private encrypted network such as Tailscale/WireGuard, or supply
`--tls-cert` and `--tls-key`. The API token grants the authority of the Unix
account running this service; do not expose token-authenticated plain HTTP to
the public internet. Terminal websockets are enabled by default for feature
parity and can be removed with `--disable-terminal`.

Adjust the template user, paths, bind address, and network protection before
installation.

## Remote upgrades

After the one-time launcher bootstrap, the attached WebUI can upgrade an older
backend from Settings. The controller builds its matching backend artifact,
self-tests it, and signs the canonical manifest plus bytes with an HMAC key
derived from that backend's API token. The backend then:

1. requires an idle node (no running/queued turns or terminal sessions),
2. verifies the signature, monotonic version, size, and SHA-256,
3. validates the ZIP and runs its isolated `self-test`,
4. retains `puppy-backend.previous.pyz` and atomically replaces the live file,
5. exits with the launcher's reserved upgrade status,
6. lets the launcher health-check the new API and either commit or restore the
   previous artifact before restarting it.

Pending state and the last result live under `data/upgrade/` with private
permissions, so a launcher/Supervisor crash or host reboot during replacement
continues the same validation or rollback on the next start. Replays and
downgrades are rejected because the target version must be newer than the
running version.
