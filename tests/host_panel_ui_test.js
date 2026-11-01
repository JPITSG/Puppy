"use strict";
/* The host activity box behind the footer's CPU reading, against the fake DOM:
   what opening and closing it polls, the drawn CPU chart and its gaps, each
   node's disks, the latency rows the controller measured, and the trimmed
   process tree - plus
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

const GIB = 1024 * 1024 * 1024;
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
    disks: {
      sampled_at: now + 30, stalled: ["/mnt/archive"],
      mounts: [
        { mount: "/", fs: "ext4", puppy: true, total: 100 * GIB, used: 40 * GIB,
          available: 55 * GIB, projects: 2, paths: ["/home/mira/harbor", "/home/mira/notes"] },
        { mount: "/srv/projects", fs: "xfs", puppy: false, total: 2048 * GIB,
          used: 1900 * GIB, available: 148 * GIB, projects: 9,
          paths: ["/srv/projects/a", "/srv/projects/b"] },
        { mount: "/mnt/scratch", fs: "btrfs", puppy: false, total: 10 * GIB,
          used: 9.7 * GIB, available: 0.3 * GIB, projects: 1, paths: ["/mnt/scratch/x"] },
        { mount: "/mnt/locked", fs: "nfs4", puppy: false, projects: 1, paths: [],
          error: "Permission denied" },
      ],
    },
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
  let aimed = 0, noticesClosed = 0;
  const context = vm.createContext({
  navigation: { layer: () => () => {} }, navigationRemember: () => {}, navigationChanged: () => {},
    /* The footer holds one box under the engine stats: opening this one
       closes the notification box, whose own test covers the other way. */
    closeNoticesPanel: () => { noticesClosed++; },
    document, el, state, console, Date, Math, Number, Array, JSON, Set, Map,
    Promise, isNaN, parseFloat,
    setTimeout: () => 1, clearTimeout: () => {},
    choiceSvg: () => el("svg"),
    saveStringSet: () => assert.fail("Host section choices must stay page-local"),
    $: id => document.getElementById(id),
    /* The sidebar's shared slide lives outside this slice. Its contract with
       the box is all that matters here: the panel is asked to move with the
       motion on, and its content is only let go once a close has finished. */
    setDisclosureCollapsed: (body, collapsed, animate, done) => {
      // the box's own slide; its sections' are the sections test's
      if (animate && body.id === "foot-host") slides.push({ collapsed, animate: true });
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
  vm.runInContext("let disclosureSeq = 0;\n" +
    between("function disclosureButton(", "/* A mouse has a natural double-click;") +
    between("function wireDisclosureSurface(", "function choiceOptionNode("), context);
  /* The box borrows the footer's shared dot column, so bring the real helper
     in beside the slice rather than stubbing the column it draws. */
  vm.runInContext(between("/* The footer's one dot column.",
                          "function renderFootEngines()"), context);
  vm.runInContext(
    between("/* ================= host activity ================= */",
            '\n$("host-cpu").onclick'), context);
  /* The box's own state is a script-scoped const, like every other module
     private in app.js: read it out of the context rather than exporting it. */
  const hostPanel = vm.runInContext("hostPanel", context);
  return { context, document, state, calls, hostPanel, slides,
    aimed: () => aimed, noticesClosed: () => noticesClosed,
    panel: () => document.getElementById("foot-host"),
    foot };
}

/* Every opening shows the four sections shut, and opens the ones that are
   not green once it has read them; a toggle is the person's own until the
   box closes, survives polls and focus, and a backend that disappears
   cannot detach a focused control. */
async function sections() {
  const calm = () => { const data = metrics(); data.disks.mounts = data.disks.mounts.slice(0, 1);
    data.disks.stalled = []; return data; };
  const answers = { "0:host/metrics": metrics(), "2:host/metrics": calm(),
    "0:backends/latency": { backends: [{ id: 2, name: "Workshop", ok: true, ms: 2 }] } };
  const app = consoleFor({
    backends: [{ id: 2, name: "Workshop", capabilities: ["host-metrics-v1"] }],
    remoteOk: { 2: true }, answers,
  });
  const section = key => app.document.getElementById(`host-section-${key}`);
  const button = key => section(key).querySelector(".disclosure-toggle");
  const body = key => section(key).querySelector(".host-sec-body");
  const shut = () => ["cpu", "disk", "latency", "processes"].filter(key => body(key).hidden);
  app.context.openHostPanel();
  // nothing read yet in this opening: everything shut
  assert.deepEqual(shut(), ["cpu", "disk", "latency", "processes"]);
  for (const key of ["cpu", "disk", "latency", "processes"]) {
    assert.equal(button(key).getAttribute("aria-expanded"), "false");
    assert.equal(button(key).getAttribute("aria-controls"), body(key).id);
    assert.match(button(key).getAttribute("aria-label"), /^Expand /);
  }
  await new Promise(resolve => setTimeout(resolve, 0));
  // this instance's disks are not green (a full one, one that did not answer)
  assert.deepEqual(shut(), ["cpu", "latency", "processes"]);
  assert.equal(button("disk").getAttribute("aria-expanded"), "true");
  for (const key of ["cpu", "disk", "latency", "processes"]) {
    const was = body(key).hidden;
    button(key).click();
    assert.equal(body(key).hidden, !was);
    const control = button(key);
    control.focus();
    await app.context.pollHostPanel();
    assert.equal(button(key), control);
    assert.equal(app.document.activeElement, control);
    assert.equal(body(key).hidden, !was, "a toggle holds through polls, the disks' status too");
    button(key).click();
    assert.equal(body(key).hidden, was);
  }
  button("processes").focus();
  const processControl = button("processes");
  app.state.remoteOk[2] = false;
  app.context.renderHostPanel();
  assert.equal(section("latency"), null);
  assert.equal(button("processes"), processControl);
  app.state.remoteOk[2] = true;
  app.context.renderHostPanel();
  // a section toggled in this opening keeps the person's choice, whatever
  // its status does: every one was touched above
  answers["0:backends/latency"] = { backends: [{ id: 2, name: "Workshop", ok: false,
    error: "connection refused" }] };
  await app.context.pollHostPanel();
  assert.deepEqual(shut(), ["cpu", "latency", "processes"]);
  // a new opening forgets the toggles; a round trip that fails while the
  // box is open then opens an untouched Latency by itself
  app.context.closeHostPanel();
  answers["0:backends/latency"] = { backends: [{ id: 2, name: "Workshop", ok: true, ms: 2 }] };
  app.context.openHostPanel();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(shut(), ["cpu", "latency", "processes"]);
  answers["0:backends/latency"] = { backends: [{ id: 2, name: "Workshop", ok: false,
    error: "connection refused" }] };
  await app.context.pollHostPanel();
  assert.deepEqual(shut(), ["cpu", "processes"]);
  // and when it comes back green nothing folds away under the reader
  answers["0:backends/latency"] = { backends: [{ id: 2, name: "Workshop", ok: true, ms: 2 }] };
  await app.context.pollHostPanel();
  assert.deepEqual(shut(), ["cpu", "processes"]);
  // a section the person shut stays shut whatever its status says
  button("disk").click();
  await app.context.pollHostPanel();
  assert.equal(body("disk").hidden, true);
  // a new opening starts over: shut, then what is not green opens
  app.context.closeHostPanel();
  answers["0:host/metrics"] = calm();
  answers["2:host/metrics"] = (() => { const data = calm(); data.cpu.history.push([1040, 96]); return data; })();
  app.context.openHostPanel();
  assert.deepEqual(shut(), ["cpu", "disk", "latency", "processes"]);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(shut(), ["disk", "latency", "processes"], "a backend working at 96% opens CPU");
  // a node that cannot be read opens CPU too; a node from before the disks does not open Disk
  app.context.closeHostPanel();
  answers["2:host/metrics"] = new Error("request timed out");
  answers["0:host/metrics"] = (() => { const data = calm(); delete data.disks; return data; })();
  app.hostPanel.nodes.delete(2);
  app.context.openHostPanel();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(shut(), ["disk", "latency", "processes"]);
  // all green: everything stays shut
  app.context.closeHostPanel();
  answers["2:host/metrics"] = calm();
  answers["0:host/metrics"] = calm();
  app.context.openHostPanel();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(shut(), ["cpu", "disk", "latency", "processes"]);
  // Clicking the heading uses the same single-click contract as a backend.
  section("latency").querySelector(".host-sec-title").click();
  assert.equal(body("latency").hidden, false);
  app.context.closeHostPanel();
}

const rows = (node, cls) => node.querySelectorAll(`.${cls}`);
const text = node => (node ? node.textContent : "");
/* Every leading dot in the box stands in the footer's own column, the one
   the backend and engine rows above the box are built with, so a row reads
   as [cell][dot centred in it][name] whatever the dot's size. */
const inDotColumn = (row, dot) => {
  const lead = row.children[0];
  return !!lead && lead.className === "foot-ico" &&
    lead.children.length === 1 && lead.children[0].classList.contains(dot);
};
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
  assert.equal(app.noticesClosed(), 1, "the notification box gives way to this one");
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(app.calls, [[0, "host/metrics?window=900"]]);
  // the readings that landed mid-slide re-aim it instead of snapping at the end
  assert.ok(app.aimed() > 0);
  // CPU, Disk and Processes, each naming this instance (no backend: no latency)
  assert.deepEqual(rows(app.panel(), "host-sec-title").map(text), ["CPU", "Disk", "Processes"]);
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
  // the fill is one closed shape under the line: floor, up to the first
  // sample, along the line, back to the floor - never a second subpath, which
  // would close on the first sample and fill a wedge across the chart
  const area = svg.querySelector(".host-chart-area");
  assert.equal(area.getAttribute("d"),
    "M0.00 100L0.00 100.00L50.00 50.00L100.00 0.00L100.00 100Z");
  assert.ok(!area.getAttribute("d").slice(1).includes("M"));
  assert.ok(svg.querySelector(".host-chart-grid"));
  assert.equal(svg.querySelectorAll(".host-chart-grid").length, 1);
  assert.equal(svg.querySelectorAll(".host-chart-floor").length, 0);
  // the sparkline keeps its ink but drops the mid-line and the fill well,
  // and draws the floor its line lies on
  const spark = context.hostChart(series,
    { from: 0, to: 6, height: 13, grid: false, floor: true, cls: "spark" });
  assert.equal(spark.querySelectorAll(".host-chart-grid").length, 0);
  assert.equal(spark.querySelector(".host-chart-floor").getAttribute("d"), "M0 100H100");
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
  /* The root has no rails, so its dot takes the same column as the node head
     above it and as the backends above the box; every deeper row leads with
     its rails instead and is indented past that column. */
  assert.ok(inDotColumn(rows(app.panel(), "host-node-head")[2], "gdot"));
  assert.ok(inDotColumn(tree[0], "host-proc-dot"));
  for (const row of tree.slice(1))
    assert.equal(row.children[0].className, "host-proc-rails");
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

/* ---- disk space: every filesystem the node's work lives on ---- */
async function diskSpace() {
  const app = consoleFor({
    backends: [{ id: 2, name: "Workshop", capabilities: ["host-metrics-v1"] },
               { id: 3, name: "Old box", capabilities: ["host-metrics-v1"] }],
    remoteOk: { 2: true, 3: true },
    answers: {
      "0:host/metrics": metrics(),
      "2:host/metrics": new Error("request timed out"),
      // a backend from before the section answers without it
      "3:host/metrics": (() => { const old = metrics(); delete old.disks; return old; })(),
      "0:backends/latency": { backends: [{ id: 2, name: "Workshop", ok: true, ms: 2 },
                                         { id: 3, name: "Old box", ok: true, ms: 3 }] } },
  });
  app.context.openHostPanel();
  await new Promise(resolve => setTimeout(resolve, 0));
  // between CPU and Latency
  assert.deepEqual(rows(app.panel(), "host-sec-title").map(text),
    ["CPU", "Disk", "Latency", "Processes"]);
  const section = app.document.getElementById("host-section-disk");
  const groups = section.querySelectorAll(".host-node");
  assert.deepEqual(groups.map(group => text(group.querySelector(".host-node-name"))),
    ["this instance", "Workshop", "Old box"], "one group per node, as the CPU section");
  const here = groups[0].querySelectorAll(".host-disk");
  assert.deepEqual(here.map(row => text(row.querySelector(".host-disk-name"))),
    ["/", "/srv/projects", "/mnt/scratch", "/mnt/locked", "/mnt/archive"],
    "the node's order, then the mounts that did not answer");
  for (const row of here) assert.ok(inDotColumn(row, "gdot"), "the footer's dot column");
  // Puppy's own mount says so
  assert.deepEqual(here.map(row => text(row.querySelector(".host-disk-tag"))),
    ["Puppy", "", "", "", ""]);
  // the figure in words, the meter and the dot in one tone
  assert.deepEqual(here.map(row => text(row.querySelector(".host-disk-free"))),
    ["55.0G free", "148G free", "307M free", "unreadable", "no answer"]);
  const meter = row => row.querySelector(".host-disk-meter");
  const dot = row => row.querySelector(".gdot").className;
  assert.deepEqual(here.map(dot),
    ["gdot ok", "gdot warn", "gdot bad", "gdot", "gdot"]);
  assert.equal(meter(here[0]).className, "host-disk-meter");
  assert.equal(meter(here[1]).className, "host-disk-meter warn");
  assert.equal(meter(here[2]).className, "host-disk-meter bad");
  assert.equal(meter(here[3]), null, "no meter for a mount not read");
  assert.equal(meter(here[4]), null);
  assert.equal(here[3].querySelector(".host-disk-free").className, "host-disk-free quiet");
  // the share in use is used over used and free, as df counts it
  const used = 40 / 95 * 100;
  assert.equal(meter(here[0]).querySelector(".host-disk-fill").style.width, `${used}%`);
  assert.equal(meter(here[0]).getAttribute("aria-label"), "42% used");
  assert.equal(here[0].title, "/ · ext4 · 40.0G of 100G used (42% used) · 55.0G free · " +
    "holds Puppy · 2 projects: /home/mira/harbor, /home/mira/notes");
  assert.ok(here[1].title.endsWith("9 projects: /srv/projects/a, /srv/projects/b, …"), here[1].title);
  assert.equal(here[1].querySelector(".host-disk-free").textContent, "148G free");
  // four figures and a unit at most, so every figure fits one width
  assert.deepEqual([0, 512, 99.94 * 1024, 1023.6 * GIB, 1.5 * 1024 * GIB, 5 * 1024 ** 5]
    .map(app.context.hostDiskBytes), ["0B", "512B", "99.9K", "1024G", "1.5T", "5.0P"]);
  assert.equal(here[3].title, "/mnt/locked · nfs4 · Permission denied · 1 project");
  assert.equal(here[4].title, "/mnt/archive · did not answer in time · asked again on a later read");
  // a node that could not be read, and one from before the section
  assert.equal(text(groups[1].querySelector(".host-empty")), "request timed out");
  assert.equal(text(groups[2].querySelector(".host-empty")),
    "This backend does not report disk space");
  assert.equal(groups[2].querySelectorAll(".host-disk").length, 0);
  app.context.closeHostPanel();
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
  assert.ok(inDotColumn(pings[0], "gdot"));
  assert.equal(text(pings[1].querySelector(".host-ping-name")), "old");
  assert.equal(text(pings[1].querySelector(".host-ping-ms")), "failed");
  assert.equal(rows(app.panel(), "host-node-name").map(text)
    .includes("laptop"), false);
  // a node that is there but does not report activity still says so
  const empties = rows(app.panel(), "host-empty").map(text);
  assert.ok(!empties.includes("Backend unavailable"), empties.join("|"));
  assert.ok(empties.includes("This backend does not report host activity"), empties.join("|"));
  // a second measurement builds each backend's sparkline: the measurements
  // side by side (a spell with the tab hidden is not a gap in the link),
  // one unbroken line scaled to its own peak with headroom, on a floor
  app.hostPanel.pings.set(2, [[500, 12.4], [504, 15.2], [900, 4.0], [904, 15.2]]);
  context.renderHostPanel();
  const spark = rows(app.panel(), "host-ping")[0].querySelector(".host-chart.spark");
  assert.ok(spark);
  assert.equal(spark.querySelectorAll(".host-chart-line").length, 1);
  const peak = (100 - 100 / 1.15).toFixed(2);
  assert.equal(spark.querySelector(".host-chart-line").getAttribute("d"),
    `M0.00 ${(100 - (12.4 / (15.2 * 1.15)) * 100).toFixed(2)}L33.33 ${peak}` +
    `L66.67 ${(100 - (4 / (15.2 * 1.15)) * 100).toFixed(2)}L100.00 ${peak}`);
  assert.ok(spark.querySelector(".host-chart-area"));
  assert.ok(spark.querySelector(".host-chart-floor"));
  // three reachable nodes, each with one chart, one disk and one process block
  assert.equal(rows(app.panel(), "host-node").length, 9);
  // a failed read keeps the tree that node last served rather than blanking it
  app.hostPanel.nodes.set(2, { data: metrics(), error: "network error" });
  context.renderHostPanel();
  assert.equal(rows(app.panel(), "host-tree").length, 2);
}

/* ---- a first read already has a chart, independent of slow process reads ---- */
async function cachedLatency() {
  const history = Array.from({ length: 60 }, (_, i) => [1000 + i * 4, i + 1]);
  let payload = { backends: [{ id: 2, name: "Workshop", ok: true, ms: 60, history }] };
  let finishMetrics;
  const slowMetrics = new Promise(resolve => { finishMetrics = resolve; });
  const app = consoleFor({
    backends: [{ id: 2, name: "Workshop", capabilities: [] }],
    remoteOk: { 2: true },
    answers: { "0:host/metrics": () => slowMetrics,
      "0:backends/latency": () => payload },
  });
  app.context.openHostPanel();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.ok(app.panel().querySelector(".host-chart.spark"));
  assert.equal(app.hostPanel.pings.get(2).length, 40);
  assert.equal(app.hostPanel.pings.get(2)[0][0], history[20][0]);
  await app.context.readHostLatency(app.hostPanel.sequence);
  assert.equal(app.hostPanel.pings.get(2).length, 40); // replacement, never duplicated
  app.context.closeHostPanel();
  finishMetrics(metrics());
  await new Promise(resolve => setTimeout(resolve, 0));
  app.context.openHostPanel();
  assert.ok(app.panel().querySelector(".host-chart.spark"));
  await new Promise(resolve => setTimeout(resolve, 0));
  payload = { backends: [{ id: 2, name: "Workshop", ok: false, offline: true, history: [] }] };
  await app.context.readHostLatency(app.hostPanel.sequence);
  assert.equal(app.hostPanel.pings.size, 0);
  payload = { backends: [{ id: 2, name: "Workshop", ok: true, ms: 3,
    history: [[2000, 2], [2004, 3]] }] };
  await app.context.readHostLatency(app.hostPanel.sequence);
  assert.equal(app.hostPanel.pings.get(2).length, 2);
  assert.ok(app.panel().querySelector(".host-chart.spark"));
  app.state.backends = [];
  app.context.renderHostPanel();
  assert.equal(rows(app.panel(), "host-ping").length, 0);
  await app.context.readHostLatency(app.hostPanel.sequence);
  assert.equal(app.hostPanel.pings.size, 0);
  app.context.closeHostPanel();
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
  // CPU, Disk and Processes only, each holding this instance alone
  assert.deepEqual(rows(app.panel(), "host-sec-title").map(text), ["CPU", "Disk", "Processes"]);
  assert.deepEqual(rows(app.panel(), "host-node-name").map(text),
    ["this instance", "this instance", "this instance"]);
  // and it comes back the moment the health worker says the node is up
  app.state.remoteOk[2] = true;
  context.renderHostPanel();
  assert.deepEqual(rows(app.panel(), "host-sec-title").map(text),
    ["CPU", "Disk", "Latency", "Processes"]);
  assert.deepEqual(rows(app.panel(), "host-node-name").map(text),
    ["this instance", "nas", "this instance", "nas", "this instance", "nas"]);
}

/* ---- with no backends configured there is nothing to measure ---- */
async function soloInstance() {
  const app = consoleFor({ answers: { "0:host/metrics": metrics() } });
  app.context.toggleHostPanel();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(app.calls, [[0, "host/metrics?window=900"]]);
  assert.equal(rows(app.panel(), "host-ping").length, 0);
  assert.equal(rows(app.panel(), "host-sec").length, 3, "CPU, Disk and Processes");
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
  await sections();
  await diskSpace();
  charting();
  await liveSamples();
  await processTree();
  await backends();
  await cachedLatency();
  await allUnreachable();
  await soloInstance();
  await emptyStates();
  console.log("host activity panel tests passed");
}

main().catch(error => { console.error(error); process.exit(1); });
