#!/usr/bin/env python3
"""Notes sharing uses real files, with no engine, server or external state."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
from puppy import agent_notes as notes


class SharedNotesTest(unittest.TestCase):
    def setUp(self):
        private = BASE / "data" / "tests"
        private.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="agent-notes-", dir=str(private))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = {"cwd": str(self.root)}
        self.agents = self.root / "AGENTS.md"
        self.claude = self.root / "CLAUDE.md"

    def state(self):
        return notes.describe(self.session)["shared"]

    def save(self, **body):
        body.setdefault("expected_revision", self.state()["revision"])
        notes.write_shared(self.session, body)

    def test_create_edit_split_and_remove(self):
        self.assertTrue(self.state()["available"])
        self.assertFalse(self.state()["linked"])
        self.save(shared=True, text="Instructions\n")
        self.assertEqual(os.readlink(self.claude), "AGENTS.md")
        self.assertEqual(self.claude.read_text(), "Instructions\n")
        self.assertTrue(self.state()["linked"])
        self.agents.chmod(0o600)
        self.save(shared=True, text="Edited\n")
        self.assertEqual(self.agents.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.claude.read_text(), "Edited\n")
        self.save(shared=False, texts={"AGENTS.md": "A", "CLAUDE.md": "C"})
        self.assertFalse(self.claude.is_symlink())
        self.assertEqual(self.claude.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.agents.read_text(), "A")
        self.assertEqual(self.claude.read_text(), "C")
        self.assertFalse(self.state()["available"])
        self.claude.write_text("A")
        self.save(shared=True, text="A")
        self.save(shared=True, delete=True)
        self.assertFalse(os.path.lexists(self.claude))
        self.assertFalse(self.agents.exists())

    def test_empty_equal_and_distinct(self):
        for a, c, allowed in [("A", "", True), ("", "C", True),
                               ("Same", "Same", True), ("A", "C", False),
                               ("A\n", "A", False)]:
            with self.subTest(a=a, c=c):
                self.agents.write_text(a)
                self.claude.write_text(c)
                self.assertEqual(self.state()["available"], allowed)
                if allowed:
                    self.save(shared=True, text=a or c)
                    self.assertEqual(self.claude.read_text(), a or c)
                    self.claude.unlink()
                else:
                    with self.assertRaises(notes.NotesError) as failure:
                        self.save(shared=True, text="replacement")
                    self.assertEqual(failure.exception.status, 409)
                    self.assertEqual(self.agents.read_text(), a)
                    self.assertEqual(self.claude.read_text(), c)

    def test_claude_only_source_keeps_text_and_permissions(self):
        self.claude.write_text("Private instructions")
        self.claude.chmod(0o600)
        self.save(shared=True, text="Private instructions")
        self.assertEqual(self.agents.read_text(), "Private instructions")
        self.assertEqual(self.agents.stat().st_mode & 0o777, 0o600)
        self.assertEqual(os.readlink(self.claude), "AGENTS.md")

    def test_detect_relative_absolute_and_dangling_links(self):
        for target in ["AGENTS.md", "./AGENTS.md", str(self.agents)]:
            with self.subTest(target=target):
                self.claude.symlink_to(target)
                self.assertTrue(self.state()["linked"])
                self.save(shared=True, text="Repaired")
                self.assertEqual(self.claude.read_text(), "Repaired")
                self.claude.unlink()
                self.agents.unlink()

    def test_other_links_and_uneditable_files_not_offered(self):
        self.claude.symlink_to("other.md")
        self.assertFalse(self.state()["available"])
        self.claude.unlink()
        self.agents.symlink_to("CLAUDE.md")
        self.claude.write_text("reverse link")
        self.assertFalse(self.state()["available"])
        self.agents.unlink()
        self.agents.mkdir()
        self.assertFalse(self.state()["available"])
        self.agents.rmdir()
        self.agents.write_text("x" * (notes.MAX_NOTE_BYTES + 1))
        self.assertFalse(self.state()["available"])

    def test_stale_editor_rejected_without_replacing_either_file(self):
        self.agents.write_text("original")
        revision = self.state()["revision"]
        self.claude.write_text("new separate instructions")
        with self.assertRaises(notes.NotesError) as failure:
            self.save(shared=True, text="old draft", expected_revision=revision)
        self.assertEqual(failure.exception.status, 409)
        self.assertEqual(self.agents.read_text(), "original")
        self.assertEqual(self.claude.read_text(), "new separate instructions")

    def test_lossy_decoding_never_makes_unique_files_look_identical(self):
        self.agents.write_bytes(b"\xff")
        self.claude.write_bytes(b"\xfe")
        self.assertFalse(self.state()["available"])
        with self.assertRaises(notes.NotesError):
            self.save(shared=True, text="replacement")
        self.assertEqual(self.agents.read_bytes(), b"\xff")
        self.assertEqual(self.claude.read_bytes(), b"\xfe")

    def test_invalid_or_oversized_input_does_not_change_files(self):
        self.save(shared=True, text="Keep")
        for body in [dict(shared="yes", text="x"), dict(shared=True, text=7),
                     dict(shared=False, texts={"AGENTS.md": "x"}),
                     dict(shared=False, texts={"AGENTS.md": "x", "CLAUDE.md": 7}),
                     dict(shared=True, text="x" * (notes.MAX_NOTE_BYTES + 1))]:
            with self.subTest(body_keys=list(body)):
                with self.assertRaises(notes.NotesError):
                    self.save(**body)
                self.assertTrue(self.claude.is_symlink())
                self.assertEqual(self.agents.read_text(), "Keep")

    def test_replacement_failure_rolls_back(self):
        self.claude.write_text("Only copy")
        real_replace = os.replace

        def fail_claude(source, target):
            if str(target) == str(self.claude):
                raise OSError("replacement failed")
            return real_replace(source, target)

        with patch.object(notes.os, "replace", side_effect=fail_claude):
            with self.assertRaises(notes.NotesError):
                self.save(shared=True, text="New text")
        self.assertFalse(self.agents.exists())
        self.assertFalse(self.claude.is_symlink())
        self.assertEqual(self.claude.read_text(), "Only copy")
        self.save(shared=True, text="Linked original")
        with patch.object(notes.os, "replace", side_effect=fail_claude):
            with self.assertRaises(notes.NotesError):
                self.save(shared=False, texts={"AGENTS.md": "A", "CLAUDE.md": "C"})
        self.assertTrue(self.claude.is_symlink())
        self.assertEqual(self.agents.read_text(), "Linked original")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), list(notes.NOTE_FILES))


if __name__ == "__main__":
    unittest.main()
