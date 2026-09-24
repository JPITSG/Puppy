/* Optional libraries: shared downloads, retries, CSS order and view lifetimes.
   Runs the real loader and view methods without a browser, network or shell. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { FakeDocument } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert(start >= 0 && end > start, from);
  return source.slice(start, end);
};
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness() {
  const document = new FakeDocument();
  document.head = document.createElement("head");
  document.body.appendChild(document.head);
  document.head.innerHTML = '<link id="console-styles"><link id="app-styles">';
  document.currentScript = { src: "https://example.test/console/static/app.js?v=17" };
  const timers = new Map(), notices = [], sockets = [], terminals = [];
  let timerId = 0;
  class Terminal {
    constructor() { terminals.push(this); }
    loadAddon() {} open() {} write() {} blur() {} focus() {}
    onResize() { return { dispose() {} }; }
    dispose() { this.disposed = true; }
  }
  const context = vm.createContext({ document, console, URL, timers, terminals,
    setTimeout: (fn, ms) => { timers.set(++timerId, {fn, ms}); return timerId; },
    clearTimeout: id => timers.delete(id),
    $: id => document.getElementById(id),
    el: (tag, cls, text) => {
      const node = document.createElement(tag);
      node.className = cls || "";
      if (text !== undefined) node.textContent = text;
      return node;
    },
    esc: text => String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;"),
    toast: (...args) => notices.push(args), TOAST_LONG: 10000,
    backendConnectionAllowed: () => true,
    remoteStoppingMessage: () => "",
    terminalDeadWording: () => ({ ended: "Terminal ended", start: "New shell", close: "Close shell" }),
    saveTabs() {}, syncSessionBrowserChips() {},
    wsUrl: (_bid, name) => name,
    WebSocket: class {
      constructor(url) { this.url = url; this.readyState = 0; sockets.push(this); }
    },
    ResizeObserver: class { observe() {} disconnect() {} },
  });
  vm.runInContext(between("/* ================= optional libraries", "/* ================= helpers") +
    between("function md(text)", "/* Engine output sometimes") +
    between("class SessionView {", "/* ================= BrowserView") +
    "\nglobalThis.Chat = SessionView; globalThis.Term = TermView;", context);
  const nodes = () => document.head.children.filter(node => node.src || node.href);
  const finish = (name, ok = true) => {
    const node = nodes().find(node => (node.src || node.href).includes(name));
    assert(node, name);
    if (ok) {
      if (name === "xterm.min.js") context.Terminal = Terminal;
      if (name === "addon-fit.min.js") context.FitAddon = { FitAddon: class { fit() {} } };
      if (name === "marked.min.js") context.marked = { parse: text => `<b>${text}</b>` };
      if (name === "purify.min.js") context.DOMPurify = { sanitize: text => "sanitized: " + text };
      node.onload();
    } else node.onerror();
  };
  const chat = () => Object.assign(Object.create(context.Chat.prototype), {
    tab: {bid:0, sid:7}, closed:false, ws:null, reconnectTimer:null, connectionSequence:0,
    setReconnecting() {},
  });
  const term = () => {
    const view = Object.create(context.Term.prototype);
    view.tab = {bid:0, id:"t:1"}; view.closed = false; view.connectionSequence = 0;
    view.root = document.createElement("div");
    view.root.innerHTML = '<div class="term-host"><div class="term-mount"></div></div><button class="owner"></button>';
    document.body.appendChild(view.root);
    view.host = view.root.querySelector(".term-host"); view.mount = view.root.querySelector(".term-mount");
    view.ownerBtn = view.root.querySelector(".owner");
    view.renderBinding = view.stopMetadataScrolling = () => {};
    view.connect = () => { view.connected = true; };
    return view;
  };
  return { context, document, nodes, finish, timers, notices, sockets, terminals, chat, term };
}

(async () => {
  const h = harness();
  assert.equal(h.nodes().length, 0, "evaluating the console requests no optional assets");
  const first = h.context.loadTerminalLibraries(), second = h.context.loadTerminalLibraries();
  assert.equal(h.nodes().length, 3, "concurrent terminal opens share all three assets");
  for (const node of h.nodes()) {
    const url = new URL(node.src || node.href);
    assert.equal(url.origin, "https://example.test");
    assert(url.pathname.startsWith("/console/static/vendor/"));
    assert.equal(url.search, "?v=17", "lazy loads retain the current console's version");
  }
  assert.equal(h.document.head.children[0].href.includes("xterm.css"), true,
    "vendor CSS precedes both shared and console overrides");
  let settled = false;
  first.then(() => { settled = true; });
  h.finish("xterm.min.js"); h.finish("addon-fit.min.js");
  await tick(); assert.equal(settled, false, "opening waits for the stylesheet too");
  h.finish("xterm.css");
  await Promise.all([first, second]);
  await h.context.loadTerminalLibraries();
  assert.equal(h.nodes().length, 3, "later terminals reuse loaded assets");

  const m = harness();
  const loading = m.context.loadMarkdownLibraries();
  const refused = assert.rejects(loading, /Could not load/);
  m.finish("marked.min.js"); m.finish("purify.min.js", false);
  await refused;
  assert.equal(m.context.md("<img onerror=bad()>"), "&lt;img onerror=bad()&gt;",
    "a parser without a sanitizer never yields HTML");
  const retry = m.context.loadMarkdownLibraries();
  assert.equal(m.nodes().length, 2, "retry keeps the successful parser and replaces the failed sanitizer");
  m.finish("purify.min.js"); await retry;
  assert.equal(m.context.md("Hello"), "sanitized: <b>Hello</b>");
  const broken = m.context.loadConsoleAsset("vendor/broken.js", {ready: () => false});
  const bad = assert.rejects(broken, /Could not initialize/);
  m.nodes().find(node => node.src.includes("broken.js")).onload(); await bad;
  const stalled = m.context.loadConsoleAsset("vendor/stalled.js");
  const timeout = assert.rejects(stalled, /timed out/);
  [...m.timers.values()].find(timer => timer.ms === 15000).fn(); await timeout;
  assert(!m.nodes().some(node => (node.src || "").includes("stalled.js")));

  const c = harness(), closing = c.chat();
  const connection = closing.connect();
  await closing.connect();
  assert.equal(c.nodes().length, 2); assert.equal(c.sockets.length, 0);
  closing.closed = true;
  c.finish("marked.min.js"); c.finish("purify.min.js");
  await connection;
  assert.equal(c.sockets.length, 0, "late library completion cannot connect a closed conversation");
  const reopened = c.chat(); await reopened.connect();
  assert.equal(c.sockets.length, 1); assert.equal(c.nodes().length, 2);
  await reopened.connect(); assert.equal(c.sockets.length, 1);
  const f = harness(), plain = f.chat();
  const fallback = plain.connect();
  f.finish("marked.min.js", false); f.finish("purify.min.js", false);
  await fallback;
  assert.equal(f.sockets.length, 1, "failed formatting does not block chat access");
  assert.equal(f.notices.length, 1); assert.match(f.notices[0][0], /showing plain text/);

  const t = harness(), closingTerm = t.term();
  const start = closingTerm.start();
  assert.equal(t.terminals.length, 0);
  assert.equal(closingTerm.root.querySelector(".term-dead-new").disabled, true);
  closingTerm.destroy();
  t.finish("xterm.min.js"); t.finish("addon-fit.min.js"); t.finish("xterm.css");
  await start;
  assert.equal(t.terminals.length, 0, "late library completion cannot open a closed terminal");
  assert.equal(closingTerm.connected, undefined);
  const active = t.term(); await active.start();
  assert.equal(t.terminals.length, 1); assert.equal(active.isDead(), false);
  active.destroy();
  assert.equal(t.timers.size, 0, "closing cancels the pending fit/connect timer");
  const r = harness(), retryTerm = r.term();
  const failedStart = retryTerm.start();
  r.finish("xterm.min.js", false); r.finish("addon-fit.min.js"); r.finish("xterm.css");
  await failedStart;
  const button = retryTerm.root.querySelector(".term-dead-new");
  assert.equal(button.textContent, "Retry"); assert.equal(button.disabled, false);
  button.onclick();
  assert.equal(r.nodes().length, 3); r.finish("xterm.min.js"); await tick();
  assert.equal(r.terminals.length, 1); assert.equal(retryTerm.isDead(), false);
  retryTerm.destroy();
  console.log("PASS: lazy assets, concurrent loads, versioned paths, CSS order, retries, safe Markdown fallback and closed-view guards");
})().catch(error => { console.error(error); process.exitCode = 1; });
