/* Run with node tests/background_task_ui_test.js. Native background-task
   endings in the real transcript renderer, with no browser or engine. */
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
  v.inner = document.createElement("div");
  document.body.appendChild(v.inner);
  v.newestSeq = 0;
  v.scrollBottom = () => {};
  return v;
}
const tool = (id, seq = 1) => ({seq, kind: "tool_use", data: {
  tool: "Bash", tool_use_id: id, input: {command: "node tests/example.js"},
}});
const result = (id, seq = 2) => ({seq, kind: "tool_result", data: {
  tool_use_id: id, content: "Command running in background", is_error: false,
}});
const notice = (status, id, seq = 3) => ({seq, kind: "info", data: {
  subtype: "task", task_id: "task-example", status,
  ...(id ? {tool_use_id: id} : {}),
  text: status === "completed" ? "node tests/example.js" :
    `Background task ${status}: node tests/example.js`,
}});
for (const [status, tone] of [["completed", "ok"], ["failed", "bad"], ["stopped", "warn"]]) {
  const v = view();
  [tool("one"), result("one"), tool("two", 4), notice(status, "one", 5)]
    .forEach(ev => v.renderEvent(ev, true));
  const card = v.toolCards.one, other = v.toolCards.two;
  const update = card.querySelector(".background-task");
  assert.ok(update, status);
  assert.equal(v.inner.children.length, 2, "the update belongs to its exact card");
  assert.equal(other.querySelector(".background-task"), null, "identical commands do not associate tools");
  assert.equal(update.querySelector(".background-task-label").textContent, `Background task ${status}`);
  assert.ok(update.querySelector(".background-task-label").classList.contains(tone));
  assert.equal(update.querySelector(".background-task-text").textContent, "node tests/example.js");
  assert.equal(update.parentNode, card, "the update stays outside the collapsed body");
  assert.equal(card.classList.contains("open"), false, "arrival does not open the card");
  assert.equal(card.classList.contains("err"), status === "failed");
  assert.ok(card.querySelector(".t-state").classList.contains(tone));
  assert.equal(v.findEventNode(5), update, "search finds the attached event itself");
  card.querySelector(".tool-head").onclick();
  assert.ok(card.classList.contains("open"));
  v.renderEvent(result("one", 6), true);
  assert.ok(card.querySelector(".t-state").classList.contains(tone), "a late launch result cannot overwrite task completion");
  assert.ok(card.classList.contains("open"), "updates preserve disclosure choice");
}

// Current records without native provenance remain fully readable. No
// migration, command matching, or pairing with the most recent tool.
const independent = view();
independent.renderEvent(tool("one"), false);
independent.renderEvent(notice("completed", null), false);
assert.equal(independent.inner.children.length, 2);
assert.equal(independent.toolCards.one.querySelector(".background-task"), null);
assert.equal(independent.inner.querySelectorAll(".info-line").length, 0);

// A history window can begin at the notice. Loading the earlier tool moves
// that same visible node into the card and keeps its search sequence.
const history = view();
history.renderEvent(notice("failed", "one", 9), false);
const original = history.findEventNode(9);
assert.equal(original.parentNode, history.inner);
history.renderEvent(tool("one"), false);
assert.equal(original.parentNode, history.toolCards.one);
assert.equal(history.inner.children.length, 1);
assert.equal(history.findEventNode(9), original);
assert.equal(history.newestSeq, 9);
history.renderEvent(result("one"), false);
assert.ok(history.toolCards.one.querySelector(".t-state").classList.contains("bad"));

const safe = view();
const unknown = notice("__proto__", null);
unknown.data.text = '<img src=x onerror="alert(1)">\n' + "x".repeat(1000);
safe.renderEvent(unknown, false);
assert.equal(safe.inner.querySelector(".background-task-label").textContent, "Background task updated");
assert.equal(safe.inner.querySelector(".background-task-text").textContent, unknown.data.text);
assert.equal(safe.inner.querySelector("img"), null, "engine text is not markup");
console.log("PASS: background-task labels, exact native tool association, visible folded updates, independent history, late results and search targets");
