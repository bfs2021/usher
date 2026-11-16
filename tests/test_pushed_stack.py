"""Screens pushed over Status form a stack kept in history.state: each has a back button naming the screen below it."""

import json
import shutil
import subprocess
import unittest

from tests.webjs import WEB

SCRIPT = """
import { Shell } from "%(shell)s";
import { Changelog } from "%(changelog)s";
import { store } from "%(data)s";
class El {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.classList = { toggle() {} }; }
  append(...k) { this.kids.push(...k.flat()); }
  replaceChildren(...k) { this.kids = k; }
  setAttribute(n, v) { this.attrs[n] = v; }
  addEventListener() {}
  querySelector() { return null; }
}
globalThis.Node = El;
globalThis.document = { title: "", createElement: (tag) => new El(tag), createElementNS: (ns, tag) => new El(tag), createTextNode: (text) => ({ text }), querySelectorAll: () => [] };
globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
globalThis.matchMedia = () => ({ matches: true });
const entries = [{ state: {} }];
let at = 0;
globalThis.history = {
  get state() { return entries[at].state; },
  pushState(state) { entries.splice(at + 1, entries.length, { state }); at++; },
  back() { at--; },
};
const App = Shell(Changelog(class { querySelectorAll() { return []; } statusShape() { return ""; } status() { return new El("div"); } loadChangelog() {} }));
store.me = { name: "Sam" };
const app = new App();
app.main = new El("main");
app.main.scrollTo = () => {};
const words = (n) => (n.text ?? "") + (n.kids || []).map(words).join(" ");
const seen = () => ({ stack: app.pushed, title: document.title, back: words(app.main.kids[0]).trim(), state: history.state.pushed || [] });
const out = {};
app.show("/status", false);
out.status = seen();
app.openChangelog();
out.changelog = seen();
app.popScreen();
app.show("/status", false);   // what the browser's back does
out.back = seen();
at = 1;
app.show("/status", false);   // forward, or a reload on this entry
out.forward = seen();
// A stack of two: the top one names the one below it. Only the Changelog exists today, so open it twice.
app.pushScreen("changelog");
out.two = seen();
app.popScreen();
app.show("/status", false);
out.twoBack = seen();
console.log(JSON.stringify(out));
"""


class PushedStack(unittest.TestCase):
    def test_the_stack_is_a_list_of_names_in_history_state(self):
        src = (WEB / "shell.js").read_text(encoding="utf-8")
        self.assertIn("history.state?.pushed", src)
        self.assertIn("[...this.pushed, name]", src)
        self.assertIn("PUSHED_TITLES[this.pushed.at(-2)] || screen.name", src)  # the back label is the screen below
        self.assertNotIn('this.pushed === "changelog"', src)
        changelog = (WEB / "changelog.js").read_text(encoding="utf-8")
        self.assertIn('this.pushScreen("changelog")', changelog)
        self.assertNotIn("this.pushed", changelog)

    @unittest.skipUnless(shutil.which("node"), "needs Node to run the client's code")
    def test_pushing_popping_and_history_keep_the_stack(self):
        script = SCRIPT % {"shell": (WEB / "shell.js").as_uri(), "changelog": (WEB / "changelog.js").as_uri(), "data": (WEB / "data.js").as_uri()}
        run = subprocess.run(["node", "--input-type=module"], input=script, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        self.assertEqual(out["status"]["stack"], [])
        self.assertEqual(out["status"]["title"], "Status · Usher")
        self.assertEqual(out["changelog"]["stack"], ["changelog"])
        self.assertEqual(out["changelog"]["title"], "Changelog · Usher")
        self.assertEqual(out["changelog"]["back"].split()[0], "Status")  # back from the first pushed screen names Status
        self.assertEqual(out["back"]["stack"], [])
        self.assertEqual(out["forward"]["stack"], ["changelog"])
        self.assertEqual(out["two"]["stack"], ["changelog", "changelog"])
        self.assertEqual(out["two"]["state"], ["changelog", "changelog"])
        self.assertEqual(out["two"]["back"].split()[0], "Changelog")  # the back label of the second names the first
        self.assertEqual(out["twoBack"]["stack"], ["changelog"])


if __name__ == "__main__":
    unittest.main()
