"use strict";
/* Puppy console SPA - vanilla ES6, no build step. */

/* ================= helpers ================= */
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

function toast(text, level = "info", ms = 4200) {
  const t = el("div", "toast " + (level === "error" ? "err" : level === "ok" ? "ok" : ""), text);
  $("toasts").appendChild(t);
  setTimeout(() => t.remove(), ms);
}

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function fmtTokens(n) {
  if (n == null) return "";
  return n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
}
const QUEUE_ROWS = 5;     // queued messages listed before collapsing to "+N more"

/* the header's thinking status; the live thinking block mirrors the header
   verbatim, so this is also what that block reads while a turn is thinking */
function thinkingLabel(tokens) {
  return tokens ? `thinking… ${fmtTokens(tokens)} tokens` : "thinking…";
}
/* localStorage is per-origin; behind a path-mounting relay every app on the
   host shares it. Namespace all keys by the mount path - "" when served at
   the root, so existing local keys keep working unchanged. */
const LS_NS = location.pathname.replace(/\/$/, "");
const lsKey = (k) => (LS_NS ? LS_NS + ":" : "") + k;
const lsGet = (k) => localStorage.getItem(lsKey(k));
const lsSet = (k, v) => localStorage.setItem(lsKey(k), v);
const lsDel = (k) => localStorage.removeItem(lsKey(k));

function tailPath(p, n = 26) {
  if (!p) return "";
  return p.length > n ? "…" + p.slice(-n) : p;
}

function apiPath(bid, path) {
  return bid ? `/api/b/${bid}/${path}` : `/api/${path}`;
}
async function api(bid, path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.body !== undefined && typeof o.body !== "string") {
    o.body = JSON.stringify(o.body);
    o.headers["Content-Type"] = "application/json";
  }
  let r;
  try {
    r = await fetch(apiPath(bid, path), o);
  } catch (e) {
    throw new Error("network error");
  }
  if (r.status === 401) { showAuth(); throw new Error("auth required"); }
  let data = null;
  try { data = await r.json(); } catch (e) { /* non-json */ }
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}
function wsUrl(bid, path) {
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  return proto + location.host + apiPath(bid, path);
}

function md(text) {
  try {
    return DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: true }));
  } catch (e) {
    return esc(text);
  }
}

/* append text to node with URLs as clickable new-tab links (DOM-built, no innerHTML) */
function linkifyInto(node, text) {
  const re = /https?:\/\/[^\s<>"]+/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    let url = m[0];
    const trail = url.match(/[)\]'".,;:!?]+$/);
    if (trail) url = url.slice(0, -trail[0].length);
    if (!url) continue;
    if (m.index > last) node.appendChild(document.createTextNode(text.slice(last, m.index)));
    const a = el("a", "", url);
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.onclick = (e) => e.stopPropagation();   // don't toggle tool cards etc.
    node.appendChild(a);
    last = m.index + url.length;
    re.lastIndex = last;
  }
  node.appendChild(document.createTextNode(text.slice(last)));
  return node;
}

/* ================= state ================= */
const state = {
  authed: false,
  instance: "",
  engines: [],            // local engines info
  engMap: {},             // key -> engine info (local)
  backends: [],           // remote backends [{id,name,url}]
  sessions: [],           // local sessions (live via updates ws)
  remoteSessions: {},     // bid -> sessions[]
  remoteOk: {},           // bid -> bool
  engCache: {},           // bid -> engines[]
  tabs: [],               // [{id,type,bid,sid,title,cmd}]
  active: null,           // tab id
  showArchived: false,
  views: {},              // tab id -> view object
};

function saveTabs() {
  try {
    lsSet("puppy.tabs", JSON.stringify({
      tabs: state.tabs.map(t => ({ id: t.id, type: t.type, bid: t.bid, sid: t.sid, title: t.title, cmd: t.cmd })),
      active: state.active,
    }));
  } catch (e) {}
}
function loadTabs() {
  try {
    const d = JSON.parse(lsGet("puppy.tabs") || "null");
    if (d && Array.isArray(d.tabs)) { state.tabs = d.tabs; state.active = d.active; }
  } catch (e) {}
}

/* ================= auth ================= */
let authMode = "login";
function showAuth(mode) {
  if (mode) authMode = mode;
  $("app").classList.add("hidden");
  $("auth-shell").classList.remove("hidden");
  const setup = authMode === "setup";
  $("auth-title").textContent = setup ? "Create admin account" : "Sign in";
  $("auth-sub").textContent = setup ? "First run - choose the credentials for this puppy instance" : "AI coding session manager";
  $("auth-pass2-wrap").classList.toggle("hidden", !setup);
  $("auth-submit").textContent = setup ? "Create & enter" : "Sign in";
  $("auth-err").classList.add("hidden");
}
async function initAuth() {
  const st = await api(0, "auth/status");
  state.instance = st.instance_name || "puppy";
  if (st.setup_required) { showAuth("setup"); return; }
  if (!st.authed) { showAuth("login"); return; }
  await enterApp();
}
$("auth-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const user = $("auth-user").value.trim();
  const pass = $("auth-pass").value;
  const errBox = $("auth-err");
  errBox.classList.add("hidden");
  try {
    if (authMode === "setup") {
      if (pass !== $("auth-pass2").value) throw new Error("passwords do not match");
      await api(0, "auth/setup", { method: "POST", body: { username: user, password: pass } });
    } else {
      await api(0, "auth/login", { method: "POST", body: { username: user, password: pass } });
    }
    $("auth-pass").value = ""; $("auth-pass2").value = "";
    await enterApp();
  } catch (e) {
    errBox.textContent = e.message;
    errBox.classList.remove("hidden");
  }
});

/* ================= boot ================= */
let updatesWs = null;
let updatesRetry = 800;

async function enterApp() {
  state.authed = true;
  $("auth-shell").classList.add("hidden");
  $("app").classList.remove("hidden");
  await refreshState();
  loadTabs();
  renderTabs();
  const valid = state.tabs.filter(t => t.type !== "session" ||
    (t.bid ? true : state.sessions.some(s => s.id === t.sid)));
  state.tabs = valid;
  if (state.active && !state.tabs.some(t => t.id === state.active)) state.active = null;
  if (!state.active && state.tabs.length) state.active = state.tabs[0].id;
  renderTabs(); renderSidebar(); activateTab(state.active);
  connectUpdates();
  if (!state.booted) {
    state.booted = true;
    pollRemotes(); setInterval(pollRemotes, 12000);
  }
}

async function refreshState() {
  const s = await api(0, "state");
  state.instance = s.instance_name;
  state.sessionColors = s.session_colors || [];
  state.engines = s.engines;
  state.engMap = {};
  s.engines.forEach(e => state.engMap[e.key] = e);
  state.backends = s.backends;
  state.sessions = s.sessions;
  state.defaultCwd = s.default_cwd || "/";
  renderSidebar();
}

function connectUpdates() {
  if (updatesWs) try { updatesWs.close(); } catch (e) {}
  const ws = new WebSocket(wsUrl(0, "ws/updates"));
  updatesWs = ws;
  ws.onopen = () => { updatesRetry = 800; $("conn-dot").classList.add("ok"); };
  ws.onmessage = (ev) => {
    try {
      const d = JSON.parse(ev.data);
      if (d.type === "sessions") { state.sessions = d.sessions; renderSidebar(); syncTabsWithSessions(); }
    } catch (e) {}
  };
  ws.onclose = () => {
    $("conn-dot").classList.remove("ok");
    if (!state.authed) return;
    setTimeout(connectUpdates, updatesRetry);
    updatesRetry = Math.min(updatesRetry * 1.6, 15000);
  };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}

async function pollRemotes() {
  for (const b of state.backends) {
    try {
      const d = await api(b.id, "sessions");
      state.remoteSessions[b.id] = d.sessions || [];
      state.remoteOk[b.id] = true;
    } catch (e) {
      state.remoteOk[b.id] = false;
    }
  }
  if (state.backends.length) renderSidebar();
}

/* ================= sidebar ================= */
function sessionsFor(bid) {
  return bid ? (state.remoteSessions[bid] || []) : state.sessions;
}
function backendName(bid) {
  if (!bid) return state.instance || "local";
  const b = state.backends.find(x => x.id === bid);
  return b ? b.name : `backend ${bid}`;
}

function renderSidebar() {
  const root = $("sess-groups");
  root.innerHTML = "";
  const groups = [{ bid: 0, name: backendName(0), ok: true }]
    .concat(state.backends.map(b => ({ bid: b.id, name: b.name, ok: state.remoteOk[b.id] !== false })));
  const showGroups = groups.length > 1;
  for (const g of groups) {
    if (showGroups) {
      const t = el("div", "sess-group-title");
      const dot = el("span", "gdot " + (g.ok ? "ok" : "bad"));
      t.appendChild(dot); t.appendChild(document.createTextNode(g.name));
      root.appendChild(t);
    }
    const list = sessionsFor(g.bid).filter(s => state.showArchived || !s.archived);
    if (!list.length) {
      root.appendChild(el("div", "sess-group-title", g.bid === 0 && !showGroups ? "no sessions yet" : "—"));
    }
    for (const s of list) {
      const item = el("button", "sess-item" + (s.archived ? " archived" : ""));
      const tabId = `s:${g.bid}:${s.id}`;
      if (state.active === tabId) item.classList.add("active");
      const r1 = el("div", "si-row");
      r1.appendChild(sessDot(s));
      r1.appendChild(el("div", "si-name", s.name || `session ${s.id}`));
      r1.appendChild(el("span", "si-be", backendName(g.bid)));
      const r2 = el("div", "si-row sub");
      r2.appendChild(provIcon(s.engine));
      r2.appendChild(el("div", "si-sub", tailPath(s.cwd)));
      item.appendChild(r1); item.appendChild(r2);
      item.onclick = () => { openSessionTab(g.bid, s.id, s); closeDrawer(); };
      item.addEventListener("contextmenu", (e) => sessionContextMenu(e, g.bid, s));
      wireSessionDrag(item, g.bid, s.id);
      root.appendChild(item);
    }
  }
  let archTotal = 0;
  for (const g of groups) archTotal += sessionsFor(g.bid).filter(s => s.archived).length;
  const tog = $("toggle-archived");
  tog.textContent = (state.showArchived ? "hide archived" : "show archived") + ` (${archTotal})`;
  tog.classList.toggle("hidden", archTotal === 0);
  renderFootEngines();
}

function sessDot(s) {
  const dot = el("span", "sess-dot" + (s.status === "running" ? " running" : ""));
  dot.style.color = s.color || "var(--txt3)";   // fill and spinner both ride currentColor
  return dot;
}

const PROVIDERS = { claude: "anthropic", codex: "openai" };

function provIcon(engine) {
  const p = PROVIDERS[engine];
  const n = el("span", "prov" + (p ? " prov-" + p : ""));
  if (!p) n.textContent = (engine || "?")[0].toUpperCase();
  n.title = p ? p : engine;
  return n;
}

/* right-click menu on sidebar sessions - mirrors the open-view ⋮ menu */
function ctxMenuAt(x, y) {
  document.querySelectorAll(".menu.dyn").forEach(m => m.remove());
  const menu = el("div", "menu dyn");
  menu.style.position = "fixed";
  menu.style.left = Math.min(x, window.innerWidth - 220) + "px";
  menu.style.top = Math.min(y, window.innerHeight - 330) + "px";
  menu.style.right = "auto"; menu.style.bottom = "auto";
  document.body.appendChild(menu);
  return menu;
}

function refreshGroup(bid) { if (bid) pollRemotes(); }   // local changes arrive via the updates websocket

function sessionContextMenu(ev, bid, s) {
  ev.preventDefault();
  ev.stopPropagation();
  const menu = ctxMenuAt(ev.clientX, ev.clientY);
  const add = (label, fn, danger) => {
    const b = el("button", danger ? "danger" : "", label);
    b.onclick = (e) => { e.stopPropagation(); menu.remove(); fn(); };
    menu.appendChild(b);
  };
  const patch = async (body) => {
    try { await api(bid, `sessions/${s.id}`, { method: "PATCH", body }); refreshGroup(bid); }
    catch (e) { toast(e.message, "error"); }
  };
  add("Rename", async () => {
    const val = await modalPrompt("Rename session", "", s.name || "");
    if (val !== null) patch({ name: val.trim() });
  });
  add("Dot color…", () => {
    const cm = ctxMenuAt(ev.clientX, ev.clientY);
    cm.classList.add("color-menu");
    for (const c of state.sessionColors || []) {
      const b = el("button", "swatch" + (s.color === c ? " sel" : ""));
      const d = el("span", "sess-dot");
      d.style.color = c;
      b.appendChild(d);
      b.onclick = (e) => { e.stopPropagation(); cm.remove(); patch({ color: c }); };
      cm.appendChild(b);
    }
  });
  add("Switch engine…", () => modalSwitchEngine({
    session: s, tab: { bid, sid: s.id }, updateHead() { refreshGroup(bid); },
  }));
  menu.appendChild(el("div", "menu-sep"));
  add("Copy cwd", () => { navigator.clipboard.writeText(s.cwd); toast("copied"); });
  if (s.has_native) add("Copy native session ID", async () => {
    try {
      const r = await api(bid, `sessions/${s.id}`);
      navigator.clipboard.writeText(r.session.native_session_id || "");
      toast("copied");
    } catch (e) { toast(e.message, "error"); }
  });
  menu.appendChild(el("div", "menu-sep"));
  add(s.archived ? "Unarchive" : "Archive", () => patch({ archived: !s.archived }));
  add("Delete session", async () => {
    const ok = await modalConfirm("Delete session?",
      "The puppy transcript is removed permanently. Files in the working directory are not touched.");
    if (!ok) return;
    try {
      await api(bid, `sessions/${s.id}`, { method: "DELETE" });
      closeTab(`s:${bid}:${s.id}`);
      refreshGroup(bid);
      toast("session deleted");
    } catch (e) { toast(e.message, "error"); }
  }, true);
}

/* sticky manual ordering: drag a session onto another to move it there */
let dragSess = null;

function wireSessionDrag(item, bid, sid) {
  item.draggable = true;
  item.addEventListener("dragstart", (e) => {
    dragSess = { bid, sid };
    item.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", String(sid)); } catch (err) {}
  });
  item.addEventListener("dragend", () => {
    dragSess = null;
    document.querySelectorAll(".sess-item.dragging,.sess-item.drag-over")
      .forEach(n => n.classList.remove("dragging", "drag-over"));
  });
  item.addEventListener("dragover", (e) => {
    if (!dragSess || dragSess.bid !== bid || dragSess.sid === sid) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    item.classList.add("drag-over");
  });
  item.addEventListener("dragleave", () => item.classList.remove("drag-over"));
  item.addEventListener("drop", async (e) => {
    e.preventDefault();
    item.classList.remove("drag-over");
    if (!dragSess || dragSess.bid !== bid || dragSess.sid === sid) return;
    const src = dragSess.sid;
    dragSess = null;
    const all = sessionsFor(bid);            // full list incl. archived - keeps hidden rows in place
    const ids = all.map(x => x.id);
    const from = ids.indexOf(src), to = ids.indexOf(sid);
    if (from < 0 || to < 0) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    all.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));   // optimistic
    renderSidebar();
    try { await api(bid, "sessions/reorder", { method: "POST", body: { order: ids } }); }
    catch (err) { toast(err.message, "error"); }
  });
}

function weeklyQuotaLeft(e) {
  // driver-provided quota (codex: parsed from its own records), else scan the
  // stored rate-limit info for a weekly window - tolerant of either shape
  if (e.quota && typeof e.quota.weekly_used_percent === "number")
    return Math.max(0, Math.min(100, 100 - e.quota.weekly_used_percent));
  const rl = e.rate_limit;
  if (rl && typeof rl === "object") {
    for (const w of [rl.primary, rl.secondary])
      if (w && w.window_minutes === 10080 && typeof w.used_percent === "number")
        return Math.max(0, Math.min(100, 100 - w.used_percent));
    if (/seven_day|weekly/i.test(rl.rateLimitType || ""))
      for (const k in rl)
        if (/percent|utilization/i.test(k) && typeof rl[k] === "number")
          return Math.max(0, Math.min(100, 100 - rl[k]));
  }
  return null;
}

function renderFootEngines() {
  const root = $("foot-engines");
  root.innerHTML = "";
  for (const e of state.engines) {
    const row = el("div", "foot-eng");
    const ico = el("span", "foot-ico");
    ico.appendChild(el("span", `engine-dot ${e.key}`));
    row.appendChild(ico);
    row.appendChild(document.createTextNode(e.label));
    const pct = weeklyQuotaLeft(e);
    const stTxt = !e.installed ? "missing" : (e.auth === "ok" ? "ready" : "no auth");
    const st = el("span", "st " + (e.installed && e.auth === "ok" ? "ok" : "bad"),
      stTxt + (pct != null ? ` · ${Math.round(pct)}% wk` : ""));
    if (pct != null) st.title = `${Math.round(pct)}% of the weekly quota remaining`;
    row.appendChild(st);
    root.appendChild(row);
  }
}

$("toggle-archived").onclick = () => { state.showArchived = !state.showArchived; renderSidebar(); };

/* ================= tabs ================= */
let termSeq = 1;

function findSessionMeta(bid, sid) {
  return sessionsFor(bid).find(s => s.id === sid);
}

function openSessionTab(bid, sid, meta) {
  const id = `s:${bid}:${sid}`;
  let tab = state.tabs.find(t => t.id === id);
  if (!tab) {
    tab = { id, type: "session", bid, sid, title: (meta && (meta.name || `session ${sid}`)) || `session ${sid}` };
    state.tabs.push(tab);
  }
  activateTab(id);
}

function openTermTab(bid, cmd) {
  const id = `t:${Date.now()}:${termSeq++}`;
  state.tabs.push({ id, type: "term", bid: bid || 0, cmd: cmd || "", title: cmd ? cmd.slice(0, 24) : `shell @ ${backendName(bid || 0)}` });
  activateTab(id);
}

function openSettingsTab() {
  if (!state.tabs.some(t => t.id === "settings")) {
    state.tabs.push({ id: "settings", type: "settings", title: "Settings" });
  }
  activateTab("settings");
}

function closeTab(id) {
  const idx = state.tabs.findIndex(t => t.id === id);
  if (idx < 0) return;
  state.tabs.splice(idx, 1);
  const v = state.views[id];
  if (v && v.destroy) v.destroy();
  delete state.views[id];
  if (state.active === id) {
    const next = state.tabs[Math.max(0, idx - 1)];
    state.active = next ? next.id : null;
  }
  renderTabs();
  activateTab(state.active);
}

function activateTab(id) {
  state.active = id || null;
  renderTabs(); renderSidebar();
  document.querySelectorAll("#views .view").forEach(v => v.classList.remove("on"));
  $("empty-hint").classList.toggle("hidden", !!state.active);
  if (!state.active) { saveTabs(); return; }
  const tab = state.tabs.find(t => t.id === state.active);
  if (!tab) return;
  let view = state.views[tab.id];
  if (!view) {
    if (tab.type === "session") view = new SessionView(tab);
    else if (tab.type === "term") view = new TermView(tab);
    else view = new SettingsView(tab);
    state.views[tab.id] = view;
  }
  view.root.classList.add("on");
  if (view.onShow) view.onShow();
  saveTabs();
}

let dragTabId = null;

function renderTabs() {
  const root = $("tabs");
  root.innerHTML = "";
  for (const t of state.tabs) {
    const tab = el("div", "tab" + (state.active === t.id ? " active" : ""));
    tab.draggable = true;
    tab.addEventListener("dragstart", (e) => {
      dragTabId = t.id;
      tab.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", t.id); } catch (err) {}
    });
    tab.addEventListener("dragend", () => {
      dragTabId = null;
      document.querySelectorAll(".tab.dragging,.tab.drag-over").forEach(n => n.classList.remove("dragging", "drag-over"));
    });
    tab.addEventListener("dragover", (e) => {
      if (!dragTabId || dragTabId === t.id) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      tab.classList.add("drag-over");
    });
    tab.addEventListener("dragleave", () => tab.classList.remove("drag-over"));
    tab.addEventListener("drop", (e) => {
      e.preventDefault();
      if (!dragTabId || dragTabId === t.id) return;
      const from = state.tabs.findIndex(x => x.id === dragTabId);
      const to = state.tabs.findIndex(x => x.id === t.id);
      dragTabId = null;
      if (from < 0 || to < 0) return;
      state.tabs.splice(to, 0, state.tabs.splice(from, 1)[0]);
      renderTabs();
    });
    let dotCls = "settings", dotColor = "";
    if (t.type === "session") {
      const meta = findSessionMeta(t.bid, t.sid);
      dotCls = meta ? meta.engine : "claude";
      if (meta && meta.color) dotColor = meta.color;
      if (meta && meta.status === "running") tab.classList.add("running");
      if (meta) t.title = meta.name || `session ${t.sid}`;
    } else if (t.type === "term") dotCls = "term";
    const tdot = el("span", "t-dot " + dotCls);
    if (dotColor) tdot.style.color = dotColor;
    tab.appendChild(tdot);
    tab.appendChild(el("span", "t-title", t.title || "tab"));
    if (t.type === "session") {
      tab.addEventListener("contextmenu", (e) => {
        const meta = findSessionMeta(t.bid, t.sid);
        if (meta) sessionContextMenu(e, t.bid, meta);
      });
    }
    const x = el("button", "t-close", "×");
    x.onclick = (e) => { e.stopPropagation(); closeTab(t.id); };
    tab.appendChild(x);
    tab.onclick = () => activateTab(t.id);
    root.appendChild(tab);
  }
  saveTabs();
}

function syncTabsWithSessions() {
  let dirty = false;
  for (const t of [...state.tabs]) {
    if (t.type === "session" && !t.bid && !state.sessions.some(s => s.id === t.sid)) {
      closeTab(t.id); dirty = true;
    }
  }
  if (!dirty) renderTabs();
}

/* tab add menu */
$("btn-tab-add").onclick = (e) => {
  e.stopPropagation();
  $("tab-add-menu").classList.toggle("hidden");
};
document.addEventListener("click", () => {
  $("tab-add-menu").classList.add("hidden");
  document.querySelectorAll(".menu.dyn").forEach(m => m.remove());
});
$("tab-add-menu").addEventListener("click", (e) => {
  const act = e.target.dataset && e.target.dataset.act;
  if (!act) return;
  $("tab-add-menu").classList.add("hidden");
  if (act === "new-session") modalNewSession();
  else if (act === "open-session") modalOpenSession();
  else if (act === "new-terminal") modalNewTerminal();
});
$("btn-new-session").onclick = () => { modalNewSession(); closeDrawer(); };
$("btn-settings").onclick = () => { openSettingsTab(); closeDrawer(); };

/* drawer (mobile) */
$("burger").onclick = () => $("app").classList.add("side-open");
$("side-backdrop").onclick = closeDrawer;
function closeDrawer() { $("app").classList.remove("side-open"); }

/* Touch-only drawer gestures. The narrow edge target keeps ordinary chat,
   terminal and tab gestures untouched while making the closed drawer easy to
   discover. Vertical movement remains native scrolling inside the drawer. */
(() => {
  const app = $("app");
  const mobile = window.matchMedia("(max-width: 900px)");
  const swipeDistance = 52;

  function wireSwipe(target, opening) {
    let start = null;
    let suppressClick = false;

    target.addEventListener("pointerdown", (e) => {
      if (!mobile.matches || e.pointerType !== "touch" || !e.isPrimary) return;
      const open = app.classList.contains("side-open");
      if ((opening && open) || (!opening && !open)) return;
      start = { id: e.pointerId, x: e.clientX, y: e.clientY };
      try { target.setPointerCapture(e.pointerId); } catch (err) {}
    });

    target.addEventListener("pointerup", (e) => {
      if (!start || e.pointerId !== start.id) return;
      const dx = e.clientX - start.x;
      const dy = e.clientY - start.y;
      const horizontal = Math.abs(dx) >= swipeDistance && Math.abs(dx) > Math.abs(dy) * 1.2;
      start = null;
      if (!horizontal || (opening ? dx <= 0 : dx >= 0)) return;
      e.preventDefault();
      suppressClick = true;
      setTimeout(() => { suppressClick = false; }, 350);
      if (opening) app.classList.add("side-open");
      else closeDrawer();
    });

    target.addEventListener("pointercancel", () => { start = null; });
    target.addEventListener("click", (e) => {
      if (!suppressClick) return;
      e.preventDefault();
      e.stopPropagation();
      suppressClick = false;
    }, true);
  }

  wireSwipe($("drawer-edge"), true);
  wireSwipe($("side"), false);
  wireSwipe($("side-backdrop"), false);
})();

/* light / dark theme (class applied pre-paint by an inline head script) */
function applyTheme(t) {
  document.documentElement.classList.toggle("light", t === "light");
  $("btn-theme").textContent = t === "light" ? "☾" : "☀";
  lsSet("puppy.theme", t);
}
$("btn-theme").onclick = () =>
  applyTheme(document.documentElement.classList.contains("light") ? "dark" : "light");
applyTheme(lsGet("puppy.theme") || "dark");

/* sidebar width: draggable, persisted */
(() => {
  const saved = parseInt(lsGet("puppy.sidew") || "", 10);
  if (saved) document.documentElement.style.setProperty("--side-w", Math.min(480, Math.max(200, saved)) + "px");
  const grip = $("side-resize");
  if (!grip) return;
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    grip.classList.add("active");
    grip.setPointerCapture(e.pointerId);
    const move = (ev) => {
      const w = Math.min(480, Math.max(200, Math.round(ev.clientX)));
      document.documentElement.style.setProperty("--side-w", w + "px");
    };
    const up = () => {
      grip.classList.remove("active");
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", up);
      const w = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--side-w"), 10);
      if (w) lsSet("puppy.sidew", String(w));
      window.dispatchEvent(new Event("resize"));   // xterm fit etc.
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
  });
  grip.addEventListener("dblclick", () => {
    document.documentElement.style.setProperty("--side-w", "256px");
    lsDel("puppy.sidew");
    window.dispatchEvent(new Event("resize"));
  });
})();

/* ctrl+j -> newline in the composer (CLI muscle memory; keeps Firefox from
   opening its Downloads/bookmarks popup). Terminal tabs keep native handling. */
document.addEventListener("keydown", (e) => {
  if (!e.ctrlKey || e.metaKey || e.altKey || (e.key !== "j" && e.key !== "J")) return;
  const t = e.target;
  if (t && t.closest && t.closest(".view.term")) return; // xterm owns keys there
  e.preventDefault();
  const view = state.views[state.active];
  if (!view || !view.ta) return;
  const ta = view.ta;
  // don't hijack typing in modal/settings inputs - just swallow the browser shortcut
  if (t !== ta && t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  ta.focus();
  const s = ta.selectionStart, en = ta.selectionEnd;
  ta.value = ta.value.slice(0, s) + "\n" + ta.value.slice(en);
  ta.selectionStart = ta.selectionEnd = s + 1;
  ta.dispatchEvent(new Event("input", { bubbles: true }));
});

/* clicking the same trigger while its menu is open closes it (returns true) */
function closeMenusToggling(anchor) {
  let wasOpen = false;
  document.querySelectorAll(".menu.dyn").forEach(m => {
    if (m._anchor === anchor) wasOpen = true;
    m.remove();
  });
  return wasOpen;
}

/* ================= SessionView ================= */
const TOOL_ICONS = {
  Bash: "$", shell: "$", Read: "📄", Write: "✏️", Edit: "✏️", file_change: "✏️",
  Grep: "🔎", Glob: "🔎", WebSearch: "🌐", WebFetch: "🌐", Task: "🤖", TodoWrite: "☑",
};
function toolSummary(tool, input) {
  if (!input) return "";
  if (input.command) return input.command;
  if (input.file_path) return input.file_path;
  if (input.pattern) return input.pattern;
  if (input.query) return input.query;
  if (input.changes) return input.changes.map(c => c.path).join(", ");
  const s = JSON.stringify(input);
  return s.length > 120 ? s.slice(0, 120) + "…" : s;
}

class SessionView {
  constructor(tab) {
    this.tab = tab;
    this.session = null;
    this.status = "idle";
    this.ws = null;
    this.closed = false;
    this.retry = 800;
    this.toolCards = {};
    this.liveEl = null;
    this.liveKind = null;
    this.statusText = "";     // header status; the transcript foot mirrors it
    this.statusRow = null;    // standalone foot row, used when no thinking block is live
    this.oldestSeq = null;
    this.history = [];        // sent messages, oldest first (shell-style recall)
    this.histIdx = null;
    this.histDraft = "";
    this.attachments = [];    // {path, url}: server path sent with the message, blob url for the preview
    this.buildDom();
    this._onResize = () => { this.syncGutter(); this.syncComposerMeta(); this.syncHeadOverflow(); this.syncQueueFade(); };
    window.addEventListener("resize", this._onResize);
    this.connect();
  }

  buildDom() {
    const root = el("div", "view chat");
    root.innerHTML = `
      <div class="chat-head">
        <div class="chat-meta-viewport">
          <div class="chat-meta-scroll">
            <span class="chip eng"><span class="dot"></span><span class="eng-label">…</span></span>
            <span class="chip be" title="Backend"></span>
            <span class="chip cwd" title=""></span>
            <span class="chat-status"></span>
          </div>
        </div>
        <div class="chat-menu">
          <button class="icon-btn menu-btn" title="Session menu">⋮</button>
        </div>
      </div>
      <div class="chat-scroll"><div class="chat-inner"></div></div>
      <div class="queue-strip hidden"></div>
      <div class="approval hidden"></div>
      <div class="composer">
        <div class="composer-box">
          <textarea rows="1" placeholder="Message the agent…"></textarea>
          <div class="attach-strip hidden"></div>
          <div class="composer-row">
            <button class="mini perm" title="Permission mode"><span class="mini-key">permissions:</span><span class="mini-value">auto</span></button>
            <button class="mini model" title="Model"><span class="mini-key">model:</span><span class="mini-value">auto</span></button>
            <button class="mini effort" title="Reasoning effort"><span class="mini-key">effort:</span><span class="mini-value">auto</span></button>
            <span class="spacer"></span>
            <button class="btn-send">Send</button>
          </div>
        </div>
      </div>`;
    $("views").appendChild(root);
    this.root = root;
    this.scroll = root.querySelector(".chat-scroll");
    this.inner = root.querySelector(".chat-inner");
    this.ta = root.querySelector("textarea");
    this.sendBtn = root.querySelector(".btn-send");
    this.composerRow = root.querySelector(".composer-row");
    this.headMeta = root.querySelector(".chat-meta-scroll");
    this.headMetaViewport = root.querySelector(".chat-meta-viewport");
    this.statusEl = root.querySelector(".chat-status");
    this.approvalEl = root.querySelector(".approval");
    this.queueEl = root.querySelector(".queue-strip");
    this.attachStrip = root.querySelector(".attach-strip");
    this.headMeta.addEventListener("scroll", () => this.syncHeadOverflow(), { passive: true });
    this.ta.addEventListener("paste", (e) => this.handlePaste(e));

    this.fieldSizing = window.CSS && CSS.supports && CSS.supports("field-sizing", "content");
    if (this.fieldSizing) {
      this.ta.classList.add("fs-content");
    } else {
      this.taGhost = el("div", "ta-ghost");
      const tcs = getComputedStyle(this.ta);
      for (const p of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing",
                       "paddingTop", "paddingBottom", "paddingLeft", "paddingRight",
                       "borderTopWidth", "borderBottomWidth", "borderLeftWidth", "borderRightWidth", "boxSizing"])
        this.taGhost.style[p] = tcs[p];
      root.querySelector(".composer-box").appendChild(this.taGhost);
    }
    this._lastTaH = 0;

    const draft = lsGet("puppy.draft." + this.tab.id);
    if (draft) { this.ta.value = draft; this.resizeComposer(); }
    this.ta.addEventListener("input", () => {
      this.histIdx = null;   // manual edits exit history mode
      this.resizeComposer();
      lsSet("puppy.draft." + this.tab.id, this.ta.value);
    });
    this.ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.submit(); return; }
      if (e.shiftKey || e.ctrlKey || e.altKey || e.metaKey || e.isComposing) return;
      const atStart = this.ta.selectionStart === 0 && this.ta.selectionEnd === 0;
      const atEnd = this.ta.selectionStart === this.ta.value.length && this.ta.selectionEnd === this.ta.value.length;
      if (e.key === "ArrowUp" && atStart && this.history.length) {
        if (this.histIdx === null) { this.histIdx = this.history.length; this.histDraft = this.ta.value; }
        if (this.histIdx > 0) {
          e.preventDefault();
          this.histIdx--;
          this.setComposer(this.history[this.histIdx]);
        }
      } else if (e.key === "ArrowDown" && atEnd && this.histIdx !== null) {
        e.preventDefault();
        this.histIdx++;
        if (this.histIdx >= this.history.length) {
          this.setComposer(this.histDraft);
          this.histIdx = null;
        } else {
          this.setComposer(this.history[this.histIdx]);
        }
      }
    });
    this.sendBtn.onclick = () => this.status === "running" ? this.interrupt() : this.submit();
    root.querySelector(".menu-btn").onclick = (e) => { e.stopPropagation(); this.showMenu(e.currentTarget); };
    root.querySelector(".mini.perm").onclick = (e) => { e.stopPropagation(); this.showPermMenu(e.currentTarget); };
    root.querySelector(".mini.model").onclick = (e) => { e.stopPropagation(); this.showModelMenu(e.currentTarget); };
    root.querySelector(".mini.effort").onclick = (e) => { e.stopPropagation(); this.showEffortMenu(e.currentTarget); };
  }

  connect() {
    if (this.closed) return;
    const ws = new WebSocket(wsUrl(this.tab.bid, `ws/session/${this.tab.sid}`));
    this.ws = ws;
    ws.onopen = () => { this.retry = 800; };
    ws.onmessage = (ev) => {
      let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      this.handle(d);
    };
    ws.onclose = () => {
      if (this.closed) return;
      this.setStatus("reconnecting…");
      setTimeout(() => this.connect(), this.retry);
      this.retry = Math.min(this.retry * 1.7, 15000);
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }

  destroy() {
    this.closed = true;
    window.removeEventListener("resize", this._onResize);
    if (this.ws) try { this.ws.close(); } catch (e) {}
    this.attachments.forEach(a => URL.revokeObjectURL(a.url));
    this.root.remove();
  }

  onShow() {
    this.syncGutter(); this.syncComposerMeta(); this.syncHeadOverflow();
    this.scrollBottom(true); this.ta.focus();
  }

  /* transcript sits left of the scrollbar; export its width so the composer /
     approval / queue columns can align with the transcript column exactly */
  syncGutter() {
    const g = this.scroll.offsetWidth - this.scroll.clientWidth;
    this.root.style.setProperty("--sbw", (g > 0 ? g : 0) + "px");
  }

  syncHeadOverflow() {
    const sc = this.headMeta;
    if (!sc || !sc.clientWidth) return;
    const moreRight = sc.scrollLeft + sc.clientWidth < sc.scrollWidth - 1;
    this.headMetaViewport.classList.toggle("more-right", moreRight);
  }

  /* Keep the controls on one line. If their labelled forms would overflow,
     retain the values and menus but drop the redundant key prefixes. */
  syncComposerMeta() {
    const row = this.composerRow;
    if (!row || !row.clientWidth) return;
    row.classList.remove("compact-meta");
    row.classList.toggle("compact-meta", row.scrollWidth > row.clientWidth + 1);
  }

  /* with field-sizing the browser autosizes natively; otherwise measure on the
     hidden mirror and only write the height when it actually changed. Either way,
     re-pin the transcript when the composer's height moved. */
  resizeComposer() {
    const ta = this.ta, sc = this.scroll;
    const pinned = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 60;
    if (!this.fieldSizing) {
      const g = this.taGhost;
      g.style.width = ta.offsetWidth + "px";
      const v = ta.value;
      g.textContent = v.endsWith("\n") ? v + "\u200b" : (v || "\u200b");
      const needed = Math.min(Math.max(g.offsetHeight, 24), 224);
      if (needed !== ta.offsetHeight) ta.style.height = needed + "px";
    }
    const h = ta.offsetHeight;
    if (h !== this._lastTaH) {
      this._lastTaH = h;
      if (pinned) sc.scrollTop = sc.scrollHeight;
    }
  }

  setComposer(v) {
    this.ta.value = v;
    this.ta.selectionStart = this.ta.selectionEnd = v.length;
    this.resizeComposer();
    lsSet("puppy.draft." + this.tab.id, v);
  }

  /* ---- incoming ---- */
  handle(d) {
    switch (d.type) {
      case "snapshot":
        this.session = d.session;
        this.status = d.status;
        this.inner.innerHTML = "";
        this.toolCards = {};
        this.oldestSeq = d.events.length ? d.events[0].seq : null;
        if (d.events.length >= 200) this.addLoadOlder();
        d.events.forEach(ev => this.renderEvent(ev, false));
        this.history = d.events.filter(ev => ev.kind === "user")
          .map(ev => (ev.data && ev.data.text) || "").filter(Boolean);
        this.histIdx = null;
        this.updateHead();
        this.updateRunState();
        this.renderQueue(d.queued || []);
        this.syncLiveStatus();
        if (d.pending_approval) this.showApproval(d.pending_approval);
        else this.hideApproval();
        this.scrollBottom(true);
        break;
      case "event":
        this.clearLive();
        this.renderEvent(d.event, true);
        if (d.event.kind === "user") {
          if (d.event.data && d.event.data.text) this.history.push(d.event.data.text);
          if (this._forceScroll) { this._forceScroll = false; this.scrollBottom(true); }
        }
        this.syncLiveStatus();
        break;
      case "delta":
        this.appendLive(d.block, d.text);
        break;
      case "status":
        this.setStatus(d.text);
        break;
      case "turn_init":
        this.setStatus(`model ${d.model}`);
        if (this.session) { this.session.last_model = d.model; this.updateHead(); }
        break;
      case "thinking_tokens":
        this.setStatus(thinkingLabel(d.tokens));
        break;
      case "approval_request":
        this.showApproval(d.req);
        break;
      case "approval_resolved":
        this.hideApproval();
        break;
      case "queued":
        this.renderQueue(d.queued || []);
        break;
      case "turn_done":
        this.status = "idle";
        this.clearLive();
        this.updateRunState();
        this.setStatus("");
        break;
      case "session_meta":
        this.session = d.session;
        this.updateHead();
        break;
      case "rate_limit":
        if (d.info && d.info.status && d.info.status !== "allowed")
          toast(`${d.engine}: rate limit ${d.info.status}`, "error");
        break;
      case "toast":
        toast(d.text, d.level || "info");
        break;
    }
    const runningKinds = { user: 1, assistant: 1, thinking: 1, tool_use: 1, tool_result: 1 };
    if (d.type === "delta" || (d.type === "event" && runningKinds[d.event.kind])) {
      this.status = "running"; this.updateRunState();
    }
  }

  updateHead() {
    const s = this.session;
    if (!s) return;
    const eng = this.root.querySelector(".chip.eng");
    eng.className = "chip eng eng-" + s.engine;
    eng.querySelector(".dot").style.background = s.color || "";
    eng.querySelector(".eng-label").textContent =
      (state.engMap[s.engine] ? state.engMap[s.engine].label : s.engine) +
      (s.last_model ? " · " + s.last_model.replace(/^claude-/, "") : (s.model ? " · " + s.model : ""));
    const cwd = this.root.querySelector(".chip.cwd");
    cwd.textContent = tailPath(s.cwd, 34);
    cwd.title = s.cwd;
    this.root.querySelector(".chip.be").textContent = backendName(this.tab.bid);
    const setMini = (cls, label, value) => {
      const button = this.root.querySelector(".mini." + cls);
      button.querySelector(".mini-value").textContent = value;
      button.title = label + ": " + value;
      button.setAttribute("aria-label", label + ": " + value);
    };
    setMini("perm", "Permission mode", s.permission_mode || "auto");
    setMini("model", "Model", s.model || "auto");
    setMini("effort", "Reasoning effort", s.effort || "auto");
    this.syncComposerMeta();
    this.syncHeadOverflow();
    this.tab.title = s.name || `session ${s.id}`;
    renderTabs();
  }

  updateRunState() {
    const running = this.status === "running";
    if (running) this.sendBtn.innerHTML = '<span class="stop-sq"></span>Stop';
    else this.sendBtn.textContent = "Send";
    this.sendBtn.classList.toggle("stop", running);
    if (!running) this.setStatus("");
    else this.syncLiveStatus();
  }

  setStatus(text) {
    this.statusText = text || "";
    this.statusEl.innerHTML = text ? `<span class="spinner"></span>${esc(text)}` : "";
    if (!text) this.statusEl.innerHTML = "";
    this.syncLiveStatus();
    this.syncHeadOverflow();
  }

  /* ---- transcript rendering ---- */
  addLoadOlder() {
    const btn = el("button", "btn btn-sm btn-ghost load-older", "load older…");
    btn.onclick = async () => {
      try {
        const d = await api(this.tab.bid, `sessions/${this.tab.sid}/events?before_seq=${this.oldestSeq}&limit=200`);
        const evs = d.events || [];
        if (!evs.length) { btn.remove(); return; }
        this.oldestSeq = evs[0].seq;
        const frag = document.createDocumentFragment();
        const tmp = this.inner;
        const anchor = btn.nextSibling;
        evs.forEach(ev => {
          const node = this.buildEventNode(ev);
          if (node) frag.appendChild(node);
        });
        tmp.insertBefore(frag, anchor);
        if (evs.length < 200) btn.remove();
      } catch (e) { toast(e.message, "error"); }
    };
    this.inner.appendChild(btn);
  }

  renderEvent(ev, live) {
    const node = this.buildEventNode(ev);
    if (node) {
      this.inner.appendChild(node);
      this.scrollBottom(!live);
    }
  }

  buildEventNode(ev) {
    const d = ev.data || {};
    switch (ev.kind) {
      case "user": {
        const n = el("div", "msg msg-user");
        linkifyInto(n, d.text || "");
        return n;
      }
      case "assistant": {
        const n = el("div", "msg msg-assistant");
        const box = el("div", "md");
        box.innerHTML = md(d.text || "");
        box.querySelectorAll("a").forEach(a => { a.target = "_blank"; a.rel = "noopener noreferrer"; });
        n.appendChild(box);
        return n;
      }
      case "thinking": {
        const n = el("details", "think");
        const sum = el("summary");
        sum.appendChild(el("span", "think-brain", "🧠"));
        sum.appendChild(el("span", "think-label", "thinking"));
        const body = el("div", "tbody", d.text || "");
        n.appendChild(sum); n.appendChild(body);
        return n;
      }
      case "tool_use": {
        const n = el("div", "tool-card");
        const head = el("div", "tool-head");
        head.appendChild(el("span", "t-caret", "❯"));
        head.appendChild(el("span", "t-ico", TOOL_ICONS[d.tool] || "🔧"));
        head.appendChild(el("span", "t-name", d.tool || "tool"));
        head.appendChild(linkifyInto(el("span", "t-sum"), toolSummary(d.tool, d.input)));
        head.appendChild(el("span", "t-state", "…"));
        const body = el("div", "tool-body");
        const inp = linkifyInto(el("pre"), JSON.stringify(d.input || {}, null, 2));
        body.appendChild(el("div", "tb-label", "input"));
        body.appendChild(inp);
        head.onclick = () => n.classList.toggle("open");
        n.appendChild(head); n.appendChild(body);
        if (d.tool_use_id) this.toolCards[d.tool_use_id] = n;
        return n;
      }
      case "tool_result": {
        const card = d.tool_use_id && this.toolCards[d.tool_use_id];
        if (card) {
          const stateEl = card.querySelector(".t-state");
          stateEl.textContent = d.is_error ? "✗" : "✔";
          stateEl.className = "t-state " + (d.is_error ? "bad" : "ok");
          if (d.is_error) card.classList.add("err");
          const body = card.querySelector(".tool-body");
          body.appendChild(el("div", "tb-label", d.is_error ? "error" : "result"));
          body.appendChild(linkifyInto(el("pre"), (d.content || "").slice(0, 12000) || "(empty)"));
          return null;
        }
        const n = el("div", "tool-card" + (d.is_error ? " err" : "") + " open");
        n.appendChild(el("div", "tool-head"));
        const body = el("div", "tool-body");
        body.appendChild(linkifyInto(el("pre"), (d.content || "").slice(0, 12000)));
        n.appendChild(body);
        return n;
      }
      case "info": {
        if (d.subtype === "todo") {
          const n = el("div", "todo-card");
          for (const line of (d.text || "").split("\n")) {
            if (!line.trim()) continue;
            const mm = line.match(/^\[( |x)\]\s?(.*)$/i);
            const done = !!mm && mm[1].toLowerCase() === "x";
            const row = el("div", "todo-row" + (done ? " done" : ""));
            row.appendChild(el("span", "todo-box", done ? "✔" : ""));
            row.appendChild(el("span", "todo-txt", mm ? mm[2] : line));
            n.appendChild(row);
          }
          return n;
        }
        const n = el("div", "info-line" + (d.subtype === "interrupted" || d.subtype === "model_switch" ? " warn" : ""));
        n.textContent = d.text || d.subtype || "";
        return n;
      }
      case "engine_switch": {
        const n = el("div", "switch-line");
        n.appendChild(document.createTextNode(`moved ${d.from} → ${d.to}`));
        return n;
      }
      case "error": {
        return el("div", "err-card", d.text || "error");
      }
      case "result": {
        const n = el("div", "result-line");
        const bits = [];
        if (!d.ok) bits.push(`<span class="bad">✗ ${esc((d.error || "failed").slice(0, 80))}</span>`);
        else bits.push("✔");
        if (d.duration_ms) bits.push((d.duration_ms / 1000).toFixed(1) + "s");
        const u = d.usage || {};
        if (u.output_tokens != null) bits.push(fmtTokens(u.output_tokens) + " out");
        if (d.cost_usd != null) bits.push("$" + d.cost_usd.toFixed(3));
        bits.push(fmtTime(ev.ts));
        n.innerHTML = bits.join(" · ");
        return n;
      }
    }
    return null;
  }

  /* live streaming bubble */
  appendLive(block, text) {
    if (this.liveEl && this.liveKind !== block) this.clearLive();
    if (!this.liveEl) {
      this.liveKind = block;
      if (block === "thinking") {
        this.liveEl = el("details", "think msg-live");
        this.liveEl.open = false;
        const sum = el("summary");
        sum.appendChild(el("span", "think-label", this.statusText || thinkingLabel(0)));
        this.liveEl.appendChild(sum);
        this.liveEl.appendChild(el("div", "tbody"));
      } else {
        this.liveEl = el("div", "msg msg-assistant msg-live");
        this.liveEl.appendChild(el("div", "md"));
        this.liveEl.appendChild(el("span", "cursor"));
      }
      this.inner.appendChild(this.liveEl);
      this.syncLiveStatus();
    }
    const target = block === "thinking" ? this.liveEl.querySelector(".tbody") : this.liveEl.querySelector(".md");
    target.textContent += text;
    this.scrollBottom(false);
  }
  clearLive() {
    if (this.liveEl) { this.liveEl.remove(); this.liveEl = null; this.liveKind = null; }
  }
  /* The foot of the transcript always says what the header says while a turn
     runs. A streaming thinking block carries it in its summary; the rest of the
     time - tool calls, text streaming, the gaps between blocks - a standalone
     row does, so the two never disagree. */
  syncLiveStatus() {
    const text = this.statusText || thinkingLabel(0);
    const thinking = this.liveEl && this.liveKind === "thinking";
    if (thinking) {
      const lab = this.liveEl.querySelector(".think-label");
      if (lab) lab.textContent = text;
    }
    if (this.status !== "running" || thinking) {
      if (this.statusRow) { this.statusRow.remove(); this.statusRow = null; }
      return;
    }
    if (!this.statusRow) {
      this.statusRow = el("div", "live-status");
      this.statusRow.appendChild(el("span", "spinner"));
      this.statusRow.appendChild(el("span", "think-label", text));
    } else {
      this.statusRow.querySelector(".think-label").textContent = text;
    }
    if (this.inner.lastChild !== this.statusRow) {
      this.inner.appendChild(this.statusRow);   // stays the last thing in the transcript
      this.scrollBottom(false);
    }
  }

  scrollBottom(force) {
    const nearBottom = this.scroll.scrollHeight - this.scroll.scrollTop - this.scroll.clientHeight < 160;
    if (force || nearBottom) this.scroll.scrollTop = this.scroll.scrollHeight;
  }

  /* ---- outgoing ---- */
  async handlePaste(e) {
    const items = e.clipboardData ? [...e.clipboardData.items] : [];
    const images = items.filter(it => it.kind === "file" && /^image\//.test(it.type));
    if (!images.length) return;
    e.preventDefault();
    for (const it of images) {
      const blob = it.getAsFile();
      if (!blob) continue;
      try {
        const r = await fetch(apiPath(this.tab.bid, `sessions/${this.tab.sid}/upload`), {
          method: "POST", headers: { "Content-Type": blob.type }, body: blob,
        });
        const d = await r.json().catch(() => null);
        if (!r.ok) throw new Error((d && d.error) || `HTTP ${r.status}`);
        /* the blob is already in hand, so the thumbnail costs no round trip -
           the uploads directory is not served over HTTP */
        this.attachments.push({ path: d.path, url: URL.createObjectURL(blob) });
        this.renderAttachments();
      } catch (err) {
        toast("image upload failed: " + err.message, "error");
      }
    }
  }

  renderAttachments() {
    this.attachStrip.innerHTML = "";
    this.attachStrip.classList.toggle("hidden", !this.attachments.length);
    for (const a of this.attachments) {
      const chip = el("span", "attach-chip");
      const img = el("img", "attach-thumb");
      img.src = a.url;
      img.alt = "pasted image";
      img.title = a.path.split("/").pop();
      chip.appendChild(img);
      const x = el("button", "attach-x", "×");
      x.title = "Remove attachment";
      x.onclick = () => {
        URL.revokeObjectURL(a.url);
        this.attachments = this.attachments.filter(o => o !== a);
        this.renderAttachments();
      };
      chip.appendChild(x);
      this.attachStrip.appendChild(chip);
    }
  }

  submit() {
    let text = this.ta.value.trim();
    if (!text && !this.attachments.length) return;
    if (!this.ws || this.ws.readyState !== 1) { toast("not connected", "error"); return; }
    if (this.attachments.length) {
      const lines = this.attachments
        .map(a => `[image attached: ${a.path} — view it with your image/file tools]`).join("\n");
      text = text ? text + "\n\n" + lines : lines;
      this.attachments.forEach(a => URL.revokeObjectURL(a.url));
      this.attachments = [];
      this.renderAttachments();
    }
    this.ws.send(JSON.stringify({ type: "message", text }));
    this.ta.value = "";
    this.resizeComposer();
    this.histIdx = null; this.histDraft = "";
    // sending always jumps to the bottom - now, and again when the sent
    // message echoes back as a transcript event (even if it was queued)
    this._forceScroll = true;
    this.scrollBottom(true);
    lsDel("puppy.draft." + this.tab.id);
    this.status = "running"; this.updateRunState();
    this.setStatus("starting…");
  }
  interrupt() {
    if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify({ type: "interrupt" }));
  }

  /* ---- approvals ---- */
  showApproval(req) {
    this.approvalEl.classList.remove("hidden");
    this.approvalEl.innerHTML = "";
    this.approvalEl.appendChild(el("div", "ap-title", `⚠ approval: ${req.display_name || req.tool_name}` +
      (req.description ? ` — ${req.description}` : "")));
    const pre = el("pre");
    const inp = req.input || {};
    linkifyInto(pre, inp.command || inp.file_path && (inp.file_path + (inp.content ? "\n---\n" + String(inp.content).slice(0, 800) : ""))
      || JSON.stringify(inp, null, 2).slice(0, 1500));
    this.approvalEl.appendChild(pre);
    const btns = el("div", "ap-btns");
    const allow = el("button", "btn btn-ok btn-sm", "Allow");
    allow.onclick = () => this.respondApproval(req, "allow");
    const deny = el("button", "btn btn-danger btn-sm", "Deny");
    deny.onclick = () => this.respondApproval(req, "deny");
    btns.appendChild(allow); btns.appendChild(deny);
    for (const sug of req.suggestions || []) {
      if (sug.type === "setMode" && sug.mode) {
        const b = el("button", "btn btn-sm", `Allow + switch to ${sug.mode}`);
        b.onclick = () => this.respondApproval(req, "allow", [sug]);
        btns.appendChild(b);
      }
    }
    this.approvalEl.appendChild(btns);
    this.scrollBottom(true);
  }
  hideApproval() {
    this.approvalEl.classList.add("hidden");
    this.approvalEl.innerHTML = "";
  }
  respondApproval(req, behavior, updated_permissions) {
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify({
        type: "approval_response", request_id: req.request_id,
        behavior, updated_permissions: updated_permissions || undefined,
      }));
    }
    this.hideApproval();
  }

  renderQueue(q) {
    const box = this.queueEl;
    box.innerHTML = "";
    if (!q.length) { box.classList.add("hidden"); return; }
    box.classList.remove("hidden");
    box.appendChild(el("div", "q-head", `queued · ${q.length}`));
    /* one row per message, in the order they will run. The per-item cap only
       keeps a runaway message out of the DOM - each row is a single line that
       fades out at whatever width is going. */
    const shown = q.slice(0, QUEUE_ROWS);
    shown.forEach((text, i) => {
      const row = el("div", "q-item");
      row.appendChild(el("span", "q-n", String(i + 1)));
      const t = el("span", "q-t", text.length > 200 ? text.slice(0, 199) + "…" : text);
      t.title = text;
      row.appendChild(t);
      const x = el("button", "q-x", "×");
      x.title = "Cancel this queued message";
      x.onclick = () => this.unqueue(i, text);   // full text, not the capped copy
      row.appendChild(x);
      box.appendChild(row);
    });
    if (q.length > shown.length)
      box.appendChild(el("div", "q-more", `+${q.length - shown.length} more`));
    this.syncQueueFade();
  }
  unqueue(index, text) {
    if (!this.ws || this.ws.readyState !== 1) { toast("not connected", "error"); return; }
    this.ws.send(JSON.stringify({ type: "unqueue", index, text }));
  }
  /* mask a row's tail only when its line overruns the box */
  syncQueueFade() {
    const box = this.queueEl;
    if (!box || box.classList.contains("hidden")) return;
    box.querySelectorAll(".q-t").forEach(t => {
      t.classList.toggle("clipped", t.scrollWidth > t.clientWidth + 1);
    });
  }

  /* ---- menus / meta ops ---- */
  showMenu(anchor) {
    if (closeMenusToggling(anchor)) return;
    const menu = el("div", "menu dyn");
    menu._anchor = anchor;
    const add = (label, fn, danger) => {
      const b = el("button", danger ? "danger" : "", label);
      b.onclick = (e) => { e.stopPropagation(); menu.remove(); fn(); };
      menu.appendChild(b);
    };
    add("Rename", () => this.rename());
    add("Dot color…", () => this.pickColor(anchor));
    add("Switch engine…", () => modalSwitchEngine(this));
    menu.appendChild(el("div", "menu-sep"));
    add("Copy cwd", () => { navigator.clipboard.writeText(this.session.cwd); toast("copied"); });
    if (this.session && this.session.native_session_id)
      add("Copy native session ID", () => { navigator.clipboard.writeText(this.session.native_session_id); toast("copied"); });
    menu.appendChild(el("div", "menu-sep"));
    add(this.session && this.session.archived ? "Unarchive" : "Archive", () => this.archive());
    add("Delete session", () => this.deleteSession(), true);
    anchor.parentElement.appendChild(menu);
  }

  pickColor(anchor) {
    document.querySelectorAll(".menu.dyn").forEach(m => m.remove());   // always opens fresh (invoked from the ⋮ menu)
    const menu = el("div", "menu dyn color-menu");
    for (const c of state.sessionColors || []) {
      const b = el("button", "swatch" + (this.session && this.session.color === c ? " sel" : ""));
      const d = el("span", "sess-dot");
      d.style.color = c;
      b.appendChild(d);
      b.onclick = async (e) => {
        e.stopPropagation(); menu.remove();
        await this.patchSession({ color: c });
        renderSidebar(); renderTabs();
      };
      menu.appendChild(b);
    }
    anchor.parentElement.appendChild(menu);
  }

  showPermMenu(anchor) {
    if (!this.session) return;
    const eng = state.engMap[this.session.engine];
    const opts = (eng && eng.permission_options) || [];
    this.optionMenu(anchor, opts, this.session.permission_mode, (v) => this.patchSession({ permission_mode: v }));
  }

  async patchSession(body) {
    try {
      const r = await api(this.tab.bid, `sessions/${this.tab.sid}`, { method: "PATCH", body });
      this.session = r.session; this.updateHead();
    } catch (e) { toast(e.message, "error"); }
  }

  optionMenu(anchor, opts, current, onPick) {
    if (closeMenusToggling(anchor)) return null;
    const menu = el("div", "menu dyn");
    menu._anchor = anchor;
    menu.style.bottom = "36px"; menu.style.top = "auto"; menu.style.right = "auto";
    menu.style.left = anchor.offsetLeft + "px";   // open above its own button, not the row start
    for (const o of opts) {
      const b = el("button", "", `${o.label}${current === o.value ? " ✔" : ""}`);
      b.title = o.hint || "";
      b.onclick = (e) => { e.stopPropagation(); menu.remove(); onPick(o.value); };
      menu.appendChild(b);
    }
    anchor.parentElement.style.position = "relative";
    anchor.parentElement.appendChild(menu);
    return menu;
  }

  showModelMenu(anchor) {
    if (!this.session) return;
    const eng = state.engMap[this.session.engine];
    const opts = (eng && eng.model_options) || [];
    const cur = this.session.model || "";
    const menu = this.optionMenu(anchor, opts, cur, (v) => this.patchSession({ model: v }));
    if (!menu) return;
    const isCustom = cur && !opts.some(o => o.value === cur);
    const custom = el("button", "", `Custom…${isCustom ? ` (${cur}) ✔` : ""}`);
    custom.title = "Any model id the engine accepts";
    custom.onclick = async (e) => {
      e.stopPropagation(); menu.remove();
      const val = await modalPrompt("Model override",
        "Model for this session (empty = engine default).", cur);
      if (val !== null) this.patchSession({ model: val.trim() });
    };
    menu.appendChild(custom);
  }

  showEffortMenu(anchor) {
    if (!this.session) return;
    const eng = state.engMap[this.session.engine];
    const opts = (eng && eng.effort_options) || [];
    if (!opts.length) { toast("engine has no effort levels", "info"); return; }
    this.optionMenu(anchor, opts, this.session.effort || "", (v) => this.patchSession({ effort: v }));
  }

  async rename() {
    const val = await modalPrompt("Rename session", "", this.session ? this.session.name : "");
    if (val === null) return;
    try {
      const r = await api(this.tab.bid, `sessions/${this.tab.sid}`, { method: "PATCH", body: { name: val.trim() } });
      this.session = r.session; this.updateHead(); renderSidebar();
    } catch (e) { toast(e.message, "error"); }
  }

  async archive() {
    try {
      const r = await api(this.tab.bid, `sessions/${this.tab.sid}`,
        { method: "PATCH", body: { archived: !(this.session && this.session.archived) } });
      this.session = r.session; renderSidebar(); toast(this.session.archived ? "archived" : "unarchived");
    } catch (e) { toast(e.message, "error"); }
  }

  async deleteSession() {
    const ok = await modalConfirm("Delete session?",
      "The puppy transcript is removed permanently. Files in the working directory are not touched.");
    if (!ok) return;
    try {
      await api(this.tab.bid, `sessions/${this.tab.sid}`, { method: "DELETE" });
      closeTab(this.tab.id);
      if (this.tab.bid) pollRemotes();
      toast("session deleted");
    } catch (e) { toast(e.message, "error"); }
  }
}

/* ================= TermView ================= */
class TermView {
  constructor(tab) {
    this.tab = tab;
    this.closed = false;
    this.root = el("div", "view term");
    this.root.innerHTML = `<div class="term-wrap"><div class="term-host"></div></div>`;
    $("views").appendChild(this.root);
    this.host = this.root.querySelector(".term-host");
    this.started = false;
  }
  onShow() {
    if (!this.started) { this.started = true; this.start(); }
    else if (this.fit) setTimeout(() => this.fit.fit(), 30);
    if (this.term) this.term.focus();
  }
  start() {
    this.term = new Terminal({
      cursorBlink: true, fontSize: 13, scrollback: 8000,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      theme: { background: "#000000", foreground: "#e8e8ec", cursor: "#f0a84a" },
    });
    this.fit = new FitAddon.FitAddon();
    this.term.loadAddon(this.fit);
    this.term.open(this.host);
    setTimeout(() => { this.fit.fit(); this.connect(); }, 40);
    this.resizeObs = new ResizeObserver(() => {
      if (!this.fit) return;
      try { this.fit.fit(); } catch (e) {}
      this.sendResize();
    });
    this.resizeObs.observe(this.host);
  }
  connect() {
    const params = new URLSearchParams({ cols: this.term.cols, rows: this.term.rows });
    if (this.tab.cmd) params.set("cmd", this.tab.cmd);
    const ws = new WebSocket(wsUrl(this.tab.bid, "ws/term?" + params.toString()));
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    const enc = new TextEncoder();
    ws.onopen = () => {
      this.term.focus();
      this.dataSub = this.term.onData(d => { if (ws.readyState === 1) ws.send(enc.encode(d)); });
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") return;
      this.term.write(new Uint8Array(ev.data));
    };
    ws.onclose = () => { if (!this.closed) this.showDead(); };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }
  sendResize() {
    if (this.ws && this.ws.readyState === 1)
      this.ws.send(JSON.stringify({ type: "resize", cols: this.term.cols, rows: this.term.rows }));
  }
  showDead() {
    if (this.root.querySelector(".term-dead")) return;
    const d = el("div", "term-dead");
    d.appendChild(el("div", "", "terminal ended"));
    const b = el("button", "btn btn-pri", "New shell");
    b.onclick = () => {
      d.remove();
      this.term.reset();
      this.connect();
    };
    d.appendChild(b);
    this.root.querySelector(".term-wrap").style.position = "relative";
    this.root.appendChild(d);
  }
  destroy() {
    this.closed = true;
    if (this.resizeObs) this.resizeObs.disconnect();
    if (this.ws) try { this.ws.close(); } catch (e) {}
    if (this.term) this.term.dispose();
    this.root.remove();
  }
}

/* ================= SettingsView ================= */
class SettingsView {
  constructor(tab) {
    this.tab = tab;
    this.root = el("div", "view settings");
    this.root.innerHTML = `<div class="settings-scroll"><div class="settings-inner"></div></div>`;
    $("views").appendChild(this.root);
    this.inner = this.root.querySelector(".settings-inner");
    this.render();
  }
  destroy() { this.root.remove(); }
  onShow() { this.render(); }

  async render() {
    let settings, engines;
    try {
      [settings, engines] = await Promise.all([api(0, "settings"), api(0, "engines")]);
    } catch (e) { this.inner.innerHTML = `<div class="err-card">${esc(e.message)}</div>`; return; }
    state.engines = engines.engines;
    state.engines.forEach(e2 => state.engMap[e2.key] = e2);
    renderFootEngines();
    this.inner.innerHTML = "";

    /* instance */
    const c1 = el("div", "card");
    c1.innerHTML = `<h2>Instance</h2>
      <label>Instance name<input type="text" id="set-name" value="${esc(settings.instance_name)}"></label>
      <label>Default working directory<input type="text" id="set-cwd" value="${esc(settings.default_cwd || "")}"></label>
      <label>Terminal command<input type="text" id="set-term" value="${esc(settings.terminal_command)}"></label>
      <div class="kv"><span class="k">Listen</span><span class="v">${esc(settings.web.host)}:${esc(String(settings.web.port))}</span></div>
      <div class="kv"><span class="k">Version</span><span class="v">${esc(settings.version)}</span></div>
      <div class="kv"><span class="k">API token</span><span class="v" id="set-token" style="cursor:pointer" title="click to reveal / copy">••••••••••••</span></div>
      <div class="m-btns" style="justify-content:flex-start;margin-top:10px">
        <button class="btn btn-pri btn-sm" id="set-save">Save</button>
        <button class="btn btn-sm btn-ghost" id="set-logout">Log out</button>
      </div>
`;
    this.inner.appendChild(c1);
    let tokenShown = false;
    c1.querySelector("#set-token").onclick = () => {
      const elx = c1.querySelector("#set-token");
      if (!tokenShown) { elx.textContent = settings.api_token; tokenShown = true; }
      else { navigator.clipboard.writeText(settings.api_token); toast("token copied"); }
    };
    c1.querySelector("#set-save").onclick = async () => {
      try {
        await api(0, "settings", { method: "PATCH", body: {
          instance_name: c1.querySelector("#set-name").value,
          default_cwd: c1.querySelector("#set-cwd").value,
          terminal_command: c1.querySelector("#set-term").value,
        }});
        toast("saved", "ok"); refreshState();
      } catch (e) { toast(e.message, "error"); }
    };
    c1.querySelector("#set-logout").onclick = async () => {
      await api(0, "auth/logout", { method: "POST" });
      location.reload();
    };

    /* engines */
    const c2 = el("div", "card");
    c2.innerHTML = `<h2>Engines</h2>`;
    for (const e2 of state.engines) {
      const row = el("div", "kv");
      row.appendChild(el("span", "engine-dot " + e2.key));
      const name = el("span", "k", e2.label);
      row.appendChild(name);
      row.appendChild(el("span", "pill " + (e2.installed ? "ok" : "bad"), e2.installed ? (e2.version || "installed") : "not installed"));
      row.appendChild(el("span", "pill " + (e2.auth === "ok" ? "ok" : "bad"), "auth: " + e2.auth));
      if (e2.rate_limit && e2.rate_limit.resetsAt) {
        const reset = new Date(e2.rate_limit.resetsAt * 1000);
        row.appendChild(el("span", "pill " + (e2.rate_limit.status === "allowed" ? "" : "warn"),
          `${e2.rate_limit.rateLimitType || "window"} resets ${reset.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`));
      }
      c2.appendChild(row);
    }
    this.inner.appendChild(c2);

    /* backends */
    const c3 = el("div", "card");
    c3.innerHTML = `<h2>Backends</h2><div id="be-list"></div>
      <div class="settings-form" style="margin-top:12px">
        <label>Name<input type="text" id="be-name"></label>
        <label>URL<input type="text" id="be-url"></label>
        <label class="full">API token<input type="text" id="be-token"></label>
        <div class="full"><button class="btn btn-pri btn-sm" id="be-add">Add backend</button></div>
      </div>`;
    const beList = c3.querySelector("#be-list");
    const renderBes = () => {
      beList.innerHTML = "";
      if (!state.backends.length) beList.innerHTML = `<p class="hint">No remote backends. This instance ("${esc(backendName(0))}") is always available as local.</p>`;
      for (const b of state.backends) {
        const row = el("div", "be-row");
        row.appendChild(el("span", "be-name", b.name));
        row.appendChild(el("span", "be-url", b.url));
        const test = el("button", "btn btn-sm", "Test");
        test.onclick = async () => {
          test.textContent = "…";
          try {
            const r = await api(0, `backends/${b.id}/test`, { method: "POST" });
            test.textContent = "Test";
            if (r.ok) toast(`${b.name}: ok (${r.remote && r.remote.version})`, "ok");
            else toast(`${b.name}: ${r.error || "HTTP " + r.status}`, "error");
          } catch (e) { test.textContent = "Test"; toast(e.message, "error"); }
        };
        const rm = el("button", "btn btn-danger btn-sm", "Remove");
        rm.onclick = async () => {
          if (!(await modalConfirm("Remove backend?", `${b.name} (${b.url})`))) return;
          await api(0, `backends/${b.id}`, { method: "DELETE" });
          await refreshState(); renderBes(); renderSidebar();
        };
        row.appendChild(test); row.appendChild(rm);
        beList.appendChild(row);
      }
    };
    renderBes();
    c3.querySelector("#be-add").onclick = async () => {
      try {
        await api(0, "backends", { method: "POST", body: {
          name: c3.querySelector("#be-name").value,
          url: c3.querySelector("#be-url").value,
          token: c3.querySelector("#be-token").value,
        }});
        toast("backend added", "ok");
        await refreshState(); renderBes(); pollRemotes();
        c3.querySelector("#be-name").value = c3.querySelector("#be-url").value = c3.querySelector("#be-token").value = "";
      } catch (e) { toast(e.message, "error"); }
    };
    this.inner.appendChild(c3);

    /* security */
    const c4 = el("div", "card");
    c4.innerHTML = `<h2>Security</h2>
      <div class="settings-form">
        <label>Current password<input type="password" id="pw-old"></label>
        <label>New password<input type="password" id="pw-new"></label>
        <div class="full"><button class="btn btn-pri btn-sm" id="pw-save">Change password</button></div>
      </div>`;
    c4.querySelector("#pw-save").onclick = async () => {
      try {
        await api(0, "auth/password", { method: "POST", body: {
          old: c4.querySelector("#pw-old").value, new: c4.querySelector("#pw-new").value } });
        toast("password changed", "ok");
        c4.querySelector("#pw-old").value = c4.querySelector("#pw-new").value = "";
      } catch (e) { toast(e.message, "error"); }
    };
    this.inner.appendChild(c4);
  }
}

/* ================= modals ================= */
function modal(html) {
  const back = el("div", "modal-backdrop");
  const m = el("div", "modal");
  m.innerHTML = html;
  back.appendChild(m);
  $("modal-root").appendChild(back);
  const close = () => back.remove();
  back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
  document.addEventListener("keydown", function escH(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", escH); }
  });
  return { m, close };
}

function modalConfirm(title, text) {
  return new Promise((resolve) => {
    const { m, close } = modal(`<h2>${esc(title)}</h2><p style="color:var(--txt2);font-size:13px">${esc(text || "")}</p>
      <div class="m-btns"><button class="btn" id="mc-no">Cancel</button><button class="btn btn-danger btn-solid" id="mc-yes">Confirm</button></div>`);
    m.querySelector("#mc-no").onclick = () => { close(); resolve(false); };
    m.querySelector("#mc-yes").onclick = () => { close(); resolve(true); };
  });
}

function modalPrompt(title, hint, value) {
  return new Promise((resolve) => {
    const { m, close } = modal(`<h2>${esc(title)}</h2>
      ${hint ? `<p class="hint">${esc(hint)}</p>` : ""}
      <input type="text" id="mp-val">
      <div class="m-btns"><button class="btn" id="mp-no">Cancel</button><button class="btn btn-pri" id="mp-yes">OK</button></div>`);
    const inp = m.querySelector("#mp-val");
    inp.value = value || ""; inp.focus(); inp.select();
    const done = (v) => { close(); resolve(v); };
    m.querySelector("#mp-no").onclick = () => done(null);
    m.querySelector("#mp-yes").onclick = () => done(inp.value);
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") done(inp.value); });
  });
}

/* new session */
async function modalNewSession() {
  const beOpts = [{ id: 0, name: backendName(0) }].concat(state.backends);
  const { m, close } = modal(`<h2>New session</h2>
    <label>Backend<select id="ns-be">${beOpts.map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join("")}</select></label>
    <div class="engine-pick" id="ns-engines"></div>
    <label>Working directory<input type="text" id="ns-cwd" spellcheck="false"></label>
    <div class="dirpick hidden" id="ns-dirs"></div>
    <label class="check" style="margin:8px 0"><input type="checkbox" id="ns-mkdir"> create directory if missing</label>
    <label>Name <span style="text-transform:none;letter-spacing:0">(optional, auto from first message)</span><input type="text" id="ns-name"></label>
    <div class="field-row">
      <label>Model<select id="ns-model"></select></label>
      <label>Effort<select id="ns-effort"></select></label>
      <label>Permissions<select id="ns-perm"></select></label>
    </div>
    <label class="hidden" id="ns-model-custom-wrap">Custom model<input type="text" id="ns-model-custom" placeholder="model id"></label>
    <div class="field-lbl" style="margin-top:8px">Color<div class="swatch-row" id="ns-colors"></div></div>
    <div class="m-btns"><button class="btn" id="ns-cancel">Cancel</button><button class="btn btn-pri" id="ns-go">Start session</button></div>`);

  const beSel = m.querySelector("#ns-be");
  const engBox = m.querySelector("#ns-engines");
  const permSel = m.querySelector("#ns-perm");
  const modelSel = m.querySelector("#ns-model");
  const effortSel = m.querySelector("#ns-effort");
  const customWrap = m.querySelector("#ns-model-custom-wrap");
  const customInp = m.querySelector("#ns-model-custom");
  modelSel.onchange = () => customWrap.classList.toggle("hidden", modelSel.value !== "__custom__");

  const colorBox = m.querySelector("#ns-colors");
  const palette = state.sessionColors || [];
  let nsColor = palette[Math.floor(Math.random() * palette.length)] || "";
  const renderColors = () => {
    colorBox.innerHTML = "";
    for (const c of palette) {
      const b = el("button", "swatch" + (c === nsColor ? " sel" : ""));
      b.type = "button";
      const d = el("span", "sess-dot");
      d.style.color = c;
      b.appendChild(d);
      b.onclick = () => { nsColor = c; renderColors(); };
      colorBox.appendChild(b);
    }
  };
  renderColors();
  const cwdInp = m.querySelector("#ns-cwd");
  const dirBox = m.querySelector("#ns-dirs");
  cwdInp.value = lsGet("puppy.lastcwd") || state.defaultCwd || "/";
  let engines = [];
  let engine = null;

  async function loadEngines() {
    const bid = parseInt(beSel.value, 10);
    try {
      if (bid === 0) engines = state.engines;
      else {
        if (!state.engCache[bid]) state.engCache[bid] = (await api(bid, "engines")).engines;
        engines = state.engCache[bid];
      }
    } catch (e) { toast("backend unreachable: " + e.message, "error"); engines = []; }
    engBox.innerHTML = "";
    for (const e2 of engines) {
      const card = el("div", "ep");
      card.dataset.key = e2.key;
      card.innerHTML = `<div class="ep-ico prov ${PROVIDERS[e2.key] ? "prov-" + PROVIDERS[e2.key] : ""}">${PROVIDERS[e2.key] ? "" : esc((e2.key[0] || "?").toUpperCase())}</div>
        <div class="ep-name">${esc(e2.label)}</div>
        <div class="ep-sub">${e2.installed ? (e2.auth === "ok" ? "ready" : "no auth") : "missing"}</div>`;
      card.onclick = () => pick(e2.key);
      engBox.appendChild(card);
    }
    pick(engines.length ? engines[0].key : null);
  }
  function pick(key) {
    engine = key;
    engBox.querySelectorAll(".ep").forEach(c => c.classList.toggle("sel", c.dataset.key === key));
    const e2 = engines.find(x => x.key === key);
    const fill = (sel, opts, selected) => {
      sel.innerHTML = "";
      for (const o of opts) {
        const opt = document.createElement("option");
        opt.value = o.value; opt.textContent = o.label; opt.title = o.hint || "";
        if (o.value === selected) opt.selected = true;
        sel.appendChild(opt);
      }
    };
    fill(permSel, e2 ? e2.permission_options : [], e2 ? e2.default_permission : "");
    fill(modelSel, (e2 ? e2.model_options : []).concat([{ value: "__custom__", label: "Custom…" }]), "");
    fill(effortSel, e2 ? e2.effort_options : [], "");
    modelSel.onchange();
  }
  beSel.onchange = loadEngines;
  await loadEngines();

  /* directory browser */
  let dirTimer = null;
  async function browse() {
    const bid = parseInt(beSel.value, 10);
    try {
      const d = await api(bid, "fs?path=" + encodeURIComponent(cwdInp.value || "/"));
      dirBox.classList.remove("hidden");
      dirBox.innerHTML = "";
      if (d.parent !== null && d.parent !== undefined) {
        const up = el("button", "", "↩ " + d.parent);
        up.onclick = () => { cwdInp.value = d.parent; browse(); };
        dirBox.appendChild(up);
      }
      for (const name of d.dirs) {
        const b = el("button", "", "📁 " + name);
        b.onclick = () => { cwdInp.value = (d.path === "/" ? "" : d.path) + "/" + name; browse(); };
        dirBox.appendChild(b);
      }
      if (!d.dirs.length) dirBox.appendChild(el("button", "", "(no subdirectories)"));
    } catch (e) { dirBox.classList.add("hidden"); }
  }
  cwdInp.addEventListener("focus", browse);
  cwdInp.addEventListener("input", () => { clearTimeout(dirTimer); dirTimer = setTimeout(browse, 350); });

  m.querySelector("#ns-cancel").onclick = close;
  m.querySelector("#ns-go").onclick = async () => {
    const bid = parseInt(beSel.value, 10);
    if (!engine) { toast("pick an engine", "error"); return; }
    try {
      const r = await api(bid, "sessions", { method: "POST", body: {
        engine, cwd: cwdInp.value.trim(), name: m.querySelector("#ns-name").value,
        model: modelSel.value === "__custom__" ? customInp.value.trim() : modelSel.value,
        effort: effortSel.value, permission_mode: permSel.value, color: nsColor,
        mkdir: m.querySelector("#ns-mkdir").checked,
      }});
      lsSet("puppy.lastcwd", cwdInp.value.trim());
      close();
      if (bid) await pollRemotes();
      openSessionTab(bid, r.session.id, r.session);
    } catch (e) { toast(e.message, "error"); }
  };
}

/* open existing session */
function modalOpenSession() {
  const groups = [{ bid: 0, name: backendName(0), sessions: state.sessions }]
    .concat(state.backends.map(b => ({ bid: b.id, name: b.name, sessions: state.remoteSessions[b.id] || [] })));
  let html = `<h2>Open session</h2><input type="text" id="os-filter" placeholder="filter…"><div id="os-list" style="max-height:50vh;overflow-y:auto;margin-top:10px"></div>`;
  const { m, close } = modal(html);
  const list = m.querySelector("#os-list");
  const filterInp = m.querySelector("#os-filter");
  const render = () => {
    const q = filterInp.value.toLowerCase();
    list.innerHTML = "";
    for (const g of groups) {
      const matches = g.sessions.filter(s => !q || (s.name || "").toLowerCase().includes(q) || (s.cwd || "").toLowerCase().includes(q));
      if (!matches.length) continue;
      if (groups.length > 1) list.appendChild(el("div", "sess-group-title", g.name));
      for (const s of matches) {
        const item = el("button", "sess-item");
        const r1 = el("div", "si-row");
        r1.appendChild(sessDot(s));
        r1.appendChild(el("div", "si-name", (s.name || `session ${s.id}`) + (s.archived ? " (archived)" : "")));
        const r2 = el("div", "si-row sub");
        r2.appendChild(provIcon(s.engine));
        r2.appendChild(el("div", "si-sub", tailPath(s.cwd)));
        item.appendChild(r1); item.appendChild(r2);
        item.onclick = () => { close(); openSessionTab(g.bid, s.id, s); };
        list.appendChild(item);
      }
    }
  };
  filterInp.addEventListener("input", render);
  filterInp.focus();
  render();
}

/* new terminal */
function modalNewTerminal() {
  const beOpts = [{ id: 0, name: backendName(0) }].concat(state.backends);
  const { m, close } = modal(`<h2>New terminal</h2>
    <label>Backend<select id="nt-be">${beOpts.map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join("")}</select></label>
    <label>Command <span style="text-transform:none">(optional)</span><input type="text" id="nt-cmd" placeholder="default shell — or e.g. ssh user@host"></label>
    <div class="m-btns"><button class="btn" id="nt-cancel">Cancel</button><button class="btn btn-pri" id="nt-go">Open</button></div>`);
  m.querySelector("#nt-cancel").onclick = close;
  const go = () => {
    const bid = parseInt(m.querySelector("#nt-be").value, 10);
    const cmd = m.querySelector("#nt-cmd").value.trim();
    close();
    openTermTab(bid, cmd);
  };
  m.querySelector("#nt-go").onclick = go;
  m.querySelector("#nt-cmd").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
}

/* switch engine */
function modalSwitchEngine(view) {
  const s = view.session;
  if (!s) return;
  const engines = state.engines;
  const { m, close } = modal(`<h2>Switch engine</h2>
    <p class="hint">The session keeps its transcript and working directory. The new engine starts a fresh
    native session seeded with a handoff of the conversation so far. Same-engine reseed is allowed
    (rebuilds context from the transcript).</p>
    <div class="engine-pick" id="se-engines"></div>
    <div class="m-btns"><button class="btn" id="se-cancel">Cancel</button><button class="btn btn-pri" id="se-go">Switch</button></div>`);
  const box = m.querySelector("#se-engines");
  let pick = s.engine === "claude" ? "codex" : "claude";
  const render = () => {
    box.innerHTML = "";
    for (const e2 of engines) {
      const card = el("div", "ep" + (pick === e2.key ? " sel" : ""));
      card.innerHTML = `<div class="ep-ico prov ${PROVIDERS[e2.key] ? "prov-" + PROVIDERS[e2.key] : ""}">${PROVIDERS[e2.key] ? "" : esc((e2.key[0] || "?").toUpperCase())}</div>
        <div class="ep-name">${esc(e2.label)}</div>
        <div class="ep-sub">${e2.key === s.engine ? "current (reseed)" : (e2.auth === "ok" ? "ready" : "no auth")}</div>`;
      card.onclick = () => { pick = e2.key; render(); };
      box.appendChild(card);
    }
  };
  render();
  m.querySelector("#se-cancel").onclick = close;
  m.querySelector("#se-go").onclick = async () => {
    try {
      const r = await api(view.tab.bid, `sessions/${view.tab.sid}/switch`, { method: "POST", body: { engine: pick } });
      view.session = r.session;
      view.updateHead();
      close();
      toast(`switched to ${pick}`, "ok");
    } catch (e) { toast(e.message, "error"); }
  };
}

/* Optical label centring. Flexbox centres a button's line box, but a font's
   ascent, descent and cap height are not symmetric about it, so the cap band
   can land up to a pixel below the geometric centre - Segoe UI is the worst
   offender, SF and Roboto are already square. Measure the live font once and
   publish the correction as a bottom pad: shrinking the content box lifts the
   label by half the pad. No metrics => 0, i.e. plain geometric centring. */
function syncLabelNudge() {
  try {
    const ref = el("span", "btn");
    ref.style.cssText = "position:absolute;left:-9999px;top:0;visibility:hidden";
    document.body.appendChild(ref);
    const cs = getComputedStyle(ref);
    const size = parseFloat(cs.fontSize);
    const font = `${cs.fontStyle} ${cs.fontWeight} ${size}px ${cs.fontFamily}`;
    ref.remove();

    /* where the browser actually puts the baseline inside a line-height:1 box:
       a zero-height inline-block's bottom edge sits exactly on it. Block probe,
       so its own top edge is the line box top rather than the font's content box */
    const probe = document.createElement("div");
    /* line-height comes after the font shorthand, which resets it */
    probe.style.cssText = "position:absolute;left:-9999px;top:0;visibility:hidden;" +
      "display:block;width:max-content;font:" + font + ";line-height:1";
    probe.textContent = "H";
    const strut = document.createElement("span");
    strut.style.cssText = "display:inline-block;width:0;height:0";
    probe.appendChild(strut);
    document.body.appendChild(probe);
    const box = probe.getBoundingClientRect();
    const baseline = strut.getBoundingClientRect().bottom - box.top;
    probe.remove();

    const ctx = document.createElement("canvas").getContext("2d");
    ctx.font = font;
    const cap = ctx.measureText("H").actualBoundingBoxAscent;   // ink above the baseline

    const low = baseline - cap / 2 - size / 2;   // >0: the cap band sits that low
    if (!size || !cap || !isFinite(low) || low <= 0.1) return;
    document.documentElement.style.setProperty("--label-pb",
      (2 * Math.min(low, 1.5) / size).toFixed(4) + "em");
  } catch (e) { /* keep the geometric centre */ }
}

/* ================= go ================= */
syncLabelNudge();
initAuth().catch(e => {
  toast("failed to reach backend: " + e.message, "error");
  showAuth("login");
});
