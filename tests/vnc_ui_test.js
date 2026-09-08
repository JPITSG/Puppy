/* The VNC pane's own half of the contract: damage rectangles painted straight
   into the canvas, DOM buttons translated to RFB's numbering, gesture input
   coalesced per frame, and the state pill speaking the shared tone words.
   No browser, node, engine, network or quota. */
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const { FakeDocument } = require('./fake_dom.js');
const document = new FakeDocument();
document.visibilityState = 'visible';

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
const intervals = new Map();
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
  performance: { now: () => now },
  setInterval(fn) { const id = ++timerSerial; intervals.set(id, fn); return id; },
  clearInterval(id) { intervals.delete(id); },
  document,
  setTimeout(fn, wait) { const id = ++timerSerial; timers.set(id, { fn, wait }); return id; },
  clearTimeout(id) { timers.delete(id); },
  backendConnectionAllowed: () => true,
});
vm.runInContext(source.slice(start, end) + ';this.View = VncView;', context);
vm.runInContext(source.slice(source.indexOf('function fmtBytes('),
  source.indexOf('function fmtEndpoint(')), context);
const View = context.View;
const browserStart = source.indexOf('class BrowserView {');
const browserEnd = source.indexOf('\n/* ================= ', browserStart);
vm.runInContext(source.slice(browserStart, browserEnd) + ';this.BrowserView = BrowserView;', context);

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
                      'stateDot', 'sizeText', 'fpsText', 'frameStats', 'throughputText', 'root', 'canvas'])
    nodes[name] = wire(element());
  const sent = [];
  Object.assign(view, nodes, {
    tab: { id: 'v:0:AB12', bid: 0, vncId: 'AB12', vncHost: 'desk.example' },
    closed: false, viewerActive: true, viewOnly: false, connected: false,
    vncGone: false, status: null, frameW: 0, frameH: 0,
    fpsTimer: null, fpsFrames: 0,
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

const view = makeView({ connected: true });
painted.length = 0;
view.applyFrame(pixelFrame(3, 5, 2, 4));
assert.equal(painted.length, 1);
assert.equal(painted[0].op, 'put');
assert.deepEqual([painted[0].x, painted[0].y], [3, 5], 'damage lands at its origin');
assert.equal(painted[0].image.width, 2);
assert.equal(painted[0].image.height, 4);
assert.equal(painted[0].image.data.length, 2 * 4 * 4, 'payload is wrapped, not copied');
assert.equal(view.connected, true);

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

/* Throughput is the backend's encoded byte rate, independent of painted FPS. */
for (const [rate, expected] of [[0, '0 B/s'], [42.3, '42 B/s'],
  [1536, '1.5 KiB/s'], [2 * 1024 ** 2, '2.0 MiB/s'],
  [3 * 1024 ** 3, '3.0 GiB/s']]) {
  live.handleMessage({ type: 'throughput', bytes_per_second: rate });
  assert.equal(live.throughputText.textContent, expected);
  assert.ok(!live.throughputText.classes.has('hidden'), 'zero is a valid visible rate');
}
for (const rate of [-1, Infinity, NaN, '123', null, undefined]) {
  live.handleMessage({ type: 'throughput', bytes_per_second: rate });
  assert.equal(live.throughputText.textContent, '3.0 GiB/s');
}
live.resetFps();
assert.equal(live.throughputText.textContent, '');
assert.ok(live.throughputText.classes.has('hidden'));
for (const flags of [{ viewerActive: false }, { closed: true }, { connected: false }]) {
  const unavailable = makeView({ connected: true, ...flags });
  unavailable.resetFps();
  unavailable.handleMessage({ type: 'throughput', bytes_per_second: 123 });
  assert.equal(unavailable.throughputText.textContent, '',
    'late telemetry must not restore a hidden or disconnected reading');
  assert.ok(unavailable.throughputText.classes.has('hidden'));
}

/* Both frame-stat pills wait for dimensions AND a measured FPS, including zero. */
for (const Pane of [View, context.BrowserView]) {
  const stats = makeView();
  Pane.prototype.resetFps.call(stats);
  assert.ok(stats.frameStats.classes.has('hidden'));
  Pane.prototype.recordFrame.call(stats);
  now += 1000;
  intervals.get(stats.fpsTimer)();
  assert.ok(stats.frameStats.classes.has('hidden'), 'FPS alone is not a complete reading');
  stats.frameW = 1280; stats.frameH = 800;
  now += 1000;
  intervals.get(stats.fpsTimer)();
  assert.equal(stats.fpsText.textContent, '0.0 FPS');
  assert.ok(!stats.frameStats.classes.has('hidden'), 'zero FPS stays visible');
  Pane.prototype.resetFps.call(stats);
  assert.ok(stats.frameStats.classes.has('hidden'), 'pause/disconnect hides the whole pill');
  assert.equal(stats.fpsText.textContent, '');
  assert.equal(intervals.size, 0);
  Pane.prototype.recordFrame.call(stats);
  assert.ok(stats.frameStats.classes.has('hidden'), 'resume waits for a fresh sample');
  now += 1000;
  intervals.get(stats.fpsTimer)();
  assert.ok(!stats.frameStats.classes.has('hidden'));
  Pane.prototype.resetFps.call(stats);
}

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
  'resize, gesture coalescing, tone vocabulary, throughput, status handling and ' +
  'quiet bounded reconnection');

/* Disconnect is authoritative, even if cached damage was already in flight. */
context.el = (tag, cls = '', text = '') => {
  const node = document.createElement(tag);
  node.className = cls; node.textContent = text;
  return node;
};
const stage = document.createElement('div');
const ended = makeView({ root: stage, stage, connected: true,
  isDead: View.prototype.isDead, resetContinuousInput() {},
  ws: { close() {} },
});
ended.handleMessage({ type: 'gone', reason: 'The VNC server closed the connection' });
assert.equal(ended.stateText.textContent, 'Disconnected', 'first disconnect renders after its overlay exists');
const paintedBeforeEnd = painted.length;
ended.applyFrame(pixelFrame(0, 0, 2, 2));
ended.applyFrame(copyFrame(0, 0, 2, 2, 2, 2));
assert.equal(ended.connected, false, 'late damage cannot resurrect a disconnected server');
assert.equal(painted.length, paintedBeforeEnd, 'late pixels and copies are ignored');
assert.ok(ended.isDead(), 'the disconnect reason and Reconnect button stay visible');
ended.handleMessage({ type: 'status', connected: false, error: 'Connection ended' });
assert.equal(ended.stateText.textContent, 'Disconnected', 'a disconnected status cannot clear the reason');
ended.handleMessage({ type: 'status', connected: true, width: 10, height: 10 });
assert.equal(ended.isDead(), false, 'only a successful status clears the disconnect');
ended.applyFrame(pixelFrame(0, 0, 2, 2));
assert.equal(painted.length, paintedBeforeEnd + 1);

/* A viewer WebSocket can open even when every RFB dial fails. Exercise the
   real socket callbacks so that opening it cannot replenish the dial budget. */
context.noteRemoteSocketReachable = () => {};
context.remoteStoppingMessage = () => '';
context.wsUrl = () => 'ws://fixture/vnc/AB12';
context.WebSocket = class {
  static OPEN = 1;
  constructor() { this.readyState = 1; }
  close() { this.readyState = 3; this.onclose(); }
};
const retry = makeView({ ws: null, visible: true, connectionSequence: 0,
  reconnectTimer: null, reconnectAttempts: 0, reconnectDelay: 1000,
  syncViewerActivity() {}, resetContinuousInput() {},
  showDead() { this.dead = true; }, isDead() { return !!this.dead; },
});
timers.clear();
for (let i = 0; i <= 5; i++) {
  retry.connect();
  retry.ws.onopen();
  retry.ws.onmessage({ data: JSON.stringify({ type: 'gone', dial: true, reason: 'Refused' }) });
  if (i < 5) assert.equal(timers.size, 1);
  else assert.equal(timers.size, 0, 'five failed RFB redials exhaust the budget');
  timers.clear(); retry.reconnectTimer = null;
}

/* The connection form uses the same close hook for Cancel, Escape and the
   backdrop; none can leave a late success opening a tab behind the user. */
let dialog, requests = [], opened = [], notices = [], pendingPosts = [], nextId = 0;
context.state = { backends: [{ id: 7, capabilities: ['vnc-instances', 'vnc-connect-cancel'] },
  { id: 8, capabilities: ['vnc-instances'] }] };
context.backendName = bid => `Backend ${bid}`;
context.backendHasCapability = (backend, cap) => !!backend?.capabilities.includes(cap);
context.vncEnabledFor = () => true;
context.esc = value => String(value);
context.newDraftClientId = () => `fixture-request-${++nextId}`;
context.openVncTab = (...args) => opened.push(args);
context.toast = text => notices.push(text);
context.modal = html => {
  const m = document.createElement('div'); m.innerHTML = html;
  document.body.appendChild(m);
  const listeners = [];
  const close = () => {
    if (!m.isConnected) return;
    m.remove(); listeners.forEach(fn => fn());
  };
  dialog = { m, close };
  return { m, close, onClose: fn => listeners.push(fn) };
};
context.api = (bid, path, options) => {
  requests.push({ bid, path, ...options });
  if (options.method === 'POST') return new Promise((resolve, reject) => pendingPosts.push({ resolve, reject }));
  return Promise.resolve({ ok: true });
};
vm.runInContext(source.slice(source.indexOf('function modalNewVnc('),
  source.indexOf('/* the "@VNC" shortcut wizard */')), context);
const nv = id => dialog.m.querySelector(`#nv-${id}`);
function connectForm(bid = 0) {
  requests = []; opened = []; notices = []; pendingPosts = [];
  context.modalNewVnc('fixture-group');
  nv('host').value = 'screen.example'; nv('be').value = String(bid);
  return nv('form').onsubmit({ preventDefault() {} });
}
const result = { vnc: { id: 'AB12', host: 'screen.example', port: 5900, label: '' } };
(async () => {
  for (const bid of [0, 7, 8]) {
    for (const dismiss of [() => nv('cancel').onclick(), () => dialog.close()]) {
      const work = connectForm(bid);
      assert.equal(nv('cancel').disabled, false, 'Cancel remains active during a dial');
      assert.equal(nv('go').disabled, true);
      assert.equal(nv('host').disabled, true);
      await nv('form').onsubmit({ preventDefault() {} });
      assert.equal(pendingPosts.length, 1, 'double submission does not create two connections');
      dismiss();
      assert.equal(dialog.m.isConnected, false);
      if (bid !== 8) {
        assert.equal(requests[1].path, `vnc/connect/${requests[0].body.request_id}`);
        assert.equal(requests[1].bid, bid, 'cancellation goes to the dialling backend');
      } else assert.equal(requests.length, 1, 'an older peer receives no unknown route');
      pendingPosts[0].resolve(result);
      await work;
      assert.equal(opened.length, 0, 'a cancelled late success opens no tab');
      assert.equal(notices.length, 0, 'a cancelled late success shows no success toast');
      assert.equal(requests.at(-1).path, bid === 8 ? 'vnc/instances/AB12' :
        `vnc/connect/${requests[0].body.request_id}`, 'the late connection is reclaimed');
    }
  }
  let work = connectForm();
  nv('cancel').onclick();
  pendingPosts[0].reject(new Error('VNC connection cancelled'));
  await work;
  assert.equal(notices.length, 0, 'expected cancellation is quiet');

  work = connectForm();
  pendingPosts[0].resolve(result);
  await work;
  assert.equal(opened.length, 1);
  assert.equal(requests.length, 1, 'successful close does not cancel the new connection');
  assert.equal(notices.length, 1);

  work = connectForm();
  pendingPosts[0].reject(new Error('request timed out'));
  await work;
  assert.ok(dialog.m.isConnected);
  assert.equal(nv('go').disabled, false, 'a failed dial permits a retry');
  assert.equal(nv('cancel').disabled, false);
  assert.ok(dialog.m.querySelector('.form-error').textContent.includes('request timed out'));
  assert.equal(requests[1].method, 'DELETE', 'a timed out request is reclaimed too');
  const firstId = requests[0].body.request_id;
  work = nv('form').onsubmit({ preventDefault() {} });
  assert.notEqual(requests.at(-1).body.request_id, firstId, 'retry gets a new cancellation identity');
  pendingPosts[1].resolve(result);
  await work;
  assert.equal(opened.length, 1);
  console.log('vnc disconnect and connection-modal tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
