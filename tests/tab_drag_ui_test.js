/* Run with node tests/tab_drag_ui_test.js. Actual workspace/task tab drag
   handlers, task discovery across devices and saved visibility, with
   deterministic geometry and no engine. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument, fire } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}

function harness(bid = 0, namespace = "") {
  const document = new FakeDocument(), storage = new Map(), frames = [], animations = [];
  const sessions = new Map(), panes = [], toolbars = new Map();
  const state = { layout: {}, tabs: [], views: {}, active: "outer-b", activeGroup: "pane" };
  let reduced = false, saves = 0, revealed = null;
  const createElement = document.createElement.bind(document);
  document.createElement = tag => {
    const node = createElement(tag);
    node.getBoundingClientRect = () => {
      const index = node.parentNode ? node.parentNode.children.indexOf(node) : 0;
      const left = index * 100 - ((node.parentNode && node.parentNode.scrollLeft) || 0);
      return { left, top: 40, right: left + 100, bottom: 70, width: 100, height: 30 };
    };
    node.animate = (keys, options) => {
      const animation = { node, keys, options, cancel() { this.cancelled = true; } };
      animations.push(animation);
      return animation;
    };
    return node;
  };
  class SessionView {
    constructor(tab) { this.tab = tab; this.root = document.createElement("div"); this.shows = 0; }
    syncTaskReviewMenu() {}
    captureScroll() { return 17; }
    restoreScroll(value) { this.scroll = value; }
    onShow() { this.shows++; }
    onVisibility() {}
    destroy() { this.destroyed = true; this.root.remove(); }
  }
  const icon = () => document.createElement("svg");
  const context = vm.createContext({
  navigation: { layer: () => () => {} }, navigationRemember: () => {}, navigationChanged: () => {},
    document, state, SessionView,
    lsKey: key => namespace + key,
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
    window: { matchMedia: () => ({ matches: reduced }) },
    requestAnimationFrame: fn => frames.push(fn),
    sessionsFor: node => sessions.get(node) || [],
    findSessionMeta: (node, sid) => (sessions.get(node) || []).find(session => session.id === sid),
    tasksIcon: icon, plusIcon: icon, xIcon: icon,
    syncHorizontalOverflow() {}, syncPromptSpinnerPhase() {}, titlePending: () => false,
    sessionHasActivity: meta => meta.status === "running",
    suppressContextGestureActivation() {},
    workspacePane: id => panes.find(pane => pane.id === id),
    normalizeWorkspace() {}, renderSidebar() {},
    removeTabFromPane(id) {
      const pane = panes.find(pane => pane.tabs.includes(id));
      pane.tabs = pane.tabs.filter(tab => tab !== id);
      if (pane.active === id) pane.active = pane.tabs[0] || null;
    },
    collapseEmptyPane() {},
    workspaceRevision: 0, renderedWorkspaceRevision: 0, renderedWorkspaceSignature: "",
    workspaceSignature: () => "", isTabVisible: () => false,
    refreshRenderedTabbars() {
      for (const pane of panes) {
        const strip = toolbars.get(pane.id).strip;
        strip.replaceChildren(...pane.tabs.map(id => context.renderTabNode(
          { id, type: "session", sid: 1, bid }, pane, strip)));
      }
      return true;
    },
    finishTabScrollLayout: focus => { revealed = focus; },
    saveTabs: () => { saves++; },
  });
  vm.runInContext([
    'let dragTab = null, tabDropMarker = null;',
    between("const el = ", "/* Close buttons"),
    between("function promptStatusBase(", "/* Claude exposes a streaming thinking block"),
    between("function guardNativeTouchDrag(", "/* A compact backend label"),
    between("const SLIDE_MOTION_MS =", "/* Sticky manual ordering"),
    between("function makeTabDragImage(", "function syncHorizontalOverflow("),
    between("function validTabReorder(", "function applySplitRatio("),
    between("let finishPaneResize =", "function showTabAddMenu("),
    between("function renderTabs(", 'document.addEventListener("dragover", event =>'),
    between("const TASK_STATES =", "/* A removed task's condensed conversation"),
    between("class SessionWorkspaceView {", "async function modalNewTask("),
    'globalThis.Workspace = SessionWorkspaceView;',
    'globalThis.currentDrag = () => dragTab;',
  ].join("\n"), context);
  function workspace(parent = 1, ids = [2, 3, 4]) {
    sessions.set(bid, [...(sessions.get(bid) || []),
      { id: parent, name: "Demo project", status: "idle", engine: "codex" },
      ...ids.map(id => ({ id, name: `Task ${id}`, status: "idle", engine: "codex",
        task: { parent, state: "ready", result_seq: 1 } })),
    ]);
    const view = new context.Workspace({ id: `s:${bid}:${parent}`, type: "session", sid: parent, bid });
    document.body.appendChild(view.root);
    for (const id of ids) view.openTask(id);
    return view;
  }
  function toolbar(id, ids) {
    const pane = { id, tabs: ids, active: ids[1] || ids[0] };
    panes.push(pane);
    const bar = document.createElement("div"), strip = document.createElement("div");
    bar.className = "tabbar"; strip.className = "tabs";
    bar.appendChild(strip); document.body.appendChild(bar);
    context.wireTabbar(bar, strip, pane);
    const host = document.createElement("div");
    host.className = "pane-views";
    document.body.appendChild(host);
    context.wirePaneDrop(host, pane);
    const result = { pane, bar, strip, host };
    toolbars.set(id, result);
    context.renderTabs();
    return result;
  }
  function start(tab, pointerType = "mouse") {
    fire(tab, "pointerdown", { pointerType });
    const data = { effectAllowed: "", values: {},
      setData(type, value) { this.values[type] = value; },
      setDragImage(image, x, y) { this.image = image; this.x = x; this.y = y; },
    };
    const event = fire(tab, "dragstart", { dataTransfer: data, clientX: tab.getBoundingClientRect().left + 40 });
    assert.equal(tab.classList.contains("dragging"), false, "dim only after the native card is captured");
    while (frames.length) frames.shift()();
    return { data, event };
  }
  return { document, storage, sessions, context, animations, workspace, toolbar, start, bid,
    setReduced: value => { reduced = value; }, saves: () => saves, revealed: () => revealed };
}
const order = strip => strip.children.map(tab => Number(tab.dataset.sid));
const taskTab = (workspace, id) => workspace.strip.children.find(tab => Number(tab.dataset.sid) === id);
function over(bar, x = 1000) { return fire(bar, "dragover", { clientX: x, clientY: 50, dataTransfer: {} }); }
function drop(bar) { return fire(bar, "drop", { clientX: 1000, dataTransfer: {} }); }

for (const bid of [0, 7]) {
  const desktop = harness(bid), phone = harness(bid);
  const desktopView = desktop.workspace(1, []), phoneView = phone.workspace(1, []);
  const task = { id: 2, name: "Created on desktop", status: "running", engine: "codex",
    task: { parent: 1, state: "running", result_seq: 0 } };
  desktop.sessions.get(bid).push(task);
  desktopView.openTask(2);
  phone.sessions.get(bid).push(structuredClone(task));
  phoneView.refreshTasks();
  assert.deepEqual(order(phoneView.strip), [1, 2], "another device discovers new task tabs from session updates");
  assert.equal(phoneView.selected, 1, "discovery keeps the current conversation selected");
  const runningLabel = taskTab(phoneView, 2).querySelector(".t-state");
  assert.ok(runningLabel.classList.contains("prompt-status-label"));
  assert.equal(runningLabel.getAttribute("aria-label"), "Running", "animation keeps the accessible status stable");
  phone.sessions.get(bid)[1].task.needs_approval = true;
  phoneView.refreshTasks();
  assert.equal(taskTab(phoneView, 2).querySelector(".t-state").textContent, "Needs approval");
  assert.equal(taskTab(phoneView, 2).querySelector(".prompt-status-label"), null,
    "a running task waiting for approval does not animate its status");
  phone.sessions.get(bid)[1].task.needs_approval = false;
  phoneView.refreshTasks();
  assert.ok(taskTab(phoneView, 2).querySelector(".prompt-status-label"), "the animation returns after approval");
  for (const taskState of ["queued", "held", "ready", "failed", "stopped", "applied"]) {
    phone.sessions.get(bid)[1].task.state = taskState;
    phone.sessions.get(bid)[1].task.result_seq = 10;
    phoneView.refreshTasks();
    assert.deepEqual(order(phoneView.strip), [1, 2], "task state changes never hide a tab");
    assert.equal(taskTab(phoneView, 2).querySelector(".prompt-status-label"), null,
      "only the running status animates");
    assert.equal(taskTab(phoneView, 2).querySelector(".t-state").classList.contains("unread"), true,
      "discovery does not mark the answer read");
  }
  const fresh = harness(bid);
  fresh.sessions.set(bid, structuredClone(phone.sessions.get(bid)));
  const freshView = new fresh.context.Workspace(phoneView.tab);
  assert.deepEqual(order(freshView.strip), [1, 2], "a device with no saved tabs shows existing tasks");
  const hiddenView = phoneView.taskViews.get(2);
  taskTab(phoneView, 2).querySelector(".t-close").click();
  assert.equal(hiddenView.destroyed, true);
  assert.deepEqual(order(phoneView.strip), [1], "an explicit close hides even an unread task");
  desktopView.refreshTasks();
  assert.deepEqual(order(desktopView.strip), [1, 2], "a close is local to its device");
  phoneView.refreshTasks();
  assert.deepEqual(order(phoneView.strip), [1], "later session snapshots keep the task hidden");
  phoneView.destroy();
  const restored = new phone.context.Workspace(phoneView.tab);
  assert.deepEqual(order(restored.strip), [1], "explicitly hidden tabs stay hidden after a reload");
  restored.openTask(2);
  assert.deepEqual(order(restored.strip), [1, 2], "Open from a sheet or search restores a hidden tab");
  assert.equal(restored.selected, 2);
  restored.destroy();
  const reopened = new phone.context.Workspace(phoneView.tab);
  assert.deepEqual(order(reopened.strip), [1, 2], "reopening is saved across reloads");
  assert.equal(reopened.selected, 2);
  reopened.destroy();
  desktopView.destroy(); phoneView.destroy(); freshView.destroy();
}

for (const bid of [0, 7]) {
  const h = harness(bid), view = h.workspace();
  view.closeTask(3);
  view.opened = [4, 2];
  view.select(4);
  const savedSessions = h.sessions.get(bid);
  view.destroy();
  h.sessions.delete(bid);
  const restored = new h.context.Workspace(view.tab);
  assert.deepEqual([...restored.opened], [4, 2], "pending bootstrap preserves saved order");
  assert.equal(restored.selected, 4, "pending bootstrap preserves selection");
  assert.deepEqual(JSON.parse(h.storage.get(restored.hiddenStorageKey)), [3]);
  h.sessions.set(bid, [...savedSessions,
    { id: 6, task: { parent: 1, state: "failed", created_at: 20 } },
    { id: 5, task: { parent: 1, state: "ready", created_at: 10 } },
    { id: 11, task: { parent: 10, state: "running", created_at: 1 } },
  ]);
  restored.refreshTasks();
  assert.deepEqual(order(restored.strip), [1, 4, 2, 5, 6],
    "tasks created while away append in creation order, within their own Main");
  assert.equal(restored.selected, 4);
  const removed = restored.taskViews.get(4);
  h.sessions.set(bid, h.sessions.get(bid).filter(session => ![3, 4].includes(session.id)));
  restored.refreshTasks();
  assert.deepEqual(order(restored.strip), [1, 2, 5, 6], "removed tasks lose their tabs");
  assert.equal(removed.destroyed, true);
  assert.equal(restored.selected, 1, "removing the selected task returns to Main");
  assert.deepEqual([...restored.hidden], [], "removed tasks leave no hidden-tab preference");
  restored.destroy();
}

{
  const h = harness(0, "/demo:"), view = h.workspace();
  view.closeTask(2);
  assert.ok(h.storage.has("/demo:puppy.sessionTasks.0.1.hidden"), "hidden preferences use the mount namespace");
  assert.equal(h.storage.has("puppy.sessionTasks.0.1.hidden"), false);
  view.destroy();
  for (const raw of ["null", "{}", "[true]", "[0]", "[1]", "[2,2]", "[2.5]", '"[2]"', "[", JSON.stringify(Array.from({length:65}, (_, i) => i + 2))]) {
    h.storage.set(view.hiddenStorageKey, raw);
    const before = [...h.storage];
    assert.throws(() => new h.context.Workspace(view.tab), /Invalid saved hidden task tabs|JSON/);
    assert.deepEqual([...h.storage], before, "invalid preferences are rejected without rewriting browser state");
  }
}

for (const bid of [0, 7]) {
  const h = harness(bid), view = h.workspace(), outer = h.toolbar("pane", ["outer-a", "outer-b"]);
  view.select(3);
  const active = view.activeView(), sourceTab = taskTab(view, 2);
  const { data } = h.start(sourceTab);
  assert.equal(data.effectAllowed, "move");
  assert.equal(data.y, 1, "card uses the workspace tab's below-cursor anchor");
  assert.equal(data.x, 40);
  assert.equal(data.image.classList.contains("tab-drag-image"), true);
  assert.equal(data.image.classList.contains("task-tab"), true, "detached card retains its task sizing");
  assert.equal(data.image.classList.contains("active"), false);
  assert.equal(data.image.getAttribute("aria-hidden"), "true");
  assert.equal(data.image.style.width, "100px");
  assert.equal(sourceTab.classList.contains("dragging"), true);
  assert.equal(view.strip.classList.contains("reordering"), true);
  assert.equal(fire(view.bar, "dragenter", { dataTransfer: {} }).defaultPrevented, true);
  assert.equal(over(view.bar).defaultPrevented, true);
  assert.deepEqual(order(view.strip), [1, 3, 4, 2], "slot and siblings move before dropping");
  assert.deepEqual([...view.opened], [2, 3, 4], "hover does not persist an order");
  assert.ok(h.animations.length > 0, "uses shared sibling slide animation");
  assert.ok(h.animations.every(animation => animation.options.duration === 180));
  assert.equal(over(outer.bar).defaultPrevented, false, "tasks cannot enter workspace tab bars");
  assert.equal(over(outer.host).defaultPrevented, false, "tasks cannot split panes");
  assert.equal(drop(outer.bar).defaultPrevented, false);
  assert.equal(drop(outer.host).defaultPrevented, false);
  const saves = h.saves();
  h.context.renderTabs("outer-b");
  assert.equal(h.saves(), saves, "outer rerenders are deferred during task drags too");
  h.sessions.get(bid).find(session => session.id === 2).task.state = "failed";
  view.refreshTasks();
  assert.equal(taskTab(view, 2), sourceTab, "live updates keep the native source connected");
  assert.equal(drop(view.bar).defaultPrevented, true);
  fire(sourceTab, "dragend");
  assert.deepEqual([...view.opened], [3, 4, 2]);
  assert.deepEqual(order(view.strip), [1, 3, 4, 2]);
  assert.equal(view.activeView(), active, "reorder preserves the selected conversation and its view");
  assert.equal(taskTab(view, 2).querySelector(".t-state").textContent, "Failed", "deferred metadata is rendered");
  assert.equal(h.revealed(), "outer-b", "pending outer focus is replayed");
  assert.equal(h.context.currentDrag(), null);
  assert.equal(data.image.isConnected, false);
  assert.equal(view.strip.classList.contains("reordering"), false);
  assert.deepEqual(JSON.parse(h.storage.get(view.storageKey)).open, [3, 4, 2]);
  const restored = new h.context.Workspace(view.tab);
  assert.deepEqual(order(restored.strip), [1, 3, 4, 2], "order survives reload");
  assert.equal(restored.selected, 3);
  restored.destroy();

  const cancelled = taskTab(view, 2), ghost = h.start(cancelled).data.image;
  over(view.bar, -200);
  assert.deepEqual(order(view.strip), [1, 2, 3, 4], "Main remains first even past the left edge");
  fire(cancelled, "dragend"); // Escape or a drop outside an accepted strip.
  assert.deepEqual(order(view.strip), [1, 3, 4, 2], "cancel restores order even with an unchanged render signature");
  assert.equal(ghost.isConnected, false);
  assert.ok(!taskTab(view, 1).draggable, "Main is not draggable");
  const other = h.workspace(10, [11, 12]);
  const source = taskTab(view, 4);
  h.start(source);
  assert.equal(over(other.bar).defaultPrevented, false, "tasks cannot move to another session");
  assert.equal(drop(other.bar).defaultPrevented, false);
  fire(source, "dragend");
  view.closeTask(4);
  view.openTask(4);
  assert.deepEqual(order(view.strip), [1, 3, 2, 4], "reopened tasks append without sorting the others");
  fire(taskTab(view, 3), "keydown", { key: "ArrowRight" });
  assert.equal(view.selected, 2, "keyboard navigation follows the reordered strip");
  const hide = taskTab(view, 3).querySelector(".t-close");
  hide.click();
  assert.equal(view.selected, 2, "close mark still hides without activating the task");
  assert.deepEqual(order(view.strip), [1, 2, 4]);

  const touchTab = taskTab(view, 2);
  const touch = h.start(touchTab, "touch");
  assert.equal(touch.event.defaultPrevented, true);
  assert.equal(h.context.currentDrag(), null, "touch swipes are left to the browser, as for workspace tabs");
  fire(touchTab, "touchend");
  h.setReduced(true);
  const animationCount = h.animations.length;
  h.start(touchTab);
  over(view.bar);
  assert.equal(h.animations.length, animationCount, "reduced motion keeps immediate slot movement");
  const destroyedGhost = h.context.currentDrag().dragImage;
  view.destroy();
  assert.equal(h.context.currentDrag(), null, "closing the owning workspace clears an active drag");
  assert.equal(destroyedGhost.isConnected, false);
}

for (const change of ["remove", "open", "discover"]) {
  const h = harness(), view = h.workspace();
  view.closeTask(4);
  const tab = taskTab(view, 2);
  h.start(tab); over(view.bar);
  if (change === "remove") {
    h.sessions.set(0, h.sessions.get(0).filter(session => session.id !== 2));
    view.refreshTasks();
  } else if (change === "open") view.openTask(4);
  else {
    h.sessions.get(0).push({ id: 5, task: { parent: 1, state: "running" } });
    view.refreshTasks();
  }
  drop(view.bar); fire(tab, "dragend");
  assert.deepEqual([...view.opened], change === "remove" ? [3] : change === "open" ? [2, 3, 4] : [2, 3, 5],
    "stale drops never restore a removed tab or discard a newly opened one");
  assert.equal(h.context.currentDrag(), null);
}

{
  const h = harness(), view = h.workspace();
  const outer = h.toolbar("pane", ["outer-a", "outer-b", "outer-c"]);
  const tab = outer.strip.children[0], { data } = h.start(tab);
  assert.equal(data.image.classList.contains("tab-drag-image"), true);
  assert.equal(data.y, 1);
  assert.equal(over(view.bar).defaultPrevented, false, "workspace tabs cannot enter task strips");
  assert.equal(drop(view.bar).defaultPrevented, false);
  over(outer.bar); drop(outer.bar); fire(tab, "dragend");
  assert.deepEqual([...outer.pane.tabs], ["outer-b", "outer-c", "outer-a"]);
  assert.equal(outer.pane.active, "outer-b", "workspace reorder still preserves selection");
  const cancelled = outer.strip.children[0];
  h.start(cancelled); over(outer.bar); fire(cancelled, "dragend");
  assert.deepEqual(outer.strip.children.map(node => node.dataset.tabId), ["outer-b", "outer-c", "outer-a"]);
  const destination = h.toolbar("other", ["outer-d"]);
  h.start(outer.strip.children[0]);
  over(destination.bar); drop(destination.bar);
  assert.deepEqual([...destination.pane.tabs], ["outer-d", "outer-b"], "outer cross-pane moves still work");
  assert.equal(destination.pane.active, "outer-b");
  assert.equal(h.document.querySelector(".tab-drop-marker"), null);
}
console.log("PASS: task discovery across devices, explicit hiding, reloads, scope, shared tab dragging, live updates and touch");
