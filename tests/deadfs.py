#!/usr/bin/env python3
"""A file system that stops answering on command, for the suites.

``Mount(backing, point)`` mounts ``backing`` at ``point`` through a small FUSE
daemon written here against the kernel's own protocol - no library and no
helper program - in a process of its own, so that a test stuck on it cannot
take the daemon along. ``die()`` makes it hold every request without a reply,
which is what a network mount does when its server goes away: whoever touches
the mount waits in the kernel for as long as it lasts. ``revive()`` answers
what was held, ``close()`` aborts it and unmounts.

Needs root and /dev/fuse (``available()``); ``private_mounts()`` first gives the
process a mount namespace of its own, so nothing outside ever sees the mount
and nothing is left when the process ends.
"""
from __future__ import annotations

import ctypes
import errno
import os
import select
import signal
import stat
import struct
import subprocess
import sys

CLONE_NEWNS = 0x00020000
PR_SET_PDEATHSIG = 1
MS_NOSUID, MS_NODEV, MS_REC, MS_PRIVATE = 2, 4, 16384, 1 << 18
MNT_DETACH = 2

_libc = ctypes.CDLL(None, use_errno=True)


def available() -> bool:
    return sys.platform.startswith("linux") and os.geteuid() == 0 and \
        os.path.exists("/dev/fuse")


def private_mounts() -> bool:
    """A mount namespace of this process's own. Call it before any thread is
    made: a namespace belongs to the thread that asks, and its children's."""
    if not available():
        return False
    if _libc.unshare(CLONE_NEWNS):
        return False
    return _libc.mount(b"none", b"/", None, MS_REC | MS_PRIVATE, None) == 0


class Mount:
    """The daemon's handle: mounted and answering once this returns."""

    def __init__(self, backing, point):
        self.backing, self.point = str(backing), str(point)
        os.makedirs(self.point, exist_ok=True)
        self.daemon = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), self.backing, self.point],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self._expect(b"mounted")
        self.dead = False

    def _expect(self, word):
        ready, _, _ = select.select([self.daemon.stdout], [], [], 10)
        line = self.daemon.stdout.readline() if ready else b""
        if line.strip() != word:
            raise RuntimeError("the test file system answered {!r}".format(line))

    def _say(self, word):
        self.daemon.stdin.write(word + b"\n")
        self.daemon.stdin.flush()
        self._expect(word)

    def die(self):
        self._say(b"die")
        self.dead = True

    def revive(self):
        self._say(b"revive")
        self.dead = False

    def close(self):
        if self.daemon.poll() is None:
            try:
                self.daemon.stdin.close()     # aborts whatever still waits
                self.daemon.wait(10)
            except Exception:
                self.daemon.kill()
                self.daemon.wait(10)
        _libc.umount2(os.fsencode(self.point), MNT_DETACH)


# ---- the daemon ----

_IN = struct.Struct("<IIQQIIIHH")
_ATTR = struct.Struct("<QQQQQQIIIIIIIIII")
LOOKUP, FORGET, GETATTR, SETATTR, READLINK, SYMLINK, MKNOD, MKDIR, UNLINK, RMDIR, RENAME, LINK, \
    OPEN, READ, WRITE, STATFS, RELEASE = 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18
FSYNC, FLUSH, INIT, OPENDIR, READDIR, RELEASEDIR, FSYNCDIR, ACCESS, CREATE, INTERRUPT, DESTROY = \
    20, 25, 26, 27, 28, 29, 30, 34, 35, 36, 38
BATCH_FORGET, RENAME2 = 42, 45


class _Daemon:
    def __init__(self, fd, backing):
        self.fd = fd
        self.paths = {1: os.fsencode(backing)}
        self.nodes = {os.fsencode(backing): 1}
        self.next_node = 2
        self.files = {}
        self.next_file = 1
        self.dead = False
        self.held = []

    def node(self, path):
        number = self.nodes.get(path)
        if number is None:
            number = self.nodes[path] = self.next_node
            self.paths[number] = path
            self.next_node += 1
        return number

    def attr(self, info):
        def sec(value):
            return int(value) & 0xffffffffffffffff
        return _ATTR.pack(info.st_ino, info.st_size, info.st_blocks, sec(info.st_atime),
                          sec(info.st_mtime), sec(info.st_ctime), info.st_atime_ns % 10 ** 9,
                          info.st_mtime_ns % 10 ** 9, info.st_ctime_ns % 10 ** 9, info.st_mode,
                          info.st_nlink, info.st_uid, info.st_gid, 0, 4096, 0)

    def entry(self, path):
        info = os.lstat(path)
        # nothing is cached: every touch of the mount comes here
        return struct.pack("<QQQQII", self.node(path), 0, 0, 0, 0, 0) + self.attr(info)

    def reply(self, unique, payload=b"", error=0):
        try:
            os.write(self.fd, struct.pack("<IiQ", 16 + len(payload), -error, unique) + payload)
        except OSError:
            pass    # the request was abandoned meanwhile

    def opened(self, handle, flags=0):
        number = self.next_file
        self.next_file += 1
        self.files[number] = handle
        return struct.pack("<QII", number, flags, 0)

    def serve(self, raw):
        _length, opcode, unique, nodeid, _uid, _gid, _pid, _ext, _pad = _IN.unpack_from(raw)
        body = raw[_IN.size:]
        if opcode in (FORGET, BATCH_FORGET, INTERRUPT):
            return
        if opcode == INIT:
            major, minor = struct.unpack_from("<II", body)
            self.reply(unique, struct.pack("<IIIIHHIIHHIII", 7, min(minor, 31), 65536, 0, 12, 9,
                                           131072, 1, 32, 0, 0, 0, 0) + b"\0" * 20)
            return
        if self.dead:
            self.held.append(raw)
            return
        try:
            payload = self.answer(opcode, nodeid, body)
        except OSError as exc:
            self.reply(unique, error=exc.errno or errno.EIO)
        else:
            self.reply(unique, payload)

    def answer(self, opcode, nodeid, body):
        path = self.paths.get(nodeid)
        if path is None:
            raise OSError(errno.ESTALE, "gone")

        def child(raw):
            return os.path.join(path, raw.rstrip(b"\0"))
        if opcode == LOOKUP:
            return self.entry(child(body))
        if opcode == GETATTR:
            return struct.pack("<QII", 0, 0, 0) + self.attr(os.lstat(path))
        if opcode == SETATTR:
            valid, _p, _fh, size, _owner, atime, mtime, _c, ans, mns, _cns, mode, _u, uid, gid, _v = \
                struct.unpack_from("<IIQQQQQQIIIIIIII", body)
            if valid & 1:
                os.chmod(path, mode & 0o7777)
            if valid & 6:
                os.lchown(path, uid if valid & 2 else -1, gid if valid & 4 else -1)
            if valid & 8:
                os.truncate(path, size)
            if valid & 0x30:
                info = os.lstat(path)
                os.utime(path, ns=(atime * 10 ** 9 + ans if valid & 0x10 else info.st_atime_ns,
                                   mtime * 10 ** 9 + mns if valid & 0x20 else info.st_mtime_ns),
                         follow_symlinks=False)
            return struct.pack("<QII", 0, 0, 0) + self.attr(os.lstat(path))
        if opcode == READLINK:
            return os.readlink(path)
        if opcode == SYMLINK:
            name, target = body.split(b"\0")[:2]
            os.symlink(target, child(name))
            return self.entry(child(name))
        if opcode == MKDIR:
            mode, umask = struct.unpack_from("<II", body)
            os.mkdir(child(body[8:]), mode & ~umask)
            return self.entry(child(body[8:]))
        if opcode == MKNOD:
            mode, rdev, umask, _pad = struct.unpack_from("<IIII", body)
            os.mknod(child(body[16:]), mode & ~umask, rdev)
            return self.entry(child(body[16:]))
        if opcode == UNLINK:
            os.unlink(child(body))
            self.nodes.pop(child(body), None)
            return b""
        if opcode == RMDIR:
            os.rmdir(child(body))
            self.nodes.pop(child(body), None)
            return b""
        if opcode in (RENAME, RENAME2):
            skip = 8 if opcode == RENAME else 16
            newdir = struct.unpack_from("<Q", body)[0]
            old, new = body[skip:].split(b"\0")[:2]
            target = os.path.join(self.paths[newdir], new)
            os.rename(child(old), target)
            for known in list(self.nodes):
                if known == child(old) or known.startswith(child(old) + b"/"):
                    number = self.nodes.pop(known)
                    moved = target + known[len(child(old)):]
                    self.nodes[moved] = number
                    self.paths[number] = moved
            return b""
        if opcode == LINK:
            source = struct.unpack_from("<Q", body)[0]
            os.link(self.paths[source], child(body[8:]), follow_symlinks=False)
            return self.entry(child(body[8:]))
        if opcode == OPEN:
            flags = struct.unpack_from("<I", body)[0]
            return self.opened(os.open(path, flags & ~(os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW)))
        if opcode == CREATE:
            flags, mode, umask, _flags = struct.unpack_from("<IIII", body)
            name = child(body[16:])
            handle = os.open(name, flags | os.O_CREAT, mode & ~umask)
            return self.entry(name) + self.opened(handle)
        if opcode == READ:
            handle, offset, size = struct.unpack_from("<QQI", body)
            return os.pread(self.files[handle], size, offset)
        if opcode == WRITE:
            handle, offset, size = struct.unpack_from("<QQI", body)
            return struct.pack("<II", os.pwrite(self.files[handle], body[40:40 + size], offset), 0)
        if opcode == RELEASE:
            handle = self.files.pop(struct.unpack_from("<Q", body)[0], None)
            if handle is not None:
                os.close(handle)
            return b""
        if opcode == OPENDIR:
            return struct.pack("<QII", 0, 0, 0)
        if opcode == READDIR:
            _handle, offset, size = struct.unpack_from("<QQI", body)
            rows = [b".", b".."] + sorted(os.listdir(path))
            out = b""
            for index in range(offset, len(rows)):
                name = rows[index]
                kind = stat.S_IFDIR if index < 2 else os.lstat(os.path.join(path, name)).st_mode
                row = struct.pack("<QQII", index + 1, index + 1, len(name), stat.S_IFMT(kind) >> 12)
                row += name + b"\0" * (-len(name) % 8)
                if len(out) + len(row) > size:
                    break
                out += row
            return out
        if opcode == STATFS:
            info = os.statvfs(path)
            return struct.pack("<QQQQQIIII", info.f_blocks, info.f_bfree, info.f_bavail, info.f_files,
                               info.f_ffree, info.f_bsize, info.f_namemax, info.f_frsize, 0) + b"\0" * 24
        if opcode in (FLUSH, FSYNC, FSYNCDIR, RELEASEDIR, ACCESS, DESTROY):
            return b""
        raise OSError(errno.ENOSYS, "not here")


def _main(backing, point):
    # gone with its test, however that ends: one that was killed while it waits
    # here could not end before this answers
    _libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    fd = os.open("/dev/fuse", os.O_RDWR | os.O_CLOEXEC)
    options = "fd={},rootmode=40000,user_id=0,group_id=0,allow_other".format(fd).encode()
    if _libc.mount(b"puppy-deadfs", os.fsencode(point), b"fuse.puppydead", MS_NOSUID | MS_NODEV, options):
        code = ctypes.get_errno()
        raise SystemExit("mount: " + os.strerror(code))
    daemon = _Daemon(fd, os.path.realpath(backing))
    say = lambda word: (sys.stdout.buffer.write(word + b"\n"), sys.stdout.buffer.flush())
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    poller.register(0, select.POLLIN | select.POLLHUP)
    started = False
    while True:
        for ready, _events in poller.poll():
            if ready == 0:
                word = sys.stdin.buffer.readline().strip()
                if not word:
                    return
                if word == b"die":
                    daemon.dead = True
                elif word == b"revive":
                    daemon.dead = False
                    held, daemon.held = daemon.held, []
                    for raw in held:
                        daemon.serve(raw)
                say(word)
                continue
            try:
                raw = os.read(fd, 1 << 20)
            except OSError as exc:
                if exc.errno in (errno.EINTR, errno.EAGAIN, errno.ENOENT):
                    continue
                return
            daemon.serve(raw)
            if not started:
                started = True
                say(b"mounted")


if __name__ == "__main__":
    _main(sys.argv[1], sys.argv[2])
