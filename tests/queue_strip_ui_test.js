/* Run with node tests/queue_strip_ui_test.js. The queue strip's pending
   changes against the fake DOM: every field of a queued model, effort,
   permission or Fast change names both sides ("Effort Max → Medium"), read
   against what is in force where the row stands - the session's own
   settings for the first change, that change's result for the one behind
   it, and the whole queue for a held row, which re-sends to the queue's
   end; an engine switch names both engines and reads each side with its own
   engine's catalog; a default on either side is called that; a tool row
   keeps its name. No browser, engine, network or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { FakeDocument } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}

const document = new FakeDocument();
const icon = () => document.createElement("svg");
const state = {
  backends: [], engCache: {},
  engines: [
    { key: "claude", label: "Claude", supports_fast_mode: false,
      model_options: [
        { value: "claude-fable-5-1", label: "Fable 5.1", aliases: ["fable"] },
        { value: "claude-opus-5", label: "Opus 5" },
        { value: "claude-sonnet-5", label: "Sonnet 5" },
      ],
      effort_options: [
        { value: "low", label: "Low" }, { value: "medium", label: "Medium" },
        { value: "high", label: "High" }, { value: "max", label: "Max" },
      ],
      permission_options: [
        { value: "auto", label: "Auto" }, { value: "acceptEdits", label: "Accept edits" },
        { value: "bypassPermissions", label: "Bypass all" },
      ] },
    { key: "codex", label: "Codex", supports_fast_mode: true,
      model_options: [
        { value: "gpt-5.6-sol", label: "GPT-5.6-Sol" }, { value: "gpt-5.5", label: "GPT-5.5" },
        { value: "gpt-5.5-mini", label: "GPT-5.5 Mini" },
      ],
      effort_options: [
        { value: "low", label: "Low" }, { value: "medium", label: "Medium" },
        { value: "high", label: "High" }, { value: "xhigh", label: "Extra high" },
      ],
      permission_options: [
        { value: "read-only", label: "Read only" },
        { value: "workspace-write", label: "Workspace write" },
        { value: "danger-full-access", label: "Full access" },
      ] },
  ],
};
state.engMap = Object.fromEntries(state.engines.map(e => [e.key, e]));
const context = vm.createContext({
  document, state, console, syncSessionBrowserChips() {},
  refreshIcon: icon, xIcon: icon, queueEditIcon: icon, queuePauseIcon: icon,
  backendSupportsQueueEdit: () => false, backendSupportsQueuePause: () => false,
  backendSupportsQueueReorder: () => false,
  sessionToolLabel: value => value === "compact" ? "Compact context" : "Session tool",
  splitAttachmentMarkers: text => ({ text, attachments: [] }),
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("const QUEUE_ROWS = ", "\n"),
  between("function engineInfo(", "function engineReady("),
  between("function engineModelOption(", "function engineOffersModel("),
  between("const headWord = ", '/* "5.6-Sol Max"'),
  between("function effectiveQueuedConfig(", "/* One workspace tab owns Main"),
  "class QueueHost {",
  "  constructor(box, bid, session) {",
  "    this.queueEl = box; this.tab = { bid, sid: 1 }; this.session = session;",
  "    this.queued = []; this.held = []; this.pausedQueue = []; this.queueOpen = true;",
  "    this.queueDrag = null; this.queueRevision = 0;",
  "  }",
  "  followingTail(fn) { this.paints = (this.paints || 0) + 1; fn(); }",
  "  cancelQueueDrag() {} wireQueueDropZone() {} syncQueueFade() {} syncQueueEditBusy() {}",
  "  setBackgroundTasks() {} updateHead() { this.heads = (this.heads || 0) + 1; }",
  between("  /* A queue entry is a prompt string", "  prepareQueueDrag(row, event) {"),
  /* the socket's session_meta frame, as the real handler takes it */
  "  handle(d) { switch (d.type) {",
  between('      case "session_meta": {', '      case "rate_limit": {'),
  "  } }",
  "}",
  "globalThis.QueueHost = QueueHost; globalThis.el = el;",
].join("\n"), context);

/* the strip's rows as their text, held rows first as the strip lists them */
function rows(host) {
  return host.queueEl.querySelectorAll(".q-item").map(row => ({
    marker: row.querySelector(".q-n").textContent,
    text: row.querySelector(".q-t").textContent,
    label: row.querySelector(".q-t").getAttribute("aria-label"),
    cfg: row.className.includes("q-cfg"),
  }));
}
function mount(session, queue, held = []) {
  const box = context.el("div", "queue-strip");
  document.body.appendChild(box);
  const host = new context.QueueHost(box, 0, session);
  host.renderQueue(queue, held, [], 1);
  return host;
}
const strip = (session, queue, held = []) => rows(mount(session, queue, held));
const claude = { engine: "claude", model: "claude-fable-5-1", effort: "max",
  permission_mode: "auto", fast_mode: false };

/* a change reads from the session's own settings: the strip in the report */
{
  const shown = strip(claude, [
    "next we want to optimize the print + screen hook",
    { kind: "config", key: "c1", engine: "claude", effort: "medium" },
    "build commit and push.",
  ]);
  assert.deepEqual(shown.map(r => [r.marker, r.text]), [
    ["1", "next we want to optimize the print + screen hook"],
    ["2", "Effort Max → Medium"],
    ["3", "build commit and push."],
  ]);
  assert.equal(shown[1].label, "Effort Max → Medium", "the row's name is its text");
  assert.ok(shown[1].cfg);
}

/* every field, with the catalog's labels on both sides */
{
  const [row] = strip(claude, [{ kind: "config", key: "c1", engine: "claude",
    model: "claude-sonnet-5", effort: "low", permission_mode: "bypassPermissions" }]);
  assert.equal(row.text,
    "Model Fable 5.1 → Sonnet 5 · Effort Max → Low · Permission Auto → Bypass all");
}

/* a spelling the catalog knows as an alias reads as its label; a value it
   does not know prints as typed; an empty value is the default */
{
  const [row] = strip({ ...claude, model: "fable", effort: "" }, [
    { kind: "config", key: "c1", engine: "claude", model: "claude-custom-9", effort: "high" }]);
  assert.equal(row.text, "Model Fable 5.1 → claude-custom-9 · Effort default → High");
  const [back] = strip({ ...claude, effort: "high" }, [
    { kind: "config", key: "c1", engine: "claude", model: "", effort: "" }]);
  assert.equal(back.text, "Model Fable 5.1 → default · Effort High → default");
}

/* the second change reads from the first's result, not the session's; a
   prompt between them changes nothing about that */
{
  const shown = strip(claude, [
    { kind: "config", key: "c1", engine: "claude", effort: "medium" },
    "carry on",
    { kind: "config", key: "c2", engine: "claude", effort: "high", model: "claude-opus-5" },
    { kind: "config", key: "c3", engine: "claude", effort: "low" },
  ]);
  assert.deepEqual(shown.map(r => r.text), [
    "Effort Max → Medium", "carry on",
    "Model Fable 5.1 → Opus 5 · Effort Medium → High",
    "Effort High → Low",
  ]);
}

/* Fast on codex reads on/off on both sides */
{
  const codex = { engine: "codex", model: "gpt-5.6-sol", effort: "high",
    permission_mode: "workspace-write", fast_mode: true };
  const shown = strip(codex, [
    { kind: "config", key: "c1", engine: "codex", fast_mode: "off" },
    { kind: "config", key: "c2", engine: "codex", fast_mode: "on", model: "gpt-5.5-mini" },
  ]);
  assert.deepEqual(shown.map(r => r.text), [
    "Fast on → off",
    "Model 5.6-Sol → 5.5 Mini · Fast off → on",
  ]);
}

/* an engine switch names both engines, and reads each side of every setting
   with its own engine's catalog - Fast is named when either engine has it */
{
  const codex = { engine: "codex", model: "gpt-5.6-sol", effort: "xhigh",
    permission_mode: "danger-full-access", fast_mode: true };
  const [row] = strip(codex, [{ kind: "engine", key: "e1", engine: "claude",
    model: "claude-fable-5-1", effort: "max", permission_mode: "acceptEdits", fast_mode: "off" }]);
  assert.equal(row.text, "Engine Codex → Claude · Model 5.6-Sol → Fable 5.1 · " +
    "Effort Extra high → Max · Permission Full access → Accept edits · Fast on → off");
  const [back] = strip(claude, [{ kind: "engine", key: "e1", engine: "codex",
    model: "gpt-5.5", effort: "medium", permission_mode: "read-only", fast_mode: "off" }]);
  assert.equal(back.text, "Engine Claude → Codex · Model Fable 5.1 → 5.5 · " +
    "Effort Max → Medium · Permission Auto → Read only · Fast off → off");
  /* a switch to an engine's empty defaults still names what it takes over
     from; a switch carrying nothing at all is the engine line alone */
  const [bare] = strip(claude, [{ kind: "engine", key: "e1", engine: "codex",
    model: "", effort: "", permission_mode: "", fast_mode: "off" }]);
  assert.equal(bare.text, "Engine Claude → Codex · Model Fable 5.1 → default · " +
    "Effort Max → default · Permission Auto → default · Fast off → off");
  const [alone] = strip({ engine: "claude" }, [{ kind: "engine", key: "e1", engine: "opencode" }]);
  assert.equal(alone.text, "Engine Claude → opencode");
}

/* a change queued behind a switch reads from the switch's target */
{
  const shown = strip(claude, [
    { kind: "engine", key: "e1", engine: "codex", model: "gpt-5.5", effort: "medium",
      permission_mode: "workspace-write", fast_mode: "off" },
    { kind: "config", key: "c1", engine: "codex", effort: "high", fast_mode: "on" },
  ]);
  assert.equal(shown[1].text, "Effort Medium → High · Fast off → on");
}

/* a held row re-sends to the queue's end, so it reads from what the whole
   queue leaves in force; with nothing queued that is the session itself */
{
  const shown = strip(claude,
    [{ kind: "config", key: "c1", engine: "claude", effort: "medium" }, "then this"],
    [{ kind: "config", key: "h1", engine: "claude", effort: "low", model: "claude-opus-5" }]);
  assert.deepEqual(shown.map(r => [r.marker, r.text]), [
    ["!", "Model Fable 5.1 → Opus 5 · Effort Medium → Low"],
    ["1", "Effort Max → Medium"],
    ["2", "then this"],
  ]);
  assert.equal(shown[0].label, "held after an interruption · Model Fable 5.1 → Opus 5 · Effort Medium → Low");
  const [idle] = strip(claude, [],
    [{ kind: "config", key: "h1", engine: "claude", effort: "low" }]);
  assert.equal(idle.text, "Effort Max → Low");
}

/* a tool row keeps its name, and a change with nothing in it its placeholder */
{
  const shown = strip(claude, [
    { kind: "tool", key: "t1", tool: "compact" },
    { kind: "config", key: "c1", engine: "claude" },
  ]);
  assert.deepEqual(shown.map(r => r.text), ["Compact context", "Setting change"]);
}

/* before the session has loaded, the from side is the default */
{
  const [row] = strip(null, [{ kind: "config", key: "c1", engine: "claude", effort: "medium" }]);
  assert.equal(row.text, "Effort default → Medium");
}

/* the session's settings landing without a queue broadcast - a change
   applied to the idle session, a switch applied at once - repaint what a
   held row changes from; a session_meta that moves nothing a row reads (a
   rename, a colour) leaves the strip alone, as does one with only prompts
   and tool rows on it */
{
  const host = mount(claude, [], [{ kind: "config", key: "h1", engine: "claude", effort: "low" }]);
  assert.equal(rows(host)[0].text, "Effort Max → Low");
  const paints = host.paints;
  host.handle({ type: "session_meta", session: { ...claude, name: "renamed", color: "#f00" } });
  assert.equal(host.paints, paints, "a rename repaints nothing on the strip");
  assert.equal(host.heads, 1, "the head is painted as before");
  host.handle({ type: "session_meta", session: { ...claude, effort: "medium" } });
  assert.equal(host.paints, paints + 1, "a moved setting repaints the strip");
  assert.equal(rows(host)[0].text, "Effort Medium → Low");
  host.handle({ type: "session_meta", session: { ...claude, engine: "codex", model: "gpt-5.5",
    effort: "high", permission_mode: "workspace-write", fast_mode: true } });
  assert.equal(rows(host)[0].text, "Effort High → Low", "a switch applied at once moves the from side");
  const prompts = mount(claude, ["one", { kind: "tool", key: "t1", tool: "compact" }], ["two"]);
  const before = prompts.paints;
  prompts.handle({ type: "session_meta", session: { ...claude, effort: "medium" } });
  assert.equal(prompts.paints, before, "prompts and tool rows read nothing from the session");
}

console.log("PASS: queued changes name both sides of every field against what is in force where they stand");
