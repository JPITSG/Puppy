"""Shared persistent sign-in store for this node's managed browsers.

When ``browser.shared_storage`` is on, every managed browser on the node
merges its cookie jar (and, best effort, per-origin localStorage) into one
private store under ``data/browser/shared/``. Each browser three-way syncs
against that trunk with its own last-agreed baseline: local changes flow into
the store, other browsers' changes flow back out, deletions propagate through
the same diff, and a same-cookie conflict keeps the copy from the browser the
user was actually driving. Cookies are the strong path (live CDP import and
removal); localStorage is captured from the visible page and re-seeded only
into documents that do not already hold the key.

Like the rest of ``data/browser/`` this store is deliberately outside snapshot
coverage: it is node-local login state, equivalent to the engines' native
credential stores. Partitioned (CHIPS) cookies are never exported - their CDP
shape is version-dependent and they are third-party embed state, not sign-ins.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time

from puppy import config

log = logging.getLogger("puppy.browser")

STORE_VERSION = 1
MAX_COOKIES = 3000
MAX_COOKIE_BYTES = 8192           # name+value ceiling per cookie
MAX_ORIGINS = 64
MAX_ORIGIN_BYTES = 256 * 1024     # JSON-encoded localStorage per origin
MAX_STORAGE_BYTES = 1536 * 1024   # all origins together
SESSION_COOKIE_RETENTION = 30 * 24 * 3600
# Sites roll session-cookie expiries on every response; drift below this is
# not a change worth rewriting the store (and re-poking peers) over.
EXPIRY_SLACK = 600.0
_SAMESITE = ("Strict", "Lax", "None")
_COMPARE_FIELDS = ("name", "value", "domain", "path", "secure", "httpOnly",
                   "sameSite", "sourceScheme", "sourcePort")


def cookie_from_cdp(raw) -> "dict | None":
    """Normalize one Storage.getCookies entry; None when it must not sync."""
    if not isinstance(raw, dict):
        return None
    if raw.get("partitionKey") not in (None, "", {}):
        return None
    name = raw.get("name")
    value = raw.get("value")
    domain = raw.get("domain")
    if not isinstance(name, str) or not name or not isinstance(value, str) or \
            not isinstance(domain, str) or not domain:
        return None
    if len(name) + len(value) > MAX_COOKIE_BYTES:
        return None
    path = raw.get("path")
    cookie = {
        "name": name, "value": value, "domain": domain,
        "path": path if isinstance(path, str) and path else "/",
        "secure": bool(raw.get("secure")),
        "httpOnly": bool(raw.get("httpOnly")),
    }
    if raw.get("sameSite") in _SAMESITE:
        cookie["sameSite"] = raw["sameSite"]
    expires = raw.get("expires")
    if not raw.get("session") and isinstance(expires, (int, float)) and \
            not isinstance(expires, bool) and math.isfinite(expires) and expires > 0:
        cookie["expires"] = float(expires)
    if raw.get("sourceScheme") in ("Secure", "NonSecure"):
        cookie["sourceScheme"] = raw["sourceScheme"]
    port = raw.get("sourcePort")
    if isinstance(port, int) and not isinstance(port, bool) and 0 < port <= 65535:
        cookie["sourcePort"] = port
    return cookie


def cookie_key(cookie: dict) -> str:
    return "\x00".join((cookie["name"], cookie["domain"], cookie["path"]))


def same_cookie(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    for field in _COMPARE_FIELDS:
        if a.get(field) != b.get(field):
            return False
    ea, eb = a.get("expires"), b.get("expires")
    if (ea is None) != (eb is None):
        return False
    return ea is None or abs(ea - eb) <= EXPIRY_SLACK


def capture_expression(limit: int = MAX_ORIGIN_BYTES) -> str:
    """One bounded page-world read of the current origin's localStorage."""
    return (
        "(function puppySharedStorageCapture(){try{"
        "if(!/^https?:$/.test(location.protocol))return null;"
        "var out={},total=0,i,k,v;"
        "for(i=0;i<localStorage.length;i++){"
        "k=localStorage.key(i);v=localStorage.getItem(k);"
        "total+=k.length+(v?v.length:0);"
        "if(total>%d)return{origin:location.origin,over:true};"
        "out[k]=v;}"
        "return{origin:location.origin,items:out};"
        "}catch(e){return null}})()" % int(limit)
    )


def seed_script(storage_map: dict) -> str:
    """Document-start script that fills only keys the origin does not have.

    Existing values always win locally: overwriting them could clobber a
    token this very browser just refreshed. json.dumps with ensure_ascii
    keeps the embedded blob a safe JS literal (U+2028/29 escaped).
    """
    return (
        "(function puppySharedStorageSeed(){try{"
        "if(!/^https?:$/.test(location.protocol))return;"
        "var map=%s;"
        "var data=map[location.origin];if(!data)return;"
        "for(var k in data){try{"
        "if(localStorage.getItem(k)===null)localStorage.setItem(k,data[k])"
        "}catch(e){}}"
        "}catch(e){}})();" % json.dumps(storage_map, ensure_ascii=True)
    )


def store_path() -> str:
    return os.path.join(config.DATA_DIR, "browser", "shared", "state.json")


def _valid_items(value) -> "dict | None":
    if not isinstance(value, dict):
        return None
    items = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            return None
        items[key] = item
    return items


def _load_blocking(path: str) -> "dict | None":
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as exc:
        raise RuntimeError("shared browser store is unreadable: {}".format(exc))
    if not isinstance(raw, dict) or raw.get("v") != STORE_VERSION:
        # No-migration rule: an unsupported persisted shape is rejected, and
        # for this rebuildable login cache rejection means a fresh store.
        raise RuntimeError("shared browser store has an unsupported shape")
    cookies = {}
    for entry in raw.get("cookies") or []:
        if not isinstance(entry, dict):
            continue
        # Stored cookies carry no "session" flag: session cookies simply have
        # no expires field, so the CDP normalizer revalidates them as-is.
        cookie = cookie_from_cdp(entry.get("cookie"))
        if cookie is None:
            continue
        seen = entry.get("seen")
        cookies[cookie_key(cookie)] = {
            "cookie": cookie,
            "seen": float(seen) if isinstance(seen, (int, float)) and
            not isinstance(seen, bool) and math.isfinite(seen) else time.time(),
        }
    storage = {}
    raw_storage = raw.get("storage")
    if isinstance(raw_storage, dict):
        for origin, entry in raw_storage.items():
            if not isinstance(origin, str) or not isinstance(entry, dict):
                continue
            items = _valid_items(entry.get("items"))
            if not items:
                continue
            ts = entry.get("ts")
            storage[origin] = {
                "items": items,
                "ts": float(ts) if isinstance(ts, (int, float)) and
                not isinstance(ts, bool) and math.isfinite(ts) else time.time(),
            }
    serial = raw.get("serial")
    if not isinstance(serial, int) or isinstance(serial, bool) or serial < 0:
        serial = 0
    try:
        saved_at = float(os.path.getmtime(path))
    except OSError:
        saved_at = None
    return {"cookies": cookies, "storage": storage, "serial": serial,
            "saved_at": saved_at}


def _save_blocking(path: str, payload: dict) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


class SharedStore:
    """The node's one merge point; all mutation happens under its lock."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._loaded = False
        self._cookies = {}    # key -> {"cookie": {...}, "seen": ts}
        self._storage = {}    # origin -> {"items": {...}, "ts": ts}
        self._serial = 0
        self._dirty = False
        self._persistence_error = ""
        self._persistence_error_at = None
        self._last_saved_at = None

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            loaded = await asyncio.get_event_loop().run_in_executor(
                None, _load_blocking, store_path())
        except Exception as exc:
            # The shared store is a rebuildable node-local cache, so an
            # unreadable/current-shape failure starts from empty as before,
            # but it is no longer silent. Mark it dirty so the first browser
            # sync attempts a current-shape durable replacement.
            self._persistence_error = str(exc)[:400]
            self._persistence_error_at = time.time()
            self._dirty = True
            log.warning("%s; starting empty", exc)
            loaded = None
        if loaded is not None:
            self._cookies = loaded["cookies"]
            self._storage = loaded["storage"]
            self._serial = loaded["serial"]
            self._last_saved_at = loaded["saved_at"]
        self._loaded = True

    async def _save(self) -> bool:
        next_serial = self._serial + 1
        payload = {
            "v": STORE_VERSION,
            "serial": next_serial,
            "cookies": [dict(entry) for entry in self._cookies.values()],
            "storage": {origin: dict(entry)
                        for origin, entry in self._storage.items()},
        }
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, _save_blocking, store_path(), payload)
        except Exception as exc:
            self._dirty = True
            self._persistence_error = str(exc)[:400]
            self._persistence_error_at = time.time()
            log.warning("shared browser store save failed: %s", exc)
            return False
        self._serial = next_serial
        self._dirty = False
        self._persistence_error = ""
        self._persistence_error_at = None
        self._last_saved_at = time.time()
        return True

    def _prune_cookies(self, now: float) -> bool:
        changed = False
        for key in list(self._cookies):
            cookie = self._cookies[key]["cookie"]
            expires = cookie.get("expires")
            if expires is not None and expires < now:
                del self._cookies[key]
                changed = True
            elif expires is None and \
                    now - self._cookies[key]["seen"] > SESSION_COOKIE_RETENTION:
                del self._cookies[key]
                changed = True
        if len(self._cookies) > MAX_COOKIES:
            # Session cookies rank by last sighting, persistent ones by expiry.
            ranked = sorted(self._cookies.items(), key=lambda item: (
                item[1]["cookie"].get("expires") or
                item[1]["seen"] + SESSION_COOKIE_RETENTION))
            for key, _entry in ranked[:len(self._cookies) - MAX_COOKIES]:
                del self._cookies[key]
            changed = True
        return changed

    def _prune_storage(self) -> bool:
        changed = False
        while len(self._storage) > MAX_ORIGINS or (self._storage and sum(
                len(json.dumps(entry["items"], ensure_ascii=True))
                for entry in self._storage.values()) > MAX_STORAGE_BYTES):
            oldest = min(self._storage, key=lambda origin: self._storage[origin]["ts"])
            del self._storage[oldest]
            changed = True
        return changed

    async def sync(self, cookies_now: dict, cookie_baseline: dict,
                   storage_now: dict, storage_baseline: dict) -> dict:
        """Three-way merge one browser against the trunk.

        Returns instructions for that browser (cookies to set/delete), the
        agreed post-merge state to adopt as its next baseline, the current
        serial for seed-script freshness, and whether the trunk changed (so
        the caller can wake sibling browsers).
        """
        async with self._lock:
            await self._ensure_loaded()
            now = time.time()
            changed = self._prune_cookies(now)
            set_into = []
            delete_from = []
            for key in set(cookies_now) | set(cookie_baseline) | set(self._cookies):
                browser_cookie = cookies_now.get(key)
                base = cookie_baseline.get(key)
                entry = self._cookies.get(key)
                trunk = entry["cookie"] if entry else None
                browser_changed = not same_cookie(browser_cookie, base)
                trunk_changed = not same_cookie(trunk, base)
                if browser_changed:
                    # Local edits win, including over a concurrent trunk edit.
                    if browser_cookie is None:
                        if entry is not None:
                            del self._cookies[key]
                            changed = True
                    elif not same_cookie(browser_cookie, trunk):
                        self._cookies[key] = {"cookie": dict(browser_cookie),
                                              "seen": now}
                        changed = True
                elif trunk_changed:
                    if trunk is None:
                        if browser_cookie is not None:
                            delete_from.append({
                                "name": browser_cookie["name"],
                                "domain": browser_cookie["domain"],
                                "path": browser_cookie["path"],
                            })
                    elif not same_cookie(browser_cookie, trunk):
                        set_into.append(dict(trunk))
                if browser_cookie is not None and key in self._cookies:
                    self._cookies[key]["seen"] = now

            for origin, items in storage_now.items():
                base = storage_baseline.get(origin) or {}
                entry = self._storage.get(origin)
                trunk_items = dict(entry["items"]) if entry else {}
                merged = dict(trunk_items)
                for key in set(items) | set(base) | set(trunk_items):
                    browser_value = items.get(key)
                    if browser_value != base.get(key):
                        if browser_value is None:
                            merged.pop(key, None)
                        else:
                            merged[key] = browser_value
                if len(json.dumps(merged, ensure_ascii=True)) > MAX_ORIGIN_BYTES:
                    continue
                if merged != trunk_items or entry is None:
                    if merged:
                        self._storage[origin] = {"items": merged, "ts": now}
                        changed = True
                    elif entry is not None:
                        del self._storage[origin]
                        changed = True
                elif entry is not None:
                    entry["ts"] = now
            changed = self._prune_storage() or changed

            if changed or self._dirty:
                await self._save()
            return {
                "set_cookies": set_into,
                "delete_cookies": delete_from,
                "cookie_state": {key: dict(entry["cookie"])
                                 for key, entry in self._cookies.items()},
                "storage_state": {origin: dict(self._storage[origin]["items"])
                                  for origin in storage_now
                                  if origin in self._storage},
                "serial": self._serial,
                "changed": changed,
            }

    async def persistence_health(self) -> dict:
        """Authenticated status metadata; never includes stored site data."""
        async with self._lock:
            await self._ensure_loaded()
            return {
                "ok": not self._persistence_error and not self._dirty,
                "dirty": bool(self._dirty),
                "last_saved_at": self._last_saved_at,
                "error_at": self._persistence_error_at,
                "error": self._persistence_error,
            }

    async def seed_snapshot(self) -> tuple:
        async with self._lock:
            await self._ensure_loaded()
            return self._serial, {origin: dict(entry["items"])
                                  for origin, entry in self._storage.items()}


_store = None


def store() -> SharedStore:
    global _store
    if _store is None:
        _store = SharedStore()
    return _store
