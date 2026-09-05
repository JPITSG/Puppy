/* Run with node tests/task_fold_ui_test.js. No browser or engine required.
   The console side of folding a removed task into Main, against the fake DOM:
   the remove confirm and its default, the route chosen per node capability,
   and the archive card built lazily from one transcript row. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  if (start < 0 || end < 0) throw new Error("slice not found: " + from);
  return source.slice(start, end);
};

const document = new FakeDocument();
const state = { backends: [{ id: 7, capabilities: ["session-tasks", "session-task-fold"] }], nodeCapabilities: [] };
let dialog = null, requests = [], toasts = [], apiReply = { folded: true }, confirmResult = true;
/* the dialog frame: real elements from the dialog's own markup, closed by
   leaving the document, so isConnected and onClose behave as in the app */
function modal(html, className = "") {
  const m = document.createElement("div");
  m.className = "modal" + (className ? " " + className : "");
  m.innerHTML = html;
  document.body.appendChild(m);
  const listeners = [];
  const close = () => { if (!m.isConnected) return; m.remove(); for (const listener of listeners) listener(); };
  dialog = { m, close };
  return { m, close, onClose: fn => listeners.push(fn) };
}
async function api(bid, route, options) {
  requests.push({ bid, route, ...options });
  return apiReply;
}
const icon = () => document.createElement("svg");
const context = vm.createContext({
  document, state, modal, api,
  esc: text => String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
  toast: text => toasts.push(text), refreshSessionList: async () => {}, renderSidebar: () => {},
  modalConfirm: async () => confirmResult, sessionDeleteMessage: () => "legacy copy",
  choiceSvg: icon, tasksIcon: icon,
  fmtDateTime: ts => (ts ? "stamp" : ""),
  engineConfigParts: (bid, engine, model, effort) => [String(engine), [model, effort].filter(Boolean).join(" ")],
  linkifyInto: (node, text) => { node.textContent = text; return node; },
  md: text => "<p>" + text + "</p>",
  decorateMarkdownLinks: () => {}, decorateMarkdownImages: () => {}, decorateCodeBlocks: () => {},
  decorateMentionsInto: (node, text) => { node.textContent = text; },
  splitAttachmentMarkers: text => ({ text, attachments: [] }),
  toolIcon: () => "$", toolLabel: tool => String(tool),
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function backendSupportsTaskFold(", "/* \"Enable tasks\""),
  between("/* A removed task's condensed conversation", "/* The sidebar's activity slot"),
  between("/* Removing a task is the moment", "class SessionWorkspaceView {"),
].join("\n"), context);
/* values built inside the vm context carry its own prototypes */
const plain = value => JSON.parse(JSON.stringify(value));
/* everything a reader would see in a subtree: text, plus the hover titles */
const collect = node => [node.textContent, node.title || "", ...node.children.map(collect)].join("\n");
const node = key => dialog.m.querySelector(key);

(async () => {
  const session = { id: 50, name: "Fold me", task: { parent: 10 } };
  // The confirm: folding is on by default, the checkbox is the whole decision,
  // and cancelling by button or backdrop answers null.
  let pending = context.modalRemoveTask(session);
  assert.ok(dialog.m.classList.contains("remove-task-modal"));
  assert.equal(node("#rt-fold").checked, true, "folding is on by default");
  assert.ok(dialog.m.textContent.includes("Fold me"), "the copy names the task");
  assert.equal(document.activeElement, node("#rt-yes"));
  node("#rt-yes").onclick();
  assert.deepEqual(plain(await pending), { fold: true });
  assert.equal(dialog.m.isConnected, false);
  pending = context.modalRemoveTask(session);
  node("#rt-fold").checked = false;
  node("#rt-yes").onclick();
  assert.deepEqual(plain(await pending), { fold: false });
  pending = context.modalRemoveTask(session);
  node("#rt-no").onclick();
  assert.equal(await pending, null);
  pending = context.modalRemoveTask(session);
  dialog.close();
  assert.equal(await pending, null);

  // A capable node gets the fold route with the chosen flag; a legacy node
  // keeps the plain confirm and DELETE, and never sees a fold request.
  let choice = context.confirmTaskRemoval(7, session);
  node("#rt-yes").onclick();
  assert.deepEqual(plain(await choice), { fold: true });
  assert.equal(await context.removeTaskSession(7, session, { fold: true }), true);
  assert.deepEqual(plain(requests.at(-1)), { bid: 7, route: "sessions/10/tasks/50/remove", method: "POST",
    body: { fold: true }, timeoutMs: 120000 });
  apiReply = { folded: false };
  assert.equal(await context.removeTaskSession(7, session, { fold: false }), false);
  assert.deepEqual(plain(requests.at(-1).body), { fold: false });
  state.backends[0].capabilities = ["session-tasks"];
  choice = await context.confirmTaskRemoval(7, session);
  assert.deepEqual(plain(choice), { fold: false, legacy: true });
  assert.equal(await context.removeTaskSession(7, session, choice), false);
  assert.deepEqual(plain(requests.at(-1)), { bid: 7, route: "sessions/50", method: "DELETE" });
  confirmResult = false;
  assert.equal(await context.confirmTaskRemoval(7, session), null);

  // The sheet's Remove, end to end on a capable node.
  state.backends[0].capabilities = ["session-tasks", "session-task-fold"];
  apiReply = { folded: true };
  const run = context.removeTask(7, session);
  node("#rt-yes").onclick();
  await run;
  assert.equal(toasts.at(-1), "Task folded into Main");
  assert.equal(requests.at(-1).route, "sessions/10/tasks/50/remove");
  apiReply = { folded: false };
  const discard = context.removeTask(7, session);
  node("#rt-fold").checked = false;
  node("#rt-yes").onclick();
  await discard;
  assert.equal(toasts.at(-1), "Task removed");
  assert.deepEqual(plain(requests.at(-1).body), { fold: false });

  // The archive card: a one-line head, a body built on the first open only.
  const archive = { subtype: "session_task_archive", task_id: 50, name: "Fold me", prompt: "Do the thing",
    engine: "codex", model: "gpt-5", effort: "high", state: "applied", turns: 2,
    created_at: 1, completed_at: 2, applied_at: 3, folded_at: 4, summary: "Done.",
    applied_files: "A\tfolded.txt\nM\tb.txt", unapplied_files: "A\tlater.txt\n",
    entries: [
      { kind: "user", ts: 1, text: "Do the thing" }, { kind: "tool", ts: 1, tool: "Bash", text: "echo hi" },
      { kind: "aside", ts: 1, text: "why echo?" }, { kind: "aside_answer", ts: 1, text: "because" },
      { kind: "assistant", ts: 2, text: "Done." }, { kind: "error", ts: 2, text: "boom" },
      { kind: "switch", ts: 2, text: "moved from codex to claude" },
      { kind: "thinking", ts: 2, text: "never shown" }, null, "junk"] };
  const card = context.taskArchiveNode(archive, 9, 7);
  assert.ok(card.classList.contains("tool-card") && card.classList.contains("task-archive"));
  const [head, body] = card.children;
  const headText = collect(head);
  for (const part of ["Fold me", "2 prompts", "2 files applied", "folded stamp", "Applied"])
    assert.ok(headText.includes(part), "head shows " + part);
  assert.ok(!headText.includes("Do the thing"), "the head stays one line");
  assert.equal(body.children.length, 0, "the body waits for the first open");
  head.onclick();
  assert.ok(card.classList.contains("open"));
  const bodyText = collect(body);
  for (const part of ["Do the thing", "echo hi", "why echo?", "because", "Done.", "boom",
    "moved from codex to claude", "A  folded.txt", "M  b.txt", "A  later.txt", "codex gpt-5 high", "stamp"])
    assert.ok(bodyText.includes(part), "body shows " + part);
  assert.ok(!bodyText.includes("never shown"), "unknown entry kinds are skipped");
  assert.equal(body.querySelectorAll(".md").length, 2, "assistant and side answers render as markdown");
  assert.equal(body.querySelectorAll(".msg-user").length, 1, "the sent bubble is the transcript's own");
  const built = body.children.length;
  head.onclick();
  assert.ok(!card.classList.contains("open") && body.children.length === built, "closing keeps the built body");
  head.onclick();
  assert.ok(card.classList.contains("open") && body.children.length === built, "reopening does not rebuild");

  assert.deepEqual(plain(context.taskArchiveState({ state: "ready" })), ["Not applied", "warn"]);
  assert.deepEqual(plain(context.taskArchiveState({ state: "failed" })), ["Failed", "bad"]);
  assert.deepEqual(plain(context.taskArchiveState({ state: "pending" })), ["Unfinished", "warn"]);
  assert.deepEqual(plain(context.taskArchiveState({ state: "weird" })), ["weird", ""]);
  assert.deepEqual(plain(context.nameStatusList("A\tfoo\nM\tbar\n\nplain\n")),
    [{ status: "A", path: "foo" }, { status: "M", path: "bar" }, { status: "", path: "plain" }]);

  // Without entries the final answer stands in; a stopped task reads as such.
  const bare = context.taskArchiveNode({ subtype: "session_task_archive", name: "Bare", state: "stopped",
    summary: "Partial.", entries: [] }, 4, 7);
  assert.ok(collect(bare.children[0]).includes("Stopped"));
  assert.ok(!collect(bare.children[0]).includes("prompts"));
  bare.children[0].onclick();
  assert.ok(collect(bare.children[1]).includes("Partial."));
  console.log("PASS: remove confirm default and choices, capability routing, sheet removal toasts, lazily built archive card, states and file lists");
})().catch(error => { console.error(error); process.exitCode = 1; });
