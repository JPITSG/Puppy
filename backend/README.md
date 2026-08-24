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
  --advertise-url https://100.64.0.12:10888 \
  --auto-tls \
  --default-cwd /srv/projects \
  --enable-remote-upgrade
```

This generates and prints a pasteable pairing block containing an API token and
the SHA-256 pin for a persistent, self-signed TLS identity. The private key and
token are stored only in the private data directory; the public fingerprint is
what lets the controller authenticate the certificate without a public CA.
`--auto-tls` requires the `openssl` command once, when the identity is created.
Run the backend through the launcher:

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
Artifact upgrades intentionally do not replace that stable launcher. When
enabling TLS on an installation created before pinned health checks existed,
copy the newly built launcher once before changing the backend to HTTPS.

Fresh headless data directories default to automatic TLS; `--auto-tls` is kept
explicit in deployment commands so the intended transport is visible. HTTPS
and secure WebSockets share the same bind port. The API token remains
application-layer authentication; standard TLS provides traffic encryption and
the certificate pin provides backend identity. Paste the complete pairing JSON
in Settings so the controller stores and enforces the pin on probes, proxied API
requests, WebSockets, upgrades, and launcher health checks. Redirects are never
followed with backend credentials.

For a CA-issued certificate, use `--tls-cert` and `--tls-key` instead of
`--auto-tls`; these paths are persisted in backend configuration. Pairing still
includes a pin. `--disable-tls` retains legacy cleartext HTTP compatibility,
which the WebUI labels explicitly and which should only be used over a trusted,
encrypted private network such as WireGuard. The API token grants the authority
of the Unix account running this service; never expose token-authenticated
cleartext HTTP to an untrusted network. Terminal WebSockets are enabled by
default for feature parity and can be removed with `--disable-terminal`.

The automatic identity survives backend artifact upgrades because it lives in
`data/tls/`. If that directory is lost or a custom leaf certificate is rotated,
the fingerprint changes: remove and re-add the backend using fresh pairing JSON.
Pin changes are intentionally never accepted automatically.

Adjust the template user, paths, bind address, and network protection before
installation.

## Scratch workspaces

Controllers can create a session with `workspace_kind: "temporary"` instead of
supplying a working directory. The backend creates a mode-0700 directory inside
a private namespace under the OS temporary directory (normally `/tmp`), scoped
to both the service user and backend data directory. It advertises this contract
with the `temporary-workspaces` capability, so controllers do not offer it for
older nodes that would ignore the field.

Scratch files survive an ordinary service restart but are deliberately not
durable host data. Deleting the session removes them; a reboot or the host's
temporary-file policy may remove them first. The SQLite session and transcript
remain in `data/`. A missing workspace is reported as `workspace_missing`, and
the reset endpoint—or the next turn—creates a new empty directory, clears the
engine-native session id, and seeds the fresh engine context with a notice that
the old files were cleared. Startup cleanup removes only unreferenced,
service-owned directories inside the validated private namespace; normal
working directories are never removed.

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
running version. TLS-enabled nodes advertise remote upgrade support only when
the active launcher declares pinned-certificate health-check support.
