/* A small in-process DOM for the console's node-run UI checks: enough of
   Element, Document and Event for the shared prompt box and the dialogs that
   host it - a tree, class lists, attributes, simple selectors, listeners with
   bubbling, focus, and an innerHTML parser for the app's own markup. Nothing
   here lays anything out; every measurement is zero. */
"use strict";

const VOID = new Set(["input", "br", "img", "hr", "meta", "link", "path", "circle", "rect", "line"]);
const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'", nbsp: " " };
const decode = text => text.replace(/&(amp|lt|gt|quot|#39|nbsp);/g, (_, key) => ENTITIES[key]);

class FakeEvent {
  constructor(type, init = {}) {
    this.type = type;
    this.bubbles = false;
    this.cancelable = true;
    Object.assign(this, init);
    this.defaultPrevented = false;
    this.propagationStopped = false;
    this.target = null;
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.propagationStopped = true; }
}

function matchesCompound(node, compound) {
  const attributes = [...compound.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)];
  compound = compound.replace(/\[([\w-]+)(?:="([^"]*)")?\]/g, "");
  for (const [, name, value] of attributes) {
    if (!node.hasAttribute(name) || (value !== undefined && node.getAttribute(name) !== value)) return false;
  }
  const parts = /^([a-zA-Z][\w-]*|\*)?((?:[#.][\w-]+)*)$/.exec(compound);
  if (!parts) throw new Error("unsupported selector: " + compound);
  if (parts[1] && parts[1] !== "*" && node.tag !== parts[1].toLowerCase()) return false;
  for (const token of (parts[2] || "").match(/[#.][\w-]+/g) || []) {
    if (token[0] === "#" && node.id !== token.slice(1)) return false;
    if (token[0] === "." && !node._classes.has(token.slice(1))) return false;
  }
  return true;
}

/* compound selectors joined by descendant combinators; lists split on commas */
function matchesSelector(node, selector) {
  const compounds = selector.trim().split(/\s+/);
  if (!matchesCompound(node, compounds[compounds.length - 1])) return false;
  let ancestor = node.parentNode;
  for (let i = compounds.length - 2; i >= 0; i--) {
    while (ancestor && !(ancestor instanceof FakeElement && matchesCompound(ancestor, compounds[i])))
      ancestor = ancestor.parentNode;
    if (!ancestor) return false;
    ancestor = ancestor.parentNode;
  }
  return true;
}

function walk(root, visit) {
  for (const child of root.children) {
    visit(child);
    walk(child, visit);
  }
}

class FakeElement {
  constructor(tag, doc) {
    this.tag = String(tag).toLowerCase();
    this.tagName = this.tag.toUpperCase();
    this.ownerDocument = doc;
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this._classes = new Set();
    this._text = "";
    this.value = "";
    this.disabled = false;
    this.readOnly = false;
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.scrollHeight = 0; this.clientHeight = 0; this.offsetHeight = 0;
    this.scrollWidth = 0; this.clientWidth = 0; this.offsetWidth = 0; this.scrollTop = 0;
    this.files = null;
    this.checked = false;
    const classes = this._classes;
    this.classList = {
      add: (...keys) => keys.forEach(key => classes.add(key)),
      remove: (...keys) => keys.forEach(key => classes.delete(key)),
      toggle: (key, on) => {
        const want = on === undefined ? !classes.has(key) : !!on;
        if (want) classes.add(key); else classes.delete(key);
        return want;
      },
      contains: key => classes.has(key),
    };
  }
  get className() { return [...this._classes].join(" "); }
  set className(value) {
    this._classes.clear();
    for (const key of String(value).split(/\s+/)) if (key) this._classes.add(key);
  }
  get id() { return this.attributes.id || ""; }
  set id(value) { this.attributes.id = String(value); }
  get isConnected() {
    for (let node = this; node; node = node.parentNode) if (node === this.ownerDocument.body) return true;
    return false;
  }
  get firstElementChild() { return this.children[0] || null; }
  get parentElement() { return this.parentNode instanceof FakeElement ? this.parentNode : null; }
  get nextSibling() {
    if (!this.parentNode) return null;
    return this.parentNode.children[this.parentNode.children.indexOf(this) + 1] || null;
  }
  get options() { return this.tag === "select" ? this.children : undefined; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set textContent(value) { this._text = String(value); this.children = []; }
  set innerHTML(html) { this.children = []; this._text = ""; parseInto(this, String(html)); }
  get innerHTML() { return this.textContent; }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
  insertBefore(child, reference) {
    if (!reference) return this.appendChild(child);
    if (child === reference) return child;
    if (!this.children.includes(reference)) throw new Error("reference is not a child");
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.children.splice(this.children.indexOf(reference), 0, child);
    return child;
  }
  cloneNode(deep = false) {
    const clone = this.ownerDocument.createElement(this.tag);
    for (const [key, value] of Object.entries(this.attributes)) clone.setAttribute(key, value);
    clone.className = this.className;
    Object.assign(clone.dataset, this.dataset);
    Object.assign(clone.style, this.style);
    clone._text = this._text;
    if (deep) for (const child of this.children) clone.appendChild(child.cloneNode(true));
    return clone;
  }
  replaceChildren(...nodes) {
    for (const child of [...this.children]) this.removeChild(child);
    this._text = "";
    this.append(...nodes);
  }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    child.parentNode = null;
    return child;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  setAttribute(key, value) {
    this.attributes[key] = String(value);
    if (key === "class") this.className = value;
    else if (key === "disabled") this.disabled = true;
    else if (key === "checked") this.checked = true;   // a checkbox's initial state, like the browser's
    else if (key === "value") this.value = String(value);
    else if (key.startsWith("data-")) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
  }
  getAttribute(key) {
    if (key === "class") return this.className;
    if (key.startsWith("data-")) {
      const name = key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      return name in this.dataset ? String(this.dataset[name]) : null;
    }
    return key in this.attributes ? this.attributes[key] : null;
  }
  removeAttribute(key) {
    delete this.attributes[key];
    if (key.startsWith("data-")) delete this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())];
    if (key === "disabled") this.disabled = false;
  }
  hasAttribute(key) { return this.getAttribute(key) !== null; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter(item => item !== fn);
  }
  dispatchEvent(event) {
    if (!event.target) event.target = this;
    for (let node = this; node; node = node.parentNode) {
      for (const fn of (node.listeners[event.type] || []).slice()) fn.call(node, event);
      const handler = node["on" + event.type];
      if (typeof handler === "function") handler.call(node, event);
      if (!event.bubbles || event.propagationStopped) return !event.defaultPrevented;
    }
    if (event.bubbles) this.ownerDocument.dispatchEvent(event);
    return !event.defaultPrevented;
  }
  focus() { this.ownerDocument.activeElement = this; }
  blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = null; }
  click() { if (!this.disabled) this.dispatchEvent(new FakeEvent("click", { bubbles: true })); }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  matches(selector) { return selector.split(",").some(part => matchesSelector(this, part)); }
  closest(selector) {
    for (let node = this; node instanceof FakeElement; node = node.parentNode)
      if (node.matches(selector)) return node;
    return null;
  }
  contains(other) {
    for (let node = other; node; node = node.parentNode) if (node === this) return true;
    return false;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const found = [];
    walk(this, node => { if (node.matches(selector) && !found.includes(node)) found.push(node); });
    return found;
  }
  scrollIntoView() {}
  getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
}

const TAG_RE = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^\s=>\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|([^<]+)/g;
const ATTR_RE = /([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;

function parseInto(parent, html) {
  const stack = [parent];
  TAG_RE.lastIndex = 0;
  let match;
  while ((match = TAG_RE.exec(html)) !== null) {
    if (match[0].startsWith("<!--")) continue;
    const top = stack[stack.length - 1];
    if (match[1]) {
      const tag = match[1].toLowerCase();
      for (let i = stack.length - 1; i > 0; i--) if (stack[i].tag === tag) { stack.length = i; break; }
    } else if (match[2]) {
      const node = new FakeElement(match[2], parent.ownerDocument);
      ATTR_RE.lastIndex = 0;
      let attr;
      while ((attr = ATTR_RE.exec(match[3] || "")) !== null) {
        const value = attr[2] !== undefined ? attr[2] : attr[3] !== undefined ? attr[3] :
          attr[4] !== undefined ? attr[4] : "";
        node.setAttribute(attr[1], decode(value));
      }
      top.appendChild(node);
      if (!match[4] && !VOID.has(node.tag)) stack.push(node);
    } else if (match[5]) {
      const text = decode(match[5]);
      if (top.tag === "textarea") top.value += text;
      else if (text.trim()) top._text += text;
    }
  }
}

class FakeDocument {
  constructor() {
    this.listeners = {};
    this.activeElement = null;
    this.body = new FakeElement("body", this);
  }
  createElement(tag) { return new FakeElement(tag, this); }
  createElementNS(_ns, tag) { return new FakeElement(tag, this); }
  createTextNode(text) { const node = new FakeElement("#text", this); node._text = String(text); return node; }
  getElementById(id) { return this.body.querySelector("#" + id); }
  querySelector(selector) { return this.body.querySelector(selector); }
  querySelectorAll(selector) { return this.body.querySelectorAll(selector); }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter(item => item !== fn);
  }
  dispatchEvent(event) {
    for (const fn of (this.listeners[event.type] || []).slice()) fn.call(this, event);
    return !event.defaultPrevented;
  }
}

/* keydown/paste/drop as the box receives them: a FakeEvent with the fields
   the app reads, dispatched with bubbling like the browser's */
function fire(node, type, init = {}) {
  const event = new FakeEvent(type, { bubbles: true, ...init });
  node.dispatchEvent(event);
  return event;
}

module.exports = { FakeDocument, FakeElement, FakeEvent, fire };
