/* Run with node tests/tool_result_ui_test.js. A tool result belongs to its
   call's card, folded away until the reader opens it - including when the
   window it arrived in started after that call. No browser or engine. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const {FakeDocument} = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
};
const document = new FakeDocument();
const icon = () => document.createElement("svg");
const context = vm.createContext({
  document, Map, console, choiceSvg: icon, toolIconNode: icon,
  xIcon: icon, checkIcon: icon, promptSpinnerNode: icon,
  linkifyInto: (node, text) => { node.textContent = text; return node; },
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function displayValue(", "function toolIconNode("),
  between("function toolStateInto(", "/* A side question"),
  between("/* Task endings stay visible", "const ATTACHMENT_PREVIEW_TYPES"),
  "class View {",
  between("  findEventNode(seq)", "  /* Dividers mark"),
  between("  buildEventNode(ev)", "  /* live streaming bubble */"),
  "} globalThis.View = View;",
].join("\n"), context);

function view() {
  const v = new context.View();
  v.toolCards = {};
  v.backgroundTaskUpdates = new Map();
  v.orphanResults = new Map();
  v.inner = document.createElement("div");
  document.body.appendChild(v.inner);
  v.newestSeq = 0;
  v.scrollBottom = () => {};
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

console.log("PASS: tool results fold into their call's card, closed, and reunite when it loads");
