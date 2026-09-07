/* Immediate gesture starts with bounded trailing input and ordered releases. */
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
