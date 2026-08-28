"""Managed headless browser surface (independent named Chromiums per node).

Puppy owns the whole lifecycle of a user-supplied Chromium/Chrome binary and
drives it exclusively over Chromium's ``--remote-debugging-pipe``: CDP JSON
messages, NUL-terminated, on inherited file descriptors 3/4. No DevTools TCP
port ever exists, no display server is involved, and nothing else on the host
can reach the browser. The WebUI receives the screen as screencast JPEG frames
and sends input over the same authenticated websocket surface the terminal
uses; a controller reaches a remote node's browser through the existing
generic backend proxy, so the browser itself never binds a socket.

Enablement is node-owned config (``browser.enabled``), toggled locally or -
for a headless backend - through its authenticated API. Enabling requires the
availability probe to pass: a binary on PATH (or ``PUPPY_BROWSER_BIN``) whose
``--version`` parses and is recent enough for reliable headless streaming.
Installing/maintaining the binary is deliberately the host's job.

``--no-sandbox`` is added exactly when this process runs as root, where
Chromium refuses to start otherwise; unprivileged deployments keep the
sandbox. Each four-character browser ID owns a separate profile below
``data/browser/instances/`` (0700). Like the engines' native credential/session
stores this state is deliberately outside snapshot coverage: node-local render
and login state, never required for restore.
"""
from __future__ import annotations

import asyncio
import base64
import collections
import fcntl
import json
import logging
import math
import os
import re
import secrets
import shutil
import signal
import stat
import string
import time

from aiohttp import WSMsgType, web

from puppy import config

log = logging.getLogger("puppy.browser")

BINARY_CANDIDATES = ("chromium", "chromium-browser", "google-chrome",
                     "google-chrome-stable", "chrome", "chrome-headless-shell")
# First major with the fully rendering "new" headless mode our screencast needs.
MIN_MAJOR = 112
PROBE_TTL_SECONDS = 300
VERSION_TIMEOUT = 12.0
START_TIMEOUT = 20.0
CALL_TIMEOUT = 10.0
IDLE_STOP_SECONDS = 900        # no viewers this long -> browser exits
# Chromium needs a usable size before the first viewer arrives. Once one does,
# its visible pane replaces this default with a bounded, same-aspect viewport.
DEFAULT_VIEWPORT_W, DEFAULT_VIEWPORT_H = 1280, 800
MIN_VIEWPORT_W, MIN_VIEWPORT_H = 160, 120
MAX_VIEWPORT_W, MAX_VIEWPORT_H = 3840, 2160
SCREENCAST_QUALITY = 70
MAX_CDP_BUFFER = 32 * 1024 * 1024
MAX_TEXT_BACKLOG = 64          # queued small messages per viewer
MAX_URL_LENGTH = 4096
MAX_INSERT_TEXT = 8192
MAX_AX_NODES = 400
MAX_AX_TEXT = 48 * 1024
COLOR_SCHEMES = ("dark", "light")
BROWSER_ID_RE = re.compile(r"^[A-Z0-9]{4}$")
BROWSER_ID_ALPHABET = string.ascii_uppercase + string.digits
ID_RETENTION_SECONDS = 30 * 24 * 60 * 60
CATALOG_VERSION = 1

_probe_cache = None            # (monotonic ts, dict)
_manager = None


class BrowserError(RuntimeError):
    pass


def invalidate_probe() -> None:
    global _probe_cache
    _probe_cache = None


def _browser_root() -> str:
    path = os.path.join(config.DATA_DIR, "browser")
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _instances_root() -> str:
    path = os.path.join(_browser_root(), "instances")
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def normalize_browser_id(value) -> str:
    browser_id = str(value or "").strip().upper()
    if not BROWSER_ID_RE.fullmatch(browser_id):
        raise BrowserError("browser ID must be four A-Z/0-9 characters")
    return browser_id


def _instance_root(browser_id: str) -> str:
    browser_id = normalize_browser_id(browser_id)
    return os.path.join(_instances_root(), browser_id)


def _catalog_path() -> str:
    return os.path.join(_browser_root(), "instances.json")


def enabled() -> bool:
    return bool(config.get("browser.enabled", False))


def normalize_color_scheme(value) -> str:
    """Anything unrecognised reads as dark: this is a rendering hint, and a
    stale or malformed one must never keep a browser from starting."""
    text = str(value or "").strip().lower()
    return text if text in COLOR_SCHEMES else "dark"


def color_scheme() -> str:
    return normalize_color_scheme(config.get("browser.color_scheme", "dark"))


def sandbox_mode() -> str:
    return "no-sandbox" if os.geteuid() == 0 else "sandboxed"


async def _run_version(binary: str) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=VERSION_TIMEOUT)
        return out.decode(errors="replace").strip().splitlines()[0] if out else ""
    except Exception as exc:
        log.warning("browser version probe failed for %s: %s", binary, exc)
        return ""


async def probe(force: bool = False) -> dict:
    """Availability check: binary present, runnable, and recent enough."""
    global _probe_cache
    now = time.monotonic()
    if not force and _probe_cache and now - _probe_cache[0] < PROBE_TTL_SECONDS:
        return dict(_probe_cache[1])
    result = {"available": False, "reason": "", "binary": "", "product": "", "major": 0}
    override = os.environ.get("PUPPY_BROWSER_BIN", "")
    binary = ""
    if override:
        # An explicit override wins and fails closed rather than falling back.
        if os.path.isfile(override) and os.access(override, os.X_OK):
            binary = os.path.abspath(override)
        else:
            result["reason"] = "PUPPY_BROWSER_BIN is not an executable file"
    else:
        for name in BINARY_CANDIDATES:
            found = shutil.which(name)
            if found:
                binary = found
                break
        if not binary:
            result["reason"] = "no Chromium or Chrome binary found on this node's PATH"
    if binary:
        result["binary"] = binary
        product = await _run_version(binary)
        match = re.search(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?", product or "")
        if not product:
            result["reason"] = "the browser binary did not report a version"
        elif not match:
            result["reason"] = "could not parse a browser version from: " + product[:80]
        else:
            result["product"] = product
            result["major"] = int(match.group(1))
            if result["major"] < MIN_MAJOR:
                result["reason"] = (
                    "browser {} is too old for reliable headless streaming "
                    "(needs {}+)".format(result["major"], MIN_MAJOR))
            else:
                result["available"] = True
    _probe_cache = (now, dict(result))
    return result


async def status_payload() -> dict:
    st = await probe()
    m = manager()
    return {
        "supported": True,
        "enabled": enabled(),
        "available": st["available"],
        "reason": st["reason"],
        "binary": st["binary"],
        "product": st["product"],
        "sandbox": sandbox_mode(),
        "color_scheme": color_scheme(),
        "running": bool(m and m.running),
        "viewers": m.viewer_count() if m else 0,
        "instances": m.instance_payloads() if m else [],
    }


def ping_payload() -> dict:
    """Cheap additive /api/ping metadata; never probes or spawns anything."""
    return {"enabled": enabled()}


async def set_enabled(value: bool) -> dict:
    if value:
        st = await probe()
        if not st["available"]:
            raise BrowserError(st["reason"] or "no usable browser on this node")
    config.set_value("browser.enabled", bool(value))
    if not value and _manager is not None:
        await _manager.stop("browser disabled")
    return await status_payload()


async def set_color_scheme(value) -> str:
    """The WebUI theme is browser-local state, so viewers tell the node which
    one they are using. Persisting it means the agent's own screenshots and any
    browser opened with nobody watching still match the user's UI."""
    scheme = normalize_color_scheme(value)
    if scheme != color_scheme():
        config.set_value("browser.color_scheme", scheme)
    if _manager is not None:
        for instance in list(_manager.instances.values()):
            try:
                await instance.apply_color_scheme()
            except BrowserError:
                pass   # a browser that is stopping simply picks it up next start
    return scheme


async def apply_config() -> None:
    """Reconcile live browsers after a snapshot's config/database replacement."""
    if _manager is not None:
        await _manager.clear_session_bindings()
    if not enabled() and _manager is not None:
        await _manager.stop("browser disabled by restored configuration")
        return
    # a restore can carry a different theme; browsers still running follow it
    await set_color_scheme(color_scheme())


async def shutdown() -> None:
    if _manager is not None:
        await _manager.stop("Puppy is shutting down")


def manager() -> "BrowserRegistry":
    global _manager
    if _manager is None:
        _manager = BrowserRegistry()
    return _manager


# ---- spawn helpers ----

def _dup_high(fd: int) -> int:
    """Move an fd to >=16 so a dup2 file action never targets its own number
    (dup2 with equal fds would keep close-on-exec and the fd would vanish on
    exec - the child must genuinely receive 0..4)."""
    high = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 16)
    os.close(fd)
    return high


# The blank tab a fresh instance opens on reads as a broken browser. This card
# is painted into that same about:blank document (no navigation, no history
# entry, no network), so the address bar stays empty and the screen says which
# browser this is and that it is ready.
START_PAGE_TITLE = "Browser {} is ready"
# Both palettes ship in the page and the emulated prefers-color-scheme picks
# one, so the card restyles itself the instant a viewer syncs its theme - no
# reload, and no second document to keep in step.
START_PAGE_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light"><title>{title}</title>
<style>
 :root{{
  --bg-top:#171a21; --bg-bottom:#0b0c0f; --txt:#e7eaf0; --id:#ffffff;
  --acc:#5aa2f5; --acc-bg:rgba(90,162,245,.10); --acc-line:rgba(90,162,245,.28);
  --ok:#22e5a4; --txt2:#aab2c0; --txt3:#8b93a3;
  --kbd-bg:rgba(255,255,255,.06); --kbd-line:rgba(255,255,255,.12); --kbd-txt:#c2c9d6;
 }}
 @media (prefers-color-scheme:light){{
  :root{{
   --bg-top:#ffffff; --bg-bottom:#eef1f7; --txt:#1b2434; --id:#101828;
   --acc:#3b82e0; --acc-bg:rgba(59,130,224,.10); --acc-line:rgba(59,130,224,.30);
   --ok:#0b9e71; --txt2:#48536b; --txt3:#8a94a8;
   --kbd-bg:rgba(15,23,42,.05); --kbd-line:rgba(15,23,42,.14); --kbd-txt:#48536b;
  }}
 }}
 html,body{{height:100%;margin:0}}
 body{{
  display:flex;align-items:center;justify-content:center;
  background:radial-gradient(120% 90% at 50% 0%,var(--bg-top) 0%,var(--bg-bottom) 62%);
  color:var(--txt);font:400 15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased;
 }}
 .card{{text-align:center;padding:0 28px;max-width:520px}}
 .mark{{
  display:inline-flex;align-items:center;justify-content:center;
  width:52px;height:52px;margin-bottom:22px;border-radius:16px;
  background:var(--acc-bg);border:1px solid var(--acc-line);color:var(--acc);
 }}
 /* The WebUI's own sans, not a code face: this is a name to read and say
    back, not a snippet. The trailing letter-space is pulled back in so wide
    tracking does not shove the centred word off axis. */
 .id{{
  font:800 42px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,
   "Helvetica Neue",Arial,sans-serif;
  letter-spacing:.2em;text-indent:.2em;margin:0 0 12px;color:var(--id);
 }}
 .ready{{margin:0 0 22px;font-size:14px;color:var(--txt2);letter-spacing:.02em}}
 .ready b{{font-weight:600;color:var(--ok)}}
 .hint{{margin:0;font-size:12.5px;line-height:1.7;color:var(--txt3)}}
 .hint kbd{{
  font:11.5px/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  padding:2px 6px;border-radius:5px;background:var(--kbd-bg);
  border:1px solid var(--kbd-line);color:var(--kbd-txt);
 }}
</style></head><body>
<div class="card">
 <div class="mark"><svg viewBox="0 0 16 16" width="26" height="26" fill="none"
  stroke="currentColor" stroke-width="1.2" aria-hidden="true">
  <circle cx="8" cy="8" r="6.25"/><path d="M8 1.75c-1.9 1.7-2.85 3.85-2.85 6.25S6.1 12.55 8 14.25"/>
  <path d="M8 1.75c1.9 1.7 2.85 3.85 2.85 6.25S9.9 12.55 8 14.25"/>
  <path d="M2.2 5.9h11.6M2.2 10.1h11.6"/></svg></div>
 <p class="id">{browser_id}</p>
 <p class="ready"><b>Ready.</b> This browser is empty and waiting.</p>
 <p class="hint">Type an address in the bar above and press <kbd>Enter</kbd>,<br>
  or ask the agent to open a page in Browser {browser_id}.</p>
</div>
</body></html>"""


def start_page_html(browser_id: str) -> str:
    return START_PAGE_HTML.format(title=START_PAGE_TITLE.format(browser_id),
                                  browser_id=browser_id)


def launch_argv(binary: str, profile_dir: str, as_root: bool) -> list:
    argv = [
        binary,
        "--headless=new",
        "--remote-debugging-pipe",
        "--no-first-run",
        "--no-default-browser-check",
        "--hide-crash-restore-bubble",
        "--disable-background-networking",
        "--disable-component-update",
        "--mute-audio",
        "--window-size={},{}".format(DEFAULT_VIEWPORT_W, DEFAULT_VIEWPORT_H),
        "--user-data-dir=" + profile_dir,
    ]
    if as_root:
        argv.append("--no-sandbox")   # Chromium refuses a sandboxed root
    argv.append("about:blank")
    return argv


def _kill_stale_instance(profile_dir: str, pidfile: str) -> None:
    """A hard-killed Puppy can orphan the browser (it runs in its own process
    group). The pidfile plus a strict cmdline check scoped to our own profile
    directory lets the next start reclaim it without ever touching a stranger."""
    try:
        pid = int(open(pidfile, "r", encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return
    try:
        with open("/proc/{}/cmdline".format(pid), "rb") as f:
            cmdline = f.read().decode("utf-8", "replace")
    except OSError:
        return
    if ("--user-data-dir=" + profile_dir) not in cmdline:
        return
    log.warning("reclaiming stale managed browser pid=%s", pid)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        time.sleep(0.4)
    try:
        os.unlink(pidfile)
    except OSError:
        pass


async def _reap_group(pid: int) -> None:
    """INT-then-KILL the browser's own process group, then collect the child."""
    for sig, wait in ((signal.SIGINT, 1.5), (signal.SIGTERM, 1.5), (signal.SIGKILL, 3.0)):
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done == pid:
                return
        except ChildProcessError:
            return
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return
        await asyncio.sleep(wait)
    try:
        await asyncio.get_event_loop().run_in_executor(None, os.waitpid, pid, 0)
    except Exception:
        pass


class _ReadProtocol(asyncio.Protocol):
    """NUL-delimited CDP JSON stream from the browser's fd 4."""

    def __init__(self, owner):
        self.owner = owner
        self.buffer = bytearray()

    def data_received(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > MAX_CDP_BUFFER:
            log.error("browser CDP stream exceeded %s bytes; dropping it", MAX_CDP_BUFFER)
            self.owner._on_pipe_lost()
            return
        while True:
            cut = self.buffer.find(b"\0")
            if cut < 0:
                return
            raw = bytes(self.buffer[:cut])
            del self.buffer[:cut + 1]
            try:
                message = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            if isinstance(message, dict):
                self.owner._on_message(message)

    def connection_lost(self, exc):
        self.owner._on_pipe_lost()


class _Viewer:
    """One websocket watcher. A single sender task per socket keeps writes
    serialized; frames collapse to the newest under backpressure while small
    JSON messages stay ordered and bounded."""

    def __init__(self, ws):
        self.ws = ws
        self.frame = None
        self.texts = collections.deque()
        self.wake = asyncio.Event()
        self.closed = False
        self.task = asyncio.ensure_future(self._run())

    def send_json(self, payload: dict) -> None:
        if len(self.texts) < MAX_TEXT_BACKLOG:
            self.texts.append(json.dumps(payload))
        self.wake.set()

    def send_frame(self, data: bytes) -> None:
        self.frame = data
        self.wake.set()

    async def _run(self):
        try:
            while not self.closed:
                await self.wake.wait()
                self.wake.clear()
                while self.texts:
                    await self.ws.send_str(self.texts.popleft())
                frame, self.frame = self.frame, None
                if frame is not None:
                    await self.ws.send_bytes(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def close(self):
        self.closed = True
        self.task.cancel()


# key names that need a virtual key code because they carry no text
_VIRTUAL_KEYS = {
    "Enter": 13, "Backspace": 8, "Tab": 9, "Escape": 27, "Delete": 46,
    "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40,
    "Home": 36, "End": 35, "PageUp": 33, "PageDown": 34, "Insert": 45,
    "Shift": 16, "Control": 17, "Alt": 18, "Meta": 91, "ContextMenu": 93,
    "F5": 116,
}
_MOUSE_BUTTONS = ("none", "left", "middle", "right")
_MOUSE_KINDS = {"down": "mousePressed", "up": "mouseReleased", "move": "mouseMoved"}


def _normalize_url(text: str) -> str:
    value = str(text or "").strip()[:MAX_URL_LENGTH]
    if not value:
        return ""
    if value.startswith(("about:", "data:")) or \
            re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", value):
        return value
    host = value.split("/", 1)[0].split(":", 1)[0].lower()
    bare_host = " " not in value and (
        "." in host or host == "localhost" or re.match(r"^\d+(\.\d+){3}$", host))
    if not bare_host:
        from urllib.parse import quote
        return "https://duckduckgo.com/?q=" + quote(value)
    # LAN-ish names rarely serve TLS; public names get it by default.
    plain = (host == "localhost" or re.match(r"^\d+(\.\d+){3}$", host) or
             host.endswith((".lan", ".local", ".home", ".internal")))
    return ("http://" if plain else "https://") + value


def _normalize_viewport(width, height):
    """Validate a viewer's CSS-pixel pane size and bound rendering cost.

    Transient hidden/zero-sized panes are ignored rather than collapsing the
    page. Oversized panes are reduced as a pair so their aspect ratio (and the
    input mapping that depends on it) remains intact.
    """
    if isinstance(width, bool) or isinstance(height, bool):
        return None
    try:
        width = float(width)
        height = float(height)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(width) or not math.isfinite(height):
        return None
    width = int(round(width))
    height = int(round(height))
    if width < MIN_VIEWPORT_W or height < MIN_VIEWPORT_H:
        return None
    scale = min(1.0, MAX_VIEWPORT_W / width, MAX_VIEWPORT_H / height)
    width = int(round(width * scale))
    height = int(round(height * scale))
    if width < MIN_VIEWPORT_W or height < MIN_VIEWPORT_H:
        return None
    return {"width": width, "height": height}


class Manager:
    """One isolated managed browser instance and its viewers."""

    def __init__(self, browser_id: str, origin: str = "user", owner_session=None):
        self.browser_id = normalize_browser_id(browser_id)
        self.origin = origin if origin in ("agent", "user", "legacy") else "user"
        self.owner_session = int(owner_session) if owner_session is not None else None
        self.root = _instance_root(self.browser_id)
        os.makedirs(self.root, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        self.lock = asyncio.Lock()
        self.attach_lock = asyncio.Lock()
        self.viewport_lock = asyncio.Lock()
        self.agent_lock = asyncio.Lock()
        self.closed = False
        self.running = False
        self.stopping = False
        self.pid = None
        self.write_transport = None
        self.read_transport = None
        self.next_id = 0
        self.pending = {}
        self.viewers = {}            # ws -> _Viewer
        self.targets = {}            # targetId -> targetInfo
        self.page_target = ""
        self.page_session = ""
        self.screencasting = False
        self.started_at = 0.0
        self.viewport = {"width": DEFAULT_VIEWPORT_W, "height": DEFAULT_VIEWPORT_H}
        self.frame_meta = dict(self.viewport)
        self.nav = {"url": "about:blank", "title": "", "can_back": False,
                    "can_forward": False}
        self.agent_refs = {}
        self.idle_task = None

    def viewer_count(self) -> int:
        return len(self.viewers)

    def _subdir(self, name: str) -> str:
        path = os.path.join(self.root, name)
        os.makedirs(path, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
        return path

    @property
    def pidfile(self) -> str:
        return os.path.join(self.root, "chrome.pid")

    # ---- lifecycle ----

    async def ensure_started(self) -> None:
        async with self.lock:
            if self.closed:
                raise BrowserError("Browser {} is closed".format(self.browser_id))
            if not enabled():
                raise BrowserError("the browser is disabled on this node")
            if self.running:
                return
            st = await probe()
            if not enabled():
                raise BrowserError("the browser is disabled on this node")
            if not st["available"]:
                raise BrowserError(st["reason"] or "no usable browser on this node")
            profile = self._subdir("profile")
            home = self._subdir("home")
            downloads = self._subdir("downloads")
            _kill_stale_instance(profile, self.pidfile)
            argv = launch_argv(st["binary"], profile, os.geteuid() == 0)
            env = dict(os.environ)
            env["HOME"] = home
            env.pop("DISPLAY", None)
            env.pop("WAYLAND_DISPLAY", None)

            cmd_read, cmd_write = os.pipe()   # us -> browser (its fd 3)
            out_read, out_write = os.pipe()   # browser -> us (its fd 4)
            cmd_read = _dup_high(cmd_read)
            out_write = _dup_high(out_write)
            devnull = _dup_high(os.open(os.devnull, os.O_RDWR))
            log_path = os.path.join(self.root, "chrome.log")
            log_fd = _dup_high(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
            spawned = None
            spawn_error = None
            try:
                spawned = os.posix_spawn(st["binary"], argv, env, file_actions=[
                    (os.POSIX_SPAWN_DUP2, devnull, 0),
                    (os.POSIX_SPAWN_DUP2, log_fd, 1),
                    (os.POSIX_SPAWN_DUP2, log_fd, 2),
                    (os.POSIX_SPAWN_DUP2, cmd_read, 3),
                    (os.POSIX_SPAWN_DUP2, out_write, 4),
                ], setpgroup=0)
            except OSError as exc:
                spawn_error = exc
            finally:
                for fd in (cmd_read, out_write, devnull, log_fd):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if spawned is None:
                    for fd in (cmd_write, out_read):
                        try:
                            os.close(fd)
                        except OSError:
                            pass
            if spawn_error is not None:
                raise BrowserError("could not start the browser: {}".format(spawn_error))
            try:
                with open(self.pidfile, "w", encoding="utf-8") as f:
                    f.write(str(spawned))
                os.chmod(self.pidfile, 0o600)
            except OSError:
                pass

            loop = asyncio.get_event_loop()
            self.pid = spawned
            self.stopping = False
            self.targets = {}
            self.page_target = ""
            self.page_session = ""
            self.screencasting = False
            self.viewport = {"width": DEFAULT_VIEWPORT_W, "height": DEFAULT_VIEWPORT_H}
            self.frame_meta = dict(self.viewport)
            self.nav = {"url": "about:blank", "title": "", "can_back": False,
                        "can_forward": False}
            self.agent_refs = {}
            try:
                self.read_transport, _ = await loop.connect_read_pipe(
                    lambda: _ReadProtocol(self), os.fdopen(out_read, "rb", buffering=0))
                self.write_transport, _ = await loop.connect_write_pipe(
                    asyncio.Protocol, os.fdopen(cmd_write, "wb", buffering=0))
                self.running = True
                version = await self.call("Browser.getVersion", timeout=START_TIMEOUT)
                await self.call("Target.setDiscoverTargets", {"discover": True})
                try:
                    await self.call("Browser.setDownloadBehavior", {
                        "behavior": "allow", "downloadPath": downloads})
                except BrowserError:
                    pass   # best effort; downloads just land in the profile
                await self._attach_page("")
                await self._show_start_page()
                self.started_at = time.time()
                log.info("managed Browser %s started pid=%s %s%s", self.browser_id, spawned,
                         version.get("product", ""),
                         " (no sandbox: running as root)" if os.geteuid() == 0 else "")
            except Exception as exc:
                log.error("managed Browser %s failed to start: %s", self.browser_id, exc)
                await self._teardown()
                await _reap_group(spawned)
                tail = ""
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        tail = f.read()[-400:].strip()
                except OSError:
                    pass
                raise BrowserError("the browser failed to start" +
                                   (": " + tail.splitlines()[-1] if tail else ""))

    async def stop(self, reason: str) -> None:
        async with self.lock:
            if not self.running and self.pid is None:
                return
            log.info("stopping managed Browser %s pid=%s (%s)",
                     self.browser_id, self.pid, reason)
            self.stopping = True
            pid = self.pid
            if self.running:
                try:
                    await self.call("Browser.close", timeout=3.0)
                except Exception:
                    pass
            self._broadcast_json({"type": "gone", "reason": reason})
            await self._teardown()
            if pid is not None:
                await _reap_group(pid)
            self.stopping = False

    async def _teardown(self) -> None:
        self.running = False
        self._cancel_idle()
        for fut in list(self.pending.values()):
            if not fut.done():
                fut.set_exception(BrowserError("browser exited"))
        self.pending.clear()
        for transport in (self.write_transport, self.read_transport):
            if transport is not None:
                try:
                    transport.close()
                except Exception:
                    pass
        self.write_transport = None
        self.read_transport = None
        self.page_target = ""
        self.page_session = ""
        self.screencasting = False
        self.agent_refs = {}
        self.pid = None
        try:
            os.unlink(self.pidfile)
        except OSError:
            pass

    def _on_pipe_lost(self) -> None:
        if self.stopping or not self.running:
            return
        log.warning("managed Browser %s pid=%s exited unexpectedly",
                    self.browser_id, self.pid)
        self.running = False
        pid, self.pid = self.pid, None
        for fut in list(self.pending.values()):
            if not fut.done():
                fut.set_exception(BrowserError("browser exited"))
        self.pending.clear()
        self._broadcast_json({"type": "gone", "reason": "the browser exited"})
        if pid is not None:
            asyncio.ensure_future(_reap_group(pid))
        asyncio.ensure_future(self._teardown())

    # ---- CDP plumbing ----

    def _send_raw(self, message: dict) -> None:
        if self.write_transport is None:
            raise BrowserError("browser is not running")
        self.write_transport.write(json.dumps(message).encode("utf-8") + b"\0")

    def _fire(self, method: str, params=None, session: str = "") -> None:
        """Send without waiting (input events; unknown-id replies are ignored)."""
        if not self.running:
            return
        self.next_id += 1
        message = {"id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        if session:
            message["sessionId"] = session
        try:
            self._send_raw(message)
        except BrowserError:
            pass

    async def call(self, method: str, params=None, session: str = "",
                   timeout: float = CALL_TIMEOUT) -> dict:
        if not self.running:
            raise BrowserError("browser is not running")
        self.next_id += 1
        mid = self.next_id
        fut = asyncio.get_event_loop().create_future()
        self.pending[mid] = fut
        message = {"id": mid, "method": method}
        if params is not None:
            message["params"] = params
        if session:
            message["sessionId"] = session
        self._send_raw(message)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise BrowserError("browser did not answer {} in time".format(method))
        finally:
            self.pending.pop(mid, None)

    def _on_message(self, message: dict) -> None:
        mid = message.get("id")
        if mid is not None:
            fut = self.pending.pop(mid, None)
            if fut is not None and not fut.done():
                if "error" in message:
                    err = message["error"] or {}
                    fut.set_exception(BrowserError(
                        str(err.get("message") or "browser call failed")))
                else:
                    fut.set_result(message.get("result") or {})
            return
        method = message.get("method") or ""
        params = message.get("params") or {}
        if method == "Page.screencastFrame":
            if message.get("sessionId") != self.page_session:
                return
            self._fire("Page.screencastFrameAck",
                       {"sessionId": params.get("sessionId", 0)},
                       session=self.page_session)
            metadata = params.get("metadata") or {}
            width = int(metadata.get("deviceWidth") or 0)
            height = int(metadata.get("deviceHeight") or 0)
            if width > 0 and height > 0 and \
                    (width != self.frame_meta["width"] or height != self.frame_meta["height"]):
                self.frame_meta = {"width": width, "height": height}
                self._broadcast_json({"type": "frame_meta", **self.frame_meta})
            try:
                frame = base64.b64decode(params.get("data") or "")
            except Exception:
                return
            for viewer in list(self.viewers.values()):
                viewer.send_frame(frame)
        elif method == "Target.targetCreated":
            info = params.get("targetInfo") or {}
            tid = info.get("targetId")
            if not tid:
                return
            known = tid in self.targets
            self.targets[tid] = info
            # Follow real popups (window.open / target=_blank) so OAuth-style
            # flows stay visible; the discovery burst at startup is not one.
            if not known and info.get("type") == "page" and info.get("openerId") \
                    and self.page_target and tid != self.page_target:
                asyncio.ensure_future(self._attach_page(tid))
        elif method == "Target.targetInfoChanged":
            info = params.get("targetInfo") or {}
            tid = info.get("targetId")
            if not tid:
                return
            self.targets[tid] = info
            if tid == self.page_target:
                changed = (self.nav["url"] != info.get("url", "") or
                           self.nav["title"] != info.get("title", ""))
                self.nav["url"] = info.get("url", self.nav["url"])
                self.nav["title"] = info.get("title", self.nav["title"])
                if changed:
                    self.agent_refs = {}
                    asyncio.ensure_future(self._refresh_nav())
        elif method == "Target.targetDestroyed":
            tid = params.get("targetId")
            self.targets.pop(tid, None)
            if tid == self.page_target:
                self.page_target = ""
                self.page_session = ""
                self.screencasting = False
                self.agent_refs = {}
                asyncio.ensure_future(self._attach_page(""))
        elif method == "Page.javascriptDialogOpening":
            if message.get("sessionId") != self.page_session:
                return
            kind = params.get("type") or "dialog"
            accept = kind in ("alert", "beforeunload")
            self._fire("Page.handleJavaScriptDialog", {"accept": accept},
                       session=self.page_session)
            self._broadcast_json({
                "type": "dialog", "kind": kind,
                "message": str(params.get("message") or "")[:400],
                "action": "accepted" if accept else "dismissed",
            })

    async def _attach_page(self, target_id: str) -> None:
        """Attach (or re-attach) the streamed page; creates one if none exist."""
        async with self.attach_lock:
            if not self.running:
                return
            previous_session = self.page_session
            if not target_id:
                pages = [tid for tid, info in self.targets.items()
                         if info.get("type") == "page"]
                if not pages:
                    try:
                        listed = await self.call("Target.getTargets")
                    except BrowserError:
                        return
                    for info in listed.get("targetInfos") or []:
                        if isinstance(info, dict) and info.get("targetId"):
                            self.targets[info["targetId"]] = info
                    pages = [tid for tid, info in self.targets.items()
                             if info.get("type") == "page"]
                if pages:
                    target_id = pages[-1]
                else:
                    created = await self.call("Target.createTarget", {"url": "about:blank"})
                    target_id = created.get("targetId") or ""
            if not target_id:
                return
            attached = await self.call("Target.attachToTarget",
                                       {"targetId": target_id, "flatten": True})
            session = attached.get("sessionId") or ""
            if not session:
                return
            self.page_target = target_id
            self.page_session = session
            await self.call("Page.enable", session=session)
            await self.call("DOM.enable", session=session)
            try:
                async with self.viewport_lock:
                    await self._apply_viewport(session, self.viewport)
            except BrowserError:
                pass   # keep streaming even if an unusual Chromium rejects emulation
            try:
                await self.apply_color_scheme()
            except BrowserError:
                pass   # a rendering hint is never worth failing an attach over
            try:
                await self.call("Accessibility.enable", session=session)
            except BrowserError:
                pass
            self.agent_refs = {}
            if previous_session and previous_session != session:
                self._fire("Target.detachFromTarget", {"sessionId": previous_session})
            info = self.targets.get(target_id) or {}
            self.nav["url"] = info.get("url", "about:blank")
            self.nav["title"] = info.get("title", "")
            if self.viewers:
                await self._start_screencast()
                await self._send_fresh_frame()
            await self._refresh_nav()

    async def apply_color_scheme(self) -> None:
        """Emulation is per attached page, so this is re-applied on every
        attach as well as when the viewer's theme changes. It emulates the
        standard media feature only - never Chromium's force-dark filter,
        which repaints sites that deliberately have no dark mode."""
        if not self.page_session or not self.running:
            return
        await self.call("Emulation.setEmulatedMedia", {
            "features": [{"name": "prefers-color-scheme", "value": color_scheme()}],
        }, session=self.page_session)

    async def _apply_viewport(self, session: str, size: dict) -> None:
        await self.call("Emulation.setDeviceMetricsOverride", {
            "width": size["width"], "height": size["height"],
            "deviceScaleFactor": 1, "mobile": False,
            "screenWidth": size["width"], "screenHeight": size["height"],
        }, session=session)

    async def resize_viewport(self, size: dict) -> None:
        """Make the page's real CSS viewport follow the latest viewer pane."""
        async with self.viewport_lock:
            if size == self.viewport:
                return
            session = self.page_session
            if not session or not self.running:
                return
            try:
                await self._apply_viewport(session, size)
            except BrowserError as exc:
                log.debug("Browser %s viewport resize skipped: %s", self.browser_id, exc)
                return
            self.viewport = dict(size)
            # A damage frame normally follows the emulation change. Update the
            # mapping immediately too, so input never remains on the old size.
            self.frame_meta = dict(size)
        self._broadcast_json({"type": "frame_meta", **self.frame_meta})
        await self._send_fresh_frame()

    async def _show_start_page(self) -> None:
        """Paint the identifying ready card into the launch tab's blank
        document. Cosmetic only: a failure here never fails a launch."""
        if not self.page_session or (self.nav.get("url") or "about:blank") != "about:blank":
            return
        try:
            tree = await self.call("Page.getFrameTree", session=self.page_session)
            frame_id = ((tree.get("frameTree") or {}).get("frame") or {}).get("id") or ""
            if not frame_id:
                return
            await self.call("Page.setDocumentContent",
                            {"frameId": frame_id, "html": start_page_html(self.browser_id)},
                            session=self.page_session)
        except BrowserError as exc:
            log.debug("Browser %s start page skipped: %s", self.browser_id, exc)
            return
        self.nav["title"] = START_PAGE_TITLE.format(self.browser_id)
        await self._refresh_nav()
        if self.viewers:
            await self._send_fresh_frame()

    async def _start_screencast(self) -> None:
        if self.screencasting or not self.page_session:
            return
        await self.call("Page.startScreencast", {
            "format": "jpeg", "quality": SCREENCAST_QUALITY,
            "maxWidth": MAX_VIEWPORT_W, "maxHeight": MAX_VIEWPORT_H,
        }, session=self.page_session)
        self.screencasting = True

    async def _stop_screencast(self) -> None:
        if not self.screencasting:
            return
        self.screencasting = False
        if self.page_session and self.running:
            try:
                await self.call("Page.stopScreencast", session=self.page_session)
            except BrowserError:
                pass

    async def _send_fresh_frame(self, viewer=None) -> None:
        """Screencast frames only arrive on damage; a still page would leave a
        newcomer staring at nothing, so push one explicit screenshot."""
        if not self.page_session or not self.running:
            return
        try:
            shot = await self.call("Page.captureScreenshot",
                                   {"format": "jpeg", "quality": SCREENCAST_QUALITY},
                                   session=self.page_session)
            frame = base64.b64decode(shot.get("data") or "")
        except Exception:
            return
        if not frame:
            return
        targets = [viewer] if viewer is not None else list(self.viewers.values())
        for item in targets:
            item.send_frame(frame)

    async def _refresh_nav(self) -> None:
        if not self.page_session or not self.running:
            return
        try:
            history = await self.call("Page.getNavigationHistory", session=self.page_session)
            index = int(history.get("currentIndex") or 0)
            entries = history.get("entries") or []
            self.nav["can_back"] = index > 0
            self.nav["can_forward"] = index + 1 < len(entries)
        except BrowserError:
            pass
        self._broadcast_json({"type": "status", "running": True, **self.nav})

    def _broadcast_json(self, payload: dict) -> None:
        for viewer in list(self.viewers.values()):
            viewer.send_json(payload)

    # ---- viewers ----

    def _cancel_idle(self) -> None:
        if self.idle_task is not None:
            self.idle_task.cancel()
            self.idle_task = None

    def _arm_idle(self) -> None:
        self._cancel_idle()

        async def later():
            try:
                await asyncio.sleep(IDLE_STOP_SECONDS)
            except asyncio.CancelledError:
                return
            if not self.viewers:
                await self.stop("no viewers for {} minutes".format(IDLE_STOP_SECONDS // 60))

        self.idle_task = asyncio.ensure_future(later())

    async def attach_viewer(self, ws) -> None:
        await self.ensure_started()
        self._cancel_idle()
        viewer = _Viewer(ws)
        self.viewers[ws] = viewer
        viewer.send_json({"type": "status", "running": True, **self.nav})
        viewer.send_json({"type": "frame_meta", **self.frame_meta})
        try:
            await self._start_screencast()
        except BrowserError:
            pass
        await self._send_fresh_frame(viewer)

    def detach_viewer(self, ws) -> None:
        viewer = self.viewers.pop(ws, None)
        if viewer is not None:
            viewer.close()
        if not self.viewers and self.running:
            asyncio.ensure_future(self._stop_screencast())
            self._arm_idle()

    # ---- input from viewers ----

    async def handle_client(self, data: dict) -> None:
        kind = data.get("type")
        if kind == "mouse":
            self._dispatch_mouse(data)
        elif kind == "wheel":
            self._dispatch_wheel(data)
        elif kind == "key":
            self._dispatch_key(data)
        elif kind == "insert_text":
            text = str(data.get("text") or "")[:MAX_INSERT_TEXT]
            if text:
                self._fire("Input.insertText", {"text": text}, session=self.page_session)
        elif kind == "navigate":
            url = _normalize_url(data.get("url"))
            if url and self.page_session:
                await self.call("Page.navigate", {"url": url}, session=self.page_session)
        elif kind in ("back", "forward"):
            await self._history_step(1 if kind == "forward" else -1)
        elif kind == "reload":
            if self.page_session:
                self._fire("Page.reload", session=self.page_session)
        elif kind == "viewport":
            size = _normalize_viewport(data.get("width"), data.get("height"))
            if size is not None:
                await self.resize_viewport(size)
        elif kind == "color_scheme":
            await set_color_scheme(data.get("value"))

    def _point(self, data: dict) -> tuple:
        nx = min(1.0, max(0.0, float(data.get("nx") or 0.0)))
        ny = min(1.0, max(0.0, float(data.get("ny") or 0.0)))
        return (round(nx * self.viewport["width"], 2),
                round(ny * self.viewport["height"], 2))

    @staticmethod
    def _modifiers(data: dict) -> int:
        try:
            return int(data.get("modifiers") or 0) & 15
        except (TypeError, ValueError):
            return 0

    def _dispatch_mouse(self, data: dict) -> None:
        if not self.page_session:
            return
        event_type = _MOUSE_KINDS.get(data.get("kind"))
        if event_type is None:
            return
        button = data.get("button")
        if button not in _MOUSE_BUTTONS:
            button = "left" if event_type != "mouseMoved" else "none"
        try:
            clicks = max(0, min(3, int(data.get("clickCount") or 0)))
        except (TypeError, ValueError):
            clicks = 0
        x, y = self._point(data)
        self._fire("Input.dispatchMouseEvent", {
            "type": event_type, "x": x, "y": y, "button": button,
            "clickCount": clicks, "modifiers": self._modifiers(data),
        }, session=self.page_session)

    def _dispatch_wheel(self, data: dict) -> None:
        if not self.page_session:
            return
        x, y = self._point(data)
        try:
            dx = max(-2000.0, min(2000.0, float(data.get("dx") or 0.0)))
            dy = max(-2000.0, min(2000.0, float(data.get("dy") or 0.0)))
        except (TypeError, ValueError):
            return
        self._fire("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": x, "y": y, "deltaX": dx, "deltaY": dy,
            "modifiers": self._modifiers(data),
        }, session=self.page_session)

    @staticmethod
    def _key_payload(data: dict):
        kind = data.get("kind")
        if kind not in ("down", "up"):
            return None
        key = str(data.get("key") or "")[:32]
        code = str(data.get("code") or "")[:32]
        text = str(data.get("text") or "")[:8]
        modifiers = Manager._modifiers(data)
        payload = {"modifiers": modifiers, "key": key, "code": code}
        if data.get("repeat") is True:
            payload["autoRepeat"] = True
        if kind == "up":
            payload["type"] = "keyUp"
        elif key == "Enter":
            payload.update({"type": "keyDown", "text": "\r", "unmodifiedText": "\r"})
        elif text and (modifiers & ~8) == 0:    # plain or shifted printable
            payload.update({"type": "keyDown", "text": text, "unmodifiedText": text})
        else:
            payload["type"] = "rawKeyDown"
        vk = _VIRTUAL_KEYS.get(key)
        if vk is None and len(key) == 1:
            vk = ord(key.upper()) if key.upper().isalnum() and ord(key) < 128 else None
        if vk is not None:
            payload["windowsVirtualKeyCode"] = vk
            payload["nativeVirtualKeyCode"] = vk
        if payload["type"] == "rawKeyDown" and vk is None and not key:
            return None
        return payload

    def _dispatch_key(self, data: dict) -> None:
        if not self.page_session:
            return
        payload = self._key_payload(data)
        if payload is None:
            return
        self._fire("Input.dispatchKeyEvent", payload, session=self.page_session)

    async def _history_step(self, direction: int) -> None:
        if not self.page_session:
            return
        history = await self.call("Page.getNavigationHistory", session=self.page_session)
        index = int(history.get("currentIndex") or 0) + direction
        entries = history.get("entries") or []
        if 0 <= index < len(entries) and isinstance(entries[index], dict):
            entry_id = entries[index].get("id")
            if entry_id is not None:
                await self.call("Page.navigateToHistoryEntry", {"entryId": entry_id},
                                session=self.page_session)

    # ---- high-level input from the per-turn agent bridge ----

    async def agent_command(self, method: str, params: dict) -> dict:
        """Run one bounded browser operation for the private stdio MCP bridge.

        The bridge intentionally cannot issue arbitrary CDP.  Serializing its
        calls keeps accessibility refs stable within one action while still
        allowing the person watching the Browser tab to interact normally.
        """
        async with self.agent_lock:
            await self.ensure_started()
            self._cancel_idle()
            if not self.page_session:
                raise BrowserError("the browser has no page to control")
            try:
                if method == "snapshot":
                    return await self._agent_snapshot(
                        params.get("include_screenshot") is True)
                if method == "screenshot":
                    return await self._agent_screenshot()
                if method == "navigate":
                    return await self._agent_navigate(params)
                if method == "click":
                    return await self._agent_click(params)
                if method == "type":
                    return await self._agent_type(params)
                if method == "press":
                    return await self._agent_press(params)
                if method == "scroll":
                    return await self._agent_scroll(params)
                if method == "back":
                    self.agent_refs = {}
                    await self._history_step(-1)
                    await asyncio.sleep(0.35)
                    await self._refresh_nav()
                    await self._agent_refresh_identity()
                    return {"text": self._agent_page_text("Went back.")}
                if method == "reload":
                    self.agent_refs = {}
                    await self.call("Page.reload", session=self.page_session)
                    await asyncio.sleep(0.35)
                    await self._refresh_nav()
                    await self._agent_refresh_identity()
                    return {"text": self._agent_page_text("Reloaded the page.")}
                if method == "wait":
                    delay = self._bounded_number(params.get("milliseconds", 1000),
                                                 0, 10000, "milliseconds")
                    await asyncio.sleep(delay / 1000.0)
                    await self._refresh_nav()
                    await self._agent_refresh_identity()
                    return {"text": self._agent_page_text(
                        "Waited {} ms.".format(int(delay)))}
                raise BrowserError("unknown browser operation")
            finally:
                # A Browser tab normally attaches as soon as the first-use
                # event reaches the WebUI. With no connected console, retain
                # the same 15-minute agent-only idle lifecycle as a closed tab.
                if self.running and not self.viewers:
                    self._arm_idle()

    @staticmethod
    def _bounded_number(value, low: float, high: float, label: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise BrowserError("{} must be a number".format(label))
        if not math.isfinite(number) or number < low or number > high:
            raise BrowserError("{} must be between {} and {}".format(label, low, high))
        return number

    @staticmethod
    def _ax_value(value) -> str:
        if isinstance(value, dict):
            value = value.get("value")
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()[:300]

    def _agent_page_text(self, lead: str) -> str:
        title = self.nav.get("title") or "(untitled)"
        url = self.nav.get("url") or "about:blank"
        return "{}\nPage: {}\nURL: {}".format(lead, title, url)

    async def _agent_refresh_identity(self) -> None:
        """Refresh title/URL without accepting an arbitrary script from tools."""
        try:
            evaluated = await self.call("Runtime.evaluate", {
                "expression": "JSON.stringify({url:location.href,title:document.title})",
                "returnByValue": True,
            }, session=self.page_session)
            value = (evaluated.get("result") or {}).get("value")
            identity = json.loads(value) if isinstance(value, str) else {}
            if isinstance(identity, dict):
                url = str(identity.get("url") or "")[:MAX_URL_LENGTH]
                title = str(identity.get("title") or "")[:500]
                if url:
                    self.nav["url"] = url
                self.nav["title"] = title
        except (BrowserError, TypeError, ValueError):
            pass

    async def _agent_snapshot(self, include_screenshot: bool) -> dict:
        await self._agent_refresh_identity()
        result = await self.call("Accessibility.getFullAXTree", session=self.page_session)
        raw_nodes = result.get("nodes") or []
        nodes = [node for node in raw_nodes[:MAX_AX_NODES]
                 if isinstance(node, dict) and node.get("nodeId") is not None]
        by_id = {str(node["nodeId"]): node for node in nodes}
        children = set()
        for node in nodes:
            children.update(str(item) for item in (node.get("childIds") or []))
        roots = [node for node in nodes if str(node["nodeId"]) not in children]
        if not roots and nodes:
            roots = [nodes[0]]

        interactive_roles = {
            "button", "checkbox", "combobox", "gridcell", "link", "listbox",
            "menuitem", "menuitemcheckbox", "menuitemradio", "option", "radio",
            "searchbox", "slider", "spinbutton", "switch", "tab", "textbox",
            "treeitem",
        }
        semantic_roles = {
            "alert", "article", "dialog", "heading", "img", "list", "listitem",
            "main", "navigation", "region", "row", "statictext", "table",
        }
        self.agent_refs = {}
        lines = [
            "Page: {}".format(self.nav.get("title") or "(untitled)"),
            "URL: {}".format(self.nav.get("url") or "about:blank"),
            "Viewport: {}x{}".format(self.viewport["width"], self.viewport["height"]),
            "Accessibility snapshot:",
        ]
        seen = set()
        shown = 0

        def walk(node, depth):
            nonlocal shown
            node_id = str(node.get("nodeId"))
            if node_id in seen or shown >= MAX_AX_NODES:
                return
            seen.add(node_id)
            role = self._ax_value(node.get("role")) or "generic"
            role_key = role.lower()
            name = self._ax_value(node.get("name"))
            value = self._ax_value(node.get("value"))
            props = {}
            for prop in node.get("properties") or []:
                if isinstance(prop, dict) and prop.get("name"):
                    props[str(prop["name"])] = prop.get("value")
            focusable = self._ax_value(props.get("focusable")).lower() == "true"
            interactive = role_key in interactive_roles or (
                focusable and role_key not in ("document", "rootwebarea", "webarea"))
            backend_id = node.get("backendDOMNodeId")
            ref = ""
            if interactive and backend_id is not None:
                ref = "b{}".format(len(self.agent_refs) + 1)
                self.agent_refs[ref] = backend_id
            meaningful = interactive or role_key in semantic_roles or (
                bool(name or value) and role_key not in ("inlinetextbox", "none"))
            ignored = node.get("ignored") is True
            if meaningful and not ignored:
                parts = []
                if ref:
                    parts.append("[{}]".format(ref))
                parts.append(role)
                if name:
                    parts.append(json.dumps(name, ensure_ascii=False))
                if value and value != name:
                    parts.append("value=" + json.dumps(value, ensure_ascii=False))
                for flag in ("disabled", "expanded", "selected", "checked", "required"):
                    flag_value = self._ax_value(props.get(flag)).lower()
                    if flag_value and flag_value not in ("false", "undefined"):
                        parts.append("{}={}".format(flag, flag_value))
                line = "  " * min(depth, 12) + "- " + " ".join(parts)
                if sum(len(item) + 1 for item in lines) + len(line) <= MAX_AX_TEXT:
                    lines.append(line)
                    shown += 1
                else:
                    return
            child_depth = depth if ignored or not meaningful else depth + 1
            for child_id in node.get("childIds") or []:
                child = by_id.get(str(child_id))
                if child is not None:
                    walk(child, child_depth)

        for root in roots:
            walk(root, 0)
        if len(raw_nodes) > len(nodes) or shown >= MAX_AX_NODES:
            lines.append("… snapshot truncated; narrow the page or inspect again after acting")
        if shown == 0:
            lines.append("- No named accessible elements were reported.")
        payload = {"text": "\n".join(lines)}
        if include_screenshot:
            payload.update(await self._agent_screenshot(include_text=False))
        return payload

    async def _agent_screenshot(self, include_text: bool = True) -> dict:
        if include_text:
            await self._agent_refresh_identity()
        shot = await self.call("Page.captureScreenshot", {
            "format": "jpeg", "quality": SCREENCAST_QUALITY,
            "fromSurface": True, "captureBeyondViewport": False,
        }, session=self.page_session)
        data = str(shot.get("data") or "")
        if not data:
            raise BrowserError("the browser returned an empty screenshot")
        payload = {"image": {"data": data, "mime_type": "image/jpeg"}}
        if include_text:
            payload["text"] = self._agent_page_text(
                "Captured the {}x{} viewport.".format(
                    self.viewport["width"], self.viewport["height"]))
        return payload

    async def _agent_navigate(self, params: dict) -> dict:
        url = _normalize_url(params.get("url"))
        if not url:
            raise BrowserError("url must not be empty")
        wait_ms = self._bounded_number(params.get("wait_ms", 500),
                                       0, 10000, "wait_ms")
        self.agent_refs = {}
        await self.call("Page.navigate", {"url": url}, session=self.page_session)
        if wait_ms:
            await asyncio.sleep(wait_ms / 1000.0)
        await self._refresh_nav()
        await self._agent_refresh_identity()
        return {"text": self._agent_page_text("Navigated to {}.".format(url))}

    async def _agent_point(self, params: dict) -> tuple:
        ref = str(params.get("ref") or "").strip().lower()
        if ref:
            backend_id = self.agent_refs.get(ref)
            if backend_id is None:
                raise BrowserError(
                    "unknown or stale element ref {}; take a fresh snapshot".format(ref))
            try:
                await self.call("DOM.scrollIntoViewIfNeeded", {
                    "backendNodeId": backend_id,
                }, session=self.page_session)
                box = await self.call("DOM.getBoxModel", {
                    "backendNodeId": backend_id,
                }, session=self.page_session)
            except BrowserError:
                raise BrowserError(
                    "element {} is no longer available; take a fresh snapshot".format(ref))
            model = box.get("model") or {}
            quad = model.get("content") or model.get("border") or []
            if len(quad) < 8:
                raise BrowserError("element {} has no clickable box".format(ref))
            return (round(sum(float(value) for value in quad[0::2]) / 4, 2),
                    round(sum(float(value) for value in quad[1::2]) / 4, 2))
        if "x" not in params or "y" not in params:
            raise BrowserError("click requires an element ref or both x and y")
        x = self._bounded_number(params.get("x"), 0, self.viewport["width"], "x")
        y = self._bounded_number(params.get("y"), 0, self.viewport["height"], "y")
        return x, y

    async def _agent_click(self, params: dict) -> dict:
        x, y = await self._agent_point(params)
        button = str(params.get("button") or "left")
        if button not in _MOUSE_BUTTONS[1:]:
            raise BrowserError("button must be left, middle, or right")
        clicks = int(self._bounded_number(params.get("click_count", 1),
                                          1, 3, "click_count"))
        base = {"x": x, "y": y, "button": button, "clickCount": clicks,
                "modifiers": 0}
        await self.call("Input.dispatchMouseEvent", {"type": "mousePressed", **base},
                        session=self.page_session)
        await self.call("Input.dispatchMouseEvent", {"type": "mouseReleased", **base},
                        session=self.page_session)
        return {"text": self._agent_page_text(
            "Clicked at ({}, {}).".format(x, y))}

    async def _agent_key_call(self, kind: str, key: str, modifiers: int = 0,
                              text: str = "") -> None:
        payload = self._key_payload({"kind": kind, "key": key,
                                     "modifiers": modifiers, "text": text})
        if payload is None:
            raise BrowserError("invalid key event")
        await self.call("Input.dispatchKeyEvent", payload, session=self.page_session)

    async def _agent_type(self, params: dict) -> dict:
        ref = str(params.get("ref") or "").strip().lower()
        backend_id = self.agent_refs.get(ref)
        if backend_id is None:
            raise BrowserError(
                "unknown or stale element ref {}; take a fresh snapshot".format(ref or "(empty)"))
        text = str(params.get("text") or "")
        if len(text) > MAX_INSERT_TEXT:
            raise BrowserError("text is too long (maximum {} characters)".format(MAX_INSERT_TEXT))
        try:
            await self.call("DOM.focus", {"backendNodeId": backend_id},
                            session=self.page_session)
        except BrowserError:
            raise BrowserError(
                "element {} is no longer focusable; take a fresh snapshot".format(ref))
        if params.get("clear") is True:
            await self._agent_key_call("down", "a", modifiers=2)
            await self._agent_key_call("up", "a", modifiers=2)
            await self._agent_key_call("down", "Backspace")
            await self._agent_key_call("up", "Backspace")
        if text:
            await self.call("Input.insertText", {"text": text},
                            session=self.page_session)
        return {"text": self._agent_page_text(
            "Inserted {} character{} into {}.".format(
                len(text), "" if len(text) == 1 else "s", ref))}

    async def _agent_press(self, params: dict) -> dict:
        key = str(params.get("key") or "")[:32]
        if not key:
            raise BrowserError("key must not be empty")
        requested = params.get("modifiers") or []
        if not isinstance(requested, list):
            raise BrowserError("modifiers must be a list")
        bits = {"Alt": 1, "Control": 2, "Meta": 4, "Shift": 8}
        if any(item not in bits for item in requested):
            raise BrowserError("unknown key modifier")
        modifiers = sum(bits[item] for item in set(requested))
        await self._agent_key_call("down", key, modifiers=modifiers)
        await self._agent_key_call("up", key, modifiers=modifiers)
        label = "+".join(list(requested) + [key])
        return {"text": self._agent_page_text("Pressed {}.".format(label))}

    async def _agent_scroll(self, params: dict) -> dict:
        dx = self._bounded_number(params.get("delta_x", 0), -2000, 2000, "delta_x")
        dy = self._bounded_number(params.get("delta_y"), -2000, 2000, "delta_y")
        await self.call("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": self.viewport["width"] / 2,
            "y": self.viewport["height"] / 2,
            "deltaX": dx, "deltaY": dy, "modifiers": 0,
        }, session=self.page_session)
        return {"text": self._agent_page_text(
            "Scrolled by ({}, {}).".format(dx, dy))}


def _safe_remove_instance_storage(browser_id: str) -> None:
    """Remove one retired instance directory, never an arbitrary path."""
    root = _instance_root(browser_id)
    parent = os.path.realpath(_instances_root())
    if os.path.realpath(os.path.dirname(root)) != parent:
        raise BrowserError("refusing to remove browser storage outside its namespace")
    try:
        info = os.lstat(root)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or \
            info.st_uid != os.geteuid():
        raise BrowserError("refusing to remove unowned browser instance storage")
    profile = os.path.join(root, "profile")
    pidfile = os.path.join(root, "chrome.pid")
    _kill_stale_instance(profile, pidfile)
    shutil.rmtree(root)


def _write_catalog(records: dict, bindings: dict) -> None:
    path = _catalog_path()
    payload = {
        "version": CATALOG_VERSION,
        "ids": records,
        "bindings": {str(key): value for key, value in bindings.items()},
    }
    temporary = path + ".tmp-" + secrets.token_hex(6)
    fd = None
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _load_catalog() -> tuple:
    path = _catalog_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        raw = {}
    except Exception as exc:
        log.warning("could not read managed browser ID history: %s", exc)
        raw = {}
    raw_ids = raw.get("ids") if isinstance(raw, dict) else {}
    records = {}
    changed = not isinstance(raw_ids, dict)
    now = time.time()
    cutoff = now - ID_RETENTION_SECONDS
    for browser_id, record in (raw_ids.items() if isinstance(raw_ids, dict) else []):
        if not isinstance(browser_id, str) or not BROWSER_ID_RE.fullmatch(browser_id) or \
                not isinstance(record, dict):
            changed = True
            continue
        try:
            created_at = float(record.get("created_at"))
            closed_raw = record.get("closed_at")
            closed_at = None if closed_raw is None else float(closed_raw)
        except (TypeError, ValueError):
            changed = True
            continue
        if not math.isfinite(created_at) or created_at <= 0 or \
                (closed_at is not None and (not math.isfinite(closed_at) or closed_at <= 0)):
            changed = True
            continue
        if closed_at is not None and closed_at < cutoff:
            changed = True
            try:
                _safe_remove_instance_storage(browser_id)
            except Exception as exc:
                log.warning("could not prune Browser %s storage: %s", browser_id, exc)
            continue
        origin = record.get("origin")
        if origin not in ("agent", "user", "legacy"):
            origin = "user"
            changed = True
        owner = record.get("owner_session")
        try:
            owner = int(owner) if owner is not None else None
        except (TypeError, ValueError):
            owner = None
            changed = True
        if owner is not None and owner <= 0:
            owner = None
            changed = True
        records[browser_id] = {
            "created_at": created_at,
            "closed_at": closed_at,
            "origin": origin,
            "owner_session": owner,
        }
    raw_bindings = raw.get("bindings") if isinstance(raw, dict) else {}
    bindings = {}
    if isinstance(raw_bindings, dict):
        for raw_session, browser_id in raw_bindings.items():
            try:
                session_id = int(raw_session)
            except (TypeError, ValueError):
                changed = True
                continue
            if session_id > 0 and browser_id in records and \
                    records[browser_id]["closed_at"] is None:
                bindings[session_id] = browser_id
            else:
                changed = True
    elif raw_bindings is not None:
        changed = True
    if changed or not os.path.exists(path):
        _write_catalog(records, bindings)
    return records, bindings


class BrowserRegistry:
    """Node-owned catalog and lifecycle for independent browser instances."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.records, self.bindings = _load_catalog()
        self.instances = {}
        for browser_id, record in self.records.items():
            if record["closed_at"] is None:
                self.instances[browser_id] = Manager(
                    browser_id, record["origin"], record["owner_session"])
        self._reclaim_unknown_stale_instances()

    @property
    def running(self) -> bool:
        return any(instance.running for instance in self.instances.values())

    def viewer_count(self) -> int:
        return sum(instance.viewer_count() for instance in self.instances.values())

    def instance_payloads(self) -> list:
        payloads = []
        ordered = sorted(self.instances.items(),
                         key=lambda item: self.records[item[0]]["created_at"])
        for browser_id, instance in ordered:
            payloads.append({
                "id": browser_id,
                "origin": instance.origin,
                "running": instance.running,
                "viewers": instance.viewer_count(),
                "created_at": self.records[browser_id]["created_at"],
            })
        return payloads

    def _reclaim_unknown_stale_instances(self) -> None:
        try:
            names = os.listdir(_instances_root())
        except OSError:
            names = []
        for name in names:
            if not BROWSER_ID_RE.fullmatch(name):
                continue
            root = os.path.join(_instances_root(), name)
            try:
                info = os.lstat(root)
            except OSError:
                continue
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or \
                    info.st_uid != os.geteuid():
                continue
            _kill_stale_instance(os.path.join(root, "profile"),
                                 os.path.join(root, "chrome.pid"))
        # Reclaim the pre-instance layout after an ungraceful upgrade too.
        _kill_stale_instance(os.path.join(_browser_root(), "profile"),
                             os.path.join(_browser_root(), "chrome.pid"))

    def _prune(self) -> None:
        cutoff = time.time() - ID_RETENTION_SECONDS
        removed = []
        new_records = dict(self.records)
        for browser_id, record in self.records.items():
            closed_at = record.get("closed_at")
            if closed_at is not None and closed_at < cutoff:
                removed.append(browser_id)
                del new_records[browser_id]
        if not removed:
            return
        for browser_id in removed:
            try:
                _safe_remove_instance_storage(browser_id)
            except Exception as exc:
                log.warning("could not prune Browser %s storage: %s", browser_id, exc)
        _write_catalog(new_records, self.bindings)
        self.records = new_records

    def _new_id(self) -> str:
        self._prune()
        for _ in range(4096):
            browser_id = "".join(secrets.choice(BROWSER_ID_ALPHABET) for _ in range(4))
            # "A-Z0-9 mix" is literal: every generated ID contains both kinds.
            if not any(char.isalpha() for char in browser_id) or \
                    not any(char.isdigit() for char in browser_id):
                continue
            if browser_id not in self.records and \
                    not os.path.lexists(_instance_root(browser_id)):
                return browser_id
        raise BrowserError("could not allocate a unique browser ID")

    @staticmethod
    def _normalize_owner(owner_session):
        if owner_session is None:
            return None
        try:
            owner_session = int(owner_session)
        except (TypeError, ValueError):
            raise BrowserError("invalid browser session identity")
        if owner_session <= 0:
            raise BrowserError("invalid browser session identity")
        return owner_session

    def _create_locked(self, origin: str, owner_session=None) -> Manager:
        """Register one instance while ``self.lock`` is held."""
        if not enabled():
            raise BrowserError("the browser is disabled on this node")
        browser_id = self._new_id()
        record = {
            "created_at": time.time(), "closed_at": None,
            "origin": origin, "owner_session": owner_session,
        }
        instance = Manager(browser_id, origin, owner_session)
        new_records = dict(self.records)
        new_records[browser_id] = record
        new_bindings = dict(self.bindings)
        if owner_session is not None:
            new_bindings[owner_session] = browser_id
        _write_catalog(new_records, new_bindings)
        self.records = new_records
        self.bindings = new_bindings
        self.instances[browser_id] = instance
        return instance

    async def _start_created(self, instance: Manager) -> Manager:
        try:
            await instance.ensure_started()
        except Exception:
            await self.close(instance.browser_id, "browser launch failed")
            raise
        return instance

    async def create(self, origin: str = "user", owner_session=None) -> Manager:
        if not enabled():
            raise BrowserError("the browser is disabled on this node")
        if origin not in ("agent", "user", "legacy"):
            raise BrowserError("invalid browser origin")
        owner_session = self._normalize_owner(owner_session)
        async with self.lock:
            instance = self._create_locked(origin, owner_session)
        return await self._start_created(instance)

    def get(self, browser_id) -> Manager:
        browser_id = normalize_browser_id(browser_id)
        instance = self.instances.get(browser_id)
        if instance is None or instance.closed:
            raise BrowserError("Browser {} is closed or unknown".format(browser_id))
        return instance

    async def close(self, browser_id, reason: str = "closed by user") -> bool:
        browser_id = normalize_browser_id(browser_id)
        async with self.lock:
            instance = self.instances.get(browser_id)
            record = self.records.get(browser_id)
            if instance is None or record is None or record.get("closed_at") is not None:
                return False
            new_records = dict(self.records)
            new_records[browser_id] = {**record, "closed_at": time.time()}
            new_bindings = {session_id: selected for session_id, selected in
                            self.bindings.items() if selected != browser_id}
            _write_catalog(new_records, new_bindings)
            instance.closed = True
            self.instances.pop(browser_id, None)
            self.records = new_records
            self.bindings = new_bindings
        await instance.stop(reason)
        return True

    async def stop(self, reason: str) -> None:
        instances = list(self.instances.values())
        if instances:
            await asyncio.gather(*(instance.stop(reason) for instance in instances),
                                 return_exceptions=True)

    async def clear_session_bindings(self) -> None:
        """Do not let restored database IDs inherit pre-restore browser state."""
        async with self.lock:
            new_records = {browser_id: dict(record)
                           for browser_id, record in self.records.items()}
            for browser_id, record in new_records.items():
                if record.get("closed_at") is None and record.get("owner_session") is not None:
                    record["owner_session"] = None
            _write_catalog(new_records, {})
            self.records = new_records
            self.bindings = {}
            for instance in self.instances.values():
                instance.owner_session = None

    async def agent_browser(self, session_id: int, requested_id=None,
                            fresh: bool = False) -> Manager:
        session_id = self._normalize_owner(session_id)
        if fresh:
            return await self.create("agent", session_id)
        explicit = requested_id is not None and str(requested_id).strip() != ""
        if explicit:
            browser_id = normalize_browser_id(requested_id)
            async with self.lock:
                instance = self.instances.get(browser_id)
                if instance is None or instance.closed:
                    raise BrowserError(
                        "Browser {} is closed or unknown".format(browser_id))
                if self.bindings.get(session_id) != browser_id:
                    new_bindings = dict(self.bindings)
                    new_bindings[session_id] = browser_id
                    _write_catalog(self.records, new_bindings)
                    self.bindings = new_bindings
            await instance.ensure_started()
            return instance

        created = False
        async with self.lock:
            browser_id = self.bindings.get(session_id)
            instance = self.instances.get(browser_id) if browser_id else None
            if instance is None or instance.closed:
                instance = self._create_locked("agent", session_id)
                created = True
        if created:
            return await self._start_created(instance)
        try:
            await instance.ensure_started()
        except BrowserError:
            if instance.closed:
                return await self.create("agent", session_id)
            raise
        return instance

    async def legacy_browser(self) -> Manager:
        created = False
        async with self.lock:
            instance = next((candidate for candidate in self.instances.values()
                             if candidate.origin == "legacy" and not candidate.closed), None)
            if instance is None:
                instance = self._create_locked("legacy")
                created = True
        if created:
            return await self._start_created(instance)
        await instance.ensure_started()
        return instance


# ---- HTTP + websocket surface (registered by register_execution_api) ----

async def h_status(request: web.Request):
    return web.json_response(await status_payload())


async def h_enabled(request: web.Request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid browser request"}, status=400)
    if not isinstance(body, dict) or type(body.get("enabled")) is not bool:
        return web.json_response({"error": "browser enabled must be on or off"}, status=400)
    try:
        payload = await set_enabled(body["enabled"])
    except BrowserError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    log.info("managed browser %s on this node",
             "enabled" if body["enabled"] else "disabled")
    try:
        from puppy import runner
        runner.broadcast_update({"type": "browser", **ping_payload()})
    except Exception:
        pass
    return web.json_response({"ok": True, **payload})


async def h_create(request: web.Request):
    if request.app.get("puppy_snapshot_busy"):
        return web.json_response({"error": "Puppy backup or restore in progress"}, status=503)
    try:
        instance = await manager().create("user")
    except BrowserError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    return web.json_response({
        "ok": True,
        "browser": {"id": instance.browser_id, "origin": instance.origin},
    }, status=201)


async def h_close(request: web.Request):
    if request.app.get("puppy_snapshot_busy"):
        return web.json_response({"error": "Puppy backup or restore in progress"}, status=503)
    try:
        browser_id = normalize_browser_id(request.match_info.get("browser_id"))
        closed = await manager().close(browser_id)
    except BrowserError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not closed:
        return web.json_response({"error": "Browser {} is closed or unknown".format(
            browser_id)}, status=404)
    return web.json_response({"ok": True, "id": browser_id})


async def ws_browser(request: web.Request):
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 20)
    await ws.prepare(request)
    if request.app.get("puppy_snapshot_busy"):
        await ws.close(code=1013, message=b"Puppy backup or restore in progress")
        return ws
    if not enabled():
        try:
            await ws.send_json({"type": "error",
                                "text": "the browser is disabled on this node"})
        except Exception:
            pass
        await ws.close()
        return ws
    try:
        requested_id = request.match_info.get("browser_id")
        m = manager().get(requested_id) if requested_id else \
            await manager().legacy_browser()
        await m.attach_viewer(ws)
    except BrowserError as exc:
        try:
            await ws.send_json({"type": "error", "text": str(exc)})
        except Exception:
            pass
        await ws.close()
        return ws
    log.info("Browser %s viewer attached (%s total) for %s",
             m.browser_id, m.viewer_count(), request.remote)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
                continue
            try:
                data = json.loads(msg.data)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            try:
                await m.handle_client(data)
            except BrowserError as exc:
                try:
                    await ws.send_json({"type": "error", "text": str(exc)})
                except Exception:
                    break
            except Exception:
                log.exception("browser input handling failed")
    finally:
        m.detach_viewer(ws)
        log.info("Browser %s viewer detached (%s left)",
                 m.browser_id, m.viewer_count())
    return ws


def register(app: web.Application) -> None:
    app.router.add_get("/api/browser/status", h_status)
    app.router.add_post("/api/browser/enabled", h_enabled)
    app.router.add_post("/api/browser/instances", h_create)
    app.router.add_delete(
        "/api/browser/instances/{browser_id:[A-Z0-9]{4}}", h_close)
    app.router.add_get(
        "/api/ws/browser/{browser_id:[A-Z0-9]{4}}", ws_browser)
    app.router.add_get("/api/ws/browser", ws_browser)

    async def on_startup(_app):
        manager()  # load ID history and reclaim any browser left by a hard stop
        from puppy import browser_agent
        await browser_agent.start(_app)

    async def on_cleanup(_app):
        from puppy import browser_agent
        await browser_agent.stop(_app)
        await shutdown()

    app.on_startup.append(on_startup)
    # Cleanup runs after the runtime's shutdown hook has allowed live engine
    # turns to finish, so their per-turn browser tools remain valid during the
    # normal graceful-stop window.
    app.on_cleanup.append(on_cleanup)
