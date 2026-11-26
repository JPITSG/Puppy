/* Run with node tests/shared_files_ui_test.js. A file an agent shares is a
   link in its answer that downloads that session's own copy: the one link
   scheme that survives sanitizing, drawn as a download where the words have a
   session and as plain words where they have none, named and sized by one
   read of the session's shares, unavailable once its file is gone, explained
   rather than followed when it cannot work, and a chip on the share tool's
   own card. No browser, engine, network or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const {FakeDocument, FakeElement, FakeEvent} = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
};

/* the browser's own childNodes: a run of text, then the elements */
Object.defineProperty(FakeElement.prototype, "childNodes", {
  configurable: true,
  get() {
    const nodes = [];
    if (this._text) {
      const text = this.ownerDocument.createTextNode(this._text);
      this._text = "";
      nodes.push(text);
    }
    return nodes.concat(this.children);
  },
});

const document = new FakeDocument();
const icon = () => document.createElement("svg");
const toasts = [];
const reads = [];
let answer = null;
const state = {backends: [], remoteOk: {}, instance: "desk"};
const context = vm.createContext({
  document, Map, Set, URL, console, state,
  downloadIcon: icon,
  toast: (text, tone) => toasts.push([text, tone]),
  api: (bid, route, options) => {
    const read = {bid, route, options};
    read.promise = new Promise((resolve, reject) => { read.resolve = resolve; read.reject = reject; });
    reads.push(read);
    return read.promise;
  },
  backendConnectionAllowed: bid => !bid || state.remoteOk[bid] !== false,
  backendName: bid => bid ? (state.backends.find(item => item.id === bid) || {}).name : state.instance,
  /* the card's own machinery, beside the shares, is the tool card suite's */
  toolStateInto() {}, toolInterruptedInto() {}, backgroundTaskStateInto() {},
  questionMarkChosen() {}, toolImageItems: () => [], toolImagesInto() {},
  baseName: value => String(value).split("/").pop(),
  displayValue: value => typeof value === "string" ? value : JSON.stringify(value, null, 2),
  linkifyInto: (node, text) => { node.textContent = text; return node; },
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function apiPath(", "async function api("),
  between("function fmtBytes(", "\n}\n") + "\n}\n",
  between("function backendHasCapability(", "function backendSupportsShutdownNotice("),
  between("const MD_URI_PATTERN", "function md(text)"),
  between("function decorateMarkdownLinks(", "/* The same problem one step worse"),
  between("/* ================= files an agent shares", "const PLAIN_CODE_LANGS"),
  between("function fillToolResultInto(", "/* ================= tool images"),
  "globalThis.sharedFileSessions = sharedFileSessions; globalThis.MD_URI_PATTERN = MD_URI_PATTERN;",
].join("\n"), context);

const ID = "f" + "0123456789abcdef".repeat(2);
const OTHER = "f" + "fedcba9876543210".repeat(2);
const THIRD = "f" + "00112233445566778899aabbccddeeff";
const settle = () => new Promise(resolve => setImmediate(resolve));
const bubble = html => {
  const box = document.createElement("div");
  box.className = "md";
  box.innerHTML = html;
  document.body.appendChild(box);
  return box;
};
const press = node => {
  const event = new FakeEvent("click", {bubbles: true});
  node.dispatchEvent(event);
  return event;
};
const reset = () => {
  context.sharedFileSessions.clear();
  reads.length = 0;
  toasts.length = 0;
};

/* The sanitizer keeps exactly DOMPurify's own rule plus the two schemes. */
{
  const allowed = context.MD_URI_PATTERN;
  for (const ok of ["https://example.test/x", "http://a", "mailto:x@y", "tel:1", "#frag", "/abs",
                    "relative/path.stl", "puppy-file:" + ID, "PUPPY-FILE:" + ID])
    assert.ok(allowed.test(ok), ok);
  for (const bad of ["javascript:alert(1)", "data:text/html,x", "vbscript:x", "file:///etc/passwd",
                     "puppy-files:" + ID, "blob:https://x/y"])
    assert.ok(!allowed.test(bad), bad);
  const vendored = fs.readFileSync(path.join(__dirname, "../puppy/static/vendor/purify.min.js"), "utf8");
  assert.ok(vendored.includes(String(allowed).replace("|puppy-file|puppy-preview", "")
    .replace(/\/i$/, "/i")),
    "the vendored DOMPurify's own pattern, with puppy-file and puppy-preview the only additions");
  console.log("PASS: the sanitizer keeps DOMPurify's own link rule with puppy-file and puppy-preview the only additions");
}

/* A link in words that have a session is that session's download. */
(async () => {
  reset();
  const box = bubble(`<p>The latest STL file is <a href="puppy-file:${ID}">here</a> and the
    <a href="puppy-file:${OTHER}">log</a>; see <a href="https://example.test/">docs</a> or
    <a href="src/main.py">main.py</a> or <a href="puppy-file:fNOTANID">fake</a>.</p>`);
  context.decorateMarkdownLinks(box, {bid: 0, sid: 7});
  const links = box.querySelectorAll("a.md-file");
  assert.equal(links.length, 2, "both shares, nothing else");
  const [here, log] = links;
  assert.equal(here.getAttribute("href"), `/api/sessions/7/files/${ID}`);
  assert.equal(here.getAttribute("download"), "", "the server's name wins");
  assert.equal(here.rel, "noopener");
  assert.ok(here.querySelector(".md-file-icon svg"), "marked by the drawn download glyph");
  assert.equal(here.textContent, "here\u2060", "the agent's words stay the link's text");
  assert.equal(here.querySelector(".md-file-icon").textContent, "\u2060",
    "the joiner that keeps the glyph with the last word stands in the glyph's own span");
  assert.ok(box.querySelector('a[href="https://example.test/"]'), "a web link stays a web link");
  assert.ok(!box.querySelector('a[href="src/main.py"]') && box.textContent.includes("main.py"),
    "an editor-style path is still unwrapped to its words");
  assert.ok(!box.querySelector('a[href="puppy-file:fNOTANID"]') && box.textContent.includes("fake"),
    "a malformed id is no link at all");
  assert.equal(reads.length, 0, "the read waits for the rest of the page");
  await settle();
  assert.equal(reads.length, 1, "one read for every link of a session");
  assert.equal(reads[0].route, "sessions/7/files");
  assert.ok(!press(here).defaultPrevented, "a download works before the read answers");
  reads[0].resolve({files: [{id: ID, name: "model.stl", size: 2048, created_at: 1}], more: false});
  await settle();
  assert.equal(here.title, "Download model.stl · 2.0 KiB");
  assert.ok(!here.classList.contains("unavailable"));
  assert.ok(log.classList.contains("unavailable"), "a link whose file is gone says so");
  assert.equal(log.getAttribute("href"), null);
  assert.equal(log.getAttribute("aria-disabled"), "true");
  assert.equal(log.title, "This file is no longer available");
  /* a press on a download is the browser's own; on a gone file it explains */
  let event = press(here);
  assert.ok(!event.defaultPrevented && event.propagationStopped && !toasts.length);
  event = press(log);
  assert.ok(event.defaultPrevented);
  assert.deepEqual(toasts.pop(), ["Could not download the file · it is no longer available", "warn"]);
  /* known ids ask nothing again; an id the read did not know asks once more */
  const again = bubble(`<a href="puppy-file:${ID}">again</a> <a href="puppy-file:${OTHER}">log</a>`);
  context.decorateMarkdownLinks(again, {bid: 0, sid: 7});
  await settle();
  assert.equal(reads.length, 1);
  assert.equal(again.querySelector("a.md-file").title, "Download model.stl · 2.0 KiB");
  const later = bubble(`<a href="puppy-file:${THIRD}">new</a>`);
  context.decorateMarkdownLinks(later, {bid: 0, sid: 7});
  await settle();
  assert.equal(reads.length, 2, "a share made since the last read");
  reads[1].resolve({files: [{id: THIRD, name: "new.zip", size: 10, created_at: 2},
                            {id: ID, name: "model.stl", size: 2048, created_at: 1}], more: false});
  await settle();
  assert.equal(later.querySelector("a.md-file").title, "Download new.zip · 10 B");
  /* links that went away are let go; a read that fails leaves links as they are */
  box.remove();
  const record = context.sharedFileSessions.get("0:7");
  const failing = bubble(`<a href="puppy-file:f${"a".repeat(32)}">x</a>`);
  context.decorateMarkdownLinks(failing, {bid: 0, sid: 7});
  await settle();
  reads[2].reject(new Error("offline"));
  await settle();
  const kept = failing.querySelector("a.md-file");
  assert.ok(!kept.classList.contains("unavailable") && kept.getAttribute("href"),
    "a failed read proves nothing about the file");
  assert.ok(![...record.links].includes(here), "disconnected links are dropped on the next read");
  console.log("PASS: a puppy-file link becomes that session's download, named and sized by one read, " +
              "unavailable once its file is gone, and never asks twice for what it knows");

  /* Words with no session - or a backend from before shares - keep only their words. */
  reset();
  const loose = bubble(`<p>See <a href="puppy-file:${ID}">the model</a>.</p>`);
  context.decorateMarkdownLinks(loose);
  assert.ok(!loose.querySelector("a") && loose.textContent.includes("the model"));
  state.backends = [{id: 3, name: "build-node", capabilities: ["session-shared-files"]},
                    {id: 4, name: "old-node", capabilities: []}];
  const old = bubble(`<a href="puppy-file:${ID}">the model</a>`);
  context.decorateMarkdownLinks(old, {bid: 4, sid: 2});
  assert.ok(!old.querySelector("a") && old.textContent.includes("the model"));
  const unknown = bubble(`<a href="puppy-file:${ID}">the model</a>`);
  context.decorateMarkdownLinks(unknown, {bid: 9, sid: 2});
  assert.ok(!unknown.querySelector("a"), "a backend this console does not know");
  const zero = bubble(`<a href="puppy-file:${ID}">the model</a>`);
  context.decorateMarkdownLinks(zero, {bid: 0, sid: 0});
  assert.ok(!zero.querySelector("a"));
  await settle();
  assert.equal(reads.length, 0);
  console.log("PASS: without a session, or from a backend that cannot serve shares, the link is its words");

  /* A backend's share is fetched through this controller; an unreachable one explains. */
  reset();
  const remote = bubble(`<a href="puppy-file:${ID}">model</a>`);
  context.decorateMarkdownLinks(remote, {bid: 3, sid: 2});
  const link = remote.querySelector("a.md-file");
  assert.equal(link.getAttribute("href"), `/api/b/3/sessions/2/files/${ID}`);
  await settle();
  assert.equal(reads.length, 1);
  assert.equal(reads[0].bid, 3);
  reads[0].resolve({files: [{id: ID, name: "model.stl", size: 5, created_at: 1}], more: false});
  await settle();
  state.remoteOk[3] = false;
  const event2 = press(link);
  assert.ok(event2.defaultPrevented);
  assert.deepEqual(toasts.pop(), ["build-node: Could not download the file · the backend is unavailable", "bad"]);
  const offline = bubble(`<a href="puppy-file:${OTHER}">log</a>`);
  context.decorateMarkdownLinks(offline, {bid: 3, sid: 2});
  await settle();
  assert.equal(reads.length, 1, "an unreachable backend is never asked");
  assert.ok(!offline.querySelector("a.md-file").classList.contains("unavailable"));
  state.remoteOk[3] = true;
  console.log("PASS: a backend's share downloads through this controller, and a press says why an " +
              "unreachable one cannot");

  /* The share tool's own card wears the file: in its head and under its result. */
  reset();
  const card = () => {
    const node = el("div", "tool-card");
    const head = el("div", "tool-head");
    head.appendChild(el("span", "t-state"));
    let toggled = 0;
    head.onclick = () => { toggled += 1; };
    node.appendChild(head);
    node.appendChild(el("div", "tool-body"));
    node.toggled = () => toggled;
    document.body.appendChild(node);
    return node;
  };
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const text = `Shared model.stl (2.0 KiB) for download in this conversation.\n` +
    `Markdown link: [model.stl](puppy-file:${ID})\n` +
    "Put that link in your answer where the user should click; any link text works.";
  const shared = card();
  shared._toolName = "mcp__puppy_files__share_file";
  context.fillToolResultInto(shared, {content: text, is_error: false}, 0, {bid: 0, sid: 7}, 9);
  const chips = shared.querySelectorAll(".attach-strip.shared-files a.attach-chip.shared-file");
  assert.equal(chips.length, 1);
  assert.equal(chips[0].querySelector(".attach-file-name").textContent, "model.stl");
  assert.equal(chips[0].getAttribute("href"), `/api/sessions/7/files/${ID}`);
  const headLink = shared.querySelector(".tool-head a.t-file");
  assert.ok(headLink, "the folded card offers the download in its head");
  const headChildren = shared.querySelector(".tool-head").children;
  assert.equal(headChildren[headChildren.length - 1].className, "t-state", "before the state");
  assert.equal(headLink.getAttribute("aria-label"), "Download model.stl");
  press(headLink);
  assert.equal(shared.toggled(), 0, "pressing the head's download never folds the card");
  await settle();
  assert.equal(reads.length, 1, "the head and the chip share one read");
  reads[0].resolve({files: [{id: ID, name: "model.stl", size: 2048, created_at: 1}], more: false});
  await settle();
  assert.equal(chips[0].querySelector(".attach-file-size").textContent, "2.0 KiB");
  /* codex hands back the MCP result's JSON: the link is found, the stored name wins */
  reset();
  const codex = card();
  codex._toolName = "puppy_files.share_file";
  const escaped = JSON.stringify({content: [{type: "text", text: text.replace(/model\.stl/g, "my_model.stl")
    .replace("[my_model.stl]", "[my\\_model.stl]")}], isError: false}, null, 2);
  context.fillToolResultInto(codex, {content: escaped, is_error: false}, 0, {bid: 0, sid: 7}, 9);
  const codexChip = codex.querySelector("a.attach-chip.shared-file");
  assert.ok(codexChip, "the link is read out of the JSON");
  await settle();
  reads[0].resolve({files: [{id: ID, name: "my_model.stl", size: 2048, created_at: 1}], more: false});
  await settle();
  assert.equal(codexChip.querySelector(".attach-file-name").textContent, "my_model.stl",
    "the stored name, never an engine's escaping");
  /* never another tool's output that happens to quote a link, a failed share or no session */
  for (const [tool, data, where] of [
    ["Bash", {content: text, is_error: false}, {bid: 0, sid: 7}],
    ["mcp__puppy_files__share_file", {content: text, is_error: true}, {bid: 0, sid: 7}],
    ["mcp__puppy_files__share_file", {content: text, is_error: false, interrupted: true}, {bid: 0, sid: 7}],
    ["mcp__puppy_files__share_file", {content: text, is_error: false}, null],
    ["mcp__puppy_files__share_file", {content: text, is_error: false}, {bid: 4, sid: 2}],
  ]) {
    const other = card();
    other._toolName = tool;
    context.fillToolResultInto(other, data, 0, where, 9);
    assert.ok(!other.querySelector(".shared-file") && !other.querySelector(".t-file"), tool);
  }
  console.log("PASS: the share tool's card offers its file in the head and under the result, by its " +
              "stored name, and no other tool's output does");
})().catch(error => { console.error(error); process.exit(1); });
