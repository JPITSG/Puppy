#!/usr/bin/env python3
"""Real console, two isolated browsers, and reproducible anonymous screenshots.

Uses host Chromium through Puppy's debugging-pipe driver; no dependencies,
engine turns, or external services. --screenshots refreshes assets/; --serve
keeps the demo open for interactive review (mira / preview-password).
"""
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import sys
import time
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root("console-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp import web
from puppy import auth, browser, config, db, runner, search, session_tasks
from puppy import web as webui
from puppy.drivers import all_drivers


async def engines(*args, **kwargs):
    return [{**webui._engine_choices(driver), "installed": True, "auth": "ok",
             "version": "", "model_catalog_loaded": True,
             "model_options": [{"value": "", "label": "Default",
                                "effort_options": [{"value": "", "label": "Default"}]}]}
            for driver in all_drivers()]


async def fixture():
    config.load()
    config.set_value("instance_name", "Studio")
    config.set_value("sessions.default_cwd", "/home/mira/projects")
    db.connect()
    auth.create_user("mira", "preview-password")
    sid = db.create_session("Harbor dashboard", "claude", "/home/mira/projects/harbor", "", "", "", "default")
    for name, engine, folder in [("Release checklist", "codex", "harbor"),
                                  ("Garden planner", "opencode", "garden"),
                                  ("API cleanup", "claude", "atlas"),
                                  ("Weekend notes", "codex", "notes")]:
        db.create_session(name, engine, "/home/mira/projects/" + folder, "", "", "", "default")
    db.bump_session_to_top(sid)
    # Real task rows keep the shared task-strip renderer in every preview.
    # Only their status is simulated; no engine or project operation runs.
    for name in ("Card spacing", "Phone navigation"):
        tid = db.create_session(name, "codex", "/home/mira/projects/harbor-task", "", "", "", "default",
                                workspace_kind="temporary")
        session_tasks._save(tid, {"format": 1, "parent": sid,
            "request_id": "preview-task-" + str(tid), "prompt": name,
            "context": "", "base": "0" * 40, "created_at": time.time(),
            "outcome": "pending", "summary": "", "completed_at": 0,
            "applied_at": 0, "result_seq": 0})
        runner.hub(tid).status = "running"
        db.touch_session(tid, status="running")
    events = [
        ("user", {"text": "Make the dashboard easier to scan on a phone. Keep the activity feed and simplify the cards."}),
        ("assistant", {"text": "I’ll check the card layout and navigation first, then test the narrow-screen view."}),
        ("tool_use", {"tool_use_id": "demo-read", "tool": "Read", "input": {"file_path": "/home/mira/projects/harbor/src/dashboard.css"}}),
        ("tool_result", {"tool_use_id": "demo-read", "content": "Dashboard layout and shared card styles"}),
        ("assistant", {"text": "The cards share one layout rule. I can reduce the visual noise without changing how the activity feed works."}),
        ("tool_use", {"tool_use_id": "demo-edit", "tool": "Edit", "input": {"file_path": "/home/mira/projects/harbor/src/dashboard.css", "old_string": "gap: 24px;", "new_string": "gap: 16px;"}}),
        ("tool_result", {"tool_use_id": "demo-edit", "content": "Updated the shared card spacing"}),
        ("tool_use", {"tool_use_id": "demo-test", "tool": "Bash", "input": {"command": "npm test -- dashboard"}}),
        ("tool_result", {"tool_use_id": "demo-test", "content": "12 tests passed"}),
        ("assistant", {"text": "The dashboard now uses consistent spacing and clearer card headings. The activity feed stays visible on smaller screens.\n\nAll 12 layout tests passed, including the phone navigation checks."}),
        ("result", {"ok": True, "duration_ms": 12400, "usage": {"input_tokens": 8400, "output_tokens": 1250}}),
    ]
    for kind, data in events:
        db.add_event(sid, kind, data)
    search.reconcile()
    app = webui.build_app()
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.on_cleanup.clear()
    # Even an accidental click in the interactive preview cannot run an engine.
    for session in db.list_sessions():
        runner.hub(session["id"]).send_message = lambda text: {"error": "Preview only"}
    return app, sid


async def evaluate(instance, expression):
    result = await instance.call("Runtime.evaluate", {
        "expression": expression, "returnByValue": True, "awaitPromise": True,
        "userGesture": True}, session=instance.page_session)
    if result.get("exceptionDetails"):
        raise AssertionError(result["exceptionDetails"])
    return result.get("result", {}).get("value")


async def until(instance, expression):
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if await evaluate(instance, expression):
            return
        await asyncio.sleep(.03)
    raise AssertionError("browser condition timed out: " + expression)


async def open_console(instance, url, sid):
    await instance.ensure_started()
    await instance.call("Page.navigate", {"url": url}, session=instance.page_session)
    await until(instance, "typeof fetch === 'function' && location.pathname === '/' && document.querySelector('input') !== null")
    await evaluate(instance, "fetch('/api/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'mira',password:'preview-password'})}).then(r=>r.json())")
    await instance.call("Page.reload", session=instance.page_session)
    await until(instance, "typeof state !== 'undefined' && state.sessions.length > 0")
    await evaluate(instance, "openSessionTab(0," + str(sid) + ",state.sessions.find(s=>s.id===" + str(sid) + ")); window.demoView=state.views['s:0:" + str(sid) + "'].activeView(); true")
    await until(instance, "!!demoView.sharedDraft && demoView.draftReady")


async def type_text(instance, text):
    await evaluate(instance, "demoView.composer.ta.focus()")
    await instance.call("Input.insertText", {"text": text}, session=instance.page_session)


async def checks(a, b, hub, capture=False):
    # Measure real layout: an idle status must not reserve a row below tools.
    for width, height in [(1440, 900), (390, 844)]:
        await a.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height,
                     "deviceScaleFactor": 1, "mobile": width == 390}, session=a.page_session)
        for args, visible in [("", False), ("1", True), ("0,true", True),
                              ("0,false,true,'Save failed'", True), ("", False)]:
            layout = await evaluate(a, """(() => {
                const c = demoView.composer;
                c.showPresence(%s);
                const row = c.box.querySelector('.composer-presence');
                const tools = c.box.querySelector('.composer-row');
                return {height:row.getBoundingClientRect().height,
                    gap:c.box.getBoundingClientRect().bottom-tools.getBoundingClientRect().bottom,
                    padding:parseFloat(getComputedStyle(c.box).paddingBottom)+parseFloat(getComputedStyle(c.box).borderBottomWidth)};
            })()""" % args)
            assert (layout["height"] > 0) == visible, layout
            if not visible:
                assert abs(layout["gap"] - layout["padding"]) < 1, layout
    await a.call("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 800,
                 "deviceScaleFactor": 1, "mobile": False}, session=a.page_session)
    print("PASS: idle composer collapses below tools on desktop and phone; active status remains visible", flush=True)
    # Take the second editor offline, so both edits really begin from the
    # same revision. Reconnect must retain the local fork and reveal it.
    await evaluate(b, "demoView.ws.close(); true")
    await until(b, "!demoView.draftReady")
    await type_text(b, "My private idea")
    await type_text(a, "Start with the activity feed")
    await until(a, "!demoView.sharedDraft.flight")
    await until(b, "demoView.draftReady && demoView.sharedDraft.conflict")
    assert await evaluate(b, "demoView.composer.text()") == "My private idea"
    # Live input on the private branch still advertises presence, but never
    # changes the peer's text, selection, or textarea height. The status row
    # itself appears only while there is something to show.
    await evaluate(a, "demoView.composer.ta.setSelectionRange(6,10); true")
    before = await evaluate(a, "({text:demoView.composer.text(),start:demoView.composer.ta.selectionStart,end:demoView.composer.ta.selectionEnd,height:demoView.composer.ta.getBoundingClientRect().height})")
    await type_text(b, " while you work")
    await until(a, "demoView.sharedDraft.count === 1")
    after = await evaluate(a, "({text:demoView.composer.text(),start:demoView.composer.ta.selectionStart,end:demoView.composer.ta.selectionEnd,height:demoView.composer.ta.getBoundingClientRect().height})")
    assert before == after, (before, after)
    await b.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844,
                 "deviceScaleFactor": 2, "mobile": True}, session=b.page_session)
    await evaluate(b, "demoView.composer.box.querySelector('.composer-draft-review').click(); true")
    await until(b, "!!document.querySelector('.draft-review-form')")
    assert await evaluate(b, "document.querySelector('.draft-local').value") == "My private idea while you work"
    assert await evaluate(b, "[...document.querySelectorAll('.draft-review-form button')].every(b=>b.scrollWidth<=b.clientWidth+1 && b.getBoundingClientRect().right<innerWidth)")
    if capture:
        shot = await b.call("Page.captureScreenshot", {"format": "png"}, session=b.page_session)
        (BASE / "data" / "draft-review-mobile.png").write_bytes(base64.b64decode(shot["data"]))
    await evaluate(b, "document.querySelector('.draft-cancel').click(); demoView.composer.ta.focus(); true")
    await b.call("Emulation.setDeviceMetricsOverride", {"width": 1280, "height": 800,
                 "deviceScaleFactor": 1, "mobile": False}, session=b.page_session)
    await type_text(b, " — continued")
    assert await evaluate(b, "demoView.composer.text().endsWith('— continued')")
    # A full reload restores the losing variant from the existing journal.
    await b.call("Page.reload", session=b.page_session)
    await until(b, "typeof state !== 'undefined' && !!state.views['s:0:1'] && !!state.views['s:0:1'].sharedDraft")
    await evaluate(b, "window.demoView=state.views['s:0:1'].activeView(); true")
    await until(b, "demoView.draftReady && demoView.sharedDraft.conflict")
    assert await evaluate(b, "demoView.composer.text().endsWith('— continued')")
    await evaluate(b, "demoView.sharedDraft.review(); document.querySelector('.draft-use-shared').click(); true")
    assert await evaluate(b, "demoView.composer.text()") == before["text"]
    await evaluate(b, "demoView.composer.stopTyping(); true")
    await until(a, "demoView.sharedDraft.count === 0")
    # Hold the backend write barrier until both live editors have sent a
    # different value based on the same revision.
    async with hub._draft_lock:
        await evaluate(a, "demoView.composer.caretToEnd(); true")
        await type_text(a, " and a summary")
        await type_text(b, " with filters")
        local_a = await evaluate(a, "demoView.composer.text()")
        local_b = await evaluate(b, "demoView.composer.text()")
    await until(b, "demoView.sharedDraft.conflict && !demoView.sharedDraft.flight")
    assert await evaluate(a, "demoView.composer.text()") == local_a
    assert await evaluate(b, "demoView.composer.text()") == local_b
    # Rejection keeps the real box intact and permits a retry. Success sends
    # the private variant without consuming the other editor's shared draft.
    await evaluate(b, "demoView.submit(); true")
    await until(b, "!!demoView.sharedDraft.sendError")
    assert await evaluate(b, "demoView.composer.text()") == local_b
    submitted = []
    hub.send_message = lambda text: submitted.append(text) or {"ok": True}
    await evaluate(b, "demoView.sharedDraft.review(); true")
    await until(b, "!demoView.sharedDraft.sent")
    assert submitted == [local_b]
    assert db.get_session_draft(hub.id)["text"] == local_a
    assert await evaluate(a, "demoView.composer.text()") == local_a
    print("PASS: two real browser profiles, simultaneous/offline collisions, presence, caret/selection/height, review, reload, refused send and retry", flush=True)


async def screenshots(instance):
    assets = BASE / "assets"
    await until(instance, "document.querySelectorAll('.task-tab .prompt-status-label').length === 2")
    for width, height, scale, name in [(1440, 900, 1, "desktop"), (390, 844, 2, "mobile")]:
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": scale,
            "mobile": name == "mobile"}, session=instance.page_session)
        if name == "desktop":
            await evaluate(instance, "openSearchTab(null, 'dashboard'); splitTabIntoPane('search',workspacePaneForTab('s:0:1').id,'right'); renderTabs(); true")
        else:
            await evaluate(instance, "closeTab('search'); activateTab('s:0:1'); true")
        for theme in ("dark", "light"):
            await evaluate(instance, "applyTheme(" + json.dumps(theme) + "); demoView.sharedDraft.count=0; demoView.sharedDraft.paint(); demoView.composer.ta.blur(); demoView.scrollBottom(true); true")
            await asyncio.sleep(.3)
            data = await instance.call("Page.captureScreenshot", {"format": "png"}, session=instance.page_session)
            (assets / (name + "-" + theme + ".png")).write_bytes(base64.b64decode(data["data"]))
    await evaluate(instance, "applyTheme('dark'); $('app').classList.add('side-open'); true")
    await asyncio.sleep(.3)
    data = await instance.call("Page.captureScreenshot", {"format": "png"}, session=instance.page_session)
    (assets / "mobile-dark-sidebar.png").write_bytes(base64.b64decode(data["data"]))
    print("PASS: regenerated five console screenshots with invented demo data", flush=True)


async def main(args):
    instances = []
    server = None
    try:
        with patch.object(webui, "_engines_payload", engines), patch.object(webui, "_node_user", return_value="mira"):
            app, sid = await fixture()
            server = web.AppRunner(app)
            await server.setup()
            site = web.TCPSite(server, "127.0.0.1", 0)
            await site.start()
            url = "http://127.0.0.1:{}/".format(site._server.sockets[0].getsockname()[1])
            print("Demo: " + url, flush=True)
            if args.serve:
                await asyncio.Event().wait()
                return
            config.set_value("browser.enabled", True)
            instances = [browser.Manager("TSTA"), browser.Manager("TSTB")]
            for instance in instances:
                await open_console(instance, url, sid)
            await checks(*instances, runner.hub(sid), args.screenshots)
            if args.screenshots:
                await screenshots(instances[0])
    finally:
        for instance in instances:
            await instance.stop("test finished")
        if server:
            await server.cleanup()
        shutil.rmtree(ROOT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", action="store_true")
    parser.add_argument("--serve", action="store_true")
    asyncio.run(main(parser.parse_args()))
