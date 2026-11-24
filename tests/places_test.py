#!/usr/bin/env python3
"""Places without any drive at all: the mounts as the kernel lists them, paths
followed one name at a time, the scout's protocol and its end, what a caller is
told and when, and programs started in folders that are not Puppy's own. A
drive that really stops answering is tests/dead_mount_test.py's."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import patch
import zipfile

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root  # noqa: E402

ROOT = private_root("places-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")
CONTROL = ROOT / "control"
os.environ["PLACES_TEST_CONTROL"] = str(CONTROL)

from puppy import config, place_scout, places  # noqa: E402

FAKE_ID = 4711
OTHER_ID = 4712
# A scout that does as the control file says: it stands on the fake mount until
# told otherwise, ends, or talks nonsense. Everything it is sent is written down.
FAKE_SCOUT = '''
import json, os, sys, threading, time
control = os.environ["PLACES_TEST_CONTROL"]
def mode():
    try:
        with open(control) as handle:
            return handle.read().strip()
    except OSError:
        return ""
lock = threading.Lock()
def say(answer):
    with lock:
        print(json.dumps(answer), flush=True)
def go(request):
    with lock, open(control + ".log", "a") as handle:
        handle.write(json.dumps(request) + "\\n")
    now = mode()
    if now == "end":
        os._exit(3)
    if now == "nonsense":
        print("what?", flush=True)
        return
    if now == "mute":
        return
    point = os.path.join(os.path.dirname(control), "mnt")
    number = %d + (os.path.exists(control + ".anew") and 10)
    if request["path"].startswith(point + "2"):
        point, number = point + "2", %d
    if number in request["shun"]:
        say({"id": request["id"], "shunned": number})
        return
    say({"id": request["id"], "on": number, "point": point})
    while mode() == "hang":
        time.sleep(0.01)
    say({"id": request["id"], "real": os.path.realpath(request["path"]),
         "directory": os.path.isdir(request["path"]), "space": [512, 10, 4, 3]})
for line in sys.stdin:
    threading.Thread(target=go, args=(json.loads(line),), daemon=True).start()
os._exit(0)
''' % (FAKE_ID, OTHER_ID)


def control(word):
    CONTROL.write_text(word)


def sent() -> list:
    try:
        return [json.loads(line) for line in Path(str(CONTROL) + ".log").read_text().splitlines()]
    except OSError:
        return []


def fake_table(own=(), source=None):
    table = real_table(own)
    for name, number in (("mnt", FAKE_ID), ("mnt2", OTHER_ID)):
        table[str(ROOT / name)] = place_scout.Mount(number, str(ROOT / name), "fuse")
    return table


real_table = place_scout.read_table


async def until(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "never came true"
        await asyncio.sleep(0.01)


async def refused(awaitable, text=places.GONE):
    started = time.monotonic()
    try:
        await awaitable
    except places.NotAnswering as exc:
        assert text in str(exc), str(exc)
        return time.monotonic() - started, exc
    raise AssertionError("a place that does not answer was reached")


# ---- mounts and paths ----

def test_mounts():
    source = ROOT / "mountinfo"
    source.write_bytes(b"""22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw
30 22 0:40 / /mnt/nas rw - nfs4 server:/export rw
31 22 0:41 / /mnt/with\\040a\\011gap rw - cifs //server/share rw
32 30 0:42 / /mnt/nas/inner rw - fuse.sshfs host: rw
33 22 8:2 / /data rw - ext4 /dev/sdb1 rw
34 33 8:3 / /data rw - ext4 /dev/sdc1 rw
broken line
""")
    table = place_scout.read_table(["/data/puppy", "/"], source=str(source))
    find = lambda path: place_scout.find(table, path)
    assert {mount.id for mount in table.values()} == {22, 30, 31, 32, 34}
    assert find("/mnt/nas/project/deep").id == 30 and find("/mnt/nas").id == 30
    assert find("/mnt/nasty").id == 22, "only whole names count"
    assert find("/mnt/nas/inner/x").id == 32
    assert find("/mnt/with a\tgap/x").point == "/mnt/with a\tgap"
    assert find("/data/x").id == 34, "the later of two at one point covers the earlier"
    assert (find("/mnt/nas").kind, find("/mnt/nas/inner").kind, find("/").kind) == ("nfs4", "fuse.sshfs", "ext4")
    assert [mount.id for mount in table.values() if mount.own] == [22, 34]
    assert place_scout.read_table(["/"], source=str(ROOT / "none")) == {}
    assert place_scout.find({}, "/anything") is None
    real = place_scout.read_table(places.own_paths())
    assert real[os.sep].own and place_scout.find(real, str(config.DATA_DIR)).own
    print("mounts: escapes, over-mounts, nesting, whole names, and which are Puppy's own")


def test_follow():
    top = ROOT / "tree"
    (top / "a" / "b").mkdir(parents=True)
    (top / "a" / "file").write_text("x")
    (top / "rel").symlink_to("a/b")
    (top / "abs").symlink_to(top / "a")
    (top / "a" / "b" / "up").symlink_to("../../a")
    (top / "loop").symlink_to("loop")
    (top / "gone").symlink_to("nowhere/at/all")
    follow = lambda path: place_scout.follow(str(path), lambda here: None)
    for path, real, directory in (
            (top, top, True), (top / "rel", top / "a" / "b", True), (top / "abs" / "b", top / "a" / "b", True),
            (top / "rel" / "up" / "file", top / "a" / "file", False),
            (top / "rel" / ".." , top / "a", True), (top / "a" / "." / "b", top / "a" / "b", True),
            (top / "missing" / "x", top / "missing" / "x", False),
            (top / "a" / "file" / "x", top / "a" / "file" / "x", False),
            (Path("/"), Path("/"), True)):
        got = follow(path)
        assert got[:2] == (str(real), directory), (path, got)
        if os.path.lexists(str(path)):
            assert got[0] == os.path.realpath(str(path)), path
    assert follow(top / "loop")[1] is False and follow(top / "gone")[:2] == (str(top / "nowhere/at/all"), False)
    assert follow(str(top) + "/nul\0l")[1] is False and follow(str(top) + "/\udfff")[1] is False
    assert follow(top / "missing" / "x")[2] == str(top), "the last of it that is there"
    # every name is announced before it is touched, and a refusal is final
    touched = []

    def enter(path):
        touched.append(path)
        if path.endswith("/b"):
            raise places._Elsewhere()
    try:
        place_scout.follow(str(top / "rel" / "up"), enter)
    except places._Elsewhere:
        pass
    assert touched[-3:] == [str(top / "rel"), str(top / "a"), str(top / "a" / "b")], touched
    print("paths: links of every kind, loops, what is missing or impossible, announced name by name")


# ---- the scout ----

def scout_process(root, *own, isolated=True):
    return subprocess.Popen([sys.executable, "-I", "-S", "-c", places._SCOUT, str(root), *own],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def test_scout():
    (ROOT / "odd").mkdir()
    odd = os.fsdecode(os.fsencode(str(ROOT / "odd")) + b"/caf\xe9 \"quoted\"\n")
    os.mkdir(odd)
    with zipfile.ZipFile(str(ROOT / "packed.pyz"), "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("__init__.py", "place_scout.py"):
            archive.write(str(BASE / "puppy" / name), "puppy/" + name)
    for root in (BASE, ROOT / "packed.pyz"):
        process = scout_process(root, *places.own_paths())
        try:
            for number, (path, real, directory) in enumerate((
                    (str(ROOT), str(ROOT), True), (odd, odd, True), (str(ROOT / "nothing"), str(ROOT / "nothing"), False),
                    (str(CONTROL.parent), str(CONTROL.parent), True)), 1):
                process.stdin.write(json.dumps({"id": number, "path": path, "shun": []}).encode("ascii") + b"\n")
                process.stdin.flush()
                answers = []
                while not answers or "real" not in answers[-1]:
                    answers.append(json.loads(process.stdout.readline()))
                # one of Puppy's own mounts is not asked for its space
                assert answers[-1] == {"id": number, "real": real, "directory": directory, "space": None}, answers
            # a mount to be left alone is not touched: the one the root is on,
            # taken for somebody else's
            shun = place_scout.find(real_table(), str(ROOT)).id
            process.stdin.write(json.dumps({"id": 9, "path": str(ROOT), "shun": [shun]}).encode() + b"\n")
            process.stdin.flush()
            assert json.loads(process.stdout.readline()) == {"id": 9, "shunned": shun}
            process.stdin.close()
            assert process.wait(5) == 0, "it is gone when its input ends"
        finally:
            process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
    # somebody else's is, which its server has to answer
    process = scout_process(BASE, "/proc")
    process.stdin.write(json.dumps({"id": 1, "path": str(ROOT / "nothing"), "shun": []}).encode() + b"\n")
    process.stdin.flush()
    on = json.loads(process.stdout.readline())
    while "on" in on:
        on = json.loads(process.stdout.readline())
    unit, blocks, unused, available = on["space"]
    assert on["real"] == str(ROOT / "nothing") and unit > 0 and blocks >= unused >= 0 and available >= 0, on
    process.stdin.close()
    process.wait(5)
    process.stdout.close()
    process.stderr.close()
    assert isinstance(place_scout.space(str(ROOT / "nothing")), str), "why it cannot be said"
    process = scout_process(BASE, *places.own_paths())
    process.stdout.close()
    process.stdin.write(json.dumps({"id": 1, "path": "/", "shun": []}).encode() + b"\n")
    process.stdin.flush()
    assert process.wait(5) == 0, "and when nobody listens"
    process.stdin.close()
    process.stderr.close()
    print("scout: paths of any bytes, mounts left alone, from the tree and from a zipapp, and its end")


# ---- what a caller is told ----

async def own_places():
    top = ROOT / "tree"
    place = await places.visit(top / "rel")
    assert (place.path, place.real, place.directory, place.foreign) == \
        (str(top / "rel"), str(top / "a" / "b"), True, False)
    assert (await places.visit(top / "none")).directory is False
    assert await places.ask(top, os.listdir, str(top / "a")) is not None
    assert threading.current_thread().name not in await places.ask(
        top, lambda: threading.current_thread().name)
    for error in (KeyError("k"), OSError(5, "five"), ValueError("v")):
        def fail(error=error):
            raise error
        try:
            await places.ask(top, fail)
        except type(error) as exc:
            assert exc is error
        else:
            raise AssertionError("a failure was lost")
    try:
        await places.ask(top, next, iter(()))
    except RuntimeError as exc:
        assert "StopIteration" in str(exc)
    seconds, exc = await refused(places.ask(top, time.sleep, 1.0, limit=0.2), places.SLOW)
    assert exc.point is None
    assert 0.15 < seconds < 0.8, seconds
    assert not places.silent(top), "slow work says nothing about the drive"
    assert not places._scouts, "nobody is sent to Puppy's own mounts"
    # a path that is followed on Puppy's own mounts only
    assert places.nearby(top / "rel", top) == (str(top / "a" / "b"), True)
    assert places.nearby(top / "none", top) == (str(top / "none"), False)
    print("own places: where they lead, failures as they were, slow work, no scout")


async def foreign_places():
    control("")
    project = ROOT / "mnt" / "project"
    project.mkdir(parents=True)
    (ROOT / "mnt2").mkdir()
    (ROOT / "into").symlink_to(project)
    places._table_at = None
    place = await places.visit(project)
    assert (place.real, place.directory, place.foreign, place.space) == (str(project), True, True, [512, 10, 4, 3])
    assert (await places.visit(ROOT / "into")).foreign, "through a link from Puppy's own mounts"
    scout = places._scouts[asyncio.get_running_loop()]
    assert len(places._scouts) == 1 and scout.process.returncode is None
    assert places.nearby(ROOT / "into", ROOT) is None, "not to be gone to from here"
    assert places.nearby(project, project) == (str(project), True)

    # it stops answering: everybody on the way is told in time, a few of them
    # at a time are sent, and those after them not at all
    control("hang")
    before = len(sent())
    started = time.monotonic()
    ran = []
    outcomes = await asyncio.gather(*(
        refused(places.ask(project, ran.append, n)) for n in range(40)))
    assert time.monotonic() - started < 2.0, "nobody waits for the others' time"
    assert len(sent()) - before <= places.MOUNT_PLACES, "the rest wait where it costs nothing"
    assert any(not exc.queued for _, exc in outcomes)
    assert {exc.point for _, exc in outcomes if not exc.queued} == {str(ROOT / "mnt")}
    assert places.silent(project) and not places.silent(ROOT) and not places.silent(ROOT / "mnt2")
    seconds, exc = await refused(places.visit(project / "deeper"))
    assert seconds < 0.1 and not exc.queued and exc.point == str(ROOT / "mnt")
    seconds, exc = await refused(places.visit(ROOT / "into"))
    assert seconds < 0.1 and exc.point == str(ROOT / "mnt"), "a link into it is refused as well"
    assert sent()[-1]["shun"] == [FAKE_ID] and len(sent()) - before <= places.MOUNT_PLACES + 1
    # other drives, and Puppy's own, go on
    assert (await places.visit(ROOT)).directory and (await places.visit(str(config.DATA_DIR)))
    assert threading.active_count() < 10, threading.enumerate()

    # it comes back by itself, and nothing that was refused is done after all
    returned = []
    places.on_return(lambda: returned.append(1))
    places.on_return(lambda: 1 / 0)     # one that fails keeps none of the others
    control("")
    await until(lambda: not places.silent(project))
    await asyncio.sleep(0.05)
    assert returned == [1] and not ran, (returned, ran)
    del places._returns[:]
    assert (await places.visit(project)).directory

    # somebody who goes away before it is reached: the same
    control("hang")
    task = asyncio.ensure_future(places.ask(project, ran.append, "late"))
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    control("")
    await asyncio.sleep(0.1)
    assert not ran and not places.silent(project), "one who left says nothing about the drive"

    # it stops answering while the work is under way
    gate = threading.Event()
    for patience, at_least in ((None, 0.5), (1.2, 1.5)):
        work = asyncio.ensure_future(refused(places.ask(project, gate.wait, 10, limit=20, patience=patience)))
        await asyncio.sleep(0.1)
        control("hang")
        seconds, _ = await work
        assert at_least < seconds < at_least + 2.0, (patience, seconds)
        control("")
        await until(lambda: not places.silent(project))
    # and work that merely takes long is left to it while the drive answers
    started = time.monotonic()
    assert await places.ask(project, time.sleep, 1.5, limit=10) is None and time.monotonic() - started > 1.4
    gate.set()

    # two drives: one that hangs leaves the other alone, and a drive that is
    # mounted anew is another one
    control("hang")
    await refused(places.visit(project))
    assert (await asyncio.wait_for(asyncio.shield(asyncio.ensure_future(
        places.visit(ROOT))), 1)).directory
    anew = Path(str(CONTROL) + ".anew")
    anew.touch()
    place_scout.read_table = lambda own=(), source=None: dict(
        fake_table(own), **{str(ROOT / "mnt"): place_scout.Mount(FAKE_ID + 10, str(ROOT / "mnt"))})
    await asyncio.sleep(places.TABLE_SECONDS + 0.05)
    assert places.silent(project), "nobody has looked again yet"
    seconds, _ = await refused(places.visit(project))
    assert seconds > 0.2, "it was gone to, as a new mount: {}".format(seconds)
    place_scout.read_table = fake_table
    anew.unlink()
    control("")
    await until(lambda: not places._overdue)
    print("foreign places: told in time, silent and alone in it, back by itself, nothing done later, "
          "work that outlasts the drive, mounted anew")


async def scouts_end():
    project = ROOT / "mnt" / "project"
    places._table_at = None
    first = places._scouts.get(asyncio.get_running_loop())
    for word in ("end", "nonsense"):
        # it ends under its callers: another goes, then Puppy itself
        control(word)
        place = await places.visit(project)
        assert place.directory and place.foreign, word
        control("")
        assert (await places.visit(project)).directory
        current = places._scouts[asyncio.get_running_loop()]
        assert current is not first and not current.gone, word
        first = current
    # ended while it stood on the drive: nobody knows about the drive any more
    control("hang")
    await refused(places.visit(project))
    assert places.silent(project)
    os.kill(first.process.pid, signal.SIGKILL)
    await until(lambda: not places.silent(project))
    control("")
    assert (await places.visit(project)).directory
    # one that says nothing at all is not the drive's fault, and is replaced
    current = places._scouts[asyncio.get_running_loop()]
    control("mute")
    await refused(places.visit(project))
    assert not places.silent(project) and current.gone
    control("")
    assert (await places.visit(project)).directory
    assert places._scouts[asyncio.get_running_loop()] is not current
    # none can be started: the walk is made here, and another is tried later
    await places.close()
    with patch.object(places.sys, "executable", str(ROOT / "no-python")):
        assert (await places.visit(project)).directory
        assert places._scouts[asyncio.get_running_loop()].failed is not None
        with patch.object(asyncio, "create_subprocess_exec", side_effect=AssertionError("too soon")):
            assert (await places.visit(project)).directory
        with patch.object(places, "RETRY_SECONDS", 0.0):
            assert (await places.visit(project)).directory
    with patch.object(places, "RETRY_SECONDS", 0.0):
        assert (await places.visit(project)).directory
    scout = places._scouts[asyncio.get_running_loop()]
    assert scout.failed is None and not scout.gone
    # sent home, it goes; whoever comes next has a new one
    await places.close()
    assert scout.process.returncode is not None
    assert not places._scouts and (await places.visit(project)).directory
    await places.close()
    print("scouts: one that ends, talks nonsense, says nothing or cannot start costs its callers nothing")


async def launches():
    for name in ("plain", "with space", "-dash", "quote'\"$HOME`x`", "line\nbreak", "ünï"):
        folder = ROOT / "launch" / name
        folder.mkdir(parents=True)
        for foreign in (False, True):
            place = places.Place(str(folder), str(folder), True, foreign)
            argv, cwd = places.launch(place, [sys.executable, "-c", "import os, sys; print(os.getcwd()); "
                                              "print(sys.argv[1:]); sys.stdout.flush(); sys.exit(7)",
                                              "a b", "", "$HOME", "-n", "x\ny"])
            assert (cwd is None) == foreign
            process = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, stdout=asyncio.subprocess.PIPE, start_new_session=True)
            out = (await process.communicate())[0].decode()
            assert out == "{}\n{}\n".format(folder, ["a b", "", "$HOME", "-n", "x\ny"]), (name, foreign, out)
            assert process.returncode == 7
    # the program is the process that was started, so signals reach it
    argv, cwd = places.launch(places.Place(str(ROOT), str(ROOT), True, True), ["/bin/sleep", "30"])
    process = await asyncio.create_subprocess_exec(*argv, cwd=cwd, start_new_session=True)
    await until(lambda: b"sleep" in Path("/proc/{}/cmdline".format(process.pid)).read_bytes().split(b"\0")[0])
    process.send_signal(signal.SIGTERM)
    assert await process.wait() == -signal.SIGTERM
    argv, cwd = places.launch(places.Place(str(ROOT / "none"), "", True, True), ["/bin/echo", "no"])
    process = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, error = await process.communicate()
    assert process.returncode and not out and b"none" in error, (process.returncode, out, error)
    print("launches: any folder and any words, the program itself, and one that is not there")


async def main():
    config.ensure_dirs()
    with patch.object(places, "TOUCH_SECONDS", 0.5):
        await own_places()
        with patch.object(place_scout, "read_table", fake_table), \
                patch.object(places, "_SCOUT", FAKE_SCOUT):
            await foreign_places()
            await scouts_end()
        places._table_at = None
        await launches()


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        test_mounts()
        test_follow()
        test_scout()
        asyncio.run(main())
        print("places tests passed")
    finally:
        shutil.rmtree(str(ROOT), ignore_errors=True)
