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

function consoleFor(sessions, remoteSessions, marks = false) {
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
    backendSupportsSessionPinning: () => marks, backendSupportsAgentNotes: () => marks,
    backendSupportsSessionGit: () => marks, backendConnectionAllowed: () => true,
    sessionOrderPending: new Set(), sessionOrderNodeKey: bid => `n${bid}`,
    setSessionPinned() {}, modalAgentNotes() {}, api: () => Promise.reject(new Error("no node")),
    activationPointer: () => () => "mouse", suppressContextGestureActivation() {},
    wireSessionDrag() {}, wireSessionDropZone() {}, renderFootEngines() {},
    animateSessionRows: (root, rebuild) => rebuild(),
  });
  vm.runInContext([
    between("function sidebarSessionKey(", "function focusedSessionKey("),
    between("function mergeSidebarRows(", "const AGENT_NOTE_FILES ="),
    between("function sessionOrderSnapshot(", "function acceptSessionListPayload("),
  ].concat(marks ? [
    /* the real marks and glyphs, so the lane is the one the console draws */
    between("function sessionPinIcon(", "/* A compact transport indicator"),
    between("function backendSupportsSessionGit(", "function describeAgentNote("),
  ] : []).join("\n"), context);
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

// The Git mark stands between the pin and the notes on every row, drawing
// exactly what the node's record says: a repository at full strength, no
// repository or an unanswered directory as the faint outline, and the reason
// in its label when the node could not look. It is a labelled image, never a
// button, and it is drawn only for nodes that advertise the capability.
const lane = consoleFor([
  session(1, 40, { git: { repo: true, checked_at: 1 }, agent_notes: ["AGENTS.md"] }),
  session(2, 30, { git: { repo: false, checked_at: 1 } }),
  session(3, 20, { git: null }),
  session(4, 10, { git: { repo: null, checked_at: 1, error: "Permission denied" } }),
  session(5, 5),
], {}, true);
lane.render();
const rows = lane.document.getElementById("sess-groups").children
  .filter(node => node.classList.contains("sess-item"));
assert.equal(rows.length, 5);
for (const row of rows) {
  const marks = row.querySelector(".si-actions").children;
  assert.deepEqual(marks.map(mark => mark.className.split(" ")[0]), ["si-pin", "si-git", "si-notes"]);
  const git = marks[1];
  assert.equal(git.getAttribute("role"), "img");
  assert.equal(git.tagName.toLowerCase(), "span");
  assert.equal(git.tabIndex, undefined, "nothing opens from the mark yet");
  assert.equal(marks[0].tabIndex, 0);
  assert.equal(git.children.length, 1);
  const svg = git.children[0];
  assert.equal(svg.getAttribute("viewBox"), "0 0 18 18");
  assert.equal(svg.getAttribute("width"), "14");
  assert.equal(svg.children.length, 4, "three nodes and the link");
  for (const part of svg.children) assert.equal(part.getAttribute("stroke-width"), "1.25");
}
const labels = rows.map(row => row.querySelector(".si-git").getAttribute("aria-label"));
assert.deepEqual(labels, [
  "Git repository", "No Git repository", "Git repository not checked yet",
  "Git repository could not be checked · Permission denied", "Git repository not checked yet",
]);
assert.deepEqual(rows.map(row => row.querySelector(".si-git").classList.contains("has")),
  [true, false, false, false, false]);
assert.equal(rows[0].querySelector(".si-notes").classList.contains("has"), true);

// A node that does not advertise the capability gets no mark and no request.
const bare = consoleFor([session(1, 40, { git: { repo: true, checked_at: 1 } })], {}, false);
bare.render();
assert.equal(bare.document.querySelectorAll(".si-git").length, 0);

// Focusing a session asks the node once per change of focus: the same session
// again, however it is re-selected, sends nothing; a session on an unreachable
// node sends nothing; and an answer lands on the row and redraws the list.
{
  const calls = [];
  let answer = { ok: true, git: { repo: true, checked_at: 2 } };
  const focus = consoleFor([session(1, 40, { git: null }), session(2, 30, { git: null })],
    { 2: [session(7, 20, { git: null })] }, true);
  focus.context.api = (bid, path, options) => {
    calls.push([bid, path, options.method]);
    return Promise.resolve(answer);
  };
  let renders = 0;
  const realRender = focus.context.renderSidebar;
  focus.context.renderSidebar = () => { renders++; return realRender(); };
  focus.context.sessionGitFocused(0, 1);
  focus.context.sessionGitFocused(0, 1);
  assert.deepEqual(calls, [[0, "sessions/1/git/refresh", "POST"]]);
  Promise.resolve().then(() => Promise.resolve()).then(() => {
    assert.deepEqual(focus.state.sessions[0].git, { repo: true, checked_at: 2 });
    assert.equal(renders, 1, "a changed answer redraws once");
    // an unchanged answer draws nothing; an identical mark is the same mark
    answer = { ok: true, git: { repo: true, checked_at: 3 } };
    focus.context.sessionGitFocused(0, 2);
    focus.context.sessionGitFocused(0, 1);
    assert.equal(calls.length, 3);
    return Promise.resolve().then(() => Promise.resolve());
  }).then(() => {
    assert.equal(renders, 2, "session 2 changed; session 1 already said so");
    focus.context.backendConnectionAllowed = bid => bid !== 2;
    focus.context.sessionGitFocused(2, 7);
    assert.equal(calls.length, 3, "an unreachable node is not probed");
    focus.context.backendConnectionAllowed = () => true;
    focus.context.api = () => Promise.reject(new Error("HTTP 503"));
    focus.context.sessionGitFocused(0, 2);
    focus.context.sessionGitFocused(0, 1);
    return Promise.resolve().then(() => Promise.resolve());
  }).then(() => {
    assert.equal(renders, 2, "a failed re-check changes nothing");
    assert.deepEqual(focus.state.sessions[0].git, { repo: true, checked_at: 2 });
    console.log("sidebar git mark tests passed");
  }).catch(error => { console.error(error); process.exit(1); });
}
