/* Run with node tests/toast_ui_test.js. No browser or engine required.
   The console's one notice surface, against the fake DOM: the shared grammar
   every message is held to, the status tone vocabulary, the two lives, the
   folding of an identical notice into a counted row that renews its own hide
   timer instead of stacking a second copy, and the report every shown notice
   sends to the controller's history - in order, one at a time, kept through
   an outage and dropped only on a refusal. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  if (start < 0 || end < 0) throw new Error("slice not found: " + from);
  return source.slice(start, end);
};

const document = new FakeDocument();
const host = document.createElement("div");
host.id = "toasts";
document.body.appendChild(host);

/* One clock for Date.now and the timers, so a life can be spent exactly. */
let now = 1000000, nextTimer = 0;
const timers = new Map();
/* The controller the reports go to: every call is kept, and `answer` decides
   whether the next one is taken, refused for good or lost on the way. */
const reports = [];
const ingested = [];
let answer = () => ({ type: "notices", items: [] });
const context = vm.createContext({
  document,
  Date: { now: () => now },
  setTimeout: (fn, ms) => {
    const id = ++nextTimer;
    timers.set(id, { fn, at: now + Math.max(0, Number(ms) || 0) });
    return id;
  },
  clearTimeout: id => { timers.delete(id); },
  api: async (bid, route, options) => {
    assert.equal(bid, 0, "the history lives on the controller");
    assert.equal(route, "notices");
    assert.equal(options.method, "POST");
    assert.equal(options.noAuthRedirect, true, "a report never sends the reader to sign in");
    reports.push({ ...options.body });
    return answer(options.body);
  },
  ingestNoticesPayload: payload => { ingested.push(payload); },
});
const advance = ms => {
  const until = now + ms;
  for (;;) {
    let due = null;
    for (const entry of timers)
      if (entry[1].at <= until && (!due || entry[1].at < due[1].at)) due = entry;
    if (!due) break;
    now = due[1].at;
    timers.delete(due[0]);
    due[1].fn();
  }
  now = until;
};
vm.runInContext([
  between("const $ = (id)", "/* Close buttons"),
  between("const TOAST_SWIPE_INTENT_PX", "/* Clipboard.writeText is unavailable"),
].join("\n"), context);
/* top-level const stays lexical to the script, so read those by name */
const read = expression => vm.runInContext(expression, context);
const { toast, toastText, toastTone } = context;
const TOAST_SHORT = read("TOAST_SHORT"), TOAST_LONG = read("TOAST_LONG");

const rows = () => host.children;
const only = () => {
  assert.equal(rows().length, 1, "one notice was expected");
  return rows()[0];
};
/* end every live notice the way its own timer would, so no stale identity
   survives into the next section of this check */
const clear = () => {
  advance(TOAST_LONG * 2);
  for (const row of [...rows()]) row.remove();
  read("liveToasts").clear();
  timers.clear();
};
const touch = (node, type, init) =>
  node.dispatchEvent(new FakeEvent(type, {
    pointerType: "touch", isPrimary: true, pointerId: 1, timeStamp: now, ...init,
  }));

// ---- the grammar: one shape, whoever wrote the words -------------------
assert.equal(toastText("  Session   deleted \n "), "Session deleted");
assert.equal(toastText("Backend unavailable."), "Backend unavailable",
  "a notice never ends in a full stop");
assert.equal(toastText("Reading logs..."), "Reading logs...",
  "an ellipsis is not a full stop");
assert.equal(toastText("Saved·done"), "Saved · done", "one spacing for the separator");
assert.equal(toastText("Page dialog accepted ·"), "Page dialog accepted",
  "an empty last clause leaves no dangling separator");
assert.equal(toastText("backup or restore in progress"), "Backup or restore in progress");
assert.equal(toastText("NAS: draft cannot exceed 4000 characters"),
  "NAS: Draft cannot exceed 4000 characters", "the subject keeps its own capital");
assert.equal(toastText("puppy.db is locked"), "puppy.db is locked",
  "a leading file name keeps the spelling the reader has to type back");
assert.equal(toastText("https://nas.test/api answered 500"),
  "https://nas.test/api answered 500", "a URL is not a subject");
assert.equal(toastText(""), "");
assert.equal(toastText(null), "");

// ---- the tone vocabulary ----------------------------------------------
assert.deepEqual(["ok", "warn", "bad", "busy", "info"].map(toastTone),
  ["ok", "warn", "bad", "busy", "info"]);
assert.equal(toastTone("error"), "bad", "a backend still saying error is understood");
assert.equal(toastTone("err"), "bad");
assert.equal(toastTone("stale"), "info", "an unknown word paints no class of its own");
assert.equal(toastTone(undefined), "info");

toast("Session deleted", "ok");
assert.equal(only().className, "toast ok");
assert.equal(only().textContent, "Session deleted");
clear();
toast("NAS: could not save", "error");
assert.equal(only().className, "toast bad");
assert.equal(only().textContent, "NAS: Could not save");
clear();
toast("   ");
assert.equal(rows().length, 0, "an empty notice is not a row");

// ---- two lives ---------------------------------------------------------
assert.equal(TOAST_SHORT, 4200);
assert.equal(TOAST_LONG, 7000);
toast("Backend added", "ok");
advance(TOAST_SHORT - 1);
assert.equal(rows().length, 1);
advance(1);
assert.equal(rows().length, 0, "the default life ends the notice");
toast("Instance settings saved", "ok", TOAST_LONG);
advance(TOAST_SHORT);
assert.equal(rows().length, 1, "the long life outlasts the short one");
advance(TOAST_LONG - TOAST_SHORT);
assert.equal(rows().length, 0);

// ---- repeats fold instead of stacking ----------------------------------
toast("Could not copy to the clipboard", "bad");
const folded = only();
assert.equal(folded.children.length, 1, "the first notice carries no count");
toast("Could not copy to the clipboard", "bad");
assert.equal(rows().length, 1, "an identical notice never stacks a second row");
assert.equal(rows()[0], folded, "the live row is counted where it already sits");
assert.equal(folded.textContent, "2 ×Could not copy to the clipboard");
assert.equal(folded.querySelector(".toast-count").textContent, "2 ×");
assert.ok(folded.querySelector(".toast-count").classList.contains("toast-bump"));
toast("Could not copy to the clipboard", "bad");
assert.equal(folded.querySelector(".toast-count").textContent, "3 ×");
/* the normalised text is the identity: differently written, same notice */
toast("could not copy to the clipboard.", "bad");
assert.equal(rows().length, 1);
assert.equal(folded.querySelector(".toast-count").textContent, "4 ×");
/* a different tone is a different notice, and so is different wording */
toast("Could not copy to the clipboard", "warn");
toast("Could not copy the selection", "bad");
assert.equal(rows().length, 3);
clear();

// ---- a repeat renews the hide timer ------------------------------------
toast("Draft is still syncing · wait for the session to reconnect", "bad");
advance(4000);
toast("Draft is still syncing · wait for the session to reconnect", "bad");
advance(TOAST_SHORT - 1);
assert.equal(rows().length, 1, "the repeat renewed the life from when it arrived");
advance(1);
assert.equal(rows().length, 0);
toast("Workspace sync is still pending", "warn", TOAST_LONG);
advance(1000);
toast("Workspace sync is still pending", "warn", TOAST_SHORT);
advance(TOAST_LONG - 1001);
assert.equal(rows().length, 1, "a shorter repeat can never cut a life short");
advance(1);
assert.equal(rows().length, 0);
/* once gone, the same notice starts a fresh row rather than resuming a count */
toast("Workspace sync is still pending", "warn");
assert.equal(only().children.length, 1);
assert.equal(only().textContent, "Workspace sync is still pending");
clear();

// ---- a held toast keeps its life; a swipe ends it ----------------------
toast("Not connected · wait for the session to reconnect", "bad", TOAST_LONG);
const held = only();
touch(held, "pointerdown", { clientX: 0, clientY: 0 });
advance(TOAST_LONG * 3);
assert.equal(rows().length, 1, "a finger on the notice holds it open");
touch(held, "pointerup", { clientX: 0, clientY: 0 });
advance(799);
assert.equal(rows().length, 1, "a released notice stays legible for a moment");
advance(1);
assert.equal(rows().length, 0);

toast("Not connected · wait for the session to reconnect", "bad", TOAST_LONG);
const swiped = only();
touch(swiped, "pointerdown", { clientX: 0, clientY: 0 });
touch(swiped, "pointermove", { clientX: 100, clientY: 0 });
touch(swiped, "pointerup", { clientX: 100, clientY: 0 });
advance(400);
assert.equal(rows().length, 0, "a rightward swipe dismisses the notice");
toast("Not connected · wait for the session to reconnect", "bad", TOAST_LONG);
assert.equal(only().children.length, 1,
  "a swiped notice releases its slot, so the next one is not counted onto it");

// ---- every shown notice is reported to the history, in order -----------
const settle = () => new Promise(resolve => setImmediate(resolve));
const outbox = () => read("noticeOutbox");
const refusal = status => Object.assign(new Error(`HTTP ${status}`), { status });
(async () => {
  await settle();
  assert.equal(outbox().length, 0, "everything shown so far has been reported");
  assert.deepEqual(reports[0], { text: "Session deleted", tone: "ok" },
    "the report carries the normalised text and tone the toast showed");
  assert.deepEqual(reports[1], { text: "NAS: Could not save", tone: "bad" });
  assert.ok(!reports.some(report => report.text === ""), "an empty notice is not reported");
  const repeats = reports.filter(report =>
    report.text === "Could not copy to the clipboard" && report.tone === "bad");
  assert.equal(repeats.length, 4, "a folded repeat is reported like the first arrival");
  assert.equal(ingested.length, reports.length, "each answer is the list, taken as published");
  reports.length = 0;
  ingested.length = 0;
  clear();

  // A controller that cannot be reached keeps the report for later; nothing
  // is dropped, nothing is reordered, and no toast is raised about it.
  answer = () => { throw new Error("network error"); };
  toast("Backend Workshop: Could not reach it", "bad");
  await settle();
  assert.equal(reports.length, 1);
  assert.equal(outbox().length, 1, "a lost report waits");
  assert.equal(rows().length, 1, "no notice is raised about a notice");
  toast("Session renamed", "ok");
  toast("Session renamed", "ok");
  await settle();
  assert.equal(reports.length, 1, "an outage is not retried on every toast");
  assert.deepEqual(Array.from(outbox(), entry => entry.text),
    ["Backend Workshop: Could not reach it", "Session renamed", "Session renamed"]);
  answer = () => ({ type: "notices", items: [] });
  advance(read("NOTICE_RETRY_MS") - 1);
  await settle();
  assert.equal(reports.length, 1, "the retry waits its full interval");
  advance(1);
  await settle();
  assert.equal(outbox().length, 0, "the retry sends everything that waited, in order");
  assert.deepEqual(reports.slice(1).map(report => report.text),
    ["Backend Workshop: Could not reach it", "Session renamed", "Session renamed"]);
  reports.length = 0;
  clear();

  // A reconnect flushes at once instead of waiting for the timer.
  answer = () => { throw new Error("network error"); };
  toast("Draft saved", "ok");
  await settle();
  assert.equal(outbox().length, 1);
  answer = () => ({ type: "notices", items: [] });
  context.flushNotices();
  await settle();
  assert.equal(outbox().length, 0);
  assert.equal(reports.length, 2);
  assert.equal(timers.size, 1, "the retry timer is cancelled; only the toast's own remains");
  reports.length = 0;
  clear();

  // A reconnect with nothing waiting is not a flush: it must not leave a
  // finished flush in place that every later notice would queue behind.
  assert.equal(context.flushNotices(), null);
  await settle();
  assert.equal(read("noticeFlush"), null);
  toast("Session archived", "ok");
  await settle();
  assert.equal(reports.length, 1, "the next notice still goes out at once");
  assert.equal(outbox().length, 0);
  reports.length = 0;
  clear();

  // A list the box cannot draw does not stall the reports behind it.
  context.ingestNoticesPayload = () => { throw new Error("cannot draw"); };
  toast("Draft discarded", "ok");
  toast("Draft discarded again", "ok");
  await settle();
  assert.equal(reports.length, 2);
  assert.equal(outbox().length, 0);
  assert.equal(read("noticeFlush"), null);
  context.ingestNoticesPayload = payload => { ingested.push(payload); };
  reports.length = 0;
  clear();

  // A refusal is final; a busy controller, a session that must sign in again
  // and a timeout are not.
  for (const status of [400, 404, 413]) {
    answer = () => { throw refusal(status); };
    toast(`Refused ${status}`, "info");
    await settle();
    assert.equal(outbox().length, 0, `a ${status} drops the report`);
    assert.equal(reports.length, 1);
    reports.length = 0;
    clear();
  }
  for (const status of [401, 408, 429, 500, 503]) {
    answer = () => { throw refusal(status); };
    toast(`Kept ${status}`, "info");
    await settle();
    assert.equal(outbox().length, 1, `a ${status} keeps the report`);
    answer = () => ({ type: "notices", items: [] });
    advance(read("NOTICE_RETRY_MS"));
    await settle();
    assert.equal(outbox().length, 0);
    assert.equal(reports.length, 2);
    reports.length = 0;
    clear();
  }

  // The outbox is bounded: the oldest waiting reports make room.
  answer = () => { throw new Error("network error"); };
  for (let index = 0; index < 130; index++) toast(`Burst ${index}`, "info");
  await settle();
  assert.equal(outbox().length, read("NOTICE_OUTBOX"));
  assert.equal(outbox()[0].text, "Burst 30");
  assert.equal(outbox()[outbox().length - 1].text, "Burst 129");
  answer = () => ({ type: "notices", items: [] });
  context.flushNotices();
  await settle();
  assert.equal(outbox().length, 0);

  console.log("PASS: toast grammar, tone vocabulary, two lives, folded repeats with " +
    "renewed timers, swipe/hold lifetimes, and every shown notice reported to the " +
    "history in order through outages, refusals and reconnects");
})().catch(error => { console.error(error); process.exit(1); });
