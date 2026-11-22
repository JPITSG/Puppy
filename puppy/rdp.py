"""Node-owned RDP client connections: the remote-screen system's second protocol.

Puppy dials a Windows Remote Desktop server itself, the way it dials a VNC
server: one TCP socket per identified connection, one decoded framebuffer, and
the same damage rectangles to every console viewer and the same agent tools.
An :class:`RdpInstance` *is* a :class:`puppy.vnc.VncInstance` - the registry,
IDs, viewer socket, idle timer, screenshots and agent surface are shared - and
only the wire beneath it is different.

The whole client is stdlib.  ``ssl`` carries the TLS layer through memory BIOs,
so the RDP framing above it stays ordinary buffered parsing; ``hashlib`` and
``hmac`` cover Network Level Authentication (CredSSP with NTLMv2), with a small
in-module MD4 for hosts whose OpenSSL no longer offers it and an in-module RC4
for NTLM sealing.  Nothing is installed, spawned or written to disk, and the
password lives only in this process, exactly like a VNC password.

What is spoken, and what is deliberately not:

* Security: TLS, and NLA (CredSSP 2-6 over NTLMv2, with the TLS key bound into
  the exchange and early user authorization when the server offers it).  Legacy
  "standard RDP security" with its RC4 session encryption is never offered.
* Graphics: classic bitmap updates only.  The client advertises no drawing
  orders, no bitmap caches, no surface commands, no RemoteFX and no graphics
  pipeline, so every screen change arrives as a bitmap - Interleaved RLE at 15,
  16 or 24 bits per pixel, or uncompressed.  32-bit colour is not offered,
  because its planar codec would buy nothing an agent can see.
* Input: fast-path keyboard (scancodes for keys, Unicode for text), mouse and
  wheel events, with the slow-path PDUs as the fallback the server may demand.
* Nobody watching means nobody paying: with no active viewer and no agent at
  work the client sends Suppress Output, so an unwatched session costs one idle
  socket, and allowing output again brings the whole screen back when someone
  looks.

Cost discipline, as in the VNC client: the RLE decoder works in runs, not
pixels.  Fills and colour runs are byte multiplications, a background run is a
periodic copy of the row above, foreground runs and foreground/background
images are whole-chunk XORs through Python's big integers, and the 8-pixel
patterns of a bitmask come from a 256-entry table built from two 16-entry
halves.  Pixel conversion to RGBA is a handful of C-level slice assignments.

Disconnecting leaves the Windows session running behind its lock screen, so a
reconnect resumes where the person or the agent left off.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import logging
import os
import socket
import ssl
import struct
import time

from puppy import vnc

log = logging.getLogger("puppy.rdp")

DEFAULT_PORT = 3389
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
MIN_DIMENSION = 200
MAX_DESKTOP = 4096
MAX_USERNAME = 256
HANDSHAKE_TIMEOUT = 30.0
READ_CHUNK = 1 << 16
# How long agent attention keeps display updates flowing with no viewer.
AGENT_ATTENTION = 120.0
# A reassembled fast-path update never grows past this: the server's own
# limit is the multifragment size the client advertises.
MAX_REQUEST_SIZE = 4 << 20
CLIENT_BUILD = 19041

# negotiation
PROTOCOL_SSL = 0x1
PROTOCOL_HYBRID = 0x2
PROTOCOL_HYBRID_EX = 0x8

_NEGOTIATION_FAILURES = {
    1: "the server requires TLS, which it then refused",
    2: "the server does not allow TLS, and Puppy never speaks legacy RDP security",
    3: "the server has no TLS certificate configured",
    4: "the server reported inconsistent security flags",
    5: "the server requires Network Level Authentication: supply a user name and password",
    6: "the server requires TLS with smart-card or certificate sign-in",
}

# client info flags
INFO_MOUSE = 0x00000001
INFO_DISABLECTRLALTDEL = 0x00000002
INFO_AUTOLOGON = 0x00000008
INFO_UNICODE = 0x00000010
INFO_MAXIMIZESHELL = 0x00000020
INFO_LOGONNOTIFY = 0x00000040
INFO_ENABLEWINDOWSKEY = 0x00000100
INFO_LOGONERRORS = 0x00010000
INFO_MOUSE_HAS_WHEEL = 0x00020000
INFO_NOAUDIOPLAYBACK = 0x00080000

# What a remote session leaves out to keep its screen cheap and still: no
# wallpaper, no window contents while dragging, no menu animations, no cursor
# shadow and no blinking caret - a blinking caret would keep the screen from
# ever settling for an agent waiting for it to stop changing.
PERFORMANCE_FLAGS = 0x01 | 0x02 | 0x04 | 0x20 | 0x40

# security header flags
SEC_INFO_PKT = 0x0040
SEC_LICENSE_PKT = 0x0080

# share control PDU types
PDUTYPE_DEMANDACTIVE = 0x1
PDUTYPE_CONFIRMACTIVE = 0x3
PDUTYPE_DEACTIVATEALL = 0x6
PDUTYPE_DATA = 0x7
PDUTYPE_SERVER_REDIRECT = 0xA

# share data PDU types
PDUTYPE2_UPDATE = 0x02
PDUTYPE2_CONTROL = 0x14
PDUTYPE2_INPUT = 0x1C
PDUTYPE2_SYNCHRONIZE = 0x1F
PDUTYPE2_REFRESH_RECT = 0x21
PDUTYPE2_SUPPRESS_OUTPUT = 0x23
PDUTYPE2_FONTLIST = 0x27
PDUTYPE2_SET_ERROR_INFO = 0x2F

STREAM_LOW = 1
UPDATETYPE_BITMAP = 0x0001

# fast-path output
FASTPATH_UPDATETYPE_BITMAP = 0x1
FASTPATH_FRAGMENT_SINGLE = 0
FASTPATH_FRAGMENT_LAST = 1
FASTPATH_FRAGMENT_FIRST = 2
FASTPATH_FRAGMENT_NEXT = 3

# bitmap data flags
BITMAP_COMPRESSION = 0x0001
NO_BITMAP_COMPRESSION_HDR = 0x0400

# input
INPUT_FLAG_SCANCODES = 0x0001
INPUT_FLAG_MOUSEX = 0x0004
INPUT_FLAG_FASTPATH_INPUT = 0x0008
INPUT_FLAG_UNICODE = 0x0010
INPUT_FLAG_FASTPATH_INPUT2 = 0x0020
TS_INPUT_FLAG_MOUSE_HWHEEL = 0x0100

PTRFLAGS_HWHEEL = 0x0400
PTRFLAGS_WHEEL = 0x0200
PTRFLAGS_WHEEL_NEGATIVE = 0x0100
PTRFLAGS_MOVE = 0x0800
PTRFLAGS_DOWN = 0x8000
PTRFLAGS_BUTTON1 = 0x1000          # left
PTRFLAGS_BUTTON2 = 0x2000          # right
PTRFLAGS_BUTTON3 = 0x4000          # middle
WHEEL_NOTCH = 120

# The disconnect reasons a server announces before it ends a session.
_ERROR_INFO = {
    0x00000001: "an administrator or another session ended this connection",
    0x00000002: "the session was signed out by an administrator",
    0x00000003: "the session was idle for longer than the server allows",
    0x00000004: "the sign-in took longer than the server allows",
    0x00000005: "another connection took over this session",
    0x00000006: "the server ran out of memory",
    0x00000007: "the server refused the connection",
    0x00000009: "this account is not allowed to connect to that server",
    0x0000000A: "the server asked for fresh credentials",
    0x0000000B: "someone on the remote computer ended this connection",
    0x0000000C: "the session was signed out",
    0x0000000F: "the server's display driver was not ready",
    0x00000010: "the server's display driver failed",
    0x00000100: "the server's licensing failed",
    0x00000101: "the server could not reach a license server",
    0x00000102: "the server has no Remote Desktop license for this connection",
    0x00000103: "the server rejected this client's license",
    0x00000106: "the server's license is invalid",
    0x00000108: "the license server ran out of client licenses",
    0x00000109: "the license has expired",
}

# NTSTATUS and SSPI codes a CredSSP refusal carries, worded for the pane.
_SIGN_IN_REFUSALS = {
    0xC000006D: "Windows rejected the user name or password",
    0xC000006A: "Windows rejected the password",
    0xC0000064: "Windows does not know that user name",
    0xC000006E: "this account is restricted from signing in this way",
    0xC000006F: "this account may not sign in at this time of day",
    0xC0000070: "this account may not sign in from this computer",
    0xC0000071: "this account's password has expired",
    0xC0000072: "this account is disabled",
    0xC0000193: "this account has expired",
    0xC0000224: "this account must change its password before it can sign in",
    0xC0000234: "this account is locked out",
    0xC000015B: "this account is not allowed to sign in over Remote Desktop",
    0xC0000413: "an authentication firewall refused the sign-in",
    0x80090302: "the server does not support this sign-in method",
    0x80090308: "the server rejected the sign-in message",
    0x8009030C: "Windows rejected the sign-in",
    0x8009030E: "the server has no credentials for this sign-in",
}


class RdpError(vnc.VncError):
    """Every RDP refusal is a remote-screen refusal the shared code handles."""


class _Closed(RdpError):
    pass


def _check(condition, message: str) -> None:
    if not condition:
        raise RdpError(message)


# --------------------------------------------------------------------------
# Crypto: MD4 (fallback), RC4, NTLMv2, CredSSP
# --------------------------------------------------------------------------

def _md4_python(data: bytes) -> bytes:
    """RFC 1320, for hosts whose OpenSSL no longer ships MD4 (OpenSSL 3)."""
    def rotl(value, shift):
        value &= 0xFFFFFFFF
        return ((value << shift) | (value >> (32 - shift))) & 0xFFFFFFFF

    message = bytearray(data)
    bit_length = (len(data) * 8) & 0xFFFFFFFFFFFFFFFF
    message.append(0x80)
    while len(message) % 64 != 56:
        message.append(0)
    message += struct.pack("<Q", bit_length)
    a, b, c, d = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
    for offset in range(0, len(message), 64):
        x = struct.unpack("<16I", bytes(message[offset:offset + 64]))
        aa, bb, cc, dd = a, b, c, d

        def f(x_, y_, z_):
            return (x_ & y_) | (~x_ & z_)

        def g(x_, y_, z_):
            return (x_ & y_) | (x_ & z_) | (y_ & z_)

        def h(x_, y_, z_):
            return x_ ^ y_ ^ z_

        for index in (0, 4, 8, 12):
            a = rotl(a + f(b, c, d) + x[index], 3)
            d = rotl(d + f(a, b, c) + x[index + 1], 7)
            c = rotl(c + f(d, a, b) + x[index + 2], 11)
            b = rotl(b + f(c, d, a) + x[index + 3], 19)
        for index in (0, 1, 2, 3):
            a = rotl(a + g(b, c, d) + x[index] + 0x5A827999, 3)
            d = rotl(d + g(a, b, c) + x[index + 4] + 0x5A827999, 5)
            c = rotl(c + g(d, a, b) + x[index + 8] + 0x5A827999, 9)
            b = rotl(b + g(c, d, a) + x[index + 12] + 0x5A827999, 13)
        for index in (0, 2, 1, 3):
            a = rotl(a + h(b, c, d) + x[index] + 0x6ED9EBA1, 3)
            d = rotl(d + h(a, b, c) + x[index + 8] + 0x6ED9EBA1, 9)
            c = rotl(c + h(d, a, b) + x[index + 4] + 0x6ED9EBA1, 11)
            b = rotl(b + h(c, d, a) + x[index + 12] + 0x6ED9EBA1, 15)
        a = (a + aa) & 0xFFFFFFFF
        b = (b + bb) & 0xFFFFFFFF
        c = (c + cc) & 0xFFFFFFFF
        d = (d + dd) & 0xFFFFFFFF
    return struct.pack("<4I", a, b, c, d)


def md4(data: bytes) -> bytes:
    try:
        return hashlib.new("md4", data).digest()
    except (ValueError, TypeError):
        return _md4_python(data)


class Rc4:
    """The stream cipher NTLM seals with; tiny messages only, so pure Python."""

    __slots__ = ("box", "i", "j")

    def __init__(self, key: bytes):
        box = list(range(256))
        j = 0
        for i in range(256):
            j = (j + box[i] + key[i % len(key)]) & 0xFF
            box[i], box[j] = box[j], box[i]
        self.box = box
        self.i = self.j = 0

    def update(self, data: bytes) -> bytes:
        box, i, j = self.box, self.i, self.j
        out = bytearray(len(data))
        for index, value in enumerate(data):
            i = (i + 1) & 0xFF
            j = (j + box[i]) & 0xFF
            box[i], box[j] = box[j], box[i]
            out[index] = value ^ box[(box[i] + box[j]) & 0xFF]
        self.i, self.j = i, j
        return bytes(out)


def _hmac_md5(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.md5).digest()


def _utf16(text: str) -> bytes:
    return str(text or "").encode("utf-16-le")


NTLMSSP_NEGOTIATE_UNICODE = 0x00000001
NTLMSSP_REQUEST_TARGET = 0x00000004
NTLMSSP_NEGOTIATE_SIGN = 0x00000010
NTLMSSP_NEGOTIATE_SEAL = 0x00000020
NTLMSSP_NEGOTIATE_NTLM = 0x00000200
NTLMSSP_NEGOTIATE_ALWAYS_SIGN = 0x00008000
NTLMSSP_NEGOTIATE_EXTENDED_SESSIONSECURITY = 0x00080000
NTLMSSP_NEGOTIATE_TARGET_INFO = 0x00800000
NTLMSSP_NEGOTIATE_VERSION = 0x02000000
NTLMSSP_NEGOTIATE_128 = 0x20000000
NTLMSSP_NEGOTIATE_KEY_EXCH = 0x40000000
NTLMSSP_NEGOTIATE_56 = 0x80000000
NTLM_NEGOTIATE_FLAGS = (
    NTLMSSP_NEGOTIATE_56 | NTLMSSP_NEGOTIATE_KEY_EXCH | NTLMSSP_NEGOTIATE_128 |
    NTLMSSP_NEGOTIATE_VERSION | NTLMSSP_NEGOTIATE_EXTENDED_SESSIONSECURITY |
    NTLMSSP_NEGOTIATE_ALWAYS_SIGN | NTLMSSP_NEGOTIATE_NTLM | NTLMSSP_NEGOTIATE_SEAL |
    NTLMSSP_NEGOTIATE_SIGN | NTLMSSP_REQUEST_TARGET | NTLMSSP_NEGOTIATE_UNICODE)
# Windows 10 build 19041, NTLM revision 15: what the version field says.
NTLM_VERSION = struct.pack("<BBHBBBB", 10, 0, CLIENT_BUILD, 0, 0, 0, 15)

MSV_AV_EOL = 0
MSV_AV_NB_COMPUTER = 1
MSV_AV_NB_DOMAIN = 2
MSV_AV_DNS_COMPUTER = 3
MSV_AV_FLAGS = 6
MSV_AV_TIMESTAMP = 7

_SIGNING_CLIENT = b"session key to client-to-server signing key magic constant\x00"
_SIGNING_SERVER = b"session key to server-to-client signing key magic constant\x00"
_SEALING_CLIENT = b"session key to client-to-server sealing key magic constant\x00"
_SEALING_SERVER = b"session key to server-to-client sealing key magic constant\x00"


def ntowfv2(password: str, user: str, domain: str) -> bytes:
    return _hmac_md5(md4(_utf16(password)), _utf16(str(user or "").upper() + str(domain or "")))


def av_pairs(blob: bytes) -> list:
    """The target information list, in order, without its terminator."""
    pairs = []
    pos = 0
    while pos + 4 <= len(blob):
        kind, length = struct.unpack_from("<HH", blob, pos)
        pos += 4
        if kind == MSV_AV_EOL:
            return pairs
        _check(pos + length <= len(blob), "the server sent malformed NTLM target information")
        pairs.append((kind, bytes(blob[pos:pos + length])))
        pos += length
    raise RdpError("the server sent NTLM target information without its end")


def _av_blob(pairs: list) -> bytes:
    return b"".join(struct.pack("<HH", kind, len(value)) + value for kind, value in pairs) + \
        b"\x00\x00\x00\x00"


def _filetime() -> bytes:
    return struct.pack("<Q", int((time.time() + 11644473600) * 10000000))


class Ntlm:
    """The client half of NTLMv2 as CredSSP drives it: three messages, then a
    sealing context for the public-key binding and the delegated credentials.

    ``client_challenge``, ``session_key`` and ``timestamp`` exist so the
    published MS-NLMP test vectors can be reproduced byte for byte.
    """

    def __init__(self, user: str, domain: str, password: str, workstation: str = "",
                 client_challenge: bytes = None, session_key: bytes = None,
                 timestamp: bytes = None, version: bytes = NTLM_VERSION):
        self.user = str(user or "")
        self.domain = str(domain or "")
        self.password = str(password or "")
        self.workstation = str(workstation or "")
        self.client_challenge = client_challenge
        self.session_key = session_key
        self.timestamp = timestamp
        self.version = version
        self.negotiate_message = b""
        self.flags = 0
        self.server_name = ""
        self.server_dns = ""
        self.server_domain = ""
        self.server_version = ""
        self.exported_key = b""
        self.send_seq = 0
        self.recv_seq = 0
        self.send_seal = self.recv_seal = None
        self.send_sign = self.recv_sign = b""

    def negotiate(self) -> bytes:
        message = (b"NTLMSSP\x00" + struct.pack("<II", 1, NTLM_NEGOTIATE_FLAGS) +
                   struct.pack("<HHI", 0, 0, 0) + struct.pack("<HHI", 0, 0, 0) + self.version)
        self.negotiate_message = message
        return message

    def authenticate(self, challenge: bytes) -> bytes:
        """Answer the CHALLENGE_MESSAGE with the AUTHENTICATE_MESSAGE."""
        challenge = bytes(challenge)
        _check(len(challenge) >= 48 and challenge[:8] == b"NTLMSSP\x00" and
               struct.unpack_from("<I", challenge, 8)[0] == 2,
               "the server did not answer with an NTLM challenge")
        flags = struct.unpack_from("<I", challenge, 20)[0]
        server_challenge = challenge[24:32]
        info_length, _info_max, info_offset = struct.unpack_from("<HHI", challenge, 40)
        _check(info_offset + info_length <= len(challenge),
               "the server sent a malformed NTLM challenge")
        pairs = av_pairs(challenge[info_offset:info_offset + info_length]) \
            if flags & NTLMSSP_NEGOTIATE_TARGET_INFO and info_length else []
        _check(flags & NTLMSSP_NEGOTIATE_EXTENDED_SESSIONSECURITY,
               "the server offered only legacy NTLM, which Puppy does not speak")
        if flags & NTLMSSP_NEGOTIATE_VERSION and len(challenge) >= 56:
            major, minor, build = struct.unpack_from("<BBH", challenge, 48)
            self.server_version = "Windows {}.{} build {}".format(major, minor, build)
        found = dict(pairs)
        self.server_name = found.get(MSV_AV_NB_COMPUTER, b"").decode("utf-16-le", "replace")
        self.server_dns = found.get(MSV_AV_DNS_COMPUTER, b"").decode("utf-16-le", "replace")
        self.server_domain = found.get(MSV_AV_NB_DOMAIN, b"").decode("utf-16-le", "replace")

        has_timestamp = MSV_AV_TIMESTAMP in found
        timestamp = self.timestamp or found.get(MSV_AV_TIMESTAMP) or _filetime()
        client_challenge = self.client_challenge or os.urandom(8)
        # The server's list, with MsvAvFlags saying a MIC follows when the
        # server stamped the challenge (it then requires one).
        out_pairs = [(kind, value) for kind, value in pairs if kind != MSV_AV_FLAGS]
        if has_timestamp:
            flag_value = struct.unpack("<I", found[MSV_AV_FLAGS])[0] \
                if len(found.get(MSV_AV_FLAGS, b"")) == 4 else 0
            out_pairs.append((MSV_AV_FLAGS, struct.pack("<I", flag_value | 0x2)))
        temp = (b"\x01\x01" + b"\x00" * 6 + timestamp + client_challenge + b"\x00" * 4 +
                _av_blob(out_pairs) + b"\x00" * 4)
        key = ntowfv2(self.password, self.user, self.domain)
        proof = _hmac_md5(key, server_challenge + temp)
        nt_response = proof + temp
        lm_response = b"\x00" * 24 if has_timestamp else \
            _hmac_md5(key, server_challenge + client_challenge) + client_challenge
        session_base = _hmac_md5(key, proof)
        if flags & NTLMSSP_NEGOTIATE_KEY_EXCH:
            self.exported_key = self.session_key or os.urandom(16)
            encrypted_key = Rc4(session_base).update(self.exported_key)
        else:
            self.exported_key = session_base
            encrypted_key = b""

        fields = [_utf16(self.domain), _utf16(self.user), _utf16(self.workstation),
                  lm_response, nt_response, encrypted_key]
        base = 88 if has_timestamp else 72
        offset = base
        positions = []
        for value in fields:
            positions.append(offset)
            offset += len(value)
        domain, user, workstation, lm, nt, session = fields

        def field(index):
            value = fields[index]
            return struct.pack("<HHI", len(value), len(value), positions[index])

        head = (b"NTLMSSP\x00" + struct.pack("<I", 3) + field(3) + field(4) + field(0) +
                field(1) + field(2) + field(5) + struct.pack("<I", flags) + self.version)
        mic = b"\x00" * 16 if has_timestamp else b""
        payload = domain + user + workstation + lm + nt + session
        message = head + mic + payload
        if has_timestamp:
            mic = _hmac_md5(self.exported_key,
                            self.negotiate_message + challenge + message)
            message = head + mic + payload
        self.flags = flags
        self._derive_keys()
        return message

    def _derive_keys(self) -> None:
        key = self.exported_key
        if self.flags & NTLMSSP_NEGOTIATE_128:
            seal = key
        elif self.flags & NTLMSSP_NEGOTIATE_56:
            seal = key[:7]
        else:
            seal = key[:5]
        self.send_sign = hashlib.md5(key + _SIGNING_CLIENT).digest()
        self.recv_sign = hashlib.md5(key + _SIGNING_SERVER).digest()
        self.send_seal = Rc4(hashlib.md5(seal + _SEALING_CLIENT).digest())
        self.recv_seal = Rc4(hashlib.md5(seal + _SEALING_SERVER).digest())
        self.send_seq = self.recv_seq = 0

    def seal(self, data: bytes) -> bytes:
        """EncryptMessage as CredSSP lays it out: signature, then ciphertext."""
        sealed = self.send_seal.update(data)
        digest = _hmac_md5(self.send_sign, struct.pack("<I", self.send_seq) + data)[:8]
        if self.flags & NTLMSSP_NEGOTIATE_KEY_EXCH:
            digest = self.send_seal.update(digest)
        signature = struct.pack("<I", 1) + digest + struct.pack("<I", self.send_seq)
        self.send_seq += 1
        return signature + sealed

    def unseal(self, blob: bytes) -> bytes:
        _check(len(blob) >= 16, "the server sent a truncated sealed message")
        signature, sealed = bytes(blob[:16]), bytes(blob[16:])
        data = self.recv_seal.update(sealed)
        digest = _hmac_md5(self.recv_sign, struct.pack("<I", self.recv_seq) + data)[:8]
        if self.flags & NTLMSSP_NEGOTIATE_KEY_EXCH:
            digest = self.recv_seal.update(digest)
        expected = struct.pack("<I", 1) + digest + struct.pack("<I", self.recv_seq)
        _check(hmac.compare_digest(signature, expected),
               "the server's sealed reply failed its integrity check")
        self.recv_seq += 1
        return data


# ---- DER, for CredSSP's TSRequest and the server certificate ----

def _der_length(length: int) -> bytes:
    if length < 0x80:
        return bytes((length,))
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes((0x80 | len(raw),)) + raw


def _der(tag: int, content: bytes) -> bytes:
    return bytes((tag,)) + _der_length(len(content)) + content


def _der_integer(value: int) -> bytes:
    if value < 0:
        raw = value.to_bytes(4, "big", signed=True)
    else:
        raw = value.to_bytes(value.bit_length() // 8 + 1, "big")
    return _der(0x02, raw)


def _der_items(data: bytes) -> list:
    """The TLVs laid end to end in ``data``: [(tag, value bytes)]."""
    items = []
    pos = 0
    data = bytes(data)
    while pos < len(data):
        _check(pos + 2 <= len(data), "malformed DER structure")
        tag = data[pos]
        length = data[pos + 1]
        pos += 2
        if length & 0x80:
            count = length & 0x7F
            _check(1 <= count <= 4 and pos + count <= len(data), "malformed DER length")
            length = int.from_bytes(data[pos:pos + count], "big")
            pos += count
        _check(pos + length <= len(data), "truncated DER value")
        items.append((tag, data[pos:pos + length]))
        pos += length
    return items


def subject_public_key(certificate: bytes) -> bytes:
    """The certificate's SubjectPublicKey bits - what CredSSP binds to."""
    try:
        (tag, cert), = _der_items(certificate)
        _check(tag == 0x30, "certificate is not a sequence")
        tbs = _der_items(cert)[0]
        _check(tbs[0] == 0x30, "certificate has no TBSCertificate")
        fields = _der_items(tbs[1])
        if fields and fields[0][0] == 0xA0:          # explicit version
            fields = fields[1:]
        spki = fields[5]
        _check(spki[0] == 0x30, "certificate has no SubjectPublicKeyInfo")
        bits = _der_items(spki[1])[1]
        _check(bits[0] == 0x03 and bits[1][:1] == b"\x00", "unexpected public key encoding")
        return bits[1][1:]
    except (IndexError, ValueError) as exc:
        raise RdpError("the server's TLS certificate is malformed: {}".format(exc))


def ts_request(version: int, nego: bytes = b"", auth_info: bytes = b"",
               pub_key_auth: bytes = b"", nonce: bytes = b"") -> bytes:
    parts = [_der(0xA0, _der_integer(version))]
    if nego:
        parts.append(_der(0xA1, _der(0x30, _der(0x30, _der(0xA0, _der(0x04, nego))))))
    if auth_info:
        parts.append(_der(0xA2, _der(0x04, auth_info)))
    if pub_key_auth:
        parts.append(_der(0xA3, _der(0x04, pub_key_auth)))
    if nonce:
        parts.append(_der(0xA5, _der(0x04, nonce)))
    return _der(0x30, b"".join(parts))


def parse_ts_request(data: bytes) -> dict:
    """version, nego, auth_info, pub_key_auth, error and nonce of a TSRequest."""
    try:
        (tag, body), = _der_items(data)
        _check(tag == 0x30, "the server's CredSSP message is not a TSRequest")
        out = {"version": 0, "nego": b"", "auth_info": b"", "pub_key_auth": b"",
               "error": 0, "nonce": b""}
        for field, value in _der_items(body):
            inner = _der_items(value)
            if field == 0xA0 and inner and inner[0][0] == 0x02:
                out["version"] = int.from_bytes(inner[0][1], "big", signed=True)
            elif field == 0xA1:
                for _tag, token in _der_items(inner[0][1]) if inner else ():
                    for kind, raw in _der_items(token):
                        if kind == 0xA0:
                            out["nego"] = _der_items(raw)[0][1]
            elif field == 0xA2 and inner:
                out["auth_info"] = inner[0][1]
            elif field == 0xA3 and inner:
                out["pub_key_auth"] = inner[0][1]
            elif field == 0xA4 and inner and inner[0][0] == 0x02:
                out["error"] = int.from_bytes(inner[0][1], "big", signed=True) & 0xFFFFFFFF
            elif field == 0xA5 and inner:
                out["nonce"] = inner[0][1]
        return out
    except (IndexError, ValueError) as exc:
        raise RdpError("the server sent a malformed CredSSP message: {}".format(exc))


def ts_credentials(domain: str, user: str, password: str) -> bytes:
    creds = _der(0x30, _der(0xA0, _der(0x04, _utf16(domain))) +
                 _der(0xA1, _der(0x04, _utf16(user))) +
                 _der(0xA2, _der(0x04, _utf16(password))))
    return _der(0x30, _der(0xA0, _der_integer(1)) + _der(0xA1, _der(0x04, creds)))


_CLIENT_BINDING = b"CredSSP Client-To-Server Binding Hash\x00"
_SERVER_BINDING = b"CredSSP Server-To-Client Binding Hash\x00"


def _increment_le(data: bytes) -> bytes:
    value = bytearray(data)
    for index in range(len(value)):
        value[index] = (value[index] + 1) & 0xFF
        if value[index]:
            break
    return bytes(value)


def sign_in_refusal(code: int) -> str:
    text = _SIGN_IN_REFUSALS.get(code & 0xFFFFFFFF)
    return "{} (0x{:08X})".format(text, code & 0xFFFFFFFF) if text else \
        "the server refused the sign-in (0x{:08X})".format(code & 0xFFFFFFFF)


def _spnego_token(token: bytes) -> bytes:
    """The NTLM message inside a SPNEGO NegTokenResp, or the token itself.

    Windows answers in the shape it was asked in, so a raw NTLM negotiation
    gets raw NTLM back; this only unwraps a server that insists on SPNEGO.
    """
    token = bytes(token)
    if token[:8] == b"NTLMSSP\x00" or not token or token[0] != 0xA1:
        return token
    try:
        (_tag, body), = _der_items(token)
        (_seq, fields), = _der_items(body)
        for field, value in _der_items(fields):
            if field == 0xA2:
                return _der_items(value)[0][1]
    except (ValueError, RdpError):
        pass
    raise RdpError("the server answered the sign-in with an unexpected token")


def certificate_name(certificate: bytes) -> str:
    """The subject's common name, which Windows sets to the computer's name."""
    try:
        (_tag, cert), = _der_items(certificate)
        fields = _der_items(_der_items(cert)[0][1])
        if fields and fields[0][0] == 0xA0:
            fields = fields[1:]
        for _set_tag, rdn in _der_items(fields[4][1]):
            for _seq_tag, pair in _der_items(rdn):
                parts = _der_items(pair)
                if len(parts) == 2 and parts[0] == (0x06, b"\x55\x04\x03"):
                    return parts[1][1].decode("utf-8", "replace").strip()[:vnc.MAX_LABEL]
    except (IndexError, ValueError, RdpError):
        pass
    return ""


def fingerprint(certificate: bytes) -> str:
    digest = hashlib.sha256(certificate).hexdigest().upper()
    return ":".join(digest[index:index + 2] for index in range(0, len(digest), 2))


def split_username(value: str) -> tuple:
    """``DOMAIN\\user`` is a domain and a user; ``user@domain`` stays whole."""
    text = str(value or "").strip()
    if "\\" in text:
        domain, _, user = text.partition("\\")
        return user, domain
    return text, ""


# --------------------------------------------------------------------------
# Transport: TCP, then TLS through memory BIOs
# --------------------------------------------------------------------------

class _Wire:
    """Buffered reads and whole-PDU writes, over TCP and then over TLS.

    ``ssl.SSLObject`` keeps TLS synchronous and in memory, so the framing above
    it is the same exact-size parsing on either side of the upgrade.  Every
    ``send`` is one complete PDU written in one synchronous step, so the reader
    loop and a viewer's input can never interleave inside a message.
    ``received_bytes`` counts what arrived on the socket, which is what the
    console's throughput readout means.
    """

    __slots__ = ("reader", "writer", "buf", "pos", "received_bytes", "tls",
                 "incoming", "outgoing")

    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.buf = bytearray()
        self.pos = 0
        self.received_bytes = 0
        self.tls = None
        self.incoming = None
        self.outgoing = None

    async def _raw(self) -> bytes:
        data = await self.reader.read(READ_CHUNK)
        if not data:
            raise _Closed("the RDP server closed the connection")
        self.received_bytes += len(data)
        return data

    async def _pull(self) -> None:
        if self.tls is not None:
            # Records that came in with the end of the handshake, or with an
            # earlier read, wait inside TLS: take them before the socket.
            before = len(self.buf)
            self._decrypt()
            if len(self.buf) > before:
                return
        data = await self._raw()
        if self.tls is None:
            self.buf += data
            return
        self.incoming.write(data)
        self._decrypt()

    def _decrypt(self) -> None:
        while True:
            try:
                chunk = self.tls.read(READ_CHUNK)
            except ssl.SSLWantReadError:
                break
            except ssl.SSLZeroReturnError:
                raise _Closed("the RDP server closed the connection")
            except ssl.SSLError as exc:
                raise RdpError("the TLS connection failed: {}".format(
                    exc.reason or exc))
            if not chunk:
                raise _Closed("the RDP server closed the connection")
            self.buf += chunk
        self._flush()

    def _flush(self) -> None:
        if self.outgoing is not None:
            pending = self.outgoing.read()
            if pending:
                self.writer.write(pending)

    async def need(self, count: int) -> None:
        if len(self.buf) - self.pos >= count:
            return
        if self.pos:
            del self.buf[:self.pos]
            self.pos = 0
        while len(self.buf) < count:
            await self._pull()

    def take(self, count: int) -> bytes:
        end = self.pos + count
        out = bytes(self.buf[self.pos:end])
        self.pos = end
        return out

    async def read(self, count: int) -> bytes:
        await self.need(count)
        return self.take(count)

    def send(self, data: bytes) -> None:
        if self.tls is None:
            self.writer.write(data)
        else:
            self.tls.write(data)
            self._flush()

    async def start_tls(self, server_hostname=None) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # A Remote Desktop certificate is almost always self-signed, so there
        # is nothing to verify it against: its fingerprint is shown instead,
        # and with NLA the CredSSP exchange binds this exact key to the
        # password before the password is sent.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        try:
            context.minimum_version = ssl.TLSVersion.TLSv1
        except (AttributeError, ValueError):
            pass
        self.incoming = ssl.MemoryBIO()
        self.outgoing = ssl.MemoryBIO()
        try:
            tls = context.wrap_bio(self.incoming, self.outgoing, server_side=False,
                                   server_hostname=server_hostname)
        except (ValueError, UnicodeError):
            # A name TLS cannot carry as SNI is simply not sent: nothing is
            # verified against it anyway.
            tls = context.wrap_bio(self.incoming, self.outgoing, server_side=False)
        if len(self.buf) > self.pos:
            self.incoming.write(bytes(self.buf[self.pos:]))
        self.buf = bytearray()
        self.pos = 0
        while True:
            try:
                tls.do_handshake()
                break
            except ssl.SSLWantReadError:
                self._flush()
                self.incoming.write(await self._raw())
            except ssl.SSLError as exc:
                raise RdpError("TLS negotiation with the server failed: {}".format(
                    exc.reason or exc))
        self.tls = tls
        self._flush()

    def certificate(self) -> bytes:
        return self.tls.getpeercert(True) if self.tls is not None else b""

    def _pdu_length(self) -> int:
        """The length of the PDU at the read position, or 0 while unknown."""
        available = len(self.buf) - self.pos
        if available < 2:
            return 0
        first = self.buf[self.pos]
        if first & 3 == 3:                          # TPKT (slow path)
            if available < 4:
                return 0
            return self.buf[self.pos + 2] << 8 | self.buf[self.pos + 3]
        second = self.buf[self.pos + 1]             # fast path
        if second & 0x80:
            if available < 3:
                return 0
            return (second & 0x7F) << 8 | self.buf[self.pos + 2]
        return second

    def pdu_ready(self) -> bool:
        length = self._pdu_length()
        return bool(length) and len(self.buf) - self.pos >= length

    async def read_pdu(self) -> bytes:
        """One whole PDU, slow path (TPKT) or fast path, headers included."""
        await self.need(2)
        first = self.buf[self.pos]
        if first & 3 == 3:
            await self.need(4)
        elif self.buf[self.pos + 1] & 0x80:
            await self.need(3)
        length = self._pdu_length()
        if first & 3 == 3:
            _check(length >= 7, "the RDP server sent a malformed packet")
        elif first & 3:
            raise RdpError("the RDP server sent an unknown kind of packet")
        else:
            _check(length >= 3, "the RDP server sent a malformed fast-path packet")
        return await self.read(length)


# --------------------------------------------------------------------------
# X.224, MCS and GCC
# --------------------------------------------------------------------------

def _tpkt(payload: bytes) -> bytes:
    return struct.pack(">BBH", 3, 0, len(payload) + 4) + payload


def _x224(payload: bytes) -> bytes:
    return _tpkt(b"\x02\xf0\x80" + payload)


def connection_request(protocols: int, cookie: str = "") -> bytes:
    body = b""
    cookie = "".join(char for char in str(cookie or "")
                     if " " < char < "\x7f")[:64]
    if cookie:
        # The routing cookie a Remote Desktop broker reads; mstsc sends it too.
        body += b"Cookie: mstshash=" + cookie.encode("ascii") + b"\r\n"
    body += struct.pack("<BBHI", 1, 0, 8, protocols)
    return _tpkt(bytes((len(body) + 6, 0xE0, 0, 0, 0, 0, 0)) + body)


def parse_connection_confirm(pdu: bytes) -> int:
    """The security protocol the server chose, or a refusal in words."""
    _check(len(pdu) >= 11 and pdu[5] & 0xF0 == 0xD0,
           "the server did not answer the Remote Desktop connection request")
    if len(pdu) < 19:
        raise RdpError("the server speaks only legacy RDP security, which Puppy "
                       "never uses; enable TLS or Network Level Authentication")
    kind, _flags, length, value = struct.unpack_from("<BBHI", pdu, 11)
    _check(length == 8, "the server sent a malformed negotiation reply")
    if kind == 3:
        raise RdpError(_NEGOTIATION_FAILURES.get(
            value, "the server refused the connection (negotiation failure {})".format(
                value)))
    _check(kind == 2, "the server sent an unknown negotiation reply")
    return value


def _x224_payload(pdu: bytes) -> bytes:
    _check(len(pdu) >= 7, "the RDP server sent a truncated packet")
    li = pdu[4]
    code = pdu[5] & 0xF0
    if code == 0x80:
        raise _Closed("the RDP server ended the connection")
    _check(code == 0xF0 and 5 + li <= len(pdu), "the RDP server sent an unexpected packet")
    return pdu[5 + li:]


def _ber_length(length: int) -> bytes:
    if length < 0x80:
        return bytes((length,))
    if length < 0x100:
        return bytes((0x81, length))
    return bytes((0x82, length >> 8, length & 0xFF))


def _ber(tag: bytes, content: bytes) -> bytes:
    return tag + _ber_length(len(content)) + content


def _ber_integer(value: int) -> bytes:
    return _ber(b"\x02", value.to_bytes(value.bit_length() // 8 + 1, "big"))


def _ber_read(data: bytes, pos: int) -> tuple:
    """(tag, value, next position) of one BER element."""
    _check(pos + 2 <= len(data), "the server sent a truncated MCS reply")
    tag = data[pos]
    pos += 1
    if tag & 0x1F == 0x1F:
        tag = tag << 8 | data[pos]
        pos += 1
    length = data[pos]
    pos += 1
    if length & 0x80:
        count = length & 0x7F
        _check(1 <= count <= 3 and pos + count <= len(data),
               "the server sent a malformed MCS reply")
        length = int.from_bytes(data[pos:pos + count], "big")
        pos += count
    _check(pos + length <= len(data), "the server sent a truncated MCS reply")
    return tag, data[pos:pos + length], pos + length


_DOMAIN_TARGET = (34, 2, 0, 1, 0, 1, 0xFFFF, 2)
_DOMAIN_MINIMUM = (1, 1, 1, 1, 0, 1, 0x420, 2)
_DOMAIN_MAXIMUM = (0xFFFF, 0xFC17, 0xFFFF, 1, 0, 1, 0xFFFF, 2)


def _domain_parameters(values) -> bytes:
    return _ber(b"\x30", b"".join(_ber_integer(value) for value in values))


def _per_length(length: int) -> bytes:
    return bytes((length,)) if length < 0x80 else struct.pack(">H", 0x8000 | length)


def conference_create_request(user_data: bytes) -> bytes:
    """T.124 ConferenceCreateRequest around the client data blocks."""
    _check(len(user_data) >= 0x80, "client data is unexpectedly short")
    return (b"\x00\x05\x00\x14\x7c\x00\x01" + _per_length(len(user_data) + 14) +
            b"\x00\x08\x00\x10\x00\x01\xc0\x00Duca" + _per_length(len(user_data)) +
            user_data)


def connect_initial(user_data: bytes) -> bytes:
    body = (_ber(b"\x04", b"\x01") + _ber(b"\x04", b"\x01") + _ber(b"\x01", b"\xff") +
            _domain_parameters(_DOMAIN_TARGET) + _domain_parameters(_DOMAIN_MINIMUM) +
            _domain_parameters(_DOMAIN_MAXIMUM) +
            _ber(b"\x04", conference_create_request(user_data)))
    return _x224(_ber(b"\x7f\x65", body))


def client_core_data(width: int, height: int, client_name: str, selected: int) -> bytes:
    name = _utf16(client_name)[:30]
    name += b"\x00" * (32 - len(name))
    body = struct.pack("<IHHHHII", 0x00080004, width, height, 0xCA01, 0xAA03,
                       0x409, CLIENT_BUILD)
    body += name + struct.pack("<III", 4, 0, 12) + b"\x00" * 64
    # 24, 16 and 15 bits per pixel; error info PDUs and a valid connection
    # type (LAN) in the early capabilities.
    body += struct.pack("<HHIHHH", 0xCA01, 1, 0, 24, 0x0007, 0x0001 | 0x0020)
    body += b"\x00" * 64 + struct.pack("<BBI", 6, 0, selected)
    return struct.pack("<HH", 0xC001, len(body) + 4) + body


def client_data(width: int, height: int, client_name: str, selected: int) -> bytes:
    return (client_core_data(width, height, client_name, selected) +
            struct.pack("<HHII", 0xC004, 12, 0, 0) +       # cluster: no redirection
            struct.pack("<HHII", 0xC002, 12, 0, 0))        # security: TLS does it


def parse_connect_response(payload: bytes) -> bytes:
    """The server data blocks of an MCS Connect-Response."""
    tag, body, _end = _ber_read(payload, 0)
    _check(tag == 0x7F66, "the server did not answer the MCS connection")
    tag, result, pos = _ber_read(body, 0)
    _check(tag == 0x0A, "the server sent a malformed MCS reply")
    if result != b"\x00":
        raise RdpError("the server refused the MCS connection (result {})".format(
            int.from_bytes(result or b"\x00", "big")))
    _tag, _connect_id, pos = _ber_read(body, pos)
    _tag, _parameters, pos = _ber_read(body, pos)
    tag, user_data, _pos = _ber_read(body, pos)
    _check(tag == 0x04, "the server sent a malformed MCS reply")
    at = user_data.find(b"McDn")
    _check(at >= 0 and at + 5 <= len(user_data), "the server sent no conference data")
    pos = at + 4
    length = user_data[pos]
    if length & 0x80:
        _check(pos + 2 <= len(user_data), "the server sent malformed conference data")
        length = (length & 0x7F) << 8 | user_data[pos + 1]
        pos += 2
    else:
        pos += 1
    _check(pos + length <= len(user_data), "the server sent truncated conference data")
    return user_data[pos:pos + length]


def parse_server_data(data: bytes) -> dict:
    out = {"version": 0, "io_channel": 1003, "channels": [], "message_channel": 0,
           "encryption": 0}
    pos = 0
    while pos + 4 <= len(data):
        kind, length = struct.unpack_from("<HH", data, pos)
        _check(length >= 4 and pos + length <= len(data),
               "the server sent malformed conference data")
        body = data[pos + 4:pos + length]
        if kind == 0x0C01 and len(body) >= 4:
            out["version"] = struct.unpack_from("<I", body)[0]
        elif kind == 0x0C02 and len(body) >= 4:
            out["encryption"] = struct.unpack_from("<I", body)[0]
        elif kind == 0x0C03 and len(body) >= 4:
            io_channel, count = struct.unpack_from("<HH", body)
            _check(count <= 31 and 4 + 2 * count <= len(body),
                   "the server announced too many channels")
            out["io_channel"] = io_channel
            out["channels"] = list(struct.unpack_from("<{}H".format(count), body, 4))
        elif kind == 0x0C04 and len(body) >= 2:
            out["message_channel"] = struct.unpack_from("<H", body)[0]
        pos += length
    return out


ERECT_DOMAIN = _x224(b"\x04\x01\x00\x01\x00")
ATTACH_USER = _x224(b"\x28")
DISCONNECT_ULTIMATUM = _x224(b"\x21\x80")


def channel_join(user_id: int, channel: int) -> bytes:
    return _x224(struct.pack(">BHH", 0x38, user_id - 1001, channel))


def send_data(user_id: int, channel: int, data: bytes) -> bytes:
    return _x224(struct.pack(">BHHBH", 0x64, user_id - 1001, channel, 0x70,
                             0x8000 | len(data)) + data)


def _mcs(payload: bytes) -> tuple:
    """(MCS PDU kind, channel, data); data is the whole PDU for non-data kinds."""
    _check(payload, "the RDP server sent an empty MCS packet")
    kind = payload[0] >> 2
    if kind not in (25, 26):
        return kind, 0, payload
    _check(len(payload) >= 7, "the RDP server sent a truncated MCS packet")
    channel = payload[3] << 8 | payload[4]
    pos = 6
    length = payload[pos]
    if length & 0x80:
        _check(len(payload) >= 8, "the RDP server sent a truncated MCS packet")
        length = (length & 0x7F) << 8 | payload[pos + 1]
        pos += 2
    else:
        pos += 1
    data = payload[pos:pos + length]
    _check(len(data) == length, "the RDP server sent a truncated MCS packet")
    return kind, channel, data


MCS_ATTACH_USER_CONFIRM = 11
MCS_CHANNEL_JOIN_CONFIRM = 15
MCS_DISCONNECT_ULTIMATUM = 8
MCS_SEND_DATA_INDICATION = 26


# --------------------------------------------------------------------------
# Client info, licensing, capabilities, finalization
# --------------------------------------------------------------------------

def _timezone() -> bytes:
    """TS_TIME_ZONE_INFORMATION: this node's standard offset, no rules."""
    bias = int(time.timezone // 60)
    return (struct.pack("<i", bias) + b"\x00" * 64 + b"\x00" * 16 + struct.pack("<i", 0) +
            b"\x00" * 64 + b"\x00" * 16 + struct.pack("<i", 0))


def client_info(domain: str, user: str, password: str, address: str = "") -> bytes:
    flags = (INFO_MOUSE | INFO_DISABLECTRLALTDEL | INFO_UNICODE | INFO_MAXIMIZESHELL |
             INFO_LOGONNOTIFY | INFO_ENABLEWINDOWSKEY | INFO_LOGONERRORS |
             INFO_MOUSE_HAS_WHEEL | INFO_NOAUDIOPLAYBACK)
    if user:
        flags |= INFO_AUTOLOGON
    domain_raw, user_raw, password_raw = _utf16(domain), _utf16(user), _utf16(password)
    body = struct.pack("<IIHHHHH", 0, flags, len(domain_raw), len(user_raw),
                       len(password_raw), 0, 0)
    for raw in (domain_raw, user_raw, password_raw, b"", b""):
        body += raw + b"\x00\x00"
    address_raw = _utf16(address) + b"\x00\x00"
    directory = _utf16("C:\\Windows\\System32\\mstscax.dll") + b"\x00\x00"
    body += (struct.pack("<HH", 2, len(address_raw)) + address_raw +
             struct.pack("<H", len(directory)) + directory + _timezone() +
             struct.pack("<IIH", 0, PERFORMANCE_FLAGS, 0))
    return struct.pack("<HH", SEC_INFO_PKT, 0) + body


LICENSE_REQUEST = 0x01
LICENSE_ERROR_ALERT = 0xFF
STATUS_VALID_CLIENT = 0x00000007

_LICENSE_ERRORS = {
    0x01: "the server's licensing certificate is invalid",
    0x02: "the server has no Remote Desktop license to give this connection",
    0x03: "the licensing exchange failed its integrity check",
    0x04: "the license does not cover this server",
    0x06: "the server could not reach its Remote Desktop license server",
    0x08: "the license server rejected this client",
    0x0B: "the license server rejected this client's product",
    0x0C: "the licensing exchange was malformed",
}


def _caps(*sets) -> bytes:
    return b"".join(struct.pack("<HH", kind, len(body) + 4) + body for kind, body in sets)


def client_capabilities(width: int, height: int, server_caps: dict) -> list:
    """What Puppy can draw: plain bitmaps, and nothing a cache would need.

    No drawing orders, no caches, no codecs: every screen change then arrives
    as a bitmap, which is the one thing the decoder below understands.
    """
    sets = [
        (1, struct.pack("<HHHHHHHHHBB", 1, 3, 0x200, 0, 0,
                        0x0001 | 0x0004 | 0x0010 | 0x0400, 0, 0, 0, 1, 1)),
        (2, struct.pack("<HHHHHHHHHBBHH", 24, 1, 1, 1, width, height, 0, 1, 1,
                        0, 0, 1, 0)),
        (3, b"\x00" * 16 + struct.pack("<IHHHHHH", 0, 1, 20, 0, 1, 0, 0x002A) +
         b"\x00" * 32 + struct.pack("<HHIIHHHH", 0, 0, 0, 230400, 0, 0, 0, 0)),
        (19, struct.pack("<HBB", 0, 0, 0) + b"\x00" * 32),       # bitmap cache v2
        (8, struct.pack("<HHH", 1, 20, 21)),                     # pointer
        (13, struct.pack("<HHIIII", INPUT_FLAG_SCANCODES | INPUT_FLAG_MOUSEX |
                         INPUT_FLAG_FASTPATH_INPUT | INPUT_FLAG_UNICODE |
                         INPUT_FLAG_FASTPATH_INPUT2 | TS_INPUT_FLAG_MOUSE_HWHEEL,
                         0, 0x409, 4, 0, 12) + b"\x00" * 64),
        (15, struct.pack("<I", 0)),                              # brush
        (16, b"\x00" * 40 + struct.pack("<IHH", 0, 0, 0)),       # glyph cache: none
        (17, struct.pack("<IHH", 0, 0, 0)),                      # offscreen cache
        (20, struct.pack("<II", 0, 1600)),                       # virtual channels
        (12, struct.pack("<HH", 0, 0)),                          # sound
        (9, struct.pack("<HH", 0, 0)),                           # share
        (14, struct.pack("<HH", 1, 0)),                          # font
        (5, struct.pack("<HHHH", 0, 0, 2, 2)),                   # control
        (10, struct.pack("<HH", 6, 0)),                          # colour cache
        (7, struct.pack("<HHHH", 0, 0, 0, 0)),                   # window activation
    ]
    if 26 in server_caps:
        sets.append((26, struct.pack("<I", MAX_REQUEST_SIZE)))
    return sets


def parse_demand_active(body: bytes) -> tuple:
    _check(len(body) >= 12, "the server sent a truncated Demand Active PDU")
    share_id, source_length, _caps_length = struct.unpack_from("<IHH", body)
    pos = 8 + source_length
    _check(pos + 4 <= len(body), "the server sent a truncated Demand Active PDU")
    count = struct.unpack_from("<H", body, pos)[0]
    pos += 4
    caps = {}
    for _ in range(count):
        _check(pos + 4 <= len(body), "the server sent truncated capabilities")
        kind, length = struct.unpack_from("<HH", body, pos)
        _check(length >= 4 and pos + length <= len(body),
               "the server sent malformed capabilities")
        caps[kind] = body[pos + 4:pos + length]
        pos += length
    return share_id, caps


def confirm_active(share_id: int, user_id: int, sets: list) -> bytes:
    source = b"MSTSC\x00"
    capabilities = _caps(*sets)
    body = (struct.pack("<IHHH", share_id, 0x03EA, len(source), len(capabilities) + 4) +
            source + struct.pack("<HH", len(sets), 0) + capabilities)
    return struct.pack("<HHH", len(body) + 6, PDUTYPE_CONFIRMACTIVE | 0x10, user_id) + body


def share_data(share_id: int, user_id: int, kind: int, payload: bytes) -> bytes:
    return (struct.pack("<HHH", len(payload) + 18, PDUTYPE_DATA | 0x10, user_id) +
            struct.pack("<IBBHBBH", share_id, 0, STREAM_LOW, len(payload), kind, 0, 0) +
            payload)


# --------------------------------------------------------------------------
# Bitmaps: Interleaved RLE and uncompressed, to RGBA
# --------------------------------------------------------------------------

_FGBG_TABLES = {}


def _fgbg_table(foreground: bytes) -> list:
    """Eight pixels per mask byte (least significant bit first), by doubling.

    Two 16-entry nibble tables make the 256 patterns, so a new foreground
    colour costs a few hundred concatenations and a mask byte costs one lookup.
    """
    table = _FGBG_TABLES.get(foreground)
    if table is None:
        zero = b"\x00" * len(foreground)
        nibbles = [b"".join(foreground if value >> bit & 1 else zero
                            for bit in range(4)) for value in range(16)]
        table = [nibbles[mask & 15] + nibbles[mask >> 4] for mask in range(256)]
        if len(_FGBG_TABLES) >= 256:
            _FGBG_TABLES.clear()
        _FGBG_TABLES[foreground] = table
    return table


def _xor_above(out: bytearray, pos: int, count: int, row: int, pattern: bytes) -> None:
    """out[pos + k] = out[pos + k - row] ^ pattern[k], one row at a time.

    A chunk never reaches past ``row`` bytes, so what it reads was written
    before it - including by this same run, as the format requires.
    """
    done = 0
    while done < count:
        chunk = min(row, count - done)
        start = pos + done
        above = int.from_bytes(out[start - row:start - row + chunk], "little")
        value = above ^ int.from_bytes(pattern[done:done + chunk], "little")
        out[start:start + chunk] = value.to_bytes(chunk, "little")
        done += chunk


def _copy_above(out: bytearray, pos: int, count: int, row: int) -> None:
    done = 0
    while done < count:
        chunk = min(row, count - done)
        start = pos + done
        out[start:start + chunk] = out[start - row:start - row + chunk]
        done += chunk


def rle_decode(src: bytes, width: int, height: int, pixel: int) -> bytearray:
    """Interleaved RLE (MS-RDPBCGR 2.2.9.1.1.3.1.2.4) into bottom-up rows.

    ``pixel`` is 2 (15 and 16 bits) or 3 (24 bits).  Orders are applied as
    whole runs: fills and colour runs are byte multiplications, a background
    run copies the row above, foreground runs and images XOR against it.
    Whether an order is on the first line is decided where it starts, which is
    how Windows encodes it.
    """
    row = width * pixel
    out = bytearray(row * height)
    end = len(out)
    white = b"\xff" * pixel
    foreground = white
    insert = False
    first = True
    pos = 0
    index = 0
    size = len(src)
    while index < size:
        if first and pos >= row:
            first = False
            insert = False
        header = src[index]
        if header & 0xC0 != 0xC0:
            code = header >> 5
        elif header & 0xF0 == 0xF0:
            code = header
        else:
            code = header >> 4
        if code in (0xF9, 0xFA, 0xFD, 0xFE):
            index += 1
            length = 8 if code in (0xF9, 0xFA) else 1
        elif code >= 0xF0:
            _check(index + 3 <= size, "truncated RLE order")
            length = src[index + 1] | src[index + 2] << 8
            index += 3
        elif code in (0, 1, 3, 4):
            length = header & 0x1F
            if length:
                index += 1
            else:
                _check(index + 2 <= size, "truncated RLE order")
                length = src[index + 1] + 32
                index += 2
        elif code == 2:
            length = header & 0x1F
            if length:
                length *= 8
                index += 1
            else:
                _check(index + 2 <= size, "truncated RLE order")
                length = src[index + 1] + 1
                index += 2
        elif code in (0x0C, 0x0E):
            length = header & 0x0F
            if length:
                index += 1
            else:
                _check(index + 2 <= size, "truncated RLE order")
                length = src[index + 1] + 16
                index += 2
        elif code == 0x0D:
            length = header & 0x0F
            if length:
                length *= 8
                index += 1
            else:
                _check(index + 2 <= size, "truncated RLE order")
                length = src[index + 1] + 1
                index += 2
        else:
            raise RdpError("invalid RLE order 0x{:02X}".format(header))

        if code in (0x00, 0xF0):                     # background run
            if insert:
                _check(length >= 1 and pos + pixel <= end, "RLE run overflows its bitmap")
                if first:
                    out[pos:pos + pixel] = foreground
                else:
                    _xor_above(out, pos, pixel, row, foreground)
                pos += pixel
                length -= 1
            count = length * pixel
            _check(pos + count <= end, "RLE run overflows its bitmap")
            if not first:
                _copy_above(out, pos, count, row)
            pos += count                             # first line: black, already zero
            insert = True
            continue
        insert = False

        if code in (0x01, 0xF1, 0x0C, 0xF6):         # foreground runs
            if code in (0x0C, 0xF6):
                _check(index + pixel <= size, "truncated RLE colour")
                foreground = bytes(src[index:index + pixel])
                index += pixel
            count = length * pixel
            _check(pos + count <= end, "RLE run overflows its bitmap")
            if first:
                out[pos:pos + count] = foreground * length
            else:
                _xor_above(out, pos, count, row, foreground * length)
            pos += count
        elif code in (0x0E, 0xF8):                   # dithered run
            _check(index + 2 * pixel <= size, "truncated RLE colours")
            pair = bytes(src[index:index + 2 * pixel])
            index += 2 * pixel
            count = 2 * length * pixel
            _check(pos + count <= end, "RLE run overflows its bitmap")
            out[pos:pos + count] = pair * length
            pos += count
        elif code in (0x03, 0xF3):                   # colour run
            _check(index + pixel <= size, "truncated RLE colour")
            colour = bytes(src[index:index + pixel])
            index += pixel
            count = length * pixel
            _check(pos + count <= end, "RLE run overflows its bitmap")
            out[pos:pos + count] = colour * length
            pos += count
        elif code in (0x02, 0xF2, 0x0D, 0xF7, 0xF9, 0xFA):   # foreground/background
            if code in (0x0D, 0xF7):
                _check(index + pixel <= size, "truncated RLE colour")
                foreground = bytes(src[index:index + pixel])
                index += pixel
            table = _fgbg_table(foreground)
            if code == 0xF9:
                pattern = table[0x03]
            elif code == 0xFA:
                pattern = table[0x05]
            else:
                masks = (length + 7) // 8
                _check(index + masks <= size, "truncated RLE mask")
                pattern = b"".join(map(table.__getitem__, src[index:index + masks]))
                index += masks
            count = length * pixel
            _check(pos + count <= end, "RLE image overflows its bitmap")
            if first:
                out[pos:pos + count] = pattern[:count]
            else:
                _xor_above(out, pos, count, row, pattern)
            pos += count
        elif code in (0x04, 0xF4):                   # colour image
            count = length * pixel
            _check(index + count <= size and pos + count <= end,
                   "RLE image overflows its bitmap")
            out[pos:pos + count] = src[index:index + count]
            index += count
            pos += count
        elif code == 0xFD:                           # white
            _check(pos + pixel <= end, "RLE run overflows its bitmap")
            out[pos:pos + pixel] = white
            pos += pixel
        elif code == 0xFE:                           # black
            _check(pos + pixel <= end, "RLE run overflows its bitmap")
            out[pos:pos + pixel] = b"\x00" * pixel
            pos += pixel
        else:
            raise RdpError("invalid RLE order 0x{:02X}".format(header))
    return out


def _expand(bits: int) -> bytes:
    return bytes(((value << (8 - bits)) | (value >> (2 * bits - 8))) & 0xFF
                 for value in range(1 << bits))


_EXPAND5 = _expand(5)
_EXPAND6 = _expand(6)
# Translation tables from one byte of a little-endian 16-bit pixel to a part
# of a channel, so a whole row converts in a few C-level passes.
_565_R = bytes(_EXPAND5[high >> 3] for high in range(256))
_565_B = bytes(_EXPAND5[low & 0x1F] for low in range(256))
_565_G_LOW = bytes(low >> 5 for low in range(256))
_565_G_HIGH = bytes((high & 0x07) << 3 for high in range(256))
_555_R = bytes(_EXPAND5[(high >> 2) & 0x1F] for high in range(256))
_555_G_HIGH = bytes((high & 0x03) << 3 for high in range(256))
_EXPAND5_TABLE = bytes(_EXPAND5[value & 0x1F] for value in range(256))
_EXPAND6_TABLE = bytes(_EXPAND6[value & 0x3F] for value in range(256))


def _or_bytes(first: bytes, second: bytes) -> bytes:
    return (int.from_bytes(first, "little") | int.from_bytes(second, "little")).to_bytes(
        len(first), "little")


def to_rgba(raw: bytes, bpp: int) -> bytearray:
    """Top-down BGR24, RGB565 or RGB555 pixels as RGBA, opaque."""
    pixel = 3 if bpp == 24 else 2
    count = len(raw) // pixel
    out = bytearray(b"\xff" * (count * 4))
    if bpp == 24:
        out[0::4] = raw[2::3]
        out[1::4] = raw[1::3]
        out[2::4] = raw[0::3]
        return out
    low, high = raw[0::2], raw[1::2]
    if bpp == 16:
        out[0::4] = high.translate(_565_R)
        out[1::4] = _or_bytes(low.translate(_565_G_LOW),
                              high.translate(_565_G_HIGH)).translate(_EXPAND6_TABLE)
    else:
        out[0::4] = high.translate(_555_R)
        out[1::4] = _or_bytes(low.translate(_565_G_LOW),
                              high.translate(_555_G_HIGH)).translate(_EXPAND5_TABLE)
    out[2::4] = low.translate(_565_B)
    return out


def decode_bitmap(data: bytes, width: int, height: int, bpp: int, compressed: bool,
                  visible_width: int, visible_height: int) -> bytearray:
    """One TS_BITMAP_DATA as top-down RGBA, cut to what lands on the screen."""
    if bpp not in (15, 16, 24):
        raise RdpError("the server sent {}-bit bitmaps, which Puppy did not ask "
                       "for".format(bpp))
    pixel = 3 if bpp == 24 else 2
    if compressed:
        raw = rle_decode(data, width, height, pixel)
        stride = width * pixel
    else:
        stride = width * pixel
        if len(data) != stride * height:
            stride = (stride + 3) & ~3              # DIB rows pad to 4 bytes
        _check(len(data) >= stride * height, "the server sent a truncated bitmap")
        raw = data
    cut = visible_width * pixel
    # Bitmaps are bottom-up: the visible top row is the bitmap's top row.
    rows = [raw[(height - 1 - row) * stride:(height - 1 - row) * stride + cut]
            for row in range(visible_height)]
    return to_rgba(b"".join(rows), bpp)


# --------------------------------------------------------------------------
# Keyboard
# --------------------------------------------------------------------------

# Keysyms (the console's and the agent's shared vocabulary) to scan codes and
# their extended-key flag.
_SCANCODES = {
    0xFF08: (0x0E, False), 0xFF09: (0x0F, False), 0xFF0D: (0x1C, False),
    0xFF1B: (0x01, False), 0xFF63: (0x52, True), 0xFFFF: (0x53, True),
    0xFF50: (0x47, True), 0xFF57: (0x4F, True), 0xFF55: (0x49, True),
    0xFF56: (0x51, True), 0xFF51: (0x4B, True), 0xFF52: (0x48, True),
    0xFF53: (0x4D, True), 0xFF54: (0x50, True), 0xFFE1: (0x2A, False),
    0xFFE2: (0x36, False), 0xFFE3: (0x1D, False), 0xFFE4: (0x1D, True),
    0xFFE9: (0x38, False), 0xFFEA: (0x38, True), 0xFFEB: (0x5B, True),
    0xFFEC: (0x5C, True), 0xFFE5: (0x3A, False), 0xFF7F: (0x45, False),
    0xFF14: (0x46, False), 0xFF67: (0x5D, True), 0xFF61: (0x37, True),
    0xFE03: (0x38, True), 0xFF8D: (0x1C, True),
}
for _index in range(10):
    _SCANCODES[0xFFBE + _index] = (0x3B + _index, False)
_SCANCODES[0xFFC8] = (0x57, False)
_SCANCODES[0xFFC9] = (0x58, False)
KEY_PAUSE = 0xFF13

# With Control, Alt or the Windows key held a character is a shortcut, which
# Windows reads from the key rather than the text: the US positions here.
_US_KEYS = {" ": 0x39}
for _plain, _shifted, _first in (("1234567890-=", "!@#$%^&*()_+", 0x02),
                                 ("qwertyuiop[]", "QWERTYUIOP{}", 0x10),
                                 ("asdfghjkl;'`", "ASDFGHJKL:\"~", 0x1E),
                                 ("\\zxcvbnm,./", "|ZXCVBNM<>?", 0x2B)):
    for _offset, (_a, _b) in enumerate(zip(_plain, _shifted)):
        _US_KEYS[_a] = _US_KEYS[_b] = _first + _offset

_US_SHIFTED = frozenset("!@#$%^&*()_+QWERTYUIOP{}ASDFGHJKL:\"~|ZXCVBNM<>?")
_SHORTCUT_KEYSYMS = {0xFFE3, 0xFFE4, 0xFFE9, 0xFFEB, 0xFFEC}
_ALTGR_KEYSYMS = {0xFFEA, 0xFE03}


def keysym_char(keysym: int) -> str:
    if 0x20 <= keysym <= 0x7E or 0xA0 <= keysym <= 0xFF:
        return chr(keysym)
    if 0x01000020 <= keysym <= 0x0110FFFF:
        return chr(keysym - 0x01000000)
    return ""


# --------------------------------------------------------------------------
# Input events
# --------------------------------------------------------------------------

def _fast_event(event: tuple) -> bytes:
    kind = event[0]
    if kind == "scan":
        return bytes(((0 << 5) | event[2], event[1]))
    if kind == "unicode":
        return bytes(((4 << 5) | event[2],)) + struct.pack("<H", event[1])
    if kind == "mouse":
        return bytes((1 << 5,)) + struct.pack("<HHH", event[1], event[2], event[3])
    return bytes(((3 << 5) | event[1],))                       # sync


def _slow_event(event: tuple) -> bytes:
    kind = event[0]
    if kind == "scan":
        flags = (0x8000 if event[2] & 1 else 0x4000) | \
            (0x0100 if event[2] & 2 else 0) | (0x0200 if event[2] & 4 else 0)
        return struct.pack("<IHHHH", 0, 0x0004, flags, event[1], 0)
    if kind == "unicode":
        return struct.pack("<IHHHH", 0, 0x0005, 0x8000 if event[2] else 0, event[1], 0)
    if kind == "mouse":
        return struct.pack("<IHHHH", 0, 0x8001, event[1], event[2], event[3])
    return struct.pack("<IHHI", 0, 0x0000, 0, event[1])


def fast_input(events: list) -> bytes:
    """One fast-path input PDU: the count rides the header up to 15 events
    and a byte of its own after the length beyond that, up to 255."""
    _check(1 <= len(events) <= 255, "a fast-path input PDU holds 1 to 255 events")
    body = b"".join(map(_fast_event, events))
    if len(events) <= 15:
        header, count = len(events) << 2, b""
    else:
        header, count = 0, bytes((len(events),))
    total = len(body) + len(count) + 2
    if total > 0x7F:
        total += 1
        length = struct.pack(">H", 0x8000 | total)
    else:
        length = bytes((total,))
    return bytes((header,)) + length + count + body


def slow_input(events: list) -> bytes:
    return struct.pack("<HH", len(events), 0) + b"".join(map(_slow_event, events))


def _wheel_flags(direction: str) -> int:
    notch = WHEEL_NOTCH
    if direction == "up":
        return PTRFLAGS_WHEEL | notch
    if direction == "down":
        return PTRFLAGS_WHEEL | PTRFLAGS_WHEEL_NEGATIVE | (0x100 - notch)
    if direction == "right":
        return PTRFLAGS_HWHEEL | notch
    return PTRFLAGS_HWHEEL | PTRFLAGS_WHEEL_NEGATIVE | (0x100 - notch)


def _client_name() -> str:
    name = socket.gethostname().split(".")[0].strip()
    return name[:15] or "Puppy"


def _sni(host: str):
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        return host


CREDSSP_VERSION = 6
MAX_TS_REQUEST = 1 << 20
MAX_BITMAP_PIXELS = 1 << 22


# --------------------------------------------------------------------------
# The connection
# --------------------------------------------------------------------------

class RdpInstance(vnc.VncInstance):
    """One identified Remote Desktop connection on the shared screen surface.

    Everything a viewer or an agent touches - the framebuffer, damage frames,
    the idle timer, screenshots, the agent's pointer and keyboard actions - is
    the VNC instance's own code.  This class supplies the wire beneath it.
    """

    kind = "RDP"
    protocol = "rdp"
    handshake_timeout = HANDSHAKE_TIMEOUT

    def __init__(self, vnc_id: str, host: str, port: int, password: str = "",
                 view_only: bool = False, label: str = "", origin: str = "user",
                 username: str = "", width: int = DEFAULT_WIDTH,
                 height: int = DEFAULT_HEIGHT):
        super().__init__(vnc_id, host, port, password, view_only, label, origin)
        self.username = str(username or "")
        self.desktop_width = int(width or DEFAULT_WIDTH)
        self.desktop_height = int(height or DEFAULT_HEIGHT)
        self.wire = None
        self.security = ""
        self.certificate = ""
        self.server = ""
        self.user_id = 0
        self.io_channel = 1003
        self.share_id = 0
        self.server_source = 1002
        self.activated = False
        self.fastpath_input = False
        self.unicode_input = True
        self.hwheel = False
        self.refresh_supported = True
        self.suppress_supported = False
        self.display_on = True
        self.attention_until = 0.0
        self.attention_handle = None
        self.server_error = 0
        self.fragment = None
        self.fragment_code = -1
        self.damage = []
        self.pending_input = []
        self.modifiers = set()

    def payload(self) -> dict:
        data = super().payload()
        data.update({"username": self.username, "security": self.security,
                     "certificate": self.certificate, "server": self.server})
        return data

    # ---- connection sequence ----

    def _open_stream(self):
        self.wire = _Wire(self.reader, self.writer)
        return self.wire

    async def _handshake(self) -> None:
        # Whoever asked for this connection is about to look at it; when they
        # do not, the timer lets the output be suppressed.
        self._attend()
        self.server_error = 0
        wire = self.wire
        user, domain = split_username(self.username)
        credentials = bool(user and self.password)
        requested = PROTOCOL_SSL | (PROTOCOL_HYBRID | PROTOCOL_HYBRID_EX
                                    if credentials else 0)
        wire.send(connection_request(requested, user))
        await self.writer.drain()
        try:
            reply = await wire.read_pdu()
        except RdpError:
            raise RdpError("{}:{} is not a Remote Desktop server, or it refused "
                           "this client".format(self.host, self.port))
        selected = parse_connection_confirm(reply)
        if not selected & (PROTOCOL_SSL | PROTOCOL_HYBRID | PROTOCOL_HYBRID_EX):
            raise RdpError("the server chose legacy RDP security, which Puppy "
                           "never uses; enable TLS or Network Level Authentication")
        await wire.start_tls(_sni(self.host))
        certificate = wire.certificate()
        self.certificate = fingerprint(certificate) if certificate else ""
        self.name = certificate_name(certificate) if certificate else ""
        if selected & (PROTOCOL_HYBRID | PROTOCOL_HYBRID_EX):
            await self._credssp(certificate, user, domain,
                                bool(selected & PROTOCOL_HYBRID_EX))
            self.security = "NLA"
        else:
            self.security = "TLS"
        await self._join(selected)
        wire.send(send_data(self.user_id, self.io_channel,
                            client_info(domain, user, self.password,
                                        self._local_address())))
        await self.writer.drain()
        first = await self._licensing()
        if first is not None:
            self._share_pdus(first)
        while not self.activated:
            await self._next_pdu()

    def _local_address(self) -> str:
        try:
            return str(self.writer.get_extra_info("sockname")[0])
        except Exception:
            return ""

    async def _ts_request(self) -> dict:
        wire = self.wire
        await self.writer.drain()
        await wire.need(2)
        tag, length = wire.buf[wire.pos], wire.buf[wire.pos + 1]
        _check(tag == 0x30, "the server sent an unexpected sign-in message")
        if length & 0x80:
            count = length & 0x7F
            _check(1 <= count <= 3, "the server sent a malformed sign-in message")
            await wire.need(2 + count)
            total = 2 + count + int.from_bytes(
                wire.buf[wire.pos + 2:wire.pos + 2 + count], "big")
        else:
            total = 2 + length
        _check(total <= MAX_TS_REQUEST, "the server sent an oversized sign-in message")
        return parse_ts_request(await wire.read(total))

    async def _credssp(self, certificate: bytes, user: str, domain: str,
                       early: bool) -> None:
        """Network Level Authentication: NTLMv2 inside CredSSP, over the TLS.

        The password leaves this process only at the end, sealed, and only
        after the server proved it holds both the password's key and the very
        TLS key this connection is encrypted with.
        """
        wire = self.wire
        public_key = subject_public_key(certificate)
        ntlm = Ntlm(user, domain, self.password, workstation=_client_name())
        nonce = os.urandom(32)
        try:
            wire.send(ts_request(CREDSSP_VERSION, nego=ntlm.negotiate(), nonce=nonce))
            reply = await self._ts_request()
            if reply["error"]:
                raise RdpError(sign_in_refusal(reply["error"]))
            _check(reply["nego"], "the server did not answer the sign-in")
            peer = reply["version"]
            authenticate = ntlm.authenticate(_spnego_token(reply["nego"]))
            self.server = ntlm.server_version
            if ntlm.server_dns or ntlm.server_name:
                self.name = (ntlm.server_dns or ntlm.server_name)[:vnc.MAX_LABEL]
            binding = hashlib.sha256(_CLIENT_BINDING + nonce + public_key).digest() \
                if peer >= 5 else public_key
            wire.send(ts_request(CREDSSP_VERSION, nego=authenticate,
                                 pub_key_auth=ntlm.seal(binding), nonce=nonce))
            reply = await self._ts_request()
        except _Closed:
            raise RdpError("the server ended the connection during sign-in, which "
                           "usually means it rejected the user name or password")
        if reply["error"]:
            raise RdpError(sign_in_refusal(reply["error"]))
        _check(reply["pub_key_auth"], "the server did not confirm the sign-in")
        echo = ntlm.unseal(reply["pub_key_auth"])
        expected = hashlib.sha256(_SERVER_BINDING + nonce + public_key).digest() \
            if peer >= 5 else _increment_le(public_key)
        if not hmac.compare_digest(echo, expected):
            raise RdpError("the server could not prove it owns the TLS certificate "
                           "it presented, so the password was not sent")
        wire.send(ts_request(CREDSSP_VERSION, nonce=nonce, auth_info=ntlm.seal(
            ts_credentials(domain, user, self.password))))
        await self.writer.drain()
        if early:
            try:
                result = struct.unpack("<I", await wire.read(4))[0]
            except _Closed:
                raise RdpError("the server ended the connection after sign-in")
            if result == 5:
                raise RdpError("Windows accepted the password, but this account is "
                               "not allowed to sign in over Remote Desktop")
            if result:
                raise RdpError("the server refused this account (authorization "
                               "result {})".format(result))

    async def _slow_pdu(self) -> tuple:
        """(MCS kind, channel, data) of the next packet, before activation."""
        pdu = await self.wire.read_pdu()
        _check(pdu[0] & 3 == 3, "the server sent screen updates before it was ready")
        kind, channel, data = _mcs(_x224_payload(pdu))
        if kind == MCS_DISCONNECT_ULTIMATUM:
            raise RdpError(self._end_reason("the server ended the connection"))
        return kind, channel, data

    async def _join(self, selected: int) -> None:
        wire = self.wire
        width = max(MIN_DIMENSION, min(MAX_DESKTOP, self.desktop_width))
        height = max(MIN_DIMENSION, min(MAX_DESKTOP, self.desktop_height))
        wire.send(connect_initial(client_data(width, height, _client_name(), selected)))
        await self.writer.drain()
        server = parse_server_data(parse_connect_response(
            _x224_payload(await wire.read_pdu())))
        if server["encryption"]:
            raise RdpError("the server insists on legacy RDP encryption inside TLS, "
                           "which Puppy does not speak")
        self.io_channel = server["io_channel"]
        wire.send(ERECT_DOMAIN)
        wire.send(ATTACH_USER)
        await self.writer.drain()
        kind, _channel, data = await self._slow_pdu()
        _check(kind == MCS_ATTACH_USER_CONFIRM and len(data) >= 4 and data[1] == 0,
               "the server refused to attach this client")
        self.user_id = 1001 + (data[2] << 8 | data[3])
        channels = [self.user_id, self.io_channel] + server["channels"]
        if server["message_channel"]:
            channels.append(server["message_channel"])
        for channel in channels:
            wire.send(channel_join(self.user_id, channel))
            await self.writer.drain()
            kind, _channel, data = await self._slow_pdu()
            _check(kind == MCS_CHANNEL_JOIN_CONFIRM and len(data) >= 2 and data[1] == 0,
                   "the server refused to open channel {}".format(channel))

    async def _licensing(self):
        """Wait out the licensing phase; return a PDU that already follows it."""
        while True:
            kind, _channel, data = await self._slow_pdu()
            if kind != MCS_SEND_DATA_INDICATION:
                continue
            if len(data) < 4 or not struct.unpack_from("<H", data)[0] & SEC_LICENSE_PKT:
                return data                    # no licensing at all: activation
            _check(len(data) >= 8, "the server sent a truncated licensing message")
            message = data[4]
            if message == LICENSE_ERROR_ALERT:
                _check(len(data) >= 16, "the server sent a truncated licensing message")
                code, _transition = struct.unpack_from("<II", data, 8)
                if code == STATUS_VALID_CLIENT:
                    return None
                raise RdpError(_LICENSE_ERRORS.get(
                    code, "the server's licensing failed (error {})".format(code)))
            if message == LICENSE_REQUEST:
                raise RdpError("this server issues Remote Desktop client licenses, "
                               "which Puppy cannot request")
            raise RdpError("the server sent an unexpected licensing message")

    # ---- the PDU stream ----

    async def _next_pdu(self) -> None:
        pdu = await self.wire.read_pdu()
        if pdu[0] & 3 != 3:
            self._fast_path(pdu)
            return
        kind, channel, data = _mcs(_x224_payload(pdu))
        if kind == MCS_DISCONNECT_ULTIMATUM:
            raise _Closed(self._end_reason("the server ended the session"))
        if kind == MCS_SEND_DATA_INDICATION and channel == self.io_channel:
            self._share_pdus(data)

    def _end_reason(self, default: str) -> str:
        if self.server_error:
            return _ERROR_INFO.get(
                self.server_error, "the server ended the session (error 0x{:08X})".format(
                    self.server_error))
        return default

    def _share_pdus(self, data: bytes) -> None:
        pos = 0
        size = len(data)
        while pos + 4 <= size:
            total = data[pos] | data[pos + 1] << 8
            if total == 0x8000:                     # flow control, 8 bytes
                pos += 8
                continue
            _check(total >= 4 and pos + total <= size,
                   "the server sent a malformed share PDU")
            kind = (data[pos + 2] | data[pos + 3] << 8) & 0x0F
            source = data[pos + 4] | data[pos + 5] << 8 if total >= 6 else 0
            body = data[pos + 6:pos + total]
            if kind == PDUTYPE_DATA:
                self._data_pdu(body)
            elif kind == PDUTYPE_DEMANDACTIVE:
                self._demand_active(body, source)
            elif kind == PDUTYPE_DEACTIVATEALL:
                self.activated = False
            elif kind == PDUTYPE_SERVER_REDIRECT:
                raise RdpError("the server redirected this connection to another "
                               "server, which Puppy does not follow")
            pos += total

    def _data_pdu(self, body: bytes) -> None:
        _check(len(body) >= 12, "the server sent a truncated data PDU")
        kind = body[8]
        if body[9] & 0x20:
            raise RdpError("the server compressed its output, which Puppy did not "
                           "ask for")
        payload = body[12:]
        if kind == PDUTYPE2_UPDATE:
            if len(payload) >= 2 and payload[0] | payload[1] << 8 == UPDATETYPE_BITMAP:
                self._bitmap_update(payload)
        elif kind == PDUTYPE2_SET_ERROR_INFO and len(payload) >= 4:
            code = struct.unpack_from("<I", payload)[0]
            if code:
                self.server_error = code

    def _demand_active(self, body: bytes, source: int) -> None:
        share_id, caps = parse_demand_active(body)
        self.share_id = share_id
        self.server_source = source or 1002
        general = caps.get(1, b"")
        if len(general) >= 20:
            self.refresh_supported = bool(general[18])
            self.suppress_supported = bool(general[19])
        width, height = self.desktop_width, self.desktop_height
        bitmap = caps.get(2, b"")
        if len(bitmap) >= 12:
            width, height = struct.unpack_from("<HH", bitmap, 8)
        _check(1 <= width <= vnc.MAX_DIMENSION and 1 <= height <= vnc.MAX_DIMENSION,
               "the server announced an unusable desktop size")
        input_caps = caps.get(13, b"")
        flags = struct.unpack_from("<H", input_caps)[0] if len(input_caps) >= 2 else 0
        self.fastpath_input = bool(flags & (INPUT_FLAG_FASTPATH_INPUT |
                                            INPUT_FLAG_FASTPATH_INPUT2))
        self.unicode_input = bool(flags & INPUT_FLAG_UNICODE)
        self.hwheel = bool(flags & TS_INPUT_FLAG_MOUSE_HWHEEL)
        resized = (width, height) != (self.width, self.height)
        if resized:
            self._resize(width, height)
        wire = self.wire
        wire.send(send_data(self.user_id, self.io_channel, confirm_active(
            share_id, self.user_id, client_capabilities(width, height, caps))))
        for kind, payload in (
                (PDUTYPE2_SYNCHRONIZE, struct.pack("<HH", 1, self.server_source)),
                (PDUTYPE2_CONTROL, struct.pack("<HHI", 4, 0, 0)),      # cooperate
                (PDUTYPE2_CONTROL, struct.pack("<HHI", 1, 0, 0)),      # request control
                (PDUTYPE2_FONTLIST, struct.pack("<HHHH", 0, 0, 3, 50))):
            self._send_share(kind, payload)
        self.activated = True
        self.display_on = True
        self.fragment = None
        # Every lock key off, whatever the last session left behind.
        self.pending_input = [("sync", 0)]
        self._flush_input()
        if resized and self.connected:
            self._broadcast_json({"type": "size", "width": width, "height": height})
            vnc._state_changed()
        self._sync_display()

    def _send_share(self, kind: int, payload: bytes) -> None:
        self.wire.send(send_data(self.user_id, self.io_channel, share_data(
            self.share_id, self.user_id, kind, payload)))

    def _fast_path(self, pdu: bytes) -> None:
        if pdu[0] & 0xC0:
            raise RdpError("the server encrypted its output, which Puppy did not ask for")
        pos = 3 if pdu[1] & 0x80 else 2
        size = len(pdu)
        while pos < size:
            header = pdu[pos]
            pos += 1
            code = header & 0x0F
            fragment = header >> 4 & 0x03
            if header >> 6 & 0x02:
                _check(pos < size, "the server sent a truncated update")
                if pdu[pos] & 0x20:
                    raise RdpError("the server compressed its output, which Puppy "
                                   "did not ask for")
                pos += 1
            _check(pos + 2 <= size, "the server sent a truncated update")
            length = pdu[pos] | pdu[pos + 1] << 8
            pos += 2
            data = pdu[pos:pos + length]
            _check(len(data) == length, "the server sent a truncated update")
            pos += length
            if fragment != FASTPATH_FRAGMENT_SINGLE:
                if fragment == FASTPATH_FRAGMENT_FIRST:
                    self.fragment = bytearray(data)
                    self.fragment_code = code
                    continue
                if self.fragment is None or code != self.fragment_code:
                    self.fragment = None
                    continue
                self.fragment += data
                _check(len(self.fragment) <= MAX_REQUEST_SIZE,
                       "the server sent an oversized update")
                if fragment == FASTPATH_FRAGMENT_NEXT:
                    continue
                data = bytes(self.fragment)
                self.fragment = None
            if code == FASTPATH_UPDATETYPE_BITMAP:
                self._bitmap_update(data)

    def _bitmap_update(self, data: bytes) -> None:
        _check(len(data) >= 4, "the server sent a truncated bitmap update")
        update_type, count = struct.unpack_from("<HH", data)
        if update_type != UPDATETYPE_BITMAP:
            return
        pos = 4
        size = len(data)
        for _ in range(count):
            _check(pos + 18 <= size, "the server sent a truncated bitmap update")
            left, top, right, bottom, width, height, bpp, flags, length = \
                struct.unpack_from("<9H", data, pos)
            pos += 18
            compressed = bool(flags & BITMAP_COMPRESSION)
            if compressed and not flags & NO_BITMAP_COMPRESSION_HDR:
                _check(pos + 8 <= size, "the server sent a truncated bitmap update")
                length = struct.unpack_from("<H", data, pos + 2)[0]
                pos += 8
            body = data[pos:pos + length]
            _check(len(body) == length, "the server sent a truncated bitmap")
            pos += length
            self._paint(left, top, right, bottom, width, height, bpp, compressed, body)

    def _paint(self, left, top, right, bottom, width, height, bpp, compressed,
               body) -> None:
        if not width or not height or right < left or bottom < top:
            return
        _check(width * height <= MAX_BITMAP_PIXELS, "the server sent an oversized bitmap")
        visible_width = min(right - left + 1, width, self.width - left)
        visible_height = min(bottom - top + 1, height, self.height - top)
        if visible_width <= 0 or visible_height <= 0:
            return
        rgba = decode_bitmap(body, width, height, bpp, compressed,
                             visible_width, visible_height)
        self._blit(rgba, left, top, visible_width, visible_height)
        self.damage.append((left, top, visible_width, visible_height, rgba))

    def _flush_damage(self) -> None:
        """Count one applied update and hand its rectangles to the viewers."""
        damage, self.damage = self.damage, []
        if not damage:
            return
        self.frame_seq += 1
        self.frame_wake.set()
        self.frame_wake.clear()
        if not self.viewers:
            return
        head = vnc._FRAME_HEAD
        if len(damage) > vnc.MAX_UPDATE_RECTS:
            left = min(item[0] for item in damage)
            top = min(item[1] for item in damage)
            right = max(item[0] + item[2] for item in damage)
            bottom = max(item[1] + item[3] for item in damage)
            frames = [head.pack(vnc.FRAME_PIXELS, 0, left, top, right - left,
                                bottom - top) +
                      self._extract(left, top, right - left, bottom - top)]
        else:
            frames = [head.pack(vnc.FRAME_PIXELS, 0, x, y, w, h) + bytes(rgba)
                      for x, y, w, h, rgba in damage]
        self._publish(frames)

    async def _run(self) -> None:
        reason = "Connection closed"
        loop = asyncio.get_event_loop()
        try:
            await self._drain()
            started = loop.time()
            while self.connected:
                if not self.wire.pdu_ready():
                    self._flush_damage()
                    await self._drain()
                    started = loop.time()
                elif loop.time() - started > 0.05:
                    # A long burst of buffered updates must not starve the
                    # other sockets and turns this node serves.
                    self._flush_damage()
                    await asyncio.sleep(0)
                    started = loop.time()
                await self._next_pdu()
        except asyncio.CancelledError:
            raise
        except _Closed as exc:
            reason = self._end_reason(str(exc))
        except vnc.VncError as exc:
            reason = str(exc)
        except (OSError, asyncio.IncompleteReadError) as exc:
            reason = "RDP connection lost: {}".format(exc)
        except Exception:
            log.exception("RDP %s failed", self.vnc_id)
            reason = "RDP connection failed"
        self.task = None
        try:
            await self.disconnect(reason)
        except Exception:
            pass

    def _resize(self, width: int, height: int) -> None:
        if not 1 <= width <= vnc.MAX_DIMENSION or not 1 <= height <= vnc.MAX_DIMENSION:
            raise RdpError("the RDP server announced an unusable screen size")
        self.width = width
        self.height = height
        # Black until painted, as a Remote Desktop window is.
        self.frame = bytearray(b"\x00\x00\x00\xff" * (width * height))
        for viewer in self.viewers.values():
            viewer.invalidate()

    async def _close_socket(self) -> None:
        wire, self.wire = self.wire, None
        if wire is not None and self.writer is not None and self.user_id:
            try:
                wire.send(DISCONNECT_ULTIMATUM)
            except Exception:
                pass
        if self.attention_handle is not None:
            self.attention_handle.cancel()
            self.attention_handle = None
        self.activated = False
        self.user_id = 0
        self.fragment = None
        self.damage = []
        self.pending_input = []
        self.modifiers.clear()
        await super()._close_socket()

    # ---- who is looking ----

    def _wanted(self) -> bool:
        return self._streaming() or time.monotonic() < self.attention_until

    def _sync_display(self) -> None:
        """Suppress Output while nobody watches; ask for the screen when they do."""
        if not self.activated or self.wire is None or not self.suppress_supported:
            return
        want = self._wanted()
        if want == self.display_on:
            return
        self.display_on = want
        if want:
            payload = struct.pack("<B3xHHHH", 1, 0, 0, self.width - 1, self.height - 1)
        else:
            payload = struct.pack("<B3x", 0)
        self._send_share(PDUTYPE2_SUPPRESS_OUTPUT, payload)

    def _attend(self) -> None:
        self.attention_until = time.monotonic() + AGENT_ATTENTION
        if self.attention_handle is not None:
            self.attention_handle.cancel()
        try:
            self.attention_handle = asyncio.get_event_loop().call_later(
                AGENT_ATTENTION + 1, self._sync_display)
        except RuntimeError:
            self.attention_handle = None
        self._sync_display()

    def touch(self) -> None:
        super().touch()
        self._attend()

    def detach_viewer(self, ws) -> None:
        super().detach_viewer(ws)
        self._sync_display()

    def set_viewer_active(self, ws, active: bool) -> None:
        super().set_viewer_active(ws, active)
        self._sync_display()

    def _request_update(self, full: bool = False, force: bool = False) -> None:
        """RDP pushes every change itself; only who is watching matters."""
        if not self.connected or not self.activated or self.wire is None:
            return
        if force:
            self._attend()
        else:
            self._sync_display()

    def _refresh(self) -> None:
        if not self.activated or self.wire is None:
            return
        if not self.display_on:
            self._sync_display()
        elif self.refresh_supported:
            self._send_share(PDUTYPE2_REFRESH_RECT, struct.pack(
                "<B3xHHHH", 1, 0, 0, self.width - 1, self.height - 1))

    # ---- input ----

    def _event(self, event: tuple) -> None:
        self.pending_input.append(event)
        if len(self.pending_input) >= 60:
            self._flush_input()

    def _flush_input(self) -> None:
        events, self.pending_input = self.pending_input, []
        if not events or not self.activated or self.wire is None:
            return
        if self.fastpath_input:
            for start in range(0, len(events), 60):
                self.wire.send(fast_input(events[start:start + 60]))
        else:
            for start in range(0, len(events), 60):
                self._send_share(PDUTYPE2_INPUT, slow_input(events[start:start + 60]))

    async def _drain(self) -> None:
        try:
            self._flush_input()
        except Exception:
            pass
        await super()._drain()

    def _pointer(self, x: int, y: int, mask: int) -> None:
        mask &= 0x07
        changed = mask ^ (self.buttons & 0x07)
        if (x, y) != self.pointer or not changed:
            self._event(("mouse", PTRFLAGS_MOVE, x, y))
        self.pointer = (x, y)
        # RFB numbers the buttons left, middle, right; RDP left, right, middle.
        for bit, flag in ((1, PTRFLAGS_BUTTON1), (2, PTRFLAGS_BUTTON3),
                          (4, PTRFLAGS_BUTTON2)):
            if changed & bit:
                self._event(("mouse", flag | (PTRFLAGS_DOWN if mask & bit else 0), x, y))
        self.buttons = mask

    def _key(self, keysym: int, down: bool) -> None:
        if not keysym:
            return
        if keysym == KEY_PAUSE:
            if down:                                 # Pause has no release
                for event in (("scan", 0x1D, 4), ("scan", 0x45, 0),
                              ("scan", 0x1D, 5), ("scan", 0x45, 1)):
                    self._event(event)
            return
        release = 0 if down else 1
        scan = _SCANCODES.get(keysym)
        if scan is not None:
            if 0xFFE1 <= keysym <= 0xFFEC or keysym == 0xFE03:
                if down:
                    self.modifiers.add(keysym)
                else:
                    self.modifiers.discard(keysym)
            code, extended = scan
            self._event(("scan", code, release | (2 if extended else 0)))
            return
        char = keysym_char(keysym)
        if not char:
            return
        shortcut = bool(self.modifiers & _SHORTCUT_KEYSYMS) and \
            not self.modifiers & _ALTGR_KEYSYMS
        code = _US_KEYS.get(char)
        if code is not None and (shortcut or not self.unicode_input):
            # A shortcut takes the key as it is; only a server without
            # Unicode input needs Shift added for a capital or a symbol.
            shift = down and not shortcut and char in _US_SHIFTED and \
                not self.modifiers & {0xFFE1, 0xFFE2}
            if shift:
                self._event(("scan", 0x2A, 0))
            self._event(("scan", code, release))
            if shift:
                self._event(("scan", 0x2A, 1))
            return
        if not self.unicode_input:
            return
        units = char.encode("utf-16-le")
        for start in range(0, len(units), 2):
            self._event(("unicode", units[start] | units[start + 1] << 8, release))

    def _wheel(self, data: dict) -> None:
        try:
            dx = float(data.get("dx") or 0.0)
            dy = float(data.get("dy") or 0.0)
        except (TypeError, ValueError):
            return
        if dx != dx or dy != dy:
            return
        x, y = self._fb_point(data)
        if (x, y) != self.pointer:
            self._event(("mouse", PTRFLAGS_MOVE, x, y))
            self.pointer = (x, y)
        self.wheel_x += max(-4096.0, min(4096.0, dx))
        self.wheel_y += max(-4096.0, min(4096.0, dy))
        steps = []
        while self.wheel_y <= -vnc._WHEEL_STEP:
            self.wheel_y += vnc._WHEEL_STEP
            steps.append("up")
        while self.wheel_y >= vnc._WHEEL_STEP:
            self.wheel_y -= vnc._WHEEL_STEP
            steps.append("down")
        while self.wheel_x <= -vnc._WHEEL_STEP:
            self.wheel_x += vnc._WHEEL_STEP
            if self.hwheel:
                steps.append("left")
        while self.wheel_x >= vnc._WHEEL_STEP:
            self.wheel_x -= vnc._WHEEL_STEP
            if self.hwheel:
                steps.append("right")
        for direction in steps[:vnc._MAX_WHEEL_STEPS]:
            self._event(("mouse", _wheel_flags(direction), x, y))

    async def handle_client(self, data: dict, viewer_ws=None) -> None:
        if data.get("type") == "refresh":
            self._refresh()
            await self._drain()
            return
        await super().handle_client(data, viewer_ws)

    async def agent_scroll(self, x, y, direction: str = "down",
                           clicks: int = 3) -> tuple:
        """A wheel notch is one RDP wheel event of 120, either axis."""
        self._agent_writable()
        direction = str(direction or "").lower()
        if direction not in ("up", "down", "left", "right"):
            raise RdpError("scroll direction must be up, down, left or right")
        if direction in ("left", "right") and not self.hwheel:
            raise RdpError("this Remote Desktop server does not accept sideways "
                           "scrolling")
        clicks = vnc._agent_count(clicks, vnc.AGENT_MAX_WHEEL, "clicks")
        point = self._agent_point(x, y)
        self._pointer(point[0], point[1], self.buttons)
        for _ in range(clicks):
            self._event(("mouse", _wheel_flags(direction), point[0], point[1]))
        await self._drain()
        return point
