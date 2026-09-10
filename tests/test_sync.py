"""同步引擎端到端测试 —— 使用真实的 git 仓库（本地裸库，无需网络）。"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
        raise AssertionError("git %s 失败：%s" % (args, proc.stderr))
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

    # -- 工具 ---------------------------------------------------------------
    def make_config(self, **sync_overrides):
        sync = SyncConfig(source=self.source, workdir=self.work, **sync_overrides)
        cfg = Config(git=GitConfig(url=self.remote, branch="main"), sync=sync,
                     server=ServerConfig(listen=""))
        cfg.include_re = re.compile(sync.include)
        cfg.exclude_re = re.compile(sync.exclude) if sync.exclude else None
        return cfg

    def engine(self, cfg=None):
        return GitSync(cfg or self.make_config(), logging.getLogger("test"))

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
        """模拟「别人在同一时间往远端推了东西」。"""
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

    # -- 用例 ---------------------------------------------------------------
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
        self.assertEqual(result.summary, "无变更")
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
        self.push_foreign_commit({"README.md": "文档\n", "notes.txt": "x\n"})

        cfg = self.make_config(include=r"\.conf$")
        self.engine(cfg).sync_once()

        self.assertEqual(sorted(self.remote_files()), ["README.md", "a.conf", "notes.txt"])
        self.assertEqual(self.remote_show("README.md"), "文档\n")

    def test_local_wins_on_conflict(self):
        self.write("a.conf", "local\n")
        engine = self.engine()
        engine.sync_once()
        self.push_foreign_commit({"a.conf": "remote\n", "extra.conf": "remote only\n"})

        engine.sync_once()

        self.assertEqual(self.remote_show("a.conf"), "local\n")     # 冲突以本地为准
        self.assertNotIn("extra.conf", self.remote_files())         # 本地没有的，git 上同步删除
        self.assertIsNotNone(self.remote_head())

    def test_push_race_is_retried_and_local_wins(self):
        self.write("a.conf", "local\n")
        self.write("b.conf", "bee\n")
        cfg = self.make_config()
        engine = self.engine(cfg)
        engine.sync_once()

        test = self

        class RacyEngine(GitSync):
            """在同步的准备阶段插入一次远端推送，制造「推送被拒」的竞态场景。"""

            injected = False

            def _prepare_worktree(self):
                super()._prepare_worktree()
                if not RacyEngine.injected:
                    RacyEngine.injected = True
                    test.push_foreign_commit({"a.conf": "remote wins?\n", "c.conf": "来自远端\n"})

        test.write("a.conf", "local v2\n")
        racy = RacyEngine(cfg, logging.getLogger("test"))
        result = racy.sync_once()

        self.assertTrue(result.ok)                                   # 竞态被自动重试消化
        self.assertEqual(self.remote_show("a.conf"), "local v2\n")   # 冲突仍以本地为准
        self.assertEqual(self.remote_show("b.conf"), "bee\n")
        self.assertNotIn("c.conf", self.remote_files())              # 本地没有的仍被删除
        self.assertEqual(self.remote_commit_count(), 3)              # 远端提交未被丢弃（没有强推）

    def test_remote_tracks_deleted_files_only_when_enabled(self):
        self.write("a.conf", "a\n")
        self.write("b.conf", "b\n")
        cfg = self.make_config(delete_missing=False)
        engine = self.engine(cfg)
        engine.sync_once()

        os.remove(os.path.join(self.source, "b.conf"))
        result = engine.sync_once()

        self.assertTrue(result.ok)
        self.assertIn("b.conf", self.remote_files())                 # delete_missing=false 时保留
        self.assertEqual(result.deleted, [])

    def test_empty_source_is_refused(self):
        self.write("a.conf", "a\n")
        engine = self.engine()
        engine.sync_once()
        os.remove(os.path.join(self.source, "a.conf"))

        with self.assertRaises(SyncError) as ctx:
            engine.sync_once()

        self.assertIn("allow_empty", str(ctx.exception))
        self.assertEqual(self.remote_files(), ["a.conf"])            # 远端未被清空

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

        # 试运行不会污染工作副本：紧接着的正常同步依然生效
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
        self.assertEqual(result.changed, ["a.conf"])    # 试运行不残留暂存内容
        self.assertEqual(result.deleted, [])
        self.assertEqual(self.remote_files(), ["a.conf"])

    def test_token_is_sent_as_http_basic_auth(self):
        """真正验证凭据注入：本地起一个返回 401 的 HTTP 端点，检查 git 发出的请求头。"""
        import base64
        import http.server
        import threading

        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 接口
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

    def test_workdir_reused_across_runs(self):
        self.write("a.conf", "a\n")
        engine = self.engine()
        engine.sync_once()
        self.assertTrue(os.path.isdir(os.path.join(self.work, ".git")))
        # 故意弄脏工作副本，下一轮应被重置
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

        # token 只出现在实际使用的 URL 里，任何输出都要先脱敏
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
