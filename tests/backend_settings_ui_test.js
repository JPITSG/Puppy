/* Run with node tests/backend_settings_ui_test.js. The Settings backend card's
   actual markup and handlers, without a browser, network or backend mutation. */
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
const backend = { id: 7, name: "Demo backend", urls: ["https://demo.invalid"], capabilities: [] };
const state = { backends: [backend], remoteOk: { 7: true }, remoteErrors: {}, views: {} };
const requests = [], notices = [], confirmations = [], removed = [];
let hold = null, failure = "", confirmed = true;
const context = vm.createContext({
  document, el, state, settings: { version: "1.0.0" }, generation: 1,
  esc: text => String(text), plusIcon: () => el("svg"), xIcon: () => el("svg"),
  transportShieldIcon: () => el("svg"), backendLocationNode: b => el("span", "be-url", b.urls[0]),
  backendName: () => "Demo instance", backendHasCapability: () => false,
  requestAnimationFrame: callback => callback(),
  TOAST_LONG: 7000, toast: (...args) => notices.push(args),
  modalConfirm: async (...args) => { confirmations.push(args); return confirmed; },
  installBackendRecord() {}, enableAddedBackendBrowser: async () => {},
  discardBackendRecord: id => removed.push(id),
  api: async (bid, route, options) => {
    requests.push({ bid, route, ...options });
    if (hold) await hold;
    if (failure) throw new Error(failure);
    return { backend };
  },
});
vm.runInContext([
  between("function configuredBackendUrls(", "function activeBackendUrl("),
  between("function backendUrlEditor(", "function formatSessionActivity("),
  "globalThis.Card = class { render() { const c2 = el('div'); this.inner.appendChild(c2);",
  between("    /* backends */", "    /* security */"),
  "} };",
].join("\n"), context);
function card() {
  const view = new context.Card();
  Object.assign(view, { inner: el("div"), remoteBackendDots: new Map(), remoteBackendMeta: new Map(),
    backendAutoToggles: new Map(), upgradeButtons: new Map(), upgradesInProgress: new Set(),
    syncUpgradeButtons() {}, syncBackendAutoToggles() {},
  });
  document.body.appendChild(view.inner); view.render();
  return view;
}
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const view = card(), form = view.inner.querySelector(".backend-add-form");
  const add = form.querySelector("#be-add"), error = form.querySelector(".form-error");
  assert.equal(form.tagName, "FORM", "native Enter submission requires an actual form");
  assert.equal(add.getAttribute("type"), "submit");
  for (const action of form.querySelectorAll(".backend-url-action"))
    assert.equal(action.type, "button", "URL editor actions never implicitly submit");
  const submit = () => {
    const event = new FakeEvent("submit");
    const result = form.onsubmit(event);
    assert.ok(event.defaultPrevented);
    return result;
  };
  await submit();
  assert.equal(requests.length, 0);
  assert.match(error.textContent, /at least one backend URL/);
  assert.equal(error.classList.contains("hidden"), false);
  form.querySelector("#be-name").value = "Demo backend";
  form.querySelector("#be-token").value = "demo-token";
  form.querySelector("#be-urls input").value = "https://demo.invalid";
  let release;
  hold = new Promise(resolve => { release = resolve; }); failure = "network error";
  const pending = submit();
  assert.equal(add.disabled, true); assert.equal(add.textContent, "Adding…");
  assert.equal(form.getAttribute("aria-busy"), "true");
  await submit(); assert.equal(requests.length, 1, "repeated Enter cannot add duplicate backends");
  release(); await pending; hold = null;
  assert.equal(error.textContent, "network error");
  assert.equal(notices.length, 0, "add failures stay inline");
  assert.equal(add.disabled, false); assert.equal(add.textContent, "Add backend");
  assert.equal(form.querySelector("#be-token").value, "demo-token", "failure preserves the form draft");
  assert.equal(form.hasAttribute("aria-busy"), false);
  failure = ""; await submit();
  assert.equal(requests.length, 2);
  assert.deepEqual(JSON.parse(JSON.stringify(requests[1].body)), {
    name: "Demo backend", urls: ["https://demo.invalid"], token: "demo-token",
    tls_fingerprint: "", auto_upgrade: false,
  });
  view.inner.remove();

  const removalView = card();
  const remove = removalView.inner.querySelectorAll(".be-actions button").find(b => b.textContent === "Remove");
  requests.length = 0; notices.length = 0;
  confirmed = false; await remove.onclick();
  assert.equal(requests.length, 0); assert.equal(removed.length, 0);
  confirmed = true; failure = "network error";
  hold = new Promise(resolve => { release = resolve; });
  const removing = remove.onclick(); await tick();
  assert.equal(remove.disabled, true); assert.equal(remove.textContent, "Removing…");
  const count = confirmations.length;
  await remove.onclick(); assert.equal(confirmations.length, count);
  assert.equal(requests.length, 1, "a pending removal cannot be duplicated");
  release(); await removing; hold = null;
  assert.deepEqual(removed, [], "a failed DELETE retains the backend record");
  assert.deepEqual(notices[0], ["Demo backend: Could not remove backend · network error", "error", 7000]);
  assert.equal(remove.disabled, false); assert.equal(remove.textContent, "Remove");
  failure = ""; await remove.onclick();
  assert.equal(requests.length, 2); assert.deepEqual(removed, [7]);
  assert.equal(requests[1].route, "backends/7"); assert.equal(requests[1].method, "DELETE");
  assert.equal(confirmations[count - 1][2].destructive, true);
  const refreshView = card();
  const refreshRemove = refreshView.inner.querySelectorAll(".be-actions button").find(b => b.textContent === "Remove");
  refreshView.render = async () => { throw new Error("network error"); };
  await refreshRemove.onclick();
  assert.equal(notices[notices.length - 1][0],
    "Demo backend: Backend removed · Could not refresh settings · network error",
    "a refresh failure cannot report a confirmed deletion as failed");
  console.log("PASS: backend form submission, duplicate guard, inline errors, removal failure and retry");
})().catch(error => { console.error(error); process.exitCode = 1; });
