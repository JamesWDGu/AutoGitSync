"""The control CLI uses the running daemon, not a second sync engine."""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import main as main_module  # noqa: E402


class TriggerTest(unittest.TestCase):
    def setUp(self):
        self.state = main_module.RuntimeState()
        self.trigger = threading.Event()
        self.server = main_module._HealthServer(
            ("127.0.0.1", 0), main_module._make_handler(self.state, self.trigger, "control-token"))
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.env = {
            "LISTEN": "0.0.0.0:%d" % self.server.server_address[1],
            "API_TOKEN": "control-token",
        }

    def invoke(self, **overrides):
        env = dict(self.env, **overrides)
        output = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(output):
            code = main_module.main(["--trigger"])
        self.assertNotIn("control-token", output.getvalue())
        return code, output.getvalue()

    def test_trigger_uses_custom_port_and_token_without_git_config(self):
        with mock.patch.object(main_module, "GitSync") as engine:
            code, output = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("sync requested", output)
        self.assertTrue(self.trigger.is_set())
        engine.assert_not_called()

    def test_wrong_token_is_rejected(self):
        code, output = self.invoke(API_TOKEN="wrong")
        self.assertEqual(code, 1)
        self.assertIn("401", output)
        self.assertFalse(self.trigger.is_set())

    def test_disabled_endpoint_reports_offline_alternative(self):
        code, output = self.invoke(LISTEN="")
        self.assertEqual(code, 1)
        self.assertIn("--once", output)
        self.assertFalse(self.trigger.is_set())

    def test_invalid_port_is_configuration_error(self):
        code, _ = self.invoke(LISTEN="127.0.0.1:invalid")
        self.assertEqual(code, 2)

    def test_unreachable_endpoint_is_failure(self):
        with mock.patch.object(main_module.http.client.HTTPConnection, "request",
                               side_effect=OSError("connection refused")):
            code, output = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("daemon is running", output)

    def test_trigger_does_not_use_proxy(self):
        code, _ = self.invoke(HTTP_PROXY="http://127.0.0.1:1", http_proxy="http://127.0.0.1:1")
        self.assertEqual(code, 0)
        self.assertTrue(self.trigger.is_set())

    def test_trigger_does_not_forward_credentials_on_redirect(self):
        followed = threading.Event()

        class RedirectHandler(main_module.http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", "/capture")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                followed.set()
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server.RequestHandlerClass = RedirectHandler
        code, output = self.invoke()
        self.assertEqual(code, 1)
        self.assertIn("302", output)
        self.assertFalse(followed.is_set())

    def test_malformed_host_fails_without_traceback(self):
        code, _ = self.invoke(LISTEN="bad\nhost:8080")
        self.assertEqual(code, 1)

    def test_trigger_without_auth_when_server_has_no_token(self):
        self.server.RequestHandlerClass = main_module._make_handler(self.state, self.trigger, "")
        code, _ = self.invoke(API_TOKEN="")
        self.assertEqual(code, 0)
        self.assertTrue(self.trigger.is_set())

    def test_cli_modes_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            main_module.build_parser().parse_args(["--trigger", "--once"])
        self.assertEqual(exc.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
