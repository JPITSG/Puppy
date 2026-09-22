/* Run with node tests/spellcheck_ui_test.js. No browser, engine, network or
   quota: the bundled dictionary is read straight off disk and the shared
   prompt box runs against the fake DOM.

   Four things are checked here. The asset itself (its exact header, its
   declared count, its sorted order, and that the gzip sibling every browser
   is served is the same file). The checker (what it knows, what it refuses to
   look at, what it suggests, and what it will and will not correct on its
   own). The box (the browser's checker switched off, the two switches in
   its tools menu, the marks painted under the text, autocorrect finishing a
   word, and every rule about when it must keep its hands off). And the
   words added to the dictionary, which are the controller's: nothing marked
   until its list is known, Add and Remove from dictionary going over its
   routes, every send reporting its marked words, and a word the controller
   says it learned known at once with the notice saying so. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const zlib = require("node:zlib");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument, FakeEvent, fire } = require("./fake_dom.js");
const root = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(root, "puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  if (start < 0 || end < 0) throw new Error("slice not found: " + from);
  return source.slice(start, end);
};

/* ---------- the bundled asset ---------- */

const dictPath = path.join(root, "puppy/static/dict/en.txt");
const dictText = fs.readFileSync(dictPath, "utf8");
const lines = dictText.split("\n");
assert.equal(lines[0], "#puppy-dictionary 1 en");
const head = lines.findIndex(line => line.startsWith("#words "));
assert.ok(head > 0, "the asset declares its word count");
const declared = Number(lines[head].slice(7));
const words = lines.slice(head + 1);
assert.equal(words[words.length - 1], "", "the body ends in a newline");
words.pop();
assert.equal(words.length, declared, "declared count matches the body");
assert.ok(declared > 100000, "a real dictionary, not a stub");
{
  let previous = "";
  let ranked = 0;
  for (const line of words) {
    const tab = line.indexOf("\t");
    const word = tab < 0 ? line : line.slice(0, tab);
    if (tab >= 0) {
      assert.match(line.slice(tab + 1), /^[0-4]$/, "rank digit on " + line);
      ranked++;
    }
    assert.doesNotMatch(word, /[\s.\-/\\0-9]/, "a token the console could meet: " + word);
    assert.ok(word > previous, "sorted by code point: " + previous + " then " + word);
    previous = word;
  }
  assert.ok(ranked > 20000 && ranked < declared, "some words are common, not all");
}
/* Every browser is served the gzip sibling, so it must be this same file. */
const gz = zlib.gunzipSync(fs.readFileSync(dictPath + ".gz")).toString("utf8");
assert.equal(gz, dictText, "en.txt.gz is en.txt");
assert.ok(fs.existsSync(path.join(root, "puppy/static/dict/COPYRIGHT")),
  "the word list ships with its licence");

/* ---------- the checker ---------- */

const document = new FakeDocument();
const storage = new Map();
const calls = { toasts: [], menus: [], edited: 0, requests: [] };
let dictionaryReply = async () => ({ ok: true, text: async () => dictText });
/* the controller behind the dictionary's routes: what it answers next */
let replies = [];
const spellingPayload = (words, extra = {}) => ({ type: "spelling", words, learn_sends: 5, learn_days: 7,
  state_topic: "spelling", state_revision: 1, runtime_id: "r", ...extra });
const icon = () => document.createElement("svg");
const context = vm.createContext({
  document, window: { CSS: { supports: () => true }, innerWidth: 1200, innerHeight: 800 },
  CSS: { supports: () => true }, Event: FakeEvent, console, Int32Array, Number, Math, Date, Set, Map,
  URL: { createObjectURL: () => "blob:fake", revokeObjectURL: () => {} },
  AbortController, setTimeout, clearTimeout, getComputedStyle: () => ({}),
  fetch: (url, init) => dictionaryReply(url, init),
  api: async (bid, route, options = {}) => {
    calls.requests.push({ bid, route, method: options.method || "GET", body: options.body });
    const reply = replies.length ? replies.shift() : spellingPayload([]);
    if (reply instanceof Error) throw reply;
    return reply;
  },
  acceptStateSnapshot: () => true,
  toast: (text, tone) => calls.toasts.push({ text, tone }), TOAST_LONG: 7000,
  esc: text => String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"),
  apiPath: (bid, route) => (bid ? `/api/b/${bid}/` : "/api/") + route,
  fmtBytes: value => value + " B",
  plusIcon: icon, xIcon: icon, attachmentFileIcon: icon, globeIcon: icon, terminalIcon: icon,
  choiceSvg: icon, refreshIcon: icon, queueEditIcon: icon, toolsIcon: icon,
  lsGet: key => storage.has(key) ? storage.get(key) : null,
  lsSet: (key, value) => storage.set(key, value), lsDel: key => storage.delete(key),
  linkifyInto: (node, text) => { node.appendChild(document.createTextNode(text)); return node; },
  uploadSettingsFor: () => null, rememberUploadSettings: () => null,
  backendSupportsFileUploads: () => true, backendConnectionAllowed: () => true,
  nodeStateStreamActive: () => true, browserEnabledFor: () => false, vncEnabledFor: () => false,
  backendHasCapability: () => false, spawnExecFor: () => false,
  findSessionMeta: () => null, backendName: () => "this node",
  browserInstancesFor: () => null, terminalInstancesFor: () => null, uploadPreviewUrl: () => "",
  state: { backends: [], tabs: [], sessions: [], engCache: {}, engines: [], views: {}, active: "",
    remoteBrowserStatus: {}, remoteUploadSettings: {}, terminalInstances: {}, vncInstances: {},
    browserStatus: { enabled: false, instances: [] }, uploadSettings: { enabled: false } },
  /* the menu itself is choice-menu machinery with its own checks; here we
     only care which rows a box offers and what picking one does */
  openChoiceMenu: (anchor, options) => { calls.menus.push({ anchor, ...options }); return null; },
  syncOpenChoiceMenus: () => { calls.synced = (calls.synced || 0) + 1; },
});
vm.runInContext([
  between("const el = ", "/* Close buttons"),
  between("function storedStringSet(", "/* Server-side drafts are shared."),
  between("const MENTION_TOKEN_RE", "/* ================= tooltips ================= */"),
  between("const CARET_MIRROR_STYLES", "/* ctrl+j ->"),
  between("const MENTION_QUERY_MAX", "/* ================= Composer ================="),
  between("/* ================= Composer =================", "/* ================= SessionView ================="),
  between("const ATTACHMENT_PREVIEW_TYPES", "/* Resolve the configuration at the queue tail"),
].join("\n"), context);
const {
  Composer, composerBoxHtml, spellPrefs, setSpellPref, spellMenuChecks, spellDictionary,
  parseSpellDictionary, loadSpellDictionary, spellWordKnown, spellMisspellings,
  spellSuggestions, spellAutocorrection, spellRememberWord, spellForgetWord, spellLearnSent,
  spellPersonal, applySpelling, loadSpellPersonal,
} = vm.runInContext(`({ Composer, composerBoxHtml, spellPrefs, setSpellPref, spellMenuChecks,
  spellDictionary, parseSpellDictionary, loadSpellDictionary, spellWordKnown, spellMisspellings,
  spellSuggestions, spellAutocorrection, spellRememberWord, spellForgetWord, spellLearnSent,
  spellPersonal, applySpelling, loadSpellPersonal })`, context);

// A file that is not exactly the shipped shape is refused, never repaired.
assert.equal(parseSpellDictionary("#puppy-dictionary 2 en\n#words 1\nthe\n"), null);
assert.equal(parseSpellDictionary("#puppy-dictionary 1 en\n#words 2\nthe\n"), null);
assert.equal(parseSpellDictionary("#puppy-dictionary 1 en\n#words 1\nthe"), null);
assert.equal(parseSpellDictionary("#words 1\nthe\n"), null);
assert.ok(parseSpellDictionary("#puppy-dictionary 1 en\n#words 2\nthe\t0\nzebra\n"));

/* values cross the VM boundary, so compare plain node-side arrays */
const marks = text => [...spellMisspellings(text)].map(mark => mark.word);
const requests = () => JSON.parse(JSON.stringify(calls.requests));
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(r => setImmediate(r)); };

(async () => {
  // Nothing is misspelled while the dictionary is still on its way - the
  // bundled list, or the controller's list of the words added to it.
  assert.deepEqual(marks("teh recieve"), []);
  assert.equal(spellWordKnown("teh"), true);
  await loadSpellDictionary();
  assert.ok(spellDictionary.data, "the bundled dictionary loaded");
  assert.deepEqual(calls.toasts, []);
  assert.equal(spellPersonal.words, null);
  assert.deepEqual(marks("teh recieve"), [], "no marks until the added words are known");
  assert.equal(spellWordKnown("teh"), true);
  // the list over HTTP, when the stream has not brought it: one read
  replies = [spellingPayload(["Zorbium"])];
  await loadSpellPersonal();
  assert.deepEqual(calls.requests.map(r => [r.bid, r.route, r.method]), [[0, "spelling", "GET"]]);
  assert.deepEqual([...spellPersonal.words], ["zorbium"], "held lower-case, as the list keeps it");
  assert.equal(spellWordKnown("Zorbium"), true);
  assert.deepEqual(marks("teh zorbium"), ["teh"]);
  calls.requests.length = 0;
  // a read that fails is tried again later, not on every keystroke, and
  // the tools menu says why nothing is marked
  spellPersonal.words = null;
  replies = [new Error("HTTP 503")];
  await loadSpellPersonal();
  assert.equal(spellPersonal.words, null);
  assert.equal(loadSpellPersonal(), null, "no second read straight away");
  assert.equal(calls.requests.length, 1);
  assert.match(spellMenuChecks()[0].hint, /^Could not read the words added to the dictionary · HTTP 503$/);
  assert.deepEqual(calls.toasts, [], "a list that could not be read raises no notice of its own");
  // the stream's frame is the usual way the list arrives
  applySpelling(spellingPayload([]));
  assert.deepEqual([...spellPersonal.words], []);
  assert.equal(spellMenuChecks()[0].hint, "Underline words Puppy's own dictionary does not know");
  calls.requests.length = 0;

  // Words, their possessives, their cases, and the app's own supplement.
  for (const word of ["the", "receive", "separate", "definitely", "occurrence", "Puppy's",
                      "agents'", "London", "london", "USA", "usa", "colour", "color",
                      "organise", "organize", "don't", "café", "cafe", "facade",
                      "backend", "repo", "async",
                      "workflow", "ok", "Alice", "spellcheck"])
    assert.equal(spellWordKnown(word), true, word + " is a word");
  // The supplement carries what this console is typed at; SCOWL's large class
  // carries the rest of ordinary English.
  for (const word of ["idempotent", "monorepo", "symlink", "debounce", "subagent",
                      "worktree", "scrollbar", "telemetry", "unallocated", "unaffordable"])
    assert.equal(spellWordKnown(word), true, word + " is a word");
  // Broader source coverage must reach the real checker, including both
  // regional spellings and the complete verb family that prompted the fix.
  for (const word of ["anonymize", "anonymized", "anonymizes", "anonymizing",
                      "anonymise", "anonymised", "anonymises", "anonymising",
                      "deduplication", "deserialize", "deserialise", "cybersecurity",
                      "livestream", "livestreamed", "livestreaming", "livestreams",
                      "neurodiversity", "microplastic", "workaround", "uptime"])
    assert.equal(spellWordKnown(word), true, word + " is a word");
  assert.deepEqual(marks("Please anonymize this data and anonymise the screenshots"), []);
  assert.deepEqual(marks("Anonymized data needs deduplication and deserialization"), []);
  assert.deepEqual(marks("Please anonmyize this data"), ["anonmyize"]);
  assert.ok(spellSuggestions("anonmyize").some(item => item.word === "anonymize"));
  for (const word of ["anonymize", "Anonymise", "deduplication", "deserialize"])
    assert.equal(spellAutocorrection(word), "", word + " stays as typed");
  for (const word of ["anonmyize", "deserialzie", "livestreaam"])
    assert.equal(spellAutocorrection(word), "", "an added word is not an automatic target");
  for (const word of ["dependancy", "canteloupe", "anonymyze", "anonymiseing"])
    assert.equal(spellWordKnown(word), false, word + " remains a misspelling");
  /* Words English forms rather than collects. SCOWL 2020.12.07 lists none of
     these; the build derives them from SCOWL's own verbs and inflections, so
     the console stops underlining what its user actually writes. */
  for (const word of ["scrollable", "draggable", "resizable", "hoverable", "focusable",
                      "pluggable", "tappable", "pinnable", "renderable", "dismissable",
                      "cacheable", "parseable", "unscrollable", "unclickable",
                      "undraggable", "unresizable"])
    assert.equal(spellWordKnown(word), true, word + " is a formed word");
  /* and the shapes English does not form: a stem whose silent-e sibling owns
     the inflections ("changed" is "change"'s, "raged" is "rage"'s, and "rag"
     makes "raggable" instead), the "-eable" of a long Latinate verb, and "un-"
     over something already negative. */
  for (const word of ["changable", "dyable", "ragable", "wagable", "stagable",
                      "spicable", "lungable", "invalidateable", "ununsettlable"])
    assert.equal(spellWordKnown(word), false, word + " is not a word");
  assert.equal(spellWordKnown("raggable"), true, "the doubled form is the one rag makes");
  for (const word of ["teh", "recieve", "seperate", "definately", "occurence", "Teh",
                      "thsi", "kittn", "backedn"])
    assert.equal(spellWordKnown(word), false, word + " is not");

  // What is prose and what is not. Code, paths, versions, identifiers,
  // acronyms, attachment markers and "@" directives are never anyone's typo.
  assert.deepEqual(marks("Please chekc this sentance"), ["chekc", "sentance"]);
  assert.deepEqual(marks("run `npm instal foo` now"), []);
  assert.deepEqual(marks("```\nteh broekn code\n```\nand teh prose"), ["teh"]);
  assert.deepEqual(marks("```\nunclosed teh fence"), []);
  assert.deepEqual(marks("see puppy/static/app.js and /srv/projects/pupy"), []);
  assert.deepEqual(marks("the file app.js and dict.txt"), []);
  assert.deepEqual(marks("call esc(value) and spellDraw twice"), []);
  assert.deepEqual(marks("VNC RFB ACP MCP are fine"), []);
  assert.deepEqual(marks("visit https://exampel.test/thigns today"), []);
  assert.deepEqual(marks("mail someone@exampel.test now"), []);
  assert.deepEqual(marks("@Browser A8AR open teh page"), ["teh"]);
  assert.deepEqual(marks("@Spawn an agent using codex at high effort"), []);
  assert.deepEqual(marks("[image attached: /srv/data/uploads/10/x/shot.png — view it with your image/file tools]"), []);
  assert.deepEqual(marks("a well-knwon problem"), ["knwon"]);
  assert.deepEqual(marks("costs $5 and 12:30 and v1.2.3"), []);
  assert.deepEqual(marks("A sentence ending in teh."), ["teh"]);
  assert.deepEqual(marks("x".repeat(30000) + " teh"), [], "a huge draft is left alone");

  // Suggestions: ranked, cased like the typo, and reachable past one edit.
  assert.equal(spellSuggestions("teh")[0].word, "the");
  assert.equal(spellSuggestions("Teh")[0].word, "The");
  assert.equal(spellSuggestions("recieve")[0].word, "receive");
  assert.equal(spellSuggestions("seperate")[0].word, "separate");
  assert.equal(spellSuggestions("definately")[0].word, "definitely");
  assert.equal(spellSuggestions("alicce")[0].word, "Alice");
  assert.ok([...spellSuggestions("occurence")].some(item => item.word === "occurrence"));
  assert.ok([...spellSuggestions("seperatly")].some(item => item.word === "separately"),
    "a two-edit typo still finds its word");
  assert.ok(spellSuggestions("teh").length <= 6);
  // A formed word is a suggestion like any other: this is the whole point of
  // deriving them, since no single edit reaches "scrollable" from "scroll".
  assert.equal(spellSuggestions("scrolable")[0].word, "scrollable");
  assert.equal(spellSuggestions("scrollible")[0].word, "scrollable");
  assert.equal(spellSuggestions("resizeble")[0].word, "resizable");
  assert.ok([...spellSuggestions("draggble")].some(item => item.word === "draggable"));

  // Autocorrect is deliberately timid: one clear candidate, a common word,
  // and a margin over the runner-up. Everything else stays as typed.
  assert.equal(spellAutocorrection("teh"), "the");
  assert.equal(spellAutocorrection("adn"), "and");
  assert.equal(spellAutocorrection("hte"), "the");
  assert.equal(spellAutocorrection("dont"), "don't");
  assert.equal(spellAutocorrection("recieve"), "receive");
  assert.equal(spellAutocorrection("Teh"), "The");
  // a letter struck twice, or once too few times, is the commonest slip of all
  assert.equal(spellAutocorrection("woord"), "word", "not wood");
  assert.equal(spellAutocorrection("hhigh"), "high", "not thigh");
  assert.equal(spellAutocorrection("untill"), "until");
  assert.equal(spellAutocorrection("tomorow"), "tomorrow");
  assert.equal(spellAutocorrection("accross"), "across");
  assert.equal(spellAutocorrection("begining"), "beginning");
  assert.equal(spellSuggestions("woord")[0].word, "word");
  assert.equal(spellAutocorrection("the"), "", "a word it knows is left alone");
  assert.equal(spellAutocorrection("ther"), "", "an ambiguous typo is the writer's");
  assert.equal(spellAutocorrection("realy"), "", "relay or really: the writer's call");
  assert.equal(spellAutocorrection("grep"), "", "a commoner word is not reason enough");
  assert.equal(spellAutocorrection("wrd"), "", "word or ward: nothing stands clear");
  assert.equal(spellAutocorrection("ot"), "", "two letters say too little");
  assert.equal(spellAutocorrection("zzzqqq"), "", "nothing close enough");
  assert.equal(spellAutocorrection("scrolable"), "",
    "a derived word is unranked: Puppy suggests it but never types it");
  // A supplement word is ranked, because it is one this console reaches for.
  assert.equal(spellAutocorrection("idempotant"), "idempotent");
  assert.equal(spellAutocorrection("monrepo"), "monorepo");

  /* ---------- the prompt box ---------- */

  const makeBox = (host = {}) => {
    const wrap = document.createElement("div");
    wrap.innerHTML = composerBoxHtml({ placeholder: "Type…" });
    document.body.appendChild(wrap);
    const box = wrap.querySelector(".composer-box");
    const composer = new Composer(box, { bid: 0, sid: 10, submit: () => {},
      edited: () => calls.edited++, ...host });
    return { box, composer, ta: composer.ta };
  };
  /* typing, as the browser reports it: the value, the caret, then input */
  const type = (composer, text, inputType = "insertText") => {
    const ta = composer.ta;
    ta.value += text;
    ta.selectionStart = ta.selectionEnd = ta.value.length;
    fire(ta, "input", { inputType, data: text });
  };
  const painted = box => [...box.querySelectorAll(".spell-layer .sp-bad")].map(node => node.textContent);
  const layerText = box => {
    const layer = box.querySelector(".spell-layer");
    return layer ? layer.textContent : null;
  };

  // (a) the browser's own checker is off in every box, on every platform.
  const first = makeBox();
  assert.equal(first.ta.getAttribute("spellcheck"), "false");
  assert.equal(first.ta.getAttribute("autocorrect"), "off");
  assert.equal(first.ta.getAttribute("autocapitalize"), null,
    "sentence capitalisation on phones is left alone");

  // (b) every box has the tools button, with both switches in its menu.
  assert.ok(first.box.querySelector(".tools-open"), "a box without a host still has tools");
  spellPrefs.check = true;
  spellPrefs.correct = false;
  fire(first.box.querySelector(".tools-open"), "click");
  let menu = calls.menus.pop();
  assert.equal(menu.actions, true);
  assert.deepEqual([...menu.opts], []);
  assert.deepEqual([...menu.checks].map(row => [row.label, row.on, !!row.disabled]),
    [["Spell check", true, false], ["Autocorrect", false, false]]);

  // a host's own rows come first, above the spelling switches
  const hosted = makeBox({ tools: () => ({ opts: [{ value: "compact", label: "Compact" }],
    checks: [{ label: "Fast mode", on: false, onToggle: () => {} }] }),
    runTool: value => { calls.ranTool = value; } });
  fire(hosted.box.querySelector(".tools-open"), "click");
  menu = calls.menus.pop();
  assert.deepEqual([...menu.opts].map(o => o.value), ["compact"]);
  assert.deepEqual([...menu.checks].map(row => row.label),
    ["Fast mode", "Spell check", "Autocorrect"]);
  menu.onPick("compact");
  assert.equal(calls.ranTool, "compact");

  // (e) autocorrect is unavailable, and unchecked, while spell check is off.
  setSpellPref("check", false);
  assert.equal(spellPrefs.check, false);
  let rows = spellMenuChecks();
  assert.equal(rows[1].disabled, true);
  assert.equal(rows[1].on, false);
  assert.equal(storage.get("puppy.spellcheck"), "0");
  // asking for autocorrect turns the checker it needs back on
  setSpellPref("correct", true);
  assert.deepEqual([spellPrefs.check, spellPrefs.correct], [true, true]);
  assert.equal(spellMenuChecks()[1].disabled, false);
  // and switching the checker off takes autocorrect with it, for good
  setSpellPref("check", false);
  assert.deepEqual([spellPrefs.check, spellPrefs.correct], [false, false]);
  assert.equal(storage.get("puppy.autocorrect"), "0");

  // Marks: only the misspelled words, and only while the switch is on.
  setSpellPref("check", true);
  const b = makeBox();
  type(b.composer, "teh quick brown fox");
  b.composer.spellDraw(true);
  assert.deepEqual(painted(b.box), ["teh"]);
  assert.equal(layerText(b.box), "teh quick brown fox\n", "the layer mirrors the text");
  type(b.composer, " jumpd");
  b.composer.spellDraw(true);
  assert.deepEqual(painted(b.box), ["teh", "jumpd"]);
  setSpellPref("check", false);
  assert.equal(b.box.querySelector(".spell-layer"), null, "off means no marks at all");
  setSpellPref("check", true);
  assert.deepEqual(painted(b.box), ["teh", "jumpd"], "and on brings them straight back");

  /* The layer stands on the field's scrollport, not its border box: once a
     prompt overflows, the scrollbar takes its width from the text, and a
     layer as wide as the whole field wrapped its lines later than the field
     did and could not scroll as far. The box is read from the rects, so a
     field half a pixel wide - which breaks its lines at exactly that width -
     is matched to the fraction, not to the rounded clientWidth. */
  const port = makeBox();
  port.box.clientLeft = port.box.clientTop = 1;     // the box's own border
  port.box.getBoundingClientRect = () => ({ left: 100, top: 200, width: 900, height: 320 });
  port.ta.getBoundingClientRect = () => ({ left: 112.5, top: 210, width: 875.5, height: 224 });
  Object.assign(port.ta, { offsetWidth: 876, clientWidth: 866, offsetHeight: 224, clientHeight: 224,
    scrollTop: 78 });
  type(port.composer, "teh quick");
  port.composer.spellDraw(true);
  const portLayer = port.box.querySelector(".spell-layer");
  const portBox = () => [portLayer.style.left, portLayer.style.top, portLayer.style.width, portLayer.style.height];
  assert.deepEqual(portBox(), ["11.5px", "9px", "865.5px", "224px"],
    "from the box's padding edge, the field's exact width less its 10px scrollbar");
  assert.equal(portLayer.scrollTop, 78, "scrolled as far as the field");
  // a field with a border of its own: the layer stands inside it
  Object.assign(port.ta, { clientLeft: 1, clientTop: 1, clientWidth: 864, clientHeight: 222 });
  port.composer.spellSync();
  assert.deepEqual(portBox(), ["12.5px", "10px", "863.5px", "222px"]);
  // the same field with nothing to scroll: the whole padding box
  Object.assign(port.ta, { clientLeft: 0, clientTop: 0, clientWidth: 876, clientHeight: 224 });
  port.composer.spellSync();
  assert.deepEqual(portBox(), ["11.5px", "9px", "875.5px", "224px"]);

  /* An edit moves the words the marks sit under: ctrl+j puts every line
     after it one line down, and the box grows under them. The marks go with
     the text in the same tick, without waiting for the next scan. */
  const follow = makeBox();
  type(follow.composer, "teh quick brown jumpd ");
  follow.composer.spellDraw(true);
  assert.deepEqual(painted(follow.box), ["teh", "jumpd"]);
  follow.ta.selectionStart = follow.ta.selectionEnd = 9;
  follow.composer.newline();          // no spellDraw(true): the scan is still pending
  assert.equal(layerText(follow.box), "teh quick\n brown jumpd \n",
    "the layer re-wraps with the text at once");
  assert.deepEqual(painted(follow.box), ["teh", "jumpd"], "and the marks come along");
  assert.deepEqual([...follow.composer.spellMarks].map(mark => [mark.start, mark.end]),
    [[0, 3], [17, 22]], "at the offsets the newline left them at");
  // The word an edit runs through is nobody's typo until it is scanned again.
  follow.ta.value = "teh quick\n brown jmpd ";
  follow.ta.selectionStart = follow.ta.selectionEnd = 21;
  fire(follow.ta, "input", { inputType: "deleteContentBackward" });
  assert.deepEqual(painted(follow.box), ["teh"], "the word being edited drops its mark");
  follow.ta.blur();                   // the fingers leave it; now it is a typo
  follow.composer.spellDraw(true);
  assert.deepEqual(painted(follow.box), ["teh", "jmpd"], "and the scan brings it back");
  // An edit that leaves nothing marked takes the layer with it.
  follow.ta.value = "teh quick";
  follow.ta.selectionStart = follow.ta.selectionEnd = 9;
  fire(follow.ta, "input", { inputType: "deleteContentBackward" });
  assert.deepEqual(painted(follow.box), ["teh"], "what the edit did not touch stays");
  follow.ta.value = "quick";
  follow.ta.selectionStart = follow.ta.selectionEnd = 0;
  fire(follow.ta, "input", { inputType: "deleteContentBackward" });
  assert.equal(follow.box.querySelector(".spell-layer"), null);

  // The word under the typist's fingers is unfinished, not misspelled: its
  // mark waits for the word to end, or for the caret to leave it.
  const held = makeBox();
  held.ta.focus();
  type(held.composer, "teh quick wro");
  held.composer.spellDraw(true);
  assert.deepEqual(painted(held.box), ["teh"], "the word being typed carries no mark");
  assert.equal(layerText(held.box), "teh quick wro\n");
  type(held.composer, "d");
  held.composer.spellDraw(true);
  assert.deepEqual(painted(held.box), ["teh"]);
  type(held.composer, " ");
  held.composer.spellDraw(true);
  assert.deepEqual(painted(held.box), ["teh", "wrod"], "finished by a space, it is marked");
  type(held.composer, "jumpd");
  held.composer.spellDraw(true);
  assert.deepEqual(painted(held.box), ["teh", "wrod"]);
  // backspacing into the word keeps it under the fingers
  held.ta.value = "teh quick wrod jum";
  held.ta.selectionStart = held.ta.selectionEnd = held.ta.value.length;
  fire(held.ta, "input", { inputType: "deleteContentBackward" });
  held.composer.spellDraw(true);
  assert.deepEqual(painted(held.box), ["teh", "wrod"]);
  // the caret leaving the word (a click, an arrow key) finishes it
  held.ta.selectionStart = held.ta.selectionEnd = 3;
  fire(held.ta, "keyup", { key: "Home" });
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.deepEqual(painted(held.box), ["teh", "wrod", "jum"]);
  // so does leaving the box
  held.ta.selectionStart = held.ta.selectionEnd = held.ta.value.length;
  type(held.composer, "pd");
  held.composer.spellDraw(true);
  assert.deepEqual(painted(held.box), ["teh", "wrod"]);
  held.ta.blur();
  fire(held.ta, "blur");
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.deepEqual(painted(held.box), ["teh", "wrod", "jumpd"], "a blurred box hides nothing");
  // a paste lands finished text: the caret at its end holds nothing back
  held.ta.focus();
  held.ta.value = "teh quick wrod jumpd teh";
  held.ta.selectionStart = held.ta.selectionEnd = held.ta.value.length;
  fire(held.ta, "input", { inputType: "insertFromPaste", data: " teh" });
  held.composer.spellDraw(true);
  assert.deepEqual(painted(held.box), ["teh", "wrod", "jumpd", "teh"]);
  held.ta.blur();

  // Autocorrect finishes a word, and only when the switch is on.
  const c = makeBox();
  type(c.composer, "teh");
  assert.equal(c.ta.value, "teh", "still being typed");
  type(c.composer, " ");
  assert.equal(c.ta.value, "teh ", "with autocorrect off nothing moves");
  setSpellPref("correct", true);
  const d = makeBox();
  type(d.composer, "teh");
  type(d.composer, " ");
  assert.equal(d.ta.value, "the ");
  assert.equal(d.ta.selectionStart, 4, "the caret stays where the typist left it");
  assert.ok(calls.edited > 0, "a correction is a draft edit like any other");
  type(d.composer, "dont");
  type(d.composer, ".");
  assert.equal(d.ta.value, "the don't.");
  type(d.composer, " brown fox");
  assert.equal(d.ta.value, "the don't. brown fox", "a word still being typed is untouched");

  // The word came back: this box stops correcting it.
  const e = makeBox();
  type(e.composer, "teh");
  type(e.composer, " ");
  assert.equal(e.ta.value, "the ");
  e.ta.value = "teh ";                     // ctrl+z, or simply retyped
  e.ta.selectionStart = e.ta.selectionEnd = 4;
  fire(e.ta, "input", { inputType: "historyUndo" });
  assert.ok(e.composer.spellRefused.has("teh"));
  e.ta.value = "teh"; e.ta.selectionStart = e.ta.selectionEnd = 3;
  type(e.composer, " ");
  assert.equal(e.ta.value, "teh ", "a word the writer restored is left alone");
  assert.deepEqual(painted(e.box), [], "but a refused word is not marked either");

  /* Backspace, straight after a correction, is the phone keyboard's gesture:
     the word Puppy typed goes back to the one that was typed, the space that
     finished it stays, and this box leaves that word alone from then on. */
  const back = makeBox();
  back.ta.focus();
  type(back.composer, "teh");
  type(back.composer, " ");
  assert.equal(back.ta.value, "the ");
  const undoKey = new FakeEvent("keydown", { key: "Backspace" });
  back.ta.dispatchEvent(undoKey);
  assert.equal(undoKey.defaultPrevented, true, "backspace is the revert, not a deletion");
  assert.equal(back.ta.value, "teh ", "exactly what was typed, space and all");
  assert.equal(back.ta.selectionStart, 4, "ready to carry on typing");
  assert.ok(back.composer.spellRefused.has("teh"));
  assert.equal(back.composer.spellFix, null, "there is nothing left to take back");
  assert.deepEqual(painted(back.box), ["teh"],
    "a kept word is still one the dictionary does not know");
  // and finishing it again does not start the fight over
  type(back.composer, "teh");
  type(back.composer, " ");
  assert.equal(back.ta.value, "teh teh ", "the word it was told to keep is left alone");
  // the next backspace is an ordinary backspace again
  const second = new FakeEvent("keydown", { key: "Backspace" });
  back.ta.dispatchEvent(second);
  assert.equal(second.defaultPrevented, false);
  assert.equal(back.ta.value, "teh teh ");

  /* It is armed only while the correction is still the last thing that
     happened: one more keystroke, or the caret moving, and backspace deletes
     a character like it always did. */
  const late = makeBox();
  late.ta.focus();
  type(late.composer, "adn");
  type(late.composer, " ");
  assert.equal(late.ta.value, "and ");
  type(late.composer, "x");
  const typedOn = new FakeEvent("keydown", { key: "Backspace" });
  late.ta.dispatchEvent(typedOn);
  assert.equal(typedOn.defaultPrevented, false, "typing on disarms the gesture");
  assert.equal(late.ta.value, "and x");
  const moved = makeBox();
  moved.ta.focus();
  type(moved.composer, "adn");
  type(moved.composer, " ");
  assert.equal(moved.ta.value, "and ");
  moved.ta.selectionStart = moved.ta.selectionEnd = 0;
  const elsewhere = new FakeEvent("keydown", { key: "Backspace" });
  moved.ta.dispatchEvent(elsewhere);
  assert.equal(elsewhere.defaultPrevented, false, "a caret that moved disarms it too");
  assert.equal(moved.ta.value, "and ");
  // a modified backspace (delete the word before the caret) is never taken
  const held2 = makeBox();
  held2.ta.focus();
  type(held2.composer, "adn");
  type(held2.composer, " ");
  const withCtrl = new FakeEvent("keydown", { key: "Backspace", ctrlKey: true });
  held2.ta.dispatchEvent(withCtrl);
  assert.equal(withCtrl.defaultPrevented, false);
  assert.equal(held2.ta.value, "and ");

  // A paste is not typing, however it ends.
  const paste = makeBox();
  paste.ta.value = "teh "; paste.ta.selectionStart = paste.ta.selectionEnd = 4;
  fire(paste.ta, "input", { inputType: "insertFromPaste", data: "teh " });
  assert.equal(paste.ta.value, "teh ", "pasted prose is marked, never rewritten");
  paste.composer.spellDraw(true);
  assert.deepEqual(painted(paste.box), ["teh"]);

  // Deleting never corrects, an IME composition never corrects, a busy box
  // never corrects, and neither do mentions or code.
  const f = makeBox();
  type(f.composer, "teh");
  f.ta.value = "teh "; f.ta.selectionStart = f.ta.selectionEnd = 4;
  fire(f.ta, "input", { inputType: "deleteContentBackward" });
  assert.equal(f.ta.value, "teh ");
  f.composer.composing = true;
  type(f.composer, "adn ");
  assert.equal(f.ta.value, "teh adn ", "mid-composition text is never rewritten");
  f.composer.composing = false;
  f.composer.setBusy(true);
  type(f.composer, "adn ");
  assert.equal(f.ta.value, "teh adn adn ");
  f.composer.setBusy(false);
  const g = makeBox();
  type(g.composer, "`teh`");
  type(g.composer, " ");
  assert.equal(g.ta.value, "`teh` ", "code is not prose");
  type(g.composer, "@Browser A8AR");
  type(g.composer, " ");
  assert.equal(g.ta.value, "`teh` @Browser A8AR ", "a directive is not prose");

  // A draft arriving from another device is marked, never corrected.
  const h = makeBox();
  h.composer.replace("teh shared draft");
  assert.equal(h.ta.value, "teh shared draft");
  h.composer.spellDraw(true);
  assert.deepEqual(painted(h.box), ["teh"]);
  // and recalling a sent prompt behaves the same way
  h.composer.set("adn recalled");
  assert.equal(h.ta.value, "adn recalled");

  // The suggestion menu, and the personal dictionary behind it.
  const i = makeBox();
  type(i.composer, "a kittn here");
  i.composer.spellDraw(true);
  i.ta.selectionStart = i.ta.selectionEnd = 4;
  const menuEvent = new FakeEvent("contextmenu", { clientX: 40, clientY: 60 });
  i.ta.dispatchEvent(menuEvent);
  assert.equal(menuEvent.defaultPrevented, true, "the browser's own menu steps aside");
  const suggestions = calls.menus.pop();
  assert.equal(suggestions.label, "Spelling of kittn");
  assert.deepEqual([suggestions.at.x, suggestions.at.y], [40, 60]);
  assert.ok([...suggestions.opts].some(option => option.value === "kitten"));
  assert.equal(suggestions.footers[0].label, "Add to dictionary");
  suggestions.onPick("kitten");
  assert.equal(i.ta.value, "a kitten here");
  assert.deepEqual(painted(i.box), []);
  // a right-click on ordinary text leaves the browser's menu alone
  i.ta.selectionStart = i.ta.selectionEnd = 1;
  const plain = new FakeEvent("contextmenu", { clientX: 10, clientY: 10 });
  i.ta.dispatchEvent(plain);
  assert.equal(plain.defaultPrevented, false);
  assert.equal(calls.menus.length, 0);
  // "Add to dictionary" goes to the controller, and its answer - the list
  // as it now is - takes effect at once, in every box
  const j = makeBox();
  type(j.composer, "our zorbium works");
  j.composer.spellDraw(true);
  assert.deepEqual(painted(j.box), ["zorbium"]);
  calls.requests.length = 0;
  replies = [spellingPayload(["zorbium"])];
  await spellRememberWord("Zorbium");
  assert.deepEqual(requests(), [{ bid: 0, route: "spelling/words", method: "POST", body: { word: "zorbium" } }]);
  assert.deepEqual([...spellPersonal.words], ["zorbium"]);
  assert.deepEqual(painted(j.box), []);
  assert.equal(storage.has("puppy.dictionary"), false, "nothing of the list is kept in the browser");
  // a refusal is said, and the list stays as it was
  replies = [new Error("not a word the dictionary can hold")];
  await spellRememberWord("x1");
  assert.deepEqual(calls.toasts.pop(), { text: "Could not add “x1” to the dictionary · not a word the dictionary can hold", tone: "bad" });
  assert.deepEqual([...spellPersonal.words], ["zorbium"]);
  calls.requests.length = 0;

  /* ---------- what the checker learns ---------- */

  // Every send reports the message's marked words to the controller - each
  // once, lower-case, however often it appears, never a word the checker
  // does not mark - and the controller's answer is the list plus whatever
  // it learned, which is known at once and said in a notice.
  const send = text => {
    const box = makeBox();
    type(box.composer, text);
    box.composer.take();
    box.composer.destroy();
  };
  calls.toasts.length = 0;
  calls.requests.length = 0;
  replies = [spellingPayload(["zorbium"], { learned: [] })];
  send("run the eval now, the Eval matters");
  await settle();
  assert.deepEqual(requests(), [{ bid: 0, route: "spelling/sent", method: "POST", body: { words: ["eval"] } }]);
  assert.equal(spellWordKnown("eval"), false, "not yet learned");
  assert.deepEqual(calls.toasts, []);
  calls.requests.length = 0;
  replies = [spellingPayload(["eval", "zorbium"], { learned: ["eval"] })];
  send("eval and frobz, said the zorbium");
  await settle();
  assert.deepEqual(requests()[0].body, { words: ["eval", "frobz"] }, "marked words only, in order");
  assert.equal(spellWordKnown("eval"), true, "the controller's answer is the list");
  assert.equal(spellWordKnown("Eval"), true);
  assert.deepEqual(calls.toasts, [{ text: "Added “eval” to the dictionary · sent 5 times in 7 days · right-click it to remove it", tone: "ok" }]);
  calls.toasts.length = 0;
  calls.requests.length = 0;
  // the notice counts what the controller says it counts
  replies = [spellingPayload(["blorf", "eval", "zorbium"], { learned: ["blorf"], learn_sends: 3, learn_days: 2 })];
  send("blorf");
  await settle();
  assert.equal(calls.toasts[0].text, "Added “blorf” to the dictionary · sent 3 times in 2 days · right-click it to remove it");
  calls.toasts.length = 0;
  applySpelling(spellingPayload(["eval", "zorbium"]));
  calls.requests.length = 0;
  // a message with nothing marked reports nothing: code, a directive, a
  // word it knows, one the list holds
  send("run `blorf` and @Browser A8AR and the word receive, eval");
  await settle();
  assert.deepEqual(calls.requests, []);
  // nor does anything with spell check off, or before the list is known
  setSpellPref("check", false);
  send("blorf blorf");
  await settle();
  assert.deepEqual(calls.requests, []);
  setSpellPref("check", true);
  const knownWords = spellPersonal.words;
  spellPersonal.words = null;
  spellLearnSent("blorf again");
  await settle();
  assert.deepEqual(calls.requests, [], "no list, no marks, no report");
  spellPersonal.words = knownWords;
  // a report the controller could not take is a lost count, nothing more
  replies = [new Error("HTTP 503")];
  send("blorf once more");
  await settle();
  assert.equal(calls.requests.length, 1);
  assert.deepEqual(calls.toasts, []);
  assert.equal(spellWordKnown("blorf"), false);
  calls.requests.length = 0;
  // Remove from dictionary: the way back for a learned word (or one added
  // by hand), from the same menu, on the word itself
  const learned = makeBox();
  type(learned.composer, "run the eval now");
  learned.composer.spellDraw(true);
  assert.deepEqual(painted(learned.box), [], "a learned word carries no mark");
  learned.ta.selectionStart = learned.ta.selectionEnd = 10;   // inside "eval"
  const backEvent = new FakeEvent("contextmenu", { clientX: 30, clientY: 40 });
  learned.ta.dispatchEvent(backEvent);
  assert.equal(backEvent.defaultPrevented, true);
  const wayBack = calls.menus.pop();
  assert.equal(wayBack.label, "Spelling of eval");
  assert.deepEqual([...wayBack.opts].map(option => [option.label, !!option.disabled]),
    [["In the dictionary", true]]);
  assert.deepEqual([...wayBack.footers].map(footer => footer.label), ["Remove from dictionary"]);
  replies = [spellingPayload(["zorbium"])];
  await wayBack.footers[0].run();
  assert.deepEqual(requests(), [{ bid: 0, route: "spelling/words/eval", method: "DELETE" }]);
  assert.equal(spellWordKnown("eval"), false, "marked again as soon as the list comes back");
  assert.deepEqual(painted(learned.box), ["eval"]);
  calls.requests.length = 0;
  // a word with an apostrophe travels encoded
  applySpelling(spellingPayload(["agents’", "zorbium"]));
  replies = [spellingPayload(["zorbium"])];
  await spellForgetWord("agents’");
  assert.equal(calls.requests[0].route, "spelling/words/agents%E2%80%99");
  calls.requests.length = 0;
  // a right-click on a word the bundled dictionary knows still leaves the
  // browser's menu alone
  learned.ta.selectionStart = learned.ta.selectionEnd = 5;      // inside "the"
  const known = new FakeEvent("contextmenu", { clientX: 10, clientY: 10 });
  learned.ta.dispatchEvent(known);
  assert.equal(known.defaultPrevented, false);
  assert.equal(calls.menus.length, 0);
  learned.composer.destroy();

  // Leaving the box takes its marks with it.
  const k = makeBox();
  type(k.composer, "teh");
  k.composer.spellDraw(true);
  assert.equal(painted(k.box).length, 1);
  k.composer.destroy();
  assert.equal(k.box.querySelector(".spell-layer"), null);
  // and sending empties them without leaving a stale layer behind
  const l = makeBox();
  type(l.composer, "teh end");
  l.composer.spellDraw(true);
  assert.equal(painted(l.box).length, 1);
  l.composer.take();
  assert.equal(l.box.querySelector(".spell-layer"), null);

  // A dictionary that cannot be read says so once and leaves the box usable.
  const failing = vm.createContext(Object.assign({}, context));
  spellDictionary.data = null;
  spellDictionary.error = "";
  spellDictionary.failedAt = 0;
  dictionaryReply = async () => ({ ok: false, status: 404 });
  await loadSpellDictionary();
  assert.equal(spellDictionary.data, null);
  assert.equal(calls.toasts.length, 1);
  assert.match(calls.toasts[0].text, /^Could not load the spelling dictionary · HTTP 404$/);
  assert.equal(calls.toasts[0].tone, "bad");
  assert.match(spellMenuChecks()[0].hint, /^Could not load the bundled dictionary/);
  const m = makeBox();
  type(m.composer, "teh end ");
  assert.equal(m.ta.value, "teh end ", "no dictionary, no corrections and no marks");
  assert.equal(m.box.querySelector(".spell-layer"), null);
  // a failure is not retried on every keystroke
  assert.equal(loadSpellDictionary(), null);
  void failing;

  await settle();
  console.log("PASS: bundled dictionary shape, what the checker knows and skips, " +
    "suggestions and timid autocorrection, the prompt box's switches, marks and menus, " +
    "and the controller's added words - nothing marked until known, Add and Remove over its routes, " +
    "every send reporting its marked words, and a learned word known at once with its notice");
})().catch(error => { console.error(error); process.exit(1); });
