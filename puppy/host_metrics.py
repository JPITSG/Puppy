"""Whole-host CPU telemetry, disk space and the process tree Puppy itself is running.

The footer's CPU reading is one live sample; opening it asks the node for that
reading's recent history, the memory/load facts beside it, the free space on
the filesystems its work lives on, and the processes descending from this
Puppy process. Everything comes from ``/proc`` and ``statvfs`` alone - no
new dependency and no shelling out - and nothing is persisted: this is
replaceable telemetry a restart is entitled to forget, like the frame counters
beside a browser.
"""
from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from collections import deque
from typing import Optional, Tuple

from aiohttp import web

from puppy import browser, config, db, runner
from puppy.drivers import engine_keys


FIRST_SAMPLE_SECONDS = 1.0
SAMPLE_INTERVAL_SECONDS = 3.0
# Half an hour of samples: enough to read a finished turn's shape, small
# enough (a float pair each) that no console pays for keeping it.
HISTORY_SAMPLES = 600
DEFAULT_WINDOW_SECONDS = 900.0
MAX_WINDOW_SECONDS = 3600.0
# Two consoles watching one node must not double its /proc reads.
PROCESS_CACHE_SECONDS = 1.0
# Trimming: a tree nobody can read is worse than one that says it was trimmed.
MAX_NODES = 160
MAX_DEPTH = 8
MAX_CHILDREN = 20
# Identical leaf siblings (a browser's renderers) fold into one counted row.
GROUP_SIBLINGS_AT = 3
CMD_CHARS = 160
_STATE_KEY = "puppy_host_metrics"
# Disk space is asked of every filesystem this node's work lives on: Puppy's
# own program and data, and each session's project. A filesystem can stop
# answering (a network mount whose server went away) and a stat of it then
# blocks for as long as the kernel likes, so each mount is asked in a daemon
# thread of its own under one short deadline. One that misses it is reported
# as not answering, its thread left to finish by itself, and it is not asked
# again while that thread is still out - the panel never waits on it.
DISK_TIMEOUT_SECONDS = 1.0
DISK_CACHE_SECONDS = 10.0
DISK_MAX_PATHS = 400
DISK_MAX_STUCK = 16
DISK_SAMPLE_PATHS = 6
MOUNTINFO_LIMIT = 4 * 1024 * 1024

CpuTimes = Tuple[int, int]  # (all accounted CPU time, idle CPU time)

_SHELLS = ("sh", "bash", "dash", "zsh", "ash", "fish", "ksh")
_INTERPRETERS = ("python", "python2", "python3", "node", "nodejs", "bun",
                 "deno", "ruby", "perl", "env", "npx", "sh", "bash", "zsh")
# Two consecutive scans give one process its CPU share; the cache is pruned to
# the live set on every scan so a finished process cannot leak. Scans run in
# the executor, and two of them interleaving would read back each other's
# fresh timestamps as "no time has passed", so one lock owns both.
_process_samples = {}
_process_cache = {"at": 0.0, "payload": None, "key": None}
_process_lock = threading.Lock()
_disk_lock = threading.Lock()
_disk_cache = {"at": 0.0, "payload": None, "key": None}
_disk_probes = {}   # the mount a probe set out for -> that probe, while it is out


def _parse_cpu_stat(contents: str) -> Optional[CpuTimes]:
    """Parse Linux's aggregate /proc/stat CPU counters.

    guest and guest_nice are already included in user and nice, respectively,
    so only the first eight counters are included in the total.
    """
    for raw_line in contents.splitlines():
        fields = raw_line.split()
        if not fields or fields[0] != "cpu":
            continue
        if len(fields) < 5:
            return None
        try:
            values = [int(value) for value in fields[1:]]
        except ValueError:
            return None
        if any(value < 0 for value in values):
            return None
        accounted = values[:8]
        total = sum(accounted)
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        if total <= 0 or idle > total:
            return None
        return total, idle
    return None


def _read_cpu_times(path: str = "/proc/stat") -> Optional[CpuTimes]:
    try:
        with open(path, "r", encoding="ascii") as stream:
            return _parse_cpu_stat(stream.read())
    except (OSError, UnicodeError):
        return None


def _cpu_percent(previous: CpuTimes, current: CpuTimes) -> Optional[float]:
    total_delta = current[0] - previous[0]
    idle_delta = current[1] - previous[1]
    if total_delta <= 0 or idle_delta < 0 or idle_delta > total_delta:
        return None
    return max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta))


def latest(app) -> Optional[dict]:
    state = app.get(_STATE_KEY)
    payload = state.get("latest") if isinstance(state, dict) else None
    return dict(payload) if isinstance(payload, dict) else None


def history(app, window_seconds: float = DEFAULT_WINDOW_SECONDS,
            now: Optional[float] = None) -> list:
    """The samples inside the window, oldest first, as [timestamp, percent]."""
    state = app.get(_STATE_KEY)
    samples = state.get("history") if isinstance(state, dict) else None
    if not samples:
        return []
    cutoff = (time.time() if now is None else now) - max(0.0, window_seconds)
    return [[round(at, 3), value] for at, value in samples if at >= cutoff]


def _record_sample(app, at: float, value: Optional[float]) -> None:
    state = app.get(_STATE_KEY)
    if not isinstance(state, dict) or value is None:
        return
    state["history"].append((at, value))


# ---------------------------------------------------------------- host facts

def _sysconf(name: str, fallback: int) -> int:
    try:
        value = int(os.sysconf(name))
    except (OSError, ValueError, AttributeError):
        return fallback
    return value if value > 0 else fallback


def _clock_ticks() -> int:
    return _sysconf("SC_CLK_TCK", 100)


def _page_size() -> int:
    return _sysconf("SC_PAGESIZE", 4096)


def _cpu_count() -> int:
    return max(1, os.cpu_count() or 1)


def _load_average() -> Optional[list]:
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except (OSError, AttributeError):
        return None


def _host_uptime(proc: str = "/proc") -> Optional[float]:
    try:
        with open(os.path.join(proc, "uptime"), "r", encoding="ascii") as stream:
            return max(0.0, float(stream.read().split()[0]))
    except (OSError, ValueError, IndexError, UnicodeError):
        return None


def _memory(proc: str = "/proc") -> Optional[dict]:
    wanted = {"MemTotal": 0, "MemAvailable": 0}
    try:
        with open(os.path.join(proc, "meminfo"), "r", encoding="ascii") as stream:
            for line in stream:
                name, _, rest = line.partition(":")
                if name in wanted:
                    wanted[name] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError, UnicodeError):
        return None
    total, available = wanted["MemTotal"], wanted["MemAvailable"]
    if total <= 0 or available < 0 or available > total:
        return None
    return {"total": total, "available": available, "used": total - available}


# -------------------------------------------------------------- process tree

def _parse_process_stat(text: str) -> Optional[dict]:
    """One /proc/<pid>/stat line. The command may itself contain ") "."""
    try:
        opened = text.index("(")
        closed = text.rindex(")")
        pid = int(text[:opened].strip())
        comm = text[opened + 1:closed]
        fields = text[closed + 2:].split()
        if len(fields) < 22 or pid <= 0:
            return None
        return {
            "pid": pid, "comm": comm, "state": fields[0][:1],
            "ppid": int(fields[1]),
            "jiffies": int(fields[11]) + int(fields[12]),
            "threads": max(1, int(fields[17])),
            "starttime": int(fields[19]),
            "rss": max(0, int(fields[21])) * _page_size(),
        }
    except (ValueError, IndexError):
        return None


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as stream:
            return stream.read(16384).decode("utf-8", "replace")
    except OSError:
        return None


def _argv(proc: str, pid: int) -> list:
    raw = _read_text(os.path.join(proc, str(pid), "cmdline"))
    if not raw:
        return []
    return [part for part in raw.split("\0") if part]


def _display_command(argv: list, comm: str) -> str:
    """The command line, with each huge argument (an engine's MCP config, a
    system prompt) shortened, then the whole line capped."""
    if not argv:
        return "[{}]".format(comm)
    parts = []
    for argument in argv:
        value = " ".join(argument.split())
        parts.append(value if len(value) <= 40 else value[:39] + "\u2026")
    return " ".join(parts)[:CMD_CHARS]


def _short_argument(value: str) -> str:
    name = os.path.basename(value.rstrip("/")) or value
    return name[:28]


def _bridge_name(argv: list) -> str:
    """Puppy's own turn-bound MCP bridges, named by what they bridge to."""
    for index, argument in enumerate(argv):
        if argument == "-m" and index + 1 < len(argv):
            module = argv[index + 1]
            if module.startswith("puppy.") and module.endswith("_agent"):
                return module[len("puppy."):-len("_agent")][:20]
    return ""


def _label(argv: list, comm: str) -> str:
    """A name a person can read: the program, plus what it is running."""
    if not argv:
        return (comm or "?")[:40]
    bridge = _bridge_name(argv)
    if bridge:
        return "{} bridge".format(bridge)
    head = os.path.basename(argv[0].rstrip("/")) or comm or "?"
    head = head[:28]
    if head in _INTERPRETERS or head.startswith("python"):
        index = 1
        while index < len(argv):
            argument = argv[index]
            if argument == "-m" and index + 1 < len(argv):
                return "{} -m {}".format(head, argv[index + 1][:28])
            if argument in ("-c", "-lc", "-ec"):
                break
            if argument.startswith("-") or "=" in argument:
                index += 1
                continue
            if " " in argument:
                break
            return "{} {}".format(head, _short_argument(argument))
        return head
    # A browser's helpers differ only by role, and that role is the useful part.
    for argument in argv[1:]:
        if argument.startswith("--type="):
            return "{} {}".format(head, argument[7:][:20] or "helper")
    return head


def _kind(label: str, argv: list, comm: str) -> str:
    """A coarse family for the row's dot; engine names come from the registry."""
    if _bridge_name(argv):
        return "bridge"
    program = os.path.basename(argv[0].rstrip("/")) if argv else (comm or "")
    words = {program.lower()} | {word.lower() for word in label.split()}
    for key in engine_keys():
        if key in words:
            return key
    if any(name in words for name in browser.BINARY_CANDIDATES):
        return "browser"
    if program.lower().startswith("chrom") or program.lower().startswith("chrome"):
        return "browser"
    if program.lower() in _SHELLS:
        return "shell"
    return "proc"


def _scan(proc: str, monotonic: float) -> dict:
    """Every readable process, with its CPU share since the previous scan."""
    ticks = float(_clock_ticks())
    boot_uptime = _host_uptime(proc)
    records, seen = {}, set()
    try:
        entries = os.listdir(proc)
    except OSError:
        return {}
    for entry in entries:
        if not entry.isdigit():
            continue
        text = _read_text(os.path.join(proc, entry, "stat"))
        if not text:
            continue
        record = _parse_process_stat(text)
        if record is None:
            continue
        pid = record["pid"]
        seen.add(pid)
        previous = _process_samples.get(pid)
        cpu = None
        # A recycled pid is a different process: its start time must match too.
        if previous and previous[0] == record["starttime"]:
            elapsed = monotonic - previous[2]
            if elapsed > 0.05:
                used = (record["jiffies"] - previous[1]) / ticks
                if used >= 0:
                    cpu = round(max(0.0, 100.0 * used / elapsed), 1)
        _process_samples[pid] = (record["starttime"], record["jiffies"], monotonic)
        # Only the processes that survive the trim are described: reading a
        # command line for every process on the host would be most of the work
        # for none of the rows.
        record["cpu"] = cpu
        record["uptime"] = round(max(0.0, boot_uptime - record["starttime"] / ticks), 1) \
            if boot_uptime is not None else None
        records[pid] = record
    for pid in [pid for pid in _process_samples if pid not in seen]:
        _process_samples.pop(pid, None)
    return records


def _node(record: dict, root_pid: int, proc: str) -> dict:
    """Describe one kept process: its readable name, family and command."""
    argv = _argv(proc, record["pid"])
    label = _label(argv, record["comm"])
    return {
        "pid": record["pid"], "label": label,
        "cmd": _display_command(argv, record["comm"]),
        "kind": "puppy" if record["pid"] == root_pid else _kind(label, argv, record["comm"]),
        "cpu": record["cpu"], "rss": record["rss"], "threads": record["threads"],
        "state": record["state"], "uptime": record["uptime"], "children": [],
    }


def _group_key(node: dict):
    """What makes two childless siblings the same row. Puppy's per-turn MCP
    bridges are one family: five of them under every engine is noise, not
    information."""
    if node["kind"] == "bridge":
        return ("agent bridges", "bridge")
    return (node["label"], node["kind"])


def _fold_siblings(nodes: list) -> list:
    """Fold identical childless siblings into one counted row."""
    groups, order = {}, []
    for node in nodes:
        key = _group_key(node) if not node["children"] else None
        if key is None:
            order.append(node)
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(node)
    folded = []
    for item in order:
        if isinstance(item, dict):
            folded.append(item)
            continue
        members = groups[item]
        if len(members) < GROUP_SIBLINGS_AT:
            folded.extend(members)
            continue
        known = [member["cpu"] for member in members if member["cpu"] is not None]
        folded.append({
            "label": item[0], "kind": item[1], "count": len(members),
            "cpu": round(sum(known), 1) if known else None,
            "rss": sum(member["rss"] for member in members),
            "threads": sum(member["threads"] for member in members),
            "cmd": members[0]["cmd"], "children": [], "group": True,
            "pids": [member["pid"] for member in members[:12]],
        })
    return folded


def _sort_key(node: dict):
    return (-(node.get("cpu") or 0.0), -(node.get("rss") or 0), node.get("label") or "")


def build_tree(records: dict, root_pid: int, proc: str = "/proc") -> Optional[dict]:
    """The subtree below this Puppy process, trimmed to stay readable."""
    root = records.get(root_pid)
    if root is None:
        return None
    children = {}
    for record in records.values():
        children.setdefault(record["ppid"], []).append(record)
    budget = {"left": MAX_NODES - 1, "hidden": 0}

    def descend(record: dict, depth: int) -> dict:
        node = _node(record, root_pid, proc)
        kids = children.get(record["pid"], [])
        if not kids:
            return node
        if depth >= MAX_DEPTH:
            budget["hidden"] += len(kids)
            node["more"] = len(kids)
            return node
        ranked = sorted(kids, key=lambda item: (-(item["cpu"] or 0.0), -item["rss"]))
        built, skipped = [], 0
        for child in ranked:
            # Folding happens after the walk, so read enough siblings for a
            # browser's renderers to become one row rather than a "+n".
            if budget["left"] <= 0 or len(built) >= MAX_CHILDREN * 4:
                skipped += 1
                continue
            budget["left"] -= 1
            built.append(descend(child, depth + 1))
        built = _fold_siblings(built)
        built.sort(key=_sort_key)
        if len(built) > MAX_CHILDREN:
            skipped += sum(item.get("count", 1) for item in built[MAX_CHILDREN:])
            built = built[:MAX_CHILDREN]
        if skipped:
            budget["hidden"] += skipped
            node["more"] = node.get("more", 0) + skipped
        node["children"] = built
        return node

    tree = descend(root, 0)
    tree["hidden"] = budget["hidden"]
    return tree


def processes(proc: str = "/proc", root_pid: Optional[int] = None,
              now: Optional[float] = None) -> dict:
    """One trimmed snapshot of the tree, cached briefly for concurrent viewers."""
    key = (proc, root_pid)
    with _process_lock:
        moment = time.time() if now is None else now
        monotonic = time.monotonic()
        cached = _process_cache["payload"]
        if cached is not None and _process_cache["key"] == key and \
                0 <= monotonic - _process_cache["at"] < PROCESS_CACHE_SECONDS:
            return cached
        records = _scan(proc, monotonic)
        tree = build_tree(records, os.getpid() if root_pid is None else root_pid, proc)
        payload = {
            "root": tree, "total": len(records),
            "counted": _count(tree), "hidden": (tree or {}).get("hidden", 0),
            "sampled_at": moment,
        }
        _process_cache.update({"at": monotonic, "payload": payload, "key": key})
        return payload


def _count(node: Optional[dict]) -> int:
    if not node:
        return 0
    return int(node.get("count", 1)) + sum(_count(child) for child in node.get("children", ()))


def _unescape_mount(text: str) -> str:
    # mountinfo writes a space, tab, newline or backslash as \ooo
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), text)


def _mounts(path: str = "/proc/self/mountinfo") -> list:
    """(mount point, filesystem type) per mount, in mount order. Reading the
    table asks the kernel, never the filesystems themselves."""
    try:
        with open(path, "rb") as stream:
            text = stream.read(MOUNTINFO_LIMIT).decode("utf-8", "replace")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        left, separator, right = line.partition(" - ")
        fields = left.split()
        kinds = right.split()
        if not separator or len(fields) < 5 or not kinds:
            continue
        rows.append((_unescape_mount(fields[4]), kinds[0]))
    return rows


def _mount_of(path: str, mounts: list) -> Tuple[str, str]:
    """The mount a path lies on: the longest mount point it starts with, the
    later of two at one point (the one mounted over the other)."""
    best = ("/", "")
    for point, kind in mounts:
        inside = path == point or point == "/" or \
            path.startswith(point.rstrip("/") + "/")
        if inside and len(point) >= len(best[0]):
            best = (point, kind)
    return best


def _probe_disks(paths: list, mounts: list) -> dict:
    """In a probe's own thread: where each path really lies (a symlink can
    lead onto another mount), and the space of every mount they land on."""
    found = {}
    for kind, path in paths:
        try:
            real = os.path.realpath(path)
        except (OSError, ValueError):
            continue
        if kind != "puppy" and not os.path.isdir(real):
            continue        # a project whose directory is gone lies nowhere
        point, fs = _mount_of(real, mounts)
        entry = found.setdefault(point, {"mount": point, "fs": fs, "puppy": False,
                                         "projects": set()})
        if kind == "puppy":
            entry["puppy"] = True
        else:
            entry["projects"].add(real)
    for point, entry in found.items():
        try:
            info = os.statvfs(point)
        except OSError as exc:
            entry["error"] = exc.strerror or "could not read its space"
            continue
        size = info.f_frsize or info.f_bsize
        entry["total"] = info.f_blocks * size
        entry["used"] = max(0, info.f_blocks - info.f_bfree) * size
        entry["available"] = info.f_bavail * size
    return found


def disks(projects=(), mountinfo: str = "/proc/self/mountinfo",
          timeout: float = DISK_TIMEOUT_SECONDS, now: Optional[float] = None) -> dict:
    """The free space of every filesystem holding Puppy (its program and its
    data) or one of the given project directories, best effort and bounded:
    a mount that does not answer within ``timeout`` is named in ``stalled``
    instead, and a mount still being asked from an earlier read is not asked
    again. Cached briefly, so viewers share one reading."""
    puppy_paths = [("puppy", config.BASE_DIR), ("puppy", config.DATA_DIR)]
    project_paths = sorted({os.path.abspath(str(path)) for path in projects
                            if isinstance(path, str) and path})[:DISK_MAX_PATHS]
    key = (mountinfo, tuple(project_paths))
    with _disk_lock:
        moment = time.time() if now is None else now
        monotonic = time.monotonic()
        cached = _disk_cache["payload"]
        if cached is not None and _disk_cache["key"] == key and \
                0 <= monotonic - _disk_cache["at"] < DISK_CACHE_SECONDS:
            return cached
        mounts = _mounts(mountinfo)
        # Grouped by the mount each path names on its face, which takes no
        # filesystem access: a mount that hangs holds up one thread only.
        groups = {}
        for kind, path in puppy_paths + [("project", path) for path in project_paths]:
            groups.setdefault(_mount_of(os.path.abspath(path), mounts)[0], []).append((kind, path))
        stalled = []
        started = []
        for point, paths in groups.items():
            probe = _disk_probes.get(point)
            if probe is not None and probe["thread"].is_alive():
                stalled.append(point)
                continue
            if sum(1 for item in _disk_probes.values() if item["thread"].is_alive()) >= DISK_MAX_STUCK:
                stalled.append(point)
                continue
            probe = {"result": None}

            def run(probe=probe, paths=paths):
                try:
                    probe["result"] = _probe_disks(paths, mounts)
                except Exception:
                    probe["result"] = {}

            probe["thread"] = threading.Thread(target=run, name="puppy-disk-space", daemon=True)
            _disk_probes[point] = probe
            probe["thread"].start()
            started.append((point, probe))
        deadline = time.monotonic() + max(0.0, timeout)
        found = {}
        for point, probe in started:
            probe["thread"].join(max(0.0, deadline - time.monotonic()))
            if probe["thread"].is_alive():
                stalled.append(point)
                continue
            _disk_probes.pop(point, None)
            for mount, entry in (probe["result"] or {}).items():
                merged = found.setdefault(mount, dict(entry, projects=set()))
                merged["puppy"] = merged["puppy"] or entry["puppy"]
                merged["projects"] |= entry["projects"]
        rows = []
        for mount, entry in found.items():
            if mount in stalled:
                continue
            projects_here = sorted(entry.pop("projects"))
            entry["projects"] = len(projects_here)
            entry["paths"] = projects_here[:DISK_SAMPLE_PATHS]
            rows.append(entry)
        rows.sort(key=lambda row: (not row["puppy"], row["mount"]))
        payload = {"mounts": rows, "stalled": sorted(set(stalled)), "sampled_at": moment}
        _disk_cache.update({"at": monotonic, "payload": payload, "key": key})
        return payload


def project_directories() -> list:
    """Every session's working directory on this node, archived ones too."""
    try:
        return [row["cwd"] for row in db.query("SELECT DISTINCT cwd FROM sessions")
                if row["cwd"]]
    except Exception:
        return []


def snapshot(app, window_seconds: float = DEFAULT_WINDOW_SECONDS,
             proc: str = "/proc", projects=()) -> dict:
    """Everything the console's CPU panel shows for one node."""
    sample = latest(app) or {}
    now = time.time()
    return {
        "ok": True,
        "sampled_at": now,
        "cpu": {
            "percent": sample.get("cpu_percent"),
            "at": sample.get("sampled_at"),
            "cores": _cpu_count(),
            "interval": SAMPLE_INTERVAL_SECONDS,
            "window": window_seconds,
            "history": history(app, window_seconds, now),
        },
        "memory": _memory(proc),
        "load": _load_average(),
        "uptime": _host_uptime(proc),
        "disks": disks(projects, now=now),
        "processes": processes(proc, now=now),
    }


async def h_metrics(request: web.Request):
    try:
        window = float(request.query.get("window", DEFAULT_WINDOW_SECONDS))
    except (TypeError, ValueError):
        return web.json_response({"error": "window must be a number"}, status=400)
    if not 0 < window <= MAX_WINDOW_SECONDS:
        window = DEFAULT_WINDOW_SECONDS
    app = request.app
    projects = project_directories()
    payload = await asyncio.get_event_loop().run_in_executor(
        None, lambda: snapshot(app, window, projects=projects))
    return web.json_response(payload)


async def _sample_loop(app) -> None:
    state = app[_STATE_KEY]
    publish = app.get("puppy_role") == "full"
    previous = _read_cpu_times()
    delay = FIRST_SAMPLE_SECONDS
    while True:
        await asyncio.sleep(delay)
        delay = SAMPLE_INTERVAL_SECONDS
        current = _read_cpu_times()
        value = _cpu_percent(previous, current) if previous and current else None
        previous = current
        payload = {
            "type": "host_metrics",
            "cpu_percent": round(value, 1) if value is not None else None,
            "sampled_at": time.time(),
        }
        state["latest"] = payload
        _record_sample(app, payload["sampled_at"], payload["cpu_percent"])
        # This is replaceable telemetry, not an edge: a slow or backgrounded
        # console needs only the newest sample and receives it on reconnect.
        # A headless node keeps the same history for its panel, but its
        # controller reads it over HTTP rather than through the state stream.
        if publish:
            runner.publish_state(payload)


async def _lifecycle(app):
    task = asyncio.create_task(_sample_loop(app), name="puppy-host-metrics")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        app[_STATE_KEY]["latest"] = None


def register(app) -> None:
    """Start one sampler for a node process, regardless of client count."""
    if app.get(_STATE_KEY) is not None:
        return
    app[_STATE_KEY] = {"latest": None,
                       "history": deque(maxlen=HISTORY_SAMPLES)}
    app.router.add_get("/api/host/metrics", h_metrics)
    app.cleanup_ctx.append(_lifecycle)


def reset_for_tests() -> None:
    _process_samples.clear()
    _process_cache.update({"at": 0.0, "payload": None, "key": None})
    _disk_cache.update({"at": 0.0, "payload": None, "key": None})
    _disk_probes.clear()
