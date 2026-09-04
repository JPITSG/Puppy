#!/usr/bin/env python3
"""Task isolation, queue ownership, review/apply and API contracts without quota."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root('session-tasks-')
os.environ['PUPPY_DATA'] = str(ROOT / 'data')
from puppy import config, db, runner, session_tasks as tasks, workspaces


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


async def actual_runner(parent):
    from puppy.drivers.base import Driver
    class ScriptDriver(Driver):
        key = "codex"
        label = "Script driver"
        binary = sys.executable
        def build_cmd(self, session, first_turn, prompt, pinned_id, **kwargs):
            assert "isolated project copy" in kwargs["system_prompt"]
            return [sys.executable, "-c", "import json,time; time.sleep(" + ("3" if "Stop me" in prompt else ".4") + "); print(json.dumps({'a':'event','kind':'assistant','data':{'text':'Actual runner finished'}})); print(json.dumps({'a':'result','data':{'ok':True}}))"]
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
        slow = await tasks.create(parent, {"prompt":"Stop me", "request_id":"stop-runner"})
        fast = await tasks.create(parent, {"prompt":"Keep working", "request_id":"keep-runner"})
        slow_hub, fast_hub = runner.hub(slow['id']), runner.hub(fast['id'])
        await asyncio.sleep(.1)
        await slow_hub.interrupt()
        await asyncio.gather(slow_hub.turn_task, fast_hub.turn_task)
        assert tasks.public(slow['id'])['state'] == 'stopped'
        assert tasks.public(fast['id'])['state'] == 'ready'
    print('PASS: real concurrent runner lifecycle, task guidance and independent Stop')


async def main():
    config.ensure_dirs()
    project = ROOT / 'project'
    project.mkdir()
    tasks._git(project, 'init', '--quiet')
    (project/'a.txt').write_text('original a\n')
    (project/'b.txt').write_text('original b\n')
    (project/'.gitignore').write_text('ignored/\n')
    (project/'ignored').mkdir()
    (project/'ignored'/'secret').write_text('synthetic private file')
    tasks._git(project,'add','-A')
    tasks._git(project,'commit','-qm','Initial')
    (project/'b.txt').write_text('uncommitted baseline b\n')
    (project/'untracked.txt').write_text('keep this too\n')
    parent = db.create_session('Project','codex',str(project),'','','#e0784f','workspace-write')
    db.add_event(parent,'user',{'text':'Use the existing design language.'})
    with patch.object(runner.SessionHub, '_start_turn', start):
        a, b = await asyncio.gather(tasks.create(parent, {'prompt':'Feature A','request_id':'a'}),
                                     tasks.create(parent, {'prompt':'Feature B','request_id':'b'}))
        aid, bid = a['id'], b['id']
        ap, bp = Path(a['cwd']), Path(b['cwd'])
        assert ap != bp and ap != project and bp != project
        assert (ap/'b.txt').read_text() == 'uncommitted baseline b\n'
        assert (bp/'untracked.txt').is_file() and not (bp/'ignored').exists()
        assert tasks._git(ap,'remote').strip() == b''
        assert a['permission_mode'] == 'workspace-write'
        assert 'Use the existing design' in tasks.guidance(aid, True)
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
        await tasks.review(parent,aid,review['token'])
        assert (project/'a.txt').read_text() == 'feature a\n'
        assert tasks.public(aid)['state'] == 'applied'
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
    await actual_runner(parent)
    from puppy.web import build_app
    sys.path.insert(0,str(BASE/'backend'))
    from puppy_backend.app import build_app as backend_app
    for app in (build_app(),backend_app()):
        assert 'session-tasks' in app['puppy_capabilities']
        assert '/api/sessions/{sid}/tasks/{tid}/{action}' in {r.resource.canonical for r in app.router.routes()}
    # Offline rollback retains all session ids, messages and private files.
    before = {s['id']: (s['cwd'], db.get_events(s['id'])) for s in db.list_sessions(True)}
    with patch('socket.create_connection', side_effect=OSError('offline')):
        archive = tasks.detach_for_rollback()
    assert archive.stat().st_mode & 0o777 == 0o600
    assert json.loads(archive.read_text())['tasks'] and not tasks.records()
    assert before == {s['id']: (s['cwd'], db.get_events(s['id'])) for s in db.list_sessions(True)}
    print('PASS: rollback preserves conversations and working copies')
    print('PASS: concurrent independent queues/copies, dirty baselines, context/settings, idempotency, review tokens, compatible apply, atomic conflict rejection, busy ownership, lifecycle and both runtimes')


if __name__ == '__main__':
    try: asyncio.run(main())
    finally:
        for row in db.list_sessions(include_archived=True):
            if workspaces.is_temporary(row): workspaces.remove_temporary(row)
        shutil.rmtree(ROOT)
