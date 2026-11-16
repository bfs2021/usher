"""On iPhone the tab bar must not ride up with the page or stay stuck away from the bottom edge. The page itself
never scrolls: the shell fills the screen, only the main area scrolls, and the tab bar sits in the shell's last row
instead of floating over a scrolling body."""

import re
import unittest
from pathlib import Path
from tests.webjs import client_source

CSS = Path("web/app.css").read_text()
JS = client_source()


def rule(selector, css=CSS):
    """The declarations of the first top-level rule whose selector is exactly `selector`."""
    match = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    if not match:
        raise AssertionError(f"no rule for {selector} in web/app.css")
    return match.group(1)


class TabBarPinned(unittest.TestCase):
    def test_page_itself_never_scrolls(self):
        self.assertIn("overflow: hidden", rule("html, body"))
        shell = rule(".shell")
        self.assertIn("position: fixed", shell)
        self.assertIn("inset: 0", shell)

    def test_only_the_main_area_scrolls(self):
        main = rule(".main")
        self.assertIn("overflow-y: auto", main)
        self.assertIn("overscroll-behavior-y: contain", main)

    def test_tab_bar_is_part_of_the_shell_not_floating(self):
        tabbar = rule(".tabbar")
        self.assertNotIn("position: fixed", tabbar)
        self.assertIn("env(safe-area-inset-bottom)", tabbar)
        self.assertIn("grid-template-rows: minmax(0, 1fr) auto", rule(".shell"))

    def test_nothing_sizes_itself_to_100vh(self):
        self.assertNotRegex(CSS, r"\b100vh\b")

    def test_scrolling_back_to_top_uses_the_main_area(self):
        self.assertNotRegex(JS, r"(?<![.\w])scrollTo\(")
        self.assertIn("this.main.scrollTo(", JS)


if __name__ == "__main__":
    unittest.main()
