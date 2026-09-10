"""Regression tests for bounded temporary collections and sync object lifetimes."""

from __future__ import annotations

import datetime as dt
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest import mock
import weakref

from tests import test_sync as fixtures

import git_sync
import main as main_module
from git_sync import GitError, SyncError, SyncResult


class TrackedDict(dict):
    """Allow weak references without retaining the collection under test."""


class TrackedList(list):
    """Allow weak references without retaining the collection under test."""


class SyncMemoryTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.SyncTestCase()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_workdir_iterator_is_lazy_and_skips_git(self):
        engine = self.f.engine()
        visited = []

        def walk(root):
            visited.append(root)
            directories = [".git", "nested"]
            yield root, directories, ["a.conf"]
            self.assertEqual(directories, ["nested"])
            visited.append("nested")
            yield str(Path(root) / "nested"), [], ["b.conf", ".git"]

        with mock.patch.object(git_sync.os, "walk", new=walk):
            paths = engine._iter_workdir_files()
            self.assertIs(iter(paths), paths)
            self.assertEqual(visited, [])
            self.assertEqual(next(paths), "a.conf")
            self.assertEqual(visited, [self.f.work])
            self.assertEqual(list(paths), ["nested/b.conf"])

    def test_prune_consumes_files_incrementally(self):
        engine = self.f.engine()
        root = Path(self.f.work)
        root.mkdir()
        for name in ("a.conf", "b.conf"):
            (root / name).write_text("old\n")

        def paths():
            yield "a.conf"
            self.assertFalse((root / "a.conf").exists())
            yield "b.conf"

        with mock.patch.object(engine, "_iter_workdir_files", new=paths):
            self.assertIsNone(engine._prune({}))
        self.assertEqual(list(root.iterdir()), [])

    def test_overlay_returns_no_unused_change_list(self):
        self.f.write("a.conf", "local\n")
        engine = self.f.engine()
        engine._prepare_worktree()
        self.assertIsNone(engine._overlay(engine.scan_source()))
        self.assertEqual((Path(self.f.work) / "a.conf").read_text(), "local\n")

    def test_conflicts_are_checked_before_any_overlay_write(self):
        self.f.push_foreign_commit({"a.conf": "remote\n", "z.conf/README.md": "keep\n"})
        self.f.write("a.conf", "local\n")
        self.f.write("z.conf", "local\n")
        engine = self.f.engine(self.f.make_config(include=r"\.conf$"))
        with self.assertRaisesRegex(SyncError, "unmanaged file"):
            engine.sync_once()
        self.assertEqual((Path(self.f.work) / "a.conf").read_text(), "remote\n")
        self.assertEqual((Path(self.f.work) / "z.conf/README.md").read_text(), "keep\n")

    def test_empty_source_safety_still_counts_all_managed_files(self):
        self.f.push_foreign_commit({"a.conf": "old\n", "sub/b.conf": "old\n", "README.md": "keep\n"})
        head = self.f.remote_head()
        engine = self.f.engine(self.f.make_config(include=r"\.conf$"))
        with self.assertRaisesRegex(SyncError, "remote has 2 managed file"):
            engine.sync_once()
        self.assertEqual(self.f.remote_head(), head)

    def test_scan_and_ignore_lists_are_released_before_staging(self):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                engine = self.f.engine()
                references = []

                def scan():
                    paths = TrackedDict({"a.conf": "source/a.conf"})
                    references.append(weakref.ref(paths))
                    return paths

                def ignored(paths):
                    ignored_paths = TrackedList(["ignored.conf"])
                    references.append(weakref.ref(ignored_paths))
                    return ignored_paths

                def git(args, **kwargs):
                    self.assertTrue(all(reference() is None for reference in references))
                    return "A\0a.conf\0" if "--name-status" in args else ""

                with mock.patch.multiple(engine, scan_source=scan, _ignored_by_repo=ignored,
                                         _prepare_worktree=lambda: None, _reset_to_remote=lambda: None,
                                         _overlay=lambda paths: None, _prune=lambda paths: None,
                                         _git=git, _commit=lambda changed, deleted: "1234567",
                                         _push=lambda **kwargs: None):
                    result = engine.sync_once(dry_run=dry_run)
                self.assertTrue(result.ok)
                self.assertEqual(result.changed, ["a.conf"])
                self.assertEqual(result.deleted, [])

    def test_push_retry_releases_previous_staged_lists(self):
        engine = self.f.engine()
        references = []
        pushes = []

        def prepare():
            self.assertTrue(all(reference() is None for reference in references))

        def staged():
            changed, deleted = TrackedList(["a.conf"]), TrackedList(["gone.conf"])
            references.extend((weakref.ref(changed), weakref.ref(deleted)))
            return changed, deleted

        def push(**kwargs):
            pushes.append(True)
            if len(pushes) == 1:
                raise GitError("remote moved")

        with mock.patch.multiple(engine, _prepare_worktree=prepare,
                                 scan_source=lambda: {"a.conf": "source/a.conf"},
                                 _overlay=lambda paths: None, _prune=lambda paths: None,
                                 _ignored_by_repo=lambda paths: [], _git=lambda *args, **kwargs: "",
                                 _staged_changes=staged, _commit=lambda changed, deleted: "1234567",
                                 _push=push):
            result = engine.sync_once()
        self.assertEqual(len(pushes), 2)
        self.assertEqual(result.changed, ["a.conf"])
        self.assertEqual(result.deleted, ["gone.conf"])

    def test_staged_parser_preserves_all_statuses_and_special_paths(self):
        engine = self.f.engine()
        output = 'A\0 leading.conf\0M\0line\nbreak.conf\0T\0\u914d\u7f6e.conf\0D\0trailing.conf \0'
        with mock.patch.object(engine, "_git", return_value=output):
            changed, deleted = engine._staged_changes()
        self.assertEqual(changed, [" leading.conf", "line\nbreak.conf", "\u914d\u7f6e.conf"])
        self.assertEqual(deleted, ["trailing.conf "])
        with mock.patch.object(engine, "_git", return_value=""):
            self.assertEqual(engine._staged_changes(), ([], []))

    def test_ignored_parser_preserves_special_paths(self):
        engine = self.f.engine()
        paths = [" leading.conf", "line\nbreak.conf", "\u914d\u7f6e.conf", "trailing.conf "]
        with mock.patch.object(engine, "_git_rc", return_value=(0, "\0".join(paths) + "\0")) as git:
            self.assertEqual(engine._ignored_by_repo(paths), paths)
        self.assertEqual(git.call_args.kwargs["stdin"], "\0".join(paths) + "\0")

    def test_benchmark_runs_without_a_working_checkout_directory(self):
        script = Path(__file__).resolve().parent.parent / "tools/benchmark_memory.py"
        result = subprocess.run([sys.executable, str(script), "--files", "50"],
                                cwd=self.f.tmp, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["files"], 50)
        self.assertIn("not container RSS", report["metric"])
        for name in ("workdir_inventory", "staged_parser", "simulated_sync"):
            self.assertEqual(report["cases"][name]["result"], 50)
        self.assertIn("idle_traced_mib", report["cases"]["daemon_idle"]["result"])

    def test_daemon_releases_results_while_idle_and_before_next_sync(self):
        for ok in (True, False):
            with self.subTest(ok=ok):
                cfg = self.f.make_config(interval="1h")
                stop, trigger = threading.Event(), threading.Event()
                state = main_module.RuntimeState()
                lock = io.StringIO()
                references = []
                snapshots = []

                def sync(engine):
                    self.assertTrue(all(reference() is None for reference in references))
                    result = SyncResult(ok=ok, changed=["a.conf"], deleted=["gone.conf"],
                                        commit="1234567" if ok else None,
                                        error=None if ok else "test failure", finished_at=dt.datetime.now())
                    references.append(weakref.ref(result))
                    return result

                def wait(timeout):
                    self.assertTrue(all(reference() is None for reference in references))
                    snapshots.append(state.snapshot())
                    if len(snapshots) == 1:
                        trigger.set()
                    else:
                        stop.set()

                with mock.patch.object(main_module.threading, "Event", side_effect=[stop, trigger]), \
                        mock.patch.object(main_module, "RuntimeState", return_value=state), \
                        mock.patch.object(main_module, "acquire_lock", return_value=lock), \
                        mock.patch.object(main_module, "start_health_server", return_value=None), \
                        mock.patch.object(main_module.signal, "signal"), \
                        mock.patch.object(main_module.GitSync, "sync_once", new=sync), \
                        mock.patch.object(stop, "wait", side_effect=wait):
                    self.assertEqual(main_module.run_daemon(cfg, main_module.Schedule(cfg)), 0)
                self.assertTrue(lock.closed)
                self.assertEqual(len(references), 2)
                for run, snapshot in enumerate(snapshots, 1):
                    self.assertEqual(snapshot["runs"], run)
                    self.assertEqual(snapshot["last_ok"], ok)
                    self.assertEqual(snapshot["last_run"]["changed"], 1)
                    self.assertEqual(snapshot["last_run"]["deleted"], 1)


if __name__ == "__main__":
    unittest.main()
