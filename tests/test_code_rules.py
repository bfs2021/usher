"""Code rules from CONTRIBUTING.md -> Code style, the cheap ones checked here: no `var` in web/*.js, no bare `except:`
and no `except ...: pass` in usher/. A handled failure that is meant to be quiet is allowlisted below, with why."""

import ast
import re
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAR = re.compile(r"(?:^|[\s;{}(])var\s+[A-Za-z_$]", re.M)

QUIET_EXCEPTS = Counter(
    {  # (file, exception caught): how many `except ...: pass` it may have
        ("usher/files.py", "OSError"): 1,  # cleaning up a temporary file before re-raising the real error
        ("usher/files.py", "FileNotFoundError"): 1,  # make_private skips files that don't exist yet
        ("usher/replace.py", "(OSError, ValueError)"): 1,  # a missing or unreadable replacements file starts fresh
        ("usher/notify_routes.py", "(TypeError, ValueError)"): 1,  # a malformed time zone offset is ignored, the rest is saved
        ("usher/server.py", "(BrokenPipeError, ConnectionResetError, TimeoutError, OSError)"): 1,  # the client went away
    }
)


def var_declarations(source):
    """Lines that declare with `var`."""
    return [n for n, line in enumerate(source.splitlines(), 1) if not line.lstrip().startswith(("//", "*")) and VAR.search(line)]


def except_problems(source, path):
    """(bare excepts as line numbers, Counter of (path, exception) for handlers whose whole body is `pass`)."""
    bare, quiet = [], Counter()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            bare.append(node.lineno)
        elif all(isinstance(s, ast.Pass) for s in node.body):
            quiet[(path, ast.unparse(node.type))] += 1
    return bare, quiet


class CodeRulesTests(unittest.TestCase):
    def test_no_var_in_web_js(self):
        for path in sorted((ROOT / "web").glob("*.js")):
            self.assertEqual(var_declarations(path.read_text(encoding="utf-8")), [], f"{path.name} uses var")

    def test_no_bare_or_silent_except_in_usher(self):
        quiet = Counter()
        for path in sorted((ROOT / "usher").rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            bare, q = except_problems(path.read_text(encoding="utf-8"), rel)
            self.assertEqual(bare, [], f"{rel} has a bare except: on these lines")
            quiet += q
        over = {k: n for k, n in quiet.items() if n > QUIET_EXCEPTS[k]}
        self.assertEqual(over, {}, "except ...: pass swallows an error; handle it, let it raise, or allowlist it with why")

    def test_checks_catch_what_they_should(self):
        self.assertEqual(var_declarations("const a = 1;\nvar b = 2;\nfor (var i = 0;;) {}\n// var c\n"), [2, 3])
        self.assertEqual(var_declarations("el.style.color = 'var(--ink)';\nlet variant = 1;\n"), [])
        bare, quiet = except_problems("try:\n    x()\nexcept:\n    y()\n", "usher/x.py")
        self.assertEqual(bare, [3])
        bare, quiet = except_problems("try:\n    x()\nexcept ValueError:\n    pass\n", "usher/x.py")
        self.assertEqual((bare, quiet), ([], Counter({("usher/x.py", "ValueError"): 1})))
        bare, quiet = except_problems("try:\n    x()\nexcept ValueError:\n    log.warning('x')\n", "usher/x.py")
        self.assertEqual((bare, quiet), ([], Counter()))


class CodeRulesDocumentedTests(unittest.TestCase):
    def test_contributing_has_code_style_and_commit_format(self):
        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        section = re.search(r"^## Code style\n(.*?)(?=^## )", text, re.M | re.S)
        self.assertIsNotNone(section, "CONTRIBUTING.md has no Code style section")
        self.assertLessEqual(len(section.group(1).strip().splitlines()), 10, "keep the Code style section short")
        for rule in ("swallow an error", "secrets", "untrusted", "never `var`"):
            self.assertIn(rule, section.group(1))
        self.assertIn("`type(scope): summary`", text)
        self.assertIn(".github/pull_request_template.md", text)

    def test_pull_request_template_has_every_heading(self):
        template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
        for heading in ("## Summary", "## What changed", "## How to test"):
            self.assertIn(heading, template)
        self.assertIn("Fixes #", template)
        self.assertIn("deploy/compose.yaml", template)


if __name__ == "__main__":
    unittest.main()
