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
import os
from pathlib import Path
import shlex
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
from puppy import auth, browser, config, db, runner, search, session_tasks, session_aliases, terminal
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
                    }), compact:getComputedStyle(v.askBtn.querySelector('.composer-action-label')).display==='none',
                    wrapped:v.sendBtn.getBoundingClientRect().top>v.composerMetaViewport.getBoundingClientRect().top};
                })()""")
                assert all(b["width"] > 0 and b["inside"] and b["hittable"]
                           for b in result["buttons"]), (theme, ratio, result)
                assert result["compact"] == (ratio < .7), result
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

        # The marks are laid out exactly where the words are: same box, same
        # metrics, same wrapping, same scroll.
        layout = await evaluate(instance, """(() => {
            const c = demoView.composer, ta = c.ta;
            c.set('Teh quick brown fox jumpd over the lazy dog. '.repeat(60) + 'sentance');
            c.spellDraw(true);
            const layer = c.box.querySelector('.spell-layer');
            ta.scrollTop = 0;
            ta.dispatchEvent(new Event('scroll'));
            const marks = [...layer.querySelectorAll('.sp-bad')];
            const box = ta.getBoundingClientRect(), mine = layer.getBoundingClientRect();
            const ts = getComputedStyle(ta), ls = getComputedStyle(layer);
            const same = ['fontFamily','fontSize','lineHeight','letterSpacing','paddingTop',
                          'paddingLeft','paddingRight','paddingBottom']
                .every(prop => ts[prop] === ls[prop]);
            const first = marks[0].getBoundingClientRect();
            const last = marks[marks.length - 1].getBoundingClientRect();
            ta.scrollTop = 40;
            ta.dispatchEvent(new Event('scroll'));
            return {words: marks.map(node => node.textContent), same,
                aligned: Math.abs(box.x - mine.x) < .5 && Math.abs(box.y - mine.y) < .5 &&
                    Math.abs(box.width - mine.width) < .5 && Math.abs(box.height - mine.height) < .5,
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
                scrolled: ta.scrollTop > 0 && layer.scrollTop === ta.scrollTop,
                clipped: ls.overflow === 'hidden'};
        })()""")
        assert layout["words"][:2] == ["Teh", "jumpd"] and "sentance" in layout["words"], layout
        assert layout["same"] and layout["aligned"] and layout["inside"], layout
        assert layout["wraps"] and layout["lines"], layout
        assert layout["decoration"] and layout["hidden"] and layout["under"], layout
        assert layout["scrolled"] and layout["clipped"], layout
        if capture:
            shot = await instance.call("Page.captureScreenshot", {"format": "png"},
                                       session=instance.page_session)
            (BASE / "data" / "spell-marks.png").write_bytes(base64.b64decode(shot["data"]))

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
          "underlines, autocorrect typed and undone, suggestion and tools menus", flush=True)


async def checks(a, b, hub, capture=False):
    await message_reuse_checks(a)
    await spell_check_checks(a, capture)
    await workspace_move_checks(a, capture)
    await session_mention_checks(a)
    await pane_resize_checks(a)
    await narrow_composer_checks(a, capture)
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
            # Sweep the registry before launching the two standalone viewer
            # profiles; they deliberately have no persistent catalog entries.
            browser.manager()
            instances = [browser.Manager("TSTA"), browser.Manager("TSTB")]
            for instance in instances:
                await open_console(instance, url, sid)
            await quota_checks(instances[0])
            await engine_activity_checks(instances[0])
            await checks(*instances, runner.hub(sid), args.screenshots)
            await browser_cursor_checks(instances[0])
            await terminal_io_checks(instances[0])
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
    asyncio.run(main(parser.parse_args()))
