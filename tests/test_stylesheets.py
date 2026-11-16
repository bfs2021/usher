"""app.css keeps only what every screen shares; each screen's styles live in a stylesheet named after it. Every
stylesheet is linked from index.html and cached by the service worker, or a screen would load unstyled."""

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
SHARED_LIMIT = 400


class StylesheetTests(unittest.TestCase):
    def test_app_css_keeps_well_clear_of_the_file_size_limit(self):
        lines = len((WEB / "app.css").read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(lines, SHARED_LIMIT, "move screen-specific styles into a stylesheet named after the screen")

    def test_every_stylesheet_is_linked_after_app_css_and_cached(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        linked = re.findall(r'<link rel="stylesheet" href="/([^"]+\.css)">', html)
        shell = (WEB / "sw.js").read_text(encoding="utf-8")
        shell = shell[shell.index("const SHELL") :].split("];")[0]
        cached = re.findall(r'"/([^"]+\.css)"', shell)
        self.assertEqual(linked[0], "app.css", "the shared styles come first")
        for path in sorted(WEB.glob("*.css")):
            self.assertIn(path.name, linked, f"{path.name} is not linked from index.html")
            self.assertIn(path.name, cached, f"{path.name} is not in the service worker's SHELL")
        self.assertEqual(sorted(linked), sorted(cached))


if __name__ == "__main__":
    unittest.main()
