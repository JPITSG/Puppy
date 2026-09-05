#!/usr/bin/env python3
"""Task isolation, queue ownership, review/apply and API contracts without quota."""
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sys
import threading
from unittest.mock import AsyncMock, patch
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root('session-tasks-')
os.environ['PUPPY_DATA'] = str(ROOT / 'data')
from puppy import config, db, runner, session_tasks as tasks, uploads, workspaces


def start(hub, prompt):
    hub.status = 'running'
    hub.interrupted = False
    hub._active_prompt_text = prompt
    hub.test_seq = db.add_event(hub.id, 'user', {'text':prompt})['seq']


def finish(sid, text='Implemented and checked.', status='ok'):
    hub = runner.hub(sid)
    db.add_event(sid, 'assistant', {'text':text})
    db.add_event(sid, 'result', {'ok':status == 'ok'})
    tasks.finished(sid, status, hub.test_seq)
    hub.status = 'idle'


async def rejected(call, contains):
    try:
        await call
    except tasks.TaskError as error:
        assert contains in str(error), str(error)
    else:
        raise AssertionError('expected rejection: ' + contains)


def assert_main_inspection(text, path):
    assert json.dumps(str(path), ensure_ascii=False) in text
    assert 'read-only' in text and 'separate user confirmation' in text
    assert 'engine\'s permissions in force' in text
    assert 'Keep all edits, generated files, Git writes and test runs in' in text
    assert "Never modify Main's files, index or refs" in text
    assert 'git --no-optional-locks' in text
    assert 'Do not access or edit' not in text


async def resolution_api(client, parent):
    prefix = '/api/sessions/{}/tasks'.format(parent)
    project = Path(db.get_session(parent)['cwd'])
    sid = None
    try:
        with patch.object(runner.SessionHub, '_start_turn', start):
            response = await client.post(prefix, json={'prompt': 'HTTP conflicts', 'request_id': 'http-conflicts'})
            assert response.status == 200, await response.text()
            row = (await response.json())['session']
            sid = row['id']
            (Path(row['cwd']) / 'http-conflict').write_text('task addition\n')
            (project / 'http-conflict').write_text('Main addition\n')
            finish(sid)
            route = prefix + '/' + str(sid)
            data = await (await client.post(route + '/review', json={})).json()
            token = data['token']
            for value in ('true', 1, None, [], {}):
                response = await client.post(route + '/apply', json={'token': token, 'resolve_conflicts': value})
                assert response.status == 409 and 'true or false' in (await response.json())['error']
            response = await client.post(route + '/apply', json={'token': token})
            assert response.status == 409 and 'already exists' in (await response.json())['error']
            assert len([event for event in db.get_events(sid) if event['kind'] == 'user']) == 1
            response = await client.post(route + '/apply', json={'token': token, 'resolve_conflicts': True})
            data = await response.json()
            assert response.status == 200 and data['resolving'] and not data['applied'], data
            assert data['task']['id'] == sid and data['task']['task']['state'] == 'running'
            response = await client.post(route + '/apply', json={'token': token, 'resolve_conflicts': True})
            assert response.status == 409
            assert len([event for event in db.get_events(sid) if event['kind'] == 'user']) == 2
            assert (project / 'http-conflict').read_text() == 'Main addition\n'
            finish(sid)
    finally:
        (project / 'http-conflict').unlink(missing_ok=True)
        if sid:
            runner.hub(sid).status = 'idle'
            workspaces.remove_temporary(db.get_session(sid))
            runner.drop_hub(sid)
            db.delete_session(sid)
    print('PASS: conflict-resolution HTTP opt-in, validation, follow-up delivery and duplicate rejection')


async def conflict_resolution():
    project = ROOT / 'resolution-project'
    project.mkdir()
    tasks._git(project, 'init', '--quiet')
    for name, text in {'a.txt': 'original\n', 'b.txt': 'original b\n', 'deleted.txt': 'delete me\n'}.items():
        (project / name).write_text(text)
    tasks._git(project, 'add', '-A')
    tasks._git(project, 'commit', '-qm', 'Initial')
    parent = db.create_session('Resolution', 'codex', str(project), '', '', '#e0784f', 'workspace-write')
    subdir = project / 'nested "Main"'
    subdir.mkdir()
    alias = db.create_session('Same project', 'codex', str(subdir), '', '', '#e0784f', 'workspace-write')
    with patch.object(runner.SessionHub, '_start_turn', start):
        rows = [await tasks.create(pid, {'prompt': 'Feature ' + name, 'request_id': name})
                for pid, name in ((parent, 'A'), (parent, 'B'), (alias, 'C'))]
        a, b, c = [row['id'] for row in rows]
        ap, bp, cp = [Path(row['cwd']) for row in rows]
        for sid, path, name in zip((a, b, c), (ap, bp, cp), ('A', 'B', 'C')):
            (path / 'a.txt').write_text(name + '\n')
            (path / ('new-' + name)).write_text(name + '\n')
            finish(sid)
        # Staged work must survive resolution preparation byte for byte.
        tasks._git(bp, 'add', '--', 'new-B')
        index = (bp / '.git' / 'index').read_bytes()
        main_index = (project / '.git' / 'index').read_bytes()
        reviews = [await tasks.review(pid, sid) for pid, sid in ((parent, a), (parent, b), (alias, c))]
        old_b = tasks.record(b)
        token = reviews[1]['token']
        users = lambda sid: len([row for row in db.get_events(sid) if row['kind'] == 'user'])
        for value in ('true', 1, None, [], {}):
            await rejected(tasks.review(parent, b, token, value), 'true or false')
        (project / 'a.txt').write_text('drift\n')
        await rejected(tasks.review(parent, b, token), 'patch')
        assert tasks.record(b) == old_b and users(b) == 1
        (bp / 'new-B').write_text('changed since review\n')
        await rejected(tasks.review(parent, b, token, True), 'review them again')
        (bp / 'new-B').write_text('B\n')
        runner.hub(parent).status = 'running'
        await rejected(tasks.review(parent, b, token, True), 'idle')
        runner.hub(parent).status = 'idle'
        # A refused engine dispatch rolls the baseline back. The same review
        # can retry even if Main has drifted again since its failed snapshot.
        with patch.object(runner.hub(b), 'send_message', return_value={'error': 'Engine unavailable'}):
            await rejected(tasks.review(parent, b, token, True), 'Engine unavailable')
        assert tasks.record(b) == old_b and users(b) == 1
        assert tasks._git(bp, 'rev-parse', 'refs/puppy/base').decode().strip() == old_b['base']
        (project / 'a.txt').write_text('different drift\n')
        with patch.object(runner.hub(b), 'send_message', return_value={'error': 'Engine unavailable'}):
            await rejected(tasks.review(parent, b, token, True), 'Engine unavailable')
        assert tasks.record(b) == old_b
        (project / 'a.txt').write_text('original\n')
        (project / 'b.txt').write_text('Main uncommitted change\n')
        (project / 'main-only').write_text('Main addition\n')
        (project / 'deleted.txt').unlink()
        original_git = tasks._git
        def fatal_git(cwd, *args, **kwargs):
            if args[:2] == ('apply', '--check'):
                raise tasks.GitError('Invalid patch', 128)
            return original_git(cwd, *args, **kwargs)
        with patch.object(tasks, '_git', fatal_git):
            await rejected(tasks.review(parent, b, token, True), 'Invalid patch')
        assert users(b) == 1
        # Hold A inside its project lock; both same-parent B and other-parent C
        # must wait, then capture A's applied result instead of racing it.
        loop = asyncio.get_running_loop()
        applying, release = asyncio.Event(), threading.Event()
        def slow_git(cwd, *args, **kwargs):
            if str(cwd) == str(project) and args[:2] == ('apply', '--binary'):
                loop.call_soon_threadsafe(applying.set)
                assert release.wait(10)
            return original_git(cwd, *args, **kwargs)
        with patch.object(tasks, '_git', slow_git):
            first = asyncio.create_task(tasks.review(parent, a, reviews[0]['token'], True))
            try:
                await asyncio.wait_for(applying.wait(), 5)
                second = asyncio.create_task(tasks.review(parent, b, token, True))
                third = asyncio.create_task(tasks.review(alias, c, reviews[2]['token'], True))
                await asyncio.sleep(.05)
                assert not second.done() and not third.done()
            finally:
                release.set()
            ar, br, cr = await asyncio.gather(first, second, third)
        assert ar['applied'] and not ar.get('resolving')
        assert br['resolving'] and cr['resolving'] and not br['applied'] and not cr['applied']
        assert users(a) == 1 and users(b) == users(c) == 2
        assert (project / 'a.txt').read_text() == 'A\n'
        assert not (project / 'new-B').exists() and not (project / 'new-C').exists()
        assert (bp / 'a.txt').read_text() == 'B\n' and (bp / '.git' / 'index').read_bytes() == index
        for sid, path, before in ((b, bp, reviews[1]), (c, cp, reviews[2])):
            ref = 'refs/puppy/resolve/' + before['token']
            assert tasks._git(path, 'show', ref + '/main:a.txt') == b'A\n'
            assert tasks._git(path, 'show', ref + '/main:b.txt') == b'Main uncommitted change\n'
            assert tasks._git(path, 'show', ref + '/task:a.txt') == (b'B\n' if sid == b else b'C\n')
            assert tasks._git(path, 'show', ref + '/base:a.txt') == b'original\n'
            assert tasks._git(path, 'remote').strip() == b''
            prompt = runner.hub(sid)._active_prompt_text
            assert ref in prompt and 'must review again' in prompt
            assert_main_inspection(prompt, project)
            assert 'rather than changing the pinned review baseline' in prompt
            assert_main_inspection(tasks.guidance(sid), project if sid == b else subdir)
        await rejected(tasks.review(parent, b, token, True), 'finish')
        # A failed/stopped resolution never applies or starts another prompt.
        finish(b, 'Needs more work', 'error')
        assert users(b) == 2 and (project / 'a.txt').read_text() == 'A\n'
        await rejected(tasks.review(parent, b, token, True), 'review them again')
        def reconcile(sid, path, merged, name):
            # Simulate the agent integrating the supplied Main snapshot and
            # reapplying its intent, including a staged file and Main deletes.
            tasks._git(path, 'restore', '--source', tasks.record(sid)['base'], '--worktree', '--', '.')
            (path / 'a.txt').write_text(merged + '\n')
            (path / ('new-' + name)).write_text(name + '\n')
            finish(sid, 'Resolved and checked')
        reconcile(b, bp, 'A+B', 'B')
        reconcile(c, cp, 'A+C', 'C')
        new_b, new_c = await tasks.review(parent, b), await tasks.review(alias, c)
        assert new_b['token'] != token and 'b.txt' not in new_b['files'] and 'deleted.txt' not in new_b['files']
        assert 'main-only' not in new_b['files'] and 'new-B' in new_b['files']
        await tasks.review(parent, b, new_b['token'])
        # C resolved against A, but B landed first. Its next explicit apply
        # takes a fresh snapshot and starts exactly one further resolution.
        cr = await tasks.review(alias, c, new_c['token'], True)
        assert cr['resolving'] and users(c) == 3
        assert tasks._git(cp, 'show', tasks.record(c)['base'] + ':a.txt') == b'A+B\n'
        reconcile(c, cp, 'A+B+C', 'C')
        await tasks.review(alias, c, (await tasks.review(alias, c))['token'])
        assert (project / 'a.txt').read_text() == 'A+B+C\n'
        assert all((project / ('new-' + name)).is_file() for name in ('A', 'B', 'C'))
        assert (project / 'b.txt').read_text() == 'Main uncommitted change\n'
        assert not (project / 'deleted.txt').exists()
        assert (project / '.git' / 'index').read_bytes() == main_index
        blob = tasks._git(bp, 'rev-parse', old_b['base'] + ':a.txt').decode().strip()
        tasks._git(bp, 'update-index', '--index-info', data=(
            '0 {0}\ta.txt\n100644 {1} 1\ta.txt\n100644 {1} 2\ta.txt\n'.format('0' * len(blob), blob)).encode())
        await rejected(tasks.review(parent, b), 'unresolved Git conflicts')
        tasks.validate_persisted(db.connect())
    print('PASS: opt-in conflict turns, snapshot preservation, refusal rollback, stale/duplicate guards, ordered cross-parent applies and repeated Main drift')


async def actual_runner(parent):
    from puppy.drivers.base import Driver
    class ScriptDriver(Driver):
        key = "codex"
        label = "Script driver"
        binary = sys.executable
        def build_cmd(self, session, first_turn, prompt, pinned_id, **kwargs):
            assert "isolated project copy" in kwargs["system_prompt"]
            assert_main_inspection(kwargs['system_prompt'], db.get_session(parent)['cwd'])
            if 'Resolve conflicts for' in prompt:
                assert not first_turn and session['native_session_id'] == 'test-native-task'
                assert_main_inspection(prompt, db.get_session(parent)['cwd'])
            return [sys.executable, "-c", "import json,time; time.sleep(" + ("3" if "Stop me" in prompt else ".4") + "); print(json.dumps({'a':'native_id','id':'test-native-task'})); print(json.dumps({'a':'event','kind':'assistant','data':{'text':'Actual runner finished'}})); print(json.dumps({'a':'result','data':{'ok':True}}))"]
        def parse_line(self, line, ctx):
            return [json.loads(line)]
    with patch.object(runner, "get_driver", return_value=ScriptDriver()):
        a = await tasks.create(parent, {"prompt":"Real runner A", "request_id":"runner-a"})
        b = await tasks.create(parent, {"prompt":"Real runner B", "request_id":"runner-b"})
        assert runner.hub(a['id']).status == runner.hub(b['id']).status == 'running'
        await asyncio.gather(runner.hub(a['id']).turn_task, runner.hub(b['id']).turn_task)
        for row in (a,b):
            assert tasks.public(row['id'])['state'] == 'ready', db.get_events(row['id'])
            assert tasks.public(row['id'])['summary'] == 'Actual runner finished'
        project = Path(db.get_session(parent)['cwd'])
        (Path(a['cwd']) / 'runner-conflict').write_text('task addition\n')
        (project / 'runner-conflict').write_text('Main addition\n')
        review = await tasks.review(parent, a['id'])
        response = await tasks.review(parent, a['id'], review['token'], True)
        assert response['resolving']
        await runner.hub(a['id']).turn_task
        assert tasks.public(a['id'])['state'] == 'ready', db.get_events(a['id'])
        assert len([row for row in db.get_events(a['id']) if row['kind'] == 'user']) == 2
        assert (project / 'runner-conflict').read_text() == 'Main addition\n'
        (project / 'runner-conflict').unlink()
        slow = await tasks.create(parent, {"prompt":"Stop me", "request_id":"stop-runner"})
        fast = await tasks.create(parent, {"prompt":"Keep working", "request_id":"keep-runner"})
        slow_hub, fast_hub = runner.hub(slow['id']), runner.hub(fast['id'])
        await asyncio.sleep(.1)
        await slow_hub.interrupt()
        await asyncio.gather(slow_hub.turn_task, fast_hub.turn_task)
        assert tasks.public(slow['id'])['state'] == 'stopped'
        assert tasks.public(fast['id'])['state'] == 'ready'
    print('PASS: real concurrent runner lifecycle, native history on conflict follow-ups, task guidance and independent Stop')


async def toggle_api(app, parent, child):
    from aiohttp.test_utils import TestClient, TestServer
    # Exercise real routes/middleware without starting external engine probes.
    app.on_startup.clear()
    app.on_shutdown.clear()
    app.cleanup_ctx.clear()
    client = TestClient(TestServer(app), headers={'X-Puppy-Token': config.get('auth.api_token')})
    await client.start_server()
    empty = db.create_session('Toggle only', 'codex', str(ROOT), '', '', '#e0784f', 'workspace-write')
    route = '/api/sessions/' + str(empty)
    try:
        assert 'session-tasks-toggle' in app['puppy_capabilities']
        assert 'session-task-config' in app['puppy_capabilities']
        assert 'session-task-conflict-resolution' in app['puppy_capabilities']
        await configured_creation_api(client, parent)
        await resolution_api(client, parent)
        for value in (None, 'false', 0, 1, [], {}):
            response = await client.patch(route, json={'tasks_enabled': value})
            assert response.status == 400, await response.text()
        response = await client.patch(route, json={'tasks_enabled': False, 'name': 'Wrong'})
        assert response.status == 400 and db.get_session(empty)['name'] == 'Toggle only'
        # Main may still be running; only task conversations are blockers.
        runner.hub(empty).status = 'running'
        with patch.object(runner.hub(empty), 'broadcast') as broadcast:
            response = await client.patch(route, json={'tasks_enabled': False})
            data = await response.json()
            assert response.status == 200 and data['session']['tasks_enabled'] is False, data
            assert broadcast.call_args[0][0]['session']['tasks_enabled'] is False
        runner.hub(empty).status = 'idle'
        assert (await (await client.get(route)).json())['session']['tasks_enabled'] is False
        listed = (await (await client.get('/api/sessions')).json())['sessions']
        assert next(s for s in listed if s['id'] == empty)['tasks_enabled'] is False
        assert next(s for s in listed if s['id'] == parent)['tasks_enabled'] is True
        response = await client.post(route + '/tasks', json={'prompt': 'Blocked', 'request_id': 'disabled'})
        assert response.status == 409 and 'disabled' in (await response.json())['error']
        # Status-bar visibility is independent and does not re-enable Tasks.
        response = await client.patch(route, json={'show_meta': False})
        assert response.status == 200 and (await response.json())['session']['tasks_enabled'] is False
        response = await client.patch(route, json={'tasks_enabled': True})
        data = await response.json()
        assert data['session']['tasks_enabled'] is True and data['session']['show_meta'] is False
        assert db.meta_get(tasks.DISABLED_PREFIX + str(empty)) is None
        # Finished/hidden, running, queued, and held children all block it.
        hub = runner.hub(child)
        for status, queue, held in [('idle', [], []), ('running', [], []),
                                     ('idle', ['Later'], []), ('idle', [], ['Held'])]:
            hub.status, hub.queue, hub.held = status, queue, held
            response = await client.patch('/api/sessions/' + str(parent), json={'tasks_enabled': False})
            assert response.status == 409 and 'Remove all' in (await response.json())['error']
            assert tasks.enabled(parent)
        hub.status, hub.queue, hub.held = 'idle', [], []
        response = await client.patch('/api/sessions/' + str(child), json={'tasks_enabled': False})
        assert response.status == 409
        await tasks.set_enabled(empty, False)
        tasks.validate_persisted(db.connect())
    finally:
        runner.drop_hub(empty)
        db.delete_session(empty)
        assert db.meta_get(tasks.DISABLED_PREFIX + str(empty)) is None
        await client.close()
    print('PASS: ' + app['puppy_role'] + ' task toggle, broadcasts, per-session persistence, blockers and validation')


async def configured_creation_api(client, parent):
    """Explicit dialog choices reach the very first turn on either runtime."""
    from puppy.drivers import all_drivers
    models = [
        {'value': '', 'label': 'Default', 'effort_options': [{'value': ''}]},
        {'value': 'precise', 'label': 'Precise', 'effort_options': [{'value': ''}, {'value': 'high'}]},
        {'value': 'quick', 'label': 'Quick', 'effort_options': [{'value': ''}, {'value': 'low'}]},
    ]
    original = db.get_session(parent)
    before = set(tasks.children(parent))
    fields = ('engine', 'model', 'effort', 'permission_mode')
    chosen = dict(engine='codex', model='precise', effort='high', permission_mode='read-only')
    started = []
    def capture(hub, prompt):
        started.append({key: db.get_session(hub.id)[key] for key in fields})
        start(hub, prompt)
    async def create(body, status=200):
        response = await client.post('/api/sessions/{}/tasks'.format(parent), json=body)
        data = await response.json()
        assert response.status == status, (response.status, data)
        return data
    try:
        with ExitStack() as stack:
            for driver in all_drivers():
                stack.enter_context(patch.object(driver, 'refresh_model_options', AsyncMock()))
                stack.enter_context(patch.object(driver, 'model_options', return_value=models))
                stack.enter_context(patch.object(driver, 'allow_custom_model', False))
            stack.enter_context(patch.object(runner.SessionHub, '_start_turn', capture))
            db.touch_session(parent, **chosen, fast_mode=1)
            inherited = (await create({'prompt': 'Inherited', 'request_id': 'inherit-config'}))['session']
            assert started[-1] == chosen and inherited['fast_mode'] is True
            # Main changes after the browser captured its selections.
            db.touch_session(parent, model='quick', effort='low', permission_mode='workspace-write')
            main_now = db.get_session(parent)
            body = dict(chosen, prompt='Captured choices', request_id='explicit-config')
            explicit = (await create(body))['session']
            assert started[-1] == chosen
            assert (await create(body))['session']['id'] == explicit['id']
            assert len(started) == 2, 'an uncertain retry must not start twice'
            switched = dict(chosen, engine='claude', permission_mode='bypassPermissions')
            other = (await create(dict(switched, prompt='Other engine', request_id='other-config')))['session']
            assert started[-1] == switched and other['fast_mode'] is False
            native = dict(chosen, model='', effort='')
            await create(dict(native, prompt='Native defaults', request_id='native-config'))
            assert started[-1] == native, 'empty choices must not become saved defaults'
            with patch('puppy.engine_defaults.values', return_value={
                    'model': 'quick', 'effort': 'low', 'permission_mode': 'auto'}):
                await create({'engine': 'claude', 'prompt': 'Engine defaults', 'request_id': 'engine-config'})
                assert started[-1] == dict(engine='claude', model='quick', effort='low', permission_mode='auto')
            for index, invalid in enumerate(({'engine': []}, {'engine': 'unknown'},
                    {'model': 'retired'}, {'model': None}, {'effort': 'low'},
                    {'effort': 1}, {'permission_mode': 'invalid'}, {'permission_mode': None})):
                with patch.object(workspaces, 'create_temporary') as allocate:
                    data = await create(dict(body, **invalid, request_id='invalid-' + str(index)), 409)
                    assert data['error']
                    allocate.assert_not_called()
            assert all(db.get_session(parent)[key] == main_now[key] for key in fields + ('fast_mode',)), \
                'task choices must not edit Main'
            tasks.validate_persisted(db.connect())
    finally:
        db.touch_session(parent, **{key: original[key] for key in fields}, fast_mode=original['fast_mode'])
        for sid in set(tasks.children(parent)) - before:
            runner.hub(sid).status = 'idle'
            workspaces.remove_temporary(db.get_session(sid))
            runner.drop_hub(sid)
            db.delete_session(sid)
    print('PASS: task config inheritance, explicit first-turn choices, validation, defaults, Fast reset and retries')


async def attachment_adoption(parent):
    """Files staged under Main travel into the task's own storage with its prompt."""
    root = Path(config.DATA_DIR) / 'uploads'
    def stage(upload_id, name, data):
        directory = root / str(parent) / upload_id
        directory.mkdir(parents=True, mode=0o700)
        (directory / name).write_bytes(data)
        return str(directory / name)
    image = stage('1700000000000-a1b2c3d4e5', 'shot.png', b'png-bytes')
    doc = stage('1700000000001-b2c3d4e5f6', 'notes.txt', b'notes')
    kept = stage('1700000000002-c3d4e5f6a7', 'draft.png', b'main-draft')
    db.set_session_draft(parent, '[image attached: ' + kept + ' — view it with your image/file tools]')
    # prose naming a Main upload path is prose; only marker lines are adopted
    prose = 'Build it like the mockup at ' + image + ' and mention @Browser AB12'
    markers = ('[image attached: ' + image + ' — view it with your image/file tools]\n'
               '[file attached: ' + doc + ' (notes.txt, 5 B) — inspect it with your file tools]\n'
               '[image attached: ' + kept + ' — view it with your image/file tools]')
    prompt = prose + '\n\n' + markers
    tid = None
    try:
        with patch.object(workspaces, 'create_temporary') as allocate:
            gone = str(root / str(parent) / '1700000000009-d4e5f6a7b8' / 'gone.png')
            await rejected(tasks.create(parent, {'prompt': '[image attached: ' + gone +
                ' — view it with your image/file tools]', 'request_id': 'gone'}), 'no longer available')
            allocate.assert_not_called()
        created = await tasks.create(parent, {'prompt': prompt, 'request_id': 'attachments'})
        tid = created['id']
        task_root = root / str(tid)
        assert (task_root / '1700000000000-a1b2c3d4e5' / 'shot.png').read_bytes() == b'png-bytes'
        assert (task_root / '1700000000001-b2c3d4e5f6' / 'notes.txt').read_bytes() == b'notes'
        assert (task_root / '1700000000002-c3d4e5f6a7' / 'draft.png').read_bytes() == b'main-draft'
        expected = prose + '\n\n' + markers.replace(str(root / str(parent)) + '/', str(task_root) + '/')
        assert expected != prompt and str(root / str(parent)) in expected, 'prose keeps its path'
        assert tasks.record(tid)['prompt'] == expected, tasks.record(tid)['prompt']
        assert runner.hub(tid)._active_prompt_text == expected, 'the first turn carries the copies'
        assert [e['data']['text'] for e in db.get_events(tid) if e['kind'] == 'user'] == [expected]
        # Main's copies served only the dialog, except the one its own draft still names.
        assert not (root / str(parent) / '1700000000000-a1b2c3d4e5').exists()
        assert not (root / str(parent) / '1700000000001-b2c3d4e5f6').exists()
        assert (root / str(parent) / '1700000000002-c3d4e5f6a7' / 'draft.png').is_file()
        # A retry after a lost reply still carries Main's paths and finds its task.
        assert (await tasks.create(parent, {'prompt': prompt, 'request_id': 'attachments'}))['id'] == tid
        await rejected(tasks.create(parent, {'prompt': prompt + ' changed', 'request_id': 'attachments'}), 'different')
        assert uploads.rewrite_attachment_paths(parent, tid, 'no markers here') == 'no markers here'
        tasks.validate_persisted(db.connect())
    finally:
        db.set_session_draft(parent, '')
        shutil.rmtree(root / str(parent), ignore_errors=True)
        if tid is not None:
            runner.hub(tid).status = 'idle'
            workspaces.remove_temporary(db.get_session(tid))
            runner.drop_hub(tid)
            db.delete_session(tid)
            uploads.remove_session_storage(tid)
            assert not (root / str(tid)).exists()
            protected = ROOT / 'protected-upload-cleanup'
            protected.mkdir()
            (protected / 'keep.txt').write_text('preserve these bytes')
            link = root / str(tid)
            link.symlink_to(protected, target_is_directory=True)
            try:
                uploads.remove_session_storage(tid)
                assert (protected / 'keep.txt').read_text() == 'preserve these bytes'
                assert link.is_symlink()
            finally:
                link.unlink()
                shutil.rmtree(protected)
    print('PASS: task prompts adopt Main-staged attachments, keep prose, refuse missing files and retry by identity')


async def main():
    config.ensure_dirs()
    await conflict_resolution()
    project = ROOT / 'project'
    project.mkdir()
    tasks._git(project, 'init', '--quiet')
    (project/'a.txt').write_text('original a\n')
    (project/'b.txt').write_text('original b\n')
    (project/'.gitignore').write_text('ignored/\nCLAUDE.md\n')
    (project/'CLAUDE.md').write_text('project notes\n')
    (project/'ignored').mkdir()
    (project/'ignored'/'secret').write_text('synthetic private file')
    tasks._git(project,'add','-A')
    tasks._git(project,'commit','-qm','Initial')
    (project/'b.txt').write_text('uncommitted baseline b\n')
    (project/'untracked.txt').write_text('keep this too\n')
    parent = db.create_session('Project','codex',str(project),'','','#e0784f','workspace-write')
    db.add_event(parent,'user',{'text':'Use the existing design language.'})
    with patch.object(runner.SessionHub, '_start_turn', start):
        # A disable arriving during a real clone must wait and then refuse;
        # another queued creation still succeeds and retains its own work.
        loop = asyncio.get_running_loop()
        copying, release = asyncio.Event(), threading.Event()
        copy_project = tasks._copy_project
        def slow_copy(*args):
            loop.call_soon_threadsafe(copying.set)
            if not release.wait(10):
                raise AssertionError('copy was not released')
            return copy_project(*args)
        with patch.object(tasks, '_copy_project', slow_copy):
            first = asyncio.create_task(tasks.create(parent, {'prompt':'Feature A','request_id':'a'}))
            try:
                await asyncio.wait_for(copying.wait(), 5)
                disable = asyncio.create_task(tasks.set_enabled(parent, False))
                second = asyncio.create_task(tasks.create(parent, {'prompt':'Feature B','request_id':'b'}))
                await asyncio.sleep(0)
                assert not disable.done()
            finally:
                release.set()
            a, b = await asyncio.gather(first, second)
            await rejected(disable, 'Remove all')
        assert tasks.enabled(parent)
        aid, bid = a['id'], b['id']
        ap, bp = Path(a['cwd']), Path(b['cwd'])
        assert ap != bp and ap != project and bp != project
        assert (ap/'b.txt').read_text() == 'uncommitted baseline b\n'
        assert (bp/'untracked.txt').is_file() and not (bp/'ignored').exists()
        assert (ap/'CLAUDE.md').read_text() == 'project notes\n'
        assert tasks._git(ap,'rev-parse','--verify','refs/puppy/base').decode().strip() == tasks.record(aid)['base']
        assert all('tasks_enabled' in s for s in runner.sessions_payload()['sessions'])
        assert tasks._git(ap,'remote').strip() == b''
        assert a['permission_mode'] == 'workspace-write'
        assert 'Use the existing design' in tasks.guidance(aid, True)
        assert_main_inspection(tasks.guidance(aid, True), project)
        assert_main_inspection(tasks.guidance(aid, False), project)
        # A manual drift follow-up gets the current path even without a
        # resolution snapshot. Quote unusual paths as data on one line.
        moved = str(ROOT / 'Main "renamed"\nfolder')
        try:
            db.touch_session(parent, cwd=moved)
            text = tasks.guidance(aid, False)
            assert_main_inspection(text, moved)
            assert json.loads(text.split('JSON-quoted path): ', 1)[1].splitlines()[0]) == moved
            assert 'Use the existing design' not in text
        finally:
            db.touch_session(parent, cwd=str(project))
        assert (await tasks.create(parent, {'prompt':'Feature A','request_id':'a'}))['id'] == aid
        await rejected(tasks.create(parent, {'prompt':'Different','request_id':'a'}),'different')
        assert runner.hub(aid).status == runner.hub(bid).status == 'running'
        runner.hub(aid).send_message('A follow-up')
        assert runner.hub(aid).queue == ['A follow-up'] and not runner.hub(bid).queue
        runner.hub(aid).queue.clear()
        await rejected(tasks.review(parent,aid),'finish')
        (ap/'a.txt').write_text('feature a\n')
        (bp/'b.txt').write_text('feature b\n')
        finish(aid); finish(bid)
        assert (project/'a.txt').read_text() == 'original a\n'
        assert tasks.public(aid)['state'] == 'ready'
        review = await tasks.review(parent,aid)
        assert 'a.txt' in review['files'] and 'feature a' in review['diff']
        # The review reads the working tree through a private index: nothing
        # is staged behind the engine's back, and the force-copied notes file
        # stays tracked instead of showing up as a deletion.
        assert tasks._git(ap,'diff','--cached','--name-only').strip() == b''
        assert 'CLAUDE.md' not in review['files'] and not (ap/'.git'/'puppy-review-index').exists()
        # Only this session's own tasks and operations block deleting it.
        assert 'tasks' in tasks.delete_blocker(db.get_session(parent))
        assert tasks.delete_blocker(db.get_session(aid)) is None
        tasks._busy_roots.add(str(ap))
        assert 'using' in tasks.delete_blocker(db.get_session(aid))
        assert 'using' in tasks.reset_blocker(db.get_session(parent)) or 'tasks' in tasks.reset_blocker(db.get_session(parent))
        tasks._busy_roots.discard(str(ap))
        assert 'working copies' in tasks.reset_blocker(db.get_session(aid))
        await tasks.review(parent,aid,review['token'])
        assert (project/'a.txt').read_text() == 'feature a\n'
        assert tasks.public(aid)['state'] == 'applied'
        # The applied tree is the next baseline and stays pinned in the copy.
        assert tasks._git(ap,'rev-parse','--verify','refs/puppy/base').decode().strip() == tasks.record(aid)['base']
        review = await tasks.review(parent,bid)
        await tasks.review(parent,bid,review['token'])
        assert (project/'b.txt').read_text() == 'feature b\n'
        # An applied task's next delta excludes its previously applied edits.
        (ap/'new.txt').write_text('follow-up delta\n')
        review = await tasks.review(parent,aid)
        assert 'new.txt' in review['files'] and 'a.txt' not in review['files']
        (ap/'new.txt').write_text('changed after review\n')
        await rejected(tasks.review(parent,aid,review['token']),'changed')
        # A conflicting patch must not partially apply its independent file.
        (ap/'a.txt').write_text('task overlapping edit\n')
        (project/'a.txt').write_text('Main overlapping edit\n')
        review = await tasks.review(parent,aid)
        await rejected(tasks.review(parent,aid,review['token']),'patch')
        assert not (project/'new.txt').exists()
        assert (project/'a.txt').read_text() == 'Main overlapping edit\n'
        assert (ap/'new.txt').is_file()
        # Busy workspace ownership survives a refused competing apply.
        tasks._busy_roots.add(str(project))
        await rejected(tasks.review(parent,bid,(await tasks.review(parent,bid))['token']),'another task')
        assert str(project) in tasks._busy_roots
        tasks._busy_roots.clear()
        runner.hub(parent).status = 'running'
        await rejected(tasks.create(parent, {'prompt':'C','request_id':'c'}),'idle')
        runner.hub(parent).status = 'idle'
        tasks.validate_persisted(db.connect())
        rows = runner.sessions_payload()['sessions']
        assert next(s for s in rows if s['id']==parent)['task_activity']['total'] == 2
        assert all(s['task']['parent']==parent for s in rows if s['id'] in (aid,bid))
        try: db.delete_session(parent)
        except ValueError: pass
        else: raise AssertionError('orphan tasks allowed')
        workspaces.remove_temporary(db.get_session(bid))
        await rejected(tasks.wait_for_workspace(db.get_session(bid),runner.hub(bid)), 'missing')
        runner.drop_hub(bid); db.delete_session(bid)
        assert tasks.record(bid) is None and tasks.children(parent)==[aid]
        # A clone containing symlinks cannot redirect a task copy outside it.
        (project/'external-link').symlink_to(str(ROOT))
        await rejected(tasks.create(parent, {'prompt':'Symlink copy','request_id':'symlink'}), 'relative symlinks')
        (project/'external-link').unlink()
        (project/'external-link').symlink_to('a.txt')
        copied = await tasks.create(parent, {'prompt':'Symlink copy','request_id':'symlink'})
        assert (Path(copied['cwd'])/'external-link').is_symlink()
        finish(copied['id'])
        await attachment_adoption(parent)
    await actual_runner(parent)
    from puppy.web import build_app
    sys.path.insert(0,str(BASE/'backend'))
    from puppy_backend.app import build_app as backend_app
    for build in (build_app, backend_app):
        app = build()
        assert 'session-tasks' in app['puppy_capabilities']
        assert '/api/sessions/{sid}/tasks/{tid}/{action}' in {r.resource.canonical for r in app.router.routes()}
        await toggle_api(app, parent, aid)
    # Offline rollback retains all session ids, messages and private files,
    # and archives the per-session Tasks toggle with the grouping it removes.
    keep = db.create_session('Toggle kept', 'codex', str(ROOT), '', '', '#e0784f', 'workspace-write')
    await tasks.set_enabled(keep, False)
    before = {s['id']: (s['cwd'], db.get_events(s['id'])) for s in db.list_sessions(True)}
    with patch('socket.create_connection', side_effect=OSError('offline')):
        archive = tasks.detach_for_rollback()
    assert archive.stat().st_mode & 0o777 == 0o600
    archived = json.loads(archive.read_text())
    assert archived['tasks'] and archived['tasks_disabled'] == [keep] and not tasks.records()
    assert db.meta_get(tasks.DISABLED_PREFIX + str(keep)) is None and not tasks.disabled_ids()
    assert before == {s['id']: (s['cwd'], db.get_events(s['id'])) for s in db.list_sessions(True)}
    print('PASS: rollback preserves conversations and working copies')
    print('PASS: concurrent independent queues/copies, dirty baselines, context/settings, idempotency, review tokens, compatible apply, atomic conflict rejection, busy ownership, lifecycle and both runtimes')


if __name__ == '__main__':
    try: asyncio.run(main())
    finally:
        for row in db.list_sessions(include_archived=True):
            if workspaces.is_temporary(row): workspaces.remove_temporary(row)
        shutil.rmtree(ROOT)
