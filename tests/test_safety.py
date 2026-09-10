"""Regression tests for filesystem boundaries, locking, and history-only syncs."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
import unittest

from tests import test_sync as fixtures

import main as main_module
from git_sync import SyncError


class SyncSafetyTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.SyncTestCase()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def seed_link(self, name, target):
        self.f.write("keep.conf", "keep\n")
        self.f.engine().sync_once()
        path = Path(self.f.work) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)
        fixtures.git(["add", "--", name], cwd=self.f.work)
        fixtures.git(["commit", "--quiet", "-m", "add link"], cwd=self.f.work)
        fixtures.git(["push", "--quiet", "origin", "HEAD:main"], cwd=self.f.work)

    def test_remote_file_link_cannot_write_outside_worktree(self):
        outside = Path(self.f.tmp) / "outside.conf"
        outside.write_text("untouched\n")
        self.seed_link("a.conf", outside)
        self.f.write("a.conf", "local\n")
        self.f.engine().sync_once()
        self.assertEqual(outside.read_text(), "untouched\n")
        self.assertEqual(self.f.remote_show("a.conf"), "local\n")
        self.assertFalse((Path(self.f.work) / "a.conf").is_symlink())

    def test_dangling_remote_link_cannot_create_external_file(self):
        outside = Path(self.f.tmp) / "missing.conf"
        self.seed_link("a.conf", outside)
        self.f.write("a.conf", "local\n")
        self.f.engine().sync_once()
        self.assertFalse(outside.exists())
        self.assertEqual(self.f.remote_show("a.conf"), "local\n")

    def test_parent_link_cannot_remove_external_directory(self):
        outside = Path(self.f.tmp) / "outside"
        nested = outside / "a.conf"
        nested.mkdir(parents=True)
        (nested / "keep.txt").write_text("untouched\n")
        self.seed_link("nested", outside)
        self.f.write("nested/a.conf", "local\n")
        self.f.engine().sync_once()
        self.assertEqual((nested / "keep.txt").read_text(), "untouched\n")
        self.assertEqual(self.f.remote_show("nested/a.conf"), "local\n")

    def test_source_links_are_preserved_not_dereferenced(self):
        outside = Path(self.f.tmp) / "outside"
        outside.mkdir()
        (outside / "secret").write_text("do not copy\n")
        self.f.write("keep.conf", "keep\n")
        targets = {"file-link": outside / "secret", "dir-link": outside,
                   "broken-link": outside / "missing"}
        for name, target in targets.items():
            (Path(self.f.source) / name).symlink_to(target)
        engine = self.f.engine()
        engine.sync_once()
        for name, target in targets.items():
            self.assertEqual(self.f.remote_show(name), str(target))
            mode = fixtures.git(["--git-dir", self.f.remote, "ls-tree", "main", name]).stdout.split()[0]
            self.assertEqual(mode, "120000")
        self.assertIsNone(engine.sync_once().commit)
        for name in targets:
            (Path(self.f.source) / name).unlink()
        result = engine.sync_once()
        self.assertEqual(set(result.deleted), set(targets))
        self.assertEqual((outside / "secret").read_text(), "do not copy\n")

    def test_git_pointer_file_is_never_managed(self):
        self.f.write(".git", "gitdir: /not-a-repository\n")
        self.f.write("a.conf", "local\n")
        self.f.engine().sync_once()
        self.assertEqual(self.f.remote_files(), ["a.conf"])

    def test_directory_swap_preserves_unmatched_files(self):
        self.f.push_foreign_commit({"app.conf/README.md": "untouched\n"})
        self.f.write("app.conf", "local\n")
        head = self.f.remote_head()
        with self.assertRaisesRegex(SyncError, "unmanaged file"):
            self.f.engine(self.f.make_config(include=r"\.conf$")).sync_once()
        self.assertEqual(self.f.remote_head(), head)
        self.assertEqual(self.f.remote_show("app.conf/README.md"), "untouched\n")

    def test_directory_swap_preserves_excluded_files(self):
        self.f.push_foreign_commit({"app/keep.conf": "untouched\n"})
        self.f.write("app", "local\n")
        with self.assertRaisesRegex(SyncError, "unmanaged file"):
            self.f.engine(self.f.make_config(exclude=r"keep\.conf$")).sync_once()
        self.assertEqual(self.f.remote_show("app/keep.conf"), "untouched\n")

    def test_parent_file_swap_preserves_unmatched_file(self):
        self.f.push_foreign_commit({"app": "untouched\n"})
        self.f.write("app/a.conf", "local\n")
        with self.assertRaisesRegex(SyncError, "unmanaged file"):
            self.f.engine(self.f.make_config(include=r"\.conf$")).sync_once()
        self.assertEqual(self.f.remote_show("app"), "untouched\n")

    def test_parent_link_swap_preserves_excluded_link(self):
        outside = Path(self.f.tmp) / "outside"
        outside.mkdir()
        self.seed_link("app", outside)
        self.f.write("app/a.conf", "local\n")
        with self.assertRaisesRegex(SyncError, "unmanaged file"):
            self.f.engine(self.f.make_config(exclude=r"^app$")).sync_once()
        self.assertEqual(self.f.remote_show("app"), str(outside))

    def test_managed_directory_swap_counts_all_deletions(self):
        self.f.push_foreign_commit({"app/a.conf": "old\n", "app/b.conf": "old\n"})
        self.f.write("app", "local\n")
        result = self.f.engine().sync_once()
        self.assertEqual(result.changed, ["app"])
        self.assertEqual(result.deleted, ["app/a.conf", "app/b.conf"])
        self.assertIn("3 file(s)", fixtures.git(
            ["--git-dir", self.f.remote, "log", "-1", "--format=%s", "main"]).stdout)

    def test_staged_names_preserve_unicode_newlines_and_spaces(self):
        names = ["\u914d\u7f6e.conf", " line\nbreak.conf ", 'quote".conf']
        for name in names:
            self.f.write(name, "local\n")
        engine = self.f.engine()
        self.assertEqual(set(engine.sync_once().changed), set(names))
        self.f.write("keep.conf", "keep\n")
        for name in names:
            (Path(self.f.source) / name).unlink()
        self.assertEqual(set(engine.sync_once().deleted), set(names))

    def test_dry_run_and_once_respect_existing_lock(self):
        self.f.write("a.conf", "local\n")
        cfg = self.f.make_config()
        lock = main_module.acquire_lock(cfg.sync.workdir)
        lock_path = Path(cfg.sync.workdir).parent / ".autogitsync.lock"
        owner = lock_path.read_text()
        try:
            for dry_run in (True, False):
                with self.subTest(dry_run=dry_run):
                    self.assertEqual(main_module.run_once(cfg, dry_run=dry_run), 1)
                    self.assertFalse(Path(self.f.work).exists())
                    self.assertEqual(lock_path.read_text(), owner)
        finally:
            lock.close()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main_module.run_once(cfg, dry_run=True), 0)
        self.assertEqual(main_module.run_once(cfg, dry_run=False), 0)

    def seed_history(self):
        engine = self.f.engine()
        for version in range(4):
            self.f.write("a.conf", "version %d\n" % version)
            engine.sync_once()

    def test_enable_and_lower_retention_without_file_changes(self):
        self.seed_history()
        for keep in (3, 1):
            with self.subTest(keep=keep):
                engine = self.f.engine(self.f.make_config(force_push_latest=keep))
                result = engine.sync_once()
                self.assertEqual(self.f.remote_commit_count(), keep)
                self.assertTrue(self.f.remote_head().startswith(result.commit))
                self.assertEqual((result.changed, result.deleted), ([], []))
                head = self.f.remote_head()
                self.assertIsNone(engine.sync_once().commit)
                self.assertEqual(self.f.remote_head(), head)

    def test_retention_reports_the_pushed_commit(self):
        engine = self.f.engine(self.f.make_config(force_push_latest=1))
        for content in ("first\n", "second\n"):
            self.f.write("a.conf", content)
            result = engine.sync_once()
            self.assertTrue(self.f.remote_head().startswith(result.commit))

    def test_history_only_dry_run_does_not_rewrite(self):
        self.seed_history()
        head = self.f.remote_head()
        engine = self.f.engine(self.f.make_config(force_push_latest=1))
        result = engine.sync_once(dry_run=True)
        self.assertIn("history limit", result.detail)
        self.assertEqual(self.f.remote_head(), head)
        self.assertEqual(self.f.remote_commit_count(), 4)
        self.assertEqual(fixtures.git(["rev-parse", "HEAD"], cwd=self.f.work).stdout.strip(), head)

    def test_retention_on_empty_repository_is_noop(self):
        result = self.f.engine(self.f.make_config(force_push_latest=1)).sync_once()
        self.assertTrue(result.ok)
        self.assertIsNone(result.commit)
        self.assertIsNone(self.f.remote_head())


if __name__ == "__main__":
    unittest.main()
