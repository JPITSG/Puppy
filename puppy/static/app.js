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

function bellIcon(size, off) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const draw = (d) => {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", d);
    p.setAttribute("stroke", "currentColor");
    /* lighter than the 2 the other drawn icons use: this one is rendered at
       24-box scale in a 14px button, where 2 reads heavy beside ⚙ and ☀ */
    p.setAttribute("stroke-width", "1.5");
    p.setAttribute("stroke-linecap", "round");
    p.setAttribute("stroke-linejoin", "round");
    p.setAttribute("fill", "none");
    svg.appendChild(p);
  };
  draw("M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3c0 0 3-2 3-9");
  draw("M10.3 21a1.94 1.94 0 0 0 3.4 0");
  if (off) draw("M4.5 3.5 L19.5 20.5");
  return svg;
}

/* Same reason as the close cross above: a "+" character is placed on the font's
   math axis, which is not the middle of its line box, so the glyph lands about
   1.5px low in a flex-centred button however the box is aligned. Drawn ink is
   centred by construction, on every font and platform. */
function plusIcon(size) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 12 12");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(NS, "path");
  p.setAttribute("d", "M6 2.75 L6 9.25 M2.75 6 L9.25 6");
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

function terminalIcon(size) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const frame = document.createElementNS(NS, "rect");
  frame.setAttribute("x", "1.5");
  frame.setAttribute("y", "2.5");
  frame.setAttribute("width", "13");
  frame.setAttribute("height", "11");
  frame.setAttribute("rx", "1.5");
  frame.setAttribute("fill", "none");
  frame.setAttribute("stroke", "currentColor");
  frame.setAttribute("stroke-width", "1.15");
  const prompt = document.createElementNS(NS, "path");
  prompt.setAttribute("d", "M4.25 6 6.25 8 4.25 10 M8 10h3.5");
  prompt.setAttribute("fill", "none");
  prompt.setAttribute("stroke", "currentColor");
  prompt.setAttribute("stroke-width", "1.15");
  prompt.setAttribute("stroke-linecap", "round");
  prompt.setAttribute("stroke-linejoin", "round");
  svg.appendChild(frame);
  svg.appendChild(prompt);
  return svg;
}

function globeIcon(size) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", "M8 1.75a6.25 6.25 0 1 0 0 12.5 6.25 6.25 0 0 0 0-12.5Z" +
    "M8 1.75c-1.9 1.7-2.85 3.85-2.85 6.25S6.1 12.55 8 14.25" +
    "m0-12.5c1.9 1.7 2.85 3.85 2.85 6.25S9.9 12.55 8 14.25" +
    "M2.2 5.9h11.6M2.2 10.1h11.6");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.15");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  svg.appendChild(path);
  return svg;
}

function refreshIcon(size = 10) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 12 12");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", "M10 6a4 4 0 0 1-6.8 2.8L2 7.6m0 0V10m0-2.4h2.4 " +
    "M2 6a4 4 0 0 1 6.8-2.8L10 4.4m0 0V2m0 2.4H7.6");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.2");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  svg.appendChild(path);
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

function attachmentFileIcon(size = 18) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 18 18");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const page = document.createElementNS(NS, "path");
  page.setAttribute("d", "M4 1.75h6.1L14 5.65v10.6H4Z M10 1.75v4h4");
  page.setAttribute("fill", "none");
  page.setAttribute("stroke", "currentColor");
  page.setAttribute("stroke-width", "1.2");
  page.setAttribute("stroke-linejoin", "round");
  svg.appendChild(page);
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

/* A mouse has a natural double-click; a touchscreen does not. Track the
   pointer that produced each click instead of guessing from viewport size, so
   both modes keep working on a convertible with mouse and touch attached. */
function activationPointer(target) {
  let pointerType = "";
  target.addEventListener("pointerdown", event => {
    if (event.isPrimary === false) return;
    pointerType = event.pointerType || "";
  });
  target.addEventListener("pointercancel", () => { pointerType = ""; });
  return event => {
    const type = event.pointerType || pointerType;
    pointerType = "";
    return type;
  };
}

/* Browsers may follow contextmenu with a compatibility click from the same
   gesture. Keep this guard at document level: session rows and tabs are live
   DOM and may be replaced between those two events. A genuinely new pointer,
   touch, or keyboard gesture disarms it before that gesture can click, so a
   desktop right-click followed by a left-click remains ordinary activation. */
let suppressContextGestureClick = false;

function clearContextGestureClick() {
  suppressContextGestureClick = false;
}

document.addEventListener("pointerdown", clearContextGestureClick, true);
document.addEventListener("touchstart", clearContextGestureClick,
  { capture: true, passive: true });
document.addEventListener("keydown", clearContextGestureClick, true);
document.addEventListener("click", event => {
  if (!suppressContextGestureClick) return;
  clearContextGestureClick();
  event.preventDefault();
  event.stopImmediatePropagation();
}, true);

function suppressContextGestureActivation(target) {
  target.addEventListener("contextmenu", () => {
    suppressContextGestureClick = true;
  }, true);
}

/* Native HTML drag and touch long-press compete for the same gesture on mobile.
   Disable draggability for that touch before the browser runs its default
   pointerdown action, then restore it when the gesture ends. The dragstart
   check is a fallback for engines that already committed to a native drag.
   A later mouse pointerdown re-enables ordinary desktop reordering. */
function guardNativeTouchDrag(target) {
  let touchOrigin = false;
  const startTouch = () => {
    touchOrigin = true;
    target.draggable = false;
  };
  const finishTouch = () => { target.draggable = true; };
  target.addEventListener("pointerdown", event => {
    if (event.isPrimary === false) return;
    touchOrigin = event.pointerType === "touch";
    target.draggable = !touchOrigin;
  }, true);
  target.addEventListener("touchstart", startTouch, { capture: true, passive: true });
  target.addEventListener("pointerup", finishTouch, true);
  target.addEventListener("pointercancel", finishTouch, true);
  target.addEventListener("touchend", finishTouch, true);
  target.addEventListener("touchcancel", finishTouch, true);
  return event => {
    if (!touchOrigin) return false;
    event.preventDefault();
    target.draggable = true;
    return true;
  };
}

function wireDoubleClickOrTouch(target, activate) {
  const pointerForClick = activationPointer(target);
  target.addEventListener("click", event => {
    const pointerType = pointerForClick(event);
    if (pointerType !== "touch" && event.detail !== 2) return;
    event.preventDefault();
    event.stopPropagation();
    activate();
  });
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
      const hint = option ? (option.title || "") : "";
      if (hint) button.title = hint;
      else {
        button.removeAttribute("title");
        button.removeAttribute("data-tip");
      }
      button.setAttribute("aria-label", fieldName ? `${fieldName}: ${text}` : text);
    },
  };
  select._choiceControl = control;

  /* `pointer` marks a highlight the mouse is responsible for: it does not move
     the keyboard cursor, so leaving the menu can put the highlight back where
     the keys left it. `quiet` skips the scroll - only key presses may move the
     list under the pointer. */
  const setActive = (index, { pointer = false, quiet = false } = {}) => {
    if (!control.menu || index < 0 || index >= select.options.length ||
        select.options[index].disabled) return;
    control.activeIndex = index;
    if (!pointer) control.cursorIndex = index;
    control.menu.querySelectorAll(".choice-option").forEach((row, i) =>
      row.classList.toggle("active", i === index));
    const row = control.menu.querySelector(`[data-choice-index="${index}"]`);
    if (row) {
      button.setAttribute("aria-activedescendant", row.id);
      if (!quiet) row.scrollIntoView({ block: "nearest" });
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
      if (option.title) row.title = option.title;
      row.onmouseenter = () => setActive(index, { pointer: true, quiet: true });
      row.onmousedown = (event) => event.preventDefault();
      row.onclick = (event) => { event.stopPropagation(); choose(index); };
      menu.appendChild(row);
    });
    /* the pointer takes its highlight with it when it goes */
    menu.onmouseleave = () => setActive(control.cursorIndex, { quiet: true });
    menu.onclick = (event) => event.stopPropagation();
    document.body.appendChild(menu);
    menu.style.maxHeight = Math.max(80, window.innerHeight - 16) + "px";
    control.menu = menu;
    control.activeIndex = control.cursorIndex = select.selectedIndex;
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
  requestAnimationFrame(syncAllTabOverflow);
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

/* ---- engine configuration shorthand ----
   Names an engine + model + effort compactly ("codex 5.6 Sol Max"). Model and
   effort vocabularies are driver-supplied and change on their own (codex reads
   its CLI's model cache), so nothing here may know a model by name: display
   text comes from the live option lists, and the only compaction is peeling off
   a leading family word that most of that engine's models carry - "GPT-5.6-Sol"
   loses its "GPT" because "GPT-5.5" and the rest repeat it, while claude's
   Fable / Opus / Sonnet share nothing and survive whole. Most, not all: real
   catalogs mix in one-off names (codex ships a "Daybreak Blue"), and a single
   outlier must not make the whole family keep its prefix. A model the lists no
   longer offer (custom id, retired slug) still prints, just uncompacted. */
function engineInfo(bid, key) {
  const list = (bid && Array.isArray(state.engCache[bid])) ? state.engCache[bid] : state.engines;
  return (Array.isArray(list) && list.find(e => e.key === key)) || state.engMap[key] || null;
}

const headWord = (s) => (String(s).match(/^[^\s\-_/]+/) || [""])[0];
const tailWords = (s) => String(s).slice(headWord(s).length).replace(/^[\s\-_/]+/, "");

function modelShorthand(eng, value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const options = ((eng && eng.model_options) || []).filter(o => o && o.value);
  const match = options.find(o => o.value === raw);
  let text = (match && match.label) || raw;
  let peers = options.map(o => o.label || o.value);
  while (peers.length > 1 && tailWords(text)) {
    const head = headWord(text).toUpperCase();
    /* a word carrying digits is a version, not a family name: "GPT-6 Mini"
       gives up its GPT but keeps the 6 even when every model shares it */
    if (!head || /\d/.test(head)) break;
    const shares = peers.filter(p => headWord(p).toUpperCase() === head);
    if (shares.length < 2 || shares.length * 2 < peers.length) break;
    text = tailWords(text);
    peers = peers.map(p => headWord(p).toUpperCase() === head ? tailWords(p) : p).filter(Boolean);
  }
  return text;
}

function effortShorthand(eng, value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const match = ((eng && eng.effort_options) || []).find(o => o && o.value === raw);
  return (match && match.label) || raw;
}

/* "5.6-Sol Max", or "" when both sit on the engine's defaults. `blank` names
   the default model for lines where the engine is not there to carry it. */
function engineConfigDetail(bid, engine, model, effort, blank) {
  const eng = engineInfo(bid, engine);
  return [modelShorthand(eng, model) || blank || "", effortShorthand(eng, effort)]
    .filter(Boolean).join(" ");
}

/* [engine, "model effort"] - the caller styles the two apart */
function engineConfigParts(bid, engine, model, effort) {
  return [String(engine || "?"), engineConfigDetail(bid, engine, model, effort)];
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
    button.setAttribute("aria-label", "Copy code");
    button.appendChild(copyIcon());
    button.onclick = async (e) => {
      e.preventDefault(); e.stopPropagation();
      try {
        await writeClipboardText(String(code.textContent || "").replace(/\n$/, ""));
        clearTimeout(button._copyReset);
        button.classList.add("done");
        button.replaceChildren(copyIcon(true));
        button.setAttribute("aria-label", "Copied");
        button._copyReset = setTimeout(() => {
          if (!button.isConnected) return;
          button.classList.remove("done");
          button.replaceChildren(copyIcon());
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

/* ================= tooltips ================= */
/* Custom title bubbles. A capture-phase pointerover moves an element's native
   `title` into `data-tip` the first time it is hovered (suppressing the
   browser bubble) and positions one shared fixed #tip node near the anchor,
   so every current and future `.title =` assignment keeps working unchanged.
   Empty titles keep an empty `data-tip` so they still suppress an ancestor's
   bubble the way `title=""` does natively. */
const tips = (() => {
  const SHOW_MS = 500;   // first-hover delay
  const WARM_MS = 350;   // instant re-show window after a hide
  const GAP = 7, EDGE = 8;
  let root = null, card = null;
  let anchor = null;     // element the visible bubble belongs to
  let pending = null;    // element waiting out SHOW_MS
  let mode = "pointer";  // how the bubble was summoned: pointer | focus
  let showTimer = 0, outTimer = 0, fadeTimer = 0, tick = 0;
  let warmUntil = 0, lastX = -1, lastY = -1;
  let aimX = -1;         // where the bubble points, taken from the pointer at show time

  /* the freshest tip for an element: a live `title` wins over the adopted
     copy because renderers keep assigning `.title` after adoption */
  const text = (a) => {
    if (!a) return "";
    const v = a.hasAttribute("title") ? a.getAttribute("title") : a.getAttribute("data-tip");
    return (v || "").trim();
  };

  /* title -> data-tip; glyph-only controls keep the text as their name */
  const adopt = (a) => {
    if (!a.hasAttribute("title")) return;
    const t = a.getAttribute("title") || "";
    a.removeAttribute("title");
    a.setAttribute("data-tip", t);
    const owned = a.hasAttribute("data-tip-label");
    if (owned || (!a.hasAttribute("aria-label") && !a.hasAttribute("aria-labelledby") &&
        (a.textContent || "").trim().length <= 2)) {
      if (t.trim()) { a.setAttribute("aria-label", t); a.setAttribute("data-tip-label", "1"); }
      else if (owned) { a.removeAttribute("aria-label"); a.removeAttribute("data-tip-label"); }
    }
  };

  const anchorOf = (n) => (n instanceof Element) ? n.closest("[title],[data-tip]") : null;

  function place(glide) {
    const r = anchor.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    const w = root.offsetWidth, h = root.offsetHeight;
    let side = (r.top + r.bottom) / 2 < vh / 2 ? "bottom" : "top";
    if (side === "bottom" && r.bottom + GAP + h > vh - EDGE && r.top - GAP - h >= EDGE) side = "top";
    else if (side === "top" && r.top - GAP - h < EDGE && r.bottom + GAP + h <= vh - EDGE) side = "bottom";
    const y = side === "bottom" ? Math.min(r.bottom + GAP, vh - EDGE - h) : Math.max(r.top - GAP - h, EDGE);
    /* Centre on the anchor while the bubble is as wide as it - the tidy look for
       buttons and chips. Wider anchors get the bubble over the pointer instead:
       a full-width row's centre can be an arm's length from the words actually
       being hovered. Frozen at show time so it does not chase the mouse. */
    const cx = (aimX >= 0 && r.width > w) ? Math.min(Math.max(aimX, r.left), r.right)
      : (r.left + r.right) / 2;
    const x = Math.max(EDGE, Math.min(cx - w / 2, vw - EDGE - w));
    root.classList.toggle("glide", !!glide);
    root.dataset.side = side;
    root.style.transform = `translate(${Math.round(x)}px,${Math.round(y)}px)`;
  }

  function show(a, why) {
    if (!root) {
      root = el("div");
      root.id = "tip";
      root.hidden = true;
      root.setAttribute("aria-hidden", "true");
      card = el("div", "tip-card");
      root.appendChild(card);
      document.body.appendChild(root);
    }
    clearTimeout(showTimer); showTimer = 0; pending = null;
    clearTimeout(outTimer); outTimer = 0;
    clearTimeout(fadeTimer); fadeTimer = 0;
    const t = text(a);
    if (!t) { hide(); return; }
    const glide = !root.hidden && anchor !== a;
    anchor = a; mode = why;
    aimX = why === "pointer" ? lastX : -1;   // keyboard focus has no pointer to aim at
    root.classList.remove("out");
    if (card.textContent !== t) card.textContent = t;
    if (root.hidden) { root.hidden = false; root.classList.remove("glide"); }
    place(glide);
    if (!tick) tick = setInterval(onTick, 300);
  }

  function hide() {
    clearTimeout(showTimer); showTimer = 0; pending = null;
    clearTimeout(outTimer); outTimer = 0;
    if (tick) { clearInterval(tick); tick = 0; }
    anchor = null;
    if (!root || root.hidden) return;
    warmUntil = performance.now() + WARM_MS;
    root.classList.add("out");
    clearTimeout(fadeTimer);
    fadeTimer = setTimeout(() => { root.hidden = true; root.classList.remove("out", "glide"); }, 130);
  }

  function kill() { hide(); warmUntil = 0; }

  /* re-renders replace hovered nodes without any pointer event: re-resolve
     the element under the pointer and follow it; a surviving anchor gets its
     text refreshed (live meters) and its drift tracked (reorder animations) */
  function onTick() {
    if (!anchor) return;
    if (mode === "focus") { if (!anchor.isConnected) hide(); return; }
    const a = anchorOf(document.elementFromPoint(lastX, lastY));
    if (!a) { hide(); return; }
    adopt(a);
    const t = text(a);
    if (!t) { hide(); return; }
    if (a !== anchor) { show(a, "pointer"); return; }
    if (card.textContent !== t) card.textContent = t;
    place(true);
  }

  function onOver(e) {
    if (e.pointerType === "touch") return;
    lastX = e.clientX; lastY = e.clientY;
    const a = anchorOf(e.target);
    if (!a) return;
    adopt(a);
    if (!text(a)) return;
    if (a === anchor) { clearTimeout(outTimer); outTimer = 0; return; }
    if ((root && !root.hidden) || performance.now() < warmUntil) { show(a, "pointer"); return; }
    if (pending === a) return;
    pending = a;
    clearTimeout(showTimer);
    showTimer = setTimeout(() => {
      showTimer = 0;
      const p = pending; pending = null;
      if (p && p.isConnected) show(p, "pointer");
    }, SHOW_MS);
  }

  function onOut(e) {
    const t = e.target;
    if (!(t instanceof Element)) return;
    const leaves = (a) => a && (t === a || a.contains(t)) &&
      !(e.relatedTarget instanceof Element && a.contains(e.relatedTarget));
    if (leaves(pending)) { clearTimeout(showTimer); showTimer = 0; pending = null; }
    if (leaves(anchor) && !outTimer) {
      /* brief linger so a hop onto an adjacent anchor glides instead */
      outTimer = setTimeout(() => { outTimer = 0; hide(); }, 70);
    }
  }

  function onScroll(e) {
    const a = anchor || pending;
    if (!a) return;
    const s = e.target;
    if (s === document || s === window || (s instanceof Element && s.contains(a))) hide();
  }

  function onFocusIn(e) {
    let fv = false;
    try { fv = e.target instanceof Element && e.target.matches(":focus-visible"); }
    catch (err) { /* selector unsupported: skip focus bubbles */ }
    if (!fv) return;
    const a = anchorOf(e.target);
    if (!a) return;
    adopt(a);
    if (text(a)) show(a, "focus");
  }

  const opts = { capture: true, passive: true };
  document.addEventListener("pointerover", onOver, opts);
  document.addEventListener("pointerout", onOut, opts);
  document.addEventListener("pointermove", (e) => {
    if (anchor || pending) { lastX = e.clientX; lastY = e.clientY; }
  }, opts);
  document.addEventListener("pointerdown", kill, opts);
  document.addEventListener("dragstart", kill, opts);
  document.addEventListener("dragenter", kill, opts);
  document.addEventListener("scroll", onScroll, opts);
  window.addEventListener("resize", kill);
  window.addEventListener("blur", kill);
  document.addEventListener("visibilitychange", () => { if (document.hidden) kill(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") kill();
    else if (mode === "focus" && anchor && e.key !== "Tab") hide();
  }, true);
  document.addEventListener("focusin", onFocusIn);
  document.addEventListener("focusout", () => { if (mode === "focus") hide(); });
  return { text };
})();

/* ================= state ================= */
const state = {
  authed: false,
  instance: "",
  engines: [],            // local engines info
  engMap: {},             // key -> engine info (local)
  usageRefresh: null,     // local account-usage refresh metadata
  uploadSettings: null,   // local per-file upload policy
  localEngineCheckedAt: 0,
  backends: [],           // remote backends [{id,name,url}]
  sessions: [],           // local sessions (live via updates ws)
  remoteSessions: {},     // bid -> sessions[]
  remoteOk: {},           // bid -> bool
  remoteErrors: {},       // bid -> latest reachability error
  engCache: {},           // bid -> engines[]
  nodeUsers: {},          // bid -> account the node's puppy process runs as
  notify: { configured: false, enabled: false },   // completion-alert bell
  browser: { enabled: false },  // this instance's managed-browser toggle
  remoteBrowser: {},      // bid -> {enabled} from that node's ping metadata
  remoteEngineErrors: {}, // bid -> latest engine-status error (node can still be reachable)
  remoteEngineCheckedAt: {},
  remoteNodeCheckedAt: {},
  remoteUsageRefresh: {}, // bid -> account-usage refresh metadata
  remoteUploadSettings: {}, // bid -> remote per-file upload policy
  tabs: [],               // [{id,type,bid,sid,browserId,title,cmd}]
  active: null,           // focused tab id (each pane also has its own active tab)
  activeGroup: null,      // focused workspace pane id
  selectedSession: null,  // sidebar selection, independent until a session tab is focused
  layout: null,           // recursive pane/split tree
  showArchived: false,
  views: {},              // tab id -> view object
};

const REMOTE_POLL_INTERVAL = 12000;
const REMOTE_POLL_TIMEOUT = 5000;
const REMOTE_ENGINE_REFRESH = 60000;
const ENGINE_POLL_TIMEOUT = 15000;
const UPGRADE_READINESS_INTERVAL = 2000;
const UPGRADE_READINESS_TIMEOUT = 4000;
/* An engine updater shells out to a package manager, so progress is measured
   in tens of seconds. Poll gently and only while one is actually running. */
const ENGINE_UPGRADE_POLL_INTERVAL = 3000;
const ENGINE_REFRESH_TIMEOUT = 30000;
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
    if (sessionActivityAnchors.has(key)) {
      const startedAt = sessionActivityAnchors.get(key);
      sessionActivityAnchors.delete(key);
      reportRemoteCompletion(bid, session, startedAt, receivedAt);
    }
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

/* Completions on remote backends are seen here, by whichever consoles are
   watching (session tab streams and the 12-second polls both funnel through
   the activity ingest). Report them; the controller deduplicates, so several
   open browsers ring once. Local sessions fire on the server itself and are
   deliberately not reported. */
function reportRemoteCompletion(bid, session, startedAt, now) {
  if (!bid || !state.notify.configured || !state.notify.enabled) return;
  api(0, "notify/fire", { method: "POST", body: {
    bid, sid: session.id,
    info: {
      session: session.name || `session ${session.id}`,
      engine: session.engine || "",
      model: session.model || session.last_model || "",
      status: "done",
      duration: String(Math.max(0, Math.round((now - Number(startedAt || now)) / 1000))),
      cwd: session.cwd || "",
    },
  } }).catch(() => { /* the next completion tries again */ });
}

function reconcileRemoteState() {
  const live = new Set(state.backends.map(backend => String(backend.id)));
  for (const bucket of [state.remoteSessions, state.remoteOk, state.remoteErrors,
                        state.engCache, state.remoteEngineErrors,
                        state.remoteEngineCheckedAt, state.remoteNodeCheckedAt,
                        state.remoteUsageRefresh, state.remoteUploadSettings,
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

let workspaceIdSequence = 1;
let workspaceRevision = 0;

function workspaceId(prefix) {
  return `${prefix}:${Date.now().toString(36)}:${workspaceIdSequence++}`;
}

function newPane(tabIds = [], active = null) {
  const tabs = [...tabIds];
  return {
    kind: "pane", id: workspaceId("pane"), tabs,
    active: tabs.includes(active) ? active : (tabs[0] || null),
  };
}

function newSplit(axis, first, second, ratio = .5) {
  return {
    kind: "split", id: workspaceId("split"), axis: axis === "column" ? "column" : "row",
    ratio: Math.max(.1, Math.min(.9, Number(ratio) || .5)), first, second,
  };
}

function workspacePanes(node = state.layout, result = []) {
  if (!node) return result;
  if (node.kind === "pane") result.push(node);
  else if (node.kind === "split") {
    workspacePanes(node.first, result);
    workspacePanes(node.second, result);
  }
  return result;
}

function workspacePane(id) {
  return workspacePanes().find(pane => pane.id === id) || null;
}

function workspacePaneForTab(tabId) {
  return workspacePanes().find(pane => pane.tabs.includes(tabId)) || null;
}

function workspaceReplaceNode(id, replacement, node = state.layout) {
  if (!node || node.kind !== "split") return false;
  if (node.first && node.first.id === id) { node.first = replacement; return true; }
  if (node.second && node.second.id === id) { node.second = replacement; return true; }
  return workspaceReplaceNode(id, replacement, node.first) ||
    workspaceReplaceNode(id, replacement, node.second);
}

function collapseEmptyPane(pane) {
  if (!pane || pane.tabs.length || !state.layout || state.layout.id === pane.id) return;
  const findParent = (node) => {
    if (!node || node.kind !== "split") return null;
    if (node.first === pane || node.second === pane) return node;
    return findParent(node.first) || findParent(node.second);
  };
  const parent = findParent(state.layout);
  if (!parent) return;
  const sibling = parent.first === pane ? parent.second : parent.first;
  if (state.layout === parent) state.layout = sibling;
  else workspaceReplaceNode(parent.id, sibling);
}

function removeTabFromPane(tabId) {
  const pane = workspacePaneForTab(tabId);
  if (!pane) return null;
  const index = pane.tabs.indexOf(tabId);
  pane.tabs.splice(index, 1);
  if (pane.active === tabId)
    pane.active = pane.tabs[Math.max(0, index - 1)] || null;
  return pane;
}

function syncTabOrderFromLayout() {
  const byId = new Map(state.tabs.map(tab => [tab.id, tab]));
  const ordered = [];
  for (const pane of workspacePanes()) {
    for (const id of pane.tabs) {
      const tab = byId.get(id);
      if (tab) { ordered.push(tab); byId.delete(id); }
    }
  }
  for (const tab of state.tabs) if (byId.has(tab.id)) ordered.push(tab);
  state.tabs = ordered;
}

/* Saved layouts are browser-owned state, but still normalize them strictly:
   stale tabs disappear, duplicate membership is ignored, empty branches
   collapse, and old flat tab archives become one pane. */
function normalizeWorkspace() {
  const validTabs = new Set(state.tabs.map(tab => tab.id));
  const assignedTabs = new Set();
  const nodeIds = new Set();
  const safeId = (value, prefix) => {
    if (typeof value === "string" && value.length <= 120 && !nodeIds.has(value)) {
      nodeIds.add(value); return value;
    }
    let id;
    do { id = workspaceId(prefix); } while (nodeIds.has(id));
    nodeIds.add(id); return id;
  };
  const walk = (node, depth) => {
    if (!node || typeof node !== "object" || depth > 24) return null;
    if (node.kind === "pane") {
      const tabs = [];
      if (Array.isArray(node.tabs)) for (const id of node.tabs) {
        if (typeof id === "string" && validTabs.has(id) && !assignedTabs.has(id)) {
          assignedTabs.add(id); tabs.push(id);
        }
      }
      return {
        kind: "pane", id: safeId(node.id, "pane"), tabs,
        active: tabs.includes(node.active) ? node.active : (tabs[0] || null),
      };
    }
    if (node.kind !== "split") return null;
    const first = walk(node.first, depth + 1);
    const second = walk(node.second, depth + 1);
    if (!first) return second;
    if (!second) return first;
    if (first.kind === "pane" && !first.tabs.length) return second;
    if (second.kind === "pane" && !second.tabs.length) return first;
    const ratio = Number(node.ratio);
    return {
      kind: "split", id: safeId(node.id, "split"),
      axis: node.axis === "column" ? "column" : "row",
      ratio: Number.isFinite(ratio) ? Math.max(.1, Math.min(.9, ratio)) : .5,
      first, second,
    };
  };

  state.layout = walk(state.layout, 0) || newPane();
  let panes = workspacePanes();
  let target = panes.find(pane => pane.id === state.activeGroup) ||
    panes.find(pane => pane.tabs.includes(state.active)) || panes[0];
  for (const tab of state.tabs) {
    if (!assignedTabs.has(tab.id)) {
      target.tabs.push(tab.id);
      assignedTabs.add(tab.id);
    }
  }
  panes = workspacePanes();
  target = panes.find(pane => pane.id === state.activeGroup) ||
    panes.find(pane => pane.tabs.includes(state.active)) || panes[0];
  if (state.active && target.tabs.includes(state.active)) target.active = state.active;
  if (!target.active || !target.tabs.includes(target.active)) target.active = target.tabs[0] || null;
  state.activeGroup = target.id;
  state.active = target.active;
  syncTabOrderFromLayout();
  workspaceRevision++;
}

function storedWorkspace(node) {
  if (!node || node.kind === "pane") return {
    kind: "pane", id: node ? node.id : workspaceId("pane"),
    tabs: node ? [...node.tabs] : [], active: node ? node.active : null,
  };
  return {
    kind: "split", id: node.id, axis: node.axis,
    ratio: Math.round(node.ratio * 10000) / 10000,
    first: storedWorkspace(node.first), second: storedWorkspace(node.second),
  };
}

function saveTabs() {
  try {
    lsSet("puppy.tabs", JSON.stringify({
      version: 2,
      tabs: state.tabs.map(t => ({
        id: t.id, type: t.type, bid: t.bid, sid: t.sid, title: t.title,
        browserId: t.browserId, cmd: t.cmd, ended: t.ended === true,
      })),
      active: state.active,
      activeGroup: state.activeGroup,
      layout: storedWorkspace(state.layout),
    }));
  } catch (e) {}
}

function storedTabs(value) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  const tabs = [];
  for (const item of value) {
    if (!item || typeof item !== "object" || typeof item.id !== "string" ||
        !item.id || item.id.length > 512 || seen.has(item.id) ||
        !["session", "term", "browser", "settings"].includes(item.type)) continue;
    const tab = { id: item.id, type: item.type };
    if (typeof item.bid === "number" && Number.isFinite(item.bid)) tab.bid = item.bid;
    if (typeof item.sid === "number" && Number.isFinite(item.sid)) tab.sid = item.sid;
    if (item.type === "browser" && typeof item.browserId === "string" &&
        /^[A-Z0-9]{4}$/.test(item.browserId.toUpperCase()))
      tab.browserId = item.browserId.toUpperCase();
    if (typeof item.title === "string") tab.title = item.title.slice(0, 1000);
    if (typeof item.cmd === "string") tab.cmd = item.cmd.slice(0, 10000);
    /* A shell the user ended is restored as ended: reopening the tab must not
       silently start a second login session on that host. */
    if (item.ended === true) tab.ended = true;
    tabs.push(tab);
    seen.add(tab.id);
  }
  return tabs;
}

function loadTabs() {
  try {
    const d = JSON.parse(lsGet("puppy.tabs") || "null");
    if (d && Array.isArray(d.tabs)) {
      state.tabs = storedTabs(d.tabs);
      state.active = typeof d.active === "string" ? d.active : null;
      state.activeGroup = typeof d.activeGroup === "string" ? d.activeGroup : null;
      state.layout = d.layout && typeof d.layout === "object" ? d.layout :
        newPane(state.tabs.map(tab => tab.id), state.active);
    }
  } catch (e) {}
}

/* ================= auth ================= */
let authMode = "login";
function showAuth(mode) {
  if (mode) authMode = mode;
  state.authed = false;
  stopRemotePolling();
  if (updatesWs) try { updatesWs.close(); } catch (error) {}
  setLocalConnection(false);
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
  const valid = state.tabs.filter(t => {
    if (t.type === "session")
      return t.bid ? true : state.sessions.some(s => s.id === t.sid);
    /* Pre-ID singleton tabs cannot identify a logical browser on an upgraded
       node. Drop them instead of silently creating an unnamed extra instance;
       retain them only for older remote nodes using the compatibility route. */
    if (t.type === "browser" && !t.browserId && browserInstancesFor(t.bid || 0))
      return false;
    return true;
  });
  state.tabs = valid;
  normalizeWorkspace();
  renderTabs(); renderSidebar();
  connectUpdates();
  startRemotePolling();
}

async function refreshState() {
  const s = await api(0, "state");
  state.instance = s.instance_name;
  if (typeof s.user === "string") state.nodeUsers[0] = s.user;
  if (s.notify) { state.notify = s.notify; syncBell(); }
  state.sessionColors = s.session_colors || [];
  state.engines = Array.isArray(s.engines) ? s.engines : [];
  state.usageRefresh = s.usage_refresh || state.usageRefresh;
  rememberUploadSettings(0, s.uploads);
  state.localEngineCheckedAt = 0;
  state.engMap = {};
  state.engines.forEach(e => state.engMap[e.key] = e);
  state.backends = Array.isArray(s.backends) ? s.backends : [];
  state.browser = { enabled: !!(s.browser && s.browser.enabled) };
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
    setLocalConnection(true);
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
      } else if (d.type === "host_metrics") {
        renderHostCpu(d.cpu_percent);
      } else if (d.type === "notify") {
        state.notify = { configured: !!d.configured, enabled: !!d.enabled };
        syncBell();
      } else if (d.type === "browser") {
        state.browser = { enabled: !!d.enabled };
        renderSidebar();
      } else if (d.type === "browser_activity") {
        handleBrowserActivity(0, d.session_id, d.turn_id, d.browser_id);
      }
    } catch (e) {}
  };
  ws.onclose = () => {
    if (sequence !== updatesConnectionSequence || updatesWs !== ws) return;
    updatesWs = null;
    setLocalConnection(false);
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

function renderHostCpu(cpuPercent) {
  const output = $("host-cpu");
  const value = typeof cpuPercent === "number" ? cpuPercent : NaN;
  if (!Number.isFinite(value) || value < 0 || value > 100 ||
      !$("conn-dot").classList.contains("ok")) {
    output.textContent = "";
    output.removeAttribute("aria-label");
    output.classList.add("hidden");
    return;
  }
  output.textContent = `CPU ${Math.round(value)}%`;
  output.setAttribute("aria-label", `WebUI host CPU usage: ${value.toFixed(1)}%`);
  output.classList.remove("hidden");
}

function setLocalConnection(connected) {
  const dot = $("conn-dot");
  dot.classList.toggle("ok", connected);
  dot.setAttribute("aria-label", connected ? "Connected" : "Disconnected");
  if (!connected) renderHostCpu(null);
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
    setNodeUser(0, payload.user);
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
      if (node.uploads) rememberUploadSettings(bid, node.uploads);
      const remoteBrowser = { enabled: !!(node.browser && node.browser.enabled) };
      const knownBrowser = state.remoteBrowser[bid];
      if (!knownBrowser || knownBrowser.enabled !== remoteBrowser.enabled) {
        state.remoteBrowser[bid] = remoteBrowser;
        renderSidebar();
      }
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
    setNodeUser(bid, engines.user);
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

/* Shell tabs are titled by where the shell lands: the account the node's
   puppy process runs as. Derived at render time, so it corrects itself when
   the account arrives from a poll, and survives titles saved by older builds. */
function setNodeUser(bid, user) {
  if (typeof user !== "string" || state.nodeUsers[bid] === user) return;
  state.nodeUsers[bid] = user;
  renderTabs();
}
function shellTabTitle(tab) {
  const bid = tab.bid || 0;
  return `${state.nodeUsers[bid] || "shell"} @ ${backendName(bid)}`;
}

function browserTabTitle(tabOrBid, browserId = "") {
  const tab = tabOrBid && typeof tabOrBid === "object" ? tabOrBid : null;
  const bid = tab ? (tab.bid || 0) : (Number(tabOrBid) || 0);
  const id = String(tab ? (tab.browserId || "") : browserId).toUpperCase();
  return id ? `Browser ${id} @ ${backendName(bid)}` : `Browser @ ${backendName(bid)}`;
}

function browserEnabledFor(bid) {
  if (!bid) return !!(state.browser && state.browser.enabled);
  const backend = state.backends.find(item => item.id === bid);
  if (!backend || !backendHasCapability(backend, "browser")) return false;
  return !!(state.remoteBrowser[bid] && state.remoteBrowser[bid].enabled);
}

/* Post-add follow-through for the add form's browser box: the node is only
   reachable once it is registered, so the toggle is honoured here rather than
   pretended at. */
async function enableAddedBackendBrowser(added) {
  const bid = Number(added && added.id) || 0;
  if (!bid) return;
  const name = String((added.remote && added.remote.name) || `backend ${bid}`);
  const capabilities = (added.remote && added.remote.capabilities) || [];
  if (Number(added.remote && added.remote.protocol || 0) !== 0 &&
      !(Array.isArray(capabilities) && capabilities.includes("browser"))) {
    toast(`${name}: this node does not offer a managed browser`, "error", 7000);
    return;
  }
  try {
    const result = await api(bid, "browser/enabled",
      { method: "POST", body: { enabled: true } });
    state.remoteBrowser[bid] = { enabled: !!result.enabled };
    toast(`${name}: browser enabled`, "ok");
  } catch (error) {
    toast(`${name}: browser not enabled · ${error.message}`, "error", 8000);
  }
}

function browserInstancesFor(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  return !!backend && backendHasCapability(backend, "browser-instances");
}

async function openNewBrowser(bid, groupId = null) {
  bid = Number(bid) || 0;
  if (!browserInstancesFor(bid)) {
    openBrowserTab(bid, "", groupId);
    return;
  }
  try {
    const result = await api(bid, "browser/instances", {
      method: "POST", body: {}, timeoutMs: 45000,
    });
    const browserId = String(result && result.browser && result.browser.id || "").toUpperCase();
    if (!/^[A-Z0-9]{4}$/.test(browserId))
      throw new Error("node returned an invalid browser ID");
    openBrowserTab(bid, browserId, groupId);
  } catch (error) {
    toast(`${backendName(bid)}: ${error.message || "could not open browser"}`,
      "error", 7000);
  }
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
    label.setAttribute("aria-label", `Agent active, ${value}`);
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

function backendSupportsManualUsageRefresh(bid) {
  if (!bid) return true;
  const backend = state.backends.find(b => b.id === bid);
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("engine-usage-refresh-manual");
}

/* Forced version re-checks and engine CLI upgrades ship as one additive route
   pair, so one capability gates both controls. Never inferred for older nodes:
   a hidden control is better than a button their router would reject. */
function backendSupportsEngineUpgrade(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("engine-upgrade");
}

function backendSupportsFileUploads(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("file-uploads");
}

function normalizeUploadSettings(value) {
  if (!value || typeof value !== "object") return null;
  const megabytes = Number(value.max_file_size_mb);
  if (!Number.isInteger(megabytes) || megabytes < 0 || megabytes > 1024) return null;
  const expectedBytes = megabytes * 1024 * 1024;
  if (value.max_file_size_bytes != null && Number(value.max_file_size_bytes) !== expectedBytes)
    return null;
  if (typeof value.enabled === "boolean" && value.enabled !== (megabytes > 0)) return null;
  return {
    enabled: megabytes > 0,
    max_file_size_mb: megabytes,
    max_file_size_bytes: expectedBytes,
  };
}

function uploadSettingsFor(bid) {
  return bid ? state.remoteUploadSettings[bid] || null : state.uploadSettings;
}

function rememberUploadSettings(bid, value) {
  const normalized = normalizeUploadSettings(value);
  if (!normalized) return null;
  if (bid) state.remoteUploadSettings[bid] = normalized;
  else state.uploadSettings = normalized;
  for (const view of Object.values(state.views)) {
    if (!view || !view.tab || view.tab.type !== "session" || Number(view.tab.bid || 0) !== Number(bid || 0))
      continue;
    view.uploadPolicy = normalized;
    if (typeof view.syncUploadButton === "function") view.syncUploadButton();
  }
  return normalized;
}

function backendSupportsAutoUpgrade(backend) {
  /* This policy is deliberately narrower than legacy execution inference:
     only a headless node explicitly advertising the signed upgrade contract
     may be armed for unattended replacement. */
  return !!backend && backend.role === "backend" &&
    Array.isArray(backend.capabilities) && backend.capabilities.includes("remote-upgrade");
}

function sidebarSessionKey(bid, sid) {
  return `${Number(bid) || 0}:${String(sid)}`;
}

function focusedSessionKey() {
  const tab = state.tabs.find(item => item.id === state.active);
  return tab && tab.type === "session" ? sidebarSessionKey(tab.bid, tab.sid) : null;
}

function selectSidebarSession(bid, sid) {
  const key = sidebarSessionKey(bid, sid);
  state.selectedSession = key;
  document.querySelectorAll(".sess-item[data-session-key]").forEach(item =>
    item.classList.toggle("active", item.dataset.sessionKey === key));
}

function renderSidebar() {
  if (dragSess && dragSess.item && dragSess.item.isConnected) {
    dragSess.renderPending = true;
    return;
  }
  const root = $("sess-groups");
  root.innerHTML = "";
  const groups = [{ bid: 0, name: backendName(0), ok: true, status: "ok",
    browser: browserEnabledFor(0) }]
    .concat(state.backends.map(b => {
      const status = remoteAvailability(b.id);
      return { bid: b.id, name: b.name, ok: status !== "bad", status,
        terminal: backendHasCapability(b, "terminal"),
        browser: browserEnabledFor(b.id) };
    }));
  const availableSessions = new Set();
  for (const group of groups)
    for (const session of sessionsFor(group.bid))
      availableSessions.add(sidebarSessionKey(group.bid, session.id));
  if (state.selectedSession && !availableSessions.has(state.selectedSession))
    state.selectedSession = null;
  const selectedSession = state.selectedSession || focusedSessionKey();
  const showGroups = groups.length > 1;
  for (const g of groups) {
    const group = el("section", "sess-group");
    const body = el("div", "sess-group-body");
    if (showGroups) {
      const t = el("div", "sess-group-title");
      const dot = el("span", "gdot " + g.status);
      dot.setAttribute("role", "img");
      dot.setAttribute("aria-label", g.bid ? remoteAvailabilityTitle(g.bid) : "available");
      const name = el("span", "sess-group-name", g.name);
      const key = g.bid ? `remote:${g.bid}` : "local";
      const disclosure = disclosureButton(`${g.name} sessions`, body,
        collapsedSessionBackends, "puppy.collapsed.session-backends", key);
      wireDoubleClickOrTouch(name, () => disclosure.click());
      t.appendChild(dot);
      t.appendChild(name);
      if (g.browser) {
        const browse = el("button", "sess-group-terminal sess-group-browser");
        browse.type = "button";
        browse.setAttribute("aria-label", `Open browser on ${g.name}`);
        browse.appendChild(globeIcon(12));
        browse.onclick = event => {
          event.preventDefault();
          event.stopPropagation();
          openNewBrowser(g.bid, state.activeGroup);
          closeDrawer();
        };
        t.appendChild(browse);
      }
      if (!g.bid || g.terminal) {
        const terminal = el("button", "sess-group-terminal");
        terminal.type = "button";
        terminal.setAttribute("aria-label", `Open terminal on ${g.name}`);
        terminal.appendChild(terminalIcon(12));
        terminal.onclick = event => {
          event.preventDefault();
          event.stopPropagation();
          openTermTab(g.bid, "");
          closeDrawer();
        };
        t.appendChild(terminal);
      }
      t.appendChild(disclosure);
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
      item.dataset.sessionId = String(s.id);
      item.dataset.sessionKey = sidebarSessionKey(g.bid, s.id);
      if (selectedSession === item.dataset.sessionKey) item.classList.add("active");
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
        activity.setAttribute("aria-label", `Agent active, ${activity.textContent}`);
      } else {
        activity.classList.add("idle");
        activity.textContent = "IDLE";
        activity.setAttribute("aria-label", "Session idle");
      }
      r1.appendChild(activity);
      const r2 = el("div", "si-row sub");
      r2.appendChild(provIcon(s.engine));
      const workspace = el("div", "si-sub" + (s.workspace_missing ? " warn" : ""),
        workspaceLabel(s));
      workspace.setAttribute("aria-label", workspaceTitle(s));
      r2.appendChild(workspace);
      item.appendChild(r1); item.appendChild(r2);
      const pointerForClick = activationPointer(item);
      item.onclick = event => {
        const pointerType = pointerForClick(event);
        selectSidebarSession(g.bid, s.id);
        /* Keyboard activation has no click count, so it follows touch and opens
           immediately. A mouse opens only on the second click. */
        if (pointerType === "touch" || event.detail === 0 || event.detail === 2) {
          openSessionTab(g.bid, s.id, s);
          closeDrawer();
        }
      };
      suppressContextGestureActivation(item);
      item.addEventListener("contextmenu", (e) => sessionContextMenu(e, g.bid, s));
      wireSessionDrag(item, g.bid, s.id);
      body.appendChild(item);
    }
    group.appendChild(body);
    wireSessionDropZone(group, body, g.bid);
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
  n.setAttribute("role", "img");
  n.setAttribute("aria-label", p || engine || "unknown engine");
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
  /* Mobile WebKit can begin native drag and still deliver contextmenu, but
     omit dragend afterwards. Tear down either app drag before opening the menu
     so no stale gray/reordering state can survive that platform sequence. */
  cancelSessionDrag();
  cancelTabDrag();
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

/* Live sortable layouts. The DOM slot moves during dragover; FLIP animates
   every affected sibling from its old visual position to its new one. */
const REORDER_MOTION_MS = 180;
const REORDER_EASING = "cubic-bezier(.16,1,.3,1)";

function reorderChildren(container, selector) {
  return Array.from(container.children).filter(node => node.matches(selector));
}

function animateChildReorder(container, selector, mutate) {
  const before = new Map(reorderChildren(container, selector)
    .map(node => [node, node.getBoundingClientRect()]));
  mutate();
  const reduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  for (const node of reorderChildren(container, selector)) {
    const first = before.get(node);
    const previous = node._puppyReorderAnimation;
    if (previous) {
      node._puppyReorderAnimation = null;
      previous.cancel();
    }
    if (!first || reduced || typeof node.animate !== "function") continue;
    const last = node.getBoundingClientRect();
    const dx = first.left - last.left;
    const dy = first.top - last.top;
    if (Math.abs(dx) < .5 && Math.abs(dy) < .5) continue;
    const animation = node.animate([
      { transform: `translate(${dx}px,${dy}px)` },
      { transform: "translate(0,0)" },
    ], { duration: REORDER_MOTION_MS, easing: REORDER_EASING });
    node._puppyReorderAnimation = animation;
    const clear = () => {
      if (node._puppyReorderAnimation === animation)
        node._puppyReorderAnimation = null;
    };
    animation.onfinish = clear;
    animation.oncancel = clear;
  }
}

function moveDragSlot(container, dragged, selector, coordinate, horizontal) {
  if (!dragged || dragged.parentElement !== container) return false;
  const current = reorderChildren(container, selector);
  const siblings = current.filter(node => node !== dragged);
  let slot = siblings.length;
  for (let i = 0; i < siblings.length; i++) {
    const box = siblings[i].getBoundingClientRect();
    const midpoint = horizontal ? box.left + box.width / 2 : box.top + box.height / 2;
    if (coordinate < midpoint) { slot = i; break; }
  }
  if (current.indexOf(dragged) === slot) return false;
  animateChildReorder(container, selector, () => {
    if (slot < siblings.length) container.insertBefore(dragged, siblings[slot]);
    else container.appendChild(dragged);
  });
  return true;
}

function restoreDragSlots(context, selector) {
  if (!context.container || !context.container.isConnected) return;
  animateChildReorder(context.container, selector, () => {
    for (const node of context.originalOrder)
      if (node.parentElement === context.container) context.container.appendChild(node);
  });
}

function sortSessionsByOrder(sessions, ids) {
  const positions = new Map(ids.map((id, index) => [id, index]));
  sessions.sort((a, b) => {
    const ap = positions.has(a.id) ? positions.get(a.id) : Number.MAX_SAFE_INTEGER;
    const bp = positions.has(b.id) ? positions.get(b.id) : Number.MAX_SAFE_INTEGER;
    return ap - bp;
  });
}

function acceptReorderDrag(event) {
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
}

/* Sticky manual ordering remains scoped to one backend. Hidden archived rows
   retain their durable slots while the visible rows move around them. */
let dragSess = null;

function cancelSessionDrag(item = null) {
  if (!dragSess || (item && dragSess.item !== item)) return;
  const context = dragSess;
  dragSess = null;
  restoreDragSlots(context, ".sess-item");
  if (context.item) context.item.classList.remove("dragging");
  if (context.container) context.container.classList.remove("reordering");
  if (context.renderPending)
    setTimeout(() => {
      if (dragSess) dragSess.renderPending = true;
      else renderSidebar();
    }, REORDER_MOTION_MS);
}

function wireSessionDrag(item, bid, sid) {
  item.draggable = true;
  const blockTouchDrag = guardNativeTouchDrag(item);
  item.addEventListener("dragstart", (e) => {
    if (blockTouchDrag(e)) return;
    const container = item.parentElement;
    dragSess = {
      bid, sid, item, container, renderPending: false,
      originalOrder: reorderChildren(container, ".sess-item"),
    };
    container.classList.add("reordering");
    requestAnimationFrame(() => {
      if (dragSess && dragSess.item === item) item.classList.add("dragging");
    });
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", `puppy-session:${bid}:${sid}`); } catch (err) {}
  });
  item.addEventListener("dragend", () => cancelSessionDrag(item));
}

function wireSessionDropZone(surface, body, bid) {
  /* The whole backend block is a drop surface, not only the row container.
     That keeps the native cursor valid over the title and the small spaces
     exposed while siblings animate. dragenter matters when live reflow puts
     a different element beneath a stationary pointer before the next
     dragover event arrives. */
  surface.addEventListener("dragenter", (e) => {
    if (!dragSess || dragSess.bid !== bid || dragSess.container !== body) return;
    acceptReorderDrag(e);
  });
  surface.addEventListener("dragover", (e) => {
    if (!dragSess || dragSess.bid !== bid || dragSess.container !== body) return;
    acceptReorderDrag(e);
    moveDragSlot(body, dragSess.item, ".sess-item", e.clientY, false);
  });
  surface.addEventListener("drop", async (e) => {
    if (!dragSess || dragSess.bid !== bid || dragSess.container !== body) return;
    acceptReorderDrag(e);
    const context = dragSess;
    dragSess = null;
    context.item.classList.remove("dragging");
    body.classList.remove("reordering");

    const all = sessionsFor(bid);
    const previousIds = all.map(session => session.id);
    const visibleIds = reorderChildren(body, ".sess-item")
      .map(node => Number(node.dataset.sessionId));
    const visibleSet = new Set(visibleIds);
    if (visibleIds.some(id => !Number.isInteger(id)) || visibleSet.size !== visibleIds.length ||
        visibleIds.some(id => !previousIds.includes(id))) {
      renderSidebar();
      return;
    }
    let visibleIndex = 0;
    const ids = all.map(session => visibleSet.has(session.id) ?
      visibleIds[visibleIndex++] : session.id);
    if (visibleIndex !== visibleIds.length) {
      renderSidebar();
      return;
    }
    const changed = ids.some((id, index) => id !== previousIds[index]);
    if (!changed) {
      if (context.renderPending) renderSidebar();
      return;
    }

    sortSessionsByOrder(all, ids); // optimistic; the DOM is already in this order
    if (context.renderPending) renderSidebar();
    try {
      await api(bid, "sessions/reorder", { method: "POST", body: { order: ids } });
    } catch (err) {
      const current = sessionsFor(bid);
      if (current.length === previousIds.length &&
          current.every(session => previousIds.includes(session.id)))
        sortSessionsByOrder(current, previousIds);
      renderSidebar();
      toast(err.message, "error");
    }
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

/* Every engine-bearing response (poll, usage refresh, version refresh, engine
   upgrade) carries the same {engines, usage_refresh} pair. One applier keeps
   local and remote caches, the footer and Settings in step whatever asked. */
function applyEnginesPayload(bid, result) {
  if (!result || !Array.isArray(result.engines) || !result.usage_refresh)
    throw new Error("node returned an invalid engine response");
  if (bid) {
    state.engCache[bid] = result.engines;
    state.remoteUsageRefresh[bid] = result.usage_refresh;
    state.remoteOk[bid] = true;
    delete state.remoteErrors[bid];
    state.remoteEngineCheckedAt[bid] = Date.now();
    delete state.remoteEngineErrors[bid];
  } else {
    state.engines = result.engines;
    state.engMap = {};
    state.engines.forEach(engine => state.engMap[engine.key] = engine);
    state.usageRefresh = result.usage_refresh;
    state.localEngineCheckedAt = Date.now();
  }
  syncRemoteStateViews();
}

function applyUsageRefreshPayload(bid, result) {
  applyEnginesPayload(bid, result);
}

/* Installed and latest versions both refresh on their own timers on the node.
   This is the impatient path: re-probe the CLIs and re-ask the registry now. */
async function refreshEngineVersions(bid, button, nodeName) {
  if (button.disabled) return;
  button.disabled = true;
  button.classList.add("refreshing");
  button.setAttribute("aria-busy", "true");
  try {
    applyEnginesPayload(bid, await api(bid, "engines/refresh",
      { method: "POST", timeoutMs: ENGINE_REFRESH_TIMEOUT }));
  } catch (error) {
    toast(`${nodeName}: ${error.message}`, "error", 7000);
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.classList.remove("refreshing");
      button.removeAttribute("aria-busy");
    }
  }
}

async function refreshCodexUsage(bid, button, nodeName) {
  if (button.disabled) return;
  button.disabled = true;
  button.classList.add("refreshing");
  button.setAttribute("aria-busy", "true");
  try {
    const result = await api(bid, "engines/usage-refresh", {
      method: "POST", timeoutMs: 20000,
    });
    applyUsageRefreshPayload(bid, result);
    if (result.usage_refresh.last_error)
      toast(`${nodeName}: ${result.usage_refresh.last_error}`, "error", 7000);
  } catch (error) {
    toast(`${nodeName}: ${error.message}`, "error", 7000);
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.classList.remove("refreshing");
      button.removeAttribute("aria-busy");
    }
  }
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
      dot.setAttribute("role", "img");
      dot.setAttribute("aria-label", g.bid ? remoteAvailabilityTitle(g.bid) : "available");
      ico.appendChild(dot);
      head.appendChild(ico);
      const name = el("span", "foot-engine-name", g.name);
      const key = g.bid ? `remote:${g.bid}` : "local";
      const disclosure = disclosureButton(`${g.name} engine status`, body,
        collapsedStatusBackends, "puppy.collapsed.status-backends", key);
      wireDoubleClickOrTouch(name, () => disclosure.click());
      head.appendChild(name);
      if (g.bid && g.version) {
        const version = el("span", "foot-engine-version", `· v${g.version}`);
        head.appendChild(version);
      }
      head.appendChild(disclosure);
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
      row.appendChild(st);
      if (e.key === "codex" && e.installed && e.auth === "ok" &&
          backendSupportsManualUsageRefresh(g.bid)) {
        const refresh = el("button", "foot-usage-refresh");
        refresh.type = "button";
        refresh.setAttribute("aria-label", `Refresh ${g.name} Codex weekly usage`);
        refresh.appendChild(refreshIcon(10));
        refresh.onclick = event => {
          event.preventDefault();
          event.stopPropagation();
          refreshCodexUsage(g.bid, refresh, g.name);
        };
        row.appendChild(refresh);
      }
      body.appendChild(row);
    }
    group.appendChild(body);
    root.appendChild(group);
  }
}

$("toggle-archived").onclick = () => { state.showArchived = !state.showArchived; renderSidebar(); };

/* ================= tabs ================= */
let termSeq = 1;
let dragTab = null;
let tabDropMarker = null;
let tabAddTargetGroup = null;
let tabAddAnchor = null;
let renderedTabPanes = new Map();
let renderedWorkspaceRevision = -1;
let renderedWorkspaceSignature = "";
const tabScrollPositions = new Map();
let tabScrollLayoutFrame = null;
let pendingTabScrollFocus = null;
const browserActivityTurns = new Map();

function findSessionMeta(bid, sid) {
  return sessionsFor(bid).find(s => s.id === sid);
}

function targetWorkspacePane(groupId = null) {
  let pane = workspacePane(groupId) || workspacePane(state.activeGroup) || workspacePanes()[0];
  if (!pane) {
    state.layout = newPane();
    state.activeGroup = state.layout.id;
    workspaceRevision++;
    pane = state.layout;
  }
  return pane;
}

function putTabInPane(tabId, groupId = null) {
  const existing = workspacePaneForTab(tabId);
  if (existing) return existing;
  const pane = targetWorkspacePane(groupId);
  pane.tabs.push(tabId);
  pane.active = tabId;
  return pane;
}

function putTabAfter(tabId, afterTabId, groupId = null) {
  const existing = workspacePaneForTab(tabId);
  if (existing) return existing;
  const afterPane = afterTabId ? workspacePaneForTab(afterTabId) : null;
  const pane = afterPane || targetWorkspacePane(groupId);
  const index = afterTabId ? pane.tabs.indexOf(afterTabId) : -1;
  if (index >= 0) pane.tabs.splice(index + 1, 0, tabId);
  else pane.tabs.push(tabId);
  return pane;
}

function openSessionTab(bid, sid, meta, groupId = null) {
  const id = `s:${bid}:${sid}`;
  let tab = state.tabs.find(t => t.id === id);
  if (!tab) {
    tab = { id, type: "session", bid, sid, title: (meta && (meta.name || `session ${sid}`)) || `session ${sid}` };
    state.tabs.push(tab);
    putTabInPane(id, groupId);
  }
  activateTab(id);
}

function openTermTab(bid, cmd, groupId = null) {
  const id = `t:${Date.now()}:${termSeq++}`;
  state.tabs.push({ id, type: "term", bid: bid || 0, cmd: cmd || "",
    title: cmd ? cmd.slice(0, 24) : shellTabTitle({ bid: bid || 0 }) });
  putTabInPane(id, groupId);
  activateTab(id);
}

/* Browser IDs are node-scoped. Reopening one ID reuses its screen while a new
   ID creates another isolated browser tab. Agent-created tabs can be inserted
   in the background without changing the focused pane or composer. */
function openBrowserTab(bid, browserId = "", groupId = null, options = {}) {
  bid = Number(bid) || 0;
  browserId = String(browserId || "").toUpperCase();
  if (browserId && !/^[A-Z0-9]{4}$/.test(browserId)) return null;
  const id = browserId ? `b:${bid}:${browserId}` : `b:${bid}`;
  let tab = state.tabs.find(t => t.id === id);
  if (!tab) {
    tab = { id, type: "browser", bid, browserId };
    tab.title = browserTabTitle(tab);
    state.tabs.push(tab);
    if (options.afterTabId)
      putTabAfter(id, options.afterTabId, groupId);
    else
      putTabInPane(id, groupId);
  }
  /* The node binds a browser to whichever session last used it, so the tab
     follows that binding and the owning chat can point at it. */
  if (Number(options.sid) > 0) {
    tab.sid = Number(options.sid);
    tab.browserGone = false;
  }
  if (options.activate === false) {
    const focused = document.activeElement;
    const restoreFocus = focused instanceof HTMLElement &&
      !!focused.closest("#workspace-tree");
    syncTabOrderFromLayout();
    renderTabs();
    if (restoreFocus && focused.isConnected)
      focused.focus({ preventScroll: true });
  } else {
    activateTab(id);
  }
  syncSessionBrowserChips();
  return tab;
}

/* Every open chat re-reads which of its browsers are still live. Cheap: the
   list is tiny and each view rebuilds only when its own set changed. */
function syncSessionBrowserChips() {
  for (const view of Object.values(state.views))
    if (view && typeof view.syncBrowserChips === "function") view.syncBrowserChips();
}

/* The node announces each browser first touched in a turn. Local sessions can
   deliver it over both live sockets, so retain a client-side dedupe window.
   Insert it beside the originating chat but leave that chat focused. */
function handleBrowserActivity(bid, sid, turnId, browserId = "") {
  bid = Number(bid) || 0;
  sid = Number(sid) || 0;
  const token = String(turnId || "");
  browserId = String(browserId || "").toUpperCase();
  if (browserId && !/^[A-Z0-9]{4}$/.test(browserId)) return;
  if (!sid || !token) return;
  const key = `${bid}:${token}:${browserId}`;
  if (browserActivityTurns.has(key)) return;
  browserActivityTurns.set(key, Date.now());
  while (browserActivityTurns.size > 128)
    browserActivityTurns.delete(browserActivityTurns.keys().next().value);

  if (bid) state.remoteBrowser[bid] = { enabled: true };
  else state.browser = { enabled: true };
  const sessionTabId = `s:${bid}:${sid}`;
  const sessionPane = workspacePaneForTab(sessionTabId);
  openBrowserTab(bid, browserId, sessionPane ? sessionPane.id : null,
    { activate: false, afterTabId: sessionTabId, sid });
}

function openSettingsTab(groupId = null) {
  if (!state.tabs.some(t => t.id === "settings")) {
    state.tabs.push({ id: "settings", type: "settings", title: "Settings" });
    putTabInPane("settings", groupId);
  }
  activateTab("settings");
}

function closeTab(id) {
  const idx = state.tabs.findIndex(t => t.id === id);
  if (idx < 0) return;
  const closing = state.tabs[idx];
  if (closing.type === "browser" && closing.browserId) {
    api(closing.bid || 0, `browser/instances/${encodeURIComponent(closing.browserId)}`,
      { method: "DELETE" }).catch(error => {
        if (error.status !== 404)
          toast(`Browser ${closing.browserId} may still be running: ${error.message}`,
            "error", 7000);
      });
  }
  const pane = removeTabFromPane(id);
  state.tabs.splice(idx, 1);
  const v = state.views[id];
  if (v && v.destroy) v.destroy();
  delete state.views[id];
  collapseEmptyPane(pane);
  normalizeWorkspace();
  renderTabs(state.active);
  renderSidebar();
  if (closing.type === "browser") syncSessionBrowserChips();
}

function isTabVisible(id) {
  const pane = workspacePaneForTab(id);
  return !!pane && pane.active === id;
}

function focusWorkspacePane(groupId) {
  const pane = workspacePane(groupId);
  if (!pane || state.activeGroup === pane.id) return;
  state.activeGroup = pane.id;
  state.active = pane.active;
  const tab = state.tabs.find(item => item.id === state.active);
  if (tab && tab.type === "session")
    state.selectedSession = sidebarSessionKey(tab.bid, tab.sid);
  document.querySelectorAll(".workspace-pane").forEach(node =>
    node.classList.toggle("focused", node.dataset.paneId === pane.id));
  renderSidebar();
  saveTabs();
}

function activateTab(id, groupId = null) {
  const tab = state.tabs.find(t => t.id === id);
  if (!tab) return;
  const pane = workspacePaneForTab(id) || putTabInPane(id, groupId);
  pane.active = id;
  state.activeGroup = pane.id;
  state.active = id;
  if (tab.type === "session")
    state.selectedSession = sidebarSessionKey(tab.bid, tab.sid);
  syncTabOrderFromLayout();
  renderTabs(id); renderSidebar();
}

function ensureTabView(tab) {
  let view = state.views[tab.id];
  if (!view) {
    if (tab.type === "session") view = new SessionView(tab);
    else if (tab.type === "term") view = new TermView(tab);
    else if (tab.type === "browser") view = new BrowserView(tab);
    else view = new SettingsView(tab);
    state.views[tab.id] = view;
    view.root.dataset.tabId = tab.id;
  }
  return view;
}

function burgerButton() {
  const button = el("button", "icon-btn burger");
  button.type = "button";
  button.setAttribute("aria-label", "Menu");
  button.innerHTML = `<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">
    <rect x="2" y="3" width="12" height="2" rx="1"/>
    <rect x="2" y="7" width="12" height="2" rx="1"/>
    <rect x="2" y="11" width="12" height="2" rx="1"/>
  </svg>`;
  button.onclick = event => {
    event.stopPropagation();
    $("app").classList.add("side-open");
  };
  return button;
}

function makeTabDragImage(tab) {
  const ghost = tab.cloneNode(true);
  ghost.classList.remove("active", "dragging", "running");
  ghost.classList.add("tab-drag-image");
  ghost.draggable = false;
  ghost.setAttribute("aria-hidden", "true");
  const rect = tab.getBoundingClientRect();
  ghost.style.width = `${Math.ceil(rect.width)}px`;
  ghost.style.height = `${Math.ceil(rect.height)}px`;
  document.body.appendChild(ghost);
  return ghost;
}

function cleanupTabDrag(context = dragTab) {
  if (tabDropMarker) { tabDropMarker.remove(); tabDropMarker = null; }
  document.querySelectorAll(".split-preview.on").forEach(node =>
    node.classList.remove("on", "left", "right", "top", "bottom"));
  if (context) {
    if (context.item) context.item.classList.remove("dragging");
    if (context.container) context.container.classList.remove("reordering");
    if (context.dragImage) context.dragImage.remove();
    context.previewHost = null;
    context.previewSide = null;
    context.dragImage = null;
  }
}

function cancelTabDrag(item) {
  if (!dragTab || (item && dragTab.item !== item)) return;
  const context = dragTab;
  dragTab = null;
  cleanupTabDrag(context);
  renderTabs(context.pendingFocus || null);
}

function tabInsertionIndex(container, x) {
  const tabs = reorderChildren(container, ".tab");
  for (let index = 0; index < tabs.length; index++) {
    const rect = tabs[index].getBoundingClientRect();
    if (x < rect.left + rect.width / 2) return index;
  }
  return tabs.length;
}

function showTabDropMarker(container, index) {
  if (tabDropMarker) tabDropMarker.remove();
  tabDropMarker = el("span", "tab-drop-marker");
  tabDropMarker.setAttribute("aria-hidden", "true");
  const tabs = reorderChildren(container, ".tab");
  if (index < tabs.length) container.insertBefore(tabDropMarker, tabs[index]);
  else container.appendChild(tabDropMarker);
}

function renderTabNode(t, pane, tabsRoot) {
  const tab = el("div", "tab" + (pane.active === t.id ? " active" : ""));
  tab.draggable = true;
  const blockTouchDrag = guardNativeTouchDrag(tab);
  tab.dataset.tabId = t.id;
  tab.addEventListener("dragstart", (event) => {
    if (blockTouchDrag(event)) return;
    const dragImage = makeTabDragImage(tab);
    dragTab = {
      id: t.id, item: tab, container: tabsRoot, sourcePaneId: pane.id,
      pendingFocus: null, dragImage,
    };
    tabsRoot.classList.add("reordering");
    requestAnimationFrame(() => {
      if (dragTab && dragTab.item === tab) tab.classList.add("dragging");
    });
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      const rect = tab.getBoundingClientRect();
      const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
      const y = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
      try {
        event.dataTransfer.setData("text/plain", `puppy-tab:${t.id}`);
        event.dataTransfer.setDragImage(dragImage, x, y);
      } catch (error) {}
    }
  });
  tab.addEventListener("dragend", () => cancelTabDrag(tab));

  let dotCls = "settings", dotColor = "";
  if (t.type === "session") {
    const meta = findSessionMeta(t.bid, t.sid);
    dotCls = meta ? meta.engine : "claude";
    if (meta && meta.color) dotColor = meta.color;
    if (meta && meta.status === "running") tab.classList.add("running");
    if (meta) t.title = meta.name || `session ${t.sid}`;
  } else if (t.type === "term") dotCls = "term";
  else if (t.type === "browser") dotCls = "browser";
  const tdot = el("span", "t-dot " + dotCls);
  if (dotColor) tdot.style.color = dotColor;
  if (t.type === "settings") tdot.appendChild(gearIcon(12));
  else if (t.type === "term") tdot.appendChild(terminalIcon(12));
  else if (t.type === "browser") tdot.appendChild(globeIcon(12));
  tab.appendChild(tdot);
  tab.appendChild(el("span", "t-title",
    (t.type === "term" && !t.cmd) ? shellTabTitle(t) :
    (t.type === "browser") ? browserTabTitle(t) : (t.title || "tab")));
  if (t.type === "session") {
    suppressContextGestureActivation(tab);
    tab.addEventListener("contextmenu", (event) => {
      const meta = findSessionMeta(t.bid, t.sid);
      if (meta) sessionContextMenu(event, t.bid, meta);
    });
  }
  const close = el("button", "t-close");
  close.type = "button";
  close.appendChild(xIcon(12));
  close.onclick = event => { event.stopPropagation(); closeTab(t.id); };
  tab.appendChild(close);
  tab.onclick = () => activateTab(t.id, pane.id);
  return tab;
}

function syncHorizontalOverflow(scroller, viewport = scroller && scroller.parentElement) {
  if (!scroller || !viewport) return;
  const maximum = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
  const position = Math.max(0, Math.min(maximum, scroller.scrollLeft));
  const overflowed = scroller.clientWidth > 0 && maximum > 1;
  viewport.classList.toggle("more-left", overflowed && position > 1);
  viewport.classList.toggle("more-right", overflowed && position < maximum - 1);
}

function syncAllTabOverflow() {
  document.querySelectorAll(".tab-scroll > .tabs").forEach(scroller =>
    syncHorizontalOverflow(scroller));
}

function wireTabScrolling(tabsRoot, paneId) {
  tabsRoot.addEventListener("scroll", () => {
    tabScrollPositions.set(paneId, tabsRoot.scrollLeft);
    syncHorizontalOverflow(tabsRoot);
  }, { passive: true });
}

function revealTabInStrip(tabId) {
  const pane = workspacePaneForTab(tabId);
  if (!pane) return;
  const paneRoot = Array.from(document.querySelectorAll(".workspace-pane"))
    .find(node => node.dataset.paneId === pane.id);
  if (!paneRoot) return;
  const tabsRoot = paneRoot.querySelector(":scope > .tabbar > .tab-scroll > .tabs");
  if (!tabsRoot) return;
  const tab = Array.from(tabsRoot.children)
    .find(node => node.classList.contains("tab") && node.dataset.tabId === tabId);
  if (!tab) return;

  const stripRect = tabsRoot.getBoundingClientRect();
  const tabRect = tab.getBoundingClientRect();
  const maximum = Math.max(0, tabsRoot.scrollWidth - tabsRoot.clientWidth);
  const viewport = tabsRoot.parentElement;
  const fadeWidth = Math.max(0, parseFloat(
    getComputedStyle(viewport).getPropertyValue("--edge-scroll-fade-size")) || 0);
  let target = tabsRoot.scrollLeft;
  const visibleLeft = stripRect.left + (target > 1 ? fadeWidth : 0);
  if (tabRect.left < visibleLeft) target -= visibleLeft - tabRect.left;
  else {
    const visibleRight = stripRect.right - (maximum > target + 1 ? fadeWidth : 0);
    if (tabRect.right > visibleRight) target += tabRect.right - visibleRight;
  }
  target = Math.max(0, Math.min(maximum, target));
  if (Math.abs(target - tabsRoot.scrollLeft) < 1) {
    syncHorizontalOverflow(tabsRoot);
    return;
  }
  /* A newly opened view can refresh the tab nodes immediately. A smooth scroll
     still reports its old position during that refresh, which used to persist
     zero and cancel the reveal before it moved. Make the destination current
     and authoritative now; the overflow cues retain their own soft transition. */
  tabScrollPositions.set(pane.id, target);
  tabsRoot.scrollLeft = target;
  syncHorizontalOverflow(tabsRoot);
}

function finishTabScrollLayout(focusTabId = null) {
  if (focusTabId) pendingTabScrollFocus = focusTabId;
  if (tabScrollLayoutFrame !== null) return;
  tabScrollLayoutFrame = requestAnimationFrame(() => {
    tabScrollLayoutFrame = null;
    const targetTabId = pendingTabScrollFocus;
    pendingTabScrollFocus = null;
    document.querySelectorAll(".tab-scroll > .tabs").forEach(tabsRoot => {
      const saved = tabScrollPositions.get(tabsRoot.dataset.paneId);
      if (saved != null) tabsRoot.scrollLeft = saved;
    });
    if (targetTabId) revealTabInStrip(targetTabId);
    syncAllTabOverflow();
  });
}

function dropTabOnToolbar(pane, tabsRoot, event) {
  if (!dragTab) return;
  acceptReorderDrag(event);
  event.stopPropagation();
  const context = dragTab;
  const source = workspacePane(context.sourcePaneId);
  const destination = workspacePane(pane.id);
  if (!source || !destination) { cancelTabDrag(); return; }

  if (source.id === destination.id) {
    const ids = reorderChildren(tabsRoot, ".tab").map(node => node.dataset.tabId);
    const valid = ids.length === destination.tabs.length && new Set(ids).size === ids.length &&
      ids.every(id => destination.tabs.includes(id));
    if (!valid) { cancelTabDrag(); return; }
    destination.tabs = ids;
  } else {
    const index = tabInsertionIndex(tabsRoot, event.clientX);
    removeTabFromPane(context.id);
    collapseEmptyPane(source);
    const target = workspacePane(pane.id);
    if (!target) { cancelTabDrag(); return; }
    target.tabs.splice(Math.max(0, Math.min(index, target.tabs.length)), 0, context.id);
    target.active = context.id;
  }
  state.activeGroup = destination.id;
  const target = workspacePane(destination.id);
  state.active = target ? target.active : null;
  dragTab = null;
  cleanupTabDrag(context);
  normalizeWorkspace();
  renderTabs(state.active);
  renderSidebar();
}

function wirePaneTabbar(tabbar, tabsRoot, pane) {
  tabbar.addEventListener("dragenter", event => {
    if (!dragTab) return;
    acceptReorderDrag(event);
  });
  tabbar.addEventListener("dragover", event => {
    if (!dragTab) return;
    acceptReorderDrag(event);
    event.stopPropagation();
    cleanupSplitPreview();
    const source = workspacePane(dragTab.sourcePaneId);
    if (source && source.id === pane.id) {
      if (tabDropMarker) { tabDropMarker.remove(); tabDropMarker = null; }
      moveDragSlot(tabsRoot, dragTab.item, ".tab", event.clientX, true);
    } else {
      const index = tabInsertionIndex(tabsRoot, event.clientX);
      showTabDropMarker(tabsRoot, index);
    }
  });
  tabbar.addEventListener("drop", event => dropTabOnToolbar(pane, tabsRoot, event));
}

function splitSideForPoint(host, x, y) {
  const rect = host.getBoundingClientRect();
  const nx = Math.max(0, Math.min(1, (x - rect.left) / Math.max(1, rect.width)));
  const ny = Math.max(0, Math.min(1, (y - rect.top) / Math.max(1, rect.height)));
  const horizontalEdge = Math.min(nx, 1 - nx);
  const verticalEdge = Math.min(ny, 1 - ny);
  if (horizontalEdge < verticalEdge) return nx < .5 ? "left" : "right";
  return ny < .5 ? "top" : "bottom";
}

function cleanupSplitPreview() {
  if (!dragTab || !dragTab.previewHost) return;
  const preview = dragTab.previewHost.querySelector(":scope > .split-preview");
  if (preview) preview.classList.remove("on", "left", "right", "top", "bottom");
  dragTab.previewHost = null;
  dragTab.previewSide = null;
}

function showSplitPreview(host, side) {
  if (!dragTab) return;
  if (dragTab.previewHost !== host || dragTab.previewSide !== side) cleanupSplitPreview();
  const preview = host.querySelector(":scope > .split-preview");
  if (!preview) return;
  preview.classList.remove("left", "right", "top", "bottom");
  preview.classList.add("on", side);
  dragTab.previewHost = host;
  dragTab.previewSide = side;
}

function splitTabIntoPane(tabId, targetPaneId, side) {
  let target = workspacePane(targetPaneId);
  const source = workspacePaneForTab(tabId);
  if (!target || !source || (target === source && source.tabs.length < 2)) return false;
  removeTabFromPane(tabId);
  if (source !== target) {
    collapseEmptyPane(source);
    target = workspacePane(targetPaneId);
    if (!target) return false;
  }
  const pane = newPane([tabId], tabId);
  const axis = side === "left" || side === "right" ? "row" : "column";
  const before = side === "left" || side === "top";
  const split = newSplit(axis, before ? pane : target, before ? target : pane);
  if (state.layout.id === target.id) state.layout = split;
  else if (!workspaceReplaceNode(target.id, split)) return false;
  state.activeGroup = pane.id;
  state.active = tabId;
  normalizeWorkspace();
  return true;
}

function wirePaneDrop(host, pane) {
  host.addEventListener("dragover", event => {
    if (!dragTab) return;
    const source = workspacePane(dragTab.sourcePaneId);
    if (!source || (source.id === pane.id && source.tabs.length < 2)) {
      cleanupSplitPreview();
      return;
    }
    acceptReorderDrag(event);
    event.stopPropagation();
    if (tabDropMarker) { tabDropMarker.remove(); tabDropMarker = null; }
    showSplitPreview(host, splitSideForPoint(host, event.clientX, event.clientY));
  }, true);
  host.addEventListener("drop", event => {
    if (!dragTab || dragTab.previewHost !== host) return;
    acceptReorderDrag(event);
    event.stopPropagation();
    const context = dragTab;
    const side = context.previewSide;
    dragTab = null;
    cleanupTabDrag(context);
    if (splitTabIntoPane(context.id, pane.id, side)) {
      renderTabs(context.id);
      renderSidebar();
    } else renderTabs();
  }, true);
}

function applySplitRatio(split, first, second, divider) {
  first.style.flex = `${split.ratio} 1 0px`;
  second.style.flex = `${1 - split.ratio} 1 0px`;
  divider.setAttribute("aria-valuenow", String(Math.round(split.ratio * 100)));
}

function wireSplitter(split, root, first, second, divider) {
  const setRatio = value => {
    split.ratio = Math.max(.1, Math.min(.9, value));
    applySplitRatio(split, first, second, divider);
  };
  divider.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    divider.classList.add("active");
    divider.setPointerCapture(event.pointerId);
    const move = moveEvent => {
      const rect = root.getBoundingClientRect();
      const dividerSize = split.axis === "row" ? divider.offsetWidth : divider.offsetHeight;
      const total = Math.max(1, (split.axis === "row" ? rect.width : rect.height) - dividerSize);
      const point = split.axis === "row" ? moveEvent.clientX - rect.left : moveEvent.clientY - rect.top;
      const minimum = Math.min(.45, 80 / total);
      setRatio(Math.max(minimum, Math.min(1 - minimum, (point - dividerSize / 2) / total)));
    };
    const finish = () => {
      divider.classList.remove("active");
      divider.removeEventListener("pointermove", move);
      divider.removeEventListener("pointerup", finish);
      divider.removeEventListener("pointercancel", finish);
      saveTabs();
      window.dispatchEvent(new Event("resize"));
    };
    divider.addEventListener("pointermove", move);
    divider.addEventListener("pointerup", finish);
    divider.addEventListener("pointercancel", finish);
  });
  divider.addEventListener("dblclick", () => {
    setRatio(.5); saveTabs(); window.dispatchEvent(new Event("resize"));
  });
  divider.addEventListener("keydown", event => {
    const decrease = split.axis === "row" ? event.key === "ArrowLeft" : event.key === "ArrowUp";
    const increase = split.axis === "row" ? event.key === "ArrowRight" : event.key === "ArrowDown";
    if (!decrease && !increase) return;
    event.preventDefault();
    setRatio(split.ratio + (increase ? 1 : -1) * (event.shiftKey ? .1 : .02));
    saveTabs(); window.dispatchEvent(new Event("resize"));
  });
  applySplitRatio(split, first, second, divider);
}

function showTabAddMenu(groupId, button, event) {
  event.stopPropagation();
  const menu = $("tab-add-menu");
  const closing = !menu.classList.contains("hidden") && tabAddAnchor === button;
  menu.classList.add("hidden");
  tabAddAnchor = null;
  if (closing) return;
  tabAddTargetGroup = groupId;
  tabAddAnchor = button;
  menu.classList.remove("hidden");
  positionAnchoredMenu(menu, button);
}

function renderWorkspacePane(pane) {
  const root = el("section", "workspace-node workspace-pane" +
    (pane.id === state.activeGroup ? " focused" : ""));
  root.dataset.paneId = pane.id;
  root.addEventListener("pointerdown", event => {
    if (event.button === 0 || event.button === 2) focusWorkspacePane(pane.id);
  });
  const tabbar = el("div", "tabbar");
  tabbar.appendChild(burgerButton());
  const tabScroll = el("div", "tab-scroll edge-scroll-viewport");
  const tabsRoot = el("div", "tabs");
  tabsRoot.dataset.paneId = pane.id;
  for (const id of pane.tabs) {
    const tab = state.tabs.find(item => item.id === id);
    if (tab) tabsRoot.appendChild(renderTabNode(tab, pane, tabsRoot));
  }
  tabScroll.appendChild(tabsRoot);
  tabbar.appendChild(tabScroll);
  const addWrap = el("div", "tab-add-wrap");
  const add = el("button", "icon-btn", "＋");
  add.type = "button";
  add.setAttribute("aria-label", "New tab");
  add.onclick = event => showTabAddMenu(pane.id, add, event);
  addWrap.appendChild(add);
  tabbar.appendChild(addWrap);
  wireTabScrolling(tabsRoot, pane.id);
  wirePaneTabbar(tabbar, tabsRoot, pane);
  root.appendChild(tabbar);

  const host = el("div", "views pane-views");
  const preview = el("div", "split-preview");
  preview.setAttribute("aria-hidden", "true");
  host.appendChild(preview);
  wirePaneDrop(host, pane);
  for (const id of pane.tabs) {
    const tab = state.tabs.find(item => item.id === id);
    if (!tab) continue;
    const view = pane.active === id ? ensureTabView(tab) : state.views[id];
    if (!view) continue;
    view.root.classList.toggle("on", pane.active === id);
    host.appendChild(view.root);
  }
  root.appendChild(host);
  return root;
}

function renderWorkspaceNode(node) {
  if (node.kind === "pane") return renderWorkspacePane(node);
  const root = el("div", `workspace-node workspace-split ${node.axis}`);
  root.dataset.splitId = node.id;
  const first = renderWorkspaceNode(node.first);
  const divider = el("div", "splitter");
  divider.tabIndex = 0;
  divider.setAttribute("role", "separator");
  divider.setAttribute("aria-label", "Resize panes");
  divider.setAttribute("aria-orientation", node.axis === "row" ? "vertical" : "horizontal");
  divider.setAttribute("aria-valuemin", "10");
  divider.setAttribute("aria-valuemax", "90");
  const second = renderWorkspaceNode(node.second);
  root.appendChild(first); root.appendChild(divider); root.appendChild(second);
  wireSplitter(node, root, first, second, divider);
  return root;
}

function workspaceSignature(node) {
  if (node.kind === "pane")
    return JSON.stringify(["pane", node.id, node.active, node.tabs]);
  return JSON.stringify(["split", node.id, node.axis,
    workspaceSignature(node.first), workspaceSignature(node.second)]);
}

function refreshRenderedTabbars() {
  for (const pane of workspacePanes()) {
    const root = Array.from(document.querySelectorAll(".workspace-pane"))
      .find(node => node.dataset.paneId === pane.id);
    if (!root) return false;
    const tabsRoot = root.querySelector(":scope > .tabbar > .tab-scroll > .tabs");
    if (!tabsRoot) return false;
    tabScrollPositions.set(pane.id, tabsRoot.scrollLeft);
    const nodes = [];
    for (const id of pane.tabs) {
      const tab = state.tabs.find(item => item.id === id);
      if (tab) nodes.push(renderTabNode(tab, pane, tabsRoot));
    }
    tabsRoot.innerHTML = "";
    for (const node of nodes) tabsRoot.appendChild(node);
    tabsRoot.scrollLeft = tabScrollPositions.get(pane.id) || 0;
  }
  return true;
}

function renderTabs(focusTabId = null) {
  if (dragTab && dragTab.item && dragTab.item.isConnected) {
    if (focusTabId) dragTab.pendingFocus = focusTabId;
    return;
  }
  if (!state.layout) normalizeWorkspace();
  const signature = workspaceSignature(state.layout);
  if (renderedWorkspaceRevision === workspaceRevision &&
      renderedWorkspaceSignature === signature && refreshRenderedTabbars()) {
    if (focusTabId && isTabVisible(focusTabId)) {
      const view = state.views[focusTabId];
      if (view && view.onShow) view.onShow(true);
    }
    finishTabScrollLayout(focusTabId);
    saveTabs();
    return;
  }
  const tree = $("workspace-tree");
  const hint = $("empty-hint");
  hint.remove();
  for (const view of Object.values(state.views))
    if (view.root && view.root.parentNode) view.root.remove();
  tree.innerHTML = "";
  tree.appendChild(renderWorkspaceNode(state.layout));

  const panes = workspacePanes();
  if (!state.tabs.length) {
    const host = tree.querySelector(".pane-views");
    hint.classList.remove("hidden");
    if (host) host.appendChild(hint);
  } else {
    hint.classList.add("hidden");
    $("workspace").appendChild(hint);
  }

  const current = new Map();
  const show = new Map();
  for (const pane of panes) {
    if (!pane.active) continue;
    current.set(pane.active, pane.id);
    if (renderedTabPanes.get(pane.active) !== pane.id)
      show.set(pane.active, pane.id === state.activeGroup);
  }
  if (focusTabId && current.has(focusTabId)) show.set(focusTabId, true);
  renderedTabPanes = current;
  renderedWorkspaceRevision = workspaceRevision;
  renderedWorkspaceSignature = signature;
  const requests = [...show.entries()].sort((a, b) => Number(a[1]) - Number(b[1]));
  for (const [id, focus] of requests) {
    const view = state.views[id];
    if (view && view.onShow) view.onShow(focus);
  }
  finishTabScrollLayout(focusTabId || state.active);
  requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
  saveTabs();
}

document.addEventListener("dragover", event => {
  if (dragTab && !(event.target instanceof Element && event.target.closest(".pane-views")))
    cleanupSplitPreview();
}, true);

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
document.addEventListener("click", () => {
  $("tab-add-menu").classList.add("hidden");
  tabAddAnchor = null;
  closeMenusToggling(null);
  closeChoiceMenu();
});
$("tab-add-menu").addEventListener("click", (e) => {
  const button = e.target.closest && e.target.closest("button[data-act]");
  const act = button && button.dataset.act;
  if (!act) return;
  $("tab-add-menu").classList.add("hidden");
  const groupId = tabAddTargetGroup;
  tabAddAnchor = null;
  if (act === "new-session") modalNewSession(groupId);
  else if (act === "open-session") modalOpenSession(groupId);
  else if (act === "new-terminal") modalNewTerminal(groupId);
  else if (act === "new-browser") openBrowserFromMenu(groupId);
});
$("btn-new-session").onclick = () => { modalNewSession(state.activeGroup); closeDrawer(); };
$("btn-settings").onclick = () => { openSettingsTab(state.activeGroup); closeDrawer(); };

/* drawer (mobile) */
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
function currentTheme() {
  return document.documentElement.classList.contains("light") ? "light" : "dark";
}
function applyTheme(t) {
  document.documentElement.classList.toggle("light", t === "light");
  $("btn-theme").textContent = t === "light" ? "☾" : "☀";
  lsSet("puppy.theme", t);
  /* The theme is this browser's own state, so each node has to be told: its
     managed browsers render pages with the matching prefers-color-scheme. */
  for (const view of Object.values(state.views))
    if (view && typeof view.sendColorScheme === "function") view.sendColorScheme();
}
$("btn-theme").onclick = () =>
  applyTheme(document.documentElement.classList.contains("light") ? "dark" : "light");
applyTheme(lsGet("puppy.theme") || "dark");

/* completion-alert bell: appears once a completion command is configured;
   click arms or silences it (the state lives on the server, so it holds
   with every browser closed) */
function syncBell() {
  const bell = $("btn-bell");
  if (!bell) return;
  const n = state.notify || {};
  bell.classList.toggle("hidden", !n.configured);
  bell.classList.toggle("on", !!n.enabled);
  bell.textContent = "";
  bell.appendChild(bellIcon(14, !n.enabled));
  bell.setAttribute("aria-label", n.enabled ?
    "Completion alerts armed; activate to silence" :
    "Completion alerts off; activate to arm");
  bell.setAttribute("aria-pressed", n.enabled ? "true" : "false");

  const toggle = $("nf-enabled");
  const toggleRoot = $("nf-enabled-wrap");
  if (toggle && toggleRoot) {
    const saving = toggle.dataset.saving === "true";
    if (!saving) toggle.checked = !!n.enabled;
    toggle.disabled = saving;
    toggleRoot.classList.toggle("disabled", saving);
    toggleRoot.removeAttribute("title");
    toggleRoot.removeAttribute("data-tip");
    const label = saving ? "Updating completion alerts" : !n.configured && n.enabled ?
      "Completion alerts enabled, but no command is configured, so nothing will run" :
      `Completion alerts ${n.enabled ? "enabled" : "disabled"}`;
    toggle.setAttribute("aria-label", saving ? label :
      `${label}; activate to ${n.enabled ? "disable" : "enable"}`);
  }
}
$("btn-bell").onclick = async () => {
  const want = !(state.notify && state.notify.enabled);
  try {
    const r = await api(0, "notify/toggle", { method: "POST", body: { enabled: want } });
    state.notify = { configured: !!(r.settings.command || "").trim(),
                     enabled: !!r.settings.enabled };
    syncBell();
  } catch (e) { toast(e.message, "error"); }
};

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

/* A keystroke the browser handled itself brings the caret back into view; an
   edit made from script gets no such courtesy, so a composer that has grown to
   its max height keeps showing the window it had before the change until the
   next real character arrives. Measure where the caret sits on a throwaway
   mirror of the textarea - the same trick the autosize ghost uses - and scroll
   only when the caret is actually outside the visible band. */
const CARET_MIRROR_STYLES = [
  "fontFamily", "fontSize", "fontWeight", "fontStyle", "lineHeight", "letterSpacing",
  "textIndent", "textTransform", "wordSpacing", "tabSize",
  "paddingTop", "paddingBottom", "paddingLeft", "paddingRight",
];
function scrollCaretIntoView(ta) {
  if (!ta || ta.scrollHeight <= ta.clientHeight + 1) return;   // nothing to scroll
  const cs = getComputedStyle(ta);
  const mirror = document.createElement("div");
  for (const prop of CARET_MIRROR_STYLES) mirror.style[prop] = cs[prop];
  /* clientWidth excludes the scrollbar the overflow just introduced, so the
     mirror wraps exactly where the textarea does. */
  mirror.style.cssText += ";position:absolute;top:0;left:-9999px;visibility:hidden;" +
    "pointer-events:none;white-space:pre-wrap;overflow-wrap:break-word;box-sizing:border-box;" +
    "width:" + ta.clientWidth + "px";
  mirror.textContent = ta.value.slice(0, ta.selectionEnd);
  const caret = document.createElement("span");
  caret.textContent = "\u200b";
  mirror.appendChild(caret);
  document.body.appendChild(mirror);
  const top = caret.offsetTop;
  const bottom = top + (caret.offsetHeight || parseFloat(cs.lineHeight) || 16);
  mirror.remove();
  if (bottom > ta.scrollTop + ta.clientHeight) ta.scrollTop = bottom - ta.clientHeight;
  else if (top < ta.scrollTop) ta.scrollTop = top;
}

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
  ta.dispatchEvent(new Event("input", { bubbles: true }));   // resizes first
  scrollCaretIntoView(ta);
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

const ATTACHMENT_PREVIEW_TYPES = new Set([
  "image/png", "image/jpeg", "image/webp", "image/gif",
]);

/* Attachments reach the engine as marker lines appended to the message text, so
   sent messages carry them in their transcript text. The composer parses its own
   markers back out when recalling history: the chips come back as chips instead
   of raw bracket lines, and a recalled marker is re-sent verbatim. */
const ATTACH_IMAGE_PREFIX = "[image attached: ";
const ATTACH_IMAGE_SUFFIX = " — view it with your image/file tools]";
const ATTACH_FILE_PREFIX = "[file attached: ";
const ATTACH_FILE_SUFFIX = " — inspect it with your file tools]";
const SENT_THUMBNAIL_LIMIT = 12;   // recall keeps this many sent image previews

function baseName(path) {
  const value = String(path || "");
  return value.split("/").pop() || value;
}

function attachmentMarkerLine(a) {
  if (a.line) return a.line;   // recalled markers round-trip byte for byte
  return a.preview
    ? `${ATTACH_IMAGE_PREFIX}${a.path}${ATTACH_IMAGE_SUFFIX}`
    : `${ATTACH_FILE_PREFIX}${a.path} (${a.name}, ${fmtBytes(a.size)})${ATTACH_FILE_SUFFIX}`;
}

/* One marker line -> a composer attachment that owns nothing: its file is already
   referenced by the sent message, so dismissing the chip must never discard it. */
function parseAttachmentMarker(line) {
  const recalled = (path, extra) => path ? Object.assign({
    path, name: baseName(path), line, uploadId: "", url: "",
    ownsUrl: false, size: 0, sizeText: "", uploading: false, controller: null,
    removed: false,
  }, extra) : null;
  if (line.startsWith(ATTACH_IMAGE_PREFIX) && line.endsWith(ATTACH_IMAGE_SUFFIX))
    return recalled(line.slice(ATTACH_IMAGE_PREFIX.length,
                               line.length - ATTACH_IMAGE_SUFFIX.length),
                    { preview: true, sizeText: "image" });
  if (!(line.startsWith(ATTACH_FILE_PREFIX) && line.endsWith(ATTACH_FILE_SUFFIX))) return null;
  // "<path> (<name>, <size>)", where <name> is the path's own basename: scanning
  // the candidate splits keeps names holding spaces, commas or brackets intact.
  const inner = line.slice(ATTACH_FILE_PREFIX.length, line.length - ATTACH_FILE_SUFFIX.length);
  for (let at = inner.indexOf(" ("); at >= 0; at = inner.indexOf(" (", at + 1)) {
    const path = inner.slice(0, at);
    const tail = inner.slice(at + 2);
    const lead = baseName(path) + ", ";
    if (!path || !tail.startsWith(lead) || !tail.endsWith(")")) continue;
    return recalled(path, { preview: false, sizeText: tail.slice(lead.length, -1) });
  }
  return null;
}

/* Markers are the message's trailing block; anything above them is the prose the
   composer should show. A message that was only attachments recalls as chips. */
function splitAttachmentMarkers(text) {
  const lines = String(text || "").split("\n");
  const attachments = [];
  for (;;) {
    const parsed = lines.length ? parseAttachmentMarker(lines[lines.length - 1]) : null;
    if (!parsed) break;
    attachments.unshift(parsed);
    lines.pop();
  }
  return {
    text: attachments.length ? lines.join("\n").replace(/\s+$/, "") : String(text || ""),
    attachments,
  };
}

function dataTransferHasFiles(transfer) {
  if (!transfer) return false;
  const types = transfer.types ? [...transfer.types] : [];
  if (types.includes("Files")) return true;
  return transfer.items ? [...transfer.items].some(item => item.kind === "file") : false;
}

function filesFromDataTransfer(transfer) {
  const result = { files: [], directories: 0 };
  if (!transfer) return result;
  const items = transfer.items ? [...transfer.items] : [];
  if (items.length) {
    for (const item of items) {
      if (item.kind !== "file") continue;
      let entry = null;
      try {
        if (typeof item.webkitGetAsEntry === "function") entry = item.webkitGetAsEntry();
      } catch (_) { /* fall back to getAsFile below */ }
      if (entry && entry.isDirectory) {
        result.directories++;
        continue;
      }
      const file = item.getAsFile();
      if (file) result.files.push(file);
    }
    return result;
  }
  result.files = transfer.files ? [...transfer.files] : [];
  return result;
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
    this.switchLines = [];    // engine-switch dividers, re-labelled as state arrives
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
    this.attachments = [];    // staged server files, retained only when their message is sent
    this.histAttach = null;   // staged attachments parked while history recall is active
    this.sentThumbs = new Map();  // path -> object URL, so recall can re-show previews
    this.uploadPolicy = uploadSettingsFor(this.tab.bid);
    this.fileDragDepth = 0;
    this.nativeComposerChoices = prefersNativeChoices();
    this.browserChipKey = null;   // set of linked-browser bubbles now rendered
    this.buildDom();
    this.syncBrowserChips();      // a restored browser tab has a bubble at once
    this._onResize = () => { this.syncGutter(); this.syncComposerMeta(); this.syncHeadOverflow(); this.syncQueueFade(); };
    window.addEventListener("resize", this._onResize);
    this.connect();
  }

  buildDom() {
    const root = el("div", "view chat");
    const composerChoice = (cls, key, label) => this.nativeComposerChoices ?
      `<span class="mini composer-native-choice ${cls}">
        <span class="mini-key">${esc(key)}:</span><span class="mini-value">auto</span>
        <select aria-label="${esc(label)}" disabled></select>
      </span>` :
      `<button type="button" class="mini ${cls}" aria-label="${esc(label)}">
        <span class="mini-key">${esc(key)}:</span><span class="mini-value">auto</span>
      </button>`;
    root.innerHTML = `
      <div class="chat-head">
        <div class="chat-meta-viewport edge-scroll-viewport">
          <div class="chat-meta-scroll">
            <span class="chip eng"><span class="dot"></span><span class="eng-label">…</span></span>
            <span class="chip be"></span>
            <span class="chip cwd"></span>
            <span class="chat-status"></span>
          </div>
        </div>
        <div class="chat-menu">
          <button class="icon-btn menu-btn" aria-label="Session menu">⋮</button>
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
            <div class="composer-meta-viewport edge-scroll-viewport">
              <div class="composer-meta-scroll">
                <button type="button" class="mini attach-add" aria-label="Attach files">
                  <span aria-hidden="true"></span></button>
                ${composerChoice("perm", "permissions", "Permission mode")}
                ${composerChoice("model", "model", "Model")}
                ${composerChoice("effort", "effort", "Reasoning effort")}
              </div>
            </div>
            <button class="btn-send">Send</button>
          </div>
          <input class="hidden attach-input" type="file" multiple>
        </div>
      </div>`;
    this.root = root;
    this.scroll = root.querySelector(".chat-scroll");
    this.inner = root.querySelector(".chat-inner");
    this.ta = root.querySelector("textarea");
    this.composerBox = root.querySelector(".composer-box");
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
    this.attachButton = root.querySelector(".attach-add");
    this.attachButton.firstElementChild.appendChild(plusIcon(12));
    this.fileInput = root.querySelector(".attach-input");
    this.headMeta.addEventListener("scroll", () => this.syncHeadOverflow(), { passive: true });
    this.composerMeta.addEventListener("scroll", () => this.syncComposerOverflow(), { passive: true });
    this.ta.addEventListener("paste", (e) => this.handlePaste(e));
    this.composerBox.addEventListener("dragenter", (e) => this.handleFileDragEnter(e));
    this.composerBox.addEventListener("dragover", (e) => this.handleFileDragOver(e));
    this.composerBox.addEventListener("dragleave", (e) => this.handleFileDragLeave(e));
    this.composerBox.addEventListener("drop", (e) => this.handleFileDrop(e));
    this.attachButton.onclick = () => {
      this.fileInput.value = "";
      this.fileInput.click();
    };
    this.fileInput.onchange = () => {
      const files = this.fileInput.files ? [...this.fileInput.files] : [];
      this.fileInput.value = "";
      if (files.length) this.uploadFiles(files);
    };
    this.syncUploadButton();

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

    // the draft is stored in sendable form, so staged attachments survive a reload
    const draft = lsGet("puppy.draft." + this.tab.id);
    if (draft) {
      const restored = splitAttachmentMarkers(draft);
      this.ta.value = restored.text;
      this.attachments = restored.attachments;
      this.renderAttachments();
      this.resizeComposer();
    }
    this.ta.addEventListener("input", () => {
      this.ctrlCStreak = 0;
      this.histIdx = null;   // manual edits exit history mode
      this.releaseHistoryAttachments();
      this.resizeComposer();
      this.saveDraft();
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
        if (this.histIdx === null) {
          this.histIdx = this.history.length;
          this.histDraft = this.ta.value;
          this.histAttach = this.attachments;   // parked, not discarded
        }
        if (this.histIdx > 0) {
          e.preventDefault();
          this.histIdx--;
          this.recallComposer(this.history[this.histIdx]);
        }
      } else if (e.key === "ArrowDown" && atEnd && this.histIdx !== null) {
        e.preventDefault();
        this.histIdx++;
        if (this.histIdx >= this.history.length) {
          this.attachments = this.histAttach || [];
          this.histAttach = null;
          this.renderAttachments();
          this.setComposer(this.histDraft);
          this.histIdx = null;
        } else {
          this.recallComposer(this.history[this.histIdx]);
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
    this.clearFileDropTarget();
    this.connectionSequence++;
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    window.removeEventListener("resize", this._onResize);
    if (this.ws) try { this.ws.close(); } catch (e) {}
    this.releaseHistoryAttachments();
    for (const attachment of [...this.attachments]) this.removeAttachment(attachment, true);
    for (const url of this.sentThumbs.values()) URL.revokeObjectURL(url);
    this.sentThumbs.clear();
    this.root.remove();
  }

  onShow(focus = true) {
    this.syncGutter(); this.syncComposerMeta(); this.syncHeadOverflow();
    this.syncUploadButton();
    this.scrollBottom(true);
    if (focus) this.ta.focus();
  }

  syncRemoteState() {
    this.syncUploadButton();
  }

  syncUploadButton() {
    if (!this.attachButton) return;
    const supported = backendSupportsFileUploads(this.tab.bid);
    const unavailable = !!this.tab.bid && state.remoteOk[this.tab.bid] === false;
    const policy = this.uploadPolicy || uploadSettingsFor(this.tab.bid);
    const disabledByPolicy = !!policy && !policy.enabled;
    this.attachButton.disabled = !supported || unavailable || disabledByPolicy;
    let label;
    if (!supported) label = "Upgrade this backend to attach arbitrary files";
    else if (unavailable) label = "Backend unavailable";
    else if (disabledByPolicy) label = "File uploads are disabled on this backend";
    else if (policy) label = `Attach files · ${policy.max_file_size_mb} MiB maximum each`;
    else label = "Attach files";
    this.attachButton.removeAttribute("title");
    this.attachButton.removeAttribute("data-tip");
    this.attachButton.setAttribute("aria-label", label);
  }

  /* transcript sits left of the scrollbar; export its width so the composer /
     approval / queue columns can align with the transcript column exactly */
  syncGutter() {
    const g = this.scroll.offsetWidth - this.scroll.clientWidth;
    this.root.style.setProperty("--sbw", (g > 0 ? g : 0) + "px");
  }

  syncHeadOverflow() {
    syncHorizontalOverflow(this.headMeta, this.headMetaViewport);
  }

  syncComposerOverflow() {
    syncHorizontalOverflow(this.composerMeta, this.composerMetaViewport);
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
    scrollCaretIntoView(this.ta);   // recalling a long entry lands on its end
    this.saveDraft();
  }

  /* Keep the stored draft in the form the message would be sent in: reopening the
     tab parses its markers back into chips instead of showing bracket lines. */
  saveDraft() {
    const markers = this.attachments.filter(a => a.path).map(attachmentMarkerLine).join("\n");
    const text = this.ta.value;
    lsSet("puppy.draft." + this.tab.id,
          markers ? (text ? text + "\n\n" + markers : markers) : text);
  }

  /* Recall a sent message: its attachment markers become chips again, and the
     preview from the original send is reused when this tab still holds it. */
  recallComposer(text) {
    const recalled = splitAttachmentMarkers(text);
    for (const attachment of recalled.attachments) {
      if (!attachment.preview) continue;
      const url = this.sentThumbs.get(attachment.path);
      if (url) attachment.url = url;   // owned by sentThumbs, never revoked here
    }
    this.attachments = recalled.attachments;   // the parked list keeps its own array
    this.renderAttachments();
    this.setComposer(recalled.text);
  }

  /* History mode ended without walking back to the draft, so the attachments it
     parked are unreachable: discard the uploads they still own. */
  releaseHistoryAttachments() {
    if (!this.histAttach) return;
    const parked = this.histAttach;
    this.histAttach = null;
    for (const attachment of parked)
      if (!this.attachments.includes(attachment)) this.removeAttachment(attachment, true);
  }

  /* A sent image's object URL outlives its chip so recall can show the preview;
     everything else the composer created is released immediately. */
  retireSentAttachment(attachment) {
    if (!attachment.url || !attachment.ownsUrl) return;
    if (!attachment.preview || !attachment.path) {
      URL.revokeObjectURL(attachment.url);
      return;
    }
    const previous = this.sentThumbs.get(attachment.path);
    if (previous && previous !== attachment.url) URL.revokeObjectURL(previous);
    this.sentThumbs.delete(attachment.path);
    this.sentThumbs.set(attachment.path, attachment.url);
    while (this.sentThumbs.size > SENT_THUMBNAIL_LIMIT) {
      const oldest = this.sentThumbs.keys().next().value;
      URL.revokeObjectURL(this.sentThumbs.get(oldest));
      this.sentThumbs.delete(oldest);
    }
  }

  /* ---- incoming ---- */
  handle(d) {
    switch (d.type) {
      case "snapshot":
        this.session = d.session;
        if (d.uploads) {
          const policy = rememberUploadSettings(this.tab.bid, d.uploads);
          if (policy) this.uploadPolicy = policy;
        }
        this.syncUploadButton();
        this.status = d.status;
        noteSessionActivity(this.tab.bid, this.tab.sid, d.status === "running",
          d.active_since, d.server_time);
        this.retry = 800;
        this.inner.innerHTML = "";
        this.toolCards = {};
        this.switchLines = [];
        this.oldestSeq = d.events.length ? d.events[0].seq : null;
        if (d.events.length >= 200) this.addLoadOlder();
        d.events.forEach(ev => this.renderEvent(ev, false));
        this.history = d.events.filter(ev => ev.kind === "user")
          .map(ev => (ev.data && ev.data.text) || "").filter(Boolean);
        this.histIdx = null;
        this.renderQueue(d.queued || []);   // before updateHead: pickers read the queue
        this.updateHead();
        this.updateRunState();
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
        this.updateHead();   // the pickers speak for whatever is now last in line
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
      case "browser_activity":
        handleBrowserActivity(this.tab.bid, this.tab.sid, d.turn_id, d.browser_id);
        break;
    }
    const runningKinds = { user: 1, assistant: 1, thinking: 1, tool_use: 1, tool_result: 1 };
    if (d.type === "delta" || (d.type === "event" && runningKinds[d.event.kind])) {
      const becameRunning = this.status !== "running";
      this.status = "running"; this.updateRunState();
      if (becameRunning) noteSessionActivity(this.tab.bid, this.tab.sid, true);
    }
  }

  /* A browser this session launched lives in its own tab, which can be
     scrolled out of the tabbar or parked in another pane entirely. The header
     says one exists and takes you to it, and stops saying so the moment the
     browser closes. */
  syncBrowserChips() {
    const live = state.tabs.filter(t => t.type === "browser" && t.browserId &&
      (t.bid || 0) === (this.tab.bid || 0) && t.sid === this.tab.sid &&
      t.browserGone !== true);
    const key = live.map(t => t.id).join("\u0000");
    if (key === this.browserChipKey) return;
    this.browserChipKey = key;
    const scroll = this.root.querySelector(".chat-meta-scroll");
    for (const stale of scroll.querySelectorAll(".chip.browser")) stale.remove();
    /* Right behind the engine chip: the meta strip scrolls horizontally, and a
       live browser the user has not noticed is worth more of that first screen
       than the static backend and path chips behind it. */
    const anchor = scroll.querySelector(".chip.be") ||
      this.root.querySelector(".chat-status");
    for (const t of live) {
      const chip = el("button", "chip browser");
      chip.type = "button";
      chip.appendChild(globeIcon(11));
      chip.appendChild(el("span", "chip-text", `Browser ${t.browserId}`));
      chip.setAttribute("aria-label", `Show Browser ${t.browserId}`);
      chip.onclick = () => activateTab(t.id);
      scroll.insertBefore(chip, anchor);
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
    cwd.removeAttribute("title");
    cwd.removeAttribute("data-tip");
    cwd.setAttribute("aria-label", workspaceTitle(s));
    cwd.classList.toggle("warn", !!s.workspace_missing);
    this.root.querySelector(".chip.be").textContent = backendName(this.tab.bid);
    this.syncBrowserChips();
    const setMini = (cls, label, value, pending = false) => {
      const control = this.root.querySelector(".mini." + cls);
      control.querySelector(".mini-value").textContent = value;
      control.classList.toggle("pending", pending);
      const title = label + ": " + value + (pending ? " · applies after the queue" : "");
      control.removeAttribute("title");
      control.removeAttribute("data-tip");
      const select = control.querySelector("select");
      (select || control).setAttribute("aria-label", title);
    };
    const eff = this.effectiveConfig();
    setMini("perm", "Permission mode", s.permission_mode || "auto");
    setMini("model", "Model", eff.model || "auto", eff.queuedModel);
    setMini("effort", "Reasoning effort", eff.effort || "auto", eff.queuedEffort);
    this.syncSwitchLines();   // the newest divider tracks the live selection
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

  /* Dividers mark where the configuration changed, and name it on both sides.
     A model/effort divider carries both sides itself: the engine writes it as a
     turn starts, so what it names is what actually ran. An engine switch cannot
     - it resets the incoming engine to its defaults, and the model is picked
     afterwards - so that side is resolved from what came later: the "from" side
     of the next divider, or, for the newest one (the segment still running),
     the configuration the session's last turn used. Never the live picker: a
     model chosen but not yet sent anything has not run. Redone whenever either
     input changes, which is cheap at a handful of dividers. */
  syncSwitchLines() {
    const s = this.session || {};
    const lines = this.switchLines
      .filter(n => n.isConnected || !n.parentNode)
      .sort((a, b) => a._switch.seq - b._switch.seq);
    this.switchLines = lines;
    lines.forEach((node, i) => {
      const { data: d, engines } = node._switch;
      if (!engines) return this.fillSwitchLine(node, d, d.to_model, d.to_effort);
      const next = lines[i + 1] && lines[i + 1]._switch.data;
      const used = (!next && s.engine === d.to && s.used_config) || {};
      this.fillSwitchLine(node, d, next ? next.from_model : used.model,
        next ? next.from_effort : used.effort);
    });
  }

  switchLineNode(ev, d, engines) {
    const n = el("div", "switch-line" + (engines ? "" : " cfg-line"));
    n._switch = { seq: ev.seq, data: d, engines };
    this.switchLines.push(n);
    this.syncSwitchLines();
    return n;
  }

  /* "moved codex 5.6-Sol Max → claude Fable Max" for an engine switch; the
     engine is dropped from a model/effort change, which keeps the same one. */
  fillSwitchLine(node, d, toModel, toEffort) {
    const engines = node._switch.engines;
    const side = (engine, model, effort) => {
      if (!engines) return [el("span", "sw-cfg",
        engineConfigDetail(this.tab.bid, engine, model, effort, "default"))];
      const [name, detail] = engineConfigParts(this.tab.bid, engine, model, effort);
      const parts = [el("span", "sw-eng", name)];
      if (detail) parts.push(" ", el("span", "sw-cfg", detail));
      return parts;
    };
    const engine = engines ? "" : (d.engine || (this.session || {}).engine);
    const body = el("span", "sw-body");
    if (engines) body.append("moved ");
    body.append(...side(engines ? d.from : engine, d.from_model, d.from_effort),
      " ", el("span", "sw-arrow", "→"), " ",
      ...side(engines ? d.to : engine, toModel, toEffort));
    node.textContent = "";
    node.appendChild(body);
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
        if (d.subtype === "config_change") return this.switchLineNode(ev, d, false);
        const warned = d.subtype === "interrupted" || d.subtype === "model_switch" ||
          d.subtype === "workspace_reset";
        const n = el("div", "info-line" + (warned ? " warn" : ""));
        n.textContent = d.text || d.subtype || "";
        return n;
      }
      case "engine_switch": return this.switchLineNode(ev, d, true);
      case "error": {
        return el("div", "err-card", d.text || "error");
      }
      /* one shape for every engine: outcome · how long · tokens out · when.
         Cost is deliberately absent - engines price differently (and some not
         at all), so the line would stop meaning the same thing everywhere. */
      case "result": {
        const n = el("div", "result-line");
        const bits = [];
        if (!d.ok) bits.push(`<span class="bad">✗ ${esc((d.error || "failed").slice(0, 80))}</span>`);
        else bits.push("✔");
        if (d.duration_ms) bits.push((d.duration_ms / 1000).toFixed(1) + "s");
        const u = d.usage || {};
        if (u.output_tokens != null) bits.push(fmtTokens(u.output_tokens) + " out");
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
  showFileDropTarget() {
    if (!this.composerBox) return;
    this.syncUploadButton();
    const unavailable = this.attachButton && this.attachButton.disabled;
    this.composerBox.dataset.dropHint = unavailable ?
      (this.attachButton.getAttribute("aria-label") || "Files cannot be attached") :
      "Drop files to attach";
    this.composerBox.classList.add("file-drag");
    this.composerBox.classList.toggle("drop-rejected", !!unavailable);
  }

  clearFileDropTarget() {
    this.fileDragDepth = 0;
    if (!this.composerBox) return;
    this.composerBox.classList.remove("file-drag", "drop-rejected");
    delete this.composerBox.dataset.dropHint;
  }

  handleFileDragEnter(event) {
    if (!dataTransferHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    event.stopPropagation();
    this.fileDragDepth++;
    this.showFileDropTarget();
  }

  handleFileDragOver(event) {
    if (!dataTransferHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    event.stopPropagation();
    this.showFileDropTarget();
    try {
      event.dataTransfer.dropEffect = this.attachButton.disabled ? "none" : "copy";
    } catch (_) { /* some browsers expose a read-only dropEffect */ }
  }

  handleFileDragLeave(event) {
    if (!this.composerBox.classList.contains("file-drag")) return;
    event.preventDefault();
    event.stopPropagation();
    this.fileDragDepth = Math.max(0, this.fileDragDepth - 1);
    if (this.fileDragDepth === 0) this.clearFileDropTarget();
  }

  handleFileDrop(event) {
    if (!dataTransferHasFiles(event.dataTransfer)) return;
    event.preventDefault();
    event.stopPropagation();
    const dropped = filesFromDataTransfer(event.dataTransfer);
    this.clearFileDropTarget();
    if (dropped.directories)
      toast("folders cannot be attached · drop individual files instead", "error", 6000);
    if (dropped.files.length) this.uploadFiles(dropped.files);
  }

  handlePaste(e) {
    const items = e.clipboardData ? [...e.clipboardData.items] : [];
    const files = items.filter(item => item.kind === "file")
      .map(item => item.getAsFile()).filter(Boolean);
    if (!files.length) return;
    e.preventDefault();
    this.uploadFiles(files, true);
  }

  uploadFiles(files, fromClipboard = false) {
    const arbitraryFiles = backendSupportsFileUploads(this.tab.bid);
    const policy = this.uploadPolicy || uploadSettingsFor(this.tab.bid);
    if (policy && !policy.enabled) {
      toast("file uploads are disabled on this backend", "error");
      return;
    }
    for (const file of files) {
      const legacyImage = !arbitraryFiles && fromClipboard &&
        ATTACHMENT_PREVIEW_TYPES.has(String(file.type || "").toLowerCase());
      if (!arbitraryFiles && !legacyImage) {
        toast("upgrade this backend to attach arbitrary files", "error");
        continue;
      }
      if (policy && file.size > policy.max_file_size_bytes) {
        toast(`${file.name || "file"} is ${fmtBytes(file.size)} · maximum is ` +
          `${policy.max_file_size_mb} MiB`, "error", 6500);
        continue;
      }
      this.uploadFile(file, legacyImage);
    }
  }

  async uploadFile(file, legacyImage = false) {
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const preview = ATTACHMENT_PREVIEW_TYPES.has(String(file.type || "").toLowerCase());
    const attachment = {
      name: file.name || "file", size: file.size, contentType: file.type || "application/octet-stream",
      path: "", uploadId: "", url: preview ? URL.createObjectURL(file) : "",
      ownsUrl: preview, sizeText: "", line: "",
      preview, uploading: true, controller, removed: false,
    };
    this.attachments.push(attachment);
    this.renderAttachments();
    try {
      const response = await fetch(apiPath(this.tab.bid, `sessions/${this.tab.sid}/upload`), {
        method: "POST",
        headers: {
          "Content-Type": attachment.contentType,
          "X-Puppy-Filename": encodeURIComponent(attachment.name),
          "X-Puppy-Size": String(file.size),
        },
        body: file,
        redirect: "error",
        ...(controller ? { signal: controller.signal } : {}),
      });
      const result = await response.json().catch(() => null);
      const policy = rememberUploadSettings(this.tab.bid, result && result.uploads);
      if (policy) this.uploadPolicy = policy;
      if (!response.ok) throw new Error((result && result.error) || `HTTP ${response.status}`);
      if (!result || typeof result.path !== "string" ||
          (!legacyImage && typeof result.upload_id !== "string"))
        throw new Error("backend returned an invalid upload response");
      if (attachment.removed) {
        if (result.upload_id) this.discardServerUpload(result.upload_id, true);
        return;
      }
      attachment.path = result.path;
      attachment.uploadId = result.upload_id || "";
      attachment.name = result.name || attachment.name;
      const reportedSize = result.size == null ? file.size : Number(result.size);
      if (!Number.isFinite(reportedSize) || reportedSize < 0)
        throw new Error("backend returned an invalid upload size");
      attachment.size = reportedSize;
      attachment.contentType = result.content_type || attachment.contentType;
      attachment.uploading = false;
      attachment.controller = null;
      this.renderAttachments();
    } catch (error) {
      if (!attachment.removed) {
        const wasAborted = !!(controller && controller.signal.aborted);
        this.removeAttachment(attachment, true);
        if (!wasAborted)
          toast(`file upload failed: ${error.message}`, "error", 6500);
      }
    }
  }

  discardServerUpload(uploadId, quiet = false) {
    if (!uploadId) return;
    api(this.tab.bid, `sessions/${this.tab.sid}/upload/${uploadId}`, {
      method: "DELETE", keepalive: true, timeoutMs: 10000,
    }).catch(error => {
      if (!quiet) toast(`could not discard upload: ${error.message}`, "error");
    });
  }

  removeAttachment(attachment, quiet = false) {
    if (!attachment || attachment.removed) return;
    attachment.removed = true;
    if (attachment.controller) attachment.controller.abort();
    if (attachment.url) {
      // a recalled chip borrows its preview from sentThumbs; only revoke our own
      if (attachment.ownsUrl) URL.revokeObjectURL(attachment.url);
      attachment.url = "";
    }
    this.attachments = this.attachments.filter(item => item !== attachment);
    if (attachment.uploadId) this.discardServerUpload(attachment.uploadId, quiet);
    this.renderAttachments();
  }

  renderAttachments() {
    this.attachStrip.innerHTML = "";
    this.attachStrip.classList.toggle("hidden", !this.attachments.length);
    for (const a of this.attachments) {
      // a recalled image without its original preview falls back to a named chip
      const thumbnail = a.preview && !!a.url;
      const chip = el("span", "attach-chip " + (thumbnail ? "image" : "file") +
        (a.uploading ? " uploading" : ""));
      if (thumbnail) {
        const img = el("img", "attach-thumb");
        img.src = a.url;
        img.alt = a.name;
        chip.appendChild(img);
        if (a.uploading) chip.appendChild(el("span", "attach-state", "uploading"));
      } else {
        const icon = el("span", "attach-file-icon");
        icon.appendChild(attachmentFileIcon());
        const copy = el("span", "attach-file-copy");
        copy.appendChild(el("span", "attach-file-name", a.name));
        copy.appendChild(el("span", "attach-file-size",
          `${a.uploading ? "uploading · " : ""}${a.sizeText || fmtBytes(a.size)}`));
        chip.appendChild(icon);
        chip.appendChild(copy);
      }
      const x = el("button", "attach-x");
      x.type = "button";
      x.appendChild(xIcon(12));
      x.setAttribute("aria-label", "Remove attachment");
      x.onclick = () => this.removeAttachment(a);
      chip.appendChild(x);
      this.attachStrip.appendChild(chip);
    }
    if (this.attachButton)
      this.attachButton.classList.toggle("uploading",
        this.attachments.some(attachment => attachment.uploading));
    if (!this.closed) this.saveDraft();   // teardown must not rewrite the draft
  }

  submit() {
    let text = this.ta.value.trim();
    if (!text && !this.attachments.length) return;
    if (this.attachments.some(attachment => attachment.uploading)) {
      toast("wait for file uploads to finish", "error");
      return;
    }
    if (!this.ws || this.ws.readyState !== 1) { toast("not connected", "error"); return; }
    if (this.attachments.length) {
      const lines = this.attachments.map(attachmentMarkerLine).join("\n");
      text = text ? text + "\n\n" + lines : lines;
      this.attachments.forEach(a => this.retireSentAttachment(a));
      this.attachments = [];
      this.renderAttachments();
    }
    this.ws.send(JSON.stringify({ type: "message", text }));
    this.ta.value = "";
    this.resizeComposer();
    this.histIdx = null; this.histDraft = "";
    this.releaseHistoryAttachments();
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

  /* a queue entry is a prompt string, or a pending model/effort change */
  describeQueuedConfig(item) {
    const eng = engineInfo(this.tab.bid, (this.session || {}).engine);
    const parts = [];
    if ("model" in item) parts.push("model → " + (modelShorthand(eng, item.model) || "default"));
    if ("effort" in item) parts.push("effort → " + (effortShorthand(eng, item.effort) || "default"));
    return parts.join(" · ") || "setting change";
  }

  renderQueue(q) {
    const box = this.queueEl;
    this.queued = q;
    box.innerHTML = "";
    if (!q.length) { box.classList.add("hidden"); this.queueOpen = false; return; }
    box.classList.remove("hidden");
    box.classList.toggle("expanded", !!this.queueOpen);
    box.appendChild(el("div", "q-head", `queued · ${q.length}`));
    /* one row per item, in the order they will run. The list is capped so a
       deep queue cannot push the composer down the screen; the tail is one
       click away. The per-item cap only keeps a runaway message out of the DOM
       - each row is a single line that fades out at whatever width is going. */
    const shown = this.queueOpen ? q : q.slice(0, QUEUE_ROWS);
    shown.forEach((item, i) => {
      const cfg = !!(item && typeof item === "object");
      const row = el("div", "q-item" + (cfg ? " q-cfg" : ""));
      row.appendChild(el("span", "q-n", String(i + 1)));
      const text = cfg ? this.describeQueuedConfig(item)
        : (item.length > 200 ? item.slice(0, 199) + "…" : item);
      const t = el("span", "q-t", text);
      t.setAttribute("aria-label", cfg ? text : item);
      row.appendChild(t);
      const x = el("button", "q-x");
      x.type = "button";
      x.appendChild(xIcon(12));   // even size in an even box: no half-pixel centring
      x.setAttribute("aria-label",
        cfg ? "Cancel this queued change" : "Cancel this queued message");
      x.onclick = () => this.unqueue(i, cfg ? item.key || "" : item);   // full text, not the capped copy
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

  /* what the NEXT prompt will run under: the session's model/effort, then any
     pending changes waiting in the queue, in order. The pickers and mini pills
     speak about the next prompt, so they read this rather than the session. */
  effectiveConfig() {
    const s = this.session || {};
    const out = { model: s.model || "", effort: s.effort || "",
                  queuedModel: false, queuedEffort: false };
    for (const item of this.queued || []) {
      if (!item || typeof item !== "object") continue;
      if ("model" in item) { out.model = item.model || ""; out.queuedModel = true; }
      if ("effort" in item) { out.effort = item.effort || ""; out.queuedEffort = true; }
    }
    return out;
  }

  composerChoiceSpec(kind, native = false) {
    const s = this.session || {};
    const eng = state.engMap[s.engine];
    const eff = this.effectiveConfig();
    if (kind === "perm") return {
      options: [...((eng && eng.permission_options) || [])],
      selected: s.permission_mode || "",
    };
    if (kind === "effort") return {
      options: [...((eng && eng.effort_options) || [])],
      selected: eff.effort || "",
    };
    const options = [...((eng && eng.model_options) || [])];
    const current = eff.model || "";
    const custom = !!current && !options.some(option => option.value === current);
    if (native && custom) {
      options.push({ value: "__current_custom__", label: `Current: ${current}` });
      options.push({ value: "__custom__", label: "Custom…" });
      return { options, selected: "__current_custom__" };
    }
    options.push({
      value: "__custom__", label: custom ? `Custom… (${current})` : "Custom…"
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
        if (item.hint) option.title = item.hint;
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
    menu.setAttribute("aria-label",
      anchor.getAttribute("aria-label") || tips.text(anchor) || "Choices");
    menu.style.visibility = "hidden";
    anchor.setAttribute("aria-haspopup", "listbox");
    anchor.setAttribute("aria-expanded", "true");
    const rows = [];
    const dismiss = (returnFocus = false) => {
      menu.remove();
      anchor.setAttribute("aria-expanded", "false");
      if (returnFocus && anchor.isConnected) anchor.focus();
    };
    const highlight = (index) => rows.forEach((row, i) => row.classList.toggle("active", i === index));
    /* where the highlight belongs with no pointer on the menu: the row the keys
       are on, else the current value - the state the menu opened in */
    const resting = () => {
      const focused = rows.indexOf(document.activeElement);
      if (focused >= 0) return focused;
      const selectedAt = rows.findIndex(row => row.classList.contains("selected"));
      return selectedAt >= 0 ? selectedAt : 0;
    };
    opts.forEach((o, index) => {
      const selected = current === o.value;
      const row = choiceOptionNode(o.label, selected);
      if (o.hint) row.title = o.hint;
      row.tabIndex = selected ? 0 : -1;
      row.onmouseenter = () => highlight(index);
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
    /* the pointer takes its highlight with it when it goes */
    menu.onmouseleave = () => highlight(resting());
    menu.onclick = (event) => event.stopPropagation();
    document.body.appendChild(menu);
    const openAt = resting();
    highlight(openAt);
    requestAnimationFrame(() => {
      if (!menu.isConnected) return;
      positionChoiceMenu({ menu, button: anchor });
      (rows[openAt] || menu).focus({ preventScroll: true });
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
    this.host = this.root.querySelector(".term-host");
    this.started = false;
  }
  onShow(focus = true) {
    if (!this.started) { this.started = true; this.start(); }
    else if (this.fit) setTimeout(() => this.fit.fit(), 30);
    if (focus && this.term && !this.isDead()) this.term.focus();
  }
  isDead() { return !!this.root.querySelector(".term-dead"); }
  start() {
    this.term = new Terminal({
      cursorBlink: true, fontSize: 13, scrollback: 8000,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      theme: { background: "#000000", foreground: "#e8e8ec", cursor: "#f0a84a" },
    });
    this.fit = new FitAddon.FitAddon();
    this.term.loadAddon(this.fit);
    this.term.open(this.host);
    /* Copy on select, the way a terminal emulator does. The write runs inside
       the mouseup that ended the gesture: browsers that want a user gesture
       accept it, and so does the execCommand fallback that plain-HTTP
       deployments need. The mouseup is listened for on the document because a
       drag often ends outside the terminal, but only for the duration of a
       gesture that began inside it - a click anywhere else must not re-copy a
       selection still sitting here. Registering it from our own mousedown also
       puts us behind the listener xterm registers from its (inner, therefore
       earlier) one, so the selection is final by the time we read it. */
    this.onSelectUp = () => this.copySelection();
    this.onSelectDown = event => {
      if (event.button !== 0) return; // a right-click must not replace what it is about to paste
      document.addEventListener("mouseup", this.onSelectUp, { once: true });
    };
    this.host.addEventListener("mousedown", this.onSelectDown);
    /* Clipboard reads have no selection-based HTTP fallback like writes do.
       On a secure origin, plain right-click pastes through xterm so bracketed
       paste mode is preserved. Shift+right-click always keeps the browser's
       menu, as does plain right-click wherever clipboard reads are unavailable. */
    this.onContextMenu = event => {
      if (event.shiftKey || !window.isSecureContext || !navigator.clipboard ||
          typeof navigator.clipboard.readText !== "function" ||
          !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      event.preventDefault();
      this.pasteClipboard();
    };
    this.host.addEventListener("contextmenu", this.onContextMenu);
    setTimeout(() => {
      this.fit.fit();
      if (this.tab.ended === true) this.showDead();
      else this.connect();
    }, 40);
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
      if (state.active === this.tab.id && isTabVisible(this.tab.id)) this.term.focus();
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
  copySelection() {
    if (!this.term || !this.term.hasSelection()) return;   // a plain click keeps the clipboard
    const text = this.term.getSelection();
    if (!text.trim()) return;                              // a stray drag over blank rows too
    writeClipboardText(text).catch(() => {
      if (this.copyWarned) return;                         // once per terminal, not per drag
      this.copyWarned = true;
      toast("could not copy the selection to the clipboard", "error");
    });
  }
  async pasteClipboard() {
    try {
      const text = await navigator.clipboard.readText();
      if (this.closed || !this.term || !text ||
          !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      this.term.paste(text);
      this.term.focus();
    } catch (e) {
      if (this.pasteWarned) return;
      this.pasteWarned = true;
      toast("clipboard paste was blocked · use Shift+right-click for the browser menu", "error", 7000);
    }
  }
  showDead() {
    if (this.tab.ended !== true) { this.tab.ended = true; saveTabs(); }
    if (this.isDead()) { this.syncRemoteState(); return; }
    /* DECTCEM rather than a CSS override: the cursor is the terminal's to draw,
       and a hidden one stays hidden under a translucent overlay whichever
       renderer xterm chose. reset() on a new shell brings it back. */
    this.term.write("\x1b[?25l");
    this.term.blur();
    const d = el("div", "term-dead");
    d.appendChild(el("div", "term-dead-message", "Terminal ended"));
    const actions = el("div", "term-dead-actions");
    const fresh = el("button", "btn btn-pri term-dead-new", "New shell");
    fresh.type = "button";
    fresh.onclick = () => {
      this.tab.ended = false;
      saveTabs();
      d.remove();
      this.term.reset();
      this.term.write("\x1b[?25h");
      this.connect();
      this.term.focus();
    };
    const close = el("button", "btn term-dead-close", "Close shell");
    close.type = "button";
    close.onclick = () => closeTab(this.tab.id);
    actions.appendChild(fresh);
    actions.appendChild(close);
    d.appendChild(actions);
    /* Anchored to the terminal box, not the padded view, so the stack is
       centred on the black rectangle the user is actually looking at. */
    this.host.appendChild(d);
    this.syncRemoteState();
  }
  syncRemoteState() {
    const dead = this.root.querySelector(".term-dead");
    if (!dead) return;
    const message = dead.querySelector(".term-dead-message");
    const button = dead.querySelector(".term-dead-new");
    const unavailable = !!this.tab.bid && state.remoteOk[this.tab.bid] === false;
    message.textContent = unavailable ? "Backend unavailable" : "Terminal ended";
    button.textContent = unavailable ? "Waiting for backend…" : "New shell";
    button.disabled = unavailable;
  }
  destroy() {
    this.closed = true;
    this.connectionSequence++;
    if (this.resizeObs) this.resizeObs.disconnect();
    if (this.onSelectDown) this.host.removeEventListener("mousedown", this.onSelectDown);
    if (this.onSelectUp) document.removeEventListener("mouseup", this.onSelectUp);
    if (this.onContextMenu) this.host.removeEventListener("contextmenu", this.onContextMenu);
    if (this.ws) try { this.ws.close(); } catch (e) {}
    if (this.dataSub) { this.dataSub.dispose(); this.dataSub = null; }
    if (this.term) this.term.dispose();
    this.root.remove();
  }
}

/* ================= BrowserView ================= */
/* Screen + input for one identified managed headless browser. Frames arrive
   as raw JPEG websocket messages; input goes back as normalized coordinates
   so the mapping survives any display scaling on this side. */
class BrowserView {
  constructor(tab) {
    this.tab = tab;
    this.closed = false;
    this.started = false;
    this.connectionSequence = 0;
    this.frameW = 1280;
    this.frameH = 800;
    this.frameUrl = null;
    this.urlFocused = false;
    this.lastUrl = "";
    this.moveQueued = null;
    this.root = el("div", "view browser");
    this.root.innerHTML = `
      <div class="br-bar">
        <button class="icon-btn br-back" type="button" aria-label="Back" disabled>
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor"
            stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M10.5 3 5.5 8l5 5"/></svg>
        </button>
        <button class="icon-btn br-reload" type="button" aria-label="Reload"></button>
        <input class="br-url" type="text" inputmode="url" autocomplete="off"
          autocapitalize="off" spellcheck="false" placeholder="Address or search"
          aria-label="Address or search">
        <button class="icon-btn br-kbd" type="button" aria-label="Type into the page"
          aria-pressed="false" aria-expanded="false">
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor"
            stroke-width="1.15" stroke-linecap="round" aria-hidden="true">
            <rect x="1.5" y="4" width="13" height="8.5" rx="1.5"/>
            <path d="M4 6.75h.01M7 6.75h.01M10 6.75h.01M12.2 6.75h.01M4 9h.01M7 9h.01
              M10 9h.01M12.2 9h.01M5 11h6"/></svg>
        </button>
      </div>
      <div class="br-type hidden">
        <input class="br-ime" type="text" autocomplete="off" autocapitalize="none"
          autocorrect="off" spellcheck="false" enterkeyhint="enter"
          placeholder="Type here - keys go to the page"
          aria-label="Send typing and keys to the page">
        <button class="btn btn-sm br-type-bksp" type="button" aria-label="Backspace">
          <svg viewBox="0 0 22 16" width="17" height="13" fill="none" stroke="currentColor"
            stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M20.2 2.4H7.6L1.6 8l6 5.6h12.6z"/>
            <path d="M10.9 5.9l5.2 4.2M16.1 5.9l-5.2 4.2"/></svg>
        </button>
        <button class="btn btn-sm br-type-enter" type="button">Enter</button>
      </div>
      <div class="br-stage" tabindex="0" role="application" aria-label="Remote browser screen">
        <img class="br-screen" alt="" draggable="false">
      </div>`;
    this.bar = this.root.querySelector(".br-bar");
    this.backBtn = this.root.querySelector(".br-back");
    this.reloadBtn = this.root.querySelector(".br-reload");
    this.reloadBtn.appendChild(refreshIcon(13));
    this.urlInput = this.root.querySelector(".br-url");
    this.kbdBtn = this.root.querySelector(".br-kbd");
    this.typeRow = this.root.querySelector(".br-type");
    this.stage = this.root.querySelector(".br-stage");
    this.screen = this.root.querySelector(".br-screen");
    this.ime = this.root.querySelector(".br-ime");
  }

  onShow(focus = true) {
    if (!this.started) { this.started = true; this.start(); }
    if (!focus || this.isDead()) return;
    if (this.typeRow.classList.contains("hidden")) this.stage.focus({ preventScroll: true });
    else this.ime.focus({ preventScroll: true });
  }
  isDead() { return !!this.root.querySelector(".br-dead"); }

  send(payload) {
    if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify(payload));
  }
  static modifiers(e) {
    return (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
  }
  /* Keys a soft keyboard cannot express as inserted text, so the relay row
     forwards them as real key events instead. */
  static get RELAY_KEYS() {
    return { Enter: 1, Backspace: 1, Delete: 1, Tab: 1, ArrowUp: 1, ArrowDown: 1,
             ArrowLeft: 1, ArrowRight: 1, Home: 1, End: 1, PageUp: 1, PageDown: 1 };
  }
  sendKey(key, modifiers = 0) {
    this.send({ type: "key", kind: "down", key, code: key, text: "", modifiers });
    this.send({ type: "key", kind: "up", key, code: key, text: "", modifiers });
  }
  /* Sent on every connect as well as on a toggle: the node stores the last
     value it heard, so a browser the agent opens with nobody watching still
     starts in the mode this WebUI is using. */
  sendColorScheme() {
    this.send({ type: "color_scheme", value: currentTheme() });
  }
  showTyping(on) {
    this.typeRow.classList.toggle("hidden", !on);
    this.kbdBtn.classList.toggle("on", !!on);
    this.kbdBtn.setAttribute("aria-pressed", on ? "true" : "false");
    this.kbdBtn.setAttribute("aria-expanded", on ? "true" : "false");
    if (on) {
      this.ime.value = "";
      this.ime.focus();     // synchronous inside the click: raises the keyboard
    } else if (!this.isDead()) {
      this.stage.focus({ preventScroll: true });
    }
  }
  point(clientX, clientY) {
    const rect = this.screen.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      nx: Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)),
      ny: Math.min(1, Math.max(0, (clientY - rect.top) / rect.height)),
    };
  }

  start() {
    const buttons = ["left", "middle", "right"];
    const mouse = (kind, e) => {
      const p = this.point(e.clientX, e.clientY);
      if (!p) return;
      this.send({ type: "mouse", kind, ...p, button: buttons[e.button] || "left",
        clickCount: Math.max(1, Math.min(3, e.detail || 1)),
        modifiers: BrowserView.modifiers(e) });
    };
    this.stage.addEventListener("mousedown", e => {
      if (this.isDead()) return;
      e.preventDefault();
      this.stage.focus({ preventScroll: true });
      mouse("down", e);
    });
    this.stage.addEventListener("mouseup", e => { if (!this.isDead()) mouse("up", e); });
    /* rAF-collapsed pointer moves: only the newest position matters */
    this.stage.addEventListener("mousemove", e => {
      if (this.isDead()) return;
      const p = this.point(e.clientX, e.clientY);
      if (!p) return;
      const idle = this.moveQueued === null;
      this.moveQueued = { type: "mouse", kind: "move", ...p, button: "none",
        clickCount: 0, modifiers: BrowserView.modifiers(e) };
      if (idle) requestAnimationFrame(() => {
        const queued = this.moveQueued;
        this.moveQueued = null;
        if (queued && !this.closed) this.send(queued);
      });
    });
    this.stage.addEventListener("contextmenu", e => e.preventDefault());
    this.stage.addEventListener("wheel", e => {
      if (this.isDead()) return;
      e.preventDefault();
      const p = this.point(e.clientX, e.clientY);
      if (!p) return;
      const scale = e.deltaMode === 1 ? 16 : 1;
      this.send({ type: "wheel", ...p, dx: e.deltaX * scale, dy: e.deltaY * scale,
        modifiers: BrowserView.modifiers(e) });
    }, { passive: false });

    /* touch: drag scrolls, a short still tap clicks */
    let touch = null;
    this.stage.addEventListener("touchstart", e => {
      if (this.isDead() || e.touches.length !== 1) { touch = null; return; }
      const t = e.touches[0];
      touch = { x: t.clientX, y: t.clientY, sx: t.clientX, sy: t.clientY,
        at: Date.now(), moved: false };
      e.preventDefault();
    }, { passive: false });
    this.stage.addEventListener("touchmove", e => {
      if (!touch || e.touches.length !== 1) return;
      e.preventDefault();
      const t = e.touches[0];
      const dx = t.clientX - touch.x, dy = t.clientY - touch.y;
      touch.x = t.clientX; touch.y = t.clientY;
      if (Math.abs(t.clientX - touch.sx) + Math.abs(t.clientY - touch.sy) > 9)
        touch.moved = true;
      const p = this.point(t.clientX, t.clientY);
      if (p && touch.moved) this.send({ type: "wheel", ...p, dx: -dx, dy: -dy, modifiers: 0 });
    }, { passive: false });
    this.stage.addEventListener("touchend", e => {
      const gesture = touch;
      touch = null;
      if (!gesture || gesture.moved || Date.now() - gesture.at > 600) return;
      e.preventDefault();
      const p = this.point(gesture.x, gesture.y);
      if (!p) return;
      this.send({ type: "mouse", kind: "down", ...p, button: "left", clickCount: 1, modifiers: 0 });
      this.send({ type: "mouse", kind: "up", ...p, button: "left", clickCount: 1, modifiers: 0 });
    }, { passive: false });

    const key = (kind, e) => {
      if (this.isDead()) return;
      /* local clipboard pastes through the dedicated paste event instead */
      if (kind === "down" && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "v") return;
      e.preventDefault();
      e.stopPropagation();
      this.send({ type: "key", kind, key: e.key, code: e.code,
        text: e.key.length === 1 ? e.key : "",
        modifiers: BrowserView.modifiers(e), repeat: e.repeat === true });
    };
    this.stage.addEventListener("keydown", e => key("down", e));
    this.stage.addEventListener("keyup", e => key("up", e));
    this.stage.addEventListener("paste", e => {
      const text = e.clipboardData && e.clipboardData.getData("text");
      if (!text) return;
      e.preventDefault();
      this.send({ type: "insert_text", text });
    });

    /* Typing relay. The stage takes keystrokes directly once focused, but it
       cannot summon a soft keyboard: it is a user-select:none, unfocusable
       surface, and on iOS an input inside one never opens the keyboard at all.
       So the relay is its own visible row outside the stage - focusing a real,
       on-screen field is what actually raises the keyboard, and on desktop the
       button now has something to show for itself. */
    this.kbdBtn.onclick = () => this.showTyping(this.typeRow.classList.contains("hidden"));
    this.typeRow.querySelector(".br-type-bksp").onclick = () => {
      this.sendKey("Backspace");
      this.ime.focus();
    };
    this.typeRow.querySelector(".br-type-enter").onclick = () => {
      this.sendKey("Enter");
      this.ime.focus();
    };
    this.ime.addEventListener("input", () => {
      const value = this.ime.value;
      this.ime.value = "";
      if (value) this.send({ type: "insert_text", text: value });
    });
    this.ime.addEventListener("keydown", e => {
      e.stopPropagation();   // never let the app's global shortcuts see this
      if (e.key === "Escape") { e.preventDefault(); this.showTyping(false); return; }
      if (!BrowserView.RELAY_KEYS[e.key]) return;
      e.preventDefault();
      this.sendKey(e.key, BrowserView.modifiers(e));
    });

    this.urlInput.addEventListener("focus", () => {
      this.urlFocused = true;
      this.urlInput.select();
    });
    this.urlInput.addEventListener("blur", () => {
      this.urlFocused = false;
      this.urlInput.value = this.lastUrl === "about:blank" ? "" : this.lastUrl;
    });
    this.urlInput.addEventListener("keydown", e => {
      e.stopPropagation();
      if (e.key === "Enter") {
        const target = this.urlInput.value.trim();
        if (target) this.send({ type: "navigate", url: target });
        this.urlInput.blur();
        this.stage.focus({ preventScroll: true });
      } else if (e.key === "Escape") {
        this.urlInput.blur();
      }
    });
    this.backBtn.onclick = () => this.send({ type: "back" });
    this.reloadBtn.onclick = () => this.send({ type: "reload" });
    this.connect();
  }

  connect() {
    const sequence = ++this.connectionSequence;
    const path = this.tab.browserId ?
      `ws/browser/${encodeURIComponent(this.tab.browserId)}` : "ws/browser";
    const ws = new WebSocket(wsUrl(this.tab.bid, path));
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      if (this.closed || sequence !== this.connectionSequence || this.ws !== ws) {
        try { ws.close(); } catch (error) {}
        return;
      }
      noteRemoteSocketReachable(this.tab.bid);
      this.sendColorScheme();
    };
    ws.onmessage = ev => {
      if (sequence !== this.connectionSequence || this.ws !== ws) return;
      noteRemoteSocketReachable(this.tab.bid);
      if (typeof ev.data !== "string") { this.showFrame(ev.data); return; }
      let d = null;
      try { d = JSON.parse(ev.data); } catch (error) { return; }
      if (d.type === "status") {
        this.lastUrl = d.url || "";
        if (!this.urlFocused)
          this.urlInput.value = this.lastUrl === "about:blank" ? "" : this.lastUrl;
        this.urlInput.title = d.title || "";
        this.backBtn.disabled = !d.can_back;
        this.clearDead();
      } else if (d.type === "frame_meta") {
        this.frameW = Number(d.width) || this.frameW;
        this.frameH = Number(d.height) || this.frameH;
      } else if (d.type === "dialog") {
        toast(`page ${d.kind || "dialog"} ${d.action}: ${d.message || ""}`.trim(),
          "info", 6000);
      } else if (d.type === "error") {
        this.showDead(d.text || "Browser unavailable", true);
      } else if (d.type === "gone") {
        this.showDead(d.reason || "Browser ended", true);
      }
    };
    ws.onclose = () => {
      if (sequence !== this.connectionSequence || this.ws !== ws) return;
      this.ws = null;
      if (!this.closed) this.showDead("Connection closed");
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }

  showFrame(buffer) {
    const previous = this.frameUrl;
    this.frameUrl = URL.createObjectURL(new Blob([buffer], { type: "image/jpeg" }));
    this.screen.src = this.frameUrl;
    this.screen.classList.add("live");
    if (previous) URL.revokeObjectURL(previous);
    this.clearDead();
  }

  clearDead() {
    const dead = this.root.querySelector(".br-dead");
    if (dead) dead.remove();
    this.markGone(false);
  }

  /* Only the node's own verdict retires the owning chat's bubble: a dropped
     socket means we cannot see the browser, not that it stopped. */
  markGone(gone) {
    if (this.tab.browserGone === gone) return;
    this.tab.browserGone = gone;
    syncSessionBrowserChips();
  }

  showDead(message, ended = false) {
    if (ended) this.markGone(true);
    let dead = this.root.querySelector(".br-dead");
    if (dead) {
      dead.querySelector(".term-dead-message").textContent = message;
      return;
    }
    dead = el("div", "term-dead br-dead");
    dead.appendChild(el("div", "term-dead-message", message));
    const actions = el("div", "term-dead-actions");
    const again = el("button", "btn btn-pri", "Reconnect");
    again.type = "button";
    again.onclick = () => {
      this.clearDead();
      if (this.ws) { try { this.ws.close(); } catch (e) {} }
      this.connect();
      this.stage.focus({ preventScroll: true });
    };
    const close = el("button", "btn", "Close");
    close.type = "button";
    close.onclick = () => closeTab(this.tab.id);
    actions.appendChild(again);
    actions.appendChild(close);
    dead.appendChild(actions);
    this.stage.appendChild(dead);
  }

  destroy() {
    this.closed = true;
    this.connectionSequence++;
    if (this.ws) { try { this.ws.close(); } catch (e) {} }
    this.ws = null;
    if (this.frameUrl) { URL.revokeObjectURL(this.frameUrl); this.frameUrl = null; }
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
    this.uploadRows = new Map();
    this.upgradeButtons = new Map();
    this.backendAutoToggles = new Map();
    this.upgradeReadiness = new Map();
    this.upgradesInProgress = new Set();
    this.upgradePollTimer = null;
    this.upgradePollGeneration = 0;
    this.engineUpgradeState = new Map();   // "bid:engine" -> "running" | "idle"
    this.engineUpgradePollTimer = null;
    this.engineUpgradePollGeneration = 0;
    this.localEngineGroup = null;
    this.root = el("div", "view settings");
    this.root.innerHTML = `<div class="settings-scroll"><div class="settings-inner"></div></div>`;
    this.inner = this.root.querySelector(".settings-inner");
  }
  destroy() {
    this.renderGeneration++;
    this.stopUpgradeReadinessPolling();
    this.stopEngineUpgradePolling();
    this.engineUpgradeState.clear();
    this.remoteEngineGroups.clear();
    this.remoteBackendDots.clear();
    this.remoteBackendMeta.clear();
    this.usageRows.clear();
    this.uploadRows.clear();
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

  stopEngineUpgradePolling() {
    this.engineUpgradePollGeneration++;
    if (this.engineUpgradePollTimer !== null) clearTimeout(this.engineUpgradePollTimer);
    this.engineUpgradePollTimer = null;
  }

  /* Engine payloads carry the node's own upgrade state, so an update in flight
     is discovered rather than remembered: a reload, a second browser, or a run
     another window started all converge on the same rows and the same report. */
  engineNodes() {
    const cached = bid => Object.prototype.hasOwnProperty.call(state.engCache, bid) ?
      state.engCache[bid] : null;
    return [{ bid: 0, name: backendName(0), engines: state.engines }]
      .concat(state.backends.map(b => ({ bid: b.id, name: b.name, engines: cached(b.id) })));
  }

  syncEngineUpgrades() {
    let running = false;
    for (const node of this.engineNodes()) {
      if (!Array.isArray(node.engines)) continue;
      for (const engine of node.engines) {
        const id = `${node.bid}:${engine.key}`;
        const was = this.engineUpgradeState.get(id);
        if (engine.upgrade_state === "running") {
          running = true;
          this.engineUpgradeState.set(id, "running");
        } else {
          this.engineUpgradeState.set(id, "idle");
          if (was === "running") this.reportEngineUpgrade(node.name, engine);
        }
      }
    }
    if (running) this.startEngineUpgradePolling();
    else this.stopEngineUpgradePolling();
  }

  reportEngineUpgrade(nodeName, engine) {
    const result = engine.upgrade_result;
    if (!result) {
      toast(`${nodeName}: ${engine.label} update finished`, "info", 6000);
      return;
    }
    if (!result.ok) {
      toast(`${nodeName}: ${engine.label} update failed`, "error", 7000);
      modalNotice(`${engine.label} update failed`,
        `${nodeName}: ${result.error || "the updater reported a failure"}` +
        (result.output ? `\n\n${result.output}` : ""));
      return;
    }
    if (result.changed) {
      toast(`${nodeName}: ${engine.label} updated to ${result.to_version || "a new version"}`,
        "ok", 6000);
      return;
    }
    /* Exit status alone is not proof: the vendor updater can succeed without
       moving the version. Report what it said and let the pill speak. */
    toast(`${nodeName}: ${engine.label} unchanged on ${result.to_version || "its version"}` +
      (result.message ? ` · ${result.message}` : ""), "info", 7000);
  }

  startEngineUpgradePolling() {
    if (this.engineUpgradePollTimer !== null) return;
    const generation = ++this.engineUpgradePollGeneration;
    const tick = async () => {
      this.engineUpgradePollTimer = null;
      /* Only destroy() ends this loop: a hidden or detached Settings pane must
         still land the result of an upgrade the user started from it. */
      if (generation !== this.engineUpgradePollGeneration) return;
      const nodes = [...new Set([...this.engineUpgradeState]
        .filter(([, value]) => value === "running")
        .map(([id]) => Number(id.split(":")[0])))];
      await Promise.all(nodes.map(async bid => {
        try {
          applyEnginesPayload(bid, await api(bid, "engines", { timeoutMs: ENGINE_POLL_TIMEOUT }));
        } catch (error) {
          console.warn("engine upgrade poll failed", error);
        }
      }));
      if (generation !== this.engineUpgradePollGeneration) return;
      if ([...this.engineUpgradeState.values()].includes("running"))
        this.engineUpgradePollTimer = setTimeout(tick, ENGINE_UPGRADE_POLL_INTERVAL);
    };
    this.engineUpgradePollTimer = setTimeout(tick, ENGINE_UPGRADE_POLL_INTERVAL);
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
      active_uploads: Math.max(0, Number(value.active_uploads) || 0),
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
      const description = upgrading ?
        "This backend upgrade is already in progress" : supported ?
        "Upgrade this backend automatically when it is outdated and idle" :
        "Automatic upgrades require an upgrade-capable headless backend";
      record.root.removeAttribute("title");
      record.root.removeAttribute("data-tip");
      record.input.setAttribute("aria-label", `${backend.name}: ${description}`);
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
      const set = (text, disabled, description) => {
        button.textContent = text;
        button.disabled = disabled;
        button.removeAttribute("title");
        button.removeAttribute("data-tip");
        button.setAttribute("aria-label", description ? `${text}: ${description}` : text);
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
    if (generation !== this.upgradePollGeneration || !isTabVisible(this.tab.id)) return;
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
      if (generation !== this.upgradePollGeneration || !isTabVisible(this.tab.id) ||
          !this.root.isConnected) return;
      try { await this.refreshUpgradeReadiness(generation); }
      catch (error) { console.warn("upgrade readiness poll failed", error); }
      if (generation === this.upgradePollGeneration && isTabVisible(this.tab.id) &&
          this.root.isConnected)
        this.upgradePollTimer = setTimeout(tick, UPGRADE_READINESS_INTERVAL);
    };
    tick();
  }

  syncRemoteState() {
    this.syncEngineUpgrades();
    if (this.localEngineGroup)
      this.localEngineGroup.update({ status: "ok", engines: state.engines });
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
      dot.setAttribute("aria-label", remoteAvailabilityTitle(bid));
    }
    for (const [bid, meta] of this.remoteBackendMeta) {
      const backend = state.backends.find(item => item.id === bid);
      if (!backend || !meta.isConnected) continue;
      const value = backendLocationVersion(backend);
      meta.textContent = value;
      meta.setAttribute("aria-label", value);
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
    for (const [bid, row] of this.uploadRows) {
      if (!bid) {
        row.update(state.uploadSettings, "ok", true);
        continue;
      }
      if (!state.backends.some(backend => backend.id === bid)) continue;
      row.update(state.remoteUploadSettings[bid] || null,
        remoteAvailability(bid), backendSupportsFileUploads(bid));
    }
    this.syncUpgradeButtons();
  }

  engineRow(bid, nodeName, e2) {
    const row = el("div", "engine-row");
    const identity = el("div", "engine-row-identity");
    identity.appendChild(el("span", "engine-dot " + e2.key));
    identity.appendChild(el("span", "engine-row-name", e2.label));
    const statuses = el("div", "engine-row-statuses");
    const versionState = !e2.installed ? "bad" : e2.update_available === true ? "warn" : "ok";
    const version = el("span", "pill engine-version " + versionState,
      e2.installed ? (e2.version || "installed") : "not installed");
    version.setAttribute("aria-label", e2.update_available === true && e2.latest_version ?
      `${version.textContent} · latest is ${e2.latest_version}` : version.textContent);
    statuses.appendChild(version);
    statuses.appendChild(el("span", "pill " + (e2.auth === "ok" ? "ok" : "bad"),
      "auth: " + e2.auth));
    row.appendChild(identity);
    row.appendChild(statuses);
    const action = this.engineUpdateButton(bid, nodeName, e2);
    if (action) {
      const actions = el("div", "engine-row-actions");
      actions.appendChild(action);
      row.appendChild(actions);
      row.classList.add("has-actions");
    }
    return row;
  }

  /* The update button is the verb for the amber "an update exists" pill, so it
     appears only when there is something to do (or something already running).
     A settled, current node keeps the panel exactly as it reads today. */
  engineUpdateButton(bid, nodeName, e2) {
    const upgrading = e2.upgrade_state === "running";
    if (!upgrading && !(e2.installed && e2.update_available === true)) return null;
    const button = el("button", "btn btn-sm engine-update", "Update");
    button.type = "button";
    const set = (text, disabled, description) => {
      button.textContent = text;
      button.disabled = disabled;
      button.setAttribute("aria-label", `${nodeName} ${e2.label}: ${description}`);
    };
    const target = e2.latest_version ? `v${e2.latest_version}` : "the latest version";
    if (upgrading) {
      set("Updating…", true, "update is running");
      button.classList.add("busy");
    } else if (!e2.upgrade_supported || !backendSupportsEngineUpgrade(bid)) {
      set("Update", true, "this node cannot update its engines from here");
    } else if (sessionsFor(bid).some(s => s.engine === e2.key && s.status === "running")) {
      set("Busy", true, "a session is using this engine - finish it first");
    } else {
      set("Update", false, `update to ${target}`);
      button.classList.add("wants-update");
      button.onclick = () => this.startEngineUpgrade(bid, nodeName, e2);
    }
    return button;
  }

  async startEngineUpgrade(bid, nodeName, e2) {
    try {
      const result = await api(bid, `engines/${encodeURIComponent(e2.key)}/upgrade`,
        { method: "POST", timeoutMs: 30000 });
      applyEnginesPayload(bid, result);
      toast(`${nodeName}: updating ${e2.label}…`, "info");
    } catch (error) {
      toast(`${nodeName}: ${error.message}`, "error", 7000);
      this.syncRemoteState();
    }
  }

  engineGroup(name, meta, bid) {
    const root = el("section", "engine-node");
    const head = el("div", "engine-node-head");
    const dot = el("span", "gdot pending");
    dot.setAttribute("role", "img");
    dot.setAttribute("aria-label", "checking");
    head.appendChild(dot);
    const nameEl = el("span", "engine-node-name", name);
    nameEl.setAttribute("aria-label", name);
    const metaEl = el("span", "engine-node-meta", meta);
    metaEl.setAttribute("aria-label", meta);
    head.appendChild(nameEl);
    head.appendChild(metaEl);
    let refresh = null;
    if (backendSupportsEngineUpgrade(bid)) {
      refresh = el("button", "engine-node-refresh");
      refresh.type = "button";
      refresh.setAttribute("aria-label",
        `Re-check installed and latest engine versions on ${name}`);
      refresh.appendChild(refreshIcon(11));
      refresh.onclick = () => refreshEngineVersions(bid, refresh, name);
      head.appendChild(refresh);
    }
    const body = el("div", "engine-node-body");
    root.appendChild(head); root.appendChild(body);

    const update = ({ status = "pending", engines = null, message = "", detail = "" }) => {
      dot.className = "gdot " + status;
      const statusLabel = status === "ok" ? "available" : status === "bad" ? "unavailable" : "checking";
      dot.setAttribute("aria-label", detail ? `${statusLabel}: ${detail}` : statusLabel);
      if (refresh && !refresh.classList.contains("refreshing"))
        refresh.disabled = status === "bad";
      body.innerHTML = "";
      if (message || engines === null) {
        const note = el("div", "engine-node-message", message || "Checking engines…");
        note.setAttribute("aria-label", detail || note.textContent);
        body.appendChild(note);
      } else if (!engines.length) {
        body.appendChild(el("div", "engine-node-message", "No engines reported"));
      } else {
        engines.forEach(e2 => body.appendChild(this.engineRow(bid, name, e2)));
        /* Silence is only trustworthy while the check itself works: say so
           when the latest-version lookup is the thing that is unavailable. */
        const stale = engines.find(e2 => e2.installed && e2.latest_check_error);
        if (stale)
          body.appendChild(el("div", "engine-node-message engine-node-stale",
            `Latest-version check unavailable · ${stale.latest_check_error}`));
      }
    };
    const setMeta = value => {
      metaEl.textContent = value || "";
      if (value) metaEl.setAttribute("aria-label", value);
      else metaEl.removeAttribute("aria-label");
    };
    return { root, update, setMeta };
  }

  usageRefreshRow(name, bid) {
    const root = el("div", "usage-refresh-row");
    const identity = el("div", "usage-refresh-identity");
    const nameEl = el("div", "usage-refresh-name", name);
    nameEl.setAttribute("aria-label", name);
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
    interval.setAttribute("aria-label",
      `${name} usage refresh interval in minutes; 0 disables automatic refresh; maximum 1440`);
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
      note.setAttribute("aria-label", description);
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
        applyUsageRefreshPayload(bid, result);
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

  uploadLimitRow(name, bid) {
    const root = el("div", "usage-refresh-row upload-limit-row");
    const identity = el("div", "usage-refresh-identity");
    const nameEl = el("div", "usage-refresh-name", name);
    nameEl.setAttribute("aria-label", name);
    const note = el("div", "usage-refresh-note", "Checking setting…");
    identity.appendChild(nameEl);
    identity.appendChild(note);

    const controls = el("div", "usage-refresh-controls");
    const limit = document.createElement("input");
    limit.type = "number";
    limit.min = "0";
    limit.max = "1024";
    limit.step = "1";
    limit.inputMode = "numeric";
    limit.setAttribute("aria-label",
      `${name} maximum upload size in MiB; 0 disables file uploads; maximum 1024`);
    const unit = el("span", "usage-refresh-unit", "MiB");
    const save = el("button", "btn btn-sm", "Apply");
    controls.appendChild(limit);
    controls.appendChild(unit);
    controls.appendChild(save);
    root.appendChild(identity);
    root.appendChild(controls);

    let current = null;
    let availability = bid ? "pending" : "ok";
    let supported = !bid;
    let saving = false;
    let loadError = "";
    const update = (metadata, reachable, canConfigure) => {
      current = normalizeUploadSettings(metadata) || null;
      availability = reachable;
      supported = canConfigure;
      if (!saving && current && document.activeElement !== limit)
        limit.value = String(current.max_file_size_mb);
      const disabled = saving || !canConfigure || reachable === "bad" || !current;
      limit.disabled = disabled;
      save.disabled = disabled;
      let description;
      if (!canConfigure) description = "Backend upgrade required";
      else if (reachable === "bad") description = "Backend unavailable";
      else if (!current) description = loadError || "Checking backend setting…";
      else if (!current.enabled) description = "File uploads are disabled";
      else description = `Any file type · ${current.max_file_size_mb} MiB maximum each`;
      note.textContent = description;
      note.setAttribute("aria-label", description);
    };
    const load = async () => {
      if (!supported) return;
      try {
        const result = await api(bid, "uploads/settings", { timeoutMs: 8000 });
        if (!root.isConnected) return;
        loadError = "";
        const policy = rememberUploadSettings(bid, result.uploads);
        if (!policy) throw new Error("backend returned an invalid upload setting");
        update(policy, bid ? remoteAvailability(bid) : "ok", true);
      } catch (error) {
        if (!root.isConnected) return;
        loadError = error.message || "Could not load upload setting";
        update(null, bid ? remoteAvailability(bid) : "ok", true);
      }
    };
    save.onclick = async () => {
      if (!limit.value.trim()) {
        toast("enter a maximum file size in MiB", "error");
        limit.focus();
        return;
      }
      const megabytes = Number(limit.value);
      if (!Number.isInteger(megabytes) || megabytes < 0 || megabytes > 1024) {
        toast("maximum file size must be a whole number from 0 to 1024 MiB", "error");
        limit.focus();
        return;
      }
      saving = true;
      save.textContent = "Saving…";
      update(current, availability, supported);
      try {
        const result = await api(bid, "uploads/settings", {
          method: "PATCH", body: { max_file_size_mb: megabytes },
        });
        loadError = "";
        const policy = rememberUploadSettings(bid, result.uploads);
        if (!policy) throw new Error("backend returned an invalid upload setting");
        current = policy;
        toast(`${name}: file uploads ${policy.enabled ? `limited to ${megabytes} MiB` : "disabled"}`, "ok");
      } catch (error) {
        toast(`${name}: ${error.message}`, "error", 7000);
      } finally {
        saving = false;
        save.textContent = "Apply";
        update(current, availability, supported);
      }
    };
    update(null, availability, supported);
    return { root, update, load };
  }

  /* One wiring for the instance card and every backend row: probe the node's
     browser status, gate the toggle on availability, and surface the reason.
     A row variant (no note element) carries the reason via title + tap toast. */
  async wireBrowserToggle(bid, input, note, generation, root = null) {
    const name = backendName(bid);
    const setNote = (text, warn) => {
      if (note) {
        note.textContent = text;
        note.classList.toggle("warn", !!warn);
      }
      if (root) root.title = text;
    };
    const apply = st => {
      input.checked = !!st.enabled;
      input.disabled = !st.available && !st.enabled;
      if (root) {
        root.classList.toggle("disabled", input.disabled);
        root.onclick = input.disabled ?
          () => toast(`${name}: ${st.reason || "no usable browser"}`, "error", 6000) : null;
      }
      if (st.available) {
        setNote((st.product || "browser available") +
          (st.sandbox === "no-sandbox" ? " · sandbox off (runs as root)" : ""), false);
      } else {
        setNote(st.reason || "no usable browser on this node", true);
      }
    };
    /* Only availability needs the probe: whether the toggle is on is already
       known from /api/state and the node pings. Seed the switch from that, or
       it renders off and visibly flips on a moment later. */
    input.checked = browserEnabledFor(bid);
    let status;
    try {
      status = await api(bid, "browser/status", { timeoutMs: ENGINE_POLL_TIMEOUT });
    } catch (error) {
      if (generation !== this.renderGeneration || !input.isConnected) return;
      input.disabled = true;
      if (root) root.classList.add("disabled");
      setNote(error.message || "browser status unavailable", true);
      return;
    }
    if (generation !== this.renderGeneration || !input.isConnected) return;
    apply(status);
    input.onchange = async () => {
      const desired = input.checked;
      input.disabled = true;
      setNote(desired ? "Enabling…" : "Disabling…", false);
      try {
        const result = await api(bid, "browser/enabled", {
          method: "POST", body: { enabled: desired } });
        apply(result);
        if (bid) state.remoteBrowser[bid] = { enabled: !!result.enabled };
        else state.browser = { enabled: !!result.enabled };
        renderSidebar();
        toast(`${name}: browser ${result.enabled ? "enabled" : "disabled"}`, "ok");
      } catch (error) {
        input.checked = !desired;
        input.disabled = false;
        setNote(error.message, true);
        toast(`${name}: ${error.message}`, "error", 6500);
      }
    };
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
    rememberUploadSettings(0, settings.uploads);
    state.localEngineCheckedAt = Date.now();
    state.engMap = {};
    state.engines.forEach(e2 => state.engMap[e2.key] = e2);
    renderFootEngines();
    this.inner.innerHTML = "";
    this.remoteEngineGroups.clear();
    this.remoteBackendDots.clear();
    this.remoteBackendMeta.clear();
    this.usageRows.clear();
    this.uploadRows.clear();
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
      <label class="be-auto be-auto-add browser-toggle">
        <input type="checkbox" id="set-browser" disabled
          aria-label="Enable the managed browser on this instance">
        <span class="be-auto-track" aria-hidden="true"><span></span></span>
        <span class="be-auto-copy"><span>Browser</span>
          <small id="set-browser-note">Checking availability…</small></span>
      </label>
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
      <div class="kv"><span class="k">API token</span><span class="v" id="set-token"
        role="button" tabindex="0" aria-label="Reveal API token" style="cursor:pointer">••••••••••••</span></div>
      <div class="m-btns" style="justify-content:flex-start;margin-top:10px">
        <button class="btn btn-pri btn-sm" id="set-save">Save</button>
        <button class="btn btn-sm btn-ghost" id="set-logout">Log out</button>
      </div>
`;
    this.inner.appendChild(c1);
    this.wireBrowserToggle(0, c1.querySelector("#set-browser"),
      c1.querySelector("#set-browser-note"), generation);
    let tokenShown = false;
    const tokenControl = c1.querySelector("#set-token");
    const useToken = () => {
      if (!tokenShown) {
        tokenControl.textContent = settings.api_token;
        tokenControl.setAttribute("aria-label", "Copy API token");
        tokenShown = true;
      } else copyWithToast(settings.api_token, "token copied");
    };
    tokenControl.onclick = useToken;
    tokenControl.onkeydown = event => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      useToken();
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
    const localGroup = this.engineGroup(settings.instance_name,
      `this instance · v${settings.version}`, 0);
    localGroup.update({ status: "ok", engines: state.engines });
    this.localEngineGroup = localGroup;
    c2.appendChild(localGroup.root);
    for (const b of state.backends) {
      const meta = backendLocationVersion(b);
      const group = this.engineGroup(b.name, meta, b.id);
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

    /* per-node attachment limits */
    const uploadCard = el("div", "card upload-limit-card");
    uploadCard.innerHTML = `<h2>File uploads</h2>
      <p class="usage-refresh-copy">Set the maximum size of one attached file on each
        receiving node. Files stream directly to private session storage and may be any type.
        Use 0 to disable uploads.</p>`;
    const uploadList = el("div", "upload-limit-list");
    const localUploads = this.uploadLimitRow(settings.instance_name, 0);
    localUploads.update(state.uploadSettings, "ok", true);
    this.uploadRows.set(0, localUploads);
    uploadList.appendChild(localUploads.root);
    const uploadLoads = [];
    for (const b of state.backends) {
      const row = this.uploadLimitRow(b.name, b.id);
      const supported = backendSupportsFileUploads(b.id);
      row.update(state.remoteUploadSettings[b.id] || null,
        remoteAvailability(b.id), supported);
      this.uploadRows.set(b.id, row);
      uploadList.appendChild(row.root);
      if (supported) uploadLoads.push(row.load);
    }
    uploadCard.appendChild(uploadList);
    this.inner.appendChild(uploadCard);
    uploadLoads.forEach(load => load());
    this.syncRemoteState();
    pollRemotes({ forceEngines: true })
      .catch(error => console.warn("settings remote poll failed", error));

    /* completion alert: a command a chosen node runs when a session finishes */
    const notifyCard = el("div", "card notify-card");
    notifyCard.innerHTML = `<div class="notify-head">
        <h2>Completion alert</h2>
        <label class="be-auto notify-toggle" id="nf-enabled-wrap">
          <input type="checkbox" id="nf-enabled">
          <span class="be-auto-track" aria-hidden="true"><span></span></span>
          <span class="be-auto-label">Enabled</span>
        </label>
      </div>
      <p class="usage-refresh-copy">When a session finishes its work — its prompt and
        anything queued behind it — run this command on a node: play a sound, ping your home
        automation, anything. Arm or silence it any time with the bell in the sidebar footer.</p>
      <div class="notify-fields">
        <label>Run on<select id="nf-backend" aria-label="Node the command runs on"></select></label>
        <label>Command<input type="text" id="nf-cmd" autocomplete="off" autocapitalize="off"
          spellcheck="false" placeholder='e.g. mosquitto_pub -t puppy/done -m {session}'></label>
      </div>
      <p class="usage-refresh-copy">Placeholders <span class="mono-inline">{backend} {session}
        {engine} {model} {status} {duration} {cwd} {id}</span> are substituted shell-quoted, and the
        same values arrive as <span class="mono-inline">PUPPY_*</span> environment variables.
        The switch and sidebar bell control the same enabled state. With no command, completions
        do nothing and the bell stays hidden.</p>
      <div class="notify-actions">
        <button class="btn btn-pri btn-sm" id="nf-save">Save</button>
        <button class="btn btn-sm" id="nf-test">Test</button>
        <span class="notify-note" id="nf-note"></span>
      </div>`;
    const nfBackend = notifyCard.querySelector("#nf-backend");
    const nfCmd = notifyCard.querySelector("#nf-cmd");
    const nfEnabled = notifyCard.querySelector("#nf-enabled");
    const nfNote = notifyCard.querySelector("#nf-note");
    const nfLocal = document.createElement("option");
    nfLocal.value = "0";
    nfLocal.textContent = `${backendName(0)} (local)`;
    nfBackend.appendChild(nfLocal);
    for (const b of state.backends) {
      const option = document.createElement("option");
      option.value = String(b.id);
      const capable = Array.isArray(b.capabilities) && b.capabilities.includes("notify-exec");
      option.textContent = b.name + (capable ? "" : " — upgrade to enable");
      option.disabled = !capable;
      nfBackend.appendChild(option);
    }
    this.inner.appendChild(notifyCard);
    syncBell();
    enhanceChoiceSelect(nfBackend);
    const nfNoteSet = (text, bad = false) => {
      nfNote.textContent = text;
      nfNote.classList.toggle("bad", !!bad);
    };
    api(0, "notify").then((r) => {
      nfCmd.value = r.settings.command || "";
      const have = [...nfBackend.options].some(o => o.value === String(r.settings.backend));
      nfBackend.value = have ? String(r.settings.backend) : "0";
      refreshChoiceSelect(nfBackend);
      state.notify = { configured: !!(r.settings.command || "").trim(),
                       enabled: !!r.settings.enabled };
      syncBell();
    }).catch(() => nfNoteSet("could not load the current setting", true));
    nfEnabled.onchange = async () => {
      const desired = nfEnabled.checked;
      nfEnabled.dataset.saving = "true";
      syncBell();
      nfNoteSet("");
      try {
        const r = await api(0, "notify/toggle", {
          method: "POST", body: { enabled: desired },
        });
        state.notify = { configured: !!(r.settings.command || "").trim(),
                         enabled: !!r.settings.enabled };
      } catch (e) {
        nfNoteSet(e.message, true);
      } finally {
        delete nfEnabled.dataset.saving;
        syncBell();
      }
    };
    notifyCard.querySelector("#nf-save").onclick = async () => {
      try {
        const r = await api(0, "notify", { method: "POST", body: {
          backend: Number(nfBackend.value || 0), command: nfCmd.value,
        } });
        state.notify = { configured: !!(r.settings.command || "").trim(),
                         enabled: !!r.settings.enabled };
        syncBell();
        nfNoteSet("saved");
        toast(state.notify.configured ? "completion alert saved" :
          "completion alert command cleared", "ok");
      } catch (e) { nfNoteSet(e.message, true); }
    };
    notifyCard.querySelector("#nf-test").onclick = async () => {
      nfNoteSet("running…");
      try {
        const r = await api(0, "notify/test", { method: "POST", body: {
          backend: Number(nfBackend.value || 0), command: nfCmd.value,
        } });
        if (r.ok) nfNoteSet("ok" + (r.output ? " · " + r.output.slice(-120) : ""));
        else nfNoteSet(r.error || `exit ${r.rc}` + (r.output ? " · " + r.output.slice(-120) : ""), true);
      } catch (e) { nfNoteSet(e.message, true); }
    };

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
        <label class="be-auto be-auto-add full">
          <input type="checkbox" id="be-auto"
            aria-label="Upgrade this headless backend automatically when it is outdated and idle">
          <span class="be-auto-track" aria-hidden="true"><span></span></span>
          <span class="be-auto-copy"><span>Auto-upgrade when idle</span>
            <small>Signed release · readiness checked · rollback protected</small></span>
        </label>
        <label class="be-auto be-auto-add full">
          <input type="checkbox" id="be-browser"
            aria-label="Turn on the managed browser on this backend once it is added">
          <span class="be-auto-track" aria-hidden="true"><span></span></span>
          <span class="be-auto-copy"><span>Managed browser</span>
            <small>Enabled once the backend is added, if its node supports one</small></span>
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
        availability.setAttribute("role", "img");
        availability.setAttribute("aria-label", "checking");
        const name = el("span", "be-name", b.name);
        const backendIdentity = [b.role, b.remote_version && `v${b.remote_version}`,
          b.protocol != null && `protocol ${b.protocol}`].filter(Boolean).join(" · ");
        name.setAttribute("aria-label", backendIdentity ?
          `${b.name} · ${backendIdentity}` : b.name);
        const backendMeta = backendLocationVersion(b);
        const url = el("span", "be-url", backendMeta);
        url.setAttribute("aria-label", backendMeta);
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
        const securityLabel = isPinned
          ? `Encrypted · pinned certificate SHA-256: ${b.tls_fingerprint}`
          : isTls ? "Encrypted · certificate verified by the controller system trust store"
            : "Cleartext · traffic to this backend is not encrypted";
        security.setAttribute("role", "img");
        security.setAttribute("aria-label", securityLabel);
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
        const toggles = el("div", "be-toggles");
        toggles.appendChild(autoRoot);
        if (backendHasCapability(b, "browser")) {
          const browserRoot = el("label", "be-auto be-auto-existing be-browser");
          const browserInput = document.createElement("input");
          browserInput.type = "checkbox";
          browserInput.disabled = true;
          browserInput.setAttribute("aria-label", `Enable the managed browser on ${b.name}`);
          const browserTrack = el("span", "be-auto-track");
          browserTrack.setAttribute("aria-hidden", "true");
          browserTrack.appendChild(el("span"));
          browserRoot.appendChild(browserInput);
          browserRoot.appendChild(browserTrack);
          browserRoot.appendChild(el("span", "be-auto-label", "Browser"));
          this.wireBrowserToggle(b.id, browserInput, null, generation, browserRoot);
          toggles.appendChild(browserRoot);
        }
        row.appendChild(toggles);
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
    this.inner.insertBefore(c3, c2);
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
        const wantBrowser = c3.querySelector("#be-browser").checked;
        const added = await api(0, "backends", { method: "POST", body: {
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
        c3.querySelector("#be-browser").checked = false;
        /* The browser is per-node state, so the box is an intent: the node only
           exists to answer once it has been added. A refusal leaves the backend
           in place - it is a separate setting, not part of the connection. */
        if (wantBrowser) await enableAddedBackendBrowser(added);
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
async function modalNewSession(groupId = null) {
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
  /* Every new session starts from the configured default, never from wherever
     the last one happened to be pointed. */
  cwdInp.value = state.defaultCwd || "/";
  lsDel("puppy.lastcwd");   // drop what older builds remembered
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
    scratchButton.removeAttribute("title");
    scratchButton.removeAttribute("data-tip");
    if (supported) scratchButton.removeAttribute("aria-label");
    else scratchButton.setAttribute("aria-label", "Scratch workspace · backend upgrade required");
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
      close();
      if (bid) await pollRemotes();
      openSessionTab(bid, r.session.id, r.session, groupId);
    } catch (e) { toast(e.message, "error"); }
  };
}

/* open existing session */
function modalOpenSession(groupId = null) {
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
        workspace.setAttribute("aria-label", workspaceTitle(s));
        r2.appendChild(workspace);
        item.appendChild(r1); item.appendChild(r2);
        item.onclick = () => { close(); openSessionTab(g.bid, s.id, s, groupId); };
        list.appendChild(item);
      }
    }
  };
  filterInp.addEventListener("input", render);
  filterInp.focus();
  render();
}

/* new terminal */
function modalNewTerminal(groupId = null) {
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
    openTermTab(bid, cmd, groupId);
  };
  m.querySelector("#nt-go").onclick = go;
  m.querySelector("#nt-cmd").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
}

/* new isolated browser */
function openBrowserFromMenu(groupId = null) {
  const nodes = [{ id: 0, name: backendName(0) }]
    .concat(state.backends)
    .filter(node => browserEnabledFor(node.id));
  if (!nodes.length) {
    toast("no node has its browser enabled · see Settings", "error", 6000);
    openSettingsTab(groupId);
    return;
  }
  if (nodes.length === 1) {
    openNewBrowser(nodes[0].id, groupId);
    return;
  }
  const { m, close } = modal(`<h2>Open browser</h2>
    <label>Node<select id="nb-be">${nodes.map(b =>
      `<option value="${b.id}">${esc(b.name)}</option>`).join("")}</select></label>
    <div class="m-btns"><button class="btn" id="nb-cancel">Cancel</button>
    <button class="btn btn-pri" id="nb-go">Open</button></div>`);
  m.querySelector("#nb-cancel").onclick = close;
  m.querySelector("#nb-go").onclick = () => {
    const bid = parseInt(m.querySelector("#nb-be").value, 10) || 0;
    close();
    openNewBrowser(bid, groupId);
  };
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
