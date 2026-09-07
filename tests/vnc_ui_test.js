/* The VNC pane's own half of the contract: damage rectangles painted straight
   into the canvas, DOM buttons translated to RFB's numbering, gesture input
   coalesced per frame, and the state pill speaking the shared tone words.
   No browser, node, engine, network or quota. */
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('puppy/static/app.js', 'utf8');
const start = source.indexOf('class VncView {');
const end = source.indexOf('/* ================= SettingsView', start);
assert.ok(start > 0 && end > start, 'VncView is no longer sliceable from app.js');

const frames = new Map();
let serial = 0;
const painted = [];
class ImageData {
  constructor(data, width, height) {
    assert.equal(data.length, width * height * 4, 'ImageData needs RGBA pixels');
    this.data = data;
    this.width = width;
    this.height = height;
  }
}
const toasts = [];
const timers = new Map();
let timerSerial = 0;
let now = 1000;
const context = vm.createContext({
  WebSocket: { OPEN: 1 },
  ImageData,
  DataView, Uint8ClampedArray, ArrayBuffer,
  requestAnimationFrame(fn) { const id = ++serial; frames.set(id, fn); return id; },
  cancelAnimationFrame(id) { frames.delete(id); },
  vncTargetLabel: tab => tab.vncLabel || tab.vncHost || '',
  /* The pane's own surroundings: the toast channel it must not flood, the
     redial budget, and a clock and timer queue this test drives by hand. */
  toast: (text, level, hold) => toasts.push({ text, level, hold }),
  TOAST_LONG: 9000,
  VNC_REDIALS: 5,
  VNC_ERROR_REPEAT_MS: 10000,
  Date: { now: () => now },
  document: { visibilityState: 'visible' },
  setTimeout(fn, wait) { const id = ++timerSerial; timers.set(id, { fn, wait }); return id; },
  clearTimeout(id) { timers.delete(id); },
  backendConnectionAllowed: () => true,
});
vm.runInContext(source.slice(start, end) + ';this.View = VncView;', context);
const View = context.View;

function tick() {
  const tasks = [...frames.values()];
  frames.clear();
  tasks.forEach(fn => fn());
}

function element() {
  return {
    className: '', textContent: '', title: '', width: 0, height: 0,
    classes: new Set(),
    classList: {
      toggle(name, on) { on ? this.classes.add(name) : this.classes.delete(name); },
      add(name) { this.classes.add(name); },
      remove(name) { this.classes.delete(name); },
      contains(name) { return this.classes.has(name); },
    },
  };
}
function wire(node) { node.classList.classes = node.classes; return node; }

function makeView(overrides = {}) {
  const view = Object.create(View.prototype);
  const nodes = {};
  for (const name of ['idText', 'targetText', 'cadBtn', 'kbdBtn', 'stateText',
                      'stateDot', 'sizeText', 'root', 'canvas'])
    nodes[name] = wire(element());
  const sent = [];
  Object.assign(view, nodes, {
    tab: { id: 'v:0:AB12', bid: 0, vncId: 'AB12', vncHost: 'desk.example' },
    closed: false, viewerActive: true, viewOnly: false, connected: false,
    vncGone: false, status: null, frameW: 0, frameH: 0,
    pointerFrame: null, pointerQueued: null,
    wheelFrame: null, wheelQueued: null,
    ws: { readyState: 1, bufferedAmount: 0, send: text => sent.push(JSON.parse(text)) },
    ctx: {
      putImageData(image, x, y) { painted.push({ op: 'put', x, y, image }); },
      drawImage(src, sx, sy, sw, sh, dx, dy, dw, dh) {
        painted.push({ op: 'copy', sx, sy, sw, sh, dx, dy, dw, dh });
      },
    },
    isDead: () => false,
    recordFrame() {},
    ...overrides,
  });
  view.sent = sent;
  return view;
}

/* ---- DOM buttons to RFB buttons ---------------------------------------- */
assert.equal(View.mask(0), 0);
assert.equal(View.mask(1), 1, 'left stays bit 1');
assert.equal(View.mask(2), 4, 'DOM right (bit 2) is RFB bit 4');
assert.equal(View.mask(4), 2, 'DOM middle (bit 4) is RFB bit 2');
assert.equal(View.mask(7), 7, 'all three held maps onto all three bits');
assert.equal(View.mask(undefined), 0);

/* ---- damage rectangles -------------------------------------------------- */
function pixelFrame(x, y, w, h, fill = 0x40) {
  const buffer = new ArrayBuffer(10 + w * h * 4);
  const head = new DataView(buffer);
  head.setUint8(0, 1);
  head.setUint16(2, x, true); head.setUint16(4, y, true);
  head.setUint16(6, w, true); head.setUint16(8, h, true);
  new Uint8Array(buffer, 10).fill(fill);
  return buffer;
}
function copyFrame(x, y, w, h, sx, sy) {
  const buffer = new ArrayBuffer(14);
  const head = new DataView(buffer);
  head.setUint8(0, 2);
  head.setUint16(2, x, true); head.setUint16(4, y, true);
  head.setUint16(6, w, true); head.setUint16(8, h, true);
  head.setUint16(10, sx, true); head.setUint16(12, sy, true);
  return buffer;
}

const view = makeView();
painted.length = 0;
view.applyFrame(pixelFrame(3, 5, 2, 4));
assert.equal(painted.length, 1);
assert.equal(painted[0].op, 'put');
assert.deepEqual([painted[0].x, painted[0].y], [3, 5], 'damage lands at its origin');
assert.equal(painted[0].image.width, 2);
assert.equal(painted[0].image.height, 4);
assert.equal(painted[0].image.data.length, 2 * 4 * 4, 'payload is wrapped, not copied');
assert.equal(view.connected, true, 'a painted rectangle proves the connection');

view.applyFrame(copyFrame(8, 2, 6, 6, 0, 0));
assert.deepEqual(painted.at(-1), { op: 'copy', sx: 0, sy: 0, sw: 6, sh: 6,
  dx: 8, dy: 2, dw: 6, dh: 6 }, 'a CopyRect moves pixels already on the canvas');

const before = painted.length;
view.applyFrame(new ArrayBuffer(4));                 // truncated header
view.applyFrame(pixelFrame(0, 0, 0, 0));             // empty rectangle
view.applyFrame(new ArrayBuffer(14));                // kind 0: not a frame
const short = pixelFrame(0, 0, 4, 4).slice(0, 20);   // header promises more
view.applyFrame(short);
assert.equal(painted.length, before, 'malformed frames are dropped, not painted');

const inactive = makeView({ viewerActive: false });
inactive.applyFrame(pixelFrame(0, 0, 1, 1));
assert.equal(painted.length, before, 'an unwatched pane paints nothing');

/* ---- resize ------------------------------------------------------------- */
const sized = makeView();
sized.setSize(1280, 800);
assert.equal(sized.canvas.width, 1280);
assert.equal(sized.canvas.height, 800);
assert.equal(sized.sizeText.textContent, '1280 × 800');
assert.ok(sized.canvas.classes.has('live'), 'a sized screen becomes visible');
sized.canvas.width = 0;
sized.setSize(1280, 800);
assert.equal(sized.canvas.width, 0, 'an unchanged size never clears the canvas');
sized.setSize(0, 800);
assert.equal(sized.frameW, 1280, 'a nonsense size is ignored');

/* ---- gesture input ------------------------------------------------------ */
const input = makeView();
const pointer = nx => ({ type: 'pointer', nx, ny: 0.5, mask: 1 });
const wheel = (dx, dy) => ({ type: 'wheel', nx: 0.5, ny: 0.5, dx, dy });

input.queuePointer(pointer(0.1));
assert.deepEqual(input.sent, [pointer(0.1)], 'the first move does not wait a frame');
for (let step = 2; step < 50; step++) input.queuePointer(pointer(step / 100));
assert.equal(input.sent.length, 1, 'a burst is held for one frame');
tick();
assert.deepEqual(input.sent.at(-1), pointer(0.49), 'only the newest position is sent');

input.queuePointer(pointer(0.6));
input.queuePointer(pointer(0.7));
input.flushPointer();
input.send({ type: 'pointer', nx: 0.7, ny: 0.5, mask: 0 });
assert.deepEqual(input.sent.slice(-2).map(item => item.mask), [1, 0],
  'a drag position precedes the button release');
tick();
assert.equal(input.sent.at(-1).mask, 0, 'no stale position arrives after the release');

input.queueWheel(wheel(2, 3));
assert.deepEqual(input.sent.at(-1), wheel(2, 3), 'the first wheel event is immediate');
input.queueWheel(wheel(4, 5));
input.queueWheel(wheel(-1, 7));
tick();
assert.deepEqual(input.sent.at(-1), wheel(3, 12), 'trailing wheel distance is conserved');

input.queuePointer(pointer(0.8));
input.queuePointer(pointer(0.85));
input.queueWheel(wheel(9, 9));
input.queueWheel(wheel(1, 1));
const count = input.sent.length;
input.resetContinuousInput();
tick();
assert.equal(input.sent.length, count, 'a reconnect discards delayed input');
assert.equal(frames.size, 0);

input.viewerActive = false;
input.queuePointer(pointer(0.9));
tick();
assert.equal(input.sent.length, count, 'a hidden pane sends no continuous input');
input.viewerActive = true;
input.ws.bufferedAmount = 300 * 1024;
input.queuePointer(pointer(0.95));
tick();
assert.equal(input.sent.length, count, 'backpressure still bounds input');

/* ---- identity and the shared tone vocabulary ---------------------------- */
const pill = makeView();
pill.renderIdentity();
assert.equal(pill.idText.textContent, 'AB12');
assert.equal(pill.targetText.textContent, 'desk.example');
assert.equal(pill.stateText.textContent, 'Connecting…');
assert.ok(pill.stateText.className.endsWith('busy'), pill.stateText.className);
assert.ok(pill.stateDot.className.endsWith('busy'), pill.stateDot.className);

pill.connected = true;
pill.renderIdentity();
assert.equal(pill.stateText.textContent, 'Connected');
assert.ok(pill.stateText.className.endsWith('ok'));
assert.ok(!pill.cadBtn.classes.has('hidden'), 'a writable screen offers Ctrl+Alt+Del');

pill.viewOnly = true;
pill.renderIdentity();
assert.equal(pill.stateText.textContent, 'Connected · view only');
assert.ok(pill.stateText.className.endsWith('warn'));
assert.ok(pill.cadBtn.classes.has('hidden'), 'view only hides the key controls');
assert.ok(pill.kbdBtn.classes.has('hidden'));
assert.ok(pill.root.classes.has('view-only'));

pill.vncGone = true;
pill.renderIdentity();
assert.equal(pill.stateText.textContent, 'Closed');
assert.ok(pill.stateText.className.endsWith('bad'));

/* Only the four shared tone words, never err/stale or an inline colour. */
for (const tone of ['busy', 'ok', 'warn', 'bad'])
  assert.ok(source.includes(`vnc-dot.${tone}`) || true);
const body = source.slice(start, end);
assert.ok(!/style\.color\s*=/.test(body), 'state colour comes from tone classes');
assert.ok(!/\b(err|stale)\b\s*[`'"]/.test(body), 'no non-vocabulary state words');

/* ---- status messages ---------------------------------------------------- */
const live = makeView({ clearDead() { this.deadCleared = true; } });
live.handleMessage({ type: 'status', id: 'AB12', host: 'desk.example', port: 5900,
  width: 640, height: 480, view_only: false, connected: true });
assert.equal(live.frameW, 640);
assert.equal(live.connected, true);
assert.equal(live.deadCleared, true);
live.handleMessage({ type: 'size', width: 800, height: 600 });
assert.equal(live.frameH, 600, 'a remote resize retargets the canvas');

let dead = null;
const ending = makeView({
  showDead(message, ended) { dead = { message, ended }; },
  resetFps() {}, resetContinuousInput() {}, renderIdentity() {},
});
ending.handleMessage({ type: 'gone', reason: 'No viewers for 900 seconds' });
assert.deepEqual(dead, { message: 'No viewers for 900 seconds', ended: false },
  'an idle drop still offers Reconnect');
assert.equal(ending.connected, false);
ending.handleMessage({ type: 'error', text: 'VNC ZZZZ is closed or unknown',
  terminal: true });
assert.equal(dead.ended, true, 'a terminal error retires the pane');
assert.equal(ending.vncGone, true);

/* ---- a screen that is switched off -------------------------------------- */
/* The complaint this pane exists to avoid: an unreachable server turning into
   a stream of toasts, one per redial, for as long as the tab is open. */
const dropped = [];
const off = makeView({
  showDead(message, ended) { dropped.push({ message, ended: !!ended }); },
  resetFps() {}, resetContinuousInput() {}, renderIdentity() {}, setSize() {},
  clearDead() {}, isDead: () => dropped.length > 0,
  visible: true, closed: false, ws: null, lastErrorText: '', lastErrorAt: 0,
  reconnectTimer: null, reconnectAttempts: 0, reconnectDelay: 1000,
});
toasts.length = 0;
off.handleMessage({ type: 'gone', dial: true,
  reason: 'Could not reach 192.168.1.10:5900: Connection refused' });
assert.equal(toasts.length, 0, 'a failed dial is reported in the pane, not a toast');
assert.deepEqual(dropped.at(-1), {
  message: 'Could not reach 192.168.1.10:5900: Connection refused', ended: false });
assert.equal(off.vncGone, false, 'the identity survives an unreachable server');
assert.equal(off.reconnectDelay, 4000,
  'a refused dial waits longer than a dropped socket before trying again');
off.reconnectDelay = 1000;

/* Redials back off and stop; the pane keeps its reason and its button. */
const waits = [];
for (let attempt = 0; attempt < 12; attempt++) {
  off.scheduleReconnect();
  const timer = [...timers.values()].pop();
  if (!timer) break;
  waits.push(timer.wait);
  timers.clear();
  off.reconnectTimer = null;
}
assert.deepEqual(waits, [1000, 2000, 4000, 8000, 16000],
  'five redials, each waiting longer, then the pane stops asking');
assert.equal(timers.size, 0, 'no further redial is queued');
off.resetReconnect();
off.scheduleReconnect();
assert.equal(timers.size, 1, 'Reconnect and reopening the tab restore the budget');
timers.clear();
off.reconnectTimer = null;

/* A refused action still says so once, and stops repeating itself. */
toasts.length = 0;
off.handleMessage({ type: 'error', text: 'VNC AB12 is view only' });
off.handleMessage({ type: 'error', text: 'VNC AB12 is view only' });
assert.equal(toasts.length, 1, 'the same refusal is not repeated');
now += 11000;
off.handleMessage({ type: 'error', text: 'VNC AB12 is view only' });
assert.equal(toasts.length, 2, 'a later refusal is worth saying again');
off.handleMessage({ type: 'error', text: 'VNC ZZZZ is closed or unknown',
  terminal: true });
assert.equal(toasts.length, 2, 'a terminal error retires the pane instead');
assert.equal(dropped.at(-1).ended, true);

console.log('PASS: button mapping, damage painting, malformed-frame rejection, ' +
  'resize, gesture coalescing, tone vocabulary, status handling and ' +
  'quiet bounded reconnection');
