/* Generated-title settings and the creation dialogs' choice against the
   shared fake DOM: the Settings card (load, retry, the backend list by
   capability, the engine list by what a backend has installed, the model and
   effort pickers with Custom…, the prompt and its reset, the unsaved note,
   Save, the switch, Try it) and the check under a dialog's Name field (shown
   only where a title can be asked for, yielding to a typed name), plus the
   pending shimmer's predicate. No browser, engine, network or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(require("node:path").join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (a, b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a)));
const document = new FakeDocument();
const claude = {
  key: "claude", label: "Claude Code", installed: true, allow_custom_model: true,
  model_options: [
    { value: "", label: "Default", hint: "" },
    { value: "haiku", label: "Haiku", hint: "", effort_options: [
      { value: "", label: "Default" }, { value: "low", label: "Low" }] },
    { value: "opus", label: "Opus", hint: "", effort_options: [
      { value: "", label: "Default" }, { value: "high", label: "High" }, { value: "max", label: "Max" }] },
  ],
  effort_options: [{ value: "", label: "Default" }, { value: "low", label: "Low" }, { value: "high", label: "High" }],
};
const codex = { key: "codex", label: "Codex", installed: false, allow_custom_model: false,
  model_options: [{ value: "", label: "Default" }, { value: "gpt-x", label: "GPT X" }], effort_options: [] };
const state = {
  instance: "Studio",
  titles: { enabled: false, configured: false, backend: 0, engine: "", model: "" },
  backends: [
    { id: 1, name: "Peer", capabilities: ["spawn-exec", "session-titles"] },
    { id: 2, name: "Older peer", capabilities: ["spawn-exec"] },
  ],
  engines: [claude, codex], engMap: { claude, codex }, engCache: {}, remoteOk: {},
};
const requests = [];
const context = vm.createContext({ document, state, console, Number, JSON, Promise, Set, Array,
  $: id => document.getElementById(id),
  el: (tag, cls, text) => { const e = document.createElement(tag); e.className = cls; if (text !== undefined) e.textContent = text; return e; },
  enhanceChoiceSelect: () => {}, refreshChoiceSelect: () => {},
  backendPoolable: () => true,
  rememberEnginePayload: (bid, result) => { if (bid) state.engCache[bid] = result.engines; },
  ENGINE_POLL_TIMEOUT: 15000,
  enginePayloadListeners: new Set(),
  api: (bid, route, options) => new Promise((resolve, reject) => requests.push({ bid, route, options, resolve, reject })),
});
for (const [start, end] of [
  ["function effortOptionsForModel(", "/* One engine card for every picker"],
  ["function fillEngineChoice(", "const headWord = "],
  ["function backendConnectionAllowed(", "function remoteAvailability("],
  ["function backendName(", "/* What the sidebar quick-search can see"],
  ["function backendHasCapability(", "function backendSupportsShutdownNotice("],
  ["function engineInfo(", "function engineReady("],
  ["const titlesStateListeners", "/* completion-alert bell:"],
]) vm.runInContext(between(start, end), context);
vm.runInContext("this.titlesStateListeners = titlesStateListeners;", context);
vm.runInContext(`class View { ${between("  titleSettingsCard(", "  async render() {")} }; this.View = View;`, context);
const tick = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
const settings = (overrides = {}) => ({ enabled: false, backend: 0, engine: "", model: "", effort: "",
  prompt: "Name it: {message}", ...overrides });
const payload = (overrides = {}) => ({ settings: settings(overrides), default_prompt: "Default prompt {message}",
  placeholder: "{message}", max_prompt_chars: 4000 });
const values = select => [...select.options].map(option => [option.value, option.textContent, !!option.disabled]);
/* objects made inside the page's realm compare by shape, not prototype */
const same = (actual, expected) => assert.equal(JSON.stringify(actual), JSON.stringify(expected));

(async () => {
  /* the card */
  const view = new context.View(); view.renderGeneration = 1;
  const card = view.titleSettingsCard(1); document.body.appendChild(card);
  const form = card.querySelector("form");
  const backend = card.querySelector("#tt-backend"), engine = card.querySelector("#tt-engine");
  const model = card.querySelector("#tt-model"), effort = card.querySelector("#tt-effort");
  const custom = card.querySelector("#tt-custom"), customWrap = card.querySelector("#tt-custom-wrap");
  const prompt = card.querySelector("#tt-prompt"), reset = card.querySelector("#tt-reset");
  const sample = card.querySelector("#tt-sample"), enabled = card.querySelector("#tt-enabled");
  const save = card.querySelector("#tt-save"), tryIt = card.querySelector("#tt-try");
  const note = card.querySelector("#tt-note"), error = card.querySelector(".form-error");
  const engineNote = card.querySelector("#tt-engine-note");
  const submit = () => form.onsubmit(new FakeEvent("submit"));
  assert.equal(save.disabled, true); assert.equal(tryIt.disabled, true);
  assert.deepEqual(values(backend), [["0", "Studio (local)", false], ["1", "Peer", false],
    ["2", "Older peer · upgrade to enable", true]]);
  assert.equal(sample.value, "Fix the login redirect loop after signing in");
  assert.equal(enabled.getAttribute("aria-label"), "Session titles disabled; activate to enable");
  await tick(); requests.shift().reject(new Error("Load failed")); await tick();
  assert.equal(error.textContent, "Load failed"); assert.equal(save.disabled, true);
  const retry = card.querySelector("#tt-retry"); assert.equal(retry.classList.contains("hidden"), false);
  let loading = retry.onclick(); requests.shift().resolve(payload({ engine: "claude", model: "haiku", effort: "low" }));
  await loading;
  assert.equal(engine.value, "claude"); assert.equal(model.value, "haiku"); assert.equal(effort.value, "low");
  assert.equal(prompt.value, "Name it: {message}"); assert.equal(save.disabled, false);
  assert.equal(tryIt.disabled, false); assert.equal(note.textContent, "");
  assert.equal(engineNote.textContent, "");
  assert.deepEqual(values(engine), [["", "Choose an engine", false], ["claude", "Claude Code", false],
    ["codex", "Codex", true]]);
  assert.deepEqual(values(model).map(row => row[0]), ["", "haiku", "opus", "__custom__"]);
  assert.deepEqual(values(effort).map(row => row[0]), ["", "low"]);
  same(state.titles, { enabled: false, configured: true, backend: 0, engine: "claude", model: "haiku" });

  // a model change offers that model's efforts and drops one it lacks
  model.value = "opus"; model.onchange();
  assert.deepEqual(values(effort).map(row => row[0]), ["", "high", "max"]);
  assert.equal(effort.value, ""); assert.equal(note.textContent, "Unsaved changes");
  assert.equal(note.classList.contains("dirty"), true);
  // Custom… opens the box, and the typed id is what is saved
  model.value = "__custom__"; model.onchange();
  assert.equal(customWrap.classList.contains("hidden"), false);
  custom.value = "claude-next"; custom.oninput();
  assert.deepEqual(values(effort).map(row => row[0]), ["", "low", "high"]);
  // the prompt's reset takes the controller's default
  prompt.value = "Mine"; prompt.oninput();
  reset.onclick(); assert.equal(prompt.value, "Default prompt {message}");
  prompt.value = "  Name it: {message}  "; prompt.oninput();
  // Try it runs the entered values, saved or not, with the sample
  sample.value = " Add a footer "; sample.oninput();
  let trying = tryIt.onclick(); await tick();
  assert.equal(form.getAttribute("aria-busy"), "true"); assert.equal(save.disabled, true);
  assert.equal(note.textContent, "Titling…");
  let request = requests.shift();
  assert.equal(request.route, "titles/test"); assert.equal(request.options.method, "POST");
  assert.equal(request.options.operation, "Generating a title");
  same(request.options.body, { backend: 0, engine: "claude", model: "claude-next", effort: "",
    prompt: "Name it: {message}", message: "Add a footer" });
  request.resolve({ ok: true, title: "Footer for the page", model: "claude-next", elapsed_s: 4 }); await trying;
  assert.equal(note.textContent, "“Footer for the page” · claude-next · 4s · Unsaved changes");
  assert.equal(save.disabled, false);
  trying = tryIt.onclick(); await tick(); request = requests.shift();
  request.reject(new Error("the model answered without a title")); await trying;
  assert.equal(error.textContent, "the model answered without a title");
  assert.equal(note.textContent, "Unsaved changes");
  // an empty sample cannot be tried
  sample.value = "  "; sample.oninput(); assert.equal(tryIt.disabled, true);
  sample.value = "x"; sample.oninput(); assert.equal(tryIt.disabled, false);

  // Save carries exactly the five settings; the switch is never among them
  let saving = submit(); await tick(); request = requests.shift();
  assert.equal(request.route, "titles"); assert.equal(request.options.method, "PUT");
  same(request.options.body, { backend: 0, engine: "claude", model: "claude-next", effort: "",
    prompt: "Name it: {message}" });
  assert.equal(save.disabled, true);
  request.reject(new Error("Disk full")); await saving;
  assert.equal(error.textContent, "Disk full"); assert.equal(note.textContent, "Unsaved changes");
  assert.equal(custom.value, "claude-next");
  saving = submit(); await tick();
  requests.shift().resolve({ ok: true, settings: settings({ engine: "claude", model: "claude-next" }) }); await saving;
  assert.equal(note.textContent, "Saved"); assert.equal(error.textContent, "");
  same(state.titles, { enabled: false, configured: true, backend: 0, engine: "claude", model: "claude-next" });

  // the switch is its own request and follows the answer
  enabled.checked = true;
  let toggling = enabled.onchange(); await tick(); request = requests.shift();
  assert.equal(request.route, "titles/toggle"); same(request.options.body, { enabled: true });
  assert.equal(enabled.disabled, true);
  assert.equal(enabled.getAttribute("aria-label"), "Updating session titles");
  request.resolve({ ok: true, settings: settings({ enabled: true, engine: "claude", model: "claude-next" }) }); await toggling;
  assert.equal(enabled.checked, true); assert.equal(enabled.disabled, false);
  assert.equal(enabled.getAttribute("aria-label"), "Session titles enabled; activate to disable");
  assert.equal(state.titles.enabled, true);
  enabled.checked = false; toggling = enabled.onchange(); await tick();
  requests.shift().reject(new Error("Toggle failed")); await toggling;
  assert.equal(enabled.checked, true); assert.equal(error.textContent, "Toggle failed");
  // the stream's word moves the switch too, and says when nothing is chosen
  state.titles = { enabled: true, configured: false, backend: 0, engine: "", model: "" };
  for (const sync of context.titlesStateListeners) sync();
  assert.equal(enabled.getAttribute("aria-label"),
    "Session titles enabled, but no engine is chosen, so nothing will be named; activate to disable");
  state.titles = { enabled: true, configured: true, backend: 0, engine: "claude", model: "claude-next" };
  for (const sync of context.titlesStateListeners) sync();

  // another backend: its engines are asked for once, and the pickers follow
  backend.value = "1"; backend.onchange(); await tick();
  assert.equal(engineNote.textContent, "Loading this backend's engines…");
  request = requests.shift(); assert.equal(request.bid, 1); assert.equal(request.route, "engines");
  const remoteClaude = { ...claude, installed: false };
  const remoteCodex = { ...codex, installed: true };
  request.resolve({ engines: [remoteClaude, remoteCodex] }); await tick();
  for (const listener of context.enginePayloadListeners) listener(1, [remoteClaude, remoteCodex]);
  assert.deepEqual(values(engine), [["", "Choose an engine", false], ["claude", "Claude Code", true],
    ["codex", "Codex", false]]);
  // the saved engine stays selected, with the note saying why it will not do
  assert.equal(engine.value, "claude");
  assert.equal(engineNote.textContent, "This engine is not installed on the chosen backend.");
  engine.value = ""; engine.onchange();
  assert.equal(engineNote.textContent, "Choose the engine that names sessions.");
  // a catalog repaint keeps a choice just made, not the saved one
  for (const listener of context.enginePayloadListeners) listener(1, [remoteClaude, remoteCodex]);
  assert.equal(engine.value, "");
  assert.deepEqual(values(model), [["", "Engine default", false]]); assert.equal(model.disabled, true);
  engine.value = "codex"; engine.onchange();
  assert.deepEqual(values(model).map(row => row[0]), ["", "gpt-x"]);   // no Custom… for a catalog-only engine
  assert.equal(engineNote.textContent, "");
  saving = submit(); await tick(); request = requests.shift();
  same(request.options.body, { backend: 1, engine: "codex", model: "", effort: "",
    prompt: "Name it: {message}" });
  request.resolve({ ok: true, settings: settings({ enabled: true, backend: 1, engine: "codex" }) }); await saving;
  // a backend removed while the card is open keeps its saved choice visible
  state.backends = [state.backends[1]]; view.titlesSync();
  assert.equal(backend.value, "1"); assert.equal(backend.options[2].textContent, "Unavailable backend");
  assert.equal(requests.length, 0);
  // a re-render retires the card's listeners
  const listeners = context.titlesStateListeners.size;
  view.titlesRetire(); assert.equal(context.titlesStateListeners.size, listeners - 1);
  console.log("titles settings card ok");

  /* the creation dialogs' choice */
  state.backends = [
    { id: 1, name: "Peer", capabilities: ["spawn-exec", "session-titles"] },
    { id: 2, name: "Older peer", capabilities: ["spawn-exec"] },
  ];
  state.titles = { enabled: false, configured: false, backend: 0, engine: "", model: "" };
  const wrap = document.createElement("div"); wrap.className = "auto-title hidden";
  wrap.innerHTML = `<label class="check"><input type="checkbox" checked> Generate a title</label>
    <p class="help auto-title-note"></p>`;
  const nameInput = document.createElement("input");
  const closers = [];
  let bid = 0;
  const choice = context.wireAutoTitleChoice(wrap, nameInput, () => bid, fn => closers.push(fn),
    "once the first message is sent");
  const check = wrap.querySelector("input"), noteEl = wrap.querySelector(".auto-title-note");
  assert.equal(wrap.classList.contains("hidden"), true); assert.equal(choice.wanted(), false);
  // on, but nothing chosen: still nothing to offer
  state.titles = { enabled: true, configured: false, backend: 0, engine: "", model: "" };
  for (const sync of context.titlesStateListeners) sync();
  assert.equal(wrap.classList.contains("hidden"), true); assert.equal(choice.wanted(), false);
  state.titles = { enabled: true, configured: true, backend: 1, engine: "claude", model: "haiku" };
  state.engCache[1] = [claude];
  for (const sync of context.titlesStateListeners) sync();
  assert.equal(wrap.classList.contains("hidden"), false); assert.equal(choice.wanted(), true);
  assert.equal(noteEl.textContent, "Claude Code · Haiku · Peer names it once the first message is sent.");
  assert.equal(check.disabled, false);
  // a typed name wins
  nameInput.value = "Typed"; nameInput.dispatchEvent(new FakeEvent("input"));
  assert.equal(check.disabled, true); assert.equal(choice.wanted(), false);
  assert.equal(noteEl.textContent, "The name above is kept as typed.");
  nameInput.value = ""; nameInput.dispatchEvent(new FakeEvent("input"));
  assert.equal(check.disabled, false); assert.equal(choice.wanted(), true);
  check.checked = false; assert.equal(choice.wanted(), false); check.checked = true;
  // a backend that keeps no such requests offers nothing
  bid = 2; choice.sync();
  assert.equal(wrap.classList.contains("hidden"), true); assert.equal(choice.wanted(), false);
  bid = 1; choice.sync(); assert.equal(wrap.classList.contains("hidden"), false);
  // the label names the model by its id when no catalog describes it, and
  // leaves out this instance
  state.titles = { enabled: true, configured: true, backend: 0, engine: "codex", model: "gpt-z" };
  assert.equal(context.titlesGeneratorLabel(), "Codex · gpt-z");
  state.titles = { enabled: true, configured: true, backend: 0, engine: "claude", model: "" };
  assert.equal(context.titlesGeneratorLabel(), "Claude Code · default model");
  // closing the dialog retires its listener
  const before = context.titlesStateListeners.size;
  closers.forEach(fn => fn()); assert.equal(context.titlesStateListeners.size, before - 1);
  // the New task dialog carries the choice without a note: offered, disabled
  // under a typed name and released with it, and nothing to write to
  state.titles = { enabled: true, configured: true, backend: 1, engine: "claude", model: "haiku" };
  const bare = document.createElement("div"); bare.className = "auto-title hidden";
  bare.innerHTML = `<label class="check"><input type="checkbox" checked> Generate a title from the task</label>`;
  const bareName = document.createElement("input");
  const bareClosers = [];
  const bareChoice = context.wireAutoTitleChoice(bare, bareName, () => 1, fn => bareClosers.push(fn));
  const bareCheck = bare.querySelector("input");
  assert.equal(bare.querySelector(".auto-title-note"), null);
  assert.equal(bare.classList.contains("hidden"), false); assert.equal(bareChoice.wanted(), true);
  bareName.value = "Typed"; bareName.dispatchEvent(new FakeEvent("input"));
  assert.equal(bareCheck.disabled, true); assert.equal(bareChoice.wanted(), false);
  bareName.value = ""; bareName.dispatchEvent(new FakeEvent("input"));
  assert.equal(bareCheck.disabled, false); assert.equal(bareChoice.wanted(), true);
  assert.equal(bare.children.length, 1, "no note is added for the dialog");
  bareClosers.forEach(fn => fn());
  // what shimmers: a request on its way, never an armed or absent record
  assert.equal(context.titlePending({ auto_title: { state: "requested", requested_at: 1 } }), true);
  assert.equal(context.titlePending({ auto_title: { state: "armed", requested_at: 0 } }), false);
  assert.equal(context.titlePending({ auto_title: null }), false);
  assert.equal(context.titlePending(null), false);
  // ... and the sidebar's row builder puts the shimmer on the name alone
  vm.runInContext(between("function sessionRowRows(", "/* Merge authoritative node lists"), context);
  Object.assign(context, {
    sessDot: () => document.createElement("span"), provIcon: () => document.createElement("span"),
    sessionLocationLabel: () => "/home/mira", sessionLocationTitle: () => "/home/mira",
    sessionWorkspace: () => null, linkForSession: () => null,
  });
  const pendingRow = context.sessionRowRows(0, { id: 5, name: "Weekend notes",
    auto_title: { state: "requested", requested_at: 1 } });
  assert.equal(pendingRow.r1.querySelector(".si-name").className, "si-name titling");
  const plainRow = context.sessionRowRows(0, { id: 6, name: "Garden planner", auto_title: null });
  assert.equal(plainRow.r1.querySelector(".si-name").className, "si-name");
  console.log("title choice ok");
  console.log("generated title UI tests passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
