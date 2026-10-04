/* Run with node tests/task_menu_ui_test.js. No browser or engine required. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start);
  return source.slice(start, end);
}
const document = new FakeDocument();
const state = { views: {}, nodeCapabilities: ["session-task-refresh"],
  backends: [{ id: 7, capabilities: ["session-task-refresh"] }] };
let reviewed;
let complete, fail;
const requests = [], notices = [];
const context = vm.createContext({
  document, state,
  closeAllMenus() { document.querySelectorAll(".menu").forEach(menu => menu.remove()); return false; },
  backendSupportsEngineDefaults: () => false,
  menuCheckRow: () => document.createElement("button"),
  chatLogMenu: () => document.createElement("div"),
  sessionViewShowsMeta: () => true, setSessionShowsMeta() {}, appendSessionTasksToggle() {},
  sessionWorkspace: () => null, isScratchWorkspace: () => true,
  positionAnchoredMenu() {},
  modalReviewTask: (workspace, session) => { reviewed = { workspace, session }; },
  api: (bid, path, opts) => new Promise((resolve, reject) => {
    requests.push({ bid, path, opts }); complete = resolve; fail = reject;
  }),
  toast: (text, tone) => notices.push({ text, tone }),
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function backendSupportsTaskRefresh(", "/* \"Enable tasks\""),
  between("function canMoveScratch(", "function modalMoveWorkspace("),
  between("function liveViews(", "/* A removed task's condensed conversation"),
  between("class SessionView {", "/* ================= TermView"),
  'globalThis.SessionView = SessionView;',
  'globalThis.Workspace = class {',
  between("  ensureTask(task) {", "  openTask(sid) {"),
  '};',
].join("\n"), context);

(async () => {
for (const bid of [0, 7]) {
  const task = { id: 12, name: "Task", task: { parent: 10, state: "running" } };
  const view = Object.create(context.SessionView.prototype);
  view.tab = { bid, sid: 12, type: "session" };
  view.root = document.createElement("div");
  view.session = { ...task, model: "socket-model" };
  const workspace = new context.Workspace();
  workspace.tab = { bid, sid: 10 };
  workspace.taskViews = new Map([[12, view]]);
  state.views[`s:${bid}:10`] = workspace;
  const anchor = document.createElement("button");
  document.body.appendChild(anchor);
  view.showMenu(anchor);
  const button = view.taskReviewMenuButton;
  const refresh = view.taskRefreshMenuButton;
  assert.ok(refresh);
  assert.equal(refresh.parentNode.children.indexOf(refresh), refresh.parentNode.children.indexOf(button) + 1,
    "Refresh from Main is immediately under Review changes");
  assert.equal(button.disabled, true, "running task cannot be reviewed");

  for (const status of ["ready", "running", "failed", "queued", "stopped", "applied", "held"]) {
    const latest = { ...task, model: "list-model", task: { ...task.task, state: status, summary: status } };
    workspace.ensureTask(latest); // Same path used by live session-list refreshes.
    assert.equal(view.taskReviewMenuButton, button, "menu stays open across updates");
    assert.equal(button.isConnected, true);
    assert.equal(button.disabled, !["ready", "failed", "stopped", "applied"].includes(status), status);
    assert.equal(refresh.disabled, button.disabled, "refresh follows the same live idle states");
    assert.equal(view.session.model, "socket-model", "list updates preserve socket-owned configuration");
  }
  workspace.ensureTask({ ...task, task: { ...task.task, state: "ready", summary: "Finished" } });
  button.click();
  assert.equal(reviewed.workspace, workspace);
  assert.equal(reviewed.session.task.summary, "Finished", "review receives current task metadata");
  assert.equal(button.isConnected, false, "click closes the menu");
  view.showMenu(anchor);
  assert.equal(view.taskReviewMenuButton.disabled, false, "reopening the menu needs no task reopen");
  const before = requests.length;
  view.taskRefreshMenuButton.click();
  assert.equal(requests.length, before + 1);
  assert.equal(requests[before].path, "sessions/10/tasks/12/refresh");
  assert.equal(requests[before].bid, bid);
  assert.equal(requests[before].opts.operation, "Refreshing task from Main");
  view.showMenu(anchor);
  assert.equal(view.taskRefreshMenuButton.disabled, true, "another menu cannot dispatch a duplicate refresh");
  complete({ refreshed: true, changed_files: 2 });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(view.taskRefreshMenuButton.disabled, false);
  assert.equal(notices.at(-1).text, "Refreshed from Main · 2 files changed");
  view.taskRefreshMenuButton.click(); complete({ refreshed: false, changed_files: 0 });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(notices.at(-1).text, "Task already matches Main");
  view.showMenu(anchor); view.taskRefreshMenuButton.click(); fail(new Error("Task has changes"));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(notices.at(-1).tone, "bad");
  assert.ok(notices.at(-1).text.startsWith("Could not refresh task from Main · "));
  const count = notices.length;
  view.showMenu(anchor); view.taskRefreshMenuButton.click(); fail({ cancelled: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(notices.length, count, "the operation surface owns cancellation feedback");
  view.showMenu(anchor);
  delete state.views[`s:${bid}:10`];
  view.syncTaskReviewMenu();
  assert.equal(view.taskReviewMenuButton.disabled, true, "missing owner prevents review");
  assert.equal(view.taskRefreshMenuButton.disabled, true, "missing owner prevents refresh");
  if (bid) state.backends[0].capabilities = []; else state.nodeCapabilities = [];
  document.querySelectorAll(".menu").forEach(menu => menu.remove());
  view.showMenu(anchor);
  assert.equal([...document.querySelectorAll(".menu button")].some(b => b.textContent === "Refresh from Main"), false,
    "older nodes are never offered the new command");
}
/* Apply to Main when done: the task's own switch, right under Refresh from
   Main, reading the list's record - never a stale copy - and asking for the
   opposite of what it shows. */
{
  const switches = [];
  const rows = [];
  context.menuCheckRow = (label, on, fn) => {
    const row = document.createElement("button");
    row.textContent = label;
    row.setAttribute("aria-checked", String(on));
    row.onclick = fn;
    rows.push(row);
    return row;
  };
  context.setTaskAutoApply = (bid, session, enabled) => switches.push({ bid, id: session.id, enabled });
  state.nodeCapabilities = ["session-task-refresh", "session-task-auto-apply"];
  const listed = { id: 12, name: "Task", task: { parent: 10, state: "ready", auto_apply: null } };
  context.findSessionMeta = (bid, sid) => sid === 12 ? listed : null;
  const view = Object.create(context.SessionView.prototype);
  view.tab = { bid: 0, sid: 12, type: "session" };
  view.root = document.createElement("div");
  view.session = { id: 12, name: "Task", task: { parent: 10, state: "running", auto_apply: null } };
  const anchor = document.createElement("button");
  document.body.appendChild(anchor);
  const open = () => {
    document.querySelectorAll(".menu").forEach(menu => menu.remove());
    rows.length = 0;
    view.showMenu(anchor);
    return rows.find(row => row.textContent === "Apply to Main when done");
  };
  let row = open();
  assert.ok(row, "the switch is offered for a task");
  assert.equal(row.parentNode.children.indexOf(row),
    row.parentNode.children.indexOf(view.taskRefreshMenuButton) + 1, "right under Refresh from Main");
  assert.equal(row.getAttribute("aria-checked"), "false");
  row.onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(switches.at(-1))), { bid: 0, id: 12, enabled: true });
  listed.task = { ...listed.task, auto_apply: { phase: "waiting", rounds: 0, max_rounds: 8, note: "" } };
  row = open();
  assert.equal(row.getAttribute("aria-checked"), "true", "the list's record, not the view's older copy");
  row.onclick();
  assert.equal(switches.at(-1).enabled, false);
  state.nodeCapabilities = ["session-task-refresh"];
  assert.equal(open(), undefined, "a node without the switch never offers it");
  view.session = { id: 12, name: "Main itself" };
  view.tab = { bid: 0, sid: 10, type: "session" };
  state.nodeCapabilities = ["session-task-refresh", "session-task-auto-apply"];
  assert.equal(open(), undefined, "Main has no switch of its own");
}
console.log("PASS: local and remote task menus follow live states; refresh sits below review, gates capability, deduplicates requests and reports success, no-op, refusal and cancellation; the Apply to Main when done switch reads the list and asks for the opposite");
})().catch(error => { console.error(error); process.exitCode = 1; });
