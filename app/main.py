#!/usr/bin/env python3
"""AutoGitSync daemon entry point.

Configuration comes entirely from environment variables (only ``GIT_REPO`` is required);
there is no config file.  Messages are English by default and can be switched to Chinese
with ``LOG_LANG=zh``.

Usage:
    python main.py                # run as a daemon, syncing on the SCHEDULE / INTERVAL
    python main.py --once         # sync once and exit
    python main.py --dry-run      # show what would change, without committing
    python main.py --check        # print the effective configuration and sync plan
    python main.py --healthcheck  # probe the health endpoint (Docker HEALTHCHECK)
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import http.server
import json
import logging
import os
import signal
import sys
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cron import Cron, CronError                                      # noqa: E402
from git_sync import (Config, ConfigError, GitSync, SyncError,        # noqa: E402
                      SyncResult, load_config, parse_interval, parse_listen)
from i18n import set_language, t                                      # noqa: E402

VERSION = os.environ.get("AUTOGITSYNC_VERSION") or "1.4.0"   # injected by CI from the git tag
LOG = logging.getLogger("autogitsync")


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
def setup_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    logging.getLogger("autogitsync").setLevel(getattr(logging, level.upper(), logging.INFO))


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------
class Schedule:
    """Sync period: a cron expression when given, a fixed interval otherwise."""

    def __init__(self, cfg: Config) -> None:
        self.cron: Optional[Cron] = None
        self.interval: Optional[float] = None
        if cfg.sync.schedule:
            self.cron = Cron(cfg.sync.schedule)
        else:
            self.interval = parse_interval(cfg.sync.interval or "5m")

    def next_after(self, now: dt.datetime) -> dt.datetime:
        if self.cron is not None:
            return self.cron.next_after(now)
        return now + dt.timedelta(seconds=self.interval or 300)

    def describe(self) -> str:
        if self.cron is not None:
            return t("cron %r (container local time, %s)", self.cron.expression, _local_tz_name())
        return _human_interval(self.interval or 0)


def _local_tz_name() -> str:
    return dt.datetime.now().astimezone().tzname() or t("local time")


def _human_interval(seconds: float) -> str:
    for unit, size in (("day", 86400.0), ("hour", 3600.0), ("minute", 60.0)):
        if seconds >= size and abs(seconds % size) < 1e-9:
            value = seconds / size
            return t("every %g %s", value, t(unit if value == 1 else unit + "s"))
    return t("every %g seconds", seconds)


# --------------------------------------------------------------------------
# Runtime state (read by the health endpoint)
# --------------------------------------------------------------------------
class RuntimeState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = dt.datetime.now()
        self.running = False
        self.runs = 0
        self.failures = 0
        self.last_run: Optional[dict] = None
        self.next_run: Optional[dt.datetime] = None
        self.last_error: Optional[str] = None

    def begin(self) -> None:
        with self._lock:
            self.running = True

    def finish(self, result: SyncResult) -> None:
        with self._lock:
            self.running = False
            self.runs += 1
            if not result.ok:
                self.failures += 1
                self.last_error = result.error
            self.last_run = {
                "ok": result.ok,
                "dry_run": result.dry_run,
                "changed": len(result.changed),
                "deleted": len(result.deleted),
                "commit": result.commit,
                "error": result.error,
                "duration_s": round(result.duration, 3),
                "finished_at": (result.finished_at or dt.datetime.now()).isoformat(timespec="seconds"),
            }

    def set_next_run(self, when: Optional[dt.datetime]) -> None:
        with self._lock:
            self.next_run = when

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = dt.datetime.now()
            return {
                "service": "autogitsync",
                "version": VERSION,
                "status": "running" if self.running else "idle",
                "uptime_s": round((now - self.started_at).total_seconds(), 1),
                "started_at": self.started_at.isoformat(timespec="seconds"),
                "runs": self.runs,
                "failures": self.failures,
                "last_run": self.last_run,
                "last_error": self.last_error,
                "next_run": self.next_run.isoformat(timespec="seconds") if self.next_run else None,
                "last_ok": None if self.last_run is None else self.last_run["ok"],
            }


# --------------------------------------------------------------------------
# Health endpoint
# --------------------------------------------------------------------------
def _make_handler(state: RuntimeState, trigger: threading.Event, api_token: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "AutoGitSync/" + VERSION
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:  # keep probes out of the INFO log
            LOG.debug("health %s - %s", self.address_string(), fmt % args)

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = urlsplit(self.path).path.rstrip("/") or "/"
            snapshot = state.snapshot()
            if path in ("/", "/healthz", "/health"):
                self._send(200, {"status": "ok", "version": VERSION,
                                 "uptime_s": snapshot["uptime_s"]})
            elif path in ("/status", "/readyz"):
                healthy = snapshot["last_ok"] is not False
                self._send(200 if healthy else 503, snapshot)
            else:
                self._send(404, {"error": "not found", "endpoints": ["/healthz", "/status", "POST /sync"]})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = urlsplit(self.path).path.rstrip("/")
            if path != "/sync":
                self._send(404, {"error": "not found"})
                return
            if api_token:
                header = self.headers.get("Authorization", "")
                if header != "Bearer " + api_token:
                    self._send(401, {"error": "unauthorized"})
                    return
            trigger.set()
            self._send(202, {"status": "accepted", "message": t("sync requested")})

    return Handler


class _HealthServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_health_server(cfg: Config, state: RuntimeState, trigger: threading.Event):
    host, port = parse_listen(cfg.server.listen)
    if not port:
        return None
    try:
        server = _HealthServer((host, port), _make_handler(state, trigger, cfg.server.api_token))
    except OSError as exc:
        LOG.warning(t("health endpoint cannot listen on %s:%d (%s), continuing without it"),
                    host, port, exc)
        return None
    thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    thread.start()
    LOG.info(t("health endpoint listening on http://%s:%d/healthz "
               "(/status for status, POST /sync to trigger)"), host, port)
    return server


def do_healthcheck() -> int:
    """Liveness probe used by the Docker HEALTHCHECK.

    The port is read straight from ``LISTEN``: inside a container the health check and the
    daemon share the same environment, so changing the port can never make it probe the
    wrong one.  An empty ``LISTEN`` disables the endpoint, which counts as healthy.
    """
    try:
        _, port = parse_listen(os.environ.get("LISTEN", "0.0.0.0:8080"))
    except ConfigError as exc:
        print(t("LISTEN is invalid, cannot probe: %s", exc))
        return 1
    if not port:
        print(t("health endpoint disabled, skipping check"))
        return 0

    url = "http://127.0.0.1:%d/healthz" % port
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status == 200:
                return 0
            print(t("health check failed: %s returned %d", url, response.status))
    except (urllib.error.URLError, OSError) as exc:
        print(t("health check failed: %s (%s)", url, exc))
    return 1


# --------------------------------------------------------------------------
# Single instance guard
# --------------------------------------------------------------------------
def acquire_lock(workdir: str):
    """File lock so two processes never operate on the same work copy."""
    lock_path = os.path.join(os.path.dirname(os.path.abspath(workdir)), ".autogitsync.lock")
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        handle = open(lock_path, "w", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise SyncError(t("cannot lock the data directory (another instance may be running): "
                          "%s (%s)", lock_path, exc))
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


# --------------------------------------------------------------------------
# Run modes
# --------------------------------------------------------------------------
def run_daemon(cfg: Config, schedule: Schedule) -> int:
    state = RuntimeState()
    stop = threading.Event()
    trigger = threading.Event()
    engine = GitSync(cfg, LOG)
    lock = acquire_lock(cfg.sync.workdir)  # one instance per data directory

    def on_signal(signum, _frame):
        LOG.info(t("received %s, stopping the scheduler (the current sync will finish first)"),
                 signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    server = start_health_server(cfg, state, trigger)
    LOG.info(t("AutoGitSync %s starting: %s -> %s#%s, schedule %s"),
             VERSION, cfg.sync.source, cfg.git.url, cfg.git.branch, schedule.describe())
    LOG.info(t("sync rules: include=%r exclude=%r delete_missing=%s repo_dir=%s"),
             cfg.sync.include, cfg.sync.exclude, cfg.sync.delete_missing, cfg.sync.workdir)
    if cfg.git.url.startswith(("http://", "https://")) and not cfg.git.token:
        LOG.warning(t("GIT_REPO is an http(s) URL but GIT_TOKEN is not set; "
                      "pushing to a private repository will fail"))

    next_run = dt.datetime.now()
    if not cfg.sync.run_on_start:
        next_run = schedule.next_after(next_run)
    state.set_next_run(next_run)

    while not stop.is_set():
        now = dt.datetime.now()
        if trigger.is_set() or now >= next_run:
            trigger.clear()
            state.begin()
            try:
                result = engine.sync_once()
            except (SyncError, ConfigError) as exc:
                result = SyncResult.failure(str(exc))
                LOG.error(t("sync failed: %s"), exc)
            except Exception as exc:  # never let an unexpected error kill the daemon
                result = SyncResult.failure("%s: %s" % (type(exc).__name__, exc))
                LOG.exception(t("unexpected error during sync"))
            state.finish(result)
            if result.ok:
                LOG.info(t("sync finished: %s"), result.summary)
            next_run = schedule.next_after(dt.datetime.now())
            state.set_next_run(next_run)
            LOG.info(t("next sync at %s"), next_run.strftime("%Y-%m-%d %H:%M:%S"))
            continue
        stop.wait(min(1.0, max(0.05, (next_run - now).total_seconds())))

    if server is not None:
        server.shutdown()
        server.server_close()
    lock.close()          # release the single instance lock
    LOG.info(t("AutoGitSync stopped"))
    return 0


def run_once(cfg: Config, dry_run: bool) -> int:
    engine = GitSync(cfg, LOG)
    lock = None
    try:
        if not dry_run:
            lock = acquire_lock(cfg.sync.workdir)
        result = engine.sync_once(dry_run=dry_run)
    except (SyncError, ConfigError) as exc:
        LOG.error(t("sync failed: %s"), exc)
        return 1
    finally:
        if lock is not None:
            lock.close()
    if dry_run and result.detail:
        print(result.detail)
    LOG.info(t("done: %s"), result.summary)
    return 0


def print_check(cfg: Config, schedule: Schedule) -> int:
    """Print the effective configuration (all from environment variables) and the plan."""
    engine = GitSync(cfg, LOG)
    desired = engine.scan_source()
    total = sum(len(files) for _, _, files in os.walk(cfg.sync.source))
    print(t("effective configuration (all from environment variables):"))
    rows = (
        ("GIT_REPO", cfg.git.url),
        ("GIT_BRANCH", cfg.git.branch),
        ("GIT_TOKEN", t("set") if cfg.git.token else t("not set")),
        ("SOURCE_DIR", cfg.sync.source),
        ("INCLUDE", repr(cfg.sync.include)),
        ("EXCLUDE", repr(cfg.sync.exclude)),
        ("SCHEDULE", repr(cfg.sync.schedule)),
        ("INTERVAL", repr(cfg.sync.interval)),
        ("DELETE_MISSING", cfg.sync.delete_missing),
        ("ALLOW_EMPTY", cfg.sync.allow_empty),
        ("REPO_DIR", cfg.sync.workdir),
        ("RUN_ON_START", cfg.sync.run_on_start),
        ("FORCE_PUSH_LATEST", cfg.sync.force_push_latest),
        ("LISTEN", repr(cfg.server.listen)),
        ("LOG_LEVEL", cfg.log_level),
    )
    for name, value in rows:
        print("  %-16s = %s" % (name, value))
    print()
    print(t("matched files: %d (out of %d file(s) in the directory)", len(desired), total))
    for relpath in sorted(desired)[:20]:
        print("  - %s" % relpath)
    if len(desired) > 20:
        print(t("  ... (%d more)", len(desired) - 20))
    print()
    print(t("schedule   : %s", schedule.describe()))
    print(t("next 5 runs:"))
    moment = dt.datetime.now()
    for _ in range(5):
        moment = schedule.next_after(moment)
        print("  %s" % moment.strftime("%Y-%m-%d %H:%M:%S"))
    if not desired and cfg.sync.delete_missing and not cfg.sync.allow_empty:
        print()
        print(t("note: nothing matched, the sync will refuse to delete anything "
                "(ALLOW_EMPTY=false)"))
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autogitsync",
        description=t("Sync a local directory to a Git repository on a schedule (local wins "
                      "on conflicts). Configured entirely through environment variables."))
    parser.add_argument("--once", action="store_true", help=t("sync once and exit"))
    parser.add_argument("--dry-run", action="store_true",
                        help=t("show what would change, without committing or pushing"))
    parser.add_argument("--check", action="store_true",
                        help=t("print the effective configuration and sync plan, then exit"))
    parser.add_argument("--healthcheck", action="store_true",
                        help=t("probe the health endpoint (used by the Docker HEALTHCHECK)"))
    parser.add_argument("--log-level", default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"], help=t("override LOG_LEVEL"))
    parser.add_argument("--version", action="version", version="AutoGitSync " + VERSION)
    return parser


def main(argv: Optional[list] = None) -> int:
    # pick the message language before anything can print
    set_language(os.environ.get("LOG_LANG", ""))
    args = build_parser().parse_args(argv)

    if args.healthcheck:
        return do_healthcheck()

    try:
        cfg = load_config()
    except ConfigError as exc:
        logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s", stream=sys.stderr)
        LOG.error(t("configuration error: %s"), exc)
        return 2

    setup_logging(args.log_level or cfg.log_level)

    try:
        schedule = Schedule(cfg)
    except (CronError, ConfigError) as exc:
        LOG.error(t("schedule configuration error: %s"), exc)
        return 2

    if args.check:
        return print_check(cfg, schedule)
    try:
        if args.dry_run:
            return run_once(cfg, dry_run=True)
        if args.once:
            return run_once(cfg, dry_run=False)
        return run_daemon(cfg, schedule)
    except SyncError as exc:          # e.g. another instance already holds the data directory
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
