"""Goes to places that may not answer, so that Puppy does not have to.

Whoever touches a drive that has stopped answering waits in the kernel, often
past any signal, and a process with one thread waiting there can be neither
ended nor replaced until the drive answers. ``places`` therefore sends this
process to whatever is not on one of Puppy's own mounts, and only goes there
itself once the place has answered. A scout that is stuck costs nothing: Puppy
goes on, and can be restarted, without it.

It is told in one JSON line where to go, and which mounts to leave alone:

    {"id": 7, "path": "/mnt/nas/project", "shun": [61]}

Each request has a thread of its own and is answered by lines of its own -
whenever it is about to touch another mount, so that it is known where it
stands if nothing more is heard of it, and once it has arrived:

    {"id": 7, "on": 61, "point": "/mnt/nas"}
    {"id": 7, "real": "/mnt/nas/project", "directory": true, "space": [4096, 900, 300, 280]}

or ``{"id": 7, "shunned": 61}`` when the way leads to a mount it must leave
alone. When its input ends it is gone, whatever its threads are waiting for.

Only the standard library is used here, and little of it, because Puppy starts
this with ``-I -S``; the walk itself is shared with ``places``.
"""
import errno
import json
import os
import re
import stat
import sys
import threading
import time

MAX_LINKS = 40
TABLE_LIMIT = 16 * 1024 * 1024


class Mount:
    __slots__ = ("id", "point", "kind", "own")

    def __init__(self, number, point, kind="", own=False):
        self.id = number
        self.point = point
        self.kind = kind    # of file system
        self.own = own      # one Puppy itself stands on


def _unescaped(field):
    return os.fsdecode(re.sub(rb"\\([0-7]{3})", lambda m: bytes((int(m.group(1), 8),)), field))


def read_table(own=(), source="/proc/self/mountinfo"):
    """{mount point: Mount} as this process sees them; of two at one point, the
    later covers the earlier. The mounts the ``own`` paths are on are marked.
    Empty where the kernel does not say."""
    table = {}
    try:
        with open(source, "rb") as handle:
            lines = handle.read(TABLE_LIMIT).splitlines()
    except OSError:
        return table
    for line in lines:
        fields = line.split(b" ")
        kind = line.partition(b" - ")[2].split(b" ")[0]
        try:
            point = _unescaped(fields[4])
            table[point] = Mount(int(fields[0]), point, kind.decode("utf-8", "replace"))
        except (IndexError, ValueError):
            continue
    for path in own:
        mount = find(table, path)
        if mount is not None:
            mount.own = True
    return table


def find(table, path):
    """The mount a path is on by its face: no link followed, nothing touched."""
    while True:
        mount = table.get(path)
        if mount is not None or path == os.path.dirname(path):
            return mount
        path = os.path.dirname(path)


def follow(path, enter):
    """(where the path leads, whether a folder is there, the last of it that
    is there), followed one name at a time.

    ``enter(path)`` is told what is about to be touched, and may refuse. What
    ends the path early - a name that is not there, or not for this account -
    is for whoever asked to meet again and say in their own words."""
    at = os.sep
    rest = [name for name in path.split(os.sep) if name]
    links = 0
    directory = True
    enter(at)
    while rest:
        name = rest.pop(0)
        if name == os.curdir:
            continue
        if name == os.pardir:
            at, directory = os.path.dirname(at), True
            continue
        here = os.path.join(at, name)
        enter(here)
        try:
            info = os.lstat(here)
            if stat.S_ISLNK(info.st_mode):
                links += 1
                if links > MAX_LINKS:
                    raise OSError(errno.ELOOP, os.strerror(errno.ELOOP), path)
                target = os.readlink(here)
                rest = [part for part in target.split(os.sep) if part] + rest
                if target.startswith(os.sep):
                    at = os.sep
                continue
        except (OSError, ValueError):   # or a name no file could have
            return os.path.normpath(os.path.join(here, *rest)), False, at
        at, directory = here, stat.S_ISDIR(info.st_mode)
    return at, directory, at


def space(path):
    """[unit, all, unused, available] of a path's mount, or why it cannot be
    said."""
    try:
        info = os.statvfs(path)
    except OSError as exc:
        return exc.strerror or "could not read its space"
    return [info.f_frsize or info.f_bsize, info.f_blocks, info.f_bfree, info.f_bavail]


def arrive(path, enter, own):
    """(where the path leads, whether a folder is there, the space there) once
    the mount there has been asked something its server must answer, which is
    its space: names and attributes may all come from a cache. ``own(path)``
    says it is one of Puppy's, which is not asked (None)."""
    real, directory, there = follow(path, enter)
    return real, directory, None if own(there) else space(there)


class _Shunned(Exception):
    pass


_lock = threading.Lock()
_read = [None, 0.0]     # the mounts, and when they were read


def _mounts(own):
    with _lock:
        if _read[0] is None or time.monotonic() - _read[1] >= 1.0:
            _read[:] = read_table(own), time.monotonic()
        return _read[0]


def _go(request, own, say):
    number = request["id"]
    shun = set(request.get("shun") or ())
    table = _mounts(own)
    standing = [None]

    def enter(path):
        mount = find(table, path)
        if mount is None or mount.id == standing[0]:
            return
        if mount.id in shun:
            raise _Shunned(mount.id)
        standing[0] = mount.id
        if not mount.own:
            say({"id": number, "on": mount.id, "point": mount.point})

    def is_own(path):
        mount = find(table, path)
        return mount is None or mount.own
    try:
        real, directory, room = arrive(request["path"], enter, is_own)
        say({"id": number, "real": real, "directory": directory, "space": room})
    except _Shunned as exc:
        say({"id": number, "shunned": exc.args[0]})


def main(own):
    lock = threading.Lock()
    out = sys.stdout.buffer

    def say(answer):
        line = json.dumps(answer).encode("ascii") + b"\n"
        with lock:
            try:
                out.write(line)
                out.flush()
            except (OSError, ValueError):
                os._exit(0)     # nobody is listening any more

    for line in sys.stdin.buffer:
        threading.Thread(target=_go, args=(json.loads(line), own, say), daemon=True).start()
    os._exit(0)
