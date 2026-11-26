# Remote Desktop connections

Puppy is also a Windows Remote Desktop (RDP) client. `puppy/rdp.py` dials the
server from the chosen backend, signs in, decodes the screen on the backend
and hands it to the same remote-screen surface the [VNC client](vnc.md) uses.
An `RdpInstance` *is* a `VncInstance`: the registry, the four-character IDs,
the catalog, the viewer socket and its damage frames, the idle timer, the
console pane and the agent's tools are all shared, and only the wire beneath
them differs. Nothing is installed, spawned or proxied: the whole client is
`asyncio`, `ssl` through memory BIOs, `hashlib`/`hmac`, and an in-module MD4
(for hosts whose OpenSSL dropped it) and RC4 (for NTLM sealing).

## What is spoken

| Layer | Supported |
| --- | --- |
| Transport | TCP, X.224 negotiation, then TLS 1.0 or newer (no certificate validation; the SHA-256 fingerprint is shown) |
| Security | Network Level Authentication: CredSSP 2-6 with NTLMv2, the TLS key bound into the exchange, early user authorization (`HYBRID_EX`); TLS alone when the server allows it |
| Licensing | the licensing alert a workstation or an unlicensed server sends (`STATUS_VALID_CLIENT`) |
| Graphics | bitmap updates only: Interleaved RLE or uncompressed, 15, 16 and 24 bits per pixel, fast-path and slow-path, fragmented updates |
| Session | Deactivate-Reactivate (a resize), Set Error Info, Disconnect Provider Ultimatum, Suppress Output, Refresh Rect |
| Input | fast-path (or slow-path) scan codes, Unicode keys, mouse buttons, wheel and horizontal wheel, lock-key synchronization |

Legacy "standard RDP security" (its RC4 session encryption) is never offered,
and a server that insists on it is refused. Not supported: the client license
exchange a Remote Desktop Session Host with licensing configured asks for
(refused with that reason), smart-card or certificate sign-in, Kerberos,
redirection by a connection broker, and every virtual channel (clipboard,
audio, drives, printers, dynamic channels and the graphics pipeline).

The capability exchange asks for plain bitmaps on purpose: no drawing orders,
no bitmap or glyph caches, no surface commands and no codecs are advertised,
so every screen change arrives as a bitmap the decoder below understands, and
32-bit colour (whose planar codec buys nothing an agent can see) is not
offered.

## Signing in

With a user name and a password the client requests TLS and NLA. CredSSP runs
NTLMv2 over the TLS connection: NEGOTIATE, the server's CHALLENGE (its
computer name and Windows version are kept for the catalog), then AUTHENTICATE
with a message integrity code when the server stamped its challenge. Alongside
AUTHENTICATE goes the TLS certificate's SubjectPublicKey, sealed - hashed with
a fresh 32-byte nonce from CredSSP 5 on, echoed below it - and the server has
to send back the matching proof, sealed with its own key. Only then does the
password travel, sealed, as `TSPasswordCreds`. A server that cannot prove it
holds the certificate's key is refused before the password leaves the
process, which is what protects NLA against a man in the middle even though
the certificate itself is unverified. With `HYBRID_EX`, the server's four-byte
authorization answer then says whether the account may sign in over Remote
Desktop at all.

A refusal is worded from its NTSTATUS code (wrong user name or password,
account disabled, locked out, expired, password expired or to be changed, not
allowed to sign in remotely, and so on), with the code itself kept for the
rest. `DOMAIN\user` is split into a domain and a user; `user@domain` is sent
as the user name. Without a user name the client requests TLS alone; a server
that requires NLA then says so, and one that allows it shows its own sign-in
screen in the tab. The password is also placed in the client info for the
server's automatic sign-in, as every Remote Desktop client does.

## The screen

After MCS (one user channel and the I/O channel; no virtual channels are
requested) and the client info, the server's Demand Active names the desktop
size, which wins over the one asked for (1280 × 800 by default, any size from
200 to 4096 pixels a side through the routes and the agent's `connect`). A
later Deactivate All and Demand Active is a resize: the framebuffer is
replaced, viewers receive `size`, and the capabilities are confirmed again.

The RLE decoder works in runs, never per pixel: fills and colour runs are byte
multiplications, a background run copies the row above (in chunks no longer
than a row, so a run longer than a row reads what it wrote), foreground runs
and foreground/background images are whole-chunk XORs through Python's big
integers, and a bitmask's eight pixels come from a 256-entry table built from
two 16-entry halves per foreground colour. Whether an order is on the first
line is decided where the order starts, as the reference decoder does. Pixels
become RGBA in a few C-level passes: slice assignments for 24-bit BGR, and
for 16- and 15-bit colour translation tables per byte plus one big-integer OR
for the green that spans both bytes. Uncompressed bitmaps are bottom-up with
rows padded to four bytes, and every bitmap is cut to the part that lands on
the screen.

Updates are applied as they arrive and handed to viewers once nothing more is
buffered (or every 50 ms during a long burst, which also yields the event
loop): up to 48 rectangles travel as they are, more collapse into one cut of
their bounding box, exactly as VNC damage does. Each such hand-over counts one
applied update for an agent waiting on `sync`, with its rectangles logged for
waits that watch one region (see the agent tools in `docs/vnc.md`).

Remote Desktop pushes every change itself, so where VNC stops requesting
frames, RDP sends Suppress Output: with no active viewer and no recent agent
attention (two minutes after its last tool call) the server stops sending the
screen, and the next viewer or agent turns it back on, which makes the server
repaint. The viewer's refresh button sends Refresh Rect. The client info asks
Windows for no caret blink, wallpaper, menu animations or window contents
while dragging, so a screen an agent waits on does settle.

## Input

Named keys travel as scan codes with their extended-key flag (Pause as its
own sequence), and characters as Unicode key events, so the remote keyboard
layout never turns a typed character into another one. With Control, Alt or
the Windows key held a character is a shortcut, which Windows reads from the
key, so it travels as its US scan code instead (AltGr, or a held right Alt, is
not a shortcut). A server that takes no Unicode input gets US scan codes, with
Shift added for capitals and symbols. Mouse buttons are translated from RFB's
left/middle/right numbering to RDP's, the wheel turns in notches of 120, and
the horizontal wheel is used only where the server advertises it. Ctrl+Alt+Del
is the three scan codes, which Windows treats as the secure attention sequence
from a remote client. Fast-path input is used when the server offers it, and
the events of one action travel together.

## Routes and the agent

There are no new routes. `POST /api/vnc/instances` takes `protocol: "rdp"`
with an optional `username`, `width` and `height` (a user name or a size on a
VNC request is refused), and every catalog entry, status payload and screen
line names its protocol. RDP entries add `username`, `security` (`NLA` or
`TLS`), `certificate` (the SHA-256 fingerprint) and `server` (the Windows
version its NTLM challenge named). Nodes that dial RDP advertise the additive
`rdp` capability; the console offers *New RDP connection* only for those.
The port defaults to 3389 and has no display-number shorthand.

The agent's `connect` takes the same `protocol`, `username`, `width` and
`height`, every tool's `screen_id` addresses either kind, and the mentions are
`@RDP <ID>` for an open connection and `@RDP host[:port] [user [password]]` for
one to dial, built by the composer's *New RDP connection* wizard; its wording
and `vnc_agent.TOOL_INSTRUCTIONS` stay in lockstep. The editable policy is the
same Settings → System prompt → Remote screen guidance.

Closing a connection sends Disconnect Provider Ultimatum, which leaves the
Windows session running behind its lock screen for the next connection.
Windows' own reason for ending a session (signed out, idle limit, another
connection took over, and so on) arrives as Set Error Info and is the reason
the pane shows.

## Tests

`python3 tests/rdp_test.py` holds NTLMv2, the session keys and sealing to the
published MS-NLMP vectors, MD4 and RC4 to their known answers, and the RLE
decoder to a pixel-by-pixel transliteration of the reference decoder over
1500 random order streams at 15/16 and 24 bits. A stub server on loopback
speaks the real sequence, including the server half of CredSSP that checks
the MIC, the key binding and the delegated credentials, so the suite covers
sign-in (CredSSP 6 and 3, TLS only), every refusal (a wrong password, NLA
required, a server that cannot prove its key - the password then never sent -
a denied account, a license request, legacy security, a non-RDP server),
fast-path, fragmented and slow-path updates in every depth, cuts at the
screen's edge, the bounding-box collapse, a resize, Windows ending the
session, input on both input paths, Suppress Output following viewers and
agents, the routes on both runtimes and the agent bridge. No Windows server,
engine, network or quota is used. The certificate is a throwaway one made by
the `openssl` command for the run.
