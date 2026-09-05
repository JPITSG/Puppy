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

function consoleFor(sessions, remoteSessions) {
  const document = new FakeDocument();
  const el = (tag, cls = "", text = "") => {
    const node = document.createElement(tag);
    node.className = cls; node.textContent = text; return node;
  };
  for (const id of ["sess-groups", "toggle-archived"]) {
    const node = el("div"); node.id = id; document.body.appendChild(node);
  }
  const state = { sessions, remoteSessions, views: {}, tabs: [], backends: [{ id: 2 }],
    sessionFilter: "", showArchived: false, remoteOk: { 2: true }, selectedSession: "2:1" };
  const context = vm.createContext({
    document, el, state, console, Date, dragSess: null,
    $: id => document.getElementById(id),
    sessionsFor: bid => bid ? state.remoteSessions[bid] || [] : state.sessions,
    sortNodeGroups: nodes => nodes,
    focusedSessionKey: () => null,
    sessionActivityAnchors: new Map(), sessionActivityKey: (bid, sid) => `${bid}:${sid}`,
    ingestOneSessionActivity() {}, formatSessionActivity: () => "1s", syncPromptSpinnerPhase() {},
    backendName: bid => `Backend ${bid}`, taskActivityLabel: () => "",
    sessionFilterHaystack: (bid, session) => session.name.toLowerCase(),
    sessionRowRows: () => ({ r1: el("div"), r2: el("div") }),
    backendSupportsSessionPinning: () => false, backendSupportsAgentNotes: () => false,
    activationPointer: () => () => "mouse", suppressContextGestureActivation() {},
    wireSessionDrag() {}, wireSessionDropZone() {}, renderFootEngines() {},
    animateSessionRows: (root, rebuild) => rebuild(),
  });
  vm.runInContext([
    between("function sidebarSessionKey(", "function focusedSessionKey("),
    between("function mergeSidebarRows(", "const AGENT_NOTE_FILES ="),
    between("function sessionOrderSnapshot(", "function acceptSessionListPayload("),
  ].join("\n"), context);
  return { state, context, render() {
    context.renderSidebar();
    return document.getElementById("sess-groups").children
      .filter(node => node.classList.contains("sess-item"))
      .map(node => node.dataset.sessionKey);
  }, document };
}

const session = (id, order_at = 0, extra = {}) => ({
  id, name: `Session ${id}`, order_at, status: "idle", ...extra,
});
const localPin = session(9, 0, { pinned: true });
const remotePin = session(9, 0, { pinned: true });
const a = session(1, 20), b = session(2, 10), c = session(1, 5);
const ui = consoleFor([localPin, a, b], { 2: [remotePin, c] });
assert.deepEqual(ui.render(), ["0:9", "2:9", "0:1", "0:2", "2:1"]);

// A remote session already first on its backend must overtake both local rows.
c.status = "running"; c.order_at = 30;
assert.deepEqual(ui.render(), ["0:9", "2:9", "2:1", "0:1", "0:2"]);
assert.ok(ui.document.getElementById("sess-groups").children[2].classList.contains("active"));
// Completion/edits do not change the durable order; neither does a disconnect.
c.status = "idle"; b.updated_at = 1000;
ui.state.remoteOk[2] = false;
const settled = ui.render();
assert.deepEqual(settled, ["0:9", "2:9", "2:1", "0:1", "0:2"]);
// Another browser and a reload derive exactly the same order, without history.
const reloaded = consoleFor(JSON.parse(JSON.stringify(ui.state.sessions)),
  JSON.parse(JSON.stringify(ui.state.remoteSessions)));
assert.deepEqual(reloaded.render(), settled);

// Local activity interleaves the backends. Continuations do not refresh recency.
a.status = "running"; a.order_at = 40;
assert.deepEqual(ui.render(), ["0:9", "2:9", "0:1", "2:1", "0:2"]);
c.status = "running";
assert.deepEqual(ui.render(), ["0:9", "2:9", "0:1", "2:1", "0:2"]);
const before = ui.context.sessionOrderSnapshot(0);
a.order_at = 50;
assert.equal(ui.context.sameSessionOrderSnapshot(before, ui.context.sessionOrderSnapshot(0)), false);

// Manual reorder swaps the node's slots; an intervening remote row stays put.
ui.state.sessions = [localPin, b, a]; b.order_at = 50; a.order_at = 10;
assert.deepEqual(ui.render(), ["0:9", "2:9", "0:2", "2:1", "0:1"]);
b.archived = true;
assert.deepEqual(ui.render(), ["0:9", "2:9", "2:1", "0:1"]);
ui.state.showArchived = true;
assert.deepEqual(ui.render(), ["0:9", "2:9", "0:2", "2:1", "0:1"]);
ui.state.sessionFilter = "session 1";
assert.deepEqual(ui.render(), ["2:1", "0:1"]);
ui.state.sessionFilter = "";

// Task rows never escape their parent, including a newer task timestamp.
ui.state.remoteSessions[2].push(session(3, 100, { task: { parent: 1 } }));
assert.deepEqual(ui.render(), ["0:9", "2:9", "0:2", "2:1", "0:1"]);
// A pinned session starting work cannot move within or out of the pin block.
remotePin.status = "running";
assert.deepEqual(ui.render(), ["0:9", "2:9", "0:2", "2:1", "0:1"]);
// Unpin enters the head of the global ordinary block.
remotePin.pinned = false; remotePin.order_at = 60;
assert.deepEqual(ui.render(), ["0:9", "2:9", "0:2", "2:1", "0:1"]);

// Unpromoted/older peers have deterministic ties and retain their own order.
const old = consoleFor([session(2), session(1)], { 2: [
  { id: 2, name: "Older peer", pinned: true }, { id: 1, name: "Older peer" },
] });
assert.deepEqual(old.render(), ["2:2", "0:2", "0:1", "2:1"]);
console.log("sidebar cross-backend ordering tests passed");
