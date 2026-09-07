/* Real frame preparation against delayed and failed image loads. */
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('puppy/static/app.js', 'utf8');
const start = source.indexOf('class BrowserView {');
const end = source.indexOf('\n/* ================= ', start);
const loads = [], released = [], presented = [], urls = new Set();
let active = 0, maxActive = 0;
class Image {
  constructor() {
    this.style={cursor:''};this.classList={add:()=>{}};
  }
  set src(url) {
    this.id=url.slice(5);active++;maxActive=Math.max(maxActive,active);loads.push(this);
  }
  replaceWith(image) { presented.push(image.id); }
  // Waiting for decode() after load can add a refresh interval. A loaded
  // replacement must be presented without that additional promise barrier.
  decode() { return new Promise(()=>{}); }
}
const context = vm.createContext({
  Blob: class { constructor(parts) { this.id=parts[0]; } }, Image,
  URL: {
    createObjectURL(blob) { const url='blob:'+blob.id;urls.add(url);return url; },
    revokeObjectURL(url) { urls.delete(url);released.push(url.slice(5)); },
  },
});
vm.runInContext(source.slice(start,end)+';this.View=BrowserView;',context);
const screen=new Image();screen.className='br-screen';screen.style.cursor='text';
const v=Object.assign(Object.create(context.View.prototype), {
  pendingFrame:null,preparingFrame:false,frameEpoch:0,closed:false,viewerActive:true,
  draws:0,screen,recordFrame(){this.draws++;},clearDead(){},
});
const settle=()=>new Promise(resolve=>setImmediate(resolve));
async function finish(fail=false) {
  const image=loads.shift();active--;
  if(fail)image.onerror(new Error('bad JPEG'));else image.onload();
  await settle();
}
(async()=>{
  v.showFrame('first');v.showFrame('obsolete');v.showFrame('latest');
  assert.equal(loads.length,1,'one preparation at a time');
  assert.equal(v.draws,0,'arrivals do not count as presented frames');
  assert.equal(v.screen,screen,'old image stays visible until the new image loads');
  await finish();
  assert.deepEqual(presented,['first']);assert.equal(loads[0].id,'latest');
  await finish();
  assert.deepEqual(presented,['first','latest']);
  assert.deepEqual(released,['first','latest']);assert.equal(urls.size,0);
  assert.equal(v.screen.style.cursor,'text');assert.equal(v.screen.className,'br-screen');
  assert.equal(v.screen.draggable,false);assert.equal(v.screen.alt,'');
  assert.equal(maxActive,1);assert.equal(v.preparingFrame,false);
  v.showFrame('corrupt');v.showFrame('recovery');
  await finish(true);assert.equal(presented.at(-1),'latest');
  await finish();assert.equal(presented.at(-1),'recovery');
  assert.equal(v.draws,3);assert.equal(urls.size,0);
  for(const reason of ['hide','reconnect','close']) {
    v.closed=false;v.viewerActive=true;
    v.showFrame(reason);v.showFrame('queued-'+reason);
    const before=v.draws;v.resetFrames();
    if(reason==='hide')v.viewerActive=false;
    if(reason==='close')v.closed=true;
    await finish();
    assert.equal(v.draws,before,'late image after '+reason+' cannot be presented');
    assert.equal(loads.length,0);assert.equal(urls.size,0);
    assert.equal(released.at(-1),reason);
  }
  v.closed=false;v.viewerActive=true;
  v.showFrame('old-connection');v.resetFrames();v.showFrame('new-connection');
  await finish();assert.equal(loads[0].id,'new-connection');
  await finish();assert.equal(presented.at(-1),'new-connection');
  assert.equal(urls.size,0);assert.equal(v.preparingFrame,false);
  console.log('PASS: bounded latest-frame loading, presented FPS, error recovery, hide/reconnect/close, URL cleanup and cursor preservation');
})().catch(error=>{console.error(error);process.exitCode=1;});
