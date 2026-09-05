/* Run with node tests/task_config_ui_test.js. No browser or engine required. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => source.slice(source.indexOf(from), source.indexOf(to, source.indexOf(from)));

class Element {
  constructor(tag) {
    this.tag = tag; this.value = ""; this.disabled = false; this.children = [];
    this.dataset = {}; this.attributes = {}; this.classes = new Set();
    this.classList = { add: key => this.classes.add(key), remove: key => this.classes.delete(key),
      toggle: (key, on) => on ? this.classes.add(key) : this.classes.delete(key),
      contains: key => this.classes.has(key) };
  }
  set innerHTML(html) { this.html = html; this.children = []; }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(key, value) { this.attributes[key] = value; }
  querySelectorAll() { return this.children; }
  focus() { this.focused = true; }
}
const document = { createElement: tag => new Element(tag) };
const el = tag => new Element(tag);
const refreshChoiceSelect = () => {};
const esc = text => String(text);
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
const remote = [catalog("first"), { ...catalog("second"), allow_custom_model: true,
  effort_options: options(["", "high"]) }];
const state = { engines: [catalog("local")], engCache: { 7: remote },
  backends: [{ id: 7, capabilities: ["session-tasks", "session-task-config"] }] };
const captured = { engine: "first", model: "precise", effort: "high", permission_mode: "full" };
const main = { session: { ...captured }, effectiveConfig() { return { ...this.session }; } };
const sessions = [main.session];
const sessionsFor = () => sessions;
const workspace = { tab: { bid: 7, sid: 10 }, selected: 99,
  taskViews: new Map([[10, main], [99, { session: { engine: "second" } }]]),
  openTask(id) { this.opened = id; } };
let dialog, requests = [], fail = false, hold = null;
const ENGINE_POLL_TIMEOUT = 60000;
async function api(bid, route, options) {
  requests.push({ bid, route, ...options });
  if (route === "engines") return { engines: remote };
  if (hold) await hold;
  if (fail) throw new Error("Reply lost");
  return { session: { id: 50, ...options.body } };
}
function rememberEnginePayload(bid, data) {
  state.engCache[bid] = data.engines;
  for (const listener of enginePayloadListeners) listener(bid, data.engines);
}
function modal(html) {
  const nodes = {};
  for (const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g)) nodes["#" + match[2]] = new Element(match[1]);
  nodes[".backend-edit-error"] = new Element("p");
  const listeners = [];
  const m = { html, isConnected: true, setAttribute() {}, querySelector: key => nodes[key],
    querySelectorAll: () => Object.values(nodes).filter(node => ["input", "textarea", "select"].includes(node.tag))
      .concat(nodes["#nt-engines"] ? nodes["#nt-engines"].children : []) };
  const close = () => { m.isConnected = false; for (const listener of listeners) listener(); };
  dialog = { m, nodes, close, onClose: fn => listeners.push(fn) };
  return dialog;
}
const context = vm.createContext({ document, el, refreshChoiceSelect, esc, provSpec, renderSidebar,
  enginePayloadListeners, state, sessionsFor, modal, api, rememberEnginePayload, ENGINE_POLL_TIMEOUT });
vm.runInContext([
  between("function engineStatusText(", "const headWord ="),
  between("function nodeHasCapability(", "/* \"Enable tasks\""),
  between("async function modalNewTask(", "/* The review sheet:"),
].join("\n"), context);
const open = async () => { await context.modalNewTask(workspace); return dialog.nodes; };
const values = nodes => ({ engine: nodes["#nt-engines"].children.find(card => card.classes.has("sel"))?.dataset.key,
  model: nodes["#nt-model"].value, effort: nodes["#nt-effort"].value, permission_mode: nodes["#nt-perm"].value });
const selectEngine = (nodes, key) => nodes["#nt-engines"].children.find(card => card.dataset.key === key).onclick();

(async () => {
  let nodes = await open();
  assert.deepEqual(values(nodes), captured, "Main's choices, even from another task tab");
  assert.equal(requests.length, 0, "a loaded node catalog needs no extra polling");
  assert.ok(nodes["#nt-prompt"].focused);
  main.session.model = "quick";
  rememberEnginePayload(7, { engines: remote });
  assert.deepEqual(values(nodes), captured, "later Main updates must not change the dialog");
  nodes["#nt-perm"].value = "safe";
  rememberEnginePayload(7, { engines: remote });
  assert.equal(nodes["#nt-perm"].value, "safe", "refresh keeps user edits");
  nodes["#nt-model"].value = "quick"; nodes["#nt-model"].onchange();
  assert.equal(nodes["#nt-effort"].value, "");
  assert.deepEqual(nodes["#nt-effort"].children.map(node => node.value), ["", "low"]);
  selectEngine(nodes, "second");
  assert.deepEqual(values(nodes), { engine: "second", ...remote[1].session_defaults });
  nodes["#nt-model"].value = "__custom__"; nodes["#nt-model"].onchange();
  rememberEnginePayload(7, { engines: remote });
  assert.equal(nodes["#nt-model"].value, "__custom__", "an empty custom field stays open on refresh");
  nodes["#nt-model-custom"].value = "custom/model"; nodes["#nt-model-custom"].oninput();
  nodes["#nt-effort"].value = "high";
  rememberEnginePayload(7, { engines: remote });
  assert.equal(nodes["#nt-model-custom"].value, "custom/model");
  selectEngine(nodes, "second");
  assert.equal(nodes["#nt-model-custom"].value, "custom/model", "reselecting the current engine keeps edits");
  selectEngine(nodes, "first");
  assert.deepEqual(values(nodes), captured, "returning to Main's engine restores its captured choices");
  nodes["#nt-prompt"].value = "A task";
  nodes["#nt-name"].value = "My task";
  fail = true;
  let release; hold = new Promise(resolve => { release = resolve; });
  const pending = nodes["#nt-start"].onclick();
  assert.equal(nodes["#nt-start"].disabled, true);
  assert.equal(nodes["#nt-model"].disabled, true);
  await nodes["#nt-start"].onclick();
  release(); await pending; hold = null;
  assert.equal(requests.length, 1, "no duplicate submission while preparing");
  assert.equal(dialog.m.isConnected, true);
  assert.equal(nodes[".backend-edit-error"].textContent, "Reply lost");
  assert.equal(nodes["#nt-model"].disabled, false);
  fail = false; await nodes["#nt-start"].onclick();
  assert.deepEqual(requests[1], requests[0], "retry keeps the same identity and choices");
  assert.equal(requests[0].bid, 7);
  assert.equal(requests[0].route, "sessions/10/tasks");
  assert.deepEqual(JSON.parse(JSON.stringify(requests[0].body)), { ...captured, name: "My task", prompt: "A task",
    request_id: requests[0].body.request_id });
  assert.equal(workspace.opened, 50);
  assert.equal(enginePayloadListeners.size, 0);

  main.session = { ...captured, model: "retired" };
  nodes = await open();
  assert.equal(nodes["#nt-model"].value, "retired");
  assert.equal(nodes["#nt-model"].children[0].disabled, true);
  dialog.close();
  main.session = { ...captured };
  delete state.engCache[7]; requests = [];
  nodes = await open(); await Promise.resolve();
  assert.equal(requests[0].route, "engines");
  assert.deepEqual(values(nodes), captured, "late remote catalog preserves Main's selections");
  dialog.close();
  state.backends[0].capabilities = ["session-tasks"];
  nodes = await open();
  assert.equal(nodes["#nt-model"], undefined, "old nodes must not silently ignore editable choices");
  nodes["#nt-prompt"].value = "Legacy task";
  await nodes["#nt-start"].onclick();
  assert.equal(Object.hasOwn(requests.at(-1).body, "engine"), false);
  assert.equal(enginePayloadListeners.size, 0);
  console.log("PASS: task modal inheritance, engine/model dependencies, catalog refresh, custom/retired choices, remote/legacy nodes and retry lifecycle");
})().catch(error => { console.error(error); process.exitCode = 1; });
