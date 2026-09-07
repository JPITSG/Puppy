/* Cursor lifecycle against the real BrowserView methods; no browser/network. */
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('puppy/static/app.js', 'utf8');
const start = source.indexOf('class BrowserView {');
const end = source.indexOf('/* ================= SettingsView', start);
const timers = new Map();
let serial = 0;
const context = vm.createContext({
  WebSocket: { OPEN: 1 },
  setTimeout(fn, delay) { const id = ++serial; timers.set(id, {fn, delay}); return id; },
  clearTimeout(id) { timers.delete(id); },
});
vm.runInContext(source.slice(start, end) + ';this.View = BrowserView;', context);
const sent = [];
const view = Object.assign(Object.create(context.View.prototype), {
  cursorSupported: true, cursorPoint: {nx:.1, ny:.2}, cursorPending: null,
  cursorSerial: 0, cursorTimer: null, viewerActive: true, closed: false,
  screen: {style: {cursor: ''}}, ws: {readyState:1, bufferedAmount:0},
  send: data => sent.push(data),
});
function tick() {
  assert.equal(timers.size, 1);
  const [id, task] = [...timers][0];
  timers.delete(id);
  task.fn();
  return task.delay;
}
function answer(value) { view.receiveCursor({id:sent.at(-1).id, value}); }

view.queueCursor(0); tick();
assert.equal(sent.length, 1);
view.queueCursor(0);
assert.equal(timers.size, 1, 'one pending request, not one per movement');
answer('pointer');
assert.equal(view.screen.style.cursor, 'pointer');
assert.equal(tick(), 120, 'stationary pointer keeps refreshing');
answer('grabbing');
assert.equal(view.screen.style.cursor, 'grabbing');
tick();
view.cursorPoint = {nx:.8, ny:.9};
answer('text');
assert.equal(view.screen.style.cursor, 'grabbing', 'old coordinates cannot repaint');
tick(); answer('url(https://example.invalid/cursor.svg), pointer');
assert.equal(view.screen.style.cursor, 'default', 'page URLs never enter console CSS');
tick(); answer('not-allowed');
assert.equal(view.screen.style.cursor, 'not-allowed');

tick();
const stale = sent.at(-1).id;
view.resetCursor();
view.receiveCursor({id:stale, value:'pointer'});
assert.equal(view.screen.style.cursor, '');
assert.equal(timers.size, 0, 'leaving cancels all cursor timers');
view.cursorPoint = {nx:.1, ny:.2}; view.queueCursor(); tick();
view.receiveCursor({id:stale, value:'text'});
assert.equal(view.screen.style.cursor, '');
assert.equal(tick(), 1200, 'lost response expires');
assert.equal(view.screen.style.cursor, '');
tick(); answer('none');
assert.equal(view.screen.style.cursor, 'none');
view.resetCursor();
view.viewerActive = false; view.cursorPoint = {nx:0, ny:0};
view.queueCursor(); assert.equal(timers.size, 0);
view.viewerActive = true; view.cursorSupported = false;
view.queueCursor(); assert.equal(timers.size, 0, 'older backends receive no cursor requests');
view.cursorSupported = true; view.ws.bufferedAmount = 300*1024;
const before = sent.length;
view.queueCursor(); tick();
assert.equal(sent.length, before, 'backpressure also bounds cursor traffic');
view.resetCursor();
console.log('PASS: cursor keywords, stationary refresh, coalescing, stale replies, timeout, leave, inactivity, old backends and backpressure');
