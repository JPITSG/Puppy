/* Run with node tests/prompt_gutter_ui_test.js. The prompt gutter against the
   fake DOM with a modelled layout: the session's prompt index read from the
   node's compact rows a page at a time, then only after the last prompt it
   holds, and whole again when the node's total no longer adds up; an older
   node's kind=user event pages (and no read at all from a node with
   neither); the line a prompt is listed by; numbers that count the
   session's prompts with steers aside, kept until the index changes; a pin
   for every prompt on the page - its number or steer bead, time, label,
   place beside its bubble - the room the gutter takes from a narrow column
   and none from a wide one, no gutter for a narrow transcript, a filtered
   one or one with no prompts, the thread standing still between the plates
   while the pins scroll, the list button under the top plate, pins faded
   behind a plate or the button (measured once per render, never on a
   scroll), the lit prompt, the plates' reach into history not on the page,
   a step's landing under the list button with one flash at a time, live
   prompts joining, and the teardown. Then the prompt list over six hundred
   prompts: one float on the shared menus' terms, a window of rows that
   never draws more than it shows, the prompt being read lit and in view,
   the list's own scroll, the filter by text or number, the keys and the
   pointer, a pick landing like a step, a new index under an open list, and
   every way it closes. No browser, engine, network or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const {FakeDocument, FakeElement, FakeEvent} = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
};

/* The modelled layout: the transcript's box on the screen, the column's left
   edge, and each bubble's place in the scrolled content. */
const BOX = {top: 100, left: 300};
const layout = {width: 1200, height: 600, column: 150, places: new Map(), scroll: null, root: null};
FakeElement.prototype.getBoundingClientRect = function () {
  const scroll = layout.scroll;
  if (this === scroll) return {top: BOX.top, left: BOX.left, width: layout.width, height: layout.height,
    right: BOX.left + layout.width, bottom: BOX.top + layout.height};
  if (this === layout.root) return {top: 0, left: 0, width: 2000, height: 2000, right: 2000, bottom: 2000};
  if (this.classList && this.classList.contains("chat-inner"))
    return {top: BOX.top, left: BOX.left + layout.column, width: 900, height: 0, right: 0, bottom: 0};
  if (this.classList && this.classList.contains("prompt-list-button")) {
    const left = parseFloat(this.style.left) || 0, top = parseFloat(this.style.top) || 0;
    return {top, left, width: 26, height: 26, right: left + 26, bottom: top + 26};
  }
  const y = layout.places.get(this);
  if (y === undefined) return {top: 0, left: 0, width: 0, height: 0, right: 0, bottom: 0};
  const top = BOX.top + y - scroll.scrollTop;
  return {top, left: BOX.left + layout.column + 500, width: 300, height: 40, right: 0, bottom: top + 40};
};

const document = new FakeDocument();
const requests = [];
const routes = {};
let frames = [];
const observers = [];
class Observer { constructor(fn) { this.fn = fn; this.on = true; observers.push(this); }
  observe() {} disconnect() { this.on = false; } }
const scrolls = [];
/* the shared floats: closeAllMenus takes every open .choice-menu.dyn away and
   says whether the one hanging from this anchor was among them */
const closeAllMenus = anchor => {
  let wasOpen = false;
  for (const menu of document.body.querySelectorAll(".choice-menu.dyn")) {
    if (anchor && menu._anchor === anchor) wasOpen = true;
    if (menu._anchor) menu._anchor.setAttribute("aria-expanded", "false");
    menu.remove();
  }
  return wasOpen;
};
const context = vm.createContext({
  document, console, Math, Number, Promise, JSON, Map, Set, String, Array, Error, Object, RegExp,
  window: {matchMedia: () => ({matches: false}), innerWidth: 1600, innerHeight: 900},
  MutationObserver: Observer, ResizeObserver: Observer,
  requestAnimationFrame: fn => { frames.push(fn); return frames.length; },
  cancelAnimationFrame: () => { frames = []; },
  setTimeout: () => 0,
  fmtTime: ts => "t" + ts,
  fmtStamp: ts => "s" + ts,
  focusOnShow: () => true,
  closeAllMenus,
  state: {backends: [{id: 3, capabilities: []}, {id: 4, capabilities: ["session-prompt-history"]},
    {id: 5, capabilities: ["session-prompt-history", "session-prompt-index"]}]},
  backendHasCapability: (backend, name) => !!backend && backend.capabilities.includes(name),
  api: async (bid, route) => {
    requests.push([bid, route]);
    const answer = typeof routes[route] === "function" ? routes[route]() : routes[route];
    if (answer instanceof Error) throw answer;
    return JSON.parse(JSON.stringify(answer || {events: [], prompts: [], total: 0, more: false}));
  },
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function chevronIcon(size", "function toolsIcon("),
  between("const CHAT_LOG_ALL = 7;", "const chatLogMasks = new Map();"),
  between("const ATTACH_IMAGE_PREFIX", "const SENT_THUMBNAIL_LIMIT"),
  between("function baseName(path)", "function splitAttachmentMarkers("),
  between("/* ================= prompt gutter", "class SessionView {"),
].join("\n"), context);
const api = vm.runInContext(`({ PromptGutter, el, promptExcerpt, PROMPT_LANDING, PROMPT_GUTTER, PROMPT_INDEX_PAGE,
  PROMPT_LIST_OVERSCAN, PROMPT_LIST_ROW, ATTACH_IMAGE_PREFIX, ATTACH_IMAGE_SUFFIX, ATTACH_FILE_PREFIX,
  ATTACH_FILE_SUFFIX })`, context);
const flush = () => { const run = frames; frames = []; run.forEach(fn => fn()); };
const settle = () => new Promise(resolve => setImmediate(resolve));

function makeView(bid = 0, sid = 7) {
  const root = api.el("div", "view");
  const scroll = root.appendChild(api.el("div", "chat-scroll"));
  const inner = scroll.appendChild(api.el("div", "chat-inner"));
  const view = {root, scroll, inner, tab: {bid, sid}, chatLogMask: 7, oldestSeq: null, newestSeq: 0,
    jumps: [], jumpToPrompt(seq) { this.jumps.push(seq); }, detached: false,
    newest: 0, goToNewest() { this.newest++; }};
  scroll.clientWidth = layout.width;
  scroll.clientHeight = layout.height;
  scroll.scrollHeight = 2000;
  scroll.scrollTo = ({top}) => { scrolls.push(top); scroll.scrollTop = top; };
  layout.scroll = scroll;
  layout.root = root;
  layout.places = new Map();
  document.body.appendChild(root);
  return view;
}
function bubble(view, seq, y, {steering = false, at = 1000 + seq, text = "prompt " + seq} = {}) {
  const node = api.el("div", "msg msg-user", text);
  node.dataset.seq = String(seq);
  node.dataset.at = String(at);
  node.dataset.excerpt = api.promptExcerpt(text);
  if (steering) node.dataset.steering = "1";
  view.inner.appendChild(node);
  layout.places.set(node, y);
  return node;
}
const pins = view => [...view.scroll.querySelectorAll(".prompt-pin")].map(pin => ({
  number: pin.querySelector(".prompt-pin-dot").textContent, time: pin.querySelector(".prompt-pin-time").textContent,
  steer: pin.classList.contains("steer"), top: parseFloat(pin.style.top), left: parseFloat(pin.style.left),
  label: pin.getAttribute("aria-label"), on: pin.classList.contains("on"), tucked: pin.classList.contains("tucked")}));
const userEvent = (seq, steering = false) => ({seq, kind: "user", ts: 1000 + seq, data: {text: "p" + seq, steering}});
const row = (seq, steering = false, text = "prompt " + seq) => [seq, 1000 + seq, steering ? 1 : 0, text];
const key = (node, name) => {
  const event = new FakeEvent("keydown", {bubbles: true});
  event.key = name;
  node.dispatchEvent(event);
  return event;
};

(async () => {
  /* ---- the line a prompt is listed by ---- */
  {
    const image = name => api.ATTACH_IMAGE_PREFIX + "/data/uploads/1/a/" + name + api.ATTACH_IMAGE_SUFFIX;
    const file = name => api.ATTACH_FILE_PREFIX + "/data/uploads/1/b/" + name + " (" + name + ", 3 KB)" + api.ATTACH_FILE_SUFFIX;
    assert.equal(api.promptExcerpt("  \n  Fix the   coupon\treset.\nsecond line"), "Fix the coupon reset.");
    assert.equal(api.promptExcerpt(image("shot.png")), "shot.png", "a prompt of attachments alone: its files");
    assert.equal(api.promptExcerpt(file("notes, v2 (draft).txt") + "\n" + image("b.png")), "notes, v2 (draft).txt, b.png");
    assert.equal(api.promptExcerpt(image("a.png") + "\nLook at this"), "Look at this", "text wins over files");
    const long = api.promptExcerpt("x".repeat(300));
    assert.equal(long.length, 120);
    assert.ok(long.endsWith("…"));
    assert.equal(api.promptExcerpt(""), "");
    assert.equal(api.promptExcerpt(undefined), "");
  }
  console.log("PASS: a prompt is listed by its first line, collapsed and bounded, or by its files when it has no text");

  /* ---- the index: compact rows, after what it holds, whole when it stops adding up ---- */
  {
    const view = makeView(0, 7);
    const gutter = new api.PromptGutter(view);
    assert.ok(view.scroll.querySelector(".prompt-pins") && view.root.querySelector(".prompt-plates"),
              "the pins scroll with the transcript, the plates stand over it");
    const thread = view.root.querySelector(".prompt-thread");
    assert.ok(thread && !view.scroll.contains(thread) && thread.nextSibling === view.scroll,
              "the thread stands still under the transcript, outside the scroller");
    routes["sessions/7/prompts?after_seq=0"] = {prompts: [row(2), row(5, true), row(9)], total: 5, more: true};
    routes["sessions/7/prompts?after_seq=9"] = {prompts: [row(14), row(20)], total: 5, more: false};
    await gutter.refreshIndex();
    assert.deepEqual(requests.map(r => r[1]), ["sessions/7/prompts?after_seq=0", "sessions/7/prompts?after_seq=9"],
                     "this node's compact rows, page after page");
    assert.deepEqual([...gutter.index.keys()], [2, 5, 9, 14, 20]);
    assert.ok(gutter.index.get(5).steering && gutter.index.get(9).text === "prompt 9");
    assert.equal(gutter.indexSeq, 20);
    assert.deepEqual([...gutter.ordered().numbers], [[2, 1], [5, 0], [9, 2], [14, 3], [20, 4]]);
    const order = gutter.ordered();
    assert.equal(gutter.ordered(), order, "the order is kept until the index changes");
    // the next snapshot asks only for what follows
    requests.length = 0;
    routes["sessions/7/prompts?after_seq=20"] = {prompts: [row(26)], total: 6, more: false};
    await gutter.refreshIndex();
    assert.deepEqual(requests.map(r => r[1]), ["sessions/7/prompts?after_seq=20"]);
    assert.equal(gutter.index.size, 6);
    assert.notEqual(gutter.ordered(), order, "a new prompt orders the index again");
    // a live prompt newer than the read is not a prompt the read missed
    gutter.index.set(31, {seq: 31, at: 0, steering: false, text: "live"});
    requests.length = 0;
    routes["sessions/7/prompts?after_seq=26"] = {prompts: [], total: 6, more: false};
    await gutter.refreshIndex();
    assert.deepEqual(requests.map(r => r[1]), ["sessions/7/prompts?after_seq=26"], "it still adds up");
    // a copy that no longer adds up is read whole, keeping only live prompts newer than the read
    requests.length = 0;
    routes["sessions/7/prompts?after_seq=26"] = {prompts: [], total: 4, more: false};
    routes["sessions/7/prompts?after_seq=0"] = {prompts: [row(2), row(9), row(14), row(26)], total: 4, more: false};
    await gutter.refreshIndex();
    assert.deepEqual(requests.map(r => r[1]), ["sessions/7/prompts?after_seq=26", "sessions/7/prompts?after_seq=0"]);
    assert.deepEqual([...gutter.index.keys()].sort((a, b) => a - b), [2, 9, 14, 26, 31]);
    // a page that moves nothing ends the read
    requests.length = 0;
    routes["sessions/7/prompts?after_seq=26"] = {prompts: [row(3)], total: 5, more: true};
    await gutter.refreshIndex();
    assert.ok(requests.length <= 3, "no endless paging on a page that goes nowhere");
    // a failed read keeps what the gutter had
    const before = gutter.index.size;
    routes["sessions/7/prompts?after_seq=26"] = new Error("Backend unavailable");
    await gutter.refreshIndex();
    assert.equal(gutter.index.size, before);
    // a newer read wins over an older one still on its way
    let release;
    routes["sessions/7/prompts?after_seq=26"] = () => new Promise(resolve => { release = resolve; });
    const slow = gutter.refreshIndex();
    routes["sessions/7/prompts?after_seq=26"] = {prompts: [row(40)], total: 6, more: false};
    await gutter.refreshIndex();
    release({prompts: [row(50)], total: 99, more: false});
    await slow;
    assert.ok(gutter.index.has(40) && !gutter.index.has(50), "the older answer is dropped");

    // a remote node: the compact rows where it serves them, its event pages where it does not, nothing without either
    requests.length = 0;
    await new api.PromptGutter(makeView(3, 8)).refreshIndex();
    assert.equal(requests.length, 0, "a backend with neither read is not asked");
    routes["sessions/8/prompts?after_seq=0"] = {prompts: [row(4)], total: 1, more: false};
    const compact = new api.PromptGutter(makeView(5, 8));
    await compact.refreshIndex();
    assert.deepEqual(requests, [[5, "sessions/8/prompts?after_seq=0"]]);
    requests.length = 0;
    const page = api.PROMPT_INDEX_PAGE;
    const full = Array.from({length: page}, (_, i) => userEvent(1000 + i * 2, i % 50 === 7));
    routes[`sessions/9/events?kind=user&limit=${page}`] = {events: full};
    routes[`sessions/9/events?kind=user&limit=${page}&before_seq=1000`] = {events: [userEvent(5), userEvent(9, true), userEvent(12)]};
    const older = new api.PromptGutter(makeView(4, 9));
    await older.refreshIndex();
    assert.deepEqual(requests.map(r => r[1]), [`sessions/9/events?kind=user&limit=${page}`,
      `sessions/9/events?kind=user&limit=${page}&before_seq=1000`], "an older node's event pages, newest first");
    assert.equal(older.index.size, page + 3);
    assert.ok(older.index.get(9).steering && older.index.get(12).text === "p12", "the line read from the event");
  }
  console.log("PASS: the index read from the node's compact rows a page at a time, then only after its last prompt, " +
              "whole again when it stops adding up, an older node's event pages, and no read from a node with neither");

  /* ---- pins, numbers and the gutter's room ---- */
  {
    const view = makeView(0, 9);
    view.oldestSeq = 40; view.newestSeq = 90;
    const gutter = new api.PromptGutter(view);
    // the index knows two older prompts and a steer the page does not hold
    for (const [seq, steering] of [[3, false], [8, true], [20, false]])
      gutter.index.set(seq, {seq, at: 1000 + seq, steering, text: "old " + seq});
    bubble(view, 41, 10);
    bubble(view, 55, 300, {steering: true});
    bubble(view, 60, 700);
    bubble(view, 90, 1400);
    view.scroll.scrollTop = 0;
    gutter.render();
    assert.deepEqual(pins(view).map(p => [p.number, p.steer, p.time, p.label]), [
      ["3", false, "t1041", "Prompt 3 · t1041"], ["", true, "t1055", "Steered prompt · t1055"],
      ["4", false, "t1060", "Prompt 4 · t1060"], ["5", false, "t1090", "Prompt 5 · t1090"]],
      "numbers count from the session's first prompt, steers aside");
    assert.equal(gutter.index.get(60).text, "prompt 60", "a prompt on the page joins with its line");
    assert.deepEqual(pins(view).map(p => p.top), [14, 304, 704, 1404], "each pin beside its bubble");
    const centre = layout.column - 52 + 20;
    assert.ok(pins(view).every(p => p.left === centre), "on one line, 32px before the column");
    assert.equal(view.root.style["--prompt-gutter"], "0px", "a column with its own margin takes no room");
    assert.equal(parseFloat(gutter.thread.style.left), BOX.left + centre);
    const plates = [...view.root.querySelectorAll(".prompt-plate")];
    assert.deepEqual(plates.map(p => p.getAttribute("aria-label")), ["Previous prompt", "Next prompt"]);
    assert.equal(parseFloat(plates[0].style.top), BOX.top + 8);
    assert.equal(parseFloat(plates[1].style.top), BOX.top + layout.height - 8 - 32);
    assert.equal(parseFloat(plates[0].style.left), BOX.left + centre - 16, "the plates on the thread's line");
    const list = view.root.querySelector(".prompt-list-button");
    assert.equal(list.getAttribute("aria-label"), "All prompts");
    assert.equal(parseFloat(list.style.top), BOX.top + 8 + 32 + 10, "the list button under the top plate");
    assert.equal(parseFloat(list.style.left), BOX.left + centre - 13, "on the thread's line");
    // a narrow column: the gutter takes its room, and every column shares it
    layout.width = 800; layout.column = 16;
    view.scroll.clientWidth = 800;
    view.scroll.scrollTop = 1400;   // the reader at the transcript's foot
    gutter.render();
    assert.equal(view.root.style["--prompt-gutter"], api.PROMPT_GUTTER + "px");
    assert.equal(view.scroll.scrollTop, view.scroll.scrollHeight, "the narrower column keeps them at its foot");
    // a hidden transcript measures nothing: it keeps its gutter, so showing it again moves nothing
    view.scroll.clientHeight = 0; view.scroll.clientWidth = 0;
    gutter.render();
    assert.equal(view.root.style["--prompt-gutter"], api.PROMPT_GUTTER + "px", "hidden is not narrow");
    assert.ok(!gutter.plates.classList.contains("hidden"));
    view.scroll.clientHeight = layout.height; view.scroll.clientWidth = 800;
    view.scroll.scrollTop = 0;
    gutter.render();
    // too narrow for a gutter, a filtered transcript, no prompts: none at all
    layout.width = 500; view.scroll.clientWidth = 500;
    gutter.render();
    assert.ok(gutter.layer.classList.contains("hidden") && gutter.plates.classList.contains("hidden"));
    assert.equal(view.root.style["--prompt-gutter"], "0px");
    layout.width = 1200; layout.column = 150; view.scroll.clientWidth = 1200;
    view.chatLogMask = 6;
    gutter.render();
    assert.ok(gutter.layer.classList.contains("hidden"), "Questions filtered out: no gutter");
    view.chatLogMask = 7;
    gutter.render();
    assert.ok(!gutter.layer.classList.contains("hidden"));
  }
  console.log("PASS: a pin per prompt beside its bubble, numbered from the session's first prompt with steers as beads, " +
              "the list button under the top plate, the room taken only from a narrow column, no gutter when too narrow or filtered");

  /* ---- scrolling: the thread, tucked pins, the lit prompt, the plates' reach ---- */
  {
    const view = makeView(0, 10);
    view.oldestSeq = 40; view.newestSeq = 90;
    const gutter = new api.PromptGutter(view);
    gutter.index.set(3, {seq: 3, at: 1, steering: false, text: "old"});
    bubble(view, 41, 10);
    bubble(view, 60, 700);
    bubble(view, 90, 1400);
    gutter.render();
    // the pins' heights are measured by a render, never by a scroll
    for (const pin of view.scroll.querySelectorAll(".prompt-pin")) pin.offsetHeight = 34;
    gutter.render();
    assert.ok(gutter.layout.every(entry => entry.height === 34));
    assert.equal(parseFloat(gutter.thread.style.top), BOX.top + 8 + 16, "the thread starts under the top plate");
    assert.equal(parseFloat(gutter.thread.style.height), layout.height - 48, "and ends under the bottom one");
    const still = [gutter.thread.style.top, gutter.thread.style.height];
    view.scroll.scrollTop = 680;
    gutter.follow();
    assert.deepEqual([gutter.thread.style.top, gutter.thread.style.height], still, "and a scroll leaves it where it is");
    let state = pins(view);
    assert.ok(state[1].tucked, "a pin behind the top plate fades");
    assert.ok(state[1].on && !state[0].on && !state[2].on, "the prompt being read is lit");
    assert.equal(gutter.current, 60);
    // the list button covers the gutter under the plate as the plate does
    view.scroll.scrollTop = 700 - 70;
    gutter.follow();
    assert.ok(pins(view)[1].tucked, "a pin under the list button fades");
    view.scroll.scrollTop = 700 - api.PROMPT_LANDING;
    gutter.follow();
    assert.ok(!pins(view)[1].tucked && pins(view)[1].on, "a landed prompt stands clear of the button, lit");
    // the plates: the prompt above by place, the next below by place
    assert.equal(gutter.neighbour(-1), 41);
    assert.equal(gutter.neighbour(1), 90);
    // at the top of the page, above lies the index's prompt, not the first
    // one on the page, which cannot be scrolled any higher
    view.scroll.scrollTop = 0;
    assert.equal(gutter.neighbour(-1), 3, "a prompt not on the page");
    // past the last prompt no prompt follows, but the newest message does until the foot
    const press = (plate, detail = 1) => plate.onclick({detail});
    view.scroll.scrollTop = 1400 - api.PROMPT_LANDING;
    gutter.follow();
    assert.ok(!gutter.down.disabled && !gutter.up.disabled, "short of the foot the bottom plate stays live");
    assert.equal(gutter.down.getAttribute("aria-label"), "Newest message");
    press(gutter.down);
    assert.deepEqual([view.newest, view.jumps.length], [1, 0], "a press past the last prompt goes to the newest message");
    view.scroll.scrollTop = 1400;   // the foot: nothing further
    gutter.follow();
    assert.ok(gutter.down.disabled, "at the newest message the bottom plate greys");
    view.detached = true;           // a window of older history: the newest is not on the page
    gutter.follow();
    assert.ok(!gutter.down.disabled, "a window of older history still has its newest to go to");
    view.detached = false;
    // short of a next prompt: one press steps to it, the second of a double click goes on to the newest
    view.scroll.scrollTop = 0;
    gutter.follow();
    assert.equal(gutter.down.getAttribute("aria-label"), "Next prompt");
    press(gutter.down);
    assert.deepEqual([view.jumps, view.newest], [[60], 1]);
    press(gutter.down, 2);
    assert.deepEqual([view.jumps, view.newest], [[60], 2], "the double click's second press: the newest");
    press(gutter.up, 2);
    assert.equal(view.newest, 2, "the top plate's double click is two steps, never the newest");
    view.jumps.length = 0;
    view.newest = 0;
    view.scroll.scrollTop = 1400 - api.PROMPT_LANDING;
    gutter.follow();
    // a window of older history: below lies the index's prompt after the page, not loaded yet
    gutter.index.set(120, {seq: 120, at: 1, steering: false, text: "newer"});
    gutter.order = null;
    assert.equal(gutter.neighbour(1), 120, "a prompt after the page");
    assert.equal(gutter.firstAfter(90), 120);
    assert.equal(gutter.lastBefore(41), 3);
    assert.equal(gutter.lastBefore(3), null);
    gutter.index.delete(120);
    gutter.order = null;
    gutter.index.delete(3);
    gutter.order = null;
    view.scroll.scrollTop = 0;
    gutter.follow();
    assert.ok(gutter.up.disabled && !gutter.down.disabled, "a step that would move nothing is no step");
    // with no prompt of the page at the reading line, the one being read is the newest before the page
    gutter.index.set(3, {seq: 3, at: 1, steering: false, text: "old"});
    gutter.order = null;
    view.scroll.scrollTop = 0;
    layout.places.set(view.inner.children[0], 300);
    gutter.render();
    assert.equal(gutter.current, 3, "the prompt the page's first lines answer");
    layout.places.set(view.inner.children[0], 10);
    gutter.render();
    // at the bottom, a last prompt the transcript cannot lift to the button is no step either
    view.scroll.scrollHeight = 1500;
    view.scroll.scrollTop = 900;
    gutter.follow();
    assert.ok(gutter.down.disabled, "the last prompt already as high as it can go");
    assert.ok(pins(view)[2].on, "and at the transcript's end the last prompt in view is the one lit");
    view.scroll.scrollHeight = 2000;
    view.scroll.scrollTop = 0;
    // a press steps through the view, a pin's press to its own prompt
    gutter.follow();
    gutter.down.onclick();
    assert.deepEqual(view.jumps, [60]);
    view.scroll.querySelectorAll(".prompt-pin")[2].onclick();
    assert.deepEqual(view.jumps, [60, 90]);
  }
  console.log("PASS: the thread from plate to plate, still while the pins scroll, pins faded behind a plate or the list " +
              "button, the prompt being read lit, the plates reaching the prompt above or below and into history not " +
              "on the page, greyed at either end");

  /* ---- a step's landing, live prompts, the teardown ---- */
  {
    const view = makeView(0, 11);
    view.oldestSeq = 1; view.newestSeq = 20;
    const gutter = new api.PromptGutter(view);
    const first = bubble(view, 2, 100);
    const second = bubble(view, 9, 900);
    gutter.render();
    scrolls.length = 0;
    gutter.land(second);
    assert.deepEqual(scrolls, [900 - api.PROMPT_LANDING], "under the list button");
    assert.ok(second.classList.contains("search-flash"));
    gutter.land(first);
    assert.ok(first.classList.contains("search-flash") && !second.classList.contains("search-flash"),
              "one flash at a time");
    // a live prompt joins the index and gets its pin on the next frame
    bubble(view, 21, 1600, {steering: true});
    gutter.schedule();
    flush();
    assert.deepEqual(pins(view).map(p => [p.number, p.steer]), [["1", false], ["2", false], ["", true]]);
    assert.ok(gutter.index.get(21).steering);
    // a bubble gone takes its pin with it
    first.remove();
    gutter.render();
    assert.equal(pins(view).length, 2);
    gutter.toggleList();
    assert.ok(document.body.querySelector(".prompt-list"));
    gutter.destroy();
    assert.ok(observers.filter(o => o.on).length < observers.length, "its observers are let go");
    assert.ok(!view.root.querySelector(".prompt-thread"), "and its thread goes with it");
    assert.ok(!document.body.querySelector(".prompt-list"), "and its open list");
  }
  console.log("PASS: a step lands its prompt under the list button with one flash at a time; live prompts join with " +
              "their pins, removed bubbles take theirs, the teardown lets go");

  /* ---- the prompt list over six hundred prompts ---- */
  {
    const view = makeView(0, 12);
    view.oldestSeq = 5000; view.newestSeq = 5100;
    const gutter = new api.PromptGutter(view);
    const words = ["checkout", "coupon", "flaky test", "release notes", "translate"];
    for (let i = 1; i <= 600; i++) {
      const seq = i * 8, steering = i % 25 === 3;
      gutter.index.set(seq, {seq, at: 1000 + i, steering, text: `Prompt about ${words[i % words.length]} number ${i}`});
    }
    bubble(view, 5004, 900, {text: "Prompt about the newest thing"});
    gutter.render();
    view.scroll.scrollTop = 0;
    gutter.follow();
    assert.equal(gutter.current, 4800, "the prompt being read is the newest before the page");
    const button = view.root.querySelector(".prompt-list-button");
    button.onclick();
    const panel = document.body.querySelector(".prompt-list");
    assert.ok(panel && panel.classList.contains("choice-menu") && panel.classList.contains("dyn"),
              "one of the shared floats");
    assert.equal(panel._anchor, button);
    assert.equal(button.getAttribute("aria-expanded"), "true");
    const filter = panel.querySelector(".prompt-list-filter");
    const rows = panel.querySelector(".prompt-list-rows");
    assert.equal(document.activeElement, filter, "the filter takes the keys");
    assert.equal(filter.getAttribute("aria-controls"), rows.id);
    assert.equal(panel.querySelector(".prompt-list-count").textContent, "577", "the numbered prompts, the one on the page too");
    const space = panel.querySelector(".prompt-list-space");
    const total = gutter.index.size;
    assert.equal(parseFloat(space.style.height), total * api.PROMPT_LIST_ROW, "room for every row");
    const drawn = () => [...space.querySelectorAll(".prompt-list-row")];
    const bound = 12 + 2 * api.PROMPT_LIST_OVERSCAN + 1;
    assert.ok(drawn().length <= bound, `only the rows in view are drawn (${drawn().length})`);
    // the prompt being read: active, lit, in the middle of the list's view
    const current = drawn().find(node => node.classList.contains("current"));
    assert.ok(current && current.classList.contains("active"));
    assert.equal(current._seq, 4800);
    assert.equal(filter.getAttribute("aria-activedescendant"), current.id);
    assert.ok(rows.scrollTop > 0 && Math.abs(rows.scrollTop + 6 * 32 - current._at * 32) < 40, "and in view");
    assert.equal(parseFloat(current.style.top), current._at * api.PROMPT_LIST_ROW);
    assert.equal(current.querySelector(".prompt-list-num").textContent, "576");
    assert.match(current.querySelector(".prompt-list-text").textContent, /number 600/);
    assert.equal(current.querySelector(".prompt-list-time").textContent, "s1600", "the console's one stamp");
    // the list's own scroll draws the rows it reaches, and lets the rest go
    rows.scrollTop = 0;
    rows.dispatchEvent(new FakeEvent("scroll"));
    flush();
    assert.equal(drawn()[0]._seq, 8, "the first prompt at the top");
    assert.deepEqual(drawn().map(node => node._at), drawn().map((_, i) => i), "the rows in reading order");
    assert.equal(drawn()[0].getAttribute("aria-posinset"), "1");
    assert.equal(drawn()[0].getAttribute("aria-setsize"), String(total), "a window of the whole list");
    assert.ok(drawn().length <= bound && drawn().every(node => node._at < bound));
    const steer = drawn().find(node => node.classList.contains("steer"));
    assert.ok(steer && steer.querySelector(".prompt-list-num").textContent === "", "a steer is a bead with no number");
    const beside = by => drawn().find(node => node._at === steer._at + by);
    assert.ok(steer.classList.contains("rail-up") && beside(-1).classList.contains("rail-down") &&
              !steer.classList.contains("rail-down") && !beside(1).classList.contains("rail-up") &&
              !beside(-1).classList.contains("rail-up"), "a steer hangs on a rail from the prompt it was steered into");
    // the keys: down, up, a page, a pick
    key(filter, "ArrowDown");
    const active = () => drawn().find(node => node.classList.contains("active"));
    assert.ok(rows.scrollTop > 0, "the active row is kept in view");
    const at = active()._at;
    key(filter, "ArrowUp");
    assert.equal(active()._at, at - 1);
    key(filter, "PageUp");
    assert.ok(active()._at < at - 5, "a page at a time");
    key(filter, "PageDown");
    assert.equal(active()._at, at - 1, "a page back and a page forward");
    key(filter, "PageDown");
    assert.equal(active()._at, at, "the last row is as far as a page goes");
    // the filter: by text, by number, nothing
    filter.value = "flaky";
    filter.dispatchEvent(new FakeEvent("input"));
    assert.equal(panel.querySelector(".prompt-list-count").textContent, "120 found");
    assert.equal(rows.scrollTop, 0);
    assert.ok(drawn().every(node => /flaky/.test(node.textContent)));
    assert.equal(active()._at, 0, "the first match is the one Enter takes");
    // a filter can put any row above a steer: no rail from a prompt it was not steered into
    filter.value = "release notes";
    filter.dispatchEvent(new FakeEvent("input"));
    assert.ok(drawn().filter(node => node.classList.contains("steer")).length > 1);
    assert.ok(drawn().every(node => !node.classList.contains("rail-up") && !node.classList.contains("rail-down")),
              "no rail between rows that are not neighbours in the session");
    filter.value = "#576";
    filter.dispatchEvent(new FakeEvent("input"));
    assert.deepEqual(drawn().map(node => node._seq), [4800], "a number finds its prompt");
    filter.value = "no such words";
    filter.dispatchEvent(new FakeEvent("input"));
    assert.equal(drawn().length, 0);
    assert.ok(!panel.querySelector(".prompt-list-empty").classList.contains("hidden"), "an empty list says so");
    // Escape clears a filter, then closes
    key(filter, "Escape");
    assert.equal(filter.value, "");
    assert.equal(drawn().find(node => node.classList.contains("current"))._seq, 4800, "back at the prompt being read");
    // a new index under an open list keeps the row the keys are on
    key(filter, "ArrowUp");
    const kept = active()._seq;
    gutter.index.set(9000, {seq: 9000, at: 0, steering: false, text: "a newer prompt"});
    gutter.order = null;
    gutter.render();
    assert.equal(active()._seq, kept);
    assert.equal(panel.querySelector(".prompt-list-count").textContent, "578");
    // the transcript moving under the list moves its lit row
    gutter.current = kept;
    gutter.list.markCurrent();
    assert.equal(drawn().find(node => node.classList.contains("current"))._seq, kept);
    // Enter lands the active prompt like a step and hands the keys back to the button
    key(filter, "Enter");
    assert.deepEqual(view.jumps, [kept]);
    assert.ok(!panel.isConnected && gutter.list === null);
    assert.equal(button.getAttribute("aria-expanded"), "false");
    assert.equal(document.activeElement, button);
    // the pointer: a row it passes is the active one, a click lands it
    button.onclick();
    const again = document.body.querySelector(".prompt-list");
    const target = [...again.querySelectorAll(".prompt-list-row")][3];
    const move = new FakeEvent("pointermove", {bubbles: true});
    target.querySelector(".prompt-list-text").dispatchEvent(move);
    assert.ok(target.classList.contains("active"));
    target.querySelector(".prompt-list-text").dispatchEvent(new FakeEvent("click", {bubbles: true}));
    assert.equal(view.jumps[1], target._seq);
    assert.ok(!again.isConnected);
    // the button toggles; a press outside (the shared floats) takes it away and the next press opens again
    button.onclick();
    assert.ok(document.body.querySelector(".prompt-list"));
    button.onclick();
    assert.ok(!document.body.querySelector(".prompt-list"), "the button closes its open list");
    button.onclick();
    closeAllMenus(null);
    assert.equal(button.getAttribute("aria-expanded"), "false");
    gutter.follow();
    assert.equal(gutter.list, null, "the gutter lets go of a list the shared floats took away");
    button.onclick();
    assert.ok(document.body.querySelector(".prompt-list"), "and the next press opens one");
    // Escape on an empty filter closes it; a transcript too narrow for the gutter closes it
    key(document.body.querySelector(".prompt-list-filter"), "Escape");
    assert.ok(!document.body.querySelector(".prompt-list"));
    button.onclick();
    layout.width = 500; view.scroll.clientWidth = 500;
    gutter.render();
    assert.ok(!document.body.querySelector(".prompt-list"), "no gutter, no list");
    layout.width = 1200; view.scroll.clientWidth = 1200;
  }
  console.log("PASS: the prompt list over six hundred prompts - a shared float that draws only the rows in view, the " +
              "prompt being read lit and centred, steers on a rail from their own prompt and from no other, its own " +
              "scroll, the filter by text or number, the keys and the " +
              "pointer, picks landing like a step, a new index under it, and every way it closes");
})().catch(error => { console.error(error); process.exit(1); });
