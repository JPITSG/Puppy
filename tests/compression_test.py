#!/usr/bin/env python3
"""Startup gzip on the wire, cache invalidation and normal static semantics."""
import asyncio
import gzip
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from puppy import compression
from tests.scratch import private_root


async def main():
    root = private_root("compression-")
    static = root / "static"
    static.mkdir()
    script = static / "app.js"
    original = b"console.log('first');\n" * 5000
    script.write_bytes(original)
    stylesheet = b".panel { display: flex; }\n" * 500
    (static / "app.css").write_bytes(stylesheet)
    (static / "small.js").write_text("void 0;\n")
    (static / "text.txt").write_bytes(original)
    (static / "text.txt.gz").write_bytes(gzip.compress(original, mtime=0))
    outside = root / "private.js"
    outside.write_bytes(b"private source\n" * 1000)
    (static / "outside.js").symlink_to(outside)
    state = {"sessions": [{"name": "Garden planner", "status": "idle"}] * 100}
    page = "<p>Startup page</p>" * 100

    async def index(request):
        return web.Response(text=page, content_type="text/html")

    async def initial_state(request):
        return web.json_response(state, headers={"Vary": "Origin"})

    app = web.Application(middlewares=[compression.middleware])
    app.router.add_get("/", index)
    app.router.add_get("/api/state", initial_state)
    app.router.add_static("/static/", static, follow_symlinks=False)
    server = TestServer(app)
    await server.start_server()
    compression._gzip_asset.cache_clear()
    compressed_on = []
    real_gzip = compression._gzip

    def observed_gzip(body):
        compressed_on.append(threading.get_ident())
        return real_gzip(body)

    try:
        with patch.object(compression, "_gzip", observed_gzip):
            async with aiohttp.ClientSession(auto_decompress=False,
                    skip_auto_headers={"Accept-Encoding"}) as http:
                async def get(path, encoding=None, method="GET", **headers):
                    if encoding is not None:
                        headers["Accept-Encoding"] = encoding
                    async with http.request(method, server.make_url(path), headers=headers) as response:
                        return response.status, response.headers, await response.read()

                status, headers, body = await get("/static/app.js?v=one", "gzip")
                assert status == 200 and headers["Content-Encoding"] == "gzip"
                assert headers["Vary"] == "Accept-Encoding"
                assert "javascript" in headers["Content-Type"]
                assert int(headers["Content-Length"]) == len(body) < len(original) / 2
                assert gzip.decompress(body) == original
                calls = len(compressed_on)
                assert calls == 1
                # A query string must neither duplicate the cache nor choose
                # a stale representation when the source file changes.
                status, head, empty = await get("/static/app.js?v=two", "gzip", method="HEAD")
                assert status == 200 and not empty
                assert head["Content-Length"] == headers["Content-Length"]
                assert len(compressed_on) == calls
                for encoding, zipped in [
                    (None, False), ("identity", False), ("br", False),
                    ("gzip, deflate, br", True), ("GZip", True),
                    ("gzip;q=0.3", True), ("*;q=0.5", True),
                    ("gzip;q=0, *;q=1", False), ("gzip;q=nan", False),
                    ("gzip;q=invalid", False), ("xgzip", False),
                ]:
                    status, negotiated, data = await get("/static/app.js", encoding)
                    assert status == 200
                    assert (negotiated.get("Content-Encoding") == "gzip") is zipped, encoding
                    assert (gzip.decompress(data) if zipped else data) == original, encoding
                assert len(compressed_on) == calls, "warm assets should not be recompressed"
                status, headers, body = await get("/static/app.css", "gzip")
                assert headers["Content-Type"] == "text/css"
                assert gzip.decompress(body) == stylesheet

                _, plain, _ = await get("/static/app.js", "identity")
                status, headers, body = await get("/static/app.js", "gzip", Range="bytes=0-19")
                assert status == 206 and body == original[:20]
                assert "Content-Encoding" not in headers
                assert headers["Content-Range"] == "bytes 0-19/{}".format(len(original))
                status, _, body = await get("/static/app.js", "gzip", **{"If-None-Match": plain["ETag"]})
                assert status == 304 and not body
                assert (await get("/static/app.js", "gzip", Range="bytes=999999999-"))[0] == 416
                status, headers, body = await get("/static/small.js", "gzip")
                assert status == 200 and "Content-Encoding" not in headers and body == b"void 0;\n"
                status, headers, body = await get("/static/text.txt", "gzip")
                assert status == 200 and gzip.decompress(body) == original

                # Atomic replacement with the same length and mtime still
                # invalidates the cache, even while old versions remain in it.
                before = script.stat()
                replacement = static / "replacement.js"
                changed = original.replace(b"first", b"later")
                replacement.write_bytes(changed)
                os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
                replacement.replace(script)
                status, _, body = await get("/static/app.js", "gzip")
                assert status == 200 and gzip.decompress(body) == changed
                script.unlink()
                assert (await get("/static/app.js", "gzip"))[0] == 404
                assert (await get("/static/outside.js", "gzip"))[0] == 404
                assert (await get("/static/missing.js", "gzip"))[0] == 404

                for marker in ["one", "two"]:
                    state["marker"] = marker
                    status, headers, body = await get("/api/state", "gzip")
                    assert status == 200 and headers["Content-Encoding"] == "gzip"
                    assert headers["Vary"] == "Origin, Accept-Encoding"
                    assert int(headers["Content-Length"]) == len(body)
                    assert json.loads(gzip.decompress(body)) == state, "state must never be cached"
                status, headers, body = await get("/api/state", "gzip;q=0")
                assert "Content-Encoding" not in headers and json.loads(body) == state
                status, headers, body = await get("/", "gzip")
                assert status == 200 and gzip.decompress(body).decode() == page
                assert all(tid != threading.get_ident() for tid in compressed_on), \
                    "compression must not block the event loop"
        print("PASS: gzip negotiation, warm assets, source replacement, HEAD/ranges/304, "
              "missing/escaped paths, gzip siblings, fresh state and off-loop compression")
    finally:
        await server.close()
        compression._gzip_asset.cache_clear()
        shutil.rmtree(root)


if __name__ == "__main__":
    asyncio.run(main())
