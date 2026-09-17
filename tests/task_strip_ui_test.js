/* Run with node tests/task_strip_ui_test.js. The selected task's two verbs
   on the task strip - Review and the bin between the Tasks button and + -
   against the real task workspace on the fake DOM: where they stand, when
   they show (a task, never Main, never a selection the node's list has not
   named), Review greyed exactly when the menu's row is and the bin greyed
   for a task still working or queued, the names they carry, one press one
   sheet or one request, the focus they hand on when their task goes, and
   the Tasks sheet's Remove asking the bin's question. No browser, engine,
   network or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}

const document = new FakeDocument();
const el = (tag, cls = "", text = "") => {
  const node = document.createElement(tag); node.className = cls; node.textContent = text; return node;
};
const BID = 3;
let sessions = [];
let main = { id: 1, name: "Demo project", status: "idle", engine: "codex", tasks_enabled: true };
const storage = new Map();
const removals = [];      // every removeTask call: [bid, session]
const reviews = [];       // every modalReviewTask call: [workspace, session]
let settle = null;        // resolves the removal in flight
const context = vm.createContext({
  document, el, console,
  state: { views: {} },
  localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
  lsKey: key => key,
  sessionsFor: () => sessions,
  findSessionMeta: (bid, sid) => sid === 1 ? main : sessions.find(session => session.id === sid),
  navigationRemember() {}, navigationChanged() {},
  wireTabbar() {}, wireTabDrag() {}, syncHorizontalOverflow() {}, syncPromptSpinnerPhase() {},
  titlePending: () => false,
  tasksIcon: () => el("svg", "tasks-icon"), plusIcon: () => el("svg", "plus-icon"),
  xIcon: () => el("svg", "x-icon"), trashIcon: () => el("svg", "trash-icon"), reviewIcon: () => el("svg", "review-icon"),
  sessDot: () => el("span", "sess-dot"),
  taskStateClass: () => "", taskStateLabel: task => task.state, taskActivityTitle: () => "",
  promptStatusLabel: label => el("span", "t-state", label),
  modalNewTask() {}, modalReviewTask: (workspace, session) => { reviews.push([workspace, session]); },
  modal() { throw new Error("no dialog expected"); },
  removeTask: (bid, session) => new Promise(resolve => { removals.push([bid, session]); settle = resolve; }),
});
vm.runInContext(`
class SessionView {
  constructor(tab) { this.tab = tab; this.root = el("div", "view chat"); this.session = null; }
  onShow() {} onVisibility() {} syncTaskReviewMenu() {} syncMetaVisibility() {}
  destroy() { this.root.remove(); }
}
let dragTab = null;
${between("function taskReviewable(", "/* A removed task's condensed conversation")}
${between("class SessionWorkspaceView {", "async function modalNewTask(")}
globalThis.SessionWorkspaceView = SessionWorkspaceView;
globalThis.taskRemovable = taskRemovable;
globalThis.taskReviewable = taskReviewable;
`, context);

const task = (id, name, state, extra = {}) => ({
  id, name, status: state === "running" ? "running" : "idle", engine: "codex",
  task: { parent: 1, state, created_at: id, result_seq: 0, needs_approval: false, ...extra },
});
sessions = [main,
  task(2, "Card spacing", "ready"), task(3, "Phone navigation", "running"), task(4, "Feed order", "queued"),
  task(5, "Login copy", "running", { needs_approval: true }), task(6, "Held one", "held"),
  task(7, "Starting one", "pending"), task(8, "Stopped one", "stopped"), task(9, "Failed one", "failed"),
  task(10, "Applied one", "applied"), task(11, "", "ready")];

const workspace = new context.SessionWorkspaceView({ id: `s:${BID}:1`, type: "session", bid: BID, sid: 1, title: "Demo" });
document.body.appendChild(workspace.root);
const bin = workspace.removeButton, review = workspace.reviewButton;
/* a verb's face: absent, greyed, or live */
const face = button => button.classList.contains("hidden") ? "hidden" : button.disabled ? "greyed" : "live";
const binFace = () => face(bin), reviewFace = () => face(review);
const selectedTab = () => workspace.strip.querySelector('[aria-selected="true"]');

/* ---- where they stand: Tasks, Review, the bin, then + ---- */
{
  const actions = workspace.bar.querySelector(".task-tab-actions");
  assert.deepEqual(actions.children.map(node => node.className.split(" ").find(cls => cls.startsWith("task-"))),
    ["task-overview-button", "task-review-button", "task-remove-button", "task-add-button"],
    "Tasks, Review, the bin - the destructive verb last - then +");
  for (const [button, icon] of [[review, ".review-icon"], [bin, ".trash-icon"]]) {
    assert.equal(button.tag, "button");
    assert.equal(button.type, "button", "never a submit");
    assert.ok(button.classList.contains("icon-btn"), "the strip's own button face");
    assert.ok(button.querySelector(icon), "a drawn glyph, never a font character");
  }
  assert.equal(review.getAttribute("aria-haspopup"), "dialog", "Review opens a sheet, like the Tasks button");
}

/* ---- Main never offers them ---- */
assert.equal(workspace.selected, 1);
assert.equal(binFace(), "hidden", "the bin is hidden while Main is selected");
assert.equal(reviewFace(), "hidden", "Review is hidden while Main is selected");

/* ---- a task: both stand, Review greyed exactly as the menu's row, the bin
   greyed unless the node would take the task back ---- */
workspace.openTask(2);
assert.equal(binFace(), "live", "a finished task can go");
assert.equal(reviewFace(), "live", "a finished task can be reviewed");
assert.equal(bin.getAttribute("aria-label"), "Remove Card spacing", "named like the tab's close mark");
assert.equal(bin.title, "Remove Card spacing", "the hover says which conversation goes");
assert.equal(review.getAttribute("aria-label"), "Review Card spacing", "Review named the same way");
assert.equal(review.title, "Review Card spacing");
for (const [id, binExpected, reviewExpected, why] of [
  [3, "greyed", "greyed", "a running task must be stopped first, and has nothing to review yet"],
  [4, "greyed", "greyed", "a queued task is still working"],
  [5, "greyed", "greyed", "a task waiting for an approval is running"],
  [6, "live", "greyed", "a held task is nobody's work, but it has not finished"],
  [7, "live", "greyed", "a task that never got going can go, and has nothing to review"],
  [8, "live", "live", "a stopped task"],
  [9, "live", "live", "a failed task"],
  [10, "live", "live", "an applied task"],
]) {
  workspace.openTask(id);
  assert.equal(binFace(), binExpected, why);
  assert.equal(reviewFace(), reviewExpected, why);
  // the menu's row and the sheet's Remove ask the same questions of the same record
  const record = sessions.find(s => s.id === id).task;
  assert.equal(!review.disabled, context.taskReviewable(record), why);
  assert.equal(!bin.disabled, context.taskRemovable(record), why);
}
workspace.openTask(11);
assert.equal(bin.getAttribute("aria-label"), "Remove Task 11", "an unnamed task by the tab's fallback name");
assert.equal(review.getAttribute("aria-label"), "Review Task 11");
workspace.select(1);
assert.equal(binFace(), "hidden", "back on Main the bin goes");
assert.equal(reviewFace(), "hidden", "and so does Review");

/* ---- live: the selected task's state changes under the reader ---- */
workspace.select(2);
assert.equal(binFace(), "live");
sessions[1] = task(2, "Card spacing", "running");
workspace.refreshTasks();
assert.equal(binFace(), "greyed", "a task started again from elsewhere greys the bin, which keeps its place");
assert.equal(reviewFace(), "greyed", "and greys Review");
sessions[1] = task(2, "Card spacing", "ready");
workspace.refreshTasks();
assert.equal(binFace(), "live", "and both come back once the task stops");
assert.equal(reviewFace(), "live");
sessions[1] = task(2, "Card spacing renamed", "ready");
workspace.refreshTasks();
assert.equal(bin.getAttribute("aria-label"), "Remove Card spacing renamed", "a rename reaches the name");
assert.equal(review.getAttribute("aria-label"), "Review Card spacing renamed");

/* ---- Review: one press, one sheet, for the selected task ---- */
workspace.reviewSelectedTask();
assert.equal(reviews.length, 1, "one sheet");
// the class hands the sheet its own object; the constructor's Proxy wraps
// it for the outside, so compare the workspace by its tab, not identity
assert.equal(reviews[0][0].tab.id, workspace.tab.id, "opened for this workspace");
assert.equal(reviews[0][1].id, 2, "for the selected task, from the node's list");
workspace.select(1);
workspace.reviewSelectedTask();
workspace.openTask(3);
workspace.reviewSelectedTask();
workspace.openTask(6);
workspace.reviewSelectedTask();
workspace.selected = 99;
workspace.reviewSelectedTask();
assert.equal(reviews.length, 1, "Main, a running task, a held task or an unnamed selection opens nothing");
// a greyed Review under the focus hands it to the selected tab
workspace.openTask(2);
review.focus();
sessions[1] = task(2, "Card spacing", "running");
workspace.refreshTasks();
assert.equal(reviewFace(), "greyed");
assert.equal(document.activeElement, selectedTab(), "the focus a greyed Review held passes to its tab");
sessions[1] = task(2, "Card spacing", "ready");
workspace.refreshTasks();
// and so does a greyed bin
bin.focus();
sessions[1] = task(2, "Card spacing", "running");
workspace.refreshTasks();
assert.equal(binFace(), "greyed");
assert.equal(document.activeElement, selectedTab(), "the focus a greyed bin held passes to its tab");
sessions[1] = task(2, "Card spacing", "ready");
workspace.refreshTasks();

/* ---- one press, one request, for the selected task ---- */
(async () => {
  assert.equal(workspace.selected, 2);
  bin.focus();
  let pending = workspace.removeSelectedTask();
  assert.equal(removals.length, 1, "one request");
  assert.equal(removals[0][0], BID);
  assert.equal(removals[0][1].id, 2, "the selected task, from the node's list");
  const again = workspace.removeSelectedTask();
  assert.equal(removals.length, 1, "a second press while the first is out asks nothing");
  await again;
  // the task is gone from the node's list: the strip closes its tab, Main
  // takes the display, and the focus the bin held goes to Main's tab
  sessions = sessions.filter(session => session.id !== 2);
  settle();
  await pending;
  assert.equal(workspace.removing, false);
  workspace.refreshTasks();
  assert.equal(workspace.selected, 1, "Main takes the removed task's place");
  assert.equal(binFace(), "hidden");
  assert.equal(document.activeElement, selectedTab(), "the focus the bin held passes to the selected tab");
  assert.equal(selectedTab().textContent, "Main");

  // focus elsewhere is left alone when the bin goes
  workspace.openTask(6);
  assert.equal(binFace(), "live");
  const elsewhere = el("textarea");
  document.body.appendChild(elsewhere);
  elsewhere.focus();
  sessions = sessions.filter(session => session.id !== 6);
  workspace.refreshTasks();
  assert.equal(binFace(), "hidden");
  assert.equal(document.activeElement, elsewhere, "focus that was not on the bin stays where it was");

  // a press that finds nothing to remove asks nothing: Main selected, a
  // running task selected, a task the node's list has stopped naming
  workspace.select(1);
  await workspace.removeSelectedTask();
  workspace.openTask(3);
  await workspace.removeSelectedTask();
  workspace.selected = 99;
  await workspace.removeSelectedTask();
  assert.equal(removals.length, 1, "nothing to remove, nothing asked");
  workspace.select(1);

  // a refused removal (the node's toast is removeTask's) releases the press
  workspace.openTask(7);
  pending = workspace.removeSelectedTask();
  assert.equal(removals.length, 2);
  settle();   // removeTask itself swallowed the refusal
  await pending;
  assert.equal(workspace.removing, false, "the next press is free again");
  assert.equal(binFace(), "live", "the task is still there, so is the bin");

  /* ---- before the node's list: a restored selection names no task ---- */
  const saved = main;
  main = undefined;
  sessions = [];
  storage.set(`puppy.sessionTasks.${BID}.5`, JSON.stringify({ format: 1, open: [12], active: 12, seen: {} }));
  const fresh = new context.SessionWorkspaceView({ id: `s:${BID}:5`, type: "session", bid: BID, sid: 5, title: "Later" });
  assert.equal(fresh.selected, 12, "the saved selection waits for the list");
  assert.equal(face(fresh.removeButton), "hidden", "no task named yet, no bin");
  assert.equal(face(fresh.reviewButton), "hidden", "and no Review");
  main = saved;

  /* ---- the Tasks sheet's Remove asks the same question ---- */
  sessions = [main, task(2, "Card spacing", "ready"), task(3, "Phone navigation", "running"),
    task(4, "Feed order", "queued"), task(5, "Login copy", "running", { needs_approval: true }),
    task(6, "Held one", "held"), task(7, "Starting one", "pending")];
  workspace.overview = el("div", "task-list");
  workspace.renderedOverview = "";
  workspace.renderOverview(workspace.tasks());
  const cards = workspace.overview.querySelectorAll(".session-task-card");
  const verdicts = Object.fromEntries(cards.map(card => [card.querySelector(".t-name").textContent,
    !card.querySelectorAll(".task-card-actions button")[2].disabled]));
  assert.deepEqual(verdicts, { "Card spacing": true, "Phone navigation": false, "Feed order": false,
    "Login copy": false, "Held one": true, "Starting one": true }, "the sheet's Remove and the bin agree");
  for (const state of ["ready", "held", "pending", "stopped", "failed", "applied"])
    assert.equal(context.taskRemovable({ state }), true, state);
  for (const state of ["running", "queued"])
    assert.equal(context.taskRemovable({ state }), false, state);
  assert.equal(context.taskRemovable(null), false, "no record, nothing to remove");

  console.log("PASS: the task strip's Review and bin between Tasks and +, shown for the selected task and never for Main or an unnamed selection, Review greyed exactly as the menu's row and the bin greyed for a working or queued task, both named like the tab's close mark, one press one sheet or one request, focus handed to the selected tab when a verb greys or goes, and the Tasks sheet's Remove on the bin's rule");
})().catch(error => { console.error(error); process.exit(1); });
