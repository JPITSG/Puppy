/* Browser history for the console. The adapter restores views; it must never
   replay a command. Private view data and dialog factories stay in this page's
   bounded memory, never in URLs, history.state, or browser storage. */
"use strict";

class ConsoleHistory {
  constructor(adapter, browser = window) {
    this.adapter = adapter;
    this.browser = browser;
    this.entries = new Map();
    this.layers = [];
    this.factories = new Map();
    this.serial = Date.now() * 1000 + Math.floor(Math.random() * 1000);
    this.current = null;
    this.applying = false;
    this.pending = false;
    this.scheduled = false;
    this.restoringLayer = null;
    this.revision = 0;
    browser.addEventListener("popstate", event => this.pop(event.state));
  }

  static route(value) {
    return value && Object.keys(value).sort().join() === "search,seq,settings,tab,task" &&
      typeof value.tab === "string" && /^(?:|settings|search|s:\d+:\d+|t:\d+:\d+|b:\d+(?::[A-Z0-9]{4})?|v:\d+:[A-Z0-9]{4})$/.test(value.tab) &&
      [value.task, value.seq, value.search].every(n => Number.isSafeInteger(n) && n >= 0) &&
      Array.isArray(value.settings) && value.settings.length === 3 &&
      value.settings.every(n => Number.isSafeInteger(n) && n >= 0);
  }

  static valid(value) {
    return value && Object.keys(value).sort().join() === "index,layers,page,route,v" &&
      value.v === 1 && typeof value.page === "string" && /^[a-z0-9-]{1,80}$/.test(value.page) &&
      Number.isSafeInteger(value.index) && value.index >= 0 && ConsoleHistory.route(value.route) &&
      Array.isArray(value.layers) && value.layers.length <= 32 &&
      value.layers.every(id => Number.isSafeInteger(id) && id > 0) &&
      new Set(value.layers).size === value.layers.length;
  }

  same(a, b) {
    return JSON.stringify(a.route) === JSON.stringify(b.route) &&
      JSON.stringify(a.layers) === JSON.stringify(b.layers);
  }

  read() { return { route: this.adapter.read(), layers: this.layers.map(layer => layer.id) }; }

  start(route = null) {
    const saved = this.browser.history.state;
    const valid = ConsoleHistory.valid(saved);
    if (saved != null && !valid) {
      this.adapter.error(new Error("Invalid browser history entry"));
      return;
    }
    this.page = valid ? saved.page : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
    this.current = { v: 1, page: this.page, index: valid ? saved.index : 0,
      route: route || (valid ? saved.route : this.adapter.read()), layers: [] };
    this.applying = true;
    try { this.adapter.apply(this.current.route, null); }
    finally { this.applying = false; }
    this.current.route = this.adapter.read();
    this.write(this.current, true);
    this.browser.history.scrollRestoration = "manual";
  }

  adopt(route) {
    // A typed/pasted hash already has a browser entry. Give that entry the
    // same ownership as an in-app link instead of pushing a duplicate.
    if (!this.current) { this.start(route); return; }
    this.pop({ v: 1, page: this.page, index: this.current.index + 1, route, layers: [] });
  }

  reconcile() {
    if (!this.current || this.applying || this.pending) return;
    this.write({ ...this.current, ...this.read() }, true);
  }

  write(entry, replace = false) {
    try {
      this.browser.history[replace ? "replaceState" : "pushState"](entry, "", this.adapter.url(entry.route));
    } catch (error) {
      this.adapter.error(error);
      return false;
    }
    this.current = entry;
    this.entries.set(entry.index, { entry, memory: this.adapter.capture() });
    this.prune();
    return true;
  }

  prune() {
    // Recent entries retain scroll/results and reopenable dialogs. Older base
    // routes remain usable using their small history.state identities alone.
    while (this.entries.size > 128) this.entries.delete(this.entries.keys().next().value);
    const used = new Set(this.layers.map(layer => layer.id));
    for (const { entry } of this.entries.values()) for (const id of entry.layers) used.add(id);
    for (const id of this.factories.keys()) if (!used.has(id)) this.factories.delete(id);
  }

  remember() {
    if (!this.current || this.applying || this.pending) return;
    if (!this.same(this.read(), this.current)) return;
    const saved = this.entries.get(this.current.index);
    if (saved) saved.memory = this.adapter.capture();
  }

  changed() {
    if (!this.current || this.applying) return;
    this.revision++;
    if (this.scheduled) return;
    this.scheduled = true;
    // A single action may activate an outer session and then one of its tasks,
    // or close a sheet and open a view. Those are one destination.
    Promise.resolve().then(() => { this.scheduled = false; this.flush(); });
  }

  flush() {
    if (!this.current || this.applying || this.pending) return;
    const next = this.read();
    if (this.same(next, this.current)) return;
    const removed = this.current.layers.some(id => !next.layers.includes(id));
    if (removed) {
      // Consume the closed layer before recording any next action. go() is
      // asynchronous: a new action in this interval belongs after the pop.
      const parents = [...this.entries.values()].map(row => row.entry)
        .filter(row => row.index < this.current.index &&
          row.layers.every(id => next.layers.includes(id)))
        .sort((a, b) => b.index - a.index);
      if (parents.length) {
        this.pending = true;
        this.browser.history.go(parents[0].index - this.current.index);
        return;
      }
    }
    for (const index of this.entries.keys()) if (index > this.current.index) this.entries.delete(index);
    this.write({ v: 1, page: this.page, index: this.current.index + 1, ...next });
  }

  layer(dismiss, reopen = null) {
    const id = this.restoringLayer || ++this.serial;
    const layer = { id, dismiss };
    this.layers.push(layer);
    if (reopen) this.factories.set(id, reopen);
    this.changed();
    return () => {
      const at = this.layers.indexOf(layer);
      if (at < 0) return;
      this.layers.splice(at, 1);
      // Only an explicitly audited, read-only factory can return through
      // Forward. Transactional prompts/progress have no such factory.
      this.changed();
    };
  }

  pop(value) {
    if (!this.current) return;
    if (value == null) {
      this.adapter.external();
      return;
    }
    if (!ConsoleHistory.valid(value)) {
      this.adapter.error(new Error("Invalid browser history entry"));
      return;
    }
    if (value.page !== this.page) {
      this.page = value.page;
      this.entries.clear();
    }
    this.revision++;
    const previous = this.current;
    const saved = this.entries.get(value.index);
    if (this.pending) {
      this.pending = false;
      this.current = value;
      this.flush();
      return;
    }
    this.remember();
    this.applying = true;
    try {
      this.adapter.dismissMenus();
      const target = value.layers.filter(id => this.layers.some(layer => layer.id === id) || this.factories.has(id));
      for (const layer of [...this.layers].reverse()) {
        if (target.includes(layer.id)) continue;
        layer.dismiss();
        if (this.layers.includes(layer)) {
          // An operation's own cancellation path retains the dialog until
          // cleanup finishes. Restore the history cursor while it does so.
          this.pending = true;
          this.browser.history.go(previous.index - value.index);
          return;
        }
      }
      this.current = value;
      this.adapter.apply(value.route, saved && saved.memory);
      for (const id of target) {
        if (this.layers.some(layer => layer.id === id)) continue;
        this.restoringLayer = id;
        this.factories.get(id)();
        this.restoringLayer = null;
      }
      const actual = { ...value, ...this.read() };
      this.write(actual, true);
    } catch (error) {
      this.adapter.error(error);
      this.write({ ...value, ...this.read() }, true);
    } finally {
      this.restoringLayer = null;
      this.applying = false;
    }
  }
}
