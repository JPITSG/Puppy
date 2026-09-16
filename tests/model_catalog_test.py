#!/usr/bin/env python3
"""No-turn tests for dynamic engine model discovery and cache semantics."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from puppy.drivers import base  # noqa: E402
from puppy.drivers.base import Driver, ModelCatalogResult  # noqa: E402
from puppy.drivers.claude import (  # noqa: E402
    ClaudeDriver,
    _read_model_catalog as read_claude_catalog,
    parse_model_catalog as parse_claude_catalog,
)
from puppy.drivers.codex import (  # noqa: E402
    CodexDriver,
    _read_model_catalog as read_codex_catalog,
    parse_model_catalog as parse_codex_catalog,
)
from puppy.drivers.opencode import OpenCodeDriver  # noqa: E402


def values(options):
    return [item["value"] for item in options]


def write_executable(path: Path, source: str) -> None:
    path.write_text("#!{}\n{}".format(sys.executable, source), encoding="utf-8")
    path.chmod(0o755)


class FakeCatalogDriver(Driver):
    key = "catalog-test"
    label = "Catalog test"
    dynamic_model_options = True
    allow_custom_model = False

    def __init__(self):
        self.calls = []
        self.started = None
        self.release = None
        self.fail = False
        self.serial = 0

    def _fallback_model_options(self):
        return [
            {"value": "", "label": "Default"},
            {"value": "fallback", "label": "Fallback"},
        ]

    async def _discover_model_options(self, force: bool):
        self.calls.append(force)
        if self.started is not None and len(self.calls) == 1:
            self.started.set()
            await self.release.wait()
        if self.fail:
            raise RuntimeError("catalog unavailable")
        self.serial += 1
        return ModelCatalogResult([
            {"value": "", "label": "Default", "effort_options": [
                {"value": "", "label": "Default"}]},
            {"value": "model-{}".format(self.serial), "label": "Model {}".format(
                self.serial), "effort_options": [
                    {"value": "", "label": "Default"},
                    {"value": "high", "label": "High"}]},
        ], source="engine")

    def resolved_binary(self) -> str:
        return "/fake/catalog-test"

    async def status(self) -> dict:
        return {
            "installed": True, "version": "1.0", "auth": "ok", "detail": "",
            "version_checked_at": 1.0, "latest_version": "1.0",
            "update_available": False, "latest_checked_at": 1.0,
            "latest_check_error": "", "upgrade_supported": False,
            "upgrade_state": "idle", "upgrade_result": None,
        }


def check_parsers_and_turn_ingest() -> None:
    claude = parse_claude_catalog([
        {"value": "default", "displayName": "Default (recommended)",
         "resolvedModel": "vendor-default", "supportsEffort": True,
         "supportedEffortLevels": ["high", "low"]},
        {"value": "future", "displayName": "Future",
         "supportsEffort": False},
        {"value": "future", "displayName": "Duplicate"},
    ])
    assert values(claude) == ["", "future"]
    assert values(claude[0]["effort_options"]) == ["", "low", "high"]
    assert values(claude[1]["effort_options"]) == [""]
    assert "vendor-default" in claude[0]["hint"]
    assert claude[0]["resolved_model"] == "vendor-default"

    # 2.1.263's picker says fable; older saved requests say fable[1m].
    # Alternate request spellings belong to the same row without rewriting
    # the saved request or borrowing another model's effort levels.
    renamed = parse_claude_catalog([
        {"value": "default", "resolvedModel": "vendor-current"},
        {"value": "fable", "displayName": "Fable",
         "resolvedModel": "claude-fable-5-1",
         "supportedEffortLevels": ["high", "max"]},
        {"value": "future", "displayName": "Future",
         "resolvedModel": "vendor-current", "supportedEffortLevels": ["low"]},
        {"value": "future[1m]", "displayName": "Future extended",
         "resolvedModel": "vendor-current[1m]", "supportedEffortLevels": ["max"]},
    ])
    assert "aliases" not in renamed[0]  # default remains engine-owned
    assert "fable[1m]" in renamed[1]["aliases"]
    assert "claude-fable-5-1" in renamed[1]["aliases"]
    aliases = ClaudeDriver()
    aliases._model_catalog_state().ingest(renamed)
    assert values(aliases.effort_options_for_model("fable[1m]")) == ["", "high", "max"]
    assert values(aliases.effort_options_for_model("future")) == ["", "low"]
    assert values(aliases.effort_options_for_model("future[1m]")) == ["", "max"]
    assert values(aliases.effort_options_for_model("vendor-current[1m]")) == ["", "max"]
    assert values(aliases.effort_options_for_model("vendor-current")) == ["", "low"]
    assert aliases.model_request_matches("future[1m]", "vendor-current") is True
    assert aliases.model_request_matches("future[1m]", "vendor-other") is False

    # The catalog already returned by a real turn is useful evidence too and
    # must update the same last-known-good state as the no-turn probe.
    driver = ClaudeDriver()
    ctx = driver.turn_context({}, True, "hello", "pin")
    assert driver.parse_line(json.dumps({
        "type": "control_response",
        "response": {"subtype": "success", "request_id": "init_1",
                     "response": {"models": [
                         {"value": "default", "displayName": "Default"},
                         {"value": "turn-new[2m]", "displayName": "Turn New",
                          "resolvedModel": "vendor-future-9[2m]",
                          "supportedEffortLevels": ["max"]},
                     ]}},
    }), ctx) == []
    assert ctx["control_ready"] is True
    assert values(driver.model_options()) == ["", "turn-new[2m]"]
    assert driver.model_catalog_source() == "turn"
    # No family or version knowledge is needed: the live catalog maps an
    # invented future alias, and the driver tolerates the CLI/provider forms
    # with and without their capacity selector.
    assert driver.model_request_matches(
        "turn-new[2m]", "vendor-future-9", ctx) is True
    assert driver.models_equivalent(
        "vendor-future-9[2m]", "vendor-future-9", ctx) is True
    assert driver.model_request_matches(
        "turn-new[2m]", "vendor-other-1", ctx) is False
    assert driver.models_equivalent(
        "vendor-future-9", "vendor-other-1", ctx) is False

    # The picker's spelling is not the model. Claude Code spells the current
    # model as it was requested and an org-gated row from a persisted cache,
    # so one read says fable[1m] and the next claude-fable-5-1[1m] for the
    # same resolved model: every spelling once shown for it stays its alias.
    learned = ClaudeDriver()
    learned._model_catalog_state().succeeded(ModelCatalogResult(parse_claude_catalog([
        {"value": "default", "resolvedModel": "vendor-current"},
        {"value": "fable[1m]", "displayName": "Fable",
         "resolvedModel": "claude-fable-5-1", "supportedEffortLevels": ["high", "max"]},
        {"value": "sonnet", "displayName": "Sonnet", "resolvedModel": "claude-sonnet-5"},
    ]), source="engine"), forced=False)
    learned._model_catalog_state().succeeded(ModelCatalogResult(parse_claude_catalog([
        {"value": "default", "resolvedModel": "vendor-current"},
        {"value": "claude-fable-5-1[1m]", "displayName": "Fable",
         "resolvedModel": "claude-fable-5-1", "supportedEffortLevels": ["high", "max"]},
        {"value": "sonnet", "displayName": "Sonnet", "resolvedModel": "claude-sonnet-5"},
    ]), source="engine"), forced=False)
    fable = learned.model_option("fable[1m]")
    assert fable is not None and fable["value"] == "claude-fable-5-1[1m]"
    assert set(fable["aliases"]) >= {"fable[1m]", "fable", "claude-fable-5-1"}
    assert values(learned.effort_options_for_model("fable[1m]")) == ["", "high", "max"]
    assert learned.model_option("claude-sonnet-5")["value"] == "sonnet"
    # A spelling the new catalog names as a row of its own belongs to that
    # row; nothing inherited may claim it or that row's resolved model.
    learned._model_catalog_state().succeeded(ModelCatalogResult(parse_claude_catalog([
        {"value": "default", "resolvedModel": "vendor-current"},
        {"value": "fable", "displayName": "Fable", "resolvedModel": "claude-fable-5-1"},
        {"value": "fable[1m]", "displayName": "Fable 1M",
         "resolvedModel": "claude-fable-5-1[1m]"},
    ]), source="engine"), forced=False)
    assert learned.model_option("fable[1m]")["value"] == "fable[1m]"
    assert learned.model_option("claude-fable-5-1[1m]")["value"] == "fable[1m]"
    assert "fable[1m]" not in learned.model_option("fable")["aliases"]
    assert "claude-fable-5-1[1m]" not in learned.model_option("fable")["aliases"]
    assert "claude-fable-5-1" in learned.model_option("fable")["aliases"]

    # A turn's picker is evidence, never the picker: a turn on another model
    # reports Fable under the CLI's cached spelling. The probe's row keeps its
    # name and gains that spelling, a model the list never named is added,
    # and the next probe drops it again while the learned spelling survives.
    merged = ClaudeDriver()
    merged._model_catalog_state().succeeded(ModelCatalogResult(parse_claude_catalog([
        {"value": "default", "resolvedModel": "vendor-current"},
        {"value": "fable[1m]", "displayName": "Fable",
         "resolvedModel": "claude-fable-5-1", "supportedEffortLevels": ["high", "max"]},
        {"value": "sonnet", "displayName": "Sonnet", "resolvedModel": "claude-sonnet-5"},
    ]), source="engine"), forced=False)
    turn_ctx = merged.turn_context({"model": "sonnet"}, True, "hello", "pin-2")
    assert merged.parse_line(json.dumps({
        "type": "control_response",
        "response": {"subtype": "success", "request_id": "init_1",
                     "response": {"models": [
                         {"value": "default", "resolvedModel": "vendor-current"},
                         {"value": "claude-fable-5-1[1m]", "displayName": "Fable",
                          "resolvedModel": "claude-fable-5-1",
                          "supportedEffortLevels": ["low"]},
                         {"value": "sonnet", "displayName": "Sonnet",
                          "resolvedModel": "claude-sonnet-5"},
                         {"value": "opus", "displayName": "Opus",
                          "resolvedModel": "claude-opus-5"},
                     ]}},
    }), turn_ctx) == []
    assert values(merged.model_options()) == ["", "fable[1m]", "sonnet", "opus"]
    assert merged.model_catalog_source() == "engine"
    assert merged.model_option("claude-fable-5-1[1m]")["value"] == "fable[1m]"
    assert values(merged.effort_options_for_model("claude-fable-5-1[1m]")) == ["", "high", "max"]
    assert merged.model_option("opus")["resolved_model"] == "claude-opus-5"
    merged._model_catalog_state().succeeded(ModelCatalogResult(parse_claude_catalog([
        {"value": "default", "resolvedModel": "vendor-current"},
        {"value": "fable[1m]", "displayName": "Fable",
         "resolvedModel": "claude-fable-5-1", "supportedEffortLevels": ["high", "max"]},
        {"value": "sonnet", "displayName": "Sonnet", "resolvedModel": "claude-sonnet-5"},
    ]), source="engine"), forced=False)
    assert values(merged.model_options()) == ["", "fable[1m]", "sonnet"]
    assert merged.model_option("opus") is None
    assert merged.model_option("claude-fable-5-1[1m]")["value"] == "fable[1m]"

    # A spelling the CLI resolved joins the row standing for that model, by
    # resolved model or by value; one it resolved to an unlisted model stays
    # hand-typed and changes nothing.
    state = merged._model_catalog_state()
    assert state.learn_spelling("Fable-Request", "claude-fable-5-1") is True
    assert merged.model_option("Fable-Request")["value"] == "fable[1m]"
    assert state.learn_spelling("sonnet-again", "sonnet") is True
    assert merged.model_option("sonnet-again")["value"] == "sonnet"
    kept = merged.model_options()
    assert state.learn_spelling("claude-foo", "claude-foo") is False
    assert state.learn_spelling("", "claude-sonnet-5") is False
    assert merged.model_options() == kept
    assert merged.model_option("claude-foo") is None

    # Contract observed from Claude Code 2.1.260. These names live only in the
    # regression: production follows value -> resolvedModel from initialize.
    current = parse_claude_catalog([
        {"value": "opus[1m]", "displayName": "Opus (1M context)",
         "resolvedModel": "claude-opus-5[1m]"},
        {"value": "fable[1m]", "displayName": "Fable",
         "resolvedModel": "claude-fable-5-1"},
    ])
    current_ctx = {"model_options": current}
    assert driver.model_request_matches(
        "opus[1m]", "claude-opus-5[1m]", current_ctx) is True
    assert driver.model_request_matches(
        "opus[1m]", "claude-opus-5", current_ctx) is True
    assert driver.model_request_matches(
        "fable[1m]", "claude-fable-5-1", current_ctx) is True
    assert driver.model_request_matches(
        "fable[1m]", "claude-opus-5", current_ctx) is False
    # Full model ids remain valid even when they are not picker rows.
    assert driver.model_request_matches(
        "claude-opus-5[1m]", "claude-opus-5", {"model_options": []}) is True
    stream_ctx = driver.turn_context(
        {"model": "opus[1m]"}, True, "hello", "current-pin")
    stream_ctx["model_options"] = current
    init_actions = driver.parse_line(json.dumps({
        "type": "system", "subtype": "init", "session_id": "current",
        "model": "claude-opus-5[1m]", "tools": [],
    }), stream_ctx)
    assert any(action["a"] == "model" for action in init_actions)
    assert driver.parse_line(json.dumps({
        "type": "assistant", "message": {
            "model": "claude-opus-5", "content": []},
    }), stream_ctx) == []

    codex = parse_codex_catalog([
        {"model": "model-a", "displayName": "Model A", "isDefault": True,
         "supportedReasoningEfforts": [
             {"reasoningEffort": "low", "description": "quick"},
             {"reasoningEffort": "max", "description": "deep"}],
         # Deliberately invented id: production must carry catalog data, not
         # know today's native service-tier spelling.
         "serviceTiers": [
             {"id": "speed-tier-v73", "name": "Fast",
              "description": "faster service"},
             {"id": "speed-tier-v73", "name": "duplicate"},
             {"id": "", "name": "broken"}]},
        {"model": "hidden", "displayName": "Hidden", "hidden": True},
        {"model": "model-b", "displayName": "Model B",
         "supportedReasoningEfforts": [
             {"reasoningEffort": "medium", "description": "balanced"}]},
    ])
    assert values(codex) == ["", "model-a", "model-b"]
    assert values(codex[0]["effort_options"]) == ["", "low", "max"]
    assert values(codex[1]["effort_options"]) == ["", "low", "max"]
    assert values(codex[2]["effort_options"]) == ["", "medium"]
    assert values(codex[0]["service_tiers"]) == ["speed-tier-v73"]
    assert values(codex[1]["service_tiers"]) == ["speed-tier-v73"]
    assert codex[2]["service_tiers"] == []
    codex_driver = CodexDriver()
    codex_driver._cache_file_options = codex
    # A cache left by another binary is display fallback, not proof that the
    # currently installed app-server accepts its optional turn field.
    assert codex_driver.fast_mode_tier("") == ""
    codex_driver._model_catalog_state().options = codex
    codex_driver._model_catalog_state().source = "engine"
    assert codex_driver.fast_mode_tier("") == "speed-tier-v73"
    assert codex_driver.fast_mode_tier("model-a") == "speed-tier-v73"
    assert codex_driver.fast_mode_tier("model-b") == ""


async def check_protocol_probes(root: Path) -> None:
    claude = root / "fake-claude"
    probe_log = root / "fake-claude.log"
    # The echo the real CLI makes (2.1.273): started with --model, its picker
    # carries a row spelled that way resolving to the model it names, or to
    # itself for a spelling it does not know.
    write_executable(claude, r'''
import json
import sys
assert "--no-session-persistence" in sys.argv
assert "--strict-mcp-config" in sys.argv
model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else ""
with open(%r, "a", encoding="utf-8") as handle:
    handle.write(model + "\n")
request = json.loads(sys.stdin.readline())
assert request["request"]["subtype"] == "initialize"
models = [
    {"value": "default", "displayName": "Default"},
    {"value": "released-today", "displayName": "Released Today",
     "resolvedModel": "vendor-today", "supportedEffortLevels": ["high", "max"]},
]
if model and model.casefold() not in ("released-today",):
    known = model.casefold() in ("today[1m]", "vendor-today")
    models.append({"value": model, "displayName": "Released Today" if known else model,
                   "resolvedModel": "vendor-today" if known else model})
print(json.dumps({
    "type": "control_response",
    "response": {"subtype": "success", "request_id": request["request_id"],
                 "response": {"models": models}},
}), flush=True)
''' % str(probe_log))
    found = await read_claude_catalog(str(claude))
    assert values(found) == ["", "released-today"]
    assert values(found[1]["effort_options"]) == ["", "high", "max"]

    # Unlisted spellings are resolved by the CLI itself, once per catalog:
    # one that resolves to a listed model becomes its alias with its effort
    # levels, one the CLI does not know stays hand-typed, and a listed one
    # is never asked about.
    probe_log.write_text("", encoding="utf-8")
    driver = ClaudeDriver()
    driver.resolved_binary = lambda: str(claude)
    await driver.refresh_model_options(force=True)
    assert values(driver.model_options()) == ["", "released-today"]
    asked = lambda: probe_log.read_text(encoding="utf-8").splitlines()
    await driver.cover_model_spellings(["today[1m]", "claude-foo", "released-today", ""])
    assert asked() == ["", "today[1m]", "claude-foo"]
    assert driver.model_option("today[1m]")["value"] == "released-today"
    assert values(driver.effort_options_for_model("today[1m]")) == ["", "high", "max"]
    assert driver.model_option("claude-foo") is None
    assert driver.model_catalog_source() == "engine"
    await driver.cover_model_spellings(["claude-foo", "today[1m]"])
    assert asked() == ["", "today[1m]", "claude-foo"]
    # The next discovery keeps the learned spelling and grants one more ask.
    driver.invalidate_model_options()
    await driver.refresh_model_options()
    assert driver.model_option("today[1m]")["value"] == "released-today"
    await driver.cover_model_spellings(["claude-foo", "today[1m]"])
    assert asked() == ["", "today[1m]", "claude-foo", "", "claude-foo"]

    codex = root / "fake-codex"
    write_executable(codex, r'''
import json
import sys
assert sys.argv[1:] == ["app-server", "--stdio"]
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        print(json.dumps({"id": request["id"], "result": {}}), flush=True)
    elif method == "initialized":
        continue
    elif method == "model/list":
        cursor = request["params"].get("cursor")
        assert request["params"]["includeHidden"] is False
        if cursor is None:
            result = {"data": [{
                "model": "first", "displayName": "First", "isDefault": True,
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "high", "description": "high"}],
                "serviceTiers": [{"id": "next-fast-id", "name": "Fast",
                                  "description": "quick"}],
            }], "nextCursor": "page-2"}
        else:
            assert cursor == "page-2"
            result = {"data": [
                {"model": "second", "displayName": "Second",
                 "supportedReasoningEfforts": []},
                {"model": "secret", "displayName": "Secret", "hidden": True},
            ], "nextCursor": None}
        print(json.dumps({"id": request["id"], "result": result}), flush=True)
''')
    found = await read_codex_catalog(str(codex))
    assert values(found) == ["", "first", "second"]
    assert values(found[1]["effort_options"]) == ["", "high"]
    assert values(found[2]["effort_options"]) == [""]
    assert values(found[0]["service_tiers"]) == ["next-fast-id"]
    assert values(found[1]["service_tiers"]) == ["next-fast-id"]
    assert found[2]["service_tiers"] == []


async def check_probe_group_cleanup(root: Path) -> None:
    """A timed-out wrapper cannot strand a child that ignores the first INT."""
    pid_file = root / "probe-child.pid"
    probe = root / "hanging-probe"
    write_executable(probe, r'''
import subprocess
import sys
import time
child = subprocess.Popen([sys.executable, "-c",
    "import signal,time;signal.signal(signal.SIGINT,signal.SIG_IGN);time.sleep(60)"])
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.write(str(child.pid))
time.sleep(60)
''')
    driver = FakeCatalogDriver()
    assert await driver._run_quick(
        [str(probe), str(pid_file)], timeout=0.5) == ""
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    for _ in range(40):
        try:
            stat = Path("/proc/{}/stat".format(child_pid)).read_text(
                encoding="utf-8").split()
            alive = len(stat) > 2 and stat[2] != "Z"
        except (FileNotFoundError, ProcessLookupError):
            alive = False
        if not alive:
            break
        await asyncio.sleep(0.05)
    assert not alive, "timed-out probe left child {} running".format(child_pid)


async def check_shared_cache() -> None:
    old_ttl = base.MODEL_CATALOG_TTL_SECONDS
    old_retry = base.MODEL_CATALOG_RETRY_SECONDS
    old_force_floor = base.MODEL_CATALOG_FORCE_MIN_INTERVAL_SECONDS
    base.MODEL_CATALOG_TTL_SECONDS = 600
    base.MODEL_CATALOG_RETRY_SECONDS = (600,)
    base.MODEL_CATALOG_FORCE_MIN_INTERVAL_SECONDS = 0
    try:
        driver = FakeCatalogDriver()
        driver.started = asyncio.Event()
        driver.release = asyncio.Event()
        routine = asyncio.ensure_future(driver.refresh_model_options())
        await driver.started.wait()
        duplicate = asyncio.ensure_future(driver.refresh_model_options())
        forced = asyncio.ensure_future(driver.refresh_model_options(force=True))
        await asyncio.sleep(0)
        driver.release.set()
        await asyncio.gather(routine, duplicate, forced)
        # Ordinary callers coalesce, while a force arriving during an ordinary
        # read receives one stronger follow-up attempt.
        assert driver.calls == [False, True]
        assert values(driver.model_options()) == ["", "model-2"]

        previous = driver.model_options()
        driver.fail = True
        driver.invalidate_model_options()
        await driver.refresh_model_options(force=True)
        assert driver.model_options() == previous
        assert driver.model_catalog_error() == "catalog unavailable"
        assert driver.model_catalog_loaded() is True  # still has last-known-good
        attempts = len(driver.calls)
        await driver.refresh_model_options()
        assert len(driver.calls) == attempts  # failure back-off

        driver.fail = False
        await driver.refresh_model_options(force=True)
        assert len(driver.calls) == attempts + 1  # force bypasses back-off
        assert driver.model_catalog_error() == ""
        assert values(driver.model_options())[-1] == "model-3"

        # Default-only discoveries are failures and never erase a good list.
        async def empty_discovery(force):
            return ModelCatalogResult([{"value": "", "label": "Default"}])
        driver._discover_model_options = empty_discovery
        kept = driver.model_options()
        await driver.refresh_model_options(force=True)
        assert driver.model_options() == kept
        assert driver.model_catalog_error() == "engine reported no models"

        first_failure = FakeCatalogDriver()
        first_failure.fail = True
        await first_failure.refresh_model_options()
        assert first_failure.model_catalog_checked_at() is not None
        assert first_failure.model_catalog_loaded() is False
        first_failure.fail = False
        await first_failure.refresh_model_options(force=True)
        assert first_failure.model_catalog_loaded() is True
    finally:
        base.MODEL_CATALOG_TTL_SECONDS = old_ttl
        base.MODEL_CATALOG_RETRY_SECONDS = old_retry
        base.MODEL_CATALOG_FORCE_MIN_INTERVAL_SECONDS = old_force_floor


async def check_opencode_force_fallback() -> None:
    """Older binaries retry plainly but disclose that no network force ran."""
    driver = OpenCodeDriver()
    calls = []

    async def command(_binary: str, refresh: bool):
        calls.append(refresh)
        if refresh:
            return 2, "Unknown argument: --refresh"
        return 0, '''provider/new-model
{"id":"new-model","providerID":"provider","name":"New Model","variants":{}}
'''

    driver.resolved_binary = lambda: "/fake/opencode"
    driver._run_catalog_command = command
    await driver.refresh_model_options(force=True)
    assert calls == [True, False]
    assert values(driver.model_options()) == ["", "provider/new-model"]
    assert "cannot force its provider cache" in driver.model_catalog_note()
    assert driver.model_catalog_error() == ""


async def check_manual_refresh_route() -> None:
    """The Engines-card action must bypass the normal catalog TTL."""
    from puppy import web as web_module

    driver = FakeCatalogDriver()
    driver.key = "claude"  # one registered engine's persisted defaults row
    await driver.refresh_model_options()
    assert driver.calls == [False]
    originals = (
        web_module.all_drivers,
        web_module.cli_releases.refresh_if_due,
        web_module.db.meta_get,
        web_module.db.query,
    )

    async def release_refresh(_drivers, force=False):
        assert force is True

    try:
        web_module.all_drivers = lambda: [driver]
        web_module.cli_releases.refresh_if_due = release_refresh
        web_module.db.meta_get = lambda _key: None
        web_module.db.query = lambda _sql, _args=(): []
        response = await web_module.h_engines_refresh(SimpleNamespace(headers={}))
        payload = json.loads(response.text)
    finally:
        (web_module.all_drivers,
         web_module.cli_releases.refresh_if_due,
         web_module.db.meta_get,
         web_module.db.query) = originals
    assert driver.calls == [False, True]
    assert values(payload["engines"][0]["model_options"]) == ["", "model-2"]
    assert payload["engines"][0]["model_catalog_source"] == "engine"


def check_loop_affinity() -> None:
    """The registry singleton survives callers that create fresh event loops."""
    driver = FakeCatalogDriver()
    old_force_floor = base.MODEL_CATALOG_FORCE_MIN_INTERVAL_SECONDS
    base.MODEL_CATALOG_FORCE_MIN_INTERVAL_SECONDS = 0

    async def contend(force: bool) -> None:
        driver.calls = []
        driver.started = asyncio.Event()
        driver.release = asyncio.Event()
        first = asyncio.ensure_future(driver.refresh_model_options(force=force))
        await driver.started.wait()
        second = asyncio.ensure_future(driver.refresh_model_options(force=force))
        await asyncio.sleep(0)
        driver.release.set()
        await asyncio.gather(first, second)
        assert driver.calls == [force]

    try:
        asyncio.run(contend(False))
        driver.invalidate_model_options()
        asyncio.run(contend(True))
    finally:
        base.MODEL_CATALOG_FORCE_MIN_INTERVAL_SECONDS = old_force_floor


async def main() -> None:
    check_parsers_and_turn_ingest()
    with tempfile.TemporaryDirectory(prefix="puppy-model-catalog-") as tmp:
        await check_protocol_probes(Path(tmp))
        await check_probe_group_cleanup(Path(tmp))
    await check_shared_cache()
    await check_opencode_force_fallback()
    await check_manual_refresh_route()
    print("model catalog tests passed")


if __name__ == "__main__":
    check_loop_affinity()
    asyncio.run(main())
