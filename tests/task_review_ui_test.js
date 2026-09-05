/* Run with node tests/task_review_ui_test.js. No browser or engine required. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => source.slice(source.indexOf(from), source.indexOf(to, source.indexOf(from)));
class Element {
  constructor(tag, cls = "", text = "") {
    this.tag = tag; this.textContent = text; this.children = []; this.checked = false; this.disabled = false;
    this.classes = new Set(cls.split(/\s+/));
    this.classList = { add: key => this.classes.add(key), remove: key => this.classes.delete(key),
      contains: key => this.classes.has(key) };
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.children = []; }
}
let dialog, requests, notices, response, failure, held;
const state = { nodeCapabilities: ["session-task-conflict-resolution"],
  backends: [{ id: 7, capabilities: ["session-task-conflict-resolution"] }] };
const session = { id: 12, name: "Task", task: { state: "ready", summary: "Checked <script>text</script>" } };
const workspace = { tab: { bid: 7, sid: 10 }, openTask(id) { this.opened = id; } };
const changed = { files: "M\ta.txt\n", diff: "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\n+new\n",
  token: "review-token", has_changes: true };
function modal(html) {
  const nodes = {};
  for (const match of html.matchAll(/<(\w+)\b([^>]*)>/g)) {
    const [, tag, attrs] = match;
    const cls = /class="([^"]*)"/.exec(attrs)?.[1] || "";
    const node = new Element(tag, cls);
    node.disabled = /\bdisabled\b/.test(attrs);
    for (const name of cls.split(/\s+/).filter(Boolean)) nodes["." + name] = node;
    const id = /\bid="([^"]+)"/.exec(attrs)?.[1];
    if (id) nodes["#" + id] = node;
  }
  const m = { html, isConnected: true, querySelector: key => nodes[key] || null };
  dialog = { m, nodes, close() { m.isConnected = false; } };
  return dialog;
}
async function api(bid, route, options) {
  requests.push({ bid, route, ...options });
  if (held) await held;
  if (failure) throw new Error(failure);
  return response;
}
const context = vm.createContext({ state, modal, api, el: (...args) => new Element(...args),
  fmtStamp: () => "12:00", TOAST_LONG: 7000, toast: text => notices.push(text), refreshSessionList: async () => {}, renderSidebar() {} });
vm.runInContext([
  between("function nodeHasCapability(", "/* \"Enable tasks\""),
  between("const TASK_STATES =", "/* two offset frames:"),
  between("async function modalReviewTask(", "class SessionView {"),
].join("\n"), context);
async function open(data = changed) {
  response = data; failure = ""; requests = []; notices = []; held = null; workspace.opened = null;
  await context.modalReviewTask(workspace, session);
  return dialog.nodes;
}
const lastBody = () => JSON.parse(JSON.stringify(requests.at(-1).body));
(async () => {
  let nodes = await open();
  assert.equal(nodes["#tr-resolve"].checked, false, "each review starts opted out");
  assert.equal(nodes["#tr-resolve"].disabled, false);
  assert.equal(nodes["#tr-resolve-wrap"].classes.has("hidden"), false);
  assert.match(dialog.m.html, /aria-describedby="tr-resolve-note"/);
  assert.match(dialog.m.html, /one follow-up/);
  assert.match(dialog.m.html, /normal model quota/);
  assert.match(dialog.m.html, /inspect Main read-only when needed/);
  assert.match(dialog.m.html, /review and apply again/);
  assert.match(dialog.m.html, /one at a time/);
  assert.match(dialog.m.html, /<span class="note-para">If Main has conflicting changes/, "the note runs to paragraphs");
  assert.match(dialog.m.html, /<span class="note-para">You must review and apply again/);
  assert.equal(nodes[".task-review-truncated"].classes.has("hidden"), true, "a full preview has no shortened notice");
  assert.match(nodes[".task-review-note"].textContent, /^Applying writes/, "the apply note is its own paragraph");
  assert.equal(nodes[".task-review-summary"].textContent, session.task.summary, "summary remains literal text");
  assert.ok(nodes[".task-review-diff"].children.some(line => line.classes.has("add")));
  response = { applied: true };
  await nodes["#tr-apply"].onclick();
  assert.deepEqual(lastBody(), { token: changed.token, resolve_conflicts: false });
  assert.equal(dialog.m.isConnected, false);
  assert.match(notices[0], /applied to Main/);
  assert.equal(workspace.opened, null);

  nodes = await open({ ...changed, truncated: true });
  assert.equal(nodes[".task-review-truncated"].classes.has("hidden"), false, "a cut preview says so above the note");
  assert.match(nodes[".task-review-note"].textContent, /^Applying writes/);

  nodes = await open();
  nodes["#tr-resolve"].checked = true;
  response = { applied: false, resolving: true };
  let release; held = new Promise(resolve => { release = resolve; });
  const pending = nodes["#tr-apply"].onclick();
  assert.equal(nodes["#tr-apply"].disabled, true);
  assert.equal(nodes["#tr-resolve"].disabled, true);
  assert.match(nodes["#tr-apply"].textContent, /Applying/);
  await nodes["#tr-apply"].onclick();
  assert.equal(requests.length, 2, "review plus exactly one apply even under a double click");
  release(); await pending; held = null;
  assert.deepEqual(lastBody(), { token: changed.token, resolve_conflicts: true });
  assert.equal(workspace.opened, session.id);
  assert.equal(dialog.m.isConnected, false);
  assert.match(notices[0], /resolution started/);
  assert.doesNotMatch(notices[0], /applied to Main/);

  nodes = await open();
  assert.equal(nodes["#tr-resolve"].checked, false, "opting in does not persist into a later review");
  failure = "Main is busy";
  nodes["#tr-resolve"].checked = true;
  await nodes["#tr-apply"].onclick();
  assert.equal(dialog.m.isConnected, true);
  assert.equal(nodes["#tr-apply"].disabled, false);
  assert.equal(nodes["#tr-resolve"].disabled, false);
  assert.equal(nodes["#tr-resolve"].checked, true, "a refused request keeps the dialog choice");
  assert.equal(nodes[".form-error"].textContent, failure);
  assert.equal(notices.length, 0);
  failure = ""; response = { applied: true };
  await nodes["#tr-apply"].onclick();
  assert.deepEqual(lastBody(), { token: changed.token, resolve_conflicts: true });

  nodes = await open({ token: changed.token, has_changes: false });
  assert.equal(nodes["#tr-resolve-wrap"].classes.has("hidden"), true);
  assert.equal(nodes["#tr-resolve"].disabled, true);
  assert.equal(nodes["#tr-apply"].textContent, "Mark as reviewed");
  response = { applied: true }; await nodes["#tr-apply"].onclick();
  assert.deepEqual(lastBody(), { token: changed.token });
  assert.match(notices[0], /marked as reviewed/);

  session.task.state = "applied";
  nodes = await open({ token: changed.token, has_changes: false });
  assert.equal(nodes["#tr-apply"].classes.has("hidden"), false, "an applied task can still be reviewed again");
  assert.equal(nodes["#tr-apply"].disabled, false);
  assert.equal(nodes["#tr-apply"].textContent, "Mark as reviewed");
  response = { applied: true }; await nodes["#tr-apply"].onclick();
  assert.deepEqual(lastBody(), { token: changed.token });
  assert.equal(dialog.m.isConnected, false);
  session.task.state = "ready";

  state.backends[0].capabilities = ["session-tasks"];
  nodes = await open();
  assert.equal(nodes["#tr-resolve"], undefined, "old nodes are not offered an unsupported option");
  response = { applied: true }; await nodes["#tr-apply"].onclick();
  assert.deepEqual(lastBody(), { token: changed.token });
  workspace.tab.bid = 0;
  nodes = await open();
  assert.ok(nodes["#tr-resolve"], "local and remote capability lists both work");

  failure = "Review unavailable";
  await context.modalReviewTask(workspace, session);
  assert.equal(dialog.nodes["#tr-apply"].disabled, true);
  assert.equal(dialog.nodes["#tr-resolve"].disabled, true);
  console.log("PASS: review toggle explanation, defaults, opt-in, duplicate guard, progress, errors, task navigation, empty changes and mixed-version nodes");
})().catch(error => { console.error(error); process.exitCode = 1; });
