"""Every list row and shelf card is made by one helper in web/rows.js, so a fix or design change is made once."""

import re
import unittest

from tests.webjs import WEB


class TitleRowTests(unittest.TestCase):
    def sources(self):
        return {p.name: p.read_text() for p in WEB.glob("*.js") if p.name != "sw.js"}

    def test_row_markup_appears_only_in_the_helper(self):
        for name, text in self.sources().items():
            if name != "rows.js":
                self.assertNotIn("row has-thumb", text, name)

    def test_card_markup_appears_only_in_the_helper(self):
        for name, text in self.sources().items():
            if name != "rows.js":
                self.assertNotRegex(text, r'class: "card"', name)

    def test_screens_use_the_helpers(self):
        src = self.sources()
        for name, helper, count in (
            ("home.js", "titleRow", 1),
            ("downloads.js", "titleRow", 2),
            ("search.js", "titleRow", 1),
            ("upcoming.js", "titleRow", 2),
            ("sheets.js", "titleRow", 1),
            ("home.js", "titleCard", 1),
            ("upcoming.js", "titleCard", 1),
            ("discover.js", "titleCard", 1),
        ):
            self.assertGreaterEqual(len(re.findall(r"\b%s\(" % helper, src[name])), count, f"{name} uses {helper}")

    def test_a_clickable_row_always_has_a_screen_reader_label(self):
        rows = self.sources()["rows.js"]
        self.assertIn('"aria-label": label ?? joined(title, subtitle,', rows)

    def test_helpers_are_in_the_offline_cache(self):
        self.assertIn('"/rows.js"', (WEB / "sw.js").read_text())


if __name__ == "__main__":
    unittest.main()
