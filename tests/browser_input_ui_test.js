/* Immediate gesture starts with bounded trailing input and ordered releases,
   and a press the page holds until it is released, wherever the pointer goes. */
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('puppy/static/app.js', 'utf8');
const start = source.indexOf('class BrowserView {');
const end = source.indexOf('\n/* ================= ', start);
const frames = new Map(), sent = [];
let serial = 0;
const context = vm.createContext({
  WebSocket:{OPEN:1},
  requestAnimationFrame(fn) { const id=++serial; frames.set(id,fn); return id; },
  cancelAnimationFrame(id) { frames.delete(id); },
});
vm.runInContext(source.slice(start,end)+';this.View=BrowserView;', context);
const v = Object.assign(Object.create(context.View.prototype), {
  moveFrame:null,moveQueued:null,wheelFrame:null,wheelQueued:null,
  closed:false,viewerActive:true,queueCursor(){},
  ws:{readyState:1,bufferedAmount:0,send:text=>sent.push(JSON.parse(text))},
});
function tick() {
  const tasks=[...frames.values()]; frames.clear(); tasks.forEach(fn=>fn());
}
const move = x => ({type:'mouse',kind:'move',nx:x,ny:.5});
const wheel = (dx,dy) => ({type:'wheel',nx:.5,ny:.5,dx,dy,modifiers:0});

v.queueMove(move(.1));
assert.deepEqual(sent,[move(.1)],'first movement does not wait for rAF');
for(let i=2;i<100;i++)v.queueMove(move(i/100));
assert.equal(frames.size,1); assert.equal(sent.length,1);
tick();
assert.deepEqual(sent.at(-1),move(.99),'only latest trailing position is sent');
assert.equal(sent.length,2);
tick(); assert.equal(frames.size,0);

v.queueWheel(wheel(2,3));
assert.deepEqual(sent.at(-1),wheel(2,3),'first wheel does not wait for rAF');
v.queueWheel(wheel(4,5));v.queueWheel(wheel(-1,7));
tick(); assert.deepEqual(sent.at(-1),wheel(3,12),'trailing wheel distance is conserved');
tick(); assert.equal(frames.size,0);

v.queueMove(move(.2));v.queueMove(move(.3));
v.flushMove();v.send({type:'mouse',kind:'up'});
assert.deepEqual(sent.slice(-2),[move(.3),{type:'mouse',kind:'up'}]);
tick(); assert.equal(sent.at(-1).kind,'up','no late movement after release');

v.queueMove(move(.4));v.queueMove(move(.5));
v.queueWheel(wheel(2,5));v.queueWheel(wheel(3,9));
const count=sent.length;
v.resetContinuousInput(); tick();
assert.equal(sent.length,count,'hiding or reconnecting discards delayed input');
assert.equal(frames.size,0);
assert.equal(v.moveQueued,null);assert.equal(v.wheelQueued,null);

v.viewerActive=false;v.queueMove(move(.6));v.queueWheel(wheel(1,1));tick();
assert.equal(sent.length,count,'inactive viewers send no continuous input');
v.viewerActive=true;v.ws.bufferedAmount=300*1024;
v.queueMove(move(.7));v.queueWheel(wheel(1,1));tick();
assert.equal(sent.length,count,'backpressure still bounds input');
console.log('PASS: immediate gesture starts, burst coalescing, wheel distance, release ordering, lifecycle cancellation and backpressure');

/* The real wiring: a press is the page's until released. */
{
  const handlers = {};
  let dead = false;
  const captured = [];
  let cursorResets = 0;
  const screen = {
    style: {cursor: 'text'},
    getBoundingClientRect: () => ({left: 100, top: 50, width: 1000, height: 500}),
    setPointerCapture: () => assert.fail('every frame replaces the screen, and a capture goes with it'),
  };
  const out = [];
  const w = Object.assign(Object.create(context.View.prototype), {
    moveFrame: null, moveQueued: null, closed: false, viewerActive: true, screen,
    queueCursor() {}, resetCursor() { cursorResets++; },
    isDead: () => dead,
    stage: {style: {}, addEventListener: (type, fn) => { handlers[type] = fn; }, focus() {},
      setPointerCapture: id => captured.push(id)},
    ws: {readyState: 1, bufferedAmount: 0, send: text => out.push(JSON.parse(text))},
  });
  w.wireMouse();
  const event = (x, y, extra = {}) => Object.assign({clientX: x, clientY: y, button: 0, buttons: 0,
    detail: 1, target: screen, preventDefault() {}}, extra);
  const moved = (...args) => { handlers.mousemove(event(...args)); tick(); };
  const last = () => out[out.length - 1];

  // hovering says no button is held
  moved(600, 300);
  assert.equal(last().kind, 'move');
  assert.equal(last().button, 'none');
  assert.equal(last().buttons, 0);

  // the press takes the pointer for the stage, which frames never replace,
  // and keeps the cursor it was pressed with while it holds it
  handlers.pointerdown(event(1090, 60, {pointerType: 'mouse', pointerId: 7}));
  assert.deepEqual(captured, [7], 'a mouse press captures the pointer');
  handlers.mousedown(event(1090, 60, {buttons: 1}));
  assert.equal(last().kind, 'down');
  assert.equal(last().button, 'left');
  assert.equal(last().buttons, 1);
  assert.equal(w.stage.style.cursor, 'text');
  w.cursorPoint = 'pressed here';
  const resets = cursorResets;

  // a drag says so: this is what makes a scrollbar's thumb follow. A captured
  // pointer's events name the stage, which must not read as leaving the page
  moved(1090, 300, {buttons: 1, target: w.stage});
  assert.equal(cursorResets, resets, 'the cursor stays through a press');
  assert.equal(w.cursorPoint, 'pressed here');
  assert.equal(last().kind, 'move');
  assert.equal(last().button, 'left', 'a move names the button it holds');
  assert.equal(last().buttons, 1);
  assert.equal(last().clickCount, 0);
  assert.equal(last().ny, 0.5);

  // off the stage it keeps going, held to the page's edge
  moved(5000, 900, {buttons: 1});
  assert.equal(last().nx, 1, 'past the right edge is the right edge');
  assert.equal(last().ny, 1);
  assert.equal(last().button, 'left');
  moved(-400, -400, {buttons: 1});
  assert.equal(last().nx, 0);
  assert.equal(last().ny, 0);

  // the release lands wherever it happens, and after the last position
  handlers.mousemove(event(1090, 200, {buttons: 1}));
  handlers.mousemove(event(1090, 250, {buttons: 1}));
  const before = out.length;
  handlers.mouseup(event(5000, 250));
  const released = out.slice(before);
  assert.deepEqual(released.map(item => item.kind), ['move', 'up'], 'the pending position goes first');
  assert.equal(released[0].ny, 0.4);
  assert.equal(released[0].button, 'left');
  assert.equal(released[1].button, 'left');
  assert.equal(released[1].buttons, 0);
  assert.equal(released[1].nx, 1);
  assert.equal(w.stage.style.cursor, '', 'the stage gives the cursor back');
  tick();

  // and the pointer is hovering again
  moved(600, 300);
  assert.equal(last().button, 'none');
  assert.equal(last().buttons, 0);

  // a press that began elsewhere is not the page's
  const quiet = out.length;
  handlers.mouseup(event(600, 300));
  assert.equal(out.length, quiet, 'no release for a press the page never had');
  moved(600, 310, {buttons: 1});
  assert.equal(last().button, 'none', 'dragging in from outside stays a hover');
  assert.equal(last().buttons, 0);

  // each button by its own bit, the DOM's and DevTools' count
  for (const [button, mask, name] of [[2, 2, 'right'], [1, 4, 'middle']]) {
    handlers.mousedown(event(600, 300, {button, buttons: mask}));
    assert.equal(last().button, name);
    assert.equal(last().buttons, mask);
    moved(610, 300, {buttons: mask});
    assert.equal(last().button, name);
    assert.equal(last().buttons, mask);
    handlers.mouseup(event(610, 300, {button}));
    assert.equal(last().kind, 'up');
    assert.equal(last().button, name);
    assert.equal(last().buttons, 0);
  }
  // two held at once: both are said, the left one named
  handlers.mousedown(event(600, 300, {button: 2, buttons: 2}));
  handlers.mousedown(event(600, 300, {button: 0, buttons: 3}));
  assert.equal(last().buttons, 3);
  moved(620, 300, {buttons: 3});
  assert.equal(last().button, 'left');
  assert.equal(last().buttons, 3);
  handlers.mouseup(event(620, 300, {button: 0, buttons: 2}));
  assert.equal(last().button, 'left');
  assert.equal(last().buttons, 2, 'the other button is still held');
  moved(630, 300, {buttons: 2});
  assert.equal(last().button, 'right');
  handlers.mouseup(event(630, 300, {button: 2}));
  assert.equal(last().buttons, 0);
  tick();

  // a release the stage never saw is made good, so the page holds nothing
  handlers.mousedown(event(600, 300, {buttons: 1}));
  const lost = out.length;
  handlers.mousemove(event(650, 300, {buttons: 0}));
  tick();
  assert.deepEqual(out.slice(lost).map(item => [item.kind, item.button, item.buttons]),
    [['up', 'left', 0], ['move', 'none', 0]]);

  // a touch is the touch path's, and a dead view takes nothing
  captured.length = 0;
  handlers.pointerdown(event(600, 300, {pointerType: 'touch', pointerId: 8}));
  assert.deepEqual(captured, []);
  dead = true;
  const still = out.length;
  handlers.pointerdown(event(600, 300, {pointerType: 'mouse', pointerId: 9}));
  assert.deepEqual(captured, [], 'an overlay in a dead view keeps its own clicks');
  handlers.mousedown(event(600, 300, {buttons: 1}));
  handlers.mousemove(event(600, 300, {buttons: 1}));
  handlers.mouseup(event(600, 300));
  tick();
  assert.equal(out.length, still);
  dead = false;

  // a pointer that is already gone never breaks the press
  w.stage.setPointerCapture = () => { throw new Error('NotFoundError'); };
  handlers.pointerdown(event(600, 300, {pointerType: 'mouse', pointerId: 10}));
  handlers.mousedown(event(600, 300, {buttons: 1}));
  assert.equal(last().kind, 'down');
  // a screen with no layout yet sends nothing, and holds nothing
  handlers.mouseup(event(600, 300));
  screen.getBoundingClientRect = () => ({left: 0, top: 0, width: 0, height: 0});
  const blank = out.length;
  handlers.mousedown(event(600, 300, {buttons: 1}));
  handlers.mouseup(event(600, 300));
  assert.equal(out.length, blank);
  console.log('PASS: moves name the held button and mask, the stage captures the pointer and keeps its cursor, drags clamp to the page past the stage, releases land after the last position, foreign presses stay hovers, every button by its bit, a lost release is made good, touch and dead views stay out');
}
