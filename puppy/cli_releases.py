"""Cached latest-release checks for engine CLI tools.

Release metadata is advisory UI state: an unavailable registry must never make
an installed engine unavailable. Drivers declare a small release source and
this module owns bounded network access, semantic-version comparison, request
coalescing, and the periodic lifecycle shared by the full and headless apps.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import quote

import aiohttp

from puppy import __version__

log = logging.getLogger("puppy.cli_releases")

CHECK_INTERVAL_SECONDS = 6 * 60 * 60
FAILURE_RETRY_SECONDS = 15 * 60
# A forced check is a person pressing refresh; repeated presses coalesce into
# one registry request rather than one per press.
FORCE_MIN_INTERVAL_SECONDS = 10
REQUEST_TIMEOUT_SECONDS = 6
MAX_RESPONSE_BYTES = 64 * 1024
NPM_REGISTRY_BASE = "https://registry.npmjs.org"

_SEMVER_RE = re.compile(
    r"(?<![0-9A-Za-z])v?"
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?![0-9A-Za-z.-])"
)

_cache: Dict[str, Dict[str, Any]] = {}
_next_due = 0.0
_last_attempt = 0.0
_refresh_lock: Optional[asyncio.Lock] = None
_refresh_loop = None


def _parsed_version(value: str, exact: bool = False):
    text = str(value or "").strip()
    if len(text) > 128:
        return None
    matches = [_SEMVER_RE.fullmatch(text)] if exact else _SEMVER_RE.finditer(text)
    for match in matches:
        if match is None:
            continue
        prerelease = match.group(4)
        identifiers = tuple(prerelease.split(".")) if prerelease else None
        if identifiers and any(part.isdigit() and len(part) > 1 and part.startswith("0")
                               for part in identifiers):
            continue
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)), identifiers)
    return None


def compare_versions(installed_output: str, latest_version: str) -> Optional[int]:
    """Return -1/0/1 using SemVer precedence, or None for unknown formats."""
    installed = _parsed_version(installed_output)
    latest = _parsed_version(latest_version, exact=True)
    if installed is None or latest is None:
        return None
    if installed[:3] != latest[:3]:
        return -1 if installed[:3] < latest[:3] else 1
    left, right = installed[3], latest[3]
    if left is None or right is None:
        if left is right:
            return 0
        return 1 if left is None else -1  # a stable release follows its prerelease
    for left_part, right_part in zip(left, right):
        if left_part == right_part:
            continue
        left_numeric, right_numeric = left_part.isdigit(), right_part.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_part) < int(right_part) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_part < right_part else 1
    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


def _driver_source(driver) -> Optional[Tuple[str, str]]:
    value = getattr(driver, "release_source", None)
    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "").strip().lower()
    package = str(value.get("package") or "").strip()
    if kind != "npm" or not package or len(package) > 214 or \
            any(ord(char) < 33 for char in package):
        return None
    return kind, package


def status(driver, installed_output: str) -> dict:
    """Dynamic release fields merged into Driver.status() outside its CLI cache."""
    source = _driver_source(driver)
    result = {
        "latest_version": "",
        "latest_checked_at": None,
        "latest_check_error": "",
        "update_available": None,
    }
    if source is None:
        return result
    signature = "{}:{}".format(*source)
    cached = _cache.get(str(driver.key)) or {}
    if cached.get("source") != signature:
        return result
    latest = str(cached.get("latest_version") or "")
    result.update(
        latest_version=latest,
        latest_checked_at=cached.get("checked_at"),
        latest_check_error=str(cached.get("error") or ""),
    )
    comparison = compare_versions(installed_output, latest) if latest else None
    result["update_available"] = comparison == -1 if comparison is not None else None
    return result


async def _read_capped(response) -> bytes:
    """Read a whole response body, refusing anything past the cap.

    StreamReader.read(n) is not "read n bytes": it returns whatever has arrived
    when it first wakes, so a body split across chunks - which is how the
    registry sends its gzipped, unsized responses - decodes as a truncated
    prefix. Loop to EOF instead, and keep the cap enforced on the way.
    """
    chunks = []
    total = 0
    async for chunk in response.content.iter_chunked(8192):
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise RuntimeError("npm registry response is too large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _fetch_npm(http: aiohttp.ClientSession, package: str) -> str:
    url = "{}/{}/latest".format(NPM_REGISTRY_BASE.rstrip("/"), quote(package, safe=""))
    async with http.get(url, allow_redirects=False) as response:
        if response.status != 200:
            raise RuntimeError("npm registry returned HTTP {}".format(response.status))
        if response.content_length is not None and response.content_length > MAX_RESPONSE_BYTES:
            raise RuntimeError("npm registry response is too large")
        body = await _read_capped(response)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        # The byte count is the whole diagnosis when this comes back: a short
        # body means the response was cut, a plausible one means it was not JSON.
        raise RuntimeError(
            "npm registry returned invalid JSON ({} bytes)".format(len(body))) from exc
    version = payload.get("version") if isinstance(payload, dict) else None
    if not isinstance(version, str) or _parsed_version(version, exact=True) is None:
        raise RuntimeError("npm registry returned an invalid latest version")
    return version


async def _refresh(drivers: Iterable) -> bool:
    sources: Dict[str, dict] = {}
    for driver in drivers:
        source = _driver_source(driver)
        if source is None:
            continue
        signature = "{}:{}".format(*source)
        record = sources.setdefault(signature, {"source": source, "keys": []})
        record["keys"].append(str(driver.key))
    if not sources:
        return True

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS, connect=3)
    headers = {
        "Accept": "application/json",
        "User-Agent": "puppy/{0} cli-release-check".format(__version__),
    }
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as http:
        async def fetch(record):
            kind, package = record["source"]
            if kind == "npm":
                return await _fetch_npm(http, package)
            raise RuntimeError("unsupported CLI release source")

        records = list(sources.items())
        results = await asyncio.gather(
            *(fetch(record) for _signature, record in records), return_exceptions=True)

    checked_at = time.time()
    all_ok = True
    for (signature, record), result in zip(records, results):
        for key in record["keys"]:
            previous = _cache.get(key) or {}
            if isinstance(result, Exception):
                all_ok = False
                error = str(result)[:240] or result.__class__.__name__
                _cache[key] = {
                    "source": signature,
                    "latest_version": previous.get("latest_version", "")
                    if previous.get("source") == signature else "",
                    "checked_at": previous.get("checked_at")
                    if previous.get("source") == signature else None,
                    "attempted_at": checked_at,
                    "error": error,
                }
                log.warning("%s latest-version check failed: %s", key, error)
            else:
                _cache[key] = {
                    "source": signature,
                    "latest_version": result,
                    "checked_at": checked_at,
                    "attempted_at": checked_at,
                    "error": "",
                }
    return all_ok


def _lock_for_running_loop() -> asyncio.Lock:
    global _refresh_lock, _refresh_loop
    loop = asyncio.get_running_loop()
    if _refresh_lock is None or _refresh_loop is not loop:
        _refresh_lock = asyncio.Lock()
        _refresh_loop = loop
    return _refresh_lock


async def refresh_if_due(drivers: Iterable, force: bool = False) -> float:
    """Refresh coalesced release sources and return seconds until the next check."""
    global _next_due, _last_attempt
    async with _lock_for_running_loop():
        now = time.monotonic()
        if not force and _next_due > now:
            return _next_due - now
        if force and _last_attempt and now - _last_attempt < FORCE_MIN_INTERVAL_SECONDS:
            return max(0.0, _next_due - now)
        _last_attempt = now
        try:
            successful = await _refresh(drivers)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            successful = False
            log.warning("CLI latest-version refresh failed: %s", exc)
        interval = CHECK_INTERVAL_SECONDS if successful else FAILURE_RETRY_SECONDS
        _next_due = time.monotonic() + interval
        return float(interval)


async def _periodic_worker() -> None:
    from puppy.drivers import all_drivers

    while True:
        try:
            delay = await refresh_if_due(all_drivers())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("CLI latest-version worker failed")
            delay = FAILURE_RETRY_SECONDS
        await asyncio.sleep(max(1.0, delay))


async def _lifecycle(app):
    task = asyncio.create_task(_periodic_worker(), name="puppy-cli-release-check")
    app["puppy_cli_release_task"] = task
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def register(app) -> None:
    """Attach one non-blocking periodic checker to an aiohttp application."""
    if app.get("puppy_cli_release_registered"):
        return
    app["puppy_cli_release_registered"] = True
    app.cleanup_ctx.append(_lifecycle)


def reset_for_tests() -> None:
    global _next_due, _last_attempt, _refresh_lock, _refresh_loop
    _cache.clear()
    _next_due = 0.0
    _last_attempt = 0.0
    _refresh_lock = None
    _refresh_loop = None
