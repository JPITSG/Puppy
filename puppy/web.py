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

from puppy import (__version__, agent_notes, auth, backends, bind_verify, browser,
                   cli_auto_upgrade, cli_releases,
                   cli_upgrade, config, db, engine_defaults, host_metrics, listener_handoff, notify,
                   live_websockets, localization, protocol, runner, search, snapshots,
                   spawn_exec,
                   state_stream, system_prompts, terminal, uploads,
                   usage_refresh, workspace_links, workspace_sync, workspaces)
from puppy import web_tls
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

def _node_payload(app: web.Application) -> dict:
    payload = {
        "ok": True,
        "name": config.get("instance_name"),
        "version": __version__,
        "runtime_id": str(app.get("puppy_runtime_id") or ""),
        "protocol": protocol.API_PROTOCOL,
        # stable machine identity: lets a controller notice that an execution
        # node and a workspace node are one box and skip the mirror entirely
        "node_uuid": db.node_uuid(),
        "role": app.get("puppy_role", "full"),
        "capabilities": list(app.get(
            "puppy_capabilities", protocol.execution_capabilities())),
        "uploads": uploads.settings_payload(),
        "browser": browser.ping_payload(),
    }
    if app.get("puppy_role") == "backend":
        payload["shutting_down"] = bool(app.get("puppy_shutdown_draining"))
    upgrade = app.get("puppy_upgrade")
    if callable(upgrade):
        upgrade = upgrade()
    if upgrade is not None:
        payload["upgrade"] = upgrade
    build = app.get("puppy_build")
    if build is not None:
        payload["build"] = build
    transport = app.get("puppy_transport")
    if transport is not None:
        payload["transport"] = dict(transport)
    return payload


async def h_ping(request: web.Request):
    return web.json_response(_node_payload(request.app))


async def _engines_payload(refresh_usage: bool = True, refresh_models: bool = True):
    if refresh_usage:
        await usage_refresh.maybe_refresh()
    drivers = all_drivers()
    statuses = await asyncio.gather(*(driver.status() for driver in drivers))
    if refresh_models:
        due = [driver for driver, status in zip(drivers, statuses)
               if driver.dynamic_model_options and status.get("installed")]
        results = await asyncio.gather(
            *(driver.refresh_model_options() for driver in due),
            return_exceptions=True)
        for driver, result in zip(due, results):
            if isinstance(result, BaseException):
                log.warning("%s model catalog refresh escaped its driver: %s",
                            driver.key, result)
    return [{**status, **_engine_choices(driver)}
            for driver, status in zip(drivers, statuses)]


def _engine_choices(d) -> dict:
    model_options = []
    for raw_option in d.model_options():
        option = dict(raw_option)
        # Service-tier ids are execution details. Publish the semantic
        # availability bit and keep the opaque value inside the driver.
        option.pop("service_tiers", None)
        model_options.append(option)
    if d.supports_fast_mode:
        # The browser needs only availability. The driver keeps ownership
        # of mapping this semantic flag to the catalog's opaque tier id.
        for option in model_options:
            option["fast_mode_available"] = bool(
                d.fast_mode_tier(option.get("value") or ""))
            option["fast_mode_hint"] = d.fast_mode_hint(
                option.get("value") or "")
    return {
        "key": d.key, "label": d.label,
        "availability_only": d.availability_only,
        "permission_options": d.permission_options(),
        "default_permission": d.default_permission(),
        "session_defaults": engine_defaults.values(d),
        "factory_defaults": engine_defaults.factory(d),
        "model_options": model_options,
        "effort_options": d.effort_options(),
        "tool_options": d.tool_options(),
        "supports_fast_mode": bool(d.supports_fast_mode),
        "allow_custom_model": d.allow_custom_model,
        "dynamic_model_options": d.dynamic_model_options,
        "model_catalog_loaded": d.model_catalog_loaded(),
        "model_catalog_error": d.model_catalog_error(),
        "model_catalog_note": d.model_catalog_note(),
        "model_catalog_source": d.model_catalog_source(),
        "model_catalog_checked_at": d.model_catalog_checked_at(),
        "model_catalog_updated_at": d.model_catalog_updated_at(),
        "rate_limit": db.meta_get(f"rate_limit.{d.key}"),
    }


async def _engines_response(refresh_usage: bool = True,
                            refresh_models: bool = True) -> dict:
    return {
        "engines": await _engines_payload(refresh_usage, refresh_models),
        "usage_refresh": usage_refresh.payload(),
        "auto_upgrade": cli_auto_upgrade.payload(),
        "timers": config.timers_payload(),
        # Retained for older consoles that label anonymous shell tabs.
        "user": _node_user(),
    }


def _publish_engines(payload: dict) -> None:
    state_stream.publish({"type": "engines", **payload})


def _state_stream_interval() -> float:
    intervals = [
        config.timer_seconds("cli_status_minutes"),
        config.timer_seconds("model_catalog_minutes"),
        config.timer_seconds("cli_release_minutes"),
    ]
    usage_minutes = usage_refresh.minutes()
    if usage_minutes > 0:
        intervals.append(float(usage_minutes * 60))
    return min(intervals)


async def _state_stream_snapshots(app: web.Application, topics,
                                  probes: bool) -> list:
    if probes and app.get("puppy_snapshot_busy"):
        return []
    wanted = None if topics is None else set(topics)
    includes = lambda name: wanted is None or name in wanted
    payloads = []
    if includes("node"):
        payloads.append({"type": "node", **_node_payload(app)})
    # The lifecycle's pre-listener pass must stay syscall-only.  CLI status,
    # account/model discovery, and the browser binary probe run in the
    # publisher task after startup (or in the established bootstrap request),
    # never delay the socket from becoming reachable.
    if includes("engines") and probes:
        payloads.append({
            "type": "engines",
            **await _engines_response(
                refresh_usage=probes, refresh_models=probes),
        })
    if includes("browser_status") and probes:
        payloads.append({"type": "browser_status",
                         **await browser.status_payload()})
    if includes("terminal_instances") and \
            protocol.TERMINAL_INSTANCES_CAPABILITY in \
            app.get("puppy_capabilities", ()):
        payloads.append({"type": "terminal_instances",
                         "instances": terminal.manager().instance_payloads()})
    return payloads


async def _publish_restored_state(app: web.Application) -> None:
    """Replace every snapshot whose durable source was just restored.

    Restore closes existing viewers, but a new WebSocket may attach without
    first calling ``/api/state``.  Never let that attach inherit snapshots
    cached from the database that was replaced.  Expensive engine/browser
    observations are discarded and rebuilt by the process publisher; cheap
    durable and runtime-owned topics are installed synchronously here.
    """
    runner.clear_published_state("engines")
    runner.clear_published_state("browser_status")
    runner.broadcast_sessions()
    runner.publish_state({"type": "backends", "backends": backends.list_backends()})
    runner.publish_state({"type": "workspace_links",
                          "links": workspace_links.public_links()})
    runner.publish_state({"type": "notify", **notify.public_state()})
    for payload in await _state_stream_snapshots(app, None, probes=False):
        runner.publish_state(payload)
    state_stream.wake()


def _node_user() -> str:
    """Account this node's puppy process runs as - what a shell here lands on."""
    try:
        import getpass
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or os.environ.get("LOGNAME") or ""


async def h_state(request: web.Request):
    # Keep initial app/auth entry fast. The process-owned state publisher does
    # due probes once and pushes the result to every console; app entry never
    # multiplies vendor/account checks by the number of open browsers.
    engines = await _engines_payload(refresh_usage=False, refresh_models=False)
    session_state = runner.sessions_payload()
    payload = {
        "capabilities": list(request.app.get("puppy_capabilities", protocol.execution_capabilities())),
        "version": __version__,
        # Process identity lets an already-open console distinguish a brief
        # socket interruption from a same-listener restart. The latter needs a
        # full asset/state reload (version, uptime, and LC_TIME clock format).
        "runtime_id": str(request.app.get("puppy_runtime_id") or ""),
        "instance_name": config.get("instance_name"),
        "clock_format": localization.clock_format(),
        "user": _node_user(),
        "engines": engines,
        "usage_refresh": usage_refresh.payload(),
        "auto_upgrade": cli_auto_upgrade.payload(),
        "timers": config.timers_payload(),
        "backends": backends.list_backends(),
        "sessions": session_state["sessions"],
        "server_time": session_state["server_time"],
        "workspace_links": workspace_links.public_links(),
        "default_cwd": config.get("sessions.default_cwd", "/"),
        "uploads": uploads.settings_payload(),
        "session_colors": db.SESSION_COLORS,
        "notify": notify.public_state(),
        "browser": browser.ping_payload(),
    }
    _publish_engines({
        "engines": engines,
        "usage_refresh": payload["usage_refresh"],
        "auto_upgrade": payload["auto_upgrade"],
        "timers": payload["timers"],
        "user": payload["user"],
    })
    runner.publish_state({"type": "backends", "backends": payload["backends"]})
    runner.publish_state({"type": "workspace_links",
                          "links": payload["workspace_links"]})
    runner.publish_state({"type": "notify", **payload["notify"]})
    return web.json_response(payload)


async def h_engines(request: web.Request):
    payload = await _engines_response()
    _publish_engines(payload)
    return web.json_response(payload)


async def h_usage_refresh_get(request: web.Request):
    return web.json_response({"usage_refresh": usage_refresh.payload()})


async def h_timers_get(_request: web.Request):
    return web.json_response({"timers": config.timers_payload()})


async def h_timers_patch(request: web.Request):
    """Update this node's cache/refresh timers and wake affected workers."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid timer settings request"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid timer settings request"}, status=400)
    before = config.timer_values()
    try:
        after = config.set_timers(body)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    changed = {name for name in after if after[name] != before[name]}
    if "cli_release_minutes" in changed:
        cli_releases.settings_changed()
    if "model_catalog_minutes" in changed:
        for driver in all_drivers():
            driver.invalidate_model_options()
    if "cli_status_minutes" in changed:
        driver_base.invalidate_status()
    if "completion_sync_seconds" in changed:
        notify.wake_worker()
    state_stream.wake("engines", "node")
    return web.json_response({"ok": True, "timers": config.timers_payload()})


async def h_usage_refresh_post(request: web.Request):
    await usage_refresh.maybe_refresh(force=True)
    payload = await _engines_response(
        refresh_usage=False, refresh_models=False)
    _publish_engines(payload)
    return web.json_response(payload)


async def h_engines_refresh(request: web.Request):
    """Force installed-version, sign-in, release, and model-catalog checks.

    All otherwise refresh on their own timers. This is the manual override
    behind the Settings refresh control; model discovery never starts a turn."""
    drivers = all_drivers()
    driver_base.invalidate_status()
    dynamic = [driver for driver in drivers
               if driver.dynamic_model_options and driver.resolved_binary()]
    results = await asyncio.gather(
        cli_releases.refresh_if_due(drivers, force=True),
        *(driver.status() for driver in drivers),
        *(driver.refresh_model_options(force=True) for driver in dynamic),
        return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            log.warning("manual engine refresh component failed: %s", result)
    payload = await _engines_response(
        refresh_usage=False, refresh_models=False)
    _publish_engines(payload)
    return web.json_response(payload)


async def h_engine_auto_upgrade_get(_request: web.Request):
    return web.json_response({"auto_upgrade": cli_auto_upgrade.payload()})


async def h_engine_defaults(request: web.Request):
    """Read/replace this node's starting choices for one engine."""
    try:
        driver = get_driver(request.match_info["key"])
    except KeyError:
        return web.json_response({"error": "unknown engine"}, status=404)
    if request.method == "PUT":
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid request"}, status=400)
        try:
            config.normalize_engine_defaults(body)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
    await driver.refresh_model_options()
    if request.method == "PUT":
        try:
            engine_defaults.validate(driver, body)
            config.set_engine_defaults(driver.key, body)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        state_stream.wake("engines")
    return web.json_response({"engine": _engine_choices(driver)})


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
    state_stream.wake("engines")
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
    payload = await _engines_response(
        refresh_usage=False, refresh_models=False)
    _publish_engines(payload)
    return web.json_response({"ok": True, **payload})


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
    payload = await _engines_response(
        refresh_usage=False, refresh_models=False)
    _publish_engines(payload)
    return web.json_response(payload)


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
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid request"}, status=400)
    engine = body.get("engine") or ""
    try:
        driver = get_driver(engine)
    except KeyError:
        return web.json_response({"error": f"unknown engine '{engine}'"}, status=400)
    await driver.refresh_model_options()
    try:
        choices = engine_defaults.for_session(driver, body)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
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
    perm, model, effort = choices["permission_mode"], choices["model"], choices["effort"]
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
    if not isinstance(body, dict):
        return web.json_response({"error": "request body must be an object"}, status=400)
    ids = body.get("order")
    expected_order = body.get("expected_order") if "expected_order" in body else None
    expected_pinned = body.get("expected_pinned") if "expected_pinned" in body else None
    try:
        db.reorder_sessions(ids, expected_order=expected_order,
                            expected_pinned=expected_pinned)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except db.SessionOrderConflict as exc:
        return web.json_response({"error": str(exc)}, status=409)
    payload = runner.broadcast_sessions()
    return web.json_response(dict(payload, ok=True))


async def h_session_get(request: web.Request):
    s = _session_or_404(request)
    h = runner.hub(s["id"])
    return web.json_response({"session": runner.session_payload(s), "status": h.status,
                              "active_since": h.active_since if h.status == "running" else None,
                              "steering": h.steering_state(s),
                              "side_question": h.side_question_state(s),
                              "server_time": time.time(),
                              "uploads": uploads.settings_payload(),
                              "events": db.get_events(s["id"], limit=200)})


async def h_session_patch(request: web.Request):
    s = _session_or_404(request)
    body = await request.json()
    if not isinstance(body, dict):
        return web.json_response({"error": "request body must be an object"}, status=400)
    if "tasks_enabled" in body:
        if type(body["tasks_enabled"]) is not bool or set(body) != {"tasks_enabled"}:
            return web.json_response(
                {"error": "tasks_enabled must be a boolean and updated separately"}, status=400)
        from puppy import session_tasks
        try:
            await session_tasks.set_enabled(s["id"], body["tasks_enabled"])
        except session_tasks.TaskError as exc:
            return web.json_response({"error": str(exc)}, status=409)
        payload = runner.session_payload(db.get_session(s["id"]))
        runner.broadcast_sessions()
        runner.hub(s["id"]).broadcast({"type": "session_meta", "session": payload})
        return web.json_response({"ok": True, "session": payload})
    if "tasks_digest" in body:
        if type(body["tasks_digest"]) is not bool or set(body) != {"tasks_digest"}:
            return web.json_response(
                {"error": "tasks_digest must be a boolean and updated separately"}, status=400)
        from puppy import session_tasks
        try:
            await session_tasks.set_digest(s["id"], body["tasks_digest"])
        except session_tasks.TaskError as exc:
            return web.json_response({"error": str(exc)}, status=409)
        payload = runner.session_payload(db.get_session(s["id"]))
        runner.broadcast_sessions()
        runner.hub(s["id"]).broadcast({"type": "session_meta", "session": payload})
        return web.json_response({"ok": True, "session": payload})
    if "fast_mode" in body and type(body["fast_mode"]) is not bool:
        return web.json_response(
            {"error": "fast_mode must be true or false"}, status=400)
    fields = {}
    turn_config = {}
    pending = None
    driver = None
    pin_requested = "pinned" in body
    pin_changed = False
    if pin_requested:
        if type(body["pinned"]) is not bool:
            return web.json_response({"error": "pinned must be true or false"}, status=400)
        if any(key in body for key in (
                "name", "model", "effort", "permission_mode", "fast_mode", "color",
                "archived", "show_meta")):
            return web.json_response(
                {"error": "pinned must be updated separately"}, status=400)
        expected_pin = body.get("expected_pinned") if "expected_pinned" in body else None
        if expected_pin is not None and type(expected_pin) is not bool:
            return web.json_response(
                {"error": "expected_pinned must be true or false"}, status=400)
    if "model" in body or "effort" in body or \
            "permission_mode" in body or "fast_mode" in body:
        # Validate against the engine these fields will actually reach: the
        # session's own engine plus every pending switch already queued. The
        # hub re-checks that engine when the change is queued, so a switch
        # landing during the refresh below fails loudly instead of misapplying.
        pending = runner.hub(s["id"]).pending_config()
        try:
            driver = get_driver(pending["engine"])
        except KeyError:
            return web.json_response(
                {"error": "unknown engine '{}'".format(pending["engine"])},
                status=400)
        if body.get("fast_mode") is True and not driver.supports_fast_mode:
            return web.json_response(
                {"error": "Fast mode is not available for {}".format(
                    driver.label)}, status=400)
        if "model" in body or "effort" in body or body.get("fast_mode") is True:
            await driver.refresh_model_options()
    if "name" in body:
        fields["name"] = str(body["name"]).strip()[:80]
    if "model" in body:
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
        val = str(body["effort"]).strip()
        model = turn_config.get("model", pending["model"] or driver.default_model())
        if val in [o["value"] for o in driver.effort_options_for_model(model)]:
            turn_config["effort"] = val
    if "model" in turn_config and "effort" not in turn_config:
        current_effort = str(pending["effort"] or "")
        if current_effort not in [o["value"] for o in
                                  driver.effort_options_for_model(turn_config["model"])]:
            turn_config["effort"] = ""
    target_model = turn_config.get("model", pending["model"] or driver.default_model()) \
        if pending is not None else ""
    if "fast_mode" in body:
        if body["fast_mode"]:
            if not driver.fast_mode_tier(target_model):
                return web.json_response(
                    {"error": "The selected {} model does not offer Fast mode".format(
                        driver.label)}, status=400)
        turn_config["fast_mode"] = "on" if body["fast_mode"] else "off"
    elif "model" in turn_config and pending.get("fast_mode") == "on" and \
            not driver.fast_mode_tier(target_model):
        # A queued or immediate model move must not leave an impossible Fast
        # request attached to the new model.
        turn_config["fast_mode"] = "off"
    if "permission_mode" in body:
        val = str(body["permission_mode"] or "").strip()
        if val not in [str(option.get("value") or "") for option in
                       driver.permission_options()]:
            return web.json_response(
                {"error": "That permission mode is not available for {}".format(
                    driver.label)}, status=400)
        turn_config["permission_mode"] = val
    if "color" in body and body["color"] in db.SESSION_COLORS:
        fields["color"] = body["color"]
    if "archived" in body:
        fields["archived"] = 1 if body["archived"] else 0
    if "show_meta" in body:
        fields["show_meta"] = 1 if body["show_meta"] else 0
    # While a turn runs or prompts wait, a configuration change joins the queue
    # and applies in order - prompts sent before it keep the configuration they
    # were written under. With nothing pending it applies like any other field.
    # "handled" means the hub took charge (queued, or already in force there).
    queued_config = False
    if turn_config:
        turn_config["engine"] = pending["engine"]
        result = runner.hub(s["id"]).queue_config(turn_config)
        if result.get("error"):
            return web.json_response({"error": result["error"]}, status=409)
        queued_config = bool(result.get("handled"))
        if not queued_config:
            fields.update({k: v for k, v in turn_config.items()
                           if k in ("model", "effort", "permission_mode")})
            if "fast_mode" in turn_config:
                fields["fast_mode"] = 1 if turn_config["fast_mode"] == "on" else 0
    if pin_requested:
        try:
            pin_changed = db.set_session_pinned(
                s["id"], body["pinned"], expected=expected_pin)
        except db.SessionOrderConflict as exc:
            return web.json_response({"error": str(exc)}, status=409)
    if fields:
        db.touch_session(s["id"], **fields)
    list_payload = None
    if fields or pin_changed:
        list_payload = runner.broadcast_sessions()
        runner.hub(s["id"]).broadcast(
            {"type": "session_meta", "session": runner.session_payload(db.get_session(s["id"]))})
    response = {
        "ok": True, "queued_config": queued_config,
        "session": runner.session_payload(db.get_session(s["id"])),
    }
    # Pin callers need the authoritative stable-partitioned order immediately,
    # especially through a remote proxy whose session list otherwise polls.
    if pin_requested:
        if list_payload is None:
            list_payload = runner.sessions_payload()
        response["sessions"] = list_payload["sessions"]
        response["server_time"] = list_payload["server_time"]
    return web.json_response(response)


async def h_session_delete(request: web.Request):
    s = _session_or_404(request)
    from puppy import session_tasks
    blocker = session_tasks.delete_blocker(s)
    if blocker:
        return web.json_response({"error": blocker}, status=409)
    if runner.hub(s["id"]).status == "running":
        return web.json_response(
            {"error": "turn in progress - stop it before deleting the session"}, status=409)
    try:
        workspace_removed = await remove_session(s)
    except workspaces.WorkspaceError as exc:
        return web.json_response({"error": str(exc)}, status=500)
    return web.json_response({"ok": True, "workspace_removed": workspace_removed})


async def remove_session(s) -> bool:
    """Delete a session whose blockers were already checked: its private
    workspace, mirror, queue, uploads, browser/terminal bindings and rows.
    The plain delete route and a task's fold-and-remove share this so the two
    cannot drift; a WorkspaceError leaves the session in place."""
    workspace_removed = workspaces.remove_temporary(s)
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
    try:
        await terminal.manager().clear_session_binding(s["id"])
    except Exception as exc:
        log.warning("session %s terminal binding cleanup failed: %s", s["id"], exc)
    db.delete_session(s["id"])
    runner.broadcast_sessions()
    log.info("session %s deleted workspace_removed=%s", s["id"], workspace_removed)
    return workspace_removed


async def h_session_workspace_reset(request: web.Request):
    s = _session_or_404(request)
    from puppy import session_tasks
    blocker = session_tasks.reset_blocker(s)
    if blocker:
        return web.json_response({"error": blocker}, status=409)
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


async def _session_steer(s: dict, body) -> tuple:
    """Validate and hand off one steer for either HTTP or the session socket."""
    if not isinstance(body, dict):
        return {"error": "steering request must be an object"}, 400
    text = body.get("text", "")
    if not isinstance(text, str):
        return {"error": "steering text must be text"}, 400
    text = text.strip()
    if not text:
        return {"error": "empty steering message"}, 400
    if len(text) > runner.MAX_STEER_CHARS:
        return {
            "error": "steering message cannot exceed {} characters".format(
                runner.MAX_STEER_CHARS)}, 400
    request_id = body.get("request_id", "")
    if request_id is None:
        request_id = ""
    if not isinstance(request_id, str) or \
            (request_id and not runner.valid_steer_request_id(request_id)):
        return {"error": "invalid steering request id"}, 400
    expected_turn_id = body.get("expected_turn_id", "")
    if not isinstance(expected_turn_id, str) or not expected_turn_id or \
            len(expected_turn_id) > runner.MAX_STEER_TURN_ID_CHARS:
        return {"error": "a valid expected turn id is required"}, 400
    res = await runner.hub(s["id"]).steer(
        text, request_id, expected_turn_id=expected_turn_id)
    return res, 409 if "error" in res else 200


async def h_session_steer(request: web.Request):
    s = _session_or_404(request)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid steering request"}, status=400)
    res, status = await _session_steer(s, body)
    return web.json_response(res, status=status)


async def _session_ask(s: dict, body) -> tuple:
    """Validate and hand off one side question over either transport."""
    if not isinstance(body, dict):
        return {"error": "question request must be an object"}, 400
    question = body.get("question", "")
    if not isinstance(question, str):
        return {"error": "the question must be text"}, 400
    question = question.strip()
    if not question:
        return {"error": "empty question"}, 400
    if len(question) > runner.MAX_SIDE_QUESTION_CHARS:
        return {
            "error": "a question cannot exceed {} characters".format(
                runner.MAX_SIDE_QUESTION_CHARS)}, 400
    request_id = body.get("request_id", "")
    if request_id is None:
        request_id = ""
    if not isinstance(request_id, str) or \
            (request_id and not runner.valid_steer_request_id(request_id)):
        return {"error": "invalid question id"}, 400
    expected_turn_id = body.get("expected_turn_id", "")
    if not isinstance(expected_turn_id, str) or not expected_turn_id or \
            len(expected_turn_id) > runner.MAX_STEER_TURN_ID_CHARS:
        return {"error": "a valid expected turn id is required"}, 400
    res = await runner.hub(s["id"]).ask(
        question, request_id, expected_turn_id=expected_turn_id)
    return res, 409 if "error" in res else 200


async def h_session_ask(request: web.Request):
    """Ask the model a question beside its running turn (never in it)."""
    s = _session_or_404(request)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid question request"}, status=400)
    res, status = await _session_ask(s, body)
    return web.json_response(res, status=status)


async def h_session_interrupt(request: web.Request):
    s = _session_or_404(request)
    await runner.hub(s["id"]).interrupt()
    return web.json_response({"ok": True})


async def h_session_switch(request: web.Request):
    """Switch a session's engine, or queue the switch behind pending work.

    While a turn runs or prompts wait, the switch joins the message queue as
    an ordered row - prompts sent before it keep the engine they were written
    under - and applies exactly like a queued model change. With nothing
    pending it applies immediately, as before."""
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
    # No await between this guard and the queue/apply below. An updater claims
    # its per-engine slot synchronously after its own blocker check, so a
    # switch can neither land nor queue while the target's installed package
    # is being rewritten, and the updater cannot miss a switch queued here.
    if cli_upgrade.is_running(engine):
        return web.json_response(
            {"error": "{} is being updated - try again when it finishes".format(
                driver.label)}, status=409)
    try:
        choices = engine_defaults.for_session(driver, {})
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    result = runner.hub(s["id"]).request_engine_switch(
        engine, choices["model"], choices["effort"], choices["permission_mode"])
    if "error" in result:
        return web.json_response({"error": result["error"]}, status=409)
    return web.json_response(
        {"ok": True, "queued": bool(result.get("queued")),
         "session": runner.session_payload(db.get_session(s["id"]))})


async def h_session_tool(request: web.Request):
    """Run one engine-native session tool (additive `session-tools`):
    "compact" summarizes the native context, queued behind pending work;
    "undo" drops the last prompt and its reply from the native conversation
    and needs an idle session. The engine decides what it offers."""
    s = _session_or_404(request)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid tool request"}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get("tool"), str):
        return web.json_response({"error": "tool must be text"}, status=400)
    tool = body["tool"].strip()
    if tool not in runner.SESSION_TOOLS:
        return web.json_response({"error": "unknown tool '{}'".format(tool[:32])},
                                 status=400)
    result = runner.hub(s["id"]).request_tool(tool)
    if "error" in result:
        return web.json_response({"error": result["error"]}, status=409)
    return web.json_response(result)


async def h_session_events(request: web.Request):
    s = _session_or_404(request)
    before = request.query.get("before_seq")
    after = request.query.get("after_seq")
    try:
        limit = int(request.query.get("limit", "200"))
        before_seq = int(before) if before is not None else None
        after_seq = int(after) if after is not None else None
    except (TypeError, ValueError):
        return web.json_response({"error": "event cursor and limit must be integers"},
                                 status=400)
    if not 1 <= limit <= 500:
        return web.json_response({"error": "event limit must be between 1 and 500"},
                                 status=400)
    if before_seq is not None and after_seq is not None:
        return web.json_response(
            {"error": "use either before_seq or after_seq, not both"}, status=400)
    if before_seq is not None and before_seq < 1:
        return web.json_response({"error": "event cursor must be positive"}, status=400)
    if after_seq is not None and after_seq < 0:
        return web.json_response({"error": "event cursor cannot be negative"}, status=400)
    events = db.get_events(s["id"], before_seq=before_seq, limit=limit,
                           after_seq=after_seq)
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
    transport = web_tls.settings_payload()
    configured_listener = web_tls.configured_listener(
        config.get("web.host"), config.get("web.port"))
    configured_web = {
        **configured_listener,
        "openssl_available": transport["openssl_available"],
        "identities": transport["identities"],
    }
    runtime_web = request.app.get("puppy_runtime_web") or configured_listener
    started = float(request.app.get("puppy_started_monotonic", time.monotonic()))
    return web.json_response({
        "instance_name": config.get("instance_name"),
        "terminal_command": config.get("terminal.command"),
        "default_cwd": config.get("sessions.default_cwd"),
        "api_token": config.get("auth.api_token"),
        "web": configured_web,
        "active_web": runtime_web,
        "web_restart_required": web_tls.listener_key(configured_listener) !=
                                web_tls.listener_key(runtime_web),
        "uptime_seconds": max(0, int(time.monotonic() - started)),
        "usage_refresh": usage_refresh.payload(),
        "timers": config.timers_payload(),
        "uploads": uploads.settings_payload(),
        "version": __version__,
    })


async def h_settings_patch(request: web.Request):
    body = await request.json()
    node_changed = False
    if "instance_name" in body:
        instance_name = str(body["instance_name"]).strip()[:60] or "puppy"
        # the spawn bridge resolves this name to the local node, so it must
        # not shadow a paired backend or a reserved alias
        conflict = backends.instance_name_conflict(instance_name)
        if conflict:
            return web.json_response({"error": conflict}, status=409)
        config.set_value("instance_name", instance_name)
        node_changed = True
    if "terminal_command" in body:
        config.set_value("terminal.command", str(body["terminal_command"]).strip() or "/bin/bash -l")
    if "default_cwd" in body:
        config.set_value("sessions.default_cwd", str(body["default_cwd"]).strip() or "/")
    if node_changed:
        state_stream.wake("node")
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
            proposed_port, connected_host=connected_host,
            target_scheme=body.get("scheme"),
            https_source=body.get("https_source"),
            certificate_path=body.get("certificate_path"),
            private_key_path=body.get("private_key_path")))
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
            not listener_handoff.target_matches(record, request.host, request.scheme):
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
    configured = web_tls.configured_listener(
        config.get("web.host"), config.get("web.port", 0))
    if web_tls.listener_key(configured) != web_tls.listener_key(record):
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
        request.app, request.match_info["token"], request.host, request.scheme)
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
    auth.set_session_cookie(request, response, session_token)
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
    workspace_links.pause_for_snapshot()
    notify.pause_for_snapshot()
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
        workspace_links.resume_after_snapshot()
        notify.resume_after_snapshot()
        state_stream.wake()


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
    restored_state = False
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
        state_stream.invalidate()
        workspace_links.pause_for_snapshot()
        notify.pause_for_snapshot()
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
        restored_state = True
        notify.reset_after_restore()
        usage_refresh.reset_due(clear_status=True)
        backends.reset_auto_upgrade_schedule()
        # the restored database replaces every session this node knew, so the
        # derived search index must be re-derived rather than trusted
        search.request_full_reconcile()
        try:
            await browser.apply_config()
        except Exception as exc:
            log.warning("restored state but could not reconcile the browser: %s", exc)
        try:
            # Shared terminals are ephemeral and deliberately excluded from a
            # snapshot. Drop ended transcripts and old session-ID bindings so
            # restored chats cannot inherit pre-restore terminal state.
            await terminal.manager().stop("Puppy state was restored")
        except Exception as exc:
            log.warning("restored state but could not clear shared terminals: %s", exc)
        try:
            await backends.close_client()
        except Exception as exc:
            log.warning("restored state but could not close the old backend client: %s", exc)
        await _publish_restored_state(request.app)
        return web.json_response(result)
    except snapshots.SnapshotError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        log.exception("snapshot import failed")
        return web.json_response({"error": "restore failed: {}".format(exc)}, status=500)
    finally:
        if owns_busy:
            request.app["puppy_snapshot_busy"] = None
            workspace_links.resume_after_snapshot(restored=restored_state)
            notify.resume_after_snapshot()
            state_stream.wake()
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
    live_websockets.track(request, ws)
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
                if t in ("steer", "ask"):
                    await ws.send_json({
                        "type": "{}_complete".format(t),
                        "request_id": str(data.get("request_id") or "")[:128],
                        "error": "backup or restore in progress",
                    })
                else:
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
                if t in ("steer", "ask"):
                    await ws.send_json({
                        "type": "{}_complete".format(t),
                        "request_id": str(data.get("request_id") or "")[:128],
                        "error": message,
                    })
                else:
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
            elif t in ("steer", "ask"):
                if t == "steer":
                    res, _status = await _session_steer(s, data)
                else:
                    res, _status = await _session_ask(s, data)
                await ws.send_json({
                    "type": "{}_complete".format(t),
                    "request_id": str(data.get("request_id") or "")[:128],
                    **res,
                })
            elif t == "unqueue":
                try:
                    idx = int(data.get("index", -1))
                except (TypeError, ValueError):
                    idx = -1
                res = h.unqueue(idx, data.get("text") or "")
                if "error" in res:
                    await ws.send_json({"type": "toast", "level": "error", "text": res["error"]})
            elif t == "edit_queue":
                try:
                    idx = int(data.get("index", -1))
                except (TypeError, ValueError):
                    idx = -1
                request_id = str(data.get("request_id") or "")[:80]
                res = await h.edit_queued(idx, data.get("text"))
                await ws.send_json({
                    "type": "queue_edit_complete", "request_id": request_id,
                    **res,
                })
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
    live_websockets.track(request, ws)
    runner.updates_attach(ws)
    try:
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


async def h_completions(request: web.Request):
    """Shared, authenticated authoritative completion cursor."""
    if request.app.get("puppy_snapshot_busy"):
        return web.json_response(
            {"error": "completion history is paused for backup or restore"},
            status=503)
    raw = request.query.get("after", "0")
    try:
        after = int(raw)
    except (TypeError, ValueError):
        return web.json_response({"error": "after must be a non-negative integer"},
                                 status=400)
    if after < 0 or str(after) != str(raw):
        return web.json_response({"error": "after must be a non-negative integer"},
                                 status=400)
    try:
        return web.json_response(notify.completion_events(after))
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=500)


def _notify_broadcast() -> None:
    runner.publish_state({"type": "notify", **notify.public_state()})


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
    notify.wake_worker()
    log.info("notify command %s (backend %s)", "configured" if command else "cleared", bid)
    return web.json_response({"ok": True, "settings": notify.settings()})


async def h_notify_toggle(request: web.Request):
    body = await request.json()
    config.set_value("notify.enabled", bool(body.get("enabled")))
    _notify_broadcast()
    notify.wake_worker()
    return web.json_response({"ok": True, "settings": notify.settings()})


async def h_notify_test(request: web.Request):
    """Run once, now, with the values from the panel (unsaved), so the command
    can be proven before trusting it from another device."""
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
    if be is None or backends.backend_supports(
            bid, protocol.COMPLETION_EVENTS_CAPABILITY) or \
            not notify.accept_remote_fire(bid, sid):
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
    r.add_get("/api/timers", h_timers_get)
    r.add_patch("/api/timers", h_timers_patch)
    r.add_post("/api/engines/refresh", h_engines_refresh)
    r.add_get("/api/engines/auto-upgrade", h_engine_auto_upgrade_get)
    r.add_patch("/api/engines/auto-upgrade", h_engine_auto_upgrade_patch)
    r.add_get("/api/engines/{key:[A-Za-z0-9_-]{1,32}}/defaults", h_engine_defaults)
    r.add_put("/api/engines/{key:[A-Za-z0-9_-]{1,32}}/defaults", h_engine_defaults)
    r.add_post("/api/engines/{key:[A-Za-z0-9_-]{1,32}}/upgrade", h_engine_upgrade)

    r.add_get("/api/sessions", h_sessions_list)
    r.add_get("/api/completions", h_completions)
    r.add_post("/api/sessions", h_session_create)
    r.add_post("/api/sessions/reorder", h_sessions_reorder)
    r.add_get("/api/sessions/{sid:\\d+}", h_session_get)
    r.add_patch("/api/sessions/{sid:\\d+}", h_session_patch)
    r.add_delete("/api/sessions/{sid:\\d+}", h_session_delete)
    r.add_post("/api/sessions/{sid:\\d+}/workspace/reset", h_session_workspace_reset)
    r.add_post("/api/sessions/{sid:\\d+}/message", h_session_message)
    r.add_post("/api/sessions/{sid:\\d+}/steer", h_session_steer)
    r.add_post("/api/sessions/{sid:\\d+}/ask", h_session_ask)
    r.add_post("/api/sessions/{sid:\\d+}/interrupt", h_session_interrupt)
    r.add_post("/api/sessions/{sid:\\d+}/switch", h_session_switch)
    r.add_post("/api/sessions/{sid:\\d+}/tool", h_session_tool)
    r.add_get("/api/sessions/{sid:\\d+}/events", h_session_events)

    r.add_get("/api/fs", h_fs)
    r.add_post("/api/fs/mkdir", h_fs_mkdir)
    r.add_get("/api/ws/session/{sid:\\d+}", ws_session)
    r.add_get("/api/ws/updates", ws_updates)
    if include_terminal:
        terminal.register(app)
        r.add_post("/api/notify/exec", h_notify_exec)
    browser.register(app)
    uploads.register(app)
    workspace_sync.register(app)
    spawn_exec.register(app)
    from puppy import session_links
    session_links.register(app)
    from puppy import session_tasks
    session_tasks.register(app)
    search.register(app)
    agent_notes.register(app)
    state_stream.register(
        app, _state_stream_snapshots, _state_stream_interval,
        snapshot_topics=("engines", "node", "browser_status",
                         "terminal_instances"),
        periodic_topics=("engines", "node", "browser_status"))


def build_app(runtime_web: dict = None,
              runtime_ssl_context=None) -> web.Application:
    app = web.Application(middlewares=[auth.middleware, state_change_guard],
                          client_max_size=8 * 1024 * 1024)
    live_websockets.initialize(app)
    app["puppy_role"] = "full"
    host_metrics.register(app)
    app["puppy_snapshot_busy"] = None
    app["puppy_mutations"] = 0
    app["puppy_started_monotonic"] = time.monotonic()
    app["puppy_runtime_web"] = dict(runtime_web or web_tls.configured_listener(
        config.get("web.host", "127.0.0.1"), int(config.get("web.port", 10888))))
    app["puppy_runtime_ssl_context"] = runtime_ssl_context
    app["puppy_runtime_id"] = secrets.token_urlsafe(16)
    runner.configure_updates(app["puppy_runtime_id"])
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
    app.on_startup.append(notify.start_worker)

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
        try:
            await bind_verify.close_all(app)
            await backends.stop_auto_upgrade_worker(app)
            await backends.stop_health_worker(app)
            await workspace_links.stop_worker(app)
            await notify.stop_worker(app)
            await runner.shutdown()
        finally:
            await live_websockets.close_all(app)
            await backends.close_client()

    app.on_shutdown.append(on_shutdown)
    return app
