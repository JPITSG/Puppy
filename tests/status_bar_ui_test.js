/* Run with node tests/status_bar_ui_test.js. No browser or engine required.
   The status bar is the session's setting, not each conversation's: a task
   tab's strip shows and hides with Main's. Against the fake DOM: what a
   view's strip follows, the row in a task head's menu and in the sidebar's
   menu reading Main's setting and setting Main's (never the task's), the
   answer landing on the list row and on every view of the workspace ahead of
   the stream, the list itself landing on the task views when another console
   made the change, the seed for views built before the list, and a refusal
   that changes nothing. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}

const document = new FakeDocument();
const state = { sessions: [], remoteSessions: {}, views: {}, backends: [{ id: 7 }] };
const calls = [], toasts = [], refreshed = [];
let sidebarRenders = 0, failNext = null;
const icon = () => document.createElement("svg");
const context = vm.createContext({
  document, state, console, dragTab: null,
  checkIcon: icon, xIcon: icon,
  closeAllMenus() { document.querySelectorAll(".menu").forEach(menu => menu.remove()); return false; },
  createContextMenu() {
    const menu = document.createElement("div");
    menu.className = "menu dyn";
    document.body.appendChild(menu);
    return menu;
  },
  cancelSessionDrag() {}, cancelTabDrag() {}, positionContextMenu() {}, positionAnchoredMenu() {},
  backendSupportsEngineDefaults: () => false, backendSupportsSessionPinning: () => false,
  backendSupportsTaskRefresh: () => false,
  appendSessionTasksToggle() {}, sessionWorkspace: () => null, canMoveScratch: () => false,
  canMoveProject: () => false,
  titlePending: () => false, taskActivityTitle: () => "", promptStatusLabel: (text) => {
    const node = document.createElement("span"); node.textContent = text; return node;
  },
  syncPromptSpinnerPhase() {}, wireTabDrag() {}, syncHorizontalOverflow() {},
  toast: (text, tone) => toasts.push([text, tone]),
  refreshGroup: bid => refreshed.push(bid),
  /* the real one begins exactly so: every workspace re-reads its tasks */
  renderSidebar() {
    sidebarRenders++;
    for (const view of Object.values(state.views)) if (view && view.taskViews) view.refreshTasks();
  },
  /* the node: Main's PATCH answers Main's payload; a task's is refused as
     the node refuses it, with the reason the console shows */
  async api(bid, route, options) {
    calls.push({ bid, route, ...options });
    if (failNext) { const error = new Error(failNext); failNext = null; throw error; }
    const id = Number(route.split("/")[1]);
    if (id !== 10 && id !== 20)
      throw new Error("The status bar is shown or hidden from the main session");
    return { ok: true, session: { id, name: "Main", show_meta: options.body.show_meta, from: "patch" } };
  },
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function isScratchWorkspace(", "/* A linked remote workspace rides"),
  between("function sessionsFor(", "/* Pinning and drag-reorder"),
  between("function findSessionMeta(", "function targetWorkspacePane("),
  between("function syncSessionMetaVisibility(", "function syncTabsWithSessions("),
  between("function liveViews(", "/* A removed task's condensed conversation"),
  between("function sessionContextMenu(", "/* Live sortable layouts."),
  between("class SessionView {", "/* ================= TermView"),
  "globalThis.SessionView = SessionView;",
  /* the workspace's own task refresh, on a frame that holds what it reads */
  "globalThis.Workspace = class {",
  "  tasks() { return sessionsFor(this.tab.bid).filter(s => s.task && s.task.parent === this.tab.sid); }",
  "  closeTask() {} save() {}",
  between("  ensureTask(task) {", "  openTask(sid) {"),
  between("  refreshTasks() {", "  onShow(focus = true)"),
  "};",
].join("\n"), context);

/* a leaf view on the real prototype: the strip's class is all it needs */
function leaf(bid, sid, session) {
  const view = Object.create(context.SessionView.prototype);
  view.tab = { id: `s:${bid}:${sid}`, type: "session", bid, sid };
  view.root = document.createElement("div");
  view.root.className = "chat";
  view.session = session;
  view.repaints = 0;
  /* the real updateHead paints the whole head; here it is the strip alone */
  view.updateHead = function () { this.repaints++; this.syncMetaVisibility(); };
  return view;
}
function workspace(bid, sid, views) {
  const ws = new context.Workspace();
  ws.tab = { id: `s:${bid}:${sid}`, type: "session", bid, sid };
  ws.root = document.createElement("div");
  ws.strip = document.createElement("div");
  ws.overviewButton = document.createElement("button");
  ws.reviewButton = document.createElement("button");
  ws.removeButton = document.createElement("button");
  ws.taskViews = new Map(views.map(view => [view.tab.sid, view]));
  ws.selected = sid; ws.shown = sid; ws.opened = views.map(view => view.tab.sid).filter(id => id !== sid);
  ws.hidden = new Set(); ws.seen = {}; ws.overview = null; ws.rendered = "";
  state.views[ws.tab.id] = ws;
  return ws;
}
const hidden = view => view.root.classList.contains("meta-hidden");
const task = (id, parent) => ({ parent, state: "running", created_at: id, result_seq: 0, needs_approval: false });
const checkRow = menu => menu.querySelectorAll(".menu-check")
  .find(row => row.querySelector(".menu-check-label").textContent === "Show status bar");
const openHeadMenu = view => {
  const anchor = document.createElement("button");
  document.body.appendChild(anchor);
  view.showMenu(anchor);
  return checkRow(document.querySelector(".menu"));
};
const openSidebarMenu = (bid, row) => {
  context.sessionContextMenu(new FakeEvent("contextmenu", { clientX: 4, clientY: 4 }), bid, row);
  return checkRow(document.querySelector(".menu"));
};
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
/* values built inside the vm context carry its own prototypes */
const plain = value => JSON.parse(JSON.stringify(value));

(async () => {
  for (const bid of [0, 7]) {
    calls.length = toasts.length = refreshed.length = 0;
    sidebarRenders = 0;
    for (const key of Object.keys(state.views)) delete state.views[key];
    /* Main and two tasks on the list; the task rows say what the node says
       for them (Main's value), which the console never reads anyway */
    const main = { id: 10, name: "Main", show_meta: true, tasks_enabled: true };
    const rows = [main,
      { id: 12, name: "Task A", show_meta: true, task: task(12, 10) },
      { id: 13, name: "Task B", show_meta: true, task: task(13, 10) },
      /* another session's task never reaches this workspace */
      { id: 21, name: "Elsewhere", show_meta: false, task: task(21, 20) },
      { id: 20, name: "Other", show_meta: false, tasks_enabled: true }];
    if (bid) state.remoteSessions[bid] = rows; else state.sessions = rows;
    const mainView = leaf(bid, 10, { id: 10, name: "Main", show_meta: true, from: "socket" });
    const taskA = leaf(bid, 12, { id: 12, name: "Task A", show_meta: true, task: task(12, 10), from: "socket" });
    const taskB = leaf(bid, 13, null);   // no snapshot yet: the list is all it has
    const ws = workspace(bid, 10, [mainView, taskA, taskB]);

    // What a view's strip follows.
    assert.equal(context.sessionMetaOwner(bid, main), main, "Main's own row");
    assert.equal(context.sessionMetaOwner(bid, rows[1]), main, "a task's is Main's row");
    assert.equal(context.sessionMetaOwner(bid, rows[3]), rows[4], "another session's task, that session's");
    assert.equal(context.sessionMetaOwner(bid, { id: 99, task: task(99, 98) }), null, "Main unknown");
    assert.equal(context.sessionViewShowsMeta(mainView), true);
    assert.equal(context.sessionViewShowsMeta(taskA), true);
    assert.equal(context.sessionViewShowsMeta(taskB), true);
    main.show_meta = false;
    assert.equal(context.sessionViewShowsMeta(mainView), true, "Main's socket payload wins over the list");
    assert.equal(context.sessionViewShowsMeta(taskA), false, "a task follows Main's row, not its own payload");
    assert.equal(context.sessionViewShowsMeta(taskB), false, "with or without a payload of its own");
    mainView.session = null;
    assert.equal(context.sessionViewShowsMeta(mainView), false, "Main without a payload reads its row");
    const stranger = leaf(bid, 99, null);
    assert.equal(context.sessionViewShowsMeta(stranger), true, "a session the list does not know is shown");
    assert.equal(context.sessionShowsMeta({ id: 1 }), true, "a node too old to know the column means shown");
    // The seed before the list, and the list landing on a view without a payload.
    main.show_meta = true;
    taskB.root.classList.add("meta-hidden");
    context.syncSessionMetaVisibility();
    assert.equal(hidden(taskB), false, "the list corrects a task view built before it");
    assert.equal(hidden(mainView), false);
    mainView.session = { id: 10, name: "Main", show_meta: true, from: "socket" };

    // Another console hid the strip: the list brings Main's row, and the
    // workspace's refresh lands it on every task view - and again on show.
    main.show_meta = false;
    ws.refreshTasks();
    assert.equal(hidden(taskA), true, "a task view follows Main's row through the list");
    assert.equal(hidden(taskB), true);
    assert.equal(hidden(mainView), false, "Main's own payload still says shown until its socket says otherwise");
    mainView.session.show_meta = false; mainView.updateHead();
    assert.equal(hidden(mainView), true);
    main.show_meta = true;
    ws.refreshTasks();
    assert.equal(hidden(taskA), false);
    assert.equal(hidden(taskB), false);
    assert.equal(hidden(mainView), true, "the list never overrides Main's own payload");
    mainView.session.show_meta = true; mainView.updateHead();

    // A task's head menu: its row reads Main's setting, and a press sets
    // Main's - never the task's - landing on the list row, Main's view and
    // every task view before the stream brings the list.
    let row = openHeadMenu(taskA);
    assert.equal(row.getAttribute("aria-checked"), "true");
    assert.equal(row.getAttribute("role"), "menuitemcheckbox");
    row.click();
    await settle();
    assert.equal(document.querySelector(".menu"), null, "the press closes the menu");
    assert.deepEqual(plain(calls.map(c => [c.bid, c.route, c.method, c.body])),
      [[bid, "sessions/10", "PATCH", { show_meta: false }]], "the PATCH is Main's");
    assert.equal(main.show_meta, false, "the list row has the answer");
    assert.equal(mainView.session.from, "patch", "Main's view holds the answer");
    assert.equal(mainView.repaints, 3, "and repainted its head");
    assert.equal(hidden(mainView), true);
    assert.equal(hidden(taskA), true, "the task whose menu it was");
    assert.equal(hidden(taskB), true, "and its sibling");
    assert.equal(sidebarRenders, 1);
    assert.deepEqual(refreshed, bid ? [bid] : [], "a remote backend's group is re-read, the local one streams");
    assert.equal(taskA.session.show_meta, true, "the task's own payload is left alone: it is not read");
    row = openHeadMenu(taskA);
    assert.equal(row.getAttribute("aria-checked"), "false", "reopened, the row already agrees");
    context.closeAllMenus(null);

    // The sidebar's row on Main: the same setting, the way back once every
    // head - and the ⋮ each carried - is gone.
    row = openSidebarMenu(bid, main);
    assert.equal(row.getAttribute("aria-checked"), "false", "the sidebar agrees with the task's menu");
    row.click();
    await settle();
    assert.deepEqual(plain([calls[1].route, calls[1].body]), ["sessions/10", { show_meta: true }]);
    assert.equal(main.show_meta, true);
    assert.equal(hidden(mainView), false);
    assert.equal(hidden(taskA), false, "every task tab shows its strip again");
    assert.equal(hidden(taskB), false);
    row = openSidebarMenu(bid, main);
    assert.equal(row.getAttribute("aria-checked"), "true");
    context.closeAllMenus(null);
    // Main's own head menu takes the same path.
    row = openHeadMenu(mainView);
    assert.equal(row.getAttribute("aria-checked"), "true");
    row.click();
    await settle();
    assert.deepEqual(plain([calls[2].route, calls[2].body]), ["sessions/10", { show_meta: false }]);
    assert.equal(hidden(taskB), true);
    assert.equal(toasts.length, 0);

    // A refusal changes nothing and is the one bad toast.
    failNext = "Could not reach the backend";
    row = openHeadMenu(taskB);
    assert.equal(row.getAttribute("aria-checked"), "false");
    row.click();
    await settle();
    assert.equal(main.show_meta, false);
    assert.equal(hidden(taskB), true);
    assert.deepEqual(plain(toasts), [["Could not reach the backend", "bad"]]);
    assert.equal(calls.length, 4);
    // A task with no known Main is sent as itself, and the node's refusal
    // reaches the console as a toast rather than a silent press.
    const orphan = leaf(bid, 99, { id: 99, show_meta: true, task: task(99, 98) });
    row = openHeadMenu(orphan);
    row.click();
    await settle();
    assert.equal(calls[4].route, "sessions/99");
    assert.equal(toasts[1][0], "The status bar is shown or hidden from the main session");
  }
  console.log("PASS: the status bar is the session's setting, read and set from every menu and followed by every task tab");
})().catch(error => { console.error(error); process.exit(1); });
