# Engine defaults

Each backend stores an independent set of starting permissions, model and effort
for Claude, Codex and OpenCode. Open **Engine defaults** from any of the desktop
composer's three dropdowns, or from the session menu (also available on phones).
The editor uses saved values initially; **Use this conversation’s choices** copies
the choices shown for the next prompt, including queued changes. Reset fills the
engine's original choices. Cancel discards edits; Save defaults persists them.

New sessions and tasks prefill from these values. Switching or reseeding an engine
captures the destination engine's defaults when the request is made, even if the switch
must wait in the queue. Existing sessions, tasks and queued switches keep their
own choices. A new task initially selects Main's engine and loads that backend's
saved model, effort and permissions; changing engines loads its saved defaults.
The prepared task's choices survive catalog/default refreshes. Engine-native
defaults remain a separate choice: an empty model or effort lets the engine decide.
Defaults do not alter the engines' config files or one-shot spawned jobs.

`engine-defaults` is an additive capability on both runtimes. Authenticated
`GET /api/engines/{key}/defaults` returns an `engine` object with picker catalogs,
`session_defaults`, and `factory_defaults`. `PUT` replaces that engine's complete
`{permission_mode, model, effort}` row and returns the same response. It validates
against the backend's driver catalog and publishes through the engine state topic.
The ordinary engines payload carries both defaults objects; the console requires
`session_defaults` when preparing a new session, task or engine switch.

On session or task creation, omitted choice fields use saved defaults; explicit
empty model and effort fields still mean engine default. Unavailable choices are refused, including
a retired saved model or unsupported model/effort combination. They remain visible
in the editor until the user repairs them. Backup import validates exact shape and
text independently of live catalogs so an offline engine does not prevent restore.

## Persisted shape

`engines.defaults` contains exactly these rows in `data/config.json`:

```json
"defaults": {
  "claude": {"permission_mode": "", "model": "", "effort": ""},
  "codex": {"permission_mode": "", "model": "", "effort": ""},
  "opencode": {"permission_mode": "", "model": "", "effort": ""}
}
```

Empty permission delegates to the driver's original permission default. Fresh
installations initialize this shape. Existing config and backup archives must
already contain the complete current shape; runtime conversion is not supported.
