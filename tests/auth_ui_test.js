/* Sign-in and setup flows against the actual public script, without an engine. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { FakeDocument, fire } = require("./fake_dom.js");
const html = fs.readFileSync(path.join(__dirname, "../puppy/static/auth.html"), "utf8");
const source = fs.readFileSync(path.join(__dirname, "../puppy/static/auth.js"), "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
const reply = (data, ok = true) => ({ ok, json: async () => data });

async function start(status) {
  const document = new FakeDocument();
  document.body.innerHTML = html.slice(html.indexOf("<body>") + 6, html.indexOf("</body>"));
  const calls = [];
  let reloads = 0;
  let respond = async () => reply(status);
  vm.runInNewContext(source, {
    document, AbortController, setTimeout, clearTimeout, location: { reload: () => { reloads++; } },
    fetch: async (url, options) => { calls.push({ url, options }); return respond(); },
  });
  await tick();
  const field = id => document.getElementById("auth-" + id);
  return { document, calls, field, reloads: () => reloads,
    respond: fn => { respond = fn; },
    submit: () => fire(field("form"), "submit"),
  };
}

(async () => {
  const login = await start({ authed: false, setup_required: false });
  assert.equal(login.document.title, "Sign in");
  assert(login.field("pass2-wrap").classList.contains("hidden"));
  assert.equal(login.field("pass").autocomplete, "current-password");
  login.field("user").value = " demo ";
  login.field("pass").value = "secret123";
  let finish;
  login.respond(() => new Promise(resolve => { finish = resolve; }));
  login.submit();
  login.submit();
  assert.equal(login.calls.length, 2, "only one in-flight credential submission");
  assert.equal(login.field("submit").disabled, true);
  assert.equal(login.field("submit").getAttribute("aria-busy"), "true");
  assert.deepEqual(JSON.parse(login.calls[1].options.body), { username: "demo", password: "secret123" });
  finish(reply({ error: "invalid credentials" }, false));
  await tick();
  assert.equal(login.field("err").textContent, "invalid credentials");
  assert.equal(login.field("submit").disabled, false);
  assert.equal(login.reloads(), 0);
  login.respond(async () => reply({ ok: true }));
  login.submit();
  await tick();
  assert.equal(login.reloads(), 1);
  assert.equal(login.field("pass").value, "");

  for (const setup_code_required of [true, false]) {
    const setup = await start({ authed: false, setup_required: true, setup_code_required });
    assert.equal(setup.field("setup-code").required, setup_code_required);
    assert.equal(setup.field("pass2").required, true);
    assert.equal(setup.field("pass").autocomplete, "new-password");
    setup.field("user").value = "demo";
    setup.field("pass").value = "secret123";
    setup.field("pass2").value = "mismatch";
    setup.submit();
    await tick();
    assert.equal(setup.calls.length, 1, "mismatched passwords never sent");
    assert.equal(setup.field("err").textContent, "passwords do not match");
    setup.field("pass2").value = "secret123";
    setup.field("setup-code").value = "bootstrap-code";
    setup.respond(async () => reply({ ok: true }));
    setup.submit();
    await tick();
    assert.equal(setup.calls[1].url, "/api/auth/setup");
    assert.equal(JSON.parse(setup.calls[1].options.body).setup_code, "bootstrap-code");
    assert.equal(setup.reloads(), 1);
    assert.equal(setup.field("setup-code").value, "");
  }
  const cancelled = await start({ authed: false, setup_required: false });
  cancelled.field("user").value = "demo";
  cancelled.field("pass").value = "keep-this-draft";
  let lateReply;
  cancelled.respond(() => new Promise(resolve => { lateReply = resolve; }));
  cancelled.submit();
  assert.equal(cancelled.field("cancel").classList.contains("hidden"), false);
  cancelled.field("cancel").onclick();
  lateReply(reply({ ok: true }));
  await tick();
  assert.equal(cancelled.reloads(), 0, "a late sign-in result cannot override Stop waiting");
  assert.equal(cancelled.field("pass").value, "keep-this-draft");
  assert.equal(cancelled.field("submit").disabled, false);
  assert.equal(cancelled.field("err").classList.contains("hidden"), true);

  const already = await start({ authed: true });
  assert.equal(already.reloads(), 1);

  // An unavailable status endpoint must not silently guess login vs setup.
  const failed = await start(null);
  assert.equal(failed.reloads(), 0);
  failed.respond(async () => reply({ authed: false, setup_required: true }));
  failed.submit();
  await tick();
  assert.equal(failed.calls[1].url, "/api/auth/status");
  assert.equal(failed.document.title, "Create account");
  assert.equal(failed.field("submit").disabled, false);
  console.log("Sign-in/setup, errors, submission guard, reload and status recovery passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
