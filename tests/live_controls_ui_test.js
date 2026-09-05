/* Run with node tests/live_controls_ui_test.js. Live state transitions against
   the real UI methods; no browser, engine, network, or model quota. */
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
const el = (tag, cls = "", text = "") => {
  const node = document.createElement(tag); node.className = cls; node.textContent = text; return node;
};
const state = { views: {}, sessions: [], remoteSessions: {}, engines: [], engMap: {}, engCache: {},
  nodeCapabilities: ["session-tools", "fast-mode", "engine-upgrade"],
  backends: [{ id: 7, name: "Peer", capabilities: ["session-search", "session-tools", "fast-mode", "engine-upgrade"], protocol: 1 }],
  remoteOk: { 7: false }, remoteErrors: {}, remoteBrowser: {}, remoteBrowserStatus: {}, remoteSessionCheckedAt: {},
  remoteEngineErrors: {}, remoteEngineCheckedAt: {}, remoteStopping: {}, remoteUsageRefresh: {}, remoteAutoUpgrade: {}, remoteUploadSettings: {},
  sessionColors: [], defaultCwd: "/project", browser: { enabled: false }, version: "1.0.1" };
const nodeStateListeners = new Set(), enginePayloadListeners = new Set();
let dialog, requests = [], apiReply = {}, hold = null, failure = "", stream = true;
let sidebarUpdates = 0, tabUpdates = 0;
const notices = [];
function modal(html) {
  const m = el("div", "modal"); m.innerHTML = html; document.body.appendChild(m);
  const listeners = [];
  const close = () => { m.remove(); listeners.forEach(listener => listener()); };
  dialog = { m, close, onClose: listener => listeners.push(listener) };
  return dialog;
}
const capability = (bid, key) => !bid ? state.nodeCapabilities.includes(key) :
  state.backends.find(node => node.id === bid)?.capabilities.includes(key) === true;
const backendConnectionAllowed = bid => !bid || state.remoteOk[bid] === true;
const context = vm.createContext({
  console, document, el, state, nodeStateListeners, enginePayloadListeners, modal,
  Composer: { live: new Set() },
  backendSupportsFileUploads: bid => capability(bid, "file-uploads"),
  uploadSettingsFor: () => ({ enabled: true, max_file_size_mb: 10 }),
  requestAnimationFrame: callback => { callback(); return 1; }, setTimeout, clearTimeout,
  closeAllMenus() { document.querySelectorAll(".dyn").forEach(menu => menu.remove()); return false; },
  positionChoiceMenu() {}, positionAnchoredMenu() {}, tips: { text: () => "" },
  choiceOptionNode: (label, selected) => el("button", "choice-option" + (selected ? " selected" : ""), label),
  menuCheckRow: (label, on) => { const row = el("button", "menu-check", label); row.setAttribute("aria-checked", String(on)); return row; },
  sessionsFor: bid => bid ? state.remoteSessions[bid] || [] : state.sessions,
  findSessionMeta: (bid, sid) => (bid ? state.remoteSessions[bid] || [] : state.sessions).find(s => s.id === sid),
  backendName: bid => bid ? "Peer" : "Main", backendConnectionAllowed,
  nodeHasCapability: capability,
  backendSupportsSessionTools: bid => capability(bid, "session-tools"),
  backendSupportsFastMode: bid => capability(bid, "fast-mode"),
  backendSupportsEngineUpgrade: bid => capability(bid, "engine-upgrade"),
  backendSupportsEngineDefaults: () => false,
  backendSupportsQueuedPermission: bid => capability(bid, "queued-permission-config"),
  backendSupportsScratch: bid => !bid || capability(bid, "temporary-workspaces"),
  backendSupportsWorkspaceMirror: bid => !bid || capability(bid, "workspace-mirror"),
  backendSupportsWorkspaceProvider: bid => !bid || capability(bid, "workspace-provider"),
  backendHasCapability: (backend, key) => backend?.capabilities.includes(key) === true,
  nodeSupportsSearch: bid => capability(bid, "session-search"),
  browserHandoffFor: () => true, terminalHandoffFor: () => true, terminalInstancesFor: () => true,
  nodeStateStreamActive: () => stream,
  remoteAvailability: bid => backendConnectionAllowed(bid) ? "ok" : "bad",
  remoteStoppingMessage: () => "",
  browserEnabledFor: bid => bid ? !!state.remoteBrowser[bid]?.enabled : !!state.browser.enabled,
  closeBrowserTabsForBackend() {},
  refreshChoiceSelect() {}, enhanceChoiceSelect() {},
  provSpec: key => ({ className: key, text: key }), sessDot: () => el("span"), choiceSvg: () => el("svg"), refreshIcon: () => el("svg"),
  refreshEngineVersions: bid => requests.push({ bid, route: "engines/refresh" }),
  esc: text => String(text), layoutSwatchRow() {},
  window: { addEventListener() {}, removeEventListener() {} }, wireDirectoryPicker() {},
  renderSidebar: () => { sidebarUpdates++; }, syncTabsWithSessions: () => { tabUpdates++; },
  acceptStateSnapshot: () => true, noteLocalStateStreamTopic() {}, ingestSessionActivity() {},
  toast: (...args) => notices.push(args), TOAST_LONG: 7000, ENGINE_POLL_TIMEOUT: 10000,
  async api(bid, route, options = {}) {
    requests.push({ bid, route, ...options });
    if (hold) await hold;
    if (failure) throw new Error(failure);
    return apiReply;
  },
});
vm.runInContext([
  between("function engineInfo(", "const headWord ="),
  between("function liveViews()", "/* The wrapper that owns"),
  between("function applyNodeStateSnapshot(", "function applyRemoteStreamState("),
  between("function syncRemoteStateViews()", "function remoteStoppingMessage("),
  between("const SESSION_TOOLS =", "function sessionToolLabel("),
  between("class SessionView {", "/* ================= TermView"),
  between("class TermView {", "/* ================= SettingsView"),
  between("class SearchView {", "/* ================= modals"),
  between("function workspaceBackendChoices(", "/* The same editor serves"),
  between("async function modalNewSession(", "/* open existing session */"),
  "globalThis.classes = {SessionView, TermView, BrowserView, SearchView, SettingsView};",
  "globalThis.UploadComposer = class {",
  between("  syncUploadButton() {", "  /* Leave the box."),
  "};",
].join("\n"), context);
const { SessionView, TermView, BrowserView, SearchView, SettingsView } = context.classes;
const update = () => context.syncRemoteStateViews();
const button = (root, label) => root.querySelectorAll("button").find(node => node.textContent === label);
const mount = node => { document.body.appendChild(node); return node; };
function sessionView(bid = 0) {
  const view = Object.create(SessionView.prototype);
  Object.assign(view, { tab: { bid, sid: 1 }, session: { engine: "engine", model: "model" }, root: mount(el("div")) });
  view.effectiveConfig = () => view.session;
  // Exercise the same sync entry used by updateHead and syncRemoteState,
  // without constructing an unrelated transcript or opening its socket.
  view.syncRemoteState = () => view.syncNativeComposerChoices();
  return view;
}
function engineCatalog(fast = false) {
  return [{ key: "engine", label: "Engine", installed: true, auth: "ok", allow_custom_model: false,
    supports_fast_mode: true, tool_options: [{ value: "compact" }],
    permission_options: [{ value: "safe", label: "Safe" }],
    model_options: [{ value: "model", label: "Model", fast_mode_available: fast,
      effort_options: [{ value: "high", label: "High" }] }],
    upgrade_supported: true, update_available: true, latest_version: "2.0.0", version: "1.0.0" }];
}
(async () => {
  state.engines = engineCatalog();
  const view = sessionView();
  state.views.chat = view;
  const anchor = mount(el("button"));
  view.showToolsMenu(anchor);
  const menu = view.composerMenu;
  assert.equal(button(menu, "Undo last turn").disabled, true);
  assert.equal(button(menu, "Fast mode").disabled, true);
  button(menu, "Compact context").focus();
  state.engines[0].tool_options.push({ value: "undo" });
  state.engines[0].model_options[0].fast_mode_available = true;
  update();
  assert.equal(view.composerMenu, menu);
  assert.equal(button(menu, "Undo last turn").disabled, false);
  assert.equal(button(menu, "Fast mode").disabled, false);
  assert.equal(document.activeElement, button(menu, "Compact context"), "live refresh preserves keyboard focus");
  let fast;
  view.setFastMode = value => { fast = value; };
  view.session.fast_mode = true; update();
  assert.equal(button(menu, "Fast mode").getAttribute("aria-checked"), "true");
  button(menu, "Fast mode").click();
  assert.equal(fast, false, "toggle acts on the latest setting");
  view.showModelMenu(anchor);
  const models = view.composerMenu;
  state.engines[0].model_options.push({ value: "new", label: "New model" }); update();
  assert.ok(button(models, "New model"));
  let model;
  view.applyModelChoice = value => { model = value; };
  button(models, "New model").click(); assert.equal(model, "new");
  view.showToolsMenu(anchor);
  state.engines[0].tool_options = []; update();
  assert.equal(button(view.composerMenu, "Compact context").disabled, true, "withdrawn actions disable live");
  view.composerMenu.remove();
  view.tab.bid = 7; state.engCache[7] = state.engines;
  view.session.permission_mode = "safe";
  view.showPermMenu(anchor);
  view.session.queuedEngine = true; update();
  assert.equal(button(view.composerMenu, "Safe").disabled, true);
  state.backends[0].capabilities.push("queued-permission-config"); update();
  assert.equal(button(view.composerMenu, "Safe").disabled, false);
  view.showToolsMenu(anchor);
  const toolMenu = view.composerMenu;
  toolMenu.focus();
  toolMenu.onkeydown(new FakeEvent("keydown", { key: "ArrowDown" }));
  assert.equal(document.activeElement, button(toolMenu, "Fast mode"), "keyboard skips actions disabled by live state");
  view.composerMenu.remove(); delete state.views.chat;

  const composer = new context.UploadComposer();
  composer.host = { bid: 7 }; composer.attachButton = mount(el("button"));
  context.Composer.live.add(composer); // Dialog composers are not leaf SessionViews.
  state.backends[0].capabilities.push("file-uploads");
  state.remoteOk[7] = false; update(); assert.equal(composer.attachButton.disabled, true);
  state.remoteOk[7] = true; update(); assert.equal(composer.attachButton.disabled, false);
  state.remoteOk[7] = false; update(); assert.equal(composer.attachButton.disabled, true);
  context.Composer.live.delete(composer);

  const search = Object.create(SearchView.prototype);
  search.nodesBox = mount(el("div")); search.excludedNodes = new Set([7]);
  state.views.search = search;
  update(); assert.equal(button(search.nodesBox, "Peer").disabled, true);
  state.remoteOk[7] = true; update();
  assert.equal(button(search.nodesBox, "Peer").disabled, false);
  assert.equal(button(search.nodesBox, "Peer").getAttribute("aria-pressed"), "false", "recovery keeps the user's excluded backend");
  const searchPeer = button(search.nodesBox, "Peer"); update();
  assert.equal(button(search.nodesBox, "Peer"), searchPeer, "unrelated state leaves controls in place");
  searchPeer.click(); assert.equal(search.excludedNodes.has(7), false);
  state.remoteOk[7] = false; update(); assert.equal(button(search.nodesBox, "Peer").disabled, true);
  delete state.views.search;

  const settings = new SettingsView({ id: "settings" });
  mount(settings.root); state.views.settings = settings;
  settings.localEngineGroup = settings.engineGroup("Main", "", 0);
  settings.root.appendChild(settings.localEngineGroup.root);
  const activity = status => context.applyNodeStateSnapshot(0, {
    type: "sessions", state_topic: "sessions", sessions: [{ id: 1, engine: "engine", status }],
  });
  activity("running"); assert.equal(button(settings.root, "Busy").disabled, true);
  activity("idle"); assert.equal(button(settings.root, "Update").disabled, false, "local completion releases Update without an engine payload");
  activity("running"); assert.equal(button(settings.root, "Busy").disabled, true);
  assert.ok(sidebarUpdates > 0 && tabUpdates === 3);
  state.backends[0].capabilities = [];
  const group = settings.engineGroup("Peer", "", 7); mount(group.root);
  group.update({ status: "ok", engines: [] });
  assert.equal(group.root.querySelector(".engine-node-refresh"), null);
  state.backends[0].capabilities.push("engine-upgrade"); state.remoteOk[7] = true;
  group.update({ status: "ok", engines: [] });
  group.root.querySelector(".engine-node-refresh").click();
  assert.equal(requests.at(-1).route, "engines/refresh");

  for (const bid of [0, 7]) {
    state.remoteOk[7] = false;
    stream = bid !== 0; failure = bid === 0 ? "Temporary status error" : "";
    const input = mount(el("input")), root = mount(el("div")), note = mount(el("span"));
    const shared = { input: mount(el("input")), root: mount(el("div")) };
    await settings.wireBrowserToggle(bid, input, note, settings.renderGeneration, root, null, shared);
    assert.equal(input.disabled, true);
    assert.equal(shared.input.disabled, true);
    stream = true; failure = ""; state.remoteOk[7] = true;
    const status = { available: true, enabled: false, shared_storage: false, instances: [] };
    if (bid) state.remoteBrowserStatus[bid] = status; else state.browserStatus = status;
    update();
    assert.equal(input.disabled, false); assert.equal(shared.input.disabled, false);
    assert.equal(typeof input.onchange, "function", "stream recovery enables a wired action");
    let release; hold = new Promise(resolve => { release = resolve; });
    apiReply = { ...status, enabled: true };
    input.checked = true;
    const pending = input.onchange(); update();
    assert.equal(input.disabled, true, "live status cannot release a pending toggle");
    assert.equal(input.checked, true, "live status preserves the pending choice");
    const count = requests.length; await input.onchange(); assert.equal(requests.length, count);
    release(); await pending; hold = null;
    assert.equal(input.disabled, false); assert.equal(input.checked, true);
    hold = new Promise(resolve => { release = resolve; });
    apiReply = { ...status, enabled: true, shared_storage: true };
    shared.input.checked = true;
    const sharing = shared.input.onchange(); update();
    assert.equal(shared.input.disabled, true); assert.equal(shared.input.checked, true);
    release(); await sharing; hold = null;
    assert.equal(shared.input.disabled, false); assert.equal(shared.input.checked, true);
    failure = "Toggle refused";
    input.checked = false;
    await input.onchange();
    assert.equal(input.disabled, false); assert.equal(input.checked, true);
    assert.deepEqual(notices.at(-1), [`${bid ? "Peer" : "Main"}: Toggle refused`, "error", context.TOAST_LONG]);
    shared.input.checked = false;
    await shared.input.onchange();
    assert.equal(shared.input.disabled, false); assert.equal(shared.input.checked, true);
    assert.deepEqual(notices.at(-1), [`${bid ? "Peer" : "Main"}: Toggle refused`, "error", context.TOAST_LONG]);
    failure = "";
    if (bid) {
      state.remoteOk[bid] = false; update();
      assert.equal(input.disabled, true, "cached stream status cannot enable an offline backend");
      assert.equal(shared.input.disabled, true);
    }
    settings.remoteBrowserToggles.delete(bid);
  }
  // The notification card's destination selector updates without losing its
  // selected backend when a peer gains the execution capability.
  context.settings = settings;
  context.nfBackend = mount(el("select"));
  vm.runInContext("(function () {\n" +
    between("    let notifyNodeSignature =", "    this.inner.appendChild(notifyCard);") +
    "\n}).call(settings);", context);
  let peer = () => context.nfBackend.options.find(option => option.value === "7");
  assert.equal(peer().disabled, true);
  state.backends[0].capabilities.push("notify-exec"); update();
  assert.equal(peer().disabled, false);
  context.nfBackend.value = "7";
  state.backends[0].name = "Renamed peer"; update();
  assert.equal(context.nfBackend.value, "7"); assert.equal(peer().textContent, "Renamed peer");
  delete state.views.settings;

  for (const View of [TermView, BrowserView]) {
    const linked = Object.create(View.prototype);
    Object.assign(linked, { tab: { bid: 0, terminalId: "ABCD", browserId: "ABCD" }, root: mount(el("div")),
      binding: { sessionId: null }, bindingKnown: true, linkBusy: "",
      meta: el("div"), idText: el("span"), ownerBtn: mount(el("button")), ownerText: el("span"),
      ownerGlyph: el("span"), ownerDot: el("span"), ownerArrow: el("span") });
    state.views.link = linked;
    state.sessions = [];
    linked.renderBinding(); linked.showLinkMenu(linked.ownerBtn);
    const linkMenu = linked.linkMenu;
    assert.equal(button(linkMenu, "No sessions on this backend").disabled, true);
    state.sessions = [{ id: 22, name: "New session", engine: "engine" }];
    update();
    assert.equal(linked.linkMenu, linkMenu);
    assert.equal(button(linkMenu, "New session").disabled, false);
    linked.binding.sessionId = 22; linked.renderBinding();
    assert.ok(button(linkMenu, View === TermView ? "Unlink terminal" : "Unlink browser"));
    let target; linked.changeBinding = id => { target = id; };
    button(linkMenu, "New session").click(); assert.equal(target, 22);
    delete state.views.link;
  }

  state.remoteOk[7] = false;
  state.engines = engineCatalog(); state.engCache[7] = engineCatalog();
  state.backends[0].capabilities = [];
  await context.modalNewSession();
  const form = dialog.m, backend = form.querySelector("#ns-be");
  const remote = form.querySelector('[data-kind="remote"]');
  assert.equal(backend.options.find(option => option.value === "7").disabled, true);
  assert.equal(remote.disabled, true);
  form.querySelector("#ns-name").value = "Keep my draft";
  state.remoteOk[7] = true; state.backends[0].capabilities.push("workspace-provider"); update();
  assert.equal(backend.options.find(option => option.value === "7").disabled, false);
  assert.equal(remote.disabled, false);
  assert.equal(form.querySelector("#ns-name").value, "Keep my draft");
  backend.value = "7"; backend.onchange();
  const scratch = form.querySelector('[data-kind="temporary"]');
  assert.equal(scratch.disabled, true);
  state.backends[0].capabilities.push("temporary-workspaces"); update();
  assert.equal(scratch.disabled, false);
  assert.equal(backend.value, "7");
  dialog.close(); assert.equal(nodeStateListeners.size, 0); assert.equal(enginePayloadListeners.size, 0);
  update();
  console.log("PASS: live composer menus, search recovery, local engine readiness, browser toggles, session links and New session choices");
})().catch(error => { console.error(error); process.exitCode = 1; });
