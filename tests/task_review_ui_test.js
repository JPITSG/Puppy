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
let dialog, requests, notices, response, failure, held, foldFailure, navigation, refreshFailure;
const state = { nodeCapabilities: ["session-task-conflict-resolution", "session-task-fold"],
  backends: [{ id: 7, capabilities: ["session-task-conflict-resolution", "session-task-fold"] }] };
const session = { id: 12, name: "Task", task: { parent: 10, state: "ready", summary: "Checked <script>text</script>" } };
const workspace = { tab: { id: "s:7:10", bid: 7, sid: 10 }, openTask(id) { this.opened = id; },
  closeTask(id) { navigation.push(["close", id]); },
  closeTaskOverview() { navigation.push(["closeOverview"]); },
  select(id) { navigation.push(["select", id]); } };
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
  if (route.endsWith("/remove")) {
    if (foldFailure) throw new Error(foldFailure);
    return { folded: true };
  }
  return response;
}
const context = vm.createContext({ state, modal, api, el: (...args) => new Element(...args),
  backendName: bid => bid ? "Remote" : "Local",
  fmtStamp: () => "12:00", TOAST_LONG: 7000, toast: text => notices.push(text),
  activateTab: id => navigation.push(["activate", id]),
  refreshSessionList: async () => { if (refreshFailure) throw new Error(refreshFailure); }, renderSidebar() {} });
vm.runInContext([
  between("function backendSupportsTaskFold(", "/* \"Enable tasks\""),
  between("const TASK_STATES =", "/* two offset frames:"),
  between("async function removeTaskSession(", "async function removeTask("),
  between("async function modalReviewTask(", "class SessionView {"),
].join("\n"), context);
async function open(data = changed) {
  response = data; failure = ""; requests = []; notices = []; held = null; workspace.opened = null;
  foldFailure = ""; navigation = []; refreshFailure = "";
  await context.modalReviewTask(workspace, session);
  return dialog.nodes;
}
const lastBody = () => JSON.parse(JSON.stringify(requests.at(-1).body));
(async () => {
  let nodes = await open();
  assert.equal(nodes["#tr-resolve"].checked, false, "each review starts opted out");
  assert.equal(nodes["#tr-resolve"].disabled, false);
  assert.equal(nodes["#tr-resolve-wrap"].classes.has("hidden"), false);
  assert.equal(nodes["#tr-fold"].checked, false, "folding starts opted out");
  assert.equal(nodes["#tr-fold"].disabled, false);
  assert.equal(nodes["#tr-fold-wrap"].classes.has("hidden"), false);
  assert.match(dialog.m.html, /aria-describedby="tr-fold-note"/);
  assert.match(dialog.m.html, /permanently remove the task's private working copy/);
  assert.ok(dialog.m.html.indexOf('id="tr-fold-wrap"') > dialog.m.html.indexOf('id="tr-resolve-wrap"'),
    "folding is the bottom review option");
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
  assert.equal(nodes[".task-review-summary"], undefined, "review omits the task summary");
  assert.equal(nodes[".task-review-summary-lbl"], undefined, "review omits the summary label");
  assert.ok(nodes[".task-review-diff"].children.some(line => line.classes.has("add")));
  response = { applied: true };
  await nodes["#tr-apply"].onclick();
  assert.deepEqual(lastBody(), { token: changed.token, resolve_conflicts: false });
  assert.equal(dialog.m.isConnected, false);
  assert.match(notices[0], /applied to Main/);
  assert.equal(workspace.opened, null);
  assert.equal(requests.length, 2, "default applies without removing the task");
  assert.deepEqual(navigation, []);

  for (const bid of [0, 7]) {
    for (const hasChanges of [true, false]) {
      workspace.tab.bid = bid;
      workspace.tab.id = `s:${bid}:10`;
      nodes = await open({ ...changed, has_changes: hasChanges });
      nodes["#tr-fold"].checked = true;
      response = { applied: true };
      await nodes["#tr-apply"].onclick();
      assert.deepEqual(requests.map(request => request.route), [
        "sessions/10/tasks/12/review", "sessions/10/tasks/12/apply", "sessions/10/tasks/12/remove"]);
      assert.ok(requests.every(request => request.bid === bid));
      assert.deepEqual(lastBody(), { fold: true });
      assert.deepEqual(navigation, [["close", 12], ["closeOverview"], ["select", 10], ["activate", `s:${bid}:10`]]);
      assert.equal(dialog.m.isConnected, false);
      assert.match(notices[0], /applied and folded into Main/);
    }
  }

  nodes = await open();
  assert.equal(nodes["#tr-fold"].checked, false, "folding choice does not persist across reviews");
  nodes["#tr-fold"].checked = true;
  response = { applied: true }; foldFailure = "Task started working";
  await nodes["#tr-apply"].onclick();
  assert.equal(dialog.m.isConnected, true);
  assert.match(nodes[".form-error"].textContent, /Applied to Main, but.*Task started working/);
  assert.equal(nodes["#tr-apply"].textContent, "Retry folding");
  assert.equal(nodes["#tr-fold"].disabled, true);
  assert.equal(nodes["#tr-resolve"].disabled, true);
  assert.deepEqual(navigation, [], "failed folding leaves the task open and focused");
  assert.deepEqual(notices, []);
  foldFailure = "";
  await nodes["#tr-apply"].onclick();
  assert.equal(requests.filter(request => request.route.endsWith("/apply")).length, 1,
    "fold retry never reuses the consumed apply token");
  assert.equal(requests.filter(request => request.route.endsWith("/remove")).length, 2);
  assert.equal(dialog.m.isConnected, false);
  assert.deepEqual(navigation, [["close", 12], ["closeOverview"], ["select", 10], ["activate", "s:7:10"]]);

  nodes = await open();
  nodes["#tr-fold"].checked = true;
  response = { applied: true }; refreshFailure = "Could not refresh sessions";
  await nodes["#tr-apply"].onclick();
  assert.equal(dialog.m.isConnected, false, "list refresh failure does not offer apply or fold again");
  assert.equal(requests.length, 3);
  assert.equal(notices.at(-1), `Remote: ${refreshFailure}`);

  state.backends[0].capabilities = ["session-task-conflict-resolution"];
  nodes = await open();
  assert.equal(nodes["#tr-fold-wrap"].classes.has("hidden"), true);
  assert.equal(nodes["#tr-fold"].disabled, true);
  response = { applied: true }; await nodes["#tr-apply"].onclick();
  assert.equal(requests.length, 2, "backends without folding still apply normally");
  state.backends[0].capabilities.push("session-task-fold");

  nodes = await open({ ...changed, truncated: true });
  assert.equal(nodes[".task-review-truncated"].classes.has("hidden"), false, "a cut preview says so above the note");
  assert.match(nodes[".task-review-note"].textContent, /^Applying writes/);

  nodes = await open();
  nodes["#tr-resolve"].checked = true;
  nodes["#tr-fold"].checked = true;
  response = { applied: false, resolving: true };
  let release; held = new Promise(resolve => { release = resolve; });
  const pending = nodes["#tr-apply"].onclick();
  assert.equal(nodes["#tr-apply"].disabled, true);
  assert.equal(nodes["#tr-resolve"].disabled, true);
  assert.equal(nodes["#tr-fold"].disabled, true);
  assert.match(nodes["#tr-apply"].textContent, /Applying/);
  await nodes["#tr-apply"].onclick();
  assert.equal(requests.length, 2, "review plus exactly one apply even under a double click");
  release(); await pending; held = null;
  assert.deepEqual(lastBody(), { token: changed.token, resolve_conflicts: true });
  assert.equal(workspace.opened, session.id);
  assert.equal(dialog.m.isConnected, false);
  assert.match(notices[0], /resolution started/);
  assert.doesNotMatch(notices[0], /applied to Main/);
  assert.equal(requests.length, 2, "conflict resolution never folds the task");
  assert.deepEqual(navigation, []);

  nodes = await open();
  assert.equal(nodes["#tr-resolve"].checked, false, "opting in does not persist into a later review");
  failure = "Main is busy";
  nodes["#tr-resolve"].checked = true;
  nodes["#tr-fold"].checked = true;
  await nodes["#tr-apply"].onclick();
  assert.equal(dialog.m.isConnected, true);
  assert.equal(nodes["#tr-apply"].disabled, false);
  assert.equal(nodes["#tr-resolve"].disabled, false);
  assert.equal(nodes["#tr-resolve"].checked, true, "a refused request keeps the dialog choice");
  assert.equal(nodes[".form-error"].textContent, failure);
  assert.equal(notices.length, 0);
  assert.equal(nodes["#tr-fold"].disabled, false);
  assert.equal(nodes["#tr-fold"].checked, true);
  assert.equal(requests.length, 2, "a refused apply never removes the task");
  assert.deepEqual(navigation, []);
  nodes["#tr-fold"].checked = false;
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

  workspace.tab.bid = 0;
  nodes = await open();
  assert.ok(nodes["#tr-resolve"], "local and remote capability lists both work");

  failure = "Review unavailable";
  await context.modalReviewTask(workspace, session);
  assert.equal(dialog.nodes["#tr-apply"].disabled, true);
  assert.equal(dialog.nodes["#tr-resolve"].disabled, true);
  assert.equal(dialog.nodes["#tr-fold"].disabled, true);
  console.log("PASS: review toggles, apply then fold, fold retry, defaults, duplicate guard, progress, errors, Main focus, empty changes and local/remote capabilities");
})().catch(error => { console.error(error); process.exitCode = 1; });
