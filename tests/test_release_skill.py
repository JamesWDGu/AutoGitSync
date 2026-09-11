"""Keep the tracked release skill discoverable, English-only, and safe to package."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".agents/skills/autogitsync-release"


class ReleaseSkillTest(unittest.TestCase):
    def test_skill_has_valid_identity_and_description(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        header = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(header)
        name = re.search(r"^name: ([a-z0-9-]+)$", header.group(1), re.MULTILINE)
        self.assertIsNotNone(name)
        self.assertEqual(name.group(1), SKILL.name)
        self.assertLessEqual(len(name.group(1)), 64)
        self.assertNotIn("--", name.group(1))
        self.assertFalse(name.group(1).startswith("-"))
        self.assertFalse(name.group(1).endswith("-"))
        description = re.search(r"^description: >-\n((?:  .+\n)+)", header.group(1), re.MULTILINE)
        self.assertIsNotNone(description)
        folded = " ".join(line.strip() for line in description.group(1).splitlines())
        self.assertTrue(folded)
        self.assertLessEqual(len(folded), 1024)
        self.assertNotIn("allowed-tools:", header.group(1))
        self.assertIn("Loading it does not authorize", text)

    def test_skill_and_supporting_documents_are_english_only(self):
        self.assertFalse(list(SKILL.rglob("*.zh*")))
        for path in SKILL.rglob("*.md"):
            with self.subTest(document=str(path.relative_to(SKILL))):
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"[\u3400-\u9fff]")
                self.assertNotIn("\ufffd", text)
                self.assertNotIn("/Users/", text)
                self.assertNotIn("/home/", text)

    def test_shell_examples_have_valid_syntax(self):
        checked = 0
        for path in SKILL.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            for block in re.findall(r"```bash\n(.*?)```", text, re.DOTALL):
                with self.subTest(document=str(path.relative_to(SKILL)), block=checked):
                    result = subprocess.run(["bash", "-n"], input=block, text=True,
                                            capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    checked += 1
        self.assertGreaterEqual(checked, 4)

    def test_message_templates_match_release_conventions(self):
        commit = (SKILL / "assets/commit-message.md").read_text(encoding="utf-8")
        self.assertRegex(commit.splitlines()[0], r"^(feat!|feat|fix|docs|ci): ")
        for section in ("Motivation:", "Impact:", "Validation:", "Release decision:"):
            self.assertIn(section, commit)
        annotation = (SKILL / "assets/tag-message.md").read_text(encoding="utf-8")
        title, body = annotation.split("\n\n", 1)
        self.assertEqual(title, "AutoGitSync X.Y.Z")
        self.assertTrue(body.startswith("- "))
        self.assertNotIn("## Changes", body)  # the workflow supplies this heading
        self.assertIn("### Compatibility and migration", body)
        self.assertIn("### Validation", body)
        self.assertIn("### Publication", body)

    def test_skill_is_excluded_from_the_container_context(self):
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".agents", ignored)
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY app/ /app/", dockerfile)


if __name__ == "__main__":
    unittest.main()
