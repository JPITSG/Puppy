/* Run with node tests/prompt_gutter_ui_test.js. The prompt gutter against the
   fake DOM with a modelled layout: the session's prompt index read a page at
   a time (and not asked of a node without the read), numbers that count the
   session's prompts with steers aside, a pin for every prompt on the page -
   its number or steer ring, time, label, place beside its bubble - the room
   the gutter takes from a narrow column and none from a wide one, no gutter
   for a narrow transcript, a filtered one or one with no prompts, the thread
   standing still between the plates while the pins scroll, pins faded
   behind a plate (measured once per render, never on a scroll), the lit prompt, the plates' reach
   into history not on the page, a step's landing with one flash at a time,
   live prompts joining, and the teardown. No browser, engine, network or
   quota. */
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
const context = vm.createContext({
  document, console, Math, Number, Promise, JSON, Map, Set, String, Array, Error, Object,
  window: {matchMedia: () => ({matches: false})},
  MutationObserver: Observer, ResizeObserver: Observer,
  requestAnimationFrame: fn => { frames.push(fn); return frames.length; },
  cancelAnimationFrame: () => { frames = []; },
  setTimeout: () => 0,
  fmtTime: ts => "t" + ts,
  state: {backends: [{id: 3, capabilities: []}, {id: 4, capabilities: ["session-prompt-history"]}]},
  backendHasCapability: (backend, name) => !!backend && backend.capabilities.includes(name),
  api: async (bid, route) => {
    requests.push([bid, route]);
    const answer = routes[route];
    if (answer instanceof Error) throw answer;
    return JSON.parse(JSON.stringify(answer || {events: []}));
  },
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function chevronIcon(size", "function toolsIcon("),
  between("const CHAT_LOG_ALL = 7;", "const chatLogMasks = new Map();"),
  between("/* ================= prompt gutter", "class SessionView {"),
].join("\n"), context);
const api = vm.runInContext(`({ PromptGutter, el, PROMPT_LANDING, PROMPT_GUTTER, PROMPT_INDEX_PAGE })`, context);
const flush = () => { const run = frames; frames = []; run.forEach(fn => fn()); };
const settle = () => new Promise(resolve => setImmediate(resolve));

function makeView(bid = 0, sid = 7) {
  const root = api.el("div", "view");
  const scroll = root.appendChild(api.el("div", "chat-scroll"));
  const inner = scroll.appendChild(api.el("div", "chat-inner"));
  const view = {root, scroll, inner, tab: {bid, sid}, chatLogMask: 7, oldestSeq: null, newestSeq: 0,
    jumps: [], jumpToPrompt(seq) { this.jumps.push(seq); }};
  scroll.clientWidth = layout.width;
  scroll.clientHeight = layout.height;
  scroll.scrollHeight = 2000;
  scroll.scrollTo = ({top}) => { scrolls.push(top); scroll.scrollTop = top; };
  layout.scroll = scroll;
  layout.root = root;
  layout.places = new Map();
  return view;
}
function bubble(view, seq, y, {steering = false, at = 1000 + seq} = {}) {
  const node = api.el("div", "msg msg-user", "prompt " + seq);
  node.dataset.seq = String(seq);
  node.dataset.at = String(at);
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

(async () => {
  /* ---- the index, a page at a time ---- */
  {
    const view = makeView(0, 7);
    const gutter = new api.PromptGutter(view);
    assert.ok(view.scroll.querySelector(".prompt-pins") && view.root.querySelector(".prompt-plates"),
              "the pins scroll with the transcript, the plates stand over it");
    const thread = view.root.querySelector(".prompt-thread");
    assert.ok(thread && !view.scroll.contains(thread) && thread.nextSibling === view.scroll,
              "the thread stands still under the transcript, outside the scroller");
    const page = api.PROMPT_INDEX_PAGE;
    const full = Array.from({length: page}, (_, i) => userEvent(1000 + i * 2, i % 50 === 7));
    routes[`sessions/7/events?kind=user&limit=${page}`] = {events: full};
    routes[`sessions/7/events?kind=user&limit=${page}&before_seq=1000`] = {events: [userEvent(5), userEvent(9, true), userEvent(12)]};
    await gutter.refreshIndex();
    assert.deepEqual(requests.map(r => r[1]), [`sessions/7/events?kind=user&limit=${page}`,
      `sessions/7/events?kind=user&limit=${page}&before_seq=1000`], "newest page first, then back from its oldest");
    assert.equal(gutter.index.size, page + 3);
    assert.ok(gutter.index.get(9).steering && !gutter.index.get(12).steering);
    // a node without the read is never asked; one with it is
    requests.length = 0;
    const remote = new api.PromptGutter(makeView(3, 8));
    await remote.refreshIndex();
    assert.equal(requests.length, 0, "a backend without session-prompt-history is not asked");
    routes["sessions/8/events?kind=user&limit=500"] = {events: [userEvent(2)]};
    const capable = new api.PromptGutter(makeView(4, 8));
    await capable.refreshIndex();
    assert.deepEqual(requests, [[4, "sessions/8/events?kind=user&limit=500"]]);
    // a failed read keeps what the gutter had
    routes["sessions/8/events?kind=user&limit=500"] = new Error("Backend unavailable");
    await capable.refreshIndex();
    assert.equal(capable.index.size, 1);
  }
  console.log("PASS: the session's prompt index read a page at a time, never asked of a node without the read, kept through a failed read");

  /* ---- pins, numbers and the gutter's room ---- */
  {
    const view = makeView(0, 9);
    view.oldestSeq = 40; view.newestSeq = 90;
    const gutter = new api.PromptGutter(view);
    // the index knows two older prompts and a steer the page does not hold
    for (const [seq, steering] of [[3, false], [8, true], [20, false]])
      gutter.index.set(seq, {seq, at: 1000 + seq, steering});
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
    // a narrow column: the gutter takes its room, and every column shares it
    layout.width = 800; layout.column = 16;
    view.scroll.clientWidth = 800;
    gutter.render();
    assert.equal(view.root.style["--prompt-gutter"], api.PROMPT_GUTTER + "px");
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
  console.log("PASS: a pin per prompt beside its bubble, numbered from the session's first prompt with steers as rings, the room taken only from a narrow column, no gutter when too narrow or filtered");

  /* ---- scrolling: the thread, tucked pins, the lit prompt, the plates' reach ---- */
  {
    const view = makeView(0, 10);
    view.oldestSeq = 40; view.newestSeq = 90;
    const gutter = new api.PromptGutter(view);
    gutter.index.set(3, {seq: 3, at: 1, steering: false});
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
    const state = pins(view);
    assert.ok(state[1].tucked, "a pin behind the top plate fades");
    assert.ok(state[1].on && !state[0].on && !state[2].on, "the prompt being read is lit");
    view.scroll.scrollTop = 600;
    gutter.follow();
    assert.ok(!pins(view)[1].tucked, "and shows again once clear of the plate");
    // the plates: the prompt above by place, the next below by place
    view.scroll.scrollTop = 700 - api.PROMPT_LANDING;
    gutter.follow();
    assert.equal(gutter.neighbour(-1), 41);
    assert.equal(gutter.neighbour(1), 90);
    // at the top of the page, above lies the index's prompt, not the first
    // one on the page, which cannot be scrolled any higher
    view.scroll.scrollTop = 0;
    assert.equal(gutter.neighbour(-1), 3, "a prompt not on the page");
    // past the last prompt nothing follows; before the first nothing precedes
    view.scroll.scrollTop = 1400 - api.PROMPT_LANDING;
    gutter.follow();
    assert.ok(gutter.down.disabled && !gutter.up.disabled);
    gutter.index.delete(3);
    view.scroll.scrollTop = 0;
    gutter.follow();
    assert.ok(gutter.up.disabled && !gutter.down.disabled, "a step that would move nothing is no step");
    // at the bottom, a last prompt the transcript cannot lift to the plate is no step either
    view.scroll.scrollHeight = 1500;
    view.scroll.scrollTop = 900;
    gutter.follow();
    assert.ok(gutter.down.disabled, "the last prompt already as high as it can go");
    assert.ok(pins(view)[2].on, "and at the transcript's end the last prompt in view is the one lit");
    view.scroll.scrollHeight = 2000;
    view.scroll.scrollTop = 0;
    // a press steps through the view, a pin's press to its own prompt
    gutter.down.onclick();
    assert.deepEqual(view.jumps, [60]);
    view.scroll.querySelectorAll(".prompt-pin")[2].onclick();
    assert.deepEqual(view.jumps, [60, 90]);
  }
  console.log("PASS: the thread from plate to plate, still while the pins scroll, pins faded behind a plate, the prompt being read lit, the plates reaching the prompt above or below and into history not on the page, greyed at either end");

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
    assert.deepEqual(scrolls, [900 - api.PROMPT_LANDING], "under the top plate");
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
    gutter.destroy();
    assert.ok(observers.filter(o => o.on).length < observers.length, "its observers are let go");
    assert.ok(!view.root.querySelector(".prompt-thread"), "and its thread goes with it");
  }
  console.log("PASS: a step lands its prompt under the top plate with one flash at a time; live prompts join with their pins, removed bubbles take theirs, the teardown lets go");
})().catch(error => { console.error(error); process.exit(1); });
