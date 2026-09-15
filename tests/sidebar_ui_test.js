"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
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
    modalSessionGit(bid, s) { opened.push([bid, s.id]); },
    selectSidebarSession(bid, sid) { selected.push([bid, sid]); }, openSessionTab() {}, closeDrawer() {},
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
/* the sheets the Git marks opened, and the rows a press reached: [bid, sid] each */
const opened = [], selected = [];
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
// in its label when the node could not look. A repository's mark is a button
// on the notes control's vocabulary - it opens the sheet - while every other
// mark is a labelled image a press passes through to the row; and the mark
// is drawn only for nodes that advertise the capability.
const lane = consoleFor([
  session(1, 40, { git: { repo: true, checked_at: 1, changes: 0, unpushed: 0 }, agent_notes: ["AGENTS.md"] }),
  session(2, 30, { git: { repo: false, checked_at: 1 } }),
  session(3, 20, { git: null }),
  session(4, 10, { git: { repo: null, checked_at: 1, error: "Permission denied" } }),
  session(5, 5),
], {}, true);
lane.render();
const rows = lane.document.getElementById("sess-groups").children
  .filter(node => node.classList.contains("sess-item"));
assert.equal(rows.length, 5);
for (const [at, row] of rows.entries()) {
  const marks = row.querySelector(".si-actions").children;
  assert.deepEqual(marks.map(mark => mark.className.split(" ")[0]), ["si-pin", "si-git", "si-notes"]);
  const git = marks[1];
  assert.equal(git.tagName.toLowerCase(), "span");
  assert.equal(git.getAttribute("role"), at === 0 ? "button" : "img");
  assert.equal(git.tabIndex, at === 0 ? 0 : undefined, "only a repository's mark opens anything");
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
  "Git repository · nothing to commit or push · open details", "No Git repository",
  "Git repository not checked yet",
  "Git repository could not be checked · Permission denied", "Git repository not checked yet",
]);
// a press on the button opens the sheet for that row and never reaches the
// row; Enter and Space open it too; the images answer to nothing
{
  const press = (mark, type, init) => {
    const event = new FakeEvent(type, { bubbles: true, ...init });
    mark.dispatchEvent(event);
    return event;
  };
  const button = rows[0].querySelector(".si-git");
  let click = press(button, "click");
  assert.deepEqual(opened, [[0, 1]]);
  assert.ok(click.defaultPrevented && click.propagationStopped, "the row is not activated");
  assert.ok(press(button, "pointerdown").propagationStopped, "no row drag starts on the mark");
  assert.ok(press(button, "contextmenu").propagationStopped, "no row menu opens from the mark");
  press(button, "keydown", { key: "Enter" });
  press(button, "keydown", { key: " " });
  press(button, "keydown", { key: "a" });
  assert.deepEqual(opened, [[0, 1], [0, 1], [0, 1]]);
  assert.deepEqual(selected, [], "the row behind the button was never reached");
  for (const row of rows.slice(1)) {
    click = press(row.querySelector(".si-git"), "click");
    assert.ok(!click.propagationStopped, "a press on an image is a press on the row");
  }
  assert.deepEqual(selected, [[0, 2], [0, 3], [0, 4], [0, 5]]);
  assert.deepEqual(opened, [[0, 1], [0, 1], [0, 1]]);
  opened.length = 0;
  selected.length = 0;
  // a node that answers the record but does not serve the sheet's read
  // keeps the repository's mark an image
  const older = consoleFor([session(1, 40, { git: { repo: true, checked_at: 1, changes: 1, unpushed: 0 } })], {}, true);
  older.context.backendSupportsSessionGitDetail = () => false;
  older.render();
  const mark = older.document.querySelector(".si-git");
  assert.equal(mark.getAttribute("role"), "img");
  assert.equal(mark.tabIndex, undefined);
  assert.equal(mark.getAttribute("aria-label"), "Git repository · 1 uncommitted change · nothing to push");
  assert.equal(mark.className, "si-git has warn", "the mark itself is as before");
}
assert.deepEqual(rows.map(row => row.querySelector(".si-git").classList.contains("has")),
  [true, false, false, false, false]);
assert.deepEqual(rows.map(row => row.querySelector(".si-git").classList.contains("warn")),
  [false, false, false, false, false]);
// a plain mark carries no tooltip: its label already says all there is
assert.deepEqual(rows.map(row => row.querySelector(".si-git").title), [undefined, undefined, undefined, undefined, undefined]);
assert.equal(rows[0].querySelector(".si-notes").classList.contains("has"), true);

// The heads-up: a repository holding uncommitted changes or commits no
// remote has takes the warn tone as well as full strength, and its label
// says how much of each. A repository with no remote has nowhere to push,
// so its commits are never unpushed; a repository git would not read keeps
// the plain mark with git's reason; and a node from before the work state
// answers without counts, which is a plain repository, never a heads-up.
{
  const work = consoleFor([
    session(1, 90, { git: { repo: true, checked_at: 1, changes: 3, unpushed: 2 } }),
    session(2, 80, { git: { repo: true, checked_at: 1, changes: 1, unpushed: 0 } }),
    session(3, 70, { git: { repo: true, checked_at: 1, changes: 0, unpushed: 1 } }),
    session(4, 60, { git: { repo: true, checked_at: 1, changes: 0, unpushed: null } }),
    session(5, 50, { git: { repo: true, checked_at: 1, changes: 2, unpushed: null } }),
    session(6, 40, { git: { repo: true, checked_at: 1, changes: null, unpushed: null,
      error: "detected dubious ownership in repository at '/srv/app'" } }),
    session(7, 30, { git: { repo: true, checked_at: 1 } }),
    session(8, 20, { git: { repo: false, checked_at: 1, changes: 4, unpushed: 4 } }),
  ], {}, true);
  work.render();
  const marks = work.document.getElementById("sess-groups").children
    .filter(node => node.classList.contains("sess-item"))
    .map(row => row.querySelector(".si-git"));
  assert.deepEqual(marks.map(mark => mark.className), [
    "si-git has warn", "si-git has warn", "si-git has warn", "si-git has", "si-git has warn",
    "si-git has", "si-git has", "si-git",
  ]);
  assert.deepEqual(marks.map(mark => mark.getAttribute("aria-label")), [
    "Git repository · 3 uncommitted changes · 2 unpushed commits · open details",
    "Git repository · 1 uncommitted change · nothing to push · open details",
    "Git repository · nothing to commit · 1 unpushed commit · open details",
    "Git repository · nothing to commit · no remote · open details",
    "Git repository · 2 uncommitted changes · no remote · open details",
    "Git repository · changes could not be checked · detected dubious ownership in repository at '/srv/app' · open details",
    "Git repository · open details",
    "No Git repository",
  ]);
  // every repository's mark opens the sheet, a refused one included - the
  // sheet is where git's reason is read in full
  assert.deepEqual(marks.map(mark => mark.getAttribute("role")),
    ["button", "button", "button", "button", "button", "button", "button", "img"]);
  // The orange marks alone carry a tooltip saying why: a node from before
  // the rundown answers counts alone, so these say only what they know.
  assert.deepEqual(marks.map(mark => mark.title), [
    "Uncommitted and unpushed work\n3 uncommitted changes\n2 unpushed commits",
    "Uncommitted work\n1 uncommitted change",
    "Unpushed work\n1 unpushed commit",
    undefined,
    "Uncommitted work\n2 uncommitted changes",
    undefined, undefined, undefined,
  ]);
  // a changed count is a changed record - the label and possibly the tone -
  // while null and a missing count are the same absence
  const same = work.context.sessionGitSame;
  const base = { repo: true, checked_at: 1, changes: 1, unpushed: 0 };
  assert.equal(same(base, { ...base, checked_at: 2 }), true);
  assert.equal(same(base, { ...base, changes: 2 }), false);
  assert.equal(same(base, { ...base, unpushed: 1 }), false);
  assert.equal(same(base, { ...base, unpushed: null }), false);
  assert.equal(same({ repo: true, checked_at: 1 }, { repo: true, checked_at: 2, changes: null, unpushed: null }), true);
  assert.equal(same({ repo: true, checked_at: 1 }, { repo: true, checked_at: 2, changes: 0, unpushed: 0 }), false);
  assert.equal(same({ repo: false, checked_at: 1 }, { repo: false, checked_at: 2 }), true);
  // the rundown is part of the record too: the same totals sorted into
  // other kinds, or on another branch, redraw the tooltip; a node that
  // stops naming the branch, or names it detached, is a change as well
  const full = { ...base, branch: "main", staged: 1, unstaged: 0, untracked: 0, conflicts: 0 };
  assert.equal(same(full, { ...full, checked_at: 2 }), true);
  assert.equal(same(full, { ...full, staged: 0, unstaged: 1 }), false);
  assert.equal(same(full, { ...full, branch: "feature" }), false);
  assert.equal(same(full, { ...full, branch: null }), false);
  assert.equal(same(full, base), false);
  assert.equal(same({ ...full, branch: null }, { ...full, branch: null, checked_at: 3 }), true);
}

// The tooltip of an orange mark: one line naming the work and the branch
// holding it, then the changes sorted by what git would do with them and
// the commits no remote has, each only when there is any. A kind with
// nothing in it is not listed, a detached HEAD is named as such, a record
// naming no branch says nothing about one, and only a record without the
// kinds spells "uncommitted" out again on the changes line.
{
  const tip = record => {
    const one = consoleFor([session(1, 10, { git: record })], {}, true);
    one.render();
    return one.document.querySelector(".si-git").title;
  };
  const repo = { repo: true, checked_at: 1 };
  assert.equal(tip({ ...repo, changes: 3, unpushed: 1, branch: "main",
    staged: 1, unstaged: 1, untracked: 1, conflicts: 0 }),
    "Uncommitted and unpushed work on main\n" +
    "3 changes · 1 staged, 1 unstaged, 1 untracked\n1 unpushed commit");
  assert.equal(tip({ ...repo, changes: 1, unpushed: 0, branch: "feature/login",
    staged: 0, unstaged: 0, untracked: 1, conflicts: 0 }),
    "Uncommitted work on feature/login\n1 change · 1 untracked");
  assert.equal(tip({ ...repo, changes: 0, unpushed: 2, branch: "main",
    staged: 0, unstaged: 0, untracked: 0, conflicts: 0 }),
    "Unpushed work on main\n2 unpushed commits");
  assert.equal(tip({ ...repo, changes: 3, unpushed: null, branch: "main",
    staged: 0, unstaged: 1, untracked: 0, conflicts: 2 }),
    "Uncommitted work on main\n3 changes · 1 unstaged, 2 conflicts");
  assert.equal(tip({ ...repo, changes: 1, unpushed: 1, branch: null,
    staged: 0, unstaged: 0, untracked: 0, conflicts: 1 }),
    "Uncommitted and unpushed work on a detached HEAD\n" +
    "1 change · 1 conflict\n1 unpushed commit");
  assert.equal(tip({ ...repo, changes: 2, unpushed: 0, staged: 2, unstaged: 0, untracked: 0, conflicts: 0 }),
    "Uncommitted work\n2 changes · 2 staged");
  assert.equal(tip({ ...repo, changes: 2, unpushed: 0, branch: "main" }),
    "Uncommitted work on main\n2 uncommitted changes");
  assert.equal(tip({ ...repo, changes: 0, unpushed: 0, branch: "main",
    staged: 0, unstaged: 0, untracked: 0, conflicts: 0 }), undefined, "nothing to say");
  assert.equal(tip({ ...repo, changes: null, unpushed: null, error: "no git" }), undefined);
  console.log("sidebar git tooltip tests passed");
}

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
    // a fresh count on the focused session is news: the row turns orange
    answer = { ok: true, git: { repo: true, checked_at: 4, changes: 2, unpushed: 0 } };
    focus.context.sessionGitFocused(0, 2);
    focus.context.sessionGitFocused(0, 1);
    return Promise.resolve().then(() => Promise.resolve());
  }).then(() => {
    assert.equal(renders, 4, "each session's count changed");
    const marks = focus.document.getElementById("sess-groups").children
      .filter(node => node.classList.contains("sess-item"))
      .map(row => row.querySelector(".si-git")).filter(Boolean).map(mark => mark.className);
    assert.deepEqual(marks, ["si-git has warn", "si-git has warn"], "the remote node offers no mark");
    focus.context.backendConnectionAllowed = bid => bid !== 2;
    focus.context.sessionGitFocused(2, 7);
    assert.equal(calls.length, 5, "an unreachable node is not probed");
    focus.context.backendConnectionAllowed = () => true;
    focus.context.api = () => Promise.reject(new Error("HTTP 503"));
    focus.context.sessionGitFocused(0, 2);
    focus.context.sessionGitFocused(0, 1);
    return Promise.resolve().then(() => Promise.resolve());
  }).then(() => {
    assert.equal(renders, 4, "a failed re-check changes nothing");
    assert.deepEqual(focus.state.sessions[0].git, { repo: true, checked_at: 4, changes: 2, unpushed: 0 });
    console.log("sidebar git mark tests passed");
  }).catch(error => { console.error(error); process.exit(1); });
}

// The sheet a repository's mark opens, against the fake DOM: the facts and
// the captions drawn from the row's record the moment it opens, the read
// through the node's own route filling in the root, the upstream, the
// paths grouped by kind with git's verbs and the commits, the row's record
// moving with that read, the empty lines of a clean repository, a
// repository with no remote, an unborn branch and a detached HEAD, git's
// refusal and a vanished repository, a read that fails, and Refresh.
{
  const document = new FakeDocument();
  const el = (tag, cls = "", text = "") => {
    const node = document.createElement(tag);
    node.className = cls; node.textContent = text; return node;
  };
  const sessions = [];
  let dialog = null, reopen = null, replies = [], requests = [], renders = 0, dialogs = [];
  const modal = (html, className = "", again = null) => {
    const m = document.createElement("div");
    m.className = "modal" + (className ? " " + className : "");
    m.innerHTML = html;
    document.body.appendChild(m);
    reopen = again;
    dialog = { m, close: () => m.remove() };
    dialogs.push(className);
    return dialog;
  };
  const context = vm.createContext({
    document, el, console, Date, modal, state: { backends: [] },
    api: (bid, path, options) => {
      requests.push([bid, path, JSON.parse(JSON.stringify(options))]);
      const reply = replies.shift();
      return reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply);
    },
    sessionsFor: () => sessions, renderSidebar: () => { renders++; },
    sidebarSessionKey: (bid, sid) => `${bid}:${sid}`, backendSupportsSessionGit: () => true,
    backendConnectionAllowed: () => true, sessionLocationLabel: s => `…/${s.cwd.split("/").pop()}`,
    fmtStamp: at => `at ${at}`, navigationSessionDialog: (bid, sid, open) => open(bid, { id: sid, cwd: "/p", git: null }),
  });
  vm.runInContext([
    between("function sessionGitWork(", "/* The row itself is a button"),
    between("const SESSION_GIT_VERBS =", "function sessDot("),
  ].join("\n"), context);
  const settle = () => new Promise(resolve => setTimeout(resolve, 0));
  const text = selector => [...dialog.m.querySelectorAll(selector)].map(node => node.textContent);
  const facts = () => [...dialog.m.querySelectorAll(".ws-fact")]
    .map(row => [row.querySelector(".wsf-l").textContent, row.querySelector(".wsf-v").textContent,
                 row.querySelector(".wsf-v").className.replace("wsf-v", "").trim()]);
  const captions = () => text(".session-git-section .field-lbl");
  const lists = () => [...dialog.m.querySelectorAll(".session-git-list")].map(list => list.children.map(child => {
    if (child.classList.contains("sgl-group")) {
      const kind = child.querySelector(".sgl-kind");
      return [kind ? kind.textContent : "", child.querySelector(".sgl-rows").children.map(cell => cell.textContent)];
    }
    if (child.classList.contains("sgl-commits")) return child.children.map(cell => cell.textContent);
    return child.textContent;
  }));

  // the verbs git would use, from its own two-column codes
  const what = context.sessionGitWhat;
  assert.equal(what({ kind: "staged", code: "M." }), "modified");
  assert.equal(what({ kind: "staged", code: "A." }), "new file");
  assert.equal(what({ kind: "staged", code: "D." }), "deleted");
  assert.equal(what({ kind: "staged", code: "R." }), "renamed");
  assert.equal(what({ kind: "staged", code: "C." }), "copied");
  assert.equal(what({ kind: "staged", code: "T." }), "type changed");
  assert.equal(what({ kind: "staged", code: "MM" }), "modified · edited since staging");
  assert.equal(what({ kind: "staged", code: "AD" }), "new file · deleted since staging");
  assert.equal(what({ kind: "staged", code: "RT" }), "renamed · type changed since staging");
  assert.equal(what({ kind: "unstaged", code: ".M" }), "modified");
  assert.equal(what({ kind: "unstaged", code: ".D" }), "deleted");
  assert.equal(what({ kind: "unstaged", code: ".A" }), "new file");
  assert.equal(what({ kind: "untracked", code: "??" }), "");
  assert.equal(what({ kind: "conflicts", code: "UU" }), "both modified");
  assert.equal(what({ kind: "conflicts", code: "DD" }), "both deleted");
  assert.equal(what({ kind: "conflicts", code: "AU" }), "added by us");
  assert.equal(what({ kind: "conflicts", code: "UD" }), "deleted by them");
  assert.equal(what({ kind: "conflicts", code: "XY" }), "XY", "a code this does not know is shown as git wrote it");
  assert.equal(what({ kind: "staged", code: "X." }), "X");
  assert.equal(what({ kind: "", code: "" }), "");
  assert.equal(what({ kind: "staged" }), "");

  // the mark's tone as the sheet's State (values built inside the vm
  // context carry its own prototypes, hence the round trip)
  const stateOf = git => JSON.parse(JSON.stringify(context.sessionGitState(git)));
  const repo = { repo: true, checked_at: 1 };
  assert.deepEqual(stateOf({ ...repo, changes: 2, unpushed: 1 }), { text: "Uncommitted and unpushed work", tone: "warn" });
  assert.deepEqual(stateOf({ ...repo, changes: 2, unpushed: 0 }), { text: "Uncommitted work", tone: "warn" });
  assert.deepEqual(stateOf({ ...repo, changes: 0, unpushed: 3 }), { text: "Unpushed work", tone: "warn" });
  assert.deepEqual(stateOf({ ...repo, changes: 0, unpushed: 0 }), { text: "Nothing to commit or push", tone: "ok" });
  assert.deepEqual(stateOf({ ...repo, changes: 0, unpushed: null }), { text: "Nothing to commit · no remote", tone: "ok" });
  assert.deepEqual(stateOf({ ...repo, changes: null, unpushed: null, error: "no git" }), { text: "Could not be read · no git", tone: "bad" });
  assert.deepEqual(stateOf({ repo: null, checked_at: 1, error: "gone" }), { text: "Could not be checked · gone", tone: "bad" });
  assert.deepEqual(stateOf({ repo: false, checked_at: 1 }), { text: "No Git repository", tone: "" });
  assert.deepEqual(stateOf(repo), { text: "Repository", tone: "" });
  assert.deepEqual(stateOf(null), { text: "Not checked yet", tone: "" });

  const run = async () => {
    // opening: the row's record at once, the lists waiting on the read
    const row = { id: 1, name: "Garden", cwd: "/home/mira/garden/src", git: { repo: true, checked_at: 5,
      changes: 5, unpushed: 2, branch: "main", staged: 2, unstaged: 1, untracked: 1, conflicts: 1 } };
    sessions.length = 0; sessions.push(row);
    const read = { ok: true, git: { ...row.git, checked_at: 6 }, root: "/home/mira/garden", detail: {
      head: "abc1234def", upstream: "origin/main", ahead: 2, behind: 1, remotes: ["origin"],
      paths: [{ kind: "staged", code: "M.", path: "src/a.js" },
              { kind: "staged", code: "RM", path: "src/new name.js", from: "src/old.js" },
              { kind: "unstaged", code: ".D", path: "src/gone.js" },
              { kind: "untracked", code: "??", path: "notes/" },
              { kind: "conflicts", code: "UU", path: "src/index.js" }],
      commits: [{ hash: "abc1234", subject: "Tighten spacing", author: "Mira", at: 100 },
                { hash: "9876543", subject: "Shade map", author: "", at: null }],
      more_paths: 0, more_commits: 0 } };
    replies = [read];
    context.modalSessionGit(0, row);
    assert.deepEqual(dialogs, ["session-git-modal"]);
    assert.equal(dialog.m.querySelector(".session-git-intro").textContent, "Garden · …/src");
    assert.deepEqual(facts(), [["Branch", "main", ""], ["State", "Uncommitted and unpushed work", "warn"], ["Checked", "at 5", ""]]);
    assert.deepEqual(captions(), ["Changes 5 · 2 staged, 1 unstaged, 1 untracked, 1 conflict", "Unpushed commits 2"]);
    assert.deepEqual(lists(), [["Loading…"], ["Loading…"]]);
    assert.equal(dialog.m.querySelector("#session-git-refresh").disabled, true);
    assert.equal(dialog.m.getAttribute("aria-busy"), "true");
    assert.equal(document.activeElement, dialog.m.querySelector("#session-git-close"));
    assert.deepEqual(requests, [[0, "sessions/1/git", { timeoutMs: 45000 }]]);
    await settle();
    // the read: the root above the session's directory, the upstream, the
    // paths grouped the way git groups them, the commits, and the row's
    // record brought up to date - the list redrawn because it changed
    assert.deepEqual(facts(), [["Repository", "/home/mira/garden", ""], ["Branch", "main", ""],
      ["Upstream", "origin/main · 2 ahead, 1 behind", ""],
      ["State", "Uncommitted and unpushed work", "warn"], ["Checked", "at 6", ""]]);
    assert.deepEqual(captions(), ["Changes 5 · 2 staged, 1 unstaged, 1 untracked, 1 conflict", "Unpushed commits 2 · not on origin"]);
    assert.deepEqual(lists(), [[
      ["Staged", ["modified", "src/a.js", "renamed · edited since staging", "src/old.js → src/new name.js"]],
      ["Unstaged", ["deleted", "src/gone.js"]],
      ["Untracked", ["notes/"]],
      ["Conflicts", ["both modified", "src/index.js"]],
    ], [["abc1234", "Tighten spacing", "Mira · at 100", "9876543", "Shade map", ""]]]);
    const untracked = dialog.m.querySelectorAll(".sgl-path.full");
    assert.equal(untracked.length, 1, "an untracked path spans both columns: it has no verb");
    assert.equal(dialog.m.querySelector(".sgl-from").textContent, "src/old.js → ");
    assert.equal(dialog.m.querySelector("#session-git-refresh").disabled, false);
    assert.equal(dialog.m.getAttribute("aria-busy"), null);
    assert.equal(row.git.checked_at, 6, "the row carries the read's record");
    assert.equal(renders, 0, "an unchanged record redraws nothing");
    assert.ok(dialog.m.querySelector(".form-error").classList.contains("hidden"));

    // Refresh: one press one request, a press while it is pending sends
    // nothing; a changed count reaches the row and redraws the list; the
    // bounds' leftovers are counted at the foot of each list
    replies = [{ ...read, git: { ...read.git, checked_at: 7, changes: 6, untracked: 2 },
      detail: { ...read.detail, more_paths: 40, more_commits: 1 } }];
    dialog.m.querySelector("#session-git-refresh").click();
    dialog.m.querySelector("#session-git-refresh").click();
    assert.equal(requests.length, 2);
    await settle();
    assert.equal(row.git.changes, 6);
    assert.equal(renders, 1);
    assert.deepEqual(captions(), ["Changes 6 · 2 staged, 1 unstaged, 2 untracked, 1 conflict", "Unpushed commits 2 · not on origin"]);
    assert.deepEqual(lists()[0][4], "… and 40 more paths");
    assert.deepEqual(lists()[1][1], "… and 1 more commit");
    // a read that fails after one that answered keeps the lists and says
    // so inline; the first read failing leaves nothing to list
    replies = [new Error("HTTP 503")];
    dialog.m.querySelector("#session-git-refresh").click();
    await settle();
    assert.equal(dialog.m.querySelector(".form-error").textContent, "HTTP 503");
    assert.ok(!dialog.m.querySelector(".form-error").classList.contains("hidden"));
    assert.equal(lists().length, 2, "the last read's lists stay");
    assert.equal(dialog.m.querySelector("#session-git-refresh").disabled, false);
    dialog.m.querySelector("#session-git-close").click();
    assert.equal(dialog.m.isConnected, false);
    replies = [new Error("network error")];
    context.modalSessionGit(0, row);
    assert.deepEqual(lists(), [["Loading…"], ["Loading…"]]);
    await settle();
    assert.deepEqual(lists(), []);
    assert.equal(dialog.m.querySelector(".form-error").textContent, "network error");
    assert.deepEqual(facts().map(fact => fact[0]), ["Branch", "State", "Checked"], "the row's facts stay");
    dialog.close();

    // Back and Forward: the sheet is a dialog layer that reopens fresh for
    // the session as it is then
    assert.equal(typeof reopen, "function");
    replies = [{ ok: true, git: { repo: false, checked_at: 9 }, root: null, detail: null }];
    reopen();
    assert.deepEqual(dialogs.slice(-1), ["session-git-modal"]);
    await settle();
    assert.deepEqual(facts(), [["State", "No Git repository", ""], ["Checked", "at 9", ""]]);
    assert.deepEqual(lists(), [], "nothing to list outside a repository");
    dialog.close();

    // a clean repository says so on both lists; one with no remote has
    // nowhere to push; an unborn branch and a detached HEAD are named
    const clean = { id: 2, name: "", cwd: "/p", git: { repo: true, checked_at: 1, changes: 0, unpushed: 0, branch: "main",
      staged: 0, unstaged: 0, untracked: 0, conflicts: 0 } };
    const bare = { head: "1234567890", upstream: "origin/main", ahead: 0, behind: 0, remotes: ["origin"],
      paths: [], commits: [], more_paths: 0, more_commits: 0 };
    replies = [{ ok: true, git: clean.git, root: "/p", detail: bare }];
    context.modalSessionGit(0, clean);
    assert.equal(dialog.m.querySelector(".session-git-intro").textContent, "Session 2 · …/p");
    await settle();
    assert.deepEqual(facts(), [["Branch", "main", ""], ["Upstream", "origin/main · up to date", ""],
      ["State", "Nothing to commit or push", "ok"], ["Checked", "at 1", ""]]);
    assert.deepEqual(captions(), ["Changes none", "Unpushed commits none"]);
    assert.deepEqual(lists(), [["Nothing to commit"], ["Nothing to push"]]);
    dialog.close();
    replies = [{ ok: true, git: { ...clean.git, unpushed: null, branch: "trunk" }, root: "/p",
      detail: { ...bare, head: null, upstream: null, remotes: [] } }];
    context.modalSessionGit(0, { ...clean, git: { ...clean.git, unpushed: null } });
    await settle();
    assert.deepEqual(facts(), [["Branch", "trunk · no commits yet", ""], ["Upstream", "no remote", ""],
      ["State", "Nothing to commit · no remote", "ok"], ["Checked", "at 1", ""]]);
    assert.deepEqual(captions(), ["Changes none", "Unpushed commits no remote"]);
    assert.deepEqual(lists(), [["Nothing to commit"], ["No remote to push to"]]);
    dialog.close();
    replies = [{ ok: true, git: { ...clean.git, unpushed: 1, branch: null }, root: "/p",
      detail: { ...bare, upstream: null, remotes: ["origin", "backup"],
        commits: [{ hash: "1234567", subject: "Five", author: "Mira", at: 3 }] } }];
    context.modalSessionGit(0, { ...clean, git: { ...clean.git, unpushed: 1, branch: null } });
    assert.deepEqual(captions(), ["Changes none", "Unpushed commits 1"]);
    await settle();
    assert.deepEqual(facts(), [["Branch", "HEAD detached at 1234567", ""], ["Upstream", "not set", ""],
      ["State", "Unpushed work", "warn"], ["Checked", "at 1", ""]]);
    assert.deepEqual(captions(), ["Changes none", "Unpushed commits 1 · not on any remote"]);
    assert.deepEqual(lists(), [["Nothing to commit"], [["1234567", "Five", "Mira · at 3"]]]);
    dialog.close();

    // git's refusal: the reason in the State fact, and nothing to list
    replies = [{ ok: true, git: { repo: true, checked_at: 2, changes: null, unpushed: null,
      error: "detected dubious ownership in repository at '/p'" }, root: "/p", detail: null }];
    context.modalSessionGit(0, clean);
    await settle();
    assert.deepEqual(facts(), [["State", "Could not be read · detected dubious ownership in repository at '/p'", "bad"], ["Checked", "at 2", ""]]);
    assert.deepEqual(lists(), []);
    dialog.close();
    console.log("sidebar git sheet tests passed");
  };
  run().catch(error => { console.error(error); process.exit(1); });
}
