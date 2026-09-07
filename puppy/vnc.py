"""Node-owned VNC (RFB) client connections shared by console viewers.

Puppy dials the remote VNC server itself over an ordinary TCP socket and keeps
one decoded framebuffer per identified connection.  Viewers receive damage
rectangles as raw RGBA over their own WebSocket, so the browser paints with
``putImageData`` and never runs an image codec or a decompressor of its own.

The whole client is stdlib: ``asyncio`` for the socket, ``zlib`` for the ZRLE
and Zlib encodings, and a small DES for the legacy VNC challenge.  Nothing is
installed, spawned, or written to disk - a connection's password lives only in
this process, exactly like a terminal's shell.

Cost discipline, because a framebuffer is the one place where per-pixel Python
would be ruinous:

* Every decoder writes whole runs, rows or tiles through C-level ``bytes``
  operations.  Packed ZRLE palettes are expanded through a 256-entry table
  built by doubling, so a row costs one ``join`` over its packed bytes rather
  than one step per pixel.
* The wire is read through one buffered reader that pulls in 64 KiB chunks and
  serves synchronous slices, so a Hextile tile costs a handful of awaits.
* The pixel format is negotiated so a pixel is already RGBA in memory: the
  server sends little-endian 32-bit true colour shifted 0/8/16, and only the
  alpha byte is forced to opaque.
* Nobody watching means nobody paying: with no active viewer the update
  request loop stops, so an unwatched connection costs one idle socket.

A VNC connection is a view onto another machine, not local state, so it never
blocks an engine turn, a backup, or a node upgrade.  Losing it costs the user
a reconnect and nothing else.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import re
import secrets
import string
import struct
import time
import zlib

from aiohttp import WSMsgType, web

from puppy import config, live_websockets

log = logging.getLogger("puppy.vnc")

VNC_ID_ALPHABET = string.ascii_uppercase + string.digits
VNC_ID_RE = re.compile(r"^[A-Z0-9]{4}$")

DEFAULT_PORT = 5900
MAX_HOST = 255
MAX_LABEL = 120
# The RFB challenge uses at most the first eight characters; a longer string is
# accepted so a pasted password is not silently rejected before truncation.
MAX_PASSWORD = 256
MAX_TEXT = 4096
CONNECT_TIMEOUT = 15.0
HANDSHAKE_TIMEOUT = 20.0
# A server that announces something outside this is either broken or hostile.
MAX_DIMENSION = 8192

READ_CHUNK = 1 << 16
# One full-screen refresh is split into bands so a fresh viewer paints
# progressively instead of waiting on a single multi-megabyte message.
FULL_BAND_BYTES = 1 << 20
# Beyond this the viewer is hopelessly behind: drop what is queued and settle
# it with one fresh full frame once its socket has drained.
MAX_VIEWER_BYTES = 8 << 20
MAX_TEXT_BACKLOG = 64
# Above this many damage rectangles the per-rectangle headers and Python-level
# bookkeeping cost more than re-cutting one bounding rectangle out of the
# framebuffer, so the update collapses into it.
MAX_UPDATE_RECTS = 48

# Frames to the viewer. Both headers are byte-addressed, never aligned, because
# the browser reads them with a DataView and the payload as a Uint8 view.
FRAME_PIXELS = 1
FRAME_COPY = 2
_FRAME_HEAD = struct.Struct("<BBHHHH")
_FRAME_COPY_TAIL = struct.Struct("<HH")

ENC_RAW = 0
ENC_COPYRECT = 1
ENC_RRE = 2
ENC_HEXTILE = 5
ENC_ZLIB = 6
ENC_ZRLE = 16
ENC_DESKTOP_SIZE = -223
ENC_LAST_RECT = -224
# Preference order as sent: ZRLE first because its zlib stream arrives as one
# sized blob and decodes entirely synchronously, Raw last because it is only
# the mandatory fallback.
CLIENT_ENCODINGS = (ENC_ZRLE, ENC_HEXTILE, ENC_ZLIB, ENC_RRE, ENC_COPYRECT,
                    ENC_RAW, ENC_DESKTOP_SIZE, ENC_LAST_RECT)

SEC_NONE = 1
SEC_VNC_AUTH = 2

_HEXTILE_RAW = 1
_HEXTILE_BG = 2
_HEXTILE_FG = 4
_HEXTILE_SUBRECTS = 8
_HEXTILE_COLOURED = 16

_manager = None


class VncError(RuntimeError):
    pass


class _Closed(VncError):
    """The remote end went away mid-message."""


# --------------------------------------------------------------------------
# DES, for the RFB "VNC Authentication" challenge only.
# --------------------------------------------------------------------------
# RFB predates every modern handshake: the server sends 16 random bytes and the
# client returns them encrypted with the password as a DES key, each key byte
# bit-reversed.  There is no DES in the standard library and the whole exchange
# happens twice per connection, so this is written for clarity, not speed.

_DES_IP = (
    58, 50, 42, 34, 26, 18, 10, 2, 60, 52, 44, 36, 28, 20, 12, 4,
    62, 54, 46, 38, 30, 22, 14, 6, 64, 56, 48, 40, 32, 24, 16, 8,
    57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
    61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7)
_DES_FP = (
    40, 8, 48, 16, 56, 24, 64, 32, 39, 7, 47, 15, 55, 23, 63, 31,
    38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53, 21, 61, 29,
    36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27,
    34, 2, 42, 10, 50, 18, 58, 26, 33, 1, 41, 9, 49, 17, 57, 25)
_DES_E = (
    32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, 8, 9, 10, 11, 12, 13,
    12, 13, 14, 15, 16, 17, 16, 17, 18, 19, 20, 21, 20, 21, 22, 23, 24, 25,
    24, 25, 26, 27, 28, 29, 28, 29, 30, 31, 32, 1)
_DES_P = (
    16, 7, 20, 21, 29, 12, 28, 17, 1, 15, 23, 26, 5, 18, 31, 10,
    2, 8, 24, 14, 32, 27, 3, 9, 19, 13, 30, 6, 22, 11, 4, 25)
_DES_PC1 = (
    57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18,
    10, 2, 59, 51, 43, 35, 27, 19, 11, 3, 60, 52, 44, 36,
    63, 55, 47, 39, 31, 23, 15, 7, 62, 54, 46, 38, 30, 22,
    14, 6, 61, 53, 45, 37, 29, 21, 13, 5, 28, 20, 12, 4)
_DES_PC2 = (
    14, 17, 11, 24, 1, 5, 3, 28, 15, 6, 21, 10,
    23, 19, 12, 4, 26, 8, 16, 7, 27, 20, 13, 2,
    41, 52, 31, 37, 47, 55, 30, 40, 51, 45, 33, 48,
    44, 49, 39, 56, 34, 53, 46, 42, 50, 36, 29, 32)
_DES_SHIFTS = (1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1)
_DES_SBOXES = (
    (14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
     0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
     4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
     15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13),
    (15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
     3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
     0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
     13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9),
    (10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
     13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
     13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
     1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12),
    (7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
     13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
     10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
     3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14),
    (2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
     14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
     4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
     11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3),
    (12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
     10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
     9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
     4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13),
    (4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
     13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
     1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
     6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12),
    (13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
     1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
     7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
     2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11))


def _des_permute(block: int, table, width: int) -> int:
    out = 0
    for position in table:
        out = (out << 1) | ((block >> (width - position)) & 1)
    return out


def _des_subkeys(key: int) -> list:
    permuted = _des_permute(key, _DES_PC1, 64)
    left, right = permuted >> 28, permuted & 0x0FFFFFFF
    keys = []
    for shift in _DES_SHIFTS:
        left = ((left << shift) | (left >> (28 - shift))) & 0x0FFFFFFF
        right = ((right << shift) | (right >> (28 - shift))) & 0x0FFFFFFF
        keys.append(_des_permute((left << 28) | right, _DES_PC2, 56))
    return keys


def _des_encrypt_block(block: bytes, subkeys: list) -> bytes:
    state = _des_permute(int.from_bytes(block, "big"), _DES_IP, 64)
    left, right = state >> 32, state & 0xFFFFFFFF
    for subkey in subkeys:
        expanded = _des_permute(right, _DES_E, 32) ^ subkey
        mixed = 0
        for index in range(8):
            chunk = (expanded >> (42 - 6 * index)) & 0x3F
            row = ((chunk & 0x20) >> 4) | (chunk & 1)
            column = (chunk >> 1) & 0x0F
            mixed = (mixed << 4) | _DES_SBOXES[index][row * 16 + column]
        left, right = right, left ^ _des_permute(mixed, _DES_P, 32)
    return _des_permute((right << 32) | left, _DES_FP, 64).to_bytes(8, "big")


_BIT_REVERSED = bytes(int("{:08b}".format(value)[::-1], 2) for value in range(256))


def vnc_auth_response(password: str, challenge: bytes) -> bytes:
    """Encrypt the server's 16-byte challenge with the RFB DES key schedule."""
    raw = password.encode("latin-1", errors="replace")[:8].ljust(8, b"\x00")
    subkeys = _des_subkeys(int.from_bytes(raw.translate(_BIT_REVERSED), "big"))
    return b"".join(_des_encrypt_block(challenge[index:index + 8], subkeys)
                    for index in (0, 8))


# --------------------------------------------------------------------------
# Keyboard
# --------------------------------------------------------------------------

# X11 keysyms for the keys a browser reports by name rather than as text.
_KEYSYMS = {
    "Backspace": 0xFF08, "Tab": 0xFF09, "Enter": 0xFF0D, "Escape": 0xFF1B,
    "Insert": 0xFF63, "Delete": 0xFFFF, "Home": 0xFF50, "End": 0xFF57,
    "PageUp": 0xFF55, "PageDown": 0xFF56, "ArrowLeft": 0xFF51,
    "ArrowUp": 0xFF52, "ArrowRight": 0xFF53, "ArrowDown": 0xFF54,
    "Shift": 0xFFE1, "Control": 0xFFE3, "Alt": 0xFFE9, "Meta": 0xFFEB,
    "CapsLock": 0xFFE5, "NumLock": 0xFF7F, "ScrollLock": 0xFF14,
    "ContextMenu": 0xFF67, "PrintScreen": 0xFF61, "Pause": 0xFF13,
    "AltGraph": 0xFE03,
}
for _index in range(1, 13):
    _KEYSYMS["F{}".format(_index)] = 0xFFBD + _index
# The right-hand modifiers are distinguishable only through ``code``.
_KEYSYMS_BY_CODE = {
    "ShiftRight": 0xFFE2, "ControlRight": 0xFFE4, "AltRight": 0xFFEA,
    "MetaRight": 0xFFEC, "NumpadEnter": 0xFF8D,
}
KEY_CONTROL = 0xFFE3
KEY_ALT = 0xFFE9
KEY_DELETE = 0xFFFF


def keysym_for(key: str, code: str = "") -> int:
    """Map one browser key event onto an X11 keysym, or 0 for nothing to send."""
    key = str(key or "")
    code = str(code or "")
    if code in _KEYSYMS_BY_CODE:
        return _KEYSYMS_BY_CODE[code]
    if key in _KEYSYMS:
        return _KEYSYMS[key]
    if len(key) == 1:
        return char_keysym(key)
    return 0


def char_keysym(char: str) -> int:
    point = ord(char)
    # Latin-1 is its own keysym range; everything else uses the Unicode block.
    return point if point < 256 else 0x01000000 + point


# --------------------------------------------------------------------------
# Wire helpers
# --------------------------------------------------------------------------

def normalize_vnc_id(value) -> str:
    vnc_id = str(value or "").strip().upper()
    if not VNC_ID_RE.fullmatch(vnc_id):
        raise VncError("invalid VNC ID")
    return vnc_id


def normalize_host(value) -> str:
    host = str(value or "").strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1].strip()
    if not host or len(host) > MAX_HOST:
        raise VncError("supply the VNC server's host name or address")
    if any(char.isspace() for char in host) or "\x00" in host or "/" in host:
        raise VncError("invalid VNC host")
    return host


def normalize_port(value, display: bool = True) -> int:
    """Validate a port, optionally reading 0-99 as a VNC display number."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_PORT
    if isinstance(value, bool):
        raise VncError("VNC port must be a whole number")
    if isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            raise VncError("VNC port must be a whole number")
        port = int(text)
    elif isinstance(value, int) or (isinstance(value, float) and
                                    value == int(value)):
        port = int(value)
    else:
        raise VncError("VNC port must be a whole number")
    # Bare display numbers are how VNC is written down everywhere: :1 is 5901.
    if display and 0 <= port <= 99:
        port += DEFAULT_PORT
    if not 1 <= port <= 65535:
        raise VncError("VNC port must be between 1 and 65535")
    return port


def parse_target(value) -> tuple:
    """Split ``host``, ``host:port``, ``host::port`` or ``[v6]:port``."""
    text = str(value or "").strip()
    if not text:
        raise VncError("supply the VNC server's host name or address")
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise VncError("invalid VNC host")
        return normalize_host(text[1:end]), normalize_port(
            text[end + 1:].lstrip(":") or None)
    if "::" in text:
        # host::5901 is the traditional "this is a real port, not a display"
        # spelling, so the display-number shorthand does not apply.
        host, _, port = text.partition("::")
        return normalize_host(host), normalize_port(port, display=False)
    if text.count(":") == 1:
        host, _, port = text.partition(":")
        return normalize_host(host), normalize_port(port)
    return normalize_host(text), DEFAULT_PORT


def _bounded_text(value, limit: int, label: str) -> str:
    text = "" if value is None else str(value)
    if "\x00" in text or len(text) > limit:
        raise VncError("invalid {}".format(label))
    return text


class _Stream:
    """Buffered reader: bulk pulls from the socket, synchronous slices out.

    A Hextile tile or an RFB header is a handful of small fields.  Awaiting the
    transport for each one costs more than the decode itself, so callers ask
    ``need`` once for a field group they can size exactly, then read it without
    suspending.  Sizes are always exact: over-reading would block a decode that
    has already consumed the server's last byte for this update.
    """

    __slots__ = ("reader", "buf", "pos")

    def __init__(self, reader):
        self.reader = reader
        self.buf = bytearray()
        self.pos = 0

    async def need(self, count: int) -> None:
        if len(self.buf) - self.pos >= count:
            return
        if self.pos:
            del self.buf[:self.pos]
            self.pos = 0
        while len(self.buf) < count:
            data = await self.reader.read(max(READ_CHUNK, count - len(self.buf)))
            if not data:
                raise _Closed("the VNC server closed the connection")
            self.buf += data

    async def read(self, count: int) -> bytes:
        await self.need(count)
        return self.take(count)

    def take(self, count: int) -> bytes:
        end = self.pos + count
        out = bytes(memoryview(self.buf)[self.pos:end])
        self.pos = end
        return out

    def u8(self) -> int:
        value = self.buf[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        value = self.buf[self.pos] << 8 | self.buf[self.pos + 1]
        self.pos += 2
        return value

    def u32(self) -> int:
        value = int.from_bytes(memoryview(self.buf)[self.pos:self.pos + 4], "big")
        self.pos += 4
        return value

    def s32(self) -> int:
        value = self.u32()
        return value - 0x100000000 if value >= 0x80000000 else value


def _expand_rgbx(raw: bytes, pixels: int) -> bytearray:
    """Force the pad byte of already-RGBX wire pixels to opaque."""
    out = bytearray(raw)
    out[3::4] = b"\xff" * pixels
    return out


def _expand_cpixels(raw, pixels: int) -> bytearray:
    """Widen three-byte ZRLE CPIXELs to RGBA in four C-level passes."""
    out = bytearray(b"\xff" * (pixels * 4))
    source = bytes(raw)
    out[0::4] = source[0::3]
    out[1::4] = source[1::3]
    out[2::4] = source[2::3]
    return out


def _packed_palette_table(palette: list, bits: int) -> list:
    """One entry per packed byte, built by doubling rather than per pixel.

    A packed-palette tile row is then ``join``ed straight out of this table:
    the only Python-level step left is one lookup per byte of the row.
    """
    table = [palette[index] if index < len(palette) else palette[0]
             for index in range(1 << bits)]
    width = bits
    while width < 8:
        # The outer value supplies the high bits, so index == (a << width) | b.
        table = [high + low for high in table for low in table]
        width *= 2
    return table


class _Viewer:
    """One WebSocket watcher with a single serialized sender task.

    Damage is a stream of deltas, so unlike a screencast it cannot be collapsed
    to "the newest frame".  A viewer that falls too far behind therefore has its
    backlog dropped outright and is settled with one fresh full frame once its
    socket drains, which bounds memory without ever painting a torn picture.
    """

    __slots__ = ("ws", "active", "queue", "queued_bytes", "texts", "wake",
                 "closed", "needs_full", "task", "settle", "full", "full_top")

    def __init__(self, ws, settle):
        self.ws = ws
        self.active = True
        self.queue = collections.deque()
        self.queued_bytes = 0
        self.texts = collections.deque()
        self.wake = asyncio.Event()
        self.closed = False
        self.needs_full = True
        self.settle = settle
        self.full = None
        self.full_top = 0
        self.task = asyncio.ensure_future(self._run())

    def invalidate(self) -> None:
        self.queue.clear()
        self.queued_bytes = 0
        self.full = None
        self.needs_full = True
        self.wake.set()

    def send_json(self, payload: dict) -> None:
        if len(self.texts) < MAX_TEXT_BACKLOG:
            self.texts.append(json.dumps(payload))
        self.wake.set()

    def send_frame(self, data: bytes) -> None:
        if not self.active or self.closed:
            self.invalidate()
            return
        if self.needs_full:
            return
        if self.queued_bytes + len(data) > MAX_VIEWER_BYTES:
            self.invalidate()
            return
        self.queue.append(data)
        self.queued_bytes += len(data)
        self.wake.set()

    def idle(self) -> bool:
        return not self.queue and self.full is None

    async def _run(self) -> None:
        try:
            while not self.closed:
                await self.wake.wait()
                self.wake.clear()
                while not self.closed:
                    while self.texts:
                        await self.ws.send_str(self.texts.popleft())
                    if not self.active:
                        break
                    if self.needs_full:
                        self.settle(self)
                    if self.full is not None:
                        width, height, pixels = self.full
                        stride = width * 4
                        top = self.full_top
                        rows = min(height - top, max(1, FULL_BAND_BYTES // stride))
                        frame = _FRAME_HEAD.pack(
                            FRAME_PIXELS, 0, 0, top, width, rows) + \
                            pixels[top * stride:(top + rows) * stride]
                        self.full_top += rows
                        if self.full_top == height:
                            self.full = None
                        # Do not retain the snapshot through a blocked send once
                        # a resize, pause or overflow has invalidated it.
                        del pixels
                    elif self.queue:
                        frame = self.queue.popleft()
                        self.queued_bytes -= len(frame)
                    else:
                        break
                    await self.ws.send_bytes(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def close(self) -> None:
        self.closed = True
        self.queue.clear()
        self.queued_bytes = 0
        self.full = None
        self.task.cancel()


class VncInstance:
    """One identified connection to one remote VNC server."""

    def __init__(self, vnc_id: str, host: str, port: int, password: str = "",
                 view_only: bool = False, label: str = "",
                 origin: str = "user"):
        self.vnc_id = vnc_id
        self.host = host
        self.port = port
        self.password = password
        self.view_only = bool(view_only)
        self.label = label
        self.origin = origin
        self.created_at = time.time()

        self.viewers = {}
        self.closed = False
        self.connected = False
        self.name = ""
        self.width = 0
        self.height = 0
        self.frame = bytearray()
        self.error = ""

        self.reader = None
        self.writer = None
        self.stream = None
        self.task = None
        self.idle_task = None
        self.start_lock = asyncio.Lock()
        self.inflate_zlib = None
        self.inflate_zrle = None
        self.update_pending = False
        self.buttons = 0
        self.pointer = (0, 0)
        self.wheel_x = 0.0
        self.wheel_y = 0.0
        self.pressed = set()

    # ---- catalog ----

    def viewer_count(self) -> int:
        return len(self.viewers)

    def payload(self) -> dict:
        return {
            "id": self.vnc_id,
            "host": self.host,
            "port": self.port,
            "label": self.label,
            "origin": self.origin,
            "connected": self.connected,
            "view_only": self.view_only,
            "viewers": len(self.viewers),
            "width": self.width,
            "height": self.height,
            "name": self.name,
            "error": self.error,
            "created_at": self.created_at,
        }

    def status_payload(self) -> dict:
        return {"type": "status", **self.payload()}

    # ---- lifecycle ----

    def _cancel_idle(self) -> None:
        if self.idle_task is not None:
            self.idle_task.cancel()
            self.idle_task = None

    def _arm_idle(self) -> None:
        self._cancel_idle()
        seconds = config.get("vnc.idle_timeout")
        if not seconds or self.viewers or not self.connected:
            return

        async def later():
            try:
                await asyncio.sleep(seconds)
            except asyncio.CancelledError:
                return
            if not self.viewers:
                await self.disconnect(
                    "No viewers for {} seconds".format(seconds))

        self.idle_task = asyncio.ensure_future(later())

    async def ensure_started(self) -> None:
        """Dial the server, or return at once when the session is already up."""
        if self.closed:
            raise VncError("VNC {} is closed".format(self.vnc_id))
        async with self.start_lock:
            if self.connected or self.closed:
                return
            self.error = ""
            try:
                self.reader, self.writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    CONNECT_TIMEOUT)
            except asyncio.TimeoutError:
                self.error = "Could not reach {}:{} within {:.0f}s".format(
                    self.host, self.port, CONNECT_TIMEOUT)
                raise VncError(self.error)
            except OSError as exc:
                self.error = "Could not reach {}:{}: {}".format(
                    self.host, self.port, exc.strerror or exc)
                raise VncError(self.error)
            self.stream = _Stream(self.reader)
            try:
                await asyncio.wait_for(self._handshake(), HANDSHAKE_TIMEOUT)
            except asyncio.TimeoutError:
                await self._close_socket()
                self.error = "The VNC server did not complete its handshake"
                raise VncError(self.error)
            except VncError as exc:
                await self._close_socket()
                self.error = str(exc)
                raise
            except (OSError, asyncio.IncompleteReadError) as exc:
                await self._close_socket()
                self.error = "VNC handshake failed: {}".format(exc)
                raise VncError(self.error)
            self.connected = True
            self.task = asyncio.ensure_future(self._run())
            log.info("VNC %s connected to %s:%s (%sx%s, %s)", self.vnc_id,
                     self.host, self.port, self.width, self.height,
                     self.name or "unnamed")
        _state_changed()

    async def _handshake(self) -> None:
        stream = self.stream
        greeting = await stream.read(12)
        match = re.match(rb"^RFB (\d{3})\.(\d{3})\n$", greeting)
        if not match:
            raise VncError("{}:{} is not a VNC server".format(self.host, self.port))
        major, minor = int(match.group(1)), int(match.group(2))
        if major != 3 or minor < 3:
            raise VncError("unsupported RFB protocol {}.{}".format(major, minor))
        minor = 8 if minor >= 8 else (7 if minor >= 7 else 3)
        self.writer.write("RFB 003.{:03d}\n".format(minor).encode("ascii"))
        await self.writer.drain()

        if minor >= 7:
            count = (await stream.read(1))[0]
            if count == 0:
                raise VncError(await self._read_reason(stream) or
                               "the VNC server refused the connection")
            offered = set(await stream.read(count))
            chosen = self._choose_security(offered)
            self.writer.write(bytes((chosen,)))
            await self.writer.drain()
        else:
            await stream.need(4)
            chosen = stream.u32()
            if chosen == 0:
                raise VncError(await self._read_reason(stream) or
                               "the VNC server refused the connection")
            if chosen not in (SEC_NONE, SEC_VNC_AUTH):
                raise VncError(
                    "the VNC server requires an unsupported security type")
            if chosen == SEC_VNC_AUTH and not self.password:
                raise VncError("this VNC server requires a password")

        if chosen == SEC_VNC_AUTH:
            challenge = await stream.read(16)
            self.writer.write(vnc_auth_response(self.password, challenge))
            await self.writer.drain()
        # RFB 3.3 reports no result for the "None" type; every later version
        # always does, and only 3.8 explains a failure.
        if minor >= 7 or chosen == SEC_VNC_AUTH:
            await stream.need(4)
            result = stream.u32()
            if result != 0:
                reason = await self._read_reason(stream) if minor >= 8 else ""
                raise VncError(reason or (
                    "the VNC password was rejected" if chosen == SEC_VNC_AUTH
                    else "the VNC server refused the connection"))

        # ClientInit: always share the desktop, never disconnect other viewers.
        self.writer.write(b"\x01")
        await self.writer.drain()

        await stream.need(24)
        width, height = stream.u16(), stream.u16()
        stream.take(16)          # the server's own pixel format; we replace it
        name_length = stream.u32()
        if name_length > 4096:
            raise VncError("the VNC server sent an oversized desktop name")
        raw_name = await stream.read(name_length)
        self.name = raw_name.decode("utf-8", errors="replace").strip()[:120]
        self._resize(width, height)

        await self._send_pixel_format()
        await self._send_encodings()

    def _choose_security(self, offered: set) -> int:
        if self.password and SEC_VNC_AUTH in offered:
            return SEC_VNC_AUTH
        if SEC_NONE in offered:
            return SEC_NONE
        if SEC_VNC_AUTH in offered:
            raise VncError("this VNC server requires a password")
        raise VncError("the VNC server requires an unsupported security type")

    @staticmethod
    async def _read_reason(stream) -> str:
        try:
            await stream.need(4)
            length = stream.u32()
            if length > 4096:
                return ""
            return (await stream.read(length)).decode(
                "utf-8", errors="replace").strip()[:200]
        except Exception:
            return ""

    async def _send_pixel_format(self) -> None:
        # 32 bits, depth 24, little-endian, true colour, red/green/blue shifted
        # 0/8/16: in memory that is exactly R,G,B,pad - the browser's RGBA
        # order, so no channel ever has to be rearranged in Python.
        self.writer.write(struct.pack(
            ">BxxxBBBBHHHBBBxxx", 0, 32, 24, 0, 1, 255, 255, 255, 0, 8, 16))
        await self.writer.drain()

    async def _send_encodings(self) -> None:
        self.writer.write(struct.pack(">BxH", 2, len(CLIENT_ENCODINGS)) +
                          b"".join(struct.pack(">i", value)
                                   for value in CLIENT_ENCODINGS))
        await self.writer.drain()

    async def _drain(self) -> None:
        """Flush the tiny outbound stream without ever ending the session.

        Input arrives on each viewer's own task while the reader loop runs, so
        two coroutines can reach the transport at once. Every write is one
        complete RFB message, and a drain that overlaps another is not worth a
        disconnection.
        """
        writer = self.writer
        if writer is None:
            return
        try:
            await writer.drain()
        except Exception:
            pass

    def _resize(self, width: int, height: int) -> None:
        if not 1 <= width <= MAX_DIMENSION or not 1 <= height <= MAX_DIMENSION:
            raise VncError("the VNC server announced an unusable screen size")
        self.width = width
        self.height = height
        self.frame = bytearray(b"\xff" * (width * height * 4))
        for viewer in self.viewers.values():
            viewer.invalidate()

    async def _close_socket(self) -> None:
        writer, self.writer = self.writer, None
        self.reader = None
        self.stream = None
        self.inflate_zlib = None
        self.inflate_zrle = None
        self.update_pending = False
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def disconnect(self, reason: str = "Disconnected") -> None:
        """Drop the remote session but keep the identity for a reconnect."""
        if not self.connected and self.writer is None:
            return
        self.connected = False
        self.error = reason
        self._cancel_idle()
        task, self.task = self.task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        await self._close_socket()
        self.buttons = 0
        self.pressed.clear()
        for viewer in self.viewers.values():
            viewer.needs_full = True
            viewer.send_json({"type": "gone", "reason": reason})
        log.info("VNC %s disconnected: %s", self.vnc_id, reason)
        _state_changed()

    async def close(self, reason: str = "Closed") -> None:
        self.closed = True
        await self.disconnect(reason)
        for viewer in list(self.viewers.values()):
            viewer.close()
        self.viewers = {}

    # ---- viewers ----

    def _streaming(self) -> bool:
        return any(viewer.active for viewer in self.viewers.values())

    async def attach_viewer(self, ws) -> None:
        await self.ensure_started()
        self._cancel_idle()
        viewer = _Viewer(ws, self._settle)
        self.viewers[ws] = viewer
        viewer.send_json(self.status_payload())
        self._settle(viewer)
        self._request_update(True)
        _state_changed()

    def detach_viewer(self, ws) -> None:
        viewer = self.viewers.pop(ws, None)
        if viewer is not None:
            viewer.close()
        if not self.viewers and self.connected:
            self._arm_idle()
        _state_changed()

    def set_viewer_active(self, ws, active: bool) -> None:
        viewer = self.viewers.get(ws)
        if viewer is None or viewer.active == bool(active):
            return
        viewer.active = bool(active)
        if not viewer.active:
            viewer.invalidate()
            return
        self._settle(viewer)
        # A pane that just became visible must not wait for the next remote
        # change: with nobody watching, the request loop was parked.
        self._request_update(True)

    def _settle(self, viewer) -> None:
        """Freeze a complete picture; the writer sends one band per drain.

        Full pictures can exceed the delta backlog limit. Queueing all their
        bands at once would discard the top of a large screen on every retry.
        Later deltas queue behind this snapshot, preserving CopyRect order.
        """
        if not viewer.active or not self.width or not self.frame:
            return
        viewer.needs_full = False
        viewer.queue.clear()
        viewer.queued_bytes = 0
        viewer.full = (self.width, self.height, bytes(self.frame))
        viewer.full_top = 0
        viewer.wake.set()

    def _publish(self, rects: list) -> None:
        for viewer in self.viewers.values():
            if not viewer.active:
                viewer.needs_full = True
                continue
            if viewer.needs_full:
                # Only once the socket has caught up, so a viewer that is too
                # slow for the deltas is not also flooded with full frames.
                if viewer.idle():
                    self._settle(viewer)
                continue
            for frame in rects:
                viewer.send_frame(frame)

    def _broadcast_json(self, payload: dict) -> None:
        for viewer in self.viewers.values():
            viewer.send_json(payload)

    # ---- update loop ----

    def _request_update(self, full: bool = False) -> None:
        """Ask for the next frame, unless one is already in flight."""
        if not self.connected or self.writer is None:
            return
        if self.update_pending and not full:
            return
        if not full and not self._streaming():
            return
        try:
            self.writer.write(struct.pack(
                ">BBHHHH", 3, 0 if full else 1, 0, 0, self.width, self.height))
        except Exception:
            return
        self.update_pending = True

    async def _run(self) -> None:
        reason = "Connection closed"
        try:
            self._request_update(True)
            await self._drain()
            while self.connected:
                await self.stream.need(1)
                kind = self.stream.u8()
                if kind == 0:
                    await self._read_framebuffer_update()
                elif kind == 1:
                    await self._skip_colour_map()
                elif kind == 2:
                    pass                          # Bell
                elif kind == 3:
                    await self._skip_cut_text()
                else:
                    raise VncError(
                        "the VNC server sent an unknown message ({})".format(kind))
        except asyncio.CancelledError:
            raise
        except _Closed as exc:
            reason = str(exc)
        except VncError as exc:
            reason = str(exc)
        except (OSError, asyncio.IncompleteReadError) as exc:
            reason = "VNC connection lost: {}".format(exc)
        except Exception:
            log.exception("VNC %s failed", self.vnc_id)
            reason = "VNC connection failed"
        self.task = None
        try:
            await self.disconnect(reason)
        except Exception:
            pass

    async def _skip_colour_map(self) -> None:
        await self.stream.need(5)
        self.stream.take(1)
        self.stream.u16()
        count = self.stream.u16()
        await self.stream.read(count * 6)

    async def _skip_cut_text(self) -> None:
        await self.stream.need(7)
        self.stream.take(3)
        length = self.stream.u32()
        while length:
            step = min(length, READ_CHUNK)
            await self.stream.read(step)
            length -= step

    async def _read_framebuffer_update(self) -> None:
        stream = self.stream
        await stream.need(3)
        stream.take(1)
        count = stream.u16()
        rects = []
        pixels = 0
        index = 0
        resized = False
        while index < count:
            index += 1
            await stream.need(12)
            x, y, width, height = stream.u16(), stream.u16(), stream.u16(), stream.u16()
            encoding = stream.s32()
            if encoding == ENC_LAST_RECT:
                break
            if encoding == ENC_DESKTOP_SIZE:
                self._resize(width, height)
                self._broadcast_json({"type": "size", "width": width,
                                      "height": height})
                rects = []
                pixels = 0
                resized = True
                continue
            if x + width > self.width or y + height > self.height:
                raise VncError("the VNC server sent a rectangle off screen")
            if not width or not height:
                continue
            if encoding == ENC_COPYRECT:
                await stream.need(4)
                source_x, source_y = stream.u16(), stream.u16()
                if source_x + width > self.width or source_y + height > self.height:
                    raise VncError("the VNC server sent a copy from off screen")
                self._copy_rect(x, y, width, height, source_x, source_y)
                rects.append(_FRAME_HEAD.pack(FRAME_COPY, 0, x, y, width, height) +
                             _FRAME_COPY_TAIL.pack(source_x, source_y))
                continue
            buf = await self._decode(encoding, width, height)
            self._blit(buf, x, y, width, height)
            pixels += width * height
            rects.append(_FRAME_HEAD.pack(
                FRAME_PIXELS, 0, x, y, width, height) + bytes(buf))
        self.update_pending = False
        if rects:
            # Many small rectangles cost more in headers and per-frame work
            # than one clean cut out of the freshly updated framebuffer.
            if len(rects) > MAX_UPDATE_RECTS and pixels:
                rects = [self._bounding_frame(rects)]
            self._publish(rects)
        self._request_update(resized)
        await self._drain()

    def _bounding_frame(self, rects: list) -> bytes:
        left = top = 1 << 20
        right = bottom = 0
        for frame in rects:
            _, _, x, y, width, height = _FRAME_HEAD.unpack_from(frame)
            left = min(left, x)
            top = min(top, y)
            right = max(right, x + width)
            bottom = max(bottom, y + height)
        width, height = right - left, bottom - top
        return _FRAME_HEAD.pack(FRAME_PIXELS, 0, left, top, width, height) + \
            bytes(self._extract(left, top, width, height))

    # ---- framebuffer ----

    def _blit(self, buf, x: int, y: int, width: int, height: int) -> None:
        stride = self.width * 4
        row = width * 4
        if row == stride:
            start = y * stride
            self.frame[start:start + row * height] = buf
            return
        frame = self.frame
        destination = y * stride + x * 4
        source = 0
        for _ in range(height):
            frame[destination:destination + row] = buf[source:source + row]
            destination += stride
            source += row

    def _extract(self, x: int, y: int, width: int, height: int) -> bytes:
        stride = self.width * 4
        row = width * 4
        view = memoryview(self.frame)
        if row == stride:
            return bytes(view[y * stride:(y + height) * stride])
        out = bytearray(row * height)
        source = y * stride + x * 4
        destination = 0
        for _ in range(height):
            out[destination:destination + row] = view[source:source + row]
            destination += row
            source += stride
        return bytes(out)

    def _copy_rect(self, x: int, y: int, width: int, height: int,
                   source_x: int, source_y: int) -> None:
        # Reading the whole source first keeps overlapping copies correct
        # without caring which way the rectangle moved.
        self._blit(self._extract(source_x, source_y, width, height),
                   x, y, width, height)

    # ---- decoders ----

    async def _decode(self, encoding: int, width: int, height: int):
        if encoding == ENC_RAW:
            return _expand_rgbx(await self.stream.read(width * height * 4),
                                width * height)
        if encoding == ENC_RRE:
            return await self._decode_rre(width, height)
        if encoding == ENC_HEXTILE:
            return await self._decode_hextile(width, height)
        if encoding == ENC_ZLIB:
            await self.stream.need(4)
            length = self.stream.u32()
            raw = self._inflate("zlib", await self.stream.read(length),
                                width * height * 4)
            return _expand_rgbx(raw, width * height)
        if encoding == ENC_ZRLE:
            await self.stream.need(4)
            length = self.stream.u32()
            return self._decode_zrle(self._inflate(
                "zrle", await self.stream.read(length)), width, height)
        raise VncError("the VNC server used encoding {} we did not "
                       "request".format(encoding))

    def _inflate(self, which: str, data: bytes, expect: int = 0) -> bytes:
        # Zlib and ZRLE own separate streams that each span the whole session:
        # that shared history is what makes them cheap, so neither is ever
        # reset mid-connection and the two must never be mixed.
        attribute = "inflate_" + which
        stream = getattr(self, attribute)
        if stream is None:
            stream = zlib.decompressobj()
            setattr(self, attribute, stream)
        out = stream.decompress(data)
        if expect and len(out) != expect:
            raise VncError("the VNC server sent a short compressed rectangle")
        return out

    async def _decode_rre(self, width: int, height: int) -> bytearray:
        stream = self.stream
        await stream.need(8)
        count = stream.u32()
        background = stream.take(4)[:3] + b"\xff"
        out = bytearray(background * (width * height))
        if count > width * height + 1024:
            raise VncError("the VNC server sent an implausible RRE rectangle")
        await stream.need(count * 12)
        row = width * 4
        for _ in range(count):
            colour = stream.take(4)[:3] + b"\xff"
            x, y = stream.u16(), stream.u16()
            sub_width, sub_height = stream.u16(), stream.u16()
            if x + sub_width > width or y + sub_height > height:
                raise VncError("the VNC server sent an RRE subrectangle off "
                               "its rectangle")
            line = colour * sub_width
            start = y * row + x * 4
            for _ in range(sub_height):
                out[start:start + sub_width * 4] = line
                start += row
        return out

    async def _decode_hextile(self, width: int, height: int) -> bytearray:
        stream = self.stream
        out = bytearray(width * height * 4)
        row = width * 4
        background = b"\x00\x00\x00\xff"
        foreground = b"\xff\xff\xff\xff"
        for tile_y in range(0, height, 16):
            tile_height = min(16, height - tile_y)
            for tile_x in range(0, width, 16):
                tile_width = min(16, width - tile_x)
                await stream.need(1)
                mode = stream.u8()
                base = tile_y * row + tile_x * 4
                if mode & _HEXTILE_RAW:
                    pixels = tile_width * tile_height
                    tile = _expand_rgbx(await stream.read(pixels * 4), pixels)
                    line = tile_width * 4
                    source = 0
                    for offset in range(tile_height):
                        start = base + offset * row
                        out[start:start + line] = tile[source:source + line]
                        source += line
                    continue
                extra = (4 if mode & _HEXTILE_BG else 0) + \
                    (4 if mode & _HEXTILE_FG else 0) + \
                    (1 if mode & _HEXTILE_SUBRECTS else 0)
                if extra:
                    await stream.need(extra)
                if mode & _HEXTILE_BG:
                    background = stream.take(4)[:3] + b"\xff"
                if mode & _HEXTILE_FG:
                    foreground = stream.take(4)[:3] + b"\xff"
                line = background * tile_width
                for offset in range(tile_height):
                    start = base + offset * row
                    out[start:start + tile_width * 4] = line
                if not mode & _HEXTILE_SUBRECTS:
                    continue
                count = stream.u8()
                coloured = bool(mode & _HEXTILE_COLOURED)
                await stream.need(count * (6 if coloured else 2))
                for _ in range(count):
                    colour = stream.take(4)[:3] + b"\xff" if coloured \
                        else foreground
                    position = stream.u8()
                    extent = stream.u8()
                    sub_x, sub_y = position >> 4, position & 15
                    sub_width, sub_height = (extent >> 4) + 1, (extent & 15) + 1
                    if sub_x + sub_width > tile_width or \
                            sub_y + sub_height > tile_height:
                        raise VncError("the VNC server sent a Hextile "
                                       "subrectangle outside its tile")
                    fill = colour * sub_width
                    start = base + (sub_y * row) + sub_x * 4
                    for _ in range(sub_height):
                        out[start:start + sub_width * 4] = fill
                        start += row
        return out

    def _decode_zrle(self, data: bytes, width: int, height: int) -> bytearray:
        out = bytearray(width * height * 4)
        row = width * 4
        position = 0
        size = len(data)

        def take(count: int) -> bytes:
            nonlocal position
            end = position + count
            if end > size:
                raise VncError("the VNC server sent a short ZRLE rectangle")
            chunk = data[position:end]
            position = end
            return chunk

        for tile_y in range(0, height, 64):
            tile_height = min(64, height - tile_y)
            for tile_x in range(0, width, 64):
                tile_width = min(64, width - tile_x)
                pixels = tile_width * tile_height
                base = tile_y * row + tile_x * 4
                line = tile_width * 4
                mode = take(1)[0]
                if mode == 0:
                    tile = _expand_cpixels(take(pixels * 3), pixels)
                elif mode == 1:
                    tile = (take(3) + b"\xff") * pixels
                elif mode < 17:
                    palette = self._zrle_palette(take(mode * 3), mode)
                    bits = 1 if mode == 2 else (2 if mode <= 4 else 4)
                    per_row = (tile_width * bits + 7) // 8
                    table = _packed_palette_table(palette, bits)
                    get = table.__getitem__
                    tile = bytearray()
                    for _ in range(tile_height):
                        tile += b"".join(map(get, take(per_row)))[:line]
                elif mode == 128:
                    tile = self._zrle_runs(take, pixels, None)
                elif mode > 129:
                    tile = self._zrle_runs(
                        take, pixels,
                        self._zrle_palette(take((mode - 128) * 3), mode - 128))
                else:
                    raise VncError(
                        "the VNC server sent an invalid ZRLE tile ({})".format(mode))
                source = 0
                for offset in range(tile_height):
                    start = base + offset * row
                    out[start:start + line] = tile[source:source + line]
                    source += line
        return out

    @staticmethod
    def _zrle_palette(raw: bytes, count: int) -> list:
        return [raw[index * 3:index * 3 + 3] + b"\xff" for index in range(count)]

    @staticmethod
    def _zrle_runs(take, pixels: int, palette) -> bytearray:
        """Plain or palette RLE: one C-level fill per run, never per pixel."""
        out = bytearray()
        remaining = pixels
        while remaining > 0:
            if palette is None:
                colour = take(3) + b"\xff"
                length = 1
                while True:
                    step = take(1)[0]
                    length += step
                    if step != 255:
                        break
            else:
                index = take(1)[0]
                if index & 0x80:
                    index &= 0x7F
                    length = 1
                    while True:
                        step = take(1)[0]
                        length += step
                        if step != 255:
                            break
                else:
                    length = 1
                if index >= len(palette):
                    raise VncError("the VNC server sent a ZRLE palette index "
                                   "out of range")
                colour = palette[index]
            if length > remaining:
                raise VncError("the VNC server sent an overlong ZRLE run")
            out += colour * length
            remaining -= length
        return out

    # ---- input ----

    def _writable(self) -> bool:
        return self.connected and self.writer is not None and not self.view_only

    def _fb_point(self, data: dict) -> tuple:
        try:
            nx = float(data.get("nx"))
            ny = float(data.get("ny"))
        except (TypeError, ValueError):
            return self.pointer
        if nx != nx or ny != ny:                  # NaN
            return self.pointer
        x = int(min(1.0, max(0.0, nx)) * max(0, self.width - 1) + 0.5)
        y = int(min(1.0, max(0.0, ny)) * max(0, self.height - 1) + 0.5)
        return x, y

    def _pointer(self, x: int, y: int, mask: int) -> None:
        self.pointer = (x, y)
        self.buttons = mask & 0xFF
        self.writer.write(struct.pack(">BBHH", 5, self.buttons, x, y))

    def _key(self, keysym: int, down: bool) -> None:
        if keysym:
            self.writer.write(struct.pack(">BBxxI", 4, 1 if down else 0, keysym))

    def _tap(self, keysym: int) -> None:
        self._key(keysym, True)
        self._key(keysym, False)

    async def handle_client(self, data: dict, viewer_ws=None) -> None:
        kind = data.get("type")
        if kind == "viewer_active":
            if type(data.get("active")) is bool:
                self.set_viewer_active(viewer_ws, data["active"])
            return
        if kind == "refresh":
            self._request_update(True)
        elif kind == "pointer":
            if not self._writable():
                return
            try:
                mask = int(data.get("mask") or 0)
            except (TypeError, ValueError):
                mask = 0
            x, y = self._fb_point(data)
            self._pointer(x, y, mask)
        elif kind == "wheel":
            if not self._writable():
                return
            self._wheel(data)
        elif kind == "key":
            if not self._writable():
                return
            down = data.get("kind") == "down"
            keysym = keysym_for(data.get("key"), data.get("code"))
            if not keysym:
                return
            # Track what is held so a lost focus or a dropped socket cannot
            # strand a modifier down on the remote machine.
            if down:
                self.pressed.add(keysym)
            else:
                self.pressed.discard(keysym)
            self._key(keysym, down)
        elif kind == "text":
            if not self._writable():
                return
            text = _bounded_text(data.get("text"), MAX_TEXT, "text")
            for char in text.replace("\r\n", "\n"):
                if char in "\r\n":
                    self._tap(_KEYSYMS["Enter"])
                elif char == "\t":
                    self._tap(_KEYSYMS["Tab"])
                else:
                    self._tap(char_keysym(char))
        elif kind == "cad":
            if not self._writable():
                return
            for keysym in (KEY_CONTROL, KEY_ALT, KEY_DELETE):
                self._key(keysym, True)
            for keysym in (KEY_DELETE, KEY_ALT, KEY_CONTROL):
                self._key(keysym, False)
        elif kind == "release_keys":
            self._release_keys()
        else:
            return
        await self._drain()

    def _wheel(self, data: dict) -> None:
        """Turn scroll deltas into the button 4-7 taps RFB uses for a wheel."""
        try:
            dx = float(data.get("dx") or 0.0)
            dy = float(data.get("dy") or 0.0)
        except (TypeError, ValueError):
            return
        if dx != dx or dy != dy:
            return
        x, y = self._fb_point(data)
        self.pointer = (x, y)
        self.wheel_x += max(-4096.0, min(4096.0, dx))
        self.wheel_y += max(-4096.0, min(4096.0, dy))
        steps = []
        while self.wheel_y <= -_WHEEL_STEP:
            self.wheel_y += _WHEEL_STEP
            steps.append(1 << 3)                  # button 4, wheel up
        while self.wheel_y >= _WHEEL_STEP:
            self.wheel_y -= _WHEEL_STEP
            steps.append(1 << 4)                  # button 5, wheel down
        while self.wheel_x <= -_WHEEL_STEP:
            self.wheel_x += _WHEEL_STEP
            steps.append(1 << 5)
        while self.wheel_x >= _WHEEL_STEP:
            self.wheel_x -= _WHEEL_STEP
            steps.append(1 << 6)
        for bit in steps[:_MAX_WHEEL_STEPS]:
            self.writer.write(struct.pack(">BBHH", 5, self.buttons | bit, x, y))
            self.writer.write(struct.pack(">BBHH", 5, self.buttons, x, y))

    def _release_keys(self) -> None:
        if not self._writable() or not self.pressed:
            return
        for keysym in sorted(self.pressed):
            self._key(keysym, False)
        self.pressed.clear()


_WHEEL_STEP = 53.0        # one notch of a typical browser wheel event
_MAX_WHEEL_STEPS = 24


def _state_changed() -> None:
    """Publish the cheap catalog the console's tabs and menus read."""
    try:
        from puppy import state_stream
        instances = _manager.instance_payloads() if _manager is not None else []
        state_stream.publish({"type": "vnc_instances", "instances": instances})
    except Exception:
        pass


class VncRegistry:
    """Every VNC connection this node owns, keyed by its four-character ID."""

    def __init__(self):
        self.instances = {}
        self.lock = asyncio.Lock()

    def _new_id(self) -> str:
        while True:
            candidate = "".join(secrets.choice(VNC_ID_ALPHABET) for _ in range(4))
            if candidate not in self.instances:
                return candidate

    async def create(self, host: str, port: int = DEFAULT_PORT,
                     password: str = "", view_only: bool = False,
                     label: str = "", origin: str = "user") -> VncInstance:
        async with self.lock:
            instance = VncInstance(self._new_id(), host, port, password,
                                   view_only, label, origin)
            self.instances[instance.vnc_id] = instance
        try:
            await instance.ensure_started()
        except VncError:
            async with self.lock:
                self.instances.pop(instance.vnc_id, None)
            raise
        return instance

    def get(self, vnc_id) -> VncInstance:
        vnc_id = normalize_vnc_id(vnc_id)
        instance = self.instances.get(vnc_id)
        if instance is None or instance.closed:
            raise VncError("VNC {} is closed or unknown".format(vnc_id))
        return instance

    async def close(self, vnc_id, reason: str = "Closed by user") -> bool:
        vnc_id = normalize_vnc_id(vnc_id)
        async with self.lock:
            instance = self.instances.pop(vnc_id, None)
        if instance is None or instance.closed:
            return False
        await instance.close(reason)
        _state_changed()
        return True

    def instance_payloads(self) -> list:
        return [instance.payload() for instance in self.instances.values()]

    async def stop(self, reason: str) -> None:
        async with self.lock:
            instances = list(self.instances.values())
            self.instances = {}
        if instances:
            await asyncio.gather(*(instance.close(reason) for instance in instances),
                                 return_exceptions=True)


def idle_settings_changed() -> None:
    if _manager is not None:
        for instance in list(_manager.instances.values()):
            instance._arm_idle()


def manager() -> VncRegistry:
    global _manager
    if _manager is None:
        _manager = VncRegistry()
    return _manager


def _request_spec(source) -> dict:
    if not isinstance(source, dict):
        raise VncError("invalid VNC request")
    host = source.get("host")
    port = source.get("port")
    # A supplied port is authoritative, so a bare IPv6 address stays intact.
    # Only a lone target string is split, and then IPv6 needs its brackets.
    if port in (None, "") and isinstance(host, str):
        host, port = parse_target(host)
    else:
        host = normalize_host(host)
        port = normalize_port(port)
    view_only = source.get("view_only", False)
    if type(view_only) is not bool:
        raise VncError("view_only must be true or false")
    return {
        "host": host,
        "port": port,
        "password": _bounded_text(source.get("password"), MAX_PASSWORD, "password"),
        "view_only": view_only,
        "label": _bounded_text(source.get("label"), MAX_LABEL, "label").strip(),
    }


async def h_instances(request: web.Request):
    return web.json_response({"instances": manager().instance_payloads()})


async def h_create(request: web.Request):
    if request.app.get("puppy_snapshot_busy"):
        return web.json_response(
            {"error": "Puppy backup or restore in progress"}, status=503)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid VNC request"}, status=400)
    try:
        spec = _request_spec(body)
    except VncError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    try:
        instance = await manager().create(**spec, origin="user")
    except VncError as exc:
        return web.json_response({"error": str(exc)}, status=502)
    _state_changed()
    return web.json_response({"ok": True, "vnc": instance.payload()}, status=201)


async def h_close(request: web.Request):
    try:
        vnc_id = normalize_vnc_id(request.match_info.get("vnc_id"))
        closed = await manager().close(vnc_id)
    except VncError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not closed:
        return web.json_response(
            {"error": "VNC {} is closed or unknown".format(vnc_id)}, status=404)
    return web.json_response({"ok": True, "id": vnc_id})


async def ws_vnc(request: web.Request):
    # aiohttp negotiates permessage-deflate by default and compresses large
    # frames in a worker thread, so damage rectangles ride the socket already
    # deflated without the event loop ever paying for it.
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 20)
    await ws.prepare(request)
    live_websockets.track(request, ws)
    if request.app.get("puppy_snapshot_busy"):
        await ws.close(code=1013, message=b"Puppy backup or restore in progress")
        return ws
    try:
        instance = manager().get(request.match_info.get("vnc_id"))
    except VncError as exc:
        try:
            await ws.send_json({"type": "error", "text": str(exc), "terminal": True})
        except Exception:
            pass
        await ws.close()
        return ws
    try:
        await instance.attach_viewer(ws)
    except VncError as exc:
        try:
            await ws.send_json({"type": "error", "text": str(exc)})
        except Exception:
            pass
        await ws.close()
        return ws
    log.info("VNC %s viewer attached (%s total) for %s", instance.vnc_id,
             instance.viewer_count(), request.remote)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
                continue
            try:
                data = json.loads(msg.data)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            try:
                await instance.handle_client(data, viewer_ws=ws)
            except VncError as exc:
                try:
                    await ws.send_json({"type": "error", "text": str(exc)})
                except Exception:
                    break
            except Exception:
                log.exception("VNC input handling failed")
    finally:
        instance.detach_viewer(ws)
        log.info("VNC %s viewer detached (%s left)", instance.vnc_id,
                 instance.viewer_count())
    return ws


async def shutdown() -> None:
    if _manager is not None:
        await _manager.stop("Puppy is shutting down")


def register(app: web.Application) -> None:
    app.router.add_get("/api/vnc/instances", h_instances)
    app.router.add_post("/api/vnc/instances", h_create)
    app.router.add_delete("/api/vnc/instances/{vnc_id:[A-Z0-9]{4}}", h_close)
    app.router.add_get("/api/ws/vnc/{vnc_id:[A-Z0-9]{4}}", ws_vnc)

    async def on_startup(_app):
        manager()

    async def on_cleanup(_app):
        await shutdown()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
