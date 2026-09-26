/* Run with node tests/model_substitute_ui_test.js. No browser or engine.
   Another model answering in place of the requested one, against the fake
   DOM: the node's model_substitute taken only for the model the head names,
   the model's name in the head and the composer's Model value taking the warn
   class exactly while it applies (never for a queued change, a queued engine
   switch or a request the stand-in does not belong to), what each says to a
   screen reader, and the transcript's cards - where a stand-in started, where
   the requested model came back, the neutral move, rows from before the state
   field, and the summary above the result with catalog labels, spellings kept
   apart where two would read the same, the engine's default and its own
   reason. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}

const document = new FakeDocument();
const ENGINE = {
  key: "claude", label: "Claude Code",
  model_options: [
    { value: "", label: "Default" },
    { value: "main", label: "Main 1", aliases: ["main[1m]", "vendor-main-1", "vendor-main-1[1m]"] },
    { value: "lite", label: "Lite 2", aliases: ["vendor-lite-2"] },
    { value: "tiny", label: "Tiny 3", aliases: ["vendor-tiny-3"] },
  ],
};
const state = { engines: [ENGINE], engMap: { claude: ENGINE }, engCache: {} };
const context = vm.createContext({
  document, state, console,
  backendName: () => "Studio",
  workspaceLocationLabel: () => "~/projects/harbor",
  workspaceLocationTitle: () => "/home/mira/projects/harbor",
  renderTabs() {},
  linkifyInto: (node, text) => { node.textContent = text; return node; },
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function engineInfo(", "function engineReady("),
  between("function engineModelOption(", "function engineOffersModel("),
  between("function taskUpdateNode(", "function sessionTaskUpdateNode("),
  between("function effectiveQueuedConfig(", "/* Whether two session rows differ"),
  between("class SessionView {", "/* ================= TermView"),
  "globalThis.SessionView = SessionView;",
].join("\n"), context);
const c = context;

/* ---- what the node says, and when it is the head's to say ---- */
const sub = { requested: "main", served: "vendor-lite-2", baseline: "", note: "" };
assert.equal(c.sessionModelSubstitute({ last_model: "vendor-lite-2", model_substitute: sub }), sub);
assert.equal(c.sessionModelSubstitute({ last_model: "vendor-main-1", model_substitute: sub }), null,
  "a turn's own first report moved the head's model: the node's word is for another one");
assert.equal(c.sessionModelSubstitute({ last_model: "vendor-lite-2" }), null, "an older node says nothing");
assert.equal(c.sessionModelSubstitute({ last_model: "", model_substitute: { ...sub, served: "" } }), null);
assert.equal(c.sessionModelSubstitute(null), null);
assert.equal(c.modelStandInWords("", "claude", sub),
  "Lite 2 is answering instead of the requested Main 1");
assert.equal(c.modelStandInWords("", "claude", { requested: "", served: "vendor-lite-2", baseline: "vendor-main-1" }),
  "Lite 2 is answering instead of the engine's default, Main 1");
assert.equal(c.modelStandInWords("", "claude", { requested: "", served: "vendor-lite-2", baseline: "" }),
  "Lite 2 is answering instead of the engine's default model");
assert.equal(c.modelStandInWords("", "claude", { requested: "main[1m]", served: "vendor-main-1" }),
  "vendor-main-1 is answering instead of the requested main[1m]",
  "two spellings that read the same keep the engine's own");
assert.equal(c.modelStandInWords("", "other", { requested: "big", served: "small" }),
  "small is answering instead of the requested big", "a catalog that knows neither");
console.log("PASS: the node's stand-in is taken for the model the head names, in catalog words");

/* ---- the head and the composer ---- */
function view(session, queued = []) {
  const v = Object.create(c.SessionView.prototype);
  v.tab = { id: "s::1", type: "session", bid: "", sid: 1 };
  v.root = document.createElement("div");
  v.root.innerHTML = `<span class="chip eng"><span class="dot"></span><span class="eng-label">…</span></span>
    <span class="chip cwd"></span>
    <button class="mini perm"><span class="mini-key">Permissions:</span><span class="mini-value">auto</span></button>
    <button class="mini model"><span class="mini-key">Model:</span><span class="mini-value">auto</span></button>
    <button class="mini effort"><span class="mini-key">Effort:</span><span class="mini-value">auto</span></button>`;
  document.body.appendChild(v.root);
  v.session = session;
  v.queued = queued;
  for (const name of ["syncTaskReviewMenu", "syncMetaVisibility", "syncWorkspaceChip",
    "syncBrowserChips", "syncSwitchLines", "syncNativeComposerChoices", "syncFastIndicator",
    "syncComposerMeta", "syncHeadOverflow"]) v[name] = () => {};
  v.updateHead();
  return v;
}
const head = v => v.root.querySelector(".chip.eng .eng-model");
const chip = v => v.root.querySelector(".chip.eng");
const mini = v => v.root.querySelector(".mini.model");
const session = (extra = {}) => ({
  id: 1, name: "Harbor", engine: "claude", model: "main", effort: "high",
  permission_mode: "auto", last_model: "vendor-lite-2", model_substitute: { ...sub }, ...extra,
});

let v = view(session());
assert.equal(head(v).textContent, "vendor-lite-2");
assert.ok(head(v).classList.contains("substituted"), "the head's model name takes the warn class");
assert.equal(v.root.querySelector(".eng-label").textContent, "Studio · Claude Code · vendor-lite-2");
assert.equal(chip(v).getAttribute("aria-label"),
  "Studio · Claude Code · vendor-lite-2 · Lite 2 is answering instead of the requested Main 1");
assert.equal(mini(v).querySelector(".mini-value").textContent, "main");
assert.ok(mini(v).classList.contains("substituted"), "and so does the request it stands in for");
assert.equal(mini(v).getAttribute("aria-label"),
  "Model: main · Lite 2 is answering instead of the requested Main 1");

/* the requested model answering again: both go back */
v.session = session({ last_model: "vendor-main-1", model_substitute: null });
v.updateHead();
assert.equal(head(v).textContent, "vendor-main-1");
assert.equal(head(v).classList.contains("substituted"), false);
assert.equal(chip(v).hasAttribute("aria-label"), false);
assert.equal(mini(v).classList.contains("substituted"), false);
assert.equal(mini(v).getAttribute("aria-label"), "Model: main");

/* turn_init moved the head's model before the node's word arrived */
v.session = session();
v.updateHead();
v.session.last_model = "vendor-main-1";
v.updateHead();
assert.equal(head(v).classList.contains("substituted"), false);
assert.equal(mini(v).classList.contains("substituted"), false);

/* a change waiting in the queue is what the pill shows: that request has not
   been stood in for, and the head still says what is answering now */
v = view(session(), [{ kind: "config", engine: "claude", model: "tiny" }]);
assert.ok(head(v).classList.contains("substituted"));
assert.equal(mini(v).querySelector(".mini-value").textContent, "tiny");
assert.ok(mini(v).classList.contains("pending"));
assert.equal(mini(v).classList.contains("substituted"), false);
/* an effort change alone leaves the model's pill saying it */
v = view(session(), [{ kind: "config", engine: "claude", effort: "max" }]);
assert.ok(mini(v).classList.contains("substituted"));
/* a queued engine switch names the target's model instead */
v = view(session(), [{ kind: "engine", engine: "codex", model: "gpt", effort: "", permission_mode: "auto", fast_mode: "off" }]);
assert.equal(head(v).classList.contains("substituted"), false);
assert.equal(mini(v).classList.contains("substituted"), false);
assert.match(chip(v).getAttribute("aria-label"), /engine switch queued/);
/* a request the stand-in does not belong to is never claimed */
v = view(session({ model: "tiny" }));
assert.ok(head(v).classList.contains("substituted"), "the node only says so while it applies");
assert.equal(mini(v).classList.contains("substituted"), false);
/* the engine's default, stood in for */
v = view(session({ model: "", model_substitute: { requested: "", served: "vendor-lite-2", baseline: "vendor-main-1", note: "" } }));
assert.equal(mini(v).querySelector(".mini-value").textContent, "auto");
assert.ok(mini(v).classList.contains("substituted"));
assert.equal(mini(v).getAttribute("aria-label"),
  "Model: auto · Lite 2 is answering instead of the engine's default, Main 1");
console.log("PASS: the head's model name and the composer's Model value take the warn class exactly while the stand-in applies");

/* ---- the transcript ---- */
const card = node => ({
  kind: [...node._classes].filter(name => name !== "task-update").join(" "),
  label: node.querySelector(".task-update-label").textContent,
  tone: [...node.querySelector(".task-update-label")._classes]
    .filter(name => !["task-update-label", "state-word"].includes(name)).join(" "),
  text: (node.querySelector(".task-update-text") || { textContent: "" }).textContent,
});
const note = "Switched to Lite 2 due to high demand for Main 1";
assert.deepEqual(card(c.modelSwitchNode({ state: "substituted", engine: "claude", requested: "main",
  served: "vendor-lite-2", note, text: note }, "")),
  { kind: "model-update", label: "Model switched", tone: "warn", text: note },
  "the engine's own words when it gave them");
assert.deepEqual(card(c.modelSwitchNode({ state: "substituted", engine: "claude", requested: "main",
  served: "vendor-tiny-3", note: "", text: "vendor-tiny-3 is answering instead of the requested main" }, "")),
  { kind: "model-update", label: "Model switched", tone: "warn",
    text: "Tiny 3 is answering instead of the requested Main 1" });
assert.deepEqual(card(c.modelSwitchNode({ state: "resumed", engine: "claude", requested: "main",
  served: "vendor-main-1", text: "vendor-main-1 is answering again" }, "")),
  { kind: "model-update", label: "Requested model resumed", tone: "ok", text: "Main 1 is answering again" });
assert.deepEqual(card(c.modelSwitchNode({ state: "changed", engine: "claude", from_model: "vendor-main-0",
  to_model: "vendor-main-1", text: "engine model changed: vendor-main-0 → vendor-main-1" }, "")),
  { kind: "model-update", label: "Engine model changed", tone: "", text: "vendor-main-0 → vendor-main-1" });
/* rows written before the state field read as they always did */
assert.deepEqual(card(c.modelSwitchNode({ text: "engine model changed: a → b" }, "")),
  { kind: "model-update", label: "Engine model changed", tone: "", text: "a → b" });
assert.deepEqual(card(c.modelSwitchNode({ text: "requested model 'main' but engine is serving b" }, "")),
  { kind: "model-update", label: "Engine model notice", tone: "warn",
    text: "requested model 'main' but engine is serving b" });

const summary = extra => card(c.modelSubstitutedNode({ subtype: "model_substituted", engine: "claude",
  requested: "main", baseline: "", models: ["vendor-lite-2"], throughout: true, note: "", text: "raw", ...extra }, ""));
assert.deepEqual(summary({ note }), { kind: "model-summary", label: "Answered by another model", tone: "warn",
  text: "Lite 2 answered this prompt instead of the requested Main 1\n" + note });
assert.equal(summary({ throughout: false }).text, "Lite 2 answered part of this prompt instead of the requested Main 1");
assert.equal(summary({ models: ["vendor-lite-2", "vendor-tiny-3"] }).text,
  "Lite 2 and Tiny 3 answered this prompt instead of the requested Main 1");
assert.equal(summary({ models: ["vendor-lite-2", "vendor-tiny-3", "other-4"] }).text,
  "Lite 2, Tiny 3 and other-4 answered this prompt instead of the requested Main 1");
assert.equal(summary({ tool: "compact" }).text, "Lite 2 answered this turn instead of the requested Main 1");
assert.equal(summary({ requested: "", baseline: "vendor-main-1" }).text,
  "Lite 2 answered this prompt instead of the engine's default, Main 1");
assert.equal(summary({ requested: "main[1m]", models: ["vendor-main-1"] }).text,
  "vendor-main-1 answered this prompt instead of the requested main[1m]");
assert.deepEqual(summary({ models: [] }), { kind: "model-summary", label: "Answered by another model",
  tone: "warn", text: "raw" });
const hostile = c.modelSubstitutedNode({ engine: "claude", requested: "main", models: ["<img src=x>"],
  note: '<script>alert(1)</script>' }, "");
assert.equal(hostile.querySelector("img"), null);
assert.equal(hostile.querySelector("script"), null);
console.log("PASS: the transcript's cards name each switch, the return, the neutral move and older rows; the summary reads in catalog words");
