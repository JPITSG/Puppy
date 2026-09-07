#!/usr/bin/env python3
"""Scratch promotion, rollback, operation ownership and both HTTP runtimes."""
import asyncio
import os
from pathlib import Path
import shutil
import sys
import threading
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root('workspace-move-')
os.environ['PUPPY_DATA'] = str(ROOT / 'data')
from puppy import config, db, runner, session_tasks, snapshots, workspaces


def create():
    source = Path(workspaces.create_temporary())
    (source / '.git').mkdir()
    (source / '.git' / 'config').write_text('git data')
    (source / 'file').write_bytes(b'project\x00bytes')
    (source / 'file').chmod(0o755)
    (source / 'link').symlink_to('file')
    (source / 'external').symlink_to(ROOT / 'outside')
    sid = db.create_session('Scratch', 'codex', str(source), '', '', '', 'workspace-write',
                            workspace_kind='temporary')
    db.touch_session(sid, native_session_id='old-context')
    db.add_event(sid, 'assistant', {'text': 'Existing conversation'})
    return sid, source


async def reject(sid, target):
    try:
        await workspaces.move_session(sid, target)
    except (workspaces.WorkspaceError, session_tasks.TaskError, OSError):
        return
    raise AssertionError('Expected refusal: ' + str(target))


async def main():
    config.ensure_dirs()
    sid, source = create()
    target = ROOT / 'permanent'
    for invalid in ('relative', '', None, str(source / 'nested'), str(ROOT / 'data' / 'project'),
                    str(ROOT / 'missing' / 'project'), str(ROOT), str(source)):
        await reject(sid, invalid)
    for attr, value in [('status', 'running'), ('queue', ['pending']), ('held', ['held'])]:
        hub = runner.hub(sid)
        old = getattr(hub, attr)
        setattr(hub, attr, value)
        await reject(sid, str(target))
        setattr(hub, attr, old)
    with patch.object(session_tasks, 'record', return_value={'parent': 2}):
        await reject(sid, str(target))
    with patch.object(session_tasks, 'children', return_value=[2]):
        await reject(sid, str(target))
    # A task may be created while the move waits for the shared parent lock.
    # Revalidate after acquiring it, before allocating the destination.
    with patch.object(session_tasks, 'children', return_value=[2]):
        async with session_tasks.session_operation(sid):
            blocked_move = asyncio.create_task(reject(sid, str(target)))
            await asyncio.sleep(.02)
            assert not blocked_move.done() and not target.exists()
        await blocked_move
    with patch.object(shutil, 'copytree', side_effect=OSError('disk full')):
        await reject(sid, str(target))
    assert not target.exists() and (source / 'file').exists()
    with patch.object(db, 'touch_session', side_effect=OSError('db failure')):
        await reject(sid, str(target))
    assert not target.exists() and (source / 'file').exists()
    assert db.get_session(sid)['cwd'] == str(source)

    entered, release = threading.Event(), threading.Event()
    copy = workspaces._copy_to_directory
    def slow_copy(*args):
        entered.set()
        assert release.wait(10)
        copy(*args)
    with patch.object(workspaces, '_copy_to_directory', slow_copy):
        request = asyncio.create_task(workspaces.move_session(sid, str(target)))
        try:
            while not entered.is_set():
                await asyncio.sleep(.01)
            assert session_tasks.busy()
            assert session_tasks.delete_blocker(db.get_session(sid))
            assert session_tasks.reset_blocker(db.get_session(sid))
            assert snapshots.blockers()
            waiter = asyncio.create_task(session_tasks.wait_for_workspace(db.get_session(sid), runner.hub(sid)))
            await asyncio.sleep(.02)
            assert not waiter.done()
            # Losing the caller cannot release ownership while the worker copies.
            request.cancel()
            try:
                await request
            except asyncio.CancelledError:
                pass
            assert session_tasks.busy()
        finally:
            release.set()
        while session_tasks.busy():
            await asyncio.sleep(.01)
        await waiter
    updated = db.get_session(sid)
    assert updated['workspace_kind'] == 'directory' and not updated['native_session_id']
    assert updated['cwd'] == str(target) and not source.exists()
    assert (target / 'file').read_bytes() == b'project\x00bytes'
    assert (target / 'file').stat().st_mode & 0o777 == 0o755
    assert (target / 'link').is_symlink() and (target / 'external').is_symlink()
    assert (target / '.git' / 'config').read_text() == 'git data'
    assert not workspaces.remove_temporary(updated)
    assert target.exists()
    assert db.query_one("SELECT id FROM events WHERE session_id=? AND kind='assistant'", (sid,))
    await reject(sid, str(ROOT / 'again'))

    from aiohttp.test_utils import TestClient, TestServer
    from puppy.web import build_app
    sys.path.insert(0, str(BASE / 'backend'))
    from puppy_backend.app import build_app as backend_app
    for build in (build_app, backend_app):
        app = build()
        app.on_startup.clear()
        app.on_shutdown.clear()
        app.cleanup_ctx.clear()
        assert 'workspace-move' in app['puppy_capabilities']
        client = TestClient(TestServer(app), headers={'X-Puppy-Token': config.get('auth.api_token')})
        await client.start_server()
        try:
            sid, source = create()
            path = '/api/sessions/{}/workspace/move'.format(sid)
            response = await client.post(path, json=[])
            assert response.status == 400
            response = await client.post(path, json={'destination': str(source)})
            assert response.status == 409
            target = ROOT / ('http-' + str(sid))
            response = await client.post(path, json={'destination': str(target)})
            assert response.status == 200, await response.text()
            result = await response.json()
            assert result['session']['workspace_kind'] == 'directory'
            assert result['session']['cwd'] == str(target)
            response = await client.delete('/api/sessions/{}'.format(sid))
            assert response.status == 200, await response.text()
            assert (target / 'file').exists()
        finally:
            await client.close()
    print('PASS: move preservation, rollback, validation, cancellation, shared guards and both runtimes')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(ROOT)
