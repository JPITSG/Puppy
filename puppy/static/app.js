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
/* Close buttons draw their cross rather than setting a "×": the glyph's ink
   sits off the centre of its em box (measured ~1px high, ~0.5px left in the UI
   font), and no amount of flex centring moves ink the font placed off-axis. */
function xIcon(size) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 12 12");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(NS, "path");
  p.setAttribute("d", "M3.5 3.5 L8.5 8.5 M8.5 3.5 L3.5 8.5");
  p.setAttribute("stroke", "currentColor");
  p.setAttribute("stroke-width", "1.5");
  p.setAttribute("stroke-linecap", "round");
  p.setAttribute("fill", "none");
  svg.appendChild(p);
  return svg;
}

function gearIcon(size) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const gear = document.createElementNS(NS, "path");
  gear.setAttribute("d", "M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.09a2 2 0 0 1 1 1.74v.5a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.38a2 2 0 0 0-.73-2.73l-.15-.09a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z");
  gear.setAttribute("fill", "none");
  gear.setAttribute("stroke", "currentColor");
  gear.setAttribute("stroke-width", "2");
  gear.setAttribute("stroke-linecap", "round");
  gear.setAttribute("stroke-linejoin", "round");
  const hub = document.createElementNS(NS, "circle");
  hub.setAttribute("cx", "12");
  hub.setAttribute("cy", "12");
  hub.setAttribute("r", "3");
  hub.setAttribute("fill", "none");
  hub.setAttribute("stroke", "currentColor");
  hub.setAttribute("stroke-width", "2");
  svg.appendChild(gear);
  svg.appendChild(hub);
  return svg;
}

function copyIcon(done = false) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", "14");
  svg.setAttribute("height", "14");
  svg.setAttribute("aria-hidden", "true");
  if (done) {
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", "M3 8.3 6.3 11.5 13 4.8");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "1.7");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.appendChild(path);
  } else {
    for (const [x, y] of [[5, 2], [2, 5]]) {
      const rect = document.createElementNS(NS, "rect");
      rect.setAttribute("x", x); rect.setAttribute("y", y);
      rect.setAttribute("width", "9"); rect.setAttribute("height", "9");
      rect.setAttribute("rx", "1.2"); rect.setAttribute("fill", "none");
      rect.setAttribute("stroke", "currentColor"); rect.setAttribute("stroke-width", "1.35");
      svg.appendChild(rect);
    }
  }
  return svg;
}

/* A compact transport indicator for backend rows. Shape as well as colour
   carries the state, so the distinction survives colour-vision differences:
   encrypted connections get a check; cleartext connections get an X. */
function transportShieldIcon(encrypted) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 18 18");
  svg.setAttribute("width", "18");
  svg.setAttribute("height", "18");
  svg.setAttribute("aria-hidden", "true");

  const shield = document.createElementNS(NS, "path");
  shield.setAttribute("d", "M9 1.7 15 4v4.25c0 3.7-2.25 6.35-6 8-3.75-1.65-6-4.3-6-8V4L9 1.7Z");
  shield.setAttribute("fill", "currentColor");
  shield.setAttribute("fill-opacity", ".1");
  shield.setAttribute("stroke", "currentColor");
  shield.setAttribute("stroke-width", "1.1");
  shield.setAttribute("stroke-linejoin", "round");
  svg.appendChild(shield);

  const mark = document.createElementNS(NS, "path");
  mark.setAttribute("d", encrypted
    ? "M5.65 8.7 7.75 10.8 12.25 6.3"
    : "M6.35 6.45 11.65 11.55 M11.65 6.45 6.35 11.55");
  mark.setAttribute("fill", "none");
  mark.setAttribute("stroke", "currentColor");
  mark.setAttribute("stroke-width", "1.2");
  mark.setAttribute("stroke-linecap", "round");
  mark.setAttribute("stroke-linejoin", "round");
  svg.appendChild(mark);
  return svg;
}

const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

function toast(text, level = "info", ms = 4200) {
  const t = el("div", "toast " + (level === "error" ? "err" : level === "ok" ? "ok" : ""), text);
  $("toasts").appendChild(t);
  setTimeout(() => t.remove(), ms);
}

/* Clipboard.writeText is unavailable on some plain-HTTP deployments. Keep one
   fallback for every copy surface instead of letting those buttons fail there. */
async function writeClipboardText(text) {
  const value = String(text == null ? "" : text);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try { await navigator.clipboard.writeText(value); return; }
    catch (e) { /* fall through to the selection-based path */ }
  }
  const ta = el("textarea");
  const active = document.activeElement;
  ta.value = value;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0;pointer-events:none";
  document.body.appendChild(ta);
  ta.select();
  let copied = false;
  try { copied = !!document.execCommand("copy"); }
  finally {
    ta.remove();
    if (active && active.focus) {
      try { active.focus({ preventScroll: true }); } catch (e) { active.focus(); }
    }
  }
  if (!copied) throw new Error("clipboard unavailable");
}

async function copyWithToast(text, message = "copied") {
  try { await writeClipboardText(text); toast(message); return true; }
  catch (e) { toast("copy failed", "error"); return false; }
}

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
function fmtTokens(n) {
  if (n == null) return "";
  return n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
}
function cmpVersion(a, b) {
  const parse = (v) => /^\d+\.\d+\.\d+$/.test(v || "") ? v.split(".").map(Number) : null;
  const av = parse(a), bv = parse(b);
  if (!av || !bv) return null;
  for (let i = 0; i < 3; i++) if (av[i] !== bv[i]) return av[i] < bv[i] ? -1 : 1;
  return 0;
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

function storedStringSet(key) {
  try {
    const values = JSON.parse(lsGet(key) || "[]");
    return new Set(Array.isArray(values) ? values.map(String) : []);
  } catch (_) {
    return new Set();
  }
}

function saveStringSet(key, values) {
  try { lsSet(key, JSON.stringify([...values])); } catch (_) { /* storage is optional */ }
}

function snapshotBrowserState() {
  saveTabs();
  const values = {};
  const namespace = LS_NS ? LS_NS + ":" : "";
  const prefix = namespace + "puppy.";
  try {
    for (let index = 0; index < localStorage.length; index++) {
      const rawKey = localStorage.key(index);
      if (!rawKey || !rawKey.startsWith(prefix)) continue;
      const value = localStorage.getItem(rawKey);
      if (value !== null) values[rawKey.slice(namespace.length)] = value;
    }
  } catch (_) { /* browser storage is optional */ }
  return values;
}

function listenerHandoffBrowserState() {
  const values = snapshotBrowserState();
  const kept = {};
  let bytes = 0;
  /* Keep well below the backup format's 2 MiB UI-state ceiling. Extremely
     large drafts remain untouched at the old origin instead of preventing a
     listener restart; ordinary tabs, theme, layout and drafts transfer. */
  const entries = Object.entries(values).sort(([a], [b]) =>
    Number(a.startsWith("puppy.draft.")) - Number(b.startsWith("puppy.draft.")));
  for (const [key, value] of entries) {
    const size = typeof TextEncoder !== "undefined"
      ? new TextEncoder().encode(key + value).length : (key.length + value.length) * 2;
    if (bytes + size > 1536 * 1024) continue;
    kept[key] = value;
    bytes += size;
  }
  return kept;
}

function followListenerHandoff(handoff) {
  const back = el("div", "modal-backdrop listener-handoff-backdrop");
  const m = el("div", "modal listener-handoff-modal");
  const title = el("h2", "", "Restart queued");
  const status = el("p", "listener-handoff-status",
    "Active work will finish first. Waiting for Puppy on the new listener…");
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const progress = el("div", "listener-handoff-progress");
  progress.setAttribute("aria-hidden", "true");
  for (let i = 0; i < 3; i++) progress.appendChild(el("span"));
  const help = el("p", "listener-handoff-help");
  help.append("This page will reconnect automatically. If your browser blocks the check, ");
  const direct = el("a", "", "open the verified listener");
  direct.href = handoff.claim_url;
  direct.rel = "noreferrer";
  help.appendChild(direct);
  help.append(".");
  m.append(title, status, progress, help);
  back.appendChild(m);
  $("modal-root").replaceChildren(back);

  const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  const deadline = Date.now() + Math.max(60, Number(handoff.expires_in) || 2400) * 1000;
  let delay = 500;
  (async () => {
    while (Date.now() < deadline) {
      try {
        const response = await fetch(handoff.ready_url, {
          method: "GET", mode: "cors", credentials: "omit", cache: "no-store",
          redirect: "error", referrerPolicy: "no-referrer",
        });
        let payload = null;
        try { payload = await response.json(); } catch (_) { /* retry below */ }
        if (response.ok && payload && payload.ready === true) {
          status.textContent = "New listener is ready. Reconnecting…";
          location.replace(handoff.claim_url);
          return;
        }
        if (response.status === 404 || response.status === 409) {
          throw new Error((payload && payload.error) || "the listener handoff expired");
        }
      } catch (error) {
        if (error && /expired|changed|not queued/i.test(error.message || "")) {
          status.textContent = `${error.message}. Use the link below or return to Settings and verify again.`;
          progress.classList.add("hidden");
          return;
        }
        /* A refused connection is the normal interval between old and new
           listeners. Keep this document alive and try the proved endpoint. */
      }
      await wait(delay);
      delay = Math.min(2500, Math.round(delay * 1.35));
    }
    status.textContent = "Puppy did not return before the secure handoff expired. Open the listener and sign in again.";
    progress.classList.add("hidden");
    direct.href = handoff.next_url;
  })();
}

function restoreBrowserState(values) {
  if (!values || typeof values !== "object" || Array.isArray(values)) return;
  const namespace = LS_NS ? LS_NS + ":" : "";
  const prefix = namespace + "puppy.";
  try {
    const remove = [];
    for (let index = 0; index < localStorage.length; index++) {
      const rawKey = localStorage.key(index);
      if (rawKey && rawKey.startsWith(prefix)) remove.push(rawKey);
    }
    remove.forEach(key => localStorage.removeItem(key));
    for (const [key, value] of Object.entries(values)) {
      if (/^puppy\.[A-Za-z0-9_.:-]{1,240}$/.test(key) && typeof value === "string")
        localStorage.setItem(namespace + key, value);
    }
  } catch (_) { /* the server state is still restored if storage is unavailable */ }
}

function fmtBytes(value) {
  const bytes = Math.max(0, Number(value) || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GiB`;
}

function fmtEndpoint(host, port) {
  const value = String(host || "");
  return `${value.includes(":") ? `[${value}]` : value}:${port}`;
}

const collapsedSessionBackends = storedStringSet("puppy.collapsed.session-backends");
const collapsedStatusBackends = storedStringSet("puppy.collapsed.status-backends");
let disclosureSeq = 0;

function tailPath(p, n = 26) {
  if (!p) return "";
  return p.length > n ? "…" + p.slice(-n) : p;
}

/* Desktop browsers render <select> popups outside the page, so their large
   native panels cannot follow Puppy's theme. Keep the native picker on touch
   devices, where it is the better control, and progressively enhance choices
   on precise pointers with one app-owned menu. */
let openChoiceControl = null;
let choiceMenuSeq = 0;

function prefersNativeChoices() {
  if (window.matchMedia)
    return !window.matchMedia("(hover:hover) and (pointer:fine)").matches;
  return typeof navigator !== "undefined" && !!navigator.maxTouchPoints;
}

function choiceSvg(kind) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 12 12");
  svg.setAttribute("width", "12");
  svg.setAttribute("height", "12");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.4");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("d", kind === "check" ? "M2.2 6.2 4.8 8.7 9.8 3.4" : "M2.5 4.5 6 8 9.5 4.5");
  svg.appendChild(path);
  return svg;
}

/* Backend sections are rebuilt whenever sessions or availability change. Keep
   their disclosure state outside the DOM, and expose a real button/panel
   relationship so the compact chevron remains keyboard and screen-reader
   accessible. */
function disclosureButton(label, body, collapsedKeys, storageKey, itemKey) {
  const key = String(itemKey);
  const button = el("button", "disclosure-toggle");
  button.type = "button";
  body.id = `disclosure-panel-${++disclosureSeq}`;
  button.setAttribute("aria-controls", body.id);
  button.appendChild(choiceSvg("arrow"));

  const sync = () => {
    const isCollapsed = collapsedKeys.has(key);
    body.hidden = isCollapsed;
    button.setAttribute("aria-expanded", isCollapsed ? "false" : "true");
    const action = isCollapsed ? "Expand" : "Collapse";
    button.title = `${action} ${label}`;
    button.setAttribute("aria-label", `${action} ${label}`);
  };
  button.onclick = () => {
    if (collapsedKeys.has(key)) collapsedKeys.delete(key);
    else collapsedKeys.add(key);
    saveStringSet(storageKey, collapsedKeys);
    sync();
  };
  sync();
  return button;
}

function choiceOptionNode(label, selected = false) {
  const row = el("button", "choice-option" + (selected ? " selected" : ""));
  row.type = "button";
  row.setAttribute("role", "option");
  row.setAttribute("aria-selected", selected ? "true" : "false");
  row.appendChild(el("span", "choice-option-label", label));
  const mark = el("span", "choice-mark");
  if (selected) mark.appendChild(choiceSvg("check"));
  row.appendChild(mark);
  return row;
}

function closeChoiceMenu(returnFocus = false) {
  const control = openChoiceControl;
  if (!control) return;
  openChoiceControl = null;
  if (control.menu) control.menu.remove();
  control.menu = null;
  control.wrap.classList.remove("open");
  control.button.setAttribute("aria-expanded", "false");
  control.button.removeAttribute("aria-controls");
  control.button.removeAttribute("aria-activedescendant");
  if (returnFocus && control.button.isConnected) control.button.focus();
}

function positionAnchoredMenu(menu, button, matchButtonWidth = false) {
  if (!menu || !button || !button.isConnected) return;
  const rect = button.getBoundingClientRect();
  menu.style.visibility = "hidden";
  menu.style.position = "fixed";
  menu.style.right = "auto";
  menu.style.bottom = "auto";
  if (matchButtonWidth) menu.style.minWidth = Math.ceil(rect.width) + "px";
  menu.style.left = "0px";
  menu.style.top = "0px";
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  const edge = 8;
  const gap = 5;
  const roomBelow = window.innerHeight - rect.bottom - edge;
  const roomAbove = rect.top - edge;
  let top = rect.bottom + gap;
  if (height > roomBelow && roomAbove > roomBelow) top = Math.max(edge, rect.top - height - gap);
  else top = Math.min(top, window.innerHeight - height - edge);
  const centered = rect.left + (rect.width - width) / 2;
  const left = Math.max(edge, Math.min(centered, window.innerWidth - width - edge));
  menu.style.left = Math.round(left) + "px";
  menu.style.top = Math.round(Math.max(edge, top)) + "px";
  menu.style.visibility = "visible";
}

function positionChoiceMenu(control) {
  positionAnchoredMenu(control.menu, control.button, true);
}

function refreshChoiceSelect(select) {
  if (select && select._choiceControl) select._choiceControl.refresh();
}

function enhanceChoiceSelect(select) {
  if (!select || select._choiceControl) return select;
  if (prefersNativeChoices()) return select;

  const label = select.closest("label");
  const fieldName = select.getAttribute("aria-label") ||
    (label && label.firstChild && label.firstChild.nodeType === 3 ?
      label.firstChild.textContent.trim() : "Choice");
  const wrap = el("span", "choice-control");
  const button = el("button", "choice-button");
  button.type = "button";
  button.setAttribute("role", "combobox");
  button.setAttribute("aria-haspopup", "listbox");
  button.setAttribute("aria-autocomplete", "none");
  button.setAttribute("aria-expanded", "false");
  const value = el("span", "choice-value");
  const arrow = el("span", "choice-arrow");
  arrow.appendChild(choiceSvg("arrow"));
  button.appendChild(value);
  button.appendChild(arrow);
  wrap.appendChild(button);
  select.classList.add("choice-native");
  select.setAttribute("aria-hidden", "true");
  select.tabIndex = -1;
  select.insertAdjacentElement("afterend", wrap);

  const control = {
    select, wrap, button, value, menu: null, activeIndex: -1,
    typeBuffer: "", typeTimer: null,
    refresh() {
      if (openChoiceControl === control) closeChoiceMenu();
      let option = select.options[select.selectedIndex];
      if (!option && select.options.length) {
        select.selectedIndex = 0;
        option = select.options[0];
      }
      const text = option ? option.textContent : "Not available";
      value.textContent = text;
      button.disabled = select.disabled || !option;
      button.title = option ? (option.title || "") : "No choices available";
      button.setAttribute("aria-label", fieldName ? `${fieldName}: ${text}` : text);
    },
  };
  select._choiceControl = control;

  const setActive = (index) => {
    if (!control.menu || index < 0 || index >= select.options.length ||
        select.options[index].disabled) return;
    control.activeIndex = index;
    control.menu.querySelectorAll(".choice-option").forEach((row, i) =>
      row.classList.toggle("active", i === index));
    const row = control.menu.querySelector(`[data-choice-index="${index}"]`);
    if (row) {
      button.setAttribute("aria-activedescendant", row.id);
      row.scrollIntoView({ block: "nearest" });
    }
  };

  const moveActive = (delta) => {
    const enabled = [...select.options].map((option, index) => option.disabled ? -1 : index)
      .filter(index => index >= 0);
    if (!enabled.length) return;
    let at = enabled.indexOf(control.activeIndex);
    if (at < 0) at = delta > 0 ? -1 : 0;
    setActive(enabled[(at + delta + enabled.length) % enabled.length]);
  };

  const choose = (index) => {
    const option = select.options[index];
    if (!option || option.disabled) return;
    select.selectedIndex = index;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    control.refresh();
    if (button.isConnected) button.focus();
  };

  const open = () => {
    if (button.disabled) return;
    if (openChoiceControl === control) return;
    closeChoiceMenu();
    closeMenusToggling(null);
    const menu = el("div", "choice-menu");
    const menuId = `choice-menu-${++choiceMenuSeq}`;
    menu.id = menuId;
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-label", fieldName || "Choices");
    [...select.options].forEach((option, index) => {
      const selected = index === select.selectedIndex;
      const row = choiceOptionNode(option.textContent, selected);
      row.id = `${menuId}-${index}`;
      row.dataset.choiceIndex = String(index);
      row.disabled = option.disabled;
      row.tabIndex = -1;
      row.title = option.title || "";
      row.onmouseenter = () => setActive(index);
      row.onmousedown = (event) => event.preventDefault();
      row.onclick = (event) => { event.stopPropagation(); choose(index); };
      menu.appendChild(row);
    });
    menu.onclick = (event) => event.stopPropagation();
    document.body.appendChild(menu);
    menu.style.maxHeight = Math.max(80, window.innerHeight - 16) + "px";
    control.menu = menu;
    control.activeIndex = select.selectedIndex;
    openChoiceControl = control;
    wrap.classList.add("open");
    button.setAttribute("aria-expanded", "true");
    button.setAttribute("aria-controls", menuId);
    setActive(control.activeIndex);
    positionChoiceMenu(control);
  };

  button.onclick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (openChoiceControl === control) closeChoiceMenu();
    else open();
  };
  button.onkeydown = (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (openChoiceControl !== control) open();
      else moveActive(event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      if (openChoiceControl !== control) return;
      event.preventDefault();
      const enabled = [...select.options].map((option, index) => option.disabled ? -1 : index)
        .filter(index => index >= 0);
      if (enabled.length) setActive(event.key === "Home" ? enabled[0] : enabled[enabled.length - 1]);
      return;
    }
    if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
      event.preventDefault();
      if (openChoiceControl === control) choose(control.activeIndex);
      else open();
      return;
    }
    if (event.key === "Escape" && openChoiceControl === control) {
      event.preventDefault();
      closeChoiceMenu(true);
      return;
    }
    if (event.key === "Tab" && openChoiceControl === control) {
      closeChoiceMenu();
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      clearTimeout(control.typeTimer);
      control.typeBuffer += event.key.toLowerCase();
      control.typeTimer = setTimeout(() => { control.typeBuffer = ""; }, 650);
      const found = [...select.options].findIndex(option => !option.disabled &&
        option.textContent.trim().toLowerCase().startsWith(control.typeBuffer));
      if (found >= 0) {
        event.preventDefault();
        if (openChoiceControl !== control) open();
        setActive(found);
      }
    }
  };
  select.addEventListener("change", () => control.refresh());
  control.refresh();
  return select;
}

window.addEventListener("resize", () => {
  closeChoiceMenu();
  closeMenusToggling(null);
});
document.addEventListener("scroll", (event) => {
  if (openChoiceControl && (!openChoiceControl.menu ||
      !openChoiceControl.menu.contains(event.target))) closeChoiceMenu();
}, true);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && openChoiceControl) {
    event.preventDefault();
    closeChoiceMenu(true);
  }
});

function isScratchWorkspace(session) {
  return !!session && session.workspace_kind === "temporary";
}

function workspaceLabel(session, n = 26) {
  if (!isScratchWorkspace(session)) return tailPath((session && session.cwd) || "", n);
  return session.workspace_missing ? "Scratch workspace expired" : "Scratch workspace";
}

function workspaceTitle(session) {
  if (!isScratchWorkspace(session)) return (session && session.cwd) || "";
  if (session.workspace_missing)
    return "The host cleared this scratch workspace. It will be recreated before the next turn.";
  return `Disposable scratch workspace · ${session.cwd}`;
}

function sessionDeleteMessage(session) {
  return isScratchWorkspace(session)
    ? "The transcript and all files in its scratch workspace are removed permanently."
    : "The puppy transcript is removed permanently. Files in the working directory are not touched.";
}

function apiPath(bid, path) {
  return bid ? `/api/b/${bid}/${path}` : `/api/${path}`;
}
async function api(bid, path, opts = {}) {
  const o = { headers: {}, ...opts };
  const timeoutMs = Math.max(0, Number(o.timeoutMs) || 0);
  delete o.timeoutMs;
  let timeout = null;
  let controller = null;
  if (timeoutMs && !o.signal && typeof AbortController !== "undefined") {
    controller = new AbortController();
    o.signal = controller.signal;
    timeout = setTimeout(() => controller.abort(), timeoutMs);
  }
  if (o.body !== undefined && typeof o.body !== "string") {
    o.body = JSON.stringify(o.body);
    o.headers["Content-Type"] = "application/json";
  }
  try {
    let r;
    try {
      r = await fetch(apiPath(bid, path), o);
    } catch (e) {
      if (controller && controller.signal.aborted) throw new Error("request timed out");
      throw new Error("network error");
    }
    /* A proxied backend can reject its controller token with 401 while this
       browser's local session remains perfectly valid. Only a local 401 means
       the WebUI itself must return to the sign-in screen. */
    if (r.status === 401 && !bid) { showAuth(); throw new Error("auth required"); }
    let data = null;
    try { data = await r.json(); } catch (e) { /* non-json */ }
    if (!r.ok) {
      const error = new Error((data && data.error) || `HTTP ${r.status}`);
      error.status = r.status;
      error.data = data;
      throw error;
    }
    return data;
  } finally {
    if (timeout !== null) clearTimeout(timeout);
  }
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

/* Engine output sometimes uses Markdown's link syntax for editor-style file
   references. Those targets are meaningful to a local IDE, not to this web
   page, where clicking them only produces a bogus same-origin request. Keep
   genuine web URLs clickable and unwrap every other link back to its contents. */
function decorateMarkdownLinks(root) {
  root.querySelectorAll("a").forEach(a => {
    let protocol = "";
    try { protocol = new URL(a.getAttribute("href") || "").protocol; }
    catch (error) { /* a relative path is not a web URL */ }
    if (protocol !== "http:" && protocol !== "https:") {
      a.replaceWith(...a.childNodes);
      return;
    }
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  });
}

const PLAIN_CODE_LANGS = new Set([
  "", "text", "txt", "plain", "plaintext", "console", "terminal", "shell-session", "none",
]);
const OUTPUT_CODE_LANGS = new Set(["output", "log", "logs", "trace", "traceback", "stdout", "stderr"]);
const SHELL_CODE_LANGS = new Set(["bash", "sh", "shell", "zsh", "fish", "powershell", "ps1", "cmd", "bat"]);
const COMMAND_NAMES = new Set((
  "bash sh zsh fish pwsh powershell cmd env export source python node deno bun npm npx pnpm yarn " +
  "pip pipx uv git gh curl wget ssh scp rsync cd ls cp mv rm mkdir rmdir touch chmod chown cat " +
  "sed awk grep rg find xargs printf echo tee make cmake ninja docker podman kubectl helm systemctl " +
  "service supervisorctl go cargo rustc java javac gradle mvn dotnet terraform tofu ansible apt dnf yum brew"
).split(" "));

function codeLanguage(code) {
  for (const cls of code.classList) if (cls.startsWith("language-")) return cls.slice(9).toLowerCase();
  return "";
}

function lineLooksLikeCommand(line) {
  const value = line.trim().replace(/^(?:[$>#]\s*)/, "").replace(/^sudo\s+/i, "");
  const match = value.match(/^(?:"([^"]+)"|'([^']+)'|(\S+))/);
  if (!match) return false;
  const executable = (match[1] || match[2] || match[3]).split(/[\\/]/).pop().toLowerCase()
    .replace(/\.exe$/, "");
  return COMMAND_NAMES.has(executable) || /^(?:python|pip)\d+(?:\.\d+)?$/.test(executable);
}

/* Explicit programming/config languages are intentional snippets. Plain or
   unlabeled fences need stronger evidence so quoted output and incidental
   identifiers do not acquire a copy control. */
function probablyCopyableCode(code) {
  const text = String(code.textContent || "").replace(/\n$/, "").trim();
  if (text.length < 4) return false;
  const lang = codeLanguage(code);
  if (OUTPUT_CODE_LANGS.has(lang)) return false;
  if ((PLAIN_CODE_LANGS.has(lang) || SHELL_CODE_LANGS.has(lang)) &&
      /(^|\s)(?:\.{3}|…)(?=\s|$)/.test(text)) return false; // incomplete command/example
  if (!PLAIN_CODE_LANGS.has(lang)) return true;

  const lines = text.split("\n").filter(line => line.trim());
  const syntax = /^\s*(?:const|let|var|function|class|def|async\s+def|import|from|package|func|fn|return|if|for|while|select|insert|update|delete|create|alter)\b/i;
  const assignment = /^\s*(?:[-?]\s*)?[A-Za-z_][\w.-]*\s*[:=]/;
  const equals = /^\s*[A-Za-z_][\w.-]*\s*=/;
  const punctuation = /(?:=>|\$\(|\$\{|&&|\|\||[{}\[\]]|<\/?[A-Za-z]|^\s*#(?:!|include|define)|["'][^"']+["']\s*:|\b[A-Za-z_$][\w.$]*\s*\([^)]*\)\s*;?\s*$|;\s*$)/;
  if (lines.length === 1)
    return lineLooksLikeCommand(lines[0]) || syntax.test(lines[0]) || equals.test(lines[0]) || punctuation.test(lines[0]);
  const hasCommand = lines.some(lineLooksLikeCommand);
  const codeLines = lines.filter(line => lineLooksLikeCommand(line) || syntax.test(line) ||
    assignment.test(line) || punctuation.test(line) || (hasCommand && /^\s*--?[\w-]+/.test(line))).length;
  return codeLines >= 2;
}

function decorateCodeBlocks(root) {
  root.querySelectorAll("pre > code").forEach(code => {
    if (!probablyCopyableCode(code)) return;
    const pre = code.parentElement;
    if (!pre || pre.parentElement.classList.contains("code-block")) return;
    const wrap = el("div", "code-block");
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);
    const button = el("button", "code-copy");
    button.type = "button";
    button.title = "Copy code";
    button.setAttribute("aria-label", "Copy code");
    button.appendChild(copyIcon());
    button.onclick = async (e) => {
      e.preventDefault(); e.stopPropagation();
      try {
        await writeClipboardText(String(code.textContent || "").replace(/\n$/, ""));
        clearTimeout(button._copyReset);
        button.classList.add("done");
        button.replaceChildren(copyIcon(true));
        button.title = "Copied";
        button.setAttribute("aria-label", "Copied");
        button._copyReset = setTimeout(() => {
          if (!button.isConnected) return;
          button.classList.remove("done");
          button.replaceChildren(copyIcon());
          button.title = "Copy code";
          button.setAttribute("aria-label", "Copy code");
        }, 1400);
      } catch (err) { toast("copy failed", "error"); }
    };
    wrap.appendChild(button);
  });
}

/* append text to node with URLs as clickable new-tab links (DOM-built, no innerHTML) */
function linkifyInto(node, text) {
  text = String(text == null ? "" : text);
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
  usageRefresh: null,     // local account-usage refresh metadata
  localEngineCheckedAt: 0,
  backends: [],           // remote backends [{id,name,url}]
  sessions: [],           // local sessions (live via updates ws)
  remoteSessions: {},     // bid -> sessions[]
  remoteOk: {},           // bid -> bool
  remoteErrors: {},       // bid -> latest reachability error
  engCache: {},           // bid -> engines[]
  remoteEngineErrors: {}, // bid -> latest engine-status error (node can still be reachable)
  remoteEngineCheckedAt: {},
  remoteNodeCheckedAt: {},
  remoteUsageRefresh: {}, // bid -> account-usage refresh metadata
  tabs: [],               // [{id,type,bid,sid,title,cmd}]
  active: null,           // tab id
  showArchived: false,
  views: {},              // tab id -> view object
};

const REMOTE_POLL_INTERVAL = 12000;
const REMOTE_POLL_TIMEOUT = 5000;
const REMOTE_ENGINE_REFRESH = 60000;
const ENGINE_POLL_TIMEOUT = 15000;
const UPGRADE_READINESS_INTERVAL = 2000;
const UPGRADE_READINESS_TIMEOUT = 4000;
const remotePollSequence = {};
let remotePollTimer = null;
let remotePollingGeneration = 0;

/* Browser-clock anchors for uninterrupted work blocks. The server sends both
   active_since and server_time so a controller can display a remote duration
   without assuming that the two machines' wall clocks agree. */
const sessionActivityAnchors = new Map();

function sessionActivityKey(bid, sid) {
  return `${bid || 0}:${sid}`;
}

function ingestOneSessionActivity(bid, session, serverTime, receivedAt) {
  const key = sessionActivityKey(bid, session.id);
  if (session.status !== "running") {
    sessionActivityAnchors.delete(key);
    return;
  }

  const activeSince = Number(session.active_since);
  const serverNow = Number(serverTime);
  let candidate = null;
  if (Number.isFinite(activeSince) && activeSince > 0 &&
      Number.isFinite(serverNow) && serverNow >= activeSince) {
    candidate = receivedAt - (serverNow - activeSince) * 1000;
  } else if (Number.isFinite(activeSince) && activeSince > 0 &&
             !sessionActivityAnchors.has(key)) {
    /* Compatibility with an additive implementation that supplies the start
       but not its server clock. This can be skewed, so use it only initially. */
    candidate = activeSince * 1000;
  } else if (!sessionActivityAnchors.has(key)) {
    /* Older backends have no timing fields. Start at first observation; the
       status remains useful even though the elapsed value is approximate. */
    candidate = receivedAt;
  }

  if (candidate !== null) {
    const current = sessionActivityAnchors.get(key);
    if (current === undefined || Math.abs(current - candidate) > 2000)
      sessionActivityAnchors.set(key, candidate);
  }
}

function ingestSessionActivity(bid, sessions, serverTime) {
  const receivedAt = Date.now();
  const prefix = `${bid || 0}:`;
  const seen = new Set();
  for (const session of sessions) {
    const key = sessionActivityKey(bid, session.id);
    seen.add(key);
    ingestOneSessionActivity(bid, session, serverTime, receivedAt);
  }
  for (const key of sessionActivityAnchors.keys()) {
    if (key.startsWith(prefix) && !seen.has(key)) sessionActivityAnchors.delete(key);
  }
}

function reconcileRemoteState() {
  const live = new Set(state.backends.map(backend => String(backend.id)));
  for (const bucket of [state.remoteSessions, state.remoteOk, state.remoteErrors,
                        state.engCache, state.remoteEngineErrors,
                        state.remoteEngineCheckedAt, state.remoteNodeCheckedAt,
                        state.remoteUsageRefresh,
                        remotePollSequence]) {
    for (const id of Object.keys(bucket)) if (!live.has(String(id))) delete bucket[id];
  }
  for (const key of sessionActivityAnchors.keys()) {
    const bid = key.slice(0, key.indexOf(":"));
    if (bid !== "0" && !live.has(bid)) sessionActivityAnchors.delete(key);
  }
}

function remoteAvailability(bid) {
  return state.remoteOk[bid] === false ? "bad" :
    state.remoteOk[bid] === true ? "ok" : "pending";
}

function remoteAvailabilityTitle(bid) {
  const status = remoteAvailability(bid);
  if (status === "pending") return "checking";
  if (status === "bad")
    return `unavailable${state.remoteErrors[bid] ? ": " + state.remoteErrors[bid] : ""}`;
  return state.remoteEngineErrors[bid] ?
    `available · engine status unavailable: ${state.remoteEngineErrors[bid]}` : "available";
}

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
  state.authed = false;
  stopRemotePolling();
  if (updatesWs) try { updatesWs.close(); } catch (error) {}
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
let updatesReconnectTimer = null;
let updatesConnectionSequence = 0;

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
  startRemotePolling();
}

async function refreshState() {
  const s = await api(0, "state");
  state.instance = s.instance_name;
  state.sessionColors = s.session_colors || [];
  state.engines = Array.isArray(s.engines) ? s.engines : [];
  state.usageRefresh = s.usage_refresh || state.usageRefresh;
  state.localEngineCheckedAt = 0;
  state.engMap = {};
  state.engines.forEach(e => state.engMap[e.key] = e);
  state.backends = Array.isArray(s.backends) ? s.backends : [];
  state.sessions = Array.isArray(s.sessions) ? s.sessions : [];
  ingestSessionActivity(0, state.sessions, s.server_time);
  reconcileRemoteState();
  state.defaultCwd = s.default_cwd || "/";
  renderSidebar();
}

function connectUpdates() {
  if (!state.authed) return;
  if (updatesWs && (updatesWs.readyState === 0 || updatesWs.readyState === 1)) return;
  if (updatesReconnectTimer !== null) {
    clearTimeout(updatesReconnectTimer);
    updatesReconnectTimer = null;
  }
  const sequence = ++updatesConnectionSequence;
  const ws = new WebSocket(wsUrl(0, "ws/updates"));
  updatesWs = ws;
  ws.onopen = () => {
    if (sequence !== updatesConnectionSequence || updatesWs !== ws) {
      try { ws.close(); } catch (error) {}
      return;
    }
    updatesRetry = 800;
    $("conn-dot").classList.add("ok");
  };
  ws.onmessage = (ev) => {
    if (sequence !== updatesConnectionSequence || updatesWs !== ws) return;
    try {
      const d = JSON.parse(ev.data);
      if (d.type === "sessions" && Array.isArray(d.sessions)) {
        state.sessions = d.sessions;
        ingestSessionActivity(0, state.sessions, d.server_time);
        renderSidebar();
        syncTabsWithSessions();
      } else if (d.type === "backends" && Array.isArray(d.backends)) {
        state.backends = d.backends;
        reconcileRemoteState();
        renderSidebar();
        syncRemoteStateViews();
      }
    } catch (e) {}
  };
  ws.onclose = () => {
    if (sequence !== updatesConnectionSequence || updatesWs !== ws) return;
    updatesWs = null;
    $("conn-dot").classList.remove("ok");
    if (!state.authed) return;
    const delay = Math.round(updatesRetry * (.85 + Math.random() * .3));
    updatesRetry = Math.min(updatesRetry * 1.6, 15000);
    updatesReconnectTimer = setTimeout(() => {
      updatesReconnectTimer = null;
      connectUpdates();
    }, delay);
  };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}

function remotePollIsCurrent(bid, sequence) {
  return remotePollSequence[bid] === sequence &&
    state.backends.some(backend => backend.id === bid);
}

function syncRemoteStateViews() {
  try { renderSidebar(); } catch (error) { console.warn("remote sidebar update failed", error); }
  for (const view of Object.values(state.views)) {
    if (!view || typeof view.syncRemoteState !== "function") continue;
    try { view.syncRemoteState(); } catch (error) { console.warn("remote state view update failed", error); }
  }
}

async function pollLocalEngines(forceEngines = false) {
  const now = Date.now();
  if (!forceEngines && now - Number(state.localEngineCheckedAt || 0) < REMOTE_ENGINE_REFRESH)
    return;
  try {
    const payload = await api(0, "engines", { timeoutMs: ENGINE_POLL_TIMEOUT });
    if (!payload || !Array.isArray(payload.engines))
      throw new Error("instance returned an invalid engines response");
    state.engines = payload.engines;
    state.engMap = {};
    state.engines.forEach(engine => state.engMap[engine.key] = engine);
    state.usageRefresh = payload.usage_refresh || state.usageRefresh;
    state.localEngineCheckedAt = Date.now();
  } catch (error) {
    /* A broken local request should not turn the 12-second session poll into
       a tight engine-status retry loop. The normal one-minute cadence retries. */
    state.localEngineCheckedAt = Date.now();
    console.warn("local engine status refresh failed", error);
  }
}

async function pollRemoteBackend(backend, forceEngines = false) {
  const bid = backend.id;
  const sequence = (remotePollSequence[bid] || 0) + 1;
  remotePollSequence[bid] = sequence;
  const wasReachable = state.remoteOk[bid] === true;
  let payload = null;
  let failure = null;

  /* A backend restart can invalidate one pooled connection while the new
     listener is already healthy. Confirm one failure before publishing it. */
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      payload = await api(bid, "sessions", { timeoutMs: REMOTE_POLL_TIMEOUT });
      if (!payload || !Array.isArray(payload.sessions))
        throw new Error("backend returned an invalid sessions response");
      failure = null;
      break;
    } catch (error) {
      failure = error;
      if (attempt === 0) {
        await new Promise(resolve => setTimeout(resolve, 300));
        if (!remotePollIsCurrent(bid, sequence)) return;
      }
    }
  }
  if (!remotePollIsCurrent(bid, sequence)) return;
  if (failure) {
    state.remoteOk[bid] = false;
    state.remoteErrors[bid] = failure.message || "backend unavailable";
    return;
  }

  state.remoteSessions[bid] = payload.sessions;
  ingestSessionActivity(bid, payload.sessions, payload.server_time);
  state.remoteOk[bid] = true;
  delete state.remoteErrors[bid];

  const now = Date.now();
  const nodeIsStale = now - Number(state.remoteNodeCheckedAt[bid] || 0) >=
    REMOTE_ENGINE_REFRESH;
  if (forceEngines || nodeIsStale) {
    try {
      let node;
      try { node = await api(bid, "node", { timeoutMs: REMOTE_POLL_TIMEOUT }); }
      catch (_) { node = await api(bid, "ping", { timeoutMs: REMOTE_POLL_TIMEOUT }); }
      if (!node || node.ok !== true || typeof node.version !== "string")
        throw new Error("backend returned invalid node metadata");
      if (!remotePollIsCurrent(bid, sequence)) return;
      backend.remote_version = node.version;
      if (typeof node.role === "string") backend.role = node.role;
      if (Number.isInteger(node.protocol)) backend.protocol = node.protocol;
      if (Array.isArray(node.capabilities)) backend.capabilities = node.capabilities;
    } catch (error) {
      /* Sessions are the reachability authority. Metadata failure must not
         turn a healthy backend red or erase the last-known version. */
      console.warn(`backend ${backend.name} metadata refresh failed`, error);
    } finally {
      state.remoteNodeCheckedAt[bid] = Date.now();
    }
  }
  const hasCachedEngines = Object.prototype.hasOwnProperty.call(state.engCache, bid);
  const enginesAreStale = now - Number(state.remoteEngineCheckedAt[bid] || 0) >=
    REMOTE_ENGINE_REFRESH;
  const refreshEngines = forceEngines || !wasReachable || !hasCachedEngines ||
    enginesAreStale || !!state.remoteEngineErrors[bid];
  if (!refreshEngines) return;

  try {
    const engines = await api(bid, "engines", { timeoutMs: ENGINE_POLL_TIMEOUT });
    if (!engines || !Array.isArray(engines.engines))
      throw new Error("backend returned an invalid engines response");
    if (!remotePollIsCurrent(bid, sequence)) return;
    state.engCache[bid] = engines.engines;
    if (engines.usage_refresh) state.remoteUsageRefresh[bid] = engines.usage_refresh;
    state.remoteEngineCheckedAt[bid] = Date.now();
    delete state.remoteEngineErrors[bid];
  } catch (error) {
    if (!remotePollIsCurrent(bid, sequence)) return;
    /* Sessions proved the node is reachable. Keep last-known engine data and
       retry this narrower status request next cycle instead of marking the
       whole backend down. */
    state.remoteEngineErrors[bid] = error.message || "engine status unavailable";
  }
}

async function pollRemotes(options = {}) {
  const forceEngines = !!options.forceEngines;
  const backends = [...state.backends];
  await Promise.all([
    pollLocalEngines(forceEngines),
    ...backends.map(backend => pollRemoteBackend(backend, forceEngines)),
  ]);
  reconcileRemoteState();
  syncRemoteStateViews();
}

function startRemotePolling() {
  const generation = ++remotePollingGeneration;
  const tick = async () => {
    remotePollTimer = null;
    if (!state.authed || generation !== remotePollingGeneration) return;
    try { await pollRemotes(); }
    catch (error) { console.warn("remote poll failed", error); }
    finally {
      if (state.authed && generation === remotePollingGeneration)
        remotePollTimer = setTimeout(tick, REMOTE_POLL_INTERVAL);
    }
  };
  if (remotePollTimer !== null) clearTimeout(remotePollTimer);
  tick();
}

function stopRemotePolling() {
  remotePollingGeneration++;
  if (remotePollTimer !== null) clearTimeout(remotePollTimer);
  remotePollTimer = null;
}

/* With the controller proxy's upstream-first handshake, a remote socket open
   or message is direct proof that the backend is reachable. Let that newer
   evidence invalidate an older in-flight HTTP failure and refresh the lists. */
function noteRemoteSocketReachable(bid) {
  if (!bid) return;
  const backend = state.backends.find(item => String(item.id) === String(bid));
  if (!backend) return;
  bid = backend.id;
  if (state.remoteOk[bid] === true) return;
  remotePollSequence[bid] = (remotePollSequence[bid] || 0) + 1;
  state.remoteOk[bid] = true;
  delete state.remoteErrors[bid];
  syncRemoteStateViews();
  pollRemotes({ forceEngines: true })
    .catch(error => console.warn("socket recovery poll failed", error));
}

window.addEventListener("online", () => {
  if (state.authed) {
    connectUpdates();
    pollRemotes({ forceEngines: true })
      .catch(error => console.warn("online remote poll failed", error));
  }
});
document.addEventListener("visibilitychange", () => {
  if (state.authed && document.visibilityState === "visible")
    pollRemotes({ forceEngines: true })
      .catch(error => console.warn("resume remote poll failed", error));
});

/* ================= sidebar ================= */
function sessionsFor(bid) {
  return bid ? (state.remoteSessions[bid] || []) : state.sessions;
}
function backendName(bid) {
  if (!bid) return state.instance || "local";
  const b = state.backends.find(x => x.id === bid);
  return b ? b.name : `backend ${bid}`;
}

function backendLocationVersion(backend) {
  return [backend.url, backend.remote_version ? `v${backend.remote_version}` : ""]
    .filter(Boolean).join(" · ");
}

function formatSessionActivity(startedAt, now = Date.now()) {
  const start = Number(startedAt);
  const total = Number.isFinite(start) ?
    Math.max(0, Math.floor((now - start) / 1000)) : 0;
  const seconds = String(total % 60).padStart(2, "0");
  const minutes = String(Math.floor(total / 60) % 60).padStart(2, "0");
  if (total < 3600) return `${minutes}:${seconds}`;
  const hours = String(Math.floor(total / 3600)).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

function updateSessionActivityLabels() {
  const now = Date.now();
  document.querySelectorAll(".si-be.active-time[data-activity-key]").forEach(label => {
    const startedAt = sessionActivityAnchors.get(label.dataset.activityKey);
    if (startedAt === undefined) return;
    const value = formatSessionActivity(startedAt, now);
    if (label.textContent !== value) label.textContent = value;
  });
}

function noteSessionActivity(bid, sid, running, activeSince, serverTime) {
  const sessions = sessionsFor(bid);
  const session = sessions.find(item => String(item.id) === String(sid));
  const sample = session || { id: sid };
  sample.status = running ? "running" : "idle";
  sample.active_since = running ? (activeSince == null ? sample.active_since : activeSince) : null;
  ingestOneSessionActivity(bid, sample, serverTime, Date.now());
  renderSidebar();
  renderTabs();
}

setInterval(updateSessionActivityLabels, 1000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") updateSessionActivityLabels();
});

function backendHasCapability(backend, capability) {
  if (!backend || Number(backend.protocol || 0) === 0) return true; // legacy full nodes
  return Array.isArray(backend.capabilities) && backend.capabilities.includes(capability);
}

function backendSupportsScratch(bid) {
  if (!bid) return true;
  const backend = state.backends.find(b => b.id === bid);
  // Protocol-0 nodes ignore unknown create fields, so they must not be offered
  // a scratch choice that would silently become a normal directory session.
  return !!backend && Number(backend.protocol || 0) > 0 &&
    Array.isArray(backend.capabilities) && backend.capabilities.includes("temporary-workspaces");
}

function backendSupportsUsageRefresh(bid) {
  if (!bid) return true;
  const backend = state.backends.find(b => b.id === bid);
  /* Unlike the older execution surface, this is a new API route. Never infer
     it for protocol-0 nodes: an explicit capability keeps Settings from
     offering a control that the remote cannot accept. */
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("engine-usage-refresh");
}

function backendSupportsAutoUpgrade(backend) {
  /* This policy is deliberately narrower than legacy execution inference:
     only a headless node explicitly advertising the signed upgrade contract
     may be armed for unattended replacement. */
  return !!backend && backend.role === "backend" &&
    Array.isArray(backend.capabilities) && backend.capabilities.includes("remote-upgrade");
}

function renderSidebar() {
  const root = $("sess-groups");
  root.innerHTML = "";
  const groups = [{ bid: 0, name: backendName(0), ok: true, status: "ok" }]
    .concat(state.backends.map(b => {
      const status = remoteAvailability(b.id);
      return { bid: b.id, name: b.name, ok: status !== "bad", status };
    }));
  const showGroups = groups.length > 1;
  for (const g of groups) {
    const group = el("section", "sess-group");
    const body = el("div", "sess-group-body");
    if (showGroups) {
      const t = el("div", "sess-group-title");
      const dot = el("span", "gdot " + g.status);
      dot.title = g.bid ? remoteAvailabilityTitle(g.bid) : "available";
      const name = el("span", "sess-group-name", g.name);
      name.title = g.name;
      const key = g.bid ? `remote:${g.bid}` : "local";
      t.appendChild(dot);
      t.appendChild(name);
      t.appendChild(disclosureButton(`${g.name} sessions`, body,
        collapsedSessionBackends, "puppy.collapsed.session-backends", key));
      group.appendChild(t);
    }
    const allSessions = sessionsFor(g.bid);
    const list = allSessions.filter(s => state.showArchived || !s.archived);
    if (!list.length) {
      let message = "No sessions yet";
      if (!g.ok) message = "Backend unavailable";
      else if (!state.showArchived && allSessions.some(s => s.archived)) message = "Archived sessions hidden";
      else if (showGroups) message = g.bid === 0 ? "No local sessions" : "No sessions attached";
      body.appendChild(el("div", "sess-group-empty" + (showGroups ? "" : " standalone"), message));
    }
    for (const s of list) {
      const item = el("button", "sess-item" + (s.archived ? " archived" : ""));
      const tabId = `s:${g.bid}:${s.id}`;
      if (state.active === tabId) item.classList.add("active");
      const r1 = el("div", "si-row");
      r1.appendChild(sessDot(s));
      r1.appendChild(el("div", "si-name", s.name || `session ${s.id}`));
      const activity = el("span", "si-be");
      if (s.status === "running") {
        const key = sessionActivityKey(g.bid, s.id);
        if (!sessionActivityAnchors.has(key))
          ingestOneSessionActivity(g.bid, s, null, Date.now());
        activity.classList.add("active-time");
        activity.dataset.activityKey = key;
        activity.textContent = formatSessionActivity(sessionActivityAnchors.get(key));
        activity.title = "Agent active";
      } else {
        activity.classList.add("idle");
        activity.textContent = "IDLE";
        activity.title = "Session idle";
      }
      r1.appendChild(activity);
      const r2 = el("div", "si-row sub");
      r2.appendChild(provIcon(s.engine));
      const workspace = el("div", "si-sub" + (s.workspace_missing ? " warn" : ""),
        workspaceLabel(s));
      workspace.title = workspaceTitle(s);
      r2.appendChild(workspace);
      item.appendChild(r1); item.appendChild(r2);
      item.onclick = () => { openSessionTab(g.bid, s.id, s); closeDrawer(); };
      item.addEventListener("contextmenu", (e) => sessionContextMenu(e, g.bid, s));
      wireSessionDrag(item, g.bid, s.id);
      body.appendChild(item);
    }
    group.appendChild(body);
    root.appendChild(group);
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
  closeMenusToggling(null);
  const menu = el("div", "menu dyn");
  menu.style.position = "fixed";
  menu.style.left = Math.min(x, window.innerWidth - 220) + "px";
  menu.style.top = Math.min(y, window.innerHeight - 330) + "px";
  menu.style.right = "auto"; menu.style.bottom = "auto";
  document.body.appendChild(menu);
  return menu;
}

function refreshGroup(bid) {
  if (bid) pollRemotes().catch(error => console.warn("remote group refresh failed", error));
}   // local changes arrive via the updates websocket

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
  add("Dot color", () => {
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
  add("Switch engine", () => modalSwitchEngine({
    session: s, tab: { bid, sid: s.id }, updateHead() { refreshGroup(bid); },
  }));
  menu.appendChild(el("div", "menu-sep"));
  add(isScratchWorkspace(s) ? "Copy workspace path" : "Copy cwd", () => copyWithToast(s.cwd));
  if (s.has_native) add("Copy native session id", async () => {
    try {
      const r = await api(bid, `sessions/${s.id}`);
      await copyWithToast(r.session.native_session_id || "");
    } catch (e) { toast(e.message, "error"); }
  });
  if (isScratchWorkspace(s)) add(s.workspace_missing ? "Recreate scratch workspace" :
    "Reset scratch workspace", async () => {
    const ok = await modalConfirm(
      s.workspace_missing ? "Recreate scratch workspace?" : "Reset scratch workspace?",
      s.workspace_missing ?
        "Puppy will create a new empty workspace. The transcript is kept." :
        "All files in this scratch workspace are removed permanently. The transcript is kept.");
    if (!ok) return;
    try {
      await api(bid, `sessions/${s.id}/workspace/reset`, { method: "POST" });
      refreshGroup(bid);
      toast(s.workspace_missing ? "scratch workspace recreated" : "scratch workspace reset");
    } catch (e) { toast(e.message, "error"); }
  });
  menu.appendChild(el("div", "menu-sep"));
  add(s.archived ? "Unarchive" : "Archive", () => patch({ archived: !s.archived }));
  add("Delete session", async () => {
    const ok = await modalConfirm("Delete session?", sessionDeleteMessage(s));
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
  const groups = [{ bid: 0, name: backendName(0), engines: state.engines }]
    .concat(state.backends.map(b => ({
      bid: b.id, name: b.name, version: b.remote_version || "",
      engines: Object.prototype.hasOwnProperty.call(state.engCache, b.id) ? state.engCache[b.id] : null,
    })));
  const showGroups = groups.length > 1;
  for (const g of groups) {
    const group = el("div", "foot-engine-group");
    const body = el("div", "foot-engine-body");
    if (showGroups) {
      const head = el("div", "foot-engine-head");
      const ico = el("span", "foot-ico");
      const status = g.bid === 0 ? "ok" : remoteAvailability(g.bid);
      const dot = el("span", "gdot " + status);
      dot.title = g.bid ? remoteAvailabilityTitle(g.bid) : "available";
      ico.appendChild(dot);
      head.appendChild(ico);
      const name = el("span", "foot-engine-name", g.name);
      name.title = g.name;
      const key = g.bid ? `remote:${g.bid}` : "local";
      head.appendChild(name);
      if (g.bid && g.version) {
        const version = el("span", "foot-engine-version", `· v${g.version}`);
        version.title = `Backend version ${g.version}`;
        head.appendChild(version);
      }
      head.appendChild(disclosureButton(`${g.name} engine status`, body,
        collapsedStatusBackends, "puppy.collapsed.status-backends", key));
      group.appendChild(head);
    }
    if (g.bid && state.remoteOk[g.bid] === false) {
      body.appendChild(el("div", "foot-engine-empty", "backend unavailable"));
    } else if (g.engines === null) {
      body.appendChild(el("div", "foot-engine-empty", "checking engines…"));
    } else if (!g.engines.length) {
      body.appendChild(el("div", "foot-engine-empty", "no engines reported"));
    } else for (const e of g.engines) {
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
      body.appendChild(row);
    }
    group.appendChild(body);
    root.appendChild(group);
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
      if (!dragTabId) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      tab.classList.add("drag-over");
    });
    tab.addEventListener("dragleave", () => tab.classList.remove("drag-over"));
    tab.addEventListener("drop", (e) => {
      e.preventDefault();
      tab.classList.remove("drag-over");
      if (!dragTabId) return;
      const sourceId = dragTabId;
      dragTabId = null;
      if (sourceId === t.id) return;  // visible drop slot, intentionally no reorder
      const from = state.tabs.findIndex(x => x.id === sourceId);
      const to = state.tabs.findIndex(x => x.id === t.id);
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
    if (t.type === "settings") tdot.appendChild(gearIcon(12));
    tab.appendChild(tdot);
    tab.appendChild(el("span", "t-title", t.title || "tab"));
    if (t.type === "session") {
      tab.addEventListener("contextmenu", (e) => {
        const meta = findSessionMeta(t.bid, t.sid);
        if (meta) sessionContextMenu(e, t.bid, meta);
      });
    }
    const x = el("button", "t-close");
    x.appendChild(xIcon(12));
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
  const menu = $("tab-add-menu");
  const opening = menu.classList.contains("hidden");
  menu.classList.toggle("hidden");
  if (opening) positionAnchoredMenu(menu, e.currentTarget);
};
document.addEventListener("click", () => {
  $("tab-add-menu").classList.add("hidden");
  closeMenusToggling(null);
  closeChoiceMenu();
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
  document.querySelectorAll(".menu.dyn,.choice-menu.dyn").forEach(m => {
    if (anchor && m._anchor === anchor) wasOpen = true;
    if (m._anchor) m._anchor.setAttribute("aria-expanded", "false");
    m.remove();
  });
  return wasOpen;
}

/* ================= SessionView ================= */
const TOOL_ICONS = {
  Bash: "$", shell: "$", Read: "📄", Write: "✏️", Edit: "✏️", file_change: "✏️",
  Grep: "🔎", Glob: "🔎", WebSearch: "🌐", WebFetch: "🌐", Task: "🤖", TodoWrite: "☑",
};
function displayValue(value, limit = 12000) {
  let text;
  if (value == null) text = "";
  else if (typeof value === "string") text = value;
  else {
    try { text = JSON.stringify(value, null, 2); }
    catch (e) { text = String(value); }
    if (text === undefined) text = String(value);
  }
  return text.length > limit ? text.slice(0, limit) + "…" : text;
}
function toolIcon(tool) {
  if (TOOL_ICONS[tool]) return TOOL_ICONS[tool];
  const name = String(tool || "").toLowerCase();
  if (/(web|search|browse|fetch|url)/.test(name)) return "🌐";
  if (/(read|view|image)/.test(name)) return "📄";
  if (/(write|edit|patch|change|file)/.test(name)) return "✏️";
  if (/(shell|exec|command|bash)/.test(name)) return "$";
  if (/(todo|plan)/.test(name)) return "☑";
  if (/(task|agent|collab)/.test(name)) return "🤖";
  return "🔧";
}
function toolLabel(tool) {
  return String(tool || "tool").replace(/_/g, " ");
}
function toolSummary(tool, input) {
  if (input == null) return "";
  if (typeof input !== "object") return displayValue(input, 120);
  for (const key of ["command", "file_path", "path", "pattern", "query", "url"])
    if (input[key]) return displayValue(input[key], 120).replace(/\s+/g, " ");
  if (Array.isArray(input.queries))
    return displayValue(input.queries.map(x => displayValue(x, 80)).join(" · "), 120);
  if (Array.isArray(input.changes)) return displayValue(input.changes.map(c =>
    c && typeof c === "object" ? (c.path || c.file_path || displayValue(c, 50)) : displayValue(c, 50)
  ).join(", "), 120);
  return displayValue(input, 120).replace(/\s+/g, " ");
}
function toolCardNode(data, completed = false) {
  const d = data || {};
  const n = el("div", "tool-card" + (d.is_error ? " err" : ""));
  const head = el("div", "tool-head");
  head.appendChild(el("span", "t-caret", "❯"));
  head.appendChild(el("span", "t-ico", toolIcon(d.tool)));
  head.appendChild(el("span", "t-name", toolLabel(d.tool)));
  head.appendChild(linkifyInto(el("span", "t-sum"), toolSummary(d.tool, d.input)));
  const stateEl = el("span", "t-state", completed ? (d.is_error ? "✗" : "✔") : "…");
  if (completed) stateEl.classList.add(d.is_error ? "bad" : "ok");
  head.appendChild(stateEl);
  const body = el("div", "tool-body");
  if (d.input !== undefined) {
    body.appendChild(el("div", "tb-label", "input"));
    body.appendChild(linkifyInto(el("pre"), displayValue(d.input)));
  }
  head.onclick = () => n.classList.toggle("open");
  n.appendChild(head); n.appendChild(body);
  return n;
}

class SessionView {
  constructor(tab) {
    this.tab = tab;
    this.session = null;
    this.status = "idle";
    this.ws = null;
    this.closed = false;
    this.retry = 800;
    this.reconnectTimer = null;
    this.connectionSequence = 0;
    this.toolCards = {};
    this.liveEl = null;
    this.liveKind = null;
    this.statusText = "";     // header status; the transcript foot mirrors it
    this.statusRow = null;    // standalone foot row, used when no thinking block is live
    this.queued = [];         // last queue payload, re-rendered when the list expands
    this.queueOpen = false;   // whether the tail past QUEUE_ROWS is showing
    this.oldestSeq = null;
    this.history = [];        // sent messages, oldest first (shell-style recall)
    this.histIdx = null;
    this.histDraft = "";
    this.ctrlCStreak = 0;     // composer-only: second consecutive Ctrl-C clears the queue
    this.attachments = [];    // {path, url}: server path sent with the message, blob url for the preview
    this.nativeComposerChoices = prefersNativeChoices();
    this.buildDom();
    this._onResize = () => { this.syncGutter(); this.syncComposerMeta(); this.syncHeadOverflow(); this.syncQueueFade(); };
    window.addEventListener("resize", this._onResize);
    this.connect();
  }

  buildDom() {
    const root = el("div", "view chat");
    const composerChoice = (cls, key, label) => this.nativeComposerChoices ?
      `<span class="mini composer-native-choice ${cls}" title="${esc(label)}">
        <span class="mini-key">${esc(key)}:</span><span class="mini-value">auto</span>
        <select aria-label="${esc(label)}" disabled></select>
      </span>` :
      `<button type="button" class="mini ${cls}" title="${esc(label)}">
        <span class="mini-key">${esc(key)}:</span><span class="mini-value">auto</span>
      </button>`;
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
            <div class="composer-meta-viewport">
              <div class="composer-meta-scroll">
                ${composerChoice("perm", "permissions", "Permission mode")}
                ${composerChoice("model", "model", "Model")}
                ${composerChoice("effort", "effort", "Reasoning effort")}
              </div>
            </div>
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
    this.composerMeta = root.querySelector(".composer-meta-scroll");
    this.composerMetaViewport = root.querySelector(".composer-meta-viewport");
    this.composerNativeSelects = this.nativeComposerChoices ? {
      perm: root.querySelector(".composer-native-choice.perm select"),
      model: root.querySelector(".composer-native-choice.model select"),
      effort: root.querySelector(".composer-native-choice.effort select"),
    } : null;
    this.headMeta = root.querySelector(".chat-meta-scroll");
    this.headMetaViewport = root.querySelector(".chat-meta-viewport");
    this.statusEl = root.querySelector(".chat-status");
    this.approvalEl = root.querySelector(".approval");
    this.queueEl = root.querySelector(".queue-strip");
    this.attachStrip = root.querySelector(".attach-strip");
    this.headMeta.addEventListener("scroll", () => this.syncHeadOverflow(), { passive: true });
    this.composerMeta.addEventListener("scroll", () => this.syncComposerOverflow(), { passive: true });
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
      this.ctrlCStreak = 0;
      this.histIdx = null;   // manual edits exit history mode
      this.resizeComposer();
      lsSet("puppy.draft." + this.tab.id, this.ta.value);
    });
    this.ta.addEventListener("keydown", (e) => {
      if (e.isComposing) { this.ctrlCStreak = 0; return; }
      const ctrlC = e.ctrlKey && !e.metaKey && !e.altKey && (e.key === "c" || e.key === "C");
      if (e.key === "Escape" || ctrlC) {
        e.preventDefault();
        e.stopPropagation();
        if (e.repeat) return; // holding C must not accidentally erase the queue
        this.ctrlCStreak = ctrlC ? this.ctrlCStreak + 1 : 0;
        this.interrupt(this.ctrlCStreak > 1);
        return;
      }
      // Modifier keydowns between two presses do not break the sequence; typing does.
      if (!(["Control", "Shift", "Alt", "Meta"].includes(e.key))) this.ctrlCStreak = 0;
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
    this.ta.addEventListener("blur", () => { this.ctrlCStreak = 0; });
    this.ta.addEventListener("pointerdown", () => { this.ctrlCStreak = 0; });
    this.sendBtn.onclick = () => this.status === "running" ? this.interrupt() : this.submit();
    root.querySelector(".menu-btn").onclick = (e) => { e.stopPropagation(); this.showMenu(e.currentTarget); };
    if (this.nativeComposerChoices) {
      const bind = (kind, apply) => {
        const select = this.composerNativeSelects[kind];
        select.onchange = async () => {
          const value = select.value;
          select.disabled = true;
          try { await apply(value); }
          finally {
            this.syncNativeComposerChoices();
            this.syncComposerMeta();
          }
        };
      };
      bind("perm", (value) => this.patchSession({ permission_mode: value }));
      bind("model", (value) => this.applyModelChoice(value));
      bind("effort", (value) => this.patchSession({ effort: value }));
    } else {
      root.querySelector(".mini.perm").onclick = (e) => { e.stopPropagation(); this.showPermMenu(e.currentTarget); };
      root.querySelector(".mini.model").onclick = (e) => { e.stopPropagation(); this.showModelMenu(e.currentTarget); };
      root.querySelector(".mini.effort").onclick = (e) => { e.stopPropagation(); this.showEffortMenu(e.currentTarget); };
    }
  }

  connect() {
    if (this.closed) return;
    if (this.ws && (this.ws.readyState === 0 || this.ws.readyState === 1)) return;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const sequence = ++this.connectionSequence;
    const ws = new WebSocket(wsUrl(this.tab.bid, `ws/session/${this.tab.sid}`));
    this.ws = ws;
    ws.onopen = () => {
      if (this.closed || sequence !== this.connectionSequence || this.ws !== ws) {
        try { ws.close(); } catch (error) {}
        return;
      }
      noteRemoteSocketReachable(this.tab.bid);
    };
    ws.onmessage = (ev) => {
      if (sequence !== this.connectionSequence || this.ws !== ws) return;
      let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      noteRemoteSocketReachable(this.tab.bid);
      this.handle(d);
    };
    ws.onclose = () => {
      if (sequence !== this.connectionSequence || this.ws !== ws) return;
      this.ws = null;
      if (this.closed) return;
      this.setStatus("connection lost · reconnecting…");
      const delay = Math.round(this.retry * (.85 + Math.random() * .3));
      this.retry = Math.min(this.retry * 1.7, 15000);
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.connect();
      }, delay);
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }

  destroy() {
    this.closed = true;
    this.connectionSequence++;
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
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

  syncRightOverflow(sc, viewport) {
    if (!sc || !viewport || !sc.clientWidth) return;
    const moreRight = sc.scrollLeft + sc.clientWidth < sc.scrollWidth - 1;
    viewport.classList.toggle("more-right", moreRight);
  }

  syncHeadOverflow() {
    this.syncRightOverflow(this.headMeta, this.headMetaViewport);
  }

  syncComposerOverflow() {
    this.syncRightOverflow(this.composerMeta, this.composerMetaViewport);
  }

  /* Keep the controls on one line. First drop redundant key prefixes; if long
     values still do not fit, their own touch-scroll lane contains the overflow
     without ever displacing the Send / Stop button. */
  syncComposerMeta() {
    const row = this.composerRow, sc = this.composerMeta;
    if (!row || !sc || !sc.clientWidth) return;
    row.classList.remove("compact-meta");
    row.classList.toggle("compact-meta", sc.scrollWidth > sc.clientWidth + 1);
    this.syncComposerOverflow();
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
        noteSessionActivity(this.tab.bid, this.tab.sid, d.status === "running",
          d.active_since, d.server_time);
        this.retry = 800;
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
        /* New nodes tell us whether this turn flowed directly into a queued
           one. On older nodes the pre-pop queue is the closest equivalent. */
        const continued = typeof d.continued === "boolean" ?
          d.continued : this.queued.length > 0;
        this.status = continued ? "running" : "idle";
        this.clearLive();
        this.updateRunState();
        this.setStatus(continued ? "starting next queued message…" : "");
        noteSessionActivity(this.tab.bid, this.tab.sid, continued);
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
      const becameRunning = this.status !== "running";
      this.status = "running"; this.updateRunState();
      if (becameRunning) noteSessionActivity(this.tab.bid, this.tab.sid, true);
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
    cwd.textContent = workspaceLabel(s, 34);
    cwd.title = workspaceTitle(s);
    cwd.classList.toggle("warn", !!s.workspace_missing);
    this.root.querySelector(".chip.be").textContent = backendName(this.tab.bid);
    const setMini = (cls, label, value) => {
      const control = this.root.querySelector(".mini." + cls);
      control.querySelector(".mini-value").textContent = value;
      control.title = label + ": " + value;
      const select = control.querySelector("select");
      (select || control).setAttribute("aria-label", label + ": " + value);
    };
    setMini("perm", "Permission mode", s.permission_mode || "auto");
    setMini("model", "Model", s.model || "auto");
    setMini("effort", "Reasoning effort", s.effort || "auto");
    this.syncNativeComposerChoices();
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
        decorateMarkdownLinks(box);
        decorateCodeBlocks(box);
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
        const n = toolCardNode(d);
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
          body.appendChild(linkifyInto(el("pre"), displayValue(d.content) || "(empty)"));
          return null;
        }
        const n = toolCardNode({tool: d.tool || "tool_result", is_error: d.is_error}, true);
        n.classList.add("open");
        const body = n.querySelector(".tool-body");
        body.appendChild(el("div", "tb-label", d.is_error ? "error" : "result"));
        body.appendChild(linkifyInto(el("pre"), displayValue(d.content) || "(empty)"));
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
        if (d.subtype === "web_search") {
          const query = String(d.text || "").replace(/^web search:\s*/i, "");
          return toolCardNode({tool: "web_search", input: query ? {query} : undefined}, true);
        }
        const warned = d.subtype === "interrupted" || d.subtype === "model_switch" ||
          d.subtype === "workspace_reset";
        const n = el("div", "info-line" + (warned ? " warn" : ""));
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
      const x = el("button", "attach-x");
      x.appendChild(xIcon(12));
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
    this.status = "running";
    noteSessionActivity(this.tab.bid, this.tab.sid, true);
    this.updateRunState();
    this.setStatus("starting…");
  }
  interrupt(clearQueue = false) {
    if (this.ws && this.ws.readyState === 1)
      this.ws.send(JSON.stringify({ type: "interrupt", clear_queue: clearQueue }));
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
    this.queued = q;
    box.innerHTML = "";
    if (!q.length) { box.classList.add("hidden"); this.queueOpen = false; return; }
    box.classList.remove("hidden");
    box.classList.toggle("expanded", !!this.queueOpen);
    box.appendChild(el("div", "q-head", `queued · ${q.length}`));
    /* one row per message, in the order they will run. The list is capped so a
       deep queue cannot push the composer down the screen; the tail is one
       click away. The per-item cap only keeps a runaway message out of the DOM
       - each row is a single line that fades out at whatever width is going. */
    const shown = this.queueOpen ? q : q.slice(0, QUEUE_ROWS);
    shown.forEach((text, i) => {
      const row = el("div", "q-item");
      row.appendChild(el("span", "q-n", String(i + 1)));
      const t = el("span", "q-t", text.length > 200 ? text.slice(0, 199) + "…" : text);
      t.title = text;
      row.appendChild(t);
      const x = el("button", "q-x");
      x.appendChild(xIcon(12));   // even size in an even box: no half-pixel centring
      x.title = "Cancel this queued message";
      x.onclick = () => this.unqueue(i, text);   // full text, not the capped copy
      row.appendChild(x);
      box.appendChild(row);
    });
    if (q.length > shown.length) {
      const more = el("button", "q-more", `+${q.length - shown.length} more`);
      more.onclick = () => { this.queueOpen = true; this.renderQueue(this.queued); };
      box.appendChild(more);
    } else if (this.queueOpen && q.length > QUEUE_ROWS) {
      const less = el("button", "q-more", "show fewer");
      less.onclick = () => { this.queueOpen = false; this.renderQueue(this.queued); };
      box.appendChild(less);
    }
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
    add("Dot color", () => this.pickColor(anchor));
    add("Switch engine", () => modalSwitchEngine(this));
    menu.appendChild(el("div", "menu-sep"));
    add(isScratchWorkspace(this.session) ? "Copy workspace path" : "Copy cwd",
      () => copyWithToast(this.session.cwd));
    if (this.session && this.session.native_session_id)
      add("Copy native session id", () => copyWithToast(this.session.native_session_id));
    if (isScratchWorkspace(this.session))
      add(this.session.workspace_missing ? "Recreate scratch workspace" :
        "Reset scratch workspace", () => this.resetWorkspace());
    menu.appendChild(el("div", "menu-sep"));
    add(this.session && this.session.archived ? "Unarchive" : "Archive", () => this.archive());
    add("Delete session", () => this.deleteSession(), true);
    anchor.parentElement.appendChild(menu);
    positionAnchoredMenu(menu, anchor);
  }

  pickColor(anchor) {
    closeMenusToggling(null);   // always opens fresh (invoked from the ⋮ menu)
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
    positionAnchoredMenu(menu, anchor);
  }

  composerChoiceSpec(kind, native = false) {
    const s = this.session || {};
    const eng = state.engMap[s.engine];
    if (kind === "perm") return {
      options: [...((eng && eng.permission_options) || [])],
      selected: s.permission_mode || "",
    };
    if (kind === "effort") return {
      options: [...((eng && eng.effort_options) || [])],
      selected: s.effort || "",
    };
    const options = [...((eng && eng.model_options) || [])];
    const current = s.model || "";
    const custom = !!current && !options.some(option => option.value === current);
    if (native && custom) {
      options.push({ value: "__current_custom__", label: `Current: ${current}` });
      options.push({ value: "__custom__", label: "Custom…", hint: "Any model id the engine accepts" });
      return { options, selected: "__current_custom__" };
    }
    options.push({
      value: "__custom__", label: custom ? `Custom… (${current})` : "Custom…",
      hint: "Any model id the engine accepts",
    });
    return { options, selected: custom ? "__custom__" : current };
  }

  syncNativeComposerChoices() {
    if (!this.nativeComposerChoices || !this.composerNativeSelects || !this.session) return;
    for (const kind of ["perm", "model", "effort"]) {
      const select = this.composerNativeSelects[kind];
      const spec = this.composerChoiceSpec(kind, true);
      const supplied = spec.options.length > 0;
      const options = [...spec.options];
      if (supplied && !options.some(option => String(option.value) === String(spec.selected)))
        options.unshift({ value: spec.selected, label: spec.selected || "Default" });
      if (!options.length)
        options.push({ value: spec.selected, label: spec.selected || "Not available" });
      select.replaceChildren();
      for (const item of options) {
        const option = document.createElement("option");
        option.value = item.value;
        option.textContent = item.label;
        option.title = item.hint || "";
        option.disabled = !!item.disabled;
        option.selected = String(item.value) === String(spec.selected);
        select.appendChild(option);
      }
      select.disabled = !supplied;
      select.parentElement.classList.toggle("disabled", !supplied);
    }
  }

  showPermMenu(anchor) {
    if (!this.session) return;
    const spec = this.composerChoiceSpec("perm");
    this.optionMenu(anchor, spec.options, spec.selected,
      (value) => this.patchSession({ permission_mode: value }));
  }

  async patchSession(body) {
    try {
      const r = await api(this.tab.bid, `sessions/${this.tab.sid}`, { method: "PATCH", body });
      this.session = r.session; this.updateHead();
    } catch (e) { toast(e.message, "error"); }
  }

  optionMenu(anchor, opts, current, onPick) {
    if (closeMenusToggling(anchor)) return null;
    closeChoiceMenu();
    const menu = el("div", "choice-menu composer-choice-menu dyn");
    menu._anchor = anchor;
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-label", anchor.title || "Choices");
    menu.style.visibility = "hidden";
    anchor.setAttribute("aria-haspopup", "listbox");
    anchor.setAttribute("aria-expanded", "true");
    const rows = [];
    const dismiss = (returnFocus = false) => {
      menu.remove();
      anchor.setAttribute("aria-expanded", "false");
      if (returnFocus && anchor.isConnected) anchor.focus();
    };
    opts.forEach((o, index) => {
      const selected = current === o.value;
      const row = choiceOptionNode(o.label, selected);
      row.title = o.hint || "";
      row.tabIndex = selected ? 0 : -1;
      row.onmouseenter = () => rows.forEach((item, i) => item.classList.toggle("active", i === index));
      row.onfocus = row.onmouseenter;
      row.onclick = (event) => {
        event.stopPropagation();
        dismiss(true);
        onPick(o.value);
      };
      rows.push(row);
      menu.appendChild(row);
    });
    menu.onkeydown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dismiss(true);
        return;
      }
      if (event.key === "Tab") {
        dismiss();
        return;
      }
      if (!rows.length) return;
      const at = Math.max(0, rows.indexOf(document.activeElement));
      let next = null;
      if (event.key === "ArrowDown") next = (at + 1) % rows.length;
      else if (event.key === "ArrowUp") next = (at - 1 + rows.length) % rows.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = rows.length - 1;
      if (next !== null) {
        event.preventDefault();
        rows.forEach((row, index) => { row.tabIndex = index === next ? 0 : -1; });
        rows[next].focus();
      }
    };
    menu.onclick = (event) => event.stopPropagation();
    document.body.appendChild(menu);
    const selectedRow = rows.find(row => row.classList.contains("selected")) || rows[0];
    if (selectedRow) selectedRow.classList.add("active");
    requestAnimationFrame(() => {
      if (!menu.isConnected) return;
      positionChoiceMenu({ menu, button: anchor });
      (selectedRow || menu).focus({ preventScroll: true });
    });
    if (!rows.length) {
      menu.tabIndex = -1;
      menu.appendChild(el("span", "choice-empty", "No choices available"));
    }
    return menu;
  }

  showModelMenu(anchor) {
    if (!this.session) return;
    const spec = this.composerChoiceSpec("model");
    this.optionMenu(anchor, spec.options, spec.selected, (value) => this.applyModelChoice(value));
  }

  async applyModelChoice(value) {
    if (value === "__current_custom__") return;
    if (value !== "__custom__") { await this.patchSession({ model: value }); return; }
    const current = (this.session && this.session.model) || "";
    const val = await modalPrompt("Model override",
      "Model for this session (empty = engine default).", current);
    if (val !== null) await this.patchSession({ model: val.trim() });
  }

  showEffortMenu(anchor) {
    if (!this.session) return;
    const spec = this.composerChoiceSpec("effort");
    if (!spec.options.length) { toast("engine has no effort levels", "info"); return; }
    this.optionMenu(anchor, spec.options, spec.selected,
      (value) => this.patchSession({ effort: value }));
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
    const ok = await modalConfirm("Delete session?", sessionDeleteMessage(this.session));
    if (!ok) return;
    try {
      await api(this.tab.bid, `sessions/${this.tab.sid}`, { method: "DELETE" });
      closeTab(this.tab.id);
      if (this.tab.bid) refreshGroup(this.tab.bid);
      toast("session deleted");
    } catch (e) { toast(e.message, "error"); }
  }

  async resetWorkspace() {
    if (!isScratchWorkspace(this.session)) return;
    const wasMissing = !!this.session.workspace_missing;
    const ok = await modalConfirm(
      wasMissing ? "Recreate scratch workspace?" : "Reset scratch workspace?",
      wasMissing ? "Puppy will create a new empty workspace. The transcript is kept." :
        "All files in this scratch workspace are removed permanently. The transcript is kept.");
    if (!ok) return;
    try {
      const r = await api(this.tab.bid, `sessions/${this.tab.sid}/workspace/reset`,
        { method: "POST" });
      this.session = r.session;
      this.updateHead();
      if (this.tab.bid) refreshGroup(this.tab.bid);
      toast(wasMissing ? "scratch workspace recreated" : "scratch workspace reset");
    } catch (e) { toast(e.message, "error"); }
  }
}

/* ================= TermView ================= */
class TermView {
  constructor(tab) {
    this.tab = tab;
    this.closed = false;
    this.connectionSequence = 0;
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
    const sequence = ++this.connectionSequence;
    if (this.dataSub) { this.dataSub.dispose(); this.dataSub = null; }
    const params = new URLSearchParams({ cols: this.term.cols, rows: this.term.rows });
    if (this.tab.cmd) params.set("cmd", this.tab.cmd);
    const ws = new WebSocket(wsUrl(this.tab.bid, "ws/term?" + params.toString()));
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    const enc = new TextEncoder();
    ws.onopen = () => {
      if (this.closed || sequence !== this.connectionSequence || this.ws !== ws) {
        try { ws.close(); } catch (error) {}
        return;
      }
      noteRemoteSocketReachable(this.tab.bid);
      this.term.focus();
      this.dataSub = this.term.onData(d => { if (ws.readyState === 1) ws.send(enc.encode(d)); });
    };
    ws.onmessage = (ev) => {
      if (sequence !== this.connectionSequence || this.ws !== ws) return;
      if (typeof ev.data === "string") return;
      noteRemoteSocketReachable(this.tab.bid);
      this.term.write(new Uint8Array(ev.data));
    };
    ws.onclose = () => {
      if (sequence !== this.connectionSequence || this.ws !== ws) return;
      this.ws = null;
      if (this.dataSub) { this.dataSub.dispose(); this.dataSub = null; }
      if (!this.closed) this.showDead();
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }
  sendResize() {
    if (this.ws && this.ws.readyState === 1)
      this.ws.send(JSON.stringify({ type: "resize", cols: this.term.cols, rows: this.term.rows }));
  }
  showDead() {
    if (this.root.querySelector(".term-dead")) { this.syncRemoteState(); return; }
    const d = el("div", "term-dead");
    d.appendChild(el("div", "term-dead-message", "terminal ended"));
    const b = el("button", "btn btn-pri", "New shell");
    b.onclick = () => {
      d.remove();
      this.term.reset();
      this.connect();
    };
    d.appendChild(b);
    this.root.querySelector(".term-wrap").style.position = "relative";
    this.root.appendChild(d);
    this.syncRemoteState();
  }
  syncRemoteState() {
    const dead = this.root.querySelector(".term-dead");
    if (!dead) return;
    const message = dead.querySelector(".term-dead-message");
    const button = dead.querySelector("button");
    const unavailable = !!this.tab.bid && state.remoteOk[this.tab.bid] === false;
    message.textContent = unavailable ? "backend unavailable" : "terminal ended";
    button.textContent = unavailable ? "Waiting for backend…" : "New shell";
    button.disabled = unavailable;
  }
  destroy() {
    this.closed = true;
    this.connectionSequence++;
    if (this.resizeObs) this.resizeObs.disconnect();
    if (this.ws) try { this.ws.close(); } catch (e) {}
    if (this.dataSub) { this.dataSub.dispose(); this.dataSub = null; }
    if (this.term) this.term.dispose();
    this.root.remove();
  }
}

/* ================= SettingsView ================= */
class SettingsView {
  constructor(tab) {
    this.tab = tab;
    this.renderGeneration = 0;
    this.remoteEngineGroups = new Map();
    this.remoteBackendDots = new Map();
    this.remoteBackendMeta = new Map();
    this.usageRows = new Map();
    this.upgradeButtons = new Map();
    this.backendAutoToggles = new Map();
    this.upgradeReadiness = new Map();
    this.upgradesInProgress = new Set();
    this.upgradePollTimer = null;
    this.upgradePollGeneration = 0;
    this.localEngineGroup = null;
    this.root = el("div", "view settings");
    this.root.innerHTML = `<div class="settings-scroll"><div class="settings-inner"></div></div>`;
    $("views").appendChild(this.root);
    this.inner = this.root.querySelector(".settings-inner");
  }
  destroy() {
    this.renderGeneration++;
    this.stopUpgradeReadinessPolling();
    this.remoteEngineGroups.clear();
    this.remoteBackendDots.clear();
    this.remoteBackendMeta.clear();
    this.usageRows.clear();
    this.upgradeButtons.clear();
    this.backendAutoToggles.clear();
    this.upgradeReadiness.clear();
    this.upgradesInProgress.clear();
    this.localEngineGroup = null;
    this.root.remove();
  }
  onShow() { this.render(); }

  stopUpgradeReadinessPolling() {
    this.upgradePollGeneration++;
    if (this.upgradePollTimer !== null) clearTimeout(this.upgradePollTimer);
    this.upgradePollTimer = null;
  }

  normalizeReportedReadiness(value) {
    if (!value || typeof value !== "object" || typeof value.ready !== "boolean" ||
        typeof value.state !== "string" || typeof value.reason !== "string") return null;
    return {
      ready: value.ready,
      state: value.state,
      reason: value.reason,
      sessions: Array.isArray(value.sessions) ? value.sessions : [],
      active_terminals: Math.max(0, Number(value.active_terminals) || 0),
    };
  }

  readinessFromDescriptor(bid, descriptor) {
    const reported = this.normalizeReportedReadiness(descriptor && descriptor.readiness);
    if (reported) return reported;
    if (descriptor && Object.prototype.hasOwnProperty.call(descriptor, "readiness")) {
      return { ready: false, state: "checking", reason: "backend returned invalid readiness" };
    }
    if (!descriptor || descriptor.supported !== true) {
      return {
        ready: false, state: "unsupported",
        reason: (descriptor && descriptor.reason) || "backend does not support remote upgrades",
      };
    }
    /* Compatibility bridge for the first upgrade of older upgrade-capable
       nodes. They verify terminals and races authoritatively on POST, but only
       session activity can be inferred before they gain readiness reporting. */
    if (!Object.prototype.hasOwnProperty.call(state.remoteSessions, bid)) {
      return { ready: false, state: "checking", reason: "waiting for backend session status" };
    }
    const busy = sessionsFor(bid).some(session => session.status === "running");
    return {
      ready: !busy,
      state: busy ? "busy" : "ready",
      reason: busy ? "backend has an active session" :
        "readiness inferred from sessions until this backend is upgraded",
      legacy: true,
    };
  }

  isUpgradeCandidate(record) {
    const backend = state.backends.find(item => item.id === record.backend.id) || record.backend;
    return backend.role === "backend" && backendHasCapability(backend, "remote-upgrade") &&
      cmpVersion(backend.remote_version, record.controllerVersion) === -1;
  }

  syncBackendAutoToggles() {
    for (const [bid, record] of this.backendAutoToggles) {
      const backend = state.backends.find(item => item.id === bid);
      if (!backend || !record.input.isConnected) continue;
      const enabled = !!backend.auto_upgrade;
      const supported = backendSupportsAutoUpgrade(backend);
      if (!record.saving) record.input.checked = enabled;
      const upgrading = this.upgradesInProgress.has(bid) || !!backend.upgrade_in_progress;
      record.input.disabled = record.saving || upgrading ||
        (!supported && !enabled);
      record.root.classList.toggle("disabled", record.input.disabled);
      record.root.title = upgrading ?
        "This backend upgrade is already in progress" : supported ?
        "Upgrade this backend automatically when it is outdated and idle" :
        "Automatic upgrades require an upgrade-capable headless backend";
    }
  }

  syncUpgradeButtons() {
    for (const [bid, record] of this.upgradeButtons) {
      const button = record.button;
      if (!button.isConnected) continue;
      const backend = state.backends.find(item => item.id === bid) || record.backend;
      const versionOrder = cmpVersion(backend.remote_version, record.controllerVersion);
      const capable = backend.role === "backend" &&
        backendHasCapability(backend, "remote-upgrade");
      const set = (text, disabled, title) => {
        button.textContent = text;
        button.disabled = disabled;
        button.title = title || "";
      };
      if (this.upgradesInProgress.has(bid) || backend.upgrade_in_progress) {
        set("Upgrading…", true, "Upgrade is being staged and health-checked");
      } else if (!capable) {
        set("Upgrade", true,
          "This backend needs one manual upgrade before WebUI upgrades are available");
      } else if (versionOrder === 0) {
        set("Current", true, `Already at v${record.controllerVersion}`);
      } else if (versionOrder === 1) {
        set("Newer", true,
          `Backend v${backend.remote_version} is newer than this controller`);
      } else if (versionOrder === null) {
        set("Upgrade", true, "Backend version cannot be compared safely");
      } else if (state.remoteOk[bid] === false) {
        set("Unavailable", true, state.remoteErrors[bid] || "Backend unavailable");
      } else if (sessionsFor(bid).some(session => session.status === "running")) {
        const known = this.upgradeReadiness.get(bid);
        set("Busy", true, known && known.state === "busy" ?
          known.reason : "Backend has an active session");
      } else {
        const readiness = this.upgradeReadiness.get(bid);
        if (!readiness) {
          set("Checking…", true, "Checking whether the backend can accept an upgrade");
        } else if (readiness.ready) {
          set("Upgrade", false, readiness.reason || "Backend is ready to upgrade");
        } else if (readiness.state === "busy") {
          set("Busy", true, readiness.reason);
        } else if (readiness.state === "upgrading") {
          set("Upgrading…", true, readiness.reason);
        } else if (readiness.state === "blocked" || readiness.state === "unsupported") {
          set("Blocked", true, readiness.reason);
        } else {
          set("Checking…", true, readiness.reason || "Upgrade readiness is unavailable");
        }
      }
    }
    this.syncBackendAutoToggles();
  }

  async refreshUpgradeReadiness(generation) {
    const candidates = [...this.upgradeButtons.entries()]
      .filter(([, record]) => this.isUpgradeCandidate(record) &&
        !this.upgradesInProgress.has(record.backend.id));
    const results = await Promise.all(candidates.map(async ([bid]) => {
      try {
        const descriptor = await api(bid, "node/upgrade", {
          timeoutMs: UPGRADE_READINESS_TIMEOUT,
        });
        return { bid, readiness: this.readinessFromDescriptor(bid, descriptor), reachable: true };
      } catch (error) {
        const rejected = this.normalizeReportedReadiness(
          error.data && error.data.readiness);
        return {
          bid,
          readiness: rejected || {
            ready: false, state: "checking", reason: error.message || "readiness check failed",
          },
          reachable: !!rejected,
        };
      }
    }));
    if (generation !== this.upgradePollGeneration || state.active !== this.tab.id) return;
    for (const result of results) {
      this.upgradeReadiness.set(result.bid, result.readiness);
      if (result.reachable) {
        state.remoteOk[result.bid] = true;
        delete state.remoteErrors[result.bid];
      }
    }
    this.syncRemoteState();
  }

  startUpgradeReadinessPolling() {
    this.stopUpgradeReadinessPolling();
    const generation = this.upgradePollGeneration;
    if (![...this.upgradeButtons.values()].some(record => this.isUpgradeCandidate(record)))
      return;
    const tick = async () => {
      this.upgradePollTimer = null;
      if (generation !== this.upgradePollGeneration || state.active !== this.tab.id ||
          !this.root.isConnected) return;
      try { await this.refreshUpgradeReadiness(generation); }
      catch (error) { console.warn("upgrade readiness poll failed", error); }
      if (generation === this.upgradePollGeneration && state.active === this.tab.id &&
          this.root.isConnected)
        this.upgradePollTimer = setTimeout(tick, UPGRADE_READINESS_INTERVAL);
    };
    tick();
  }

  syncRemoteState() {
    for (const [bid, group] of this.remoteEngineGroups) {
      const backend = state.backends.find(item => item.id === bid);
      if (!backend) continue;
      group.setMeta(backendLocationVersion(backend));
      const reachable = state.remoteOk[bid];
      const cached = Object.prototype.hasOwnProperty.call(state.engCache, bid) ?
        state.engCache[bid] : null;
      if (reachable === false) {
        group.update({
          status: "bad", engines: [], message: "Backend unavailable",
          detail: state.remoteErrors[bid] || "The controller cannot reach this backend",
        });
      } else if (reachable === true && cached === null) {
        group.update({
          status: "ok", engines: null,
          message: state.remoteEngineErrors[bid] ?
            "Backend available · retrying engine status…" : "Loading engines…",
          detail: state.remoteEngineErrors[bid] || "",
        });
      } else {
        group.update({
          status: reachable === true ? "ok" : "pending", engines: cached,
          detail: state.remoteEngineErrors[bid] || "",
        });
      }
    }
    for (const [bid, dot] of this.remoteBackendDots) {
      const status = remoteAvailability(bid);
      dot.className = "gdot " + status;
      dot.title = remoteAvailabilityTitle(bid);
    }
    for (const [bid, meta] of this.remoteBackendMeta) {
      const backend = state.backends.find(item => item.id === bid);
      if (!backend || !meta.isConnected) continue;
      const value = backendLocationVersion(backend);
      meta.textContent = value;
      meta.title = value;
    }
    for (const [bid, row] of this.usageRows) {
      if (!bid) {
        row.update(state.usageRefresh, "ok", true);
        continue;
      }
      if (!state.backends.some(backend => backend.id === bid)) continue;
      row.update(state.remoteUsageRefresh[bid] || null,
        remoteAvailability(bid), backendSupportsUsageRefresh(bid));
    }
    this.syncUpgradeButtons();
  }

  engineRow(e2) {
    const row = el("div", "engine-row");
    const identity = el("div", "engine-row-identity");
    identity.appendChild(el("span", "engine-dot " + e2.key));
    identity.appendChild(el("span", "engine-row-name", e2.label));
    const statuses = el("div", "engine-row-statuses");
    const version = el("span", "pill engine-version " + (e2.installed ? "ok" : "bad"),
      e2.installed ? (e2.version || "installed") : "not installed");
    version.title = version.textContent;
    statuses.appendChild(version);
    statuses.appendChild(el("span", "pill " + (e2.auth === "ok" ? "ok" : "bad"),
      "auth: " + e2.auth));
    if (e2.rate_limit && e2.rate_limit.resetsAt) {
      const reset = new Date(e2.rate_limit.resetsAt * 1000);
      const rate = el("span", "pill " +
        (e2.rate_limit.status === "allowed" ? "" : "warn"),
        `${e2.rate_limit.rateLimitType || "window"} resets ${reset.toLocaleTimeString([], {
          hour: "2-digit", minute: "2-digit",
        })}`);
      rate.title = rate.textContent;
      statuses.appendChild(rate);
    }
    row.appendChild(identity);
    row.appendChild(statuses);
    return row;
  }

  engineGroup(name, meta) {
    const root = el("section", "engine-node");
    const head = el("div", "engine-node-head");
    const dot = el("span", "gdot pending");
    head.appendChild(dot);
    const nameEl = el("span", "engine-node-name", name);
    nameEl.title = name;
    const metaEl = el("span", "engine-node-meta", meta);
    metaEl.title = meta;
    head.appendChild(nameEl);
    head.appendChild(metaEl);
    const body = el("div", "engine-node-body");
    root.appendChild(head); root.appendChild(body);

    const update = ({ status = "pending", engines = null, message = "", detail = "" }) => {
      dot.className = "gdot " + status;
      const statusLabel = status === "ok" ? "available" : status === "bad" ? "unavailable" : "checking";
      dot.title = detail ? `${statusLabel}: ${detail}` : statusLabel;
      body.innerHTML = "";
      if (message || engines === null) {
        const note = el("div", "engine-node-message", message || "Checking engines…");
        if (detail) note.title = detail;
        body.appendChild(note);
      } else if (!engines.length) {
        body.appendChild(el("div", "engine-node-message", "No engines reported"));
      } else {
        engines.forEach(e2 => body.appendChild(this.engineRow(e2)));
      }
    };
    const setMeta = value => {
      metaEl.textContent = value || "";
      metaEl.title = value || "";
    };
    return { root, update, setMeta };
  }

  usageRefreshRow(name, bid) {
    const root = el("div", "usage-refresh-row");
    const identity = el("div", "usage-refresh-identity");
    const nameEl = el("div", "usage-refresh-name", name);
    nameEl.title = name;
    const note = el("div", "usage-refresh-note", "Checking setting…");
    identity.appendChild(nameEl);
    identity.appendChild(note);

    const controls = el("div", "usage-refresh-controls");
    const interval = document.createElement("input");
    interval.type = "number";
    interval.min = "0";
    interval.max = "1440";
    interval.step = "1";
    interval.inputMode = "numeric";
    interval.setAttribute("aria-label", `${name} usage refresh interval in minutes`);
    interval.title = "0 disables automatic refresh; maximum 1440 minutes";
    const unit = el("span", "usage-refresh-unit", "min");
    const save = el("button", "btn btn-sm", "Apply");
    controls.appendChild(interval);
    controls.appendChild(unit);
    controls.appendChild(save);
    root.appendChild(identity);
    root.appendChild(controls);

    let current = null;
    let availability = bid ? "pending" : "ok";
    let supported = !bid;
    let saving = false;
    const describe = (metadata, reachable, canConfigure) => {
      if (!canConfigure) return "Backend upgrade required";
      if (reachable === "bad") return "Backend unavailable";
      if (!metadata) return "Checking backend setting…";
      if (!metadata.enabled) return "Automatic refresh is off";
      if (metadata.last_error) return `Last refresh failed · ${metadata.last_error}`;
      if (metadata.last_success_at) {
        const stamp = new Date(Number(metadata.last_success_at) * 1000);
        if (!Number.isNaN(stamp.getTime())) return `Last refreshed ${stamp.toLocaleString()}`;
      }
      return "Refreshes on the next engine-status check";
    };
    const update = (metadata, reachable = "ok", canConfigure = true) => {
      current = metadata;
      availability = reachable;
      supported = canConfigure;
      if (metadata && document.activeElement !== interval)
        interval.value = String(metadata.minutes);
      const disabled = saving || !canConfigure || reachable === "bad" || !metadata;
      interval.disabled = disabled;
      save.disabled = disabled;
      const description = describe(metadata, reachable, canConfigure);
      note.textContent = description;
      note.title = description;
    };
    save.onclick = async () => {
      if (!interval.value.trim()) {
        toast("enter a refresh interval in minutes", "error");
        interval.focus();
        return;
      }
      const minutes = Number(interval.value);
      if (!Number.isInteger(minutes) || minutes < 0 || minutes > 1440) {
        toast("refresh interval must be 0 or a whole number from 1 to 1440", "error");
        interval.focus();
        return;
      }
      saving = true;
      save.textContent = minutes ? "Refreshing…" : "Saving…";
      update(current, availability, supported);
      try {
        const result = await api(bid, "engines/usage-refresh", {
          method: "PATCH", body: { minutes }, timeoutMs: 20000,
        });
        if (!result || !Array.isArray(result.engines) || !result.usage_refresh)
          throw new Error("node returned an invalid usage refresh response");
        if (bid) {
          state.engCache[bid] = result.engines;
          state.remoteUsageRefresh[bid] = result.usage_refresh;
          state.remoteOk[bid] = true;
          delete state.remoteErrors[bid];
          state.remoteEngineCheckedAt[bid] = Date.now();
          delete state.remoteEngineErrors[bid];
          const group = this.remoteEngineGroups.get(bid);
          if (group) group.update({ status: "ok", engines: result.engines });
        } else {
          state.engines = result.engines;
          state.engMap = {};
          state.engines.forEach(engine => state.engMap[engine.key] = engine);
          state.usageRefresh = result.usage_refresh;
          state.localEngineCheckedAt = Date.now();
          if (this.localEngineGroup)
            this.localEngineGroup.update({ status: "ok", engines: result.engines });
        }
        renderFootEngines();
        current = result.usage_refresh;
        const suffix = result.usage_refresh.last_error ? " · refresh failed" : "";
        toast(`${name}: usage refresh saved${suffix}`,
          result.usage_refresh.last_error ? "error" : "ok", 6000);
      } catch (error) {
        toast(`${name}: ${error.message}`, "error", 7000);
      } finally {
        saving = false;
        save.textContent = "Apply";
        this.syncRemoteState();
      }
    };
    return { root, update };
  }

  async render() {
    this.stopUpgradeReadinessPolling();
    const generation = ++this.renderGeneration;
    let settings, engines;
    try {
      [settings, engines] = await Promise.all([api(0, "settings"), api(0, "engines")]);
    } catch (e) {
      if (generation === this.renderGeneration)
        this.inner.innerHTML = `<div class="err-card">${esc(e.message)}</div>`;
      return;
    }
    if (generation !== this.renderGeneration) return;
    state.engines = engines.engines;
    state.usageRefresh = engines.usage_refresh || settings.usage_refresh || state.usageRefresh;
    state.localEngineCheckedAt = Date.now();
    state.engMap = {};
    state.engines.forEach(e2 => state.engMap[e2.key] = e2);
    renderFootEngines();
    this.inner.innerHTML = "";
    this.remoteEngineGroups.clear();
    this.remoteBackendDots.clear();
    this.remoteBackendMeta.clear();
    this.usageRows.clear();
    this.upgradeButtons.clear();
    this.backendAutoToggles.clear();
    /* A hidden Settings tab is not polled. Re-enter through Checking rather
       than briefly enabling a button from an arbitrarily old ready result. */
    this.upgradeReadiness.clear();
    this.localEngineGroup = null;

    /* instance */
    const c1 = el("div", "card");
    const activeWeb = settings.active_web || settings.web;
    c1.innerHTML = `<h2>Instance</h2>
      <label>Instance name<input type="text" id="set-name" value="${esc(settings.instance_name)}"></label>
      <label>Default working directory<input type="text" id="set-cwd" value="${esc(settings.default_cwd || "")}"></label>
      <label>Terminal command<input type="text" id="set-term" value="${esc(settings.terminal_command)}"></label>
      <div class="bind-fields">
        <label>Bind IP<input type="text" id="set-bind" value="${esc(settings.web.host)}"
          inputmode="url" autocomplete="off" autocapitalize="off" spellcheck="false"></label>
        <label>Bind port<input type="number" id="set-port" value="${esc(settings.web.port)}"
          min="1" max="65535" step="1" inputmode="numeric" autocomplete="off"></label>
      </div>
      <p class="bind-help">Literal IPv4 or IPv6 address and TCP port for this WebUI instance.
        A changed endpoint is tested directly from this browser before it can be saved.</p>
      <div class="kv"><span class="k">Active listener</span><span class="v">${esc(fmtEndpoint(activeWeb.host, activeWeb.port))}</span></div>
      ${settings.web_restart_required ? `<div class="bind-pending">
        <span>Restart required to activate ${esc(fmtEndpoint(settings.web.host, settings.web.port))}.</span>
        <button class="btn btn-sm" id="set-activate" type="button">Verify &amp; restart</button>
      </div>` : ""}
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
      else copyWithToast(settings.api_token, "token copied");
    };
    const saveSettings = async (forceActivation = false) => {
      const saveButton = c1.querySelector("#set-save");
      const activateButton = c1.querySelector("#set-activate");
      const bindInput = c1.querySelector("#set-bind");
      const portInput = c1.querySelector("#set-port");
      const proposedBind = bindInput.value.trim();
      const proposedPortText = portInput.value.trim();
      const proposedPort = Number(proposedPortText);
      if (!proposedPortText || !Number.isInteger(proposedPort) ||
          proposedPort < 1 || proposedPort > 65535) {
        toast("bind port must be a whole number between 1 and 65535", "error");
        portInput.focus();
        return;
      }
      const bindChanged = proposedBind !== String(settings.web.host || "") ||
        proposedPort !== Number(settings.web.port);
      const configuredPending = !!settings.web_restart_required &&
        proposedBind === String(settings.web.host || "") &&
        proposedPort === Number(settings.web.port);
      const endpointProofNeeded = bindChanged || configuredPending || forceActivation;
      if (endpointProofNeeded && !(await modalConfirm(
        bindChanged ? "Change and restart Puppy listener?" : "Restart Puppy listener?",
        `Puppy will first ask this browser to reach ${proposedBind
          ? fmtEndpoint(proposedBind, proposedPort) : "the proposed endpoint"} directly. ` +
        `Only after that succeeds will it save the listener and queue a graceful restart. ` +
        "Active turns are allowed to finish, then this page reconnects automatically."))) return;
      saveButton.disabled = true;
      if (activateButton) activateButton.disabled = true;
      saveButton.textContent = endpointProofNeeded ? "Verifying…" : "Saving…";
      let bindCommit = null;
      let verified = null;
      let commitAttempted = false;
      try {
        if (endpointProofNeeded) {
          const prepared = await api(0, "settings/bind/prepare", {
            method: "POST", body: {
              host: proposedBind, port: proposedPort, origin: location.origin,
            },
          });
          const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
          const timer = controller ? setTimeout(() => controller.abort(), 8000) : null;
          let proofResponse;
          try {
            proofResponse = await fetch(prepared.verify_url, {
              method: "GET", mode: "cors", credentials: "omit", cache: "no-store",
              redirect: "error", referrerPolicy: "no-referrer",
              ...(controller ? { signal: controller.signal } : {}),
            });
          } catch (error) {
            throw new Error(controller && controller.signal.aborted
              ? "the direct browser check timed out"
              : "this browser could not reach the proposed IP and port");
          } finally {
            if (timer !== null) clearTimeout(timer);
          }
          let proof = null;
          try { proof = await proofResponse.json(); } catch (_) { /* checked below */ }
          if (!proofResponse.ok || !proof || proof.ok !== true || proof.token !== prepared.token)
            throw new Error("the proposed address did not return Puppy's verification proof");
          verified = prepared;
        }
        await api(0, "settings", { method: "PATCH", body: {
          instance_name: c1.querySelector("#set-name").value,
          default_cwd: c1.querySelector("#set-cwd").value,
          terminal_command: c1.querySelector("#set-term").value,
        }});
        if (verified) {
          commitAttempted = true;
          bindCommit = await api(0, "settings/bind/commit", {
            method: "POST", body: { token: verified.token },
          });
        }
        if (bindCommit && bindCommit.restart_required) {
          if (!bindCommit.handoff || !bindCommit.handoff.token)
            throw new Error(bindCommit.handoff_error ||
              "automatic activation requires a signed-in browser session");
          const activated = await api(0, "settings/bind/activate", {
            method: "POST", body: {
              token: bindCommit.handoff.token,
              browser_state: listenerHandoffBrowserState(),
            },
          });
          followListenerHandoff(activated);
          return;
        }
        await refreshState();
        await this.render();
        toast("saved", "ok");
      } catch (e) {
        if (bindCommit) {
          const activation = bindCommit.restart_required
            ? `Automatic restart was not queued. Return to Settings and use Verify & restart ` +
              `to activate ${fmtEndpoint(bindCommit.host, bindCommit.port)}.`
            : "The active listener already matches this setting; no restart is required.";
          modalNotice("Listener was saved",
            `The browser check and save succeeded, but activation did not complete: ` +
            `${e.message}. ${activation}`);
        } else if (commitAttempted && verified) {
          let current = null;
          try { current = await api(0, "settings", { timeoutMs: 3000 }); } catch (_) { /* uncertain */ }
          if (current && String(current.web.host) === String(verified.host) &&
              Number(current.web.port) === Number(verified.port)) {
            const activation = current.web_restart_required
              ? `Restart Puppy to activate ${fmtEndpoint(current.web.host, current.web.port)}.`
              : "The active listener already matches this setting; no restart is required.";
            modalNotice("Listener was saved",
              `The save completed even though its response was interrupted. ${activation}`);
          } else if (current && String(current.web.host) === String(settings.web.host) &&
                     Number(current.web.port) === Number(settings.web.port)) {
            modalNotice("Listener was not changed",
              `${e.message}. Puppy remains configured on ${fmtEndpoint(settings.web.host, settings.web.port)}.`);
          } else {
            modalNotice("Listener save could not be confirmed",
              `${e.message}. The current listener remains active. Reload Settings and confirm the ` +
              "configured listener before restarting Puppy.");
          }
        } else if (endpointProofNeeded) modalNotice("Listener was not changed",
          `${e.message}. Puppy remains configured on ${fmtEndpoint(settings.web.host, settings.web.port)}.`);
        else toast(e.message, "error");
      } finally {
        if (saveButton.isConnected) {
          saveButton.disabled = false;
          saveButton.textContent = "Save";
        }
        if (activateButton && activateButton.isConnected) activateButton.disabled = false;
      }
    };
    c1.querySelector("#set-save").onclick = () => saveSettings(false);
    const activateButton = c1.querySelector("#set-activate");
    if (activateButton) activateButton.onclick = () => saveSettings(true);
    c1.querySelector("#set-logout").onclick = async () => {
      await api(0, "auth/logout", { method: "POST" });
      location.reload();
    };

    /* engines */
    const c2 = el("div", "card");
    c2.innerHTML = `<h2>Engines</h2>`;
    const localGroup = this.engineGroup(settings.instance_name, `this instance · v${settings.version}`);
    localGroup.update({ status: "ok", engines: state.engines });
    this.localEngineGroup = localGroup;
    c2.appendChild(localGroup.root);
    for (const b of state.backends) {
      const meta = backendLocationVersion(b);
      const group = this.engineGroup(b.name, meta);
      const cached = state.engCache[b.id];
      group.update({
        status: state.remoteOk[b.id] === false ? "bad" : state.remoteOk[b.id] === true ? "ok" : "pending",
        engines: cached || null,
      });
      this.remoteEngineGroups.set(b.id, group);
      c2.appendChild(group.root);
    }
    this.inner.appendChild(c2);

    /* account usage refresh */
    const usageCard = el("div", "card usage-refresh-card");
    usageCard.innerHTML = `<h2>Usage refresh</h2>
      <p class="usage-refresh-copy">Choose how often each node asks its installed engines
        for current account-limit data. This read-only check does not start a turn or consume
        model tokens. Use 0 to disable it.</p>`;
    const usageList = el("div", "usage-refresh-list");
    const localUsage = this.usageRefreshRow(settings.instance_name, 0);
    localUsage.update(state.usageRefresh, "ok", true);
    this.usageRows.set(0, localUsage);
    usageList.appendChild(localUsage.root);
    for (const b of state.backends) {
      const usage = this.usageRefreshRow(b.name, b.id);
      usage.update(state.remoteUsageRefresh[b.id] || null,
        remoteAvailability(b.id), backendSupportsUsageRefresh(b.id));
      this.usageRows.set(b.id, usage);
      usageList.appendChild(usage.root);
    }
    usageCard.appendChild(usageList);
    this.inner.appendChild(usageCard);
    this.syncRemoteState();
    pollRemotes({ forceEngines: true })
      .catch(error => console.warn("settings remote poll failed", error));

    /* backends */
    const c3 = el("div", "card");
    c3.innerHTML = `<h2>Backends</h2><div id="be-list"></div>
      <div class="settings-form" style="margin-top:12px">
        <label>Name <span style="text-transform:none">(optional)</span><input type="text" id="be-name"></label>
        <label>URL<input type="text" id="be-url"></label>
        <label class="full">API token<input type="password" id="be-token" autocomplete="off"></label>
        <label class="full">TLS certificate SHA-256 <span style="text-transform:none">(optional)</span>
          <input type="text" id="be-tls" autocomplete="off" spellcheck="false"
            placeholder="Supplied automatically by pairing JSON"></label>
        <label class="full">Pairing JSON <span style="text-transform:none">(optional)</span>
          <textarea id="be-pairing" rows="3" placeholder="Paste puppy-backend pairing output"></textarea></label>
        <label class="be-auto be-auto-add full" title="Upgrade this headless backend automatically when it is outdated and idle">
          <input type="checkbox" id="be-auto">
          <span class="be-auto-track" aria-hidden="true"><span></span></span>
          <span class="be-auto-copy"><span>Auto-upgrade when idle</span>
            <small>Signed release · readiness checked · rollback protected</small></span>
        </label>
        <div class="full"><button class="btn btn-pri btn-sm" id="be-add">Add backend</button></div>
      </div>`;
    const beList = c3.querySelector("#be-list");
    const renderBes = () => {
      beList.innerHTML = "";
      this.remoteBackendDots.clear();
      this.remoteBackendMeta.clear();
      if (!state.backends.length) beList.innerHTML = `<p class="hint">No remote backends. This instance ("${esc(backendName(0))}") is always available as local.</p>`;
      for (const b of state.backends) {
        const row = el("div", "be-row");
        const details = el("div", "be-details");
        const identity = el("div", "be-identity");
        const nameRow = el("div", "be-name-row");
        const availability = el("span", "gdot pending");
        const name = el("span", "be-name", b.name);
        name.title = [b.role, b.remote_version && `v${b.remote_version}`,
          b.protocol != null && `protocol ${b.protocol}`].filter(Boolean).join(" · ");
        const backendMeta = backendLocationVersion(b);
        const url = el("span", "be-url", backendMeta);
        url.title = backendMeta;
        const metaRow = el("div", "be-meta-row");
        const autoRoot = el("label", "be-auto be-auto-existing");
        const autoInput = document.createElement("input");
        autoInput.type = "checkbox";
        autoInput.checked = !!b.auto_upgrade;
        autoInput.setAttribute("aria-label", `Automatically upgrade ${b.name} when idle`);
        const autoTrack = el("span", "be-auto-track");
        autoTrack.setAttribute("aria-hidden", "true");
        autoTrack.appendChild(el("span"));
        autoRoot.appendChild(autoInput);
        autoRoot.appendChild(autoTrack);
        autoRoot.appendChild(el("span", "be-auto-label", "Auto-upgrade"));
        const autoRecord = { root: autoRoot, input: autoInput, saving: false };
        this.backendAutoToggles.set(b.id, autoRecord);
        autoInput.onchange = async () => {
          const desired = autoInput.checked;
          const previous = !!b.auto_upgrade;
          autoRecord.saving = true;
          this.syncBackendAutoToggles();
          try {
            const result = await api(0, `backends/${b.id}`, {
              method: "PATCH", body: { auto_upgrade: desired },
            });
            const current = state.backends.find(item => item.id === b.id);
            if (current && result.backend) Object.assign(current, result.backend);
            Object.assign(b, result.backend || { auto_upgrade: desired });
          } catch (error) {
            const current = state.backends.find(item => item.id === b.id);
            if (current) current.auto_upgrade = previous;
            b.auto_upgrade = previous;
            toast(`${b.name}: ${error.message}`, "error", 6500);
          } finally {
            autoRecord.saving = false;
            this.syncBackendAutoToggles();
          }
        };
        const isTls = /^https:\/\//i.test(b.url || "");
        const isPinned = isTls && !!b.tls_fingerprint;
        const security = el("span", `be-security ${isTls ? "secure" : "clear"}`);
        const securityLabel = isPinned ? "Encrypted: TLS certificate pinned"
          : isTls ? "Encrypted: TLS certificate verified"
            : "Cleartext: traffic is not encrypted";
        security.setAttribute("role", "img");
        security.setAttribute("aria-label", securityLabel);
        security.title = isPinned
          ? `Encrypted · pinned certificate SHA-256: ${b.tls_fingerprint}`
          : isTls ? "Encrypted · certificate verified by the controller system trust store"
            : "Cleartext · traffic to this backend is not encrypted";
        security.appendChild(transportShieldIcon(isTls));
        nameRow.appendChild(availability);
        nameRow.appendChild(name);
        identity.appendChild(nameRow);
        metaRow.appendChild(url);
        identity.appendChild(metaRow);
        details.appendChild(security);
        details.appendChild(identity);
        this.remoteBackendDots.set(b.id, availability);
        this.remoteBackendMeta.set(b.id, url);
        row.appendChild(details);
        row.appendChild(autoRoot);
        const actions = el("div", "be-actions");
        const test = el("button", "btn btn-sm", "Test");
        test.onclick = async () => {
          test.textContent = "…";
          try {
            const r = await api(0, `backends/${b.id}/test`, { method: "POST" });
            if (r.ok) {
              state.remoteOk[b.id] = true;
              delete state.remoteErrors[b.id];
              toast(`${b.name}: ok (${r.remote && r.remote.version})`, "ok");
              await refreshState(); await this.render();
            } else {
              const message = r.error || "HTTP " + r.status;
              state.remoteOk[b.id] = false;
              state.remoteErrors[b.id] = message;
              syncRemoteStateViews();
              toast(`${b.name}: ${message}`, "error");
            }
          } catch (e) {
            toast(e.message, "error");
          } finally {
            if (test.isConnected) test.textContent = "Test";
            pollRemotes({ forceEngines: true })
              .catch(error => console.warn("backend test follow-up poll failed", error));
          }
        };
        const upgrade = el("button", "btn btn-sm", "Upgrade");
        this.upgradeButtons.set(b.id, {
          button: upgrade, backend: b, controllerVersion: settings.version,
        });
        upgrade.onclick = async () => {
          if (upgrade.disabled || this.upgradesInProgress.has(b.id)) return;
          this.upgradesInProgress.add(b.id);
          this.syncUpgradeButtons();
          let result;
          try {
            result = await api(0, `backends/${b.id}/upgrade`, { method: "POST" });
          } catch (e) {
            this.upgradesInProgress.delete(b.id);
            const rejected = this.normalizeReportedReadiness(
              e.data && e.data.readiness);
            if (rejected) {
              this.upgradeReadiness.set(b.id, rejected);
              state.remoteOk[b.id] = true;
              delete state.remoteErrors[b.id];
            }
            this.syncUpgradeButtons();
            modalNotice("Upgrade rejected", `${b.name}: ${e.message}`);
            return;
          }
          this.upgradesInProgress.delete(b.id);
          const upgradedBackend = state.backends.find(item => item.id === b.id);
          if (upgradedBackend && result && result.to_version)
            upgradedBackend.remote_version = result.to_version;
          this.syncUpgradeButtons();
          delete state.engCache[b.id];
          delete state.remoteEngineCheckedAt[b.id];
          delete state.remoteEngineErrors[b.id];
          try { await refreshState(); await this.render(); }
          catch (error) { console.warn("post-upgrade settings refresh failed", error); }
          pollRemotes({ forceEngines: true })
            .catch(error => console.warn("post-upgrade remote poll failed", error));
        };
        const rm = el("button", "btn btn-danger btn-sm", "Remove");
        rm.onclick = async () => {
          if (!(await modalConfirm("Remove backend?", `${b.name} (${b.url})`))) return;
          await api(0, `backends/${b.id}`, { method: "DELETE" });
          await refreshState(); await this.render();
        };
        actions.appendChild(test); actions.appendChild(upgrade); actions.appendChild(rm);
        row.appendChild(actions);
        beList.appendChild(row);
      }
    };
    this.inner.appendChild(c3);
    renderBes();
    this.syncUpgradeButtons();
    c3.querySelector("#be-add").onclick = async () => {
      try {
        let paired = {};
        const raw = c3.querySelector("#be-pairing").value.trim();
        if (raw) {
          try { paired = JSON.parse(raw); }
          catch (e) { throw new Error("invalid pairing JSON"); }
          if (!paired || typeof paired !== "object" || Array.isArray(paired))
            throw new Error("invalid pairing JSON");
        }
        const pairingValue = (id, key) => {
          const entered = c3.querySelector(id).value.trim();
          return entered || (typeof paired[key] === "string" ? paired[key] : "");
        };
        await api(0, "backends", { method: "POST", body: {
          name: pairingValue("#be-name", "name"),
          url: pairingValue("#be-url", "url"),
          token: pairingValue("#be-token", "token"),
          tls_fingerprint: pairingValue("#be-tls", "tls_sha256"),
          auto_upgrade: c3.querySelector("#be-auto").checked,
        }});
        toast("backend added", "ok");
        c3.querySelector("#be-name").value = c3.querySelector("#be-url").value =
        c3.querySelector("#be-token").value = c3.querySelector("#be-tls").value = "";
        c3.querySelector("#be-pairing").value = "";
        c3.querySelector("#be-auto").checked = false;
        await refreshState(); await this.render();
        pollRemotes({ forceEngines: true })
          .catch(error => console.warn("new-backend poll failed", error));
      } catch (e) { toast(e.message, "error"); }
    };
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

    /* backup and restore */
    const c5 = el("div", "card snapshot-card");
    c5.innerHTML = `<h2>Backup &amp; restore</h2>
      <p class="snapshot-copy">A backup restores this instance’s settings, accounts, backend
        connections, local sessions and transcripts, uploads, scratch workspaces, tabs, and drafts.
        Remote sessions remain on their registered backends. Ordinary project directories and
        engine sign-ins/native caches remain on their machines.</p>
      <p class="snapshot-warning">The archive contains private credentials and API tokens.
        Only import a backup you trust, and store it securely.</p>
      <div class="snapshot-actions">
        <button class="btn btn-pri btn-sm" id="snapshot-export">Export backup</button>
        <button class="btn btn-sm" id="snapshot-import">Import backup…</button>
        <input class="hidden" type="file" id="snapshot-file"
          accept=".tar.gz,application/gzip,application/x-gzip">
      </div>`;
    const exportButton = c5.querySelector("#snapshot-export");
    const importButton = c5.querySelector("#snapshot-import");
    const fileInput = c5.querySelector("#snapshot-file");
    exportButton.onclick = async () => {
      exportButton.disabled = true;
      importButton.disabled = true;
      exportButton.textContent = "Preparing…";
      try {
        const prepared = await api(0, "snapshot/export", {
          method: "POST", body: { ui: snapshotBrowserState() },
        });
        const link = el("a");
        link.href = prepared.download;
        link.download = prepared.filename || "puppy-snapshot.tar.gz";
        document.body.appendChild(link);
        link.click();
        link.remove();
        toast(`backup ready · ${prepared.sessions} sessions · ${fmtBytes(prepared.size)}`, "ok");
      } catch (error) {
        toast(error.message, "error", 7000);
      } finally {
        if (exportButton.isConnected) {
          exportButton.disabled = false;
          importButton.disabled = false;
          exportButton.textContent = "Export backup";
        }
      }
    };
    importButton.onclick = () => fileInput.click();
    fileInput.onchange = async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      const confirmed = await modalConfirm("Restore Puppy backup?",
        `This replaces current Puppy settings and sessions with “${file.name}”. ` +
        "Running turns, queued messages, and terminals must be stopped first.");
      if (!confirmed) { fileInput.value = ""; return; }
      exportButton.disabled = true;
      importButton.disabled = true;
      importButton.textContent = "Restoring…";
      try {
        const response = await fetch(apiPath(0, "snapshot/import"), {
          method: "POST", body: file, headers: { "Content-Type": "application/gzip" },
        });
        let result = null;
        try { result = await response.json(); } catch (_) { /* handled below */ }
        if (response.status === 401) { showAuth(); throw new Error("auth required"); }
        if (!response.ok) throw new Error((result && result.error) || `HTTP ${response.status}`);
        restoreBrowserState(result.ui || {});
        location.reload();
      } catch (error) {
        toast(error.message, "error", 8000);
        if (importButton.isConnected) {
          exportButton.disabled = false;
          importButton.disabled = false;
          importButton.textContent = "Import backup…";
          fileInput.value = "";
        }
      }
    };
    this.inner.appendChild(c5);
    this.syncRemoteState();
    this.startUpgradeReadinessPolling();
  }
}

/* ================= modals ================= */
function modal(html, className = "") {
  const back = el("div", "modal-backdrop");
  const m = el("div", "modal" + (className ? " " + className : ""));
  m.innerHTML = html;
  back.appendChild(m);
  $("modal-root").appendChild(back);
  m.querySelectorAll("select").forEach(select => enhanceChoiceSelect(select));
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    closeChoiceMenu();
    back.remove();
    document.removeEventListener("keydown", escH);
  };
  back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
  function escH(e) {
    if (e.key === "Escape" && !e.defaultPrevented && !openChoiceControl) close();
  }
  document.addEventListener("keydown", escH);
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

function modalNotice(title, text) {
  const { m, close } = modal(`<h2>${esc(title)}</h2>
    <p style="color:var(--txt2);font-size:13px;line-height:1.55">${esc(text || "")}</p>
    <div class="m-btns"><button class="btn btn-pri" id="mn-ok">OK</button></div>`);
  m.querySelector("#mn-ok").onclick = close;
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
    <div class="field-lbl">Workspace
      <div class="workspace-pick" id="ns-workspace">
        <button type="button" class="wp sel" data-kind="directory" aria-pressed="true">
          <span class="wp-name">Directory</span><span class="wp-sub">Use a project folder</span>
        </button>
        <button type="button" class="wp" data-kind="temporary" aria-pressed="false">
          <span class="wp-name">Scratch</span><span class="wp-sub">No folder to choose</span>
        </button>
      </div>
    </div>
    <div id="ns-dir-fields">
      <label>Working directory<input type="text" id="ns-cwd" spellcheck="false"></label>
      <div class="dirpick hidden" id="ns-dirs"></div>
      <label class="check" style="margin:8px 0"><input type="checkbox" id="ns-mkdir"> create directory if missing</label>
    </div>
    <p class="hint scratch-note hidden" id="ns-scratch-note">Puppy creates a private empty workspace in the host's temporary storage (normally /tmp). It survives Puppy restarts and is deleted with this session, but the host may clear it—commonly on reboot. The transcript is kept and Puppy can start a fresh workspace.</p>
    <label>Name <span style="text-transform:none;letter-spacing:0">(optional, auto from first message)</span><input type="text" id="ns-name"></label>
    <div class="field-row">
      <label>Model<select id="ns-model"></select></label>
      <label>Effort<select id="ns-effort"></select></label>
      <label>Permissions<select id="ns-perm"></select></label>
    </div>
    <label class="hidden" id="ns-model-custom-wrap">Custom model<input type="text" id="ns-model-custom" placeholder="model id"></label>
    <div class="field-lbl" style="margin-top:8px">Color<div class="swatch-row" id="ns-colors"></div></div>
    <div class="m-btns"><button class="btn" id="ns-cancel">Cancel</button><button class="btn btn-pri" id="ns-go">Start session</button></div>`,
    "new-session-modal");

  const beSel = m.querySelector("#ns-be");
  const engBox = m.querySelector("#ns-engines");
  const permSel = m.querySelector("#ns-perm");
  const modelSel = m.querySelector("#ns-model");
  const effortSel = m.querySelector("#ns-effort");
  const customWrap = m.querySelector("#ns-model-custom-wrap");
  const customInp = m.querySelector("#ns-model-custom");
  const workspaceBox = m.querySelector("#ns-workspace");
  const directoryFields = m.querySelector("#ns-dir-fields");
  const scratchNote = m.querySelector("#ns-scratch-note");
  const scratchButton = workspaceBox.querySelector('[data-kind="temporary"]');
  let workspaceKind = "directory";
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
  let engineLoadSequence = 0;

  function pickWorkspace(kind) {
    if (kind === "temporary" && scratchButton.disabled) return;
    workspaceKind = kind;
    workspaceBox.querySelectorAll(".wp").forEach(button => {
      const selected = button.dataset.kind === kind;
      button.classList.toggle("sel", selected);
      button.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    directoryFields.classList.toggle("hidden", kind !== "directory");
    scratchNote.classList.toggle("hidden", kind !== "temporary");
    if (kind !== "directory") dirBox.classList.add("hidden");
  }
  workspaceBox.querySelectorAll(".wp").forEach(button => {
    button.onclick = () => pickWorkspace(button.dataset.kind);
  });

  function syncWorkspaceSupport() {
    const supported = backendSupportsScratch(parseInt(beSel.value, 10));
    scratchButton.disabled = !supported;
    scratchButton.title = supported ? "" : "Upgrade this backend to use scratch workspaces";
    scratchButton.querySelector(".wp-sub").textContent = supported ?
      "No folder to choose" : "Backend upgrade required";
    if (!supported && workspaceKind === "temporary") pickWorkspace("directory");
  }

  async function loadEngines() {
    const bid = parseInt(beSel.value, 10);
    const sequence = ++engineLoadSequence;
    let loaded = [];
    try {
      if (bid === 0) loaded = state.engines;
      else {
        if (!Object.prototype.hasOwnProperty.call(state.engCache, bid)) {
          const result = await api(bid, "engines", { timeoutMs: ENGINE_POLL_TIMEOUT });
          if (!result || !Array.isArray(result.engines))
            throw new Error("backend returned an invalid engines response");
          state.engCache[bid] = result.engines;
          if (result.usage_refresh) state.remoteUsageRefresh[bid] = result.usage_refresh;
          state.remoteEngineCheckedAt[bid] = Date.now();
          delete state.remoteEngineErrors[bid];
        }
        loaded = state.engCache[bid];
      }
    } catch (e) {
      if (sequence !== engineLoadSequence || parseInt(beSel.value, 10) !== bid) return;
      toast("backend unavailable: " + e.message, "error");
      if (bid) {
        state.remoteEngineErrors[bid] = e.message;
        syncRemoteStateViews();
        pollRemotes({ forceEngines: true })
          .catch(error => console.warn("new-session recovery poll failed", error));
      }
    }
    if (sequence !== engineLoadSequence || parseInt(beSel.value, 10) !== bid) return;
    engines = loaded;
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
      refreshChoiceSelect(sel);
    };
    fill(permSel, e2 ? e2.permission_options : [], e2 ? e2.default_permission : "");
    fill(modelSel, (e2 ? e2.model_options : []).concat([{ value: "__custom__", label: "Custom…" }]), "");
    fill(effortSel, e2 ? e2.effort_options : [], "");
    modelSel.onchange();
  }
  beSel.onchange = () => { syncWorkspaceSupport(); loadEngines(); };
  syncWorkspaceSupport();
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
        engine, workspace_kind: workspaceKind,
        cwd: workspaceKind === "directory" ? cwdInp.value.trim() : "",
        name: m.querySelector("#ns-name").value,
        model: modelSel.value === "__custom__" ? customInp.value.trim() : modelSel.value,
        effort: effortSel.value, permission_mode: permSel.value, color: nsColor,
        mkdir: workspaceKind === "directory" && m.querySelector("#ns-mkdir").checked,
      }});
      if (workspaceKind === "directory") lsSet("puppy.lastcwd", cwdInp.value.trim());
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
      const matches = g.sessions.filter(s => !q || (s.name || "").toLowerCase().includes(q) ||
        (s.cwd || "").toLowerCase().includes(q) || workspaceLabel(s).toLowerCase().includes(q));
      if (!matches.length) continue;
      if (groups.length > 1) list.appendChild(el("div", "sess-group-title", g.name));
      for (const s of matches) {
        const item = el("button", "sess-item");
        const r1 = el("div", "si-row");
        r1.appendChild(sessDot(s));
        r1.appendChild(el("div", "si-name", (s.name || `session ${s.id}`) + (s.archived ? " (archived)" : "")));
        const r2 = el("div", "si-row sub");
        r2.appendChild(provIcon(s.engine));
        const workspace = el("div", "si-sub" + (s.workspace_missing ? " warn" : ""),
          workspaceLabel(s));
        workspace.title = workspaceTitle(s);
        r2.appendChild(workspace);
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
  const beOpts = [{ id: 0, name: backendName(0) }]
    .concat(state.backends.filter(b => backendHasCapability(b, "terminal")));
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
