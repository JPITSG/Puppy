/* Run with node tests/running_list_ui_test.js. What a backend already runs,
   against the fake DOM: each browser, terminal and VNC connection as its row
   says it (name, state word, who started it, the session it serves, what it
   runs or reaches, when), the ones without a tab here first and newest first;
   the list in New browser, New terminal and New VNC connection - out of sight
   while the backend holds nothing, following the backend picker, one fresh
   read of the node over the catalog the console keeps unless a live stream
   keeps it, a late answer for another backend dropped, a failed read keeping
   what was shown, a press closing the dialog and showing that instance in a
   tab, the catalog followed while the dialog is open and rows that still say
   the same left in place; New browser opening
   a browser at once only when its one backend has nothing to show; and the
   chat head's chips for a browser or terminal the node binds to the session
   without a tab here, a press opening it beside the chat. No browser, engine,
   network or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
};

const document = new FakeDocument();
const opened = [], requests = [], routes = {};
let dialog = null, stream = false;
const icon = name => () => { const n = document.createElement("svg"); n.setAttribute("data-icon", name); return n; };
const context = vm.createContext({
  document, console, Number, String, JSON, Promise, Map, Set, Array, Object, RegExp, Error,
  state: {
    backends: [{ id: 7, name: "Workshop", capabilities: ["terminal", "vnc-instances"] }],
    tabs: [], browserStatus: null, remoteBrowserStatus: {}, terminalInstances: {}, vncInstances: {},
  },
  el: (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  },
  esc: value => String(value),
  backendName: bid => (bid ? "Workshop" : "Studio"),
  backendHasCapability: (backend, cap) => !!backend && backend.capabilities.includes(cap),
  browserEnabledFor: bid => bid === 0 || context.state.browserBackends === "both",
  vncEnabledFor: () => true,
  findSessionMeta: (bid, sid) => (sid === 4 ? { id: 4, name: "Harbor dashboard" } :
    sid === 9 ? { id: 9, name: "Garden planner" } : null),
  fmtStamp: ts => `stamp ${ts}`,
  terminalCliLabel: tab => (tab.engine === "claude" ? "Claude Code" : ""),
  vncInstancesFor: bid => (Array.isArray(context.state.vncInstances[bid]) ? context.state.vncInstances[bid] : null),
  nodeStateListeners: new Set(),
  POPOUT: null,
  nodeStateStreamActive: () => stream,
  globeIcon: icon("globe"), terminalIcon: icon("terminal"), vncIcon: icon("vnc"),
  openBrowserTab: (...args) => opened.push(["browser", ...args]),
  openTermTab: (...args) => opened.push(["terminal", ...args]),
  openVncTab: (...args) => opened.push(["vnc", ...args]),
  openNewBrowser: (...args) => opened.push(["new-browser", ...args]),
  openSettingsTab() {}, toast() {}, TOAST_LONG: 1, newDraftClientId: () => "req",
  workspacePaneForTab: id => (id === "s:0:4" ? { id: "pane-left" } : null),
  backendConnectionAllowed: () => true,
  browserInstancesFor: () => true,
  terminalInstancesFor: () => true,
  applyBrowserStatusSnapshot: (bid, payload) => {
    if (payload && Array.isArray(payload.instances)) {
      if (bid) context.state.remoteBrowserStatus[bid] = payload; else context.state.browserStatus = payload;
    }
  },
  applyTerminalInstancesSnapshot: (bid, payload) => {
    if (payload && Array.isArray(payload.instances)) context.state.terminalInstances[bid] = payload.instances;
  },
  applyVncInstancesSnapshot: (bid, payload) => {
    if (payload && Array.isArray(payload.instances)) context.state.vncInstances[bid] = payload.instances;
  },
  modal: (html, cls, reopen) => {
    const m = document.createElement("div");
    m.innerHTML = html;
    document.body.appendChild(m);
    const listeners = [];
    const close = () => { if (!m.isConnected) return; m.remove(); listeners.forEach(fn => fn()); };
    dialog = { m, close, reopen };
    return { m, close, onClose: fn => listeners.push(fn) };
  },
  api: (bid, route, options) => {
    requests.push([bid, route]);
    const answer = routes[`${bid}:${route}`];
    if (typeof answer === "function") return answer();
    if (answer instanceof Error) return Promise.reject(answer);
    return Promise.resolve(JSON.parse(JSON.stringify(answer || { instances: [] })));
  },
});
vm.runInContext([
  between("/* ---- what a backend already runs ---- */", '/* the "@VNC" shortcut wizard */'),
  "globalThis.running = { RUNNING_KINDS, runningEntry, runningEntries, runningHidden, runningList, " +
    "refreshRunningCatalogs, " +
    "modalNewTerminal, openBrowserFromMenu, modalNewVnc };",
].join("\n"), context);
const api = vm.runInContext("globalThis.running", context);
const settle = () => new Promise(resolve => setImmediate(resolve));
// what the last press opened, as plain data (the vm's objects are of another realm)
const last = () => JSON.parse(JSON.stringify(opened.pop()));
const rows = () => [...dialog.m.querySelectorAll(".running-item")];
const rowText = node => [node.querySelector(".si-name").textContent, node.querySelector(".ri-state").textContent,
  node.querySelector(".ri-sub").textContent];
const box = () => dialog.m.querySelector(".running-box");

const browserItems = [
  { id: "A8AR", origin: "agent", running: true, viewers: 0, session_id: 4, created_at: 300 },
  { id: "K2QX", origin: "user", running: false, viewers: 0, session_id: null, created_at: 200 },
  { id: "Z9ZZ", origin: "agent", running: true, viewers: 1, session_id: 9, created_at: 100 },
  { id: "bad!", origin: "agent", running: true, viewers: 0, created_at: 999 },
];

(async () => {
  /* ---- one instance as its row says it ---- */
  {
    const entry = (kind, item, bid = 0) => {
      const e = api.runningEntry(kind, bid, item);
      return [e.name, e.word, e.parts.map(p => (p.mono ? `<${p.text}>` : p.text)).join(" · ")];
    };
    assert.deepEqual(entry("browser", browserItems[0]),
      ["Browser A8AR", "Running", "Started by an agent · Harbor dashboard · stamp 300"]);
    assert.deepEqual(entry("browser", browserItems[1]),
      ["Browser K2QX", "Idle", "Started from a console · stamp 200"], "a stopped browser is idle until shown");
    assert.deepEqual(entry("browser", browserItems[2]).slice(0, 2), ["Browser Z9ZZ", "Open elsewhere"]);
    context.state.tabs = [{ id: "b:0:A8AR", type: "browser", bid: 0, browserId: "A8AR" }];
    assert.equal(api.runningEntry("browser", 0, browserItems[0]).word, "In a tab here");
    assert.deepEqual(entry("terminal", { id: "T1T1", origin: "agent", running: true, viewers: 0,
      session_id: 4, command: "npm run dev", cwd: "/home/mira/harbor", created_at: 5 }),
      ["Terminal T1T1", "Running", "<npm run dev> · Harbor dashboard · </home/mira/harbor> · Started by an agent · stamp 5"]);
    assert.deepEqual(entry("terminal", { id: "T2T2", origin: "user", running: false, viewers: 0,
      command: "", cwd: "/srv", created_at: 6 }), ["Terminal T2T2", "Ended", "Shell · </srv> · Started from a console · stamp 6"]);
    assert.deepEqual(entry("terminal", { id: "C1C1", origin: "user", running: true, viewers: 2,
      engine: "claude", command: "/usr/local/bin/claude", cwd: "/tmp/cli", created_at: 7 }).slice(0, 2),
      ["Claude Code C1C1", "Open elsewhere"], "an engine's CLI is named for it, never by its binary");
    assert.deepEqual(entry("vnc", { id: "V1V1", host: "10.0.0.5", port: 5901, label: "Lab desk",
      origin: "agent", connected: true, viewers: 0, view_only: true, created_at: 8 }),
      ["Lab desk", "Connected", "<VNC V1V1> · <10.0.0.5:5901> · View only · Started by an agent · stamp 8"]);
    assert.deepEqual(entry("vnc", { id: "V3V3", host: "10.0.0.9", port: 5900, label: "", session_id: 4,
      origin: "agent", connected: true, viewers: 0, created_at: 9 })[2],
      "<VNC V3V3> · Harbor dashboard · Started by an agent · stamp 9", "a screen names the session whose agent chose it");
    assert.deepEqual(entry("vnc", { id: "V2V2", host: "screen.example", port: 5900, label: "",
      origin: "user", connected: false, error: "Refused", viewers: 0, created_at: 9 }).slice(0, 2),
      ["screen.example", "Disconnected"]);
    const order = api.runningEntries("browser", 0, browserItems).map(r => r.entry.id);
    assert.deepEqual(order, ["K2QX", "Z9ZZ", "A8AR"], "the ones without a tab here first, newest first, bad ids dropped");
    context.state.browserStatus = { enabled: true, instances: [browserItems[0]] };
    assert.equal(api.runningHidden("browser", 0), false, "a browser in a tab here is not hidden");
    context.state.browserStatus = { enabled: true, instances: browserItems };
    assert.equal(api.runningHidden("browser", 0), true);
    context.state.tabs = [];
  }
  console.log("PASS: each browser, terminal and VNC connection as its row says it, the hidden ones first and newest first");

  /* ---- New browser: the list, a press, the backend picker ---- */
  {
    // one backend with a browser this console has no tab for: the dialog, not a new browser at once.
    // No stream keeps the catalog, so the node is asked before the choice is made
    requests.length = 0;
    context.state.browserStatus = { enabled: true, instances: [] };
    routes["0:browser/status"] = { enabled: true, instances: browserItems };
    routes["0:terminal/instances"] = { instances: [] };
    await api.openBrowserFromMenu("pane-right");
    assert.ok(dialog && dialog.m.isConnected, "something to show: the dialog opens");
    assert.equal(opened.length, 0);
    assert.ok(!box().classList.contains("hidden"));
    assert.equal(dialog.m.querySelector(".running-head").textContent, "Browsers already on Studio");
    await settle();
    assert.deepEqual(JSON.parse(JSON.stringify(requests)),
      [[0, "browser/status"], [0, "terminal/instances"], [0, "browser/status"]],
      "the catalogs read for the choice, then one fresh read for the list");
    assert.deepEqual(rows().map(rowText), [
      ["Browser A8AR", "Running", "Started by an agent · Harbor dashboard · stamp 300"],
      ["Browser K2QX", "Idle", "Started from a console · stamp 200"],
      ["Browser Z9ZZ", "Open elsewhere", "Started by an agent · Garden planner · stamp 100"]]);
    const form = dialog.m.querySelector("#nb-form");
    const kids = [...form.children].map(n => n.className || n.tagName);
    assert.ok(kids.indexOf("running-box") < kids.indexOf("m-btns"), "the list stands above the buttons");
    assert.equal(rows()[2].getAttribute("aria-label"),
      "Show Browser Z9ZZ · Open elsewhere · Started by an agent · Garden planner · stamp 100");
    rows()[0].onclick();
    assert.ok(!dialog.m.isConnected, "a press closes the dialog");
    assert.deepEqual(last(), ["browser", 0, "A8AR", "pane-right", { sid: 4 }],
      "and shows that browser in a tab, bound to the session it serves");
    assert.equal(context.nodeStateListeners.size, 0, "the closed list stops following the catalog");
    // nothing hidden on the one backend: a new browser at once, as before
    // (a live stream keeps the catalog, so nothing is asked first)
    stream = true;
    context.state.browserStatus = { enabled: true, instances: [] };
    dialog = null;
    requests.length = 0;
    await api.openBrowserFromMenu("pane-right");
    stream = false;
    assert.equal(dialog, null);
    assert.equal(requests.length, 0);
    assert.deepEqual(last(), ["new-browser", 0, "pane-right"]);
    // a reopen always asks, and an empty backend keeps the list out of sight
    routes["0:browser/status"] = { enabled: true, instances: [] };
    await api.openBrowserFromMenu(null, true);
    await settle();
    assert.ok(box().classList.contains("hidden") && rows().length === 0);
    dialog.m.querySelector("#nb-form").onsubmit(new FakeEvent("submit"));
    assert.deepEqual(last(), ["new-browser", 0, null], "Open browser still opens a new one");
  }
  {
    // the catalog read: one per backend at a time, nothing with a live stream
    requests.length = 0;
    context.state.browserBackends = "both";   // the browser is on on that backend too
    let release;
    routes["7:browser/status"] = () => new Promise(resolve => { release = resolve; });
    routes["7:terminal/instances"] = { instances: [{ id: "WRK1", running: true, session_id: 4 }] };
    routes["7:vnc/instances"] = { instances: [{ id: "VRK1", host: "10.0.0.5", port: 5900, session_id: 4 }] };
    context.state.vncInstances[7] = [];   // a catalog this console has read before
    const first = api.refreshRunningCatalogs(7), again = api.refreshRunningCatalogs(7);
    assert.equal(first, again, "a second read for the same backend joins the first");
    release({ enabled: true, instances: [{ id: "RB01", running: true }] });
    await first;
    assert.deepEqual(JSON.parse(JSON.stringify(requests)),
      [[7, "browser/status"], [7, "terminal/instances"], [7, "vnc/instances"]]);
    assert.equal(context.state.remoteBrowserStatus[7].instances[0].id, "RB01");
    assert.equal(context.state.terminalInstances[7][0].id, "WRK1");
    assert.equal(context.state.vncInstances[7][0].id, "VRK1", "the screens too, for the chat head's chips");
    stream = true;
    requests.length = 0;
    await api.refreshRunningCatalogs(7);
    assert.equal(requests.length, 0, "a live stream keeps the catalog itself");
    stream = false;
    context.state.browserBackends = "";
    delete context.state.vncInstances[7];
  }
  console.log("PASS: New browser lists the backend's browsers, a press shows one in a tab, and opens a new one at " +
              "once only when there is nothing to show; without a stream the catalogs are read, one read per backend");

  /* ---- New terminal: the picker, the fresh read, the stream, a late answer ---- */
  {
    requests.length = 0;
    stream = false;
    context.state.terminalInstances = { 0: [], 7: [{ id: "OLD1", origin: "user", running: true, viewers: 0,
      command: "", cwd: "/old", created_at: 1 }] };
    routes["0:terminal/instances"] = { instances: [] };
    let release;
    routes["7:terminal/instances"] = () => new Promise(resolve => { release = resolve; });
    api.modalNewTerminal("pane-left");
    await settle();
    assert.ok(box().classList.contains("hidden"), "the local node runs none");
    const picker = dialog.m.querySelector("#nt-be");
    picker.value = "7";
    picker.dispatchEvent(new FakeEvent("change"));
    assert.deepEqual(rows().map(rowText)[0].slice(0, 2), ["Terminal OLD1", "Running"],
      "the catalog this console keeps paints at once");
    assert.equal(dialog.m.querySelector(".running-head").textContent, "Terminals already on Workshop");
    // the backend changes back before the slow answer lands: that answer is dropped
    picker.value = "0";
    picker.dispatchEvent(new FakeEvent("change"));
    release({ instances: [{ id: "LATE", origin: "agent", running: true, viewers: 0, created_at: 2 }] });
    await settle();
    assert.equal(rows().length, 0, "a late answer for another backend is dropped");
    // the fresh read wins over a stale catalog while no stream keeps it
    routes["7:terminal/instances"] = { instances: [{ id: "NEW1", origin: "agent", running: true, viewers: 0,
      session_id: 4, command: "pytest -x", cwd: "/home/mira/harbor", created_at: 3 }] };
    picker.value = "7";
    picker.dispatchEvent(new FakeEvent("change"));
    await settle();
    assert.deepEqual(rows().map(n => n.querySelector(".si-name").textContent), ["Terminal NEW1"]);
    assert.equal(rows()[0].querySelector(".ri-sub .mono-inline").textContent, "pytest -x", "a command reads as code");
    // with a live stream its catalog is as new as anything, and the open list follows it
    stream = true;
    context.state.terminalInstances[7] = [{ id: "LIVE", origin: "user", running: true, viewers: 0, created_at: 4 }];
    for (const listener of context.nodeStateListeners) listener();
    assert.deepEqual(rows().map(n => n.querySelector(".si-name").textContent), ["Terminal LIVE"]);
    const node = rows()[0];
    for (const listener of context.nodeStateListeners) listener();
    assert.equal(rows()[0], node, "rows that say the same stay the same nodes, so no press is swallowed");
    stream = false;
    // a failed read keeps what was shown
    routes["7:terminal/instances"] = new Error("Backend unavailable");
    picker.dispatchEvent(new FakeEvent("change"));
    await settle();
    assert.deepEqual(rows().map(n => n.querySelector(".si-name").textContent), ["Terminal LIVE"]);
    rows()[0].onclick();
    assert.deepEqual(last(), ["terminal", 7, "", "pane-left", "", "LIVE", {}],
      "it attaches by its ID and never runs a command again");
    // an engine's CLI keeps its name and its restart
    context.state.terminalInstances[0] = [{ id: "C1C1", origin: "user", running: true, viewers: 1,
      engine: "claude", command: "/usr/local/bin/claude", cwd: "/tmp/cli", created_at: 5 }];
    stream = true;
    api.modalNewTerminal(null);
    rows()[0].onclick();
    assert.deepEqual(last(), ["terminal", 0, "", null, "", "C1C1", { engine: "claude" }]);
    stream = false;
  }
  console.log("PASS: New terminal follows its backend picker, reads the node once over a stale catalog, " +
              "drops a late answer, keeps what it showed on a failure, follows a live catalog and attaches by ID");

  /* ---- New VNC connection ---- */
  {
    context.state.vncInstances = { 0: [{ id: "V1V1", host: "10.0.0.5", port: 5901, label: "Lab desk",
      origin: "agent", connected: true, viewers: 0, created_at: 8 }] };
    stream = true;
    api.modalNewVnc("pane-left");
    const form = dialog.m.querySelector("#nv-form");
    const kids = [...form.children].map(n => n.className || n.tagName);
    assert.ok(kids.indexOf("running-box") < kids.indexOf("form-error hidden"),
      "the list stands above the form's error line and buttons");
    assert.equal(dialog.m.querySelector(".running-head").textContent, "Connections already on Studio");
    assert.deepEqual(rowText(rows()[0]).slice(0, 2), ["Lab desk", "Connected"]);
    rows()[0].onclick();
    assert.deepEqual(last(), ["vnc", 0, "V1V1", "pane-left", { host: "10.0.0.5", port: 5901, label: "Lab desk" }]);
    stream = false;
  }
  console.log("PASS: New VNC connection lists the connections already open and a press shows one");

  /* ---- the chat head's chips ---- */
  {
    vm.runInContext([
      "class ChipView {",
      between("  syncBrowserChips() {", "  syncWorkspaceChip() {"),
      "} globalThis.ChipView = ChipView;",
    ].join("\n"), context);
    const view = new context.ChipView();
    view.tab = { bid: 0, sid: 4 };
    view.session = { id: 4 };
    view.root = document.createElement("div");
    view.root.innerHTML = '<div class="chat-meta-scroll"><span class="chip cwd"></span><span class="chat-status"></span></div>';
    context.state.tabs = [{ id: "b:0:TAB1", type: "browser", bid: 0, browserId: "TAB1", sid: 4 }];
    context.state.browserStatus = { enabled: true, instances: [
      { id: "TAB1", session_id: 4, running: true }, { id: "HID1", session_id: 4, running: false },
      { id: "OTHR", session_id: 9, running: true }] };
    context.state.terminalInstances = { 0: [
      { id: "TERM", session_id: 4, running: true }, { id: "DONE", session_id: 4, running: false }] };
    view.syncBrowserChips();
    view.syncTerminalChips();
    const chips = () => [...view.root.querySelectorAll(".chip.browser, .chip.terminal")]
      .map(n => n.querySelector(".chip-text").textContent);
    assert.deepEqual(chips(), ["Browser TAB1", "Browser HID1", "Terminal TERM"],
      "the session's own browsers and running terminals, with a tab here or without; never another session's or an ended one");
    const hidden = [...view.root.querySelectorAll(".chip.browser")][1];
    hidden.onclick();
    assert.deepEqual(last(), ["browser", 0, "HID1", "pane-left", { sid: 4, afterTabId: "s:0:4" }],
      "a press opens it beside the chat, in the chat's pane");
    // a task's chips open beside its Main
    view.tab = { bid: 0, sid: 12 };
    view.session = { id: 12, task: { parent: 4 } };
    context.state.terminalInstances = { 0: [{ id: "TSK1", session_id: 12, running: true }] };
    view.terminalChipKey = null;
    view.syncTerminalChips();
    [...view.root.querySelectorAll(".chip.terminal")][0].onclick();
    assert.deepEqual(last(), ["terminal", 0, "", "pane-left", "", "TSK1", { sid: 12, afterTabId: "s:0:4" }]);
    // once the tab exists the catalog adds nothing more
    context.state.tabs.push({ id: "t:1", type: "term", bid: 0, terminalId: "TSK1", sid: 12 });
    view.syncTerminalChips();
    assert.deepEqual([...view.root.querySelectorAll(".chip.terminal")].map(n => n.querySelector(".chip-text").textContent),
      ["Terminal TSK1"], "one chip, the tab's");
    // a session's own window holds no such tab, so it offers no catalog chip
    context.POPOUT = { id: "s:0:4" };
    assert.equal(view.untabbedRunning("browser").length, 0);
    context.POPOUT = null;
    // a remote screen the session's agent chose has its chip too, tab or not
    view.tab = { bid: 0, sid: 4 };
    view.session = { id: 4 };
    context.state.tabs = [{ id: "v:0:VTAB", type: "vnc", bid: 0, vncId: "VTAB", sid: 4 },
      { id: "v:0:GONE", type: "vnc", bid: 0, vncId: "GONE", sid: 4, vncGone: true }];
    context.state.vncInstances = { 0: [
      { id: "VTAB", session_id: 4, host: "10.0.0.5", port: 5900 },
      { id: "VHID", session_id: 4, host: "10.0.0.6", port: 5901, label: "Lab desk" },
      { id: "VOTH", session_id: 9, host: "10.0.0.7", port: 5900 },
      { id: "VNON", session_id: null, host: "10.0.0.8", port: 5900 }] };
    view.syncVncChips();
    const vncChips = () => [...view.root.querySelectorAll(".chip.vnc")].map(n => n.querySelector(".chip-text").textContent);
    assert.deepEqual(vncChips(), ["VNC VTAB", "VNC VHID"],
      "the session's own screens, with a tab here or without; never another session's, an unbound or a closed one");
    assert.equal([...view.root.querySelectorAll(".chip.vnc")][1].getAttribute("aria-label"), "Show VNC VHID");
    [...view.root.querySelectorAll(".chip.vnc")][1].onclick();
    assert.deepEqual(last(), ["vnc", 0, "VHID", "pane-left",
      { sid: 4, host: "10.0.0.6", port: 5901, label: "Lab desk", afterTabId: "s:0:4" }],
      "a press opens it beside the chat, attached to that connection and the session");
    context.state.vncInstances = { 0: [] };
    context.state.tabs = [];
    view.syncVncChips();
    assert.deepEqual(vncChips(), [], "a closed connection takes its chip with it");
  }
  console.log("PASS: the chat head's chips name the browsers, running terminals and remote screens the node binds " +
              "to its session without a tab here, and a press opens one beside the chat (beside Main for a task)");
})().catch(error => { console.error(error); process.exit(1); });
