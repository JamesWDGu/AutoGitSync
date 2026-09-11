"""Pin release-only publication and execute its gate against local bare remotes."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/docker.yml"
TAG_CONDITION = "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"


def job_text(name):
    text = WORKFLOW.read_text(encoding="utf-8")
    return re.search(r"(?ms)^  " + re.escape(name) + r":\n(.*?)(?=^  [a-z][\w-]*:|\Z)", text).group(1)


class ReleasePolicyTest(unittest.TestCase):
    def test_branch_and_manual_runs_cannot_publish(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("    branches: [main, release]\n", text)
        self.assertIn('    tags: ["v*"]\n', text)
        self.assertIn("  workflow_dispatch:\n\n", text)
        self.assertNotIn("inputs.publish", text)
        for name in ("publish", "release-gate"):
            with self.subTest(job=name):
                self.assertEqual(re.findall(r"(?m)^    if: (.+)$", job_text(name)), [TAG_CONDITION])
        self.assertIn("    needs: [test, smoke, release-gate]\n", job_text("publish"))
        self.assertIn("    needs: test\n", job_text("smoke"))
        for name in ("release", "dockerhub"):
            self.assertIn("    needs: publish\n", job_text(name))
        self.assertIn("    branches: [main, release]\n",
                      (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))

    def test_mirrors_use_the_same_stable_tags_without_branch_images(self):
        blocks = []
        for name in ("publish", "dockerhub"):
            tags = re.search(r"(?m)^          tags: \|\n((?: {12}.+\n)+)", job_text(name)).group(1)
            blocks.append(tags)
            self.assertNotIn("type=ref,event=branch", tags)
            self.assertNotIn("is_default_branch", tags)
            self.assertIn("type=raw,value=latest\n", tags)
            self.assertIn("type=semver,pattern={{version}}\n", tags)
            self.assertIn("type=semver,pattern={{major}}.{{minor}}\n", tags)
        self.assertEqual(blocks[0], blocks[1])

    def test_public_guidance_describes_release_branch_publication(self):
        names = ("README.md", "README.zh-CN.md", "CONTRIBUTING.md", "CONTRIBUTING.zh-CN.md",
                 "AGENTS.md", ".agents/skills/autogitsync-release/SKILL.md",
                 ".agents/skills/autogitsync-release/references/release-checklist.md")
        for name in names:
            with self.subTest(document=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn("`release`", text)
                self.assertNotIn("Both `main` builds", text)
                self.assertNotIn("main push still", text)
                self.assertNotIn("main-branch event", text)


class ReleaseGateTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ags-release-gate-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.origin, self.source, self.checkout = (self.root / name for name in ("origin.git", "source", "checkout"))
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_TERMINAL_PROMPT="0", AUTOGITSYNC_VERSION="must-not-override-source")
        self.git(self.root, "init", "--bare", "-q", str(self.origin))
        self.git(self.root, "init", "-q", str(self.source))
        self.git(self.source, "config", "user.name", "Release Test")
        self.git(self.source, "config", "user.email", "ci@localhost")
        self.git(self.source, "remote", "add", "origin", str(self.origin))
        (self.source / "app").mkdir()
        (self.source / "app/main.py").write_text(
            'import os\nprint("AutoGitSync " + (os.environ.get("AUTOGITSYNC_VERSION") or "0.1.0"))\n',
            encoding="utf-8")
        self.git(self.source, "add", "app/main.py")
        self.commit()
        self.sha = self.git(self.source, "rev-parse", "HEAD")
        self.git(self.source, "push", "origin", "HEAD:refs/heads/release")
        self.git(self.root, "init", "-q", str(self.checkout))
        self.git(self.checkout, "remote", "add", "origin", str(self.origin))
        binary = self.root / "bin"
        binary.mkdir()
        (binary / "python").symlink_to(sys.executable)
        self.env["PATH"] = str(binary) + os.pathsep + os.environ["PATH"]
        step = job_text("release-gate").split("        run: |\n", 1)[1]
        self.script = textwrap.dedent(step.split("\n  # ---", 1)[0])

    def git(self, directory, *args):
        return subprocess.run(["git", "-C", str(directory), *args], env=self.env,
                              capture_output=True, text=True, check=True, timeout=20).stdout.strip()

    def commit(self):
        self.git(self.source, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "fixture")

    def prepare(self, tag="v0.1.0", annotated=True):
        args = ["-a", tag, "-m", "Fixture release"] if annotated else [tag]
        self.git(self.source, "-c", "tag.gpgSign=false", "tag", *args)
        self.git(self.source, "push", "origin", "refs/tags/" + tag)
        # Reproduce checkout's commit-pinned shallow tag ref, not a normal clone.
        self.git(self.checkout, "fetch", "--no-tags", "--depth=1", "origin",
                 "+%s:refs/tags/%s" % (self.sha, tag))
        self.git(self.checkout, "checkout", "--detach", "-q", self.sha)
        self.env.update(GITHUB_REF_NAME=tag, GITHUB_SHA=self.sha)

    def run_gate(self, success, message=""):
        result = subprocess.run(["bash", "-c", self.script], cwd=self.checkout, env=self.env,
                                capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        if success:
            self.assertEqual(result.returncode, 0, output)
        else:
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn(message, output)

    def test_valid_release_repairs_shallow_tag_and_ignores_version_override(self):
        self.prepare()
        self.assertEqual(self.git(self.checkout, "cat-file", "-t", "refs/tags/v0.1.0"), "commit")
        self.run_gate(True)
        self.assertEqual(self.git(self.checkout, "cat-file", "-t", "refs/tags/v0.1.0"), "tag")

    def test_tag_on_other_branch_is_rejected(self):
        self.commit()
        self.sha = self.git(self.source, "rev-parse", "HEAD")
        self.prepare()
        self.run_gate(False, "release branch tip must match")

    def test_old_release_commit_is_rejected_after_branch_advances(self):
        self.prepare()
        self.commit()
        self.git(self.source, "push", "origin", "HEAD:refs/heads/release")
        self.run_gate(False, "release branch tip must match")

    def test_missing_release_branch_fails_closed(self):
        self.prepare()
        self.git(self.origin, "update-ref", "-d", "refs/heads/release")
        self.run_gate(False, "refs/heads/release")

    def test_missing_remote_tag_cannot_use_checkout_tag(self):
        self.prepare()
        self.git(self.origin, "update-ref", "-d", "refs/tags/v0.1.0")
        self.run_gate(False, "refs/tags/v0.1.0")

    def test_lightweight_tag_is_rejected(self):
        self.prepare(annotated=False)
        self.run_gate(False, "release tags must be annotated")

    def test_source_version_mismatch_is_rejected(self):
        self.prepare(tag="v0.1.1")
        self.run_gate(False, "source version must match")

    def test_event_commit_mismatch_is_rejected(self):
        self.prepare()
        self.env["GITHUB_SHA"] = "0" * 40
        self.run_gate(False, "must match the event commit")

    def test_remote_tag_move_cannot_publish_a_different_event_commit(self):
        self.prepare()
        self.commit()
        self.git(self.source, "-c", "tag.gpgSign=false", "tag", "-af", "v0.1.0", "-m", "Moved fixture")
        self.git(self.source, "push", "--force", "origin", "refs/tags/v0.1.0")
        self.run_gate(False, "must match the event commit")

    def test_noncanonical_and_prerelease_names_fail_before_fetch(self):
        for tag in ("v01.1.0", "v1.02.0", "v1.0.03", "v1.2", "v1.2.3-rc.1", "v1.2.3+build", "vnext"):
            with self.subTest(tag=tag):
                self.env.update(GITHUB_REF_NAME=tag, GITHUB_SHA=self.sha)
                self.run_gate(False, "canonical vMAJOR.MINOR.PATCH syntax")


if __name__ == "__main__":
    unittest.main()
