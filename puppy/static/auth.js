"use strict";

const field = id => document.getElementById(id);
let setup = false;
let ready = false;
let submitting = false;

function showError(error) {
  field("auth-err").textContent = error.message;
  field("auth-err").classList.remove("hidden");
}

async function request(action, body) {
  const response = await fetch("/api/auth/" + action, {
    method: body ? "POST" : "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : {},
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  let data;
  try { data = await response.json(); }
  catch (error) { throw new Error("Unable to connect. Please try again"); }
  if (!response.ok) throw new Error(data.error || "Unable to sign in");
  return data;
}

async function initialize() {
  const status = await request("status");
  if (status.authed) { location.reload(); return; }
  setup = status.setup_required;
  const codeRequired = setup && status.setup_code_required;
  document.title = setup ? "Create account" : "Sign in";
  field("auth-title").textContent = setup ? "Create admin account" : "Sign in";
  field("auth-sub").textContent = setup ? (codeRequired ?
    "Enter the bootstrap code from the startup log, then choose your credentials" :
    "First run - choose your administrator credentials") :
    "Enter your credentials to continue";
  field("auth-setup-code-wrap").classList.toggle("hidden", !codeRequired);
  field("auth-setup-code").required = !!codeRequired;
  field("auth-pass2-wrap").classList.toggle("hidden", !setup);
  field("auth-pass2").required = !!setup;
  field("auth-pass").autocomplete = setup ? "new-password" : "current-password";
  field("auth-submit").textContent = setup ? "Create & enter" : "Sign in";
  ready = true;
}

field("auth-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (submitting) return;
  submitting = true;
  const button = field("auth-submit");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  field("auth-err").classList.add("hidden");
  try {
    // A failed initial status read can be retried without submitting credentials.
    if (!ready) { await initialize(); return; }
    const body = {
      username: field("auth-user").value.trim(),
      password: field("auth-pass").value,
    };
    if (setup) {
      if (body.password !== field("auth-pass2").value)
        throw new Error("passwords do not match");
      body.setup_code = field("auth-setup-code").value;
    }
    await request(setup ? "setup" : "login", body);
    field("auth-pass").value = "";
    field("auth-pass2").value = "";
    field("auth-setup-code").value = "";
    location.reload();
  } catch (error) { showError(error); }
  finally {
    submitting = false;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
});

initialize().catch(showError).finally(() => { field("auth-submit").disabled = false; });
