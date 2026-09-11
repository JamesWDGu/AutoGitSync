"""Keep the standard license and public license metadata consistent, offline."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent


class LicenseTest(unittest.TestCase):
    def test_standard_0bsd_grant_and_disclaimer_are_preserved(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "BSD Zero Clause License")
        self.assertRegex(text, r"Copyright \(c\) \d{4} AutoGitSync contributors")
        body = " ".join(text[text.index("Permission to "):].split())
        # SHA-256 of the whitespace-normalized grant/disclaimer from SPDX 0BSD.
        # https://spdx.org/licenses/0BSD.json (copyright owner is project-specific).
        expected = "1fc595850815e2afbaf6b3aa49edaa2fb51a99028bc3a78d0ebe834ead586914"
        self.assertEqual(hashlib.sha256(body.encode("utf-8")).hexdigest(), expected)

    def test_license_metadata_matches_across_public_surfaces(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/docker.yml").read_text(encoding="utf-8")
        skill = (ROOT / ".agents/skills/autogitsync-release/SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'org\.opencontainers\.image\.licenses="([^"]+)"', dockerfile), ["0BSD"])
        labels = re.findall(r"(?m)^          labels: \|\n((?: {12}.+\n)+)", workflow)
        self.assertTrue(labels)
        for block in labels:
            self.assertEqual(re.findall(r"org\.opencontainers\.image\.licenses=(\S+)", block), ["0BSD"])
        self.assertEqual(re.findall(r"(?m)^license: (.+)$", skill), ["0BSD"])
        for name in ("README.md", "README.zh-CN.md", "CONTRIBUTING.md", "CONTRIBUTING.zh-CN.md", "AGENTS.md"):
            with self.subTest(document=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn("[0BSD](LICENSE)", text)

    def test_readmes_keep_the_ai_note_prominent_and_compact(self):
        for name in ("README.md", "README.zh-CN.md"):
            intro = (ROOT / name).read_text(encoding="utf-8").split("\n## ", 1)[0]
            with self.subTest(document=name):
                for marker in ("> [!IMPORTANT]", "100% AI", "[0BSD](LICENSE)",
                               "[AGENTS.md](AGENTS.md)", "[skills](.agents/skills/)",
                               "PR", "issue", "tokens", "star"):
                    self.assertIn(marker, intro)
                self.assertLessEqual(len(intro.splitlines()), 30)


if __name__ == "__main__":
    unittest.main()
