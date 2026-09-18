/* Run with node tests/drag_scroll_ui_test.js. The lists a reorder drag
   scrolls, against the fake DOM with a modelled layout: the speed curve
   toward either edge and past it, the frames that move the session list
   with the slot following the rows under a still pointer, the pointer off
   the sidebar parking the list and back on it moving it again, a frame the
   browser's own belt already moved adding nothing, the drop past the
   list's end still landing, the drag's end releasing the box, and the same
   for the footer's backend box and the queue. No browser, engine, network
   or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { FakeDocument, FakeElement, fire } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}

const ROW = 40;                   // every row's height in the modelled layout
const BOX = { top: 100, bottom: 300, left: 0, right: 240 };   // the scroller's box

/* A vertical list laid out under a scroller whose rows are `selector`:
   each row stands ROW tall in its current order from the box's top, less
   the scroll, so a moved slot is measured where it now stands. */
function layout(scroller, selector) {
  scroller.getBoundingClientRect = () => ({ ...BOX, width: BOX.right - BOX.left, height: BOX.bottom - BOX.top });
  scroller.clientHeight = BOX.bottom - BOX.top;
  const rows = () => scroller.querySelectorAll(selector);
  scroller.scrollHeight = rows().length * ROW;
  for (const row of rows())
    row.getBoundingClientRect = () => {
      const top = BOX.top + rows().indexOf(row) * ROW - scroller.scrollTop;
      return { top, bottom: top + ROW, left: BOX.left, right: BOX.right, width: BOX.right - BOX.left, height: ROW };
    };
}

function harness() {
  const document = new FakeDocument(), frames = [], timeouts = [], toasts = [], calls = [];
  const el = (tag, cls = "", text = "") => {
    const node = document.createElement(tag);
    node.className = cls; node.textContent = text; return node;
  };
  const state = { sessions: [], remoteSessions: {}, backends: [], remoteOk: {} };
  const context = vm.createContext({
    document, el, state, console, Date, Element: FakeElement,
    window: { matchMedia: () => ({ matches: false }), addEventListener() {}, removeEventListener() {} },
    requestAnimationFrame: fn => frames.push(fn),
    cancelAnimationFrame: id => { frames[id - 1] = null; },
    setTimeout: (fn, ms) => timeouts.push([fn, ms]),
    $: id => document.getElementById(id),
    sessionsFor: bid => bid ? state.remoteSessions[bid] || [] : state.sessions,
    sessionOrderPending: new Set(), sessionOrderNodeKey: bid => `n${bid}`,
    backendSupportsSessionPinning: () => false,
    nodeHasCapability: () => false,
    acceptSessionListPayload: () => true, refreshSessionList: async () => {},
    renderSidebar() { context.renders = (context.renders || 0) + 1; },
    api: async (bid, route, options) => { calls.push({ bid, route, ...options }); return { sessions: [] }; },
    toast: (text, tone) => toasts.push([text, tone]), TOAST_LONG: 9000,
    lsSet: (key, value) => { context.saved = [key, value]; }, NODE_ORDER_KEY: "order",
  });
  vm.runInContext([
    between("function guardNativeTouchDrag(", "/* A compact backend label"),
    between("const SLIDE_MOTION_MS =", "// below this much of the weekly allowance"),
    between("function sessionOrderRecency(", "function renderSidebar("),
    between("function sessionOrderSnapshot(", "function acceptSessionListPayload("),
    between("let dragNode = null;", "/* Whether an engine row's name opens"),
    "globalThis.scrolling = () => dragScroll;",
    "globalThis.sessionDrag = () => dragSess;",
    "globalThis.nodeDrag = () => dragNode;",
  ].join("\n"), context);
  /* the next frame, at `now` on the frame clock */
  const frame = now => {
    const pending = frames.splice(0, frames.length).filter(Boolean);
    assert.ok(pending.length <= 1, "one frame loop at a time");
    for (const fn of pending) fn(now);
    return pending.length;
  };
  return { document, el, state, context, frames, frame, timeouts, toasts, calls };
}

/* The sidebar as index.html lays it out: the list's box between the New
   session button and the search box, the footer's backend box below. */
function sidebar(h, count) {
  const side = h.el("aside", "side"); side.id = "side";
  const actions = h.el("div", "side-actions"); side.appendChild(actions);
  const scroll = h.el("div", "side-scroll"); side.appendChild(scroll);
  const groups = h.el("div"); groups.id = "sess-groups"; scroll.appendChild(groups);
  const search = h.el("div", "side-search"); side.appendChild(search);
  const foot = h.el("div", "side-foot"); side.appendChild(foot);
  const engines = h.el("div", "foot-engines"); engines.id = "foot-engines"; foot.appendChild(engines);
  h.document.body.appendChild(side);
  h.state.sessions = [];
  for (let id = 1; id <= count; id++) {
    h.state.sessions.push({ id, name: `Session ${id}`, order_at: 100 - id });
    const row = h.el("div", "sess-item");
    row.dataset.bid = "0"; row.dataset.sessionId = String(id); row.dataset.sessionKey = `0:${id}`;
    groups.appendChild(row);
    h.context.wireSessionDrag(row, 0, id);
  }
  h.context.wireSessionDropZone(groups);
  layout(scroll, ".sess-item");
  return { side, actions, scroll, groups, search, foot, engines,
    rows: () => groups.querySelectorAll(".sess-item").map(row => Number(row.dataset.sessionId)),
    row: id => groups.querySelector(`.sess-item[data-session-id="${id}"]`) };
}

const transfer = () => ({ effectAllowed: "", setData() {} });
function startDrag(h, item, x = 100, y = 120) {
  fire(item, "pointerdown", { pointerType: "mouse" });
  const event = fire(item, "dragstart", { dataTransfer: transfer(), clientX: x, clientY: y });
  h.frames.splice(0, h.frames.length).forEach(fn => fn && fn(0));   // the dimming frame
  return event;
}
const over = (target, x, y) => fire(target, "dragover", { clientX: x, clientY: y, dataTransfer: {} });

/* ---- the curve ---- */
{
  const h = harness();
  const box = h.el("div");
  box.getBoundingClientRect = () => ({ ...BOX, width: 240, height: 200 });
  const speed = (x, y) => h.context.dragScrollVelocity(box, false, x, y);
  assert.equal(speed(50, 200), 0, "the middle of the box holds still");
  assert.equal(speed(50, 148), 0, "so does the band's inner edge");
  assert.equal(speed(50, 252), 0);
  assert.ok(speed(50, 260) > 0 && speed(50, 260) < 30, "the first pixels into the band barely move it");
  assert.ok(speed(50, 276) > speed(50, 260), "deeper in moves faster");
  assert.equal(speed(50, 300), 600, "the edge itself is the full speed");
  assert.equal(speed(50, 420), 600, "and so is anywhere past it");
  assert.equal(speed(50, 100), -600, "toward the top with the opposite sign");
  assert.equal(speed(50, 20), -600);
  assert.equal(speed(50, 124), -speed(50, 276), "the same curve on both sides");
  const short = h.el("div");
  short.getBoundingClientRect = () => ({ top: 100, bottom: 160, left: 0, right: 240, width: 240, height: 60 });
  assert.equal(h.context.dragScrollVelocity(short, false, 50, 125), 0, "a short box keeps a still middle third");
  assert.ok(h.context.dragScrollVelocity(short, false, 50, 119) < 0, "and a band a third of its height");
  assert.ok(h.context.dragScrollVelocity(short, false, 50, 141) > 0);
  const wide = h.el("div");
  wide.getBoundingClientRect = () => ({ top: 0, bottom: 30, left: 100, right: 700, width: 600, height: 30 });
  assert.equal(h.context.dragScrollVelocity(wide, true, 400, 15), 0, "a strip reads its width");
  assert.equal(h.context.dragScrollVelocity(wide, true, 700, 15), 600);
  assert.equal(h.context.dragScrollVelocity(wide, true, 100, 15), -600);
  assert.equal(h.context.dragScrollVelocity(wide, true, 900, 15), 600, "past the strip's end, over the buttons");
}

/* ---- the session list ---- */
{
  const h = harness(), bar = sidebar(h, 12);   // 480px of rows in a 200px box
  const dragged = bar.row(2);
  startDrag(h, dragged);
  assert.ok(h.context.sessionDrag(), "the drag stands");
  over(bar.row(4), 100, 240);
  assert.equal(h.context.scrolling().scroller, bar.scroll, "the drag aims at the list's box");
  assert.equal(h.context.scrolling().velocity, 0, "which the middle of the list does not move");
  assert.deepEqual(bar.rows().slice(0, 5), [1, 3, 4, 2, 5], "the slot follows the pointer as before");
  assert.equal(h.frames.length, 0);

  const past = over(bar.search, 100, 330);   // held below the list, over the search box
  assert.equal(past.defaultPrevented, true, "the sidebar accepts the drag past the list's end");
  assert.equal(bar.scroll.classList.contains("reorder-scroll"), true, "the box is marked while a drag scrolls it");
  assert.deepEqual(bar.rows().slice(0, 6), [1, 3, 4, 5, 2, 6], "past the end, the row takes the last slot shown");
  assert.equal(h.context.scrolling().velocity, 600, "and the list is moving at full speed");
  assert.equal(h.frames.length, 1, "on animation frames");
  let clock = 1000;
  const frames = (count, step = 25) => { for (let i = 0; i < count; i++) h.frame(clock += step); };
  const near = (value, expected, what) => assert.ok(Math.abs(value - expected) < .01, `${what} (${value})`);
  frames(1);
  near(bar.scroll.scrollTop, 9.6, "the first frame moves a nominal 16ms");
  frames(1);
  near(bar.scroll.scrollTop, 24.6, "25ms at 600px/s is 15px more");
  frames(2, 40);
  near(bar.scroll.scrollTop, 72.6, "the frame clock paces it, not the frame count");
  frames(3);
  near(bar.scroll.scrollTop, 117.6, "and on");
  assert.deepEqual(bar.rows().slice(0, 10), [1, 3, 4, 5, 6, 7, 8, 2, 9, 10],
    "the slot follows the rows scrolling under the still pointer, at the last slot shown");
  frames(1, 300);   // a late frame counts at most 50ms
  near(bar.scroll.scrollTop, 147.6, "a stalled frame cannot jump");
  frames(12);
  assert.equal(bar.scroll.scrollTop, 280, "the list stops at its end");
  assert.deepEqual(bar.rows(), [1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 2], "the row is last of all");
  assert.equal(h.frames.length, 0, "and the loop ends with it");

  over(bar.foot, 100, 400);   // still held past the end, further down
  assert.equal(h.frames.length, 1, "each dragover offers a frame");
  assert.equal(h.frame(clock += 25), 1);
  assert.equal(bar.scroll.scrollTop, 280, "which has nowhere to go");
  assert.equal(h.frames.length, 0);

  /* back up: the pointer over the New session button, above the list */
  over(bar.actions, 100, 40);
  assert.equal(h.context.scrolling().velocity, -600);
  assert.deepEqual(bar.rows().slice(6, 9), [8, 2, 9], "above the box, the row takes the first slot shown, not one scrolled away");
  frames(1);
  near(bar.scroll.scrollTop, 270.4, "the list moves back up");
  frames(4);
  near(bar.scroll.scrollTop, 210.4, "at the same pace");
  assert.deepEqual(bar.rows().slice(4, 8), [6, 2, 7, 8], "the slot climbs the rows revealed above it");

  /* off the sidebar: the list holds where it is */
  const elsewhere = h.el("div", "main"); h.document.body.appendChild(elsewhere);
  const away = over(elsewhere, 600, 40);
  assert.equal(away.defaultPrevented, false, "the workspace does not take a session row");
  assert.equal(h.context.scrolling().velocity, 0, "the pointer off the sidebar parks the list");
  const parked = bar.scroll.scrollTop;
  frames(1);
  assert.equal(bar.scroll.scrollTop, parked, "a parked list does not move");
  assert.equal(h.frames.length, 0, "and its loop ends");
  over(bar.actions, 100, 40);
  frames(1);
  assert.ok(bar.scroll.scrollTop < parked, "back over the sidebar, it moves again");

  /* a frame something else moved: the browser's own belt, a wheel */
  over(bar.row(3), 100, 110);   // in the band, inside the list
  assert.ok(h.context.scrolling().velocity < 0 && h.context.scrolling().velocity > -600, "inside the band, gently");
  frames(1);
  const ours = bar.scroll.scrollTop;
  bar.scroll.scrollTop = ours - 11;   // the belt took 11px between frames
  frames(1);
  assert.equal(bar.scroll.scrollTop, ours - 11, "a frame the browser scrolled adds nothing");
  frames(1);
  assert.ok(bar.scroll.scrollTop < ours - 11, "the next one is ours again");

  /* let go past the end: the reorder lands */
  bar.scroll.scrollTop = 280;
  over(bar.foot, 100, 400);
  const drop = fire(bar.foot, "drop", { clientX: 100, clientY: 400, dataTransfer: {} });
  assert.equal(drop.defaultPrevented, true, "a drop past the list's end is a drop");
  assert.equal(h.context.sessionDrag(), null);
  assert.equal(h.context.scrolling(), null, "the drop stops the scroll");
  assert.equal(bar.scroll.classList.contains("reorder-scroll"), false, "and releases the box");
  assert.equal(h.calls.length, 1, "one reorder request");
  assert.equal(h.calls[0].route, "sessions/reorder");
  assert.deepEqual(h.calls[0].body.order, [1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 2], "for the order the drag left");
  assert.deepEqual(h.toasts, []);
  fire(dragged, "dragend");
  assert.equal(h.context.scrolling(), null);
}

/* a drag ended without a drop lets the box go and puts the rows back */
{
  const h = harness(), bar = sidebar(h, 12);
  startDrag(h, bar.row(2));
  over(bar.search, 100, 330);
  h.frame(1000);
  h.frame(1025);
  assert.ok(bar.scroll.scrollTop > 0);
  assert.equal(h.frames.length, 1);
  fire(bar.row(2), "dragend");   // Escape, or let go over the workspace
  assert.equal(h.context.scrolling(), null, "the end stops the scroll");
  assert.equal(bar.scroll.classList.contains("reorder-scroll"), false);
  assert.equal(h.frames.filter(Boolean).length, 0, "its frame is cancelled");
  assert.deepEqual(bar.rows(), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], "the rows go back");
  assert.equal(h.calls.length, 0, "nothing is sent");
}

/* ---- the footer's backend box ---- */
{
  const h = harness(), bar = sidebar(h, 3);
  const box = bar.engines;
  for (let i = 1; i <= 8; i++) {
    const group = h.el("div", "foot-engine-group");
    group.dataset.nodeKey = `node${i}`;
    const head = h.el("div", "foot-engine-head");
    group.appendChild(head);
    box.appendChild(group);
    h.context.wireNodeGroupDrag(group, head, `node${i}`);
  }
  h.context.wireNodeGroupDropZone(box);
  layout(box, ".foot-engine-group");   // 320px of groups in a 200px box
  const order = () => box.querySelectorAll(".foot-engine-group").map(group => group.dataset.nodeKey);
  const head = box.children[0].children[0];
  startDrag(h, head);
  assert.ok(h.context.nodeDrag(), "the group drag stands");
  const past = over(bar.search, 100, 60);   // the search box stands above the footer
  assert.equal(past.defaultPrevented, true, "the sidebar takes a group past its box");
  assert.equal(h.context.scrolling().scroller, box, "and scrolls the box");
  assert.equal(h.context.scrolling().velocity, -600);
  assert.equal(box.classList.contains("reorder-scroll"), true);
  box.scrollTop = 40;
  const below = over(bar.foot, 100, 360);
  assert.equal(below.defaultPrevented, true);
  assert.equal(h.context.scrolling().velocity, 600, "past its bottom edge too");
  h.frame(1000);
  h.frame(1025);
  assert.ok(box.scrollTop > 40 && box.scrollTop < 120, `the box scrolls (${box.scrollTop})`);
  for (let at = 1050; at <= 1300; at += 25) h.frame(at);
  assert.equal(box.scrollTop, 120, "to its end");
  assert.deepEqual(order(), ["node2", "node3", "node4", "node5", "node6", "node7", "node8", "node1"],
    "with the group last");
  const drop = fire(bar.foot, "drop", { clientX: 100, clientY: 360, dataTransfer: {} });
  assert.equal(drop.defaultPrevented, true);
  assert.equal(h.context.scrolling(), null);
  assert.equal(box.classList.contains("reorder-scroll"), false);
  assert.deepEqual(JSON.parse(h.context.saved[1]), ["node2", "node3", "node4", "node5", "node6", "node7", "node8", "node1"],
    "the order the drag left is saved");
  /* a session drag over the footer never moves the backend box */
  h.context.saved = null;
  startDrag(h, bar.row(1));
  over(bar.foot, 100, 360);
  assert.equal(h.context.scrolling().scroller, bar.scroll, "a row's drag scrolls the session list");
  fire(bar.row(1), "dragend");
}

/* ---- the queue box ---- */
{
  const h = harness();
  const methods = [
    between("  prepareQueueDrag(row, event) {", "  queueReorderComplete(message) {"),
  ].join("\n");
  const sent = [];
  vm.runInContext(`
    class QueueHost {
      constructor(box) {
        this.queueEl = box; this.queueDrag = null; this.queueDragSeq = 0; this.queueRevision = 3;
        this.queued = []; this.held = []; this.pausedQueue = []; this.queuePendingRequest = "";
        this.ws = { readyState: 1, send: text => this.sent.push(JSON.parse(text)) };
        this.sent = []; this.painted = 0;
      }
      renderQueue() { this.painted++; }
      ${methods}
    }
    globalThis.QueueHost = QueueHost; globalThis.WebSocket = { OPEN: 1 };
  `, h.context);
  const box = h.el("div", "queue-strip expanded");
  h.document.body.appendChild(box);
  box.appendChild(h.el("div", "q-head", "queued · 9"));
  const host = new h.context.QueueHost(box);
  const paint = () => {
    const live = h.el("div", "q-live-list");
    host.queued = [];
    for (let i = 0; i < 9; i++) {
      host.queued.push(`prompt ${i}`);
      const row = h.el("div", "q-item q-live");
      row.dataset.queueIndex = String(i);
      live.appendChild(row);
      host.wireQueueDrag(row);
    }
    host.wireQueueDropZone(live);
    box.appendChild(live);
    return live;
  };
  let live = paint();
  const more = h.el("button", "q-more", "Show fewer"); box.appendChild(more);
  layout(box, ".q-live");   // 360px of rows in a 200px box
  const order = () => live.querySelectorAll(".q-live").map(row => Number(row.dataset.queueIndex));
  const begin = row => {
    fire(row, "pointerdown", { button: 0, pointerType: "mouse" });
    host.queueReorderReady({ request_id: host.queueDrag.requestId, ok: true, queue_revision: 3 });
    startDrag(h, row);
  };
  begin(live.children[1]);
  assert.ok(host.queueDrag && host.queueDrag.dragging, "the row drag stands once the node held the queue");
  const past = over(more, 100, 340);   // below the rows, over Show fewer
  assert.equal(past.defaultPrevented, true, "the box takes a row past its rows");
  assert.equal(h.context.scrolling().scroller, box, "and scrolls itself");
  assert.equal(h.context.scrolling().velocity, 600);
  assert.equal(box.classList.contains("reorder-scroll"), true);
  h.frame(1000);
  h.frame(1025);
  assert.ok(box.scrollTop > 0);
  assert.deepEqual(order(), [0, 2, 3, 4, 5, 1, 6, 7, 8], "the row rides the last slot shown");
  const drop = fire(more, "drop", { clientX: 100, clientY: 340, dataTransfer: {} });
  assert.equal(drop.defaultPrevented, true, "let go over Show fewer, it lands");
  assert.equal(h.context.scrolling(), null);
  assert.equal(box.classList.contains("reorder-scroll"), false);
  const reorder = host.sent.find(message => message.type === "reorder_queue");
  assert.ok(reorder, "the node is sent the order");
  assert.deepEqual([...reorder.order], order(), "the drag left");
  /* the box is wired once; a repaint's fresh list is the drag's own */
  live.remove();
  live = paint();
  layout(box, ".q-live");
  assert.equal(box.dataset.queueReorderWired, "1");
  box.scrollTop = 40;
  begin(live.children[2]);
  over(box.children[0], 100, 105);   // over the head, in the box's top band
  assert.ok(h.context.scrolling().velocity < 0, "the box scrolls up");
  assert.deepEqual(order().slice(0, 3), [0, 2, 1], "the row takes the slot at the pointer");
  box.scrollTop = 0;
  over(h.document.body, 100, 20);   // past the top of the box, off it
  assert.equal(h.context.scrolling().velocity, 0, "off the box, it holds");
  over(box.children[0], 100, 90);   // the box's own padding, above its rows
  assert.equal(h.context.scrolling().velocity, -600, "past the rows' top, the box scrolls up");
  assert.deepEqual(order().slice(0, 3), [2, 0, 1], "and the row takes the first slot shown");
  host.cancelQueueDrag(null, true);
  assert.equal(h.context.scrolling(), null, "the drag's end stops the scroll");
  assert.equal(box.classList.contains("reorder-scroll"), false);
}

console.log("PASS: reorder drags scroll their lists - the curve, the session list, the backend box and the queue");
