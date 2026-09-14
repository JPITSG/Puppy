#!/usr/bin/env python3
"""Opt-in attachment steering probe against an installed engine and real model.

Run: python3 tests/steering_live_test.py --engine claude|codex|opencode [--model ID]
Spends one short real model turn. Copies only native sign-in/model selection
files into private scratch storage; original configuration/history is untouched.
The model must read a random file and view an image attached after its first
shell command starts, within the same native turn and process.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import random
import secrets
import shutil
import struct
import subprocess
import sys
import time
from unittest.mock import AsyncMock, patch
import zlib

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root


def png(colors):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    row = b"\0" + b"".join(bytes(rgb) * 160 for rgb in colors)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 480, 160, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(row * 160)) + chunk(b"IEND", b""))


def isolate(root, engine):
    """Copy credentials without printing them or exposing unrelated projects."""
    source_home = Path.home()
    native = root / "native"
    native.mkdir(mode=0o700)
    child = {"HOME": str(native), "TMPDIR": str(root / "tmp")}
    for key in ("CONFIG", "DATA", "CACHE", "STATE"):
        target = native / key.lower()
        target.mkdir()
        child["XDG_" + key + "_HOME"] = str(target)
    copies = []
    if engine == "claude":
        source = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(source_home / ".claude")))
        target = native / ".claude"
        child["CLAUDE_CONFIG_DIR"] = str(target)
        copies.append((source / ".credentials.json", target / ".credentials.json"))
        # Account metadata belongs with the copied OAuth login, without global
        # hooks, project trust, MCP definitions or plugin configuration.
        settings = source / ".config.json"
        if not settings.is_file():
            settings = (source if os.environ.get("CLAUDE_CONFIG_DIR") else source_home) / ".claude.json"
        if settings.is_file():
            account = json.loads(settings.read_text()).get("oauthAccount")
            if account:
                target.mkdir(mode=0o700, exist_ok=True)
                (target / ".claude.json").write_text(json.dumps({"oauthAccount": account, "hasCompletedOnboarding": True}))
        child["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    elif engine == "codex":
        source = Path(os.environ.get("CODEX_HOME", str(source_home / ".codex")))
        child["CODEX_HOME"] = str(native / ".codex")
        copies.append((source / "auth.json", native / ".codex" / "auth.json"))
    else:
        source_data = Path(os.environ.get("XDG_DATA_HOME", str(source_home / ".local/share")))
        source_state = Path(os.environ.get("XDG_STATE_HOME", str(source_home / ".local/state")))
        copies += [(source_data / "opencode/auth.json", native / "data/opencode/auth.json"),
                   (source_state / "opencode/model.json", native / "state/opencode/model.json")]
        child.update({"OPENCODE_TEST_HOME": str(native), "OPENCODE_DISABLE_AUTOUPDATE": "1",
                      "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_PURE": "1",
                      "OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER": "1"})
    for source, destination in copies:
        if not source.is_file():
            continue
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o600)
    return child


async def probe(args, root, child):
    from puppy import config, db, runner, uploads
    from puppy.drivers import get_driver

    config.load()
    config.set_value("sessions.turn_timeout", 180)
    db.connect()
    driver = get_driver(args.engine)
    build_env = driver.build_env
    build_cmd = driver.build_cmd
    project = root / "project"
    project.mkdir()
    (root / "tmp").mkdir()
    # The child must not discover this repository's development instructions.
    (project / "AGENTS.md").write_text("This is an isolated attachment test. Only read the files the user attaches.\n")
    (project / "CLAUDE.md").write_text("Only perform the user's attachment test.\n")
    subprocess.run(["git", "init", "--quiet", str(project)], check=True)

    def command(*values, **kwargs):
        argv = build_cmd(*values, **kwargs)
        if args.engine == "claude":
            argv += ["--setting-sources", "", "--settings", '{"disableAllHooks":true}']
        return argv

    def environment(*values, **kwargs):
        value = {**build_env(*values, **kwargs), **child}
        if args.engine == "opencode":
            from puppy.drivers.opencode import PUPPY_AGENT
            inline = json.loads(value["OPENCODE_CONFIG_CONTENT"])
            # ACP read approvals can omit rawInput. Authorize the fixture's
            # directory in the native policy instead of guessing that path
            # from a prompt displayed without its file arguments.
            inline["agent"][PUPPY_AGENT]["permission"] = {
                "read": {"*": "deny", str(root) + "/**": "allow"},
                "external_directory": {"*": "deny", str(root) + "/**": "allow"},
            }
            value["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline)
        return value

    mode = "read-only" if args.engine == "codex" else "default"
    sid = db.create_session("Attachment probe", args.engine, str(project), args.model or "", "", "", mode)
    hub = runner.hub(sid)
    secret = secrets.token_hex(12)
    palette = {"red": (255, 0, 0), "green": (0, 180, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0)}
    colors = random.SystemRandom().sample(list(palette), 3)
    prompt = ("First use your shell to run exactly `sleep 10`. Do not use other tools in parallel. "
              "After it finishes follow any additional user instruction received during the wait; "
              "otherwise reply WAIT_FINISHED. Do not read files unless a later message attaches them.")
    with ExitStack() as stack:
        stack.enter_context(patch.object(driver, "build_env", side_effect=environment))
        stack.enter_context(patch.object(driver, "build_cmd", side_effect=command))
        for name in ("browser_agent", "terminal_agent", "vnc_agent", "spawn_agent", "session_agent"):
            stack.enter_context(patch.object(getattr(runner, name), "turn_mcp", return_value=None))
        stack.enter_context(patch.object(runner.session_links, "prepare_turn", AsyncMock()))
        stack.enter_context(patch.object(runner.system_prompts, "turn_prompt", return_value=""))
        try:
            assert hub.send_message(prompt) == {"queued": False}
            deadline = time.monotonic() + 180
            submitted = False
            approval_ids = set()
            while time.monotonic() < deadline:
                approval = hub.pending_approval
                if approval and approval["request_id"] not in approval_ids:
                    approval_ids.add(approval["request_id"])
                    # The probe authorizes reading only its own scratch tree
                    # and the requested sleep; refuse all other commands.
                    inputs = approval.get("input") or {}
                    tool = str(approval.get("tool_name", "")).lower()
                    safe = tool in ("bash", "execute") and inputs.get("command") == "sleep 10"
                    filename = inputs.get("file_path") or inputs.get("filePath")
                    if tool == "read" and isinstance(filename, str):
                        target = (project / filename).resolve()
                        safe = root in target.parents
                    await hub.approval_response(approval["request_id"], "allow" if safe else "deny")
                events = db.get_events(sid)
                # Some ACP versions expose only the shell executable in
                # rawInput ("bash"), omitting its script. Match the live tool
                # identity instead of relying on a provider's command label.
                finished = {e["data"].get("tool_use_id") for e in events if e["kind"] == "tool_result"}
                sleeping = any(e["kind"] == "tool_use" and e["data"].get("tool_use_id") not in finished
                               for e in events)
                if not submitted and sleeping and hub.steering_state()["ready"]:
                    # These files did not exist when the original prompt ran.
                    paths = []
                    for name, data in (("note.txt", (secret + "\n").encode()),
                                       ("picture.png", png([palette[c] for c in colors]))):
                        directory = uploads._new_upload_directory(sid)
                        path = directory / name
                        path.write_bytes(data)
                        paths.append(path)
                    text = ("Read the attached text file and open the attached image with your image viewing tool. "
                            "Do not infer the image from its filename or decode its pixels with code. "
                            "Reply with FILE_CODE=<file contents> and IMAGE_COLORS=<three color names, left to right, comma separated>.\n\n" +
                            uploads.ATTACH_FILE_PREFIX + str(paths[0]) + " (note.txt, 25 B)" + uploads.ATTACH_FILE_SUFFIX + "\n" +
                            uploads.ATTACH_IMAGE_PREFIX + str(paths[1]) + uploads.ATTACH_IMAGE_SUFFIX)
                    turn_id, pid = hub.steering_state()["turn_id"], hub.proc.pid
                    response = await hub.steer(text, "attachment-probe", expected_turn_id=turn_id)
                    assert response.get("ok"), response
                    submitted = True
                    print(args.engine + ": attachments handed to the active engine", flush=True)
                if hub.status == "idle":
                    break
                if submitted and not hub._turn_result_seen:
                    assert hub._active_turn_id == turn_id and hub.proc.pid == pid, "steering restarted the native process/turn"
                await asyncio.sleep(.05)
            else:
                raise AssertionError("engine probe exceeded its deadline")
            if hub.turn_task:
                await hub.turn_task
            events = db.get_events(sid)
            results = [e["data"] for e in events if e["kind"] == "result"]
            answers = "\n".join(e["data"].get("text", "") for e in events if e["kind"] == "assistant")
            (root / "events.json").write_text(json.dumps(events))
            assert submitted, "engine never offered an active shell boundary"
            assert len(results) == 1 and results[0].get("ok"), "native turn failed"
            assert secret in answers, "model did not read the newly attached file"
            normalized = answers.lower().replace(" ", "")
            assert ",".join(colors) in normalized, "model did not identify the newly attached image"
            assert hub._steer_receipts["attachment-probe"]["status"] == "accepted", "native acknowledgement missing"
            assert not hub.queue and len([e for e in events if e["kind"] == "user"]) == 2
            print("PASS: " + args.engine + " read the new file, viewed the new image and acknowledged steering in one native turn", flush=True)
        finally:
            if hub.status != "idle":
                await hub.kill()
            if hub.turn_task:
                await hub.turn_task
            runner.drop_hub(sid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=("claude", "codex", "opencode"))
    parser.add_argument("--model", help="explicit native model; otherwise use the engine default")
    parser.add_argument("--keep", action="store_true", help="retain private probe data for diagnosis")
    args = parser.parse_args()
    root = private_root("steering-live-")
    child = isolate(root, args.engine)
    os.environ["PUPPY_DATA"] = str(root / "puppy")
    try:
        asyncio.run(probe(args, root, child))
    finally:
        if args.keep:
            print("Private probe data: " + str(root), flush=True)
        else:
            shutil.rmtree(root)


if __name__ == "__main__":
    main()
