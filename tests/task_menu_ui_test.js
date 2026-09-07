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
const state = { views: {} };
let reviewed;
const context = vm.createContext({
  document, state,
  closeAllMenus() { document.querySelectorAll(".menu").forEach(menu => menu.remove()); return false; },
  backendSupportsEngineDefaults: () => false,
  menuCheckRow: () => document.createElement("button"),
  sessionShowsMeta: () => true, appendSessionTasksToggle() {},
  sessionWorkspace: () => null, isScratchWorkspace: () => true,
  positionAnchoredMenu() {},
  modalReviewTask: (workspace, session) => { reviewed = { workspace, session }; },
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function canMoveScratch(", "function modalMoveWorkspace("),
  between("function workspaceViewFor(", "/* A removed task's condensed conversation"),
  between("class SessionView {", "/* ================= TermView"),
  'globalThis.SessionView = SessionView;',
  'globalThis.Workspace = class {',
  between("  ensureTask(task) {", "  openTask(sid) {"),
  '};',
].join("\n"), context);

for (const bid of [0, 7]) {
  const task = { id: 12, name: "Task", task: { parent: 10, state: "running" } };
  const view = Object.create(context.SessionView.prototype);
  view.tab = { bid, sid: 12 };
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
  assert.equal(button.disabled, true, "running task cannot be reviewed");

  for (const status of ["ready", "running", "failed", "queued", "stopped", "applied", "held"]) {
    const latest = { ...task, model: "list-model", task: { ...task.task, state: status, summary: status } };
    workspace.ensureTask(latest); // Same path used by live session-list refreshes.
    assert.equal(view.taskReviewMenuButton, button, "menu stays open across updates");
    assert.equal(button.isConnected, true);
    assert.equal(button.disabled, !["ready", "failed", "stopped", "applied"].includes(status), status);
    assert.equal(view.session.model, "socket-model", "list updates preserve socket-owned configuration");
  }
  workspace.ensureTask({ ...task, task: { ...task.task, state: "ready", summary: "Finished" } });
  button.click();
  assert.equal(reviewed.workspace, workspace);
  assert.equal(reviewed.session.task.summary, "Finished", "review receives current task metadata");
  assert.equal(button.isConnected, false, "click closes the menu");
  view.showMenu(anchor);
  assert.equal(view.taskReviewMenuButton.disabled, false, "reopening the menu needs no task reopen");
  delete state.views[`s:${bid}:10`];
  view.syncTaskReviewMenu();
  assert.equal(view.taskReviewMenuButton.disabled, true, "missing owner prevents review");
}
console.log("PASS: task menu follows live completion and restart states on local and remote nodes");
