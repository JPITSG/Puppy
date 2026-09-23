#!/usr/bin/env python3
"""Refresh real task copies from arbitrary Main repositories; no model quota."""
import asyncio
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import threading
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.session_tasks_test import ROOT, start, finish, rejected
from puppy import config, db, operations, runner, session_tasks as tasks, workspaces
from puppy.drivers.base import Driver


async def fixture(name):
    project = ROOT / name
    project.mkdir()
    tasks._git(project, 'init', '--quiet')
    tasks._git(project, 'symbolic-ref', 'HEAD', 'refs/heads/integration/next')
    for name, data in {'note': b'original\n', 'remove': b'old\n', 'file-to-dir': b'file\n',
                       'binary': b'\0\1\2', '.gitignore': b'cache/\nlocal-*\nAGENTS.md\n'}.items():
        (project / name).write_bytes(data)
    (project / 'dir-to-file').mkdir()
    (project / 'dir-to-file' / 'child').write_text('child\n')
    (project / 'AGENTS.md').write_text('Invented project instructions\n')
    tasks._git(project, 'add', '-A')
    tasks._git(project, 'commit', '-qm', 'Initial')
    (project / 'nested').mkdir()
    parent = db.create_session('Example', 'codex', str(project / 'nested'), '', '', '', 'workspace-write')
    row = await tasks.create(parent, {'prompt': 'Discuss the design before editing', 'request_id': 'discuss'})
    sid = row['id']
    finish(sid, 'Here is a proposed design; no edits yet.')
    db.touch_session(sid, native_session_id='fixture-conversation')
    db.set_session_draft(sid, 'keep this draft')
    return project, parent, sid, Path(row['cwd'])


def snapshot(sid, path):
    return (tasks.record(sid), tasks._git(path, 'rev-parse', 'HEAD'),
            tasks._git(path, 'rev-parse', 'refs/puppy/base'), (path / '.git/index').read_bytes(),
            tasks._git(path, '--no-optional-locks', 'status', '--porcelain=v1', '--untracked-files=all'),
            {p.relative_to(path).as_posix(): (os.readlink(p) if p.is_symlink() else p.read_bytes())
             for p in path.rglob('*') if '.git' not in p.relative_to(path).parts and (p.is_symlink() or p.is_file())})


async def successful_refresh():
    project, parent, sid, path = await fixture('arbitrary-project')
    before_session = db.get_session(sid)
    conversation = db.get_events(sid)
    draft = db.get_session_draft(sid)
    stale = await tasks.review(parent, sid)
    (project / 'note').write_text('committed update\n')
    tasks._git(project, 'commit', '-qam', 'Upstream progress')
    (project / 'note').write_text('uncommitted Main update\n')
    (project / 'remove').unlink()
    (project / 'file-to-dir').unlink()
    (project / 'file-to-dir').mkdir()
    (project / 'file-to-dir' / 'child').write_text('new child\n')
    shutil.rmtree(project / 'dir-to-file')
    (project / 'dir-to-file').write_text('now a file\n')
    (project / 'binary').write_bytes(b'\0\xff\xfe\x01')
    (project / 'AGENTS.md').write_text('Updated project instructions\n')
    (project / 'run me').write_text('example\n')
    (project / 'run me').chmod(0o755)
    (project / 'relative-link').symlink_to('note')
    (project / 'námé\nline').write_text('new untracked file\n')
    (path / 'cache').mkdir()
    (path / 'cache' / 'artifact').write_text('keep generated output')
    (path / 'local-link').symlink_to('cache')
    index = (project / '.git/index').read_bytes()
    head = tasks._git(project, 'rev-parse', 'HEAD')
    result = await tasks.refresh(parent, sid)
    assert result['refreshed'] and result['changed_files'] >= 10, result
    for name in ('note', 'file-to-dir/child', 'dir-to-file', 'binary', 'AGENTS.md', 'run me', 'námé\nline'):
        assert (path / name).read_bytes() == (project / name).read_bytes(), name
    assert not (path / 'remove').exists()
    assert os.readlink(path / 'relative-link') == 'note'
    assert (path / 'run me').stat().st_mode & 0o111
    assert (path / 'cache/artifact').read_text() == 'keep generated output'
    assert os.readlink(path / 'local-link') == 'cache'
    assert (project / '.git/index').read_bytes() == index
    assert tasks._git(project, 'rev-parse', 'HEAD') == head
    assert tasks._git(path, 'status', '--porcelain').strip() == b''
    assert tasks._git(path, 'symbolic-ref', 'HEAD').strip() == b'refs/heads/integration/next'
    assert tasks._git(path, 'remote').strip() == b''
    assert tasks._git(path, 'rev-parse', 'HEAD').decode().strip() == tasks.record(sid)['base']
    after_session = db.get_session(sid)
    for key in ('cwd', 'native_session_id', 'engine', 'model', 'effort', 'permission_mode'):
        assert after_session[key] == before_session[key], key
    assert db.get_events(sid)[:len(conversation)] == conversation
    assert db.get_session_draft(sid) == draft
    assert not (await tasks.review(parent, sid))['has_changes']
    await rejected(tasks.review(parent, sid, stale['token']), 'review them again')
    guidance = tasks.guidance(sid)
    assert 'refreshed from Main' in guidance and 'Re-read' in guidance and 'note' in guidance
    db.add_event(sid, 'result', {'ok': False})
    db.add_event(sid, 'result', {'ok': True, 'tool': 'compact'})
    assert 'refreshed from Main' in tasks.guidance(sid), 'failures and maintenance do not consume the note'
    db.add_event(sid, 'user', {'text': 'Now implement it'})
    assert 'refreshed from Main' in tasks.guidance(sid)
    db.add_event(sid, 'result', {'ok': True})
    assert 'refreshed from Main' not in tasks.guidance(sid)
    before = snapshot(sid, path)
    events = db.get_events(sid)
    result = await tasks.refresh(parent, sid)
    assert not result['refreshed'] and result['changed_files'] == 0
    assert snapshot(sid, path) == before and db.get_events(sid) == events, 'an unchanged Main is a no-op'
    (path / 'task-change').write_text('the implementation\n')
    reviewed = await tasks.review(parent, sid)
    assert reviewed['files'].strip() == 'A\ttask-change'
    await tasks.review(parent, sid, reviewed['token'])
    assert (project / 'task-change').read_text() == 'the implementation\n'
    # A baseline advanced by Apply is a tree, and the task's working files
    # may still be uncommitted against HEAD. Refresh remains well-defined.
    (project / 'task-change').write_text('Main continued\n')
    assert (await tasks.refresh(parent, sid))['refreshed']
    assert (path / 'task-change').read_text() == 'Main continued\n'
    assert not (await tasks.review(parent, sid))['has_changes']
    print('PASS: current Main files, arbitrary branch/subdirectory, uncommitted and binary changes, links/modes, transitions, retained context/artifacts, one-time guidance, stale review and post-apply baseline')


async def refusals_and_rollback():
    project, parent, sid, path = await fixture('refusals')
    original = (path / 'note').read_bytes()
    (project / 'note').write_text('new Main\n')
    for staged in (False, True):
        (path / 'note').write_text('task edit\n')
        if staged:
            tasks._git(path, 'add', 'note')
            (path / 'note').write_bytes(original)  # net review empty, staged edit survives
        before = snapshot(sid, path)
        await rejected(tasks.refresh(parent, sid), 'staged changes' if staged else 'changes of its own')
        assert snapshot(sid, path) == before
        tasks._git(path, 'reset', '--quiet', 'HEAD')
        (path / 'note').write_bytes(original)
    (path / 'untracked').write_text('keep me')
    await rejected(tasks.refresh(parent, sid), 'changes of its own')
    (path / 'untracked').unlink()
    (path / 'note').write_text('committed task edit\n')
    tasks._git(path, 'commit', '-qam', 'Task edit')
    await rejected(tasks.refresh(parent, sid), 'changes of its own')
    tasks._git(path, 'reset', '--hard', tasks.record(sid)['base'])
    for owner in (parent, sid):
        hub = runner.hub(owner)
        hub.status = 'running'
        await rejected(tasks.refresh(parent, sid), 'idle' if owner == parent else 'finish')
        hub.status = 'idle'
    for name in ('queue', 'held'):
        getattr(runner.hub(sid), name).append('queued prompt')
        await rejected(tasks.refresh(parent, sid), 'finish')
        getattr(runner.hub(sid), name).clear()
    await rejected(tasks.refresh(parent + 10000, sid), 'does not belong')
    # Ignored files, directory ancestors and descendants must not be replaced.
    (path / 'cache').mkdir()
    (path / 'cache/artifact').write_text('private build artifact')
    (project / 'cache').write_text('new tracked file')
    tasks._git(project, 'add', '-f', 'cache')
    before = snapshot(sid, path)
    await rejected(tasks.refresh(parent, sid), 'overwrite a local')
    assert snapshot(sid, path) == before
    tasks._git(project, 'reset', '--quiet', 'HEAD', '--', 'cache')
    (project / 'cache').unlink()
    (project / 'local-collision').write_text('new Main file')
    tasks._git(project, 'add', '-f', 'local-collision')
    (path / 'local-collision').write_text('precious ignored file')
    before = snapshot(sid, path)
    await rejected(tasks.refresh(parent, sid), 'overwrite a local')
    assert snapshot(sid, path) == before
    tasks._git(project, 'reset', '--quiet', 'HEAD', '--', 'local-collision')
    (project / 'local-collision').unlink()
    # A failed metadata write rolls back files, HEAD, index and review ref.
    before = snapshot(sid, path)
    with patch.object(tasks, '_save_refresh', side_effect=OSError('fixture write failure')):
        try:
            await tasks.refresh(parent, sid)
        except OSError as error:
            assert str(error) == 'fixture write failure'
        else:
            raise AssertionError('write failure was hidden')
    assert snapshot(sid, path) == before
    assert not (path / '.git/index.lock').exists()
    assert not list((path / '.git').glob('puppy-refresh-*'))
    assert not tasks.busy()
    # A checkout interrupted after writing some files still restores the
    # original tree. A ref failure and event failure restore it too.
    original_git = tasks._git
    def partial_checkout(cwd, *args, **kwargs):
        if str(cwd) == str(path) and args[:3] == ('read-tree', '-m', '-u'):
            (path / 'note').write_text('partially installed Main\n')
            raise tasks.GitError('fixture checkout failure', 1)
        return original_git(cwd, *args, **kwargs)
    with patch.object(tasks, '_git', partial_checkout):
        await rejected(tasks.refresh(parent, sid), 'fixture checkout failure')
    after = snapshot(sid, path)
    assert after == before, [i for i, (a, b) in enumerate(zip(after, before)) if a != b]
    def refused_refs(cwd, *args, **kwargs):
        if str(cwd) == str(path) and args[:2] == ('update-ref', '--stdin'):
            raise tasks.GitError('fixture ref failure', 1)
        return original_git(cwd, *args, **kwargs)
    with patch.object(tasks, '_git', refused_refs):
        await rejected(tasks.refresh(parent, sid), 'fixture ref failure')
    assert snapshot(sid, path) == before
    events = db.get_events(sid)
    db.execute("CREATE TEMP TRIGGER refuse_refresh_event BEFORE INSERT ON events "
               "WHEN NEW.session_id={} BEGIN SELECT RAISE(ABORT,'fixture event failure'); END".format(sid))
    try:
        try:
            await tasks.refresh(parent, sid)
        except sqlite3.IntegrityError as error:
            assert 'fixture event failure' in str(error)
        else:
            raise AssertionError('event failure was hidden')
    finally:
        db.execute('DROP TRIGGER refuse_refresh_event')
    assert snapshot(sid, path) == before and db.get_events(sid) == events
    db.touch_session(parent, cwd=str(path))
    try:
        await rejected(asyncio.wait_for(tasks.refresh(parent, sid), 5), 'independent working copies')
    finally:
        db.touch_session(parent, cwd=str(project / 'nested'))
    print('PASS: dirty/staged/committed/untracked work, busy/queued/held tasks, ownership, ignored collisions and rollback without lost files or Git state')


async def concurrency_and_cancel():
    project, parent, sid, path = await fixture('concurrency')
    (project / 'note').write_text('Main progressed\n')
    before = snapshot(sid, path)
    loop = asyncio.get_running_loop()
    copying, release = asyncio.Event(), threading.Event()
    original_copy = tasks._copy_project
    def slow_copy(*args):
        loop.call_soon_threadsafe(copying.set)
        assert release.wait(10)
        return original_copy(*args)
    with patch.object(tasks, '_copy_project', slow_copy):
        job = asyncio.create_task(tasks.refresh(parent, sid))
        try:
            await asyncio.wait_for(copying.wait(), 5)
            assert tasks._root_busy(db.get_session(sid)) and tasks._root_busy(db.get_session(parent))
            assert tasks.delete_blocker(db.get_session(sid))
            runner.hub(sid).queue.append('arrived during snapshot')
        finally:
            release.set()
        await rejected(job, 'finish')
        runner.hub(sid).queue.clear()
    assert snapshot(sid, path) == before and not tasks.busy()
    op = operations.Operation()
    token = operations._current.set(op)
    copying.clear(); release.clear()
    try:
        with patch.object(tasks, '_copy_project', slow_copy):
            job = asyncio.create_task(tasks.refresh(parent, sid))
            try:
                await asyncio.wait_for(copying.wait(), 5)
                op.cancel()
            finally:
                release.set()
            try:
                await job
            except operations.Cancelled:
                pass
            else:
                raise AssertionError('cancellation did not reach snapshot preparation')
    finally:
        operations._current.reset(token)
    assert snapshot(sid, path) == before and not tasks.busy()
    # An apply by another Main session in the same repository finishes
    # before the waiting refresh takes its snapshot.
    async with tasks.workspace_operation(str(project)):
        job = asyncio.create_task(tasks.refresh(parent, sid))
        await asyncio.sleep(.05)
        assert not job.done()
        (project / 'note').write_text('Main changed before the project lock was released\n')
    assert (await job)['refreshed'], 'locks were released for a retry'
    assert (path / 'note').read_bytes() == (project / 'note').read_bytes()
    print('PASS: ownership during snapshot preparation, late queued work refused, cancellation cleans up before writes, successful retry')


async def runtimes():
    from aiohttp.test_utils import TestClient, TestServer
    from puppy.web import build_app
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
    from puppy_backend.app import build_app as backend_app
    for i, build in enumerate((build_app, backend_app)):
        project, parent, sid, path = await fixture('runtime-' + str(i))
        app = build()
        app.on_startup.clear(); app.on_shutdown.clear(); app.cleanup_ctx.clear()
        assert 'session-task-refresh' in app['puppy_capabilities']
        client = TestClient(TestServer(app), headers={'X-Puppy-Token': config.get('auth.api_token')})
        await client.start_server()
        try:
            route = '/api/sessions/{}/tasks/{}/refresh'.format(parent, sid)
            (project / 'note').write_text('HTTP snapshot\n')
            response = await client.post(route, json={})
            data = await response.json()
            assert response.status == 200 and data['refreshed'], data
            assert data['task']['id'] == sid and data['changed_files'] == 1
            assert (path / 'note').read_text() == 'HTTP snapshot\n'
            response = await client.post(route, json={})
            assert response.status == 200 and not (await response.json())['refreshed']
            (path / 'note').write_text('local edit\n')
            response = await client.post(route, json={})
            assert response.status == 409 and 'changes of its own' in (await response.json())['error']
            response = await client.post(route, json=[])
            assert response.status == 409
            app['puppy_snapshot_busy'] = True
            response = await client.post(route, json={})
            assert response.status == (503 if build is build_app else 409), await response.text()
            app['puppy_snapshot_busy'] = False
            workspaces.remove_temporary(db.get_session(sid))
            response = await client.post(route, json={})
            assert response.status == 409 and 'unavailable' in (await response.json())['error']
        finally:
            await client.close()
    print('PASS: authenticated refresh routes, capability, responses, busy snapshot and missing workspace on both runtimes')


async def main():
    config.load()
    db.connect()
    with patch.object(Driver, 'refresh_model_options', AsyncMock()), patch.object(runner.SessionHub, '_start_turn', start):
        await successful_refresh()
        await refusals_and_rollback()
        await concurrency_and_cancel()
        await runtimes()
    tasks.validate_persisted(db.connect())


if __name__ == '__main__':
    try:
        asyncio.run(main())
    finally:
        for row in db.list_sessions(include_archived=True):
            if workspaces.is_temporary(row):
                workspaces.remove_temporary(row)
        shutil.rmtree(ROOT)
