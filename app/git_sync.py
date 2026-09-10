"""AutoGitSync core: environment-variable configuration and the Git sync engine.

Configuration comes **entirely from environment variables** - there is no config file.
Only ``GIT_REPO`` is required, everything else has a sensible default (see docs/configuration.md).

Sync semantics (every run follows the same deterministic flow):

1. reset the local work copy to the latest remote commit - the remote is only the
   destination, never the source of truth;
2. scan ``SOURCE_DIR`` and copy every file matching ``INCLUDE`` / ``EXCLUDE`` into the
   work copy, keeping its **original relative path**;
3. delete files that exist on the remote but not locally, as long as they match the
   filters (``DELETE_MISSING=true``);
4. commit and push; if the push is rejected (the remote moved on), fetch again, replay the
   local files, commit and retry - **local always wins** and, by default, nothing is ever
   force-pushed and no remote history is lost.

Exception: with ``FORCE_PUSH_LATEST=N`` (N > 0) each run rewrites the branch history down
to its **last N commits** and force-pushes (the oldest kept commit becomes a parentless
root commit).  ``N=1`` means the remote only ever holds the newest state, so content that
was synced earlier (for example a secret file that was later deleted) cannot be recovered
from the commit log.  The cost is that older history is dropped and the branch must allow
force pushes.

Safety: a missing ``SOURCE_DIR`` fails the startup validation, and when nothing matches
while the remote still holds managed files the run is refused instead of wiping the
repository (so mounting the wrong directory cannot destroy it).
"""

from __future__ import annotations

import datetime as dt
import filecmp
import logging
import os
import re
import shutil
import socket
import stat as stat_module
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from itertools import chain
from typing import Dict, Iterator, List, Mapping, Optional, Pattern, Tuple

from i18n import t

__all__ = [
    "Config", "ConfigError", "GitConfig", "GitError", "GitSync",
    "ServerConfig", "SyncConfig", "SyncError", "SyncResult", "load_config",
    "parse_interval", "parse_listen",
]


class ConfigError(ValueError):
    """The configuration (environment variables) is invalid."""


class SyncError(RuntimeError):
    """Something went wrong while syncing."""


class GitError(SyncError):
    """A git command failed."""


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
@dataclass
class GitConfig:
    url: str = ""
    branch: str = "main"
    token: str = ""
    username: str = "x-access-token"
    author_name: str = "AutoGitSync"
    author_email: str = "autogitsync@localhost"
    commit_message: str = "sync: {count} file(s) changed at {time}"
    push_retries: int = 3


@dataclass
class SyncConfig:
    source: str = ""
    include: str = ".*"
    exclude: str = ""
    delete_missing: bool = True
    allow_empty: bool = False
    workdir: str = "/data/repo"
    run_on_start: bool = True
    schedule: str = ""
    interval: str = ""
    force_push_latest: int = 0        # >0: force-push, keeping only the last N commits


@dataclass
class ServerConfig:
    listen: str = "0.0.0.0:8080"
    api_token: str = ""


@dataclass
class Config:
    git: GitConfig = field(default_factory=GitConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    log_level: str = "INFO"
    include_re: Pattern[str] = field(default=None, repr=False)  # type: ignore[assignment]
    exclude_re: Optional[Pattern[str]] = field(default=None, repr=False)


_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|sec|m|min|h|hr|d|day)?\s*$", re.IGNORECASE)
_DURATION_UNITS = {
    "ms": 0.001, "s": 1.0, "sec": 1.0, "m": 60.0, "min": 60.0,
    "h": 3600.0, "hr": 3600.0, "d": 86400.0, "day": 86400.0,
}
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off", "")
_DEFAULT_COMMIT_MESSAGE = "sync: {count} file(s) changed at {time}"


def parse_interval(text: str) -> float:
    """Parse ``30s`` / ``5m`` / ``2h`` / ``30`` (seconds by default) into seconds."""
    match = _DURATION_RE.match(str(text))
    if not match:
        raise ConfigError(t("Invalid interval: %r (examples: 30s / 5m / 2h / 1d)", text))
    value = float(match.group(1))
    seconds = value * _DURATION_UNITS[(match.group(2) or "s").lower()]
    if seconds <= 0:
        raise ConfigError(t("Interval must be greater than 0: %r", text))
    return seconds


def parse_listen(value: str) -> Tuple[str, int]:
    """Parse ``host:port``; an empty value or port 0 disables the endpoint."""
    text = (value or "").strip()
    if not text:
        return "", 0
    if ":" in text:
        host, _, port_text = text.rpartition(":")
        host = host.strip("[]") or "0.0.0.0"
    else:
        host, port_text = "0.0.0.0", text
    try:
        port = int(port_text)
    except ValueError:
        raise ConfigError(t("Invalid port: %r (example: 0.0.0.0:8080, empty disables the endpoint)",
                            value))
    if port < 0 or port > 65535:
        raise ConfigError(t("Port out of range: %r", value))
    return host, port


def _env_str(env: Mapping[str, str], name: str, default: str = "") -> str:
    value = env.get(name)
    return default if value is None else value.strip()


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None:
        return default
    text = value.strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ConfigError(t("%s must be a boolean (true/false), got %r", name, value))


def _env_int(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    try:
        number = int(value.strip())
    except ValueError:
        raise ConfigError(t("%s must be an integer, got %r", name, value))
    if number < minimum:
        raise ConfigError(t("%s cannot be less than %d, got %d", name, minimum, number))
    return number


def load_config(environ: Optional[Mapping[str, str]] = None) -> Config:
    """Read and validate the configuration from environment variables.

    Only ``GIT_REPO`` is required (private repositories also need ``GIT_TOKEN``);
    everything else has a default.  ``environ`` exists for tests and defaults to
    ``os.environ``.
    """
    env: Mapping[str, str] = os.environ if environ is None else environ

    git_cfg = GitConfig(
        url=_env_str(env, "GIT_REPO"),
        branch=_env_str(env, "GIT_BRANCH", "main"),
        token=_env_str(env, "GIT_TOKEN"),
        username=_env_str(env, "GIT_USERNAME", "x-access-token"),
        author_name=_env_str(env, "GIT_AUTHOR_NAME", "AutoGitSync"),
        author_email=_env_str(env, "GIT_AUTHOR_EMAIL", "autogitsync@localhost"),
        commit_message=_env_str(env, "COMMIT_MESSAGE", _DEFAULT_COMMIT_MESSAGE),
        push_retries=_env_int(env, "PUSH_RETRIES", 3),
    )
    sync_cfg = SyncConfig(
        source=_env_str(env, "SOURCE_DIR", "/source"),
        include=_env_str(env, "INCLUDE", ".*"),
        exclude=_env_str(env, "EXCLUDE"),
        delete_missing=_env_bool(env, "DELETE_MISSING", True),
        allow_empty=_env_bool(env, "ALLOW_EMPTY", False),
        workdir=_env_str(env, "REPO_DIR", "/data/repo"),
        run_on_start=_env_bool(env, "RUN_ON_START", True),
        force_push_latest=_env_int(env, "FORCE_PUSH_LATEST", 0),
        schedule=_env_str(env, "SCHEDULE"),
        interval=_env_str(env, "INTERVAL"),
    )
    server_cfg = ServerConfig(
        listen=_env_str(env, "LISTEN", "0.0.0.0:8080"),
        api_token=_env_str(env, "API_TOKEN"),
    )
    log_level = _env_str(env, "LOG_LEVEL", "INFO").upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ConfigError(t("Invalid LOG_LEVEL: %r", log_level))

    cfg = Config(git=git_cfg, sync=sync_cfg, server=server_cfg, log_level=log_level)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    from cron import Cron, CronError  # imported lazily to keep parser and config decoupled

    if not cfg.git.url:
        raise ConfigError(t("GIT_REPO is required (Git repository URL)"))
    if not cfg.git.branch:
        raise ConfigError(t("GIT_BRANCH cannot be empty"))
    if not cfg.sync.source:
        raise ConfigError(t("SOURCE_DIR cannot be empty"))
    cfg.sync.source = os.path.abspath(os.path.expanduser(cfg.sync.source))
    cfg.sync.workdir = os.path.abspath(os.path.expanduser(cfg.sync.workdir))

    if not os.path.isdir(cfg.sync.source):
        raise ConfigError(t("SOURCE_DIR is not an existing directory: %s "
                            "(did you forget to mount it?)", cfg.sync.source))

    try:
        cfg.include_re = re.compile(cfg.sync.include)
    except re.error as exc:
        raise ConfigError(t("INCLUDE is not a valid regex: %s (%s)", cfg.sync.include, exc))
    if cfg.sync.exclude:
        try:
            cfg.exclude_re = re.compile(cfg.sync.exclude)
        except re.error as exc:
            raise ConfigError(t("EXCLUDE is not a valid regex: %s (%s)", cfg.sync.exclude, exc))

    source = cfg.sync.source.rstrip(os.sep)
    workdir = cfg.sync.workdir.rstrip(os.sep)
    if source == workdir:
        raise ConfigError(t("SOURCE_DIR and REPO_DIR cannot be the same directory: %s", source))
    if source.startswith(workdir + os.sep) or workdir.startswith(source + os.sep):
        raise ConfigError(t("SOURCE_DIR and REPO_DIR cannot be nested: %s / %s", source, workdir))

    if cfg.sync.schedule:
        try:
            Cron(cfg.sync.schedule)
        except CronError as exc:
            raise ConfigError(t("SCHEDULE is not a valid cron expression: %s", exc))
    elif cfg.sync.interval:
        try:
            parse_interval(cfg.sync.interval)
        except ConfigError as exc:
            raise ConfigError("INTERVAL %s" % exc)
    else:
        cfg.sync.interval = "5m"   # default period when neither cron nor interval is given

    try:
        parse_listen(cfg.server.listen)   # fail at startup, not later in the health check
    except ConfigError as exc:
        raise ConfigError("LISTEN %s" % exc)


# --------------------------------------------------------------------------
# Sync result
# --------------------------------------------------------------------------
@dataclass
class SyncResult:
    ok: bool = False
    changed: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    commit: Optional[str] = None
    error: Optional[str] = None
    detail: str = ""
    dry_run: bool = False
    started_at: dt.datetime = field(default_factory=dt.datetime.now)
    finished_at: Optional[dt.datetime] = None

    @property
    def duration(self) -> float:
        end = self.finished_at or dt.datetime.now()
        return (end - self.started_at).total_seconds()

    @property
    def summary(self) -> str:
        if not self.ok:
            return t("failed: %s", self.error)
        if self.dry_run:
            return t("dry run: %d added/updated, %d deleted", len(self.changed), len(self.deleted))
        if self.commit is None:
            return t("no changes")
        return t("committed %s: %d added/updated, %d deleted in %.1fs",
                 self.commit, len(self.changed), len(self.deleted), self.duration)

    @classmethod
    def failure(cls, error: str, started_at: Optional[dt.datetime] = None) -> "SyncResult":
        return cls(ok=False, error=error, started_at=started_at or dt.datetime.now(),
                   finished_at=dt.datetime.now())


# --------------------------------------------------------------------------
# Sync engine
# --------------------------------------------------------------------------
def _build_auth_url(cfg: GitConfig) -> str:
    """Inject the token into an https URL; anything else (path, file://, ssh) is returned as is."""
    url = cfg.url.strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not cfg.token:
        return url
    user = urllib.parse.quote(cfg.username or "x-access-token", safe="")
    secret = urllib.parse.quote(cfg.token, safe="")
    return urllib.parse.urlunsplit(
        (parts.scheme, "%s:%s@%s" % (user, secret, parts.netloc),
         parts.path, parts.query, parts.fragment))


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:  # keep unknown placeholders untouched
        return "{%s}" % key


class GitSync:
    """Syncs a local directory to a Git repository; one call equals one full run."""

    def __init__(self, cfg: Config, logger: Optional[logging.Logger] = None) -> None:
        self.cfg = cfg
        self.log = logger or logging.getLogger("autogitsync")
        self.workdir = cfg.sync.workdir
        self.source = cfg.sync.source
        self.include_re = cfg.include_re or re.compile(cfg.sync.include)
        self.exclude_re = cfg.exclude_re
        self.auth_url = _build_auth_url(cfg.git)
        self._secrets = [s for s in {cfg.git.token,
                                     urllib.parse.quote(cfg.git.token, safe="")} if s]

    # -- low level helpers --------------------------------------------------
    def _redact(self, text: Optional[str]) -> str:
        if not text:
            return ""
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def _git_rc(self, args: List[str], cwd: Optional[str] = None, stdin: Optional[str] = None,
                env_extra: Optional[Dict[str, str]] = None) -> Tuple[int, str]:
        env = os.environ.copy()
        env.update({
            "GIT_TERMINAL_PROMPT": "0",   # fail fast on auth errors instead of hanging
            "GIT_ASKPASS": "/bin/true",
            "GCM_INTERACTIVE": "never",
            "LC_ALL": "C",
        })
        if env_extra:
            env.update(env_extra)
        command = ["git"]
        if cwd:
            command += ["-C", cwd]
        command += list(args)
        try:
            proc = subprocess.run(command, capture_output=True, text=True, env=env, input=stdin)
        except FileNotFoundError:
            raise GitError(t("git executable not found, make sure git is installed in the image"))
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, self._redact(output if "-z" in args else output.strip())

    def _git(self, args: List[str], cwd: Optional[str] = None,
             env_extra: Optional[Dict[str, str]] = None) -> str:
        code, output = self._git_rc(args, cwd=cwd, env_extra=env_extra)
        if code != 0:
            action = " ".join(a for a in args[:2] if not a.startswith("-"))
            raise GitError(t("git %s failed (exit %d): %s", action, code, output or t("no output")))
        return output

    def _managed(self, relpath: str) -> bool:
        """Whether a relative path is inside the managed set."""
        if ".git" in relpath.split("/"):
            return False
        if not self.include_re.search(relpath):
            return False
        if self.exclude_re and self.exclude_re.search(relpath):
            return False
        return True

    # -- work copy preparation ---------------------------------------------
    def _remote_branch_exists(self) -> bool:
        output = self._git(["ls-remote", "--heads", self.auth_url,
                            "refs/heads/%s" % self.cfg.git.branch])
        return bool(output.strip())

    def _ensure_remote(self) -> None:
        code, _ = self._git_rc(["remote", "get-url", "origin"], cwd=self.workdir)
        if code == 0:
            self._git(["remote", "set-url", "origin", self.auth_url], cwd=self.workdir)
        else:
            self._git(["remote", "add", "origin", self.auth_url], cwd=self.workdir)

    def _prepare_worktree(self) -> None:
        """Bring the work copy back to a clean copy of the latest remote commit."""
        if not os.path.isdir(os.path.join(self.workdir, ".git")):
            self._clone_or_init()
        else:
            self._ensure_remote()
            remote_ref = "refs/remotes/origin/%s" % self.cfg.git.branch
            if self._remote_branch_exists():
                self._git(["fetch", "--prune", "--quiet", "origin",
                           "+refs/heads/{0}:{1}".format(self.cfg.git.branch, remote_ref)],
                          cwd=self.workdir)
            else:
                self.log.warning(t("remote branch %s does not exist yet, it will be created by this push"),
                                 self.cfg.git.branch)
                self._git_rc(["update-ref", "-d", remote_ref], cwd=self.workdir)  # drop stale ref
        self._reset_to_remote()

    def _clone_or_init(self) -> None:
        parent = os.path.dirname(self.workdir)
        os.makedirs(parent, exist_ok=True)
        if os.path.isdir(self.workdir):
            leftovers = os.listdir(self.workdir)
            if leftovers:
                raise SyncError(t("work directory is not empty and is not a git repository, "
                              "please clean it: %s", self.workdir))
        else:
            os.makedirs(self.workdir)

        if self._remote_branch_exists():
            self.log.info(t("first run: cloning %s (branch %s)"), self.cfg.git.url, self.cfg.git.branch)
            self._git(["clone", "--quiet", "--branch", self.cfg.git.branch,
                       "--single-branch", self.auth_url, self.workdir])
        else:
            self.log.info(t("first run: remote has no branch %s yet, initializing an empty repository"),
                          self.cfg.git.branch)
            self._git(["init", "--quiet", self.workdir])
            self._git(["remote", "add", "origin", self.auth_url], cwd=self.workdir)

    def _has_head(self) -> bool:
        code, _ = self._git_rc(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=self.workdir)
        return code == 0

    def _reset_to_remote(self) -> None:
        if self._has_head():
            self._git(["reset", "--hard", "--quiet"], cwd=self.workdir)
        else:
            # An empty repository has no HEAD to reset, so clear the index by hand -
            # otherwise staged content from a previous dry run or failed run leaks in.
            self._git(["read-tree", "--empty"], cwd=self.workdir)
        self._git(["clean", "-fdxq"], cwd=self.workdir)

        remote_ref = "refs/remotes/origin/%s" % self.cfg.git.branch
        code, _ = self._git_rc(["rev-parse", "--verify", "--quiet", remote_ref], cwd=self.workdir)
        if code == 0:
            self._git(["checkout", "--quiet", "-B", self.cfg.git.branch, remote_ref], cwd=self.workdir)
            self._git(["reset", "--hard", "--quiet", remote_ref], cwd=self.workdir)
            self._git(["clean", "-fdxq"], cwd=self.workdir)
        else:
            self._git(["checkout", "--quiet", "-B", self.cfg.git.branch], cwd=self.workdir)

    # -- local directory -> work copy --------------------------------------
    @staticmethod
    def _same_dir(left: str, right: str) -> bool:
        """Whether two paths are the same directory (inode based, detects overlapping mounts)."""
        try:
            return os.path.samefile(left, right)
        except OSError:
            return False

    def scan_source(self) -> Dict[str, str]:
        """Map of relative path -> absolute local path, already filtered by include/exclude."""
        if self._same_dir(self.source, self.workdir):
            raise SyncError(t("SOURCE_DIR and REPO_DIR point to the same directory: %s", self.source))

        desired: Dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(self.source):
            keep: List[str] = []
            dirnames.sort()
            for name in dirnames:
                if name == ".git":
                    continue
                # The work copy itself must never be treated as content to sync: when two
                # volumes overlap on the host (SOURCE_DIR can see REPO_DIR) every run would
                # nest one more level of data/repo/... into the repository, forever.
                if self._same_dir(os.path.join(dirpath, name), self.workdir):
                    self.log.warning(t(
                        "SOURCE_DIR contains the work copy %s (the two mounts overlap on the "
                        "host); skipping it - mount the data volume outside the synced "
                        "directory", self.workdir))
                    continue
                if os.path.islink(os.path.join(dirpath, name)):
                    filenames.append(name)  # preserve directory links without traversing them
                else:
                    keep.append(name)
            dirnames[:] = keep

            filenames.sort()
            for name in filenames:
                abspath = os.path.join(dirpath, name)
                relpath = os.path.relpath(abspath, self.source).replace(os.sep, "/")
                if self._managed(relpath):
                    desired[relpath] = abspath
        return desired

    def _check_path_conflicts(self, desired: Dict[str, str]) -> None:
        """Refuse type swaps that would remove files outside the managed set."""
        ancestors = set()
        for relpath in desired:
            parts = relpath.split("/")
            ancestors.update("/".join(parts[:index]) for index in range(1, len(parts)))
        for relpath in self._iter_workdir_files():
            if self._managed(relpath):
                continue
            parts = relpath.split("/")
            replaces_parent = any("/".join(parts[:index]) in desired
                                  for index in range(1, len(parts)))
            if relpath in ancestors or replaces_parent:
                raise SyncError(self._redact(t(
                    "cannot replace a file or directory because it would remove an unmanaged file: %s",
                    relpath)))

    def _ensure_parents(self, relpath: str) -> None:
        """Create the parent directories of a target path, removing a file that sits in the way."""
        parts = relpath.split("/")[:-1]
        current = self.workdir
        for part in parts:
            current = os.path.join(current, part)
            if os.path.islink(current) or (os.path.exists(current) and not os.path.isdir(current)):
                os.remove(current)
            if not os.path.exists(current):
                os.makedirs(current)

    @staticmethod
    def _same_exec_bit(left: str, right: str) -> bool:
        try:
            left_mode = os.stat(left).st_mode & stat_module.S_IXUSR
            right_mode = os.stat(right).st_mode & stat_module.S_IXUSR
        except OSError:
            return False
        return bool(left_mode) == bool(right_mode)

    def _ignored_by_repo(self, relpaths: List[str]) -> List[str]:
        """Managed files that are excluded by the **target repository's** .gitignore.

        ``git add`` skips those silently (no error, nothing in the commit), so without a
        warning the only visible symptom is "the log says N files, the repository has
        fewer".  A very common cause: the repository template ships a .gitignore with
        ``.env`` in it.
        """
        if not relpaths:
            return []
        code, output = self._git_rc(["check-ignore", "-z", "--stdin"],
                                    cwd=self.workdir, stdin="\0".join(relpaths) + "\0")
        if code not in (0, 1):      # 0 = some are ignored, 1 = none are
            return []
        return [match.group() for match in re.finditer(r"[^\0]+", output)]

    def _overlay(self, desired: Dict[str, str]) -> None:
        """Copy local files only after the complete path-conflict preflight succeeds."""
        self._check_path_conflicts(desired)
        for relpath in sorted(desired):
            source = desired[relpath]
            self._ensure_parents(relpath)  # replace parent links before inspecting the target
            target = os.path.join(self.workdir, relpath)
            if os.path.islink(target):
                if os.path.islink(source) and os.readlink(source) == os.readlink(target):
                    continue
                os.unlink(target)  # never write through a remote-controlled symlink
            elif os.path.isdir(target):
                shutil.rmtree(target)
            elif (not os.path.islink(source) and os.path.isfile(target)
                  and filecmp.cmp(source, target, shallow=False)
                  and self._same_exec_bit(source, target)):
                continue
            if os.path.islink(source):
                if os.path.lexists(target):
                    os.unlink(target)
                os.symlink(os.readlink(source), target)
            else:
                shutil.copy2(source, target)

    def _prune(self, desired: Dict[str, str]) -> None:
        """Delete files that exist on the remote but are gone locally (and match the filters)."""
        if not self.cfg.sync.delete_missing:
            return
        for relpath in self._iter_workdir_files():
            if relpath in desired or not self._managed(relpath):
                continue
            try:
                os.remove(os.path.join(self.workdir, relpath))
            except OSError as exc:
                raise SyncError(t("failed to delete %s: %s", relpath, exc))

        # drop directories left empty by the deletions (git does not track empty dirs)
        for dirpath, _dirnames, _filenames in os.walk(self.workdir, topdown=False):
            if dirpath == self.workdir or ".git" in os.path.relpath(dirpath, self.workdir).split(os.sep):
                continue
            try:
                os.rmdir(dirpath)  # a nonempty directory fails without allocating a listing
            except OSError:
                pass

    # -- commit and push ----------------------------------------------------
    def _commit_message(self, changed: List[str], deleted: List[str]) -> str:
        template = self.cfg.git.commit_message or "sync: {count} file(s) changed at {time}"
        values = {
            "count": len(changed) + len(deleted),
            "changed": len(changed),
            "deleted": len(deleted),
            "time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": self.source,
            "host": socket.gethostname(),
        }
        try:
            return template.format_map(_SafeDict(values))
        except (IndexError, ValueError):
            return template

    def _commit(self, changed: List[str], deleted: List[str]) -> Optional[str]:
        code, _ = self._git_rc(["diff", "--cached", "--quiet"], cwd=self.workdir)
        if code == 0:
            return None                          # identical to the remote already
        self._git(["-c", "user.name=%s" % self.cfg.git.author_name,
                   "-c", "user.email=%s" % self.cfg.git.author_email,
                   "commit", "--quiet", "-m", self._commit_message(changed, deleted)],
                  cwd=self.workdir)
        return self._git(["rev-parse", "--short", "HEAD"], cwd=self.workdir).strip()

    def _truncate_history(self, keep: int) -> bool:
        """Rewrite the branch history down to its last ``keep`` commits.

        Used by ``FORCE_PUSH_LATEST``: the remote branch keeps only the most recent runs, so
        anything synced earlier (for example a secret file that was later deleted) is gone
        from the commit log.  The oldest kept commit becomes a parentless root commit.

        Only the parent chain is rewritten - trees, messages and commit dates are reused, so
        the kept states are byte-for-byte identical.
        """
        if not self._has_head():
            return False
        shas = self._git(["rev-list", "-n", str(keep), "HEAD"], cwd=self.workdir).split()
        if not shas:
            return False
        # Nothing to truncate when the oldest kept commit is already a root commit.  The
        # test must be "does it have a parent", never "did rev-list return a single
        # commit": with keep=1 it always returns exactly one.
        if len(self._git(["rev-list", "--parents", "-n", "1", shas[-1]],
                         cwd=self.workdir).split()) <= 1:
            return False
        parent: Optional[str] = None
        for sha in reversed(shas):                   # rebuild starting from the oldest
            tree = self._git(["rev-parse", "%s^{tree}" % sha], cwd=self.workdir).strip()
            message = self._git(["log", "-1", "--format=%B", sha], cwd=self.workdir).rstrip("\n")
            dates = self._git(["log", "-1", "--format=%aI%n%cI", sha],
                              cwd=self.workdir).splitlines()
            args = ["commit-tree", tree]
            if parent:
                args += ["-p", parent]
            args += ["-m", message]
            env_extra = {}
            if len(dates) == 2:
                env_extra = {"GIT_AUTHOR_DATE": dates[0].strip(),
                             "GIT_COMMITTER_DATE": dates[1].strip()}
            parent = self._git(["-c", "user.name=%s" % self.cfg.git.author_name,
                                "-c", "user.email=%s" % self.cfg.git.author_email] + args,
                               cwd=self.workdir, env_extra=env_extra).strip()
        if parent:
            self._git(["update-ref", "refs/heads/%s" % self.cfg.git.branch, parent],
                      cwd=self.workdir)
        return parent is not None

    def _push(self, force: bool = False) -> None:
        args = ["push", "--quiet"]
        if force:
            args.append("--force")
        args += ["origin", "HEAD:refs/heads/%s" % self.cfg.git.branch]
        self._git(args, cwd=self.workdir)

    # -- public entry point -------------------------------------------------
    def sync_once(self, dry_run: bool = False) -> SyncResult:
        """Run one sync.  Raises :class:`SyncError`; ``dry_run`` never commits or pushes."""
        result = SyncResult(ok=False)
        attempts = max(1, int(self.cfg.git.push_retries) + 1)

        for attempt in range(1, attempts + 1):
            self._prepare_worktree()
            desired = self.scan_source()

            if not desired and self.cfg.sync.delete_missing and not self.cfg.sync.allow_empty:
                remote_managed = sum(1 for rel in self._iter_workdir_files() if self._managed(rel))
                if remote_managed:
                    raise SyncError(t(
                        "no file in %s matches %r, but the remote has %d managed file(s); "
                        "skipping this run to avoid wiping the repository (set "
                        "ALLOW_EMPTY=true if this is intended)",
                        self.source, self.cfg.sync.include, remote_managed))

            self._overlay(desired)
            self._prune(desired)

            ignored = self._ignored_by_repo(sorted(desired))
            if ignored:
                self.log.warning(t(
                    "%d managed file(s) are excluded by the target repository's .gitignore "
                    "and will not be committed: %s%s; remove the matching rule from that "
                    ".gitignore (or exclude them with EXCLUDE)",
                    len(ignored), ", ".join(ignored[:5]), " ..." if len(ignored) > 5 else ""))
            del desired, ignored  # do not overlap the source inventory with staged paths or packing

            self._git(["add", "-A", "--", "."], cwd=self.workdir)
            # The index also includes deletions caused by file/directory swaps. NUL-delimited
            # paths keep whitespace and non-ASCII names intact, without Git's display quoting.
            changed, deleted = self._staged_changes()
            keep = int(self.cfg.sync.force_push_latest or 0)

            if dry_run:
                detail = self._git(["diff", "--cached", "--stat"], cwd=self.workdir)
                if keep > 0:
                    detail += "\n" + t("history limit: keep at most %d commit(s) (force-push enabled)", keep)
                self._reset_to_remote()   # restore the work copy, leave no dry-run traces
                result.ok = True
                result.dry_run = True
                result.changed, result.deleted, result.detail = changed, deleted, detail
                result.finished_at = dt.datetime.now()
                return result

            commit = self._commit(changed, deleted) if changed or deleted else None
            rewritten = self._truncate_history(keep) if keep > 0 else False
            if commit is None and not rewritten:
                result.ok = True
                result.finished_at = dt.datetime.now()
                return result
            if rewritten:
                commit = self._git(["rev-parse", "--short", "HEAD"], cwd=self.workdir).strip()
            try:
                self._push(force=keep > 0)
            except GitError as exc:
                if attempt < attempts:
                    self.log.warning(t("push rejected (the remote moved meanwhile), "
                                       "retry %d/%d: %s"), attempt, attempts - 1, exc)
                    del changed, deleted  # a retry must not retain the previous attempt's paths
                    continue
                raise
            result.ok = True
            result.changed, result.deleted, result.commit = changed, deleted, commit
            result.finished_at = dt.datetime.now()
            return result

        raise SyncError(t("push still failing after %d attempts", attempts))  # pragma: no cover

    def _iter_workdir_files(self) -> Iterator[str]:
        """Yield work-copy paths without collecting the entire tree or following links."""
        for dirpath, dirnames, filenames in os.walk(self.workdir):
            links = {name for name in dirnames if name != ".git"
                     and os.path.islink(os.path.join(dirpath, name))}
            dirnames[:] = [name for name in dirnames if name != ".git" and name not in links]
            for name in chain(filenames, sorted(links)):
                if name == ".git":
                    continue
                abspath = os.path.join(dirpath, name)
                yield os.path.relpath(abspath, self.workdir).replace(os.sep, "/")

    def _staged_changes(self) -> Tuple[List[str], List[str]]:
        output = self._git(["diff", "--cached", "--name-status", "--no-renames", "-z"], cwd=self.workdir)
        # Keep the complete public result, but avoid split/slice copies of the whole diff.
        changed, deleted = [], []
        for entry in re.finditer(r"([^\0]+)\0([^\0]+)\0", output):
            status, path = entry.groups()
            (deleted if status == "D" else changed).append(path)
        return changed, deleted
