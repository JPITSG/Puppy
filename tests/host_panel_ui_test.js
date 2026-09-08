"use strict";
/* The host activity box behind the footer's CPU reading, against the fake DOM:
   what opening and closing it polls, the drawn CPU chart and its gaps, the
   latency rows the controller measured, and the trimmed process tree - plus
   every node it must not go and probe. No browser, engine, network or quota. */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { FakeDocument } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");

function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}

const CPU_SERIES = (at, count, value) =>
  Array.from({ length: count }, (_, index) => [at + index * 3, value + index]);

function metrics(overrides = {}) {
  const now = 1000;
  return {
    ok: true, sampled_at: now + 30,
    cpu: { percent: 12, cores: 8, interval: 3, window: 900,
           history: CPU_SERIES(now, 11, 10) },
    memory: { total: 16 * 1024 * 1024 * 1024, available: 8 * 1024 * 1024 * 1024,
              used: 8 * 1024 * 1024 * 1024 },
    load: [0.5, 0.25, 0.1], uptime: 90061,
    processes: {
      total: 411, counted: 4, hidden: 0, sampled_at: now + 30,
      root: {
        pid: 10, label: "python3 -m puppy", cmd: "/usr/local/bin/python3 -m puppy",
        kind: "puppy", cpu: 1.4, rss: 62914560, threads: 9, state: "S",
        uptime: 7200, hidden: 0, children: [{
          pid: 20, label: "claude", cmd: "claude -p --output-format stream-json",
          kind: "claude", cpu: 34.2, rss: 331350016, threads: 12, state: "R",
          uptime: 61, more: 2, children: [{
            label: "agent bridges", kind: "bridge", count: 5, group: true,
            cpu: 0.5, rss: 91226112, threads: 5, cmd: "python3 -m puppy.browser_agent",
            pids: [21, 22, 23, 24, 25], children: [],
          }],
        }],
      },
    },
    ...overrides,
  };
}

function consoleFor(options = {}) {
  const document = new FakeDocument();
  const el = (tag, cls = "", text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  for (const id of ["foot-host", "host-cpu"]) {
    const node = el("div");
    node.id = id;
    document.body.appendChild(node);
  }
  const foot = el("div", "side-foot");
  document.body.appendChild(foot);
  foot.appendChild(document.getElementById("foot-host"));
  const state = {
    backends: options.backends || [],
    remoteOk: options.remoteOk || {},
    nodeCapabilities: options.nodeCapabilities || ["host-metrics-v1"],
  };
  const calls = [];
  const slides = [];
  let aimed = 0;
  const context = vm.createContext({
    document, el, state, console, Date, Math, Number, Array, JSON, Set, Map,
    Promise, isNaN, parseFloat,
    setTimeout: () => 1, clearTimeout: () => {},
    $: id => document.getElementById(id),
    /* The sidebar's shared slide lives outside this slice. Its contract with
       the box is all that matters here: the panel is asked to move with the
       motion on, and its content is only let go once a close has finished. */
    setDisclosureCollapsed: (body, collapsed, animate, done) => {
      slides.push({ collapsed, animate: !!animate });
      body.hidden = collapsed;
      if (done) done();
    },
    refreshDisclosureHeight: () => { aimed++; },
    backendName: bid => (bid ? `backend ${bid}` : "this instance"),
    nodeHasCapability: (bid, capability) => {
      const list = bid ? (state.backends.find(item => item.id === bid) || {}).capabilities
        : state.nodeCapabilities;
      return Array.isArray(list) && list.includes(capability);
    },
    sortNodeGroups: groups => groups,
    remoteAvailability: bid => (state.remoteOk[bid] === false ? "bad" :
      state.remoteOk[bid] === true ? "ok" : "pending"),
    remoteAvailabilityTitle: () => "available",
    remoteStoppingMessage: () => "",
    backendStateNote: availability => (availability === "bad" ? "Backend unavailable" :
      availability === "ok" ? "" : "Checking backend…"),
    api: async (bid, route) => {
      calls.push([bid, route]);
      const answer = (options.answers || {})[`${bid}:${route.split("?")[0]}`];
      if (answer === undefined) throw new Error("no stub for " + route);
      if (answer instanceof Error) throw answer;
      return typeof answer === "function" ? answer() : answer;
    },
  });
  vm.runInContext(
    between("/* ================= host activity ================= */",
            '\n$("host-cpu").onclick'), context);
  /* The box's own state is a script-scoped const, like every other module
     private in app.js: read it out of the context rather than exporting it. */
  const hostPanel = vm.runInContext("hostPanel", context);
  return { context, document, state, calls, hostPanel, slides,
    aimed: () => aimed,
    panel: () => document.getElementById("foot-host"),
    foot };
}

const rows = (node, cls) => node.querySelectorAll(`.${cls}`);
const text = node => (node ? node.textContent : "");
/* A row's rails read as a shape: "|" a level that still has rows below it,
   "." one that is finished, "T" this row's tee and "L" its closing corner. */
const railShape = row => row.querySelectorAll(".host-rail").map(rail =>
  rail.className.includes("line") ? "|" : rail.className.includes("tee") ? "T" :
    rail.className.includes("end") ? "L" : ".").join("");

/* ---- opening, closing, and what each costs ---- */
async function toggling() {
  const app = consoleFor({ answers: { "0:host/metrics": metrics() } });
  const { context } = app;
  assert.equal(app.hostPanel.open, false);
  context.toggleHostPanel();
  assert.equal(app.hostPanel.open, true);
  assert.equal(app.panel().hidden, false);
  // it slides in rather than appearing, and does so with content to show
  assert.deepEqual(app.slides, [{ collapsed: false, animate: true }]);
  assert.ok(app.panel().children.length > 0);
  assert.equal(app.foot.classList.contains("host-open"), true);
  assert.equal(app.document.getElementById("host-cpu").getAttribute("aria-expanded"), "true");
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(app.calls, [[0, "host/metrics?window=900"]]);
  // the readings that landed mid-slide re-aim it instead of snapping at the end
  assert.ok(app.aimed() > 0);
  // one CPU section and one process section, both naming this instance
  assert.equal(rows(app.panel(), "host-sec").length, 2);
  assert.equal(text(rows(app.panel(), "host-sec-title")[0]), "CPU");
  assert.equal(text(rows(app.panel(), "host-sec-title")[1]), "Processes");
  context.toggleHostPanel();
  assert.equal(app.hostPanel.open, false);
  assert.equal(app.panel().hidden, true);
  assert.deepEqual(app.slides[1], { collapsed: true, animate: true });
  // the content goes only once that slide has finished
  assert.equal(app.panel().children.length, 0);
  assert.equal(app.foot.classList.contains("host-open"), false);
  assert.equal(app.document.getElementById("host-cpu").getAttribute("aria-expanded"), "false");
  // a response that lands after the box closed renders nothing
  const before = app.calls.length;
  await context.pollHostPanel();
  assert.equal(app.calls.length, before);
  assert.equal(app.panel().children.length, 0);
}

/* ---- the drawn chart ---- */
function charting() {
  const { context } = consoleFor();
  const series = [[0, 0], [3, 50], [6, 100]];
  series.gap = 3;
  const segments = context.hostChartSegments(series, 0, 6, 100);
  assert.equal(segments.length, 1);
  // arrays cross the VM boundary, so compare their shape rather than identity
  assert.equal(JSON.stringify(segments[0]), JSON.stringify([[0, 100], [50, 50], [100, 0]]));
  // a gap wider than four sample intervals breaks the line instead of
  // drawing a straight run across time the node was away
  const broken = [[0, 10], [3, 20], [60, 30], [63, 40]];
  broken.gap = 3;
  assert.equal(context.hostChartSegments(broken, 0, 63, 100).length, 2);
  // latency uses the same helper with its own ceiling
  const pings = [[0, 5], [1, 10]];
  assert.equal(JSON.stringify(context.hostChartSegments(pings, 0, 1, 10)[0]),
    JSON.stringify([[0, 50], [100, 0]]));

  const svg = context.hostChart(series, { from: 0, to: 6, height: 48 });
  assert.equal(svg.getAttribute("viewBox"), "0 0 100 100");
  assert.equal(svg.getAttribute("preserveAspectRatio"), "none");
  assert.equal(svg.style.height, "48px");
  const line = svg.querySelector(".host-chart-line");
  assert.equal(line.getAttribute("vector-effect"), "non-scaling-stroke");
  assert.equal(line.getAttribute("d"), "M0.00 100.00L50.00 50.00L100.00 0.00");
  const area = svg.querySelector(".host-chart-area");
  assert.ok(area.getAttribute("d").startsWith("M0.00 100"));
  assert.ok(area.getAttribute("d").endsWith("L100.00 100Z"));
  assert.ok(svg.querySelector(".host-chart-grid"));
  assert.equal(svg.querySelectorAll(".host-chart-grid").length, 1);
  // the sparkline keeps its ink but drops the mid-line and the fill well
  const spark = context.hostChart(series, { from: 0, to: 6, height: 13, grid: false, cls: "spark" });
  assert.equal(spark.querySelectorAll(".host-chart-grid").length, 0);
  assert.ok(spark.className.includes("spark"));
}

/* ---- this node's chart is its served history plus the live stream ---- */
async function liveSamples() {
  const app = consoleFor({ answers: { "0:host/metrics": metrics() } });
  const { context } = app;
  context.toggleHostPanel();
  await new Promise(resolve => setTimeout(resolve, 0));
  const served = context.hostSeries(0);
  assert.equal(served.length, 11);
  const now = Date.now() / 1000;
  context.hostCpuSample(97);
  const merged = context.hostSeries(0);
  assert.equal(merged.length, 12);
  assert.equal(merged[11][1], 97);
  assert.ok(merged[11][0] >= now);
  // the note and facts read from the merged series and the node's own facts
  const note = text(rows(app.panel(), "host-node-note")[0]);
  assert.equal(note, "97% · peak 97%");
  /* the three facts hold their own thirds of the chart's width: cores at the
     left edge, load over the middle, memory at the right */
  const facts = rows(app.panel(), "host-facts")[0];
  assert.deepEqual(facts.children.map(cell => [cell.className, text(cell)]), [
    ["host-fact", "8 cores"],
    ["host-fact mid", "load 0.50"],
    ["host-fact end", "8.0G/16.0G"],
  ]);
  // the sidebar is narrow, so the rest of the machine is one hover away
  assert.ok(facts.title.includes("load 0.50 0.25 0.10"), facts.title);
  assert.ok(facts.title.includes("memory 8.0G of 16.0G used"), facts.title);
  assert.ok(facts.title.includes("up 1d 1h"), facts.title);
  assert.ok(facts.title.startsWith("last "), facts.title);
  // a stream sample keeps arriving while the box is open and repaints only
  // that node's chart, never the whole box
  const tree = rows(app.panel(), "host-tree")[0];
  context.hostCpuSample(3);
  assert.equal(rows(app.panel(), "host-tree")[0], tree);
  assert.equal(text(rows(app.panel(), "host-node-note")[0]).startsWith("3%"), true);
  // samples arriving while the box is closed are still kept for the next open
  context.toggleHostPanel();
  context.hostCpuSample(44);
  assert.equal(app.hostPanel.live[app.hostPanel.live.length - 1][1], 44);
}

/* ---- the trimmed process tree ---- */
async function processTree() {
  const app = consoleFor({ answers: { "0:host/metrics": metrics() } });
  const { context } = app;
  context.toggleHostPanel();
  await new Promise(resolve => setTimeout(resolve, 0));
  const tree = rows(app.panel(), "host-proc");
  assert.equal(tree.length, 4);                 // root, engine, folded bridges, +2 more
  assert.equal(text(rows(app.panel(), "host-proc-name")[0]), "python3 -m puppy");
  /* One rail cell per level: the root has none, a last child ends in a corner
     and a row with siblings still to come in a tee, and a level that is done
     leaves a blank column rather than a bar running past its last row. */
  assert.equal(railShape(tree[0]), "");
  assert.equal(railShape(tree[1]), "L");        // the only child of the root
  assert.equal(railShape(tree[2]), ".T");       // a trimmed "+n more" follows it
  assert.equal(railShape(tree[3]), ".L");
  assert.ok(tree[0].querySelector(".k-puppy"));
  assert.ok(tree[1].querySelector(".k-claude"));
  assert.ok(tree[2].querySelector(".k-bridge"));
  assert.equal(text(tree[1].querySelector(".host-proc-cpu")), "34%");
  assert.equal(text(tree[0].querySelector(".host-proc-cpu")), "1.4%");
  assert.equal(text(tree[1].querySelector(".host-proc-rss")), "316M");
  assert.equal(text(tree[2].querySelector(".host-proc-count")), "×5");
  assert.equal(text(tree[3]), "+2 more");
  assert.ok(tree[0].title.startsWith("pid 10 · 9 threads · up 2h 0m"), tree[0].title);
  assert.ok(tree[0].title.endsWith("/usr/local/bin/python3 -m puppy"), tree[0].title);
  assert.ok(tree[2].title.startsWith("5 processes"), tree[2].title);
  const note = text(rows(app.panel(), "host-node-note")[1]);
  assert.equal(note, "4 of 411");
}

/* ---- backends: latency the controller measured, and what is never probed ---- */
async function backends() {
  const app = consoleFor({
    backends: [{ id: 2, name: "nas", capabilities: ["host-metrics-v1"] },
               { id: 3, name: "laptop", capabilities: ["host-metrics-v1"] },
               { id: 4, name: "old", capabilities: [] }],
    remoteOk: { 2: true, 3: false, 4: true },
    answers: {
      "0:host/metrics": metrics(),
      "2:host/metrics": metrics(),
      "0:backends/latency": { ok: true, measured_at: 500, backends: [
        { id: 2, name: "nas", ok: true, ms: 12.4, url: "https://nas:10888" },
        { id: 3, name: "laptop", ok: false, offline: true, error: "connection refused" },
        { id: 4, name: "old", ok: false, error: "timed out" },
      ] },
    },
  });
  const { context } = app;
  context.toggleHostPanel();
  await new Promise(resolve => setTimeout(resolve, 0));
  /* An unreachable backend is the controller's verdict; a metrics poll never
     goes looking for it, and a node without the capability is never asked. */
  assert.deepEqual(app.calls.sort(), [
    [0, "backends/latency"], [0, "host/metrics?window=900"], [2, "host/metrics?window=900"],
  ].sort());
  /* The unreachable backend is not shown anywhere in the box: no latency row,
     no chart and no process tree. A node that answered but failed the ping
     still has one, because that is a fact about a link that exists. */
  const pings = rows(app.panel(), "host-ping");
  assert.equal(pings.length, 2);
  assert.equal(text(pings[0].querySelector(".host-ping-name")), "nas");
  assert.equal(text(pings[0].querySelector(".host-ping-ms")), "12.4 ms");
  assert.ok(pings[0].querySelector(".gdot.ok"));
  assert.equal(text(pings[1].querySelector(".host-ping-name")), "old");
  assert.equal(text(pings[1].querySelector(".host-ping-ms")), "failed");
  assert.equal(rows(app.panel(), "host-node-name").map(text)
    .includes("laptop"), false);
  // a node that is there but does not report activity still says so
  const empties = rows(app.panel(), "host-empty").map(text);
  assert.ok(!empties.includes("Backend unavailable"), empties.join("|"));
  assert.ok(empties.includes("This backend does not report host activity"), empties.join("|"));
  // a second measurement builds each backend's sparkline
  app.hostPanel.pings.set(2, [[500, 12.4], [504, 15.2]]);
  context.renderHostPanel();
  assert.ok(rows(app.panel(), "host-ping")[0].querySelector(".host-chart.spark"));
  // three reachable nodes, each with one chart block and one process block
  assert.equal(rows(app.panel(), "host-node").length, 6);
  // a failed read keeps the tree that node last served rather than blanking it
  app.hostPanel.nodes.set(2, { data: metrics(), error: "network error" });
  context.renderHostPanel();
  assert.equal(rows(app.panel(), "host-tree").length, 2);
}

/* ---- every backend unreachable: the whole latency section goes ---- */
async function allUnreachable() {
  const app = consoleFor({
    backends: [{ id: 2, name: "nas", capabilities: ["host-metrics-v1"] }],
    remoteOk: { 2: false },
    answers: {
      "0:host/metrics": metrics(),
      "0:backends/latency": { ok: true, measured_at: 500, backends: [
        { id: 2, name: "nas", ok: false, offline: true, error: "connection refused" },
      ] },
    },
  });
  const { context } = app;
  context.toggleHostPanel();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(rows(app.panel(), "host-ping").length, 0);
  // CPU and Processes only, both holding this instance alone
  assert.deepEqual(rows(app.panel(), "host-sec-title").map(text), ["CPU", "Processes"]);
  assert.deepEqual(rows(app.panel(), "host-node-name").map(text),
    ["this instance", "this instance"]);
  // and it comes back the moment the health worker says the node is up
  app.state.remoteOk[2] = true;
  context.renderHostPanel();
  assert.deepEqual(rows(app.panel(), "host-sec-title").map(text),
    ["CPU", "Latency", "Processes"]);
  assert.deepEqual(rows(app.panel(), "host-node-name").map(text),
    ["this instance", "nas", "this instance", "nas"]);
}

/* ---- with no backends configured there is nothing to measure ---- */
async function soloInstance() {
  const app = consoleFor({ answers: { "0:host/metrics": metrics() } });
  app.context.toggleHostPanel();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(app.calls, [[0, "host/metrics?window=900"]]);
  assert.equal(rows(app.panel(), "host-ping").length, 0);
  assert.equal(rows(app.panel(), "host-sec").length, 2);
}

/* ---- a node still collecting, and one that failed ---- */
async function emptyStates() {
  const app = consoleFor({ answers: { "0:host/metrics": new Error("network error") } });
  const { context } = app;
  context.toggleHostPanel();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(text(rows(app.panel(), "host-chart-empty")[0]), "network error");
  assert.equal(text(rows(app.panel(), "host-empty")[0]), "network error");
  const thin = metrics();
  thin.cpu.history = [[1000, 5]];
  app.hostPanel.nodes.set(0, { data: thin, error: "" });
  app.hostPanel.live = [];
  context.renderHostPanel();
  assert.equal(text(rows(app.panel(), "host-chart-empty")[0]), "Collecting samples…");
}

async function main() {
  await toggling();
  charting();
  await liveSamples();
  await processTree();
  await backends();
  await allUnreachable();
  await soloInstance();
  await emptyStates();
  console.log("host activity panel tests passed");
}

main().catch(error => { console.error(error); process.exit(1); });
