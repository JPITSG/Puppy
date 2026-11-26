# Previews

An agent that builds something the user looks at in a browser - an HTML
mockup, a 3D model viewer, a wiring diagram, a report, a folder of images -
shows it with a preview: a tab beside the chat holding the page, served by
Puppy itself. Before previews, agents started `python3 -m http.server` on the
network for this and left those servers running, unauthenticated, for weeks
(seventeen were found on one machine, the oldest 17 days old). A preview is
the same thing done inside Puppy: no port of the agent's own, no server left
behind, the page reachable only through the signed-in console, and gone when
it is stopped or its session is deleted.

## The tools

Every prompt turn carries the `puppy_preview` stdio MCP bridge
(`python -m puppy.preview_agent`), turn-bound like the other bridges: a
mode-0600, same-uid Unix socket under `data/previews/`, refused once the turn
has ended.

- `show_preview(path, name?, root?)` shows a folder or one file in it. A
  folder opens at its `index.html`, or as a listing when it has none; a file
  opens itself, served from its folder. `root` names a larger folder to serve
  when the page loads files outside its own (`root` must hold `path`). The
  name is the tab's and the link's; the folder's or file's own when omitted.
  It answers three lines:

  ```
  Showing Plate viewer as Preview K3QZ in a tab beside this chat.
  Serves: hardware/plate/ (its index.html, or a listing of the folder)
  Markdown link: [Plate viewer](puppy-preview:K3QZ)
  ```

  Showing the same page again (the same root and entry) is the same preview -
  the same ID, link and address - and reloads the user's tab: that is how an
  agent shows a change. A new name given then renames it.
- `previews` lists the session's previews (ID, name, what each shows, when it
  was last shown, its link). It only looks, so Claude Code allows it up front.
- `close_preview(preview_id)` ends one: its tab and link stop working.

The MCP server's instructions are built in (`TOOL_INSTRUCTIONS`, within Claude
Code's 4000-character limit); the editable policy - Settings → System prompt →
**Preview guidance**, `config.DEFAULT_PREVIEW_SYSTEM_PROMPT` by default -
rides each engine's system prompt as the descriptor's `engine_guidance`
(Codex's `<puppy_preview_policy>` block). The default tells the agent to show
what it built with a preview instead of a web server of its own, to show it
again after a change, and to bind a project's own development server, when one
is really needed, to 127.0.0.1.

## What may be shown

A preview serves one folder, its root, which lies inside the session's working
folder (the folder itself included) or strictly inside the system temporary
folder. Never the whole home or system folder, Puppy's own data folder (unless
the session works in a folder Puppy manages there), an engine CLI's home, the
hidden entries directly in the home folder, or a system location such as
`/proc` or `/etc/ssh` - the same rules a shared file follows - and never a
hidden name anywhere in it. Every request is checked again as it is served: the
path is followed to where it leads, and a link leading out of the root, a
hidden name, Puppy's data folder, a CLI home or a system location is a plain
404. The file is then opened by walking that path with `O_NOFOLLOW`, so a link
swapped in meanwhile cannot redirect the read.

Every touch of the folder goes through `places`: a network drive that stops
answering costs that request a `503` within seconds, never a frozen Puppy.

## Serving

Files are read from disk at every load, never copied. Each answer carries an
ETag from the file's size, modification time and inode, and `Cache-Control:
private, no-cache`, so a reload revalidates (`304`) and a change is seen at
once; text, scripts, styles, JSON, SVG, STL, OBJ and WebAssembly up to 16 MiB
are gzipped when the browser accepts it. A file is at most 256 MiB, streamed in
2 MiB reads. A folder asked for without its slash is redirected (`301`,
relative) so the relative links of what opens there work. Byte ranges are not
served, so a video in a preview plays but does not seek.

### The sandbox, and why the address carries a token

Every answer carries

```
Content-Security-Policy: sandbox allow-scripts allow-forms allow-popups
  allow-modals allow-downloads allow-pointer-lock allow-presentation;
  frame-ancestors 'self'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Access-Control-Allow-Origin: *
```

and the console's frame carries the same `sandbox` attribute. Without
`allow-same-origin` the page runs in an origin of its own: neither it nor
anything it loads from the internet can read the console's cookies or storage,
touch the console's page or call its API, whatever the agent wrote. Its own
scripts, fetches of its own files (cross-origin from an opaque origin, hence
`Access-Control-Allow-Origin: *` on answers that carry no credentials), WebGL,
canvases, forms and downloads work; cookies, `localStorage`, `sessionStorage`,
IndexedDB, service workers and workers do not, which the tools tell the agent.
Only Puppy's own page may frame it.

A sandboxed page's own requests carry no cookie (the console's is
`SameSite=Strict`, and the page's origin is not the console's), so the files
cannot sit behind the sign-in. Each preview therefore has a secret token - 192
random bits - which its address carries: `/preview/<backend>/<token>/<path>`
on the controller. Only the signed-in console reads it, from `GET
/api/sessions/{sid}/previews`; the agent and the transcript only ever see the
four-character ID. Anyone given the address could open the preview until it is
stopped, as with any capability link, so it is treated like the session's own
link: it never appears in a URL the console records, and reaches the browser's
own history only when the page is opened in a window of its own or its own
links are followed inside the frame. A wrong token is the same plain `404 Not
found` as any missing file, naming nothing about the Puppy that answered.

## Where previews live

The exact `session_previews.<sid>` meta record holds a session's previews:

```json
{"format": 1, "items": [{"id": "K3QZ", "token": "<32 URL-safe characters>",
  "base": "cwd", "root": "hardware/plate", "entry": "", "name": "Plate viewer",
  "created_at": 1795700000.0, "shown_at": 1795700300.0}]}
```

`base` is `cwd` (the root relative to the session's working folder, so a
project move or a workspace promotion carries the preview along) or `temp`
(relative to the temporary folder, never empty); `entry` is what opens in the
root, `""` for the root itself and ending in `/` for a folder in it. At most 20
items, oldest first, ids and tokens unique on the node. The record is
validated at startup on both runtimes and in a backup, never repaired, and
deleted with its session - a folded task's previews go with the task, whose
folder goes too. It is in backups with the rest of the database.

The preview policy is the optional exact `preview_guidance` meta record,
`{"format": 1, "text": "..."}`, absent while the default stands; Settings
saves the default by deleting it. It is a meta record rather than a
`config.json` key so that adding it changed no node's config shape (like the
background task limit), validated the same way.

## Routes

On both runtimes, behind the additive `session-previews` capability:

- `GET /api/sessions/{sid}/previews` - `{previews: [{id, name, token, entry,
  folder, where, created_at, shown_at}]}`, behind the sign-in.
- `DELETE /api/sessions/{sid}/previews/{id}` - ends one; the console's Stop.
- `GET|HEAD /api/previews/{token}/{path}` - a file, behind the node's own
  authentication: what a controller relays for a backend's preview.

On the full runtime only, `GET|HEAD /preview/<backend>/<token>/<path>` answers
without a sign-in, the token being the key: backend `0` is this instance's own,
any other is relayed as a stream (`backends.relay_preview`) with the
controller's own headers, the backend's ETag, length and gzip passed through
and its redirect only when relative. An unknown backend is the preview's 404;
an unreachable one a plain `503` that names no backend.

Each `show_preview` call and each close sends the session's sockets (and, for
this instance's sessions, the updates stream) an ephemeral `preview_activity`
event: `{preview_id, event, closed?, name?}`. The `event` id lets a console
that hears it over both of its sockets act once.

## The console

A preview tab (`p:<backend>:<ID>`, kept in the saved layout with its session)
is the managed browser's surfaces around a sandboxed frame: the bar with
Reload, the folder in full, a window of its own and Stop; the identity pill
(`Preview K3QZ`, its copy button), the session pill that opens its session,
and the state pill (`Loading…`, `Live`, `Ended`, `Unavailable`). An
announcement opens a new preview beside its chat (Main's tab for a task)
without taking focus, reloads an open one where it stands, and ends a closed
one. Reload and a reshow replace the frame, so they add no step to the
browser's history. Stop asks first (`Stop Preview K3QZ?`, destructive). A
closed tab leaves the preview running; its link opens it again.

Every change to the workspace rebuilds it, and a frame taken out of the page
loses its page. A preview view is `keepAlive`: where the browser moves a node
without resetting it (`Element.moveBefore`, current Chromium) its root waits in
a hidden parking place during the rebuild and moves to its new place, so a tab
switch leaves the 3D model at its angle; elsewhere it reloads, as before.

`[name](puppy-preview:K3QZ)` in an answer is a link that opens the tab; one
read of the session's previews gives every link on the page its address, so a
middle or modified click opens the page in a browser tab of its own, and a
link to a preview that is no longer open says so. `show_preview`'s tool card
offers the preview in its folded head and under its result, like a shared
file's card. A session's own window hands a preview to the console it came
from.

## Tests

`tests/previews_test.py` covers what may be shown, the record's exact shape,
serving on both runtimes (the headers on every answer, index pages, listings,
the folder's slash, links out, hidden names, revalidation, gzip, HEAD, a wrong
token, a restore), a controller relaying a real headless backend, the bridge,
every driver and the policy record. `tests/previews_ui_test.js` runs the
console's side against the fake DOM, and `python3 tests/console_browser_test.py
--previews-only` drives it in real Chromium (`PREVIEW_SHOT=<dir>` saves its
screenshots): a page that reports, by `postMessage`, that it fetched its own
file while cookies, storage, the console's page and its API were out of reach,
the frame filling its stage on a desktop and a phone in both themes, a tab
switch that leaves the page as it was, a reshow that reloads it, the link, a
wrong token and Stop. `tests/dead_mount_test.py` checks the preview routes and
tools against a drive that hangs.
