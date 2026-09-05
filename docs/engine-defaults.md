# Engine defaults

Each backend stores an independent set of starting permissions, model and effort
for Claude, Codex and OpenCode. Open **Engine defaults…** from any of the desktop
composer's three dropdowns, or from the session menu (also available on phones).
The editor uses saved values initially; **Use this conversation’s choices** copies
the choices shown for the next prompt, including queued changes. Reset fills the
engine's original choices. Cancel discards edits; Save defaults persists them.

New sessions prefill from these values. Switching or reseeding an engine captures
the destination engine's defaults when the request is made, even if the switch
must wait in the queue. Existing sessions, queued switches and task inheritance
keep their own choices. A task starts with Main's configuration. Engine-native
defaults remain a separate choice: an empty model or effort lets the engine decide.
Defaults do not alter the engines' config files or one-shot spawned jobs.

`engine-defaults` is an additive capability on both runtimes. Authenticated
`GET /api/engines/{key}/defaults` returns an `engine` object with picker catalogs,
`session_defaults`, and `factory_defaults`. `PUT` replaces that engine's complete
`{permission_mode, model, effort}` row and returns the same response. It validates
against the backend's driver catalog and publishes through the engine state topic.
The ordinary engines payload carries both defaults objects. Older backends keep
their existing behavior and do not show the editor.

On session creation, omitted fields use saved defaults; explicit empty model and
effort fields still mean engine default. Unavailable choices are refused, including
a retired saved model or unsupported model/effort combination. They remain visible
in the editor until the user repairs them. Backup import validates exact shape and
text independently of live catalogs so an offline engine does not prevent restore.

## Applying this config shape

Before starting this version on an existing node, manually add the following
`defaults` field inside `engines` in that node's private `data/config.json`, keeping
every other current field. Do this for each node being updated while it is stopped.
Fresh installations initialize it automatically; existing files are never migrated.

```json
"defaults": {
  "claude": {"permission_mode": "", "model": "", "effort": ""},
  "codex": {"permission_mode": "", "model": "", "effort": ""},
  "opencode": {"permission_mode": "", "model": "", "effort": ""}
}
```

Empty permission delegates to the driver's original permission default. Settings
backup/restore includes all three rows and rejects archives with the old config
shape. Keep a backup from before this change for rollback to the previous version;
that version requires its previous config shape. This task does not update live
node configs or deploy code.
