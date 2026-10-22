/* Saved-backup card: edits survive polling, requests cannot race newer saves,
   Run now uses saved settings, and leaving the view retires its polling. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs"), vm = require("node:vm");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(require("node:path").join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (a,b) => source.slice(source.indexOf(a), source.indexOf(b,source.indexOf(a)));
const document = new FakeDocument(), requests = [], timers = new Map(), confirms = [], toasts = [];
const state = {clockFormat:"24h"};
let timerId = 0;
const context = vm.createContext({document, state, console, Number, JSON, Promise, Set, Map,
  el: (tag,cls,text) => {const n = document.createElement(tag); n.className = cls || ""; if(text !== undefined)n.textContent = text; return n;},
  setTimeout: fn => {timers.set(++timerId,fn); return timerId;}, clearTimeout: id => timers.delete(id),
  fmtBytes: n => `${n} B`, fmtStamp: n => `stamp ${n}`, toast: () => {},
  trashIcon: () => document.createElement("svg"),
  modalConfirm: (title, text, options) => new Promise(resolve => confirms.push({title, text, options, resolve})),
  api: (bid,route,options) => new Promise((resolve,reject) => requests.push({bid,route,options,resolve,reject})),
});
vm.runInContext(between("function fmtClockSetting(", "/* One stamp for"), context);
vm.runInContext(`class View { ${between("  backupSettingsCard(", "  async render() {")} }; this.View=View;`,context);
const tick = async () => {for(let i=0;i<8;i++)await Promise.resolve();};
const payload = changes => ({settings:{enabled:false,at:"03:00",directory:"/home/mira/backups",keep:7},
  next_at:0,timezone:"CEST",keep_max:100,archive_limit_mib:512,archive_limit:{min:16,max:16384,default:512},
  status:"idle",waiting:"",files:[],history:[],...changes});
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
  const limit=$("#backup-limit");
  assert.equal(limit.value,"512"); assert.equal(limit.min,"16"); assert.equal(limit.max,"16384");
  assert.match($("#backup-help").textContent,/archive limit \(16–16384 MiB, 512 by default\)/);

  // A poll that finishes during typing updates history but preserves every edit.
  const reading=view.backupRefresh();
  dir.value="/home/mira/archive"; at.value="04:15"; keep.value="3"; limit.value="1024"; enabled.checked=true; form.oninput();
  assert.equal(run.disabled,true); assert.equal(save.disabled,false);
  reply("snapshot/backups",payload()); await reading;
  assert.equal(dir.value,"/home/mira/archive"); assert.equal(at.value,"04:15");
  assert.equal(enabled.checked,true); assert.equal(keep.value,"3"); assert.equal(limit.value,"1024");
  assert.equal($("#backup-note").textContent,"Unsaved changes");

  // Saving invalidates a pending read even if the old response arrives last.
  const stale=view.backupRefresh(); const old=requests.shift();
  let saving=form.onsubmit(new FakeEvent("submit"));
  let request=requests.shift(); assert.equal(request.options.method,"PUT");
  const cfg={enabled:true,at:"04:15",directory:"/home/mira/archive",keep:3};
  same(request.options.body,{...cfg,archive_limit_mib:1024});
  request.resolve(payload({settings:cfg,next_at:500,archive_limit_mib:1024})); await saving;
  old.resolve(payload()); await stale;
  assert.equal(dir.value,cfg.directory); assert.equal(limit.value,"1024");
  assert.equal(save.disabled,true); assert.equal(run.disabled,false);
  assert.equal($("#backup-status").textContent,"Next backup · stamp 500");

  // Failures stay inline without losing edits; time conversion follows the host.
  state.clockFormat="12h"; at.value="not a time"; form.oninput();
  await form.onsubmit(new FakeEvent("submit")); assert.match(error.textContent,/3:30 AM/);
  assert.equal(requests.length,0); at.value="5:45 PM"; form.oninput();
  saving=form.onsubmit(new FakeEvent("submit")); request=requests.shift();
  assert.equal(request.options.body.at,"17:45"); request.reject(new Error("Read-only directory")); await saving;
  assert.equal(error.textContent,"Read-only directory"); assert.equal(at.value,"5:45 PM");
  saving=form.onsubmit(new FakeEvent("submit")); request=requests.shift();
  request.resolve(payload({settings:{...cfg,enabled:false,at:"17:45"},archive_limit_mib:1024})); await saving;
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

  // Every row ends with a bin: after Download it deletes a saved copy; on a
  // rotated or failed line, with no copy behind it, it clears the line.
  context.toast = (text, tone) => toasts.push([text, tone]);
  const where = {directory:"/home/mira/backups"};
  const kept = payload({files:[{...file,...where},{...file,...where,id:"b",available:false,download:null}],
    history:data.history});
  read=view.backupRefresh(); reply("snapshot/backups",kept); await read;
  let bins=card.querySelectorAll(".backup-delete");
  same(bins.map(b=>b.getAttribute("aria-label")),["Delete backup from stamp 400","Delete backup from stamp 400",
    "Clear stamp 200 from the list","Clear stamp 100 from the list"]);
  const firstActions=bins[0].parentNode;
  assert.equal(firstActions.className,"backup-actions");
  same(firstActions.children.map(n=>n.className),["backup-download","icon-btn backup-delete"]);
  same(bins[2].parentNode.children.map(n=>n.className),["help","icon-btn backup-delete"]);
  same(bins[3].parentNode.children.map(n=>n.className),["icon-btn backup-delete"]);
  assert.equal(bins[0].disabled,false);
  // it asks first, naming the copy and its file; Cancel sends nothing
  bins[0].onclick(); await tick();
  let ask=confirms.shift();
  assert.equal(ask.title,"Delete backup?");
  assert.equal(ask.text,"Its archive backup.tar.gz is deleted from /home/mira/backups and cannot be recovered.");
  same(ask.options,{confirmLabel:"Delete",destructive:true,subject:"stamp 400 · 900 B"});
  ask.resolve(false); await tick();
  assert.equal(requests.length,0);
  // confirmed: one request, the bins held while it runs, the focus handed on
  bins[0].onclick(); await tick(); confirms.shift().resolve(true); await tick();
  request=requests.shift();
  assert.equal(request.route,"snapshot/saved/a"); same(request.options,{method:"DELETE"});
  assert.ok(card.querySelectorAll(".backup-delete").every(b=>b.disabled),"no second delete while one runs");
  assert.equal(run.disabled,true);
  const afterA=payload({files:[{...file,...where,id:"b",available:false,download:null}],history:data.history.slice(1),deleted:"deleted"});
  request.resolve(afterA); await tick();
  same(toasts.pop(),["Backup deleted","ok"]);
  bins=card.querySelectorAll(".backup-delete");
  assert.equal(bins.length,3); assert.equal(bins[0].disabled,false);
  assert.equal(document.activeElement,bins[0],"the focus passes to the row that took its place");
  assert.doesNotMatch($("#backup-history").textContent,/Download/);
  // a copy whose file is gone only leaves the list, and says so
  bins[0].onclick(); await tick(); ask=confirms.shift();
  assert.match(ask.text,/missing or was replaced, so it only leaves this list/);
  ask.resolve(true); await tick();
  request=requests.shift(); assert.equal(request.route,"snapshot/saved/b");
  request.reject(new Error("Could not delete the backup · Permission denied")); await tick();
  assert.equal(error.textContent,"Could not delete the backup · Permission denied","a refusal stays inline");
  assert.equal(card.querySelectorAll(".backup-delete").length,3); assert.equal(toasts.length,0);
  card.querySelector(".backup-delete").onclick(); await tick(); confirms.shift().resolve(true); await tick();
  requests.shift().resolve(payload({history:data.history.slice(2),deleted:"missing"})); await tick();
  same(toasts.pop(),["Backup removed from the list · its file was already gone","ok"]);
  bins=card.querySelectorAll(".backup-delete");
  assert.equal(bins.length,2); assert.equal(document.activeElement,bins[0]);
  // clearing a line asks nothing and says nothing: one request, the bins held
  bins[0].onclick(); await tick();
  assert.equal(confirms.length,0);
  request=requests.shift();
  assert.equal(request.route,"snapshot/history/c"); same(request.options,{method:"DELETE"});
  assert.ok(card.querySelectorAll(".backup-delete").every(b=>b.disabled),"no second press while one runs");
  request.resolve(payload({history:data.history.slice(3)})); await tick();
  assert.equal(toasts.length,0); assert.doesNotMatch($("#backup-history").textContent,/Rotated/);
  bins=card.querySelectorAll(".backup-delete");
  assert.equal(bins.length,1); assert.equal(document.activeElement,bins[0]);
  bins[0].onclick(); await tick();
  request=requests.shift(); assert.equal(request.route,"snapshot/history/d");
  request.reject(new Error("Wait for the current backup or restore to finish")); await tick();
  assert.equal(error.textContent,"Wait for the current backup or restore to finish","a refusal stays inline");
  assert.equal(card.querySelectorAll(".backup-delete").length,1); assert.equal(toasts.length,0);
  card.querySelector(".backup-delete").onclick(); await tick();
  requests.shift().resolve(payload({history:[]})); await tick();
  assert.equal(card.querySelectorAll(".backup-delete").length,0); assert.equal(error.textContent,"");
  assert.match($("#backup-history").textContent,/No saved backups yet/);
  assert.equal(document.activeElement,run,"with no row left the focus goes to Run now");
  // a replaced file is left alone, and the notice says so
  read=view.backupRefresh(); reply("snapshot/backups",kept); await read;
  card.querySelector(".backup-delete").onclick(); await tick(); confirms.shift().resolve(true); await tick();
  requests.shift().resolve(payload({files:kept.files.slice(1),history:data.history,deleted:"kept"})); await tick();
  same(toasts.pop(),["Backup removed from the list · the file at its place was replaced, so it was left alone","warn"]);
  // a running backup holds every bin
  read=view.backupRefresh(); reply("snapshot/backups",{...kept,status:"running"}); await read;
  assert.ok(card.querySelectorAll(".backup-delete").every(b=>b.disabled));
  context.toast = () => {};
  read=view.backupRefresh(); reply("snapshot/backups",payload({status:"running"})); await read;
  assert.equal(save.disabled,true); assert.equal(run.disabled,true); assert.equal(dir.disabled,true);
  assert.equal(limit.disabled,true);
  assert.equal($("#snapshot-import").disabled,true); assert.equal($("#snapshot-export").disabled,true);

  // Hidden pages do not poll; retired cards cannot consume a late response.
  document.hidden=true; await view.backupRefresh(); assert.equal(requests.length,0); document.hidden=false;
  read=view.backupRefresh(); request=requests.shift(); const oldDirectory=dir.value;
  view.backupRetire(); assert.equal(timers.size,0);
  request.resolve(payload()); await read; assert.equal(dir.value,oldDirectory);
  await view.backupRefresh(); assert.equal(requests.length,0);
  console.log("PASS: backup settings and the archive limit, saved-only Run now, local-clock parsing, inline failures, edit/read races, stable downloads, deleting a saved copy behind its confirm, clearing failed and rotated lines, and polling lifecycle");
})().catch(error=>{console.error(error);process.exitCode=1;});
