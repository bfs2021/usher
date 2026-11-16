"""The client has no build step, so nothing else would notice a syntax slip before a phone loads it.

JavaScript is parsed with Node (present on the CI runners; the test is skipped where Node is missing). HTML and
CSS are checked with the standard library: balanced tags, and balanced braces and strings.
"""

import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _TagBalance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.open_tags = []
        self.problems = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.open_tags.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.open_tags or self.open_tags[-1] != tag:
            self.problems.append(f"</{tag}> at line {self.getpos()[0]} closes {self.open_tags[-1:] or 'nothing'}")
            return
        self.open_tags.pop()


def html_problems(text):
    parser = _TagBalance()
    parser.feed(text)
    parser.close()
    return parser.problems + [f"<{tag}> is never closed" for tag in parser.open_tags]


def css_problems(text):
    """Unbalanced braces or parentheses, an unterminated string or comment."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S) if text.count("/*") == text.count("*/") else None
    if text is None:
        return ["a comment is never closed"]
    problems, depth, parens, quote = [], 0, 0, None
    for number, line in enumerate(text.splitlines(), 1):
        for ch in line:
            if quote:
                quote = None if ch == quote else quote
            elif ch in "\"'":
                quote = ch
            elif ch in "{}":
                depth += 1 if ch == "{" else -1
            elif ch in "()":
                parens += 1 if ch == "(" else -1
            if depth < 0 or parens < 0:
                problems.append(f"line {number}: closes something that was never opened")
                depth = parens = 0
        if quote:
            problems.append(f"line {number}: a string is never closed")
            quote = None
    if depth:
        problems.append("a block is never closed")
    return problems + (["a parenthesis is never closed"] if parens else [])


class WebSyntaxTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs Node to parse JavaScript")
    def test_every_script_parses_as_a_module(self):
        for path in sorted(WEB.glob("*.js")):
            run = subprocess.run(["node", "--input-type=module", "--check"], input=path.read_text(encoding="utf-8"), capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, f"{path.name}: {run.stderr.strip()}")

    def test_html_tags_are_balanced(self):
        self.assertEqual(html_problems((WEB / "index.html").read_text(encoding="utf-8")), [])

    def test_css_is_balanced(self):
        for path in sorted(WEB.glob("*.css")):
            self.assertEqual(css_problems(path.read_text(encoding="utf-8")), [], path.name)

    def test_the_checks_catch_slips(self):
        self.assertTrue(html_problems("<div><p>hi</div>"))
        self.assertTrue(html_problems("<div>"))
        self.assertEqual(html_problems("<div><br><img src=x></div>"), [])
        self.assertTrue(css_problems("a { color: red;"))
        self.assertTrue(css_problems("a { color: red; } }"))
        self.assertTrue(css_problems("a { content: 'x; }"))
        self.assertEqual(css_problems("a { content: '}'; } /* { */"), [])


if __name__ == "__main__":
    unittest.main()
