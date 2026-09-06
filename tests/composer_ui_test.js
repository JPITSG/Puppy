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
const storage = new Map();
let reviewDialog;
let blobs = 0;
let controlHold = null;
let historyRead = null;
let storedEvents = [];
const promptEvent = (seq, text) => ({ seq, kind: "user", data: { text } });
function historyPage(route) {
  const query = new URLSearchParams(route.split("?")[1]);
  const before = Number(query.get("before_seq")) || Infinity;
  const kind = query.get("kind");
  return { events: storedEvents.filter(e => e.seq < before && (!kind || e.kind === kind))
    .slice(-Number(query.get("limit"))) };
}
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
  api: async (bid, route, options = {}) => {
    calls.api.push({ bid, route, ...options });
    if (/\/events\?/.test(route))
      return historyRead ? historyRead(bid, route, options) : historyPage(route);
    if (controlHold && /\/(ask|steer)$/.test(route)) await controlHold;
    return {};
  },
  backendSupportsSessionControlSocket: () => false,
  newDraftClientId: () => "control-test", TOAST_LONG: 7000,
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
  WebSocket: { OPEN: 1 },
  lsGet: key => storage.has(key) ? storage.get(key) : null,
  lsSet: (key, value) => storage.set(key, value), lsDel: key => storage.delete(key),
  modal: html => {
    const m = document.createElement("div"); m.innerHTML = html; document.body.appendChild(m);
    const listeners = [];
    reviewDialog = { m, close() { m.remove(); listeners.forEach(fn => fn()); }, onClose: fn => listeners.push(fn) };
    return reviewDialog;
  },
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
  between("const DRAFT_JOURNAL_VERSION", "function snapshotBrowserState"),
  between("class SharedDraft {", "class SessionView {"),
  between("class SessionView {", "/* ================= TermView"),
].join("\n"), context);
const { Composer, composerBoxHtml, SessionView, SharedDraft } = vm.runInContext(
  "({ Composer, composerBoxHtml, SessionView, SharedDraft })", context);

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

  /* Presence belongs to the shared box, never changes the value/caret, and
     releases on blur, backgrounding and destruction. IME owns its preedit. */
  const presence = [], p = makeBox({ typing: active => presence.push(active) });
  p.ta.focus(); type(p.ta, "Keep my selection");
  p.ta.selectionStart = 2; p.ta.selectionEnd = 7;
  const attachmentState = p.composer.attachments;
  p.composer.showPresence(1);
  p.composer.showPresence(2, true);
  assert.equal(p.ta.value, "Keep my selection");
  assert.equal(p.ta.selectionStart, 2); assert.equal(p.ta.selectionEnd, 7);
  assert.equal(p.composer.attachments, attachmentState);
  assert.equal(p.ta.readOnly, false);
  assert.deepEqual(presence, [true], "repeated edits and paints do not flood presence");
  assert.equal(p.composer.protectsDraft(), true);
  fire(p.ta, "compositionstart");
  assert.equal(key(p.ta, "Enter", { isComposing: true }).defaultPrevented, false);
  fire(p.ta, "compositionend");
  fire(p.ta, "blur"); assert.deepEqual(presence, [true, false]);
  type(p.ta, "More");
  document.visibilityState = "hidden"; fire(document, "visibilitychange");
  assert.deepEqual(presence, [true, false, true, false]);
  document.visibilityState = "visible";
  type(p.ta, "Still editable"); p.composer.destroy();
  assert.deepEqual(presence, [true, false, true, false, true, false]);

  /* Real shared-draft state machine with deliberately delayed and colliding
     websocket replies. No replace can run during a local conflict. */
  function draftBox(id) {
    const sent = [], v = { tab: { id }, draftClientId: id, draftClientSeq: 0,
      draftReady: false, draftRevision: 0, draftJournal: null,
      status: "idle", updateRunState() {}, setStatus() {},
      ws: { readyState: 1, bufferedAmount: 0, send: raw => sent.push(JSON.parse(raw)) },
      draftValue() { return this.composer.value(); },
      clearDraftJournal() { storage.delete("puppy.draft." + id); this.draftJournal = null; },
    };
    const box = makeBox({ edited: () => sync.save(), typing: active => sync.typing(active),
      reviewDraft: () => sync.review() });
    v.composer = box.composer;
    const sync = new SharedDraft(v); v.sharedDraft = sync;
    sync.initialize({ text: "shared", revision: 1 });
    return { ...box, v, sync, sent };
  }
  const wire = (text, revision, id = "peer", seq = 1, more = {}) =>
    ({ type: "draft", text, revision, client_id: id, client_seq: seq, ...more });
  const a = draftBox("draft-a");
  a.ta.focus(); type(a.ta, "my first edit"); type(a.ta, "my latest edit");
  assert.equal(a.sent.filter(x => x.type === "draft").length, 1, "typing coalesces while an echo waits");
  a.ta.selectionStart = 3; a.ta.selectionEnd = 8;
  a.sync.receive(wire("their edit", 2));
  a.sync.receive(wire("their edit", 2, "draft-a", 1, { type: "draft_conflict" }));
  assert.equal(a.ta.value, "my latest edit");
  assert.equal(a.ta.selectionStart, 3); assert.equal(a.ta.selectionEnd, 8);
  assert.equal(a.sync.conflict, true);
  const writes = a.sent.filter(x => x.type === "draft").length;
  type(a.ta, "I can keep typing");
  assert.equal(a.sent.filter(x => x.type === "draft").length, writes, "a private fork never overwrites the shared draft");
  a.sync.receive(wire("their newer edit", 3));
  assert.equal(a.ta.value, "I can keep typing");
  assert.equal(JSON.parse(storage.get("puppy.draft.draft-a")).text, "I can keep typing");
  a.v.draftReady = false; a.sync.disconnect();
  a.sync.initialize({ text: "their newest edit", revision: 4 });
  assert.equal(a.ta.value, "I can keep typing"); assert.equal(a.sync.conflict, true);
  a.sync.review();
  assert.equal(reviewDialog.m.querySelector(".draft-local").value, "I can keep typing");
  assert.equal(reviewDialog.m.querySelector(".draft-shared").value, "their newest edit");
  a.sync.receive(wire("changed during review", 5));
  fire(reviewDialog.m.querySelector("form"), "submit");
  assert.equal(reviewDialog.m.querySelector(".form-error").classList.contains("hidden"), false);
  assert.equal(a.sync.conflict, true, "a stale review cannot authorize overwriting a later edit");
  reviewDialog.close(); a.sync.review();
  fire(reviewDialog.m.querySelector("form"), "submit");
  const replacement = a.sent.filter(x => x.type === "draft").at(-1);
  assert.equal(replacement.expected_revision, 5);
  a.sync.receive(wire("I can keep typing", 6, "draft-a", replacement.client_seq));
  assert.equal(a.sync.conflict, false); assert.equal(a.v.draftJournal, null);
  a.ta.focus();
  a.sync.receive(wire("peer edits after my acknowledgement", 7));
  assert.equal(a.ta.value, "I can keep typing", "even acknowledged typing is protected while focused");
  a.sync.review(); reviewDialog.m.querySelector(".draft-use-shared").click();
  assert.equal(a.ta.value, "peer edits after my acknowledgement");

  const idle = draftBox("draft-idle");
  idle.sync.receive(wire("follow on another device", 2));
  assert.equal(idle.ta.value, "follow on another device");
  assert.equal(idle.sync.conflict, false);
  fire(idle.ta, "compositionstart");
  idle.sync.receive(wire("peer during composition", 3));
  assert.equal(idle.ta.value, "follow on another device", "preedit is never interrupted");
  fire(idle.ta, "compositionend");

  /* Send consumes its own saved prefix after overtaking coalesced edits; a
     peer's different draft remains intact and typing after Send survives. */
  const sending = draftBox("draft-send");
  sending.ta.focus(); type(sending.ta, "pre"); type(sending.ta, "precise message");
  const sendValue = sending.composer.value(), message = { text: sending.composer.take() };
  sending.sync.prepareSend(message, sendValue);
  sending.sync.receive(wire("pre", 2, "draft-send", 1));
  assert.equal(sending.ta.value, "");
  sending.sync.receive(wire("pre", 2, "draft-send", message.draft_client_seq, { consumed: false }));
  const clear = sending.sent.filter(x => x.type === "draft").at(-1);
  assert.equal(clear.text, ""); assert.equal(clear.expected_revision, 2);
  type(sending.ta, "next message");
  sending.sync.receive(wire("peer keeps this", 3, "draft-send", clear.client_seq, { type: "draft_conflict" }));
  assert.equal(sending.ta.value, "next message"); assert.equal(sending.sync.conflict, true);
  const next = { text: sending.composer.take() };
  sending.sync.prepareSend(next, "next message");
  sending.sync.receive(wire("peer keeps this", 3, "draft-send", next.draft_client_seq, { consumed: false }));
  assert.equal(sending.ta.value, "peer keeps this");
  for (const entry of [a, idle, sending]) entry.composer.destroy();

  const preedit = draftBox("draft-preedit");
  preedit.ta.focus(); fire(preedit.ta, "compositionstart");
  preedit.v.draftReady = false;
  preedit.sync.initialize({ text: "different attach snapshot", revision: 2 });
  assert.equal(preedit.ta.value, "shared", "initial snapshots cannot interrupt IME either");
  fire(preedit.ta, "compositionend"); preedit.composer.destroy();

  const uploading = draftBox("draft-upload");
  const upload = { uploading: true, name: "notes.txt" };
  uploading.composer.attachments.push(upload);
  uploading.sync.receive(wire("peer while uploading", 2));
  assert.equal(uploading.composer.attachments[0], upload);
  assert.equal(uploading.ta.value, "shared"); assert.equal(uploading.sync.conflict, true);
  uploading.composer.destroy();

  const refused = draftBox("draft-refused");
  refused.ta.focus(); type(refused.ta, "pending prompt");
  refused.sync.receive(wire("pending prompt", 2, "draft-refused", 1));
  const attempted = { text: refused.composer.message() };
  refused.sync.prepareSend(attempted, refused.composer.value());
  assert.equal(refused.ta.value, "pending prompt", "Send waits for acknowledgement without locking the editor");
  type(refused.ta, "pending prompt with a new thought");
  refused.sync.sendFailed({ client_seq: attempted.draft_client_seq, error: "Engine unavailable", accepted: false });
  assert.equal(refused.ta.value, "pending prompt with a new thought");
  assert.equal(refused.sync.sent, 0); assert.equal(refused.sync.sendError, true);
  assert.equal(refused.v.draftJournal.submitted, false);
  // A private send's journal must compare with the server at Send time, not
  // the much older common ancestor of its fork. A lost reply cannot authorize
  // an automatic overwrite of that shared version after a reload.
  refused.sync.receive(wire("peer draft", 8));
  refused.sync.prepareSend({}, refused.composer.value());
  assert.equal(refused.v.draftJournal.baseRevision, 8);
  refused.v.draftReady = false; refused.sync.disconnect();
  refused.sync.initialize({ text: "peer draft", revision: 8 });
  assert.equal(refused.ta.value, "pending prompt with a new thought");
  assert.equal(refused.sync.conflict, true);
  refused.composer.destroy();

  const cleared = draftBox("draft-cleared");
  cleared.ta.focus(); type(cleared.ta, "pre"); type(cleared.ta, "complete prompt");
  const complete = { text: cleared.composer.message() };
  cleared.sync.prepareSend(complete, cleared.composer.value());
  cleared.sync.receive(wire("pre", 2, "draft-cleared", 1));
  cleared.sync.receive(wire("pre", 2, "draft-cleared", complete.draft_client_seq, { consumed: false }));
  const clearAttempt = cleared.sent.filter(x => x.type === "draft").at(-1);
  cleared.sync.receive(wire("a newer peer draft", 3, "draft-cleared", clearAttempt.client_seq, { type: "draft_conflict" }));
  assert.equal(cleared.ta.value, "a newer peer draft");
  assert.equal(cleared.sync.conflict, false, "a rejected cleanup must not create a phantom empty local draft");
  cleared.composer.destroy();

  /* A newly started upload has no marker yet, so value equality cannot
     prove that a delayed Send acknowledgement may clear the whole box. */
  for (const cleanupFailed of [false, true]) {
    const id = "draft-send-upload-" + cleanupFailed, box = draftBox(id);
    const sent = { text: box.composer.message() };
    box.sync.prepareSend(sent, box.composer.value());
    const upload = { uploading: true, name: "next-file.txt" };
    box.composer.attachments.push(upload);
    if (cleanupFailed)
      box.sync.sendFailed({ client_seq: sent.draft_client_seq, accepted: true, error: "Cleanup failed" });
    else
      box.sync.receive(wire("", 2, id, sent.draft_client_seq, { consumed: true }));
    assert.equal(box.composer.attachments[0], upload, "acknowledgement keeps the new upload");
    assert.equal(box.ta.value, "shared", "the editor stays intact while the next upload is pending");
    box.composer.destroy();
  }

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
  type(b.ta, "");
  type(b.ta, "@ab");
  b.composer.dismissMentionOutside(b.ta);
  assert.ok(b.composer.mention, "typing retains the mention list");
  b.composer.dismissMentionOutside(b.box.querySelector(".mention-pop button"));
  assert.ok(b.composer.mention, "pressing a mention row leaves it available for its click");
  b.composer.dismissMentionOutside(c.ta);
  assert.equal(b.composer.mention, null, "another composer dismisses the list");
  type(b.ta, "@ab");
  b.composer.dismissMentionOutside(document.createElement("button"));
  assert.equal(b.composer.mention, null, "touching a control dismisses without requiring blur");
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

  /* shell-style recall comes from the node, even on a fresh box */
  storedEvents = [promptEvent(1, "first"), promptEvent(2, "second")];
  b.ta.selectionStart = b.ta.selectionEnd = 0;
  key(b.ta, "ArrowUp");
  await settle();
  assert.equal(b.ta.value, "second");
  b.ta.selectionStart = b.ta.selectionEnd = 0;
  key(b.ta, "ArrowUp");
  assert.equal(b.ta.value, "first");
  key(b.ta, "ArrowDown");
  assert.equal(b.ta.value, "second");
  key(b.ta, "ArrowDown");
  assert.equal(b.ta.value, "", "walking past the newest entry returns the draft");
  assert.equal(b.composer.history, null);

  /* A second device, a remote task host, more than a thousand prompts, long
     tool-only tails, duplicate text and attachment-only prompts. */
  state.backends.push({ id: 7, capabilities: ["session-prompt-history"] });
  storedEvents = Array.from({ length: 1203 }, (_, i) =>
    promptEvent(i * 3 + 1, i < 2 ? "same prompt" : "prompt " + i));
  storedEvents[2].data.text = marker;
  storedEvents.push(...Array.from({ length: 700 }, (_, i) =>
    ({ seq: 3610 + i, kind: "tool_result", data: { text: "tool output" } })));
  const firstDevice = makeBox({ bid: 7, sid: 42, privateUploads: () => false });
  const secondDevice = makeBox({ bid: 7, sid: 42, privateUploads: () => false });
  const up = box => { box.ta.selectionStart = box.ta.selectionEnd = 0; return key(box.ta, "ArrowUp"); };
  const down = box => { box.composer.caretToEnd(); return key(box.ta, "ArrowDown"); };
  firstDevice.composer.replace("unfinished\n\n" + marker);
  const parked = firstDevice.composer.attachments[0];
  mark = calls.api.length;
  up(firstDevice); up(secondDevice); await settle();
  assert.equal(firstDevice.ta.value, "prompt 1202");
  assert.equal(secondDevice.ta.value, firstDevice.ta.value);
  assert.ok(calls.api.slice(mark).every(c => c.bid === 7 &&
    c.route.startsWith("sessions/42/events?limit=50&kind=user") && c.cache === "no-store"));
  storedEvents.push(promptEvent(5000, "sent from another device"));
  const walk = firstDevice.composer.history;
  firstDevice.composer.replace(firstDevice.composer.value());
  assert.equal(firstDevice.composer.history, walk, "unchanged reconnect draft preserves the walk");
  for (let i = 1201; i >= 0; i--) {
    up(firstDevice); await settle();
    assert.equal(firstDevice.composer.message(), storedEvents[i].data.text, "prompt " + i);
  }
  assert.equal(firstDevice.ta.value, "same prompt", "the first prompt is reachable");
  const pageCount = calls.api.length - mark;
  up(firstDevice); await settle();
  assert.equal(calls.api.length - mark, pageCount, "the beginning does not fetch forever");
  assert.equal(firstDevice.composer.history.index, 1202);
  for (let i = 0; i < 1203; i++) down(firstDevice);
  assert.equal(firstDevice.composer.value(), "unfinished\n\n" + marker);
  assert.equal(firstDevice.composer.attachments[0], parked, "original staged chip survives the whole walk");
  assert.equal(firstDevice.composer.history, null);
  up(firstDevice); await settle();
  assert.equal(firstDevice.ta.value, "sent from another device", "each new walk refreshes the tail");
  firstDevice.composer.destroy(); secondDevice.composer.destroy();

  /* Older nodes page the ordinary transcript, including empty prompt pages. */
  state.backends[0].capabilities = [];
  const older = makeBox({ bid: 7 });
  storedEvents = [promptEvent(1, "very first"), ...Array.from({ length: 650 }, (_, i) =>
    ({ seq: i + 2, kind: "assistant", data: { text: "reply" } }))];
  mark = calls.api.length;
  up(older); await settle();
  assert.equal(older.ta.value, "very first");
  assert.equal(calls.api.length - mark, 4);
  assert.ok(calls.api.slice(mark).every(c => !c.route.includes("kind=")));
  older.composer.destroy();

  const offline = makeBox({ bid: 7 });
  offline.composer.replace("offline draft");
  context.backendConnectionAllowed = () => false;
  mark = calls.api.length;
  up(offline); await settle();
  assert.equal(calls.api.length, mark, "recall never probes a backend already marked offline");
  assert.equal(offline.ta.value, "offline draft");
  offline.composer.destroy();
  context.backendConnectionAllowed = () => true;

  /* Pending reads coalesce repeated arrows and never overwrite a later edit,
     draft from another console, send, host transaction, or closed box. */
  for (const action of ["repeat", "down", "type", "composing", "shared", "take", "busy", "destroy", "upload", "remove"]) {
    const box = makeBox({ privateUploads: () => false });
    box.composer.replace("draft\n\n" + marker);
    let release;
    historyRead = () => new Promise(resolve => { release = resolve; });
    mark = calls.api.length;
    up(box);
    const request = calls.api[calls.api.length - 1];
    assert.equal(box.ta.getAttribute("aria-busy"), "true");
    if (action === "repeat") { up(box); up(box); down(box); }
    else if (action === "down") down(box);
    else if (action === "type") type(box.ta, "new edit");
    else if (action === "composing") fire(box.ta, "compositionstart");
    else if (action === "shared") box.composer.replace("remote edit");
    else if (action === "take") box.composer.take();
    else if (action === "busy") box.composer.setBusy(true);
    else if (action === "destroy") box.composer.destroy();
    else if (action === "upload") paste(box.ta);
    else if (action === "remove") box.box.querySelector(".attach-x").onclick();
    const expected = box.composer.value();
    assert.equal(calls.api.length - mark, 1, "one pending history read");
    release({ events: [promptEvent(1, "oldest"), promptEvent(2, "middle"), promptEvent(3, "newest")] });
    await settle();
    if (action === "repeat") assert.equal(box.ta.value, "middle", "rapid Up/Down intentions are preserved");
    else {
      assert.equal(box.composer.value(), expected, action + " survives a late response");
      assert.ok(request.signal.aborted, action + " cancels the read");
    }
    assert.equal(box.ta.getAttribute("aria-busy"), null);
    if (action === "upload") { completeUpload(calls.fetch[calls.fetch.length - 1]); await settle(); }
    if (action !== "destroy") box.composer.destroy();
    historyRead = null;
  }

  /* Recall is a local editing intention before its first response arrives.
     Main's protected drafts keep a peer from cancelling it, and its guarded
     Send must cancel an earlier read even while it retains the editor. */
  const recalling = draftBox("draft-recall");
  recalling.ta.focus();
  let releaseRecall;
  historyRead = () => new Promise(resolve => { releaseRecall = resolve; });
  up(recalling);
  const pendingWalk = recalling.composer.history;
  assert.equal(recalling.composer.locallyEdited, false, "recall is pending before any value change");
  recalling.sync.receive(wire("peer draft during recall", 2));
  assert.equal(recalling.composer.history, pendingWalk, "focused recall survives a conflicting peer draft");
  assert.equal(recalling.ta.value, "shared");
  assert.equal(recalling.sync.conflict, true);
  releaseRecall({ events: [promptEvent(1, "the original prompt")] });
  await settle();
  assert.equal(recalling.ta.value, "the original prompt");
  assert.equal(recalling.sent.filter(frame => frame.type === "draft").length, 0,
    "recall must not overwrite the newer shared draft during a conflict");
  down(recalling);
  assert.equal(recalling.ta.value, "shared", "the parked local draft is restored");
  assert.equal(recalling.sync.server.text, "peer draft during recall");
  recalling.composer.destroy();

  const sendingRecall = draftBox("draft-send-recall");
  up(sendingRecall);
  const recallRequest = calls.api[calls.api.length - 1];
  const pendingSend = { text: sendingRecall.composer.message() };
  sendingRecall.sync.prepareSend(pendingSend, sendingRecall.composer.value());
  assert.ok(recallRequest.signal.aborted, "guarded Send cancels an earlier read before acknowledgement");
  assert.equal(sendingRecall.ta.value, "shared");
  releaseRecall({ events: [promptEvent(1, "must not arrive after Send")] });
  await settle();
  assert.equal(sendingRecall.ta.value, "shared");
  sendingRecall.sync.receive(wire("", 2, "draft-send-recall", pendingSend.draft_client_seq, { consumed: true }));
  assert.equal(sendingRecall.ta.value, "");
  sendingRecall.composer.destroy();
  historyRead = null;

  /* A failed older page retains the displayed prompt and retries the same
     cursor. Repeated text is still two separate positions. */
  const retry = makeBox();
  storedEvents = Array.from({ length: 51 }, (_, i) => promptEvent(i + 1, "entry " + i));
  up(retry); await settle();
  for (let i = 0; i < 49; i++) up(retry);
  historyRead = async () => { throw new Error("connection lost"); };
  up(retry); await settle();
  assert.equal(retry.ta.value, "entry 1");
  assert.match(calls.toasts[calls.toasts.length - 1].text, /Press Up to retry/);
  const failedRoute = calls.api[calls.api.length - 1].route;
  historyRead = null;
  up(retry); await settle();
  assert.equal(calls.api[calls.api.length - 1].route, failedRoute);
  assert.equal(retry.ta.value, "entry 0");
  retry.composer.destroy();

  for (const response of [{ events: [] }, { events: [promptEvent(0, "broken")] }, {}]) {
    const box = makeBox();
    box.composer.replace("keep my draft\n\n" + marker);
    historyRead = async () => response;
    up(box); await settle();
    assert.equal(box.composer.value(), "keep my draft\n\n" + marker);
    assert.equal(box.composer.history, null);
    box.composer.destroy();
  }
  historyRead = null;

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
  mark = calls.api.length;
  up(b);
  assert.equal(calls.api.length, mark, "a held box cannot start recall");
  assert.equal(b.box.querySelector(".attach-add").disabled, true);
  mark = calls.fetch.length;
  paste(b.ta);
  assert.equal(calls.fetch.length, mark);
  const heldValue = b.composer.value();
  assert.equal(b.box.querySelector(".attach-x").disabled, true);
  b.box.querySelector(".attach-x").onclick();
  assert.equal(b.composer.value(), heldValue, "busy chips cannot be removed, even by a queued click");
  b.composer.renderAttachments();
  assert.equal(b.box.querySelector(".attach-x").disabled, true, "rerenders keep the removal lock");
  b.composer.setBusy(false);
  assert.equal(b.ta.readOnly, false);
  assert.equal(b.box.querySelector(".attach-add").disabled, false);
  assert.equal(b.box.querySelector(".attach-x").disabled, false);

  /* leaving: a cancelled dialog discards, a closing view keeps, in flight is
     discarded when its late answer lands */
  const cancelled = makeBox();
  paste(cancelled.ta);
  const idc = completeUpload(calls.fetch[calls.fetch.length - 1]);
  await settle();
  mark = calls.api.length;
  cancelled.composer.setBusy(true);
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

  /* Real Ask/Steer handoffs must treat a newly added file like a text edit,
     including uploads with no path yet and shared edits from another device. */
  for (const socket of [false, true]) for (const action of ["ask", "steer"])
    for (const change of ["none", "text", "composing", "uploading", "uploaded", "shared"]) {
      const box = makeBox({ privateUploads: () => false });
      type(box.ta, "Question");
      let release;
      controlHold = new Promise(resolve => { release = resolve; });
      context.backendSupportsSessionControlSocket = () => socket;
      let saves = 0;
      const view = Object.create(SessionView.prototype);
      Object.assign(view, { composer: box.composer, tab: { bid: 0, sid: 10 }, draftReady: true,
        sideQuestion: { ready: true, turn_id: "turn" }, steering: { ready: true, turn_id: "turn" },
        updateAskControl() {}, updateSteerControl() {}, scrollBottom() {}, saveDraft() { saves++; },
        sendActiveTurnControl: (kind, body) => context.api(0, `sessions/10/${kind}`, { body }),
      });
      const pending = view[action]();
      const request = calls.api[calls.api.length - 1];
      assert.equal(request.body[action === "ask" ? "question" : "text"], "Question");
      let upload;
      if (change === "text") type(box.ta, "Next message");
      else if (change === "composing") fire(box.ta, "compositionstart");
      else if (change === "shared") box.composer.replace("Question\n\n" + marker);
      else if (["uploading", "uploaded"].includes(change)) {
        paste(box.ta); upload = calls.fetch[calls.fetch.length - 1];
        if (change === "uploaded") { completeUpload(upload); await settle(); }
      }
      release(); await pending; controlHold = null;
      assert.equal(saves, change === "none" ? 1 : 0, `${action}/${change}: clear only an unchanged draft`);
      if (change === "none") assert.ok(box.composer.isEmpty());
      else if (change === "text") assert.equal(box.ta.value, "Next message");
      else if (change === "composing") {
        assert.equal(box.ta.value, "Question", `${action}: preedit survives acknowledgement`);
        assert.equal(box.composer.composing, true);
        fire(box.ta, "compositionend");
      }
      else {
        assert.equal(box.ta.value, "Question");
        assert.equal(box.composer.attachments.length, 1, `${action}/${change}: unsent chip survives`);
        if (change === "uploading") { completeUpload(upload); await settle(); }
        assert.ok(box.composer.attachments[0].path, "late upload still completes into the retained draft");
      }
      box.composer.destroy();
    }
  console.log("PASS: shared prompt box keys, mentions, attachments, full stored recall across devices, paging/retry/cancellation, shared replacement, host gating and release paths");
})().catch(error => { console.error(error); process.exitCode = 1; });
