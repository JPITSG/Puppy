/* Run with node tests/schedules_ui_test.js. The schedules sheet behind the
   footer's clock, against the fake DOM: each timing as its card words it in
   both clocks; the topic landing in the console (the button's working and
   waiting tones, the open sheets following it) and the run conversations it
   keeps out of the sidebar - only while their schedule keeps them; the sheet's
   cards in the topic's order, redrawn in place, and every verb on them (the
   switch, Run now, Stop, History, Edit, Duplicate, Delete with or without
   the kept conversations); the history sheet's facts and runs, a run's
   conversation opened only when this console knows it as that run, a run
   deleted; and the editor: its defaults, the body it sends for each kind of
   timing, its inline refusals, the instance's preview of the next runs, and
   an edit and a copy read back from the controller. No browser, engine,
   network or quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
};

const document = new FakeDocument();
const button = document.createElement("button");
button.id = "btn-schedules";
document.body.appendChild(button);
const requests = [], routes = {}, toasts = [], opened = [], dialogs = [];
let sidebarRenders = 0, confirmAnswer = true;
const engines = [{
  key: "claude", label: "Claude Code", allow_custom_model: true,
  session_defaults: { model: "", effort: "", permission_mode: "default" },
  model_options: [{ value: "", label: "Default" }, { value: "sonnet", label: "Sonnet 5",
    effort_options: [{ value: "", label: "Default" }, { value: "high", label: "High" }] }],
  effort_options: [{ value: "", label: "Default" }, { value: "high", label: "High" }],
  permission_options: [{ value: "default", label: "Ask" }, { value: "bypassPermissions", label: "Bypass" }],
}];
class FakeComposer {
  constructor(box, host) { this.box = box; this.host = host; this.text = ""; this.busy = false; FakeComposer.last = this; }
  set(value) { this.text = value; }
  message() { return this.text.trim(); }
  setBusy(busy) { this.busy = busy; }
  syncUploadButton() {}
  destroy() { this.destroyed = true; }
  focus() {}
}
const context = vm.createContext({
  document, console, Number, String, JSON, Promise, Map, Set, Array, Object, RegExp, Error, Date,
  Math, setTimeout, clearTimeout,
  state: {
    clockFormat: "24h", schedules: null, backends: [
      { id: 7, name: "Workshop", capabilities: ["session-schedule-runs"] },
      { id: 8, name: "Attic", capabilities: [] }],
    engines, engCache: { 7: engines }, engMap: {}, notify: { configured: true, enabled: true },
    defaultCwd: "/home/mira",
  },
  $: id => document.getElementById(id),
  el: (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  },
  esc: value => String(value == null ? "" : value),
  backendName: bid => ({ 0: "Studio", 7: "Workshop", 8: "Attic" }[bid] || `backend ${bid}`),
  backendHasCapability: (backend, cap) => !!backend && (backend.capabilities || []).includes(cap),
  backendConnectionAllowed: () => true,
  engineInfo: (bid, key) => engines.find(item => item.key === key) || null,
  modelDisplayName: (bid, engine, model) => (model === "sonnet" ? "Sonnet 5" : model),
  fmtStamp: ts => `stamp ${ts}`,
  hostDuration: seconds => `${seconds}s`,
  findSessionMeta: (bid, sid) => context.sessions[`${bid}:${sid}`] || null,
  sessions: {},
  openSessionTab: (...args) => opened.push(args),
  liveViews: () => [],
  renderSidebar: () => { sidebarRenders += 1; },
  promptSpinnerNode: () => { const n = document.createElement("span"); n.className = "spinner"; return n; },
  md: text => String(text), decorateMarkdownLinks() {}, decorateMarkdownImages() {},
  loadMarkdownLibraries: () => Promise.resolve(),
  modalSubjectHtml: text => `<p class="modal-subject">${text}</p>`,
  modalCopyHtml: text => `<p class="modal-copy">${text}</p>`,
  modalConfirm: async () => confirmAnswer,
  composerBoxHtml: ({ id }) => `<div class="composer-box"><textarea id="${id}"></textarea></div>`,
  Composer: FakeComposer,
  refreshChoiceSelect() {}, wireDirectoryPicker() {},
  rememberEnginePayload() {}, enginePayloadListeners: new Set(), ENGINE_POLL_TIMEOUT: 1000,
  focusOnShow: () => true, TOAST_LONG: 2, TOAST_SHORT: 1,
  toast: (text, tone) => toasts.push([text, tone]),
  requestAnimationFrame: fn => setTimeout(fn, 0),
  modal: (html, cls, reopen) => {
    const m = document.createElement("div");
    m.className = "modal " + (cls || "");
    m.innerHTML = html;
    document.body.appendChild(m);
    const listeners = [];
    const close = () => { if (!m.isConnected) return; m.remove(); listeners.splice(0).forEach(fn => fn()); };
    const record = { m, close, reopen, cls };
    dialogs.push(record);
    return { m, close, onClose: fn => listeners.push(fn) };
  },
  api: (bid, route, options = {}) => {
    requests.push([bid, route, options.method || "GET", options.body === undefined ? null :
      JSON.parse(JSON.stringify(options.body))]);
    const answer = routes[`${options.method || "GET"} ${route}`];
    if (typeof answer === "function") return answer(options);
    if (answer instanceof Error) return Promise.reject(answer);
    return Promise.resolve(JSON.parse(JSON.stringify(answer || {})));
  },
});
vm.runInContext([
  between("/* Daily schedules are persisted as canonical 24-hour HH:MM", "/* One stamp for"),
  between("function effortOptionsForModel(engine, model) {", "const headWord"),
  between("/* ================= scheduled prompts =================", '$("btn-schedules").onclick'),
  "globalThis.sch = { schedulesPublicState, applySchedules, scheduledRunHidden, scheduleTimingText, " +
    "scheduleTargetText, scheduleStateText, modalSchedules, modalScheduleHistory, modalScheduleEditor, " +
    "scheduleTimesValue, scheduleMonthDaysValue };",
].join("\n"), context);
const sch = vm.runInContext("globalThis.sch", context);
const settle = (ms = 0) => new Promise(resolve => setTimeout(resolve, ms));
const plain = value => JSON.parse(JSON.stringify(value));
const top = () => dialogs.filter(item => item.m.isConnected).pop();
const text = node => node.textContent.replace(/\s+/g, " ").trim();

const schedule = (id, changes = {}) => ({
  id, name: "Log review", enabled: true, backend: 0, engine: "claude", model: "sonnet",
  effort: "high", permission_mode: "bypassPermissions", workspace_kind: "directory",
  cwd: "/var/log", timing: { kind: "daily", times: ["06:00"] }, until: "", max_runs: 0,
  late: "run", limit_minutes: 60, notify: "agent", alert_command: false, keep: 10,
  created_at: 1, updated_at: 1, next_at: 1000, pending_at: 0, scheduled_runs: 2,
  pending_reason: "", excerpt: "Check /var/log for the last 24 hours.", state: "scheduled",
  upcoming: [1000, 2000, 3000], run: { n: 2, trigger: "schedule", due_at: 500, started_at: 500,
    finished_at: 560, backend: 0, session: 41, state: "ok", reason: "", flag: "Disk at 95%",
    kept: true, waiting: false, excerpt: "All quiet" },
  runs: 2, next_run: 3, kept_runs: [1, 2], ...changes,
});
const topic = list => ({ type: "schedules", schedules: list, timezone: "CEST",
  limits: { keep: 50, keep_default: 10, limit_minutes: 1440, limit_default: 60 } });

(async () => {
  /* ---- the timing, as a card words it ---- */
  {
    const t = sch.scheduleTimingText;
    assert.equal(t({ kind: "daily", times: ["06:00", "18:30"] }), "Every day at 06:00 and 18:30");
    assert.equal(t({ kind: "weekly", days: [1, 2, 3, 4, 5], times: ["08:30"] }), "Weekdays at 08:30");
    assert.equal(t({ kind: "weekly", days: [6, 7], times: ["10:00"] }), "Weekends at 10:00");
    assert.equal(t({ kind: "weekly", days: [1, 5], times: ["09:00", "12:00", "17:00"] }),
      "Mondays and Fridays at 09:00, 12:00 and 17:00");
    assert.equal(t({ kind: "monthly", days: [-1, 1, 15], times: ["09:00"] }),
      "Monthly on the 1st, 15th and last day at 09:00");
    assert.equal(t({ kind: "monthly", days: [2, 3, 11, 21, 22], times: ["09:00"] }),
      "Monthly on the 2nd, 3rd, 11th, 21st and 22nd at 09:00");
    assert.equal(t({ kind: "interval", minutes: 30, date: "2026-10-15", time: "08:00" }), "Every 30 minutes");
    assert.equal(t({ kind: "interval", minutes: 120, date: "2026-10-15", time: "08:00" }), "Every 2 hours");
    assert.equal(t({ kind: "interval", minutes: 1440, date: "2026-10-15", time: "08:00" }), "Every 1 day");
    assert.equal(t({ kind: "interval", minutes: 90, date: "2026-10-15", time: "08:00" }), "Every 90 minutes");
    assert.equal(t({ kind: "cron", expr: "0 6 * * 1-5" }), "Cron 0 6 * * 1-5");
    assert.match(t({ kind: "once", date: "2026-10-16", time: "09:00" }), /^Once, .+ at 09:00$/);
    context.state.clockFormat = "12h";
    assert.equal(t({ kind: "daily", times: ["06:00", "18:30"] }), "Every day at 6:00 AM and 6:30 PM");
    assert.deepEqual(plain(sch.scheduleTimesValue("6:30 PM, 6:00 am")), ["06:00", "18:30"]);
    context.state.clockFormat = "24h";
    assert.deepEqual(plain(sch.scheduleTimesValue("18:30, 06:00, 06:00")), ["06:00", "18:30"]);
    assert.equal(sch.scheduleTimesValue("6pm"), null);
    assert.equal(sch.scheduleTimesValue(""), null);
    assert.deepEqual(plain(sch.scheduleMonthDaysValue("15, 1 last")), [-1, 1, 15]);
    assert.equal(sch.scheduleMonthDaysValue("32"), null);
    assert.equal(sch.scheduleTargetText(schedule("a1")), "Studio · Claude Code · Sonnet 5 · High · Bypass");
    assert.equal(sch.scheduleTargetText(schedule("a1", { backend: 7, model: "", effort: "" })),
      "Workshop · Claude Code · Default model · Bypass");
  }

  /* ---- the topic: state, the button, the sidebar's hidden runs ---- */
  {
    assert.equal(sch.scheduledRunHidden({ schedule: { id: "a1", run: 2 } }), false);
    sch.applySchedules(topic([schedule("a1")]));
    assert.equal(context.state.schedules.timezone, "CEST");
    assert.equal(sch.scheduledRunHidden({ schedule: { id: "a1", run: 2 } }), true);
    assert.equal(sch.scheduledRunHidden({ schedule: { id: "a1", run: 3 } }), false);
    assert.equal(sch.scheduledRunHidden({ schedule: { id: "zz", run: 2 } }), false);
    assert.equal(sch.scheduledRunHidden({ id: 4 }), false);
    assert.equal(sidebarRenders, 1);
    sch.applySchedules(topic([schedule("a1")]));
    assert.equal(sidebarRenders, 1, "the same kept runs redraw nothing");
    assert.ok(!button.classList.contains("busy") && !button.classList.contains("attention"));
    assert.equal(button.getAttribute("aria-label"), "Schedules");
    const working = schedule("a1", { state: "working", run: { ...schedule("a1").run, state: "working",
      finished_at: 0, waiting: false } });
    sch.applySchedules(topic([working]));
    assert.ok(button.classList.contains("busy"));
    assert.equal(button.getAttribute("aria-label"), "Schedules · 1 run working");
    sch.applySchedules(topic([{ ...working, run: { ...working.run, waiting: true } }]));
    assert.ok(button.classList.contains("attention") && !button.classList.contains("busy"));
    assert.equal(button.getAttribute("aria-label"), "Schedules · a run is waiting for your approval");
    assert.equal(sch.scheduleStateText({ ...working, run: { ...working.run, waiting: true } }).text,
      "Run 2 is waiting for your approval");
    assert.equal(sch.scheduleStateText(schedule("a1", { state: "waiting", pending_reason: "Workshop is offline" })).text,
      "Due · waiting because Workshop is offline");
    assert.equal(sch.scheduleStateText(schedule("a1", { state: "off" })).text, "Off");
    assert.equal(sch.scheduleStateText(schedule("a1", { state: "ended" })).text, "Ended · nothing left to run");
  }

  /* ---- the sheet ---- */
  {
    sch.applySchedules(topic([schedule("a1"), schedule("b2", { name: "Disk check", enabled: false,
      state: "off", next_at: 0, run: null, kept_runs: [] })]));
    const { m } = sch.modalSchedules();
    const cards = () => [...m.querySelectorAll(".schedule-card")];
    assert.deepEqual(cards().map(card => card.dataset.id), ["a1", "b2"]);
    const first = cards()[0];
    assert.equal(text(first.querySelector(".schedule-name")), "Log review");
    assert.equal(text(first.querySelector(".schedule-when")), "Next stamp 1000");
    assert.equal(text(first.querySelectorAll(".schedule-card-line")[0]), "Every day at 06:00");
    assert.equal(text(first.querySelector(".schedule-card-prompt")), "Check /var/log for the last 24 hours.");
    assert.equal(text(first.querySelector(".schedule-card-last")), "Last run 2 · stamp 500 · Finished · Disk at 95%");
    assert.ok(first.querySelector(".schedule-card-detail").classList.contains("warn"));
    assert.deepEqual(cards()[1].querySelectorAll("button").map(text),
      ["Run now", "History", "Edit", "Duplicate", "Delete"]);
    assert.equal(cards()[1].querySelector("input").checked, false);
    assert.ok(m.querySelector(".schedule-empty").classList.contains("hidden"));
    // the switch, Run now
    routes["PATCH schedules/b2"] = { schedule: {} };
    const input = cards()[1].querySelector("input");
    input.checked = true;
    input.onchange();
    await settle();
    assert.deepEqual(requests.pop(), [0, "schedules/b2", "PATCH", { enabled: true }]);
    routes["POST schedules/a1/run"] = { run: { n: 3 } };
    first.querySelector('[data-act="run"]').click();
    await settle();
    assert.deepEqual(requests.pop().slice(0, 3), [0, "schedules/a1/run", "POST"]);
    assert.deepEqual(toasts.pop(), ["Log review: Run 3 started", "busy"]);
    routes["POST schedules/a1/run"] = Object.assign(new Error("A run of this schedule is still working"), { status: 409 });
    first.querySelector('[data-act="run"]').click();
    await settle();
    assert.deepEqual(toasts.pop(), ["Log review: Could not start a run · A run of this schedule is still working", "bad"]);
    // an unchanged card stays the same node; a changed one is redrawn where it stood
    const before = cards()[1];
    sch.applySchedules(topic([schedule("a1", { state: "working", run: { ...schedule("a1").run, n: 3,
      state: "working", finished_at: 0, session: 50, kept: true } }), schedule("b2", { name: "Disk check",
      enabled: false, state: "off", next_at: 0, run: null, kept_runs: [] })]));
    assert.equal(cards()[1], before);
    assert.deepEqual(cards()[0].querySelectorAll("button").map(text), ["Stop", "History", "Edit", "Duplicate", "Delete"]);
    assert.equal(text(cards()[0].querySelector(".schedule-when")), "Run 3 working since stamp 500");
    assert.ok(cards()[0].querySelector(".spinner"));
    // a working run this console knows as that run can be opened
    context.sessions["0:50"] = { id: 50, schedule: { id: "a1", run: 3 } };
    sch.applySchedules(topic([schedule("a1", { state: "working", kept_runs: [1, 2, 3], run: {
      ...schedule("a1").run, n: 3, state: "working", finished_at: 0, session: 50, kept: true } })]));
    assert.deepEqual(cards().map(card => card.dataset.id), ["a1"]);
    assert.deepEqual(cards()[0].querySelectorAll("button").map(text),
      ["Stop", "Open", "History", "Edit", "Duplicate", "Delete"]);
    routes["POST schedules/a1/stop"] = { run: { n: 3 } };
    cards()[0].querySelector('[data-act="stop"]').click();
    await settle();
    assert.deepEqual(toasts.pop(), ["Log review: Stopping run 3", "busy"]);
    cards()[0].querySelector('[data-act="open"]').click();
    assert.deepEqual(plain(opened.pop()), [0, 50, { id: 50, schedule: { id: "a1", run: 3 } }]);
    assert.ok(!m.isConnected, "opening a run's conversation closes the sheets over it");
    // an empty list says so
    sch.applySchedules(topic([]));
    const again = sch.modalSchedules().m;
    assert.ok(!again.querySelector(".schedule-empty").classList.contains("hidden"));
    dialogs.pop().close();
  }

  /* ---- deleting ---- */
  {
    sch.applySchedules(topic([schedule("a1")]));
    const { m } = sch.modalSchedules();
    routes["DELETE schedules/a1?conversations=1"] = { ok: true, removed: 2, left: 0 };
    m.querySelector('[data-act="delete"]').click();
    const confirm = top();
    assert.equal(text(confirm.m.querySelector(".schedule-delete-conversations")),
      "Also delete its 2 kept conversations");
    confirm.m.querySelector("#sd-yes").onclick();
    await settle();
    assert.deepEqual(requests.pop().slice(0, 3), [0, "schedules/a1?conversations=1", "DELETE"]);
    assert.deepEqual(toasts.pop(), ["Schedule deleted", "ok"]);
    assert.ok(!confirm.m.isConnected);
    routes["DELETE schedules/a1?conversations=0"] = { ok: true, removed: 0, left: 0 };
    m.querySelector('[data-act="delete"]').click();
    const second = top();
    second.m.querySelector("#sd-conversations").checked = false;
    second.m.querySelector("#sd-yes").onclick();
    await settle();
    assert.equal(requests.pop()[1], "schedules/a1?conversations=0");
    routes["DELETE schedules/a1?conversations=1"] = Object.assign(new Error("A run of this schedule is still working · stop it first"), { status: 409 });
    m.querySelector('[data-act="delete"]').click();
    const failing = top();
    failing.m.querySelector("#sd-yes").onclick();
    await settle();
    assert.ok(failing.m.isConnected);
    assert.equal(text(failing.m.querySelector(".form-error")),
      "Could not delete the schedule · A run of this schedule is still working · stop it first");
    failing.close();
    dialogs.filter(item => item.m.isConnected).forEach(item => item.close());
  }

  /* ---- the history ---- */
  {
    context.sessions["0:41"] = { id: 41, schedule: { id: "a1", run: 2 } };
    context.sessions["0:40"] = { id: 40, schedule: { id: "other", run: 1 } };
    sch.applySchedules(topic([schedule("a1")]));
    routes["GET schedules/a1"] = { schedule: schedule("a1"), runs: [
      { n: 2, trigger: "schedule", due_at: 500, started_at: 500, finished_at: 560, backend: 0, session: 41,
        state: "ok", reason: "", flag: "Disk at 95%", kept: true, waiting: false, answer: "## All quiet" },
      { n: 1, trigger: "manual", due_at: 0, started_at: 100, finished_at: 130, backend: 0, session: 40,
        state: "failed", reason: "the disk is unreadable", flag: "", kept: true, waiting: false, answer: "" },
    ] };
    const { m } = sch.modalScheduleHistory("a1");
    await settle(5);
    assert.equal(text(m.querySelector("h2")), "Log review");
    const facts = [...m.querySelectorAll(".ws-fact")].map(row => [text(row.querySelector(".field-lbl")),
      text(row.querySelector(".wsf-v"))]);
    assert.deepEqual(facts, [["State", "Next stamp 1000"], ["Repeats", "Every day at 06:00"],
      ["Then", "stamp 2000 · stamp 3000"], ["Runs on", "Studio · Claude Code · Sonnet 5 · High · Bypass"],
      ["Directory", "/var/log"], ["Notify", "When the agent flags something"]]);
    const runs = [...m.querySelectorAll(".schedule-run")];
    assert.equal(runs.length, 2);
    assert.equal(text(runs[0].querySelector(".sr-state")), "Finished · 60s");
    assert.equal(text(runs[0].querySelector(".sr-when")), "stamp 500 · On time");
    assert.equal(text(runs[0].querySelector(".sr-flag")), "Disk at 95%");
    assert.equal(text(runs[1].querySelector(".sr-when")), "stamp 100 · Started by hand");
    assert.equal(text(runs[1].querySelector(".sr-reason")), "the disk is unreadable");
    assert.ok(runs[1].querySelector(".sr-reason").classList.contains("bad"));
    assert.equal(text(m.querySelector(".schedule-runs-section .field-optional")), "· 2");
    // the run this console knows as that run opens; another schedule's session never does
    assert.deepEqual(runs[0].querySelectorAll("button").map(text), ["Open conversation", "Delete"]);
    assert.deepEqual(runs[1].querySelectorAll("button").map(text), ["Delete"]);
    routes["DELETE schedules/a1/runs/1"] = { ok: true };
    runs[1].querySelectorAll("button")[0].onclick();
    await settle(5);
    assert.ok(requests.some(item => item[1] === "schedules/a1/runs/1" && item[2] === "DELETE"));
    runs[0].querySelector("button").onclick();
    assert.deepEqual(plain(opened.pop()).slice(0, 2), [0, 41]);
    assert.ok(!m.isConnected);
    // a schedule gone from the topic closes its history
    const second = sch.modalScheduleHistory("a1").m;
    await settle(5);
    sch.applySchedules(topic([]));
    assert.ok(!second.isConnected);
  }

  /* ---- the editor ---- */
  {
    sch.applySchedules(topic([]));
    const previews = [];
    routes["POST schedules/preview"] = options => {
      previews.push(plain(options.body));
      return Promise.resolve({ next: [1000, 2000, 3000, 4000], timezone: "CEST" });
    };
    sch.modalScheduleEditor();
    const editor = top();
    const $$ = selector => editor.m.querySelector(selector);
    await settle(300);
    assert.equal($$("h2").textContent, "New schedule");
    assert.equal($$("#sc-kind").value, "daily");
    assert.equal($$("#sc-times").value, "06:00");
    assert.equal($$("#sc-cwd").value, "/home/mira");
    assert.equal($$("#sc-limit").value, "60");
    assert.equal($$("#sc-keep").value, "10");
    assert.equal($$("#sc-enabled").checked, true);
    assert.equal($$("#sc-engine").value, "claude");
    assert.equal($$("#sc-perm").value, "default");
    assert.deepEqual($$("#sc-backend").querySelectorAll("option").map(option =>
      [option.value, text(option), option.disabled]),
      [["0", "Studio", false], ["7", "Workshop", false], ["8", "Attic (needs a Puppy upgrade)", true]]);
    assert.deepEqual(previews.pop(), { timing: { kind: "daily", times: ["06:00"] }, until: "", max_runs: 0 });
    assert.equal(text($$("#sc-preview")), "Next: stamp 1000 · stamp 2000 · stamp 3000 · times are in this instance's time zone, CEST");
    // the kinds show their own fields
    const shown = cls => !editor.m.querySelector(cls).classList.contains("hidden");
    $$("#sc-kind").value = "weekly";
    $$("#sc-kind").dispatchEvent(new FakeEvent("change"));
    await settle(300);
    assert.ok(shown("#sc-days") || shown(".sc-weekly"));
    assert.equal(previews.pop().timing.kind, "weekly");
    // refusals stay on the form
    const submit = async () => { $$(".schedule-form").onsubmit(new FakeEvent("submit")); await settle(5); };
    await submit();
    assert.equal(text($$(".form-error")), "Give the schedule a name");
    $$("#sc-name").value = "Log review";
    await submit();
    assert.equal(text($$(".form-error")), "Enter the prompt the schedule sends");
    FakeComposer.last.set("Check /var/log for the last 24 hours.");
    $$("#sc-times").value = "six";
    await submit();
    assert.equal(text($$(".form-error")), "Use times like 03:30, separated by commas");
    $$("#sc-times").value = "08:30, 17:00";
    for (const button of editor.m.querySelectorAll("#sc-days button")) button.setAttribute("aria-pressed", "false");
    await submit();
    assert.equal(text($$(".form-error")), "Choose the days of the week it runs on");
    editor.m.querySelector('#sc-days [data-day="1"]').onclick();
    editor.m.querySelector('#sc-days [data-day="5"]').onclick();
    $$("#sc-ends").value = "count";
    $$("#sc-count").value = "4";
    $$("#sc-notify").value = "agent";
    $$("#sc-alert").checked = true;
    $$("#sc-limit").value = "30";
    routes["POST schedules"] = { schedule: { name: "Log review", next_at: 1000 } };
    await submit();
    const [, route, method, body] = requests.filter(item => item[1] === "schedules").pop();
    assert.equal(route, "schedules");
    assert.equal(method, "POST");
    assert.deepEqual(body, {
      name: "Log review", prompt: "Check /var/log for the last 24 hours.", enabled: true, backend: 0,
      engine: "claude", model: "", effort: "", permission_mode: "default", workspace_kind: "directory",
      cwd: "/home/mira", timing: { kind: "weekly", days: [1, 5], times: ["08:30", "17:00"] },
      until: "", max_runs: 4, late: "run", limit_minutes: 30, notify: "agent", alert_command: true, keep: 10 });
    assert.ok(!editor.m.isConnected);
    assert.deepEqual(toasts.pop(), ["Log review: Scheduled · next run stamp 1000", "ok"]);
  }
  {
    // every other kind's body, on a backend with a scratch workspace
    const kinds = [
      ["once", e => { e.querySelector("#sc-date").value = "2026-10-16"; e.querySelector("#sc-time").value = "9:05"; },
        { kind: "once", date: "2026-10-16", time: "09:05" }],
      ["interval", e => { e.querySelector("#sc-every").value = "2"; e.querySelector("#sc-unit").value = "hours";
        e.querySelector("#sc-from-date").value = "2026-10-15"; e.querySelector("#sc-from-time").value = "08:00"; },
        { kind: "interval", minutes: 120, date: "2026-10-15", time: "08:00" }],
      ["monthly", e => { e.querySelector("#sc-mdays").value = "last, 1"; e.querySelector("#sc-times").value = "09:00"; },
        { kind: "monthly", days: [-1, 1], times: ["09:00"] }],
      ["cron", e => { e.querySelector("#sc-cron").value = " 0 6 * * 1-5 "; },
        { kind: "cron", expr: "0 6 * * 1-5" }],
    ];
    for (const [kind, fill, timing] of kinds) {
      sch.modalScheduleEditor();
      const editor = top();
      await settle(5);
      editor.m.querySelector("#sc-name").value = "Run " + kind;
      FakeComposer.last.set("Do the " + kind + " thing");
      editor.m.querySelector("#sc-backend").value = "7";
      editor.m.querySelector("#sc-backend").onchange();
      editor.m.querySelector('#sc-workspace [data-kind="temporary"]').onclick();
      editor.m.querySelector("#sc-kind").value = kind;
      fill(editor.m);
      editor.m.querySelector(".schedule-form").onsubmit(new FakeEvent("submit"));
      await settle(5);
      const body = requests.filter(item => item[1] === "schedules").pop()[3];
      assert.deepEqual(body.timing, timing, kind);
      assert.equal(body.backend, 7);
      assert.equal(body.workspace_kind, "temporary");
      assert.equal(body.cwd, "");
      assert.equal(body.until, "");
      assert.equal(body.max_runs, 0);
      assert.ok(!editor.m.isConnected, kind);
    }
  }
  {
    // a refusal from the controller stays inline, the form usable again
    routes["POST schedules"] = Object.assign(new Error("Cron hour field: 25 is outside 0–23"), { status: 400 });
    sch.modalScheduleEditor();
    const editor = top();
    await settle(5);
    editor.m.querySelector("#sc-name").value = "Bad";
    FakeComposer.last.set("x");
    editor.m.querySelector("#sc-kind").value = "cron";
    editor.m.querySelector("#sc-cron").value = "0 25 * * *";
    editor.m.querySelector(".schedule-form").onsubmit(new FakeEvent("submit"));
    await settle(5);
    assert.ok(editor.m.isConnected);
    assert.equal(text(editor.m.querySelector(".form-error")), "Could not save the schedule · Cron hour field: 25 is outside 0–23");
    assert.equal(editor.m.querySelector("#sc-save").disabled, false);
    assert.equal(FakeComposer.last.busy, false);
    // the preview says what the instance refuses
    routes["POST schedules/preview"] = () => Promise.reject(Object.assign(new Error("Cron hour field: 25 is outside 0–23"), { status: 400 }));
    editor.m.querySelector("#sc-cron").dispatchEvent(new FakeEvent("input"));
    await settle(300);
    assert.equal(text(editor.m.querySelector("#sc-preview")), "Cron hour field: 25 is outside 0–23");
    assert.ok(editor.m.querySelector("#sc-preview").classList.contains("bad"));
    editor.close();
  }
  {
    // an edit and a copy read the full schedule back from the controller
    routes["POST schedules/preview"] = { next: [], timezone: "CEST" };
    sch.applySchedules(topic([schedule("a1")]));
    routes["GET schedules/a1"] = { schedule: { ...schedule("a1"), prompt: "Check /var/log for the last 24 hours.",
      timing: { kind: "monthly", days: [-1, 15], times: ["09:00", "21:00"] }, until: "2026-12-31" }, runs: [] };
    await sch.modalScheduleEditor("a1");
    const editor = top();
    assert.equal(editor.m.querySelector("h2").textContent, "Edit schedule");
    assert.equal(editor.m.querySelector("#sc-name").value, "Log review");
    assert.equal(FakeComposer.last.text, "Check /var/log for the last 24 hours.");
    assert.equal(editor.m.querySelector("#sc-kind").value, "monthly");
    assert.equal(editor.m.querySelector("#sc-mdays").value, "last, 15");
    assert.equal(editor.m.querySelector("#sc-times").value, "09:00, 21:00");
    assert.equal(editor.m.querySelector("#sc-ends").value, "date");
    assert.equal(editor.m.querySelector("#sc-until").value, "2026-12-31");
    assert.equal(editor.m.querySelector("#sc-model").value, "sonnet");
    assert.equal(editor.m.querySelector("#sc-effort").value, "high");
    assert.equal(editor.m.querySelector("#sc-perm").value, "bypassPermissions");
    assert.equal(editor.m.querySelector("#sc-cwd").value, "/var/log");
    await settle(300);
    assert.equal(text(editor.m.querySelector("#sc-preview")), "Nothing left to run with these times · times are in this instance's time zone, CEST");
    routes["PUT schedules/a1"] = { schedule: { name: "Log review", next_at: 0 } };
    editor.m.querySelector(".schedule-form").onsubmit(new FakeEvent("submit"));
    await settle(5);
    const put = requests.filter(item => item[1] === "schedules/a1" && item[2] === "PUT").pop();
    assert.deepEqual(put[3].timing, { kind: "monthly", days: [-1, 15], times: ["09:00", "21:00"] });
    assert.equal(put[3].until, "2026-12-31");
    assert.deepEqual(toasts.pop(), ["Log review: Saved", "ok"]);
    await sch.modalScheduleEditor(null, "a1");
    const copy = top();
    assert.equal(copy.m.querySelector("h2").textContent, "New schedule");
    assert.equal(copy.m.querySelector("#sc-name").value, "Log review (copy)");
    routes["POST schedules"] = { schedule: { name: "Log review (copy)", next_at: 0 } };
    copy.m.querySelector(".schedule-form").onsubmit(new FakeEvent("submit"));
    await settle(5);
    assert.equal(requests.filter(item => item[1] === "schedules").pop()[3].name, "Log review (copy)");
  }
  console.log("schedules UI tests passed");
})().catch(error => { console.error(error); process.exit(1); });
