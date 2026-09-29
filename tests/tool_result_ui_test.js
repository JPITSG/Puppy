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

/* Recovered calls are unknown outcomes, never successful completions. The
   result must survive both directions of history paging and remain folded. */
{
  for (const backwards of [false, true]) {
    const v = view();
    const ended = result("lost", 70, {is_error: true, interrupted: true,
      content: "Puppy restarted before this tool reported a result. Its outcome is unknown."});
    if (backwards) {
      v.renderEvent(ended, true);
      v.inner.insertBefore(v.buildEventNode(call("lost", 10)), v.inner.firstChild);
    } else [call("lost", 10), ended].forEach(ev => v.renderEvent(ev, true));
    assert.equal(cards(v).length, 1);
    const card = cards(v)[0];
    assert.equal(card.querySelector(".t-state").className, "t-state warn");
    assert.equal(card.querySelector(".t-state").textContent, "interrupted");
    assert.equal(card.querySelector(".t-state").getAttribute("aria-label"), "interrupted");
    assert.equal([...card.querySelectorAll(".tb-label")].pop().textContent, "interrupted");
    assert.ok(!card.classList.contains("open") && !card.classList.contains("err"));
    assert.ok(resultText(card).includes("outcome is unknown"));
  }
}

/* Partial history is not evidence that a tool is still running or failed.
   Calls in an idle session, before the current activity block, or behind a
   known turn ending show ended until their authoritative result is loaded. */
{
  const v = view(); v.status = "idle";
  v.renderEvent(call("old", 1), true);
  assert.equal(v.toolCards.old.querySelector(".t-state").textContent, "ended");
  v.renderEvent(result("old", 2), true);
  assert.equal(v.toolCards.old.querySelector(".t-state").className, "t-state ok");
  v.status = "running"; v.tab = {bid: 0, sid: 9};
  sessionMeta = {active_since: 100};
  v.renderEvent({...call("previous", 3), ts: 99}, true);
  v.renderEvent({...call("current", 4), ts: 101}, true);
  assert.equal(v.toolCards.previous.querySelector(".t-state").textContent, "ended");
  assert.equal(v.toolCards.current.querySelector(".t-state").className, "t-state busy");
  v.renderEvent({seq: 5, kind: "result", data: {ok: false}}, true);
  assert.equal(v.toolCards.current.querySelector(".t-state").textContent, "ended");
  const older = v.buildEventNode({...call("paged", 2), ts: 101});
  assert.equal(older.querySelector(".t-state").textContent, "ended");
  v.renderEvent({...call("next", 6), ts: 102}, true);
  assert.equal(v.toolCards.next.querySelector(".t-state").className, "t-state busy");
  // A reconnect while detached keeps the reading window but stops its old
  // spinners using the snapshot's fresh idle state.
  v.status = "idle";
  context.refreshToolStates(v);
  assert.equal(v.toolCards.next.querySelector(".t-state").textContent, "ended");
  sessionMeta = null;
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
  v.renderEvent({...result("first", 12), ts: 4603.5}, true);
  assert.equal(tip(v, "first"), "Completed · 1:00:18", "the finished call's clock stops at its own result");
  assert.equal(tip(v, "second"), "Running · 1:00:08", "a peer finishing does not stop this call");
  v.renderEvent({...result("second", 13, {is_error: true}), ts: 4603.5}, true);
  assert.equal(tip(v, "second"), "Failed · 1:00:08", "a failure also stops its clock");
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

/* A finished call's mark says how it ended and how long it took, measured
   between the node's own stamps for the call and for the ending the mark
   shows - never this browser's clock, and never a guess for a missing stamp. */
{
  sessionMeta = {id: 2, status: "running", active_since: 900};
  context.ingestOneSessionActivity(7, sessionMeta, 1000, clockNow);
  const v = view(); v.tab = {bid: 7, sid: 2}; v.status = "running";
  const tip = id => context.tips.text(v.toolCards[id].querySelector(".t-state"));
  v.renderEvent({...call("done", 1), ts: 950.25}, true);
  v.renderEvent({...result("done", 2), ts: 978.9}, true);
  assert.equal(tip("done"), "Completed · 0:28");
  v.renderEvent({...call("quick", 3), ts: 980}, true);
  v.renderEvent({...result("quick", 4), ts: 980.4}, true);
  assert.equal(tip("quick"), "Completed · 0:00", "a call under a second reads the stopwatch's own face");
  v.renderEvent({...call("broke", 5), ts: 981}, true);
  v.renderEvent({...result("broke", 6, {is_error: true, content: "exit 1"}), ts: 4708}, true);
  assert.equal(tip("broke"), "Failed · 1:02:07");
  clockNow += 3600000;
  assert.equal(tip("done"), "Completed · 0:28", "a finished call's time stands still");
  v.status = "idle";
  assert.equal(tip("done"), "Completed · 0:28", "and outlives the turn that ran it");
  assert.equal(tip("broke"), "Failed · 1:02:07");
  v.closed = true;
  assert.equal(tip("done"), "Completed · 0:28", "nothing about it is live");
  v.closed = false;

  // Only a check or a cross has an ending to measure to.
  v.renderEvent({...call("lost", 7), ts: 982}, true);
  v.renderEvent({...result("lost", 8, {is_error: true, interrupted: true}), ts: 990}, true);
  assert.equal(v.toolCards.lost.querySelector(".t-state").textContent, "interrupted");
  assert.equal(tip("lost"), "", "an interrupted call's outcome and time are unknown");
  v.renderEvent({...call("unfinished", 9), ts: 983}, true);
  assert.equal(v.toolCards.unfinished.querySelector(".t-state").textContent, "ended");
  assert.equal(tip("unfinished"), "", "an ended call without its result has no time");
  v.renderEvent({...call("unstamped", 10), ts: 984}, true);
  v.renderEvent(result("unstamped", 11), true);
  assert.ok(v.toolCards.unstamped.querySelector(".t-state").classList.contains("ok"));
  assert.equal(tip("unstamped"), "", "a result without a stamp is never measured");
  v.renderEvent(call("undated", 12), true);
  v.renderEvent({...result("undated", 13), ts: 990}, true);
  assert.equal(tip("undated"), "", "nor is a call without one");
  v.renderEvent({...call("backwards", 14), ts: 991}, true);
  v.renderEvent({...result("backwards", 15), ts: 990}, true);
  assert.equal(tip("backwards"), "", "a result stamped before its call is refused, not shown as zero");

  // A window that starts after the call measures once paging back brings it.
  v.renderEvent({...result("paged", 21), ts: 1075.5}, true);
  const node = v.buildEventNode({...call("paged", 20), ts: 1000});
  v.inner.insertBefore(node, v.inner.firstChild);
  assert.equal(tip("paged"), "Completed · 1:15", "the reunited card measures to its result's own stamp");

  // A command sent to the background: its launch completes at once, then the
  // task's ending decides the mark and so the time, whichever loads first.
  const notice = (id, status, seq, ts) => ({seq, ts, kind: "info", data: {
    subtype: "task", task_id: "task-" + id, tool_use_id: id, status, text: "npm run build"}});
  for (const [status, reading] of [["completed", "Completed · 5:25"], ["failed", "Failed · 5:25"], ["stopped", ""]]) {
    const live = view(); live.tab = {bid: 7, sid: 2}; live.status = "running";
    const liveTip = () => context.tips.text(live.toolCards.bg.querySelector(".t-state"));
    live.renderEvent({...call("bg", 1), ts: 1000}, true);
    live.renderEvent({...result("bg", 2, {content: "Command running in background"}), ts: 1000.2}, true);
    assert.equal(liveTip(), "Completed · 0:00", "the launch itself took no time");
    live.renderEvent(notice("bg", status, 3, 1325.7), true);
    assert.equal(liveTip(), reading, `the ${status} task's ending is the call's`);
    live.renderEvent({...result("bg", 4), ts: 1400}, true);
    assert.equal(liveTip(), reading, "a late launch result cannot move the task's ending");

    const history = view(); history.tab = live.tab; history.status = "idle";
    history.renderEvent(notice("bg", status, 9, 1325.7), false);
    history.renderEvent({...call("bg", 1), ts: 1000}, false);
    assert.equal(context.tips.text(history.toolCards.bg.querySelector(".t-state")), reading,
      "a history window that began at the ending measures to it");
    history.renderEvent({...result("bg", 2), ts: 1000.2}, false);
    assert.equal(context.tips.text(history.toolCards.bg.querySelector(".t-state")), reading,
      "and the launch result loading after it keeps that ending");
  }
  sessionMeta = null;
}

console.log("PASS: tool results fold and reunite; per-call hover clocks retain event starts across reloads and clock skew, tick independently, and stop on completion or interruption; finished calls read Completed or Failed with how long they took between their own stamps, background endings included");
