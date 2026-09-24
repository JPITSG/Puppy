/* Run with node tests/tool_result_ui_test.js. A tool result belongs to its
   call's card, folded away until the reader opens it - including when the
   window it arrived in started after that call. No browser or engine. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const {FakeDocument, FakeElement} = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
};
const document = new FakeDocument();
const icon = () => document.createElement("svg");
let clockNow = 2000000, sessionMeta = null;
const context = vm.createContext({
  document, Map, console, choiceSvg: icon, toolIconNode: icon,
  Element: FakeElement, window: {addEventListener() {}}, Date: {now: () => clockNow},
  findSessionMeta: () => sessionMeta, fmtTime: () => "12:00", fmtTokens: String,
  xIcon: icon, checkIcon: icon, promptSpinnerNode: icon,
  linkifyInto: (node, text) => { node.textContent = text; return node; },
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("const tips =", "/* ================= state ================= */"),
  between("const sessionActivityAnchors =", "function reconcileRemoteState("),
  between("function formatSessionActivity(", "function formatUptime("),
  between("function displayValue(", "function toolIconNode("),
  between("function toolStateInto(", "/* A side question"),
  between("/* Task endings stay visible", "const ATTACHMENT_PREVIEW_TYPES"),
  "class View {",
  between("  findEventNode(seq)", "  /* Dividers mark"),
  between("  buildEventNode(ev)", "  /* live streaming bubble */"),
  between("  atBottom() {", "  /* ---- outgoing ---- */"),
  "} globalThis.View = View; globalThis.tips = tips;",
].join("\n"), context);

/* one row is taller than the 160px of slack atBottom allows, so a single box
   arriving is the difference between following the tail and losing it */
const ROW = 200, VIEWPORT = 300;
function view() {
  const v = new context.View();
  v.toolCards = {};
  v.backgroundTaskUpdates = new Map();
  v.orphanResults = new Map();
  v.inner = document.createElement("div");
  document.body.appendChild(v.inner);
  v.newestSeq = 0;
  /* Nothing here lays anything out, so the scroller is modelled: every box in
     the transcript is one row tall wherever it sits, so a card that grows in
     place moves the foot exactly as an appended node does. The real atBottom,
     scrollBottom and followingTail run against it. */
  const rows = () => v.inner.querySelectorAll(
    ".tool-card,.task-update,.msg,.tool-card.open .tb-label").length;
  v.scroll = {clientHeight: VIEWPORT, scrollTop: 0,
    get scrollHeight() { return rows() * ROW; }};
  return v;
}
const call = (id, seq) => ({seq, kind: "tool_use", data: {
  tool: "file_change", tool_use_id: id, input: {changes: []},
}});
const result = (id, seq, extra = {}) => ({seq, kind: "tool_result", data: {
  tool_use_id: id, content: "add: /project/notes.md", is_error: false, ...extra,
}});
const cards = v => v.inner.querySelectorAll(".tool-card");
/* the call's own input is the first pre in the body; the result is the last */
const resultText = card => {
  const bodies = card.querySelectorAll(".tool-body pre");
  return bodies.length ? bodies[bodies.length - 1].textContent : "";
};

/* The ordinary pair: one card, carrying the result, closed until opened. */
{
  const v = view();
  [call("a", 1), result("a", 2)].forEach(ev => v.renderEvent(ev, true));
  assert.equal(cards(v).length, 1, "the result must not add a second card");
  const card = v.toolCards.a;
  assert.ok(!card.classList.contains("open"), "a paired card stays closed");
  assert.equal(resultText(card), "add: /project/notes.md");
  assert.equal(card.querySelector(".t-name").textContent, "file change");
}

/* A window that starts after the call: the result stands alone, still folded
   away - this is the card that used to open itself in every reader's face. */
{
  const v = view();
  v.renderEvent(result("b", 9), true);
  const [orphan] = cards(v);
  assert.ok(orphan, "the result is still shown");
  assert.ok(!orphan.classList.contains("open"), "an orphan result stays closed");
  assert.equal(orphan.querySelector(".t-name").textContent, "tool result");
  assert.equal(resultText(orphan), "add: /project/notes.md");
  assert.equal(v.orphanResults.get("b").length, 1);
}

/* Paging back to the call reunites them: its card takes the result and the
   standalone card goes, leaving exactly what an unbroken window shows. */
{
  const v = view();
  v.renderEvent(result("c", 9), true);
  const orphan = cards(v)[0];
  const node = v.buildEventNode(call("c", 8));      // Load older builds this
  v.inner.insertBefore(node, v.inner.firstChild);
  assert.equal(cards(v).length, 1, "only the call's card is left");
  assert.equal(cards(v)[0], node);
  assert.ok(!orphan.isConnected, "the standalone card is removed");
  assert.equal(resultText(node), "add: /project/notes.md");
  assert.ok(!node.classList.contains("open"), "the reunited card stays closed");
  assert.equal(v.orphanResults.has("c"), false, "nothing is left waiting");
}

/* A failure reads the same way whichever window it arrived in. */
{
  const v = view();
  v.renderEvent(result("d", 9, {is_error: true, content: "denied"}), true);
  const orphan = cards(v)[0];
  assert.ok(orphan.classList.contains("err"));
  assert.equal(orphan.querySelector(".tool-body .tb-label").textContent, "error");
  const node = v.buildEventNode(call("d", 8));
  v.inner.insertBefore(node, v.inner.firstChild);
  assert.ok(node.classList.contains("err"), "the call's card carries the failure");
  assert.equal(node.querySelector(".t-state").className, "t-state bad");
}

/* A result with no call id of its own cannot be reunited, and must not queue
   itself under an empty key waiting for a call that can never match. */
{
  const v = view();
  v.renderEvent({seq: 3, kind: "tool_result", data: {content: "loose", is_error: false}}, true);
  assert.equal(cards(v).length, 1);
  assert.ok(!cards(v)[0].classList.contains("open"));
  assert.equal(v.orphanResults.size, 0);
}

/* Filling a card the reader has opened grows the transcript exactly as a new
   card does, so a reading at the foot has to be carried down with it. */
{
  const v = view();
  for (let i = 0; i < 4; i++) v.renderEvent(call(`c${i}`, i + 1), false);
  v.toolCards.c0.querySelector(".tool-head").onclick();          // opened to read
  v.scroll.scrollTop = v.scroll.scrollHeight - v.scroll.clientHeight;
  assert.ok(v.atBottom(), "the reading starts on the tail");
  v.renderEvent(result("c0", 20), true);
  assert.equal(cards(v).length, 4, "the result folded into its call's card");
  assert.ok(v.atBottom(), "an open card that grew keeps the reader on the tail");

  v.scroll.scrollTop = 0;                                        // scrolled up
  v.toolCards.c1.querySelector(".tool-head").onclick();
  v.renderEvent(result("c1", 21), true);
  assert.equal(v.scroll.scrollTop, 0, "and never moves a reader who is not");
}

/* Each live bubble reads its own persisted call timestamp against the backend
   clock, even when that clock differs from this browser's by many minutes. */
{
  sessionMeta = {id: 1, status: "running", active_since: 900};
  context.ingestOneSessionActivity(7, sessionMeta, 1000, clockNow);
  const v = view(); v.tab = {bid: 7, sid: 1}; v.status = "running";
  const first = {...call("first", 10), ts: 985};
  const second = {...call("second", 11), ts: 995};
  v.renderEvent(first, true); v.renderEvent(second, true);
  const tip = (owner, id) => context.tips.text(owner.toolCards[id].querySelector(".t-state"));
  assert.equal(tip(v, "first"), "Running · 0:15");
  assert.equal(tip(v, "second"), "Running · 0:05");
  clockNow += 3000;
  assert.equal(tip(v, "first"), "Running · 0:18", "hover reads elapsed time, never starts it");
  assert.equal(tip(v, "second"), "Running · 0:08");
  const reloaded = view(); reloaded.tab = v.tab; reloaded.status = "running";
  context.ingestOneSessionActivity(7, sessionMeta, 1003, clockNow);
  reloaded.renderEvent(first, true);
  assert.equal(tip(reloaded, "first"), "Running · 0:18", "snapshot/rebuild retains the original start");
  clockNow += 3600000;
  assert.equal(tip(v, "first"), "Running · 1:00:18");
  v.renderEvent(result("first", 12), true);
  assert.equal(tip(v, "first"), "", "the finished call cannot keep a live tooltip");
  assert.equal(tip(v, "second"), "Running · 1:00:08", "a peer finishing does not stop this call");
  v.renderEvent(result("second", 13, {is_error: true}), true);
  assert.equal(tip(v, "second"), "", "a failure also stops its clock");
  v.renderEvent({...call("stopped", 14), ts: 996}, true);
  context.backgroundTaskStateInto(v.toolCards.stopped, {status: "stopped"});
  assert.equal(tip(v, "stopped"), "");
  v.renderEvent({...call("unanswered", 15), ts: 997}, true);
  v.renderEvent({seq: 16, ts: 1004, kind: "result", data: {ok: false}}, true);
  assert.equal(tip(v, "unanswered"), "", "a turn ending stops a call without its own result");
  v.renderEvent({...call("next", 17), ts: 1005}, true);
  assert.ok(tip(v, "next").startsWith("Running · "), "the next call has its own clock");
  v.status = "idle";
  assert.equal(tip(v, "next"), "", "idle sessions have no running call clocks");
  v.status = "running"; sessionMeta.active_since = 1006;
  assert.equal(tip(v, "next"), "", "old calls cannot restart in a new work block");
  sessionMeta.active_since = 900;
  v.renderEvent(call("undated", 18), true);
  assert.equal(tip(v, "undated"), "", "an unknown start is never invented");
  v.closed = true;
  assert.equal(tip(reloaded, "first"), "Running · 1:00:18");
  reloaded.closed = true;
  assert.equal(tip(reloaded, "first"), "", "retired views stop live readouts");
}

console.log("PASS: tool results fold and reunite; per-call hover clocks retain event starts across reloads and clock skew, tick independently, and stop on completion or interruption");
