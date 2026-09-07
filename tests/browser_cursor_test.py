"""Cursor isolation and input latency; no real browser, network, or engines."""
import asyncio
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.scratch import private_root
ROOT = private_root("cursor-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")
from puppy import browser, browser_cursor, config


async def main():
    config.load()
    instance = browser.Manager("CURS")
    instance.running = True
    instance.page_session = "page"
    viewers = [browser._Viewer(object()), browser._Viewer(object())]
    replies = [[], []]
    for viewer, output in zip(viewers, replies):
        viewer.send_json = output.append
        instance.viewers[viewer.ws] = viewer
    fired = []
    instance._fire = lambda *args, **kwargs: fired.append((args, kwargs))
    gates = [asyncio.Event(), asyncio.Event()]
    calls = []

    async def read(*args):
        i = len(calls)
        calls.append(args)
        await gates[min(i, 1)].wait()
        return "pointer"

    async def until(test):
        for _ in range(100):
            if test():
                return
            await asyncio.sleep(.01)
        raise AssertionError("cursor condition timed out")

    def request(request_id, viewer=0, **fields):
        return instance.handle_client({"type": "cursor", "id": request_id,
                                       "nx": .25, "ny": .5, **fields}, viewers[viewer].ws)

    try:
        with patch.object(browser_cursor, "read_cursor", read):
            await request(1)
            await until(lambda: len(calls) == 1)
            for i in range(2, 20):
                await request(i)
            await instance.handle_client({"type": "mouse", "kind": "move", "nx": .5, "ny": .5})
            assert fired[-1][0][0] == "Input.dispatchMouseEvent"
            assert len(calls) == 1, "slow cursor reads must coalesce without blocking input"
            gates[0].set()
            await until(lambda: len(calls) == 2)
            assert not replies[0] and not replies[1], "obsolete reads stay private and silent"
            instance.cursor_epoch += 1
            gates[1].set()
            await until(lambda: bool(replies[0]))
            assert replies[0] == [{"type": "cursor", "id": 19, "value": "default"}]
            assert not replies[1]
            await until(lambda: viewers[0].cursor_task is None)
            for fields in ({"nx": float("nan")}, {"ny": 2}, {"nx": "0.5"}, {"nx": True}):
                await request(20, **fields)
            for bad_id in (0, True, 2**53, "id"):
                await request(bad_id)
            assert viewers[0].cursor_task is None
            await request(21, viewer=1)
            await until(lambda: bool(replies[1]))
            assert replies[1][-1]["value"] == "pointer"

        async def hangs(*args):
            await asyncio.Event().wait()

        with patch.object(browser_cursor, "read_cursor", hangs):
            await request(22)
            await until(lambda: len(replies[0]) == 2)
            assert replies[0][-1] == {"type": "cursor", "id": 22, "value": "default"}
            await request(23)
            task = viewers[0].cursor_task
            # Same cancellation used for hiding and disconnecting the viewer.
            viewers[0].cancel_cursor()
            await asyncio.gather(task, return_exceptions=True)
            assert viewers[0].cursor_request is None and viewers[0].cursor_task is None
            viewers[0].active = False
            await request(24)
            assert viewers[0].cursor_task is None

        releases = []
        async def call(method, params, **kwargs):
            if method == "DOM.getNodeForLocation":
                return {"backendNodeId": 1}
            if method == "DOM.resolveNode":
                return {"object": {"objectId": "node"}}
            return {"result": {"value": "url(https://example.invalid), pointer"}}
        fire = lambda *args, **kwargs: releases.append((args, kwargs))
        assert await browser_cursor.read_cursor(call, fire, "page", 1, 1, "group") == "default"
        assert releases[-1][0] == ("Runtime.releaseObjectGroup", {"objectGroup": "group"})
        print("PASS: cursor input latency, coalescing, viewer isolation, navigation races, validation, timeout, cancellation and keyword filtering")
    finally:
        for viewer in viewers:
            viewer.close()
        await asyncio.gather(*(v.task for v in viewers), return_exceptions=True)
        shutil.rmtree(ROOT)


if __name__ == "__main__":
    asyncio.run(main())
