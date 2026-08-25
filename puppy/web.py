"""HTTP + websocket API and static frontend."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import random
import secrets
import shutil
import tempfile
import time

from aiohttp import WSMsgType, web

from puppy import (__version__, auth, backends, bind_verify, config, db,
                   listener_handoff, protocol, runner, snapshots, terminal,
                   usage_refresh, workspaces)
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


@web.middleware
async def state_change_guard(request: web.Request, handler):
    """Freeze mutations while a consistent snapshot is built or installed."""
    snapshot_path = request.path.startswith("/api/snapshot/")
    busy = request.app.get("puppy_snapshot_busy")
    websocket = request.headers.get("Upgrade", "").lower() == "websocket"
    mutating = request.method not in ("GET", "HEAD", "OPTIONS")
    if busy and not snapshot_path and request.path.startswith("/api/") and \
            (busy == "restore" or mutating or websocket):
        return web.json_response(
            {"error": "Puppy {} in progress".format(
                "restore" if busy == "restore" else "backup")}, status=503)
    if mutating and not snapshot_path:
        request.app["puppy_mutations"] = request.app.get("puppy_mutations", 0) + 1
        try:
            return await handler(request)
        finally:
            request.app["puppy_mutations"] = max(
                0, request.app.get("puppy_mutations", 1) - 1)
    return await handler(request)


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
    if callable(upgrade):
        upgrade = upgrade()
    if upgrade is not None:
        payload["upgrade"] = upgrade
    build = request.app.get("puppy_build")
    if build is not None:
        payload["build"] = build
    transport = request.app.get("puppy_transport")
    if transport is not None:
        payload["transport"] = dict(transport)
    return web.json_response(payload)


async def _engines_payload(refresh_usage: bool = True):
    if refresh_usage:
        await usage_refresh.maybe_refresh()
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
    # Keep initial app/auth entry fast. The browser immediately follows with
    # an asynchronous engine poll, which performs a due account refresh.
    engines = await _engines_payload(refresh_usage=False)
    return web.json_response({
        "version": __version__,
        "instance_name": config.get("instance_name"),
        "engines": engines,
        "usage_refresh": usage_refresh.payload(),
        "backends": backends.list_backends(),
        "sessions": runner.sessions_payload()["sessions"],
        "default_cwd": config.get("sessions.default_cwd", "/"),
        "session_colors": db.SESSION_COLORS,
    })


async def h_engines(request: web.Request):
    engines = await _engines_payload()
    return web.json_response({
        "engines": engines,
        "usage_refresh": usage_refresh.payload(),
    })


async def h_usage_refresh_get(request: web.Request):
    return web.json_response({"usage_refresh": usage_refresh.payload()})


async def h_usage_refresh_patch(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid usage refresh request"}, status=400)
    if not isinstance(body, dict) or "minutes" not in body:
        return web.json_response({"error": "usage refresh minutes are required"}, status=400)
    try:
        interval = usage_refresh.set_minutes(body["minutes"])
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if interval > 0:
        await usage_refresh.maybe_refresh(force=True)
    return web.json_response({
        "engines": await _engines_payload(refresh_usage=False),
        "usage_refresh": usage_refresh.payload(),
    })


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
    workspace_kind = str(body.get("workspace_kind") or workspaces.KIND_DIRECTORY)
    if workspace_kind not in workspaces.KINDS:
        return web.json_response({"error": "unknown workspace kind"}, status=400)
    cwd = ""
    if workspace_kind == workspaces.KIND_DIRECTORY:
        cwd = os.path.abspath(
            str(body.get("cwd") or "").strip() or config.get("sessions.default_cwd", "/"))
        if not os.path.isdir(cwd):
            if body.get("mkdir"):
                try:
                    os.makedirs(cwd, exist_ok=True)
                except OSError as e:
                    return web.json_response({"error": f"mkdir failed: {e}"}, status=400)
            else:
                return web.json_response(
                    {"error": f"directory does not exist: {cwd}", "mkdir_possible": True},
                    status=400)
    perm = body.get("permission_mode") or driver.default_permission()
    if perm not in [o["value"] for o in driver.permission_options()]:
        perm = driver.default_permission()
    effort = str(body.get("effort") or "").strip()
    if effort not in [o["value"] for o in driver.effort_options()]:
        effort = ""
    color = body.get("color") if body.get("color") in db.SESSION_COLORS else random.choice(db.SESSION_COLORS)
    name = str(body.get("name") or "").strip()[:80]
    model = str(body.get("model") or "").strip()[:60]
    created_workspace = ""
    if workspace_kind == workspaces.KIND_TEMPORARY:
        try:
            cwd = created_workspace = workspaces.create_temporary()
        except workspaces.WorkspaceError as exc:
            return web.json_response({"error": str(exc)}, status=500)
    try:
        sid = db.create_session(name, engine, cwd, model, effort, color, perm,
                                workspace_kind=workspace_kind)
    except Exception:
        if created_workspace:
            workspaces.discard_created(created_workspace)
        raise
    runner.broadcast_sessions()
    log.info("session %s created engine=%s workspace=%s cwd=%s",
             sid, engine, workspace_kind, cwd)
    return web.json_response({"ok": True, "session": runner.session_payload(db.get_session(sid))})


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
    return web.json_response({"session": runner.session_payload(s), "status": h.status,
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
        runner.hub(s["id"]).broadcast(
            {"type": "session_meta", "session": runner.session_payload(db.get_session(s["id"]))})
    return web.json_response(
        {"ok": True, "session": runner.session_payload(db.get_session(s["id"]))})


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
    if runner.hub(s["id"]).status == "running":
        return web.json_response(
            {"error": "turn in progress - stop it before deleting the session"}, status=409)
    try:
        workspace_removed = workspaces.remove_temporary(s)
    except workspaces.WorkspaceError as exc:
        return web.json_response({"error": str(exc)}, status=500)
    runner.drop_hub(s["id"])
    shutil.rmtree(os.path.join(config.DATA_DIR, "uploads", str(s["id"])), ignore_errors=True)
    db.delete_session(s["id"])
    runner.broadcast_sessions()
    log.info("session %s deleted workspace_removed=%s", s["id"], workspace_removed)
    return web.json_response({"ok": True, "workspace_removed": workspace_removed})


async def h_session_workspace_reset(request: web.Request):
    s = _session_or_404(request)
    h = runner.hub(s["id"])
    if h.status == "running":
        return web.json_response(
            {"error": "turn in progress - stop it before resetting the workspace"}, status=409)
    if not workspaces.is_temporary(s):
        return web.json_response({"error": "this session does not use a scratch workspace"},
                                 status=400)
    try:
        updated = workspaces.reset_session(s)
    except workspaces.WorkspaceError as exc:
        return web.json_response({"error": str(exc)}, status=500)
    ev = db.add_event(s["id"], "info", {
        "subtype": "workspace_reset",
        "text": "Scratch workspace reset; its previous temporary files were cleared.",
    })
    h.broadcast({"type": "event", "event": ev})
    h.broadcast({"type": "session_meta", "session": runner.session_payload(updated)})
    runner.broadcast_sessions()
    log.info("session %s scratch workspace reset", s["id"])
    return web.json_response({"ok": True, "session": runner.session_payload(updated)})


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
    h.broadcast({"type": "session_meta",
                 "session": runner.session_payload(db.get_session(s["id"]))})
    runner.broadcast_sessions()
    log.info("session %s switched %s -> %s", s["id"], old, engine)
    return web.json_response(
        {"ok": True, "session": runner.session_payload(db.get_session(s["id"]))})


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
    runtime_web = request.app.get("puppy_runtime_web") or {
        "host": config.get("web.host"), "port": config.get("web.port")}
    configured_web = {"host": config.get("web.host"), "port": config.get("web.port")}
    return web.json_response({
        "instance_name": config.get("instance_name"),
        "terminal_command": config.get("terminal.command"),
        "default_cwd": config.get("sessions.default_cwd"),
        "api_token": config.get("auth.api_token"),
        "web": configured_web,
        "active_web": runtime_web,
        "web_restart_required": configured_web != runtime_web,
        "usage_refresh": usage_refresh.payload(),
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


async def h_bind_prepare(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid bind verification request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid bind verification request"}, status=400)
    origin = str(body.get("origin") or "")
    supplied_origin = request.headers.get("Origin")
    if supplied_origin and supplied_origin.rstrip("/") != origin.rstrip("/"):
        return web.json_response({"error": "browser origin mismatch"}, status=403)
    sockname = request.transport.get_extra_info("sockname") if request.transport else None
    connected_host = sockname[0] if isinstance(sockname, tuple) and sockname else None
    try:
        proposed_port = body["port"] if "port" in body else config.get("web.port", 10888)
        return web.json_response(await bind_verify.prepare(
            request.app, str(request["user"]), body.get("host"), origin,
            proposed_port, connected_host=connected_host))
    except bind_verify.BindVerificationError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)


async def h_bind_commit(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid bind commit request"}, status=400)
    try:
        result = await bind_verify.commit(
            request.app, str(request["user"]),
            body.get("token") if isinstance(body, dict) else "")
        return web.json_response(result)
    except bind_verify.BindVerificationError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)


async def h_bind_activate(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid listener activation request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid listener activation request"}, status=400)
    try:
        browser_state = snapshots.validate_ui_state(body.get("browser_state"))
        result = listener_handoff.activate(
            request.app, str(request["user"]), str(body.get("token") or ""),
            browser_state, restart=request.app.get("puppy_restart_hook"))
        log.info("queued verified WebUI listener restart for %s by %r",
                 result["host"], request["user"])
        return web.json_response(result)
    except (listener_handoff.ListenerHandoffError, snapshots.SnapshotError) as exc:
        return web.json_response(
            {"error": str(exc)},
            status=getattr(exc, "status", 400))
    except Exception:
        log.exception("could not queue verified WebUI listener restart")
        return web.json_response({"error": "could not queue Puppy's graceful restart"},
                                 status=500)


def _handoff_json(payload: dict, status: int, headers: dict) -> web.Response:
    return web.Response(
        status=status, headers=headers,
        body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        content_type="application/json")


async def h_bind_handoff_ready(request: web.Request):
    token = request.match_info["token"]
    record = listener_handoff.lookup(token)
    if record is None:
        return web.json_response(
            {"error": "listener handoff expired or was already used"}, status=404,
            headers={"Cache-Control": "no-store"})
    supplied_origin = request.headers.get("Origin", "").rstrip("/")
    if (supplied_origin and supplied_origin != record["origin"]) or \
            not listener_handoff.target_matches(record, request.host):
        return web.json_response(
            {"error": "listener handoff target mismatch"}, status=403,
            headers={"Cache-Control": "no-store"})
    headers = listener_handoff.cors_headers(record)
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=headers)
    if request.method != "GET":
        return _handoff_json({"error": "method not allowed"}, 405, headers)
    if record.get("status") != "queued":
        return _handoff_json({"error": "listener restart was not queued"}, 409, headers)
    if str(config.get("web.host")) != record.get("host") or \
            int(config.get("web.port", 0)) != int(record.get("port", 0)):
        return _handoff_json({"error": "configured listener changed"}, 409, headers)
    ready = listener_handoff.is_ready(request.app, record)
    return _handoff_json({"ok": True, "ready": ready}, 200 if ready else 202, headers)


def _handoff_bootstrap_html(browser_state: dict) -> str:
    # Escape HTML-significant code points even inside JSON so a draft cannot
    # terminate the inline script. The page is a one-use bootstrap and never
    # renders user text into markup.
    encoded = json.dumps(browser_state, separators=(",", ":"), sort_keys=True)
    encoded = encoded.replace("&", "\\u0026").replace("<", "\\u003c").replace(
        ">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return """<!doctype html><html><head><meta charset=\"utf-8\">
<meta name=\"referrer\" content=\"no-referrer\"><title>Reconnecting to Puppy</title></head>
<body><p>Reconnecting to Puppy&hellip;</p><script>
(function () { var values = %s; try { Object.keys(values).forEach(function (key) {
localStorage.setItem(key, values[key]); }); } catch (_) {} location.replace("/"); })();
</script></body></html>""" % encoded


async def h_bind_handoff_claim(request: web.Request):
    record = listener_handoff.claim(
        request.app, request.match_info["token"], request.host)
    if record is None:
        return web.Response(
            status=404, text="Listener handoff expired or is not ready.",
            content_type="text/plain", headers={"Cache-Control": "no-store"})
    if db.query_one("SELECT id FROM users WHERE username=?", (record["user"],)) is None:
        return web.Response(
            status=403, text="The browser account used for this handoff no longer exists.",
            content_type="text/plain", headers={"Cache-Control": "no-store"})
    session_token = auth.issue_session(record["user"])
    response = web.Response(
        text=_handoff_bootstrap_html(record.get("browser_state") or {}),
        content_type="text/html", headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                                       "style-src 'none'; base-uri 'none'; form-action 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        })
    response.set_cookie(
        auth.COOKIE_NAME, session_token, max_age=auth.SESSION_TTL,
        httponly=True, samesite="Strict", path="/")
    return response


# ---- backup / restore ----

def _snapshot_conflict(app: web.Application):
    if app.get("puppy_snapshot_busy"):
        return "another backup or restore is already in progress"
    mutations = int(app.get("puppy_mutations", 0))
    if mutations:
        return "another state-changing request is still in progress"
    blocked = snapshots.blockers()
    if blocked:
        return "close or stop these first: " + "; ".join(blocked)
    return ""


async def h_snapshot_export(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid backup request"}, status=400)
    try:
        ui_state = snapshots.validate_ui_state(body.get("ui") if isinstance(body, dict) else None)
    except snapshots.SnapshotError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    conflict = _snapshot_conflict(request.app)
    if conflict:
        return web.json_response({"error": conflict}, status=409)
    request.app["puppy_snapshot_busy"] = "export"
    result = None
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, snapshots.create_archive, ui_state)
        blocked = snapshots.blockers()
        if blocked:
            snapshots.discard_export(result)
            result = None
            return web.json_response(
                {"error": "backup could not obtain an idle snapshot: " + "; ".join(blocked)},
                status=409)
        token = snapshots.register_export(result, str(request["user"]))
        loop.call_later(snapshots.EXPORT_TTL, snapshots.expire_export, token)
        return web.json_response({
            "ok": True, "download": "/api/snapshot/download/" + token,
            "filename": result["filename"], "size": result["size"],
            "sessions": result["sessions"],
        })
    except (snapshots.SnapshotError, ValueError) as exc:
        if result:
            snapshots.discard_export(result)
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        if result:
            snapshots.discard_export(result)
        log.exception("snapshot export failed")
        return web.json_response({"error": "backup failed: {}".format(exc)}, status=500)
    finally:
        request.app["puppy_snapshot_busy"] = None


async def h_snapshot_download(request: web.Request):
    item = snapshots.claim_export(request.match_info["token"], str(request["user"]))
    if item is None:
        return web.json_response({"error": "backup download expired or was already used"},
                                 status=404)
    path = Path(item["path"])
    response = web.StreamResponse(status=200, headers={
        "Content-Type": "application/gzip",
        "Content-Disposition": 'attachment; filename="{}"'.format(item["filename"]),
        "Content-Length": str(path.stat().st_size),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    })
    try:
        await response.prepare(request)
        with path.open("rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                await response.write(chunk)
        await response.write_eof()
        return response
    finally:
        snapshots.discard_export(item)


async def h_snapshot_import(request: web.Request):
    conflict = _snapshot_conflict(request.app)
    if conflict:
        return web.json_response({"error": conflict}, status=409)
    try:
        declared = int(request.headers.get("Content-Length", "0") or 0)
    except ValueError:
        declared = 0
    if declared > snapshots.MAX_ARCHIVE_BYTES:
        return web.json_response({"error": "snapshot archive exceeds the 512 MiB limit"},
                                 status=413)

    descriptor, upload_name = tempfile.mkstemp(prefix="upload-", suffix=".tar.gz",
                                               dir=str(snapshots.work_root()))
    os.chmod(upload_name, 0o600)
    size = 0
    staged = None
    owns_busy = False
    try:
        with os.fdopen(descriptor, "wb") as output:
            async for chunk in request.content.iter_chunked(1024 * 1024):
                size += len(chunk)
                if size > snapshots.MAX_ARCHIVE_BYTES:
                    return web.json_response(
                        {"error": "snapshot archive exceeds the 512 MiB limit"}, status=413)
                output.write(chunk)
        if not size:
            return web.json_response({"error": "snapshot archive is empty"}, status=400)

        loop = asyncio.get_running_loop()
        staged = await loop.run_in_executor(None, snapshots.stage_import, upload_name)
        conflict = _snapshot_conflict(request.app)
        if conflict:
            return web.json_response({"error": conflict}, status=409)

        request.app["puppy_snapshot_busy"] = "restore"
        owns_busy = True
        # Recheck states that could have changed immediately before the marker
        # was installed. New mutations are rejected from this point onward.
        blocked = snapshots.blockers()
        if request.app.get("puppy_mutations") or blocked:
            detail = "another state change is in progress" if request.app.get(
                "puppy_mutations") else "; ".join(blocked)
            return web.json_response({"error": detail}, status=409)
        await runner.detach_for_restore()
        await backends.close_proxy_websockets()
        await bind_verify.close_all(request.app)
        blocked = snapshots.blockers()
        if blocked:
            return web.json_response({"error": "; ".join(blocked)}, status=409)
        result = snapshots.commit_import(staged)
        usage_refresh.reset_due(clear_status=True)
        try:
            await backends.close_client()
        except Exception as exc:
            log.warning("restored state but could not close the old backend client: %s", exc)
        return web.json_response(result)
    except snapshots.SnapshotError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        log.exception("snapshot import failed")
        return web.json_response({"error": "restore failed: {}".format(exc)}, status=500)
    finally:
        if owns_busy:
            request.app["puppy_snapshot_busy"] = None
        snapshots.discard_staged(staged)
        try:
            os.unlink(upload_name)
        except FileNotFoundError:
            pass


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
            if request.app.get("puppy_snapshot_busy"):
                await ws.send_json({
                    "type": "toast", "level": "error",
                    "text": "backup or restore in progress",
                })
                continue
            if request.app.get("puppy_upgrade_draining"):
                await ws.send_json({
                    "type": "toast", "level": "error",
                    "text": "backend is restarting for an upgrade",
                })
                continue
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
    r.add_get("/api/engines/usage-refresh", h_usage_refresh_get)
    r.add_patch("/api/engines/usage-refresh", h_usage_refresh_patch)

    r.add_get("/api/sessions", h_sessions_list)
    r.add_post("/api/sessions", h_session_create)
    r.add_post("/api/sessions/reorder", h_sessions_reorder)
    r.add_get("/api/sessions/{sid:\\d+}", h_session_get)
    r.add_patch("/api/sessions/{sid:\\d+}", h_session_patch)
    r.add_delete("/api/sessions/{sid:\\d+}", h_session_delete)
    r.add_post("/api/sessions/{sid:\\d+}/workspace/reset", h_session_workspace_reset)
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
    app = web.Application(middlewares=[auth.middleware, state_change_guard],
                          client_max_size=8 * 1024 * 1024)
    app["puppy_role"] = "full"
    app["puppy_snapshot_busy"] = None
    app["puppy_mutations"] = 0
    app["puppy_runtime_web"] = {
        "host": config.get("web.host", "0.0.0.0"),
        "port": int(config.get("web.port", 10888)),
    }
    app["puppy_runtime_id"] = secrets.token_urlsafe(16)
    app["puppy_bind_verifications"] = {}
    listener_handoff.cleanup()
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
    r.add_post("/api/settings/bind/prepare", h_bind_prepare)
    r.add_route("*", "/api/settings/bind/verify/{token:[A-Za-z0-9_-]+}",
                bind_verify.h_probe)
    r.add_post("/api/settings/bind/commit", h_bind_commit)
    r.add_post("/api/settings/bind/activate", h_bind_activate)
    r.add_route("*", listener_handoff.HANDOFF_PREFIX +
                "{token:[A-Za-z0-9_-]+}/ready", h_bind_handoff_ready)
    r.add_get(listener_handoff.HANDOFF_PREFIX +
              "{token:[A-Za-z0-9_-]+}", h_bind_handoff_claim)
    r.add_post("/api/snapshot/export", h_snapshot_export)
    r.add_get("/api/snapshot/download/{token:[A-Za-z0-9_-]+}", h_snapshot_download)
    r.add_post("/api/snapshot/import", h_snapshot_import)
    register_execution_api(app, include_terminal=True)

    async def on_shutdown(app):
        await bind_verify.close_all(app)
        await runner.shutdown()
        await backends.close_client()

    app.on_shutdown.append(on_shutdown)
    return app
