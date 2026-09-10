"""环境变量配置、调度与健康端点测试。"""

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
from git_sync import ConfigError, load_config, parse_interval, parse_listen  # noqa: E402
from main import RuntimeState, Schedule, do_healthcheck, start_health_server  # noqa: E402

REPO = "https://example.com/me/configs.git"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class EnvConfigTest(unittest.TestCase):
    """配置全部来自环境变量，这里用注入的映射来验证。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ags-cfg-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = os.path.join(self.tmp, "source")
        os.makedirs(self.source, exist_ok=True)
        self.workdir = os.path.join(self.tmp, "repo")

    def load(self, **env):
        values = {"AGS_GIT_REPO": REPO, "AGS_SOURCE": self.source, "AGS_WORKDIR": self.workdir}
        values.update(env)
        return load_config({key: value for key, value in values.items() if value is not None})

    # -- 必填与默认值 -------------------------------------------------------
    def test_only_repo_is_required(self):
        cfg = load_config({"AGS_GIT_REPO": REPO, "AGS_SOURCE": self.source,
                           "AGS_WORKDIR": self.workdir})
        self.assertEqual(cfg.git.url, REPO)
        self.assertEqual(cfg.git.branch, "main")
        self.assertEqual(cfg.git.token, "")
        self.assertEqual(cfg.git.username, "x-access-token")
        self.assertEqual(cfg.git.author_name, "AutoGitSync")
        self.assertEqual(cfg.git.author_email, "autogitsync@localhost")
        self.assertEqual(cfg.git.commit_message, "sync: {count} file(s) changed at {time}")
        self.assertEqual(cfg.git.push_retries, 3)
        self.assertEqual(cfg.sync.include, ".*")
        self.assertEqual(cfg.sync.exclude, "")
        self.assertTrue(cfg.sync.delete_missing)
        self.assertFalse(cfg.sync.allow_empty)
        self.assertTrue(cfg.sync.run_on_start)
        self.assertEqual(cfg.sync.interval, "5m")          # 既没给 cron 也没给间隔
        self.assertEqual(cfg.sync.schedule, "")
        self.assertEqual(cfg.server.listen, "0.0.0.0:8080")
        self.assertEqual(cfg.server.api_token, "")
        self.assertEqual(cfg.log_level, "INFO")
        self.assertIsNotNone(cfg.include_re.search("app/settings.conf"))

    def test_source_defaults_to_slash_source(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config({"AGS_GIT_REPO": REPO})
        self.assertIn("/source", str(ctx.exception))       # 提示里带上默认目录

    def test_missing_repo_is_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config({})
        self.assertIn("AGS_GIT_REPO", str(ctx.exception))

    # -- 全部可配置项 -------------------------------------------------------
    def test_every_value_can_come_from_env(self):
        cfg = self.load(
            AGS_GIT_BRANCH="prod",
            AGS_GIT_TOKEN="tok-123",
            AGS_GIT_USERNAME="oauth2",
            AGS_GIT_AUTHOR_NAME="bot",
            AGS_GIT_AUTHOR_EMAIL="bot@example.com",
            AGS_COMMIT_MESSAGE="sync {count}",
            AGS_PUSH_RETRIES="5",
            AGS_INCLUDE=r"\.conf$",
            AGS_EXCLUDE="^cache/",
            AGS_DELETE_MISSING="false",
            AGS_ALLOW_EMPTY="true",
            AGS_RUN_ON_START="false",
            AGS_SCHEDULE="*/10 * * * *",
            AGS_LISTEN="127.0.0.1:9999",
            AGS_API_TOKEN="api-tok",
            AGS_LOG_LEVEL="debug",
        )
        self.assertEqual(cfg.git.branch, "prod")
        self.assertEqual(cfg.git.token, "tok-123")
        self.assertEqual(cfg.git.username, "oauth2")
        self.assertEqual(cfg.git.author_email, "bot@example.com")
        self.assertEqual(cfg.git.commit_message, "sync {count}")
        self.assertEqual(cfg.git.push_retries, 5)
        self.assertEqual(cfg.sync.include, r"\.conf$")
        self.assertEqual(cfg.sync.exclude, "^cache/")
        self.assertFalse(cfg.sync.delete_missing)
        self.assertTrue(cfg.sync.allow_empty)
        self.assertFalse(cfg.sync.run_on_start)
        self.assertEqual(cfg.sync.schedule, "*/10 * * * *")
        self.assertEqual(cfg.server.listen, "127.0.0.1:9999")
        self.assertEqual(cfg.server.api_token, "api-tok")
        self.assertEqual(cfg.log_level, "DEBUG")
        self.assertIsNotNone(cfg.exclude_re.search("cache/x"))

    def test_values_are_trimmed(self):
        cfg = self.load(AGS_GIT_BRANCH="  dev  ", AGS_GIT_TOKEN=" tok ")
        self.assertEqual(cfg.git.branch, "dev")
        self.assertEqual(cfg.git.token, "tok")

    # -- 类型解析 -----------------------------------------------------------
    def test_bool_parsing(self):
        for text in ("1", "true", "TRUE", "yes", "on", " true "):
            with self.subTest(text=text):
                self.assertTrue(self.load(AGS_DELETE_MISSING=text).sync.delete_missing)
        for text in ("0", "false", "no", "off", "", "  "):
            with self.subTest(text=text):
                self.assertFalse(self.load(AGS_DELETE_MISSING=text).sync.delete_missing)
        with self.assertRaises(ConfigError) as ctx:
            self.load(AGS_DELETE_MISSING="maybe")
        self.assertIn("AGS_DELETE_MISSING", str(ctx.exception))

    def test_int_parsing(self):
        self.assertEqual(self.load(AGS_PUSH_RETRIES="7").git.push_retries, 7)
        self.assertEqual(self.load(AGS_PUSH_RETRIES="").git.push_retries, 3)
        for bad in ("abc", "-1", "1.5"):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError) as ctx:
                    self.load(AGS_PUSH_RETRIES=bad)
                self.assertIn("AGS_PUSH_RETRIES", str(ctx.exception))

    def test_bad_log_level_is_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load(AGS_LOG_LEVEL="chatty")
        self.assertIn("AGS_LOG_LEVEL", str(ctx.exception))

    # -- 校验 ---------------------------------------------------------------
    def test_bad_regex_is_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load(AGS_INCLUDE="([")
        self.assertIn("AGS_INCLUDE", str(ctx.exception))
        with self.assertRaises(ConfigError) as ctx:
            self.load(AGS_EXCLUDE="([")
        self.assertIn("AGS_EXCLUDE", str(ctx.exception))

    def test_bad_cron_and_interval_are_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load(AGS_SCHEDULE="61 * * * *")
        self.assertIn("AGS_SCHEDULE", str(ctx.exception))
        with self.assertRaises(ConfigError) as ctx:
            self.load(AGS_INTERVAL="soon")
        self.assertIn("AGS_INTERVAL", str(ctx.exception))

    def test_bad_listen_is_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load(AGS_LISTEN="127.0.0.1:abc")
        self.assertIn("AGS_LISTEN", str(ctx.exception))

    def test_missing_source_dir_is_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load(AGS_SOURCE=os.path.join(self.tmp, "nope"))
        self.assertIn("AGS_SOURCE", str(ctx.exception))

    def test_source_and_workdir_must_not_overlap(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load(AGS_WORKDIR=os.path.join(self.source, "repo"))
        self.assertIn("嵌套", str(ctx.exception))
        with self.assertRaises(ConfigError):
            self.load(AGS_WORKDIR=self.source)

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
        self.assertEqual(parse_listen("0.0.0.0:0"), ("0.0.0.0", 0))
        with self.assertRaises(ConfigError):
            parse_listen("127.0.0.1:abc")


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ags-sched-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = os.path.join(self.tmp, "source")
        os.makedirs(self.source, exist_ok=True)

    def schedule(self, **env):
        values = {"AGS_GIT_REPO": REPO, "AGS_SOURCE": self.source,
                  "AGS_WORKDIR": os.path.join(self.tmp, "repo")}
        values.update(env)
        return Schedule(load_config(values))

    def test_cron_schedule(self):
        schedule = self.schedule(AGS_SCHEDULE="*/10 * * * *")
        nxt = schedule.next_after(main_module.dt.datetime(2024, 5, 6, 13, 3))
        self.assertEqual(nxt, main_module.dt.datetime(2024, 5, 6, 13, 10))
        self.assertIn("cron", schedule.describe())

    def test_interval_schedule(self):
        schedule = self.schedule(AGS_INTERVAL="90s")
        now = main_module.dt.datetime(2024, 5, 6, 13, 3)
        self.assertEqual(schedule.next_after(now), now + main_module.dt.timedelta(seconds=90))
        self.assertIn("秒", schedule.describe())
        self.assertIn("分钟", self.schedule(AGS_INTERVAL="5m").describe())

    def test_schedule_wins_over_interval(self):
        schedule = self.schedule(AGS_SCHEDULE="0 3 * * *", AGS_INTERVAL="1s")
        self.assertIsNotNone(schedule.cron)
        self.assertIsNone(schedule.interval)

    def test_interval_default(self):
        self.assertEqual(self.schedule().interval, 300.0)


class HealthEndpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ags-health-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.source = os.path.join(self.tmp, "source")
        os.makedirs(self.source, exist_ok=True)

    def start(self, api_token=""):
        port = free_port()
        cfg = load_config({"AGS_GIT_REPO": REPO, "AGS_SOURCE": self.source,
                           "AGS_WORKDIR": os.path.join(self.tmp, "repo"),
                           "AGS_LISTEN": "127.0.0.1:%d" % port, "AGS_API_TOKEN": api_token})
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

    def test_healthcheck_reads_ags_listen(self):
        port, _, _ = self.start()
        with mock.patch.dict(os.environ, {"AGS_LISTEN": "127.0.0.1:%d" % port}, clear=False):
            self.assertEqual(do_healthcheck(), 0)

    def test_healthcheck_follows_custom_port(self):
        """端口不是默认的 8080 时也要探测正确（曾经这里会导致容器永远 unhealthy）。"""
        port, _, _ = self.start()
        self.assertNotEqual(port, 8080)
        with mock.patch.dict(os.environ, {"AGS_LISTEN": "0.0.0.0:%d" % port}, clear=False):
            self.assertEqual(do_healthcheck(), 0)

    def test_healthcheck_against_dead_port(self):
        with mock.patch.dict(os.environ, {"AGS_LISTEN": "127.0.0.1:%d" % free_port()}, clear=False):
            self.assertEqual(do_healthcheck(), 1)

    def test_healthcheck_skipped_when_endpoint_disabled(self):
        buffer = io.StringIO()
        with mock.patch.dict(os.environ, {"AGS_LISTEN": ""}, clear=False):
            with contextlib.redirect_stdout(buffer):
                code = do_healthcheck()
        self.assertEqual(code, 0)
        self.assertIn("未启用", buffer.getvalue())

    def test_healthcheck_rejects_bad_listen(self):
        with mock.patch.dict(os.environ, {"AGS_LISTEN": "127.0.0.1:abc"}, clear=False):
            self.assertEqual(do_healthcheck(), 1)


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
        patch = mock.patch.dict(os.environ, {
            "AGS_GIT_REPO": REPO,
            "AGS_SOURCE": self.source,
            "AGS_WORKDIR": os.path.join(self.tmp, "repo"),
            "AGS_INCLUDE": r"\.conf$",
            "AGS_SCHEDULE": "@hourly",
            "AGS_LISTEN": "",
        }, clear=False)
        patch.start()
        self.addCleanup(patch.stop)

    def test_check_subcommand(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main_module.main(["--check"])
        output = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("AGS_GIT_REPO", output)
        self.assertIn("app/settings.conf", output)
        self.assertIn("匹配文件: 1 个", output)
        self.assertIn("接下来 5 次", output)
        self.assertIn("AGS_GIT_TOKEN    = 未设置", output)

    def test_check_without_repo_exits_2(self):
        with mock.patch.dict(os.environ, {"AGS_GIT_REPO": ""}, clear=False), \
                contextlib.redirect_stderr(io.StringIO()):
            code = main_module.main(["--check"])
        self.assertEqual(code, 2)

    def test_check_ignores_unknown_env_vars(self):
        """配置只认 AGS_* 前缀，其它环境变量不应干扰。"""
        with mock.patch.dict(os.environ, {"INCLUDE": "nope", "SOURCE": "/tmp"}, clear=False):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(main_module.main(["--check"]), 0)
        self.assertIn("app/settings.conf", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
