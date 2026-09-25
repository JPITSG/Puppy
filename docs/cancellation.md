# Cancellation of console operations

The cancellation audit covers the console and public sign-in scripts, their
HTTP and WebSocket handlers, background jobs, subprocesses, worker threads,
workspace ownership, and both execution runtimes. The following gaps are fixed.

| Surface | Control and effect | Point after which preparation cannot be cancelled |
| --- | --- | --- |
| New browser | Cancel launch, terminate startup and close its new identity/profile | Successful handoff to the browser tab |
| Browser navigation | Reload becomes Stop loading; sends `Page.stopLoading` and cancels pending viewer navigation tasks | Does not undo a page's completed actions |
| New session | Cancel engine/catalog preparation | Directory/session creation; remote creation commits before sending its first mutation to another backend |
| New task | Cancel cloning, copying and attachment adoption; clean up before any task prompt starts | Task record and prompt dispatch |
| Task review/apply | Cancel Git diff, apply-check and conflict-snapshot preparation; preserve Main | File apply, review-baseline update or dispatch of conflict resolution |
| Task removal/folding | Cancel Git inspection and transcript condensation | Appending the archive and deleting the task |
| Move scratch project | Cancel copying; remove the unused destination, retain the original and its session path | Session path commit; source reclamation then finishes |
| Move project | Cancel a cross-filesystem copy; remove the unused destination, keep the original and every session's path | The rename, or the session paths' commit after a copy; removing the original then finishes |
| Backup export | Cancel database copy, file staging, hashing and compression; discard staging | Issuing its one-use download; the browser's download controls own the transfer |
| Backup import | Cancel upload, extraction, validation and scratch preparation | Detaching clients and replacing live state; commit/rollback completes |
| Add/edit/test backend | Cancel network probes before storing the new connection | Saving the registration/connection; test observations are advisory |
| Upgrade backend | Cancel discovery, package build and candidate self-test | Signed upload/restart/health-check handoff; that pipeline completes or rolls back |
| Engine refresh | Cancel the requesting wait | Shared cache probes may continue for other consumers |
| Engine CLI update | Cancel preflight; Stop update ends an already-running updater process group, retains output and rechecks its version | Stopping does not undo package files already written by the vendor updater |
| Completion-command test | Cancel the local or relayed command process group | Completed command effects remain; older remote nodes cannot revoke a submitted command |
| Listener verification | Cancel preparation or the direct browser proof; no settings save or restart follows | Settings commit and graceful restart handoff |
| Workspace sync | Cancel manifest discovery | Replacing an expired provider lease, or reconciliation begins writing preserved conflict copies, applying batches and updating its baseline |
| Side question | Ask becomes Cancel question, including on other connected consoles; withdraw only the pending native question | An answer already received remains; cancellation never stops or steers the main turn |
| Full-history search | Search becomes Cancel; Escape, a replacement search or closing its tab aborts outstanding browser waits | Read-only backend queries may finish independently |
| Sign-in/setup | Stop waiting or Escape aborts the browser request, preserves input and suppresses late navigation | Does not reverse an account/session already created; retry first rechecks sign-in/setup state |

Operations that already had an exit were checked: running agent turns and
compact use Stop; queued/held work has removal controls; uploads have removable
chips and discard cleanup; VNC connects have request-scoped cancellation and
late-success reclamation; terminal startup has tab close and late-ID cleanup;
VNC/browser/terminal reconnections stop when their views close. Read-only sheets,
menus, history and Settings loads remain dismissible/navigable and guard against
writing obsolete results into disconnected views. Small confirmed writes,
deletions, permission responses and published restart handoffs are commits,
not reversible preparation. A queued graceful restart still follows the
existing drain and handoff rules.

## Shared implementation

`puppy/operations.py` owns the additive `operation-cancel-v1` contract. Only
explicitly decorated handlers participate. The browser passes a fresh random
`X-Puppy-Operation` header and uses authenticated
`GET`/`DELETE /api/operations/{id}` on the executing backend. IDs are scoped to
the authenticated principal. A cancel arriving before its POST reserves a
cancellation tombstone; duplicate POSTs cannot start work again. Records are
bounded, transient, expire ten minutes after completion and are not persisted
or included in backups. Active records are never evicted.

Cancellation is cooperative. Worker copies, Git processes and compressed
archive streams check it; cancellation-safe asynchronous waits cancel and drain
their child. Threads retain their owning operation and locks until cleanup
finishes. `commit()` arbitrates cancellation under the same lock before an
irreversible step. A refused cancellation is presented as Finishing with Close,
not as success. Accepted cancellation keeps the dialog visible until the owner
reports cleanup complete. Failed cancellation remains visible and retryable.
A lost HTTP reply triggers best-effort cancellation without claiming rollback.
Waiting for session, project or sync ownership is also cancellable; a waiter
never releases another operation's lock. Browser launch failure waits for its
closed profile to be removed before completing cancellation.

`operationRequest()` is the shared console progress surface. Cancel, Escape and
backdrop dismissal use the same handler. It is deferred: the dialog is built
only if the request is still running `CANCEL_DIALOG_DELAY` (five seconds) later,
so an operation that answers sooner neither interrupts nor takes the focus, and
no operation state is polled for it. A dialog that does open reads that state
immediately, because it arrives in the middle of the work rather than at its
start. The read-only wait uses the same delay. Control queries do not force
sign-in navigation while a restore is replacing authentication state. The API
helper combines caller abort signals with deadlines, including while reading JSON.
The separate public auth page exposes no console assets or instance identity.

The other additive capabilities are:

- `browser-navigation-stop`: viewer socket message `{type:"stop_loading"}`.
- `engine-upgrade-cancel`: `DELETE /api/engines/{key}/upgrade` with the exact
  observed `{started_at}` from `upgrade_started_at`; stale requests are refused.
  `upgrade_stopping` reports an accepted stop until the run settles.
- `side-question-cancel`: `DELETE /api/sessions/{sid}/ask` with `request_id` and
  `expected_turn_id`. Readiness includes `pending_request_id`. Cancellation is
  serialized with stdin writes, recorded as an append-only unanswered result,
  excluded from follow-up history, and ignores late answers.

All execution routes are shared by the full WebUI and headless backend. The
controller-only listener proof also has authenticated
`DELETE /api/settings/bind/prepare` with `{token}` to release its verifier.
Older peers are capability-gated and retain their existing behavior. No config,
database, archive or client-storage shapes changed.

## Verification

`tests/operations_test.py` uses private workspaces, real local HTTP, stub
processes and native-control fixtures. It covers early/duplicate cancellation,
commit races, worker draining, task/browser/copy cleanup on both authenticated
runtimes, cookie-authenticated backup/restore staging cleanup, command child
termination, updater output retention and side-question isolation.
`tests/operation_ui_test.js` exercises real request and modal code against a
hand-turned clock: the deferred dialog and the quick operation that never opens
one, keyboard and backdrop cancellation, cleanup waits, failed cancellation,
commit races, older nodes, abort deadlines and browser loading controls.
Existing task, auth, UI, backend, browser, snapshot, workspace, notification and
listener suites cover the surrounding behavior without paid engine turns. The
separate existing `side_question_test.py` is a live integration test and uses
subscription quota.
