# VNC connections

Puppy is the VNC client. `puppy/vnc.py` dials a remote RFB server over an
ordinary TCP socket, decodes its framebuffer on the backend, and sends each
console viewer only the rectangles that changed. Nothing is installed, spawned
or proxied: the whole client is `asyncio`, `zlib` and a small DES for RFB's
legacy challenge.

The same surface also dials Windows Remote Desktop: an RDP connection is an
instance of this one with another wire beneath it, sharing the IDs, the
catalog, the viewer protocol below, the pane and the agent's tools. See
[Remote Desktop connections](rdp.md). Every catalog entry and status payload
names its `protocol`, `vnc` or `rdp`.

## What is spoken

| Layer | Supported |
| --- | --- |
| Protocol | RFB 3.3, 3.7, 3.8 (the client offers the highest the server names) |
| Security | `None` (1) and `VNC Authentication` (2) |
| Pixel format | client-imposed: 32 bpp, depth 24, little-endian, true colour, red/green/blue shifted 0/8/16 |
| Encodings | ZRLE (16), Hextile (5), Zlib (6), RRE (2), CopyRect (1), Raw (0) |
| Pseudo-encodings | DesktopSize (-223), LastRect (-224) |
| Input | `PointerEvent`, `KeyEvent` |

The encoding list is sent in that preference order. Tight is deliberately not
offered: its JPEG sub-encoding would need an image decoder, and ZRLE is
supported by every server that supports Tight. Cursor pseudo-encodings are not
requested either, so the server keeps compositing the pointer into the screen
and the picture stays complete.

The pixel format is chosen so a decoded pixel is *already* what a browser
canvas wants: little-endian 32-bit true colour shifted 0/8/16 puts R, G, B and
one pad byte in memory in that order. Only the pad byte is forced opaque, and
ZRLE's three-byte CPIXELs are widened in four C-level passes rather than per
pixel. Packed palettes are expanded through a 256-entry table built by
doubling, so a tile row costs one `join` over its packed bytes.

## Viewer protocol

`GET /api/ws/vnc/{id}` carries JSON status in both directions and binary
damage rectangles from the node. Every binary frame starts with a 10-byte
little-endian header:

| Offset | Field |
| --- | --- |
| 0 | kind: 1 = pixels, 2 = copy |
| 1 | reserved, 0 |
| 2, 4 | destination x, y |
| 6, 8 | width, height |

A `pixels` frame is followed by `width × height × 4` RGBA bytes, which the
console wraps in an `ImageData` and paints with `putImageData` - no copy, no
codec, no decompressor in the page. A `copy` frame carries only a source x/y
and is painted with a canvas-to-canvas `drawImage`, so a window drag costs no
pixels at all. aiohttp's permessage-deflate compresses both, and large frames
are deflated in a worker thread rather than on the event loop.

Node to viewer, as JSON: `status` (the full instance payload), `size` after a
desktop resize, `gone` when the remote session ends, and `error`. Viewer to
node: `viewer_active`, `pointer`, `wheel`, `key`, `text`, `cad`, `refresh` and
`release_keys`. Pointer coordinates are normalized to 0..1 so the console never
has to know the framebuffer's size; the node maps them onto the pixel grid and
clamps them.

Active viewers also receive an additive `throughput` JSON message with
`bytes_per_second`, sampled about once a second using a monotonic clock.
This counts incoming RFB bytes read from the server before decompression,
including RFB headers but excluding TCP/IP overhead and the separate viewer
WebSocket traffic. The sender reports zero during quiet intervals, resets its
baseline on resume or redial, and sends no periodic samples to inactive viewers.
The console displays this rate in a pill beside dimensions and FPS, using
B/s, KiB/s, MiB/s or GiB/s. Each pill stays hidden until its readings are
available, and hides again when the viewer is hidden or disconnected. Valid
zero readings remain visible. Older backends that do not send the additive
message leave the throughput pill hidden.

Nobody watching means nobody paying. With every viewer inactive - a hidden
pane, a backgrounded window, or no viewer at all - and no agent tool call in the
last two minutes (`AGENT_ATTENTION`, the same rule as RDP's Suppress Output),
the node stops issuing `FramebufferUpdateRequest`s entirely, so an unwatched
connection costs one idle socket. A viewer that returns is settled with the whole screen in bands, and so
is one whose backlog had to be dropped. A refresh freezes one complete picture
and the socket writer sends its bands progressively, outside the 8 MiB delta
queue budget. Later deltas follow the frozen picture. Overflow discards pending
pixels and wakes the writer to take a fresh picture after the in-flight send
drains, even if the server sends no further damage. Resize and viewer inactivity
also discard pending pixels so an older picture cannot overwrite the new one.

## Lifecycle

Instances are node-owned and in-memory, with a random four-character A-Z0-9 ID
like browsers and terminals. `GET`/`POST /api/vnc/instances`,
`DELETE /api/vnc/instances/{id}` and the viewer socket are registered in
`register_execution_api`, so the full console and the headless backend serve
them identically, behind the additive `vnc` and `vnc-instances` capabilities.
The catalog rides the node state stream as the `vnc_instances` topic.

With the additive `vnc-connect-cancel` capability, the create POST accepts an
optional `request_id` (16–80 ASCII letters, digits, underscores or hyphens).
`DELETE /api/vnc/connect/{request_id}` cancels that attempt during TCP dial or
handshake and closes any connection whose successful reply raced cancellation.
It also records cancellation arriving before the POST, so request ordering
cannot start a dial the user already cancelled. IDs are single-use within the
retention window; retries use a fresh ID. Records are memory-only, contain no
passwords, and expire 120 seconds after completion (pruned on new requests),
with a cap of 512 records; pending attempts are never evicted. Repeated DELETEs
are harmless. Both runtimes authenticate the route through the execution API.

New VNC connection also lists the connections the chosen backend already
holds - its catalog, then one fresh `GET /api/vnc/instances` - and a click on
one opens its tab without dialling anything, so a screen an agent connected
while no console watched, or one opened from another machine, can be shown
here (the same list Puppy's New browser and New terminal dialogs keep for
their kinds).

The connection modal keeps Cancel enabled; its close hook also handles Escape
and the backdrop. Dismissed attempts cannot open tabs or show success notices.
Older backends use the existing create route, with a successful late reply
reclaimed through instance DELETE. Failed or timed-out creates also request
cancellation on capable nodes. The console allows 45 seconds for the create
request, covering the 15-second dial and 20-second handshake bounds.

A connection's password is held only by the process that dialled the server and
only while the instance exists. The VNC client never persists it or returns it
through a route. A password included in an agent prompt or tool call can still
be retained in the conversation and its backups. To keep it out of the
transcript, connect through the VNC tab's dialog and give the agent only the
connection's four-character ID.

The `vnc.idle_timeout` setting (Settings → Timeouts) drops an unwatched
connection but keeps its identity, so reopening the tab dials the same server
again. Closing the tab closes the connection. A dropped socket, a refused
handshake and a malformed update all end that one connection with a reason the
pane shows; nothing else on the node is affected, and a VNC connection never
blocks a turn, a backup or a node upgrade.

Disconnect invalidates queued damage and cached full-screen snapshots before
the `gone` notice. A disconnected instance never settles a viewer from its
cached framebuffer. The console likewise ignores damage until a successful
`status` says the server is connected, so bytes already in flight cannot erase
the reason or restore a Connected pill. The first disconnect paints the
Disconnected pill immediately. A `gone` closes that viewer socket; automatic
redials retain their budget until an RFB connection succeeds, rather than
resetting it whenever the viewer WebSocket opens.

## Agent tools

The same connection is what an engine turn works with. `puppy/vnc_agent.py` is
the turn-bound stdio MCP bridge every driver receives, behind a mode-0600,
same-uid Unix socket into the running node: `screens`, `connect`, `screenshot`,
`move`, `click`, `drag`, `scroll`, `type`, `press`, `wait` and `disconnect`.
Nothing in it speaks RFB - each tool is one high-level action on a connection
the node already owns, so there is no second path to the server.

An agent has no viewer, so for two minutes after each of its calls the picture
keeps coming as if one watched, and between its calls the framebuffer stays the
screen as it is. `sync()` issues one request by hand and waits for the answer;
a server that sends nothing means the framebuffer is already current. Every
applied update is logged with the rectangles it painted (the last 64 updates,
up to 16 rectangles each, else their bounding box), so `watch()` can wait on
one region of the screen and pass over a clock on another monitor. Its
`stable` mode returns once the region has been unchanged for the quiet
interval - silence proves that only once a whole interval has passed with no
answer, so a budget shorter than the interval is never called stable - and its
`change` mode first waits for the region to change and then for quiet. A
change small enough and close enough to where the pointer was or went (64
pixels) does not count as that change: the server draws the pointer into the
picture, since Puppy never asks for the cursor as a separate shape. A log that
has lost track counts as the whole region changing.

Every action - `move`, `click`, `drag`, `scroll`, `type`, `press` - and `wait`
takes a `screenshot` option, `{}` for its defaults, so one call acts and shows
the result. The picture shows the region of the last picture this chat
received of the screen (or the one passed, `{}` being the whole screen), and
the action's wait watches that region: by default for a change caused after
the action - updates that had stopped coming are caught up with first, so a
change made while nobody watched is not taken for the action's - then for
`settle_ms` (300) of quiet, within `timeout_ms` (2000). The text says what
changed and where (`Changed: 10x10 at 150,80`) or that nothing changed beyond
the pointer, which is a cheap sign the action missed. `screenshot` and `wait`
take `until: change` as well, meaning a change since the chat's last picture.
A picture identical to the last one this turn received of the same region and
scale is answered with a short note instead, unless `force` is set.

An argument a tool does not take is refused by name with the list of those it
does, never silently dropped; the names agents were seen writing for real
ones (`amount` and `notches` for scroll's `clicks`, `clicks` for click's
`count`, `from_x`/`from_y` for drag's start, `vnc_id` for `screen_id`,
`quiet_ms` and `settle_ms` for each other, wait's old `until_stable`) are
taken as what they meant. `press` takes a shortcut written as one key
(`Control+Alt+Delete`, `Ctrl++`), the punctuation keys by name (`minus`,
`plus`, `comma`, `slash`, ...) and the keypad (`Numpad0`-`Numpad9`,
`NumpadAdd`, `NumpadEnter`, ...; RDP sends the keypad's own scan codes).

A screenshot is a PNG built from the framebuffer with `zlib` and nothing else.
The rows are strided out as whole 32-bit pixels through one `memoryview` cast
and their pad byte is dropped in a single C-level pass - on the event loop,
between two updates, because the reader paints into that same buffer and a
worker reading it meanwhile could mix two frames - and only the deflate of
that copy runs in the `jobs` pool: no image library, and no per-pixel Python,
exactly like the decoders. A region larger than `max_dimension` (1920 by
default, so 1080p is 1:1, and never above 2000) or than 3.75 megapixels is
reduced by a whole factor - models take up to 2576 pixels, but once a
conversation holds more than twenty images every side must fit 2000 and the
area 3.75 MP, and a picture shrunk on the way in would make the stated scale
wrong. The result states the screen size, the image size and the factor,
because every coordinate an action takes is a remote screen pixel.

The chat box inserts `@VNC A8AR` for an open connection and
`@VNC host:port password` for one to dial (`@RDP` likewise, with a user name
before the password); `vnc_agent.TOOL_INSTRUCTIONS` and `modalVncShortcut` in
`app.js` are the two halves of that wording and must stay in lockstep. Every
tool addresses a connection of either kind by its `screen_id`. Each screen first touched in a turn broadcasts one ephemeral
`vnc_activity` event, so its tab appears beside the chat without taking focus,
and the node remembers which screen a session's tools mean until it is closed.
The catalog names that session as each connection's additive `session_id` (the
session whose agent last chose the screen, `null` before any has), so that
chat's head shows a `VNC <ID>` (or `RDP <ID>`) chip for it like a browser's or a terminal's,
in every console, with a tab there or without.
The editable policy sent with every turn is Settings → System prompt → Remote
screen guidance (`system_prompt.vnc`).

## Refusals

Validation refuses rather than corrects: an unreachable host, a wrong password,
a server that requires a security type Puppy does not implement, a screen size
outside 1..8192, a rectangle outside the screen, a copy from outside the
screen, an encoding that was never requested, an invalid ZRLE tile, and an
unknown server message all end the connection with a named reason. The console
says so in the pane itself, with the reason and a Reconnect button, rather than
as a toast: a screen that is switched off would otherwise repeat one for every
redial. Automatic redials back off and stop after five, and reopening the tab or
pressing Reconnect starts that budget again.

An agent tool refuses the same way: an argument the tool does not take, an
unknown key, a button that is not left/middle/right, a coordinate that is not
a number, a view-only connection asked for input, or an unbound session asking
for a screen it never named are all answered with what was wrong and what can
be named instead.
