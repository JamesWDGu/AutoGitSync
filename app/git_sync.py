"""AutoGitSync 核心：环境变量配置 + Git 同步引擎。

配置**全部来自环境变量**，没有任何配置文件。只有 ``GIT_REPO`` 是必填的，
其余都有合理默认值（见 README 的环境变量表）。

同步语义（每轮同步都遵循同一套流程）：

1. 把本地工作副本重置为远端分支的最新状态 —— 远端只是「目的地」，不是真相来源；
2. 扫描 ``SOURCE_DIR`` 目录，把匹配 ``INCLUDE`` / ``EXCLUDE`` 的文件
   按**原有相对路径**覆盖进工作副本；
3. 远端存在、本地不存在、且匹配规则的文件被删除（``DELETE_MISSING=true`` 时）；
4. 提交并推送；若推送时远端已前进（非快进），则重新拉取并重放本地文件后重试
   —— 即**冲突一律以本地为准**，且不会强推、不丢远端历史。

安全性：``SOURCE_DIR`` 目录不存在会导致启动校验失败；当该目录中一个匹配文件都没有、
而远端却存在被管理的文件时，默认拒绝执行删除（避免一次误挂载把仓库清空）。
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
from typing import Dict, List, Mapping, Optional, Pattern, Tuple

__all__ = [
    "Config", "ConfigError", "GitConfig", "GitError", "GitSync",
    "ServerConfig", "SyncConfig", "SyncError", "SyncResult", "load_config",
    "parse_interval", "parse_listen",
]


class ConfigError(ValueError):
    """配置（环境变量）非法。"""


class SyncError(RuntimeError):
    """同步过程出错。"""


class GitError(SyncError):
    """git 命令执行失败。"""


# --------------------------------------------------------------------------
# 配置
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
    """把 ``30s`` / ``5m`` / ``2h`` / ``30``（默认秒）解析为秒数。"""
    match = _DURATION_RE.match(str(text))
    if not match:
        raise ConfigError("周期格式非法：%r（示例：30s / 5m / 2h / 1d）" % (text,))
    value = float(match.group(1))
    seconds = value * _DURATION_UNITS[(match.group(2) or "s").lower()]
    if seconds <= 0:
        raise ConfigError("周期必须大于 0：%r" % (text,))
    return seconds


def parse_listen(value: str) -> Tuple[str, int]:
    """解析 ``host:port``；端口为空或 0 表示关闭该端点。"""
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
        raise ConfigError("端口非法：%r（示例：0.0.0.0:8080，留空表示关闭）" % (value,))
    if port < 0 or port > 65535:
        raise ConfigError("端口超出范围：%r" % (value,))
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
    raise ConfigError("%s 必须是布尔值（true/false），当前是 %r" % (name, value))


def _env_int(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    try:
        number = int(value.strip())
    except ValueError:
        raise ConfigError("%s 必须是整数，当前是 %r" % (name, value))
    if number < minimum:
        raise ConfigError("%s 不能小于 %d，当前是 %d" % (name, minimum, number))
    return number


def load_config(environ: Optional[Mapping[str, str]] = None) -> Config:
    """从环境变量读取并校验配置。

    只有 ``GIT_REPO`` 是必填项（对接私有仓库还需要 ``GIT_TOKEN``），
    其余都有默认值。``environ`` 仅用于测试注入，默认取 ``os.environ``。
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
        schedule=_env_str(env, "SCHEDULE"),
        interval=_env_str(env, "INTERVAL"),
    )
    server_cfg = ServerConfig(
        listen=_env_str(env, "LISTEN", "0.0.0.0:8080"),
        api_token=_env_str(env, "API_TOKEN"),
    )
    log_level = _env_str(env, "LOG_LEVEL", "INFO").upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ConfigError("LOG_LEVEL 取值非法：%r" % log_level)

    cfg = Config(git=git_cfg, sync=sync_cfg, server=server_cfg, log_level=log_level)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    from cron import Cron, CronError  # 延迟导入，避免解析器与配置互相牵连

    if not cfg.git.url:
        raise ConfigError("必须设置环境变量 GIT_REPO（Git 仓库地址）")
    if not cfg.git.branch:
        raise ConfigError("GIT_BRANCH 不能为空")
    if not cfg.sync.source:
        raise ConfigError("SOURCE_DIR 不能为空")
    cfg.sync.source = os.path.abspath(os.path.expanduser(cfg.sync.source))
    cfg.sync.workdir = os.path.abspath(os.path.expanduser(cfg.sync.workdir))

    if not os.path.isdir(cfg.sync.source):
        raise ConfigError("SOURCE_DIR 不是一个已存在的目录：%s（记得把目录挂载进来）"
                          % cfg.sync.source)

    try:
        cfg.include_re = re.compile(cfg.sync.include)
    except re.error as exc:
        raise ConfigError("INCLUDE 不是合法正则：%s（%s）" % (cfg.sync.include, exc))
    if cfg.sync.exclude:
        try:
            cfg.exclude_re = re.compile(cfg.sync.exclude)
        except re.error as exc:
            raise ConfigError("EXCLUDE 不是合法正则：%s（%s）" % (cfg.sync.exclude, exc))

    source = cfg.sync.source.rstrip(os.sep)
    workdir = cfg.sync.workdir.rstrip(os.sep)
    if source == workdir:
        raise ConfigError("SOURCE_DIR 与 REPO_DIR 不能是同一个目录：%s" % source)
    if source.startswith(workdir + os.sep) or workdir.startswith(source + os.sep):
        raise ConfigError("SOURCE_DIR 与 REPO_DIR 不能互相嵌套：%s / %s" % (source, workdir))

    if cfg.sync.schedule:
        try:
            Cron(cfg.sync.schedule)
        except CronError as exc:
            raise ConfigError("SCHEDULE 不是合法的 cron 表达式：%s" % exc)
    elif cfg.sync.interval:
        try:
            parse_interval(cfg.sync.interval)
        except ConfigError as exc:
            raise ConfigError("INTERVAL %s" % exc)
    else:
        cfg.sync.interval = "5m"   # 既没给 cron 也没给间隔时的默认周期

    try:
        parse_listen(cfg.server.listen)   # 端口非法时在启动阶段就报错，而不是等健康检查才发现
    except ConfigError as exc:
        raise ConfigError("LISTEN %s" % exc)


# --------------------------------------------------------------------------
# 同步结果
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
            return "失败：%s" % self.error
        if self.dry_run:
            return "试运行：新增/更新 %d，删除 %d" % (len(self.changed), len(self.deleted))
        if self.commit is None:
            return "无变更"
        return "提交 %s：新增/更新 %d，删除 %d，耗时 %.1fs" % (
            self.commit, len(self.changed), len(self.deleted), self.duration)

    @classmethod
    def failure(cls, error: str, started_at: Optional[dt.datetime] = None) -> "SyncResult":
        return cls(ok=False, error=error, started_at=started_at or dt.datetime.now(),
                   finished_at=dt.datetime.now())


# --------------------------------------------------------------------------
# 同步引擎
# --------------------------------------------------------------------------
def _build_auth_url(cfg: GitConfig) -> str:
    """把 token 注入 https 地址；非 http(s)（本地路径 / file:// / ssh）原样返回。"""
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
    def __missing__(self, key: str) -> str:  # 模板里出现未知占位符时保持原样
        return "{%s}" % key


class GitSync:
    """把本地目录同步到 Git 仓库的引擎（一次调用 = 一轮完整同步）。"""

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

    # -- 基础工具 -----------------------------------------------------------
    def _redact(self, text: Optional[str]) -> str:
        if not text:
            return ""
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def _git_rc(self, args: List[str], cwd: Optional[str] = None) -> Tuple[int, str]:
        env = os.environ.copy()
        env.update({
            "GIT_TERMINAL_PROMPT": "0",   # 认证失败立刻退出，而不是挂起等待输入
            "GIT_ASKPASS": "/bin/true",
            "GCM_INTERACTIVE": "never",
            "LC_ALL": "C",
        })
        command = ["git"]
        if cwd:
            command += ["-C", cwd]
        command += list(args)
        try:
            proc = subprocess.run(command, capture_output=True, text=True, env=env)
        except FileNotFoundError:
            raise GitError("未找到 git 可执行文件，请确认镜像中已安装 git")
        output = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, self._redact(output.strip())

    def _git(self, args: List[str], cwd: Optional[str] = None) -> str:
        code, output = self._git_rc(args, cwd=cwd)
        if code != 0:
            action = " ".join(a for a in args[:2] if not a.startswith("-"))
            raise GitError("git %s 执行失败（exit %d）：%s" % (action, code, output or "无输出"))
        return output

    def _managed(self, relpath: str) -> bool:
        """相对路径是否落在同步范围内。"""
        if not self.include_re.search(relpath):
            return False
        if self.exclude_re and self.exclude_re.search(relpath):
            return False
        return True

    # -- 工作副本准备 -------------------------------------------------------
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
        """让工作副本回到「远端分支最新状态」这个干净起点。"""
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
                self.log.warning("远端分支 %s 不存在，将在本次推送时创建", self.cfg.git.branch)
                self._git_rc(["update-ref", "-d", remote_ref], cwd=self.workdir)  # 清掉过期引用
        self._reset_to_remote()

    def _clone_or_init(self) -> None:
        parent = os.path.dirname(self.workdir)
        os.makedirs(parent, exist_ok=True)
        if os.path.isdir(self.workdir):
            leftovers = os.listdir(self.workdir)
            if leftovers:
                raise SyncError("工作目录非空且不是 git 仓库，请清理后重试：%s" % self.workdir)
        else:
            os.makedirs(self.workdir)

        if self._remote_branch_exists():
            self.log.info("首次运行：克隆 %s（分支 %s）", self.cfg.git.url, self.cfg.git.branch)
            self._git(["clone", "--quiet", "--branch", self.cfg.git.branch,
                       "--single-branch", self.auth_url, self.workdir])
        else:
            self.log.info("首次运行：远端暂无分支 %s，初始化空仓库", self.cfg.git.branch)
            self._git(["init", "--quiet", self.workdir])
            self._git(["remote", "add", "origin", self.auth_url], cwd=self.workdir)

    def _has_head(self) -> bool:
        code, _ = self._git_rc(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=self.workdir)
        return code == 0

    def _reset_to_remote(self) -> None:
        if self._has_head():
            self._git(["reset", "--hard", "--quiet"], cwd=self.workdir)
        else:
            # 空仓库（还没有任何提交）时没有 HEAD 可重置，必须手动清空索引，
            # 否则上一轮试运行/失败的暂存内容会残留到下一轮。
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

    # -- 本地目录 -> 工作副本 ----------------------------------------------
    def scan_source(self) -> Dict[str, str]:
        """返回 ``相对路径 -> 本地绝对路径``（已按 include/exclude 过滤）。"""
        desired: Dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(self.source):
            dirnames[:] = sorted(d for d in dirnames if d != ".git")
            for name in sorted(filenames):
                abspath = os.path.join(dirpath, name)
                relpath = os.path.relpath(abspath, self.source).replace(os.sep, "/")
                if self._managed(relpath):
                    desired[relpath] = abspath
        return desired

    def _ensure_parents(self, relpath: str) -> None:
        """保证目标路径的父目录存在；若父级位置上是一个文件，先删掉它。"""
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

    def _overlay(self, desired: Dict[str, str]) -> List[str]:
        """把本地文件覆盖到工作副本，返回实际发生变化的相对路径。"""
        changed: List[str] = []
        for relpath, source in sorted(desired.items()):
            target = os.path.join(self.workdir, relpath)
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)          # 远端是目录，本地是文件
            elif (os.path.isfile(target) and filecmp.cmp(source, target, shallow=False)
                  and self._same_exec_bit(source, target)):
                continue                        # 内容与可执行位都一致，无需变更
            self._ensure_parents(relpath)
            shutil.copy2(source, target)
            changed.append(relpath)
        return changed

    def _prune(self, desired: Dict[str, str]) -> List[str]:
        """删除远端存在、本地已不存在且匹配规则的文件。"""
        if not self.cfg.sync.delete_missing:
            return []
        deleted: List[str] = []
        for dirpath, dirnames, filenames in os.walk(self.workdir):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in filenames:
                abspath = os.path.join(dirpath, name)
                relpath = os.path.relpath(abspath, self.workdir).replace(os.sep, "/")
                if relpath in desired or not self._managed(relpath):
                    continue
                try:
                    os.remove(abspath)
                except OSError as exc:
                    raise SyncError("删除 %s 失败：%s" % (relpath, exc))
                deleted.append(relpath)

        # 清掉因删除而变空的目录（git 本身不跟踪空目录）
        for dirpath, _dirnames, _filenames in os.walk(self.workdir, topdown=False):
            if dirpath == self.workdir or ".git" in os.path.relpath(dirpath, self.workdir).split(os.sep):
                continue
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
            except OSError:
                pass
        return deleted

    # -- 提交与推送 ---------------------------------------------------------
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
        self._git(["add", "-A", "--", "."], cwd=self.workdir)
        code, _ = self._git_rc(["diff", "--cached", "--quiet"], cwd=self.workdir)
        if code == 0:
            return None                          # 与远端完全一致
        self._git(["-c", "user.name=%s" % self.cfg.git.author_name,
                   "-c", "user.email=%s" % self.cfg.git.author_email,
                   "commit", "--quiet", "-m", self._commit_message(changed, deleted)],
                  cwd=self.workdir)
        return self._git(["rev-parse", "--short", "HEAD"], cwd=self.workdir).strip()

    def _push(self) -> None:
        self._git(["push", "--quiet", "origin",
                   "HEAD:refs/heads/%s" % self.cfg.git.branch], cwd=self.workdir)

    # -- 对外主流程 ---------------------------------------------------------
    def sync_once(self, dry_run: bool = False) -> SyncResult:
        """执行一轮同步。失败抛 :class:`SyncError`；``dry_run`` 下不做任何提交/推送。"""
        result = SyncResult(ok=False)
        attempts = max(1, int(self.cfg.git.push_retries) + 1)

        for attempt in range(1, attempts + 1):
            self._prepare_worktree()
            desired = self.scan_source()

            if not desired and self.cfg.sync.delete_missing and not self.cfg.sync.allow_empty:
                remote_managed = [
                    rel for rel in self._list_workdir_files() if self._managed(rel)
                ]
                if remote_managed:
                    raise SyncError(
                        "本地目录 %s 中没有任何匹配 %r 的文件，但远端有 %d 个受管文件；"
                        "为避免误删整个仓库已跳过本次同步（确认无误可设置 sync.allow_empty = true）"
                        % (self.source, self.cfg.sync.include, len(remote_managed)))

            changed = self._overlay(desired)
            deleted = self._prune(desired)
            self._git(["add", "-A", "--", "."], cwd=self.workdir)
            staged = self._staged_list()

            if dry_run:
                detail = self._git(["diff", "--cached", "--stat"], cwd=self.workdir)
                self._reset_to_remote()   # 还原工作副本，不留下试运行痕迹
                result.ok = True
                result.dry_run = True
                result.changed, result.deleted, result.detail = changed, deleted, detail
                result.finished_at = dt.datetime.now()
                return result

            if not staged:
                result.ok = True
                result.changed, result.deleted = [], []
                result.finished_at = dt.datetime.now()
                return result

            commit = self._commit(changed, deleted)
            try:
                self._push()
            except GitError as exc:
                if attempt < attempts:
                    self.log.warning("推送被拒绝（远端在此期间已更新），第 %d/%d 次重试：%s",
                                     attempt, attempts - 1, exc)
                    continue
                raise
            result.ok = True
            result.changed, result.deleted, result.commit = changed, deleted, commit
            result.finished_at = dt.datetime.now()
            return result

        raise SyncError("推送重试 %d 次后仍然失败" % attempts)  # pragma: no cover

    def _list_workdir_files(self) -> List[str]:
        files = []
        for dirpath, dirnames, filenames in os.walk(self.workdir):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in filenames:
                abspath = os.path.join(dirpath, name)
                files.append(os.path.relpath(abspath, self.workdir).replace(os.sep, "/"))
        return files

    def _staged_list(self) -> List[str]:
        output = self._git(["diff", "--cached", "--name-only"], cwd=self.workdir)
        return [line.strip() for line in output.splitlines() if line.strip()]
