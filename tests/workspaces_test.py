#!/usr/bin/env python3
"""Persistent managed workspace paths, restart survival and deletion boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tests.scratch import private_root
from puppy import config, db, workspaces


class WorkspacesTest(unittest.TestCase):
    def setUp(self):
        self.root = private_root("workspaces-")
        self.data = self.root / "configured data"
        self.override = mock.patch.object(config, "DATA_DIR", str(self.data))
        self.override.start()

    def tearDown(self):
        self.override.stop()
        shutil.rmtree(self.root)

    def session(self, path, **fields):
        return dict(id=1, cwd=str(path), workspace_kind="temporary", **fields)

    def test_data_directory_and_restart_survival(self):
        # Creating and reopening a workspace must not consult OS temp storage.
        with mock.patch("tempfile.gettempdir", side_effect=AssertionError("OS temp used")):
            path = Path(workspaces.create_temporary())
        self.assertEqual(path.parent, self.data / "workspaces")
        self.assertTrue(path.is_absolute())
        for directory in (self.data, path.parent, path):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        (path / "keep.txt").write_text("survives restart")
        session = self.session(path, native_session_id="keep-native-context")
        environment = dict(os.environ, PUPPY_DATA=str(self.data),
                           PYTHONPATH=str(BASE), TMPDIR=str(self.root / "absent-temp"))
        output = subprocess.check_output([
            sys.executable, "-c",
            "import json,sys; from puppy import workspaces; "
            "session,reset=workspaces.ensure_session(json.loads(sys.argv[1])); "
            "print(json.dumps([session,reset]))", json.dumps(session),
        ], env=environment, cwd=str(self.root), text=True)
        self.assertEqual(json.loads(output), [session, False])
        self.assertEqual((path / "keep.txt").read_text(), "survives restart")
        with mock.patch.object(config, "DATA_DIR", str(self.root / "another install")):
            other = Path(workspaces.create_temporary())
            self.assertEqual(other.parent, self.root / "another install" / "workspaces")
            self.assertFalse(workspaces.is_available(session))
        self.assertTrue(workspaces.is_available(session))

    def test_deleting_last_workspace_keeps_data_directory(self):
        path = Path(workspaces.create_temporary())
        self.assertTrue(workspaces.remove_temporary(self.session(path)))
        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())
        self.assertTrue(self.data.is_dir())
        self.assertFalse(workspaces.remove_temporary(self.session(path)))
        self.assertTrue(self.data.is_dir())

    def test_orphans_stay_inside_managed_namespace(self):
        live = Path(workspaces.create_temporary())
        archived = Path(workspaces.create_temporary())
        orphan = Path(workspaces.create_temporary())
        unrelated = live.parent / "operator-files"
        unrelated.mkdir()
        (unrelated / "keep").write_text("unrelated")
        uploads = self.data / "uploads"
        uploads.mkdir()
        (uploads / "keep").write_text("attachment")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep").write_text("external")
        link = live.parent / "session-link"
        link.symlink_to(outside, target_is_directory=True)
        live.chmod(0o755)
        with mock.patch.object(db, "list_sessions", return_value=[
                self.session(live), self.session(archived, archived=True)]) as listing:
            self.assertEqual(workspaces.cleanup_orphans(), 1)
            listing.assert_called_once_with(include_archived=True)
        self.assertTrue(live.is_dir() and archived.is_dir())
        self.assertEqual(live.stat().st_mode & 0o777, 0o700)
        self.assertFalse(orphan.exists())
        self.assertTrue(link.is_symlink())
        self.assertEqual((outside / "keep").read_text(), "external")
        self.assertEqual((uploads / "keep").read_text(), "attachment")
        self.assertEqual((unrelated / "keep").read_text(), "unrelated")

    def test_unmanaged_paths_and_ownership_are_rejected(self):
        path = Path(workspaces.create_temporary())
        outside = self.root / "session-outside"
        outside.mkdir()
        (outside / "keep").write_text("external")
        plain = path.parent / "ordinary-project"
        plain.mkdir()
        for invalid in (outside, plain, path / "session-nested"):
            with self.assertRaises(workspaces.WorkspaceError):
                workspaces.remove_temporary(self.session(invalid))
        ordinary = dict(id=2, cwd=str(path), workspace_kind="directory")
        self.assertFalse(workspaces.remove_temporary(ordinary))
        self.assertTrue(path.exists())
        with mock.patch.object(workspaces, "_uid", return_value=os.geteuid() + 1):
            self.assertFalse(workspaces.is_available(self.session(path)))
            with self.assertRaises(workspaces.WorkspaceError):
                workspaces.remove_temporary(self.session(path))
            with self.assertRaises(workspaces.WorkspaceError):
                workspaces.create_temporary()
        self.assertEqual((outside / "keep").read_text(), "external")

    def test_symlinked_workspace_root_never_follows_target(self):
        path = Path(workspaces.create_temporary())
        session = self.session(path)
        (path / "keep").write_text("external after root replacement")
        moved = self.root / "moved-workspaces"
        path.parent.rename(moved)
        path.parent.symlink_to(moved, target_is_directory=True)
        self.assertFalse(workspaces.is_available(session))
        with self.assertRaises(workspaces.WorkspaceError):
            workspaces.remove_temporary(session)
        with self.assertRaises(workspaces.WorkspaceError):
            workspaces.create_temporary()
        self.assertEqual(workspaces.cleanup_orphans(), 0)
        self.assertEqual((moved / path.name / "keep").read_text(),
                         "external after root replacement")


if __name__ == "__main__":
    unittest.main()
