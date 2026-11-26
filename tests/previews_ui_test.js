/* Run with node tests/previews_ui_test.js. A preview an agent shows is a tab
   beside its chat and a link in its answer: the tab layout keeps a preview
   tab by its ID and session, the node's announcements open one beside the
   chat without taking focus, reload one that is open and end a closed one -
   each once, though a local console hears it twice - the link opens the tab
   on a plain press and is the page's own address for a middle or modified
   one, says so when the preview is gone and is words where there is no
   session, show_preview's card offers the preview, the pane frames the
   preview sandboxed and stops it behind a confirm, and a kept-alive view
   moves where the browser can move it. No browser, engine, network or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const {FakeDocument, FakeElement, FakeEvent} = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
};

Object.defineProperty(FakeElement.prototype, "childNodes", {
  configurable: true,
  get() {
    const nodes = [];
    if (this._text) {
      const text = this.ownerDocument.createTextNode(this._text);
      this._text = "";
      nodes.push(text);
    }
    return nodes.concat(this.children);
  },
});

const document = new FakeDocument();
const icon = () => document.createElement("svg");
const toasts = [], reads = [], opened = [], activated = [], confirms = [], closed = [];
let confirmAnswer = true;
const state = {backends: [], remoteOk: {}, tabs: [], views: {}, instance: "desk",
  sessions: [{id: 5, name: "Plates", color: "#4a90e2"}, {id: 6, name: "Task", task: {parent: 5}}]};
const panes = {"s:0:5": {id: "pane-1"}};
const context = vm.createContext({
  document, Map, Set, URL, console, state, Promise, setImmediate,
  POPOUT: null, TOAST_LONG: 7000,
  refreshIcon: icon, stopSquareIcon: icon, downloadIcon: icon,
  toast: (text, tone) => toasts.push([text, tone]),
  api: (bid, route, options = {}) => {
    const read = {bid, route, options};
    read.promise = new Promise((resolve, reject) => { read.resolve = resolve; read.reject = reject; });
    reads.push(read);
    return read.promise;
  },
  backendConnectionAllowed: bid => !bid || state.remoteOk[bid] !== false,
  backendName: bid => bid ? (state.backends.find(item => item.id === bid) || {}).name : state.instance,
  remoteAvailability: () => "bad",
  backendStateNote: () => "Backend unavailable",
  findSessionMeta: (bid, sid) => bid ? null : state.sessions.find(item => item.id === sid),
  liveViews: () => [],
  workspacePaneForTab: id => panes[id] || null,
  putTabAfter: (id, after, group) => opened.push({id, after, group}),
  putTabInPane: (id, group) => opened.push({id, after: null, group}),
  activateTab: id => activated.push(id),
  renderTabs() {}, syncTabOrderFromLayout() {}, saveTabs() {},
  openSessionTab: (bid, sid) => activated.push(`s:${bid}:${sid}`),
  closeTab: id => closed.push(id),
  modalConfirm: (title, text, options) => {
    confirms.push({title, text, options});
    return Promise.resolve(confirmAnswer);
  },
  wireMetadataScrolling: () => () => {},
  wireCopyButton: button => button,
  openingConsole: () => null, popoutOwns: () => false,
  toolStateInto() {}, toolInterruptedInto() {}, backgroundTaskStateInto() {},
  questionMarkChosen() {}, toolImageItems: () => [], toolImagesInto() {},
  baseName: value => String(value).split("/").pop(),
  displayValue: value => typeof value === "string" ? value : JSON.stringify(value, null, 2),
  linkifyInto: (node, text) => { node.textContent = text; return node; },
  sharedFilesEnabledFor: () => false,
  $: id => document.body.querySelector("#" + id),
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function apiPath(", "async function api("),
  between("function backendHasCapability(", "function backendSupportsShutdownNotice("),
  between("function previewIcon(", "function searchIcon("),
  between("function storedTabs(", "function loadTabs("),
  "const previewActivityEvents = new Map();",
  between("/* ================= previews =================", "/* ================= console navigation"),
  between("const MD_URI_PATTERN", "function md(text)"),
  between("function decorateMarkdownLinks(", "/* The same problem one step worse"),
  between("const SHARED_FILE_LINK", "const SHARED_FILE_MENTION"),
  between("/* ================= links to previews", "const PLAIN_CODE_LANGS"),
  between("function fillToolResultInto(", "/* ================= tool images"),
  between("const keptViewSlots = [];", "function renderWorkspaceNode("),
  between("class PreviewView {", "class VncView {"),
  "globalThis.previewSessions = previewSessions; globalThis.MD_URI_PATTERN = MD_URI_PATTERN;",
  "globalThis.PreviewView = PreviewView; globalThis.PREVIEW_SANDBOX = PREVIEW_SANDBOX;",
  "globalThis.previewActivityEvents = previewActivityEvents;",
].join("\n"), context);

const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };
const press = (node, extra = {}) => {
  const event = new FakeEvent("click", {bubbles: true});
  Object.assign(event, extra);
  node.dispatchEvent(event);
  return event;
};
const bubble = html => {
  const box = document.createElement("div");
  box.className = "md";
  box.innerHTML = html;
  document.body.appendChild(box);
  return box;
};
const reset = () => {
  context.previewSessions.clear();
  context.previewActivityEvents.clear();
  for (const list of [toasts, reads, opened, activated, confirms, closed]) list.length = 0;
  state.tabs.length = 0;
  for (const key of Object.keys(state.views)) delete state.views[key];
};
const PREVIEWS = [{id: "K3QZ", name: "Plate viewer", token: "T".repeat(32), entry: "",
  folder: "/work/plates/viewer", where: "viewer/"},
{id: "M7XA", name: "Wiring", token: "W".repeat(32), entry: "a b/page.html",
  folder: "/work/plates/wiring", where: "wiring/a b/page.html"}];

(async () => {
  /* ---- the tab layout keeps a preview by its ID and session ---- */
  const stored = context.storedTabs([
    {id: "p:0:K3QZ", type: "preview", bid: 0, sid: 5, previewId: "K3QZ", title: "Plate viewer"},
    {id: "p:0:BAD", type: "preview", bid: 0, sid: 5, previewId: "bad1"},
    {id: "p:0:NOSID", type: "preview", bid: 0, previewId: "ABCD"},
    {id: "p:0:LOW", type: "preview", bid: 0, sid: 5, previewId: "abcd"},
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(stored.map(tab => [tab.id, tab.previewId, tab.sid, tab.title]))),
    [["p:0:K3QZ", "K3QZ", 5, "Plate viewer"]]);
  assert.equal(context.previewUrl(3, "T".repeat(32), "a b/c#.html"),
    "/preview/3/" + "T".repeat(32) + "/a%20b/c%23.html");
  assert.equal(context.previewTabTitle({bid: 0, previewId: "K3QZ", title: ""}), "Preview K3QZ @ desk");
  assert.equal(context.previewTabTitle({bid: 0, previewId: "K3QZ", title: "Plate viewer"}),
    "Plate viewer @ desk");
  console.log("PASS: a preview tab is kept by its four-character ID and its session, " +
    "named for the preview, at an address whose path is encoded name by name");

  /* ---- the node's announcements, each once ---- */
  reset();
  state.tabs.push({id: "s:0:5", type: "session", bid: 0, sid: 5});
  context.handlePreviewActivity(0, 5, "k3qz", "e1", false, "Plate viewer");
  assert.deepEqual(opened, [{id: "p:0:K3QZ", after: "s:0:5", group: "pane-1"}],
    "beside its chat, in its chat's pane");
  assert.equal(state.tabs.find(item => item.id === "p:0:K3QZ").title, "Plate viewer",
    "named before it is first shown");
  assert.deepEqual(activated, [], "without taking focus");
  const tab = state.tabs.find(item => item.id === "p:0:K3QZ");
  assert.equal(tab.type, "preview");
  assert.equal(tab.sid, 5);
  context.handlePreviewActivity(0, 5, "K3QZ", "e1");
  assert.equal(opened.length, 1, "the same announcement over the other socket is heard once");
  const calls = [];
  state.views[tab.id] = {reload: () => calls.push("reload"), ended: () => calls.push("ended")};
  context.handlePreviewActivity(0, 5, "K3QZ", "e2");
  context.handlePreviewActivity(0, 5, "K3QZ", "e3", true);
  assert.deepEqual(calls, ["reload", "ended"], "an open tab reloads, and ends when it is closed");
  assert.equal(opened.length, 1);
  context.handlePreviewActivity(0, 6, "M7XA", "e4");
  assert.deepEqual(opened[1], {id: "p:0:M7XA", after: "s:0:5", group: "pane-1"},
    "a task's preview opens beside its Main");
  context.handlePreviewActivity(0, 5, "ZZZZ", "e5", true);
  assert.equal(opened.length, 2, "a close for a preview with no tab opens nothing");
  context.handlePreviewActivity(0, 5, "TOOLONG", "e6");
  assert.equal(opened.length, 2);
  console.log("PASS: a shown preview opens beside its chat without focus, a reshown one reloads, " +
    "a closed one ends, each heard once");

  /* ---- links in an answer ---- */
  reset();
  state.tabs.push({id: "s:0:5", type: "session", bid: 0, sid: 5});
  let box = bubble('<p>The viewer is <a href="puppy-preview:K3QZ">here</a> and ' +
    '<a href="puppy-preview:GONE">there</a>.</p>');
  context.decorateMarkdownLinks(box, {bid: 0, sid: 5});
  const [link, gone] = box.querySelectorAll("a");
  assert.ok(link.classList.contains("md-preview") && link.classList.contains("md-file"));
  assert.equal(link.getAttribute("href"), null, "no address until the read gives one");
  await settle();
  assert.equal(reads.length, 1, "every link of one pass joins one read");
  assert.equal(reads[0].route, "sessions/5/previews");
  reads[0].resolve({previews: PREVIEWS});
  await settle();
  assert.equal(link.getAttribute("href"), "/preview/0/" + "T".repeat(32) + "/");
  assert.equal(link.title, "Open Plate viewer · Preview K3QZ");
  assert.equal(link.getAttribute("target"), "_blank");
  assert.ok(gone.classList.contains("unavailable"));
  assert.equal(gone.getAttribute("href"), null);
  let event = press(link);
  assert.ok(event.defaultPrevented, "a plain press opens the tab, not the page");
  assert.deepEqual(activated, ["p:0:K3QZ"]);
  assert.equal(state.tabs.find(item => item.id === "p:0:K3QZ").title, "Plate viewer");
  event = press(link, {ctrlKey: true});
  assert.ok(!event.defaultPrevented, "a modified press is the browser's own, on the address");
  press(gone);
  assert.deepEqual(toasts.pop(), ["Could not open the preview · it is no longer open", "warn"]);
  /* without a session, or from a backend without previews, it is words */
  box = bubble('<a href="puppy-preview:K3QZ">words</a>');
  context.decorateMarkdownLinks(box, null);
  assert.equal(box.querySelectorAll("a").length, 0);
  assert.equal(box.textContent, "words");
  state.backends.push({id: 2, name: "nas", capabilities: []});
  box = bubble('<a href="puppy-preview:K3QZ">old node</a>');
  context.decorateMarkdownLinks(box, {bid: 2, sid: 5});
  assert.equal(box.querySelectorAll("a").length, 0);
  state.backends[0].capabilities = ["session-previews"];
  box = bubble('<a href="puppy-preview:K3QZ">remote</a>');
  context.decorateMarkdownLinks(box, {bid: 2, sid: 5});
  await settle();
  assert.equal(reads[reads.length - 1].bid, 2);
  reads[reads.length - 1].resolve({previews: PREVIEWS});
  await settle();
  assert.equal(box.querySelector("a").getAttribute("href"), "/preview/2/" + "T".repeat(32) + "/");
  state.remoteOk[2] = false;
  press(box.querySelector("a"));
  assert.deepEqual(toasts.pop(), ["nas: Could not open the preview · the backend is unavailable", "bad"]);
  state.remoteOk[2] = true;
  /* an announcement reads the links again: a closed preview's link says so */
  const before = reads.length;
  context.handlePreviewActivity(0, 5, "K3QZ", "e9", true);
  await settle();
  assert.equal(reads.length, before + 1);
  reads[reads.length - 1].resolve({previews: [PREVIEWS[1]]});
  await settle();
  assert.ok(link.classList.contains("unavailable"));
  console.log("PASS: a puppy-preview link opens its tab, is its page's address for a modified " +
    "press, says so once the preview is closed, and is words without a session or a capable node");

  /* ---- show_preview's card ---- */
  reset();
  const card = document.createElement("div");
  card.innerHTML = '<div class="tool-head"><span class="t-state"></span></div><div class="tool-body"></div>';
  card._toolName = "mcp__puppy_preview__show_preview";
  context.fillToolResultInto(card, {content: "Showing Plate viewer as Preview K3QZ in a tab beside " +
    "this chat.\nMarkdown link: [Plate viewer](puppy-preview:K3QZ)\nPut that link in your answer."},
    Date.now(), {bid: 0, sid: 5}, 12);
  const head = card.querySelector(".t-preview");
  assert.ok(head, "the head offers the preview, folded or not");
  assert.equal(head.getAttribute("aria-label"), "Open Plate viewer");
  assert.equal(card.querySelectorAll(".preview-chip").length, 1);
  await settle();
  reads[0].resolve({previews: PREVIEWS});
  await settle();
  assert.equal(card.querySelector(".preview-chip .attach-file-name").textContent, "Plate viewer");
  const other = document.createElement("div");
  other.innerHTML = '<div class="tool-head"><span class="t-state"></span></div><div class="tool-body"></div>';
  other._toolName = "Bash";
  context.fillToolResultInto(other, {content: "[x](puppy-preview:K3QZ)"}, Date.now(),
    {bid: 0, sid: 5}, 13);
  assert.equal(other.querySelector(".t-preview"), null, "only show_preview's own card");
  console.log("PASS: show_preview's card offers the preview in its head and under its result");

  /* ---- the pane ---- */
  reset();
  const paneTab = {id: "p:0:M7XA", type: "preview", bid: 0, sid: 5, previewId: "M7XA", title: ""};
  state.tabs.push(paneTab);
  const view = new context.PreviewView(paneTab);
  document.body.appendChild(view.root);
  assert.equal(view.keepAlive, true);
  assert.equal(view.root.querySelector(".br-id").textContent, "M7XA");
  assert.equal(view.root.querySelector(".pv-open").getAttribute("aria-disabled"), "true");
  view.onShow();
  assert.equal(reads[0].route, "sessions/5/previews");
  assert.equal(view.root.querySelector(".pv-state-text").textContent, "Loading…");
  reads[0].resolve({previews: PREVIEWS});
  await settle();
  const frame = view.root.querySelector(".pv-frame");
  assert.ok(frame, "the page is framed");
  assert.equal(frame.getAttribute("sandbox"), context.PREVIEW_SANDBOX);
  assert.ok(!/allow-same-origin/.test(context.PREVIEW_SANDBOX), "never the console's own origin");
  assert.equal(frame.getAttribute("referrerpolicy"), "no-referrer");
  assert.equal(frame.src, "/preview/0/" + "W".repeat(32) + "/a%20b/page.html");
  assert.equal(paneTab.title, "Wiring", "the tab takes the preview's name");
  assert.equal(view.root.querySelector(".pv-where").textContent,
    "/work/plates/wiring/a b/page.html", "the folder in full, as an address bar names its page");
  assert.equal(view.root.querySelector(".pv-open").getAttribute("href"), frame.src);
  assert.equal(view.root.querySelector(".br-owner-text").textContent, "Plates");
  frame.dispatchEvent(new FakeEvent("load"));
  assert.equal(view.root.querySelector(".pv-state-text").textContent, "Live");
  /* a reload reads the preview again and makes a new frame */
  view.reload();
  reads[1].resolve({previews: PREVIEWS});
  await settle();
  assert.notEqual(view.root.querySelector(".pv-frame"), frame, "a fresh frame adds no history step");
  assert.equal(view.root.querySelectorAll(".pv-frame").length, 1);
  /* Stop asks first, then ends the preview on its node */
  confirmAnswer = false;
  view.root.querySelector(".pv-stop").onclick();
  await settle();
  assert.equal(confirms.length, 1);
  assert.equal(confirms[0].title, "Stop Preview M7XA?");
  assert.equal(confirms[0].options.destructive, true);
  assert.equal(reads.length, 2, "a cancelled confirm sends nothing");
  confirmAnswer = true;
  view.root.querySelector(".pv-stop").onclick();
  await settle();
  assert.equal(reads[2].route, "sessions/5/previews/M7XA");
  assert.equal(reads[2].options.method, "DELETE");
  reads[2].resolve({ok: true, id: "M7XA"});
  await settle();
  assert.equal(view.root.querySelector(".pv-frame"), null);
  assert.equal(view.root.querySelector(".pv-dead .term-dead-message").textContent, "Preview ended");
  assert.equal(view.root.querySelector(".pv-state-text").textContent, "Ended");
  assert.ok(view.root.querySelector(".pv-stop").disabled);
  assert.equal(view.root.querySelector(".pv-open").getAttribute("aria-disabled"), "true");
  assert.deepEqual(toasts.pop(), ["Preview M7XA: Stopped", "ok"]);
  view.root.querySelector(".pv-dead .btn").onclick();
  assert.deepEqual(closed, [paneTab.id], "Close is the ended pane's way out");
  /* a preview the node no longer has, and a backend it cannot reach */
  const lost = new context.PreviewView({id: "p:0:ABCD", type: "preview", bid: 0, sid: 5,
    previewId: "ABCD"});
  lost.onShow();
  reads[reads.length - 1].resolve({previews: PREVIEWS});
  await settle();
  assert.equal(lost.root.querySelector(".pv-dead .term-dead-message").textContent, "Preview ended");
  state.remoteOk[2] = false;
  const away = new context.PreviewView({id: "p:2:K3QZ", type: "preview", bid: 2, sid: 5,
    previewId: "K3QZ"});
  const count = reads.length;
  away.onShow();
  assert.equal(reads.length, count, "an unreachable backend is not asked");
  assert.equal(away.root.querySelector(".pv-dead .term-dead-message").textContent,
    "Backend unavailable");
  assert.deepEqual([...away.root.querySelectorAll(".pv-dead .btn")].map(node => node.textContent),
    ["Close", "Retry"]);
  view.destroy();
  console.log("PASS: the pane frames its preview sandboxed at its own address, reloads it in a " +
    "fresh frame, stops it behind a confirm, and says when it ended or cannot be reached");

  /* ---- kept alive where the browser can move a node ---- */
  assert.equal(context.movesAlive(), false, "the fake page has no moveBefore");
  const moves = [];
  context.Element = FakeElement;
  FakeElement.prototype.moveBefore = function (node, reference) {
    moves.push(node);
    this.insertBefore(node, reference);
  };
  assert.equal(context.movesAlive(), true);
  const parking = context.viewParking();
  assert.ok(parking.isConnected && parking.hidden);
  const kept = document.createElement("div");
  parking.moveBefore(kept, null);
  const host = document.createElement("div");
  document.body.appendChild(host);
  const slot = document.createElement("div");
  host.appendChild(slot);
  vm.runInContext("keptViewSlots", context).push([slot, kept]);
  context.placeKeptViews();
  assert.equal(kept.parentNode, host);
  assert.equal(slot.parentNode, null);
  assert.deepEqual(moves, [kept, kept], "moved, never detached");
  delete FakeElement.prototype.moveBefore;
  console.log("PASS: a kept-alive view waits in the parking place and moves to its new place");
})().catch(error => { console.error(error); process.exitCode = 1; });
