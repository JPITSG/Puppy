/* Run with node tests/task_auto_apply_ui_test.js. The console's side of
   Apply to Main when done and the task tools against the fake DOM: the
   state a task set to apply when done shows (resolving, applying, waiting
   for Main) and the Tasks sheet's line for it, the switch's one request and
   its answer landing on the list row and the open view, the transcript rows
   the worker and a merge of Main write, and Settings' Task guidance - drawn,
   reset and saved for a backend whose payload carries it, absent for one
   from before. No browser, engine, network or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument, FakeEvent, fire } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}

const document = new FakeDocument();
const requests = [], notices = [];
const metas = new Map(), views = new Map();
let answer = null, failure = null, rendered = 0;
const state = { backends: [{ id: 7, capabilities: [] }],
  nodeCapabilities: ["session-task-auto-apply", "session-task-agent"], remoteSystemPrompts: {} };
const context = vm.createContext({
  document, state, console, Event: FakeEvent,
  api: async (bid, route, opts = {}) => {
    requests.push({ bid, route, opts });
    if (failure) throw failure;
    return answer;
  },
  toast: (text, tone) => notices.push({ text, tone }), TOAST_LONG: 7000,
  findSessionMeta: (bid, sid) => metas.get(`${bid}:${sid}`) || null,
  sessionViewFor: (bid, sid) => views.get(`${bid}:${sid}`) || null,
  renderSidebar: () => { rendered++; },
  linkifyInto: (node, text) => { node.appendChild(document.createTextNode(text)); return node; },
  enhanceChoiceSelect() {}, refreshChoiceSelect() {},
  backendSupportsSystemPrompt: () => true, remoteAvailability: () => "ok",
  backendConnectionAllowed: () => true, backendStateNote: () => "Backend unavailable",
  navigationRemember() {}, navigationChanged() {},
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function nodeHasCapability(", "/* \"Enable tasks\""),
  between("const TASK_STATES", "/* Refresh is a command"),
  between("function taskUpdateNode(", "/* A model as its engine's catalog names it"),
  between("function sessionTaskUpdateNode(", "function backgroundTaskUpdateNode("),
  "globalThis.Settings = class {",
  between("  systemPromptCard(nodes, initialPayload, generation) {", "  notifySettingsCard(generation) {"),
  "};",
].join("\n"), context);
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };

(async () => {
  /* ---- the state a task set to apply when done shows ---- */
  const auto = (phase, extra = {}) => ({ armed_at: 1, rounds: 2, max_rounds: 8, note: "",
    resolving: phase === "resolving", phase, ...extra });
  const task = (stateName, autoApply, extra = {}) =>
    ({ state: stateName, needs_approval: false, auto_apply: autoApply, ...extra });
  const busyNote = "waiting for Main to be idle";
  for (const [record, label, tone, line] of [
    [task("running", null), "Running", "busy", ""],
    [task("ready", null), "Review", "ok", ""],
    [task("running", auto("working")), "Running", "busy", "Applies to Main when done"],
    [task("running", auto("resolving")), "Resolving", "busy",
     "Applies to Main when done · resolving conflicts with Main, round 2 of 8"],
    [task("ready", auto("waiting")), "Applying", "busy", "Applies to Main when done · applying next"],
    [task("ready", auto("waiting", { note: busyNote })), "Waiting", "busy",
     "Applies to Main when done · waiting for Main to be idle"],
    [task("applied", auto("applying")), "Applying", "busy", "Applies to Main when done · applying now"],
    [task("stopped", auto("paused")), "Stopped", "warn",
     "Applies to Main when done · once a turn finishes successfully"],
    [task("held", auto("held")), "Held", "warn",
     "Applies to Main when done · once its held messages are sent or discarded"],
    [task("running", auto("resolving"), { needs_approval: true }), "Needs approval", "warn",
     "Applies to Main when done · resolving conflicts with Main, round 2 of 8"],
  ]) {
    assert.equal(context.taskStateLabel(record), label, JSON.stringify(record));
    assert.equal(context.taskStateClass(record), tone, JSON.stringify(record));
    assert.equal(context.taskAutoApplyLine(record), line, JSON.stringify(record));
  }
  assert.equal(context.taskReviewable(task("ready", auto("waiting"))), true,
    "a task on its way into Main can still be reviewed by hand");

  /* ---- the switch: one request, its answer on the list row and the view ---- */
  const docs = { id: 12, name: "Docs", task: { parent: 10, state: "running", auto_apply: null } };
  const view = { session: { id: 12, model: "socket-model", task: docs.task } };
  metas.set("0:12", docs);
  views.set("0:12", view);
  answer = { session: { id: 12, name: "Docs", task: { parent: 10, state: "running", auto_apply: auto("working") } } };
  await context.setTaskAutoApply(0, docs, true);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].route, "sessions/10/tasks/12/auto-apply");
  assert.equal(requests[0].opts.method, "POST");
  assert.equal(JSON.stringify(requests[0].opts.body), '{"enabled":true}');
  assert.equal(docs.task.auto_apply.phase, "working", "the list row reads the answer at once");
  assert.equal(view.session.task.auto_apply.phase, "working", "and so does the open conversation");
  assert.equal(view.session.model, "socket-model", "the view keeps its own socket-owned fields");
  assert.equal(rendered, 1, "every strip and sheet repaints from the list");
  failure = new Error("Task does not belong to this session");
  await context.setTaskAutoApply(0, docs, false);
  assert.deepEqual(notices.at(-1), { text: "Could not turn off Apply to Main when done · " +
    "Task does not belong to this session", tone: "bad" }, "the toast grammar: what failed, then why");
  assert.equal(docs.task.auto_apply.phase, "working", "a refusal changes nothing");
  failure = null;
  const remote = { id: 5, name: "Remote", task: { parent: 4, state: "ready" } };
  const before = requests.length;
  await context.setTaskAutoApply(7, remote, true);
  assert.equal(requests.length, before, "a node without the switch is never asked");
  state.backends[0].capabilities.push("session-task-auto-apply");
  answer = { session: { id: 5, task: { parent: 4, state: "ready", auto_apply: auto("waiting") } } };
  await context.setTaskAutoApply(7, remote, true);
  assert.equal(requests.at(-1).bid, 7, "a remote task asks its own node");
  assert.equal(requests.at(-1).route, "sessions/4/tasks/5/auto-apply");

  /* ---- the transcript rows ---- */
  const row = data => {
    const node = context.sessionTaskUpdateNode(data);
    const label = node.querySelector(".task-update-label");
    const text = node.querySelector(".task-update-text");
    return [label.textContent, ["ok", "busy", "warn", "bad"].find(tone => label.classList.contains(tone)) || "",
      text ? text.textContent : ""];
  };
  assert.deepEqual(row({ subtype: "session_task", text: "Task changes applied automatically: Docs" }),
    ["Task changes applied automatically", "ok", "Docs"]);
  assert.deepEqual(row({ subtype: "session_task",
    text: "Task changes applied automatically: Docs · merged with Main's newer changes" }),
    ["Task changes applied automatically", "ok", "Docs · merged with Main's newer changes"]);
  assert.deepEqual(row({ subtype: "session_task", text: "Resolving conflicts with Main in task: Docs · round 1 of 8" }),
    ["Resolving conflicts with Main in task", "busy", "Docs · round 1 of 8"]);
  assert.deepEqual(row({ subtype: "session_task",
    text: "Task not applied automatically: Docs · its working copy is missing · review it from the Tasks sheet" }),
    ["Task not applied automatically", "warn", "Docs · its working copy is missing · review it from the Tasks sheet"]);
  assert.deepEqual(row({ subtype: "session_task", text: "Task changes applied: Docs" }),
    ["Task changes applied", "ok", "Docs"], "the review sheet's own row is unchanged");
  assert.deepEqual(row({ subtype: "session_task_sync", text: "Main merged into this task: 3 files updated, 1 conflicting",
    conflicts: ["a.txt"] }), ["Main merged into this task", "warn", "3 files updated, 1 conflicting · a.txt"]);
  assert.deepEqual(row({ subtype: "session_task_sync", text: "Main merged into this task: 1 file updated",
    conflicts: [] }), ["Main merged into this task", "ok", "1 file updated"]);

  /* ---- Settings: Task guidance where the backend carries it ---- */
  const payload = withTasks => ({ custom: "", remote_workspace: "r", remote_workspace_default: "r",
    browser: "b", browser_default: "b", terminal: "t", terminal_default: "t", vnc: "v",
    vnc_default: "v", spawn: "s", spawn_default: "s", max_chars: 32768,
    ...(withTasks ? { tasks: "Own task policy", tasks_default: "Default task policy" } : {}) });
  const settings = Object.create(context.Settings.prototype);
  settings.renderGeneration = 1;
  let card = settings.systemPromptCard([{ bid: 0, name: "this node" }], payload(true), 1);
  document.body.appendChild(card);
  const section = card.querySelector(".system-prompt-tasks");
  assert.ok(section && !section.classList.contains("hidden"), "drawn for a backend that has it");
  assert.equal(section.querySelector("h3").textContent, "Task guidance");
  const text = section.querySelector("textarea");
  assert.equal(text.value, "Own task policy");
  assert.equal(text.getAttribute("aria-label"), "Task system prompt");
  text.value = "Edited";
  fire(text, "input");
  assert.equal(card.querySelector(".system-prompt-status").textContent, "Unsaved changes");
  section.querySelector(".system-prompt-reset").click();
  assert.equal(text.value, "Default task policy");
  answer = { system_prompt: Object.assign(payload(true), { tasks: "Default task policy" }) };
  requests.length = 0;
  card.querySelector(".system-prompt-actions .btn-pri").click();
  await settle();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].opts.method, "PATCH");
  assert.equal(requests[0].opts.body.tasks, "Default task policy");
  assert.equal(card.querySelector(".system-prompt-status").textContent, "Saved for new turns");
  card.remove();
  // a backend from before the task tools: no section, and nothing sent for it
  card = settings.systemPromptCard([{ bid: 0, name: "this node" }], payload(false), 1);
  document.body.appendChild(card);
  assert.ok(card.querySelector(".system-prompt-tasks").classList.contains("hidden"),
    "a payload without the field draws no section");
  answer = { system_prompt: payload(false) };
  requests.length = 0;
  card.querySelector(".system-prompt-actions .btn-pri").click();
  await settle();
  assert.equal(requests.length, 1);
  assert.equal("tasks" in requests[0].opts.body, false, "an older backend is never sent the field");
  assert.equal(card.querySelector(".system-prompt-status").textContent, "Saved for new turns");
  card.remove();
  console.log("PASS: Apply to Main when done in the console: state words and the Tasks sheet line, " +
    "the switch's request and answer, the worker's and a merge's transcript rows, and Settings' " +
    "Task guidance with and without the backend field");
})().catch(error => { console.error(error); process.exitCode = 1; });
