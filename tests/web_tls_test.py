#!/usr/bin/env python3
"""No-network frontend TLS identity, HTTPS listener, and cookie checks."""
from __future__ import annotations

import asyncio
from http.cookies import SimpleCookie
import os
from pathlib import Path
import shutil
import subprocess
import sys

import aiohttp
from aiohttp import web


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root  # noqa: E402

TEST_ROOT = private_root("web-tls-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")
os.environ["PUPPY_SETUP_CODE"] = "web-tls-bootstrap-code"

from puppy import auth, config, db, web_tls  # noqa: E402
from puppy.web import build_app  # noqa: E402

assert "PUPPY_SETUP_CODE" not in os.environ


def expect_tls_error(call, contains: str) -> None:
    try:
        call()
    except web_tls.WebTLSError as exc:
        assert contains.lower() in str(exc).lower(), str(exc)
    else:
        raise AssertionError("invalid WebUI TLS input was accepted")


async def exercise_exposed_setup_gate() -> None:
    assert auth.setup_code_required_for_host("0.0.0.0") is True
    assert auth.setup_code_required_for_host("console.example.test") is True
    assert auth.setup_code_required_for_host("127.0.0.1") is False
    assert auth.setup_code_required_for_host("::1") is False
    assert auth.setup_code_required_for_host("localhost") is False

    listener = web_tls.configured_listener("0.0.0.0", 0)
    app = build_app(runtime_web=listener)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    url = "http://127.0.0.1:{}".format(port)
    account = {"username": "bootstrap-admin", "password": "secret123"}
    try:
        async with aiohttp.ClientSession() as http:
            async with http.get(url + "/api/auth/status") as response:
                status = await response.json()
                assert status["setup_required"] is True
                assert status["setup_code_required"] is True
                assert "web-tls-bootstrap-code" not in str(status)
            async with http.post(url + "/api/auth/setup", json=account) as response:
                assert response.status == 403, await response.text()
            async with http.post(url + "/api/auth/setup", json=dict(
                    account, setup_code="wrong-bootstrap-code")) as response:
                assert response.status == 403, await response.text()
            async def valid_setup(username):
                body = dict(account, username=username,
                            setup_code="web-tls-bootstrap-code")
                async with http.post(url + "/api/auth/setup", json=body) as response:
                    return response.status, await response.text()

            raced = await asyncio.gather(
                valid_setup("bootstrap-admin-a"),
                valid_setup("bootstrap-admin-b"))
            assert sorted(status for status, _text in raced) == [200, 400], raced
            assert db.query_one("SELECT count(*) AS n FROM users")["n"] == 1
            async with http.get(url + "/api/auth/status") as response:
                status = await response.json()
                assert status["setup_required"] is False
                assert status["setup_code_required"] is False
    finally:
        await runner.cleanup()
        db.execute("DELETE FROM web_sessions")
        db.execute("DELETE FROM users")


async def exercise_https(runtime: web_tls.Runtime) -> None:
    listener = runtime.listener("127.0.0.1", 0)
    app = build_app(runtime_web=listener, runtime_ssl_context=runtime.context)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=runtime.context)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    app["puppy_runtime_web"]["port"] = port
    url = "https://127.0.0.1:{}".format(port)
    jar = aiohttp.CookieJar(unsafe=True)
    try:
        async with aiohttp.ClientSession(cookie_jar=jar) as http:
            async with http.get(url + "/api/auth/status", ssl=False) as response:
                status = await response.json()
                assert status["setup_code_required"] is False
            async with http.post(
                    url + "/api/auth/setup", ssl=False,
                    json={"username": "tls-admin", "password": "secret123"}) as response:
                assert response.status == 200, await response.text()
                cookies = SimpleCookie()
                cookies.load(response.headers.get("Set-Cookie", ""))
                assert auth.SECURE_COOKIE_NAME in cookies
                morsel = cookies[auth.SECURE_COOKIE_NAME]
                assert morsel["secure"] is True
                assert morsel["httponly"] is True
            async with http.get(url + "/api/settings", ssl=False) as response:
                payload = await response.json()
                assert response.status == 200, payload
                assert payload["active_web"]["scheme"] == "https"
                assert payload["active_web"]["certificate_sha256"] == \
                    runtime.identity.fingerprint
                assert isinstance(payload["uptime_seconds"], int)
                assert payload["uptime_seconds"] >= 0
            token_headers = {
                "X-Puppy-Token": config.get("auth.api_token"), "Origin": url,
            }
            async with http.post(
                    url + "/api/settings/bind/prepare", ssl=False,
                    headers=token_headers, json={
                        "host": "127.0.0.1", "port": port, "origin": url,
                        "scheme": "http", "https_source": "custom",
                    }) as response:
                prepared = await response.json()
                assert response.status == 200, prepared
                assert prepared["verify_url"].startswith("https://")
                assert prepared["next_url"].startswith("http://")
            async with http.get(
                    prepared["verify_url"], ssl=False,
                    headers={"Origin": url}) as response:
                proof = await response.json()
                assert response.status == 200, proof
            async with http.post(
                    url + "/api/settings/bind/commit", ssl=False,
                    headers=token_headers, json={"token": prepared["token"]}) as response:
                committed = await response.json()
                assert response.status == 200, committed
                assert committed["scheme"] == "http"
                assert committed["restart_required"] is True
            async with http.get(url + "/api/settings", ssl=False) as response:
                pending = await response.json()
                assert pending["web"]["scheme"] == "http"
                assert pending["active_web"]["scheme"] == "https"
                assert pending["web_restart_required"] is True
            try:
                async with http.get("http://127.0.0.1:{}".format(port)):
                    raise AssertionError("plaintext HTTP reached the HTTPS-only listener")
            except (aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError):
                pass
    finally:
        await runner.cleanup()


async def main() -> None:
    try:
        config.load()
        db.connect()
        assert web_tls.load_state() == web_tls.DEFAULT_STATE
        assert web_tls.settings_payload()["identities"]["auto"]["available"] is False
        assert config.get("web.host") == "127.0.0.1"
        await exercise_exposed_setup_gate()

        openssl = shutil.which("openssl")
        assert openssl, "this test host needs openssl for the generation path"
        prepared = web_tls.prepare_change(
            "https", "auto", "", "",
            ("console.example.test", "192.0.2.15", "0.0.0.0"))
        web_tls.commit_change(prepared)
        runtime = web_tls.load_runtime()
        assert runtime.scheme == "https"
        assert runtime.identity is not None
        assert set(runtime.identity.sans) == {
            "DNS:console.example.test", "DNS:localhost",
            "IP:127.0.0.1", "IP:192.0.2.15", "IP:::1",
        }
        certificate_report = subprocess.check_output([
            openssl, "x509", "-in", runtime.identity.certificate,
            "-noout", "-subject", "-issuer", "-ext", "subjectAltName",
        ], text=True)
        assert "CN = Puppy" in certificate_report
        assert "console.example.test" in certificate_report
        assert config.get("instance_name") not in certificate_report
        assert "organization" not in certificate_report.lower()
        public = web_tls.settings_payload()
        serialized = str(public)
        assert public["identities"]["auto"]["usable"] is True
        assert runtime.identity.private_key not in serialized
        assert "PRIVATE KEY" not in serialized

        # Importing a server-side pair copies it beneath Puppy's private TLS
        # directory; the source paths never become persisted listener state.
        imported = web_tls.prepare_change(
            "https", "custom", runtime.identity.certificate,
            runtime.identity.private_key, ("console.example.test",))
        web_tls.commit_change(imported)
        custom_runtime = web_tls.load_runtime()
        assert custom_runtime.identity.source == "custom"
        assert custom_runtime.identity.fingerprint == runtime.identity.fingerprint
        assert Path(custom_runtime.identity.certificate).parent == \
            Path(config.DATA_DIR).resolve() / "tls" / "web"

        # An identity that becomes date-invalid after startup remains visible
        # in Settings so it can be replaced, but a fresh runtime refuses it.
        check_dates = web_tls._certificate_dates
        try:
            def expired(_path):
                raise web_tls.WebTLSError("WebUI certificate has expired")
            web_tls._certificate_dates = expired
            expired_public = web_tls.settings_payload()["identities"]["custom"]
            assert expired_public["available"] is True
            assert expired_public["usable"] is False
            assert "expired" in expired_public["error"]
            expect_tls_error(web_tls.load_runtime, "expired")
        finally:
            web_tls._certificate_dates = check_dates

        wrong_key = TEST_ROOT / "wrong.key"
        subprocess.check_call([
            openssl, "genrsa", "-out", str(wrong_key), "2048",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        expect_tls_error(lambda: web_tls.prepare_change(
            "https", "custom", runtime.identity.certificate,
            str(wrong_key), ("console.example.test",)), "usable PEM pair")

        await exercise_https(custom_runtime)

        db.meta_del(web_tls.STATE_KEY)
        expect_tls_error(web_tls.load_state, "missing")
        db.meta_set(web_tls.STATE_KEY, web_tls.DEFAULT_STATE)
        db.execute("UPDATE meta SET value=? WHERE key=?",
                   ("not-json", web_tls.STATE_KEY))
        expect_tls_error(web_tls.load_state, "unreadable")
        db.meta_set(web_tls.STATE_KEY, web_tls.DEFAULT_STATE)
        print("WebUI TLS generation, custom import, HTTPS, secure cookies, and uptime passed")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
