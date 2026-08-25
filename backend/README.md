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
  --usage-refresh-minutes 15 \
  --max-upload-size-mb 8 \
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

## Session activity timing

Session-list responses and update events include `server_time`. A running
session also includes `active_since`, the start of its current uninterrupted
work block. That start is retained while queued messages flow into subsequent
turns and is cleared only after both the active turn and queue are empty. This
lets a controller show one continuous elapsed time while compensating for clock
differences between the controller and backend. These fields are additive;
controllers can continue to attach older nodes that do not send them.

## Account usage refresh

The headless package stores the same per-node usage-refresh interval as the full
runtime. Set it while pairing or serving with `--usage-refresh-minutes N` (1 to
1440 minutes); use `0` to disable it. Once attached, the controller exposes the
same value in Settings → Usage refresh and can change it through the authenticated
`/api/engines/usage-refresh` endpoint. The node advertises this support with the
`engine-usage-refresh` capability, so older backends remain explicitly disabled
in the Settings UI.

New nodes also advertise `engine-usage-refresh-manual`. The authenticated POST
form of the same endpoint performs one immediate read even when the automatic
interval is disabled. Controllers use it for the compact refresh control beside
a ready Codex status; older nodes omit the capability, so the control is hidden
until they are upgraded.

The refresh is lazy: a due engine-status poll asks supported installed CLIs for
their current read-only account-limit snapshot. It does not start a turn or
consume model tokens, concurrent polls coalesce, and failed attempts are
rate-limited. Codex currently supplies a direct account snapshot; its existing
local rollout record remains the fallback if the account read is unavailable.

## File uploads

The headless package accepts streamed session attachments of any file type and
stores them privately under `data/uploads/`. Files are created mode 0600 inside
mode-0700, session-specific directories; uploaded executables are therefore not
made executable merely by transferring them. Set the per-file limit with
`--max-upload-size-mb N` (maximum 1024 MiB); `0` disables uploads. The same
setting is available for every current attached node in Settings → File uploads
through the authenticated `/api/uploads/settings` endpoint. The receiver checks
both declared and actual byte counts, so controller or client-side checks are
only conveniences and cannot bypass the node's limit.

Controllers stream remote uploads rather than buffering them and preserve TLS
pinning, token authentication, redirect rejection, and the receiving node's
authority over the limit. Active transfers temporarily make upgrade readiness
busy so an artifact restart cannot interrupt a partially written file. Older
nodes retain their image-only endpoint and remain explicitly marked unsupported
for arbitrary-file settings until upgraded.

## CLI release status

The full runtime and headless package periodically read bounded `latest`
metadata for each engine's vendor-published package over HTTPS. Successful
results are cached for six hours; failures keep the last known result and retry
after 15 minutes. This is advisory only: it never installs software, never
starts an engine turn, and a registry or network failure cannot disable an
installed engine. `/api/engines` reports the latest version and whether the
installed semantic version is older, allowing the controller to mark only the
existing version pill as outdated.

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

`GET /api/node/upgrade` reports live readiness as `ready`, `busy`, `upgrading`,
`blocked`, or `unsupported`, including active session/queue blockers and the
terminal count. Settings polls this lightweight status while visible: the
Upgrade button is enabled only when the node reports that it can accept the
request. The POST repeats the same check and can still reject a race or any
runtime blocker before staging; it checks the workload again after candidate
validation. Older upgrade-capable nodes are supported for their first upgrade
by conservatively inferring session activity, while their existing POST gate
remains authoritative for terminals and races.

Each attached headless backend also has an opt-in **Auto-upgrade when idle**
policy in the controller's Backends card. It is controller-owned (and therefore
included in a WebUI backup), not a setting stored on the remote node. When the
controller version moves ahead, its background worker checks the node's live
readiness and starts the same signed, self-tested, health-checked upgrade flow.
Busy nodes are left alone and checked again shortly; unavailable or blocked
nodes back off and retry. The backend still repeats its idle/readiness check at
POST time, so a turn or terminal that starts during artifact preparation wins
the race and safely rejects the automatic attempt.
