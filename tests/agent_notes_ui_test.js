/* Run with node tests/agent_notes_ui_test.js. Real modal code, fake DOM;
   no browser, network, engine, or subscription quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => source.slice(source.indexOf(from), source.indexOf(to, source.indexOf(from)));
const document = new FakeDocument();
let dialog, response, failure, pending, requests, confirmations;
const session = { id: 12, name: "Notes", cwd: "/project" };
const context = vm.createContext({
  document,
  modal: (html, className) => {
    const m = document.createElement("div");
    m.className = "modal " + className;
    m.innerHTML = html;
    document.body.appendChild(m);
    dialog = { m, close: () => m.remove() };
    return dialog;
  },
  api: async (bid, route, options) => {
    requests.push({ bid, route, ...options });
    if (options && pending) await pending;
    if (options && failure) throw new Error(failure);
    return response;
  },
  modalConfirm: async (...args) => { confirmations.push(args); return true; },
  sessionLocationLabel: () => "/project",
  sessionsFor: () => [session], renderSidebar: () => {}, toast: () => {},
  xIcon: () => document.createElement("svg"),
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("const AGENT_NOTE_FILES =", "function backendSupportsSessionPinning"),
  between("function describeAgentNote(", "function sessDot("),
].join("\n"), context);
const plain = value => JSON.parse(JSON.stringify(value));
const node = selector => dialog.m.querySelector(selector);
const areas = () => dialog.m.querySelectorAll("textarea");
const file = (name, text = "", exists = !!text, extra = {}) =>
  ({ name, text, exists, readable: true, truncated: false, symlink: "", ...extra });
const data = (a = "", c = "", linked = false, available = true) => ({
  files: [file("AGENTS.md", a), file("CLAUDE.md", c, !!c, { symlink: linked ? "AGENTS.md" : "" })],
  present: [], shared: { linked, available, revision: "revision-1" },
});
async function open(payload) {
  if (dialog) dialog.close();
  response = payload; failure = null; pending = null; requests = []; confirmations = [];
  context.modalAgentNotes(7, session);
  await Promise.resolve(); await Promise.resolve();
  assert.equal(requests[0].route, "sessions/12/agent-notes");
}
function toggle(checked) {
  node("#agent-notes-shared").checked = checked;
  node("#agent-notes-shared").onchange();
}
const submit = () => node("form").onsubmit({ preventDefault() {} });

(async () => {
  await open(data());
  assert.equal(node("#agent-notes-shared-wrap").classList.contains("hidden"), false);
  assert.equal(node("#agent-notes-shared-wrap").classList.contains("check"), true);
  assert.equal(areas().length, 2);
  toggle(true);
  assert.equal(areas().length, 1);
  areas()[0].value = "Shared draft";
  assert.equal(requests.length, 1, "toggling and typing do not write before Save");
  await submit();
  assert.deepEqual(plain(requests[1]), { bid: 7, route: "sessions/12/agent-notes", method: "PUT",
    body: { shared: true, text: "Shared draft", expected_revision: "revision-1" } });
  assert.equal(dialog.m.isConnected, false);

  for (const [a, c] of [["Only agents", ""], ["", "Only claude"], ["Same", "Same"]]) {
    await open(data(a, c));
    toggle(true);
    assert.equal(areas()[0].value, a || c);
    toggle(false);
    assert.deepEqual(areas().map(area => area.value), [a, c], "toggle back preserves original drafts");
    toggle(true);
    areas()[0].value = "New shared draft";
    toggle(false);
    assert.deepEqual(areas().map(area => area.value), ["New shared draft", "New shared draft"]);
  }

  await open(data("Shared", "Shared", true));
  assert.equal(node("#agent-notes-shared").checked, true);
  assert.equal(areas().length, 1);
  await submit();
  assert.equal(requests.length, 1, "unchanged linked text needs no write");
  await open(data("", "", true));
  await submit();
  assert.deepEqual(plain(requests[1].body), { shared: true, text: "", expected_revision: "revision-1" },
    "saving a dangling canonical link initializes its missing source, even with empty text");
  await open(data("Shared", "Shared", true));
  toggle(false);
  assert.equal(areas().length, 2);
  areas()[1].value = "Claude only";
  await submit();
  assert.deepEqual(plain(requests[1].body), { shared: false,
    texts: { "AGENTS.md": "Shared", "CLAUDE.md": "Claude only" }, expected_revision: "revision-1" });

  await open(data("A", "C", false, false));
  assert.equal(node("#agent-notes-shared-wrap").classList.contains("hidden"), true);
  assert.equal(node("#agent-notes-shared-help").classList.contains("hidden"), true);
  assert.equal(areas().length, 2);
  areas()[1].value = "Updated C";
  await submit();
  assert.deepEqual(plain(requests[1].body), { name: "CLAUDE.md", text: "Updated C" });

  const legacy = data("A", "C"); delete legacy.shared;
  await open(legacy);
  assert.equal(node("#agent-notes-shared-wrap").classList.contains("hidden"), true);
  assert.equal(areas().length, 2, "older backends keep their existing editor");

  await open(data("A", ""));
  areas()[1].value = "Different unsaved draft";
  toggle(true);
  assert.equal(node("#agent-notes-shared").checked, false);
  assert.deepEqual(areas().map(area => area.value), ["A", "Different unsaved draft"]);
  assert.match(node(".form-error").textContent, /different text/);

  await open(data("Shared", "Shared", true));
  areas()[0].value = "Keep this draft";
  failure = "the notes changed; reopen the editor before saving";
  let release;
  pending = new Promise(resolve => { release = resolve; });
  const saving = submit();
  assert.equal(node("#agent-notes-shared").disabled, true);
  assert.equal(areas()[0].disabled, true);
  await submit();
  assert.equal(requests.length, 2, "busy form cannot submit twice");
  release(); await saving;
  assert.equal(dialog.m.isConnected, true);
  assert.equal(areas()[0].value, "Keep this draft");
  assert.equal(node("#agent-notes-shared").disabled, false);
  assert.match(node(".form-error").textContent, /notes changed/);

  await open(data("Shared", "Shared", true));
  const remove = node(".agent-notes-remove");
  assert.equal(remove.getAttribute("aria-label"), "Remove AGENTS.md and CLAUDE.md");
  response = data();
  await remove.onclick();
  assert.deepEqual(plain(requests[1].body), { shared: true, delete: true, expected_revision: "revision-1" });
  assert.deepEqual(plain(confirmations[0][2]), { confirmLabel: "Remove", destructive: true });
  assert.equal(areas().length, 2);

  const unreadable = data("", "C", false, false);
  unreadable.files[0].readable = false;
  await open(unreadable);
  areas()[1].value = "Changed";
  failure = "write failed";
  await submit();
  assert.equal(areas()[0].disabled, true, "failed save must not enable an unreadable editor");
  assert.equal(node("#agent-notes-shared").disabled, true);

  await open(data("Shared", "Shared", true));
  let keyboardSubmits = 0;
  node("form").requestSubmit = () => { keyboardSubmits++; };
  for (const modifier of ["ctrlKey", "metaKey"]) {
    const event = new FakeEvent("keydown", { key: "Enter", [modifier]: true });
    areas()[0].dispatchEvent(event);
    assert.equal(event.defaultPrevented, true);
  }
  assert.equal(keyboardSubmits, 2);
  toggle(false);
  node("#agent-notes-cancel").onclick();
  assert.equal(requests.length, 1, "Cancel never applies a link change");
  console.log("agent notes UI tests passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
