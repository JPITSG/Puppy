#!/usr/bin/env python3
"""Real console profiles, embedded browser input, and anonymous screenshots.

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
from puppy import auth, browser, config, db, runner, search, session_tasks, session_aliases
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


async def identity_pill_checks(instance, capture=False):
    await evaluate(instance, """(() => {
        window.identityPreview=modal('<h2>Browser and terminal IDs</h2><p class="modal-copy">Component comparison · anonymous preview</p>');
        for(const [label,id] of [['Browser','A7K2'],['Terminal','B8L3']]) {
            const meta=el('div','br-meta'+(label==='Terminal'?' term-meta':''));
            const pill=el('div','br-ident');pill.setAttribute('aria-label',label+' identity');
            pill.append(el('span','br-ident-label',label),el('span','br-id',id));
            const button=el('button','icon-btn br-copy-id'+(label==='Terminal'?' term-copy-id':''));
            button.type='button';button.setAttribute('aria-label','Copy '+label+' ID');
            button.appendChild(copyIcon());pill.appendChild(button);meta.appendChild(pill);
            const owner=el('button','br-owner');
            owner.appendChild(el('span','br-owner-text','Harbor accessibility and dashboard navigation review'));
            meta.appendChild(owner);
            identityPreview.m.appendChild(meta);
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
                if capture:
                    shot=await instance.call("Page.captureScreenshot",{"format":"png"},session=instance.page_session)
                    (BASE / "data" / ("identity-pills-fixed-"+name+"-"+theme+".png")).write_bytes(base64.b64decode(shot["data"]))
    finally:
        await evaluate(instance,"identityPreview.close(); delete window.identityPreview; applyTheme('dark'); true")
    print("PASS: browser and terminal Copy ID controls fit inside their pills on desktop and phones in both themes",flush=True)


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


async def pane_size_checks(instance, capture=False):
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

    async def verify(count):
        await until(instance, """(() => {
            const badges=[...document.querySelectorAll('.pane-size-badge')];
            return badges.length===%s && badges.every(b => {
                const p=b.parentElement.getBoundingClientRect(), r=b.getBoundingClientRect();
                const s=getComputedStyle(b);
                return b.textContent===`${Math.round(p.width)} × ${Math.round(p.height)}` &&
                    r.left===p.left && r.top===p.top && r.right<=p.right &&
                    s.borderRadius==='0px' && s.pointerEvents==='none' &&
                    s.backgroundColor.startsWith('rgba(') &&
                    document.elementFromPoint(r.left+2,r.top+2)!==b;
            });
        })()""" % count)

    try:
        for theme in ("dark", "light"):
            await evaluate(instance, "applyTheme(%s); true" % json.dumps(theme))
            for axis, count in (("row", 3), ("column", 2)):
                point = await evaluate(instance, """(() => {
                    window.sizeDivider=document.querySelector('.workspace-split.%s > .splitter');
                    const r=sizeDivider.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};
                })()""" % axis)
                x, y = point["x"], point["y"]
                await mouse("mousePressed", x, y)
                await verify(count)
                before = await evaluate(instance, "[...document.querySelectorAll('.pane-size-badge')].map(b=>b.textContent)")
                x += 45 if axis == "row" else 0
                y += 45 if axis == "column" else 0
                await mouse("mouseMoved", x, y)
                await verify(count)
                after = await evaluate(instance, "[...document.querySelectorAll('.pane-size-badge')].map(b=>b.textContent)")
                assert before != after, (axis, before, after)
                if capture and axis == "row":
                    shot = await instance.call("Page.captureScreenshot", {"format": "png"}, session=instance.page_session)
                    (BASE / "data" / ("pane-size-" + theme + ".png")).write_bytes(base64.b64decode(shot["data"]))
                await mouse("mouseReleased", x, y)
                await verify(0)
                # Cancellation, capture loss and leaving the window all clean up.
                for event in ("pointercancel", "lostpointercapture", "blur"):
                    await mouse("mousePressed", x, y)
                    await verify(count)
                    await evaluate(instance, """%s.dispatchEvent(new Event(%s)); true""" %
                                   ("window" if event == "blur" else "sizeDivider", json.dumps(event)))
                    await verify(0)
                    await mouse("mouseReleased", x, y)
                await evaluate(instance, "sizeDivider.dispatchEvent(new KeyboardEvent('keydown',{key:%s})); true" %
                               json.dumps("ArrowLeft" if axis == "row" else "ArrowUp"))
                await verify(count)
                await until(instance, "!document.querySelector('.pane-size-badge')")
                await evaluate(instance, "sizeDivider.dispatchEvent(new MouseEvent('dblclick')); true")
                await verify(count)
                await until(instance, "!document.querySelector('.pane-size-badge')")
        await evaluate(instance, "sizeDivider.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp'})); closeTab('s:0:2'); true")
        await verify(0)
    finally:
        await evaluate(instance, "closeTab('s:0:2'); closeTab('search'); applyTheme('dark'); delete window.sizeDivider; true")
    print("PASS: pane dimensions track real nested horizontal/vertical resizing in both themes; release, cancellation, capture loss, blur, keyboard, reset and rebuild cleanup", flush=True)


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


async def checks(a, b, hub, capture=False):
    await session_mention_checks(a)
    await pane_size_checks(a, capture)
    await narrow_composer_checks(a, capture)
    await context_menu_checks(a, capture)
    await workspace_footer_checks(a, capture)
    await new_session_choices_checks(a, capture)
    await reply_image_checks(a, capture)
    await side_question_wrap_checks(a, capture)
    await status_color_checks(a, capture)
    await identity_pill_checks(a, capture)
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
    print("PASS: regenerated five console screenshots with invented demo data", flush=True)


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
        await hover(30, 180, "grab")
        # No mouse movement or visual change: CSS cursor alone must refresh.
        await evaluate(page, "document.querySelector('#dynamic').style.cursor='wait'; true")
        await until(console, "cursorView.screen.style.cursor === 'wait'")
        await hover(30, 25, "zoom-in")
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
            await checks(*instances, runner.hub(sid), args.screenshots)
            await browser_cursor_checks(instances[0])
            if args.screenshots:
                await screenshots(instances[0])
    finally:
        for instance in instances:
            await instance.stop("test finished")
        await browser.shutdown()
        if server:
            await server.cleanup()
        shutil.rmtree(ROOT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", action="store_true")
    parser.add_argument("--serve", action="store_true")
    asyncio.run(main(parser.parse_args()))
