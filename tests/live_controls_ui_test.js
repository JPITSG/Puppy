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
  wireAutoTitleChoice: () => ({ sync() {}, wanted: () => false }),
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
  refreshChoiceSelect() {}, enhanceChoiceSelect() {}, openChoiceControl: null,
  provSpec: key => ({ className: key, text: key }), sessDot: () => el("span"), choiceSvg: () => el("svg"), refreshIcon: () => el("svg"),
  refreshEngineVersions: bid => requests.push({ bid, route: "engines/refresh" }),
  rememberEnginePayload(bid, result) {
    if (bid) state.engCache[bid] = result.engines; else state.engines = result.engines;
    for (const listener of enginePayloadListeners) listener(Number(bid) || 0, result.engines);
  },
  esc: text => String(text), layoutSwatchRow() {},
  linkifyInto: (node, text) => { node.textContent = text; },
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
  between("/* Under the anchor, or at a point when one was given. */",
          "/* ================= composer @-mentions"),
  between("function liveViews()", "/* The wrapper that owns"),
  between("function applyNodeStateSnapshot(", "function applyRemoteStreamState("),
  between("function syncRemoteStateViews()", "function remoteStoppingMessage("),
  between("const SESSION_TOOLS =", "function sessionToolLabel("),
  between("/* ================= questions the engine asks", "function toolCardNode("),
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
/* The prompt box owns the tools menu and appends the spelling switches to it;
   this stands in for it as a host, so a session's own rows and their live
   refresh are exercised exactly as the box would open them. */
const toolsMenu = (view, anchor) => {
  const spec = () => ({ ...view.toolsMenuSpec(), current: "", footers: [] });
  return context.openChoiceMenu(anchor, { ...spec(), actions: true, live: spec,
    onPick: value => view.runSessionTool(value), ownerView: view.root });
};
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
    session_defaults: {permission_mode:"safe",model:"model",effort:"high"},
    model_options: [{ value: "model", label: "Model", fast_mode_available: fast,
      effort_options: [{ value: "high", label: "High" }] }],
    upgrade_supported: true, update_available: true, latest_version: "2.0.0", version: "1.0.0" }];
}
(async () => {
  state.engines = engineCatalog();
  const view = sessionView();
  state.views.chat = view;
  const anchor = mount(el("button"));
  // The same composer on an updated OpenCode node enables compact alone;
  // an older node's empty offer must continue to disable it.
  const opencode = { key: "opencode", label: "OpenCode", tool_options: [] };
  state.engines.push(opencode);
  const nativeView = sessionView();
  nativeView.session.engine = "opencode";
  state.views.native = nativeView;
  const nativeMenu = toolsMenu(nativeView, anchor);
  assert.equal(button(nativeMenu, "Compact context").disabled, true);
  opencode.tool_options = [{ value: "compact" }]; update();
  assert.ok(nativeMenu.isConnected);
  assert.equal(button(nativeMenu, "Compact context").disabled, false);
  assert.equal(button(nativeMenu, "Undo last turn").disabled, true);
  nativeMenu.remove(); delete state.views.native;
  const menu = toolsMenu(view, anchor);
  assert.equal(button(menu, "Undo last turn").disabled, true);
  assert.equal(button(menu, "Fast mode").disabled, true);
  button(menu, "Compact context").focus();
  state.engines[0].tool_options.push({ value: "undo" });
  state.engines[0].model_options[0].fast_mode_available = true;
  update();
  assert.ok(menu.isConnected, "a live refresh redraws the open menu in place");
  assert.equal(button(menu, "Undo last turn").disabled, false);
  assert.equal(button(menu, "Fast mode").disabled, false);
  assert.equal(document.activeElement, button(menu, "Compact context"), "live refresh preserves keyboard focus");
  let fast;
  view.setFastMode = value => { fast = value; };
  view.session.fast_mode = true; update();
  assert.equal(button(menu, "Fast mode").getAttribute("aria-checked"), "true");
  button(menu, "Fast mode").click();
  assert.equal(fast, false, "toggle acts on the latest setting");
  const models = view.showModelMenu(anchor);
  state.engines[0].model_options.push({ value: "new", label: "New model" }); update();
  assert.ok(button(models, "New model"));
  let model;
  view.applyModelChoice = value => { model = value; };
  button(models, "New model").click(); assert.equal(model, "new");
  /* Same rule in the composer: while this engine's catalog has not answered,
     the session's model is named plainly rather than reported as a choice
     the engine no longer offers. */
  view.session.model = "long[1m]";
  state.engines[0].dynamic_model_options = true;
  state.engines[0].model_catalog_loaded = false;
  const provisional = view.showModelMenu(anchor);
  assert.ok(button(provisional, "long[1m]"), "a pending catalog offers the model by name");
  assert.equal(button(provisional, "Current: long[1m]"), undefined);
  provisional.remove();
  state.engines[0].model_catalog_loaded = true;
  const answered = view.showModelMenu(anchor);
  assert.ok(button(answered, "Current: long[1m]"), "an answered catalog reports an unknown model");
  answered.remove();
  view.session.model = "model";
  delete state.engines[0].dynamic_model_options;
  delete state.engines[0].model_catalog_loaded;
  const withdrawn = toolsMenu(view, anchor);
  state.engines[0].tool_options = []; update();
  assert.equal(button(withdrawn, "Compact context").disabled, true, "withdrawn actions disable live");
  withdrawn.remove();
  view.tab.bid = 7; state.engCache[7] = state.engines;
  view.session.permission_mode = "safe";
  const perm = view.showPermMenu(anchor);
  view.session.queuedEngine = true; update();
  assert.equal(button(perm, "Safe").disabled, true);
  state.backends[0].capabilities.push("queued-permission-config"); update();
  assert.equal(button(perm, "Safe").disabled, false);
  const toolMenu = toolsMenu(view, anchor);
  toolMenu.focus();
  toolMenu.onkeydown(new FakeEvent("keydown", { key: "ArrowDown" }));
  assert.equal(document.activeElement, button(toolMenu, "Fast mode"), "keyboard skips actions disabled by live state");
  toolMenu.remove(); delete state.views.chat;

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
    assert.deepEqual(notices.at(-1), [`${bid ? "Peer" : "Main"}: Toggle refused`, "bad", context.TOAST_LONG]);
    shared.input.checked = false;
    await shared.input.onchange();
    assert.equal(shared.input.disabled, false); assert.equal(shared.input.checked, true);
    assert.deepEqual(notices.at(-1), [`${bid ? "Peer" : "Main"}: Toggle refused`, "bad", context.TOAST_LONG]);
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
  vm.runInContext("(function () { let notifyNodeSignature = ''; const backend = nfBackend;\n" +
    between("    this.notifyBackendsSync = () => {", "    enhanceChoiceSelect(backend);") +
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

  /* A dynamic catalog that has not answered is serving its driver's
     provisional fallback list, so a saved default missing from it is not a
     hand-typed model: it stays a model by name, and the answer that finally
     describes it must not leave the picker stuck on "Custom…". */
  const named = { value: "long[1m]", label: "Long context",
    effort_options: [{ value: "high", label: "High" }] };
  const pending = { ...engineCatalog()[0], allow_custom_model: true,
    dynamic_model_options: true, model_catalog_loaded: false,
    session_defaults: { permission_mode: "safe", model: named.value, effort: "high" } };
  state.engines = [pending];
  apiReply = { engines: [{ ...pending, model_catalog_loaded: true,
    model_options: [...pending.model_options, named] }] };
  let releaseEngines;
  hold = new Promise(resolve => { releaseEngines = resolve; });
  const opening = context.modalNewSession();
  await new Promise(resolve => setImmediate(resolve));
  const nsModel = dialog.m.querySelector("#ns-model");
  const nsCustom = dialog.m.querySelector("#ns-model-custom");
  const nsCustomWrap = dialog.m.querySelector("#ns-model-custom-wrap");
  assert.equal(nsModel.value, named.value, "a provisional catalog cannot invent a custom model");
  assert.equal(nsCustom.value, "");
  assert.equal(nsCustomWrap.classList.contains("hidden"), true);
  releaseEngines(); hold = null; await opening;
  assert.equal(nsModel.value, named.value, "the answered catalog keeps that model");
  assert.equal(nsModel.options.find(option => option.value === named.value).textContent, named.label);
  assert.equal(nsCustomWrap.classList.contains("hidden"), true);
  nsModel.value = "__custom__"; nsModel.onchange();
  nsCustom.value = "mine/only"; nsCustom.oninput();
  context.rememberEnginePayload(0, apiReply);
  assert.equal(nsModel.value, "__custom__", "a model the engine never offers keeps the box open");
  assert.equal(nsCustom.value, "mine/only");
  nsCustom.value = named.value; nsCustom.oninput();
  context.rememberEnginePayload(0, apiReply);
  assert.equal(nsModel.value, named.value, "a refresh stops calling a catalogued model custom");
  assert.equal(nsCustomWrap.classList.contains("hidden"), true);
  dialog.close();

  /* A catalog alias uses the named model in both dialog and composer pickers
     without turning a saved context selector into a different request. */
  const aliasRow = { value: "fable", label: "Fable", aliases: ["fable[1m]"],
    effort_options: [{ value: "max", label: "Max" }] };
  state.engines = [{ ...pending, model_catalog_loaded: true,
    session_defaults: { permission_mode: "safe", model: "fable[1m]", effort: "max" },
    model_options: [...pending.model_options, aliasRow] }];
  await context.modalNewSession();
  const aliasSelect = dialog.m.querySelector("#ns-model");
  assert.equal(aliasSelect.value, "fable[1m]");
  assert.equal(aliasSelect.options.find(option => option.value === "fable[1m]").textContent, "Fable");
  assert.equal(dialog.m.querySelector("#ns-model-custom-wrap").classList.contains("hidden"), true);
  assert.equal(dialog.m.querySelector("#ns-effort").value, "max");
  dialog.close();
  const aliasView = sessionView();
  aliasView.session.model = "fable[1m]";
  for (const native of [false, true]) {
    const spec = aliasView.composerChoiceSpec("model", native);
    assert.equal(spec.selected, "fable[1m]");
    assert.equal(spec.options.find(option => option.value === spec.selected).label, "Fable");
  }
  const exactContext = { ...aliasRow, value: "fable[1m]", label: "Fable extended", aliases: [],
    effort_options: [{ value: "high", label: "High" }] };
  state.engines[0].model_options.push(exactContext);
  assert.equal(aliasView.composerChoiceSpec("model").options.find(
    option => option.value === "fable[1m]").label, "Fable extended", "exact entries precede aliases");
  assert.equal(aliasView.composerChoiceSpec("effort").options[0].value, "high");
  aliasView.root.remove();
  state.engines = engineCatalog(); apiReply = {};
  update();

  /* Permission cards stay until the backend confirms. Disconnects disable
     every choice without pretending that an unanswered request was handled. */
  const approval = Object.create(SessionView.prototype), sent = [];
  Object.assign(approval, { tab: { bid: 0, sid: 1 }, root: mount(el("div")),
    approvalEl: mount(el("div", "approval hidden")), queueEl: el("div"), reconnecting: false,
    scrollBottom() {}, renderStatus() {}, updateSteerControl() {},
    setBackgroundTasks(value) { assert.equal(value, null); },
    ws: { readyState: 1, send: text => sent.push(JSON.parse(text)) },
  });
  approval.root.appendChild(approval.queueEl);
  const req = { request_id: "permission-1", tool_name: "Read", input: { file_path: "/demo/notes.txt" },
    suggestions: [{ type: "setMode", mode: "safe" }, { type: "allowAlways", label: "Always allow" }] };
  const choices = () => approval.approvalEl.querySelectorAll(".ap-btns button");
  const visible = () => !approval.approvalEl.classList.contains("hidden");
  approval.showApproval(req);
  assert.equal(choices().length, 4);
  for (const disconnected of [null, { readyState: 0 }, { readyState: 2 }, { readyState: 3 }]) {
    approval.ws = disconnected;
    for (const choice of choices()) choice.onclick(); // socket closed before onclose arrives
    assert.equal(sent.length, 0);
    assert.ok(visible());
    approval.setReconnecting(true);
    assert.ok(choices().every(choice => choice.disabled), "all suggestions disable together");
  }
  approval.ws = { readyState: 1, send: text => sent.push(JSON.parse(text)) };
  approval.setReconnecting(false);
  assert.ok(choices().every(choice => !choice.disabled));
  /* Allow, the engine's wider allows in their order, Deny last */
  assert.deepEqual([...choices()].map(choice => choice.textContent),
    ["Allow", "Allow + switch to safe", "Always allow", "Deny"]);
  choices()[1].onclick();
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0].updated_permissions, [req.suggestions[0]]);
  assert.ok(visible(), "WebSocket.send does not prove backend acceptance");
  assert.equal(approval.approvalEl.getAttribute("aria-busy"), "true");
  for (const choice of choices()) choice.onclick();
  assert.equal(sent.length, 1, "only one response while confirmation is pending");
  approval.handle({ type: "approval_resolved", request_id: "older-request" });
  assert.ok(visible(), "an old acknowledgement cannot dismiss the current permission");
  approval.handle({ type: "toast", level: "error", text: "backup or restore in progress" });
  assert.ok(visible());
  assert.ok(choices().every(choice => !choice.disabled), "a legacy refusal permits retry");
  choices()[3].onclick();
  assert.equal(sent[1].behavior, "deny");
  approval.ws = null; approval.setReconnecting(true);
  assert.ok(visible(), "disconnect before confirmation preserves the request");
  approval.ws = { readyState: 1, send: () => { throw new Error("socket closed"); } };
  approval.setReconnecting(false);
  approval.showApproval(req); // pending_approval from the next attach snapshot
  choices()[0].onclick();
  assert.ok(visible());
  assert.match(notices[notices.length - 1][0], /^Main: Approval response could not be sent/);
  approval.ws.send = text => sent.push(JSON.parse(text));
  choices()[0].onclick();
  approval.handle({ type: "approval_resolved", request_id: req.request_id });
  assert.equal(visible(), false);
  assert.equal(approval.approvalRequest, null);
  console.log("PASS: live composer menus, search recovery, local engine readiness, browser toggles, session links and New session choices");
})().catch(error => { console.error(error); process.exitCode = 1; });
