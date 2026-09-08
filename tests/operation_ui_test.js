/* Real request/progress/modal code, with controlled replies and no network. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const { FakeDocument, FakeElement, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => source.slice(source.indexOf(from), source.indexOf(to, source.indexOf(from)));
const document = new FakeDocument();
const el = (tag, cls = "", text = "") => { const n = document.createElement(tag); n.className = cls; n.textContent = text; return n; };
const root = el("div"); root.id = "modal-root"; document.body.appendChild(root);
const opener = el("button"); document.body.appendChild(opener); opener.focus();
let serial = 0, reply, fetcher, calls = [];
const deferred = () => { let resolve; const promise = new Promise(done => resolve = done); return { promise, resolve }; };
const response = (status, data) => ({ status, ok: status < 400, json: async () => data });
/* One clock for every timer this code arms, so a progress dialog's wait is
   spent deliberately and an operation that answers first can prove it never
   opened one. */
let now = 1000000, nextTimer = 0;
const timers = new Map();
const context = vm.createContext({
  document, HTMLElement: FakeElement, console, el, $: id => document.getElementById(id),
  esc: text => text, state: { backends: [] }, AbortController, Blob,
  setTimeout: (fn, ms) => {
    const id = ++nextTimer;
    timers.set(id, { fn, at: now + Math.max(0, Number(ms) || 0) });
    return id;
  },
  clearTimeout: id => { timers.delete(id); },
  closeChoiceMenu() {}, enhanceChoiceSelect() {}, openChoiceControl: null, showAuth() {},
  apiPath: (bid, route) => `${bid}/${route}`,
  newDraftClientId: () => `operation-0000000000-${++serial}`,
  backendHasCapability: (backend, name) => !!backend && backend.capabilities.includes(name),
  fetch: async (url, opts) => { calls.push({ url, opts }); return fetcher(url, opts); },
});
vm.runInContext(between("async function api(", "function wsUrl(") + "\n" +
  between("const modalStack =", "/* ---- modal copy ----"), context);
const tick = () => new Promise(resolve => setImmediate(resolve));
const advance = async ms => {
  const until = now + ms;
  for (;;) {
    let due = null;
    for (const entry of timers)
      if (entry[1].at <= until && (!due || entry[1].at < due[1].at)) due = entry;
    if (!due) break;
    now = due[1].at;
    timers.delete(due[0]);
    due[1].fn();
    await tick();
  }
  now = until;
  await tick();
};
/* top-level const stays lexical to the script, so read it by name */
const DELAY = vm.runInContext("CANCEL_DIALOG_DELAY", context);
const waitOut = () => advance(DELAY);
const controlReads = () => calls.filter(call => !call.opts.method && call.url.includes("/operations/"));
(async () => {
  // Work that answers inside the delay is never interrupted by a dialog.
  calls = []; reply = deferred();
  fetcher = () => reply.promise;
  const quick = context.api(0, "work", { method: "POST", body: {}, operation: "Preparing task" });
  await advance(DELAY - 1);
  assert.equal(root.children.length, 0, "no dialog while the wait is still short");
  reply.resolve(response(200, { ok: true }));
  assert.deepEqual(await quick, { ok: true });
  await advance(DELAY * 2);
  assert.equal(root.children.length, 0, "a finished operation never opens its dialog");
  assert.equal(calls.length, 1, "and asks for no operation state");
  assert.equal(document.activeElement, opener, "nor does it take the focus");

  for (const dismissal of ["button", "escape", "backdrop"]) {
    calls = []; reply = deferred(); let discarded = false;
    fetcher = (url, opts) => opts.method === "DELETE" ? response(200, { state: "cancelling" }) : reply.promise;
    const job = context.api(0, "work", { method: "POST", body: {}, operation: "Preparing task", cancelClose: () => { discarded = true; } });
    const failure = job.catch(error => error);
    assert.equal(root.querySelector(".operation-cancel"), null, "the offer waits out the delay");
    await waitOut();
    const cancel = root.querySelector(".operation-cancel");
    assert.equal(cancel.disabled, false);
    assert.equal(document.activeElement, cancel);
    assert.equal(controlReads().length, 1, "a dialog opening mid-operation reads its state at once");
    if (dismissal === "button") await cancel.onclick();
    if (dismissal === "escape") document.dispatchEvent(new FakeEvent("keydown", { key: "Escape" }));
    if (dismissal === "backdrop") root.querySelector(".modal-backdrop").dispatchEvent(new FakeEvent("mousedown"));
    await tick();
    const cancels = calls.filter(call => call.opts.method === "DELETE");
    assert.equal(cancels.length, 1, dismissal);
    assert.equal(cancel.disabled, true);
    assert.equal(cancel.isConnected, true, "keep progress until the backend drains cleanup");
    assert.equal(discarded, false);
    const sentId = calls[0].opts.headers["X-Puppy-Operation"];
    assert(cancels.every(call => call.url.endsWith(sentId)));
    reply.resolve(response(409, { error: "Operation cancelled", cancelled: true }));
    assert.equal((await failure).cancelled, true);
    assert.equal(discarded, true);
    assert.equal(root.children.length, 0);
    assert.equal(document.activeElement, opener);
  }
  // A commit that wins the race cannot be presented as a successful abort.
  reply = deferred(); calls = [];
  fetcher = (url, opts) => opts.method === "DELETE" ? response(200, { state: "finishing" }) : reply.promise;
  const finishing = context.api(0, "work", { method: "POST", operation: "Applying task" });
  await waitOut();
  const close = root.querySelector(".operation-cancel"); await close.onclick();
  assert.equal(close.textContent, "Close");
  assert(root.querySelector(".modal-copy").textContent.includes("can no longer be cancelled"));
  await close.onclick();
  assert.equal(root.children.length, 0);
  reply.resolve(response(200, { applied: true }));
  assert.equal((await finishing).applied, true);

  // A commit reached during the wait is already named when the dialog opens.
  reply = deferred(); calls = [];
  fetcher = (url, opts) => opts.method === "POST" ? reply.promise : response(200, { state: "finishing" });
  const committed = context.api(0, "work", { method: "POST", operation: "Applying task" });
  await waitOut();
  const settled = root.querySelector(".operation-cancel");
  assert.equal(settled.textContent, "Close");
  assert.equal(settled.disabled, false);
  await advance(5000);
  assert.equal(controlReads().length, 1, "a settled operation is not polled again");
  reply.resolve(response(200, { applied: true }));
  assert.equal((await committed).applied, true);
  assert.equal(root.children.length, 0);

  // A failed cancellation stays visible and retryable; it never hides a job.
  reply = deferred(); let failCancel = true;
  fetcher = (url, opts) => opts.method === "DELETE" ? response(failCancel ? 503 : 200,
    failCancel ? { error: "offline" } : { state: "cancelling" }) : reply.promise;
  const retrying = context.api(0, "work", { method: "POST", operation: "Copying" }).catch(error => error);
  await waitOut();
  const retry = root.querySelector(".operation-cancel"); await retry.onclick();
  assert.equal(retry.disabled, false);
  assert.equal(root.querySelector(".form-error").classList.contains("hidden"), false);
  failCancel = false; await retry.onclick();
  // An actual cleanup failure is an error even after cancellation was accepted.
  reply.resolve(response(500, { error: "cleanup failed" }));
  const failed = await retrying;
  assert.equal(failed.message, "cleanup failed");
  assert.equal(failed.cancelled, false);

  context.state.backends = [{ id: 7, capabilities: [] }];
  fetcher = () => response(200, { ok: true }); calls = [];
  await context.api(7, "work", { method: "POST", operation: "Preparing" });
  await waitOut();
  assert.equal(root.children.length, 0);
  assert.equal(calls[0].opts.headers["X-Puppy-Operation"], undefined, "negotiate older nodes");

  // A read-only wait borrows the same delay and aborts its own request.
  calls = []; reply = deferred(); let readSignal = null;
  fetcher = (url, opts) => { readSignal = opts.signal; return reply.promise; };
  const reading = context.api(0, "check", { readOperation: "Verifying listener" }).catch(error => error);
  await advance(DELAY - 1);
  assert.equal(root.querySelector(".read-cancel"), null, "a quick read shows nothing either");
  await advance(1);
  const stop = root.querySelector(".read-cancel");
  assert.equal(document.activeElement, stop);
  stop.onclick();
  assert.equal(root.children.length, 0);
  assert.equal(readSignal.aborted, true);
  reply.resolve(response(200, { ok: true }));
  assert.equal((await reading).cancelled, true);

  // Explicit abort still works when a request also carries a deadline.
  const controller = new AbortController();
  fetcher = (url, opts) => new Promise((resolve, reject) => {
    opts.signal.addEventListener("abort", () => reject(new Error("abort")));
  });
  const aborting = context.api(0, "search", { signal: controller.signal, timeoutMs: 1000 }).catch(error => error);
  controller.abort();
  assert.equal((await aborting).cancelled, true);
  context.xIcon = () => el("svg", "stop-icon");
  context.refreshIcon = () => el("svg", "refresh-icon");
  vm.runInContext("globalThis.LoadingView = class {" +
    between("  canStopLoading() {", "  clearDead() {\n    const dead = this.root.querySelector(\".br-dead\")") + "};", context);
  const view = new context.LoadingView();
  Object.assign(view, { tab: { bid: 0 }, root: el("div"), reloadBtn: el("button"),
    loadingTimer: null, resetCursor() {}, queueCursor() {} });
  view.setLoading(true);
  assert.equal(view.reloadBtn.getAttribute("aria-label"), "Stop loading");
  assert.equal(view.canStopLoading(), true);
  view.setLoading(false);
  assert.equal(view.reloadBtn.getAttribute("aria-label"), "Reload");
  view.tab.bid = 7; view.setLoading(true);
  assert.equal(view.canStopLoading(), false, "older nodes never receive unknown loading commands");
  view.setLoading(false);

  console.log("PASS: deferred dialogs, Cancel/Escape/backdrop, cleanup waits, commit race, retry, errors, capability gating and read aborts");
})().catch(error => { console.error(error); process.exitCode = 1; });
