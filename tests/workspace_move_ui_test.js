/* Shared move dialog: capability gates, backend routing, busy and inline errors. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { FakeDocument } = require('./fake_dom.js');
const source = fs.readFileSync(require('node:path').join(__dirname, '../puppy/static/app.js'), 'utf8');
const document = new FakeDocument();
let dialog, resolveRequest, fail = true, calls = [], notices = [];
const context = vm.createContext({
  state: {backends: [{id: 2, capabilities: ['workspace-move']}, {id: 3, capabilities: []}]},
  isScratchWorkspace: s => !!s && s.workspace_kind === 'temporary',
  backendHasCapability: (b, c) => !!b && b.capabilities.includes(c),
  backendName: () => 'Studio', esc: s => s,
  modal: html => {
    const m = document.createElement('div'); m.innerHTML = html; document.body.appendChild(m);
    dialog = m; return {m, close: () => m.remove()};
  },
  wireDirectoryPicker: (input, box, bid) => assert.equal(bid(), 2),
  api: async (bid, route, options) => {
    calls.push({bid, route, options});
    await new Promise(resolve => {resolveRequest = resolve;});
    if (fail) throw new Error('Destination already exists');
    return {session: {id: 4, cwd: '/projects/harbor', workspace_kind: 'directory'}};
  },
  sessionViewFor: () => null, refreshGroup: bid => assert.equal(bid, 2),
  toast: message => notices.push(message),
});
vm.runInContext(source.slice(source.indexOf('function canMoveScratch('),
  source.indexOf('/* Independent workspace roles:', source.indexOf('function canMoveScratch('))), context);
(async () => {
  const session = {id: 4, workspace_kind: 'temporary'};
  assert.equal(context.canMoveScratch(2, session), true);
  assert.equal(context.canMoveScratch(3, session), false);
  assert.equal(context.canMoveScratch(0, {...session, task: {}}), false);
  assert.equal(context.canMoveScratch(0, {...session, workspace_missing: true}), false);
  assert.equal(context.canMoveScratch(0, {...session, workspace_kind: 'directory'}), false);
  context.modalMoveWorkspace(2, session);
  const form = dialog.querySelector('form'), input = dialog.querySelector('#move-cwd');
  input.value = '';
  await form.onsubmit({preventDefault() {}});
  assert.equal(calls.length, 0);
  assert.equal(dialog.querySelector('.form-error').textContent, 'Choose a destination directory');
  input.value = '/projects/harbor';
  let submit = form.onsubmit({preventDefault() {}});
  assert.equal(dialog.querySelector('#move-go').disabled, true);
  await form.onsubmit({preventDefault() {}});
  assert.equal(calls.length, 1);
  resolveRequest(); await submit;
  assert.equal(dialog.querySelector('.form-error').textContent, 'Destination already exists');
  assert.equal(input.value, '/projects/harbor');
  assert.equal(notices.length, 0);
  fail = false;
  submit = form.onsubmit({preventDefault() {}}); resolveRequest(); await submit;
  assert.equal(calls[1].bid, 2);
  assert.equal(calls[1].route, 'sessions/4/workspace/move');
  assert.equal(calls[1].options.body.destination, input.value);
  assert.equal(dialog.isConnected, false);
  assert.equal(notices[0], 'Studio: Project moved to /projects/harbor');
  console.log('PASS: scratch move capability gates, dialog, retry, busy state and backend routing');
})().catch(error => {console.error(error); process.exitCode = 1;});
