/* Run with node tests/image_viewer_ui_test.js. The image viewer that opens
   from a sent (or waiting) image's thumbnail, against the fake DOM and a
   hand-turned clock: its geometry, the thumbnail's own button, the dialog it
   builds, its keys and wheel, mouse and touch gestures, the history reopen,
   a failed load and its teardown. No browser, engine, network or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const {FakeDocument, FakeElement, FakeEvent, fire} = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
};

/* Nothing in the fake DOM is laid out: an element answers the box a test
   gives it, and zeros otherwise. */
FakeElement.prototype.getBoundingClientRect = function () {
  const r = this._rect || {left: 0, top: 0, width: 0, height: 0};
  return {...r, right: r.left + r.width, bottom: r.top + r.height, x: r.left, y: r.top};
};
const box = (node, left, top, width, height) => { node._rect = {left, top, width, height}; };

const document = new FakeDocument();
const clock = {now: 1000, timers: [], seq: 0};
const setTimeoutFake = (fn, ms = 0) => {
  const timer = {id: ++clock.seq, fn, at: clock.now + Math.max(0, ms)};
  clock.timers.push(timer);
  return timer.id;
};
const clearTimeoutFake = id => { clock.timers = clock.timers.filter(timer => timer.id !== id); };
function advance(ms) {
  const until = clock.now + ms;
  for (;;) {
    const due = clock.timers.filter(timer => timer.at <= until).sort((a, b) => a.at - b.at)[0];
    if (!due) break;
    clock.timers = clock.timers.filter(timer => timer !== due);
    clock.now = Math.max(clock.now, due.at);
    due.fn();
  }
  clock.now = until;
}
const motion = {reduce: true};
const windowListeners = {};
const window = {
  innerWidth: 1000, innerHeight: 800,
  matchMedia: query => ({matches: /reduce/.test(query) ? motion.reduce : false}),
  addEventListener: (type, fn) => (windowListeners[type] = windowListeners[type] || []).push(fn),
  removeEventListener: (type, fn) => {
    windowListeners[type] = (windowListeners[type] || []).filter(item => item !== fn);
  },
};

/* modal() as the viewer relies on it: the dialog in a backdrop, the head the
   real one builds around the title with its close button, close listeners,
   the dismissal hook and the history reopen factory it is given. */
const dialogs = [];
function modal(html, className = "", reopen = null) {
  const back = document.createElement("div");
  back.className = "modal-backdrop";
  const m = document.createElement("div");
  m.className = "modal" + (className ? " " + className : "");
  m.innerHTML = html;
  back.appendChild(m);
  document.body.appendChild(back);
  const head = document.createElement("div");
  head.className = "modal-head";
  const heading = m.querySelector("h2");
  m.insertBefore(head, heading);
  head.appendChild(heading);
  const closeButton = document.createElement("button");
  closeButton.className = "icon-btn modal-close";
  head.appendChild(closeButton);
  const record = {m, back, className, reopen, closed: false, listeners: [], dismissal: null};
  record.close = () => {
    if (record.closed) return;
    record.closed = true;
    back.remove();
    for (const listener of record.listeners) listener();
  };
  record.dismiss = () => record.dismissal ? record.dismissal() : record.close();
  closeButton.onclick = () => record.dismiss();
  dialogs.push(record);
  return {m, close: record.close, onClose: fn => record.listeners.push(fn),
          onDismiss: fn => { record.dismissal = fn; }};
}

const context = vm.createContext({
  document, window, modal, console, Math, Number, Promise,
  setTimeout: setTimeoutFake, clearTimeout: clearTimeoutFake,
  requestAnimationFrame: fn => fn(),
  fmtBytes: bytes => bytes + " B",
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function xIcon(size)", "function bellIcon(size, off)"),
  between("function plusIcon(size)", "/* the composer's session-tools trigger"),
  between("function attachmentFileIcon(size = 18)", "function sessionPinIcon("),
  between("const ATTACHMENT_PREVIEW_TYPES", "/* Resolve the configuration at the queue tail"),
  between("function apiPath(bid, path)", "async function api(bid, path"),
  // a tool's pictures on its card; the card's other helpers are not under test
  "function toolStateInto() {} function toolInterruptedInto() {} function backgroundTaskStateInto() {}",
  "function questionMarkChosen() {} const displayValue = value => String(value || '');",
  "function linkifyInto(node, text) { node.textContent = text; return node; }",
  between("function fillToolResultInto(card, d, endedAt", "/* The task's ending decides the mark"),
].join("\n"), context);
const api = vm.runInContext(`({ attachmentChipNode, openImageViewer, openImageViewerFrom,
  fillToolResultInto, toolImageItems, el,
  imageViewerFit, imageViewerRange, imageViewerClamp, imageViewerAround, imageViewerCover,
  imageViewerInset, imageViewerVelocity, IMAGE_VIEWER_MAX_SCALE, IMAGE_VIEWER_TAP_MS })`, context);
const current = () => vm.runInContext("imageViewerOpen", context);
const close = (a, b) => Math.abs(a - b) < 1e-6;
/* values built inside the context carry its own prototypes; compare them as data */
const plain = value => JSON.parse(JSON.stringify(value));

/* ---- the geometry ---- */
assert.equal(api.imageViewerFit(2000, 1000, {width: 864, height: 664}), 0.432);
assert.equal(api.imageViewerFit(300, 200, {width: 864, height: 664}), 1, "a small picture keeps its own pixels");
assert.equal(api.imageViewerFit(0, 0, {width: 864, height: 664}), 1);
assert.equal(api.imageViewerFit(2000, 1000, {width: 0, height: 664}), 1, "an unlaid stage never scales to nothing");
assert.deepEqual(plain(api.imageViewerRange(500, 1000)), [250, 250], "a picture that fits is centred");
assert.deepEqual(plain(api.imageViewerRange(1500, 1000)), [-500, 0], "a larger one never shows a gap");
assert.equal(api.imageViewerClamp(-600, [-500, 0]), -500);
assert.equal(api.imageViewerClamp(100, [-500, 0]), 0);
assert.equal(api.imageViewerClamp(-250, [-500, 0]), -250);
assert.ok(close(api.imageViewerClamp(-600, [-500, 0], 0.35), -535), "a finger pulls past an edge reluctantly");
assert.ok(close(api.imageViewerClamp(100, [-500, 0], 0.35), 35));
{
  const before = {s: 0.5, x: 100, y: 50};
  const after = api.imageViewerAround(before, 1, 300, 250);
  assert.deepEqual(plain([after.s, after.x, after.y]), [1, -100, -150]);
  assert.deepEqual([(300 - before.x) / before.s, (250 - before.y) / before.s],
                   [(300 - after.x) / after.s, (250 - after.y) / after.s], "the point under the pointer stays put");
}
{
  const wide = api.imageViewerCover({left: 10, top: 20, width: 54, height: 54}, 2000, 1000, {left: 0, top: 0});
  assert.deepEqual(plain([wide.s, wide.x, wide.y, wide.inset]), [0.054, 10, 20, [0, 1000, 0, 0]],
                   "a wide picture covers its thumbnail anchored left, the right of it cropped away");
  const tall = api.imageViewerCover({left: 10, top: 20, width: 26, height: 54}, 100, 2000, {left: 5, top: 0});
  assert.ok(close(tall.s, 0.26) && tall.x === 5 && close(tall.y, 20 + (54 - 520) / 2));
  assert.ok(tall.inset[1] === 0 && close(tall.inset[0], (2000 - 54 / 0.26) / 2) && tall.inset[0] === tall.inset[2],
            "a tall one is cropped evenly above and below");
  assert.equal(api.imageViewerInset([1, 2, 3, 4], 5), "inset(1px 2px 3px 4px round 5px)");
}
{
  const track = [{t: 0, x: 0, y: 0}, {t: 50, x: 25, y: 0}, {t: 100, x: 50, y: 10}];
  assert.deepEqual(plain(api.imageViewerVelocity(track, 110)), {x: 0.5, y: 0.1});
  assert.deepEqual(plain(api.imageViewerVelocity(track, 300)), {x: 0, y: 0}, "a finger that stood still has no speed");
  assert.deepEqual(plain(api.imageViewerVelocity([], 300)), {x: 0, y: 0});
}
console.log("PASS: fit never past the picture's own pixels, centred and gap-free ranges, reluctant edges, zoom about a fixed point, the thumbnail's cover and crop, release speed");

/* ---- the thumbnail is the viewer's button ---- */
{
  const chip = api.attachmentChipNode({name: "shot.png", path: "/x/shot.png", preview: true}, "blob:shot");
  const view = chip.querySelector(".attach-view");
  assert.ok(view && view.tag === "button" && view.type === "button");
  assert.equal(view.getAttribute("aria-label"), "View shot.png");
  const thumb = view.querySelector(".attach-thumb");
  assert.equal(thumb.src, "blob:shot");
  assert.equal(thumb.alt, "shot.png");
  assert.ok(view.children[0] === thumb, "the picture is inside the button, beside the remove control");
  // a broken preview falls back to the named card, keeping its remove control
  const remove = document.createElement("button");
  remove.className = "attach-x";
  chip.appendChild(remove);
  thumb.onerror();
  assert.ok(chip.querySelector(".attach-view") === null);
  assert.ok(chip.classList.contains("file") && chip.querySelector(".attach-x") === remove);
  const file = api.attachmentChipNode({name: "notes.txt", path: "/x/notes.txt", preview: false, size: 4}, "");
  assert.ok(file.querySelector(".attach-view") === null, "a file card opens nothing");
}
console.log("PASS: an image chip's thumbnail is its own labelled button; a file card and a broken preview are not");

/* ---- one strip, opened from its second thumbnail ---- */
const STAGE = {width: 1000, height: 800};
function layout(viewer) {
  box(viewer.stage, 0, 0, STAGE.width, STAGE.height);
  box(viewer.head, 12, 12, 976, 36);
  box(viewer.bar, 430, 744, 140, 44);
  box(viewer.prevButton, 16, 380, 40, 40);
}
function load(viewer, width, height) {
  viewer.img.naturalWidth = width;
  viewer.img.naturalHeight = height;
  viewer.img.onload();
}
function strip(urls) {
  const row = document.createElement("div");
  row.className = "attach-strip sent";
  for (const [n, url] of urls.entries())
    row.appendChild(api.attachmentChipNode({name: "shot-" + (n + 1) + ".png", path: "/u/" + n, preview: true}, url));
  document.body.appendChild(row);
  return row;
}
const ev = (type, init = {}) => new FakeEvent(type, {bubbles: true, timeStamp: clock.now, ...init});
const key = (viewer, name, init = {}) => { const e = ev("keydown", {key: name, ...init}); viewer.m.dispatchEvent(e); return e; };
const press = (viewer, type, id, x, y, kind = "mouse") =>
  viewer.stage.dispatchEvent(ev(type, {pointerId: id, clientX: x, clientY: y, pointerType: kind, button: 0}));
function opened(urls = ["blob:a", "blob:b", "blob:c"], at = 1, size = [2000, 1000]) {
  const row = strip(urls);
  row.querySelectorAll(".attach-view")[at].onclick();
  const viewer = current();
  assert.ok(viewer, "the press opened a viewer");
  layout(viewer);
  if (size) load(viewer, ...size);
  return {viewer, row, dialog: dialogs[dialogs.length - 1]};
}
const point = (viewer, x, y) => [(x - viewer.x) / viewer.s, (y - viewer.y) / viewer.s];

{
  const row = strip(["blob:a", "blob:b", "blob:c"]);
  row.querySelectorAll(".attach-view")[1].onclick();
  const viewer = current(), dialog = dialogs[dialogs.length - 1];
  assert.equal(dialog.className, "image-viewer");
  assert.deepEqual(plain(viewer.items.map(item => item.url)), ["blob:a", "blob:b", "blob:c"], "the strip's own images, in order");
  assert.deepEqual(plain(viewer.items.map(item => item.name)), ["shot-1.png", "shot-2.png", "shot-3.png"]);
  assert.ok(viewer.items[1].thumb === row.querySelectorAll(".attach-thumb")[1], "it flies from the pressed thumbnail");
  assert.equal(viewer.index, 1);
  const m = viewer.m;
  assert.ok(m.parentNode.classList.contains("iv-backdrop"));
  assert.ok(!m.classList.contains("iv-enter"), "the entrance fade starts on the next frame");
  assert.ok(!m.classList.contains("iv-single"));
  assert.ok(document.activeElement === m, "keys reach the viewer at once");
  const caption = m.querySelector(".modal-head .iv-caption");
  assert.ok(caption && caption.querySelector("h2.iv-name") && caption.querySelector(".iv-meta"));
  assert.ok(m.querySelector(".modal-head .modal-close"));
  for (const selector of [".iv-scrim", ".iv-stage .iv-img", ".iv-status", ".iv-prev", ".iv-next",
                          ".iv-bar .iv-out", ".iv-bar .iv-level", ".iv-bar .iv-in", ".iv-bar .iv-download"])
    assert.ok(m.querySelector(selector), selector);
  const link = m.querySelector(".iv-download");
  assert.ok(link.tag === "a" && link.href === "blob:b" && link.download === "shot-2.png",
            "the bar's download saves the picture shown under its own name");
  assert.equal(link.getAttribute("aria-label"), "Download shot-2.png");
  assert.equal(viewer.name.textContent, "shot-2.png");
  assert.equal(viewer.meta.textContent, "2 of 3", "the counter shows before the picture has loaded");
  assert.ok(m.classList.contains("iv-loading"));
  assert.ok(viewer.status.classList.contains("hidden"));
  advance(250);
  assert.equal(viewer.status.textContent, "Loading…", "a slow picture says so");
  assert.ok(!viewer.status.classList.contains("hidden"));
  layout(viewer);
  load(viewer, 2000, 1000);
  assert.ok(!m.classList.contains("iv-loading") && viewer.status.classList.contains("hidden"));
  assert.equal(viewer.meta.textContent, "2000 × 1000 · 2 of 3");
  // frame: 48 head, 56 bar, 56 arrow, plus 12 of air = 68 on every side
  assert.ok(close(viewer.fit, 0.432), viewer.fit);
  assert.ok(close(viewer.s, 0.432) && close(viewer.x, 68) && close(viewer.y, 184), [viewer.s, viewer.x, viewer.y]);
  assert.equal(viewer.img.style.transform, "translate3d(68px,184px,0) scale(0.432)");
  assert.equal(viewer.img.style.width, "2000px");
  assert.equal(viewer.level.textContent, "43%");
  assert.equal(viewer.level.getAttribute("aria-label"), "Zoom to 100%");
  assert.ok(viewer.outButton.disabled && !viewer.inButton.disabled, "nothing to zoom out of at the fit");
  assert.ok(!viewer.prevButton.disabled && !viewer.nextButton.disabled);
  dialog.dismiss();
  assert.ok(dialog.closed && current() === null, "Escape, Back or the X close it at once");
  assert.deepEqual(windowListeners.resize, [], "and it stops listening to the window");
}
console.log("PASS: a thumbnail opens its strip's images on the one pressed, framed between the bars at a fit that never upscales, counted and labelled, a download for the picture shown, Loading said only when slow");

/* ---- keys ---- */
{
  const {viewer, dialog} = opened();
  key(viewer, "+");
  assert.ok(close(viewer.s, 0.648) && close(viewer.x, -148) && close(viewer.y, 76), "+ zooms about the centre");
  assert.ok(!viewer.outButton.disabled && viewer.m.classList.contains("iv-zoomed"));
  key(viewer, "-");
  assert.ok(close(viewer.s, 0.432) && close(viewer.x, 68) && close(viewer.y, 184), "- back to the fit");
  key(viewer, "-");
  assert.ok(close(viewer.s, 0.432), "never below the fit");
  key(viewer, "1");
  assert.ok(close(viewer.s, 1) && close(viewer.x, -500) && close(viewer.y, -100), "1 is actual size");
  assert.ok(viewer.m.classList.contains("iv-pannable"));
  assert.equal(viewer.level.textContent, "100%");
  assert.equal(viewer.level.getAttribute("aria-label"), "Zoom to fit");
  key(viewer, "ArrowLeft");
  assert.ok(close(viewer.x, -350), "a wide zoomed picture pans with the arrows");
  key(viewer, "ArrowUp");
  assert.ok(close(viewer.y, 0), "and never past its edge");
  assert.equal(viewer.index, 1, "panning is not paging");
  key(viewer, "0");
  assert.ok(close(viewer.s, 0.432) && !viewer.m.classList.contains("iv-pannable"), "0 fits again");
  key(viewer, "ArrowRight");
  assert.equal(viewer.index, 2, "at the fit the arrows page");
  assert.equal(viewer.name.textContent, "shot-3.png");
  assert.ok(viewer.downloadLink.href === "blob:c" && viewer.downloadLink.download === "shot-3.png",
            "paging moves the download with it");
  assert.ok(viewer.nextButton.disabled && !viewer.prevButton.disabled, "the last picture has no next");
  load(viewer, 300, 200);
  assert.equal(viewer.s, 1, "a small picture opens at its own size");
  assert.equal(viewer.level.getAttribute("aria-label"), "Zoom to 250%");
  key(viewer, "ArrowRight");
  assert.equal(viewer.index, 2, "past the last there is nothing");
  key(viewer, "PageUp");
  assert.equal(viewer.index, 1);
  load(viewer, 2000, 1000);
  key(viewer, "PageDown");
  assert.equal(viewer.index, 2);
  key(viewer, "Home");
  const control = key(viewer, "+", {ctrlKey: true});
  assert.equal(control.defaultPrevented, false, "the browser keeps its own shortcuts");
  viewer.nextButton.focus();
  const enter = new FakeEvent("keydown", {bubbles: true, key: "Enter"});
  viewer.nextButton.dispatchEvent(enter);
  assert.equal(enter.defaultPrevented, false, "Enter on a button is the button's");
  // a button disabled under the focus hands it to the dialog
  viewer.prevButton.focus();
  key(viewer, "PageUp");
  assert.ok(viewer.index === 1 && document.activeElement === viewer.prevButton);
  key(viewer, "PageUp");
  assert.ok(viewer.index === 0 && viewer.prevButton.disabled);
  assert.ok(document.activeElement === viewer.m, "the first picture's disabled previous lets go of the focus");
  dialog.dismiss();
}
console.log("PASS: + - 0 1 zoom and fit, arrows pan a picture larger than the window and page one that fits, PageUp/PageDown always page, modifier shortcuts and button keys untouched, focus kept off disabled buttons");

/* ---- wheel, buttons and the level ---- */
{
  const {viewer, dialog} = opened();
  viewer.inButton.onclick();
  assert.ok(close(viewer.s, 0.648));
  viewer.outButton.onclick();
  assert.ok(close(viewer.s, 0.432));
  viewer.level.onclick();
  assert.ok(close(viewer.s, 1), "the level goes to actual size");
  const before = point(viewer, 300, 250);
  const wheel = ev("wheel", {deltaY: -100, deltaX: 0, deltaMode: 0, clientX: 300, clientY: 250});
  viewer.stage.dispatchEvent(wheel);
  assert.ok(wheel.defaultPrevented, "the page never scrolls or zooms under the viewer");
  assert.ok(close(viewer.s, Math.exp(0.2)));
  const after = point(viewer, 300, 250);
  assert.ok(close(before[0], after[0]) && close(before[1], after[1]), "a wheel zooms about the pointer");
  const s = viewer.s;
  viewer.stage.dispatchEvent(ev("wheel", {deltaY: -3, deltaX: 0, deltaMode: 1, clientX: 300, clientY: 250}));
  assert.ok(close(viewer.s, s * Math.exp(48 * 0.002)), "lines are counted as pixels");
  const pinch = viewer.s;
  viewer.stage.dispatchEvent(ev("wheel", {deltaY: 10, deltaX: 0, deltaMode: 0, ctrlKey: true, clientX: 300, clientY: 250}));
  assert.ok(close(viewer.s, pinch * Math.exp(-0.1)), "a trackpad pinch steps finer");
  const x = viewer.x;
  viewer.stage.dispatchEvent(ev("wheel", {deltaY: 2, deltaX: 40, deltaMode: 0, clientX: 300, clientY: 250}));
  assert.ok(close(viewer.x, x - 40), "a sideways stroke pans");
  for (let n = 0; n < 40; n++)
    viewer.stage.dispatchEvent(ev("wheel", {deltaY: -400, deltaX: 0, deltaMode: 0, clientX: 300, clientY: 250}));
  assert.equal(viewer.s, api.IMAGE_VIEWER_MAX_SCALE, "never past eight times its pixels");
  assert.ok(viewer.inButton.disabled && viewer.img.classList.contains("iv-crisp"), "magnified, its pixels are square");
  viewer.level.onclick();
  assert.ok(close(viewer.s, 0.432) && !viewer.img.classList.contains("iv-crisp"), "the level fits again");
  dialog.dismiss();
}
console.log("PASS: the bar's buttons and level, a wheel about the pointer (lines, pinch steps, sideways pans), the eight-times ceiling and square pixels when magnified");

/* ---- the mouse ---- */
{
  const {viewer, dialog} = opened();
  press(viewer, "pointerdown", 1, 500, 400);
  clock.now += 40;
  press(viewer, "pointermove", 1, 560, 420);
  press(viewer, "pointerup", 1, 560, 420);
  assert.ok(close(viewer.x, 68) && !dialog.closed, "a drag at the fit moves nothing and closes nothing");
  viewer.stage.dispatchEvent(ev("dblclick", {clientX: 500, clientY: 400}));
  assert.ok(close(viewer.s, 1), "a double click on the picture zooms there");
  const [ix, iy] = point(viewer, 500, 400);
  assert.ok(close(ix, (500 - 68) / 0.432) && close(iy, (400 - 184) / 0.432));
  press(viewer, "pointerdown", 1, 500, 400);
  clock.now += 16;
  press(viewer, "pointermove", 1, 400, 350);
  assert.ok(close(viewer.x, -600) && close(viewer.y, -150), "a zoomed picture follows the drag");
  clock.now += 16;
  press(viewer, "pointermove", 1, -500, 350);
  assert.ok(viewer.x < -1000 && viewer.x > -1500, "past its edge it follows reluctantly");
  clock.now += 200;
  press(viewer, "pointerup", 1, -500, 350);
  assert.ok(close(viewer.x, -1000), "and settles back at the edge when let go");
  viewer.stage.dispatchEvent(ev("dblclick", {clientX: 500, clientY: 400}));
  assert.ok(close(viewer.s, 0.432), "a second double click fits again");
  press(viewer, "pointerdown", 1, 500, 400);
  press(viewer, "pointerup", 1, 500, 400);
  assert.ok(!dialog.closed, "a press on the picture keeps it open");
  press(viewer, "pointerdown", 1, 20, 20);
  press(viewer, "pointerup", 1, 20, 20);
  assert.ok(dialog.closed, "a press beside it closes at once");
}
console.log("PASS: a mouse drag at the fit does nothing, a double click zooms where it lands and back, a zoomed drag pans with a reluctant edge, a press beside the picture closes");

/* ---- touch ---- */
{
  const {viewer, dialog} = opened();
  press(viewer, "pointerdown", 1, 400, 400, "touch");
  press(viewer, "pointerdown", 2, 600, 400, "touch");
  press(viewer, "pointermove", 1, 300, 400, "touch");
  press(viewer, "pointermove", 2, 700, 400, "touch");
  assert.ok(close(viewer.s, 0.864) && close(viewer.x, -364) && close(viewer.y, -32), "two fingers zoom about their middle");
  press(viewer, "pointerup", 2, 700, 400, "touch");
  press(viewer, "pointermove", 1, 250, 380, "touch");
  assert.ok(close(viewer.x, -414) && close(viewer.y, -52), "the finger still down carries on panning");
  clock.now += 200;
  press(viewer, "pointerup", 1, 250, 380, "touch");
  // squeezed below the fit, it springs back to the fit
  press(viewer, "pointerdown", 1, 300, 400, "touch");
  press(viewer, "pointerdown", 2, 700, 400, "touch");
  press(viewer, "pointermove", 1, 480, 400, "touch");
  press(viewer, "pointermove", 2, 520, 400, "touch");
  assert.ok(viewer.s < 0.432 && viewer.s > 0.432 * 0.1, "below the fit the picture follows reluctantly");
  press(viewer, "pointerup", 1, 480, 400, "touch");
  press(viewer, "pointerup", 2, 520, 400, "touch");
  assert.ok(close(viewer.s, 0.432) && close(viewer.x, 68) && close(viewer.y, 184), "and settles at the fit");
  // a swipe pages; a short one springs back
  press(viewer, "pointerdown", 1, 500, 400, "touch");
  clock.now += 16;
  press(viewer, "pointermove", 1, 420, 404, "touch");
  clock.now += 200;
  press(viewer, "pointerup", 1, 420, 404, "touch");
  assert.equal(viewer.index, 1, "a short slow swipe stays");
  assert.ok(close(viewer.x, 68));
  press(viewer, "pointerdown", 1, 500, 400, "touch");
  clock.now += 16;
  press(viewer, "pointermove", 1, 290, 404, "touch");
  assert.ok(close(viewer.x, 68 - 210), "the picture follows the finger");
  press(viewer, "pointerup", 1, 290, 404, "touch");
  assert.equal(viewer.index, 2, "a long one pages");
  load(viewer, 2000, 1000);
  press(viewer, "pointerdown", 1, 500, 400, "touch");
  clock.now += 16;
  press(viewer, "pointermove", 1, 200, 404, "touch");
  assert.ok(close(viewer.x, 68 - 300 * 0.35), "past the last picture it gives way reluctantly");
  press(viewer, "pointerup", 1, 200, 404, "touch");
  assert.equal(viewer.index, 2);
  assert.ok(close(viewer.x, 68));
  // a short pull springs back; a long one puts the picture away
  press(viewer, "pointerdown", 1, 500, 400, "touch");
  clock.now += 16;
  press(viewer, "pointermove", 1, 500, 460, "touch");
  assert.ok(viewer.s < 0.432 && Number(viewer.scrim.style.opacity) < 1, "a pull shrinks the picture and lifts the veil");
  clock.now += 200;
  press(viewer, "pointerup", 1, 500, 460, "touch");
  assert.ok(close(viewer.s, 0.432) && viewer.scrim.style.opacity === "" && !dialog.closed);
  press(viewer, "pointerdown", 1, 500, 400, "touch");
  clock.now += 16;
  press(viewer, "pointermove", 1, 500, 600, "touch");
  press(viewer, "pointerup", 1, 500, 600, "touch");
  assert.ok(dialog.closed, "a long pull closes");
}
{
  const {viewer, dialog} = opened();
  const tap = (x, y) => {
    press(viewer, "pointerdown", 1, x, y, "touch");
    press(viewer, "pointerup", 1, x, y, "touch");
  };
  tap(500, 400);
  assert.ok(!viewer.m.classList.contains("iv-bare"), "a tap waits for a second one");
  advance(api.IMAGE_VIEWER_TAP_MS);
  assert.ok(viewer.m.classList.contains("iv-bare"), "one tap on the picture hides the chrome");
  tap(500, 400);
  advance(api.IMAGE_VIEWER_TAP_MS);
  assert.ok(!viewer.m.classList.contains("iv-bare"), "and the next brings it back");
  tap(500, 400);
  clock.now += 120;
  tap(505, 402);
  assert.ok(close(viewer.s, 1), "a double tap zooms where it lands");
  advance(api.IMAGE_VIEWER_TAP_MS);
  assert.ok(!viewer.m.classList.contains("iv-bare"), "and is not a single tap as well");
  tap(500, 400);
  clock.now += 120;
  tap(500, 400);
  assert.ok(close(viewer.s, 0.432));
  tap(20, 20);
  assert.ok(!dialog.closed);
  advance(api.IMAGE_VIEWER_TAP_MS);
  assert.ok(dialog.closed, "a tap beside the picture closes");
}
{
  const {viewer, dialog} = opened();
  press(viewer, "pointerdown", 1, 20, 20, "touch");
  press(viewer, "pointerup", 1, 20, 20, "touch");
  dialog.dismiss();
  const left = clock.timers.length;
  advance(api.IMAGE_VIEWER_TAP_MS);
  assert.equal(clock.timers.length, 0);
  assert.ok(left >= 0 && current() === null, "a tap pending at the close never fires");
}
console.log("PASS: a pinch about its middle handing over to a pan, a squeeze springing back to the fit, swipes paging with a reluctant end, pulls springing back or closing, taps hiding the chrome, double taps zooming, a tap beside closing");

/* ---- history, motion, failure, the window ---- */
{
  const {viewer, dialog} = opened();
  key(viewer, "ArrowRight");
  load(viewer, 2000, 1000);
  dialog.dismiss();
  assert.equal(typeof dialog.reopen, "function", "Forward has a fresh viewer to open");
  dialog.reopen();
  const again = current();
  assert.ok(again && again !== viewer, "a fresh one");
  assert.equal(again.index, 2, "on the picture last shown");
  assert.deepEqual(plain(again.items.map(item => item.url)), ["blob:a", "blob:b", "blob:c"]);
  layout(again);
  again.img.onerror();
  assert.ok(again.m.classList.contains("iv-failed"));
  assert.equal(again.status.textContent, "Could not load this image");
  assert.ok(again.status.classList.contains("bad"));
  assert.ok(again.outButton.disabled && again.inButton.disabled && again.level.disabled);
  key(again, "+");
  assert.ok(!again.ready, "a failed picture takes no zoom");
  press(again, "pointerdown", 1, 500, 400, "touch");
  clock.now += 16;
  press(again, "pointermove", 1, 290, 404, "touch");
  press(again, "pointerup", 1, 290, 404, "touch");
  assert.equal(again.index, 2, "a swipe still pages away from it");
  again.img.onerror();
  key(again, "ArrowLeft");
  assert.equal(again.index, 1, "and so do the keys");
  layout(again);
  load(again, 2000, 1000);
  assert.ok(!again.m.classList.contains("iv-failed") && !again.level.disabled);
  // the window grows: a fitted picture refits, a zoomed one keeps its zoom
  STAGE.width = 1400;
  layout(again);
  for (const fn of windowListeners.resize) fn();
  assert.ok(again.fit > 0.432 && close(again.s, again.fit), "a fitted picture refits");
  key(again, "1");
  STAGE.width = 1000;
  layout(again);
  for (const fn of windowListeners.resize) fn();
  assert.ok(close(again.s, 1), "a zoomed one keeps its zoom");
  const last = dialogs[dialogs.length - 1];
  key(again, "PageUp");
  again.img.onerror();
  press(again, "pointerdown", 1, 500, 400);
  press(again, "pointerup", 1, 500, 400);
  assert.ok(last.closed, "and a press where no picture stands closes");
}
{
  motion.reduce = false;
  const row = strip(["blob:a", "blob:b"]);
  const thumbs = row.querySelectorAll(".attach-thumb");
  box(thumbs[0], 600, 500, 54, 54);
  row.querySelectorAll(".attach-view")[0].onclick();
  const viewer = current(), dialog = dialogs[dialogs.length - 1];
  layout(viewer);
  load(viewer, 2000, 1000);
  assert.ok(viewer.img.classList.contains("iv-anim"), "it flies in");
  assert.equal(viewer.img.style.clipPath, "inset(0px 0px 0px 0px round " + 8 / 0.432 + "px)",
               "from the thumbnail's crop to the whole picture");
  assert.equal(viewer.img.style.borderRadius, 8 / 0.432 + "px", "the corners hold one radius through the flight");
  advance(0);
  assert.equal(viewer.img.style.clipPath, "", "and the crop is gone once it lands");
  // two quick presses: the second counts from the picture the first asked for
  viewer.nextButton.onclick();
  assert.equal(viewer.index, 1);
  assert.ok(viewer.img.classList.contains("iv-leaving"), "the picture leaves first");
  viewer.prevButton.onclick();
  assert.equal(viewer.index, 0);
  advance(0);
  assert.equal(viewer.img.src, "blob:a", "the first press's picture never comes");
  load(viewer, 2000, 1000);
  assert.ok(!viewer.img.classList.contains("iv-leaving"));
  // closing hands the picture to a ghost that flies home, then goes
  viewer.inButton.onclick();
  const img = viewer.img;
  dialog.dismiss();
  assert.ok(dialog.closed && current() === null);
  const ghost = document.querySelector(".iv-ghost");
  assert.ok(ghost && ghost.getAttribute("aria-hidden") === "true");
  assert.ok(img.parentNode === ghost, "the viewer's own decoded picture flies");
  assert.ok(ghost.classList.contains("iv-going") && ghost.querySelector(".iv-scrim"));
  assert.ok(img.style.transform.startsWith("translate3d(600px,500px,0)"), "to its thumbnail");
  advance(60);
  assert.ok(document.querySelector(".iv-ghost") === null, "and leaves the page once it has landed");
  // a viewer opened while a ghost still flies lands it at once
  row.querySelectorAll(".attach-view")[1].onclick();
  const next = current();
  layout(next);
  load(next, 2000, 1000);
  next.dialog.close();
  next.dialog.close();
  motion.reduce = true;
}
console.log("PASS: Forward reopens a fresh viewer on the picture last shown, a failed picture says so and still pages, the window refits a fitted picture only, the flight in and home with one radius, quick presses never show a stale picture");

/* ---- a picture a tool gave the model, on its card ---- */
{
  const card = (input) => {
    const n = api.el("div", "tool-card");
    const head = n.appendChild(api.el("div", "tool-head"));
    head.appendChild(api.el("span", "t-sum", "summary"));
    head.appendChild(api.el("span", "t-state"));
    let folds = 0;
    head.onclick = () => { folds++; n.classList.toggle("open"); };
    n.appendChild(api.el("div", "tool-body"));
    n._toolInput = input;
    n.folds = () => folds;
    return n;
  };
  const ID = "t1790890786930-9cf6575096";
  const read = card({file_path: "/tmp/shots/10-crop.png"});
  api.fillToolResultInto(read, {content: "[image]", images: [{id: ID, type: "image/png", size: 10}]}, 5,
                         {bid: 0, sid: 7});
  const head = read.querySelector(".tool-head");
  const thumb = head.querySelector(".t-thumb");
  assert.ok(thumb && thumb.tag === "button" && thumb.type === "button", "the head holds the picture's button");
  assert.ok(thumb.nextSibling === head.querySelector(".t-state"), "just before the call's state");
  assert.equal(thumb.getAttribute("aria-label"), "View 10-crop.png", "named for the file the call read");
  const small = thumb.querySelector("img");
  assert.equal(small.src, "/api/sessions/7/upload/" + ID);
  assert.equal(small.loading, "lazy", "a long history fetches only what is scrolled to");
  assert.ok(thumb.querySelector(".t-thumb-more") === null);
  const body = read.querySelector(".tool-body");
  assert.ok(body.querySelector("pre") === null, "the [image] line is the picture, not text");
  const strip = body.querySelector(".attach-strip.tool-images");
  const chip = strip.querySelector(".attach-chip.image .attach-view .attach-thumb");
  assert.ok(chip && chip.src === small.src && chip.loading === "lazy", "and under the result the sent image's chip");
  // a press on the head's picture opens the viewer, never folds the card
  const press = new FakeEvent("click");
  thumb.onclick(press);
  assert.ok(press.propagationStopped, "the card's fold never sees it");
  assert.equal(read.folds(), 0);
  const viewer = current();
  assert.ok(viewer, "the viewer opens");
  assert.deepEqual(plain(viewer.items.map(item => [item.url, item.name])), [[small.src, "10-crop.png"]]);
  viewer.dialog.close();
  assert.ok(current() === null);
  // the result chip opens the same viewer on the card's pictures
  strip.querySelector(".attach-view").onclick();
  assert.deepEqual(plain(current().items.map(item => item.name)), ["10-crop.png"]);
  current().dialog.close();

  // a capture with a caption and three pictures, on a remote backend
  const shot = card({});
  const ids = ["t1790890786931-0000000001", "t1790890786932-0000000002", "t1790890786933-0000000003"];
  api.fillToolResultInto(shot, {content: "Browser A91O\nCaptured the viewport\n[image]\n[image]\n[image]",
    images: [{id: ids[0], type: "image/png", size: 1}, {id: ids[1], type: "image/jpeg", size: 2},
             {id: ids[2], type: "image/webp", size: 3}]}, 5, {bid: 3, sid: 9});
  assert.equal(shot.querySelector(".tool-body pre").textContent, "Browser A91O\nCaptured the viewport",
               "the caption stays, its [image] lines go");
  const several = shot.querySelector(".t-thumb");
  assert.equal(several.getAttribute("aria-label"), "View 3 images");
  assert.equal(several.querySelector(".t-thumb-more").textContent, "+2");
  assert.equal(several.querySelector("img").src, "/api/b/3/sessions/9/upload/" + ids[0], "asked of the backend that holds it");
  several.onclick(new FakeEvent("click"));
  assert.deepEqual(plain(current().items.map(item => item.name)), ["image-1.png", "image-2.jpg", "image-3.webp"]);
  current().dialog.close();
  // a picture the node no longer has takes its head button with it
  several.querySelector("img").onerror();
  assert.ok(shot.querySelector(".t-thumb") === null);

  // only what the node stores is a picture: an id of its form, a raster type
  assert.deepEqual(plain(api.toolImageItems(0, 7, [{id: "../../etc", type: "image/png"},
    {id: "1790890786930-9cf6575096", type: "image/png"}, {id: ID, type: "image/svg+xml"}, null], {})), []);
  const plainResult = card({});
  api.fillToolResultInto(plainResult, {content: "[image]", images: [{id: "x", type: "image/png"}]}, 5, {bid: 0, sid: 7});
  assert.ok(plainResult.querySelector(".t-thumb") === null && plainResult.querySelector(".tool-images") === null);
  assert.equal(plainResult.querySelector("pre").textContent, "[image]", "an older result reads as it always did");
  const empty = card({});
  api.fillToolResultInto(empty, {content: ""}, 5, {bid: 0, sid: 7});
  assert.equal(empty.querySelector("pre").textContent, "(Empty)");
}
console.log("PASS: a tool's pictures on its card - a labelled lazy thumbnail in the head that opens the viewer without folding the card, the sent image's chips under the caption, local and remote, named for the file read, an unknown id or type never a picture");
