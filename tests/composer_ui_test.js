/* Run with node tests/composer_ui_test.js. No browser or engine required.
   The shared prompt box (Composer in app.js) against the fake DOM: its
   markup, key contract, "@" list, pasted/dropped/attached files becoming
   marker lines, sent-message recall, value replacement, host gating and
   what leaving the box releases. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument, FakeEvent, fire } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  if (start < 0 || end < 0) throw new Error("slice not found: " + from);
  return source.slice(start, end);
};

const document = new FakeDocument();
const calls = { api: [], fetch: [], toasts: [] };
let blobs = 0;
const state = {
  backends: [], tabs: [], engCache: {}, engines: [], views: {}, active: "",
  remoteBrowserStatus: {}, remoteUploadSettings: {}, terminalInstances: { 0: [{ id: "T1T1", session_id: 10 }] },
  browserStatus: { enabled: true, instances: [
    { id: "AB12", session_id: 10, running: true }, { id: "CD34", session_id: 11, running: true }] },
  uploadSettings: { enabled: true, max_file_size_mb: 32, max_file_size_bytes: 32 * 1024 * 1024 },
};
const icon = () => document.createElement("svg");
const context = vm.createContext({
  document, window: { CSS: { supports: () => true } }, CSS: { supports: () => true },
  Event: FakeEvent, console,
  URL: { createObjectURL: () => "blob:fake-" + (++blobs), revokeObjectURL: () => {} },
  AbortController, setTimeout, clearTimeout, getComputedStyle: () => ({}),
  fetch: (url, init) => new Promise(resolve => calls.fetch.push({ url, init, resolve })),
  api: async (bid, route, options = {}) => { calls.api.push({ bid, route, ...options }); return {}; },
  apiPath: (bid, route) => (bid ? `/api/b/${bid}/` : "/api/") + route,
  toast: (text, level) => calls.toasts.push({ text, level }),
  state,
  esc: text => String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
  fmtBytes: value => value + " B",
  plusIcon: icon, xIcon: icon, attachmentFileIcon: icon, globeIcon: icon, terminalIcon: icon,
  choiceSvg: icon, refreshIcon: icon,
  uploadSettingsFor: bid => bid ? state.remoteUploadSettings[bid] || null : state.uploadSettings,
  rememberUploadSettings: () => null,
  backendSupportsFileUploads: () => true,
  backendConnectionAllowed: () => true,
  nodeStateStreamActive: () => true,
  browserEnabledFor: () => true,
  backendHasCapability: (backend, capability) => !!backend && backend.capabilities.includes(capability),
  spawnExecFor: () => false,
  findSessionMeta: (bid, sid) => ({ name: "Session " + sid }),
  backendName: bid => bid ? "node " + bid : "this node",
  browserInstancesFor: () => true, terminalInstancesFor: () => true,
  uploadPreviewUrl: () => "",
  rememberEnginePayload: () => {},
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("const CARET_MIRROR_STYLES", "/* ctrl+j ->"),
  between("const MENTION_QUERY_MAX", "/* ================= Composer ================="),
  between("/* ================= Composer =================", "/* ================= SessionView ================="),
  between("const ATTACHMENT_PREVIEW_TYPES", "/* Resolve the configuration at the queue tail"),
].join("\n"), context);
const { Composer, composerBoxHtml } = vm.runInContext("({ Composer, composerBoxHtml })", context);

function makeBox(host = {}, options = {}) {
  const wrap = document.createElement("div");
  wrap.innerHTML = composerBoxHtml({ placeholder: "Type…", ...options });
  document.body.appendChild(wrap);
  const box = wrap.querySelector(".composer-box");
  const events = { submit: 0, edited: 0, updated: 0 };
  const composer = new Composer(box, { bid: 0, sid: 10,
    submit: () => events.submit++, edited: () => events.edited++, updated: () => events.updated++, ...host });
  return { wrap, box, composer, events, ta: composer.ta };
}
const key = (ta, name, init = {}) => fire(ta, "keydown", { key: name, ...init });
const type = (ta, text) => { ta.value = text; ta.selectionStart = ta.selectionEnd = text.length; fire(ta, "input"); };
const image = { name: "shot.png", size: 5, type: "image/png" };
const paste = (ta, file = image) => fire(ta, "paste", { clipboardData: { items: [{ kind: "file", getAsFile: () => file }] } });
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
/* the node's answer to one upload: an id in its own shape, the stored path */
function completeUpload(entry, over = {}) {
  const id = "17000000000" + String(calls.fetch.indexOf(entry)).padStart(2, "0") + "-a1b2c3d4e5";
  const name = decodeURIComponent(entry.init.headers["X-Puppy-Filename"]);
  entry.resolve({ ok: true, status: 200, json: async () => ({ ok: true, upload_id: id,
    path: `/srv/data/uploads/10/${id}/${name}`, name, size: 5,
    content_type: entry.init.headers["Content-Type"], ...over }) });
  return id;
}
const deletes = from => calls.api.slice(from).filter(c => c.method === "DELETE").map(c => c.route);

(async () => {
  /* markup: one builder for every host, with the host's controls in place */
  const wrap = document.createElement("div");
  wrap.innerHTML = composerBoxHtml({ id: "nt-prompt", placeholder: "Describe…", rows: 6, className: "mention-below",
    controls: '<button class="mini extra"></button>', actions: '<button class="btn-send">Send</button>' });
  const built = wrap.querySelector(".composer-box");
  assert.ok(built.classList.contains("mention-below"));
  assert.equal(built.querySelector("textarea").id, "nt-prompt");
  assert.equal(built.querySelector("textarea").getAttribute("rows"), "6");
  assert.equal(built.querySelector("textarea").getAttribute("placeholder"), "Describe…");
  for (const selector of [".mention-pop", ".attach-strip", ".composer-row .composer-meta-scroll .attach-add",
      ".composer-meta-scroll .extra", ".composer-row .btn-send", ".attach-input"])
    assert.ok(built.querySelector(selector), selector);

  /* keys */
  const b = makeBox();
  assert.equal(Composer.of(b.ta), b.composer);
  type(b.ta, "hello");
  assert.deepEqual(b.events, { submit: 0, edited: 1, updated: 1 });
  assert.equal(key(b.ta, "Enter").defaultPrevented, true);
  assert.equal(b.events.submit, 1, "Enter sends");
  assert.equal(key(b.ta, "Enter", { shiftKey: true }).defaultPrevented, false, "Shift+Enter is a newline");
  assert.equal(b.events.submit, 1);
  assert.equal(key(b.ta, "Escape").defaultPrevented, false, "no list and no escape hook: the key propagates to a dialog");
  b.composer.newline();
  assert.equal(b.ta.value, "hello\n");
  assert.equal(b.events.edited, 2, "ctrl+j goes through the edit path");
  assert.equal(b.composer.message(), "hello");
  assert.equal(b.composer.value(), "hello\n");
  assert.equal(b.composer.sendBlocker(), "");
  assert.equal(b.composer.take(), "hello");
  assert.equal(b.ta.value, "");
  assert.equal(b.events.edited, 2, "take persists nothing itself; the send owns its draft frame");
  assert.ok(b.composer.isEmpty());
  let escapes = 0;
  const c = makeBox({ escape: event => { escapes++; event.preventDefault(); } });
  assert.equal(key(c.ta, "Escape").defaultPrevented, true);
  assert.equal(escapes, 1);

  /* the "@" list */
  type(b.ta, "see @ab");
  assert.deepEqual(Array.from(b.composer.mention.items.map(item => item.label)), ["Browser AB12"]);
  assert.equal(b.composer.mention.items[0].hint, "this session");
  assert.equal(b.box.querySelector(".mention-pop").classList.contains("hidden"), false);
  assert.equal(key(b.ta, "Enter").defaultPrevented, true);
  assert.equal(b.ta.value, "see @Browser AB12 ");
  assert.equal(b.composer.mention, null);
  assert.equal(b.events.submit, 1, "completing a mention is not a send");
  type(b.ta, "@");
  assert.deepEqual(Array.from(b.composer.mention.items.map(item => item.label)),
    ["Browser AB12", "Browser CD34", "Terminal T1T1", "Session", "New browser", "New terminal"]);
  assert.equal(b.composer.mention.items[1].hint, "Session 11");
  key(b.ta, "ArrowDown");
  assert.equal(b.composer.mention.sel, 1);
  const escaped = key(b.ta, "Escape");
  assert.ok(escaped.defaultPrevented && escaped.propagationStopped, "Escape closes the list and nothing else");
  assert.equal(b.composer.mention, null);
  assert.equal(escapes, 1);
  const m = makeBox({ selfHint: "Main" });
  type(m.ta, "@AB1");
  assert.equal(m.composer.mention.items[0].hint, "Main", "a host names its own session in its own words");
  m.composer.mentionData.browsers = [{ id: "EF56", session_id: 10, running: true }];
  m.composer.updateMention();
  assert.equal(m.composer.mention, null, "a refreshed catalog drops instances that are gone");
  type(m.ta, "@ef");
  assert.deepEqual(Array.from(m.composer.mention.items.map(item => item.label)), ["Browser EF56"]);

  /* paste -> upload -> chip -> marker line */
  type(b.ta, "A task");
  assert.equal(paste(b.ta).defaultPrevented, true);
  assert.equal(calls.fetch.length, 1);
  assert.equal(calls.fetch[0].url, "/api/sessions/10/upload");
  assert.equal(calls.fetch[0].init.headers["X-Puppy-Filename"], "shot.png");
  assert.equal(b.composer.sendBlocker(), "Wait for file uploads to finish");
  assert.equal(b.box.querySelector(".attach-strip").classList.contains("hidden"), false);
  assert.ok(b.box.querySelector(".attach-chip.image.uploading"));
  const id1 = completeUpload(calls.fetch[0]);
  await settle();
  assert.equal(b.composer.sendBlocker(), "");
  assert.equal(b.box.querySelector(".attach-chip.uploading"), null);
  const marker = `[image attached: /srv/data/uploads/10/${id1}/shot.png — view it with your image/file tools]`;
  assert.equal(b.composer.message(), "A task\n\n" + marker);
  assert.equal(b.composer.value(), "A task\n\n" + marker);

  /* drop */
  const notes = { name: "notes.txt", size: 5, type: "text/plain" };
  const transfer = { types: ["Files"], items: [{ kind: "file", getAsFile: () => notes }], files: [notes] };
  fire(b.box, "dragenter", { dataTransfer: transfer });
  assert.ok(b.box.classList.contains("file-drag"));
  assert.equal(b.box.dataset.dropHint, "Drop files to attach");
  fire(b.box, "drop", { dataTransfer: transfer });
  assert.equal(b.box.classList.contains("file-drag"), false);
  const id2 = completeUpload(calls.fetch[1]);
  await settle();
  assert.equal(b.composer.attachments.length, 2);
  assert.equal(b.composer.message(), "A task\n\n" + marker + "\n" +
    `[file attached: /srv/data/uploads/10/${id2}/notes.txt (notes.txt, 5 B) — inspect it with your file tools]`);

  /* removing a chip deletes the bytes only for a box that owns them alone */
  let mark = calls.api.length;
  b.box.querySelectorAll(".attach-x")[1].onclick();
  assert.equal(b.composer.attachments.length, 1);
  assert.deepEqual(deletes(mark), [`sessions/10/upload/${id2}`]);
  const shared = makeBox({ privateUploads: () => false });
  paste(shared.ta);
  completeUpload(calls.fetch[2]);
  await settle();
  mark = calls.api.length;
  shared.box.querySelector(".attach-x").onclick();
  assert.deepEqual(deletes(mark), [], "a shared draft may still name the file on another device");

  /* take carries the message, keeps the sent preview, empties the box */
  assert.equal(b.composer.take(), "A task\n\n" + marker);
  assert.equal(b.composer.attachments.length, 0);
  assert.equal(b.composer.value(), "");
  assert.match(b.composer.sentPreview(`/srv/data/uploads/10/${id1}/shot.png`), /^blob:/);

  /* shell-style recall */
  b.composer.setHistory(["first", "second"]);
  b.ta.selectionStart = b.ta.selectionEnd = 0;
  key(b.ta, "ArrowUp");
  assert.equal(b.ta.value, "second");
  b.ta.selectionStart = b.ta.selectionEnd = 0;
  key(b.ta, "ArrowUp");
  assert.equal(b.ta.value, "first");
  key(b.ta, "ArrowDown");
  assert.equal(b.ta.value, "second");
  key(b.ta, "ArrowDown");
  assert.equal(b.ta.value, "", "walking past the newest entry returns the draft");
  assert.equal(b.composer.histIdx, null);

  /* a shared value replaces text and chips without persisting anything */
  const edited = b.events.edited;
  const draft = "hello\n\n" + marker;
  b.composer.replace(draft, true);
  assert.equal(b.ta.value, "hello");
  assert.equal(b.ta.selectionStart, 5);
  assert.equal(b.composer.attachments.length, 1);
  assert.equal(b.composer.value(), draft);
  assert.equal(b.events.edited, edited);

  /* a host may refuse files beyond the node's own policy */
  const blocked = makeBox({ uploadsBlocked: () => "Upgrade this node to attach files to tasks" });
  assert.equal(blocked.box.querySelector(".attach-add").disabled, true);
  assert.equal(blocked.box.querySelector(".attach-add").getAttribute("aria-label"),
    "Upgrade this node to attach files to tasks");
  mark = calls.fetch.length;
  paste(blocked.ta);
  assert.equal(calls.fetch.length, mark);
  assert.equal(calls.toasts[calls.toasts.length - 1].text, "Upgrade this node to attach files to tasks");
  fire(blocked.box, "dragenter", { dataTransfer: transfer });
  assert.ok(blocked.box.classList.contains("drop-rejected"));
  fire(blocked.box, "dragleave", {});

  /* a host transaction holds the box */
  b.composer.setBusy(true);
  assert.equal(b.ta.readOnly, true);
  assert.equal(b.box.querySelector(".attach-add").disabled, true);
  mark = calls.fetch.length;
  paste(b.ta);
  assert.equal(calls.fetch.length, mark);
  b.composer.setBusy(false);
  assert.equal(b.ta.readOnly, false);
  assert.equal(b.box.querySelector(".attach-add").disabled, false);

  /* leaving: a cancelled dialog discards, a closing view keeps, in flight is
     discarded when its late answer lands */
  const cancelled = makeBox();
  paste(cancelled.ta);
  const idc = completeUpload(calls.fetch[calls.fetch.length - 1]);
  await settle();
  mark = calls.api.length;
  cancelled.composer.destroy({ discardUploads: true });
  assert.deepEqual(deletes(mark), [`sessions/10/upload/${idc}`]);
  assert.equal(Composer.live.has(cancelled.composer), false);
  assert.equal(Composer.of(cancelled.ta), null);
  const view = makeBox();
  paste(view.ta);
  completeUpload(calls.fetch[calls.fetch.length - 1]);
  await settle();
  mark = calls.api.length;
  view.composer.destroy();
  assert.deepEqual(deletes(mark), []);
  const late = makeBox();
  paste(late.ta);
  const pending = calls.fetch[calls.fetch.length - 1];
  mark = calls.api.length;
  late.composer.destroy({ discardUploads: true });
  assert.ok(pending.init.signal.aborted);
  const idl = completeUpload(pending);
  await settle();
  assert.deepEqual(deletes(mark), [`sessions/10/upload/${idl}`]);
  assert.equal(document.listeners.selectionchange.length, Composer.live.size,
    "every live box listens to the caret once; none outlives its box");
  console.log("PASS: shared prompt box markup, key contract, @ list, paste/drop/attach to marker lines, chip removal ownership, recall, shared replacement, host gating, busy hold and release paths");
})().catch(error => { console.error(error); process.exitCode = 1; });
