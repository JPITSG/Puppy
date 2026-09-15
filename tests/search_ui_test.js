/* Run with node tests/search_ui_test.js. No browser, engine, network or quota.
   The Search tab's results against the fake DOM, through the real SearchView:
   a query fanned out to the online backends, the "Show all N matches" button
   a session with more hits than the first page gets, the page it loads and
   the pages after it, a failure that hands the button back with its own
   label, and the page requests cancelled - silently - when a new search
   replaces the results or the tab closes, while a page that Back has moved
   into history's memory still lands in its own list. Then the live filters:
   a backend, kind, time or sort change once a search has run searches again
   for the results' own query and leaves the box's draft alone, while a
   filter before the first search, a choice already made, the last kind, a
   reachability repaint and typing ask nothing. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { FakeDocument, FakeEvent } = require("./fake_dom.js");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/app.js"), "utf8");
const between = (from, to) => {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  if (start < 0 || end < 0) throw new Error("slice not found: " + from);
  return source.slice(start, end);
};

const document = new FakeDocument();
const el = (tag, cls = "", text = "") => {
  const node = document.createElement(tag); node.className = cls; node.textContent = text; return node;
};
const state = {
  backends: [{ id: 7, name: "Peer", capabilities: ["session-search"] },
             { id: 8, name: "Asleep", capabilities: ["session-search"] }],
  remoteOk: { 7: true, 8: false },
};
/* Every request is held until the test answers it, the way a backend
   answers in its own time, and honours the signal the way api() does: an
   abort rejects at once with a cancelled error. */
let requests = [], toasts = [], opened = [], stops = 0;
function api(bid, route, options = {}) {
  const request = { bid, route, options, params: new URLSearchParams(route.split("?")[1] || "") };
  requests.push(request);
  return new Promise((resolve, reject) => {
    request.resolve = resolve;
    request.reject = reject;
    if (options.signal) options.signal.addEventListener("abort", () => reject(
      Object.assign(new Error("Request cancelled"), { cancelled: true })), { once: true });
  });
}
const context = vm.createContext({
  document, el, state, api, console, setTimeout, clearTimeout, AbortController, URLSearchParams,
  toast: (text, tone) => toasts.push({ text: context.toastText(text), tone }),
  navigationRemember() {}, navigationChanged() { stops++; },
  lsGet: () => null, lsSet() {},
  backendName: bid => bid ? state.backends.find(node => node.id === bid).name : "Studio",
  sessDot: () => el("span", "sdot"), provIcon: () => el("svg"),
  sessionLocationTitle: session => session.cwd || "",
  fmtStamp: ts => "t" + ts,
  openSessionLocation: (bid, sid, session, seq) => opened.push({ bid, sid, seq }),
});
vm.runInContext([
  "let navigationSearchSequence = 0;",
  between("function toastText(", "/* The console's one tone vocabulary"),
  between("const SEARCH_KIND_CHIPS =", "class SettingsView {"),
  "Object.assign(globalThis, { SearchView, SEARCH_PER_SESSION, SEARCH_PAGE, SEARCH_KIND_CHIPS });",
].join("\n"), context);
const { SearchView, SEARCH_PER_SESSION, SEARCH_PAGE, SEARCH_KIND_CHIPS } = context;

const settle = () => new Promise(resolve => setImmediate(resolve));
const match = (seq, kind = "assistant") => ({ seq, kind, ts: seq, snippet: `hit ${seq} here` });
const group = (id, total, shown = Math.min(total, SEARCH_PER_SESSION)) => ({
  session: { id, name: `Session ${id}`, engine: "claude", cwd: "/p" },
  total, matches: Array.from({ length: shown }, (_, i) => match(i + 1)),
});
const buttons = root => root.querySelectorAll("button");
const showAll = hit => buttons(hit).find(node => node.classList.contains("sh-more"));
const rows = hit => hit.querySelector(".sh-matches").children;

async function search(view, query, replies) {
  view.input.value = query;
  const pending = view.runSearch();
  await settle();
  const answered = requests.splice(0);
  assert.equal(answered.length, Object.keys(replies).length, "one request per online backend");
  for (const request of answered) request.resolve(replies[request.bid]);
  await pending;
}

async function main() {
  const view = new SearchView({ id: "search", type: "search" });
  document.body.appendChild(view.root);

  /* The query goes to this console and the online peer only; a session
     with more matches than its first page carries the button. */
  await search(view, "dashboard", {
    0: { total: 14, session_total: 2, sessions: [group(1, 12), group(2, 2)] },
    7: { total: 3, session_total: 1, sessions: [group(5, 3)] },
  });
  assert.equal(view.searchController, null);
  assert.equal(view.goButton.textContent, "Search");
  assert.match(view.statusBox.textContent, /17 matches in 3 sessions · searched 2 backends/);
  assert.match(view.statusBox.textContent, /1 backend skipped \(offline\)/);
  const hits = view.resultsBox.querySelectorAll(".search-hit");
  assert.equal(hits.length, 3);
  const [big, small, peer] = hits;
  assert.equal(rows(big).length, SEARCH_PER_SESSION);
  assert.equal(showAll(big).textContent, "Show all 12 matches");
  assert.equal(showAll(small), undefined, "a session shown whole has no button");
  assert.equal(showAll(peer), undefined);
  assert.equal(big.querySelector(".sh-count").textContent, "12 matches");
  rows(big)[2].click();
  assert.deepEqual(opened.pop(), { bid: 0, sid: 1, seq: 3 }, "a row opens its own message");

  /* The regression: the press asks the backend for that session's page
     with the search's own filters and a live signal, and throws nothing. */
  showAll(big).click();
  assert.deepEqual(toasts, [], "the press raises no notice of its own");
  assert.equal(showAll(big).disabled, true, "the press is loading, not failing");
  assert.equal(showAll(big).textContent, "Loading…");
  assert.equal(requests.length, 1, "the press makes exactly one request");
  let page = requests.pop();
  assert.equal(page.bid, 0);
  assert.equal(page.params.get("q"), "dashboard");
  assert.equal(page.params.get("sid"), "1");
  assert.equal(page.params.get("limit"), String(SEARCH_PAGE));
  assert.equal(page.params.get("offset"), "0");
  assert.equal(page.params.has("per"), false, "a page is not a fan-out query");
  assert.ok(page.options.signal instanceof AbortSignal && !page.options.signal.aborted);
  assert.equal(view.pageRequests.size, 1);
  page.resolve({ total: 12, matches: Array.from({ length: 12 }, (_, i) => match(i + 1)) });
  await settle();
  assert.equal(rows(big).length, 12, "the page replaces the preview");
  assert.equal(showAll(big), undefined, "everything shown removes the button");
  assert.equal(view.pageRequests.size, 0);
  assert.deepEqual(toasts, []);
  assert.equal(rows(big)[11].querySelector("mark").textContent, "12");

  /* A session with more than one page keeps loading from where it stopped. */
  await search(view, "cards", {
    0: { total: 120, session_total: 1, sessions: [group(3, 120)] },
    7: { total: 0, session_total: 0, sessions: [] },
  });
  const long = view.resultsBox.querySelector(".search-hit");
  showAll(long).click();
  page = requests.pop();
  assert.equal(page.params.get("offset"), "0");
  page.resolve({ total: 120, matches: Array.from({ length: SEARCH_PAGE }, (_, i) => match(i + 1)) });
  await settle();
  assert.equal(rows(long).length, SEARCH_PAGE);
  assert.equal(showAll(long).textContent, `Load more (${SEARCH_PAGE} of 120)`);
  assert.equal(showAll(long).disabled, false);
  showAll(long).click();
  page = requests.pop();
  assert.equal(page.params.get("offset"), String(SEARCH_PAGE), "the next page starts after the rows shown");
  assert.equal(page.params.get("sid"), "3");
  page.resolve({ total: 120, matches: Array.from({ length: SEARCH_PAGE }, (_, i) => match(SEARCH_PAGE + i + 1)) });
  await settle();
  assert.equal(rows(long).length, 2 * SEARCH_PAGE, "a later page appends");
  assert.equal(rows(long)[0].querySelector("mark").textContent, "1");
  assert.equal(showAll(long).textContent, `Load more (${2 * SEARCH_PAGE} of 120)`);

  /* A failed page is one notice in the console's grammar, and the button
     comes back with the label it had - a continuation still says so. */
  showAll(long).click();
  requests.pop().reject(new Error("network error"));
  await settle();
  assert.deepEqual(toasts.splice(0), [{ text: "Studio: Could not load matches · network error", tone: "bad" }]);
  assert.equal(showAll(long).disabled, false);
  assert.equal(showAll(long).textContent, `Load more (${2 * SEARCH_PAGE} of 120)`);
  assert.equal(rows(long).length, 2 * SEARCH_PAGE, "nothing shown is lost");
  assert.equal(view.pageRequests.size, 0);
  showAll(long).click();
  page = requests.pop();
  assert.equal(page.params.get("offset"), String(2 * SEARCH_PAGE), "the retry continues from the same place");
  page.resolve({ total: 120, matches: [match(101)] });
  await settle();
  assert.equal(rows(long).length, 2 * SEARCH_PAGE + 1);

  /* A new search replaces the results, so a page still on its way is
     cancelled without a notice; its button is handed back so a list that
     Back brings out of history's memory can be pressed again. */
  showAll(long).click();
  page = requests.pop();
  assert.equal(view.pageRequests.size, 1);
  const kept = [...view.resultsBox.children];
  const pending = view.runSearch();
  await settle();
  assert.equal(page.options.signal.aborted, true, "replacing the results aborts their pages");
  assert.equal(view.pageRequests.size, 0);
  assert.equal(long.isConnected, false);
  assert.equal(showAll(long).disabled, false);
  assert.equal(showAll(long).textContent, `Load more (${2 * SEARCH_PAGE + 1} of 120)`);
  assert.deepEqual(toasts, [], "a cancelled page is not a failure");
  for (const request of requests.splice(0))
    request.resolve({ total: 6, session_total: 1, sessions: [group(9, 6)] });
  await pending;
  assert.equal(view.resultsBox.querySelectorAll(".search-hit").length, 2);

  /* Back swaps the results for remembered ones without a new search: a
     page requested before the swap is not the swapped-in list's, but it
     is its own list's, and lands there for Forward to show. */
  const fresh = view.resultsBox.querySelector(".search-hit");
  showAll(fresh).click();
  page = requests.pop();
  view.resultsBox.replaceChildren(...kept);   // what navigationApply does for Back
  assert.equal(fresh.isConnected, false);
  assert.equal(page.options.signal.aborted, false, "history's swap cancels nothing");
  page.resolve({ total: 6, matches: Array.from({ length: 6 }, (_, i) => match(i + 1)) });
  await settle();
  assert.equal(rows(fresh).length, 6, "the page lands in the list it was pressed in");
  assert.equal(showAll(fresh), undefined);
  assert.equal(rows(kept[0]).length, 2 * SEARCH_PAGE + 1, "the list on show is untouched");
  assert.deepEqual(toasts, []);

  /* Closing the tab ends a page in flight the same way. */
  showAll(kept[0]).click();
  page = requests.pop();
  view.destroy();
  await settle();
  assert.equal(page.options.signal.aborted, true, "a closed tab ends its page requests");
  assert.equal(view.pageRequests.size, 0);
  assert.equal(showAll(kept[0]).disabled, false);
  assert.deepEqual(toasts, []);
  assert.equal(view.root.isConnected, false);

  /* ---- live filters ---- */
  const live = new SearchView({ id: "search", type: "search" });
  document.body.appendChild(live.root);
  const kindChip = label => [...live.kindsBox.children].find(chip => chip.textContent === label);
  const nodeChip = name => [...live.nodesBox.children].find(chip =>
    chip.querySelector(".search-chip-label").textContent === name);
  const seg = (box, label) => [...box.children].find(btn => btn.textContent === label);
  const empty = { total: 0, session_total: 0, sessions: [] };
  const answer = (reply = { total: 1, session_total: 1, sessions: [group(1, 1)] }) => {
    for (const request of requests.splice(0)) request.resolve(request.bid ? empty : reply);
    return settle();
  };
  const kindsExcept = key => SEARCH_KIND_CHIPS.map(chip => chip.key).filter(k => k !== key).join(",");

  /* Before the first search a filter is a setting for it: nothing is asked
     and no destination is recorded. */
  let marker = stops;
  kindChip("Tools").click();
  seg(live.timeBox, "7 days").click();
  seg(live.orderBox, "Newest").click();
  nodeChip("Peer").click();
  await settle();
  assert.equal(requests.length, 0, "a filter before the first search asks nothing");
  assert.equal(stops, marker);
  assert.equal(JSON.stringify([live.kinds.has("tool"), live.timeKey, live.order, [...live.excludedNodes]]),
    JSON.stringify([false, "week", "recent", [7]]), "the settings are still taken");
  kindChip("Tools").click();
  seg(live.timeBox, "Any time").click();
  seg(live.orderBox, "Relevance").click();
  nodeChip("Peer").click();
  await settle();
  assert.equal(requests.length, 0);

  /* Results up, and a draft in the box nobody submitted: a kind pressed
     off searches again for the query the results answer, with the kinds
     that remain, and the draft stays where it was. */
  await search(live, "dashboard", {
    0: { total: 14, session_total: 2, sessions: [group(1, 12), group(2, 2)] },
    7: { total: 3, session_total: 1, sessions: [group(5, 3)] },
  });
  assert.equal(live.resultsBox.querySelectorAll(".search-hit").length, 3);
  live.input.value = "cards draft";
  live.input.dispatchEvent(new FakeEvent("input", { bubbles: true }));
  live.input.dispatchEvent(new FakeEvent("keydown", { key: "s", bubbles: true }));
  await settle();
  assert.equal(requests.length, 0, "typing alone asks nothing");
  marker = stops;
  kindChip("Tools").click();
  await settle();
  assert.equal(requests.length, 2, "one request per online backend, without a press on Search");
  for (const request of requests) {
    assert.equal(request.params.get("q"), "dashboard", "the results' own query, not the draft");
    assert.equal(request.params.get("kinds"), kindsExcept("tool"));
    assert.equal(request.params.get("per"), String(SEARCH_PER_SESSION), "a fan-out query, like Search");
  }
  assert.equal(live.input.value, "cards draft", "the draft is not submitted");
  assert.equal(live.goButton.textContent, "Cancel");
  assert.equal(stops, marker + 1, "a filter search is a destination of its own");
  assert.equal(JSON.stringify(live.navigationQuery), JSON.stringify({ query: "dashboard",
    kinds: kindsExcept("tool").split(","), time: "any", order: "relevance", excluded: [] }));
  await answer();
  assert.equal(live.resultsBox.querySelectorAll(".search-hit").length, 1, "the results are replaced");
  assert.equal(live.lastCore.kinds, kindsExcept("tool"), "pages take the new filters");
  assert.equal(live.goButton.textContent, "Search");

  /* The last kind cannot go, so nothing is asked. */
  live.kinds = new Set(["info"]);
  live.renderKindChips();
  kindChip("System").click();
  await settle();
  assert.equal(requests.length, 0, "a kind that must stay asks nothing");
  assert.equal(JSON.stringify([...live.kinds]), '["info"]');
  live.kinds = new Set(kindsExcept("tool").split(","));
  live.renderKindChips();

  /* A search in flight is replaced, not joined: the time window asks
     again, the sort cancels that and asks once more, and the choice
     already made is not a change. */
  seg(live.timeBox, "7 days").click();
  await settle();
  const windowed = requests.splice(0);
  assert.equal(windowed.length, 2);
  assert.ok(windowed.every(request => Number(request.params.get("after")) > 0), "the window rides the query");
  seg(live.orderBox, "Newest").click();
  await settle();
  assert.ok(windowed.every(request => request.options.signal.aborted), "the next change cancels the last's requests");
  const sorted = requests.splice(0);
  assert.equal(sorted.length, 2);
  assert.ok(sorted.every(request => request.params.get("order") === "recent" &&
    Number(request.params.get("after")) > 0));
  marker = stops;
  seg(live.orderBox, "Newest").click();
  seg(live.timeBox, "7 days").click();
  await settle();
  assert.equal(requests.length, 0, "the choice already made asks nothing");
  assert.equal(stops, marker);
  assert.ok(sorted.every(request => !request.options.signal.aborted), "and cancels nothing");
  assert.deepEqual(toasts, [], "a replaced search is not a failure");
  for (const request of sorted) request.resolve({ total: 0, session_total: 0, sessions: [] });
  await settle();
  assert.match(live.resultsBox.textContent, /No matches for "dashboard"/);

  /* Nothing found is still an answer: widening the window asks again. */
  seg(live.timeBox, "Any time").click();
  await settle();
  assert.equal(requests.length, 2);
  assert.ok(requests.every(request => !request.params.has("after")));
  await answer();
  assert.equal(live.resultsBox.querySelectorAll(".search-hit").length, 1);

  /* Backends: leaving the peer out asks this console alone; a repaint for
     reachability asks nothing; nothing selected shows nothing, because
     the earlier results do not stand for backends no longer searched. */
  nodeChip("Peer").click();
  await settle();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].bid, 0);
  await answer({ total: 2, session_total: 1, sessions: [group(1, 2)] });
  assert.equal(rows(live.resultsBox.querySelector(".search-hit")).length, 2);
  state.remoteOk[8] = true;
  live.syncRemoteState();
  assert.equal(nodeChip("Asleep").disabled, false, "the repaint took the change");
  state.remoteOk[8] = false;
  live.syncRemoteState();
  assert.equal(nodeChip("Asleep").disabled, true);
  await settle();
  assert.equal(requests.length, 0, "a reachability repaint asks nothing");
  const shown = live.resultsBox.querySelector(".search-hit");
  nodeChip("Studio").click();
  await settle();
  assert.equal(requests.length, 0, "nothing selected asks nothing");
  assert.match(live.statusBox.textContent, /No online backends are selected to search/);
  assert.equal(live.resultsBox.children.length, 0, "and shows nothing");
  assert.equal(shown.isConnected, false);
  assert.equal(live.goButton.textContent, "Search");
  nodeChip("Studio").click();
  await settle();
  assert.equal(requests.length, 1, "selecting one again asks it");
  assert.equal(requests[0].params.get("q"), "dashboard");
  await answer();

  /* A cancelled search keeps its filters live: leaving out the backend
     that was slow is the very thing to do, and it runs again without it. */
  nodeChip("Peer").click();
  await settle();
  assert.equal(requests.length, 2);
  live.cancelSearch();
  assert.ok(requests.every(request => request.options.signal.aborted));
  requests.splice(0);
  assert.match(live.statusBox.textContent, /Search cancelled/);
  nodeChip("Peer").click();
  await settle();
  assert.equal(requests.length, 1, "a filter after a cancel searches again");
  assert.equal(requests[0].bid, 0);
  await answer();
  assert.equal(live.resultsBox.querySelectorAll(".search-hit").length, 1);

  /* Back to the tab before its first search: the filters are settings
     again until Enter or Search runs one. */
  live.cancelSearch(); live.lastCore = null;   // what navigationApply does for that entry
  live.resultsBox.replaceChildren(); live.statusBox.replaceChildren();
  marker = stops;
  kindChip("Tools").click();
  seg(live.orderBox, "Relevance").click();
  await settle();
  assert.equal(requests.length, 0, "no search to run again");
  assert.equal(stops, marker);
  live.input.value = "dashboard";
  live.input.dispatchEvent(new FakeEvent("keydown", { key: "Enter", bubbles: true }));
  await settle();
  assert.equal(requests.length, 1, "Enter runs the box");
  assert.equal(requests[0].params.get("q"), "dashboard");
  assert.equal(requests[0].params.has("kinds"), false, "every kind again");
  await answer();
  live.destroy();
  assert.deepEqual(toasts, []);

  console.log("PASS: search results, the Show all button, its pages and continuations, a failed page's notice and label, pages cancelled by a new search or a closed tab but not by Back, and the filters that search again once results are up - never the draft in the box");
}

main().catch(error => { console.error(error); process.exit(1); });
