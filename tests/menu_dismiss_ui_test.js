/* Run with node tests/menu_dismiss_ui_test.js. No browser or engine required. */
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
/* The shared fake DOM only bubbles. Model document capture here so controls
   that stop propagation exercise the same ordering as a touch or mouse press. */
const capture = {};
const listen = document.addEventListener.bind(document);
document.addEventListener = (kind, fn, options) => {
  if (options === true) (capture[kind] ||= []).push(fn);
  else listen(kind, fn);
};
function dispatch(target, kind) {
  const event = new FakeEvent(kind, { bubbles: true, target });
  event.target = target;
  for (const fn of capture[kind] || []) fn(event);
  target.dispatchEvent(event);
}
function focus(target) { target.focus(); dispatch(target, "focusin"); }
function press(target) { dispatch(target, "pointerdown"); dispatch(target, "click"); }
function node(tag, cls = "") {
  const item = document.createElement(tag); item.className = cls;
  document.body.appendChild(item); return item;
}
const app = node("div"); app.id = "app";
const add = node("div", "menu hidden"); add.id = "tab-add-menu";
const plus = node("button"), plusIcon = document.createElement("svg");
plus.appendChild(plusIcon);
const outside = node("button");
for (const kind of ["pointerdown", "focusin", "click"])
  outside.addEventListener(kind, event => event.stopPropagation());
let mobile = true;
const context = vm.createContext({
  document, Composer: { live: new Set() }, Event: FakeEvent,
  window: { innerHeight: 800 }, setTimeout, clearTimeout,
  $: id => document.getElementById(id),
  drawerLayout: () => mobile,
  openDrawer: () => app.classList.add("side-open"),
  setSideCollapsed: on => app.classList.toggle("side-collapsed", on),
  /* the real one measures and places; only the rect it reports back matters here */
  positionAnchoredMenu: (menu, button) =>
    menu && button && button.isConnected ? button.getBoundingClientRect() : null,
  prefersNativeChoices: () => false,
  choiceSvg: () => document.createElement("svg"),
});
vm.runInContext([
  "let tabAddAnchor = null, tabAddTargetGroup = null, openChoiceControl = null;",
  "let choiceMenuSeq = 0;",
  between("const el = ", "/* Close buttons"),
  between("function choiceOptionNode(", "function positionAnchoredMenu("),
  between("function positionChoiceMenu(", "window.addEventListener(\"resize\""),
  between("/* An open list is placed once", "document.addEventListener(\"keydown\""),
  between("function closeAllMenus(", "/* ================= composer @-mentions"),
  between("function showTabAddMenu(", "function renderWorkspacePane("),
  between("function burgerButton(", "function makeTabDragImage("),
].join("\n"), context);
plus.onclick = event => context.showTabAddMenu("pane", plus, event);
const burger = context.burgerButton(); document.body.appendChild(burger);
const burgerIcon = burger.querySelector("rect");
const addOpen = () => !add.classList.contains("hidden");

press(plusIcon);
assert.ok(addOpen(), "the opening click must not dismiss its own menu");
focus(plus);
assert.ok(addOpen(), "focus on the trigger stays inside");
press(plusIcon);
assert.equal(addOpen(), false, "second press toggles closed instead of reopening");
for (const kind of ["pointerdown", "focusin", "click"]) {
  press(plus);
  dispatch(outside, kind);
  assert.equal(addOpen(), false, kind + " dismisses even when the target stops propagation");
}
for (mobile of [true, false]) {
  press(plus);
  dispatch(burgerIcon, "pointerdown");
  assert.equal(addOpen(), false, "menu closes before the sidebar changes layout");
  dispatch(burgerIcon, "click");
  assert.ok(app.classList.contains(mobile ? "side-open" : "side-collapsed"));
}

/* The same boundaries cover session/context, browser/terminal binding, and
   composer choice menus, including SVG descendants and keyboard focus. */
for (const cls of ["menu dyn", "menu dyn br-link-menu", "choice-menu composer-choice-menu dyn"]) {
  const anchor = node("button"), icon = document.createElement("svg");
  anchor.appendChild(icon);
  const open = () => {
    const menu = node("div", cls); menu._anchor = anchor;
    anchor.setAttribute("aria-expanded", "true");
    const row = document.createElement("button"); menu.appendChild(row);
    return { menu, row };
  };
  let { menu, row } = open();
  focus(row); press(row); focus(anchor); dispatch(icon, "pointerdown");
  assert.ok(menu.isConnected, "menu rows and the trigger stay inside: " + cls);
  focus(outside);
  assert.equal(menu.isConnected, false);
  assert.equal(anchor.getAttribute("aria-expanded"), "false");
  ({ menu, row } = open());
  let picked = false;
  row.onclick = event => { event.stopPropagation(); menu.remove(); picked = true; };
  press(row);
  assert.ok(picked, "dismissal must leave the row alive until its action runs");
  ({ menu } = open());
  press(plus);
  assert.equal(menu.isConnected, false, "opening a different menu retires the old one");
  assert.ok(addOpen());
  context.closeAllMenus(null);
}

/* Enhanced selects own additional ARIA and wrapper state. Use the real close
   path and verify that outside focus releases all of it without stealing focus. */
const wrap = node("div", "open"), button = document.createElement("button");
wrap.appendChild(button);
const choice = node("div", "choice-menu"), option = document.createElement("button");
choice.appendChild(option);
button.setAttribute("aria-expanded", "true");
button.setAttribute("aria-controls", "choices");
button.setAttribute("aria-activedescendant", "option");
context.control = { wrap, button, menu: choice };
vm.runInContext("openChoiceControl = control", context);
focus(button); press(option);
assert.ok(choice.isConnected);
focus(outside);
assert.equal(choice.isConnected, false);
assert.equal(wrap.classList.contains("open"), false);
assert.equal(button.getAttribute("aria-expanded"), "false");
assert.equal(button.hasAttribute("aria-controls"), false);
assert.equal(button.hasAttribute("aria-activedescendant"), false);
assert.equal(document.activeElement, outside);
context.closeAllMenus(null);

/* ---- an open list only answers to things that concern it ----
   The enhanced <select> is the console's dropdown everywhere: settings cards,
   the New session and New task dialogs, the engine pickers. Run the real
   control and check what may and may not take it away from the user. */
const host = node("div");
const select = document.createElement("select");
select.setAttribute("aria-label", "Model");
host.appendChild(select);
select.insertAdjacentElement = (where, item) => {
  assert.equal(where, "afterend");
  host.appendChild(item);
  return item;
};
const setOptions = (rows, selectedValue) => {
  select.replaceChildren();
  for (const row of rows) {
    const option = document.createElement("option");
    option.value = row.value;
    option.textContent = row.label;
    option.disabled = !!row.disabled;
    select.appendChild(option);
  }
  select.selectedIndex = rows.findIndex(row => row.value === selectedValue);
};
setOptions([
  { value: "opus", label: "Opus" },
  { value: "sonnet", label: "Sonnet" },
  { value: "haiku", label: "Haiku" },
], "sonnet");
context.enhanceChoiceSelect(select);
const choiceWrap = host.querySelector(".choice-control");
const choiceButton = choiceWrap.querySelector(".choice-button");
let anchorTop = 100;
choiceButton.getBoundingClientRect = () =>
  ({ top: anchorTop, left: 20, right: 120, bottom: anchorTop + 24, width: 100, height: 24 });
const listOpen = () => {
  const menu = document.body.children.find(item =>
    item.classList.contains("choice-menu") && item !== choice);
  return menu && menu.isConnected ? menu : null;
};
const scrolled = () => {
  for (const fn of capture.scroll || []) fn(new FakeEvent("scroll", { target: document }));
};
const rowValues = menu => menu.querySelectorAll(".choice-option").map(row => row.dataset.choiceValue);
const activeValue = menu => {
  const row = menu.querySelector(".choice-option.active");
  return row ? row.dataset.choiceValue : null;
};

press(choiceButton);
let list = listOpen();
assert.ok(list, "the trigger opens its list");
assert.equal(choiceButton.getAttribute("aria-expanded"), "true");
assert.equal(activeValue(list), "sonnet", "the list opens on its current value");

/* The bug: a running agent's transcript scrolls on every line it prints, and
   every one of those scrolls used to close whatever dropdown was open. */
for (let line = 0; line < 5; line++) scrolled();
assert.ok(listOpen() === list, "a scroll that does not move the control leaves the list open");

/* A scroll that really does carry the control away still closes it. */
anchorTop = 40;
scrolled();
assert.ok(listOpen() === null, "a scroll that moves the control closes its list");
assert.equal(choiceButton.getAttribute("aria-expanded"), "false");

press(choiceButton);
list = listOpen();
assert.ok(list, "the list reopens");
const before = rowValues(list);
/* A repaint that changed nothing about the choices - a card marking itself
   dirty, a busy flag going down - must not disturb the list being read. */
for (let paint = 0; paint < 3; paint++) context.refreshChoiceSelect(select);
assert.ok(listOpen() === list, "an unchanged repaint leaves the list alone");
assert.deepEqual(rowValues(list), before);

/* Choices that really did change are redrawn in place, keeping the highlight
   on the value it was on - the same contract the app's other menus keep. */
setOptions([
  { value: "fast", label: "Fast" },
  { value: "opus", label: "Opus" },
  { value: "sonnet", label: "Sonnet" },
  { value: "haiku", label: "Haiku" },
], "sonnet");
context.refreshChoiceSelect(select);
assert.ok(listOpen() === list, "a changed catalog redraws the list instead of closing it");
assert.deepEqual(rowValues(list), ["fast", "opus", "sonnet", "haiku"]);
assert.equal(activeValue(list), "sonnet", "the highlight stays on the value it was on");

/* Losing the control is still the one thing that closes it. */
select.disabled = true;
context.refreshChoiceSelect(select);
assert.ok(listOpen() === null, "a disabled control closes its list");
select.disabled = false;
context.refreshChoiceSelect(select);

press(choiceButton);
list = listOpen();
assert.ok(list);
const changes = [];
select.addEventListener("change", () => changes.push(select.options[select.selectedIndex].value));
press(list.querySelectorAll(".choice-option")[3]);
assert.deepEqual(changes, ["haiku"], "picking a row reports its value once");
assert.ok(listOpen() === null, "picking a row closes the list");
assert.ok(document.activeElement === choiceButton, "focus returns to the trigger");
assert.equal(choiceButton.querySelector(".choice-value").textContent, "Haiku");

/* A trigger a re-render took away leaves nothing to hang from: the next sync
   pass retires the list rather than leaving it over the page. */
press(choiceButton);
assert.ok(listOpen(), "the list reopens once more");
host.removeChild(choiceWrap);
context.syncOpenChoiceMenus();
assert.ok(listOpen() === null, "a list whose trigger was re-rendered away is retired");
host.appendChild(choiceWrap);

console.log("PASS: menu dismissal on outside pointer, focus and click; hamburger; trigger toggles; row actions; choice cleanup; open lists survive unrelated scrolls and repaints");
