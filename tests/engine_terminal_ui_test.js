/* Run with node tests/engine_terminal_ui_test.js. No browser, engine, network
   or quota. The footer's engine names as links to the engine's own CLI,
   against the fake DOM: which names become buttons (an installed CLI on a
   reachable backend whose node offers terminals and the route) and which stay
   plain text, the plain face and the name they carry, a press opening one
   terminal tab that asks the node for the engine rather than a command and
   the next press going to it, the tab's title and its ended pane's words,
   what a restored tab keeps, and the rows the footer draws unchanged. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "../puppy/static/app.css"), "utf8");
function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}

const document = new FakeDocument();
const foot = document.createElement("div");
foot.id = "foot-engines";
document.body.appendChild(foot);
const engine = (key, label, extra = {}) =>
  ({ key, label, installed: true, auth: "ok", ...extra });
const state = {
  tabs: [], views: {}, engines: [], engMap: {}, engCache: {}, backends: [],
  remoteOk: {}, remoteStopping: {}, remoteErrors: {}, remoteEngineErrors: {},
  nodeCapabilities: [], sessions: [], remoteSessions: {}, version: "1.0.0",
  activeGroup: null, active: null, browser: { enabled: false }, remoteBrowser: {},
};
const activated = [], placed = [], drawerCloses = [], shellOpens = [];
const icon = () => document.createElement("svg");
const context = vm.createContext({
  document, state, console, Date, JSON, Number, String, Array, Map, Set, Math,
  el(tag, cls = "", text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  },
  $: id => document.getElementById(id),
  backendName: bid => (bid ? `backend ${bid}` : "this instance"),
  backendPoolable: bid => state.remoteOk[bid] === true,
  remoteAvailabilityTitle: () => "available",
  remoteStoppingMessage: () => "",
  sharedWeeklyQuotas: () => new Map(),
  sharedQuotaObservations: new Map(),
  sortNodeGroups: groups => groups,
  wireNodeGroupDropZone() {}, wireNodeGroupDrag() {}, wireDisclosureSurface() {},
  disclosureButton: () => icon(),
  collapsedStatusBackends: new Set(),
  browserEnabledFor: () => false,
  globeIcon: icon, terminalIcon: icon,
  openNewBrowser() {},
  closeDrawer: () => drawerCloses.push(true),
  weeklyQuotaLeft: () => null, quotaTitle: () => "",
  sessionsFor: bid => (bid ? state.remoteSessions[bid] || [] : state.sessions),
  syncPromptSpinnerPhase: node => node,
  PROMPT_SPIN_MS: 1000, QUOTA_LOW_PERCENT: 20,
  termSeq: 1,
  activateTab: id => activated.push(id),
  putTabInPane: (id, groupId) => placed.push([id, groupId]),
  putTabAfter: (id, after, groupId) => placed.push([id, after, groupId]),
  syncTabOrderFromLayout() {}, renderTabs() {}, syncSessionBrowserChips() {},
});
vm.runInContext([
  "let dragNode = null;",
  between("function engineInfo(bid, key) {", "\nfunction engineReady(engine) {"),
  between("function engineReady(engine) {", "\n/* One wording for a backend that is not answering"),
  between("function engineStatusText(engine) {", "\nfunction effortOptionsForModel("),
  between("function backendConnectionAllowed(bid) {", "\nfunction remoteAvailability(bid) {"),
  between("function remoteAvailability(bid) {", "\nfunction remoteAvailabilityTitle(bid) {"),
  between("/* The engine whose own CLI a terminal tab runs", "\nfunction browserTabTitle("),
  between("function storedTabs(value) {", "\nfunction loadTabs() {"),
  between("function backendHasCapability(backend, capability) {", "\nfunction backendSupportsShutdownNotice("),
  between("function nodeHasCapability(bid, capability) {", '\n/* "Enable tasks" sits beside'),
  between("function openTermTab(bid, cmd,", "\n/* The footer's engine names open that engine's own CLI"),
  between("/* The footer's engine names open that engine's own CLI", "\n/* Browser IDs are node-scoped."),
  between("/* Whether an engine row's name opens the engine's own CLI",
          "\n/* ================= host activity ================= */"),
].join("\n"), context);
const run = code => vm.runInContext(code, context);
/* a value from the console's own context, as data (its prototypes are the
   other realm's) */
const plain = value => JSON.parse(JSON.stringify(value));

/* Every engine row, as a person reads it: the text of the name and how it is
   made, the status word after it, and the label a button carries. */
function rows() {
  return [...foot.querySelectorAll(".foot-eng")].map(row => {
    const button = row.querySelector(".foot-eng-open");
    const text = row.children.find(node => node.tag === "#text");
    return {
      name: button ? button.textContent : text ? text.textContent : "",
      link: !!button,
      label: button ? button.getAttribute("aria-label") : null,
      type: button ? `${button.tag}/${button.type}` : null,
      status: row.querySelector(".st-word").textContent,
      order: row.children.map(node => node.tag === "#text" ? "text" : node.className.split(" ")[0]),
    };
  });
}

function bodies() {
  return [...foot.querySelectorAll(".foot-engine-group")].map(group =>
    group.querySelector(".foot-engine-empty") ?
      group.querySelector(".foot-engine-empty").textContent : "rows");
}

function reset() {
  state.tabs = [];
  activated.length = 0;
  placed.length = 0;
  drawerCloses.length = 0;
}

/* Which names become links. */
function linksWhereThereIsACli() {
  state.engines = [engine("claude", "Claude Code"), engine("codex", "Codex", { auth: "bad" }),
                   engine("opencode", "OpenCode", { installed: false, availability_only: true })];
  state.engMap = Object.fromEntries(state.engines.map(item => [item.key, item]));
  state.nodeCapabilities = ["terminal", "terminal-instances", "terminal-engine-cli"];
  const remoteEngines = () => [engine("claude", "Claude Code"), engine("codex", "Codex")];
  state.backends = [
    { id: 7, name: "Workshop", protocol: 2, capabilities: ["terminal", "terminal-instances", "terminal-engine-cli"] },
    { id: 8, name: "Older", protocol: 2, capabilities: ["terminal", "terminal-instances"] },
    { id: 9, name: "Away", protocol: 2, capabilities: ["terminal", "terminal-instances", "terminal-engine-cli"] },
    { id: 10, name: "Shell-less", protocol: 2, capabilities: ["terminal-engine-cli"] },
    { id: 11, name: "Checking", protocol: 2, capabilities: ["terminal", "terminal-instances", "terminal-engine-cli"] },
  ];
  state.engCache = { 7: remoteEngines(), 8: remoteEngines(), 9: remoteEngines(), 10: remoteEngines() };
  state.remoteOk = { 7: true, 8: true, 9: false, 10: true, 11: true };
  run("renderFootEngines()");
  const seen = rows();
  /* this instance: the installed CLIs open, signed in or not - the CLI is
     where one signs in - and a missing binary is the plain text it was */
  assert.deepEqual(seen.slice(0, 3).map(row => [row.name, row.link, row.status]), [
    ["Claude Code", true, "Ready"], ["Codex", true, "No auth"], ["OpenCode", false, "No binary"],
  ]);
  assert.equal(seen[0].label, "Open Claude Code in a terminal on this instance");
  assert.equal(seen[1].label, "Open Codex in a terminal on this instance");
  assert.equal(seen[0].type, "button/button", "a real button, so a keyboard reaches it");
  /* the row is the row it was: dot, name, status, with the name in the
     name's own place */
  assert.deepEqual(seen[0].order, ["foot-ico", "foot-eng-open", "st"]);
  assert.deepEqual(seen[2].order, ["foot-ico", "text", "st"]);
  /* a backend that offers the route; one from before it; one that offers it
     but not a terminal at all; and the unavailable and the still-checking
     ones draw no engine rows in the first place */
  assert.deepEqual(seen.slice(3).map(row => [row.name, row.link]), [
    ["Claude Code", true], ["Codex", true],
    ["Claude Code", false], ["Codex", false],
    ["Claude Code", false], ["Codex", false],
  ]);
  assert.equal(seen[3].label, "Open Claude Code in a terminal on Workshop");
  assert.deepEqual(bodies(), ["rows", "rows", "rows", "Backend unavailable", "rows", "Checking engines…"]);
  /* this instance without the route (a console served by an older node):
     plain names, nothing else different */
  state.nodeCapabilities = ["terminal", "terminal-instances"];
  run("renderFootEngines()");
  assert.deepEqual(rows().slice(0, 3).map(row => [row.name, row.link, row.status]), [
    ["Claude Code", false, "Ready"], ["Codex", false, "No auth"], ["OpenCode", false, "No binary"],
  ]);
  state.nodeCapabilities = ["terminal", "terminal-instances", "terminal-engine-cli"];
}

/* A press opens one terminal tab for the CLI; the next press goes to it. */
function onePressOneTab() {
  reset();
  run("renderFootEngines()");
  const open = foot.querySelector(".foot-eng-open");
  const event = new FakeEvent("click", { bubbles: true });
  open.dispatchEvent(event);
  assert.ok(event.defaultPrevented && event.propagationStopped,
    "the press is the button's alone, not the row's drag or the drawer's");
  assert.equal(state.tabs.length, 1);
  const tab = state.tabs[0];
  assert.equal(tab.type, "term");
  assert.equal(tab.bid, 0);
  assert.equal(tab.engine, "claude");
  assert.equal(tab.cmd, "", "the node chooses the command");
  assert.equal(tab.cwd, "", "and the directory");
  assert.equal(tab.terminalId, "", "a fresh tab has no terminal yet");
  assert.equal(tab.title, "Claude Code @ this instance");
  assert.deepEqual(activated, [tab.id]);
  assert.deepEqual(placed, [[tab.id, null]]);
  assert.equal(drawerCloses.length, 1, "on a phone the drawer closes behind the press");
  /* the request the tab makes of its node names the engine and nothing
     else; a shell tab still carries its own command and directory */
  assert.deepEqual(plain(run("terminalCreateBody(state.tabs[0], 120, 40)")),
    { cols: 120, rows: 40, engine: "claude" });
  assert.deepEqual(plain(run('terminalCreateBody({ cmd: "/bin/zsh", cwd: "/srv" }, 80, 24)')),
    { cols: 80, rows: 24, command: "/bin/zsh", cwd: "/srv" });
  assert.deepEqual(plain(run("terminalCreateBody({}, 80, 24)")),
    { cols: 80, rows: 24, command: "", cwd: "" });
  /* the same name pressed again goes to the tab that is live */
  open.dispatchEvent(new FakeEvent("click", { bubbles: true }));
  assert.equal(state.tabs.length, 1);
  assert.deepEqual(activated, [tab.id, tab.id]);
  /* another engine, or the same engine on another backend, is its own tab */
  foot.querySelectorAll(".foot-eng-open")[1].dispatchEvent(new FakeEvent("click", { bubbles: true }));
  assert.equal(state.tabs.length, 2);
  assert.equal(state.tabs[1].engine, "codex");
  assert.equal(state.tabs[1].title, "Codex @ this instance");
  foot.querySelectorAll(".foot-eng-open")[2].dispatchEvent(new FakeEvent("click", { bubbles: true }));
  assert.equal(state.tabs.length, 3);
  assert.deepEqual([state.tabs[2].bid, state.tabs[2].engine, state.tabs[2].title],
    [7, "claude", "Claude Code @ backend 7"]);
  /* once the CLI has been quit its tab stands ended, and the next press
     opens a fresh one rather than the ended tab; a terminal the node no
     longer lists is treated the same */
  tab.ended = true;
  open.dispatchEvent(new FakeEvent("click", { bubbles: true }));
  assert.equal(state.tabs.length, 4);
  assert.equal(state.tabs[3].engine, "claude");
  assert.equal(activated[activated.length - 1], state.tabs[3].id);
  state.tabs[3].terminalGone = true;
  open.dispatchEvent(new FakeEvent("click", { bubbles: true }));
  assert.equal(state.tabs.length, 5);
  /* the shell opener beside it is untouched: a plain terminal tab */
  const shell = run('openTermTab(0, "", null, "/home/mira")');
  assert.equal(shell.engine, undefined);
  assert.equal(shell.title, "Terminal @ this instance");
  assert.equal(shell.cwd, "/home/mira");
}

/* The name a CLI tab carries, on the tab and in its ended pane. */
function namesOfTheTab() {
  const title = tab => run(`terminalTabTitle(${JSON.stringify(tab)})`);
  assert.equal(title({ type: "term", bid: 0, engine: "claude", terminalId: "A1B2" }),
    "Claude Code A1B2 @ this instance");
  assert.equal(title({ type: "term", bid: 7, engine: "codex", terminalId: "A1B2" }),
    "Codex A1B2 @ backend 7");
  assert.equal(title({ type: "term", bid: 0, terminalId: "A1B2" }), "Terminal A1B2 @ this instance");
  assert.equal(title({ type: "term", bid: 0 }), "Terminal @ this instance");
  /* a restored tab may be read before the engine list; the key stands in
     for the label until it arrives, and a remote label is that node's */
  assert.equal(title({ type: "term", bid: 0, engine: "kimi", terminalId: "A1B2" }),
    "kimi A1B2 @ this instance");
  state.engCache[7] = [engine("claude", "Claude Code (beta)")];
  assert.equal(title({ type: "term", bid: 7, engine: "claude" }), "Claude Code (beta) @ backend 7");
  delete state.engCache[7];
  const words = tab => plain(run(`terminalDeadWording(${JSON.stringify(tab)})`));
  assert.deepEqual(words({ type: "term", bid: 0, engine: "claude" }),
    { ended: "Claude Code ended", start: "Start Claude Code", close: "Close terminal" });
  assert.deepEqual(words({ type: "term", bid: 0 }),
    { ended: "Terminal ended", start: "New shell", close: "Close shell" });
  /* the ended pane reads these words: no shell is ever offered for a CLI */
  const dead = between("  showDead(markEnded = true) {", "  handleNodeStopping() {");
  assert.ok(dead.includes("const words = terminalDeadWording(this.tab);"));
  assert.ok(dead.includes('el("div", "term-dead-message", words.ended)'));
  assert.ok(dead.includes('el("button", "btn btn-pri term-dead-new", words.start)'));
  assert.ok(dead.includes('el("button", "btn term-dead-close", words.close)'));
  assert.ok(!dead.includes('"New shell"') && !dead.includes('"Close shell"'));
  const sync = between("  syncRemoteState() {", "  destroy() {");
  assert.ok(sync.includes("(ended ? words.ended : \"Terminal disconnected\")"));
  assert.ok(sync.includes("(ended ? words.start : \"Reconnect\")"));
  /* and the pane's request goes through the one body builder */
  const create = between("  async createInstance(sequence, ownerSession = null) {", "  async connect() {");
  assert.ok(create.includes("terminalCreateBody(this.tab, this.term.cols, this.term.rows)"));
  assert.ok(!create.includes("command: this.tab.cmd"));
}

/* What a restored tab keeps. */
function restoredTabs() {
  const stored = plain(run(`storedTabs(${JSON.stringify([
    { id: "t:1", type: "term", bid: 0, engine: "claude", terminalId: "a1b2", ended: true },
    { id: "t:2", type: "term", bid: 7, engine: "../etc", terminalId: "C3D4" },
    { id: "t:3", type: "term", bid: 0, engine: 5 },
    { id: "b:0:E5F6", type: "browser", bid: 0, browserId: "E5F6", engine: "claude" },
    { id: "t:4", type: "term", bid: 0, cmd: "/bin/zsh", cwd: "/srv" },
  ])})`));
  assert.deepEqual(stored.map(tab => [tab.id, tab.engine, tab.terminalId, tab.ended, tab.cmd]), [
    ["t:1", "claude", "A1B2", true, undefined],
    ["t:2", undefined, "C3D4", undefined, undefined],
    ["t:3", undefined, undefined, undefined, undefined],
    ["b:0:E5F6", undefined, undefined, undefined, undefined],
    ["t:4", undefined, undefined, undefined, "/bin/zsh"],
  ]);
  const saved = between("function saveTabs() {", "\nfunction storedTabs(value) {");
  assert.ok(saved.includes("cmd: t.cmd, cwd: t.cwd, engine: t.engine, ended: t.ended === true,"));
}

/* The face: the name's own font, colour and line, only the cursor added. */
function plainFace() {
  const start = css.indexOf(".foot-eng-open{");
  const rule = css.slice(start, css.indexOf("}", start));
  for (const declaration of ["margin:0", "padding:0", "border:0", "background:none",
                             "color:inherit", "font:inherit", "letter-spacing:inherit",
                             "cursor:pointer"])
    assert.ok(rule.includes(declaration), declaration);
  assert.ok(!/text-decoration|hover/.test(rule), "no underline, no wash: the cursor is the whole tell");
  assert.ok(!css.includes(".foot-eng-open:hover"), "nothing changes under the pointer but the pointer");
}

linksWhereThereIsACli();
onePressOneTab();
namesOfTheTab();
restoredTabs();
plainFace();
console.log("PASS: footer engine names open the engine's CLI in a terminal - which names " +
  "are links, one press one tab and the next press going to it, the tab's name and " +
  "ended words, restored tabs, and the plain face");
