/* Run with node tests/background_task_ui_test.js. Live counts and native
   background-task endings, with no browser or engine. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const {FakeDocument} = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
};
const document = new FakeDocument();
const icon = () => document.createElement("svg");
const context = vm.createContext({
  document, Map, console, choiceSvg: icon, toolIconNode: icon,
  xIcon: icon, checkIcon: icon, promptSpinnerNode: icon,
  linkifyInto: (node, text) => { node.textContent = text; return node; },
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function displayValue(", "function toolIconNode("),
  between("function toolStateInto(", "/* A side question"),
  between("/* Task endings stay visible", "const ATTACHMENT_PREVIEW_TYPES"),
  "class View {",
  between("  findEventNode(seq)", "  /* Dividers mark"),
  between("  buildEventNode(ev)", "  /* live streaming bubble */"),
  between("  atBottom() {", "  /* ---- outgoing ---- */"),
  "} globalThis.View = View;",
].join("\n"), context);
/* one row is taller than the 160px of slack atBottom allows, so a single box
   arriving is the difference between following the tail and losing it */
const ROW = 200, VIEWPORT = 300;
function view() {
  const v = new context.View();
  v.toolCards = {};
  v.backgroundTaskUpdates = new Map();
  v.orphanResults = new Map();
  v.inner = document.createElement("div");
  document.body.appendChild(v.inner);
  v.newestSeq = 0;
  /* Nothing here lays anything out, so the scroller is modelled: every box in
     the transcript is one row tall wherever it sits, so a card that grows in
     place moves the foot exactly as an appended node does. The real atBottom,
     scrollBottom and followingTail run against it. */
  const rows = () => v.inner.querySelectorAll(
    ".tool-card,.task-update,.msg,.tool-card.open .tb-label").length;
  v.scroll = {clientHeight: VIEWPORT, scrollTop: 0,
    get scrollHeight() { return rows() * ROW; }};
  return v;
}
const tool = (id, seq = 1) => ({seq, kind: "tool_use", data: {
  tool: "Bash", tool_use_id: id, input: {command: "node tests/example.js"},
}});
const result = (id, seq = 2) => ({seq, kind: "tool_result", data: {
  tool_use_id: id, content: "Command running in background", is_error: false,
}});
const notice = (status, id, seq = 3) => ({seq, kind: "info", data: {
  subtype: "task", task_id: "task-example", status,
  ...(id ? {tool_use_id: id} : {}),
  text: status === "completed" ? "node tests/example.js" :
    `Background task ${status}: node tests/example.js`,
}});
for (const [status, tone] of [["completed", "ok"], ["failed", "bad"], ["stopped", "warn"]]) {
  const v = view();
  [tool("one"), result("one"), tool("two", 4), notice(status, "one", 5)]
    .forEach(ev => v.renderEvent(ev, true));
  const card = v.toolCards.one, other = v.toolCards.two;
  const update = card.querySelector(".background-task");
  assert.ok(update, status);
  assert.equal(v.inner.children.length, 2, "the update belongs to its exact card");
  assert.equal(other.querySelector(".background-task"), null, "identical commands do not associate tools");
  assert.equal(update.querySelector(".task-update-label").textContent, `Background task ${status}`);
  assert.ok(update.querySelector(".task-update-label").classList.contains(tone));
  assert.equal(update.querySelector(".task-update-text").textContent, "node tests/example.js");
  assert.equal(update.parentNode, card, "the update stays outside the collapsed body");
  assert.equal(card.classList.contains("open"), false, "arrival does not open the card");
  assert.equal(card.classList.contains("err"), status === "failed");
  assert.ok(card.querySelector(".t-state").classList.contains(tone));
  assert.equal(v.findEventNode(5), update, "search finds the attached event itself");
  card.querySelector(".tool-head").onclick();
  assert.ok(card.classList.contains("open"));
  v.renderEvent(result("one", 6), true);
  assert.ok(card.querySelector(".t-state").classList.contains(tone), "a late launch result cannot overwrite task completion");
  assert.ok(card.classList.contains("open"), "updates preserve disclosure choice");
}

// Current records without native provenance remain fully readable. No
// migration, command matching, or pairing with the most recent tool.
const independent = view();
independent.renderEvent(tool("one"), false);
independent.renderEvent(notice("completed", null), false);
assert.equal(independent.inner.children.length, 2);
assert.equal(independent.toolCards.one.querySelector(".background-task"), null);
assert.equal(independent.inner.querySelectorAll(".info-line").length, 0);

// A history window can begin at the notice. Loading the earlier tool moves
// that same visible node into the card and keeps its search sequence.
const history = view();
history.renderEvent(notice("failed", "one", 9), false);
const original = history.findEventNode(9);
assert.equal(original.parentNode, history.inner);
history.renderEvent(tool("one"), false);
assert.equal(original.parentNode, history.toolCards.one);
assert.equal(history.inner.children.length, 1);
assert.equal(history.findEventNode(9), original);
assert.equal(history.newestSeq, 9);
history.renderEvent(result("one"), false);
assert.ok(history.toolCards.one.querySelector(".t-state").classList.contains("bad"));

const safe = view();
const unknown = notice("__proto__", null);
unknown.data.text = '<img src=x onerror="alert(1)">\n' + "x".repeat(1000);
safe.renderEvent(unknown, false);
assert.equal(safe.inner.querySelector(".task-update-label").textContent, "Background task updated");
assert.equal(safe.inner.querySelector(".task-update-text").textContent, unknown.data.text);
assert.equal(safe.inner.querySelector("img"), null, "engine text is not markup");

console.log("PASS: background-task labels, exact native tool association, visible folded updates, independent history, late results and search targets");

/* The tail follows a card that grows in place. An ending folded into a card
   already on screen adds its box to the transcript exactly as an appended node
   does, and the reader at the foot must be shown it - measured afterwards that
   growth is indistinguishable from having scrolled away, so the decision has
   to be sampled first and cannot be recovered. */
const tail = (rows) => {
  const v = view();
  for (let i = 0; i < rows; i++) v.renderEvent(tool(`t${i}`, i + 1), false);
  v.scroll.scrollTop = v.scroll.scrollHeight - v.scroll.clientHeight;  // at the foot
  assert.ok(v.atBottom(), "the reading starts on the tail");
  return v;
};

const folded = tail(4);
folded.renderEvent(notice("completed", "t0", 20), true);
assert.equal(folded.inner.children.length, 4, "the ending folded into its card");
assert.ok(folded.toolCards.t0.querySelector(".background-task"), "and is really there");
assert.ok(folded.atBottom(), "a card that grew in place keeps the reader on the tail");

const standalone = tail(4);
standalone.renderEvent(notice("completed", null, 20), true);
assert.equal(standalone.inner.children.length, 5, "no card to fold into");
assert.ok(standalone.atBottom(), "an appended ending follows the tail too");

const opened = tail(4);
opened.toolCards.t0.querySelector(".tool-head").onclick();   // the reader opens it
opened.scroll.scrollTop = opened.scroll.scrollHeight - opened.scroll.clientHeight;
opened.renderEvent(result("t0", 21), true);
assert.ok(opened.toolCards.t0.classList.contains("open"));
assert.ok(opened.atBottom(), "a result filled into an open card follows too");

const reading = tail(4);
reading.scroll.scrollTop = 0;   // scrolled up into the conversation
reading.renderEvent(notice("completed", "t0", 20), true);
assert.equal(reading.scroll.scrollTop, 0, "growth never yanks a reader off their place");

const detached = tail(4);
detached.detached = true;
const parked = detached.scroll.scrollTop;
detached.renderEvent(notice("completed", "t0", 20), true);
assert.equal(detached.scroll.scrollTop, parked,
  "a history window is never pulled to the tail");

const held = tail(4);
held.renderEvent(notice("completed", "t0", 20), true, false);
assert.ok(!held.atBottom(), "an explicit refusal to follow is still obeyed");
console.log("PASS: the tail follows content grown inside a card, and only when it was already following");

// Exercise the real socket dispatcher and lifecycle methods as well as the
// pill renderer: a history rebuild must never masquerade as live task state.
Object.assign(context, {
  tasksIcon: icon, globeIcon: icon, terminalIcon: icon,
  remoteStoppingMessage: () => "", noteSessionActivity() {},
  syncSessionBrowserChips() {}, state: {tabs: []},
});
vm.runInContext([
  between("function effectiveQueuedConfig(", "/* One workspace tab owns Main"),
  "class HeaderView {",
  between("  handle(d) {", "  /* A browser this session launched"),
  between("  syncBrowserChips() {", "  syncWorkspaceChip() {"),
  between("  updateRunState() {", "  setSteeringState(value) {"),
  between("  setReconnecting(value) {", "  setStatus(text) {"),
  /* session_meta asks whether the queue strip reads the session */
  between("  /* Whether a row on the strip reads", "  queueRow("),
  "} globalThis.HeaderView = HeaderView;",
].join("\n"), context);
function header(sid = 1, bid = 0) {
  const v = new context.HeaderView();
  v.tab = {sid, bid};
  v.session = {engine: "claude"};
  v.status = "running";
  v.root = document.createElement("div");
  v.root.innerHTML = '<div class="chat-meta-scroll"><span class="chip eng"></span>' +
    '<span class="chip cwd"></span><span class="chat-status"></span></div>';
  v.sendBtn = document.createElement("button");
  v.queueBtn = document.createElement("button");
  v.composer = {syncUploadButton() {}};
  v.steering = v.sideQuestion = {};
  v.scrollBottom = v.syncHeadOverflow = v.updateSteerControl = v.renderStatus =
    v.updateApprovalControl = v.setSteeringState = v.setSideQuestionState =
    v.initializeDraft = v.rebuildTranscript = v.mergeSkipped = v.renderQueue =
    v.updateHead = v.syncLiveStatus = v.hideApproval = v.clearLive =
    v.renderEvent = v.syncTailPill = () => {};
  v.setStatus = text => { v.statusText = text; };
  v.skippedEvents = [];
  v.atBottom = () => false;
  return v;
}
const live = (...ids) => ({tasks: ids.map(id => ({id, type: "future-kind"}))});
const chip = v => v.root.querySelector(".chip.background-tasks");
const snapshot = (background_tasks, engine = "claude", status = "running") => ({
  type: "snapshot", session: {engine}, status, events: [], background_tasks,
});
const a = header(), b = header(2, 7);
a.handle(snapshot(live("a", "b")));
assert.equal(chip(a).textContent, "2 tasks", "attach restores the live count before any new event");
assert.equal(chip(a).getAttribute("aria-label"), "2 background tasks running");
assert.equal(chip(a).tagName, "SPAN", "the count does not promise a dialog or action");
assert.equal(chip(b), null, "each conversation/backend owns its own count");
const stable = chip(a);
a.handle({type: "background_tasks", ...live("a", "a", "b"), waiting: true, text: "Waiting"});
assert.equal(chip(a), stable, "repeats update in place without duplicates or focus changes");
assert.equal(chip(a).textContent, "2 tasks", "native identities deduplicate the set");
assert.equal(a.statusText, "Waiting", "the existing waiting status still works");
a.detached = true;
a.handle({type: "background_tasks", ...live("b"), waiting: false});
assert.equal(chip(a).textContent, "1 task", "live updates work while reading old history");
a.handle({type: "event", event: notice("completed", "b")});
assert.equal(chip(a).textContent, "1 task", "transcript endings cannot change the live list");
a.detached = false;
a.handle({type: "background_tasks", ...live()});
assert.equal(chip(a), null, "empty replacement removes the pill");
for (const value of [undefined, null, {}, {tasks: {}}, {tasks: "a"},
  {tasks: [null]}, {tasks: [{id: 4}]}, {tasks: [{id: " "}]},
  {tasks: [{id: "valid"}, {}]}]) {
  a.handle(snapshot(live("a")));
  a.handle(snapshot(value));
  assert.equal(chip(a), null, "unknown or malformed state hides the count");
}
for (const engine of ["claude", "codex", "opencode"]) {
  a.handle(snapshot(undefined, engine));
  assert.equal(chip(a), null, "engine name alone proves no background work");
  a.handle(snapshot(live(), engine));
  assert.equal(chip(a), null, "unsupported/idle drivers never show a zero counter");
}
a.handle(snapshot(live("a"), "future-engine"));
assert.equal(chip(a).textContent, "1 task", "normalized evidence needs no engine allowlist");
a.setReconnecting(true);
assert.equal(chip(a), null);
a.setReconnecting(false);
assert.equal(chip(a), null, "socket open cannot resurrect stale state");
a.handle(snapshot(live("a", "b")));
assert.equal(chip(a).textContent, "2 tasks", "fresh snapshot restores after reconnect");
for (const continued of [false, true]) {
  a.handle(snapshot(live("a")));
  a.handle({type: "turn_done", continued});
  assert.equal(chip(a), null, "completion clears even when queued work continues");
}
a.handle(snapshot(live("a")));
a.handle({type: "session_meta", session: {engine: "opencode"}});
assert.equal(chip(a), null, "an engine switch cannot retain the previous engine's count");
a.handle(snapshot(live("a"), "claude", "idle"));
assert.equal(chip(a), null, "an idle snapshot cannot display old tasks");

// Catalog repaints keep linked controls ahead of the count, without replacing
// it or letting a second session's browser leak into this session's header.
a.handle(snapshot(live("a")));
context.state.tabs = [
  {type: "browser", browserId: "A123", sid: 1, bid: 0, id: "browser"},
  {type: "term", terminalId: "B234", sid: 1, bid: 0, id: "term"},
  {type: "browser", browserId: "C345", sid: 2, bid: 7, id: "other"},
];
a.syncBrowserChips(); a.syncTerminalChips();
const lane = a.root.querySelector(".chat-meta-scroll");
assert.deepEqual(lane.children.map(n => n.className), [
  "chip eng", "chip cwd", "chip browser", "chip terminal", "chip background-tasks", "chat-status"]);
console.log("PASS: background-task pill snapshots, live replacements, deduplication, per-session ownership, unsupported engines, history, reconnects, turn/engine changes and linked controls");
