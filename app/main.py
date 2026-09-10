#!/usr/bin/env python3
"""AutoGitSync 守护进程入口。

用法：
    python main.py --config /config/config.toml       # 常驻运行，按周期同步
    python main.py --once                             # 立即同步一次并退出
    python main.py --dry-run                          # 试运行，只显示将要发生的变更
    python main.py --check                            # 校验配置并打印同步计划
    python main.py --healthcheck                      # 探测健康端点（供 Docker HEALTHCHECK 使用）
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
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cron import Cron, CronError                                      # noqa: E402
from git_sync import (DEFAULT_CONFIG_PATH, Config, ConfigError,       # noqa: E402
                      GitSync, SyncError, SyncResult, load_config, parse_interval,
                      parse_listen)

VERSION = os.environ.get("AGS_VERSION") or "1.0.0"   # 镜像构建时由 CI 注入 git tag
LOG = logging.getLogger("autogitsync")


# --------------------------------------------------------------------------
# 日志
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
# 周期计算
# --------------------------------------------------------------------------
class Schedule:
    """同步周期：优先使用 cron 表达式，否则使用固定间隔。"""

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
            return "cron %r（容器本地时区，%s）" % (self.cron.expression, _local_tz_name())
        return "每 %s" % _human_interval(self.interval or 0)


def _local_tz_name() -> str:
    return dt.datetime.now().astimezone().tzname() or "本地时区"


def _human_interval(seconds: float) -> str:
    for unit, size in (("天", 86400.0), ("小时", 3600.0), ("分钟", 60.0)):
        if seconds >= size and abs(seconds % size) < 1e-9:
            return "%g %s" % (seconds / size, unit)
    return "%g 秒" % seconds


# --------------------------------------------------------------------------
# 运行状态（供健康端点读取）
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
# 健康端点
# --------------------------------------------------------------------------
def _make_handler(state: RuntimeState, trigger: threading.Event, api_token: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "AutoGitSync/" + VERSION
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:  # 降噪：健康探测不进 INFO 日志
            LOG.debug("health %s - %s", self.address_string(), fmt % args)

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 接口
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

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 接口
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
            self._send(202, {"status": "accepted", "message": "已请求立即同步"})

    return Handler


class _HealthServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# 运行中的实例把「实际监听地址」写在这里，供 --healthcheck（镜像内置 HEALTHCHECK）读取。
# 放在 /tmp 是因为容器内一定是可写的，且随容器生命周期自动消失。
DEFAULT_HEALTH_FILE = "/tmp/autogitsync.health"


def _health_pointer_path() -> str:
    return os.environ.get("AGS_HEALTH_FILE") or DEFAULT_HEALTH_FILE


def _write_health_pointer(address: str) -> None:
    path = _health_pointer_path()
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(address + "\n")
    except OSError as exc:
        LOG.debug("无法写入健康检查指引文件 %s：%s", path, exc)


def _read_health_pointer() -> str:
    try:
        with open(_health_pointer_path(), "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _clear_health_pointer() -> None:
    try:
        os.remove(_health_pointer_path())
    except OSError:
        pass


def start_health_server(cfg: Config, state: RuntimeState, trigger: threading.Event):
    host, port = parse_listen(cfg.server.listen)
    if not port:
        return None
    try:
        server = _HealthServer((host, port), _make_handler(state, trigger, cfg.server.api_token))
    except OSError as exc:
        LOG.warning("健康端点无法监听 %s:%d（%s），服务继续运行", host, port, exc)
        return None
    thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    thread.start()
    _write_health_pointer("127.0.0.1:%d" % server.server_address[1])
    LOG.info("健康端点已启动：http://%s:%d/healthz（/status 查看状态，POST /sync 立即同步）",
             host, port)
    return server


def do_healthcheck(cfg_text: str) -> int:
    """存活探测（供 Docker HEALTHCHECK 使用）。

    探测端口按优先级取：

    1. 运行中的实例启动时写下的**实际监听地址**（``AGS_HEALTH_FILE``）——
       这样即使配置文件不在默认位置、或把 ``server.listen`` 改成了别的端口，
       镜像内置的健康检查依然指向正确的端口；
    2. 配置文件里的 ``server.listen``（为空表示端点已关闭，直接判定健康）；
    3. 环境变量 ``AGS_HEALTH_ADDR``（配置文件读不出来时的兜底）。

    多个候选地址会依次探测，任意一个返回 200 即视为健康。
    """
    try:
        cfg = load_config(cfg_text)
    except ConfigError:
        cfg = None

    if cfg is not None:
        _, configured_port = parse_listen(cfg.server.listen)
        if not configured_port:
            print("健康端点未启用，跳过检查")
            return 0
    else:
        configured_port = 0

    candidates: List[int] = []
    for address in (_read_health_pointer(),
                    "127.0.0.1:%d" % configured_port if configured_port else ""):
        if not address:
            continue
        try:
            _, port = parse_listen(address)
        except ConfigError:
            continue
        if port and port not in candidates:
            candidates.append(port)
    if not candidates:
        try:
            _, port = parse_listen(os.environ.get("AGS_HEALTH_ADDR", "0.0.0.0:8080"))
        except ConfigError:
            port = 8080
        if port:
            candidates.append(port)

    last_error = "没有可探测的地址"
    for port in candidates:
        url = "http://127.0.0.1:%d/healthz" % port
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return 0
                last_error = "%s 返回 %d" % (url, response.status)
        except (urllib.error.URLError, OSError) as exc:
            last_error = "%s（%s）" % (url, exc)
    print("健康检查失败：%s" % last_error)
    return 1


# --------------------------------------------------------------------------
# 单实例保护
# --------------------------------------------------------------------------
def acquire_lock(workdir: str):
    """用文件锁避免两个进程同时操作同一份工作副本。"""
    lock_path = os.path.join(os.path.dirname(os.path.abspath(workdir)), ".autogitsync.lock")
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        handle = open(lock_path, "w", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise SyncError("无法锁定数据目录（可能已有实例在运行）：%s（%s）" % (lock_path, exc))
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


# --------------------------------------------------------------------------
# 运行模式
# --------------------------------------------------------------------------
def run_daemon(cfg: Config, schedule: Schedule) -> int:
    state = RuntimeState()
    stop = threading.Event()
    trigger = threading.Event()
    engine = GitSync(cfg, LOG)
    lock = acquire_lock(cfg.sync.workdir)  # 保证同一数据目录只有一个实例

    def on_signal(signum, _frame):
        LOG.info("收到信号 %s，停止调度（当前同步会先跑完）", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    server = start_health_server(cfg, state, trigger)
    LOG.info("AutoGitSync %s 启动：%s -> %s#%s，周期 %s",
             VERSION, cfg.sync.source, cfg.git.url, cfg.git.branch, schedule.describe())
    LOG.info("同步规则：include=%r exclude=%r delete_missing=%s workdir=%s",
             cfg.sync.include, cfg.sync.exclude, cfg.sync.delete_missing, cfg.sync.workdir)
    if cfg.git.url.startswith(("http://", "https://")) and not cfg.git.token:
        LOG.warning("git.url 是 http(s) 地址但未配置 token，私有仓库将无法推送")

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
                LOG.error("同步失败：%s", exc)
            except Exception as exc:  # 兜底：任何意外都不应让守护进程退出
                result = SyncResult.failure("%s: %s" % (type(exc).__name__, exc))
                LOG.exception("同步出现未预期错误")
            state.finish(result)
            if result.ok:
                LOG.info("同步完成：%s", result.summary)
            next_run = schedule.next_after(dt.datetime.now())
            state.set_next_run(next_run)
            LOG.info("下次同步时间：%s", next_run.strftime("%Y-%m-%d %H:%M:%S"))
            continue
        stop.wait(min(1.0, max(0.05, (next_run - now).total_seconds())))

    if server is not None:
        server.shutdown()
        server.server_close()
    _clear_health_pointer()
    lock.close()          # 释放单实例锁
    LOG.info("AutoGitSync 已停止")
    return 0


def run_once(cfg: Config, dry_run: bool) -> int:
    engine = GitSync(cfg, LOG)
    lock = None
    try:
        if not dry_run:
            lock = acquire_lock(cfg.sync.workdir)
        result = engine.sync_once(dry_run=dry_run)
    except (SyncError, ConfigError) as exc:
        LOG.error("同步失败：%s", exc)
        return 1
    finally:
        if lock is not None:
            lock.close()
    if dry_run and result.detail:
        print(result.detail)
    LOG.info("完成：%s", result.summary)
    return 0


def print_check(cfg: Config, schedule: Schedule) -> int:
    engine = GitSync(cfg, LOG)
    desired = engine.scan_source()
    total = sum(len(files) for _, _, files in os.walk(cfg.sync.source))
    print("配置文件   : %s" % cfg.path)
    print("Git 仓库   : %s#%s（token %s）"
          % (cfg.git.url, cfg.git.branch, "已配置" if cfg.git.token else "未配置"))
    print("本地目录   : %s" % cfg.sync.source)
    print("工作副本   : %s" % cfg.sync.workdir)
    print("匹配规则   : include=%r exclude=%r" % (cfg.sync.include, cfg.sync.exclude))
    print("匹配文件   : %d 个（目录内共 %d 个文件）" % (len(desired), total))
    for relpath in sorted(desired)[:20]:
        print("             - %s" % relpath)
    if len(desired) > 20:
        print("             …（其余 %d 个）" % (len(desired) - 20))
    print("删除缺失   : %s" % ("是" if cfg.sync.delete_missing else "否"))
    print("同步周期   : %s" % schedule.describe())
    print("启动即同步 : %s" % ("是" if cfg.sync.run_on_start else "否"))
    print("健康端点   : %s" % (cfg.server.listen or "未启用"))
    print("接下来 5 次:")
    moment = dt.datetime.now()
    for _ in range(5):
        moment = schedule.next_after(moment)
        print("             %s" % moment.strftime("%Y-%m-%d %H:%M:%S"))
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autogitsync",
        description="把本地目录按原有路径定时同步到 Git 仓库（冲突以本地为准）")
    parser.add_argument("-c", "--config", default=os.environ.get("AGS_CONFIG", DEFAULT_CONFIG_PATH),
                        help="配置文件路径（默认 %s，可用环境变量 AGS_CONFIG 指定）" % DEFAULT_CONFIG_PATH)
    parser.add_argument("--once", action="store_true", help="立即同步一次后退出")
    parser.add_argument("--dry-run", action="store_true", help="试运行：显示将要发生的变更，不提交不推送")
    parser.add_argument("--check", action="store_true", help="校验配置并打印同步计划后退出")
    parser.add_argument("--healthcheck", action="store_true", help="探测健康端点（供 Docker HEALTHCHECK 使用）")
    parser.add_argument("--log-level", default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="覆盖配置中的日志级别")
    parser.add_argument("--version", action="version", version="AutoGitSync " + VERSION)
    return parser


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.healthcheck:
        return do_healthcheck(args.config)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s", stream=sys.stderr)
        LOG.error("配置错误：%s", exc)
        return 2

    setup_logging(args.log_level or cfg.log_level)

    try:
        schedule = Schedule(cfg)
    except (CronError, ConfigError) as exc:
        LOG.error("周期配置错误：%s", exc)
        return 2

    if args.check:
        return print_check(cfg, schedule)
    try:
        if args.dry_run:
            return run_once(cfg, dry_run=True)
        if args.once:
            return run_once(cfg, dry_run=False)
        return run_daemon(cfg, schedule)
    except SyncError as exc:          # 例如另一个实例已占用数据目录
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
