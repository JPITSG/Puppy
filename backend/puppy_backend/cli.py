"""Command-line entry point for the headless backend artifact."""
from __future__ import annotations

import argparse
import json
import logging
import os
import ssl
import sys
from pathlib import Path

from puppy import __version__


def _default_data_dir() -> str:
    override = os.environ.get("PUPPY_BACKEND_DATA")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    executable = Path(sys.argv[0]).resolve()
    if executable.suffix == ".pyz":
        return str(executable.parent / "data")
    return str(Path(__file__).resolve().parents[1] / "data")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="puppy-backend",
        description="Headless execution backend for a Puppy web console")
    parser.add_argument("command", nargs="?", default="serve",
                        choices=("serve", "token", "pairing"))
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", default=_default_data_dir(),
                        help="private state directory (default: beside the artifact)")
    parser.add_argument("--name", help="backend name advertised to controllers")
    parser.add_argument("--bind", dest="host", help="listen address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, help="listen port (default: 10888)")
    parser.add_argument("--advertise-url",
                        help="controller-reachable base URL printed by the pairing command")
    parser.add_argument("--api-token", "--token", dest="api_token",
                        help="set the API token (prefer PUPPY_BACKEND_TOKEN to avoid shell history)")
    parser.add_argument("--default-cwd", help="default working directory for new sessions")
    parser.add_argument("--terminal-command", help="default command for terminal tabs")
    terminals = parser.add_mutually_exclusive_group()
    terminals.add_argument("--terminal", dest="terminal_enabled", action="store_true",
                           help="enable remote terminal websockets")
    terminals.add_argument("--disable-terminal", dest="terminal_enabled", action="store_false",
                           help="disable remote terminal websockets")
    parser.set_defaults(terminal_enabled=None)
    parser.add_argument("--turn-timeout", type=float, help="maximum turn duration in seconds")
    parser.add_argument("--shutdown-grace", type=float,
                        help="seconds to wait for active turns during shutdown")
    parser.add_argument("--tls-cert", help="PEM certificate chain for direct HTTPS")
    parser.add_argument("--tls-key", help="PEM private key for direct HTTPS")
    parser.add_argument("--log-level", choices=("debug", "info", "warning", "error"),
                        default="info")
    return parser


def _configure(args, parser: argparse.ArgumentParser):
    data_dir = os.path.abspath(os.path.expanduser(args.data_dir))
    os.environ["PUPPY_DATA"] = data_dir

    # Import only after PUPPY_DATA is fixed: config paths are module constants.
    from puppy import config

    config.load()
    if args.name is not None:
        config.set_value("instance_name", args.name.strip()[:60] or "puppy-backend")
    if args.host is not None:
        config.set_value("backend.host", args.host.strip())
    if args.port is not None:
        if not 1 <= args.port <= 65535:
            parser.error("--port must be between 1 and 65535")
        config.set_value("backend.port", args.port)
    if args.advertise_url is not None:
        url = args.advertise_url.strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            parser.error("--advertise-url must start with http:// or https://")
        config.set_value("backend.advertise_url", url)
    supplied_token = args.api_token or os.environ.get("PUPPY_BACKEND_TOKEN")
    if supplied_token:
        supplied_token = supplied_token.strip()
        if len(supplied_token) < 16:
            parser.error("API token must be at least 16 characters")
        config.set_value("auth.api_token", supplied_token)
    if args.default_cwd is not None:
        cwd = os.path.abspath(os.path.expanduser(args.default_cwd))
        if not os.path.isdir(cwd):
            parser.error(f"--default-cwd is not a directory: {cwd}")
        config.set_value("sessions.default_cwd", cwd)
    if args.terminal_command is not None:
        command = args.terminal_command.strip()
        if not command:
            parser.error("--terminal-command cannot be empty")
        config.set_value("terminal.command", command)
    if args.terminal_enabled is not None:
        config.set_value("backend.terminal_enabled", bool(args.terminal_enabled))
    if args.turn_timeout is not None:
        if args.turn_timeout <= 0:
            parser.error("--turn-timeout must be positive")
        config.set_value("sessions.turn_timeout", args.turn_timeout)
    if args.shutdown_grace is not None:
        if args.shutdown_grace < 0:
            parser.error("--shutdown-grace cannot be negative")
        config.set_value("sessions.shutdown_grace", args.shutdown_grace)
    if bool(args.tls_cert) != bool(args.tls_key):
        parser.error("--tls-cert and --tls-key must be supplied together")
    return config


def _derived_url(config, tls_enabled: bool) -> str:
    advertised = str(config.get("backend.advertise_url", "") or "").rstrip("/")
    if advertised:
        return advertised
    host = str(config.get("backend.host", "127.0.0.1"))
    if host in ("0.0.0.0", "::", ""):
        return ""
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    scheme = "https" if tls_enabled else "http"
    return f"{scheme}://{display_host}:{int(config.get('backend.port', 10888))}"


def _pairing(config, tls_enabled: bool) -> dict:
    from puppy import protocol

    terminal_enabled = bool(config.get("backend.terminal_enabled", True))
    return {
        "name": config.get("instance_name"),
        "url": _derived_url(config, tls_enabled),
        "token": config.get("auth.api_token"),
        "protocol": protocol.API_PROTOCOL,
        "capabilities": protocol.execution_capabilities(terminal_enabled),
    }


def _ssl_context(args):
    if not args.tls_cert:
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.tls_cert, args.tls_key)
    return context


def main() -> None:
    os.umask(0o077)
    parser = _parser()
    args = parser.parse_args()
    config = _configure(args, parser)

    if args.command == "token":
        print(config.get("auth.api_token"))
        return
    if args.command == "pairing":
        pairing = _pairing(config, bool(args.tls_cert))
        if not pairing["url"]:
            parser.error("pairing needs --advertise-url when listening on a wildcard address")
        print(json.dumps(pairing, indent=2, sort_keys=True))
        return

    from aiohttp import web as aioweb
    from puppy.main import initialize_runtime

    from .app import build_app

    initialize_runtime()
    logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))
    host = str(config.get("backend.host", "127.0.0.1"))
    port = int(config.get("backend.port", 10888))
    terminal_enabled = bool(config.get("backend.terminal_enabled", True))
    tls_context = _ssl_context(args)
    log = logging.getLogger("puppy.backend")
    log.info("headless backend %s starting on %s:%s (data: %s, terminal: %s)",
             __version__, host, port, config.DATA_DIR,
             "enabled" if terminal_enabled else "disabled")
    url = _derived_url(config, tls_context is not None)
    if url:
        log.info("pair this backend at %s; retrieve credentials with the pairing command", url)
    else:
        log.info("set --advertise-url to produce a controller pairing block")
    aioweb.run_app(build_app(include_terminal=terminal_enabled), host=host, port=port,
                    print=None, shutdown_timeout=5, ssl_context=tls_context)


if __name__ == "__main__":
    main()
