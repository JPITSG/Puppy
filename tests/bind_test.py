#!/usr/bin/env python3
"""No-quota browser-reachability tests for verified WebUI bind changes."""
from __future__ import annotations

import asyncio
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import time
from urllib.parse import urlsplit
import warnings

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

PRIVATE_TESTS = BASE / "data" / "tests"
PRIVATE_TESTS.mkdir(parents=True, exist_ok=True, mode=0o700)
PRIVATE_TESTS.chmod(0o700)
TEST_ROOT = Path(tempfile.mkdtemp(prefix="bind-", dir=str(PRIVATE_TESTS)))
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from puppy import auth, bind_verify, config, db, listener_handoff  # noqa: E402
from puppy.web import build_app  # noqa: E402


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


async def json_response(response):
    try:
        return await response.json()
    except Exception:
        return {"error": await response.text()}


def _write_hook(path: Path, source: str, mode: int = 0o700) -> None:
    path.write_text("#!/bin/sh\n" + source, encoding="utf-8")
    path.chmod(mode)


def _expect_hook_error(fragment: str) -> None:
    try:
        listener_handoff.queue_restart()
    except listener_handoff.ListenerHandoffError as exc:
        assert fragment in str(exc).lower(), str(exc)
        assert exc.status == 503
    else:
        raise AssertionError("unsafe restart hook was accepted")


def test_restart_hook_contract() -> None:
    hooks = TEST_ROOT / "restart hooks"
    hooks.mkdir()
    record = hooks / "restart record"
    valid = hooks / "valid hook"
    _write_hook(valid, """
case "$1" in
  probe) [ "$2" = "$PUPPY_TEST_EXPECT_PID" ] ;;
  restart) printf '%s\n%s\n%s\n' "$1" "$2" "$PUPPY_DATA" > "$PUPPY_TEST_RECORD" ;;
  *) exit 2 ;;
esac
""")
    saved_hook = os.environ.get(listener_handoff.RESTART_HOOK_ENV)
    saved_timeout = listener_handoff.RESTART_PROBE_TIMEOUT
    os.environ.update({
        listener_handoff.RESTART_HOOK_ENV: str(valid),
        "PUPPY_TEST_EXPECT_PID": str(os.getpid()),
        "PUPPY_TEST_RECORD": str(record),
    })
    try:
        child = listener_handoff.queue_restart()
        deadline = time.monotonic() + 2
        while not record.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert record.read_text(encoding="utf-8").splitlines() == [
            "restart", str(os.getpid()), os.path.abspath(config.DATA_DIR)]
        os.waitpid(child, 0)

        os.environ.pop(listener_handoff.RESTART_HOOK_ENV, None)
        _expect_hook_error("requires a deployment restart hook")
        os.environ[listener_handoff.RESTART_HOOK_ENV] = "relative-hook"
        _expect_hook_error("absolute path")

        unsafe = hooks / "unsafe hook"
        _write_hook(unsafe, "exit 0\n", mode=0o722)
        os.environ[listener_handoff.RESTART_HOOK_ENV] = str(unsafe)
        _expect_hook_error("unsafe ownership or permissions")

        linked = hooks / "linked hook"
        linked.symlink_to(valid)
        os.environ[listener_handoff.RESTART_HOOK_ENV] = str(linked)
        _expect_hook_error("unsafe ownership or permissions")

        wrong_process = hooks / "wrong process"
        _write_hook(wrong_process, "exit 9\n")
        os.environ[listener_handoff.RESTART_HOOK_ENV] = str(wrong_process)
        _expect_hook_error("could not verify this puppy process")

        timeout = hooks / "timeout hook"
        _write_hook(timeout, "while :; do :; done\n")
        os.environ[listener_handoff.RESTART_HOOK_ENV] = str(timeout)
        listener_handoff.RESTART_PROBE_TIMEOUT = 0.05
        _expect_hook_error("probe timed out")
        listener_handoff.RESTART_PROBE_TIMEOUT = saved_timeout

        vanished = hooks / "vanished hook"
        _write_hook(vanished, """
if [ "$1" = probe ]; then rm -- "$0"; exit 0; fi
exit 1
""")
        os.environ[listener_handoff.RESTART_HOOK_ENV] = str(vanished)
        _expect_hook_error("unavailable")
    finally:
        listener_handoff.RESTART_PROBE_TIMEOUT = saved_timeout
        if saved_hook is None:
            os.environ.pop(listener_handoff.RESTART_HOOK_ENV, None)
        else:
            os.environ[listener_handoff.RESTART_HOOK_ENV] = saved_hook
        os.environ.pop("PUPPY_TEST_EXPECT_PID", None)
        os.environ.pop("PUPPY_TEST_RECORD", None)


async def main() -> None:
    test_restart_hook_contract()
    port = free_port()
    config.load()
    config.set_value("web.host", "127.0.0.1")
    config.set_value("web.port", port)
    db.connect()
    db.execute("INSERT INTO users(username,pwhash,created_at) VALUES(?,?,?)",
               ("bind-admin", "unused-in-this-test", 1))
    browser_session = auth.issue_session("bind-admin")

    app = build_app()
    restart_calls = []
    app["puppy_restart_hook"] = lambda: restart_calls.append("queued")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    origin = "http://127.0.0.1:{}".format(port)
    auth_headers = {"X-Puppy-Token": config.get("auth.api_token"), "Origin": origin}
    browser_headers = {
        "Cookie": "{}={}".format(auth.COOKIE_NAME, browser_session),
        "Origin": origin,
    }

    try:
        async with aiohttp.ClientSession() as http:
            # The current listener owns this exact socket. Its public nonce
            # route proves that the proposed address reaches this process.
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers=auth_headers,
                                 json={"host": "127.0.0.1", "origin": origin}) as response:
                same = await json_response(response)
                assert response.status == 200, same
            async with http.get(same["verify_url"], headers={"Origin": origin}) as response:
                proof = await json_response(response)
                assert response.status == 200, proof
                assert response.headers["Access-Control-Allow-Origin"] == origin
                assert proof == {"ok": True, "token": same["token"]}
            async with http.post(origin + "/api/settings/bind/commit",
                                 headers=auth_headers,
                                 json={"token": same["token"]}) as response:
                committed = await json_response(response)
                assert response.status == 200, committed
                assert committed["restart_required"] is False

            # A new port is proved by a temporary listener on the exact
            # proposed endpoint. Config changes only after the browser reaches
            # it, while the original WebUI socket remains active.
            proposed_port = free_port()
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers=auth_headers,
                                 json={"host": "127.0.0.1", "port": proposed_port,
                                       "origin": origin}) as response:
                port_change = await json_response(response)
                assert response.status == 200, port_change
            assert port_change["port"] == proposed_port
            assert port_change["verify_url"].startswith(
                "http://127.0.0.1:{}/".format(proposed_port))
            assert config.get("web.port") == port
            async with http.get(port_change["verify_url"],
                                headers={"Origin": origin}) as response:
                proof = await json_response(response)
                assert response.status == 200, proof
            assert config.get("web.port") == port
            async with http.post(origin + "/api/settings/bind/commit",
                                 headers=auth_headers,
                                 json={"token": port_change["token"]}) as response:
                committed = await json_response(response)
                assert response.status == 200, committed
            assert committed["port"] == proposed_port
            assert committed["previous_port"] == port
            assert committed["restart_required"] is True
            assert "handoff" not in committed  # API tokens cannot mint browser sessions
            assert config.get("web.port") == proposed_port
            async with http.get(origin + "/api/settings", headers=auth_headers) as response:
                settings = await json_response(response)
                assert settings["web"]["port"] == proposed_port
                assert settings["active_web"]["port"] == port
                assert settings["web_restart_required"] is True
            # Proofs are one-use even though their public route remains safe.
            async with http.post(origin + "/api/settings/bind/commit",
                                 headers=auth_headers,
                                 json={"token": port_change["token"]}) as response:
                assert response.status == 409

            # Revert through the live listener so the remaining address tests
            # continue against the original configured endpoint.
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers=auth_headers,
                                 json={"host": "127.0.0.1", "port": port,
                                       "origin": origin}) as response:
                port_revert = await json_response(response)
                assert response.status == 200, port_revert
            async with http.get(port_revert["verify_url"],
                                headers={"Origin": origin}) as response:
                assert response.status == 200
            async with http.post(origin + "/api/settings/bind/commit",
                                 headers=auth_headers,
                                 json={"token": port_revert["token"]}) as response:
                reverted = await json_response(response)
                assert response.status == 200, reverted
                assert reverted["restart_required"] is False
            assert config.get("web.port") == port

            # The ordinary settings patch cannot bypass endpoint proof.
            async with http.patch(origin + "/api/settings", headers=auth_headers,
                                  json={"web": {"host": "127.0.0.8",
                                                "port": proposed_port}}) as response:
                unchanged = await json_response(response)
                assert response.status == 200, unchanged
            assert config.get("web.host") == "127.0.0.1"
            assert config.get("web.port") == port

            # An unrelated process on a proposed new port is a hard conflict;
            # Puppy's active listener cannot explain EADDRINUSE there.
            occupied_port_server = await asyncio.start_server(
                lambda _reader, writer: writer.close(), "127.0.0.6", 0)
            occupied_port = occupied_port_server.sockets[0].getsockname()[1]
            try:
                async with http.post(origin + "/api/settings/bind/prepare",
                                     headers=auth_headers,
                                     json={"host": "127.0.0.6", "port": occupied_port,
                                           "origin": origin}) as response:
                    collision = await json_response(response)
                    assert response.status == 400, collision
                    assert "address already in use" in collision["error"].lower()
            finally:
                occupied_port_server.close()
                await occupied_port_server.wait_closed()
            assert config.get("web.port") == port

            # EADDRINUSE on an address the active Puppy listener cannot own is
            # a real collision, not permission to rely on the public route.
            occupied = await asyncio.start_server(
                lambda _reader, writer: writer.close(), "127.0.0.5", port)
            try:
                async with http.post(origin + "/api/settings/bind/prepare",
                                     headers=auth_headers,
                                     json={"host": "127.0.0.5",
                                           "origin": origin}) as response:
                    collision = await json_response(response)
                    assert response.status == 400, collision
                    assert "address already in use" in collision["error"].lower()
            finally:
                occupied.close()
                await occupied.wait_closed()

            # A different loopback address receives a temporary listener on
            # the real configured port. OPTIONS alone must never count as proof.
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers=auth_headers,
                                 json={"host": "127.0.0.2", "origin": origin}) as response:
                alternate = await json_response(response)
                assert response.status == 200, alternate
            async with http.options(alternate["verify_url"], headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Private-Network": "true",
            }) as response:
                assert response.status == 204
                assert response.headers["Access-Control-Allow-Private-Network"] == "true"
            async with http.post(origin + "/api/settings/bind/commit",
                                 headers=auth_headers,
                                 json={"token": alternate["token"]}) as response:
                unverified = await json_response(response)
                assert response.status == 409, unverified
            async with http.get(alternate["verify_url"],
                                headers={"Origin": origin}) as response:
                proof = await json_response(response)
                assert response.status == 200, proof
            async with http.post(origin + "/api/settings/bind/commit",
                                 headers=auth_headers,
                                 json={"token": alternate["token"]}) as response:
                committed = await json_response(response)
                assert response.status == 200, committed
                assert committed["host"] == "127.0.0.2"
                assert committed["restart_required"] is True
            assert config.get("web.host") == "127.0.0.2"
            async with http.get(origin + "/api/settings", headers=auth_headers) as response:
                settings = await json_response(response)
                assert response.status == 200, settings
                assert settings["web"]["host"] == "127.0.0.2"
                assert settings["active_web"]["host"] == "127.0.0.1"
                assert settings["web_restart_required"] is True

            # A response reached through the wrong browser origin cannot arm
            # the commit token, even though the temporary listener is healthy.
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers=auth_headers,
                                 json={"host": "127.0.0.3", "origin": origin}) as response:
                wrong_origin = await json_response(response)
                assert response.status == 200, wrong_origin
            async with http.get(wrong_origin["verify_url"],
                                headers={"Origin": "http://example.invalid"}) as response:
                assert response.status == 403
            async with http.post(origin + "/api/settings/bind/commit",
                                 headers=auth_headers,
                                 json={"token": wrong_origin["token"]}) as response:
                assert response.status == 409
            assert config.get("web.host") == "127.0.0.2"

            # A proof prepared against stale config cannot overwrite a newer
            # listener choice made by another administrator.
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers=auth_headers,
                                 json={"host": "127.0.0.4", "origin": origin}) as response:
                stale = await json_response(response)
                assert response.status == 200, stale
            async with http.get(stale["verify_url"],
                                headers={"Origin": origin}) as response:
                assert response.status == 200
            config.set_value("web.host", "127.0.0.9")
            async with http.post(origin + "/api/settings/bind/commit",
                                 headers=auth_headers,
                                 json={"token": stale["token"]}) as response:
                changed_during_proof = await json_response(response)
                assert response.status == 409, changed_during_proof
                assert "changed while" in changed_during_proof["error"]
            assert config.get("web.host") == "127.0.0.9"
            config.set_value("web.host", "127.0.0.2")

            # Invalid input and a mixed-context verification fail closed.
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers=auth_headers,
                                 json={"host": "localhost", "origin": origin}) as response:
                assert response.status == 400
            for invalid_port in (0, -1, 65536, 1.5, True, "12x", ""):
                async with http.post(origin + "/api/settings/bind/prepare",
                                     headers=auth_headers,
                                     json={"host": "127.0.0.1", "port": invalid_port,
                                           "origin": origin}) as response:
                    invalid = await json_response(response)
                    assert response.status == 400, (invalid_port, invalid)
                    assert "whole number" in invalid["error"]
            assert bind_verify.normalize_bind_port("10888") == 10888
            assert bind_verify.normalize_bind_port(10888.0) == 10888
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers=auth_headers,
                                 json={"host": "192.0.2.1", "origin": origin}) as response:
                nonlocal_ip = await json_response(response)
                assert response.status == 400, nonlocal_ip
                assert "cannot bind" in nonlocal_ip["error"]
            https_origin = "https://127.0.0.1:{}".format(port)
            async with http.post(origin + "/api/settings/bind/prepare", headers={
                    "X-Puppy-Token": config.get("auth.api_token"), "Origin": https_origin,
            }, json={"host": "127.0.0.4", "origin": https_origin}) as response:
                assert response.status == 400
            assert config.get("web.host") == "127.0.0.2"

            # A concurrent port edit invalidates an otherwise valid proof just
            # like a concurrent host edit; neither field may be overwritten.
            stale_port = free_port()
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers=auth_headers,
                                 json={"host": "127.0.0.2", "port": stale_port,
                                       "origin": origin}) as response:
                stale_endpoint = await json_response(response)
                assert response.status == 200, stale_endpoint
            async with http.get(stale_endpoint["verify_url"],
                                headers={"Origin": origin}) as response:
                assert response.status == 200
            concurrent_port = free_port()
            config.set_value("web.port", concurrent_port)
            async with http.post(origin + "/api/settings/bind/commit",
                                 headers=auth_headers,
                                 json={"token": stale_endpoint["token"]}) as response:
                changed_during_proof = await json_response(response)
                assert response.status == 409, changed_during_proof
                assert "changed while" in changed_during_proof["error"]
            assert config.get("web.port") == concurrent_port
            config.set_value("web.port", port)

            # Wildcard binding is verified through the concrete origin that is
            # already known to reach this host; 0.0.0.0 and a potentially
            # dual-stack hostname are never used as the proof URL.
            named_origin = "http://puppy.test:{}".format(port)
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers={
                                     "X-Puppy-Token": config.get("auth.api_token"),
                                     "Origin": named_origin,
                                     "Host": "puppy.test:{}".format(port),
                                 },
                                 json={"host": "0.0.0.0",
                                       "origin": named_origin}) as response:
                wildcard = await json_response(response)
                assert response.status == 200, wildcard
                assert wildcard["verify_url"].startswith(origin + "/")
            async with http.get(wildcard["verify_url"],
                                headers={"Origin": named_origin}) as response:
                assert response.status == 200
            async with http.post(origin + "/api/settings/bind/commit",
                                 headers=auth_headers,
                                 json={"token": wildcard["token"]}) as response:
                committed = await json_response(response)
                assert response.status == 200, committed
            assert config.get("web.host") == "0.0.0.0"

            # An IPv6 wildcard cannot be certified through this IPv4-only
            # connection. Treating the hostname as proof could lock the user
            # out after restart if it happened to resolve over IPv4 here.
            async with http.post(origin + "/api/settings/bind/prepare", headers={
                    "X-Puppy-Token": config.get("auth.api_token"),
                    "Origin": named_origin,
                    "Host": "puppy.test:{}".format(port),
            }, json={"host": "::", "origin": named_origin}) as response:
                wrong_family = await json_response(response)
                assert response.status == 400, wrong_family
                assert "cannot safely verify an IPv6 wildcard" in wrong_family["error"]
            assert bind_verify.normalize_bind_ip(
                "0:0:0:0:0:0:0:1") == "::1"

            # Reverting a pending setting to the listener that is already
            # active cancels the restart requirement.
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers=auth_headers,
                                 json={"host": "127.0.0.1",
                                       "origin": origin}) as response:
                active_again = await json_response(response)
                assert response.status == 200, active_again
            async with http.get(active_again["verify_url"],
                                headers={"Origin": origin}) as response:
                assert response.status == 200
            async with http.post(origin + "/api/settings/bind/commit",
                                 headers=auth_headers,
                                 json={"token": active_again["token"]}) as response:
                committed = await json_response(response)
                assert response.status == 200, committed
                assert committed["restart_required"] is False
            async with http.get(origin + "/api/settings", headers=auth_headers) as response:
                settings = await json_response(response)
                assert settings["web_restart_required"] is False

            previous_ttl = bind_verify.VERIFY_TTL
            bind_verify.VERIFY_TTL = 0.05
            try:
                async with http.post(origin + "/api/settings/bind/prepare",
                                     headers=auth_headers,
                                     json={"host": "127.0.0.4", "origin": origin}) as response:
                    expiring = await json_response(response)
                    assert response.status == 200, expiring
                await asyncio.sleep(0.1)
                async with http.get(origin + bind_verify.VERIFY_PREFIX + expiring["token"],
                                    headers={"Origin": origin}) as response:
                    assert response.status == 404
                async with http.post(origin + "/api/settings/bind/commit",
                                     headers=auth_headers,
                                     json={"token": expiring["token"]}) as response:
                    assert response.status == 409
            finally:
                bind_verify.VERIFY_TTL = previous_ttl

            # Unknown/expired one-use routes are public but reveal no state.
            async with http.get(origin + bind_verify.VERIFY_PREFIX + "not-a-token",
                                headers={"Origin": origin}) as response:
                assert response.status == 404

            # A cookie-authenticated browser gets a one-use handoff only after
            # proving the target again. Activation queues exactly one restart,
            # waits for a different runtime on the exact target, then carries
            # both login and namespaced UI state across the new origin.
            target_host = "127.0.0.4"
            target_authority = "{}:{}".format(target_host, port)
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers=browser_headers,
                                 json={"host": target_host, "port": port,
                                       "origin": origin}) as response:
                prepared = await json_response(response)
                assert response.status == 200, prepared
            async with http.get(prepared["verify_url"],
                                headers={"Origin": origin}) as response:
                assert response.status == 200
            async with http.post(origin + "/api/settings/bind/commit",
                                 headers=browser_headers,
                                 json={"token": prepared["token"]}) as response:
                committed = await json_response(response)
                assert response.status == 200, committed
            assert committed["restart_required"] is True
            handoff = committed["handoff"]
            record_path = Path(config.DATA_DIR) / "runtime" / "listener-handoff.json"
            assert record_path.is_file()
            assert handoff["token"] not in record_path.read_text(encoding="utf-8")

            async with http.post(origin + "/api/settings/bind/activate",
                                 headers=browser_headers,
                                 json={"token": "wrong", "browser_state": {}}) as response:
                assert response.status == 409
            assert restart_calls == []

            ui_state = {
                "puppy.tabs": json.dumps({
                    "version": 2, "tabs": [], "active": None,
                    "activeGroup": "pane:handoff",
                    "layout": {"kind": "pane", "id": "pane:handoff",
                               "tabs": [], "active": None},
                }, separators=(",", ":")),
                "puppy.theme": "light",
                "puppy.draft.7": json.dumps({
                    "_puppy_draft": 1,
                    "text": "</script><script>unsafe()</script>",
                    "base_revision": 0,
                    "submitted": False,
                }, separators=(",", ":")),
            }
            async with http.post(origin + "/api/settings/bind/activate",
                                 headers=browser_headers,
                                 json={"token": handoff["token"],
                                       "browser_state": ui_state}) as response:
                activated = await json_response(response)
                assert response.status == 200, activated
            assert activated["queued"] is True
            assert restart_calls == ["queued"]
            # Retrying an interrupted activation response is idempotent.
            async with http.post(origin + "/api/settings/bind/activate",
                                 headers=browser_headers,
                                 json={"token": handoff["token"],
                                       "browser_state": ui_state}) as response:
                retried = await json_response(response)
                assert response.status == 200, retried
            assert restart_calls == ["queued"]
            async with http.post(origin + "/api/settings/bind/prepare",
                                 headers=browser_headers,
                                 json={"host": "127.0.0.5", "port": port,
                                       "origin": origin}) as response:
                already_queued = await json_response(response)
                assert response.status == 409, already_queued
                assert "already queued" in already_queued["error"]
            assert listener_handoff.lookup(handoff["token"]) is not None

            ready_path = urlsplit(activated["ready_url"]).path
            target_headers = {"Host": target_authority, "Origin": origin}
            async with http.options(origin + ready_path, headers={
                    **target_headers,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Private-Network": "true",
            }) as response:
                assert response.status == 204
                assert response.headers["Access-Control-Allow-Origin"] == origin
                assert response.headers["Access-Control-Allow-Private-Network"] == "true"
            async with http.get(origin + ready_path,
                                headers=target_headers) as response:
                waiting = await json_response(response)
                assert response.status == 202, waiting
                assert waiting["ready"] is False

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                app["puppy_runtime_web"] = {"host": target_host, "port": port}
                app["puppy_runtime_id"] = "replacement-runtime"
            async with http.get(origin + ready_path,
                                headers=target_headers) as response:
                ready = await json_response(response)
                assert response.status == 200, ready
                assert ready["ready"] is True

            claim_path = urlsplit(activated["claim_url"]).path
            async with http.get(origin + claim_path,
                                headers={"Host": target_authority},
                                allow_redirects=False) as response:
                bootstrap = await response.text()
                assert response.status == 200, bootstrap
                assert response.headers["Cache-Control"] == "no-store"
                assert "</script><script>unsafe()" not in bootstrap
                assert "\\u003c/script\\u003e" in bootstrap
                cookies = SimpleCookie()
                cookies.load(response.headers["Set-Cookie"])
                new_session = cookies[auth.COOKIE_NAME].value
                assert auth.session_user(new_session) == "bind-admin"
            assert not record_path.exists()
            async with http.get(origin + claim_path,
                                headers={"Host": target_authority}) as response:
                assert response.status == 404
            assert listener_handoff.lookup(handoff["token"]) is None
            record_path.write_text('{"expires_at":"not-a-number"}', encoding="utf-8")
            listener_handoff.cleanup()
            assert not record_path.exists()  # corrupt crash residue cannot wedge startup
    finally:
        await runner.cleanup()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    print("bind proof, graceful restart handoff, CORS, and cleanup passed")


if __name__ == "__main__":
    asyncio.run(main())
