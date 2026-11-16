"""The account button on Status and in the sidebar shows the person's initials; a name with no letters shows a person glyph."""

import json
import re
import shutil
import subprocess
import unittest

from tests.webjs import WEB

CODE = """
const { initials } = await import("%(util)s");
console.log(JSON.stringify(["Sam Rivers", "sam", "  mary  jane  watson ", "élodie ñandú", "李 小龍", "42 Robots", "", "  ", "-- !!", null, undefined].map(initials)));
"""


@unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
class Initials(unittest.TestCase):
    def test_first_and_last_word_capitalised_and_empty_when_no_letters(self):
        run = subprocess.run(["node", "--input-type=module", "-e", CODE % {"util": (WEB / "util.js").as_uri()}], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(run.stdout), ["SR", "S", "MW", "ÉÑ", "李小", "4R", "", "", "", "", ""])


class Styling(unittest.TestCase):
    def test_one_round_neutral_fill_for_both_buttons(self):
        css = (WEB / "app.css").read_text(encoding="utf-8")
        rule = re.search(r"^\.avatar \{([^}]*)\}", css, re.M).group(1)
        self.assertIn("border-radius: 1.2em", rule)
        self.assertIn("min-width: 2.4em", rule, "sized in em so larger text does not spill out of the circle")
        self.assertIn("background: var(--fill-strong)", rule)
        self.assertIn(".navbar .nav-action .avatar", css)
        self.assertIn(".sidebar .who .avatar", css)


if __name__ == "__main__":
    unittest.main()
