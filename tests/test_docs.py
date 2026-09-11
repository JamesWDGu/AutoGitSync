"""Keep the small README and bilingual reference consistent with the application."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

import git_sync  # noqa: E402
import i18n  # noqa: E402


class DocumentationTest(unittest.TestCase):
    @staticmethod
    def config_table(path):
        text = path.read_text(encoding="utf-8")
        rows = re.findall(r'^\| `([A-Z][A-Z0-9_]*)` \| (`[^`]*`|required) \|', text, re.MULTILINE)
        return {name: default.strip("`") for name, default in rows}

    def code_defaults(self):
        # No real mount is needed: this checks the default values, not filesystem validation.
        with mock.patch.object(git_sync.os.path, "isdir", return_value=True):
            cfg = git_sync.load_config({"GIT_REPO": "https://example.com/configs.git"})
        tree = ast.parse((ROOT / "app/git_sync.py").read_text(encoding="utf-8"))
        sections = {"GitConfig": cfg.git, "SyncConfig": cfg.sync, "ServerConfig": cfg.server}
        defaults = {}
        names = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id.startswith("_env_"):
                names.add(node.args[1].value)
            if node.func.id not in sections:
                continue
            for keyword in node.keywords:
                call = keyword.value
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) \
                        and call.func.id.startswith("_env_"):
                    defaults[call.args[1].value] = getattr(sections[node.func.id], keyword.arg)
        defaults["LOG_LEVEL"] = cfg.log_level
        self.assertEqual(set(defaults), names)
        defaults["GIT_REPO"] = "required"
        defaults["LOG_LANG"] = i18n.DEFAULT_LANGUAGE
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        defaults["TZ"] = re.search(r"\bTZ=(\S+)", dockerfile).group(1)
        return {name: ('""' if value == "" else str(value).lower() if isinstance(value, bool)
                       else str(value)) for name, value in defaults.items()}

    def test_full_configuration_tables_match_code(self):
        expected = self.code_defaults()
        for name in ("configuration.md", "configuration.zh-CN.md"):
            with self.subTest(document=name):
                self.assertEqual(self.config_table(ROOT / "docs" / name), expected)

    def test_readme_defaults_are_an_accurate_subset(self):
        expected = self.code_defaults()
        tables = []
        for name in ("README.md", "README.zh-CN.md"):
            table = self.config_table(ROOT / name)
            self.assertIn("GIT_REPO", table)
            self.assertLessEqual(len(table), 8)
            for key, value in table.items():
                self.assertEqual(value, expected[key], "%s: %s" % (name, key))
            tables.append(table)
        self.assertEqual(tables[0], tables[1])

    def test_local_documentation_links_exist(self):
        paths = list(ROOT.glob("README*.md")) + list(ROOT.glob("CONTRIBUTING*.md"))
        paths += list((ROOT / "docs").glob("*.md"))
        paths += list((ROOT / ".agents/skills").rglob("*.md"))
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#"):
                    continue
                target = target.split("#", 1)[0]
                with self.subTest(document=path.name, target=target):
                    self.assertTrue((path.parent / target).exists())

    def test_bilingual_command_examples_match(self):
        for name in ("README", "CONTRIBUTING", "docs/configuration", "docs/usage"):
            english = (ROOT / (name + ".md")).read_text(encoding="utf-8")
            chinese = (ROOT / (name + ".zh-CN.md")).read_text(encoding="utf-8")
            pattern = r"```([^\n]*)\n(.*?)```"
            with self.subTest(document=name):
                self.assertEqual(re.findall(pattern, english, re.DOTALL),
                                 re.findall(pattern, chinese, re.DOTALL))


if __name__ == "__main__":
    unittest.main()
