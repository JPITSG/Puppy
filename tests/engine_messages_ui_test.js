/* Readable engine notices through the actual session receiver and toast/history
   path. No browser, native engine or subscription quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { FakeDocument } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
function between(from, to) {
  const a = source.indexOf(from), b = source.indexOf(to, a);
  assert(a >= 0 && b > a, from);
  return source.slice(a, b);
}
const document = new FakeDocument(), host = document.createElement("div");
host.id = "toasts";
document.body.appendChild(host);
const reports = [], stamps = [];
const context = vm.createContext({
  document, Date, console,
  setTimeout: () => 1, clearTimeout: () => {},
  api: async (_bid, _route, options) => { reports.push(options.body); return {type: "notices", items: []}; },
  ingestNoticesPayload: () => {},
  engineInfo: (bid, key) => bid === 7 && key === "fixture" ? {label: "Example engine"} : null,
  fmtStamp: value => { stamps.push(value); return value < 8640000000000 ? "Sep 26, 09:00" : ""; },
});
vm.runInContext([
  between("const $ = (id)", "/* Close buttons"),
  between("const TOAST_SWIPE_INTENT_PX", "/* Clipboard.writeText is unavailable"),
  between("/* Engine adapters own protocol meanings", "/* ================= notifications ================= */"),
  "function receive(d) { switch (d.type) {",
  between('      case "rate_limit": {', '      case "toast":'),
  "} }",
].join("\n"), context);
const receive = data => context.receive.call({tab: {bid: 7}}, data);
const lines = () => host.querySelectorAll(".toast-text").map(n => n.textContent);
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

(async () => {
  const reset = Date.now() / 1000 + 86400;
  receive({type: "rate_limit", engine: "fixture", info: {status: "allowed_warning"}, notice: {
    text: "Approaching your weekly usage limit · 80% used", tone: "warn", resets_at: reset,
  }});
  await tick();
  assert.equal(lines()[0], "Example engine: Approaching your weekly usage limit · 80% used · resets Sep 26, 09:00");
  assert.equal(reports[0].text, lines()[0], "history gets the readable text, including the formatted reset");
  assert.deepEqual(stamps, [reset]);
  receive({type: "rate_limit", engine: "fixture", info: {status: "allowed_warning"}, notice: null});
  receive({type: "rate_limit", engine: "fixture", info: {status: "allowed"}});
  receive({type: "rate_limit", engine: "fixture", info: {primary: {usedPercent: 95}}});
  assert.equal(lines().length, 1, "quiet samples never become warnings");
  // Mixed-version nodes still produce readable text, with no guessed meaning.
  receive({type: "rate_limit", engine: "fixture", info: {status: "future_code"}});
  assert.equal(lines()[1], "Example engine: Usage status changed");
  assert(!lines().join(" ").includes("future_code"));
  receive({type: "engine_notice", engine: "fixture", notice: {text: "Connection restored", tone: "ok"}});
  receive({type: "engine_notice", engine: "fixture", notice: {text: "Connection restored", tone: "ok"}});
  assert.equal(lines().length, 3, "the shared toast repeats in place");
  assert.equal(host.children[2].querySelector(".toast-count").textContent, "2 ×");
  receive({type: "engine_notice", engine: "future_internal_engine_id", notice: {
    text: "<img src=x onerror=alert(1)>", tone: "arbitrary_vendor_color", resets_at: "tomorrow",
  }});
  assert.equal(lines()[3], "Engine: <img src=x onerror=alert(1)>");
  assert.equal(host.querySelectorAll("img").length, 0, "vendor prose stays text");
  assert(host.children[3].classList.contains("info"));
  for (const resets_at of [0, true, "1900000000", Infinity, NaN, Date.now() / 1000 - 10])
    receive({type: "engine_notice", engine: "fixture", notice: {text: "Reset not reported", resets_at}});
  assert.deepEqual(stamps, [reset], "missing, malformed and past reset times do not imply recovery");
  const count = lines().length;
  for (const notice of [null, {}, {text: {}}, {text: "  "}])
    receive({type: "engine_notice", engine: "fixture", notice});
  assert.equal(lines().length, count);
  await tick();
  assert(reports.every(row => !row.text.includes("allowed_warning")));
  console.log("PASS: native notices and legacy fallbacks use engine labels, shared clock formatting, safe text, toast tones and notification history");
})().catch(error => { console.error(error); process.exitCode = 1; });
