"""A redraw of the screen in the background, or after opening a row or changing a setting, keeps the page where it is.
Only tapping the top bar (or switching screens) scrolls."""

import json
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
import { Shell } from "%(shell)s";
globalThis.document = { title: "" };
globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
class Screens { status() { return { tag: "new-content" }; } }
const app = new (Shell(Screens))();
app.current = "/status";
// A scroll area whose position is lost the moment its content is replaced, as a phone's browser may do.
const main = {
  scrollTop: 640,
  firstChild: {},
  querySelector: () => ({ replaceWith: () => { main.scrollTop = 0; } }),
  scrollTo(a, b) { main.scrollTop = typeof a === "object" ? a.top : b; },
};
app.main = main;
app.render(false);
const redraw = main.scrollTop;
main.scrollTop = 640;
app.go("/status");
console.log(JSON.stringify({ redraw, topBar: main.scrollTop }));
"""


class ScrollKept(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_redraw_keeps_scroll_and_top_bar_scrolls_to_top(self):
        script = SCRIPT % {"shell": (WEB / "shell.js").as_uri()}
        run = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertEqual(out["redraw"], 640)
        self.assertEqual(out["topBar"], 0)


if __name__ == "__main__":
    unittest.main()
