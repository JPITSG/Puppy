#!/usr/bin/env python3
"""No-quota tests for Puppy's built-in Remote Desktop client.

The cryptography is held to the published MS-NLMP NTLMv2 vectors, the bitmap
decoder to a pixel-by-pixel transliteration of the reference RLE decoder over
random order streams, and the whole connection to a stub RDP server on
loopback that speaks the real sequence: X.224 negotiation, TLS, CredSSP with
NTLMv2 (verifying what the client proves and sends), MCS and GCC, the client
info, licensing, capability exchange, finalization, fast-path and slow-path
bitmap updates, deactivation, error info and input.  The certificate is a
throwaway one made by the openssl command for this run.  No Windows server,
viewer, image library, engine or external network is involved.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import random
import shutil
import ssl
import struct
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tests.scratch import private_root  # noqa: E402

TEST_ROOT = private_root("rdp-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

import aiohttp  # noqa: E402
from aiohttp import web as aioweb  # noqa: E402

from puppy import config, db, protocol, rdp, runner, vnc, vnc_agent  # noqa: E402
from puppy.web import build_app  # noqa: E402
from backend.puppy_backend.app import build_app as backend_app  # noqa: E402

h = bytes.fromhex


async def wait_for(predicate, timeout=5.0, label="condition"):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for {}".format(label))


class ViewerSocket:
    def __init__(self):
        self.texts = []
        self.frames = []

    async def send_str(self, payload):
        self.texts.append(json.loads(payload))

    async def send_bytes(self, payload):
        self.frames.append(bytes(payload))

    async def close(self, *args, **kwargs):
        pass

    def messages(self, kind):
        return [item for item in self.texts if item.get("type") == kind]


def make_certificate(folder: Path) -> tuple:
    cert, key = folder / "cert.pem", folder / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=stub-desktop"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(cert), str(key)


# --------------------------------------------------------------------------
# crypto
# --------------------------------------------------------------------------

def check_crypto() -> None:
    assert rdp._md4_python(b"").hex() == "31d6cfe0d16ae931b73c59d7e0c089c0"
    assert rdp._md4_python(b"abc").hex() == "a448017aaf21d8525fc10ae87aa6729d"
    assert rdp._md4_python(b"1234567890" * 8).hex() == "e33b4ddc9c38f2199c3e7b164fcc0536"
    assert rdp.md4(b"message digest") == rdp._md4_python(b"message digest")
    assert rdp.Rc4(b"Key").update(b"Plaintext").hex() == "bbf316e8d940af0ad3"
    assert rdp.Rc4(b"Wiki").update(b"pedia").hex() == "1021bf0420"

    # MS-NLMP 4.2.4: NTLMv2 with the published inputs, byte for byte.
    assert rdp.ntowfv2("Password", "User", "Domain").hex() == \
        "0c868a403bfd7a93a3001ef22ef02e3f"
    target = rdp._av_blob([(rdp.MSV_AV_NB_DOMAIN, "Domain".encode("utf-16-le")),
                           (rdp.MSV_AV_NB_COMPUTER, "Server".encode("utf-16-le"))])
    flags = 0xE28A8233
    name = "Domain".encode("utf-16-le")
    challenge = (b"NTLMSSP\x00" + struct.pack("<I", 2) +
                 struct.pack("<HHI", len(name), len(name), 56) +
                 struct.pack("<I", flags) + h("0123456789abcdef") + b"\x00" * 8 +
                 struct.pack("<HHI", len(target), len(target), 56 + len(name)) +
                 h("0601b01d0000000f") + name + target)
    ntlm = rdp.Ntlm("User", "Domain", "Password", "COMPUTER",
                    client_challenge=b"\xaa" * 8, session_key=b"\x55" * 16,
                    timestamp=b"\x00" * 8)
    ntlm.negotiate()
    auth = ntlm.authenticate(challenge)

    def field(offset):
        length, _max, pos = struct.unpack_from("<HHI", auth, offset)
        return auth[pos:pos + length]
    assert field(12).hex() == "86c35097ac9cec102554764a57cccc19" + "aa" * 8
    assert field(20)[:16].hex() == "68cd0ab851e51c96aabc927bebef6a1c"
    assert field(52).hex() == "c5dad2544fc9799094ce1ce90bc9d03e"
    assert field(28) == "Domain".encode("utf-16-le")
    assert field(36) == "User".encode("utf-16-le")
    assert field(44) == "COMPUTER".encode("utf-16-le")
    assert ntlm.server_version == "Windows 6.1 build 7600", ntlm.server_version
    assert ntlm.server_name == "Server" and ntlm.server_domain == "Domain"
    assert hashlib.md5(b"\x55" * 16 + rdp._SEALING_CLIENT).hexdigest() == \
        "59f600973cc4960a25480a7c196e4c58"
    assert ntlm.send_sign.hex() == "4788dc861b4782f35d43fd98fe1a2d39"
    sealed = ntlm.seal("Plaintext".encode("utf-16-le"))
    assert sealed[16:].hex() == "54e50165bf1936dc996020c1811b0f06fb5f"
    assert sealed[:16].hex() == "01000000" "7fb38ec5c55d4976" "00000000"

    # A challenge without extended session security is refused, not downgraded.
    weak = bytearray(challenge)
    struct.pack_into("<I", weak, 20, flags & ~rdp.NTLMSSP_NEGOTIATE_EXTENDED_SESSIONSECURITY)
    try:
        rdp.Ntlm("User", "Domain", "Password").authenticate(bytes(weak))
    except rdp.RdpError as exc:
        assert "legacy NTLM" in str(exc), exc
    else:
        raise AssertionError("legacy NTLM was accepted")

    # A stamped challenge means a MIC and MsvAvFlags, with a zero LMv2.
    stamped = rdp._av_blob([(rdp.MSV_AV_NB_COMPUTER, "S".encode("utf-16-le")),
                            (rdp.MSV_AV_TIMESTAMP, b"\x01" * 8)])
    message = (b"NTLMSSP\x00" + struct.pack("<I", 2) + struct.pack("<HHI", 0, 0, 56) +
               struct.pack("<I", flags) + b"\x11" * 8 + b"\x00" * 8 +
               struct.pack("<HHI", len(stamped), len(stamped), 56) +
               h("0a0061470000000f") + stamped)
    client = rdp.Ntlm("u", "", "p", session_key=b"\x22" * 16)
    negotiate = client.negotiate()
    auth = client.authenticate(message)
    assert struct.unpack_from("<HHI", auth, 28)[2] == 88, "the payload follows a MIC"
    lm_len, _, lm_at = struct.unpack_from("<HHI", auth, 12)
    assert auth[lm_at:lm_at + lm_len] == b"\x00" * 24
    zeroed = auth[:72] + b"\x00" * 16 + auth[88:]
    assert auth[72:88] == hmac.new(b"\x22" * 16, negotiate + message + zeroed,
                                   hashlib.md5).digest()
    nt_len, _, nt_at = struct.unpack_from("<HHI", auth, 20)
    blob = auth[nt_at + 16:nt_at + nt_len]
    assert blob[8:16] == b"\x01" * 8, "the server's timestamp is the one used"
    pairs = dict(rdp.av_pairs(blob[28:]))
    assert pairs[rdp.MSV_AV_FLAGS] == struct.pack("<I", 2), pairs

    # DER: requests, an NTSTATUS error code, credentials and the binding key.
    request = rdp.ts_request(6, nego=b"NTLMSSP\x00abc", pub_key_auth=b"x", nonce=b"n" * 32)
    parsed = rdp.parse_ts_request(request)
    assert (parsed["version"], parsed["nego"], parsed["pub_key_auth"], parsed["nonce"]) == \
        (6, b"NTLMSSP\x00abc", b"x", b"n" * 32), parsed
    error = rdp._der(0x30, rdp._der(0xA0, rdp._der_integer(6)) +
                     rdp._der(0xA4, rdp._der_integer(-1073741715)))
    assert rdp.parse_ts_request(error)["error"] == 0xC000006D
    assert "rejected the user name or password" in rdp.sign_in_refusal(0xC000006D)
    assert "0x12345678" in rdp.sign_in_refusal(0x12345678)
    assert rdp._increment_le(b"\xff\xff\x01") == b"\x00\x00\x02"
    assert rdp.split_username("CORP\\alice") == ("alice", "CORP")
    assert rdp.split_username(" alice@corp.example ") == ("alice@corp.example", "")
    spnego = rdp._der(0xA1, rdp._der(0x30, rdp._der(0xA0, rdp._der(0x0A, b"\x01")) +
                                     rdp._der(0xA2, rdp._der(0x04, b"NTLMSSP\x00x"))))
    assert rdp._spnego_token(spnego) == b"NTLMSSP\x00x"
    print("  crypto vectors ok")


def check_certificate(cert_path: str) -> None:
    der = ssl.PEM_cert_to_DER_cert(Path(cert_path).read_text())
    key = rdp.subject_public_key(der)
    # An RSA key's bit string is itself a DER SEQUENCE of modulus and exponent.
    (tag, body), = rdp._der_items(key)
    assert tag == 0x30 and [item[0] for item in rdp._der_items(body)] == [0x02, 0x02]
    assert rdp.certificate_name(der) == "stub-desktop"
    print_ = rdp.fingerprint(der)
    assert print_ == ":".join(hashlib.sha256(der).hexdigest().upper()[i:i + 2]
                              for i in range(0, 64, 2))
    try:
        rdp.subject_public_key(b"\x30\x03\x02\x01\x00")
    except rdp.RdpError:
        pass
    else:
        raise AssertionError("a malformed certificate was accepted")
    print("  certificate parsing ok")


# --------------------------------------------------------------------------
# bitmaps
# --------------------------------------------------------------------------

def naive_rle(src: bytes, width: int, height: int, px: int):
    """The reference decoder, transliterated pixel by pixel: None on failure."""
    row = width * px
    out = bytearray(row * height)
    end = len(out)
    white, black = b"\xff" * px, b"\x00" * px
    xor = lambda a, b: bytes(x ^ y for x, y in zip(a, b))  # noqa: E731
    state = {"d": 0}

    class Fail(Exception):
        pass

    def put(value):
        d = state["d"]
        if d + px > end:
            raise Fail
        out[d:d + px] = value
        state["d"] = d + px

    def above():
        d = state["d"]
        return bytes(out[d - row:d - row + px])

    def capacity(count):
        if count < 0 or state["d"] + count * px > end:
            raise Fail

    fg = white
    insert = False
    first = True
    s = 0
    n = len(src)
    try:
        while s < n:
            if first and state["d"] >= row:
                first = False
                insert = False
            hdr = src[s]
            if hdr & 0xC0 != 0xC0:
                code = hdr >> 5
            elif hdr & 0xF0 == 0xF0:
                code = hdr
            else:
                code = hdr >> 4

            def length_of():
                if code in (0x02,):
                    if hdr & 0x1F:
                        return (hdr & 0x1F) * 8, 1
                    if s + 2 > n:
                        raise Fail
                    return src[s + 1] + 1, 2
                if code == 0x0D:
                    if hdr & 0x0F:
                        return (hdr & 0x0F) * 8, 1
                    if s + 2 > n:
                        raise Fail
                    return src[s + 1] + 1, 2
                if code in (0, 1, 3, 4):
                    if hdr & 0x1F:
                        return hdr & 0x1F, 1
                    if s + 2 > n:
                        raise Fail
                    return src[s + 1] + 32, 2
                if code in (0x0C, 0x0E):
                    if hdr & 0x0F:
                        return hdr & 0x0F, 1
                    if s + 2 > n:
                        raise Fail
                    return src[s + 1] + 16, 2
                if code in (0xF0, 0xF1, 0xF2, 0xF3, 0xF4, 0xF6, 0xF7, 0xF8):
                    if s + 3 > n:
                        raise Fail
                    return src[s + 1] | src[s + 2] << 8, 3
                raise Fail

            if code in (0x00, 0xF0):
                length, advance = length_of()
                s += advance
                if first:
                    if insert:
                        capacity(1)
                        put(fg)
                        length -= 1
                    capacity(length)
                    for _ in range(length):
                        put(black)
                else:
                    if insert:
                        capacity(1)
                        put(xor(above(), fg))
                        length -= 1
                    capacity(length)
                    for _ in range(length):
                        put(above())
                insert = True
                continue
            insert = False
            if code in (0x01, 0xF1, 0x0C, 0xF6):
                length, advance = length_of()
                s += advance
                if code in (0x0C, 0xF6):
                    if s + px > n:
                        raise Fail
                    fg = bytes(src[s:s + px])
                    s += px
                capacity(length)
                for _ in range(length):
                    put(fg if first else xor(above(), fg))
            elif code in (0x0E, 0xF8):
                length, advance = length_of()
                s += advance
                if s + 2 * px > n:
                    raise Fail
                a, b = bytes(src[s:s + px]), bytes(src[s + px:s + 2 * px])
                s += 2 * px
                capacity(2 * length)
                for _ in range(length):
                    put(a)
                    put(b)
            elif code in (0x03, 0xF3):
                length, advance = length_of()
                s += advance
                if s + px > n:
                    raise Fail
                colour = bytes(src[s:s + px])
                s += px
                capacity(length)
                for _ in range(length):
                    put(colour)
            elif code in (0x02, 0xF2, 0x0D, 0xF7):
                length, advance = length_of()
                s += advance
                if code in (0x0D, 0xF7):
                    if s + px > n:
                        raise Fail
                    fg = bytes(src[s:s + px])
                    s += px
                remaining = length
                while remaining > 0:
                    if s >= n:
                        raise Fail
                    mask = src[s]
                    s += 1
                    bits = min(8, remaining)
                    capacity(bits)
                    for bit in range(bits):
                        hit = mask >> bit & 1
                        if first:
                            put(fg if hit else black)
                        else:
                            put(xor(above(), fg) if hit else above())
                    remaining -= bits
            elif code in (0x04, 0xF4):
                length, advance = length_of()
                s += advance
                capacity(length)
                if s + length * px > n:
                    raise Fail
                for _ in range(length):
                    put(bytes(src[s:s + px]))
                    s += px
            elif code in (0xF9, 0xFA):
                s += 1
                mask = 0x03 if code == 0xF9 else 0x05
                capacity(8)
                for bit in range(8):
                    hit = mask >> bit & 1
                    if first:
                        put(fg if hit else black)
                    else:
                        put(xor(above(), fg) if hit else above())
            elif code == 0xFD:
                s += 1
                capacity(1)
                put(white)
            elif code == 0xFE:
                s += 1
                capacity(1)
                put(black)
            else:
                raise Fail
    except Fail:
        return None
    return bytes(out)


def random_order(rng: random.Random, px: int) -> bytes:
    pixel = lambda: bytes(rng.randrange(256) for _ in range(px))  # noqa: E731
    choice = rng.randrange(20)
    small = rng.randrange(1, 32)
    if choice == 0:                                   # regular background run
        return bytes((small,)) if rng.random() < .7 else bytes((0, rng.randrange(256)))
    if choice == 1:                                   # regular foreground run
        return bytes((0x20 | small,))
    if choice == 2:                                   # foreground/background image
        count = small
        return bytes((0x40 | count,)) + bytes(rng.randrange(256) for _ in range(count))
    if choice == 3:
        extra = rng.randrange(256)
        return bytes((0x40, extra)) + bytes(rng.randrange(256) for _ in range((extra + 8) // 8))
    if choice == 4:                                   # colour run
        return bytes((0x60 | small,)) + pixel()
    if choice == 5:                                   # colour image
        return bytes((0x80 | small,)) + b"".join(pixel() for _ in range(small))
    if choice == 6:                                   # lite set-fg run
        return bytes((0xC0 | rng.randrange(1, 16),)) + pixel()
    if choice == 7:                                   # lite set-fg image
        count = rng.randrange(1, 16)
        return bytes((0xD0 | count,)) + pixel() + bytes(rng.randrange(256) for _ in range(count))
    if choice == 8:                                   # lite dithered run
        return bytes((0xE0 | rng.randrange(1, 16),)) + pixel() + pixel()
    if choice == 9:                                   # mega runs
        code = rng.choice((0xF0, 0xF1, 0xF3, 0xF6, 0xF8))
        length = rng.randrange(0, 300)
        tail = {0xF3: pixel(), 0xF6: pixel(), 0xF8: pixel() + pixel()}.get(code, b"")
        return bytes((code,)) + struct.pack("<H", length) + tail
    if choice == 10:                                  # mega images
        code = rng.choice((0xF2, 0xF7, 0xF4))
        length = rng.randrange(0, 120)
        if code == 0xF4:
            return bytes((code,)) + struct.pack("<H", length) + \
                b"".join(pixel() for _ in range(length))
        return bytes((code,)) + struct.pack("<H", length) + \
            (pixel() if code == 0xF7 else b"") + \
            bytes(rng.randrange(256) for _ in range((length + 7) // 8))
    if choice == 11:
        return bytes((rng.choice((0xF9, 0xFA, 0xFD, 0xFE)),))
    if choice == 12:                                  # extended regular lengths
        return bytes((0x60, rng.randrange(256))) + pixel()
    if choice == 13:
        return bytes((0x20, rng.randrange(256)))
    if choice == 14:
        return bytes((0xC0, rng.randrange(256))) + pixel()
    if choice == 15:
        extra = rng.randrange(256)
        return bytes((0xD0, extra)) + pixel() + \
            bytes(rng.randrange(256) for _ in range((extra + 8) // 8))
    if choice == 16:
        extra = rng.randrange(256)
        return bytes((0x80, extra)) + b"".join(pixel() for _ in range(extra + 32))
    if choice == 17:
        return bytes((0xE0, rng.randrange(256))) + pixel() + pixel()
    return bytes((small,))                            # more background runs


def order_pixels(order: bytes) -> int:
    """How many pixels one generated order writes."""
    hdr = order[0]
    code = hdr >> 5 if hdr & 0xC0 != 0xC0 else (hdr if hdr & 0xF0 == 0xF0 else hdr >> 4)
    if code in (0xF9, 0xFA):
        return 8
    if code in (0xFD, 0xFE):
        return 1
    if code >= 0xF0:
        count = order[1] | order[2] << 8
        return 2 * count if code == 0xF8 else count
    if code in (0x02, 0x0D):
        mask = 0x1F if code == 0x02 else 0x0F
        return (hdr & mask) * 8 if hdr & mask else order[1] + 1
    if code in (0, 1, 3, 4):
        return hdr & 0x1F if hdr & 0x1F else order[1] + 32
    count = hdr & 0x0F if hdr & 0x0F else order[1] + 16
    return 2 * count if code == 0x0E else count


def check_rle() -> None:
    rng = random.Random(20261122)
    agreed = failed = 0
    for trial in range(1500):
        px = rng.choice((2, 3))
        width = rng.choice((1, 3, 8, 16, 37, 64))
        orders = [random_order(rng, px) for _ in range(rng.randrange(1, 60))]
        total = sum(map(order_pixels, orders))
        # Most bitmaps fit their stream, some with room to spare; a few are
        # too small or cut short, which both decoders must refuse alike.
        height = max(1, -(-total // width)) + rng.randrange(0, 3)
        if rng.random() < .1:
            height = max(1, height // 2)
        stream = b"".join(orders)
        if rng.random() < .05:
            stream = stream[:rng.randrange(len(stream) + 1)]
        expected = naive_rle(stream, width, height, px)
        try:
            actual = bytes(rdp.rle_decode(stream, width, height, px))
        except rdp.RdpError:
            actual = None
        assert actual == expected, (trial, px, width, height, stream.hex())
        if expected is None:
            failed += 1
        else:
            agreed += 1
    assert agreed > 1000 and failed > 50, (agreed, failed)

    # A row of colour, then a background run copying it: a whole tile in
    # two orders, the way Windows sends a flat area.
    tile = bytes((0x60 | 4,)) + b"\x10\x20\x30" + bytes((4 * 3,))
    assert bytes(rdp.rle_decode(tile, 4, 4, 3)) == b"\x10\x20\x30" * 16
    # Two background runs in a row insert one foreground pixel (white).
    tile = bytes((0x60 | 2,)) + b"\x01\x02\x03" + bytes((2,)) + bytes((2,))
    out = bytes(rdp.rle_decode(tile, 2, 3, 3))
    assert out[6:12] == b"\x01\x02\x03" * 2
    assert out[12:15] == bytes(x ^ 0xFF for x in b"\x01\x02\x03")
    for bad in (b"\xa0", b"\xf5\x00\x00", b"\x61", b"\x82\x01\x02"):
        try:
            rdp.rle_decode(bad, 4, 4, 3)
        except rdp.RdpError:
            pass
        else:
            raise AssertionError("accepted RLE {}".format(bad.hex()))
    print("  RLE decoder ok ({} streams agreed, {} refused alike)".format(agreed, failed))


def pack565(r, g, b) -> int:
    return (r >> 3) << 11 | (g >> 2) << 5 | b >> 3


def pack555(r, g, b) -> int:
    return (r >> 3) << 10 | (g >> 3) << 5 | b >> 3


def seen565(r, g, b) -> tuple:
    r5, g6, b5 = r >> 3, g >> 2, b >> 3
    return (r5 << 3 | r5 >> 2, g6 << 2 | g6 >> 4, b5 << 3 | b5 >> 2)


def seen555(r, g, b) -> tuple:
    r5, g5, b5 = r >> 3, g >> 3, b >> 3
    return (r5 << 3 | r5 >> 2, g5 << 3 | g5 >> 2, b5 << 3 | b5 >> 2)


def wire_pixels(colours, bpp) -> bytes:
    if bpp == 24:
        return b"".join(bytes((b, g, r)) for r, g, b in colours)
    pack = pack565 if bpp == 16 else pack555
    return b"".join(struct.pack("<H", pack(*colour)) for colour in colours)


def seen(colour, bpp) -> tuple:
    return colour if bpp == 24 else (seen565 if bpp == 16 else seen555)(*colour)


def check_pixels() -> None:
    colours = [(0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0), (0, 0, 255),
               (18, 52, 86), (200, 100, 50), (7, 130, 251)]
    for bpp in (24, 16, 15):
        out = bytes(rdp.to_rgba(wire_pixels(colours, bpp), bpp))
        assert out == b"".join(bytes(seen(c, bpp)) + b"\xff" for c in colours), bpp
    # Uncompressed rows are bottom-up and padded to four bytes; the visible
    # part is cut from the top-left.
    rows = [[(1, 2, 3), (4, 5, 6), (7, 8, 9)], [(10, 11, 12), (13, 14, 15), (16, 17, 18)]]
    raw = b"".join(wire_pixels(row, 24) + b"\x00\x00\x00" for row in reversed(rows))
    out = bytes(rdp.decode_bitmap(raw, 3, 2, 24, False, 2, 2))
    assert out == b"".join(bytes(c) + b"\xff" for c in
                           [rows[0][0], rows[0][1], rows[1][0], rows[1][1]]), out
    unpadded = b"".join(wire_pixels(row, 24) for row in reversed(rows))
    assert bytes(rdp.decode_bitmap(unpadded, 3, 2, 24, False, 3, 1)) == \
        b"".join(bytes(c) + b"\xff" for c in rows[0])
    try:
        rdp.decode_bitmap(b"\x00" * 64, 4, 4, 32, False, 4, 4)
    except rdp.RdpError as exc:
        assert "32-bit" in str(exc)
    else:
        raise AssertionError("32-bit bitmaps were accepted")
    print("  pixel conversion ok")


def check_wire_helpers() -> None:
    request = rdp.connection_request(11, "alice")
    assert request[:2] == b"\x03\x00" and b"Cookie: mstshash=alice\r\n" in request
    assert struct.unpack_from("<BBHI", request, len(request) - 8) == (1, 0, 8, 11)
    assert rdp.connection_request(1, "bad\r\nname") .count(b"\r\n") == 1
    confirm = rdp._tpkt(bytes((14, 0xD0, 0, 0, 0, 0, 0)) + struct.pack("<BBHI", 2, 0, 8, 2))
    assert rdp.parse_connection_confirm(confirm) == 2
    failure = rdp._tpkt(bytes((14, 0xD0, 0, 0, 0, 0, 0)) + struct.pack("<BBHI", 3, 0, 8, 5))
    try:
        rdp.parse_connection_confirm(failure)
    except rdp.RdpError as exc:
        assert "Network Level Authentication" in str(exc), exc
    else:
        raise AssertionError("a negotiation failure was accepted")
    legacy = rdp._tpkt(bytes((6, 0xD0, 0, 0, 0, 0, 0)))
    try:
        rdp.parse_connection_confirm(legacy)
    except rdp.RdpError as exc:
        assert "legacy RDP security" in str(exc), exc
    else:
        raise AssertionError("legacy RDP security was accepted")
    data = rdp.client_data(1280, 800, "studio", 8)
    assert len(rdp.client_core_data(1280, 800, "studio", 8)) == 216
    initial = rdp.connect_initial(data)
    payload = rdp._x224_payload(initial)
    tag, body, _ = rdp._ber_read(payload, 0)
    assert tag == 0x7F65
    # The spec's own capture: the GCC preamble and the core block's head.
    assert b"\x00\x05\x00\x14\x7c\x00\x01" in body and b"\x00\x08\x00\x10\x00\x01\xc0\x00Duca" in body
    assert b"\x01\xc0\xd8\x00\x04\x00\x08\x00\x00\x05\x20\x03\x01\xca\x03\xaa\x09\x04\x00\x00" in body
    sets = rdp.client_capabilities(1024, 768, {26: b"\x00\x00\x04\x00"})
    lengths = {kind: len(value) + 4 for kind, value in sets}
    assert lengths[1] == 24 and lengths[2] == 28 and lengths[3] == 88 and lengths[13] == 88
    assert lengths[19] == 40 and lengths[16] == 52 and lengths[26] == 8
    pdu = rdp.confirm_active(0x103EA, 1007, sets)
    total, kind, source = struct.unpack_from("<HHH", pdu)
    assert (total, kind, source) == (len(pdu), 0x13, 1007)
    share_id, originator, source_len, combined = struct.unpack_from("<IHHH", pdu, 6)
    assert originator == 0x03EA and source_len == 6 and pdu[16:22] == b"MSTSC\x00"
    assert combined == len(pdu) - 22
    events = [("scan", 0x1E, 0), ("unicode", 0x20AC, 1), ("mouse", 0x8000 | 0x1000, 3, 4),
              ("sync", 0)]
    fast = rdp.fast_input(events)
    assert fast[0] == 4 << 2 and fast[1] == len(fast)
    assert fast[2:] == bytes((0, 0x1E)) + bytes((0x81,)) + b"\xac\x20" + \
        bytes((0x20,)) + struct.pack("<HHH", 0x9000, 3, 4) + bytes((0x60,))
    long_fast = rdp.fast_input([("scan", 0x1E, 0)] * 75)
    assert long_fast[0] == 0 and long_fast[1] & 0x80 and long_fast[3] == 75, long_fast[:4]
    assert ((long_fast[1] & 0x7F) << 8 | long_fast[2]) == len(long_fast) == 4 + 150
    assert fast_events(long_fast) == [("scan", 0x1E, 0)] * 75
    print("  wire helpers ok")


# --------------------------------------------------------------------------
# the stub server
# --------------------------------------------------------------------------

class ServerWire(rdp._Wire):
    async def accept_tls(self, context) -> None:
        self.incoming = ssl.MemoryBIO()
        self.outgoing = ssl.MemoryBIO()
        tls = context.wrap_bio(self.incoming, self.outgoing, server_side=True)
        while True:
            try:
                tls.do_handshake()
                break
            except ssl.SSLWantReadError:
                self._flush()
                self.incoming.write(await self._raw())
        self.tls = tls
        self._flush()


def server_ntlm(exported: bytes, flags: int) -> rdp.Ntlm:
    """A sealing context with the client's keys the other way round."""
    context = rdp.Ntlm("", "", "")
    context.flags = flags
    context.exported_key = exported
    context._derive_keys()
    context.send_sign, context.recv_sign = context.recv_sign, context.send_sign
    context.send_seal, context.recv_seal = context.recv_seal, context.send_seal
    return context


def rle_encode(rows: list, px: int) -> bytes:
    """A plain encoder for test tiles: colour runs, images, row copies."""
    width = len(rows[0])
    bottom_up = [b"".join(pixel for pixel in row) for row in reversed(rows)]
    flat = b"".join(bottom_up)
    out = bytearray()
    pos = 0
    total = len(flat) // px
    previous_bg = False
    while pos < total:
        line = pos // width
        if line and not previous_bg:
            run = 0
            while pos + run < total and \
                    flat[(pos + run) * px:(pos + run + 1) * px] == \
                    flat[(pos + run - width) * px:(pos + run - width + 1) * px] and run < 280:
                run += 1
            if run >= 2:
                out += bytes((run,)) if run < 32 else bytes((0, run - 32))
                pos += run
                previous_bg = True
                continue
        colour = flat[pos * px:(pos + 1) * px]
        run = 1
        while pos + run < total and flat[(pos + run) * px:(pos + run + 1) * px] == colour \
                and run < 280:
            run += 1
        if run >= 3:
            out += (bytes((0x60 | run,)) if run < 32 else bytes((0x60, run - 32))) + colour
            pos += run
        else:
            count = min(total - pos, 31)
            out += bytes((0x80 | count,)) + flat[pos * px:(pos + count) * px]
            pos += count
        previous_bg = False
    return bytes(out)


def bitmap_data(left, top, rows, bpp, compressed, headerless=False) -> bytes:
    height, width = len(rows), len(rows[0])
    px = 3 if bpp == 24 else 2
    encoded_rows = [[wire_pixels([colour], bpp) for colour in row] for row in rows]
    if compressed:
        body = rle_encode(encoded_rows, px)
        flags = rdp.BITMAP_COMPRESSION
        if headerless:
            flags |= rdp.NO_BITMAP_COMPRESSION_HDR
            header = b""
        else:
            header = struct.pack("<HHHH", 0, len(body), width * px, width * height * px)
    else:
        stride = (width * px + 3) & ~3
        body = b"".join(b"".join(row).ljust(stride, b"\x00") for row in reversed(encoded_rows))
        flags = 0
        header = b""
    return struct.pack("<9H", left, top, left + width - 1, top + height - 1, width, height,
                       bpp, flags, len(header) + len(body)) + header + body


def bitmap_update(*rects) -> bytes:
    return struct.pack("<HH", rdp.UPDATETYPE_BITMAP, len(rects)) + b"".join(rects)


class StubRdpServer:
    """A scripted Remote Desktop server: the real sequence, canned screens."""

    def __init__(self, cert, key, *, nla=True, early=True, width=640, height=400,
                 password="s3cret pass", fastpath_input=True, license_request=False,
                 wrong_binding=False, early_denied=False, credssp_version=6,
                 legacy=False, skip_licensing=False):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert, key)
        self.cert_der = ssl.PEM_cert_to_DER_cert(Path(cert).read_text())
        self.nla, self.early = nla, early
        self.width, self.height = width, height
        self.password = password
        self.fastpath_input = fastpath_input
        self.license_request = license_request
        self.wrong_binding = wrong_binding
        self.early_denied = early_denied
        self.credssp_version = credssp_version
        self.legacy = legacy
        self.skip_licensing = skip_licensing
        self.requested = None
        self.cookie = b""
        self.selected = None
        self.credentials = None
        self.info = None
        self.core = None
        self.confirm_caps = None
        self.joined = []
        self.events = []
        self.share = []
        self.connections = 0
        self.disconnected = False
        self.wire = None
        self.ready = asyncio.Event()
        self.share_id = 0x103EA
        self.sequence = []
        self.failures = []

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()
        if self.wire is not None:
            self.wire.writer.close()
        assert not self.failures, self.failures

    # ---- framing ----

    def sdi(self, data: bytes, channel: int = 1003) -> bytes:
        return rdp._x224(struct.pack(">BHHBH", 0x68, 1, channel, 0x70, 0x8000 | len(data)) + data)

    def share_control(self, kind: int, body: bytes) -> bytes:
        return struct.pack("<HHH", len(body) + 6, kind | 0x10, 1002) + body

    def share_data(self, kind: int, payload: bytes) -> bytes:
        return self.share_control(rdp.PDUTYPE_DATA, struct.pack(
            "<IBBHBBH", self.share_id, 0, 1, len(payload), kind, 0, 0) + payload)

    async def send(self, data: bytes) -> None:
        self.wire.send(data)
        await self.wire.writer.drain()

    async def read_ts(self):
        wire = self.wire
        await wire.need(2)
        length = wire.buf[wire.pos + 1]
        if length & 0x80:
            count = length & 0x7F
            await wire.need(2 + count)
            total = 2 + count + int.from_bytes(wire.buf[wire.pos + 2:wire.pos + 2 + count], "big")
        else:
            total = 2 + length
        return rdp.parse_ts_request(await wire.read(total))

    # ---- the sequence ----

    async def _handle(self, reader, writer):
        self.connections += 1
        self.disconnected = False
        self.wire = wire = ServerWire(reader, writer)
        try:
            request = await wire.read_pdu()
            self.requested = struct.unpack_from("<I", request, len(request) - 4)[0]
            at = request.find(b"Cookie: mstshash=")
            self.cookie = request[at + 17:request.find(b"\r\n", at)] if at >= 0 else b""
            if self.legacy:
                await self.send(rdp._tpkt(bytes((6, 0xD0, 0, 0, 0, 0, 0))))
                return
            if self.nla and not self.requested & rdp.PROTOCOL_HYBRID:
                await self.send(rdp._tpkt(bytes((14, 0xD0, 0, 0, 0, 0, 0)) +
                                          struct.pack("<BBHI", 3, 0, 8, 5)))
                return
            if self.nla:
                self.selected = rdp.PROTOCOL_HYBRID_EX if self.early and \
                    self.requested & rdp.PROTOCOL_HYBRID_EX else rdp.PROTOCOL_HYBRID
            else:
                self.selected = rdp.PROTOCOL_SSL
            await self.send(rdp._tpkt(bytes((14, 0xD0, 0, 0, 0, 0, 0)) +
                                      struct.pack("<BBHI", 2, 0, 8, self.selected)))
            await wire.accept_tls(self.context)
            if self.selected != rdp.PROTOCOL_SSL and not await self._credssp():
                return
            await self._mcs()
            if not await self._licensing():
                return
            await self.send(self.sdi(self.demand_active(self.width, self.height)))
            await self._serve()
        except (rdp.RdpError, ConnectionError, ssl.SSLError):
            pass
        except AssertionError as exc:
            self.failures.append(repr(exc))
        finally:
            self.disconnected = True
            writer.close()

    async def _credssp(self) -> bool:
        spk = rdp.subject_public_key(self.cert_der)
        first = await self.read_ts()
        negotiate = first["nego"]
        assert negotiate[:12] == b"NTLMSSP\x00\x01\x00\x00\x00", negotiate[:12]
        flags = 0xE2898235
        target = rdp._av_blob([(rdp.MSV_AV_NB_DOMAIN, "STUBNET".encode("utf-16-le")),
                               (rdp.MSV_AV_NB_COMPUTER, "STUB".encode("utf-16-le")),
                               (rdp.MSV_AV_DNS_COMPUTER, "stub.example".encode("utf-16-le")),
                               (rdp.MSV_AV_TIMESTAMP, rdp._filetime())])
        name = "STUBNET".encode("utf-16-le")
        server_challenge = os.urandom(8)
        challenge = (b"NTLMSSP\x00" + struct.pack("<I", 2) +
                     struct.pack("<HHI", len(name), len(name), 56) + struct.pack("<I", flags) +
                     server_challenge + b"\x00" * 8 +
                     struct.pack("<HHI", len(target), len(target), 56 + len(name)) +
                     struct.pack("<BBHBBBB", 10, 0, 20348, 0, 0, 0, 15) + name + target)
        self.wire.send(rdp.ts_request(self.credssp_version, nego=challenge))
        second = await self.read_ts()
        auth, nonce = second["nego"], second["nonce"]
        assert len(nonce) == 32 and nonce == first["nonce"]

        def field(offset):
            length, _max, pos = struct.unpack_from("<HHI", auth, offset)
            return auth[pos:pos + length]
        nt, domain = field(20), field(28).decode("utf-16-le")
        user, encrypted = field(36).decode("utf-16-le"), field(52)
        key = rdp.ntowfv2(self.password, user, domain)
        proof = hmac.new(key, server_challenge + nt[16:], hashlib.md5).digest()
        if user.lower() != "alice" or not hmac.compare_digest(proof, nt[:16]):
            await self.send(rdp._der(0x30, rdp._der(0xA0, rdp._der_integer(
                self.credssp_version)) + rdp._der(0xA4, rdp._der_integer(
                    0xC000006D - (1 << 32)))))
            return False
        base = hmac.new(key, proof, hashlib.md5).digest()
        exported = rdp.Rc4(base).update(encrypted)
        zeroed = auth[:72] + b"\x00" * 16 + auth[88:]
        assert auth[72:88] == hmac.new(exported, negotiate + challenge + zeroed,
                                       hashlib.md5).digest(), "the MIC must verify"
        context = server_ntlm(exported, flags)
        binding = context.unseal(second["pub_key_auth"])
        if self.credssp_version >= 5:
            assert binding == hashlib.sha256(rdp._CLIENT_BINDING + nonce + spk).digest()
            answer = hashlib.sha256(rdp._SERVER_BINDING + nonce + spk).digest()
        else:
            assert binding == spk
            answer = rdp._increment_le(spk)
        if self.wrong_binding:
            answer = bytes((answer[0] ^ 1,)) + answer[1:]
        self.wire.send(rdp.ts_request(self.credssp_version, pub_key_auth=context.seal(answer)))
        third = await self.read_ts()
        creds = rdp._der_items(context.unseal(third["auth_info"]))[0][1]
        fields = dict(rdp._der_items(creds))
        assert rdp._der_items(fields[0xA0])[0][1] == b"\x01", "password credentials"
        inner = dict(rdp._der_items(rdp._der_items(rdp._der_items(fields[0xA1])[0][1])[0][1]))
        self.credentials = tuple(rdp._der_items(inner[tag])[0][1].decode("utf-16-le")
                                 for tag in (0xA0, 0xA1, 0xA2))
        if self.selected == rdp.PROTOCOL_HYBRID_EX:
            await self.send(struct.pack("<I", 5 if self.early_denied else 0))
            if self.early_denied:
                return False
        return True

    async def _mcs(self) -> None:
        wire = self.wire
        payload = rdp._x224_payload(await wire.read_pdu())
        tag, body, _ = rdp._ber_read(payload, 0)
        assert tag == 0x7F65
        pos = 0
        for _ in range(6):
            _tag, _value, pos = rdp._ber_read(body, pos)
        _tag, gcc, _ = rdp._ber_read(body, pos)
        at = gcc.find(b"Duca") + 4
        length = gcc[at]
        if length & 0x80:
            length = (length & 0x7F) << 8 | gcc[at + 1]
            at += 2
        else:
            at += 1
        blocks = gcc[at:at + length]
        kind, size = struct.unpack_from("<HH", blocks)
        assert kind == 0xC001 and size == 216
        self.core = {"width": struct.unpack_from("<H", blocks, 8)[0],
                     "height": struct.unpack_from("<H", blocks, 10)[0],
                     "selected": struct.unpack_from("<I", blocks, 212)[0],
                     "name": blocks[24:56].decode("utf-16-le").rstrip("\x00")}
        server = (struct.pack("<HHIII", 0x0C01, 16, 0x00080004, self.requested, 0) +
                  struct.pack("<HHII", 0x0C02, 12, 0, 0) +
                  struct.pack("<HHHH", 0x0C03, 8, 1003, 0))
        rest = b"\x14\x76\x0a\x01\x01\x00\x01\xc0\x00McDn" + rdp._per_length(len(server)) + server
        conference = b"\x00\x05\x00\x14\x7c\x00\x01" + rdp._per_length(len(rest)) + rest
        response = rdp._ber(b"\x7f\x66", rdp._ber(b"\x0a", b"\x00") + rdp._ber(b"\x02", b"\x00") +
                            rdp._domain_parameters(rdp._DOMAIN_TARGET) +
                            rdp._ber(b"\x04", conference))
        await self.send(rdp._x224(response))
        assert rdp._x224_payload(await wire.read_pdu())[:1] == b"\x04"        # erect domain
        assert rdp._x224_payload(await wire.read_pdu()) == b"\x28"             # attach user
        await self.send(rdp._x224(b"\x2e\x00\x00\x06"))
        for _ in range(2):
            join = rdp._x224_payload(await wire.read_pdu())
            assert join[0] == 0x38 and join[1:3] == b"\x00\x06"
            channel = join[3] << 8 | join[4]
            self.joined.append(channel)
            await self.send(rdp._x224(b"\x3e\x00\x00\x06" + join[3:5] + join[3:5]))
        kind, channel, info = rdp._mcs(rdp._x224_payload(await wire.read_pdu()))
        assert kind == 25 and channel == 1003
        flags, = struct.unpack_from("<H", info)
        assert flags & rdp.SEC_INFO_PKT
        _code_page, info_flags, cb_domain, cb_user, cb_password = struct.unpack_from("<IIHHH", info, 4)
        pos = 18 + 4
        domain = info[pos:pos + cb_domain].decode("utf-16-le")
        pos += cb_domain + 2
        user = info[pos:pos + cb_user].decode("utf-16-le")
        pos += cb_user + 2
        password = info[pos:pos + cb_password].decode("utf-16-le")
        self.info = {"flags": info_flags, "domain": domain, "user": user, "password": password}

    def demand_active(self, width, height) -> bytes:
        general = struct.pack("<HHHHHHHHHBB", 1, 3, 0x200, 0, 0, 0x041D, 0, 0, 0, 1, 1)
        bitmap = struct.pack("<HHHHHHHHHBBHH", 24, 1, 1, 1, width, height, 0, 1, 1, 0, 0, 1, 0)
        flags = rdp.INPUT_FLAG_SCANCODES | rdp.INPUT_FLAG_UNICODE | rdp.TS_INPUT_FLAG_MOUSE_HWHEEL
        if self.fastpath_input:
            flags |= rdp.INPUT_FLAG_FASTPATH_INPUT2
        caps = [(1, general), (2, bitmap), (13, struct.pack("<HHIIII", flags, 0, 0x409, 4, 0, 12) +
                                           b"\x00" * 64), (26, struct.pack("<I", 0x40000))]
        body = rdp._caps(*caps)
        return self.share_control(rdp.PDUTYPE_DEMANDACTIVE, struct.pack(
            "<IHH", self.share_id, 4, len(body) + 4) + b"RDP\x00" +
            struct.pack("<HH", len(caps), 0) + body + struct.pack("<I", 0))

    async def _licensing(self) -> bool:
        if self.license_request:
            licence = struct.pack("<BBH", rdp.LICENSE_REQUEST, 3, 36) + b"\x00" * 32
            await self.send(self.sdi(struct.pack("<HH", rdp.SEC_LICENSE_PKT, 0) + licence))
            try:
                await self.wire.read_pdu()
            except rdp.RdpError:
                pass
            return False
        if not self.skip_licensing:
            alert = struct.pack("<BBHIIHH", rdp.LICENSE_ERROR_ALERT, 3, 16,
                                rdp.STATUS_VALID_CLIENT, 2, 4, 0)
            await self.send(self.sdi(struct.pack("<HH", rdp.SEC_LICENSE_PKT, 0) + alert))
        return True

    async def _serve(self) -> None:
        """Every client PDU after the Demand Active, activation or not."""
        while True:
            pdu = await self.wire.read_pdu()
            if pdu[0] & 3 != 3:
                self.events.extend(fast_events(pdu))
                continue
            kind, _channel, data = rdp._mcs(rdp._x224_payload(pdu))
            if kind == 8:
                self.share.append(("ultimatum", b""))
                return
            control = struct.unpack_from("<H", data, 2)[0] & 0x0F
            if control == rdp.PDUTYPE_CONFIRMACTIVE:
                share_id, caps = rdp.parse_demand_active(data[6:10] + data[12:])
                assert share_id == self.share_id
                self.confirm_caps = caps
                self.sequence.append("confirm")
                continue
            kind2 = data[6 + 8]
            payload = data[18:]
            if kind2 == rdp.PDUTYPE2_INPUT:
                self.events.extend(slow_events(payload))
                continue
            self.share.append((kind2, payload))
            if kind2 in (rdp.PDUTYPE2_SYNCHRONIZE, rdp.PDUTYPE2_CONTROL,
                         rdp.PDUTYPE2_FONTLIST):
                self.sequence.append(kind2)
            if kind2 == rdp.PDUTYPE2_FONTLIST:
                assert self.sequence[-5:] == ["confirm", rdp.PDUTYPE2_SYNCHRONIZE,
                                              rdp.PDUTYPE2_CONTROL, rdp.PDUTYPE2_CONTROL,
                                              rdp.PDUTYPE2_FONTLIST], self.sequence
                for reply, body in ((rdp.PDUTYPE2_SYNCHRONIZE, struct.pack("<HH", 1, 1007)),
                                    (rdp.PDUTYPE2_CONTROL, struct.pack("<HHI", 4, 0, 0)),
                                    (rdp.PDUTYPE2_CONTROL, struct.pack("<HHI", 2, 1007, 0x3EA)),
                                    (0x28, struct.pack("<HHHH", 0, 0, 3, 4))):
                    await self.send(self.sdi(self.share_data(reply, body)))
                self.ready.set()

    # ---- scripted output ----

    async def fast_update(self, data: bytes, code=rdp.FASTPATH_UPDATETYPE_BITMAP,
                          pieces=1) -> None:
        size = len(data)
        chunks = [data[size * i // pieces:size * (i + 1) // pieces] for i in range(pieces)]
        updates = b""
        for index, chunk in enumerate(chunks):
            fragment = rdp.FASTPATH_FRAGMENT_SINGLE if pieces == 1 else (
                rdp.FASTPATH_FRAGMENT_FIRST if index == 0 else
                rdp.FASTPATH_FRAGMENT_LAST if index == pieces - 1 else
                rdp.FASTPATH_FRAGMENT_NEXT)
            update = bytes((code | fragment << 4,)) + struct.pack("<H", len(chunk)) + chunk
            total = len(update) + 3
            updates = update
            await self.send(bytes((0,)) + struct.pack(">H", 0x8000 | total) + updates)

    async def slow_update(self, data: bytes) -> None:
        await self.send(self.sdi(self.share_data(rdp.PDUTYPE2_UPDATE, data)))

    async def reactivate(self, width, height) -> None:
        self.ready.clear()
        await self.send(self.sdi(self.share_control(rdp.PDUTYPE_DEACTIVATEALL, struct.pack(
            "<IH", self.share_id, 1) + b"\x00")))
        await self.send(self.sdi(self.demand_active(width, height)))
        await asyncio.wait_for(self.ready.wait(), 5)

    async def end(self, error_info: int) -> None:
        await self.send(self.sdi(self.share_data(rdp.PDUTYPE2_SET_ERROR_INFO,
                                                 struct.pack("<I", error_info))))
        await self.send(rdp._x224(b"\x20\x80"))

    def kinds(self, kind2: int) -> list:
        return [payload for kind, payload in self.share if kind == kind2]


def fast_events(pdu: bytes) -> list:
    count = pdu[0] >> 2 & 0x0F
    pos = 3 if pdu[1] & 0x80 else 2
    if not count:
        count = pdu[pos]
        pos += 1
    events = []
    for _ in range(count):
        header = pdu[pos]
        code, flags = header >> 5, header & 0x1F
        if code == 0:
            events.append(("scan", pdu[pos + 1], flags))
            pos += 2
        elif code == 1:
            events.append(("mouse",) + struct.unpack_from("<HHH", pdu, pos + 1))
            pos += 7
        elif code == 3:
            events.append(("sync", flags))
            pos += 1
        elif code == 4:
            events.append(("unicode", struct.unpack_from("<H", pdu, pos + 1)[0], flags))
            pos += 3
        else:
            raise AssertionError("unknown fast-path event {}".format(code))
    return events


def slow_events(payload: bytes) -> list:
    count, = struct.unpack_from("<H", payload)
    events = []
    for index in range(count):
        _time, kind = struct.unpack_from("<IH", payload, 4 + index * 12)
        a, b, c = struct.unpack_from("<HHH", payload, 10 + index * 12)
        if kind == 0x0004:
            events.append(("scan", b, (1 if a & 0x8000 else 0) | (2 if a & 0x0100 else 0) |
                           (4 if a & 0x0200 else 0)))
        elif kind == 0x0005:
            events.append(("unicode", b, 1 if a & 0x8000 else 0))
        elif kind == 0x8001:
            events.append(("mouse", a, b, c))
        else:
            events.append(("sync", struct.unpack_from("<I", payload, 10 + index * 12 + 2)[0]))
    return events


def pixel_at(instance, x, y) -> tuple:
    offset = (y * instance.width + x) * 4
    return tuple(instance.frame[offset:offset + 3])


async def dial(server, **kwargs) -> rdp.RdpInstance:
    instance = rdp.RdpInstance("TEST", "127.0.0.1", server.port, kwargs.pop("password", "s3cret pass"),
                               username=kwargs.pop("username", "STUBNET\\alice"), **kwargs)
    await instance.ensure_started()
    await asyncio.wait_for(server.ready.wait(), 5)
    return instance


# --------------------------------------------------------------------------
# connection checks
# --------------------------------------------------------------------------

async def check_sign_in(cert, key) -> None:
    server = await StubRdpServer(cert, key).start()
    try:
        instance = await dial(server, width=1024, height=768)
        assert server.requested == rdp.PROTOCOL_SSL | rdp.PROTOCOL_HYBRID | rdp.PROTOCOL_HYBRID_EX
        assert server.cookie == b"alice"
        assert server.credentials == ("STUBNET", "alice", "s3cret pass"), server.credentials
        assert server.core["width"] == 1024 and server.core["height"] == 768, server.core
        assert server.core["selected"] == rdp.PROTOCOL_HYBRID_EX
        assert server.joined == [1007, 1003]
        assert server.info["user"] == "alice" and server.info["domain"] == "STUBNET"
        assert server.info["flags"] & rdp.INFO_AUTOLOGON
        # The server's own desktop size wins over the one asked for.
        assert (instance.width, instance.height) == (640, 400)
        bitmap = server.confirm_caps[2]
        assert struct.unpack_from("<HH", bitmap, 8) == (640, 400)
        assert struct.unpack_from("<H", bitmap)[0] == 24
        assert server.confirm_caps[3][20 + 12:20 + 12 + 32] == b"\x00" * 32, \
            "no drawing orders are advertised"
        assert struct.unpack_from("<I", server.confirm_caps[26])[0] == rdp.MAX_REQUEST_SIZE
        payload = instance.payload()
        assert payload["protocol"] == "rdp" and payload["security"] == "NLA"
        assert payload["username"] == "STUBNET\\alice"
        assert payload["certificate"] == rdp.fingerprint(server.cert_der)
        assert payload["server"] == "Windows 10.0 build 20348", payload["server"]
        assert payload["name"] == "stub.example", payload["name"]
        assert "s3cret" not in json.dumps(payload), "the password never leaves the node"
        await wait_for(lambda: ("sync", 0) in server.events, label="lock keys synced")
        assert server.sequence == ["confirm", rdp.PDUTYPE2_SYNCHRONIZE, rdp.PDUTYPE2_CONTROL,
                                   rdp.PDUTYPE2_CONTROL, rdp.PDUTYPE2_FONTLIST], server.sequence
        controls = server.kinds(rdp.PDUTYPE2_CONTROL)
        assert [struct.unpack_from("<H", body)[0] for body in controls] == [4, 1], controls
        await instance.close("done")
        await wait_for(lambda: server.disconnected, label="server saw the disconnect")
        assert ("ultimatum", b"") in server.share, "a closed connection says goodbye"
    finally:
        await server.stop()

    # The CredSSP versions before 5 bind the key by echo instead of hash.
    server = await StubRdpServer(cert, key, credssp_version=3, early=False).start()
    try:
        instance = await dial(server)
        assert server.selected == rdp.PROTOCOL_HYBRID and instance.security == "NLA"
        await instance.close("done")
    finally:
        await server.stop()

    # Refusals, each in words a person can act on.
    for options, kwargs, words in (
            ({}, {"password": "wrong"}, "rejected the user name or password"),
            ({}, {"username": "", "password": ""}, "requires Network Level Authentication"),
            ({"wrong_binding": True}, {}, "could not prove it owns the TLS certificate"),
            ({"early_denied": True}, {}, "not allowed to sign in over Remote Desktop"),
            ({"license_request": True}, {}, "issues Remote Desktop client licenses"),
            ({"legacy": True}, {}, "legacy RDP security")):
        server = await StubRdpServer(cert, key, **options).start()
        try:
            instance = rdp.RdpInstance("TEST", "127.0.0.1", server.port,
                                       kwargs.get("password", "s3cret pass"),
                                       username=kwargs.get("username", "alice"))
            try:
                await instance.ensure_started()
            except vnc.VncError as exc:
                assert words in str(exc), (words, str(exc))
                assert instance.error == str(exc)
            else:
                raise AssertionError("{} was not refused".format(options or kwargs))
            assert not instance.connected
            if options.get("wrong_binding"):
                assert server.credentials is None, "the password was sent anyway"
        finally:
            await server.stop()

    # TLS without NLA: credentials ride the client info for the server's own
    # sign-in, and no CredSSP happens at all.
    server = await StubRdpServer(cert, key, nla=False).start()
    try:
        instance = await dial(server, username="bob", password="pw")
        assert server.selected == rdp.PROTOCOL_SSL and server.credentials is None
        assert server.info["user"] == "bob" and server.info["password"] == "pw"
        assert instance.security == "TLS" and instance.name == "stub-desktop"
        await instance.close("done")
    finally:
        await server.stop()

    # Something that is not a Remote Desktop server at all.
    async def vnc_like(reader, writer):
        writer.write(b"RFB 003.008\n")
        await writer.drain()
        await reader.read(100)
        writer.close()
    other = await asyncio.start_server(vnc_like, "127.0.0.1", 0)
    try:
        instance = rdp.RdpInstance("TEST", "127.0.0.1",
                                   other.sockets[0].getsockname()[1], "", username="")
        try:
            await instance.ensure_started()
        except vnc.VncError as exc:
            assert "is not a Remote Desktop server" in str(exc), exc
        else:
            raise AssertionError("an RFB server passed for RDP")
    finally:
        other.close()
        await other.wait_closed()
    print("  sign-in, refusals and TLS-only ok")


async def check_screen(cert, key) -> None:
    server = await StubRdpServer(cert, key, width=128, height=64).start()
    instance = None
    try:
        instance = await dial(server)
        viewer = ViewerSocket()
        await instance.attach_viewer(viewer)
        await wait_for(lambda: viewer.frames, label="first full frame")
        rng = random.Random(7)
        expected = {}
        rects = []
        for index, (bpp, compressed) in enumerate(((24, True), (24, False), (16, True),
                                                   (16, False), (15, True), (15, False))):
            palette = [(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                       for _ in range(3)]
            rows = []
            for y in range(16):
                row = []
                for x in range(16):
                    row.append(palette[0] if y < 4 else palette[1] if x < 8 else
                               palette[(x + y) % 3])
                rows.append(row)
            left, top = (index % 4) * 32, (index // 4) * 32
            rects.append(bitmap_data(left, top, rows, bpp, compressed, headerless=index == 2))
            for y in range(16):
                for x in range(16):
                    expected[(left + x, top + y)] = seen(rows[y][x], bpp)
        seq = instance.frame_seq
        await server.fast_update(bitmap_update(*rects[:2]))
        await server.fast_update(bitmap_update(*rects[2:4]), pieces=3)
        await server.slow_update(bitmap_update(*rects[4:]))
        await wait_for(lambda: all(pixel_at(instance, x, y) == colour
                                   for (x, y), colour in expected.items()),
                       label="every rectangle painted")
        assert instance.frame_seq > seq
        # A bitmap reaching past the screen is cut at its edge, not refused.
        edge = bitmap_data(120, 60, [[(9, 9, 9)] * 16] * 16, 24, False)
        await server.fast_update(bitmap_update(edge))
        await wait_for(lambda: pixel_at(instance, 127, 63) == (9, 9, 9), label="edge")
        frames = [vnc_frame(frame) for frame in viewer.frames[1:]]
        assert any(frame["w"] == 8 and frame["h"] == 4 and frame["x"] == 120
                   for frame in frames), [(f["x"], f["y"], f["w"], f["h"]) for f in frames]

        # Fewer rectangles than the limit travel as they are; more collapse
        # into one cut of their bounding box.
        many = [bitmap_data(x * 2, 40, [[(x, 1, 2)] * 2] * 2, 24, False) for x in range(60)]
        before = len(viewer.frames)
        await server.fast_update(bitmap_update(*many))
        await wait_for(lambda: pixel_at(instance, 118, 41) == (59, 1, 2), label="many")
        await wait_for(lambda: len(viewer.frames) > before, label="bounding frame")
        frame = vnc_frame(viewer.frames[-1])
        assert (frame["x"], frame["y"], frame["w"], frame["h"]) == (0, 40, 120, 2), frame

        # The server's own resize: deactivate, a new size, a size message.
        await server.reactivate(160, 90)
        await wait_for(lambda: (instance.width, instance.height) == (160, 90), label="resize")
        await wait_for(lambda: viewer.messages("size"), label="size message")
        assert viewer.messages("size")[-1] == {"type": "size", "width": 160, "height": 90}
        assert instance.payload()["width"] == 160

        # The end: Windows says why, then drops the connection.
        await server.end(0x0000000C)
        await wait_for(lambda: viewer.messages("gone"), label="gone")
        assert viewer.messages("gone")[-1]["reason"] == "the session was signed out"
        assert not instance.connected
    finally:
        if instance is not None:
            await instance.close("done")
        await server.stop()
    print("  screen updates, cuts, resize and the end ok")


def vnc_frame(frame: bytes) -> dict:
    kind, _, x, y, width, height = struct.unpack_from("<BBHHHH", frame)
    assert kind == vnc.FRAME_PIXELS and len(frame) == 10 + width * height * 4
    return {"x": x, "y": y, "w": width, "h": height, "pixels": frame[10:]}


async def check_input(cert, key) -> None:
    for fastpath in (True, False):
        server = await StubRdpServer(cert, key, width=200, height=100,
                                     fastpath_input=fastpath).start()
        instance = None
        try:
            instance = await dial(server)
            viewer = ViewerSocket()
            await instance.attach_viewer(viewer)
            server.events.clear()
            send = lambda data: instance.handle_client(data, viewer)  # noqa: E731
            await send({"type": "pointer", "nx": 0.5, "ny": 0.5, "mask": 1})
            await send({"type": "pointer", "nx": 0.5, "ny": 0.5, "mask": 0})
            await send({"type": "pointer", "nx": 1, "ny": 1, "mask": 4})
            await send({"type": "pointer", "nx": 1, "ny": 1, "mask": 6})
            await send({"type": "pointer", "nx": 1, "ny": 1, "mask": 0})
            await send({"type": "wheel", "nx": 0, "ny": 0, "dx": 0, "dy": 120})
            await send({"type": "wheel", "nx": 0, "ny": 0, "dx": -60, "dy": -67})
            await send({"type": "key", "kind": "down", "key": "Control", "code": "ControlLeft"})
            await send({"type": "key", "kind": "down", "key": "c", "code": "KeyC"})
            await send({"type": "key", "kind": "up", "key": "c", "code": "KeyC"})
            await send({"type": "key", "kind": "up", "key": "Control", "code": "ControlLeft"})
            await send({"type": "key", "kind": "down", "key": "ArrowUp", "code": "ArrowUp"})
            await send({"type": "key", "kind": "up", "key": "ArrowUp", "code": "ArrowUp"})
            await send({"type": "text", "text": "hé€\n😀"})
            await send({"type": "cad"})
            await send({"type": "key", "kind": "down", "key": "Shift", "code": "ShiftLeft"})
            await send({"type": "release_keys"})
            down = rdp.PTRFLAGS_DOWN
            expected = [
                ("mouse", rdp.PTRFLAGS_MOVE, 100, 50),
                ("mouse", rdp.PTRFLAGS_BUTTON1 | down, 100, 50),
                ("mouse", rdp.PTRFLAGS_BUTTON1, 100, 50),
                ("mouse", rdp.PTRFLAGS_MOVE, 199, 99),
                ("mouse", rdp.PTRFLAGS_BUTTON2 | down, 199, 99),     # RFB bit 4: right
                ("mouse", rdp.PTRFLAGS_BUTTON3 | down, 199, 99),     # RFB bit 2: middle
                ("mouse", rdp.PTRFLAGS_BUTTON3, 199, 99),
                ("mouse", rdp.PTRFLAGS_BUTTON2, 199, 99),
                ("mouse", rdp.PTRFLAGS_MOVE, 0, 0),
                ("mouse", 0x0388, 0, 0), ("mouse", 0x0388, 0, 0),     # 120: two notches down
                # the 14 left over and 67 up make one notch up; 60 left is one
                # notch on the horizontal wheel the stub advertises
                ("mouse", 0x0278, 0, 0), ("mouse", 0x0588, 0, 0),
            ]
            expected += [
                ("scan", 0x1D, 0), ("scan", 0x2E, 0), ("scan", 0x2E, 1), ("scan", 0x1D, 1),
                ("scan", 0x48, 2), ("scan", 0x48, 3),
                ("unicode", ord("h"), 0), ("unicode", ord("h"), 1),
                ("unicode", 0xE9, 0), ("unicode", 0xE9, 1),
                ("unicode", 0x20AC, 0), ("unicode", 0x20AC, 1),
                ("scan", 0x1C, 0), ("scan", 0x1C, 1),
                ("unicode", 0xD83D, 0), ("unicode", 0xDE00, 0),
                ("unicode", 0xD83D, 1), ("unicode", 0xDE00, 1),
                ("scan", 0x1D, 0), ("scan", 0x38, 0), ("scan", 0x53, 2),
                ("scan", 0x53, 3), ("scan", 0x38, 1), ("scan", 0x1D, 1),
                ("scan", 0x2A, 0), ("scan", 0x2A, 1),
            ]
            await wait_for(lambda: len(server.events) >= len(expected), label="input")
            assert server.events == expected, (fastpath, server.events)

            # The agent's tools reach the same wire.
            server.events.clear()
            await instance.agent_click(10, 20, "left", 2)
            await instance.agent_press("Delete", ["Control", "Alt"])
            await instance.agent_press("Pause")
            await instance.agent_scroll(5, 6, "right", 1)
            await instance.agent_type("A")
            expected = [
                ("mouse", rdp.PTRFLAGS_MOVE, 10, 20),
                ("mouse", rdp.PTRFLAGS_BUTTON1 | down, 10, 20), ("mouse", rdp.PTRFLAGS_BUTTON1, 10, 20),
                ("mouse", rdp.PTRFLAGS_BUTTON1 | down, 10, 20), ("mouse", rdp.PTRFLAGS_BUTTON1, 10, 20),
                ("scan", 0x1D, 0), ("scan", 0x38, 0), ("scan", 0x53, 2), ("scan", 0x53, 3),
                ("scan", 0x38, 1), ("scan", 0x1D, 1),
                ("scan", 0x1D, 4), ("scan", 0x45, 0), ("scan", 0x1D, 5), ("scan", 0x45, 1),
                ("mouse", rdp.PTRFLAGS_MOVE, 5, 6), ("mouse", 0x0478, 5, 6),
                ("unicode", ord("A"), 0), ("unicode", ord("A"), 1),
            ]
            await wait_for(lambda: len(server.events) >= len(expected), label="agent input")
            assert server.events == expected, (fastpath, server.events)

            # View only refuses input before it is sent.
            instance.view_only = True
            server.events.clear()
            await send({"type": "pointer", "nx": 0, "ny": 0, "mask": 1})
            try:
                await instance.agent_click(1, 1)
            except vnc.VncError as exc:
                assert "RDP TEST is view only" in str(exc), exc
            else:
                raise AssertionError("a view-only screen took a click")
            await asyncio.sleep(0.1)
            assert server.events == []
        finally:
            if instance is not None:
                await instance.close("done")
            await server.stop()
    print("  input on both paths ok")


async def check_attention(cert, key) -> None:
    """Nobody watching means Suppress Output; a viewer or an agent ends it."""
    server = await StubRdpServer(cert, key, width=64, height=32).start()
    instance = None
    old = rdp.AGENT_ATTENTION
    rdp.AGENT_ATTENTION = 0.3
    try:
        instance = await dial(server)
        suppress = lambda: server.kinds(rdp.PDUTYPE2_SUPPRESS_OUTPUT)  # noqa: E731
        await wait_for(lambda: suppress(), timeout=3, label="unwatched suppress")
        assert suppress()[-1] == b"\x00\x00\x00\x00", suppress()
        assert not instance.display_on
        viewer = ViewerSocket()
        await instance.attach_viewer(viewer)
        await wait_for(lambda: len(suppress()) == 2, label="viewer wakes output")
        assert suppress()[-1] == struct.pack("<B3xHHHH", 1, 0, 0, 63, 31)
        instance.set_viewer_active(viewer, False)
        await wait_for(lambda: len(suppress()) == 3, label="hidden pane suppresses")
        # An agent's sync wakes it for its attention span, then lets it go.
        await instance.sync(timeout=0.2)
        await wait_for(lambda: len(suppress()) == 4, label="agent wakes output")
        await wait_for(lambda: len(suppress()) == 5, timeout=3, label="attention lapses")
        instance.set_viewer_active(viewer, True)
        await wait_for(lambda: len(suppress()) == 6, label="shown again")
        await instance.handle_client({"type": "refresh"}, viewer)
        await wait_for(lambda: server.kinds(rdp.PDUTYPE2_REFRESH_RECT), label="refresh")
        assert server.kinds(rdp.PDUTYPE2_REFRESH_RECT)[-1] == \
            struct.pack("<B3xHHHH", 1, 0, 0, 63, 31)
        instance.detach_viewer(viewer)
        await wait_for(lambda: len(suppress()) == 7, timeout=3, label="detached")
    finally:
        rdp.AGENT_ATTENTION = old
        if instance is not None:
            await instance.close("done")
        await server.stop()
    print("  suppress output follows who is watching ok")


async def check_agent_screenshot(cert, key) -> None:
    server = await StubRdpServer(cert, key, width=32, height=16).start()
    instance = None
    try:
        instance = await dial(server)
        await server.fast_update(bitmap_update(bitmap_data(0, 0, [[(200, 10, 20)] * 32] * 16,
                                                           24, True)))
        await wait_for(lambda: pixel_at(instance, 31, 15) == (200, 10, 20), label="painted")
        assert await instance.settle(quiet_ms=50, timeout_ms=1000)
        shot = instance.screenshot()
        assert shot["png"][:8] == b"\x89PNG\r\n\x1a\n"
        assert (shot["screen_width"], shot["screen_height"], shot["step"]) == (32, 16, 1)
    finally:
        if instance is not None:
            await instance.close("done")
        await server.stop()
    print("  agent screenshot ok")


# --------------------------------------------------------------------------
# routes, bridge, contract
# --------------------------------------------------------------------------

def check_validation() -> None:
    spec = vnc._request_spec({"protocol": "rdp", "host": "desk.example",
                              "username": " CORP\\alice ", "password": "p",
                              "width": 1600, "height": 900})
    assert spec == {"protocol": "rdp", "host": "desk.example", "port": 3389, "password": "p",
                    "view_only": False, "label": "", "username": "CORP\\alice",
                    "width": 1600, "height": 900}, spec
    assert vnc._request_spec({"protocol": "rdp", "host": "desk:2"})["port"] == 2, \
        "RDP has no display-number shorthand"
    assert vnc._request_spec({"protocol": "rdp", "host": "[fe80::1]:3390"})["port"] == 3390
    assert vnc._request_spec({"host": "desk:2"})["port"] == 5902
    assert vnc._request_spec({"host": "desk"})["protocol"] == "vnc"
    for bad in ({"protocol": "ftp", "host": "x"},
                {"protocol": "rdp", "host": "x", "width": 100},
                {"protocol": "rdp", "host": "x", "height": 9000},
                {"protocol": "rdp", "host": "x", "width": "1280"},
                {"protocol": "rdp", "host": "x", "username": "a\x00b"},
                {"protocol": "rdp", "host": "x", "password": "orphan"},
                {"host": "x", "username": "alice"},
                {"host": "x", "width": 1280}):
        try:
            vnc._request_spec(bad)
        except vnc.VncError:
            pass
        else:
            raise AssertionError("accepted {!r}".format(bad))
    print("  request validation ok")


async def check_http_surface(cert, key, app_factory=build_app) -> None:
    server = await StubRdpServer(cert, key, width=48, height=24).start()
    app = app_factory()
    web_runner = aioweb.AppRunner(app)
    await web_runner.setup()
    site = aioweb.TCPSite(web_runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = "http://127.0.0.1:{}".format(port)
    headers = {"X-Puppy-Token": config.get("auth.api_token")}
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(url + "/api/ping", headers=headers) as response:
                assert "rdp" in (await response.json())["capabilities"]
            for bad in ({"protocol": "rdp", "host": "x", "width": 50},
                        {"protocol": "nope", "host": "x"},
                        {"host": "x", "username": "u"}):
                async with http.post(url + "/api/vnc/instances", headers=headers,
                                     json=bad) as response:
                    assert response.status == 400, (bad, response.status)
            async with http.post(url + "/api/vnc/instances", headers=headers, json={
                    "protocol": "rdp", "host": "127.0.0.1", "port": server.port,
                    "username": "alice", "password": "wrong"}) as response:
                assert response.status == 502
                assert "rejected the user name or password" in (await response.json())["error"]
            async with http.post(url + "/api/vnc/instances", headers=headers, json={
                    "protocol": "rdp", "host": "127.0.0.1", "port": server.port,
                    "username": "alice", "password": "s3cret pass", "label": "Desk",
                    "request_id": "rdp-request-0123456789"}) as response:
                created = await response.json()
                assert response.status == 201, created
            entry = created["vnc"]
            assert entry["protocol"] == "rdp" and entry["security"] == "NLA"
            assert entry["width"] == 48 and entry["label"] == "Desk"
            assert "password" not in entry and "s3cret" not in json.dumps(created)
            async with http.get(url + "/api/vnc/instances", headers=headers) as response:
                listed = (await response.json())["instances"]
                assert [item["protocol"] for item in listed] == ["rdp"], listed
            socket = await http.ws_connect(url + "/api/ws/vnc/" + entry["id"], headers=headers)
            status = await asyncio.wait_for(socket.receive_json(), 5)
            assert status["type"] == "status" and status["protocol"] == "rdp", status
            frame = await asyncio.wait_for(socket.receive(), 5)
            assert frame.type is aiohttp.WSMsgType.BINARY
            assert vnc_frame(frame.data)["w"] == 48
            server.events.clear()
            await socket.send_json({"type": "pointer", "nx": 1, "ny": 0, "mask": 1})
            await wait_for(lambda: ("mouse", rdp.PTRFLAGS_BUTTON1 | rdp.PTRFLAGS_DOWN, 47, 0)
                           in server.events, label="proxied click")
            await socket.close()
            async with http.delete(url + "/api/vnc/instances/" + entry["id"],
                                   headers=headers) as response:
                assert response.status == 200
            async with http.delete(url + "/api/vnc/instances/" + entry["id"],
                                   headers=headers) as response:
                assert response.status == 404
                assert "closed or unknown" in (await response.json())["error"]
    finally:
        await vnc.manager().stop("test cleanup")
        await web_runner.cleanup()
        await server.stop()
    print("  HTTP surface ok ({})".format(app_factory.__module__))


async def check_agent_bridge(cert, key, session_id: int) -> None:
    server = await StubRdpServer(cert, key, width=40, height=20).start()
    registry = vnc.VncRegistry()
    old_manager = vnc._manager
    vnc._manager = registry
    hub = runner.hub(session_id)
    hub.status = "running"
    hub._active_turn_id = "rdp-turn"
    hub._vnc_activity_announced = set()
    try:
        call = lambda method, **params: vnc_agent._dispatch({  # noqa: E731
            "session_id": session_id, "turn_id": "rdp-turn",
            "method": method, "params": params})
        assert "no remote screen connection open" in (await call("screens"))["text"]
        opened = await call("connect", protocol="rdp", host="127.0.0.1", port=server.port,
                            username="STUBNET\\alice", password="s3cret pass",
                            width=800, height=600)
        first = opened["text"].splitlines()[0]
        screen_id = first.split(" ")[1]
        assert first.startswith("RDP {} on 127.0.0.1:{}".format(screen_id, server.port)), first
        assert "STUBNET\\alice" in first and "40x20 screen" in first, first
        assert opened["image"]["mime_type"] == "image/png"
        assert server.core["width"] == 800 and server.core["height"] == 600
        assert "(RDP 127.0.0.1:{})".format(server.port) in registry.agent_choices()
        listed = await call("screens")
        assert "RDP {}".format(screen_id) in listed["text"], listed
        server.events.clear()
        typed = await call("type", text="ok", screen_id=screen_id)
        assert "Typed 2 character(s)." in typed["text"]
        await wait_for(lambda: ("unicode", ord("k"), 1) in server.events, label="typed")
        scrolled = await call("scroll", x=3, y=4, direction="up", clicks=2)
        assert "Scrolled up 2 notch(es) at 3,4." in scrolled["text"], scrolled
        closed = await call("disconnect")
        assert "Closed RDP {}".format(screen_id) in closed["text"], closed
        assert registry.instance_payloads() == []
    finally:
        hub.status = "idle"
        hub._active_turn_id = ""
        await registry.stop("test cleanup")
        vnc._manager = old_manager
        await server.stop()
    print("  agent bridge ok")


def check_contract() -> None:
    assert protocol.RDP_CAPABILITY in protocol.execution_capabilities(include_terminal=False)
    schema = {tool["name"]: tool["inputSchema"]["properties"] for tool in vnc_agent.TOOLS}
    assert schema["connect"]["protocol"]["enum"] == ["vnc", "rdp"]
    assert {"username", "width", "height"} <= set(schema["connect"])
    assert "screen_id" in schema["click"] and "vnc_id" not in schema["click"]
    for words in ('"@RDP A8AR"', '"@RDP host:port user password"', '"@VNC A8AR"',
                  '"@VNC host:port password"', "omit screen_id"):
        assert words in vnc_agent.TOOL_INSTRUCTIONS, words
    assert "VNC or RDP" in config.DEFAULT_VNC_SYSTEM_PROMPT
    app_js = (BASE / "puppy" / "static" / "app.js").read_text()
    index = (BASE / "puppy" / "static" / "index.html").read_text()
    assert 'data-act="new-rdp"' in index
    assert 'act === "new-rdp") modalNewVnc(groupId, "rdp")' in app_js
    assert "function rdpShortcutText" in app_js and "@${kind} ${inst.id}" in app_js
    assert '"New RDP connection"' in app_js and 'backendHasCapability(backend, "rdp")' in app_js
    assert "RDP (?:[A-Z0-9]{4}|" in app_js
    print("  contract ok")


async def main() -> None:
    try:
        config.load()
        db.connect()
        cert, key = make_certificate(TEST_ROOT)
        check_crypto()
        check_certificate(cert)
        check_rle()
        check_pixels()
        check_wire_helpers()
        check_validation()
        check_contract()
        await check_sign_in(cert, key)
        await check_screen(cert, key)
        await check_input(cert, key)
        await check_attention(cert, key)
        await check_agent_screenshot(cert, key)
        await check_http_surface(cert, key)
        await check_http_surface(cert, key, backend_app)
        await check_agent_bridge(cert, key, db.create_session(
            "rdp agent", "codex", str(TEST_ROOT), "", "", "#7aa2f7", "danger-full-access"))
        print("rdp tests passed")
    finally:
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
