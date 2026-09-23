"use strict";
/* The Token usage sheet against the fake DOM: which nodes it asks and what
   it says about the ones it cannot, the figures it adds up, the columns it
   folds hours into, the colours it keeps per series (an engine's own hue,
   survivors keeping theirs, the tail folded into Other), the chart's marks,
   the readout the arrow keys move, the breakdown that is the chart's table
   view, the sessions list and the session a row opens, the three choices it
   remembers per browser, and a newer read that drops an older one's answer.
   No browser, engine, network or quota. */
process.env.TZ = "UTC";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");

function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}

const DAY = 86400;
// Tue 2026-09-22 15:00 UTC
const NOW = Date.UTC(2026, 8, 22, 15) / 1000;
const H = at => Math.floor(at / 3600) * 3600;
const plain = value => JSON.parse(JSON.stringify(value));

function node(bid, buckets, extra = {}) {
  const totals = { input: 0, output: 0, cache_read: 0, cache_write: 0, reasoning: 0, turns: 0 };
  for (const row of buckets) {
    totals.input += row[3]; totals.output += row[4]; totals.cache_read += row[5];
    totals.cache_write += row[6]; totals.reasoning += row[7]; totals.turns += row[8];
  }
  return { ok: true, since: 0, until: 0, step: 3600, offset: 0, first_at: NOW - 10 * DAY,
    columns: ["at", "engine", "model", "input", "output", "cache_read", "cache_write",
              "reasoning", "turns"],
    buckets, totals, sessions: [], session_count: 0, jobs: {}, ...extra };
}

function consoleFor(options = {}) {
  const document = new FakeDocument();
  const el = (tag, cls = "", text) => {
    const made = document.createElement(tag);
    if (cls) made.className = cls;
    if (text !== undefined) made.textContent = text;
    return made;
  };
  const storage = new Map(Object.entries(options.storage || {}));
  const calls = { requests: [], opened: [], closed: 0, modals: 0 };
  const replies = options.replies || {};
  const button = el("button");
  button.id = "btn-usage";
  document.body.appendChild(button);
  const state = {
    backends: options.backends || [],
    engCache: {},
    engines: [{ key: "claude", label: "Claude Code", model_options: [
      { value: "opus[1m]", label: "Opus 5.5 (1M)", aliases: ["claude-opus-5-5[1m]"] }] },
      { key: "codex", label: "Codex", model_options: [] },
      { key: "opencode", label: "OpenCode", model_options: [] }],
    nodeCapabilities: options.localCapable === false ? [] : ["token-usage"],
  };
  const context = vm.createContext({
    document, el, state, console, Date, Math, Number, Array, JSON, Set, Map, Promise,
    Object, String, isNaN, parseFloat, setTimeout, clearTimeout,
    SVG_NS: "http://www.w3.org/2000/svg",
    $: id => document.getElementById(id),
    lsGet: key => storage.has(key) ? storage.get(key) : null,
    lsSet: (key, value) => storage.set(key, value),
    backendName: bid => bid ? (state.backends.find(b => b.id === bid) || {}).name || `backend ${bid}` : "Studio",
    backendConnectionAllowed: bid => !bid || !(options.offline || []).includes(bid),
    remoteAvailability: bid => (options.offline || []).includes(bid) ? "bad" : "ok",
    backendStateNote: availability => availability === "bad" ? "Backend unavailable" : "",
    nodeHasCapability: (bid, capability) => {
      if (!bid) return state.nodeCapabilities.includes(capability);
      const backend = state.backends.find(b => b.id === bid);
      return !!backend && backend.capabilities.includes(capability);
    },
    engineInfo: (bid, key) => ((options.catalogs || {})[bid] || state.engines)
      .find(engine => engine.key === key) || null,
    engineModelOption: (engine, model) => ((engine && engine.model_options) || []).find(option =>
      option.value === model || (option.aliases || []).includes(model)),
    fmtDateTime: (ts, opts) => {
      if (opts.hour) return new Intl.DateTimeFormat("en-GB", opts).format(new Date(ts * 1000));
      const date = new Date(ts * 1000);
      const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      let text = `${months[date.getMonth()]} ${date.getDate()}`;
      if (opts.weekday) text = `${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][date.getDay()]}, ${text}`;
      if (opts.year) text += `, ${date.getFullYear()}`;
      if (opts.month === "long") text = `${months[date.getMonth()]}ember ${date.getFullYear()}`;
      return text;
    },
    findSessionMeta: (bid, sid) => (options.known || []).includes(`${bid}:${sid}`) ? { id: sid } : null,
    openSessionTab: (bid, sid, meta) => calls.opened.push([bid, sid, plain(meta)]),
    modal: (html, cls, reopen) => {
      calls.modals++;
      const back = el("div", "modal-backdrop");
      const m = el("div", "modal " + cls);
      m.innerHTML = html;
      back.appendChild(m);
      document.body.appendChild(back);
      const listeners = [];
      const close = () => { calls.closed++; back.remove(); listeners.forEach(fn => fn()); };
      calls.reopen = reopen;
      return { m, close, onClose: fn => listeners.push(fn) };
    },
    api: (bid, route, opts) => {
      calls.requests.push({ bid, route, opts: plain(opts) });
      const reply = typeof replies[bid] === "function" ? replies[bid](route) : replies[bid];
      if (reply instanceof Error) return Promise.reject(reply);
      return Promise.resolve(reply);
    },
  });
  context.Date.now = () => NOW * 1000;
  vm.runInContext(between("/* ================= token usage =================",
    "$(\"toggle-archived\")"), context);
  return { document, context, calls, storage, state };
}

const settle = () => new Promise(resolve => setImmediate(resolve));
const text = (root, selector) => [...root.querySelectorAll(selector)].map(item => item.textContent);

(async () => {
  const realNow = Date.now;
  Date.now = () => NOW * 1000;
  try {
    /* ---------- the helpers ---------- */
    const { context } = consoleFor();
    assert.deepEqual(["0", "999", "1k", "1.2k", "12.3k", "123k", "1.2M", "12M", "1.2B"].map(String),
      [0, 999, 1000, 1234, 12345, 123456, 1234567, 12e6, 1.2e9].map(context.fmtUsage));
    assert.equal(context.fmtUsage(-5), "0");
    assert.deepEqual([0, 1, .5, .123, .05, .0005].map(context.usagePercent),
      ["0%", "100%", "50%", "12%", "5.0%", "<0.1%"]);
    assert.deepEqual(plain(context.usageTicks(0)), [0]);
    assert.deepEqual(plain(context.usageTicks(28.9e6)), [0, 1e7, 2e7, 3e7]);
    assert.deepEqual(plain(context.usageTicks(7)), [0, 2, 4, 6, 8]);
    // a range asks from its first midnight by the hour; All by the day at this offset
    const month = context.usageSpan("30", NOW);
    assert.equal(month.since, Date.UTC(2026, 7, 24) / 1000);
    assert.equal(month.step, 3600);
    assert.equal(month.until, NOW + 60);
    assert.equal(context.usageSpan("7", NOW).since, Date.UTC(2026, 8, 16) / 1000);
    const all = context.usageSpan("all", NOW);
    assert.deepEqual([all.step, all.offset, all.until - all.since], [86400, 0, 5 * 365 * DAY]);
    assert.equal(context.usageSpan("nonsense", NOW).range.key, "30", "anything else is the default");
    // Rolling elapsed hours, including quiet ones, partial edges and repeated
    // local hours. The report's fixed offset also covers half/quarter-hour zones.
    const hourly = context.usageSpan("24h", NOW);
    assert.deepEqual([hourly.since, hourly.until, hourly.step], [NOW - DAY, NOW, 3600]);
    assert.equal(context.usageUnit(hourly, NOW - 900 * DAY, NOW), "hour");
    assert.equal(context.usageBuckets(hourly, "hour", NOW - 60, NOW).length, 24);
    const partial = context.usageSpan("24h", NOW + 1500);
    assert.equal(context.usageBuckets(partial, "hour", null, NOW + 1500).length, 25);
    for (const [zone, at] of [["Asia/Kolkata", NOW], ["Asia/Kathmandu", NOW],
      ["America/New_York", Date.UTC(2026, 10, 1, 12) / 1000],
      ["America/New_York", Date.UTC(2026, 2, 8, 12) / 1000],
      ["Australia/Lord_Howe", Date.UTC(2026, 3, 5, 12) / 1000]]) {
      process.env.TZ = zone;
      const span = context.usageSpan("24h", at);
      const buckets = context.usageBuckets(span, "hour", null, at);
      assert.equal(span.until - span.since, DAY);
      assert.equal(new Set(buckets.map(b => b.key)).size, buckets.length, zone);
      assert.equal((buckets[0].start + span.offset) % 3600, 0, zone);
      assert.ok(buckets.length >= 24 && buckets.length <= 25, zone);
      const rows = buckets.map(b => ({ bid: 0, at: b.start, input: 1, output: 0,
        cache_read: 0, cache_write: 0, reasoning: 0 }));
      context.usageFill(buckets, rows, span, "hour", "backend", "all", new Set(["b:0"]));
      assert.ok(buckets.every(b => b.total === 1), zone + " each report bucket lands once");
      for (let i = 1; i < buckets.length; i++) assert.equal(buckets[i].start - buckets[i - 1].start, 3600);
      if (zone === "America/New_York" && new Date(at * 1000).getMonth() === 10) {
        const repeated = buckets.filter(b => new Date(b.start * 1000).getHours() === 1);
        assert.equal(repeated.length, 2);
        assert.notEqual(context.usageBucketLabel(repeated[0], "hour", true),
          context.usageBucketLabel(repeated[1], "hour", true), "hover distinguishes the repeated hour");
      }
    }
    process.env.TZ = "UTC";
    // the columns: a day each, a week from Monday, a month; empty ones kept
    const days = context.usageBuckets(month, "day", null, NOW);
    assert.equal(days.length, 30);
    assert.equal(days[0].start, Date.UTC(2026, 7, 24) / 1000);
    assert.equal(days[29].start, Date.UTC(2026, 8, 22) / 1000);
    // a range begins no earlier than the first day anything was counted
    const counted = context.usageBuckets(month, "day", NOW - 3 * DAY, NOW);
    assert.equal(counted.length, 4);
    assert.equal(counted[0].start, Date.UTC(2026, 8, 19) / 1000);
    assert.equal(context.usageBuckets(month, "day", NOW - 90 * DAY, NOW).length, 30);
    const weeks = context.usageBuckets(all, "week", NOW - 200 * DAY, NOW);
    assert.equal(new Date(weeks[0].start * 1000).getUTCDay(), 1, "weeks start on Monday");
    assert.equal(context.usageUnit(all, NOW - 100 * DAY, NOW), "day");
    assert.equal(context.usageUnit(all, NOW - 200 * DAY, NOW), "week");
    assert.equal(context.usageUnit(all, NOW - 900 * DAY, NOW), "month");
    assert.equal(context.usageUnit(month, NOW - 900 * DAY, NOW), "day", "a fixed range is always daily");

    /* ---------- colours ---------- */
    const series = (lens, keys) => keys.map(([key, engine, value]) => ({ key, engine, value }));
    // an engine wears its own hue; another engine takes a free slot, never a reserved one
    let engines = series("engine", [["e:codex", "codex", 9], ["e:claude", "claude", 5], ["e:x", "x", 1]]);
    context.usageAssignSlots(engines, "engine");
    assert.deepEqual(engines.map(item => item.slot), [3, 2, 1]);
    // survivors keep their slot when the series on show change
    let models = series("model", [["m:a", "claude", 9], ["m:b", "claude", 8], ["m:c", "codex", 7]]);
    context.usageAssignSlots(models, "model");
    assert.deepEqual(models.map(item => item.slot), [1, 2, 3]);
    models = series("model", [["m:c", "codex", 9], ["m:d", "codex", 8], ["m:a", "claude", 1]]);
    context.usageAssignSlots(models, "model");
    assert.deepEqual(models.map(item => item.slot), [3, 2, 1], "c and a keep theirs; d takes the free one");
    // past seven, the tail folds into Other
    const many = series("backend", Array.from({ length: 9 }, (_, n) => [`b:${n}`, "", 20 - n]));
    const { colored, folded } = context.usageAssignSlots(many, "backend");
    assert.equal(colored.length, 7);
    assert.deepEqual(folded.map(item => item.slot), [0, 0]);
    assert.equal(context.usageColor(0), "var(--viz-other)");
    assert.equal(context.usageColor(4), "var(--viz-4)");
    // a requested alias and the id it resolved to are one model: the
    // catalog's row, named once
    const spelled = (model, input) => ({ bid: 0, at: NOW, engine: "claude", model, input, output: 0,
      cache_read: 0, cache_write: 0, reasoning: 0 });
    const merged = context.usageSeries([spelled("opus[1m]", 5), spelled("claude-opus-5-5[1m]", 7),
      spelled("claude-sonnet-9", 2)], "model", "all");
    assert.deepEqual(plain(merged.map(item => [item.label, item.value])),
      [["Opus 5.5 (1M)", 12], ["claude-sonnet-9", 2]]);
    // and so is one model two backends' catalogs file under different rows:
    // it is the name the catalog gives it, per engine
    const fleet = consoleFor({ catalogs: {
      0: [{ key: "claude", label: "Claude Code", model_options: [
        { value: "fable[1m]", label: "Fable", aliases: ["fable", "claude-fable-5-1"] }] },
        { key: "codex", label: "Codex", model_options: [{ value: "fable", label: "Fable" }] }],
      5: [{ key: "claude", label: "Claude Code", model_options: [
        { value: "fable", label: "Fable", aliases: ["claude-fable-5-1[1m]"] }] }] } }).context;
    const onBackend = (bid, engine, model, input) => ({ ...spelled(model, input), bid, engine });
    const fleetModels = fleet.usageSeries([onBackend(0, "claude", "claude-fable-5-1", 5),
      onBackend(5, "claude", "claude-fable-5-1[1m]", 7), onBackend(5, "claude", "fable", 1),
      onBackend(0, "codex", "fable", 3)], "model", "all");
    assert.deepEqual(plain(fleetModels.map(item => [item.label, item.value])),
      [["Fable · Claude Code", 13], ["Fable · Codex", 3]],
      "one series per engine's model, the engine named only where two engines share a name");

    /* ---------- the sheet ---------- */
    const today = Date.UTC(2026, 8, 22) / 1000;
    const local = [
      [H(today + 3600 * 9), "claude", "claude-opus-5-5[1m]", 10, 200, 3000, 40, 0, 2],
      [H(today + 3600 * 9), "claude", "claude-haiku-4-5", 500, 20, 0, 0, 0, 1],
      [H(today - DAY + 3600 * 23), "codex", "gpt-6-astra", 400, 50, 600, 0, 30, 1],
      [H(today - 3 * DAY), "claude", "", 1, 1, 1, 0, 0, 1],
    ];
    const remote = [
      [H(today + 3600 * 2), "opencode", "vendor/open", 7, 70, 700, 0, 60, 1],
    ];
    const studio = node(0, local, {
      sessions: [
        { id: 4, name: "Harbor dashboard", color: "#e0784f", deleted: false, parent: null, parent_name: "",
          engines: ["claude"], input: 511, output: 221, cache_read: 3001, cache_write: 40, reasoning: 0,
          turns: 3, last_at: today },
        { id: 9, name: "", color: "", deleted: true, parent: null, parent_name: "",
          engines: ["codex"], input: 400, output: 50, cache_read: 600, cache_write: 0, reasoning: 30,
          turns: 1, last_at: today }],
      session_count: 7,
      jobs: { title: { input: 3, output: 3, cache_read: 0, cache_write: 0, reasoning: 0, turns: 2 } },
      first_at: NOW - 40 * DAY });
    const workshop = node(3, remote, { first_at: NOW - 20 * DAY, sessions: [
      { id: 2, name: "Card spacing", color: "#4dd0c4", deleted: false, parent: 1, parent_name: "Garden",
        engines: ["opencode"], input: 7, output: 70, cache_read: 700, cache_write: 0, reasoning: 60,
        turns: 1, last_at: today }], session_count: 1 });
    const sheet = consoleFor({
      backends: [{ id: 3, name: "Workshop", capabilities: ["token-usage"] },
                 { id: 5, name: "Attic", capabilities: ["token-usage"] },
                 { id: 6, name: "Old box", capabilities: [] }],
      offline: [5], replies: { 0: studio, 3: workshop }, known: ["0:4", "3:2"] });
    sheet.context.modalTokenUsage();
    const m = sheet.document.querySelector(".token-usage-modal");
    assert.ok(m, "the sheet opened");
    assert.equal(typeof sheet.calls.reopen, "function", "Back and Forward can reopen it fresh");
    assert.equal(m.querySelector(".tu-readout").textContent, "Reading token usage…");
    await settle();
    // this instance and every backend that can answer, each asked once for the range
    assert.deepEqual(sheet.calls.requests.map(request => request.bid), [0, 3]);
    const query = sheet.calls.requests[0].route;
    assert.equal(query, `token-usage?since=${Date.UTC(2026, 7, 24) / 1000}&until=${NOW + 60}&step=3600&offset=0`);
    assert.deepEqual(sheet.calls.requests[0].opts, { timeoutMs: 20000 });
    // the ones it could not ask are named, not silently left out
    assert.deepEqual(text(m, ".tu-note"), ["Attic: Backend unavailable",
      "Old box: Keeps no token ledger yet · update Puppy on it"]);
    assert.equal(m.querySelector(".tu-intro").textContent,
      "What every engine used on each backend · counted since Aug 13, 2026");
    // the figures, summed across the nodes
    assert.deepEqual(text(m, ".tu-tile-label"), ["Total", "Input", "Cache", "Output", "Turns"]);
    assert.deepEqual(text(m, ".tu-tile-value"), ["5.6k", "918", "4.3k", "341", "6"]);
    assert.deepEqual(text(m, ".tu-tile-sub"), ["2 backends", "uncached", "4.3k read · 40 written",
      "90 reasoning", "in 8 sessions"]);
    // by backend: two series, the chart's legend and table agree
    const svg = m.querySelector(".tu-plot svg");
    assert.ok(svg, "the chart is drawn");
    const bars = m.querySelectorAll(".tu-plot .tu-bar");
    assert.equal(bars.length, 4, "a column per day that has usage, one segment per series in it");
    assert.deepEqual(text(m, ".tu-legend-label"), ["Studio", "Workshop"]);
    assert.deepEqual(text(m, ".tu-table .tu-name-label"), ["Studio", "Workshop"]);
    assert.deepEqual(text(m, ".tu-table .tu-total"), ["4.8k", "777"]);
    assert.deepEqual(text(m, ".tu-pct"), ["86%", "14%"]);
    assert.deepEqual(text(m, ".tu-table .tu-name-sub"), ["in 911 · cache 3.6k · out 271", "in 7 · cache 700 · out 70"],
      "only the split a phone shows under the name");
    // the readout: the range's total and its peak, then every series
    assert.equal(m.querySelector(".tu-read-when").textContent, "Last 30 days");
    assert.equal(m.querySelector(".tu-read-sum").textContent, "5.6k");
    assert.equal(m.querySelector(".tu-read-note").textContent, "peak Sep 22 · 4.5k");
    assert.deepEqual(text(m, ".tu-read-item"), ["4.8kStudio", "777Workshop"]);
    // the arrow keys read one column at a time; Escape goes back to the range
    const plot = m.querySelector(".tu-plot");
    plot.dispatchEvent(new FakeEvent("keydown", { key: "ArrowLeft" }));
    assert.equal(m.querySelector(".tu-read-when").textContent, "Tue, Sep 22");
    assert.equal(m.querySelector(".tu-read-sum").textContent, "4.5k");
    assert.ok(m.querySelectorAll(".tu-plot .tu-bar.dim").length > 0, "the other columns step back");
    assert.equal(m.querySelectorAll(".tu-plot .tu-hot").length, 1);
    plot.dispatchEvent(new FakeEvent("keydown", { key: "ArrowLeft" }));
    assert.equal(m.querySelector(".tu-read-when").textContent, "Mon, Sep 21");
    assert.deepEqual(text(m, ".tu-read-item"), ["1.1kStudio", "0Workshop"],
      "every series in its place, whatever the day used");
    assert.deepEqual(text(m, ".tu-read-item.none"), ["0Workshop"]);
    plot.dispatchEvent(new FakeEvent("keydown", { key: "Home" }));
    assert.equal(m.querySelector(".tu-read-when").textContent, "Mon, Aug 24");
    assert.deepEqual(text(m, ".tu-read-item.none"), ["0Studio", "0Workshop"], "a quiet day reads nought for each");
    const escape = new FakeEvent("keydown", { key: "Escape", cancelable: true, bubbles: true });
    plot.dispatchEvent(escape);
    assert.equal(escape.defaultPrevented, true, "the sheet stays open");
    assert.equal(m.querySelector(".tu-read-when").textContent, "Last 30 days");
    assert.equal(m.querySelectorAll(".tu-plot .tu-bar.dim").length, 0);

    // by engine: each engine's own hue, stacked in slot order
    const pick = (group, label) => [...m.querySelectorAll(`.${group} .seg-btn`)]
      .find(item => item.textContent === label);
    const engineButton = pick("tu-lens", "Engine");
    engineButton.focus();
    engineButton.click();
    assert.equal(sheet.document.activeElement, engineButton, "the pressed choice keeps the focus");
    assert.equal(engineButton.getAttribute("aria-pressed"), "true");
    assert.equal(sheet.storage.get("puppy.usage.by"), "engine");
    assert.deepEqual(text(m, ".tu-legend-label"), ["Claude Code", "Codex", "OpenCode"]);
    const swatches = [...m.querySelectorAll(".tu-legend .tu-swatch")].map(item => item.style.background);
    assert.deepEqual(swatches, ["var(--viz-2)", "var(--viz-3)", "var(--viz-7)"]);
    assert.equal(m.querySelector(".tu-breakdown .field-optional").textContent, "· by engine");
    // by model: the catalog's names, an engine's default named for it
    pick("tu-lens", "Model").click();
    assert.deepEqual(text(m, ".tu-table .tu-name-label"),
      ["Opus 5.5 (1M)", "gpt-6-astra", "vendor/open", "claude-haiku-4-5", "Claude Code default"]);
    // a measure recounts everything under it without asking again
    const asked = sheet.calls.requests.length;
    pick("tu-measure", "Output").click();
    assert.equal(sheet.calls.requests.length, asked);
    assert.equal(sheet.storage.get("puppy.usage.measure"), "output");
    assert.deepEqual(text(m, ".tu-table .tu-total"), ["200", "70", "50", "20", "1"]);
    assert.equal(m.querySelector(".tu-read-sum").textContent, "341");

    // the sessions: heaviest first, a task named with its Main, a deleted
    // one unopenable, the runs that belong to no session after them
    assert.equal(m.querySelector(".tu-sessions-section .field-optional").textContent,
      "· the 3 heaviest of 8");
    assert.deepEqual(text(m, ".tu-session-name"),
      ["Harbor dashboard", "Card spacing", "Deleted session", "Session titles"]);
    assert.deepEqual(text(m, ".tu-session-sub"), ["Studio · Claude Code",
      "Workshop · OpenCode · task of Garden", "Studio · Codex", "generated names"]);
    assert.deepEqual(text(m, ".tu-session-turns"), ["3 turns", "1 turn", "1 turn", "2 runs"]);
    const rows = m.querySelectorAll(".tu-session");
    assert.deepEqual(rows.map(row => row.tagName.toLowerCase()), ["button", "button", "div", "div"]);
    assert.equal(rows[0].querySelector(".tu-dot").style.background, "#e0784f");
    rows[1].click();
    assert.deepEqual(sheet.calls.opened, [[3, 2, { id: 2 }]]);
    assert.equal(sheet.calls.closed, 1, "the sheet closes for the session it opens");

    /* ---------- a range asks again; a newer read wins ---------- */
    let release;
    const slow = new Promise(resolve => { release = resolve; });
    const racing = consoleFor({ replies: { 0: route => route.includes("step=86400") ?
      node(0, [[Date.UTC(2026, 8, 1) / 1000, "claude", "", 5, 5, 5, 0, 0, 1]],
        { first_at: Date.UTC(2026, 8, 1, 9) / 1000 }) : slow } });
    racing.context.modalTokenUsage();
    const r = racing.document.querySelector(".token-usage-modal");
    const seg = label => [...r.querySelectorAll(".tu-range .seg-btn")].find(item => item.textContent === label);
    assert.equal(seg("30 days").getAttribute("aria-pressed"), "true");
    assert.ok(r.querySelector(".tu-body").classList.contains("loading"));
    assert.equal(r.querySelector("#tu-refresh").disabled, true);
    seg("All").click();
    await settle();
    assert.equal(racing.storage.get("puppy.usage.range"), "all");
    assert.ok(racing.calls.requests[1].route.includes("step=86400&offset=0"));
    assert.equal(r.querySelector(".tu-read-when").textContent, "All time");
    assert.equal(r.querySelector(".tu-read-sum").textContent, "15");
    release(node(0, [[H(today), "claude", "", 999, 0, 0, 0, 0, 1]]));
    await settle();
    assert.equal(r.querySelector(".tu-read-sum").textContent, "15", "the older answer is dropped");
    assert.ok(!r.querySelector(".tu-body").classList.contains("loading"));
    assert.equal(r.querySelector("#tu-refresh").disabled, false);

    /* ---------- the remembered choices, and what nothing looks like ---------- */
    const remembered = consoleFor({ storage: { "puppy.usage.range": "7", "puppy.usage.by": "model",
      "puppy.usage.measure": "cache" }, replies: { 0: node(0, []) } });
    remembered.context.modalTokenUsage();
    await settle();
    const q = remembered.document.querySelector(".token-usage-modal");
    const on = group => [...q.querySelectorAll(`.${group} .seg-btn.on`)].map(item => item.textContent);
    assert.deepEqual([on("tu-range"), on("tu-lens"), on("tu-measure")], [["7 days"], ["Model"], ["Cache"]]);
    assert.equal(q.querySelector(".tu-plot-empty").textContent, "No tokens used in the last 7 days");
    assert.equal(q.querySelector(".tu-table .tu-empty").textContent, "Nothing used in this range");
    assert.equal(q.querySelector(".tu-sessions .tu-empty").textContent, "No session used tokens in this range");
    assert.deepEqual(text(q, ".tu-legend-item"), [], "no legend for nothing");
    const stale = consoleFor({ storage: { "puppy.usage.range": "365", "puppy.usage.by": "Engine" },
      replies: { 0: node(0, []) } });
    stale.context.modalTokenUsage();
    await settle();
    const s = stale.document.querySelector(".token-usage-modal");
    assert.deepEqual([...s.querySelectorAll(".seg-btn.on")].map(item => item.textContent),
      ["30 days", "Backend", "All"], "anything but the exact value reads as the default");

    const daySheet = consoleFor({ storage: { "puppy.usage.range": "24h" }, replies: {
      0: node(0, [[NOW - DAY, "claude", "", 2, 3, 0, 0, 0, 1],
        [NOW - 3600, "codex", "", 4, 5, 0, 0, 0, 1]]) } });
    const dayView = daySheet.context.modalTokenUsage();
    await settle();
    assert.equal(dayView.view.chart.unit, "hour");
    assert.equal(dayView.view.chart.buckets.length, 24);
    assert.equal(dayView.view.chart.total, 14);
    assert.equal(dayView.m.querySelector(".tu-read-when").textContent, "Last 24 hours");
    assert.deepEqual(text(dayView.m, ".tu-tile-label"), ["Total", "Input", "Cache", "Output", "Turns"]);
    assert.ok(daySheet.calls.requests[0].route.includes(`since=${NOW - DAY}&until=${NOW}&step=3600`));
    assert.ok(!dayView.m.textContent.includes("$") && !dayView.m.textContent.includes("cost"));
    dayView.m.querySelector(".tu-plot").dispatchEvent(new FakeEvent("keydown", { key: "End" }));
    assert.equal(dayView.m.querySelector(".tu-read-sum").textContent, "9");

    /* ---------- failures ---------- */
    const failing = consoleFor({ backends: [{ id: 3, name: "Workshop", capabilities: ["token-usage"] }],
      replies: { 0: new Error("HTTP 500"), 3: node(3, remote) } });
    failing.context.modalTokenUsage();
    await settle();
    const f = failing.document.querySelector(".token-usage-modal");
    assert.deepEqual(text(f, ".tu-note"), ["Studio: Could not read its token usage · HTTP 500"]);
    assert.ok(f.querySelector(".form-error").classList.contains("hidden"), "one node still answered");
    assert.equal(f.querySelector(".tu-tile-sub").textContent, "1 backend");
    const nothing = consoleFor({ localCapable: false });
    nothing.context.modalTokenUsage();
    await settle();
    const n = nothing.document.querySelector(".token-usage-modal");
    assert.equal(n.querySelector(".form-error").textContent, "No backend keeps a token ledger yet");
    assert.ok(!n.querySelector(".form-error").classList.contains("hidden"));
    assert.equal(nothing.calls.requests.length, 0);
    const down = consoleFor({ replies: { 0: new Error("request timed out") } });
    down.context.modalTokenUsage();
    await settle();
    assert.equal(down.document.querySelector(".token-usage-modal .form-error").textContent,
      "Could not read token usage · request timed out");

    // the footer button opens it
    const footer = consoleFor({ replies: { 0: node(0, []) } });
    footer.document.getElementById("btn-usage").click();
    assert.equal(footer.calls.modals, 1);
  } finally {
    Date.now = realNow;
  }
  console.log("PASS: token usage sheet - the nodes asked and the ones named, figures summed, " +
    "columns folded, colours kept per series, the chart and its keyboard readout, the table view, " +
    "sessions and the one a row opens, remembered choices and the newer read winning");
})().catch(error => { console.error(error); process.exit(1); });
