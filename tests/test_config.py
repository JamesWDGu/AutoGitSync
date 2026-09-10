"""配置加载、调度与健康端点测试。"""

import contextlib
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import main as main_module  # noqa: E402
from git_sync import ConfigError, load_config, parse_interval  # noqa: E402
from main import RuntimeState, Schedule, do_healthcheck, parse_listen, start_health_server  # noqa: E402

BASE_CONFIG = """
[git]
url = "https://example.com/me/configs.git"
branch = "main"
token = "${TEST_GIT_TOKEN}"
author_name = "bot"
author_email = "bot@example.com"

[sync]
source = "{source}"
include = '\\.(conf|ya?ml)$'
exclude = '(^|/)secret/'
delete_missing = true
workdir = "{workdir}"
schedule = "*/5 * * * *"
run_on_start = false

[server]
listen = "127.0.0.1:0"

[log]
level = "debug"
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _KeepPlaceholders(dict):
    """只替换 {source}/{workdir}，其余 ``{...}``（例如 ${ENV}）保持原样。"""

    def __missing__(self, key):
        return "{%s}" % key


class ConfigLoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ags-cfg-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = os.path.join(self.tmp, "source")
        os.makedirs(self.source, exist_ok=True)
        self.workdir = os.path.join(self.tmp, "data", "repo")
        patcher = mock.patch.dict(os.environ, {"TEST_GIT_TOKEN": "tok-123"}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_config(self, text=None, **overrides):
        if text is None:
            body = BASE_CONFIG.format_map(_KeepPlaceholders(
                source=overrides.get("source", self.source),
                workdir=overrides.get("workdir", self.workdir)))
        else:
            body = text
        path = os.path.join(self.tmp, "config.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        return path

    def test_full_config_and_env_expansion(self):
        path = self.write_config()
        cfg = load_config(path)

        self.assertEqual(cfg.git.url, "https://example.com/me/configs.git")
        self.assertEqual(cfg.git.token, "tok-123")
        self.assertEqual(cfg.git.branch, "main")
        self.assertEqual(cfg.sync.source, os.path.abspath(self.source))
        self.assertEqual(cfg.sync.workdir, os.path.abspath(self.workdir))
        self.assertTrue(cfg.sync.delete_missing)
        self.assertFalse(cfg.sync.run_on_start)
        self.assertEqual(cfg.sync.schedule, "*/5 * * * *")
        self.assertEqual(cfg.log_level, "DEBUG")
        self.assertIsNotNone(cfg.include_re.search("app/settings.conf"))

    def test_env_default_value(self):
        path = self.write_config(
            text='[git]\nurl = "https://example.com/x.git"\ntoken = "${NOPE_MISSING:-fallback}"\n\n'
                 '[sync]\nsource = "%s"\ninterval = "1m"\n' % self.source)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOPE_MISSING", None)
            cfg = load_config(path)
        self.assertEqual(cfg.git.token, "fallback")

    def test_missing_env_without_default_raises(self):
        path = self.write_config(
            text='[git]\nurl = "https://example.com/x.git"\ntoken = "${NOPE_MISSING}"\n\n'
                 '[sync]\nsource = "%s"\n' % self.source)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOPE_MISSING", None)
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
        self.assertIn("NOPE_MISSING", str(ctx.exception))

    def test_default_interval_when_nothing_configured(self):
        path = self.write_config(
            text='[git]\nurl = "https://example.com/x.git"\n\n[sync]\nsource = "%s"\n' % self.source)
        cfg = load_config(path)
        self.assertEqual(cfg.sync.interval, "5m")
        self.assertEqual(cfg.sync.include, ".*")

    def test_unknown_key_is_rejected(self):
        path = self.write_config(
            text='[git]\nurl = "https://example.com/x.git"\n\n[sync]\nsource = "%s"\nnope = 1\n'
                 % self.source)
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("nope", str(ctx.exception))

    def test_unknown_section_is_rejected(self):
        path = self.write_config(text='[nope]\nx = 1\n')
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_bad_regex_is_rejected(self):
        path = self.write_config(
            text='[git]\nurl = "https://example.com/x.git"\n\n[sync]\nsource = "%s"\ninclude = "(["\n'
                 % self.source)
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("include", str(ctx.exception))

    def test_bad_cron_is_rejected(self):
        path = self.write_config(
            text='[git]\nurl = "https://example.com/x.git"\n\n[sync]\nsource = "%s"\n'
                 'schedule = "61 * * * *"\n' % self.source)
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("schedule", str(ctx.exception))

    def test_missing_source_is_rejected(self):
        path = self.write_config(source=os.path.join(self.tmp, "nope"))
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("sync.source", str(ctx.exception))

    def test_nested_source_and_workdir_rejected(self):
        path = self.write_config(workdir=os.path.join(self.source, "repo"))
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("嵌套", str(ctx.exception))

    def test_missing_file_is_rejected(self):
        with self.assertRaises(ConfigError):
            load_config(os.path.join(self.tmp, "not-there.toml"))

    def test_bad_listen_is_rejected(self):
        path = self.write_config(
            text='[git]\nurl = "https://example.com/x.git"\n\n[sync]\nsource = "%s"\n\n'
                 '[server]\nlisten = "127.0.0.1:abc"\n' % self.source)
        with self.assertRaises(ConfigError) as ctx:
            load_config(path)
        self.assertIn("server.listen", str(ctx.exception))

    def test_interval_parsing(self):
        self.assertEqual(parse_interval("30"), 30.0)
        self.assertEqual(parse_interval("30s"), 30.0)
        self.assertEqual(parse_interval("5m"), 300.0)
        self.assertEqual(parse_interval("2h"), 7200.0)
        self.assertEqual(parse_interval("1d"), 86400.0)
        for bad in ("", "abc", "0s", "-5m", "5x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError):
                    parse_interval(bad)

    def test_parse_listen(self):
        self.assertEqual(parse_listen("0.0.0.0:8080"), ("0.0.0.0", 8080))
        self.assertEqual(parse_listen("127.0.0.1:1234"), ("127.0.0.1", 1234))
        self.assertEqual(parse_listen("8080"), ("0.0.0.0", 8080))
        self.assertEqual(parse_listen(":9000"), ("0.0.0.0", 9000))
        self.assertEqual(parse_listen(""), ("", 0))
        with self.assertRaises(ConfigError):
            parse_listen("127.0.0.1:abc")


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ags-sched-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def config(self, **sync):
        from git_sync import Config, GitConfig, ServerConfig, SyncConfig

        sync_cfg = SyncConfig(source=self.tmp, **sync)
        return Config(git=GitConfig(url="https://example.com/x.git"), sync=sync_cfg,
                      server=ServerConfig(listen=""))

    def test_cron_schedule(self):
        schedule = Schedule(self.config(schedule="*/10 * * * *"))
        nxt = schedule.next_after(main_module.dt.datetime(2024, 5, 6, 13, 3))
        self.assertEqual(nxt, main_module.dt.datetime(2024, 5, 6, 13, 10))
        self.assertIn("cron", schedule.describe())

    def test_interval_schedule(self):
        schedule = Schedule(self.config(interval="90s"))
        now = main_module.dt.datetime(2024, 5, 6, 13, 3)
        self.assertEqual(schedule.next_after(now), now + main_module.dt.timedelta(seconds=90))
        self.assertIn("秒", schedule.describe())

        self.assertIn("分钟", Schedule(self.config(interval="5m")).describe())

    def test_interval_default(self):
        schedule = Schedule(self.config())
        self.assertEqual(schedule.interval, 300.0)


class HealthEndpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ags-health-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # 每个用例用独立的「指引文件」，避免相互干扰
        self.health_file = os.path.join(self.tmp, "health.pointer")
        patcher = mock.patch.dict(os.environ, {"AGS_HEALTH_FILE": self.health_file}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_config(self, listen, extra=""):
        path = os.path.join(self.tmp, "config.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('[git]\nurl = "https://example.com/x.git"\n\n'
                         '[sync]\nsource = "%s"\n\n[server]\nlisten = "%s"\n%s'
                         % (self.tmp, listen, extra))
        return path

    def start(self, api_token=""):
        from git_sync import Config, GitConfig, ServerConfig, SyncConfig

        port = free_port()
        cfg = Config(git=GitConfig(url="https://example.com/x.git"),
                     sync=SyncConfig(source=self.tmp),
                     server=ServerConfig(listen="127.0.0.1:%d" % port, api_token=api_token))
        state = RuntimeState()
        trigger = threading.Event()
        server = start_health_server(cfg, state, trigger)
        self.assertIsNotNone(server)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return port, state, trigger

    def get(self, url, method="GET", headers=None):
        request = urllib.request.Request(url, method=method, headers=headers or {})
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_and_status(self):
        port, state, _ = self.start()
        status, payload = self.get("http://127.0.0.1:%d/healthz" % port)
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")

        status, payload = self.get("http://127.0.0.1:%d/status" % port)
        self.assertEqual(status, 200)
        self.assertIsNone(payload["last_run"])
        self.assertEqual(payload["failures"], 0)

    def test_trigger_sync(self):
        port, _, trigger = self.start()
        status, payload = self.get("http://127.0.0.1:%d/sync" % port, method="POST")
        self.assertEqual(status, 202)
        self.assertTrue(trigger.is_set())

    def test_trigger_requires_token(self):
        port, _, trigger = self.start(api_token="s3cret")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("http://127.0.0.1:%d/sync" % port, method="POST")
        self.assertEqual(ctx.exception.code, 401)
        self.assertFalse(trigger.is_set())

        status, _ = self.get("http://127.0.0.1:%d/sync" % port, method="POST",
                             headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(status, 202)
        self.assertTrue(trigger.is_set())

    def test_unknown_path(self):
        port, _, _ = self.start()
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("http://127.0.0.1:%d/nope" % port)
        self.assertEqual(ctx.exception.code, 404)

    def test_healthcheck_command(self):
        port, _, _ = self.start()
        self.assertEqual(do_healthcheck(self.write_config("127.0.0.1:%d" % port)), 0)

    def test_healthcheck_uses_actual_port_written_by_daemon(self):
        """配置读不出来（例如 -c 指向别处、或配置在容器里的其他路径）时，
        也要能通过守护进程写下的实际监听地址探测成功。"""
        port, _, _ = self.start()
        with open(self.health_file, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "127.0.0.1:%d" % port)
        self.assertEqual(do_healthcheck(os.path.join(self.tmp, "not-there.toml")), 0)

    def test_stale_health_pointer_falls_back_to_config_port(self):
        """指引文件残留（上次进程被强杀）时，回退用配置里的端口判断。"""
        port, _, _ = self.start()
        with open(self.health_file, "w", encoding="utf-8") as handle:
            handle.write("127.0.0.1:%d\n" % free_port())
        self.assertEqual(do_healthcheck(self.write_config("127.0.0.1:%d" % port)), 0)

    def test_healthcheck_against_dead_port(self):
        with mock.patch.dict(os.environ, {"AGS_HEALTH_ADDR": "127.0.0.1:%d" % free_port()}):
            self.assertEqual(do_healthcheck(os.path.join(self.tmp, "missing.toml")), 1)

    def test_healthcheck_skipped_when_endpoint_disabled(self):
        # 端点被显式关闭时不应去探测 8080，否则容器会永远 unhealthy
        with mock.patch.dict(os.environ, {"AGS_HEALTH_ADDR": "127.0.0.1:%d" % free_port()}):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = do_healthcheck(self.write_config(""))
        self.assertEqual(code, 0)
        self.assertIn("未启用", buffer.getvalue())


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ags-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = os.path.join(self.tmp, "source")
        os.makedirs(os.path.join(self.source, "app"), exist_ok=True)
        with open(os.path.join(self.source, "app", "settings.conf"), "w", encoding="utf-8") as handle:
            handle.write("a=1\n")
        with open(os.path.join(self.source, "README.md"), "w", encoding="utf-8") as handle:
            handle.write("hi\n")

    def config_path(self):
        path = os.path.join(self.tmp, "config.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('[git]\nurl = "https://example.com/x.git"\nbranch = "main"\n\n'
                         "[sync]\nsource = \"%s\"\ninclude = '\\.conf$'\nschedule = \"@hourly\"\n"
                         '[server]\nlisten = ""\n' % self.source)
        return path

    def test_check_subcommand(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main_module.main(["--check", "-c", self.config_path()])
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("app/settings.conf", output)
        self.assertIn("匹配文件   : 1 个", output)
        self.assertIn("接下来 5 次", output)
        self.assertIn("token 未配置", output)

    def test_bad_config_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code = main_module.main(["--check", "-c", os.path.join(self.tmp, "nope.toml")])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
