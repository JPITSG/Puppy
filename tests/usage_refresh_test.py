#!/usr/bin/env python3
"""No-token tests for scheduled engine account-usage refreshes."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys

import aiohttp
from aiohttp import web

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tests.scratch import private_root  # noqa: E402

TEST_ROOT = private_root("usage-refresh-")
os.environ["PUPPY_DATA"] = str(TEST_ROOT / "data")
os.environ["CODEX_HOME"] = str(TEST_ROOT / "codex-home")
COUNTER = TEST_ROOT / "account-reads.txt"
os.environ["PUPPY_USAGE_TEST_COUNTER"] = str(COUNTER)

from puppy import config, db, usage_refresh  # noqa: E402
from puppy.drivers import get_driver  # noqa: E402
from puppy.drivers import base as driver_base  # noqa: E402
from puppy.drivers import codex as codex_driver  # noqa: E402
from puppy.web import build_app  # noqa: E402


FAKE_CLI = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args == ["--version"]:
    print("account-test-cli 1.0")
    raise SystemExit(0)
if args == ["login", "status"]:
    print("Logged in")
    raise SystemExit(0)
if args != ["app-server", "--stdio"]:
    raise SystemExit(2)

for raw in sys.stdin:
    request = json.loads(raw)
    method = request.get("method")
    if method == "initialize":
        response = {"id": request["id"], "result": {}}
    elif method == "account/rateLimits/read":
        counter = Path(os.environ["PUPPY_USAGE_TEST_COUNTER"])
        try:
            count = int(counter.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            count = 0
        counter.write_text(str(count + 1), encoding="utf-8")
        if os.environ.get("PUPPY_USAGE_TEST_FAIL"):
            response = {"id": request["id"],
                        "error": {"code": -32000, "message": "simulated account failure"}}
        else:
            response = {"id": request["id"], "result": {
                "rateLimits": {
                    "primary": {"windowDurationMins": 300, "usedPercent": 4,
                                "resetsAt": 2000000000},
                    "secondary": {"windowDurationMins": 10080, "usedPercent": 37,
                                  "resetsAt": 2000001000}
                },
                "rateLimitsByLimitId": {"codex": {
                    "limitId": "codex",
                    "primary": {"windowDurationMins": 300, "usedPercent": 5,
                                "resetsAt": 2000000000},
                    "secondary": {"windowDurationMins": 10080, "usedPercent": 42,
                                  "resetsAt": 2000001000}
                }}
            }}
    elif method == "model/list":
        response = {"id": request["id"], "result": {"data": [{
            "model": "account-test", "displayName": "Account Test",
            "isDefault": True, "supportedReasoningEfforts": [
                {"reasoningEffort": "high", "description": "High"}
            ]
        }], "nextCursor": None}}
    else:
        continue
    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
    sys.stdout.flush()
'''


def read_count() -> int:
    try:
        return int(COUNTER.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 0


async def read_json(response):
    try:
        return await response.json()
    except Exception:
        return {"error": await response.text()}


async def main() -> None:
    fake = TEST_ROOT / "account-test-cli"
    fake.write_text(FAKE_CLI, encoding="utf-8")
    fake.chmod(0o700)
    codex = get_driver("codex")
    other = get_driver("claude")
    original_codex_binary = codex.binary
    original_other_binary = other.binary
    runner = None
    try:
        codex.binary = str(fake)
        other.binary = str(TEST_ROOT / "not-installed")
        driver_base._status_cache.clear()
        codex_driver._quota_cache.update(
            ts=0.0, quota=None, account_as_of=0.0)
        usage_refresh.reset_due(clear_status=True)

        config.load()
        config.set_value("engines.usage_refresh_minutes", 15)
        db.connect()
        app = build_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        url = "http://127.0.0.1:{}".format(port)
        headers = {"X-Puppy-Token": config.get("auth.api_token")}

        async with aiohttp.ClientSession() as http:
            # Concurrent browser polls coalesce into one account read.
            async def engines():
                async with http.get(url + "/api/engines", headers=headers) as response:
                    payload = await read_json(response)
                    assert response.status == 200, payload
                    return payload

            first, concurrent = await asyncio.gather(engines(), engines())
            assert read_count() == 1
            for payload in (first, concurrent):
                status = next(item for item in payload["engines"] if item["key"] == "codex")
                assert status["quota"] and \
                    status["quota"]["weekly_used_percent"] == 42.0, status
                assert status["quota"]["source"] == "account", status
                monitor = status["usage_monitor"]
                assert monitor["version"] == 1 and monitor["bucket"] == "codex"
                assert monitor["account"] is None  # fake CLI has no login identity
                assert monitor["sample"]["used_percent"] == 42.0
                assert monitor["sample"]["resets_at"] == 2000001000
                assert payload["usage_refresh"]["minutes"] == 15
                assert payload["usage_refresh"]["last_success_at"] is not None

            await engines()
            assert read_count() == 1  # interval rate limit

            # Saving a nonzero interval persists it and refreshes immediately.
            async with http.patch(url + "/api/engines/usage-refresh", headers=headers,
                                  json={"minutes": 5}) as response:
                saved = await read_json(response)
                assert response.status == 200, saved
            assert saved["usage_refresh"]["minutes"] == 5
            assert config.get("engines.usage_refresh_minutes") == 5
            assert read_count() == 2

            # Dynamic quota data must not remain trapped behind the slower
            # installed/version/auth status cache.
            codex_driver._quota_cache["quota"] = {
                "weekly_used_percent": 61.0, "source": "test", "as_of": 1.0,
            }
            assert (await codex.status())["quota"]["weekly_used_percent"] == 61.0

            for invalid in (-1, 1441, 1.5, True, "5", None):
                async with http.patch(url + "/api/engines/usage-refresh",
                                      headers=headers, json={"minutes": invalid}) as response:
                    rejected = await read_json(response)
                    assert response.status == 400, (invalid, rejected)
            async with http.patch(url + "/api/engines/usage-refresh",
                                  headers=headers, json={}) as response:
                assert response.status == 400
            assert config.get("engines.usage_refresh_minutes") == 5
            assert read_count() == 2

            # A CLI/account failure is reported but the setting remains saved;
            # subsequent status polls do not hammer the failing process.
            os.environ["PUPPY_USAGE_TEST_FAIL"] = "1"
            async with http.patch(url + "/api/engines/usage-refresh", headers=headers,
                                  json={"minutes": 6}) as response:
                failed = await read_json(response)
                assert response.status == 200, failed
            assert "simulated account failure" in failed["usage_refresh"]["last_error"]
            assert config.get("engines.usage_refresh_minutes") == 6
            assert read_count() == 3
            await engines()
            assert read_count() == 3
            os.environ.pop("PUPPY_USAGE_TEST_FAIL", None)

            async with http.patch(url + "/api/engines/usage-refresh", headers=headers,
                                  json={"minutes": 0}) as response:
                disabled = await read_json(response)
                assert response.status == 200, disabled
            assert disabled["usage_refresh"]["enabled"] is False
            assert config.get("engines.usage_refresh_minutes") == 0
            await engines()
            assert read_count() == 3

            # Manual refresh remains available when the automatic interval is
            # off, and simultaneous clicks coalesce into one account read.
            async def manual_refresh():
                async with http.post(url + "/api/engines/usage-refresh",
                                     headers=headers) as response:
                    payload = await read_json(response)
                    assert response.status == 200, payload
                    return payload

            manual, manual_concurrent = await asyncio.gather(
                manual_refresh(), manual_refresh())
            assert read_count() == 4
            for payload in (manual, manual_concurrent):
                status = next(item for item in payload["engines"]
                              if item["key"] == "codex")
                assert status["quota"]["weekly_used_percent"] == 42.0
                assert payload["usage_refresh"]["enabled"] is False
                assert payload["usage_refresh"]["last_error"] == ""

            async with http.get(url + "/api/engines/usage-refresh") as response:
                assert response.status == 401
            async with http.post(url + "/api/engines/usage-refresh") as response:
                assert response.status == 401

        # Persisted config must carry the complete current shape.
        incomplete = config.export_data()
        incomplete.pop("engines")
        try:
            config.normalize_import(incomplete)
        except ValueError:
            pass
        else:
            raise AssertionError("incomplete config was accepted")
        malformed = config.export_data()
        malformed["engines"]["usage_refresh_minutes"] = "15"
        try:
            config.normalize_import(malformed)
        except ValueError:
            pass
        else:
            raise AssertionError("string usage interval was accepted")

        print("usage refresh API, coalescing, failure, and compatibility tests passed")
    finally:
        os.environ.pop("PUPPY_USAGE_TEST_FAIL", None)
        if runner is not None:
            await runner.cleanup()
        codex.binary = original_codex_binary
        other.binary = original_other_binary
        driver_base._status_cache.clear()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
