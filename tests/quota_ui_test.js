"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const context = vm.createContext({Date, Map, JSON, fmtDateTime: value => String(value)});
vm.runInContext(source.slice(source.indexOf("const QUOTA_LOW_PERCENT ="),
                             source.indexOf("const TIMEOUT_FIELDS =")), context);
const now = 1000;
function engine(used, at = 990, extra = {}) {
  return {key: "codex", installed: true, auth: "ok", version: "1", update_available: false,
    usage_monitor: {version: 1, provider: "provider-a", account: "a".repeat(64),
      bucket: "all-model-week", window_minutes: 10080,
      sample: used === null ? null : {used_percent: used, resets_at: 2000, observed_at: at}, ...extra}};
}
function group(name, engines, extra = {}) {
  return {name, engines, sharedUsage: true, offline: false, ...extra};
}
const local = engine(42, 950), peer = engine(44), empty = engine(null);
peer.version = "2"; peer.update_available = true;
const groups = [group("Studio", [local]), group("Workshop", [peer]), group("Laptop", [empty])];
const before = JSON.stringify(groups);
let samples = context.sharedWeeklyQuotas(groups, now);
assert.equal(samples.get(local), samples.get(peer));
assert.equal(samples.get(empty), samples.get(peer));
assert.equal(context.weeklyQuotaLeft(samples.get(local)), 56);
assert.equal(JSON.stringify(groups), before, "only quota presentation may change");
assert.match(context.quotaTitle(local, samples.get(local)), /reported 990 · reading from Workshop/);

// Late delivery cannot override a newer observation; later measurements can
// decrease or increase, including around reset. Never sum percentages.
local.usage_monitor.sample = {used_percent: 20, observed_at: 900, resets_at: 2000};
samples = context.sharedWeeklyQuotas(groups, now);
assert.equal(samples.get(local).usedPercent, 44);
local.usage_monitor.sample = {used_percent: 3, observed_at: 999, resets_at: 3000};
samples = context.sharedWeeklyQuotas(groups, now);
assert.equal(samples.get(peer).usedPercent, 3);

for (const override of [{account: "b".repeat(64)}, {provider: "provider-b"},
                        {bucket: "model-specific-week"}, {account: null}]) {
  const independent = engine(90, 999, override);
  samples = context.sharedWeeklyQuotas([group("A", [peer]), group("B", [independent])], now);
  assert.equal(samples.get(peer).usedPercent, 44);
  assert.equal(samples.get(independent).usedPercent, 90);
}
// Multiple engines may share ONLY when their full declared scope agrees.
const otherEngine = engine(null); otherEngine.key = "another-driver";
samples = context.sharedWeeklyQuotas([group("A", [peer, otherEngine])], now);
assert.equal(samples.get(otherEngine).usedPercent, 44);

for (const condition of [{offline: true}, {sharedUsage: false}]) {
  samples = context.sharedWeeklyQuotas([group("A", [empty]), group("B", [peer], condition)], now);
  assert.equal(samples.get(empty), null);
}
const signedOut = engine(90, 999); signedOut.auth = "missing";
samples = context.sharedWeeklyQuotas([group("A", [peer]), group("B", [signedOut])], now);
assert.equal(samples.get(peer).usedPercent, 44);
assert.equal(samples.get(signedOut).usedPercent, 90);

for (const raw of [{used_percent: NaN, observed_at: 990, resets_at: 2000},
                   {used_percent: 99, observed_at: 990, resets_at: 900},
                   {used_percent: 99, observed_at: 2000, resets_at: 3000},
                   {used_percent: -1, observed_at: 990, resets_at: 2000},
                   {used_percent: "99", observed_at: 990, resets_at: 2000}]) {
  const bad = engine(99, 999, {sample: raw});
  samples = context.sharedWeeklyQuotas([group("A", [empty]), group("B", [bad])], now);
  assert.equal(samples.get(empty), null);
}
const noReset = engine(99, 999, {sample: {used_percent: 99, observed_at: 999, resets_at: null}});
samples = context.sharedWeeklyQuotas([group("A", [empty]), group("B", [noReset])], now);
assert.equal(samples.get(empty), null);
assert.equal(samples.get(noReset).usedPercent, 99);

// Restart/login changes publish an empty new monitor. Old persisted rows must
// not be attached to the current identity or resurrected through fallback.
const reset = engine(null, 999, {account: "b".repeat(64)});
reset.quota = {weekly_used_percent: 99};
samples = context.sharedWeeklyQuotas([group("A", [peer]), group("B", [reset])], now);
assert.equal(samples.get(reset), null);
const legacy = {key: "codex", quota: {weekly_used_percent: 77}};
samples = context.sharedWeeklyQuotas([group("A", [empty]), group("Old node", [legacy])], now);
assert.equal(samples.get(legacy).usedPercent, 77);
assert.equal(samples.get(empty), null);
// A contributor going offline or changing its login must not roll back the
// latest valid observation for other entries still using its former account.
const cache = new Map(), older = engine(12, 900), latest = engine(44, 990);
context.sharedWeeklyQuotas([group("A", [older]), group("B", [latest])], now, cache);
samples = context.sharedWeeklyQuotas([group("A", [older]), group("B", [latest], {offline: true})], now, cache);
assert.equal(samples.get(older).usedPercent, 44);
latest.usage_monitor.account = "b".repeat(64);
latest.usage_monitor.sample = null;
samples = context.sharedWeeklyQuotas([group("A", [older]), group("B", [latest])], now, cache);
assert.equal(samples.get(older).usedPercent, 44);
assert.equal(samples.get(latest), null);
samples = context.sharedWeeklyQuotas([group("A", [older])], 2001, cache);
assert.equal(samples.get(older), null);
assert.equal(cache.size, 0);
console.log("PASS: shared percentages, exact buckets, isolation, freshness and legacy nodes");
