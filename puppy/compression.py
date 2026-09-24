"""Gzip for console startup, after the ordinary authentication and routing.

Code assets retain a small process-local cache of compressed bytes, keyed by
their file identity. Nothing is written beside the source files, and an edit
invalidates its cached representation automatically. Reads and compression run
outside the event loop. JSON/HTML responses are compressed but never cached.
"""
import asyncio
from functools import lru_cache
import gzip
import mimetypes
import os
from pathlib import Path
import stat

from aiohttp import web


MIN_BYTES = 1024
GZIP_LEVEL = 6
ASSET_CACHE_ENTRIES = 32
BODY_PATHS = {"/", "/api/state"}
FILE_CONDITIONS = ("Range", "If-Range", "If-Match", "If-None-Match",
                   "If-Modified-Since", "If-Unmodified-Since")


def accepts_gzip(request):
    """An explicit refusal wins over a wildcard; absent encoding stays plain."""
    qualities = {}
    for entry in request.headers.get("Accept-Encoding", "").split(","):
        parts = entry.strip().lower().split(";")
        quality = 1.0
        for parameter in parts[1:]:
            name, _, value = parameter.strip().partition("=")
            if name.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        qualities[parts[0].strip()] = quality if 0 <= quality <= 1 else 0.0
    return qualities.get("gzip", qualities.get("*", 0)) > 0


def vary_encoding(response):
    value = ", ".join(response.headers.getall("Vary", []))
    fields = {part.strip().lower() for part in value.split(",")}
    if "*" not in fields and "accept-encoding" not in fields:
        response.headers["Vary"] = (value + ", " if value else "") + "Accept-Encoding"


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _gzip(body):
    return gzip.compress(body, compresslevel=GZIP_LEVEL, mtime=0)


@lru_cache(maxsize=ASSET_CACHE_ENTRIES)
def _gzip_asset(path, identity):
    with path.open("rb") as source:
        body = source.read()
        if _identity(os.fstat(source.fileno())) != identity:
            # An edit raced this read. Let FileResponse serve the current file;
            # do not retain bytes under the old file's identity.
            raise OSError("Asset changed while reading")
    return _gzip(body)


def _asset(directory, filename):
    root = Path(directory).resolve()
    path = (root / filename).resolve()
    path.relative_to(root)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size < MIN_BYTES:
        return None
    body = _gzip_asset(path, _identity(info))
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return body, content_type


@web.middleware
async def middleware(request, handler):
    response = await handler(request)
    if request.method not in ("GET", "HEAD") or response.status != 200 or \
            response.prepared or "Content-Encoding" in response.headers:
        return response

    if isinstance(response, web.FileResponse):
        # Only the static router's JS/CSS files. Its normal path checks have
        # already run; downloads, the dictionary's own gzip sibling and other
        # file responses keep their existing serving behavior.
        info = request.match_info.route.resource.get_info()
        filename = request.match_info.get("filename", "")
        if "directory" not in info or Path(filename).suffix not in (".js", ".css"):
            return response
        vary_encoding(response)
        if not accepts_gzip(request) or any(name in request.headers for name in FILE_CONDITIONS):
            return response
        try:
            asset = await asyncio.get_running_loop().run_in_executor(
                None, _asset, info["directory"], filename)
        except (OSError, ValueError, RuntimeError):
            # Preserve FileResponse's missing-file, permission and path errors.
            return response
        if asset is None:
            return response
        body, content_type = asset
        headers = response.headers.copy()
        headers["Content-Encoding"] = "gzip"
        headers.setdefault("Content-Type", content_type)
        return web.Response(body=body, headers=headers)

    if request.path in BODY_PATHS and isinstance(response, web.Response) and \
            isinstance(response.body, bytes) and len(response.body) >= MIN_BYTES:
        vary_encoding(response)
        if accepts_gzip(request):
            response.body = await asyncio.get_running_loop().run_in_executor(
                None, _gzip, response.body)
            response.headers["Content-Encoding"] = "gzip"
    return response
