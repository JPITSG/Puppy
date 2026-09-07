Managed Scratch sessions and task copies use
`<configured-data-directory>/workspaces/session-<random>/` as of **1.0.455**.
They no longer select storage through `/tmp`, `TMPDIR`, or Python's default
temporary directory. Their files survive service restarts and reboots.

The default data directory is `<installation>/data/`; `PUPPY_DATA` overrides it
for the full runtime, and the headless backend uses its configured data
directory. Puppy resolves each workspace to an absolute path on that backend.
No installation path is hardcoded, and the layout needs no service UID or
data-directory hash component. `data/` and its contents are gitignored. The data directory,
workspace root and allocated workspaces are private, mode 0700.

| Managed workspace use | Current storage and lifetime | Source |
| --- | --- | --- |
| Scratch sessions | Working files in `workspaces/session-<random>/`, retained until session deletion, explicit reset, or orphan cleanup. If a directory goes missing, Puppy keeps the transcript and creates a fresh workspace before the next turn. | [Workspace lifecycle](../puppy/workspaces.py), [execution](../puppy/runner.py) |
| Inner session tasks | Independent project clone, uncommitted files, Git objects and review refs in the same workspace namespace. Copies remain until task removal, including after apply. A missing task copy blocks further work and is never silently recreated. | [Tasks](../puppy/session_tasks.py), [task contract](session-tasks.md) |
| Conflict-resolution preparation | A short-lived managed copy of Main, removed after its snapshot enters the task's Git store. Pinned resolution inputs then live with the task. | [Resolution preparation](../puppy/session_tasks.py) |
| Existing backup restoration | Saved Scratch/task trees are restored into new directories under the configured workspace root, including independent task Git objects. | [Snapshots](../puppy/snapshots.py) |

All four workflows share the allocator in `puppy/workspaces.py`. Ownership,
path-prefix and symlink checks constrain deletion to managed directories.
Removing the final workspace never removes the parent data directory. Task
operation guards keep managed copies independent even when the data directory
is physically inside Main's project.

The existing persisted value `workspace_kind: "temporary"`, the
`temporary-workspaces` wire capability and internal temporary-workspace helper
names still describe the session-owned disposable lifecycle. They do not
select the OS temporary directory. No persisted setting/schema change,
automatic migration, or backup creation routine was added.

The remaining **OS-temp allocations** are separate build and test work:

| Source | Prefixes under the selected OS temporary directory | Contents and cleanup |
| --- | --- | --- |
| [backend/build.py](../backend/build.py) | `puppy-backend-build-` | Python source staging for the backend zipapp, including builds invoked by controller upgrades. Context manager removes it on ordinary exit. A hard kill can leave staging behind. |
| [integration_test.py](../tests/integration_test.py) | `puppy_test_data_`, `puppy_test_ws1_`, `puppy_test_ws2_` | Test server state and two real-engine workspaces; removed in `finally`. |
| [side_question_test.py](../tests/side_question_test.py) | `puppy_sq_data_`, `puppy_sq_ws_` | Test server state and real-engine workspace; removed in `finally`. |
| [spawn_test.py](../tests/spawn_test.py) | `puppy-spawn-test-` | Fake engine script, test data and project. The finalizer shuts down jobs but does not remove the root. |
| [model_catalog_test.py](../tests/model_catalog_test.py) | `puppy-model-catalog-` | Model catalog probe fixtures; context manager removes the directory. |
| [backend_test.py](../tests/backend_test.py) | `opencode-usage-`, `puppy-normal-workspace-` | Synthetic usage database and ordinary-project deletion fixture; both removed in `finally`. |

These are ten creation sites without an explicit directory: one build site and
nine test sites. Python selects their temporary directory from `TMPDIR`,
`TEMP`, `TMP`, or platform defaults (normally `/tmp` on Unix). Neither `run.sh`
nor the supplied backend service template sets these variables. Moving managed
workspaces does not relocate these separate allocations.

The shared [test root helper](../tests/scratch.py) uses
`<checkout>/data/tests/`, with a six-hour stale-root sweep. The main backend
suite root also explicitly uses `data/tests/`. Managed workspaces allocated by
those suites now live inside each test's configured data directory. Remaining
literal `/tmp` paths in test payloads are mock/example paths or rejection
fixtures, not additional Puppy storage allocators.

Other temporary files and runtime storage already use explicit destinations:

| Area | Destination | Source |
| --- | --- | --- |
| Controller upgrade build output and smoke-test data | `<data>/upgrades/build-<random>/` | [backends.py](../puppy/backends.py) |
| Backend candidate smoke test | `<launcher-state-dir>/smoke-<random>/` | [upgrade.py](../backend/puppy_backend/upgrade.py) |
| Snapshot export/import/rollback staging and uploaded archives | `<data>/snapshots/` | [snapshots.py](../puppy/snapshots.py), [web.py](../puppy/web.py) |
| WebUI TLS validation/generation staging | `<data>/.web-tls-<random>/`, `<data>/.web-tls-generate-<random>/` | [web_tls.py](../puppy/web_tls.py) |
| Agent-note atomic replacement | Temporary file beside the destination note, following a note symlink to its target directory | [agent_notes.py](../puppy/agent_notes.py) |
| Database, configuration, logs and attachments | `puppy.db`, `config.json`, `puppy.log`, `uploads/` under `<data>` | [config.py](../puppy/config.py), [uploads.py](../puppy/uploads.py) |
| Browser profiles, HOME, downloads, logs, PID files, catalog and shared sign-in state | `<data>/browser/` | [browser.py](../puppy/browser.py), [browser_store.py](../puppy/browser_store.py) |
| Agent bridge sockets | `<data>/{browser,terminal,vnc,spawn,session}/agent.sock` | [Browser](../puppy/browser_agent.py), [terminal](../puppy/terminal_agent.py), [VNC](../puppy/vnc_agent.py), [spawn](../puppy/spawn_agent.py), [session](../puppy/session_agent.py) |
| Search, remote mirrors, leases and conflict storage | `<data>/search/`, `mirrors/`, `workspace_leases/`, `workspace/` | [search.py](../puppy/search.py), [workspace_sync.py](../puppy/workspace_sync.py), [workspace_links.py](../puppy/workspace_links.py) |
| Task review index and Git objects | Inside the managed task's `.git/` directory | [session_tasks.py](../puppy/session_tasks.py) |
| Atomic `.tmp` writes | Beside their destination config, catalog, TLS identity, handoff/upgrade record or build output | `.tmp` is a filename suffix, not a reference to `/tmp`. |
| Workspace sync staging | `.puppy-sync-tmp-*` beside destination files; temporary journal/cache files in the store metadata directory; `.puppy-keep-tmp-*` inside kept-conflict storage | [workspace_sync.py](../puppy/workspace_sync.py), [workspace_links.py](../puppy/workspace_links.py) |

The UI's New session → Scratch hint describes private workspace creation and
deletion/reset. It no longer warns of reboot expiry or names `/tmp`. Missing
workspaces are labelled "Scratch workspace missing"; task tooltips explain
that a missing copy cannot be recreated automatically. Reset transcript and
engine-handoff messages describe a fresh empty workspace without attributing
file loss to the host's temporary-file policy. The main README, backend README,
task documentation and agent notes describe the current data-directory layout.

This is a source inventory, not a live fleet disk scan. An operator can still
place a configured data directory or ordinary project under an OS-temp path.
Vendor engines/tools, Chromium, Git, notification commands and CLI updaters
can create their own temporary files. Puppy's environment handling does not
redirect those files. SQLite's internal temporary storage is also
implementation-dependent. These third-party runtime writes are outside the
managed-workspace change.

The previous OS-temp namespace is not accepted as managed storage by the new
allocator and is not automatically moved or removed. Existing normal project
sessions retain their working directories. Deploy the updated code/package on
each backend for new managed workspaces there to use the new location.
