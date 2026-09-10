"""Message language tests: English by default, Chinese via LOG_LANG."""

import ast
import contextlib
import io
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

import i18n  # noqa: E402
import main as main_module  # noqa: E402
from git_sync import ConfigError, load_config  # noqa: E402


class CatalogTest(unittest.TestCase):
    """The Chinese catalog must cover every t("...") template in the sources."""

    def t_templates(self):
        templates = set()
        for path in sorted(APP_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                if not (isinstance(func, ast.Name) and func.id == "t"):
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    templates.add(first.value)
        return templates

    def test_every_template_has_a_translation(self):
        missing = sorted(self.t_templates() - set(i18n._ZH))
        self.assertEqual(missing, [], "missing Chinese translation for: %r" % (missing,))

    def test_catalog_has_no_untouched_placeholders(self):
        """翻译里保留的占位符必须和英文模板一致（否则格式化会抛异常）。"""
        for english, chinese in i18n._ZH.items():
            self.assertEqual(
                sorted(self.placeholders(english)), sorted(self.placeholders(chinese)),
                "placeholder mismatch for %r" % english)

    @staticmethod
    def placeholders(text):
        import re
        return re.findall(r"%[-+ #0-9.]*[diouxXeEfFgGcrsa]", text)


class LanguageSwitchTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_english_is_the_default(self):
        self.assertEqual(i18n.set_language(""), "en")
        self.assertEqual(i18n.set_language("en"), "en")
        self.assertEqual(i18n.set_language("fr"), "en")          # unknown -> English
        self.assertEqual(i18n.set_language("zh"), "zh")
        self.assertEqual(i18n.language(), "zh")

    def test_accepts_locale_style_values(self):
        for value in ("zh", "zh-CN", "zh_CN", "ZH", " zh-cn "):
            with self.subTest(value=value):
                self.assertEqual(i18n.set_language(value), "zh")

    def test_unknown_keys_fall_back_to_the_message_itself(self):
        i18n.set_language("zh")
        self.assertEqual(i18n.t("not in the catalog"), "not in the catalog")

    def test_formatting_arguments_are_applied(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("got %d of %s", 2, "x"), "got 2 of x")
        i18n.set_language("zh")
        self.assertEqual(i18n.t("failed: %s", "boom"), "失败：boom")


class LanguageIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ags-i18n-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        source = os.path.join(self.tmp, "source")
        os.makedirs(source, exist_ok=True)
        self.cleanup = self.push_env({
            "GIT_REPO": "https://example.com/me/configs.git",
            "SOURCE_DIR": source,
            "REPO_DIR": os.path.join(self.tmp, "repo"),
            "LISTEN": "",
        })

    def push_env(self, values):
        previous = {key: os.environ.get(key) for key in values}
        os.environ.update(values)
        return previous

    def tearDown(self):
        i18n.set_language("en")
        for key, value in self.cleanup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def check_output(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main_module.main(["--check"])
        self.assertEqual(code, 0)
        return buffer.getvalue()

    def test_error_messages_follow_the_language(self):
        os.environ.pop("GIT_REPO", None)
        i18n.set_language("en")
        with self.assertRaises(ConfigError) as ctx:
            load_config()
        self.assertIn("GIT_REPO is required", str(ctx.exception))

        i18n.set_language("zh")
        with self.assertRaises(ConfigError) as ctx:
            load_config()
        self.assertIn("必须设置环境变量 GIT_REPO", str(ctx.exception))

    def test_log_lang_env_var_switches_check_output(self):
        os.environ["LOG_LANG"] = "en"
        self.assertIn("effective configuration", self.check_output())
        self.assertIn("not set", self.check_output())

        os.environ["LOG_LANG"] = "zh"
        output = self.check_output()
        self.assertIn("当前生效的配置", output)
        self.assertIn("未设置", output)

        os.environ["LOG_LANG"] = "zh-CN"       # locale style also works
        self.assertIn("当前生效的配置", self.check_output())

    def test_default_is_english(self):
        os.environ.pop("LOG_LANG", None)
        output = self.check_output()
        self.assertIn("effective configuration", output)
        self.assertNotIn("当前生效", output)


if __name__ == "__main__":
    unittest.main()
