#!/usr/bin/env python3
"""Compare Python allocation peaks on synthetic inventories, without Git, disk I/O, or Docker.

Run each source revision in a fresh process with the same interpreter and --files value.
--app-dir selects the app directory from another checkout. These are tracemalloc figures,
not container RSS: native allocations, Git subprocesses, and filesystem caches are excluded.
"""

from __future__ import annotations

import argparse
import gc
import importlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import threading
import time
import tracemalloc
from unittest import mock


def relative_path(index):
    return "service-%04d/config-%06d.conf" % (index // 500, index)


def staged_output(count):
    return "".join("%s\0%s\0" % ("D" if index % 5 == 0 else "M", relative_path(index))
                   for index in range(count))


def measure(action):
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    try:
        result = action()
        _, peak = tracemalloc.get_traced_memory()
        return {"peak_mib": round(peak / 2**20, 3),
                "elapsed_s": round(time.perf_counter() - started, 3), "result": result}
    finally:
        tracemalloc.stop()


def walk_count(engine, count):
    def walk(root):
        for start in range(0, count, 500):
            names = ["config-%06d.conf" % index for index in range(start, min(start + 500, count))]
            yield os.path.join(root, "service-%04d" % (start // 500)), [], names

    # The fallback allows comparison with revisions before the iterator optimization.
    walk_files = getattr(engine, "_iter_workdir_files", None) or engine._list_workdir_files
    with mock.patch("os.walk", new=walk):
        return sum(1 for _ in walk_files())


def parse_count(engine, output):
    with mock.patch.object(engine, "_git", new=lambda *args, **kwargs: output):
        changed, deleted = engine._staged_changes()
    return len(changed) + len(deleted)


def simulated_sync(engine, count):
    def scan():
        return {relative_path(index): os.path.join(engine.source, relative_path(index))
                for index in range(count)}

    def git(args, **kwargs):
        return staged_output(count) if "--name-status" in args else ""

    with mock.patch.multiple(engine, _prepare_worktree=lambda: None, scan_source=scan,
                             _overlay=lambda paths: None, _prune=lambda paths: None,
                             _ignored_by_repo=lambda paths: [], _git=git,
                             _commit=lambda changed, deleted: "1234567", _push=lambda **kwargs: None):
        result = engine.sync_once()
    return len(result.changed) + len(result.deleted)


def daemon_idle(main_module, cfg, count):
    stop, trigger = threading.Event(), threading.Event()
    observation = {}
    lock = io.StringIO()

    def sync(engine):
        return main_module.SyncResult(ok=True, commit="1234567",
                                      changed=[relative_path(index) for index in range(count)])

    def wait(timeout):
        current, _ = tracemalloc.get_traced_memory()
        observation["idle_traced_mib"] = round(current / 2**20, 3)
        stop.set()

    with mock.patch.object(main_module.threading, "Event", side_effect=[stop, trigger]), \
            mock.patch.object(main_module, "acquire_lock", return_value=lock), \
            mock.patch.object(main_module, "start_health_server", return_value=None), \
            mock.patch.object(main_module.signal, "signal"), \
            mock.patch.object(main_module.GitSync, "sync_once", new=sync), \
            mock.patch.object(stop, "wait", side_effect=wait):
        try:
            main_module.run_daemon(cfg, main_module.Schedule(cfg))
        finally:
            lock.close()
    return observation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=Path(__file__).resolve().parent.parent / "app")
    parser.add_argument("--files", type=int, default=100000)
    args = parser.parse_args()
    if args.files <= 0:
        parser.error("--files must be positive")
    sys.path.insert(0, str(args.app_dir.resolve()))
    sync_module = importlib.import_module("git_sync")
    main_module = importlib.import_module("main")
    cfg = sync_module.Config(git=sync_module.GitConfig(url="unused.git"),
                             sync=sync_module.SyncConfig(source="source", workdir="work", interval="1h"),
                             server=sync_module.ServerConfig(listen=""))
    engine = sync_module.GitSync(cfg)
    output = staged_output(args.files)  # excluded from the isolated parser measurement
    cases = {"workdir_inventory": lambda: walk_count(engine, args.files),
             "staged_parser": lambda: parse_count(engine, output),
             "simulated_sync": lambda: simulated_sync(engine, args.files),
             "daemon_idle": lambda: daemon_idle(main_module, cfg, args.files)}
    report = {"python": platform.python_version(), "files": args.files,
              "metric": "synthetic Python allocations, not container RSS", "cases": {}}
    for name, action in cases.items():
        result = measure(action)
        if name != "daemon_idle":
            assert result["result"] == args.files, "Incorrect path count"
        report["cases"][name] = result
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
