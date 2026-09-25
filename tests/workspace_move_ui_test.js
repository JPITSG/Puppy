/* Shared move dialog: capability gates, backend routing, busy and inline errors,
   and the project variant - the sessions that follow a folder, the path it
   starts from, its compare field and the notice it ends with. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { FakeDocument } = require('./fake_dom.js');
const source = fs.readFileSync(require('node:path').join(__dirname, '../puppy/static/app.js'), 'utf8');
const document = new FakeDocument();
let dialog, resolveRequest, reply = null, calls = [], notices = [], views = {};
const sessions = {
  2: [
    {id: 4, name: 'Harbor', cwd: '/projects/harbor', workspace_kind: 'directory'},
    {id: 5, name: 'Harbor styles', cwd: '/projects/harbor/src', workspace_kind: 'directory'},
    {id: 6, name: '', cwd: '/projects/harbor/docs', workspace_kind: 'directory', archived: true},
    {id: 7, name: 'Harbor notes', cwd: '/projects/harbor-notes', workspace_kind: 'directory'},
    {id: 8, name: 'Everything', cwd: '/projects', workspace_kind: 'directory'},
    {id: 9, name: 'A task', cwd: '/data/workspaces/session-x', workspace_kind: 'temporary',
     task: {parent: 4}},
    {id: 10, name: 'Linked', cwd: '/projects/harbor/mirror', workspace_kind: 'directory',
     workspace: {root: '/srv/remote', node: 'Atlas'}},
  ],
};
const context = vm.createContext({
  state: {backends: [{id: 2, capabilities: ['workspace-move', 'project-move']},
                     {id: 3, capabilities: ['workspace-move']}]},
  isScratchWorkspace: s => !!s && s.workspace_kind === 'temporary',
  sessionWorkspace: s => s && s.workspace && s.workspace.root ? s.workspace : null,
  sessionsFor: bid => sessions[bid] || [],
  backendHasCapability: (b, c) => !!b && b.capabilities.includes(c),
  backendName: () => 'Studio', esc: s => s,
  modalSubjectHtml: subject => `<p class="modal-copy modal-subject">${subject}</p>`,
  TOAST_LONG: 7000,
  modal: html => {
    const m = document.createElement('div'); m.innerHTML = html; document.body.appendChild(m);
    dialog = m; return {m, close: () => m.remove()};
  },
  wireDirectoryPicker: (input, box, bid) => {
    assert.equal(bid(), 2);
    /* the picker lists what is typed: wiring it after the prefill keeps
       the dialog from opening on the project's own folders */
    input.wiredWith = input.value;
  },
  api: async (bid, route, options) => {
    calls.push({bid, route, options});
    await new Promise(resolve => {resolveRequest = resolve;});
    if (reply instanceof Error) throw reply;
    return reply;
  },
  sessionViewFor: (bid, sid) => views[sid] || null, refreshGroup: bid => assert.equal(bid, 2),
  toast: (message, tone, ms) => notices.push({message, tone, ms}),
});
context.document = document;
vm.runInContext(source.slice(source.indexOf('function canMoveScratch('),
  source.indexOf('/* Independent workspace roles:', source.indexOf('function canMoveScratch('))), context);

const submit = async (value) => {
  const form = dialog.querySelector('form');
  if (value !== undefined) dialog.querySelector('#move-cwd').value = value;
  const pending = form.onsubmit({preventDefault() {}});
  if (resolveRequest) { const resolve = resolveRequest; resolveRequest = null; resolve(); }
  await pending;
};

(async () => {
  /* ---- scratch promotion keeps its own dialog ---- */
  const scratch = {id: 4, workspace_kind: 'temporary'};
  assert.equal(context.canMoveScratch(2, scratch), true);
  assert.equal(context.canMoveScratch(3, {id: 4, workspace_kind: 'temporary'}), true);
  assert.equal(context.canMoveScratch(0, {...scratch, task: {}}), false);
  assert.equal(context.canMoveScratch(0, {...scratch, workspace_missing: true}), false);
  assert.equal(context.canMoveScratch(0, {...scratch, workspace_kind: 'directory'}), false);
  context.modalMoveWorkspace(2, scratch);
  assert.equal(dialog.querySelector('h2').textContent, 'Move to directory');
  assert.equal(dialog.querySelector('.modal-subject'), null);
  assert.equal(dialog.querySelector('.move-note'), null);
  const form = dialog.querySelector('form'), input = dialog.querySelector('#move-cwd');
  assert.equal(input.value, '');
  input.value = '';
  await form.onsubmit({preventDefault() {}});
  assert.equal(calls.length, 0);
  assert.equal(dialog.querySelector('.form-error').textContent, 'Choose a destination directory');
  input.value = '/projects/harbor';
  reply = new Error('Destination already exists');
  let pending = form.onsubmit({preventDefault() {}});
  assert.equal(dialog.querySelector('#move-go').disabled, true);
  await form.onsubmit({preventDefault() {}});
  assert.equal(calls.length, 1);
  resolveRequest(); resolveRequest = null; await pending;
  assert.equal(dialog.querySelector('.form-error').textContent, 'Destination already exists');
  assert.equal(input.value, '/projects/harbor');
  assert.equal(notices.length, 0);
  reply = {session: {id: 4, cwd: '/projects/harbor', workspace_kind: 'directory'}};
  await submit();
  assert.equal(calls[1].bid, 2);
  assert.equal(calls[1].route, 'sessions/4/workspace/move');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1].options.body)), {destination: '/projects/harbor'});
  assert.equal(calls[1].options.operation, 'Moving scratch workspace');
  assert.equal(dialog.isConnected, false);
  assert.deepEqual(notices[0], {message: 'Studio: Project moved to /projects/harbor', tone: 'ok', ms: undefined});
  console.log('PASS: scratch move capability gates, dialog, retry, busy state and backend routing');

  /* ---- an ordinary project folder ---- */
  const harbor = sessions[2][0];
  assert.equal(context.canMoveProject(2, harbor), true);
  assert.equal(context.canMoveProject(3, harbor), false);            // node without project-move
  assert.equal(context.canMoveProject(0, harbor), true);             // this instance
  assert.equal(context.canMoveProject(2, sessions[2][5]), false);    // a task copy
  assert.equal(context.canMoveProject(2, sessions[2][6]), false);    // a linked session's mirror
  assert.equal(context.canMoveProject(2, scratch), false);           // scratch has its own row
  assert.equal(context.canMoveProject(2, null), false);
  // Inside the folder, archived too; never a sibling sharing its prefix, a
  // parent, a task copy or a linked mirror.
  assert.deepEqual(context.projectMoveFollowers(2, harbor).map(s => s.id), [5, 6]);
  assert.deepEqual(context.projectMoveFollowers(2, {...harbor, cwd: '/projects/harbor/'}).map(s => s.id), [5, 6]);
  assert.deepEqual(context.projectMoveFollowers(2, sessions[2][1]).map(s => s.id), []);
  const named = n => Array.from({length: n}, (_, i) => ({id: 20 + i, name: `S${i + 1}`}));
  const tail = 'starts fresh engine context with a transcript handoff on its next turn. ' +
    'Reopen terminals after moving.';
  assert.equal(context.projectMoveNote([]), 'The conversation stays and ' + tail);
  assert.equal(context.projectMoveNote(named(1)),
    'S1 works in this folder too and moves with it. Every conversation stays and ' + tail);
  assert.equal(context.projectMoveNote(named(2)),
    'S1 and S2 work in this folder too and move with it. Every conversation stays and ' + tail);
  assert.equal(context.projectMoveNote(named(3)).split('.')[0], 'S1, S2 and S3 work in this folder too and move with it');
  assert.equal(context.projectMoveNote(named(5)).split('.')[0], 'S1, S2 and 3 more work in this folder too and move with it');
  assert.equal(context.projectMoveNote([{id: 6, name: ''}]).split(' works')[0], 'Session 6');

  calls = []; notices = [];
  views[4] = {session: harbor, updateHead() { this.updated = true; }};
  context.modalMoveWorkspace(2, harbor);
  const box = dialog.querySelector('#move-cwd');
  assert.equal(dialog.querySelector('h2').textContent, 'Move project');
  assert.equal(dialog.querySelector('.modal-subject').textContent, '/projects/harbor');
  const copies = dialog.querySelectorAll('.modal-copy');
  assert.equal(copies.length, 2);
  assert.equal(copies[1].textContent, "Move this project's folder to a new directory on Studio.");
  assert.match(dialog.querySelector('.move-note').textContent, /^Harbor styles and Session 6 work in this folder too/);
  // The path to edit, whole, selected and focused before the picker is wired.
  assert.equal(box.value, '/projects/harbor');
  assert.equal(box.wiredWith, '/projects/harbor');
  assert.equal(document.activeElement, box);
  assert.deepEqual([box.selectionStart, box.selectionEnd], [0, '/projects/harbor'.length]);
  // An unchanged path asks nothing, with or without a trailing slash.
  for (const same of ['/projects/harbor', ' /projects/harbor/ ']) {
    await submit(same);
    assert.equal(calls.length, 0);
    assert.equal(dialog.querySelector('.form-error').textContent, 'Choose a different directory');
  }
  await submit('');
  assert.equal(dialog.querySelector('.form-error').textContent, 'Choose a destination directory');
  // A refusal stays inline with the path kept.
  reply = new Error('Harbor styles is working in this folder; wait for it to finish or stop it, and clear its queue');
  await submit(' /archive/harbor ');
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0].options.body)),
    {destination: '/archive/harbor', expected_cwd: '/projects/harbor'});
  assert.equal(calls[0].options.operation, 'Moving project');
  assert.equal(calls[0].options.timeoutMs, 1800000);
  assert.equal(dialog.querySelector('.form-error').textContent,
    'Harbor styles is working in this folder; wait for it to finish or stop it, and clear its queue');
  assert.equal(dialog.querySelector('.form-error').classList.contains('hidden'), false);
  assert.equal(dialog.querySelector('#move-cwd').disabled, false);
  assert.equal(notices.length, 0);
  // Cancel closes through the operation's own dialog: nothing more to say.
  reply = Object.assign(new Error('Operation cancelled'), {cancelled: true});
  await submit();
  assert.equal(dialog.querySelector('.form-error').textContent,
    'Harbor styles is working in this folder; wait for it to finish or stop it, and clear its queue');
  assert.equal(notices.length, 0);
  // Moved with its followers: the view follows at once, the notice counts them.
  reply = {session: {...harbor, cwd: '/archive/harbor'}, moved: [4, 5, 6], retained: ''};
  await submit();
  assert.equal(dialog.isConnected, false);
  assert.equal(views[4].session.cwd, '/archive/harbor');
  assert.equal(views[4].updated, true);
  assert.deepEqual(notices.pop(), {message: 'Studio: Project moved to /archive/harbor · 2 other sessions moved with it',
    tone: 'ok', ms: undefined});
  // Alone, and with an original that could not be removed completely.
  context.modalMoveWorkspace(2, {...harbor, cwd: '/archive/harbor'});
  reply = {session: {...harbor, cwd: '/mnt/data/harbor'}, moved: [4], retained: '/archive/harbor'};
  await submit('/mnt/data/harbor');
  assert.deepEqual(notices.pop(), {message: 'Studio: Project moved to /mnt/data/harbor · the original folder ' +
    'could not be removed completely · remove /archive/harbor by hand', tone: 'warn', ms: 7000});
  context.modalMoveWorkspace(2, {...harbor, cwd: '/mnt/data/harbor'});
  reply = {session: {...harbor, cwd: '/srv/harbor'}, moved: [4], retained: ''};
  await submit('/srv/harbor');
  assert.deepEqual(notices.pop(), {message: 'Studio: Project moved to /srv/harbor', tone: 'ok', ms: undefined});
  console.log('PASS: project move gates, followers named beforehand, the path to edit, compare field, refusals and notices');
})().catch(error => {console.error(error); process.exitCode = 1;});
