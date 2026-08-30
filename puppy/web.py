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

from puppy import (__version__, auth, backends, bind_verify, browser,
                   cli_auto_upgrade, cli_releases,
                   cli_upgrade, config, db, host_metrics, listener_handoff, notify,
                   protocol, runner, snapshots, system_prompts, terminal, uploads,
                   usage_refresh, workspace_links, workspace_sync, workspaces)
from puppy.drivers import all_drivers, get_driver
from puppy.drivers import base as driver_base

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
        # stable machine identity: lets a controller notice that an execution
        # node and a workspace node are one box and skip the mirror entirely
        "node_uuid": db.node_uuid(),
        "role": request.app.get("puppy_role", "full"),
        "capabilities": list(request.app.get(
            "puppy_capabilities", protocol.execution_capabilities())),
        "uploads": uploads.settings_payload(),
        "browser": browser.ping_payload(),
    }
    if request.app.get("puppy_role") == "backend":
        payload["shutting_down"] = bool(request.app.get("puppy_shutdown_draining"))
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


async def _engines_payload(refresh_usage: bool = True, refresh_models: bool = True):
    if refresh_usage:
        await usage_refresh.maybe_refresh()
    engines = []
    for d in all_drivers():
        st = await d.status()
        if refresh_models and d.dynamic_model_options and st.get("installed"):
            await d.refresh_model_options()
        engines.append({
            "key": d.key, "label": d.label, **st,
            "availability_only": d.availability_only,
            "permission_options": d.permission_options(),
            "default_permission": d.default_permission(),
            "model_options": d.model_options(),
            "effort_options": d.effort_options(),
            "allow_custom_model": d.allow_custom_model,
            "dynamic_model_options": d.dynamic_model_options,
            "model_catalog_loaded": d.model_catalog_loaded(),
            "model_catalog_error": d.model_catalog_error(),
            "rate_limit": db.meta_get(f"rate_limit.{d.key}"),
        })
    return engines


def _node_user() -> str:
    """Account this node's puppy process runs as - what a shell here lands on."""
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or os.environ.get("LOGNAME") or ""


async def h_state(request: web.Request):
    # Keep initial app/auth entry fast. The browser immediately follows with
    # an asynchronous engine poll, which performs a due account refresh.
    engines = await _engines_payload(refresh_usage=False, refresh_models=False)
    session_state = runner.sessions_payload()
    return web.json_response({
        "version": __version__,
        "instance_name": config.get("instance_name"),
        "user": _node_user(),
        "engines": engines,
        "usage_refresh": usage_refresh.payload(),
        "auto_upgrade": cli_auto_upgrade.payload(),
        "backends": backends.list_backends(),
        "sessions": session_state["sessions"],
        "server_time": session_state["server_time"],
        "workspace_links": workspace_links.public_links(),
        "default_cwd": config.get("sessions.default_cwd", "/"),
        "uploads": uploads.settings_payload(),
        "session_colors": db.SESSION_COLORS,
        "notify": notify.public_state(),
        "browser": browser.ping_payload(),
    })


async def h_engines(request: web.Request):
    engines = await _engines_payload()
    return web.json_response({
        "engines": engines,
        "usage_refresh": usage_refresh.payload(),
        "auto_upgrade": cli_auto_upgrade.payload(),
        # additive: lets the console label this node's shells "user @ node"
        "user": _node_user(),
    })


async def h_usage_refresh_get(request: web.Request):
    return web.json_response({"usage_refresh": usage_refresh.payload()})


async def h_usage_refresh_post(request: web.Request):
    await usage_refresh.maybe_refresh(force=True)
    return web.json_response({
        "engines": await _engines_payload(refresh_usage=False),
        "usage_refresh": usage_refresh.payload(),
        "auto_upgrade": cli_auto_upgrade.payload(),
    })


async def h_engines_refresh(request: web.Request):
    """Force one installed-version re-probe and one latest-release check.

    Both values otherwise refresh on their own timers (installed on the status
    cache, latest on the periodic release worker). This is the manual override
    behind the Settings refresh control; it never starts a turn."""
    drivers = all_drivers()
    driver_base.invalidate_status()
    await cli_releases.refresh_if_due(drivers, force=True)
    await asyncio.gather(*(driver.refresh_model_options(force=True)
                           for driver in drivers if driver.dynamic_model_options))
    return web.json_response({
        "engines": await _engines_payload(refresh_usage=False),
        "usage_refresh": usage_refresh.payload(),
        "auto_upgrade": cli_auto_upgrade.payload(),
    })


async def h_engine_auto_upgrade_get(_request: web.Request):
    return web.json_response({"auto_upgrade": cli_auto_upgrade.payload()})


async def h_engine_auto_upgrade_patch(request: web.Request):
    """Set this node's unattended engine-update schedule."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid request"}, status=400)
    try:
        cli_auto_upgrade.set_settings(body)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"ok": True, "auto_upgrade": cli_auto_upgrade.payload()})


async def h_engine_upgrade(request: web.Request):
    """Start this node's own vendor updater for one engine CLI.

    The command is fixed by the driver; the request only names an engine that
    must already be registered here. Engines are spawned per turn, so no
    restart is involved - but the package is rewritten in place, so sessions
    using that engine must be idle first."""
    key = str(request.match_info.get("key") or "")
    try:
        driver = get_driver(key)
    except KeyError:
        return web.json_response({"error": "unknown engine"}, status=404)
    if not cli_upgrade.supported(driver):
        return web.json_response(
            {"error": "{} cannot be upgraded from here".format(driver.label)}, status=400)
    blockers = runner.engine_blockers(driver.key)
    if blockers:
        return web.json_response({
            "error": "{} is busy on this backend - finish or stop its sessions first".format(
                driver.label),
            "blockers": blockers,
        }, status=409)
    try:
        await cli_upgrade.start(driver)
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    return web.json_response({
        "ok": True,
        "engines": await _engines_payload(refresh_usage=False),
        "usage_refresh": usage_refresh.payload(),
        "auto_upgrade": cli_auto_upgrade.payload(),
    })


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
        "auto_upgrade": cli_auto_upgrade.payload(),
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
    await driver.refresh_model_options()
    workspace_kind = str(body.get("workspace_kind") or workspaces.KIND_DIRECTORY)
    if workspace_kind not in workspaces.KINDS:
        return web.json_response({"error": "unknown workspace kind"}, status=400)
    cwd = ""
    workspace_json = ""
    mirror_uid = ""
    link = body.get("workspace")
    if link is not None:
        # controller-brokered remote workspace: the engine works in a private
        # stable mirror here while the project lives on the descriptor's node
        if not isinstance(link, dict) or workspace_kind != workspaces.KIND_DIRECTORY:
            return web.json_response(
                {"error": "invalid linked workspace descriptor"}, status=400)
        root = str(link.get("root") or "").strip()
        if not root.startswith("/") or len(root) > 4096:
            return web.json_response(
                {"error": "linked workspace root must be an absolute path"},
                status=400)
        node = str(link.get("node") or "").strip()[:80]
        label = str(link.get("label") or "").strip()[:512] or \
            ("{}:{}".format(node, root) if node else root)
        uid = link.get("uid") if isinstance(link.get("uid"), str) else ""
        uid = uid or workspace_sync.new_mirror_uid()
        try:
            cwd = workspace_sync.allocate_mirror(
                uid, os.path.basename(root.rstrip("/")) or "project")
        except (workspace_sync.SyncError, OSError) as exc:
            return web.json_response(
                {"error": "cannot allocate the workspace mirror: {}".format(exc)},
                status=400)
        mirror_uid = uid
        workspace_json = json.dumps(
            {"uid": uid, "root": root, "node": node, "label": label},
            separators=(",", ":"))
    elif workspace_kind == workspaces.KIND_DIRECTORY:
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
    model = str(body.get("model") or "").strip()[:config.MAX_MODEL_ID_CHARS]
    if not model:
        model = driver.default_model()
    allowed_models = [str(option.get("value") or "") for option in driver.model_options()]
    if not driver.allow_custom_model and model not in allowed_models:
        return web.json_response({
            "error": "That {} model is not available on this backend".format(driver.label)},
            status=400)
    effort = str(body.get("effort") or "").strip()
    if effort not in [o["value"] for o in driver.effort_options_for_model(model)]:
        effort = ""
    color = body.get("color") if body.get("color") in db.SESSION_COLORS else random.choice(db.SESSION_COLORS)
    name = str(body.get("name") or "").strip()[:80]
    created_workspace = ""
    if workspace_kind == workspaces.KIND_TEMPORARY:
        try:
            cwd = created_workspace = workspaces.create_temporary()
        except workspaces.WorkspaceError as exc:
            return web.json_response({"error": str(exc)}, status=500)
    try:
        sid = db.create_session(name, engine, cwd, model, effort, color, perm,
                                workspace_kind=workspace_kind,
                                workspace=workspace_json)
    except Exception:
        if created_workspace:
            workspaces.discard_created(created_workspace)
        if mirror_uid:
            try:
                workspace_sync.remove_mirror(mirror_uid)
            except workspace_sync.SyncError as exc:
                log.warning("mirror rollback failed: %s", exc)
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
                              "active_since": h.active_since if h.status == "running" else None,
                              "server_time": time.time(),
                              "uploads": uploads.settings_payload(),
                              "events": db.get_events(s["id"], limit=200)})


async def h_session_patch(request: web.Request):
    s = _session_or_404(request)
    body = await request.json()
    fields = {}
    turn_config = {}
    if "name" in body:
        fields["name"] = str(body["name"]).strip()[:80]
    if "model" in body:
        driver = get_driver(s["engine"])
        await driver.refresh_model_options()
        model = str(body["model"] or "").strip()[:config.MAX_MODEL_ID_CHARS]
        if not model:
            model = driver.default_model()
        allowed = [str(option.get("value") or "") for option in driver.model_options()]
        if not driver.allow_custom_model and model not in allowed:
            return web.json_response(
                {"error": "That {} model is not available on this backend".format(driver.label)},
                status=400)
        turn_config["model"] = model
    if "effort" in body:
        driver = get_driver(s["engine"])
        val = str(body["effort"]).strip()
        model = turn_config.get("model", s.get("model") or driver.default_model())
        if val in [o["value"] for o in driver.effort_options_for_model(model)]:
            turn_config["effort"] = val
    if "model" in turn_config and "effort" not in turn_config:
        driver = get_driver(s["engine"])
        current_effort = str(s.get("effort") or "")
        if current_effort not in [o["value"] for o in
                                  driver.effort_options_for_model(turn_config["model"])]:
            turn_config["effort"] = ""
    if "color" in body and body["color"] in db.SESSION_COLORS:
        fields["color"] = body["color"]
    if "archived" in body:
        fields["archived"] = 1 if body["archived"] else 0
    if "show_meta" in body:
        fields["show_meta"] = 1 if body["show_meta"] else 0
    if "permission_mode" in body:
        driver = get_driver(s["engine"])
        val = str(body["permission_mode"])
        if val in [o["value"] for o in driver.permission_options()]:
            fields["permission_mode"] = val
    # While a turn runs or prompts wait, a model/effort change joins the queue
    # and applies in order - prompts sent before it keep the configuration they
    # were written under. With nothing pending it applies like any other field.
    # True means the hub took charge of it (queued, or already in force there).
    queued_config = bool(turn_config) and runner.hub(s["id"]).queue_config(turn_config)
    if not queued_config:
        fields.update(turn_config)
    if fields:
        db.touch_session(s["id"], **fields)
        runner.broadcast_sessions()
        runner.hub(s["id"]).broadcast(
            {"type": "session_meta", "session": runner.session_payload(db.get_session(s["id"]))})
    return web.json_response(
        {"ok": True, "queued_config": queued_config,
         "session": runner.session_payload(db.get_session(s["id"]))})


async def h_session_delete(request: web.Request):
    s = _session_or_404(request)
    if runner.hub(s["id"]).status == "running":
        return web.json_response(
            {"error": "turn in progress - stop it before deleting the session"}, status=409)
    try:
        workspace_removed = workspaces.remove_temporary(s)
    except workspaces.WorkspaceError as exc:
        return web.json_response({"error": str(exc)}, status=500)
    descriptor = workspace_sync.session_workspace(s)
    if descriptor is not None:
        try:
            workspace_sync.remove_mirror(descriptor.get("uid"))
        except workspace_sync.SyncError as exc:
            log.warning("session %s mirror cleanup skipped: %s", s["id"], exc)
    runner.drop_hub(s["id"])
    shutil.rmtree(os.path.join(config.DATA_DIR, "uploads", str(s["id"])), ignore_errors=True)
    try:
        await browser.manager().clear_session_binding(s["id"])
    except Exception as exc:
        log.warning("session %s browser binding cleanup failed: %s", s["id"], exc)
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
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid message request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "message request must be an object"}, status=400)
    text = body.get("text", "")
    if not isinstance(text, str):
        return web.json_response({"error": "message text must be text"}, status=400)
    res = runner.hub(s["id"]).send_message(text)
    status = 400 if "error" in res else 200
    return web.json_response(res, status=status)


async def h_session_interrupt(request: web.Request):
    s = _session_or_404(request)
    await runner.hub(s["id"]).interrupt()
    return web.json_response({"ok": True})


async def h_session_switch(request: web.Request):
    s = _session_or_404(request)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid engine switch request"}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get("engine"), str):
        return web.json_response({"error": "engine must be text"}, status=400)
    engine = body.get("engine") or ""
    try:
        driver = get_driver(engine)
    except KeyError:
        return web.json_response({"error": f"unknown engine '{engine}'"}, status=400)
    await driver.refresh_model_options()
    model = driver.default_model()
    h = runner.hub(s["id"])
    if h.status == "running":
        return web.json_response({"error": "turn in progress - interrupt first"}, status=409)
    old = s["engine"]
    # Snapshot what the outgoing engine actually ran, so the transcript divider can
    # name both configurations - a model picked but never sent anything never ran,
    # and must not be recorded as if it had. A session with no completed turn has
    # no such record and falls back to its selection. The incoming
    # engine is reset to its defaults just below and its model is chosen later, so
    # the WebUI resolves that side from what runs next rather than from here.
    used = runner.parse_used_config(s["used_config"]) or \
        {"model": s["model"] or s["last_model"], "effort": s["effort"]}
    ev = db.add_event(s["id"], "engine_switch", {
        "from": old, "to": engine,
        "from_model": used["model"], "from_effort": used["effort"],
    })
    db.touch_session(s["id"], engine=engine, native_session_id="", model=model, effort="",
                     last_model="", used_config="", permission_mode=driver.default_permission())
    discarded_config_changes = h.discard_pending_config()
    h.broadcast({"type": "event", "event": ev})
    h.broadcast({"type": "session_meta",
                 "session": runner.session_payload(db.get_session(s["id"]))})
    runner.broadcast_sessions()
    log.info("session %s switched %s -> %s", s["id"], old, engine)
    return web.json_response(
        {"ok": True, "discarded_config_changes": discarded_config_changes,
         "session": runner.session_payload(db.get_session(s["id"]))})


async def h_session_events(request: web.Request):
    s = _session_or_404(request)
    before = request.query.get("before_seq")
    try:
        limit = int(request.query.get("limit", "200"))
        before_seq = int(before) if before is not None else None
    except (TypeError, ValueError):
        return web.json_response({"error": "event cursor and limit must be integers"},
                                 status=400)
    if not 1 <= limit <= 500:
        return web.json_response({"error": "event limit must be between 1 and 500"},
                                 status=400)
    if before_seq is not None and before_seq < 1:
        return web.json_response({"error": "event cursor must be positive"}, status=400)
    events = db.get_events(s["id"], before_seq=before_seq, limit=limit)
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
        "uploads": uploads.settings_payload(),
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
        backends.reset_auto_upgrade_schedule()
        try:
            await browser.apply_config()
        except Exception as exc:
            log.warning("restored state but could not reconcile the browser: %s", exc)
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
    try:
        # Draft changes are serialized with this first frame so a just-joined
        # device cannot receive revision N+1 and then be rolled back by an
        # overtaking revision-N snapshot.
        await h.attach_with_snapshot(ws)
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
                continue
            try:
                data = json.loads(msg.data)
            except Exception:
                continue
            if not isinstance(data, dict):
                await ws.send_json({"type": "toast", "level": "error",
                                    "text": "session message must be an object"})
                continue
            t = data.get("type")
            if request.app.get("puppy_snapshot_busy"):
                await ws.send_json({
                    "type": "toast", "level": "error",
                    "text": "backup or restore in progress",
                })
                continue
            if request.app.get("puppy_upgrade_draining") or \
                    request.app.get("puppy_shutdown_draining"):
                message = ("backend is restarting for an upgrade" if
                           request.app.get("puppy_upgrade_draining") else
                           "backend is shutting down")
                await ws.send_json({
                    "type": "toast", "level": "error",
                    "text": message,
                })
                continue
            if t == "approval_response":
                await h.approval_response(
                    data.get("request_id", ""),
                    "allow" if data.get("behavior") == "allow" else "deny",
                    message=data.get("message", ""),
                    updated_permissions=data.get("updated_permissions"))
            elif t == "message":
                text = data.get("text", "")
                supplied_draft = data.get("draft")
                if "draft" in data and not isinstance(supplied_draft, str):
                    await ws.send_json({
                        "type": "toast", "level": "error",
                        "text": "draft text must be text",
                    })
                    continue
                if isinstance(supplied_draft, str) and \
                        len(supplied_draft) > db.MAX_DRAFT_CHARS:
                    await ws.send_json({
                        "type": "toast", "level": "error",
                        "text": "draft cannot exceed {} characters".format(
                            db.MAX_DRAFT_CHARS),
                    })
                    continue
                res = h.send_message(text) if isinstance(text, str) else \
                    {"error": "message text must be text"}
                if "error" in res:
                    await ws.send_json({"type": "toast", "level": "error", "text": res["error"]})
                elif isinstance(supplied_draft, str):
                    draft_result = await h.consume_draft(
                        supplied_draft, data.get("draft_client_id", ""),
                        data.get("draft_client_seq", 0), recipient=ws)
                    if "error" in draft_result:
                        await ws.send_json({"type": "toast", "level": "error",
                                            "text": draft_result["error"]})
            elif t == "draft":
                draft_result = await h.update_draft(
                    data.get("text"), data.get("client_id", ""),
                    data.get("client_seq", 0))
                if "error" in draft_result:
                    await ws.send_json({"type": "toast", "level": "error",
                                        "text": draft_result["error"]})
            elif t == "unqueue":
                try:
                    idx = int(data.get("index", -1))
                except (TypeError, ValueError):
                    idx = -1
                res = h.unqueue(idx, data.get("text") or "")
                if "error" in res:
                    await ws.send_json({"type": "toast", "level": "error", "text": res["error"]})
            elif t == "set_queue_paused":
                try:
                    idx = int(data.get("index", -1))
                except (TypeError, ValueError):
                    idx = -1
                paused = data.get("paused")
                if not isinstance(paused, bool):
                    res = {"error": "pause state must be true or false"}
                else:
                    res = h.set_queue_paused(idx, data.get("text") or "", paused)
                if "error" in res:
                    await ws.send_json({"type": "toast", "level": "error", "text": res["error"]})
            elif t == "begin_queue_reorder":
                request_id = str(data.get("request_id") or "")[:80]
                res = h.begin_queue_reorder(
                    ws, request_id, data.get("queue_revision"))
                await ws.send_json({
                    "type": "queue_reorder_ready", "request_id": request_id,
                    **res,
                })
            elif t in ("finish_queue_reorder", "reorder_queue"):
                request_id = str(data.get("request_id") or "")[:80]
                if t == "reorder_queue":
                    res = h.reorder_queue(
                        ws, request_id, data.get("queue_revision"),
                        data.get("order"))
                else:
                    res = h.finish_queue_reorder(ws, request_id)
                await ws.send_json({
                    "type": "queue_reorder_complete", "request_id": request_id,
                    **res,
                })
            elif t in ("requeue_held", "discard_held"):
                try:
                    idx = int(data.get("index", -1))
                except (TypeError, ValueError):
                    idx = -1
                op = h.requeue_held if t == "requeue_held" else h.discard_held
                res = op(idx, data.get("text") or "")
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
        if request.app.get("puppy_role") == "full":
            metrics = host_metrics.latest(request.app)
            if metrics is not None:
                await ws.send_json(metrics)
        async for msg in ws:
            if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break
    finally:
        runner.updates_detach(ws)
    return ws


# ---- app assembly ----

async def h_notify_exec(request: web.Request):
    """Shared surface: run one completion command on this node. Registered only
    alongside the terminal - a node built without a shell surface stays without
    one. The command arrives fully expanded; info feeds PUPPY_* variables."""
    body = await request.json()
    command = str(body.get("command") or "").strip()[:notify.MAX_COMMAND]
    if not command:
        return web.json_response({"error": "empty command"}, status=400)
    result = await notify.run_local(command, notify.clean_info(body.get("info")))
    return web.json_response(result)


def _notify_broadcast() -> None:
    runner.broadcast_update({"type": "notify", **notify.public_state()})


async def h_notify_get(request: web.Request):
    return web.json_response({"ok": True, "settings": notify.settings(),
                              "placeholders": list(notify.PLACEHOLDERS)})


async def h_notify_set(request: web.Request):
    body = await request.json()
    command = str(body.get("command") or "").strip()[:notify.MAX_COMMAND]
    bid = body.get("backend", 0)
    try:
        bid = int(bid)
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid backend"}, status=400)
    if bid and backends.get_backend(bid) is None:
        return web.json_response({"error": "unknown backend"}, status=400)
    config.set_value("notify.backend", bid)
    config.set_value("notify.command", command)
    # The switch/bell owns enabled independently. An enabled alert with no
    # command remains inert, and saving a command never undoes a user's choice.
    _notify_broadcast()
    log.info("notify command %s (backend %s)", "configured" if command else "cleared", bid)
    return web.json_response({"ok": True, "settings": notify.settings()})


async def h_notify_toggle(request: web.Request):
    body = await request.json()
    config.set_value("notify.enabled", bool(body.get("enabled")))
    _notify_broadcast()
    return web.json_response({"ok": True, "settings": notify.settings()})


async def h_notify_test(request: web.Request):
    """Run once, now, with the values from the panel (unsaved), so the command
    can be proven before trusting it from across the house."""
    body = await request.json()
    override = {"command": str(body.get("command") or "").strip()[:notify.MAX_COMMAND]}
    if "backend" in body:
        try:
            override["backend"] = int(body.get("backend") or 0)
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid backend"}, status=400)
    result = await notify.dispatch({
        "backend": config.get("instance_name") or "local",
        "session": "test session", "engine": "claude", "model": "test-model",
        "status": "ok", "duration": "42", "cwd": config.get("sessions.default_cwd", "/"),
        "id": "0",
    }, override=override)
    return web.json_response(result)


async def h_notify_fire(request: web.Request):
    """Consoles report a remote backend's session going idle. Deduplicated so
    several open browsers ring once; local sessions fire from the runner and
    are rejected here to keep that single-source."""
    body = await request.json()
    try:
        bid = int(body.get("bid"))
        sid = int(body.get("sid"))
    except (TypeError, ValueError):
        return web.json_response({"error": "invalid session reference"}, status=400)
    if bid <= 0:
        return web.json_response({"error": "local sessions fire on the server"}, status=400)
    info = notify.clean_info(body.get("info"))
    # Defense in depth for console-reported remote completions: even if a
    # client regresses and reports a stopped turn, it must never reach the
    # configured command or consume the dedupe window for a later real finish.
    if info.get("status") == "interrupted":
        return web.json_response({"ok": True, "fired": False})
    if not notify.active():
        return web.json_response({"ok": True, "fired": False})
    be = backends.get_backend(bid)
    if be is None or not notify.accept_remote_fire(bid, sid):
        return web.json_response({"ok": True, "fired": False})
    info["backend"] = be["name"]
    info["id"] = str(sid)
    asyncio.ensure_future(notify._fire(info))
    return web.json_response({"ok": True, "fired": True})


def register_execution_api(app: web.Application, include_terminal: bool = True) -> None:
    """Register the API surface consumed through a local or remote session tab.

    The full console and the deployable headless backend both call this. Keep
    backend-facing route changes here so the two runtimes cannot silently drift.
    """
    cli_releases.register(app)
    cli_auto_upgrade.register(app)
    system_prompts.register(app)
    r = app.router
    r.add_get("/api/ping", h_ping)
    r.add_get("/api/node", h_ping)
    r.add_get("/api/engines", h_engines)
    r.add_get("/api/engines/usage-refresh", h_usage_refresh_get)
    r.add_post("/api/engines/usage-refresh", h_usage_refresh_post)
    r.add_patch("/api/engines/usage-refresh", h_usage_refresh_patch)
    r.add_post("/api/engines/refresh", h_engines_refresh)
    r.add_get("/api/engines/auto-upgrade", h_engine_auto_upgrade_get)
    r.add_patch("/api/engines/auto-upgrade", h_engine_auto_upgrade_patch)
    r.add_post("/api/engines/{key:[A-Za-z0-9_-]{1,32}}/upgrade", h_engine_upgrade)

    r.add_get("/api/sessions", h_sessions_list)
    r.add_post("/api/sessions", h_session_create)
    r.add_post("/api/sessions/reorder", h_sessions_reorder)
    r.add_get("/api/sessions/{sid:\\d+}", h_session_get)
    r.add_patch("/api/sessions/{sid:\\d+}", h_session_patch)
    r.add_delete("/api/sessions/{sid:\\d+}", h_session_delete)
    r.add_post("/api/sessions/{sid:\\d+}/workspace/reset", h_session_workspace_reset)
    r.add_post("/api/sessions/{sid:\\d+}/message", h_session_message)
    r.add_post("/api/sessions/{sid:\\d+}/interrupt", h_session_interrupt)
    r.add_post("/api/sessions/{sid:\\d+}/switch", h_session_switch)
    r.add_get("/api/sessions/{sid:\\d+}/events", h_session_events)

    r.add_get("/api/fs", h_fs)
    r.add_post("/api/fs/mkdir", h_fs_mkdir)
    r.add_get("/api/ws/session/{sid:\\d+}", ws_session)
    r.add_get("/api/ws/updates", ws_updates)
    if include_terminal:
        r.add_get("/api/ws/term", terminal.ws_terminal)
        r.add_post("/api/notify/exec", h_notify_exec)
    browser.register(app)
    uploads.register(app)
    workspace_sync.register(app)


def build_app() -> web.Application:
    app = web.Application(middlewares=[auth.middleware, state_change_guard],
                          client_max_size=8 * 1024 * 1024)
    app["puppy_role"] = "full"
    host_metrics.register(app)
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
    workspace_links.register(app)
    app.on_startup.append(backends.start_health_worker)
    app.on_startup.append(backends.start_auto_upgrade_worker)
    app.on_startup.append(workspace_links.start_worker)

    r.add_get("/api/state", h_state)
    r.add_get("/api/notify", h_notify_get)
    r.add_post("/api/notify", h_notify_set)
    r.add_post("/api/notify/toggle", h_notify_toggle)
    r.add_post("/api/notify/test", h_notify_test)
    r.add_post("/api/notify/fire", h_notify_fire)
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
        await backends.stop_auto_upgrade_worker(app)
        await backends.stop_health_worker(app)
        await workspace_links.stop_worker(app)
        await runner.shutdown()
        await backends.close_client()

    app.on_shutdown.append(on_shutdown)
    return app
