"use strict";
/* The notification box behind the footer's tray, against the fake DOM: how it
   opens and closes on the sidebar's slide, what it reads and what it leaves
   to the stream, the rows it draws in the footer's dot column, a repeat
   counted where it stands, a new arrival entering at the top without
   rebuilding the rows below it, and the revision guard that keeps an HTTP
   answer and the stream from rewinding each other. No browser, engine,
   network or quota. */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { FakeDocument } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");

function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}

const NOW = 1_757_600_000;   // seconds; the stamps below are relative to it
let nextRevision = 1;
function payload(items, revision = nextRevision++) {
  return { type: "notices", state_topic: "notices", state_revision: revision,
    runtime_id: "run-1", limit: 100, items };
}
function notice(id, text, tone, count = 1, at = NOW - id, first = at) {
  return { id, text, tone, count, first_at: first, at };
}

function consoleFor(options = {}) {
  const document = new FakeDocument();
  const el = (tag, cls = "", text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const foot = el("div", "side-foot");
  document.body.appendChild(foot);
  const box = el("div", "foot-notices");
  box.id = "foot-notices";
  box.hidden = true;
  foot.appendChild(box);
  const button = el("button", "icon-btn");
  button.id = "btn-notices";
  button.setAttribute("aria-expanded", "false");
  foot.appendChild(button);
  const state = {
    runtimeId: "run-1", stateStreamReady: { 0: options.streamActive !== false },
    stateStreamRuntime: {}, stateStreamRevisions: {}, clockFormat: "24h",
    notices: options.notices === undefined ? null : options.notices,
  };
  const calls = { reads: 0, slides: [], layers: [], released: 0, hostClosed: 0, aimed: 0 };
  const context = vm.createContext({
    document, el, state, console, Date, Math, Number, Array, JSON, Set, Map, Promise,
    Object, String, Intl, isNaN, parseFloat, setTimeout, clearTimeout,
    $: id => document.getElementById(id),
    navigation: { layer: (dismiss, reopen) => {
      calls.layers.push({ dismiss, reopen });
      return () => { calls.released++; };
    } },
    setDisclosureCollapsed: (body, collapsed, animate, done) => {
      calls.slides.push({ collapsed, animate });
      body.hidden = collapsed;
      if (done) done();
    },
    refreshDisclosureHeight: () => { calls.aimed++; },
    nodeStateStreamActive: bid => state.stateStreamReady[bid] === true,
    closeHostPanel: () => { calls.hostClosed++; },
    clearNodeStateRevisions: () => {},
    location: { reload: () => assert.fail("a matching runtime never reloads") },
    api: async (bid, route, opts) => {
      assert.equal(bid, 0);
      assert.equal(route, "notices");
      assert.equal(opts.method, undefined, "the box only ever reads");
      calls.reads++;
      const answer = options.read;
      if (answer instanceof Error) throw answer;
      return typeof answer === "function" ? answer() : answer;
    },
  });
  vm.runInContext([
    between("function serverClockOptions(", "/* Unattended-update schedules"),
    between("/* One stamp for \"when\"", "function fmtTokens("),
    between("function acceptStateSnapshot(", "/* Every prompt box on that node"),
    between("/* The footer's one dot column.", "function renderFootEngines()"),
    between("/* ================= notifications ================= */",
            '\n$("btn-notices").onclick'),
  ].join("\n"), context);
  return { context, document, state, calls, foot, box, button,
    rows: () => box.querySelectorAll(".notice-row"),
    panel: vm.runInContext("noticesPanel", context) };
}

const settle = () => new Promise(resolve => setImmediate(resolve));
const text = node => (node ? node.textContent : "");
const inDotColumn = row => {
  const lead = row.children[0];
  return !!lead && lead.className === "foot-ico" && lead.children.length === 1 &&
    lead.children[0].classList.contains("gdot");
};

/* ---- opening reads the list only when the stream is not there to bring it */
async function openingAndReading() {
  const read = payload([notice(2, "Session deleted", "ok"),
                        notice(1, "NAS: Could not save", "bad", 3, NOW - 10, NOW - 60)]);
  const app = consoleFor({ streamActive: true, read });
  app.context.openNoticesPanel();
  assert.equal(app.panel.open, true);
  assert.equal(app.button.getAttribute("aria-expanded"), "true");
  assert.ok(app.foot.classList.contains("notices-open"),
    "the engine stats give up height while the box is open");
  assert.deepEqual(app.calls.slides.at(-1), { collapsed: false, animate: true });
  assert.equal(app.calls.hostClosed, 1, "the footer holds one box at a time");
  assert.equal(app.calls.layers.length, 1, "the box is a Back-closable layer");
  assert.equal(text(app.box.querySelector(".host-empty")), "Reading…",
    "with no list yet, the box says it is reading");
  await settle();
  assert.equal(app.calls.reads, 1, "a live stream without a list yet still needs one read");
  assert.equal(app.rows().length, 2);
  const [first, second] = app.rows();
  assert.equal(text(first.querySelector(".notice-text")), "Session deleted");
  assert.equal(first.querySelector(".toast-count"), null, "a single arrival shows no count");
  assert.ok(inDotColumn(first) && first.querySelector(".gdot").classList.contains("ok"));
  assert.equal(first.querySelector(".gdot").getAttribute("aria-label"), "completed");
  assert.equal(text(first.querySelector(".notice-time")), app.context.fmtStamp(NOW - 2));
  assert.equal(text(second.querySelector(".toast-count")), "3 ×");
  assert.ok(second.querySelector(".gdot").classList.contains("bad"));
  assert.equal(text(second.querySelector(".notice-time")), app.context.fmtStamp(NOW - 10));
  assert.ok(!first.title && !second.title, "a row carries no tooltip");
  assert.equal(text(app.box.querySelector(".host-sec-title")), "Notifications");
  assert.equal(text(app.box.querySelector(".host-node-note")), "2");
  assert.ok(!app.box.querySelector(".notices-head").title, "nor does the head");
  assert.equal(app.box.querySelector(".host-empty"), null);
  assert.ok(!app.rows().some(row => row.classList.contains("notice-new")),
    "the first paint does not animate every row in");
  assert.equal(app.state.stateStreamRevisions["0:notices"], read.state_revision,
    "the read is accepted as the stream revision it carries");

  app.context.closeNoticesPanel();
  assert.equal(app.panel.open, false);
  assert.equal(app.button.getAttribute("aria-expanded"), "false");
  assert.ok(!app.foot.classList.contains("notices-open"));
  assert.deepEqual(app.calls.slides.at(-1), { collapsed: true, animate: true });
  assert.equal(app.box.children.length, 0, "the content is let go once the close finished");
  assert.equal(app.panel.rows.size, 0);
  assert.equal(app.calls.released, 1, "closing releases its history layer");
  assert.equal(app.state.notices.items.length, 2, "the list itself is kept for the next opening");

  // With the list already on the page and the stream live, opening reads nothing.
  app.context.openNoticesPanel();
  await settle();
  assert.equal(app.calls.reads, 1);
  assert.equal(app.rows().length, 2);
  app.context.closeNoticesPanel();

  // Without the stream, opening re-reads even though a list is on the page.
  app.state.stateStreamReady[0] = false;
  app.context.openNoticesPanel();
  await settle();
  assert.equal(app.calls.reads, 2);
  app.context.closeNoticesPanel();
  assert.equal(app.calls.released, 3);
}

/* ---- an empty history and a read that fails --------------------------- */
async function emptyAndFailed() {
  const empty = consoleFor({ read: payload([]) });
  empty.context.openNoticesPanel();
  await settle();
  assert.equal(text(empty.box.querySelector(".host-node-note")), "0",
    "an empty history is the head alone, its count saying so");
  assert.equal(empty.box.querySelector(".host-empty"), null, "no line explains a zero");
  assert.equal(empty.box.querySelector(".notices-list").children.length, 0);
  assert.equal(empty.rows().length, 0);
  empty.context.closeNoticesPanel();

  const failed = consoleFor({ read: new Error("network error") });
  failed.context.openNoticesPanel();
  await settle();
  assert.equal(text(failed.box.querySelector(".host-empty")), "network error",
    "a failed first read says so in the box, never in a toast");
  /* the stream then brings the list, and the box takes it */
  failed.context.applyNotices(payload([notice(1, "Copied", "ok")]));
  assert.equal(failed.rows().length, 1);
  assert.equal(failed.box.querySelector(".host-empty"), null);
  failed.context.closeNoticesPanel();
}

/* ---- live updates keep the rows already on the page ------------------- */
async function liveUpdates() {
  const items = [notice(3, "Copied", "ok"), notice(2, "Session deleted", "ok"),
                 notice(1, "NAS: Could not save", "bad")];
  const app = consoleFor({ notices: { items, limit: 100 } });
  app.state.stateStreamRevisions["0:notices"] = 5;
  app.context.openNoticesPanel();
  await settle();
  assert.equal(app.calls.reads, 0);
  const before = [...app.rows()];
  assert.equal(before.length, 3);

  // A repeat of the newest entry counts up on its own row and bumps the count.
  app.context.ingestNoticesPayload(payload(
    [notice(3, "Copied", "ok", 2), items[1], items[2]], 6));
  let rows = [...app.rows()];
  assert.equal(rows.length, 3);
  assert.equal(rows[0], before[0], "the row keeps its identity");
  assert.equal(text(rows[0].querySelector(".toast-count")), "2 ×");
  assert.ok(rows[0].querySelector(".toast-count").classList.contains("toast-bump"));
  assert.ok(!rows[0].classList.contains("notice-new"));

  // A new notice enters at the top; the three below it are the same nodes.
  app.context.ingestNoticesPayload(payload(
    [notice(4, "Draft saved", "ok"), notice(3, "Copied", "ok", 2), items[1], items[2]], 7));
  rows = [...app.rows()];
  assert.equal(rows.length, 4);
  assert.equal(text(rows[0].querySelector(".notice-text")), "Draft saved");
  assert.ok(rows[0].classList.contains("notice-new"), "an arrival enters like its toast");
  assert.deepEqual(rows.slice(1), before);
  assert.equal(text(app.box.querySelector(".host-node-note")), "4");

  // The list is pruned from the far end: the oldest row goes, nothing else moves.
  app.context.ingestNoticesPayload(payload(
    [notice(5, "Session renamed", "ok"), notice(4, "Draft saved", "ok"),
     notice(3, "Copied", "ok", 2), items[1]], 8));
  rows = [...app.rows()];
  assert.equal(rows.length, 4);
  assert.equal(text(rows[3].querySelector(".notice-text")), "Session deleted");
  assert.equal(app.panel.rows.has(1), false);
  assert.equal(app.panel.rows.size, 4);

  // An older revision - a read that lost the race with the stream - is ignored.
  app.context.ingestNoticesPayload(payload([notice(1, "stale", "info")], 7));
  assert.equal(app.rows().length, 4);
  assert.equal(app.state.notices.items[0].text, "Session renamed");
  // The stream path has already had its revision accepted, so it applies as given.
  app.context.applyNotices(payload([notice(6, "Instance settings saved", "ok")], 9));
  assert.equal(app.rows().length, 1);
  assert.equal(text(app.rows()[0].querySelector(".notice-text")), "Instance settings saved");

  // Rows the controller would never send are left out rather than drawn wrong.
  app.context.applyNotices({ type: "notices", limit: 100, items: [
    notice(7, "kept", "warn"), { id: 8, text: "", tone: "ok", count: 1, at: NOW, first_at: NOW },
    { id: 9, text: "wrong tone", tone: "error", count: 1, at: NOW, first_at: NOW },
    { id: 10, text: "no count", tone: "ok", at: NOW, first_at: NOW },
    { id: "11", text: "string id", tone: "ok", count: 1, at: NOW, first_at: NOW },
    null, "text",
  ] });
  assert.equal(app.rows().length, 1);
  assert.equal(text(app.rows()[0].querySelector(".notice-text")), "kept");
  assert.ok(app.rows()[0].querySelector(".gdot").classList.contains("warn"));
  app.context.applyNotices({ type: "notices", items: [] });
  assert.equal(app.rows().length, 0);
  assert.equal(app.box.querySelector(".host-empty"), null, "an emptied list draws no line");
  assert.equal(app.box.querySelector(".notices-list").children.length, 0);
  assert.equal(text(app.box.querySelector(".host-node-note")), "0");
  app.context.applyNotices({ type: "notices" });
  assert.equal(app.state.notices.items.length, 0, "a payload without items changes nothing");
  app.context.closeNoticesPanel();

  // Updates while the box is closed only keep the list; nothing is drawn.
  app.context.applyNotices(payload([notice(12, "Later", "busy")], 10));
  assert.equal(app.box.children.length, 0);
  assert.equal(app.state.notices.items[0].tone, "busy");
  app.context.openNoticesPanel();
  assert.ok(app.rows()[0].querySelector(".gdot").classList.contains("busy"));
  assert.equal(app.rows()[0].querySelector(".gdot").getAttribute("aria-label"), "in progress");
  app.context.closeNoticesPanel();
}

/* ---- the tray toggles, and every tone has a name for a screen reader --- */
function toggling() {
  const app = consoleFor({ notices: { items: [], limit: 100 } });
  app.context.toggleNoticesPanel();
  assert.equal(app.panel.open, true);
  app.context.toggleNoticesPanel();
  assert.equal(app.panel.open, false);
  app.context.closeNoticesPanel();
  assert.equal(app.calls.released, 1, "closing a closed box releases nothing twice");
  const labels = vm.runInContext("NOTICE_TONE_LABELS", app.context);
  assert.deepEqual(Object.keys(labels).sort(), ["bad", "busy", "info", "ok", "warn"],
    "the toast's whole tone vocabulary, and nothing else");
}

(async () => {
  await openingAndReading();
  await emptyAndFailed();
  await liveUpdates();
  toggling();
  console.log("PASS: notification box opening/closing on the footer's slide, its one " +
    "read when the stream cannot bring the list, rows in the dot column with counts " +
    "and stamps, live arrivals and repeats keeping existing rows, the revision guard " +
    "and the tone vocabulary");
})().catch(error => { console.error(error); process.exit(1); });
