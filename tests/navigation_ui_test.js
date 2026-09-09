/* The real history controller against an asynchronous browser history stack.
   Run with node tests/navigation_ui_test.js; no browser, server or engines. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const context = vm.createContext({ Date, Math, Map, Set, Promise });
vm.runInContext(fs.readFileSync(require("node:path").join(__dirname,
  "../puppy/static/navigation.js"), "utf8") + "\nthis.ConsoleHistory = ConsoleHistory;", context);
const { ConsoleHistory } = context;
const tick = () => new Promise(resolve => setImmediate(resolve));
const route = (tab = "s:0:1", task = 1, seq = 0, search = 0) => ({ tab, task, seq, search, settings: [0, 0, 0] });

function fixture() {
  const stack = [null]; let cursor = 0, listener;
  let view = route(), memory = { top: 0 }, errors = 0, external = 0;
  const browser = {
    addEventListener: (kind, callback) => { listener = callback; },
    history: {
      get state() { return stack[cursor]; },
      pushState(value) { stack.splice(++cursor); stack.push(structuredClone(value)); },
      replaceState(value) { stack[cursor] = structuredClone(value); },
      go(delta) {
        setImmediate(() => {
          const target = cursor + delta;
          if (target < 0 || target >= stack.length) return;
          cursor = target;
          listener({ state: structuredClone(stack[cursor]) });
        });
      },
    },
  };
  const history = new ConsoleHistory({
    read: () => view, apply: (value, saved) => { view = value; if (saved) memory = saved; },
    capture: () => memory, url: () => "/", dismissMenus() {},
    external: () => external++, error: () => errors++,
  }, browser);
  history.start();
  const move = async delta => { browser.history.go(delta); await tick(); await tick(); await tick(); };
  return { history, browser, stack, move, get view() { return view; },
    get cursor() { return cursor; }, get memory() { return memory; },
    get errors() { return errors; }, get external() { return external; },
    navigate(value) { history.remember(); view = value; history.changed(); },
    scroll(top) { memory = { top }; history.remember(); },
  };
}

(async () => {
  let f = fixture();
  assert.equal(f.stack.length, 1, "boot replaces; it never traps Back behind a sentinel");
  f.navigate(route("settings", 0)); f.navigate(route("search", 0)); await tick();
  assert.equal(f.stack.length, 2, "one compound action makes one entry");
  f.navigate(route("search", 0)); await tick();
  assert.equal(f.stack.length, 2, "re-selecting the active destination does not stack");
  await f.move(-1); assert.equal(f.view.tab, "s:0:1");
  await f.move(1); assert.equal(f.view.tab, "search");
  await f.move(-1); f.navigate(route("settings", 0)); await tick();
  assert.equal(f.stack.length, 2); assert.equal(f.view.tab, "settings");

  f = fixture(); f.scroll(281);
  f.navigate(route("s:0:1", 7)); await tick();
  f.navigate(route("s:0:1", 7, 500)); await tick();
  await f.move(-2);
  assert.equal(f.view.task, 1); assert.equal(f.memory.top, 281);
  await f.move(2); assert.equal(f.view.seq, 500);

  f = fixture(); let layers = [], cleanups = 0;
  function open(name, reopen = true) {
    const row = { name, close: null };
    const release = f.history.layer(() => row.close(), reopen ? () => open(name) : null);
    row.close = () => { layers = layers.filter(item => item !== row); release(); cleanups++; };
    layers.push(row); return row;
  }
  open("editor"); await tick(); open("nested"); await tick();
  await f.move(-1); assert.deepEqual(layers.map(row => row.name), ["editor"]);
  await f.move(1); assert.deepEqual(layers.map(row => row.name), ["editor", "nested"]);
  await f.move(-2); assert.equal(layers.length, 0); assert.equal(cleanups, 3);
  await f.move(2); assert.deepEqual(layers.map(row => row.name), ["editor", "nested"]);
  layers[1].close(); await tick(); await tick();
  assert.equal(f.cursor, 1, "Close consumes its entry instead of adding a closed copy");
  await f.move(1); assert.equal(layers.length, 2, "Forward also reopens an explicitly closed surface");
  layers[1].close(); await tick(); await tick();
  layers[0].close(); f.navigate(route("settings", 0)); await tick(); await tick(); await tick();
  assert.equal(f.view.tab, "settings");
  await f.move(-1); assert.equal(f.view.tab, "s:0:1"); assert.equal(layers.length, 0);

  f = fixture(); layers = [];
  open("confirm", false); await tick();
  await f.move(-1); await f.move(1);
  assert.equal(layers.length, 0, "Forward never revives a resolved transaction");

  f = fixture(); let cancelling = 0;
  const finish = f.history.layer(() => cancelling++);
  await tick(); await f.move(-1);
  assert.equal(cancelling, 1); assert.equal(f.cursor, 1);
  assert.equal(f.history.layers.length, 1, "cancellation owns its surface until cleanup");
  finish(); await tick(); await tick();
  assert.equal(f.cursor, 0); assert.equal(f.history.layers.length, 0);

  f = fixture();
  for (let i = 1; i <= 160; i++) { f.navigate(route("s:0:1", 1, i)); await tick(); }
  assert.equal(f.history.entries.size, 128);
  await f.move(-159); assert.equal(f.view.seq, 1, "eviction retains base navigation");
  assert.equal(f.errors, 0);
  const valid = f.browser.history.state;
  assert.ok(ConsoleHistory.valid(valid));
  for (const value of [null, {}, { ...valid, v: 0 }, { ...valid, extra: true },
    { ...valid, layers: [1, 1] }, { ...valid, route: { ...valid.route, seq: -1 } },
    { ...valid, route: { ...valid.route, tab: "javascript:alert(1)" } }])
    assert.equal(!!ConsoleHistory.valid(value), false);
  f.browser.history.replaceState({ v: 0 });
  f.history.start();
  assert.deepEqual(f.browser.history.state, { v: 0 }, "invalid saved state is refused without rewriting it");
  assert.equal(f.errors, 1);
  f = fixture();
  f.navigate(route("settings", 0)); await tick();
  f.navigate({ ...route("settings", 0), settings: [9, 0, 0] }); await tick();
  await f.move(-1); assert.deepEqual(f.view.settings, [0, 0, 0]);
  await f.move(1); assert.deepEqual(f.view.settings, [9, 0, 0]);
  f.browser.history.go(-1); f.browser.history.go(-1); await tick(); await tick();
  assert.equal(f.view.tab, "s:0:1", "rapid traversal lands at the newest requested entry");
  console.log("PASS: browser history batching, Back/Forward, tasks and message locations, scroll, nested dialog cleanup/reopening, consumed closes, close-and-navigate races, cancellation, bounded memory and strict state");
})().catch(error => { console.error(error); process.exitCode = 1; });
