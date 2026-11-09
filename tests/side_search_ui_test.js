/* Run with node tests/side_search_ui_test.js. What the sidebar's quick
   search looks in, against the fake DOM: the three things a row shows, each
   chosen by a tick that slides out inside the box while it is in use - all
   three by default, the browser keeping a narrower choice as one exact digit
   and reading anything else as all three, never nothing. Focusing the box
   brings them out; leaving it empty takes them away, and leaving it with
   words in it does not; a press on a tick keeps the caret where it was.
   No browser, engine, network or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { FakeDocument, fire } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}

/* a fresh page: the box as index.html lays it out, the browser's storage
   holding `stored` (null for nothing) */
function page(stored) {
  const document = new FakeDocument();
  const make = (tag, id) => { const node = document.createElement(tag); if (id) node.id = id; return node; };
  const box = make("div", "side-search"), panel = make("div", "side-search-in");
  panel.hidden = true;
  const row = make("div"), input = make("input", "side-search-input");
  row.append(input, make("button", "side-search-adv"), make("button", "side-search-go"));
  box.append(panel, row);
  document.body.appendChild(box);
  const storage = new Map(stored === null ? [] : [["puppy.sideSearchIn", stored]]);
  const icon = name => () => { const svg = document.createElement("svg"); svg.dataset.icon = name; return svg; };
  const calls = { renders: 0, slides: [], searchTab: [] };
  const context = vm.createContext({
    document, console,
    state: { sessionFilter: "", activeGroup: "g" },
    $: id => document.getElementById(id),
    lsGet: key => storage.has(key) ? storage.get(key) : null,
    lsSet: (key, value) => storage.set(key, String(value)),
    lsDel: key => storage.delete(key),
    renderSidebar: () => { calls.renders += 1; },
    setDisclosureCollapsed: (node, collapsed, animate) => {
      calls.slides.push({ node, collapsed, animate });
      node.hidden = collapsed;
    },
    checkIcon: icon("check"), xIcon: icon("x"), searchIcon: icon("search"), tuneIcon: icon("tune"),
    openSearchTab: (group, text) => calls.searchTab.push([group, text]),
    closeDrawer() {},
    sessionLocationTitle: session => session.cwd,
    backendName: bid => bid ? "studio-2" : "studio",
  });
  vm.runInContext([
    between("const el = ", "/* Close buttons"),
    between("/* What the sidebar quick-search can see", "/* The engine whose own CLI"),
    between("/* ---- sidebar quick-search ----", "/* ================= tabs"),
    "globalThis.peek = () => ({ mask: sideSearchIn, open: sideSearchOpen });",
  ].join("\n"), context);
  const choices = () => panel.querySelectorAll(".side-search-choice");
  const read = () => choices().map(choice => ({
    label: choice.children[1].textContent,
    checked: choice.getAttribute("aria-checked"),
    ticked: choice.querySelector(".menu-check-mark").children.length === 1,
    locked: choice.getAttribute("aria-disabled") === "true",
  }));
  return { document, context, box, panel, input, storage, calls, choices, read };
}
const session = { id: 4, name: "Harbor dashboard", cwd: "/home/mira/projects/harbor" };

/* the three things a row shows, all three by default */
{
  const p = page(null);
  assert.equal(p.context.peek().mask, 7);
  assert.equal(p.context.sessionFilterHaystack(0, session),
    "harbor dashboard\n/home/mira/projects/harbor\nstudio");
  assert.equal(p.context.sessionFilterHaystack(2, { id: 9, cwd: "/srv/x" }),
    "session 9\n/srv/x\nstudio-2", "an unnamed session is searched by the name its row shows");
  assert.deepEqual(p.read(), [
    { label: "Name", checked: "true", ticked: true, locked: false },
    { label: "Path", checked: "true", ticked: true, locked: false },
    { label: "Backend", checked: "true", ticked: true, locked: false },
  ]);
  assert.equal(p.panel.getAttribute("role"), null, "the group's role is the page's own markup");
  assert.ok(p.choices().every(choice => choice.getAttribute("role") === "checkbox" && choice.type === "button"));
  assert.equal(p.panel.hidden, true, "nothing shows before the box is used");
}

/* a narrower choice kept by the browser is one exact digit; anything else
   reads as all three and is left as it was */
for (const [stored, mask] of [["1", 1], ["5", 5], ["6", 6], ["0", 7], ["8", 7], ["07", 7], ["5 ", 7], ["", 7], ["x", 7]]) {
  const p = page(stored);
  assert.equal(p.context.peek().mask, mask, JSON.stringify(stored));
  assert.equal(p.storage.get("puppy.sideSearchIn"), stored, "what was there is not written over");
}
{
  const p = page("5");
  assert.equal(p.context.sessionFilterHaystack(0, session), "harbor dashboard\nstudio");
  assert.deepEqual(p.read().map(r => r.checked), ["true", "false", "true"]);
  const only = page("2");
  assert.equal(only.context.sessionFilterHaystack(0, session), "/home/mira/projects/harbor");
  assert.deepEqual(only.read().map(r => r.locked), [false, true, false], "the last one left is held");
}

/* focusing the box slides them out; leaving it empty slides them back, and
   leaving it with words in it - or for one of its own controls - does not */
{
  const p = page(null);
  fire(p.input, "focus", { bubbles: false });
  assert.deepEqual(p.calls.slides.map(s => [s.node, s.collapsed, s.animate]), [[p.panel, false, true]]);
  assert.equal(p.panel.hidden, false);
  fire(p.input, "focus", { bubbles: false });
  assert.equal(p.calls.slides.length, 1, "focusing again asks for nothing");
  fire(p.input, "focusout", { relatedTarget: p.choices()[1] });
  fire(p.input, "focusout", { relatedTarget: p.document.getElementById("side-search-adv") });
  assert.equal(p.panel.hidden, false, "a move within the box keeps them");
  p.input.value = "harbor";
  fire(p.input, "input");
  assert.equal(p.context.state.sessionFilter, "harbor");
  fire(p.input, "focusout", { relatedTarget: null });
  assert.equal(p.panel.hidden, false, "left with words in it, they stay to say what was searched");
  fire(p.input, "focus", { bubbles: false });
  p.input.value = "  ";
  fire(p.input, "input");
  fire(p.input, "focusout", { relatedTarget: null });
  assert.equal(p.panel.hidden, true, "left empty, they go");
  assert.deepEqual(p.calls.slides.map(s => s.collapsed), [false, true]);
  fire(p.input, "focusout", { relatedTarget: null });
  assert.equal(p.calls.slides.length, 2, "and only once");

  // Escape clears what is in the box first (spaces too) and keeps them out;
  // Escape on the empty box lets it go, which takes them with it
  fire(p.input, "focus", { bubbles: false });
  p.document.activeElement = p.input;
  assert.ok(fire(p.input, "keydown", { key: "Escape" }).defaultPrevented);
  assert.equal(p.input.value, "");
  assert.equal(p.document.activeElement, p.input);
  assert.equal(p.panel.hidden, false);
  assert.ok(fire(p.input, "keydown", { key: "Escape" }).defaultPrevented);
  assert.equal(p.document.activeElement, null);
  fire(p.input, "focusout", { relatedTarget: null });
  assert.equal(p.panel.hidden, true);
}

/* a press on a choice: the caret stays (a mouse's press is not a focus), a
   finger is left to the browser, and the choice is the mask, the storage and
   - only while something is typed - the list */
{
  const p = page(null);
  const [name, where, backend] = p.choices();
  assert.equal(fire(where, "pointerdown", { pointerType: "mouse" }).defaultPrevented, true);
  assert.equal(fire(where, "pointerdown", { pointerType: "pen" }).defaultPrevented, true);
  assert.equal(fire(where, "pointerdown", { pointerType: "touch" }).defaultPrevented, false);
  where.click();
  assert.equal(p.context.peek().mask, 5);
  assert.equal(p.storage.get("puppy.sideSearchIn"), "5");
  assert.equal(p.calls.renders, 0, "with nothing typed the list has nothing to redo");
  assert.deepEqual(p.read().map(r => [r.checked, r.ticked]), [["true", true], ["false", false], ["true", true]]);
  p.context.state.sessionFilter = "harbor";
  name.click();
  assert.equal(p.context.peek().mask, 4);
  assert.equal(p.calls.renders, 1, "while something is typed the list follows at once");
  assert.equal(p.storage.get("puppy.sideSearchIn"), "4");
  assert.deepEqual(p.read().map(r => r.locked), [false, false, true]);
  backend.click();
  assert.equal(p.context.peek().mask, 4, "the last one left cannot be taken away");
  assert.equal(p.calls.renders, 1);
  name.click(); where.click();
  assert.equal(p.context.peek().mask, 7);
  assert.equal(p.storage.has("puppy.sideSearchIn"), false, "all three is nothing to keep");
  assert.deepEqual(p.read().map(r => r.locked), [false, false, false]);
  assert.equal(p.context.sessionFilterHaystack(0, session),
    "harbor dashboard\n/home/mira/projects/harbor\nstudio");
}

/* the box's own buttons are as before: the sliders carry the words to the
   Search tab, the cross clears and gives the caret back */
{
  const p = page(null);
  p.input.value = "garden";
  fire(p.input, "input");
  p.document.getElementById("side-search-adv").click();
  assert.deepEqual(p.calls.searchTab, [["g", "garden"]]);
  p.document.getElementById("side-search-go").click();
  assert.equal(p.input.value, "");
  assert.equal(p.context.state.sessionFilter, "");
  assert.equal(p.document.activeElement, p.input);
}

console.log("PASS: the quick search looks in the name, path and backend its ticks choose - all three by default, one exact digit kept by the browser and anything else read as all three, never nothing - sliding out on focus, staying while words are in the box or focus is inside it, going when left empty, and keeping the caret through a press");
