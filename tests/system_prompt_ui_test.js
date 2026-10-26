/* Run with node tests/system_prompt_ui_test.js. Settings → System prompt's
   Reset to default buttons against the fake DOM: each is offered only while
   its field's text differs from Puppy's default for it - a policy stored
   from an older default, an edit typed, saved or not, down to one space -
   and greys again the moment the text is the default once more, by its own
   press or typed back; a press puts the caret in the field it reset and is
   saved like any edit. Task guidance's follows the backend's payload, and
   every one stays grey while a backend cannot be edited or a save is on its
   way. No browser, engine, network or quota. */
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
let answer = null, connectable = true;
const state = { backends: [], nodeCapabilities: [], remoteSystemPrompts: {} };
const context = vm.createContext({
  document, state, console, Event: FakeEvent,
  api: async (bid, route, opts = {}) => {
    requests.push({ bid, route, opts });
    return answer;
  },
  toast: (text, tone) => notices.push({ text, tone }), TOAST_LONG: 7000,
  enhanceChoiceSelect() {}, refreshChoiceSelect() {},
  backendSupportsSystemPrompt: () => true, remoteAvailability: () => "ok",
  backendConnectionAllowed: () => connectable, backendStateNote: () => "Backend unavailable",
  navigationRemember() {}, navigationChanged() {},
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  "globalThis.Settings = class {",
  between("  systemPromptCard(nodes, initialPayload, generation) {", "  notifySettingsCard(generation) {"),
  "};",
].join("\n"), context);
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };

const DEFAULTS = {
  remote_workspace: "Remote workspace default", browser: "Browser default",
  terminal: "Terminal default", vnc: "Remote screen default", spawn: "Spawn default",
  tasks: "Task default",
};
const SECTIONS = {
  remote_workspace: ".system-prompt-remote-workspace", browser: ".system-prompt-browser",
  terminal: ".system-prompt-terminal", vnc: ".system-prompt-vnc", spawn: ".system-prompt-spawn",
  tasks: ".system-prompt-tasks",
};
/* a payload whose fields are the defaults, as a backend answers it, with the
   overrides as stored there */
const payload = (overrides = {}, withTasks = true) => {
  const value = { custom: "", max_chars: 32768 };
  for (const [key, text] of Object.entries(DEFAULTS)) {
    if (key === "tasks" && !withTasks) continue;
    value[key] = text;
    value[key + "_default"] = text;
  }
  return Object.assign(value, overrides);
};
const settings = Object.create(context.Settings.prototype);
settings.renderGeneration = 1;
const nodes = [{ bid: 0, name: "this node" }, { bid: 7, name: "build" }];
const field = (card, key) => card.querySelector(SECTIONS[key] + " textarea");
const reset = (card, key) => card.querySelector(SECTIONS[key] + " .system-prompt-reset");
const offered = card => Object.keys(SECTIONS).filter(key => !reset(card, key).disabled);
const status = card => card.querySelector(".system-prompt-status").textContent;
const type = (card, key, text) => {
  const area = field(card, key);
  area.focus();
  area.value = text;
  fire(area, "input");
};

(async () => {
  /* ---- only a field whose text is not its default offers a reset ---- */
  let card = settings.systemPromptCard(nodes, payload({ spawn: "Spawn policy from an older Puppy" }), 1);
  document.body.appendChild(card);
  assert.equal(card.querySelectorAll(".system-prompt-reset").length, 6);
  assert.deepEqual(offered(card), ["spawn"],
    "a policy stored from an older default is the one field that can be reset");
  assert.equal(status(card), "Up to 32,768 characters per field");

  type(card, "browser", "Browser default, and then some");
  assert.deepEqual(offered(card), ["browser", "spawn"], "an edit offers its reset at once");
  assert.equal(status(card), "Unsaved changes");
  type(card, "browser", "Browser default");
  assert.deepEqual(offered(card), ["spawn"], "typed back to the default, it greys again");
  assert.equal(status(card), "Up to 32,768 characters per field");
  type(card, "browser", "Browser default ");
  assert.deepEqual(offered(card), ["browser", "spawn"], "one space is a difference");
  type(card, "browser", "Browser default");

  /* ---- a press puts the default back, the caret in its field, and greys ---- */
  reset(card, "spawn").click();
  assert.equal(field(card, "spawn").value, "Spawn default");
  assert.equal(document.activeElement, field(card, "spawn"), "the caret lands in the field it reset");
  assert.deepEqual(offered(card), [], "nothing left to reset");
  assert.equal(status(card), "Unsaved changes", "the reset is an edit until it is saved");
  reset(card, "spawn").click();   // a grey button does nothing
  assert.equal(field(card, "spawn").value, "Spawn default");

  /* ---- saved, it stays grey; while the save is on its way, all are ---- */
  let release;
  answer = new Promise(resolve => { release = resolve; });
  type(card, "terminal", "Terminal, my way");
  assert.deepEqual(offered(card), ["terminal"]);
  requests.length = 0;
  card.querySelector(".system-prompt-actions .btn-pri").click();
  await settle();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].opts.method, "PATCH");
  assert.equal(requests[0].opts.body.spawn, "Spawn default", "the reset is saved like any edit");
  assert.equal(requests[0].opts.body.terminal, "Terminal, my way");
  assert.deepEqual(offered(card), [], "nothing can be reset while a save is on its way");
  release({ system_prompt: payload({ terminal: "Terminal, my way" }) });
  await settle();
  assert.equal(status(card), "Saved for new turns");
  assert.deepEqual(offered(card), ["terminal"], "the saved custom text can still be reset");
  reset(card, "terminal").click();
  assert.equal(field(card, "terminal").value, "Terminal default");
  assert.deepEqual(offered(card), []);
  assert.equal(status(card), "Unsaved changes");
  card.remove();

  /* ---- each backend's own text, and none while it cannot be edited ---- */
  state.remoteSystemPrompts[7] = payload({ vnc: "Screens, carefully", tasks: "Tasks, my way" });
  card = settings.systemPromptCard(nodes, payload(), 1);
  document.body.appendChild(card);
  assert.deepEqual(offered(card), []);
  const select = card.querySelector("select");
  select.value = "7";
  fire(select, "change");
  assert.deepEqual(offered(card), ["vnc", "tasks"], "the backend's own text against its own defaults");
  select.value = "0";
  fire(select, "change");
  connectable = false;
  select.value = "7";
  fire(select, "change");
  assert.deepEqual(offered(card), [], "an unreachable backend offers no reset");
  assert.ok(field(card, "vnc").disabled);
  connectable = true;
  card.remove();

  /* ---- Task guidance's follows the payload: none without the field ---- */
  settings.systemPromptBid = 0;   // the card reopens on the backend last chosen
  card = settings.systemPromptCard(nodes, payload({ tasks: "Old task policy" }), 1);
  document.body.appendChild(card);
  assert.deepEqual(offered(card), ["tasks"]);
  reset(card, "tasks").click();
  assert.equal(field(card, "tasks").value, "Task default");
  assert.deepEqual(offered(card), []);
  card.remove();
  card = settings.systemPromptCard(nodes, payload({}, false), 1);
  document.body.appendChild(card);
  assert.ok(card.querySelector(".system-prompt-tasks").classList.contains("hidden"));
  assert.ok(reset(card, "tasks").disabled, "a backend from before the task tools offers no task reset");
  card.remove();
  assert.deepEqual(notices.map(item => item.tone), ["ok"], "one save, one notice");
  console.log("PASS: Settings → System prompt offers each Reset to default only while its field " +
    "differs from the default - stored, typed or saved - greys it again once the text is the " +
    "default, and offers none while a backend cannot be edited or a save is on its way");
})().catch(error => { console.error(error); process.exitCode = 1; });
