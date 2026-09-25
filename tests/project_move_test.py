#!/usr/bin/env python3
"""Project moves: the rename and copy paths, the sessions that follow a folder,
the guards and refusals, rollback, Cancel, Git worktrees and both runtimes.

Private fixtures, real git on scratch repositories and loopback HTTP only; no
engine, network or quota. System, home and Puppy folders are refused through
the read-only validator alone - never by attempting to move them.
"""
import asyncio
import errno
import hashlib
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from tests.scratch import private_root
ROOT = private_root('project-move-')
os.environ['PUPPY_DATA'] = str(ROOT / 'data')
from aiohttp.test_utils import TestClient, TestServer
from puppy import (config, db, operations, project_move, runner, session_tasks, snapshots,
                   workspace_sync, workspaces)
from puppy.web import build_app
sys.path.insert(0, str(BASE / 'backend'))
from puppy_backend.app import build_app as backend_app

GIT = ['git', '-c', 'user.name=Mira', '-c', 'user.email=mira@example.com', '-c', 'init.defaultBranch=main',
       '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgSign=false']
AS_ROOT = os.geteuid() == 0


def git(cwd, *args):
    return subprocess.run([*GIT, *args], cwd=str(cwd), check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout.decode()


def project(name, parent=ROOT):
    """A small repository holding every kind of entry a move must keep."""
    root = Path(parent) / name
    (root / 'src' / 'deep').mkdir(parents=True)
    (root / 'README.md').write_text('# Harbor\n')
    (root / 'src' / 'app.py').write_bytes(b'print("harbor")\x00\n')
    (root / 'src' / 'app.py').chmod(0o755)
    (root / 'src' / 'deep' / 'data.bin').write_bytes(os.urandom(300000))
    git(root, 'init', '--quiet')
    git(root, 'add', 'README.md', 'src')
    git(root, 'commit', '--quiet', '-m', 'Start')
    os.link(root / 'README.md', root / 'README.hard')
    (root / 'link').symlink_to('src/app.py')
    (root / 'src-link').symlink_to('src')
    (root / 'absolute').symlink_to(ROOT / 'nowhere')
    os.mkfifo(str(root / 'pipe'))
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(root / 'server.sock'))
    listener.close()
    if AS_ROOT:
        os.chown(str(root / 'src' / 'deep' / 'data.bin'), 1234, 2345)
    try:
        os.setxattr(str(root / 'README.md'), 'user.puppy', b'kept')
    except OSError:
        pass
    os.utime(str(root / 'README.md'), (1700000000, 1700000000))
    os.utime(str(root / 'src' / 'deep'), (1700000100, 1700000100))
    return root


def tree(root):
    """Everything a move promises to keep, keyed by path inside the folder."""
    root = str(root)
    found, inodes = {}, {}
    for folder, dirs, files in os.walk(root):
        for name in dirs + files:
            path = os.path.join(folder, name)
            info = os.lstat(path)
            relative = os.path.relpath(path, root)
            entry = [stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]
            if stat.S_ISLNK(info.st_mode):
                entry.append(os.readlink(path))
            else:
                entry.append(info.st_mtime_ns)
            if stat.S_ISREG(info.st_mode):
                entry.append(hashlib.sha256(Path(path).read_bytes()).hexdigest())
                inodes.setdefault(info.st_ino, set()).add(relative)
                try:
                    entry.append(os.getxattr(path, 'user.puppy'))
                except OSError:
                    entry.append(None)
            found[relative] = tuple(entry)
    return found, sorted(sorted(group) for group in inodes.values() if len(group) > 1)


def session(name, cwd, **fields):
    sid = db.create_session(name, 'codex', str(cwd), '', '', '', 'workspace-write', **fields)
    db.touch_session(sid, native_session_id='native-' + str(sid), last_model='gpt-demo')
    db.add_event(sid, 'assistant', {'text': 'Earlier work in ' + name})
    return sid


def moves_of(sid):
    return [event['data']['text'] for event in db.get_events(sid, limit=50)
            if event['kind'] == 'info' and event['data'].get('subtype') == 'workspace_move']


async def refuse(sid, destination, fragment, expected_cwd=None):
    try:
        await project_move.move(sid, destination, expected_cwd)
    except (workspaces.WorkspaceError, session_tasks.TaskError) as exc:
        assert fragment in str(exc), (fragment, str(exc))
        return
    raise AssertionError('expected a refusal mentioning ' + fragment)


class Hub:
    """Set a hub's state for one check and put it back."""
    def __init__(self, sid, **state):
        self.hub, self.state, self.saved = runner.hub(sid), state, {}

    def __enter__(self):
        for key, value in self.state.items():
            self.saved[key] = getattr(self.hub, key)
            setattr(self.hub, key, value)
        return self.hub

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            setattr(self.hub, key, value)


async def validation():
    source = project('harbor')
    sid = session('Harbor', source)
    before = tree(source)
    (ROOT / 'taken').mkdir()
    for destination, fragment in [
            (None, 'absolute'), ('', 'absolute'), ('relative/path', 'absolute'), ('/', 'folder name'),
            (str(ROOT / 'missing' / 'harbor'), "parent directory must already exist"),
            (str(ROOT / 'taken'), 'already exists'), (str(source), 'already in'),
            (str(source / 'src' / 'moved'), 'outside the project folder'),
            (str(ROOT / 'data' / 'harbor'), "outside Puppy's data directory")]:
        await refuse(sid, destination, fragment)
    await refuse(sid, str(ROOT / 'elsewhere'), 'no longer in', expected_cwd=str(ROOT / 'other'))
    # The folder itself: missing, a link, a mount point or holding one.
    real = os.path.realpath(source)
    for patched, fragment in [([real], 'is a mount point'),
                              ([os.path.join(real, 'src')], 'contains the mount point')]:
        with patch.object(project_move, '_mount_points', return_value=patched):
            await refuse(sid, str(ROOT / 'elsewhere'), fragment)
    alias = ROOT / 'harbor-alias'
    alias.symlink_to(source)
    linked_sid = session('Through a link', alias)
    await refuse(linked_sid, str(ROOT / 'elsewhere'), 'symbolic link')
    gone = session('Gone', ROOT / 'never-made')
    await refuse(gone, str(ROOT / 'elsewhere'), 'missing')
    inside_data = session('In data', ROOT / 'data')
    await refuse(inside_data, str(ROOT / 'elsewhere'), "inside Puppy's data directory")
    # The validator alone for folders that must never be tried for real.
    home = os.path.realpath(os.path.expanduser('~'))
    for cwd, fragments in [('', ('no project folder',)), ('relative/path', ('no project folder',)),
                           ('/', ('system folder',)), ('/usr', ('system folder',)),
                           ('/etc', ('system folder',)), (str(BASE), ("Puppy's",)),
                           (str(ROOT), ("Puppy's data",)), (home, ('home folder', 'system folder')),
                           (os.path.dirname(os.path.realpath(sys.executable)), ('the Python Puppy runs on',))]:
        try:
            project_move._source({'cwd': cwd})
        except workspaces.WorkspaceError as exc:
            assert any(fragment in str(exc) for fragment in fragments), (cwd, str(exc))
        else:
            raise AssertionError('validator accepted ' + cwd)
    # A home folder below a system one is refused for being home.
    with patch.object(project_move, '_protected',
                      return_value=[(os.path.realpath(ROOT / 'taken'), "this account's home folder")]):
        try:
            project_move._source({'cwd': str(ROOT / 'taken')})
        except workspaces.WorkspaceError as exc:
            assert 'would take this account' in str(exc), str(exc)
        else:
            raise AssertionError('validator accepted a home folder')
    # Scratch, task and linked sessions are not ordinary project folders.
    scratch = db.create_session('Scratch', 'codex', workspaces.create_temporary(), '', '', '',
                                'workspace-write', workspace_kind='temporary')
    await refuse(scratch, str(ROOT / 'elsewhere'), 'does not work in a project directory')
    with patch.object(session_tasks, 'record', return_value={'parent': sid}):
        await refuse(sid, str(ROOT / 'elsewhere'), 'Task copies')
    mirror = db.create_session('Linked', 'codex', str(source), '', '', '', 'workspace-write',
                               workspace='{"uid":"u1","root":"/srv/remote","node":"Atlas","label":"remote"}')
    await refuse(mirror, str(ROOT / 'elsewhere'), 'lives on another backend')
    # A folder shared with another backend's linked session keeps its place.
    lease = workspace_sync.create_lease(str(source / 'src'))
    await refuse(sid, str(ROOT / 'elsewhere'), 'linked session on another backend')
    workspace_sync.release_lease(lease['id'])
    lease = workspace_sync.create_lease(str(ROOT / 'taken'))
    await refuse(sid, str(ROOT / 'taken' / 'harbor'), 'choose a destination outside it')
    workspace_sync.release_lease(lease['id'])
    # Work in the folder: the session itself, then a follower by name.
    follower = session('Harbor styles', source / 'src')
    for sid_, state, fragment in [(sid, {'status': 'running'}, 'Finish or stop the turn'),
                                  (sid, {'queue': ['next']}, 'clear queued work'),
                                  (follower, {'status': 'running'}, 'Harbor styles is working'),
                                  (follower, {'queue': ['next']}, 'Harbor styles is working')]:
        with Hub(sid_, **state):
            await refuse(sid, str(ROOT / 'elsewhere'), fragment)
    # Another operation owning the folder, a folder inside it or around it.
    for busy in (str(source), str(source / 'src'), str(ROOT)):
        session_tasks._busy_roots.add(busy)
        try:
            await refuse(sid, str(ROOT / 'elsewhere'), 'Another workspace operation')
        finally:
            session_tasks._busy_roots.discard(busy)
    runner._draining = True
    try:
        await refuse(sid, str(ROOT / 'elsewhere'), 'shutting down')
    finally:
        runner._draining = False
    assert tree(source) == before and not (ROOT / 'elsewhere').exists()
    assert db.get_session(sid)['cwd'] == str(source) and db.get_session(sid)['native_session_id']
    assert not moves_of(sid) and not session_tasks.busy()
    for created in (linked_sid, gone, inside_data, scratch, mirror, follower, sid):
        workspaces.remove_temporary(db.get_session(created))
        db.delete_session(created)
        runner.drop_hub(created)
    shutil.rmtree(source)
    alias.unlink()
    print('PASS: destination, folder, session-kind, lease, busy, shutdown and stale-dialog refusals change nothing')


async def renames():
    source = project('harbor')
    status = git(source, 'status', '--porcelain')   # settles git's own index first
    before = tree(source)
    inode = os.lstat(source / 'src' / 'deep' / 'data.bin').st_ino
    sid = session('Harbor', source)
    styles = session('Harbor styles', source / 'src')
    archived = session('Harbor archive', source / 'src' / 'deep')
    db.touch_session(archived, archived=1)
    through_inside = session('Through the inner link', source / 'src-link')
    (ROOT / 'side-door').symlink_to(source / 'src')
    through_outside = session('Through the side door', ROOT / 'side-door')
    parent = session('Everything', ROOT)
    sibling_dir = ROOT / 'harbor-notes'
    sibling_dir.mkdir()
    sibling = session('Harbor notes', sibling_dir)
    with Hub(styles, held=['Run the tests']):
        result = await project_move.move(sid, str(ROOT / 'moved' / '..' / 'harbor-2026'), str(source))
        assert runner.hub(styles).held == ['Run the tests']   # held work stays held
    target = ROOT / 'harbor-2026'
    assert result['session']['cwd'] == str(target) and result['retained'] == ''
    assert sorted(result['moved']) == sorted([sid, styles, archived, through_inside, through_outside])
    after = tree(target)
    assert not source.exists() and after == before, set(after[0].items()) ^ set(before[0].items())
    assert os.lstat(target / 'src' / 'deep' / 'data.bin').st_ino == inode   # a rename, not a copy
    expected = {sid: target, styles: target / 'src', archived: target / 'src' / 'deep',
                through_inside: target / 'src-link', through_outside: target / 'src'}
    for moved, cwd in expected.items():
        row = db.get_session(moved)
        assert row['cwd'] == str(cwd) and row['native_session_id'] == '' and row['last_model'] == ''
        assert row['workspace_kind'] == 'directory'
        old = {sid: source, styles: source / 'src', archived: source / 'src' / 'deep',
               through_inside: source / 'src-link', through_outside: ROOT / 'side-door'}[moved]
        assert moves_of(moved) == [project_move.MOVED_TEXT.format(old, cwd)], moves_of(moved)
    assert db.get_session(archived)['archived']
    for untouched, cwd in ((parent, ROOT), (sibling, sibling_dir)):
        row = db.get_session(untouched)
        assert row['cwd'] == str(cwd) and row['native_session_id'] and not moves_of(untouched)
    assert git(target, 'status', '--porcelain') == status
    assert db.query_one("SELECT id FROM events WHERE session_id=? AND kind='assistant'", (sid,))
    await refuse(sid, str(ROOT / 'again'), 'no longer in', expected_cwd=str(source))
    for created in (sid, styles, archived, through_inside, through_outside, parent, sibling):
        db.delete_session(created)
        runner.drop_hub(created)
    (ROOT / 'side-door').unlink()
    shutil.rmtree(target)
    shutil.rmtree(sibling_dir)
    print('PASS: a rename keeps every entry and inode and takes followers, archived rows and linked paths along')


async def copies():
    """Across filesystems: forced on one, then for real through /dev/shm."""
    shm = Path(tempfile.mkdtemp(prefix='puppy-move-', dir='/dev/shm')) \
        if os.path.isdir('/dev/shm') else None
    cross = shm is not None and os.stat(str(shm)).st_dev != os.stat(str(ROOT)).st_dev
    try:
        for forced in (True, False):
            if not forced and not cross:
                print('SKIP: no second filesystem at /dev/shm')
                continue
            source = project('atlas')
            status = git(source, 'status', '--porcelain')
            before = tree(source)
            sid = session('Atlas', source)
            follower = session('Atlas docs', source / 'src')
            target = (ROOT if forced else shm) / 'atlas-copy'
            if forced:
                with patch.object(project_move, '_same_filesystem', return_value=False):
                    result = await project_move.move(sid, str(target))
            else:
                result = await project_move.move(sid, str(target))
            after = tree(target)
            if not forced and not AS_ROOT:
                before = ({key: value[:2] + value[4:] for key, value in before[0].items()}, before[1])
                after = ({key: value[:2] + value[4:] for key, value in after[0].items()}, after[1])
            assert after == before, set(after[0].items()) ^ set(before[0].items())
            assert stat.S_ISFIFO(os.lstat(target / 'pipe').st_mode)
            assert stat.S_ISSOCK(os.lstat(target / 'server.sock').st_mode)
            assert os.readlink(target / 'absolute') == str(ROOT / 'nowhere')
            assert not source.exists() and result['retained'] == ''
            assert sorted(result['moved']) == sorted([sid, follower])
            assert db.get_session(follower)['cwd'] == str(target / 'src')
            assert git(target, 'status', '--porcelain') == status
            assert git(target, 'fsck', '--no-progress') == ''
            db.delete_session(sid)
            db.delete_session(follower)
            shutil.rmtree(target)
        # Past the commit the move completes: the original that could not be
        # removed is named, never put back over the copy.
        source = project('atlas')
        sid = session('Atlas', source)
        target = ROOT / 'atlas-kept'
        with patch.object(project_move, '_same_filesystem', return_value=False), \
                patch.object(project_move, '_remove_original', return_value='server.sock: Permission denied'):
            result = await project_move.move(sid, str(target))
        assert result['retained'] == str(source) and source.exists()
        assert db.get_session(sid)['cwd'] == str(target) and (target / 'README.md').exists()
        assert 'could not be removed completely (server.sock: Permission denied)' in moves_of(sid)[0]
        db.delete_session(sid)
        shutil.rmtree(target)
        shutil.rmtree(source)
        # A shared device number that still refuses a rename (a bind mount).
        source = project('atlas')
        sid = session('Atlas', source)
        target = ROOT / 'atlas-bind'
        with patch.object(project_move, '_rename', side_effect=OSError(errno.EXDEV, 'Invalid cross-device link')):
            result = await project_move.move(sid, str(target))
        assert not source.exists() and (target / 'src' / 'app.py').exists() and result['retained'] == ''
        db.delete_session(sid)
        shutil.rmtree(target)
    finally:
        if shm is not None:
            shutil.rmtree(str(shm), ignore_errors=True)
    print('PASS: a copy keeps owners, modes, times, xattrs, links, FIFOs and sockets, then removes the original')


async def rollback():
    source = project('garden')
    before = tree(source)
    inode = os.lstat(source / 'README.md').st_ino
    sid = session('Garden', source)
    follower = session('Garden beds', source / 'src')
    target = ROOT / 'garden-moved'
    # A refused rename leaves nothing behind.
    with patch.object(project_move, '_rename', side_effect=PermissionError(errno.EACCES, 'Permission denied')):
        await refuse(sid, str(target), 'Permission denied')
    # A database failure after the rename puts the folder back where it was.
    with patch.object(db, 'relocate_sessions', side_effect=OSError('database is locked')):
        try:
            await project_move.move(sid, str(target))
        except OSError as exc:
            assert 'database is locked' in str(exc)
        else:
            raise AssertionError('expected the database failure')
    assert os.lstat(source / 'README.md').st_ino == inode
    # ... and discards a copy, keeping the original.
    with patch.object(project_move, '_same_filesystem', return_value=False), \
            patch.object(db, 'relocate_sessions', side_effect=OSError('database is locked')):
        try:
            await project_move.move(sid, str(target))
        except OSError:
            pass
        else:
            raise AssertionError('expected the database failure')
    # A copy that fails part-way is removed, the original untouched.
    with patch.object(project_move, '_same_filesystem', return_value=False), \
            patch.object(project_move, '_copy_file', side_effect=OSError(errno.ENOSPC, 'No space left on device',
                                                                          'README.md')):
        await refuse(sid, str(target), 'No space left on device')
    assert not target.exists() and tree(source) == before
    for moved in (sid, follower):
        row = db.get_session(moved)
        assert row['cwd'] in (str(source), str(source / 'src')) and row['native_session_id'] and not moves_of(moved)
    assert not session_tasks.busy()
    db.delete_session(sid)
    db.delete_session(follower)
    shutil.rmtree(source)
    print('PASS: refused renames, database failures and failed copies leave the folder and every session as they were')


async def ownership():
    """A long copy owns the folder: turns wait, backups and upgrades are refused,
    overlapping operations are refused, and losing the caller changes nothing."""
    source = project('orchard')
    sid = session('Orchard', source)
    follower = session('Orchard map', source / 'src')
    target = ROOT / 'orchard-moved'
    entered, release = threading.Event(), threading.Event()
    copy = project_move._copy_file

    def slow_copy(*args):
        entered.set()
        assert release.wait(20)
        copy(*args)

    with patch.object(project_move, '_same_filesystem', return_value=False), \
            patch.object(project_move, '_copy_file', slow_copy):
        request = asyncio.create_task(project_move.move(sid, str(target)))
        try:
            while not entered.is_set():
                await asyncio.sleep(.01)
            assert session_tasks.busy() and snapshots.blockers()
            assert any(row['id'] == sid for row in runner.upgrade_blockers())
            assert session_tasks.delete_blocker(db.get_session(follower))
            waiter = asyncio.create_task(session_tasks.wait_for_workspace(db.get_session(follower),
                                                                          runner.hub(follower)))
            for overlapping in (str(source / 'src'), str(ROOT)):
                try:
                    async with session_tasks.workspace_operation(overlapping):
                        raise AssertionError('overlapping operation was allowed')
                except session_tasks.TaskError as exc:
                    assert 'another task' in str(exc)
            # A session made in the folder while it copies follows it too.
            latecomer = session('Orchard latecomer', source / 'src' / 'deep')
            await asyncio.sleep(.05)
            assert not waiter.done() and not target.joinpath('README.md').exists()
            # Losing the caller cannot release the folder mid-copy.
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
    assert db.get_session(sid)['cwd'] == str(target) and not source.exists()
    assert db.get_session(follower)['cwd'] == str(target / 'src')
    assert db.get_session(latecomer)['cwd'] == str(target / 'src' / 'deep')
    for created in (sid, follower, latecomer):
        db.delete_session(created)
        runner.drop_hub(created)
    shutil.rmtree(target)
    # Nested roots exclude each other in both orders.
    async with session_tasks.workspace_operation(str(ROOT / 'a' / 'b')):
        assert session_tasks.overlaps_busy(str(ROOT / 'a')) and session_tasks.overlaps_busy(str(ROOT / 'a' / 'b' / 'c'))
        assert not session_tasks.overlaps_busy(str(ROOT / 'a' / 'bc'))
    assert not session_tasks.overlaps_busy(str(ROOT))
    print('PASS: a moving folder holds turns, backups, upgrades and overlapping operations; followers made meanwhile follow')


async def worktrees():
    main = ROOT / 'atlas-main'
    main.mkdir()
    (main / 'notes.md').write_text('main\n')
    git(main, 'init', '--quiet')
    git(main, 'add', 'notes.md')
    git(main, 'commit', '--quiet', '-m', 'Start')
    git(main, 'worktree', 'add', '--quiet', '-b', 'inside', '.claude/worktrees/inside')
    outside = ROOT / 'atlas-review'
    git(main, 'worktree', 'add', '--quiet', '-b', 'review', str(outside))
    sid = session('Atlas', main)
    review = session('Atlas review', outside)
    target = ROOT / 'atlas-moved'
    await project_move.move(sid, str(target))
    listing = git(target, 'worktree', 'list', '--porcelain')
    for path in (target, target / '.claude' / 'worktrees' / 'inside', outside):
        assert 'worktree {}\n'.format(path) in listing, listing
        git(path, 'status', '--porcelain')
    assert git(target, 'worktree', 'prune', '--dry-run', '--verbose') == ''
    # The linked worktree moving on its own is found again from the main one.
    moved_review = ROOT / 'atlas-review-2026'
    await project_move.move(review, str(moved_review))
    listing = git(target, 'worktree', 'list', '--porcelain')
    assert 'worktree {}\n'.format(moved_review) in listing and 'worktree {}\n'.format(outside) not in listing
    assert git(moved_review, 'rev-parse', '--abbrev-ref', 'HEAD').strip() == 'review'
    assert git(target, 'worktree', 'prune', '--dry-run', '--verbose') == ''
    for created in (sid, review):
        db.delete_session(created)
    shutil.rmtree(target)
    shutil.rmtree(moved_review)
    print('PASS: Git worktrees inside, outside and moved on their own stay linked')


async def runtimes():
    for build in (build_app, backend_app):
        app = build()
        app.on_startup.clear()
        app.on_shutdown.clear()
        app.cleanup_ctx.clear()
        assert 'project-move' in app['puppy_capabilities'] and 'workspace-move' in app['puppy_capabilities']
        client = TestClient(TestServer(app), headers={'X-Puppy-Token': config.get('auth.api_token')})
        await client.start_server()
        try:
            source = project('harbor')
            sid = session('Harbor', source)
            follower = session('Harbor styles', source / 'src')
            route = '/api/sessions/{}/workspace/move'.format(sid)
            target = ROOT / 'harbor-http'
            for body in ([], {}, {'destination': str(target), 'extra': 1},
                         {'destination': str(target), 'expected_cwd': 5}):
                response = await client.post(route, json=body)
                assert response.status == 400, (body, await response.text())
            response = await client.post(route, json={'destination': str(target),
                                                      'expected_cwd': str(ROOT / 'other')})
            assert response.status == 409 and 'no longer in' in (await response.json())['error']
            # Cancel stops the copy before anything authoritative changes.
            entered = threading.Event()
            copy = project_move._copy_file

            def slow_copy(*args):
                entered.set()
                while True:
                    operations.checkpoint()
                    time.sleep(.01)

            key = 'project-move-' + os.urandom(8).hex()
            with patch.object(project_move, '_same_filesystem', return_value=False), \
                    patch.object(project_move, '_copy_file', slow_copy):
                request = asyncio.create_task(client.post(route, json={'destination': str(target)},
                                                          headers={operations.HEADER: key}))
                while not entered.is_set():
                    await asyncio.sleep(.01)
                cancel = await client.delete('/api/operations/' + key)
                assert cancel.status == 200 and (await cancel.json())['state'] == 'cancelling'
                response = await request
                assert response.status == 409 and (await response.json())['cancelled']
            assert not target.exists() and (source / 'README.md').exists() and not session_tasks.busy()
            assert db.get_session(sid)['cwd'] == str(source) and not moves_of(sid)
            assert copy is project_move._copy_file
            response = await client.post(route, json={'destination': str(target), 'expected_cwd': str(source)})
            assert response.status == 200, await response.text()
            result = await response.json()
            assert result['ok'] and result['session']['cwd'] == str(target) and result['retained'] == ''
            assert sorted(result['moved']) == sorted([sid, follower])
            assert result['session']['workspace_kind'] == 'directory'
            response = await client.post(route, json={'destination': str(ROOT / 'again'),
                                                      'expected_cwd': str(source)})
            assert response.status == 409
            # The scratch promotion keeps its own contract on the same route.
            scratch_path = Path(workspaces.create_temporary())
            (scratch_path / 'file').write_text('scratch')
            scratch = db.create_session('Scratch', 'codex', str(scratch_path), '', '', '', 'workspace-write',
                                        workspace_kind='temporary')
            promoted = ROOT / 'promoted-scratch'
            response = await client.post('/api/sessions/{}/workspace/move'.format(scratch),
                                         json={'destination': str(promoted), 'expected_cwd': str(ROOT)})
            assert response.status == 409 and 'has changed' in (await response.json())['error']
            response = await client.post('/api/sessions/{}/workspace/move'.format(scratch),
                                         json={'destination': str(promoted)})
            assert response.status == 200, await response.text()
            payload = await response.json()
            assert payload['session']['workspace_kind'] == 'directory' and 'moved' not in payload
            for created in (sid, follower, scratch):
                response = await client.delete('/api/sessions/{}'.format(created))
                assert response.status == 200, await response.text()
            assert (target / 'README.md').exists() and (promoted / 'file').exists()
            shutil.rmtree(target)
            shutil.rmtree(promoted)
        finally:
            await client.close()
    print('PASS: both runtimes advertise project-move and serve it, with its compare field, Cancel and the scratch contract')


async def main():
    config.ensure_dirs()
    db.connect()
    await validation()
    await renames()
    await copies()
    await rollback()
    await ownership()
    await worktrees()
    await runtimes()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
