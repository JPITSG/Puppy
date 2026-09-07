#!/usr/bin/env python3
"""No-quota tests for Puppy's built-in VNC client.

Everything runs against a stub RFB server on loopback that speaks the real
protocol: the version and security handshakes, the pixel format and encoding
negotiation, and framebuffer updates in each encoding the client advertises.
No VNC server, viewer, image library or external network is involved, and no
engine is invoked.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import os
import shutil
import struct
import sys
import zlib

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tests.scratch import private_root  # noqa: E402

TEST_ROOT = private_root("vnc-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

import aiohttp  # noqa: E402
from aiohttp import web as aioweb  # noqa: E402

from puppy import (config, db, protocol, runner, system_prompts, vnc,  # noqa: E402
                   vnc_agent)
from puppy.drivers.claude import ClaudeDriver  # noqa: E402
from puppy.drivers.codex import CodexDriver  # noqa: E402
from puppy.drivers.opencode import OpenCodeDriver  # noqa: E402
from puppy.web import build_app  # noqa: E402


class CaptureSocket:
    """A stand-in for the console sockets the runner announces tabs on."""

    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)

    async def send_str(self, payload):
        self.messages.append(json.loads(payload))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

async def wait_for(predicate, timeout=5.0, label="condition"):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for {}".format(label))


def rgba(*colours) -> bytes:
    """Expected framebuffer bytes for a run of (r, g, b) triples."""
    return b"".join(bytes((r, g, b, 255)) for r, g, b in colours)


def wire_pixel(r, g, b) -> bytes:
    """One pixel as the negotiated format puts it on the wire."""
    return bytes((r, g, b, 0))


def cpixel(r, g, b) -> bytes:
    return bytes((r, g, b))


def rect(x, y, width, height, encoding, payload=b"") -> bytes:
    return struct.pack(">HHHHi", x, y, width, height, encoding) + payload


def update(*rects, count=None) -> bytes:
    return struct.pack(">BxH", 0,
                       len(rects) if count is None else count) + b"".join(rects)


class ViewerSocket:
    """A stand-in for the viewer WebSocket the node writes frames to."""

    def __init__(self):
        self.texts = []
        self.frames = []
        self.closed = False

    async def send_str(self, payload):
        self.texts.append(json.loads(payload))

    async def send_bytes(self, payload):
        self.frames.append(bytes(payload))

    async def close(self, *args, **kwargs):
        self.closed = True

    def messages(self, kind):
        return [item for item in self.texts if item.get("type") == kind]

    def pixel_frames(self):
        return [decode_frame(frame) for frame in self.frames]


def decode_frame(frame: bytes) -> dict:
    kind, _, x, y, width, height = struct.unpack_from("<BBHHHH", frame)
    out = {"kind": kind, "x": x, "y": y, "w": width, "h": height}
    if kind == vnc.FRAME_COPY:
        out["sx"], out["sy"] = struct.unpack_from("<HH", frame, 10)
    else:
        out["pixels"] = frame[10:]
        assert len(out["pixels"]) == width * height * 4, out
    return out


class StubVncServer:
    """A scripted RFB server: real handshake, canned framebuffer updates."""

    def __init__(self, width=96, height=64, password="", security=(1, 2),
                 version=b"RFB 003.008\n", name="stub screen",
                 fail_reason=""):
        self.width = width
        self.height = height
        self.password = password
        self.security = security
        self.version = version
        self.name = name
        self.fail_reason = fail_reason
        self.updates = []
        self.pointer_events = []
        self.key_events = []
        self.cut_text = []
        self.requests = []
        self.encodings = []
        self.pixel_format = b""
        self.client_version = b""
        self.server = None
        self.port = 0
        self.connections = 0
        self.auth_failed = False
        # A real server answers a request the moment it has something to send,
        # so the stub holds outstanding requests rather than dropping them.
        self.pending = 0
        self.writer = None

    async def start(self):
        self.server = await asyncio.start_server(
            self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        if self.server is not None:
            self.server.close()
            try:
                await self.server.wait_closed()
            except Exception:
                pass
            self.server = None

    def queue(self, payload: bytes):
        self.updates.append(payload)
        self._flush()

    def _flush(self):
        while self.pending and self.updates and self.writer is not None:
            self.pending -= 1
            self.writer.write(self.updates.pop(0))

    async def _serve(self, reader, writer):
        self.connections += 1
        try:
            await self._session(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionResetError,
                asyncio.CancelledError, BrokenPipeError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _session(self, reader, writer):
        self.writer = writer
        self.pending = 0
        writer.write(self.version)
        await writer.drain()
        self.client_version = await reader.readexactly(12)
        minor = int(self.client_version[8:11])
        if minor >= 7:
            if self.fail_reason:
                writer.write(b"\x00" + struct.pack(">I", len(self.fail_reason)) +
                             self.fail_reason.encode())
                await writer.drain()
                return
            writer.write(bytes((len(self.security),)) + bytes(self.security))
            await writer.drain()
            chosen = (await reader.readexactly(1))[0]
        else:
            chosen = self.security[0]
            writer.write(struct.pack(">I", chosen))
            await writer.drain()
        if chosen == 2:
            challenge = bytes(range(16))
            writer.write(challenge)
            await writer.drain()
            answer = await reader.readexactly(16)
            expected = vnc.vnc_auth_response(self.password, challenge)
            if answer != expected:
                self.auth_failed = True
                writer.write(struct.pack(">I", 1))
                if minor >= 8:
                    reason = b"bad password"
                    writer.write(struct.pack(">I", len(reason)) + reason)
                await writer.drain()
                return
            writer.write(struct.pack(">I", 0))
            await writer.drain()
        elif minor >= 7:
            writer.write(struct.pack(">I", 0))
            await writer.drain()
        await reader.readexactly(1)                       # ClientInit
        name = self.name.encode()
        writer.write(struct.pack(">HH", self.width, self.height) +
                     b"\x20\x18\x00\x01\x00\xff\x00\xff\x00\xff\x00\x08\x10"
                     b"\x00\x00\x00" + struct.pack(">I", len(name)) + name)
        await writer.drain()
        while True:
            kind = (await reader.readexactly(1))[0]
            if kind == 0:
                self.pixel_format = await reader.readexactly(19)
            elif kind == 2:
                head = await reader.readexactly(3)
                count = struct.unpack(">H", head[1:])[0]
                body = await reader.readexactly(count * 4)
                self.encodings = list(struct.unpack(">{}i".format(count), body))
            elif kind == 3:
                body = await reader.readexactly(9)
                self.requests.append(struct.unpack(">BHHHH", body))
                self.pending += 1
                self._flush()
                await writer.drain()
            elif kind == 4:
                body = await reader.readexactly(7)
                down, keysym = body[0], struct.unpack(">I", body[3:])[0]
                self.key_events.append((bool(down), keysym))
            elif kind == 5:
                body = await reader.readexactly(5)
                mask, x, y = body[0], struct.unpack(">H", body[1:3])[0], \
                    struct.unpack(">H", body[3:])[0]
                self.pointer_events.append((mask, x, y))
            elif kind == 6:
                body = await reader.readexactly(7)
                length = struct.unpack(">I", body[3:])[0]
                self.cut_text.append(await reader.readexactly(length))
            else:
                raise AssertionError("stub saw unknown client message {}".format(kind))


# --------------------------------------------------------------------------
# encoders used to script the stub
# --------------------------------------------------------------------------

def raw_payload(pixels) -> bytes:
    return b"".join(wire_pixel(*pixel) for pixel in pixels)


def rre_payload(background, subrects) -> bytes:
    out = struct.pack(">I", len(subrects)) + wire_pixel(*background)
    for colour, x, y, width, height in subrects:
        out += wire_pixel(*colour) + struct.pack(">HHHH", x, y, width, height)
    return out


def hextile_raw_tile(pixels) -> bytes:
    return b"\x01" + raw_payload(pixels)


def hextile_solid_tile(colour) -> bytes:
    return b"\x02" + wire_pixel(*colour)


def hextile_subrect_tile(background, foreground, subrects, coloured=False) -> bytes:
    mode = 0x02 | 0x04 | 0x08 | (0x10 if coloured else 0)
    out = bytes((mode,)) + wire_pixel(*background) + wire_pixel(*foreground)
    out += bytes((len(subrects),))
    for colour, x, y, width, height in subrects:
        if coloured:
            out += wire_pixel(*colour)
        out += bytes(((x << 4) | y, ((width - 1) << 4) | (height - 1)))
    return out


class ZrleWriter:
    """One deflate stream per connection, exactly as a real server keeps."""

    def __init__(self):
        self.compressor = zlib.compressobj()

    def rectangle(self, tiles: bytes) -> bytes:
        blob = self.compressor.compress(tiles) + \
            self.compressor.flush(zlib.Z_SYNC_FLUSH)
        return struct.pack(">I", len(blob)) + blob


def zrle_raw_tile(pixels) -> bytes:
    return b"\x00" + b"".join(cpixel(*pixel) for pixel in pixels)


def zrle_solid_tile(colour) -> bytes:
    return b"\x01" + cpixel(*colour)


def zrle_packed_tile(palette, indices, width, height) -> bytes:
    bits = 1 if len(palette) == 2 else (2 if len(palette) <= 4 else 4)
    out = bytes((len(palette),)) + b"".join(cpixel(*colour) for colour in palette)
    per_byte = 8 // bits
    for row in range(height):
        packed = bytearray()
        column = 0
        while column < width:
            value = 0
            for step in range(per_byte):
                value <<= bits
                if column + step < width:
                    value |= indices[row * width + column + step]
            packed.append(value)
            column += per_byte
        out += bytes(packed)
    return out


def _run_length(length: int) -> bytes:
    out = bytearray()
    remaining = length - 1
    while remaining >= 255:
        out.append(255)
        remaining -= 255
    out.append(remaining)
    return bytes(out)


def zrle_plain_rle_tile(runs) -> bytes:
    out = b"\x80"
    for colour, length in runs:
        out += cpixel(*colour) + _run_length(length)
    return out


def zrle_palette_rle_tile(palette, runs) -> bytes:
    out = bytes((128 + len(palette),)) + \
        b"".join(cpixel(*colour) for colour in palette)
    for index, length in runs:
        if length == 1:
            out += bytes((index,))
        else:
            out += bytes((index | 0x80,)) + _run_length(length)
    return out


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def check_des() -> None:
    """The two published DES known-answer vectors, then the RFB key quirk."""
    subkeys = vnc._des_subkeys(0x133457799BBCDFF1)
    assert vnc._des_encrypt_block(bytes.fromhex("0123456789ABCDEF"),
                                  subkeys).hex().upper() == "85E813540F0AB405"
    assert vnc._des_encrypt_block(
        b"\x00" * 8, vnc._des_subkeys(0)).hex().upper() == "8CA64DE9C1B123A7"
    # RFB reverses the bits of every key byte before the schedule is built.
    assert vnc._BIT_REVERSED[0x01] == 0x80 and vnc._BIT_REVERSED[0x80] == 0x01
    answer = vnc.vnc_auth_response("secret", bytes(range(16)))
    assert len(answer) == 16
    # Only the first eight characters are key material, and both halves of the
    # challenge are encrypted independently under the same key.
    assert vnc.vnc_auth_response("secretXY", bytes(range(16))) != answer
    assert vnc.vnc_auth_response("secretXYZ", bytes(range(16))) == \
        vnc.vnc_auth_response("secretXY", bytes(range(16)))
    assert answer[:8] == vnc.vnc_auth_response("secret", bytes(range(8)) * 2)[:8]
    print("  DES challenge ok")


def check_validation() -> None:
    assert vnc.normalize_vnc_id("ab12") == "AB12"
    for bad in ("", "abc", "ABCDE", "AB-1", None):
        try:
            vnc.normalize_vnc_id(bad)
        except vnc.VncError:
            pass
        else:
            raise AssertionError("accepted VNC ID {!r}".format(bad))
    assert vnc.normalize_port(None) == 5900
    assert vnc.normalize_port(1) == 5901        # a display number, not a port
    assert vnc.normalize_port(0) == 5900
    assert vnc.normalize_port("5999") == 5999
    assert vnc.normalize_port(99) == 5999
    assert vnc.normalize_port(100) == 100
    assert vnc.normalize_port("1", display=False) == 1
    for bad in ("abc", "1.5", "-1", 0.5, True, 70000, -1, []):
        try:
            vnc.normalize_port(bad)
        except vnc.VncError:
            pass
        else:
            raise AssertionError("accepted port {!r}".format(bad))
    for bad in ("", "  ", "a b", "a/b", "x" * 300, "a\x00b"):
        try:
            vnc.normalize_host(bad)
        except vnc.VncError:
            pass
        else:
            raise AssertionError("accepted host {!r}".format(bad))
    assert vnc.parse_target("host") == ("host", 5900)
    assert vnc.parse_target("host:2") == ("host", 5902)
    assert vnc.parse_target("host:5905") == ("host", 5905)
    assert vnc.parse_target("host::5905") == ("host", 5905)
    assert vnc.parse_target("[fe80::1]:5901") == ("fe80::1", 5901)
    assert vnc.parse_target("[fe80::1]") == ("fe80::1", 5900)
    # A supplied port is authoritative, so a bare IPv6 host survives intact.
    spec = vnc._request_spec({"host": "fe80::1", "port": 5901})
    assert spec["host"] == "fe80::1" and spec["port"] == 5901, spec
    spec = vnc._request_spec({"host": " desk.example ", "port": "3",
                              "label": " Lab screen ", "view_only": True})
    assert spec == {"host": "desk.example", "port": 5903, "password": "",
                    "view_only": True, "label": "Lab screen"}, spec
    try:
        vnc._request_spec({"host": "desk", "port": 1, "view_only": "yes"})
    except vnc.VncError:
        pass
    else:
        raise AssertionError("accepted a non-boolean view_only")
    assert vnc.keysym_for("a") == ord("a")
    assert vnc.keysym_for("Enter") == 0xFF0D
    assert vnc.keysym_for("Shift", "ShiftLeft") == 0xFFE1
    assert vnc.keysym_for("Shift", "ShiftRight") == 0xFFE2
    assert vnc.keysym_for("F7") == 0xFFC4
    assert vnc.keysym_for("Unidentified") == 0
    assert vnc.char_keysym("€") == 0x01000000 + 0x20AC
    print("  request validation ok")


def check_pixel_helpers() -> None:
    """The widening paths every decoder shares must be exact, not approximate."""
    raw = wire_pixel(1, 2, 3) + wire_pixel(250, 251, 252)
    assert bytes(vnc._expand_rgbx(raw, 2)) == rgba((1, 2, 3), (250, 251, 252))
    packed = cpixel(9, 8, 7) + cpixel(6, 5, 4) + cpixel(3, 2, 1)
    assert bytes(vnc._expand_cpixels(packed, 3)) == \
        rgba((9, 8, 7), (6, 5, 4), (3, 2, 1))
    palette = [b"\x01\x02\x03\xff", b"\x0a\x0b\x0c\xff"]
    table = vnc._packed_palette_table(palette, 1)
    assert len(table) == 256 and len(table[0]) == 32
    assert table[0b10000000] == palette[1] + palette[0] * 7
    assert table[0b00000001] == palette[0] * 7 + palette[1]
    four = vnc._packed_palette_table(palette + palette, 2)
    assert len(four) == 256 and len(four[0]) == 16
    assert four[0b00011011] == palette[0] + palette[1] + palette[0] + palette[1]
    print("  pixel expansion ok")


async def connected_instance(server, **kwargs):
    instance = vnc.VncInstance("T35T", "127.0.0.1", server.port, **kwargs)
    await instance.ensure_started()
    return instance


async def check_handshakes() -> None:
    # RFB 3.8 with no authentication at all.
    server = await StubVncServer(security=(1,)).start()
    instance = await connected_instance(server)
    assert instance.connected and instance.width == 96 and instance.height == 64
    assert instance.name == "stub screen"
    assert server.client_version == b"RFB 003.008\n"
    await wait_for(lambda: server.encodings, label="encoding negotiation")
    # The negotiated format is what makes a decoded pixel already RGBA: 32-bit
    # little-endian true colour with red/green/blue shifted 0/8/16.
    assert server.pixel_format[3:] == struct.pack(
        ">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 0, 8, 16), \
        server.pixel_format
    assert list(server.encodings) == list(vnc.CLIENT_ENCODINGS), server.encodings
    await instance.close("test")
    await server.stop()

    # RFB 3.3, where the server alone picks the security type.
    server = await StubVncServer(version=b"RFB 003.003\n", security=(1,)).start()
    instance = await connected_instance(server)
    assert instance.connected and server.client_version == b"RFB 003.003\n"
    await instance.close("test")
    await server.stop()

    # VNC authentication, right and wrong.
    server = await StubVncServer(password="hunter2", security=(2,)).start()
    instance = await connected_instance(server, password="hunter2")
    assert instance.connected and not server.auth_failed
    await instance.close("test")
    try:
        await connected_instance(server, password="wrong")
    except vnc.VncError as exc:
        assert "bad password" in str(exc), exc
    else:
        raise AssertionError("a wrong password connected")
    assert server.auth_failed
    # A server that offers only VNC auth must say so rather than fail obscurely.
    try:
        await connected_instance(server)
    except vnc.VncError as exc:
        assert "requires a password" in str(exc), exc
    else:
        raise AssertionError("connected to a password-protected server without one")
    await server.stop()

    # A refusal before security selection is reported with the server's reason.
    server = await StubVncServer(fail_reason="too many viewers").start()
    try:
        await connected_instance(server)
    except vnc.VncError as exc:
        assert "too many viewers" in str(exc), exc
    else:
        raise AssertionError("a refused connection reported success")
    await server.stop()

    # Something that is not a VNC server at all.
    server = await StubVncServer(version=b"HTTP/1.1 200").start()
    try:
        await connected_instance(server)
    except vnc.VncError as exc:
        assert "not a VNC server" in str(exc), exc
    else:
        raise AssertionError("a non-VNC listener reported success")
    await server.stop()

    # Nothing listening at all.
    closed = await StubVncServer().start()
    port = closed.port
    await closed.stop()
    instance = vnc.VncInstance("D34D", "127.0.0.1", port)
    try:
        await instance.ensure_started()
    except vnc.VncError as exc:
        assert "Could not reach" in str(exc), exc
    else:
        raise AssertionError("connected to a closed port")
    print("  handshakes ok")


async def check_encodings() -> None:
    """Every advertised encoding must land the same pixels in the buffer."""
    server = await StubVncServer(width=8, height=4).start()
    instance = await connected_instance(server)
    viewer = ViewerSocket()
    await instance.attach_viewer(viewer)
    stride = instance.width * 4

    def row(index):
        return bytes(instance.frame[index * stride:(index + 1) * stride])

    # Raw: a 2x2 patch in the corner.
    server.queue(update(rect(0, 0, 2, 2, vnc.ENC_RAW, raw_payload(
        [(10, 20, 30), (40, 50, 60), (70, 80, 90), (100, 110, 120)]))))
    await wait_for(lambda: row(0)[:8] == rgba((10, 20, 30), (40, 50, 60)),
                   label="raw rectangle")
    assert row(1)[:8] == rgba((70, 80, 90), (100, 110, 120))

    # CopyRect: move that patch two pixels right without sending pixels.
    server.queue(update(rect(2, 0, 2, 2, vnc.ENC_COPYRECT,
                             struct.pack(">HH", 0, 0))))
    await wait_for(lambda: row(0)[8:16] == rgba((10, 20, 30), (40, 50, 60)),
                   label="copyrect")
    copies = [frame for frame in viewer.pixel_frames()
              if frame["kind"] == vnc.FRAME_COPY]
    assert copies and copies[-1] == {"kind": vnc.FRAME_COPY, "x": 2, "y": 0,
                                     "w": 2, "h": 2, "sx": 0, "sy": 0}, copies

    # RRE: a background with one subrectangle painted over it.
    server.queue(update(rect(0, 2, 8, 2, vnc.ENC_RRE, rre_payload(
        (5, 5, 5), [((200, 100, 50), 1, 0, 2, 1)]))))
    await wait_for(lambda: row(2)[:16] == rgba(
        (5, 5, 5), (200, 100, 50), (200, 100, 50), (5, 5, 5)),
        label="rre rectangle")
    assert row(3)[:8] == rgba((5, 5, 5), (5, 5, 5))

    # Zlib: raw pixels through the session's own deflate stream.
    compressor = zlib.compressobj()
    blob = compressor.compress(raw_payload([(1, 1, 1), (2, 2, 2)])) + \
        compressor.flush(zlib.Z_SYNC_FLUSH)
    server.queue(update(rect(6, 0, 2, 1, vnc.ENC_ZLIB,
                             struct.pack(">I", len(blob)) + blob)))
    await wait_for(lambda: row(0)[24:32] == rgba((1, 1, 1), (2, 2, 2)),
                   label="zlib rectangle")

    # Hextile: a raw tile, a solid tile and a background/subrect tile.
    server.queue(update(rect(0, 0, 8, 4, vnc.ENC_HEXTILE,
                             hextile_raw_tile([(index, index, index)
                                               for index in range(32)]))))
    await wait_for(lambda: row(0)[:8] == rgba((0, 0, 0), (1, 1, 1)),
                   label="hextile raw tile")
    assert row(3)[28:32] == rgba((31, 31, 31))
    server.queue(update(rect(0, 0, 8, 4, vnc.ENC_HEXTILE,
                             hextile_solid_tile((9, 9, 9)))))
    await wait_for(lambda: bytes(instance.frame) == rgba(*([(9, 9, 9)] * 32)),
                   label="hextile solid tile")
    server.queue(update(rect(0, 0, 8, 4, vnc.ENC_HEXTILE,
                             hextile_subrect_tile((3, 3, 3), (7, 7, 7),
                                                  [(None, 1, 1, 2, 2)]))))
    await wait_for(lambda: row(1)[4:12] == rgba((7, 7, 7), (7, 7, 7)),
                   label="hextile subrectangles")
    assert row(0)[:4] == rgba((3, 3, 3)) and row(2)[4:8] == rgba((7, 7, 7))
    server.queue(update(rect(0, 0, 8, 4, vnc.ENC_HEXTILE,
                             hextile_subrect_tile((0, 0, 0), (0, 0, 0),
                                                  [((11, 22, 33), 5, 3, 3, 1)],
                                                  coloured=True))))
    await wait_for(lambda: row(3)[20:32] == rgba(
        (11, 22, 33), (11, 22, 33), (11, 22, 33)),
        label="coloured hextile subrectangles")

    # ZRLE: every tile mode, one rectangle at a time through one zlib stream.
    zrle = ZrleWriter()
    server.queue(update(rect(0, 0, 8, 4, vnc.ENC_ZRLE,
                             zrle.rectangle(zrle_solid_tile((60, 61, 62))))))
    await wait_for(lambda: bytes(instance.frame) == rgba(*([(60, 61, 62)] * 32)),
                   label="zrle solid tile")
    server.queue(update(rect(0, 0, 4, 2, vnc.ENC_ZRLE, zrle.rectangle(
        zrle_raw_tile([(index, 0, 255 - index) for index in range(8)])))))
    await wait_for(lambda: row(0)[:16] == rgba(
        (0, 0, 255), (1, 0, 254), (2, 0, 253), (3, 0, 252)),
        label="zrle raw tile")
    assert row(1)[:4] == rgba((4, 0, 251))
    palette = [(1, 2, 3), (4, 5, 6)]
    server.queue(update(rect(0, 0, 4, 2, vnc.ENC_ZRLE, zrle.rectangle(
        zrle_packed_tile(palette, [0, 1, 1, 0, 1, 0, 0, 1], 4, 2)))))
    await wait_for(lambda: row(0)[:16] == rgba(
        (1, 2, 3), (4, 5, 6), (4, 5, 6), (1, 2, 3)),
        label="zrle packed palette tile")
    assert row(1)[:16] == rgba((4, 5, 6), (1, 2, 3), (1, 2, 3), (4, 5, 6))
    server.queue(update(rect(0, 0, 4, 2, vnc.ENC_ZRLE, zrle.rectangle(
        zrle_plain_rle_tile([((8, 8, 8), 3), ((9, 9, 9), 5)])))))
    await wait_for(lambda: row(0)[:16] == rgba(
        (8, 8, 8), (8, 8, 8), (8, 8, 8), (9, 9, 9)),
        label="zrle plain RLE tile")
    server.queue(update(rect(0, 0, 4, 2, vnc.ENC_ZRLE, zrle.rectangle(
        zrle_palette_rle_tile([(2, 2, 2), (3, 3, 3)],
                              [(0, 1), (1, 6), (0, 1)])))))
    await wait_for(lambda: row(0)[:16] == rgba(
        (2, 2, 2), (3, 3, 3), (3, 3, 3), (3, 3, 3)),
        label="zrle palette RLE tile")
    assert row(1)[12:16] == rgba((2, 2, 2))

    # LastRect ends an update whose rectangle count was never known.
    server.queue(update(rect(0, 0, 1, 1, vnc.ENC_RAW, raw_payload([(77, 0, 0)])),
                        rect(0, 0, 0, 0, vnc.ENC_LAST_RECT),
                        count=0xFFFF))
    await wait_for(lambda: row(0)[:4] == rgba((77, 0, 0)), label="LastRect")

    # DesktopSize reallocates the framebuffer and tells the viewer.
    server.queue(update(rect(0, 0, 16, 8, vnc.ENC_DESKTOP_SIZE)))
    await wait_for(lambda: instance.width == 16 and instance.height == 8,
                   label="DesktopSize")
    assert len(instance.frame) == 16 * 8 * 4
    await wait_for(lambda: viewer.messages("size"), label="size notice")
    assert viewer.messages("size")[-1] == {"type": "size", "width": 16,
                                           "height": 8}
    instance.detach_viewer(viewer)
    await instance.close("test")
    await server.stop()
    print("  encodings ok")


async def check_hostile_updates() -> None:
    """A malformed or lying server ends its own connection, never the process."""
    cases = [
        ("off-screen rectangle",
         update(rect(90, 60, 40, 40, vnc.ENC_RAW, b"\x00" * 16))),
        ("copy from off screen",
         update(rect(0, 0, 4, 4, vnc.ENC_COPYRECT, struct.pack(">HH", 95, 63)))),
        ("unrequested encoding",
         update(rect(0, 0, 2, 2, vnc.ENC_RAW - 99, b""))),
        ("unknown server message", b"\x77"),
    ]
    for label, payload in cases:
        server = await StubVncServer(width=96, height=64).start()
        instance = await connected_instance(server)
        viewer = ViewerSocket()
        await instance.attach_viewer(viewer)
        server.queue(payload)
        await wait_for(lambda: not instance.connected, label=label)
        assert instance.error, label
        assert viewer.messages("gone"), label
        instance.detach_viewer(viewer)
        await instance.close("test")
        await server.stop()

    # An invalid ZRLE tile mode is refused inside the decoder itself.
    server = await StubVncServer(width=8, height=4).start()
    instance = await connected_instance(server)
    zrle = ZrleWriter()
    server.queue(update(rect(0, 0, 8, 4, vnc.ENC_ZRLE, zrle.rectangle(b"\x11"))))
    await wait_for(lambda: not instance.connected, label="invalid ZRLE tile")
    assert "ZRLE" in instance.error, instance.error
    await instance.close("test")
    await server.stop()

    # A screen size no client could hold is refused at the handshake.
    server = await StubVncServer(width=60000, height=60000).start()
    try:
        await connected_instance(server)
    except vnc.VncError as exc:
        assert "unusable screen size" in str(exc), exc
    else:
        raise AssertionError("accepted an impossible screen size")
    await server.stop()
    print("  hostile updates ok")


async def check_viewer_frames() -> None:
    server = await StubVncServer(width=8, height=4).start()
    instance = await connected_instance(server)
    viewer = ViewerSocket()
    await instance.attach_viewer(viewer)
    # Attaching settles the viewer with the whole screen before any damage.
    await wait_for(lambda: viewer.frames, label="settle frame")
    first = decode_frame(viewer.frames[0])
    assert first["kind"] == vnc.FRAME_PIXELS and first["x"] == 0 and \
        first["y"] == 0 and first["w"] == 8 and first["h"] == 4, first
    assert viewer.messages("status")[0]["width"] == 8

    # Damage carries only its own rectangle.
    viewer.frames.clear()
    server.queue(update(rect(3, 1, 2, 1, vnc.ENC_RAW,
                             raw_payload([(1, 2, 3), (4, 5, 6)]))))
    await wait_for(lambda: viewer.frames, label="damage frame")
    damage = decode_frame(viewer.frames[0])
    assert damage == {"kind": vnc.FRAME_PIXELS, "x": 3, "y": 1, "w": 2, "h": 1,
                      "pixels": rgba((1, 2, 3), (4, 5, 6))}, damage

    # An inactive viewer is not sent frames, and gets the whole screen back
    # when it returns rather than the deltas it slept through.
    instance.set_viewer_active(viewer, False)
    viewer.frames.clear()
    server.queue(update(rect(0, 0, 1, 1, vnc.ENC_RAW, raw_payload([(9, 9, 9)]))))
    await wait_for(lambda: bytes(instance.frame[:4]) == rgba((9, 9, 9)),
                   label="update while inactive")
    assert not viewer.frames
    instance.set_viewer_active(viewer, True)
    await wait_for(lambda: viewer.frames, label="resume frame")
    resumed = decode_frame(viewer.frames[0])
    assert resumed["w"] == 8 and resumed["h"] == 4, resumed
    assert resumed["pixels"][:4] == rgba((9, 9, 9))

    # A viewer that falls hopelessly behind is caught up, never unbounded.
    viewer.frames.clear()
    slow = instance.viewers[viewer]
    slow.queued_bytes = vnc.MAX_VIEWER_BYTES
    slow.send_frame(b"x" * 64)
    assert slow.needs_full and not slow.queue
    slow.queued_bytes = 0
    server.queue(update(rect(0, 0, 1, 1, vnc.ENC_RAW, raw_payload([(3, 3, 3)]))))
    await wait_for(lambda: any(decode_frame(frame)["w"] == 8
                               for frame in viewer.frames),
                   label="catch-up full frame")

    # A handful of rectangles are forwarded as they came.
    viewer.frames.clear()
    server.queue(update(
        rect(0, 0, 1, 1, vnc.ENC_RAW, raw_payload([(4, 4, 4)])),
        rect(7, 3, 1, 1, vnc.ENC_RAW, raw_payload([(5, 5, 5)]))))
    await wait_for(lambda: len(viewer.frames) == 2, label="two damage frames")
    assert [frame["x"] for frame in viewer.pixel_frames()] == [0, 7]

    # Past the limit they collapse into one cut of the framebuffer instead.
    viewer.frames.clear()
    rects = [rect(index % 8, (index // 8) % 4, 1, 1, vnc.ENC_RAW,
                  raw_payload([(index % 256, 0, 0)]))
             for index in range(vnc.MAX_UPDATE_RECTS + 1)]
    server.queue(update(*rects))
    await wait_for(lambda: viewer.frames, label="collapsed update")
    await asyncio.sleep(0.1)
    assert len(viewer.frames) == 1, len(viewer.frames)
    collapsed = decode_frame(viewer.frames[0])
    assert collapsed["w"] == 8 and collapsed["h"] == 4, collapsed
    assert collapsed["pixels"] == bytes(instance.frame)

    instance.detach_viewer(viewer)
    await instance.close("test")
    await server.stop()
    print("  viewer frames ok")


async def check_large_viewer_refresh() -> None:
    """Full pictures exceed the delta budget; blocked writers must still recover."""
    class GatedSocket(ViewerSocket):
        def __init__(self):
            super().__init__()
            self.gate = asyncio.Event()
            self.started = asyncio.Event()
            self.events = []

        async def send_str(self, payload):
            self.events.append("text")
            await super().send_str(payload)

        async def send_bytes(self, payload):
            self.started.set()
            await self.gate.wait()
            self.events.append("pixels")
            await super().send_bytes(payload)

    instance = vnc.VncInstance("BIG1", "screen.example", 5900)
    instance._resize(1080, 2400)
    original = b"".join(rgba((y % 256, 50, 70)) * instance.width
                        for y in range(instance.height))
    assert len(original) > vnc.MAX_VIEWER_BYTES
    instance.frame[:] = original
    socket = GatedSocket()
    viewer = vnc._Viewer(socket, instance._settle)
    instance.viewers[socket] = viewer

    def picture(frames, width, height):
        result = bytearray(width * height * 4)
        for payload in frames:
            item = decode_frame(payload)
            row = item["w"] * 4
            pixels = item.get("pixels")
            if item["kind"] == vnc.FRAME_COPY:
                pixels = b"".join(
                    result[((item["sy"] + y) * width + item["sx"]) * 4:
                           ((item["sy"] + y) * width + item["sx"]) * 4 + row]
                    for y in range(item["h"]))
            for y in range(item["h"]):
                start = ((item["y"] + y) * width + item["x"]) * 4
                result[start:start + row] = pixels[y * row:(y + 1) * row]
        return bytes(result)

    async def drain():
        socket.gate.set()
        await wait_for(lambda: viewer.idle() and not viewer.needs_full,
                       label="complete large picture")
        await asyncio.sleep(0)

    try:
        instance._settle(viewer)
        await socket.started.wait()
        # A delta arriving during a blocked refresh must follow the frozen
        # picture, including when it changes a band not sent yet.
        patch = rgba((1, 2, 3))
        instance._blit(patch, 0, 2399, 1, 1)
        delta = vnc._FRAME_HEAD.pack(vnc.FRAME_PIXELS, 0, 0, 2399, 1, 1) + patch
        instance._copy_rect(1, 2399, 1, 1, 0, 2399)
        copied = vnc._FRAME_HEAD.pack(vnc.FRAME_COPY, 0, 1, 2399, 1, 1) + \
            vnc._FRAME_COPY_TAIL.pack(0, 2399)
        instance._publish([delta, copied])
        await drain()
        assert picture(socket.frames[:-2], 1080, 2400) == original
        assert socket.frames[-2:] == [delta, copied]
        assert picture(socket.frames, 1080, 2400) == bytes(instance.frame)

        # Overflow while a send is blocked must recover even if the server
        # sends no more updates. Deltas after the loss must not run alone.
        socket.frames.clear()
        socket.gate.clear()
        socket.started.clear()
        instance._settle(viewer)
        await socket.started.wait()
        instance.frame[:] = rgba((80, 90, 100)) * (1080 * 2400)
        viewer.send_frame(b"x" * (vnc.MAX_VIEWER_BYTES + 1))
        viewer.send_frame(delta)
        assert viewer.needs_full and not viewer.queue
        await drain()
        assert picture(socket.frames, 1080, 2400) == bytes(instance.frame)
        assert not viewer.queue and viewer.queued_bytes == 0

        # Resize behind a blocked old band: discard all remaining old pixels,
        # send the size before any new bands, and recover without new damage.
        socket.frames.clear()
        socket.events.clear()
        socket.gate.clear()
        socket.started.clear()
        instance._settle(viewer)
        await socket.started.wait()
        instance._resize(4, 3)
        instance.frame[:] = rgba((9, 8, 7)) * 12
        instance._broadcast_json({"type": "size", "width": 4, "height": 3})
        await drain()
        assert len(socket.frames) == 2
        assert socket.events == ["pixels", "text", "pixels"]
        assert picture(socket.frames[1:], 4, 3) == bytes(instance.frame)
        assert socket.messages("size")[-1]["width"] == 4

        # Hiding drops a pending snapshot as well as queued deltas.
        socket.gate.clear()
        socket.started.clear()
        instance._settle(viewer)
        await socket.started.wait()
        instance.set_viewer_active(socket, False)
        assert viewer.full is None and not viewer.queue
        socket.gate.set()
        await asyncio.sleep(0)
        socket.frames.clear()
        instance.set_viewer_active(socket, True)
        await drain()
        assert picture(socket.frames, 4, 3) == bytes(instance.frame)
    finally:
        viewer.close()
        await asyncio.gather(viewer.task, return_exceptions=True)
    print("  large refresh, blocked writer recovery, resize and pause ok")


async def check_streaming_pause() -> None:
    """With nobody watching, the node stops asking the server for frames."""
    server = await StubVncServer(width=8, height=4).start()
    instance = await connected_instance(server)
    viewer = ViewerSocket()
    await instance.attach_viewer(viewer)
    server.queue(update(rect(0, 0, 1, 1, vnc.ENC_RAW, raw_payload([(1, 1, 1)]))))
    await wait_for(lambda: bytes(instance.frame[:4]) == rgba((1, 1, 1)),
                   label="first update")
    instance.set_viewer_active(viewer, False)
    before = len(server.requests)
    server.queue(update(rect(0, 0, 1, 1, vnc.ENC_RAW, raw_payload([(2, 2, 2)]))))
    await asyncio.sleep(0.2)
    assert len(server.requests) == before, server.requests[before:]
    instance.set_viewer_active(viewer, True)
    await wait_for(lambda: len(server.requests) > before,
                   label="request resumed for an active viewer")
    instance.detach_viewer(viewer)
    await instance.close("test")
    await server.stop()
    print("  streaming pause ok")


async def check_input() -> None:
    server = await StubVncServer(width=101, height=51).start()
    instance = await connected_instance(server)
    viewer = ViewerSocket()
    await instance.attach_viewer(viewer)

    # Normalized coordinates land on the framebuffer's own pixel grid, and the
    # middle/right buttons swap places between DOM and RFB numbering.
    await instance.handle_client({"type": "pointer", "nx": 0.0, "ny": 0.0,
                                  "mask": 1}, viewer)
    await instance.handle_client({"type": "pointer", "nx": 1.0, "ny": 1.0,
                                  "mask": 4}, viewer)
    await instance.handle_client({"type": "pointer", "nx": 0.5, "ny": 0.5,
                                  "mask": 0}, viewer)
    await wait_for(lambda: len(server.pointer_events) >= 3, label="pointer events")
    assert server.pointer_events[:3] == [(1, 0, 0), (4, 100, 50), (0, 50, 25)], \
        server.pointer_events
    # Out-of-range or nonsense coordinates clamp instead of escaping the screen.
    await instance.handle_client({"type": "pointer", "nx": 9, "ny": -9,
                                  "mask": 0}, viewer)
    await instance.handle_client({"type": "pointer", "nx": "x", "ny": None,
                                  "mask": 0}, viewer)
    await wait_for(lambda: len(server.pointer_events) >= 5, label="clamped pointer")
    assert server.pointer_events[3] == (0, 100, 0), server.pointer_events
    assert server.pointer_events[4] == (0, 100, 0), server.pointer_events

    # Keys travel as X11 keysyms, and typed text as press/release pairs.
    server.key_events.clear()
    await instance.handle_client({"type": "key", "kind": "down", "key": "Shift",
                                  "code": "ShiftLeft"}, viewer)
    await instance.handle_client({"type": "key", "kind": "down", "key": "A",
                                  "code": "KeyA"}, viewer)
    await instance.handle_client({"type": "key", "kind": "up", "key": "A",
                                  "code": "KeyA"}, viewer)
    await wait_for(lambda: len(server.key_events) >= 3, label="key events")
    assert server.key_events[:3] == [(True, 0xFFE1), (True, ord("A")),
                                     (False, ord("A"))], server.key_events
    # Whatever is still held is released when the viewer looks away.
    instance._release_keys()
    await wait_for(lambda: (False, 0xFFE1) in server.key_events,
                   label="held modifier released")

    server.key_events.clear()
    await instance.handle_client({"type": "text", "text": "hi\r\n\t"}, viewer)
    await wait_for(lambda: len(server.key_events) >= 8, label="typed text")
    assert server.key_events[:8] == [
        (True, ord("h")), (False, ord("h")), (True, ord("i")), (False, ord("i")),
        (True, 0xFF0D), (False, 0xFF0D), (True, 0xFF09), (False, 0xFF09)], \
        server.key_events

    server.key_events.clear()
    await instance.handle_client({"type": "cad"}, viewer)
    await wait_for(lambda: len(server.key_events) >= 6, label="ctrl-alt-del")
    assert server.key_events[:6] == [
        (True, 0xFFE3), (True, 0xFFE9), (True, 0xFFFF),
        (False, 0xFFFF), (False, 0xFFE9), (False, 0xFFE3)], server.key_events

    # A wheel notch is a button 4/5 tap; smaller deltas accumulate first.
    server.pointer_events.clear()
    await instance.handle_client({"type": "wheel", "nx": 0.0, "ny": 0.0,
                                  "dx": 0, "dy": 10}, viewer)
    await asyncio.sleep(0.05)
    assert not server.pointer_events, server.pointer_events
    await instance.handle_client({"type": "wheel", "nx": 0.0, "ny": 0.0,
                                  "dx": 0, "dy": 50}, viewer)
    await wait_for(lambda: len(server.pointer_events) >= 2, label="wheel step")
    assert server.pointer_events[:2] == [(1 << 4, 0, 0), (0, 0, 0)], \
        server.pointer_events
    server.pointer_events.clear()
    await instance.handle_client({"type": "wheel", "nx": 0.0, "ny": 0.0,
                                  "dx": 0, "dy": -120}, viewer)
    await wait_for(lambda: len(server.pointer_events) >= 4, label="wheel up steps")
    assert server.pointer_events[0] == (1 << 3, 0, 0), server.pointer_events

    # A full redraw is available on demand even when nothing changed.
    before = len(server.requests)
    await instance.handle_client({"type": "refresh"}, viewer)
    await wait_for(lambda: any(item[0] == 0 for item in server.requests[before:]),
                   label="full redraw request")
    instance.detach_viewer(viewer)
    await instance.close("test")

    # View-only refuses every input path while still painting the screen.
    instance = await connected_instance(server, view_only=True)
    viewer = ViewerSocket()
    await instance.attach_viewer(viewer)
    server.pointer_events.clear()
    server.key_events.clear()
    for payload in ({"type": "pointer", "nx": 0.5, "ny": 0.5, "mask": 1},
                    {"type": "key", "kind": "down", "key": "a", "code": "KeyA"},
                    {"type": "text", "text": "no"},
                    {"type": "wheel", "nx": 0, "ny": 0, "dx": 0, "dy": 500},
                    {"type": "cad"}):
        await instance.handle_client(payload, viewer)
    await asyncio.sleep(0.15)
    assert not server.pointer_events and not server.key_events
    assert instance.payload()["view_only"] is True
    instance.detach_viewer(viewer)
    await instance.close("test")
    await server.stop()
    print("  input ok")


async def check_lifecycle() -> None:
    server = await StubVncServer(width=8, height=4).start()
    registry = vnc.VncRegistry()
    old_manager = vnc._manager
    vnc._manager = registry
    try:
        instance = await registry.create("127.0.0.1", server.port,
                                         label="Lab screen")
        assert vnc.VNC_ID_RE.fullmatch(instance.vnc_id), instance.vnc_id
        payload = registry.instance_payloads()[0]
        assert payload["id"] == instance.vnc_id and payload["connected"] is True
        assert payload["label"] == "Lab screen" and payload["port"] == server.port
        assert payload["width"] == 8 and payload["name"] == "stub screen"
        assert registry.get(instance.vnc_id.lower()) is instance

        # An unwatched connection is dropped, and reattaching dials again.
        config.set_timeouts({"vnc_idle_seconds": 1})
        vnc.idle_settings_changed()
        viewer = ViewerSocket()
        await instance.attach_viewer(viewer)
        instance.detach_viewer(viewer)
        await wait_for(lambda: not instance.connected, timeout=6.0,
                       label="idle disconnect")
        assert registry.get(instance.vnc_id) is instance   # identity survives
        config.set_timeouts({"vnc_idle_seconds": 0})
        vnc.idle_settings_changed()
        before = server.connections
        viewer = ViewerSocket()
        await instance.attach_viewer(viewer)
        assert instance.connected and server.connections == before + 1
        assert instance.idle_task is None                  # zero disables it

        # Closing is final: the catalog forgets it and the socket is gone.
        assert await registry.close(instance.vnc_id) is True
        assert registry.instance_payloads() == []
        assert await registry.close(instance.vnc_id) is False
        try:
            registry.get(instance.vnc_id)
        except vnc.VncError:
            pass
        else:
            raise AssertionError("a closed VNC connection is still resolvable")
        try:
            await instance.ensure_started()
        except vnc.VncError as exc:
            assert "closed" in str(exc), exc
        else:
            raise AssertionError("a closed VNC connection reconnected")

        # A connection that cannot be established leaves no catalog entry.
        await server.stop()
        try:
            await registry.create("127.0.0.1", server.port)
        except vnc.VncError:
            pass
        else:
            raise AssertionError("a failed dial was catalogued")
        assert registry.instance_payloads() == []
    finally:
        config.set_timeouts({"vnc_idle_seconds":
                             config.TIMEOUT_DEFAULTS["vnc_idle_seconds"]})
        await registry.stop("test cleanup")
        vnc._manager = old_manager
        await server.stop()
    print("  lifecycle ok")


async def check_http_surface() -> None:
    server = await StubVncServer(width=8, height=4).start()
    app = build_app()
    runner = aioweb.AppRunner(app)
    await runner.setup()
    site = aioweb.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = "http://127.0.0.1:{}".format(port)
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(url + "/api/ping", headers=headers) as response:
                ping = await response.json()
                assert "vnc" in ping["capabilities"], ping
                assert "vnc-instances" in ping["capabilities"], ping

            async with http.get(url + "/api/timeouts", headers=headers) as response:
                timeouts = (await response.json())["timeouts"]
                assert timeouts["values"]["vnc_idle_seconds"] == 900, timeouts
                assert timeouts["defaults"]["vnc_idle_seconds"] == 900, timeouts

            async with http.get(url + "/api/vnc/instances",
                                headers=headers) as response:
                assert (await response.json())["instances"] == []

            for bad in ({"host": ""}, {"host": "a b"}, {"host": "x", "port": 0.5},
                        {"host": "x", "port": "nope"},
                        {"host": "x", "view_only": "yes"}):
                async with http.post(url + "/api/vnc/instances", headers=headers,
                                     json=bad) as response:
                    assert response.status == 400, (bad, response.status)

            async with http.post(url + "/api/vnc/instances", headers=headers,
                                 json={"host": "127.0.0.1", "port": 1}) as response:
                assert response.status == 502, response.status
                assert "Could not reach" in (await response.json())["error"]

            async with http.post(
                    url + "/api/vnc/instances", headers=headers,
                    json={"host": "127.0.0.1", "port": server.port,
                          "label": "Lab"}) as response:
                created = await response.json()
                assert response.status == 201, created
            vnc_id = created["vnc"]["id"]
            assert created["vnc"]["label"] == "Lab"
            assert created["vnc"]["connected"] is True

            async with http.get(url + "/api/vnc/instances",
                                headers=headers) as response:
                listed = (await response.json())["instances"]
                assert [item["id"] for item in listed] == [vnc_id], listed

            socket = await http.ws_connect(
                url + "/api/ws/vnc/" + vnc_id, headers=headers)
            status = await asyncio.wait_for(socket.receive_json(), 5)
            assert status["type"] == "status" and status["id"] == vnc_id, status
            assert status["width"] == 8 and status["height"] == 4, status
            frame = await asyncio.wait_for(socket.receive(), 5)
            assert frame.type is aiohttp.WSMsgType.BINARY, frame
            assert decode_frame(frame.data)["w"] == 8
            await socket.send_json({"type": "pointer", "nx": 0.5, "ny": 0.5,
                                    "mask": 1})
            await wait_for(lambda: server.pointer_events, label="proxied pointer")
            await socket.close()

            # An unknown ID is a terminal error on the socket, not a hang.
            socket = await http.ws_connect(
                url + "/api/ws/vnc/ZZZZ", headers=headers)
            failure = await asyncio.wait_for(socket.receive_json(), 5)
            assert failure["type"] == "error" and failure["terminal"] is True
            await socket.close()

            async with http.delete(url + "/api/vnc/instances/" + vnc_id,
                                   headers=headers) as response:
                assert response.status == 200, await response.text()
            async with http.delete(url + "/api/vnc/instances/" + vnc_id,
                                   headers=headers) as response:
                assert response.status == 404, response.status

            # Unauthenticated callers see nothing.
            async with http.get(url + "/api/vnc/instances") as response:
                assert response.status == 401, response.status
    finally:
        await runner.cleanup()
        await server.stop()
    print("  HTTP surface ok")


def png_pixels(image: bytes) -> tuple:
    """Decode our own PNG the long way round, to prove it is a real one."""
    assert image[:8] == b"\x89PNG\r\n\x1a\n", image[:8]
    offset = 8
    header = None
    data = b""
    while offset < len(image):
        length = struct.unpack(">I", image[offset:offset + 4])[0]
        tag = image[offset + 4:offset + 8]
        body = image[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", image[offset + 8 + length:offset + 12 + length])[0]
        assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF, tag
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            data += body
        offset += 12 + length
    assert header is not None and header[2] == 8 and header[3] == 2, header
    width, height = header[0], header[1]
    raw = zlib.decompress(data)
    stride = width * 3
    rows = []
    for index in range(height):
        line = raw[index * (stride + 1):(index + 1) * (stride + 1)]
        assert line[0] == 0, "this encoder never predicts"
        rows.append(line[1:])
    return width, height, rows


async def check_agent_screenshot() -> None:
    """The picture an engine turn gets, without a viewer or an image library."""
    server = await StubVncServer(width=4, height=3).start()
    instance = await connected_instance(server)
    # Four wide is enough to see a strided reduction keep the right columns.
    colours = [(10, 20, 30), (40, 50, 60), (70, 80, 90), (100, 110, 120)]
    server.queue(update(rect(0, 0, 4, 3, vnc.ENC_RAW,
                             raw_payload(colours * 3))))
    await wait_for(lambda: instance.frame_seq >= 1, label="first update")

    shot = instance.screenshot()
    assert (shot["step"], shot["out_width"], shot["out_height"]) == (1, 4, 3), shot
    width, height, rows = png_pixels(shot["png"])
    assert (width, height) == (4, 3)
    assert rows[0] == b"".join(bytes(colour) for colour in colours), rows[0]

    # A screen larger than the requested image is reduced by a whole factor,
    # and every reported number describes what the caller actually received.
    small = instance.screenshot(max_dimension=vnc.AGENT_MIN_DIMENSION)
    assert small["step"] == 1, small          # 4x3 is already small enough
    reduced = instance.screenshot(max_dimension=2)   # clamped to the minimum
    assert reduced["step"] == 1, reduced
    frame, screen_width, _screen_height = instance.frame_snapshot()
    forced = vnc._rgb_rows(frame, screen_width, 0, 0, 4, 3, 2)
    assert len(forced) == 2 and len(forced[0]) == 6, forced
    assert forced[0] == bytes(colours[0]) + bytes(colours[2]), forced[0]

    # One self-consistent view: a size that no longer matches the buffer is
    # refused rather than encoded, so a resize cannot tear a worker's picture.
    instance.width = 9
    try:
        instance.screenshot()
    except vnc.VncError as exc:
        assert "not been received" in str(exc), exc
    else:
        raise AssertionError("a mismatched framebuffer was captured")
    instance.width = 4

    region = instance.screenshot(region={"x": 1, "y": 1, "width": 2, "height": 2})
    assert (region["x"], region["y"], region["width"], region["height"]) == \
        (1, 1, 2, 2), region
    width, height, rows = png_pixels(region["png"])
    assert (width, height) == (2, 2)
    assert rows[0] == bytes(colours[1]) + bytes(colours[2]), rows[0]
    # A region outside the screen is clamped onto it rather than refused.
    clamped = instance.screenshot(region={"x": 3, "y": 2, "width": 900,
                                          "height": 900})
    assert (clamped["width"], clamped["height"]) == (1, 1), clamped

    # With no viewer the update loop is parked, so the agent turns it by hand.
    before = len(server.requests)
    assert await instance.sync(timeout=0.3) is False, "an idle screen is current"
    assert len(server.requests) > before, "the agent must drive the request"
    server.queue(update(rect(0, 0, 4, 3, vnc.ENC_RAW, raw_payload(colours * 3))))
    assert await instance.sync(timeout=2.0) is True
    assert await instance.settle(quiet_ms=100, timeout_ms=1500) is True

    await instance.close("test")
    await server.stop()
    print("  agent screenshot ok")


async def check_agent_input() -> None:
    """Everything a person does with a mouse and keyboard, as RFB messages."""
    server = await StubVncServer(width=101, height=51).start()
    instance = await connected_instance(server)
    server.pointer_events.clear()

    assert await instance.agent_move(50, 25) == (50, 25)
    assert await instance.agent_move(-5, 9999) == (0, 50), "coordinates clamp"
    await wait_for(lambda: len(server.pointer_events) >= 2, label="pointer moves")
    assert server.pointer_events[:2] == [(0, 50, 25), (0, 0, 50)]

    server.pointer_events.clear()
    await instance.agent_click(10, 12)
    await wait_for(lambda: len(server.pointer_events) >= 3, label="click")
    assert server.pointer_events[:3] == [(0, 10, 12), (1, 10, 12), (0, 10, 12)]

    server.pointer_events.clear()
    server.key_events.clear()
    await instance.agent_click(10, 12, button="right", count=2,
                               modifiers=["Control"])
    await wait_for(lambda: len(server.pointer_events) >= 5, label="double click")
    assert server.pointer_events[:5] == [
        (0, 10, 12), (4, 10, 12), (0, 10, 12), (4, 10, 12), (0, 10, 12)]
    assert server.key_events[:1] == [(True, 0xFFE3)]
    assert server.key_events[-1] == (False, 0xFFE3), "a held modifier is released"

    # A drag really travels: press, intermediate positions, release.
    server.pointer_events.clear()
    await instance.agent_drag(0, 0, 100, 50, steps=4, duration_ms=0)
    await wait_for(lambda: len(server.pointer_events) >= 7, label="drag")
    assert server.pointer_events[0] == (0, 0, 0)
    assert server.pointer_events[1] == (1, 0, 0), "the button goes down first"
    assert [item[0] for item in server.pointer_events[2:6]] == [1, 1, 1, 1]
    assert server.pointer_events[5] == (1, 100, 50)
    assert server.pointer_events[6] == (0, 100, 50), "and up at the end"

    server.pointer_events.clear()
    await instance.agent_scroll(5, 6, direction="up", clicks=2)
    await wait_for(lambda: len(server.pointer_events) >= 4, label="scroll")
    assert server.pointer_events[:4] == [
        (1 << 3, 5, 6), (0, 5, 6), (1 << 3, 5, 6), (0, 5, 6)]

    server.key_events.clear()
    assert await instance.agent_type("hi\n") == 3
    await wait_for(lambda: len(server.key_events) >= 6, label="typed text")
    assert server.key_events[:6] == [
        (True, ord("h")), (False, ord("h")), (True, ord("i")), (False, ord("i")),
        (True, 0xFF0D), (False, 0xFF0D)]

    server.key_events.clear()
    assert await instance.agent_press("Delete", modifiers=["Control", "alt"]) == \
        "Control+Alt+Delete"
    await wait_for(lambda: len(server.key_events) >= 4, label="shortcut")
    assert server.key_events[:4] == [
        (True, 0xFFE3), (True, 0xFFE9), (True, 0xFFFF), (False, 0xFFFF)]
    assert server.key_events[-2:] == [(False, 0xFFE9), (False, 0xFFE3)]

    server.key_events.clear()
    assert await instance.agent_press("space") == "Space"
    await wait_for(lambda: len(server.key_events) >= 2, label="space")
    assert server.key_events[:2] == [(True, 32), (False, 32)]

    server.key_events.clear()
    assert await instance.agent_press("esc") == "Escape"
    assert await instance.agent_press("f5") == "F5"
    assert await instance.agent_press("a", count=2) == "a"
    await wait_for(lambda: len(server.key_events) >= 8, label="named keys")
    assert server.key_events[:2] == [(True, 0xFF1B), (False, 0xFF1B)]
    assert server.key_events[2:4] == [(True, 0xFFC2), (False, 0xFFC2)]
    assert server.key_events[4:8] == [(True, ord("a")), (False, ord("a"))] * 2

    # Refusals name the mistake instead of guessing at it.
    for call, expected in (
            (instance.agent_press("Hyperspace"), "unknown key"),
            (instance.agent_press("a", modifiers=["Enter"]), "not a modifier"),
            (instance.agent_click(1, 1, button="thumb"), "button must be"),
            (instance.agent_click(1, 1, count=99), "click count"),
            (instance.agent_click(1, 1, count=2.5), "whole number"),
            (instance.agent_click(1, 1, count=float("inf")), "whole number"),
            (instance.agent_scroll(1, 1, direction="sideways"), "direction"),
            (instance.agent_move("x", 1), "coordinates must be numbers"),
            (instance.agent_type(""), "supply the text")):
        try:
            await call
        except vnc.VncError as exc:
            assert expected in str(exc), (expected, exc)
        else:
            raise AssertionError("a bad {} was accepted".format(expected))

    await instance.close("test")

    # View only refuses the whole agent surface, exactly like a viewer's input.
    instance = await connected_instance(server, view_only=True)
    server.pointer_events.clear()
    server.key_events.clear()
    for call in (instance.agent_click(1, 1), instance.agent_move(1, 1),
                 instance.agent_drag(1, 1, 2, 2), instance.agent_scroll(1, 1),
                 instance.agent_type("no"), instance.agent_press("Enter")):
        try:
            await call
        except vnc.VncError as exc:
            assert "view only" in str(exc), exc
        else:
            raise AssertionError("view only accepted agent input")
    await asyncio.sleep(0.1)
    assert not server.pointer_events and not server.key_events
    await instance.close("test")
    await server.stop()
    print("  agent input ok")


async def check_agent_bridge(session_id: int) -> None:
    """The turn-bound MCP bridge: identity, selection, tabs and refusals."""
    server = await StubVncServer(width=8, height=4).start()
    registry = vnc.VncRegistry()
    old_manager = vnc._manager
    vnc._manager = registry
    session_capture = CaptureSocket()
    updates_capture = CaptureSocket()
    hub = runner.hub(session_id)
    hub.status = "running"
    hub._active_turn_id = "vnc-turn"
    hub._vnc_activity_announced = set()
    hub.attach(session_capture)
    runner.updates_attach(updates_capture)
    try:
        assert vnc_agent.turn_mcp(session_id, "vnc-turn") is None
        vnc_agent._server = object()
        try:
            descriptor = vnc_agent.turn_mcp(session_id, "vnc-turn")
        finally:
            vnc_agent._server = None
        assert descriptor["name"] == "puppy_vnc"
        assert descriptor["args"] == ["-m", "puppy.vnc_agent"]
        assert descriptor["env"]["PUPPY_VNC_SESSION_ID"] == str(session_id)
        assert descriptor["engine_guidance"] == system_prompts.vnc_prompt()

        call = lambda method, **params: vnc_agent._dispatch({
            "session_id": session_id, "turn_id": "vnc-turn",
            "method": method, "params": params})

        empty = await call("screens")
        assert "no VNC connection open" in empty["text"], empty
        try:
            await call("screenshot")
        except vnc_agent.VncAgentError as exc:
            assert "no remote screen is selected" in str(exc), exc
        else:
            raise AssertionError("an unbound session was given a screen")

        opened = await call("connect", host="127.0.0.1", port=server.port,
                            password="", label="Lab")
        vnc_id = opened["text"].split(" ")[1]
        assert vnc.VNC_ID_RE.fullmatch(vnc_id), opened["text"]
        assert registry.bindings[session_id] == vnc_id
        assert opened["image"]["mime_type"] == "image/png"
        assert "Image is the whole 8x4 screen at 1:1." in opened["text"], opened
        assert "Lab" in opened["text"]

        listed = await call("screens")
        assert "this chat's current screen" in listed["text"], listed

        # The tab appears beside the chat exactly once for the turn.
        await wait_for(lambda: any(item.get("type") == "vnc_activity"
                                   for item in session_capture.messages))
        await wait_for(lambda: any(item.get("type") == "vnc_activity"
                                   for item in updates_capture.messages))
        assert [item["vnc_id"] for item in session_capture.messages
                if item.get("type") == "vnc_activity"] == [vnc_id]

        moved = await call("click", x=3, y=2)
        assert "Left click at 3,2." in moved["text"], moved
        assert "image" not in moved, "a picture is only sent when asked for"
        pictured = await call("press", key="Enter", screenshot=True)
        assert "Pressed Enter." in pictured["text"] and pictured["image"]
        waited = await call("wait", quiet_ms=50, timeout_ms=800)
        assert "unchanged" in waited["text"], waited

        # An explicit ID selects; an unknown one is refused, not invented.
        assert (await call("screenshot", vnc_id=vnc_id))["image"]
        try:
            await call("screenshot", vnc_id="ZZZZ")
        except vnc_agent.VncAgentError as exc:
            assert "closed or unknown" in str(exc), exc
        else:
            raise AssertionError("an unknown VNC ID was accepted")

        # A turn that is no longer running owns no tools.
        hub._active_turn_id = "another-turn"
        try:
            await call("screenshot")
        except vnc_agent.VncAgentError as exc:
            assert "no longer running" in str(exc), exc
        else:
            raise AssertionError("a stale turn kept its VNC tools")
        hub._active_turn_id = "vnc-turn"

        closed = await call("disconnect", vnc_id=vnc_id)
        assert "Closed VNC {}".format(vnc_id) in closed["text"], closed
        assert registry.instance_payloads() == []
        assert session_id not in registry.bindings, "a closed screen is unbound"
    finally:
        hub.status = "idle"
        hub._active_turn_id = ""
        hub.detach(session_capture)
        runner.updates_detach(updates_capture)
        await registry.stop("test cleanup")
        vnc._manager = old_manager
        await server.stop()
    print("  agent bridge ok")


def check_agent_contract() -> None:
    """The wording and shape the console and the engine both depend on."""
    names = {tool["name"] for tool in vnc_agent.TOOLS}
    assert names == {"screens", "connect", "screenshot", "move", "click", "drag",
                     "scroll", "type", "press", "wait", "disconnect"}, names
    for tool in vnc_agent.TOOLS:
        assert tool["inputSchema"]["additionalProperties"] is False, tool["name"]
        assert tool["description"] and tool["annotations"], tool["name"]
    # The mention wording is one contract shared with app.js.
    assert '"@VNC A8AR"' in vnc_agent.TOOL_INSTRUCTIONS
    assert '"@VNC host:port password"' in vnc_agent.TOOL_INSTRUCTIONS
    policy = "Node VNC policy."
    config.set_system_prompts(
        config.get("system_prompt.custom"),
        config.get("system_prompt.remote_workspace"),
        config.get("system_prompt.browser"),
        config.get("system_prompt.terminal"), policy,
        config.get("system_prompt.spawn"))
    try:
        assert vnc_agent.instructions() == \
            policy + " " + vnc_agent.TOOL_INSTRUCTIONS
        session = {"cwd": str(TEST_ROOT)}
        vnc_agent._server = object()
        descriptor = vnc_agent.turn_mcp(1, "contract")
        vnc_agent._server = None
        claude = ClaudeDriver().build_cmd(session, True, "hi", "pin",
                                          vnc_mcp=descriptor)
        servers = json.loads(claude[claude.index("--mcp-config") + 1])
        assert set(servers["mcpServers"]) == {"puppy_vnc"}, servers
        assert claude[claude.index("--append-system-prompt") + 1] == policy
        codex = CodexDriver().turn_context(session, True, "hi", "pin",
                                           vnc_mcp=descriptor)
        assert "<puppy_vnc_policy>" in codex["prompt"] and policy in codex["prompt"]
        opencode = OpenCodeDriver().turn_context(session, True, "hi", "pin",
                                                 vnc_mcp=descriptor)
        assert [item["name"] for item in opencode["mcp_servers"]] == ["puppy_vnc"]
    finally:
        config.set_system_prompts(
            config.get("system_prompt.custom"),
            config.get("system_prompt.remote_workspace"),
            config.get("system_prompt.browser"),
            config.get("system_prompt.terminal"),
            config.DEFAULT_VNC_SYSTEM_PROMPT,
            config.get("system_prompt.spawn"))
    print("  agent contract ok")


def check_config_shape() -> None:
    exported = config.export_data()
    assert exported["vnc"] == {"idle_timeout": 900}, exported["vnc"]
    assert config.normalize_import(exported) == exported
    outdated = config.export_data()
    outdated.pop("vnc")
    try:
        config.normalize_import(outdated)
    except ValueError as exc:
        assert "vnc" in str(exc), exc
    else:
        raise AssertionError("a config without the VNC section was accepted")
    assert exported["system_prompt"]["vnc"] == config.DEFAULT_VNC_SYSTEM_PROMPT
    outdated = config.export_data()
    outdated["system_prompt"].pop("vnc")
    try:
        config.normalize_import(outdated)
    except ValueError as exc:
        assert "vnc" in str(exc), exc
    else:
        raise AssertionError("a config without the VNC prompt was accepted")
    assert protocol.VNC_CAPABILITY in protocol.BASE_CAPABILITIES
    assert protocol.VNC_INSTANCES_CAPABILITY in \
        protocol.execution_capabilities(include_terminal=False)
    print("  config and capabilities ok")


def check_static_contract() -> None:
    """The console's half of the wire contract, checked against the sources."""
    app_js = (BASE / "puppy" / "static" / "app.js").read_text()
    app_css = (BASE / "puppy" / "static" / "app.css").read_text()
    index = (BASE / "puppy" / "static" / "index.html").read_text()
    assert 'data-act="new-vnc"' in index
    assert "class VncView" in app_js
    assert "function modalNewVnc" in app_js
    # The composer's half of the mention contract vnc_agent.TOOL_INSTRUCTIONS
    # describes: existing screens by ID, and the wizard that builds a target.
    assert "`@VNC ${inst.id}`" in app_js
    assert "function modalVncShortcut" in app_js
    assert "function vncShortcutText" in app_js
    assert "handleVncActivity" in app_js and "vnc_activity" in app_js
    assert "system-prompt-vnc" in app_js and "vncDefault" in app_js
    assert "ws/vnc/${encodeURIComponent(this.tab.vncId)}" in app_js
    assert "vnc/instances" in app_js
    # The two frame kinds the node sends, and nothing else to decode them with.
    assert "putImageData" in app_js and "drawImage" in app_js
    assert "vnc_instances" in app_js
    # The pane must reuse the browser's own surfaces rather than restate them.
    for shared in ("br-bar", "br-meta", "br-ident", "br-stats", "br-stage",
                   "br-screen", "br-type", "term-dead"):
        assert shared in app_js.split("class VncView")[1].split(
            "/* ================= SettingsView")[0], shared
    assert ".vnc-target{" in app_css and ".tab .t-dot.vnc{" in app_css
    print("  console contract ok")


async def main() -> None:
    try:
        config.load()
        db.connect()
        check_config_shape()
        check_static_contract()
        check_des()
        check_validation()
        check_pixel_helpers()
        await check_handshakes()
        await check_encodings()
        await check_hostile_updates()
        await check_viewer_frames()
        await check_large_viewer_refresh()
        await check_streaming_pause()
        await check_input()
        await check_lifecycle()
        await check_http_surface()
        check_agent_contract()
        await check_agent_screenshot()
        await check_agent_input()
        await check_agent_bridge(db.create_session(
            "vnc agent", "codex", str(TEST_ROOT), "", "", "#7aa2f7",
            "danger-full-access"))
        print("vnc tests passed")
    finally:
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
