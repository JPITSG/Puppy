"""Shared workspace-synchronization surface for remote-workspace sessions.

A linked session runs its engine on one node (the mirror side) while the
authoritative project directory lives on another node (the workspace side).
The controller brokers every byte between the two over its existing
authenticated channels; nodes never talk to each other and never learn each
other's tokens.

This module is the node-side half used by BOTH runtimes: the manifest scanner,
the pure three-way reconcile planner, the hardened applier, the wire framing,
and the HTTP surface registered through ``web.register_execution_api``:

  - ``/api/workspace/leases``            provider side, rooted at a project dir
  - ``/api/sessions/{sid}/workspace/*``  mirror side, rooted at the session's
                                         private mirror, plus its turn gate

Safety rules, in one place: every relative path is validated component by
component and opened relative to a directory descriptor with O_NOFOLLOW, so no
symlink - mirrored or hostile - can escape a root. Symlinks are carried
verbatim as entries and never followed on either side. Content moves as
SHA-256-verified whole files, staged beside their target and committed under an
fsync'd journal, so a crash can never leave a half-renamed batch. Every write
is compare-and-swap against the sender's view of the receiver; a mismatch is a
reported conflict, never a silent overwrite.
"""
from __future__ import annotations

import asyncio
import errno
import gzip
import hashlib
import json
import logging
import os
import secrets
import shutil
import stat
import time

from aiohttp import web

from puppy import config, db

log = logging.getLogger("puppy.wsync")

CONTENT_TYPE = "application/x-puppy-sync"

CHUNK = 256 * 1024
MAX_TREE_ENTRIES = 250_000
MAX_LINK_TARGET = 4096
MAX_PATH_CHARS = 4096
MAX_NAME_BYTES = 255
MAX_DEPTH = 120
MAX_LINE_BYTES = 1 << 16
# a reconcile splits work into bounded batches; plain deletes always ride last
BATCH_MAX_FILES = 400
BATCH_MAX_BYTES = 256 * 1024 * 1024
LEASE_IDLE_EXPIRY = 45 * 24 * 3600
_TMP_PREFIX = ".puppy-sync-tmp-"

_MODE_MASK = 0o7777


class SyncError(RuntimeError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def validate_relpath(path) -> str:
    """Reject anything but a plain, bounded, forward relative POSIX path."""
    if not isinstance(path, str) or not path or path == ".":
        raise SyncError("invalid sync path")
    if len(path) > MAX_PATH_CHARS or "\x00" in path or path.startswith("/"):
        raise SyncError("invalid sync path")
    parts = path.split("/")
    if len(parts) > MAX_DEPTH:
        raise SyncError("sync path is too deep")
    for part in parts:
        if part in ("", ".", ".."):
            raise SyncError("invalid sync path")
        if part.startswith(_TMP_PREFIX):
            raise SyncError("reserved sync staging name")
        try:
            encoded = part.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SyncError("sync path is not valid UTF-8") from exc
        if len(encoded) > MAX_NAME_BYTES:
            raise SyncError("sync path component is too long")
    return path


def entry_signature(entry) -> tuple:
    """What "the same" means to the planner: kind, content, permissions."""
    if entry is None:
        return None
    kind = entry.get("t")
    if kind == "f":
        return ("f", entry.get("h"), entry.get("m"))
    if kind == "d":
        return ("d", entry.get("m"))
    if kind == "l":
        return ("l", entry.get("lt"))
    return ("s",)   # an unmirrorable special occupies the path


def _same_content(a, b) -> bool:
    """Kind and content match, ignoring permission bits."""
    if a is None or b is None or a.get("t") != b.get("t"):
        return False
    if a["t"] == "f":
        return a.get("h") == b.get("h")
    if a["t"] == "l":
        return a.get("lt") == b.get("lt")
    return a["t"] == "d"


# ---- three-way planner (pure) ----

def plan_reconcile(base: dict, workspace: dict, mirror: dict) -> dict:
    """Decide, per path, which side's change wins one reconcile.

    Returns {"pull": ops, "push": ops, "conflicts": [...], "base": entries}.
    "pull" ops apply to the mirror, "push" ops apply to the workspace. The
    returned base maps every path to the state both sides share once every op
    lands; a conflicted path keeps its OLD base entry, so it surfaces again on
    each reconcile until resolved.
    """
    pull, push, conflicts = [], [], []
    next_base = {}
    for path in sorted(set(base) | set(workspace) | set(mirror)):
        b, w, m = base.get(path), workspace.get(path), mirror.get(path)
        sig_b, sig_w, sig_m = (entry_signature(b), entry_signature(w),
                               entry_signature(m))
        if sig_w == sig_m:
            if w is not None:
                next_base[path] = w
            continue
        if sig_w == sig_b:                       # only the mirror moved: push
            push.extend(ops_for(path, w, m))
            if m is not None:
                next_base[path] = m
            continue
        if sig_m == sig_b:                       # only the workspace moved: pull
            pull.extend(ops_for(path, m, w))
            if w is not None:
                next_base[path] = w
            continue
        # both sides moved apart; identical content differing only in
        # permission bits settles quietly in favor of the workspace
        if _same_content(w, m):
            pull.extend(ops_for(path, m, w))
            next_base[path] = w
            continue
        conflicts.append({"path": path, "workspace": w, "mirror": m, "base": b})
        if b is not None:
            next_base[path] = b
    order_ops(pull)
    order_ops(push)
    return {"pull": pull, "push": push, "conflicts": conflicts,
            "base": next_base}


def plan_authoritative_pull(workspace: dict, mirror: dict) -> dict:
    """Make a rebuilt mirror exactly match its authoritative workspace.

    Restore markers deliberately bypass three-way history. Even if an
    unexpected process touched the empty mirror before its first barrier,
    those bytes must never be pushed into or conflict with the authority.
    """
    pull = []
    for path in sorted(set(workspace) | set(mirror)):
        current, desired = mirror.get(path), workspace.get(path)
        if entry_signature(current) != entry_signature(desired):
            pull.extend(ops_for(path, current, desired))
    order_ops(pull)
    return {"pull": pull, "push": [], "conflicts": [],
            "base": dict(workspace)}


def ops_for(path: str, current, desired) -> list:
    """Ops that move the receiver from ``current`` to ``desired``.

    ``current`` doubles as the compare-and-swap expectation at the receiver.
    A kind flip (file->dir, dir->link, ...) removes the old entry first; the
    creation that follows skips its own CAS because the pre-delete carried it.
    """
    if desired is None:
        return [{"op": "delete", "p": path, "base": current}]
    kind = desired.get("t")
    flip = current is not None and current.get("t") != kind
    ops = []
    if flip:
        ops.append({"op": "delete", "p": path, "base": current, "pre": True})
        current = None
    if kind == "d":
        if current is not None:
            ops.append({"op": "chmod", "p": path, "m": desired.get("m"),
                        "base": current})
        else:
            ops.append({"op": "mkdir", "p": path, "m": desired.get("m"),
                        "base": current, "nocas": flip})
    elif kind == "l":
        ops.append({"op": "symlink", "p": path, "lt": desired.get("lt"),
                    "base": current, "nocas": flip})
    elif current is not None and current.get("h") == desired.get("h"):
        ops.append({"op": "chmod", "p": path, "m": desired.get("m"),
                    "base": current})
    else:
        ops.append({"op": "write", "p": path, "s": desired.get("s"),
                    "h": desired.get("h"), "m": desired.get("m"),
                    "mt": desired.get("mt"), "base": current, "nocas": flip})
    return ops


def order_ops(ops: list) -> None:
    """mkdirs shallow-first, then content (flip deletes just ahead of their
    replacement), then plain deletes deep-first."""
    def rank(op):
        depth = op["p"].count("/")
        if op["op"] == "mkdir" and not op.get("nocas"):
            return (0, depth, op["p"], 0)
        if op["op"] == "delete" and not op.get("pre"):
            return (2, -depth, op["p"], 0)
        return (1, depth, op["p"], 0 if op.get("pre") else 1)
    ops.sort(key=rank)


def split_batches(ops: list) -> list:
    """Bounded batches, preserving order; plain deletes ride behind writes."""
    writes = [op for op in ops if op["op"] != "delete" or op.get("pre")]
    deletes = [op for op in ops if op["op"] == "delete" and not op.get("pre")]
    batches, current, size = [], [], 0
    for op in writes:
        cost = int(op.get("s") or 0)
        if current and (len(current) >= BATCH_MAX_FILES or
                        size + cost > BATCH_MAX_BYTES):
            batches.append(current)
            current, size = [], 0
        current.append(op)
        size += cost
    if current:
        batches.append(current)
    for index in range(0, len(deletes), BATCH_MAX_FILES):
        batches.append(deletes[index:index + BATCH_MAX_FILES])
    return batches


# ---- store: scan / read / apply over one rooted tree ----

class _RootWalker:
    """Descriptor-relative path resolution that never follows symlinks."""

    def __init__(self, root: str):
        self.root = root

    def open_parent(self, relpath: str) -> tuple:
        """Return (dirfd, leafname); the caller owns closing dirfd."""
        validate_relpath(relpath)
        parts = relpath.split("/")
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
                os.close(fd)
                fd = nxt
            return fd, parts[-1]
        except OSError:
            os.close(fd)
            raise


def _hash_fd(fd) -> str:
    digest = hashlib.sha256()
    while True:
        block = os.read(fd, CHUNK)
        if not block:
            break
        digest.update(block)
    return digest.hexdigest()


def _write_all(fd, block: bytes) -> None:
    view = memoryview(block)
    while view:
        written = os.write(fd, view)
        view = view[written:]


class Store:
    """One synchronized tree plus its private metadata directory.

    Methods are blocking; async callers run them in an executor. The HTTP
    layer serializes applies and scans per store through one asyncio lock.
    """

    def __init__(self, root: str, meta_dir: str):
        self.root = os.path.realpath(root)
        self.meta = meta_dir
        self.walker = _RootWalker(self.root)
        os.makedirs(self.meta, mode=0o700, exist_ok=True)
        self._recover()

    # -- scanning --

    def _cache_path(self) -> str:
        return os.path.join(self.meta, "hashcache.json.gz")

    def _load_cache(self) -> dict:
        try:
            with gzip.open(self._cache_path(), "rt", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_cache(self, cache: dict) -> None:
        tmp = self._cache_path() + ".tmp"
        try:
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(cache, fh, separators=(",", ":"))
            os.replace(tmp, self._cache_path())
        except OSError as exc:
            log.warning("hash cache not saved for %s: %s", self.root, exc)

    def scan(self) -> dict:
        """Walk the tree into {"entries": {...}, "skipped": {...}}.

        The walk descends through directory descriptors opened O_NOFOLLOW, so
        a directory swapped for a symlink mid-scan cannot pull outside content
        into the manifest. File hashes are reused from the cache while
        (size, mtime_ns, inode) hold still, so a steady tree costs one stat
        pass.
        """
        cache = self._load_cache()
        state = {"entries": {}, "fresh": {},
                 "skipped": {"special": 0, "hardlink": 0, "encoding": 0,
                             "error": 0}}
        try:
            root_fd = os.open(self.root,
                              os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise SyncError("workspace is unreadable: {}".format(exc), 409)
        try:
            self._scan_dir(root_fd, "", cache, state)
        finally:
            os.close(root_fd)
        self._save_cache(state["fresh"])
        return {"entries": state["entries"], "skipped": state["skipped"]}

    def _scan_dir(self, dir_fd: int, rel: str, cache: dict, state: dict) -> None:
        entries, fresh, skipped = state["entries"], state["fresh"], state["skipped"]
        try:
            names = sorted(os.listdir(dir_fd))
        except OSError:
            skipped["error"] += 1
            return
        for name in names:
            if name.startswith(_TMP_PREFIX):
                continue
            child = "{}/{}".format(rel, name) if rel else name
            try:
                validate_relpath(child)
            except SyncError:
                skipped["encoding"] += 1
                continue
            try:
                info = os.lstat(name, dir_fd=dir_fd)
            except OSError:
                skipped["error"] += 1
                continue
            if len(entries) >= MAX_TREE_ENTRIES:
                raise SyncError(
                    "workspace exceeds {} entries".format(MAX_TREE_ENTRIES), 507)
            mode = stat.S_IMODE(info.st_mode) & _MODE_MASK
            if stat.S_ISDIR(info.st_mode):
                entries[child] = {"t": "d", "m": mode}
                try:
                    child_fd = os.open(
                        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=dir_fd)
                except OSError:
                    skipped["error"] += 1
                    continue
                try:
                    self._scan_dir(child_fd, child, cache, state)
                finally:
                    os.close(child_fd)
            elif stat.S_ISLNK(info.st_mode):
                try:
                    target = os.readlink(name, dir_fd=dir_fd)
                except OSError:
                    skipped["error"] += 1
                    continue
                if len(target) > MAX_LINK_TARGET:
                    skipped["special"] += 1
                    continue
                entries[child] = {"t": "l", "lt": target}
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink > 1:
                    skipped["hardlink"] += 1
                mark = [int(info.st_size), int(info.st_mtime_ns),
                        int(info.st_ino)]
                cached = cache.get(child)
                if isinstance(cached, list) and len(cached) == 4 and \
                        cached[:3] == mark:
                    digest = cached[3]
                else:
                    try:
                        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW,
                                          dir_fd=dir_fd)
                    except OSError:
                        skipped["error"] += 1
                        continue
                    try:
                        digest = _hash_fd(file_fd)
                    finally:
                        os.close(file_fd)
                    # the file may have changed while hashing; re-stat so the
                    # cache never pins a stale digest on a fresh mark
                    try:
                        info = os.lstat(name, dir_fd=dir_fd)
                        mark = [int(info.st_size), int(info.st_mtime_ns),
                                int(info.st_ino)]
                    except OSError:
                        skipped["error"] += 1
                        continue
                fresh[child] = mark + [digest]
                entries[child] = {"t": "f", "s": int(info.st_size), "m": mode,
                                  "h": digest, "mt": int(info.st_mtime_ns)}
            else:
                skipped["special"] += 1

    # -- reading --

    def open_for_read(self, relpath: str):
        """An open regular-file fd for streaming, or None when unreadable."""
        try:
            fd, leaf = self.walker.open_parent(relpath)
        except (OSError, SyncError):
            return None
        try:
            try:
                file_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError:
                return None
        finally:
            os.close(fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                os.close(file_fd)
                return None
        except OSError:
            os.close(file_fd)
            return None
        return file_fd

    def current_entry(self, relpath: str):
        """A fresh lstat view of one path, in manifest-entry form."""
        try:
            fd, leaf = self.walker.open_parent(relpath)
        except (OSError, SyncError):
            return None
        try:
            try:
                info = os.lstat(leaf, dir_fd=fd)
            except OSError:
                return None
            mode = stat.S_IMODE(info.st_mode) & _MODE_MASK
            if stat.S_ISDIR(info.st_mode):
                return {"t": "d", "m": mode}
            if stat.S_ISLNK(info.st_mode):
                try:
                    return {"t": "l", "lt": os.readlink(leaf, dir_fd=fd)}
                except OSError:
                    return None
            if not stat.S_ISREG(info.st_mode):
                return {"t": "s"}
            try:
                file_fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError:
                return None
            try:
                digest = _hash_fd(file_fd)
            finally:
                os.close(file_fd)
            return {"t": "f", "s": int(info.st_size), "m": mode, "h": digest,
                    "mt": int(info.st_mtime_ns)}
        finally:
            os.close(fd)

    # -- applying --

    def _journal_path(self) -> str:
        return os.path.join(self.meta, "journal.json")

    def _recover(self) -> None:
        """Finish a batch interrupted mid-commit, then sweep staging temps."""
        journal_path = self._journal_path()
        record = None
        try:
            with open(journal_path, "r", encoding="utf-8") as fh:
                record = json.load(fh)
        except FileNotFoundError:
            record = None
        except Exception:
            log.warning("unreadable sync journal discarded for %s", self.root)
        if isinstance(record, dict) and record.get("state") == "committing":
            for item in record.get("renames") or []:
                try:
                    self.promote(str(item["p"]), str(item["tmp"]))
                except (OSError, SyncError, KeyError):
                    pass
            log.info("completed an interrupted sync batch for %s", self.root)
        try:
            os.unlink(journal_path)
        except OSError:
            pass
        self._sweep_temps()

    def _sweep_temps(self) -> None:
        stack = [""]
        while stack:
            rel = stack.pop()
            absolute = os.path.join(self.root, rel) if rel else self.root
            try:
                with os.scandir(absolute) as it:
                    for item in it:
                        if item.name.startswith(_TMP_PREFIX):
                            try:
                                os.unlink(item.path)
                            except OSError:
                                pass
                        elif item.is_dir(follow_symlinks=False):
                            stack.append("{}/{}".format(rel, item.name)
                                         if rel else item.name)
            except OSError:
                continue

    def promote(self, relpath: str, temp_name: str) -> None:
        """Rename a fully staged temp file over its final name, durably."""
        fd, leaf = self.walker.open_parent(relpath)
        try:
            os.replace(temp_name, leaf, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(fd)


class ApplyBatch:
    """Stage every op, verify every byte, then commit under a journal."""

    def __init__(self, store: Store):
        self.store = store
        self.results = []
        self.renames = []      # [{p, tmp, mt}] journaled for crash recovery
        self.plan = []         # commit replays renames/actions in stage order
        # mkdirs stage like other actions, but a write beneath one needs its
        # directory NOW to stage its adjacent temp file - such directories are
        # created early and remembered so commit tolerates them existing
        self.staged_dirs = {}
        self.created_dirs = set()
        self._done = False

    def fail(self, op: dict, reason: str, conflict: bool = False) -> None:
        self.results.append({"p": op.get("p"), "ok": False,
                             "conflict": conflict, "reason": reason})

    def check_base(self, op: dict) -> bool:
        """Compare-and-swap: the receiver must still look like the sender saw."""
        if op.get("nocas"):
            return True
        current = self.store.current_entry(op["p"])
        if entry_signature(current) == entry_signature(op.get("base")):
            return True
        self.fail(op, "changed since it was scanned", conflict=True)
        return False

    def stage_action(self, op: dict) -> bool:
        """Queue a mkdir/chmod/symlink/delete behind its CAS check."""
        try:
            validate_relpath(op.get("p"))
        except SyncError as exc:
            self.fail(op, str(exc))
            return False
        if op["op"] == "symlink":
            target = op.get("lt")
            if not isinstance(target, str) or not target or \
                    len(target) > MAX_LINK_TARGET or "\x00" in target:
                self.fail(op, "invalid symlink target")
                return False
        if not self.check_base(op):
            return False
        self.plan.append(("action", op))
        self.results.append({"p": op["p"], "ok": True})
        if op["op"] == "mkdir":
            self.staged_dirs[op["p"]] = op.get("m")
        return True

    def ensure_parents(self, relpath: str) -> None:
        """Create any pending staged directories a write's temp file needs."""
        parts = relpath.split("/")[:-1]
        prefix = ""
        for part in parts:
            prefix = "{}/{}".format(prefix, part) if prefix else part
            if prefix in self.created_dirs or prefix not in self.staged_dirs:
                continue
            fd, leaf = self.store.walker.open_parent(prefix)
            try:
                mode = self.staged_dirs[prefix]
                try:
                    os.mkdir(leaf, mode=(mode if isinstance(mode, int)
                                         else 0o755) & _MODE_MASK, dir_fd=fd)
                except FileExistsError:
                    pass
                self.created_dirs.add(prefix)
            finally:
                os.close(fd)

    def stage_rename(self, rename: dict) -> None:
        self.renames.append(rename)
        self.plan.append(("rename", rename))
        self.results.append({"p": rename["p"], "ok": True})

    def commit(self) -> list:
        if self._done:
            return self.results
        self._done = True
        journal_path = self.store._journal_path()
        record = {"state": "committing", "renames": self.renames,
                  "created_at": time.time()}
        temp_journal = journal_path + ".tmp"
        with open(temp_journal, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_journal, journal_path)

        outcome = {}
        for item in self.results:
            if item.get("ok"):
                outcome[item["p"]] = item
        for kind, op in self.plan:
            entry = outcome.get(op["p"])
            try:
                if kind == "rename":
                    self.store.promote(op["p"], op["tmp"])
                    if op.get("mt"):
                        self._apply_mtime(op["p"], int(op["mt"]))
                else:
                    self._perform(op)
            except (OSError, SyncError) as exc:
                if entry is not None and entry.get("ok"):
                    busy = isinstance(exc, OSError) and \
                        exc.errno in (errno.ENOTEMPTY, errno.EEXIST,
                                      errno.EISDIR, errno.ENOTDIR)
                    entry.update({"ok": False, "conflict": busy,
                                  "reason": str(exc)})
        try:
            os.unlink(journal_path)
        except OSError:
            pass
        return self.results

    def abort(self) -> None:
        if self._done:
            return
        self._done = True
        for rename in self.renames:
            try:
                fd, _leaf = self.store.walker.open_parent(rename["p"])
            except (OSError, SyncError):
                continue
            try:
                os.unlink(rename["tmp"], dir_fd=fd)
            except OSError:
                pass
            finally:
                os.close(fd)

    def _apply_mtime(self, relpath: str, mtime_ns: int) -> None:
        fd, leaf = self.store.walker.open_parent(relpath)
        try:
            os.utime(leaf, ns=(mtime_ns, mtime_ns), dir_fd=fd,
                     follow_symlinks=False)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _perform(self, op: dict) -> None:
        fd, leaf = self.store.walker.open_parent(op["p"])
        try:
            kind = op["op"]
            if kind == "mkdir":
                mode = op.get("m") if isinstance(op.get("m"), int) else 0o755
                try:
                    os.mkdir(leaf, mode=mode & _MODE_MASK, dir_fd=fd)
                except FileExistsError:
                    if op["p"] not in self.created_dirs:
                        raise
            elif kind == "chmod":
                mode = op.get("m")
                if isinstance(mode, int):
                    target = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW,
                                     dir_fd=fd)
                    try:
                        os.fchmod(target, mode & _MODE_MASK)
                    finally:
                        os.close(target)
            elif kind == "symlink":
                try:
                    os.unlink(leaf, dir_fd=fd)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    if exc.errno != errno.EISDIR:
                        raise
                    os.rmdir(leaf, dir_fd=fd)
                os.symlink(op["lt"], leaf, dir_fd=fd)
            elif kind == "delete":
                try:
                    info = os.lstat(leaf, dir_fd=fd)
                except FileNotFoundError:
                    return
                if stat.S_ISDIR(info.st_mode):
                    os.rmdir(leaf, dir_fd=fd)
                else:
                    os.unlink(leaf, dir_fd=fd)
            else:
                raise SyncError("unknown sync operation")
        finally:
            os.close(fd)


# ---- store registry (crash recovery runs once per process per tree) ----

_stores = {}
_store_locks = {}


def get_store(root: str, meta_dir: str) -> Store:
    key = os.path.realpath(root)
    store = _stores.get(key)
    if store is None or store.meta != meta_dir:
        store = Store(root, meta_dir)
        _stores[key] = store
    return store


def store_lock(store: Store) -> asyncio.Lock:
    lock = _store_locks.get(store.root)
    if lock is None:
        lock = asyncio.Lock()
        _store_locks[store.root] = lock
    return lock


def forget_store(root: str) -> None:
    key = os.path.realpath(root)
    _stores.pop(key, None)
    _store_locks.pop(key, None)


# ---- wire framing ----

def encode_frame(header: dict) -> bytes:
    return json.dumps(header, separators=(",", ":")).encode("utf-8") + b"\n"


async def read_frame(stream):
    """One JSON header line from an aiohttp stream, or None at EOF."""
    try:
        line = await stream.readline()
    except (asyncio.LimitOverrunError, ValueError) as exc:
        raise SyncError("oversized sync frame") from exc
    if not line:
        return None
    if len(line) > MAX_LINE_BYTES:
        raise SyncError("oversized sync frame")
    try:
        header = json.loads(line.decode("utf-8"))
    except Exception as exc:
        raise SyncError("invalid sync frame") from exc
    if not isinstance(header, dict):
        raise SyncError("invalid sync frame")
    return header


async def read_exact(stream, size: int):
    """Yield exactly ``size`` bytes in bounded chunks."""
    remaining = size
    while remaining > 0:
        block = await stream.read(min(CHUNK, remaining))
        if not block:
            raise SyncError("sync stream ended early")
        remaining -= len(block)
        yield block


async def _drain_item(stream, size: int) -> None:
    """Discard one write item's bytes and its trailer, keeping frame sync."""
    async for _block in read_exact(stream, size):
        pass
    await read_frame(stream)


# ---- provider-side lease registry ----

def _lease_root_dir() -> str:
    return os.path.join(config.DATA_DIR, "workspace_leases")


def _lease_dir(lease_id) -> str:
    if not isinstance(lease_id, str) or not lease_id.isalnum() or \
            not 8 <= len(lease_id) <= 64:
        raise SyncError("invalid lease id", 404)
    return os.path.join(_lease_root_dir(), lease_id)


def validate_lease_root(root) -> str:
    """A workspace root must be a plain directory outside Puppy's own data."""
    if not isinstance(root, str) or not root.strip():
        raise SyncError("workspace path is required")
    resolved = os.path.realpath(os.path.abspath(root.strip()))
    if resolved == "/":
        raise SyncError("the filesystem root cannot be linked")
    if not os.path.isdir(resolved):
        raise SyncError("workspace path is not a directory: {}".format(resolved))
    data_dir = os.path.realpath(config.DATA_DIR)
    if resolved == data_dir or resolved.startswith(data_dir + os.sep) or \
            data_dir.startswith(resolved + os.sep):
        raise SyncError("workspace path cannot overlap Puppy's private data")
    return resolved


def create_lease(root, reuse: bool = False) -> dict:
    resolved = validate_lease_root(root)
    for existing in list_leases():
        other = str(existing.get("root") or "")
        if reuse and other == resolved:
            touch_lease(str(existing["id"]))
            return get_lease(str(existing["id"]))
        if other == resolved or other.startswith(resolved + os.sep) or \
                resolved.startswith(other + os.sep):
            raise SyncError(
                "another linked session already uses {}".format(other), 409)
    lease_id = secrets.token_hex(12)
    lease_path = _lease_dir(lease_id)
    os.makedirs(lease_path, mode=0o700, exist_ok=True)
    record = {"id": lease_id, "root": resolved, "created_at": time.time(),
              "used_at": time.time()}
    _write_lease(lease_path, record)
    return record


def _write_lease(lease_path: str, record: dict) -> None:
    path = os.path.join(lease_path, "lease.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    os.chmod(path, 0o600)


def get_lease(lease_id) -> dict:
    lease_path = _lease_dir(lease_id)
    try:
        with open(os.path.join(lease_path, "lease.json"), "r",
                  encoding="utf-8") as fh:
            record = json.load(fh)
    except FileNotFoundError:
        raise SyncError("unknown workspace lease", 404)
    except Exception as exc:
        raise SyncError("unreadable workspace lease", 500) from exc
    if not isinstance(record, dict) or record.get("id") != lease_id:
        raise SyncError("unknown workspace lease", 404)
    root = str(record.get("root") or "")
    if not os.path.isdir(root):
        raise SyncError("workspace directory is missing: {}".format(root), 409)
    return record


def touch_lease(lease_id: str) -> None:
    try:
        record = get_lease(lease_id)
        record["used_at"] = time.time()
        _write_lease(_lease_dir(lease_id), record)
    except (SyncError, OSError):
        pass


def release_lease(lease_id) -> bool:
    lease_path = _lease_dir(lease_id)
    if not os.path.isdir(lease_path):
        return False
    root = None
    try:
        root = get_lease(lease_id).get("root")
    except SyncError:
        pass
    shutil.rmtree(lease_path, ignore_errors=True)
    if root:
        forget_store(root)
    return True


def list_leases() -> list:
    out = []
    try:
        names = os.listdir(_lease_root_dir())
    except OSError:
        return out
    for name in names:
        try:
            out.append(get_lease(name))
        except SyncError:
            continue
    return out


def expire_leases() -> None:
    now = time.time()
    for lease in list_leases():
        if now - float(lease.get("used_at") or 0) > LEASE_IDLE_EXPIRY:
            release_lease(lease["id"])
            log.info("expired an idle workspace lease for %s", lease.get("root"))


# ---- mirror-side store resolution ----

MIRROR_UID_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"


def new_mirror_uid() -> str:
    return "".join(secrets.choice(MIRROR_UID_CHARS) for _ in range(10))


def mirror_base(uid) -> str:
    if not isinstance(uid, str) or not 4 <= len(uid) <= 24 or \
            not all(ch in MIRROR_UID_CHARS for ch in uid):
        raise SyncError("invalid mirror id")
    return os.path.join(config.DATA_DIR, "mirrors", uid)


def mirror_leaf(name: str) -> str:
    """The deterministic, single-component project name under a mirror id."""
    safe = "".join(ch for ch in (name or "") if ch.isalnum() or ch in "._-")
    return (safe.strip(".") or "project")[:80]


def allocate_mirror(uid: str, name: str) -> str:
    """Create the stable mirror tree for one linked session; return its cwd.

    The tree keeps the project's basename so any path that leaks past prefix
    rewriting still reads naturally, and it never moves because engine-native
    resume can be pinned to the cwd.
    """
    base = mirror_base(uid)
    tree = os.path.join(base, mirror_leaf(name))
    os.makedirs(tree, mode=0o700, exist_ok=True)
    os.chmod(base, 0o700)
    return tree


def remove_mirror(uid) -> bool:
    base = mirror_base(uid)
    mirrors_root = os.path.realpath(os.path.join(config.DATA_DIR, "mirrors"))
    resolved = os.path.realpath(base)
    if os.path.dirname(resolved) != mirrors_root:
        raise SyncError("refusing to remove an unmanaged mirror path")
    if not os.path.isdir(resolved):
        return False
    for name in os.listdir(resolved):
        forget_store(os.path.join(resolved, name))
    shutil.rmtree(resolved, ignore_errors=True)
    return True


def _session_value(session, key: str, default=""):
    if session is None:
        return default
    if isinstance(session, dict):
        return session.get(key, default)
    try:
        return session[key]
    except (KeyError, IndexError, TypeError):
        return default


def session_workspace(session):
    """Parse a session row's workspace descriptor, or None when unlinked."""
    raw = _session_value(session, "workspace")
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else \
            dict(raw) if isinstance(raw, dict) else None
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("uid"):
        return None
    return data


def public_cwd(session: dict) -> str:
    """The user-facing location for a session.

    Linked sessions execute inside a private mirror, but every API, search
    result, notification and transcript surface must identify the authoritative
    project instead. Internal engine/store callers continue reading the raw
    persisted ``cwd`` directly.
    """
    descriptor = session_workspace(session)
    root = descriptor.get("root") if isinstance(descriptor, dict) else None
    return str(root) if isinstance(root, str) and root else \
        str(_session_value(session, "cwd") or "")


def mirror_store_for(session: dict) -> Store:
    descriptor = session_workspace(session)
    if descriptor is None:
        raise SyncError("session has no linked workspace")
    cwd = str(session.get("cwd") or "")
    base = mirror_base(str(descriptor.get("uid")))
    if os.path.dirname(os.path.realpath(cwd)) != os.path.realpath(base) or \
            not os.path.isdir(cwd):
        raise SyncError("mirror directory is missing", 409)
    return get_store(cwd, os.path.join(base, ".sync"))


MIRROR_RESET_VERSION = 1
MIRROR_RESET_FILE = "restore-reset.json"
MIRROR_RESET_HEADER = "X-Puppy-Workspace-Reset"


def mark_mirror_reset(base: str, reset_id: str) -> None:
    """Mark a newly reconstructed mirror as authoritative-source-only."""
    if not isinstance(reset_id, str) or not 16 <= len(reset_id) <= 128:
        raise SyncError("invalid mirror reset id")
    meta = os.path.join(base, ".sync")
    os.makedirs(meta, mode=0o700, exist_ok=True)
    path = os.path.join(meta, MIRROR_RESET_FILE)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump({"version": MIRROR_RESET_VERSION, "reset_id": reset_id},
                  handle, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def mirror_reset(store: Store):
    path = os.path.join(store.meta, MIRROR_RESET_FILE)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as exc:
        raise SyncError("mirror reset marker is unreadable", 500) from exc
    if not isinstance(value, dict) or set(value) != {"version", "reset_id"} or \
            value.get("version") != MIRROR_RESET_VERSION or \
            not isinstance(value.get("reset_id"), str) or \
            not 16 <= len(value["reset_id"]) <= 128:
        raise SyncError("mirror reset marker has an unsupported shape", 500)
    return value["reset_id"]


def acknowledge_mirror_reset(store: Store, reset_id: str) -> bool:
    current = mirror_reset(store)
    if current is None:
        return False
    if current != reset_id:
        raise SyncError("mirror reset marker changed", 409)
    os.unlink(os.path.join(store.meta, MIRROR_RESET_FILE))
    return True


# ---- shared HTTP surface (both runtimes) ----

async def _stream_manifest(request: web.Request, store: Store,
                           kind: str) -> web.StreamResponse:
    loop = asyncio.get_running_loop()
    reset_id = None
    if kind == "mirror":
        reset_id = await loop.run_in_executor(None, mirror_reset, store)
        if reset_id and request.headers.get(MIRROR_RESET_HEADER) != "1":
            raise SyncError(
                "this workspace mirror was rebuilt from a backup; upgrade its "
                "controller before synchronizing it", 409)
    async with store_lock(store):
        scanned = await loop.run_in_executor(None, store.scan)
    response = web.StreamResponse(status=200, headers={
        "Content-Type": CONTENT_TYPE, "Cache-Control": "no-store"})
    await response.prepare(request)
    header = {"kind": kind, "count": len(scanned["entries"])}
    if reset_id:
        header["reset_id"] = reset_id
    await response.write(encode_frame(header))
    buffered = bytearray()
    for path, entry in scanned["entries"].items():
        buffered += encode_frame({"p": path, **entry})
        if len(buffered) >= CHUNK:
            await response.write(bytes(buffered))
            buffered.clear()
    buffered += encode_frame({"end": True, "skipped": scanned["skipped"]})
    await response.write(bytes(buffered))
    await response.write_eof()
    return response


async def _stream_fetch(request: web.Request, store: Store) -> web.StreamResponse:
    try:
        body = await request.json()
    except Exception:
        raise SyncError("invalid fetch request")
    paths = body.get("paths") if isinstance(body, dict) else None
    if not isinstance(paths, list) or not paths or len(paths) > BATCH_MAX_FILES:
        raise SyncError("fetch requires 1-{} paths".format(BATCH_MAX_FILES))
    paths = [validate_relpath(str(path)) for path in paths]
    loop = asyncio.get_running_loop()
    response = web.StreamResponse(status=200, headers={
        "Content-Type": CONTENT_TYPE, "Cache-Control": "no-store"})
    await response.prepare(request)
    for path in paths:
        entry = await loop.run_in_executor(None, store.current_entry, path)
        if entry is None or entry.get("t") != "f":
            await response.write(encode_frame({"p": path, "missing": True}))
            continue
        fd = store.open_for_read(path)
        if fd is None:
            await response.write(encode_frame({"p": path, "missing": True}))
            continue
        declared = int(entry["s"])
        await response.write(encode_frame(
            {"p": path, "s": declared, "h": entry["h"], "m": entry["m"],
             "mt": entry.get("mt")}))
        digest = hashlib.sha256()
        sent = 0
        try:
            while sent < declared:
                block = os.read(fd, min(CHUNK, declared - sent))
                if not block:
                    break
                digest.update(block)
                sent += len(block)
                await response.write(block)
        finally:
            os.close(fd)
        if sent < declared:
            # keep the framing intact, then disown the item in its trailer
            await response.write(b"\x00" * (declared - sent))
        await response.write(encode_frame({
            "p": path,
            "ok": sent == declared and digest.hexdigest() == entry["h"]}))
    await response.write(encode_frame({"end": True}))
    await response.write_eof()
    return response


async def _handle_apply(request: web.Request, store: Store) -> web.Response:
    if request.content_type != CONTENT_TYPE:
        raise SyncError("apply requires the sync content type", 415)
    loop = asyncio.get_running_loop()
    async with store_lock(store):
        batch = ApplyBatch(store)
        stream = request.content
        try:
            while True:
                header = await read_frame(stream)
                if header is None:
                    raise SyncError("apply stream ended without a commit")
                if header.get("end"):
                    break
                op = header.get("op")
                if op == "write":
                    await _stage_write(loop, batch, header, stream)
                elif op in ("mkdir", "chmod", "symlink", "delete"):
                    await loop.run_in_executor(None, batch.stage_action, header)
                else:
                    raise SyncError("unknown sync operation")
            results = await loop.run_in_executor(None, batch.commit)
        except Exception:
            await loop.run_in_executor(None, batch.abort)
            raise
    applied = sum(1 for item in results if item.get("ok"))
    return web.json_response({"ok": True, "applied": applied,
                              "results": results})


async def _stage_write(loop, batch: ApplyBatch, op: dict, stream) -> bool:
    """Stage one write item from the request stream, or drain it cleanly."""
    declared = op.get("s")
    if not isinstance(declared, int) or declared < 0:
        raise SyncError("invalid write size")
    path = op.get("p")
    try:
        validate_relpath(path)
    except SyncError as exc:
        batch.fail(op, str(exc))
        await _drain_item(stream, declared)
        return False
    ok = await loop.run_in_executor(None, batch.check_base, op)
    if not ok:
        await _drain_item(stream, declared)
        return False
    try:
        await loop.run_in_executor(None, batch.ensure_parents, path)
        fd, _leaf = batch.store.walker.open_parent(path)
    except (OSError, SyncError) as exc:
        batch.fail(op, "parent directory unavailable: {}".format(exc))
        await _drain_item(stream, declared)
        return False
    temp_name = _TMP_PREFIX + secrets.token_hex(8)
    digest = hashlib.sha256()
    try:
        try:
            temp_fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                              mode=0o600, dir_fd=fd)
        except OSError as exc:
            batch.fail(op, "cannot stage: {}".format(exc))
            await _drain_item(stream, declared)
            return False
        failed = ""
        try:
            async for block in read_exact(stream, declared):
                digest.update(block)
                _write_all(temp_fd, block)
            trailer = await read_frame(stream)
            if not isinstance(trailer, dict) or trailer.get("p") != path:
                failed = "missing content trailer"
            elif trailer.get("ok") is not True:
                failed = "the sender abandoned this file mid-stream"
            elif digest.hexdigest() != op.get("h"):
                failed = "content hash mismatch"
            else:
                mode = op.get("m")
                if isinstance(mode, int):
                    os.fchmod(temp_fd, mode & _MODE_MASK)
                await loop.run_in_executor(None, os.fsync, temp_fd)
        except SyncError:
            os.close(temp_fd)
            try:
                os.unlink(temp_name, dir_fd=fd)
            except OSError:
                pass
            raise
        os.close(temp_fd)
        if failed:
            try:
                os.unlink(temp_name, dir_fd=fd)
            except OSError:
                pass
            batch.fail(op, failed)
            return False
    finally:
        os.close(fd)
    batch.stage_rename({"p": path, "tmp": temp_name, "mt": op.get("mt")})
    return True


def _sync_error_response(exc: SyncError) -> web.Response:
    return web.json_response({"error": str(exc)}, status=exc.status)


# -- provider routes --

async def h_lease_create(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid lease request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid lease request"}, status=400)
    loop = asyncio.get_running_loop()
    try:
        record = await loop.run_in_executor(
            None, create_lease, str(body.get("root") or ""),
            body.get("reuse") is True)
    except SyncError as exc:
        return _sync_error_response(exc)
    log.info("workspace lease %s created for %s", record["id"], record["root"])
    return web.json_response({"ok": True, "lease": record})


async def h_lease_delete(request: web.Request):
    try:
        removed = release_lease(request.match_info["lease"])
    except SyncError as exc:
        return _sync_error_response(exc)
    return web.json_response({"ok": True, "removed": removed})


async def _lease_store(request: web.Request) -> Store:
    lease = get_lease(request.match_info["lease"])
    touch_lease(lease["id"])
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, get_store, lease["root"], _lease_dir(lease["id"]))


async def h_lease_manifest(request: web.Request):
    try:
        return await _stream_manifest(request, await _lease_store(request),
                                      "workspace")
    except SyncError as exc:
        return _sync_error_response(exc)


async def h_lease_fetch(request: web.Request):
    try:
        return await _stream_fetch(request, await _lease_store(request))
    except SyncError as exc:
        return _sync_error_response(exc)


async def h_lease_apply(request: web.Request):
    try:
        return await _handle_apply(request, await _lease_store(request))
    except SyncError as exc:
        return _sync_error_response(exc)


# -- mirror routes --

def _session_for(request: web.Request) -> dict:
    session = db.get_session(int(request.match_info["sid"]))
    if session is None:
        raise SyncError("session not found", 404)
    return session


async def _mirror_store(request: web.Request) -> Store:
    session = _session_for(request)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, mirror_store_for, session)


async def h_mirror_manifest(request: web.Request):
    try:
        return await _stream_manifest(request, await _mirror_store(request),
                                      "mirror")
    except SyncError as exc:
        return _sync_error_response(exc)


async def h_mirror_fetch(request: web.Request):
    try:
        return await _stream_fetch(request, await _mirror_store(request))
    except SyncError as exc:
        return _sync_error_response(exc)


async def h_mirror_apply(request: web.Request):
    from puppy import runner
    try:
        session = _session_for(request)
        hub = runner.hub(session["id"])
        if hub.status == "running" and not hub.workspace_barrier_active():
            return web.json_response(
                {"error": "the engine is mid-turn; the mirror accepts changes "
                          "only at a sync barrier"}, status=409)
        loop = asyncio.get_running_loop()
        store = await loop.run_in_executor(None, mirror_store_for, session)
        return await _handle_apply(request, store)
    except SyncError as exc:
        return _sync_error_response(exc)


async def h_mirror_reset_ack(request: web.Request):
    """Clear a restore marker only after the capable controller says its
    authoritative-source reconcile completed cleanly."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid mirror reset acknowledgement"},
                                 status=400)
    if not isinstance(body, dict) or set(body) != {"reset_id"} or \
            not isinstance(body.get("reset_id"), str):
        return web.json_response({"error": "invalid mirror reset acknowledgement"},
                                 status=400)
    try:
        store = await _mirror_store(request)
        loop = asyncio.get_running_loop()
        cleared = await loop.run_in_executor(
            None, acknowledge_mirror_reset, store, body["reset_id"])
        return web.json_response({"ok": True, "cleared": cleared})
    except SyncError as exc:
        return _sync_error_response(exc)


async def h_mirror_grant(request: web.Request):
    """The controller reports barrier progress or completion for one session."""
    from puppy import runner
    try:
        session = _session_for(request)
    except SyncError as exc:
        return _sync_error_response(exc)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid grant"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid grant"}, status=400)
    result = runner.hub(session["id"]).workspace_grant(body)
    return web.json_response(result, status=200 if result.get("ok") else 409)


def register(app: web.Application) -> None:
    r = app.router
    r.add_post("/api/workspace/leases", h_lease_create)
    r.add_delete("/api/workspace/leases/{lease}", h_lease_delete)
    r.add_get("/api/workspace/leases/{lease}/manifest", h_lease_manifest)
    r.add_post("/api/workspace/leases/{lease}/fetch", h_lease_fetch)
    r.add_post("/api/workspace/leases/{lease}/apply", h_lease_apply)
    r.add_get("/api/sessions/{sid:\\d+}/workspace/manifest", h_mirror_manifest)
    r.add_post("/api/sessions/{sid:\\d+}/workspace/fetch", h_mirror_fetch)
    r.add_post("/api/sessions/{sid:\\d+}/workspace/apply", h_mirror_apply)
    r.add_post("/api/sessions/{sid:\\d+}/workspace/reset-ack",
               h_mirror_reset_ack)
    r.add_post("/api/sessions/{sid:\\d+}/workspace/grant", h_mirror_grant)
    expire_leases()
