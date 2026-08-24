"""HTTP + websocket API and static frontend."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import shutil
import time

from aiohttp import WSMsgType, web

from puppy import __version__, auth, backends, config, db, protocol, runner, terminal
from puppy.drivers import all_drivers, get_driver

log = logging.getLogger("puppy.web")

_index_cache = None

FAVICON_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
               "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
               "<stop offset='0' stop-color='#5aa2f5'/><stop offset='1' stop-color='#2e6fc7'/>"
               "</linearGradient></defs>"
               "<rect x='2' y='2' width='96' height='96' rx='24' fill='url(#g)'/>"
               "<path fill='#fff' d='M44 14 Q50 48 84 54 Q50 60 44 94 Q38 60 4 54 Q38 48 44 14 Z'/>"
               "<path fill='#fff' opacity='.85' d='M76 12 Q79 25 92 28 Q79 31 76 44 Q73 31 60 28 Q73 25 76 12 Z'/>"
               "</svg>")


# ---- pages ----

async def h_index(request: web.Request):
    global _index_cache
    if _index_cache is None:
        path = os.path.join(config.STATIC_DIR, "index.html")
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        _index_cache = html.replace("__V__", __version__)
    return web.Response(text=_index_cache, content_type="text/html")


async def h_favicon(request: web.Request):
    return web.Response(text=FAVICON_SVG, content_type="image/svg+xml")


# ---- core api ----

async def h_ping(request: web.Request):
    payload = {
        "ok": True,
        "name": config.get("instance_name"),
        "version": __version__,
        "protocol": protocol.API_PROTOCOL,
        "role": request.app.get("puppy_role", "full"),
        "capabilities": list(request.app.get(
            "puppy_capabilities", protocol.execution_capabilities())),
    }
    upgrade = request.app.get("puppy_upgrade")
    if upgrade is not None:
        payload["upgrade"] = upgrade
    build = request.app.get("puppy_build")
    if build is not None:
        payload["build"] = build
    return web.json_response(payload)


async def _engines_payload():
    engines = []
    for d in all_drivers():
        st = await d.status()
        engines.append({
            "key": d.key, "label": d.label, **st,
            "permission_options": d.permission_options(),
            "default_permission": d.default_permission(),
            "model_options": d.model_options(),
            "effort_options": d.effort_options(),
            "rate_limit": db.meta_get(f"rate_limit.{d.key}"),
        })
    return engines


async def h_state(request: web.Request):
    engines = await _engines_payload()
    return web.json_response({
        "version": __version__,
        "instance_name": config.get("instance_name"),
        "engines": engines,
        "backends": backends.list_backends(),
        "sessions": runner.sessions_payload()["sessions"],
        "default_cwd": config.get("sessions.default_cwd", "/"),
        "session_colors": db.SESSION_COLORS,
    })


async def h_engines(request: web.Request):
    return web.json_response({"engines": await _engines_payload()})


# ---- sessions ----

def _session_or_404(request):
    sid = int(request.match_info["sid"])
    s = db.get_session(sid)
    if s is None:
        raise web.HTTPNotFound(text=json.dumps({"error": "session not found"}),
                               content_type="application/json")
    return s


async def h_sessions_list(request: web.Request):
    return web.json_response(runner.sessions_payload())


async def h_session_create(request: web.Request):
    body = await request.json()
    engine = body.get("engine") or ""
    try:
        driver = get_driver(engine)
    except KeyError:
        return web.json_response({"error": f"unknown engine '{engine}'"}, status=400)
    cwd = os.path.abspath((body.get("cwd") or "").strip() or config.get("sessions.default_cwd", "/"))
    if not os.path.isdir(cwd):
        if body.get("mkdir"):
            try:
                os.makedirs(cwd, exist_ok=True)
            except OSError as e:
                return web.json_response({"error": f"mkdir failed: {e}"}, status=400)
        else:
            return web.json_response({"error": f"directory does not exist: {cwd}", "mkdir_possible": True},
                                     status=400)
    perm = body.get("permission_mode") or driver.default_permission()
    if perm not in [o["value"] for o in driver.permission_options()]:
        perm = driver.default_permission()
    effort = (body.get("effort") or "").strip()
    if effort not in [o["value"] for o in driver.effort_options()]:
        effort = ""
    color = body.get("color") if body.get("color") in db.SESSION_COLORS else random.choice(db.SESSION_COLORS)
    now = time.time()
    sid = db.execute(
        "INSERT INTO sessions(name,engine,cwd,model,effort,color,permission_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ((body.get("name") or "").strip()[:80], engine, cwd,
         (body.get("model") or "").strip()[:60], effort, color, perm, now, now))
    db.execute("UPDATE sessions SET sort_order=(SELECT COALESCE(MAX(sort_order),0)+1 FROM sessions) WHERE id=?", (sid,))
    runner.broadcast_sessions()
    log.info("session %s created engine=%s cwd=%s", sid, engine, cwd)
    return web.json_response({"ok": True, "session": db.get_session(sid)})


async def h_sessions_reorder(request: web.Request):
    body = await request.json()
    ids = body.get("order")
    if not isinstance(ids, list) or not all(isinstance(x, int) for x in ids):
        return web.json_response({"error": "order must be a list of session ids"}, status=400)
    db.reorder_sessions(ids)
    runner.broadcast_sessions()
    return web.json_response({"ok": True})


async def h_session_get(request: web.Request):
    s = _session_or_404(request)
    h = runner.hub(s["id"])
    return web.json_response({"session": s, "status": h.status,
                              "events": db.get_events(s["id"], limit=200)})


async def h_session_patch(request: web.Request):
    s = _session_or_404(request)
    body = await request.json()
    fields = {}
    if "name" in body:
        fields["name"] = str(body["name"]).strip()[:80]
    if "model" in body:
        fields["model"] = str(body["model"]).strip()[:60]
    if "effort" in body:
        driver = get_driver(s["engine"])
        val = str(body["effort"]).strip()
        if val in [o["value"] for o in driver.effort_options()]:
            fields["effort"] = val
    if "color" in body and body["color"] in db.SESSION_COLORS:
        fields["color"] = body["color"]
    if "archived" in body:
        fields["archived"] = 1 if body["archived"] else 0
    if "permission_mode" in body:
        driver = get_driver(s["engine"])
        val = str(body["permission_mode"])
        if val in [o["value"] for o in driver.permission_options()]:
            fields["permission_mode"] = val
    if fields:
        db.touch_session(s["id"], **fields)
        runner.broadcast_sessions()
        runner.hub(s["id"]).broadcast({"type": "session_meta", "session": db.get_session(s["id"])})
    return web.json_response({"ok": True, "session": db.get_session(s["id"])})


IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


async def h_session_upload(request: web.Request):
    """Store a pasted image; the returned path goes into the message text and
    the engine views the file with its own tools (engine-agnostic)."""
    s = _session_or_404(request)
    ctype = (request.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype not in IMAGE_TYPES:
        return web.json_response({"error": f"unsupported image type '{ctype}'"}, status=415)
    data = await request.read()
    if not data:
        return web.json_response({"error": "empty upload"}, status=400)
    updir = os.path.join(config.DATA_DIR, "uploads", str(s["id"]))
    os.makedirs(updir, exist_ok=True)
    path = os.path.join(updir, f"{int(time.time() * 1000)}.{IMAGE_TYPES[ctype]}")
    with open(path, "wb") as f:
        f.write(data)
    log.info("session %s image pasted: %s (%d bytes)", s["id"], path, len(data))
    return web.json_response({"ok": True, "path": path})


async def h_session_delete(request: web.Request):
    s = _session_or_404(request)
    runner.drop_hub(s["id"])
    shutil.rmtree(os.path.join(config.DATA_DIR, "uploads", str(s["id"])), ignore_errors=True)
    db.delete_session(s["id"])
    runner.broadcast_sessions()
    log.info("session %s deleted", s["id"])
    return web.json_response({"ok": True})


async def h_session_message(request: web.Request):
    s = _session_or_404(request)
    body = await request.json()
    res = runner.hub(s["id"]).send_message(body.get("text") or "")
    status = 400 if "error" in res else 200
    return web.json_response(res, status=status)


async def h_session_interrupt(request: web.Request):
    s = _session_or_404(request)
    await runner.hub(s["id"]).interrupt()
    return web.json_response({"ok": True})


async def h_session_switch(request: web.Request):
    s = _session_or_404(request)
    body = await request.json()
    engine = body.get("engine") or ""
    try:
        driver = get_driver(engine)
    except KeyError:
        return web.json_response({"error": f"unknown engine '{engine}'"}, status=400)
    h = runner.hub(s["id"])
    if h.status == "running":
        return web.json_response({"error": "turn in progress - interrupt first"}, status=409)
    old = s["engine"]
    ev = db.add_event(s["id"], "engine_switch", {"from": old, "to": engine})
    db.touch_session(s["id"], engine=engine, native_session_id="", model="", effort="",
                     last_model="", permission_mode=driver.default_permission())
    h.broadcast({"type": "event", "event": ev})
    h.broadcast({"type": "session_meta", "session": db.get_session(s["id"])})
    runner.broadcast_sessions()
    log.info("session %s switched %s -> %s", s["id"], old, engine)
    return web.json_response({"ok": True, "session": db.get_session(s["id"])})


async def h_session_events(request: web.Request):
    s = _session_or_404(request)
    before = request.query.get("before_seq")
    limit = min(500, int(request.query.get("limit", "200")))
    events = db.get_events(s["id"], before_seq=int(before) if before else None, limit=limit)
    return web.json_response({"events": events})


# ---- fs helpers (cwd picker) ----

async def h_fs(request: web.Request):
    path = os.path.abspath(request.query.get("path") or "/")
    show_hidden = request.query.get("hidden") == "1"
    prefix = ""
    if not os.path.isdir(path):
        # partially typed name: list the parent filtered by the last segment
        parent = os.path.dirname(path)
        if os.path.isdir(parent):
            prefix = os.path.basename(path).lower()
            path = parent
        else:
            return web.json_response({"error": "not a directory", "path": path}, status=400)
    dirs = []
    try:
        with os.scandir(path) as it:
            for e in it:
                if not e.is_dir(follow_symlinks=False):
                    continue
                if prefix:
                    if e.name.lower().startswith(prefix):
                        dirs.append(e.name)
                elif show_hidden or not e.name.startswith("."):
                    dirs.append(e.name)
    except OSError as e:
        return web.json_response({"error": str(e), "path": path}, status=400)
    dirs.sort()
    return web.json_response({"path": path, "parent": os.path.dirname(path) if path != "/" else None,
                              "dirs": dirs[:500], "filter": prefix})


async def h_fs_mkdir(request: web.Request):
    body = await request.json()
    path = os.path.abspath((body.get("path") or "").strip())
    if not path or path == "/":
        return web.json_response({"error": "bad path"}, status=400)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        return web.json_response({"error": str(e)}, status=400)
    return web.json_response({"ok": True, "path": path})


# ---- settings ----

async def h_settings_get(request: web.Request):
    return web.json_response({
        "instance_name": config.get("instance_name"),
        "terminal_command": config.get("terminal.command"),
        "default_cwd": config.get("sessions.default_cwd"),
        "api_token": config.get("auth.api_token"),
        "web": {"host": config.get("web.host"), "port": config.get("web.port")},
        "version": __version__,
    })


async def h_settings_patch(request: web.Request):
    body = await request.json()
    if "instance_name" in body:
        config.set_value("instance_name", str(body["instance_name"]).strip()[:60] or "puppy")
    if "terminal_command" in body:
        config.set_value("terminal.command", str(body["terminal_command"]).strip() or "/bin/bash -l")
    if "default_cwd" in body:
        config.set_value("sessions.default_cwd", str(body["default_cwd"]).strip() or "/")
    return await h_settings_get(request)


# ---- websockets ----

async def ws_session(request: web.Request):
    s = _session_or_404(request)
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 22)
    await ws.prepare(request)
    h = runner.hub(s["id"])
    h.attach(ws)
    try:
        await ws.send_json(h.snapshot())
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
                continue
            try:
                data = json.loads(msg.data)
            except Exception:
                continue
            t = data.get("type")
            if t == "approval_response":
                await h.approval_response(
                    data.get("request_id", ""),
                    "allow" if data.get("behavior") == "allow" else "deny",
                    message=data.get("message", ""),
                    updated_permissions=data.get("updated_permissions"))
            elif t == "message":
                res = h.send_message(data.get("text") or "")
                if "error" in res:
                    await ws.send_json({"type": "toast", "level": "error", "text": res["error"]})
            elif t == "unqueue":
                try:
                    idx = int(data.get("index", -1))
                except (TypeError, ValueError):
                    idx = -1
                res = h.unqueue(idx, data.get("text") or "")
                if "error" in res:
                    await ws.send_json({"type": "toast", "level": "error", "text": res["error"]})
            elif t == "interrupt":
                await h.interrupt(clear_queue=data.get("clear_queue") is True)
    finally:
        h.detach(ws)
    return ws


async def ws_updates(request: web.Request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    runner.updates_attach(ws)
    try:
        await ws.send_json(runner.sessions_payload())
        async for msg in ws:
            if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    finally:
        runner.updates_detach(ws)
    return ws


# ---- app assembly ----

def register_execution_api(app: web.Application, include_terminal: bool = True) -> None:
    """Register the API surface consumed through a local or remote session tab.

    The full console and the deployable headless backend both call this. Keep
    backend-facing route changes here so the two runtimes cannot silently drift.
    """
    r = app.router
    r.add_get("/api/ping", h_ping)
    r.add_get("/api/node", h_ping)
    r.add_get("/api/engines", h_engines)

    r.add_get("/api/sessions", h_sessions_list)
    r.add_post("/api/sessions", h_session_create)
    r.add_post("/api/sessions/reorder", h_sessions_reorder)
    r.add_get("/api/sessions/{sid:\\d+}", h_session_get)
    r.add_patch("/api/sessions/{sid:\\d+}", h_session_patch)
    r.add_delete("/api/sessions/{sid:\\d+}", h_session_delete)
    r.add_post("/api/sessions/{sid:\\d+}/message", h_session_message)
    r.add_post("/api/sessions/{sid:\\d+}/upload", h_session_upload)
    r.add_post("/api/sessions/{sid:\\d+}/interrupt", h_session_interrupt)
    r.add_post("/api/sessions/{sid:\\d+}/switch", h_session_switch)
    r.add_get("/api/sessions/{sid:\\d+}/events", h_session_events)

    r.add_get("/api/fs", h_fs)
    r.add_post("/api/fs/mkdir", h_fs_mkdir)
    r.add_get("/api/ws/session/{sid:\\d+}", ws_session)
    r.add_get("/api/ws/updates", ws_updates)
    if include_terminal:
        r.add_get("/api/ws/term", terminal.ws_terminal)


def build_app() -> web.Application:
    app = web.Application(middlewares=[auth.middleware], client_max_size=8 * 1024 * 1024)
    app["puppy_role"] = "full"
    app["puppy_capabilities"] = protocol.execution_capabilities(include_terminal=True)
    r = app.router
    r.add_get("/", h_index)
    r.add_get("/favicon.ico", h_favicon)
    r.add_static("/static/", config.STATIC_DIR, follow_symlinks=False)

    auth.register(app)
    backends.register(app)

    r.add_get("/api/state", h_state)
    r.add_get("/api/settings", h_settings_get)
    r.add_patch("/api/settings", h_settings_patch)
    register_execution_api(app, include_terminal=True)

    async def on_shutdown(app):
        await runner.shutdown()
        await backends.close_client()

    app.on_shutdown.append(on_shutdown)
    return app
