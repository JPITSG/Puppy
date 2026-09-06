# Completion alerts

Settings offers **On success** and **On failure** commands with a shared **Run on**
backend. Save persists both commands and the backend together. The enabled switch
and sidebar bell take effect immediately and control both outcomes. A blank
command skips its outcome; two blank commands hide the bell. Each command accepts
up to 1,000 characters. Test success and Test failure run the corresponding draft
without saving, including when alerts are disabled.

An alert fires when a session and its queue finish, using the activity block's
completion status: `ok` selects success, `error` selects failure, and `interrupted`
runs neither. Remote completions follow the same selection through the controller's
durable completion cursor; an open browser is not required.

Both commands support the existing shell-quoted placeholders and matching
`PUPPY_*` environment variables: `backend`, `session`, `engine`, `model`, `status`,
`duration`, `duration_hms`, `cwd`, and `id`. Tests supply `ok` or `error` consistently
to both `{status}` and `PUPPY_STATUS`. Execution still uses `/api/notify/exec` on
the selected backend, whose shell surface must be enabled.

## Persisted configuration

Every full instance and headless backend requires exactly this `notify` section
in its private `data/config.json` (or the data directory selected by `PUPPY_DATA`):

```json
"notify": {
  "enabled": false,
  "backend": 0,
  "success_command": "",
  "failure_command": ""
}
```

Fresh installations initialize this shape. Before deploying this change to an
existing runtime, manually replace `notify.command` with `success_command` and
`failure_command`, preserving `enabled` and `backend`. Copying the previous command
to both fields preserves its previous outcome coverage; assign different commands
or leave one empty to change it. Update each runtime being upgraded, including
headless nodes where this section is unused. Runtime loading and backup import
reject the previous shape; there is no automatic conversion. Newly exported
backups preserve both commands, and failed saves/restores retain the prior values.

The controller's authenticated `GET /api/notify` returns all four settings.
`POST /api/notify` requires exactly `backend`, `success_command`, and
`failure_command`; `POST /api/notify/toggle` accepts `enabled` separately.
`POST /api/notify/test` requires `backend`, `command`, and `status` (`ok` or `error`).
The shared backend execution route and completion event protocol are unchanged.
