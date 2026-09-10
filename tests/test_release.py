"""Exercise release-note generation with the commit-pinned checkout used by CI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


class ReleaseNotesTest(unittest.TestCase):
    def test_annotations_survive_commit_pinned_shallow_checkout(self):
        workflow = Path(__file__).resolve().parent.parent / ".github/workflows/docker.yml"
        step = workflow.read_text().split("      - name: Generate the notes and create the release\n", 1)[1]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n  # -----", 1)[0])
        script = script.replace("${{ github.repository }}", "example/project")
        annotation = "Fixture release\n\nPreserve `literal` text and $(not-a-command) as data.\n"
        with tempfile.TemporaryDirectory(prefix="ags-release-test-") as tmp:
            root = Path(tmp)
            origin, checkout, binary = (root / name for name in ("origin", "checkout", "bin"))
            for path in (origin, checkout, binary):
                path.mkdir()

            def git(directory, *args):
                result = subprocess.run(["git", "-C", str(directory), *args],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            git(origin, "init", "-q")
            git(origin, "config", "user.name", "Release Test")
            git(origin, "config", "user.email", "ci@localhost")
            git(origin, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "fixture")
            git(origin, "-c", "tag.gpgSign=false", "tag", "-a", "v0.1.0", "-m", annotation)
            git(origin, "-c", "tag.gpgSign=false", "tag", "v0.1.1")
            head = git(origin, "rev-parse", "HEAD")
            git(checkout, "init", "-q")
            git(checkout, "remote", "add", "origin", str(origin))
            gh = binary / "gh"
            gh.write_text("#!" + sys.executable + "\n" + textwrap.dedent("""\
                import json, os, sys
                from pathlib import Path
                if sys.argv[1:3] == ['release', 'view']:
                    raise SystemExit(0 if os.environ['RELEASE_EXISTS'] == 'yes' else 1)
                Path(os.environ['GH_ARGUMENTS']).write_text(json.dumps(sys.argv[1:]))
                """))
            gh.chmod(0o755)
            notes, arguments = root / "notes.md", root / "arguments.json"
            script = script.replace("/tmp/notes.md", str(notes))
            env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
                       REGISTRY="ghcr.io", GH_ARGUMENTS=str(arguments), GIT_TERMINAL_PROMPT="0")
            for tag in ("v0.1.0", "v0.1.1"):
                for exists in ("no", "yes"):
                    with self.subTest(tag=tag, exists=exists):
                        git(checkout, "fetch", "--no-tags", "--depth=1", "origin",
                            "+%s:refs/tags/%s" % (head, tag))
                        self.assertEqual(git(checkout, "cat-file", "-t", tag), "commit")
                        env.update(GITHUB_REF_NAME=tag, RELEASE_EXISTS=exists)
                        result = subprocess.run(["bash", "-c", script], cwd=checkout, env=env,
                                                capture_output=True, text=True, timeout=30)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        body = notes.read_text()
                        self.assertIn("ghcr.io/example/project:" + tag[1:], body)
                        self.assertIn("stable releases update `:latest`", body)
                        if tag == "v0.1.0":
                            self.assertEqual(git(checkout, "cat-file", "-t", tag), "tag")
                            self.assertIn("## Changes", body)
                            self.assertIn(annotation.split("\n\n", 1)[1].strip(), body)
                        else:
                            self.assertNotIn("## Changes", body)
                        args = json.loads(arguments.read_text())
                        self.assertEqual(args[:3], ["release", "create" if exists == "no" else "edit", tag])
                        self.assertEqual("--generate-notes" in args, exists == "no")
                        self.assertEqual("--verify-tag" in args, exists == "no")


if __name__ == "__main__":
    unittest.main()
