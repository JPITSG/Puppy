"""Self-hosted auth: PBKDF2 password users, cookie sessions, aiohttp middleware.

Modeled on the facestreamer house pattern (first-run setup, hashed session tokens,
login rate limiting, origin guard). Remote puppy instances authenticate with the
X-Puppy-Token header (api_token from data/config.json).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time

from aiohttp import web

from puppy import config, db

log = logging.getLogger("puppy.auth")

COOKIE_NAME = "puppy_session"
SESSION_TTL = 30 * 24 * 3600
PBKDF2_ITERS = 300_000
_DUMMY_PWHASH = "pbkdf2_sha256${}$unknown-user-salt${}".format(
    PBKDF2_ITERS, "00" * 32)
RATE_LIMIT_MAX_KEYS = 4096
RATE_LIMIT_PRUNE_SECONDS = 60

PUBLIC_PREFIXES = ("/static/", "/api/settings/bind/verify/",
                   "/api/settings/bind/handoff/")
PUBLIC_PATHS = {"/", "/favicon.ico", "/api/auth/status", "/api/auth/login", "/api/auth/setup"}

_attempts = {}  # key -> [timestamps]
_attempts_last_prune = 0.0


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERS)
    return f"pbkdf2_sha256${PBKDF2_ITERS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, hexhash = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
        return hmac.compare_digest(dk.hex(), hexhash)
    except Exception:
        return False


def has_users() -> bool:
    return db.query_one("SELECT id FROM users LIMIT 1") is not None


def create_user(username: str, password: str) -> None:
    db.execute("INSERT INTO users(username,pwhash,created_at) VALUES(?,?,?)",
               (username, hash_password(password), time.time()))


def check_login(username: str, password: str) -> bool:
    row = db.query_one("SELECT pwhash FROM users WHERE username=?", (username,))
    if row is None:
        # Match a current real account's work factor.  A deliberately different
        # digest keeps the result false without exposing whether the query found
        # a username through a several-hundred-fold timing gap.
        verify_password(password, _DUMMY_PWHASH)
        return False
    return verify_password(password, row["pwhash"])


def _rate_limited(key: str, limit: int = 6, window: int = 300) -> bool:
    global _attempts_last_prune
    now = time.monotonic()
    if now - _attempts_last_prune >= RATE_LIMIT_PRUNE_SECONDS:
        for current, timestamps in list(_attempts.items()):
            recent = [stamp for stamp in timestamps if now - stamp < window]
            if recent:
                _attempts[current] = recent
            else:
                _attempts.pop(current, None)
        _attempts_last_prune = now
    lst = [t for t in _attempts.get(key, []) if now - t < window]
    # Dict insertion order is our bounded least-recently-seen ledger.  Moving
    # this peer to the end keeps saturated attack traffic O(1) between the
    # infrequent global expiry sweeps.
    _attempts.pop(key, None)
    if len(lst) >= limit:
        _attempts[key] = lst
        return True
    if len(_attempts) >= RATE_LIMIT_MAX_KEYS:
        _attempts.pop(next(iter(_attempts)), None)
    lst.append(now)
    _attempts[key] = lst
    return False


def issue_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    th = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    db.execute("INSERT INTO web_sessions(token_hash,username,created_at,expires_at) VALUES(?,?,?,?)",
               (th, username, now, now + SESSION_TTL))
    db.execute("DELETE FROM web_sessions WHERE expires_at < ?", (now,))
    return token


def session_user(token: str):
    if not token:
        return None
    th = hashlib.sha256(token.encode()).hexdigest()
    row = db.query_one("SELECT username, expires_at FROM web_sessions WHERE token_hash=?", (th,))
    if row is None or row["expires_at"] < time.time():
        return None
    return row["username"]


def drop_session(token: str) -> None:
    if token:
        th = hashlib.sha256(token.encode()).hexdigest()
        db.execute("DELETE FROM web_sessions WHERE token_hash=?", (th,))


def token_ok(request: web.Request) -> bool:
    tok = request.headers.get("X-Puppy-Token", "")
    want = config.get("auth.api_token", "")
    return bool(tok) and bool(want) and hmac.compare_digest(tok, want)


def request_user(request: web.Request):
    """Returns username, '@token' for api-token auth, or None."""
    if token_ok(request):
        return "@token"
    return session_user(request.cookies.get(COOKIE_NAME, ""))


@web.middleware
async def middleware(request: web.Request, handler):
    path = request.path
    if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return await handler(request)

    user = request_user(request)
    if user is None:
        return web.json_response({"error": "auth required"}, status=401)

    # origin guard for cookie-authed state-changing requests
    if user != "@token" and request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("Origin")
        if origin:
            try:
                from urllib.parse import urlparse
                if urlparse(origin).netloc != request.host:
                    return web.json_response({"error": "cross-origin denied"}, status=403)
            except Exception:
                return web.json_response({"error": "cross-origin denied"}, status=403)

    request["user"] = user
    resp = await handler(request)
    if path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# ---- route handlers ----

async def h_status(request: web.Request):
    user = request_user(request)
    return web.json_response({
        "setup_required": not has_users(),
        "authed": user is not None,
        "username": None if user is None else user,
        "instance_name": config.get("instance_name"),
    })


async def h_setup(request: web.Request):
    if has_users():
        return web.json_response({"error": "already set up"}, status=400)
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or len(password) < 6:
        return web.json_response({"error": "username required, password min 6 chars"}, status=400)
    create_user(username, password)
    log.info("initial admin user %r created", username)
    token = issue_session(username)
    resp = web.json_response({"ok": True, "username": username})
    resp.set_cookie(COOKIE_NAME, token, max_age=SESSION_TTL, httponly=True, samesite="Strict", path="/")
    return resp


async def h_login(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid login request"}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get("username", ""), str) or \
            not isinstance(body.get("password", ""), str):
        return web.json_response({"error": "invalid login request"}, status=400)
    username = body.get("username", "").strip()
    password = body.get("password", "")
    # Forwarded headers are caller-controlled unless an explicitly trusted
    # proxy rewrites them.  request.remote is the authenticated transport peer;
    # proxy deployments safely share one failure bucket rather than allowing a
    # public client to mint unlimited identities and bypass the limiter.
    peer = str(request.remote or "?")[:128]
    if _rate_limited(peer):
        return web.json_response({"error": "too many attempts, wait a few minutes"}, status=429)
    if not check_login(username, password):
        log.warning("failed login for %r from %s", username, peer)
        return web.json_response({"error": "invalid credentials"}, status=403)
    token = issue_session(username)
    _attempts.pop(peer, None)
    log.info("login %r from %s", username, peer)
    resp = web.json_response({"ok": True, "username": username})
    resp.set_cookie(COOKIE_NAME, token, max_age=SESSION_TTL, httponly=True, samesite="Strict", path="/")
    return resp


async def h_logout(request: web.Request):
    drop_session(request.cookies.get(COOKIE_NAME, ""))
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE_NAME, path="/")
    return resp


async def h_change_password(request: web.Request):
    user = request.get("user")
    if user in (None, "@token"):
        return web.json_response({"error": "cookie session required"}, status=403)
    body = await request.json()
    old = body.get("old") or ""
    new = body.get("new") or ""
    if len(new) < 6:
        return web.json_response({"error": "new password min 6 chars"}, status=400)
    if not check_login(user, old):
        return web.json_response({"error": "current password incorrect"}, status=403)
    db.execute("UPDATE users SET pwhash=? WHERE username=?", (hash_password(new), user))
    db.execute("DELETE FROM web_sessions WHERE username=?", (user,))
    token = issue_session(user)
    resp = web.json_response({"ok": True})
    resp.set_cookie(COOKIE_NAME, token, max_age=SESSION_TTL, httponly=True, samesite="Strict", path="/")
    return resp


def register(app: web.Application) -> None:
    app.router.add_get("/api/auth/status", h_status)
    app.router.add_post("/api/auth/setup", h_setup)
    app.router.add_post("/api/auth/login", h_login)
    app.router.add_post("/api/auth/logout", h_logout)
    app.router.add_post("/api/auth/password", h_change_password)
