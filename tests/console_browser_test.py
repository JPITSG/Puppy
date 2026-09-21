#!/usr/bin/env python3
"""Real console profiles, embedded browser input, and anonymous screenshots.

Uses host Chromium through Puppy's debugging-pipe driver; no dependencies,
engine turns, or external services. --screenshots refreshes assets/ - the five
console captures and the four Timeouts panel previews docs/timeouts.md links -
and --serve keeps the demo open for interactive review (mira / preview-password).
"""
import argparse
import asyncio
import base64
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root("console-")
os.environ["PUPPY_DATA"] = str(ROOT / "data")

from aiohttp import web
from aiohttp.test_utils import TestServer
from puppy import auth, backends, browser, config, db, notices, runner, search, session_git, session_tasks, session_aliases, terminal
from puppy import web as webui
from puppy.drivers import all_drivers


async def engines(*args, **kwargs):
    observed = time.time()
    return [{**webui._engine_choices(driver), "installed": True, "auth": "ok",
             "version": "", "model_catalog_loaded": True,
             **({"usage_monitor": {"version": 1,
                 "provider": "demo-" + driver.key, "account": "a" * 64,
                 "bucket": "week", "window_minutes": 10080,
                 "sample": {"used_percent": 31 if driver.key == "claude" else 74,
                            "observed_at": observed, "resets_at": observed + 86400}}}
                if driver.key in ("claude", "codex") else {}),
             "model_options": [{"value": "", "label": "Default",
                                "effort_options": [{"value": "", "label": "Default"}]}]}
            for driver in all_drivers()]


# The Git marks of the invented projects. The node's own discovery would
# find no such directories, so its cache is seeded with these answers and
# every later look (the worker's pass, a focus refresh, the sheet's read)
# is answered from here. The garden is the heads-up: work its owner has
# not committed or pushed, with the rundown its tooltip reads and the paths
# and the commit its sheet lists; the atlas has no remote to push to; the
# notes are no repository.
DEMO_GIT = {"/home/mira/projects/" + folder: dict(record) for folder, record in (
    ("harbor", {"repo": True, "changes": 0, "unpushed": 0, "branch": "main",
                "staged": 0, "unstaged": 0, "untracked": 0, "conflicts": 0}),
    ("garden", {"repo": True, "changes": 3, "unpushed": 1, "branch": "main",
                "staged": 1, "unstaged": 1, "untracked": 1, "conflicts": 0}),
    ("atlas", {"repo": True, "changes": 0, "unpushed": None, "branch": "main",
               "staged": 0, "unstaged": 0, "untracked": 0, "conflicts": 0}),
    ("notes", {"repo": False}),
    ("harbor-task", {"repo": True, "changes": 2, "unpushed": 0}))}
# What the sheet lists behind those counts, per project.
DEMO_GIT_DETAIL = {"/home/mira/projects/" + folder: dict(detail) for folder, detail in (
    ("harbor", {"head": "9b1d2e4c" * 5, "upstream": "origin/main", "ahead": 0, "behind": 0,
                "remotes": ["origin"], "push_to": "origin/main", "paths": [], "commits": [],
                "more_paths": 0, "more_commits": 0}),
    ("garden", {"head": "4f2c9ab7" * 5, "upstream": "origin/main", "ahead": 1, "behind": 2,
                "remotes": ["origin"], "push_to": "origin/main",
                "paths": [{"kind": "staged", "code": "M.", "path": "src/planner.css"},
                          {"kind": "unstaged", "code": ".M", "path": "src/beds.js"},
                          {"kind": "untracked", "code": "??", "path": "notes/spring.md"}],
                "commits": [{"hash": "4f2c9ab", "subject": "Shade map for the north beds",
                             "author": "Mira Holt", "at": time.time() - 7200}],
                "more_paths": 0, "more_commits": 0}),
    ("atlas", {"head": "c07e5a19" * 5, "upstream": None, "ahead": None, "behind": None,
               "remotes": [], "push_to": None, "paths": [], "commits": [],
               "more_paths": 0, "more_commits": 0}))}


# The short log behind the sheet's History, per project: the garden's runs
# to 250 commits so the list pages, and its newest subject is long enough
# to overflow the box sideways.
DEMO_GIT_LOG = {"/home/mira/projects/harbor": 3, "/home/mira/projects/garden": 250,
                "/home/mira/projects/atlas": 12}
DEMO_GIT_LONG_SUBJECT = ("Shade map for the north beds, with the irrigation lines redrawn so every "
                         "raised bed drains toward the path and nothing pools by the shed door")


def demo_log(cwd, skip, limit):
    total = DEMO_GIT_LOG.get(str(cwd))
    if total is None:
        raise session_git.GitRefused("fatal: not a git repository (or any of the parent directories): .git")
    commits = []
    for at in range(skip, min(total, skip + limit)):
        commits.append({"hash": "{:07x}".format(0x4f2c9ab - at), "author": "Mira Holt",
                        "subject": DEMO_GIT_LONG_SUBJECT if at == 0 else "Entry {}".format(total - at),
                        "at": int(time.time()) - 3600 * at})
    return {"total": total, "skip": skip, "commits": commits, "more": skip + len(commits) < total}


def demo_git(cwd, listing=False):
    record = dict(DEMO_GIT.get(str(cwd), {"repo": False}), checked_at=time.time())
    if listing and record["repo"] is True:
        record["root"] = str(cwd)
        if str(cwd) in DEMO_GIT_DETAIL:
            record["detail"] = dict(DEMO_GIT_DETAIL[str(cwd)])
    return record


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
    db.meta_set(session_aliases.PREFIX + "A7K2", {"format": 1, "ref": db.node_uuid() + "/2"})
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
        ("user", {"text": "Make the dashboard easier to scan on a phone. Keep the activity feed and simplify the cards. Use @Session-Release-checklist-A7K2 for context."}),
        ("assistant", {"text": "I’ll check the card layout and navigation first, then test the narrow-screen view."}),
        ("tool_use", {"tool_use_id": "demo-read", "tool": "Read", "input": {"file_path": "/home/mira/projects/harbor/src/dashboard.css"}}),
        ("tool_result", {"tool_use_id": "demo-read", "content": "Dashboard layout and shared card styles"}),
        ("assistant", {"text": "The cards share one layout rule. I can reduce the visual noise without changing how the activity feed works."}),
        ("tool_use", {"tool_use_id": "demo-edit", "tool": "Edit", "input": {"file_path": "/home/mira/projects/harbor/src/dashboard.css", "old_string": "gap: 24px;", "new_string": "gap: 16px;"}}),
        ("tool_result", {"tool_use_id": "demo-edit", "content": "Updated the shared card spacing"}),
        ("tool_use", {"tool_use_id": "demo-test", "tool": "Bash", "input": {"command": "npm test -- dashboard --viewport=phone --check=card-spacing,activity-feed,navigation"}}),
        ("tool_result", {"tool_use_id": "demo-test", "content": "Command running in background"}),
        ("info", {"subtype": "task", "status": "completed", "task_id": "demo-layout-check",
                  "tool_use_id": "demo-test", "text": "12 layout tests passed, including card spacing, the activity feed and phone navigation"}),
        ("info", {"subtype": "session_task", "task_id": 8,
                  "text": "Task started: Activity feed headings"}),
        ("info", {"subtype": "session_task", "task_id": 8,
                  "text": "Conflict resolution started in task: Activity feed headings. Review its updated changes before applying."}),
        ("info", {"subtype": "session_task", "task_id": 8,
                  "text": "Task changes applied: Activity feed headings", "files": "M\tsrc/dashboard.css"}),
        ("info", {"subtype": "interrupted", "text": "Turn interrupted by user"}),
        ("info", {"subtype": "model_switch", "text": "engine model changed: preview-standard → preview-extended"}),
        ("assistant", {"text": "The dashboard now uses consistent spacing and clearer card headings. The activity feed stays visible on smaller screens.\n\nAll 12 layout tests passed, including the phone navigation checks."}),
        ("result", {"ok": True, "duration_ms": 12400, "usage": {"input_tokens": 8400, "output_tokens": 1250}}),
    ]
    for kind, data in events:
        db.add_event(sid, kind, data)
    search.reconcile()
    for cwd in DEMO_GIT:
        session_git._store(cwd, demo_git(cwd))
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


async def narrow_composer_checks(instance, capture=False):
    await instance.call("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 900, "deviceScaleFactor": 1,
        "mobile": False}, session=instance.page_session)
    await evaluate(instance, """openSearchTab(null, 'dashboard');
        splitTabIntoPane('s:0:1', workspacePaneForTab('search').id, 'left');
        demoView.status = 'running';
        demoView.setSteeringState({supported:true, ready:true, turn_id:'layout-test'});
        demoView.setSideQuestionState({supported:true, ready:true, turn_id:'layout-test'});
        demoView.updateRunState(); true""")
    try:
        for theme in ("dark", "light"):
            # The reported width, a smaller pane requiring wrapping, then a
            # wide pane that must regain its text labels without a reload.
            for ratio in (.27, .15, .7):
                await evaluate(instance, """applyTheme(%s); state.layout.ratio=%s; renderTabs();
                    (() => {const split=$('workspace-tree').firstElementChild;
                        applySplitRatio(state.layout, split.children[0], split.children[2], split.children[1]);})(); true""" %
                               (json.dumps(theme), ratio))
                await evaluate(instance, "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                result = await evaluate(instance, """(() => {
                    const v = demoView, box = v.composer.box.getBoundingClientRect();
                    const buttons = [v.askBtn, v.steerBtn, v.queueBtn, v.sendBtn];
                    return {buttons:buttons.map(b => {
                        const r = b.getBoundingClientRect();
                        const hit = document.elementFromPoint(r.x+r.width/2, r.y+r.height/2);
                        return {label:b.getAttribute('aria-label'), width:r.width,
                            inside:r.left>=box.left && r.right<=box.right &&
                                r.top>=box.top && r.bottom<=box.bottom,
                            hittable:!!hit && b.contains(hit)};
                    }), choices:[...v.root.querySelectorAll('.composer-enter-choice')].map(n=>!!n.getBoundingClientRect().width),
                    compact:getComputedStyle(v.askBtn.querySelector('.composer-action-label')).display==='none',
                    wrapped:v.sendBtn.getBoundingClientRect().top>v.composerMetaViewport.getBoundingClientRect().top};
                })()""")
                assert all(b["width"] > 0 and b["inside"] and b["hittable"]
                           for b in result["buttons"]), (theme, ratio, result)
                assert result["compact"] == (ratio < .7), result
                assert result["choices"] == [ratio == .7, ratio == .7], result
                if ratio == .15:
                    assert result["wrapped"], result
                if capture and ratio == .27:
                    shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                               session=instance.page_session)
                    (BASE / "data" / ("composer-narrow-fixed-" + theme + ".png")).write_bytes(
                        base64.b64decode(shot["data"]))
    finally:
        await evaluate(instance, """demoView.status='idle';
            demoView.setSteeringState({}); demoView.setSideQuestionState({});
            demoView.updateRunState(); closeTab('search'); applyTheme('dark'); true""")
    print("PASS: narrow desktop running controls stay visible and clickable, wrap when needed, and regain wide labels in both themes", flush=True)


async def composer_enter_checks(instance, capture=False):
    await evaluate(instance, """window.enterProbe={steer:demoView.steer,submit:demoView.submit,
        saved:lsGet('puppy.composer.enter-action'),calls:[]};
        demoView.steer=()=>enterProbe.calls.push('steer');
        demoView.submit=()=>enterProbe.calls.push('queue');
        lsDel('puppy.composer.enter-action');
        demoView.status='running';
        demoView.setSteeringState({supported:true,ready:true,turn_id:'enter-test'});
        demoView.updateRunState(); true""")
    try:
        for width in (1440, 700, 390):
            for scale in (1, 2):
                await instance.call("Emulation.setDeviceMetricsOverride", {
                    "width": width, "height": 900 if width == 1440 else 844,
                    "deviceScaleFactor": scale, "mobile": width != 1440},
                    session=instance.page_session)
                for theme in ("dark", "light"):
                    await evaluate(instance, "applyTheme(" + json.dumps(theme) + "); true")
                    result = await evaluate(instance, """(() => {
                        const actions=demoView.composer.enterActions;
                        return actions.map(({button,input})=>{
                            const b=button.getBoundingClientRect(), r=input.getBoundingClientRect();
                            const label=button.querySelector('.composer-action-label');
                            const text=label.getBoundingClientRect();
                            const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                            const target=input.parentNode.getBoundingClientRect();
                            return {shown:!!r.width,textShown:!!text.width,hit:hit===input,
                                target:target.width,mark:r.width,
                                top:r.top-b.top,right:b.right-r.right,
                                centered:Math.abs(text.top+text.height/2-b.top-b.height/2)<.1,
                                trimmed:getComputedStyle(label).textBoxTrim==='trim-both'};
                        });
                    })()""")
                    for row in result:
                        assert row["shown"] == row["textShown"] == (width == 1440), result
                        if row["shown"]:
                            assert row["hit"] and row["target"] >= 24 and row["mark"] == 12, result
                            assert 0 <= row["top"] <= 4 and 0 <= row["right"] <= 4, result
                            assert row["centered"] and row["trimmed"], result
                    if capture and width == 1440 and scale == 2:
                        clip = await evaluate(instance, """(() => {
                            const r=demoView.composerRow.getBoundingClientRect();
                            return {x:r.x,y:r.y-4,width:r.width,height:r.height+8,scale:2};
                        })()""")
                        shot = await instance.call("Page.captureScreenshot", {"format": "png", "clip": clip},
                                                   session=instance.page_session)
                        (BASE / "data" / ("composer-enter-" + theme + ".png")).write_bytes(base64.b64decode(shot["data"]))
        # Native click and keyboard events: choosing is not sending, Space
        # preserves exactly one checkbox, and Enter dispatches the chosen action.
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False},
            session=instance.page_session)
        point = await evaluate(instance, """(() => {
            const input=demoView.composer.enterActions[0].input, r=input.getBoundingClientRect();
            return {x:r.x+r.width/2,y:r.y+r.height/2};
        })()""")
        for kind in ("mousePressed", "mouseReleased"):
            await instance.call("Input.dispatchMouseEvent", {
                "type": kind, **point, "button": "left", "clickCount": 1}, session=instance.page_session)
        assert await evaluate(instance, "enterProbe.calls.length===0 && demoView.composer.enterActions[0].input.checked && !demoView.composer.enterActions[1].input.checked")
        for kind in ("keyDown", "keyUp"):
            await instance.call("Input.dispatchKeyEvent", {
                "type": kind, "key": " ", "code": "Space", "windowsVirtualKeyCode": 32}, session=instance.page_session)
        assert await evaluate(instance, "enterProbe.calls.length===0 && demoView.composer.enterActions[0].input.checked")
        await evaluate(instance, "demoView.composer.ta.focus(); true")
        for kind in ("keyDown", "keyUp"):
            await instance.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13}, session=instance.page_session)
        assert await evaluate(instance, "JSON.stringify(enterProbe.calls)==='[\"steer\"]'")
    finally:
        await evaluate(instance, """demoView.steer=enterProbe.steer; demoView.submit=enterProbe.submit;
            if(enterProbe.saved===null) lsDel('puppy.composer.enter-action');
            else lsSet('puppy.composer.enter-action',enterProbe.saved);
            demoView.status='idle'; demoView.setSteeringState({}); demoView.updateRunState();
            delete window.enterProbe; applyTheme('dark'); true""")
    print("PASS: Enter checkboxes are exclusive, clickable and keyboard usable; hidden on compact buttons; labels centered in both themes at 1x/2x", flush=True)


async def context_menu_checks(instance, capture=False):
    await evaluate(instance, """window.menuTestLinks=state.workspaceLinks;
        state.workspaceLinks=[{id:999, exec_backend:0, session_id:1,
            ws_backend:0, ws_name:'Atlas', root:'/home/mira/projects/harbor'}];
        window.menuTestSession={...demoView.session, has_native:true,
            workspace:{root:'/home/mira/projects/harbor', node:'Atlas'}}; true""")
    try:
        for width, height, name in [(1440, 900, "desktop"), (390, 844, "phone"),
                                    (390, 360, "short")]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": width == 390}, session=instance.page_session)
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(%s); true" % json.dumps(theme))
                # Let the resize event dismiss old floats before opening this menu.
                await evaluate(instance, "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                result = await evaluate(instance, """(() => {
                    if (innerWidth<=900) $('app').classList.add('side-open');
                    sessionContextMenu({preventDefault(){}, stopPropagation(){},
                        clientX:150, clientY:innerHeight-20}, 0, menuTestSession);
                    const menu=document.querySelector('.menu.dyn');
                    const r=menu.getBoundingClientRect();
                    const buttons=[...menu.querySelectorAll('button')];
                    const reach=b => {
                        b.scrollIntoView({block:'nearest'});
                        const q=b.getBoundingClientRect();
                        const hit=document.elementFromPoint(q.x+q.width/2,q.y+q.height/2);
                        return !!hit && b.contains(hit);
                    };
                    return {inside:r.left>=8 && r.top>=8 && r.right<=innerWidth-8 && r.bottom<=innerHeight-8,
                        scrolls:menu.scrollHeight>menu.clientHeight,
                        first:reach(buttons[0]),
                        archive:reach(buttons.find(b=>b.textContent==='Archive')),
                        last:reach(buttons.find(b=>b.textContent==='Delete session'))};
                })()""")
                assert result["inside"] and result["first"] and result["archive"] and result["last"], result
                assert result["scrolls"] == (name == "short"), result
                if capture:
                    shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                               session=instance.page_session)
                    (BASE / "data" / ("context-menu-fixed-" + name + "-" + theme + ".png")).write_bytes(
                        base64.b64decode(shot["data"]))
                assert await evaluate(instance, """(() => {
                    const menu=document.querySelector('.menu.dyn');
                    [...menu.querySelectorAll('button')].find(b=>b.textContent==='Dot color').click();
                    const picker=document.querySelector('.menu.color-menu');
                    const r=picker.getBoundingClientRect();
                    return picker.querySelectorAll('.swatch').length>0 &&
                        r.left>=8 && r.top>=8 && r.right<=innerWidth-8 && r.bottom<=innerHeight-8;
                })()""")
                await evaluate(instance, "closeAllMenus(null); closeDrawer(); true")
    finally:
        await evaluate(instance, """closeAllMenus(null); closeDrawer();
            state.workspaceLinks=menuTestLinks; delete window.menuTestLinks;
            delete window.menuTestSession; applyTheme('dark'); true""")
    print("PASS: session menus and color pickers fit desktop, phone and short screens; Archive/Delete remain reachable in both themes", flush=True)


async def workspace_footer_checks(instance, capture=False):
    await evaluate(instance, "window.footerTestLinks=state.workspaceLinks; true")
    try:
        for width, height, name in [(1440, 900, "desktop"), (390, 844, "phone"), (320, 640, "narrow")]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": width < 900}, session=instance.page_session)
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(%s); true" % json.dumps(theme))
                for backend in ("Atlas", "Garden laptop", "Garden-" + "workstation" * 8):
                    await evaluate(instance, """(() => {
                        const backend=%s;
                        state.workspaceLinks=[{id:999, exec_backend:0, session_id:1,
                            ws_backend:0, ws_name:backend, exec_name:'Studio',
                            root:'/home/mira/projects/harbor', state:'ok', conflicts:[]}];
                        modalWorkspaceLink(0, {...demoView.session,
                            workspace:{root:'/home/mira/projects/harbor', node:backend}});
                    })()""" % json.dumps(backend))
                    await evaluate(instance, "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                    result = await evaluate(instance, """(() => {
                        const modal=document.querySelector('.ws-link-modal');
                        const buttons=[...modal.querySelectorAll('.m-btns .btn:not(.hidden)')];
                        let previous=null;
                        return modal.scrollWidth<=modal.clientWidth && buttons.length===3 && buttons.every(b=>{
                            b.scrollIntoView({block:'nearest'});
                            const r=b.getBoundingClientRect();
                            const range=document.createRange();range.selectNodeContents(b);
                            const text=range.getBoundingClientRect();
                            const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
                            const fits=text.left>=r.left+14 && text.right<=r.right-14 &&
                                text.top>=r.top && text.bottom<=r.bottom && b.contains(hit);
                            const separated=!previous || r.top>=previous.bottom || r.left>=previous.right;
                            previous=r;
                            return fits && separated;
                        });
                    })()""")
                    assert result, (width, theme, backend)
                    if capture and backend == "Garden laptop":
                        shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                                   session=instance.page_session)
                        (BASE / "data" / ("workspace-footer-fixed-" + name + "-" + theme + ".png")).write_bytes(
                            base64.b64decode(shot["data"]))
                    await evaluate(instance, "document.querySelector('#wsl-close').click(); true")
    finally:
        await evaluate(instance, """document.querySelector('#wsl-close')?.click();
            state.workspaceLinks=footerTestLinks; delete window.footerTestLinks;
            applyTheme('dark'); true""")
    print("PASS: linked workspace footer labels fit and actions remain reachable with short and long backend names on desktop and phones in both themes", flush=True)


async def new_session_choices_checks(instance, capture=False):
    await evaluate(instance, "window.savedNativeChoices=prefersNativeChoices; true")
    try:
        for width, height, name in [(1440, 900, "desktop"), (390, 844, "phone"), (320, 640, "narrow")]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": width < 900}, session=instance.page_session)
            for native in (False, True):
                await evaluate(instance, "prefersNativeChoices=()=>%s; true" % json.dumps(native))
                for theme in ("dark", "light"):
                    await evaluate(instance, "applyTheme(%s); modalNewSession(0)" % json.dumps(theme))
                    await until(instance, "document.querySelector('#ns-model').options.length>0 && document.querySelector('#ns-perm').options.length>0")
                    await evaluate(instance, "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                    result = await evaluate(instance, """(() => {
                        const modal=document.querySelector('.new-session-modal');
                        const row=modal.querySelector('.field-row');
                        row.scrollIntoView({block:'nearest'});
                        const r=row.getBoundingClientRect();
                        return modal.scrollWidth<=modal.clientWidth && [...row.querySelectorAll('select')].every(s=>{
                            const b=s._choiceControl?.button || s;
                            const q=b.getBoundingClientRect();
                            const hit=document.elementFromPoint(q.x+q.width/2,q.y+q.height/2);
                            return q.left>=r.left && q.right<=r.right+1 && q.width>0 && b.contains(hit);
                        });
                    })()""")
                    assert result, (width, native, theme)
                    if capture:
                        shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                                   session=instance.page_session)
                        mode = "touch" if native else "mouse"
                        (BASE / "data" / ("new-session-fixed-" + name + "-" + mode + "-" + theme + ".png")).write_bytes(
                            base64.b64decode(shot["data"]))
                    if not native:
                        for field in ("model", "effort", "perm"):
                            assert await evaluate(instance, """(() => {
                                const c=document.querySelector('#ns-%s')._choiceControl;
                                c.button.click();
                                const r=c.menu.getBoundingClientRect();
                                const fits=r.left>=8 && r.right<=innerWidth-8 && r.top>=8 && r.bottom<=innerHeight-8;
                                closeChoiceMenu(true);return fits;
                            })()""" % field), (width, theme, field)
                    await evaluate(instance, "document.querySelector('#ns-cancel').click(); true")
    finally:
        await evaluate(instance, """document.querySelector('#ns-cancel')?.click();
            prefersNativeChoices=savedNativeChoices; delete window.savedNativeChoices;
            applyTheme('dark'); true""")
    print("PASS: New session model, effort and permission controls fit desktop and narrow windows with mouse and touch choices in both themes", flush=True)


async def model_alias_checks(instance):
    from puppy.drivers.claude import parse_model_catalog

    driver = next(item for item in all_drivers() if item.key == "claude")
    models = parse_model_catalog([
        {"value": "default", "displayName": "Default"},
        {"value": "fable", "displayName": "Fable", "resolvedModel": "claude-fable-5-1",
         "supportedEffortLevels": ["high", "max"]},
    ])
    saved = dict(config.get("engines.defaults.claude"))

    async def alias_engines(*args, **kwargs):
        payload = await engines()
        for engine in payload:
            if engine["key"] == "claude":
                engine["model_options"] = models
        return payload

    try:
        config.set_value("engines.defaults.claude", {
            "model": "fable[1m]", "effort": "max", "permission_mode": "auto"})
        with patch.object(driver, "model_options", return_value=models), \
                patch.object(driver, "model_catalog_loaded", return_value=True), \
                patch.object(webui, "_engines_payload", alias_engines):
            await evaluate(instance, "(async()=>{rememberEnginePayload(0, await api(0,'engines')); return true})()")
            for width, height, name in [(1440, 900, "desktop"), (390, 844, "phone")]:
                await instance.call("Emulation.setDeviceMetricsOverride", {
                    "width": width, "height": height, "deviceScaleFactor": 1,
                    "mobile": width < 900}, session=instance.page_session)
                for prefix, opening, wrap in [
                    ("nt", "modalNewTask(state.views['s:0:1'])", "nt-model-custom-wrap"),
                    ("ns", "modalNewSession()", "ns-model-custom-wrap"),
                    ("ed", "modalEngineDefaults(0,'claude')", "ed-custom-wrap"),
                ]:
                    await evaluate(instance, opening + "; true")
                    await until(instance, "document.querySelector('#%s-model')?.value==='fable[1m]'" % prefix)
                    result = await evaluate(instance, """(() => {
                        const select=document.querySelector('#%s-model');
                        return [select.selectedOptions[0].textContent,
                            document.querySelector('#%s-effort').value,
                            document.querySelector('#%s').classList.contains('hidden'),
                            [...select.options].filter(o=>o.textContent==='Fable').length];
                    })()""" % (prefix, prefix, wrap))
                    assert result == ["Fable", "max", True, 1], (name, prefix, result)
                    if prefix == "nt":
                        shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                                   session=instance.page_session)
                        (BASE / "data" / ("fable-task-" + name + ".png")).write_bytes(
                            base64.b64decode(shot["data"]))
                    await evaluate(instance, "document.querySelector('#%s-cancel').click(); true" % prefix)
    finally:
        config.set_value("engines.defaults.claude", saved)
        await evaluate(instance, """(async()=>{
            document.querySelector('#nt-cancel,#ns-cancel,#ed-cancel')?.click();
            rememberEnginePayload(0, await api(0,'engines')); return true})()""")
    print("PASS: saved Fable context alias is named in New task, New session and Engine defaults on desktop and phone; request and Max effort preserved", flush=True)


async def reply_image_checks(instance, capture=False):
    await evaluate(instance, r"""(() => {
        const canvas=document.createElement('canvas');canvas.width=1200;canvas.height=600;
        const c=canvas.getContext('2d');c.fillStyle='#19364c';c.fillRect(0,0,1200,600);
        c.fillStyle='#63c8ba';c.fillRect(40,40,1120,520);
        c.fillStyle='#19364c';c.font='48px sans-serif';c.fillText('Harbor image preview',100,180);
        c.font='32px sans-serif';c.fillText('1200 × 600 · fits the conversation',100,250);
        c.fillRect(100,320,280,120);c.fillRect(460,320,280,120);c.fillRect(820,320,280,120);
        const large=canvas.toDataURL();canvas.width=120;canvas.height=60;
        c.fillStyle='#63c8ba';c.fillRect(0,0,120,60);
        window.imageTestNode=demoView.buildEventNode({kind:'assistant',data:{text:
            'Large image preview\n\n![Harbor preview]('+large+')\n\nSmall images keep their natural size.\n\n![Small preview]('+canvas.toDataURL()+')'}});
        demoView.inner.appendChild(imageTestNode);
    })()""")
    try:
        await until(instance, "[...imageTestNode.querySelectorAll('img')].length===2 && [...imageTestNode.querySelectorAll('img')].every(i=>i.complete && i.naturalWidth>0)")
        for width, height, name in [(1440, 900, "desktop"), (390, 844, "phone"), (320, 640, "narrow")]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": width < 900}, session=instance.page_session)
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(%s); imageTestNode.scrollIntoView({block:'end'}); true" % json.dumps(theme))
                await evaluate(instance, "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                result = await evaluate(instance, """(() => {
                    const imgs=[...imageTestNode.querySelectorAll('img')];
                    const box=imageTestNode.querySelector('.md').getBoundingClientRect();
                    return demoView.scroll.scrollWidth<=demoView.scroll.clientWidth && imgs.every(i=>{
                        const r=i.getBoundingClientRect();
                        return r.left>=box.left && r.right<=box.right+1 &&
                            r.width<=i.naturalWidth && Math.abs(r.width/r.height-2)<.01;
                    }) && imgs[1].getBoundingClientRect().width===120;
                })()""")
                assert result, (width, theme)
                if capture:
                    shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                               session=instance.page_session)
                    (BASE / "data" / ("reply-image-fixed-" + name + "-" + theme + ".png")).write_bytes(
                        base64.b64decode(shot["data"]))
    finally:
        await evaluate(instance, "imageTestNode.remove(); delete window.imageTestNode; applyTheme('dark'); true")
    print("PASS: large reply images fit the transcript without distortion or horizontal overflow; small images retain their natural size in both themes", flush=True)


async def side_question_wrap_checks(instance, capture=False):
    await evaluate(instance, r"""(() => {
        const hash='0123456789abcdef'.repeat(4);
        window.wrapTestCard=asideCardNode({question:'Can you check this identifier?\n'+hash});
        fillAsideAnswer(wrapTestCard,{ok:true,text:'The identifier is:\n\n'+hash});
        demoView.inner.appendChild(wrapTestCard);
    })()""")
    try:
        for width, height, name in [(1440, 900, "desktop"), (390, 844, "phone"), (320, 640, "narrow")]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": width < 900}, session=instance.page_session)
            # Wait for the sidebar resize transition before taking evidence.
            await asyncio.sleep(.35)
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(%s); wrapTestCard.scrollIntoView({block:'end'}); true" % json.dumps(theme))
                await evaluate(instance, "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                result = await evaluate(instance, """(() => {
                    const card=wrapTestCard.getBoundingClientRect();
                    const nodes=[wrapTestCard,...wrapTestCard.querySelectorAll('.aside-q,.aside-a,.md')];
                    return demoView.scroll.scrollWidth<=demoView.scroll.clientWidth && nodes.every(n=>n.scrollWidth<=n.clientWidth) &&
                        [...wrapTestCard.querySelectorAll('.aside-q,.md p')].every(n=>{
                            const range=document.createRange();range.selectNodeContents(n);
                            return [...range.getClientRects()].every(r=>r.left>=card.left && r.right<=card.right);
                        });
                })()""")
                assert result, (width, theme)
                if capture:
                    shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                               session=instance.page_session)
                    (BASE / "data" / ("side-question-fixed-" + name + "-" + theme + ".png")).write_bytes(
                        base64.b64decode(shot["data"]))
    finally:
        await evaluate(instance, "wrapTestCard.remove(); delete window.wrapTestCard; applyTheme('dark'); true")
    print("PASS: long identifiers wrap inside side-question questions and answers without horizontal overflow on desktop and phones in both themes", flush=True)


async def status_color_checks(instance, capture=False):
    await evaluate(instance, "window.statusTestLinks=state.workspaceLinks; true")
    try:
        for width, height, name in [(1440, 900, "desktop"), (390, 844, "phone")]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": width < 900}, session=instance.page_session)
            await asyncio.sleep(.35)
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(%s); true" % json.dumps(theme))
                for surface in ("tasks", "review", "workspace"):
                    if surface == "tasks":
                        await evaluate(instance, """(() => {
                            window.statusTestModal=modal('<h2>Tasks</h2><div class="status-test-tasks"></div>');
                            const preview={overview:statusTestModal.m.querySelector('.status-test-tasks'),tab:{bid:0}};
                            SessionWorkspaceView.prototype.renderOverview.call(preview,[{
                                ...demoView.session,id:999,name:'Review dashboard spacing',
                                task:{state:'running',created_at:1,prompt:'Check the desktop and phone layouts.'}}]);
                        })()""")
                        selector = ".status-test-tasks .t-state"
                        tone = "busy"
                    elif surface == "review":
                        await evaluate(instance, """(async () => {
                            const savedApi=api;
                            api=async (bid,path,options) => path.endsWith('/tasks/999/review') ?
                                {files:'',diff:'',has_changes:false} : savedApi(bid,path,options);
                            try { await modalReviewTask({tab:{bid:0,sid:1}}, {
                                id:999,name:'Review dashboard spacing',task:{state:'failed'}}); }
                            finally { api=savedApi; }
                        })()""")
                        selector = ".task-review-facts .wsf-v.bad"
                        tone = "bad"
                    else:
                        await evaluate(instance, """state.workspaceLinks=[{id:999,exec_backend:0,session_id:1,
                            ws_backend:0,ws_name:'Garden laptop',exec_name:'Studio',state:'conflict',
                            conflicts:[],root:'/home/mira/projects/harbor'}];
                            modalWorkspaceLink(0,{...demoView.session,
                                workspace:{root:'/home/mira/projects/harbor',node:'Garden laptop'}}); true""")
                        selector = ".ws-link-modal .wsf-v.warn"
                        tone = "warn"
                    result = await evaluate(instance, """(() => {
                        const node=document.querySelector(%s);
                        const original=node.className;
                        const tones={ok:'--ok',warn:'--warn',bad:'--err',busy:'--acc2'};
                        const result=Object.entries(tones).every(([tone,token])=>{
                            node.classList.remove('ok','warn','bad','busy');node.classList.add(tone);
                            const probe=document.createElement('span');probe.style.color='var('+token+')';node.parentNode.appendChild(probe);
                            const match=getComputedStyle(node).color===getComputedStyle(probe).color;
                            probe.remove();return match;
                        });
                        node.className=original;return result && node.classList.contains(%s);
                    })()""" % (json.dumps(selector),json.dumps(tone)))
                    assert result, (width, theme, surface)
                    await evaluate(instance, "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                    if capture:
                        shot=await instance.call("Page.captureScreenshot", {"format":"png"},session=instance.page_session)
                        (BASE / "data" / ("status-colors-fixed-"+surface+"-"+name+"-"+theme+".png")).write_bytes(base64.b64decode(shot["data"]))
                    await evaluate(instance, """document.querySelector('#tr-close')?.click();
                        document.querySelector('#wsl-close')?.click();
                        if(window.statusTestModal){statusTestModal.close();delete window.statusTestModal;} true""")
    finally:
        await evaluate(instance, "state.workspaceLinks=statusTestLinks; delete window.statusTestLinks; applyTheme('dark'); true")
    print("PASS: task, review and workspace statuses use all four shared tone colors on desktop and phone in both themes", flush=True)


async def vnc_throughput_checks(instance, capture=False):
    await evaluate(instance, """(() => {
        window.vncPreview = modal('<h2>Remote screen · demo</h2>');
        vncPreview.m.style.setProperty('--modal-w', 'var(--modal-w-wide)');
        window.vncDemo = new VncView({bid:0, vncId:'A8AR', vncHost:'desk.example'});
        vncDemo.root.classList.add('on');
        vncDemo.root.style.position='relative';
        vncDemo.root.style.height='280px';
        vncPreview.m.appendChild(vncDemo.root);
        if (getComputedStyle(vncDemo.frameStats).display!=='none' ||
            getComputedStyle(vncDemo.throughputText).display!=='none')
            throw new Error('Unavailable VNC statistics must be hidden');
        vncDemo.connected=true; vncDemo.viewerActive=true;
        vncDemo.renderIdentity(); vncDemo.setSize(1280,800);
        vncDemo.recordFrame();
        vncDemo.handleMessage({type:'throughput',bytes_per_second:1572864});
    })()""")
    try:
        await until(instance, "!vncDemo.frameStats.classList.contains('hidden')")
        for width, height, name in [(1440, 900, "desktop"), (390, 844, "phone")]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": name == "phone"}, session=instance.page_session)
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(%s); vncDemo.throughputText.scrollIntoView({block:'nearest',inline:'end'}); true" % json.dumps(theme))
                await asyncio.sleep(.25)
                assert await evaluate(instance, """(() => {
                    const p=vncDemo.throughputText, r=p.getBoundingClientRect();
                    const stats=p.previousElementSibling, s=stats.getBoundingClientRect();
                    const viewport=vncDemo.meta.getBoundingClientRect();
                    return p.textContent==='1.5 MiB/s' && stats.contains(vncDemo.fpsText) &&
                        r.height===s.height && r.top===s.top && r.left>s.right &&
                        r.right<=viewport.right && r.left>=viewport.left &&
                        document.documentElement.scrollWidth<=innerWidth;
                })()"""), (width, theme)
                if capture:
                    shot = await instance.call("Page.captureScreenshot", {"format":"png"},
                                               session=instance.page_session)
                    (BASE / "data" / ("vnc-throughput-" + name + "-" + theme + ".png")).write_bytes(
                        base64.b64decode(shot["data"]))
        assert await evaluate(instance, """(() => {
            vncDemo.handleMessage({type:'gone',reason:'The VNC server closed the connection'});
            const frame=new ArrayBuffer(14), head=new DataView(frame);
            head.setUint8(0,1); head.setUint16(6,1,true); head.setUint16(8,1,true);
            vncDemo.applyFrame(frame);
            return !vncDemo.connected && vncDemo.stateText.textContent==='Disconnected' &&
                vncDemo.isDead() && getComputedStyle(vncDemo.throughputText).display==='none';
        })()"""), "late pixels must not erase a visible disconnect"
    finally:
        await evaluate(instance, "vncDemo.destroy(); vncPreview.close(); applyTheme('dark'); true")
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width":1440,"height":900,"deviceScaleFactor":1,"mobile":False},
                            session=instance.page_session)
    print("PASS: VNC throughput pill beside dimensions/FPS on desktop and phone in both themes", flush=True)


async def vnc_connection_checks(instance):
    await evaluate(instance, """(() => {
        window.vncOriginalApi=api;
        window.vncRequests=[];
        api=(bid,path,opts={}) => {
            if(path==='vnc/instances' && opts.method==='POST') {
                vncRequests.push({bid,path,opts});
                return new Promise((resolve,reject) => {window.vncRejectConnect=reject;});
            }
            if(path.startsWith('vnc/connect/') && opts.method==='DELETE') {
                vncRequests.push({bid,path,opts});
                vncRejectConnect(new Error('VNC connection cancelled'));
                return Promise.resolve({ok:true});
            }
            return vncOriginalApi(bid,path,opts);
        };
    })()""")
    try:
        for width, height in [(1440, 900), (390, 844)]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width":width,"height":height,"deviceScaleFactor":1,"mobile":width==390},
                session=instance.page_session)
            for dismiss in ["document.querySelector('#nv-cancel').click()",
                            "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
                            "document.querySelector('#nv-form').closest('.modal-backdrop').dispatchEvent(new MouseEvent('mousedown',{bubbles:true}))"]:
                assert await evaluate(instance, """(() => {
                    vncRequests.length=0; modalNewVnc();
                    document.querySelector('#nv-host').value='screen.example';
                    document.querySelector('#nv-form').requestSubmit();
                    const cancel=document.querySelector('#nv-cancel');
                    const r=cancel.getBoundingClientRect();
                    return !cancel.disabled && document.querySelector('#nv-go').disabled &&
                        r.width>0 && r.height>0 && r.bottom<=innerHeight;
                })()"""), (width, "Cancel must stay enabled and visible")
                await evaluate(instance, dismiss + "; true")
                await until(instance, "!document.querySelector('#nv-form') && vncRequests.length===2")
                assert await evaluate(instance, "vncRequests[1].path==='vnc/connect/'+vncRequests[0].opts.body.request_id")
    finally:
        await evaluate(instance, "api=vncOriginalApi; delete window.vncOriginalApi; true")
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width":1440,"height":900,"deviceScaleFactor":1,"mobile":False},
            session=instance.page_session)
    print("PASS: VNC Connect stays cancellable by button, Escape and backdrop on desktop and phone", flush=True)


async def operation_cancellation_checks(instance):
    await evaluate(instance, """(() => {
        window.cancelOriginalFetch=fetch;
        fetch=(url,opts={}) => {
            if (url==='/api/cancel-fixture')
                return new Promise(resolve => {window.cancelFinish=() => resolve(new Response(
                    JSON.stringify({error:'Operation cancelled',cancelled:true}),{status:409}));});
            if (url.startsWith('/api/operations/')) {
                if(opts.method!=='DELETE')
                    return Promise.resolve(new Response(JSON.stringify({state:'running'})));
                window.cancelSent=true;
                return Promise.resolve(new Response(JSON.stringify({state:'cancelling'})));
            }
            return cancelOriginalFetch(url,opts);
        };
    })()""")
    try:
        for width, height in [(1440, 900), (390, 844)]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width":width,"height":height,"deviceScaleFactor":1,"mobile":width==390},
                session=instance.page_session)
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(%s); true" % json.dumps(theme))
                for dismiss in ["document.querySelector('.operation-cancel').click()",
                                "history.back()",
                                "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))",
                                "document.querySelector('.operation-cancel').closest('.modal-backdrop').dispatchEvent(new MouseEvent('mousedown',{bubbles:true}))"]:
                    assert await evaluate(instance, """(() => {
                        window.cancelSent=false; window.cancelOutcome=false;
                        api(0,'cancel-fixture',{method:'POST',operation:'Preparing task'}).catch(
                            error => {window.cancelOutcome=error.cancelled;});
                        return !document.querySelector('.operation-cancel');
                    })()"""), (width, theme, "the dialog waits out its delay before interrupting")
                    await until(instance, "!!document.querySelector('.operation-cancel')")
                    assert await evaluate(instance, """(() => {
                        const button=document.querySelector('.operation-cancel'), r=button.getBoundingClientRect();
                        return !button.disabled && document.activeElement===button && r.width>0 &&
                            r.bottom<=innerHeight && document.documentElement.scrollWidth<=innerWidth;
                    })()"""), (width, theme, "Cancel must be focused, visible and reachable")
                    await evaluate(instance, dismiss + "; true")
                    await until(instance, "cancelSent && document.querySelector('.operation-cancel').disabled")
                    assert await evaluate(instance, "!cancelOutcome && document.querySelector('.modal-copy').textContent.includes('waiting for cleanup')")
                    await evaluate(instance, "cancelFinish(); true")
                    await until(instance, "cancelOutcome && !document.querySelector('.operation-cancel')")
    finally:
        await evaluate(instance, "fetch=cancelOriginalFetch; delete window.cancelOriginalFetch; applyTheme('dark'); true")
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width":1440,"height":900,"deviceScaleFactor":1,"mobile":False},
            session=instance.page_session)
    print("PASS: deferred progress dialog, then Cancel, Escape and backdrop stay visible through cleanup on desktop and phone in both themes", flush=True)


async def identity_pill_checks(instance, capture=False):
    await evaluate(instance, """(() => {
        window.identityStops=[];
        window.identityPreview=modal('<h2>Browser and terminal IDs</h2><p class="modal-copy">Component comparison · anonymous preview</p>');
        for(const [label,id] of [['Browser','A7K2'],['Terminal','B8L3']]) {
            const meta=el('div','br-meta edge-scroll-viewport'+(label==='Terminal'?' term-meta':''));
            const strip=el('div','br-meta-scroll');meta.appendChild(strip);
            const pill=el('div','br-ident');pill.setAttribute('aria-label',label+' identity');
            pill.append(el('span','br-ident-label',label),el('span','br-id',id));
            const button=el('button','icon-btn br-copy-id'+(label==='Terminal'?' term-copy-id':''));
            button.type='button';button.setAttribute('aria-label','Copy '+label+' ID');
            button.appendChild(copyIcon());pill.appendChild(button);strip.appendChild(pill);
            const owner=el('button','br-owner');
            owner.appendChild(el('span','br-owner-text','Harbor accessibility and dashboard navigation review for the autumn release'));
            strip.appendChild(owner);
            if(label==='Browser') strip.appendChild(el('span','br-stats','813 × 961 · 60.0 FPS'));
            identityPreview.m.appendChild(meta);
            identityStops.push(wireMetadataScrolling(meta));
        }
    })()""")
    try:
        for width, height, name in [(1440, 900, "desktop"), (390, 844, "phone"), (320, 640, "narrow")]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width":width,"height":height,"deviceScaleFactor":1,"mobile":width<900},session=instance.page_session)
            await asyncio.sleep(.35)
            for theme in ("dark","light"):
                await evaluate(instance,"applyTheme(%s); true" % json.dumps(theme))
                result=await evaluate(instance,"""[...identityPreview.m.querySelectorAll('.br-ident')].every(p=>{
                    const b=p.querySelector('.br-copy-id'),r=p.getBoundingClientRect(),q=b.getBoundingClientRect();
                    const icon=b.querySelector('svg').getBoundingClientRect();
                    const owner=p.parentElement.querySelector('.br-owner').getBoundingClientRect();
                    const hit=document.elementFromPoint(q.x+q.width/2,q.y+q.height/2);
                    return r.height===28 && q.width===27 && q.top>=r.top+1 && q.bottom<=r.bottom-1 &&
                        q.left>=r.left+1 && q.right<=r.right-1 && owner.left>=r.right &&
                        icon.top>=q.top && icon.bottom<=q.bottom && b.contains(hit);
                })""")
                assert result,(width,theme)
                # Check actual layout and the document wheel hook together:
                # overflow stays one row, fades follow scrolling to both ends.
                assert await evaluate(instance, """[...identityPreview.m.querySelectorAll('.br-meta-scroll')].every(s=>{
                    const v=s.parentElement, css=getComputedStyle(s), top=s.firstElementChild.getBoundingClientRect().top;
                    const e=new WheelEvent('wheel',{deltaY:10000,bubbles:true,cancelable:true});
                    const fits=[...s.children].every(p=>Math.abs(p.getBoundingClientRect().top-top)<1);
                    const faded=parseFloat(v.style.getPropertyValue('--edge-scroll-right-fade-size'))>0;
                    s.dispatchEvent(e);
                    return fits && faded && e.defaultPrevented && css.overflowX==='auto' &&
                        css.touchAction.includes('pan-x') && css.maskImage.includes('linear-gradient');
                })"""), (width,theme)
                await until(instance, """[...identityPreview.m.querySelectorAll('.br-meta-scroll')].every(s=>
                    Math.abs(s.scrollLeft-(s.scrollWidth-s.clientWidth))<1 &&
                    parseFloat(s.parentElement.style.getPropertyValue('--edge-scroll-right-fade-size'))===0)""")
                assert await evaluate(instance, """[...identityPreview.m.querySelectorAll('.br-meta-scroll')].every(s=>{
                    const e=new WheelEvent('wheel',{deltaY:100,bubbles:true,cancelable:true});s.dispatchEvent(e);
                    const left=parseFloat(s.parentElement.style.getPropertyValue('--edge-scroll-left-fade-size'))>0;
                    s.dispatchEvent(new WheelEvent('wheel',{deltaY:-10000,bubbles:true,cancelable:true}));
                    return left && !e.defaultPrevented;
                })"""), (width,theme)
                await until(instance, """[...identityPreview.m.querySelectorAll('.br-meta-scroll')].every(s=>s.scrollLeft===0 &&
                    parseFloat(s.parentElement.style.getPropertyValue('--edge-scroll-left-fade-size'))===0)""")
                if capture:
                    shot=await instance.call("Page.captureScreenshot",{"format":"png"},session=instance.page_session)
                    (BASE / "data" / ("identity-pills-fixed-"+name+"-"+theme+".png")).write_bytes(base64.b64decode(shot["data"]))
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width":1440,"height":900,"deviceScaleFactor":1,"mobile":False},session=instance.page_session)
        await evaluate(instance,"identityPreview.m.querySelectorAll('.br-owner-text').forEach(p=>p.textContent='Harbor'); true")
        await until(instance,"""[...identityPreview.m.querySelectorAll('.br-meta-scroll')].every(s=>s.scrollWidth===s.clientWidth &&
            parseFloat(s.parentElement.style.getPropertyValue('--edge-scroll-right-fade-size'))===0)""")
        await evaluate(instance,"identityPreview.m.querySelector('.br-stats').textContent='Stream dimensions and frame rate '.repeat(8); true")
        await until(instance,"parseFloat(identityPreview.m.querySelector('.br-meta').style.getPropertyValue('--edge-scroll-right-fade-size'))>0")
    finally:
        await evaluate(instance,"identityStops.forEach(stop=>stop()); delete window.identityStops; identityPreview.close(); delete window.identityPreview; applyTheme('dark'); true")
    print("PASS: browser and terminal identity rows stay single-line with working wheel scrolling, boundary fades and Copy ID controls on desktop and phones in both themes",flush=True)


async def timer_error_checks(instance, capture=False):
    await evaluate(instance, """(() => {
        window.timerPreview=modal('<h2>Settings preview</h2>');
        window.timerView={renderGeneration:1};
        timerPreview.m.appendChild(SettingsView.prototype.timerSettingsCard.call(timerView,
            [{bid:0,name:'Studio'}],state.timers,1));
    })()""")
    try:
        for width,height,name in [(1440,900,"desktop"),(390,844,"phone")]:
            await instance.call("Emulation.setDeviceMetricsOverride",{"width":width,"height":height,"deviceScaleFactor":1,"mobile":width<900},session=instance.page_session)
            await asyncio.sleep(.35)
            for theme in ('dark','light'):
                await evaluate(instance,"applyTheme(%s); true" % json.dumps(theme))
                result=await evaluate(instance,"""(async () => {
                    const row=timerPreview.m.querySelector('.timer-row'), input=row.querySelector('input'),button=row.querySelector('button'),error=row.querySelector('.form-error');
                    const oldApi=api;let calls=0;
                    api=async ()=>{calls++;throw new Error('Preview save failure');};
                    try {
                        input.value='-1';await row.onsubmit({preventDefault(){}});
                        if(calls || error.classList.contains('hidden') || !error.textContent.includes('whole number')) return false;
                        timerView.timerSettingsSync();
                        if(input.value!=='-1' || error.classList.contains('hidden')) return false;
                        input.value=input.min;input.dispatchEvent(new Event('input'));
                        if(!error.classList.contains('hidden')) return false;
                        await row.onsubmit({preventDefault(){}});
                        if(calls!==1 || !error.textContent.includes('Preview save failure') || button.disabled) return false;
                        input.onkeydown({key:'Escape'});
                        if(!error.classList.contains('hidden')) return false;
                        input.value='-1';await row.onsubmit({preventDefault(){}});
                        row.scrollIntoView({block:'center'});
                        return !document.querySelector('.toast') && input.getAttribute('aria-invalid')==='true';
                    } finally {api=oldApi;}
                })()""")
                assert result,(width,theme)
                if capture:
                    shot=await instance.call('Page.captureScreenshot',{'format':'png'},session=instance.page_session)
                    (BASE/'data'/('timer-errors-fixed-'+name+'-'+theme+'.png')).write_bytes(base64.b64decode(shot['data']))
    finally:
        await evaluate(instance,"timerPreview.close(); delete window.timerPreview; delete window.timerView; applyTheme('dark'); true")
    print('PASS: timer validation and save failures stay inline, persist across repaint, clear on edit/Escape, and never toast',flush=True)


async def usage_error_checks(instance, capture=False):
    await evaluate(instance, """(() => {
        window.usagePreview=modal('<h2>Usage refresh</h2>');
        window.usageTestRow=SettingsView.prototype.usageRefreshRow.call({},'Studio',0);
        usagePreview.m.appendChild(usageTestRow.root);
    })()""")
    try:
        for width,height,name in [(1440,900,'desktop'),(390,844,'phone'),(320,640,'narrow')]:
            await instance.call('Emulation.setDeviceMetricsOverride',{'width':width,'height':height,'deviceScaleFactor':1,'mobile':width<900},session=instance.page_session)
            await asyncio.sleep(.35)
            for theme in ('dark','light'):
                await evaluate(instance,'applyTheme(%s); true' % json.dumps(theme))
                for message in ('The account refresh could not finish because the backend is temporarily unavailable. Check its connection and try again.', 'Request failed: '+('0123456789abcdef'*8)):
                    await evaluate(instance,'usageTestRow.update({enabled:true,minutes:5,last_error:%s}, "ok", true); true' % json.dumps(message))
                    result=await evaluate(instance,"""(() => {
                        const note=usageTestRow.root.querySelector('.usage-refresh-note');
                        const r=note.getBoundingClientRect(),range=document.createRange();range.selectNodeContents(note);
                        return note.scrollWidth<=note.clientWidth && note.scrollHeight<=note.clientHeight &&
                            [...range.getClientRects()].every(q=>q.left>=r.left && q.right<=r.right+1 && q.top>=r.top-1 && q.bottom<=r.bottom+1) &&
                            usagePreview.m.scrollWidth<=usagePreview.m.clientWidth;
                    })()""")
                    assert result,(width,theme,message)
                    if capture and message.startswith('The account'):
                        shot=await instance.call('Page.captureScreenshot',{'format':'png'},session=instance.page_session)
                        (BASE/'data'/('usage-error-fixed-'+name+'-'+theme+'.png')).write_bytes(base64.b64decode(shot['data']))
    finally:
        await evaluate(instance,"usagePreview.close(); delete window.usagePreview; delete window.usageTestRow; applyTheme('dark'); true")
    print('PASS: complete usage-refresh errors and long identifiers remain visible without overflow on desktop and phones in both themes',flush=True)


async def pane_resize_checks(instance):
    await instance.call("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 900, "deviceScaleFactor": 1,
        "mobile": False}, session=instance.page_session)
    await evaluate(instance, """openSearchTab(null, 'dashboard');
        splitTabIntoPane('search', workspacePaneForTab('s:0:1').id, 'right');
        openSessionTab(0, 2, state.sessions.find(s=>s.id===2));
        splitTabIntoPane('s:0:2', workspacePaneForTab('search').id, 'bottom'); true""")
    await until(instance, "state.views['s:0:2'].activeView().draftReady")
    await evaluate(instance, "new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")

    async def mouse(kind, x, y):
        await instance.call("Input.dispatchMouseEvent", {
            "type": kind, "x": x, "y": y, "button": "left",
            "buttons": 0 if kind == "mouseReleased" else 1,
            "clickCount": 1}, session=instance.page_session)

    async def verify(active=False):
        await until(instance, "!document.querySelector('.pane-size-badge') && "
                    "sizeDivider.classList.contains('active') === %s" % json.dumps(active))

    dimensions = "[...document.querySelectorAll('.workspace-pane')].map(p=>{const r=p.getBoundingClientRect(); return [r.width,r.height]})"

    try:
        for theme in ("dark", "light"):
            await evaluate(instance, "applyTheme(%s); true" % json.dumps(theme))
            for axis in ("row", "column"):
                point = await evaluate(instance, """(() => {
                    window.sizeDivider=document.querySelector('.workspace-split.%s > .splitter');
                    const r=sizeDivider.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};
                })()""" % axis)
                x, y = point["x"], point["y"]
                await mouse("mousePressed", x, y)
                await verify(True)
                before = await evaluate(instance, dimensions)
                x += 45 if axis == "row" else 0
                y += 45 if axis == "column" else 0
                await mouse("mouseMoved", x, y)
                await verify(True)
                after = await evaluate(instance, dimensions)
                assert before != after, (axis, before, after)
                await mouse("mouseReleased", x, y)
                await verify()
                # Cancellation, capture loss and leaving the window all clean up.
                for event in ("pointercancel", "lostpointercapture", "blur"):
                    await mouse("mousePressed", x, y)
                    await verify(True)
                    await evaluate(instance, """%s.dispatchEvent(new Event(%s)); true""" %
                                   ("window" if event == "blur" else "sizeDivider", json.dumps(event)))
                    await verify()
                    await mouse("mouseReleased", x, y)
                await evaluate(instance, "sizeDivider.dispatchEvent(new KeyboardEvent('keydown',{key:%s})); true" %
                               json.dumps("ArrowLeft" if axis == "row" else "ArrowUp"))
                await verify()
                await evaluate(instance, "sizeDivider.dispatchEvent(new MouseEvent('dblclick')); true")
                await verify()
        await evaluate(instance, "sizeDivider.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp'})); closeTab('s:0:2'); true")
        await verify()
    finally:
        await evaluate(instance, "closeTab('s:0:2'); closeTab('search'); applyTheme('dark'); delete window.sizeDivider; true")
    print("PASS: nested horizontal/vertical resizing without size badges in both themes; release, cancellation, capture loss, blur, keyboard, reset and rebuild cleanup", flush=True)


async def reading_place_checks(instance):
    """Scroll position memory across tabs, with real layout and display:none:
    an idle conversation left mid-transcript comes back to the same message at
    the same height, whatever width the box has meanwhile; a running turn, or
    one that ran while the tab was away, shows its newest message; Main and a
    task tab keep their places independently; closing another tab or
    re-selecting this one moves nothing; Back restores the place too."""
    metrics = lambda width: instance.call("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": 420, "deviceScaleFactor": 1, "mobile": False},
        session=instance.page_session)
    await metrics(1440)
    await evaluate(instance, "activateTab('s:0:1'); state.views['s:0:1'].select(1); window.placeView=state.views['s:0:1'].activeView(); true")
    await until(instance, "placeView.scroll.scrollHeight - placeView.scroll.clientHeight > 200")
    at = "placeView.scroll.scrollTop"
    tail = "placeView.scroll.scrollTop === placeView.scroll.scrollHeight - placeView.scroll.clientHeight"
    anchored = """(() => { const edge = placeView.scroll.getBoundingClientRect().top;
        const node = placeView.findEventNode(Number(placeAnchor.seq));
        return node.dataset.seq === placeAnchor.seq &&
            Math.abs(node.getBoundingClientRect().top - edge - placeAnchor.offset) < 1; })()"""
    spot = await evaluate(instance, """(() => {
        const box = placeView.scroll;
        box.scrollTop = Math.round((box.scrollHeight - box.clientHeight) / 2);
        const edge = box.getBoundingClientRect().top;
        const node = [...placeView.inner.children].find(n => n.dataset.seq && n.getBoundingClientRect().bottom > edge);
        window.placeAnchor = {seq: node.dataset.seq, offset: node.getBoundingClientRect().top - edge};
        return box.scrollTop; })()""")
    assert spot > 50, spot
    await evaluate(instance, "openSettingsTab(); true")
    assert await evaluate(instance, "placeView.scroll.clientHeight === 0 && !placeView.onScreen && placeView.place.seq === Number(placeAnchor.seq)")
    await evaluate(instance, "activateTab('s:0:1'); true")
    assert await evaluate(instance, at) == spot, "an idle conversation comes back to the reader's place"
    assert await evaluate(instance, anchored)
    await evaluate(instance, "closeTab('settings'); true")
    assert await evaluate(instance, at) == spot, "closing another tab moves nothing"
    await evaluate(instance, "activateTab('s:0:1'); true")
    assert await evaluate(instance, at) == spot, "re-selecting the tab moves nothing"
    # Main and a task tab keep their places independently.
    shown = await evaluate(instance, """(() => { const w = state.views['s:0:1']; window.placeTask = w.tasks()[0].id;
        w.openTask(placeTask); const task = w.activeView();
        return task !== placeView && task.scroll.clientHeight > 0 && task.onScreen && placeView.scroll.clientHeight === 0; })()""")
    assert shown
    await evaluate(instance, "state.views['s:0:1'].select(1); true")
    assert await evaluate(instance, at) == spot, "Main comes back to its place from a task tab"
    # The anchor, not the pixel: the box is a different width when the tab returns.
    await evaluate(instance, "openSettingsTab(); true")
    await metrics(900)
    await evaluate(instance, "activateTab('s:0:1'); true")
    assert await evaluate(instance, anchored), "the same message stands at the same height after a resize"
    await metrics(1440)
    # A running turn shows its newest message; so does one that ran while the tab was away.
    await evaluate(instance, "placeView.scroll.scrollTop = %d; placeView.status = 'running'; openSettingsTab(); activateTab('s:0:1'); placeView.status = 'idle'; true" % spot)
    assert await evaluate(instance, tail), "a running turn lands on the tail"
    await evaluate(instance, """placeView.scroll.scrollTop = %d; window.placeNewest = placeView.newestSeq; openSettingsTab();
        placeView.handle({type:'event', event:{seq: placeNewest + 1, kind:'info', ts: Date.now() / 1000,
            data:{subtype:'note', text:'A note that arrived while the tab was away'}}});
        activateTab('s:0:1'); true""" % spot)
    assert await evaluate(instance, tail), "newer messages than the place knew of land on the tail"
    # The note was never persisted: the node's own transcript takes the view
    # back, recorded as the Jump to latest control records it, so history's
    # current destination is the one being read from.
    await evaluate(instance, "placeView.returnToTail(); true")
    await until(instance, "!placeView._returning && placeView.newestSeq === placeNewest && !navigation.pending && !navigation.scheduled")
    # History restores the place as well.
    await evaluate(instance, "placeView.scroll.scrollTop = %d; navigationRemember(); openSettingsTab(); true" % spot)
    await until(instance, "!navigation.pending && !navigation.scheduled")
    await evaluate(instance, "history.go(-1); true")
    await until(instance, "state.active === 's:0:1' && !navigation.pending && !navigation.scheduled")
    assert await evaluate(instance, at) == spot, "Back restores the reading position"
    await evaluate(instance, "closeTab('settings'); delete window.placeView; delete window.placeAnchor; delete window.placeTask; delete window.placeNewest; true")
    await instance.call("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False},
        session=instance.page_session)
    print("PASS: reading place kept across tab switches for idle conversations, anchored through a resize, independent for Main and tasks, unmoved by closing another tab or re-selecting, tail for running turns and newer messages, restored by Back", flush=True)


async def task_strip_verbs_checks(instance):
    """The strip's two verbs for the selected task, Review and the bin, on
    the console's own icon-button face: absent with Main selected; both
    greyed with a running task selected; both live once the node reports
    that task stopped, standing between the Tasks button and + on the
    strip's 26px box with their glyphs centred in it and named for the
    task; the bin taking the error tone under a real hover while Review
    takes the ordinary one; a real click on Review opening the review sheet
    for that task and Cancel closing it with the focus back on the button;
    a real click on the bin opening the Remove task confirm named for that
    task with Cancel holding the first focus, Cancel leaving the task and
    the bin in place with the focus back on the bin; both verbs going grey
    the moment the node reports the task working, the bin keeping its place
    at the console's disabled look and taking no tone under the pointer."""
    workspace = "state.views['s:0:1']"
    hidden = lambda button: workspace + "." + button + ".classList.contains('hidden')"
    review_hidden, bin_hidden = hidden("reviewButton"), hidden("removeButton")
    review_greyed, bin_greyed = workspace + ".reviewButton.disabled", workspace + ".removeButton.disabled"
    tid = next(s["id"] for s in db.list_sessions() if s["name"] == "Card spacing")
    hub = runner.hub(tid)
    record = session_tasks.record(tid)

    async def click(x, y):
        for kind in ("mousePressed", "mouseReleased"):
            await instance.call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y,
                                "button": "left", "clickCount": 1}, session=instance.page_session)

    async def hover(x, y):
        await instance.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y},
                            session=instance.page_session)

    await evaluate(instance, "activateTab('s:0:1'); %s.select(1); true" % workspace)
    assert await evaluate(instance, review_hidden + " && " + bin_hidden), "Main offers neither verb"
    await evaluate(instance, "%s.openTask(%d); true" % (workspace, tid))
    await until(instance, "%s.selected === %d" % (workspace, tid))
    assert await evaluate(instance, "!%s && %s && !%s && %s" % (review_hidden, review_greyed, bin_hidden, bin_greyed)), \
        "a running task: Review greyed like the menu's row, the bin greyed in its place"
    try:
        hub.status = "idle"
        db.touch_session(tid, status="idle")
        session_tasks._save(tid, dict(record, outcome="ok", completed_at=time.time()))
        runner.broadcast_sessions()
        await until(instance, "!%s && !%s && !%s && !%s" % (review_hidden, review_greyed, bin_hidden, bin_greyed))
        row = await evaluate(instance, """(() => {
            const wrap = document.querySelector('.task-tab-actions');
            const buttons = [...wrap.children];
            const boxes = buttons.map(node => node.getBoundingClientRect());
            const mid = box => box.top + box.height / 2;
            const centre = box => [box.left + box.width / 2, mid(box)];
            /* every glyph's ink box stands on its button's centre */
            const centred = buttons.every(node => {
                const [bx, by] = centre(node.getBoundingClientRect());
                const [gx, gy] = centre(node.querySelector('svg').getBoundingClientRect());
                return Math.abs(bx - gx) < 1 && Math.abs(by - gy) < 1; });
            return {order: buttons.map(node => node.className.split(' ').find(cls => cls.startsWith('task-'))),
                    face: buttons.every(node => node.classList.contains('icon-btn') && node.type === 'button'),
                    left: boxes.every((box, i) => !i || boxes[i - 1].right <= box.left),
                    boxes: boxes.map(box => [Math.round(box.width), Math.round(box.height)]),
                    level: boxes.every(box => Math.abs(mid(box) - mid(boxes[0])) < 1),
                    centred,
                    labels: buttons.slice(1, 3).map(node => node.getAttribute('aria-label')),
                    review: centre(boxes[1]), bin: centre(boxes[2]),
                    away: [boxes[0].left - 40, mid(boxes[0])]}; })()""")
        assert row["order"] == ["task-overview-button", "task-review-button", "task-remove-button", "task-add-button"], row
        assert row["face"] and row["left"] and row["level"] and row["centred"], row
        assert row["boxes"] == [[26, 26]] * 4, row
        assert row["labels"] == ["Review Card spacing", "Remove Card spacing"], row
        # the tones under the pointer: the console's error tone on the bin,
        # the ordinary hover on Review - read against the tokens themselves.
        # Pipe-driven headless Chromium reports hover:none, so the desktop
        # media branch is switched on while its real :hover rules are read.
        tones = "(() => { const probe = document.createElement('span'); document.body.appendChild(probe);" \
                " probe.style.color = 'var(--err)'; const err = getComputedStyle(probe).color;" \
                " probe.style.color = 'var(--txt)'; const txt = getComputedStyle(probe).color; probe.remove();" \
                " const bin = getComputedStyle(%s.removeButton);" \
                " return {err, txt, review: getComputedStyle(%s.reviewButton).color," \
                " bin: bin.color, opacity: bin.opacity}; })()" % (workspace, workspace)
        hover_rules_on = """window.verbHoverRules=[...document.styleSheets].flatMap(s=>[...s.cssRules])
            .filter(r=>r.media && r.conditionText==='(hover: hover)');
            verbHoverRules.forEach(r=>r.media.mediaText='all'); true"""
        hover_rules_off = "verbHoverRules.forEach(r=>r.media.mediaText='(hover: hover)'); delete window.verbHoverRules; true"
        await evaluate(instance, hover_rules_on)
        try:
            await hover(*row["bin"])
            await until(instance, "(t => t.bin === t.err && t.review !== t.err && t.opacity === '1')(%s)" % tones)
            await hover(*row["review"])
            await until(instance, "(t => t.review === t.txt && t.bin !== t.err)(%s)" % tones)
            await hover(*row["away"])
        finally:
            await evaluate(instance, hover_rules_off)
        # Review by a real click: the sheet for that task, closed by Cancel
        await click(*row["review"])
        await until(instance, "!!document.querySelector('.task-review-modal')")
        await until(instance, "document.querySelector('.task-review-modal .form-error:not(.hidden)') !== null || "
                              "document.querySelector('.task-review-modal #tr-files').textContent !== 'Loading changes…'")
        sheet = await evaluate(instance, """(() => {
            const m = document.querySelector('.task-review-modal');
            const facts = [...m.querySelectorAll('.ws-fact')].map(row => [row.querySelector('.field-lbl').textContent,
                row.querySelector('.wsf-v').textContent]);
            return {facts: facts.slice(0, 2), top: modalStack[modalStack.length - 1].m === m}; })()""")
        assert sheet["facts"] == [["Task", "Card spacing"], ["State", "Review"]] and sheet["top"], sheet
        await evaluate(instance, "document.querySelector('#tr-close').click(); true")
        await until(instance, "!document.querySelector('.task-review-modal')")
        assert await evaluate(instance, "document.activeElement === %s.reviewButton" % workspace), \
            "Cancel hands the focus back to Review"
        # the bin by a real click: the confirm named for that task
        await click(*row["bin"])
        await until(instance, "!!document.querySelector('.remove-task-modal')")
        confirm = await evaluate(instance, """({subject: document.querySelector('.remove-task-modal .modal-subject').textContent,
            fold: document.querySelector('#rt-fold').checked,
            focus: document.activeElement === document.querySelector('#rt-no')})""")
        assert confirm == {"subject": "Card spacing", "fold": True, "focus": True}, confirm
        await evaluate(instance, "document.querySelector('#rt-no').click(); true")
        await until(instance, "!document.querySelector('.remove-task-modal')")
        assert await evaluate(instance, "%s.tasks().some(s => s.id === %d) && !%s && !%s && document.activeElement === %s.removeButton"
                              % (workspace, tid, bin_hidden, review_greyed, workspace)), \
            "Cancel keeps the task, both verbs and the bin's focus"
        hub.status = "running"
        db.touch_session(tid, status="running")
        session_tasks._save(tid, record)
        runner.broadcast_sessions()
        await until(instance, "%s && %s && !%s && !%s" % (bin_greyed, review_greyed, review_hidden, bin_hidden))
        # the greyed bin keeps its place on the strip at the console's
        # disabled look, and a real hover over it lands on the strip beneath
        # (pointer-events off) instead of painting the error tone
        greyed = await evaluate(instance, """(() => {
            const wrap = document.querySelector('.task-tab-actions');
            const boxes = [...wrap.children].map(node => node.getBoundingClientRect());
            const centre = box => [box.left + box.width / 2, box.top + box.height / 2];
            return {review: centre(boxes[1]), bin: centre(boxes[2]),
                    boxes: boxes.map(box => [Math.round(box.width), Math.round(box.height)])}; })()""")
        assert greyed["bin"] == row["bin"] and greyed["review"] == row["review"], (greyed, row)
        assert greyed["boxes"] == [[26, 26]] * 4, greyed
        await evaluate(instance, hover_rules_on)
        try:
            await hover(*row["bin"])
            await until(instance, "document.querySelector('.task-tab-actions:hover') !== null")
            assert await evaluate(instance, "(t => t.bin !== t.err && t.opacity === '0.4')(%s)" % tones), \
                "a greyed bin takes no tone under the pointer"
            await hover(*row["away"])
        finally:
            await evaluate(instance, hover_rules_off)
    finally:
        hub.status = "running"
        db.touch_session(tid, status="running")
        session_tasks._save(tid, record)
        runner.broadcast_sessions()
        await evaluate(instance, "%s.select(1); true" % workspace)
    print("PASS: the task strip's Review and bin stand between Tasks and + for the selected task only, both greyed while it works with the bin keeping its place and taking no tone, glyphs centred on the strip's box, the bin red under the pointer, real clicks opening the review sheet and the named Remove task confirm, Cancel keeping everything", flush=True)


async def drag_scroll_checks(instance):
    """A reorder drag scrolls its list, in real Chromium through the drag
    the browser itself starts: a session row held past the list's end, over
    the footer, scrolls the list to its end with the row riding the last
    slot shown and lands last when let go there; held past the top, over
    the New session button, it scrolls back and lands first; held inside
    the list's bottom band, the list moves and the slot follows the rows
    under the still pointer, and a cancelled drag puts the rows back; and
    a tab held past the strip's end, over the + button, scrolls the strip
    to its end and lands last."""
    original = db._session_order_lists(db.connect())
    extras = [db.create_session("Drag row %02d" % i, "claude", "/home/mira/projects/harbor",
                                "", "", "", "default") for i in range(30)]
    runner.broadcast_sessions()
    intercepted = []
    original_on_message = instance._on_message

    def on_message(message):
        if message.get("method") == "Input.dragIntercepted":
            intercepted.append(message.get("params") or {})
        return original_on_message(message)

    instance._on_message = on_message
    list_top = "document.querySelector('.side-scroll').scrollTop"
    list_end = "(document.querySelector('.side-scroll').scrollHeight - document.querySelector('.side-scroll').clientHeight)"
    rows = "[...document.querySelectorAll('#sess-groups .sess-item')]"
    dragged_at = rows + ".findIndex(node => node.classList.contains('dragging'))"
    marked = "document.querySelector('.side-scroll').classList.contains('reorder-scroll')"
    unpinned = lambda: db._session_order_lists(db.connect())[1]

    async def mouse(kind, x, y, **extra):
        await instance.call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, **extra},
                            session=instance.page_session)

    async def lift(selector):
        """Press on the element and move until the browser starts its drag."""
        await until(instance, "!dragSess && !dragTab && sessionOrderPending.size === 0")   # the last reorder has landed
        spot = await evaluate(instance, "(() => { const r = document.querySelector(%s).getBoundingClientRect();"
                              " return {x: r.left + r.width / 2, y: r.top + r.height / 2}; })()" % json.dumps(selector))
        intercepted.clear()
        await mouse("mouseMoved", spot["x"], spot["y"])
        await mouse("mousePressed", spot["x"], spot["y"], button="left", clickCount=1)
        for step in range(1, 8):
            await mouse("mouseMoved", spot["x"], spot["y"] + step * 6, button="left", buttons=1)
            await asyncio.sleep(.04)
        end = time.monotonic() + 5
        while not intercepted and time.monotonic() < end:
            await asyncio.sleep(.03)
        assert intercepted, "the browser started a drag on " + selector
        return spot, intercepted[0]["data"]

    async def carry(data, x, y, kind="dragOver"):
        """One drag event at the point; a move onto another element fires
        only dragenter there, the next one at the same point its dragover,
        as a real pointer's stream of moves would."""
        for _ in range(2 if kind == "dragOver" else 1):
            await instance.call("Input.dispatchDragEvent", {"type": kind, "x": x, "y": y, "data": data},
                                session=instance.page_session)

    async def hold_at(selector, data, spot, dy=0):
        """Enter the drag at the lifted row, then hold it at the element."""
        target = await evaluate(instance, "(() => { const r = document.querySelector(%s).getBoundingClientRect();"
                                " return {x: r.left + r.width / 2, y: r.top + r.height / 2}; })()" % json.dumps(selector))
        await carry(data, spot["x"], spot["y"] + 40, "dragEnter")
        await carry(data, target["x"], target["y"] + dy)
        return target

    async def let_go(data, at):
        await carry(data, at["x"], at["y"], "drop")
        await mouse("mouseReleased", at["x"], at["y"], button="left", clickCount=1)

    try:
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width": 1100, "height": 520, "deviceScaleFactor": 1, "mobile": False},
            session=instance.page_session)
        await until(instance, rows + ".length >= 35 && " + list_end + " > 600")
        await evaluate(instance, "document.querySelector('.side-scroll').scrollTop = 0; true")
        await instance.call("Input.setInterceptDrags", {"enabled": True}, session=instance.page_session)

        # past the end: the list scrolls to its end, the row riding the last slot shown
        spot, data = await lift("[data-session-key='0:2']")
        foot = await hold_at(".foot-row", data, spot)
        await until(instance, "!!document.querySelector('.sess-item.dragging') && " + marked)
        await asyncio.sleep(.3)
        early = await evaluate(instance, "({top: %s, slot: %s})" % (list_top, dragged_at))
        assert 0 < early["top"] < 600, early
        assert 1 < early["slot"] < 34, early
        await until(instance, list_top + " >= " + list_end + " - 1")
        assert await evaluate(instance, dragged_at + " === 34"), "the row is last once the list has scrolled to its end"
        assert await evaluate(instance, "document.querySelector('.foot-row').closest('.side').dataset.sessionReorderWired === '1'")
        await let_go(data, foot)
        await until(instance, "!document.querySelector('.sess-item.dragging') && !" + marked)
        end = time.monotonic() + 10
        while unpinned()[-1] != 2 and time.monotonic() < end:
            await asyncio.sleep(.05)
        assert unpinned()[-1] == 2, unpinned()
        await until(instance, rows + ".pop().dataset.sessionKey === '0:2'")
        assert await evaluate(instance, list_top + " >= " + list_end + " - 1"), "the list stays where the drag left it"

        # past the top: back up, and first
        spot, data = await lift("[data-session-key='0:2']")
        head = await hold_at("#btn-new-session", data, spot)
        await until(instance, list_top + " <= 0")
        assert await evaluate(instance, dragged_at + " === 0")
        await let_go(data, head)
        await until(instance, "!document.querySelector('.sess-item.dragging')")
        end = time.monotonic() + 10
        while unpinned()[0] != 2 and time.monotonic() < end:
            await asyncio.sleep(.05)
        assert unpinned()[0] == 2, unpinned()
        await until(instance, rows + "[0].dataset.sessionKey === '0:2'")

        # inside the bottom band: the list moves and the slot follows the still pointer
        spot, data = await lift("[data-session-key='0:2']")
        band = await hold_at(".side-scroll", data, spot)
        box = await evaluate(instance, "document.querySelector('.side-scroll').getBoundingClientRect().bottom")
        await carry(data, band["x"], box - 12)
        await until(instance, list_top + " > 40")
        slot = await evaluate(instance, dragged_at)
        assert slot > 0, slot
        await asyncio.sleep(.4)
        later = await evaluate(instance, "({top: %s, slot: %s})" % (list_top, dragged_at))
        assert later["slot"] > slot, (slot, later)
        await carry(data, band["x"], box - 12, "dragCancel")
        await mouse("mouseReleased", band["x"], box - 12, button="left", clickCount=1)
        await until(instance, "!document.querySelector('.sess-item.dragging') && !" + marked + " && " +
                    rows + "[0].dataset.sessionKey === '0:2'")
        assert unpinned()[0] == 2, "a cancelled drag changes nothing"

        # a tab held past the strip's end, over the + button
        await evaluate(instance, "for (const s of state.sessions.filter(s => s.name.startsWith('Drag row')).slice(0, 8))"
                       " openSessionTab(0, s.id, s); true")
        strip = "document.querySelector('.workspace-pane .tabbar > .tab-scroll > .tabs')"
        await until(instance, strip + ".scrollWidth > " + strip + ".clientWidth + 200")
        await evaluate(instance, strip + ".scrollLeft = 0; true")
        first = await evaluate(instance, strip + ".querySelector('.tab').dataset.tabId")
        spot, data = await lift(".workspace-pane .tabbar > .tab-scroll > .tabs > .tab")
        plus = await hold_at(".workspace-pane .tabbar .tab-add-wrap .icon-btn", data, spot)
        await until(instance, "!!document.querySelector('.tab.dragging') && " + strip + ".classList.contains('reorder-scroll')")
        await until(instance, strip + ".scrollLeft >= " + strip + ".scrollWidth - " + strip + ".clientWidth - 1")
        assert await evaluate(instance, "[..." + strip + ".querySelectorAll('.tab')].pop().classList.contains('dragging')"), \
            "the tab is last once the strip has scrolled to its end"
        await let_go(data, plus)
        await until(instance, "!document.querySelector('.tab.dragging') && !" + strip + ".classList.contains('reorder-scroll')")
        assert await evaluate(instance, "workspacePaneForTab(%s).tabs.slice(-1)[0] === %s" % (json.dumps(first), json.dumps(first))), \
            "let go over +, the tab lands last"
    finally:
        instance._on_message = original_on_message
        await instance.call("Input.setInterceptDrags", {"enabled": False}, session=instance.page_session)
        await evaluate(instance, "for (const t of [...state.tabs]) if (t.type === 'session' && t.sid > 7) closeTab(t.id, false);"
                       " activateTab('s:0:1'); window.demoView = state.views['s:0:1'].activeView(); true")
        for sid in extras:
            db.delete_session(sid)
        pinned, current = db._session_order_lists(db.connect())
        db.reorder_sessions(original[0] + original[1], pinned + current, pinned)
        runner.broadcast_sessions()
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False},
            session=instance.page_session)
        await until(instance, rows + ".length === 5 && demoView.draftReady")
    print("PASS: a held session row scrolls the list past either end and lands there, the slot follows the "
          "rows under a still pointer, a cancelled drag puts them back, and a held tab scrolls its strip", flush=True)

async def session_mention_checks(instance):
    await evaluate(instance, "demoView.composer.set('', true); demoView.composer.ta.focus(); true")
    await type_text(instance, "@Session-P")
    await until(instance, "demoView.composer.mentionSession?.data?.sessions.length > 0")
    found = await evaluate(instance, "demoView.composer.mention.items.filter(i=>i.kind==='session-select').map(i=>i.label)")
    assert found == ["Phone navigation"], found
    await type_text(instance, "ho")
    found = await evaluate(instance, "demoView.composer.mention.items.filter(i=>i.kind==='session-select').map(i=>i.label)")
    assert found == ["Phone navigation"], found
    await evaluate(instance, """(() => {
        const c=demoView.composer;
        c.applyMention(c.mention.items.find(i=>i.kind==='session-select'));
        c.applyMention(c.mention.items.find(i=>i.kind==='session-insert'));
    })()""")
    value = await evaluate(instance, "demoView.composer.ta.value")
    import re
    assert re.fullmatch(r"@Session-Phone-navigation-[A-Z0-9]{4} ", value), value
    await evaluate(instance, "demoView.composer.set('', true); demoView.composer.hideMention(); true")
    # Rendered links resolve the alias through the real HTTP endpoint.
    await evaluate(instance, """(() => {
        const node=document.createElement('div');
        decorateMentionsInto(node,'@Session-Release-checklist-A7K2');
        window.shortMentionTest=node.firstChild;
    })()""")
    assert await evaluate(instance, "shortMentionTest.getAttribute('role')") == "link"
    await evaluate(instance, "shortMentionTest.onclick()")
    await until(instance, "!!state.views['s:0:2']")
    await evaluate(instance, "closeTab('s:0:2'); activateTab('s:0:1'); delete window.shortMentionTest; true")
    print("PASS: typed session-name prefixes, short insertion and clickable transcript references", flush=True)


async def workspace_move_checks(instance, capture=False):
    await evaluate(instance, "modalMoveWorkspace(0, {id: 999, name: 'Harbor sketch', workspace_kind: 'temporary'}); true")
    try:
        for width, height, name in [(1440, 900, 'desktop'), (390, 844, 'phone'), (320, 640, 'narrow')]:
            await instance.call('Emulation.setDeviceMetricsOverride', {
                'width': width, 'height': height, 'deviceScaleFactor': 2 if width < 900 else 1,
                'mobile': width < 900}, session=instance.page_session)
            for theme in ('dark', 'light'):
                await evaluate(instance, "applyTheme(%s); document.querySelector('#move-cwd').value='/home/mira/projects/harbor'; document.querySelector('#move-cwd').blur(); true" % json.dumps(theme))
                await asyncio.sleep(.2)
                assert await evaluate(instance, """(() => {
                    const m=document.querySelector('.modal'), go=m.querySelector('#move-go');
                    const r=m.getBoundingClientRect(), b=go.getBoundingClientRect();
                    return m.scrollWidth<=m.clientWidth && r.left>=0 && r.right<=innerWidth &&
                        b.left>=r.left && b.right<=r.right && go.form!==null;
                })()"""), (width, theme)
                if capture:
                    shot=await instance.call('Page.captureScreenshot', {'format':'png'}, session=instance.page_session)
                    (BASE/'data'/('workspace-move-'+name+'-'+theme+'.png')).write_bytes(base64.b64decode(shot['data']))
    finally:
        await evaluate(instance, "document.querySelector('#move-cancel').click(); applyTheme('dark'); true")
    print('PASS: scratch move dialog fits desktop and phones in both themes', flush=True)


async def message_reuse_checks(instance):
    await instance.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900,
        "deviceScaleFactor": 1, "mobile": False}, session=instance.page_session)
    # Pipe-driven headless Chromium reports hover:none. Enable the desktop
    # media branch in the fixture while exercising its actual :hover rules.
    await evaluate(instance, """window.reuseHoverRules=[...document.styleSheets].flatMap(s=>[...s.cssRules])
        .filter(r=>r.media && r.conditionText==='(hover: hover)');
        reuseHoverRules.forEach(r=>r.media.mediaText='all');
        window.reuseNode=demoView.buildEventNode({kind:'user',data:{text:'Earlier message'}});
        demoView.inner.appendChild(reuseNode); reuseNode.scrollIntoView({block:"center",behavior:"instant"}); true""")
    try:
        await instance.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 0, "y": 0}, session=instance.page_session)
        await asyncio.sleep(.3)
        layout = await evaluate(instance, """(() => {
            const reuse=reuseNode.querySelector('.user-reuse'), copy=reuseNode.querySelector('.user-copy');
            const r=reuse.getBoundingClientRect(), c=copy.getBoundingClientRect();
            return {hidden:getComputedStyle(reuse).opacity==='0', left:r.right<c.left,
                x:r.x+r.width/2,y:r.y+r.height/2};
        })()""")
        assert layout["hidden"] and layout["left"], layout
        assert await evaluate(instance, "document.elementFromPoint(%s,%s)===reuseNode.querySelector('.user-reuse') || reuseNode.querySelector('.user-reuse').contains(document.elementFromPoint(%s,%s))" % (layout["x"], layout["y"], layout["x"], layout["y"])), layout
        await instance.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": layout["x"], "y": layout["y"]}, session=instance.page_session)
        await until(instance, "getComputedStyle(reuseNode.querySelector('.user-reuse')).opacity==='1'")
        for draft in ("", "Existing draft\nsecond line"):
            result = await evaluate(instance, """(() => {
                const c=demoView.composer; c.set(%s);
                reuseNode.querySelector('.user-reuse').click();
                return {text:c.ta.value, start:c.ta.selectionStart, end:c.ta.selectionEnd,
                    focused:document.activeElement===c.ta};
            })()""" % json.dumps(draft))
            prefix = "Earlier message" + ("\n\n" if draft else "")
            assert result == {"text": prefix + draft, "start": len(prefix),
                              "end": len(prefix + draft), "focused": True}, result
    finally:
        await evaluate(instance, "reuseNode.remove(); delete window.reuseNode; reuseHoverRules.forEach(r=>r.media.mediaText='(hover: hover)'); delete window.reuseHoverRules; demoView.composer.set(''); true")
    print("PASS: message reuse is hidden until hover, sits left of Copy, focuses the composer and selects only the previous draft", flush=True)


async def spell_check_checks(instance, capture=False):
    """The bundled dictionary, the marks and autocorrect in a real browser."""
    await instance.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900,
        "deviceScaleFactor": 1, "mobile": False}, session=instance.page_session)
    keys = [{"type": "keyDown", "text": ch, "unmodifiedText": ch,
             "key": ch, "windowsVirtualKeyCode": ord(ch.upper())} for ch in "teh "]

    async def press(text):
        for ch in text:
            code = ord(ch.upper())
            await instance.call("Input.dispatchKeyEvent", {"type": "keyDown", "text": ch,
                "unmodifiedText": ch, "key": ch, "windowsVirtualKeyCode": code},
                session=instance.page_session)
            await instance.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch,
                "windowsVirtualKeyCode": code}, session=instance.page_session)

    async def click(x, y):
        for kind in ("mousePressed", "mouseReleased"):
            await instance.call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y,
                "button": "left", "clickCount": 1}, session=instance.page_session)

    try:
        # The console's own boxes never hand their text to the browser's checker.
        assert await evaluate(instance, """(() => {
            const ta = demoView.composer.ta;
            return ta.getAttribute('spellcheck') === 'false' &&
                ta.getAttribute('autocorrect') === 'off' && ta.spellcheck === false;
        })()""")
        await evaluate(instance, "setSpellPref('check', true); true")
        await until(instance, "!!spellDictionary.data")
        # It came over the wire as the gzip sibling aiohttp serves beside it.
        transfer = await evaluate(instance, """(() => {
            const entry = performance.getEntriesByType('resource')
                .find(item => item.name.endsWith('/static/dict/en.txt'));
            return entry ? {encoded: entry.encodedBodySize, decoded: entry.decodedBodySize} : null;
        })()""")
        assert transfer and transfer["decoded"] > 1000000, transfer
        assert transfer["encoded"] * 2 < transfer["decoded"], transfer

        # The marks are laid out exactly where the words are: on the field's
        # scrollport (its box less the scrollbar, which takes its width from
        # the text once a prompt overflows), with the same metrics, the same
        # wrapping and the same scroll. Every paragraph of the text is built
        # to end just past the field's edge and just short of the field's
        # full width, so a layer as wide as the whole field - which once
        # stood a line short of the text per paragraph and, scrolled to the
        # end, hung every mark that far below its word - would fail `wraps`
        # and `bottom`; `exposes` says the text has that shape first.
        layout = await evaluate(instance, """(() => {
            const c = demoView.composer, ta = c.ta;
            const ts = getComputedStyle(ta);
            /* the text's height wrapped in the field's own metrics at a width */
            const wrapped = (text, width) => {
              const mirror = document.createElement('div');
              for (const prop of CARET_MIRROR_STYLES) mirror.style[prop] = ts[prop];
              mirror.style.cssText += ';position:absolute;top:0;left:-9999px;visibility:hidden;' +
                'white-space:pre-wrap;overflow-wrap:break-word;box-sizing:border-box;width:' +
                width + 'px';
              mirror.textContent = text + '\\n';
              document.body.appendChild(mirror);
              const height = mirror.getBoundingClientRect().height;
              mirror.remove();
              return height;
            };
            /* the field at its full height carries the scrollbar */
            c.set('x\\n'.repeat(40));
            const inner = ta.clientWidth, outer = ta.offsetWidth;
            const one = wrapped('x', inner);
            const pool = ['teh', 'quick', 'brown', 'fox', 'jumpd', 'over', 'the', 'lazy', 'dog', 'sentance'];
            const bad = ['teh', 'jumpd', 'sentance'];
            const paragraphs = [];
            let next = 0, shaped = true;
            for (let i = 0; i < 14; i++) {
              /* words while they fit the field's line, then single letters -
                 which the checker never marks - until one no longer does */
              let line = pool[next++ % pool.length];
              for (;;) {
                const longer = line + ' ' + pool[next % pool.length];
                if (wrapped(longer, inner) > one) break;
                line = longer;
                next++;
              }
              while (wrapped(line + ' i', inner) === one) line += ' i';
              line += ' i';
              /* past the field's edge, within the layer's former width */
              if (wrapped(line, inner) === one || wrapped(line, outer) !== one) shaped = false;
              paragraphs.push(line);
            }
            const text = paragraphs.join('\\n');
            c.set(text);
            c.spellDraw(true);
            const layer = c.box.querySelector('.spell-layer');
            ta.scrollTop = 0;
            ta.dispatchEvent(new Event('scroll'));
            const marks = [...layer.querySelectorAll('.sp-bad')];
            const box = ta.getBoundingClientRect(), mine = layer.getBoundingClientRect();
            const ls = getComputedStyle(layer);
            const same = ['fontFamily','fontSize','lineHeight','letterSpacing','paddingTop',
                          'paddingLeft','paddingRight','paddingBottom','textBoxTrim','textBoxEdge']
                .every(prop => ts[prop] === ls[prop]);
            const first = marks[0].getBoundingClientRect();
            const last = marks[marks.length - 1].getBoundingClientRect();
            ta.scrollTop = ta.scrollHeight;
            ta.dispatchEvent(new Event('scroll'));
            const bottom = ta.scrollTop > 40 && layer.scrollTop === ta.scrollTop;
            ta.scrollTop = 40;
            ta.dispatchEvent(new Event('scroll'));
            return {words: marks.map(node => node.textContent),
                expected: text.split(/\\s+/).filter(word => bad.includes(word)), same,
                /* the scrollbar takes its width from the text here, and every
                   paragraph wraps into one more line for it */
                gutter: inner < outer && ta.clientWidth === inner,
                shaped, exposes: wrapped(text, outer) < wrapped(text, inner),
                /* the layer is the field's client box, not its border box */
                aligned: Math.abs(mine.x - (box.x + ta.clientLeft)) < .5 &&
                    Math.abs(mine.y - (box.y + ta.clientTop)) < .5 &&
                    Math.abs(mine.width - ta.clientWidth) < .5 &&
                    Math.abs(mine.height - ta.clientHeight) < .5,
                inside: first.width > 0 && first.height > 0 &&
                    first.top >= box.top - .5 && first.left >= box.left - .5 &&
                    first.bottom <= box.bottom + .5,
                /* the same text, wrapped into the same lines, to the pixel */
                wraps: ta.scrollHeight === layer.scrollHeight && ta.scrollHeight > ta.clientHeight,
                lines: last.top > first.top + 4,
                decoration: ls.textDecorationLine === 'none' &&
                    getComputedStyle(marks[0]).textDecorationLine.includes('underline') &&
                    getComputedStyle(marks[0]).textDecorationStyle === 'wavy',
                hidden: ls.color === 'rgba(0, 0, 0, 0)',
                /* the field paints over the layer: positioned, and after it */
                under: layer.nextElementSibling === ta && ts.position === 'relative',
                /* and the layer reaches the field's last scroll position */
                bottom,
                scrolled: ta.scrollTop > 0 && layer.scrollTop === ta.scrollTop,
                clipped: ls.overflow === 'hidden'};
        })()""")
        assert layout["words"] == layout["expected"] and len(layout["words"]) > 20, layout
        assert layout["gutter"] and layout["shaped"] and layout["exposes"], layout
        assert layout["same"] and layout["aligned"] and layout["inside"], layout
        assert layout["wraps"] and layout["lines"], layout
        assert layout["decoration"] and layout["hidden"] and layout["under"], layout
        assert layout["bottom"] and layout["scrolled"] and layout["clipped"], layout
        if capture:
            shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                       session=instance.page_session)
            (BASE / "data" / "spell-marks.png").write_bytes(base64.b64decode(shot["data"]))
        # A pane split down the middle gives the field a fractional width, and
        # the browser breaks its lines at exactly that width while clientWidth
        # reports it rounded. A run of one narrow letter, broken wherever the
        # edge falls, tells the two apart: the box widths are tried until one
        # wraps the run differently at the exact and the rounded width, and
        # the layer must then stand on the exact one.
        fraction = await evaluate(instance, """(() => {
            const c = demoView.composer, ta = c.ta;
            const ts = getComputedStyle(ta);
            const wrapped = (text, width) => {
              const mirror = document.createElement('div');
              for (const prop of CARET_MIRROR_STYLES) mirror.style[prop] = ts[prop];
              mirror.style.cssText += ';position:absolute;top:0;left:-9999px;visibility:hidden;' +
                'white-space:pre-wrap;overflow-wrap:break-word;box-sizing:border-box;width:' +
                width + 'px';
              mirror.textContent = text + '\\n';
              document.body.appendChild(mirror);
              const height = mirror.getBoundingClientRect().height;
              mirror.remove();
              return height;
            };
            const run = 'i'.repeat(4000);
            c.box.style.maxWidth = 'none';
            let found = null;
            for (const width of [896.5, 897.5, 898.5, 896.25, 897.25, 898.25, 896.75, 897.75, 898.75]) {
              c.box.style.width = width + 'px';
              c.set(run);
              const exact = ta.getBoundingClientRect().width - (ta.offsetWidth - ta.clientWidth);
              if (exact !== ta.clientWidth && wrapped(run, exact) !== wrapped(run, ta.clientWidth)) {
                found = {width, exact, rounded: ta.clientWidth};
                break;
              }
            }
            let result = {found};
            if (found) {
              c.spellDraw(true);
              const layer = c.box.querySelector('.spell-layer');
              const mine = layer.getBoundingClientRect(), box = ta.getBoundingClientRect();
              ta.scrollTop = ta.scrollHeight;
              ta.dispatchEvent(new Event('scroll'));
              result = {found, gutter: ta.clientWidth < ta.offsetWidth,
                exact: Math.abs(mine.width - found.exact) < 1 / 64 &&
                    Math.abs(mine.x - box.x) < 1 / 64 && Math.abs(mine.y - box.y) < 1 / 64,
                wraps: ta.scrollHeight === layer.scrollHeight && ta.scrollHeight > ta.clientHeight,
                bottom: ta.scrollTop > 40 && layer.scrollTop === ta.scrollTop};
            }
            c.box.style.width = '';
            c.box.style.maxWidth = '';
            c.set('');
            return result;
        })()""")
        assert fraction["found"] and fraction["gutter"], fraction
        assert fraction["exact"] and fraction["wraps"] and fraction["bottom"], fraction

        # A ctrl+j moves every line after it down and grows the box under it.
        # The marks go with their words in the same tick: the debounced scan is
        # taken away first, so nothing but the edit itself can have moved them.
        await evaluate(instance, """(() => {
            const c = demoView.composer, ta = c.ta;
            c.set('Teh quick brown fox jumpd over the lazy dog.');
            c.spellDraw(true);
            const layer = c.box.querySelector('.spell-layer');
            const top = ta.getBoundingClientRect().top;
            window.__before = [...layer.querySelectorAll('.sp-bad')]
                .map(node => node.getBoundingClientRect().top - top + ta.scrollTop);
            c.spellPaint = () => {};       // the scan is out of the picture
            ta.focus();
            ta.setSelectionRange(3, 3);
            return true;
        })()""")
        for phase in ("rawKeyDown", "keyUp"):
            await instance.call("Input.dispatchKeyEvent", {"type": phase, "modifiers": 2,
                "key": "j", "code": "KeyJ", "windowsVirtualKeyCode": 74},
                session=instance.page_session)
        broke = await evaluate(instance, """(() => {
            const c = demoView.composer, ta = c.ta;
            const layer = c.box.querySelector('.spell-layer');
            const box = ta.getBoundingClientRect(), mine = layer.getBoundingClientRect();
            const marks = [...layer.querySelectorAll('.sp-bad')];
            const now = marks.map(node =>
                node.getBoundingClientRect().top - box.top + ta.scrollTop);
            const line = parseFloat(getComputedStyle(ta).lineHeight);
            const was = window.__before;
            return {value: ta.value, words: marks.map(node => node.textContent),
                text: layer.textContent,
                aligned: Math.abs(mine.x - (box.x + ta.clientLeft)) < .5 &&
                    Math.abs(mine.y - (box.y + ta.clientTop)) < .5 &&
                    Math.abs(mine.width - ta.clientWidth) < .5 &&
                    Math.abs(mine.height - ta.clientHeight) < .5,
                wraps: ta.scrollHeight === layer.scrollHeight,
                stayed: Math.abs(now[0] - was[0]) < 1,
                shifted: Math.abs((now[1] - was[1]) - line) < 1};
        })()""")
        assert broke["value"] == "Teh\n quick brown fox jumpd over the lazy dog.", broke
        assert broke["words"] == ["Teh", "jumpd"] and broke["text"].startswith("Teh\n"), broke
        assert broke["aligned"] and broke["wraps"], broke
        assert broke["stayed"] and broke["shifted"], broke
        await evaluate(instance, "delete demoView.composer.spellPaint; true")

        # A word still being typed is not marked; the space that ends it, or the
        # caret leaving it, is what earns the underline.
        await evaluate(instance, "demoView.composer.set(''); demoView.composer.ta.focus(); true")
        await press("woord")
        await until(instance, "demoView.composer.ta.value === 'woord'")
        await asyncio.sleep(.4)
        assert await evaluate(instance, "!demoView.composer.box.querySelector('.sp-bad')"), \
            "the word under the fingers carries no mark"
        await press(" ")
        await until(instance, "demoView.composer.box.querySelector('.sp-bad')?.textContent === 'woord'")
        await press("hhigh")
        await asyncio.sleep(.4)
        assert await evaluate(instance, "demoView.composer.box.querySelectorAll('.sp-bad').length === 1")
        for phase in ("rawKeyDown", "keyUp"):
            await instance.call("Input.dispatchKeyEvent", {"type": phase, "key": "Home",
                "code": "Home", "windowsVirtualKeyCode": 36}, session=instance.page_session)
        await until(instance, "demoView.composer.box.querySelectorAll('.sp-bad').length === 2")

        # Autocorrect, typed on a real keyboard, and taken back by a real undo.
        await evaluate(instance, "setSpellPref('correct', true); demoView.composer.set(''); "
                                 "demoView.composer.ta.focus(); true")
        for event in keys:
            await instance.call("Input.dispatchKeyEvent", {**event}, session=instance.page_session)
            await instance.call("Input.dispatchKeyEvent",
                {"type": "keyUp", "key": event["key"],
                 "windowsVirtualKeyCode": event["windowsVirtualKeyCode"]},
                session=instance.page_session)
        await until(instance, "demoView.composer.ta.value === 'the '")
        assert await evaluate(instance, "demoView.composer.ta.selectionStart === 4")
        for phase in ("rawKeyDown", "keyUp"):
            await instance.call("Input.dispatchKeyEvent", {"type": phase, "modifiers": 2,
                "key": "z", "code": "KeyZ", "windowsVirtualKeyCode": 90},
                session=instance.page_session)
        await until(instance, "demoView.composer.ta.value.startsWith('teh')")
        assert await evaluate(instance, "demoView.composer.spellRefused.has('teh')")
        # And the phone keyboard's gesture: backspace, straight after a
        # correction typed on a real keyboard, gives the typed word back.
        await evaluate(instance, "demoView.composer.spellRefused.clear(); "
                                 "demoView.composer.set(''); demoView.composer.ta.focus(); true")
        for event in keys:
            await instance.call("Input.dispatchKeyEvent", {**event}, session=instance.page_session)
            await instance.call("Input.dispatchKeyEvent",
                {"type": "keyUp", "key": event["key"],
                 "windowsVirtualKeyCode": event["windowsVirtualKeyCode"]},
                session=instance.page_session)
        await until(instance, "demoView.composer.ta.value === 'the '")
        for phase in ("rawKeyDown", "keyUp"):
            await instance.call("Input.dispatchKeyEvent", {"type": phase, "key": "Backspace",
                "code": "Backspace", "windowsVirtualKeyCode": 8},
                session=instance.page_session)
        await until(instance, "demoView.composer.ta.value === 'teh '")
        assert await evaluate(instance, "demoView.composer.ta.selectionStart === 4"), \
            "the caret carries on where the typist left it"
        assert await evaluate(instance, "demoView.composer.spellRefused.has('teh')")
        await until(instance, "demoView.composer.box.querySelector('.sp-bad')?.textContent === 'teh'")
        # a second backspace is an ordinary backspace again
        for phase in ("rawKeyDown", "keyUp"):
            await instance.call("Input.dispatchKeyEvent", {"type": phase, "key": "Backspace",
                "code": "Backspace", "windowsVirtualKeyCode": 8},
                session=instance.page_session)
        await until(instance, "demoView.composer.ta.value === 'teh'")

        # The word the writer restored is neither corrected nor marked again.
        await evaluate(instance, """(() => { const c = demoView.composer;
            c.set(''); c.ta.value = 'teh'; c.ta.selectionStart = c.ta.selectionEnd = 3;
            c.ta.dispatchEvent(new InputEvent('input', {inputType:'insertText', data:'h'}));
            c.ta.value = 'teh '; c.ta.selectionStart = c.ta.selectionEnd = 4;
            c.ta.dispatchEvent(new InputEvent('input', {inputType:'insertText', data:' '}));
            return true; })()""")
        assert await evaluate(instance, "demoView.composer.ta.value === 'teh '")

        # The suggestion menu on a marked word, and the tools menu behind the
        # two switches, are ordinary console menus.
        await evaluate(instance, """(() => {
            const c = demoView.composer, ta = c.ta;
            c.set('a kittn here'); c.spellDraw(true);
            ta.selectionStart = ta.selectionEnd = 4;
            const spot = c.box.querySelector('.sp-bad').getBoundingClientRect();
            ta.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true,
                clientX: spot.x + spot.width / 2, clientY: spot.bottom}));
            return true;
        })()""")
        # the menu takes its place on the next frame, like every other float
        await evaluate(instance, "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        menu = await evaluate(instance, """(() => {
            const open = document.querySelector('.choice-menu.dyn');
            if (!open) return null;
            const rect = open.getBoundingClientRect();
            const rows = [...open.querySelectorAll('.choice-option')].map(row => row.textContent);
            return {rows, fits: rect.left >= 8 && rect.top >= 8 &&
                rect.right <= innerWidth - 8 && rect.bottom <= innerHeight - 8};
        })()""")
        assert menu and menu["fits"], menu
        assert "kitten" in menu["rows"] and "Add to dictionary" in menu["rows"], menu
        # It takes the keys without the browser painting a focus ring around
        # it, and a click anywhere else - the prompt box included - closes it.
        spot = await evaluate(instance, """(() => {
            const open = document.querySelector('.choice-menu.dyn');
            const menu = open.getBoundingClientRect(), ta = demoView.composer.ta.getBoundingClientRect();
            const x = ta.right - 24, y = ta.top + ta.height / 2;
            const covered = x >= menu.left && x <= menu.right && y >= menu.top && y <= menu.bottom;
            return {focused: open.contains(document.activeElement),
                outline: getComputedStyle(open).outlineStyle, x, y, covered};
        })()""")
        assert spot["focused"] and spot["outline"] == "none" and not spot["covered"], spot
        await click(spot["x"], spot["y"])
        await until(instance, "!document.querySelector('.choice-menu.dyn')")
        await evaluate(instance, """(() => {
            const c = demoView.composer, ta = c.ta;
            ta.selectionStart = ta.selectionEnd = 4;
            const spot = c.box.querySelector('.sp-bad').getBoundingClientRect();
            ta.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true,
                clientX: spot.x + spot.width / 2, clientY: spot.bottom}));
            return true;
        })()""")
        await until(instance, "!!document.querySelector('.choice-menu.dyn')")
        picked = await evaluate(instance, """(() => {
            const rows = [...document.querySelectorAll('.choice-menu.dyn .choice-option')];
            rows.find(row => row.textContent === 'kitten').click();
            return demoView.composer.ta.value;
        })()""")
        assert picked == "a kitten here", picked
        tools = await evaluate(instance, """(() => {
            closeAllMenus(null);
            setSpellPref('check', false);
            demoView.composer.box.querySelector('.tools-open').click();
            const menu = document.querySelector('.choice-menu.dyn');
            const rows = [...menu.querySelectorAll('.menu-check')].map(row => [
                row.querySelector('.menu-check-label').textContent,
                row.getAttribute('aria-checked'), row.disabled]);
            const marks = demoView.composer.box.querySelector('.spell-layer');
            closeAllMenus(null);
            return {rows, marks: !!marks};
        })()""")
        assert tools["rows"][-2:] == [["Spell check", "false", False],
                                      ["Autocorrect", "false", True]], tools
        assert not tools["marks"], "switching the checker off clears the marks"
    finally:
        await evaluate(instance, """closeAllMenus(null); setSpellPref('check', true);
            setSpellPref('correct', false); demoView.composer.spellRefused.clear();
            demoView.composer.set(''); true""")
    print("PASS: bundled dictionary served compressed, marks aligned with the text it "
          "underlines, autocorrect typed and taken back by undo and by backspace, "
          "suggestion and tools menus", flush=True)


async def icon_alignment_checks(instance):
    """Measure rendered centers: font baselines and optical shims can look
    plausible in one screenshot but move the same icon in another row."""
    await evaluate(instance, """(() => {
        window.iconProbe=el('div');
        demoView.inner.appendChild(iconProbe);
        const names=[...Object.keys(TOOL_ICON_DRAWERS),'custom_tool','exec_command',
            'apply_patch','view_image','collab_agent'];
        for(const tool of names) for(const status of ['running','done','failed']) {
            const card=toolCardNode({tool,input:{command:'Check the dashboard layout'},
                is_error:status==='failed'},status!=='running');
            card.dataset.probe=tool+' '+status;
            iconProbe.appendChild(card);
        }
        iconProbe.appendChild(taskArchiveNode({name:'Layout review',outcome:'applied',
            turns:2,folded_at:1,entries:[]},1,0));
        return true;
    })()""")
    try:
        for width, height in [(1440, 900), (390, 844)]:
            for scale in (1, 2):
                await instance.call("Emulation.setDeviceMetricsOverride", {
                    "width": width, "height": height, "deviceScaleFactor": scale,
                    "mobile": width == 390}, session=instance.page_session)
                for theme in ("dark", "light"):
                    await evaluate(instance, "applyTheme(" + json.dumps(theme) + "); true")
                    for opened in (False, True):
                        await evaluate(instance, """(() => {
                            for(const card of iconProbe.children)
                                card.classList.toggle('open',""" + json.dumps(opened) + """);
                            return true;
                        })()""")
                        await asyncio.sleep(.22)  # disclosure rotation has settled
                        result = await evaluate(instance, """(() => {
                            const failures=[]; let checked=0;
                            const center=n=>{const r=n.getBoundingClientRect();return r.top+r.height/2;};
                            const check=(mark,row,label)=>{
                                if(!mark || !row || !mark.getBoundingClientRect().height) return;
                                checked++;
                                const delta=center(mark)-center(row);
                                if(Math.abs(delta)>.05) failures.push({label,delta});
                            };
                            for(const head of iconProbe.querySelectorAll('.tool-head')) {
                                const name=head.parentNode.dataset.probe||'folded task';
                                for(const selector of ['.t-caret svg','.t-ico svg','.t-state'])
                                    check(head.querySelector(selector),head,name+' '+selector);
                                check(head.querySelector('.t-state svg, .t-state .spinner'),head,name+' status mark');
                                // Compare the visible chevron edge, including its stroke,
                                // with the icon's symmetric slot padding and the label.
                                // Equal flex gaps alone miss the arrow's empty SVG padding.
                                const arrow=head.querySelector('.t-caret svg');
                                const box=arrow.getBBox(), matrix=arrow.getScreenCTM();
                                const right=Math.max(...[box.x,box.x+box.width].flatMap(x=>
                                    [box.y,box.y+box.height].map(y=>new DOMPoint(x,y).matrixTransform(matrix).x)));
                                const stroke=parseFloat(getComputedStyle(arrow.firstElementChild).strokeWidth);
                                const edge=right+stroke/2*Math.hypot(matrix.a,matrix.c);
                                const icon=head.querySelector('.t-ico svg').getBoundingClientRect();
                                const label=head.querySelector('.t-name').getBoundingClientRect();
                                const before=icon.left-edge, after=label.left-icon.right;
                                if(Math.abs(before-after)>.05)
                                    failures.push({label:name+' horizontal spacing',before,after});
                            }
                            const pairs=[
                                ['.btn .btn-ico svg','.btn'], ['.icon-btn>svg','.icon-btn'],
                                ['.disclosure-toggle svg','.disclosure-toggle'],
                                ['.side-search-btn svg','.side-search-btn'],
                                ['.si-pin svg','.si-pin'], ['.si-git svg','.si-git'],
                                ['.si-notes svg','.si-notes'],
                                ['.t-close svg','.t-close'], ['.tab .t-dot svg','.t-dot'],
                                ['.composer-row .mini svg','.mini'],
                                ['.foot-node-act svg','.foot-node-act'],
                                ['.foot-engine-head>.foot-ico','.foot-engine-head'],
                                ['.si-row>.sess-dot','.si-row'],
                                ['.result-line svg','.result-line'],
                            ];
                            for(const [selector,parent] of pairs)
                                for(const mark of document.querySelectorAll(selector))
                                    check(mark,mark.closest(parent),selector);
                            return {checked,failures};
                        })()""")
                        assert result["checked"] >= 200, result
                        assert not result["failures"], (width, scale, theme, opened, result)
    finally:
        await evaluate(instance, "iconProbe.remove(); delete window.iconProbe; applyTheme('dark'); true")
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 900, "deviceScaleFactor": 1,
            "mobile": False}, session=instance.page_session)
    print("PASS: tool, task, result, sidebar, tab and control icons share their row's "
          "vertical center; tool/task headers have equal spacing around the icon "
          "in both themes, desktop/phone, at 1x/2x", flush=True)


async def scrollbar_corner_checks(instance, capture=False):
    """Read the rendered pixels of every kind of rounded scroll box: a thumb
    parked at either end of its bar begins after the box's own radius, the
    box's background fills the corner it would have sat in, and a square
    pane's thumb still runs to the edge. Both bars, both themes, 1x/2x."""
    await evaluate(instance, """(() => {
        const lines=Array.from({length:80},(_,i)=>'        line '+(i+1)).join('\\n');
        const wide='        '+'wide '.repeat(120);
        window.sbProbe=el('div');
        sbProbe.style.cssText='position:fixed;left:24px;top:24px;width:320px;z-index:2147483647;'+
            'display:flex;flex-direction:column;gap:12px;background:var(--bg)';
        document.body.appendChild(sbProbe);
        const place=(node,probe,css,parent)=>{
            node.dataset.probe=probe; node.style.cssText+=';'+css;
            (parent||sbProbe).appendChild(node); return node;
        };
        place(el('textarea'),'textarea','height:96px;resize:none').value=lines;
        const md=el('div','md'); sbProbe.appendChild(md);
        const code=el('pre'); code.appendChild(el('code',null,wide));
        place(code,'code block','height:56px',md);
        const diff=el('div','task-review-diff'); diff.appendChild(el('div','box-lines',wide+'\\n'+lines));
        place(diff,'review diff','height:96px;min-height:0');
        const menu=place(el('div','choice-menu'),'choice menu',
            'position:static;height:96px;width:320px;max-width:none;animation:none;box-shadow:none');
        for(let i=1;i<=40;i++) menu.appendChild(el('div',null,'row '+i)).style.paddingLeft='24px';
        const modal=place(el('div','modal'),'modal','height:120px;box-shadow:none');
        modal.appendChild(el('div',null,lines)).style.cssText='white-space:pre;padding-left:24px';
        place(el('div',null,lines),'square pane',
            'height:96px;overflow:auto;white-space:pre;background:var(--panel2)');
        // Sampled from a screenshot of one box: the image is decoded in the
        // page, and every coordinate is a CSS pixel of the box's border box.
        window.sbSample=async(data,points)=>{
            const img=new Image(); img.src='data:image/png;base64,'+data; await img.decode();
            const canvas=document.createElement('canvas');
            canvas.width=img.width; canvas.height=img.height;
            const ctx=canvas.getContext('2d',{willReadFrequently:true});
            ctx.drawImage(img,0,0);
            const k=img.width/points.width;
            return points.list.map(([x,y])=>
                [...ctx.getImageData(Math.floor((x+.5)*k),Math.floor((y+.5)*k),1,1).data].slice(0,3));
        };
        window.sbColor=value=>{
            const swatch=sbProbe.appendChild(el('div'));
            swatch.style.background=value;
            const rgb=getComputedStyle(swatch).backgroundColor; swatch.remove();
            return rgb.match(/[\\d.]+/g).slice(0,3).map(Number);
        };
        return true;
    })()""")
    geometry = """(() => {
        const node=sbProbe.querySelector('[data-probe=%s]'), cs=getComputedStyle(node);
        const r=node.getBoundingClientRect();
        return {x:r.x,y:r.y,width:r.width,height:r.height,
            border:[cs.borderTopWidth,cs.borderRightWidth,cs.borderBottomWidth,cs.borderLeftWidth].map(parseFloat),
            radius:parseFloat(cs.borderTopLeftRadius),
            vertical:node.scrollHeight>node.clientHeight, horizontal:node.scrollWidth>node.clientWidth,
            thumb:sbColor('var(--scrollbar-thumb)')};
    })()"""
    close = lambda a, b: all(abs(p - q) <= 3 for p, q in zip(a, b))

    async def sample(geom, points):
        clip = {"x": geom["x"], "y": geom["y"], "width": geom["width"], "height": geom["height"], "scale": 1}
        shot = await instance.call("Page.captureScreenshot", {"format": "png", "clip": clip},
                                   session=instance.page_session)
        return await evaluate(instance, "sbSample(%s, %s)" % (
            json.dumps(shot["data"]), json.dumps({"width": geom["width"], "list": points})))

    async def bar_checks(name, geom, axis, parked, label):
        # A bar's own axis (0 = vertical, 1 = horizontal), its start and end
        # corners, and the thumb after the inset - the box's own radius -
        # mirrored through `at`.
        top, right, bottom, left = geom["border"]
        inset = geom["radius"]
        length = geom["height"] if axis == 0 else geom["width"]
        lead = top if axis == 0 else left
        trail = bottom if axis == 0 else right
        across = (geom["width"] - right - 5) if axis == 0 else (geom["height"] - bottom - 5)
        at = (lambda along: [across, along]) if axis == 0 else (lambda along: [along, across])
        inner = geom["radius"] - lead
        # rows/columns of the corner that lie inside the box's inner curve
        clear = int(math.ceil(inner - math.sqrt(max(inner * inner - (inner - 5) ** 2, 0)))) + 1 if inner > 0 else 0
        corner = list(range(lead + clear, lead + inset - 1)) if parked == "start" else \
            list(range(length - trail - inset + 1, length - trail - clear))
        thumb_at = lead + inset + 3 if parked == "start" else length - trail - inset - 4
        # the box's own background: the same row inside the left padding for a
        # vertical bar, the middle of the bar's own row for a horizontal one
        reference = (lambda along: [max(geom["radius"], 12), along]) if axis == 0 else \
            (lambda along: [geom["width"] // 2, across])
        points = [at(along) for along in corner] + [reference(along) for along in corner] + [at(thumb_at)]
        pixels = await sample(geom, points)
        seen = pixels[:len(corner)]
        expected = pixels[len(corner):2 * len(corner)]
        thumb = pixels[-1]
        for along, pixel, background in zip(corner, seen, expected):
            assert close(pixel, background), (label, name, parked, "corner", along, pixel, background)
        assert close(thumb, geom["thumb"]), (label, name, parked, "thumb", thumb_at, thumb, geom["thumb"])
        if corner:
            assert not close(thumb, expected[0]), (label, name, parked, "thumb is not the background", thumb)

    boxes = ("textarea", "code block", "review diff", "choice menu", "modal", "square pane")
    try:
        for scale in (1, 2):
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": 1440, "height": 900, "deviceScaleFactor": scale, "mobile": False},
                session=instance.page_session)
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(" + json.dumps(theme) + "); true")
                await asyncio.sleep(.4)  # colour transitions have settled
                label = "%dx %s" % (scale, theme)
                for name in boxes:
                    geom = await evaluate(instance, geometry % json.dumps(name))
                    assert all(float(geom[key]).is_integer() for key in ("x", "y", "width", "height")), (name, geom)
                    assert (geom["radius"] > 0) == (name != "square pane"), (name, geom)
                    axes = [axis for axis, has in ((0, geom["vertical"]), (1, geom["horizontal"])) if has]
                    assert axes, (name, geom)
                    for axis in axes:
                        for parked in ("start", "end"):
                            if parked == "end" and len(axes) == 2:
                                continue  # that end meets the other bar, not a corner
                            await evaluate(instance, """(() => {
                                const node=sbProbe.querySelector('[data-probe=%s]');
                                node[%s]=%s; return true;
                            })()""" % (json.dumps(name), json.dumps("scrollTop" if axis == 0 else "scrollLeft"),
                                       "0" if parked == "start" else "1e6"))
                            await asyncio.sleep(.05)
                            await bar_checks(name, geom, axis, parked, label)
                if capture and scale == 2:
                    geom = await evaluate(instance, geometry % json.dumps("textarea"))
                    await evaluate(instance, "sbProbe.querySelector('[data-probe=textarea]').scrollTop=0; true")
                    clip = {"x": geom["x"], "y": geom["y"], "width": geom["width"], "height": geom["height"], "scale": 2}
                    shot = await instance.call("Page.captureScreenshot", {"format": "png", "clip": clip},
                                               session=instance.page_session)
                    (BASE / "data" / ("scrollbar-corner-" + theme + ".png")).write_bytes(base64.b64decode(shot["data"]))
    finally:
        await evaluate(instance, "sbProbe.remove(); delete window.sbProbe; delete window.sbSample; delete window.sbColor; applyTheme('dark'); true")
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 900, "deviceScaleFactor": 1,
            "mobile": False}, session=instance.page_session)
    print("PASS: a rounded scroll box's thumb starts after the box's radius at both ends of a "
          "vertical or horizontal bar (textarea, code block, review diff, choice menu, modal), "
          "the corner keeps the box's background, and a square pane's thumb runs to the edge, "
          "in both themes at 1x/2x", flush=True)


async def text_inset_checks(instance):
    """Read the rendered pixels of every kind of text box: the first line's
    letters stand as far below the top border, and the last line's baseline
    as far above the bottom one, as the text stands in from the left. A
    line box carries leading above and below its letters, so app.css trims
    it off the first and the last line and the padding above and below
    becomes the padding beside; where a font's letters then land is the
    font's own affair (the host's hinted mono renders its caps a pixel or
    two above their declared height, and a glyph has a side bearing), so
    every box is read against a trimmed line of its own text and font with
    no padding at all. The chat composer, with a typed line and with its
    placeholder alone - one trimmed line tall either way, the letters in the
    same place - and a textarea, a code block, the approval's command, the
    review diff and the Git sheet's list built from the console's own
    classes, at 1x/2x; then a one-line box of each kind sized by its text
    alone, which is the padding and a cap height tall with nothing to
    scroll, its letters as far from the bottom border as from the top."""
    assert await evaluate(instance, "CSS.supports('text-box', 'trim-both cap alphabetic')"), \
        "the host Chromium does not trim leading (text-box needs 133 or newer)"
    await evaluate(instance, """(() => {
        window.insetProbe=el('div');
        insetProbe.style.cssText='position:fixed;left:24px;top:24px;width:360px;z-index:2147483647;'+
            'display:flex;flex-direction:column;gap:14px;background:var(--bg)';
        document.body.appendChild(insetProbe);
        const place=(node,probe,css,parent)=>{
            node.dataset.probe=probe; node.style.cssText+=';'+css;
            (parent||insetProbe).appendChild(node); return node;
        };
        // Every scroll box holds its lines in one block, as the console
        // builds them: the code block's code, .box-lines, the list's groups.
        const lines=(box,cls,text)=>{ box.appendChild(el(cls==='code'?'code':'div',cls==='code'?null:cls,text)); return box; };
        place(el('textarea'),'textarea','height:96px;resize:none').value='Hello world\\nsecond line';
        const md=el('div','md'); insetProbe.appendChild(md);
        place(lines(el('pre'),'code','Hello world\\nsecond line'),'code block','height:56px',md);
        const approval=el('div','approval'); approval.style.cssText='margin:0;width:auto;max-width:none';
        insetProbe.appendChild(approval);
        place(lines(el('pre'),'box-lines','Hello world\\nsecond line'),'approval','',approval);
        place(lines(el('div','task-review-diff'),'box-lines','Hello world\\nsecond line'),'review diff','height:96px;min-height:0');
        const list=place(el('div','session-git-list'),'git list','');
        const group=list.appendChild(el('div','sgl-group'));
        group.appendChild(el('div','sgl-kind','Unstaged'));
        const rows=group.appendChild(el('div','sgl-rows'));
        rows.appendChild(el('span','sgl-what','modified')); rows.appendChild(el('span','sgl-path','README.md'));
        // One line of each kind, sized by that line alone: the height a
        // pinned box hides is the one a box holding a single line shows.
        const one=el('textarea'); one.rows=1;
        place(one,'one textarea','height:auto;min-height:0;resize:none;field-sizing:content').value='Hello world';
        const md1=el('div','md'); insetProbe.appendChild(md1);
        place(lines(el('pre'),'code','Hello world'),'one code block','',md1);
        const approval1=el('div','approval'); approval1.style.cssText='margin:0;width:auto;max-width:none';
        insetProbe.appendChild(approval1);
        place(lines(el('pre'),'box-lines','Hello world'),'one approval','',approval1);
        place(lines(el('div','task-review-files'),'box-lines','Hello world'),'one review files','min-height:0');
        const list1=place(el('div','session-git-list'),'one git list','');
        list1.appendChild(el('div','sgl-empty','Hello world'));
        // The reference: one trimmed line of the same text in the same font,
        // with no padding, on the probe's own background.
        window.insetReference=(source,text)=>{
            insetProbe.querySelectorAll('[data-probe=reference]').forEach(node=>node.remove());
            const cs=getComputedStyle(source), line=el('div',null,text);
            line.dataset.probe='reference';
            line.style.cssText='text-box:trim-both cap alphabetic;padding:0;border:0;margin:8px 0 8px 8px;'+
                'white-space:pre;width:max-content';
            for (const prop of ['fontFamily','fontSize','fontWeight','fontStyle','lineHeight',
                                'letterSpacing','textTransform','wordSpacing','color'])
                line.style[prop]=cs[prop];
            insetProbe.appendChild(line);
            return line;
        };
        // The capture around a node: its top-left corner and a margin,
        // snapped to whole pixels so the image's rows are the page's; a
        // box read to its bottom edge is captured whole.
        window.insetClip=(node,margin,whole)=>{
            const r=node.getBoundingClientRect();
            const clip={x:Math.floor(r.x-margin),y:Math.floor(r.y-margin),width:0,height:0};
            clip.width=Math.ceil(r.x+Math.min(r.width,80))-clip.x;
            clip.height=Math.ceil(r.y+(whole?r.height:Math.min(r.height,60))+margin)-clip.y;
            return clip;
        };
        // Where the ink starts inside a node, in CSS pixels from the inner
        // edge of its border (negative: above or left of it, which only the
        // unpadded reference can show), and where it ends above the inner
        // bottom edge: the capture is decoded in the page and scanned at
        // device resolution, a pixel counting as ink when it leaves the
        // background - the top padding row past the corner for a box, the
        // margin for the reference - and the corners outside the border's
        // curve left out.
        window.insetInk=async(data,node,margin,whole)=>{
            const r=node.getBoundingClientRect(), cs=getComputedStyle(node), clip=insetClip(node,margin,whole);
            const border=parseFloat(cs.borderTopWidth), inner=parseFloat(cs.borderTopLeftRadius)-border;
            const img=new Image(); img.src='data:image/png;base64,'+data; await img.decode();
            const canvas=document.createElement('canvas');
            canvas.width=img.width; canvas.height=img.height;
            const ctx=canvas.getContext('2d',{willReadFrequently:true});
            ctx.drawImage(img,0,0);
            const k=img.width/clip.width, step=1/k;
            const px=(x,y)=>[...ctx.getImageData(Math.floor(x*k),Math.floor(y*k),1,1).data].slice(0,3);
            const ox=r.x-clip.x+border, oy=r.y-clip.y+border, innerH=r.height-2*border;
            const bg=margin?px(1,1):px(ox+inner+6,oy+1);
            const inside=(dx,dy)=>{
                if (margin) return true;
                if (dx<0||dy<0||dy>=innerH) return false;
                if (dx<inner&&(dy<inner||dy>=innerH-inner)) {   // a corner: only well inside the curve
                    const cx=inner-dx, cy=dy<inner?inner-dy:dy-(innerH-inner);
                    return cx*cx+cy*cy<(inner-1.5)*(inner-1.5);
                }
                return true;
            };
            const rows=whole?clip.height:Math.min(clip.height,oy+40);
            let top=null, left=null, bottom=null;
            for (let y=0;y<rows;y+=step) for (let x=0;x<Math.min(clip.width,ox+60);x+=step) {
                const dx=x-ox, dy=y-oy;
                if (!inside(dx,dy)) continue;
                const p=px(x,y);
                if (Math.abs(p[0]-bg[0])+Math.abs(p[1]-bg[1])+Math.abs(p[2]-bg[2])<=60) continue;
                if (top===null||dy<top) top=dy;
                if (left===null||dx<left) left=dx;
                if (bottom===null||innerH-(dy+step)<bottom) bottom=innerH-(dy+step);
            }
            // how far the box can actually scroll (scrollHeight is a
            // rounded integer, a fractional line's is a pixel too many)
            const held=node.scrollTop; node.scrollTop=1e6; const scrollable=node.scrollTop; node.scrollTop=held;
            return {top,left,bottom:whole?bottom:null,height:r.height,innerHeight:innerH,
                paddingTop:parseFloat(cs.paddingTop),paddingLeft:parseFloat(cs.paddingLeft),
                paddingBottom:parseFloat(cs.paddingBottom),trim:cs.textBoxTrim,edge:cs.textBoxEdge,scrollable};
        };
        return true;
    })()""")

    async def ink(node, margin=0, whole=False):
        clip = await evaluate(instance, "insetClip(%s, %d, %s)" % (node, margin, "true" if whole else "false"))
        shot = await instance.call("Page.captureScreenshot", {"format": "png", "clip": dict(clip, scale=1)},
                                   session=instance.page_session)
        return await evaluate(instance, "insetInk(%s, %s, %d, %s)" % (
            json.dumps(shot["data"]), node, margin, "true" if whole else "false"))

    async def reference(source, text):
        await evaluate(instance, "insetReference(%s, %s); true" % (source, json.dumps(text)))
        return await ink("insetProbe.querySelector('[data-probe=reference]')", 8, True)

    def landed(label, box, ref, top, left, bottom=None):
        # the letters stand exactly the padding from the border, once the
        # font's own rendering (the reference's offsets) is allowed for
        assert box["top"] is not None and box["left"] is not None, (label, box)
        assert abs(box["top"] - (top + ref["top"])) <= 1, (label, "top", box, ref, top)
        assert abs(box["left"] - (left + ref["left"])) <= 1, (label, "left", box, ref, left)
        if bottom is not None:
            assert box["bottom"] is not None, (label, box)
            assert abs(box["bottom"] - (bottom + ref["bottom"])) <= 1, (label, "bottom", box, ref, bottom)

    def trimmed(label, box):
        assert (box["trim"], box["edge"]) == ("trim-both", "cap alphabetic"), (label, box)
        assert box["paddingTop"] == box["paddingLeft"] == box["paddingBottom"], (label, box)

    # each box and the element whose font its first line is set in
    boxes = (("textarea", None), ("code block", "code"), ("approval", ".box-lines"),
             ("review diff", ".box-lines"), ("git list", ".sgl-kind"))
    ones = (("one textarea", None), ("one code block", "code"), ("one approval", ".box-lines"),
            ("one review files", ".box-lines"), ("one git list", ".sgl-empty"))
    try:
        for scale in (1, 2):
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": 1440, "height": 900, "deviceScaleFactor": scale, "mobile": False},
                session=instance.page_session)
            await asyncio.sleep(.2)
            for name, first in boxes:
                node = "insetProbe.querySelector('[data-probe=%s]')" % json.dumps(name)
                box = await ink(node)
                trimmed(name, box)
                source = node + (".querySelector(%s)" % json.dumps(first) if first else "")
                ref = await reference(source, "Unstaged" if first == ".sgl-kind" else "Hello world")
                landed("%dx %s" % (scale, name), box, ref, box["paddingTop"], box["paddingLeft"])
            # One line, the box sized by it: the padding above, the cap
            # height, the padding below and nothing more - so nothing to
            # scroll - with the baseline standing the padding above the
            # bottom border as the caps stand below the top one.
            for name, line in ones:
                node = "insetProbe.querySelector('[data-probe=%s]')" % json.dumps(name)
                box = await ink(node, 0, True)
                trimmed(name, box)
                ref = await reference(node + (".querySelector(%s)" % json.dumps(line) if line else ""), "Hello world")
                landed("%dx %s" % (scale, name), box, ref,
                       box["paddingTop"], box["paddingLeft"], box["paddingBottom"])
                assert abs(box["innerHeight"] - (box["paddingTop"] + box["paddingBottom"] + ref["height"])) < .5, \
                    (name, box, ref)
                assert box["scrollable"] < .5, (name, box)
            # The composer: its box's padding and its textarea's add up the
            # same above and beside, the last line's baseline stands the
            # textarea's bottom padding above its edge with nothing to
            # scroll, and an empty box is exactly one trimmed line tall with
            # the placeholder's letters where typed ones go.
            seen = {}
            for text in ("Hello world", ""):
                await evaluate(instance, "demoView.composer.set(%s); true" % json.dumps(text))
                await asyncio.sleep(.1)
                box = await ink("demoView.composer.box")
                field = await ink("demoView.composer.ta", 0, True)
                assert (field["trim"], field["edge"]) == ("trim-both", "cap alphabetic"), field
                top = box["paddingTop"] + field["paddingTop"]
                left = box["paddingLeft"] + field["paddingLeft"]
                assert top == left, (box, field)
                ref = await reference("demoView.composer.ta", "Hello world")
                landed("%dx composer %r" % (scale, text), box, ref, top, left)
                # the typed line's baseline stands the padding above the
                # field's bottom edge (the placeholder ends in a descender)
                landed("%dx composer field %r" % (scale, text), field, ref,
                       field["paddingTop"], field["paddingLeft"], field["paddingBottom"] if text else None)
                # one trimmed line: the reference's height plus the padding,
                # never the floor, and nothing to scroll
                minimum, line_height = await evaluate(instance, """(() => {
                    const cs=getComputedStyle(demoView.composer.ta);
                    return [parseFloat(cs.minHeight), parseFloat(cs.lineHeight)];
                })()""")
                assert minimum < field["height"], (minimum, field)
                assert abs(field["height"] - (field["paddingTop"] + field["paddingBottom"] + ref["height"])) < .5, \
                    (field, ref)
                assert ref["height"] < line_height, (field, ref)
                assert field["scrollable"] < .5, field
                seen[text] = (box["top"], box["left"], field["height"])
            # the same height, and the placeholder's letters where the typed
            # ones stand (its fainter grey gives up half a device pixel of
            # antialiasing at the top of a glyph)
            typed, empty = seen["Hello world"], seen[""]
            assert typed[2] == empty[2] and abs(typed[0] - empty[0]) <= .5 and \
                abs(typed[1] - empty[1]) <= 1, seen
    finally:
        await evaluate(instance, "insetProbe.remove(); delete window.insetProbe; delete window.insetReference; "
                       "delete window.insetClip; delete window.insetInk; demoView.composer.set(''); true")
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 900, "deviceScaleFactor": 1,
            "mobile": False}, session=instance.page_session)
    print("PASS: a text box's letters stand as far below the top border, and their baseline as far "
          "above the bottom one, as the text stands in from the left (the composer typed and empty, "
          "a textarea, a code block, the approval's command, the review diff, the Git sheet's list, "
          "and a one-line box of each kind sized by its text with nothing to scroll) at 1x/2x, and "
          "an empty composer is one trimmed line tall", flush=True)


async def queue_expand_checks(instance, capture=False):
    """Expanding the queue must not raise a horizontal scrollbar.

    `.queue-strip.expanded` only names overflow-y, and a lone overflow-y
    computes the other axis to auto; each row's cancel tap target reaches 6px
    past the strip, which is enough to paint a horizontal bar over "Show
    fewer" even when there is nothing to scroll vertically."""
    await evaluate(instance, """(() => {
        window.queueSaved={q:demoView.queued,held:demoView.held,
            paused:demoView.pausedQueue,open:demoView.queueOpen};
        window.queueFill=n=>{
            demoView.queueOpen=false;
            demoView.renderQueue(Array.from({length:n},(_,i)=>
                `queued prompt ${i+1} — a line long enough to run past the right edge of the strip`),
                [], [], demoView.queueRevision);
        };
        window.queueBars=()=>{
            const box=demoView.queueEl, css=getComputedStyle(box);
            return {expanded:box.classList.contains('expanded'),
                hbar:box.offsetHeight-box.clientHeight,
                vbar:box.offsetWidth-box.clientWidth,
                overflowX:css.overflowX, overflowY:css.overflowY,
                hOverflow:box.scrollWidth-box.clientWidth,
                vOverflow:box.scrollHeight-box.clientHeight,
                more:(box.querySelector('.q-more')||{}).textContent||''};
        };
        true;
    })()""")
    try:
        for width, height, name in [(1440, 900, "desktop"), (390, 844, "phone")]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": width < 900}, session=instance.page_session)
            # Wait for the sidebar resize transition before measuring.
            await asyncio.sleep(.35)
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(%s); true" % json.dumps(theme))
                # A short queue: expanding it has nothing to scroll vertically,
                # so a horizontal bar would be the only one on screen.
                for rows, vertical in ((9, False), (60, True)):
                    await evaluate(instance, "queueFill(%d)" % rows)
                    await evaluate(instance, "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                    collapsed = await evaluate(instance, "queueBars()")
                    assert collapsed["expanded"] is False, (name, theme, rows, collapsed)
                    assert collapsed["hbar"] == 0 and collapsed["vbar"] == 0, (name, theme, rows, collapsed)
                    assert collapsed["more"] == "+%d more" % (rows - 5), (name, theme, rows, collapsed)
                    # A real click on the real control, not a flag flip.
                    await evaluate(instance, "demoView.queueEl.querySelector('.q-more').click(); true")
                    await evaluate(instance, "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                    open_ = await evaluate(instance, "queueBars()")
                    assert open_["expanded"] is True, (name, theme, rows, open_)
                    assert open_["more"] == "Show fewer", (name, theme, rows, open_)
                    assert open_["hbar"] == 0, (name, theme, rows, open_)
                    assert open_["overflowX"] != "auto", (name, theme, rows, open_)
                    # The cap still applies, and the vertical bar is the one
                    # the expanded strip is allowed to grow.
                    assert (open_["vOverflow"] > 0) is vertical, (name, theme, rows, open_)
                    assert (open_["vbar"] > 0) is vertical, (name, theme, rows, open_)
                    if capture and rows == 9 and width == 1440:
                        shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                                   session=instance.page_session)
                        (BASE / "data" / ("queue-expanded-" + name + "-" + theme + ".png")).write_bytes(
                            base64.b64decode(shot["data"]))
                    # Collapsing again returns the "+N more" foot.
                    await evaluate(instance, "demoView.queueEl.querySelector('.q-more').click(); true")
                    back = await evaluate(instance, "queueBars()")
                    assert back["expanded"] is False and back["hbar"] == 0, (name, theme, rows, back)
    finally:
        await evaluate(instance, """demoView.queueOpen=queueSaved.open;
            demoView.renderQueue(queueSaved.q||[], queueSaved.held||[], queueSaved.paused||[],
                demoView.queueRevision);
            delete window.queueFill; delete window.queueBars; delete window.queueSaved;
            applyTheme('dark'); true""")
    print("PASS: expanding the queue raises no horizontal scrollbar on desktop or phone in "
          "either theme, with and without a vertical one, and Show fewer collapses it again", flush=True)


async def background_task_checks(instance, capture=False):
    await evaluate(instance, """(async () => {
        window.backgroundSavedEvents=(await api(demoView.tab.bid,
            `sessions/${demoView.tab.sid}/events?limit=200`)).events;
        const command='node tests/layout.js --report=/home/mira/projects/harbor/'+'long-path-'.repeat(35);
        window.backgroundDemoEvents=[];
        let seq=1;
        for(const status of ['completed','failed','stopped']) {
            const id='demo-background-'+status;
            backgroundDemoEvents.push(
                {seq:seq++,kind:'tool_use',data:{tool:'Bash',tool_use_id:id,input:{command}}},
                {seq:seq++,kind:'tool_result',data:{tool_use_id:id,content:'Command running in background'}},
                {seq:seq++,kind:'info',data:{subtype:'task',tool_use_id:id,task_id:id,status,
                    text:status==='completed'?command:`Background task ${status}: ${command}`}});
        }
        backgroundDemoEvents.push(
            {seq:seq++,kind:'info',data:{subtype:'task',task_id:'missing-card',
                tool_use_id:'outside-window',status:'completed',text:command}},
            {seq:seq++,kind:'info',data:{subtype:'task',task_id:'unlinked',
                status:'failed',text:'Background task failed: '+command}});
        for (const label of ['Task started','Task changes applied','Conflict resolution started in task'])
            backgroundDemoEvents.push({seq:seq++,kind:'info',data:{subtype:'session_task',task_id:8,
                text:label+': '+command}});
        for (const [subtype,text] of [
            ['interrupted','Turn interrupted by user'],
            ['interrupted','Stopped waiting for background tasks; the engine ended them'],
            ['background_wait','Waiting for 1 background task: '+command],
            ['background_wait_stopped','Stopped waiting for background tasks; the engine ended them'],
            ['model_switch','engine model changed: preview-standard → '+command],
            ['model_switch',"requested model 'preview-standard' but engine is serving "+command]])
            backgroundDemoEvents.push({seq:seq++,kind:'info',data:{subtype,text}});
        demoView.rebuildTranscript(backgroundDemoEvents,{attached:true});
        return true;
    })()""")
    try:
        for width, height, name in [(1440, 900, "desktop"), (390, 844, "mobile")]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": name == "mobile"}, session=instance.page_session)
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(" + json.dumps(theme) + "); true")
                layout = await evaluate(instance, """(() => {
                    const updates=[...demoView.inner.querySelectorAll('.task-update')];
                    return {count:updates.length,
                        standalone:updates.filter(n=>n.parentNode===demoView.inner).length,
                        centered:demoView.inner.querySelectorAll('.info-line').length,
                        fits:updates.every(n=>n.scrollWidth<=n.clientWidth+1 &&
                            n.querySelector('.task-update-text').scrollWidth<=n.clientWidth+1),
                        left:updates.every(n=>getComputedStyle(n).textAlign==='left'),
                        visible:updates.every(n=>n.getBoundingClientRect().height>30),
                        collapsed:[...demoView.inner.querySelectorAll('.tool-body')].every(n=>getComputedStyle(n).display==='none'),
                        tones:['completed','failed','stopped'].map(status=>{
                            const card=demoView.toolCards['demo-background-'+status];
                            const label=card.querySelector('.task-update-label');
                            return {label:label.textContent,color:getComputedStyle(label).color,
                                head:getComputedStyle(card.querySelector('.t-state')).color};
                        }),
                        pageFits:document.documentElement.scrollWidth<=innerWidth};
                })()""")
                assert layout["count"] == 14 and layout["standalone"] == 11, layout
                assert layout["centered"] == 0, layout
                assert all(layout[k] for k in ("fits", "left", "visible", "collapsed", "pageFits")), layout
                assert [row["label"] for row in layout["tones"]] == [
                    "Background task completed", "Background task failed", "Background task stopped"], layout
                assert len({row["color"] for row in layout["tones"]}) == 3, layout
                assert all(row["color"] == row["head"] for row in layout["tones"]), layout
                if capture:
                    await evaluate(instance, "demoView.scroll.scrollTop=0; true")
                    await asyncio.sleep(.1)
                    data = await instance.call("Page.captureScreenshot", {"format": "png"}, session=instance.page_session)
                    (BASE / "data" / ("background-tasks-" + name + "-" + theme + ".png")).write_bytes(base64.b64decode(data["data"]))
        # Search can land directly on a visible update inside a folded card.
        assert await evaluate(instance, """(async () => {
            await demoView.jumpToSeq(6);
            const n=demoView.findEventNode(6);
            return n.classList.contains('background-task') && n.classList.contains('search-flash') &&
                n.getBoundingClientRect().bottom>0 && n.getBoundingClientRect().top<innerHeight;
        })()""")
        # Rebuilding a narrow history window retains the notice. Bringing its
        # origin into view reparents that node instead of duplicating it.
        assert await evaluate(instance, """(async () => {
            demoView.rebuildTranscript([backgroundDemoEvents[2]],{attached:false,mayHaveOlder:true});
            const update=demoView.findEventNode(3);
            if(update.parentNode!==demoView.inner) return false;
            const savedApi=api;
            let requests=0;
            api=async (bid,route,...args)=>{
                if(route.includes('/events?before_seq=3')) {
                    requests++;
                    return {events:backgroundDemoEvents.slice(0,2)};
                }
                return savedApi(bid,route,...args);
            };
            try {
                await demoView._loadOlderFn();
                const card=demoView.toolCards['demo-background-completed'];
                return requests===1 && update.parentNode===card && card.parentNode===demoView.inner &&
                    !demoView.inner.querySelector('.load-older') && demoView.oldestSeq===1 &&
                    demoView.inner.querySelectorAll('.background-task').length===1 &&
                    demoView.findEventNode(3)===update;
            } finally {api=savedApi;}
        })()""")
    finally:
        await evaluate(instance, """demoView.rebuildTranscript(backgroundSavedEvents,{attached:true});
            delete window.backgroundSavedEvents; delete window.backgroundDemoEvents;
            demoView.scrollBottom(true); true""")
    print("PASS: background-task updates attach by native ID, remain visible in folded cards, wrap on desktop and phone in both themes, and retain history/search targets", flush=True)


async def background_count_checks(a, b, hub):
    """Real session sockets share replacements and reconnect from snapshots."""
    task_rows = [{"id": "demo-watch", "type": "local_bash", "description": "Layout watcher"},
                 {"id": "demo-agent", "type": "local_agent", "description": "Check navigation"}]
    saved_status = hub.status
    count = "demoView.root.querySelector('.chip.background-tasks')"
    try:
        hub.status = "running"
        for viewer in list(hub.watchers):
            await hub.attach_with_snapshot(viewer)
        for instance in (a, b):
            await until(instance, "demoView.status === 'running'")
        hub._set_background_tasks(task_rows)
        for instance in (a, b):
            await until(instance, count + "?.textContent === '2 tasks'")
        # A real disconnect removes old evidence. The reconnect snapshot
        # restores the count even though no further task update is emitted.
        await evaluate(b, "demoView.retry=5000; demoView.ws.close(); true")
        await until(b, "demoView.reconnecting && !" + count)
        await evaluate(b, "demoView.resumeConnection(); true")
        await until(b, "!demoView.reconnecting && " + count + "?.textContent === '2 tasks'")
        for width, height, scale in [(1440, 900, 1), (390, 844, 2)]:
            await a.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": scale,
                "mobile": width == 390}, session=a.page_session)
            for theme in ("dark", "light"):
                await evaluate(a, "applyTheme(" + json.dumps(theme) + "); true")
                layout = await evaluate(a, """(() => {
                    const chip=demoView.root.querySelector('.chip.background-tasks');
                    const lane=chip.parentElement;
                    const reference=el('button','chip browser','Browser DEMO');
                    lane.insertBefore(reference,chip);
                    chip.scrollIntoView({block:'nearest',inline:'nearest'});
                    const style=getComputedStyle(chip), ref=getComputedStyle(reference);
                    const bounds=chip.getBoundingClientRect(), other=reference.getBoundingClientRect();
                    const result={sameBlue:style.color===ref.color &&
                        style.backgroundColor===ref.backgroundColor && style.borderColor===ref.borderColor,
                        sameSize:bounds.height===other.height && style.borderRadius===ref.borderRadius,
                        centered:Math.abs(bounds.top-other.top)<.5,
                        countFits:chip.scrollWidth<=chip.clientWidth,
                        noPageOverflow:document.documentElement.scrollWidth<=innerWidth,
                        passive:chip.tagName==='SPAN' && style.cursor!=='pointer',
                        named:chip.getAttribute('aria-label')==='2 background tasks running',
                        swipe:getComputedStyle(lane).overflowX==='auto'};
                    reference.remove(); return result;
                })()""")
                assert all(layout.values()), layout
        hub._set_background_tasks(task_rows[1:])
        for instance in (a, b):
            await until(instance, count + "?.textContent === '1 task'")
        hub._set_background_tasks([])
        for instance in (a, b):
            await until(instance, "!" + count)
    finally:
        hub._set_background_tasks([])
        hub.status = saved_status
        for viewer in list(hub.watchers):
            await hub.attach_with_snapshot(viewer)
        for instance in (a, b):
            await until(instance, "demoView.status === " + json.dumps(saved_status))
    print("PASS: live background-task counts over two session sockets, reconnect snapshots, "
          "zero removal and blue pill geometry on desktop/phone in both themes", flush=True)


async def attachment_steering_checks(instance, hub):
    # Real uploads and native clicks, with a held handoff so the editor can
    # change before acknowledgement. Both server transports are covered by
    # steering_test.py; this checks the rendered control and shared Composer.
    fields = ("status", "proc", "_proc_ready", "_driver_ctx", "_active_turn_id")
    saved = {key: getattr(hub, key) for key in fields}
    hub.status, hub._proc_ready, hub._active_turn_id = "running", True, "attachment-ui"
    hub.proc = SimpleNamespace(returncode=None, stdin=SimpleNamespace(is_closing=lambda: False))
    hub._driver_ctx = {"initial_user_replayed": True, "tool": ""}
    for viewer in list(hub.watchers):
        await hub.attach_with_snapshot(viewer)
    await until(instance, "demoView.steering.ready")
    await evaluate(instance, """window.attachmentProbe={
        send:demoView.sendActiveTurnControl, draft:demoView.composer.value(),
        release:null};
        demoView.sendActiveTurnControl=(kind,body)=>new Promise(resolve=>{
            attachmentProbe.kind=kind; attachmentProbe.body=body; attachmentProbe.release=resolve;
        }); true""")
    try:
        for width, height, mobile in ((1440, 900, False), (390, 844, True)):
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 2 if mobile else 1,
                "mobile": mobile}, session=instance.page_session)
            await evaluate(instance, """$('app').classList.remove('side-open'); demoView.composer.replace('');
                demoView.status='running'; demoView.setSteeringState({supported:true,ready:true,turn_id:'attachment-ui'});
                demoView.updateRunState(); attachmentProbe.body=null;
                demoView.composer.uploadFile(new File(['Invented fixture'], 'note.txt', {type:'text/plain'}))""")
            await until(instance, "!demoView.steerBtn.disabled && demoView.composer.attachments[0]?.path")
            await asyncio.sleep(.2)  # let the responsive composer finish its height change
            assert await evaluate(instance, "demoView.composer.text()===''"), "attachment-only steering"
            await evaluate(instance, "attachmentProbe.expected=demoView.composer.message(); true")
            point = await evaluate(instance, """(() => {
                const r=demoView.steerBtn.getBoundingClientRect();
                return {x:r.x+r.width/2,y:r.y+r.height/2};
            })()""")
            for kind in ("mousePressed", "mouseReleased"):
                await instance.call("Input.dispatchMouseEvent", {
                    "type": kind, **point, "button": "left", "clickCount": 1}, session=instance.page_session)
            await until(instance, "!!attachmentProbe.body")
            assert await evaluate(instance, "attachmentProbe.kind==='steer' && attachmentProbe.body.text===attachmentProbe.expected && attachmentProbe.body.expected_turn_id==='attachment-ui'")
            assert await evaluate(instance, "demoView.steerBtn.disabled && demoView.composer.attachments.length===1")
            await evaluate(instance, "attachmentProbe.release({ok:true,status:'sent'}); true")
            await until(instance, "demoView.composer.isEmpty() && !demoView.steerPending")

            # A second upload begun during the handoff must survive, even
            # before its response has supplied a path for draft serialization.
            await evaluate(instance, """demoView.composer.uploadFile(new File(['Original'], 'first.txt', {type:'text/plain'}))""")
            await until(instance, "!demoView.composer.sendBlocker()")
            await evaluate(instance, """attachmentProbe.body=null; demoView.steer();
                attachmentProbe.fetch=window.fetch;
                window.fetch=(url,opts)=>String(url).endsWith('/upload') ? new Promise((resolve,reject)=>{
                    attachmentProbe.upload=()=>attachmentProbe.fetch.call(window,url,opts).then(resolve,reject);
                }) : attachmentProbe.fetch.call(window,url,opts);
                demoView.composer.uploadFile(new File(['Next'], 'second.txt', {type:'text/plain'})); true""")
            await until(instance, "!!attachmentProbe.body && !!attachmentProbe.upload")
            assert await evaluate(instance, "demoView.composer.attachments[1].uploading && demoView.steerBtn.disabled")
            await evaluate(instance, "attachmentProbe.release({ok:true,status:'sent'}); true")
            await until(instance, "!demoView.steerPending")
            assert await evaluate(instance, "demoView.composer.attachments.length===2 && demoView.composer.attachments[1].uploading")
            await evaluate(instance, "window.fetch=attachmentProbe.fetch; attachmentProbe.upload(); true")
            await until(instance, "!demoView.composer.sendBlocker() && !demoView.steerBtn.disabled")
            assert await evaluate(instance, "demoView.composer.attachments[1].name==='second.txt'")
    finally:
        for key, value in saved.items():
            setattr(hub, key, value)
        for viewer in list(hub.watchers):
            await hub.attach_with_snapshot(viewer)
        await evaluate(instance, """if(attachmentProbe.fetch) window.fetch=attachmentProbe.fetch;
            demoView.sendActiveTurnControl=attachmentProbe.send;
            demoView.composer.replace(attachmentProbe.draft); demoView.saveDraft();
            demoView.status='idle'; demoView.setSteeringState({}); demoView.updateRunState();
            delete window.attachmentProbe; true""")
    print("PASS: attachment-only steering by real desktop/phone clicks; uploads and edits survive delayed handoffs", flush=True)


async def git_mark_checks(a, b):
    """Every row carries the Git mark between its pin and its notes, drawn
    from the node's record: a repository holding uncommitted or unpushed
    work in the console's warn tone with the counts in its label and the
    rundown in its tooltip - raised by a real hover, one line per cause -
    the rest as before, with no tooltip. Focusing another session asks the
    node to look again, and a changed answer - a mark or a count - reaches
    every console through the list."""
    marks = await evaluate(a, """(() => {
        const probe = document.createElement('span');
        probe.style.color = 'var(--warn)'; document.body.appendChild(probe);
        const warn = getComputedStyle(probe).color; probe.remove();
        return Array.from(document.querySelectorAll('.sess-item')).map(row => {
        const lane = Array.from(row.querySelector('.si-actions').children).map(m => m.className.split(' ')[0]);
        const git = row.querySelector('.si-git');
        return {lane, has: git.classList.contains('has'), warn: git.classList.contains('warn'),
            orange: getComputedStyle(git).color === warn,
            label: git.getAttribute('aria-label'), name: row.querySelector('.si-name').textContent,
            role: git.getAttribute('role'), cursor: getComputedStyle(git).cursor,
            focusable: git.tabIndex === 0,
            size: git.querySelector('svg').getBoundingClientRect().width};
    })})()""")
    assert len(marks) == 5, marks
    for mark in marks:
        assert mark["lane"] == ["si-pin", "si-git", "si-notes"], mark
        assert mark["cursor"] == "pointer" and mark["size"] == 14, mark
        assert mark["orange"] == mark["warn"], mark
        # a repository's mark is the button that opens its sheet; the rest
        # are labelled images
        assert mark["role"] == ("button" if mark["has"] else "img"), mark
        assert mark["focusable"] == mark["has"], mark
    assert [mark["has"] for mark in marks] == [True, True, True, True, False], marks
    assert marks[-1]["label"] == "No Git repository"
    assert marks[0]["label"] == "Git repository · nothing to commit or push · open details"
    by_name = {mark["name"]: mark for mark in marks}
    assert [name for name, mark in by_name.items() if mark["warn"]] == ["Garden planner"], marks
    assert by_name["Garden planner"]["label"] == \
        "Git repository · 3 uncommitted changes · 1 unpushed commit · open details"
    assert by_name["API cleanup"]["label"] == "Git repository · nothing to commit · no remote · open details"
    # The orange mark alone carries a tooltip: the console's own bubble,
    # raised by hovering it, with the branch on its first line and then
    # one line for each cause, kept on separate lines by the bubble.
    rundown = ("Uncommitted and unpushed work on main\n"
               "3 changes · 1 staged, 1 unstaged, 1 untracked\n1 unpushed commit")
    tips = await evaluate(a, """Array.from(document.querySelectorAll('.sess-item')).map(row => {
        const git = row.querySelector('.si-git');
        return [row.querySelector('.si-name').textContent,
            git.hasAttribute('title') ? git.getAttribute('title') : git.getAttribute('data-tip')]; })""")
    assert dict(tips) == {name: (rundown if name == "Garden planner" else None) for name in by_name}, tips
    # a desktop, where the sidebar stands beside the chat and a pointer can
    # rest on its marks (the checks before this one end on a phone)
    await a.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900,
                 "deviceScaleFactor": 1, "mobile": False}, session=a.page_session)
    await a.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 0, "y": 0}, session=a.page_session)
    await evaluate(a, "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    spot = await evaluate(a, """(() => { const r = Array.from(document.querySelectorAll('.sess-item'))
        .find(row => row.querySelector('.si-name').textContent === 'Garden planner')
        .querySelector('.si-git').getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()""")
    await a.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": spot["x"], "y": spot["y"]},
                 session=a.page_session)
    await until(a, "(() => { const tip = document.getElementById('tip'); return !!tip && !tip.hidden && "
                   "tip.querySelector('.tip-card').textContent === %s; })()" % json.dumps(rundown))
    bubble = await evaluate(a, """(() => { const card = document.querySelector('#tip .tip-card');
        const style = getComputedStyle(card); const box = card.getBoundingClientRect();
        const mark = document.querySelector('.sess-item .si-git.warn').getBoundingClientRect();
        return {whiteSpace: style.whiteSpace, lines: box.height / parseFloat(style.lineHeight),
            below: box.top >= mark.bottom, centred: Math.abs((box.left + box.right) / 2 - (mark.left + mark.right) / 2) <= 8}; })()""")
    assert bubble["whiteSpace"] == "pre-line" and 3 <= bubble["lines"] < 4, bubble
    assert bubble["below"] and bubble["centred"], bubble
    await a.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 0, "y": 0}, session=a.page_session)
    await until(a, "document.getElementById('tip').hidden")
    # a fresh answer for the focused session: the node re-checks on focus
    # and publishes the changed mark to the other console as well
    notes = next(s for s in db.list_sessions() if s["cwd"].endswith("/notes"))
    DEMO_GIT[notes["cwd"]] = {"repo": True, "changes": 1, "unpushed": None}
    try:
        await evaluate(a, "openSessionTab(0,%d,state.sessions.find(s=>s.id===%d)); true" % (notes["id"], notes["id"]))
        for instance in (a, b):
            await until(instance, "state.sessions.find(s=>s.id===%d).git.changes===1 && "
                        "document.querySelectorAll('.si-git.has').length===5 && "
                        "document.querySelectorAll('.si-git.has.warn').length===2" % notes["id"])
        # the count alone moving is news too: the same repository, more work
        DEMO_GIT[notes["cwd"]]["changes"] = 2
        session_git._store(notes["cwd"], demo_git(notes["cwd"]))
        runner.broadcast_sessions()
        for instance in (a, b):
            await until(instance, "document.querySelector('.sess-item[data-session-key=\"0:%d\"] .si-git')"
                        ".getAttribute('aria-label')==='Git repository · 2 uncommitted changes · no remote · open details'"
                        % notes["id"])
    finally:
        DEMO_GIT[notes["cwd"]] = {"repo": False}
        session_git._store(notes["cwd"], demo_git(notes["cwd"]))
        runner.broadcast_sessions()
        for instance in (a, b):
            await until(instance, "document.querySelectorAll('.si-git.has').length===4 && "
                        "document.querySelectorAll('.si-git.warn').length===1")
        await evaluate(a, "closeTab('s:0:%d'); activateTab('s:0:1'); true" % notes["id"])
    print("PASS: the Git mark between pin and notes on every row, drawn from the node's record - "
          "orange with its counts for uncommitted or unpushed work and the rundown in its hovered "
          "tooltip - re-checked on focus and published to every console", flush=True)


async def git_sheet_checks(a):
    """The sheet an orange mark opens, by a real click: the session's facts
    in the review sheet's voice with the State in the mark's own tone, the
    paths grouped by kind with git's verbs and the unpushed commit on the
    review sheet's list surface, read over the node's own route; Refresh
    reading again; Back closing it and Forward opening a fresh one; and, on
    a phone, the commit's author and time stepping under its subject with
    nothing pushed off the screen; the History's first hundred lines in the
    list's monospace, one line each however long, the box scrolling sideways
    and the next hundred read by a real scroll to its foot. The type is the console's: the fact
    labels and the captions in the field voice, the kind kickers on the
    kicker step, the lists in the one monospace; the facts stand on the
    review sheet's column, its own step under the title and one row under
    the next at its step. Then the actions: Revert on its own side of the
    row and Push, the primary, at its end on the desktop, two lines of two
    on a phone with every word fitting; a real click on Push and one on
    Revert through its confirm, each over the node's route, taking its
    count off the row's mark and its button off the row with a toast."""
    garden = next(s for s in db.list_sessions() if s["cwd"].endswith("/garden"))
    reads = "performance.getEntriesByType('resource').filter(e => e.name.endsWith('/api/sessions/%d/git')).length" % garden["id"]
    active = await evaluate(a, "document.querySelector('.sess-item.active') ? document.querySelector('.sess-item.active').querySelector('.si-name').textContent : ''")
    spot = await evaluate(a, """(() => { const r = Array.from(document.querySelectorAll('.sess-item'))
        .find(row => row.querySelector('.si-name').textContent === 'Garden planner')
        .querySelector('.si-git').getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()""")
    for kind in ("mousePressed", "mouseReleased"):
        await a.call("Input.dispatchMouseEvent", {"type": kind, "x": spot["x"], "y": spot["y"],
                     "button": "left", "clickCount": 1}, session=a.page_session)
    pages = "performance.getEntriesByType('resource').filter(e => e.name.includes('/api/sessions/%d/git/log?')).length" % garden["id"]
    await until(a, "!!document.querySelector('.session-git-modal') && " + reads + " === 1 && "
                   "!document.querySelector('.session-git-modal').hasAttribute('aria-busy') && "
                   + pages + " === 1 && document.querySelectorAll('.session-git-log .sgl-line').length === 100")
    sheet = await evaluate(a, """(() => {
        const m = document.querySelector('.session-git-modal');
        const probe = document.createElement('span'); document.body.appendChild(probe);
        const computed = (prop, value) => { probe.style[prop] = value; return getComputedStyle(probe)[prop]; };
        const warn = computed('color', 'var(--warn)'), mono = computed('fontFamily', 'var(--mono)');
        const kicker = computed('fontSize', 'var(--fs-2xs)'), field = computed('fontSize', 'var(--fs-xs)');
        const help = computed('color', 'var(--txt3)');
        probe.remove();
        const style = node => getComputedStyle(node);
        /* the review sheet, as a probe: the step its rows keep */
        const review = document.createElement('div'); review.className = 'modal task-review-modal';
        review.innerHTML = '<h2>Review task</h2><div class="ws-facts task-review-facts"><div class="ws-fact"><span class="field-lbl">Task</span><span class="wsf-v">x</span></div></div>';
        document.body.appendChild(review);
        const reviewFacts = review.querySelector('.ws-facts');
        const reviewStep = parseFloat(style(reviewFacts).rowGap);
        const reviewLabel = style(review.querySelector('.field-lbl')).fontSize;
        review.remove();
        /* the agent-notes editor, as a probe: the voice of its intro line and
           the steps from its title down to it and from it down to the first
           control */
        const notes = document.createElement('div'); notes.className = 'modal agent-notes-modal';
        notes.innerHTML = '<h2>Agent notes</h2><p class="modal-copy agent-notes-intro">Garden planner · /home/mira/projects/garden</p>'
            + '<form><label class="check"><input type="checkbox"> Use one file for both</label></form>';
        document.body.appendChild(notes);
        const notesIntro = notes.querySelector('.agent-notes-intro');
        const voice = node => [style(node).fontSize, style(node).lineHeight, style(node).color, style(node).fontFamily].join(' ');
        const notesTitleStep = notesIntro.getBoundingClientRect().top - notes.querySelector('h2').getBoundingClientRect().bottom;
        const notesIntroStep = notes.querySelector('.check').getBoundingClientRect().top - notesIntro.getBoundingClientRect().bottom;
        const notesVoice = voice(notesIntro), notesIntroHeight = notesIntro.getBoundingClientRect().height;
        notes.remove();
        const intro = m.querySelector('.session-git-intro');
        const facts = m.querySelector('.session-git-facts');
        const rows = Array.from(m.querySelectorAll('.ws-fact')).map(row => row.getBoundingClientRect());
        const label = m.querySelector('.ws-fact .field-lbl'), caption = m.querySelector('.session-git-section .field-lbl');
        const kind = m.querySelector('.sgl-kind'), list = m.querySelector('.session-git-list');
        const state = Array.from(m.querySelectorAll('.ws-fact')).find(row => row.querySelector('.field-lbl').textContent === 'State').querySelector('.wsf-v');
        const staged = m.querySelector('.sgl-group .sgl-rows');
        const what = staged.children[0].getBoundingClientRect(), path = staged.children[1].getBoundingClientRect();
        const commit = m.querySelector('.sgl-commits');
        return {title: m.querySelector('h2').textContent, intro: intro.textContent,
            facts: Array.from(m.querySelectorAll('.ws-fact')).map(row => [row.querySelector('.field-lbl').textContent, row.querySelector('.wsf-v').textContent]),
            reviewStep, factStep: parseFloat(style(facts).rowGap),
            notesTitleStep, titleStep: intro.getBoundingClientRect().top - m.querySelector('h2').getBoundingClientRect().bottom,
            notesIntroStep, introStep: facts.getBoundingClientRect().top - intro.getBoundingClientRect().bottom,
            notesVoice, introVoice: voice(intro),
            notesIntroHeight, introHeight: intro.getBoundingClientRect().height,
            steps: rows.slice(1).map((box, i) => box.top - rows[i].bottom),
            captionSteps: (() => {
                const sections = Array.from(m.querySelectorAll('.session-git-section'));
                const caps = sections.map(node => node.querySelector('.field-lbl').getBoundingClientRect());
                const lists = sections.map(node => node.querySelector('.session-git-list').getBoundingClientRect());
                return {first: caps[0].top - rows[rows.length - 1].bottom,
                    above: caps[1].top - lists[0].bottom, below: lists[1].top - caps[1].bottom}; })(),
            stateOrange: style(state).color === warn,
            captions: Array.from(m.querySelectorAll('.session-git-section .field-lbl')).map(node => node.textContent),
            kinds: Array.from(m.querySelectorAll('.sgl-kind')).map(node => node.textContent),
            rows: Array.from(m.querySelectorAll('.sgl-rows')).map(node => Array.from(node.children).map(cell => cell.textContent)),
            commit: Array.from(commit.children).map(cell => cell.textContent),
            metaRight: commit.lastElementChild.getBoundingClientRect().right,
            metaTop: commit.lastElementChild.getBoundingClientRect().top,
            subjectTop: commit.children[1].getBoundingClientRect().top,
            listRight: list.getBoundingClientRect().right,
            width: m.getBoundingClientRect().width,
            labelField: style(label).fontSize === field && style(label).fontSize === reviewLabel &&
                style(label).textTransform === 'uppercase' && parseFloat(style(label).marginBottom) === 0,
            kindKicker: style(kind).fontSize === kicker && style(kind).textTransform === 'uppercase' &&
                style(kind).color === help,
            captionField: style(caption).fontSize === field && style(caption).textTransform === 'uppercase',
            noteCase: style(caption.querySelector('.field-optional')).textTransform === 'none',
            listMono: style(list).fontFamily === mono, factMono: style(state).fontFamily === mono,
            columns: path.left > what.right && Math.abs(path.top - what.top) < 1,
            buttons: Array.from(m.querySelectorAll('.m-btns .btn')).map(b => [b.textContent, b.disabled, b.classList.contains('btn-pri'), b.classList.contains('btn-danger')]),
            row: (() => { const row = m.querySelector('.m-btns').getBoundingClientRect();
                const boxes = Array.from(m.querySelectorAll('.m-btns .btn')).map(b => b.getBoundingClientRect());
                return {revertAtStart: Math.abs(boxes[0].left - row.left) < 1, pushAtEnd: Math.abs(boxes[3].right - row.right) < 1,
                    oneLine: boxes.every(b => Math.abs(b.top - boxes[0].top) < 1),
                    apart: boxes[1].left - boxes[0].right > 100}; })(),
            focused: document.activeElement === m.querySelector('#session-git-close')};
    })()""")
    assert sheet["title"] == "Git repository" and sheet["intro"] == "Garden planner · /home/mira/projects/garden", sheet
    assert sheet["facts"] == [["Branch", "main"], ["Upstream", "origin/main · 1 ahead, 2 behind"],
                              ["State", "Uncommitted and unpushed work"], ["Checked", sheet["facts"][3][1]]], sheet
    assert sheet["facts"][3][1] and sheet["stateOrange"], sheet
    # the session and its directory under the title in the agent-notes
    # editor's intro voice, the same step below the title and the same step
    # above the first fact as that editor's line stands above its first
    # control
    assert sheet["introVoice"] == sheet["notesVoice"] and sheet["introHeight"] == sheet["notesIntroHeight"], sheet
    assert sheet["notesTitleStep"] == 12 and sheet["titleStep"] == sheet["notesTitleStep"], sheet
    assert sheet["notesIntroStep"] == 12 and sheet["introStep"] == sheet["notesIntroStep"], sheet
    # the four facts one under the next at the review sheet's own step
    assert sheet["reviewStep"] == 6 and sheet["factStep"] == sheet["reviewStep"], sheet
    assert len(sheet["steps"]) == 3 and all(abs(step - sheet["reviewStep"]) < .5 for step in sheet["steps"]), sheet
    # the captions at that same step: Changes under the last fact, Unpushed
    # commits under the first list and above its own
    assert all(abs(step - sheet["reviewStep"]) < .5 for step in sheet["captionSteps"].values()), sheet["captionSteps"]
    assert sheet["captions"] == ["Changes · 3 · 1 staged, 1 unstaged, 1 untracked", "Unpushed commits · 1 · not on origin",
                                 "History · 250"], sheet
    assert sheet["kinds"] == ["Staged", "Unstaged", "Untracked"], sheet
    assert sheet["rows"] == [["modified", "src/planner.css"], ["modified", "src/beds.js"], ["notes/spring.md"]], sheet
    assert sheet["commit"][:2] == ["4f2c9ab", "Shade map for the north beds"] and sheet["commit"][2].startswith("Mira Holt · "), sheet
    assert abs(sheet["metaTop"] - sheet["subjectTop"]) < 1 and sheet["listRight"] - sheet["metaRight"] > 8, sheet
    assert sheet["width"] == 640, sheet
    assert sheet["labelField"] and sheet["kindKicker"] and sheet["captionField"] and sheet["noteCase"], sheet
    assert sheet["listMono"] and sheet["factMono"] and sheet["columns"], sheet
    # the row: Revert alone at the start, Close, Refresh and the primary
    # Push together at the end, on one line
    assert sheet["buttons"] == [["Revert", False, False, True], ["Close", False, False, False],
                                ["Refresh", False, False, False], ["Push", False, True, False]], sheet
    assert all(sheet["row"].values()), sheet["row"]
    assert sheet["focused"], sheet
    # the History: the first hundred lines of the short log in the list's
    # own monospace, the newest first with its hash in the help colour, a
    # line that never wraps however long its subject - the box scrolls
    # sideways for it, the sheet and the page no wider - and the foot
    # offering the next hundred
    log = await evaluate(a, """(() => {
        const m = document.querySelector('.session-git-modal'), box = m.querySelector('.session-git-log');
        const style = node => getComputedStyle(node);
        const lines = Array.from(box.querySelectorAll('.sgl-line'));
        const help = (() => { const probe = document.createElement('span'); document.body.appendChild(probe);
            probe.style.color = 'var(--txt3)'; const c = style(probe).color; probe.remove(); return c; })();
        return {count: lines.length, first: lines[0].textContent, last: lines[99].textContent,
            hash: lines[0].querySelector('.sgl-hash').textContent, hashHelp: style(lines[0].querySelector('.sgl-hash')).color === help,
            /* the trim takes the leading off the first line, so a wrapped
               line is one taller than two line-heights, not one unlike its
               neighbour; the long subject runs past the box's edge instead */
            oneLine: lines.every(line => line.getBoundingClientRect().height < 2 * parseFloat(style(box).lineHeight)) &&
                lines[0].scrollWidth > box.clientWidth && lines[1].scrollWidth <= box.clientWidth,
            pre: style(box).whiteSpace === 'pre', sideways: box.scrollWidth > box.clientWidth && style(box).overflowX === 'auto',
            sheetWidth: m.getBoundingClientRect().width, pageFits: document.documentElement.scrollWidth <= innerWidth,
            mono: style(box).fontFamily === style(m.querySelector('.session-git-list')).fontFamily,
            foot: box.querySelector('.sgl-foot').textContent, footButton: !!box.querySelector('.sgl-foot button.sgl-load'),
            scrolls: box.scrollHeight > box.clientHeight};
    })()""")
    assert log["count"] == 100 and log["first"] == "4f2c9ab " + DEMO_GIT_LONG_SUBJECT and log["last"] == "4f2c948 Entry 151", log
    assert log["hash"] == "4f2c9ab" and log["hashHelp"] and log["mono"], log
    assert log["oneLine"] and log["pre"] and log["sideways"] and log["pageFits"] and log["sheetWidth"] == 640, log
    assert log["foot"] == "Load 100 more" and log["footButton"] and log["scrolls"], log
    # a real scroll to the foot asks for the next hundred, once; the lines
    # already shown keep their place under it
    spot = await evaluate(a, """(() => { const box = document.querySelector('.session-git-log');
        box.scrollIntoView({block: 'center'});
        const r = box.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2, inView: r.top >= 0 && r.bottom <= innerHeight}; })()""")
    assert spot["inView"], spot
    for _ in range(40):
        asked = await evaluate(a, pages + " >= 2")
        if asked:
            break
        await a.call("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": spot["x"], "y": spot["y"],
                     "deltaX": 0, "deltaY": 400}, session=a.page_session)
        await asyncio.sleep(0.05)
    await until(a, pages + " === 2 && document.querySelectorAll('.session-git-log .sgl-line').length === 200")
    more = await evaluate(a, """(() => {
        const box = document.querySelector('.session-git-log'), lines = box.querySelectorAll('.sgl-line');
        return {hundredth: lines[100].textContent, last: lines[199].textContent, foot: box.querySelector('.sgl-foot').textContent,
            kept: lines[99].getBoundingClientRect().bottom <= lines[100].getBoundingClientRect().top + 1,
            pages: %s}; })()""" % pages)
    assert more["hundredth"] == "4f2c947 Entry 150" and more["last"] == "4f2c8e4 Entry 51", more
    assert more["foot"] == "Load 50 more" and more["kept"] and more["pages"] == 2, more
    # a press on the mark opened the sheet and nothing else: the row it sits
    # on was not selected by it
    assert await evaluate(a, "document.querySelector('.sess-item.active') ? document.querySelector('.sess-item.active').querySelector('.si-name').textContent : ''") == active
    # Refresh reads again, once per press
    button = await evaluate(a, "(() => { const b = document.querySelector('#session-git-refresh'); b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()")
    for kind in ("mousePressed", "mouseReleased"):
        await a.call("Input.dispatchMouseEvent", {"type": kind, "x": button["x"], "y": button["y"],
                     "button": "left", "clickCount": 1}, session=a.page_session)
    await until(a, reads + " === 2 && !document.querySelector('.session-git-modal').hasAttribute('aria-busy') && " + pages + " === 3")
    assert await evaluate(a, "document.querySelectorAll('.session-git-modal .sgl-rows').length === 3")
    assert await evaluate(a, "document.querySelectorAll('.session-git-log .sgl-line').length === 100"), "a fresh read starts the history over"
    # Back closes the sheet; Forward opens a fresh one, read anew
    await evaluate(a, "history.back(); true")
    await until(a, "!document.querySelector('.session-git-modal')")
    await evaluate(a, "history.forward(); true")
    await until(a, "!!document.querySelector('.session-git-modal') && " + reads + " === 3 && "
                   "!document.querySelector('.session-git-modal').hasAttribute('aria-busy') && " + pages + " === 4")
    # a phone: the commit's author and time step under the subject, and the
    # sheet keeps to the screen
    await a.call("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844,
                 "deviceScaleFactor": 1, "mobile": True}, session=a.page_session)
    await evaluate(a, "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    phone = await evaluate(a, """(() => {
        const m = document.querySelector('.session-git-modal'), commit = m.querySelector('.sgl-commits');
        const meta = commit.lastElementChild.getBoundingClientRect(), subject = commit.children[1].getBoundingClientRect();
        const row = m.querySelector('.m-btns').getBoundingClientRect();
        const buttons = Array.from(m.querySelectorAll('.m-btns .btn'));
        const boxes = buttons.map(b => b.getBoundingClientRect());
        return {under: meta.top >= subject.bottom - 1 && Math.abs(meta.left - subject.left) < 1,
            fits: m.getBoundingClientRect().right <= innerWidth && document.documentElement.scrollWidth <= innerWidth,
            listFits: m.querySelector('.session-git-list').scrollWidth <= m.querySelector('.session-git-list').clientWidth,
            names: buttons.map(b => b.textContent),
            /* two lines of two: Revert and Close, then Refresh and Push, each
               pair sharing a line and the row's two edges, the second line
               below the first, every word inside its button */
            lines: [Math.abs(boxes[0].top - boxes[1].top) < 1, Math.abs(boxes[2].top - boxes[3].top) < 1, boxes[2].top > boxes[0].bottom],
            edges: [Math.abs(boxes[0].left - row.left) < 1, Math.abs(boxes[1].right - row.right) < 1,
                    Math.abs(boxes[2].left - row.left) < 1, Math.abs(boxes[3].right - row.right) < 1],
            widths: boxes.map(b => Math.round(b.width)),
            words: buttons.every(b => b.scrollWidth <= b.clientWidth)};
    })()""")
    assert phone["under"] and phone["fits"] and phone["listFits"], phone
    assert phone["names"] == ["Revert", "Close", "Refresh", "Push"], phone
    assert all(phone["lines"]) and all(phone["edges"]) and phone["words"], phone
    assert len(set(phone["widths"])) == 1 and phone["widths"][0] > 100, phone
    button = await evaluate(a, "(() => { const b = document.querySelector('#session-git-close'); b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()")
    for kind in ("mousePressed", "mouseReleased"):
        await a.call("Input.dispatchMouseEvent", {"type": kind, "x": button["x"], "y": button["y"],
                     "button": "left", "clickCount": 1}, session=a.page_session)
    await until(a, "!document.querySelector('.session-git-modal')")
    await a.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 900,
                 "deviceScaleFactor": 1, "mobile": False}, session=a.page_session)

    # The actions, by real clicks, against a node whose push and revert are
    # stubbed to move the invented record the way the real ones move a
    # repository's: the route, the guards and the fresh look are the real
    # ones. Push takes the commit off the row's mark and the button off the
    # row, and says so in a toast.
    record, listing = DEMO_GIT[garden["cwd"]], DEMO_GIT_DETAIL[garden["cwd"]]
    kept, kept_listing = dict(record), dict(listing)
    clicks = "performance.getEntriesByType('resource').filter(e => /\\/api\\/sessions\\/%d\\/git\\/(push|revert)$/.test(e.name)).length" % garden["id"]
    mark = "Array.from(document.querySelectorAll('.sess-item')).find(row => row.querySelector('.si-name').textContent === 'Garden planner').querySelector('.si-git').getAttribute('aria-label')"

    def fake_push(root):
        assert root == garden["cwd"], root
        record["unpushed"] = 0
        listing.update({"commits": [], "ahead": 0})
        return {"to": "origin/main"}

    def fake_revert(root, cwd):
        assert (root, cwd) == (garden["cwd"], garden["cwd"]), (root, cwd)
        record.update({"changes": 0, "staged": 0, "unstaged": 0, "untracked": 0})
        listing["paths"] = []

    async def press(selector):
        # the sheet is taller than the screen with its History listed, and a
        # real click needs its button on the screen
        spot = await evaluate(a, "(() => { const b = document.querySelector(%s); b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()" % json.dumps(selector))
        for kind in ("mousePressed", "mouseReleased"):
            await a.call("Input.dispatchMouseEvent", {"type": kind, "x": spot["x"], "y": spot["y"],
                         "button": "left", "clickCount": 1}, session=a.page_session)

    try:
        await evaluate(a, "modalSessionGit(0, state.sessions.find(s => s.id === %d)); true" % garden["id"])
        await until(a, "!!document.querySelector('.session-git-modal') && !document.querySelector('.session-git-modal').hasAttribute('aria-busy')")
        assert await evaluate(a, mark) == "Git repository · 3 uncommitted changes · 1 unpushed commit · open details"
        with patch.object(session_git, "_push", fake_push), patch.object(session_git, "_revert", fake_revert):
            await press("#session-git-push")
            await until(a, clicks + " === 1 && !document.querySelector('.session-git-modal').hasAttribute('aria-busy')")
            after = await evaluate(a, """(() => { const m = document.querySelector('.session-git-modal');
                return {buttons: Array.from(m.querySelectorAll('.m-btns .btn')).map(b => b.textContent),
                    caption: m.querySelectorAll('.session-git-section .field-lbl')[1].textContent,
                    state: Array.from(m.querySelectorAll('.ws-fact')).find(row => row.querySelector('.field-lbl').textContent === 'State').querySelector('.wsf-v').textContent,
                    toast: (document.querySelector('#toasts .toast:last-child .toast-text') || {}).textContent,
                    error: m.querySelector('.form-error').classList.contains('hidden'),
                    focused: document.activeElement === m.querySelector('#session-git-close')}; })()""")
            assert after["buttons"] == ["Revert", "Close", "Refresh"], after
            assert after["caption"] == "Unpushed commits · 0" and after["state"] == "Uncommitted work", after
            assert after["toast"] == "Pushed 1 commit to origin/main" and after["error"] and after["focused"], after
            assert await evaluate(a, mark) == "Git repository · 3 uncommitted changes · nothing to push · open details"
            # Revert asks first - a destructive confirm, Cancel focused - and
            # its Cancel sends nothing; the confirm's Revert does the rest
            await press("#session-git-revert")
            await until(a, "document.querySelectorAll('.modal').length === 2 && document.activeElement && document.activeElement.id === 'mc-no'")
            confirm = await evaluate(a, """(() => { const m = document.querySelectorAll('.modal')[1];
                return {title: m.querySelector('h2').textContent, subject: m.querySelector('.modal-subject').textContent,
                    copy: m.querySelector('.modal-copy:not(.modal-subject)').textContent,
                    verb: m.querySelector('#mc-yes').textContent, red: m.querySelector('#mc-yes').classList.contains('btn-danger')}; })()""")
            assert confirm["title"] == "Revert 3 changes?" and confirm["subject"] == "/home/mira/projects/garden", confirm
            assert confirm["copy"].startswith("Every uncommitted change in the work tree on main is discarded: 1 staged, 1 unstaged, 1 untracked."), confirm
            assert confirm["verb"] == "Revert" and confirm["red"], confirm
            await press("#mc-no")
            await until(a, "document.querySelectorAll('.modal').length === 1")
            assert await evaluate(a, clicks) == 1
            await press("#session-git-revert")
            await until(a, "document.querySelectorAll('.modal').length === 2")
            await press("#mc-yes")
            await until(a, clicks + " === 2 && document.querySelectorAll('.modal').length === 1 && !document.querySelector('.session-git-modal').hasAttribute('aria-busy')")
            after = await evaluate(a, """(() => { const m = document.querySelector('.session-git-modal');
                return {buttons: Array.from(m.querySelectorAll('.m-btns .btn')).map(b => b.textContent),
                    caption: m.querySelectorAll('.session-git-section .field-lbl')[0].textContent,
                    state: Array.from(m.querySelectorAll('.ws-fact')).find(row => row.querySelector('.field-lbl').textContent === 'State').querySelector('.wsf-v').textContent,
                    empty: Array.from(m.querySelectorAll('.sgl-empty')).map(node => node.textContent),
                    toast: (document.querySelector('#toasts .toast:last-child .toast-text') || {}).textContent}; })()""")
            assert after["buttons"] == ["Close", "Refresh"], after
            assert after["caption"] == "Changes · 0" and after["state"] == "Nothing to commit or push", after
            assert after["empty"] == ["Nothing to commit", "Nothing to push"], after
            assert after["toast"] == "Reverted 3 changes", after
            assert await evaluate(a, mark) == "Git repository · nothing to commit or push · open details"
        await press("#session-git-close")
        await until(a, "!document.querySelector('.session-git-modal')")
    finally:
        # the invented heads-up is put back for the lanes after this one
        record.clear()
        record.update(kept)
        listing.clear()
        listing.update(kept_listing)
        session_git._store(garden["cwd"], demo_git(garden["cwd"]))
        runner.broadcast_sessions()
    await until(a, mark + " === 'Git repository · 3 uncommitted changes · 1 unpushed commit · open details'")
    print("PASS: the Git sheet opened by a real click on the orange mark - facts, grouped paths and the "
          "unpushed commit in the console's own type, read over the node's route, read again by Refresh, "
          "closed by Back and reopened fresh by Forward, and fitting a phone with its four buttons on two "
          "lines; the History's first hundred lines unwrapped in a box that scrolls sideways, the next "
          "hundred by a real scroll to its foot, and a fresh read starting it over; Push and Revert by "
          "real clicks over the node's routes, the confirm before a revert, each taking its count off the "
          "mark and its button off the row with a toast", flush=True)


async def checks(a, b, hub, capture=False):
    await background_count_checks(a, b, hub)
    await git_mark_checks(a, b)
    await git_sheet_checks(a)
    await icon_alignment_checks(a)
    await scrollbar_corner_checks(a, capture)
    await text_inset_checks(a)
    await background_task_checks(a, capture)
    await queue_expand_checks(a, capture)
    await message_reuse_checks(a)
    await spell_check_checks(a, capture)
    await workspace_move_checks(a, capture)
    await session_mention_checks(a)
    await pane_resize_checks(a)
    await reading_place_checks(a)
    await task_strip_verbs_checks(a)
    await drag_scroll_checks(a)
    await narrow_composer_checks(a, capture)
    await composer_enter_checks(a, capture)
    await attachment_steering_checks(a, hub)
    await context_menu_checks(a, capture)
    await workspace_footer_checks(a, capture)
    await new_session_choices_checks(a, capture)
    await reply_image_checks(a, capture)
    await side_question_wrap_checks(a, capture)
    await status_color_checks(a, capture)
    await identity_pill_checks(a, capture)
    await vnc_throughput_checks(a, capture)
    await vnc_connection_checks(a)
    await operation_cancellation_checks(a)
    await timer_error_checks(a, capture)
    await usage_error_checks(a, capture)
    await host_panel_checks(a, capture)
    await notices_panel_checks(a, capture)
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
    # Typing on one console puts the other console's caret on the same spot
    # and brings that spot into view, so a draft picked up on the next device
    # continues exactly where the last one left off: after the text typed at
    # the end, at the insertion made in the middle, then at the top, with a
    # long draft scrolling the box to wherever the writing happens.
    await evaluate(b, "demoView.composer.ta.blur(); true")
    caret_b = "[demoView.composer.ta.selectionStart, demoView.composer.ta.selectionEnd]"
    await type_text(a, "Ship the fix")
    await until(b, "demoView.composer.text() === 'Ship the fix' && " + caret_b + ".join() === '12,12'")
    await evaluate(a, "demoView.composer.ta.setSelectionRange(5, 5); true")
    await type_text(a, "login ")
    await until(b, "demoView.composer.text() === 'Ship login the fix' && " + caret_b + ".join() === '11,11'")
    lines = "\n".join("line %d" % n for n in range(1, 41))
    middle = lines.index("line 20")
    await evaluate(a, "demoView.composer.set(%s); true" % json.dumps(lines))
    await until(b, "demoView.composer.text().endsWith('line 40') && " + caret_b + ".join() === '%d,%d'"
                % (len(lines), len(lines)))
    scroll_b = ("({top:demoView.composer.ta.scrollTop, room:demoView.composer.ta.scrollHeight-demoView.composer.ta.clientHeight,"
                " line:parseFloat(getComputedStyle(demoView.composer.ta).lineHeight)})")
    scrolled = await evaluate(b, scroll_b)
    assert scrolled["room"] > scrolled["line"] and scrolled["room"] - scrolled["top"] < scrolled["line"], scrolled
    await evaluate(a, "demoView.composer.ta.setSelectionRange(%d, %d); true" % (middle, middle))
    await type_text(a, "Mid: ")
    await until(b, "demoView.composer.text().includes('Mid: line 20') && " + caret_b + ".join() === '%d,%d'"
                % (middle + 5, middle + 5))
    scrolled = await evaluate(b, scroll_b)
    assert scrolled["line"] < scrolled["top"] < scrolled["room"] - scrolled["line"], scrolled
    await evaluate(a, "demoView.composer.ta.setSelectionRange(0, 0); true")
    await type_text(a, "Top: ")
    await until(b, "demoView.composer.text().startsWith('Top: line 1') && " + caret_b + ".join() === '5,5'")
    scrolled = await evaluate(b, scroll_b)
    assert scrolled["top"] < scrolled["line"], scrolled
    await evaluate(a, "demoView.composer.set(''); true")
    await until(b, "demoView.composer.text() === '' && !demoView.sharedDraft.flight")
    await until(a, "!demoView.sharedDraft.flight")
    print("PASS: a peer's typing moves this console's caret to the same spot and scrolls a long draft to it", flush=True)
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
    # Invented live work demonstrates the count through the real snapshot
    # contract. It is separate from the completed transcript's earlier task.
    preview_hub = runner.hub(1)
    preview_hub.status = "running"
    preview_hub._set_background_tasks([
        {"id": "demo-watch", "type": "local_bash", "description": "Layout watcher"},
        {"id": "demo-agent", "type": "local_agent", "description": "Check navigation"}])
    for viewer in list(preview_hub.watchers):
        await preview_hub.attach_with_snapshot(viewer)
    await until(instance, "demoView.root.querySelector('.chip.background-tasks')?.textContent === '2 tasks'")
    await until(instance, "document.querySelectorAll('.task-tab .prompt-status-label').length === 2")
    for width, height, scale, name in [(1440, 900, 1, "desktop"), (390, 844, 2, "mobile")]:
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": scale,
            "mobile": name == "mobile"}, session=instance.page_session)
        if name == "desktop":
            await evaluate(instance, "openSearchTab(null, 'dashboard'); splitTabIntoPane('search',workspacePaneForTab('s:0:1').id,'right'); state.layout.ratio=.55; renderTabs(); true")
        else:
            await evaluate(instance, "closeTab('search'); activateTab('s:0:1'); true")
        for theme in ("dark", "light"):
            await evaluate(instance, "applyTheme(" + json.dumps(theme) + "); demoView.sharedDraft.count=0; demoView.sharedDraft.paint(); demoView.composer.ta.blur(); demoView.scrollBottom(true); true")
            await evaluate(instance, "demoView.root.querySelector('.chip.background-tasks').scrollIntoView({block:'nearest',inline:'nearest'}); true")
            await asyncio.sleep(.3)
            data = await instance.call("Page.captureScreenshot", {"format": "png"}, session=instance.page_session)
            (assets / (name + "-" + theme + ".png")).write_bytes(base64.b64decode(data["data"]))
    await evaluate(instance, "applyTheme('dark'); $('app').classList.add('side-open'); true")
    await asyncio.sleep(.3)
    data = await instance.call("Page.captureScreenshot", {"format": "png"}, session=instance.page_session)
    (assets / "mobile-dark-sidebar.png").write_bytes(base64.b64decode(data["data"]))

    # The Timeouts panel previews linked from docs/timeouts.md come from this
    # same invented world, so adding or renaming a field cannot leave them
    # showing a card the console no longer has.
    await evaluate(instance, "$('app').classList.remove('side-open'); "
                             "closeTab('search'); openSettingsTab(null); true")
    await until(instance, "!!document.querySelector('.timeouts-card .timer-row')")
    for width, height, scale, name in [(1440, 900, 1, "desktop"), (390, 844, 2, "mobile")]:
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": scale,
            "mobile": name == "mobile"}, session=instance.page_session)
        for theme in ("dark", "light"):
            # The card fits a desktop pane whole; a phone can only ever show
            # part of it, so frame it from its heading down.
            block = "start" if name == "mobile" else "center"
            await evaluate(instance, "applyTheme(" + json.dumps(theme) + "); "
                           "document.querySelector('.timeouts-card')"
                           ".scrollIntoView({block:" + json.dumps(block) + "}); true")
            await asyncio.sleep(.3)
            data = await instance.call("Page.captureScreenshot", {"format": "png"},
                                       session=instance.page_session)
            (assets / ("timeouts-" + name + "-" + theme + ".png")).write_bytes(
                base64.b64decode(data["data"]))
    print("PASS: regenerated nine console screenshots with invented demo data",
          flush=True)


async def quota_checks(instance):
    result = await evaluate(instance, """(() => {
        const saved = {engines: state.engines, backends: state.backends,
                       engCache: state.engCache, remoteOk: state.remoteOk};
        const now=Date.now()/1000;
        const make=(used, at) => ({key:'codex', label:'Codex', installed:true,
            auth:'ok', update_available:false, usage_monitor:{version:1,
            provider:'quota-test', account:'b'.repeat(64), bucket:'codex',
            window_minutes:10080, sample:used===null?null:{used_percent:used,
            observed_at:at, resets_at:now+3600}}});
        try {
            state.engines=[make(20,now-50)];
            state.backends=[{id:999,name:'Workshop',capabilities:['account-quota-v1']}];
            state.engCache={999:[make(44,now-10)]}; state.remoteOk={999:true};
            renderFootEngines();
            const shared=[...document.querySelectorAll('.st-quota')].map(e=>e.textContent);
            const titles=[...document.querySelectorAll('.st-quota')].map(e=>e.title);
            state.engCache[999][0].usage_monitor.account='c'.repeat(64);
            state.engCache[999][0].usage_monitor.sample=null;
            renderFootEngines();
            const after=[...document.querySelectorAll('.st-quota')].map(e=>e.textContent);
            return {shared,titles,after};
        } finally {
            Object.assign(state,saved); sharedQuotaObservations.clear(); renderFootEngines();
        }
    })()""")
    assert result["shared"] == ["56% wk", "56% wk"], result
    assert all("reading from Workshop" in title for title in result["titles"]), result
    assert result["after"] == ["56% wk"], result
    print("PASS: real footer shares only matching accounts and retains the newest observation", flush=True)


async def session_activity_checks(a, b):
    """Two real consoles, an unopened session, and an aged attach snapshot."""
    h = runner.hub(2)  # neither console has this session's transcript socket
    h.status = "running"
    h.active_since = time.time() - 125
    try:
        # Model a publication made two minutes ago without waiting two minutes.
        # The real update writer, HTTP bootstrap and browser clocks do the rest.
        old_clock = SimpleNamespace(time=lambda: time.time() - 120,
                                    monotonic=lambda: time.monotonic() - 120)
        with patch.object(runner, "time", old_clock):
            runner.publish_state(runner.sessions_payload(), broadcast=False)
        expression = """(() => {
            const label=document.querySelector('[data-session-key="0:2"] .active-time');
            if (!label) return -1;
            return label.textContent.split(':').reduce((n, part) => n*60+Number(part), 0);
        })()"""

        async def reload_and_read(instance):
            await evaluate(instance, "window.activityReloadMarker=true")
            await instance.call("Page.reload", session=instance.page_session)
            await until(instance, "!window.activityReloadMarker && "
                        "typeof localStateStreamTopics !== 'undefined' && "
                        "localStateStreamTopics.has('sessions')")
            seconds = await evaluate(instance, expression)
            assert 124 <= seconds < 145, seconds
            return seconds

        first = await reload_and_read(a)
        second = await reload_and_read(b)
        assert abs(first - second) <= 3, (first, second)
        again = await reload_and_read(a)
        assert again >= first, (first, again)

        # Reconnect only the state socket, retaining the page and its anchors.
        await evaluate(a, "window.activitySocket=updatesWs; updatesWs.close(); true")
        await until(a, "updatesWs && updatesWs !== activitySocket && "
                    "localStateStreamTopics.has('sessions')")
        assert await evaluate(a, expression) >= again

        h.status = "idle"
        h.active_since = None
        runner.broadcast_sessions()
        for instance in (a, b):
            await until(instance, expression + " === -1")
        h.status = "running"
        h.active_since = time.time()
        runner.broadcast_sessions()
        for instance in (a, b):
            await until(instance, expression + " >= 0")
            assert await evaluate(instance, expression) < 5
    finally:
        h.status = "idle"
        h.active_since = None
        runner.broadcast_sessions()
        for instance in (a, b):
            await evaluate(instance, "window.demoView=state.views['s:0:1'].activeView(); true")
            await until(instance, "demoView.draftReady")
    print("PASS: backend session clock survives reloads, a second browser and socket reconnect; idle clears it and new work starts from zero", flush=True)


async def engine_activity_checks(instance):
    result = await evaluate(instance, """(() => {
        const saved = {engines:state.engines, backends:state.backends,
            engCache:state.engCache, remoteOk:state.remoteOk,
            sessions:state.sessions, remoteSessions:state.remoteSessions,
            sessionFilter:state.sessionFilter};
        const engine = key => ({key,label:key,installed:true,auth:'ok',update_available:true});
        const read = () => [...document.querySelectorAll('.foot-eng')].map(row => {
            const icon=row.querySelector('.foot-engine-activity');
            const word=row.querySelector('.st-word');
            if (!icon) {
                const status=row.querySelector('.st');
                if (status.firstElementChild !== word) {
                    throw new Error('Idle engine status must start with its status word');
                }
                if (status.className !== 'st' || getComputedStyle(status).display !== 'block' ||
                    getComputedStyle(status).whiteSpace !== 'normal') {
                    throw new Error('Idle engine status must retain its original layout');
                }
                return null;
            }
            const a=icon.getBoundingClientRect(), b=word.getBoundingClientRect();
            const separator=icon.nextElementSibling;
            if (!separator.classList.contains('st-sep') || separator.textContent !== ' · ' ||
                separator.nextElementSibling !== word) {
                throw new Error('Active engine status must separate spinner and word with a middle dot');
            }
            const css=getComputedStyle(icon.querySelector('svg'));
            const probe=document.createElement('span');
            probe.style.color='var(--ok)'; row.appendChild(probe);
            const green=getComputedStyle(icon).color===getComputedStyle(probe).color;
            probe.remove();
            return {label:icon.getAttribute('aria-label'), width:a.width,height:a.height,
                gap:b.left-a.right, separatorWidth:separator.getBoundingClientRect().width,
                centered:Math.abs((a.top+a.height/2)-(b.top+b.height/2))<1,
                green, word:word.textContent,
                warning:word.classList.contains('warn'), animation:css.animationName,
                duration:css.animationDuration};
        });
        try {
            state.engines=[engine('claude'),engine('codex')];
            state.backends=[{id:999,name:'Workshop',capabilities:[]}];
            state.engCache={999:[engine('claude'),engine('codex')]};
            state.remoteOk={999:true};
            state.sessions=[{id:1,engine:'claude',model:'demo-a',status:'running',task:{parent:2}},
                {id:2,engine:'codex',model:'demo-b',status:'idle',queue:['waiting']}];
            state.remoteSessions={999:[{id:1,engine:'codex',model:'demo-c',status:'running'}]};
            state.sessionFilter='no matching sessions';
            renderFootEngines();
            const active=read();
            state.sessions[0].status='idle'; renderFootEngines();
            const stopped=read();
            state.remoteOk[999]=false; renderFootEngines();
            const offline=read();
            return {active,stopped,offline};
        } finally { Object.assign(state,saved); renderFootEngines(); }
    })()""")
    local, idle, other_idle, remote = result["active"]
    assert idle is None and other_idle is None, result
    for entry, model in [(local, "demo-a"), (remote, "demo-c")]:
        assert entry["label"] == "In use · " + model, result
        assert entry["width"] == entry["height"] == 10, result
        assert entry["gap"] == entry["separatorWidth"] > 0 and entry["centered"] and entry["green"], result
        assert entry["word"] == "Ready" and entry["warning"], result
        assert entry["animation"] == "spin" and entry["duration"] == "0.8s", result
    assert result["stopped"][:3] == [None, None, None], result
    assert result["stopped"][3] is not None, result
    assert result["offline"] == [None, None], result
    print("PASS: footer activity scopes running tasks to backend/engine, ignores queued/filtered rows, clears on stop/offline, and aligns a 10px spinner before Ready", flush=True)


async def terminal_io_checks(console):
    """Real input -> PTY -> xterm parsing/rendering, including resize delivery."""
    command = shlex.join([sys.executable, str(BASE / "tests/terminal_latency_bench.py"),
                          "--child", "--screen"])
    tab_id = terminal_id = ""
    try:
        tab_id = await evaluate(console, "openTermTab(0, %s, null, %s).id" %
                                (json.dumps(command), json.dumps(str(ROOT))))
        await evaluate(console, "window.ioView=state.views[%s]; true" % json.dumps(tab_id))
        await until(console, "!!ioView.term && !!ioView.dataSub")
        await evaluate(console, """window.ioText = () => {
            const b=ioView.term.buffer.active, lines=[];
            for (let i=Math.max(0,b.length-50);i<b.length;i++)
                lines.push(b.getLine(i).translateToString(true));
            return lines.join('\\n');
        }; true""")
        await until(console, "ioText().includes('ready')")
        terminal_id = await evaluate(console, "ioView.tab.terminalId")
        await evaluate(console, "ioView.term.focus(); true")
        await console.call("Input.insertText", {"text": "p"}, session=console.page_session)
        await until(console, "ioText().trimEnd().endsWith('p')")
        # Fractional container changes within one cell must not emit resize
        # messages. A grid change must still reach the foreground PTY process.
        await evaluate(console, """window.ioResizes=[];
            const send=ioView.ws.send.bind(ioView.ws);
            ioView.ws.send=data=>{if(typeof data==='string' && JSON.parse(data).type==='resize')
                ioResizes.push(JSON.parse(data)); send(data);};
            window.ioWidth=ioView.mount.getBoundingClientRect().width;
            ioView.mount.style.width=(ioWidth-.01)+'px'; true""")
        await evaluate(console, "new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
        assert await evaluate(console, "ioResizes.length") == 0
        await evaluate(console, "ioView.mount.style.width=(ioWidth-80)+'px'; true")
        await until(console, "ioResizes.length===1 && ioText().includes('RESIZE '+ioView.term.cols+'x'+ioView.term.rows)")
        # Exercise the retained xterm parser, including UTF-8 split over PTY
        # reads and a burst much larger than the server's single-callback cap.
        await evaluate(console, """window.ioStart=performance.now(); window.ioFinished=null;
            window.ioRendered=false;
            window.ioParsed=ioView.term.onWriteParsed(()=>{
                if(ioText().includes('BURST DONE: café € 終')) ioFinished=performance.now()-ioStart;
            });
            window.ioRender=ioView.term.onRender(()=>{
                if(ioText().includes('BURST DONE: café € 終')) ioRendered=true;
            }); ioView.term.focus(); true""")
        await console.call("Input.insertText", {"text": "b"}, session=console.page_session)
        await until(console, "ioFinished!==null && ioRendered")
        elapsed = await evaluate(console, "ioFinished")
        await evaluate(console, "ioParsed.dispose(); ioRender.dispose(); true")
        # Input remains usable after the burst; there was no output loss or
        # parser overflow. Closing the tab disposes the resize subscription.
        await console.call("Input.insertText", {"text": "p"}, session=console.page_session)
        await until(console, "ioText().trimEnd().endsWith('p')")
        print("PASS: real terminal input, 3.6 MB burst parsed/rendered (%.1f ms), Unicode and foreground resize" % elapsed,
              flush=True)
    finally:
        if tab_id:
            await evaluate(console, "closeTab(%s); true" % json.dumps(tab_id))
        if terminal_id:
            await terminal.manager().close(terminal_id, "test finished")


async def engine_terminal_checks(console, sid):
    """The footer's engine names as links to the engine's own CLI, in a real
    browser: the plain text's face to the pixel with the pointer's cursor the
    only tell; a real click opening a terminal the node starts as the CLI
    itself in its scratch home, the tab named for it; the CLI's own quit
    ending the pane with Close terminal and Start Claude Code; a fresh start
    from that button in the same home; the name pressed again going to the
    live tab; and Close terminal closing it on the node."""
    script = ROOT / "fake-claude"
    script.write_text("#!/bin/sh\nprintf 'PUPPY_CLI_HOME=%s\\n' \"$PWD\"\nread -r line\nexit 0\n",
                      encoding="utf-8")
    script.chmod(0o755)
    cli_tmp = ROOT / "tmp"
    cli_tmp.mkdir(exist_ok=True)
    home = cli_tmp / ("puppy-cli-%d" % os.geteuid()) / "claude"
    claude = next(driver for driver in all_drivers() if driver.key == "claude")

    def stub_cli(key):
        assert key == "claude", key
        return claude, str(script)

    local = "document.querySelector('#foot-engines .foot-engine-group[data-node-key=\"local\"]')"
    # The face, against the same rows drawn by a node without the route: the
    # name is a button whose letters stand exactly where the plain text's
    # stood, in the row's font and colour, with no box of its own, the status
    # word where it was - and the pointer's cursor, which the plain text
    # never had. The letters are read the same way in both: a range over the
    # text node, the button's own box being the line it sits on.
    face = await evaluate(console, """(() => {
        const local = () => %s;
        const measure = () => [...local().querySelectorAll('.foot-eng')].map(row => {
            const button = row.querySelector('.foot-eng-open');
            const text = button ? button.firstChild : [...row.childNodes].find(n => n.nodeType === 3);
            const range = document.createRange(); range.selectNode(text);
            const rect = range.getBoundingClientRect();
            const style = getComputedStyle(button || row);
            const status = row.querySelector('.st').getBoundingClientRect();
            return {name: text.textContent, link: !!button, cursor: style.cursor,
                type: [style.fontFamily, style.fontSize, style.fontWeight, style.lineHeight,
                       style.color, style.letterSpacing, style.textTransform].join('|'),
                box: [rect.left, rect.top, rect.width, rect.height].map(v => Math.round(v * 2) / 2),
                status: Math.round(status.left * 2) / 2,
                row: Math.round(row.getBoundingClientRect().height * 2) / 2,
                background: style.backgroundColor, border: style.borderStyle,
                padding: style.padding, margin: style.margin,
                label: button ? button.getAttribute('aria-label') : null};
        });
        const saved = state.nodeCapabilities;
        try {
            state.nodeCapabilities = saved.filter(c => c !== 'terminal-engine-cli');
            renderFootEngines();
            const plain = measure();
            state.nodeCapabilities = saved;
            renderFootEngines();
            return {plain, links: measure()};
        } finally { state.nodeCapabilities = saved; renderFootEngines(); }
    })()""" % local)
    assert [row["name"] for row in face["links"]] == ["Claude Code", "Codex", "OpenCode"], face
    for plain, link in zip(face["plain"], face["links"]):
        assert not plain["link"] and link["link"], (plain, link)
        assert plain["cursor"] != "pointer" and link["cursor"] == "pointer", (plain, link)
        assert link["label"] == "Open %s in a terminal on Studio" % link["name"], link
        for key in ("name", "type", "box", "status", "row"):
            assert plain[key] == link[key], (key, plain, link)
        assert link["background"] == "rgba(0, 0, 0, 0)" and link["border"] == "none", link
        assert link["padding"] == "0px" and link["margin"] == "0px", link

    tab_count = await evaluate(console, "state.tabs.length")
    button = "%s.querySelector('.foot-eng-open')" % local
    centre = "(() => { const r = %s.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2}; })()"

    async def click(selector):
        point = await evaluate(console, centre % selector)
        for kind in ("mousePressed", "mouseReleased"):
            await console.call("Input.dispatchMouseEvent", {
                "type": kind, **point, "button": "left", "clickCount": 1},
                session=console.page_session)

    async def press_enter():
        await evaluate(console, "cliView.term.focus(); true")
        for kind in ("keyDown", "keyUp"):
            await console.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "Enter", "code": "Enter",
                "text": "\r" if kind == "keyDown" else "",
                "windowsVirtualKeyCode": 13}, session=console.page_session)

    first = second = None
    try:
        with patch.object(terminal, "_engine_cli", stub_cli), \
                patch.object(terminal.tempfile, "gettempdir", return_value=str(cli_tmp)):
            # A real click on the name: one terminal tab, asking the node for
            # the engine, which starts the CLI itself where it keeps it.
            await click(button)
            await until(console, "state.tabs.length === %d && state.tabs[state.tabs.length - 1].type === 'term' && "
                                 "state.tabs[state.tabs.length - 1].engine === 'claude'" % (tab_count + 1))
            await evaluate(console, "window.cliTab = state.tabs[state.tabs.length - 1]; "
                                    "window.cliView = state.views[cliTab.id]; true")
            await until(console, "!!cliView && !!cliView.term && !!cliView.dataSub && !!cliTab.terminalId")
            await evaluate(console, """window.cliText = () => {
                const b = cliView.term.buffer.active, lines = [];
                for (let i = 0; i < b.length; i++) lines.push(b.getLine(i).translateToString(true));
                return lines.join('\\n');
            }; true""")
            first = terminal.manager().get(await evaluate(console, "cliTab.terminalId"))
            assert first.engine == "claude" and first.engine_label == "Claude Code", first.status_payload()
            assert first.cwd == str(home) and home.is_dir(), first.cwd
            assert first.command == shlex.quote(str(script)), first.command
            await until(console, "cliText().includes('PUPPY_CLI_HOME=%s')" % home)
            assert await evaluate(console, "cliTab.cmd === '' && cliTab.cwd === ''"), "the node chose both"
            # the tab is named for the CLI and is the one on screen
            assert await evaluate(console, "state.active === cliTab.id && document.querySelector('.tab.active .t-title').textContent") == \
                "Claude Code %s @ Studio" % first.terminal_id
            # the name pressed again goes to this tab and opens nothing
            await evaluate(console, "activateTab('s:0:%d'); true" % sid)
            await until(console, "state.active === 's:0:%d'" % sid)
            await click(button)
            await until(console, "state.active === cliTab.id")
            assert await evaluate(console, "state.tabs.length") == tab_count + 1
            assert terminal.manager().engine_instances("claude") == [first]
            # The CLI's own quit ends the terminal in the pane - under its
            # name, with Close terminal and Start Claude Code, no shell.
            await press_enter()
            await until(console, "cliView.isDead()")
            pane = await evaluate(console, """(() => {
                const dead = cliView.root.querySelector('.term-dead');
                return {message: dead.querySelector('.term-dead-message').textContent,
                    buttons: [...dead.querySelectorAll('button')].map(b => [b.textContent, b.classList.contains('btn-pri')])};
            })()""")
            assert pane == {"message": "Claude Code ended",
                            "buttons": [["Close terminal", False], ["Start Claude Code", True]]}, pane
            assert not first.running and first.ended_reason == "Claude Code ended"
            assert terminal.manager().engine_instances("claude") == []
            # Start Claude Code starts the CLI again: a new terminal in the
            # same home, the old one released on the node.
            await click("cliView.root.querySelector('.term-dead-new')")
            await until(console, "!cliView.isDead() && cliTab.terminalId !== %s && !!cliView.dataSub" %
                        json.dumps(first.terminal_id))
            second = terminal.manager().get(await evaluate(console, "cliTab.terminalId"))
            assert second.engine == "claude" and second.cwd == str(home), second.status_payload()
            await until(console, "cliText().includes('PUPPY_CLI_HOME=%s')" % home)
            try:
                terminal.manager().get(first.terminal_id)
            except terminal.TerminalError:
                pass
            else:
                raise AssertionError("the ended terminal was kept on the node")
            assert await evaluate(console, "document.querySelector('.tab.active .t-title').textContent") == \
                "Claude Code %s @ Studio" % second.terminal_id
            # Close terminal takes the tab and the node's terminal with it.
            await press_enter()
            await until(console, "cliView.isDead()")
            await click("cliView.root.querySelector('.term-dead-close')")
            await until(console, "!state.tabs.some(t => t.id === cliTab.id) && state.tabs.length === %d" % tab_count)
            try:
                terminal.manager().get(second.terminal_id)
            except terminal.TerminalError:
                pass
            else:
                raise AssertionError("Close terminal left the terminal on the node")
        print("PASS: footer engine names are links with the plain face; a real click opens the CLI in its "
              "scratch home, the CLI's quit ends the pane with Close terminal and Start Claude Code, "
              "a restart reuses the home, the name pressed again goes to the live tab", flush=True)
    finally:
        await evaluate(console, "if (window.cliTab && state.tabs.some(t => t.id === cliTab.id)) closeTab(cliTab.id); true")
        for instance in (first, second):
            if instance is not None:
                await terminal.manager().close(instance.terminal_id, "test finished")


async def browser_cursor_checks(console):
    """Actual CDP hit testing -> authenticated viewer socket -> image cursor."""
    page = await browser.manager().create()
    tab_id = ""
    try:
        await evaluate(console, "openBrowserTab(0, %s); true" % json.dumps(page.browser_id))
        await until(console, "Object.values(state.views).some(v => v instanceof BrowserView && v.cursorSupported)")
        await evaluate(console, "window.cursorView=Object.values(state.views).find(v => v instanceof BrowserView); true")
        tab_id = await evaluate(console, "cursorView.tab.id")
        await until(console, "cursorView.screen.classList.contains('live')")
        html = """<!doctype html><style>
          body {margin:0} .box {position:absolute;left:20px;width:180px;height:35px}
          #link:hover {cursor:zoom-in}
        </style>
        <a class=box id=link style="top:20px" href="#">Link</a>
        <input class=box style="top:70px" value="Text field">
        <div class=box style="top:120px;cursor:ew-resize">Resize</div>
        <div class=box id=dynamic style="top:170px;cursor:grab">Drag</div>
        <div class=box style="top:220px;cursor:url(data:image/png;base64,AA==),crosshair">Image fallback</div>
        <div class=box style="top:270px;cursor:none">Hidden cursor</div>
        <div class=box id=shadow style="top:320px"></div>
        <iframe class=box style="top:370px;border:0" srcdoc="<style>body{margin:0;cursor:help}</style>Frame"></iframe>
        <div class=box style="top:420px">Selectable text</div>
        <button class=box style="top:470px" disabled>Disabled</button>
        <div class=box style="top:520px;cursor:default">Default</div>
        <script>document.querySelector('#shadow').attachShadow({mode:'closed'}).innerHTML =
          '<div style="height:35px;cursor:cell">Closed shadow root</div>';</script>"""
        tree = await page.call("Page.getFrameTree", session=page.page_session)
        await page.call("Page.setDocumentContent", {
            "frameId": tree["frameTree"]["frame"]["id"], "html": html}, session=page.page_session)
        await until(page, "document.querySelector('iframe').contentDocument.body?.textContent === 'Frame'")

        async def hover(x, y, expected):
            # Map viewport CSS pixels onto the actual streamed image, also
            # exercising letterboxing and the input relay's normalized points.
            point = await evaluate(console, """(() => {const r=cursorView.screen.getBoundingClientRect();
                return {x:r.left+r.width*%s/%s, y:r.top+r.height*%s/%s};})()""" %
                (x, page.viewport["width"], y, page.viewport["height"]))
            await console.call("Input.dispatchMouseEvent", {"type":"mouseMoved", **point},
                               session=console.page_session)
            await until(console, "cursorView.screen.style.cursor === %s" % json.dumps(expected))

        for y, expected in [(25, "zoom-in"), (80, "text"), (130, "ew-resize"),
                            (180, "grab"), (230, "crosshair"), (280, "none"),
                            (330, "cell"), (380, "help"), (425, "text"),
                            (480, "default")]:
            await hover(30, y, expected)
        await hover(30, 80, "text")
        point = await evaluate(console, """(() => {const r=cursorView.screen.getBoundingClientRect();
            return {x:r.left+r.width*30/%s,y:r.top+r.height*80/%s};})()""" %
            (page.viewport["width"], page.viewport["height"]))
        for kind in ("mousePressed", "mouseReleased"):
            await console.call("Input.dispatchMouseEvent", {
                "type": kind, **point, "button": "left", "clickCount": 1},
                session=console.page_session)
        for kind in ("keyDown", "keyUp"):
            await console.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "z", "code": "KeyZ", "text": "z" if kind == "keyDown" else ""},
                session=console.page_session)
        await until(page, "document.querySelector('input').value.includes('z')")
        print("PASS: native click and typing traverse the embedded viewer into the real page input", flush=True)
        await hover(30, 180, "grab")
        # No mouse movement or visual change: CSS cursor alone must refresh.
        await evaluate(page, "document.querySelector('#dynamic').style.cursor='wait'; true")
        await until(console, "cursorView.screen.style.cursor === 'wait'")
        await hover(30, 25, "zoom-in")
        await until(console, "cursorView.screen.tagName === 'IMG' && cursorView.screen.naturalWidth === cursorView.frameW && cursorView.screen.naturalHeight === cursorView.frameH")
        await evaluate(console, "cursorView.onVisibility(false);true")
        await evaluate(page, """(() => {const cover=document.createElement('div');
          cover.style.cssText='position:fixed;inset:0;background:rgb(19,93,171)';
          document.body.appendChild(cover);return true;})()""")
        assert await evaluate(console, "cursorView.fpsFrames === 0")
        await evaluate(console, "cursorView.onVisibility(true);true")
        await until(console, """(() => {
          const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
          const ctx=canvas.getContext('2d');
          ctx.drawImage(cursorView.screen,20,20,1,1,0,0,1,1);
          const p=ctx.getImageData(0,0,1,1).data;
          return Math.abs(p[0]-19)<5 && Math.abs(p[1]-93)<5 && Math.abs(p[2]-171)<5;
        })()""")
        print("PASS: real decoded image dimensions, hidden-viewer draw suppression and fresh pixels on resume", flush=True)
        await hover(31, 25, "default")
        await page.call("Page.navigate", {"url":"about:blank"}, session=page.page_session)
        await until(console, "cursorView.screen.style.cursor === 'default'")
        await console.call("Input.dispatchMouseEvent", {"type":"mouseMoved", "x":1,"y":1},
                           session=console.page_session)
        await until(console, "cursorView.cursorPoint === null && cursorView.cursorTimer === null && cursorView.screen.style.cursor === ''")
        print("PASS: real embedded cursor hover CSS, inputs, resize, image fallback, none, closed shadow DOM, iframe, text, disabled controls, stationary changes, navigation and leave", flush=True)
    finally:
        if tab_id:
            await evaluate(console, "closeTab(%s); true" % json.dumps(tab_id))
        await browser.manager().close(page.browser_id, "Cursor test finished")


async def host_section_checks(instance, capture=False):
    # Add an invented reachable peer to exercise Latency as well as the two
    # sections a standalone instance has. Pause polling while using this data.
    # An ordinary state refresh replaces state.backends and would drop the
    # invented peer mid-check, so pin it behind an accessor for the duration
    # rather than re-injecting it and hoping no refresh lands in between.
    await evaluate(instance, """window.sectionSaved={backends:state.backends, remoteOk:state.remoteOk};
        clearTimeout(hostPanel.timer); hostPanel.timer=null;
        window.sectionPeer={id:998,name:'Workshop',capabilities:[]};
        window.sectionBackends=state.backends;
        Object.defineProperty(state,'backends',{configurable:true,
            get(){return [...window.sectionBackends, window.sectionPeer];},
            set(value){window.sectionBackends=value.filter(b=>b.id!==998);}});
        window.sectionOk={...state.remoteOk,998:true};
        window.sectionOkView=new Proxy(window.sectionOk,{
            get:(t,k)=>k==='998'?true:t[k],
            set:(t,k,v)=>{if(k!=='998')t[k]=v; return true;}});
        Object.defineProperty(state,'remoteOk',{configurable:true,
            get(){return window.sectionOkView;},
            set(value){Object.assign(window.sectionOk, value);}});
        hostPanel.latency=[{id:998,name:'Workshop',ok:true,ms:2.4}];
        renderHostPanel(); true""")
    # Warm the controller's real sampler without a panel read, then let the
    # first authenticated latency GET draw that history in both viewport sizes.
    peer = web.Application()
    async def ping(_request):
        return web.json_response({"ok": True})
    peer.router.add_get("/api/ping", ping)
    server = TestServer(peer)
    await server.start_server()
    url = str(server.make_url("")).rstrip("/")
    db.execute("INSERT INTO backends(id,name,url,urls,token,created_at) VALUES(?,?,?,?,?,?)",
               (998, "Workshop", url, json.dumps([url]), "demo-token", time.time()))
    backends._mark_backend_online(998)
    interval = backends.LATENCY_INTERVAL_SECONDS
    backends.LATENCY_INTERVAL_SECONDS = .02
    await backends.start_latency_worker(None)
    try:
        deadline = time.monotonic() + 3
        while len(backends._latency.get(998, {}).get("history", [])) < 3:
            assert time.monotonic() < deadline, "latency worker did not collect history"
            await asyncio.sleep(.01)
        backends.LATENCY_INTERVAL_SECONDS = interval
        drawn = await evaluate(instance, """(async () => {
            hostPanel.pings.clear(); hostPanel.latency=[];
            await readHostLatency(hostPanel.sequence);
            return {count:hostPanel.pings.get(998)?.length,
                chart:!!document.querySelector('.host-ping .host-chart.spark')};
        })()""")
        assert drawn["count"] >= 3 and drawn["chart"], drawn
        for width, height in [(1440, 900), (390, 844)]:
            await instance.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1,
                "mobile": width < 900}, session=instance.page_session)
            await evaluate(instance, "$('app').classList.toggle('side-open', innerWidth<900); true")
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(%s); true" % json.dumps(theme))
                await asyncio.sleep(.3)
                spacing = await evaluate(instance, """(() => {
                    const backend=document.querySelector('.foot-engine-head');
                    const control=backend.querySelector('.disclosure-toggle');
                    return [...document.querySelectorAll('.host-sec-head')].every(head=>{
                        const button=head.querySelector('.disclosure-toggle');
                        return getComputedStyle(head).gap===getComputedStyle(backend).gap &&
                            getComputedStyle(button).marginLeft===getComputedStyle(control).marginLeft &&
                            button.getBoundingClientRect().width===control.getBoundingClientRect().width &&
                            button.getAttribute('aria-expanded')==='true';
                    });
                })()""")
                assert spacing, (width, theme)
                for key in ("cpu", "latency", "processes"):
                    selector = "#host-section-" + key
                    point = await evaluate(instance, """(() => {
                        const button=document.querySelector(%s+' .disclosure-toggle');
                        if(!button) return {missing:true, open:hostPanel.open,
                            ids:[...$('foot-host').children].map(c=>c.id),
                            backends:state.backends.map(b=>b.id),
                            remoteOk:JSON.stringify(state.remoteOk),
                            latency:hostPanel.latency.length,
                            err:hostPanel.latencyError};
                        button.scrollIntoView({block:'nearest'});
                        const r=button.getBoundingClientRect();
                        return {x:r.x+r.width/2,y:r.y+r.height/2};
                    })()""" % json.dumps(selector))
                    for kind in ("mousePressed", "mouseReleased"):
                        await instance.call("Input.dispatchMouseEvent", {
                            "type": kind, **point, "button": "left", "clickCount": 1},
                            session=instance.page_session)
                    # An update during the slide must keep the same button,
                    # focus, collapsed choice and running animation.
                    kept = await evaluate(instance, """(() => {
                        const section=document.querySelector(%s);
                        const button=section.querySelector('.disclosure-toggle');
                        const body=section.querySelector('.host-sec-body');
                        const moving=body.classList.contains('disclosure-animating');
                        renderHostPanel(); hostCpuSample(22);
                        return {moving, focused: document.activeElement===button,
                            activeClass: document.activeElement ? document.activeElement.className : null,
                            same: document.getElementById(body.id)===body,
                            expanded: button.getAttribute('aria-expanded'),
                            hidden: body.hidden,
                            ids:[...$('foot-host').children].map(c=>c.id),
                            backends:state.backends.map(b=>b.id)};
                    })()""" % json.dumps(selector))
                    assert not point.get("missing"), (width, theme, key, point)
                    assert kept and kept["moving"] and kept["focused"] and kept["same"] \
                        and kept["expanded"] == "false", (width, theme, key, kept)
                    await until(instance, "document.querySelector(%s+' .host-sec-body').hidden" %
                                json.dumps(selector))
                assert await evaluate(instance, "document.querySelectorAll('.host-sec-body[hidden]').length===3")
                assert await evaluate(instance, "$('foot-host').scrollHeight <= $('foot-host').clientHeight"), "Collapsed sections must not leave a scrollbar"
                # Native keyboard activation works with both Enter and Space.
                for key, name, code, number in [("cpu", "Enter", "Enter", 13),
                                               ("latency", " ", "Space", 32),
                                               ("processes", "Enter", "Enter", 13)]:
                    selector = "#host-section-" + key
                    await evaluate(instance, "document.querySelector(%s+' .disclosure-toggle').focus(); true" %
                                   json.dumps(selector))
                    for kind in ("keyDown", "keyUp"):
                        await instance.call("Input.dispatchKeyEvent", {
                            "type": kind, "key": name, "code": code,
                            "text": ("\r" if name == "Enter" else " ") if kind == "keyDown" else "",
                            "windowsVirtualKeyCode": number}, session=instance.page_session)
                    await until(instance, "document.querySelector(%s+' .disclosure-toggle').getAttribute('aria-expanded')==='true'" %
                                json.dumps(selector))
                await until(instance, "!document.querySelector('.host-sec-body.disclosure-animating')")
                if capture:
                    await evaluate(instance, "$('foot-host').scrollTop=0; true")
                    shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                               session=instance.page_session)
                    name = "desktop" if width > 900 else "phone"
                    (BASE / "data" / ("host-sections-" + name + "-" + theme + ".png")).write_bytes(
                        base64.b64decode(shot["data"]))
    finally:
        backends.LATENCY_INTERVAL_SECONDS = interval
        await backends.stop_latency_worker()
        db.execute("DELETE FROM backends WHERE id=998")
        backends._clear_backend_health(998)
        await server.close()
        await backends.close_client()
        await instance.call("Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 900, "deviceScaleFactor": 1,
            "mobile": False}, session=instance.page_session)
        await evaluate(instance, """delete state.backends; delete state.remoteOk;
            state.backends=sectionSaved.backends; state.remoteOk=sectionSaved.remoteOk;
            delete window.sectionSaved; delete window.sectionPeer; delete window.sectionBackends;
            delete window.sectionOk; delete window.sectionOkView;
            hostPanel.collapsed.clear();
            $('app').classList.remove('side-open'); applyTheme('dark'); renderHostPanel(); true""")
    print("PASS: background latency history on the first panel read, independent host section chevrons, backend spacing, live updates during slides, keyboard focus and Enter/Space on desktop and phone in both themes", flush=True)


async def host_panel_checks(instance, capture=False):
    """The CPU reading's box, in a real browser: a real click opens it, the
    node answers over the real HTTP route, the chart is drawn where the data
    says, the tree indents, and the box scrolls instead of the sidebar."""
    await instance.call("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 900, "deviceScaleFactor": 1,
        "mobile": False}, session=instance.page_session)
    await asyncio.sleep(.35)
    point = await evaluate(instance, """(() => {
        const chip=document.getElementById('host-cpu');
        chip.classList.remove('hidden');           // the stream may not have sampled yet
        const r=chip.getBoundingClientRect();
        return {x:r.x+r.width/2, y:r.y+r.height/2, expanded:chip.getAttribute('aria-expanded')};
    })()""")
    assert point["expanded"] == "false", point
    for kind in ("mousePressed", "mouseReleased"):
        await instance.call("Input.dispatchMouseEvent", {
            "type": kind, "x": point["x"], "y": point["y"], "button": "left",
            "clickCount": 1}, session=instance.page_session)
    try:
        # The box polls this node over the same authenticated route a backend
        # would answer, so its own process is what comes back. It slides in
        # like the sidebar's other panels, so let that motion settle before
        # measuring anything drawn inside it.
        await until(instance, "hostPanel.open && document.querySelectorAll('.host-proc').length > 0")
        await until(instance, "!document.getElementById('foot-host')"
                              ".classList.contains('disclosure-animating')")
        served = await evaluate(instance, """(() => {
            const data=hostPanel.nodes.get(0).data;
            return {pid:data.processes.root.pid, label:data.processes.root.label,
                    cores:data.cpu.cores, total:data.processes.total,
                    counted:data.processes.counted,
                    expanded:document.getElementById('host-cpu').getAttribute('aria-expanded'),
                    open:document.querySelector('.side-foot').classList.contains('host-open')};
        })()""")
        assert served["pid"] > 0 and served["cores"] >= 1, served
        assert served["total"] >= served["counted"] >= 1, served
        assert served["expanded"] == "true" and served["open"] is True, served
        # The footer's rows keep the spacing the column gap used to give them:
        # 4px of air above the box's rule (the engine scroller's own -4px
        # overhang eats half of the 8px) and 8px down to the icon row.
        spacing = await evaluate(instance, """(() => {
            const box=document.getElementById('foot-host').getBoundingClientRect();
            const engines=document.getElementById('foot-engines').getBoundingClientRect();
            const row=document.querySelector('.foot-row').getBoundingClientRect();
            return {above:box.top-engines.bottom, below:row.top-box.bottom};
        })()""")
        assert abs(spacing["above"] - 4) < 0.6, spacing
        assert abs(spacing["below"] - 8) < 0.6, spacing
        # Every leading dot in the footer stands in one column, the box's as
        # much as the backend and engine rows above it: whatever the dot's
        # size its centre is the same distance from the sidebar's edge, and
        # the name belongs to its dot - the air after the dot is half the air
        # before it.
        column = await evaluate(instance, """(() => {
            const edge=document.getElementById('side').getBoundingClientRect().left;
            const ink=node=>{const range=document.createRange();range.selectNode(node);
                return range.getBoundingClientRect();};
            /* the name is a button when it opens the engine's CLI, plain
               text otherwise; either way its ink is the name's box */
            const label=row=>row.querySelector('.foot-eng-open')||[...row.childNodes].find(n=>n.nodeType===3);
            const measure=(row, dot, text)=>{
                const d=row.querySelector(dot).getBoundingClientRect();
                const t=(typeof text==='string' ? row.querySelector(text)
                    .getBoundingClientRect() : ink(text(row)));
                return {before:d.left-edge, mid:d.left+d.width/2-edge, after:t.left-d.right};
            };
            return {
                backend:measure(document.querySelector('.foot-engine-head'),
                                '.gdot', '.foot-engine-name'),
                engine:measure(document.querySelector('.foot-eng'),
                               '.engine-dot', label),
                node:measure(document.querySelector('.host-node-head'),
                             '.gdot', '.host-node-name'),
                process:measure(document.querySelector('.host-proc'),
                                '.host-proc-dot', '.host-proc-name'),
            };
        })()""")
        for row in column.values():
            assert abs(row["before"] / 2 - row["after"]) < 0.6, column
            assert abs(row["mid"] - column["backend"]["mid"]) < 0.6, column
        # A known series, drawn: the line spans the well, the peak reaches its
        # top and the trough its floor, and the stroke keeps its width despite
        # the box being stretched to the sidebar's width.
        geometry = await evaluate(instance, """(() => {
            const now=Date.now()/1000;
            const data=hostPanel.nodes.get(0).data;
            const step=data.cpu.interval;   // a minute of samples, floor to peak
            data.cpu.history=[];
            for (let i=0;i<=20;i++)
                data.cpu.history.push([now-(20-i)*step, i===10?100:(i%2?50:0)]);
            hostPanel.live.length=0;
            renderHostPanel();
            const svg=document.querySelector('.host-chart');
            const slot=svg.parentElement.clientWidth;
            const box=svg.getBoundingClientRect();
            const line=svg.querySelector('.host-chart-line').getBoundingClientRect();
            const stroke=parseFloat(getComputedStyle(svg.querySelector('.host-chart-line')).strokeWidth);
            return {wide:box.width>200, fills:Math.abs(box.width-slot)<1.5,
                    segments:svg.querySelectorAll('.host-chart-line').length,
                    top:line.top-box.top, bottom:box.bottom-line.bottom,
                    left:line.left-box.left, right:box.right-line.right,
                    stroke, note:document.querySelector('.host-node-note').textContent};
        })()""")
        assert geometry["wide"] and geometry["fills"], geometry
        assert geometry["segments"] == 1, geometry
        for edge in ("top", "bottom", "left", "right"):
            assert abs(geometry[edge]) <= 1.5, (edge, geometry)
        assert 1 <= geometry["stroke"] <= 1.5, geometry
        assert geometry["note"] == "0% · peak 100%", geometry
        # Real layout: every child row is indented past its parent, its rails
        # are drawn as a tree - a corner for a last child, a tee where a
        # trimmed "+n more" still follows - and one level's cells share a
        # column and the full row height, so their bars join up.
        layout = await evaluate(instance, """(() => {
            const node=(label,kind,children,extra={}) => ({label,kind,children,cpu:1,rss:1048576,
                threads:1,pid:1,uptime:1,cmd:label,...extra});
            hostPanel.nodes.get(0).data.processes.root=node('python3 -m puppy','puppy',[
                node('claude','claude',[node('agent bridges','bridge',[],{count:5,group:true})],
                     {more:2})]);
            renderHostPanel();
            const rows=[...document.querySelectorAll('.host-proc')];
            const cells=r=>[...r.querySelectorAll('.host-rail')];
            const lead=r=>Math.round((r.querySelector('.host-proc-dot') ||
                r.querySelector('.host-proc-more-label')).getBoundingClientRect().left);
            /* the bar of a row's own level, which must stand in the column
               its parent's dot stands in rather than beside it */
            const bar=r=>{const c=cells(r).pop().getBoundingClientRect();
                return Math.round(c.left+c.width/2);};
            const shape=r=>cells(r).map(c=>c.className.includes('line')?'|':
                c.className.includes('tee')?'T':c.className.includes('end')?'L':'.').join('');
            const last=r=>cells(r).pop().getBoundingClientRect();
            return {lefts:rows.map(lead), shapes:rows.map(shape),
                    labels:rows.map(r=>r.textContent),
                    bars:rows.slice(1).map(bar),
                    dots:rows.map(r=>{const d=r.querySelector('.host-proc-dot');
                        return d ? Math.round(d.getBoundingClientRect().left+
                            d.getBoundingClientRect().width/2) : null;}),
                    column:Math.abs(last(rows[2]).left-last(rows[3]).left),
                    stretched:rows.slice(1).every(r=>
                        Math.abs(last(r).height-r.getBoundingClientRect().height)<.5),
                    inside:rows.every(r=>r.scrollWidth<=r.clientWidth)};
        })()""")
        assert layout["lefts"][0] < layout["lefts"][1] < layout["lefts"][2], layout
        assert layout["lefts"][3] == layout["lefts"][2], layout
        # The tree hangs from that same column: the first level's bar stands in
        # the root's own dot rather than beside it, and one level's cells keep
        # sharing a column below that.
        assert layout["bars"][0] == layout["dots"][0], layout
        assert layout["bars"][1] == layout["bars"][2], layout
        assert layout["shapes"] == ["", "L", ".T", ".L"], layout
        assert layout["column"] < 0.5 and layout["stretched"], layout
        assert "×5" in layout["labels"][2] and layout["labels"][3] == "+2 more", layout
        assert layout["inside"], layout
        await host_section_checks(instance, capture)
        # It is the box that scrolls, not the session list under it.
        scrolling = await evaluate(instance, """(() => {
            const box=document.getElementById('foot-host');
            const list=document.querySelector('.side-scroll');
            const engines=document.getElementById('foot-engines');
            for (let i=0;i<40;i++)
                hostPanel.nodes.get(0).data.processes.root.children.push({
                    label:'worker-'+i,kind:'proc',children:[],cpu:0,rss:1024,threads:1,
                    pid:100+i,uptime:1,cmd:'worker'});
            renderHostPanel();
            box.scrollTop=9999;
            return {scrolls:box.scrollHeight>box.clientHeight, moved:box.scrollTop>0,
                    inside:box.getBoundingClientRect().bottom<=window.innerHeight,
                    list:list.getBoundingClientRect().height>0,
                    engines:engines.getBoundingClientRect().height>0};
        })()""")
        assert all(scrolling.values()), scrolling
        if capture:
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(%s); true" % json.dumps(theme))
                shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                           session=instance.page_session)
                (BASE / "data" / ("host-panel-" + theme + ".png")).write_bytes(
                    base64.b64decode(shot["data"]))
        # It leaves the way the sidebar's other panels do: the moment it is
        # closed it is still on screen and still holding what it was showing,
        # clipped and aimed at nothing, with its own rule and padding going
        # down with its height.
        closing = await evaluate(instance, """(() => {
            const box=document.getElementById('foot-host');
            closeHostPanel();
            return {animating:box.classList.contains('disclosure-animating'),
                    hidden:box.hidden===true, kept:box.children.length>0,
                    standing:box.getBoundingClientRect().height>0,
                    height:box.style.height, padding:box.style.paddingTop,
                    border:box.style.borderTopWidth, margin:box.style.marginTop,
                    clipped:getComputedStyle(box).overflowY==='hidden'};
        })()""")
        assert closing == {"animating": True, "hidden": False, "kept": True,
                           "standing": True, "height": "0px", "padding": "0px",
                           "border": "0px", "margin": "0px",
                           "clipped": True}, closing
    finally:
        await evaluate(instance, "closeHostPanel(); applyTheme('dark'); true")
    # ...and only once that slide has finished is the box hidden and let go
    await until(instance, "document.getElementById('foot-host').hidden === true")
    closed = await evaluate(instance, """(() => ({
        empty: document.getElementById('foot-host').children.length === 0,
        styled: document.getElementById('foot-host').getAttribute('style') || '',
        expanded: document.getElementById('host-cpu').getAttribute('aria-expanded'),
        open: document.querySelector('.side-foot').classList.contains('host-open'),
        polling: hostPanel.timer !== null,
        engines: getComputedStyle(document.getElementById('foot-engines'))
            .transitionProperty,
    }))()""")
    assert closed == {"empty": True, "styled": "", "expanded": "false",
                      "open": False, "polling": False,
                      "engines": "max-height"}, closed
    # The end of that slide is the closed footer: a box collapsed to nothing
    # takes no room at all, so hiding it cannot make the footer jump by a gap,
    # a padding or a rule the motion could not take with it.
    footer = await evaluate(instance, """(() => {
        const foot=document.querySelector('.side-foot');
        const box=document.getElementById('foot-host');
        const engines=document.getElementById('foot-engines').getBoundingClientRect();
        const row=document.querySelector('.foot-row').getBoundingClientRect();
        const shut=foot.getBoundingClientRect().height;
        box.hidden=false;
        for (const [name,value] of [['height','0px'],['paddingTop','0px'],
                                    ['borderTopWidth','0px'],['marginTop','0px']])
            box.style[name]=value;
        const flat=foot.getBoundingClientRect().height;
        box.hidden=true; box.removeAttribute('style');
        return {shut, flat, back:foot.getBoundingClientRect().height,
                gap:row.top-engines.bottom};
    })()""")
    assert abs(footer["flat"] - footer["shut"]) < 0.5, footer
    assert abs(footer["back"] - footer["shut"]) < 0.5, footer
    assert abs(footer["gap"] - 4) < 0.6, footer
    print("PASS: the CPU reading opens a drawn, scrolling host box - real route, "
          "chart geometry, indented tree - and it slides shut and stops polling", flush=True)

async def notices_panel_checks(instance, capture=False):
    """The tray's box, in a real browser: a real click on the tray between the
    bell and Sign out opens it, an empty history is the head alone centred
    between the rules with its pill's clear disabled, a notice raised in the
    page reaches the controller over the real route and lands at the top of
    the open box, a repeat counts up on the row it already has, the dots stand
    in the footer's dot column, a full history scrolls inside the box rather
    than the sidebar, and a real click on the pill's clear empties it over the
    real route for the stream to bring back. It shares the footer with the
    host box and slides shut like it."""
    await instance.call("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 900, "deviceScaleFactor": 1,
        "mobile": False}, session=instance.page_session)
    await asyncio.sleep(.35)
    point = await evaluate(instance, """(() => {
        const tray=document.getElementById('btn-notices');
        const r=tray.getBoundingClientRect();
        return {x:r.x+r.width/2, y:r.y+r.height/2, expanded:tray.getAttribute('aria-expanded'),
                before:tray.previousElementSibling.id, after:tray.nextElementSibling.id,
                drawn:tray.querySelector('svg')!==null, width:r.width, height:r.height};
    })()""")
    assert point["expanded"] == "false", point
    assert point["before"] == "btn-bell" and point["after"] == "btn-logout", point
    assert point["drawn"] and point["width"] > 0 and point["height"] > 0, point
    await evaluate(instance, "openHostPanel(); true")
    await until(instance, "hostPanel.open")
    for kind in ("mousePressed", "mouseReleased"):
        await instance.call("Input.dispatchMouseEvent", {
            "type": kind, "x": point["x"], "y": point["y"], "button": "left",
            "clickCount": 1}, session=instance.page_session)
    try:
        await until(instance, "noticesPanel.open && !hostPanel.open && !!state.notices")
        await until(instance, "!document.getElementById('foot-notices')"
                              ".classList.contains('disclosure-animating')")
        opened = await evaluate(instance, """(() => ({
            expanded:document.getElementById('btn-notices').getAttribute('aria-expanded'),
            open:document.querySelector('.side-foot').classList.contains('notices-open'),
            hostOpen:document.querySelector('.side-foot').classList.contains('host-open'),
            title:document.querySelector('#foot-notices .host-sec-title').textContent,
            rows:document.querySelectorAll('#foot-notices .notice-row').length,
            empty:document.querySelector('#foot-notices .host-empty')!==null,
        }))()""")
        assert opened["expanded"] == "true" and opened["open"] and not opened["hostOpen"], opened
        assert opened["title"] == "Notifications", opened
        assert opened["rows"] == len(notices.payload()["items"]) and not opened["empty"], opened
        # With nothing recorded the box is its head alone, the pill reading
        # "0" with its clear disabled and no line saying so: the list takes
        # no room, the words stand as far under the box's own rule as they do
        # above the footer row's, the pill is centred on the same line, and
        # nothing hangs below it to give a one-line box a scrollbar.
        blank = await evaluate(instance, """(() => {
            window.keptNotices=state.notices;
            applyNotices({type:'notices', items:[]});
            const box=document.getElementById('foot-notices');
            const rule=box.getBoundingClientRect().top+parseFloat(getComputedStyle(box).borderTopWidth);
            const row=document.querySelector('.foot-row').getBoundingClientRect().top;
            const title=box.querySelector('.host-sec-title').getBoundingClientRect();
            const pill=box.querySelector('.notices-pill').getBoundingClientRect();
            const clear=box.querySelector('.notices-clear');
            const button=clear.getBoundingClientRect();
            const icon=clear.querySelector('svg').getBoundingClientRect();
            return {rows:box.querySelectorAll('.notice-row').length,
                    empty:box.querySelector('.host-empty')!==null,
                    list:getComputedStyle(box.querySelector('.notices-list')).display,
                    count:box.querySelector('.notices-count').textContent,
                    disabled:clear.disabled, label:clear.getAttribute('aria-label'),
                    tooltip:box.querySelector('.notices-head').title+box.querySelector('.notices-pill').title+clear.title,
                    dim:parseFloat(getComputedStyle(clear).opacity),
                    above:title.top-rule, below:row-title.bottom,
                    pillAbove:pill.top-rule, pillBelow:row-pill.bottom,
                    pillRight:box.getBoundingClientRect().right-parseFloat(getComputedStyle(box).paddingRight)-pill.right,
                    inset:[icon.left-button.left, icon.top-button.top],
                    scrolls:box.scrollHeight>box.clientHeight,
                    gutter:box.offsetWidth-box.clientWidth};
        })()""")
        assert blank["rows"] == 0 and not blank["empty"] and blank["list"] == "none", blank
        assert blank["count"] == "0" and blank["tooltip"] == "", blank
        assert blank["disabled"] and blank["label"] == "Clear notifications", blank
        assert blank["dim"] < 0.6, blank
        assert abs(blank["above"] - blank["below"]) < 0.6 and abs(blank["above"] - 9) < 0.6, blank
        assert abs(blank["pillAbove"] - blank["pillBelow"]) < 0.6, blank
        assert abs(blank["pillRight"]) < 0.6, blank
        assert blank["inset"] == [5, 2], blank   # the bin sits on whole pixels in its cell
        assert not blank["scrolls"] and blank["gutter"] == 0, blank
        await evaluate(instance, "applyNotices({type:'notices', items:keptNotices.items}); true")
        await until(instance, "document.querySelectorAll('#foot-notices .notice-row').length===%d"
                    % opened["rows"])
        # A notice raised in the page is reported over the real route and the
        # stream brings it back to the top of the open box; a repeat counts
        # up on that same row, and a different notice goes above it while the
        # earlier row keeps its node and moves down.
        await evaluate(instance, "toast('Harbor dashboard: Session renamed', 'ok'); true")
        await until(instance, "document.querySelector('#foot-notices .notice-row .notice-text')"
                              " && document.querySelector('#foot-notices .notice-row .notice-text')"
                              ".textContent==='Harbor dashboard: Session renamed'")
        await evaluate(instance, "document.querySelector('#foot-notices .notice-row').__first=true;"
                                 " toast('Harbor dashboard: Session renamed', 'ok'); true")
        await until(instance, "document.querySelector('#foot-notices .notice-row .toast-count')"
                              " && document.querySelector('#foot-notices .notice-row .toast-count')"
                              ".textContent==='2 ×'")
        await evaluate(instance, "toast('Garden laptop: Could not reach it · retrying', 'bad', TOAST_LONG); true")
        await until(instance, "document.querySelectorAll('#foot-notices .notice-row').length>=2"
                              " && document.querySelector('#foot-notices .notice-row .notice-text')"
                              ".textContent==='Garden laptop: Could not reach it · retrying'")
        rows = await evaluate(instance, """(() => {
            const rows=[...document.querySelectorAll('#foot-notices .notice-row')];
            const item=state.notices.items[0];
            return {
                top:rows[0].querySelector('.gdot').className, entered:rows[0].classList.contains('notice-new'),
                kept:rows[1].__first===true, count:rows[1].querySelector('.toast-count').textContent,
                second:rows[1].querySelector('.gdot').className,
                stamp:rows[0].querySelector('.notice-time').textContent, expected:fmtStamp(item.at),
                note:document.querySelector('#foot-notices .notices-count').textContent,
                total:state.notices.items.length,
                clearable:!document.querySelector('#foot-notices .notices-clear').disabled,
                tooltips:[document.querySelector('#foot-notices .notices-head'), ...rows]
                    .filter(node=>node.title).length,
            };
        })()""")
        assert rows["top"] == "gdot bad" and rows["entered"], rows
        assert rows["kept"] and rows["count"] == "2 ×" and rows["second"] == "gdot ok", rows
        assert rows["stamp"] and rows["stamp"] == rows["expected"], rows
        assert rows["note"] == str(rows["total"]) and rows["tooltips"] == 0, rows
        assert rows["clearable"], rows
        stored = notices.payload()["items"]
        assert stored[0]["text"] == "Garden laptop: Could not reach it · retrying", stored[:2]
        assert stored[0]["tone"] == "bad" and stored[0]["count"] == 1, stored[:2]
        assert stored[1]["text"] == "Harbor dashboard: Session renamed", stored[:2]
        assert stored[1]["tone"] == "ok" and stored[1]["count"] == 2, stored[:2]
        # The box keeps the host box's spacing under the engine stats, and its
        # dots stand in the footer's one column beside the backend head's.
        spacing = await evaluate(instance, """(() => {
            const box=document.getElementById('foot-notices').getBoundingClientRect();
            const engines=document.getElementById('foot-engines').getBoundingClientRect();
            const row=document.querySelector('.foot-row').getBoundingClientRect();
            const edge=document.getElementById('side').getBoundingClientRect().left;
            const measure=(row, dot, text)=>{
                const d=row.querySelector(dot).getBoundingClientRect();
                const t=row.querySelector(text).getBoundingClientRect();
                return {before:d.left-edge, mid:d.left+d.width/2-edge, after:t.left-d.right,
                        line:Math.abs((d.top+d.height/2)-(t.top+parseFloat(getComputedStyle(row.querySelector(text)).lineHeight)/2))};
            };
            return {above:box.top-engines.bottom, below:row.top-box.bottom,
                    backend:measure(document.querySelector('.foot-engine-head'),'.gdot','.foot-engine-name'),
                    notice:measure(document.querySelector('#foot-notices .notice-row'),'.gdot','.notice-text')};
        })()""")
        assert abs(spacing["above"] - 4) < 0.6 and abs(spacing["below"] - 8) < 0.6, spacing
        assert abs(spacing["notice"]["mid"] - spacing["backend"]["mid"]) < 0.6, spacing
        assert abs(spacing["notice"]["before"] / 2 - spacing["notice"]["after"]) < 0.6, spacing
        assert spacing["notice"]["line"] < 1.5, spacing
        # A full history - a hundred rows, brought by the stream - scrolls
        # inside the box; the session list and the engine stats keep theirs.
        for number in range(notices.LIMIT):
            notices.record("Weekend notes: Draft saved {}".format(number), "info")
        await until(instance, "document.querySelectorAll('#foot-notices .notice-row').length===%d"
                    % notices.LIMIT)
        scrolling = await evaluate(instance, """(() => {
            const box=document.getElementById('foot-notices');
            const list=document.querySelector('.side-scroll');
            const engines=document.getElementById('foot-engines');
            const first=document.querySelector('#foot-notices .notice-text').textContent;
            box.scrollTop=9999;
            return {scrolls:box.scrollHeight>box.clientHeight, moved:box.scrollTop>0,
                    inside:box.getBoundingClientRect().bottom<=window.innerHeight,
                    list:list.getBoundingClientRect().height>0,
                    engines:engines.getBoundingClientRect().height>0,
                    newest:first, note:document.querySelector('#foot-notices .notices-count').textContent,
                    wrapped:[...document.querySelectorAll('#foot-notices .notice-text')]
                        .every(node=>node.getBoundingClientRect().right<=box.getBoundingClientRect().right)};
        })()""")
        assert scrolling["newest"] == "Weekend notes: Draft saved %d" % (notices.LIMIT - 1), scrolling
        assert scrolling["note"] == str(notices.LIMIT), scrolling
        assert all(scrolling[key] for key in ("scrolls", "moved", "inside", "list", "engines", "wrapped")), scrolling
        if capture:
            await evaluate(instance, "document.getElementById('foot-notices').scrollTop=0; true")
            for theme in ("dark", "light"):
                await evaluate(instance, "applyTheme(%s); true" % json.dumps(theme))
                shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                           session=instance.page_session)
                (BASE / "data" / ("notices-panel-" + theme + ".png")).write_bytes(
                    base64.b64decode(shot["data"]))
        # A real click on the pill's clear sends the hundred away over the
        # real route: the stream brings the emptied list to the box, the
        # count reads 0 with the clear disabled again, the scrollbar goes
        # with the rows, and the controller has nothing left. A notice raised
        # afterwards is a fresh row with a later id, so nothing cleared can
        # come back as a repeat.
        clear = await evaluate(instance, """(() => {
            document.getElementById('foot-notices').scrollTop=0;
            const r=document.querySelector('#foot-notices .notices-clear').getBoundingClientRect();
            return {x:r.x+r.width/2, y:r.y+r.height/2, width:r.width, height:r.height};
        })()""")
        assert clear["width"] >= 18 and clear["height"] >= 14, clear
        last_id = notices.payload()["items"][0]["id"]
        for kind in ("mousePressed", "mouseReleased"):
            await instance.call("Input.dispatchMouseEvent", {
                "type": kind, "x": clear["x"], "y": clear["y"], "button": "left",
                "clickCount": 1}, session=instance.page_session)
        await until(instance, "!noticesPanel.clearing && state.notices.items.length===0"
                              " && document.querySelectorAll('#foot-notices .notice-row').length===0")
        cleared = await evaluate(instance, """(() => {
            const box=document.getElementById('foot-notices');
            return {count:box.querySelector('.notices-count').textContent,
                    disabled:box.querySelector('.notices-clear').disabled,
                    empty:box.querySelector('.host-empty')!==null,
                    scrolls:box.scrollHeight>box.clientHeight,
                    failed:[...document.querySelectorAll('#toasts .toast')]
                        .filter(node=>node.textContent.includes('Could not clear')).length};
        })()""")
        assert cleared == {"count": "0", "disabled": True, "empty": False, "scrolls": False,
                           "failed": 0}, cleared
        assert notices.payload()["items"] == []
        await evaluate(instance, "toast('Harbor dashboard: Session renamed', 'ok'); true")
        await until(instance, "state.notices.items.length===1"
                              " && !document.querySelector('#foot-notices .notices-clear').disabled")
        stored = notices.payload()["items"]
        assert [(item["text"], item["count"]) for item in stored] == \
            [("Harbor dashboard: Session renamed", 1)], stored
        assert stored[0]["id"] > last_id, (stored, last_id)
        await evaluate(instance, "document.getElementById('toasts').replaceChildren(); true")
        # Opening the host box takes the notification box's place under the
        # engine stats, and the tray closes its box the way it opened it.
        await evaluate(instance, "openHostPanel(); true")
        await until(instance, "hostPanel.open && !noticesPanel.open")
        await evaluate(instance, "closeHostPanel(); openNoticesPanel(); true")
        await until(instance, "noticesPanel.open && !document.getElementById('foot-notices')"
                              ".classList.contains('disclosure-animating')")
        for kind in ("mousePressed", "mouseReleased"):
            await instance.call("Input.dispatchMouseEvent", {
                "type": kind, "x": point["x"], "y": point["y"], "button": "left",
                "clickCount": 1}, session=instance.page_session)
        await until(instance, "!noticesPanel.open")
    finally:
        await evaluate(instance, "closeNoticesPanel(); closeHostPanel(); applyTheme('dark'); "
                                 "document.getElementById('toasts').replaceChildren(); true")
    await until(instance, "document.getElementById('foot-notices').hidden === true")
    closed = await evaluate(instance, """(() => ({
        empty: document.getElementById('foot-notices').children.length === 0,
        styled: document.getElementById('foot-notices').getAttribute('style') || '',
        expanded: document.getElementById('btn-notices').getAttribute('aria-expanded'),
        open: document.querySelector('.side-foot').classList.contains('notices-open'),
        kept: state.notices.items.length,
    }))()""")
    assert closed == {"empty": True, "styled": "", "expanded": "false", "open": False,
                      "kept": 1}, closed
    print("PASS: the tray between the bell and Sign out opens a notification box - real "
          "route and stream, an empty head centred between the rules with its pill's "
          "clear disabled, counted repeats, dot column, a scrolling hundred cleared by "
          "one real click - that shares the footer with the host box and slides shut",
          flush=True)


async def navigation_checks(instance, url, sid):
    """Real popstate, hash/reload and UI lifecycle; no command is replayed."""
    await instance.call("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False},
        session=instance.page_session)

    async def run(script):
        await evaluate(instance, script + "; true")
        await until(instance, "!navigation.pending && !navigation.scheduled")

    async def travel(delta, condition):
        await evaluate(instance, "history.go(%d); true" % delta)
        await until(instance, condition + " && !navigation.pending && !navigation.scheduled")

    await run("for (const item of [...modalStack].reverse()) item.close(); closeDrawer(); closeHostPanel(); closeNoticesPanel()")
    await run("openSessionTab(0,1,findSessionMeta(0,1)); state.views['s:0:1'].select(1)")
    await run("openSessionTab(0,2,findSessionMeta(0,2))")
    await run("openSettingsTab()")
    await until(instance, "!!state.views.settings.inner.querySelector('input')")
    await run("window.navSettingsField=state.views.settings.inner.querySelector('input'); navSettingsField.value='history-draft'; window.navSettingsScroll=state.views.settings.root.querySelector('.settings-scroll'); navSettingsScroll.scrollTop=300; navigationRemember()")
    await run("openSearchTab()")
    await travel(-1, "state.active==='settings'")
    assert await evaluate(instance, "navSettingsField.isConnected && navSettingsField.value==='history-draft' && navSettingsScroll.scrollTop===300")
    await run("""window.navBackend={id:991,name:'History fixture',capabilities:['timeout-settings'],availability:{state:'online'}};
        state.backends.push(navBackend); state.remoteOk[991]=true; state.remoteTimeouts[991]=state.timeouts;
        window.navSettings=state.views.settings;
        navSettings.inner.querySelector('.timeouts-card').replaceWith(navSettings.timeoutSettingsCard(
          [{bid:0,name:'Studio'},{bid:991,name:'History fixture'}],state.timeouts,navSettings.renderGeneration));
        window.navBackendChoice=navSettings.inner.querySelector('.timeouts-card select');
        navBackendChoice.value='991'; navBackendChoice.dispatchEvent(new Event('change',{bubbles:true}))""")
    await travel(-1, "navBackendChoice.value==='0'")
    await travel(1, "navBackendChoice.value==='991'")
    await travel(-1, "navBackendChoice.value==='0'")
    await run("state.backends=state.backends.filter(node=>node!==navBackend); delete state.remoteOk[991]; delete state.remoteTimeouts[991]")
    await travel(-1, "state.active==='s:0:2'")
    await run("openSearchTab()")
    await run("state.views.search.presetQuery('dashboard')")
    await until(instance, "!state.views.search.searchController")
    # The session with more matches than its first page carries the button;
    # a real press loads the rest over the node's own route: every row where
    # the preview stood, the button gone, and no notice raised.
    await until(instance, "document.querySelector('.search-results .sh-more')?.textContent==='Show all 7 matches'"
                          " && document.querySelectorAll('.search-results .sh-matches .sh-match').length===5")
    await evaluate(instance, "document.getElementById('toasts').replaceChildren();"
                             " document.querySelector('.search-results .sh-more').click(); true")
    await until(instance, "!document.querySelector('.search-results .sh-more')"
                          " && document.querySelectorAll('.search-results .sh-matches .sh-match').length===7")
    assert await evaluate(instance, "!document.querySelector('#toasts .toast') && state.views.search.pageRequests.size===0")
    # Results on display make the filters live. Typing in the box asks
    # nothing; a real press on the Tools chip runs the results' own query
    # again over the node's own route - no press on Search, the draft left
    # in the box and not searched - and the tool rows leave with the kind.
    await run("""window.navSearchApi=api; window.navSearches=[];
        api=(bid,path,opts)=>{ if(path.startsWith('search?')) navSearches.push(new URLSearchParams(path.slice(7)).get('q'));
          return navSearchApi(bid,path,opts); };
        state.views.search.input.focus()""")
    await instance.call("Input.insertText", {"text": " draft"}, session=instance.page_session)
    assert await evaluate(instance, "state.views.search.input.value==='dashboard draft' && navSearches.length===0"
                                    " && !state.views.search.searchController")

    async def press_kind(label, pressed):
        point = await evaluate(instance, """(() => {
            const chip=[...document.querySelectorAll('.search-kinds .search-chip')].find(c=>c.textContent===%s);
            const r=chip.getBoundingClientRect();
            return {x:r.x+r.width/2, y:r.y+r.height/2, pressed:chip.getAttribute('aria-pressed')};
        })()""" % json.dumps(label))
        assert point["pressed"] == pressed, point
        for kind in ("mousePressed", "mouseReleased"):
            await instance.call("Input.dispatchMouseEvent", {
                "type": kind, "x": point["x"], "y": point["y"], "button": "left",
                "clickCount": 1}, session=instance.page_session)
        await until(instance, "!state.views.search.searchController && !navigation.pending && !navigation.scheduled")

    await press_kind("Tools", "true")
    filtered = await evaluate(instance, """(() => ({
        asked: navSearches, draft: state.views.search.input.value,
        kinds: state.views.search.lastCore.kinds, tools: state.views.search.kinds.has('tool'),
        count: document.querySelector('.search-results .sh-count').textContent,
        rows: [...document.querySelectorAll('.search-results .sh-kind')].map(n=>n.textContent).sort(),
        more: !!document.querySelector('.search-results .sh-more'),
        toasts: document.querySelectorAll('#toasts .toast').length,
    }))()""")
    assert filtered == {"asked": ["dashboard"], "draft": "dashboard draft",
                        "kinds": "title,user,assistant,thinking,info", "tools": False,
                        "count": "3 matches", "rows": ["Prompt", "Reply", "Title"],
                        "more": False, "toasts": 0}, filtered
    # Back restores the results the chip replaced - every kind, the seven
    # rows Show all loaded - without asking again; Forward the filtered
    # ones; the chip pressed back on searches once more and leaves the
    # saved kinds as they were found.
    await travel(-1, "state.views.search.kinds.has('tool')")
    assert await evaluate(instance, "navSearches.length===1 && state.views.search.input.value==='dashboard'"
                                    " && document.querySelectorAll('.search-results .sh-match').length===7"
                                    " && [...document.querySelectorAll('.search-kinds .search-chip')]"
                                    ".find(c=>c.textContent==='Tools').getAttribute('aria-pressed')==='true'")
    await travel(1, "!state.views.search.kinds.has('tool')")
    assert await evaluate(instance, "navSearches.length===1"
                                    " && document.querySelectorAll('.search-results .sh-match').length===3")
    await press_kind("Tools", "false")
    assert await evaluate(instance, "navSearches.length===2 && state.views.search.kinds.size===6"
                                    " && document.querySelector('.search-results .sh-more')?.textContent==='Show all 7 matches'"
                                    " && JSON.parse(lsGet('puppy.search')).kinds.length===6")
    await run("api=navSearchApi")
    await run("state.views.search.presetQuery('cards')")
    await until(instance, "!state.views.search.searchController")
    await travel(-1, "state.views.search.input.value==='dashboard'")
    assert await evaluate(instance, "state.views.search.lastCore.q==='dashboard' && !!state.views.search.resultsBox.children.length")
    await travel(1, "state.views.search.input.value==='cards'")
    await run("state.views.search.openMatch(0,findSessionMeta(0,1),4)")
    await until(instance, "state.active==='s:0:1' && state.views['s:0:1'].navigationSeq===4")
    await travel(-1, "state.active==='search' && state.views.search.input.value==='cards'")
    await travel(1, "state.active==='s:0:1' && state.views['s:0:1'].navigationSeq===4")

    # A slow transcript jump cannot overwrite a newer jump or leave the live
    # transcript detached after its result has been abandoned.
    await run("""window.navReadApi=api; window.navWindow=null;
        api=(bid,path,opts)=>path.includes('/events?after_seq=') ?
          new Promise(resolve=>navWindow=resolve) : navReadApi(bid,path,opts);
        window.navChat=state.views['s:0:1'].activeView(); navChat.jumpToSeq(5000)""")
    await until(instance, "!!navWindow")
    await run("navChat.jumpToSeq(4); navWindow({events:[]}); api=navReadApi")
    assert await evaluate(instance, "navChat.navigationSeq===4 && !navChat.detached")

    await run("window.navWorkspace=state.views['s:0:1']; navWorkspace.select(1); window.navTask=navWorkspace.tasks()[0].id; navWorkspace.openTask(navTask)")
    await travel(-1, "navWorkspace.selected===1")
    await travel(1, "navWorkspace.selected===navTask")
    count = await evaluate(instance, "history.length")
    await run("navWorkspace.select(navTask); renderSidebar(); navWorkspace.refreshTasks()")
    assert await evaluate(instance, "history.length") == count
    await run("navWorkspace.openTaskOverview()")
    await travel(-1, "!document.querySelector('.task-overview-modal')")
    await travel(1, "!!document.querySelector('.task-overview-modal')")
    await run("document.querySelector('#to-close').click()")

    await run("modalNewSession()")
    await until(instance, "!!document.querySelector('#ns-name')")
    await run("document.querySelector('#ns-name').value='Unsaved history test'; window.navConfirm=null; modalConfirm('Delete?','History must never submit this',{destructive:true}).then(value=>navConfirm=value)")
    await travel(-1, "navConfirm===false && modalStack.length===1")
    assert await evaluate(instance, "document.querySelector('#ns-name').value==='Unsaved history test'")
    await travel(-1, "modalStack.length===0")
    await travel(1, "!!document.querySelector('#ns-name')")
    assert await evaluate(instance, "!document.querySelector('#mc-yes') && document.activeElement.closest('.modal')!==null")
    await run("document.querySelector('#ns-cancel').click(); openSettingsTab()")
    await travel(-1, "state.active==='s:0:1' && !modalStack.length")
    await run("openHostPanel()")
    await run("window.hostSectionHistory=JSON.stringify(history.state); document.querySelector('#host-section-cpu .disclosure-toggle').click()")
    assert await evaluate(instance, "JSON.stringify(history.state)===hostSectionHistory"), "Host disclosures add no history stop"
    await travel(-1, "!hostPanel.open")
    await travel(1, "hostPanel.open")
    assert await evaluate(instance, "document.querySelector('#host-section-cpu .disclosure-toggle').getAttribute('aria-expanded')==='false'")
    await run("document.querySelector('#host-section-cpu .disclosure-toggle').click(); delete window.hostSectionHistory")
    await run("closeHostPanel()")
    await run("openNoticesPanel()")
    await travel(-1, "!noticesPanel.open")
    await travel(1, "noticesPanel.open")
    assert await evaluate(instance, "document.getElementById('btn-notices').getAttribute('aria-expanded')==='true'")
    await run("closeNoticesPanel()")

    await instance.call("Emulation.setDeviceMetricsOverride", {
        "width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True},
        session=instance.page_session)
    await run("document.querySelector('.burger').click()")
    await travel(-1, "!document.querySelector('#app').classList.contains('side-open')")
    await travel(1, "document.querySelector('#app').classList.contains('side-open')")
    await run("document.querySelector('#btn-settings').click()")
    await travel(-1, "state.active==='s:0:1' && !document.querySelector('#app').classList.contains('side-open')")

    # Closing an identified viewer is a command. Traversal cannot reopen it or
    # issue its DELETE again. A stub view avoids touching any actual backend.
    # A notice reporting itself to the history is not a command, so it is
    # left out of the count.
    await run("""window.navApi=api; window.navWrites=[];
        api=(bid,path,opts={})=>{ if(opts.method && opts.method!=='GET' && path!=='notices') navWrites.push([path,opts.method]); return navApi(bid,path,opts); };
        state.views['v:0:HST1']={root:el('div','view'),onShow(){},destroy(){this.root.remove()}};
        openVncTab(0,'HST1')""")
    await travel(-1, "state.active==='s:0:1'")
    assert await evaluate(instance, "navWrites.length===0 && !!state.tabs.find(t=>t.id==='v:0:HST1')")
    await travel(1, "state.active==='v:0:HST1'")
    await run("closeTab('v:0:HST1')")
    writes = await evaluate(instance, "navWrites.length")
    assert writes == 1
    previous_index = await evaluate(instance, "history.state.index")
    await travel(-1, "history.state.index===%d" % (previous_index - 1))
    assert await evaluate(instance, "navWrites.length===1 && !state.tabs.find(t=>t.id==='v:0:HST1')")
    await run("api=navApi; openSessionTab(0,1,findSessionMeta(0,1)); state.views['s:0:1'].openTask(navTask)")
    await instance.call("Page.reload", session=instance.page_session)
    await until(instance, "typeof navigation!=='undefined' && !!navigation.current && state.active==='s:0:1' && state.views['s:0:1'].selected!==1")
    assert await evaluate(instance, "modalStack.length===0 && !hostPanel.open && !noticesPanel.open && !document.querySelector('#app').classList.contains('side-open')")
    await run("openSettingsTab()")
    await travel(-1, "state.active==='s:0:1'")
    await travel(1, "state.active==='settings'")

    # A reference resolving after the reader moves elsewhere must stay behind.
    await run("""window.navLinkApi=api; window.navCatalog=null;
        api=(bid,path,opts)=>path==='session-links/catalog' ?
          new Promise(resolve=>navCatalog=resolve) : navLinkApi(bid,path,opts);
        window.navLinkDone=openSessionReference('delayed/1',4)""")
    await run("openSearchTab(); navCatalog({sessions:[{ref:'delayed/1',bid:0,id:1}]}); api=navLinkApi")
    await evaluate(instance, "navLinkDone")
    assert await evaluate(instance, "state.active==='search'")
    await run("openSettingsTab()")

    # Existing cross-session citation URLs still bootstrap and enter history.
    ref = db.node_uuid() + "/1"
    await instance.call("Page.navigate", {"url": url + "#session=" + ref + "&seq=4"}, session=instance.page_session)
    await until(instance, "state.active==='s:0:1' && state.views['s:0:1'].navigationSeq===4")
    await travel(-1, "state.active==='settings'")
    await travel(1, "state.active==='s:0:1' && state.views['s:0:1'].navigationSeq===4")
    await run("location.hash=''")
    await until(instance, "state.active===null && !document.querySelector('#empty-hint').classList.contains('hidden')")
    assert await evaluate(instance, "state.tabs.length>0 && document.querySelector('#empty-hint').textContent.includes('Choose a tab')")
    await travel(-1, "state.active==='s:0:1'")
    await instance.call("Page.navigate", {"url": url + "?history-bootstrap=1#session=" + ref + "&seq=4"}, session=instance.page_session)
    await until(instance, "typeof navigation!=='undefined' && !!navigation.current && state.active==='s:0:1' && state.views['s:0:1'].navigationSeq===4")
    assert await evaluate(instance, "JSON.stringify(history.state).indexOf('history-draft')===-1")
    await run("""for (const tab of [...state.tabs]) if(['search','settings'].includes(tab.id) || tab.id==='s:0:2') closeTab(tab.id);
        activateTab('s:0:1'); state.views['s:0:1'].select(1);
        window.demoView=state.views['s:0:1'].activeView(); demoView.returnToTail(false)""")
    await until(instance, "demoView.draftReady && !demoView._returning")
    print("PASS: browser Back/Forward across tabs, search queries/results and live filters, tasks, nested dialogs, Host activity, Notifications and phone drawer; Settings backends/drafts/scroll, reload/citation routes, stale async results, and no replay of viewer close commands", flush=True)


async def main(args):
    instances = []
    server = None
    try:
        with patch.object(webui, "_engines_payload", engines), patch.object(webui, "_node_user", return_value="mira"), \
                patch.object(session_git, "inspect", demo_git), patch.object(session_git, "_log_page", demo_log):
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
            # Sweep the registry before launching the two standalone viewer
            # profiles; they deliberately have no persistent catalog entries.
            browser.manager()
            instances = [browser.Manager("TSTA"), browser.Manager("TSTB")]
            for instance in instances:
                await open_console(instance, url, sid)
            if args.navigation_only:
                await navigation_checks(instances[0], url, sid)
                return
            await session_activity_checks(*instances)
            await quota_checks(instances[0])
            await engine_activity_checks(instances[0])
            await model_alias_checks(instances[0])
            await checks(*instances, runner.hub(sid), args.screenshots)
            await browser_cursor_checks(instances[0])
            await terminal_io_checks(instances[0])
            await engine_terminal_checks(instances[0], sid)
            await navigation_checks(instances[0], url, sid)
            if args.screenshots:
                await screenshots(instances[0])
    finally:
        for instance in instances:
            await instance.stop("test finished")
        await browser.shutdown()
        await terminal.shutdown()
        if server:
            await server.cleanup()
        shutil.rmtree(ROOT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--navigation-only", action="store_true")
    asyncio.run(main(parser.parse_args()))
