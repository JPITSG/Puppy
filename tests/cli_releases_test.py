#!/usr/bin/env python3
"""No-quota tests for advisory engine CLI latest-version checks."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from puppy import cli_releases  # noqa: E402


class FakeDriver:
    key = "future"
    release_source = {"kind": "npm", "package": "@vendor/future-cli"}


async def main() -> None:
    assert cli_releases.compare_versions("future-cli 1.2.3", "1.2.3") == 0
    assert cli_releases.compare_versions("v1.2.2 (Future CLI)", "1.2.3") == -1
    assert cli_releases.compare_versions("future-cli 2.0.0", "1.9.9") == 1
    assert cli_releases.compare_versions("1.0.0-beta.2", "1.0.0") == -1
    assert cli_releases.compare_versions("1.0.0", "1.0.0-rc.1") == 1
    assert cli_releases.compare_versions("not-semver", "1.2.3") is None
    assert cli_releases.compare_versions("1.2.3", "latest") is None
    assert cli_releases.compare_versions("1." + "9" * 200 + ".0", "1.2.3") is None

    state = {"calls": 0, "mode": "ok"}

    async def streamed(request, writes):
        """A body with no Content-Length, delivered in pieces - as npm sends it."""
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        try:
            for piece in writes:
                await response.write(piece)
                await asyncio.sleep(0.01)
            await response.write_eof()
        except (ConnectionError, RuntimeError):
            pass          # a client that hangs up at the cap is the point of the case
        return response

    async def latest(request):
        state["calls"] += 1
        if state["mode"] == "split":
            body = json.dumps({"version": "1.2.4", "readme": "x" * 4000}).encode()
            return await streamed(request, [body[:64], body[64:]])
        if state["mode"] == "huge":
            oversized = cli_releases.MAX_RESPONSE_BYTES // 8192 + 2
            return await streamed(request, [b"x" * 8192] * oversized)
        if state["mode"] == "invalid":
            return web.json_response({"version": "not-a-version"})
        return web.json_response({"version": "1.2.3"})

    app = web.Application()
    app.router.add_get("/{tail:.*}", latest)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    original_registry = cli_releases.NPM_REGISTRY_BASE
    cli_releases.NPM_REGISTRY_BASE = "http://127.0.0.1:{}".format(port)
    cli_releases.reset_for_tests()
    driver = FakeDriver()

    async def check_now():
        """One real fetch, with the periodic and repeated-press cadences stood down."""
        cli_releases._next_due = 0.0
        cli_releases._last_attempt = 0.0
        return await cli_releases.refresh_if_due([driver], force=True)

    try:
        # Concurrent periodic triggers coalesce into one bounded registry call.
        await asyncio.gather(*(cli_releases.refresh_if_due([driver]) for _ in range(4)))
        assert state["calls"] == 1
        stale = cli_releases.status(driver, "future-cli 1.2.2")
        assert stale["latest_version"] == "1.2.3"
        assert stale["update_available"] is True
        assert stale["latest_checked_at"] is not None
        assert stale["latest_check_error"] == ""
        assert cli_releases.status(driver, "future-cli 1.2.3")["update_available"] is False
        assert cli_releases.status(driver, "future-cli 1.3.0")["update_available"] is False

        # A later registry failure retains the last known advisory result and
        # moves onto the shorter retry cadence instead of breaking status.
        state["mode"] = "invalid"
        delay = await check_now()
        failed = cli_releases.status(driver, "future-cli 1.2.2")
        assert delay == cli_releases.FAILURE_RETRY_SECONDS
        assert failed["latest_version"] == "1.2.3"
        assert failed["update_available"] is True
        assert "invalid latest version" in failed["latest_check_error"]

        # Pressing refresh again inside the window is the same one request.
        pressed = state["calls"]
        await cli_releases.refresh_if_due([driver], force=True)
        assert state["calls"] == pressed

        # A body that arrives in more than one chunk is still one JSON document:
        # reading whatever had landed decoded a truncated prefix and reported the
        # registry as broken every time the response happened to be split.
        state["mode"] = "split"
        await check_now()
        split = cli_releases.status(driver, "future-cli 1.2.3")
        assert split["latest_version"] == "1.2.4", split
        assert split["latest_check_error"] == "", split
        assert split["update_available"] is True

        # Reading to EOF must not cost the cap: an endless body is still refused,
        # and the last known advisory version survives it.
        state["mode"] = "huge"
        await check_now()
        oversized = cli_releases.status(driver, "future-cli 1.2.3")
        assert "too large" in oversized["latest_check_error"], oversized
        assert oversized["latest_version"] == "1.2.4"

        unknown = cli_releases.status(type("NoReleaseDriver", (), {
            "key": "none", "release_source": None,
        })(), "1.0.0")
        assert unknown["latest_version"] == ""
        assert unknown["update_available"] is None
    finally:
        cli_releases.NPM_REGISTRY_BASE = original_registry
        cli_releases.reset_for_tests()
        await runner.cleanup()
    print("CLI release version parsing, coalescing, and failure fallback passed")


if __name__ == "__main__":
    asyncio.run(main())
