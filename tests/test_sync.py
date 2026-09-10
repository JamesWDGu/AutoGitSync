"""End-to-end tests for the sync engine, using real (local) git repositories."""

import contextlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from git_sync import (Config, GitConfig, GitError, GitSync, ServerConfig,  # noqa: E402
                      SyncConfig, SyncError)

logging.getLogger("autogitsync").setLevel(logging.CRITICAL)
logging.getLogger("test").setLevel(logging.CRITICAL)


def git(args, cwd=None, check=True, text=True):
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "tester", "GIT_AUTHOR_EMAIL": "tester@localhost",
        "GIT_COMMITTER_NAME": "tester", "GIT_COMMITTER_EMAIL": "tester@localhost",
        "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C",
    })
    proc = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=text, env=env)
    if check and proc.returncode != 0:
        raise AssertionError("git %s failed: %s" % (args, proc.stderr))
    return proc


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ags-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.remote = os.path.join(self.tmp, "remote.git")
        git(["init", "--bare", "--quiet", "--initial-branch=main", self.remote])
        self.source = os.path.join(self.tmp, "source")
        os.makedirs(self.source)
        self.work = os.path.join(self.tmp, "work")

    # -- helpers ------------------------------------------------------------
    def make_config(self, **sync_overrides):
        values = {"source": self.source, "workdir": self.work}
        values.update(sync_overrides)
        sync = SyncConfig(**values)
        cfg = Config(git=GitConfig(url=self.remote, branch="main"), sync=sync,
                     server=ServerConfig(listen=""))
        cfg.include_re = re.compile(sync.include)
        cfg.exclude_re = re.compile(sync.exclude) if sync.exclude else None
        return cfg

    def engine(self, cfg=None):
        return GitSync(cfg or self.make_config(), logging.getLogger("test"))

    @contextlib.contextmanager
    def capture_logs(self, level=logging.DEBUG):
        """Collect engine logs (Python 3.9 has no assertNoLogs, so attach a handler)."""
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        logger = logging.getLogger("test")
        previous = logger.level
        logger.addHandler(handler)
        logger.setLevel(level)
        try:
            yield records
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)

    @staticmethod
    def warnings_of(records):
        return [r.getMessage() for r in records if r.levelno >= logging.WARNING]

    def write(self, relpath, content, mode=None):
        abspath = os.path.join(self.source, relpath)
        os.makedirs(os.path.dirname(abspath), exist_ok=True)
        with open(abspath, "wb") as handle:
            handle.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        if mode is not None:
            os.chmod(abspath, mode)
        return abspath

    def remote_files(self, branch="main"):
        proc = git(["--git-dir", self.remote, "ls-tree", "-r", "--name-only", branch], check=False)
        if proc.returncode != 0:
            return []
        return [line for line in proc.stdout.splitlines() if line]

    def remote_show(self, path, branch="main"):
        return git(["--git-dir", self.remote, "show", "%s:%s" % (branch, path)]).stdout

    def remote_show_bytes(self, path, branch="main"):
        return git(["--git-dir", self.remote, "show", "%s:%s" % (branch, path)], text=False).stdout

    def remote_head(self, branch="main"):
        proc = git(["--git-dir", self.remote, "rev-parse", branch], check=False)
        return proc.stdout.strip() if proc.returncode == 0 else None

    def remote_commit_count(self, branch="main"):
        return int(git(["--git-dir", self.remote, "rev-list", "--count", branch]).stdout.strip())

    def push_foreign_commit(self, files, message="foreign change"):
        """Simulate someone else pushing to the remote at the same time."""
        clone = tempfile.mkdtemp(prefix="ags-foreign-", dir=self.tmp)
        git(["clone", "--quiet", self.remote, clone])
        for relpath, content in files.items():
            target = os.path.join(clone, relpath)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(content)
        git(["add", "-A"], cwd=clone)
        git(["commit", "--quiet", "-m", message], cwd=clone)
        git(["push", "--quiet", "origin", "HEAD:refs/heads/main"], cwd=clone)
        shutil.rmtree(clone, ignore_errors=True)

    # -- tests --------------------------------------------------------------
    def test_first_sync_creates_branch_and_files(self):
        self.write("a.conf", "hello\n")
        self.write("sub/b.yaml", "b: 1\n")
        result = self.engine().sync_once()

        self.assertTrue(result.ok)
        self.assertEqual(result.commit is not None, True)
        self.assertEqual(sorted(self.remote_files()), ["a.conf", "sub/b.yaml"])
        self.assertEqual(self.remote_show("a.conf"), "hello\n")
        self.assertEqual(self.remote_show("sub/b.yaml"), "b: 1\n")

    def test_second_sync_is_noop(self):
        self.write("a.conf", "hello\n")
        engine = self.engine()
        engine.sync_once()
        before = self.remote_head()

        result = engine.sync_once()
        self.assertTrue(result.ok)
        self.assertIsNone(result.commit)
        self.assertEqual(result.summary, "no changes")
        self.assertEqual(self.remote_head(), before)
        self.assertEqual(self.remote_commit_count(), 1)

    def test_update_and_delete(self):
        self.write("keep.conf", "v1\n")
        self.write("gone.conf", "bye\n")
        engine = self.engine()
        engine.sync_once()

        self.write("keep.conf", "v2\n")
        os.remove(os.path.join(self.source, "gone.conf"))
        result = engine.sync_once()

        self.assertTrue(result.ok)
        self.assertEqual(result.changed, ["keep.conf"])
        self.assertEqual(result.deleted, ["gone.conf"])
        self.assertEqual(self.remote_files(), ["keep.conf"])
        self.assertEqual(self.remote_show("keep.conf"), "v2\n")

    def test_untouched_files_outside_include_are_preserved(self):
        self.write("a.conf", "hello\n")
        self.engine().sync_once()
        self.push_foreign_commit({"README.md": "docs\n", "notes.txt": "x\n"})

        cfg = self.make_config(include=r"\.conf$")
        self.engine(cfg).sync_once()

        self.assertEqual(sorted(self.remote_files()), ["README.md", "a.conf", "notes.txt"])
        self.assertEqual(self.remote_show("README.md"), "docs\n")

    def test_local_wins_on_conflict(self):
        self.write("a.conf", "local\n")
        engine = self.engine()
        engine.sync_once()
        self.push_foreign_commit({"a.conf": "remote\n", "extra.conf": "remote only\n"})

        engine.sync_once()

        self.assertEqual(self.remote_show("a.conf"), "local\n")     # local wins on conflicts
        self.assertNotIn("extra.conf", self.remote_files())         # gone locally -> deleted from git
        self.assertIsNotNone(self.remote_head())

    def test_push_race_is_retried_and_local_wins(self):
        self.write("a.conf", "local\n")
        self.write("b.conf", "bee\n")
        cfg = self.make_config()
        engine = self.engine(cfg)
        engine.sync_once()

        test = self

        class RacyEngine(GitSync):
            """Push to the remote between fetch and push to create a rejected-push race."""

            injected = False

            def _prepare_worktree(self):
                super()._prepare_worktree()
                if not RacyEngine.injected:
                    RacyEngine.injected = True
                    test.push_foreign_commit({"a.conf": "remote wins?\n", "c.conf": "from the remote\n"})

        test.write("a.conf", "local v2\n")
        racy = RacyEngine(cfg, logging.getLogger("test"))
        result = racy.sync_once()

        self.assertTrue(result.ok)                                   # the race was retried away
        self.assertEqual(self.remote_show("a.conf"), "local v2\n")   # local still wins
        self.assertEqual(self.remote_show("b.conf"), "bee\n")
        self.assertNotIn("c.conf", self.remote_files())              # still deleted locally-missing file
        self.assertEqual(self.remote_commit_count(), 3)              # no force push, remote commit kept

    def test_remote_tracks_deleted_files_only_when_enabled(self):
        self.write("a.conf", "a\n")
        self.write("b.conf", "b\n")
        cfg = self.make_config(delete_missing=False)
        engine = self.engine(cfg)
        engine.sync_once()

        os.remove(os.path.join(self.source, "b.conf"))
        result = engine.sync_once()

        self.assertTrue(result.ok)
        self.assertIn("b.conf", self.remote_files())                 # kept when DELETE_MISSING=false
        self.assertEqual(result.deleted, [])

    def test_empty_source_is_refused(self):
        self.write("a.conf", "a\n")
        engine = self.engine()
        engine.sync_once()
        os.remove(os.path.join(self.source, "a.conf"))

        with self.assertRaises(SyncError) as ctx:
            engine.sync_once()

        self.assertIn("ALLOW_EMPTY", str(ctx.exception))
        self.assertEqual(self.remote_files(), ["a.conf"])            # the remote was not wiped

    def test_empty_source_allowed_when_configured(self):
        self.write("a.conf", "a\n")
        cfg = self.make_config(allow_empty=True)
        engine = self.engine(cfg)
        engine.sync_once()
        os.remove(os.path.join(self.source, "a.conf"))

        result = engine.sync_once()
        self.assertTrue(result.ok)
        self.assertEqual(self.remote_files(), [])

    def test_dir_replaced_by_file_and_back(self):
        self.write("nested/a.conf", "inside\n")
        engine = self.engine()
        engine.sync_once()
        self.assertEqual(self.remote_files(), ["nested/a.conf"])

        shutil.rmtree(os.path.join(self.source, "nested"))
        self.write("nested", "now a file\n")
        engine.sync_once()
        self.assertEqual(self.remote_files(), ["nested"])
        self.assertEqual(self.remote_show("nested"), "now a file\n")

        os.remove(os.path.join(self.source, "nested"))
        self.write("nested/a.conf", "inside again\n")
        engine.sync_once()
        self.assertEqual(self.remote_files(), ["nested/a.conf"])

    def test_include_and_exclude(self):
        self.write("app/settings.conf", "1\n")
        self.write("app/settings.bak", "2\n")
        self.write("cache/tmp.conf", "3\n")
        self.write("readme.md", "4\n")
        cfg = self.make_config(include=r"\.(conf|md)$", exclude=r"^cache/")
        self.engine(cfg).sync_once()

        self.assertEqual(sorted(self.remote_files()), ["app/settings.conf", "readme.md"])

    def test_binary_content_roundtrip(self):
        payload = bytes(range(256)) * 4
        self.write("blob.bin", payload)
        cfg = self.make_config(include=r"\.bin$")
        self.engine(cfg).sync_once()
        self.assertEqual(self.remote_show_bytes("blob.bin"), payload)

    def test_executable_bit_is_synced(self):
        self.write("run.sh", "#!/bin/sh\necho hi\n", mode=0o755)
        cfg = self.make_config(include=r"\.sh$")
        self.engine(cfg).sync_once()
        mode = git(["--git-dir", self.remote, "ls-tree", "main", "run.sh"]).stdout.split()[0]
        self.assertEqual(mode, "100755")

    def test_dry_run_changes_nothing(self):
        self.write("a.conf", "a\n")
        cfg = self.make_config()
        engine = self.engine(cfg)
        engine.sync_once()
        head = self.remote_head()

        self.write("a.conf", "changed\n")
        self.write("new.conf", "new\n")
        result = engine.sync_once(dry_run=True)

        self.assertTrue(result.ok)
        self.assertTrue(result.dry_run)
        self.assertEqual(self.remote_head(), head)
        self.assertEqual(self.remote_show("a.conf"), "a\n")
        self.assertEqual(self.remote_files(), ["a.conf"])
        self.assertIn("new.conf", result.detail)

        # A dry run must not pollute the work copy: the next real sync still works
        result = engine.sync_once()
        self.assertTrue(result.ok)
        self.assertEqual(self.remote_show("a.conf"), "changed\n")
        self.assertEqual(sorted(self.remote_files()), ["a.conf", "new.conf"])

    def test_dry_run_on_empty_remote_leaves_nothing_behind(self):
        self.write("a.conf", "a\n")
        engine = self.engine()
        dry = engine.sync_once(dry_run=True)
        self.assertTrue(dry.dry_run)
        self.assertEqual(self.remote_files(), [])

        result = engine.sync_once()
        self.assertEqual(result.changed, ["a.conf"])    # no staged leftovers from the dry run
        self.assertEqual(result.deleted, [])
        self.assertEqual(self.remote_files(), ["a.conf"])

    def test_token_is_sent_as_http_basic_auth(self):
        """Verify credential injection for real: a local endpoint answers 401 and we inspect
        the request headers git actually sends."""
        import base64
        import http.server
        import threading

        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                seen.append(self.headers.get("Authorization", ""))
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="git"')
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        self.write("a.conf", "a\n")
        cfg = self.make_config()
        cfg.git.url = "http://127.0.0.1:%d/repo.git" % server.server_address[1]
        cfg.git.token = "tok-123"
        cfg.git.username = "x-access-token"
        with self.assertRaises(GitError):
            self.engine(cfg).sync_once()

        expected = "Basic " + base64.b64encode(b"x-access-token:tok-123").decode()
        self.assertIn(expected, seen)

    def test_dotenv_and_compose_in_subdirs_are_synced(self):
        """compose.yaml / .env in subdirectories: the regex requires a subdirectory and hidden
        files are not skipped."""
        self.write("compose.yaml", "root: must not be synced\n")  # root: not matched
        self.write("svc-a/compose.yaml", "a: 1\n")
        self.write("svc-a/.env", "TOKEN=a\n")
        self.write("svc-b/.env.local", "DEBUG=1\n")
        self.write("infra/db/compose.yml", "db: 1\n")
        self.write("svc-a/README.md", "must not be synced\n")

        cfg = self.make_config(include=r"^[^/]+/(?:.*/)?(?:compose\.ya?ml|\.env(?:\.[^/]+)?)$")
        engine = self.engine(cfg)
        with self.capture_logs() as records:
            result = engine.sync_once()

        self.assertTrue(result.ok)
        self.assertEqual(sorted(self.remote_files()),
                         ["infra/db/compose.yml", "svc-a/.env", "svc-a/compose.yaml",
                          "svc-b/.env.local"])
        self.assertEqual(self.remote_show("svc-a/.env"), "TOKEN=a\n")
        self.assertEqual(self.warnings_of(records), [])

    def test_files_ignored_by_target_repo_are_reported(self):
        """A .gitignore in the target repository makes git skip managed files silently - that
        must produce a warning."""
        self.write("svc-a/.env", "TOKEN=a\n")
        self.write("svc-a/compose.yaml", "a: 1\n")
        # the remote ships a .gitignore that ignores .env (very common in templates)
        self.push_foreign_commit({".gitignore": ".env\n"})

        cfg = self.make_config(include=r"^[^/]+/(?:.*/)?(?:compose\.ya?ml|\.env(?:\.[^/]+)?)$")
        engine = self.engine(cfg)
        with self.capture_logs() as records:
            result = engine.sync_once()

        self.assertTrue(result.ok)
        self.assertIn("svc-a/compose.yaml", self.remote_files())
        self.assertNotIn("svc-a/.env", self.remote_files())      # git skipped it silently
        warnings = self.warnings_of(records)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("svc-a/.env", warnings[0])
        self.assertIn("gitignore", warnings[0])
        # counts must not lie: only files that really land in the commit count
        self.assertEqual(result.changed, ["svc-a/compose.yaml"])
        self.assertEqual(result.deleted, [])
        self.assertIn("1 file(s) changed", git(
            ["--git-dir", self.remote, "log", "-1", "--format=%s", "main"]).stdout)

    def test_workdir_inside_source_is_skipped(self):
        """Overlapping mounts on the host (SOURCE_DIR can see the work copy) must not make the
        service copy itself into the repository.

        Without the guard every run nests one more level of data/repo/... forever.
        """
        workdir = os.path.join(self.source, "data", "repo")
        os.makedirs(workdir)
        link = os.path.join(self.tmp, "repo-link")     # not inside source textually, but physically
        os.symlink(workdir, link)
        self.write("a.conf", "a\n")

        cfg = self.make_config(workdir=link, include=r"\.conf$")
        engine = self.engine(cfg)
        with self.capture_logs() as records:
            for _ in range(3):
                engine.sync_once()

        self.assertEqual(sorted(self.remote_files()), ["a.conf"])
        self.assertTrue(any("overlap" in text for text in self.warnings_of(records)),
                        self.warnings_of(records))

    def test_source_equal_to_workdir_is_rejected(self):
        link = os.path.join(self.tmp, "self-link")
        os.symlink(self.source, link)
        cfg = self.make_config(workdir=link)
        with self.assertRaises(SyncError) as ctx:
            self.engine(cfg).scan_source()
        self.assertIn("same directory", str(ctx.exception))

    def test_force_push_latest_1_keeps_only_the_newest_commit(self):
        """FORCE_PUSH_LATEST=1: the remote holds a single commit and deleted content is gone
        from the history."""
        self.write("a.conf", "v1\n")
        self.write("secret.env", "SECRET=1\n")
        cfg = self.make_config(force_push_latest=1)
        engine = self.engine(cfg)

        engine.sync_once()
        self.assertEqual(self.remote_commit_count(), 1)
        self.assertEqual(sorted(self.remote_files()), ["a.conf", "secret.env"])

        self.write("a.conf", "v2\n")
        engine.sync_once()
        self.assertEqual(self.remote_commit_count(), 1)          # still a single commit
        self.assertEqual(self.remote_show("a.conf"), "v2\n")

        # delete the sensitive file: it must be unreachable from the branch history
        os.remove(os.path.join(self.source, "secret.env"))
        engine.sync_once()
        self.assertEqual(self.remote_commit_count(), 1)
        self.assertEqual(self.remote_files(), ["a.conf"])
        self.assertNotIn("secret.env", git(["--git-dir", self.remote, "log", "--all",
                                            "--name-only", "--format="]).stdout)
        self.assertNotIn("SECRET=1", git(["--git-dir", self.remote, "log", "--all", "-p"]).stdout)

    def test_force_push_latest_n_keeps_the_last_n_commits(self):
        """FORCE_PUSH_LATEST=3: history depth is capped at 3, older states are dropped."""
        cfg = self.make_config(force_push_latest=3)
        engine = self.engine(cfg)
        for version in range(1, 6):                              # 5 different contents -> 5 commits
            self.write("a.conf", "v%d\n" % version)
            engine.sync_once()

        self.assertEqual(self.remote_commit_count(), 3)
        self.assertEqual(self.remote_show("a.conf"), "v5\n")

        log = git(["--git-dir", self.remote, "log", "--all", "-p"]).stdout
        for kept in ("v3", "v4", "v5"):
            self.assertIn(kept, log)                             # the last 3 states are still there
        for dropped in ("v1", "v2"):
            self.assertNotIn(dropped, log)                       # older ones were truncated

        # the oldest kept commit is a root commit (no parent)
        oldest = git(["--git-dir", self.remote, "rev-list", "--max-parents=0", "main"]).stdout.split()
        self.assertEqual(len(oldest), 1)
        self.assertEqual(git(["--git-dir", self.remote, "rev-list", "--count", "main"]).stdout.strip(), "3")

    def test_force_push_latest_keeps_commit_dates(self):
        """Truncating keeps the original commit dates instead of stamping them all with "now"."""
        cfg = self.make_config(force_push_latest=2)
        engine = self.engine(cfg)
        for version in (1, 2, 3):
            self.write("a.conf", "v%d\n" % version)
            engine.sync_once()
            time.sleep(1.1)                          # git commit dates have one second resolution
        dates = git(["--git-dir", self.remote, "log", "--format=%aI", "main"]).stdout.split()
        self.assertEqual(len(dates), 2)
        self.assertEqual(len(set(dates)), 2)         # the two kept commits have distinct dates

    def test_default_keeps_history(self):
        """Default (FORCE_PUSH_LATEST=0) never force-pushes: history accumulates - the control
        case for the tests above."""
        self.write("keep.conf", "k\n")
        self.write("secret.env", "SECRET=1\n")
        engine = self.engine()
        engine.sync_once()
        os.remove(os.path.join(self.source, "secret.env"))
        engine.sync_once()

        self.assertEqual(self.remote_commit_count(), 2)
        self.assertNotIn("secret.env", self.remote_files())      # the file is gone...
        self.assertIn("secret.env", git(["--git-dir", self.remote, "log", "--all",
                                         "--name-only", "--format="]).stdout)  # ...but the history keeps it

    def test_workdir_reused_across_runs(self):
        self.write("a.conf", "a\n")
        engine = self.engine()
        engine.sync_once()
        self.assertTrue(os.path.isdir(os.path.join(self.work, ".git")))
        # deliberately dirty the work copy: the next run must reset it
        with open(os.path.join(self.work, "junk.txt"), "w", encoding="utf-8") as handle:
            handle.write("junk")
        engine.sync_once()
        self.assertFalse(os.path.exists(os.path.join(self.work, "junk.txt")))

    def test_token_is_never_leaked_in_errors(self):
        self.write("a.conf", "a\n")
        cfg = self.make_config()
        cfg.git.url = "http://127.0.0.1:9/repo.git"
        cfg.git.token = "sup3r-s3cret-token"
        engine = self.engine(cfg)

        # the token only lives in the URL actually used, so every output must be redacted
        self.assertIn("sup3r-s3cret-token", engine.auth_url)
        self.assertNotIn("sup3r-s3cret-token", engine._redact(engine.auth_url))
        self.assertIn("***", engine._redact(engine.auth_url))

        with self.assertRaises(GitError) as ctx:
            engine.sync_once()
        self.assertNotIn("sup3r-s3cret-token", str(ctx.exception))

    def test_non_git_workdir_with_content_is_rejected(self):
        self.write("a.conf", "a\n")
        os.makedirs(self.work)
        with open(os.path.join(self.work, "stray.txt"), "w", encoding="utf-8") as handle:
            handle.write("x")
        with self.assertRaises(SyncError):
            self.engine().sync_once()


if __name__ == "__main__":
    unittest.main()
