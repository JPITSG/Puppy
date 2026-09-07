/* Settings timeout controls against the shared fake DOM. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(require("node:path").join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (a, b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a)));
const document = new FakeDocument();
const state = { timeouts: null, remoteTimeouts: {}, backends: [] };
const requests = [], toasts = [];
const reachable = new Set([0, 1]);
const context = vm.createContext({ document, state, console, Number, Map, Set, Promise,
  el: (tag, cls, text) => { const e = document.createElement(tag); e.className = cls; if (text) e.textContent = text; return e; },
  enhanceChoiceSelect: () => {}, refreshChoiceSelect: () => {},
  backendSupportsTimeoutSettings: bid => bid !== 3,
  backendConnectionAllowed: bid => reachable.has(bid),
  remoteAvailability: bid => reachable.has(bid) ? "ok" : "bad",
  backendStateNote: () => "Backend unavailable",
  nodeStateStreamActive: () => true,
  toast: (...args) => toasts.push(args),
  api: (bid, route, options) => new Promise((resolve, reject) => requests.push({ bid, route, options, resolve, reject })),
});
vm.runInContext(between("const TIMEOUT_FIELDS =", "function normalizeTimerSettings"), context);
vm.runInContext(`class View { ${between("  timeoutSettingsCard(", "  timerSettingsCard(")} }; this.View = View;`, context);
const keys = ["turn_seconds", "spawn_runtime_seconds", "spawn_idle_seconds", "terminal_idle_seconds", "browser_idle_seconds", "vnc_idle_seconds"];
const defaults = Object.fromEntries(keys.map((key, index) => [key, [7200, 7200, 600, 900, 900, 900][index]]));
const payload = (values = defaults) => ({ values: { ...values }, defaults: { ...defaults }, max_seconds: 2147483647 });
const tick = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
const submit = form => form.onsubmit(new FakeEvent("submit"));

(async () => {
  assert.equal(vm.runInContext('normalizeTimeoutSettings({values:{}, defaults:{}, max_seconds:10})', context), null);
  const view = new context.View(); view.renderGeneration = 1;
  const card = view.timeoutSettingsCard([{ bid: 0, name: "Demo" }, { bid: 1, name: "Peer" },
    { bid: 2, name: "Offline" }, { bid: 3, name: "Older" }], payload(), 1);
  document.body.appendChild(card);
  const forms = card.querySelectorAll("form");
  assert.equal(forms.length, keys.length);
  const inputs = forms.map(form => form.querySelector("input"));
  assert.deepEqual(inputs.map(input => input.value), ["7200", "7200", "600", "900", "900", "900"]);
  for (const input of inputs) {
    assert.equal(input.min, "0"); assert.equal(input.step, "1");
    assert.match(input.getAttribute("aria-label"), /0 is unlimited/);
  }
  inputs[0].value = "0"; inputs[0].oninput();
  const sending = submit(forms[0]);
  assert.equal(forms[0].getAttribute("aria-busy"), "true");
  assert.equal(inputs[0].disabled, true);
  assert.equal(requests[0].bid, 0); assert.equal(requests[0].options.body.turn_seconds, 0);
  requests.shift().reject(new Error("Disk is full"));
  await sending;
  assert.equal(inputs[0].value, "0");
  assert.equal(forms[0].querySelector(".form-error").textContent, "Disk is full");
  assert.equal(toasts.length, 0);
  const retry = submit(forms[0]);
  requests.shift().resolve({ timeouts: payload({ ...defaults, turn_seconds: 0 }) });
  await retry;
  assert.equal(state.timeouts.values.turn_seconds, 0);
  assert.equal(forms[0].querySelector(".form-error").textContent, "");
  assert.match(toasts[0][0], /unlimited/);
  for (const invalid of ["", "-1", "1.5", "Infinity"]) {
    inputs[1].value = invalid; inputs[1].oninput(); await submit(forms[1]);
    assert.equal(requests.length, 0);
    assert.match(forms[1].querySelector(".form-error").textContent, /whole number/);
  }
  inputs[1].value = "14400"; inputs[1].oninput();
  view.timeoutSettingsSync();
  assert.equal(inputs[1].value, "14400");  // stream repaint preserves unsaved input
  const select = card.querySelector("select");
  select.value = "1"; select.onchange();
  assert.equal(requests.length, 1); assert.equal(requests[0].bid, 1);
  requests.shift().resolve({ timeouts: payload({ ...defaults, turn_seconds: 10800 }) });
  await tick();
  assert.equal(inputs[0].value, "10800");
  inputs[0].value = "0"; inputs[0].oninput();
  const remoteSave = submit(forms[0]);
  const remoteRequest = requests.shift(); assert.equal(remoteRequest.bid, 1);
  select.value = "0"; select.onchange();
  assert.equal(inputs[1].value, "14400");
  remoteRequest.resolve({ timeouts: payload({ ...defaults, turn_seconds: 0 }) });
  await remoteSave;
  assert.equal(inputs[1].value, "14400");
  assert.equal(state.remoteTimeouts[1].values.turn_seconds, 0);
  for (const bid of [2, 3]) {
    select.value = String(bid); select.onchange();
    assert.ok(inputs.every(input => input.disabled)); assert.equal(requests.length, 0);
  }
  select.value = "0"; select.onchange();
  inputs[1].onkeydown(new FakeEvent("keydown", { key: "Escape" }));
  assert.equal(inputs[1].value, "7200");
  const reset = card.querySelector(".timer-actions button");
  const resetting = reset.onclick();
  assert.equal(reset.disabled, true); assert.equal(select.disabled, true);
  const freshDefaults = { ...defaults, turn_seconds: 18000 };
  requests.shift().resolve({ timeouts: { ...payload(), defaults: freshDefaults } });
  await tick();
  const resetRequest = requests.shift();
  assert.equal(resetRequest.options.body.turn_seconds, 18000);
  resetRequest.resolve({ timeouts: payload(freshDefaults) });
  await resetting;
  assert.equal(inputs[0].value, "18000");
  console.log("timeout settings UI tests passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
