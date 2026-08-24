from __future__ import annotations

import logging
import logging.handlers
import os
import sys

from aiohttp import web as aioweb

from puppy import __version__, config, db, workspaces


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s | [%(levelname).1s] | %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    try:
        fh = logging.handlers.RotatingFileHandler(config.LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=2)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def initialize_runtime() -> None:
    """Initialize shared state for either the full console or a headless node."""
    if sys.version_info < (3, 9):
        print("puppy requires Python 3.9+", file=sys.stderr)
        sys.exit(1)
    os.umask(0o077)
    config.ensure_dirs()
    setup_logging()
    config.load()
    db.connect()
    workspaces.cleanup_orphans()
    # a previous unclean shutdown can leave sessions stuck on 'running';
    # nothing is actually running at boot (and restart.sh keys off this)
    db.execute("UPDATE sessions SET status='idle' WHERE status!='idle'")


def main() -> None:
    initialize_runtime()

    log = logging.getLogger("puppy")
    host = config.get("web.host", "0.0.0.0")
    port = int(config.get("web.port", 10888))
    log.info("puppy %s starting on %s:%s (data: %s)", __version__, host, port, config.DATA_DIR)

    from puppy.web import build_app
    aioweb.run_app(build_app(), host=host, port=port, print=None,
                   shutdown_timeout=5)


if __name__ == "__main__":
    main()
