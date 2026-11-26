"""Real MCP startup through driver descriptors, without engines or quota.

Run from source and the headless zipapp, with no inherited Puppy environment
and a different child cwd. The production descriptors must select the node's
existing config, including its edited policies, rather than initializing data
in a project directory or trying to write inside the zipapp.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

BASE = Path(__file__).resolve().parent.parent

# Run inside the selected package's interpreter context: importing descriptors
# from the checkout would conceal mistakes in the artifact's package paths.
PROBE = r'''
import json
import os
from pathlib import Path
import subprocess

from puppy import (config, browser_agent, terminal_agent, vnc_agent,
                   spawn_agent, session_agent, task_agent, files_agent)
from puppy.drivers.claude import ClaudeDriver, MCP_INSTRUCTIONS_LIMIT
from puppy.drivers.codex import CodexDriver
from puppy.drivers.opencode import OpenCodeDriver

root = Path.cwd()
node_data = root / "node-data"
child_cwd = root / "model-workspace"
child_cwd.mkdir()
config.set_value("browser.enabled", True)
bridges = {
    "spawn": spawn_agent, "browser": browser_agent,
    "terminal": terminal_agent, "vnc": vnc_agent, "session": session_agent,
    "task": task_agent, "files": files_agent,
}
# each bridge's editable policy; the session and file bridges have none
policies = {"spawn": "spawn", "browser": "browser", "terminal": "terminal",
            "vnc": "vnc", "task": "tasks"}
descriptors = {}
for kind, module in bridges.items():
    if kind in policies:
        config.set_value("system_prompt." + policies[kind], "Node policy for " + kind)
    assert module.turn_mcp(1, "startup-test") is None
    # Socket/turn ownership is exercised by the bridge suites. This test only
    # opens their descriptor gate: initialization must not require a live turn.
    module._server = object()
    descriptors[kind + "_mcp"] = module.turn_mcp(1, "startup-test")

session = {"cwd": str(child_cwd)}
claude = ClaudeDriver().build_cmd(session, True, "probe", "startup-test", **descriptors)
claude_servers = json.loads(claude[claude.index("--mcp-config") + 1])["mcpServers"]
claude_guidance = claude[claude.index("--append-system-prompt") + 1]
# Claude Code cuts a server's instructions past 4096 characters, every role's
for kind, module in bridges.items():
    for role in (("main", "task") if kind == "task" else (None,)):
        text = module.instructions(role) if role else module.instructions()
        assert len(text) <= MCP_INSTRUCTIONS_LIMIT, (kind, role, len(text))
# each policy reaches Claude once, in its system prompt; guidance a server's own
# instructions carry is not repeated there
for kind in policies:
    assert claude_guidance.count("Node policy for " + kind) == 1, kind
assert session_agent.TOOL_INSTRUCTIONS not in claude_guidance
assert files_agent.ENGINE_GUIDANCE not in claude_guidance
# what only looks is allowed up front, so auto mode does not stop to ask; no action is
allowed = claude[claude.index("--allowedTools") + 1].split(",")
expected = []
for kind, module in bridges.items():
    tools = module.TOOLS["main"] if isinstance(module.TOOLS, dict) else module.TOOLS
    expected += ["mcp__{}__{}".format(module.SERVER_NAME, tool["name"]) for tool in tools
                 if tool["annotations"]["readOnlyHint"]]
assert sorted(allowed) == sorted(expected) and len(allowed) == len(set(allowed)), allowed
for name in ("mcp__puppy_vnc__screenshot", "mcp__puppy_browser__snapshot",
             "mcp__puppy_session__read", "mcp__puppy_spawn__wait", "mcp__puppy_tasks__changes"):
    assert name in allowed, name
for name in ("mcp__puppy_vnc__click", "mcp__puppy_browser__navigate", "mcp__puppy_session__send",
             "mcp__puppy_spawn__spawn", "mcp__puppy_files__share_file",
             "mcp__puppy_terminal__snapshot", "mcp__puppy_terminal__type",
             "mcp__puppy_tasks__new_task"):
    assert name not in allowed, name
task_role = task_agent.turn_mcp(1, "startup-test", role="task")
assert task_role["read_only_tools"] == ["status", "tasks", "read", "wait"], task_role
codex = CodexDriver().build_cmd(session, True, "probe", "startup-test", **descriptors)
codex_servers = {}
for index, arg in enumerate(codex):
    if arg != "-c":
        continue
    key, value = codex[index + 1].split("=", 1)
    _, name, field = key.split(".", 2)
    server = codex_servers.setdefault(name, {"env": {}})
    if field.startswith("env."):
        server["env"][field[4:]] = json.loads(value)
    else:
        server[field] = json.loads(value)
opencode = OpenCodeDriver().turn_context(
    session, True, "probe", "startup-test", **descriptors)["mcp_servers"]
opencode_servers = {server["name"]: dict(
    server, env={item["name"]: item["value"] for item in server["env"]})
    for server in opencode}

# what each engine allows one tool call, in every bridge's environment: the
# waits of the spawn and task bridges size themselves by it (bridge_calls)
long_waits = {"spawn": spawn_agent.CALL_SECONDS, "task": task_agent.CALL_SECONDS}
for kind, module in bridges.items():
    name = module.SERVER_NAME
    assert claude_servers[name]["env"]["PUPPY_MCP_CALL_LIMIT"] == "0", kind
    assert claude_servers[name]["env"]["PUPPY_MCP_PROGRESS_RESETS"] == "0", kind
    assert codex_servers[name]["tool_timeout_sec"] == long_waits.get(kind, 70), kind
    assert codex_servers[name]["env"]["PUPPY_MCP_CALL_LIMIT"] == \
        str(long_waits.get(kind, 70)), kind
    assert opencode_servers[name]["env"]["PUPPY_MCP_CALL_LIMIT"] == "60", kind
    assert opencode_servers[name]["env"]["PUPPY_MCP_PROGRESS_RESETS"] == "1", kind
assert spawn_agent.CALL_SECONDS >= spawn_agent.LONGEST_CALL + 10
# the pictures each engine's model reads at their own size, for the remote
# screen's reductions: Claude's, Codex's original detail (asked for by the
# picture itself), and for OpenCode the provider's - only an Anthropic model is
# known to read Claude's, every other provider gets what all of them read
for kind, module in bridges.items():
    name = module.SERVER_NAME
    assert claude_servers[name]["env"]["PUPPY_IMAGE_LIMITS"] == "2000 3750000 0", kind
    assert "PUPPY_IMAGE_DETAIL" not in claude_servers[name]["env"], kind
    assert codex_servers[name]["env"]["PUPPY_IMAGE_LIMITS"] == "6000 10240000 0", kind
    assert codex_servers[name]["env"]["PUPPY_IMAGE_DETAIL"] == "original", kind
    assert opencode_servers[name]["env"]["PUPPY_IMAGE_LIMITS"] == \
        " ".join(str(value) for value in OpenCodeDriver().model_picture_limits(session)), kind
    assert "PUPPY_IMAGE_DETAIL" not in opencode_servers[name]["env"], kind
for model, limits in (("anthropic/claude-sonnet-5-5", (2000, 3750000, 0)),
                      ("openai/gpt-6", (2000, 3750000, 768)),
                      ("google/gemini-4-pro", (2000, 3750000, 768)),
                      ("anthropic", (2000, 3750000, 768)), ("", (2000, 3750000, 768))):
    assert OpenCodeDriver().model_picture_limits({"model": model}) == limits, model
assert task_agent.CALL_SECONDS >= task_agent.LONGEST_CALL + 10

config_bytes = (node_data / "config.json").read_bytes()
wire = "\n".join(json.dumps(message) for message in (
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18"}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
)) + "\n"
for engine, servers in (("codex", codex_servers), ("claude", claude_servers),
                        ("opencode", opencode_servers)):
    assert set(servers) == {module.SERVER_NAME for module in bridges.values()}
    for kind, module in bridges.items():
        server = servers[module.SERVER_NAME]
        # Deliberately omit inherited PUPPY_DATA/PYTHONPATH. Only the values
        # serialized by the real driver may locate the package and config.
        env = {"HOME": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
        env.update(server["env"])
        output = subprocess.check_output(
            [server["command"]] + server["args"], input=wire,
            env=env, cwd=str(child_cwd), text=True, timeout=10)
        replies = [json.loads(line) for line in output.splitlines()]
        assert len(replies) == 2, (engine, kind, replies)
        assert "result" in replies[0], (engine, kind, replies[0])
        assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
        instructions = replies[0]["result"]["instructions"]
        assert instructions == module.instructions(), (engine, kind)
        if kind in policies:
            assert "Node policy for " + kind not in instructions, (engine, kind)
        # the task bridge serves Main's tools by default; a task's are its own
        tools = module.TOOLS["main"] if isinstance(module.TOOLS, dict) else module.TOOLS
        assert {tool["name"] for tool in replies[1]["result"]["tools"]} == {
            tool["name"] for tool in tools}
        assert server["env"]["PUPPY_DATA"] == str(node_data)
        assert (node_data / "config.json").read_bytes() == config_bytes
        assert list(child_cwd.iterdir()) == [], (engine, kind)
# every engine still hears each policy. Codex, like Claude Code, shows each
# server's instructions itself (as its tool namespace's description, probed on
# 0.172.0), so the session and file bridges' guidance - those very texts - is
# not repeated in its prompt; OpenCode, unprobed, still gets them there
for engine, text in (
        ("codex", CodexDriver().turn_context(session, True, "probe", "startup-test",
                                             **descriptors)["prompt"]),
        ("opencode", json.dumps(OpenCodeDriver().build_env(
            session, True, "probe", "startup-test", **descriptors)))):
    for kind in policies:
        assert json.dumps("Node policy for " + kind)[1:-1] in text, (engine, kind)
    for guidance in (session_agent.TOOL_INSTRUCTIONS, files_agent.ENGINE_GUIDANCE):
        present = json.dumps(guidance.split(". ")[0])[1:-1] in text
        assert present == (engine == "opencode"), (engine, guidance[:40])
assert "--allowedTools" not in codex
print("MCP startup: seven bridges through all three drivers passed")
'''


def exercise_mcp_startup(package_path: Path, temp_root: Path) -> None:
    temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = dict(os.environ)
    env.update(PYTHONPATH=str(package_path.resolve()), PUPPY_DATA="node-data",
               HOME=str(temp_root.resolve()), PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, "-c", PROBE], cwd=str(temp_root),
                   env=env, check=True, timeout=90)


def main() -> None:
    tests_root = BASE / "data" / "tests"
    tests_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="mcp-startup-", dir=str(tests_root)) as tmp:
        root = Path(tmp)
        exercise_mcp_startup(BASE, root / "source")
        artifact = root / "release" / "puppy-backend.pyz"
        subprocess.run([sys.executable, str(BASE / "backend" / "build.py"),
                        "--output", str(artifact)], check=True)
        exercise_mcp_startup(artifact, root / "packaged")


if __name__ == "__main__":
    main()
