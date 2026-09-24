/* Saved-backup card: edits survive polling, requests cannot race newer saves,
   Run now uses saved settings, and leaving the view retires its polling. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(require("node:path").join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (a,b) => source.slice(source.indexOf(a), source.indexOf(b,source.indexOf(a)));
const document = new FakeDocument(), requests = [], timers = new Map();
const state = {clockFormat:"24h"};
let timerId = 0;
const context = vm.createContext({document, state, console, Number, JSON, Promise, Set, Map,
  el: (tag,cls,text) => {const n = document.createElement(tag); n.className = cls || ""; if(text !== undefined)n.textContent = text; return n;},
  setTimeout: fn => {timers.set(++timerId,fn); return timerId;}, clearTimeout: id => timers.delete(id),
  fmtBytes: n => `${n} B`, fmtStamp: n => `stamp ${n}`, toast: () => {},
  api: (bid,route,options) => new Promise((resolve,reject) => requests.push({bid,route,options,resolve,reject})),
});
vm.runInContext(between("function fmtClockSetting(", "/* One stamp for"), context);
vm.runInContext(`class View { ${between("  backupSettingsCard(", "  async render() {")} }; this.View=View;`,context);
const tick = async () => {for(let i=0;i<8;i++)await Promise.resolve();};
const payload = changes => ({settings:{enabled:false,at:"03:00",directory:"/home/mira/backups",keep:7},
  next_at:0,timezone:"CEST",keep_max:100,status:"idle",waiting:"",files:[],history:[],...changes});
const reply = (route,data) => {const n=requests.findIndex(r=>r.route===route); assert.ok(n>=0,route); requests.splice(n,1)[0].resolve(data);};
const same = (a,b) => assert.equal(JSON.stringify(a),JSON.stringify(b));

(async () => {
  const view=new context.View(); view.renderGeneration=1;
  view.root={getClientRects:()=>[{}]};
  const card=view.backupSettingsCard(1); document.body.appendChild(card);
  const $=selector=>card.querySelector(selector), form=$("form");
  const at=$("#backup-at"), keep=$("#backup-keep"), dir=$("#backup-directory"), enabled=$("#backup-enabled");
  const save=$("#backup-save"), run=$("#backup-run"), error=$(".form-error");
  await tick(); reply("snapshot/storage",{bytes:100});
  assert.equal(run.disabled,true);
  requests.shift().reject(new Error("Connection lost")); await tick();
  assert.equal(error.textContent,"Connection lost");
  $("#backup-retry").onclick(); reply("snapshot/backups",payload()); await tick();
  assert.equal(error.textContent,""); assert.equal(run.disabled,false); assert.equal(save.disabled,true);
  assert.equal(at.value,"03:00"); assert.equal(keep.max,"100");

  // A poll that finishes during typing updates history but preserves every edit.
  const reading=view.backupRefresh();
  dir.value="/home/mira/archive"; at.value="04:15"; keep.value="3"; enabled.checked=true; form.oninput();
  assert.equal(run.disabled,true); assert.equal(save.disabled,false);
  reply("snapshot/backups",payload()); await reading;
  assert.equal(dir.value,"/home/mira/archive"); assert.equal(at.value,"04:15");
  assert.equal(enabled.checked,true); assert.equal(keep.value,"3");
  assert.equal($("#backup-note").textContent,"Unsaved changes");

  // Saving invalidates a pending read even if the old response arrives last.
  const stale=view.backupRefresh(); const old=requests.shift();
  let saving=form.onsubmit(new FakeEvent("submit"));
  let request=requests.shift(); assert.equal(request.options.method,"PUT");
  const cfg={enabled:true,at:"04:15",directory:"/home/mira/archive",keep:3}; same(request.options.body,cfg);
  request.resolve(payload({settings:cfg,next_at:500})); await saving;
  old.resolve(payload()); await stale;
  assert.equal(dir.value,cfg.directory); assert.equal(save.disabled,true); assert.equal(run.disabled,false);
  assert.equal($("#backup-status").textContent,"Next backup · stamp 500");

  // Failures stay inline without losing edits; time conversion follows the host.
  state.clockFormat="12h"; at.value="not a time"; form.oninput();
  await form.onsubmit(new FakeEvent("submit")); assert.match(error.textContent,/3:30 AM/);
  assert.equal(requests.length,0); at.value="5:45 PM"; form.oninput();
  saving=form.onsubmit(new FakeEvent("submit")); request=requests.shift();
  assert.equal(request.options.body.at,"17:45"); request.reject(new Error("Read-only directory")); await saving;
  assert.equal(error.textContent,"Read-only directory"); assert.equal(at.value,"5:45 PM");
  saving=form.onsubmit(new FakeEvent("submit")); request=requests.shift();
  request.resolve(payload({settings:{...cfg,enabled:false,at:"17:45"}})); await saving;
  assert.equal(at.value,"5:45 PM"); assert.equal(run.disabled,false);

  // Run now queues once while disabled, with no stale form values in its body.
  const running=run.onclick(); request=requests.shift();
  assert.equal(request.route,"snapshot/backups/run"); same(request.options.body,{});
  request.resolve(payload({status:"waiting",waiting:"active session"})); await running;
  reply("snapshot/backups",payload({status:"waiting",waiting:"active session"})); await tick();
  assert.equal(run.disabled,true); assert.match($("#backup-status").textContent,/active session/);
  await run.onclick(); assert.equal(requests.length,0);

  // Retained archives have repeatable links; missing and rotated ones are clear.
  const file={id:"a",at:400,filename:"backup.tar.gz",size:900,available:true,download:"/api/snapshot/saved/a"};
  const data=payload({files:[file,{...file,id:"b",available:false}],history:[
    {id:"a",at:400,tone:"ok",message:"Backup saved",source:"manual"},
    {id:"b",at:300,tone:"warn",message:"Backup saved · old copy unavailable",source:"scheduled"},
    {id:"c",at:200,tone:"ok",message:"Backup saved",source:"scheduled"},
    {id:"d",at:100,tone:"bad",message:"Could not save backup",source:"scheduled"},
  ]});
  let read=view.backupRefresh(); reply("snapshot/backups",data); await read;
  const link=$(".backup-download"); assert.equal(link.href,file.download); assert.equal(link.download,file.filename);
  assert.match($("#backup-history").textContent,/Unavailable/); assert.match($("#backup-history").textContent,/Rotated/);
  link.focus(); read=view.backupRefresh(); reply("snapshot/backups",data); await read;
  assert.equal($(".backup-download"),link); assert.equal(document.activeElement,link);
  read=view.backupRefresh(); reply("snapshot/backups",payload({status:"running"})); await read;
  assert.equal(save.disabled,true); assert.equal(run.disabled,true); assert.equal(dir.disabled,true);
  assert.equal($("#snapshot-import").disabled,true); assert.equal($("#snapshot-export").disabled,true);

  // Hidden pages do not poll; retired cards cannot consume a late response.
  document.hidden=true; await view.backupRefresh(); assert.equal(requests.length,0); document.hidden=false;
  read=view.backupRefresh(); request=requests.shift(); const oldDirectory=dir.value;
  view.backupRetire(); assert.equal(timers.size,0);
  request.resolve(payload()); await read; assert.equal(dir.value,oldDirectory);
  await view.backupRefresh(); assert.equal(requests.length,0);
  console.log("PASS: backup settings, saved-only Run now, local-clock parsing, inline failures, edit/read races, stable downloads and polling lifecycle");
})().catch(error=>{console.error(error);process.exitCode=1;});
