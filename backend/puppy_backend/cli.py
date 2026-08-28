"""Command-line entry point for the headless backend artifact."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

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
                        choices=("serve", "token", "pairing", "self-test"))
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
    parser.add_argument(
        "--usage-refresh-minutes", type=int,
        help="read-only engine usage refresh interval; 0 disables (default: 15)")
    parser.add_argument(
        "--max-upload-size-mb", type=int,
        help="maximum size of one uploaded file in MiB; 0 disables (default: 8)")
    terminals = parser.add_mutually_exclusive_group()
    terminals.add_argument("--terminal", dest="terminal_enabled", action="store_true",
                           help="enable remote terminal websockets")
    terminals.add_argument("--disable-terminal", dest="terminal_enabled", action="store_false",
                           help="disable remote terminal websockets")
    parser.set_defaults(terminal_enabled=None)
    browsers = parser.add_mutually_exclusive_group()
    browsers.add_argument("--browser", dest="browser_enabled", action="store_true",
                          help="enable the managed headless browser (needs a usable "
                               "Chromium/Chrome binary; also toggleable from a controller)")
    browsers.add_argument("--disable-browser", dest="browser_enabled", action="store_false",
                          help="disable the managed headless browser")
    parser.set_defaults(browser_enabled=None)
    upgrades = parser.add_mutually_exclusive_group()
    upgrades.add_argument("--enable-remote-upgrade", dest="remote_upgrade_enabled",
                          action="store_true",
                          help="allow signed upgrades when running under the external launcher")
    upgrades.add_argument("--disable-remote-upgrade", dest="remote_upgrade_enabled",
                          action="store_false", help="disable remote artifact upgrades")
    parser.set_defaults(remote_upgrade_enabled=None)
    parser.add_argument("--turn-timeout", type=float, help="maximum turn duration in seconds")
    parser.add_argument("--shutdown-grace", type=float,
                        help="seconds to wait for active turns during shutdown")
    tls_modes = parser.add_mutually_exclusive_group()
    tls_modes.add_argument("--auto-tls", dest="tls_mode", action="store_const", const="auto",
                           help="create and persist a self-signed identity for pinned HTTPS")
    tls_modes.add_argument("--disable-tls", dest="tls_mode", action="store_const",
                           const="disabled", help="serve cleartext HTTP (legacy mode)")
    parser.set_defaults(tls_mode=None)
    parser.add_argument("--tls-cert", help="persist a custom PEM certificate chain for HTTPS")
    parser.add_argument("--tls-key", help="persist its PEM private key")
    parser.add_argument("--log-level", choices=("debug", "info", "warning", "error"),
                        default="info")
    return parser


def _configure(args, parser: argparse.ArgumentParser):
    data_dir = os.path.abspath(os.path.expanduser(args.data_dir))
    os.environ["PUPPY_DATA"] = data_dir

    # Import only after PUPPY_DATA is fixed: config paths are module constants.
    from puppy import config

    existing_config = os.path.exists(config.CONFIG_PATH)
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
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
            parsed.port
        except ValueError:
            parser.error("--advertise-url is invalid")
        if url and (parsed.scheme not in ("http", "https") or not host or
                    parsed.username is not None or parsed.password is not None or
                    parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            parser.error("--advertise-url must be an http(s) origin without credentials or a path")
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
    if args.usage_refresh_minutes is not None:
        try:
            interval = config.normalize_usage_refresh_minutes(args.usage_refresh_minutes)
        except ValueError as exc:
            parser.error(str(exc))
        config.set_value("engines.usage_refresh_minutes", interval)
    if args.max_upload_size_mb is not None:
        try:
            upload_limit = config.normalize_upload_limit_mb(args.max_upload_size_mb)
        except ValueError as exc:
            parser.error(str(exc))
        config.set_value("uploads.max_file_size_mb", upload_limit)
    if args.terminal_enabled is not None:
        config.set_value("backend.terminal_enabled", bool(args.terminal_enabled))
    if args.browser_enabled is not None:
        config.set_value("browser.enabled", bool(args.browser_enabled))
    if args.remote_upgrade_enabled is not None:
        config.set_value("backend.remote_upgrade_enabled", bool(args.remote_upgrade_enabled))
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
    if args.tls_cert and args.tls_mode is not None:
        parser.error("custom --tls-cert/--tls-key cannot be combined with a TLS mode flag")
    if args.tls_cert:
        config.set_value("backend.tls_cert", str(Path(args.tls_cert).expanduser().resolve()))
        config.set_value("backend.tls_key", str(Path(args.tls_key).expanduser().resolve()))
        config.set_value("backend.tls_mode", "files")
    elif args.tls_mode is not None:
        config.set_value("backend.tls_mode", args.tls_mode)
    elif not existing_config and args.command != "self-test":
        # New headless installations start secure. Existing pre-TLS configs
        # merge the disabled default so an upgrade never changes their scheme.
        config.set_value("backend.tls_mode", "auto")
    return config


def _derived_url(config, tls_enabled: bool) -> str:
    advertised = str(config.get("backend.advertise_url", "") or "").rstrip("/")
    if advertised:
        actual_scheme = "https" if tls_enabled else "http"
        if not advertised.startswith(actual_scheme + "://"):
            raise ValueError(
                "--advertise-url must use {}:// for the configured transport".format(
                    actual_scheme))
        return advertised
    host = str(config.get("backend.host", "127.0.0.1"))
    if host in ("0.0.0.0", "::", ""):
        return ""
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    scheme = "https" if tls_enabled else "http"
    return f"{scheme}://{display_host}:{int(config.get('backend.port', 10888))}"


def _pairing(config, identity) -> dict:
    from puppy import protocol
    from . import upgrade

    terminal_enabled = bool(config.get("backend.terminal_enabled", True))
    pairing = {
        "name": config.get("instance_name"),
        "url": _derived_url(config, identity.enabled),
        "token": config.get("auth.api_token"),
        "protocol": protocol.API_PROTOCOL,
        "capabilities": upgrade.capabilities(terminal_enabled, identity.enabled),
        "usage_refresh_minutes": config.get("engines.usage_refresh_minutes"),
        "max_upload_size_mb": config.get("uploads.max_file_size_mb"),
    }
    if identity.enabled:
        pairing["tls_sha256"] = identity.fingerprint
    return pairing


def _self_test() -> dict:
    from puppy import protocol
    from puppy.main import initialize_runtime
    from . import build_info
    from .app import build_app

    initialize_runtime()
    app = build_app(include_terminal=False, transport={"encrypted": False}, upgrade_health={
        "host": "127.0.0.1", "port": 1, "tls": False,
    })
    routes = sorted({route.resource.canonical for route in app.router.routes()})
    required = {"/api/ping", "/api/engines", "/api/engines/order",
                "/api/engines/usage-refresh",
                "/api/uploads/settings", "/api/sessions",
                "/api/sessions/{sid}/upload", protocol.UPGRADE_API_PATH}
    if not required.issubset(routes):
        raise RuntimeError("candidate API surface is incomplete")
    return {
        "ok": True,
        "role": "backend",
        "version": __version__,
        "protocol": protocol.API_PROTOCOL,
        "artifact": build_info.ARTIFACT_KIND,
        "build_commit": build_info.BUILD_COMMIT,
        "routes": routes,
    }


def main() -> None:
    os.umask(0o077)
    parser = _parser()
    args = parser.parse_args()
    config = _configure(args, parser)

    if args.command == "token":
        print(config.get("auth.api_token"))
        return
    if args.command == "pairing":
        from .tls import load_identity

        try:
            identity = load_identity()
            pairing = _pairing(config, identity)
        except (RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        if not pairing["url"]:
            parser.error("pairing needs --advertise-url when listening on a wildcard address")
        print(json.dumps(pairing, indent=2, sort_keys=True))
        return
    if args.command == "self-test":
        print(json.dumps(_self_test(), sort_keys=True))
        return

    from aiohttp import web as aioweb
    from puppy.main import initialize_runtime

    from .app import build_app
    from .tls import load_identity

    initialize_runtime()
    logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))
    host = str(config.get("backend.host", "127.0.0.1"))
    port = int(config.get("backend.port", 10888))
    terminal_enabled = bool(config.get("backend.terminal_enabled", True))
    try:
        identity = load_identity()
        url = _derived_url(config, identity.enabled)
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    log = logging.getLogger("puppy.backend")
    log.info("headless backend %s starting on %s:%s "
             "(data: %s, terminal: %s, usage refresh: %sm, upload limit: %s MiB, "
             "transport: %s)",
             __version__, host, port, config.DATA_DIR,
             "enabled" if terminal_enabled else "disabled",
             config.get("engines.usage_refresh_minutes"),
             config.get("uploads.max_file_size_mb"),
             "pinned TLS" if identity.enabled else "cleartext HTTP")
    if url:
        log.info("pair this backend at %s; retrieve credentials with the pairing command", url)
    else:
        log.info("set --advertise-url to produce a controller pairing block")
    transport = {"encrypted": identity.enabled}
    if identity.enabled:
        transport["certificate_sha256"] = identity.fingerprint
    aioweb.run_app(build_app(include_terminal=terminal_enabled, transport=transport,
                    upgrade_health={
                        "host": host, "port": port, "tls": identity.enabled,
                        "certificate_sha256": identity.fingerprint,
                    }), host=host, port=port,
                    print=None, shutdown_timeout=5, ssl_context=identity.context)


if __name__ == "__main__":
    main()
