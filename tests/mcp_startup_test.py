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

from puppy import config, browser_agent, terminal_agent, spawn_agent, session_agent
from puppy.drivers.claude import ClaudeDriver
from puppy.drivers.codex import CodexDriver
from puppy.drivers.opencode import OpenCodeDriver

root = Path.cwd()
node_data = root / "node-data"
child_cwd = root / "model-workspace"
child_cwd.mkdir()
config.set_value("browser.enabled", True)
bridges = {
    "spawn": spawn_agent, "browser": browser_agent,
    "terminal": terminal_agent, "session": session_agent,
}
descriptors = {}
for kind, module in bridges.items():
    if kind != "session":
        config.set_value("system_prompt." + kind, "Node policy for " + kind)
    assert module.turn_mcp(1, "startup-test") is None
    # Socket/turn ownership is exercised by the bridge suites. This test only
    # opens their descriptor gate: initialization must not require a live turn.
    module._server = object()
    descriptors[kind + "_mcp"] = module.turn_mcp(1, "startup-test")

session = {"cwd": str(child_cwd)}
claude = ClaudeDriver().build_cmd(session, True, "probe", "startup-test", **descriptors)
claude_servers = json.loads(claude[claude.index("--mcp-config") + 1])["mcpServers"]
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
        if kind != "session":
            assert "Node policy for " + kind in instructions, (engine, kind)
        assert {tool["name"] for tool in replies[1]["result"]["tools"]} == {
            tool["name"] for tool in module.TOOLS}
        assert server["env"]["PUPPY_DATA"] == str(node_data)
        assert (node_data / "config.json").read_bytes() == config_bytes
        assert list(child_cwd.iterdir()) == [], (engine, kind)
print("MCP startup: four bridges through all three drivers passed")
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
