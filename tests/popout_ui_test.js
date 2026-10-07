/* Run with node tests/popout_ui_test.js. No browser or engine required.
   A session tab's own window, against the fake DOM: the window a page is
   (?popout=s:<backend>:<session>, anything else a full console), the tab's
   Move to new window row (first on a tab's menu, never on a sidebar row or in
   a session's own window), the window it opens - this console's page naming
   the session, sized and placed over the pane the tab leaves, one window per
   session - and the tab leaving only once the window exists; a session's
   window saving no layout, closing itself with its tab, its bar without the
   burger or the +, its tab neither dragged nor offering terminals, and what
   it cannot show going to the console it came from: another session's link,
   and a browser, terminal or VNC screen its own model opened. */
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

const ORIGIN = "https://nas.test";

/* One console page: the document, the window around it and the slices of
   app.js under test, with everything else they reach for stubbed. */
function load({ search = "", pathname = "/", opener = null, sessions = [], views = [] } = {}) {
  const document = new FakeDocument();
  document.documentElement = document.createElement("html");
  const storage = new Map();
  const calls = { open: [], close: 0, focus: 0, toasts: [], closed: [], moved: [],
    renders: 0, saved: [], drag: 0, history: 0, locations: [], api: [] };
  const state = { tabs: [], views: {}, layout: null, active: null, activeGroup: null,
    sessions, remoteSessions: {}, backends: [] };
  const window = {
    open(url, name, features) {
      calls.open.push({ url, name, features });
      return window.blockOpen ? null : { focus() { calls.focus++; } };
    },
    close() { calls.close++; },
    opener, screenX: 100, screenY: 50, outerWidth: 1600, innerWidth: 1600,
    outerHeight: 1000, innerHeight: 900,
    screen: { availWidth: 1920, availHeight: 1040 },
    blockOpen: false,
  };
  const context = vm.createContext({
    document, window, state, console, Map, Set, URLSearchParams,
    location: { origin: ORIGIN, pathname, search },
    localStorage: {
      getItem: key => storage.has(key) ? storage.get(key) : null,
      setItem: (key, value) => { storage.set(key, String(value)); calls.saved.push(key); },
      removeItem: key => storage.delete(key),
    },
    TOAST_LONG: 7000,
    toast: (text, tone) => calls.toasts.push([text, tone]),
    navigation: { revision: 1, current: null, reconcile() {} },
    navigationRemember() {}, navigationChanged() { calls.history++; },
    /* the workspace: one pane holding every tab, laid out at a known place */
    workspacePaneForTab: id => state.tabs.some(tab => tab.id === id) ? { id: "pane-1", tabs: state.tabs.map(t => t.id) } : null,
    workspacePanes: () => [{ id: "pane-1", tabs: state.tabs.map(t => t.id), active: state.active }],
    removeTabFromPane: () => null, collapseEmptyPane() {}, normalizeWorkspace() {},
    renderTabs() { calls.renders++; }, renderSidebar() {}, syncSessionBrowserChips() {},
    storedWorkspace: () => ({ kind: "pane" }),
    activateTab: id => { state.active = id; },
    putTabInPane() {},
    findSessionMeta: (bid, sid) => sessions.find(s => (s.bid || 0) === bid && s.id === sid) || null,
    sessionsFor: () => sessions,
    sessionViewFor: (bid, sid) => views.find(v => v.tab.bid === bid && v.tab.sid === sid) || null,
    /* the session menu's own rows */
    createContextMenu() {
      const menu = document.createElement("div");
      menu.className = "menu dyn";
      document.body.appendChild(menu);
      return menu;
    },
    cancelSessionDrag() {}, cancelTabDrag() {}, positionContextMenu() {},
    backendSupportsEngineDefaults: () => false, backendSupportsSessionPinning: () => false,
    sessionShowsMeta: () => true, sessionMetaOwner: (bid, s) => s,
    menuCheckRow: label => { const row = document.createElement("button"); row.textContent = label; return row; },
    chatLogMenu: () => { const row = document.createElement("button"); row.textContent = "Chat log"; return row; },
    appendSessionTasksToggle() {}, isScratchWorkspace: () => false,
    sessionWorkspace: s => s.workspace || null,
    linkForSession: () => ({ ws_name: "Atlas", ws_backend: 2, root: "/srv/atlas" }),
    canMoveScratch: () => false, canMoveProject: () => false,
    openTermTab: () => { throw new Error("a terminal tab was opened"); },
    /* the tab strip's pieces */
    sessionHasActivity: () => false, syncPromptSpinnerPhase() {}, titlePending: () => false,
    suppressContextGestureActivation() {}, wireTabDrag() { calls.drag++; },
    xIcon: () => document.createElement("svg"), plusIcon: () => document.createElement("svg"),
    gearIcon: () => document.createElement("svg"),
    burgerButton: () => { const b = document.createElement("button"); b.className = "icon-btn burger"; return b; },
    showTabAddMenu() {}, wireTabScrolling() {}, wireTabbar() {}, wirePaneDrop() {},
    focusWorkspacePane() {},
    ensureTabView: tab => {
      const root = document.createElement("div");
      root.className = "view";
      return state.views[tab.id] = { root, tab };
    },
    sessionReferenceSequence: 0,
  });
  vm.runInContext([
    between("const $ = (id)", "/* Close buttons"),
    between("const LS_NS = ", "function storedStringSet("),
    between("function saveTabs() {", "function storedTabs("),
    between("function sessionContextMenu(", "/* Live sortable layouts."),
    between("function openSessionTab(", "function openTermTab("),
    between("function handleBrowserActivity(", "/* ================= console navigation"),
    between("function closeTab(", "function isTabVisible("),
    between("function renderWorkspacePane(", "function renderWorkspaceNode("),
    between("function renderTabNode(", "function syncHorizontalOverflow("),
    between("async function openSessionReference(", "document.addEventListener(\"click\", event => {"),
    "globalThis.POPOUT_VALUE = POPOUT; globalThis.popoutTargetOf = popoutTarget;",
  ].join("\n"), context);
  /* the pane the tab sits in, at the place the window should take */
  const pane = document.createElement("section");
  pane.className = "workspace-node workspace-pane";
  pane.dataset.paneId = "pane-1";
  pane.getBoundingClientRect = () => ({ left: 300, top: 42, width: 900, height: 820 });
  document.body.appendChild(pane);
  return { context, document, window, state, calls, pane, storage };
}

const session = (id, name, extra = {}) => ({ id, bid: 0, name, engine: "claude", ...extra });
const sessionTab = s => ({ id: `s:${s.bid || 0}:${s.id}`, type: "session", bid: s.bid || 0, sid: s.id, title: s.name });
const contextEvent = target => new FakeEvent("contextmenu", { clientX: 40, clientY: 30, currentTarget: target });
const rows = document => {
  const menu = document.querySelector(".menu");
  return menu ? menu.children.map(node => node.className === "menu-sep" ? "—" : node.textContent) : [];
};

// ---- which window a page is -----------------------------------------------
{
  const { context } = load();
  const target = context.popoutTargetOf;
  assert.equal(context.POPOUT_VALUE, null, "a page without the query is a full console");
  assert.deepEqual({ ...target("?popout=s:0:12") }, { bid: 0, sid: 12, id: "s:0:12" });
  assert.deepEqual({ ...target("?x=1&popout=s:3:7") }, { bid: 3, sid: 7, id: "s:3:7" },
    "a remote backend's session, beside another parameter");
  for (const bad of ["?popout=s:0:0", "?popout=s:01:2", "?popout=s:0:12x", "?popout=t:0:12",
                     "?popout=s:-1:2", "?popout=", "?popout=s:0:99999999999999999",
                     "?popout=s:1234567890:2", "?other=s:0:12"])
    assert.equal(target(bad), null, `${bad} names no session's window`);
  const popout = load({ search: "?popout=s:0:12" });
  assert.deepEqual({ ...popout.context.POPOUT_VALUE }, { bid: 0, sid: 12, id: "s:0:12" });
  assert.ok(popout.document.documentElement.classList.contains("popout"),
    "the stylesheet's hook is set as the script loads, before the app is shown");
  assert.ok(!load().document.documentElement.classList.contains("popout"));
}

// ---- the tab's Move to new window row --------------------------------------
{
  const harbor = session(12, "Harbor dashboard", { workspace: { root: "/srv/atlas" } });
  const page = load({ sessions: [harbor] });
  const tab = sessionTab(harbor);
  page.state.tabs.push(tab, sessionTab(session(5, "Release checklist")));
  page.state.active = tab.id;
  const node = page.context.renderTabNode(tab, { id: "pane-1", active: tab.id }, page.document.createElement("div"));
  assert.equal(page.calls.drag, 1, "a full console's tab is still dragged between panes");
  node.dispatchEvent(contextEvent(node));
  const menu = rows(page.document);
  assert.equal(menu[0], "Move to new window", "the tab's own verb leads its menu");
  assert.equal(menu[1], "—", "set off from the session's rows by a separator");
  assert.ok(menu.includes("Rename") && menu.includes("Terminal on Atlas"), menu);
  assert.equal(menu.filter(row => row === "Move to new window").length, 1);
  page.document.querySelector(".menu").remove();
  /* the sidebar's row is the same menu without a tab to move */
  page.context.sessionContextMenu(contextEvent(page.document.body), 0, harbor);
  assert.ok(!rows(page.document).includes("Move to new window"), "a sidebar row has no tab to move");
  page.document.querySelector(".menu").remove();

  // A press opens this console's page naming the session, over the pane.
  node.dispatchEvent(contextEvent(node));
  page.document.querySelector(".menu").children[0].click();
  assert.equal(page.calls.open.length, 1);
  assert.deepEqual(page.calls.open[0], {
    url: "/?popout=s:0:12", name: "puppy::s:0:12",
    features: "popup,width=900,height=820,left=400,top=192",
  }, "the pane's size, at its corner on the screen: the window's corner past its frame");
  assert.equal(page.calls.focus, 1, "the new window is brought forward");
  assert.deepEqual(page.state.tabs.map(t => t.id), ["s:0:5"], "the tab has left this window");
  assert.equal(page.calls.close, 0, "a full console never closes itself");
  assert.ok(page.calls.history > 0, "leaving is the tab's ordinary close, one history entry");
  assert.equal(page.document.querySelector(".menu"), null, "the menu went with the press");
}

// ---- the window's geometry and a blocked pop-up -----------------------------
{
  const page = load({ pathname: "/puppy/", sessions: [session(9, "Notes")] });
  page.state.tabs.push(sessionTab(session(9, "Notes")));
  page.pane.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 300 });
  page.window.screen = { availWidth: 400, availHeight: 2000 };
  assert.equal(page.context.moveSessionToWindow("s:0:9"), true);
  assert.deepEqual(page.calls.open[0], {
    url: "/puppy/?popout=s:0:9", name: "puppy:/puppy:s:0:9",
    features: "popup,width=400,height=520,left=100,top=150",
  }, "under a mount the window keeps the console's path; a narrow pane opens at the least width the screen allows");

  const blocked = load({ sessions: [session(9, "Notes")] });
  blocked.state.tabs.push(sessionTab(session(9, "Notes")));
  blocked.window.blockOpen = true;
  assert.equal(blocked.context.moveSessionToWindow("s:0:9"), false);
  assert.deepEqual(blocked.state.tabs.map(t => t.id), ["s:0:9"], "a blocked pop-up leaves the tab in place");
  assert.deepEqual(blocked.calls.toasts, [["Could not open a new window · allow pop-ups for Puppy in this browser", "bad"]]);
  blocked.window.open = () => { throw new Error("SecurityError"); };
  assert.equal(blocked.context.moveSessionToWindow("s:0:9"), false, "and so does a browser that refuses outright");
  assert.equal(blocked.state.tabs.length, 1);
  assert.equal(blocked.context.moveSessionToWindow("s:0:404"), false, "a tab this window does not hold moves nowhere");
  blocked.state.tabs.push({ id: "search", type: "search", title: "Search" });
  assert.equal(blocked.context.moveSessionToWindow("search"), false, "only a session tab has a window of its own");
}

// ---- a session's own window -------------------------------------------------
{
  const harbor = session(12, "Harbor dashboard", { workspace: { root: "/srv/atlas" } });
  const page = load({ search: "?popout=s:0:12", sessions: [harbor] });
  const tab = sessionTab(harbor);
  page.state.tabs.push(tab);
  page.state.active = tab.id;

  page.context.saveTabs();
  assert.deepEqual(page.calls.saved, [], "it never writes the main window's layout");
  const main = load();
  main.context.saveTabs();
  assert.deepEqual(main.calls.saved, ["puppy.tabs"], "a full console still does");

  const bar = page.context.renderWorkspacePane({ id: "pane-1", tabs: [tab.id], active: tab.id });
  assert.equal(bar.querySelector(".burger"), null, "no session list to open");
  assert.equal(bar.querySelector(".tab-add-wrap"), null, "no tab to add");
  assert.equal(bar.querySelectorAll(".tab").length, 1, "the bar holds its one tab");
  assert.equal(page.calls.drag, 0, "and that tab has no pane to be dragged to");
  const full = load({ sessions: [harbor] });
  full.state.tabs.push(tab);
  const fullBar = full.context.renderWorkspacePane({ id: "pane-1", tabs: [tab.id], active: tab.id });
  assert.ok(fullBar.querySelector(".burger") && fullBar.querySelector(".tab-add-wrap"),
    "a full console's leading pane keeps both");

  const node = bar.querySelector(".tab");
  node.dispatchEvent(contextEvent(node));
  const menu = rows(page.document);
  assert.ok(menu.includes("Rename") && menu.includes("Workspace details"), menu);
  assert.ok(!menu.includes("Move to new window"), "it is already the session's own window");
  assert.ok(!menu.some(row => row.startsWith("Terminal on")), "a terminal would be another tab");
  page.document.querySelector(".menu").remove();
  assert.equal(page.context.moveSessionToWindow(tab.id), false);
  assert.equal(page.calls.open.length, 0);

  /* another session never opens here */
  page.context.openSessionTab(0, 5, session(5, "Release checklist"));
  assert.deepEqual(page.state.tabs.map(t => t.id), ["s:0:12"]);

  /* closing its tab closes the window */
  page.context.closeTab(tab.id);
  assert.equal(page.state.tabs.length, 0);
  assert.equal(page.calls.close, 1, "the window goes with its session");
}

// ---- what a session's window hands to the console it came from --------------
{
  const opened = [];
  const console_ = {
    closed: false, location: { origin: ORIGIN, pathname: "/", search: "" },
    consoleWindowKind: () => "console",
    handleBrowserActivity: (...args) => opened.push(["browser", ...args]),
    handleTerminalActivity: (...args) => opened.push(["terminal", ...args]),
    handleVncActivity: (...args) => opened.push(["vnc", ...args]),
    openSessionReference: (...args) => opened.push(["session", ...args]),
    focus() { opened.push(["focus"]); },
  };
  const harbor = session(12, "Harbor dashboard");
  const task = session(14, "Card spacing", { task: { parent: 12 } });
  const page = load({ search: "?popout=s:0:12", opener: console_,
    sessions: [harbor, task, session(5, "Release checklist")] });
  const { context } = page;
  assert.equal(context.openingConsole(), console_);
  assert.equal(context.consoleWindowKind(), "session");
  assert.equal(load().context.consoleWindowKind(), "console");

  context.handleBrowserActivity(0, 12, "turn-1", "A8AR");
  context.handleTerminalActivity(0, 14, "turn-2", "T3RM");
  context.handleVncActivity(0, 12, "turn-3", "V1NC");
  context.handleBrowserActivity(0, 5, "turn-4", "B0TH");     // another session's
  context.handleBrowserActivity(3, 12, "turn-5", "R3MT");    // another backend's
  assert.deepEqual(opened, [
    ["browser", 0, 12, "turn-1", "A8AR"],
    ["terminal", 0, 14, "turn-2", "T3RM"],
    ["vnc", 0, 12, "turn-3", "V1NC"],
  ], "its own session's and its tasks' screens go to the console; other sessions' are that console's own news");
  assert.equal(page.state.tabs.length, 0, "and none becomes a tab here");

  /* only a full console on this origin and mount takes them */
  const refuse = (opener, why) => {
    const other = load({ search: "?popout=s:0:12", opener, sessions: [harbor] });
    assert.equal(other.context.openingConsole(), null, why);
    other.context.handleBrowserActivity(0, 12, "turn-9", "A8AR");
  };
  refuse(null, "no opener");
  refuse({ ...console_, closed: true }, "a closed opener");
  refuse({ ...console_, consoleWindowKind: () => "session" }, "another session's window");
  refuse({ ...console_, location: { origin: ORIGIN, pathname: "/other/", search: "" } }, "another mount");
  refuse({ ...console_, location: { origin: "https://elsewhere.test", pathname: "/" } }, "another origin");
  refuse({ closed: false, get location() { throw new Error("cross-origin"); } }, "an opener that moved to another site");
  refuse({ closed: false, location: { origin: ORIGIN, pathname: "/" } }, "a page that is no console (the sign-in page)");
  assert.equal(opened.length, 3, "none of them was handed anything");
}

// ---- links to other sessions -------------------------------------------------
(async () => {
  const harbor = session(12, "Harbor dashboard");
  const task = session(14, "Card spacing", { task: { parent: 12 } });
  const other = session(5, "Release checklist");
  const catalog = { sessions: [
    { ref: "a".repeat(32) + "/12", bid: 0, id: 12 },
    { ref: "a".repeat(32) + "/14", bid: 0, id: 14 },
    { ref: "a".repeat(32) + "/5", bid: 0, id: 5 },
  ] };
  const opened = [];
  const console_ = {
    closed: false, location: { origin: ORIGIN, pathname: "/", search: "" },
    consoleWindowKind: () => "console",
    openSessionReference: (...args) => opened.push(["reference", ...args]),
    focus() { opened.push(["focus"]); },
  };
  const page = load({ search: "?popout=s:0:12", opener: console_, sessions: [harbor, task, other] });
  const { context, calls } = page;
  context.api = async (bid, route) => {
    calls.api.push(route);
    if (route === "session-links/catalog") return catalog;
    const id = Number(route.split("/")[1]);
    return { session: [harbor, task, other].find(s => s.id === id) };
  };
  context.openSessionLocation = (bid, sid, s, seq) => calls.locations.push([bid, sid, seq]);
  await context.openSessionReference("a".repeat(32) + "/14", 40);
  await context.openSessionReference("a".repeat(32) + "/12", 3);
  assert.deepEqual(calls.locations, [[0, 14, 40], [0, 12, 3]],
    "its own session and its tasks open here, at the message");
  await context.openSessionReference("a".repeat(32) + "/5", 7);
  assert.deepEqual(opened, [["reference", "a".repeat(32) + "/5", 7], ["focus"]],
    "another session opens in the console the window came from");
  assert.equal(calls.locations.length, 2);

  /* with that console gone, a new one opens at the link */
  page.window.opener = null;
  await context.openSessionReference("a".repeat(32) + "/5", 7);
  assert.deepEqual(calls.open.pop(), { url: "/#session=" + "a".repeat(32) + "/5&seq=7",
    name: "_blank", features: undefined });
  page.window.blockOpen = true;
  context.showSessionInConsole("a".repeat(32) + "/5", 7);
  assert.deepEqual(calls.toasts.pop(), ["Could not open Puppy · allow pop-ups for Puppy in this browser", "bad"]);
  /* one that throws mid-call is passed over for a new console */
  page.window.blockOpen = false;
  page.window.opener = { ...console_, openSessionReference() { throw new Error("reloading"); } };
  context.showSessionInConsole("a".repeat(32) + "/5", 7);
  assert.equal(calls.open.pop().name, "_blank");

  /* a full console opens every session itself */
  const full = load({ sessions: [harbor, task, other] });
  full.context.api = context.api;
  full.context.openSessionLocation = (bid, sid, s, seq) => full.calls.locations.push([bid, sid, seq]);
  await full.context.openSessionReference("a".repeat(32) + "/5", 7);
  assert.deepEqual(full.calls.locations, [[0, 5, 7]]);
  assert.equal(full.calls.open.length, 0);

  console.log("PASS: a session tab's own window - the page that is one, the tab's Move to new window " +
    "row, the window it opens over the pane and the tab leaving only once it exists, a blocked " +
    "pop-up keeping it; the window saving no layout, closing with its tab, its bar without the " +
    "burger or the +, its tab neither dragged nor offering terminals; its own model's screens and " +
    "other sessions' links going to the console it came from, else to a new one");
})().catch(error => { console.error(error); process.exit(1); });
