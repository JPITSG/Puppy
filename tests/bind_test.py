#!/usr/bin/env python3
"""No-quota browser-reachability tests for verified WebUI bind changes."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

PRIVATE_TESTS = BASE / "data" / "tests"
PRIVATE_TESTS.mkdir(parents=True, exist_ok=True, mode=0o700)
PRIVATE_TESTS.chmod(0o700)
TEST_ROOT = Path(tempfile.mkdtemp(prefix="bind-", dir=str(PRIVATE_TESTS)))
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")

from puppy import bind_verify, config, db  # noqa: E402
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


async def main() -> None:
    port = free_port()
    config.load()
    config.set_value("web.host", "127.0.0.1")
    config.set_value("web.port", port)
    db.connect()

    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    origin = "http://127.0.0.1:{}".format(port)
    auth_headers = {"X-Puppy-Token": config.get("auth.api_token"), "Origin": origin}

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
    finally:
        await runner.cleanup()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)

    print("bind IP browser proof, fail-closed commit, CORS, and cleanup passed")


if __name__ == "__main__":
    asyncio.run(main())
