# VNC connections

Puppy is the VNC client. `puppy/vnc.py` dials a remote RFB server over an
ordinary TCP socket, decodes its framebuffer on the backend, and sends each
console viewer only the rectangles that changed. Nothing is installed, spawned
or proxied: the whole client is `asyncio`, `zlib` and a small DES for RFB's
legacy challenge.

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

Nobody watching means nobody paying. With every viewer inactive - a hidden
pane, a backgrounded window, or no viewer at all - the node stops issuing
`FramebufferUpdateRequest`s entirely, so an unwatched connection costs one idle
socket. A viewer that returns is settled with the whole screen in bands, and so
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

A connection's password is held only by the process that dialled the server and
only while the instance exists. It is never persisted, never returned by any
route, and never part of a backup.

The `vnc.idle_timeout` setting (Settings → Timeouts) drops an unwatched
connection but keeps its identity, so reopening the tab dials the same server
again. Closing the tab closes the connection. A dropped socket, a refused
handshake and a malformed update all end that one connection with a reason the
pane shows; nothing else on the node is affected, and a VNC connection never
blocks a turn, a backup or a node upgrade.

## Agent tools

The same connection is what an engine turn works with. `puppy/vnc_agent.py` is
the turn-bound stdio MCP bridge every driver receives, behind a mode-0600,
same-uid Unix socket into the running node: `screens`, `connect`, `screenshot`,
`move`, `click`, `drag`, `scroll`, `type`, `press`, `wait` and `disconnect`.
Nothing in it speaks RFB - each tool is one high-level action on a connection
the node already owns, so there is no second path to the server.

An agent has no viewer, so it turns the update loop by hand: `sync()` issues one
request and waits for the answer, and a server that sends nothing means the
framebuffer is already current. `settle()` repeats that until the picture is
quiet, which is what `wait` and the pause before every screenshot use.

A screenshot is a PNG built from the framebuffer with `zlib` and nothing else.
The rows are strided out as whole 32-bit pixels through one `memoryview` cast,
their pad byte is dropped in a single C-level pass, and the result is deflated:
no image library, and no per-pixel Python, exactly like the decoders. A screen
larger than `max_dimension` (1920 by default, so 1080p is 1:1) is reduced by a
whole factor, and the result states the screen size, the image size and the
factor, because every coordinate an action takes is a remote screen pixel.

The chat box inserts `@VNC A8AR` for an open connection and
`@VNC host:port password` for one to dial; `vnc_agent.TOOL_INSTRUCTIONS` and
`modalVncShortcut` in `app.js` are the two halves of that wording and must stay
in lockstep. Each screen first touched in a turn broadcasts one ephemeral
`vnc_activity` event, so its tab appears beside the chat without taking focus,
and the node remembers which screen a session's tools mean until it is closed.
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

An agent tool refuses the same way: an unknown key, a button that is not
left/middle/right, a coordinate that is not a number, a view-only connection
asked for input, or an unbound session asking for a screen it never named are
all answered with what was wrong and what can be named instead.
