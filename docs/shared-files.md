# Shared files

An agent can hand the user a file as a link in its answer - "The latest STL
file is [here]" - that downloads with one click. It asks for this explicitly:
every prompt turn carries Puppy's `puppy_files` bridge, whose one tool,
`share_file`, copies a file the agent names into the session's own storage
and answers the Markdown link the agent then writes. Nothing reaches the user
that the agent did not choose to share, and nothing is served from where the
agent works: the link always serves the copy taken at that moment.

## The tool

`share_file(path, name?)` takes one file's path - absolute, or relative to
the session's working folder - and an optional name for the download (the
name the agent gave the path otherwise; a symbolic link's own name, never its
target's). It answers three lines:

```
Shared model.stl (1.2 MiB) for download in this conversation.
Markdown link: [model.stl](puppy-file:f0123456789abcdef0123456789abcdef)
Put that link in your answer where the user should click; any link text works.
```

The link's text is the agent's to choose; its target is the share's id. The
tool's own guidance (built in, like the session bridge's, with no setting)
tells the agent to share what the user asks for or what it made for them, to
use only links the tool returned in this conversation, never to share
credentials, and that the copy is taken when it shares: a changed file is
shared again. Sharing the same unchanged file under the same name again
answers the earlier share instead of a second copy.

The bridge is the same shape as the browser, terminal, remote screen and
session bridges: a stdio MCP child (`python -m puppy.files_agent`) started by
the engine with the turn's identity, talking to the node over a mode-0600
same-uid Unix socket in a mode-0700 `data/files/`. The node refuses a call
from a turn that is no longer running, an unknown tool or field, and any call
while a backup or restore is in progress. Claude Code names the tool
`mcp__puppy_files__share_file`, Codex `puppy_files.share_file`; the console
recognises the share tool's card by the name's ending.

## What may be shared

A regular file inside the session's execution folder - the folder the agent
works in: a linked workspace's mirror, a task's copy, a scratch workspace - or
inside the system's temporary folder. A folder is shared as an archive the
agent makes first. Refused whatever the session's folder:

- Puppy's own data folder, except files inside a session folder Puppy manages
  there (a scratch workspace, a task's copy) when the session works in it;
- the engines' CLI homes (`<temp>/puppy-cli-<uid>/`, where the footer's CLI
  terminals sign in);
- hidden files and folders directly in the home folder, where sign-ins, keys
  and tokens live (`~/.ssh`, `~/.claude`, `~/.codex`, `~/.config`, ...),
  unless the session itself works inside that entry;
- `/proc`, `/sys`, `/dev`, `/run`, `/etc/shadow`, `/etc/gshadow` and
  `/etc/ssh`.

The path is resolved once, those rules are applied to the result, and the
file is then opened by walking the resolved path from the allowed folder one
component at a time with `O_NOFOLLOW`, so a symbolic link swapped into the
path afterwards fails the share instead of redirecting it. The opened file
must be the one examined (same device and inode) and a regular file; a FIFO
is never waited on.

These rules keep a careless or misled share - an instruction planted in a
page the agent read, say - away from where secrets live. They are not a wall
against the agent itself: it runs as the service's user, can read what that
user can read, and could copy a secret into its folder first, exactly as it
could paste one into its answer. Whatever is shared goes only to the signed-in
user's own browser.

Limits: 256 MiB per file, 1 GiB of shares per conversation and 20 shares per
turn (a reused share does not count). One session shares one file at a time,
so its limits hold under parallel calls. A refusal says what was wrong and
what to do instead, in words for the agent.

## Storage

A share is a one-file directory beside the session's attachments and tool
pictures: `data/uploads/<sid>/f<32 hex>/<name>`, the directory mode 0700 and
the file 0600, written with `O_EXCL | O_NOFOLLOW` and fsynced. The id is
random and is never an upload id, so:

- the orphan sweep, which only takes upload ids no message names, never takes
  a share;
- the composer's discard route and the attachment preview route never serve
  or remove one;
- deleting the session deletes its shares with the rest of its storage;
- backups carry them with the uploads tree, counted against the archive
  limit like any upload, and a restore brings them back;
- folding a task into Main moves the task's shares into Main's storage under
  the same ids, because the folded archive keeps the task's answers - links
  and all - and renders them in Main's view. Removing a task without folding
  deletes them with the task.

While a copy is being made it counts as an active upload, so a backup never
starts beside a half-written file; the count belongs to the copy until its
thread ends, even if the tool call stopped waiting for it.

There is no persisted record beyond the files themselves, so no database,
config or snapshot shape changed.

## Routes

Both runtimes serve them, behind the additive `session-shared-files`
capability:

- `GET /api/sessions/{sid}/files` lists the session's shares, newest first:
  `{files: [{id, name, size, created_at}], more}` (at most 500, `more` when
  there are others), `Cache-Control: no-store`.
- `GET|HEAD /api/sessions/{sid}/files/{id}` serves one share, only as a
  download: `Content-Type: application/octet-stream`,
  `Content-Disposition: attachment; filename="<ASCII>"; filename*=UTF-8''<name>`,
  `X-Content-Type-Options: nosniff`,
  `Content-Security-Policy: default-src 'none'; sandbox`,
  `Cache-Control: private, no-store`, `Cross-Origin-Resource-Policy:
  same-origin` and `Referrer-Policy: no-referrer`. Ranges and conditional
  requests work as for any file. An id this session does not hold is a 404
  ("this file is no longer available"); a directory that fails its checks (not
  the service's, not exactly one regular file) is a 409; a restore in progress
  is a 503.

A shared HTML page or SVG is therefore always a file to save, never a page
that runs on the console's origin. A controller relaying a backend's share
does not use its buffering relay (which keeps only a content type and holds
the whole answer): `backends._proxy_download` streams it, forwards ranges,
and answers with its own download headers whatever the backend sent, keeping
only an `attachment` disposition, the length, range, date and tag headers.
Before the backend answers, the usual failover and offline marking apply;
once it has, a failure ends the connection without the response's own end,
so the browser sees a failed download rather than a short one complete, and
the backend's health stays the health worker's to judge. Every buffered
relay now also carries `nosniff` and the sandboxing policy.

## In the console

`md()` keeps DOMPurify's own allowed-link rule with `puppy-file:` added, and
`decorateMarkdownLinks(root, source)` turns such a link into a download of
`sessions/<sid>/files/<id>` on the session's own backend where the words
belong to a session: the assistant's answers, a folded task's archive in Main
and a scheduled run's answer whose conversation this console knows. Anywhere
else, or from a backend without the capability, the link is reduced to its
words like any other non-web link. The link keeps the agent's words, followed
by the drawn download glyph, tied to the last word by a word joiner a copy of
the answer leaves out.

One read of the session's shares (`GET sessions/<sid>/files`, every link of a
page in one read, again only for an id it did not know) names each link
("Download model.stl · 1.2 MiB") and marks a link whose file has gone as
unavailable: no address, the help colour, and a press that explains instead
of downloading. A read that fails, or a list cut short, proves nothing about a
file, so the link stays a plain download. A press on a link to an unreachable
backend explains that too.

The share tool's own card - recognised by its name, never by a link quoted
in another tool's output - offers the download in its folded head, beside its
status, without growing the head or folding the card, and puts the file's card
under the result, named by the stored name rather than an engine's escaping
of it.

## Tests

- `python3 tests/shared_files_test.py`: what may be shared, the storage
  rules, both runtimes' routes, the controller's streaming relay against a
  real headless backend and a hostile stub, the bridge and its socket, the
  stdio MCP server, every driver carrying the bridge, and a fixture engine
  calling the bridge's MCP server through the real runner.
- `python3 tests/session_tasks_test.py`: shares moving to Main with a folded
  task, and gone with an unfolded one.
- `node tests/shared_files_ui_test.js`: the link rule, the reads, the
  unavailable state, the presses and the tool card's chips.
- `python3 tests/console_browser_test.py --shared-files-only`: real layout on
  a desktop and a phone in both themes and real downloads checked byte for
  byte.
