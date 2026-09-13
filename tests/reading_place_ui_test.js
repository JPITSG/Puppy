/* Run with node tests/reading_place_ui_test.js. Scroll position memory for
   sessions and tasks: the real SessionView landing and place methods and the
   real task workspace, against the fake DOM with a modelled transcript
   layout. No browser, engine, network, or quota. */
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
// Places are built inside the vm realm; compare their values, not their prototype.
const plain = value => JSON.parse(JSON.stringify(value));
const el = (tag, cls = "", text = "") => {
  const node = document.createElement(tag); node.className = cls; node.textContent = text; return node;
};
const sessions = [];
const storage = new Map();
let remembered = 0, changed = 0;
const context = vm.createContext({
  document, el, console,
  state: { views: {} },
  localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
  lsKey: key => key,
  sessionsFor: () => sessions,
  findSessionMeta: (bid, sid) => sessions.find(session => session.id === sid),
  navigationRemember: () => { remembered++; }, navigationChanged: () => { changed++; },
  wireTabbar() {}, wireTabDrag() {}, syncHorizontalOverflow() {}, syncPromptSpinnerPhase() {},
  tasksIcon: () => el("svg"), plusIcon: () => el("svg"), xIcon: () => el("svg"),
  taskStateClass: () => "", taskStateLabel: () => "Ready", taskActivityTitle: () => "",
  promptStatusLabel: label => el("span", "t-state", label),
  modalNewTask() {}, modal() { throw new Error("no dialog expected"); },
});
// The real SessionView keeps its name inside a closure so the harness can
// build a small view on its prototype's place, landing and transcript methods.
vm.runInContext("(() => {" + between("class SessionView {", "/* ================= TermView") +
  "\nglobalThis.Chat = SessionView; })();", context);

/* The transcript's geometry, as the browser would lay it out: one node per
   persisted event, stacked, in a box whose scroll offset is clamped and
   which reads as empty - no height, offset 0 - while it or its workspace is
   hidden, exactly as display:none reports. Heights may change while a view
   is hidden, which is what an anchored place exists for. */
const EDGE = 100;
function layout(view, heights, clientHeight = 300) {
  const box = view.scroll;
  const starts = () => heights.map((_, i) => heights.slice(0, i).reduce((a, b) => a + b, 0));
  const total = () => heights.reduce((a, b) => a + b, 0);
  const visible = () => {
    for (let node = view.root; node; node = node.parentNode) {
      if (node === document.body) return true;
      if (node.classList && node.classList.contains("view") && !node.classList.contains("on")) return false;
    }
    return false;
  };
  let offset = 0;
  Object.defineProperties(box, {
    scrollTop: { configurable: true, get: () => visible() ? offset : 0,
      set(value) { if (visible()) offset = Math.max(0, Math.min(total() - clientHeight, value)); } },
    clientHeight: { configurable: true, get: () => visible() ? clientHeight : 0 },
    scrollHeight: { configurable: true, get: () => visible() ? total() : 0 },
  });
  box.getBoundingClientRect = () => ({ top: EDGE, bottom: EDGE + clientHeight, left: 0, right: 600, width: 600, height: clientHeight });
  view.inner.replaceChildren();
  heights.forEach((_, i) => {
    const node = el("div", "msg");
    node.dataset.seq = String(i + 1);
    node.getBoundingClientRect = () => {
      const top = EDGE + starts()[i] - offset;
      return { top, bottom: top + heights[i], left: 0, right: 600, width: 600, height: heights[i] };
    };
    view.inner.appendChild(node);
  });
  view.newestSeq = heights.length;
  view.detach = () => { offset = 0; };   // what removing the node from the document does
  return heights;
}

vm.runInContext(`
class SessionView {
  constructor(tab) {
    this.tab = tab;
    this.root = el("div", "view chat");
    this.scroll = el("div", "chat-scroll");
    this.inner = el("div", "chat-inner");
    this.scroll.appendChild(this.inner);
    this.root.appendChild(this.scroll);
    this.session = null;
    this.status = "idle";
    this.newestSeq = 0;
    this.detached = false;
    this.place = null;
    this.onScreen = false;
    this.composer = { focused: 0, typing: true,
      focus() { this.focused++; }, stopTyping() { this.typing = false; }, syncUploadButton() {} };
    if (typeof globalThis.built === "function") globalThis.built(this);
  }
  syncTaskReviewMenu() {}
  destroy() { this.destroyed = true; this.root.remove(); }
}
for (const name of ["onShow", "onVisibility", "land", "captureScroll", "restoreScroll", "applyPlace",
                    "scrollBottom", "atBottom", "findEventNode", "syncGutter", "syncHeadOverflow",
                    "syncComposerMeta", "syncComposerOverflow"])
  SessionView.prototype[name] = Chat.prototype[name];
let dragTab = null;
${between("class SessionWorkspaceView {", "async function modalNewTask(")}
globalThis.SessionView = SessionView;
globalThis.SessionWorkspaceView = SessionWorkspaceView;
`, context);
const { SessionView } = context;

/* ---- one view: capture, landing, and every reason not to keep a place ---- */
{
  const view = new SessionView({ id: "s:0:9", type: "session", bid: 0, sid: 9 });
  const heights = layout(view, [100, 100, 100, 100, 100, 100, 100, 100, 100, 100]);   // 1000 tall, box 300
  assert.equal(view.captureScroll(), null, "a hidden box has nothing to read");
  view.root.classList.add("on");
  document.body.appendChild(view.root);
  view.onShow(true);
  assert.equal(view.scroll.scrollTop, 700, "a first showing lands on the tail");
  assert.equal(view.composer.focused, 1);
  assert.equal(view.onScreen, true);

  view.scroll.scrollTop = 250;
  let place = view.captureScroll();
  assert.deepEqual(plain(place), { seq: 3, offset: -50, top: 250, bottom: false, newest: 10 },
    "the first message still below the box's top edge, and how far it stands from it");
  view.scroll.scrollTop = 700;
  assert.equal(view.captureScroll().bottom, true, "the exact foot is the tail");
  view.scroll.scrollTop = 600;
  place = view.captureScroll();
  assert.equal(place.bottom, false, "a paragraph short of the foot is a place, not the tail");
  assert.equal(place.seq, 7);

  // Re-showing a view that never left keeps the reader exactly where they are.
  view.scroll.scrollTop = 250;
  view.onShow(true);
  assert.equal(view.scroll.scrollTop, 250, "a re-show never moves the transcript");
  assert.equal(view.composer.focused, 2, "but still gives the box focus");

  const hide = () => { view.onVisibility(false); view.root.classList.remove("on"); };
  const show = () => { view.root.classList.add("on"); view.onShow(false); };
  const roundTrip = () => { hide(); show(); };

  view.scroll.scrollTop = 250;
  hide();
  assert.equal(view.composer.typing, false, "leaving the screen ends the typing lease");
  assert.equal(view.onScreen, false);
  assert.deepEqual(plain(view.place), { seq: 3, offset: -50, top: 250, bottom: false, newest: 10 });
  assert.equal(view.scroll.scrollTop, 0, "hidden, the box reads as empty");
  show();
  assert.equal(view.scroll.scrollTop, 250, "an idle conversation goes back to the reader's place");
  assert.equal(view.place, null, "the place is spent by the landing");
  assert.equal(view.composer.focused, 2, "landing without focus leaves the box alone");

  // The anchor, not the pixel: a card above the place grew while the view was hidden.
  view.scroll.scrollTop = 250;
  hide();
  heights[0] += 40;
  show();
  assert.equal(view.scroll.scrollTop, 290, "the same message stands at the same height");
  assert.equal(view.findEventNode(3).getBoundingClientRect().top - EDGE, -50);
  heights[0] -= 40;

  // A narrower box after a resize: the place is measured against its top edge.
  view.scroll.scrollTop = 250;
  hide();
  heights.forEach((_, i) => { heights[i] = 150; });
  show();
  assert.equal(view.scroll.scrollTop, 350, "re-laid out, the anchored message is still the one on screen");
  heights.forEach((_, i) => { heights[i] = 100; });

  // The tail is the tail, whatever grew.
  view.scroll.scrollTop = 700;
  hide();
  heights[9] += 100;   // the same messages, the last one rendered taller meanwhile
  show();
  assert.equal(view.scroll.scrollTop, 800, "following the tail is restored as the tail");
  heights[9] -= 100;

  // A running turn shows its newest message.
  view.scroll.scrollTop = 250;
  hide();
  view.status = "running";
  show();
  assert.equal(view.scroll.scrollTop, 700, "a running turn lands on the tail");
  view.status = "idle";

  // So does one that ran while the tab was away and left newer messages.
  view.scroll.scrollTop = 250;
  hide();
  view.newestSeq = 11;
  show();
  assert.equal(view.scroll.scrollTop, 700, "newer messages than the place knew of land on the tail");
  view.newestSeq = 10;

  // A detached history window is the place the reader chose, running or not.
  view.scroll.scrollTop = 250;
  view.detached = true; view.status = "running";
  roundTrip();
  assert.equal(view.scroll.scrollTop, 250, "a detached window keeps its place through a running turn");
  view.detached = false; view.status = "idle";

  // A message no longer in the transcript cannot be gone back to.
  view.scroll.scrollTop = 250;
  hide();
  for (const node of view.inner.children.slice(0, 4)) node.remove();
  show();
  assert.equal(view.scroll.scrollTop, 700, "a rebuilt transcript without the anchor lands on the tail");
  layout(view, heights);

  // The nearest older message stands in for one folded into a card meanwhile.
  view.scroll.scrollTop = 250;
  hide();
  view.inner.children[2].remove();
  show();
  assert.equal(view.findEventNode(3).dataset.seq, "2");
  assert.equal(view.scroll.scrollTop, 150, "the message before it takes the anchor's height");
  layout(view, heights);

  // A place with no persisted message on screen is its pixel offset.
  view.scroll.scrollTop = 250;
  hide();
  view.place.seq = 0;
  show();
  assert.equal(view.scroll.scrollTop, 250);

  /* The workspace rebuild's hand-over: a view on the screen takes its place
     back at once, a hidden one holds it for its landing. */
  view.scroll.scrollTop = 420;
  let saved = view.captureScroll();
  view.detach();
  view.restoreScroll(saved);
  assert.equal(view.scroll.scrollTop, 420, "re-attached under the reader, the place is taken back at once");
  view.onShow(true);
  assert.equal(view.scroll.scrollTop, 420, "and the showing that follows does not move it");
  hide();
  saved = view.captureScroll();
  assert.deepEqual(plain(saved), plain(view.place), "a hidden view answers with the place it kept");
  view.restoreScroll({ ...saved, top: 380, seq: 4, offset: -20 });
  assert.equal(view.scroll.scrollTop, 0, "a hidden view holds a place rather than writing into nothing");
  show();
  assert.equal(view.scroll.scrollTop, 320, "and lands on it when shown");
  hide();
  view.restoreScroll(null);
  show();
  assert.equal(view.scroll.scrollTop, 700, "no place to go back to is the tail");
  view.root.remove();
}

/* ---- the task workspace: Main and task tabs, closes and vanishing tasks ---- */
{
  const geometry = { 1: [100, 100, 100, 100, 100, 100, 100, 100, 100, 100], 2: [100, 100, 100, 100], 3: [100, 100] };
  context.built = view => layout(view, geometry[view.tab.sid]);
  sessions.push({ id: 1, name: "Demo", status: "idle", engine: "codex" },
    { id: 2, name: "Task two", status: "idle", engine: "codex", task: { parent: 1, state: "ready", result_seq: 1, created_at: 1 } },
    { id: 3, name: "Task three", status: "idle", engine: "codex", task: { parent: 1, state: "ready", result_seq: 1, created_at: 2 } });
  const workspace = new context.SessionWorkspaceView({ id: "s:0:1", type: "session", bid: 0, sid: 1, title: "Demo" });
  context.state.views["s:0:1"] = workspace;
  const main = workspace.taskViews.get(1);
  assert.equal(main.onScreen, false, "nothing lands before the workspace is on the screen");
  workspace.root.classList.add("on");
  document.body.appendChild(workspace.root);
  workspace.onVisibility(true);
  workspace.onShow(true);
  assert.equal(main.scroll.scrollTop, 700, "Main lands on its tail when the tab opens");
  assert.equal(main.composer.focused, 1);

  main.scroll.scrollTop = 250;
  workspace.select(2);
  const task = workspace.taskViews.get(2);
  assert.equal(main.onScreen, false);
  assert.equal(main.composer.typing, false, "switching tabs ends Main's typing lease");
  assert.deepEqual(plain(main.place), { seq: 3, offset: -50, top: 250, bottom: false, newest: 10 });
  assert.equal(task.scroll.scrollTop, 100, "a task opened for the first time lands on its tail");
  assert.equal(task.composer.focused, 1, "and takes the focus a selection gives");
  assert.equal(task.onScreen, true);

  task.scroll.scrollTop = 30;
  workspace.select(1);
  assert.equal(main.scroll.scrollTop, 250, "back on Main, the reader's place is restored");
  assert.equal(main.composer.focused, 2);
  assert.deepEqual(plain(task.place), { seq: 1, offset: -30, top: 30, bottom: false, newest: 4 });
  workspace.select(2);
  assert.equal(task.scroll.scrollTop, 30, "and so is the task's");
  workspace.select(1);
  assert.equal(main.scroll.scrollTop, 250, "again and again");

  main.scroll.scrollTop = 400;
  const focused = main.composer.focused;
  workspace.select(1);
  assert.equal(main.scroll.scrollTop, 400, "re-selecting the current tab moves nothing");
  assert.equal(main.composer.focused, focused + 1, "and focuses its box");

  main.status = "running";
  workspace.select(2); workspace.select(1);
  assert.equal(main.scroll.scrollTop, 700, "a running Main shows its newest message");
  main.status = "idle";

  main.scroll.scrollTop = 250;
  workspace.select(2);
  main.newestSeq = 12;   // a turn ran on another device while the tab was away
  workspace.select(1);
  assert.equal(main.scroll.scrollTop, 700, "Main that ran while the task tab was open shows the result");
  main.newestSeq = 10;

  // Closing the tab the reader is on shows Main where they left it, focused.
  main.scroll.scrollTop = 250;
  workspace.select(2);
  const before = main.composer.focused;
  const closing = workspace.taskViews.get(2);
  workspace.closeTask(2);
  assert.equal(workspace.selected, 1);
  assert.equal(main.scroll.scrollTop, 250, "Main comes back to the reader's place when the task tab closes");
  assert.equal(main.composer.focused, before + 1, "and takes the closed tab's focus");
  assert.equal(closing.destroyed, true);
  assert.equal(workspace.taskViews.has(2), false);

  // A selected task vanishing from the node's list falls back to Main without stealing focus.
  main.scroll.scrollTop = 250;
  workspace.openTask(3);
  assert.equal(workspace.selected, 3);
  const focusedBefore = main.composer.focused;
  sessions.splice(sessions.findIndex(session => session.id === 3), 1);
  workspace.refreshTasks();
  assert.equal(workspace.selected, 1);
  assert.equal(main.scroll.scrollTop, 250, "Main lands on its place when its selected task vanishes");
  assert.equal(main.composer.focused, focusedBefore, "without taking the reader's focus");

  /* The workspace rebuild from renderTabs: every view is asked for its place
     and handed it back around the re-attachment; the shown one takes it at
     once, the hidden ones keep theirs, and the showing that follows lands
     only what came onto the screen. */
  sessions.push({ id: 2, name: "Task two", status: "idle", engine: "codex", task: { parent: 1, state: "ready", result_seq: 1, created_at: 1 } });
  workspace.openTask(2);
  const task2 = workspace.taskViews.get(2);
  task2.scroll.scrollTop = 30;
  workspace.select(1);
  main.scroll.scrollTop = 250;
  let saved = workspace.captureScroll();
  assert.deepEqual(plain(saved.map(([sid]) => sid).sort()), [1, 2]);
  workspace.root.remove(); main.detach(); task2.detach();
  document.body.appendChild(workspace.root);
  workspace.restoreScroll(saved);
  workspace.onVisibility(true);
  workspace.onShow(true);
  assert.equal(main.scroll.scrollTop, 250, "the visible conversation keeps its place through a rebuild");
  workspace.select(2);
  assert.equal(task2.scroll.scrollTop, 30, "and the hidden one kept the place it had");
  workspace.select(1);

  // The whole tab leaving the screen and coming back.
  main.scroll.scrollTop = 250;
  workspace.onVisibility(false);
  assert.equal(main.onScreen, false);
  assert.equal(task2.composer.typing, false, "every conversation's typing lease ends with the tab");
  workspace.root.classList.remove("on");
  saved = workspace.captureScroll();
  assert.deepEqual(plain(saved.find(([sid]) => sid === 1)[1]), { seq: 3, offset: -50, top: 250, bottom: false, newest: 10 });
  workspace.root.remove(); main.detach();
  document.body.appendChild(workspace.root);
  workspace.restoreScroll(saved);
  assert.equal(main.scroll.scrollTop, 0, "a hidden tab writes nothing");
  workspace.root.classList.add("on");
  workspace.onVisibility(true);
  workspace.onShow(false);
  assert.equal(main.scroll.scrollTop, 250, "the tab selected again lands its conversation on the reader's place");
  assert.equal(remembered > 0 && changed > 0, true, "selections went through the history hooks");
}
console.log("PASS: the reader's place across tab switches - anchored message and exact tail, re-shows, running turns, newer messages, detached windows, rebuilt transcripts, Main and task tabs, closed and vanished tasks, and the workspace rebuild hand-over");
