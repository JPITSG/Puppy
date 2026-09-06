/* Completion alert settings against the shared fake DOM. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(require("node:path").join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (a, b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a)));
const document = new FakeDocument();
const state = { notify: { configured: false, enabled: false }, backends: [
  { id: 1, name: "Demo peer", capabilities: ["notify-exec"] },
  { id: 2, name: "Older peer", capabilities: [] },
] };
const requests = [];
const context = vm.createContext({ document, state, console, Number, JSON, Promise,
  $: id => document.getElementById(id),
  el: (tag, cls) => { const e = document.createElement(tag); e.className = cls; return e; },
  bellIcon: () => document.createElement("svg"),
  enhanceChoiceSelect: () => {}, refreshChoiceSelect: () => {},
  backendName: () => "Demo local",
  api: (bid, route, options) => new Promise((resolve, reject) => requests.push({ bid, route, options, resolve, reject })),
});
vm.runInContext(between("function notifyPublicState(", "/* Sign out of this console"), context);
vm.runInContext(`class View { ${between("  notifySettingsCard(", "  async render() {")} }; this.View = View;`, context);
const tick = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
const payload = (overrides = {}) => ({ settings: { enabled: false, backend: 0,
  success_command: "success", failure_command: "failure", ...overrides } });
const change = (input, value) => { input.value = value; input.oninput(); };
const bell = document.createElement("button"); bell.id = "btn-bell"; document.body.appendChild(bell);

(async () => {
  const view = new context.View(); view.renderGeneration = 1;
  const card = view.notifySettingsCard(1); document.body.appendChild(card);
  const form = card.querySelector("form");
  const success = card.querySelector("#nf-success"), failure = card.querySelector("#nf-failure");
  const backend = card.querySelector("#nf-backend"), enabled = card.querySelector("#nf-enabled");
  const save = card.querySelector("#nf-save"), note = card.querySelector("#nf-note");
  const error = card.querySelector(".form-error");
  const testSuccess = card.querySelector("#nf-test-success"), testFailure = card.querySelector("#nf-test-failure");
  const submit = () => form.onsubmit(new FakeEvent("submit"));
  assert.equal(success.disabled, true); assert.equal(save.disabled, true);
  await tick(); requests.shift().reject(new Error("Load failed")); await tick();
  assert.equal(error.textContent, "Load failed"); assert.equal(save.disabled, true);
  const retry = card.querySelector("#nf-retry"); assert.equal(retry.classList.contains("hidden"), false);
  const loading = retry.onclick(); requests.shift().resolve(payload()); await loading;
  assert.equal(success.value, "success"); assert.equal(failure.value, "failure");
  assert.equal(enabled.checked, false); assert.equal(bell.classList.contains("hidden"), false);
  assert.equal(backend.options[2].disabled, true);
  assert.equal(save.disabled, false); assert.equal(success.getAttribute("maxlength"), "1000");
  change(failure, "draft failure"); assert.equal(note.textContent, "Unsaved changes");
  let testing = testFailure.onclick();
  assert.equal(form.getAttribute("aria-busy"), "true"); assert.equal(save.disabled, true);
  let request = requests.shift(); assert.equal(request.route, "notify/test");
  assert.equal(request.options.body.command, "draft failure"); assert.equal(request.options.body.status, "error");
  request.resolve({ ok: true, output: "error" }); await testing;
  assert.match(note.textContent, /Unsaved changes/); assert.equal(failure.value, "draft failure");
  testing = testSuccess.onclick(); request = requests.shift();
  assert.equal(request.options.body.command, "success"); assert.equal(request.options.body.status, "ok");
  request.resolve({ ok: false, rc: 2, output: "failed to run" }); await testing;
  assert.equal(error.textContent, "Exit 2 · failed to run"); assert.equal(save.disabled, false);
  enabled.checked = true;
  let toggling = enabled.onchange(); request = requests.shift();
  assert.equal(enabled.disabled, true); assert.equal(request.options.body.enabled, true);
  request.resolve(payload({ enabled: true })); await toggling;
  assert.equal(enabled.checked, true); assert.equal(failure.value, "draft failure");
  backend.value = "1"; backend.onchange();
  let saving = submit(); request = requests.shift();
  assert.equal(request.route, "notify"); assert.equal(request.options.body.backend, 1);
  assert.equal(request.options.body.success_command, "success");
  assert.equal(request.options.body.failure_command, "draft failure");
  assert.equal("enabled" in request.options.body, false);
  assert.equal(save.disabled, true);
  request.reject(new Error("Disk full")); await saving;
  assert.equal(error.textContent, "Disk full"); assert.equal(failure.value, "draft failure");
  saving = submit(); requests.shift().resolve(payload({ enabled: true, backend: 1, failure_command: "draft failure" })); await saving;
  assert.equal(error.textContent, ""); assert.equal(note.textContent, "Saved");
  change(success, ""); assert.equal(testSuccess.disabled, true); assert.equal(testFailure.disabled, false);
  saving = submit(); requests.shift().resolve(payload({ enabled: true, backend: 1, success_command: "", failure_command: "draft failure" })); await saving;
  assert.equal(bell.classList.contains("hidden"), false);
  change(failure, ""); assert.equal(testFailure.disabled, true);
  saving = submit(); requests.shift().resolve(payload({ enabled: true, backend: 1, success_command: "", failure_command: "" })); await saving;
  assert.equal(bell.classList.contains("hidden"), true); assert.equal(enabled.checked, true);
  enabled.checked = false; toggling = enabled.onchange(); requests.shift().reject(new Error("Toggle failed")); await toggling;
  assert.equal(enabled.checked, true); assert.equal(error.textContent, "Toggle failed");
  // Removing a backend never silently retargets a draft to local execution.
  state.backends = []; view.notifyBackendsSync();
  assert.equal(backend.value, "1"); assert.equal(backend.options[1].disabled, true);
  // A load finishing after a rerender must not overwrite the current bell.
  const stale = view.notifySettingsCard(1); document.body.appendChild(stale); await tick();
  view.renderGeneration = 2; requests.shift().resolve(payload()); await tick();
  assert.equal(state.notify.enabled, true);
  assert.equal(requests.length, 0);
  console.log("notification UI tests passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
