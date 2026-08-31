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
    /* lighter than the 2 a tab dot uses: this is 24-box art in a 14px footer
       button, where 2 reads heavy - the gear beside it runs 1.5 for the same
       reason, and both sit next to the ☀ glyph */
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

/* The font sun/moon this replaces has an asymmetric em box. Keep both theme
   states in the same centred 24-box as the footer's gear and bell instead. */
function themeIcon(size, moon) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.5");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  if (moon) {
    path.setAttribute("d", "M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z");
    svg.appendChild(path);
    return svg;
  }
  const sun = document.createElementNS(NS, "circle");
  sun.setAttribute("cx", "12");
  sun.setAttribute("cy", "12");
  sun.setAttribute("r", "4");
  sun.setAttribute("fill", "none");
  sun.setAttribute("stroke", "currentColor");
  sun.setAttribute("stroke-width", "1.5");
  path.setAttribute("d", "M12 2v2M12 20v2M4.93 4.93l1.42 1.42" +
    "M17.65 17.65l1.42 1.42M2 12h2M20 12h2M4.93 19.07l1.42-1.42" +
    "M17.65 6.35l1.42-1.42");
  svg.appendChild(sun);
  svg.appendChild(path);
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

/* stroke is a parameter for the same reason bellIcon runs lighter: this 24-box
   art is drawn at 12px in a tab dot and 14px in the footer, where the tab's
   weight of 2 reads heavy beside the bell it sits next to. */
/* The picker's rows used to be "📁 name" and "↩ parent" - two glyphs from
   different fonts with different advance widths, so the labels could not line
   up however they were spaced. Drawn icons in a fixed slot align by
   construction, which is the same reason xIcon and plusIcon exist. */
function folderIcon(size) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(NS, "path");
  p.setAttribute("d", "M1.9 12.4V4.2a1 1 0 0 1 1-1h3l1.5 1.7h6.7a1 1 0 0 1 1 1v6.5" +
    "a1 1 0 0 1-1 1H2.9a1 1 0 0 1-1-1Z");
  p.setAttribute("fill", "none");
  p.setAttribute("stroke", "currentColor");
  p.setAttribute("stroke-width", "1.2");
  p.setAttribute("stroke-linejoin", "round");
  svg.appendChild(p);
  return svg;
}

function parentDirIcon(size) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(NS, "path");
  p.setAttribute("d", "M8 12.8V3.7M4.6 7.1 8 3.7l3.4 3.4");
  p.setAttribute("fill", "none");
  p.setAttribute("stroke", "currentColor");
  p.setAttribute("stroke-width", "1.4");
  p.setAttribute("stroke-linecap", "round");
  p.setAttribute("stroke-linejoin", "round");
  svg.appendChild(p);
  return svg;
}

/* One row shape for every directory picker: fixed icon slot, then the label.
   `icon` may be null for a placeholder row, which keeps its text on the same
   left edge as the entries around it. */
function dirPickRow(kind, label) {
  const button = el("button", "");
  button.type = "button";
  const slot = el("span", "dp-icon " + kind);
  if (kind === "dp-up") slot.appendChild(parentDirIcon(13));
  else if (kind === "dp-folder") slot.appendChild(folderIcon(14));
  button.appendChild(slot);
  button.appendChild(el("span", "dp-name", label));
  /* Rows never take focus from the field that owns them. A row that did would
     fire focusout, closing the list before its own click could land - and the
     re-render would then destroy the very node holding the focus. */
  button.addEventListener("mousedown", event => event.preventDefault());
  return button;
}

/* Wire a text input to a directory list: the field stays authoritative (it can
   still be typed or pasted into) and the list is a way to walk to a path
   without knowing how to spell it. `bidFor` names the node to browse, so a
   picker can follow a backend selector or stay pinned to this instance. */
/* True while a pointer press is in flight. Anything that reacts to focus loss
   needs to know: the press moves focus on the way DOWN, so collapsing in-flow
   content there reflows the page between mousedown and mouseup. The element
   the user aimed at slides out from under the cursor and the browser never
   dispatches the click - the gesture is spent closing something instead of
   doing what it was for. */
let pointerPressed = false;
document.addEventListener("pointerdown", () => { pointerPressed = true; }, true);
for (const kind of ["pointerup", "pointercancel"])
  document.addEventListener(kind, () => { pointerPressed = false; }, true);

/* Run `action` after the press in flight has been released and the click it
   produces has been dispatched, or straight away when nothing is pressed - so
   a keyboard tab-out keeps closing instantly. */
function afterPointerRelease(action) {
  if (!pointerPressed) { action(); return; }
  const finish = () => {
    for (const kind of ["pointerup", "pointercancel"])
      document.removeEventListener(kind, finish, true);
    // a task rather than a microtask: the click follows the release, and this
    // has to land after it
    setTimeout(action, 0);
  };
  for (const kind of ["pointerup", "pointercancel"])
    document.addEventListener(kind, finish, true);
}

function wireDirectoryPicker(input, box, bidFor) {
  let timer = null;
  let shown = null;   // the path the list is currently showing
  const browse = async () => {
    try {
      const d = await api(bidFor(), "fs?path=" + encodeURIComponent(input.value || "/"));
      box.classList.remove("hidden");
      box.innerHTML = "";
      if (d.parent !== null && d.parent !== undefined) {
        const up = dirPickRow("dp-up", d.parent);
        up.onclick = () => { input.value = d.parent; browse(); };
        box.appendChild(up);
      }
      for (const name of d.dirs) {
        const row = dirPickRow("dp-folder", name);
        row.onclick = () => {
          input.value = (d.path === "/" ? "" : d.path) + "/" + name;
          browse();
        };
        box.appendChild(row);
      }
      if (!d.dirs.length) {
        const empty = dirPickRow("dp-none", "(No subdirectories)");
        empty.disabled = true;
        box.appendChild(empty);
      }
      /* A new directory starts at its own beginning. Emptying and refilling in
         one task never forces a layout, so the old offset would otherwise
         survive and drop you into the middle of an unrelated listing. Only on
         a real change: re-listing the same path (a keystroke that resolves
         back to it) keeps your place. */
      if (d.path !== shown) {
        shown = d.path;
        box.scrollTop = 0;
      }
    } catch (error) { box.classList.add("hidden"); }
  };
  const close = () => {
    box.classList.add("hidden");
    shown = null;   // a fresh open starts at the top of its listing
  };
  input.addEventListener("focus", browse);
  /* Escape already has focus in the field, so clicking back in fires no focus
     event: reopen on a click the list is closed for. */
  input.addEventListener("click", () => {
    if (box.classList.contains("hidden")) browse();
  });
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(browse, 350);
  });

  /* Focus leaving the pair closes the list. Tabbing from the field into a row
     stays inside it, so relatedTarget is checked against both. */
  const leaving = event => {
    const to = event.relatedTarget;
    if (to && (to === input || box.contains(to))) return;
    clearTimeout(timer);
    /* The list is in normal flow, so closing it lifts everything below by up
       to its full height. Held until the press that took the focus has landed,
       the click reaches what was aimed at and the list still closes. */
    afterPointerRelease(() => {
      const focused = document.activeElement;
      if (focused === input || box.contains(focused)) return;   // focus came back
      close();
    });
  };
  input.addEventListener("focusout", leaving);
  box.addEventListener("focusout", leaving);

  /* Escape closes the list and stops there. Marking the event handled is what
     keeps an enclosing modal open - its own Escape handler skips a
     defaultPrevented event - so the first press dismisses the list and a
     second one closes the modal. */
  const escape = event => {
    if (event.key !== "Escape" || box.classList.contains("hidden")) return;
    event.preventDefault();
    clearTimeout(timer);
    close();
    input.focus();
  };
  input.addEventListener("keydown", escape);
  box.addEventListener("keydown", escape);
  return browse;
}

function gearIcon(size, stroke = 2) {
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
  gear.setAttribute("stroke-width", String(stroke));
  gear.setAttribute("stroke-linecap", "round");
  gear.setAttribute("stroke-linejoin", "round");
  const hub = document.createElementNS(NS, "circle");
  hub.setAttribute("cx", "12");
  hub.setAttribute("cy", "12");
  hub.setAttribute("r", "3");
  hub.setAttribute("fill", "none");
  hub.setAttribute("stroke", "currentColor");
  hub.setAttribute("stroke-width", String(stroke));
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

function checkIcon(size = 13) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", "M3.5 8.6 6.4 11.5 12.5 5.2");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.9");
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

/* Queue controls use action icons: a waiting prompt shows pause, while one
   already paused shows play so the next click's effect is unambiguous. */
function queuePauseIcon(paused, size = 10) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 12 12");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  if (paused) {
    const play = document.createElementNS(NS, "path");
    play.setAttribute("d", "M4 2.7 9 6 4 9.3Z");
    play.setAttribute("fill", "currentColor");
    svg.appendChild(play);
  } else {
    for (const x of [3.6, 7.2]) {
      const bar = document.createElementNS(NS, "path");
      bar.setAttribute("d", `M${x} 2.8V9.2`);
      bar.setAttribute("fill", "none");
      bar.setAttribute("stroke", "currentColor");
      bar.setAttribute("stroke-width", "1.55");
      bar.setAttribute("stroke-linecap", "round");
      svg.appendChild(bar);
    }
  }
  return svg;
}

function queueEditIcon(size = 10) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 12 12");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", "M2.3 9.7 3 7.2l4.9-4.9a1 1 0 0 1 1.4 0l.4.4a1 1 0 0 1 0 1.4L4.8 9zM7.3 2.9l1.8 1.8");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.15");
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

const TOAST_SWIPE_INTENT_PX = 8;
const TOAST_SWIPE_FLING_PX = 28;
const TOAST_SWIPE_FLING_VELOCITY = .55;
const TOAST_SWIPE_SETTLE_MS = 180;

/* A toast sits against the right edge, so a rightward finger swipe has one
   unambiguous destination. Pointer type is checked per gesture rather than by
   viewport size: mouse use on a tablet remains ordinary, while touch on a
   convertible still works. Vertical intent is left to native page scrolling. */
function wireToastSwipe(target, dismiss, pause, resume) {
  let gesture = null;
  let settleTimer = null;

  const record = (current, position, time) => {
    current.samples.push({ position, time });
    const cutoff = time - 100;
    while (current.samples.length > 1 && current.samples[0].time < cutoff)
      current.samples.shift();
  };

  const velocity = current => {
    const first = current.samples[0];
    const last = current.samples[current.samples.length - 1];
    const elapsed = last && first ? last.time - first.time : 0;
    return elapsed > 0 ? (last.position - first.position) / elapsed : 0;
  };

  const clearSettle = () => {
    if (settleTimer !== null) clearTimeout(settleTimer);
    settleTimer = null;
    target.classList.remove("toast-swipe-settling", "toast-swipe-dismissing");
    target.style.removeProperty("transform");
    target.style.removeProperty("opacity");
  };

  const settleBack = () => {
    target.classList.remove("toast-swiping");
    target.classList.add("toast-swipe-settling");
    void target.offsetWidth;
    target.style.transform = "translate3d(0,0,0)";
    target.style.opacity = "1";
    settleTimer = setTimeout(() => {
      clearSettle();
      resume();
    }, TOAST_SWIPE_SETTLE_MS + 40);
  };

  const settleOut = () => {
    target.classList.remove("toast-swiping");
    target.classList.add("toast-swipe-settling", "toast-swipe-dismissing");
    /* Preserve the finger-owned inline frame for one layout, then expose the
       class-owned offscreen endpoint so the transition starts exactly there. */
    void target.offsetWidth;
    target.style.removeProperty("transform");
    target.style.removeProperty("opacity");
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      if (settleTimer !== null) clearTimeout(settleTimer);
      settleTimer = null;
      dismiss();
    };
    target.addEventListener("transitionend", finish, { once: true });
    settleTimer = setTimeout(finish, TOAST_SWIPE_SETTLE_MS + 40);
  };

  const move = (current, event) => {
    const dx = event.clientX - current.startX;
    const dy = event.clientY - current.startY;
    if (!current.axis) {
      const ax = Math.abs(dx);
      const ay = Math.abs(dy);
      if (Math.max(ax, ay) < TOAST_SWIPE_INTENT_PX) return;
      if (ay > ax * 1.1) { current.axis = "vertical"; return; }
      if (ax <= ay * 1.1) return;
      current.axis = dx > 0 ? "right" : "left";
      if (current.axis === "right") target.classList.add("toast-swiping");
    }
    if (current.axis !== "right") return;
    event.preventDefault();
    current.position = Math.max(0, dx);
    target.style.transform = `translate3d(${Math.round(current.position)}px,0,0)`;
    target.style.opacity = String(Math.max(0, 1 - current.position / current.width));
    record(current, current.position, event.timeStamp);
  };

  const finish = (event, cancelled = false) => {
    const current = gesture;
    if (!current || event.pointerId !== current.id) return;
    if (!cancelled) move(current, event); // retain a one-frame finger flick
    gesture = null;
    try { target.releasePointerCapture(current.id); } catch (error) {}
    if (current.axis !== "right") { resume(); return; }
    event.preventDefault();
    const distance = Math.min(120, Math.max(56, current.width * .32));
    const flung = current.position >= TOAST_SWIPE_FLING_PX &&
      velocity(current) >= TOAST_SWIPE_FLING_VELOCITY;
    if (!cancelled && (current.position >= distance || flung)) settleOut();
    else settleBack();
  };

  target.addEventListener("pointerdown", event => {
    if (event.pointerType !== "touch" || event.isPrimary === false || gesture) return;
    clearSettle();
    target.classList.add("toast-touch");
    gesture = {
      id: event.pointerId, startX: event.clientX, startY: event.clientY,
      width: Math.max(1, target.getBoundingClientRect().width),
      axis: "", position: 0,
      samples: [{ position: 0, time: event.timeStamp }],
    };
    pause();
    try { target.setPointerCapture(event.pointerId); } catch (error) {}
  });
  target.addEventListener("pointermove", event => {
    if (gesture && event.pointerId === gesture.id) move(gesture, event);
  }, { passive: false });
  target.addEventListener("pointerup", event => finish(event));
  target.addEventListener("pointercancel", event => finish(event, true));
}

function toast(text, level = "info", ms = 4200) {
  const t = el("div", "toast " + (level === "error" ? "err" : level === "ok" ? "ok" : ""), text);
  $("toasts").appendChild(t);
  const deadline = Date.now() + ms;
  let removalTimer = null;
  const remove = () => {
    if (removalTimer !== null) clearTimeout(removalTimer);
    removalTimer = null;
    t.remove();
  };
  const pauseRemoval = () => {
    if (removalTimer !== null) clearTimeout(removalTimer);
    removalTimer = null;
  };
  const resumeRemoval = () => {
    if (!t.isConnected) return;
    removalTimer = setTimeout(remove, Math.max(800, deadline - Date.now()));
  };
  wireToastSwipe(t, remove, pauseRemoval, resumeRemoval);
  removalTimer = setTimeout(remove, ms);
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

async function copyWithToast(text, message = "Copied") {
  try { await writeClipboardText(text); toast(message); return true; }
  catch (e) { toast("Copy failed", "error"); return false; }
}

function userMessageCopyButton(text) {
  const button = el("button", "user-copy");
  button.type = "button";
  button.setAttribute("aria-label", "Copy message");
  button.appendChild(copyIcon());
  button.onclick = async event => {
    event.preventDefault();
    event.stopPropagation();
    try {
      await writeClipboardText(text);
      clearTimeout(button._copyReset);
      button.classList.add("done");
      button.replaceChildren(copyIcon(true));
      button.setAttribute("aria-label", "Copied");
      button._copyReset = setTimeout(() => {
        if (!button.isConnected) return;
        button.classList.remove("done");
        button.replaceChildren(copyIcon());
        button.setAttribute("aria-label", "Copy message");
      }, 1400);
    } catch (error) { toast("Copy failed", "error"); }
  };
  return button;
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
/* How long the load-older button stays put before fetching itself, and how far
   before the top of the transcript that countdown begins. */
const LOAD_OLDER_DELAY = 550;
const LOAD_OLDER_MARGIN = 140;

/* the header's thinking status; the live thinking block mirrors the header
   verbatim, so this is also what that block reads while a turn is thinking */
function thinkingLabel(tokens) {
  return tokens ? `Thinking… ${fmtTokens(tokens)} tokens` : "Thinking…";
}
function thinkingIconNode() {
  return el("span", "think-brain", "🧠");
}
/* Every live prompt status ends with one shared, width-stable dot animation.
   Engines use both `...` and `…`; remove that marker wherever it arrived so
   token-bearing labels become "Thinking 42 tokens" followed by the same dots. */
function promptStatusBase(text) {
  return String(text == null ? "" : text)
    .replace(/\s*(?:…|\.{3})\s*/, " ").trim();
}
function promptStatusLabel(text, className) {
  const value = String(text == null ? "" : text);
  const label = el("span", `${className} prompt-status-label`, promptStatusBase(value));
  /* Generated dots are visual only. The stable source phrase prevents an
     assistive technology from announcing every animation frame. */
  label.setAttribute("aria-label", value);
  return label;
}
/* Claude exposes a streaming thinking block, while Codex first announces the
   same phase as a plain status. Give either spelling the same visual marker. */
function isThinkingStatus(text) {
  return /^thinking(?:\s|…|\.{3}|$)/i.test(String(text || "").trim());
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

/* Server-side drafts are authoritative. This versioned local record is only a
   crash journal between a browser edit and its server acknowledgement. */
const DRAFT_JOURNAL_VERSION = 1;
const DEFAULT_DRAFT_MAX_CHARS = 256 * 1024;
function readDraftJournal(tabId) {
  const raw = lsGet("puppy.draft." + tabId);
  if (raw === null) return null;
  try {
    const value = JSON.parse(raw);
    if (value && value._puppy_draft === DRAFT_JOURNAL_VERSION &&
        typeof value.text === "string" && Number.isInteger(value.base_revision) &&
        value.base_revision >= 0)
      return { text: value.text, baseRevision: value.base_revision,
        submitted: value.submitted === true };
  } catch (_) { /* invalid or incomplete journal */ }
  return null;
}

/* Nodes without shared-draft support use an intentionally local plain string.
   It is a separate live protocol mode, not another journal format. */
function readLocalDraft(tabId) {
  const raw = lsGet("puppy.draft." + tabId);
  return raw === null ? null : { text: raw, baseRevision: 0, submitted: false };
}

function writeDraftJournal(tabId, text, baseRevision, submitted = false) {
  try {
    lsSet("puppy.draft." + tabId, JSON.stringify({
      _puppy_draft: DRAFT_JOURNAL_VERSION,
      text: String(text || ""),
      base_revision: Math.max(0, Number(baseRevision) || 0),
      submitted: submitted === true,
    }));
  } catch (_) { /* storage is optional; the socket remains authoritative */ }
}

function newDraftClientId() {
  try {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function")
      return globalThis.crypto.randomUUID();
  } catch (_) {}
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
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
const disclosureMotionTimers = new WeakMap();

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
function setDisclosureCollapsed(body, collapsed, animate = false) {
  const oldTimer = disclosureMotionTimers.get(body);
  if (oldTimer) clearTimeout(oldTimer);
  disclosureMotionTimers.delete(body);

  const reduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!animate || reduced) {
    body.classList.remove("disclosure-animating");
    body.style.removeProperty("height");
    body.style.removeProperty("opacity");
    body.style.removeProperty("margin-top");
    body.hidden = collapsed;
    return;
  }

  /* Pin the panel at its live size before changing direction. This also makes
     a rapid second click reverse smoothly from the middle of a transition. */
  const wasHidden = body.hidden;
  const style = wasHidden ? null : getComputedStyle(body);
  const currentHeight = wasHidden ? 0 : body.getBoundingClientRect().height;
  const currentOpacity = wasHidden ? 0 : parseFloat(style.opacity) || 0;
  const currentMargin = wasHidden ? 0 : parseFloat(style.marginTop) || 0;

  /* Measure at auto height rather than with scrollHeight: compact text rows
     often land on a half pixel, while scrollHeight rounds to an integer and
     would leave a small snap when the inline target is cleared at the end.
     Opacity and spacing stay pinned meanwhile, so revealing a hidden panel
     cannot paint or establish a transition from their expanded values. */
  body.classList.remove("disclosure-animating");
  body.style.removeProperty("height");
  body.style.opacity = String(currentOpacity);
  body.style.marginTop = `${currentMargin}px`;
  body.hidden = false;
  const fullHeight = body.getBoundingClientRect().height;
  const fullMargin = parseFloat(getComputedStyle(body)
    .getPropertyValue("--disclosure-gap")) || 0;

  body.classList.add("disclosure-animating");
  body.style.height = `${currentHeight}px`;
  body.style.opacity = String(currentOpacity);
  body.style.marginTop = `${currentMargin}px`;
  void body.offsetHeight; // commit the starting geometry before setting the target
  body.style.height = collapsed ? "0px" : `${fullHeight}px`;
  body.style.opacity = collapsed ? "0" : "1";
  body.style.marginTop = collapsed ? "0px" : `${fullMargin}px`;

  const timer = setTimeout(() => {
    disclosureMotionTimers.delete(body);
    body.classList.remove("disclosure-animating");
    body.style.removeProperty("height");
    body.style.removeProperty("opacity");
    body.style.removeProperty("margin-top");
    body.hidden = collapsed;
  }, SLIDE_MOTION_MS + 40);
  disclosureMotionTimers.set(body, timer);
}

function disclosureButton(label, body, collapsedKeys, storageKey, itemKey) {
  const key = String(itemKey);
  const button = el("button", "disclosure-toggle");
  button.type = "button";
  body.id = `disclosure-panel-${++disclosureSeq}`;
  button.setAttribute("aria-controls", body.id);
  button.appendChild(choiceSvg("arrow"));

  const sync = (animate = false) => {
    const isCollapsed = collapsedKeys.has(key);
    button.setAttribute("aria-expanded", isCollapsed ? "false" : "true");
    const action = isCollapsed ? "Expand" : "Collapse";
    button.setAttribute("aria-label", `${action} ${label}`);
    setDisclosureCollapsed(body, isCollapsed, animate);
  };
  button.onclick = () => {
    if (collapsedKeys.has(key)) collapsedKeys.delete(key);
    else collapsedKeys.add(key);
    saveStringSet(storageKey, collapsedKeys);
    sync(true);
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

/* A compact backend label or status line is an explicit one-click disclosure
   target on every input device. Leave its real disclosure button alone: that
   button already owns its click, and letting the delegated surface handle it
   too would immediately undo the toggle. Ignore the later clicks in a desktop
   double-click sequence so a hurried activation cannot undo itself either. */
function wireDisclosureSurface(target, disclosure) {
  target.addEventListener("click", event => {
    if (event.target === disclosure ||
        (disclosure.contains && disclosure.contains(event.target))) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.detail > 1) return;
    disclosure.click();
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
    closeAllMenus(null);
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
  closeAllMenus(null);   // anchored floats cannot survive a reflow
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

/* Whether a session shows its head strip. A node too old to know the column
   omits the field entirely, and an omission means shown - never hidden. */
function sessionShowsMeta(session) {
  if (!session) return true;
  const value = session.show_meta;
  return value === undefined || value === null ? true : !!value;
}

/* A menu row that carries its own on/off state. The tick sits out at the right
   margin, so the label starts on the same column as every other row in the
   menu whether it is ticked or not. */
function menuCheckRow(label, on, fn) {
  const button = el("button", "menu-check" + (on ? " on" : ""));
  /* Off draws the empty box rather than nothing: a blank margin reads as a
     plain row, while an empty box says a tick belongs here and is currently
     absent - the state is legible without comparing against its neighbours. */
  const mark = el("span", "menu-check-mark");
  if (on) mark.appendChild(checkIcon(13));
  button.appendChild(el("span", "menu-check-label", label));
  button.appendChild(mark);
  button.setAttribute("role", "menuitemcheckbox");
  button.setAttribute("aria-checked", on ? "true" : "false");
  button.onclick = (event) => { event.stopPropagation(); closeAllMenus(null); fn(); };
  return button;
}

/* A linked remote workspace rides on the session as a structured descriptor.
   Everything user-facing shows its authoritative project path, never the
   node-private mirror directory the engine actually runs in. */
function sessionWorkspace(session) {
  const ws = session && session.workspace;
  return ws && typeof ws === "object" && ws.root ? ws : null;
}

function workspaceLabel(session, n = 26) {
  const ws = sessionWorkspace(session);
  if (ws) return tailPath(ws.root, n);
  if (!isScratchWorkspace(session)) return tailPath((session && session.cwd) || "", n);
  return session.workspace_missing ? "Scratch workspace expired" : "Scratch workspace";
}

function workspaceTitle(session) {
  const ws = sessionWorkspace(session);
  if (ws) return `${ws.root} · files on ${ws.node || "another backend"}`;
  if (!isScratchWorkspace(session)) return (session && session.cwd) || "";
  if (session.workspace_missing)
    return "The host cleared this scratch workspace. It will be recreated before the next turn.";
  return `Disposable scratch workspace · ${session.cwd}`;
}

function sessionDeleteMessage(session) {
  const ws = sessionWorkspace(session);
  if (ws) {
    return "The transcript and this backend's private synchronized copy are removed permanently." +
      (session.ws_dirty ? " Changes not yet synced to the project are lost." : "") +
      ` The project on ${ws.node || "the workspace backend"} is not touched.`;
  }
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

function engineReady(engine) {
  return !!engine && !!engine.installed &&
    (engine.availability_only === true || engine.auth === "ok");
}

function engineStatusText(engine) {
  if (!engine || !engine.installed)
    return engine && engine.availability_only === true ? "No binary" : "Missing";
  if (engine.availability_only === true) return "Ready";
  return engine.auth === "ok" ? "Ready" : "No auth";
}

function effortOptionsForModel(engine, model) {
  const match = ((engine && engine.model_options) || [])
    .find(option => option && option.value === model);
  if (match && Array.isArray(match.effort_options)) return match.effort_options;
  return [...((engine && engine.effort_options) || [])];
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

/* The same problem one step worse for images: a Markdown image whose target is
   a path on the machine the engine is running on cannot be fetched by this
   page, and an <img> that fails leaves a broken-image glyph and the raw alt
   text in the transcript. Non-web targets are never requested at all - the
   reference is shown as the named card an attachment already falls back to, so
   it stays legible without pretending it can be displayed. Genuine web images
   still load, and only fall back if the load actually fails. */
function decorateMarkdownImages(root) {
  root.querySelectorAll("img").forEach(img => {
    const src = img.getAttribute("src") || "";
    let protocol = "";
    // no base, exactly as decorateMarkdownLinks: only an absolute web-ish
    // target counts, so a bare path is recognised rather than resolved
    // against this origin and fetched as a bogus same-origin request
    try { protocol = new URL(src).protocol; } catch (error) { /* not a URL */ }
    const loadable = protocol === "http:" || protocol === "https:" ||
                     protocol === "data:" || protocol === "blob:";
    const asCard = () => {
      const name = (img.getAttribute("alt") || "").trim() || baseName(src) || "image";
      const card = el("span", "attach-chip file md-img");
      const icon = el("span", "attach-file-icon");
      icon.appendChild(attachmentFileIcon());
      const copy = el("span", "attach-file-copy");
      copy.appendChild(el("span", "attach-file-name", name));
      copy.appendChild(el("span", "attach-file-size", src || "No image source"));
      card.appendChild(icon);
      card.appendChild(copy);
      if (src) card.title = src;
      img.replaceWith(card);
    };
    if (!loadable) { asCard(); return; }
    img.onerror = asCard;
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
  "service go cargo rustc java javac gradle mvn dotnet terraform tofu ansible apt dnf yum brew"
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
      } catch (err) { toast("Copy failed", "error"); }
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
  version: "",             // this instance's own puppy version
  engines: [],            // local engines info
  engMap: {},             // key -> engine info (local)
  usageRefresh: null,     // local account-usage refresh metadata
  autoUpgrade: null,      // this instance's engine-update schedule
  uploadSettings: null,   // local per-file upload policy
  localEngineCheckedAt: 0,
  backends: [],           // remote backends [{id,name,url,urls,active_url}]
  workspaceLinks: [],     // controller-brokered remote-workspace links
  sessions: [],           // local sessions (live via updates ws)
  remoteSessions: {},     // bid -> sessions[]
  remoteOk: {},           // bid -> bool
  remoteErrors: {},       // bid -> latest reachability error
  remoteStopping: {},     // bid -> graceful node lifecycle notice
  engCache: {},           // bid -> engines[]
  nodeUsers: {},          // bid -> account the node's puppy process runs as
  notify: { configured: false, enabled: false },   // completion-alert bell
  browser: { enabled: false },  // this instance's managed-browser toggle
  remoteBrowser: {},      // bid -> {enabled} from that node's ping metadata
  remoteEngineErrors: {}, // bid -> latest engine-status error (node can still be reachable)
  remoteEngineCheckedAt: {},
  remoteNodeCheckedAt: {},
  remoteUsageRefresh: {}, // bid -> account-usage refresh metadata
  remoteAutoUpgrade: {},  // bid -> unattended engine-update schedule
  remoteUploadSettings: {}, // bid -> remote per-file upload policy
  remoteSystemPrompts: {}, // bid -> last authenticated node prompt settings
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
const remoteUpdateConnections = new Map();

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
      if (session.completion_status !== "interrupted")
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
                        state.remoteStopping,
                        state.engCache, state.remoteEngineErrors,
                        state.remoteEngineCheckedAt, state.remoteNodeCheckedAt,
                        state.remoteUsageRefresh, state.remoteAutoUpgrade,
                        state.remoteUploadSettings, state.remoteSystemPrompts,
                        state.remoteBrowser, state.nodeUsers,
                        remotePollSequence]) {
    for (const id of Object.keys(bucket)) if (!live.has(String(id))) delete bucket[id];
  }
  for (const key of sessionActivityAnchors.keys()) {
    const bid = key.slice(0, key.indexOf(":"));
    if (bid !== "0" && !live.has(bid)) sessionActivityAnchors.delete(key);
  }
  let becameOnline = false;
  for (const backend of state.backends) {
    const health = controllerBackendHealth(backend);
    if (!health) continue; // compatibility with controllers predating this field
    const bid = Number(backend.id);
    const previous = state.remoteOk[bid];
    if (health.state === "online") {
      const stopping = state.remoteStopping[bid];
      if (stopping && !stopping.controllerOfflineSeen) {
        state.remoteOk[bid] = false;
        state.remoteErrors[bid] = stopping.message;
        continue;
      }
      state.remoteOk[bid] = true;
      delete state.remoteErrors[bid];
      if (stopping) clearRemoteNodeStopping(bid);
      if (previous !== true) becameOnline = true;
    } else if (health.state === "offline") {
      if (state.remoteStopping[bid])
        state.remoteStopping[bid].controllerOfflineSeen = true;
      state.remoteOk[bid] = false;
      state.remoteErrors[bid] = health.reason || "Backend unavailable";
    } else {
      delete state.remoteOk[bid];
      delete state.remoteErrors[bid];
    }
  }
  syncRemoteUpdateConnections();
  return becameOnline;
}

/* A backend ID survives a connection edit, but everything learned through its
   old URL/token/pin does not. Keep the open workspace tabs so a moved backend
   can reconnect in place; clear only remote observations and stale poll work. */
function resetRemoteBackendConnection(bid) {
  bid = Number(bid) || 0;
  if (!bid) return;
  closeRemoteUpdateConnection(bid);
  remotePollSequence[bid] = Number(remotePollSequence[bid] || 0) + 1;
  for (const bucket of [state.remoteSessions, state.remoteOk, state.remoteErrors,
                        state.remoteStopping,
                        state.engCache, state.remoteEngineErrors,
                        state.remoteEngineCheckedAt, state.remoteNodeCheckedAt,
                        state.remoteUsageRefresh, state.remoteAutoUpgrade,
                        state.remoteUploadSettings, state.remoteSystemPrompts,
                        state.remoteBrowser, state.nodeUsers]) {
    delete bucket[bid];
  }
  for (const key of [...sessionActivityAnchors.keys()])
    if (key.startsWith(`${bid}:`)) sessionActivityAnchors.delete(key);
}

/* The controller persists one exact-version observation cache per backend.
   Hydrate it before availability is reconciled so a page opened while a node
   is already offline still has the values that node last authenticated. */
function hydrateBackendLastKnown(backends) {
  for (const backend of backends || []) {
    const bid = Number(backend && backend.id) || 0;
    const known = backend && backend.last_known;
    if (!bid || !known || typeof known !== "object" || known.version !== 1) continue;
    if (Array.isArray(known.engines)) state.engCache[bid] = known.engines;
    if (known.usage_refresh && typeof known.usage_refresh === "object")
      state.remoteUsageRefresh[bid] = known.usage_refresh;
    if (known.auto_upgrade && typeof known.auto_upgrade === "object")
      state.remoteAutoUpgrade[bid] = known.auto_upgrade;
    const uploads = normalizeUploadSettings(known.uploads);
    if (uploads) state.remoteUploadSettings[bid] = uploads;
    if (known.browser && typeof known.browser.enabled === "boolean")
      state.remoteBrowser[bid] = { enabled: known.browser.enabled };
    if (known.system_prompt && typeof known.system_prompt === "object")
      state.remoteSystemPrompts[bid] = known.system_prompt;
  }
}

function controllerBackendHealth(backend) {
  const health = backend && backend.availability;
  if (!health || typeof health !== "object" ||
      !["checking", "online", "offline"].includes(health.state)) return null;
  return health;
}

/* New controllers own reachability discovery. Older controllers have no
   availability field, so retain the prior browser-probe behavior for them. */
function backendPoolable(backendOrBid) {
  if (!backendOrBid) return true;
  const backend = typeof backendOrBid === "object" ? backendOrBid :
    state.backends.find(item => Number(item.id) === Number(backendOrBid));
  if (!backend) return false;
  const health = controllerBackendHealth(backend);
  return !health || health.state === "online";
}

function backendConnectionAllowed(bid) {
  if (!bid) return true;
  return backendPoolable(bid) && state.remoteOk[bid] !== false;
}

function remoteAvailability(bid) {
  return state.remoteOk[bid] === false ? "bad" :
    state.remoteOk[bid] === true ? "ok" : "pending";
}

function remoteAvailabilityTitle(bid) {
  if (state.remoteStopping[bid]) return state.remoteStopping[bid].message;
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
   stale tabs disappear, duplicate membership is ignored, and empty branches
   collapse. */
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
        browserId: t.browserId, terminalId: t.terminalId,
        cmd: t.cmd, cwd: t.cwd, ended: t.ended === true,
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
    if (item.type === "term" && typeof item.terminalId === "string" &&
        /^[A-Z0-9]{4}$/.test(item.terminalId.toUpperCase()))
      tab.terminalId = item.terminalId.toUpperCase();
    if (typeof item.title === "string") tab.title = item.title.slice(0, 1000);
    if (typeof item.cmd === "string") tab.cmd = item.cmd.slice(0, 10000);
    if (typeof item.cwd === "string") tab.cwd = item.cwd.slice(0, 4096);
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
    if (d && d.version === 2 && Array.isArray(d.tabs) && d.layout &&
        typeof d.layout === "object" && !Array.isArray(d.layout)) {
      state.tabs = storedTabs(d.tabs);
      state.active = typeof d.active === "string" ? d.active : null;
      state.activeGroup = typeof d.activeGroup === "string" ? d.activeGroup : null;
      state.layout = d.layout;
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
    /* Instance-capable nodes require a logical Browser ID. Unidentified tabs
       are valid only for remote nodes that expose the singleton route. */
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
  if (typeof s.version === "string") state.version = s.version;
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
  hydrateBackendLastKnown(state.backends);
  state.workspaceLinks = Array.isArray(s.workspace_links) ? s.workspace_links : [];
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
        hydrateBackendLastKnown(state.backends);
        const becameOnline = reconcileRemoteState();
        renderSidebar();
        syncRemoteStateViews();
        if (becameOnline)
          pollRemotes({ forceEngines: true })
            .catch(error => console.warn("backend recovery poll failed", error));
      } else if (d.type === "host_metrics") {
        renderHostCpu(d.cpu_percent);
      } else if (d.type === "notify") {
        state.notify = { configured: !!d.configured, enabled: !!d.enabled };
        syncBell();
      } else if (d.type === "browser") {
        state.browser = { enabled: !!d.enabled };
        if (d.enabled === false) closeBrowserTabsForBackend(0);
        renderSidebar();
      } else if (d.type === "browser_activity") {
        handleBrowserActivity(0, d.session_id, d.turn_id, d.browser_id);
      } else if (d.type === "terminal_activity") {
        handleTerminalActivity(0, d.session_id, d.turn_id, d.terminal_id);
      } else if (d.type === "workspace_links" && Array.isArray(d.links)) {
        state.workspaceLinks = d.links;
        renderSidebar();
        refreshWorkspaceChips();
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

function remoteStoppingMessage(bid) {
  const notice = state.remoteStopping[Number(bid) || 0];
  return notice && notice.message ? notice.message : "";
}

/* A graceful headless-node stop is different from discovering an outage on
   the next poll: it is authoritative now. Retire every cached running marker
   without firing a false "completed" notification, and let each open view
   replace its live controls with the same lifecycle wording. */
function handleRemoteNodeStopping(rawBid, notice = {}) {
  const backend = state.backends.find(item => String(item.id) === String(rawBid));
  if (!backend) return;
  const bid = backend.id;
  const restarting = notice.reason === "restart";
  const message = restarting ? "Backend restarting…" : "Backend shutting down…";
  remotePollSequence[bid] = Number(remotePollSequence[bid] || 0) + 1;
  const health = controllerBackendHealth(backend);
  state.remoteStopping[bid] = {
    reason: restarting ? "restart" : "shutdown", message,
    controllerOfflineSeen: !!health && health.state === "offline",
  };
  state.remoteOk[bid] = false;
  state.remoteErrors[bid] = message;
  if (Array.isArray(state.remoteSessions[bid])) {
    for (const session of state.remoteSessions[bid]) {
      session.status = "idle";
      session.active_since = null;
    }
  }
  for (const key of [...sessionActivityAnchors.keys()])
    if (key.startsWith(`${bid}:`)) sessionActivityAnchors.delete(key);
  for (const view of Object.values(state.views)) {
    if (!view || !view.tab || Number(view.tab.bid || 0) !== Number(bid)) continue;
    if (typeof view.handleNodeStopping === "function") view.handleNodeStopping(message);
  }
  renderTabs();
  syncRemoteStateViews();
}

function clearRemoteNodeStopping(rawBid) {
  const bid = Number(rawBid) || 0;
  if (!bid || !state.remoteStopping[bid]) return;
  delete state.remoteStopping[bid];
  delete state.remoteErrors[bid];
  for (const view of Object.values(state.views)) {
    if (!view || !view.tab || Number(view.tab.bid || 0) !== bid) continue;
    if (typeof view.clearNodeStopping === "function") view.clearNodeStopping();
  }
}

function closeRemoteUpdateConnection(rawBid) {
  const bid = Number(rawBid) || 0;
  const entry = remoteUpdateConnections.get(bid);
  if (!entry) return;
  remoteUpdateConnections.delete(bid);
  entry.sequence++;
  if (entry.timer !== null) clearTimeout(entry.timer);
  entry.timer = null;
  if (entry.ws) try { entry.ws.close(); } catch (error) {}
  entry.ws = null;
}

function connectRemoteUpdates(backend) {
  const bid = Number(backend && backend.id) || 0;
  if (!bid || !state.authed || !backendSupportsShutdownNotice(backend) ||
      !backendConnectionAllowed(bid)) return;
  let entry = remoteUpdateConnections.get(bid);
  if (!entry) {
    entry = { ws: null, timer: null, retry: 800, sequence: 0, sawStopping: false };
    remoteUpdateConnections.set(bid, entry);
  }
  if (entry.ws && (entry.ws.readyState === 0 || entry.ws.readyState === 1)) return;
  if (entry.timer !== null) {
    clearTimeout(entry.timer);
    entry.timer = null;
  }
  const sequence = ++entry.sequence;
  const ws = new WebSocket(wsUrl(bid, "ws/updates"));
  entry.ws = ws;
  const current = () => remoteUpdateConnections.get(bid) === entry &&
    entry.sequence === sequence && entry.ws === ws;
  ws.onopen = () => {
    if (!current()) { try { ws.close(); } catch (error) {} return; }
    entry.retry = 800;
    if (entry.sawStopping) {
      entry.sawStopping = false;
      clearRemoteNodeStopping(bid);
    }
    noteRemoteSocketReachable(bid, true);
  };
  ws.onmessage = event => {
    if (!current()) return;
    let message;
    try { message = JSON.parse(event.data); } catch (error) { return; }
    if (message.type !== "node_stopping") return;
    entry.sawStopping = true;
    handleRemoteNodeStopping(bid, message);
  };
  ws.onclose = () => {
    if (!current()) return;
    entry.ws = null;
    const live = state.backends.find(item => item.id === bid);
    if (!state.authed || !backendSupportsShutdownNotice(live) ||
        !backendConnectionAllowed(bid)) return;
    const delay = Math.round(entry.retry * (.85 + Math.random() * .3));
    entry.retry = Math.min(entry.retry * 1.7, 15000);
    entry.timer = setTimeout(() => {
      entry.timer = null;
      connectRemoteUpdates(live);
    }, delay);
  };
  ws.onerror = () => { try { ws.close(); } catch (error) {} };
}

function syncRemoteUpdateConnections() {
  const desired = new Map(state.backends
    .filter(backend => backendSupportsShutdownNotice(backend) &&
      backendPoolable(backend) &&
      state.remoteOk[Number(backend.id)] === true)
    .map(backend => [Number(backend.id), backend]));
  for (const bid of [...remoteUpdateConnections.keys()])
    if (!desired.has(bid)) closeRemoteUpdateConnection(bid);
  if (!state.authed) return;
  for (const backend of desired.values()) connectRemoteUpdates(backend);
}

function wakeRemoteUpdateConnections() {
  syncRemoteUpdateConnections();
  for (const [bid, entry] of remoteUpdateConnections) {
    if (entry.ws && (entry.ws.readyState === 0 || entry.ws.readyState === 1)) continue;
    if (entry.timer !== null) clearTimeout(entry.timer);
    entry.timer = null;
    entry.retry = 800;
    const backend = state.backends.find(item => item.id === bid);
    if (backend) connectRemoteUpdates(backend);
  }
}

function stopRemoteUpdateConnections() {
  for (const bid of [...remoteUpdateConnections.keys()]) closeRemoteUpdateConnection(bid);
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
  if (!backendConnectionAllowed(bid)) return;
  const sequence = (remotePollSequence[bid] || 0) + 1;
  remotePollSequence[bid] = sequence;
  const wasReachable = state.remoteOk[bid] === true;
  let payload = null;
  let failure = null;

  try {
    payload = await api(bid, "sessions", { timeoutMs: REMOTE_POLL_TIMEOUT });
    if (!payload || !Array.isArray(payload.sessions))
      throw new Error("backend returned an invalid sessions response");
  } catch (error) {
    failure = error;
  }
  if (!remotePollIsCurrent(bid, sequence)) return;
  if (failure) {
    const current = state.backends.find(item => Number(item.id) === Number(bid));
    const reported = failure.data && failure.data.availability;
    if (controllerBackendHealth(reported ? { availability: reported } : null) && current)
      current.availability = reported;
    if (controllerBackendHealth(current)) {
      /* The controller verdict owns reachability. In particular, a browser's
         own five-second abort must not strand this node locally when the
         controller completed a healthy request just after that deadline. */
      reconcileRemoteState();
    } else {
      state.remoteOk[bid] = false; // older controller compatibility
      state.remoteErrors[bid] = remoteStoppingMessage(bid) ||
        failure.message || "Backend unavailable";
    }
    return;
  }

  /* The old process can finish an already accepted HTTP request during its
     grace window. Only a fresh node descriptor saying it is no longer
     draining clears an explicit lifecycle notice; ordinary traffic from the
     departing process must not paint it green again. */
  if (state.remoteStopping[bid]) {
    try {
      const node = await api(bid, "node", { timeoutMs: REMOTE_POLL_TIMEOUT });
      if (!remotePollIsCurrent(bid, sequence)) return;
      if (!node || node.shutting_down !== false) return;
      clearRemoteNodeStopping(bid);
    } catch (error) {
      return;
    }
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
        throw new Error("backend returned invalid metadata");
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
        if (node.browser && node.browser.enabled === false)
          closeBrowserTabsForBackend(bid);
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
    rememberEnginePayload(bid, engines);
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
  /* Publish each node as soon as its own request settles. One sleeping remote
     must not hold healthy session lists (or local engine status) behind its
     connect timeout during startup. The final Promise still keeps the normal
     polling interval measured from the end of the whole pass. */
  const publish = () => {
    reconcileRemoteState();
    syncRemoteStateViews();
  };
  await Promise.all([
    pollLocalEngines(forceEngines).finally(publish),
    ...backends.map(backend =>
      pollRemoteBackend(backend, forceEngines).finally(publish)),
  ]);
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
  stopRemoteUpdateConnections();
}

/* With the controller proxy's upstream-first handshake, a remote socket open
   or message is direct proof that the backend is reachable. Let that newer
   evidence invalidate an older in-flight HTTP failure and refresh the lists. */
function noteRemoteSocketReachable(bid, recoveredFromStopping = false) {
  if (!bid) return;
  const backend = state.backends.find(item => String(item.id) === String(bid));
  if (!backend) return;
  bid = backend.id;
  if (state.remoteStopping[bid] && !recoveredFromStopping) return;
  if (recoveredFromStopping) clearRemoteNodeStopping(bid);
  if (state.remoteOk[bid] === true) return;
  remotePollSequence[bid] = (remotePollSequence[bid] || 0) + 1;
  state.remoteOk[bid] = true;
  delete state.remoteErrors[bid];
  syncRemoteStateViews();
  pollRemotes({ forceEngines: true })
    .catch(error => console.warn("socket recovery poll failed", error));
}

/* Mobile browsers freeze timers while the page is backgrounded. If a socket
   closed during that freeze, do not leave its accumulated backoff sitting
   between the user and a fresh snapshot after the page becomes usable again. */
function wakeSessionConnections() {
  for (const view of Object.values(state.views))
    if (view && typeof view.resumeConnection === "function") view.resumeConnection();
}

window.addEventListener("online", () => {
  if (state.authed) {
    connectUpdates();
    wakeSessionConnections();
    wakeRemoteUpdateConnections();
    pollRemotes({ forceEngines: true })
      .catch(error => console.warn("online remote poll failed", error));
  }
});
document.addEventListener("visibilitychange", () => {
  if (state.authed && document.visibilityState === "visible") {
    connectUpdates();
    wakeSessionConnections();
    wakeRemoteUpdateConnections();
    pollRemotes({ forceEngines: true })
      .catch(error => console.warn("resume remote poll failed", error));
  }
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

function terminalTabTitle(tabOrBid, terminalId = "") {
  const tab = tabOrBid && typeof tabOrBid === "object" ? tabOrBid : null;
  const bid = tab ? (tab.bid || 0) : (Number(tabOrBid) || 0);
  const id = String(tab ? (tab.terminalId || "") : terminalId).toUpperCase();
  return id ? `Terminal ${id} @ ${backendName(bid)}` : `Terminal @ ${backendName(bid)}`;
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
    toast(`${name}: This backend does not offer a managed browser`, "error", 7000);
    return;
  }
  try {
    const result = await api(bid, "browser/enabled",
      { method: "POST", body: { enabled: true } });
    state.remoteBrowser[bid] = { enabled: !!result.enabled };
    toast(`${name}: Browser enabled`, "ok");
  } catch (error) {
    toast(`${name}: Browser not enabled · ${error.message}`, "error", 8000);
  }
}

function browserInstancesFor(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  return !!backend && backendHasCapability(backend, "browser-instances");
}

function browserHandoffFor(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  return !!backend && Number(backend.protocol || 0) > 0 &&
    Array.isArray(backend.capabilities) && backend.capabilities.includes("browser-handoff");
}

function terminalInstancesFor(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  /* Unlike the long-established anonymous terminal socket, identified PTYs
     are additive. A protocol-0 full node has no capability list, so treating
     every feature as present would make an older node receive unknown POST and
     ID-scoped WebSocket routes instead of the compatible legacy connection. */
  return !!backend && Number(backend.protocol || 0) > 0 &&
    Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("terminal-instances");
}

function terminalHandoffFor(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  return !!backend && Number(backend.protocol || 0) > 0 &&
    Array.isArray(backend.capabilities) && backend.capabilities.includes("terminal-handoff");
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
      throw new Error("backend returned an invalid browser ID");
    openBrowserTab(bid, browserId, groupId);
  } catch (error) {
    toast(`${backendName(bid)}: ${error.message || "Could not open browser"}`,
      "error", 7000);
  }
}

function configuredBackendUrls(backend) {
  const source = Array.isArray(backend && backend.urls) ? backend.urls :
    [backend && backend.url];
  return [...new Set(source.map(value => String(value || "").trim()).filter(Boolean))];
}

function activeBackendUrl(backend) {
  const urls = configuredBackendUrls(backend);
  const active = String(backend && backend.active_url || "");
  return active && urls.includes(active) ? active : (urls[0] || "");
}

function backendLocationVersion(backend) {
  const urls = configuredBackendUrls(backend);
  const location = state.remoteOk[backend.id] === true ? activeBackendUrl(backend) :
    urls.join(" / ");
  return [location, backend.remote_version ? `v${backend.remote_version}` : ""]
    .filter(Boolean).join(" · ");
}

/* A disconnected node has no authoritative active address. Keep both layers
   in the same measured box and cross-fade their text so the row never shifts;
   a successful proxy request supplies active_url and collapses it to one. */
function syncBackendLocation(root, backend) {
  if (root._backendUrlTimer) clearTimeout(root._backendUrlTimer);
  root._backendUrlTimer = null;
  const configured = configuredBackendUrls(backend);
  const connected = state.remoteOk[backend.id] === true;
  const urls = connected ? [activeBackendUrl(backend)] : configured;
  let values = urls.filter(Boolean);
  if (!connected && values.length > 1 && typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches)
    values = [values.join(" / ")];
  const version = backend.remote_version ? `v${backend.remote_version}` : "";
  const allLabel = [(connected ? values[0] : values.join(" or ")) || "No URL", version]
    .filter(Boolean).join(" · ");
  root.setAttribute("aria-label", allLabel);
  root.classList.toggle("cycling", values.length > 1);
  const track = root.querySelector(".be-url-track");
  const layers = track.querySelectorAll(".be-url-layer");
  root.querySelector(".be-url-version").textContent = version;
  let index = Math.max(0, values.indexOf(root.dataset.currentUrl || ""));
  root.dataset.currentUrl = values[index] || "";
  track.dataset.front = "0";
  layers[0].textContent = values[index] || "";
  layers[1].textContent = values[(index + 1) % Math.max(1, values.length)] || "";
  if (values.length < 2) return;
  let front = 0;
  const cycle = () => {
    if (!root.isConnected || state.remoteOk[backend.id] === true) return;
    index = (index + 1) % values.length;
    const next = 1 - front;
    layers[next].textContent = values[index];
    track.dataset.front = String(next);
    root.dataset.currentUrl = values[index];
    front = next;
    root._backendUrlTimer = setTimeout(cycle, 3400);
  };
  root._backendUrlTimer = setTimeout(cycle, 3400);
}

function backendLocationNode(backend) {
  const root = el("span", "be-url");
  root.innerHTML = `<span class="be-url-track" data-front="0" aria-hidden="true">
    <span class="be-url-layer"></span><span class="be-url-layer"></span>
  </span><span class="be-url-version" aria-hidden="true"></span>`;
  syncBackendLocation(root, backend);
  return root;
}

/* Repeatable connection origins use one stable primary row with the familiar
   square + action. Each extra row owns its own remove action, which keeps the
   control compact and unambiguous on touch screens. */
function backendUrlEditor(root, initialUrls = [""]) {
  let stored = Array.isArray(initialUrls) && initialUrls.length ? initialUrls : [""];
  const render = (focusIndex, capture = true) => {
    const previous = root.querySelectorAll("input");
    if (capture && previous.length) stored = [...previous].map(input => input.value);
    if (!stored.length) stored = [""];
    root.innerHTML = "";
    stored.forEach((value, index) => {
      const row = el("div", "backend-url-row");
      const input = document.createElement("input");
      input.type = "text";
      input.inputMode = "url";
      input.autocomplete = "off";
      input.autocapitalize = "off";
      input.spellcheck = false;
      input.placeholder = index ? "Alternate backend URL" : "http:// or https://";
      input.setAttribute("aria-label", index ? `Backend URL ${index + 1}` : "Backend URL");
      input.value = value || "";
      const action = el("button", "icon-btn backend-url-action");
      action.type = "button";
      if (index === 0) {
        action.setAttribute("aria-label", "Add another backend URL");
        action.appendChild(plusIcon(13));
        action.disabled = stored.length >= 8;
        action.onclick = () => {
          if (stored.length >= 8) return;
          stored = [...root.querySelectorAll("input")].map(field => field.value);
          stored.push("");
          render(stored.length - 1, false);
        };
      } else {
        action.setAttribute("aria-label", `Remove backend URL ${index + 1}`);
        action.appendChild(xIcon(13));
        action.onclick = () => {
          stored = [...root.querySelectorAll("input")].map(field => field.value);
          stored.splice(index, 1);
          render(Math.max(0, index - 1), false);
        };
      }
      row.appendChild(input);
      row.appendChild(action);
      root.appendChild(row);
    });
    if (Number.isInteger(focusIndex)) {
      const field = root.querySelectorAll("input")[focusIndex];
      if (field) requestAnimationFrame(() => field.isConnected && field.focus());
    }
  };
  render();
  return {
    values: () => [...root.querySelectorAll("input")]
      .map(input => input.value.trim()).filter(Boolean),
    setValues: values => {
      stored = Array.isArray(values) && values.length ? [...values] : [""];
      render(undefined, false);
    },
    setDisabled: disabled => {
      root.querySelectorAll("input,button").forEach(control => control.disabled = disabled);
      if (!disabled) {
        const add = root.querySelector(".backend-url-row:first-child button");
        if (add) add.disabled = root.querySelectorAll(".backend-url-row").length >= 8;
      }
    },
  };
}

function pairingBackendUrls(paired, fallback) {
  let pairedUrls = [];
  if (Object.prototype.hasOwnProperty.call(paired, "urls")) {
    if (!Array.isArray(paired.urls) ||
        !paired.urls.every(value => typeof value === "string"))
      throw new Error("pairing JSON urls must be a list of text values");
    pairedUrls = paired.urls.map(value => value.trim()).filter(Boolean);
  } else if (Object.prototype.hasOwnProperty.call(paired, "url")) {
    if (typeof paired.url !== "string") throw new Error("pairing JSON url must be text");
    if (paired.url.trim()) pairedUrls = [paired.url.trim()];
  }
  return [...new Set([...pairedUrls, ...(fallback || [])].filter(Boolean))];
}

function formatSessionActivity(startedAt, now = Date.now()) {
  const start = Number(startedAt);
  const total = Number.isFinite(start) ?
    Math.max(0, Math.floor((now - start) / 1000)) : 0;
  const seconds = String(total % 60).padStart(2, "0");
  const minutes = Math.floor(total / 60) % 60;
  if (total < 3600) return `${minutes}:${seconds}`;
  const hours = Math.floor(total / 3600);
  return `${hours}:${String(minutes).padStart(2, "0")}:${seconds}`;
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

function noteSessionActivity(bid, sid, running, activeSince, serverTime,
                             completionStatus = "") {
  const sessions = sessionsFor(bid);
  const session = sessions.find(item => String(item.id) === String(sid));
  const sample = session || { id: sid };
  sample.status = running ? "running" : "idle";
  sample.active_since = running ? (activeSince == null ? sample.active_since : activeSince) : null;
  sample.completion_status = running ? "" : completionStatus;
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

function backendSupportsShutdownNotice(backend) {
  return !!backend && backend.role === "backend" && Number(backend.protocol || 0) > 0 &&
    Array.isArray(backend.capabilities) && backend.capabilities.includes("shutdown-notice");
}

/* Distinct from backendSupportsAutoUpgrade below, which answers whether the
   backend *package* may be replaced over the signed contract. This one is about
   the engine CLIs on that node. */
function backendSupportsEngineAutoUpgrade(bid) {
  if (!bid) return true;
  const backend = state.backends.find(b => b.id === bid);
  return backendHasCapability(backend, "engine-auto-upgrade") &&
    backendSupportsEngineUpgrade(bid);
}

function backendSupportsUploadPreviews(bid) {
  if (!bid) return true;
  const backend = state.backends.find(b => b.id === bid);
  return backendHasCapability(backend, "upload-preview");
}

/* An upload's stored path ends in its session id, its upload id, and its own
   name. That is enough to ask the node that holds it for the bytes back, so a
   preview no longer dies with the page's blob URL. */
function uploadPreviewUrl(bid, storedPath) {
  if (!backendSupportsUploadPreviews(bid)) return "";
  const match = /\/uploads\/(\d+)\/(\d{13}-[0-9a-f]{10})\/[^/]+$/
    .exec(String(storedPath || ""));
  if (!match) return "";
  return apiPath(bid, `sessions/${match[1]}/upload/${match[2]}`);
}

function backendSupportsScratch(bid) {
  if (!bid) return true;
  const backend = state.backends.find(b => b.id === bid);
  // Protocol-0 nodes ignore unknown create fields, so they must not be offered
  // a scratch choice that would silently become a normal directory session.
  return !!backend && Number(backend.protocol || 0) > 0 &&
    Array.isArray(backend.capabilities) && backend.capabilities.includes("temporary-workspaces");
}

/* Remote-workspace roles are two independent additive capabilities: a node
   can host linked sessions (mirror) and/or share its directories (provider).
   Never inferred for protocol-0 nodes. */
function backendSupportsWorkspaceMirror(bid) {
  if (!bid) return true;
  const backend = state.backends.find(b => b.id === bid);
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("workspace-mirror");
}

function backendSupportsWorkspaceProvider(bid) {
  if (!bid) return true;
  const backend = state.backends.find(b => b.id === bid);
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("workspace-provider");
}

/* The controller-side link record behind one linked session, if this console
   is the controller that brokered it. */
function linkForSession(bid, sid) {
  return (state.workspaceLinks || []).find(l =>
    l.exec_backend === (bid || 0) && l.session_id === sid) || null;
}

function refreshWorkspaceChips() {
  for (const view of Object.values(state.views)) {
    if (view && view.session && typeof view.updateHead === "function")
      view.updateHead();
  }
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

/* Forced version re-checks and engine CLI upgrades ship as one additive route
   pair, so one capability gates both controls. Never inferred for older nodes:
   a hidden control is better than a button their router would reject. */
function backendSupportsEngineUpgrade(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("engine-upgrade");
}

function backendSupportsSystemPrompt(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  /* This route is new and node-owned. Never infer it for legacy peers: an
     explicit capability prevents a prompt from appearing saved when that node
     could not possibly use it. */
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("system-prompt");
}

function backendSupportsFileUploads(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("file-uploads");
}

function backendSupportsQueuePause(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  /* Pausing adds a session-socket operation. Do not infer it for legacy or
     older remote nodes: their ordinary message queue remains fully usable,
     just without a control they cannot honor. */
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("queue-pause");
}

function backendSupportsQueueEdit(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("queue-edit");
}

function backendSupportsQueueReorder(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("queue-reorder");
}

function backendSupportsQueuedEngineSwitch(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  /* Older nodes refuse a mid-turn switch with 409, so the modal keeps its
     original wording there and the 409 surfaces as the usual error toast. */
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("queued-engine-switch");
}

function backendSupportsSessionDrafts(bid) {
  if (!bid) return true;
  const backend = state.backends.find(item => item.id === bid);
  /* Draft persistence adds messages and snapshot state to the session socket.
     Never send them to an older remote node based on protocol inference. */
  return !!backend && Array.isArray(backend.capabilities) &&
    backend.capabilities.includes("session-drafts");
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
  syncSessionBrowserChips();
}

function renderSidebar() {
  if (dragSess && dragSess.item && dragSess.item.isConnected) {
    dragSess.renderPending = true;
    return;
  }
  if (dragNode && dragNode.item && dragNode.item.isConnected &&
      dragNode.container === $("sess-groups")) {
    dragNode.renderPending = true;
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
  sortNodeGroups(groups);
  wireNodeGroupDropZone(root);
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
      wireDisclosureSurface(name, disclosure);
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
      group.dataset.nodeKey = key;
      wireNodeGroupDrag(group, t, key, ".sess-group");
    }
    const allSessions = sessionsFor(g.bid);
    const list = allSessions.filter(s => state.showArchived || !s.archived);
    if (!list.length) {
      let message = "No sessions yet";
      if (!g.ok) message = remoteStoppingMessage(g.bid) || "Backend unavailable";
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
      r1.appendChild(el("div", "si-name", s.name || `Session ${s.id}`));
      const activity = el("span", "si-be");
      if (s.status === "running") {
        const key = sessionActivityKey(g.bid, s.id);
        if (!sessionActivityAnchors.has(key))
          ingestOneSessionActivity(g.bid, s, null, Date.now());
        activity.classList.add("active-time");
        activity.dataset.activityKey = key;
        /* Match both halves of the running indicator to this session's colour:
           the status ring at the left and the clock ring/text at the right. */
        activity.style.color = s.color || "var(--txt3)";
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
      if (sessionWorkspace(s)) {
        const wsLink = linkForSession(g.bid, s.id);
        const st = wsLink ? wsLink.state : (s.ws_dirty ? "pending" : "");
        const mark = el("span", "si-ws" +
          (st === "conflict" || st === "pending" ? " warn" :
           st === "error" ? " err" :
           st === "syncing" || st === "init" ? " busy" : ""), "⇄");
        mark.setAttribute("aria-label",
          "Linked workspace" + (st ? " · " + st : ""));
        r2.appendChild(mark);
      }
      item.appendChild(r1); item.appendChild(r2);
      const pointerForClick = activationPointer(item);
      item.onclick = event => {
        const pointerType = pointerForClick(event);
        selectSidebarSession(g.bid, s.id);
        /* Keyboard activation has no click count, so it follows touch and opens
           immediately. A mouse opens on each completed double-click pair. */
        if (pointerType === "touch" || event.detail === 0 ||
            (event.detail > 0 && event.detail % 2 === 0)) {
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
  tog.textContent = (state.showArchived ? "Hide archived" : "Show archived") + ` (${archTotal})`;
  tog.classList.toggle("hidden", archTotal === 0);
  renderFootEngines();
}

function sessDot(s) {
  const dot = el("span", "sess-dot" + (s.status === "running" ? " running" : ""));
  dot.style.color = s.color || "var(--txt3)";   // fill and spinner both ride currentColor
  return dot;
}

const PROVIDERS = { claude: "anthropic", codex: "openai", opencode: "opencode" };
const PROVIDER_MARKS = { opencode: "OC" };
const PROVIDER_LABELS = { opencode: "OpenCode" };

function provSpec(engine) {
  const provider = PROVIDERS[engine] || "";
  return {
    className: provider ? `prov-${provider}` : "",
    text: PROVIDER_MARKS[provider] || (provider ? "" : (engine || "?")[0].toUpperCase()),
    label: PROVIDER_LABELS[provider] || provider || engine || "unknown engine",
  };
}

function provIcon(engine) {
  const spec = provSpec(engine);
  const n = el("span", "prov" + (spec.className ? " " + spec.className : ""), spec.text);
  n.setAttribute("role", "img");
  n.setAttribute("aria-label", spec.label);
  return n;
}

/* Twenty swatches fit one line in a wide modal and wrap in a narrow one. Left
   to plain wrapping they give a full first line and a short stub; instead the
   rows are evened out and spread edge to edge, so the block reads as a grid.
   Only when it wraps: a single line keeps its natural left-aligned run. */
function layoutSwatchRow(box) {
  if (!box) return;
  box.style.display = "";
  box.style.gridTemplateColumns = "";
  box.style.justifyContent = "";
  const count = box.children.length;
  if (!count) return;
  const style = getComputedStyle(box);
  const gap = parseFloat(style.columnGap) || 0;
  const width = box.clientWidth - parseFloat(style.paddingLeft) -
    parseFloat(style.paddingRight);
  const item = box.firstElementChild.getBoundingClientRect().width;
  if (!(width > 0) || !(item > 0)) return;   // not laid out yet: leave it be
  const perLine = Math.max(1, Math.floor((width + gap) / (item + gap)));
  if (perLine >= count) return;              // one line already
  const rows = Math.ceil(count / perLine);
  box.style.display = "grid";
  box.style.gridTemplateColumns = "repeat(" + Math.ceil(count / rows) + ", auto)";
  box.style.justifyContent = "space-between";
}

/* right-click menu on sidebar sessions - mirrors the open-view ⋮ menu */
function ctxMenuAt(x, y) {
  closeAllMenus(null);
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
  /* Also here, not only in the head's own menu: hiding the head takes its ⋮
     with it, and this is where the setting stays reachable afterwards. */
  menu.appendChild(menuCheckRow("Show status bar", sessionShowsMeta(s),
    () => patch({ show_meta: !sessionShowsMeta(s) })));
  menu.appendChild(el("div", "menu-sep"));
  const sessionWs = sessionWorkspace(s);
  add(isScratchWorkspace(s) ? "Copy workspace path" :
      sessionWs ? "Copy project path" : "Copy cwd",
      () => copyWithToast(sessionWs ? sessionWs.root : s.cwd));
  if (sessionWs) {
    add("Workspace details", () => modalWorkspaceLink(bid, s));
    const wsLink = linkForSession(bid, s.id);
    if (wsLink) add(`Terminal on ${wsLink.ws_name || "workspace backend"}`,
      () => openTermTab(wsLink.ws_backend, "", null, wsLink.root));
  }
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
      toast(s.workspace_missing ? "Scratch workspace recreated" : "Scratch workspace reset");
    } catch (e) { toast(e.message, "error"); }
  });
  menu.appendChild(el("div", "menu-sep"));
  add(s.archived ? "Unarchive" : "Archive", () => patch({ archived: !s.archived }));
  add("Delete session", async () => {
    const ok = await modalConfirm("Delete session?", sessionDeleteMessage(s));
    if (!ok) return;
    try {
      /* a linked session cascades through its controller-side link so the
         lease and sync records go with it */
      const wsLink = sessionWorkspace(s) && linkForSession(bid, s.id);
      if (wsLink) await api(0, `workspaces/${wsLink.id}?with_session=1`,
        { method: "DELETE" });
      else await api(bid, `sessions/${s.id}`, { method: "DELETE" });
      closeTab(`s:${bid}:${s.id}`);
      refreshGroup(bid);
      toast("Session deleted");
    } catch (e) { toast(e.message, "error"); }
  }, true);
}

/* Live sortable layouts. The DOM slot moves during dragover; FLIP animates
   every affected sibling from its old visual position to its new one. */
/* Sidebar and nested-panel slides. Kept in step with --slide-time in app.css. */
const SLIDE_MOTION_MS = 240;
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

// below this much of the weekly allowance remaining, the figure reads as a warning
const QUOTA_LOW_PERCENT = 33;

/* Scale is decided by the field's NAME, never its magnitude: claude's
   `utilization` is a 0..1 fraction, codex's `used_percent` is 0..100. Guessing
   by size once showed "99% wk" for a week that was 89% consumed. */
function weeklyUsedPercent(e) {
  if (e.quota && typeof e.quota.weekly_used_percent === "number")
    return e.quota.weekly_used_percent;
  const rl = e.rate_limit;
  if (!rl || typeof rl !== "object") return null;
  // the window the CLI itself calls binding, when that window is a weekly one
  if (/seven_day|weekly/i.test(rl.rateLimitType || "") &&
      typeof rl.utilization === "number")
    return rl.utilization * 100;
  const windows = rl.unifiedWindows;
  if (windows && typeof windows === "object")
    for (const name of ["seven_day", "seven_day_overage_included"]) {
      const w = windows[name];
      if (w && typeof w.utilization === "number") return w.utilization * 100;
    }
  for (const w of [rl.primary, rl.secondary])
    if (w && w.window_minutes === 10080 && typeof w.used_percent === "number")
      return w.used_percent;
  return null;
}

function weeklyQuotaLeft(e) {
  const used = weeklyUsedPercent(e);
  return used === null ? null : Math.max(0, Math.min(100, 100 - used));
}

/* One line of provenance for the quota pill: every window the engine reported,
   the weekly reset, and when the figure was captured - it only moves when a
   turn runs (claude) or the account is read (codex), so its age matters. */
function quotaTitle(e) {
  const parts = [];
  const clock = (epoch) => {
    if (typeof epoch !== "number" || !isFinite(epoch)) return "";
    try {
      return new Date(epoch * 1000).toLocaleString([], {
        weekday: "short", hour: "2-digit", minute: "2-digit" });
    } catch (error) { return ""; }
  };
  const rl = e.rate_limit;
  const windows = rl && rl.unifiedWindows;
  if (windows && typeof windows === "object") {
    const label = { five_hour: "5h", seven_day: "week",
                    seven_day_overage_included: "week incl. overage" };
    for (const name of ["five_hour", "seven_day", "seven_day_overage_included"]) {
      const w = windows[name];
      if (w && typeof w.utilization === "number") {
        let piece = `${label[name]} ${Math.round(w.utilization * 100)}% used`;
        const reset = clock(w.resetsAt);
        if (reset) piece += ` (resets ${reset})`;
        parts.push(piece);
      }
    }
  } else if (e.quota && typeof e.quota.weekly_used_percent === "number") {
    let piece = `week ${Math.round(e.quota.weekly_used_percent)}% used`;
    const reset = clock(e.quota.resets_at);
    if (reset) piece += ` (resets ${reset})`;
    parts.push(piece);
  }
  if (!parts.length) return "";
  const asOf = clock((rl && rl.captured_at) || (e.quota && e.quota.as_of));
  if (asOf) parts.push(`reported ${asOf}`);
  return parts.join(" · ");
}

/* Every engine-bearing response (poll, usage refresh, version refresh, engine
   upgrade) carries the same {engines, usage_refresh} pair. One applier keeps
   local and remote caches, the footer and Settings in step whatever asked. */
/* Every node's engine payload is remembered here and nowhere else. Several
   callers do their own partial bookkeeping around it - a poll, a lazy load, the
   settings render - and a field wired into only some of them is precisely how
   the update schedule failed to reach a backend's row. */
function rememberEnginePayload(bid, result) {
  if (bid) {
    state.engCache[bid] = result.engines;
    if (result.usage_refresh) state.remoteUsageRefresh[bid] = result.usage_refresh;
    if (result.auto_upgrade) state.remoteAutoUpgrade[bid] = result.auto_upgrade;
  } else {
    state.engines = result.engines;
    state.engMap = {};
    state.engines.forEach(engine => state.engMap[engine.key] = engine);
    if (result.usage_refresh) state.usageRefresh = result.usage_refresh;
    if (result.auto_upgrade) state.autoUpgrade = result.auto_upgrade;
  }
}

function applyEnginesPayload(bid, result) {
  if (!result || !Array.isArray(result.engines) || !result.usage_refresh)
    throw new Error("backend returned an invalid engine response");
  rememberEnginePayload(bid, result);
  if (bid) {
    state.remoteOk[bid] = true;
    delete state.remoteErrors[bid];
    state.remoteEngineCheckedAt[bid] = Date.now();
    delete state.remoteEngineErrors[bid];
  } else {
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
  /* Settings groups read the live button state: an unavailable node becomes
     "Checking backend…" for this request, then resolves from the result. */
  syncRemoteStateViews();
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
    syncRemoteStateViews();
  }
}


/* One node order shared by the session sidebar and status panel. A display
   preference for this browser, like the collapse state of the same boxes - not
   a property of the nodes, and nothing the other side needs to know. The key
   keeps its historical name so existing status-panel choices carry across. */
const NODE_ORDER_KEY = "puppy.order.status-nodes";

function nodeGroupKey(group) {
  return group.bid ? `remote:${group.bid}` : "local";
}

function savedNodeOrder() {
  try {
    const raw = JSON.parse(lsGet(NODE_ORDER_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter(key => typeof key === "string") : [];
  } catch (error) { return []; }
}

/* Nodes the saved order has never seen sort last in their natural order, the
   same rule sessions use, so attaching a backend appends it rather than
   dropping it somewhere arbitrary. */
function sortNodeGroups(groups) {
  const rank = new Map(savedNodeOrder().map((key, index) => [key, index]));
  groups.sort((a, b) => {
    const ar = rank.has(nodeGroupKey(a)) ? rank.get(nodeGroupKey(a)) : Number.MAX_SAFE_INTEGER;
    const br = rank.has(nodeGroupKey(b)) ? rank.get(nodeGroupKey(b)) : Number.MAX_SAFE_INTEGER;
    return ar - br;
  });
  return groups;
}

let dragNode = null;

function cancelNodeDrag(item = null) {
  if (!dragNode || (item && dragNode.item !== item)) return;
  const context = dragNode;
  dragNode = null;
  restoreDragSlots(context, context.selector);
  if (context.item) context.item.classList.remove("dragging");
  if (context.container) context.container.classList.remove("reordering");
  if (context.renderPending)
    setTimeout(() => {
      if (dragNode) dragNode.renderPending = true;
      else renderSidebar();
    }, REORDER_MOTION_MS);
}

/* The head is the handle, not the whole box: the body holds engine rows and a
   usage-refresh button, and a drag starting on those would be a surprise. */
function wireNodeGroupDrag(group, head, key, selector = ".foot-engine-group") {
  head.draggable = true;
  const blockTouchDrag = guardNativeTouchDrag(head);
  head.addEventListener("dragstart", (event) => {
    if (blockTouchDrag(event)) return;
    if (event.target instanceof Element && event.target.closest("button")) {
      event.preventDefault();
      return;
    }
    const container = group.parentElement;
    if (!container) return;
    dragNode = {
      key, item: group, container, selector, renderPending: false,
      originalOrder: reorderChildren(container, selector),
    };
    container.classList.add("reordering");
    requestAnimationFrame(() => {
      if (dragNode && dragNode.item === group) group.classList.add("dragging");
    });
    event.dataTransfer.effectAllowed = "move";
    try { event.dataTransfer.setData("text/plain", `puppy-node:${key}`); } catch (error) {}
  });
  head.addEventListener("dragend", () => cancelNodeDrag(group));
}

function wireNodeGroupDropZone(root) {
  if (root.dataset.reorderWired === "1") return;   // the panel outlives its rows
  root.dataset.reorderWired = "1";
  const mine = () => dragNode && dragNode.container === root;
  root.addEventListener("dragenter", (event) => {
    if (mine()) acceptReorderDrag(event);
  });
  root.addEventListener("dragover", (event) => {
    if (!mine()) return;
    acceptReorderDrag(event);
    moveDragSlot(root, dragNode.item, dragNode.selector, event.clientY, false);
  });
  root.addEventListener("drop", (event) => {
    if (!mine()) return;
    acceptReorderDrag(event);
    const context = dragNode;
    dragNode = null;
    context.item.classList.remove("dragging");
    root.classList.remove("reordering");
    const keys = reorderChildren(root, context.selector)
      .map(node => node.dataset.nodeKey).filter(Boolean);
    if (keys.length) {
      lsSet(NODE_ORDER_KEY, JSON.stringify(keys));
      renderSidebar();
    }
  });
}

function renderFootEngines() {
  const root = $("foot-engines");
  if (dragNode && dragNode.item && dragNode.item.isConnected &&
      dragNode.container === root) {
    dragNode.renderPending = true;
    return;
  }
  root.innerHTML = "";
  const groups = [{ bid: 0, name: backendName(0), version: state.version || "",
                    engines: state.engines }]
    .concat(state.backends.map(b => ({
      bid: b.id, name: b.name, version: b.remote_version || "",
      engines: Object.prototype.hasOwnProperty.call(state.engCache, b.id) ? state.engCache[b.id] : null,
    })));
  sortNodeGroups(groups);
  wireNodeGroupDropZone(root);
  for (const g of groups) {
    const group = el("div", "foot-engine-group");
    const body = el("div", "foot-engine-body");
    {
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
      head.appendChild(name);
      if (g.version) {
        const version = el("span", "foot-engine-version", `· v${g.version}`);
        version.setAttribute("aria-label", `version ${g.version}`);
        head.appendChild(version);
      }
      head.appendChild(disclosure);
      wireDisclosureSurface(head, disclosure);
      group.appendChild(head);
      group.dataset.nodeKey = key;
      wireNodeGroupDrag(group, head, key);
    }
    if (g.bid && state.remoteOk[g.bid] === false) {
      body.appendChild(el("div", "foot-engine-empty",
        remoteStoppingMessage(g.bid) || "Backend unavailable"));
    } else if (g.engines === null) {
      body.appendChild(el("div", "foot-engine-empty", "Checking engines…"));
    } else if (!g.engines.length) {
      body.appendChild(el("div", "foot-engine-empty", "No engines reported"));
    } else for (const e of g.engines) {
      const row = el("div", "foot-eng");
      const ico = el("span", "foot-ico");
      ico.appendChild(el("span", `engine-dot ${e.key}`));
      row.appendChild(ico);
      row.appendChild(document.createTextNode(e.label));
      const pct = weeklyQuotaLeft(e);
      const healthy = engineReady(e);
      /* Two independent signals in one line, so each gets its own element: the
         engine's own health, and what is left of the weekly allowance. Sharing
         a colour would let a low quota make a working engine look broken. */
      const st = el("span", "st");
      const word = el("span", "st-word " + (healthy ? "ok" : "bad"),
        engineStatusText(e));
      // ready, but the CLI is behind its latest release
      if (healthy && e.update_available === true) word.classList.add("stale");
      st.appendChild(word);
      if (pct != null) {
        st.appendChild(el("span", "st-sep", " · "));
        const quota = el("span", "st-quota" + (pct < QUOTA_LOW_PERCENT ? " low" : ""),
          `${Math.round(pct)}% wk`);
        const detail = quotaTitle(e);
        if (detail) { quota.title = detail; quota.setAttribute("aria-label", detail); }
        st.appendChild(quota);
      }
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
const terminalActivityTurns = new Map();

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
    tab = { id, type: "session", bid, sid, title: (meta && (meta.name || `Session ${sid}`)) || `Session ${sid}` };
    state.tabs.push(tab);
    putTabInPane(id, groupId);
  }
  activateTab(id);
}

function openTermTab(bid, cmd, groupId = null, cwd = "", terminalId = "", options = {}) {
  terminalId = String(terminalId || "").toUpperCase();
  if (terminalId && !/^[A-Z0-9]{4}$/.test(terminalId)) return null;
  let existing = terminalId && state.tabs.find(t => t.type === "term" &&
    (t.bid || 0) === (bid || 0) && t.terminalId === terminalId);
  if (existing) {
    if (Number(options.sid) > 0) existing.sid = Number(options.sid);
    if (options.activate === false) {
      syncTabOrderFromLayout(); renderTabs(); syncSessionBrowserChips();
    } else activateTab(existing.id);
    return existing;
  }
  const id = `t:${Date.now()}:${termSeq++}`;
  const tab = { id, type: "term", bid: bid || 0, cmd: cmd || "",
    cwd: cwd || "", terminalId,
    title: terminalTabTitle(bid, terminalId) };
  if (Number(options.sid) > 0) tab.sid = Number(options.sid);
  state.tabs.push(tab);
  if (options.afterTabId) putTabAfter(id, options.afterTabId, groupId);
  else putTabInPane(id, groupId);
  if (options.activate === false) {
    syncTabOrderFromLayout(); renderTabs(); syncSessionBrowserChips();
  } else activateTab(id);
  return tab;
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

/* Every open chat re-reads which of its browsers are still live, and visible
   browsers re-read their linked session's name and colour for the link pill.
   Cheap: both lists are tiny and each view updates only its own controls. */
function syncSessionBrowserChips() {
  for (const view of Object.values(state.views)) {
    if (view && typeof view.syncBrowserChips === "function") view.syncBrowserChips();
    if (view && typeof view.syncTerminalChips === "function") view.syncTerminalChips();
    if (view && typeof view.renderBinding === "function") view.renderBinding();
  }
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

/* A terminal first touched by the model follows the same background insertion
   as Browser: beside its originating chat, visible to the user, without
   stealing focus or disturbing the composer. */
function handleTerminalActivity(bid, sid, turnId, terminalId = "") {
  bid = Number(bid) || 0;
  sid = Number(sid) || 0;
  const token = String(turnId || "");
  terminalId = String(terminalId || "").toUpperCase();
  if (!sid || !token || !/^[A-Z0-9]{4}$/.test(terminalId)) return;
  const key = `${bid}:${token}:${terminalId}`;
  if (terminalActivityTurns.has(key)) return;
  terminalActivityTurns.set(key, Date.now());
  while (terminalActivityTurns.size > 128)
    terminalActivityTurns.delete(terminalActivityTurns.keys().next().value);
  const sessionTabId = `s:${bid}:${sid}`;
  const sessionPane = workspacePaneForTab(sessionTabId);
  openTermTab(bid, "", sessionPane ? sessionPane.id : null, "", terminalId,
    { activate: false, afterTabId: sessionTabId, sid });
}

function openSettingsTab(groupId = null) {
  if (!state.tabs.some(t => t.id === "settings")) {
    state.tabs.push({ id: "settings", type: "settings", title: "Settings" });
    putTabInPane("settings", groupId);
  }
  activateTab("settings");
}

/* Disabling one node already stops every Chromium process on that node. Retire
   only that node's viewer tabs as the matching UI half of the lifecycle; each
   identified tab also closes its now-stopped catalog entry through closeTab. */
function closeBrowserTabsForBackend(bid) {
  bid = Number(bid) || 0;
  const ids = state.tabs
    .filter(tab => tab.type === "browser" && (Number(tab.bid) || 0) === bid)
    .map(tab => tab.id);
  for (const id of ids) closeTab(id);
  return ids.length;
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
  if (closing.type === "term" && closing.terminalId &&
      terminalInstancesFor(closing.bid || 0)) {
    api(closing.bid || 0,
      `terminal/instances/${encodeURIComponent(closing.terminalId)}`,
      { method: "DELETE" }).catch(error => {
        if (error.status !== 404)
          toast(`Terminal ${closing.terminalId} may still be running: ${error.message}`,
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
  if (closing.type === "browser" || closing.type === "term") syncSessionBrowserChips();
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
  syncSessionBrowserChips();
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
  syncSessionBrowserChips();
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
    /* Two layouts, one control. Narrow: the sidebar is a drawer, so open it.
       Wide: it is a column and there is no drawer to open, so the same button
       collapses it and hands the width to the workspace. */
    if (drawerLayout()) $("app").classList.add("side-open");
    else setSideCollapsed(!$("app").classList.contains("side-collapsed"));
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
      /* Anchored just below the cursor, never at the grab point. Grabbed
         mid-tab, the card would ride at the strip's own height and lie over
         the stationary tabs - its bright border slicing across the dimmed
         original reads as that tab glitching. Below the cursor it trails
         over the pane instead, and the strip keeps telling the story: the
         dimmed slot there is the one that moves. */
      try {
        event.dataTransfer.setData("text/plain", `puppy-tab:${t.id}`);
        event.dataTransfer.setDragImage(dragImage, x, 1);
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
    if (meta) t.title = meta.name || `Session ${t.sid}`;
  } else if (t.type === "term") dotCls = "term";
  else if (t.type === "browser") dotCls = "browser";
  const tdot = el("span", "t-dot " + dotCls);
  if (dotColor) tdot.style.color = dotColor;
  if (t.type === "settings") tdot.appendChild(gearIcon(12));
  else if (t.type === "term") tdot.appendChild(terminalIcon(12));
  else if (t.type === "browser") tdot.appendChild(globeIcon(12));
  tab.appendChild(tdot);
  tab.appendChild(el("span", "t-title",
    (t.type === "term") ? terminalTabTitle(t) :
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
  if (closeAllMenus(button)) return;   // this pane's + was already open
  tabAddTargetGroup = groupId;
  tabAddAnchor = button;
  const menu = $("tab-add-menu");
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
  /* One per window, not one per pane: it opens the session list, which is not a
     property of any pane. workspacePanes() walks first-before-second at every
     split, and first is the left half of a row and the top half of a column, so
     its head is the top-left pane whatever the layout. */
  const leading = workspacePanes()[0];
  if (leading && leading.id === pane.id) tabbar.appendChild(burgerButton());
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
  const add = el("button", "icon-btn");
  add.type = "button";
  add.setAttribute("aria-label", "New tab");
  add.appendChild(plusIcon(14));
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
  /* Detaching a node discards its scroll offset outright - unlike display:none,
     which keeps it - which is why plain tab switching never showed this. This
     rebuild re-attaches every view, so positions are handed over and taken back
     around it; otherwise splitting a pane rewinds a long transcript to the top. */
  const scrolls = new Map();
  for (const [id, view] of Object.entries(state.views)) {
    if (view && typeof view.captureScroll === "function") scrolls.set(id, view.captureScroll());
    if (view.root && view.root.parentNode) view.root.remove();
  }
  tree.innerHTML = "";
  tree.appendChild(renderWorkspaceNode(state.layout));
  for (const [id, saved] of scrolls) {
    const view = state.views[id];
    if (view && typeof view.restoreScroll === "function") view.restoreScroll(saved);
  }

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

/* Views built before the session list arrived - a reload that restores tabs -
   start on the default. Correct them as soon as the list lands, rather than
   waiting for each socket snapshot. */
function syncSessionMetaVisibility() {
  for (const tab of state.tabs) {
    if (tab.type !== "session") continue;
    const view = state.views[tab.id];
    if (!view || !view.root || view.session) continue;   // its own data wins
    const meta = findSessionMeta(tab.bid, tab.sid);
    if (meta) view.root.classList.toggle("meta-hidden", !sessionShowsMeta(meta));
  }
}

function syncTabsWithSessions() {
  syncSessionMetaVisibility();
  let dirty = false;
  for (const t of [...state.tabs]) {
    if (t.type === "session" && !t.bid && !state.sessions.some(s => s.id === t.sid)) {
      closeTab(t.id); dirty = true;
    }
  }
  if (!dirty) renderTabs();
}

/* a click anywhere outside a float dismisses every one of them */
document.addEventListener("click", () => closeAllMenus(null));
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
/* the same drawn cog its own tab shows: the ⚙ glyph this replaced is a
   different icon altogether - filled, sharp-toothed, and font-dependent */
$("btn-settings").appendChild(gearIcon(14, 1.5));
$("btn-settings").onclick = () => { openSettingsTab(state.activeGroup); closeDrawer(); };

/* drawer (mobile) */
$("side-backdrop").onclick = closeDrawer;
function closeDrawer() { $("app").classList.remove("side-open"); }

/* Touch-only drawer drag. The narrow edge target keeps ordinary chat,
   terminal and tab gestures untouched while making the closed drawer easy to
   discover. Once horizontal intent wins, the drawer edge stays attached to
   the finger; vertical movement remains native scrolling inside the drawer. */
(() => {
  const app = $("app");
  const side = $("side");
  const backdrop = $("side-backdrop");
  const mobile = window.matchMedia("(max-width: 900px)");
  const intentDistance = 7;
  const flingVelocity = .45; // CSS px/ms over the most recent 100ms
  let gesture = null;
  let settleFrame = null;
  let suppressedClick = null;

  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

  function clearLivePosition() {
    side.style.removeProperty("transform");
    backdrop.style.removeProperty("opacity");
  }

  function settle(open) {
    app.classList.toggle("side-open", !!open);
    app.classList.remove("drawer-dragging");
    if (settleFrame !== null) cancelAnimationFrame(settleFrame);
    const reduced = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      settleFrame = null;
      clearLivePosition();
      return;
    }
    /* The inline position remains the transition's starting frame. Commit it
       with transitions restored, then remove it so CSS carries the drawer to
       whichever class-owned endpoint release selected. */
    void side.offsetWidth;
    settleFrame = requestAnimationFrame(() => {
      settleFrame = null;
      clearLivePosition();
    });
  }

  function recordSample(current, x, time) {
    current.samples.push({ x, time });
    const cutoff = time - 100;
    while (current.samples.length > 1 && current.samples[0].time < cutoff)
      current.samples.shift();
  }

  function move(current, event) {
    const dx = event.clientX - current.startX;
    const dy = event.clientY - current.startY;
    if (!current.axis) {
      const ax = Math.abs(dx);
      const ay = Math.abs(dy);
      if (Math.max(ax, ay) < intentDistance) return;
      if (ay > ax * 1.15) { current.axis = "vertical"; return; }
      if (ax <= ay * 1.15) return;
      current.axis = "horizontal";
      current.dragging = true;
      app.classList.add("drawer-dragging");
    }
    if (current.axis !== "horizontal") return;
    event.preventDefault();
    const x = clamp(current.baseX + dx, current.closedX, 0);
    current.progress = (x - current.closedX) / -current.closedX;
    side.style.transform = `translate3d(${x}px,0,0)`;
    backdrop.style.opacity = String(current.progress);
    recordSample(current, event.clientX, event.timeStamp);
  }

  function finish(event, cancelled = false) {
    const current = gesture;
    if (!current || event.pointerId !== current.id) return;
    if (!cancelled) move(current, event); // a quick flick may have no move frame
    gesture = null;
    try { current.target.releasePointerCapture(current.id); } catch (error) {}
    if (!current.dragging) return;

    event.preventDefault();
    suppressedClick = { target: current.target, until: Date.now() + 450 };
    let open = current.startOpen;
    if (!cancelled) {
      const first = current.samples[0];
      const last = current.samples[current.samples.length - 1];
      const elapsed = last && first ? last.time - first.time : 0;
      const velocity = elapsed > 0 ? (last.x - first.x) / elapsed : 0;
      open = Math.abs(velocity) >= flingVelocity ? velocity > 0 : current.progress >= .5;
    }
    settle(open);
  }

  function wireDrag(target, startOpen) {
    target.addEventListener("pointerdown", event => {
      if (!mobile.matches || event.pointerType !== "touch" || !event.isPrimary || gesture)
        return;
      if (app.classList.contains("side-open") !== startOpen) return;
      const width = side.getBoundingClientRect().width;
      if (!(width > 0)) return;
      /* During an edge-open drag the drawer's right edge sits under the
         finger. CSS carries it the final hidden 5% only after a closed settle,
         keeping the resting shadow outside the viewport. */
      const closedX = -width;
      gesture = {
        id: event.pointerId, target, startOpen,
        startX: event.clientX, startY: event.clientY,
        baseX: startOpen ? 0 : event.clientX - width, closedX,
        progress: startOpen ? 1 : 0, axis: "", dragging: false,
        samples: [{ x: event.clientX, time: event.timeStamp }],
      };
      try { target.setPointerCapture(event.pointerId); } catch (error) {}
    });
    target.addEventListener("pointermove", event => {
      if (gesture && event.pointerId === gesture.id) move(gesture, event);
    });
    target.addEventListener("pointerup", event => finish(event));
    target.addEventListener("pointercancel", event => finish(event, true));
    target.addEventListener("click", event => {
      if (!suppressedClick) return;
      if (Date.now() >= suppressedClick.until) { suppressedClick = null; return; }
      if (suppressedClick.target !== target) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      suppressedClick = null;
    }, true);
  }

  wireDrag($("drawer-edge"), false);
  wireDrag(side, true);
  wireDrag(backdrop, true);
})();

/* Browsers offer their own saved-value dropdowns on any field that does not say
   otherwise - "Saved info" in Edge, form history in Chrome. Nothing Puppy asks
   for is a personal detail worth remembering across sites: they are paths,
   commands, URLs and tokens, and a stale suggestion over a working directory is
   noise at best. So every text control opts out unless it declares its own
   value, which is how the sign-in fields keep working with password managers.
   Run over what is already here and over anything added later, so a modal or
   settings card built at runtime is covered without each markup site having to
   remember. Whether a browser honours the request is still the browser's call:
   profile autofill can override it for fields it classifies itself. */
const AUTOFILL_TEXT_TYPES = new Set([
  "text", "search", "url", "tel", "email", "number", "password",
]);

function suppressAutofill(root) {
  if (!root || root.nodeType !== 1) return;
  const apply = node => {
    if (node.hasAttribute("autocomplete")) return;   // a declared value wins
    if (node.tagName === "INPUT" && !AUTOFILL_TEXT_TYPES.has(
        (node.getAttribute("type") || "text").toLowerCase())) return;
    node.setAttribute("autocomplete", "off");
  };
  if (root.tagName === "INPUT" || root.tagName === "TEXTAREA") apply(root);
  for (const node of root.querySelectorAll("input,textarea")) apply(node);
}

suppressAutofill(document.body);
new MutationObserver(records => {
  for (const record of records)
    for (const node of record.addedNodes) suppressAutofill(node);
}).observe(document.body, { childList: true, subtree: true });

/* light / dark theme (class applied pre-paint by an inline head script) */
function currentTheme() {
  return document.documentElement.classList.contains("light") ? "light" : "dark";
}
function applyTheme(t) {
  document.documentElement.classList.toggle("light", t === "light");
  const button = $("btn-theme");
  button.replaceChildren(themeIcon(14, t === "light"));
  button.setAttribute("aria-label", t === "light" ?
    "Switch to dark mode" : "Switch to light mode");
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

function drawerLayout() {
  return window.matchMedia("(max-width:900px)").matches;
}

function savedSideWidth() {
  const saved = parseInt(lsGet("puppy.sidew") || "", 10);
  return saved ? Math.min(480, Math.max(200, saved)) : 256;
}

/* Collapsing zeroes --side-w as well as hiding the column: the body's backdrop
   grid is masked against that width so it never collides with the session
   list, and a stale 256 would leave a bare strip once the list is gone. */
let sideMotionTimer = null;
function setSideCollapsed(on, animate = true) {
  const app = $("app");
  const root = document.documentElement;
  const open = savedSideWidth();
  // the width the contents keep while the column slides, so they travel with
  // it instead of reflowing narrower and narrower on the way out
  root.style.setProperty("--side-w-open", open + "px");
  /* Transitioned only while the toggle runs. The resize grip writes --side-w
     on every pointermove, and a standing transition would make that drag lag
     behind the pointer. */
  if (animate) {
    app.classList.add("side-animating");
    clearTimeout(sideMotionTimer);
    sideMotionTimer = setTimeout(() => {
      app.classList.remove("side-animating");
      window.dispatchEvent(new Event("resize"));   // settle xterm on the final width
    }, SLIDE_MOTION_MS + 40);
  }
  app.classList.toggle("side-collapsed", !!on);
  root.style.setProperty("--side-w", on ? "0px" : open + "px");
  lsSet("puppy.sidecollapsed", on ? "1" : "");
  window.dispatchEvent(new Event("resize"));   // xterm fit etc.
}

/* Desktop sidebar drag. The ordinary right-edge grip remains a 200-480px
   resizer. Pulling it below that range changes into a live collapse gesture;
   when hidden, the narrow viewport-edge target reverses the same gesture.
   Width (and therefore the workspace) stays under the pointer, then the
   existing sidebar transition carries only the remaining distance. */
(() => {
  const app = $("app");
  const side = $("side");
  const grip = $("side-resize");
  const edge = $("drawer-edge");
  const root = document.documentElement;
  const desktop = window.matchMedia("(min-width: 901px)");
  const minWidth = 200;
  const maxWidth = 480;
  const flingVelocity = .45; // CSS px/ms over the most recent 100ms
  let gesture = null;
  let settleFrame = null;

  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

  root.style.setProperty("--side-w", savedSideWidth() + "px");
  if (lsGet("puppy.sidecollapsed") === "1") setSideCollapsed(true, false);

  function clearLivePosition() {
    side.style.removeProperty("width");
    side.style.removeProperty("opacity");
    side.style.removeProperty("visibility");
  }

  function clearDragClasses() {
    app.classList.remove("side-dragging", "side-drag-revealing", "side-drag-collapsing");
    grip.classList.remove("active");
  }

  function recordSample(current, position, time) {
    current.samples.push({ position, time });
    const cutoff = time - 100;
    while (current.samples.length > 1 && current.samples[0].time < cutoff)
      current.samples.shift();
  }

  function velocity(current) {
    const first = current.samples[0];
    const last = current.samples[current.samples.length - 1];
    const elapsed = last && first ? last.time - first.time : 0;
    return elapsed > 0 ? (last.position - first.position) / elapsed : 0;
  }

  function liveWidth(current, event) {
    const delta = event.clientX - current.startX;
    if (!current.moved && Math.abs(delta) < 1) return;
    current.moved = true;
    const width = Math.round(clamp(event.clientX, 0, current.maxWidth));
    current.width = width;
    root.style.setProperty("--side-w", width + "px");
    if (current.revealing) {
      side.style.opacity = String(width / current.maxWidth);
    } else if (width < minWidth) {
      app.classList.add("side-drag-collapsing");
      side.style.opacity = String(width / minWidth);
    } else {
      app.classList.remove("side-drag-collapsing");
      side.style.removeProperty("opacity");
    }
    recordSample(current, width, event.timeStamp);
    event.preventDefault();
  }

  function animateTo(collapsed, currentWidth, currentOpacity) {
    /* Pin the pointer-owned frame while CSS regains its transitions. Removing
       these inline values one frame later gives the browser an exact start
       and lets setSideCollapsed own the persisted endpoint as usual. */
    side.style.width = currentWidth + "px";
    side.style.opacity = String(currentOpacity);
    side.style.visibility = "visible";
    clearDragClasses();
    setSideCollapsed(collapsed, true);
    if (settleFrame !== null) cancelAnimationFrame(settleFrame);
    const reduced = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      settleFrame = null;
      clearLivePosition();
      return;
    }
    void side.offsetWidth;
    settleFrame = requestAnimationFrame(() => {
      settleFrame = null;
      clearLivePosition();
    });
  }

  function finish(event, cancelled = false) {
    const current = gesture;
    if (!current || event.pointerId !== current.id) return;
    if (!cancelled) liveWidth(current, event); // retain a one-frame mouse flick
    gesture = null;
    try { current.target.releasePointerCapture(current.id); } catch (error) {}
    if (!current.moved) {
      clearDragClasses();
      clearLivePosition();
      return;
    }
    event.preventDefault();

    if (cancelled) {
      animateTo(current.revealing, current.width,
        current.revealing ? current.width / current.maxWidth :
          current.width < minWidth ? current.width / minWidth : 1);
      return;
    }

    /* Releasing the grip in its normal range is a resize, not a hide. Only
       the sub-minimum lane settles to an endpoint. */
    if (!current.revealing && current.width >= minWidth) {
      clearDragClasses();
      clearLivePosition();
      root.style.setProperty("--side-w-open", current.width + "px");
      lsSet("puppy.sidew", String(current.width));
      lsSet("puppy.sidecollapsed", "");
      window.dispatchEvent(new Event("resize"));
      return;
    }

    const speed = velocity(current);
    const progress = current.revealing ? current.width / current.maxWidth :
      current.width / minWidth;
    const open = Math.abs(speed) >= flingVelocity ? speed > 0 : progress >= .5;
    const opacity = current.revealing ? progress : current.width / minWidth;
    animateTo(!open, current.width, opacity);
  }

  function begin(target, event, revealing) {
    if (!desktop.matches || event.button !== 0 || event.isPrimary === false || gesture ||
        app.classList.contains("side-animating")) return;
    if (app.classList.contains("side-collapsed") !== revealing) return;
    const openWidth = savedSideWidth();
    const startWidth = revealing ? 0 : side.getBoundingClientRect().width;
    if (!revealing && !(startWidth >= minWidth)) return;
    event.preventDefault();
    if (settleFrame !== null) {
      cancelAnimationFrame(settleFrame);
      settleFrame = null;
      clearLivePosition();
    }
    root.style.setProperty("--side-w-open", openWidth + "px");
    gesture = {
      id: event.pointerId, target, revealing, startX: event.clientX,
      width: startWidth, maxWidth: revealing ? openWidth : maxWidth,
      moved: false, samples: [{ position: startWidth, time: event.timeStamp }],
    };
    app.classList.add("side-dragging");
    app.classList.toggle("side-drag-revealing", revealing);
    if (!revealing) grip.classList.add("active");
    try { target.setPointerCapture(event.pointerId); } catch (error) {}
  }

  function wire(target, revealing) {
    if (!target) return;
    target.addEventListener("pointerdown", event => begin(target, event, revealing));
    target.addEventListener("pointermove", event => {
      if (gesture && event.pointerId === gesture.id) liveWidth(gesture, event);
    });
    target.addEventListener("pointerup", event => finish(event));
    target.addEventListener("pointercancel", event => finish(event, true));
  }

  wire(edge, true);
  wire(grip, false);
  grip.addEventListener("dblclick", () => {
    root.style.setProperty("--side-w", "256px");
    root.style.setProperty("--side-w-open", "256px");
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

/* Every float in the app closes through here, so opening one always retires
   the rest. The + menu needs it most: it is a static node rather than a .dyn
   one, so closeMenusToggling could not see it and the two stayed open together.
   Returns true when `anchor`'s own float was what closed, so a second click on
   the same trigger toggles rather than reopening. */
function closeAllMenus(anchor) {
  let wasOpen = false;
  const add = $("tab-add-menu");
  if (!add.classList.contains("hidden")) {
    if (anchor && tabAddAnchor === anchor) wasOpen = true;
    add.classList.add("hidden");
    tabAddAnchor = null;
  }
  if (closeMenusToggling(anchor)) wasOpen = true;
  closeChoiceMenu();
  return wasOpen;
}

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
/* One attachment chip. The composer strip and a sent message both build from
   here so the two presentations cannot drift apart. `url` is the preview to
   show; "" gives the named-file card, which is also what an image falls back
   to once its local preview is gone. */
function attachmentChipNode(a, url, uploading = false) {
  const chip = el("span", "attach-chip");
  const asFile = () => {
    chip.className = "attach-chip file" + (uploading ? " uploading" : "");
    const icon = el("span", "attach-file-icon");
    icon.appendChild(attachmentFileIcon());
    const copy = el("span", "attach-file-copy");
    copy.appendChild(el("span", "attach-file-name", a.name));
    copy.appendChild(el("span", "attach-file-size",
      `${uploading ? "Uploading · " : ""}${a.sizeText || fmtBytes(a.size)}`));
    chip.appendChild(icon);
    chip.appendChild(copy);
  };
  if (!url) { asFile(); return chip; }
  chip.className = "attach-chip image" + (uploading ? " uploading" : "");
  const img = el("img", "attach-thumb");
  img.alt = a.name;
  /* A node too old to serve previews, or an upload since discarded, answers
     with an error rather than bytes: keep the remove control and fall back to
     the named card the composer already shows for a preview-less image. */
  img.onerror = () => {
    const remove = chip.querySelector(".attach-x");
    chip.innerHTML = "";
    asFile();
    if (remove) chip.appendChild(remove);
  };
  img.src = url;
  chip.appendChild(img);
  if (uploading) chip.appendChild(el("span", "attach-state", "Uploading"));
  return chip;
}

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
    this.liveTextNode = null;
    this.livePendingText = "";
    this.liveFrame = null;
    this.statusText = "";     // model activity; the header and transcript foot mirror it
    this.reconnecting = false; // transport state overlays activity without replacing it
    this.shutdownSeen = false; // its next socket open proves the node restarted
    this.statusRow = null;    // standalone foot row, used when no thinking block is live
    this.queued = [];         // last queue payload, re-rendered when the list expands
    this.pausedQueue = [];    // indexes into queued; held/config rows are never pausable
    this.queueRevision = 0;   // duplicate-safe base for a full reorder permutation
    this.queueDrag = null;    // acknowledged server hold + native drag state
    this.queueDragSeq = 0;
    this.queuePendingRequest = "";
    this.queueEditSeq = 0;
    this.queueEditPending = null; // guarded queue -> shared-composer transaction
    this.queueOpen = false;   // whether the tail past QUEUE_ROWS is showing
    this.oldestSeq = null;
    this.history = [];        // sent messages, oldest first (shell-style recall)
    this.histIdx = null;
    this.histDraft = "";
    this.ctrlCStreak = 0;     // composer-only: second consecutive Ctrl-C clears the queue
    this.attachments = [];    // staged server files represented by draft marker lines
    this.histAttach = null;   // staged attachments parked while history recall is active
    this.sentThumbs = new Map();  // path -> object URL, so recall can re-show previews
    this.uploadPolicy = uploadSettingsFor(this.tab.bid);
    this.draftSupported = backendSupportsSessionDrafts(this.tab.bid);
    this.draftMaxChars = DEFAULT_DRAFT_MAX_CHARS;
    this.draftReady = false;      // true after this socket's authoritative snapshot
    this.draftRevision = 0;
    this.draftClientId = newDraftClientId();
    this.draftClientSeq = 0;
    this.draftLatestSeq = 0;
    this.draftAckSeq = 0;
    this.draftInFlightSeq = 0;
    this.draftPendingText = null;
    this.draftDeferred = null;
    this.draftTouchedBeforeReady = false;
    this.draftJournal = this.draftSupported ? readDraftJournal(this.tab.id) :
      readLocalDraft(this.tab.id);
    this.fileDragDepth = 0;
    this.nativeComposerChoices = prefersNativeChoices();
    this.browserChipKey = null;   // set of linked-browser bubbles now rendered
    this.terminalChipKey = null;  // set of linked-terminal bubbles now rendered
    this.pendingScroll = null;    // position owed back after a workspace rebuild
    this.lastScroll = null;       // last position seen while this view was visible
    this.buildDom();
    this.syncBrowserChips();      // a restored browser tab has a bubble at once
    this.syncTerminalChips();
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
                ${composerChoice("perm", "Permissions", "Permission mode")}
                ${composerChoice("model", "Model", "Model")}
                ${composerChoice("effort", "Effort", "Reasoning effort")}
              </div>
            </div>
            <button class="btn-queue hidden" type="button">Queue</button>
            <button class="btn-send">Send</button>
          </div>
          <input class="hidden attach-input" type="file" multiple>
        </div>
      </div>`;
    this.root = root;
    /* Seeded from what the session list already knows, before this view is
       painted. The head is only authoritative once the socket snapshot lands,
       and rendering it visible until then made a session that hides it flash
       its strip and then drop it. An unknown session keeps the default (shown)
       and is corrected by syncSessionMetaVisibility when the list arrives. */
    if (!sessionShowsMeta(findSessionMeta(this.tab.bid, this.tab.sid)))
      root.classList.add("meta-hidden");
    this.scroll = root.querySelector(".chat-scroll");
    /* Tracked while visible so a view that is hidden when the workspace is
       rebuilt still has a position to be handed back - a display:none element
       reports scrollTop 0, so it cannot be read at capture time. */
    this.scroll.addEventListener("scroll", () => {
      if (this.scroll.clientHeight)
        this.lastScroll = { top: this.scroll.scrollTop, bottom: this.atBottom() };
    }, { passive: true });
    this.inner = root.querySelector(".chat-inner");
    this.ta = root.querySelector("textarea");
    this.composerBox = root.querySelector(".composer-box");
    this.sendBtn = root.querySelector(".btn-send");
    this.queueBtn = root.querySelector(".btn-queue");
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

    // Paint the crash journal immediately; the socket snapshot decides whether
    // it is still newer than the durable shared value.
    if (this.draftJournal && this.draftJournal.text) {
      const restored = splitAttachmentMarkers(this.draftJournal.text);
      this.ta.value = restored.text;
      try { this.ta.setSelectionRange(restored.text.length, restored.text.length); }
      catch (_) {}
      this.attachments = restored.attachments;
      this.renderAttachments(false);
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
    /* submit() already queues when a turn is in flight - the same path Enter
       takes. This just gives that a visible control while the primary button
       is busy being Stop. */
    this.queueBtn.onclick = () => this.submit();
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
    if (this.tab.bid && !backendConnectionAllowed(this.tab.bid)) {
      this.setReconnecting(true);
      return;
    }
    if (this.ws && (this.ws.readyState === 0 || this.ws.readyState === 1)) return;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const sequence = ++this.connectionSequence;
    const ws = new WebSocket(wsUrl(this.tab.bid, `ws/session/${this.tab.sid}`));
    this.ws = ws;
    this.draftReady = false;
    this.draftLatestSeq = 0;
    this.draftAckSeq = 0;
    this.draftInFlightSeq = 0;
    this.draftPendingText = null;
    this.draftDeferred = null;
    ws.onopen = () => {
      if (this.closed || sequence !== this.connectionSequence || this.ws !== ws) {
        try { ws.close(); } catch (error) {}
        return;
      }
      this.retry = 800;
      this.setReconnecting(false);
      const recovered = this.shutdownSeen;
      this.shutdownSeen = false;
      noteRemoteSocketReachable(this.tab.bid, recovered);
    };
    ws.onmessage = (ev) => {
      if (sequence !== this.connectionSequence || this.ws !== ws) return;
      let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (d.type === "node_stopping") {
        this.shutdownSeen = true;
        handleRemoteNodeStopping(this.tab.bid, d);
        return;
      }
      this.setReconnecting(false); // a message is proof even in unusual WebSocket shims
      noteRemoteSocketReachable(this.tab.bid);
      this.handle(d);
    };
    ws.onclose = () => {
      if (sequence !== this.connectionSequence || this.ws !== ws) return;
      this.ws = null;
      this.draftReady = false;
      this.draftInFlightSeq = 0;
      this.draftPendingText = null;
      this.cancelQueueEdit("Connection lost before the queued message could be edited");
      this.cancelQueueDrag(null, false);
      if (this.closed) return;
      this.setReconnecting(true);
      if (this.tab.bid && !backendConnectionAllowed(this.tab.bid)) return;
      const delay = Math.round(this.retry * (.85 + Math.random() * .3));
      this.retry = Math.min(this.retry * 1.7, 15000);
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.connect();
      }, delay);
    };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
  }

  resumeConnection() {
    if (this.closed) return;
    if (!this.ws || this.ws.readyState > WebSocket.OPEN) this.retry = 800;
    this.connect();
  }

  destroy() {
    this.closed = true;
    this.cancelQueueEdit("", false);
    this.cancelQueueDrag(null, true);
    if (this._stopLoadOlder) this._stopLoadOlder();
    this.clearFileDropTarget();
    this.connectionSequence++;
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    window.removeEventListener("resize", this._onResize);
    if (this.ws) try { this.ws.close(); } catch (e) {}
    this.clearLive();
    /* Closing a view is not deleting its shared draft. Abort only bytes still
       in flight and release browser-owned previews; completed server uploads
       remain owned by the durable draft and can reopen on another device. */
    const staged = new Set([...(this.histAttach || []), ...this.attachments]);
    this.histAttach = null;
    this.attachments = [];
    for (const attachment of staged) {
      attachment.removed = true;
      if (attachment.controller) attachment.controller.abort();
      if (attachment.url && attachment.ownsUrl) URL.revokeObjectURL(attachment.url);
      attachment.url = "";
    }
    for (const url of this.sentThumbs.values()) URL.revokeObjectURL(url);
    this.sentThumbs.clear();
    /* the menus now sit on <body>, so closing this view no longer takes them
       with it - drop the ones it owns */
    for (const menu of document.querySelectorAll(".menu.dyn"))
      if (menu._ownerView === this.root) menu.remove();
    this.root.remove();
  }

  onShow(focus = true) {
    this.syncGutter(); this.syncComposerMeta(); this.syncHeadOverflow();
    this.syncUploadButton();
    /* A position owed from a rebuild wins over jumping to the newest message:
       the user did not open this tab, it was re-attached underneath them. */
    if (!this.applyPendingScroll()) this.scrollBottom(true);
    if (focus) this.ta.focus();
  }

  /* Read live while visible; a hidden view has no scroll box to read, so it
     falls back to the last position seen (or one still owed to it). */
  captureScroll() {
    if (this.scroll.clientHeight)
      return { top: this.scroll.scrollTop, bottom: this.atBottom() };
    return this.pendingScroll || this.lastScroll;
  }

  restoreScroll(saved) {
    this.pendingScroll = saved || null;
    this.applyPendingScroll();
  }

  /* Whether the position was actually applied: writing scrollTop on a
     display:none element is silently ignored, so a hidden view holds onto it
     until onShow. Following the tail is restored as the tail, not as a stale
     offset, since the transcript may have grown or been re-laid out. */
  applyPendingScroll() {
    const saved = this.pendingScroll;
    if (!saved || !this.scroll.clientHeight) return false;
    this.pendingScroll = null;
    this.scroll.scrollTop = saved.bottom ? this.scroll.scrollHeight : saved.top;
    this.lastScroll = { top: this.scroll.scrollTop, bottom: saved.bottom };
    return true;
  }

  syncRemoteState() {
    this.syncUploadButton();
    const stopping = !!remoteStoppingMessage(this.tab.bid);
    const unavailable = !!this.tab.bid && !backendConnectionAllowed(this.tab.bid);
    if (unavailable) {
      if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
      if (this.ws) {
        const ws = this.ws;
        this.ws = null;
        this.connectionSequence++;
        try { ws.close(); } catch (error) {}
      }
      this.draftReady = false;
      this.setReconnecting(true);
    } else if (this.tab.bid) {
      this.resumeConnection();
    }
    this.sendBtn.disabled = stopping || unavailable;
    this.queueBtn.disabled = stopping || unavailable;
    this.renderStatus();
  }

  handleNodeStopping() {
    this.shutdownSeen = true;
    this.status = "idle";
    this.clearLive();
    this.hideApproval();
    this.updateRunState();
    this.syncRemoteState();
  }

  clearNodeStopping() {
    this.shutdownSeen = false;
    this.syncRemoteState();
  }

  syncUploadButton() {
    if (!this.attachButton) return;
    const supported = backendSupportsFileUploads(this.tab.bid);
    const unavailable = !!this.tab.bid && !backendConnectionAllowed(this.tab.bid);
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

  /* Keep the draft in the form the message would be sent in: attachment marker
     lines make the same value portable to another browser without a second
     attachment data model. */
  draftValue() {
    const markers = this.attachments.filter(a => a.path).map(attachmentMarkerLine).join("\n");
    const text = this.ta.value;
    return markers ? (text ? text + "\n\n" + markers : markers) : text;
  }

  saveDraft() {
    /* A queued-message edit is an explicit full-composer replacement. Upload
       completion and other asynchronous paints may call saveDraft while its
       backend transaction is in flight; none may put the old value behind the
       edit frame on the socket. */
    if (this.queueEditPending) return this.queueEditPending.text;
    const text = this.draftValue();
    if (!this.draftSupported) {
      lsSet("puppy.draft." + this.tab.id, text);
      if (!this.draftReady) this.draftTouchedBeforeReady = true;
      return text;
    }
    writeDraftJournal(this.tab.id, text, this.draftRevision);
    this.draftJournal = {
      text, baseRevision: this.draftRevision, submitted: false,
    };
    if (!this.draftReady) {
      this.draftTouchedBeforeReady = true;
      return text;
    }
    if (!this.sendDraft(text)) {
      this.draftReady = false;
      this.draftTouchedBeforeReady = true;
    }
    return text;
  }

  sendDraft(text) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return 0;
    /* Keep the crash journal current on every edit, but place at most one
       ordinary full-value frame on the wire until its echo arrives. The latest
       value replaces any unsent intermediate keystrokes. */
    if (this.draftInFlightSeq) {
      this.draftPendingText = text;
      return this.draftInFlightSeq;
    }
    return this.sendDraftNow(text);
  }

  sendDraftNow(text) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return 0;
    const clientSeq = ++this.draftClientSeq;
    this.draftLatestSeq = clientSeq;
    this.draftInFlightSeq = clientSeq;
    this.ws.send(JSON.stringify({
      type: "draft", text,
      client_id: this.draftClientId, client_seq: clientSeq,
    }));
    return clientSeq;
  }

  clearDraftJournal() {
    lsDel("puppy.draft." + this.tab.id);
    this.draftJournal = null;
  }

  checkpointDraftJournal(baseRevision) {
    if (!this.draftJournal) return;
    this.draftJournal.baseRevision = baseRevision;
    writeDraftJournal(this.tab.id, this.draftJournal.text, baseRevision,
      this.draftJournal.submitted);
  }

  /* Reconcile the crash journal only after seeing the server revision. An
     unacknowledged edit is replayed even if the server accepted an earlier
     keystroke before the connection died. A pending Send is more conservative:
     a newer server revision means it may already have been accepted, so do not
     resurrect it as a duplicate draft. */
  initializeDraft(value) {
    if (!value || typeof value.text !== "string" ||
        !Number.isInteger(value.revision) || value.revision < 0) {
      this.draftSupported = false;
      this.draftReady = false;
      if (this.draftJournal) {
        lsSet("puppy.draft." + this.tab.id, this.draftJournal.text);
        const end = this.ta.value.length;
        try { this.ta.setSelectionRange(end, end); } catch (_) {}
      }
      return;
    }
    this.draftSupported = true;
    const serverText = value.text;
    const serverRevision = value.revision;
    const journal = readDraftJournal(this.tab.id);
    const touched = this.draftTouchedBeforeReady;
    this.draftRevision = serverRevision;
    this.draftReady = true;
    this.draftTouchedBeforeReady = false;

    if (touched) {
      const text = this.draftValue();
      writeDraftJournal(this.tab.id, text, serverRevision);
      this.draftJournal = {
        text, baseRevision: serverRevision, submitted: false,
      };
      if (!this.sendDraft(text)) {
        this.draftReady = false;
        this.draftTouchedBeforeReady = true;
      }
      return;
    }
    if (journal && journal.text !== serverText &&
        (!journal.submitted || serverRevision <= journal.baseRevision)) {
      this.applySharedDraft(journal.text, true);
      writeDraftJournal(this.tab.id, journal.text, serverRevision);
      this.draftJournal = {
        text: journal.text, baseRevision: serverRevision,
        submitted: false,
      };
      if (!this.sendDraft(journal.text)) {
        this.draftReady = false;
        this.draftTouchedBeforeReady = true;
      }
      return;
    }
    this.applySharedDraft(serverText, true);
    this.clearDraftJournal();
  }

  receiveDraft(value) {
    if (!value || typeof value.text !== "string" ||
        !Number.isInteger(value.revision) || value.revision < 0) return;
    // Once a local save found the socket closed, only the next locked snapshot
    // may reconcile it. A final buffered frame from the dying socket must not
    // erase that unsent crash journal.
    if (!this.draftReady) return;
    const own = value.client_id && value.client_id === this.draftClientId;
    const clientSeq = Number(value.client_seq) || 0;
    const previousRevision = this.draftRevision;
    if (value.revision < previousRevision) return;
    this.draftRevision = Math.max(previousRevision, value.revision);

    if (own) {
      this.draftAckSeq = Math.max(this.draftAckSeq, clientSeq);
      if (clientSeq === this.draftInFlightSeq) {
        this.draftInFlightSeq = 0;
        if (this.draftPendingText !== null) {
          const pending = this.draftPendingText;
          this.draftPendingText = null;
          if (!this.sendDraftNow(pending)) {
            this.draftReady = false;
            this.draftTouchedBeforeReady = true;
            return;
          }
        }
      }
      /* Edit intentionally supersedes every composer value sent before its
         socket frame. Do not let the acknowledgement for that older value
         enqueue it again while the guarded queue transaction is in flight. */
      if (this.queueEditPending) {
        this.draftDeferred = null;
        return;
      }
      if (clientSeq < this.draftLatestSeq) {
        this.checkpointDraftJournal(this.draftRevision);
        return;
      }
      if (value.consumed === false) {
        this.applySharedDraft(value.text);
        this.clearDraftJournal();
      } else if (this.draftValue() === value.text) {
        this.clearDraftJournal();
      } else {
        // A local edit happened after the acknowledged frame but before its
        // echo arrived. Make sure that value has its own frame and journal.
        this.saveDraft();
      }
      const deferred = this.draftDeferred;
      this.draftDeferred = null;
      if (deferred && deferred.revision > this.draftRevision)
        this.receiveDraft(deferred);
      return;
    }

    if (value.revision === previousRevision) return;
    /* Keep the explicit replacement visually atomic. The edit completion is
       ordered after its draft broadcast and will apply it while discarding
       local uploads; a genuinely newer peer revision is retained and wins. */
    if (this.queueEditPending) {
      if (!this.draftDeferred || value.revision > this.draftDeferred.revision)
        this.draftDeferred = value;
      return;
    }
    if (this.draftAckSeq < this.draftLatestSeq ||
        this.draftPendingText !== null) {
      if (!this.draftDeferred || value.revision > this.draftDeferred.revision)
        this.draftDeferred = value;
      return;
    }
    this.applySharedDraft(value.text);
    this.clearDraftJournal();
  }

  /* Replace prose and completed attachment chips, ordinarily preserving local
     uploads still in flight. An explicit queue edit replaces those too.
     Matching local image blobs are reused; everything else is released. */
  applySharedDraft(text, caretAtEnd = false, replaceUploading = false) {
    const restored = splitAttachmentMarkers(text);
    const sources = [...(this.histAttach || []), ...this.attachments];
    const byPath = new Map();
    for (const source of sources)
      if (source.path && !byPath.has(source.path)) byPath.set(source.path, source);
    const reused = new Set();
    for (const attachment of restored.attachments) {
      const source = byPath.get(attachment.path);
      if (!source) continue;
      reused.add(source);
      attachment.uploadId = source.uploadId || "";
      attachment.url = source.url || "";
      attachment.ownsUrl = !!source.ownsUrl;
      attachment.preview = source.preview;
      attachment.size = source.size;
      attachment.sizeText = source.sizeText;
      attachment.contentType = source.contentType;
    }
    const uploading = [];
    for (const source of sources) {
      if (!replaceUploading && source.uploading && !source.removed) {
        if (!uploading.includes(source)) uploading.push(source);
        continue;
      }
      if (reused.has(source)) continue;
      source.removed = true;
      if (source.controller) source.controller.abort();
      if (replaceUploading && source.uploadId)
        this.discardServerUpload(source.uploadId, true);
      if (source.url && source.ownsUrl) URL.revokeObjectURL(source.url);
      source.url = "";
    }
    this.histAttach = null;
    this.histIdx = null;
    this.histDraft = "";
    this.attachments = restored.attachments.concat(uploading);

    const oldText = this.ta.value;
    const oldStart = this.ta.selectionStart;
    const oldEnd = this.ta.selectionEnd;
    let prefix = 0;
    while (prefix < oldText.length && prefix < restored.text.length &&
           oldText[prefix] === restored.text[prefix]) prefix++;
    let suffix = 0;
    while (suffix < oldText.length - prefix && suffix < restored.text.length - prefix &&
           oldText[oldText.length - 1 - suffix] ===
             restored.text[restored.text.length - 1 - suffix]) suffix++;
    const oldChangedEnd = oldText.length - suffix;
    const newChangedEnd = restored.text.length - suffix;
    const remap = position => position <= prefix ? position :
      position >= oldChangedEnd ? newChangedEnd + (position - oldChangedEnd) : newChangedEnd;
    this.ta.value = restored.text;
    try {
      if (caretAtEnd)
        this.ta.setSelectionRange(restored.text.length, restored.text.length);
      else
        this.ta.setSelectionRange(remap(oldStart), remap(oldEnd));
    } catch (_) {}
    this.renderAttachments(false);
    this.resizeComposer();
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

  /* History mode ended without walking back to the draft, so release the local
     attachment objects it parked. Shared server bytes follow session lifecycle. */
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
        if (Number.isInteger(d.draft_max_chars) && d.draft_max_chars > 0)
          this.draftMaxChars = d.draft_max_chars;
        if (d.uploads) {
          const policy = rememberUploadSettings(this.tab.bid, d.uploads);
          if (policy) this.uploadPolicy = policy;
        }
        if (Object.prototype.hasOwnProperty.call(d, "draft")) this.initializeDraft(d.draft);
        else this.initializeDraft(null);  // older remote node: local-only compatibility
        this.syncUploadButton();
        this.status = d.status;
        noteSessionActivity(this.tab.bid, this.tab.sid, d.status === "running",
          d.active_since, d.server_time, d.completion_status);
        this.retry = 800;
        // the transcript is being rebuilt: retire the watcher on the old button
        if (this._stopLoadOlder) this._stopLoadOlder();
        this.clearLive();
        this.inner.innerHTML = "";
        this.toolCards = {};
        this.switchLines = [];
        this.oldestSeq = d.events.length ? d.events[0].seq : null;
        if (d.events.length >= 200) this.addLoadOlder();
        const transcript = document.createDocumentFragment();
        d.events.forEach(ev => {
          const node = this.buildEventNode(ev);
          if (node) transcript.appendChild(node);
        });
        this.inner.appendChild(transcript);
        this.history = d.events.filter(ev => ev.kind === "user")
          .map(ev => (ev.data && ev.data.text) || "").filter(Boolean);
        this.histIdx = null;
        this.renderQueue(d.queued || [], d.held || [], d.paused || [],
          d.queue_revision);
        // before updateHead: pickers read the queue
        this.updateHead();
        this.updateRunState();
        this.syncLiveStatus();
        if (d.pending_approval) this.showApproval(d.pending_approval);
        else this.hideApproval();
        this.scrollBottom(true);
        break;
      case "draft":
        this.receiveDraft(d);
        break;
      case "event": {
        const follow = this.atBottom();   // before clearLive reshapes the tail
        this.clearLive();
        this.renderEvent(d.event, true, follow);
        if (d.event.kind === "user") {
          if (d.event.data && d.event.data.text) this.history.push(d.event.data.text);
          if (this._forceScroll) { this._forceScroll = false; this.scrollBottom(true); }
        }
        this.syncLiveStatus();
        break;
      }
      case "delta":
        this.appendLive(d.block, d.text);
        break;
      case "status":
        this.setStatus(d.text);
        break;
      case "turn_init":
        this.setStatus(`Model ${d.model}`);
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
        this.renderQueue(d.queued || [], d.held || [], d.paused || [],
          d.queue_revision);
        this.updateHead();   // the pickers speak for whatever is now last in line
        break;
      case "queue_reorder_ready":
        this.queueReorderReady(d);
        break;
      case "queue_reorder_complete":
        this.queueReorderComplete(d);
        break;
      case "queue_edit_complete":
        this.queueEditComplete(d);
        break;
      case "queue_reorder_cancelled":
        if (this.queuePendingRequest === d.request_id ||
            this.queueDrag && this.queueDrag.requestId === d.request_id) {
          this.cancelQueueDrag(null, false);
          this.queuePendingRequest = "";
          this.renderQueue(this.queued, this.held, this.pausedQueue,
            this.queueRevision);
          toast(d.text || "Queue reorder cancelled", "error");
        }
        break;
      case "turn_done":
        /* New nodes tell us whether this turn flowed directly into a queued
           one. On older nodes the pre-pop queue is the closest equivalent. */
        const queueWaiting = d.queue_waiting === true;
        const continued = typeof d.continued === "boolean" ?
          d.continued : this.queued.length > 0;
        this.status = continued ? "running" : "idle";
        this.clearLive();
        this.updateRunState();
        this.setStatus(queueWaiting ? "Waiting for queue order…" :
          continued ? "Starting next queued message…" : "");
        noteSessionActivity(this.tab.bid, this.tab.sid, continued, null, null,
          queueWaiting ? "" : d.completion_status);
        break;
      case "session_meta":
        this.session = d.session;
        this.updateHead();
        syncSessionBrowserChips();
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
      case "terminal_activity":
        handleTerminalActivity(this.tab.bid, this.tab.sid, d.turn_id, d.terminal_id);
        break;
    }
    const runningKinds = { user: 1, assistant: 1, thinking: 1, tool_use: 1, tool_result: 1 };
    if (!remoteStoppingMessage(this.tab.bid) &&
        (d.type === "delta" || (d.type === "event" && runningKinds[d.event.kind]))) {
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
    /* Browser controls finish the pill group: engine, backend and workspace
       identify the session first, then its live browsers sit immediately before
       the transient activity text. */
    const anchor = scroll.querySelector(".chat-status");
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

  syncTerminalChips() {
    const live = state.tabs.filter(t => t.type === "term" && t.terminalId &&
      (t.bid || 0) === (this.tab.bid || 0) && t.sid === this.tab.sid &&
      t.ended !== true && t.terminalGone !== true);
    const key = live.map(t => t.id).join("\u0000");
    if (key === this.terminalChipKey) return;
    this.terminalChipKey = key;
    const scroll = this.root.querySelector(".chat-meta-scroll");
    for (const stale of scroll.querySelectorAll(".chip.terminal")) stale.remove();
    const anchor = scroll.querySelector(".chat-status");
    for (const t of live) {
      const chip = el("button", "chip terminal");
      chip.type = "button";
      chip.appendChild(terminalIcon(11));
      chip.appendChild(el("span", "chip-text", `Terminal ${t.terminalId}`));
      chip.setAttribute("aria-label", `Show Terminal ${t.terminalId}`);
      chip.onclick = () => activateTab(t.id);
      scroll.insertBefore(chip, anchor);
    }
  }

  syncWorkspaceChip() {
    const scroll = this.root.querySelector(".chat-meta-scroll");
    const existing = scroll.querySelector(".chip.ws");
    const s = this.session;
    const ws = s && sessionWorkspace(s);
    if (!ws) {
      if (existing) existing.remove();
      return;
    }
    const link = linkForSession(this.tab.bid, s.id);
    const st = link ? link.state : (s.ws_dirty ? "pending" : "");
    const labels = { init: "Preparing", syncing: "Syncing", ok: "Synced",
                     conflict: "Conflicts", error: "Sync error",
                     pending: "Sync pending" };
    const text = labels[st] || "Linked";
    const cls = "chip ws" +
      (st === "conflict" || st === "pending" ? " warn" :
       st === "error" ? " err" : st === "syncing" || st === "init" ? " busy" : "");
    let chip = existing;
    if (!chip) {
      chip = el("button", cls);
      chip.type = "button";
      /* immediately after the workspace path chip: engine, backend, path,
         then this link's live sync state */
      const cwdChip = scroll.querySelector(".chip.cwd");
      scroll.insertBefore(chip, cwdChip ? cwdChip.nextSibling :
        scroll.querySelector(".chat-status"));
    }
    chip.className = cls;
    chip.textContent = "";
    chip.appendChild(el("span", "ws-glyph", "⇄"));
    chip.appendChild(el("span", "chip-text", text));
    chip.removeAttribute("title");
    chip.removeAttribute("data-tip");
    chip.setAttribute("aria-label",
      `Linked workspace · ${ws.label || ws.root} · ${text}`);
    chip.onclick = () => modalWorkspaceLink(this.tab.bid, s);
  }

  updateHead() {
    const s = this.session;
    if (!s) return;
    this.root.classList.toggle("meta-hidden", !sessionShowsMeta(s));
    const eff = this.effectiveConfig();
    const eng = this.root.querySelector(".chip.eng");
    const engLabel = key => (state.engMap[key] ? state.engMap[key].label : key);
    eng.className = "chip eng eng-" + s.engine + (eff.queuedEngine ? " pending" : "");
    eng.querySelector(".dot").style.background = s.color || "";
    eng.querySelector(".eng-label").textContent = eff.queuedEngine
      ? engLabel(s.engine) + " → " +
        (eff.engine === s.engine ? "reseed" : engLabel(eff.engine))
      : engLabel(s.engine) +
        (s.last_model ? " · " + s.last_model.replace(/^claude-/, "") : (s.model ? " · " + s.model : ""));
    if (eff.queuedEngine)
      eng.setAttribute("aria-label", "Engine switch queued · applies after the queue");
    else eng.removeAttribute("aria-label");
    const cwd = this.root.querySelector(".chip.cwd");
    cwd.textContent = workspaceLabel(s, 34);
    cwd.removeAttribute("title");
    cwd.removeAttribute("data-tip");
    cwd.setAttribute("aria-label", workspaceTitle(s));
    cwd.classList.toggle("warn", !!s.workspace_missing);
    this.root.querySelector(".chip.be").textContent = backendName(this.tab.bid);
    this.syncWorkspaceChip();
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
    setMini("perm", "Permission mode", s.permission_mode || "auto");
    setMini("model", "Model", eff.model || "auto", eff.queuedModel);
    setMini("effort", "Reasoning effort", eff.effort || "auto", eff.queuedEffort);
    this.syncSwitchLines();   // the newest divider tracks the live selection
    this.syncNativeComposerChoices();
    this.syncComposerMeta();
    this.syncHeadOverflow();
    this.tab.title = s.name || `Session ${s.id}`;
    renderTabs();
  }

  updateRunState() {
    const running = this.status === "running";
    if (running) this.sendBtn.innerHTML = '<span class="stop-sq"></span>Stop';
    else this.sendBtn.textContent = "Send";
    this.sendBtn.classList.toggle("stop", running);
    /* only while a turn is running: idle, the primary button already says Send.
       CSS drops it below the desktop breakpoint, where the row has no room. */
    this.queueBtn.classList.toggle("hidden", !running);
    if (!running) this.setStatus("");
    else this.syncLiveStatus();
  }

  visibleStatusText() {
    return remoteStoppingMessage(this.tab.bid) ||
      (this.reconnecting ? "Connection lost · reconnecting…" : this.statusText);
  }

  renderStatus() {
    const text = this.visibleStatusText();
    const label = promptStatusBase(text);
    this.statusEl.innerHTML = text ?
      `<span class="spinner"></span><span class="status-text prompt-status-label" ` +
      `aria-label="${esc(text)}">${esc(label)}</span>` : "";
    this.syncLiveStatus();
    this.syncHeadOverflow();
  }

  setReconnecting(value) {
    const reconnecting = !!value;
    if (this.reconnecting === reconnecting) return;
    this.reconnecting = reconnecting;
    this.renderStatus();
  }

  setStatus(text) {
    this.statusText = text || "";
    this.renderStatus();
  }

  /* ---- transcript rendering ---- */
  /* Reaching the top of a transcript is the whole request for more of it, so
     the button loads itself rather than waiting to be clicked. It stays on
     screen through a short arming pause - long enough to read what is about to
     happen, so history does not simply materialise - and a user still scrolling
     upward skips that pause, because they have already asked twice. Clicking
     works exactly as before. */
  addLoadOlder() {
    if (this._stopLoadOlder) this._stopLoadOlder();
    const btn = el("button", "btn btn-sm btn-ghost load-older", "Load older…");
    let loading = false;
    let armTimer = null;
    let lastTop = null;
    let observer = null;

    const stop = () => {
      if (armTimer) clearTimeout(armTimer);
      armTimer = null;
      if (observer) observer.disconnect();
      observer = null;
      this.scroll.removeEventListener("scroll", onScroll);
      if (this._stopLoadOlder === stop) this._stopLoadOlder = null;
    };
    const disarm = () => {
      if (armTimer) clearTimeout(armTimer);
      armTimer = null;
      btn.classList.remove("armed");
    };

    const load = async () => {
      if (loading || !btn.isConnected) return;
      disarm();
      loading = true;
      btn.classList.add("busy");
      btn.textContent = "Loading older…";
      try {
        const d = await api(this.tab.bid, `sessions/${this.tab.sid}/events?before_seq=${this.oldestSeq}&limit=200`);
        const evs = d.events || [];
        if (!evs.length) { stop(); btn.remove(); return; }
        this.oldestSeq = evs[0].seq;
        const frag = document.createDocumentFragment();
        const anchor = btn.nextSibling;
        evs.forEach(ev => {
          const node = this.buildEventNode(ev);
          if (node) frag.appendChild(node);
        });
        /* Anchor the reading position: everything inserted lands above what the
           user is looking at, so without giving that height back the transcript
           would jump - and, at scrollTop 0, the button would still be in view
           and immediately load again. */
        const beforeHeight = this.scroll.scrollHeight;
        const top = this.scroll.scrollTop;
        this.inner.insertBefore(frag, anchor);
        this.scroll.scrollTop = top + (this.scroll.scrollHeight - beforeHeight);
        if (evs.length < 200) { stop(); btn.remove(); return; }
      } catch (e) {
        toast(e.message, "error");
      } finally {
        loading = false;
        if (btn.isConnected) {
          btn.classList.remove("busy");
          btn.textContent = "Load older…";
        }
      }
    };

    const arm = () => {
      if (loading || armTimer || !btn.isConnected) return;
      btn.classList.add("armed");
      armTimer = setTimeout(() => { armTimer = null; btn.classList.remove("armed"); load(); },
                            LOAD_OLDER_DELAY);
    };
    const onScroll = () => {
      const top = this.scroll.scrollTop;
      const climbing = lastTop !== null && top < lastTop - 2;
      lastTop = top;
      // still heading up while the pause runs: they have asked twice, so go now
      if (armTimer && climbing) { disarm(); load(); }
    };

    this.scroll.addEventListener("scroll", onScroll, { passive: true });
    observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) arm();
      else disarm();
    }, { root: this.scroll, rootMargin: `${LOAD_OLDER_MARGIN}px 0px 0px 0px` });
    observer.observe(btn);

    btn.onclick = load;
    this._stopLoadOlder = stop;
    this.inner.appendChild(btn);
  }

  renderEvent(ev, live, follow = null) {
    const node = this.buildEventNode(ev);
    if (!node) return;
    this.inner.appendChild(node);
    /* A decision sampled before the append beats re-measuring after it, which
       the new node's own height would skew. */
    if (follow === null) this.scrollBottom(!live);
    else if (follow) this.scrollBottom(true);
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
      .filter(n => n.isConnected || !n.parentNode || n.parentNode.nodeType === 11)
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
        /* The marker lines are how the engine receives an attachment, not how
           the person who sent it should have to read it back. Show what the
           composer showed; the message text keeps the markers untouched. */
        const { text, attachments } = splitAttachmentMarkers(d.text || "");
        if (text) linkifyInto(n, text);
        /* "leading" is set here rather than matched with :first-child, which
           counts elements only - prose is a bare text node, so the strip was
           its own first element child either way and the no-prose spacing
           applied to every message. */
        if (attachments.length) {
          const strip = el("div", "attach-strip sent" + (text ? "" : " leading"));
          for (const a of attachments) {
            /* The blob this view still holds, else the node's stored copy, so a
               reload keeps its thumbnails rather than a row of named cards. */
            const chip = attachmentChipNode(a, a.preview
              ? (this.sentThumbs.get(a.path) || uploadPreviewUrl(this.tab.bid, a.path))
              : "");
            chip.setAttribute("aria-label", `${a.name} · ${a.path}`);
            strip.appendChild(chip);
          }
          n.appendChild(strip);
        }
        /* Absolute positioning keeps this control out of the prose and
           attachment layout; copy exactly what the bubble visibly says, not
           the private marker lines used to send its attachments. */
        if (text) n.appendChild(userMessageCopyButton(text));
        return n;
      }
      case "assistant": {
        const n = el("div", "msg msg-assistant");
        const box = el("div", "md");
        box.innerHTML = md(d.text || "");
        decorateMarkdownLinks(box);
        decorateMarkdownImages(box);
        decorateCodeBlocks(box);
        n.appendChild(box);
        return n;
      }
      case "thinking": {
        const n = el("details", "think");
        const sum = el("summary");
        sum.appendChild(thinkingIconNode());
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
          body.appendChild(linkifyInto(el("pre"), displayValue(d.content) || "(Empty)"));
          return null;
        }
        const n = toolCardNode({tool: d.tool || "tool_result", is_error: d.is_error}, true);
        n.classList.add("open");
        const body = n.querySelector(".tool-body");
        body.appendChild(el("div", "tb-label", d.is_error ? "error" : "result"));
        body.appendChild(linkifyInto(el("pre"), displayValue(d.content) || "(Empty)"));
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
        return el("div", "err-card", d.text || "Error");
      }
      /* one shape for every engine: outcome · how long · tokens out · when.
         Cost is deliberately absent - engines price differently (and some not
         at all), so the line would stop meaning the same thing everywhere. */
      case "result": {
        const n = el("div", "result-line");
        const bits = [];
        if (!d.ok) bits.push(`<span class="bad">✗ ${esc((d.error || "Failed").slice(0, 80))}</span>`);
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
        sum.appendChild(thinkingIconNode());
        sum.appendChild(promptStatusLabel(
          this.statusText || thinkingLabel(0), "think-label"));
        this.liveEl.appendChild(sum);
        this.liveEl.appendChild(el("div", "tbody"));
      } else {
        this.liveEl = el("div", "msg msg-assistant msg-live");
        this.liveEl.appendChild(el("div", "md"));
        this.liveEl.appendChild(el("span", "cursor"));
      }
      this.inner.appendChild(this.liveEl);
      const target = block === "thinking" ?
        this.liveEl.querySelector(".tbody") : this.liveEl.querySelector(".md");
      this.liveTextNode = document.createTextNode("");
      target.appendChild(this.liveTextNode);
      this.syncLiveStatus();
    }
    this.livePendingText += text;
    if (this.liveFrame === null) {
      this.liveFrame = requestAnimationFrame(() => {
        this.liveFrame = null;
        this.flushLive();
      });
    }
  }
  flushLive() {
    if (this.liveFrame !== null) {
      cancelAnimationFrame(this.liveFrame);
      this.liveFrame = null;
    }
    if (!this.liveTextNode || !this.livePendingText) return;
    const follow = this.atBottom();
    const text = this.livePendingText;
    this.livePendingText = "";
    this.liveTextNode.appendData(text);
    if (follow) this.scrollBottom(true);
  }
  clearLive() {
    if (this.liveFrame !== null) cancelAnimationFrame(this.liveFrame);
    this.liveFrame = null;
    this.livePendingText = "";
    this.liveTextNode = null;
    if (this.liveEl) this.liveEl.remove();
    this.liveEl = null;
    this.liveKind = null;
  }
  /* The foot of the transcript always says what the header says while a turn
     runs. A streaming thinking block carries it in its summary; the rest of the
     time - tool calls, text streaming, the gaps between blocks - a standalone
     row does, so the two never disagree. */
  syncLiveStatus() {
    const text = this.visibleStatusText() || thinkingLabel(0);
    const thinking = this.liveEl && this.liveKind === "thinking";
    if (thinking) {
      const lab = this.liveEl.querySelector(".think-label");
      if (lab) lab.replaceWith(promptStatusLabel(text, "think-label"));
    }
    if (this.status !== "running" || thinking) {
      if (this.statusRow) { this.statusRow.remove(); this.statusRow = null; }
      return;
    }
    if (!this.statusRow) {
      this.statusRow = el("div", "live-status");
    }
    /* Codex reports this phase as `status: thinking...` rather than a streamed
       thinking block. Swap only the marker; commands and writing retain the
       generic activity spinner. Replacing these two tiny children also handles
       transitions between those states without leaving the old icon behind. */
    this.statusRow.replaceChildren(
      isThinkingStatus(text) ? thinkingIconNode() : el("span", "spinner"),
      promptStatusLabel(text, "think-label"));
    if (this.inner.lastChild !== this.statusRow) {
      const follow = this.atBottom();
      this.inner.appendChild(this.statusRow);   // stays the last thing in the transcript
      if (follow) this.scrollBottom(true);
    }
  }

  /* Is the transcript following the tail? Sample this BEFORE changing the DOM.
     Finishing a turn swaps the streaming block - which holds the raw markdown
     as plain text - for its rendered form, and a message with a table or lists
     grows by several hundred pixels in that one step. Measured afterwards, that
     growth is indistinguishable from the user having scrolled up, so the view
     stops following and strands them at the top of the final message. */
  atBottom() {
    return this.scroll.scrollHeight - this.scroll.scrollTop - this.scroll.clientHeight < 160;
  }

  scrollBottom(force) {
    if (force || this.atBottom()) this.scroll.scrollTop = this.scroll.scrollHeight;
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
      toast("Folders cannot be attached · drop individual files instead", "error", 6000);
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
      toast("File uploads are disabled on this backend", "error");
      return;
    }
    for (const file of files) {
      const legacyImage = !arbitraryFiles && fromClipboard &&
        ATTACHMENT_PREVIEW_TYPES.has(String(file.type || "").toLowerCase());
      if (!arbitraryFiles && !legacyImage) {
        toast("Upgrade this backend to attach arbitrary files", "error");
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
          toast(`File upload failed: ${error.message}`, "error", 6500);
      }
    }
  }

  discardServerUpload(uploadId, quiet = false) {
    if (!uploadId) return;
    api(this.tab.bid, `sessions/${this.tab.sid}/upload/${uploadId}`, {
      method: "DELETE", keepalive: true, timeoutMs: 10000,
    }).catch(error => {
      if (!quiet) toast(`Could not discard upload: ${error.message}`, "error");
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
    this.renderAttachments();
    /* A shared draft is a full-document last-writer-wins stream. Another
       browser may already have sent a later edit which still contains this
       marker, so deleting the bytes here could leave that accepted edit with
       a broken chip. Session deletion cleans these private staged bytes. */
    if (attachment.uploadId && !this.draftSupported)
      this.discardServerUpload(attachment.uploadId, quiet);
  }

  renderAttachments(persist = true) {
    this.attachStrip.innerHTML = "";
    this.attachStrip.classList.toggle("hidden", !this.attachments.length);
    for (const a of this.attachments) {
      // the local blob while this page still holds it, else the node's copy
      const chip = attachmentChipNode(a, a.preview
        ? (a.url || uploadPreviewUrl(this.tab.bid, a.path)) : "", a.uploading);
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
    if (persist && !this.closed) this.saveDraft();
  }

  submit() {
    const draft = this.draftValue();
    let text = this.ta.value.trim();
    if (!text && !this.attachments.length) return;
    if (this.draftSupported && !this.draftReady) {
      toast("Draft is still syncing; wait for the session to reconnect", "error");
      return;
    }
    if (this.draftSupported && this.draftReady && draft.length > this.draftMaxChars) {
      toast(`Draft is too long (${draft.length.toLocaleString()} / ${this.draftMaxChars.toLocaleString()} characters)`,
        "error", 6500);
      return;
    }
    if (this.attachments.some(attachment => attachment.uploading)) {
      toast("Wait for file uploads to finish", "error");
      return;
    }
    if (!this.ws || this.ws.readyState !== 1) { toast("Not connected", "error"); return; }
    if (this.attachments.length) {
      const lines = this.attachments.map(attachmentMarkerLine).join("\n");
      text = text ? text + "\n\n" + lines : lines;
      this.attachments.forEach(a => this.retireSentAttachment(a));
      this.attachments = [];
      this.renderAttachments(false);
    }
    const message = { type: "message", text };
    if (this.draftSupported && this.draftReady) {
      writeDraftJournal(this.tab.id, draft, this.draftRevision, true);
      this.draftJournal = {
        text: draft, baseRevision: this.draftRevision,
        submitted: true,
      };
      /* A queued latest edit must reach the server before consume_draft checks
         the message's exact value. This is the sole bounded exception to the
         one-unacknowledged-draft rule and only occurs at Send. */
      if (this.draftPendingText !== null) {
        this.draftPendingText = null;
        this.sendDraftNow(draft);
      }
      const clientSeq = ++this.draftClientSeq;
      this.draftLatestSeq = clientSeq;
      message.draft = draft;
      message.draft_client_id = this.draftClientId;
      message.draft_client_seq = clientSeq;
    }
    this.ws.send(JSON.stringify(message));
    this.ta.value = "";
    this.resizeComposer();
    this.histIdx = null; this.histDraft = "";
    this.releaseHistoryAttachments();
    // sending always jumps to the bottom - now, and again when the sent
    // message echoes back as a transcript event (even if it was queued)
    this._forceScroll = true;
    this.scrollBottom(true);
    if (!this.draftSupported || !this.draftReady)
      lsDel("puppy.draft." + this.tab.id);
    this.status = "running";
    noteSessionActivity(this.tab.bid, this.tab.sid, true);
    this.updateRunState();
    this.setStatus("Starting…");
  }
  interrupt(clearQueue = false) {
    if (this.ws && this.ws.readyState === 1)
      this.ws.send(JSON.stringify({ type: "interrupt", clear_queue: clearQueue }));
  }

  /* ---- approvals ---- */
  showApproval(req) {
    this.approvalEl.classList.remove("hidden");
    this.approvalEl.innerHTML = "";
    this.approvalEl.appendChild(el("div", "ap-title", `⚠ Approval: ${req.display_name || req.tool_name}` +
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
      if (sug.type === "allowAlways") {
        const b = el("button", "btn btn-sm", sug.label || "Always allow");
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

  /* a queue entry is a prompt string, or a pending change: an engine switch
     ({kind:"engine"}), or a model/effort change tagged with the engine whose
     catalog validated it (older nodes omit the tag - fall back to the
     session's engine, which is all they can queue against) */
  describeQueuedConfig(item) {
    const s = this.session || {};
    const eng = engineInfo(this.tab.bid, item.engine || s.engine);
    const parts = [];
    if (item.kind === "engine") {
      parts.push("Engine → " + ((eng && eng.label) || item.engine || "?"));
      if (item.model) parts.push("Model → " + (modelShorthand(eng, item.model) || item.model));
      if (item.effort) parts.push("Effort → " + (effortShorthand(eng, item.effort) || item.effort));
      return parts.join(" · ");
    }
    if ("model" in item) parts.push("Model → " + (modelShorthand(eng, item.model) || "default"));
    if ("effort" in item) parts.push("Effort → " + (effortShorthand(eng, item.effort) || "default"));
    return parts.join(" · ") || "Setting change";
  }

  queueRow(item, marker, held, paused = false) {
    const cfg = !!(item && typeof item === "object");
    const ident = cfg ? item.key || "" : item;
    const row = el("div", "q-item" + (cfg ? " q-cfg" : "") +
      (held ? " q-held" : "") + (paused ? " q-paused" : ""));
    row.appendChild(el("span", "q-n" + (held ? " q-bang" : ""), marker));
    let text;
    if (cfg) text = this.describeQueuedConfig(item);
    else {
      /* one compact line, so attachments are counted rather than spelled out */
      const parsed = splitAttachmentMarkers(item);
      const count = parsed.attachments.length;
      const body = count ? parsed.text : item;
      text = body.length > 200 ? body.slice(0, 199) + "…" : body;
      if (count) text = (text ? text + " · " : "") +
        `${count} attachment${count === 1 ? "" : "s"}`;
    }
    const t = el("span", "q-t", text);
    t.setAttribute("aria-label", (held ? "held after an interruption · " : "") +
      (paused ? "paused · " : "") +
      (cfg ? text : item));
    row.appendChild(t);
    return { row, ident, cfg };
  }

  renderQueue(q, held, paused, revision) {
    const box = this.queueEl;
    if (this.queueDrag) this.cancelQueueDrag(null, true);
    this.queued = q;
    if (Number.isInteger(revision) && revision >= 0)
      this.queueRevision = revision;
    this.held = Array.isArray(held) ? held : (this.held || []);
    if (Array.isArray(paused)) {
      this.pausedQueue = [...new Set(paused.filter(index =>
        Number.isInteger(index) && index >= 0 && index < q.length &&
        typeof q[index] === "string"))];
    } else {
      this.pausedQueue = this.pausedQueue || [];
    }
    held = this.held;
    const pausedSet = new Set(this.pausedQueue);
    box.innerHTML = "";
    if (!q.length && !held.length) { box.classList.add("hidden"); this.queueOpen = false; return; }
    box.classList.remove("hidden");
    box.classList.toggle("expanded", !!this.queueOpen);
    box.appendChild(el("div", "q-head", `queued · ${q.length + held.length}`));
    /* Held rows first: this work was queued before anything below it, and it is
       the part that needs a decision. Never capped - each is waiting on the
       user, and hiding one behind "+N more" is how it gets forgotten. */
    held.forEach((item, i) => {
      const { row, ident, cfg } = this.queueRow(item, "!", true);
      const resend = el("button", "q-resend");
      resend.type = "button";
      resend.appendChild(refreshIcon(11));
      resend.setAttribute("aria-label",
        cfg ? "Apply this held change" : "Send this held message again");
      resend.onclick = () => this.heldOp("requeue_held", i, ident);
      row.appendChild(resend);
      const x = el("button", "q-x");
      x.type = "button";
      x.appendChild(xIcon(12));
      x.setAttribute("aria-label", "Discard this held item");
      x.onclick = () => this.heldOp("discard_held", i, ident);
      row.appendChild(x);
      box.appendChild(row);
    });
    /* one row per item, in the order they will run. The list is capped so a
       deep queue cannot push the composer down the screen; the tail is one
       click away. The per-item cap only keeps a runaway message out of the DOM
       - each row is a single line that fades out at whatever width is going. */
    const shown = this.queueOpen ? q : q.slice(0, QUEUE_ROWS);
    const live = el("div", "q-live-list");
    this.wireQueueDropZone(live);
    shown.forEach((item, i) => {
      const isPaused = pausedSet.has(i);
      const { row, ident, cfg } = this.queueRow(item, String(i + 1), false, isPaused);
      row.classList.add("q-live");
      row.dataset.queueIndex = String(i);
      if (!cfg && this.draftSupported && backendSupportsQueueEdit(this.tab.bid)) {
        const edit = el("button", "q-edit");
        edit.type = "button";
        edit.appendChild(queueEditIcon(11));
        edit.setAttribute("aria-label", "Edit this queued message");
        edit.onclick = () => this.editQueued(i, ident);
        row.appendChild(edit);
      }
      if (!cfg && backendSupportsQueuePause(this.tab.bid)) {
        const toggle = el("button", "q-pause");
        toggle.type = "button";
        toggle.appendChild(queuePauseIcon(isPaused, 11));
        toggle.setAttribute("aria-label",
          isPaused ? "Resume this queued message" : "Pause this queued message");
        toggle.setAttribute("aria-pressed", String(isPaused));
        toggle.onclick = () => this.setQueuePaused(i, ident, !isPaused);
        row.appendChild(toggle);
      }
      const x = el("button", "q-x");
      x.type = "button";
      x.appendChild(xIcon(12));   // even size in an even box: no half-pixel centring
      x.setAttribute("aria-label",
        cfg ? "Cancel this queued change" : "Cancel this queued message");
      x.onclick = () => this.unqueue(i, ident);   // full text, not the capped copy
      row.appendChild(x);
      if (!cfg && q.length > 1 && backendSupportsQueueReorder(this.tab.bid))
        this.wireQueueDrag(row);
      live.appendChild(row);
    });
    box.appendChild(live);
    if (q.length > shown.length) {
      const more = el("button", "q-more", `+${q.length - shown.length} more`);
      more.onclick = () => {
        this.queueOpen = true;
        this.renderQueue(this.queued, this.held, this.pausedQueue,
          this.queueRevision);
      };
      box.appendChild(more);
    } else if (this.queueOpen && q.length > QUEUE_ROWS) {
      const less = el("button", "q-more", "Show fewer");
      less.onclick = () => {
        this.queueOpen = false;
        this.renderQueue(this.queued, this.held, this.pausedQueue,
          this.queueRevision);
      };
      box.appendChild(less);
    }
    this.syncQueueFade();
    this.syncQueueEditBusy();
  }

  prepareQueueDrag(row, event) {
    if (event.button !== 0 || event.pointerType === "touch" ||
        (event.target instanceof Element && event.target.closest("button"))) return;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    if (this.queueDrag) this.cancelQueueDrag(null, true);
    const container = row.parentElement;
    if (!container) return;
    const requestId = `${Date.now().toString(36)}-${++this.queueDragSeq}`;
    const context = {
      requestId, item: row, container, ready: false, dragging: false,
      revision: this.queueRevision,
      originalOrder: reorderChildren(container, ".q-live"),
      pointerRelease: null,
    };
    const release = () => {
      if (this.queueDrag === context && !context.dragging)
        this.cancelQueueDrag(row, true);
    };
    context.pointerRelease = release;
    window.addEventListener("pointerup", release, { capture: true, once: true });
    window.addEventListener("pointercancel", release, { capture: true, once: true });
    this.queueDrag = context;
    this.queuePendingRequest = requestId;
    this.ws.send(JSON.stringify({
      type: "begin_queue_reorder", request_id: requestId,
      queue_revision: context.revision,
    }));
  }

  wireQueueDrag(row) {
    row.classList.add("q-sortable");
    row.draggable = true;
    const blockTouchDrag = guardNativeTouchDrag(row);
    row.addEventListener("pointerdown", event => this.prepareQueueDrag(row, event));
    row.addEventListener("dragstart", event => {
      if (blockTouchDrag(event)) return;
      const context = this.queueDrag;
      if (!context || context.item !== row || !context.ready ||
          (event.target instanceof Element && event.target.closest("button"))) {
        event.preventDefault();
        return;
      }
      context.dragging = true;
      this.cleanupQueuePointer(context);
      context.container.classList.add("reordering");
      requestAnimationFrame(() => {
        if (this.queueDrag === context) row.classList.add("dragging");
      });
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        try { event.dataTransfer.setData("text/plain", `puppy-queue:${context.requestId}`); }
        catch (_) {}
      }
    });
    row.addEventListener("dragend", () => this.cancelQueueDrag(row, true));
  }

  wireQueueDropZone(container) {
    const mine = () => this.queueDrag && this.queueDrag.dragging &&
      this.queueDrag.container === container;
    container.addEventListener("dragenter", event => {
      if (mine()) acceptReorderDrag(event);
    });
    container.addEventListener("dragover", event => {
      if (!mine()) return;
      acceptReorderDrag(event);
      moveDragSlot(container, this.queueDrag.item, ".q-live", event.clientY, false);
    });
    container.addEventListener("drop", event => {
      if (!mine()) return;
      acceptReorderDrag(event);
      const context = this.queueDrag;
      const visibleOrder = reorderChildren(container, ".q-live")
        .map(node => Number(node.dataset.queueIndex));
      const visible = new Set(visibleOrder);
      const order = visibleOrder.concat(
        this.queued.map((_, index) => index).filter(index => !visible.has(index)));
      this.queueDrag = null;
      this.cleanupQueuePointer(context);
      context.item.classList.remove("dragging");
      container.classList.remove("reordering");
      this.queuePendingRequest = context.requestId;
      if (this.ws && this.ws.readyState === WebSocket.OPEN)
        this.ws.send(JSON.stringify({
          type: "reorder_queue", request_id: context.requestId,
          queue_revision: context.revision, order,
        }));
      else
        this.renderQueue(this.queued, this.held, this.pausedQueue,
          this.queueRevision);
    });
  }

  cleanupQueuePointer(context) {
    if (!context || !context.pointerRelease) return;
    window.removeEventListener("pointerup", context.pointerRelease, true);
    window.removeEventListener("pointercancel", context.pointerRelease, true);
    context.pointerRelease = null;
  }

  cancelQueueDrag(item = null, notifyServer = true) {
    const context = this.queueDrag;
    if (!context || (item && context.item !== item)) return;
    this.queueDrag = null;
    this.cleanupQueuePointer(context);
    if (context.dragging)
      restoreDragSlots(context, ".q-live");
    if (context.item) context.item.classList.remove("dragging");
    if (context.container) context.container.classList.remove("reordering");
    if (notifyServer && this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.queuePendingRequest = context.requestId;
      this.ws.send(JSON.stringify({
        type: "finish_queue_reorder", request_id: context.requestId,
      }));
    } else if (this.queuePendingRequest === context.requestId) {
      this.queuePendingRequest = "";
    }
  }

  queueReorderReady(message) {
    const context = this.queueDrag;
    if (!context || context.requestId !== message.request_id) return;
    if (message.ok === true && message.queue_revision === context.revision) {
      context.ready = true;
      return;
    }
    this.cancelQueueDrag(null, false);
    if (this.queuePendingRequest === message.request_id)
      this.queuePendingRequest = "";
    toast(message.error || "Queue changed before it could be reordered", "error");
  }

  queueReorderComplete(message) {
    if (message.request_id !== this.queuePendingRequest &&
        (!this.queueDrag || message.request_id !== this.queueDrag.requestId)) return;
    this.cancelQueueDrag(null, false);
    this.queuePendingRequest = "";
    if (Array.isArray(message.queued))
      this.renderQueue(message.queued, message.held || [], message.paused || [],
        message.queue_revision);
    if (message.error) toast(message.error, "error");
    if (message.started) {
      this.status = "running";
      this.updateRunState();
      this.setStatus("Starting next queued message…");
    }
  }

  syncQueueEditBusy() {
    const busy = !!this.queueEditPending;
    this.queueEl.classList.toggle("editing", busy);
    if (busy) this.queueEl.setAttribute("aria-busy", "true");
    else this.queueEl.removeAttribute("aria-busy");
    this.queueEl.querySelectorAll(".q-live button").forEach(button => {
      button.disabled = busy;
    });
    this.ta.readOnly = busy;
  }

  cancelQueueEdit(message = "", preserveDraft = true) {
    const context = this.queueEditPending;
    if (!context) return;
    if (context.timer !== null) clearTimeout(context.timer);
    this.queueEditPending = null;
    this.syncQueueEditBusy();
    if (preserveDraft && !this.closed) {
      const text = this.draftValue();
      writeDraftJournal(this.tab.id, text, this.draftRevision);
      this.draftJournal = {
        text, baseRevision: this.draftRevision, submitted: false,
      };
      if (!this.draftReady) this.draftTouchedBeforeReady = true;
    }
    if (message) toast(message, "error", 6500);
  }

  editQueued(index, text) {
    if (this.queueEditPending) return;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      toast("Not connected", "error");
      return;
    }
    if (!this.draftSupported || !this.draftReady) {
      toast("Draft is still syncing; wait for the session to reconnect", "error");
      return;
    }
    const requestId = `${Date.now().toString(36)}-edit-${++this.queueEditSeq}`;
    /* The click means the old full composer value is intentionally discarded.
       Suppress a coalesced keystroke so its older frame cannot land after this
       one, and journal the requested replacement across a sudden page crash. */
    this.draftPendingText = null;
    this.draftDeferred = null;
    writeDraftJournal(this.tab.id, text, this.draftRevision);
    this.draftJournal = {
      text, baseRevision: this.draftRevision, submitted: false,
    };
    const context = { requestId, text, timer: null };
    this.queueEditPending = context;
    this.syncQueueEditBusy();
    context.timer = setTimeout(() => {
      if (this.queueEditPending !== context) return;
      this.cancelQueueEdit("Backend did not confirm the queued-message edit");
    }, 10000);
    try {
      this.ws.send(JSON.stringify({
        type: "edit_queue", request_id: requestId, index, text,
      }));
    } catch (error) {
      this.cancelQueueEdit("Could not edit the queued message");
    }
  }

  queueEditComplete(message) {
    const context = this.queueEditPending;
    if (!context || context.requestId !== message.request_id) return;
    if (context.timer !== null) clearTimeout(context.timer);
    this.queueEditPending = null;
    this.syncQueueEditBusy();
    if (message.error) {
      /* The server rejected the queue identity, so restore the composer value
         whose pending transmission the explicit replacement superseded. */
      this.saveDraft();
      toast(message.error, "error", 6500);
      return;
    }
    const draft = message.draft;
    if (!draft || typeof draft.text !== "string" ||
        !Number.isInteger(draft.revision) || draft.revision < 0) {
      this.saveDraft();
      toast("Backend returned an invalid edited draft", "error", 6500);
      return;
    }
    /* A different device may have authored a later revision after this click.
       Otherwise make this explicit replacement stronger than ordinary shared
       draft updates: local uploads still in flight are discarded as part of
       replacing everything that had been in the composer. */
    const deferred = this.draftDeferred;
    this.draftDeferred = null;
    if (draft.revision >= this.draftRevision) {
      this.draftRevision = draft.revision;
      this.draftPendingText = null;
      this.applySharedDraft(draft.text, true, true);
      this.clearDraftJournal();
    } else if (deferred && deferred.revision === this.draftRevision) {
      /* A peer authored a newer value after the queue edit. Preserve that
         normal last-writer-wins result instead of reviving our older one. */
      this.applySharedDraft(deferred.text);
      this.clearDraftJournal();
    }
    if (message.started) {
      this.status = "running";
      this.updateRunState();
      this.setStatus("Starting next queued message…");
    }
    this.ta.focus();
    scrollCaretIntoView(this.ta);
  }

  unqueue(index, text) {
    if (!this.ws || this.ws.readyState !== 1) { toast("Not connected", "error"); return; }
    this.ws.send(JSON.stringify({ type: "unqueue", index, text }));
  }
  setQueuePaused(index, text, paused) {
    if (!this.ws || this.ws.readyState !== 1) { toast("Not connected", "error"); return; }
    this.ws.send(JSON.stringify({ type: "set_queue_paused", index, text, paused }));
  }
  heldOp(type, index, text) {
    if (!this.ws || this.ws.readyState !== 1) { toast("Not connected", "error"); return; }
    this.ws.send(JSON.stringify({ type, index, text }));
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
    if (closeAllMenus(anchor)) return;
    const menu = el("div", "menu dyn");
    menu._anchor = anchor;
    menu._ownerView = this.root;
    const add = (label, fn, danger) => {
      const b = el("button", danger ? "danger" : "", label);
      b.onclick = (e) => { e.stopPropagation(); menu.remove(); fn(); };
      menu.appendChild(b);
    };
    add("Rename", () => this.rename());
    add("Dot color", () => this.pickColor(anchor));
    add("Switch engine", () => modalSwitchEngine(this));
    menu.appendChild(menuCheckRow("Show status bar", sessionShowsMeta(this.session),
      () => this.patchSession({ show_meta: !sessionShowsMeta(this.session) })));
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
    /* On <body>, like every other float here. Inside the head it inherited
       .chat-head's z-index:2 stacking context, so its own z-index:100 only
       ranked it against its siblings - in a split, the divider (24) and the
       neighbouring pane's terminal layers painted straight over it. */
    document.body.appendChild(menu);
    positionAnchoredMenu(menu, anchor);
  }

  pickColor(anchor) {
    closeAllMenus(null);   // always opens fresh (invoked from the ⋮ menu)
    const menu = el("div", "menu dyn color-menu");
    menu._ownerView = this.root;
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
    document.body.appendChild(menu);   // same stacking reason as showMenu
    positionAnchoredMenu(menu, anchor);
  }

  /* what the NEXT prompt will run under: the session's engine/model/effort,
     then any pending changes waiting in the queue, in order. The pickers and
     mini pills speak about the next prompt, so they read this rather than the
     session. */
  effectiveConfig() {
    const s = this.session || {};
    const out = { engine: s.engine || "", model: s.model || "", effort: s.effort || "",
                  queuedEngine: false, queuedModel: false, queuedEffort: false };
    for (const item of this.queued || []) {
      if (!item || typeof item !== "object") continue;
      if (item.kind === "engine") {
        out.engine = item.engine || ""; out.queuedEngine = true;
        out.model = item.model || ""; out.queuedModel = true;
        out.effort = item.effort || ""; out.queuedEffort = true;
        continue;
      }
      /* a row whose validating engine no longer matches its position is an
         orphan the node will skip - reading it here would promise the wrong
         configuration */
      if (item.engine && item.engine !== out.engine) continue;
      if ("model" in item) { out.model = item.model || ""; out.queuedModel = true; }
      if ("effort" in item) { out.effort = item.effort || ""; out.queuedEffort = true; }
    }
    return out;
  }

  composerChoiceSpec(kind, native = false) {
    const s = this.session || {};
    const eff = this.effectiveConfig();
    /* model/effort choices follow the queue (a pending switch means the next
       prompt runs on its target); permission stays with the session's own
       engine because it applies immediately, and an applied switch resets it
       to the target's default anyway */
    const eng = engineInfo(this.tab.bid, eff.engine || s.engine);
    if (kind === "perm") {
      const cur = engineInfo(this.tab.bid, s.engine);
      return {
        options: [...((cur && cur.permission_options) || [])],
        selected: s.permission_mode || "",
      };
    }
    if (kind === "effort") return {
      options: effortOptionsForModel(eng, eff.model || ""),
      selected: eff.effort || "",
    };
    const options = [...((eng && eng.model_options) || [])];
    const current = eff.model || "";
    const custom = !!current && !options.some(option => option.value === current);
    const customAllowed = !eng || eng.allow_custom_model !== false;
    if (native && custom) {
      options.push({ value: "__current_custom__", label: `Current: ${current}` });
      if (customAllowed) options.push({ value: "__custom__", label: "Custom…" });
      return { options, selected: "__current_custom__" };
    }
    if (customAllowed) {
      options.push({
        value: "__custom__", label: custom ? `Custom… (${current})` : "Custom…"
      });
      return { options, selected: custom ? "__custom__" : current };
    }
    if (custom) options.unshift({
      value: "__current_custom__", label: `Current: ${current}`, disabled: true
    });
    return { options, selected: custom ? "__current_custom__" : current };
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
    if (closeAllMenus(anchor)) return null;
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
    const current = this.effectiveConfig().model || "";
    const val = await modalPrompt("Model override",
      "Model for this session (empty = engine default).", current);
    if (val !== null) await this.patchSession({ model: val.trim() });
  }

  showEffortMenu(anchor) {
    if (!this.session) return;
    const spec = this.composerChoiceSpec("effort");
    if (!spec.options.length) { toast("Engine has no effort levels", "info"); return; }
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
      this.session = r.session; renderSidebar(); toast(this.session.archived ? "Archived" : "Unarchived");
    } catch (e) { toast(e.message, "error"); }
  }

  async deleteSession() {
    const ok = await modalConfirm("Delete session?", sessionDeleteMessage(this.session));
    if (!ok) return;
    try {
      await api(this.tab.bid, `sessions/${this.tab.sid}`, { method: "DELETE" });
      closeTab(this.tab.id);
      if (this.tab.bid) refreshGroup(this.tab.bid);
      toast("Session deleted");
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
      toast(wasMissing ? "Scratch workspace recreated" : "Scratch workspace reset");
    } catch (e) { toast(e.message, "error"); }
  }
}

/* ================= TermView ================= */
class TermView {
  constructor(tab) {
    this.tab = tab;
    this.closed = false;
    this.connectionSequence = 0;
    this.bindingKnown = Number(tab.sid) > 0;
    this.binding = { sessionId: Number(tab.sid) || null, sessionName: "" };
    this.linkBusy = "";
    this.nodeEnded = tab.ended === true;
    this.root = el("div", "view term");
    this.root.innerHTML = `<div class="br-meta term-meta hidden">
        <div class="br-ident" aria-label="Terminal identity">
          <span class="br-ident-label">Terminal</span>
          <span class="br-id term-id"></span>
          <button class="icon-btn br-copy-id term-copy-id" type="button"
            aria-label="Copy Terminal ID"></button>
        </div>
        <button class="br-owner term-owner" type="button" disabled aria-haspopup="menu"
          aria-expanded="false">
          <svg class="br-owner-glyph" viewBox="0 0 16 16" width="14" height="14" fill="none"
            stroke="currentColor" stroke-width="1.35" stroke-linecap="round"
            stroke-linejoin="round" aria-hidden="true">
            <path d="M6.2 10.8 5 12a2.5 2.5 0 0 1-3.5-3.5l2.2-2.2a2.5 2.5 0 0 1 3.5 0"/>
            <path d="m9.8 5.2 1.2-1.2a2.5 2.5 0 0 1 3.5 3.5l-2.2 2.2a2.5 2.5 0 0 1-3.5 0"/>
            <path d="m5.8 5.8 4.4 4.4"/>
          </svg>
          <span class="sess-dot br-owner-dot hidden"></span>
          <span class="br-owner-text">Checking session link…</span>
          <span class="br-owner-arrow hidden"></span>
        </button>
      </div>
      <div class="term-wrap"><div class="term-host"></div></div>`;
    this.host = this.root.querySelector(".term-host");
    this.meta = this.root.querySelector(".term-meta");
    this.idText = this.root.querySelector(".term-id");
    this.copyIdBtn = this.root.querySelector(".term-copy-id");
    this.copyIdBtn.appendChild(copyIcon());
    this.ownerBtn = this.root.querySelector(".term-owner");
    this.ownerText = this.root.querySelector(".br-owner-text");
    this.ownerGlyph = this.root.querySelector(".br-owner-glyph");
    this.ownerDot = this.root.querySelector(".br-owner-dot");
    this.ownerArrow = this.root.querySelector(".br-owner-arrow");
    this.ownerArrow.appendChild(choiceSvg("arrow"));
    this.started = false;
    this.renderBinding();
  }
  onShow(focus = true) {
    this.renderBinding();
    if (!this.started) { this.started = true; this.start(); }
    else if (this.fit) setTimeout(() => this.fit.fit(), 30);
    if (focus && this.term && !this.isDead()) this.term.focus();
  }
  isDead() { return !!this.root.querySelector(".term-dead"); }

  applyBinding(payload) {
    const sessionId = Number(payload && payload.session_id) || null;
    const previous = Number(this.tab.sid) || null;
    this.bindingKnown = true;
    this.binding = {
      sessionId,
      sessionName: String(payload && payload.session_name || ""),
    };
    if (sessionId) this.tab.sid = sessionId;
    else delete this.tab.sid;
    this.renderBinding();
    if (previous !== sessionId) {
      saveTabs();
      syncSessionBrowserChips();
    }
  }

  renderBinding() {
    const terminalId = String(this.tab.terminalId || "").toUpperCase();
    this.meta.classList.toggle("hidden", !terminalId || !terminalInstancesFor(this.tab.bid));
    if (!terminalId) return;
    this.idText.textContent = terminalId;
    const set = (text, { disabled = false, dot = "", glyph = false,
                         arrow = false, aria = "" } = {}) => {
      this.ownerText.textContent = text;
      this.ownerBtn.disabled = disabled;
      this.ownerBtn.setAttribute("aria-label", aria || text);
      this.ownerGlyph.classList.toggle("hidden", !glyph);
      this.ownerDot.classList.toggle("hidden", !dot);
      if (dot) this.ownerDot.style.color = dot;
      this.ownerArrow.classList.toggle("hidden", !arrow);
    };
    if (!terminalHandoffFor(this.tab.bid)) {
      set("Session linking requires an updated backend", { disabled: true, glyph: true });
      return;
    }
    if (this.tab.terminalGone === true || this.tab.ended === true) {
      set("Terminal ended", { disabled: true, glyph: true });
      return;
    }
    if (this.linkBusy) {
      set(this.linkBusy === "unlink" ? "Unlinking…" : "Linking…",
        { disabled: true, glyph: true });
      return;
    }
    if (!this.bindingKnown) {
      set("Checking session link…", { disabled: true, glyph: true });
      return;
    }
    const ownerId = Number(this.binding.sessionId) || null;
    if (!ownerId) {
      set("Link to a session…", { glyph: true, arrow: true,
        aria: "Link this terminal to a session" });
      return;
    }
    const owner = findSessionMeta(this.tab.bid, ownerId);
    const ownerName = owner ? (owner.name || `Session ${ownerId}`) :
      (this.binding.sessionName || `Session ${ownerId}`);
    set(ownerName, { dot: (owner && owner.color) || "var(--txt3)", arrow: true,
      aria: `Linked to ${ownerName} · move or unlink` });
  }

  async changeBinding(sessionId) {
    const terminalId = String(this.tab.terminalId || "").toUpperCase();
    if (!terminalId || !terminalHandoffFor(this.tab.bid) || this.linkBusy) return;
    if ((Number(sessionId) || null) === (Number(this.binding.sessionId) || null)) return;
    this.linkBusy = sessionId ? "link" : "unlink";
    this.renderBinding();
    try {
      const path = `terminal/instances/${encodeURIComponent(terminalId)}/binding`;
      const result = sessionId ? await api(this.tab.bid, path, {
        method: "POST", body: { session_id: sessionId }, timeoutMs: 15000,
      }) : await api(this.tab.bid, path, { method: "DELETE", timeoutMs: 15000 });
      this.linkBusy = "";
      this.applyBinding(result);
      const name = sessionId && findSessionMeta(this.tab.bid, sessionId);
      toast(sessionId ? `Terminal ${terminalId} linked to ${name ? name.name : "session"}` :
        `Terminal ${terminalId} unlinked`, "ok");
    } catch (error) {
      this.linkBusy = "";
      toast(`Terminal ${terminalId}: ${error.message}`, "error", 7000);
    } finally {
      this.renderBinding();
    }
  }

  showLinkMenu(anchor) {
    if (closeAllMenus(anchor)) return;
    const bid = this.tab.bid;
    const ownerId = Number(this.binding.sessionId) || null;
    const menu = el("div", "menu dyn br-link-menu");
    const scroll = el("div", "br-link-scroll");
    menu.appendChild(scroll);
    menu._anchor = anchor;
    menu._ownerView = this.root;
    anchor.setAttribute("aria-expanded", "true");
    const add = (label, fn) => {
      const b = el("button", "", label);
      b.type = "button";
      b.onclick = e => { e.stopPropagation(); menu.remove(); fn(); };
      scroll.appendChild(b);
    };
    const sessions = sessionsFor(bid).filter(s => !s.archived || s.id === ownerId);
    if (!sessions.length) {
      const none = el("button", "", "No sessions on this backend");
      none.type = "button"; none.disabled = true; scroll.appendChild(none);
    }
    for (const s of sessions) {
      const linked = s.id === ownerId;
      const row = el("button", "br-link-sess");
      row.type = "button";
      row.setAttribute("role", "menuitemradio");
      row.setAttribute("aria-checked", linked ? "true" : "false");
      row.appendChild(sessDot(s));
      row.appendChild(el("span", "menu-check-label", s.name || `Session ${s.id}`));
      const mark = el("span", "br-link-mark");
      if (linked) mark.appendChild(choiceSvg("check"));
      row.appendChild(mark);
      row.onclick = e => {
        e.stopPropagation(); menu.remove(); this.changeBinding(s.id);
      };
      scroll.appendChild(row);
    }
    if (ownerId) {
      scroll.appendChild(el("div", "menu-sep"));
      add("Unlink terminal", () => this.changeBinding(null));
    }
    document.body.appendChild(menu);
    positionAnchoredMenu(menu, anchor);
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
    this.copyIdBtn.onclick = async () => {
      const terminalId = String(this.tab.terminalId || "").toUpperCase();
      if (!terminalId || !await copyWithToast(
          terminalId, `Terminal ${terminalId} ID copied`)) return;
      clearTimeout(this.copyIdBtn._copyReset);
      this.copyIdBtn.replaceChildren(copyIcon(true));
      this.copyIdBtn._copyReset = setTimeout(() => {
        if (this.copyIdBtn.isConnected) this.copyIdBtn.replaceChildren(copyIcon());
      }, 1400);
    };
    this.ownerBtn.onclick = e => {
      e.stopPropagation(); this.showLinkMenu(this.ownerBtn);
    };
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
  async createInstance(sequence, ownerSession = null) {
    const body = {
      command: this.tab.cmd || "", cwd: this.tab.cwd || "",
      cols: this.term.cols, rows: this.term.rows,
    };
    if (Number(ownerSession) > 0) body.session_id = Number(ownerSession);
    const result = await api(this.tab.bid, "terminal/instances", {
      method: "POST", body, timeoutMs: 30000,
    });
    const terminalId = String(result && result.terminal && result.terminal.id || "").toUpperCase();
    if (!/^[A-Z0-9]{4}$/.test(terminalId))
      throw new Error("backend returned an invalid Terminal ID");
    if (this.closed || sequence !== this.connectionSequence) {
      api(this.tab.bid, `terminal/instances/${encodeURIComponent(terminalId)}`,
        { method: "DELETE" }).catch(() => {});
      return false;
    }
    this.tab.terminalId = terminalId;
    this.tab.terminalGone = false;
    this.tab.ended = false;
    this.nodeEnded = false;
    this.bindingKnown = false;
    if (result && Object.prototype.hasOwnProperty.call(result, "session_id"))
      this.applyBinding(result);
    this.tab.title = terminalTabTitle(this.tab);
    saveTabs(); renderTabs(); this.renderBinding();
    return true;
  }

  async connect() {
    if (this.tab.bid && !backendConnectionAllowed(this.tab.bid)) {
      this.showDead(false);
      return;
    }
    const sequence = ++this.connectionSequence;
    if (this.dataSub) { this.dataSub.dispose(); this.dataSub = null; }
    const identified = terminalInstancesFor(this.tab.bid);
    if (identified && !this.tab.terminalId) {
      try {
        if (!await this.createInstance(sequence, this.tab.sid)) return;
      } catch (error) {
        if (sequence !== this.connectionSequence || this.closed) return;
        this.deadReason = error.message || "Could not open terminal";
        this.showDead(false);
        return;
      }
    }
    const params = new URLSearchParams({ cols: this.term.cols, rows: this.term.rows });
    if (this.tab.cmd) params.set("cmd", this.tab.cmd);
    if (this.tab.cwd) params.set("cwd", this.tab.cwd);
    const path = identified ?
      `ws/terminal/${encodeURIComponent(this.tab.terminalId)}` :
      "ws/term?" + params.toString();
    const ws = new WebSocket(wsUrl(this.tab.bid, path));
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    const enc = new TextEncoder();
    ws.onopen = () => {
      if (this.closed || sequence !== this.connectionSequence || this.ws !== ws) {
        try { ws.close(); } catch (error) {}
        return;
      }
      noteRemoteSocketReachable(this.tab.bid);
      this.deadReason = "";
      this.sendResize();
      if (state.active === this.tab.id && isTabVisible(this.tab.id)) this.term.focus();
      this.dataSub = this.term.onData(d => { if (ws.readyState === 1) ws.send(enc.encode(d)); });
    };
    ws.onmessage = (ev) => {
      if (sequence !== this.connectionSequence || this.ws !== ws) return;
      if (typeof ev.data === "string") {
        let data;
        try { data = JSON.parse(ev.data); } catch (error) { return; }
        if (data.type === "binding") this.applyBinding(data);
        else if (data.type === "status") {
          const terminalId = String(data.terminal_id || "").toUpperCase();
          if (/^[A-Z0-9]{4}$/.test(terminalId) && terminalId !== this.tab.terminalId) {
            this.tab.terminalId = terminalId;
            this.tab.title = terminalTabTitle(this.tab);
            saveTabs(); renderTabs(); this.renderBinding();
          }
          if (data.replay_truncated && !this.replayWarned) {
            this.replayWarned = true;
            toast(`Terminal ${terminalId}: earlier output was omitted from reconnect replay`,
              "error", 7000);
          }
          if (data.running === false) {
            this.nodeEnded = true;
            this.deadReason = data.reason || "Terminal ended";
            this.showDead(true);
          }
        } else if (data.type === "agent_input") {
          this.meta.classList.add("agent-active");
          clearTimeout(this.agentActiveTimer);
          this.agentActiveTimer = setTimeout(() =>
            this.meta.classList.remove("agent-active"), 1200);
        } else if (data.type === "error") {
          this.nodeEnded = true;
          this.tab.terminalGone = true;
          this.deadReason = data.text || "Terminal unavailable";
          this.showDead(true);
        }
        return;
      }
      noteRemoteSocketReachable(this.tab.bid);
      this.term.write(new Uint8Array(ev.data));
    };
    ws.onclose = () => {
      if (sequence !== this.connectionSequence || this.ws !== ws) return;
      this.ws = null;
      if (this.dataSub) { this.dataSub.dispose(); this.dataSub = null; }
      if (!this.closed) this.showDead(this.nodeEnded || !identified);
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
      toast("Could not copy the selection to the clipboard", "error");
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
      toast("Clipboard paste was blocked · use Shift+right-click for the browser menu", "error", 7000);
    }
  }

  async replaceTerminal(dead) {
    if (this.tab.bid && !backendConnectionAllowed(this.tab.bid)) return;
    if (!terminalInstancesFor(this.tab.bid)) {
      this.tab.ended = false;
      this.nodeEnded = false;
      saveTabs(); dead.remove(); this.term.reset();
      this.term.write("\x1b[?25h"); this.connect(); this.term.focus();
      return;
    }
    const oldId = String(this.tab.terminalId || "").toUpperCase();
    const owner = Number(this.binding.sessionId) || null;
    const sequence = ++this.connectionSequence;
    const button = dead.querySelector(".term-dead-new");
    button.disabled = true; button.textContent = "Opening…";
    try {
      if (!await this.createInstance(sequence, owner)) return;
      if (oldId) api(this.tab.bid,
        `terminal/instances/${encodeURIComponent(oldId)}`,
        { method: "DELETE" }).catch(() => {});
      dead.remove(); this.term.reset(); this.term.write("\x1b[?25h");
      this.connect(); this.term.focus(); syncSessionBrowserChips();
    } catch (error) {
      this.tab.terminalId = oldId;
      this.tab.title = terminalTabTitle(this.tab);
      saveTabs(); renderTabs(); this.renderBinding();
      this.deadReason = error.message || "Could not open terminal";
      button.disabled = false; this.syncRemoteState();
    }
  }

  showDead(markEnded = true) {
    if (markEnded && this.tab.ended !== true) {
      this.tab.ended = true; saveTabs(); syncSessionBrowserChips(); this.renderBinding();
    }
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
      if (this.tab.bid && !backendConnectionAllowed(this.tab.bid)) return;
      if (this.tab.ended === true || this.nodeEnded) this.replaceTerminal(d);
      else {
        d.remove(); this.term.reset(); this.term.write("\x1b[?25h");
        this.connect(); this.term.focus();
      }
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
  handleNodeStopping() {
    this.showDead(false);
  }
  clearNodeStopping() {
    this.syncRemoteState();
  }
  syncRemoteState() {
    const unavailable = !!this.tab.bid && !backendConnectionAllowed(this.tab.bid);
    if (unavailable && this.ws) {
      const ws = this.ws;
      this.ws = null;
      this.connectionSequence++;
      try { ws.close(); } catch (error) {}
    }
    if (unavailable && !this.isDead()) this.showDead(false);
    const dead = this.root.querySelector(".term-dead");
    if (!dead) return;
    const message = dead.querySelector(".term-dead-message");
    const button = dead.querySelector(".term-dead-new");
    message.textContent = remoteStoppingMessage(this.tab.bid) ||
      (unavailable ? "Backend unavailable" : this.deadReason ||
       (this.tab.ended || this.nodeEnded ? "Terminal ended" : "Terminal disconnected"));
    button.textContent = unavailable ? "Waiting for backend…" :
      (this.tab.ended || this.nodeEnded ? "New shell" : "Reconnect");
    button.disabled = unavailable;
  }
  destroy() {
    this.closed = true;
    this.connectionSequence++;
    if (this.resizeObs) this.resizeObs.disconnect();
    if (this.onSelectDown) this.host.removeEventListener("mousedown", this.onSelectDown);
    if (this.onSelectUp) document.removeEventListener("mouseup", this.onSelectUp);
    if (this.onContextMenu) this.host.removeEventListener("contextmenu", this.onContextMenu);
    clearTimeout(this.agentActiveTimer);
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
    this.waitingForBackend = false;
    this.frameW = 1280;
    this.frameH = 800;
    this.frameUrl = null;
    this.urlFocused = false;
    this.lastUrl = "";
    this.moveQueued = null;
    this.lastViewport = "";
    this.bindingKnown = Number(tab.sid) > 0;
    this.binding = {
      sessionId: Number(tab.sid) || null,
      sessionName: "",
    };
    this.viewportTimer = null;
    this.resizeObs = null;
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
      <div class="br-meta">
        <div class="br-ident" aria-label="Browser identity">
          <span class="br-ident-label">Browser</span>
          <span class="br-id"></span>
          <button class="icon-btn br-copy-id" type="button" aria-label="Copy Browser ID"></button>
        </div>
        <button class="br-owner" type="button" disabled aria-haspopup="menu"
          aria-expanded="false">
          <svg class="br-owner-glyph" viewBox="0 0 16 16" width="14" height="14" fill="none"
            stroke="currentColor" stroke-width="1.35" stroke-linecap="round"
            stroke-linejoin="round" aria-hidden="true">
            <path d="M6.2 10.8 5 12a2.5 2.5 0 0 1-3.5-3.5l2.2-2.2a2.5 2.5 0 0 1 3.5 0"/>
            <path d="m9.8 5.2 1.2-1.2a2.5 2.5 0 0 1 3.5 3.5l-2.2 2.2a2.5 2.5 0 0 1-3.5 0"/>
            <path d="m5.8 5.8 4.4 4.4"/>
          </svg>
          <span class="sess-dot br-owner-dot hidden"></span>
          <span class="br-owner-text">Checking session link…</span>
          <span class="br-owner-arrow hidden"></span>
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
    this.meta = this.root.querySelector(".br-meta");
    this.idText = this.root.querySelector(".br-id");
    this.copyIdBtn = this.root.querySelector(".br-copy-id");
    this.copyIdBtn.appendChild(copyIcon());
    this.ownerBtn = this.root.querySelector(".br-owner");
    this.ownerText = this.root.querySelector(".br-owner-text");
    this.ownerGlyph = this.root.querySelector(".br-owner-glyph");
    this.ownerDot = this.root.querySelector(".br-owner-dot");
    this.ownerArrow = this.root.querySelector(".br-owner-arrow");
    this.ownerArrow.appendChild(choiceSvg("arrow"));
    this.linkBusy = "";
    this.typeRow = this.root.querySelector(".br-type");
    this.stage = this.root.querySelector(".br-stage");
    this.screen = this.root.querySelector(".br-screen");
    this.ime = this.root.querySelector(".br-ime");
    this.renderBinding();
  }

  onShow(focus = true) {
    this.renderBinding();
    if (!this.started) { this.started = true; this.start(); }
    else this.queueViewport();
    if (!focus || this.isDead()) return;
    if (this.typeRow.classList.contains("hidden")) this.stage.focus({ preventScroll: true });
    else this.ime.focus({ preventScroll: true });
  }
  isDead() { return !!this.root.querySelector(".br-dead"); }

  applyBinding(payload) {
    const sessionId = Number(payload && payload.session_id) || null;
    const previous = Number(this.tab.sid) || null;
    this.bindingKnown = true;
    this.binding = {
      sessionId,
      sessionName: String(payload && payload.session_name || ""),
    };
    if (sessionId) this.tab.sid = sessionId;
    else delete this.tab.sid;
    this.renderBinding();
    if (previous !== sessionId) {
      saveTabs();
      syncSessionBrowserChips();
    }
  }

  /* One pill tells the whole linking story: who owns this browser, that the
     pill opens the session picker (chevron), and why it is unavailable when
     it is. Every state writes all four pieces - glyph, dot, text, chevron -
     so states can never bleed into each other. */
  renderBinding() {
    const browserId = String(this.tab.browserId || "").toUpperCase();
    this.meta.classList.toggle("hidden", !browserId);
    if (!browserId) return;
    this.idText.textContent = browserId;
    const set = (text, { disabled = false, dot = "", glyph = false,
                         arrow = false, aria = "" } = {}) => {
      this.ownerText.textContent = text;
      this.ownerBtn.disabled = disabled;
      this.ownerBtn.setAttribute("aria-label", aria || text);
      this.ownerGlyph.classList.toggle("hidden", !glyph);
      this.ownerDot.classList.toggle("hidden", !dot);
      if (dot) this.ownerDot.style.color = dot;
      this.ownerArrow.classList.toggle("hidden", !arrow);
    };
    if (!browserHandoffFor(this.tab.bid)) {
      set("Session linking requires an updated backend", { disabled: true, glyph: true });
      return;
    }
    if (this.tab.browserGone === true) {
      set("Browser closed", { disabled: true, glyph: true });
      return;
    }
    if (this.linkBusy) {
      set(this.linkBusy === "unlink" ? "Unlinking…" : "Linking…",
        { disabled: true, glyph: true });
      return;
    }
    if (!this.bindingKnown) {
      set("Checking session link…", { disabled: true, glyph: true });
      return;
    }
    const ownerId = Number(this.binding.sessionId) || null;
    if (!ownerId) {
      set("Link to a session…", { glyph: true, arrow: true,
        aria: "Link this browser to a session" });
      return;
    }
    const owner = findSessionMeta(this.tab.bid, ownerId);
    const ownerName = owner ? (owner.name || `Session ${ownerId}`) :
      (this.binding.sessionName || `Session ${ownerId}`);
    set(ownerName, { dot: (owner && owner.color) || "var(--txt3)", arrow: true,
      aria: `Linked to ${ownerName} · move or unlink` });
  }

  async changeBinding(sessionId) {
    const browserId = String(this.tab.browserId || "").toUpperCase();
    if (!browserId || !browserHandoffFor(this.tab.bid) || this.linkBusy) return;
    if ((Number(sessionId) || null) === (Number(this.binding.sessionId) || null))
      return;   // picking the already-linked session changes nothing
    this.linkBusy = sessionId ? "link" : "unlink";
    this.renderBinding();
    try {
      const path = `browser/instances/${encodeURIComponent(browserId)}/binding`;
      const result = sessionId ? await api(this.tab.bid, path, {
        method: "POST", body: { session_id: sessionId }, timeoutMs: 15000,
      }) : await api(this.tab.bid, path, { method: "DELETE", timeoutMs: 15000 });
      this.linkBusy = "";
      this.applyBinding(result);
      const name = sessionId && findSessionMeta(this.tab.bid, sessionId);
      toast(sessionId ? `Browser ${browserId} linked to ${name ? name.name : "session"}` :
        `Browser ${browserId} unlinked`, "ok");
    } catch (error) {
      this.linkBusy = "";
      toast(`Browser ${browserId}: ${error.message}`, "error", 7000);
    } finally {
      this.renderBinding();
    }
  }

  /* The whole linking flow lives here, on the browser's own tab: pick any
     session on this backend to link or move the browser (the node swaps
     bindings atomically), or unlink - no dependency on which chat happens to
     be selected elsewhere. */
  showLinkMenu(anchor) {
    if (closeAllMenus(anchor)) return;
    const bid = this.tab.bid;
    const ownerId = Number(this.binding.sessionId) || null;
    const menu = el("div", "menu dyn br-link-menu");
    /* Keep the native scrollport inside the framed menu. If the border and
       scrollport share an element, Chromium paints the thumb through the
       rounded top/bottom border when the list reaches either end. */
    const scroll = el("div", "br-link-scroll");
    menu.appendChild(scroll);
    menu._anchor = anchor;
    menu._ownerView = this.root;
    anchor.setAttribute("aria-expanded", "true");
    const add = (label, fn) => {
      const b = el("button", "", label);
      b.type = "button";
      b.onclick = (e) => { e.stopPropagation(); menu.remove(); fn(); };
      scroll.appendChild(b);
      return b;
    };
    /* the user's own sidebar order; archived chats are offered only while one
       still holds the link, so its checked row always exists */
    const sessions = sessionsFor(bid).filter(s => !s.archived || s.id === ownerId);
    if (!sessions.length) {
      const none = el("button", "", "No sessions on this backend");
      none.type = "button";
      none.disabled = true;
      scroll.appendChild(none);
    }
    for (const s of sessions) {
      const linked = s.id === ownerId;
      /* pick-one rows wear the plain check of the choice menus, not the
         drawn checkbox settings toggles use - only the linked row carries
         a mark, every row reserves its column so labels align */
      const row = el("button", "br-link-sess");
      row.type = "button";
      row.setAttribute("role", "menuitemradio");
      row.setAttribute("aria-checked", linked ? "true" : "false");
      row.appendChild(sessDot(s));
      row.appendChild(el("span", "menu-check-label", s.name || `Session ${s.id}`));
      const mark = el("span", "br-link-mark");
      if (linked) mark.appendChild(choiceSvg("check"));
      row.appendChild(mark);
      row.onclick = (e) => {
        e.stopPropagation();
        menu.remove();
        this.changeBinding(s.id);
      };
      scroll.appendChild(row);
    }
    if (ownerId) {
      scroll.appendChild(el("div", "menu-sep"));
      add("Unlink browser", () => this.changeBinding(null));
    }
    /* on <body> like every dynamic menu: the pane stacking context would
       otherwise paint neighbours over it in a split */
    document.body.appendChild(menu);
    positionAnchoredMenu(menu, anchor);
  }

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
  /* Chromium receives CSS pixels, not device pixels: responsive pages should
     see exactly the room the WebUI gives them without making retina phones
     stream an unnecessarily huge image. Oversized panes retain their aspect. */
  viewportSize() {
    let width = Math.floor(this.stage.clientWidth);
    let height = Math.floor(this.stage.clientHeight);
    if (width < 160 || height < 120) return null;
    const scale = Math.min(1, 3840 / width, 2160 / height);
    width = Math.round(width * scale);
    height = Math.round(height * scale);
    if (width < 160 || height < 120) return null;
    return { width, height };
  }
  sendViewport(force = false) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const size = this.viewportSize();
    if (!size) return;
    const key = `${size.width}x${size.height}`;
    if (!force && key === this.lastViewport) return;
    this.ws.send(JSON.stringify({ type: "viewport", ...size }));
    this.lastViewport = key;
  }
  queueViewport() {
    if (this.viewportTimer !== null) clearTimeout(this.viewportTimer);
    this.viewportTimer = setTimeout(() => {
      this.viewportTimer = null;
      this.sendViewport();
    }, 80);
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
    this.resizeObs = new ResizeObserver(() => this.queueViewport());
    this.resizeObs.observe(this.stage);
    this.copyIdBtn.onclick = async () => {
      const browserId = String(this.tab.browserId || "").toUpperCase();
      if (!browserId || !await copyWithToast(browserId, `Browser ${browserId} ID copied`))
        return;
      clearTimeout(this.copyIdBtn._copyReset);
      this.copyIdBtn.replaceChildren(copyIcon(true));
      this.copyIdBtn._copyReset = setTimeout(() => {
        if (this.copyIdBtn.isConnected) this.copyIdBtn.replaceChildren(copyIcon());
      }, 1400);
    };
    this.ownerBtn.onclick = (e) => {
      e.stopPropagation();
      this.showLinkMenu(this.ownerBtn);
    };
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
    if (this.closed) return;
    if (this.tab.bid && !backendConnectionAllowed(this.tab.bid)) {
      this.waitingForBackend = true;
      this.showDead(remoteStoppingMessage(this.tab.bid) || "Backend unavailable", false);
      this.syncRemoteState();
      return;
    }
    this.waitingForBackend = false;
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
      this.lastViewport = "";
      this.sendViewport(true);
    };
    ws.onmessage = ev => {
      if (sequence !== this.connectionSequence || this.ws !== ws) return;
      noteRemoteSocketReachable(this.tab.bid);
      const stopping = remoteStoppingMessage(this.tab.bid);
      if (stopping) { this.showDead(stopping, true); return; }
      if (typeof ev.data !== "string") { this.showFrame(ev.data); return; }
      let d = null;
      try { d = JSON.parse(ev.data); } catch (error) { return; }
      if (d.type === "status") {
        this.lastUrl = d.url || "";
        if (!this.urlFocused)
          this.urlInput.value = this.lastUrl === "about:blank" ? "" : this.lastUrl;
        this.backBtn.disabled = !d.can_back;
        this.clearDead();
      } else if (d.type === "frame_meta") {
        this.frameW = Number(d.width) || this.frameW;
        this.frameH = Number(d.height) || this.frameH;
      } else if (d.type === "binding") {
        this.applyBinding(d);
      } else if (d.type === "dialog") {
        toast(`Page ${d.kind || "dialog"} ${d.action}: ${d.message || ""}`.trim(),
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
      if (!this.closed) {
        const stopping = remoteStoppingMessage(this.tab.bid);
        this.showDead(stopping || "Connection closed", !!stopping);
      }
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

  handleNodeStopping(message) {
    this.showDead(message, true);
    this.syncRemoteState();
  }

  clearNodeStopping() {
    const dead = this.root.querySelector(".br-dead");
    if (dead) {
      if (dead.querySelector(".term-dead-message").textContent.includes("Backend"))
        dead.querySelector(".term-dead-message").textContent = "Browser ended";
      const reconnect = dead.querySelector(".btn-pri");
      reconnect.textContent = "Reconnect";
      reconnect.disabled = false;
    }
  }

  syncRemoteState() {
    const stopping = remoteStoppingMessage(this.tab.bid);
    const unavailable = !!this.tab.bid && !backendConnectionAllowed(this.tab.bid);
    if (unavailable) {
      this.waitingForBackend = true;
      if (this.ws) {
        const ws = this.ws;
        this.ws = null;
        this.connectionSequence++;
        try { ws.close(); } catch (error) {}
      }
      this.showDead(stopping || "Backend unavailable", !!stopping);
    } else if (this.waitingForBackend) {
      this.waitingForBackend = false;
      this.clearDead();
      this.connect();
      return;
    } else if (stopping) {
      this.showDead(stopping, true);
    }
    const reconnect = this.root.querySelector(".br-dead .btn-pri");
    if (reconnect) {
      reconnect.textContent = unavailable ? "Waiting for backend…" : "Reconnect";
      reconnect.disabled = unavailable;
    }
  }

  destroy() {
    this.closed = true;
    this.connectionSequence++;
    if (this.resizeObs) { this.resizeObs.disconnect(); this.resizeObs = null; }
    if (this.viewportTimer !== null) {
      clearTimeout(this.viewportTimer);
      this.viewportTimer = null;
    }
    if (this.ws) { try { this.ws.close(); } catch (e) {} }
    this.ws = null;
    if (this.frameUrl) { URL.revokeObjectURL(this.frameUrl); this.frameUrl = null; }
    /* the link menu sits on <body>, so closing this view does not take it */
    for (const menu of document.querySelectorAll(".menu.dyn"))
      if (menu._ownerView === this.root) menu.remove();
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
    this.autoUpgradeRows = new Map();
    this.uploadRows = new Map();
    this.remoteBrowserToggles = new Map();
    this.upgradeButtons = new Map();
    this.backendAutoToggles = new Map();
    this.upgradeReadiness = new Map();
    this.upgradesInProgress = new Set();
    this.upgradePollTimer = null;
    this.upgradePollGeneration = 0;
    this.engineUpgradeState = new Map();   // "bid:engine" -> "running" | "idle"
    this.engineUpgradeStarts = new Set();  // POST accepted or rejected asynchronously
    this.engineUpgradePollTimer = null;
    this.engineUpgradePollGeneration = 0;
    this.localEngineGroup = null;
    this.systemPromptSync = null;
    this.root = el("div", "view settings");
    this.root.innerHTML = `<div class="settings-scroll"><div class="settings-inner"></div></div>`;
    this.inner = this.root.querySelector(".settings-inner");
  }
  destroy() {
    this.renderGeneration++;
    this.stopUpgradeReadinessPolling();
    this.stopEngineUpgradePolling();
    this.engineUpgradeState.clear();
    this.engineUpgradeStarts.clear();
    this.remoteEngineGroups.clear();
    this.remoteBackendDots.clear();
    this.remoteBackendMeta.clear();
    this.usageRows.clear();
    this.autoUpgradeRows.clear();
    this.uploadRows.clear();
    this.remoteBrowserToggles.clear();
    this.upgradeButtons.clear();
    this.backendAutoToggles.clear();
    this.upgradeReadiness.clear();
    this.upgradesInProgress.clear();
    this.localEngineGroup = null;
    this.systemPromptSync = null;
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
        if (engine.upgrade_state === "running" || this.engineUpgradeStarts.has(id)) {
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
        if (bid && !backendConnectionAllowed(bid)) return;
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
        !this.upgradesInProgress.has(record.backend.id) &&
        backendConnectionAllowed(record.backend.id));
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
      group.setMeta(backend);
      const reachable = state.remoteOk[bid];
      const cached = Object.prototype.hasOwnProperty.call(state.engCache, bid) ?
        state.engCache[bid] : null;
      if (reachable === false) {
        group.update({
          status: "bad", engines: cached, message: "Backend unavailable · showing last known values",
          detail: state.remoteErrors[bid] || "The controller cannot reach this backend",
        });
      } else if (reachable === true && cached === null) {
        group.update({
          status: "ok", engines: null,
          message: state.remoteEngineErrors[bid] ?
            "Backend available · retrying engine status…" : "Loading engines…",
          detail: state.remoteEngineErrors[bid] || "",
        });
      } else if (reachable !== true && cached !== null) {
        group.update({
          status: "pending", engines: cached,
          message: "Checking backend · showing last known values",
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
      syncBackendLocation(meta, backend);
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
    for (const [bid, row] of this.autoUpgradeRows) {
      if (!bid) {
        row.update(state.autoUpgrade, "ok", true);
        continue;
      }
      if (!state.backends.some(backend => backend.id === bid)) continue;
      row.update(state.remoteAutoUpgrade[bid] || null,
        remoteAvailability(bid), backendSupportsEngineAutoUpgrade(bid));
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
    for (const [bid, record] of this.remoteBrowserToggles) {
      const availability = remoteAvailability(bid);
      const unavailable = !backendConnectionAllowed(bid);
      record.input.checked = browserEnabledFor(bid);
      if (unavailable) {
        record.input.disabled = true;
        record.root.classList.add("disabled");
        record.root.title = availability === "bad" ?
          "Backend unavailable · showing last known value" :
          "Checking backend · showing last known value";
        record.wasOffline = true;
      } else if (record.wasOffline) {
        record.wasOffline = false;
        this.wireBrowserToggle(
          bid, record.input, null, this.renderGeneration, record.root, record);
      }
    }
    if (this.systemPromptSync) this.systemPromptSync();
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
      e2.installed ? (e2.version || "Installed") : "Not installed");
    /* The pill turns orange the moment an update exists, so the version it is
       behind belongs right there where the eye already is. Only set a tooltip
       when it says something the pill does not: one that merely repeats the
       visible text is noise. */
    const described = e2.update_available === true && e2.latest_version
      ? `${version.textContent} · latest is ${e2.latest_version}`
      : version.textContent;
    version.setAttribute("aria-label", described);
    if (described !== version.textContent) version.title = described;
    statuses.appendChild(version);
    const ready = engineReady(e2);
    statuses.appendChild(el("span", "pill " + (ready ? "ok" : "bad"),
      e2.availability_only === true ? engineStatusText(e2) : "Auth: " + e2.auth));
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
    const id = `${bid}:${e2.key}`;
    const upgrading = e2.upgrade_state === "running";
    const starting = this.engineUpgradeStarts.has(id);
    if (!upgrading && !starting && !(e2.installed && e2.update_available === true)) return null;
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
    } else if (starting) {
      set("Starting…", true, "update is starting");
      button.classList.add("busy");
    } else if (!e2.upgrade_supported || !backendSupportsEngineUpgrade(bid)) {
      set("Update", true, "this backend cannot update its engines from here");
    } else if (bid && !backendConnectionAllowed(bid)) {
      set("Update", true, "the backend is unavailable");
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
    const id = `${bid}:${e2.key}`;
    if (this.engineUpgradeStarts.has(id)) return;
    this.engineUpgradeStarts.add(id);
    this.syncRemoteState();
    let accepted = false;
    try {
      const result = await api(bid, `engines/${encodeURIComponent(e2.key)}/upgrade`,
        { method: "POST", timeoutMs: 30000 });
      accepted = true;
      applyEnginesPayload(bid, result);
      toast(`${nodeName}: updating ${e2.label}…`, "info");
    } catch (error) {
      toast(`${nodeName}: ${error.message}`, "error", 7000);
    } finally {
      this.engineUpgradeStarts.delete(id);
      /* A successful updater can finish before its POST response is painted.
         While starting, syncEngineUpgrades records a logical running edge, so
         this sync reports that fast result exactly like a polled completion. */
      if (!accepted) this.engineUpgradeState.delete(id);
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
    const metaEl = el("span", "engine-node-meta");
    let locationEl = null;
    const setMeta = value => {
      if (value && typeof value === "object" && value.id != null) {
        if (!locationEl) {
          metaEl.innerHTML = "";
          locationEl = backendLocationNode(value);
          locationEl.classList.add("engine-node-location");
          metaEl.appendChild(locationEl);
        } else {
          syncBackendLocation(locationEl, value);
        }
        metaEl.removeAttribute("aria-label");
        return;
      }
      if (locationEl && locationEl._backendUrlTimer)
        clearTimeout(locationEl._backendUrlTimer);
      locationEl = null;
      metaEl.textContent = value || "";
      if (value) metaEl.setAttribute("aria-label", value);
      else metaEl.removeAttribute("aria-label");
    };
    setMeta(meta);
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
      root.classList.toggle("engine-node-offline-values",
        status !== "ok" && Array.isArray(engines));
      if (refresh) refresh.disabled = !!bid && status !== "ok";
      const statusLabel = status === "ok" ? "available" : status === "bad" ? "unavailable" : "checking";
      dot.setAttribute("aria-label", detail ? `${statusLabel}: ${detail}` : statusLabel);
      body.innerHTML = "";
      if (message) {
        const loading = status !== "bad";
        const checkingBackend = loading && status === "pending" && engines === null && !!bid;
        const noteText = message || (checkingBackend ? "Checking backend…" : "Checking engines…");
        const note = el("div", "engine-node-message" +
          (status === "bad" ? " engine-node-unavailable" : "") +
          (loading ? " engine-node-loading" : ""));
        if (loading) {
          const icon = el("span", "engine-node-loading-icon");
          icon.appendChild(refreshIcon(10));
          note.appendChild(icon);
          note.appendChild(el("span", "engine-node-loading-text", noteText));
        } else {
          note.textContent = noteText;
        }
        note.setAttribute("aria-label", detail || note.textContent);
        body.appendChild(note);
      }
      if (engines === null) {
        if (!message) {
          const note = el("div", "engine-node-message engine-node-loading");
          const icon = el("span", "engine-node-loading-icon");
          icon.appendChild(refreshIcon(10));
          note.appendChild(icon);
          note.appendChild(el("span", "engine-node-loading-text",
            status === "pending" && bid ? "Checking backend…" : "Checking engines…"));
          body.appendChild(note);
        }
      } else if (!engines.length) {
        const note = el("div", "engine-node-message engine-node-empty", "No engines reported");
        note.setAttribute("aria-label", note.textContent);
        body.appendChild(note);
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
    return { root, update, setMeta };
  }

  /* One node's unattended engine-update schedule. Off is a single line; on
     reveals when. Every control commits on its own change - no Apply button to
     find on a phone - and the note is the only place an outcome is reported,
     so a failed run cannot pass unnoticed. */
  autoUpgradeRow(name, bid) {
    const root = el("div", "eau-row");
    const identity = el("div", "eau-identity");
    const nameEl = el("div", "eau-name", name);
    nameEl.setAttribute("aria-label", name);
    const note = el("div", "eau-note", "Checking setting…");
    identity.appendChild(nameEl);
    identity.appendChild(note);

    const when = el("div", "eau-when hidden");
    const seg = el("div", "seg");
    seg.setAttribute("role", "group");
    seg.setAttribute("aria-label", `When ${name} installs engine updates`);
    const nowBtn = el("button", "seg-btn", "Right away");
    const atBtn = el("button", "seg-btn", "At");
    nowBtn.type = atBtn.type = "button";
    seg.appendChild(nowBtn);
    seg.appendChild(atBtn);
    const time = document.createElement("input");
    time.type = "time";
    time.className = "eau-time";
    time.setAttribute("aria-label", `Time of day ${name} installs engine updates`);
    when.appendChild(seg);
    when.appendChild(time);

    const toggleRoot = el("label", "be-auto eau-switch");
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.setAttribute("aria-label", `Install engine updates on ${name} automatically`);
    const track = el("span", "be-auto-track");
    track.setAttribute("aria-hidden", "true");
    track.appendChild(el("span"));
    toggleRoot.appendChild(toggle);
    toggleRoot.appendChild(track);

    root.appendChild(identity);
    root.appendChild(when);
    root.appendChild(toggleRoot);

    let current = null;
    let availability = bid ? "pending" : "ok";
    let supported = !bid;
    let saving = false;

    const describe = () => {
      if (!supported) return "Backend upgrade required";
      if (availability === "bad") return current ?
        "Backend unavailable · showing last known setting" : "Backend unavailable";
      if (availability !== "ok") return current ?
        "Checking backend · showing last known setting" : "Checking backend setting…";
      if (!current) return "Checking backend setting…";
      if (saving) return "Saving…";
      const attempts = current.last_attempts || {};
      const keys = Object.keys(attempts);
      if (keys.length) {
        /* the newest attempt is what someone needs to see: a silent failure is
           the whole risk of running this unattended */
        const newest = keys.map(k => ({ key: k, ...attempts[k] }))
          .sort((a, b) => Number(b.at || 0) - Number(a.at || 0))[0];
        const engine = state.engMap[newest.key] ? state.engMap[newest.key].label : newest.key;
        const when = new Date(Number(newest.at) * 1000);
        const stamp = Number.isNaN(when.getTime()) ? "" : ` · ${when.toLocaleString()}`;
        if (!newest.ok) {
          return `${engine} ${newest.to_version || ""} failed${stamp} — will not retry ` +
            `until a newer version appears`;
        }
        return `${engine} updated to ${newest.installed_after || newest.to_version}${stamp}`;
      }
      if (!current.enabled) return "Updates are installed by hand";
      if (current.mode === "at")
        return `Installs in the ${current.window_minutes || 120} minutes after ${current.at}`;
      return "Installs as soon as an update is found";
    };

    const paint = () => {
      const usable = supported && availability === "ok" && !!current && !saving;
      toggle.disabled = !usable;
      toggleRoot.classList.toggle("disabled", !usable);
      const on = !!(current && current.enabled);
      toggle.checked = on;
      when.classList.toggle("hidden", !on);
      const at = !!(current && current.mode === "at");
      nowBtn.classList.toggle("on", !at);
      atBtn.classList.toggle("on", at);
      nowBtn.setAttribute("aria-pressed", at ? "false" : "true");
      atBtn.setAttribute("aria-pressed", at ? "true" : "false");
      nowBtn.disabled = atBtn.disabled = !usable;
      time.disabled = !usable || !at;
      time.classList.toggle("hidden", !at);
      if (current && document.activeElement !== time) time.value = current.at || "03:30";
      note.textContent = describe();
      note.classList.toggle("warn", !!(current && current.last_attempts &&
        Object.values(current.last_attempts).some(a => a && a.ok === false)));
    };

    const commit = async patch => {
      if (saving || !current) return;
      saving = true;
      paint();
      try {
        const result = await api(bid, "engines/auto-upgrade",
          { method: "PATCH", body: patch, timeoutMs: ENGINE_POLL_TIMEOUT });
        current = result.auto_upgrade || current;
        if (bid) state.remoteAutoUpgrade[bid] = current;
        else state.autoUpgrade = current;
      } catch (error) {
        toast(`${name}: ${error.message}`, "error", 6500);
      } finally {
        saving = false;
        paint();
      }
    };

    toggle.onchange = () => commit({ enabled: toggle.checked });
    nowBtn.onclick = () => { if (current && current.mode !== "now") commit({ mode: "now" }); };
    atBtn.onclick = () => { if (current && current.mode !== "at") commit({ mode: "at" }); };
    time.onchange = () => {
      if (current && time.value && time.value !== current.at) commit({ at: time.value });
      else paint();
    };

    const update = (metadata, reachable = "ok", canConfigure = true) => {
      if (metadata && typeof metadata === "object") current = metadata;
      availability = reachable;
      supported = canConfigure;
      paint();
    };
    update(null, availability, supported);
    return { root, update };
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
      if (reachable === "bad") return metadata ?
        "Backend unavailable · showing last known setting" : "Backend unavailable";
      if (reachable !== "ok") return metadata ?
        "Checking backend · showing last known setting" : "Checking backend setting…";
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
      if (metadata && typeof metadata === "object") current = metadata;
      availability = reachable;
      supported = canConfigure;
      if (current && document.activeElement !== interval)
        interval.value = String(current.minutes);
      const disabled = saving || !canConfigure || reachable !== "ok" || !current;
      interval.disabled = disabled;
      save.disabled = disabled;
      const description = describe(current, reachable, canConfigure);
      note.textContent = description;
      note.setAttribute("aria-label", description);
    };
    save.onclick = async () => {
      if (!interval.value.trim()) {
        toast("Enter a refresh interval in minutes", "error");
        interval.focus();
        return;
      }
      const minutes = Number(interval.value);
      if (!Number.isInteger(minutes) || minutes < 0 || minutes > 1440) {
        toast("Refresh interval must be 0 or a whole number from 1 to 1440", "error");
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
        toast(`${name}: Usage refresh saved${suffix}`,
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
      const normalized = normalizeUploadSettings(metadata);
      if (normalized) current = normalized;
      availability = reachable;
      supported = canConfigure;
      if (!saving && current && document.activeElement !== limit)
        limit.value = String(current.max_file_size_mb);
      const disabled = saving || !canConfigure || reachable !== "ok" || !current;
      limit.disabled = disabled;
      save.disabled = disabled;
      let description;
      if (!canConfigure) description = "Backend upgrade required";
      else if (reachable === "bad") description = current ?
        "Backend unavailable · showing last known setting" : "Backend unavailable";
      else if (reachable !== "ok") description = current ?
        "Checking backend · showing last known setting" : "Checking backend setting…";
      else if (!current) description = loadError || "Checking backend setting…";
      else if (!current.enabled) description = "File uploads are disabled";
      else description = `Any file type · ${current.max_file_size_mb} MiB maximum each`;
      note.textContent = description;
      note.setAttribute("aria-label", description);
    };
    const load = async () => {
      if (!supported) return;
      if (bid && !backendConnectionAllowed(bid)) {
        loadError = "Backend unavailable";
        update(current, remoteAvailability(bid), true);
        return;
      }
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
        update(current, bid ? remoteAvailability(bid) : "ok", true);
      }
    };
    save.onclick = async () => {
      if (!limit.value.trim()) {
        toast("Enter a maximum file size in MiB", "error");
        limit.focus();
        return;
      }
      const megabytes = Number(limit.value);
      if (!Number.isInteger(megabytes) || megabytes < 0 || megabytes > 1024) {
        toast("Maximum file size must be a whole number from 0 to 1024 MiB", "error");
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
        toast(`${name}: File uploads ${policy.enabled ? `limited to ${megabytes} MiB` : "disabled"}`, "ok");
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
  async wireBrowserToggle(bid, input, note, generation, root = null, existingRecord = null) {
    const name = backendName(bid);
    const browserRecord = existingRecord || (bid && root ? {
      input, root, wasOffline: false,
    } : null);
    if (browserRecord) this.remoteBrowserToggles.set(bid, browserRecord);
    const setNote = (text, warn) => {
      if (note) {
        note.textContent = text;
        note.classList.toggle("warn", !!warn);
      }
      if (root) root.title = text;
    };
    const apply = st => {
      if (browserRecord) browserRecord.wasOffline = false;
      input.checked = !!st.enabled;
      if (bid) state.remoteBrowser[bid] = { enabled: !!st.enabled };
      input.disabled = !st.available && !st.enabled;
      if (root) {
        root.classList.toggle("disabled", input.disabled);
        root.onclick = input.disabled ?
          () => toast(`${name}: ${st.reason || "No usable browser"}`, "error", 6000) : null;
      }
      if (st.available) {
        setNote((st.product || "Browser available") +
          (st.sandbox === "no-sandbox" ? " · sandbox off (runs as root)" : ""), false);
      } else {
        setNote(st.reason || "No usable browser on this backend", true);
      }
    };
    /* Only availability needs the probe: whether the toggle is on is already
       known from /api/state and the node pings. Seed the switch from that, or
       it renders off and visibly flips on a moment later. */
    input.checked = browserEnabledFor(bid);
    if (bid && !backendConnectionAllowed(bid)) {
      input.disabled = true;
      if (root) root.classList.add("disabled");
      if (browserRecord) browserRecord.wasOffline = true;
      setNote(remoteAvailability(bid) === "bad" ?
        "Backend unavailable · showing last known value" :
        "Checking backend · showing last known value", true);
      return;
    }
    let status;
    try {
      status = await api(bid, "browser/status", { timeoutMs: ENGINE_POLL_TIMEOUT });
    } catch (error) {
      if (generation !== this.renderGeneration || !input.isConnected) return;
      input.disabled = true;
      if (root) root.classList.add("disabled");
      setNote(error.message || "Browser status unavailable", true);
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
        if (result.enabled === false) closeBrowserTabsForBackend(bid);
        renderSidebar();
        toast(`${name}: Browser ${result.enabled ? "enabled" : "disabled"}`, "ok");
      } catch (error) {
        input.checked = !desired;
        input.disabled = false;
        setNote(error.message, true);
        toast(`${name}: ${error.message}`, "error", 6500);
      }
    };
  }

  systemPromptCard(nodes, initialPayload, generation) {
    const card = el("div", "card system-prompt-card");
    card.innerHTML = `<h2>System prompt</h2>
      <p class="system-prompt-copy">Prompt settings live on the backend that runs the model.
        Choose a backend, then shape the instructions it adds to new turns.</p>`;

    const nodeField = el("label", "system-prompt-node");
    nodeField.appendChild(el("span", "system-prompt-node-label", "Backend"));
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Backend whose system prompt is being edited");
    for (const node of nodes) {
      const option = document.createElement("option");
      option.value = String(node.bid);
      option.textContent = node.name;
      select.appendChild(option);
    }
    nodeField.appendChild(select);
    card.appendChild(nodeField);

    const customSection = el("section", "system-prompt-section");
    const customHead = el("div", "system-prompt-section-head");
    const customCopy = el("div", "system-prompt-section-copy");
    customCopy.appendChild(el("h3", "", "Your system prompt"));
    customCopy.appendChild(el("p", "",
      "Added to every new model turn this backend starts. Leave blank to add nothing."));
    customHead.appendChild(customCopy);
    const custom = document.createElement("textarea");
    custom.className = "system-prompt-textarea config-textarea";
    custom.rows = 3;
    custom.placeholder = "No custom system prompt";
    custom.setAttribute("aria-label", "Your custom system prompt");
    customSection.appendChild(customHead);
    customSection.appendChild(custom);
    card.appendChild(customSection);

    const remoteSection = el("section", "system-prompt-section system-prompt-remote-workspace");
    const remoteHead = el("div", "system-prompt-section-head");
    const remoteCopy = el("div", "system-prompt-section-copy");
    remoteCopy.appendChild(el("h3", "", "Remote workspace guidance"));
    remoteCopy.appendChild(el("p", "",
      "Sent only when this backend runs a model against a project stored on another backend."));
    const remoteReset = el("button", "btn btn-sm btn-ghost system-prompt-reset",
      "Reset to default");
    remoteReset.type = "button";
    remoteHead.appendChild(remoteCopy);
    remoteHead.appendChild(remoteReset);
    const remoteText = document.createElement("textarea");
    remoteText.className = "system-prompt-textarea config-textarea";
    remoteText.rows = 3;
    remoteText.setAttribute("aria-label", "Remote workspace system prompt");
    remoteSection.appendChild(remoteHead);
    remoteSection.appendChild(remoteText);
    card.appendChild(remoteSection);

    const browserSection = el("section", "system-prompt-section system-prompt-browser");
    const browserHead = el("div", "system-prompt-section-head");
    const browserCopy = el("div", "system-prompt-section-copy");
    browserCopy.appendChild(el("h3", "", "Browser guidance"));
    const browserNote = el("p", "",
      "Sent to every model turn on backends where Browser is enabled; it is not sent " +
      "on backends where Browser is off.");
    browserCopy.appendChild(browserNote);
    const browserReset = el("button", "btn btn-sm btn-ghost system-prompt-reset",
      "Reset to default");
    browserReset.type = "button";
    browserHead.appendChild(browserCopy);
    browserHead.appendChild(browserReset);
    const browserText = document.createElement("textarea");
    browserText.className = "system-prompt-textarea config-textarea";
    browserText.rows = 3;
    browserText.setAttribute("aria-label", "Browser system prompt");
    browserSection.appendChild(browserHead);
    browserSection.appendChild(browserText);
    card.appendChild(browserSection);

    const terminalSection = el("section", "system-prompt-section system-prompt-terminal");
    const terminalHead = el("div", "system-prompt-section-head");
    const terminalCopy = el("div", "system-prompt-section-copy");
    terminalCopy.appendChild(el("h3", "", "Terminal guidance"));
    const terminalNote = el("p", "",
      "Sent only when this backend offers shared Terminal tools; it is not sent " +
      "when Terminal is unavailable.");
    terminalCopy.appendChild(terminalNote);
    const terminalReset = el("button", "btn btn-sm btn-ghost system-prompt-reset",
      "Reset to default");
    terminalReset.type = "button";
    terminalHead.appendChild(terminalCopy);
    terminalHead.appendChild(terminalReset);
    const terminalText = document.createElement("textarea");
    terminalText.className = "system-prompt-textarea config-textarea";
    terminalText.rows = 3;
    terminalText.setAttribute("aria-label", "Terminal system prompt");
    terminalSection.appendChild(terminalHead);
    terminalSection.appendChild(terminalText);
    card.appendChild(terminalSection);

    const actions = el("div", "system-prompt-actions");
    const status = el("span", "system-prompt-status", "Ready");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const save = el("button", "btn btn-pri btn-sm", "Save prompt");
    save.type = "button";
    actions.appendChild(status);
    actions.appendChild(save);
    card.appendChild(actions);

    const nodeByBid = bid => nodes.find(node => node.bid === bid);
    const records = new Map();
    let activeBid = 0;
    let loadSerial = 0;

    const normalized = value => {
      const prompt = value && typeof value === "object" ? value : null;
      if (!prompt || typeof prompt.custom !== "string" ||
          typeof prompt.browser !== "string" ||
          typeof prompt.browser_default !== "string")
        throw new Error("backend returned invalid system prompt settings");
      const remoteWorkspaceSupported =
        typeof prompt.remote_workspace === "string" &&
        typeof prompt.remote_workspace_default === "string";
      const terminalSupported =
        typeof prompt.terminal === "string" &&
        typeof prompt.terminal_default === "string";
      const maxChars = Number(prompt.max_chars);
      if (!Number.isInteger(maxChars) || maxChars < 1)
        throw new Error("backend returned an invalid system prompt limit");
      return {
        loaded: true,
        custom: prompt.custom,
        remoteWorkspace: remoteWorkspaceSupported ? prompt.remote_workspace : "",
        browser: prompt.browser,
        terminal: terminalSupported ? prompt.terminal : "",
        customDraft: prompt.custom,
        remoteWorkspaceDraft: remoteWorkspaceSupported ? prompt.remote_workspace : "",
        browserDraft: prompt.browser,
        terminalDraft: terminalSupported ? prompt.terminal : "",
        remoteWorkspaceDefault: remoteWorkspaceSupported ?
          prompt.remote_workspace_default : "",
        browserDefault: prompt.browser_default,
        terminalDefault: terminalSupported ? prompt.terminal_default : "",
        remoteWorkspaceSupported,
        terminalSupported,
        maxChars,
        saving: false,
        error: "",
        saved: false,
      };
    };
    try {
      records.set(0, normalized(initialPayload));
    } catch (error) {
      records.set(0, { loaded: false, loading: false, error: error.message });
    }
    for (const node of nodes) {
      if (!node.bid || !state.remoteSystemPrompts[node.bid]) continue;
      try { records.set(node.bid, normalized(state.remoteSystemPrompts[node.bid])); }
      catch (error) { /* an invalid observation is ignored and reloaded when online */ }
    }

    const supported = bid => backendSupportsSystemPrompt(bid);
    const dirty = record => !!record && record.loaded &&
      (record.customDraft !== record.custom || record.browserDraft !== record.browser ||
       (record.terminalSupported && record.terminalDraft !== record.terminal) ||
       (record.remoteWorkspaceSupported &&
        record.remoteWorkspaceDraft !== record.remoteWorkspace));
    const stash = () => {
      const record = records.get(activeBid);
      if (!record || !record.loaded || record.saving) return;
      record.customDraft = custom.value;
      if (record.remoteWorkspaceSupported)
        record.remoteWorkspaceDraft = remoteText.value;
      record.browserDraft = browserText.value;
      if (record.terminalSupported)
        record.terminalDraft = terminalText.value;
      record.saved = false;
    };
    const paint = () => {
      const record = records.get(activeBid);
      const canUse = supported(activeBid);
      const backendStatus = activeBid ? remoteAvailability(activeBid) : "ok";
      const unavailable = !!activeBid && !backendConnectionAllowed(activeBid);
      const editable = canUse && !unavailable && !!record && record.loaded && !record.saving;
      custom.disabled = browserText.disabled = !editable;
      terminalText.disabled = !editable || !record.terminalSupported;
      remoteText.disabled = !editable || !record.remoteWorkspaceSupported;
      remoteReset.disabled = !editable || !record.remoteWorkspaceSupported;
      browserReset.disabled = !editable;
      terminalReset.disabled = !editable || !record.terminalSupported;
      save.disabled = !canUse || unavailable || (!!record && record.saving);
      status.classList.remove("bad", "dirty");
      if (!canUse) {
        custom.value = remoteText.value = browserText.value = terminalText.value = "";
        custom.removeAttribute("maxlength");
        remoteText.removeAttribute("maxlength");
        browserText.removeAttribute("maxlength");
        terminalText.removeAttribute("maxlength");
        save.disabled = true;
        status.textContent = "Backend upgrade required for system prompt settings.";
      } else if (!record || record.loading) {
        custom.value = remoteText.value = browserText.value = terminalText.value = "";
        save.disabled = true;
        status.textContent = "Loading prompt…";
      } else if (!record.loaded) {
        custom.value = remoteText.value = browserText.value = terminalText.value = "";
        save.disabled = false;
        save.textContent = "Retry";
        status.textContent = record.error || "Prompt settings unavailable.";
        status.classList.add("bad");
      } else {
        custom.maxLength = remoteText.maxLength = browserText.maxLength =
          terminalText.maxLength = record.maxChars;
        if (document.activeElement !== custom) custom.value = record.customDraft;
        if (document.activeElement !== remoteText)
          remoteText.value = record.remoteWorkspaceDraft;
        remoteText.placeholder = record.remoteWorkspaceSupported ? "" :
          "Upgrade this backend to configure remote workspace guidance";
        if (document.activeElement !== browserText) browserText.value = record.browserDraft;
        if (document.activeElement !== terminalText)
          terminalText.value = record.terminalDraft;
        terminalText.placeholder = record.terminalSupported ? "" :
          "Upgrade this backend to configure Terminal guidance";
        save.textContent = record.saving ? "Saving…" : "Save prompt";
        if (unavailable) {
          status.textContent = backendStatus === "bad" ?
            "Backend unavailable · showing last known prompt settings." :
            "Checking backend · showing last known prompt settings.";
          status.classList.add("bad");
        } else if (record.error) {
          status.textContent = record.error;
          status.classList.add("bad");
        } else if (dirty(record)) {
          status.textContent = "Unsaved changes";
          status.classList.add("dirty");
        } else if (record.saved) {
          status.textContent = "Saved for new turns.";
        } else {
          status.textContent = `Up to ${record.maxChars.toLocaleString()} characters per field.`;
        }
      }
    };

    const load = async (force = false) => {
      const bid = activeBid;
      if (!supported(bid)) { paint(); return; }
      const existing = records.get(bid);
      if (bid && !backendConnectionAllowed(bid)) {
        if (!existing || !existing.loaded)
          records.set(bid, {
            loaded: false, loading: false, error: "Backend unavailable",
          });
        paint();
        return;
      }
      if (!force && existing && existing.loaded) { paint(); return; }
      const serial = ++loadSerial;
      records.set(bid, { loaded: false, loading: true, error: "", request: serial });
      paint();
      try {
        const result = await api(bid, "system-prompt", { timeoutMs: 10000 });
        if (generation !== this.renderGeneration || !card.isConnected) return;
        if (!records.get(bid) || records.get(bid).request !== serial) return;
        const loaded = normalized(result.system_prompt);
        records.set(bid, loaded);
        if (bid) state.remoteSystemPrompts[bid] = result.system_prompt;
      } catch (error) {
        if (generation !== this.renderGeneration || !card.isConnected) return;
        if (!records.get(bid) || records.get(bid).request !== serial) return;
        if (existing && existing.loaded) {
          existing.loading = false;
          existing.error = error.message || "Could not refresh prompt settings";
          records.set(bid, existing);
        } else {
          records.set(bid, {
            loaded: false, loading: false,
            error: error.message || "Could not load prompt settings",
          });
        }
      }
      if (bid === activeBid) paint();
    };

    select.onchange = () => {
      stash();
      activeBid = Number(select.value) || 0;
      this.systemPromptBid = activeBid;
      paint();
      if (!records.has(activeBid)) load();
    };
    const edited = () => {
      stash();
      paint();
    };
    custom.oninput = edited;
    remoteText.oninput = edited;
    browserText.oninput = edited;
    terminalText.oninput = edited;
    remoteReset.onclick = () => {
      const record = records.get(activeBid);
      if (!record || !record.loaded || record.saving ||
          !record.remoteWorkspaceSupported) return;
      remoteText.value = record.remoteWorkspaceDefault;
      stash();
      paint();
      remoteText.focus();
    };
    browserReset.onclick = () => {
      const record = records.get(activeBid);
      if (!record || !record.loaded || record.saving) return;
      browserText.value = record.browserDefault;
      stash();
      paint();
      browserText.focus();
    };
    terminalReset.onclick = () => {
      const record = records.get(activeBid);
      if (!record || !record.loaded || record.saving || !record.terminalSupported) return;
      terminalText.value = record.terminalDefault;
      stash();
      paint();
      terminalText.focus();
    };
    save.onclick = async () => {
      let record = records.get(activeBid);
      if (!record || !record.loaded) { await load(true); return; }
      if (record.saving) return;
      stash();
      const bid = activeBid;
      const node = nodeByBid(bid);
      record.saving = true;
      record.error = "";
      paint();
      try {
        const body = { custom: record.customDraft, browser: record.browserDraft };
        if (record.remoteWorkspaceSupported)
          body.remote_workspace = record.remoteWorkspaceDraft;
        if (record.terminalSupported)
          body.terminal = record.terminalDraft;
        const result = await api(bid, "system-prompt", {
          method: "PATCH",
          body,
          timeoutMs: 12000,
        });
        if (generation !== this.renderGeneration || !card.isConnected) return;
        record = normalized(result.system_prompt);
        record.saved = true;
        records.set(bid, record);
        if (bid) state.remoteSystemPrompts[bid] = result.system_prompt;
        toast(`${node ? node.name : "Backend"}: System prompt saved`, "ok");
      } catch (error) {
        if (generation !== this.renderGeneration || !card.isConnected) return;
        record.saving = false;
        record.error = error.message || "Could not save prompt settings";
        records.set(bid, record);
        toast(`${node ? node.name : "Backend"}: ${record.error}`, "error", 7000);
      }
      if (bid === activeBid) paint();
    };

    const preferred = Number(this.systemPromptBid) || 0;
    if (nodes.some(node => node.bid === preferred)) activeBid = preferred;
    select.value = String(activeBid);
    this.systemPromptSync = paint;
    paint();
    if (!records.has(activeBid)) Promise.resolve().then(() => load());
    return card;
  }

  async render() {
    this.stopUpgradeReadinessPolling();
    const generation = ++this.renderGeneration;
    let settings, engines, promptSettings;
    try {
      [settings, engines, promptSettings] = await Promise.all([
        api(0, "settings"), api(0, "engines"), api(0, "system-prompt"),
      ]);
    } catch (e) {
      if (generation === this.renderGeneration)
        this.inner.innerHTML = `<div class="err-card">${esc(e.message)}</div>`;
      return;
    }
    if (generation !== this.renderGeneration) return;
    rememberEnginePayload(0, {
      ...engines,
      usage_refresh: engines.usage_refresh || settings.usage_refresh || state.usageRefresh,
    });
    rememberUploadSettings(0, settings.uploads);
    state.localEngineCheckedAt = Date.now();
    renderFootEngines();
    this.inner.innerHTML = "";
    this.remoteEngineGroups.clear();
    this.remoteBackendDots.clear();
    this.remoteBackendMeta.clear();
    this.usageRows.clear();
    this.autoUpgradeRows.clear();
    this.uploadRows.clear();
    this.remoteBrowserToggles.clear();
    this.upgradeButtons.clear();
    this.backendAutoToggles.clear();
    /* A hidden Settings tab is not polled. Re-enter through Checking rather
       than briefly enabling a button from an arbitrarily old ready result. */
    this.upgradeReadiness.clear();
    this.localEngineGroup = null;
    this.systemPromptSync = null;

    /* instance */
    const c1 = el("div", "card");
    const activeWeb = settings.active_web || settings.web;
    c1.innerHTML = `<h2>Instance</h2>
      <label>Instance name<input type="text" id="set-name" value="${esc(settings.instance_name)}"></label>
      <label>Default working directory<input type="text" id="set-cwd" value="${esc(settings.default_cwd || "")}"></label>
      <div class="dirpick hidden" id="set-cwd-dirs"></div>
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
        role="button" tabindex="0" aria-label="Reveal API token" class="api-token-control">••••••••••••</span></div>
      <div class="settings-actions">
        <button class="btn btn-pri btn-sm" id="set-save">Save</button>
        <button class="btn btn-sm btn-ghost" id="set-logout">Log out</button>
      </div>
`;
    this.inner.appendChild(c1);
    /* the instance default is always this node's own filesystem */
    wireDirectoryPicker(c1.querySelector("#set-cwd"),
      c1.querySelector("#set-cwd-dirs"), () => 0);
    this.wireBrowserToggle(0, c1.querySelector("#set-browser"),
      c1.querySelector("#set-browser-note"), generation);
    /* reveal -> copy -> hide, then round again, so the token never has to stay
       on screen once it has been taken. The label names what the NEXT activation
       does, which is what a screen reader announces before the press. */
    const TOKEN_MASK = "••••••••••••";
    const TOKEN_STEPS = ["Reveal API token", "Copy API token", "Hide API token"];
    let tokenStep = 0;   // 0 masked, 1 revealed, 2 revealed and copied
    const tokenControl = c1.querySelector("#set-token");
    const useToken = () => {
      tokenStep = (tokenStep + 1) % TOKEN_STEPS.length;
      if (tokenStep === 2) copyWithToast(settings.api_token, "Token copied");
      tokenControl.textContent = tokenStep ? settings.api_token : TOKEN_MASK;
      tokenControl.setAttribute("aria-label", TOKEN_STEPS[tokenStep]);
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
        toast("Bind port must be a whole number between 1 and 65535", "error");
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
        toast("Saved", "ok");
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
      `This instance · v${settings.version}`, 0);
    localGroup.update({ status: "ok", engines: state.engines });
    this.localEngineGroup = localGroup;
    c2.appendChild(localGroup.root);
    for (const b of state.backends) {
      const group = this.engineGroup(b.name, b, b.id);
      const cached = state.engCache[b.id];
      group.update({
        status: state.remoteOk[b.id] === false ? "bad" : state.remoteOk[b.id] === true ? "ok" : "pending",
        engines: cached || null,
      });
      this.remoteEngineGroups.set(b.id, group);
      c2.appendChild(group.root);
    }
    /* Engine update scheduling is part of Engines, not a separate concept.
       Keep its existing heading and rows, separated as a section inside the
       same card so spacing remains legible without presenting two panels. */
    const autoSection = el("section", "engine-updates-section");
    autoSection.innerHTML = `<h2>Engine updates</h2>
      <p class="usage-refresh-copy">Let a backend install its own engine CLI updates using the
        same vendor updater the Update button runs. Each version is tried once: if an update
        fails it is not retried until a newer one appears, and a backend with a busy session
        waits rather than replacing an engine underneath it.</p>`;
    const autoList = el("div", "eau-list");
    const localAuto = this.autoUpgradeRow(settings.instance_name, 0);
    localAuto.update(state.autoUpgrade, "ok", true);
    this.autoUpgradeRows.set(0, localAuto);
    autoList.appendChild(localAuto.root);
    for (const b of state.backends) {
      const auto = this.autoUpgradeRow(b.name, b.id);
      auto.update(state.remoteAutoUpgrade[b.id] || null,
        remoteAvailability(b.id), backendSupportsEngineAutoUpgrade(b.id));
      this.autoUpgradeRows.set(b.id, auto);
      autoList.appendChild(auto.root);
    }
    autoSection.appendChild(autoList);
    c2.appendChild(autoSection);
    this.inner.appendChild(c2);

    const promptNodes = [{ bid: 0, name: settings.instance_name }]
      .concat(state.backends.map(backend => ({ bid: backend.id, name: backend.name })));
    this.inner.appendChild(this.systemPromptCard(
      promptNodes, promptSettings.system_prompt, generation));

    /* account usage refresh */
    const usageCard = el("div", "card usage-refresh-card");
    usageCard.innerHTML = `<h2>Usage refresh</h2>
      <p class="usage-refresh-copy">Choose how often each backend asks its installed engines
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

    /* per-backend attachment limits */
    const uploadCard = el("div", "card upload-limit-card");
    uploadCard.innerHTML = `<h2>File uploads</h2>
      <p class="usage-refresh-copy">Set the maximum size of one attached file on each
        receiving backend. Files stream directly to private session storage and may be any type.
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

    /* completion alert: a command a chosen backend runs when a session finishes */
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
        anything queued behind it — run this command on a backend: play a sound, ping your home
        automation, anything. Arm or silence it any time with the bell in the sidebar footer.</p>
      <div class="notify-fields">
        <label>Run on<select id="nf-backend" aria-label="Backend the command runs on"></select></label>
        <label>Command<input type="text" id="nf-cmd" autocomplete="off" autocapitalize="off"
          spellcheck="false" placeholder='e.g. mosquitto_pub -t puppy/done -m {session}'></label>
      </div>
      <p class="usage-refresh-copy">Placeholders <span class="mono-inline">{backend} {session}
        {engine} {model} {status} {duration} {duration_hms} {cwd} {id}</span> are substituted
        shell-quoted, and the same values arrive as <span class="mono-inline">PUPPY_*</span>
        environment variables. <span class="mono-inline">{duration}</span> is whole seconds and
        <span class="mono-inline">{duration_hms}</span> the same span as a clock
        (<span class="mono-inline">9:59</span>, <span class="mono-inline">10:00</span>,
        <span class="mono-inline">1:00:00</span>).
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
    }).catch(() => nfNoteSet("Could not load the current setting", true));
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
        nfNoteSet("Saved");
        toast(state.notify.configured ? "Completion alert saved" :
          "Completion alert command cleared", "ok");
      } catch (e) { nfNoteSet(e.message, true); }
    };
    notifyCard.querySelector("#nf-test").onclick = async () => {
      nfNoteSet("Running…");
      try {
        const r = await api(0, "notify/test", { method: "POST", body: {
          backend: Number(nfBackend.value || 0), command: nfCmd.value,
        } });
        if (r.ok) nfNoteSet("OK" + (r.output ? " · " + r.output.slice(-120) : ""));
        else nfNoteSet(r.error || `Exit ${r.rc}` + (r.output ? " · " + r.output.slice(-120) : ""), true);
      } catch (e) { nfNoteSet(e.message, true); }
    };

    /* backends */
    const c3 = el("div", "card");
    c3.innerHTML = `<h2>Backends</h2><div id="be-list"></div>
      <div class="settings-form backend-add-form">
        <label>Name <span class="field-optional">(optional)</span><input type="text" id="be-name"></label>
        <div class="backend-url-field"><span class="backend-url-caption">URLs</span>
          <div class="backend-url-editor" id="be-urls"></div>
          <small>First reachable address is used; the working address stays preferred.</small></div>
        <label class="full">API token<input type="password" id="be-token" autocomplete="off"></label>
        <label class="full">TLS certificate SHA-256 <span class="field-optional">(optional)</span>
          <input type="text" id="be-tls" autocomplete="off" spellcheck="false"
            placeholder="Supplied automatically by pairing JSON"></label>
        <label class="full">Pairing JSON <span class="field-optional">(optional)</span>
          <textarea class="config-textarea" id="be-pairing" rows="3"
            placeholder="Paste puppy-backend pairing output"></textarea></label>
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
            <small>Enabled once the backend is added, if that backend supports one</small></span>
        </label>
        <div class="full"><button class="btn btn-pri btn-sm" id="be-add">Add backend</button></div>
      </div>`;
    const addUrlEditor = backendUrlEditor(c3.querySelector("#be-urls"));
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
        const url = backendLocationNode(b);
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
        const transportUrls = configuredBackendUrls(b);
        const allTls = !!transportUrls.length && transportUrls.every(value =>
          /^https:\/\//i.test(value));
        const mixedTls = transportUrls.some(value => /^https:\/\//i.test(value)) && !allTls;
        const isPinned = allTls && !!b.tls_fingerprint;
        const security = el("span", `be-security ${allTls ? "secure" : "clear"}`);
        const securityLabel = mixedTls
          ? "Mixed transport · at least one backend URL uses cleartext HTTP"
          : isPinned
          ? `Encrypted · pinned certificate SHA-256: ${b.tls_fingerprint}`
          : allTls ? "Encrypted · certificate verified by the controller system trust store"
            : "Cleartext · traffic to this backend is not encrypted";
        security.setAttribute("role", "img");
        security.setAttribute("aria-label", securityLabel);
        security.appendChild(transportShieldIcon(allTls));
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
        const edit = el("button", "btn btn-sm", "Edit");
        edit.setAttribute("aria-label", `Edit backend ${b.name}`);
        edit.onclick = () => modalEditBackend(b, async result => {
          const current = state.backends.find(item => item.id === b.id);
          if (current && result.backend) Object.assign(current, result.backend);
          if (result.connection_changed) resetRemoteBackendConnection(b.id);
          toast(`${(result.backend && result.backend.name) || b.name}: Backend updated`, "ok");
          try {
            await refreshState();
            if (this.inner.isConnected) await this.render();
          } catch (error) {
            console.warn("post-edit backend refresh failed", error);
          }
          if (result.connection_changed) {
            for (const view of Object.values(state.views)) {
              if (!view || !view.tab || view.tab.type !== "browser" ||
                  Number(view.tab.bid) !== Number(b.id) || typeof view.connect !== "function")
                continue;
              try { view.connect(); }
              catch (error) { console.warn("backend browser reconnect failed", error); }
            }
          }
          pollRemotes({ forceEngines: true })
            .catch(error => console.warn("edited-backend poll failed", error));
        });
        const test = el("button", "btn btn-sm", "Test");
        test.onclick = async () => {
          test.textContent = "…";
          try {
            const r = await api(0, `backends/${b.id}/test`, { method: "POST" });
            if (r.ok) {
              state.remoteOk[b.id] = true;
              delete state.remoteErrors[b.id];
              toast(`${b.name}: OK (${r.remote && r.remote.version})`, "ok");
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
          const addresses = configuredBackendUrls(b).join(", ");
          if (!(await modalConfirm("Remove backend?", `${b.name} (${addresses})`))) return;
          await api(0, `backends/${b.id}`, { method: "DELETE" });
          await refreshState(); await this.render();
        };
        actions.appendChild(edit); actions.appendChild(test);
        actions.appendChild(upgrade); actions.appendChild(rm);
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
        const urls = pairingBackendUrls(paired, addUrlEditor.values());
        if (!urls.length) throw new Error("at least one backend URL is required");
        const added = await api(0, "backends", { method: "POST", body: {
          name: pairingValue("#be-name", "name"),
          urls,
          token: pairingValue("#be-token", "token"),
          tls_fingerprint: pairingValue("#be-tls", "tls_sha256"),
          auto_upgrade: c3.querySelector("#be-auto").checked,
        }});
        toast("Backend added", "ok");
        c3.querySelector("#be-name").value = c3.querySelector("#be-token").value =
          c3.querySelector("#be-tls").value = "";
        addUrlEditor.setValues([""]);
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
        toast("Password changed", "ok");
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
        toast(`Backup ready · ${prepared.sessions} sessions · ${fmtBytes(prepared.size)}`, "ok");
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
    const { m, close } = modal(`<h2>${esc(title)}</h2><p class="modal-copy">${esc(text || "")}</p>
      <div class="m-btns"><button class="btn" id="mc-no">Cancel</button><button class="btn btn-danger btn-solid" id="mc-yes">Confirm</button></div>`);
    m.querySelector("#mc-no").onclick = () => { close(); resolve(false); };
    m.querySelector("#mc-yes").onclick = () => { close(); resolve(true); };
  });
}

function modalNotice(title, text) {
  const { m, close } = modal(`<h2>${esc(title)}</h2>
    <p class="modal-copy">${esc(text || "")}</p>
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

/* Edit a paired backend without ever reading its stored token back into the
   browser. A blank token deliberately means "keep it"; pasted pairing JSON is
   the convenient credential-rotation path and supplies the primary address.
   The controller probes those candidate details
   before committing them, so this modal never has to stage a half-edit. */
function modalEditBackend(backend, onSaved) {
  const { m, close } = modal(`<h2>Edit backend</h2>
    <p class="backend-edit-intro">Update its display name or connection. New connection details are tested before they replace the current settings.</p>
    <form id="backend-edit-form">
      <div class="backend-edit-grid">
        <label>Name<input type="text" id="backend-edit-name" maxlength="80"></label>
        <div class="backend-url-field"><span class="backend-url-caption">URLs</span>
          <div class="backend-url-editor" id="backend-edit-urls"></div>
          <small>First reachable address is used; the working address stays preferred.</small></div>
        <label class="full">API token <span class="field-optional">(leave blank to keep current)</span>
          <input type="password" id="backend-edit-token" autocomplete="new-password"
            placeholder="Current token is unchanged"></label>
        <label class="full">TLS certificate SHA-256 <span class="field-optional">(optional)</span>
          <input type="text" id="backend-edit-tls" autocomplete="off" spellcheck="false"></label>
        <label class="full">Pairing JSON <span class="field-optional">(optional)</span>
          <textarea class="config-textarea" id="backend-edit-pairing" rows="3"
            placeholder="Paste new puppy-backend pairing output"></textarea></label>
        <p class="backend-edit-help full">Pairing JSON supplies the primary URL, token and certificate. Other entered URLs remain as fallbacks; the display name stays as entered.</p>
        <p class="backend-edit-error full hidden" role="alert"></p>
      </div>
      <div class="m-btns"><button type="button" class="btn" id="backend-edit-cancel">Cancel</button>
        <button type="submit" class="btn btn-pri" id="backend-edit-save">Save changes</button></div>
    </form>`, "backend-edit-modal");
  const form = m.querySelector("#backend-edit-form");
  const name = m.querySelector("#backend-edit-name");
  const urlEditor = backendUrlEditor(m.querySelector("#backend-edit-urls"),
    configuredBackendUrls(backend));
  const token = m.querySelector("#backend-edit-token");
  const fingerprint = m.querySelector("#backend-edit-tls");
  const pairing = m.querySelector("#backend-edit-pairing");
  const cancel = m.querySelector("#backend-edit-cancel");
  const save = m.querySelector("#backend-edit-save");
  const error = m.querySelector(".backend-edit-error");
  name.value = backend.name || "";
  fingerprint.value = backend.tls_fingerprint || "";

  const setError = text => {
    error.textContent = text || "";
    error.classList.toggle("hidden", !text);
  };
  const setBusy = busy => {
    form.setAttribute("aria-busy", busy ? "true" : "false");
    form.querySelectorAll("input,textarea,button").forEach(control => control.disabled = busy);
    urlEditor.setDisabled(busy);
    save.textContent = busy ? "Saving…" : "Save changes";
  };
  const pairedText = (data, keys, fallback, emptyWins = false) => {
    for (const key of keys) {
      if (!Object.prototype.hasOwnProperty.call(data, key)) continue;
      if (typeof data[key] !== "string")
        throw new Error(`pairing JSON ${key} must be text`);
      const value = data[key].trim();
      if (value || emptyWins) return value;
    }
    return fallback;
  };

  cancel.onclick = close;
  form.onsubmit = async event => {
    event.preventDefault();
    setError("");
    try {
      const displayName = name.value.trim();
      if (!displayName) throw new Error("backend name is required");
      let paired = {};
      const raw = pairing.value.trim();
      if (raw) {
        try { paired = JSON.parse(raw); }
        catch (_) { throw new Error("invalid pairing JSON"); }
        if (!paired || typeof paired !== "object" || Array.isArray(paired))
          throw new Error("invalid pairing JSON");
      }
      const urls = pairingBackendUrls(paired, urlEditor.values());
      const body = {
        name: displayName,
        urls,
        /* A cleartext pairing has no TLS field at all. Once a pairing block is
           supplied, absence therefore means "clear the old pin", not "carry
           the old HTTPS certificate into the new HTTP connection". */
        tls_fingerprint: raw ? pairedText(
          paired, ["tls_sha256", "tls_fingerprint"], "", true) :
          fingerprint.value.trim(),
      };
      const nextToken = pairedText(paired, ["token"], token.value.trim());
      if (nextToken) body.token = nextToken;
      if (!body.urls.length) throw new Error("at least one backend URL is required");
      setBusy(true);
      const result = await api(0, `backends/${backend.id}`, {
        method: "PATCH", body, timeoutMs: 45000,
      });
      close();
      if (typeof onSaved === "function") await onSaved(result);
    } catch (caught) {
      if (m.isConnected) {
        setBusy(false);
        setError(caught.message || "Backend could not be updated");
      } else {
        toast(caught.message || "Backend could not be updated", "error", 7000);
      }
    }
  };
  requestAnimationFrame(() => { if (name.isConnected) { name.focus(); name.select(); } });
  return { m, close };
}

/* Storage choices for a linked workspace deliberately exclude the backend
   running the engine. A duplicate record for the same physical machine can
   still race through; the controller's backend-identity check remains the final
   authority and safely degrades that rare case to a direct directory. */
function workspaceBackendChoices(execBid) {
  const executionBackend = Number(execBid) || 0;
  return [{ id: 0, name: backendName(0) }].concat(state.backends)
    .filter(backend => Number(backend.id) !== executionBackend &&
      (!backend.id || (backendSupportsWorkspaceProvider(backend.id) &&
        backendConnectionAllowed(backend.id))));
}

function renderWorkspaceBackendOptions(select, execBid) {
  const choices = workspaceBackendChoices(execBid);
  const previous = select.value;
  select.innerHTML = "";
  if (!choices.length) {
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "No other backend available";
    empty.disabled = empty.selected = true;
    select.appendChild(empty);
    select.value = "";
    select.disabled = true;
    refreshChoiceSelect(select);
    return choices;
  }
  for (const backend of choices) {
    const option = document.createElement("option");
    option.value = String(backend.id);
    option.textContent = backend.name;
    select.appendChild(option);
  }
  select.disabled = false;
  select.value = choices.some(backend => String(backend.id) === previous) ?
    previous : String(choices[0].id);
  refreshChoiceSelect(select);
  return choices;
}

/* new session */
async function modalNewSession(groupId = null) {
  const beOpts = [{ id: 0, name: backendName(0) }].concat(state.backends);
  const { m, close } = modal(`<h2>New session</h2>
    <label>Backend<select id="ns-be">${beOpts.map(b => {
      const offline = b.id && !backendConnectionAllowed(b.id);
      return `<option value="${b.id}"${offline ? " disabled" : ""}>` +
        `${esc(b.name)}${offline ? " (offline)" : ""}</option>`;
    }).join("")}</select></label>
    <div class="engine-pick" id="ns-engines"></div>
    <div class="field-lbl">Workspace
      <div class="workspace-pick" id="ns-workspace">
        <button type="button" class="wp sel" data-kind="directory" aria-pressed="true">
          <span class="wp-name">Directory</span><span class="wp-sub">Use a project folder</span>
        </button>
        <button type="button" class="wp" data-kind="temporary" aria-pressed="false">
          <span class="wp-name">Scratch</span><span class="wp-sub">No folder to choose</span>
        </button>
        <button type="button" class="wp" data-kind="remote" aria-pressed="false">
          <span class="wp-name">Remote</span><span class="wp-sub">Files on another backend</span>
        </button>
      </div>
    </div>
    <label id="ns-wsbe-wrap" class="hidden">Files on backend<select id="ns-wsbe"></select></label>
    <div id="ns-dir-fields">
      <label>Working directory<input type="text" id="ns-cwd" spellcheck="false"></label>
      <div class="dirpick hidden" id="ns-dirs"></div>
      <label class="check new-session-mkdir"><input type="checkbox" id="ns-mkdir"> Create directory if missing</label>
    </div>
    <p class="hint scratch-note hidden" id="ns-scratch-note">Puppy creates a private empty workspace in the host's temporary storage (normally /tmp). It survives Puppy restarts and is deleted with this session, but the host may clear it—commonly on reboot. The transcript is kept and Puppy can start a fresh workspace.</p>
    <label>Name <span class="field-optional">(optional, auto from first message)</span><input type="text" id="ns-name"></label>
    <div class="field-row">
      <label>Model<select id="ns-model"></select></label>
      <label>Effort<select id="ns-effort"></select></label>
      <label>Permissions<select id="ns-perm"></select></label>
    </div>
    <label class="hidden" id="ns-model-custom-wrap">Custom model<input type="text" id="ns-model-custom" placeholder="Model ID"></label>
    <div class="field-lbl new-session-color">Color<div class="swatch-row" id="ns-colors"></div></div>
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
  const remoteButton = workspaceBox.querySelector('[data-kind="remote"]');
  const wsbeWrap = m.querySelector("#ns-wsbe-wrap");
  const wsbeSel = m.querySelector("#ns-wsbe");
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
    layoutSwatchRow(colorBox);
  };
  renderColors();
  /* the modal is viewport-width on a phone, so the row is re-evened on resize;
     the listener retires itself once the modal is gone */
  const relayoutColors = () => {
    if (!colorBox.isConnected) {
      window.removeEventListener("resize", relayoutColors);
      return;
    }
    layoutSwatchRow(colorBox);
  };
  window.addEventListener("resize", relayoutColors);
  const cwdInp = m.querySelector("#ns-cwd");
  const dirBox = m.querySelector("#ns-dirs");
  /* Every new session starts from the configured default, never from wherever
     the last one happened to be pointed. */
  cwdInp.value = state.defaultCwd || "/";
  let engines = [];
  let engine = null;
  let engineLoadSequence = 0;

  function pickWorkspace(kind) {
    if (kind === "temporary" && scratchButton.disabled) return;
    if (kind === "remote" && remoteButton.disabled) return;
    workspaceKind = kind;
    workspaceBox.querySelectorAll(".wp").forEach(button => {
      const selected = button.dataset.kind === kind;
      button.classList.toggle("sel", selected);
      button.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    /* remote reuses the directory fields: the same path input and browser,
       just aimed at the backend where the files live */
    directoryFields.classList.toggle("hidden", kind === "temporary");
    scratchNote.classList.toggle("hidden", kind !== "temporary");
    wsbeWrap.classList.toggle("hidden", kind !== "remote");
    dirBox.classList.add("hidden");
  }
  workspaceBox.querySelectorAll(".wp").forEach(button => {
    button.onclick = () => pickWorkspace(button.dataset.kind);
  });

  function renderWsBackends() {
    const execBid = parseInt(beSel.value, 10);
    return renderWorkspaceBackendOptions(wsbeSel, execBid);
  }
  wsbeSel.onchange = () => dirBox.classList.add("hidden");

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
    const mirrorable = backendSupportsWorkspaceMirror(parseInt(beSel.value, 10));
    const storageBackends = renderWsBackends();
    const remoteAvailable = mirrorable && storageBackends.length > 0;
    remoteButton.disabled = !remoteAvailable;
    remoteButton.removeAttribute("title");
    remoteButton.removeAttribute("data-tip");
    if (remoteAvailable) remoteButton.removeAttribute("aria-label");
    else remoteButton.setAttribute("aria-label", mirrorable ?
      "Remote workspace · no other backend available" :
      "Remote workspace · backend upgrade required");
    remoteButton.querySelector(".wp-sub").textContent = !mirrorable ?
      "Backend upgrade required" : storageBackends.length ?
        "Files on another backend" : "No other backend available";
    if (!remoteAvailable && workspaceKind === "remote") pickWorkspace("directory");
  }

  async function loadEngines() {
    const bid = parseInt(beSel.value, 10);
    const sequence = ++engineLoadSequence;
    let loaded = [];
    try {
      if (bid && !backendConnectionAllowed(bid))
        throw new Error("the controller reports this backend offline");
      if (bid === 0) {
        if (state.engines.some(engine => engine.dynamic_model_options &&
            engine.model_catalog_loaded !== true)) {
          const result = await api(0, "engines", { timeoutMs: ENGINE_POLL_TIMEOUT });
          if (!result || !Array.isArray(result.engines))
            throw new Error("instance returned an invalid engines response");
          rememberEnginePayload(0, result);
        }
        loaded = state.engines;
      }
      else {
        const cached = state.engCache[bid];
        if (!Array.isArray(cached) || cached.some(engine =>
            engine.dynamic_model_options && engine.model_catalog_loaded !== true)) {
          const result = await api(bid, "engines", { timeoutMs: ENGINE_POLL_TIMEOUT });
          if (!result || !Array.isArray(result.engines))
            throw new Error("backend returned an invalid engines response");
          rememberEnginePayload(bid, result);
          state.remoteEngineCheckedAt[bid] = Date.now();
          delete state.remoteEngineErrors[bid];
        }
        loaded = state.engCache[bid];
      }
    } catch (e) {
      if (sequence !== engineLoadSequence || parseInt(beSel.value, 10) !== bid) return;
      toast("Backend unavailable: " + e.message, "error");
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
      const icon = provSpec(e2.key);
      card.dataset.key = e2.key;
      card.innerHTML = `<div class="ep-ico prov ${icon.className}">${esc(icon.text)}</div>
        <div class="ep-name">${esc(e2.label)}</div>
        <div class="ep-sub">${engineStatusText(e2)}</div>`;
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
        opt.disabled = !!o.disabled;
        if (o.value === selected) opt.selected = true;
        sel.appendChild(opt);
      }
      refreshChoiceSelect(sel);
    };
    fill(permSel, e2 ? e2.permission_options : [], e2 ? e2.default_permission : "");
    const modelOptions = [...((e2 && e2.model_options) || [])];
    if (!e2 || e2.allow_custom_model !== false)
      modelOptions.push({ value: "__custom__", label: "Custom…" });
    if (!modelOptions.length)
      modelOptions.push({ value: "", label: "No models reported", disabled: true });
    fill(modelSel, modelOptions, modelOptions[0].value);
    modelSel.disabled = !e2 || (e2.allow_custom_model === false &&
      !(e2.model_options || []).length);
    refreshChoiceSelect(modelSel);
    const syncEffort = () => {
      const model = modelSel.value === "__custom__" ? "" : modelSel.value;
      fill(effortSel, effortOptionsForModel(e2, model), "");
      customWrap.classList.toggle("hidden", modelSel.value !== "__custom__");
    };
    modelSel.onchange = syncEffort;
    syncEffort();
  }
  beSel.onchange = () => { syncWorkspaceSupport(); loadEngines(); };
  syncWorkspaceSupport();
  await loadEngines();

  /* directory browser follows the backend whose filesystem holds the files:
     the execution backend normally, the workspace backend for a remote link */
  wireDirectoryPicker(cwdInp, dirBox, () => workspaceKind === "remote" ?
    parseInt(wsbeSel.value, 10) : parseInt(beSel.value, 10));

  m.querySelector("#ns-cancel").onclick = close;
  m.querySelector("#ns-go").onclick = async () => {
    const bid = parseInt(beSel.value, 10);
    if (!engine) { toast("Pick an engine", "error"); return; }
    const shared = {
      engine, name: m.querySelector("#ns-name").value,
      model: modelSel.value === "__custom__" ? customInp.value.trim() : modelSel.value,
      effort: effortSel.value, permission_mode: permSel.value, color: nsColor,
    };
    try {
      if (workspaceKind === "remote") {
        const workspaceBackend = parseInt(wsbeSel.value, 10);
        if (!Number.isInteger(workspaceBackend))
          throw new Error("No other backend is available for this remote workspace");
        const r = await api(0, "workspaces/sessions", { method: "POST", body: {
          ...shared, backend: bid,
          workspace_backend: workspaceBackend,
          root: cwdInp.value.trim(),
          mkdir: m.querySelector("#ns-mkdir").checked,
        }, timeoutMs: 120000 });
        close();
        if (r.same_node)
          toast("Both backends resolve to the same machine - using the directory directly");
        if (r.bid) await pollRemotes();
        openSessionTab(r.bid || 0, r.session.id, r.session, groupId);
        return;
      }
      const r = await api(bid, "sessions", { method: "POST", body: {
        ...shared, workspace_kind: workspaceKind,
        cwd: workspaceKind === "directory" ? cwdInp.value.trim() : "",
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
  let html = `<h2>Open session</h2><input type="text" id="os-filter" placeholder="Filter…"><div id="os-list" class="open-session-list"></div>`;
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
        r1.appendChild(el("div", "si-name", (s.name || `Session ${s.id}`) + (s.archived ? " (archived)" : "")));
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
/* Linked-workspace detail sheet: where the files live, the live sync state,
   manual sync, per-path conflict resolution, and a shell on the file backend. */
function modalWorkspaceLink(bid, session) {
  const ws = sessionWorkspace(session);
  if (!ws) return;
  const { m, close } = modal(`<h2>Linked workspace</h2>
    <div class="ws-facts"></div>
    <div class="ws-conflict-box hidden">
      <div class="field-lbl">Conflicts</div>
      <p class="hint ws-conflict-hint">These paths changed on both sides. Pick which
      version wins; the losing bytes are kept on the controller.</p>
      <div class="ws-conflicts"></div>
      <button class="btn btn-pri ws-apply hidden" id="wsl-apply">Apply choices</button>
    </div>
    <p class="hint ws-nolink hidden">This console has no link record for this session,
    so it cannot sync it. The controller that created the link manages synchronization.</p>
    <div class="m-btns">
      <button class="btn" id="wsl-close">Close</button>
      <button class="btn hidden" id="wsl-term">Terminal</button>
      <button class="btn btn-pri" id="wsl-sync">Sync now</button>
    </div>`, "ws-link-modal");
  const facts = m.querySelector(".ws-facts");
  const conflictWrap = m.querySelector(".ws-conflict-box");
  const conflictBox = m.querySelector(".ws-conflicts");
  const applyBtn = m.querySelector("#wsl-apply");
  const noLink = m.querySelector(".ws-nolink");
  const syncBtn = m.querySelector("#wsl-sync");
  const termBtn = m.querySelector("#wsl-term");
  const choices = {};
  const stateText = (st) => ({ init: "preparing first sync", syncing: "syncing",
    ok: "synced", conflict: "conflicts need a decision",
    error: "sync error" }[st] || st || "no link record");

  const fact = (label, value, cls = "") => {
    const row = el("div", "ws-fact");
    row.appendChild(el("span", "wsf-l", label));
    row.appendChild(el("span", "wsf-v" + (cls ? " " + cls : ""), value));
    facts.appendChild(row);
  };

  const render = () => {
    const link = linkForSession(bid, session.id);
    facts.innerHTML = "";
    fact("Project", ws.root);
    fact("Files on", link ? link.ws_name : (ws.node || "another backend"));
    fact("Runs on", link ? link.exec_name : backendName(bid));
    const st = link ? link.state : "";
    fact("State", stateText(st),
      st === "conflict" ? "warn" : st === "error" ? "err" :
      st === "ok" ? "ok" : "");
    if (link && link.last_error) fact("Last error", link.last_error, "err");
    if (link) fact("Last sync", link.last_sync_at ?
      fmtTime(link.last_sync_at) + " · pass " + link.generation : "not yet");
    noLink.classList.toggle("hidden", !!link);
    syncBtn.classList.toggle("hidden", !link);
    const canShell = link && (link.ws_backend === 0 || backendHasCapability(
      state.backends.find(b => b.id === link.ws_backend), "terminal"));
    termBtn.classList.toggle("hidden", !canShell);
    if (link) termBtn.textContent = "Terminal on " + link.ws_name;

    const conflicts = (link && link.conflicts) || [];
    conflictWrap.classList.toggle("hidden", !conflicts.length);
    conflictBox.innerHTML = "";
    for (const item of conflicts) {
      const row = el("div", "wsc-row");
      const path = el("div", "wsc-path", item.path);
      path.setAttribute("aria-label", item.path);
      const pickBtn = (side, label, kind) => {
        const b = el("button", "btn wsc-pick" +
          (choices[item.path] === side ? " sel" : ""),
          `${label} (${kind})`);
        b.type = "button";
        b.onclick = () => {
          choices[item.path] = choices[item.path] === side ? undefined : side;
          if (choices[item.path] === undefined) delete choices[item.path];
          render();
        };
        return b;
      };
      const picks = el("div", "wsc-picks");
      picks.appendChild(pickBtn("workspace", "Keep project", item.workspace));
      picks.appendChild(pickBtn("session", "Keep session", item.session));
      row.appendChild(path);
      row.appendChild(picks);
      conflictBox.appendChild(row);
    }
    applyBtn.classList.toggle("hidden", !Object.keys(choices).length);
  };
  render();
  /* link state moves on its own (broadcasts land in state.workspaceLinks);
     the sheet re-reads it while open and the timer retires with the DOM */
  const timer = setInterval(() => {
    if (!m.isConnected) { clearInterval(timer); return; }
    render();
  }, 2000);

  m.querySelector("#wsl-close").onclick = close;
  termBtn.onclick = () => {
    const link = linkForSession(bid, session.id);
    if (link) { close(); openTermTab(link.ws_backend, "", null, link.root); }
  };
  syncBtn.onclick = async () => {
    const link = linkForSession(bid, session.id);
    if (!link) return;
    syncBtn.disabled = true;
    try {
      const r = await api(0, `workspaces/${link.id}/sync`,
        { method: "POST", timeoutMs: 180000 });
      if (r && r.link) {
        state.workspaceLinks = state.workspaceLinks.map(
          l => l.id === r.link.id ? r.link : l);
      }
      toast("Workspace synced");
    } catch (e) { toast(e.message, "error"); }
    syncBtn.disabled = false;
    render();
    refreshWorkspaceChips();
    renderSidebar();
  };
  applyBtn.onclick = async () => {
    const link = linkForSession(bid, session.id);
    if (!link || !Object.keys(choices).length) return;
    applyBtn.disabled = true;
    try {
      await api(0, `workspaces/${link.id}/resolve`,
        { method: "POST", body: { choices } });
      for (const key of Object.keys(choices)) delete choices[key];
      toast("Resolving conflicts…");
    } catch (e) { toast(e.message, "error"); }
    applyBtn.disabled = false;
    render();
  };
}

function modalNewTerminal(groupId = null) {
  const beOpts = [{ id: 0, name: backendName(0) }]
    .concat(state.backends.filter(b => backendHasCapability(b, "terminal")));
  const { m, close } = modal(`<h2>New terminal</h2>
    <label>Backend<select id="nt-be">${beOpts.map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join("")}</select></label>
    <label>Command <span class="field-optional">(optional)</span><input type="text" id="nt-cmd" placeholder="Default shell — or e.g. ssh user@host"></label>
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
    toast("No backend has its browser enabled · see Settings", "error", 6000);
    openSettingsTab(groupId);
    return;
  }
  if (nodes.length === 1) {
    openNewBrowser(nodes[0].id, groupId);
    return;
  }
  const { m, close } = modal(`<h2>Open browser</h2>
    <label>Backend<select id="nb-be">${nodes.map(b =>
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
  const engines = view.tab.bid ? (state.engCache[view.tab.bid] || []) : state.engines;
  const canQueue = backendSupportsQueuedEngineSwitch(view.tab.bid);
  /* what is already heading for the queue tail: a chat view knows its queue,
     the sidebar's lightweight caller does not and falls back to the session */
  const eff = typeof view.effectiveConfig === "function" ? view.effectiveConfig() : null;
  const pendingEngine = eff && eff.queuedEngine ? eff.engine : "";
  const { m, close } = modal(`<h2>Switch engine</h2>
    <p class="hint">The session keeps its transcript and working directory. The new engine starts a fresh
    native session seeded with a handoff of the conversation so far. Same-engine reseed is allowed
    (rebuilds context from the transcript).${canQueue ? ` While a turn runs or prompts wait, the
    switch joins the queue and applies in order - prompts sent before it keep the engine they
    were written under.` : ` Queued prompts remain; pending model and reasoning changes are
    cleared because they belong to the previous engine configuration.`}</p>
    <div class="engine-pick" id="se-engines"></div>
    <div class="m-btns"><button class="btn" id="se-cancel">Cancel</button><button class="btn btn-pri" id="se-go">Switch</button></div>`);
  const box = m.querySelector("#se-engines");
  let pick = (engines.find(engine => engine.key === (pendingEngine || s.engine)) ||
    engines[0] || {}).key || "";
  const render = () => {
    box.innerHTML = "";
    for (const e2 of engines) {
      const card = el("div", "ep" + (pick === e2.key ? " sel" : ""));
      const icon = provSpec(e2.key);
      const sub = e2.key === pendingEngine ? "Switch queued" :
        e2.key === s.engine ? "Current (reseed)" : engineStatusText(e2);
      card.innerHTML = `<div class="ep-ico prov ${icon.className}">${esc(icon.text)}</div>
        <div class="ep-name">${esc(e2.label)}</div>
        <div class="ep-sub">${esc(sub)}</div>`;
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
      if (r.queued) {
        toast(`Engine switch to ${pick} queued · applies after the queue`, "ok");
        return;
      }
      /* older nodes still report how many pending settings they cleared */
      const cleared = Math.max(0, Number(r.discarded_config_changes) || 0);
      toast(`Switched to ${pick}` + (cleared
        ? ` · cleared ${cleared} pending setting change${cleared === 1 ? "" : "s"}` : ""), "ok");
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
  toast("Failed to reach backend: " + e.message, "error");
  showAuth("login");
});
