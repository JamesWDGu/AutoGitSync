"""Separate public agent conventions from optional, ignored checkout notes."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class AgentNotesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="autogitsync-agent-notes-")
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.git("init", "-q")
        (self.repo / ".gitignore").write_text((ROOT / ".gitignore").read_text(encoding="utf-8"))
        (self.repo / "AGENTS.md").write_text("Shared project conventions\n")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], env=self.env,
                              capture_output=True, text=True, check=True, timeout=10).stdout

    def test_normal_add_includes_public_notes_but_not_private_copies(self):
        names = ("AGENTS.local.md", "AGENTS.local.md.bak", "AGENTS.local.md~",
                 ".AGENTS.local.md.swp", "nested/AGENTS.local.md")
        for name in names:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Local-only fixture, not real developer information\n")
        self.git("add", ".")
        self.assertEqual(set(self.git("ls-files").splitlines()), {".gitignore", "AGENTS.md"})
        self.assertEqual(set(self.git("check-ignore", *names).splitlines()), set(names))

    def test_explicit_private_add_is_rejected_without_force(self):
        (self.repo / "AGENTS.local.md").write_text("Local-only fixture\n")
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("add", "AGENTS.local.md")
        self.assertEqual(self.git("ls-files"), "")

    def test_ignoring_does_not_remove_an_already_indexed_private_file(self):
        (self.repo / "AGENTS.local.md").write_text("Local-only fixture\n")
        self.git("-c", "core.excludesFile=" + os.devnull, "add", "-f", "AGENTS.local.md")
        self.assertEqual(self.git("check-ignore", "--no-index", "AGENTS.local.md").strip(),
                         "AGENTS.local.md")
        self.assertEqual(self.git("ls-files").strip(), "AGENTS.local.md")

    def test_public_instructions_work_without_local_notes(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("[AGENTS.local.md](AGENTS.local.md)", text)
        self.assertIn("Its absence is normal", text)
        self.assertIn("not an automatic import", text)
        self.assertNotRegex(text, r"/(?:Users|home)/[^\s/]+")
        self.assertNotRegex(text, r"[A-Za-z]:\\Users\\")
        self.assertNotRegex(text, r"[\u3400-\u9fff\ufffd]")
        self.assertIn("ghcr.io/jameswdgu/autogitsync", text)
        self.assertFalse((self.repo / "AGENTS.local.md").exists())
        self.git("add", ".")
        self.assertIn("AGENTS.md", self.git("ls-files").splitlines())

    def test_private_notes_and_copies_stay_out_of_the_build_context(self):
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        for pattern in ("AGENTS.md", "AGENTS.local.*", ".AGENTS.local.*",
                        "**/AGENTS.local.*", "**/.AGENTS.local.*"):
            self.assertIn(pattern, ignored)


if __name__ == "__main__":
    unittest.main()
