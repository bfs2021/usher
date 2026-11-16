"""Home's New row: what is unread, newest first, at the top, and gone when nothing is new."""

import json
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
import { store } from "%(home)s";
import { Home } from "%(mod)s";
const screen = new (Home(class {}))();
store.me = { baseline: 1000, opened: { b: 5000 } };
const card = (jf, added) => ({ jf, added: new Date(added * 1000).toISOString() });
store.snap = { home: [
  { id: "latest-tv", items: [card("a", 2000), card("b", 4000), card("old", 500)] },
  { id: "latest-movies", items: [card("c", 3000), card("a", 2000)] },
  { id: "resume", items: [card("r", 9000)] },
] };
const ids = () => screen.unreadItems().map((c) => c.jf);
const out = { first: ids(), count: screen.unreadCount() };
store.me.opened.c = 3500;
out.afterOpen = ids();
store.me.baseline = 9999;
out.afterAll = ids();
console.log(JSON.stringify(out));
"""


class NewRowTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_new_items_once_newest_first(self):
        script = SCRIPT % {"home": (WEB / "data.js").as_uri(), "mod": (WEB / "home.js").as_uri()}
        run = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        # "b" was opened after it was added and "old" is from before the baseline; "a" is in two rows but counts once.
        self.assertEqual(out["first"], ["c", "a"])
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["afterOpen"], ["a"])
        self.assertEqual(out["afterAll"], [])

    def test_row_is_the_first_section_and_only_when_something_is_new(self):
        src = (WEB / "home.js").read_text()
        row = src.index('"section new-row"')
        self.assertIn("if (fresh.length)", src[row - 120 : row])
        self.assertLess(row, src.index("for (const row of s.home"), "New sits above Home's rows")

    def test_header_has_count_and_mark_all_and_opening_updates_the_row(self):
        src = (WEB / "home.js").read_text()
        self.assertIn('"Mark all as read"', src)
        self.assertIn("this.updateNewRow();", src)
        self.assertIn("if (!items.length) return row.remove();", src)
        self.assertIn(".head-end", (WEB / "media.css").read_text())


if __name__ == "__main__":
    unittest.main()
