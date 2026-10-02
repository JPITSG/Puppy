/* Run with node tests/session_request_ui_test.js. The session request sheet
   against the fake DOM: nothing drawn before the read answers, the request's
   facts on the review sheet's column (state in the shared tones, its kind,
   created, a deadline only while it can still run), the sessions it went to
   on one captioned list - name and backend, the state at the row's end, the
   answer or the error under it - a workflow's steps each heading its own
   list, a press on a name closing the sheet and opening that session, Stop
   remaining work only while there is work left and only after a confirm, a
   failed read keeping what the last one showed with its reason inline, and
   the read asked of the console that tracks the request. No browser,
   engine, network or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const {FakeDocument} = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = to === null ? source.length : source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
};

const document = new FakeDocument();
const dialogs = [];
function modal(html, className = "", reopen = null) {
  const back = document.createElement("div");
  back.className = "modal-backdrop";
  const m = document.createElement("div");
  m.className = "modal" + (className ? " " + className : "");
  m.innerHTML = html;
  back.appendChild(m);
  document.body.appendChild(back);
  const record = {m, className, reopen, closed: false};
  record.close = () => { if (!record.closed) { record.closed = true; back.remove(); } };
  dialogs.push(record);
  return {m, close: record.close};
}

const calls = [];
const opened = [];
const confirms = [];
let answers = {};
async function api(bid, route, opts = {}) {
  // what the page built is copied out as plain data, comparable outside it
  calls.push({bid, route, body: opts.body === undefined ? undefined : JSON.parse(JSON.stringify(opts.body))});
  const answer = answers[route];
  if (answer instanceof Error) throw answer;
  if (typeof answer === "function") return answer(opts.body, bid);
  if (answer === undefined) throw new Error("unexpected route " + route);
  return JSON.parse(JSON.stringify(answer));
}
const context = vm.createContext({
  document, modal, api, console, Math, Number, Promise, JSON, Map, Set, String, Array, Error,
  fmtStamp: at => "at " + at,
  modalConfirm: async (title, text, opts) => {
    confirms.push({title, text, opts: JSON.parse(JSON.stringify(opts))});
    return confirms.answer !== false;
  },
  openSessionReference: (ref, seq) => opened.push([String(ref), Number(seq)]),
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("/* The request sheet is the Git sheet's.", null),
].join("\n"), context);
const open = record => vm.runInContext("modalSessionRequest", context)(record);
const settle = () => new Promise(resolve => setImmediate(resolve));
const last = () => dialogs[dialogs.length - 1];
const facts = m => [...m.querySelectorAll(".session-request-facts .ws-fact")].map(row =>
  [row.querySelector(".field-lbl").textContent, row.querySelector(".wsf-v").textContent,
   row.querySelector(".wsf-v").className.replace(/^wsf-v ?/, "")]);
const rows = list => [...list.querySelectorAll(".srl-row")].map(row => {
  const state = row.querySelector(".srl-state"), text = row.querySelector(".srl-text");
  const head = row.children[0];
  return {head: head.textContent, name: head.classList.contains("srl-name"), state: state.textContent,
          tone: state.className.replace("srl-state state-word", "").trim(),
          text: text ? text.textContent : "", bad: !!text && text.classList.contains("bad")};
});

const NODE = "a".repeat(32);
const ref = id => `${NODE}/${id}`;
const catalog = {controller: NODE, sessions: [
  {ref: ref(2), title: "Release checklist", node_name: "Studio", node: NODE, bid: 0, id: 2},
  {ref: ref(3), title: "Garden planner", node_name: "Workshop", node: NODE, bid: 0, id: 3},
]};

(async () => {
  /* ---- a request to three sessions ---- */
  const request = {id: "r1", source: ref(9), action: "question", targets: [ref(2), ref(3), ref(4)],
    status: "running", created_at: 100, deadline: 3700, unavailable: [{node: "Laptop", error: "Backend unavailable"}],
    results: [
      {id: "a", target: ref(2), status: "completed", error: "", answer: "Smoke tests passed.\nOnly the changelog is open.",
       start_seq: 3, end_seq: 5},
      {id: "b", target: ref(3), status: "running", error: "", answer: ""},
      {id: "c", target: ref(4), status: "failed", error: "The session was stopped", answer: ""}]};
  let release;
  answers = {"session-links/catalog": catalog,
             "session-links/action": () => new Promise(resolve => { release = () => resolve(request); })};
  const pending = open({id: "r1", source: ref(9), targets: request.targets, status: "pending", controller: NODE});
  await settle();
  let sheet = last().m;
  assert.equal(sheet.querySelector("h2").textContent, "Session request");
  assert.deepEqual(facts(sheet), [], "nothing from the card's old copy before the read answers");
  assert.equal(sheet.querySelector(".session-request-body").textContent, "Loading…");
  assert.ok(sheet.querySelector(".sr-cancel").classList.contains("hidden"), "no Stop before the read");
  assert.ok(sheet.querySelector(".sr-refresh").disabled, "and no second read while one is out");
  release();
  await pending;
  assert.deepEqual(calls.map(call => call.route), ["session-links/catalog", "session-links/action"]);
  assert.deepEqual(calls[1].body, {source: ref(9), method: "wait", params: {id: "r1", wait_s: 0}});
  assert.equal(calls[1].bid, 0, "this console tracks it");
  assert.deepEqual(facts(sheet), [["State", "Running", "busy"], ["Kind", "Question", ""],
    ["Created", "at 100", ""], ["Deadline", "at 3700", ""]]);
  const sections = sheet.querySelectorAll(".session-request-section");
  assert.equal(sections.length, 1);
  assert.equal(sections[0].querySelector(".field-lbl").textContent, "Sessions · 3");
  const block = sections[0].querySelector(".srl-rows");
  assert.ok(block && block.parentNode === sections[0].querySelector(".session-request-list"),
            "the rows stand in one block");
  assert.deepEqual(rows(sections[0]), [
    {head: "Release checklist · Studio", name: true, state: "Completed", tone: "ok",
     text: "Smoke tests passed.\nOnly the changelog is open.", bad: false},
    {head: "Garden planner · Workshop", name: true, state: "Running", tone: "busy", text: "", bad: false},
    {head: "Session", name: true, state: "Failed", tone: "bad", text: "The session was stopped", bad: true}]);
  const first = sections[0].querySelector(".srl-name");
  assert.equal(first.querySelector(".srl-title").textContent, "Release checklist");
  assert.equal(first.querySelector(".srl-node").textContent, " · Studio");
  assert.equal(first.getAttribute("aria-label"), "Open Release checklist");
  assert.equal(sheet.querySelector(".session-request-note").textContent, "Laptop: Backend unavailable");
  assert.ok(!sheet.querySelector(".sr-cancel").classList.contains("hidden"), "work is left to stop");
  assert.ok(!sheet.querySelector(".sr-cancel").disabled && !sheet.querySelector(".sr-refresh").disabled);
  assert.ok(sheet.querySelector(".form-error").classList.contains("hidden"));
  console.log("PASS: a request's facts on the review column and its sessions on one list - name and backend, state in its tone, answer or error under it");

  /* ---- a failed read keeps what the last one showed ---- */
  answers["session-links/action"] = new Error("Backend unavailable");
  sheet.querySelector(".sr-refresh").onclick();
  await settle(); await settle();
  assert.equal(sheet.querySelector(".form-error").textContent, "Backend unavailable");
  assert.ok(!sheet.querySelector(".form-error").classList.contains("hidden"));
  assert.equal(rows(sheet.querySelector(".session-request-list")).length, 3, "the listing stays");
  assert.ok(!sheet.querySelector(".sr-refresh").disabled);
  // the next read that answers clears the reason
  answers["session-links/action"] = request;
  sheet.querySelector(".sr-refresh").onclick();
  await settle(); await settle();
  assert.ok(sheet.querySelector(".form-error").classList.contains("hidden"));
  console.log("PASS: a failed read keeps the listing with its reason inline; the next read clears it");

  /* ---- Stop asks first, then cancels; a finished request offers none ---- */
  confirms.answer = false;
  calls.length = 0;
  await sheet.querySelector(".sr-cancel").onclick();
  assert.equal(confirms.length, 1);
  assert.deepEqual(confirms[0].opts, {confirmLabel: "Stop work", destructive: true});
  assert.equal(calls.length, 0, "a declined confirm sends nothing");
  confirms.answer = true;
  answers["session-links/action"] = body => ({...request, status: "cancelled",
    results: request.results.map(row => row.status === "running" ? {...row, status: "cancelled"} : row)});
  await sheet.querySelector(".sr-cancel").onclick();
  await settle(); await settle();
  assert.deepEqual(calls[0].body, {source: ref(9), method: "cancel", params: {id: "r1", wait_s: 0}});
  assert.deepEqual(facts(sheet).slice(0, 1), [["State", "Cancelled", "warn"]]);
  assert.ok(!facts(sheet).some(([label]) => label === "Deadline"), "no deadline once it cannot run");
  assert.ok(sheet.querySelector(".sr-cancel").classList.contains("hidden"), "nothing left to stop");
  console.log("PASS: Stop remaining work only after a destructive confirm, and only while work is left");

  /* ---- a name closes the sheet and opens its session ---- */
  sheet.querySelector(".srl-name").onclick();
  assert.ok(last().closed, "the sheet goes first");
  assert.deepEqual(opened, [[ref(2), 5]], "at the answer's end");
  console.log("PASS: a session's name closes the sheet and opens it at its answer");

  /* ---- a workflow, tracked by another console ---- */
  const workflow = {id: "w1", source: ref(9), title: "Prepare the release", status: "completed", created_at: 50,
    deadline: 900, unavailable: [], steps: [
      {spec: {id: "audit", text: "List what is open."}, status: "completed", error: "",
       results: [{id: "d", target: ref(2), status: "completed", error: "", answer: "The changelog."}]},
      {spec: {id: "notes", text: "Write the notes."}, status: "failed", error: "No session answered", results: []}]};
  calls.length = 0;
  // this console is node b; the request is tracked by node a, whose sessions this console reaches as backend 4
  answers = {"session-links/catalog": {controller: "b".repeat(32), sessions: [
    ...catalog.sessions.map(row => ({...row, node: "b".repeat(32)})),
    {ref: ref(1), title: "Coordinator", node_name: "Laptop", node: NODE, bid: 4, id: 1}]},
    "session-links/action": workflow};
  await open({id: "w1", source: ref(9), targets: [ref(2)], status: "pending", controller: NODE, workflow: true});
  sheet = last().m;
  assert.equal(sheet.querySelector("h2").textContent, "Session workflow");
  assert.equal(calls[1].bid, 4, "asked of the console that tracks it");
  assert.deepEqual(calls[1].body, {source: ref(9), method: "workflow", params: {id: "w1"}});
  assert.deepEqual(facts(sheet), [["Name", "Prepare the release", ""], ["State", "Completed", "ok"],
    ["Created", "at 50", ""]]);
  const steps = [...sheet.querySelectorAll(".session-request-section")];
  assert.deepEqual(steps.map(step => step.querySelector(".field-lbl").textContent), ["Step audit", "Step notes"]);
  assert.deepEqual(rows(steps[0]), [
    {head: "List what is open.", name: false, state: "Completed", tone: "ok", text: "", bad: false},
    {head: "Release checklist · Studio", name: true, state: "Completed", tone: "ok", text: "The changelog.", bad: false}]);
  assert.deepEqual(rows(steps[1]), [
    {head: "Write the notes.", name: false, state: "Failed", tone: "bad", text: "No session answered", bad: true}]);
  assert.ok(sheet.querySelector(".sr-cancel").classList.contains("hidden"));
  console.log("PASS: a workflow's steps each head their own list with the step's request, state and error, asked of the tracking console");

  /* ---- a console that cannot be found says so ---- */
  answers = {"session-links/catalog": {...catalog, controller: "b".repeat(32)}, "session-links/action": request};
  await open({id: "r1", source: ref(9), targets: [ref(2)], status: "pending", controller: "d".repeat(32)});
  sheet = last().m;
  assert.equal(sheet.querySelector(".form-error").textContent, "The console tracking this request is unavailable");
  assert.equal(sheet.querySelector(".session-request-body").textContent, "", "no Loading… left behind");
  assert.deepEqual(facts(sheet), []);
  // a request sent nowhere yet says so on its list
  answers = {"session-links/catalog": catalog, "session-links/action": {...request, results: [], unavailable: []}};
  await open({id: "r1", source: ref(9), targets: [], status: "pending", controller: NODE});
  sheet = last().m;
  assert.equal(sheet.querySelector(".srl-empty").textContent, "Not sent to any session yet");
  assert.equal(sheet.querySelector(".field-lbl").textContent, "State");
  console.log("PASS: an unreachable tracking console and a request sent nowhere yet each say so");
})().catch(error => { console.error(error); process.exit(1); });
