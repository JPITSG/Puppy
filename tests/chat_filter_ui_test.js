/* Run with node tests/chat_filter_ui_test.js. Personal transcript choices and
   the real inline menu, without a server. Rendered visibility, live pairing,
   scrolling and navigation are covered by the console browser lane. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const start = source.indexOf("const CHAT_LOG_ALL = ");
const end = source.indexOf("/* A linked remote workspace rides", start);
assert.ok(start > 0 && end > start);
const storage = new Map();
function harness(blocked = false) {
  const document = new FakeDocument();
  const el = (tag, cls = "", text = "") => {
    const node = document.createElement(tag); node.className = cls; node.textContent = text; return node;
  };
  const updates = [], views = new Map();
  const context = vm.createContext({
    document, el, console,
    lsGet: key => { if (blocked) throw Error("storage disabled"); return storage.get(key) ?? null; },
    lsSet: (key, value) => { if (blocked) throw Error("storage disabled"); storage.set(key, value); },
    lsDel: key => { if (blocked) throw Error("storage disabled"); storage.delete(key); },
    sessionViewFor: (bid, sid) => views.get(`${bid}:${sid}`),
    choiceSvg: () => el("svg"), checkIcon: () => el("svg"),
    positionAnchoredMenu: () => updates.push("place"),
    closeAllMenus: () => document.querySelectorAll(".menu").forEach(node => node.remove()),
    navigationRemember() { throw Error("filters are not navigation"); },
    navigationChanged() { throw Error("filters are not navigation"); },
    api() { throw Error("personal filters must not mutate the session"); },
  });
  vm.runInContext(source.slice(start, end), context);
  return { context, document, el, updates, views };
}

{
  const { context: c, updates, views } = harness();
  assert.equal(c.chatLogMask(0, 1), 7, "all three shown by default");
  views.set("0:1", { applyChatLogMask: mask => updates.push(mask) });
  views.set("0:2", { applyChatLogMask() { throw Error("another task must keep its choices"); } });
  views.set("9:1", { applyChatLogMask() { throw Error("another backend must keep its choices"); } });
  for (let mask = 0; mask <= 7; mask++) {
    c.setChatLogMask(0, 1, mask);
    assert.equal(c.chatLogMask(0, 1), mask);
    assert.equal(updates.at(-1), mask, "the open view receives every choice");
    assert.equal(harness().context.chatLogMask(0, 1), mask, "reload retains exactly these choices");
    assert.equal(c.chatLogMask(0, 2), 7);
    assert.equal(c.chatLogMask(9, 1), 7);
  }
  assert.equal(storage.has("puppy.chatLog.0.1"), false, "all shown is the absent default");
  c.setChatLogMask(9, 42, 2);
  assert.equal(harness().context.chatLogMask(9, 42), 2, "a closed conversation can be configured from the sidebar");
  for (const invalid of [-1, 8, 1.5, NaN, null, true, "3"])
    assert.throws(() => c.setChatLogMask(0, 1, invalid), /Invalid chat log filters/);
  for (const invalid of ["", "8", "-1", "01", " 1", "1 ", "1.0", "true", "{}"] ) {
    storage.set("puppy.chatLog.0.50", invalid);
    assert.throws(() => harness().context.chatLogMask(0, 50), /Invalid saved chat log filters/);
    assert.equal(storage.get("puppy.chatLog.0.50"), invalid, "malformed storage is never rewritten");
  }
  storage.clear();
  const denied = harness(true).context;
  assert.equal(denied.chatLogMask(0, 1), 7);
  denied.setChatLogMask(0, 1, 0);
  assert.equal(denied.chatLogMask(0, 1), 0, "blocked storage still permits changes for this page");
}

{
  const { context: c, document, el, updates } = harness();
  const anchor = el("button"), menu = el("div", "menu");
  document.body.append(anchor, menu);
  const section = c.chatLogMenu(0, 1, menu, anchor);
  menu.appendChild(section);
  const toggle = section.querySelector(".chat-log-toggle");
  const options = section.querySelector(".chat-log-options");
  options.scrollIntoView = () => {};
  const chips = [...options.children];
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(options.hidden, true);
  assert.equal(toggle.getAttribute("aria-controls"), options.id);
  assert.equal(section.querySelector(".chat-log-summary").textContent, "All");
  assert.deepEqual(chips.map(b => b.getAttribute("aria-label")), ["Show questions", "Show answers", "Show tool calls"]);
  toggle.click();
  assert.equal(options.hidden, false);
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  assert.equal(updates.at(-1), "place", "expansion repositions the same menu to fit");
  chips[2].focus(); chips[2].click();
  assert.equal(c.chatLogMask(0, 1), 3);
  assert.equal(section.querySelector(".chat-log-summary").textContent, "2 of 3");
  assert.equal(chips[2].getAttribute("aria-pressed"), "false");
  assert.equal(document.activeElement, chips[2]);
  assert.equal(menu.isConnected, true, "picking a chip keeps the menu open");
  chips[0].click(); chips[1].click();
  assert.equal(c.chatLogMask(0, 1), 0, "every category can be disabled");
  assert.equal(section.querySelector(".chat-log-summary").textContent, "None");
  chips[0].focus();
  const right = new FakeEvent("keydown", {key: "ArrowRight"}); section.onkeydown(right);
  assert.equal(document.activeElement, chips[1]); assert.equal(right.defaultPrevented, true);
  section.onkeydown(new FakeEvent("keydown", {key: "ArrowLeft"}));
  section.onkeydown(new FakeEvent("keydown", {key: "ArrowLeft"}));
  assert.equal(document.activeElement, chips[2], "arrow keys wrap between choices");
  toggle.click(); assert.equal(options.hidden, true);
  assert.equal(c.chatLogMask(0, 1), 0, "closing the disclosure keeps the preference");
  section.onkeydown(new FakeEvent("keydown", {key: "Escape"}));
  assert.equal(menu.isConnected, false); assert.equal(document.activeElement, anchor);
  let placed = false;
  const sidebar = c.chatLogMenu(0, 1, el("div"), null, () => { placed = true; });
  sidebar.querySelector(".chat-log-options").scrollIntoView = () => {};
  sidebar.querySelector(".chat-log-toggle").click();
  assert.equal(placed, true, "the sidebar preserves its pointer-based placement");
}
console.log("PASS: inline chat filters stay open, support keyboard controls, persist each conversation independently, reject invalid preferences and work with storage disabled");
