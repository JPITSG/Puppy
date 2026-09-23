/* Run with node tests/question_ui_test.js. The card a question the engine
   asks becomes, against the fake DOM: the reader on the node's rows and the
   engine's raw input, the form's radios, checkboxes, Other box, text and
   number fields, Answer waiting for an answer, the reply by row index, Skip,
   the socket guards, the fallback to the plain permission card, and the
   transcript's tool card marking what was chosen. No browser, engine,
   network or model quota. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
function between(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, from);
  return source.slice(start, end);
}
const document = new FakeDocument();
const el = (tag, cls = "", text = "") => {
  const node = document.createElement(tag); node.className = cls; node.textContent = text; return node;
};
const notices = [];
const icons = [];
const svg = name => () => { icons.push(name); return el("svg", name); };
const context = vm.createContext({
  console, document, el, FakeEvent,
  askActionIcon: svg("ask"), globeIcon: svg("globe"), attachmentFileIcon: svg("file"),
  queueEditIcon: svg("edit"), terminalIcon: svg("terminal"), checkIcon: svg("check"),
  agentIcon: svg("agent"), toolsIcon: svg("tools"), searchIcon: svg("search"), xIcon: svg("x"),
  choiceSvg: svg("arrow"), promptSpinnerNode: () => el("span", "spinner"),
  md: text => `<p>${text}</p>`, decorateMarkdownLinks() {},
  linkifyInto: (node, text) => { node.textContent = text; return node; },
  toast: (...args) => notices.push(args), TOAST_LONG: 7000,
  backendName: () => "Main", remoteStoppingMessage: () => "", backendConnectionAllowed: () => true,
});
vm.runInContext([
  between("const TOOL_ICON_DRAWERS =", "/* A side question is put"),
  between("/* ================= questions the engine asks", "const ATTACHMENT_PREVIEW_TYPES"),
  between("/* A result belongs inside its call's card", "function backgroundTaskStateInto("),
  between("class SessionView {", "/* ================= TermView"),
  "globalThis.SessionView = SessionView;",
].join("\n"), context);
const { SessionView, questionRows, toolCardNode, fillToolResultInto, toolSummary, toolIconNode } = context;
const mount = node => { document.body.appendChild(node); return node; };
const texts = nodes => [...nodes].map(node => node.textContent);
/* values made inside the VM realm, flattened for strict deep equality */
const plain = value => JSON.parse(JSON.stringify(value));

const RAW = {
  title: "Release",
  questions: [
    { question: "The build is ready. Deploy now?", header: "Deploy", multiSelect: false,
      options: [{ label: "Deploy now (Recommended)", description: "Restart both apps" },
                { label: "Wait", description: "Keep the running build" }] },
    { question: "Which signals should I watch?", header: "Signals", multiSelect: true,
      options: [{ label: "Logs" }, { label: "Metrics" }, { label: "Traces, spans", preview: "```\ntrace\n```" }] },
  ],
};
/* the rows puppy/questions.py sends on the request */
const ROWS = [
  { index: 0, key: RAW.questions[0].question, question: RAW.questions[0].question, header: "Deploy",
    description: "", kind: "choice", multi: false, options: RAW.questions[0].options },
  { index: 1, key: RAW.questions[1].question, question: RAW.questions[1].question, header: "Signals",
    description: "", kind: "choice", multi: true, options: RAW.questions[1].options },
  { index: 2, key: "Anything else?", question: "Anything else?", header: "Notes", description: "One line is fine",
    kind: "text", multi: false, options: [], placeholder: "e.g. hold the release" },
  { index: 3, key: "How many replicas?", question: "How many replicas?", header: "", description: "",
    kind: "number", multi: false, options: [], min: 1, max: 8, default: 2, unit: "pods" },
];

(async () => {
  /* ---- the reader: the node's rows and the engine's own input read alike */
  const fromRaw = questionRows("AskUserQuestion", RAW);
  assert.equal(fromRaw.title, "Release");
  assert.deepEqual(plain(fromRaw.rows.map(row => [row.index, row.header, row.kind, row.multi, row.options.length])),
    [[0, "Deploy", "choice", false, 2], [1, "Signals", "choice", true, 3]]);
  assert.equal(fromRaw.rows[1].options[2].preview, "```\ntrace\n```");
  const fromRows = questionRows("AskUserQuestion", { questions: ROWS, title: "" });
  assert.deepEqual(plain(fromRows.rows.map(row => [row.key, row.kind, row.multi])),
    ROWS.map(row => [row.key, row.kind, row.multi]));
  assert.deepEqual([fromRows.rows[3].min, fromRows.rows[3].max, fromRows.rows[3].default, fromRows.rows[3].unit], [1, 8, 2, "pods"]);
  const loose = questionRows("mcp__ide__ask_user_question", {
    questions: [{ text: " Colour? ", title: "Hue", choices: ["Red", "Blue", "Red", ""], multi_select: "true", type: "Select" },
                { prompt: "Name", kind: "freeform" }, { question: "Empty", options: [] }, "Plain", null, { header: "x" }] });
  assert.deepEqual(plain(loose.rows.map(row => [row.question, row.kind, row.multi, row.options.map(o => o.label)])),
    [["Colour?", "choice", true, ["Red", "Blue"]], ["Name", "text", false, []], ["Empty", "text", false, []], ["Plain", "text", false, []]]);
  assert.equal(loose.rows[0].key, " Colour? ", "the key is the engine's own text");
  assert.equal(questionRows("AskUserQuestion", { question: "Go?", options: ["Yes", "No"] }).rows[0].options.length, 2);
  assert.equal(questionRows("Survey", { questions: [{ question: "Q", options: ["A"] }] }).rows.length, 1);
  for (const [tool, input] of [["TodoWrite", { items: [{ title: "milk" }] }], ["Bash", { command: "ls" }],
                               ["AskUserQuestion", { questions: [] }], ["AskUserQuestion", {}], ["AskUserQuestion", null]])
    assert.equal(questionRows(tool, input), null, tool);

  /* ---- the card */
  const sent = [];
  const view = Object.create(SessionView.prototype);
  Object.assign(view, { tab: { bid: 0, sid: 1 }, root: mount(el("div")),
    approvalEl: el("div", "approval hidden"), reconnecting: false, questionForm: null,
    scroll: el("div", "chat-scroll"), tailPill: el("div", "tail-pill"), queueEl: el("div", "queue-strip"),
    scrollBottom() {}, renderStatus() {}, updateSteerControl() {}, setBackgroundTasks() {},
    ws: { readyState: 1, send: text => sent.push(JSON.parse(text)) } });
  view.root.append(view.scroll, view.queueEl, view.approvalEl);
  view.scroll.appendChild(view.tailPill);
  const req = { request_id: "perm-q", tool_name: "AskUserQuestion", input: RAW, kind: "question",
    questions: ROWS, question_title: "Release", suggestions: [] };
  const card = view.approvalEl;
  const buttons = () => [...card.querySelectorAll(".ap-btns button")];
  const answerBtn = () => buttons().find(b => b.textContent === "Answer");
  const skipBtn = () => buttons().find(b => b.textContent === "Skip");
  const form = () => card.querySelector("form.aq-form");
  const submit = () => form().onsubmit(new FakeEvent("submit"));
  const changed = () => form().onchange(new FakeEvent("change"));
  view.showApproval(req);
  assert.ok(card.classList.contains("question") && !card.classList.contains("hidden"));
  assert.equal(card.querySelector(".ap-title-text").textContent, "Question · Release");
  assert.equal(icons.at(-1), "ask", "the title carries the drawn ask mark");
  assert.deepEqual(texts(card.querySelectorAll(".aq-header")), ["Deploy", "Signals", "Notes"]);
  assert.deepEqual(texts(card.querySelectorAll(".aq-q")), ROWS.map(row => row.question));
  assert.deepEqual(texts(card.querySelectorAll(".aq-desc")), ["One line is fine"]);
  assert.deepEqual(texts(buttons()), ["Skip", "Answer"], "Skip first, Answer last");
  assert.equal(answerBtn().type, "submit");
  const items = card.querySelectorAll(".aq-item");
  assert.equal(items.length, 4);
  const inputs = item => [...item.querySelectorAll("input")];
  assert.deepEqual(inputs(items[0]).map(i => i.type), ["radio", "radio", "radio", "text"], "a single choice: radios and Other");
  assert.deepEqual(inputs(items[1]).map(i => i.type), ["checkbox", "checkbox", "checkbox", "checkbox", "text"]);
  assert.ok(inputs(items[0]).every(i => i.type === "text" || i.name === "aq-0"));
  assert.ok(inputs(items[1]).every(i => i.type === "text" || i.name === "aq-1"));
  assert.deepEqual(texts(items[0].querySelectorAll(".aq-opt-label")), ["Deploy now (Recommended)", "Wait"]);
  assert.deepEqual(texts(items[0].querySelectorAll(".aq-opt-desc")), ["Restart both apps", "Keep the running build"]);
  assert.equal(items[1].querySelectorAll(".aq-preview").length, 1, "an option's preview is drawn under it");
  assert.equal(items[1].querySelector(".aq-preview .md").textContent, "```\ntrace\n```", "the preview is rendered as markdown");
  assert.equal(items[0].querySelector(".aq-other-input").placeholder, "Other…");
  assert.equal(items[2].querySelector("input.aq-text").placeholder, "e.g. hold the release");
  const number = items[3].querySelector("input.aq-number");
  assert.deepEqual([number.type, number.getAttribute("min"), number.getAttribute("max"), number.value], ["number", "1", "8", "2"]);
  assert.equal(items[3].querySelector(".aq-unit").textContent, "1 – 8 pods");
  assert.ok(card.querySelectorAll("input").every(i => i.type !== "text" || i.getAttribute("spellcheck") === "false"));
  assert.equal(answerBtn().disabled, false, "a number's default is already an answer");
  assert.equal(skipBtn().disabled, false);
  number.value = "";
  changed();
  assert.equal(answerBtn().disabled, true, "nothing answered: Answer waits");
  assert.equal(skipBtn().disabled, false, "Skip is always a choice");
  submit();
  assert.equal(sent.length, 0, "Enter with nothing answered sends nothing");

  /* every kind of answer, by row index */
  inputs(items[0])[1].checked = true;                       // Wait
  inputs(items[1])[0].checked = true;                       // Logs
  inputs(items[1])[2].checked = true;                       // Traces, spans
  const other1 = items[1].querySelector(".aq-other-input");
  other1.value = "the deploy channel";
  other1.oninput(new FakeEvent("input"));
  assert.equal(inputs(items[1])[3].checked, true, "typing in Other checks it");
  items[2].querySelector("input.aq-text").value = "  hold until Monday ";
  number.value = "4";
  changed();
  assert.equal(answerBtn().disabled, false);
  submit();
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0], { type: "approval_response", request_id: "perm-q", behavior: "allow",
    answers: { 0: "Wait", 1: ["Logs", "Traces, spans", "the deploy channel"], 2: "hold until Monday", 3: "4" } });
  assert.equal(card.getAttribute("aria-busy"), "true");
  assert.ok(buttons().every(b => b.disabled), "one reply while confirmation is pending");
  submit(); skipBtn().onclick();
  assert.equal(sent.length, 1);
  view.handle({ type: "approval_resolved", request_id: "older" });
  assert.ok(!card.classList.contains("hidden"), "an old acknowledgement dismisses nothing");
  view.handle({ type: "approval_resolved", request_id: "perm-q" });
  assert.ok(card.classList.contains("hidden") && !card.classList.contains("question"));
  assert.equal(view.approvalRequest, null);
  assert.equal(view.questionForm, null);
  assert.equal(card.innerHTML, "");

  /* the Other box alone, on a single choice, is the person's own words;
     an Other box checked but empty is no answer */
  view.showApproval(req);
  card.querySelector("input.aq-number").value = "";
  const first = card.querySelectorAll(".aq-item")[0];
  const other0 = first.querySelector(".aq-other-input");
  inputs(first)[2].checked = true;
  changed();
  assert.equal(answerBtn().disabled, true, "Other without words is not an answer");
  other0.value = "ship it, then tell me";
  other0.oninput(new FakeEvent("input"));
  changed();
  submit();
  assert.deepEqual(sent[1].answers, { 0: "ship it, then tell me" });
  view.handle({ type: "approval_resolved", request_id: "perm-q" });

  /* Skip: an explicit empty map, never a deny */
  view.showApproval(req);
  skipBtn().onclick();
  assert.deepEqual(sent[2], { type: "approval_response", request_id: "perm-q", behavior: "allow", answers: {} });
  view.handle({ type: "approval_resolved", request_id: "perm-q" });

  /* the socket guards the permission card has */
  view.showApproval(req);
  card.querySelectorAll(".aq-item")[0].querySelectorAll("input")[0].checked = true;
  changed();
  for (const disconnected of [null, { readyState: 0 }, { readyState: 3 }]) {
    view.ws = disconnected;
    view.setReconnecting(true);
    assert.ok(buttons().every(b => b.disabled), "disconnected: every choice disables");
    submit(); skipBtn().onclick();
    assert.equal(sent.length, 3);
    assert.ok(!card.classList.contains("hidden"));
  }
  view.ws = { readyState: 1, send: () => { throw new Error("socket closed"); } };
  view.setReconnecting(false);
  assert.ok(buttons().every(b => !b.disabled));
  submit();
  assert.match(notices.at(-1)[0], /^Main: Approval response could not be sent/);
  assert.ok(!card.classList.contains("hidden"));
  view.ws = { readyState: 1, send: text => sent.push(JSON.parse(text)) };
  submit();
  assert.deepEqual(sent[3].answers, { 0: "Deploy now (Recommended)", 3: "2" }, "the number's default rides along");
  view.handle({ type: "approval_resolved", request_id: "perm-q" });

  /* a question request the console cannot read is the plain permission card */
  view.showApproval({ request_id: "perm-x", tool_name: "AskUserQuestion", kind: "question", questions: [], input: {} });
  assert.ok(!card.classList.contains("question"));
  assert.deepEqual(texts(buttons()), ["Allow", "Deny"]);
  assert.equal(view.questionForm, null);
  view.handle({ type: "approval_resolved", request_id: "perm-x" });
  view.showApproval({ request_id: "perm-b", tool_name: "Bash", input: { command: "ls" }, suggestions: [] });
  assert.ok(!card.classList.contains("question"));
  assert.ok(card.querySelector("pre"), "a permission card keeps its command");
  view.hideApproval();

  /* ---- the transcript's tool card */
  assert.equal(toolSummary("AskUserQuestion", RAW), "The build is ready. Deploy now? · Which signals should I watch?");
  icons.length = 0;
  toolIconNode("AskUserQuestion");
  assert.equal(icons.at(-1), "ask");
  const tool = mount(toolCardNode({ tool: "AskUserQuestion", input: RAW, tool_use_id: "toolu_1" }));
  assert.equal(tool.querySelector(".t-name").textContent, "AskUserQuestion");
  assert.equal(tool.querySelector(".tb-label").textContent, "questions");
  assert.ok(!tool.querySelector(".tool-body pre"), "the questions stand in for the raw JSON");
  assert.equal(tool.querySelector(".aq-title").textContent, "Release");
  assert.deepEqual(texts(tool.querySelectorAll(".aq-opt-label")), ["Deploy now (Recommended)", "Wait", "Logs", "Metrics", "Traces, spans"]);
  assert.equal(tool.querySelectorAll("input").length, 0, "the card's list takes no input");
  fillToolResultInto(tool, { content: 'Your questions have been answered: "The build is ready. Deploy now?"="Deploy now (Recommended)", ' +
    '"Which signals should I watch?"="Logs, "Traces, spans"". You can now continue with these answers in mind.' });
  assert.deepEqual(texts(tool.querySelectorAll(".aq-opt.chosen .aq-opt-label")), ["Deploy now (Recommended)", "Logs", "Traces, spans"]);
  assert.equal(tool.querySelectorAll(".aq-answer").length, 0);
  assert.deepEqual(texts(tool.querySelectorAll(".tb-label")), ["questions", "result"]);
  assert.ok(tool.querySelector(".tool-body pre").textContent.startsWith("Your questions have been answered"));
  const own = mount(toolCardNode({ tool: "AskUserQuestion", input: RAW, tool_use_id: "toolu_2" }));
  fillToolResultInto(own, { content: 'The user answered: "The build is ready. Deploy now?"="ship it, then tell me", ' +
    '"Which signals should I watch?"="(no option selected)". Read the answers carefully.' });
  assert.equal(own.querySelectorAll(".aq-opt.chosen").length, 0);
  assert.deepEqual(texts(own.querySelectorAll(".aq-answer-text")), ["ship it, then tell me"]);
  const none = mount(toolCardNode({ tool: "AskUserQuestion", input: RAW, tool_use_id: "toolu_3" }));
  fillToolResultInto(none, { content: "The user did not answer the questions." });
  assert.equal(none.querySelectorAll(".aq-opt.chosen, .aq-answer").length, 0);
  const failed = mount(toolCardNode({ tool: "AskUserQuestion", input: RAW, tool_use_id: "toolu_4" }));
  fillToolResultInto(failed, { content: '"The build is ready. Deploy now?"="Wait"', is_error: true });
  assert.equal(failed.querySelectorAll(".aq-opt.chosen").length, 0, "an error names no choice");
  const bash = mount(toolCardNode({ tool: "Bash", input: { command: "ls" }, tool_use_id: "toolu_5" }));
  assert.ok(bash.querySelector(".tool-body pre") && !bash.querySelector(".aq-list"));
  console.log("PASS: the question card's form, replies, guards and fallback, and the tool card's reading of the answers");
})().catch(error => { console.error(error); process.exitCode = 1; });
