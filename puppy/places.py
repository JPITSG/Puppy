"""Places: folders and files outside Puppy's own storage, touched safely.

A project may live on a network drive, a removable disk or a FUSE mount, and
one of those that stops answering does not fail: whoever touches it waits in
the kernel for as long as that lasts, often past any signal. One such touch on
the event loop stops Puppy for everybody, whatever session it was for. One in
a pool of threads takes a thread away that nobody can get back, and keeps the
process from ending besides: Puppy could not even be restarted.

So nothing outside Puppy's own storage is touched on the event loop. What a
session list says about a folder is what ``session_directories`` last saw
there; everything else is a call of ``ask``, which has two parts.

It *arrives*. The path is followed one name at a time, links included, so that
the mount being touched is always known, and a mount that is not one Puppy
itself stands on is asked something its server must answer. That is done by a
process of its own (``place_scout``), which Puppy can leave behind. Arriving
takes a moment; a caller for whom it takes ``TOUCH_SECONDS`` is told that the
place is not answering (``NotAnswering``), and the mount the scout stands on is
*silent* from then on: nobody else is sent there until the scout is back from
it. Nothing is done later behind the back of a caller who was told it was not.

Then it *works*: the function runs in a thread of its own, and ``limit`` says
how long its caller waits for it. Work that takes as long as it takes belongs
in a pool (``workers.jobs``), behind a ``visit`` which has shown that the place
answers.

The mounts Puppy stands on are never silent - if one were, Puppy could not say
so - but a call there is bounded as well, and off the loop. The threads here
are daemons on purpose, and a mount is known by its id, so a drive that is
unmounted and mounted again at the same point is a new one, whatever still
waits on the old.
"""
from __future__ import annotations

import asyncio
import errno
import itertools
import json
import logging
import os
import sys
import threading
import time

from puppy import config, place_scout, workers

log = logging.getLogger("puppy.places")

# A disk that has gone to sleep takes several seconds to spin up.
TOUCH_SECONDS = 10.0
WORK_SECONDS = 30.0
# Callers on their way to one mount at a time. The line behind them costs
# nothing, and a place in it lasts no longer than TOUCH_SECONDS.
MOUNT_PLACES = 8
OWN_PLACES = 64
LINE = 100000
TABLE_SECONDS = 1.0
# How long after a scout could not be started the next one is tried.
RETRY_SECONDS = 60.0
GONE = "not answering; its disk or network drive may be disconnected"
SLOW = "taking too long to answer"
_SCOUT = ("# Puppy's scout: goes first where a drive may not answer\n"
          "import sys; sys.path.insert(0, sys.argv[1]); from puppy import place_scout; "
          "place_scout.main(sys.argv[2:])")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NotAnswering(OSError):
    """The file system under a path has stopped answering: the one mounted at
    ``point``, where that is known.

    ``queued`` when that was not seen: too many others are on their way there,
    and whether the place answers is theirs to find out."""

    def __init__(self, path, why=GONE, queued=False, point=None):
        super().__init__(errno.ETIMEDOUT, why, str(path))
        self.queued = queued
        self.point = point

    def __str__(self):
        return "{} is {}".format(self.filename, self.strerror)


class Place:
    """What was found at the end of a path."""

    __slots__ = ("path", "real", "directory", "foreign", "space")

    def __init__(self, path, real, directory, foreign, space=None):
        self.path = path            # as it was asked for, made absolute
        self.real = real            # with every link followed
        self.directory = directory
        self.foreign = foreign      # not on a mount Puppy itself stands on
        self.space = space          # there, as place_scout.space says it


# ---- mounts ----

_table_lock = threading.Lock()
_table = {}         # mount point -> Mount, as the kernel last listed them
_table_at = None

_lock = threading.Lock()
_overdue = {}       # mount id -> the calls that have not come back from it
_lanes = {}         # mount id -> the callers on their way there (loop only)
_returns = []       # called, in some thread, when a mount answers again


def own_paths() -> list:
    """What Puppy stands on: if one of these stops answering, so does Puppy."""
    named = [os.path.abspath(str(path)) for path in
             (os.sep, config.BASE_DIR, config.DATA_DIR, sys.executable or os.sep)]
    return list(dict.fromkeys(named + [os.path.realpath(path) for path in named]))


def _refresh() -> None:
    """Bring the table up to date. Only in a thread: the event loop reads
    nothing at all, not even the kernel's own list."""
    global _table, _table_at
    with _table_lock:
        if _table_at is None or time.monotonic() - _table_at >= TABLE_SECONDS:
            _table = place_scout.read_table(own_paths())
            _table_at = time.monotonic()


def mount_of(path):
    """The mount a path is on by its face: no link followed, nothing touched.
    None where the kernel does not say: every place is then taken for one of
    Puppy's own, bounded but never silent."""
    return place_scout.find(_table, path)


def mounted(path) -> bool:
    """Whether something is mounted right there."""
    mount = mount_of(path)
    return mount is not None and mount.point == path


def _own(path) -> bool:
    mount = mount_of(path)
    return mount is None or mount.own


def _is_silent(mount) -> bool:
    return mount is not None and bool(_overdue.get(mount.id))


def silent(path) -> bool:
    """Whether the mount a path names has stopped answering."""
    return _is_silent(mount_of(os.path.abspath(str(path))))


def anything_silent() -> bool:
    return bool(_overdue)


def on_return(callback) -> None:
    _returns.append(callback)


def off_return(callback) -> None:
    if callback in _returns:
        _returns.remove(callback)


def _lane(mount) -> workers.Lane:
    number = mount.id if mount is not None else 0
    lane = _lanes.get(number)
    if lane is None:
        for old in [old for old, lane in _lanes.items() if not lane.running]:
            del _lanes[old]     # a place nobody is on the way to
        own = mount is None or mount.own
        lane = _lanes[number] = workers.Lane(OWN_PLACES if own else MOUNT_PLACES, waiting=LINE)
    return lane


class _Elsewhere(Exception):
    pass


def nearby(path, base):
    """(where a path leads, whether a folder is there), without leaving the
    mount ``base`` is on and the ones Puppy stands on; None when the path leads
    to another, which may not answer. For a thread that has arrived at ``base``
    and looks at what else is named there."""
    _refresh()
    home = mount_of(os.path.abspath(base))
    home = home.id if home is not None else None

    def enter(here):
        mount = mount_of(here)
        if mount is not None and not mount.own and mount.id != home:
            raise _Elsewhere()
    try:
        return place_scout.follow(os.path.abspath(path), enter)[:2]
    except _Elsewhere:
        return None


def _refusal(path, mount) -> NotAnswering:
    return NotAnswering(path, point=None if mount is None or mount.own else mount.point)


def _shunned(number):
    """The mount a scout was told to leave alone."""
    with _lock:
        for call in _overdue.get(number, ()):
            return call.overdue
    return None


# ---- one touch ----

class _Call:
    """Where one touch is at this moment, and whether anybody waits for it."""

    def __init__(self, path, loop):
        self.path = path
        self.loop = loop
        self.arrived = loop.create_future()     # its Place
        self.result = loop.create_future()      # what the function answered
        for future in (self.arrived, self.result):
            # an answer nobody took in is not an unhandled one
            future.add_done_callback(lambda done: done.cancelled() or done.exception())
        self.mount = None       # what is being touched
        self.entered = 0.0
        self.landed = False     # the place has answered
        self.left = False       # nobody waits any more
        self.overdue = None     # the mount this has not come back from

    def enter(self, mount) -> bool:
        """About to touch ``mount``, or touching it already. False when nobody
        should: it is silent, or nobody waits for this any more."""
        with _lock:
            back = self._back()
            silent_there = _is_silent(mount)
            self.mount = mount
            self.entered = time.monotonic()
            welcome = not self.left and not silent_there
        _answers_again(back)
        return welcome

    def land(self) -> bool:
        """The place has answered: whether anybody still waits for that."""
        with _lock:
            back = self._back()
            self.landed = True
            waited_for = not self.left
        _answers_again(back)
        return waited_for

    def end(self) -> None:
        with _lock:
            back = self._back()
        _answers_again(back)

    def _back(self):
        """Whatever held this call has let go of it (under the lock): the mount
        and since when, if this was the last call waiting there."""
        mount, self.overdue = self.overdue, None
        if mount is None:
            return None
        calls = _overdue.get(mount.id, set())
        calls.discard(self)
        if calls:
            return None
        _overdue.pop(mount.id, None)
        return mount, self.entered

    def leave(self) -> None:
        """Nobody waits any more."""
        with _lock:
            self.left = True

    def give_up(self) -> bool:
        """The time to arrive is over. False when it had just arrived: whoever
        asked goes on waiting, because the work may begin. Otherwise it never
        will, and the mount it stands on is silent until it comes back."""
        with _lock:
            if self.landed:
                return False
            self.left = True
            mount = self.mount
            if mount is None or mount.own or self.overdue is not None:
                return True
            self.overdue = mount
            first = mount.id not in _overdue
            _overdue.setdefault(mount.id, set()).add(self)
        if first:
            log.warning("%s has stopped answering; what is there waits until it does",
                        mount.point)
        return True

    def settle(self, future, value=None, error=None) -> None:
        """From any thread; on the loop it is called directly."""
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(value)

    def tell(self, future, value=None, error=None) -> None:
        try:
            self.loop.call_soon_threadsafe(self.settle, future, value, error)
        except RuntimeError:
            pass    # the loop has closed: nobody is left to tell


def _answers_again(back) -> None:
    if back is None:
        return
    mount, since = back
    log.warning("%s answers again after %.0f s", mount.point, time.monotonic() - since)
    for callback in list(_returns):
        try:
            callback()
        except Exception:
            log.exception("a place's return was not taken in")


def _thread(target, *args) -> None:
    threading.Thread(target=target, args=args, name="puppy-place", daemon=True).start()


def _walk(call, anywhere) -> None:
    """Arrive in this thread: on Puppy's own mounts, or ``anywhere`` when there
    is no scout to send."""
    _refresh()
    refused = []

    def enter(path):
        mount = mount_of(path)
        if not anywhere and mount is not None and not mount.own:
            raise _Elsewhere()
        if not call.enter(mount):
            refused.append(_refusal(call.path, mount))
            raise _Elsewhere()
    try:
        real, directory, space = place_scout.arrive(call.path, enter, _own)
        if call.land():
            call.tell(call.arrived, Place(call.path, real, directory, not _own(real), space))
    except _Elsewhere:
        # None: somebody else has to go on from here
        call.tell(call.arrived, None, refused[0] if refused else None)
    except BaseException as exc:
        call.tell(call.arrived, error=exc)
    finally:
        call.end()


def _work(call, function, args) -> None:
    try:
        call.tell(call.result, function(*args))
    except StopIteration as exc:    # which a future cannot carry
        call.tell(call.result, error=RuntimeError(repr(exc)))
    except BaseException as exc:
        call.tell(call.result, error=exc)


# ---- the scout ----

class _Scout:
    """The process that goes first. One per event loop, started when somebody
    is first sent to a mount that is not Puppy's own."""

    def __init__(self):
        self.process = None
        self.gone = False
        self.failed = None      # when it could not be started
        self.calls = {}
        self.numbers = itertools.count(1)
        self.starting = None
        self.reading = None

    async def ready(self) -> bool:
        if self.starting is None:
            self.starting = asyncio.ensure_future(self._start())
        await asyncio.shield(self.starting)
        return not self.gone

    async def _start(self) -> None:
        try:
            if not sys.executable:
                raise OSError("this Python has no executable")
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-S", "-c", _SCOUT, _ROOT, *own_paths(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, cwd=os.sep, start_new_session=True,
                limit=1 << 20)
        except Exception as exc:
            log.warning("no scout could be started: %s", exc)
            self.gone, self.failed = True, time.monotonic()
            return
        self.reading = asyncio.ensure_future(self._read())

    def send(self, call) -> None:
        number = next(self.numbers)
        self.calls[number] = call
        with _lock:
            shun = [mount for mount in _overdue]
        line = json.dumps({"id": number, "path": call.path, "shun": shun})
        self.process.stdin.write(line.encode("ascii") + b"\n")

    async def _read(self) -> None:
        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                self._heard(json.loads(line))
        except (OSError, ValueError, KeyError) as exc:
            log.warning("the scout makes no sense: %s", exc)
        finally:
            self.gone = True
            calls, self.calls = self.calls, {}
            for call in calls.values():
                call.end()      # nobody knows; whoever goes next finds out
                call.settle(call.arrived)
            self._end()

    def _heard(self, answer) -> None:
        number = answer["id"]
        call = self.calls.get(number)
        if call is None:
            return
        if "on" in answer:
            call.enter(place_scout.Mount(answer["on"], answer["point"]))
            return
        del self.calls[number]
        if "shunned" in answer:
            call.end()
            call.settle(call.arrived, error=_refusal(call.path, _shunned(answer["shunned"])))
        elif call.land():
            real = answer["real"]
            call.settle(call.arrived, Place(
                call.path, real, answer["directory"], call.mount is not None or not _own(real),
                answer.get("space")))

    def lost(self, call) -> bool:
        """Whether nothing at all has been heard of a call that is overdue: not
        the drive but the scout itself does not answer."""
        return call in self.calls.values() and call.mount is None

    def _end(self) -> None:
        process = self.process
        if process is None or process.returncode is not None:
            return
        try:
            process.stdin.close()
            process.kill()      # in its own time, if one of its threads waits
        except (OSError, RuntimeError):
            pass

    def close(self) -> None:
        self.gone = True
        if self.reading is not None:
            self.reading.cancel()
        self._end()


_scouts = {}    # loop -> its scout


async def _scout():
    """This loop's scout, or None when there is no having one."""
    loop = asyncio.get_running_loop()
    scout = _scouts.get(loop)
    if scout is not None and scout.gone and \
            (scout.failed is None or time.monotonic() - scout.failed >= RETRY_SECONDS):
        scout = None
    if scout is None:
        for old in [old for old in _scouts if old.is_closed()]:
            del _scouts[old]
        scout = _scouts[loop] = _Scout()
    return scout if await scout.ready() else None


async def close() -> None:
    """Send this loop's scout home: at once, unless it is stuck somewhere."""
    scout = _scouts.pop(asyncio.get_running_loop(), None)
    if scout is None:
        return
    scout.close()
    if scout.process is not None:
        try:
            await asyncio.wait_for(scout.process.wait(), 1.0)
        except asyncio.TimeoutError:
            pass


async def _look_again() -> None:
    """Read the mounts anew, if they were not read just now."""
    if _table_at is not None and time.monotonic() - _table_at < TABLE_SECONDS:
        return
    loop = asyncio.get_running_loop()
    done = loop.create_future()

    def look():
        _refresh()
        try:
            loop.call_soon_threadsafe(lambda: done.done() or done.set_result(None))
        except RuntimeError:
            pass
    _thread(look)
    await asyncio.wait({done}, timeout=TABLE_SECONDS)


# ---- the callers' side ----

async def _arrived(call, deadline):
    """Wait for a call to arrive: its Place, None when somebody else has to go
    on from here, NotAnswering when its time is over."""
    await asyncio.wait({call.arrived}, timeout=max(0.05, deadline - time.monotonic()))
    if not call.arrived.done():
        if call.give_up():
            raise _refusal(call.path, call.mount)
        await asyncio.wait({call.arrived})      # it is on its way to the loop
    return call.arrived.result()


async def _arrive(path, deadline) -> Place:
    loop = asyncio.get_running_loop()
    if _own(path):
        call = _Call(path, loop)
        try:
            _thread(_walk, call, False)
            place = await _arrived(call, deadline)
        finally:
            call.leave()
        if place is not None:
            return place
    for _attempt in range(2):
        scout = await asyncio.wait_for(_scout(), max(0.05, deadline - time.monotonic()))
        if scout is None:
            break
        call = _Call(path, loop)
        try:
            scout.send(call)
            place = await _arrived(call, deadline)
        except NotAnswering:
            if scout.lost(call):
                scout.close()
            raise
        finally:
            call.leave()
        if place is not None:
            return place
    call = _Call(path, loop)
    try:
        _thread(_walk, call, True)
        return await _arrived(call, deadline)
    finally:
        call.leave()


async def ask(path, function, *args, within=None, limit=None, patience=None):
    """``function(*args)``, which touches ``path``, once the place has shown
    that it answers.

    Raises NotAnswering when it does not arrive ``within`` TOUCH_SECONDS, when
    it is known not to answer, when it stops answering for ``patience`` while
    the function works, and when the function takes longer than ``limit``;
    whatever the function raises comes through as it is. It only ever starts
    while somebody waits for it."""
    limit = WORK_SECONDS if limit is None else limit
    patience = TOUCH_SECONDS if patience is None else patience
    place = await visit(path, within)
    call = _Call(place.path, asyncio.get_running_loop())
    _thread(_work, call, function, args)
    deadline = time.monotonic() + limit
    quiet = None
    while not call.result.done():
        left = deadline - time.monotonic()
        if left <= 0:
            raise NotAnswering(place.path, SLOW)
        await asyncio.wait({call.result}, timeout=min(left, TOUCH_SECONDS))
        if call.result.done() or left <= TOUCH_SECONDS:
            continue
        try:
            await visit(path)   # is it slow, or no longer there?
            quiet = None
        except NotAnswering as exc:
            now = time.monotonic()
            quiet = now if quiet is None else quiet
            if not exc.queued and now - quiet >= patience:
                raise
    return call.result.result()


async def visit(path, within=None) -> Place:
    """Go there and look: what is at the end of the path, NotAnswering if
    nothing answers. Work that takes long is then done elsewhere, knowing that
    the place answered a moment ago."""
    within = TOUCH_SECONDS if within is None else within
    path = os.path.abspath(str(path))
    if _is_silent(mount_of(path)):
        await _look_again()     # by now it may be another mount
        if _is_silent(mount_of(path)):
            raise _refusal(path, mount_of(path))
    deadline = time.monotonic() + within
    try:
        async with _lane(mount_of(path)).hold(wait=within):
            if _is_silent(mount_of(path)):  # found out while this one waited
                raise _refusal(path, mount_of(path))
            return await _arrive(path, deadline)
    except (workers.Busy, asyncio.TimeoutError):
        raise NotAnswering(path, queued=True) from None


def launch(place, argv) -> tuple:
    """(argv, cwd) that start a program in a folder without Puppy going there.

    A program is started by a child that enters the folder before it becomes
    the program, and the event loop waits until it has. A folder that is not
    on Puppy's own mounts is entered by a shell that has already started."""
    if not place.foreign:
        return list(argv), place.path
    return ["/bin/sh", "-c", 'cd -- "$0" && exec "$@"', place.path, *argv], None


async def _lifecycle(app):
    yield
    await close()


def register(app) -> None:
    if not app.get("puppy_places_registered"):
        app["puppy_places_registered"] = True
        app.cleanup_ctx.append(_lifecycle)
