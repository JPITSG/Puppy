#!/usr/bin/env python3
"""Anonymous sign-in isolation and authenticated asset access; no engine calls."""
import asyncio
import os
from pathlib import Path
import re
import shutil
import sys

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root

TEST_ROOT = private_root("auth-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")
from puppy import auth, config, db
from puppy.web import build_app, FAVICON_SVG


async def main():
    config.load()
    db.connect()
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = "http://127.0.0.1:{}".format(site._server.sockets[0].getsockname()[1])
    account = {"username": "demo-admin", "password": "example-password"}
    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as http:
            async def anonymous(setup):
                async with http.get(url + "/") as response:
                    assert response.status == 200
                    assert response.headers["Cache-Control"] == "no-store"
                    page = await response.text()
                    assert "<title>Sign in</title>" in page
                    assert 'id="app"' not in page
                    assert "__V__" not in page
                assets = re.findall(r'(?:src|href)="([^"]+)"', page)
                assert set(assets) == {"/favicon.ico", "/static/auth.css", "/static/auth.js"}
                for path in assets + ["/api/auth/status"]:
                    async with http.get(url + path) as response:
                        assert response.status == 200, path
                        assert response.headers["Cache-Control"] == "no-store", path
                        text = await response.text()
                        assert not re.search(r"puppy|claude|codex|opencode|AI coding", text, re.I), path
                        if path == "/api/auth/status":
                            status = await response.json()
                            assert status["authed"] is False
                            assert status["setup_required"] is setup
                            assert "instance_name" not in status
                        if path == "/favicon.ico":
                            assert text != FAVICON_SVG
                assert "puppy" not in page.lower()
                # Every static file except the explicit public pair requires auth,
                # including HTML, vendor icons/scripts and guessed source paths.
                paths = ["/static/" + str(p.relative_to(config.STATIC_DIR))
                         for p in Path(config.STATIC_DIR).rglob("*") if p.is_file()
                         and p.name not in ("auth.css", "auth.js")]
                paths += ["/static/", "/static/app.js.map", "/api/state", "/api/ping",
                          "/static/auth.css/../app.js", "/static/%61pp.js"]
                for path in paths:
                    async with http.get(url + path) as response:
                        assert response.status == 401, (path, response.status)
                        assert await response.json() == {"error": "auth required"}
                async with http.head(url + "/static/app.js?v=old") as response:
                    assert response.status == 401
                async with http.get(url + "/static/app.js", headers={
                        "Range": "bytes=0-99", "If-None-Match": '"old"'}) as response:
                    assert response.status == 401

            await anonymous(True)
            async with http.post(url + "/api/auth/setup", json=account) as response:
                assert response.status == 200, await response.text()
            async with http.get(url + "/") as response:
                assert response.headers["Cache-Control"] == "no-store"
                page = await response.text()
                assert "Puppy Console" in page
                assert 'id="auth-form"' not in page
                assert "__V__" not in page
            for path in re.findall(r'(?:src|href)="([^\"]+)"', page):
                async with http.get(url + path) as response:
                    assert response.status == 200, path
                    assert response.headers["Cache-Control"] == "no-store", path
            async with http.get(url + "/api/auth/status") as response:
                status = await response.json()
                assert status["authed"] is True
                assert status["instance_name"] == config.get("instance_name")
            async with http.get(url + "/favicon.ico") as response:
                assert await response.text() == FAVICON_SVG
            async with http.post(url + "/api/auth/logout") as response:
                assert response.status == 200
            await anonymous(False)
            async with http.post(url + "/api/auth/login", json=dict(
                    account, password="incorrect")) as response:
                assert response.status == 403
                assert "puppy" not in (await response.text()).lower()
            async with http.post(url + "/api/auth/login", json=account) as response:
                assert response.status == 200
            # Expired/revoked sessions must get the anonymous document, even if
            # this same client previously downloaded the entire console.
            db.execute("DELETE FROM web_sessions")
            await anonymous(False)
            async with http.get(url + "/static/app.js", headers={
                    "X-Puppy-Token": config.get("auth.api_token")}) as response:
                assert response.status == 200
        print("Anonymous source isolation, setup, login/logout, revoked cookies and token assets passed")
    finally:
        await runner.cleanup()
        shutil.rmtree(TEST_ROOT)


if __name__ == "__main__":
    asyncio.run(main())
