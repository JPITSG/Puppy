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
  document, Composer: { live: new Set() },
  $: id => document.getElementById(id),
  drawerLayout: () => mobile,
  setSideCollapsed: on => app.classList.toggle("side-collapsed", on),
  positionAnchoredMenu() {},
});
vm.runInContext([
  "let tabAddAnchor = null, tabAddTargetGroup = null, openChoiceControl = null;",
  between("const el = ", "/* Close buttons"),
  between("function closeChoiceMenu(", "function positionAnchoredMenu("),
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
console.log("PASS: menu dismissal on outside pointer, focus and click; hamburger; trigger toggles; row actions; choice cleanup");
