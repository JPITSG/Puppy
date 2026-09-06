/* Run with node tests/task_config_ui_test.js. No browser or engine required.
   The New task dialog against the fake DOM: saved engine defaults, engine/
   model/effort dependencies, catalog refresh, custom and retired choices,
   local and remote nodes, the retry lifecycle, and the shared prompt box it
   hosts - Enter starts the task, attachments ride the prompt as marker lines,
   and cancelling discards what was staged. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument, FakeEvent, fire } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  if (start < 0 || end < 0) throw new Error("slice not found: " + from);
  return source.slice(start, end);
};

const document = new FakeDocument();
const refreshChoiceSelect = () => {};
const provSpec = key => ({ className: key, text: key });
const renderSidebar = () => {};
const enginePayloadListeners = new Set();
const options = values => values.map(value => ({ value, label: value || "Default" }));
const catalog = key => ({ key, label: key, installed: true, auth: "ok", allow_custom_model: false,
  dynamic_model_options: true, model_catalog_loaded: true,
  model_options: [{ value: "", label: "Default", effort_options: options([""]) },
    { value: "precise", label: "Precise", effort_options: options(["", "high"]) },
    { value: "quick", label: "Quick", effort_options: options(["", "low"]) }],
  permission_options: options(["safe", "full"]),
  session_defaults: { model: "quick", effort: "low", permission_mode: "safe" } });
const remote = [{ ...catalog("first"),
  session_defaults: { model: "precise", effort: "high", permission_mode: "full" } },
  { ...catalog("second"), allow_custom_model: true,
  effort_options: options(["", "high"]) }];
const capabilities = ["session-tasks", "session-task-config", "session-task-attachments", "file-uploads"];
const state = { engines: [catalog("local")], engCache: { 7: remote },
  backends: [{ id: 7, capabilities }], tabs: [], views: {}, active: "",
  remoteBrowserStatus: {}, browserStatus: null, terminalInstances: {},
  remoteUploadSettings: {}, uploadSettings: null };
const captured = { engine: "first", model: "precise", effort: "high", permission_mode: "full" };
const mainChoices = { engine: "first", model: "quick", effort: "low", permission_mode: "safe" };
const main = { session: { ...mainChoices }, effectiveConfig() { return { ...this.session }; } };
const sessions = [main.session];
const sessionsFor = () => sessions;
const workspace = { tab: { bid: 7, sid: 10 }, selected: 99,
  taskViews: new Map([[10, main], [99, { session: { engine: "second" } }]]),
  openTask(id) { this.opened = id; } };
let dialog, requests = [], fail = false, hold = null, catalogHold = null;
const fetches = [], toasts = [];
const ENGINE_POLL_TIMEOUT = 60000;
async function api(bid, route, options = {}) {
  requests.push({ bid, route, ...options });
  if (route === "engines") { if (catalogHold) await catalogHold; return { engines: remote }; }
  if (options.method === "DELETE") return { ok: true };
  if (hold) await hold;
  if (fail) throw new Error("Reply lost");
  return { session: { id: 50, ...options.body } };
}
function rememberEnginePayload(bid, data) {
  state.engCache[bid] = data.engines;
  for (const listener of enginePayloadListeners) listener(bid, data.engines);
}
/* the dialog frame: real elements from the dialog's own markup, closed by
   leaving the document, so isConnected and onClose behave as in the app */
function modal(html) {
  const m = document.createElement("div");
  m.className = "modal";
  m.innerHTML = html;
  document.body.appendChild(m);
  const listeners = [];
  const close = () => { if (!m.isConnected) return; m.remove(); for (const listener of listeners) listener(); };
  dialog = { m, close, onClose: fn => listeners.push(fn) };
  return dialog;
}
const icon = () => document.createElement("svg");
const context = vm.createContext({
  document, window: { CSS: { supports: () => true } }, CSS: { supports: () => true },
  Event: FakeEvent, console, crypto: require("node:crypto").webcrypto,
  URL: { createObjectURL: () => "blob:fake", revokeObjectURL: () => {} },
  AbortController, setTimeout, clearTimeout, getComputedStyle: () => ({}),
  fetch: (url, init) => new Promise(resolve => fetches.push({ url, init, resolve })),
  refreshChoiceSelect, provSpec, renderSidebar, enginePayloadListeners, state, sessionsFor, modal, api,
  rememberEnginePayload, ENGINE_POLL_TIMEOUT,
  toast: (text, level) => toasts.push({ text, level }),
  esc: text => String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
  apiPath: (bid, route) => (bid ? `/api/b/${bid}/` : "/api/") + route,
  fmtBytes: value => value + " B",
  plusIcon: icon, xIcon: icon, attachmentFileIcon: icon, globeIcon: icon, terminalIcon: icon,
  choiceSvg: icon, refreshIcon: icon,
  uploadSettingsFor: () => null, rememberUploadSettings: () => null,
  backendSupportsFileUploads: () => true, backendConnectionAllowed: () => true,
  nodeStateStreamActive: () => true, browserEnabledFor: () => false,
  backendHasCapability: (backend, capability) => !!backend && backend.capabilities.includes(capability),
  spawnExecFor: () => false, findSessionMeta: () => null, backendName: bid => "node " + bid,
  browserInstancesFor: () => true, terminalInstancesFor: () => true, uploadPreviewUrl: () => "",
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("const CARET_MIRROR_STYLES", "/* ctrl+j ->"),
  between("function engineStatusText(", "const headWord ="),
  between("const MENTION_QUERY_MAX", "/* ================= Composer ================="),
  between("/* ================= Composer =================", "/* ================= SessionView ================="),
  between("const ATTACHMENT_PREVIEW_TYPES", "/* Resolve the configuration at the queue tail"),
  between("function nodeHasCapability(", "/* \"Enable tasks\""),
  between("async function modalNewTask(", "/* The review sheet:"),
].join("\n"), context);
const { Composer } = vm.runInContext("({ Composer })", context);
const nodes = new Proxy({}, { get: (_, key) => dialog.m.querySelector(key) });
const open = async () => { await context.modalNewTask(workspace); return nodes; };
const values = n => ({ engine: n["#nt-engines"].querySelectorAll(".ep").find(card => card.classList.contains("sel")).dataset.key,
  model: n["#nt-model"].value, effort: n["#nt-effort"].value, permission_mode: n["#nt-perm"].value });
const selectEngine = (n, key) => n["#nt-engines"].querySelectorAll(".ep").find(card => card.dataset.key === key).onclick();
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const image = { name: "shot.png", size: 5, type: "image/png" };
const paste = ta => fire(ta, "paste", { clipboardData: { items: [{ kind: "file", getAsFile: () => image }] } });
function completeUpload(entry, id) {
  entry.resolve({ ok: true, status: 200, json: async () => ({ ok: true, upload_id: id,
    path: `/srv/data/uploads/10/${id}/shot.png`, name: "shot.png", size: 5, content_type: "image/png" }) });
}
const deletes = from => requests.slice(from).filter(r => r.method === "DELETE").map(r => [r.bid, r.route]);

(async () => {
  let n = await open();
  assert.deepEqual(values(n), captured, "saved defaults override Main's low effort, even from another task tab");
  assert.equal(requests.length, 0, "a loaded node catalog needs no extra polling");
  assert.equal(document.activeElement, n["#nt-prompt"]);
  assert.ok(n[".composer-box"].classList.contains("mention-below"), "the list opens downward inside a sheet");
  assert.equal(n["label.task-composer-lbl"].getAttribute("for"), "nt-prompt");
  assert.equal(Composer.live.size, 1, "the task box is the shared prompt box");
  main.session.model = "retired";
  rememberEnginePayload(7, { engines: remote });
  assert.deepEqual(values(n), captured, "later Main updates must not change the dialog");
  n["#nt-perm"].value = "safe";
  rememberEnginePayload(7, { engines: remote });
  assert.equal(n["#nt-perm"].value, "safe", "refresh keeps user edits");
  n["#nt-model"].value = "quick"; n["#nt-model"].onchange();
  assert.equal(n["#nt-effort"].value, "");
  assert.deepEqual(n["#nt-effort"].children.map(node => node.value), ["", "low"]);
  selectEngine(n, "second");
  assert.deepEqual(values(n), { engine: "second", ...remote[1].session_defaults });
  n["#nt-model"].value = "__custom__"; n["#nt-model"].onchange();
  rememberEnginePayload(7, { engines: remote });
  assert.equal(n["#nt-model"].value, "__custom__", "an empty custom field stays open on refresh");
  n["#nt-model-custom"].value = "custom/model"; n["#nt-model-custom"].oninput();
  n["#nt-effort"].value = "high";
  rememberEnginePayload(7, { engines: remote });
  assert.equal(n["#nt-model-custom"].value, "custom/model");
  selectEngine(n, "second");
  assert.equal(n["#nt-model-custom"].value, "custom/model", "reselecting the current engine keeps edits");
  selectEngine(n, "first");
  assert.deepEqual(values(n), captured, "returning to Main's engine loads its saved defaults");
  n["#nt-prompt"].value = "A task";
  n["#nt-name"].value = "My task";
  fail = true;
  let release; hold = new Promise(resolve => { release = resolve; });
  const pending = n["#nt-start"].onclick();
  assert.equal(n["#nt-start"].disabled, true);
  assert.equal(n["#nt-model"].disabled, true);
  assert.equal(n["#nt-prompt"].readOnly, true, "the box is held while the task is prepared");
  assert.equal(n[".attach-add"].disabled, true);
  await n["#nt-start"].onclick();
  release(); await pending; hold = null;
  assert.equal(requests.length, 1, "no duplicate submission while preparing");
  assert.equal(dialog.m.isConnected, true);
  assert.equal(n[".form-error"].textContent, "Reply lost");
  assert.equal(n["#nt-model"].disabled, false);
  assert.equal(n["#nt-prompt"].readOnly, false);
  assert.equal(n[".attach-add"].disabled, false);
  fail = false; await n["#nt-start"].onclick();
  assert.deepEqual(requests[1], requests[0], "retry keeps the same identity and choices");
  assert.equal(requests[0].bid, 7);
  assert.equal(requests[0].route, "sessions/10/tasks");
  assert.deepEqual(JSON.parse(JSON.stringify(requests[0].body)), { ...captured, name: "My task", prompt: "A task",
    request_id: requests[0].body.request_id });
  assert.equal(workspace.opened, 50);
  assert.equal(enginePayloadListeners.size, 0);
  assert.equal(Composer.live.size, 0, "a closed dialog leaves no box behind");

  /* Enter starts the task; Shift+Enter does not; an empty box only focuses */
  requests = [];
  n = await open();
  assert.equal(fire(n["#nt-prompt"], "keydown", { key: "Enter" }).defaultPrevented, true);
  await settle();
  assert.equal(requests.length, 0, "an empty task is not started");
  assert.equal(document.activeElement, n["#nt-prompt"]);
  n["#nt-prompt"].value = "Typed task";
  fire(n["#nt-prompt"], "keydown", { key: "Enter", shiftKey: true });
  await settle();
  assert.equal(requests.length, 0);
  fire(n["#nt-prompt"], "keydown", { key: "Enter" });
  await settle();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].body.prompt, "Typed task");
  assert.equal(dialog.m.isConnected, false);

  /* attachments ride the prompt as marker lines; a started task keeps them */
  requests = [];
  n = await open();
  n["#nt-prompt"].value = "Build this";
  assert.equal(paste(n["#nt-prompt"]).defaultPrevented, true);
  assert.equal(fetches.length, 1);
  assert.equal(fetches[0].url, "/api/b/7/sessions/10/upload");
  await n["#nt-start"].onclick();
  assert.equal(requests.length, 0, "a send waits for the upload");
  assert.equal(toasts[toasts.length - 1].text, "Wait for file uploads to finish");
  completeUpload(fetches[0], "1700000000000-a1b2c3d4e5");
  await settle();
  assert.ok(n[".attach-chip.image"]);
  hold = new Promise(resolve => { release = resolve; });
  const attachedTask = n["#nt-start"].onclick();
  assert.equal(n[".attach-x"].disabled, true, "task preparation locks attachment removal");
  n[".attach-x"].onclick();
  assert.ok(n[".attach-chip.image"], "the submitted attachment stays visible");
  assert.deepEqual(deletes(0), [], "a stale removal click cannot race adoption");
  release(); await attachedTask; hold = null;
  assert.equal(requests[0].body.prompt, "Build this\n\n[image attached: /srv/data/uploads/10/" +
    "1700000000000-a1b2c3d4e5/shot.png — view it with your image/file tools]");
  assert.deepEqual(deletes(0), [], "the node adopted the file; nothing to discard");
  assert.equal(dialog.m.isConnected, false);

  /* cancelling discards what the dialog staged; an old node offers no attach */
  requests = [];
  n = await open();
  paste(n["#nt-prompt"]);
  completeUpload(fetches[1], "1700000000001-b2c3d4e5f6");
  await settle();
  n["#nt-cancel"].onclick();
  assert.deepEqual(deletes(0), [[7, "sessions/10/upload/1700000000001-b2c3d4e5f6"]]);
  assert.equal(Composer.live.size, 0);
  const defaults = remote[0].session_defaults;
  remote[0].session_defaults = { ...defaults, model: "retired" };
  n = await open();
  assert.equal(n["#nt-model"].value, "retired");
  assert.equal(n["#nt-model"].children[0].disabled, true);
  dialog.close();
  remote[0].session_defaults = defaults;
  main.session = { ...mainChoices };
  n = await open();
  remote[0].session_defaults = { model: "quick", effort: "low", permission_mode: "safe" };
  rememberEnginePayload(7, { engines: remote });
  assert.deepEqual(values(n), captured, "a refresh of saved defaults preserves the prepared task");
  selectEngine(n, "second");
  selectEngine(n, "first");
  assert.deepEqual(values(n), { engine: "first", ...remote[0].session_defaults },
    "switching back loads the current saved defaults");
  dialog.close();
  remote[0].session_defaults = { ...defaults, model: "", effort: "" };
  n = await open();
  assert.equal(n["#nt-model"].value, "");
  assert.equal(n["#nt-effort"].value, "", "saved engine-native choices stay empty");
  dialog.close();
  remote[0].session_defaults = defaults;
  delete state.engCache[7]; requests = [];
  let releaseCatalog;
  catalogHold = new Promise(resolve => { releaseCatalog = resolve; });
  n = await open();
  assert.equal(requests[0].route, "engines");
  assert.equal(n["#nt-start"].disabled, true, "wait for the saved defaults before allowing submission");
  assert.equal(n["#nt-model"].disabled, true);
  assert.equal(n["#nt-effort"].disabled, true);
  n["#nt-prompt"].value = "Prepared while loading";
  await n["#nt-start"].onclick();
  assert.equal(requests.length, 1);
  releaseCatalog(); await settle(); catalogHold = null;
  assert.deepEqual(values(n), captured, "late remote catalog initializes from saved defaults");
  assert.equal(n["#nt-start"].disabled, false);
  assert.equal(n["#nt-effort"].disabled, false);
  assert.equal(n["#nt-prompt"].value, "Prepared while loading");
  dialog.close();
  workspace.tab.bid = 0;
  main.session = { ...captured, engine: "local" };
  n = await open();
  assert.deepEqual(values(n), { engine: "local", ...state.engines[0].session_defaults },
    "the primary uses its own defaults, independently of the remote backend");
  dialog.close();

  /* Dismissal during preparation, or after an uncertain response, cannot
     delete bytes the node may still be copying into the submitted task. */
  for (const uncertain of [false, true]) {
    requests = [];
    n = await open(); n["#nt-prompt"].value = "Use this attachment";
    paste(n["#nt-prompt"]);
    completeUpload(fetches[fetches.length - 1], "1700000000099-b2c3d4e5f6");
    await settle();
    hold = new Promise(resolve => { release = resolve; }); fail = uncertain;
    const creating = n["#nt-start"].onclick();
    if (uncertain) {
      release(); await creating;
      assert.equal(n[".attach-x"].disabled, false, "an unsuccessful dialog becomes editable again");
      n[".attach-x"].onclick();
      assert.equal(n[".attach-chip"], null);
      assert.deepEqual(deletes(0), [], "editing after a lost reply cannot race a late adoption either");
    }
    dialog.close();
    assert.deepEqual(deletes(0), [], "closing a submitted dialog leaves adoption safe");
    if (!uncertain) { release(); await creating; }
    hold = null; fail = false;
    assert.equal(Composer.live.size, 0);
  }
  console.log("PASS: task modal engine defaults, engine/model dependencies, catalog refresh and delayed initialization, custom/retired choices, local/remote nodes, retry lifecycle, and its shared prompt box (Enter, attachments, discard on cancel)");
})().catch(error => { console.error(error); process.exitCode = 1; });
